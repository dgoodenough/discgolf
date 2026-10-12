# Offseason plan — 2026-27

Written 2026-10-12, the week before the Powerball Cup (Oct 15-18). The 2027
opener is the Greater Atlanta Open on **2027-02-26**
(`data/schedule_2027.csv`). That leaves about 18 weeks, and the last two of
them are a freeze.

This file pulls everything we said would "wait for the offseason" into one
place, adds the four new projects, and puts it all in order. The other notes
stay the record of *why* each item exists. This file is the *when* and *who*.

**Who does what.** You are the decision-maker on the model (workstream A).
Everything else is built to run on automation: Claude sessions open small PRs,
CI gates them, and you review only at the checkpoints marked **◆ you**.

---

## 0. Before the Cup (this week, in-season safe)

Two things get lost if nobody acts before the offseason starts:

- **Save the raw data somewhere durable.** The cached rounds behind
  `calibrate`, and the flight recorder, both live in the Actions cache
  (`pdga-cache-2026-*`). GitHub evicts a cache that nobody reads for 7 days.
  Once the daily refresh goes quiet, the whole model-fitting corpus is one
  quiet week from gone. Completed events can be fetched again from PDGA, but
  the flight-recorder snapshots of live play cannot. Right after the Cup,
  upload `data/cache/` as a release asset (or a long-retention artifact).
- **Get the live win-probability history out of git.** `data/live_odds.csv`
  keeps only the newest two tournaments, so the series for every earlier
  event survives only in old commits. A script that walks `git log -p` on
  that file and writes `predictions/live_odds_2026.csv` makes it gradeable.
  It is the largest grading sample we have (see A1).

---

## The inventory: everything marked "offseason"

| # | Item | Source | Workstream |
|---|---|---|---|
| 1 | Rating-dependent round variance | MODEL_IDEAS §1 | A |
| 2 | Rating drift over the forecast horizon (OU fit), plus a dated ratings archive | MODEL_IDEAS §2 | A |
| 3 | Course-type / conditions variance multiplier ("course fit") | MODEL_IDEAS §1 spin-off, UX §7 | A |
| 4 | Per-round WD-rate / data-quality capture, so the variance fit can see what's missing | MODEL_IDEAS §1 | A |
| 5 | Linear rating-to-strokes map: test whether it should curve | UX §3 (Kwame) | A |
| 6 | Zero-sum error in the what-if (a winner takes points off the cutline) | UX Q1 | A |
| 7 | Model the USDGC's real qualification (blocked on the 2027 qualifier list) | MODEL_IDEAS backlog | A |
| 8 | EuroTour card displacement (low payoff, high coupling) | MODEL_IDEAS backlog | A (park) |
| 9 | Grade the season: Brier and calibration, plus flags for known-bad snapshots | README, HARDENING 8 | A |
| 10 | Hard invariant violation → hold that event's projection and show a banner | HARDENING 3 | B |
| 11 | Second validation source: recompute points per banked event from the cached sheet | HARDENING 7 | E |
| 12 | Correctable snapshots (`--resnapshot`, gate on invariants, last-write-wins) | HARDENING 8 | E |
| 13 | Hygiene: pin `requirements.txt` and action SHAs, a weekly cache key, a clock-shifted test job, MVP phase gated on `completed` | HARDENING 9 | E |
| 14 | Live loop can't start its own successor without a PAT | HARDENING incident 11 | D |
| 15 | A realistic FPO test fixture (FPO's sim path has only edge-case coverage) | PR #40 | E |
| 16 | Preseason: a zero-player division raises, so an empty table fails the daily refresh | PR #40 | F |
| 17 | Movers "why" decomposition: is it worth the compute? | MOVERS_DESIGN 9b | C |
| 18 | Per-player odds timeseries file (sparklines on any row) | MOVERS_DESIGN §5, PR #24 | C |
| 19 | Intra-day / round-to-round movers granularity | PR #24 D2 | B |
| 20 | UX recs 2-10: search + contenders default, sticky column instead of hidden sparklines, split bundle, service worker, state in URL + player cards, public scorecard, leverage in the row, live tab by default + biggest-hole swings, light mode | UX short list | C |
| 21 | Open questions: is FPO first-class (Q3)? On a phone, fan or pro (Q4)? Glossary vs inline acronyms (Q5) | UX | C ◆ you |
| 22 | Cup "strokes in hand" movers story (the column shipped; the story didn't) | MOVERS_DESIGN 9c | C |

New this round:

| # | Item | Workstream |
|---|---|---|
| N1 | Live scoring at hole and shot level, tested on any PDGA event | B |
| N2 | Page auto-updates without a refresh | C/D |
| N3 | Far fewer words on the page | C |
| N4 | Model improvement review: what the scorecard says, what to change | A ◆ you |
| N5 | When does GitHub stop being enough? | D |
| N6 | **Season rollover to 2027.** Not on your list, but it has a hard deadline: Feb 26 | F |

---

## Workstream A — the model ◆ you, throughout

The rule from MODEL_IDEAS still holds. Nothing ships until it is graded, and
every change has a materiality gate. What has been missing is the means to
grade a change *before* shipping it. So build the scorecard first and the
backtester second, and only then pull levers.

### A1. Grade 2026 (weeks 1-2, automated build, then your read)

- Build `actuals.csv` from the final standings and playoff fields. The Cup
  winner fills `won_cup`.
- Extend `evaluate.py`:
  - Add **log loss** next to Brier.
  - Compare against **baselines**: current-standings rank only, and rating
    only. Raw Brier means nothing until it beats something.
  - Grade **by `events_completed`**, so we can see how fast the forecast
    sharpened.
  - Exclude or flag the contaminated 2026-07-24..26 snapshots
    (HARDENING incident 8).
- **Grade at event level, not just season level.** About 40 Cup outcomes per
  division is a thin sample. Every event's win/podium probabilities, the
  live series from step 0, and finishing places against pre-event
  projections give thousands of graded predictions. This is where model
  changes will show up.
- Add a per-tier PIT table to `calibrate.py`. MODEL_IDEAS §1 asks for it.

Known limits to state up front: season snapshots start 2026-07-04, after 13
events, so the early-season call can't be graded. Live series exist only from
2026-08-29.

**◆ Checkpoint A1:** read the scorecard and pick **the objective**: which
metric and which predictions every later change is judged on. Suggestion:
event-level log loss on pre-event win/top-10 odds, with season Brier as a
guardrail.

### A2. A backtest harness (weeks 2-4, automated)

Add `python -m dgpt.backtest --model <variant>`. For each 2026 event, it
fits on rounds strictly before the event, predicts the event, and scores the
prediction against what happened. Model variants are config objects, not
code forks. Every lever below becomes one variant plus one run, scored on
the A1 objective, with run-to-run noise reported next to it.

**◆ Checkpoint A2:** approve what is held out and how (leave-one-event-out
vs rolling origin). Every result after this one leans on that choice.

### A3. Levers (weeks 4-12, each is a short report, you decide ship / no-ship)

In rough order of expected payoff:

1. **Variance by rating** (§1). The first look already clears the gate
   (≈0.8 strokes, about 20%). Fit a linear form (MPO) and a step form
   (FPO). Check the WD-truncation confound with item 4's capture
   (back-filled from cached sheets).
2. **Within-event form correlation.** `ROUND_SD` 4.2 is about 3.65 of
   round noise plus a correlated part. Fit that split per tier while doing
   (1). It decides how quickly live odds react.
3. **Rating drift** (§2). OU fit per division. Needs the dated ratings
   archive, so start it in week 1. Also check whether the PDGA API exposes
   rating history. If it does, the archive can be back-filled instead of
   waiting. Grade on both the 2027 tab and the 2026 live calibration.
4. **Course / conditions.** Hole `Length` and `Par` are already in every
   round payload. Fit a per-event variance multiplier against course length
   and par mix. This is also the "course fit" story readers asked for.
5. **Curved rating-to-strokes.** Test whether a spline or a tiered slope
   beats one linear slope per division.
6. **Attendance model.** New. Grade the participation probabilities against
   who actually registered. Field errors move the Cup race as much as
   score errors do, and this has never been checked.
7. **What-if zero-sum error.** Measure it against a full re-sim for about 10
   players. Fix it only if a bubble player's error is above noise.
8. USDGC qualification: once the 2027 qualifier list is out. EuroTour
   displacement: stays parked.

**◆ Checkpoint A3:** one decision per lever. The report for each is a page:
effect on the objective, noise floor, complexity added, and a recommendation.

### A4. Lock and publish (weeks 12-15)

Refit constants on the full 2026 corpus with the levers you shipped. Then
build the public **scorecard page** (UX rec 7): a reliability curve, Brier
vs baseline, and "when we said 40%". **Model freeze: 2027-02-12.**

---

## Workstream B — live scoring: holes, shots, any PDGA event (automated)

**What we have.** PDGA live scoring already gives per-hole strokes
(`HoleScores`), per-hole par, and hole `Length` for every player. The live
model uses none of it per hole. It turns "holes left" into fractional rounds
(`rem = holes/18`) and draws `ROUND_SD·√rem`. So hole-level live scoring is
a model change on data we already fetch.

**Shots are a different question.** No PDGA payload carries throw-level data.
Shot data (lies, putt distances, C1/C2, OB) comes from other providers, and
as far as I know none of them has a public API. So shots start as a spike,
not a build.

| Step | What | Gate |
|---|---|---|
| B1 | **Spike, week 1-2:** list the possible shot-data sources, what they carry, their terms, and whether any offers developer access. Also decode `PlayerThrowStatus`, which already appears in the PDGA payload. | ◆ you: pursue a source, or drop shots and go hole-level only |
| B2 | **Run on any event.** Pull the live engine out of the DGPT schedule: `python -m dgpt.live --event <tid>` runs the live model on any PDGA event, writes a bundle, never touches standings. | tests on the existing fixtures |
| B3 | **Replay harness.** Feed a stored event's flight-recorder snapshots, or its finished sheets cut hole by hole, through B2 as if live. Every event in the cache becomes a test. | |
| B4 | **Hole-level model.** Draw the remaining holes using each hole's difficulty: field scoring average so far, with par/length as the prior. A player with three easy holes left is no longer modeled like one with three hard ones. Grade it on B3 replays against the current model, using A1's event-level score. | ◆ you (it's a model change, so A's rules apply) |
| B5 | **Shadow mode in the offseason.** PDGA events run all winter. A scheduled job runs B2 on 1-3 live events each weekend, publishes nothing, and logs graded predictions. This is the test bed you asked for, and it catches new payload shapes before February. | |
| B6 | **Shot level**, only if B1 found a usable source: ingest it, then add it to the live state (lie → strokes-to-hole-out). | ◆ you |
| B7 | Movers within a day / round to round (#19), on top of B's series. | |

**Live scoring for *every* PDGA event** is where it stops being cheap. A
weekend can have hundreds of events. Polling each one every 5 minutes means
several requests per second to PDGA, all the time. That is a question for
PDGA's developer program before it is a question about our infrastructure,
and it is the clearest trigger for leaving GitHub (D). Plan: build B2-B5 so
"any event" works, run it on a short allow-list, and decide on "all events"
after asking PDGA.

---

## Workstream C — the page: fewer words, auto-update, the UX backlog (automated, two ◆)

### C1. Copy (weeks 2-8)

Measured on the live bundle on 2026-10-12, desktop. This is visible prose
outside tables and charts, so it's a rough count:

| Tab | words | First screen |
|---|---|---|
| Forecast | ~690 | ~1,020 words in the first 900px, table included (455 on a phone) |
| Event odds | ~800 | |
| What if | ~760 | |
| EuroTour | ~840 | |
| 2027 | ~550 | |
| how-it-works.html | ~6,600 | |

Plan:
- **Set budgets and enforce them in CI.** A Playwright test fails any PR that
  pushes a tab over its budget. Proposed: ≤120 words of prose per tab above
  the table/chart, and ≤25 words per panel note.
- **One explanation, one place.** Caveats and method go to
  `how-it-works.html` (itself cut to about 3,000 words) and to the column
  guide. A tab keeps a single sentence and a link.
- Inline acronym expansion on first use (Q5). Then rewrite tab by tab, one
  PR each, with before/after screenshots.

**◆ Checkpoint C1:** approve the budgets and one rewritten tab as the house
style. The other tabs then follow that example without needing you.

### C2. Auto-update (weeks 4-8)

GitHub Pages can do this, but not by simply re-fetching the bundle:

- Publish a tiny `data/version.json` (generated timestamp plus a hash for
  each bundle). The page polls it every 60s, but only while the tab is
  visible and an event is live, and backs off to 10 minutes otherwise. It
  re-fetches only bundles whose hash changed, then re-renders in place:
  scroll position, open rows and scrub position all stay put.
- **Split the bundle first** (UX rec 4). It is about 1 MB today. At a change
  every ~5 minutes, a reader watching a 4-hour round would pull about 50 MB.
  With the split, it's a few MB.
- Service worker with stale-while-revalidate (UX rec 5), so the page also
  works with bad signal at the course.

### C3. The rest of the UX backlog (weeks 6-14)

Ordered: search + contenders by default (rec 2), sticky Player column (3),
live tab by default during play + biggest-hole swings (9), state in the URL
+ build-time player cards (6), leverage in the row (8), light mode (10),
"strokes in hand" story (#22), per-player timeseries (#18), then movers
"why" (#17: build the cheap driver tag; drop the full decomposition unless
A2's harness makes it nearly free).

**◆ Checkpoint C3:** answer Q3 (FPO first-class?) and Q4 (phone: fan or
pro?). Recs 2 and 8 wait on those two answers.

---

## Workstream D — "do we need a real website?" (a decision, mostly not a build)

**Short answer: not for 2027, if two cheap changes land this offseason.**

Where GitHub actually limits us:

| Limit | Where it bites | Hits us? |
|---|---|---|
| Pages caches at the edge for 10 min (`max-age=600`, can't be changed) | Freshness | No. The cache-busting already gets around it, and C2's manifest keeps that cheap |
| About 10 builds/hour soft limit for branch-published Pages | The live loop force-pushes `site` up to ~12×/hour | **Maybe, at peak.** Switching to `actions/deploy-pages` (custom workflow) removes the limit. Do it in week 1 |
| About 100 GB/month soft bandwidth | Auto-update × readers | Only without C2's bundle split. Once split, a high-traffic weekend is a few GB |
| No server code: no push sender, no user accounts, no subscriptions | Push alerts, pick'em, saved players | **Yes, if you want any of those.** The RSS feed covers alerts for now |
| Actions cron is best-effort; 6h job ceiling; no self-dispatch without a PAT/App | Live loop | Survivable (incident 11). A GitHub App token would let the loop start its own successor |
| Polling every PDGA event | B "all events" | **Yes.** Needs an always-on worker and PDGA's OK |

**What tells you it's time to move:** any of (a) push notifications with
subscriptions, (b) live scoring for every event, (c) accounts (pick'em, "my
players"), (d) Pages bandwidth warnings even after the split. None of these
is on the 2027 critical path.

**Hedge now, for about $15/year: a custom domain.** Point it at Pages
today. If we move later (likely Cloudflare Pages/R2 plus a Worker for the
dynamic parts, with Actions still doing the compute), links and the RSS feed
keep working and nobody notices. Without the domain, moving later breaks
every shared link.

**◆ Checkpoint D:** buy the domain or don't. Decide on push / accounts / all
events in the spring with real traffic numbers. Until then, log Pages
bandwidth and build counts each week.

---

## Workstream E — engineering hygiene (automated, no checkpoints)

Inventory #10-#15, in order: the HARDENING 9 hygiene list; switch Pages to
Actions deploy (D); a durable cache backup (step 0, then weekly); correctable
snapshots (HARDENING 8: `--resnapshot`, gate on invariants, last-write-wins
now that data commits are off main); per-event structural validation
(HARDENING 7); a realistic FPO fixture; hard violation → hold that event's
projection and show a banner (HARDENING 3, using B3's replays as the test
bed); clock-shifted test job on a schedule.

---

## Workstream F — rolling over to 2027 (automated, hard deadline)

Nothing else matters if the opener doesn't work. `SEASON = 2026` is in
`config.py`, and "2026" is on 100+ lines across `dgpt/`.

- Make the season a single parameter: schedule file, cache key, tour-card
  files, `REG_PHASES`, `SIDE_EVENTS`, StatMando URLs, history file names.
  Move the 2026 history files to `predictions/2026/`.
- **Rules:** the 2027 points structure isn't published. Plan for "2026 rules
  carry over", which is what `season2027.py` already assumes, and diff
  against the announcement when it comes out.
- **Preseason state:** an empty division currently raises (#16). Before the
  first event, the forecast *is* `project.py`'s no-results-yet mode. Decide
  how the main tab shows that, either by promoting the projection or by
  letting `simulate.py` handle zero banked events, without breaking the
  "live product doesn't grow a no-results mode" rule. **◆ you**, one
  decision.
- The 2027 and What-if tabs retire or become the main tab. The EuroTour
  goes from projection to a real season with real results, which brings a
  new validation source question.
- **Dress rehearsal (weeks 15-17):** run the full pipeline against a
  real early-2027 PDGA event in shadow mode (B5), then the opener live.

---

## Calendar

| Weeks | Dates | A (you) | B | C | D/E/F |
|---|---|---|---|---|---|
| 0 | Oct 12-18 | — | — | — | step 0: back up the data. Cup. |
| 1-2 | Oct 19-Nov 1 | A1 grade ◆ | B1 shot spike ◆, B2 | copy audit, budgets ◆ | Pages→Actions deploy, hygiene, ratings archive, domain ◆ |
| 3-5 | Nov 2-22 | A2 harness ◆ | B3 replay, B5 shadow | bundle split, version.json | snapshot fixes, FPO fixture |
| 6-9 | Nov 23-Dec 20 | A3 levers 1-3 ◆ | B4 hole model ◆ | rewrite tabs, auto-update, SW | per-event validation |
| 10-12 | Dec 21-Jan 10 | A3 levers 4-7 ◆ | B6 if go | UX recs 2,3,9,6 (Q3/Q4 ◆) | F: season parameter |
| 13-15 | Jan 11-31 | A4 refit, scorecard page | shadow on real events | recs 8,10, movers | F: preseason state ◆, rules diff |
| 16-17 | Feb 1-14 | **freeze 2/12** | dress rehearsal | freeze | dress rehearsal |
| 18 | Feb 26 | opener | | | |

Your time: about 10 checkpoints, most of them a page to read and one
decision. The lever reviews in A3 are where your weeks go, which is the
split you asked for.
