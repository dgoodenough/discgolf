# Captured live-API payloads

Complete, unmodified PDGA live-API responses, captured with `capture.py`
after the events completed (completed-event responses never change upstream).
Each is the raw `{"data": ...}` envelope a real fetch returns, so tests serve
them through a mocked `live_api._get` byte-for-byte.

These are the payloads behind this season's live-scoring incidents:

| Event | Files | Why it's here |
| --- | --- | --- |
| DGPT JomezPro Champions Landing Open (100195) | `event_100195`, `round_100195_MPO_{1,2,3}` | Multi-layout event: each sheet's round-total `ToPar` is reported against that round's par, so the values are incomparable across sheets (Buhr reads -16/-11/-10 for actual rounds -6/-1/-3). Preferring `ToPar` inverted the leaderboard — the model had the winner 100% to finish 2nd (fixed in b4cfb56 by summing per-round `RoundtoPar`). |
| USWDGC (97341, FPO major) | `event_97341`, `round_97341_FPO_{1,2,3,12,13}` | The shape-surprise event: populates `RoundtoPar` rather than a live `ToPar` (6e99dd4); the weather-delay weekend that dropped 38 suspended players from the field when only the latest sheet was read (5094f16); finals live on sheet id **12** (no round 4, no 11), round **13** is a one-row sudden-death playoff sheet, and `FinalRound=12` makes naive remaining-rounds math absurd. |

## Adding the next one

When a live number looks wrong, the raw payloads are in the flight recorder
(`data/cache/flight/` — every changed response during live refreshes, newest
~48 per sheet). Promote the relevant event to a permanent fixture by adding it
to `EVENTS` in `capture.py` and re-running it, then write the failing test in
`tests/test_live_api.py` before fixing anything.
