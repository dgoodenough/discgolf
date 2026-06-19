# DGPT Standings Forecast — a Monte Carlo model

A Monte Carlo simulation that forecasts the **end-of-season standings of the Disc
Golf Pro Tour (DGPT)** — i.e. each pro's probability of finishing 1st, top-8,
top-16, etc. across the MPO and FPO divisions — rather than just predicting a
single outcome.

Inspired by [FiveThirtyEight's sports forecasts](https://projects.fivethirtyeight.com/soccer-predictions/).
It began as a Google Sheets model (capped near ~50 simulations by spreadsheet
recalculation time) and was ported to Python/pandas to run **1,000+ full-season
simulations**.

## How it works

1. **Inputs** (`events/`, `eventplayers/`, `pointslogic/`):
   - the season's event schedule — date, tour, event type, number of rounds;
   - each event's projected field, with every player's PDGA rating;
   - the DGPT points tables by tour and finishing position.
2. **Rating → expected score.** Each player's PDGA rating becomes an expected
   strokes-vs-field-average per round (≈ 6 rating points per stroke), with a
   time-decay term so events further in the future carry more uncertainty.
3. **Simulate (N = 1,000).** For every player at every event, draw each round
   from a normal distribution (σ ≈ 6.8 strokes), sum to an event score, rank the
   field, and resolve first-place ties with a random playoff.
4. **Score it.** Apply DGPT points by tour/finish, split points across ties, and
   apply each series' "best N of M events count" rule (Elite top 8, Silver top 3,
   NT top 4, PDPT top 4). Completed events use real results in place of simulated.
5. **Aggregate.** Across all 1,000 runs, compute each player's standings
   *distribution* — P(1st), P(top 8 / 16 / 32) and average finish — per division
   and tour. Results land in `results/`.

## Repository layout

```
DGPTModelV2.ipynb    the current model (load → simulate → score → aggregate)
events/              event schedules (date, tour, type, rounds)
eventplayers/        projected fields + PDGA ratings, per scenario
pointslogic/         DGPT points tables (Elite / Silver / NT / PDPT)
results/             aggregated forecast outputs (MPO & FPO)
other/               earlier model version + a results-scraping experiment
```

## Techniques shown

Monte Carlo simulation · probabilistic forecasting · pandas data wrangling ·
rating-based score modeling · sports analytics.

## Status & caveats

- Built for the **2021 DGPT season** (data snapshots dated Aug 2021). It's a
  point-in-time exploratory notebook, not a maintained library — to run a
  different season you'd swap in that season's CSVs.
- `DGPTModelV2.ipynb` is current; `other/DGPTModel.ipynb` is the earlier version
  and `other/ScreenscrapeTest.ipynb` an experiment for scraping live results.
- The final aggregation cell is intentionally verbose (one block per finishing
  position) — function over polish.
- Player ratings, schedules and results are public/factual PDGA data.

## License

[MIT](LICENSE).
