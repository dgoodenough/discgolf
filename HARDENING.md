# Hardening — offseason engineering backlog

Companion to [MODEL_IDEAS.md](MODEL_IDEAS.md). That file is model levers
(changes that alter published odds); this one is engineering: making the
pipeline harder to break, faster to diagnose, and louder when it fails.
Same freeze rule applies loosely — anything touching the publish path waits
for the offseason — but several items here are purely additive (capture,
alerting, tests) and are safe to land mid-season. Each item says which.

## What this season's incidents actually were

Every fix below shipped *after* the site published something wrong. The
pattern across them, not any single bug, is what this backlog addresses.

1. **PDGA live-API shape variants, discovered one incident at a time.**
   Four separate live-scoring fixes: not-yet-started players missing from
   the latest sheet (08d1e53), events that populate only `RoundtoPar`
   (6e99dd4), fields built from the latest sheet instead of all sheets
   (5094f16), and `ToPar` not being comparable across sheets at
   multi-layout events (b4cfb56 — the model had the eventual winner at
   100% to finish 2nd). Each variant was real, none was known in advance,
   and each was diagnosed live.
2. **Debugging requires committing one-shot probe workflows.** Seven
   probe-and-remove cycles this season (Jomez finals, USWDGC sheet state,
   StatMando FPO page, ratings payload, …) because live payloads can't be
   reproduced locally after the fact.
3. **Fix verification is per-session and discarded.** Fixes get
   mock-tested against captured payloads in the session that writes them,
   then the mocks are thrown away. The `simulate.run` shadowing crash
   (c287e19) shipped exactly this way: the mock suite for the neighboring
   change exercised `standings.compute` but never entered `simulate.run`,
   and the first post-merge refresh crashed.
4. **Failures inside the live loop are silent.** `live-refresh.yml` runs
   without `set -e`; if `dgpt.refresh` or the change-check throws, the
   loop just continues and the run stays green. A persistent crash during
   play means the site quietly serves stale odds until someone notices.
   (The weekly workflow got this right after 2026-07-13 — validate now
   fails the run *after* the deploy — but the live loop has no equivalent.)
5. **StatMando is a single, fragile validation source.** Mid-update empty
   tables (57a9a98, c3c6dbf), regex-parsed HTML, weekly cadence only — so
   engine drift mid-week has no independent check.
6. **Registration-diff semantics produced false alarms twice.** Completed
   events shown as dropped registrations (0d5b05e), standings-gated
   playoff fields shown as churn (7b94e9a). Both were display-layer
   interpretations of correct data.
7. **The repo is drowning in machine commits.** ~690 of 825 commits are
   live-refresh data commits; the pack is 46 MiB and grows all season.
8. **A snapshot written mid-incident becomes canonical for a week.**
   `snapshot.record` keeps at most one snapshot per calendar day, first
   write wins. On 2026-07-27 that write landed at 00:20:31Z — nine
   minutes *before* the doubles pairing fix (dfc802b) — and froze the
   broken roster (58 doubles registrations instead of 106, every team a
   solo). The corrected refresh nine minutes later could not replace it,
   and because the movers panel anchors both endpoints to Mondays, the
   7/20→7/27 window would have served false "dropped registration" rows
   until Aug 3. Correcting it meant hand-editing committed prediction
   history (1f355ec). Note the two failure modes are independent: a
   first-write-wins record, and a weekly panel that reads one specific
   day. The 7/24–7/26 snapshots still carry the contaminated roster —
   they feed no panel, but they do feed end-of-season grading.

## The plan

Ordered by expected payoff. "In-season safe" = additive, can't change a
published number.

### 1. Commit the regression corpus; make it a test suite *(in-season safe to start)*

The captured payloads that verified this season's fixes (Jomez
multi-layout, USWDGC `RoundtoPar`-only, weather suspension, Heinola R1,
not-yet-started rows, withdrawal 999s) are the most valuable diagnostic
asset the project has produced, and none of them are in the repo. Add
`tests/fixtures/` with those payloads and a pytest suite over
`live_api.live_field` / `final_results` asserting the known-correct
leaderboards. Add one end-to-end smoke test that runs the full
`refresh` path — `simulate.run` included — against fixture data with a
tiny sim count. The smoke test alone would have caught c287e19 before
merge; the fixtures make every future live-API fix start from "add the
failing payload" instead of "write a probe workflow."

### 2. Flight recorder: archive raw payloads during live refresh *(in-season safe)*

Every one-shot probe existed because the payload that caused a wrong
number was gone by the time we investigated. During live refresh, write
each fetched round sheet to the results cache (they're small JSON; the
cache already persists across runs) keyed by event/division/round/time,
keeping the last N snapshots per sheet. Any future "why did it say
that?" becomes a local replay instead of a CI probe. Bonus: this is the
per-round WD-rate capture MODEL_IDEAS' variance work wants anyway.

### 3. Publish-gate invariants at ingest *(flag-only version in-season safe)*

The Buhr inversion was detectable at ingest: our reconstructed totals
disagreed with the sheet's own `RunningPlace` ordering. Check a small
set of invariants each refresh — reconstructed leaderboard order vs
`RunningPlace`, no unknown round ids, per-round scores within sane
bounds, field size vs registration sanity — and on violation, warn
loudly (fail the run, keep the data commit, same posture as validate).
Offseason, consider promoting hard violations to "hold this event's
live projection and show a banner" rather than publishing known-suspect
odds.

### 4. Make the live loop fail loudly *(in-season safe)*

Wrap the refresh call in the loop so an exception marks the run failed
(notifying the owner) instead of scrolling past; keep looping on
transient errors but fail after N consecutive. Add a staleness
watchdog to the same loop: if an event is live and the published
`generated` timestamp is older than ~30 minutes, that's a failure even
if nothing threw. Cheap, and it converts every future silent-stale
incident into a push notification.

### 5. CI gate on pull requests *(process; immediate)*

There is currently no workflow that runs on PRs — the first execution
of merged code is a production refresh. Add a PR workflow running the
item-1 suite. And retire the reused `claude/standings-attendance-bug-*`
branch: fifteen PRs of unrelated fixes shipped under a branch name
describing a bug fixed in April. Fresh branch per change.

### 6. Move data commits off main *(offseason)*

Publish generated data (`docs/data`, `data/`, `results/`,
`predictions/`) from the refresh workflows via `actions/deploy-pages`
artifact deploys, or to a dedicated `data` branch, so refreshes stop
creating commits on main entirely. Main's history becomes readable
again, clones shrink, and the push/rebase races between the live loop
and merges disappear. Needs care with the pieces that are inputs as
well as outputs (`live_signature.txt`, prediction history), which is
why it waits for the offseason.

### 7. Reduce single-source validation risk *(offseason)*

Keep StatMando, but add a second, structural check that doesn't depend
on their publishing cadence: recompute each banked event's points from
the cached final sheet and diff against the standings delta (catches
engine drift per-event, immediately after banking, with no external
fetch). Run validate's cross-check per-event as well as weekly.

### 8. Make daily snapshots correctable *(in-season safe)*

Nothing today can revise a snapshot once written, so a bad one is
load-bearing until the next calendar day — and for whatever the movers
panel anchors to, a bad *Monday* is load-bearing for a week. Three
pieces, cheapest first:

- Add `python -m dgpt.refresh --resnapshot` to replace the current
  day's block instead of skipping it, so a correction is a dispatch
  rather than a hand-edit of `predictions/*.csv`.
- Gate recording on the item-3 invariants: if ingest checks fail, skip
  the snapshot and say so, rather than freezing a known-bad state as
  the day of record. A missing day is far cheaper than a wrong one.
- Consider last-write-wins per day, so a day converges on its final
  state instead of its first. The objection is commit churn — a ~650-row
  block rewritten every live refresh — which item 6 defuses by moving
  data commits off main; sequence it after that.

Also worth a flag in `evaluate`: grading currently trusts every
snapshot equally, including ones taken while the pipeline was known
broken.

### 9. Small hygiene *(cheap, offseason)*

- Pin `requirements.txt` (it's just unpinned `numpy`) and pin action
  majors to SHAs.
- The results cache saves a new cache entry every run
  (`…-${{ github.run_id }}`); with per-run growth all season it churns
  against the 10 GiB eviction limit. Save under a stable weekly key
  instead.
- `validate.py`'s HTML parsing is regex over `<tr>`; if item 7 lands,
  a parse failure downgrades from "blind spot" to "lost redundancy,"
  which is the right severity for it.
