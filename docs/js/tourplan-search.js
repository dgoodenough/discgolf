/* The Tour planner's search: pure arithmetic over the tourplan bundle.

   No imports and no DOM, so the same code runs on the page (one home's
   itinerary) and in tourplan-worker.js (the ranking of every home, which at
   generous settings is a second or two of work and must not freeze the tab).

   The question, per home town: the cheapest trip that plays `n` events within
   `weeks` weekends on the road, in one of two styles — "hub" drives home
   after every event, "road" drives event to event and home at the end. The
   difference weeks - n is the trip's budget of weekends off, spent wherever
   the calendar makes it cheapest; weeks = n is back-to-back. */

/* A leg has to fit in the days between one event's last round and the next
   one's first, at this many hours behind the wheel a day. Ten is a long day
   but a normal one for touring pros, and it is the constraint that stops a
   Sunday finish in Florida "connecting" to a Friday start in Washington. */
const DRIVE_HOURS_PER_DAY = 10;
const MIN_LEN = 2;
const TOP_HOMES = 15;

const SET_FILTER = {
  tour: (c) => c !== "a",
  all: () => true,
  atier: (c) => c === "a",
};

const dayNum = (iso) => Date.UTC(...iso.split("-").map((x, i) => (i === 1 ? x - 1 : +x))) / 86400000;

/* The events a reader can enter under the current settings, in weekend
   order, each with its predecessors: earlier events up to `maxDw` weekends
   before it that end before it starts, with the weekend gap `dw` alongside. */
function prepare(b, div, set, maxDw) {
  const keep = SET_FILTER[set];
  const ev = b.events
    .filter((e) => e[div] && keep(e.cat))
    .map((e) => ({ ...e, s: dayNum(e.start), f: dayNum(e.end) }))
    .sort((x, y) => x.wk - y.wk || x.s - y.s);
  for (const e of ev) {
    e.pred = [];
    e.dw = [];
    for (let j = 0; j < ev.length; j++) {
      const p = ev[j], dw = e.wk - p.wk;
      if (dw >= 1 && dw <= maxDw && e.s > p.f) { e.pred.push(j); e.dw.push(dw); }
    }
  }
  return ev;
}

/* Whether a leg from event p to event e fits in the days between them. */
const fits = (min, p, e) => min <= (e.s - p.f) * DRIVE_HOURS_PER_DAY * 60;

/* The most events that fit in `weeks` weekends on the road: a longest path
   through the events where the weekends skipped along the way (a gap of dw
   weekends skips dw - 1) may not exceed weeks - events. Feasibility is a road
   trip's, which a home-base trip can only match or miss — so for Home base
   style this is a ceiling, and the search says so when it can't reach it. */
function maxFit(ev, M, nv, weeks) {
  let best = 0;
  // L[i * S + o]: most events in a chain ending at i having skipped o weekends
  const S = weeks, L = new Int32Array(ev.length * S);
  for (let i = 0; i < ev.length; i++) {
    const e = ev[i];
    for (let o = 0; o < S; o++) {
      let l = o === 0 ? 1 : 0;
      for (let t = 0; t < e.pred.length; t++) {
        const j = e.pred[t], o2 = o - (e.dw[t] - 1);
        if (o2 < 0) continue;
        const lj = L[j * S + o2];
        if (lj && lj + 1 > l && fits(M[ev[j].v * nv + e.v], ev[j], e)) l = lj + 1;
      }
      L[i * S + o] = l;
      if (l && l + o <= weeks && l > best) best = l;
    }
  }
  return best;
}

/* The cheapest trip from home `h` that plays `n` events within `weeks`
   weekends. A dynamic program over (events played, last event, weekends
   skipped so far): the skipped count is what turns "n events" into "n events
   in at most `weeks` weekends", since a trip's span is events + skips.

   A state is dropped when one with fewer skips reaches the same event as
   cheaply — it can do nothing the cheaper one can't — which is what keeps a
   generous weekend budget from multiplying the work by it.

   With `all`, one trip per end event, cheapest first (the itinerary's
   alternatives); without, only the cheapest (the ranking, which asks every
   home). `scratch` is reused across homes so the ranking allocates once. */
function tripsFrom(h, ev, M, nv, n, weeks, style, all = true, scratch = null) {
  const E = ev.length, B = weeks - n, S = B + 1, INF = Infinity;
  if (B < 0 || !E) return [];
  const d = (a, b) => M[a * nv + b];
  const sc = scratch || newScratch(ev, n, S);
  // This home's cost for every predecessor edge, INF where the leg doesn't
  // fit (or skips more weekends than the budget has) — fixed across layers,
  // so computed once rather than n times.
  const { off, pj, skip } = sc.edges;
  const lc = sc.leg;
  for (let i = 0; i < E; i++) {
    const e = ev[i];
    for (let t = off[i]; t < off[i + 1]; t++) {
      const p = ev[pj[t]];
      const c = style === "hub" ? d(p.v, h) + d(h, e.v) : d(p.v, e.v);
      lc[t] = skip[t] <= B && fits(c, p, e) ? c : INF;
    }
  }
  // The live states of the current layer as a compact frontier per event:
  // (skips, cost, slot) for event j at fs[j]..fs[j+1]. Dominance pruning
  // keeps these to one or two entries each, so a transition walks those
  // rather than every possible skip count.
  let cost = sc.a, next = sc.b;
  const { fo, fc, fat, fs } = sc;
  const compact = (arr) => {
    let w = 0;
    for (let j = 0; j < E; j++) {
      fs[j] = w;
      for (let o = 0; o <= B; o++) {
        const c = arr[j * S + o];
        if (c !== INF) { fo[w] = o; fc[w] = c; fat[w] = j * S + o; w++; }
      }
    }
    fs[E] = w;
  };
  cost.fill(INF);
  for (let i = 0; i < E; i++) cost[i * S] = d(h, ev[i].v);
  compact(cost);
  for (let k = 1; k < n; k++) {
    next.fill(INF);
    const par = sc.par[k - 1];
    for (let i = 0; i < E; i++) {
      const base = i * S;
      for (let t = off[i]; t < off[i + 1]; t++) {
        const c = lc[t];
        if (c === INF) continue;
        const j = pj[t], sk = skip[t];
        for (let q = fs[j]; q < fs[j + 1]; q++) {
          const o = fo[q] + sk;
          if (o > B) continue;
          const v = fc[q] + c, at = base + o;
          if (v < next[at]) { next[at] = v; par[at] = fat[q]; }
        }
      }
      // dominance: more skips at no lower cost is never worth keeping
      let lo = next[base];
      for (let o = 1; o <= B; o++) {
        if (next[base + o] >= lo) next[base + o] = INF;
        else lo = next[base + o];
      }
    }
    [cost, next] = [next, cost];
    compact(cost);
  }
  const trace = (at) => {
    const path = [Math.floor(at / S)];
    for (let k = n - 2; k >= 0; k--) { at = sc.par[k][at]; path.unshift(Math.floor(at / S)); }
    return path;
  };
  const out = [];
  let bAt = -1, bt = INF;
  for (let i = 0; i < E; i++) {
    let iAt = -1, it = INF;
    for (let o = 0; o <= B; o++) {
      const c = cost[i * S + o];
      if (c === INF) continue;
      const total = c + d(ev[i].v, h);
      if (total < it) { it = total; iAt = i * S + o; }
    }
    if (iAt < 0) continue;
    if (all) out.push({ total: it, path: trace(iAt) });
    else if (it < bt) { bt = it; bAt = iAt; }
  }
  if (!all) return bAt < 0 ? [] : [{ total: bt, path: trace(bAt) }];
  out.sort((x, y) => x.total - y.total);
  return out;
}

/* Buffers for tripsFrom, sized once per settings change and shared by every
   home: the DP layers, the parent pointers, the predecessor edges flattened
   into typed arrays, and the frontier. */
function newScratch(ev, n, S) {
  const E = ev.length;
  const off = new Int32Array(E + 1);
  for (let i = 0; i < E; i++) off[i + 1] = off[i] + ev[i].pred.length;
  const pj = new Int32Array(off[E]), skip = new Int32Array(off[E]);
  for (let i = 0; i < E; i++) {
    ev[i].pred.forEach((j, t) => { pj[off[i] + t] = j; skip[off[i] + t] = ev[i].dw[t] - 1; });
  }
  const N = E * S;
  return { a: new Float64Array(N), b: new Float64Array(N),
           par: Array.from({ length: Math.max(0, n - 1) }, () => new Int32Array(N)),
           edges: { off, pj, skip }, leg: new Float64Array(off[E]),
           fo: new Int32Array(N), fc: new Float64Array(N), fat: new Int32Array(N), fs: new Int32Array(E + 1) };
}

/* The season's span in weekends for this event set: the most a trip could
   ever be on the road. */
function seasonWeeks(ev) {
  return ev.length ? ev[ev.length - 1].wk - ev[0].wk + 1 : MIN_LEN;
}

/* Clamp a request to what the calendar allows: weeks to the season's span,
   n to the most events that fit in those weeks. Cheap (a few ms), so the page
   runs it before asking the worker and can set its sliders straight away. */
function clampQuery(b, q) {
  const ev = prepare(b, q.div, q.set, 52);
  const span = seasonWeeks(ev);
  const weeks = Math.max(MIN_LEN, Math.min(q.weeks, span));
  const fitMax = maxFit(ev, b.minutes, b.venues.length, weeks);
  const n = Math.max(Math.min(q.n, fitMax, weeks), Math.min(MIN_LEN, fitMax));
  return { ...q, weeks, n, span, fitMax, ev };
}

/* Every home's cheapest trip, best first; the top TOP_HOMES come back. */
function rank(b, q) {
  const c = clampQuery(b, q);
  const { ev, n, weeks } = c;
  const M = b.minutes, nv = b.venues.length;
  const homes = [];
  if (n >= 1 && ev.length) {
    const sc = newScratch(ev, n, weeks - n + 1);
    for (let h = 0; h < nv; h++) {
      const [best] = tripsFrom(h, ev, M, nv, n, weeks, q.style, false, sc);
      if (best) homes.push({ h, total: best.total, path: best.path });
    }
  }
  homes.sort((x, y) => x.total - y.total);
  return { n, weeks, span: c.span, fitMax: c.fitMax, count: homes.length, homes: homes.slice(0, TOP_HOMES) };
}

export { DRIVE_HOURS_PER_DAY, MIN_LEN, clampQuery, dayNum, maxFit, newScratch, prepare, rank, tripsFrom };
