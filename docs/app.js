/* DGPT Standings Forecast app.
   Three views over per-division JSON bundles; the what-if mode re-simulates
   a single player against frozen per-sim cutlines ("cutline replay"). */
"use strict";

const state = { div: "mpo", view: "forecast", data: {}, whatifPdga: null, sort: { key: "p_cut", dir: "desc" } };

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

function shortName(name) {
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

function forecastCols(meta) {
  return [
    { key: "rank", label: "#", num: true, get: (p) => p.rank, cell: (p) => `<span class="dim">${p.rank}</span>`, dir0: "asc" },
    { key: "name", label: "Player", num: false, get: (p) => p.name.toLowerCase(), cell: playerLink, dir0: "asc" },
    { key: "rating", label: "Rating", num: true, get: (p) => p.rating || 0, cell: (p) => `<span class="dim">${p.rating || ""}</span>`, dir0: "desc" },
    { key: "starts", label: "Starts", num: true, get: (p) => p.banked.length, cell: (p) => `<span class="dim">${p.banked.length}</span>`, dir0: "desc" },
    { key: "points", label: "Points", num: true, get: (p) => p.points, cell: (p) => `<b>${fmtPts(p.points)}</b>`, dir0: "desc" },
    { key: "mean_pts", label: "Proj. pts", num: true, get: (p) => p.mean_pts, cell: (p) => `<span class="dim">${fmtPts(p.mean_pts)}</span>`, dir0: "desc" },
    { key: "mean_rank", label: "Proj. rank", num: true, get: (p) => p.mean_rank, cell: (p) => `<span class="dim">${p.mean_rank.toFixed(1)}</span>`, dir0: "asc" },
    { key: "p_cut", label: "Auto Bid", title: `P(finish top ${meta.cut} in World Standings — automatic Powerball Cup berth)`, num: true, get: (p) => p.p_cut, cell: (p) => `<span class="${probClass(p.p_cut)}">${fmtPct(p.p_cut)}</span>`, dir0: "desc" },
    { key: "p_gmc", label: "GMC", title: `P(top ${meta.gmc_cut} before the Green Mountain Championship — makes the first playoff field)`, num: true, get: (p) => p.p_gmc, cell: (p) => `<span class="${probClass(p.p_gmc)}">${fmtPct(p.p_gmc)}</span>`, dir0: "desc" },
    { key: "p_mvp", label: "MVP", title: `P(top ${meta.mvp_cut} before the MVP Open — makes the second playoff field via points)`, num: true, get: (p) => p.p_mvp, cell: (p) => `<span class="${probClass(p.p_mvp)}">${fmtPct(p.p_mvp)}</span>`, dir0: "desc" },
    { key: "spark", label: "Finish distribution", num: false, sortable: false, cell: (p) => sparkCell(p, meta) },
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
    const cls = [c.num ? "num" : "", c.sortable === false ? "" : "sortable", active ? "sorted" : ""].join(" ").trim();
    return `<th class="${cls}" data-key="${c.key}"${c.title ? ` title="${c.title}"` : ""}>${c.label}${arrow}</th>`;
  }).join("");

  const body = rows.map((p) =>
    `<tr class="expandable" data-pdga="${p.pdga}">` +
    cols.map((c) => `<td class="${c.num ? "num" : ""}${c.key === "spark" ? " sparkcell" : ""}">${c.cell(p)}</td>`).join("") +
    "</tr>"
  ).join("");

  const el = $("#view-forecast");
  el.innerHTML = `<table class="table-ledger" id="forecast-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>
    <p class="dim" style="font-size:.75rem;margin-top:6px">${rows.length} players · click a column to sort · click a row for the event-by-event breakdown and finishing-position odds · hover the sparkline for exact odds.</p>`;

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
  detail.querySelectorAll(".wf-box").forEach((c) => c.addEventListener("change", recompute));
  detail.querySelector("#wf-reset").addEventListener("click", () => {
    detail.querySelectorAll(".wf-box").forEach((c) => {
      const s = p.upcoming[+c.dataset.tid];
      c.checked = !!(s && s.play_freq >= 0.5);
    });
    recompute();
  });
  recompute();
}

// place shown next to a single event's points, small + dim
const placeTag = (place) => (place ? ` <span class="place">${ordinal(place)}</span>` : "");
const DOUBLES_NOTE = "Team pairings aren't modeled yet — points assume a field-average partner. TODO: use announced teams once the full list is out.";

function detailHtml(p, d) {
  const meta = d.meta;
  const counted = countedTids(p, meta);

  const banked = [...p.banked].sort((a, b) => b.pts - a.pts).map((b) => {
    const drop = !counted.has(b.tid);
    return `<tr class="${drop ? "dropped" : ""}">
      <td>${eventLink(b.tid, shortName(b.event))}${b.major ? ' <span class="chip">major</span>' : ""}</td>
      <td class="num">${fmtPts(b.pts)}${placeTag(b.place)}</td>
      <td>${drop ? '<span class="drop-tag">dropped</span>' : '<span class="keep-tag">counts</span>'}</td></tr>`;
  }).join("");

  const upcoming = d.events.filter((e) => p.upcoming[e.tid]).map((e) => {
    const s = p.upcoming[e.tid];
    const dflt = s.play_freq >= 0.5 ? "checked" : "";
    const note = e.tid === meta.dbl_tid ? ` <span class="note-flag" title="${DOUBLES_NOTE}">⚑ teams TBD</span>` : "";
    return `<tr>
      <td><input type="checkbox" class="wf-box" data-tid="${e.tid}" ${dflt}></td>
      <td>${eventLink(e.tid, shortName(e.name))} <span class="chip">${CLS_LABEL[e.cls] || e.cls}</span>${note}</td>
      <td class="num">${Math.round(s.play_freq * 100)}%</td>
      <td class="num">${fmtPts(s.mean)}</td>
      <td class="num dim">${fmtPts(s.p50)}</td>
      <td class="num">${fmtPts(s.p90)}</td>
      <td class="num dim">${fmtPts(s.max)}</td></tr>`;
  }).join("");

  return `<div class="detail-grid">
    <div>
      <div class="band">Season so far — best ${meta.top_n_finishes}, top ${meta.majors_counted} majors count</div>
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
    `Event data © ${d.meta.season} <a href="https://www.pdga.com">PDGA</a> · ` +
    `Player data © ${d.meta.season} <a href="https://www.pdga.com">PDGA</a> · ` +
    `PDGA Authorized Developer`;
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
