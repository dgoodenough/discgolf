/* The forecast tab: the standings table, its column guide, the expanded row,
   and the inline what-if.

   The site's main view. `toggleDetail` is exported as well as `renderForecast`
   because a player permalink (#mpo-75412) has to open a row from the shell. */

import { $, flagEmoji, fmtPct, fmtPctExact, fmtPts, fmtStroke, ordinal, state } from "./core.js";
import { tipAttrs } from "./tooltip.js";
import { CLS_LABEL, countedTids, eventLink, fieldCell, liveThru, liveTidSet, nameCell, placeTag, playoffNote, probClass, shortName, sparkCell, strokesCell, wireSparkTips } from "./cells.js";
import { eventProj, replay } from "./sim.js";
import { moversHtml } from "./movers.js";

/* ==========================================================================
   THE COLUMN GUIDE

   The definitions were always written — they sat in each header's `title`, and
   they are good — but a `title` never fires on touch, and a header cannot be
   made tappable because tapping it has to sort. So the same strings are also
   rendered as a panel, built from `forecastCols` itself rather than a second
   copy of the copy: add a column with a title and it appears here.

   Columns whose meaning is obvious to the author and opaque to everyone else
   carry no `title` at all, so they get a gloss below; and the acronym block at
   the end exists because two of four readers in the review could not decode
   MPO, FPO, GMC or MVP, which are load-bearing in almost every definition on
   the page.
   ========================================================================== */
const COL_GLOSS = {
  rank: "Current position in the World Standings, on points.",
  country: "The player's country, from their PDGA profile.",
  name: "Links to the player's PDGA profile. 🥇 marks a points-event win this season.",
  points: "DGPT points banked so far, after the per-class counting caps — the number the standings are ordered by, and a hard floor, since points only ever go up.",
  mean_pts: "The points total the model expects them to finish the season on, averaged over every simulated season.",
  spark: "Where they finish in the final World Standings across every simulated season, as a distribution: one bar per position, green inside the automatic-bid cut.",
  rating: "PDGA player rating — the model's only input for how well someone plays. About 6 rating points equal one stroke per round in MPO, 7.3 in FPO.",
  starts: "Points events played so far this season.",
  mean_rank: "Their average finishing position in the standings across the simulated seasons.",
};

const ACRONYMS = [
  ["MPO / FPO", "The two divisions: Mixed Pro Open and Female Pro Open. Each has its own standings, its own cut and its own Cup field."],
  ["DGPT", "Disc Golf Pro Tour — the tour whose standings this forecast simulates."],
  ["GMC", "The Green Mountain Championship, the first of the two playoff events."],
  ["MVP Open", "The MVP Open x OTB, the second playoff event. MVP is a disc manufacturer, not an award — an \u201cMVP Bid\u201d is a Cup place earned by finishing high there."],
  ["The Cup", "The Powerball Cup, the season-ending championship this whole forecast points at. It awards no points of its own."],
  ["PDGA", "Professional Disc Golf Association — the source of every result, rating and registration here."],
];

/* One definition list, from the columns actually on screen plus the acronyms
   they lean on. `<details>` rather than a scripted panel: it is keyboard- and
   screen-reader-operable with no JavaScript at all. */
function colGuideHtml(cols, meta) {
  const rows = cols.map((c) => {
    const def = c.title || COL_GLOSS[c.key];
    return def ? `<div><dt>${c.label}</dt><dd>${def}</dd></div>` : "";
  }).join("");
  const acr = ACRONYMS.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("");
  return `<details class="colguide">
    <summary>What do these columns mean?</summary>
    <div class="cg-body">
      <p class="cg-lede">Every column on screen, and the names the definitions use.
        Percentages above 10% are shown to the whole percent — open any row for the
        unrounded figures.</p>
      <dl class="cg-dl">${rows}</dl>
      <p class="cg-band">Names</p>
      <dl class="cg-dl">${acr}</dl>
      <p class="cg-note">Top ${meta.cut} in the standings qualify directly for a field of
        ${meta.field_size}. <a href="how-it-works.html">How the points system works</a>.</p>
    </div>
  </details>`;
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
  const finishCol = { key: "spark", label: "Finish distribution", hide: "t1", num: false, sortable: false,
    title: `Where they finish in the final World Standings across all ${meta.n_sims.toLocaleString()} simulated seasons, as a distribution — one bar per position, green inside the top ${meta.cut}, and a last bar for ${meta.max_hist_rank}th or worse`,
    cell: (p) => sparkCell(p, meta) };
  const strokesCol = { key: "strokes", label: "Starting strokes", hide: "t1", num: false, sortable: false,
    title: `Distribution of the score they'd start the Cup on: their projected finishing position run through the seed ladder (${fmtStroke((meta.start_strokes || { values: [0] }).values[0])} for the No. 1 seed, E for the bottom seeds), with a final bar for the seasons they miss the field`,
    cell: (p) => strokesCell(p, meta) };
  const distCols = !(meta.start_strokes && meta.start_strokes.values)
    ? [finishCol] : adv ? [strokesCol, finishCol] : [strokesCol];
  // Bundles published before the Cup was simulated carry no p_cup_win; the
  // column is dropped rather than shown as a table of zeroes.
  const hasCupWin = meta.cup_rounds > 0;
  const flagCol = { key: "country", label: "Nat.", hide: "t4", num: false, get: (p) => p.country || "zz",
    cell: (p) => p.country ? `<span class="flag" title="${p.country}">${flagEmoji(p.country)}</span>` : "", dir0: "asc" };
  return [
    { key: "rank", label: "#", num: true, get: (p) => p.rank, cell: (p) => `<span class="dim">${p.rank}</span>`, dir0: "asc" },
    ...(adv ? [flagCol] : []),
    { key: "name", label: "Player", num: false, get: (p) => p.name.toLowerCase(), cell: nameCell, dir0: "asc" },
    { key: "points", label: "Points", num: true, get: (p) => p.points, cell: (p) => `<b>${fmtPts(p.points)}</b>`, dir0: "desc" },
    { key: "p_champ", label: "Cup", title: "P(in the Powerball Cup field): automatic bid, MVP-performance qualifier, or a DGPT/Major event win (special invite — 100% if already won)", num: true, get: (p) => p.p_champ, cell: (p) => `<b class="${probClass(p.p_champ)}">${fmtPct(p.p_champ)}</b>`, dir0: "desc" },
    ...(hasCupWin ? [{ key: "p_cup_win", label: "Win Cup", hide: "t1", title: `P(wins the Powerball Cup): the four-round championship played out from each seed's starting strokes, over every season where they reach it. Unlike Cup, this one is a race — the whole field's odds add to 100%`, num: true, get: (p) => p.p_cup_win ?? 0, cell: (p) => `<b class="${probClass(p.p_cup_win ?? 0)}">${fmtPct(p.p_cup_win ?? 0)}</b>`, dir0: "desc" }] : []),
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
    `<tr class="expandable" data-pdga="${p.pdga}" tabindex="0" aria-expanded="false">` +
    cols.map((c) => `<td class="${[c.num ? "num" : "", c.key === "spark" || c.key === "strokes" ? "sparkcell" : "", c.key === "country" ? "flagcell" : "", c.hide || ""].join(" ").trim()}">${c.cell(p)}</td>`).join("") +
    "</tr>"
  ).join("");

  const el = $("#view-forecast");
  el.innerHTML = `
    ${moversHtml(d, state.div)}
    <div class="table-tools">
      ${colGuideHtml(cols, meta)}
      <div class="seg" id="cols-seg">
        ${[["auto", "Auto"], ["all", "All columns"], ["adv", "Advanced"]].map(([k, lbl]) =>
          `<button data-mode="${k}" class="${state.colsMode === k ? "active" : ""}">${lbl}</button>`).join("")}
      </div>
    </div>
    <div class="table-wrap${state.colsMode !== "auto" ? " cols-all" : ""}">
      <table class="table-ledger" id="forecast-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>
    </div>
    <p class="dim" style="font-size:.75rem;margin-top:6px">${rows.length} players · click a column to sort · click a row for the event breakdown and inline what-if · tap or hover a sparkline for exact odds · <b>Cup</b> = Auto Bid + MVP Bid + event-winner invites.
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
  el.querySelectorAll("tr.expandable").forEach((tr) => {
    tr.addEventListener("click", (ev) => {
      // a link, or an explanation mark, owns its own tap
      if (ev.target.closest("a") || ev.target.closest("[data-tip]")) return;
      toggleDetail(tr, d);
    });
    // the row is the site's main control and was mouse-only; Enter and Space
    // are what a keyboard reader expects of anything that expands
    tr.addEventListener("keydown", (ev) => {
      if (ev.key !== "Enter" && ev.key !== " ") return;
      if (ev.target.closest("a") || ev.target.closest("[data-tip]")) return;
      ev.preventDefault();          // Space would otherwise scroll the page
      toggleDetail(tr, d);
    });
  });
  wireSparkTips(el, meta);
}

function toggleDetail(tr, d) {
  const next = tr.nextElementSibling;
  const mark = (row, open) => row.setAttribute("aria-expanded", open ? "true" : "false");
  if (next && next.classList.contains("detail")) {
    next.remove();
    mark(tr, false);
    history.replaceState(null, "", location.pathname + location.search);
    return;
  }
  // only one row is ever open, so every other row's state is reset with it
  tr.parentElement.querySelectorAll("tr.detail").forEach((x) => x.remove());
  tr.parentElement.querySelectorAll('tr.expandable[aria-expanded="true"]')
    .forEach((x) => mark(x, false));
  mark(tr, true);
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
    // The replay carries about half a point of Monte Carlo noise run to run,
    // so a swing is reported to the whole percent and anything inside the noise
    // is reported as no move at all rather than as a spurious tenth.
    if (Math.abs(sw) < 0.01) { swingEl.textContent = ""; swingEl.className = "wf-swing"; }
    else {
      swingEl.textContent = (sw > 0 ? "▲ +" : "▼ −") + Math.round(Math.abs(sw) * 100) + "%";
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


const DOUBLES_NOTE = "No partner listed yet — projected with a field-average partner. Teams refresh automatically from registration.";
const PLAYOFF_NOTE = "Not signed up yet. Registration opens in waves off the standings, and the later ones haven't — so this field is still modelled from the qualification gate.";
const SIGNED_NOTE = "On the published signup list — in this field in every simulation, whatever the standings do.";
const PLAYIN_NOTE = "Not directly qualified for Worlds: in the field only by winning through the one-round play-in.";

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
    const win = b.place === 1 ? ` <span class="win-medal" ${tipAttrs("Event win")}>🥇</span>` : "";
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
        ? ` <span class="chip" ${tipAttrs(`Doubles team — projected with the averaged team rating (${p.dbl.team_rating})`)}>w/ ${p.dbl.partner_name}</span>`
        : ` <span class="note-flag" ${tipAttrs(`${DOUBLES_NOTE}`)}>⚑ ${p.dbl ? "partner TBD" : "teams TBD"}</span>`;
    }
    if (e.cls === "playoff") {
      const isGmc = e.tid === meta.gmc_tid;
      const signed = isGmc ? p.reg_gmc : e.tid === meta.mvp_tid ? p.reg_mvp : 0;
      const done = isGmc ? meta.gmc_field_set : meta.mvp_field_set;
      note += signed ? ` <span class="chip" ${tipAttrs(`${SIGNED_NOTE}`)}>signed up</span>`
        : done ? ""   // field is final and they are not in it: nothing to caveat
        : ` <span class="note-flag" ${tipAttrs(`${PLAYOFF_NOTE}`)}>⚑ qualification only</span>`;
    }
    // Worlds via the play-in: no schedule row of its own (it awards no
    // points), so it shows up as a qualifier on the event it feeds
    if (e.tid === meta.worlds_tid && p.p_playin > 0) {
      note += ` <span class="note-flag" ${tipAttrs(`${PLAYIN_NOTE}`)}>⚑ play-in ${fmtPct(p.p_playin)}</span>`;
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

  // Q2, the significant-figures decision: the table rounds, and this is where
  // the unrounded number lives — the row expander is already one tap away, and
  // it costs no per-cell markup across 560 rows to put it here.
  const exact = [
    ["Cup", p.p_champ],
    ...(meta.cup_rounds > 0 ? [["Win Cup", p.p_cup_win ?? 0]] : []),
    ["Auto Bid", p.p_cut],
    ["MVP Bid", p.p_mvp_qual],
  ].map(([lbl, v]) => `${lbl} <b>${fmtPctExact(v)}</b>`).join(" · ");

  return `<p class="exact-odds">Unrounded: ${exact}
    <span class="dim" ${tipAttrs("The table rounds above 10% because the model cannot resolve a tenth of a percent: the score model's spread is one pooled constant, and the cutline replay moves about half a point run to run. These are the simulation's own figures, carried to two decimals.")}>why these differ from the table</span></p>
  <div class="detail-grid">
    <div>
      <div class="band">Season so far — counts best ${meta.count_dgpt} DGPT/DGPT+, both playoffs, best ${meta.majors_counted} majors, all Jomez bonus (struck through = doesn't count)</div>
      <table class="table-ledger detail-tbl"><thead><tr><th>Event</th><th class="num">Pts (place)</th><th class="num" ${tipAttrs(`chance this finish ends up not counting by season's end`)}>Drop odds</th></tr></thead>
        <tbody>${banked || '<tr><td colspan="3" class="dim">no results yet</td></tr>'}</tbody></table>
    </div>
    <div>
      <div class="band">What-if — check the events they'll play; projected points if they do</div>
      <table class="table-ledger detail-tbl"><thead><tr>
        <th></th><th>Event</th><th class="num">Plays</th><th class="num" ${tipAttrs(`10th-percentile points — a low/floor outcome`)}>10th</th><th class="num">Med</th><th class="num">90th</th><th class="num" ${tipAttrs(`probability of winning the event`)}>Win%</th>
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

export { renderForecast, toggleDetail };
