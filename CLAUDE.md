# DGPT Standings Forecast

A Monte Carlo forecast of the Disc Golf Pro Tour season, published to
GitHub Pages. `README.md` is the reader-facing description; this file is the
working context.

## Shape

Two halves that meet at one seam:

```
dgpt/          Python pipeline: PDGA API -> points -> standings -> simulation
  |
  v  dgpt/export.py writes the JSON bundle
docs/data/*.json
  |
  v  fetched at page load
docs/          the site: vanilla ES modules, no build step, no framework
```

Working in one half rarely needs the other. `dgpt/CLAUDE.md` and
`docs/CLAUDE.md` carry the detail for each, and load when you touch them.

## Rules that hold everywhere

- **Never commit `docs/data/`.** It is gitignored and published to the `site`
  branch by `.github/publish-site.sh`. Committing the bundle to `main` is the
  mistake `notes/HARDENING.md` item 6 exists to prevent.
  `data/live_signature.txt`, `data/current_ratings.json`, `data/live_odds.csv`
  and `predictions/history_*.csv` are cross-run state and *do* stay tracked.
- **The standings are validated against StatMando and must match exactly.**
  `python -m dgpt.validate` diffs them; CI runs it after every refresh. If a
  points change makes them disagree, the change is wrong until proven
  otherwise.
- **The pipeline runs unattended every 15 minutes during live play.** Anything
  that can fail has to fail loudly and leave the last good bundle up, rather
  than publish something wrong.
- Tests: `python -m pytest tests -q` (243 tests, ~4s). CI gates every PR.
- No secrets in the repo. PDGA credentials come from `.env` (see
  `.env.example`).

## notes/

Design journals and backlogs — history and rationale, not instructions. Read
one when you are working in the area it covers:

| file | read it when |
|---|---|
| `notes/HARDENING.md` | touching the pipeline, CI, or failure handling — it is the incident log and the engineering backlog |
| `notes/MODEL_IDEAS.md` | changing the score model or its calibration |
| `notes/MOVERS_DESIGN.md` | changing the movers panel or the snapshot history behind it |
| `notes/UX_IDEAS.md` | changing the site's copy, layout, or information design |

`archive/2021/` is the original Jupyter version, kept for provenance. Nothing
there is live.
