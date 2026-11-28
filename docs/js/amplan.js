/* The Amateur planner tab (alpha): plan a season of sanctioned amateur events
   from your own home.

   The Tour planner ranks home bases over ~200 A-tier-and-up events with real
   road times. This is the same search turned toward amateurs: every
   sanctioned event open to them, B and C tiers included (~7,000 a year), from
   one home at a time — yours. dgpt/amplan.py writes the bundle;
   dgpt/tourscan.py has the measurements behind the design.

   Two shortcuts make the scale work, both measured on the 2026 calendar:

     drive time   a straight-line estimate, a + b * km, fitted to the Tour
                  planner's real road times. Within 3-8% on long legs, rough
                  under 200 km; the page says so and marks times with "~".
     pruning      each weekend keeps only the K events nearest home. K = 10
                  found the exact best trip for every home tested.

   After pruning it is the Tour planner's own search (tourplan-search.js) on
   a few hundred events: a few milliseconds, so no worker. */

import { $, bust, dateRange, esc, hm, hrs, plural } from "./core.js";
import { tipAttrs } from "./tooltip.js";
import { DRIVE_HOURS_PER_DAY, MIN_LEN, maxFit, tripsFrom } from "./tourplan-search.js";

const K_NEAREST = 10;
const ALT_TRIPS = 5;
const TIERS = [
  ["A", "A-tier"], ["B", "B-tier"], ["C", "C-tier"],
  ["X", "X-tier", "Experimental-format events (XA / XB / XC)"],
  ["M", "Majors", "Amateur Majors: Am Worlds, US Amateur Championships and the like"],
];
const STYLES = [
  ["hub", "Home base", "Drive home after every event, and out again to the next"],
  ["road", "Road trip", "Drive event to event; home only at the start and the end"],
];
const HOME_KEY = "amplan.home";

const cfg = { year: null, tiers: new Set(["A", "B", "C"]), style: "hub", n: 5, weeks: 6, home: null };
let bundle;  // undefined = not fetched yet, null = fetch failed

async function loadAm() {
  if (bundle === undefined) {
    try {
      const r = await fetch(bust("data/amplan.json"));
      bundle = r.ok ? await r.json() : null;
    } catch { bundle = null; }
  }
  return bundle;
}

/* ---------- data ---------- */

const iso = (day) => new Date(day * 864e5).toISOString().slice(0, 10);
const tierGroups = (t) => [...new Set(String(t).toUpperCase().split("/").map((x) =>
  x.startsWith("X") ? "X" : x.trim()))];

function km(lat1, lon1, lat2, lon2) {
  const r = Math.PI / 180;
  const h = Math.sin((lat2 - lat1) * r / 2) ** 2 +
    Math.cos(lat1 * r) * Math.cos(lat2 * r) * Math.sin((lon2 - lon1) * r / 2) ** 2;
  return 2 * 6371 * Math.asin(Math.sqrt(h));
}

/* Estimated road minutes. Same town (under a km) is no drive at all, not the
   fit's intercept — two events at one park shouldn't cost an hour. */
function driveMin(b, lat1, lon1, lat2, lon2) {
  const d = km(lat1, lon1, lat2, lon2);
  return d < 1 ? 0 : Math.round(b.meta.drive.a + b.meta.drive.b * d);
}

function rows(b) {
  const F = Object.fromEntries(b.meta.fields.map((f, i) => [f, i]));
  return b.events.map((r) => ({
    id: r[F.id], name: r[F.name], tier: r[F.tier], amOnly: r[F.am_only] === 1,
    s: r[F.start], f: r[F.end], wk: r[F.wk], lat: r[F.lat], lon: r[F.lon],
    place: b.places[r[F.place]], reg: r[F.reg],
    year: Number(iso(r[F.start]).slice(0, 4)),
  }));
}

/* Distinct places with a coordinate each, for the home picker. */
function placeList(all) {
  const m = new Map();
  for (const e of all) if (e.place && !m.has(e.place)) m.set(e.place, { label: e.place, lat: e.lat, lon: e.lon });
  return [...m.values()].sort((x, y) => x.label.localeCompare(y.label));
}

/* The search problem for one home: the year's events in the chosen tiers,
   pruned to the K nearest home each weekend, with a small distance matrix
   over them (home at index 0) in the shape tourplan-search expects. */
function problem(b, all, home) {
  const pool = all.filter((e) => e.year === cfg.year && tierGroups(e.tier).some((t) => cfg.tiers.has(t)));
  const byWk = new Map();
  for (const e of pool) {
    const d = driveMin(b, home.lat, home.lon, e.lat, e.lon);
    if (!byWk.has(e.wk)) byWk.set(e.wk, []);
    byWk.get(e.wk).push({ ...e, home: d });
  }
  const ev = [];
  for (const wk of [...byWk.keys()].sort((x, y) => x - y)) {
    ev.push(...byWk.get(wk).sort((x, y) => x.home - y.home).slice(0, K_NEAREST));
  }
  ev.sort((x, y) => x.wk - y.wk || x.s - y.s);
  const nv = ev.length + 1;
  const M = new Float64Array(nv * nv);
  const pt = [home, ...ev];
  for (let i = 0; i < nv; i++) {
    for (let j = i + 1; j < nv; j++) {
      M[i * nv + j] = M[j * nv + i] = driveMin(b, pt[i].lat, pt[i].lon, pt[j].lat, pt[j].lon);
    }
  }
  ev.forEach((e, i) => { e.v = i + 1; });
  const span = ev.length ? ev[ev.length - 1].wk - ev[0].wk + 1 : MIN_LEN;
  const maxDw = Math.max(1, Math.min(cfg.weeks, span));
  for (const e of ev) {
    e.pred = []; e.dw = [];
    ev.forEach((p, j) => {
      const dw = e.wk - p.wk;
      if (dw >= 1 && dw <= maxDw && e.s > p.f) { e.pred.push(j); e.dw.push(dw); }
    });
  }
  return { pool, ev, M, nv, span };
}

/* ---------- home ---------- */

function saveHome(h) {
  cfg.home = h;
  try { localStorage.setItem(HOME_KEY, JSON.stringify(h)); } catch { /* private mode etc. */ }
}
function loadHome() {
  try { return JSON.parse(localStorage.getItem(HOME_KEY) || "null"); } catch { return null; }
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

function controlsHtml(b, all, places) {
  const years = b.meta.years.map((y) => [y, `${y}`, `${all.filter((e) => e.year === y).length} events`]);
  return `<div class="pv-panel">
    <div class="pv-head"><h2>Plan a season</h2><span class="pv-form">${all.length.toLocaleString()} amateur events · alpha</span></div>
    <div class="pv-body tp-controls">
      <div class="tp-row"><label class="tp-label" for="am-home">Home</label>
        <input id="am-home" class="am-home" list="am-places" placeholder="Type a town: Emporia, KS"
          value="${cfg.home ? esc(cfg.home.label) : ""}" autocomplete="off">
        <datalist id="am-places">${places.map((p) => `<option value="${esc(p.label)}">`).join("")}</datalist>
        <button type="button" class="tp-max" id="am-locate">Use my location</button></div>
      <div class="tp-row"><span class="tp-label">Season</span>${segHtml("year", years, cfg.year)}</div>
      <div class="tp-row"><span class="tp-label">Tiers</span><div class="am-tiers">${TIERS.map(([t, label, tip]) =>
        `<label class="am-tier" ${tip ? tipAttrs(tip) : ""}><input type="checkbox" value="${t}" ${cfg.tiers.has(t) ? "checked" : ""}> ${label}</label>`).join("")}</div></div>
      <div class="tp-row"><span class="tp-label">Style</span>${segHtml("style", STYLES, cfg.style)}</div>
      ${rangeHtml("am-w", "On the road", "weekends",
        "How many weekends the trip spans, first event to last. Weekends in that span without an event are weekends off.")}
      ${rangeHtml("am-n", "Events", "played",
        "How many events you play: at most one a weekend, and no more than this calendar fits near you.")}
      <div class="tp-row tp-fit"><span class="tp-label"></span>
        <span id="am-fit-note" class="tp-unit"></span>
        <button type="button" class="tp-max" id="am-b2b">Back-to-back</button>
        <button type="button" class="tp-max" id="am-any">Any spacing</button>
        <button type="button" class="tp-max" id="am-fill">Fill</button></div>
    </div></div>`;
}

const eventLink = (e) => `<a href="https://www.pdga.com/tour/event/${e.id}" target="_blank" rel="noopener">${esc(e.name)}</a>`;

function itineraryHtml(b, P, trips) {
  const hub = cfg.style === "hub";
  const home = esc(cfg.home.label);
  const d = (x, y) => P.M[x * P.nv + y];
  return trips.map((t, k) => {
    const S = t.path.map((i) => P.ev[i]);
    let at = 0;
    const legs = S.map((e) => { const there = d(hub ? 0 : at, e.v), back = hub ? d(e.v, 0) : 0; at = e.v; return { e, there, back }; });
    const homeLeg = hub ? 0 : d(at, 0);
    const longest = Math.max(...legs.map((l) => (hub ? Math.max(l.there, l.back) : l.there)), homeLeg);
    const weeks = S[S.length - 1].wk - S[0].wk + 1, off = weeks - S.length;
    const rowsHtml = legs.map((l, x) => {
      const gap = x > 0 ? l.e.wk - legs[x - 1].e.wk - 1 : 0;
      const drive = hub ? l.there + l.back : l.there;
      return (gap > 0 ? `<tr class="tp-offrow"><td></td><td>${plural(gap, "weekend")} off</td><td></td></tr>` : "") +
        `<tr><td class="tp-when">${dateRange(iso(l.e.s), iso(l.e.f))}</td>
          <td><span class="tp-cat">${esc(l.e.tier)}</span> ${eventLink(l.e)}
            <span class="tp-at">${esc(l.e.place)}${l.e.reg ? ` · <a href="${esc(l.e.reg)}" target="_blank" rel="noopener">register</a>` : ""}</span></td>
          <td class="num tp-drive">${drive ? "~" + hm(drive) : "—"}</td></tr>`;
    }).join("") + (hub ? "" : `<tr class="tp-homeleg"><td></td><td>Home to ${home}</td>
        <td class="num tp-drive">${homeLeg ? "~" + hm(homeLeg) : "—"}</td></tr>`);
    return `<details class="tp-trip-block"${k === 0 ? " open" : ""}>
      <summary class="tp-trip-head"><b>${k === 0 ? "Best trip" : `Option ${k + 1}`}</b> ·
        ${dateRange(iso(S[0].s), iso(S[S.length - 1].f))} · ${plural(S.length, "event")} over ${plural(weeks, "weekend")}${off ? ` (${off} off)` : ""} ·
        <b>~${hrs(t.total)}</b> driving · longest leg ~${hm(longest)}</summary>
      <div class="pv-scroll"><table class="table-ledger pv-tbl tp-legs">
        <thead><tr><th>Dates</th><th>Event</th><th class="num">${hub ? "Round trip" : "Drive there"}</th></tr></thead>
        <tbody>${rowsHtml}</tbody></table></div></details>`;
  }).join("");
}

function notesHtml(b) {
  const err = b.meta.drive.error || {};
  const band = (k) => (err[k] ? `${Math.round(err[k][0] * 100)}%` : "?");
  return `<div class="exp-banner">
      <p class="exp-title">Alpha · estimated drive times · every sanctioned amateur event</p>
      <p class="exp-text">Pick a home, the tiers you play and how long you'll travel, and this finds the trip with the
        least driving across every PDGA-sanctioned event open to amateurs in the US and Canada.</p>
    </div>
    <details class="tp-notes"><summary>What this assumes</summary><ul>
      <li>Drive times are <b>estimates</b> from straight-line distance, fitted to real road times: typically within
        ${band("600-1500 km")} on long drives but ${band("0-200 km")} off on short ones, so nearby events can swap order.</li>
      <li>Each weekend only the ${K_NEAREST} events nearest your home are considered. On the 2026 calendar that found the
        same best trip as searching everything, for every home tested.</li>
      <li>A leg must fit between one event's last day and the next one's first at ${DRIVE_HOURS_PER_DAY} hours of driving a
        day.</li>
      <li>Events are PDGA-sanctioned, open to amateurs (Am or Pro-Am), not cancelled, not leagues. Divisions aren't
        checked: an event listed Pro-Am may not offer yours.</li>
      <li>Next season fills in as events are sanctioned — B and C-tier dates are approved through the winter.</li>
    </ul></details>`;
}

/* ---------- the run ---------- */

let all = null, places = null;

function syncSliders(span, weeks, n, fitMax) {
  const w = $("#am-w"), nn = $("#am-n");
  w.max = String(Math.max(MIN_LEN, span)); w.value = String(weeks);
  $("#am-w-out").textContent = String(weeks);
  nn.max = String(Math.max(MIN_LEN, fitMax)); nn.value = String(n);
  nn.disabled = fitMax <= MIN_LEN;
  $("#am-n-out").textContent = String(n);
  const off = weeks - n;
  $("#am-fit-note").textContent =
    `${off ? `up to ${plural(off, "weekend")} off` : "back-to-back"} · at most ${fitMax} events fit in ${weeks} weekends`;
}

function update(b, backToBack = false) {
  const el = $("#am-results");
  if (!cfg.home) {
    el.innerHTML = `<p class="pv-intro">Choose a home above — any town from the list, or your location — to see your trips.</p>`;
    return;
  }
  if (!cfg.tiers.size) {
    el.innerHTML = `<p class="pv-intro">Tick at least one tier.</p>`;
    return;
  }
  let P, fitMax;
  for (let guard = 0; guard < 60; guard++) {
    P = problem(b, all, cfg.home);
    cfg.weeks = Math.max(MIN_LEN, Math.min(cfg.weeks, P.span));
    fitMax = maxFit(P.ev, P.M, P.nv, cfg.weeks);
    cfg.n = Math.max(Math.min(cfg.n, fitMax, cfg.weeks), Math.min(MIN_LEN, fitMax));
    if (!(backToBack && cfg.n < cfg.weeks && cfg.n >= MIN_LEN)) break;
    cfg.weeks = cfg.n;
  }
  syncSliders(P.span, cfg.weeks, cfg.n, fitMax);
  const near = P.pool.filter((e) => driveMin(b, cfg.home.lat, cfg.home.lon, e.lat, e.lon) <= 180).length;
  const head = `<p class="pv-lede">${near.toLocaleString()} of ${P.pool.length.toLocaleString()} matching ${cfg.year}
    events are within about 3 hours of ${esc(cfg.home.label)}.</p>`;
  const trips = cfg.n >= MIN_LEN ? tripsFrom(0, P.ev, P.M, P.nv, cfg.n, cfg.weeks, cfg.style) : [];
  const seen = new Set(), alts = [];
  for (const t of trips) {
    const wk = P.ev[t.path[0]].wk;
    if (seen.has(wk)) continue;
    seen.add(wk); alts.push(t);
    if (alts.length >= ALT_TRIPS) break;
  }
  el.dataset.drawn = String(+(el.dataset.drawn || 0) + 1);
  el.innerHTML = `<div class="pv-panel" id="am-itin">
    <div class="pv-head"><h2>From ${esc(cfg.home.label)}</h2>
      <span class="pv-form">${cfg.n} events in ≤${cfg.weeks} weekends · ${cfg.style === "hub" ? "home between events" : "road trip"}</span></div>
    <div class="pv-body">${head}${alts.length ? itineraryHtml(b, P, alts)
      : `<p class="pv-lede"><b>No trip fits.</b> Try more weekends, fewer events, more tiers${cfg.style === "hub" ? " or Road trip" : ""}.</p>`}</div></div>`;
}

async function renderAmPlan() {
  const el = $("#view-am");
  $("#kicker").textContent = "Amateur Planner · alpha";
  $("#headline").textContent = "Plan your season of sanctioned events";
  $("#how-link").href = "how-it-works.html";
  $("#live-note").hidden = true;
  $("#sub-lede").textContent = "Every PDGA-sanctioned amateur event, from your home";
  const b = await loadAm();
  if (!b) {
    $("#stakes-line").innerHTML = `<b class="al">Not published yet.</b>`;
    $("#meta-line").textContent = "no bundle available";
    el.innerHTML = `<p class="pv-intro">The amateur planner's data has not been published yet; it goes up with the next
      daily refresh.</p>`;
    return;
  }
  all ||= rows(b);
  places ||= placeList(all);
  if (cfg.year == null) {
    // next season once it has enough of a calendar to plan around
    const [y0, y1] = b.meta.years;
    cfg.year = all.filter((e) => e.year === y1).length >= 1000 ? y1 : y0;
  }
  cfg.home ||= loadHome();
  $("#stakes-line").innerHTML = `<b class="lk">${all.length.toLocaleString()}</b> sanctioned amateur events in ` +
    `${b.meta.years.join(" and ")} · US and Canada`;
  $("#meta-line").innerHTML = `events: PDGA · drive times: estimated`;
  $("#pdga-attribution").innerHTML = `Event data © PDGA · PDGA Authorized Developer`;

  el.innerHTML = notesHtml(b) + controlsHtml(b, all, places) + `<div id="am-results"></div>`;
  const go = (b2b = false) => update(b, b2b);

  el.querySelectorAll(".tp-seg").forEach((seg) => seg.querySelectorAll("button").forEach((btn) =>
    btn.addEventListener("click", () => {
      const k = seg.dataset.key;
      cfg[k] = k === "year" ? +btn.dataset.v : btn.dataset.v;
      seg.querySelectorAll("button").forEach((x) => x.classList.toggle("active", x === btn));
      go();
    })));
  el.querySelectorAll(".am-tier input").forEach((cb) => cb.addEventListener("change", () => {
    if (cb.checked) cfg.tiers.add(cb.value); else cfg.tiers.delete(cb.value);
    go();
  }));
  let frame = 0;
  for (const [id, key] of [["#am-w", "weeks"], ["#am-n", "n"]]) {
    el.querySelector(id).addEventListener("input", (e) => {
      cfg[key] = +e.target.value;
      $(`${id}-out`).textContent = e.target.value;
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => go());
    });
  }
  el.querySelector("#am-b2b").addEventListener("click", () => { cfg.weeks = cfg.n; go(true); });
  el.querySelector("#am-any").addEventListener("click", () => { cfg.weeks = Infinity; go(); });
  el.querySelector("#am-fill").addEventListener("click", () => { cfg.n = Infinity; go(); });
  const homeIn = el.querySelector("#am-home");
  homeIn.addEventListener("change", () => {
    const p = places.find((x) => x.label.toLowerCase() === homeIn.value.trim().toLowerCase());
    if (p) { saveHome(p); go(); }
  });
  el.querySelector("#am-locate").addEventListener("click", () => {
    if (!navigator.geolocation) return;
    navigator.geolocation.getCurrentPosition((pos) => {
      const { latitude: lat, longitude: lon } = pos.coords;
      saveHome({ label: "your location", lat, lon });
      homeIn.value = "your location";
      go();
    }, () => { homeIn.placeholder = "Location unavailable — type a town"; });
  });
  go();
}

export { renderAmPlan, tierGroups, problem, rows };
