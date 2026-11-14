/* The shared render layer: the pieces more than one view draws with.

   Conditional formatting, the two row sparklines, player and event links, the
   name shortener, the counting-cap arithmetic, and the live-event window. A
   helper belongs here when a second view needs it — `monthDay` and
   `pendingWaves` sit here only because `playoffNote` does. */

import { fmtPct, fmtStroke, ordinal } from "./core.js";
import { probe, tipAttrs } from "./tooltip.js";

/* Holes played at a live event, straight from the feed.

   Do NOT go back to deriving it as (e.rounds - live.rem) * 18. Those two
   numbers are measured against different round counts: `rem` comes from the
   event's real round list, while `e.rounds` is the model's per-class constant
   (3, or 4 for a major). Ledgestone plays four rounds as an elite_plus event,
   so the derivation lands a whole round low there — and since the callers read
   thru <= 0 as "hasn't teed off", a wrong value costs the score too.
   Older bundles predate `thru`; fall back to the derivation for those. */
const liveThru = (e, l) =>
  Math.max(0, Math.round(l.thru != null ? l.thru : (e.rounds - l.rem) * 18));


/* ---------- shared bits ---------- */

/* Ledger conditional formatting: green = effectively in, yellow = live bubble,
   dimmed = long shot. Callers put this on the number itself, which style.css
   colours; put it on a <td> instead and tokens.css fills the whole cell. */
function probClass(p) {
  if (p >= 0.99) return "pos";
  if (p >= 0.02) return "pend";
  return "dim";
}

/* Playoff-field cell. A signed-up player is a fact, not a forecast — mark it
   so 100% here reads differently from a 100% the standings produced. */
function fieldCell(prob, signedUp, eventName) {
  if (signedUp) return `<b class="pos" ${tipAttrs(`Signed up for the ${eventName} — in the field regardless of where the standings finish`)}>✓ in</b>`;
  return `<span class="${probClass(prob)}">${fmtPct(prob)}</span>`;
}

const monthDay = (iso) => {
  const t = new Date(iso);
  return isNaN(t) ? "" : `${t.getMonth() + 1}/${t.getDate()}`;
};

/* Registration waves that haven't opened yet — the reason an unsigned player
   still has playoff-field odds instead of a zero. */
function pendingWaves(m, keys) {
  const now = Date.now();
  const out = [];
  for (const [key, label] of [["gmc", "GMC"], ["mvp", "MVP"]]) {
    if (!keys.includes(key)) continue;
    for (const ph of ((m.reg_phases || {})[key] || [])) {
      if (Date.parse(ph.opens) <= now) continue;
      // A `perf` wave is won, not invited: the top finishers at the previous
      // playoff event take the last spots, so it reads as a result, not a rank.
      if (ph.perf) out.push(`${label}'s GMC-performance wave (${ph.perf} spots)`);
      else if (ph.top) out.push(`${label} top ${ph.top} on ${monthDay(ph.opens)}`);
    }
  }
  return out;
}

/* What the playoff and Worlds fields currently rest on. Signups where a list
   exists, the qualification gate for everyone the remaining waves haven't
   reached — and, at Worlds, the play-in for the players with no other way in. */
function playoffNote(m) {
  const evs = [
    { key: "gmc", label: "GMC", n: m.gmc_signups || 0, done: !!m.gmc_field_set },
    { key: "mvp", label: "the MVP Open", n: m.mvp_signups || 0, done: !!m.mvp_field_set },
  ];
  const playin = !m.playin_entrants ? "" :
    ` <b>Worlds:</b> ${m.playin_entrants} player${m.playin_entrants === 1 ? "" : "s"} in this table
      missed direct qualification and can only get in by winning one of the ${m.playin_spots}
      spots at the one-round play-in.`;
  const listed = evs.filter((e) => e.n);
  if (!listed.length) {
    return `<b>Playoff assumption:</b> no GMC or MVP Open signup list has been published yet,
      so both fields come from the qualification gate alone and assume every qualifier attends.${playin}`;
  }
  const said = listed.map((e) => e.done
    ? `${e.label}'s field is final at <b>${e.n}</b>`
    : `<b>${e.n}</b> have entered ${e.label}`).join(", and ");
  const open = evs.filter((e) => !e.done);
  const waves = pendingWaves(m, open.map((e) => e.key));
  const tail = !open.length ? "" :
    ` Everyone else still qualifies for ${open.map((e) => e.label).join(" and ")} on points${
      waves.length ? `, because ${waves.join(" and ")} ${waves.length > 1 ? "have" : "has"} yet to open` : ""
    }.`;
  return `<b>Playoff fields:</b> ${said} — read literally, so those players are in whatever
    the standings do.${tail}${playin}`;
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

/* which banked events count toward the season total under the 2026 per-class
   caps (best N of each class; Jomez bonus always counts). The rest are
   "dropped" and shown struck through. */
const POOL_BY_CLS = { elite: "dgpt", elite_plus: "dgpt", doubles: "dgpt", playoff: "playoff", major: "major", jomez: "jomez" };
function countedTids(p, meta) {
  const pools = { dgpt: [], playoff: [], major: [], jomez: [] };
  for (const b of p.banked) {
    const pool = POOL_BY_CLS[b.cls] || "dgpt";
    pools[pool].push(b);
  }
  const counted = new Set(pools.jomez.map((b) => b.tid)); // all Jomez count
  const keepBest = (arr, n) => arr.slice().sort((a, b) => b.pts - a.pts).slice(0, n).forEach((b) => counted.add(b.tid));
  keepBest(pools.dgpt, meta.count_dgpt);
  keepBest(pools.playoff, meta.count_playoff);
  keepBest(pools.major, meta.majors_counted);
  return counted;
}

/* small inline sparkline of the finishing-rank distribution; hover shows the
   exact place + probability immediately via a shared floating tooltip.
   Drawn as 3 SVG paths (in-cut / beyond / overflow) instead of ~50 bar
   divs — cuts the table from ~28k spark nodes to ~3 per row. */
const sparkStore = new Map(); // pdga -> hist array
function sparkCell(p, meta) {
  sparkStore.set(p.pdga, p.hist);
  const H = 22, W = 120, n = p.hist.length;
  const max = Math.max(...p.hist, 1e-9);
  const bw = W / n, gap = bw * 0.15;
  const d = { in: "", out: "", over: "" };
  for (let k = 0; k < n; k++) {
    const h = Math.max(1, (p.hist[k] / max) * H);
    const key = k + 1 <= meta.cut ? "in" : k === n - 1 ? "over" : "out";
    d[key] += `M${(k * bw).toFixed(1)} ${H}h${(bw - gap).toFixed(1)}v-${h.toFixed(1)}h-${(bw - gap).toFixed(1)}z`;
  }
  return `<div class="spark" data-pdga="${p.pdga}"><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
    <path class="sp-in" d="${d.in}"/><path class="sp-out" d="${d.out}"/><path class="sp-over" d="${d.over}"/></svg></div>`;
}


/* Companion to sparkCell: the score a player would tee off the Cup on, which
   is their finishing position run through the seed ladder. Same 3-path build,
   but the buckets are scores rather than places, and the last one is the
   seasons they never reach the field — drawn in the miss colour so a bubble
   player's row reads as "how good a start, and how often no start at all". */
const strokesStore = new Map(); // pdga -> {values, hist}
function strokesCell(p, meta) {
  const values = meta.start_strokes.values, hist = p.strokes || [];
  if (!hist.length) return "";
  strokesStore.set(p.pdga, { values, hist });
  const H = 22, W = 120, n = hist.length;
  const max = Math.max(...hist, 1e-9);
  const bw = W / n, gap = bw * 0.15;
  const d = { in: "", out: "", over: "" };
  for (let k = 0; k < n; k++) {
    const h = Math.max(1, (hist[k] / max) * H);
    const key = k === n - 1 ? "over" : values[k] < 0 ? "in" : "out";
    d[key] += `M${(k * bw).toFixed(1)} ${H}h${(bw - gap).toFixed(1)}v-${h.toFixed(1)}h-${(bw - gap).toFixed(1)}z`;
  }
  return `<div class="sspark" data-pdga="${p.pdga}"><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
    <path class="sp-in" d="${d.in}"/><path class="sp-out" d="${d.out}"/><path class="sp-over" d="${d.over}"/></svg></div>`;
}

/* ---------- forecast view (standings + projections, sortable) ---------- */

// events in progress today (client-side date, so it's current between refreshes)
/* Which events are in progress is decided in one place — schedule.live_events()
   — and shipped on each schedule row as `live`. Do not re-derive it here.

   Comparing start/end against a UTC date, as this used to, is the same window
   without the grace day: a US Sunday final round finishes after 00:00 UTC, so
   the page called the event over while the pipeline was still tracking it and
   scores were still moving. That bug existed in three copies of this window
   (schedule.py, livecheck.py, here) and bit in all three.

   Bundles published before the flag existed fall back to the old comparison. */
function liveEvents(d) {
  const sched = d.schedule || [];
  if (sched.some((e) => "live" in e)) return sched.filter((e) => e.live);
  const today = new Date().toISOString().slice(0, 10);
  return sched.filter((e) => e.start <= today && today <= e.end);
}
function liveTidSet(d) {
  return new Set(liveEvents(d).map((e) => e.tid));
}

const hasWin = (p) => p.banked.some((b) => b.place === 1);
function nameCell(p) {
  return playerLink(p) + (hasWin(p) ? ` <span class="medal" ${tipAttrs("Won a points event this year — event winners who miss the cut get a special Championship invite as a bottom seed")}>🥇</span>` : "");
}

/* Both row sparklines hover the same way — bucket under the pointer, label it,
   float the shared tooltip — and differ only in what a bucket means, so the
   readout is the one thing each caller supplies. */
function wireSparkTips(el, meta) {
  const wire = (sel, read) => el.querySelectorAll(sel).forEach((sp) => {
    const src = read(+sp.dataset.pdga);
    if (!src) return;
    probe(sp, (x) => {
      const r = sp.getBoundingClientRect();
      const k = Math.min(src.n - 1, Math.max(0, Math.floor(((x - r.left) / r.width) * src.n)));
      return src.label(k);
    });
  });
  wire(".spark", (pdga) => {
    const hist = sparkStore.get(pdga);
    return hist && { n: hist.length, label: (k) =>
      `${k + 1 === hist.length ? `${hist.length}th+` : ordinal(k + 1)}: ${(hist[k] * 100).toFixed(1)}%` };
  });
  wire(".sspark", (pdga) => {
    const st = strokesStore.get(pdga);
    return st && { n: st.hist.length, label: (k) =>
      `${k === st.hist.length - 1 ? "misses the Cup" : `starts at ${fmtStroke(st.values[k])}`}: ${(st.hist[k] * 100).toFixed(1)}%` };
  });
}

// place shown next to a single event's points, small + dim
const placeTag = (place) => (place ? ` <span class="place">${ordinal(place)}</span>` : "");

export { CLS_LABEL, countedTids, eventLink, fieldCell, liveEvents, liveThru, liveTidSet, nameCell, placeTag, playerLink, playoffNote, POOL_BY_CLS, probClass, shortName, sparkCell, strokesCell, wireSparkTips };
