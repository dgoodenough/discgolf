"""Score the recorded prediction snapshots against actual outcomes.

    python -m dgpt.actuals                  # resolve what happened
    python -m dgpt.evaluate --division MPO  # grade the forecast against it

Reads predictions/actuals_{div}.csv, which dgpt.actuals writes from the final
standings and the captured playoff/Cup fields — pass --actuals to point at a
different file. Outcomes that have not been decided yet are blank there and
are skipped here, so this is worth running mid-season too.

Reports Brier score by snapshot date (how the forecast sharpened over the
season) and a calibration table for the earliest snapshot (the hardest call).
Stdlib only.
"""
from __future__ import annotations

import argparse
import csv
from collections import defaultdict

from . import config

# Prediction column -> actuals column.
#
# Mind the playoff pairs: the snapshot's `p_gmc` is P(inside the GMC *points
# cut*) — simulate's p_gmc, not p_gmc_field — while the site's GMC column and
# the bundle's `p_gmc` are P(in the field), which is a broader thing (the
# field unions the signup list with the standings gate at gmc_fill, so a
# player outside the points cut can still be in it). Grading the points-cut
# series against a made-the-field outcome would silently score two different
# questions against each other, so each is paired with its own column and the
# field series is snapshotted separately as `p_gmc_field` / `p_mvp_field`.
OUTCOMES = {
    "p_champ": "made_cup",
    "p_cut": "auto_bid",
    "p_gmc": "gmc_points_cut",
    "p_mvp": "mvp_points_cut",
    "p_gmc_field": "made_gmc",
    "p_mvp_field": "made_mvp",
}


def load_history(division: str) -> list[dict]:
    path = config.REPO_ROOT / "predictions" / f"history_{division.lower()}.csv"
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_actuals(path: str) -> dict[int, dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return {int(r["pdga_number"]): r for r in csv.DictReader(f)}


def _pair(r: dict, actuals: dict, pred_col: str, act_col: str) -> tuple[float, float] | None:
    """(prediction, outcome) for one history row, or None if either is unknown.

    Both sides can legitimately be blank and they mean different things. A
    blank prediction is a row written before that column existed (snapshot
    schema growth backfills blanks); a blank outcome is something the season
    has not decided yet. Either way there is nothing to score.
    """
    a = actuals.get(int(r["pdga_number"]))
    if not a or a.get(act_col, "") == "" or r.get(pred_col, "") == "":
        return None
    return float(r[pred_col]), float(a[act_col])


def brier_by_date(hist: list[dict], actuals: dict, pred_col: str, act_col: str) -> list[tuple[str, int, float, int]]:
    by_date: dict[str, list[float]] = defaultdict(list)
    n_completed: dict[str, int] = {}
    for r in hist:
        pair = _pair(r, actuals, pred_col, act_col)
        if pair is None:
            continue
        p, y = pair
        by_date[r["snapshot_date"]].append((p - y) ** 2)
        n_completed[r["snapshot_date"]] = int(r["events_completed"])
    return [
        (d, n_completed[d], sum(v) / len(v), len(v))
        for d, v in sorted(by_date.items())
    ]


def calibration(hist: list[dict], actuals: dict, pred_col: str, act_col: str,
                snapshot_date: str, bins: int = 10) -> list[tuple[str, int, float]]:
    buckets: dict[int, list[float]] = defaultdict(list)
    for r in hist:
        if r["snapshot_date"] != snapshot_date:
            continue
        pair = _pair(r, actuals, pred_col, act_col)
        if pair is None:
            continue
        p, y = pair
        buckets[min(bins - 1, int(p * bins))].append(y)
    out = []
    for b in range(bins):
        ys = buckets.get(b, [])
        if ys:
            out.append((f"{b/bins:.0%}-{(b+1)/bins:.0%}", len(ys), sum(ys) / len(ys)))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--division", choices=["MPO", "FPO"], required=True)
    ap.add_argument("--actuals", help="outcomes CSV (default: predictions/actuals_{div}.csv, "
                                      "written by python -m dgpt.actuals)")
    args = ap.parse_args()

    hist = load_history(args.division)
    path = args.actuals or (config.REPO_ROOT / "predictions"
                            / f"actuals_{args.division.lower()}.csv")
    actuals = load_actuals(path)
    first_date = min(r["snapshot_date"] for r in hist)

    for pred_col, act_col in OUTCOMES.items():
        rows = brier_by_date(hist, actuals, pred_col, act_col)
        if not rows:
            # Either the outcome has not been decided yet or the prediction
            # column postdates every snapshot. Say which, rather than printing
            # an empty table that reads like a bug.
            known = any(a.get(act_col, "") != "" for a in actuals.values())
            print(f"\n=== {pred_col} vs {act_col} — not gradeable yet "
                  f"({'no snapshot carries this column' if known else 'outcome undecided'}) ===")
            continue
        print(f"\n=== {pred_col} vs {act_col} — Brier by snapshot ===")
        print(f"{'date':<12}{'events_in':>10}{'brier':>10}{'n':>7}")
        for d, nev, score, n in rows:
            print(f"{d:<12}{nev:>10}{score:>10.4f}{n:>7}")
        print(f"  calibration @ {first_date}:")
        for label, n, obs in calibration(hist, actuals, pred_col, act_col, first_date):
            print(f"    predicted {label:<9} n={n:<4} observed {obs:.0%}")


if __name__ == "__main__":
    main()
