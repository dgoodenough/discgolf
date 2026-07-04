# Prediction snapshots

Append-only history of the model's forecasts, recorded by every refresh so the
model can be scored after the season. One row per player per snapshot.

- `history_mpo.csv`, `history_fpo.csv`

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
| `p_gmc` | P(makes the Green Mountain Championship field) |
| `p_mvp` | P(makes the MVP Open field via points) |
| `p_mvp_qual` | P(earns a Cup spot via MVP-performance path) |
| `p_first` | P(finishes the season as the #1 seed) |
| `mean_pts`, `mean_rank` | projected final points / standings rank |

## Scoring it after the season

The *actual* outcomes aren't stored — they come from the final results:
auto-bid = finished top 28/18; Cup = in the 32/20 championship field; GMC/MVP =
was in those playoff fields. Build an `actuals.csv`
(`pdga_number,auto_bid,made_cup,made_gmc,made_mvp` as 0/1) from the final
standings and playoff fields, then:

```
python -m dgpt.evaluate --division MPO --actuals actuals.csv
```

which reports, per snapshot date, the **Brier score** (mean squared error of
the probabilities — lower is better) and a **calibration table** (do events
predicted at 70% actually happen ~70% of the time?). Tracking Brier by
`events_completed` shows how the forecast sharpened as the season progressed.
