/* DGPT Standings Forecast app.
   Three views over per-division JSON bundles; the what-if mode re-simulates
   a single player against frozen per-sim cutlines ("cutline replay"). */
"use strict";

const state = { div: "mpo", view: "forecast", data: {}, sort: { key: "p_champ", dir: "desc" },
                colsMode: "auto", permalink: null, moverWin: "week", moversOpen: false,
                cloudMode: "chart", lev: {} };

// player permalinks: #mpo-75412 opens that division with the player expanded
{
  const m = location.hash.match(/^#(mpo|fpo)-(\d+)$/);
  if (m) { state.div = m[1]; state.permalink = +m[2]; }
}

const $ = (sel) => document.querySelector(sel);
// ISO-3166 alpha-2 -> regional-indicator flag emoji (renders as boxed letters
// on Windows, which have no flag glyphs — an acceptable country-code fallback)
const flagEmoji = (cc) =>
  (cc && cc.length === 2)
    ? String.fromCodePoint(...[...cc.toUpperCase()].map((ch) => 0x1f1e6 + ch.charCodeAt(0) - 65))
    : "";
const fmtPts = (x) => (Math.round(x * 100) / 100).toLocaleString("en-US");
// exactly 1.0 / 0.0 in the sim (0 or all failures) reads as a hard lock;
// values that merely round to the extremes stay as >99.9% / <0.1%
const fmtPct = (x) =>
  x >= 0.999995 ? "100%"
  : x >= 0.9995 ? ">99.9%"
  : x <= 0.000005 ? "0%"
  : x < 0.0005 ? "<0.1%"
  : (x * 100).toFixed(1) + "%";

// GitHub Pages serves data/*.json with Cache-Control: max-age=600 and caches
// it at the CDN edge, so a fresh refresh could sit up to 10 min behind its own
// data — and a browser hard-refresh can't force past the edge. A per-load
// query string makes every page load a URL the edge has never cached, so the
// site always shows the newest published data. (These files re-fetch only on
// page load; within a session state.data/state.movers cache them in memory.)
const bust = (path) => `${path}?t=${Date.now()}`;

async function loadDiv(div) {
  if (!state.data[div]) {
    const resp = await fetch(bust(`data/${div}.json`));
    state.data[div] = await resp.json();
  }
  if (state.movers === undefined) {
    try { state.movers = await (await fetch(bust("data/movers.json"))).json(); }
    catch { state.movers = null; }
  }
  if (state.race === undefined) {
    // absent until the pipeline has recorded a live event; the tab has its own
    // empty state rather than the page failing to render without it
    try { state.race = await (await fetch(bust("data/liveodds.json"))).json(); }
    catch { state.race = null; }
  }
  return state.data[div];
}

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

const fmtDShort = (iso) => `${+iso.slice(5, 7)}/${+iso.slice(8, 10)}`;

const MOVER_WINDOWS = [["day", "Day"], ["week", "Week"], ["season", "Season"]];

/* Holes played at a live event, straight from the feed.

   Do NOT go back to deriving it as (e.rounds - live.rem) * 18. Those two
   numbers are measured against different round counts: `rem` comes from the
   event's real round list, while `e.rounds` is the model's per-class constant
   (3, or 4 for a major). Ledgestone plays four rounds as an elite_plus event,
   so the derivation lands a whole round low there — and since the callers read
   thru <= 0 as "hasn't teed off", a wrong value costs the score too.
   Older bundles predate `thru`; fall back to the derivation for those. */
const liveThru = (e, l) =>
  Math.max(0, Math.round(l.thru != null ? l.thru : (e.rounds - l.rem) * 18));

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
        : `<span class="${rd > 0 ? "movers-up" : "movers-down"}" title="${x.rating_from} → ${x.rating_to}">${rd > 0 ? "+" : "−"}${Math.abs(rd)}</span>`;
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
          `<span class="dim" title="Projected before the event, from rating vs this field (median ${ord(l.pre_place)})">exp ${Math.round(l.pre_pts)}</span>`;
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
        seedCell = `<td class="num nowrap ${cls2}" title="Projected final standings position: ${x.proj_rank_from} → ${x.proj_rank_to}">#${rf} → #${rt}</td>`;
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
      <th></th><th>Player</th><th class="num">${isRank ? "Rank" : "Cup odds"}</th><th class="num" title="${isRank ? "Places climbed this month" : "Change in Cup odds"}">Δ</th><th title="${trendTitle}">Trend</th><th class="num" title="Change in PDGA rating over the window — a monthly ratings update can move Cup odds with no event played">Rating Δ</th>${
        showLive
          ? `<th title="Current standing and score at ${shortName((d.schedule.find((s) => s.tid === liveTid) || {}).name || "the live event")}">Now</th><th class="num" title="Projected event points, against what the model expected from this player pre-event">Proj. pts</th>`
          : `<th>Last event</th><th>Registration changes</th>`
      }${isRank ? "" : `<th class="num" title="Projected final standings seed — where the model expects them to end the season, and so the starting strokes they carry into the Cup (top seed −7 MPO / −6 FPO). Moves here tell the seeding battles the Cup odds can't (a No. 2 vs No. 3 race between two locks)">Proj. seed</th>`}<th class="num">${isRank ? "Cup odds" : "Rank"}</th>
    </tr></thead><tbody>${body}</tbody></table></details>`;
}

/* ---------- shared bits ---------- */

/* Ledger conditional formatting: green = effectively in, yellow = live bubble,
   dimmed = long shot. Callers put this on the number itself, which style.css
   colours; put it on a <td> instead and tokens.css fills the whole cell. */
function probClass(p) {
  if (p >= 0.99) return "pos";
  if (p >= 0.02) return "pend";
  return "dim";
}

/* Playoff-field cell. A signed-up player is a fact, not a forecast — mark it
   so 100% here reads differently from a 100% the standings produced. */
function fieldCell(prob, signedUp, eventName) {
  if (signedUp) return `<b class="pos" title="Signed up for the ${eventName} — in the field regardless of where the standings finish">✓ in</b>`;
  return `<span class="${probClass(prob)}">${fmtPct(prob)}</span>`;
}

const monthDay = (iso) => {
  const t = new Date(iso);
  return isNaN(t) ? "" : `${t.getMonth() + 1}/${t.getDate()}`;
};

/* Registration waves that haven't opened yet — the reason an unsigned player
   still has playoff-field odds instead of a zero. */
function pendingWaves(m, keys) {
  const now = Date.now();
  const out = [];
  for (const [key, label] of [["gmc", "GMC"], ["mvp", "MVP"]]) {
    if (!keys.includes(key)) continue;
    for (const ph of ((m.reg_phases || {})[key] || [])) {
      if (Date.parse(ph.opens) <= now) continue;
      // A `perf` wave is won, not invited: the top finishers at the previous
      // playoff event take the last spots, so it reads as a result, not a rank.
      if (ph.perf) out.push(`${label}'s GMC-performance wave (${ph.perf} spots)`);
      else if (ph.top) out.push(`${label} top ${ph.top} on ${monthDay(ph.opens)}`);
    }
  }
  return out;
}

/* What the playoff and Worlds fields currently rest on. Signups where a list
   exists, the qualification gate for everyone the remaining waves haven't
   reached — and, at Worlds, the play-in for the players with no other way in. */
function playoffNote(m) {
  const evs = [
    { key: "gmc", label: "GMC", n: m.gmc_signups || 0, done: !!m.gmc_field_set },
    { key: "mvp", label: "the MVP Open", n: m.mvp_signups || 0, done: !!m.mvp_field_set },
  ];
  const playin = !m.playin_entrants ? "" :
    ` <b>Worlds:</b> ${m.playin_entrants} player${m.playin_entrants === 1 ? "" : "s"} in this table
      missed direct qualification and can only get in by winning one of the ${m.playin_spots}
      spots at the one-round play-in.`;
  const listed = evs.filter((e) => e.n);
  if (!listed.length) {
    return `<b>Playoff assumption:</b> no GMC or MVP Open signup list has been published yet,
      so both fields come from the qualification gate alone and assume every qualifier attends.${playin}`;
  }
  const said = listed.map((e) => e.done
    ? `${e.label}'s field is final at <b>${e.n}</b>`
    : `<b>${e.n}</b> have entered ${e.label}`).join(", and ");
  const open = evs.filter((e) => !e.done);
  const waves = pendingWaves(m, open.map((e) => e.key));
  const tail = !open.length ? "" :
    ` Everyone else still qualifies for ${open.map((e) => e.label).join(" and ")} on points${
      waves.length ? `, because ${waves.join(" and ")} ${waves.length > 1 ? "have" : "has"} yet to open` : ""
    }.`;
  return `<b>Playoff fields:</b> ${said} — read literally, so those players are in whatever
    the standings do.${tail}${playin}`;
}

/* PDGA attribution requirements (pdga.com/dev/developer-program): player
   names link to the player profile, event names to the event page */
const playerLink = (p) =>
  `<a class="plink" href="https://www.pdga.com/player/${p.pdga}" target="_blank" rel="noopener">${p.name}</a>`;
const eventLink = (tid, label) =>
  `<a class="plink" href="https://www.pdga.com/tour/event/${tid}" target="_blank" rel="noopener">${label}</a>`;

// clean up a few official names that don't survive the generic rules
const NAME_OVERRIDES = [[/WGE\s*-\s*OTB Open|OTB Open by MVP/i, "OTB Open"]];

function shortName(name) {
  for (const [re, fixed] of NAME_OVERRIDES) if (re.test(name)) return fixed;
  return name
    .replace(/^DGPT( Playoffs)?( -)? /, "")
    .replace(/^DGPT\+ /, "")
    .replace(/ presented by .*| Presented by .*| powered by .*| by MVP.*/i, "")
    .replace(/^2026 PDGA /, "")
    .replace(/^DGPT JomezPro( -)? /, "Jomez: ");
}

const CLS_LABEL = { elite: "DGPT", elite_plus: "DGPT+", playoff: "playoff", major: "major", doubles: "doubles", jomez: "jomez", championship: "cup" };

/* which banked events count toward the season total under the 2026 per-class
   caps (best N of each class; Jomez bonus always counts). The rest are
   "dropped" and shown struck through. */
const POOL_BY_CLS = { elite: "dgpt", elite_plus: "dgpt", doubles: "dgpt", playoff: "playoff", major: "major", jomez: "jomez" };
function countedTids(p, meta) {
  const pools = { dgpt: [], playoff: [], major: [], jomez: [] };
  for (const b of p.banked) {
    const pool = POOL_BY_CLS[b.cls] || "dgpt";
    pools[pool].push(b);
  }
  const counted = new Set(pools.jomez.map((b) => b.tid)); // all Jomez count
  const keepBest = (arr, n) => arr.slice().sort((a, b) => b.pts - a.pts).slice(0, n).forEach((b) => counted.add(b.tid));
  keepBest(pools.dgpt, meta.count_dgpt);
  keepBest(pools.playoff, meta.count_playoff);
  keepBest(pools.major, meta.majors_counted);
  return counted;
}

/* small inline sparkline of the finishing-rank distribution; hover shows the
   exact place + probability immediately via a shared floating tooltip.
   Drawn as 3 SVG paths (in-cut / beyond / overflow) instead of ~50 bar
   divs — cuts the table from ~28k spark nodes to ~3 per row. */
const sparkStore = new Map(); // pdga -> hist array
function sparkCell(p, meta) {
  sparkStore.set(p.pdga, p.hist);
  const H = 22, W = 120, n = p.hist.length;
  const max = Math.max(...p.hist, 1e-9);
  const bw = W / n, gap = bw * 0.15;
  const d = { in: "", out: "", over: "" };
  for (let k = 0; k < n; k++) {
    const h = Math.max(1, (p.hist[k] / max) * H);
    const key = k + 1 <= meta.cut ? "in" : k === n - 1 ? "over" : "out";
    d[key] += `M${(k * bw).toFixed(1)} ${H}h${(bw - gap).toFixed(1)}v-${h.toFixed(1)}h-${(bw - gap).toFixed(1)}z`;
  }
  return `<div class="spark" data-pdga="${p.pdga}"><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
    <path class="sp-in" d="${d.in}"/><path class="sp-out" d="${d.out}"/><path class="sp-over" d="${d.over}"/></svg></div>`;
}

/* How a Cup starting score reads: negatives as strokes under, 0 as even. */
function fmtStroke(v) { return v < 0 ? `\u2212${-v}` : "E"; }

/* Companion to sparkCell: the score a player would tee off the Cup on, which
   is their finishing position run through the seed ladder. Same 3-path build,
   but the buckets are scores rather than places, and the last one is the
   seasons they never reach the field — drawn in the miss colour so a bubble
   player's row reads as "how good a start, and how often no start at all". */
const strokesStore = new Map(); // pdga -> {values, hist}
function strokesCell(p, meta) {
  const values = meta.start_strokes.values, hist = p.strokes || [];
  if (!hist.length) return "";
  strokesStore.set(p.pdga, { values, hist });
  const H = 22, W = 120, n = hist.length;
  const max = Math.max(...hist, 1e-9);
  const bw = W / n, gap = bw * 0.15;
  const d = { in: "", out: "", over: "" };
  for (let k = 0; k < n; k++) {
    const h = Math.max(1, (hist[k] / max) * H);
    const key = k === n - 1 ? "over" : values[k] < 0 ? "in" : "out";
    d[key] += `M${(k * bw).toFixed(1)} ${H}h${(bw - gap).toFixed(1)}v-${h.toFixed(1)}h-${(bw - gap).toFixed(1)}z`;
  }
  return `<div class="sspark" data-pdga="${p.pdga}"><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
    <path class="sp-in" d="${d.in}"/><path class="sp-out" d="${d.out}"/><path class="sp-over" d="${d.over}"/></svg></div>`;
}

/* ---------- forecast view (standings + projections, sortable) ---------- */

// events in progress today (client-side date, so it's current between refreshes)
/* Which events are in progress is decided in one place — schedule.live_events()
   — and shipped on each schedule row as `live`. Do not re-derive it here.

   Comparing start/end against a UTC date, as this used to, is the same window
   without the grace day: a US Sunday final round finishes after 00:00 UTC, so
   the page called the event over while the pipeline was still tracking it and
   scores were still moving. That bug existed in three copies of this window
   (schedule.py, livecheck.py, here) and bit in all three.

   Bundles published before the flag existed fall back to the old comparison. */
function liveEvents(d) {
  const sched = d.schedule || [];
  if (sched.some((e) => "live" in e)) return sched.filter((e) => e.live);
  const today = new Date().toISOString().slice(0, 10);
  return sched.filter((e) => e.start <= today && today <= e.end);
}
function liveTidSet(d) {
  return new Set(liveEvents(d).map((e) => e.tid));
}

const hasWin = (p) => p.banked.some((b) => b.place === 1);
function nameCell(p) {
  return playerLink(p) + (hasWin(p) ? ' <span class="medal" title="Won a points event this year — event winners who miss the cut get a special Championship invite as a bottom seed">🥇</span>' : "");
}

// Columns in priority order (left = most important). `hide` marks the tier
// that drops out first as the screen narrows (core columns never hide).
// `adv` columns only exist in the Advanced view.
function forecastCols(meta, adv = false) {
  const perf = meta.field_size - meta.cut; // MVP-performance championship spots
  const advCols = !adv ? [] : [
    { key: "p_gmc_cut", label: "GMC pts cut", title: `P(top ${meta.gmc_cut} on points before GMC — inside the primary window, before the field expands to fill)`, num: true, get: (p) => p.p_gmc_cut ?? 0, cell: (p) => `<span class="dim">${fmtPct(p.p_gmc_cut ?? 0)}</span>`, dir0: "desc" },
    { key: "p_mvp_cut", label: "MVP pts cut", title: `P(top ${meta.mvp_cut} on points before the MVP Open — qualifying without needing the GMC-performance path)`, num: true, get: (p) => p.p_mvp_cut ?? 0, cell: (p) => `<span class="dim">${fmtPct(p.p_mvp_cut ?? 0)}</span>`, dir0: "desc" },
    { key: "exp_starts", label: "Proj. starts", title: "Projected remaining events played (sum of attendance odds, playoff gating included)", num: true, get: (p) => p.exp_starts ?? 0, cell: (p) => `<span class="dim">${(p.exp_starts ?? 0).toFixed(1)}</span>`, dir0: "desc" },
    { key: "proj_dropped", label: "Proj. dropped", title: "Expected points from already-banked finishes that end up not counting under the per-class caps", num: true, get: (p) => p.proj_dropped ?? 0, cell: (p) => `<span class="dim">${fmtPts(p.proj_dropped ?? 0)}</span>`, dir0: "desc" },
  ];
  // The starting-strokes distribution answers the question the finish
  // distribution only implies — where you actually tee off — so it takes the
  // slot outright, and Advanced keeps both. Bundles published before the seed
  // ladder existed have no `strokes`, and fall back to the old column alone.
  const finishCol = { key: "spark", label: "Finish distribution", hide: "t1", num: false, sortable: false, cell: (p) => sparkCell(p, meta) };
  const strokesCol = { key: "strokes", label: "Starting strokes", hide: "t1", num: false, sortable: false,
    title: `Distribution of the score they'd start the Cup on: their projected finishing position run through the seed ladder (${fmtStroke((meta.start_strokes || { values: [0] }).values[0])} for the No. 1 seed, E for the bottom seeds), with a final bar for the seasons they miss the field`,
    cell: (p) => strokesCell(p, meta) };
  const distCols = !(meta.start_strokes && meta.start_strokes.values)
    ? [finishCol] : adv ? [strokesCol, finishCol] : [strokesCol];
  // Bundles published before the Cup was simulated carry no p_cup_win; the
  // column is dropped rather than shown as a table of zeroes.
  const hasCupWin = meta.cup_rounds > 0;
  const flagCol = { key: "country", label: "Nat.", num: false, get: (p) => p.country || "zz",
    cell: (p) => p.country ? `<span class="flag" title="${p.country}">${flagEmoji(p.country)}</span>` : "", dir0: "asc" };
  return [
    { key: "rank", label: "#", num: true, get: (p) => p.rank, cell: (p) => `<span class="dim">${p.rank}</span>`, dir0: "asc" },
    ...(adv ? [flagCol] : []),
    { key: "name", label: "Player", num: false, get: (p) => p.name.toLowerCase(), cell: nameCell, dir0: "asc" },
    { key: "points", label: "Points", num: true, get: (p) => p.points, cell: (p) => `<b>${fmtPts(p.points)}</b>`, dir0: "desc" },
    { key: "p_champ", label: "Cup", title: "P(in the Powerball Cup field): automatic bid, MVP-performance qualifier, or a DGPT/Major event win (special invite — 100% if already won)", num: true, get: (p) => p.p_champ, cell: (p) => `<b class="${probClass(p.p_champ)}">${fmtPct(p.p_champ)}</b>`, dir0: "desc" },
    ...(hasCupWin ? [{ key: "p_cup_win", label: "Win Cup", title: `P(wins the Powerball Cup): the four-round championship played out from each seed's starting strokes, over every season where they reach it. Unlike Cup, this one is a race — the whole field's odds add to 100%`, num: true, get: (p) => p.p_cup_win ?? 0, cell: (p) => `<b class="${probClass(p.p_cup_win ?? 0)}">${fmtPct(p.p_cup_win ?? 0)}</b>`, dir0: "desc" }] : []),
    { key: "mean_pts", label: "Proj. pts", hide: "t1", num: true, get: (p) => p.mean_pts, cell: (p) => `<span class="dim">${fmtPts(p.mean_pts)}</span>`, dir0: "desc" },
    ...distCols,
    { key: "p_cut", label: "Auto Bid", hide: "t2", title: `P(finish top ${meta.cut} in World Standings — automatic Powerball Cup berth)`, num: true, get: (p) => p.p_cut, cell: (p) => `<span class="${probClass(p.p_cut)}">${fmtPct(p.p_cut)}</span>`, dir0: "desc" },
    { key: "p_mvp_qual", label: "MVP Bid", hide: "t2", title: `P(earns a Cup spot via a top-${perf} MVP Open finish, outside the standings cut)`, num: true, get: (p) => p.p_mvp_qual, cell: (p) => `<span class="${probClass(p.p_mvp_qual)}">${fmtPct(p.p_mvp_qual)}</span>`, dir0: "desc" },
    { key: "p_gmc", label: "GMC", hide: "t3", title: `P(makes the Green Mountain Championship field — signed up already, or reached by a later registration wave: top ${meta.gmc_cut} on points, expanding to ${meta.gmc_fill || 120} if it doesn't fill)`, num: true, get: (p) => p.p_gmc, cell: (p) => fieldCell(p.p_gmc, p.reg_gmc, "Green Mountain Championship"), dir0: "desc" },
    { key: "p_mvp", label: "MVP", hide: "t3", title: `P(makes the MVP Open field — signed up already, or reached by a later invite tier: top ${meta.mvp_cut} on points, plus the top ${meta.mvp_perf || 8} GMC finishers from outside the field)`, num: true, get: (p) => p.p_mvp, cell: (p) => fieldCell(p.p_mvp, p.reg_mvp, "MVP Open"), dir0: "desc" },
    { key: "rating", label: "Rating", hide: "t4", num: true, get: (p) => p.rating || 0, cell: (p) => `<span class="dim">${p.rating || ""}</span>`, dir0: "desc" },
    { key: "starts", label: "Starts", hide: "t4", num: true, get: (p) => p.banked.length, cell: (p) => `<span class="dim">${p.banked.length}</span>`, dir0: "desc" },
    { key: "mean_rank", label: "Proj. rank", hide: "t4", num: true, get: (p) => p.mean_rank, cell: (p) => `<span class="dim">${p.mean_rank.toFixed(1)}</span>`, dir0: "asc" },
    ...advCols,
  ];
}

function renderForecast(d) {
  const meta = d.meta;
  const cols = forecastCols(meta, state.colsMode === "adv");
  const sort = state.sort;
  const col = cols.find((c) => c.key === sort.key) || cols[0];
  const rows = [...d.players].filter(
    (p) => p.points > 0 || p.p_field >= 0.0005
      || (p.live && Object.keys(p.live).length)
      || (p.att && p.att.some((a) => a >= 0.999))  // registered for a future event
  );
  rows.sort((a, b) => {
    const av = col.get(a), bv = col.get(b);
    const cmp = av < bv ? -1 : av > bv ? 1 : 0;
    return (sort.dir === "asc" ? cmp : -cmp) || a.rank - b.rank;
  });

  const head = cols.map((c) => {
    const active = c.key === sort.key;
    const arrow = active ? (sort.dir === "asc" ? " ▲" : " ▼") : "";
    const cls = [c.num ? "num" : "", c.sortable === false ? "" : "sortable", active ? "sorted" : "", c.hide || ""].join(" ").trim();
    return `<th class="${cls}" data-key="${c.key}"${c.title ? ` title="${c.title}"` : ""}>${c.label}${arrow}</th>`;
  }).join("");

  const body = rows.map((p) =>
    `<tr class="expandable" data-pdga="${p.pdga}">` +
    cols.map((c) => `<td class="${[c.num ? "num" : "", c.key === "spark" || c.key === "strokes" ? "sparkcell" : "", c.key === "country" ? "flagcell" : "", c.hide || ""].join(" ").trim()}">${c.cell(p)}</td>`).join("") +
    "</tr>"
  ).join("");

  const el = $("#view-forecast");
  el.innerHTML = `
    ${moversHtml(d, state.div)}
    <div class="table-tools">
      <div class="seg" id="cols-seg">
        ${[["auto", "Auto"], ["all", "All columns"], ["adv", "Advanced"]].map(([k, lbl]) =>
          `<button data-mode="${k}" class="${state.colsMode === k ? "active" : ""}">${lbl}</button>`).join("")}
      </div>
    </div>
    <div class="table-wrap${state.colsMode !== "auto" ? " cols-all" : ""}">
      <table class="table-ledger" id="forecast-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>
    </div>
    <p class="dim" style="font-size:.75rem;margin-top:6px">${rows.length} players · click a column to sort · click a row for the event breakdown and inline what-if · hover the sparkline for exact odds · <b>Cup</b> = Auto Bid + MVP Bid + event-winner invites.
    🥇 = won a points event this year; a DGPT Elite or Major win earns a guaranteed Cup spot via special invite (Cup = 100%), so these odds already include winning a remaining event.
    <br>${playoffNote(meta)}</p>`;

  // switching the movers window re-renders, so remember the panel was open
  el.querySelectorAll(".movers-seg button").forEach((b) =>
    b.addEventListener("click", () => {
      state.moverWin = b.dataset.mwin;
      state.moversOpen = true;
      renderForecast(d);
    })
  );
  const det = el.querySelector("details.movers");
  if (det) det.addEventListener("toggle", () => { state.moversOpen = det.open; });
  el.querySelectorAll("#cols-seg button").forEach((b) =>
    b.addEventListener("click", () => { state.colsMode = b.dataset.mode; renderForecast(d); })
  );
  el.querySelectorAll("th.sortable").forEach((th) =>
    th.addEventListener("click", () => {
      const key = th.dataset.key;
      const c = cols.find((x) => x.key === key);
      state.sort = { key, dir: state.sort.key === key ? (state.sort.dir === "asc" ? "desc" : "asc") : c.dir0 };
      renderForecast(d);
    })
  );
  el.querySelectorAll("tr.expandable").forEach((tr) =>
    tr.addEventListener("click", (ev) => { if (ev.target.closest("a")) return; toggleDetail(tr, d); })
  );
  wireSparkTips(el, meta);
}

/* Both row sparklines hover the same way — bucket under the pointer, label it,
   float the shared tooltip — and differ only in what a bucket means, so the
   readout is the one thing each caller supplies. */
function wireSparkTips(el, meta) {
  const tip = $("#spark-tip");
  const wire = (sel, read) => el.querySelectorAll(sel).forEach((sp) => {
    const src = read(+sp.dataset.pdga);
    if (!src) return;
    sp.addEventListener("mousemove", (e) => {
      const r = sp.getBoundingClientRect();
      const k = Math.min(src.n - 1, Math.max(0, Math.floor(((e.clientX - r.left) / r.width) * src.n)));
      tip.textContent = src.label(k);
      tip.hidden = false;
      tip.style.left = e.clientX + 12 + "px";
      tip.style.top = e.clientY - 8 + "px";
    });
    sp.addEventListener("mouseleave", () => { tip.hidden = true; });
  });
  wire(".spark", (pdga) => {
    const hist = sparkStore.get(pdga);
    return hist && { n: hist.length, label: (k) =>
      `${k + 1 === hist.length ? `${hist.length}th+` : ordinal(k + 1)}: ${(hist[k] * 100).toFixed(1)}%` };
  });
  wire(".sspark", (pdga) => {
    const st = strokesStore.get(pdga);
    return st && { n: st.hist.length, label: (k) =>
      `${k === st.hist.length - 1 ? "misses the Cup" : `starts at ${fmtStroke(st.values[k])}`}: ${(st.hist[k] * 100).toFixed(1)}%` };
  });
}

function ordinal(n) {
  const s = ["th", "st", "nd", "rd"], v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
}

function toggleDetail(tr, d) {
  const next = tr.nextElementSibling;
  if (next && next.classList.contains("detail")) {
    next.remove();
    history.replaceState(null, "", location.pathname + location.search);
    return;
  }
  tr.parentElement.querySelectorAll("tr.detail").forEach((x) => x.remove());
  history.replaceState(null, "", `#${state.div}-${tr.dataset.pdga}`);
  const p = d.players.find((x) => x.pdga === +tr.dataset.pdga);
  const detail = document.createElement("tr");
  detail.className = "detail";
  const td = document.createElement("td");
  td.colSpan = tr.children.length;
  td.innerHTML = detailHtml(p, d);
  detail.appendChild(td);
  tr.after(detail);
  wireWhatif(detail, p, d);
}

/* inline what-if: toggling upcoming-event attendance re-runs the cutline
   replay and updates the scenario Auto Bid within this row only */
function wireWhatif(detail, p, d) {
  const cutEl = detail.querySelector("#wf-cut");
  const swingEl = detail.querySelector("#wf-swing");
  const recompute = () => {
    const set = new Set([...detail.querySelectorAll(".wf-box:checked")].map((c) => +c.dataset.tid));
    const r = replay(d, p, set);
    cutEl.textContent = fmtPct(r.pCut);
    cutEl.className = "stat " + probClass(r.pCut);
    const sw = r.pCut - p.p_cut;
    if (Math.abs(sw) < 0.005) { swingEl.textContent = ""; swingEl.className = "wf-swing"; }
    else {
      swingEl.textContent = (sw > 0 ? "▲ +" : "▼ −") + (Math.abs(sw) * 100).toFixed(1) + "%";
      swingEl.className = "wf-swing " + (sw > 0 ? "pos" : "neg");
    }
  };
  const attOf = new Map(d.events.map((e, i) => [e.tid, p.att[i]]));
  detail.querySelectorAll(".wf-box").forEach((c) => c.addEventListener("change", recompute));
  detail.querySelector("#wf-reset").addEventListener("click", () => {
    detail.querySelectorAll(".wf-box").forEach((c) => {
      c.checked = (attOf.get(+c.dataset.tid) ?? 0) >= 0.5;
    });
    recompute();
  });
  recompute();
}

// place shown next to a single event's points, small + dim
const placeTag = (place) => (place ? ` <span class="place">${ordinal(place)}</span>` : "");
const DOUBLES_NOTE = "No partner listed yet — projected with a field-average partner. Teams refresh automatically from registration.";
const PLAYOFF_NOTE = "Not signed up yet. Registration opens in waves off the standings, and the later ones haven't — so this field is still modelled from the qualification gate.";
const SIGNED_NOTE = "On the published signup list — in this field in every simulation, whatever the standings do.";
const PLAYIN_NOTE = "Not directly qualified for Worlds: in the field only by winning through the one-round play-in.";

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

function detailHtml(p, d) {
  const meta = d.meta;
  const counted = countedTids(p, meta);
  const attOf = new Map(d.events.map((e, i) => [e.tid, p.att[i]]));
  const dateOf = new Map((d.schedule || []).map((s) => [s.tid, [s.start, s.end]]));
  const dtag = (tid) => {
    const se = dateOf.get(tid);
    if (!se) return "";
    const [ms, ds] = se[0].slice(5).split("-").map(Number);
    const [me, de] = se[1].slice(5).split("-").map(Number);
    const range = ms === me ? (ds === de ? `${ms}/${ds}` : `${ms}/${ds}-${de}`) : `${ms}/${ds}-${me}/${de}`;
    return ` <span class="ev-date">${range}</span>`;
  };

  const banked = [...p.banked].sort((a, b) => b.pts - a.pts).map((b) => {
    const drop = !counted.has(b.tid);
    const win = b.place === 1 ? ' <span class="win-medal" title="Event win">🥇</span>' : "";
    const pd = b.p_drop ?? 0;
    return `<tr class="${drop ? "dropped" : ""}">
      <td>${eventLink(b.tid, shortName(b.event))} <span class="chip">${CLS_LABEL[b.cls] || b.cls || "?"}</span>${dtag(b.tid)}${win}</td>
      <td class="num">${fmtPts(b.pts)}${placeTag(b.place)}</td>
      <td class="num ${pd >= 0.5 ? "drop-hi" : "dim"}">${pd > 0.001 ? Math.round(pd * 100) + "%" : ""}</td></tr>`;
  }).join("");

  // every remaining event — attended or not — so any can be toggled on
  const live = liveTidSet(d);
  const upcoming = d.events.map((e) => {
    const att = attOf.get(e.tid) ?? 0;
    const isLive = live.has(e.tid) && p.live && p.live[e.tid];
    const s = eventProj(d, p, e);
    const dflt = (isLive || att >= 0.5) ? "checked" : "";
    const attTxt = isLive ? "playing" : att >= 0.999 ? "yes" : att <= 0.001 ? "—" : Math.round(att * 100) + "%";
    let note = "";
    if (e.tid === meta.dbl_tid) {
      note += p.dbl && p.dbl.partner_name
        ? ` <span class="chip" title="Doubles team — projected with the averaged team rating (${p.dbl.team_rating})">w/ ${p.dbl.partner_name}</span>`
        : ` <span class="note-flag" title="${DOUBLES_NOTE}">⚑ ${p.dbl ? "partner TBD" : "teams TBD"}</span>`;
    }
    if (e.cls === "playoff") {
      const isGmc = e.tid === meta.gmc_tid;
      const signed = isGmc ? p.reg_gmc : e.tid === meta.mvp_tid ? p.reg_mvp : 0;
      const done = isGmc ? meta.gmc_field_set : meta.mvp_field_set;
      note += signed ? ` <span class="chip" title="${SIGNED_NOTE}">signed up</span>`
        : done ? ""   // field is final and they are not in it: nothing to caveat
        : ` <span class="note-flag" title="${PLAYOFF_NOTE}">⚑ qualification only</span>`;
    }
    // Worlds via the play-in: no schedule row of its own (it awards no
    // points), so it shows up as a qualifier on the event it feeds
    if (e.tid === meta.worlds_tid && p.p_playin > 0) {
      note += ` <span class="note-flag" title="${PLAYIN_NOTE}">⚑ play-in ${fmtPct(p.p_playin)}</span>`;
    }
    if (isLive) {
      // A player with no holes played is still on their pre-tournament even
      // par — show that instead of a misleading "now +0".
      const thru = liveThru(e, s.live);
      const pos = thru <= 0
        ? "yet to tee off"
        : `now ${s.live.cur >= 0 ? "+" : ""}${s.live.cur} thru ${thru}`;
      note += ` <span class="live-badge"><span class="live-dot"></span>live · ${pos}, proj ${ordinal(Math.round(s.live.mean_place))}</span>`;
    }
    // live events are locked in (player is in the field) → checkbox disabled
    return `<tr class="${att <= 0.001 && !isLive ? "not-att" : ""}">
      <td><input type="checkbox" class="wf-box" data-tid="${e.tid}" ${dflt} ${isLive ? "disabled" : ""}></td>
      <td>${eventLink(e.tid, shortName(e.name))} <span class="chip">${CLS_LABEL[e.cls] || e.cls}</span>${dtag(e.tid)}${note}</td>
      <td class="num ${att >= 0.999 || isLive ? "pos" : ""}">${attTxt}</td>
      <td class="num dim">${fmtPts(s.p10)}${placeTag(s.pl90)}</td>
      <td class="num">${fmtPts(s.p50)}${placeTag(s.pl50)}</td>
      <td class="num">${fmtPts(s.p90)}${placeTag(s.pl10)}</td>
      <td class="num ${s.win >= 0.1 ? "pos" : "dim"}">${fmtPct(s.win)}</td></tr>`;
  }).join("");

  return `<div class="detail-grid">
    <div>
      <div class="band">Season so far — counts best ${meta.count_dgpt} DGPT/DGPT+, both playoffs, best ${meta.majors_counted} majors, all Jomez bonus (struck through = doesn't count)</div>
      <table class="table-ledger detail-tbl"><thead><tr><th>Event</th><th class="num">Pts (place)</th><th class="num" title="chance this finish ends up not counting by season's end">Drop odds</th></tr></thead>
        <tbody>${banked || '<tr><td colspan="3" class="dim">no results yet</td></tr>'}</tbody></table>
    </div>
    <div>
      <div class="band">What-if — check the events they'll play; projected points if they do</div>
      <table class="table-ledger detail-tbl"><thead><tr>
        <th></th><th>Event</th><th class="num">Plays</th><th class="num" title="10th-percentile points — a low/floor outcome">10th</th><th class="num">Med</th><th class="num">90th</th><th class="num" title="probability of winning the event">Win%</th>
      </tr></thead><tbody>${upcoming || '<tr><td colspan="7" class="dim">no remaining events</td></tr>'}</tbody></table>
      <div class="wf-scenario" data-pdga="${p.pdga}">
        <span class="stat" id="wf-cut">${fmtPct(p.p_cut)}</span>
        <span class="stat-label">scenario Auto Bid <span class="dim">(model ${fmtPct(p.p_cut)})</span></span>
        <span id="wf-swing" class="wf-swing"></span>
        <button class="btn" id="wf-reset">reset to model</button>
      </div>
    </div>
  </div>`;
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
  // blended cutline ≈ "points of the last spot among OTHER players":
  // if this player was probably inside the cut in the base sim, the true
  // exclusive cutline is closer to the (cut+1)-th total.
  const w = p.p_cut;
  let qualify = 0, sumPts = 0;
  for (let i = 0; i < n; i++) {
    const pools = { dgpt: banked.dgpt.slice(), playoff: banked.playoff.slice(), major: banked.major.slice(), jomez: banked.jomez.slice() };
    for (let ev = 0; ev < events.length; ev++) {
      const pts = ptsSamples[ev][(Math.random() * SAMPLES) | 0];
      pools[POOL_BY_CLS[events[ev].cls] || "dgpt"].push(pts);
    }
    const total = seasonTotal(pools, d.meta);
    const cl = w * d.cutline2[i] + (1 - w) * d.cutline[i];
    if (total > cl) qualify++;
    sumPts += total;
  }
  return { pCut: qualify / n, meanPts: sumPts / n };
}

/* ==========================================================================
   "What's left" view — five reads of the remaining possibility space.

   The forecast table answers "will this player get in" one row at a time.
   These answer the shape questions it can't: how wide is everyone's range,
   which positions are actually still contested, how far the chasers are from
   the line, which door they walk through, and where their season gets decided.

   Panels 1-4 are pure re-reads of fields the bundle already ships (hist,
   cutline, p_cut, p_mvp_qual, att) — no pipeline change. Panel 5 re-runs the
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
    <th class="num" title="P(finish top 5)">Top 5</th>
    <th class="num" title="P(finish inside the automatic-bid cut)">Top ${m.cut}</th>
    <th class="num" title="P(finish inside the championship field size)">Top ${m.field_size}</th>
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
   distance between the two IS the chart. */
function cutlineHtml(d) {
  const m = d.meta, cl = d.cutline;
  if (!cl || cl.length < 100) return "";
  const rug = contenders(d);
  const sorted = [...cl].sort((a, b) => a - b);
  const q = (f) => sorted[Math.min(sorted.length - 1, Math.floor(f * sorted.length))];
  const p50 = q(0.5), blo = q(0.005), bhi = q(0.995);
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
      ticks += `<line class="pv-gap" x1="${x.toFixed(1)}" y1="${y}" x2="${med.toFixed(1)}" y2="${y}"/>
        <circle class="pv-gapdot" cx="${x.toFixed(1)}" cy="${y}" r="2.5"/>
        <text x="${(wide ? x + 5 : x - 5).toFixed(1)}" y="${y - 5}" text-anchor="${wide ? "start" : "end"}"
          class="pv-gaplabel">${p.name.split(/\s+/).pop()} · <tspan class="pv-gapnum">${Math.round(p50 - p.points)}</tspan> short</text>`;
    });
  let axis = "";
  for (let t = Math.ceil(xlo / 50) * 50; t <= bhi; t += 50) {
    axis += `<text x="${X(t).toFixed(1)}" y="${T + ph + 33}" text-anchor="middle" class="pc-tick">${t}</text>`;
  }
  return `<div class="pv-scroll"><svg class="pv-svg" id="pcutline" width="${W}" height="${H}"
    viewBox="0 0 ${W} ${H}" role="img"
    aria-label="Where the last automatic bid lands across the simulations, against each contender's current points">
    ${bars}
    <line class="pc-cut" x1="${med.toFixed(1)}" y1="${T - 4}" x2="${med.toFixed(1)}" y2="${T + ph + 70}"/>
    <text x="${(med + 5).toFixed(1)}" y="${T + 7}" class="pc-cutlabel">median cutline ${Math.round(p50)}</text>
    <line class="pv-axis" x1="${L}" y1="${T + ph}" x2="${W - R}" y2="${T + ph}"/>
    ${ticks}${axis}</svg></div>`;
}

/* ---------- 4 · the ways in ---------- */

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
      ? ` <span class="side-door" title="More likely to reach the Cup from outside the standings cut than inside it">side door</span>` : "";
    return `<tr><td class="num dim">${p.rank}</td><td>${nameCell(p)}${side}</td>
      <td class="num">${fmtPts(p.points)}</td>
      <td><div class="doorbar" title="auto ${fmtPct(p.p_cut)} · MVP ${fmtPct(p.p_mvp_qual)} · invite ${fmtPct(inv)}">${
        seg(p.p_cut, "dr-auto")}${seg(p.p_mvp_qual, "dr-mvp")}${seg(inv, "dr-inv")}</div></td>
      <td class="num ${probClass(p.p_cut)}">${fmtPct(p.p_cut)}</td>
      <td class="num">${fmtPct(p.p_mvp_qual)}</td>
      <td class="num dim">${inv > 0.004 ? fmtPct(inv) : ""}</td>
      <td class="num"><b class="${probClass(p.p_champ)}">${fmtPct(p.p_champ)}</b></td></tr>`;
  }).join("");
  return `<div class="pv-scroll pv-tall"><table class="table-ledger detail-tbl pv-tbl"><thead><tr>
    <th class="num">#</th><th>Player</th><th class="num">Points</th><th>Route</th>
    <th class="num" title="P(finish top ${m.cut} — automatic bid)">Auto</th>
    <th class="num" title="P(earns a spot on a top-${m.field_size - m.cut} MVP Open finish from outside the cut)">MVP</th>
    <th class="num" title="Share of their Cup odds that comes from the event-winner invite">Invite</th>
    <th class="num">Cup</th></tr></thead><tbody>${body}</tbody></table></div>`;
}

/* ---------- 5 · where the season actually gets decided ---------- */

const LEV_SAMPLES = 1200;   // per-event points draws, reused across conditions
const LEV_SIMS = 8000;      // cutline samples per conditional run

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
    let hit = 0;
    for (let s = 0, i = 0; s < N; s++, i += stride) {
      const total = sumOf(pools.dgpt, feeds.dgpt) + sumOf(pools.playoff, feeds.playoff)
        + sumOf(pools.major, feeds.major) + pools.jomez;
      if (total > w * d.cutline2[i] + (1 - w) * d.cutline[i]) hit++;
    }
    return hit / N;
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
    `<th class="num" title="${CLS_LABEL[e.cls] || e.cls} · a win pays ${fmtPts(e.curve[0])}">${shortName(e.name)}</th>`).join("");
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
    <th class="num" title="The model's current automatic-bid odds">Auto bid</th>${head}
    </tr></thead><tbody>${body}</tbody></table></div>`;
}

function levCell(c, i, p) {
  if (c.na) {
    return `<td class="num lev-na" title="Not in this field — this event cannot move their season">n/a</td>`;
  }
  const o = Math.pow(Math.min(1, c.lev / 0.65), 0.75);
  return `<td class="num lev" data-pdga="${p.pdga}" data-e="${i}">
    <i class="lev-fill" style="opacity:${o.toFixed(3)}"></i>
    <span class="lev-t${o > 0.55 ? " on" : ""}">${c.lev < 0.005 ? "·" : "+" + Math.round(c.lev * 100)}</span>
    ${c.optional ? '<span class="lev-opt" title="Not projected to play — shown as if they do">?</span>' : ""}</td>`;
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
  const sortedCl = [...d.cutline].sort((a, b) => a - b);
  const clq = (f) => sortedCl[Math.floor(f * sortedCl.length)];
  const rug = contenders(d);
  const behind = rug.filter((p) => p.points < clq(0.5));
  const sideDoor = d.players.filter((p) => p.p_mvp_qual > p.p_cut && p.p_mvp_qual > 0.1).length;

  const panel = (id, title, form, lede, chart, note) =>
    `<section class="pv-panel"><div class="pv-head"><h2>${title}</h2><span class="pv-form">${form}</span></div>
      <div class="pv-body"><p class="pv-lede">${lede}</p>${chart}
      ${note ? `<p class="pv-note">${note}</p>` : ""}</div></section>`;

  el.innerHTML = `
    <p class="pv-intro"><b>${d.events.length} event${d.events.length === 1 ? "" : "s"} left
      to award points</b> — ${evs.join(", ")}. The Powerball Cup itself pays none.
      These five panels read the same simulation the table does, sideways: not
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
       Hover any cell for the exact odds.`)}

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
       distance to the cutline is a real, closeable number.`,
      cutlineHtml(d),
      `The ${ordinal(m.cut)} spot lands between <b>${Math.round(clq(0.05))}</b> and
       <b>${Math.round(clq(0.95))}</b> points in 90% of seasons.
       ${behind.length ? `${behind.length} contender${behind.length === 1 ? " is" : "s are"} still
       short of the median.` : ""} Ticks are coloured by auto-bid odds; hover one for its gap.`)}

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
      `Hover a cell for the two conditional odds and what a win there is actually worth
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
  wireCutlineTips(el, d);
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
  const tip = $("#spark-tip");
  svg.addEventListener("mousemove", (e) => {
    const r = svg.getBoundingClientRect(), sc = svg.viewBox.baseVal.width / r.width;
    const ri = Math.floor(((e.clientY - r.top) * sc - TOP) / RH);
    const k = Math.floor(((e.clientX - r.left) * sc - LW) / CW);
    if (ri < 0 || ri >= rows.length || k < 0 || k >= H) { tip.hidden = true; return; }
    const p = rows[ri];
    tip.textContent = `${p.name} · ${k === H - 1 ? `${H}th or worse` : ordinal(k + 1)}: ${(p.hist[k] * 100).toFixed(1)}%`;
    tip.hidden = false;
    tip.style.left = e.clientX + 12 + "px";
    tip.style.top = e.clientY - 8 + "px";
  });
  svg.addEventListener("mouseleave", () => { $("#spark-tip").hidden = true; });
}

function wireCutlineTips(root, d) {
  const svg = root.querySelector("#pcutline");
  if (!svg) return;
  const rug = contenders(d);
  const sorted = [...d.cutline].sort((a, b) => a - b);
  const p50 = sorted[Math.floor(0.5 * sorted.length)];
  const tip = $("#spark-tip");
  svg.addEventListener("mousemove", (e) => {
    const r = svg.getBoundingClientRect(), sc = svg.viewBox.baseVal.width / r.width;
    const x = (e.clientX - r.left) * sc;
    // the ticks are the only hoverable mark; find the nearest by its x
    const lines = [...svg.querySelectorAll("line.pv-tick")];
    let best = -1, bd = 9e9;
    lines.forEach((ln, i) => {
      const dd = Math.abs(+ln.getAttribute("x1") - x);
      if (dd < bd) { bd = dd; best = i; }
    });
    const p = rug[best];
    if (!p || bd > 10) { tip.hidden = true; return; }
    const gap = Math.round(p50 - p.points);
    tip.textContent = `${p.name} · ${fmtPts(p.points)} pts · ` +
      `${gap > 0 ? `${gap} short of` : `${Math.abs(gap)} clear of`} the median cutline · auto bid ${fmtPct(p.p_cut)}`;
    tip.hidden = false;
    tip.style.left = e.clientX + 12 + "px";
    tip.style.top = e.clientY - 8 + "px";
  });
  svg.addEventListener("mouseleave", () => { $("#spark-tip").hidden = true; });
}

function wireLevTips(root, d) {
  const tip = $("#spark-tip");
  root.addEventListener("mousemove", (e) => {
    const td = e.target.closest && e.target.closest("td.lev");
    if (!td) return;
    const cells = (state.lev[state.div] || {})[+td.dataset.pdga];
    if (!cells) return;
    const c = cells[+td.dataset.e], ev = d.events[+td.dataset.e];
    const capped = c.winWorth < c.nominal - 1
      ? ` (of ${fmtPts(c.nominal)} — the caps eat the rest)` : "";
    tip.textContent = `${shortName(ev.name)}: bad week ${fmtPct(c.pLo)} → good week ${fmtPct(c.pHi)} · ` +
      `a win here adds ${fmtPts(c.winWorth)} pts${capped}`;
    tip.hidden = false;
    tip.style.left = e.clientX + 12 + "px";
    tip.style.top = e.clientY - 8 + "px";
  });
  root.addEventListener("mouseout", (e) => {
    if (e.target.closest && e.target.closest("td.lev")) $("#spark-tip").hidden = true;
  });
}

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

function raceGeom(r) {
  const { W, H, L, R, T, B } = RC;
  const x0 = r.x[0], x1 = Math.max(r.holes, r.x[r.x.length - 1], x0 + 18);
  const { ymax } = raceScale(r);
  return {
    x0, x1, ymax, pw: W - L - R, ph: H - T - B,
    X: (h) => L + ((h - x0) / (x1 - x0)) * (W - L - R),
    Y: (p) => T + (1 - Math.min(1, p / ymax)) * (H - T - B),
  };
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
  // Round boundaries are the chart's real x-axis: a golf tournament moves in
  // rounds, and holes-played puts the overnight gaps at zero width.
  let rounds = "";
  for (let h = 18; h <= g.x1; h += 18) {
    if (h > g.x0) {
      rounds += `<line class="rc-round" x1="${g.X(h).toFixed(1)}" y1="${T}" x2="${g.X(h).toFixed(1)}" y2="${T + g.ph}"/>`;
    }
    const mid = (Math.max(h - 18, g.x0) + Math.min(h, g.x1)) / 2;
    if (Math.min(h, g.x1) - Math.max(h - 18, g.x0) > 6) {
      rounds += `<text class="rc-rlabel" x="${g.X(mid).toFixed(1)}" y="${T + g.ph + 16}"
        text-anchor="middle">R${h / 18}</text>`;
    }
  }

  // The holes not yet played, shaded: without it the leader lines running out
  // to the label gutter read as the odds continuing flat to the finish.
  const lastX = g.X(r.x[n - 1]), fw = W - R - lastX;
  const future = fw > 3
    ? `<rect class="rc-future" x="${lastX.toFixed(1)}" y="${T}" width="${fw.toFixed(1)}" height="${g.ph}"/>${
        fw > 90 ? `<text class="rc-flabel" x="${(lastX + fw / 2).toFixed(1)}" y="${T + 12}"
          text-anchor="middle">${g.x1 - r.x[n - 1]} holes still to play</text>` : ""}`
    : "";

  let lines = "", ends = "";
  const endY = raceStack(r.series.map((s) => g.Y(s.y[n - 1])), 12.5, T + 4, T + g.ph - 2);
  r.series.forEach((s, si) => {
    const c = `rc-c${si % RC_LINES}`;
    let dAttr = "";
    for (let i = 0; i < n; i++) dAttr += `${i ? "L" : "M"}${g.X(r.x[i]).toFixed(1)} ${g.Y(s.y[i]).toFixed(1)}`;
    lines += n > 1
      ? `<path class="rc-line ${c}" d="${dAttr}"/>`
      : `<circle class="rc-dot ${c}" cx="${g.X(r.x[0]).toFixed(1)}" cy="${g.Y(s.y[0]).toFixed(1)}" r="3"/>`;
    // Labels sit in a column in the right margin, not beside the last point:
    // mid-event that point is nowhere near the right edge, and free-floating
    // labels over the runway read as data. A leader line keeps the link.
    const px = g.X(r.x[n - 1]), py = g.Y(s.y[n - 1]), lx = W - R + 8;
    ends += `<path class="rc-lead ${c}" d="M${px.toFixed(1)} ${py.toFixed(1)}H${
        (lx - 26).toFixed(1)}L${(lx - 4).toFixed(1)} ${endY[si].toFixed(1)}"/>
      <circle class="rc-dot ${c}" cx="${px.toFixed(1)}" cy="${py.toFixed(1)}" r="2.6"/>
      <text class="rc-end ${c}" x="${lx}" y="${(endY[si] + 3.5).toFixed(1)}">${
        labels[si]} <tspan class="rc-endnum">${fmtPct(s.win)}</tspan></text>`;
  });

  return `<div class="pv-scroll"><svg class="pv-svg rc-svg" id="race-chart" width="${W}" height="${H}"
    viewBox="0 0 ${W} ${H}" role="img"
    aria-label="Probability of winning the event, per contender, over the holes played">
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
    <th class="num" title="Change since the previous recorded update">Δ</th>
    <th class="num t2" title="Mean simulated finishing position">Proj</th>
    <th class="num t3" title="Mean DGPT points from this event">Pts</th>
    <th class="num" title="Powerball Cup odds">Cup</th></tr></thead><tbody>${body}</tbody></table></div>`;
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

  const chart = !series ? `<p class="hint">No updates recorded yet — the chart starts with the
      next scoring change.</p>`
    : series.x.length < 2 ? `${raceChartHtml(series)}<p class="hint">Tracking started at hole
      ${series.tracked_from} of ${series.holes}; the lines fill in from here as play continues.</p>`
    : raceChartHtml(series);

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
      `Win probability through the event, one line per contender. Holes played on the
       x-axis rather than clock time, so the overnight gaps close up and each round is
       an equal slice.`,
      chart,
      series ? `Lines are drawn for the ${series.series.length} biggest contender${
       series.series.length === 1 ? "" : "s"} — anyone above ${fmtPct(series.chart_min || 0.001)} now,
       plus up to three who once held ${fmtPct(series.peak_min || 0.15)} and have since fallen off it${
       series.others ? `; ${series.others} more sit above ${fmtPct(series.chart_min || 0.001)}
       but below the chart's cap` : ""}.
       Hover the chart for the standings at any point in the event.` : "")}

    ${panel(onNow ? "Everyone still alive" : "The final list", "&gt;0.1% to win",
      `The full list, in the app's own terms: where they stand, what the model gives them,
       and what winning here would do for their Powerball Cup odds.`,
      raceTableHtml(d, tid, prev, onNow),
      `Score is to par; <b>Proj</b> is the mean simulated finish and <b>Pts</b> the mean DGPT
       points this event pays them. <b>Δ</b> is the move since the previous recorded update.`)}`;

  wireRaceTips(el, series);
}

/* One SVG, up to a dozen lines: hover resolves the nearest recorded update from
   the pointer's x and reports the whole board there, which is the question a
   win-probability chart actually gets asked ("who was ahead at the turn?"). */
function wireRaceTips(root, r) {
  const svg = root.querySelector("#race-chart");
  if (!svg || !r) return;
  const g = raceGeom(r), guide = root.querySelector("#race-guide");
  const tip = $("#spark-tip");
  svg.addEventListener("mousemove", (e) => {
    const rect = svg.getBoundingClientRect(), sc = svg.viewBox.baseVal.width / rect.width;
    const x = (e.clientX - rect.left) * sc;
    let best = 0;
    r.x.forEach((h, i) => { if (Math.abs(g.X(h) - x) < Math.abs(g.X(r.x[best]) - x)) best = i; });
    const gx = g.X(r.x[best]);
    guide.setAttribute("x1", gx.toFixed(1));
    guide.setAttribute("x2", gx.toFixed(1));
    guide.setAttribute("visibility", "visible");
    const board = r.series
      .map((s) => ({ name: s.name, v: s.y[best] }))
      .sort((a, b) => b.v - a.v)
      .filter((s) => s.v > 0.001)
      .slice(0, 8)
      .map((s) => `${s.name} ${fmtPct(s.v)}`);
    tip.textContent = `Hole ${r.x[best]} of ${r.holes}\n${
      board.length ? board.join("\n") : "nobody above 0.1%"}`;
    tip.hidden = false;
    tip.style.left = e.clientX + 12 + "px";
    tip.style.top = e.clientY - 8 + "px";
  });
  svg.addEventListener("mouseleave", () => {
    $("#spark-tip").hidden = true;
    guide.setAttribute("visibility", "hidden");
  });
}

/* ---------- shell ---------- */

/* Last successful standings update. meta.generated is written only after a
   refresh completes, so it dates the numbers rather than the page load.

   Bundles published before the UTC fix carry a naive timestamp stamped by a
   UTC runner; a bare one is therefore read as UTC, not as the viewer's local
   time, which would otherwise shift the age by their whole offset. */
function updatedAt(meta) {
  const s = meta.generated;
  return new Date(/([+-]\d\d:?\d\d|Z)$/.test(s) ? s : s + "Z");
}

const agoText = (mins) =>
  mins < 1 ? "just now"
  : mins < 60 ? `${mins} min ago`
  : mins < 48 * 60 ? `${Math.floor(mins / 60)}h ${mins % 60}m ago`
  : `${Math.floor(mins / 1440)} days ago`;

/* What counts as late is set by the pipeline's own cadence, not a fixed
   number: during a live event the loop re-simulates within ~6 minutes of a
   scoring change, while between events the daily refresh legitimately leaves
   the numbers a day old. Flagging on one threshold would either cry wolf all
   week or stay silent through a stall mid-tournament. */
function freshness(d) {
  const at = updatedAt(d.meta);
  const mins = Math.max(0, Math.round((Date.now() - at.getTime()) / 60000));
  // liveEvents() now carries the backend's own answer, grace day included, so
  // this reads it directly. It used to sniff live player records instead,
  // because the old date-derived version called a Sunday-evening finish over
  // hours before the pipeline stopped tracking it — the window a stall hides in.
  const live = liveEvents(d).length > 0;
  return { at, mins, live, stale: mins > (live ? 25 : 26 * 60) };
}

async function render() {
  const d = await loadDiv(state.div);
  {
    const f = freshness(d);
    const abs = f.at.toISOString().replace("T", " ").replace(":00.000Z", "Z");
    const local = f.at.toLocaleString();
    $("#meta-line").innerHTML =
      `updated <span id="updated-at" class="${f.stale ? "stale" : ""}" ` +
      `title="Last successful standings update&#10;${abs} UTC&#10;${local} local">` +
      `${agoText(f.mins)}</span>${
        f.stale
          ? ` <span class="stale-flag" title="${f.live
              ? "A live event normally republishes within a few minutes of any scoring change"
              : "The daily refresh normally republishes every 24 hours"}">— later than expected</span>`
          : ""
      } · ${d.meta.n_sims.toLocaleString()} sims · ` +
      `top ${d.meta.cut} qualify directly, field of ${d.meta.field_size}`;
  }
  // live "state of the race" hook: how much is decided vs still being fought over
  {
    const spots = d.meta.field_size;
    const locked = d.players.filter((p) => p.p_champ >= 0.99).length;
    const alive = d.players.filter((p) => p.p_champ > 0.02 && p.p_champ < 0.99).length;
    const DIV = state.div.toUpperCase();
    $("#stakes-line").innerHTML = alive === 0
      ? `The ${DIV} field is set — all ${spots} spots decided.`
      : `<b class="lk">${locked}</b> of ${spots} ${DIV} spots effectively locked · ` +
        `<b class="al">${alive}</b> still in contention for the rest`;
  }
  $("#pdga-attribution").innerHTML =
    `Event and player data © ${d.meta.season} <a href="https://www.pdga.com">PDGA</a> · ` +
    `PDGA Authorized Developer`;
  const live = liveEvents(d);
  const note = $("#live-note");
  if (live.length) {
    note.innerHTML = `<span class="live-dot"></span> LIVE now: ${live.map((e) => shortName(e.name)).join(", ")} — results feed in as they finalize`;
    note.hidden = false;
  } else {
    note.hidden = true;
  }
  // all three views read the same bundle; only the visible one is built, and
  // a deep link always lands on the table it points into
  const view = state.permalink ? "forecast" : state.view;
  $("#view-forecast").hidden = view !== "forecast";
  $("#view-possible").hidden = view !== "possible";
  $("#view-race").hidden = view !== "race";
  if (view === "possible") { renderPossible(d); return; }
  if (view === "race") { renderRace(d); return; }

  renderForecast(d);
  if (state.permalink) {  // deep link: expand + scroll to the player once
    const pdga = state.permalink;
    state.permalink = null;
    const tr = document.querySelector(`#forecast-table tr[data-pdga="${pdga}"]`);
    if (tr) {
      toggleDetail(tr, d);
      tr.scrollIntoView({ block: "center" });
    }
  }
}

document.querySelectorAll("#division-seg button").forEach((b) => {
  b.classList.toggle("active", b.dataset.div === state.div);  // honor a deep link's division
  b.addEventListener("click", () => {
    state.div = b.dataset.div;
    history.replaceState(null, "", location.pathname + location.search);
    document.querySelectorAll("#division-seg button").forEach((x) => x.classList.toggle("active", x === b));
    render();
  });
});

document.querySelectorAll("#view-seg button").forEach((b) => {
  b.addEventListener("click", () => {
    state.view = b.dataset.view;
    document.querySelectorAll("#view-seg button").forEach((x) => x.classList.toggle("active", x === b));
    render();
  });
});

render();
