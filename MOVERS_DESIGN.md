# Movers tabs + odds sparkline — design

Goal: split the Biggest Movers panel into **day / week / month** tabs, each
with a sparkline of a player's Cup odds over that window's horizon (7 days,
7 weeks, whole season). Primary use case: during a tournament, see at a
glance who is over- and under-performing.

Status: design only, nothing implemented. Numbers below are measured against
`main` as of 2026-07-27.

---

## 1. The headline: this is almost entirely a read-side feature

Snapshots already record everything the three windows need. Per player per
date, `predictions/history_{div}.csv` carries `p_champ`, `p_cut`, `p_gmc`,
`p_mvp`, `p_first`, `cur_points`, `cur_rank`, `rating`, `registered`. The
whole feature is derivable from data we have been writing since 2026-07-04.

**No new snapshot fields are required.** What changes is how many windows we
compute, and that we ship a short per-mover series for the chart.

`movers.py`'s structure already generalizes: `_division_movers` picks a
baseline and a latest date via a `newest_on_or_before(cutoff)` helper, then
computes deltas and per-window context (last result, rating delta,
registration changes) *bounded to that window*. Three windows means calling
the same machinery three times with different anchors — not new machinery.

---

## 2. Hard constraints, measured

### 2.1 Snapshot history is 24 days old, not a season

```
19 snapshots, 2026-07-04 .. 2026-07-27
missing: 07-05, 07-06, 07-14, 07-21, 07-22   (5 of 24 calendar days)
```

The season started in February; snapshots began 2026-07-04. **The monthly
tab has one data point today** and a "whole season" sparkline does not exist.

Can we backfill? Partly, and the split matters:

- **Points and rank: yes, exactly.** Standings are a pure function of banked
  event results, and every event carries an end date. We can replay
  `standings.compute` as-of any past date and get the true points/rank series
  back to February.
- **`p_champ`: no, not faithfully.** It requires a 100k sim with the field,
  registrations, and ratings *as they were then*. Registration state was never
  historised, and ratings only began being stored on 2026-07-15
  (`data/current_ratings.json`). Any backfill would be a plausible-looking
  fabrication of a number we never actually published — worse than an honest
  short series.

So the monthly tab is genuinely thin until ~September. See decision D1.

### 2.2 Gaps mean "unchanged" — so forward-fill is exact, not an approximation

`snapshot.record` skips a day only when (a) one was already taken that day, or
(b) the content hash equals the previous block — *predictions unchanged*. A
missing date therefore means the published odds did not move. Forward-filling
a gap reproduces exactly what the site displayed that day.

This is a nice property: the sparkline can plot on a true calendar x-axis with
step/forward-fill and be literally correct, rather than interpolating.

(Caveat: on a day where no refresh ran at all, odds *could* have changed had we
computed them — but we didn't publish a change either, so forward-fill still
matches what a visitor saw.)

### 2.3 The daily tab cannot use today's snapshot — this is the big one

`snapshot.record` is **first-write-wins per calendar day**. During a
tournament the day's snapshot is written by the *first* refresh (often
~00:20Z, before play), so it is stale by exactly the amount the user wants to
see. Comparing yesterday's snapshot to today's snapshot would show a day-old
picture during the event — the opposite of the stated use case.

**Fix: the daily window's latest endpoint is the live bundle, not a snapshot.**
`docs/data/{div}.json` carries current `p_champ` per player and is regenerated
every ~5 minutes during play. So:

```
daily:   baseline = newest snapshot with date < today
         latest   = current p_champ from the live bundle
```

This makes the daily tab update every refresh cycle during a tournament, needs
no change to snapshot cadence, and sidesteps the frozen-snapshot failure mode
(HARDENING item 8) rather than depending on its fix.

Weekly and monthly stay snapshot-to-snapshot; they want stable endpoints.

---

## 3. Window anchoring

All three reuse one primitive — newest snapshot on-or-before a cutoff —
which `movers.py` already implements.

| Tab | Baseline | Latest | Rolls over |
|---|---|---|---|
| Day | newest snapshot before today | **live bundle** `p_champ` | continuously |
| Week | newest snapshot ≤ previous Monday | newest snapshot ≤ this Monday | Mondays |
| Month | newest snapshot ≤ 1st of previous month | newest snapshot ≤ 1st of this month | 1st |

Weekly is today's behaviour, unchanged. Each keeps the existing early-season
degradation rule: if the anchors can't be satisfied, fall back to the widest
span available rather than rendering an empty panel.

**Per-window thresholds.** `MIN_DELTA` is a flat 2% today, tuned for a week.
Daily moves are smaller and monthly larger; one threshold would leave the
daily tab empty on quiet days and the monthly tab noisy. Proposal: rank by
`|delta|`, take top 12, with a per-window floor (day 0.5%, week 2%, month 3%)
so a genuinely quiet window shows few rows or none rather than noise.

---

## 4. What gets saved, and where it's computed

**Recommendation: keep computation server-side in `movers.py`; embed a short
series per mover.**

`docs/data/movers.json` becomes:

```jsonc
{
  "mpo": {
    "day":   { "baseline": "...", "latest": "...", "movers": [ … ] },
    "week":  { … },
    "month": { … }
  },
  "fpo": { … }
}
```

Each mover keeps today's fields (`champ_from/to`, `delta`, `rank_from/to`,
`last_result`, `rating_delta`, `reg_added/removed`) plus:

```jsonc
"spark": { "dates": ["2026-07-21", …], "p": [0.42, 0.44, …] }
```

`dates` is shared per window, so hoist it to the window object rather than
repeating it per player.

**Size.** `movers.json` is 0.6 KB today. Three windows × 2 divisions × 12
movers, each with ≤ 10 floats, lands around **20–30 KB** — negligible beside
`mpo.json` at 906 KB. No new file, no new fetch.

### Why not a full per-player timeseries file?

The flexible alternative is emitting `history.json` (all ~679 players ×
all dates) and computing windows client-side. That would additionally enable
sparklines on *any* expanded row, not just the 12 movers.

Cost: ~77 KB today, growing to ~600 KB by season end unless downsampled
(daily for 14 days, weekly beyond, monthly beyond that). That is a second
~big fetch for a feature nobody asked for yet.

**Recommendation: defer it.** Embed per-mover series now; if "show me any
player's odds arc" becomes desirable, add the timeseries file then — the
snapshot data supports it whenever we want, so nothing is foreclosed.

---

## 5. Sparkline rendering

Reuse the visual language of the existing `sparkCell` (inline SVG, `viewBox`
with `preserveAspectRatio="none"`, classed `<path>`s styled from
`tokens.css`) — but it is a different chart. `sparkCell` draws a *distribution*
as bars; this is a *time series*, so: a filled area or single polyline, a
baseline rule at the window's starting value, and the last point marked.

Specifics:

- **X axis is calendar time**, not snapshot index — otherwise a 3-day gap
  looks like one step and the shape lies. Forward-fill gaps (§2.2).
- **Y axis fixed to 0–100%**, not auto-scaled per player. Auto-scaling makes a
  2% wobble look like a collapse, and these are read side-by-side down a
  column, so they must be mutually comparable.
- **Colour by direction** using the existing `movers-up` / `movers-down`
  tokens, matching the row's arrow.
- **Leading nulls, not zeros**, for players absent from early snapshots (row
  counts run 465–665, so the player set genuinely grows). A player who did not
  exist in the model is not the same as a player at 0%; start the line where
  their data starts.
- Hover/`<title>` giving date + exact percentage, consistent with the existing
  sparkline's tooltip behaviour.

---

## 6. UI

Tabs inside the existing `<details class="movers">`, styled like the existing
`#cols-seg` segmented control (that pattern already exists in
`renderForecast`). Active tab in `state` so it survives re-render, same as
`state.colsMode`.

Column set is identical across tabs; only the window changes. Header line
becomes e.g. "Biggest movers — Cup odds since 7/26" / "since 7/20" / "since
July 1", driven by the window's baseline.

---

## 7. Staging

The regression suite (`tests/`, gated on every PR by
`.github/workflows/tests.yml`) already covers `movers` via `test_smoke.py`
and `conftest.py`, so each stage below lands with tests rather than a
one-shot verification. Snapshot history is a plain CSV — a synthetic
multi-week history is a cheap fixture, no captured payload needed.

1. **`movers.py`: parameterise the window.** Extract anchor selection into a
   function taking (baseline_cutoff, latest_cutoff) and emit `week` only,
   under the new nested shape. Client reads `movers[div].week`. Pure refactor
   — assert byte-identical mover lists against today's output.
2. **Add `day` and `month` windows**, including the live-bundle latest for
   `day` and per-window floors. Backend only; test with a synthetic history
   fixture covering the anchoring rules, the gap/forward-fill semantics, and
   the early-season degradation path.
3. **Client tabs**, defaulting to `week` so current behaviour is preserved.
4. **Sparkline**: series into the payload, renderer in `app.js`.
5. *(optional, later)* per-player timeseries file + sparklines on expanded rows.

Stages 1–2 are backend-only and land without touching the site; 3–4 are
client-only. Each is independently revertible.

One sequencing note: stage 2's daily window reads current `p_champ` from the
bundle, which `movers.write_movers` already loads via `_context`. So the live
endpoint needs no new plumbing — `_context` returns it alongside the existing
completed/gated sets.

---

## 8. Decisions needed

- **D1 — Monthly tab, given ~1 month of history.** Options: (a) ship it and
  let it fill in, labelled honestly; (b) hide the tab until ≥3 months exist;
  (c) make the monthly/season view plot **rank or points** instead of odds,
  which *is* exactly reconstructable back to February (§2.1). (c) gives a real
  season-long chart immediately at the cost of the season view showing a
  different metric than the other two tabs.
- **D2 — Intra-day granularity.** The most useful tournament view might be
  *round-to-round* rather than day-to-day ("who moved during round 3"). That
  needs a new capture — snapshots are daily by design — e.g. an append-only
  intra-event series written during live refresh. Real feature, real cost;
  out of scope here unless wanted.
- **D3 — Threshold tuning.** The day/week/month floors above (0.5% / 2% / 3%)
  are guesses. Worth checking against the actual distribution of daily deltas
  before shipping.
