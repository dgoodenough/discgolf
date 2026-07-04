/* DGPT Standings Forecast app.
   Three views over per-division JSON bundles; the what-if mode re-simulates
   a single player against frozen per-sim cutlines ("cutline replay"). */
"use strict";

const state = { div: "mpo", data: {}, sort: { key: "p_champ", dir: "desc" } };

const $ = (sel) => document.querySelector(sel);
const fmtPts = (x) => (Math.round(x * 100) / 100).toLocaleString("en-US");
const fmtPct = (x) => (x >= 0.9995 ? ">99.9%" : x < 0.0005 ? "<0.1%" : (x * 100).toFixed(1) + "%");

async function loadDiv(div) {
  if (!state.data[div]) {
    const resp = await fetch(`data/${div}.json`);
    state.data[div] = await resp.json();
  }
  return state.data[div];
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

/* which banked events count toward the season total (best-N, top-2 majors);
   the rest are "dropped" and shown struck through */
function countedTids(p, meta) {
  const majors = p.banked.filter((b) => b.major).slice().sort((a, b) => b.pts - a.pts);
  const countedMajors = majors.slice(0, meta.majors_counted);
  const pool = p.banked.filter((b) => !b.major).concat(countedMajors).sort((a, b) => b.pts - a.pts);
  return new Set(pool.slice(0, meta.top_n_finishes).map((b) => b.tid));
}

/* small inline sparkline of the finishing-rank distribution; hover shows the
   exact place + probability immediately via a shared floating tooltip */
const sparkStore = new Map(); // pdga -> hist array
function sparkCell(p, meta) {
  sparkStore.set(p.pdga, p.hist);
  const max = Math.max(...p.hist, 1e-9);
  const show = p.hist.length;
  let cols = "";
  for (let k = 0; k < show; k++) {
    const h = Math.max(1, Math.round((p.hist[k] / max) * 100));
    const cls = k + 1 <= meta.cut ? "in-cut" : k === show - 1 ? "overflow" : "";
    cols += `<i class="col ${cls}" style="height:${h}%"></i>`;
  }
  return `<div class="spark" data-pdga="${p.pdga}">${cols}</div>`;
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
function forecastCols(meta) {
  const perf = meta.field_size - meta.cut; // MVP-performance championship spots
  return [
    { key: "rank", label: "#", num: true, get: (p) => p.rank, cell: (p) => `<span class="dim">${p.rank}</span>`, dir0: "asc" },
    { key: "name", label: "Player", num: false, get: (p) => p.name.toLowerCase(), cell: nameCell, dir0: "asc" },
    { key: "points", label: "Points", num: true, get: (p) => p.points, cell: (p) => `<b>${fmtPts(p.points)}</b>`, dir0: "desc" },
    { key: "p_champ", label: "Cup", title: "P(in the Powerball Cup field): automatic bid, MVP-performance qualifier, or a DGPT/Major event win (special invite — 100% if already won)", num: true, get: (p) => p.p_champ, cell: (p) => `<b class="${probClass(p.p_champ)}">${fmtPct(p.p_champ)}</b>`, dir0: "desc" },
    { key: "mean_pts", label: "Proj. pts", hide: "t1", num: true, get: (p) => p.mean_pts, cell: (p) => `<span class="dim">${fmtPts(p.mean_pts)}</span>`, dir0: "desc" },
    { key: "spark", label: "Finish distribution", hide: "t1", num: false, sortable: false, cell: (p) => sparkCell(p, meta) },
    { key: "p_cut", label: "Auto Bid", hide: "t2", title: `P(finish top ${meta.cut} in World Standings — automatic Powerball Cup berth)`, num: true, get: (p) => p.p_cut, cell: (p) => `<span class="${probClass(p.p_cut)}">${fmtPct(p.p_cut)}</span>`, dir0: "desc" },
    { key: "p_mvp_qual", label: "MVP Bid", hide: "t2", title: `P(earns a Cup spot via a top-${perf} MVP Open finish, outside the standings cut)`, num: true, get: (p) => p.p_mvp_qual, cell: (p) => `<span class="${probClass(p.p_mvp_qual)}">${fmtPct(p.p_mvp_qual)}</span>`, dir0: "desc" },
    { key: "p_gmc", label: "GMC", hide: "t3", title: `P(top ${meta.gmc_cut} before the Green Mountain Championship — makes the first playoff field). Assumes every qualifier attends; playoff signups aren't open yet.`, num: true, get: (p) => p.p_gmc, cell: (p) => `<span class="${probClass(p.p_gmc)}">${fmtPct(p.p_gmc)}</span>`, dir0: "desc" },
    { key: "p_mvp", label: "MVP", hide: "t3", title: `P(top ${meta.mvp_cut} before the MVP Open — makes the second playoff field via points). Assumes every qualifier attends; playoff signups aren't open yet.`, num: true, get: (p) => p.p_mvp, cell: (p) => `<span class="${probClass(p.p_mvp)}">${fmtPct(p.p_mvp)}</span>`, dir0: "desc" },
    { key: "rating", label: "Rating", hide: "t4", num: true, get: (p) => p.rating || 0, cell: (p) => `<span class="dim">${p.rating || ""}</span>`, dir0: "desc" },
    { key: "starts", label: "Starts", hide: "t4", num: true, get: (p) => p.banked.length, cell: (p) => `<span class="dim">${p.banked.length}</span>`, dir0: "desc" },
    { key: "mean_rank", label: "Proj. rank", hide: "t4", num: true, get: (p) => p.mean_rank, cell: (p) => `<span class="dim">${p.mean_rank.toFixed(1)}</span>`, dir0: "asc" },
  ];
}

function renderForecast(d) {
  const meta = d.meta;
  const cols = forecastCols(meta);
  const sort = state.sort;
  const col = cols.find((c) => c.key === sort.key) || cols[0];
  const rows = [...d.players].filter((p) => p.points > 0 || p.p_field >= 0.0005);
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
    <div class="table-tools">
      <button class="btn" id="cols-toggle">${state.colsAll ? "Fewer columns" : "All columns"}</button>
    </div>
    <div class="table-wrap${state.colsAll ? " cols-all" : ""}">
      <table class="table-ledger" id="forecast-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>
    </div>
    <p class="dim" style="font-size:.75rem;margin-top:6px">${rows.length} players · click a column to sort · click a row for the event breakdown and inline what-if · hover the sparkline for exact odds · <b>Cup</b> = Auto Bid + MVP Bid + event-winner invites.
    🥇 = won a points event this year; a DGPT Elite or Major win earns a guaranteed Cup spot via special invite (Cup = 100%), so these odds already include winning a remaining event.
    <br><b>Playoff assumption:</b> GMC and MVP fields assume every player who qualifies will attend — signups aren't open yet, so those odds will shift once they are.</p>`;

  $("#cols-toggle").addEventListener("click", () => { state.colsAll = !state.colsAll; renderForecast(d); });
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
  if (next && next.classList.contains("detail")) { next.remove(); return; }
  tr.parentElement.querySelectorAll("tr.detail").forEach((x) => x.remove());
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
const DOUBLES_NOTE = "Team pairings aren't modeled yet — points assume a field-average partner. TODO: use announced teams once the full list is out.";
const PLAYOFF_NOTE = "Playoff registration isn't open yet — the model assumes every player who qualifies for this field will attend. Real signups will shift these odds.";

/* project a player's points if they played event e (same model as the
   replay); returns avg / median / 90th / ceiling over a quick Monte Carlo */
function projectPoints(d, p, e, draws = 2500) {
  const out = new Float64Array(draws);
  for (let i = 0; i < draws; i++) {
    const mu = (-(p.rating - e.field_avg_rating) / d.meta.rating_pts_per_stroke) * e.rounds;
    const s = mu + d.meta.round_sd * Math.sqrt(e.rounds) * randn();
    const lam = Math.min(e.field_size, e.field_size * PHI(s / e.opp_score_sd));
    const place = 1 + Math.min(poisson(lam), Math.round(e.field_size));
    out[i] = place <= e.curve.length ? e.curve[place - 1] : 0;
  }
  out.sort();
  const q = (f) => out[Math.min(draws - 1, Math.floor(f * draws))];
  let sum = 0;
  for (let i = 0; i < draws; i++) sum += out[i];
  return { mean: sum / draws, p50: q(0.5), p90: q(0.9), max: out[draws - 1] };
}

function detailHtml(p, d) {
  const meta = d.meta;
  const counted = countedTids(p, meta);
  const attOf = new Map(d.events.map((e, i) => [e.tid, p.att[i]]));

  const banked = [...p.banked].sort((a, b) => b.pts - a.pts).map((b) => {
    const drop = !counted.has(b.tid);
    const win = b.place === 1 ? ' <span class="win-medal" title="Event win">🥇</span>' : "";
    return `<tr class="${drop ? "dropped" : ""}">
      <td>${eventLink(b.tid, shortName(b.event))} <span class="chip">${CLS_LABEL[b.cls] || b.cls || "?"}</span>${win}</td>
      <td class="num">${fmtPts(b.pts)}${placeTag(b.place)}</td>
      <td>${drop ? '<span class="drop-tag">dropped</span>' : ""}</td></tr>`;
  }).join("");

  // every remaining event — attended or not — so any can be toggled on
  const live = liveTidSet(d);
  const upcoming = d.events.map((e) => {
    const att = attOf.get(e.tid) ?? 0;
    const s = projectPoints(d, p, e);
    const dflt = att >= 0.5 ? "checked" : "";
    const attTxt = att >= 0.999 ? "yes" : att <= 0.001 ? "—" : Math.round(att * 100) + "%";
    let note = "";
    if (e.tid === meta.dbl_tid) note += ` <span class="note-flag" title="${DOUBLES_NOTE}">⚑ teams TBD</span>`;
    if (e.cls === "playoff") note += ` <span class="note-flag" title="${PLAYOFF_NOTE}">⚑ assumes qualifiers attend</span>`;
    if (live.has(e.tid)) note += ` <span class="live-badge"><span class="live-dot"></span>live</span>`;
    return `<tr class="${att <= 0.001 ? "not-att" : ""}">
      <td><input type="checkbox" class="wf-box" data-tid="${e.tid}" ${dflt}></td>
      <td>${eventLink(e.tid, shortName(e.name))} <span class="chip">${CLS_LABEL[e.cls] || e.cls}</span>${note}</td>
      <td class="num ${att >= 0.999 ? "pos" : ""}">${attTxt}</td>
      <td class="num">${fmtPts(s.mean)}</td>
      <td class="num dim">${fmtPts(s.p50)}</td>
      <td class="num">${fmtPts(s.p90)}</td>
      <td class="num dim">${fmtPts(s.max)}</td></tr>`;
  }).join("");

  return `<div class="detail-grid">
    <div>
      <div class="band">Season so far — best ${meta.top_n_finishes}, top ${meta.majors_counted} majors count (struck through = doesn't count)</div>
      <table class="table-ledger detail-tbl"><thead><tr><th>Event</th><th class="num">Pts (place)</th><th></th></tr></thead>
        <tbody>${banked || '<tr><td colspan="3" class="dim">no results yet</td></tr>'}</tbody></table>
    </div>
    <div>
      <div class="band">What-if — check the events they'll play; projected points if they do</div>
      <table class="table-ledger detail-tbl"><thead><tr>
        <th></th><th>Event</th><th class="num">Plays</th><th class="num">Avg</th><th class="num">Med</th><th class="num">90th</th><th class="num">Ceiling</th>
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

function seasonTotal(othersPts, majorPts, meta) {
  const majors = [...majorPts].sort((a, b) => b - a).slice(0, meta.majors_counted);
  const pool = othersPts.concat(majors).sort((a, b) => b - a);
  let t = 0;
  for (let i = 0; i < Math.min(meta.top_n_finishes, pool.length); i++) t += pool[i];
  return t;
}

function replay(d, p, attendSet) {
  // banked, split once
  const bankedOthers = p.banked.filter((b) => !b.major).map((b) => b.pts);
  const bankedMajors = p.banked.filter((b) => b.major).map((b) => b.pts);
  const events = d.events.filter((e) => attendSet.has(e.tid));
  const n = d.cutline.length;
  // blended cutline ≈ "points of the last spot among OTHER players":
  // if this player was probably inside the cut in the base sim, the true
  // exclusive cutline is closer to the (cut+1)-th total.
  const w = p.p_cut;
  let qualify = 0, sumPts = 0;
  for (let i = 0; i < n; i++) {
    const others = bankedOthers.slice();
    const majors = bankedMajors.slice();
    for (const e of events) {
      const mu = (-(p.rating - e.field_avg_rating) / d.meta.rating_pts_per_stroke) * e.rounds;
      const s = mu + d.meta.round_sd * Math.sqrt(e.rounds) * randn();
      const lam = Math.min(e.field_size, e.field_size * PHI(s / e.opp_score_sd));
      const place = 1 + Math.min(poisson(lam), Math.round(e.field_size));
      const pts = place <= e.curve.length ? e.curve[place - 1] : 0;
      if (e.is_major) majors.push(pts); else others.push(pts);
    }
    const total = seasonTotal(others, majors, d.meta);
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
}

document.querySelectorAll("#division-seg button").forEach((b) =>
  b.addEventListener("click", () => {
    state.div = b.dataset.div;
    document.querySelectorAll("#division-seg button").forEach((x) => x.classList.toggle("active", x === b));
    render();
  })
);

render();
