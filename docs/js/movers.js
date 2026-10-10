/* The "biggest movers" panel: three comparison windows over the prediction
   snapshots, with the per-mover odds sparkline.

   Lives apart from the forecast table it renders into because it reads a
   different file (`data/movers.json`) on a different cadence, and its three
   "why" columns are a self-contained story. */

import { fmtDShort, ordinal, state } from "./core.js";
import { tipAttrs } from "./tooltip.js";
import { liveEvents, liveThru, placeTag, shortName } from "./cells.js";

/* Cup-odds trend sparkline for one mover: a time series over the window's
   horizon, so unlike sparkCell (a distribution, drawn as bars) this is a line.
   Y is pinned to 0-100% rather than auto-scaled per player — these are read
   side-by-side down a column, so they have to be mutually comparable, and
   auto-scaling would make a 2-point wobble look like a collapse. Nulls (before
   a player entered the model) break the line instead of reading as 0%. */
function moversSpark(x, dates, m, meta) {
  const s = x.spark;
  if (!s || s.length < 2) return "";
  const H = 20, W = 76, n = s.length;
  const rank = m.metric === "rank";
  const px = (i) => (i / (n - 1)) * W;
  // odds: 0 at the bottom, 1 at the top. rank: #1 at the TOP, worst at the
  // bottom, scaled to the worst rank actually shown so the column stays
  // mutually comparable rather than compressing everyone against 650th.
  const rmax = Math.max(m.rank_max || 1, 2);
  const py = (v) => (rank ? ((v - 1) / (rmax - 1)) * H : H - v * H);
  let dAttr = "", pen = false, last = null;
  for (let i = 0; i < n; i++) {
    if (s[i] == null) { pen = false; continue; }
    dAttr += `${pen ? "L" : "M"}${px(i).toFixed(1)} ${py(s[i]).toFixed(1)}`;
    pen = true; last = i;
  }
  if (!dAttr) return "";
  const cls = x.delta > 0 ? "movers-up" : "movers-down";
  const base = s.find((v) => v != null);
  const tip = dates
    ? s.map((v, i) => (v == null ? null
        : `${fmtDShort(dates[i])} ${rank ? "#" + v : Math.round(v * 100) + "%"}`))
        .filter(Boolean).join("\n")
    : "";
  // on the rank chart the auto-bid cutline is the meaningful reference — you can
  // see who crossed it — rather than the player's own starting value
  const refY = rank
    ? (meta && meta.cut && meta.cut <= rmax ? py(meta.cut) : null)
    : (base != null ? py(base) : null);
  return `<div class="mspark"><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
    <title>${tip}</title>
    ${refY != null ? `<path class="msp-base" d="M0 ${refY.toFixed(1)}H${W}"/>` : ""}
    <path class="msp-line ${cls}" d="${dAttr}"/>
    ${last != null ? `<circle class="msp-dot ${cls}" cx="${px(last).toFixed(1)}" cy="${py(s[last]).toFixed(1)}" r="1.8"/>` : ""}
  </svg></div>`;
}


const MOVER_WINDOWS = [["day", "Day"], ["week", "Week"], ["season", "Season"]];

/* qualitative movers panel (from prediction snapshots), with three "why"s: the
   newest result, the monthly ratings move, and registration changes. A ratings
   shift alone can drive the Cup odds with no event played. Tabs pick the
   comparison window; the day window's "now" is the live published state, so it
   tracks play during a tournament. */
function moversHtml(d, div) {
  const all = state.movers && state.movers[div];
  if (!all) return "";
  // back-compat: before the first refresh on the windowed schema, movers.json
  // is still the flat single-window shape — treat it as the week tab.
  const windowed = all.movers ? { week: all } : all;
  // keep a window's tab even when it has no movers — "nothing moved this week"
  // is real information, and tabs that appear/disappear per division are worse
  const tabs = MOVER_WINDOWS.filter(([k]) => windowed[k]);
  if (!tabs.length || !tabs.some(([k]) => windowed[k].movers.length)) return "";
  const active = tabs.some(([k]) => k === state.moverWin) ? state.moverWin : tabs[tabs.length - 1][0];
  const m = windowed[active];
  const nameOf = new Map((d.schedule || []).map((s) => [s.tid, shortName(s.name)]));
  const fmtD = (iso) => `${+iso.slice(5, 7)}/${+iso.slice(8, 10)}`;
  const pct0 = (x) => Math.round(x * 100) + "%";
  // the season window is measured in standings places, not odds (odds can't be
  // reconstructed for past dates), so its two end columns swap: the primary
  // cell shows the rank move and the trailing cell current Cup odds
  const isRank = m.metric === "rank";
  // During a tournament the day tab's job is "why did this move TODAY", and the
  // answer is on the course, not in the last event they played. Swap the stale
  // "Last event" column for the live one: where they stand now, and what the
  // model projects for them versus what it expected before a disc was thrown.
  const liveTid = active === "day"
    ? (liveEvents(d).map((e) => e.tid).find((t) => d.players.some((p) => p.live && p.live[t])) ?? null)
    : null;
  const liveOf = new Map(
    liveTid == null ? [] : d.players.filter((p) => p.live && p.live[liveTid]).map((p) => [p.pdga, p.live[liveTid]])
  );
  const showLive = liveOf.size > 0;
  const ord = (n) => (n == null ? "—" : ordinal(Math.round(n)));
  const rows = m.movers.map((x) => {
    const up = x.delta > 0;
    const rank = x.rank_from ? `#${x.rank_from}→#${x.rank_to}` : `→#${x.rank_to}`;
    const primary = isRank ? rank : `${pct0(x.champ_from)} → ${pct0(x.champ_to)}`;
    const deltaCell = isRank
      ? `${x.delta > 0 ? "+" : "−"}${Math.abs(x.delta)}`
      : (x.delta > 0 ? "+" : "−") + Math.abs(Math.round(x.delta * 100));
    const trailing = isRank ? pct0(x.champ_now) : rank;
    const lr = x.last_result
      ? `${nameOf.get(x.last_result.tid) || ""}: ${Math.round(x.last_result.pts)}${placeTag(x.last_result.place)}`
      : '<span class="dim">DNP</span>';
    // rating move: signed + coloured when it changed, "—" when flat, blank when
    // unknown (a pre-ratings-snapshot baseline can't be compared)
    const rd = x.rating_delta;
    const ratingCell = rd == null
      ? '<span class="dim"></span>'
      : rd === 0
        ? '<span class="dim">—</span>'
        : `<span class="${rd > 0 ? "movers-up" : "movers-down"}" ${tipAttrs(`${x.rating_from} → ${x.rating_to}`)}>${rd > 0 ? "+" : "−"}${Math.abs(rd)}</span>`;
    const regs = [
      ...(x.reg_added || []).map((t) => `<span class="reg-chip reg-in">+ ${nameOf.get(t) || t}</span>`),
      ...(x.reg_removed || []).map((t) => `<span class="reg-chip reg-out">− ${nameOf.get(t) || t}</span>`),
    ].join(" ");
    let storyCells;
    if (showLive) {
      const l = liveOf.get(x.pdga);
      if (!l) {
        storyCells = `<td class="dim">not in field</td><td class="num dim">—</td>`;
      } else {
        const thru = liveThru(d.events.find((e) => e.tid === liveTid), l);
        const pos = l.place ? ordinal(l.place) : thru <= 0 ? "yet to tee off" : "—";
        const score = thru <= 0 ? "" : ` · ${l.cur >= 0 ? "+" : ""}${l.cur}`;
        // projected points now vs the pre-event expectation = the whole story
        const beat = l.pre_pts != null && l.mean_pts > l.pre_pts;
        const vs = l.pre_pts == null ? "" :
          `<span class="dim" ${tipAttrs(`Projected before the event, from rating vs this field (median ${ord(l.pre_place)})`)}>exp ${Math.round(l.pre_pts)}</span>`;
        storyCells =
          `<td class="nowrap">${pos}${score}<span class="dim"> thru ${thru}</span></td>` +
          `<td class="num nowrap"><span class="${l.pre_pts == null ? "" : beat ? "movers-up" : "movers-down"}">${Math.round(l.mean_pts)}</span> ${vs}</td>`;
      }
    } else {
      storyCells = `<td>${lr}</td><td>${regs}</td>`;
    }
    // projected final standings seed: the story the odds delta can't tell —
    // between two locked-in players (both 100%) only this number moves
    let seedCell = "";
    if (!isRank) {
      if (x.proj_rank_from == null || x.proj_rank_to == null) {
        seedCell = `<td class="num dim">—</td>`;
      } else {
        const rf = Math.round(x.proj_rank_from), rt = Math.round(x.proj_rank_to);
        const cls2 = rt < rf ? "movers-up" : rt > rf ? "movers-down" : "dim";
        seedCell = `<td class="num nowrap ${cls2}" ${tipAttrs(`Projected final standings position: ${x.proj_rank_from} → ${x.proj_rank_to}`)}>#${rf} → #${rt}</td>`;
      }
    }
    return `<tr>
      <td class="${up ? "movers-up" : "movers-down"}">${up ? "▲" : "▼"}</td>
      <td><a class="plink" href="https://www.pdga.com/player/${x.pdga}" target="_blank" rel="noopener">${x.name}</a></td>
      <td class="num ${up ? "movers-up" : "movers-down"}">${primary}</td>
      <td class="num dim">${deltaCell}</td>
      <td class="msparkcell">${moversSpark(x, m.spark_dates, m, d.meta)}</td>
      <td class="num">${ratingCell}</td>
      ${storyCells}
      ${seedCell}
      <td class="num dim">${trailing}</td></tr>`;
  }).join("");
  const seg = tabs.map(([k, lbl]) =>
    `<button data-mwin="${k}" class="${k === active ? "active" : ""}">${lbl}</button>`).join("");
  const floor = isRank ? "3 places" : `${Math.round((active === "day" ? 0.01 : 0.02) * 100)}%`;
  const body = rows || `<tr><td colspan="${isRank ? 9 : 10}" class="dim">No moves above ${floor} in this window — a quiet stretch.</td></tr>`;
  // the day window's "now" is live, so label it as such rather than pretending
  // it's a snapshot boundary
  const since = `since ${fmtD(m.baseline)}${m.live_latest ? "" : ` → ${fmtD(m.latest)}`}`;
  const trendTitle = isRank ? "Standings rank across the season (dashed line = auto-bid cut)"
    : active === "day" ? "Cup odds, last 7 days" : "Cup odds, last 7 weeks";
  const headline = isRank ? "Biggest movers — standings" : "Biggest movers — Cup odds";
  return `<details class="movers" ${state.moversOpen ? "open" : ""}><summary>${headline} ${since}</summary>
    <div class="seg movers-seg">${seg}</div>
    <table class="table-ledger detail-tbl"><thead><tr>
      <th></th><th>Player</th><th class="num">${isRank ? "Rank" : "Cup odds"}</th><th class="num" ${tipAttrs(`${isRank ? "Places climbed this month" : "Change in Cup odds"}`)}>Δ</th><th ${tipAttrs(`${trendTitle}`)}>Trend</th><th class="num" ${tipAttrs(`Change in PDGA rating over the window — a monthly ratings update can move Cup odds with no event played`)}>Rating Δ</th>${
        showLive
          ? `<th ${tipAttrs(`Current standing and score at ${shortName((d.schedule.find((s) => s.tid === liveTid) || {}).name || "the live event")}`)}>Now</th><th class="num" ${tipAttrs(`Projected event points, against what the model expected from this player pre-event`)}>Proj. pts</th>`
          : `<th>Last event</th><th>Registration changes</th>`
      }${isRank ? "" : `<th class="num" ${tipAttrs(`Projected final standings seed — where the model expects them to end the season, and so the starting strokes they carry into the Cup (top seed −7 MPO / −6 FPO). Moves here tell the seeding battles the Cup odds can't (a No. 2 vs No. 3 race between two locks)`)}>Proj. seed</th>`}<th class="num">${isRank ? "Cup odds" : "Rank"}</th>
    </tr></thead><tbody>${body}</tbody></table></details>`;
}

export { moversHtml };
