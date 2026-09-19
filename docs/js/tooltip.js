/* Readouts that work without a mouse: the scrub probe and the inline
   explanation marks, both drawn through the one floating `#spark-tip`.

   Imported for its side effects as much as its exports — the document-level
   listeners at the bottom are what make `data-tip` work anywhere on the page,
   so every entry point has to pull this module in. */

import { $ } from "./core.js";

/* ==========================================================================
   READOUTS THAT WORK WITHOUT A MOUSE

   Every explanation on this site used to be either a `title` attribute or a
   `mousemove` handler, which meant that on a phone none of them existed. The
   column definitions, the sparkline odds, the cutline gaps, the race chart's
   standings board — all unreachable, and several panels printed "hover any
   cell for the exact odds", an instruction a touch reader cannot follow.

   Two mechanisms replace that, both drawn through the one floating
   `#spark-tip` element:

   - `probe(el, resolve)` for anything scrubbable — the row sparklines, the
     possibility cloud, the cutline, the leverage grid, the race chart. A mouse
     hovers exactly as before; a finger presses and drags. What makes both work
     is `touch-action: pan-y` on the probe surface (style.css): the browser
     keeps vertical page scrolling for itself and hands us the horizontal
     drags, which is the axis every chart here is read along. A stationary tap
     resolves its own point, which is all the two-dimensional grids need.

   - `data-tip` for inline explanations, via `tipAttrs()`. One delegated
     handler shows the text that used to live only in a `title`. The `title` is
     kept alongside it rather than replaced, so desktop hover and the
     accessible description are unchanged and `data-tip` only adds the tap.

   Two deliberate omissions. Sortable column headers keep a bare `title`,
   because tapping one has to sort — their copy is surfaced by the column guide
   instead. And a tip mark takes no `tabindex`: `title` already gives assistive
   tech the description, and making ~600 marks focusable would bury the row
   expander (the one control keyboard users actually need) in tab stops.
   ========================================================================== */

let tipOwner = null;   // element the visible tip belongs to, or null

/* Place the tip near (x, y) but always fully inside the viewport — it used to
   be pinned to the pointer with no clamping, so a readout near the right edge
   ran off screen. A touch anchor is a fingertip, so the tip goes above it
   rather than under the hand holding the phone. */
function tipAt(x, y, touch) {
  const el = $("#spark-tip");
  el.hidden = false;
  el.style.left = "0px";
  el.style.top = "0px";
  const r = el.getBoundingClientRect(), pad = 6;
  const left = touch ? x - r.width / 2 : x + 14;
  const top = touch ? y - r.height - 20 : y - 8;
  el.style.left = Math.max(pad, Math.min(left, innerWidth - r.width - pad)) + "px";
  el.style.top = Math.max(pad, Math.min(top, innerHeight - r.height - pad)) + "px";
}

function showTip(text, x, y, touch, owner) {
  $("#spark-tip").textContent = text;
  tipOwner = owner || null;
  tipAt(x, y, touch);
}

/* A probe that draws its own cursor on the chart registers the teardown here,
   so it runs however the readout ended — a finger lifting, a tap elsewhere, a
   scroll, Escape — rather than each probe having to guess from raw events. */
const tipHooks = new WeakMap();

function hideTip() {
  const teardown = tipOwner && tipHooks.get(tipOwner);
  $("#spark-tip").hidden = true;
  tipOwner = null;
  if (teardown) teardown();
}

/* Wire one surface for scrubbing. `resolve(clientX, clientY, touch)` returns
   the readout for that point, or null where there is nothing under it. The
   `touch` flag is passed on because a fingertip lands far less precisely than
   a cursor, so a resolver that has to hit a thin mark can widen its catch
   radius instead of reporting nothing. */
function probe(el, resolve, onHide) {
  let pressed = false, viaTouch = false;
  if (onHide) tipHooks.set(el, onHide);
  const clear = () => { if (tipOwner === el) hideTip(); else if (onHide) onHide(); };
  const read = (e, touch) => {
    const text = resolve(e.clientX, e.clientY, touch);
    if (text == null) { clear(); return; }
    viaTouch = touch;
    showTip(text, e.clientX, e.clientY, touch, el);
  };
  el.classList.add("probe");          // carries touch-action: pan-y
  el.addEventListener("pointermove", (e) => {
    if (e.pointerType === "mouse") read(e, false);
    else if (pressed) read(e, true);
  });
  el.addEventListener("pointerdown", (e) => {
    if (e.pointerType === "mouse") return;   // a mouse reads on hover alone
    pressed = true;
    read(e, true);
  });
  el.addEventListener("pointerup", () => { pressed = false; });
  // a vertical pan won the gesture: the reader is scrolling, not reading
  el.addEventListener("pointercancel", () => { pressed = false; clear(); });
  /* Chromium follows every touch tap with compatibility mouse events, ending
     in a `mouseleave` once the finger lifts. Taken at face value that
     dismissed the readout the tap had just produced — the reason no chart on
     this site could be read on a phone even after the pointer handlers above
     were in place. Only a real hover ends on mouseleave. */
  el.addEventListener("mouseleave", () => { if (!viaTouch) clear(); });
  /* A probe inside an expandable row owns its own TAPS, and only its taps.
     Without this, tapping a row sparkline both read it and expanded the row,
     and the expansion moved the mark out from under the finger — firing a
     compatibility `mouseleave` that dismissed the readout the tap had just
     produced. A mouse is left alone: the hover readout is already on screen,
     so a click there can go on meaning "expand this row", as it always did. */
  el.addEventListener("click", (e) => { if (viaTouch) e.stopPropagation(); });
}

/* Inline explanations. Anchored to the mark's own box rather than to the
   pointer, so the tip lands in the same place however it was triggered, and
   tapping the same mark again dismisses it — a mark is a toggle, not something
   you have to tap elsewhere to clear. The event stops here so a tip inside a
   table row does not also expand the row. */
function openTip(mark) {
  if (tipOwner === mark) { hideTip(); return; }
  const r = mark.getBoundingClientRect();
  showTip(mark.dataset.tip, r.left + r.width / 2, r.top, true, mark);
}
document.addEventListener("click", (e) => {
  const mark = e.target.closest("[data-tip]");
  if (!mark) return;
  e.stopPropagation();
  openTip(mark);
});
// A touch tip has no mouseleave to end it: dismiss on the next touch somewhere
// else, on any scroll, and on Escape. Capture phase, so it settles before a
// probe's own pointerdown re-opens one.
document.addEventListener("pointerdown", (e) => {
  if (tipOwner && !tipOwner.contains(e.target)) hideTip();
}, true);
addEventListener("scroll", () => { if (tipOwner) hideTip(); }, { passive: true });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") hideTip(); });

/* The attribute pair an inline explanation needs, emitted from one place: the
   `title` it always had, plus the `data-tip` that makes it tappable. */
const tipAttrs = (text) => {
  const t = String(text).replace(/"/g, "&quot;");
  return `title="${t}" data-tip="${t}"`;
};

export { probe, tipAttrs };
