/* DGPT Standings Forecast — the shell.

   Eight views over five rendering paths: three views of the live 2026 bundle,
   two tables off the projection bundles, the what-if, which reads the 2027
   bundle but renders one invented player rather than the field, and the tour
   planner and its amateur sibling, which read their own bundles. Exactly one
   section is visible at a time. This module owns the page header, the
   freshness flag and the navigation; the views themselves live in js/.

   Navigation is two levels — three groups across the top, and the views of the
   current group as tabs — and all of it is plain links. The URL hash is the
   single source of truth for which view and division are showing (the routes
   are in core.js): a click changes the hash, `hashchange` re-renders, and so
   Back, reload, bookmarks and "open in new tab" all work without any code of
   their own.

   Loaded as `<script type="module">`, so the imports below are the whole
   dependency graph and nothing here is global. */

import { $, agoText, loadDiv, readRoute, routeHash, state, updatedAt } from "./js/core.js";
import { tipAttrs } from "./js/tooltip.js";
import { liveEvents, shortName } from "./js/cells.js";
import { renderForecast, toggleDetail } from "./js/forecast.js";
import { renderPossible } from "./js/possible.js";
import { renderRace } from "./js/race.js";
import { PROJ_VIEWS, renderProjection } from "./js/projection.js";
import { renderWhatIf } from "./js/whatif.js";
import { renderTourPlan } from "./js/tourplan.js";
import { renderAmPlan } from "./js/amplan.js";

/* ---------- shell ---------- */

/* Every view and the section it owns, in one list; exactly one is visible
   at a time. */
const VIEW_SECTIONS = [
  ["forecast", "#view-forecast"], ["possible", "#view-possible"],
  ["race", "#view-race"], ["next", "#view-next"], ["whatif", "#view-whatif"],
  ["euro", "#view-euro"], ["tour", "#view-tour"], ["am", "#view-am"],
];

/* The groups of the top-level nav, each listing its views in tab order. A
   group's link goes to its first view. */
const GROUPS = {
  season: ["forecast", "possible", "race"],
  next: ["next", "whatif", "euro"],
  plan: ["tour", "am"],
};
const SITE_TITLE = document.title;

/* Point every nav link at the right route for the current division, mark the
   current group / view / division, and show only the current group's tabs. */
function syncNav() {
  const mark = (a, on) => {
    a.classList.toggle("active", on);
    if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  };
  const group = Object.keys(GROUPS).find((g) => GROUPS[g].includes(state.view));
  document.querySelectorAll("#site-nav a[data-group]").forEach((a) => {
    a.href = routeHash(GROUPS[a.dataset.group][0], state.div);
    mark(a, a.dataset.group === group);
  });
  document.querySelectorAll("#view-seg a").forEach((a) => {
    const on = a.dataset.view === state.view;
    a.href = routeHash(a.dataset.view, state.div);
    a.hidden = !GROUPS[group].includes(a.dataset.view);
    mark(a, on);
    if (on) document.title = state.view === "forecast"
      ? SITE_TITLE : `${a.textContent} · ${SITE_TITLE}`;
  });
  document.querySelectorAll("#division-seg a").forEach((a) => {
    a.href = routeHash(state.view, a.dataset.div);
    mark(a, a.dataset.div === state.div);
  });
  // the amateur planner has no divisions; every other view is per division
  $("#division-seg").hidden = state.view === "am";
}

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
  syncNav();
  // The projection tabs read their own bundle and write their own header, so
  // they branch before loadDiv: the 2026 forecast's stakes line, live note and
  // freshness flag are all statements about a season in progress, and none of
  // them mean anything on a page about 2027.
  const proj = PROJ_VIEWS[state.view];
  if (proj) {
    for (const [key, sel] of VIEW_SECTIONS) $(sel).hidden = key !== proj.view;
    await renderProjection(proj);
    return;
  }
  // The what-if reads the same 2027 bundle and writes its own header for the
  // same reason, but it is not a table of the field — it is one invented
  // player against it — so it renders itself rather than going through
  // PROJ_VIEWS' shared table path.
  if (state.view === "whatif") {
    for (const [key, sel] of VIEW_SECTIONS) $(sel).hidden = key !== "whatif";
    await renderWhatIf();
    return;
  }
  // The tour planner is not a forecast at all — a shortest-path search over
  // the 2027 calendar and a road-time matrix — so it owns its header too.
  if (state.view === "tour") {
    for (const [key, sel] of VIEW_SECTIONS) $(sel).hidden = key !== "tour";
    await renderTourPlan(state.div);
    return;
  }
  // The amateur planner (alpha) is the same search over every sanctioned
  // amateur event, from one home — its own bundle and header again.
  if (state.view === "am") {
    for (const [key, sel] of VIEW_SECTIONS) $(sel).hidden = key !== "am";
    await renderAmPlan();
    return;
  }
  const d = await loadDiv(state.div);
  {
    const f = freshness(d);
    const abs = f.at.toISOString().replace("T", " ").replace(":00.000Z", "Z");
    const local = f.at.toLocaleString();
    $("#meta-line").innerHTML =
      `updated <span id="updated-at" class="${f.stale ? "stale" : ""}" ` +
      `${tipAttrs(`Last successful standings update&#10;${abs} UTC&#10;${local} local`)}>` +
      `${agoText(f.mins)}</span>${
        f.stale
          ? ` <span class="stale-flag" ${tipAttrs(`${f.live
              ? "A live event normally republishes within a few minutes of any scoring change"
              : "The daily refresh normally republishes every 24 hours"}`)}>— later than expected</span>`
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
  // the projection tabs overwrite these; put them back on the way out
  $("#how-link").href = "how-it-works.html";
  $("#kicker").textContent = `${d.meta.season} DGPT Season Forecast`;
  $("#headline").textContent = "Who makes the Powerball Cup?";
  $("#sub-lede").textContent =
    `Qualification odds from a ${Math.round(d.meta.n_sims / 1000)}k-run Monte Carlo`;
  const live = liveEvents(d);
  const note = $("#live-note");
  if (live.length) {
    note.innerHTML = `<span class="live-dot"></span> LIVE now: ${live.map((e) => shortName(e.name)).join(", ")} — results feed in as they finalize`;
    note.hidden = false;
  } else {
    note.hidden = true;
  }
  // the three season-in-progress views read this one bundle; only the visible
  // one is built (a player permalink has already set the view to the forecast
  // table it points into — readRoute in core.js)
  const view = state.view;
  for (const [key, sel] of VIEW_SECTIONS) $(sel).hidden = key !== view;
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

window.addEventListener("hashchange", () => { readRoute(); render(); });

render();
