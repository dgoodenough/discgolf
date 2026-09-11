/* The two experimental tabs — the 2027 DGPT season and the EuroTour.

   Kept apart from the forecast tab for the same reason `dgpt/project.py` is
   kept apart from `dgpt/simulate.py`: these project a season nobody has
   played, they read their own bundles, and they write their own page header.
   `PROJ_VIEWS` is the table the shell routes on. */

import { $, agoText, bust, flagEmoji, fmtDShort, fmtPct, fmtPctExact, fmtPts, fmtStroke, state, updatedAt } from "./core.js";
import { tipAttrs } from "./tooltip.js";
import { playerLink, probClass, sparkCell, strokesCell, wireSparkTips } from "./cells.js";

/* ==========================================================================
   THE EXPERIMENTAL TABS — two seasons nobody has played yet

   Everything above this line is reporting: real results, real registrations,
   published rules, and a simulation of what is left of them. These two tabs
   are not that, and the page has to say so before it says anything else. The
   2027 forecast is built from a press release; the EuroTour one is built from
   a press release that does not include a points table.

   So the design rule here is different from the rest of the site: the
   uncertainty is the headline, not a footnote. Each tab opens with a banner
   naming what is missing, and an assumptions panel — served straight from the
   model's own `meta.notes`, so the page cannot drift from what the code
   actually did — sits above the numbers rather than below them.

   Below that banner the tabs are deliberately ordinary. Same table, same
   colour meanings, same sparklines, same sorting, so a reader who has learned
   the forecast tab does not have to learn anything twice.
   ========================================================================== */

const PROJ_VIEWS = {
  next: {
    view: "next", tour: "2027", sel: "#view-next",
    kicker: "2027 DGPT Season Projection",
    h1: "Who makes the 2027 Powerball Cup?",
    lede: "A projection of the entire announced 2027 season",
    warn: "Highly experimental",
    caveat: "No results · no registrations · no published 2027 points table",
    banner: `Nothing here has been played. The 2027 schedule is announced, so the
      calendar is real — but every result, field and points total below is
      simulated from <b>this season's ratings</b> and <b>this season's attendance</b>,
      under the assumption that the 2027 points structure, playoff ladder and Cup
      format are 2026's. No 2027 registration exists. No 2027 points table has been
      published. Read it as a shape, not as odds.`,
  },
  euro: {
    view: "euro", tour: "et", sel: "#view-euro",
    kicker: "2027 DGPT EuroTour Projection",
    h1: "Who earns a 2028 Tour Card?",
    lede: "A projection of the announced 2027 EuroTour standings",
    warn: "Even more experimental",
    caveat: "No results · a partial field · displacement not modelled",
    banner: `Everything the 2027 tab warns about, plus one of its own. The points
      structure here <b>is</b> the published one — three categories, 250 / 200 / 150
      for a win, your best six results — and so are the 2028 card bands. What is
      missing is the field: this table holds only European players who appear in the
      2026 DGPT World Standings, so a European who plays purely domestic events is
      absent from a race they would really be in. Nor is <b>displacement</b>
      modelled, which only ever passes cards further down the standings — so for a
      player just outside a band, these are a floor.`,
  },
};

/* Where "projected field" does not mean what the column header says, because
   the event is not one field of individuals. Both are real events on the 2027
   calendar and both would otherwise read as a normal-sized stop. */
const PROJ_FIELD_NOTE = {
  et_nat: "Not one field: the championship weekend is modelled as parallel single-country fields, so this is everyone entering a national or regional championship, added up across all of them",
  doubles: "Entrants, not teams. 2027 pairings cannot be known, so each entrant is drawn as a singles field and read off the doubles curve at team places — the same fallback the live model uses before pairings are published",
};

const PROJ_CLS_LABEL = {
  elite: "DGPT", elite_plus: "DGPT+", major: "Major", jomez: "JomezPro",
  doubles: "Doubles", playoff: "Playoff", championship: "Cup",
  et_a: "EuroTour A", et_champs: "Euro Champs", et_nat: "National / Regional",
};

/* A date range, sliced out of the ISO strings rather than parsed. `new Date`
   would read "2027-03-12" as UTC midnight and print it in the viewer's local
   time, which moves a March 12 event to March 11 for everybody west of
   Greenwich — the schedule is a calendar, not an instant. */
const projDates = (a, b) =>
  a.slice(0, 7) === b.slice(0, 7)
    ? `${fmtDShort(a)}–${+b.slice(8, 10)}`
    : `${fmtDShort(a)}–${fmtDShort(b)}`;

async function loadProj(tour, div) {
  state.proj[tour] = state.proj[tour] || {};
  if (state.proj[tour][div] === undefined) {
    // A missing bundle is a real state, not an error: the projections are
    // regenerated by the daily refresh only, so a `site` branch published
    // before they existed simply has no file. The tab says so.
    try {
      const resp = await fetch(bust(`data/${tour}_${div}.json`));
      state.proj[tour][div] = resp.ok ? await resp.json() : null;
    } catch { state.proj[tour][div] = null; }
  }
  return state.proj[tour][div];
}

/* ---------- the assumptions panel ---------- */

function projNotesHtml(d, view) {
  const m = d.meta;
  const pools = m.pools.map((p) =>
    `<li><b>${p.label || p.classes.map((c) => PROJ_CLS_LABEL[c] || c).join(", ")}</b> — ` +
    `${p.keep == null ? "every result counts" : `best ${p.keep} count`}</li>`).join("");
  // The EuroTour publishes what a perfect season is worth, which is the one
  // number that proves the category table above has been read correctly.
  const perfect = m.max_points
    ? `<p class="expl-note">${m.et_count} results counted, and ${fmtPts(m.max_points)}
       points for a perfect season — the DGPT's own figure, and the arithmetic check
       on the table above.</p>` : "";
  return `<details class="expl">
    <summary>What this projection assumes</summary>
    <div class="expl-body">
      <p class="expl-lede">The model's own list, emitted with the numbers — if the
        code's assumptions change, this list changes with them.</p>
      <ol class="expl-list">${m.notes.map((n) => `<li>${n}</li>`).join("")}</ol>
      <p class="expl-band">Counting rules used</p>
      <ul class="expl-list">${pools}</ul>${perfect}
      <p class="expl-band">Carried over from ${m.base_season}</p>
      <ul class="expl-list">
        <li>Player ratings, as they stand today — nobody improves, declines or turns pro
          between now and then.</li>
        <li>Attendance, as a rate: each player is projected to play the same
          <i>share</i> of US stops, European stops and JomezPro stops that they played
          in ${m.base_season}. Applied to a bigger calendar that means more starts, not
          the same ones.</li>
        <li>The score model itself — about ${m.rating_pts_per_stroke} rating points to a
          stroke per round in ${m.division}, with a ${m.round_sd}-stroke event-level
          spread — refit against every completed ${m.base_season} round.</li>
        <li>The field is ${m.base_season}'s: ${m.roster_size} players with a standings row
          and a rating. A 2027 rookie does not exist here.</li>
      </ul>
    </div>
  </details>`;
}

/* ---------- the schedule panel ---------- */

/* How many events feed one EuroTour points category — the denominator in
   "best 2 of 3", which the published table gives but the schedule row does
   not. Counted off the schedule so it cannot disagree with it. */
const categorySize = (d, cat) => d.schedule.filter((e) => e.et_cat === cat).length;

function projScheduleHtml(d) {
  const m = d.meta;
  // What a row counts toward, read from the pool that actually holds it: by
  // class on the DGPT, by published points category on the EuroTour.
  const keepBy = {};
  for (const p of m.pools) {
    for (const c of p.classes) keepBy[`c:${c}`] = p.keep;
    for (const k of (p.cats || [])) keepBy[`k:${k}`] = p.keep;
  }
  const rows = d.schedule.map((e) => {
    const keep = e.et_cat ? keepBy[`k:${e.et_cat}`] : keepBy[`c:${e.cls}`];
    const win = e.et_cat && m.categories ? m.categories[e.et_cat].win : null;
    const counts = !e.counts
      ? (e.cls === "championship" ? "no points" : "—")
      : keep == null ? "bonus — all count"
      : e.et_cat ? `Cat ${e.et_cat} · ${win} to win · best ${keep} of ${categorySize(d, e.et_cat)}`
      : `best ${keep} of class`;
    // A field bigger than the part of it with rows here is worth saying out
    // loud — it is the difference between "you beat forty people" and "you
    // beat forty of the ninety who showed up".
    const hidden = Math.round(e.field_size - (e.field_listed ?? e.field_size));
    const note = hidden > 0
      ? `Open to the whole tour: about ${Math.round(e.field_size)} entrants, of whom
         ${Math.round(e.field_listed)} have a row in this table. The rest are drawn as
         unnamed opponents who take the places they earn and no points.`
      : PROJ_FIELD_NOTE[e.cls];
    const field = e.ei == null ? ""
      : note ? `<span class="side-door" ${tipAttrs(note)}>${e.field_size.toFixed(0)} *</span>`
      : e.field_size.toFixed(0);
    return `<tr>
      <td class="ev-date">${projDates(e.start, e.end)}</td>
      <td>${e.short}${e.tour === "both" ? ` <span class="both-tag" ${tipAttrs("Counts on both the DGPT World Standings and the EuroTour")}>both tours</span>` : ""}</td>
      <td class="t3 dim">${e.loc}</td>
      <td>${PROJ_CLS_LABEL[e.cls] || e.cls}</td>
      <td class="num">${e.rounds}</td>
      <td class="t2 dim">${counts}</td>
      <td class="num dim">${field}</td>
    </tr>`;
  }).join("");
  return `<div class="pv-panel">
    <div class="pv-head"><h2>The announced schedule</h2>
      <span class="pv-form">${d.schedule.length} events · rounds from the dates</span></div>
    <div class="pv-body">
      <p class="pv-lede">Round counts are read straight off the calendar — a three-day
        stop plays three rounds, a four-day one four, Pro Worlds' five-day window five.
        That rule holds at every event on the 2026 schedule, and with no PDGA round plan
        published for 2027 the dates are the only evidence there is.</p>
      <div class="pv-scroll">
        <table class="table-ledger pv-tbl"><thead><tr>
          <th>Dates</th><th>Event</th><th class="t3">Location</th><th>Type</th>
          <th class="num">Rds</th><th class="t2">Counting</th>
          <th class="num" title="Mean number of players the model puts in this field. A starred figure includes entrants with no row in this table — hover it for the split">Proj. field</th>
        </tr></thead><tbody>${rows}</tbody></table>
      </div>
    </div>
  </div>`;
}

/* ---------- the odds table ---------- */

function projCols(d) {
  const m = d.meta;
  const cup = !!m.championship;
  const flagCol = { key: "country", label: "Nat.", hide: "t4", num: false, get: (p) => p.country || "zz",
    cell: (p) => p.country ? `<span class="flag" title="${p.country}">${flagEmoji(p.country)}</span>` : "", dir0: "asc" };
  const common = [
    { key: "rank26", label: `'${String(m.base_season).slice(2)}`, title: `Their position in the ${m.base_season} World Standings as it stands today. Context only — this projection starts everyone on zero, and nothing in it is carried over except their rating and how often they play`, num: true, get: (p) => p.rank26, cell: (p) => `<span class="dim">${p.rank26}</span>`, dir0: "asc" },
    flagCol,
    { key: "name", label: "Player", num: false, get: (p) => p.name.toLowerCase(), cell: playerLink, dir0: "asc" },
    { key: "rating", label: "Rating", hide: "t4", num: true, title: "Current PDGA rating — the model's only input for how well someone plays, and it is assumed not to move before 2027", get: (p) => p.rating || 0, cell: (p) => `<span class="dim">${p.rating || ""}</span>`, dir0: "desc" },
    { key: "mean_pts", label: "Proj. pts", title: "Points the model expects them to finish 2027 on, averaged over every simulated season", num: true, get: (p) => p.mean_pts, cell: (p) => `<b>${fmtPts(p.mean_pts)}</b>`, dir0: "desc" },
  ];
  // The finishing sparkline is 120px wide, and so is the starting-strokes one.
  // Both at once is 240px of table, which is what tips a tablet into a
  // sideways scroll — so on the tab that has both, the finish distribution is
  // the one that drops first (the strokes bars already carry the finish, run
  // through the seed ladder, plus the seasons they miss the field entirely).
  const sparkCol = (tier) => ({ key: "spark", label: "Finish distribution", hide: tier, num: false, sortable: false,
    title: `Where they finish the 2027 standings across all ${m.n_sims.toLocaleString()} simulated seasons — one bar per position, green inside the top ${m.cut}, a last bar for ${m.max_hist_rank}th or worse`,
    cell: (p) => sparkCell(p, m) });
  const tail = [
    { key: "exp_starts", label: "Proj. starts", hide: "t4", title: "Events they are projected to play, summed over the season", num: true, get: (p) => p.exp_starts, cell: (p) => `<span class="dim">${p.exp_starts.toFixed(1)}</span>`, dir0: "desc" },
    { key: "mean_rank", label: "Proj. rank", hide: "t4", num: true, title: "Their average finishing position in the projected standings", get: (p) => p.mean_rank, cell: (p) => `<span class="dim">${p.mean_rank.toFixed(1)}</span>`, dir0: "asc" },
  ];
  if (!cup) {
    return [
      ...common,
      { key: "p_any", label: "A card", title: `P(finishes top ${m.card_through} — a 2028 card of either kind. Displacement, which is not modelled, only ever passes cards further down, so this is a floor)`, num: true, get: (p) => p.p_any, cell: (p) => `<b class="${probClass(p.p_any)}">${fmtPct(p.p_any)}</b>`, dir0: "desc" },
      { key: "p_full", label: "Full Tour Card", hide: "t1", title: `P(finishes top ${m.full_cards} — a 2028 Full Tour Card: every DGPT event in the US and Europe)`, num: true, get: (p) => p.p_full, cell: (p) => `<span class="${probClass(p.p_full)}">${fmtPct(p.p_full)}</span>`, dir0: "desc" },
      { key: "p_card", label: "EuroTour Card", hide: "t2", title: `P(finishes ${m.full_cards + 1}th–${m.card_through}th — a 2028 EuroTour Card: every DGPT and EuroTour event in Europe, plus up to three US Elite Series stops)`, num: true, get: (p) => p.p_card, cell: (p) => `<span class="${probClass(p.p_card)}">${fmtPct(p.p_card)}</span>`, dir0: "desc" },
      { key: "p_first", label: "Wins it", hide: "t3", title: "P(finishes first in the EuroTour standings)", num: true, get: (p) => p.p_first, cell: (p) => `<span class="${probClass(p.p_first)}">${fmtPct(p.p_first)}</span>`, dir0: "desc" },
      sparkCol("t1"),
      ...tail,
    ];
  }
  return [
    ...common,
    { key: "p_champ", label: "Cup", title: `P(in the ${m.championship} field): automatic bid, a ${m.playoff2} qualifier, or an event win`, num: true, get: (p) => p.p_champ, cell: (p) => `<b class="${probClass(p.p_champ)}">${fmtPct(p.p_champ)}</b>`, dir0: "desc" },
    { key: "p_cup_win", label: "Win Cup", hide: "t1", title: `P(wins the ${m.championship}): the ${m.cup_rounds}-round championship played out from each seed's starting strokes. A race — the field's odds add to 100%`, num: true, get: (p) => p.p_cup_win, cell: (p) => `<b class="${probClass(p.p_cup_win)}">${fmtPct(p.p_cup_win)}</b>`, dir0: "desc" },
    { key: "strokes", label: "Starting strokes", hide: "t1", num: false, sortable: false,
      title: `Distribution of the score they would start the Cup on: their projected finish run through the 2026 seed ladder (${fmtStroke(m.start_strokes.values[0])} for the No. 1 seed, E for the bottom seeds), with a final bar for the seasons they miss the field`,
      cell: (p) => strokesCell(p, m) },
    sparkCol("t3"),
    { key: "p_cut", label: "Auto Bid", hide: "t2", title: `P(finish top ${m.cut} in the 2027 standings — automatic Cup berth)`, num: true, get: (p) => p.p_cut, cell: (p) => `<span class="${probClass(p.p_cut)}">${fmtPct(p.p_cut)}</span>`, dir0: "desc" },
    { key: "p_perf", label: "KCWO Bid", hide: "t2", title: `P(earns a Cup spot with a top-${m.perf_spots} finish at the ${m.playoff2}, from outside the standings cut)`, num: true, get: (p) => p.p_perf, cell: (p) => `<span class="${probClass(p.p_perf)}">${fmtPct(p.p_perf)}</span>`, dir0: "desc" },
    { key: "p_play1", label: m.playoff1, hide: "t3", title: `P(makes the ${m.playoff1} field — top ${m.play1_cut} on points, expanding to ${m.play1_fill} if it doesn't fill)`, num: true, get: (p) => p.p_play1, cell: (p) => `<span class="${probClass(p.p_play1)}">${fmtPct(p.p_play1)}</span>`, dir0: "desc" },
    { key: "p_play2", label: "KCWO", hide: "t3", title: `P(makes the ${m.playoff2} field — top ${m.play2_cut} on points, plus the top ${m.play2_perf} ${m.playoff1} finishers from outside it)`, num: true, get: (p) => p.p_play2, cell: (p) => `<span class="${probClass(p.p_play2)}">${fmtPct(p.p_play2)}</span>`, dir0: "desc" },
    ...tail,
  ];
}

/* Expanded row: the season this player is projected to play. The forecast
   tab's expander answers "what have they banked and what if they skip X";
   here there is nothing banked and no what-if worth offering, so the useful
   question is the one attendance actually decides — which events is the model
   sending them to, and what is each worth. */
function projDetailHtml(p, d) {
  const byEi = {};
  for (const e of d.schedule) if (e.ei != null) byEi[e.ei] = e;
  const rows = p.att.map((a, i) => {
    const e = byEi[i];
    if (!e) return "";
    return `<tr class="${a < 0.5 ? "not-att" : ""}">
      <td class="ev-date">${projDates(e.start, e.end)}</td>
      <td>${e.short}</td>
      <td>${PROJ_CLS_LABEL[e.cls] || e.cls}</td>
      <td class="num ${probClass(a)}">${fmtPct(a)}</td>
    </tr>`;
  }).join("");
  const extra = d.meta.championship
    ? `<div><p class="band">Season shape</p>
        <p class="hint">Cup field ${fmtPctExact(p.p_champ)} · wins the Cup
          ${fmtPctExact(p.p_cup_win)} · automatic bid ${fmtPctExact(p.p_cut)} ·
          first in the standings ${fmtPctExact(p.p_first)}</p></div>`
    : `<div><p class="band">Season shape</p>
        <p class="hint">Full Tour Card ${fmtPctExact(p.p_full)} · EuroTour Card
          ${fmtPctExact(p.p_card)} · either ${fmtPctExact(p.p_any)} · wins the
          EuroTour ${fmtPctExact(p.p_first)}</p></div>`;
  return `<div class="detail-grid">
    <div>
      <p class="band">Projected attendance</p>
      <table class="table-ledger detail-tbl"><thead><tr>
        <th>Dates</th><th>Event</th><th>Type</th><th class="num">Plays</th>
      </tr></thead><tbody>${rows}</tbody></table>
      <p class="hint">${p.exp_starts.toFixed(1)} starts projected, from their
        ${d.meta.base_season} rate in each part of the calendar.</p>
    </div>
    ${extra}
  </div>`;
}

function projToggleDetail(tr, d) {
  const next = tr.nextElementSibling;
  const mark = (row, open) => row.setAttribute("aria-expanded", open ? "true" : "false");
  if (next && next.classList.contains("detail")) { next.remove(); mark(tr, false); return; }
  tr.parentElement.querySelectorAll("tr.detail").forEach((x) => x.remove());
  tr.parentElement.querySelectorAll('tr.expandable[aria-expanded="true"]').forEach((x) => mark(x, false));
  mark(tr, true);
  const p = d.players.find((x) => x.pdga === +tr.dataset.pdga);
  const detail = document.createElement("tr");
  detail.className = "detail";
  const td = document.createElement("td");
  td.colSpan = tr.children.length;
  td.innerHTML = projDetailHtml(p, d);
  detail.appendChild(td);
  tr.after(detail);
}

/* ---------- the view ---------- */

async function renderProjection(view) {
  const el = $(view.sel);
  const d = await loadProj(view.tour, state.div);
  $("#kicker").textContent = view.kicker;
  $("#headline").textContent = view.h1;
  $("#how-link").href = "how-it-works.html#experimental";
  $("#live-note").hidden = true;

  if (!d) {
    $("#stakes-line").innerHTML = `<b class="al">Not published yet.</b>`;
    $("#sub-lede").textContent = view.lede;
    $("#meta-line").textContent = "no bundle available";
    el.innerHTML = `<p class="pv-intro">This projection has not been generated yet.
      It is rebuilt by the daily refresh; check back after the next one.</p>`;
    return;
  }

  const m = d.meta;
  $("#sub-lede").textContent = view.lede;
  {
    const f = updatedAt(m);
    const mins = Math.max(0, Math.round((Date.now() - f.getTime()) / 60000));
    $("#meta-line").innerHTML =
      `projected ${agoText(mins)} · ${m.n_sims.toLocaleString()} sims · ` +
      (m.championship
        ? `top ${m.cut} qualify directly, field of ${m.field_size}`
        : `top ${m.full_cards} take a Full Tour Card, top ${m.card_through} a EuroTour Card`);
  }
  $("#stakes-line").innerHTML =
    `<span class="exp-chip">${view.warn}</span> ` +
    `${m.roster_size} ${state.div.toUpperCase()} players simulated through ` +
    `${d.schedule.length} announced events`;

  const cols = projCols(d);
  const sort = state.projSort[view.tour] ||
    (state.projSort[view.tour] = { key: cols[5].key, dir: "desc" });
  const col = cols.find((c) => c.key === sort.key) || cols[0];
  const rows = [...d.players];
  rows.sort((a, b) => {
    const av = col.get(a), bv = col.get(b);
    const cmp = av < bv ? -1 : av > bv ? 1 : 0;
    return (sort.dir === "asc" ? cmp : -cmp) || a.rank26 - b.rank26;
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
    "</tr>").join("");

  el.innerHTML = `
    <div class="exp-banner">
      <p class="exp-title">${view.caveat}</p>
      <p class="exp-text">${view.banner}</p>
    </div>
    ${projNotesHtml(d, view)}
    ${projScheduleHtml(d)}
    <div class="table-wrap">
      <table class="table-ledger" id="proj-${view.view}-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>
    </div>
    <p class="dim" style="font-size:.75rem;margin-top:6px">${rows.length} of ${m.roster_size}
      players shown · click a column to sort · click a row for the season the model
      projects them to play · tap or hover a sparkline for exact odds.</p>`;

  el.querySelectorAll("th.sortable").forEach((th) =>
    th.addEventListener("click", () => {
      const key = th.dataset.key;
      const c = cols.find((x) => x.key === key);
      state.projSort[view.tour] = { key, dir: sort.key === key ? (sort.dir === "asc" ? "desc" : "asc") : c.dir0 };
      renderProjection(view);
    })
  );
  el.querySelectorAll("tr.expandable").forEach((tr) => {
    tr.addEventListener("click", (ev) => {
      if (ev.target.closest("a") || ev.target.closest("[data-tip]")) return;
      projToggleDetail(tr, d);
    });
    tr.addEventListener("keydown", (ev) => {
      if (ev.key !== "Enter" && ev.key !== " ") return;
      if (ev.target.closest("a") || ev.target.closest("[data-tip]")) return;
      ev.preventDefault();
      projToggleDetail(tr, d);
    });
  });
  wireSparkTips(el, m);
}

export { PROJ_VIEWS, renderProjection };
