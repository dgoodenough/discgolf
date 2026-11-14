/* The Tour planner tab: where to base a 2027 driving trip, and which trip.

   Everything here is a shortest-path search over the published 2027 schedule
   and a town-to-town matrix of real road times, both shipped in
   data/tourplan.json by dgpt/tourplan.py. Nothing is simulated: every number
   on the tab is a sum of drive legs a reader could check on a map.

   The question has five knobs and one answer per home town:

     events     which slice of the calendar a player can enter — Elite Series
                plus Majors (a tour card), that plus every A-tier, or A-tiers
                only (no tour card)
     style      "home base": drive home after every event and out again to the
                next; "road trip": event to event, home only at the end
     played     how many events the trip plays
     on road    how many weekends the trip spans, first event to last. The
                difference is the trip's weekends off, spent wherever the
                calendar makes the driving cheapest; equal is back-to-back
     home       optional: one town's trips instead of the best town's

   The search lives in tourplan-search.js, which has no imports so the ranking
   can run in tourplan-worker.js; this module is the controls, the worker
   plumbing and the rendering. */

import { $, bust, dateRange, esc, hm, hrs, plural } from "./core.js";
import { tipAttrs } from "./tooltip.js";
import { DRIVE_HOURS_PER_DAY, MIN_LEN, clampQuery, rank, tripsFrom } from "./tourplan-search.js";

const SETS = [
  ["tour", "Elite + Majors", "Tour-card schedule: DGPT Elite Series stops and the PDGA Majors"],
  ["all", "Elite + Majors + A-tiers", "Everything open to a touring pro with a card"],
  ["atier", "A-tiers only", "No tour card: A-tier events only (JomezPro stops are A-tiers)"],
];
const STYLES = [
  ["hub", "Home base", "Drive home after every event, and out again to the next"],
  ["road", "Road trip", "Drive event to event; home only at the start and the end"],
];
const ALT_TRIPS = 5;
/* Show "working" only if an answer takes longer than this; most settings
   come back well inside it and a flash of status text would be noise. */
const BUSY_MS = 150;

const cfg = { set: "all", style: "hub", n: 5, weeks: 6, home: null };
let bundle;  // undefined = not fetched yet, null = fetch failed

async function loadTour() {
  if (bundle === undefined) {
    try {
      const r = await fetch(bust("data/tourplan.json"));
      bundle = r.ok ? await r.json() : null;
    } catch { bundle = null; }
  }
  return bundle;
}

const CAT = { elite: "Elite", major: "Major", a: "A" };

/* ---------- the ranking, off the main thread ---------- */

/* One worker, fed the bundle once. A query that arrives while another is
   still running terminates the worker rather than queueing behind it: a
   stale answer is worthless, and a slider drag would otherwise stack up
   seconds of them. Where workers are unavailable the ranking runs inline. */
let worker = null, busy = false, gen = 0, onRanked = null;

function startWorker() {
  try {
    worker = new Worker(new URL("./tourplan-worker.js", import.meta.url), { type: "module" });
  } catch { worker = null; return; }
  worker.onmessage = ({ data }) => {
    busy = false;
    if (data.gen === gen && onRanked) onRanked(data.res);
  };
  worker.onerror = () => { worker?.terminate(); worker = null; busy = false; };
  worker.postMessage({ bundle });
}

function requestRank(q) {
  const my = ++gen;
  if (busy && worker) { worker.terminate(); worker = null; }
  if (!worker) startWorker();
  if (!worker) {
    setTimeout(() => { if (my === gen && onRanked) onRanked(rank(bundle, q)); }, 0);
    return;
  }
  busy = true;
  worker.postMessage({ gen: my, q });
}

/* ---------- itinerary helpers ---------- */

/* One row per event: the drive to it and, in Home base style, the drive back
   (a round trip from home); in Road trip style the drive back is the final
   leg home, which gets its own row. */
function stops(trip, h, ev, M, nv, style) {
  const d = (a, b) => M[a * nv + b];
  let at = h;
  return trip.path.map((i) => {
    const e = ev[i];
    const row = { e, there: d(style === "hub" ? h : at, e.v), back: style === "hub" ? d(e.v, h) : null };
    at = e.v;
    return row;
  });
}

/* Distinct alternatives for one home: no two share a starting weekend. */
function alternatives(trips, ev) {
  const seen = new Set(), out = [];
  for (const t of trips) {
    const wk = ev[t.path[0]].wk;
    if (seen.has(wk)) continue;
    seen.add(wk);
    out.push(t);
    if (out.length >= ALT_TRIPS) break;
  }
  return out;
}

/* ---------- rendering ---------- */

function segHtml(key, opts, cur) {
  return `<div class="seg pv-seg tp-seg" data-key="${key}">${opts.map(([v, label, tip]) =>
    `<button type="button" data-v="${v}" class="${String(v) === String(cur) ? "active" : ""}"
      ${tip ? tipAttrs(tip) : ""}>${label}</button>`).join("")}</div>`;
}

function rangeHtml(id, label, unit, tip) {
  return `<div class="tp-row"><label class="tp-label" for="${id}">${label}
      <span class="wi-of" ${tipAttrs(tip)}>?</span></label>
    <input class="wi-range tp-range" type="range" id="${id}" min="${MIN_LEN}" max="${MIN_LEN}" step="1"
      value="${MIN_LEN}" aria-describedby="${id}-out">
    <output class="tp-n-out" id="${id}-out"></output><span class="tp-unit">${unit}</span></div>`;
}

function controlsHtml(b, div) {
  const homes = b.venues.map((v, i) => [i, v.name]).sort((x, y) => x[1].localeCompare(y[1]));
  return `<div class="pv-panel">
    <div class="pv-head"><h2>Plan a trip</h2><span class="pv-form">${b.events.filter((e) => e[div]).length} drivable events · ${b.venues.length} towns</span></div>
    <div class="pv-body tp-controls">
      <div class="tp-row"><span class="tp-label">Schedule</span>${segHtml("set", SETS, cfg.set)}</div>
      <div class="tp-row"><span class="tp-label">Style</span>${segHtml("style", STYLES, cfg.style)}</div>
      ${rangeHtml("tp-w", "On the road", "weekends",
        "How many weekends the trip spans, from the first event to the last. Any weekend in that span without an event is a weekend off.")}
      ${rangeHtml("tp-n", "Events", "played",
        "How many events you play. It can't exceed the weekends on the road — one event a weekend — or the most this calendar fits in them.")}
      <div class="tp-row tp-fit"><span class="tp-label"></span>
        <span id="tp-fit-note" class="tp-unit"></span>
        <button type="button" class="tp-max" id="tp-b2b" ${tipAttrs("Shrink the weekends on the road to the events played: no weekends off")}>Back-to-back</button>
        <button type="button" class="tp-max" id="tp-any" ${tipAttrs("Open the weekends on the road to the whole season, so events can be spaced any distance apart")}>Any spacing</button>
        <button type="button" class="tp-max" id="tp-fill" ${tipAttrs("Play as many events as fit in these weekends — with Any spacing, the whole season's worth")}>Fill</button></div>
      <div class="tp-row"><label class="tp-label" for="tp-home">Your home</label>
        <select id="tp-home"><option value="">Best home base (below)</option>${homes.map(([i, name]) =>
          `<option value="${i}" ${cfg.home === i ? "selected" : ""}>${esc(name)}</option>`).join("")}</select></div>
    </div></div>`;
}

/* The trip as a chain of names; a long one keeps its ends and elides the
   middle, which the itinerary below spells out in full. */
function tripCell(path, ev) {
  const arrow = " <span class=\"tp-arrow\">→</span> ";
  const names = path.map((i) => esc(ev[i].name));
  if (names.length <= 6) return names.join(arrow);
  return names.slice(0, 3).join(arrow) + arrow +
    `<span class="tp-arrow">${names.length - 5} more</span>` + arrow + names.slice(-2).join(arrow);
}

function homesHtml(b, ev, res) {
  const rows = res.homes.map(({ h, total, path }, r) => {
    const first = ev[path[0]], last = ev[path[path.length - 1]];
    const span = last.wk - first.wk + 1;
    return `<tr class="tp-home-row${cfg.home === h ? " tp-sel" : ""}" data-h="${h}" tabindex="0">
      <td class="num">${r + 1}</td>
      <td class="tp-town">${esc(b.venues[h].name)}</td>
      <td class="num"><b>${hrs(total)}</b></td>
      <td class="num t2">${hrs(total / res.n)}</td>
      <td class="tp-when">${dateRange(first.start, last.end)}<span class="tp-at">${plural(span, "weekend")}</span></td>
      <td class="tp-trip t1">${tripCell(path, ev)}</td></tr>`;
  }).join("");
  return `<div class="pv-panel">
    <div class="pv-head"><h2>Best home bases</h2><span class="pv-form">${res.n} events in ≤${res.weeks} weekends</span></div>
    <div class="pv-body">
      <p class="pv-lede">All ${res.count} towns that can make this trip, ranked by the total driving of each one's best
        trip; the top ${res.homes.length} are listed. Tap a town for its itinerary and runner-up trips${cfg.home == null ? "; the top town's is below" : ""}.</p>
      <div class="pv-scroll"><table class="table-ledger pv-tbl tp-tbl">
        <thead><tr><th class="num">#</th><th>Home base</th><th class="num">Driving</th>
          <th class="num t2">Per event</th><th>When</th><th class="t1">Trip</th></tr></thead>
        <tbody>${rows}</tbody></table></div>
    </div></div>`;
}

function itineraryHtml(b, ev, res, h) {
  const M = b.minutes, nv = b.venues.length;
  const alts = alternatives(tripsFrom(h, ev, M, nv, res.n, res.weeks, cfg.style), ev);
  const home = esc(b.venues[h].name);
  const hub = cfg.style === "hub";
  if (!alts.length) {
    return `<div class="pv-panel" id="tp-itin"><div class="pv-head"><h2>From ${home}</h2></div>
      <div class="pv-body"><p class="pv-lede">No ${res.n}-event trip within ${res.weeks} weekends fits from here.</p></div></div>`;
  }
  const blocks = alts.map((t, k) => {
    const S = stops(t, h, ev, M, nv, cfg.style);
    const lastE = S[S.length - 1].e;
    const homeLeg = hub ? null : M[lastE.v * nv + h];
    const legsMin = S.flatMap((r) => (hub ? [r.there, r.back] : [r.there])).concat(hub ? [] : [homeLeg]);
    const longest = Math.max(...legsMin);
    const first = S[0].e;
    const weeks = lastE.wk - first.wk + 1, off = weeks - S.length;
    const rows = S.map((r, x) => {
      const gap = x > 0 ? r.e.wk - S[x - 1].e.wk - 1 : 0;
      return (gap > 0 ? `<tr class="tp-offrow"><td></td><td>${plural(gap, "weekend")} off</td><td></td></tr>` : "") +
        `<tr><td class="tp-when">${dateRange(r.e.start, r.e.end)}</td>
        <td><span class="tp-cat tp-${r.e.cat}">${CAT[r.e.cat]}</span> ${esc(r.e.name)}
          <span class="tp-at">${esc(b.venues[r.e.v].name)}</span></td>
        <td class="num tp-drive">${hub ? (r.there + r.back ? hm(r.there + r.back) : "—") : (r.there ? hm(r.there) : "—")}</td></tr>`;
    }).join("") + (hub ? "" : `<tr class="tp-homeleg"><td></td><td>Home to ${home}</td>
        <td class="num tp-drive">${homeLeg ? hm(homeLeg) : "—"}</td></tr>`);
    const head = `<b>${k === 0 ? "Best trip" : `Option ${k + 1}`}</b> ·
        ${dateRange(first.start, lastE.end)} · ${plural(S.length, "event")} over ${plural(weeks, "weekend")}${off ? ` (${off} off)` : ""} ·
        <b>${hrs(t.total)}</b> driving · longest leg ${hm(longest)}`;
    return `<details class="tp-trip-block"${k === 0 ? " open" : ""}>
      <summary class="tp-trip-head">${head}</summary>
      <div class="pv-scroll"><table class="table-ledger pv-tbl tp-legs">
        <thead><tr><th>Dates</th><th>Event</th><th class="num">${hub ? "Round trip" : "Drive there"}</th></tr></thead>
        <tbody>${rows}</tbody></table></div></details>`;
  }).join("");
  return `<div class="pv-panel" id="tp-itin">
    <div class="pv-head"><h2>From ${home}</h2><span class="pv-form">${hub ? "home between events" : "road trip"}</span></div>
    <div class="pv-body">${blocks}</div></div>`;
}

function notesHtml(b) {
  const m = b.meta;
  return `<p class="pv-intro">The PDGA's published 2027 schedule, with <b>real road times</b> between every
    venue town. Pick the events you can enter, how you travel, how many weekends you'll be on the road and how
    many events you'll play in them, and the planner finds the cheapest trip from every town on the calendar.</p>
    <details class="tp-notes"><summary>What this assumes</summary><ul>
      <li>Drive times are OpenStreetMap road routing (OSRM), routed ${m.routed || "recently"}. They run
        conservative: long interstate legs come out roughly 20% longer than Google Maps quotes, so read the
        totals for ranking, not for planning fuel stops.</li>
      <li>A leg must fit between one event's last day and the next event's first at ${DRIVE_HOURS_PER_DAY} hours of
        driving a day. In <i>Home base</i> style that includes the detour home.</li>
      <li>Weekends off fall wherever they make the driving cheapest. In <i>Road trip</i> style you stay out
        through them; the planner doesn't send you home for an empty weekend.</li>
      <li>Home bases are the ${b.venues.length} towns that host a drivable event — a dense enough grid that the
        nearest one stands in for wherever you actually live.</li>
      <li>Only events open MPO / FPO pros can enter: amateur, junior, collegiate and masters (40+) events are left
        out, and US Women's and Throw Pink count for FPO only. The PDGA list doesn't always say when an event
        is restricted; corrections live in <code>data/tourplan/eligibility_overrides.csv</code>.</li>
      <li>Events in Europe, and ones with no announced venue, aren't drivable and aren't listed. Events not yet
        sanctioned can still move.</li>
    </ul></details>`;
}

/* Put both sliders on the clamped settings: weekends up to the season's
   span, events up to the most that fit in those weekends. */
function syncSliders(c) {
  const w = $("#tp-w"), n = $("#tp-n");
  if (!w || !n) return;
  w.max = String(Math.max(MIN_LEN, c.span));
  w.value = String(c.weeks);
  $("#tp-w-out").textContent = String(c.weeks);
  n.max = String(Math.max(MIN_LEN, c.fitMax));
  n.value = String(c.n);
  n.disabled = c.fitMax <= MIN_LEN;
  $("#tp-n-out").textContent = String(c.n);
  const off = c.weeks - c.n;
  $("#tp-fit-note").textContent =
    `${off ? `up to ${plural(off, "weekend")} off` : "back-to-back"} · at most ${c.fitMax} events fit in ${c.weeks} weekends`;
}

/* A settings change: clamp on the page (cheap) so the sliders answer at
   once, then rank in the worker and draw when the answer comes back. */
function update(b, div, backToBack = false) {
  let c = clampQuery(b, { div, set: cfg.set, style: cfg.style, n: cfg.n, weeks: cfg.weeks });
  // Back-to-back pins weekends to events; when the calendar can't fit that
  // many in a row, the event count clamps down and the weekends follow it.
  while (backToBack && c.n < c.weeks && c.n >= MIN_LEN) {
    c = clampQuery(b, { div, set: cfg.set, style: cfg.style, n: c.n, weeks: c.n });
  }
  cfg.weeks = c.weeks;
  cfg.n = c.n;
  syncSliders(c);
  const el = $("#tp-results");
  if (c.n < MIN_LEN) {
    el.innerHTML = `<div class="pv-panel"><div class="pv-body"><p class="pv-lede"><b>No trip fits.</b>
      Fewer than two events in this set connect within ${c.weeks} weekends — add weekends or widen the event set.</p></div></div>`;
    return;
  }
  const busyTimer = setTimeout(() => el.classList.add("tp-busy"), BUSY_MS);
  onRanked = (res) => {
    clearTimeout(busyTimer);
    el.classList.remove("tp-busy");
    draw(b, div, c.ev, res);
  };
  requestRank({ div, set: cfg.set, style: cfg.style, n: c.n, weeks: c.weeks });
}

let last = null;  // the last ranking drawn, so picking a home doesn't re-rank

function draw(b, div, ev, res) {
  last = { ev, res };
  const el = $("#tp-results");
  el.dataset.drawn = String(+(el.dataset.drawn || 0) + 1);  // lets a test wait for a fresh answer
  if (!res.homes.length) {
    el.innerHTML = `<div class="pv-panel"><div class="pv-body"><p class="pv-lede"><b>No trip fits.</b>
      ${cfg.style === "hub"
        ? "No home base can fit these events in these weekends with a drive home between each — try Road trip, more weekends or fewer events."
        : "Try more weekends or fewer events."}</p></div></div>`;
    return;
  }
  const h = cfg.home ?? res.homes[0].h;
  el.innerHTML = homesHtml(b, ev, res) + itineraryHtml(b, ev, res, h);
  el.querySelectorAll(".tp-home-row").forEach((tr) => {
    const pick = () => {
      cfg.home = +tr.dataset.h;
      const sel = $("#tp-home");
      if (sel) sel.value = String(cfg.home);
      draw(b, div, ev, res);
      $("#tp-itin")?.scrollIntoView({ block: "start", behavior: "smooth" });
    };
    tr.addEventListener("click", pick);
    tr.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } });
  });
}

async function renderTourPlan(div) {
  const el = $("#view-tour");
  $("#kicker").textContent = "2027 Tour Planner";
  $("#headline").textContent = "Where should you base a 2027 road trip?";
  $("#how-link").href = "how-it-works.html";
  $("#live-note").hidden = true;
  $("#sub-lede").textContent = "Real road times between every 2027 venue";
  const b = await loadTour();
  if (!b) {
    $("#stakes-line").innerHTML = `<b class="al">Not published yet.</b>`;
    $("#meta-line").textContent = "no bundle available";
    el.innerHTML = `<p class="pv-intro">The tour planner's data has not been published yet; it goes up with the
      next daily refresh.</p>`;
    return;
  }
  const DIV = div.toUpperCase();
  const n = b.events.filter((e) => e[div]).length;
  $("#stakes-line").innerHTML = `<b class="lk">${n}</b> drivable 2027 events open to ${DIV}`;
  $("#meta-line").innerHTML = `schedule: <a href="${b.meta.source}">PDGA 2027 announcement</a> · ` +
    `drive times: OSRM, © OpenStreetMap contributors`;
  $("#pdga-attribution").innerHTML = `2027 schedule © PDGA · road data © <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors`;
  if (cfg.home != null && cfg.home >= b.venues.length) cfg.home = null;

  el.innerHTML = notesHtml(b) + controlsHtml(b, div) + `<div id="tp-results"></div>`;
  el.querySelectorAll(".tp-seg").forEach((seg) => seg.querySelectorAll("button").forEach((btn) =>
    btn.addEventListener("click", () => {
      cfg[seg.dataset.key] = btn.dataset.v;
      seg.querySelectorAll("button").forEach((x) => x.classList.toggle("active", x === btn));
      update(b, div);
    })));
  // a drag fires input on every step; coalesce to one update per frame, and
  // the worker drops whatever a newer step superseded
  let frame = 0;
  for (const [id, key] of [["#tp-w", "weeks"], ["#tp-n", "n"]]) {
    el.querySelector(id).addEventListener("input", (e) => {
      cfg[key] = +e.target.value;
      $(`${id}-out`).textContent = e.target.value;
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => update(b, div));
    });
  }
  el.querySelector("#tp-b2b").addEventListener("click", () => { cfg.weeks = cfg.n; update(b, div, true); });
  el.querySelector("#tp-any").addEventListener("click", () => { cfg.weeks = Infinity; update(b, div); });
  el.querySelector("#tp-fill").addEventListener("click", () => { cfg.n = Infinity; update(b, div); });
  el.querySelector("#tp-home").addEventListener("change", (e) => {
    cfg.home = e.target.value === "" ? null : +e.target.value;
    if (last) draw(b, div, last.ev, last.res);
  });
  update(b, div);
}

export { renderTourPlan };
