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

function raceGeom(r) {
  const { W, H, L, R, T, B } = RC;
  const v = raceAxisVals(r), last = v[v.length - 1];
  // Holes keeps a runway to the finish; time has no known finish instant to
  // draw one to, so the axis ends at the newest observation rather than
  // inventing a projected end.
  const x0 = v[0];
  const x1 = state.raceAxis === "time"
    ? Math.max(last, x0 + 36e5)                       // at least an hour wide
    : Math.max(r.holes, last, x0 + 18);
  const { ymax } = raceScale(r);
  return {
    v, x0, x1, ymax, pw: W - L - R, ph: H - T - B,
    X: (h) => L + ((h - x0) / (x1 - x0)) * (W - L - R),
    Y: (p) => T + (1 - Math.min(1, p / ymax)) * (H - T - B),
  };
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

function raceChartHtml(r) {
  const { W, H, L, R, T, B } = RC;
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
  // Bands along the bottom: rounds on the holes axis, days on the time axis.
  // Same treatment either way — a rule at the boundary, the name centred in
  // its own span, and nothing drawn where there is no room for it.
  const bands = state.raceAxis === "time"
    ? raceDayBands(g)
    : Array.from({ length: Math.ceil(g.x1 / 18) }, (_, k) => ({
        boundary: k * 18,
        from: Math.max(k * 18, g.x0),
        to: Math.min((k + 1) * 18, g.x1),
        label: `R${k + 1}`,
      }));
  let rounds = "";
  for (const b of bands) {
    if (b.to <= b.from) continue;
    if (b.boundary > g.x0) {
      const bx = g.X(b.boundary).toFixed(1);
      rounds += `<line class="rc-round" x1="${bx}" y1="${T}" x2="${bx}" y2="${T + g.ph}"/>`;
    }
    if (g.X(b.to) - g.X(b.from) > 22) {
      rounds += `<text class="rc-rlabel" x="${g.X((b.from + b.to) / 2).toFixed(1)}"
        y="${T + g.ph + 16}" text-anchor="middle">${b.label}</text>`;
    }
  }

  // The holes not yet played, shaded: without it the leader lines running out
  // to the label gutter read as the odds continuing flat to the finish. Only
  // on the holes axis — the time axis ends at the newest observation, so
  // there is no runway, and shading "the future" there would be a guess at
  // when the round finishes.
  const lastX = g.X(g.v[n - 1]), fw = W - R - lastX;
  const future = (state.raceAxis !== "time" && fw > 3)
    ? `<rect class="rc-future" x="${lastX.toFixed(1)}" y="${T}" width="${fw.toFixed(1)}" height="${g.ph}"/>${
        fw > 90 ? `<text class="rc-flabel" x="${(lastX + fw / 2).toFixed(1)}" y="${T + 12}"
          text-anchor="middle">${g.x1 - r.x[n - 1]} holes still to play</text>` : ""}`
    : "";

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
      <td class="num">${l.cur > 0 ? "+" : ""}${l.cur === 0 ? "E" : l.cur}</td>
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

  const axisSeg = `<div class="pv-tools"><div class="seg pv-seg" id="race-seg">
      <button data-axis="time" class="${state.raceAxis === "time" ? "active" : ""}">Time</button>
      <button data-axis="holes" class="${state.raceAxis === "holes" ? "active" : ""}">Holes</button>
     </div><span class="pv-legend">${state.raceAxis === "time"
       ? "real pace — the gaps between rounds are real gaps"
       : "the field's mean progress — every round an equal slice"}</span></div>`;
  const chart = !series ? `<p class="hint">No updates recorded yet — the chart starts with the
      next scoring change.</p>`
    : `${axisSeg}<div id="race-holder">${raceChartHtml(series)}</div>${
        series.x.length < 2 ? `<p class="hint">Tracking started at hole
      ${series.tracked_from} of ${series.holes}; the lines fill in from here as play continues.</p>` : ""}`;

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

    ${panel(onNow ? "Everyone still alive" : "The final list", "&gt;0.1% to win",
      `The full list, in the app's own terms: where they stand, what the model gives them,
       and what winning here would do for their Powerball Cup odds.`,
      raceTableHtml(d, tid, prev, onNow),
      `Score is to par; <b>Proj</b> is the mean simulated finish and <b>Pts</b> the mean DGPT
       points this event pays them. <b>Δ</b> is the move since the previous recorded update.`)}`;

  wireRaceTips(el, series);
  el.querySelectorAll("#race-seg button").forEach((b) =>
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
