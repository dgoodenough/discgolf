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

## The plan

Ordered by expected payoff. "In-season safe" = additive, can't change a
published number.

*Items 1-5 shipped mid-season (2026-07): the fixture corpus + suite live in
`tests/` (gated by `.github/workflows/tests.yml`), the flight recorder in
`dgpt/live_api.py`, the invariant checks in `dgpt/invariants.py`, and the
failure posture in `.github/workflows/live-refresh.yml`. Item 6 shipped
2026-08 (`.github/publish-site.sh`). Items 7 and 9 are offseason work;
item 8's first two pieces (a resnapshot command, the invariant gate on
recording) are in-season safe and still open. Item 10's audit can be done
any time; acting on it is offseason.*

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

Left over from the 2026-08 dead-code sweep (`live_api.live_state` and the
orphaned `.hist` / `.drop-tag` / `.keep-tag` rules went then; these are
judgement calls, not obviously dead):

- `meta.top_n_finishes` ships in every bundle and the app never reads it —
  it reads `count_dgpt`, `count_playoff` and `majors_counted` separately.
  Drop it if nothing outside this repo consumes the bundle.
- `simulate.write_csv` and `standings.write_csv` run on every refresh and
  write to gitignored paths (`results/`, `data/standings_*.csv`) that
  nothing reads back (item 6 established this). They are local-inspection
  artefacts; either say so in a docstring or gate them behind a flag.
- `MULTIPLIERS["jomez"] = 0.0` still carries a "scale TBD
  (reverse-engineer from standings)" comment, but Jomez was solved and pays
  from flat bands in `points.jomez_bonus`. The `0.0` now survives only as a
  skip gate that `standings.event_points` has to special-case around with
  `and row["cls"] != "jomez"`. Untangling it is behaviour-touching, so
  offseason — but the comment is actively misleading today.
- `p_first` is simulated, exported and snapshotted but never rendered. That
  is deliberate (it is in `snapshot._PRED_KEYS` for end-of-season grading),
  and worth a one-line comment in `export.py` so the next reader doesn't
  delete it.

### 10. Audit whether the PDGA API carries what we scrape *(audit is in-season safe; switching sources is offseason)*

Three HTML scrapes feed the pipeline. Two of them feed the **model**:

| Scrape | Code | Feeds |
|---|---|---|
| `pdga.com/tour/event/{tid}` | `live_api.page_registrants` / `_fetch_page` | Playoff (GMC, MVP) and Worlds play-in signup lists; `registered_roster`'s fallback when PDGA Live is dark |
| `discgolfscene.com/…/registration` | `live_api.doubles_teams` | Doubles Championship team pairings |
| `statmando.com/rankings/dgpt` | `validate.py` | **Validation only — keep it.** It never touches a published number, and item 7 already covers reducing reliance on it. Not in scope here. |

The task is to walk the REST API service list
(pdga.com/dev/api/rest/v1/services) **exhaustively**, not just the three
services `pdga_api.py` happens to call today (`event`, `players`,
`player-statistics`), and establish endpoint by endpoint whether either of
the first two scrapes can be retired. Specifically:

- **Is there an entrant/registration service?** Or an expansion parameter on
  `event` that returns one? This is the big one — `page_registrants` is the
  *only* source for a qualification-gated field until a TD stages the event
  for live scoring.
- **Does anything expose doubles partners?** A team/partner field on an
  entrant record would retire the DGS scrape outright.
- **Does `event` carry registration-wave opening times?** Those are
  hand-transcribed into `config.REG_PHASES` from the event pages, and
  `fields._waves_all_open` decides whether a signup list is the field or a
  floor off them.
- **Does `players` carry a country?** Out of scope for the scrapes, but the
  same audit answers it: `data/player_countries.csv` (627 rows) and both
  `tourcard_2026_*.csv` are hand-curated with no writer anywhere in the repo,
  and they drive the European cohort prior in `fields.participation_rates`.
  If `players` carries nationality, that file becomes generated rather than
  maintained.

**Availability is not the bar — timing is.** The event-page scrape does not
exist because the data is unavailable; it exists because signups are public
*months* before PDGA Live knows the event exists, which is the whole reason
`registration_list` has two tiers and a `staged` flag. An endpoint that only
populates when the TD stages the event is no better than `_live_roster` and
replaces nothing. So for every candidate, check *when it starts returning
rows*, not just whether it does. Same for doubles: PDGA Live's `Teammates`
already wins once populated, and the DGS scrape covers exactly the window
before that.

**Why it is worth the hour even if the answer is no.** Both scrapes fail
*closed and silent*. `page_registrants` returns an empty set on any
exception, a missing page marker, or a parse under `MIN_PAGE_REGISTRANTS`;
every caller reads that as "no list published yet" and falls back to the
pure standings gate. Failing closed is the right direction, but it means a
markup change silently reverts the playoff fields to the pre-signup model
with nothing going red. The DGS parse degrades the same way, into solo teams
with a field-average partner — which is what the contaminated 2026-07-27
snapshot in item 8 looked like downstream (58 doubles registrations instead
of 106, every team a solo).

So regardless of what the audit finds, pair it with an item-3 invariant:
once a signup list has been seen non-empty for an event, a later refresh
that reads it as empty while the event is still upcoming is a violation, not
a state. Cheap, additive, and it turns both scrapes from silent-degrade into
loud-fail.
