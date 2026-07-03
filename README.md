# DGPT Standings Forecast

**Live: [dgoodenough.github.io/discgolf](https://dgoodenough.github.io/discgolf/)** —
current standings, Powerball Cup qualification odds with per-position
distributions, and a what-if mode that recomputes a player's odds instantly
as you toggle which events they'll attend.

Monte Carlo forecast of the Disc Golf Pro Tour World Standings — who makes
the Powerball Cup (the DGPT Championship), and at what odds. Inspired by
[FiveThirtyEight's sports forecasts](https://projects.fivethirtyeight.com/soccer-predictions/).
Styled with [Ledger](https://github.com/dgoodenough/style) (vendored `tokens.css`).

The what-if mode uses **cutline replay**: the browser re-simulates just the
selected player against 25,000 frozen per-sim qualification cutlines from the
full model run — real distributional math at <100ms per toggle. Validated
within ~2 points of the full model across the odds spectrum; multi-event
scenarios for a single player are exact in spirit, simultaneous edits to
many players are not (each player is scored against cutlines that don't
know about the others).

Originally built for the 2021 season with hand-pulled CSVs (preserved in
[archive/2021](archive/2021/)); rebuilt in 2026 on top of the PDGA API so it
can refresh automatically.

## How it works

1. **Schedule** — pulled from the official PDGA REST API (`tier=ES` for
   Elite Series, `tier=M` for Pro Majors, plus JomezPro Series A-tiers).
2. **Results** — finishing places for completed events from PDGA's public
   live-scoring API, with DNF/WD detection.
3. **Points** — the 2025/2026 StatMando-administered points system:
   separate MPO/FPO per-place curves ([data/pointslogic](data/pointslogic/)),
   straight class multipliers (Elite ×1 → 150 for a win, DGPT+ ×4/3 → 200,
   Playoff ×5/3 → 250, Major ×2 → 300), tie groups averaging the points of
   the places they span, JomezPro flat bonuses (20/10/5 for 1st/2nd–5th/6th–10th),
   and the season counting rules: best 14 finishes, top 2 majors counted
   (MPO 2-of-3, FPO 2-of-4), no FPO points at Heinola, the doubles-adjusted
   curve at the Preserve, and no points at the Powerball Cup or USDGC.
   **Computed standings match StatMando's official totals exactly** for all
   ~500 MPO+FPO players (validated July 2026).
4. **Fields for future events** — manual overrides, else the real PDGA Live
   registration list when the event is within two weeks, else per-player
   participation rates from this season's starts (split US / Europe /
   JomezPro).
5. **Simulation** — each run draws a field per event, simulates round
   scores (`-(rating - field_avg) / 6` strokes per round, σ = 6.82 — same
   core as the 2021 model), ranks, awards points, applies the counting
   rules on top of banked points, and tallies final standings ranks.

Championship qualification: 32 MPO / 20 FPO play the Powerball Cup — top
28 / top 18 from standings plus playoff-performance alternates. The output
reports both `p_top28_standings` (direct qualification) and `p_top32`
(field size, an upper bound that ignores the MVP Open alternate path).

## Usage

```
pip install -r requirements.txt
cp .env.example .env   # add your PDGA API credentials (developer access)
python -m dgpt.refresh --sims 100000
```

Outputs: `data/standings_{mpo,fpo}_2026.csv` (current standings) and
`results/2026/projections_{mpo,fpo}.csv` (qualification odds).

To force a specific player in/out of an event, add a row to
`data/overrides/fields.csv` (`tournament_id,pdga_number,plays`).

## Attribution

Event data © 2026 [PDGA](https://www.pdga.com) · Player data © 2026
[PDGA](https://www.pdga.com) · PDGA Authorized Developer. Per the
[PDGA developer program requirements](https://www.pdga.com/dev/developer-program),
every player name in the app links to the player's PDGA profile and every
event name links to its PDGA event page.

## Data sources

- [PDGA REST API](https://www.pdga.com/dev/api/rest/v1/services) (events,
  ratings; requires developer credentials)
- PDGA live-scoring API (results; public)
- [2026 points structure](https://www.dgpt.com/announcements/2026-points-structure/),
  [2025 standings structure](https://www.dgpt.com/announcements/2025-standings-structure/)
- [StatMando standings](https://statmando.com/rankings/dgpt/mpo) (validation only)

## License

[MIT](LICENSE).
