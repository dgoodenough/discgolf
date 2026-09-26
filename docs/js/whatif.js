/* The What-if tab: a player who does not exist, dropped into the 2027 field.

   Every other view on this site reports on people who are really there. This
   one puts a rating and a schedule into the projected 2027 season and reads
   back the same columns the 2027 tab prints for everyone else — so the answer
   lands in a shape a reader has already learned.

   The arithmetic is `dropIn()` in `sim.js`, against the summary
   `dgpt/whatif.py` ships in `meta.whatif`. This module is the controls, the
   readout, and the caveats — which are not decoration here: the numbers below
   are a projection of a projection, and the page has to say so before it says
   anything else. */

import { $, agoText, fmtPct, fmtPctExact, fmtPts, fmtStroke, ordinal, state, updatedAt } from "./core.js";
import { tipAttrs } from "./tooltip.js";
import { playerLink, probClass, sparkCell, strokesCell, wireSparkTips } from "./cells.js";
import { DROP_CLASSES, dropIn, dropInPrep } from "./sim.js";
import { loadProj } from "./projection.js";

/* Simulated seasons per slider move. 8,000 costs ~50ms on a laptop and lands
   the run-to-run spread near half a point, which is the same noise floor the
   2026 replay carries and the reason both round to the whole percent above
   10%. Ten times as many buys about a third of a point and a visible stutter,
   which is the wrong trade for a control you drag. */
const DRAWS = 8000;

/* One slider per class a reader can choose to enter, in calendar order of
   how much a start there is worth. Playoffs are deliberately absent: they are
   won, not entered, and watching them appear as you drag the others is the
   most interesting thing on the tab. */
const SLIDERS = [
  ["elite", "DGPT", "Elite Series stops"],
  ["elite_plus", "DGPT+", "the bigger Elite Series stops, worth a third more"],
  ["major", "Majors", "worth twice an Elite Series stop; only your best two count"],
  ["jomez", "JomezPro", "bonus points only, and every one of them counts"],
];

const BANNER = `Nobody here is real. The 2027 calendar is announced, so the season
  you are entering is — but the player entering it is one you invented, and the field
  is <b>this season's players at this season's ratings</b>, carried forward unchanged,
  under the assumption that 2027's points structure and playoff ladder are 2026's.
  Then the hardest part: the field does not play you back. You are added to the
  projected standings without taking a single point off anyone in them, so at the very
  top you are racing a player the model still has finishing where they were.`;

/* ---------- the state a reader is editing ---------- */

/* Somebody on the bubble, which is the one place on this table where every
   column has something to say: a rating that finishes near the automatic-bid
   cut, and the schedule the model projects for them. Anywhere higher and the
   odds are all 100%; anywhere lower and they are all zero. */
function bubbleCfg(d) {
  const cut = d.meta.cut;
  let who = d.players[0];
  for (const p of d.players) {
    if (Math.abs(p.mean_rank - cut) < Math.abs(who.mean_rank - cut)) who = p;
  }
  const clsOf = new Map(d.schedule.filter((e) => e.ei != null).map((e) => [e.ei, e.cls]));
  const cfg = { rating: who.rating, who: who.name };
  for (const cls of DROP_CLASSES) cfg[cls] = 0;
  who.att.forEach((a, ei) => {
    const cls = clsOf.get(ei);
    if (cls in cfg) cfg[cls] += a;
  });
  for (const cls of DROP_CLASSES) cfg[cls] = Math.round(cfg[cls]);
  return cfg;
}

const cfgFor = (d) => (state.whatif[state.div] ||= bubbleCfg(d));

/* Projected points to a tenth. The bundle rounds every published mean_pts the
   same way, and 8,000 seasons cannot resolve a hundredth of a point anyway —
   left raw this reads "872.42" beside a column of "925.2"s. */
const pts1 = (x) => Math.round(x * 10) / 10;

/* ---------- the controls ---------- */

/* One slider. The explanation of what a class is worth rides on the "of N"
   mark rather than in a column of its own — it is a fact about the points
   table, not about the number you picked, and it buys back the row. */
function sliderHtml(key, label, note, value, max) {
  return `<div class="wi-row">
    <label class="wi-label" for="wi-${key}">${label}
      <span class="wi-of" ${tipAttrs(note)}>of ${max}</span></label>
    <input class="wi-range" type="range" id="wi-${key}" data-key="${key}"
      min="0" max="${max}" step="1" value="${value}"
      aria-describedby="wi-${key}-out">
    <output class="wi-out" id="wi-${key}-out">${value}</output>
  </div>`;
}

/* Where a rating would sit among the players the 2027 table lists. Rebuilt on
   every drag, so it is a function rather than a string. */
function seatText(d, rating) {
  const above = d.players.filter((p) => p.rating > rating).length;
  return above === 0
    ? "the best rating in the field"
    : `${ordinal(above + 1)}-best rating of the ${d.players.length} the 2027 table lists`;
}

function controlsHtml(d, prep, cfg) {
  const m = d.meta, [lo, hi] = prep.range;
  const rating = Math.min(hi, Math.max(lo, cfg.rating));
  const ratingTip = `PDGA player rating — the model's only input for how well somebody
    plays. About ${m.rating_pts_per_stroke} rating points are worth a stroke a round in
    ${m.division}, which is why this slider moves the numbers below so hard.`;
  const rows = SLIDERS.map(([key, label, note]) =>
    sliderHtml(key, label, `${label} — ${note}`, cfg[key],
      prep.plan.find((s) => s.cls === key).list.length)).join("");
  return `<div class="pv-panel">
    <div class="pv-head"><h2>Your 2027</h2>
      <span class="pv-form">${m.division} · ${m.roster_size} rivals</span></div>
    <div class="pv-body">
      <div class="wi-controls">
        <div class="wi-row wi-rating">
          <label class="wi-label" for="wi-rating">Rating
            <span class="wi-of" ${tipAttrs(ratingTip)}>PDGA</span></label>
          <input class="wi-range" type="range" id="wi-rating" data-key="rating"
            min="${lo}" max="${hi}" step="1" value="${rating}" aria-describedby="wi-rating-out">
          <output class="wi-out" id="wi-rating-out">${rating}</output>
          <span class="wi-hint" id="wi-seat">${seatText(d, rating)}</span>
        </div>
      </div>
      <div class="wi-controls wi-2up">${rows}</div>
      <p class="pv-note"><b>Which</b> events is the model's to pick — a fresh random draw
        of that many from each class every simulated season. The two playoff events are
        not on the list: you qualify for those on points or you do not, which is part of
        what is being computed below, from
        ${DRAWS.toLocaleString()} simulated seasons each time you move a slider.</p>
      <p class="pv-note"><button class="btn" id="wi-reset">reset to the bubble</button>
        <span class="wi-hint">the starting position is ${cfg.who}'s projected 2027 —
          the season the model has finishing nearest the automatic-bid cut, which is the
          one place every column below has something to say</span></p>
    </div>
  </div>`;
}

/* ---------- the readout ---------- */

/* Every column the 2027 table carries, with the copy it carries them under —
   a reader who has learned that table should not have to learn a second set
   of names here. Odds first, because they are the question. */
function tiles(m) {
  const pct = (v) => `<b class="${probClass(v)}">${fmtPct(v)}</b>`;
  return [
    ["p_champ", "Cup", `P(in the ${m.championship} field): an automatic bid, a ${m.playoff2} qualifier, or an event win`, pct, true],
    ["p_cup_win", "Win Cup", `P(wins the ${m.championship}): the ${m.cup_rounds}-round championship played out from your seed's starting strokes. A race — the whole field's odds add to 100%`, pct, true],
    ["p_cut", "Auto Bid", `P(finish top ${m.cut} in the 2027 standings — an automatic Cup berth)`, pct, true],
    ["p_perf", "KCWO Bid", `P(you take one of the ${m.perf_spots} Cup places won at the ${m.playoff2}, from outside the standings cut)`, pct, false],
    ["p_play1", m.playoff1, `P(you make the ${m.playoff1} field — top ${m.play1_cut} on points, expanding to ${m.play1_fill} if it doesn't fill)`, pct, false],
    ["p_play2", "KCWO", `P(you make the ${m.playoff2} field — top ${m.play2_cut} on points, plus the top ${m.play2_perf} ${m.playoff1} finishers from outside it)`, pct, false],
    ["p_first", "Wins it", "P(you finish first in the 2027 World Standings)", pct, false],
    ["mean_pts", "Proj. pts", "The points total you finish on, averaged over every simulated season", (v) => `<b>${fmtPts(pts1(v))}</b>`, false],
    ["mean_rank", "Proj. rank", "Your average finishing position in the projected standings", (v) => `<b>${v.toFixed(1)}</b>`, false],
    ["exp_starts", "Proj. starts", "Events you play, the two playoff events included when you earn them", (v) => `<b>${v.toFixed(1)}</b>`, false],
  ];
}

function tilesHtml(m, r) {
  return `<div class="wi-tiles">${tiles(m).map(([key, label, note, fmt, big]) =>
    `<div class="wi-tile${big ? " wi-big" : ""}" data-key="${key}">
      <span class="wi-tile-v">${fmt(r[key])}</span>
      <span class="wi-tile-k" ${tipAttrs(note)}>${label}</span>
    </div>`).join("")}</div>`;
}

/* The two distributions, drawn by the same code that draws them in every
   table row — a synthetic player with no PDGA number, which is the one thing
   about this reader that is definitely true. */
function distHtml(m, r) {
  const me = { pdga: 0, hist: r.hist, strokes: r.strokes };
  return `<div class="wi-dists">
    <div>
      <p class="band">Where you finish</p>
      ${sparkCell(me, m)}
      <p class="hint">One bar per position across all ${r.draws.toLocaleString()} seasons,
        green inside the top ${m.cut}, a last bar for ${m.max_hist_rank}th or worse.
        Tap or hover for the exact odds.</p>
    </div>
    <div>
      <p class="band">What you'd start the Cup on</p>
      ${strokesCell(me, m)}
      <p class="hint">Your finishing position run through the seed ladder
        (${fmtStroke(m.start_strokes.values[0])} for the No. 1 seed, E for the bottom
        seeds), with a final bar for the seasons you never reach the field.</p>
    </div>
  </div>`;
}

/* Who you would have landed between. The table above is abstract until it has
   a name in it, and this is the cheapest way to put one there. */
function neighboursHtml(d, r) {
  const near = [...d.players].sort((a, b) => b.mean_pts - a.mean_pts);
  let at = near.findIndex((p) => p.mean_pts < r.mean_pts);
  if (at < 0) at = near.length;
  const rows = [];
  // three above, you, three below: `at` is the first player you outscore, so
  // the same index runs both halves and only the marker moves
  for (let i = at - 3; i < at + 3; i++) {
    if (i === at) {
      rows.push(`<tr class="wi-you"><td>you</td><td class="num"><b>${fmtPts(pts1(r.mean_pts))}</b></td>
        <td class="num"><b class="${probClass(r.p_champ)}">${fmtPct(r.p_champ)}</b></td></tr>`);
    }
    const p = near[i];
    if (!p) continue;
    rows.push(`<tr><td>${playerLink(p)} <span class="dim">${p.rating}</span></td>
      <td class="num">${fmtPts(p.mean_pts)}</td>
      <td class="num"><span class="${probClass(p.p_champ)}">${fmtPct(p.p_champ)}</span></td></tr>`);
  }
  return `<div>
    <p class="band">Who you'd have landed between</p>
    <table class="table-ledger detail-tbl"><thead><tr>
      <th>Player</th><th class="num">Proj. pts</th><th class="num">Cup</th>
    </tr></thead><tbody>${rows.join("")}</tbody></table>
    <p class="hint">Ordered by projected points, so this is where the model would file
      you — remembering that it has not taken those points off anybody.</p>
  </div>`;
}

const NOTES = [
  `<b>The field is frozen.</b> You are added to the 2027 standings, not swapped into
   them: every point you win here is a point nobody else loses. That flatters you
   most where the race is tightest, and at the very top it is stranger than
   flattering — ask for the best rating in the field and you are racing somebody the
   model still has winning the season.`,
  `<b>Which events is a coin, not a plan.</b> Each simulated season draws a fresh
   random subset of the size you asked for. A player who only enters the events that
   suit them would do better than this; one who enters the four toughest would do
   worse.`,
  `<b>The doubles championship is not on the sliders.</b> It needs a partner, and 2027
   pairings cannot be known, so it is left out of your season entirely rather than
   guessed at.`,
  `<b>The Cup's opponents are averages.</b> Win Cup plays the championship out against
   the mean rating at each seed rather than against a distribution over who holds it,
   which narrows the spread of fields you could meet there.`,
  `<b>Everything the 2027 tab assumes, this tab assumes too</b> — the ratings, the
   attendance model behind the field sizes, the 2026 points curves and counting caps,
   and a roster with no 2027 rookie in it.`,
];

const notesHtml = () => `<details class="expl">
  <summary>What this what-if assumes</summary>
  <div class="expl-body">
    <p class="expl-lede">On top of everything the 2027 projection already assumes —
      that list is on the 2027 tab, and the last item below is a reminder that it still
      applies — dropping a player into that field costs the approximations here. None of
      them is hidden in the code.</p>
    <ol class="expl-list">${NOTES.map((n) => `<li>${n}</li>`).join("")}</ol>
  </div>
</details>`;

/* ---------- the view ---------- */

let prep = null;      // per-bundle, rebuilt when the division changes
let prepKey = null;
let pending = 0;      // rAF handle: a drag coalesces into one run per frame

function recompute(d, cfg) {
  const m = d.meta;
  const r = dropIn(prep, cfg, DRAWS);
  $("#wi-results").innerHTML =
    tilesHtml(m, r) +
    `<div class="wi-split">${distHtml(m, r)}${neighboursHtml(d, r)}</div>
     <p class="exact-odds">Unrounded: ${[["Cup", r.p_champ], ["Win Cup", r.p_cup_win],
       ["Auto Bid", r.p_cut], ["KCWO Bid", r.p_perf]]
       .map(([k, v]) => `${k} <b>${fmtPctExact(v)}</b>`).join(" · ")}
       <span class="dim" ${tipAttrs(`Rounded above 10% because the model cannot resolve a tenth of a percent here: this runs ${DRAWS.toLocaleString()} seasons per move and carries about half a point of noise between runs.`)}>why these differ from the tiles</span></p>`;
  wireSparkTips($("#wi-results"), m);
}

function wire(el, d, cfg) {
  const queue = () => {
    if (pending) return;
    pending = requestAnimationFrame(() => { pending = 0; recompute(d, cfg); });
  };
  el.querySelectorAll(".wi-range").forEach((input) =>
    input.addEventListener("input", () => {
      cfg[input.dataset.key] = +input.value;
      el.querySelector(`#wi-${input.dataset.key}-out`).textContent = input.value;
      if (input.dataset.key === "rating") $("#wi-seat").textContent = seatText(d, cfg.rating);
      queue();
    }));
  el.querySelector("#wi-reset").addEventListener("click", () => {
    state.whatif[state.div] = bubbleCfg(d);
    renderWhatIf();
  });
}

async function renderWhatIf() {
  // A queued frame closes over the bundle and config it was queued with, and
  // this call is about to replace the element it would write into — which is
  // how a fast division switch used to land MPO's odds in the FPO panel.
  if (pending) { cancelAnimationFrame(pending); pending = 0; }
  const el = $("#view-whatif");
  const d = await loadProj("2027", state.div);
  $("#kicker").textContent = "2027 DGPT Season Projection";
  $("#headline").textContent = "What if you were in the 2027 field?";
  $("#how-link").href = "how-it-works.html#experimental";
  $("#live-note").hidden = true;
  $("#sub-lede").textContent = "Your odds, if you joined the projected 2027 season";

  if (d && (prepKey !== state.div || !prep)) {
    prep = dropInPrep(d);
    prepKey = state.div;
  }
  if (!d || !prep) {
    $("#stakes-line").innerHTML = `<b class="al">Not published yet.</b>`;
    $("#meta-line").textContent = "no bundle available";
    el.innerHTML = `<p class="pv-intro">This what-if has not been generated yet. It rides
      along with the 2027 projection on the daily refresh, and a bundle published before
      it existed carries no field to drop into; check back after the next one.</p>`;
    return;
  }

  const m = d.meta;
  const cfg = cfgFor(d);
  {
    const f = updatedAt(m);
    const mins = Math.max(0, Math.round((Date.now() - f.getTime()) / 60000));
    $("#meta-line").innerHTML = `field projected ${agoText(mins)} · ` +
      `${DRAWS.toLocaleString()} seasons per move · top ${m.cut} qualify directly, ` +
      `field of ${m.field_size}`;
  }
  $("#stakes-line").innerHTML = `<span class="exp-chip">Highly experimental</span> ` +
    `one invented ${m.division} player against ${m.roster_size} real ones`;

  el.innerHTML = `
    <div class="exp-banner">
      <p class="exp-title">A player who doesn't exist · no results · no registrations · the field doesn't play you back</p>
      <p class="exp-text">${BANNER}</p>
    </div>
    ${notesHtml()}
    ${controlsHtml(d, prep, cfg)}
    <div id="wi-results"></div>`;

  wire(el, d, cfg);
  recompute(d, cfg);
}

export { renderWhatIf };
