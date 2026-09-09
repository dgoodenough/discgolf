# DGPT Standings Forecast

**Live: [dgoodenough.github.io/discgolf](https://dgoodenough.github.io/discgolf/)**

I built this to answer one question all season: who makes the Powerball Cup?
It simulates the rest of the 2026 Disc Golf Pro Tour 100,000 times and
publishes qualification odds for every player, updated within 15 minutes
during events. Points are explained for newcomers at
[how it works](https://dgoodenough.github.io/discgolf/how-it-works.html).

I first built a version of this in 2021 as a Jupyter notebook fed by
hand-pulled CSVs (preserved in [archive/2021](archive/2021/)). This is the
rebuild: PDGA API data, exact points rules, and a site that maintains itself.

## What it does

- **Standings, computed independently.** Results come from PDGA live scoring,
  points from my implementation of the 2026 rules: per-place curves, class
  multipliers, tie splitting, per-class counting caps, winner invites, the
  doubles team curve, Jomez bonuses. Totals match StatMando, the official
  administrator, exactly. A CI check re-verifies that after every weekly
  refresh and fails loudly if we ever drift.
- **A calibrated simulation.** Scores are drawn from PDGA ratings. I refit
  the model against all 6,667 completed 2026 player-rounds: about 6 rating
  points equal one stroke per round in MPO (7.3 in FPO), with a 4.2-stroke
  event-level round spread. Fields use real registration lists where they
  exist and cohort-based participation rates (tour card, European) where
  they don't. The playoffs read their published signup lists and take them
  literally — an entered player is in that field however the standings
  finish — and fall back to the qualification ladder only for players whose
  registration wave hasn't opened. Worlds includes its one-round play-in:
  6 MPO / 2 FPO spots, raced against the real entry list.
- **Live events, modeled mid-round.** During play, each player's current
  score is locked in and only their remaining holes are simulated, so odds
  track the actual tournament instead of resetting to priors.
- **The live race, kept as a series.** The simulation answers "who wins this
  tournament" every time it runs; those answers are now recorded, so the
  **Event odds** tab draws every contender's win probability through the event
  on a holes-played axis — including the players who led it and lost it — over
  a table of everyone still above 0.1%.
- **The seed, not just the spot.** The Cup starts every qualifier on a score
  set by their World Standings position — -7 for the MPO No. 1, -6 for FPO,
  down to even for the bottom seeds. The **Starting strokes** column runs every
  simulated season through that ladder, so one row carries both the score a
  player would tee off on and how often they get no tee time at all.
- **The championship, played out.** Once the seed is known the Cup is a
  four-round tournament like any other, so the model runs it: scores from
  ratings, the seed's head start added to the total, low score wins. The
  **Win Cup** column is the result, and unlike qualification odds it is a race
  — the field's numbers add to 100%.
- **A what-if for every player.** Expand any row, toggle which events they
  will attend, and their Cup odds recompute in under 100ms against 25,000
  frozen simulation cutlines.
- **Its own scorecard.** Every meaningful forecast is snapshotted to
  [predictions/](predictions/) so the model can be graded (Brier,
  calibration) at season's end with `python -m dgpt.evaluate`.
- **Next season, clearly labelled as a guess.** The DGPT has published its
  2027 schedule, so two experimental tabs project seasons nobody has played:
  the whole 2027 DGPT season, and the new EuroTour. Both are built from the
  announcement plus this year's ratings and attendance rates; see
  [what they assume](#the-experimental-tabs) below.

## The experimental tabs

`dgpt/project.py` forecasts a season that has not started. It is deliberately
separate from `simulate.py` — the live 2026 forecast is the product and
updates every fifteen minutes during play, so it should not grow a "no results
yet" mode — but the two share what must not fork: the score model, the points
curves, the participation model and the ranking helpers.

Every assumption lives in `dgpt/season2027.py`, is emitted with the data, and
is shown on the page above the numbers. In short:

- **The calendar is real.** `data/schedule_2027.csv` is the announcement,
  transcribed. Round counts come from the dates (three-day stop, three rounds),
  which is the rule every 2026 event obeys. Event ids are synthetic — the PDGA
  has not assigned 2027 ones — so nothing links out to a PDGA event page yet.
- **2027 DGPT assumes 2026's rules.** Same curves, class multipliers,
  per-class counting caps, playoff windows and Cup seed ladder, with Ivy Hill
  inheriting the Green Mountain Championship's qualification and the Kansas
  City Wide Open the MVP Open's. The announcement changes the calendar and the
  postseason cadence, not the points structure. What is genuinely new is data:
  the USDGC joins the MPO race as a fourth major, and the JomezPro Series
  triples to seven all-counting stops.
- **Players carry today's ratings and this year's attendance rate.** Each is
  projected to play the same *share* of US stops, European stops and JomezPro
  stops as in 2026 — on a longer calendar, so more starts. Nobody improves,
  retires or turns pro, and a 2027 rookie does not exist.
- **The EuroTour's points structure is the published one.** Three categories
  paying 250 / 200 / 150 for a win, counted best 1-of-2, 2-of-3 and 3-of-3 —
  six results, and 1,100 for a perfect season, which `season2027.max_points()`
  asserts. The 2028 card bands (MPO top 6 Full / top 24 EuroTour, FPO top 3 /
  top 12) are theirs too. Ours is only the shape of each curve *below* first
  place: the DGPT's own per-place curve scaled to the published win value,
  which lands category 3 on exactly the Elite Series curve and 2 and 1 on the
  DGPT+ and Playoff multipliers.
- **What the EuroTour tab is still missing is the field, not the rules.** Its
  roster is European players with a 2026 DGPT standings row, so domestic-only
  Europeans are absent from a race they would really be in. At the three stops
  that are open to the whole tour — the European Open and both European Elite
  Series events — the table is at least raced against an outside field drawn
  from the rest of the standings, so a European's finish there is not measured
  against Europeans alone. Displacement (a Full Tour Card holder passing their
  EuroTour Card down) is not modelled and only ever pushes cards deeper, so the
  published odds are a floor for anyone just outside a band.

Both run at 20,000 simulated seasons in the daily refresh
(`--project-sims`, or `--skip-projection` to leave them alone, which is what
the live loop does).

## Running it

```
pip install -r requirements.txt
cp .env.example .env        # PDGA API credentials (developer program)
python -m dgpt.refresh --sims 100000
```

That rebuilds the schedule, banks any newly finished events, re-runs the
simulation, and regenerates the site data in `docs/`. GitHub Actions runs it
twice weekly, plus every 15 minutes during live play (a cheap change-check
skips the heavy work between rounds). `python -m dgpt.validate` diffs the
standings against StatMando; `python -m dgpt.calibrate` refits the score
model from cached rounds.

## Data sources

- [PDGA REST API](https://www.pdga.com/dev/api/rest/v1/services) (schedule,
  ratings; requires developer credentials), PDGA live scoring (results,
  registrations), and PDGA event pages (signup lists for the playoffs and the
  Worlds play-in, which live scoring doesn't carry until event week). Event
  and player data © 2026 PDGA. PDGA Authorized Developer.
- [DGPT points structure](https://www.dgpt.com/announcements/2026-points-structure/)
  and [playoff qualification](https://www.dgpt.com/announcements/playoff-qualification-update/)
- The DGPT's 2027 schedule announcement (Elite Series, JomezPro Series and
  EuroTour), transcribed by hand into `data/schedule_2027.csv` — it is the
  only source the experimental tabs have
- [StatMando](https://statmando.com/rankings/dgpt/mpo) for validation only

Styled with [Ledger](https://github.com/dgoodenough/style). Inspired by
FiveThirtyEight's sports forecasts.

## License

[MIT](LICENSE).
