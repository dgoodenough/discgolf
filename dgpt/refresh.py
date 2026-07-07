"""Refresh everything: schedule -> results -> standings -> projections.

Usage:
    python -m dgpt.refresh [--sims 10000] [--skip-sim]
"""
from __future__ import annotations

import argparse

from . import export, schedule, simulate, snapshot, standings


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sims", type=int, default=simulate.DEFAULT_SIMS)
    ap.add_argument("--skip-sim", action="store_true", help="standings only")
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


if __name__ == "__main__":
    main()
