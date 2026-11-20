/* The client-side Monte Carlo: the cutline replay behind the row expander's
   what-if, the per-event points draws the leverage grid reuses, and the 2027
   drop-in behind the What-if tab.

   This is the only JavaScript on the site that simulates anything. It exists
   because `replay()` has to answer in under 100ms inside a table row and
   `dropIn()` has to answer between two frames of a slider drag, which rules
   out asking the pipeline. Everything either one draws against — curves,
   field sizes, ratings, the frozen cutlines, the 2027 ladder — is shipped in
   the bundle.

   Two questions, two halves. `replay()` is about a player who exists: their
   results are banked and the only unknown is which of the events left they
   turn up to. `dropIn()` is about one who does not, which is a question only
   a season nobody has played can be asked. */

import { POOL_BY_CLS } from "./cells.js";

/* projection of a player's finish if they played event e; live events use the
   sim's remaining-holes result, future events a quick from-scratch Monte Carlo.
   Returns { win, p10, p50, p90 } points percentiles + win probability. */
function eventProj(d, p, e) {
  if (p.live && p.live[e.tid]) {
    const l = p.live[e.tid];
    return { win: l.win, p10: l.p10, p50: l.p50, p90: l.p90, pl50: Math.round(l.mean_place), live: l };
  }
  // doubles: project the TEAM (avg rating) against the team field
  const rating = e.tid === d.meta.dbl_tid && p.dbl ? p.dbl.team_rating : p.rating;
  return projectPoints(d, p, e, 2500, rating);
}

/* Unordered key for a doubles team, so both members map to one entry (a solo
   with no listed partner is its own team). */
const teamKey = (q) => JSON.stringify([q.name, (q.dbl && q.dbl.partner_name) || null].sort());

/* Everyone (other than `selfPdga`'s entry) expected at event e: {r: rating,
   a: P(plays)}. The bundle's per-player att arrays share d.events' ordering.
   For the doubles championship the field is TEAMS, not players — one entry per
   distinct team at its team rating — so the win odds are computed against ~half
   as many opponents, as they should be. */
function eventField(d, e, selfPdga) {
  const ei = d.events.findIndex((x) => x.tid === e.tid);
  const out = [];
  if (ei < 0) return out;
  if (e.tid === d.meta.dbl_tid) {
    const self = d.players.find((q) => q.pdga === selfPdga);
    const selfKey = self && self.dbl ? teamKey(self) : null;
    const seen = new Set();
    for (const q of d.players) {
      if (!q.dbl || q.att[ei] <= 0.001) continue;
      const key = teamKey(q);
      if (key === selfKey || seen.has(key)) continue;
      seen.add(key);
      out.push({ r: q.dbl.team_rating, a: q.att[ei] });
    }
    return out;
  }
  for (const q of d.players) {
    if (q.pdga !== selfPdga && q.rating && q.att[ei] > 0.001) out.push({ r: q.rating, a: q.att[ei] });
  }
  return out;
}

/* One draw of finishing place for `rating` against the event's real field:
   sample who shows up, give everyone a rating-based score, count who beat you.
   This replaces a one-Gaussian summary of the field (field_avg_rating /
   opp_score_sd), which broke on bimodal open fields — at the USWDGC the club-
   level entrants doubled opp_score_sd, and the model concluded the 990-rated
   favorites were beatable by anyone, crushing their win odds ~5x. Only the
   score DIFFERENCES matter for ranking, so the field-average offset cancels. */
function drawPlace(d, e, rating, field) {
  const k = d.meta.rating_pts_per_stroke;
  const sd = d.meta.round_sd * Math.sqrt(e.rounds);
  const mine = (-rating / k) * e.rounds + sd * randn();
  let beat = 0;
  for (const q of field) {
    if (q.a < 1 && Math.random() >= q.a) continue;
    if ((-q.r / k) * e.rounds + sd * randn() < mine) beat++;
  }
  return 1 + beat;
}

function projectPoints(d, p, e, draws = 2500, rating = p.rating) {
  // Draw against the real field (players, or TEAMS for the doubles
  // championship — its curve is team-place indexed and drawPlace ranks the
  // team rating against the other teams). Fall back to the one-Gaussian
  // summary only when we have no field to sample (e.g. no attendees yet).
  const field = eventField(d, e, p.pdga);
  const out = new Float64Array(draws);
  const places = new Float64Array(draws);
  let wins = 0;
  for (let i = 0; i < draws; i++) {
    let place;
    if (field.length) {
      place = drawPlace(d, e, rating, field);
    } else {
      const mu = (-(rating - e.field_avg_rating) / d.meta.rating_pts_per_stroke) * e.rounds;
      const s = mu + d.meta.round_sd * Math.sqrt(e.rounds) * randn();
      const lam = Math.min(e.field_size, e.field_size * PHI(s / e.opp_score_sd));
      place = 1 + Math.min(poisson(lam), Math.round(e.field_size));
    }
    if (place === 1) wins++;
    places[i] = place;
    out[i] = place <= e.curve.length ? e.curve[place - 1] : 0;
  }
  out.sort();
  places.sort();
  const q = (f) => out[Math.min(draws - 1, Math.floor(f * draws))];
  const qp = (f) => Math.round(places[Math.min(draws - 1, Math.floor(f * draws))]);
  // points percentile q pairs with place percentile (1-q): low points = high place
  return { win: wins / draws, p10: q(0.1), p50: q(0.5), p90: q(0.9), pl90: qp(0.9), pl50: qp(0.5), pl10: qp(0.1) };
}

/* ---------- what-if view (cutline replay) ---------- */

let gauss = { spare: null };
function randn() {
  if (gauss.spare !== null) { const v = gauss.spare; gauss.spare = null; return v; }
  let u, v, s;
  do { u = Math.random() * 2 - 1; v = Math.random() * 2 - 1; s = u * u + v * v; } while (s >= 1 || s === 0);
  const m = Math.sqrt((-2 * Math.log(s)) / s);
  gauss.spare = v * m;
  return u * m;
}
/* Φ via Abramowitz–Stegun erf (7.1.26). A cheap tanh approximation is ~7x
   off in the tails — which is exactly where wins live — and crushed the
   replay's P(great finish); this one is accurate to ~1e-7. */
function PHI(z) {
  const x = z / Math.SQRT2, sign = x < 0 ? -1 : 1, ax = Math.abs(x);
  const t = 1 / (1 + 0.3275911 * ax);
  const y = 1 - t * (0.254829592 + t * (-0.284496736 + t * (1.421413741 + t * (-1.453152027 + t * 1.061405429)))) * Math.exp(-ax * ax);
  return 0.5 * (1 + sign * y);
}

/* place = 1 + (# opponents who beat you) ~ Poisson(fieldSize · Φ(z));
   integer sampling keeps the win/podium tail alive (rounding kills it) */
function poisson(lam) {
  if (lam > 30) return Math.max(0, Math.round(lam + Math.sqrt(lam) * randn()));
  const L = Math.exp(-lam);
  let k = 0, p = 1;
  do { k++; p *= Math.random(); } while (p > L);
  return k - 1;
}

function seasonTotal(pools, meta) {
  const best = (arr, k) => arr.slice().sort((a, b) => b - a).slice(0, k).reduce((s, x) => s + x, 0);
  return best(pools.dgpt, meta.count_dgpt) + best(pools.playoff, meta.count_playoff) +
    best(pools.major, meta.majors_counted) + pools.jomez.reduce((s, x) => s + x, 0);
}

/* `n` draws of the points `p` would score at event `e`, against the event's
   REAL field (same model as projectPoints — the one-Gaussian shortcut broke on
   bimodal open fields like the USWDGC). Doubles draws against the team field:
   eventField returns one entry per team for the doubles championship.

   Factored out of replay() so the leverage grid can pre-sample an event once
   and reuse it across its nine conditional runs instead of redrawing per run. */
function samplePoints(d, p, e, n) {
  const rating = e.tid === d.meta.dbl_tid && p.dbl ? p.dbl.team_rating : p.rating;
  const field = eventField(d, e, p.pdga);
  const arr = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    let place;
    if (field.length) {
      place = drawPlace(d, e, rating, field);
    } else {
      const mu = (-(rating - e.field_avg_rating) / d.meta.rating_pts_per_stroke) * e.rounds;
      const s = mu + d.meta.round_sd * Math.sqrt(e.rounds) * randn();
      const lam = Math.min(e.field_size, e.field_size * PHI(s / e.opp_score_sd));
      place = 1 + Math.min(poisson(lam), Math.round(e.field_size));
    }
    arr[i] = place <= e.curve.length ? e.curve[place - 1] : 0;
  }
  return arr;
}

function replay(d, p, attendSet) {
  // banked, split into counting pools once
  const banked = { dgpt: [], playoff: [], major: [], jomez: [] };
  for (const b of p.banked) banked[POOL_BY_CLS[b.cls] || "dgpt"].push(b.pts);
  const events = d.events.filter((e) => attendSet.has(e.tid));
  const n = d.cutline.length;
  // Pre-sample each event, then draw by index inside the hot loop so the
  // replay stays under its time budget.
  const SAMPLES = 1500;
  const ptsSamples = events.map((e) => samplePoints(d, p, e, SAMPLES));
  /* The cutline this player has to beat is the one made of OTHER players, and
     which of the two published arrays that is depends on the base simulation:
     if they held a top-`cut` place in sim i, the cut-th total among everyone
     else is the (cut+1)-th overall (`cutline2`); if they did not, it is the
     cut-th overall (`cutline`). That is a per-simulation fact, and reproducing
     it exactly would need their own per-sim total — 25,000 numbers per player,
     which the bundle cannot afford to ship. So it is taken in expectation,
     with `p_cut` as the weight. `p_cut` is the right weight and does not move
     with the scenario: it is the probability they were inside the cut in the
     BASE sim, and the toggles here change their own results, not the ordering
     that fixed which array applies.

     What changed is where the weighting is applied. This used to blend the two
     thresholds and test the total against the blend, which is not the same
     quantity — an indicator is not linear, so a total sitting between the two
     cutlines counted as a certain miss instead of a `w` chance of qualifying,
     and that band is exactly where bubble players live. Counting against each
     cutline and weighting the two counts is the expectation the blend was
     reaching for.

     Measured against the old form over 18 bubble players, 7 runs each: mean
     difference +0.07pp, against a 0.51pp run-to-run noise floor — so it is not
     a visible correction today, because a player's remaining-season spread is
     hundreds of points wide while the two cutlines differ by ~18. It matters
     in the state where the band stops being narrow relative to what is left to
     play: one event to go, everyone stacked. It costs nothing to be right in
     advance — 50.6ms against the old 52.0ms, since it trades a multiply-add
     for a comparison.

     Still approximate, and knowably so: the frozen cutline treats the rest of
     the field as fixed, when a player who wins takes points that would have
     gone to the players the cutline is made of. That term needs a re-simulation
     of the field, which is not a thing a row expander can do in 100ms. */
  const w = p.p_cut;
  let qLo = 0, qHi = 0, sumPts = 0;
  for (let i = 0; i < n; i++) {
    const pools = { dgpt: banked.dgpt.slice(), playoff: banked.playoff.slice(), major: banked.major.slice(), jomez: banked.jomez.slice() };
    for (let ev = 0; ev < events.length; ev++) {
      const pts = ptsSamples[ev][(Math.random() * SAMPLES) | 0];
      pools[POOL_BY_CLS[events[ev].cls] || "dgpt"].push(pts);
    }
    const total = seasonTotal(pools, d.meta);
    if (total > d.cutline2[i]) qLo++;    // they held a top-cut place in the base sim
    if (total > d.cutline[i]) qHi++;     // they did not
    sumPts += total;
  }
  return { pCut: (w * qLo + (1 - w) * qHi) / n, meanPts: sumPts / n };
}

/* ==========================================================================
   THE 2027 DROP-IN

   Give the 2027 field a rating and a number of starts in each class, and read
   back every column the projection tab prints for the players who are really
   in it.

   Everything the field contributes is a summary shipped in `meta.whatif` —
   see `dgpt/whatif.py`, which is where the reasoning about what can be
   summarised lives. This half is the draw:

     1. pick the season      a random subset of each class, because nothing
                             here knows which stops a given player would
                             choose and pretending otherwise would be the
                             model's opinion rather than the reader's
     2. play it              a place per event off the field's own rating
                             bands, points off the class curve, per-pool
                             counting caps applied
     3. rank it              against the field's ladder, drawn at one uniform
                             so a season is strong or weak all the way down
     4. run the postseason   both playoff gates, both performance paths, the
                             seed ladder, and the Cup played out on it

   Three things stay approximate, and the tab says all three out loud. The
   field is frozen: a player dropped into it takes places off everyone else,
   and the ladder was measured without them. The Cup's opponents are the mean
   rating at each seed rather than a distribution over who holds it. And which
   events get played is a coin, not a plan.
   ========================================================================== */

// Classes a reader gets a slider for. Playoffs are missing on purpose — they
// are earned rather than entered, which is the thing the tab is computing —
// and so is the doubles championship, which needs a partner 2027 cannot name.
const DROP_CLASSES = ["elite", "elite_plus", "major", "jomez"];

// Beyond this the Poisson sampler is both slow (one uniform per opponent
// beaten) and the wrong shape: at a large expected count the number of
// players who beat you is binomial, whose variance np(1-p) is strictly
// smaller. Below it the exact draw is what keeps the win tail alive, which is
// the same reason `poisson` exists at all — and a win is a Cup invite here.
const EXACT_BEAT_MAX = 12;

/* One event, ready to draw against: the field's ratings as expected scores,
   collapsed into a lookup from "my score" to "share of the field I beat".

   The table is the whole trick behind the slider. P(one opponent beats me) is
   an average of 20 normal CDFs and does not depend on my rating at all — only
   on where my score lands — so it is built once per bundle and read with an
   interpolation per draw instead of 20 error functions. */
function dropEvent(e, meta, curve, range) {
  const k = meta.rating_pts_per_stroke;
  const sd = meta.round_sd * Math.sqrt(e.rounds);
  const mu = e.field_rq.map((r) => (-r / k) * e.rounds);
  const G = 257;
  const lo = Math.min((-range[1] / k) * e.rounds, ...mu) - 6 * sd;
  const hi = Math.max((-range[0] / k) * e.rounds, ...mu) + 6 * sd;
  const step = (hi - lo) / (G - 1);
  const ptab = new Float64Array(G);
  for (let i = 0; i < G; i++) {
    const x = lo + i * step;
    let acc = 0;
    for (const m of mu) acc += PHI((x - m) / sd);
    ptab[i] = acc / mu.length;
  }
  return {
    cls: e.cls, short: e.short, rounds: e.rounds, sd, curve,
    n: Math.max(1, Math.round(e.field_size)),
    pAt(x) {
      const t = (x - lo) / step;
      if (t <= 0) return ptab[0];
      if (t >= G - 1) return ptab[G - 1];
      const i = t | 0;
      return ptab[i] + (t - i) * (ptab[i + 1] - ptab[i]);
    },
  };
}

/* One finishing place at `ev` for a player rated `rating`. Same shape as
   `drawPlace` above — score against the field, count who got under it — with
   the field pre-summarised instead of resampled per draw. */
function dropPlace(ev, rating, k) {
  const s = (-rating / k) * ev.rounds + ev.sd * randn();
  const p = ev.pAt(s);
  const lam = ev.n * p;
  const beat = lam < EXACT_BEAT_MAX
    ? poisson(lam)
    : Math.max(0, Math.round(lam + Math.sqrt(lam * (1 - p)) * randn()));
  return 1 + Math.min(beat, ev.n);
}

const dropPoints = (ev, place) => ev.curve[Math.min(place, ev.curve.length) - 1];
const byPlace = (arr, place) => arr[Math.min(place, arr.length) - 1];
// Cup starting score for a finishing position. Past the table's end every
// seed is a bottom seed, which is what the last band already pays.
const seedStrokes = (w, rank) => byPlace(w.cup_strokes, rank);

/* A shipped quantile grid, read at one uniform. Every distribution in the kit
   uses the same grid, so this is the only place that knows its shape. */
function atQ(grid, u) {
  const last = grid.length - 1;
  const x = u * last;
  if (x >= last) return grid[last];
  const i = x | 0;
  return grid[i] + (x - i) * (grid[i + 1] - grid[i]);
}

/* Where a season total lands in the standings, in the season drawn at `u`.

   The ladder is shipped rung by rung down to the histogram's depth and then
   log-spaced, so above the depth this is exact and below it the answer is
   interpolated between two rungs — which is all "about 180th" needs. */
function dropRank(total, u, w) {
  const L = w.ladder, ranks = w.ranks, n = ranks.length;
  if (total > atQ(L[0], u)) return 1;
  if (total <= atQ(L[n - 1], u)) return ranks[n - 1] + 1;
  let lo = 1, hi = n - 1;                       // first rung this total clears
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (total > atQ(L[mid], u)) hi = mid; else lo = mid + 1;
  }
  const ra = ranks[lo - 1], rb = ranks[lo];
  if (rb === ra + 1) return rb;
  const top = atQ(L[lo - 1], u), bot = atQ(L[lo], u);
  const f = top > bot ? (top - total) / (top - bot) : 1;
  return Math.max(ra + 1, Math.min(rb, Math.round(ra * Math.exp(f * Math.log(rb / ra)))));
}

/* P(wins the Cup) for one drawn season, taken in closed form rather than by
   playing the field out: given my own score, every opponent has to beat it
   independently, so the product of their survival odds IS the probability.
   Costs one normal CDF per seat and carries no Monte Carlo noise of its own.

   The opponents are the mean rating at each seed, which is the approximation:
   the real field is a distribution over who holds the seat, not its average. */
function dropCupWin(prep, rating, rank) {
  const { meta: m, w, k, cupSd } = prep;
  const mine = (-rating / k) * m.cup_rounds + seedStrokes(w, rank) + cupSd * randn();
  let p = 1;
  for (let i = 0; i < prep.cupMu.length; i++) {
    if (i + 1 === rank) continue;               // that seat is mine
    p *= 1 - PHI((mine - prep.cupMu[i]) / cupSd);
  }
  return p;
}

/* Everything about a bundle that does not change when a slider moves.
   Returns null for a bundle published before the kit existed — the tab has
   its own empty state for that rather than the page failing to render. */
function dropInPrep(d) {
  const m = d.meta, w = m.whatif;
  if (!w || !w.ladder || !w.ladder.length) return null;
  const evs = (d.schedule || []).filter(
    (e) => e.ei != null && e.counts && e.field_rq && e.field_rq.length);
  if (!evs.length || !prepPlayoffs(evs, m)) return null;

  // Slider range: low enough to cover the bottom of a field, high enough to
  // cover the best player in it, both read off this bundle so FPO scales
  // itself rather than carrying MPO's numbers.
  const floor5 = (x) => Math.round(x / 5) * 5;
  const rq = evs.flatMap((e) => e.field_rq);
  const rated = d.players.map((p) => p.rating).filter(Boolean);
  const range = [floor5(Math.min(...rq) - 40), floor5(Math.max(...rated, ...rq) + 10)];

  const pool = {}, keep = {};
  for (const p of m.pools) {
    keep[p.name] = p.keep;
    for (const c of p.classes) pool[c] = p.name;
  }
  const invite = new Set(w.invite || []);
  const prep = { meta: m, w, k: m.rating_pts_per_stroke, range, pool, keep, plan: [] };
  const made = {};
  for (const e of evs) {
    const ev = dropEvent(e, m, w.curves[e.cls] || [0], range);
    ev.pool = pool[e.cls];
    ev.invite = invite.has(e.cls);
    (made[e.cls] = made[e.cls] || []).push(ev);
  }
  prep.play1 = made.playoff.find((e) => e.short === m.playoff1);
  prep.play2 = made.playoff.find((e) => e.short !== m.playoff1);
  // One entry per slider, each carrying its own scratch index array so a draw
  // can shuffle a subset without allocating.
  for (const cls of DROP_CLASSES) {
    const list = made[cls] || [];
    prep.plan.push({ cls, list, ix: list.map((_, i) => i), n: list.length });
  }
  prep.cupSd = m.round_sd * Math.sqrt(m.cup_rounds);
  prep.cupMu = w.cup_field.map(
    (r, i) => (-r / prep.k) * m.cup_rounds + seedStrokes(w, i + 1));
  prep.strokeIx = new Map(m.start_strokes.values.map((v, i) => [v, i]));
  return prep;
}

/* Both playoff events have to be on the calendar and nameable, since the two
   gates and both performance paths are keyed off them. */
function prepPlayoffs(evs, m) {
  const po = evs.filter((e) => e.cls === "playoff");
  return po.length >= 2 && po.some((e) => e.short === m.playoff1);
}

const bestOf = (arr, k) => {
  if (k != null && arr.length > k) arr.sort((a, b) => b - a);
  let s = 0;
  for (let i = 0; i < (k == null ? arr.length : Math.min(k, arr.length)); i++) s += arr[i];
  return s;
};

/* `draws` simulated 2027 seasons for a player rated `cfg.rating` who plays
   `cfg[cls]` events of each class. Returns the same quantities the projection
   bundle carries per player, so the tab can render them with the table's own
   cells. */
function dropIn(prep, cfg, draws = 6000) {
  const { meta: m, w, k, keep } = prep;
  const depth = m.max_hist_rank;
  const nb = m.start_strokes.values.length + 1;
  const hist = new Float64Array(depth);
  const strokes = new Float64Array(nb);
  const pools = {};
  for (const p of m.pools) pools[p.name] = [];
  for (const s of prep.plan) s.n = Math.max(0, Math.min(cfg[s.cls] | 0, s.list.length));

  let sumPts = 0, sumRank = 0, sumStarts = 0, sumCup = 0;
  let nFirst = 0, nCut = 0, nPerf = 0, nChamp = 0, nP1 = 0, nP2 = 0;

  for (let i = 0; i < draws; i++) {
    for (const key in pools) pools[key].length = 0;
    let won = false, starts = 0;

    for (const s of prep.plan) {
      // partial Fisher-Yates: n distinct events, in place, no allocation
      for (let a = 0; a < s.n; a++) {
        const b = a + ((Math.random() * (s.ix.length - a)) | 0);
        const t = s.ix[a]; s.ix[a] = s.ix[b]; s.ix[b] = t;
        const ev = s.list[s.ix[a]];
        const place = dropPlace(ev, cfg.rating, k);
        if (place === 1 && ev.invite) won = true;
        pools[ev.pool].push(dropPoints(ev, place));
        starts++;
      }
    }
    let base = 0;
    for (const p of m.pools) if (p.name !== "playoff") base += bestOf(pools[p.name], keep[p.name]);

    // one uniform per season: the field's ladder and both gate lines are read
    // at it together, so a season the field scores well in is a hard one to
    // qualify out of at every rung
    const u = Math.random();

    let pts1 = 0, place2 = 0, inP2 = false;
    if (base > atQ(w.play1_line, u)) {
      nP1++; starts++;
      const place1 = dropPlace(prep.play1, cfg.rating, k);
      pts1 = dropPoints(prep.play1, place1);
      if (place1 === 1 && prep.play1.invite) won = true;
      inP2 = Math.random() < byPlace(w.play2_perf, place1);
    }
    if (base + pts1 > atQ(w.play2_line, u)) inP2 = true;
    let pts2 = 0;
    if (inP2) {
      nP2++; starts++;
      place2 = dropPlace(prep.play2, cfg.rating, k);
      pts2 = dropPoints(prep.play2, place2);
      if (place2 === 1 && prep.play2.invite) won = true;
    }
    // pools cap independently, so the season is the non-playoff half plus
    // whatever the playoff pool keeps
    const total = base + bestOf([pts1, pts2], keep.playoff);

    const rank = dropRank(total, u, w);
    const auto = rank <= m.cut;
    const perf = !auto && inP2 && Math.random() < byPlace(w.champ_perf, place2);
    const champ = auto || perf || won;

    sumPts += total;
    sumRank += rank;
    sumStarts += starts;
    hist[Math.min(rank, depth) - 1] += 1;
    if (rank === 1) nFirst++;
    if (auto) nCut++;
    if (perf) nPerf++;
    if (champ) {
      nChamp++;
      strokes[prep.strokeIx.get(seedStrokes(w, rank)) ?? nb - 2] += 1;
      sumCup += dropCupWin(prep, cfg.rating, rank);
    } else {
      strokes[nb - 1] += 1;
    }
  }

  const per = (x) => x / draws;
  return {
    draws,
    mean_pts: per(sumPts), mean_rank: per(sumRank), exp_starts: per(sumStarts),
    p_first: per(nFirst), p_cut: per(nCut), p_perf: per(nPerf),
    p_champ: per(nChamp), p_play1: per(nP1), p_play2: per(nP2),
    p_cup_win: per(sumCup),
    hist: Array.from(hist, per), strokes: Array.from(strokes, per),
  };
}

export { DROP_CLASSES, dropIn, dropInPrep, eventProj, replay, samplePoints };

