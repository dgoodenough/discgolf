/* The "event odds" tab: the win-probability race at the tournament in
   progress, as a time series and as a table. */

import { $, fmtPct, fmtPts, ordinal, state } from "./core.js";
import { probe, tipAttrs } from "./tooltip.js";
import { liveEvents, liveThru, nameCell, probClass, shortName } from "./cells.js";

/* ==========================================================================
   EVENT ODDS — the win-probability race at the tournament in progress
   ==========================================================================
   The forecast tab answers a season-long question; this one answers the
   question the season is being decided by right now. Two panels over the same
   live projection: the race as a time series (from data/liveodds.json, which
   the pipeline accumulates one block per scoring change) and the contenders as
   a table (straight off this bundle, so it is right even before any history
   has been recorded). */

const RC = { W: 900, H: 360, L: 42, R: 142, T: 14, B: 34 };
const RC_LINES = 12;   // palette size in style.css (.rc-c0 … .rc-c11)

/* Surname is enough to label a line until two contenders share one, which at
   a 200-player major is a coin flip (Schultz/Schultz, the Andersons) — and a
   chart that labels two lines identically is worse than one with long labels. */
function raceLabels(series) {
  const surname = (n) => n.split(/\s+/).pop();
  const seen = new Map();
  series.forEach((s) => seen.set(surname(s.name), (seen.get(surname(s.name)) || 0) + 1));
  return series.map((s) => {
    const parts = s.name.split(/\s+/);
    const label = seen.get(surname(s.name)) > 1 && parts.length > 1
      ? `${parts[0][0]}. ${surname(s.name)}` : surname(s.name);
    return label.length > 13 ? label.slice(0, 12) + "\u2026" : label;
  });
}

/* Vertical label placement: lines converge at the right edge (that is what the
   end of a tournament looks like), so the ends have to be pushed apart or the
   leaders' labels stack on top of each other. Forward pass opens the gaps,
   backward pass pulls the overflow back inside the plot. */
function raceStack(ys, gap, lo, hi) {
  const ix = ys.map((y, i) => i).sort((a, b) => ys[a] - ys[b]);
  const out = ys.slice();
  ix.forEach((i, k) => {
    if (k && out[i] - out[ix[k - 1]] < gap) out[i] = out[ix[k - 1]] + gap;
  });
  for (let k = ix.length - 1; k >= 0; k--) {
    const i = ix[k];
    if (out[i] > hi) out[i] = hi - (ix.length - 1 - k) * gap;
    if (k && out[i] - out[ix[k - 1]] < gap) out[ix[k - 1]] = out[i] - gap;
  }
  ix.forEach((i) => { out[i] = Math.max(lo, out[i]); });
  return out;
}

/* Y is auto-scaled, unlike the row sparklines' pinned 0-100%. Those are read
   as a column and have to be mutually comparable; this is one chart, and for
   most of a tournament every line lives under 30% — pinning it would spend
   three quarters of the plot on empty space and flatten the race into a hairline.
   The ceiling is the smallest gridline multiple that clears the peak. */
function raceScale(r) {
  const peak = Math.max(...r.series.map((s) => Math.max(...s.y)), 0.01);
  const step = [0.02, 0.05, 0.1, 0.2, 0.25, 0.5].find((v) => peak <= v * 5) || 0.25;
  return { step, ymax: Math.min(1, Math.max(step, Math.ceil((peak * 1.05) / step) * step)) };
}

/* The two axes, and why there are two.

   HOLES is the field's mean progress (see dgpt/liveodds.py): rounds come out
   as equal slices and the overnight gaps close to nothing, which is the right
   shape for reading a tournament back.

   TIME is the honest one: it shows the real pace, a frantic last hour is wide,
   and the gaps between rounds are visible as gaps. It costs the dead ground
   overnight — at a three-day event that is a third of the axis — which is
   exactly the trade the toggle exists to let the reader make.

   Both plot the same observations. Neither changes a probability: each column
   is one simulation run at one instant, so a vertical slice sums to ~100%
   whichever axis is showing. Only the spacing differs. */
const raceAxisVals = (r) =>
  state.raceAxis === "time" ? r.t.map((s) => Date.parse(s)) : r.x;

/* The x mapping, shared by both charts on this tab. They are given the same
   W, L and R so their plot areas line up to the pixel: the fork chart sits
   under the race chart and a reader drops straight down from a line to the
   score that line was still worth. Only the box height and the y scale differ. */
function axisGeom(r, box) {
  const { W, H, L, R, T, B } = box;
  const v = raceAxisVals(r), last = v[v.length - 1];
  // Holes keeps a runway to the finish; time has no known finish instant to
  // draw one to, so the axis ends at the newest observation rather than
  // inventing a projected end.
  const x0 = v[0];
  const x1 = state.raceAxis === "time"
    ? Math.max(last, x0 + 36e5)                       // at least an hour wide
    : Math.max(r.holes, last, x0 + 18);
  return {
    v, x0, x1, pw: W - L - R, ph: H - T - B,
    X: (h) => L + ((h - x0) / (x1 - x0)) * (W - L - R),
  };
}

function raceGeom(r) {
  const g = axisGeom(r, RC), { ymax } = raceScale(r);
  return { ...g, ymax, Y: (p) => RC.T + (1 - Math.min(1, p / ymax)) * g.ph };
}

/* Day bands on the time axis, the counterpart of the round bands on holes:
   a rule at each local midnight, the weekday centred in its own day. */
function raceDayBands(g) {
  const out = [];
  const d = new Date(g.x0);
  const cur = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  for (let guard = 0; guard < 40 && cur.getTime() <= g.x1; guard++) {
    const next = new Date(cur.getFullYear(), cur.getMonth(), cur.getDate() + 1);
    out.push({
      boundary: cur.getTime(),
      from: Math.max(cur.getTime(), g.x0),
      to: Math.min(next.getTime(), g.x1),
      label: cur.toLocaleDateString(undefined, { weekday: "short" }),
    });
    cur.setTime(next.getTime());
  }
  return out;
}

/* Bands along the bottom: rounds on the holes axis, days on the time axis.
   Same treatment either way — a rule at the boundary, the name centred in its
   own span, and nothing drawn where there is no room for it. */
function bandsHtml(g, box) {
  const bands = state.raceAxis === "time"
    ? raceDayBands(g)
    : Array.from({ length: Math.ceil(g.x1 / 18) }, (_, k) => ({
        boundary: k * 18,
        from: Math.max(k * 18, g.x0),
        to: Math.min((k + 1) * 18, g.x1),
        label: `R${k + 1}`,
      }));
  let out = "";
  for (const b of bands) {
    if (b.to <= b.from) continue;
    if (b.boundary > g.x0) {
      const bx = g.X(b.boundary).toFixed(1);
      out += `<line class="rc-round" x1="${bx}" y1="${box.T}" x2="${bx}" y2="${box.T + g.ph}"/>`;
    }
    if (g.X(b.to) - g.X(b.from) > 22) {
      out += `<text class="rc-rlabel" x="${g.X((b.from + b.to) / 2).toFixed(1)}"
        y="${box.T + g.ph + 16}" text-anchor="middle">${b.label}</text>`;
    }
  }
  return out;
}

/* The holes not yet played, shaded: without it the lines running out to the
   label gutter read as the numbers continuing flat to the finish. Only on the
   holes axis — the time axis ends at the newest observation, so there is no
   runway, and shading "the future" there would be a guess at when the round
   finishes. The caption goes on the upper chart only; both are the same holes. */
function futureHtml(r, g, box, caption) {
  const n = r.x.length, lastX = g.X(g.v[n - 1]), fw = box.W - box.R - lastX;
  if (state.raceAxis === "time" || fw <= 3) return "";
  return `<rect class="rc-future" x="${lastX.toFixed(1)}" y="${box.T}"
      width="${fw.toFixed(1)}" height="${g.ph}"/>${
    caption && fw > 90 ? `<text class="rc-flabel" x="${(lastX + fw / 2).toFixed(1)}"
      y="${box.T + 12}" text-anchor="middle">${g.x1 - r.x[n - 1]} holes still to play</text>` : ""}`;
}

function raceChartHtml(r) {
  const { W, H, L, R, T } = RC;
  const g = raceGeom(r), n = r.x.length;
  const labels = raceLabels(r.series);

  let grid = "";
  const { step } = raceScale(r);
  for (let p = 0; p <= g.ymax + 1e-9; p += step) {
    const y = g.Y(p);
    grid += `<line class="pv-grid" x1="${L}" y1="${y.toFixed(1)}" x2="${W - R}" y2="${y.toFixed(1)}"/>
      <text class="pc-tick" x="${L - 6}" y="${(y + 3.5).toFixed(1)}" text-anchor="end">${
        +(p * 100).toFixed(1)}%</text>`;
  }
  const rounds = bandsHtml(g, RC), future = futureHtml(r, g, RC, true);

  let lines = "", ends = "";
  const endY = raceStack(r.series.map((s) => g.Y(s.y[n - 1])), 12.5, T + 4, T + g.ph - 2);
  r.series.forEach((s, si) => {
    const c = `rc-c${si % RC_LINES}`;
    let dAttr = "";
    for (let i = 0; i < n; i++) dAttr += `${i ? "L" : "M"}${g.X(g.v[i]).toFixed(1)} ${g.Y(s.y[i]).toFixed(1)}`;
    lines += n > 1
      ? `<path class="rc-line ${c}" d="${dAttr}"/>`
      : `<circle class="rc-dot ${c}" cx="${g.X(g.v[0]).toFixed(1)}" cy="${g.Y(s.y[0]).toFixed(1)}" r="3"/>`;
    // Labels sit in a column in the right margin, not beside the last point:
    // mid-event that point is nowhere near the right edge, and free-floating
    // labels over the runway read as data. A leader line keeps the link.
    const px = g.X(g.v[n - 1]), py = g.Y(s.y[n - 1]), lx = W - R + 8;
    ends += `<path class="rc-lead ${c}" d="M${px.toFixed(1)} ${py.toFixed(1)}H${
        (lx - 26).toFixed(1)}L${(lx - 4).toFixed(1)} ${endY[si].toFixed(1)}"/>
      <circle class="rc-dot ${c}" cx="${px.toFixed(1)}" cy="${py.toFixed(1)}" r="2.6"/>
      <text class="rc-end ${c}" x="${lx}" y="${(endY[si] + 3.5).toFixed(1)}">${
        labels[si]} <tspan class="rc-endnum">${fmtPct(s.win)}</tspan></text>`;
  });

  return `<div class="pv-scroll"><svg class="pv-svg rc-svg" id="race-chart" width="${W}" height="${H}"
    viewBox="0 0 ${W} ${H}" role="img"
    aria-label="Probability of winning the event, per contender, over ${
      state.raceAxis === "time" ? "time" : "the holes played"}">
    ${grid}${future}${rounds}
    <line class="rc-guide" id="race-guide" x1="0" y1="${T}" x2="0" y2="${T + g.ph}" visibility="hidden"/>
    <line class="pv-axis" x1="${L}" y1="${T + g.ph}" x2="${W - R}" y2="${T + g.ph}"/>
    ${lines}${ends}</svg></div>`;
}

/* ==========================================================================
   THE FORK LINE
   ==========================================================================
   The Upshot podcast's question — at what line can you stick a fork in them,
   because they're done? — and the exact complement of the race above. That
   chart asks who is winning. This one asks how far back the tournament is
   still live, which is the question everyone outside the lead card is actually
   asking.

   Each line is a SCORE, not a probability: the worst number on the board still
   holding better than those odds of winning (dgpt/liveodds.py builds them).
   Which makes the reading DOWNWARD from a line the strong one, and it is worth
   being precise about why. Below the 1% line there is nobody above 1% — if
   there were, the line would be down there with them. Above it, nothing is
   promised: a player can sit on the leaders' score and still be a longshot.
   So every band is a ceiling, and the labels say so.

   The cuts nest — everyone above 10% is above 1% — so the lines cannot cross
   and the bands between them are always the right way up. */

const FK = { W: 900, H: 250, L: 42, R: 142, T: 16, B: 34 };
const FK_PAD = 0.25;   // a quarter stroke, so no line ends up riding the frame

/* Golf's own notation, and the same form the table below uses. */
const toPar = (s) => (s === 0 ? "E" : (s > 0 ? "+" : "") + s);

/* Not fmtPct: these are the round numbers the cuts were chosen as — "1%", not
   the formatter's data-precision "1.0%" — and the bottom cut is not really a
   percentage at all. It is "won at least one of the ten thousand". */
const cutLabel = (c) => (c <= 0 ? "any" : +(c * 100).toFixed(1) + "%");

const lastAt = (ys) => {
  for (let i = ys.length - 1; i >= 0; i--) if (ys[i] != null) return i;
  return -1;
};

/* Y is strokes and it runs the way a leaderboard does — best score at the top,
   so the lines climb as the tournament tightens. The domain covers everything
   drawn, the leader included, snapped out to the gridline step: a fork line
   pinned to the frame edge reads as clipped rather than as a number. */
function forkScale(f) {
  const vals = f.lead.concat(...f.lines).filter((v) => v != null);
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const step = [1, 2, 5, 10].find((v) => (hi - lo) / v <= 8) || 10;
  return {
    step,
    lo: Math.floor((lo - FK_PAD) / step) * step,
    hi: Math.ceil((hi + FK_PAD) / step) * step,
  };
}

function forkGeom(r) {
  const g = axisGeom(r, FK), { lo, hi } = forkScale(r.fork);
  return { ...g, lo, hi, Y: (v) => FK.T + ((v - lo) / (hi - lo)) * g.ph };
}

function forkChartHtml(r) {
  const { W, H, L, R, T } = FK;
  const f = r.fork, g = forkGeom(r), n = r.x.length, k = f.cuts.length;

  // A cut nobody has reached yet is a gap, not a zero (dgpt/liveodds.py), so
  // the pen lifts rather than dropping the line to the floor.
  const linePath = (ys) => {
    let d = "", pen = false;
    for (let i = 0; i < n; i++) {
      if (ys[i] == null) { pen = false; continue; }
      d += `${pen ? "L" : "M"}${g.X(g.v[i]).toFixed(1)} ${g.Y(ys[i]).toFixed(1)}`;
      pen = true;
    }
    return d;
  };
  // Each band is filled over the contiguous runs where both its edges exist,
  // so a line that has not started yet leaves open ground instead of a fold.
  const bandPath = (top, bot) => {
    let d = "";
    for (let i = 0; i < n; i++) {
      if (top[i] == null || bot[i] == null) continue;
      let j = i, out = "", back = "";
      while (j + 1 < n && top[j + 1] != null && bot[j + 1] != null) j++;
      for (let m = i; m <= j; m++) {
        const x = g.X(g.v[m]).toFixed(1);
        out += `${m === i ? "M" : "L"}${x} ${g.Y(top[m]).toFixed(1)}`;
        back = `L${x} ${g.Y(bot[m]).toFixed(1)}` + back;
      }
      d += `${out}${back}Z`;
      i = j;
    }
    return d;
  };

  let grid = "";
  const { step } = forkScale(f);
  for (let v = g.lo; v <= g.hi + 1e-9; v += step) {
    const y = g.Y(v);
    grid += `<line class="pv-grid" x1="${L}" y1="${y.toFixed(1)}" x2="${W - R}" y2="${y.toFixed(1)}"/>
      <text class="pc-tick" x="${L - 6}" y="${(y + 3.5).toFixed(1)}" text-anchor="end">${toPar(v)}</text>`;
  }

  // Below the bottom cut is the only band that is not a ceiling but a verdict:
  // nobody down here won a single simulated tournament. It gets the palette's
  // one negative colour; the cuts above it are degrees of alive.
  let fills = `<path class="fk-dead" d="${bandPath(f.lines[0], new Array(n).fill(g.hi))}"/>`;
  for (let i = 1; i < k; i++)
    fills += `<path class="fk-band fk-c${i}" d="${bandPath(f.lines[i], f.lines[i - 1])}"/>`;

  let lines = `<path class="fk-best" d="${linePath(f.lead)}"/>`;
  for (let i = 0; i < k; i++)
    lines += `<path class="fk-line fk-c${i}" d="${linePath(f.lines[i])}"/>`;

  // Same gutter treatment as the race chart: the lines converge as the field
  // thins, so the ends are pushed apart and a dotted leader keeps the link.
  const tips = [{ v: f.lead, cls: "fk-best-end", label: "lead" }]
    .concat(f.cuts.map((c, i) => ({ v: f.lines[i], cls: `fk-c${i}`, label: cutLabel(c) })))
    .map((e) => ({ ...e, i: lastAt(e.v) }))
    .filter((e) => e.i >= 0);
  const ty = raceStack(tips.map((e) => g.Y(e.v[e.i])), 12.5, T + 4, T + g.ph - 2);
  const ends = tips.map((e, si) => {
    const px = g.X(g.v[e.i]), py = g.Y(e.v[e.i]), lx = W - R + 8;
    return `<path class="rc-lead ${e.cls}" d="M${px.toFixed(1)} ${py.toFixed(1)}H${
        (lx - 26).toFixed(1)}L${(lx - 4).toFixed(1)} ${ty[si].toFixed(1)}"/>
      <circle class="rc-dot ${e.cls}" cx="${px.toFixed(1)}" cy="${py.toFixed(1)}" r="2.6"/>
      <text class="rc-end ${e.cls}" x="${lx}" y="${(ty[si] + 3.5).toFixed(1)}">${
        e.label} <tspan class="rc-endnum">${toPar(e.v[e.i])}</tspan></text>`;
  }).join("");

  return `<div class="pv-scroll"><svg class="pv-svg rc-svg" id="fork-chart" width="${W}" height="${H}"
    viewBox="0 0 ${W} ${H}" role="img"
    aria-label="The worst score still holding each chance of winning the event, over ${
      state.raceAxis === "time" ? "time" : "the holes played"}">
    ${fills}${grid}${bandsHtml(g, FK)}${futureHtml(r, g, FK, false)}
    <line class="rc-guide" id="fork-guide" x1="0" y1="${T}" x2="0" y2="${T + g.ph}" visibility="hidden"/>
    <line class="pv-axis" x1="${L}" y1="${T + g.ph}" x2="${W - R}" y2="${T + g.ph}"/>
    ${lines}${ends}</svg></div>`;
}

/* The same scrub as the race chart, reporting the whole ladder at one instant:
   every cut, the score it sat at, and the player who was holding it there. */
function wireForkTips(root, r) {
  const svg = root.querySelector("#fork-chart");
  if (!svg || !r || !r.fork) return;
  const f = r.fork, g = forkGeom(r), guide = root.querySelector("#fork-guide");
  probe(svg, (cx) => {
    const rect = svg.getBoundingClientRect(), sc = svg.viewBox.baseVal.width / rect.width;
    const x = (cx - rect.left) * sc;
    let best = 0;
    g.v.forEach((h, i) => { if (Math.abs(g.X(h) - x) < Math.abs(g.X(g.v[best]) - x)) best = i; });
    const gx = g.X(g.v[best]);
    guide.setAttribute("x1", gx.toFixed(1));
    guide.setAttribute("x2", gx.toFixed(1));
    guide.setAttribute("visibility", "visible");
    const ladder = f.cuts.map((c, i) => {
      const v = f.lines[i][best];
      if (v == null) return `${cutLabel(c)} — nobody there yet`;
      const who = f.names[String(f.who[i][best])];
      return `${cutLabel(c)} ${toPar(v)}${who ? ` · ${who}` : ""}`;
    }).reverse();
    const when = new Date(Date.parse(r.t[best]))
      .toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" });
    return `${when} · hole ${r.x[best]} of ${r.holes}\nlead ${toPar(f.lead[best])}\n${
      ladder.join("\n")}`;
  }, () => guide.setAttribute("visibility", "hidden"));
}

/* The >0.1% list, read off this bundle rather than the history — the table has
   to be right on the very first refresh of an event, before any series exists. */
function raceRows(d, tid) {
  const key = String(tid);
  return d.players
    .filter((p) => p.live && p.live[key] && p.live[key].win > 0.001)
    .map((p) => ({ p, l: p.live[key] }))
    .sort((a, b) => b.l.win - a.l.win || (a.l.place || 999) - (b.l.place || 999));
}

function raceTableHtml(d, tid, prev, onNow) {
  const rows = raceRows(d, tid);
  const ev = (d.events || []).find((e) => e.tid === tid) || { rounds: 0 };
  // A banked event drops out of the live projection entirely, so there is no
  // live row to list once it is over — the chart above is the record of it.
  if (!rows.length) {
    return `<p class="hint">${onNow
      ? "Nobody is above 0.1% any more — the winner is decided."
      : "This event has finished and banked; the chart above is how the race played out."}</p>`;
  }
  const body = rows.map(({ p, l }) => {
    const was = prev ? prev[p.pdga] : null;
    const dl = was == null ? null : l.win - was;
    return `<tr><td class="num dim">${l.place ? ordinal(l.place) : "—"}</td>
      <td>${nameCell(p)}</td>
      <td class="num">${toPar(l.cur)}</td>
      <td class="num dim t2">${liveThru(ev, l)}</td>
      <td class="num"><b class="${probClass(l.win)}">${fmtPct(l.win)}</b></td>
      <td class="num ${dl == null ? "dim" : dl > 0 ? "movers-up" : "movers-down"}">${
        dl == null ? "" : (dl > 0 ? "+" : "−") + (Math.abs(dl) * 100).toFixed(1)}</td>
      <td class="num dim t2">${l.mean_place}</td>
      <td class="num dim t3">${fmtPts(l.mean_pts)}</td>
      <td class="num ${probClass(p.p_champ)}">${fmtPct(p.p_champ)}</td></tr>`;
  }).join("");
  return `<div class="pv-scroll pv-tall"><table class="table-ledger detail-tbl pv-tbl"><thead><tr>
    <th class="num">Pos</th><th>Player</th><th class="num">Score</th>
    <th class="num t2">Thru</th><th class="num">Win</th>
    <th class="num" ${tipAttrs(`Change since the previous recorded update`)}>Δ</th>
    <th class="num t2" ${tipAttrs(`Mean simulated finishing position`)}>Proj</th>
    <th class="num t3" ${tipAttrs(`Mean DGPT points from this event`)}>Pts</th>
    <th class="num" ${tipAttrs(`Powerball Cup odds`)}>Cup</th></tr></thead><tbody>${body}</tbody></table></div>`;
}

function renderRace(d) {
  const el = $("#view-race");
  const r = state.race && state.race[state.div];
  const live = liveEvents(d);
  const panel = (title, form, lede, chart, note) =>
    `<section class="pv-panel"><div class="pv-head"><h2>${title}</h2><span class="pv-form">${form}</span></div>
      <div class="pv-body"><p class="pv-lede">${lede}</p>${chart}
      ${note ? `<p class="pv-note">${note}</p>` : ""}</div></section>`;

  // Nothing live and nothing recorded: say so rather than showing an empty axis.
  if (!r && !live.length) {
    el.innerHTML = `<p class="pv-intro"><b>No event in progress.</b> This tab tracks the
      race to win the tournament that is on — every contender's odds, updated within
      minutes of a scoring change and kept as a series, so you can see who was ever in it
      and where it turned. It fills in as the next event plays.</p>`;
    return;
  }
  // An event that is live now always wins over a finished one still in the
  // history: for the first refresh of a new tournament those differ, and the
  // tab must not show last week's race under this week's live banner.
  const liveTid = live.length ? live[0].tid : null;
  const series = r && (liveTid == null || r.tid === liveTid) ? r : null;
  const tid = series ? series.tid : liveTid;
  const ev = (d.schedule || []).find((s) => s.tid === tid);
  const name = ev ? shortName(ev.name) : `event ${tid}`;
  const onNow = tid === liveTid;
  const rows = raceRows(d, tid);
  const lead = rows[0];

  // deltas for the table come from the previous recorded observation, which the
  // bundle carries for every recorded player rather than only the charted ones
  const prev = series && Object.keys(series.prev || {}).length ? series.prev : null;

  // One setting drives both charts — they are meant to be read as one picture,
  // so a slice has to mean the same instant in each — but the control is drawn
  // on both: a reader down at the fork chart should not have to scroll back up
  // to switch axes.
  const axisTools = (legend) => `<div class="pv-tools"><div class="seg pv-seg">
      <button data-axis="time" class="${state.raceAxis === "time" ? "active" : ""}">Time</button>
      <button data-axis="holes" class="${state.raceAxis === "holes" ? "active" : ""}">Holes</button>
     </div><span class="pv-legend">${legend}</span></div>`;
  const axisNote = state.raceAxis === "time"
    ? "real pace — the gaps between rounds are real gaps"
    : "the field's mean progress — every round an equal slice";
  const chart = !series ? `<p class="hint">No updates recorded yet — the chart starts with the
      next scoring change.</p>`
    : `${axisTools(axisNote)}<div id="race-holder">${raceChartHtml(series)}</div>${
        series.x.length < 2 ? `<p class="hint">Tracking started at hole
      ${series.tracked_from} of ${series.holes}; the lines fill in from here as play continues.</p>` : ""}`;

  // The fork panel only exists once the bundle carries the cuts. The site is
  // served from the last published bundle, which can predate this field by a
  // refresh, so its absence is a normal state and not an error.
  const fork = series && series.fork && series.fork.lines && series.fork.lead
    ? series.fork : null;
  const forkNow = fork && fork.cuts.map((c, i) => {
    const at = lastAt(fork.lines[i]);
    return at < 0 ? null : { cut: c, v: fork.lines[i][at], who: fork.names[String(fork.who[i][at])] };
  });
  const forkSw = (cls, text) => `<span class="sw fk-sw ${cls}"></span>${text}`;

  el.innerHTML = `
    <p class="pv-intro"><b>${name}${onNow ? " is on now" : " — final"}.</b>
      ${rows.length ? `<b>${rows.length}</b> player${rows.length === 1 ? "" : "s"} still have
        better than a 0.1% chance to win it${
        lead ? `, led by <b>${lead.p.name}</b> at ${fmtPct(lead.l.win)}` : ""}. ` : ""}
      ${onNow
        ? `Every simulated season starts from this tournament's real, in-progress scores, so
           these are the same numbers driving the Cup odds on the other two tabs.`
        : `These are the odds as the tournament actually played out, recorded as it went.`}</p>

    ${panel(`Who wins ${name}?`,
      series ? `${series.x.length} update${series.x.length === 1 ? "" : "s"} recorded` : "no history yet",
      `Win probability through the event, one line per contender, ${state.raceAxis === "time"
        ? `against the clock — the pace is real, so a frantic last hour is wide and the
           hours between rounds are visible as the gaps they are`
        : `against how far through the event the field is, which closes the gaps between
           rounds and makes each one an equal slice`}. Neither view moves a number:
       every column is one simulation at one instant.`,
      chart,
      series ? `Lines are drawn for the ${series.series.length} biggest contender${
       series.series.length === 1 ? "" : "s"} — anyone above ${fmtPct(series.chart_min || 0.001)} now,
       plus up to three who once held ${fmtPct(series.peak_min || 0.15)} and have since fallen off it${
       series.others ? `; ${series.others} more sit above ${fmtPct(series.chart_min || 0.001)}
       but below the chart's cap` : ""}.
       Tap or drag the chart for the standings at any point in the event.` : "")}

    ${fork ? panel("Where is the fork line?", "worst score still alive",
      `<b>“At what line can you stick a fork in them, cause they're done?”</b> Each line here
       is a score rather than a probability — the worst number on the board still holding
       better than those odds of winning. ${forkNow && forkNow[0] ? `Right now the fork is in
       at <b>${toPar(forkNow[0].v)}</b>: ${forkNow[0].who
         ? `<b>${forkNow[0].who}</b> is the worst score that has won a single simulated
            ${name}` : `no worse score has won a single simulated ${name}`}${
       forkNow[forkNow.length - 1] ? `, and it takes <b>${toPar(forkNow[forkNow.length - 1].v)}</b>
       to be better than a one-in-ten shot` : ""}. ` : ""}Read it downward: below a line there is
       nobody above those odds, because if there were, the line would be down there with them.`,
      `${axisTools(`<span>worst score still above:</span>${
        fork.cuts.map((c, i) => forkSw(`fk-c${i}`, cutLabel(c))).reverse().join("")} ·${
        forkSw("fk-deadsw", "nobody left")}`)}<div id="fork-holder">${forkChartHtml(series)}</div>`,
      `Above a line nothing is promised — a player can sit on the leaders' score and still be
       a longshot — so each band is a <b>ceiling</b>, not a guarantee, and the clear ground at
       the top is simply where the tournament still is. A line is also only ever a score
       somebody is actually standing on, which is why they start bunched at even par: on
       Thursday morning that is where the whole field is. They cannot cross, since everyone
       above 10% is above 1%. <b>any</b> is the honest floor of a ten-thousand-season
       simulation and so the jumpiest line here — it turns on a single one of those ten
       thousand going someone's way. Tap or drag for the whole ladder at any point.`)
      : ""}

    ${panel(onNow ? "Everyone still alive" : "The final list", "&gt;0.1% to win",
      `The full list, in the app's own terms: where they stand, what the model gives them,
       and what winning here would do for their Powerball Cup odds.`,
      raceTableHtml(d, tid, prev, onNow),
      `Score is to par; <b>Proj</b> is the mean simulated finish and <b>Pts</b> the mean DGPT
       points this event pays them. <b>Δ</b> is the move since the previous recorded update.`)}`;

  wireRaceTips(el, series);
  wireForkTips(el, fork ? series : null);
  el.querySelectorAll("[data-axis]").forEach((b) =>
    b.addEventListener("click", () => {
      state.raceAxis = b.dataset.axis;
      renderRace(d);   // the lede and the legend move with the axis, not just the plot
    })
  );
}

/* One SVG, up to a dozen lines: hover resolves the nearest recorded update from
   the pointer's x and reports the whole board there, which is the question a
   win-probability chart actually gets asked ("who was ahead at the turn?"). */
function wireRaceTips(root, r) {
  const svg = root.querySelector("#race-chart");
  if (!svg || !r) return;
  const g = raceGeom(r), guide = root.querySelector("#race-guide");
  probe(svg, (cx) => {
    const rect = svg.getBoundingClientRect(), sc = svg.viewBox.baseVal.width / rect.width;
    const x = (cx - rect.left) * sc;
    let best = 0;
    g.v.forEach((h, i) => { if (Math.abs(g.X(h) - x) < Math.abs(g.X(g.v[best]) - x)) best = i; });
    const gx = g.X(g.v[best]);
    guide.setAttribute("x1", gx.toFixed(1));
    guide.setAttribute("x2", gx.toFixed(1));
    guide.setAttribute("visibility", "visible");
    const board = r.series
      .map((s) => ({ name: s.name, v: s.y[best] }))
      .sort((a, b) => b.v - a.v)
      .filter((s) => s.v > 0.001)
      .slice(0, 8)
      .map((s) => `${s.name} ${fmtPct(s.v)}`);
    const when = new Date(Date.parse(r.t[best]))
      .toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" });
    return `${when} · hole ${r.x[best]} of ${r.holes}\n${
      board.length ? board.join("\n") : "nobody above 0.1%"}`;
  // the vertical guide is this chart's own cursor, so it retracts with the
  // readout — including when a tap elsewhere or a scroll dismisses it
  }, () => guide.setAttribute("visibility", "hidden"));
}

export { renderRace };
