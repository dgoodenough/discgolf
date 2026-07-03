/* DGPT Standings Forecast app.
   Three views over per-division JSON bundles; the what-if mode re-simulates
   a single player against frozen per-sim cutlines ("cutline replay"). */
"use strict";

const state = { div: "mpo", view: "projections", data: {}, whatifPdga: null };

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

function shortName(name) {
  return name
    .replace(/^DGPT( Playoffs)?( -)? /, "")
    .replace(/^DGPT\+ /, "")
    .replace(/ presented by .*| Presented by .*| powered by .*| by MVP.*/i, "")
    .replace(/^2026 PDGA /, "")
    .replace(/^DGPT JomezPro( -)? /, "Jomez: ");
}

function histHtml(p, meta) {
  const cut = meta.cut;
  const hist = p.hist;
  const max = Math.max(...hist, 1e-9);
  const show = hist.length; // last bucket = "50+"
  let cols = "", labels = "";
  for (let k = 0; k < show; k++) {
    const h = Math.max(1, Math.round((hist[k] / max) * 80));
    const cls = k + 1 <= cut ? "in-cut" : k === show - 1 ? "overflow" : "";
    const pct = (hist[k] * 100).toFixed(1);
    cols += `<div class="col ${cls}" style="height:${h}px" title="${k + 1 === show ? show + "+" : "#" + (k + 1)}: ${pct}%"></div>`;
    labels += `<span>${(k + 1) % 10 === 0 ? k + 1 : ""}</span>`;
  }
  return `<div class="hist">${cols}</div><div class="hist-labels">${labels}</div>
    <p class="dim" style="font-size:.75rem">P(finishing position); green = inside the top-${cut} cut, red bucket = ${show}th or worse.</p>`;
}

/* ---------- projections view ---------- */

function renderProjections(d) {
  const cut = d.meta.cut, fs = d.meta.field_size;
  const rows = [...d.players]
    .filter((p) => p.p_field >= 0.0005 || p.rank <= fs)
    .sort((a, b) => b.p_cut - a.p_cut || b.p_field - a.p_field || b.points - a.points);
  let html = `<table class="table-ledger"><thead><tr>
    <th class="num">#</th><th>Player</th><th class="num">Points</th>
    <th class="num">Proj. pts</th><th class="num">Proj. rank</th>
    <th class="num">P(top ${cut})</th><th></th><th class="num">P(top ${fs})</th>
    </tr></thead><tbody>`;
  rows.forEach((p) => {
    const bubble = p.p_cut < 0.99 && p.p_cut >= 0.02;
    html += `<tr class="expandable" data-pdga="${p.pdga}">
      <td class="num dim">${p.rank}</td><td>${p.name}</td>
      <td class="num">${fmtPts(p.points)}</td>
      <td class="num dim">${fmtPts(p.mean_pts)}</td>
      <td class="num dim">${p.mean_rank.toFixed(1)}</td>
      <td class="num ${probClass(p.p_cut)}">${fmtPct(p.p_cut)}</td>
      <td class="barcell"><div class="bar"><span class="${bubble ? "pend" : ""}" style="width:${Math.round(p.p_cut * 100)}%"></span></div></td>
      <td class="num ${probClass(p.p_field)}">${fmtPct(p.p_field)}</td></tr>`;
  });
  html += "</tbody></table>";
  const el = $("#view-projections");
  el.innerHTML = html;
  el.querySelectorAll("tr.expandable").forEach((tr) =>
    tr.addEventListener("click", () => toggleDetail(tr, d, "hist"))
  );
}

/* ---------- standings view ---------- */

function renderStandings(d) {
  const rows = [...d.players].sort((a, b) => a.rank - b.rank).filter((p) => p.points > 0);
  let html = `<table class="table-ledger"><thead><tr>
    <th class="num">Rank</th><th>Player</th><th class="num">Rating</th>
    <th class="num">Starts</th><th class="num">Points</th></tr></thead><tbody>`;
  for (const p of rows) {
    html += `<tr class="expandable" data-pdga="${p.pdga}">
      <td class="num">${p.rank}</td><td>${p.name}</td>
      <td class="num dim">${p.rating ?? ""}</td>
      <td class="num dim">${p.banked.length}</td>
      <td class="num"><b>${fmtPts(p.points)}</b></td></tr>`;
  }
  html += "</tbody></table>";
  const el = $("#view-standings");
  el.innerHTML = html;
  el.querySelectorAll("tr.expandable").forEach((tr) =>
    tr.addEventListener("click", () => toggleDetail(tr, d, "banked"))
  );
}

function toggleDetail(tr, d, kind) {
  const next = tr.nextElementSibling;
  if (next && next.classList.contains("detail")) { next.remove(); return; }
  tr.parentElement.querySelectorAll("tr.detail").forEach((x) => x.remove());
  const p = d.players.find((x) => x.pdga === +tr.dataset.pdga);
  const detail = document.createElement("tr");
  detail.className = "detail";
  const td = document.createElement("td");
  td.colSpan = tr.children.length;
  if (kind === "hist") {
    td.innerHTML = histHtml(p, d.meta);
  } else {
    const ev = [...p.banked].sort((a, b) => b.pts - a.pts)
      .map((b) => `<tr><td>${shortName(b.event)}${b.major ? ' <span class="chip">major</span>' : ""}</td>
                   <td class="num">${fmtPts(b.pts)}</td></tr>`).join("");
    td.innerHTML = `<table class="table-ledger" style="max-width:460px"><tbody>${ev}</tbody></table>
      <p class="dim" style="font-size:.75rem">Counting rules: best ${d.meta.top_n_finishes} finishes, top ${d.meta.majors_counted} majors.</p>`;
  }
  detail.appendChild(td);
  tr.after(detail);
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

function renderWhatif(d) {
  const list = $("#whatif-list");
  const players = [...d.players].filter((p) => p.rating).sort((a, b) => b.p_cut - a.p_cut || b.points - a.points).slice(0, 120);
  const q = ($("#whatif-search").value || "").toLowerCase();
  list.innerHTML = players
    .filter((p) => p.name.toLowerCase().includes(q))
    .map((p) => `<li data-pdga="${p.pdga}" class="${p.pdga === state.whatifPdga ? "active" : ""}">
        <span>${p.name}</span><span class="dim">${fmtPct(p.p_cut)}</span></li>`)
    .join("");
  list.querySelectorAll("li").forEach((li) =>
    li.addEventListener("click", () => { state.whatifPdga = +li.dataset.pdga; renderWhatif(d); })
  );
  if (state.whatifPdga) renderWhatifDetail(d, d.players.find((p) => p.pdga === state.whatifPdga));
}

function renderWhatifDetail(d, p) {
  const el = $("#whatif-detail");
  const idxOf = new Map(d.events.map((e, i) => [e.tid, i]));
  const checks = d.events
    .map((e) => {
      const att = p.att[idxOf.get(e.tid)];
      const known = att === 0 || att === 1;
      const checked = att >= 0.5 ? "checked" : "";
      const clsLabel = { elite: "DGPT", elite_plus: "DGPT+", playoff: "playoff", major: "major", doubles: "doubles", jomez: "jomez" }[e.cls] || e.cls;
      return `<label class="event-check">
        <input type="checkbox" data-tid="${e.tid}" ${checked}>
        <span class="ev-date">${e.start_date.slice(5)}</span>
        <span class="ev-name">${shortName(e.name)}</span>
        <span class="chip">${clsLabel}</span>
        <span class="ev-att ${known ? (att ? "reg" : "noreg") : ""}">${known ? (att ? "registered" : "not registered") : "model: " + Math.round(att * 100) + "%"}</span>
      </label>`;
    })
    .join("");
  el.innerHTML = `
    <h2 style="margin:0 0 2px">${p.name} <span class="dim" style="font-size:.85rem">#${p.rank} · ${fmtPts(p.points)} pts · ${p.rating} rated</span></h2>
    <div class="statrow">
      <div><div class="stat">${fmtPct(p.p_cut)}</div><div class="stat-label">model P(top ${d.meta.cut})</div></div>
      <div><div class="stat" id="wf-scenario">–</div><div class="stat-label">scenario P(top ${d.meta.cut})</div></div>
      <div><div class="stat" id="wf-delta">–</div><div class="stat-label">swing</div></div>
      <div><div class="stat" id="wf-pts">–</div><div class="stat-label">scenario mean pts</div></div>
    </div>
    <div id="wf-checks">${checks}</div>
    <p class="hint" style="margin-top:10px">
      <button class="btn" id="wf-reset">reset to model</button>
      &nbsp;Checkboxes start at the model's best guess (registered field or participation ≥50%).
      Scenario odds condition on exactly the checked events.</p>`;
  const recompute = () => {
    const set = new Set([...el.querySelectorAll("input:checked")].map((c) => +c.dataset.tid));
    const t0 = performance.now();
    const r = replay(d, p, set);
    const ms = (performance.now() - t0).toFixed(0);
    const delta = r.pCut - p.p_cut;
    $("#wf-scenario").textContent = fmtPct(r.pCut);
    const dEl = $("#wf-delta");
    dEl.textContent = (delta >= 0 ? "+" : "−") + fmtPct(Math.abs(delta)).replace("<", "").replace(">", "");
    dEl.className = "stat " + (Math.abs(delta) < 0.005 ? "" : delta > 0 ? "pos" : "neg");
    $("#wf-pts").textContent = fmtPts(r.meanPts);
    el.querySelector(".hint").title = `replay: ${ms}ms over ${d.cutline.length.toLocaleString()} cutlines`;
  };
  el.querySelectorAll("input[type=checkbox]").forEach((c) => c.addEventListener("change", recompute));
  $("#wf-reset").addEventListener("click", () => renderWhatifDetail(d, p));
  recompute();
}

/* ---------- shell ---------- */

async function render() {
  const d = await loadDiv(state.div);
  $("#meta-line").textContent =
    `updated ${d.meta.generated.slice(0, 10)} · ${d.meta.n_sims.toLocaleString()} sims · ` +
    `top ${d.meta.cut} qualify directly, field of ${d.meta.field_size}`;
  $("#view-projections").hidden = state.view !== "projections";
  $("#view-standings").hidden = state.view !== "standings";
  $("#view-whatif").hidden = state.view !== "whatif";
  if (state.view === "projections") renderProjections(d);
  if (state.view === "standings") renderStandings(d);
  if (state.view === "whatif") renderWhatif(d);
}

document.querySelectorAll("#division-seg button").forEach((b) =>
  b.addEventListener("click", () => {
    state.div = b.dataset.div;
    state.whatifPdga = null;
    document.querySelectorAll("#division-seg button").forEach((x) => x.classList.toggle("active", x === b));
    render();
  })
);
document.querySelectorAll("#view-seg button").forEach((b) =>
  b.addEventListener("click", () => {
    state.view = b.dataset.view;
    document.querySelectorAll("#view-seg button").forEach((x) => x.classList.toggle("active", x === b));
    render();
  })
);
$("#whatif-search").addEventListener("input", () => loadDiv(state.div).then(renderWhatif));

render();
