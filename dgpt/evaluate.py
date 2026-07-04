"""Score the recorded prediction snapshots against actual outcomes.

Run after the season with an actuals CSV (pdga_number, auto_bid, made_cup,
made_gmc, made_mvp as 0/1, built from the final standings + playoff fields):

    python -m dgpt.evaluate --division MPO --actuals actuals.csv

Reports Brier score by snapshot date (how the forecast sharpened over the
season) and a calibration table for the earliest snapshot (the hardest call).
Stdlib only.
"""
from __future__ import annotations

import argparse
import csv
from collections import defaultdict

from . import config

# prediction column -> actuals column
OUTCOMES = {
    "p_champ": "made_cup",
    "p_cut": "auto_bid",
    "p_gmc": "made_gmc",
    "p_mvp": "made_mvp",
}


def load_history(division: str) -> list[dict]:
    path = config.REPO_ROOT / "predictions" / f"history_{division.lower()}.csv"
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_actuals(path: str) -> dict[int, dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return {int(r["pdga_number"]): r for r in csv.DictReader(f)}


def brier_by_date(hist: list[dict], actuals: dict, pred_col: str, act_col: str) -> list[tuple[str, int, float, int]]:
    by_date: dict[str, list[float]] = defaultdict(list)
    n_completed: dict[str, int] = {}
    for r in hist:
        a = actuals.get(int(r["pdga_number"]))
        if not a or a.get(act_col, "") == "":
            continue
        p, y = float(r[pred_col]), float(a[act_col])
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
        a = actuals.get(int(r["pdga_number"]))
        if not a or a.get(act_col, "") == "":
            continue
        b = min(bins - 1, int(float(r[pred_col]) * bins))
        buckets[b].append(float(a[act_col]))
    out = []
    for b in range(bins):
        ys = buckets.get(b, [])
        if ys:
            out.append((f"{b/bins:.0%}-{(b+1)/bins:.0%}", len(ys), sum(ys) / len(ys)))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--division", choices=["MPO", "FPO"], required=True)
    ap.add_argument("--actuals", required=True, help="CSV: pdga_number, auto_bid, made_cup, made_gmc, made_mvp")
    args = ap.parse_args()

    hist = load_history(args.division)
    actuals = load_actuals(args.actuals)
    first_date = min(r["snapshot_date"] for r in hist)

    for pred_col, act_col in OUTCOMES.items():
        print(f"\n=== {pred_col} vs {act_col} — Brier by snapshot ===")
        print(f"{'date':<12}{'events_in':>10}{'brier':>10}{'n':>7}")
        for d, nev, score, n in brier_by_date(hist, actuals, pred_col, act_col):
            print(f"{d:<12}{nev:>10}{score:>10.4f}{n:>7}")
        print(f"  calibration @ {first_date}:")
        for label, n, obs in calibration(hist, actuals, pred_col, act_col, first_date):
            print(f"    predicted {label:<9} n={n:<4} observed {obs:.0%}")


if __name__ == "__main__":
    main()
