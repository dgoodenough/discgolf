/* Shared foundation: the one `state` object, the DOM helper, every display
   formatter, and the bundle loader.

   Nothing here knows what a view is, and nothing here imports anything — that
   is the rule that keeps this module at the bottom of the graph. A formatter
   earns its place here once a second module needs it; one with a single caller
   stays next to that caller. */

const state = { div: "mpo", view: "forecast", data: {}, sort: { key: "p_champ", dir: "desc" },
                colsMode: "auto", permalink: null, moverWin: "week", moversOpen: false,
                cloudMode: "chart", lev: {}, raceAxis: "time",
                // the experimental tabs load their own bundles and keep their
                // own sort, so switching tabs never disturbs the forecast table
                proj: {}, projSort: {} };

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
/* Displayed precision.

   The extremes are exact facts about the simulation and keep their old
   handling: exactly 1.0 / 0.0 (no failures, or none at all) reads as a hard
   lock, while a value that merely rounds to an extreme stays >99.9% / <0.1%.

   The middle of the range is deliberately coarser than it was. A tenth of a
   percent is inside what this model can actually resolve — the score model's
   variance term is a single pooled constant (MODEL_IDEAS.md item 1), and the
   cutline replay carries about half a point of Monte Carlo noise run to run —
   so "41.3%" claimed a precision nothing behind it supports. Above 10% the
   display is a whole percent. Below it a tenth still earns its place, because
   there 2.1% and 2.9% differ by 40% of a longshot's season rather than by a
   rounding artifact.

   `fmtPctExact` keeps the unrounded number for the expanded row, which is
   where the exact value stays one tap away. */
const fmtPct = (x) =>
  x >= 0.999995 ? "100%"
  : x >= 0.9995 ? ">99.9%"
  : x <= 0.000005 ? "0%"
  : x < 0.0005 ? "<0.1%"
  : x < 0.0995 ? (x * 100).toFixed(1) + "%"
  : Math.round(x * 100) + "%";
const fmtPctExact = (x) => (x * 100).toFixed(2) + "%";

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

const fmtDShort = (iso) => `${+iso.slice(5, 7)}/${+iso.slice(8, 10)}`;

/* How a Cup starting score reads: negatives as strokes under, 0 as even. */
function fmtStroke(v) { return v < 0 ? `\u2212${-v}` : "E"; }

function ordinal(n) {
  const s = ["th", "st", "nd", "rd"], v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
}


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

export { $, agoText, bust, flagEmoji, fmtDShort, fmtPct, fmtPctExact, fmtPts, fmtStroke, loadDiv, ordinal, state, updatedAt };
