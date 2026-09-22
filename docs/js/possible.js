/* The "what's left" tab: six reads of the remaining possibility space.

   Panels 1-5 are pure re-reads of fields the bundle already ships; 3 and 4
   also price a gap as a finishing place, which is arithmetic on the closing
   event's own curve rather than a simulation. Panel 6 re-runs the cutline
   replay under a fixed result at one event, which is why it is computed
   lazily, chunked one player per tick, and cached per division. */

import { $, fmtPct, fmtPts, ordinal, state } from "./core.js";
import { probe, tipAttrs } from "./tooltip.js";
import { CLS_LABEL, liveTidSet, nameCell, playerLink, playoffNote, POOL_BY_CLS, probClass, shortName } from "./cells.js";
import { samplePoints } from "./sim.js";

/* ==========================================================================
   "What's left" view — six reads of the remaining possibility space.

   The forecast table answers "will this player get in" one row at a time.
   These answer the shape questions it can't: how wide is everyone's range,
   which positions are actually still contested, how far the chasers are from
   the line and what closing that gap costs, what it takes to pass the player
   one row up, which door they walk through, and where their season gets
   decided.

   Panels 1-5 are pure re-reads of fields the bundle already ships (hist,
   cutline, p_cut, p_mvp_qual, att) — no pipeline change. Panel 6 re-runs the
   cutline replay under a fixed result at one event, which is why it is
   computed lazily, chunked, and cached per division.
   ========================================================================== */

/* Players whose automatic bid is genuinely still in play. Ordered by current
   points so the cutline rug and the leverage grid list the same people in the
   same order. */
function contenders(d, max = 26) {
  return d.players
    .filter((p) => p.p_cut > 0.004 && p.p_cut < 0.996)
    .sort((a, b) => b.points - a.points || a.rank - b.rank)
    .slice(0, max);
}

// the rug labels, the gap annotations and the head-to-head summary all want a
// name short enough to sit next to a number
const lastName = (p) => p.name.split(/\s+/).pop();

/* ---------- counting pools ---------- */

/* Exact top-`cap` sum for one counting pool after adding `adds`.

   The single-add case — every pool but the playoffs today — is the whole point
   of this view: a new result only counts for what it beats, so it is worth
   `value − the pool's current floor`, which is why the same event is worth 300
   to one player and 90 to the next. */
function poolSum(pl, adds) {
  if (!adds.length) return pl.sum;
  if (adds.length === 1) return pl.sum + Math.max(0, adds[0] - pl.floor);
  const all = pl.kept.concat(adds).sort((a, b) => b - a);
  let s = 0;
  for (let i = 0; i < pl.cap && i < all.length; i++) s += all[i];
  return s;
}

function countingPools(d, p) {
  const m = d.meta;
  const banked = { dgpt: [], playoff: [], major: [], jomez: [] };
  for (const b of p.banked) banked[POOL_BY_CLS[b.cls] || "dgpt"].push(b.pts);
  const pools = { jomez: banked.jomez.reduce((s, x) => s + x, 0) };
  for (const [k, cap] of [["dgpt", m.count_dgpt], ["playoff", m.count_playoff], ["major", m.majors_counted]]) {
    const kept = banked[k].sort((a, b) => b - a).slice(0, cap);
    pools[k] = { kept, cap, sum: kept.reduce((s, x) => s + x, 0),
                 floor: kept.length >= cap ? kept[cap - 1] : 0 };
  }
  return pools;
}

/* ---------- 1 · possibility cloud ---------- */

/* Rows of the cloud: everyone with a live claim, plus enough of the table
   below the cut to show the band widening.

   Players whose distribution is mostly the overflow bin are left out whatever
   their Cup odds. hist's last bucket is "that position or worse", so such a row
   draws as one lone mark at the right edge — a label pointing at "off the
   chart", which reads as a rendering fault rather than as data. This panel is
   about where the standings land; a deep longshot's route to the Cup is the
   MVP Open, and the ways-in panel is where that shows up. */
function cloudRows(d) {
  const m = d.meta, last = m.max_hist_rank - 1;
  return d.players
    .filter((p) => p.points > 0 && p.hist[last] < 0.5
      && (p.rank <= m.field_size + 18 || p.p_champ >= 0.005))
    .sort((a, b) => b.points - a.points || a.rank - b.rank)
    .slice(0, 52);
}

/* The row sparkline (sparkCell) drawn for every contender at once: the
   season's whole possibility space as one image. Cell opacity is normalised
   per ROW, exactly as sparkCell scales to each player's own max — the question
   each row answers is "how wide is THIS player's range", and a shared scale
   renders a diffuse longshot as a blank line. */
function cloudHtml(d) {
  const m = d.meta, rows = cloudRows(d), H = m.max_hist_rank;
  if (!rows.length) return "";
  const LW = 148, RH = 13, TOP = 24, CW = 11.4;
  const W = LW + H * CW + 22, HT = TOP + rows.length * RH + 8;
  let cells = "", labels = "", chrome = "";
  rows.forEach((p, ri) => {
    const y = TOP + ri * RH;
    const nm = p.name.length > 21 ? p.name.split(/\s+/).pop() : p.name;
    labels += `<text x="${LW - 7}" y="${y + 9.5}" text-anchor="end" class="pc-name">${nm}</text>` +
      `<text x="4" y="${y + 9.5}" class="pc-rank">${p.rank}</text>`;
    const rmax = Math.max(...p.hist, 1e-9);
    for (let k = 0; k < H; k++) {
      const v = p.hist[k] / rmax;
      if (v < 0.05) continue;
      const cls = k + 1 <= m.cut ? "pc-in" : k === H - 1 ? "pc-over" : "pc-out";
      cells += `<rect class="${cls}" x="${(LW + k * CW).toFixed(1)}" y="${y + 1}" ` +
        `width="${(CW - 1.6).toFixed(1)}" height="${RH - 2}" rx="1.5" ` +
        `opacity="${(0.25 + Math.pow(v, 0.7) * 0.75).toFixed(3)}"/>`;
    }
  });
  for (let k = 9; k < H - 1; k += 10) {
    chrome += `<text x="${(LW + k * CW + CW / 2).toFixed(1)}" y="15" text-anchor="middle" class="pc-tick">${k + 1}</text>`;
  }
  const cx = LW + m.cut * CW - 0.8;
  chrome += `<line class="pc-cut" x1="${cx.toFixed(1)}" y1="19" x2="${cx.toFixed(1)}" y2="${HT - 5}"/>` +
    `<text x="${(cx - 4).toFixed(1)}" y="15" text-anchor="end" class="pc-cutlabel">cut ${m.cut}</text>`;
  return `<div class="pv-scroll"><svg class="pcloud" id="pcloud" width="${W}" height="${HT}"
    viewBox="0 0 ${W} ${HT}" role="img"
    aria-label="Final standings position distribution for every contender, one row per player">
    ${chrome}${cells}${labels}</svg></div>`;
}

/* The cloud's table twin: the aggregations worth reading as numbers. Colour
   alone never has to carry a value. */
function cloudTableHtml(d) {
  const m = d.meta;
  const sum = (h, a, b) => h.slice(a, b).reduce((s, x) => s + x, 0);
  const body = cloudRows(d).map((p) => {
    let mr = 0, tot = 0;
    p.hist.forEach((v, i) => { mr += v * (i + 1); tot += v; });
    return `<tr><td class="num dim">${p.rank}</td><td>${playerLink(p)}</td>
      <td class="num">${fmtPts(p.points)}</td>
      <td class="num">${fmtPct(sum(p.hist, 0, 5))}</td>
      <td class="num ${probClass(sum(p.hist, 0, m.cut))}">${fmtPct(sum(p.hist, 0, m.cut))}</td>
      <td class="num">${fmtPct(sum(p.hist, 0, m.field_size))}</td>
      <td class="num dim">${(mr / (tot || 1)).toFixed(1)}</td></tr>`;
  }).join("");
  return `<div class="pv-scroll"><table class="table-ledger detail-tbl pv-tbl"><thead><tr>
    <th class="num">#</th><th>Player</th><th class="num">Points</th>
    <th class="num" ${tipAttrs(`P(finish top 5)`)}>Top 5</th>
    <th class="num" ${tipAttrs(`P(finish inside the automatic-bid cut)`)}>Top ${m.cut}</th>
    <th class="num" ${tipAttrs(`P(finish inside the championship field size)`)}>Top ${m.field_size}</th>
    <th class="num">Mean</th></tr></thead><tbody>${body}</tbody></table></div>`;
}

/* ---------- 2 · how contested each position is ---------- */

/* Effective number of candidates for each final position: 1 / Σ(share²) down
   the occupancy column, i.e. the inverse Herfindahl. 1.0 reads as "decided",
   20 as a twenty-way scramble.

   Stops one short of max_hist_rank on purpose: the last bin is "that position
   or worse", so its column sums to far more than one player's worth of
   probability and would render as false chaos at the right edge. */
function effectiveCandidates(d) {
  const H = d.meta.max_hist_rank, out = [];
  for (let k = 0; k < H - 1; k++) {
    let s = 0, ss = 0;
    for (const p of d.players) { const v = p.hist[k]; s += v; ss += v * v; }
    out.push(ss > 0 ? (s * s) / ss : null);
  }
  return out;
}

function contestedHtml(d) {
  const m = d.meta;
  const eff = effectiveCandidates(d).slice(0, 40).filter((v) => v != null);
  if (eff.length < 5) return "";
  const W = 900, H = 200, L = 40, R = 14, T = 14, B = 30;
  const pw = W - L - R, ph = H - T - B;
  const maxY = Math.max(5, Math.ceil(Math.max(...eff) / 5) * 5);
  const X = (i) => L + (i + 0.5) * (pw / eff.length);
  const Y = (v) => T + ph - (v / maxY) * ph;
  let chrome = "";
  for (let t = 0; t <= maxY; t += 5) {
    chrome += `<line class="pv-grid" x1="${L}" y1="${Y(t).toFixed(1)}" x2="${W - R}" y2="${Y(t).toFixed(1)}"/>` +
      `<text x="${L - 7}" y="${(Y(t) + 3.5).toFixed(1)}" text-anchor="end" class="pc-tick">${t}</text>`;
  }
  for (let k = 4; k < eff.length; k += 5) {
    chrome += `<text x="${X(k).toFixed(1)}" y="${H - 10}" text-anchor="middle" class="pc-tick">${k + 1}</text>`;
  }
  const pts = eff.map((v, i) => `${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join(" ");
  const cx = L + m.cut * (pw / eff.length);
  // label only the two positions that carry the story; a number on every point
  // is noise the axis and the tooltip already cover
  const marks = [1, m.cut].filter((k) => k <= eff.length).map((k) => {
    const v = eff[k - 1], first = k === 1;
    return `<circle class="pv-dot" cx="${X(k - 1).toFixed(1)}" cy="${Y(v).toFixed(1)}" r="4"/>
      <text x="${(X(k - 1) + (first ? 8 : -8)).toFixed(1)}"
        y="${(first ? Y(v) - 9 : Y(v) + 3.5).toFixed(1)}"
        text-anchor="${first ? "start" : "end"}" class="pv-mark">${v.toFixed(1)}</text>`;
  }).join("");
  return `<div class="pv-scroll"><svg class="pv-svg" id="pcontested" width="${W}" height="${H}"
    viewBox="0 0 ${W} ${H}" role="img"
    aria-label="Effective number of candidates for each final standings position">
    ${chrome}
    <line class="pc-cut" x1="${cx.toFixed(1)}" y1="${T}" x2="${cx.toFixed(1)}" y2="${T + ph}"/>
    <text x="${(cx - 5).toFixed(1)}" y="${T + 11}" text-anchor="end" class="pc-cutlabel">last automatic bid ▸</text>
    <path class="pv-area" d="M${X(0).toFixed(1)},${T + ph} L${pts.replace(/ /g, " L")} L${X(eff.length - 1).toFixed(1)},${T + ph}Z"/>
    <polyline class="pv-line" points="${pts}"/>${marks}
    <text x="${L}" y="${H - 10}" text-anchor="middle" class="pc-tick">1</text>
    <text transform="translate(10,${(T + ph / 2).toFixed(1)}) rotate(-90)" text-anchor="middle" class="pc-tick">candidates</text>
  </svg></div>`;
}

/* ---------- 3 · the cutline, and how far away you are ---------- */

/* Points only ever go up, so a player's current total is a hard floor and the
   distance to the cutline is a real, closeable number. The axis has to reach
   down to the chasers rather than just covering the cutline — the horizontal
   distance between the two IS the chart.

   A distance in points is only half of what a chaser is asking, though: the
   other half is what it costs. So every gap here is also priced as a finishing
   place at the closing event — the worst finish that still carries them over
   the line. */

/* The event a chaser's season comes down to: the MVP Open while it is still
   unplayed, and otherwise whatever is last on the calendar. */
function closingEvent(d) {
  return d.events.find((e) => e.tid === d.meta.mvp_tid) || d.events[d.events.length - 1] || null;
}

/* The three lines worth pricing against. "Low" and "high" are the cutline's
   10th and 90th percentiles — the same season in its kind and its cruel
   version — and the median between them is the headline. Named rather than
   numbered because a table column headed "10th pct" full of ordinals ("10th
   pct: 12th") is two different kinds of place in four words. */
const CUT_BANDS = [
  { f: 0.1, label: "low", head: "Low", pct: "10th percentile", blurb: "a season where the line lands low" },
  { f: 0.5, label: "median", head: "Median", pct: "median", blurb: "where the line usually lands" },
  { f: 0.9, label: "high", head: "High", pct: "90th percentile", blurb: "a season where the line lands high" },
];
const cutBands = (q) => CUT_BANDS.map((b) => ({ ...b, pts: q(b.f) }));

/* What a finish at `ev` is worth to `p`, place by place, after the per-class
   counting caps — a result only counts for what it beats. The Jomez bonus is
   the one uncapped pool and pays face value.

   Null when they are not in that field: an event that cannot move their season
   is a different answer from one worth zero, and the callers say so. */
function pricer(d, ev, p) {
  const ei = ev ? d.events.indexOf(ev) : -1;
  if (ei < 0 || !((p.live && p.live[ev.tid]) || p.att[ei] > 0.02)) return null;
  const key = POOL_BY_CLS[ev.cls] || "dgpt";
  const pool = key === "jomez" ? null : countingPools(d, p)[key];
  return (place) => {
    const v = place <= ev.curve.length ? ev.curve[place - 1] : 0;
    return pool ? poolSum(pool, [v]) - pool.sum : v;
  };
}

/* The worst finish at `ev` that still carries `p` past `target` season points.

   Walks the event's own curve to the back of its field, so "any finish does
   it" and "a win is not enough" are real answers rather than a place number
   clipped at either end.

   Two things are held still. The rest of the season stays where it stands,
   which is exact with one event left and a ceiling before that — anything
   banked elsewhere only lowers the bar. And the line itself is the one the
   base simulation drew, when a player who wins here takes points off the
   people it is made of: the same frozen-cutline approximation the row
   expander runs on, and for the same reason. */
function needAt(d, ev, p, target, price = pricer(d, ev, p)) {
  if (p.points > target) return { kind: "clear" };
  if (!price) return { kind: "out" };
  const size = Math.max(1, Math.round(ev.field_size));
  const need = target - p.points;
  let worst = 0;
  for (let k = 1; k <= size; k++) if (price(k) > need) worst = k;
  if (!worst) return { kind: "never" };
  return { kind: worst >= size ? "any" : "place", place: worst };
}

/* How a needed finish reads in prose — the gap annotations and the tick
   readout — and in a table cell, where the column head carries the sentence. */
function needText(n) {
  switch (n.kind) {
    case "clear": return "already clear";
    case "any": return "any finish does it";
    case "never": return "a win is not enough";
    case "out": return "not in the field";
    default: return `needs ${n.place === 1 ? "the win" : ordinal(n.place)}`;
  }
}
function needShort(n) {
  switch (n.kind) {
    case "clear": return "clear";
    case "any": return "any";
    case "never": return "—";
    case "out": return "n/a";
    default: return ordinal(n.place);
  }
}
function needCell(n) {
  if (n.kind === "out") {
    return `<td class="num dim" ${tipAttrs("Not in this field — this event cannot move their season")}>n/a</td>`;
  }
  if (n.kind === "never") return `<td class="num dim">—</td>`;
  if (n.kind === "clear" || n.kind === "any") return `<td class="num"><span class="pos">${needShort(n)}</span></td>`;
  return `<td class="num">${needShort(n)}</td>`;
}

function cutlineHtml(d, q) {
  const cl = d.cutline;
  if (!cl || cl.length < 100) return "";
  const rug = contenders(d);
  const bands = cutBands(q), ev = closingEvent(d);
  const p50 = bands[1].pts, blo = q(0.005), bhi = q(0.995);
  const NB = 44, bwPts = (bhi - blo) / NB;
  const bins = new Array(NB).fill(0);
  for (const x of cl) if (x >= blo && x <= bhi) bins[Math.min(NB - 1, Math.floor((x - blo) / bwPts))]++;
  const bmax = Math.max(...bins, 1);

  const W = 900, H = 236, L = 26, R = 16, T = 14, B = 94;
  const pw = W - L - R, ph = H - T - B;
  const xlo = Math.min(blo, rug.length ? Math.min(...rug.map((p) => p.points)) - 15 : blo);
  const X = (v) => L + ((v - xlo) / (bhi - xlo)) * pw;
  const barW = Math.max(1, X(blo + bwPts) - X(blo) - 1.2);
  let bars = "";
  bins.forEach((v, i) => {
    const h = (v / bmax) * ph;
    bars += `<rect class="pv-bar" x="${(X(blo + i * bwPts) + 0.6).toFixed(1)}" y="${(T + ph - h).toFixed(1)}"
      width="${barW.toFixed(1)}" height="${h.toFixed(1)}" rx="1.5"/>`;
  });
  const med = X(p50);
  let ticks = rug.map((p) =>
    `<line class="pv-tick ${probClass(p.p_cut)}" x1="${X(p.points).toFixed(1)}" y1="${T + ph + 3}"
      x2="${X(p.points).toFixed(1)}" y2="${T + ph + 19}"/>`).join("");
  // two annotated gaps, in their own band below the tick labels, each label
  // anchored away from its connector so nothing lands on an axis number:
  // the nearest chaser, and the furthest back who still has a real shot —
  // the deepest tick on the rug is a 0.5% longshot whose gap is just noise
  const behind = rug.filter((p) => p.points < p50).sort((a, b) => b.points - a.points);
  const live = behind.filter((p) => p.p_cut >= 0.1);
  [behind[0], (live.length ? live : behind)[(live.length ? live : behind).length - 1]]
    .filter((p, i, a) => p && a.indexOf(p) === i)
    .forEach((p, i) => {
      const x = X(p.points), y = T + ph + 50 + i * 17, wide = med - x > 120;
      // the gap and its price, on one line: the distance is the chart, the
      // finish it takes is what the reader came for
      const n = ev ? needAt(d, ev, p, p50) : { kind: "out" };
      const cost = n.kind === "out" || n.kind === "clear" ? "" : ` · ${needText(n)}`;
      ticks += `<line class="pv-gap" x1="${x.toFixed(1)}" y1="${y}" x2="${med.toFixed(1)}" y2="${y}"/>
        <circle class="pv-gapdot" cx="${x.toFixed(1)}" cy="${y}" r="2.5"/>
        <text x="${(wide ? x + 5 : x - 5).toFixed(1)}" y="${y - 5}" text-anchor="${wide ? "start" : "end"}"
          class="pv-gaplabel">${lastName(p)} · <tspan class="pv-gapnum">${Math.round(p50 - p.points)}</tspan> short${cost}</text>`;
    });
  let axis = "";
  for (let t = Math.ceil(xlo / 50) * 50; t <= bhi; t += 50) {
    axis += `<text x="${X(t).toFixed(1)}" y="${T + ph + 33}" text-anchor="middle" class="pc-tick">${t}</text>`;
  }
  /* The low and high marks, dashed so the median keeps the chart's one solid
     anchor. Each gets a label only where there is room for one: on a narrow
     distribution all three land within a few pixels and the labels would stack
     on top of each other, and three unreadable numbers are worse than one. */
  let guides = "";
  bands.forEach((b, i) => {
    if (i === 1) return;
    const x = X(b.pts), right = i > 1;
    guides += `<line class="pc-band" x1="${x.toFixed(1)}" y1="${T - 4}" x2="${x.toFixed(1)}" y2="${(T + ph).toFixed(1)}"/>`;
    if (Math.abs(x - med) < 62) return;
    guides += `<text x="${(x + (right ? 4 : -4)).toFixed(1)}" y="${T + 7}"
      text-anchor="${right ? "start" : "end"}" class="pc-bandlabel">${b.label} ${Math.round(b.pts)}</text>`;
  });
  return `<div class="pv-scroll"><svg class="pv-svg" id="pcutline" width="${W}" height="${H}"
    viewBox="0 0 ${W} ${H}" role="img"
    aria-label="Where the last automatic bid lands across the simulations, against each contender's current points">
    ${bars}${guides}
    <line class="pc-cut" x1="${med.toFixed(1)}" y1="${T - 4}" x2="${med.toFixed(1)}" y2="${T + ph + 70}"/>
    <text x="${(med + 5).toFixed(1)}" y="${T + 7}" class="pc-cutlabel">median ${Math.round(p50)}</text>
    <line class="pv-axis" x1="${L}" y1="${T + ph}" x2="${W - R}" y2="${T + ph}"/>
    ${ticks}${axis}</svg></div>`;
}

/* The chart's table twin: the same three lines, priced. A gap in points is
   abstract until it is a finishing place, which is the unit the weekend is
   actually played in.

   Everyone the rug draws who is not already past the high line — a player
   clear of all three has nothing to read here, and a row of three "clear"s
   would be the only thing most of the table said in September. */
function needsTableHtml(d, q) {
  const ev = closingEvent(d);
  if (!ev) return "";
  const bands = cutBands(q), evName = shortName(ev.name);
  const rows = contenders(d).filter((p) => p.points <= bands[2].pts);
  if (!rows.length) {
    return `<p class="hint">Every contender still in play is already clear of the cutline's high end.</p>`;
  }
  const head = bands.map((b) =>
    `<th class="num" ${tipAttrs(`The worst finish at ${evName} that still carries them past `
      + `${Math.round(b.pts)} points — the cutline in ${b.blurb} (${b.pct} of the simulations). `
      + `"clear" means they are already past it, "any" that last place would do it, `
      + `"—" that a win would not.`)}>${b.head}`
    + `<span class="need-pts">${Math.round(b.pts)}</span></th>`).join("");
  const body = rows.map((p) => {
    const gap = Math.round(bands[1].pts - p.points);
    return `<tr><td class="num dim">${p.rank}</td><td>${nameCell(p)}</td>
      <td class="num">${fmtPts(p.points)}</td>
      <td class="num">${gap > 0 ? gap : `<span class="pos">+${-gap}</span>`}</td>
      ${bands.map((b) => needCell(needAt(d, ev, p, b.pts))).join("")}</tr>`;
  }).join("");
  return `<div class="pv-scroll"><table class="table-ledger detail-tbl pv-tbl need-tbl"><thead><tr>
    <th class="num">#</th><th>Player</th><th class="num">Points</th>
    <th class="num" ${tipAttrs(`Points short of the median cutline. A + is points already clear of it.`)}>Short</th>
    ${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

/* ---------- 4 · head to head ---------- */

/* The cutline is one opponent; the player one row up is the other. What it
   takes to pass someone is not a property of either player alone — it depends
   on what THEY do that weekend, and on the counting caps, which can make the
   identical finish worth 250 points to one of them and 40 to the other.

   Deliberately a pair rather than a matrix. Every combination of 52 players is
   1,326 answers nobody reads; two dropdowns over the same curated list the
   possibility cloud draws is the one answer someone actually came for. */

/* The pair the panel opens on: the two players either side of the automatic-bid
   cut, which is the argument the rest of the page is about. */
function defaultPair(d) {
  const rows = cloudRows(d);
  const i = rows.findIndex((p) => p.rank > d.meta.cut);
  if (i > 0) return [rows[i].pdga, rows[i - 1].pdga];
  return rows.length > 1 ? [rows[1].pdga, rows[0].pdga] : [];
}

/* The reader's pick, or the default — validated against THIS division, since
   the selection survives a division switch and a PDGA number does not. */
function h2hPick(d) {
  const rows = cloudRows(d), saved = state.h2h[state.div] || {};
  const has = (n) => rows.some((p) => p.pdga === n);
  const [da, db] = defaultPair(d);
  const a = has(saved.a) ? saved.a : da, b = has(saved.b) ? saved.b : db;
  return a === b ? [da, db] : [a, b];
}

/* The leader's finishes to price the chase against. A points curve is brutally
   top-heavy — the first three places are worth more than places 20 through 45
   put together — so an even ladder would spend most of its rows on the flat
   part where nothing moves. Last place is always included: the bottom of the
   curve is the scenario a chaser is hoping for. */
const H2H_PLACES = [1, 2, 3, 5, 8, 12, 20, 30, 45, 60];
function h2hScenarios(ev) {
  const size = Math.max(1, Math.round(ev.field_size));
  return H2H_PLACES.filter((k) => k < size).concat(size);
}

/* How many places clear of the leader the chaser has to finish.

   `k − needed` is the arithmetic, but the two are in the same field and cannot
   share a place, so a margin of zero — "you need the place they just took" —
   is really one place ahead. Negative means the caps have eaten the leader's
   result and the chaser can finish behind them and still pass. */
function h2hMargin(k, n) {
  if (n.kind !== "place" && n.kind !== "any") return null;
  const m = k - n.place;
  return m >= 0 ? `${Math.max(1, m)} ahead` : `${-m} behind is fine`;
}

const byPdga = (rows, n) => rows.find((p) => p.pdga === n);

function h2hHtml(d) {
  const rows = cloudRows(d);
  if (rows.length < 2 || !closingEvent(d)) return "";
  const [aN, bN] = h2hPick(d);
  const sel = (id, picked) => `<select class="h2h-sel" id="${id}" aria-label="player">${
    rows.map((p) => `<option value="${p.pdga}"${p.pdga === picked ? " selected" : ""}>${
      p.rank}. ${p.name} — ${fmtPts(p.points)}</option>`).join("")}</select>`;
  return `<div class="pv-tools h2h-tools">${sel("h2h-a", aN)}
    <span class="h2h-vs">vs</span>${sel("h2h-b", bN)}</div>
    <div id="h2h-body">${h2hBodyHtml(d, byPdga(rows, aN), byPdga(rows, bN))}</div>`;
}

function h2hBodyHtml(d, pa, pb) {
  const ev = closingEvent(d);
  if (!pa || !pb || !ev) return "";
  if (pa.pdga === pb.pdga) return `<p class="hint">Pick two different players.</p>`;
  // who chases whom is the standings' answer, not the dropdowns'
  const [low, high] = pa.points <= pb.points ? [pa, pb] : [pb, pa];
  const evName = shortName(ev.name), gap = high.points - low.points;
  const priceLow = pricer(d, ev, low), priceHigh = pricer(d, ev, high);
  // PDGA attribution: an <option> cannot carry a link, so both names are
  // linked here, where the panel says them in prose
  const lead = `<b>${playerLink(high)}</b> leads <b>${playerLink(low)}</b> by <b>${fmtPts(gap)}</b> points`;

  if (!priceLow) {
    return `<p class="pv-lede">${lead}, and ${low.name} is not in the ${evName} field —
      their season is over at ${fmtPts(low.points)} points.</p>`;
  }
  if (!priceHigh) {
    const n = needAt(d, ev, low, high.points, priceLow);
    return `<p class="pv-lede">${lead}. ${high.name} is not in the ${evName} field, so that
      total is final: ${low.name} ${needText(n)}.</p>`;
  }

  /* The finish that settles it, read off the whole curve rather than off the
     ladder: both prices are monotone in place, so the finishes the chaser
     cannot answer are a prefix, and the last of them is the threshold. Taking
     it from the sparse ladder instead would quote 20th when the real line is
     26th. */
  const size = Math.max(1, Math.round(ev.field_size));
  const best = low.points + priceLow(1);
  let shut = 0;
  for (let k = 1; k <= size; k++) if (high.points + priceHigh(k) >= best) shut = k;

  // nothing to price when the lead is bigger than the event can pay: a ladder
  // of eleven dashes is a worse answer than the sentence that explains it
  if (shut >= size) {
    return `<p class="pv-lede">${lead} — more than ${evName} can pay. ${low.name} cannot pass
      them there however the weekend goes.</p>`;
  }
  const settles = shut ? ` ${shut === 1 ? "A win" : `${ordinal(shut)} or better`} from
    ${lastName(high)} puts it out of reach altogether.` : "";
  const scen = h2hScenarios(ev).map((k) => {
    const ends = high.points + priceHigh(k);
    return { k, ends, n: needAt(d, ev, low, ends, priceLow) };
  });
  const body = scen.map((r) => `<tr><td class="num">${ordinal(r.k)}</td>
    <td class="num dim">${fmtPts(r.ends)}</td>
    ${needCell(r.n)}
    <td class="num ${r.n.kind === "never" ? "dim" : ""}">${h2hMargin(r.k, r.n) || "—"}</td></tr>`).join("");
  return `<p class="pv-lede">${lead}. The margin is not a fixed number of places — the curve
    is top-heavy, so a podium from ${lastName(high)} costs ${lastName(low)} far more than a
    30th does.${settles}</p>
    <div class="pv-scroll"><table class="table-ledger detail-tbl pv-tbl h2h-tbl"><thead><tr>
      <th class="num">${lastName(high)} finishes</th>
      <th class="num" ${tipAttrs(`Their season total after that finish, once the counting caps take their cut`)}>Ends on</th>
      <th class="num" ${tipAttrs(`The worst finish that still puts ${low.name} ahead. "any" means last place would do it; "—" means a win would not.`)}>${lastName(low)} needs</th>
      <th class="num" ${tipAttrs(`How far clear of ${lastName(high)} that is. They are in the same field and cannot share a place, so one ahead is as tight as it gets — and "behind is fine" means the caps have eaten ${lastName(high)}'s result.`)}>Margin</th>
    </tr></thead><tbody>${body}</tbody></table></div>`;
}

function wireH2h(root, d) {
  const a = root.querySelector("#h2h-a"), b = root.querySelector("#h2h-b");
  if (!a || !b) return;
  const rows = cloudRows(d);
  const redraw = () => {
    state.h2h[state.div] = { a: +a.value, b: +b.value };
    const holder = root.querySelector("#h2h-body");
    if (holder) holder.innerHTML = h2hBodyHtml(d, byPdga(rows, +a.value), byPdga(rows, +b.value));
  };
  a.addEventListener("change", redraw);
  b.addEventListener("change", redraw);
}

/* ---------- 5 · the ways in ---------- */

/* p_champ is a sum over three unrelated routes, and the headline number hides
   which one a player is actually on. The invite share is the residual: the sim
   counts a DGPT/major winner as in regardless of where they finish, so for a
   player who has already won it is most of their 100%. */
function doorsHtml(d) {
  const m = d.meta;
  const rows = d.players
    .filter((p) => p.p_champ >= 0.02 && p.p_cut < 0.995)
    .sort((a, b) => b.p_champ - a.p_champ || a.rank - b.rank)
    .slice(0, 40);
  if (!rows.length) return `<p class="hint">Every remaining contender's bid is already settled.</p>`;
  const BW = 170;
  const body = rows.map((p) => {
    const inv = Math.max(0, p.p_champ - p.p_cut - p.p_mvp_qual);
    const seg = (v, cls) => (v > 0.004 ? `<i class="${cls}" style="width:${Math.max(2, v * BW).toFixed(1)}px"></i>` : "");
    const side = p.p_mvp_qual > p.p_cut && p.p_mvp_qual > 0.05
      ? ` <span class="side-door" ${tipAttrs(`More likely to reach the Cup from outside the standings cut than inside it`)}>side door</span>` : "";
    return `<tr><td class="num dim">${p.rank}</td><td>${nameCell(p)}${side}</td>
      <td class="num">${fmtPts(p.points)}</td>
      <td><div class="doorbar" ${tipAttrs(`auto ${fmtPct(p.p_cut)} · MVP ${fmtPct(p.p_mvp_qual)} · invite ${fmtPct(inv)}`)}>${
        seg(p.p_cut, "dr-auto")}${seg(p.p_mvp_qual, "dr-mvp")}${seg(inv, "dr-inv")}</div></td>
      <td class="num ${probClass(p.p_cut)}">${fmtPct(p.p_cut)}</td>
      <td class="num">${fmtPct(p.p_mvp_qual)}</td>
      <td class="num dim">${inv > 0.004 ? fmtPct(inv) : ""}</td>
      <td class="num"><b class="${probClass(p.p_champ)}">${fmtPct(p.p_champ)}</b></td></tr>`;
  }).join("");
  return `<div class="pv-scroll pv-tall"><table class="table-ledger detail-tbl pv-tbl"><thead><tr>
    <th class="num">#</th><th>Player</th><th class="num">Points</th><th>Route</th>
    <th class="num" ${tipAttrs(`P(finish top ${m.cut} — automatic bid)`)}>Auto</th>
    <th class="num" ${tipAttrs(`P(earns a spot on a top-${m.field_size - m.cut} MVP Open finish from outside the cut)`)}>MVP</th>
    <th class="num" ${tipAttrs(`Share of their Cup odds that comes from the event-winner invite`)}>Invite</th>
    <th class="num">Cup</th></tr></thead><tbody>${body}</tbody></table></div>`;
}

/* ---------- 6 · where the season actually gets decided ---------- */

const LEV_SAMPLES = 1200;   // per-event points draws, reused across conditions
const LEV_SIMS = 8000;      // cutline samples per conditional run

/* Per remaining event, the swing in automatic-bid odds between a
   90th-percentile week there and a 10th-percentile one — the cutline replay
   the row expander already runs, held fixed at one event's result.

   Conditioning presupposes they are in that field, so an event they aren't
   entered for comes back `na` rather than a leverage of zero dressed up as a
   real number. Everything else keeps replay()'s own conventions: the same
   default attendance set, and the same p_cut-blended cutline. */
function leverageFor(d, p) {
  const evs = d.events, pools = countingPools(d, p);
  const liveSet = liveTidSet(d);
  const plays = evs.map((e, i) =>
    (liveSet.has(e.tid) && p.live && p.live[e.tid]) || p.att[i] >= 0.5);
  const inField = evs.map((e, i) => plays[i] || p.att[i] > 0.02);
  const samples = evs.map((e, i) => (inField[i] ? samplePoints(d, p, e, LEV_SAMPLES) : null));

  const w = p.p_cut, N = Math.min(d.cutline.length, LEV_SIMS);
  const stride = Math.max(1, Math.floor(d.cutline.length / N));
  const scratch = new Float64Array(16);
  const prob = (fixIdx, fixVal) => {
    // which events feed which pool for THIS run (conditioning on an event they
    // weren't projected to play adds it, for that run only)
    const feeds = { dgpt: [], playoff: [], major: [] };
    evs.forEach((e, i) => {
      if (plays[i] || i === fixIdx) feeds[POOL_BY_CLS[e.cls] || "dgpt"].push(i);
    });
    const draw = (i) => (i === fixIdx ? fixVal : samples[i][(Math.random() * LEV_SAMPLES) | 0]);
    const sumOf = (pl, eis) => {
      if (!eis.length) return pl.sum;
      if (eis.length === 1) {
        const v = draw(eis[0]);
        return pl.sum + (v > pl.floor ? v - pl.floor : 0);
      }
      let n = 0;
      for (const v of pl.kept) scratch[n++] = v;
      for (const i of eis) scratch[n++] = draw(i);
      let s = 0;
      for (let a = 0; a < pl.cap && a < n; a++) {   // partial selection sort
        let b = a;
        for (let c = a + 1; c < n; c++) if (scratch[c] > scratch[b]) b = c;
        const t = scratch[a]; scratch[a] = scratch[b]; scratch[b] = t;
        s += scratch[a];
      }
      return s;
    };
    // same exclusive-cutline expectation as replay() — see the long note there
    let hLo = 0, hHi = 0;
    for (let s = 0, i = 0; s < N; s++, i += stride) {
      const total = sumOf(pools.dgpt, feeds.dgpt) + sumOf(pools.playoff, feeds.playoff)
        + sumOf(pools.major, feeds.major) + pools.jomez;
      if (total > d.cutline2[i]) hLo++;
      if (total > d.cutline[i]) hHi++;
    }
    return (w * hLo + (1 - w) * hHi) / N;
  };

  return evs.map((e, i) => {
    if (!inField[i]) return { na: true, att: p.att[i] };
    const pool = pools[POOL_BY_CLS[e.cls] || "dgpt"];
    const winWorth = poolSum(pool, [e.curve[0]]) - pool.sum;
    // a live event's own p10/p90 already know the current score; a fresh
    // pre-tournament sample would not
    const lv = p.live && p.live[e.tid];
    let hi, lo;
    if (lv) { hi = lv.p90; lo = lv.p10; }
    else {
      const s = Float64Array.from(samples[i]).sort();
      hi = s[Math.floor(0.9 * s.length)];
      lo = s[Math.floor(0.1 * s.length)];
    }
    const pHi = prob(i, hi), pLo = prob(i, lo);
    return { lev: Math.max(0, pHi - pLo), pHi, pLo, hi, lo, att: p.att[i],
             optional: !plays[i], live: !!lv, winWorth, nominal: e.curve[0] };
  });
}

function leverageHtml(d) {
  const rows = contenders(d, 24);
  if (!rows.length || !d.events.length) {
    return `<p class="hint">Nothing left to swing — every contender's automatic bid is settled.</p>`;
  }
  const cache = state.lev[state.div] || {};
  const head = d.events.map((e) =>
    `<th class="num" ${tipAttrs(`${CLS_LABEL[e.cls] || e.cls} · a win pays ${fmtPts(e.curve[0])}`)}>${shortName(e.name)}</th>`).join("");
  const body = rows.map((p) => {
    const cells = cache[p.pdga]
      ? cache[p.pdga].map((c, i) => levCell(c, i, p)).join("")
      : d.events.map(() => `<td class="num lev-wait">·</td>`).join("");
    // the current-odds column is coloured on the text only, not as a Ledger
    // cell fill — a filled column right beside the heat ramp reads as part of it
    return `<tr data-pdga="${p.pdga}"><td class="num dim">${p.rank}</td><td>${nameCell(p)}</td>
      <td class="num">${fmtPts(p.points)}</td>
      <td class="num"><span class="${probClass(p.p_cut)}">${fmtPct(p.p_cut)}</span></td>${cells}</tr>`;
  }).join("");
  return `<div class="pv-scroll pv-tall"><table class="table-ledger detail-tbl pv-tbl lev-tbl"><thead><tr>
    <th class="num">#</th><th>Player</th><th class="num">Points</th>
    <th class="num" ${tipAttrs(`The model's current automatic-bid odds`)}>Auto bid</th>${head}
    </tr></thead><tbody>${body}</tbody></table></div>`;
}

function levCell(c, i, p) {
  if (c.na) {
    return `<td class="num lev-na" ${tipAttrs(`Not in this field — this event cannot move their season`)}>n/a</td>`;
  }
  const o = Math.pow(Math.min(1, c.lev / 0.65), 0.75);
  return `<td class="num lev" data-pdga="${p.pdga}" data-e="${i}">
    <i class="lev-fill" style="opacity:${o.toFixed(3)}"></i>
    <span class="lev-t${o > 0.55 ? " on" : ""}">${c.lev < 0.005 ? "·" : "+" + Math.round(c.lev * 100)}</span>
    ${c.optional ? `<span class="lev-opt" ${tipAttrs("Not projected to play — shown as if they do")}>?</span>` : ""}</td>`;
}

/* Computed one player per tick so a 24-row grid never blocks the page: each
   row costs a pre-sample of every remaining event plus nine cutline passes,
   and the whole grid lands in ~1.2s. Results are cached per division.

   A run abandoned partway — the reader switched divisions — drops its own
   partial cache so the next visit recomputes rather than showing a grid that
   is permanently half empty. The generation token makes that safe when runs
   for two divisions overlap. */
let levRun = 0;
function computeLeverage(d, div) {
  if (state.lev[div]) return;               // done, or already in flight
  const gen = ++levRun;
  const cache = state.lev[div] = {};
  const queue = contenders(d, 24);
  const status = (txt) => { const el = $("#lev-status"); if (el) el.textContent = txt; };
  const step = () => {
    if (gen !== levRun || state.div !== div) {
      if (state.lev[div] === cache) delete state.lev[div];
      return;
    }
    const p = queue.shift();
    if (!p) { status(""); return; }
    cache[p.pdga] = leverageFor(d, p);
    const tr = document.querySelector(`.lev-tbl tr[data-pdga="${p.pdga}"]`);
    if (tr) {
      [...tr.querySelectorAll("td")].slice(4).forEach((td) => td.remove());
      tr.insertAdjacentHTML("beforeend", cache[p.pdga].map((c, i) => levCell(c, i, p)).join(""));
    }
    status(queue.length ? `computing… ${queue.length} to go` : "");
    setTimeout(step, 0);
  };
  setTimeout(step, 0);
}

/* ---------- the view ---------- */

function renderPossible(d) {
  const m = d.meta, el = $("#view-possible");
  if (!d.events.length) {
    el.innerHTML = `<p class="hint">The season is over — every event has been played,
      so there is nothing left to simulate.</p>`;
    return;
  }
  const evs = d.events.map((e) => shortName(e.name));
  const eff = effectiveCandidates(d);
  const effCut = eff[m.cut - 1], effOne = eff[0];
  const locked = d.players.filter((p) => p.p_champ >= 0.99).length;
  // one sort of the 25,000-entry cutline, read by the chart, its table, the
  // tick probe and the note below it
  const sortedCl = [...d.cutline].sort((a, b) => a - b);
  const clq = (f) => sortedCl[Math.min(sortedCl.length - 1, Math.floor(f * sortedCl.length))];
  const rug = contenders(d);
  const behind = rug.filter((p) => p.points < clq(0.5));
  const closer = closingEvent(d);
  const sideDoor = d.players.filter((p) => p.p_mvp_qual > p.p_cut && p.p_mvp_qual > 0.1).length;

  const panel = (id, title, form, lede, chart, note) =>
    `<section class="pv-panel"><div class="pv-head"><h2>${title}</h2><span class="pv-form">${form}</span></div>
      <div class="pv-body"><p class="pv-lede">${lede}</p>${chart}
      ${note ? `<p class="pv-note">${note}</p>` : ""}</div></section>`;

  el.innerHTML = `
    <p class="pv-intro"><b>${d.events.length} event${d.events.length === 1 ? "" : "s"} left
      to award points</b> — ${evs.join(", ")}. The Powerball Cup itself pays none.
      These six panels read the same simulation the table does, sideways: not
      "will this player get in" but how much of the season is still open, and where.</p>

    ${panel("cloud", "The possibility cloud", "one row per contender",
      `The finishing-position sparkline from every contender's row, stacked. A hard
       diagonal where the order is settled, smearing into a wide contested band at
       the cut — that smear is the rest of the season.`,
      `<div class="pv-tools"><div class="seg pv-seg" id="cloud-seg">
        <button data-mode="chart" class="${state.cloudMode === "chart" ? "active" : ""}">Cloud</button>
        <button data-mode="table" class="${state.cloudMode === "table" ? "active" : ""}">Numbers</button>
       </div><span class="pv-legend">
        <i class="sw pc-in"></i>inside the cut <i class="sw pc-out"></i>outside it
        <i class="sw pc-over"></i>${m.max_hist_rank}th or worse · opacity scaled to each player's own peak
       </span></div>
       <div id="cloud-holder">${state.cloudMode === "table" ? cloudTableHtml(d) : cloudHtml(d)}</div>`,
      `Rows are ordered by current points, columns are final standings position.
       Tap or hover any cell for the exact odds.`)}

    ${panel("contested", "How contested each position is", "effective candidates",
      `The same matrix read down its columns instead of across its rows: for each
       final position, how many players could still plausibly land there.`,
      contestedHtml(d),
      `Position 1 has <b>${effOne ? effOne.toFixed(1) : "—"}</b> effective candidate${effOne && effOne >= 1.5 ? "s" : ""}${
        effOne && effOne < 1.5 ? " — the top seed is already decided" : ""}, while the last
       automatic bid at #${m.cut} is a <b>${effCut ? effCut.toFixed(0) : "—"}-way</b> scramble.
       ${locked} of ${m.field_size} spots are effectively spoken for.`)}

    ${panel("cutline", "The cutline, and how far away you are", "25,000 simulated seasons",
      `Points only ever go up, so a player's total today is a hard floor and the
       distance to the cutline is a real, closeable number${closer
         ? ` — and one with a price on it: a finishing place at ${shortName(closer.name)}` : ""}.`,
      cutlineHtml(d, clq) + needsTableHtml(d, clq),
      `The ${ordinal(m.cut)} spot lands between <b>${Math.round(clq(0.1))}</b> and
       <b>${Math.round(clq(0.9))}</b> points in 80% of seasons — the two dashed marks.
       ${behind.length ? `${behind.length} contender${behind.length === 1 ? " is" : "s are"} still
       short of the median.` : ""} Ticks are coloured by auto-bid odds; tap or hover one for its gap.
       ${closer ? `Those are the line in a kind season and in a cruel one, and the table prices
       them and the median alike as a finish at
       ${shortName(closer.name)}: the worst place that still clears it once the counting caps
       have taken their cut. It holds the rest of the season where it stands${d.events.length > 1
         ? `, so with ${d.events.length} events left those places are a ceiling — anything banked
       elsewhere lowers the bar` : ""}.` : ""}`)}

    ${panel("h2h", "Head to head", "two players, one field",
      `The cutline is one opponent; the player one row up is the other. What it takes to pass
       someone depends on what they do that weekend — and on the counting caps, which can make
       the identical finish worth ${closer ? fmtPts(closer.curve[0]) : "250"} points to one of
       them and a tenth of that to the other.`,
      h2hHtml(d),
      `Exact arithmetic on the ${closer ? shortName(closer.name) : "closing event"} points curve,
       not a simulation: it says what has to happen, not how likely it is. Both players are in
       the same field and cannot share a place, so "1 ahead" is as tight as a margin gets. The
       list is everyone whose standing is still live, ordered by points.`)}

    ${panel("doors", "The ways in", "three routes, one number",
      `The Cup number is a sum over three unrelated routes — finish inside the
       standings cut, finish top ${m.field_size - m.cut} at the MVP Open from outside it, or win a points
       event and take the special invite. Players already inside the cut are left out;
       the routes only differ for the ones still fighting.`,
      doorsHtml(d),
      `${sideDoor ? `<b>${sideDoor}</b> player${sideDoor === 1 ? " is" : "s are"} likelier to reach the
       Cup through the MVP Open than through the standings — for them the season is one
       weekend, not four events. ` : ""}${playoffNote(m)}`)}

    ${panel("leverage", "Where the season actually gets decided", "auto-bid swing per event",
      `Per contender, per remaining event: the swing in automatic-bid odds between a
       90th-percentile week there and a 10th-percentile one. This is what makes the
       2026 counting caps visible — best ${m.count_dgpt} DGPT, best ${m.majors_counted} majors, both playoffs — because
       the same event is worth wildly different amounts to players sitting one row apart.`,
      leverageHtml(d),
      `Tap or hover a cell for the two conditional odds and what a win there is actually worth
       after the caps. Runs the row expander's own cutline replay held fixed at one
       result, so figures carry about a point of Monte Carlo noise.
       <span id="lev-status" class="lev-status"></span>`)}`;

  wireCloudTips(el);
  el.querySelectorAll("#cloud-seg button").forEach((b) =>
    b.addEventListener("click", () => {
      state.cloudMode = b.dataset.mode;
      el.querySelectorAll("#cloud-seg button").forEach((x) => x.classList.toggle("active", x === b));
      $("#cloud-holder").innerHTML = state.cloudMode === "table" ? cloudTableHtml(d) : cloudHtml(d);
      wireCloudTips(el);
    })
  );
  wireCutlineTips(el, d, clq);
  wireH2h(el, d);
  wireLevTips(el, d);
  computeLeverage(d, state.div);
}

/* The cloud is one SVG with ~1,000 rects, so hover is resolved from the
   pointer position like sparkCell's, not with a listener per cell. */
function wireCloudTips(root) {
  const svg = root.querySelector("#pcloud");
  if (!svg) return;
  const d = state.data[state.div], m = d.meta;
  const rows = cloudRows(d), H = m.max_hist_rank;
  const LW = 148, RH = 13, TOP = 24, CW = 11.4;
  probe(svg, (x, y) => {
    const r = svg.getBoundingClientRect(), sc = svg.viewBox.baseVal.width / r.width;
    const ri = Math.floor(((y - r.top) * sc - TOP) / RH);
    const k = Math.floor(((x - r.left) * sc - LW) / CW);
    if (ri < 0 || ri >= rows.length || k < 0 || k >= H) return null;
    const p = rows[ri];
    return `${p.name} · ${k === H - 1 ? `${H}th or worse` : ordinal(k + 1)}: ${(p.hist[k] * 100).toFixed(1)}%`;
  });
}

function wireCutlineTips(root, d, q) {
  const svg = root.querySelector("#pcutline");
  if (!svg) return;
  const rug = contenders(d);
  const bands = cutBands(q), p50 = bands[1].pts, ev = closingEvent(d);
  const evName = ev ? shortName(ev.name) : "";
  // priced once per contender rather than three times per pointer move: this
  // resolver runs on every mousemove across the chart
  const price = new Map(rug.map((p) => [p.pdga, ev ? pricer(d, ev, p) : null]));
  probe(svg, (cx, _cy, touch) => {
    const r = svg.getBoundingClientRect(), sc = svg.viewBox.baseVal.width / r.width;
    const x = (cx - r.left) * sc;
    // the ticks are the only readable mark; find the nearest by its x
    const lines = [...svg.querySelectorAll("line.pv-tick")];
    let best = -1, bd = 9e9;
    lines.forEach((ln, i) => {
      const dd = Math.abs(+ln.getAttribute("x1") - x);
      if (dd < bd) { bd = dd; best = i; }
    });
    const p = rug[best];
    if (!p || bd > (touch ? 26 : 10)) return null;
    const gap = Math.round(p50 - p.points);
    const line = `${p.name} · ${fmtPts(p.points)} pts · ` +
      `${gap > 0 ? `${gap} short of` : `${Math.abs(gap)} clear of`} the median cutline · auto bid ${fmtPct(p.p_cut)}`;
    if (!ev) return line;
    const at = bands.map((b) => needAt(d, ev, p, b.pts, price.get(p.pdga)));
    if (at[1].kind === "out") return `${line}\nNot in the ${evName} field`;
    // second line, because #spark-tip is `pre-line` and the three prices are a
    // row of numbers rather than a sentence
    return `${line}\n${evName}: ${bands.map((b, i) => `${b.label} ${needShort(at[i])}`).join(" · ")}`;
  });
}

/* The leverage grid is a table, not a drawing, so unlike the other four it
   resolves its cell by hit-testing the element under the point rather than
   from arithmetic. Probing the table (not the whole panel) keeps the readout
   off the surrounding prose. */
function wireLevTips(root, d) {
  const tbl = root.querySelector(".lev-tbl");
  if (!tbl) return;
  probe(tbl, (x, y) => {
    const hit = document.elementFromPoint(x, y);
    const td = hit && hit.closest && hit.closest("td.lev");
    if (!td) return null;
    const cells = (state.lev[state.div] || {})[+td.dataset.pdga];
    if (!cells) return null;
    const c = cells[+td.dataset.e], ev = d.events[+td.dataset.e];
    if (!c || c.na) return null;
    const capped = c.winWorth < c.nominal - 1
      ? ` (of ${fmtPts(c.nominal)} — the caps eat the rest)` : "";
    return `${shortName(ev.name)}: bad week ${fmtPct(c.pLo)} → good week ${fmtPct(c.pHi)} · ` +
      `a win here adds ${fmtPts(c.winWorth)} pts${capped}`;
  });
}

export { renderPossible };
