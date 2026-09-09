"""Refresh everything: schedule -> results -> standings -> projections.

Usage:
    python -m dgpt.refresh [--sims 10000] [--skip-sim] [--skip-projection]

The 2027 / EuroTour projections (dgpt.project) ride along at the end of each
division, off the standings table this run just computed. They are a season
away and move only as slowly as the participation rates they are fit to, so
the live loop turns them off with --skip-projection and republishes the last
published bundle instead — see .github/workflows/live-refresh.yml.
"""
from __future__ import annotations

import argparse

from . import (export, feed, liveodds, movers, project, schedule, season2027,
               simulate, snapshot, standings)


def project_season(division: str, table: list[dict], sched: list[dict], n_sims: int) -> None:
    """Run the experimental forward-season projections for one division.

    Never fatal. These tabs are labelled experimental on the page and are not
    what anyone comes here for; a bad 2027 schedule row or an empty European
    roster must not take down the refresh that publishes the live 2026
    forecast. The failure is printed and the previously published bundle stays
    up until the next run fixes it.
    """
    for spec in (season2027.DGPT, season2027.EUROTOUR):
        try:
            res = project.run(spec, division, table, sched, n_sims=n_sims)
            project.export(res)
        except Exception as e:  # noqa: BLE001 - an experimental tab never fails the run
            print(f"  {spec.label} {division} projection skipped ({e})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sims", type=int, default=simulate.DEFAULT_SIMS)
    ap.add_argument("--skip-sim", action="store_true", help="standings only")
    ap.add_argument("--project-sims", type=int, default=project.DEFAULT_SIMS,
                    help="runs for the experimental 2027 / EuroTour projections")
    ap.add_argument("--skip-projection", action="store_true",
                    help="don't re-run the 2027 / EuroTour projections (they barely move day to day)")
    ap.add_argument("--only-if-live", action="store_true",
                    help="exit early unless a points event is in progress (for the frequent live cron)")
    args = ap.parse_args()

    print("building schedule from PDGA API ...")
    rows = schedule.build()
    from . import points
    points.refresh_classes()  # re-read event classes from the fresh schedule
    done = sum(1 for r in rows if r["completed"])
    print(f"  {len(rows)} points-relevant events, {done} completed")

    if args.only_if_live:
        live = schedule.live_events(rows)
        if not live:
            print("no live event — skipping refresh")
            return
        print(f"  live now: {', '.join(r['name'][:40] for r in live)}")

    for division in ("MPO", "FPO"):
        print(f"computing {division} standings ...")
        table = standings.compute(division)
        standings.write_csv(division, table)
        print(f"  #1: {table[0]['name']} ({table[0]['points']})")
        if not args.skip_sim:
            print(f"simulating {division} ({args.sims} runs) ...")
            res = simulate.run(division, n_sims=args.sims)
            simulate.write_csv(res)
            export.export(res)
            print("  " + snapshot.record(res, division))
            print("  " + liveodds.record(res, division))
        if not args.skip_projection:
            project_season(division, table, rows, args.project_sims)

    if not args.skip_sim:
        movers.write_movers()
        print("  " + feed.write_feed())
        print("  " + liveodds.write_json())

    # Publish-gate invariants (flag-only): the refresh must still succeed —
    # the workflows read the marker file after committing and turn violations
    # into a red run, so bad-looking data alerts without the site going stale.
    try:
        from . import invariants
        invariants.run_checks()
    except Exception as e:
        print(f"  invariant checks skipped ({e})")


if __name__ == "__main__":
    main()
