/* DGPT Standings Forecast app.
   Three views over per-division JSON bundles; the what-if mode re-simulates
   a single player against frozen per-sim cutlines ("cutline replay"). */
"use strict";

const state = { div: "mpo", data: {}, sort: { key: "p_champ", dir: "desc" }, colsMode: "auto", permalink: null };

// player permalinks: #mpo-75412 opens that division with the player expanded
{
  const m = location.hash.match(/^#(mpo|fpo)-(\d+)$/);
  if (m) { state.div = m[1]; state.permalink = +m[2]; }
}

const $ = (sel) => document.querySelector(sel);
const fmtPts = (x) => (Math.round(x * 100) / 100).toLocaleString("en-US");
// exactly 1.0 / 0.0 in the sim (0 or all failures) reads as a hard lock;
// values that merely round to the extremes stay as >99.9% / <0.1%
const fmtPct = (x) =>
  x >= 0.999995 ? "100%"
  : x >= 0.9995 ? ">99.9%"
  : x <= 0.000005 ? "0%"
  : x < 0.0005 ? "<0.1%"
  : (x * 100).toFixed(1) + "%";

async function loadDiv(div) {
  if (!state.data[div]) {
    const resp = await fetch(`data/${div}.json`);
    state.data[div] = await resp.json();
  }
  if (state.movers === undefined) {
    try { state.movers = await (await fetch("data/movers.json")).json(); }
    catch { state.movers = null; }
  }
  return state.data[div];
}

/* qualitative week-over-week movers panel (from prediction snapshots), with
   the two usual "why"s: the newest result, and registration changes */
function moversHtml(d, div) {
  const m = state.movers && state.movers[div];
  if (!m || !m.movers.length) return "";
  const nameOf = new Map((d.schedule || []).map((s) => [s.tid, shortName(s.name)]));
  const fmtD = (iso) => `${+iso.slice(5, 7)}/${+iso.slice(8, 10)}`;
  const pct0 = (x) => Math.round(x * 100) + "%";
  const rows = m.movers.map((x) => {
    const up = x.delta > 0;
    const rank = x.rank_from ? `#${x.rank_from}→#${x.rank_to}` : `→#${x.rank_to}`;
    const lr = x.last_result
      ? `${nameOf.get(x.last_result.tid) || ""}: ${Math.round(x.last_result.pts)}${placeTag(x.last_result.place)}`
      : '<span class="dim">DNP</span>';
    const regs = [
      ...(x.reg_added || []).map((t) => `<span class="reg-chip reg-in">+ ${nameOf.get(t) || t}</span>`),
      ...(x.reg_removed || []).map((t) => `<span class="reg-chip reg-out">− ${nameOf.get(t) || t}</span>`),
    ].join(" ");
    return `<tr>
      <td class="${up ? "movers-up" : "movers-down"}">${up ? "▲" : "▼"}</td>
      <td><a class="plink" href="https://www.pdga.com/player/${x.pdga}" target="_blank" rel="noopener">${x.name}</a></td>
      <td class="num ${up ? "movers-up" : "movers-down"}">${pct0(x.champ_from)} → ${pct0(x.champ_to)}</td>
      <td class="num dim">${(x.delta > 0 ? "+" : "−") + Math.abs(Math.round(x.delta * 100))}</td>
      <td>${lr}</td>
      <td>${regs}</td>
      <td class="num dim">${rank}</td></tr>`;
  }).join("");
  return `<details class="movers"><summary>Biggest movers — Cup odds since ${fmtD(m.baseline)}</summary>
    <table class="table-ledger detail-tbl"><thead><tr>
      <th></th><th>Player</th><th class="num">Cup odds</th><th class="num">Δ</th><th>Last event</th><th>Registration changes</th><th class="num">Rank</th>
    </tr></thead><tbody>${rows}</tbody></table></details>`;
}

/* ---------- shared bits ---------- */

/* Ledger conditional-formatting fills: green = effectively in, yellow =
   live bubble, plain = long shot */
function probClass(p) {
  if (p >= 0.99) return "pos";
  if (p >= 0.02) return "pend";
  return "dim";
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

/* ---------- forecast view (standings + projections, sortable) ---------- */

// events in progress today (client-side date, so it's current between refreshes)
function liveEvents(d) {
  const today = new Date().toISOString().slice(0, 10);
  return (d.schedule || []).filter((e) => e.start <= today && today <= e.end);
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
  return [
    { key: "rank", label: "#", num: true, get: (p) => p.rank, cell: (p) => `<span class="dim">${p.rank}</span>`, dir0: "asc" },
    { key: "name", label: "Player", num: false, get: (p) => p.name.toLowerCase(), cell: nameCell, dir0: "asc" },
    { key: "points", label: "Points", num: true, get: (p) => p.points, cell: (p) => `<b>${fmtPts(p.points)}</b>`, dir0: "desc" },
    { key: "p_champ", label: "Cup", title: "P(in the Powerball Cup field): automatic bid, MVP-performance qualifier, or a DGPT/Major event win (special invite — 100% if already won)", num: true, get: (p) => p.p_champ, cell: (p) => `<b class="${probClass(p.p_champ)}">${fmtPct(p.p_champ)}</b>`, dir0: "desc" },
    { key: "mean_pts", label: "Proj. pts", hide: "t1", num: true, get: (p) => p.mean_pts, cell: (p) => `<span class="dim">${fmtPts(p.mean_pts)}</span>`, dir0: "desc" },
    { key: "spark", label: "Finish distribution", hide: "t1", num: false, sortable: false, cell: (p) => sparkCell(p, meta) },
    { key: "p_cut", label: "Auto Bid", hide: "t2", title: `P(finish top ${meta.cut} in World Standings — automatic Powerball Cup berth)`, num: true, get: (p) => p.p_cut, cell: (p) => `<span class="${probClass(p.p_cut)}">${fmtPct(p.p_cut)}</span>`, dir0: "desc" },
    { key: "p_mvp_qual", label: "MVP Bid", hide: "t2", title: `P(earns a Cup spot via a top-${perf} MVP Open finish, outside the standings cut)`, num: true, get: (p) => p.p_mvp_qual, cell: (p) => `<span class="${probClass(p.p_mvp_qual)}">${fmtPct(p.p_mvp_qual)}</span>`, dir0: "desc" },
    { key: "p_gmc", label: "GMC", hide: "t3", title: `P(makes the Green Mountain Championship field — top ${meta.gmc_cut} on points, expanding to 120 if it doesn't fill). Assumes every qualifier attends; playoff signups aren't open yet.`, num: true, get: (p) => p.p_gmc, cell: (p) => `<span class="${probClass(p.p_gmc)}">${fmtPct(p.p_gmc)}</span>`, dir0: "desc" },
    { key: "p_mvp", label: "MVP", hide: "t3", title: `P(makes the MVP Open field — top ${meta.mvp_cut} on points plus the top GMC finishers outside that). Assumes every qualifier attends; playoff signups aren't open yet.`, num: true, get: (p) => p.p_mvp, cell: (p) => `<span class="${probClass(p.p_mvp)}">${fmtPct(p.p_mvp)}</span>`, dir0: "desc" },
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
    cols.map((c) => `<td class="${[c.num ? "num" : "", c.key === "spark" ? "sparkcell" : "", c.hide || ""].join(" ").trim()}">${c.cell(p)}</td>`).join("") +
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
    <br><b>Playoff assumption:</b> GMC and MVP fields assume every player who qualifies will attend — signups aren't open yet, so those odds will shift once they are.</p>`;

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

function wireSparkTips(el, meta) {
  const tip = $("#spark-tip");
  el.querySelectorAll(".spark").forEach((sp) => {
    const hist = sparkStore.get(+sp.dataset.pdga);
    sp.addEventListener("mousemove", (e) => {
      const r = sp.getBoundingClientRect();
      const k = Math.min(hist.length - 1, Math.max(0, Math.floor(((e.clientX - r.left) / r.width) * hist.length)));
      const place = k + 1 === hist.length ? `${hist.length}th+` : ordinal(k + 1);
      tip.textContent = `${place}: ${(hist[k] * 100).toFixed(1)}%`;
      tip.hidden = false;
      tip.style.left = e.clientX + 12 + "px";
      tip.style.top = e.clientY - 8 + "px";
    });
    sp.addEventListener("mouseleave", () => { tip.hidden = true; });
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
const PLAYOFF_NOTE = "Playoff registration isn't open yet — the model assumes every player who qualifies for this field will attend. Real signups will shift these odds.";

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

function projectPoints(d, p, e, draws = 2500, rating = p.rating) {
  const out = new Float64Array(draws);
  const places = new Float64Array(draws);
  let wins = 0;
  for (let i = 0; i < draws; i++) {
    const mu = (-(rating - e.field_avg_rating) / d.meta.rating_pts_per_stroke) * e.rounds;
    const s = mu + d.meta.round_sd * Math.sqrt(e.rounds) * randn();
    const lam = Math.min(e.field_size, e.field_size * PHI(s / e.opp_score_sd));
    const place = 1 + Math.min(poisson(lam), Math.round(e.field_size));
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
    if (e.cls === "playoff") note += ` <span class="note-flag" title="${PLAYOFF_NOTE}">⚑ assumes qualifiers attend</span>`;
    if (isLive) note += ` <span class="live-badge"><span class="live-dot"></span>live · now ${s.live.cur >= 0 ? "+" : ""}${s.live.cur}, proj ${ordinal(Math.round(s.live.mean_place))}</span>`;
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

function replay(d, p, attendSet) {
  // banked, split into counting pools once
  const banked = { dgpt: [], playoff: [], major: [], jomez: [] };
  for (const b of p.banked) banked[POOL_BY_CLS[b.cls] || "dgpt"].push(b.pts);
  const events = d.events.filter((e) => attendSet.has(e.tid));
  const n = d.cutline.length;
  // blended cutline ≈ "points of the last spot among OTHER players":
  // if this player was probably inside the cut in the base sim, the true
  // exclusive cutline is closer to the (cut+1)-th total.
  const w = p.p_cut;
  let qualify = 0, sumPts = 0;
  for (let i = 0; i < n; i++) {
    const pools = { dgpt: banked.dgpt.slice(), playoff: banked.playoff.slice(), major: banked.major.slice(), jomez: banked.jomez.slice() };
    for (const e of events) {
      const mu = (-(p.rating - e.field_avg_rating) / d.meta.rating_pts_per_stroke) * e.rounds;
      const s = mu + d.meta.round_sd * Math.sqrt(e.rounds) * randn();
      const lam = Math.min(e.field_size, e.field_size * PHI(s / e.opp_score_sd));
      const place = 1 + Math.min(poisson(lam), Math.round(e.field_size));
      const pts = place <= e.curve.length ? e.curve[place - 1] : 0;
      pools[POOL_BY_CLS[e.cls] || "dgpt"].push(pts);
    }
    const total = seasonTotal(pools, d.meta);
    const cl = w * d.cutline2[i] + (1 - w) * d.cutline[i];
    if (total > cl) qualify++;
    sumPts += total;
  }
  return { pCut: qualify / n, meanPts: sumPts / n };
}

/* ---------- shell ---------- */

async function render() {
  const d = await loadDiv(state.div);
  $("#meta-line").textContent =
    `updated ${d.meta.generated.slice(0, 10)} · ${d.meta.n_sims.toLocaleString()} sims · ` +
    `top ${d.meta.cut} qualify directly, field of ${d.meta.field_size}`;
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

render();
