"""2026 DGPT points engine.

Base per-place curves (MPO/FPO, Elite Series scale where a win = 150) are
scaled by straight class multipliers. Ties: every tied player receives the
mean of the points for the places the tie group occupies (e.g. two players
T2 each get (125 + 115) / 2 = 120). The Preserve doubles event awards each
team member the mean of the two singles places its finishing position spans.
"""
from __future__ import annotations

import csv
from functools import lru_cache

from . import config


@lru_cache(maxsize=1)
def base_curves() -> dict[str, dict[int, float]]:
    curves: dict[str, dict[int, float]] = {"MPO": {}, "FPO": {}}
    with open(config.BASE_CURVES_CSV, newline="") as f:
        for row in csv.DictReader(f):
            place = int(row["place"])
            curves["MPO"][place] = float(row["mpo_points"])
            curves["FPO"][place] = float(row["fpo_points"])
    return curves


def event_curve(division: str, cls: str) -> dict[int, float]:
    mult = config.MULTIPLIERS[cls]
    base = base_curves()[division]
    if cls == "doubles":
        # Team place p spans singles places 2p-1 and 2p; each member gets the mean.
        return {
            p: (base.get(2 * p - 1, 0.0) + base.get(2 * p, 0.0)) / 2.0
            for p in range(1, len(base) // 2 + 1)
        }
    return {p: v * mult for p, v in base.items()}


# JomezPro Series bonus: flat bands, no tie-averaging. Reverse-engineered
# from WACO/Cascade 2026 vs official standings (4-way T2 at WACO all got 10).
JOMEZ_BANDS = ((1, 20.0), (5, 10.0), (10, 5.0))


def jomez_bonus(place: int) -> float:
    for max_place, pts in JOMEZ_BANDS:
        if place <= max_place:
            return pts
    return 0.0


def assign_points(places: list[int], division: str, cls: str) -> list[float]:
    """Points for each entry of `places` (finishing places incl. ties, e.g.
    [1, 2, 2, 4, ...]). Tied players get the mean across occupied places."""
    if cls == "jomez":
        return [jomez_bonus(p) for p in places]
    curve = event_curve(division, cls)
    from collections import Counter

    counts = Counter(places)
    tie_points = {
        p: sum(curve.get(p + i, 0.0) for i in range(n)) / n for p, n in counts.items()
    }
    return [round(tie_points[p], 2) for p in places]


def season_total(event_results: list[tuple[int, float]], division: str) -> float:
    """Season points from [(tournament_id, points)] applying counting rules:
    best MAJORS_COUNTED majors kept, then best TOP_N_FINISHES overall."""
    major_tids = (
        config.MAJOR_TIDS_MPO if division == "MPO" else config.MAJOR_TIDS_FPO
    )
    majors = sorted(
        (pts for tid, pts in event_results if tid in major_tids), reverse=True
    )
    others = [pts for tid, pts in event_results if tid not in major_tids]
    pool = others + majors[: config.MAJORS_COUNTED]
    return round(sum(sorted(pool, reverse=True)[: config.TOP_N_FINISHES]), 2)
