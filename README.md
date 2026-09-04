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
- **A what-if for every player.** Expand any row, toggle which events they
  will attend, and their Cup odds recompute in under 100ms against 25,000
  frozen simulation cutlines.
- **Its own scorecard.** Every meaningful forecast is snapshotted to
  [predictions/](predictions/) so the model can be graded (Brier,
  calibration) at season's end with `python -m dgpt.evaluate`.

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
- [StatMando](https://statmando.com/rankings/dgpt/mpo) for validation only

Styled with [Ledger](https://github.com/dgoodenough/style). Inspired by
FiveThirtyEight's sports forecasts.

## License

[MIT](LICENSE).
