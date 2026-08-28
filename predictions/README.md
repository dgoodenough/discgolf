# Prediction snapshots

Append-only history of the model's forecasts, recorded by every refresh so the
model can be scored after the season. One row per player per snapshot.

- `history_mpo.csv`, `history_fpo.csv` — the forecasts
- `actuals_mpo.csv`, `actuals_fpo.csv` — what actually happened, written by
  `python -m dgpt.actuals` (see [below](#scoring-it-after-the-season))

**Cadence:** at most one snapshot per calendar day, and only when the
predictions changed since the last one (the sim is deterministic given its
inputs, so quiet midweek days add no rows). In practice you get a snapshot
whenever results or registrations move — i.e. around every event.

## Columns

| column | meaning |
| --- | --- |
| `snapshot_date` | date the snapshot was taken (YYYY-MM-DD) |
| `taken_at` | full timestamp |
| `events_completed` | points events finished as of this snapshot (the info state) |
| `division` | MPO / FPO |
| `pdga_number`, `name`, `rating` | player |
| `cur_rank`, `cur_points` | realized standings at snapshot time (for convergence) |
| `p_champ` | P(in the Powerball Cup field) — **the headline forecast** |
| `p_cut` | P(automatic bid: top 28 MPO / 18 FPO) |
| `p_gmc` | P(inside the GMC **points cut** — top 100 MPO / 50 FPO before GMC) |
| `p_mvp` | P(inside the MVP Open **points cut** — top 72 MPO / 36 FPO before it) |
| `p_gmc_field` | P(actually **in** the GMC field — the number the site shows) |
| `p_mvp_field` | P(actually **in** the MVP Open field, incl. the GMC-performance path) |
| `p_mvp_qual` | P(earns a Cup spot via MVP-performance path) |
| `p_first` | P(finishes the season as the #1 seed) |
| `mean_pts`, `mean_rank` | projected final points / standings rank |
| `registered` | remaining events the player is registered for (`;`-separated tids) |
| `signed` | playoff signup lists they are on (`-` = none; blank = pre-schema row) |

The `p_gmc` / `p_gmc_field` pair is easy to conflate and they are not the same
question: a field is the signup list **unioned** with the standings gate at the
fill number (120 MPO), so a player outside the points cut can still be in it.
`p_gmc_field` was added part-way through 2026, so earlier rows carry a blank —
which `evaluate` skips rather than scoring as zero.

## Scoring it after the season

Actual outcomes are resolved by `dgpt.actuals`, not assembled by hand — every
one of them is a read of data the pipeline already fetches:

```
python -m dgpt.actuals                  # writes predictions/actuals_{div}.csv
python -m dgpt.evaluate --division MPO  # grades the forecast against it
```

Outcomes that have not been decided yet are written blank and skipped by
`evaluate`, so both commands are safe to run mid-season — you just get fewer
graded columns.

One piece is **not** recomputable after the fact, which is why `actuals.capture()`
runs on every refresh: a PDGA Live roster is readable for a window around its
event and then goes dark, so the GMC / MVP / Cup fields are banked into
`data/actual_fields.json` while they are up. The standings-based outcomes
(`auto_bid`, `gmc_points_cut`, `mvp_points_cut`) need no capture — they are pure
functions of banked results and can be replayed at any time.

which reports, per snapshot date, the **Brier score** (mean squared error of
the probabilities — lower is better) and a **calibration table** (do events
predicted at 70% actually happen ~70% of the time?). Tracking Brier by
`events_completed` shows how the forecast sharpened as the season progressed.
