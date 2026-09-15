/* The client-side Monte Carlo: the cutline replay behind the row expander's
   what-if, and the per-event points draws the leverage grid reuses.

   This is the only JavaScript on the site that simulates anything. It exists
   because `replay()` has to answer in under 100ms inside a table row, which
   rules out asking the pipeline. Everything it draws against — curves, field
   sizes, ratings, the frozen cutlines — is shipped in the bundle. */

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

export { eventProj, replay, samplePoints };
