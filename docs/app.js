/* DGPT Standings Forecast — the shell.

   Five tabs over two rendering paths: three views of the live 2026 bundle and
   two of the projection bundles, with exactly one section visible at a time.
   This module owns the page header, the freshness flag and the tab wiring; the
   views themselves live in js/.

   Loaded as `<script type="module">`, so the imports below are the whole
   dependency graph and nothing here is global. */

import { $, agoText, loadDiv, state, updatedAt } from "./js/core.js";
import { tipAttrs } from "./js/tooltip.js";
import { liveEvents, shortName } from "./js/cells.js";
import { renderForecast, toggleDetail } from "./js/forecast.js";
import { renderPossible } from "./js/possible.js";
import { renderRace } from "./js/race.js";
import { PROJ_VIEWS, renderProjection } from "./js/projection.js";

/* ---------- shell ---------- */

/* Every top-level view and the section it owns, in one list: five tabs across
   two rendering paths, and exactly one of them visible at a time. */
const VIEW_SECTIONS = [
  ["forecast", "#view-forecast"], ["possible", "#view-possible"],
  ["race", "#view-race"], ["next", "#view-next"], ["euro", "#view-euro"],
];

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
  // The projection tabs read their own bundle and write their own header, so
  // they branch before loadDiv: the 2026 forecast's stakes line, live note and
  // freshness flag are all statements about a season in progress, and none of
  // them mean anything on a page about 2027.
  const proj = PROJ_VIEWS[state.view];
  if (proj && !state.permalink) {
    for (const [key, sel] of VIEW_SECTIONS) $(sel).hidden = key !== proj.view;
    await renderProjection(proj);
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
  // one is built, and a deep link always lands on the table it points into
  const view = state.permalink ? "forecast" : state.view;
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
