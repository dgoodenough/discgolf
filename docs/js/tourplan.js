/* The Tour planner tab: where to base a 2027 driving trip, and which trip.

   Everything here is a shortest-path search over the published 2027 schedule
   and a town-to-town matrix of real road times, both shipped in
   data/tourplan.json by dgpt/tourplan.py. Nothing is simulated: every number
   on the tab is a sum of drive legs a reader could check on a map.

   The question has four knobs and one answer per home town:

     events    which slice of the calendar a player can enter — Elite Series
               plus Majors (a tour card), that plus every A-tier, or A-tiers
               only (no tour card)
     style     "home base": drive home after every event and out again to the
               next; "road trip": event to event, home only at the end
     length    how many events the trip plays
     spacing   back-to-back weekends only, or up to one / two weekends off
               between events

   For each candidate home (every venue town on the calendar) a dynamic
   program over the events in weekend order finds the cheapest trip of that
   length; the best homes are the ones whose cheapest trip is cheapest. It is
   a few million additions per control change, which is why it runs here
   rather than being precomputed for every combination. */

import { $, bust } from "./core.js";
import { tipAttrs } from "./tooltip.js";

/* A leg has to fit in the days between one event's last round and the next
   one's first, at this many hours behind the wheel a day. Ten is a long day
   but a normal one for touring pros, and it is the constraint that stops a
   Sunday finish in Florida "connecting" to a Friday start in Washington. */
const DRIVE_HOURS_PER_DAY = 10;

const SETS = [
  ["tour", "Elite + Majors", "Tour-card schedule: DGPT Elite Series stops and the PDGA Majors", (c) => c !== "a"],
  ["all", "Elite + Majors + A-tiers", "Everything open to a touring pro with a card", () => true],
  ["atier", "A-tiers only", "No tour card: A-tier events only (JomezPro stops are A-tiers)", (c) => c === "a"],
];
const STYLES = [
  ["hub", "Home base", "Drive home after every event, and out again to the next"],
  ["road", "Road trip", "Drive event to event; home only at the start and the end"],
];
const LENGTHS = [3, 4, 5, 6, 7, 8];
const SPACING = [
  [0, "Back-to-back", "An event every weekend, no weekends off"],
  [1, "≤1 week off", "Up to one empty weekend between consecutive events"],
  [2, "≤2 weeks off", "Up to two empty weekends between consecutive events"],
];
const TOP_HOMES = 15;
const ALT_TRIPS = 5;

const cfg = { set: "all", style: "hub", n: 5, off: 0, home: null };
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

/* ---------- formatting ---------- */

const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const dParts = (iso) => { const [, m, d] = iso.split("-").map(Number); return [m, d]; };
function dateRange(a, b) {
  const [m1, d1] = dParts(a), [m2, d2] = dParts(b);
  return m1 === m2 ? `${MON[m1 - 1]} ${d1}–${d2}` : `${MON[m1 - 1]} ${d1}–${MON[m2 - 1]} ${d2}`;
}
const dayNum = (iso) => Date.UTC(...iso.split("-").map((x, i) => (i === 1 ? x - 1 : +x))) / 86400000;
const hm = (min) => (min < 60 ? `${min}m` : `${Math.floor(min / 60)}h ${String(min % 60).padStart(2, "0")}m`);
const hrs = (min) => (min / 60).toFixed(min < 600 ? 1 : 0) + " h";
const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const CAT = { elite: "Elite", major: "Major", a: "A" };

/* ---------- the search ---------- */

/* The events a reader can enter under the current settings, in weekend
   order, each with its predecessors: earlier events 1..(1+off) weekends
   before it that end before it starts. */
function prepare(b, div, set, off) {
  const keep = SETS.find((s) => s[0] === set)[3];
  const ev = b.events
    .filter((e) => e[div] && keep(e.cat))
    .map((e) => ({ ...e, s: dayNum(e.start), f: dayNum(e.end) }))
    .sort((x, y) => x.wk - y.wk || x.s - y.s);
  for (const e of ev) {
    e.pred = [];
    for (let j = 0; j < ev.length; j++) {
      const p = ev[j], dw = e.wk - p.wk;
      if (dw >= 1 && dw <= 1 + off && e.s > p.f) e.pred.push(j);
    }
  }
  return ev;
}

/* The cheapest n-event trip from home `h`, ending at each event: one DP pass,
   then a backtrack per end. Returns trips sorted cheapest first. */
function tripsFrom(h, ev, M, nv, n, style) {
  const E = ev.length, INF = Infinity;
  const d = (a, b) => M[a * nv + b];
  const budget = (p, e) => (e.s - p.f) * DRIVE_HOURS_PER_DAY * 60;
  // the cost of getting from event p to event e, or INF if it doesn't fit
  const leg = style === "hub"
    ? (p, e) => { const c = d(p.v, h) + d(h, e.v); return c <= budget(p, e) ? c : INF; }
    : (p, e) => { const c = d(p.v, e.v); return c <= budget(p, e) ? c : INF; };
  let cost = new Float64Array(E);
  const back = [];
  for (let i = 0; i < E; i++) cost[i] = d(h, ev[i].v);
  for (let k = 1; k < n; k++) {
    const next = new Float64Array(E).fill(INF), par = new Int32Array(E).fill(-1);
    for (let i = 0; i < E; i++) {
      const e = ev[i];
      for (const j of e.pred) {
        if (cost[j] === INF) continue;
        const c = cost[j] + leg(ev[j], e);
        if (c < next[i]) { next[i] = c; par[i] = j; }
      }
    }
    back.push(par);
    cost = next;
  }
  const out = [];
  for (let i = 0; i < E; i++) {
    if (cost[i] === INF) continue;
    const total = cost[i] + d(ev[i].v, h);
    const path = [i];
    for (let k = back.length - 1; k >= 0; k--) path.unshift(back[k][path[0]]);
    out.push({ total, path });
  }
  out.sort((a, b) => a.total - b.total);
  return out;
}

function solve(b, div) {
  const ev = prepare(b, div, cfg.set, cfg.off);
  const M = b.minutes, nv = b.venues.length;
  const homes = [];
  const perHome = new Map();
  for (let h = 0; h < nv; h++) {
    const trips = tripsFrom(h, ev, M, nv, cfg.n, cfg.style);
    perHome.set(h, trips);
    if (trips.length) homes.push({ h, best: trips[0] });
  }
  homes.sort((a, b2) => a.best.total - b2.best.total);
  return { ev, homes, perHome };
}

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

function controlsHtml(b, div) {
  const homes = b.venues.map((v, i) => [i, v.name]).sort((x, y) => x[1].localeCompare(y[1]));
  return `<div class="pv-panel">
    <div class="pv-head"><h2>Plan a trip</h2><span class="pv-form">${b.events.filter((e) => e[div]).length} drivable events · ${b.venues.length} towns</span></div>
    <div class="pv-body tp-controls">
      <div class="tp-row"><span class="tp-label">Events</span>${segHtml("set", SETS, cfg.set)}</div>
      <div class="tp-row"><span class="tp-label">Style</span>${segHtml("style", STYLES, cfg.style)}</div>
      <div class="tp-row"><span class="tp-label">Length</span>${segHtml("n", LENGTHS.map((n) => [n, `${n}`, `${n} events`]), cfg.n)}
        <span class="tp-unit">events</span></div>
      <div class="tp-row"><span class="tp-label">Spacing</span>${segHtml("off", SPACING, cfg.off)}</div>
      <div class="tp-row"><label class="tp-label" for="tp-home">Your home</label>
        <select id="tp-home"><option value="">Best home base (below)</option>${homes.map(([i, name]) =>
          `<option value="${i}" ${cfg.home === i ? "selected" : ""}>${esc(name)}</option>`).join("")}</select></div>
    </div></div>`;
}

function tripCell(trip, ev) {
  return trip.path.map((i) => esc(ev[i].name)).join(" <span class=\"tp-arrow\">→</span> ");
}

function homesHtml(b, res) {
  const { ev, homes } = res;
  if (!homes.length) return "";
  const rows = homes.slice(0, TOP_HOMES).map(({ h, best }, r) => {
    const first = ev[best.path[0]], last = ev[best.path[best.path.length - 1]];
    return `<tr class="tp-home-row${cfg.home === h ? " tp-sel" : ""}" data-h="${h}" tabindex="0">
      <td class="num">${r + 1}</td>
      <td class="tp-town">${esc(b.venues[h].name)}</td>
      <td class="num"><b>${hrs(best.total)}</b></td>
      <td class="num t2">${hrs(best.total / cfg.n)}</td>
      <td class="tp-when">${dateRange(first.start, last.end)}</td>
      <td class="tp-trip t1">${tripCell(best, ev)}</td></tr>`;
  }).join("");
  return `<div class="pv-panel">
    <div class="pv-head"><h2>Best home bases</h2><span class="pv-form">cheapest ${cfg.n}-event trip from each town</span></div>
    <div class="pv-body">
      <p class="pv-lede">Every town on the 2027 calendar, ranked by the total driving of its single best trip. Tap a town for its itinerary and runner-up trips${cfg.home == null ? "; the top town's is below" : ""}.</p>
      <div class="pv-scroll"><table class="table-ledger pv-tbl tp-tbl">
        <thead><tr><th class="num">#</th><th>Home base</th><th class="num">Driving</th>
          <th class="num t2">Per event</th><th>When</th><th class="t1">Trip</th></tr></thead>
        <tbody>${rows}</tbody></table></div>
    </div></div>`;
}

function itineraryHtml(b, res, h) {
  const { ev, perHome } = res;
  const M = b.minutes, nv = b.venues.length;
  const alts = alternatives(perHome.get(h) || [], ev);
  const home = esc(b.venues[h].name);
  const hub = cfg.style === "hub";
  if (!alts.length) {
    return `<div class="pv-panel" id="tp-itin"><div class="pv-head"><h2>From ${home}</h2></div>
      <div class="pv-body"><p class="pv-lede">No ${cfg.n}-event trip fits these settings from here.</p></div></div>`;
  }
  const blocks = alts.map((t, k) => {
    const S = stops(t, h, ev, M, nv, cfg.style);
    const lastE = S[S.length - 1].e;
    const homeLeg = hub ? null : M[lastE.v * nv + h];
    const legsMin = S.flatMap((r) => (hub ? [r.there, r.back] : [r.there])).concat(hub ? [] : [homeLeg]);
    const longest = Math.max(...legsMin);
    const first = S[0].e;
    const weeks = lastE.wk - first.wk + 1;
    const rows = S.map((r) => `<tr><td class="tp-when">${dateRange(r.e.start, r.e.end)}</td>
        <td><span class="tp-cat tp-${r.e.cat}">${CAT[r.e.cat]}</span> ${esc(r.e.name)}
          <span class="tp-at">${esc(b.venues[r.e.v].name)}</span></td>
        <td class="num tp-drive">${hub ? (r.there + r.back ? hm(r.there + r.back) : "—") : (r.there ? hm(r.there) : "—")}</td></tr>`).join("")
      + (hub ? "" : `<tr class="tp-homeleg"><td></td><td>Home to ${home}</td>
        <td class="num tp-drive">${homeLeg ? hm(homeLeg) : "—"}</td></tr>`);
    const head = `<b>${k === 0 ? "Best trip" : `Option ${k + 1}`}</b> ·
        ${dateRange(first.start, lastE.end)} · ${cfg.n} events over ${weeks} weekend${weeks > 1 ? "s" : ""} ·
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
    venue town. Pick the events you can enter, how you travel, how many events and how many weekends off, and the
    planner finds the cheapest trip from every town on the calendar.</p>
    <details class="tp-notes"><summary>What this assumes</summary><ul>
      <li>Drive times are OpenStreetMap road routing (OSRM), routed ${m.routed || "recently"}. They run
        conservative: long interstate legs come out roughly 20% longer than Google Maps quotes, so read the
        totals for ranking, not for planning fuel stops.</li>
      <li>A leg must fit between one event's last day and the next event's first at ${DRIVE_HOURS_PER_DAY} hours of
        driving a day. In <i>Home base</i> style that includes the detour home.</li>
      <li>Home bases are the ${b.venues.length} towns that host a drivable event — a dense enough grid that the
        nearest one stands in for wherever you actually live.</li>
      <li>Only events open MPO / FPO pros can enter: amateur, junior, collegiate and masters (40+) events are left
        out, and US Women's and Throw Pink count for FPO only. The PDGA list doesn't always say when an event
        is restricted; corrections live in <code>data/tourplan/eligibility_overrides.csv</code>.</li>
      <li>Events in Europe, and ones with no announced venue, aren't drivable and aren't listed. Events not yet
        sanctioned can still move.</li>
    </ul></details>`;
}

function renderResults(b, div) {
  const res = solve(b, div);
  const el = $("#tp-results");
  if (!res.homes.length) {
    el.innerHTML = `<div class="pv-panel"><div class="pv-body"><p class="pv-lede"><b>No ${cfg.n}-event trip fits.</b>
      ${cfg.off === 0 ? "This calendar has no run of that many back-to-back weekends for this event set — allow a week off, or shorten the trip." : "Shorten the trip or widen the event set."}</p></div></div>`;
    return;
  }
  const h = cfg.home ?? res.homes[0].h;
  el.innerHTML = homesHtml(b, res) + itineraryHtml(b, res, h);
  el.querySelectorAll(".tp-home-row").forEach((tr) => {
    const pick = () => {
      cfg.home = +tr.dataset.h;
      const sel = $("#tp-home");
      if (sel) sel.value = String(cfg.home);
      renderResults(b, div);
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
      const key = seg.dataset.key;
      cfg[key] = key === "n" || key === "off" ? +btn.dataset.v : btn.dataset.v;
      seg.querySelectorAll("button").forEach((x) => x.classList.toggle("active", x === btn));
      renderResults(b, div);
    })));
  el.querySelector("#tp-home").addEventListener("change", (e) => {
    cfg.home = e.target.value === "" ? null : +e.target.value;
    renderResults(b, div);
  });
  renderResults(b, div);
}

export { renderTourPlan, prepare, tripsFrom };
