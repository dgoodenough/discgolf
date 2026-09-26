# dgpt/ — the pipeline

Python, stdlib plus numpy. Reads the PDGA API and PDGA live scoring, computes
points and standings, simulates the rest of the season, and writes the JSON
bundle the site reads.

## The run

`python -m dgpt.refresh` is the orchestrator, and the order matters:

```
schedule.build()        the season's points-eligible events, from the PDGA API
  -> points.refresh_classes()
standings.compute()     per division, from banked results
  -> simulate.run()     the Monte Carlo
     -> export.export()     docs/data/<div>.json      <- the seam with docs/
     -> snapshot.record()   predictions/history_*.csv <- the scorecard
     -> liveodds.record()   the live win-probability series
  -> project.run()      the experimental 2027 / EuroTour tabs
     -> whatif.Kit         the drop-in summary the What-if tab reads
movers / feed / liveodds.write_json()
invariants.run_checks()
```

`whatif.py` is a summary of a projection rather than a stage of one: it rides
along with `project.run` and reduces the 2027 DGPT standings ladder, both
playoff gates and the Cup seed profile to a ~25 KB block in `meta.whatif`, so
the browser can rank an invented player against the field without re-simulating
it. Its docstring is the argument for why quantiles are enough. It is emitted
only for a tour with a postseason — the EuroTour bundle carries none.

`asis.py` rides along with `simulate.run` the same way: while an event is live
it freezes it where it stands (`live_api.counted_standing`: complete rounds in
full, a round in progress only on the holes every player has completed),
banks that through `points`, and re-ranks the season — `meta.as_is` and
`players[].as_is`, the site's "If over now" column. A side panel: `asis.run`
swallows its own failures, so it can never cost the forecast a publish.

Supporting modules: `pdga_api` (authenticated REST, needs `.env`), `live_api`
(public live scoring, no auth), `ratings`, `fields` (who plays what),
`config` + `points` (the 2026 rules), `calibrate` (refits the score model),
`evaluate` (grades the snapshots at season's end).

## Rules

- **`dgpt/export.py` is the schema.** It is the only contract with `docs/`.
  Changing a field there means changing the site in the same commit — and the
  site is served from the last *published* bundle, so removing or renaming a
  field breaks the live page until the next refresh lands. Add fields; retire
  them slowly.

- **Standings must match StatMando exactly.** They administer the official
  points. `python -m dgpt.validate` diffs them and exits 1 on any mismatched
  total; CI runs it after every refresh. A points change that makes them
  disagree is wrong until proven otherwise. (Name-only spelling mismatches are
  warnings, not failures.)

- **Nothing in the publish path may block the publish.** Both guards run
  *after* the data commit and turn a problem into a red CI run rather than a
  stale site: `validate` exits non-zero, `invariants` writes
  `data/cache/invariant_violations.txt` for the workflow to read. Keep that
  posture — a check that can wedge the pipeline is worse than the bad number
  it was meant to catch.

- **`project.py` is deliberately separate from `simulate.py`.** The live 2026
  forecast is the product and republishes every fifteen minutes during play;
  it must not grow a "no results yet" mode. They share what must not fork —
  the score model, the points curves, the participation model, the ranking
  helpers — and nothing else. Every assumption the forward projection makes
  lives in `season2027.py`, is emitted in `meta.notes`, and is rendered above
  the numbers on the page, so the page cannot drift from what the code did.

- **An experimental tab never fails the run.** `refresh.project_season` wraps
  each projection in a bare `except` on purpose: a bad 2027 schedule row must
  not take down the 2026 forecast. Anything else failing *should* be loud.

- **The live loop is cost-shaped.** `livecheck.py` runs first without numpy and
  skips the heavy work when nothing moved, so the 15-minute cron idles between
  rounds. The loop also passes `--skip-projection` and carries the last
  published projection bundles across. Do not make the hot path more
  expensive without checking `.github/workflows/live-refresh.yml`.

- **Cross-run state is tracked; generated output is not.** `data/live_signature.txt`,
  `data/current_ratings.json`, `data/live_odds.csv` and
  `predictions/history_*.csv` are read back on the next run and stay in git.
  `docs/data/`, `results/` and `data/standings_*.csv` are gitignored.

## Tests

`python -m pytest tests -q` — 243 tests, ~4s, gates every PR. `tests/fixtures/`
holds captured PDGA payloads (the regression corpus from
`notes/HARDENING.md` item 1); `tests/fixtures/capture.py` adds new ones. When a
live-API shape change bites, the fix is a fixture plus a test, not just a
patch — that file has already changed shape six times this season.
