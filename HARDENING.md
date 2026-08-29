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

9. **A fifth live-API shape variant — and the first one a check caught.**
   `FinalRound` is not a round count: where a round is labelled "Finals" it
   is that round's ID. Ledgestone 2026 reported `FinalRound: 12` with
   `RoundsList {1, 2, 3, 12: "Finals"}`, so the remaining-holes model read
   ~11 rounds left for the whole field and flattened the live odds
   (fixed in `live_api._round_plan`). The first fix then over-corrected by
   excluding the finals id as a shootout, making it a 3-round event when
   Ledgestone plays 4 — the "Finals" sheet carries the full field over 18
   holes on its own layout. Worth noting the checks did not catch that
   second error: rem=2 is as plausible to a bounds test as rem=3, and it
   took the owner knowing the tournament. Structural facts that no
   invariant can infer are exactly where a fixture pinned from a real
   payload earns its keep. Two things worked as designed this
   time: the item-3 rem-bounds invariant flagged it within a refresh
   instead of it being noticed by eye days later, and the publish-first
   posture meant the site kept updating while the run went red. Item 1's
   corpus is why the fix ships with the variant pinned as a test rather
   than a probe workflow. This is the pattern of incident 1 continuing —
   the variants are not exhausted — but the detection loop now works.

10. **The live window closed mid-final-round; the site froze overnight on
    the second-to-last hole.** `live_events` was purely date-based
    (`start <= today <= end`, UTC). Ledgestone ended Sunday 2026-08-02; a
    6pm CDT finish is 23:00Z, and at 00:00Z Monday the loop declared
    nothing live and exited while the lead card was still on the course.
    Last capture 23:17Z: the eventual winner at 23% with one hole to
    play, served all night — nothing recomputes between the loop's exit
    and the Monday 11:00Z cron. This was a known, flagged risk ("Sunday
    overrun") that was left unfixed until it bit. Fix: a one-day grace
    window past `end_date`, open only until a refresh banks the event
    (`completed=True` in the committed schedule closes it). The grace
    period exposed a second, worse latent bug: banking is also
    date-based, and `final_results` caches sheets permanently once the
    end date passes — a refresh triggered at 00:05Z mid-final-round
    would have frozen partial results as the event's permanent record.
    So during the grace night (before 06:00Z) the scoreboard outranks
    the calendar: `event_complete` gets the banking veto, with the
    date-based answer as the fallback after 06:00Z or on API error, so a
    player abandoned mid-round without a WD marker can only delay
    banking a few hours, never past the Monday cron. Lesson twice over:
    every place the pipeline consults the calendar about live play is a
    UTC-rollover bug waiting for a US Sunday finish, and the second one
    (permanent caching keyed on date) was sitting behind the first.

11. **The loop yielded at the job ceiling and nothing took over.** The
    live loop holds a runner ~5.5h and exits under GitHub's 6h per-job
    ceiling; continuation depended on the `*/15` cron having left a
    queued successor in the shared concurrency group — described in the
    workflow as something the coarse cron did "reliably". On 2026-08-27,
    round 2 of Worlds, it had not. Run #1019 yielded at 20:01Z
    announcing "the queued successor takes over" and nothing did. GitHub
    delivered **zero** scheduled fires for the workflow between 14:30Z
    and 23:17Z, when it was restarted by hand — including the 3h11m
    after the yield, when the concurrency slot was completely free, so a
    congested queue does not explain it. Corroboration that the
    scheduler rather than the queue was at fault: `refresh.yml`'s
    `0 11 * * *` cron fired that day at 20:57Z, ~10 hours late. That
    late run is also the only reason the site was 2h15m stale instead of
    3h16m — the once-a-day job happened to land in the hole. An earlier
    gap the same day went unnoticed entirely: #1018 ended 08:52Z, #1019
    started 14:30Z, 5h38m uncovered. Nothing in the pipeline was broken;
    the loop, the gate, the publish path and the invariants were all
    healthy, which is why no run went red and nobody was notified.
    Lesson: "best effort" in GitHub's cron documentation means the
    delivery rate can go to zero for hours, so anything load-bearing
    built on "a fire will arrive within this window" is a scheduled
    outage rather than a design — and a liveness failure that produces
    no failing run produces no alert either.

12. **The publish-gate alert took the live loop down, and could not
    de-duplicate.** Two PDGA rows at Worlds carried impossible cumulative
    scores on 2026-08-29 — MPO Sander Bahnerth `cur=+981` and, two hours
    later, FPO Samantha Zaborowski `cur=+849`, both far outside the
    per-round bounds. The checks did exactly their job: published the
    data, then failed the run so the owner was notified. What nobody had
    noticed is what that costs. `exit 1` ends the run, and the run is the
    live loop, so each bad row also stopped the site updating — and the
    de-dupe meant to keep a persistent violation from re-alerting could
    never fire, because its marker lived in `data/cache/`, which is
    gitignored and carried between runs only by `actions/cache`, whose
    save step is skipped when the job ends non-zero. The alerting run is
    always the failing run, so the marker written by the run that alerts
    was precisely the one guaranteed not to survive. Every restart
    re-alerted and died again. Round 4 ran on hourly updates (16:10,
    17:26, 18:38Z) instead of six-minute ones, the 17:26 refresh being the
    daily cron rather than the loop. Fix: item 10's hand-off extended to
    this exit, and the marker moved into tracked state so it is committed
    with the rest of the cross-run state before the run dies. Lesson: an
    alerting path that shares a process with the thing it monitors will
    take that thing down with it, and state that only exists on the
    success path cannot protect the failure path.

## The plan

Ordered by expected payoff. "In-season safe" = additive, can't change a
published number.

*Items 1-5 shipped mid-season (2026-07): the fixture corpus + suite live in
`tests/` (gated by `.github/workflows/tests.yml`), the flight recorder in
`dgpt/live_api.py`, the invariant checks in `dgpt/invariants.py`, and the
failure posture in `.github/workflows/live-refresh.yml`. Item 6 shipped
2026-08 (`.github/publish-site.sh`). Items 7 and 9 are offseason work;
item 8's first two pieces (a resnapshot command, the invariant gate on
recording) are in-season safe and still open. Items 10 and 11 shipped
2026-08 (`.github/dispatch-successor.sh`, `.github/invariant-alert.sh`).*

### 1. Commit the regression corpus; make it a test suite *(shipped 2026-07)*

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

### 2. Flight recorder: archive raw payloads during live refresh *(shipped 2026-07)*

Every one-shot probe existed because the payload that caused a wrong
number was gone by the time we investigated. During live refresh, write
each fetched round sheet to the results cache (they're small JSON; the
cache already persists across runs) keyed by event/division/round/time,
keeping the last N snapshots per sheet. Any future "why did it say
that?" becomes a local replay instead of a CI probe. Bonus: this is the
per-round WD-rate capture MODEL_IDEAS' variance work wants anyway.

### 3. Publish-gate invariants at ingest *(flag-only version shipped 2026-07)*

The Buhr inversion was detectable at ingest: our reconstructed totals
disagreed with the sheet's own `RunningPlace` ordering. Check a small
set of invariants each refresh — reconstructed leaderboard order vs
`RunningPlace`, no unknown round ids, per-round scores within sane
bounds, field size vs registration sanity — and on violation, warn
loudly (fail the run, keep the data commit, same posture as validate).
Offseason, consider promoting hard violations to "hold this event's
live projection and show a banner" rather than publishing known-suspect
odds.

### 4. Make the live loop fail loudly *(shipped 2026-07)*

Wrap the refresh call in the loop so an exception marks the run failed
(notifying the owner) instead of scrolling past; keep looping on
transient errors but fail after N consecutive. Add a staleness
watchdog to the same loop: if an event is live and the published
`generated` timestamp is older than ~30 minutes, that's a failure even
if nothing threw. Cheap, and it converts every future silent-stale
incident into a push notification.

### 5. CI gate on pull requests *(shipped 2026-07)*

There is currently no workflow that runs on PRs — the first execution
of merged code is a production refresh. Add a PR workflow running the
item-1 suite. And retire the reused `claude/standings-attendance-bug-*`
branch: fifteen PRs of unrelated fixes shipped under a branch name
describing a bug fixed in April. Fresh branch per change.

### 6. Move data commits off main *(shipped 2026-08)*

Generated output no longer lands on main. `.github/publish-site.sh`
builds a tree from `docs/` and force-pushes it to the `site` branch as
a single **parentless** commit, so the published payload has exactly one
version at any time and accumulates no history. Pages serves `site`
/docs.

One consequence worth knowing before it confuses someone: **the static
pages now go live on the next publish, not on merge.** Pages used to
rebuild from any commit touching `docs/` on main, so a copy fix appeared
as soon as it landed. It now rides along with the data bundle, so it
waits for the 11:00 UTC refresh or a live-refresh iteration.
`.github/workflows/publish-docs.yml` (dispatch-only) exists for when
that wait is wrong: it republishes `docs/` in under a minute with no pip
and no simulation, carrying the currently-published bundle across
untouched. `publish-site.sh` refuses to run if that bundle is absent,
because it force-pushes and a fresh checkout of main has the pages but
no `docs/data` — publishing that would blank the live site.

Bootstrapping is ordered: the branch does not exist until the publish
step has run once, and the Pages settings dropdown will not offer a
branch that does not exist. So run *Refresh forecast* first (the
force-push creates the ref — nothing is created by hand), then point
Settings → Pages at `site` /docs. Between those two steps Pages keeps
serving main/docs, so the site freezes rather than breaking, which is
the failure mode most likely to go unnoticed.

The split follows the "inputs as well as outputs" caution above, which
is what makes this in-season safe rather than offseason work:

| | |
|---|---|
| Published to `site`, gitignored on main | `docs/data/*.json` |
| Untracked, local build products | `results/`, `data/standings_*.csv` |
| Still committed to main (state the pipeline reads back) | `data/live_signature.txt`, `data/current_ratings.json`, `data/schedule_2026.csv`, `predictions/history_*.csv` |

Nothing reads `results/` or `data/standings_*.csv` back — they are
written and never opened again (`simulate.write_csv`,
`standings.write_csv`), so untracking them loses no input. The
prediction history stays on main: `evaluate` and `movers` both read it,
README advertises it, and it is appended at most once per calendar day
rather than once per refresh.

Before: ~1.6MB of JSON rewritten on main every ~6 minutes during play,
48 of the last 50 commits machine output. After: a live iteration that
only moves scores makes **no commit to main at all**.

Remaining: `data/current_ratings.json` is 200KB on one line and is
rewritten whenever any rating moves. That is rare (`refresh_if_stale`
writes only on an actual change) so it was left alone, but it is the
next-largest thing on main if it ever gets chattier.

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

### 10. Hand the live loop off explicitly *(shipped 2026-08)*

`.github/dispatch-successor.sh`, after incident 11. Before yielding at
the ~5.5h budget the run dispatches its own successor and polls until
that run actually exists, re-dispatching once and failing the run if it
cannot confirm one. The verification is the substance, not politeness: a
204 from the dispatch endpoint is not a run, as the 2026-08-26 Actions
incident showed, and a hand-off that reports success with nothing
enqueued reproduces the outage exactly. `workflow_dispatch` (with
`repository_dispatch`) is the documented exception to the rule that
GITHUB_TOKEN-triggered events start no workflow run, so this needs no
PAT — only `actions: write`.

The dispatch happens *before* the current run exits, so the successor
lands in the concurrency group as the pending run and starts the moment
the slot frees: the shape the cron was supposed to produce, minus the
dependency on delivery. A crash-out gets one automatic retry, marked
`after_failure=true`, and a run carrying that flag does not chain again
— transient breaks self-heal in a minute, persistent ones cannot spin
runners.

What still rides on the cron is *starting* the loop when an event goes
live. That is the more forgiving window — the loop exits in seconds when
nothing is live, so a late start costs the opening minutes of a round
rather than hours mid-round — but it is the same dependency, and a
multi-hour delivery gap across a Sunday-morning tee time would show. The
fix if it bites: keep looping while a points event starts within the
next several hours, instead of exiting on "nothing live right now".

### 11. Stop the invariant alert from killing the loop *(shipped 2026-08)*

`.github/invariant-alert.sh`, after incident 12. The alert decision moved
out of the workflow body and ahead of the state commit, so the de-dupe
marker (`data/invariant_alerted.txt`, tracked — not `data/cache/`, which
no failing run can save) is committed before the run exits. The publish
still happens first and the run still goes red; what changed is that the
loop hands off to a successor on the way out, and that successor reads
the committed marker, sees the same violation set, and stays quiet
instead of dying on it.

The semantics are per episode, not per season: a clean run clears the
marker, so a row that breaks, gets fixed, and breaks again is reported
both times. The hand-off here is deliberately unbounded — unlike the
crash-out retry, a repeat cannot re-alert, so it cannot chain.

Not addressed: `refresh.yml`'s own invariant step has no de-dupe and will
still go red once a day while a violation persists. That is a daily job
rather than the liveness path, so a red run there is the intended signal
and costs nothing but noise — but if a violation ever persists for a week
it will be a week of red dailies.
