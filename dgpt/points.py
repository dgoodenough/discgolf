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


# event class -> counting bucket
POOL_BY_CLS = {
    "elite": "dgpt", "elite_plus": "dgpt", "doubles": "dgpt",
    "playoff": "playoff", "major": "major", "jomez": "jomez",
    "championship": None,
}

_CLS_BY_TID: dict[int, str] = {}


def _cls_by_tid() -> dict[int, str]:
    """tid -> event class, read from the schedule CSV (authoritative, unlike
    the runtime-populated config.JOMEZ_TIDS). Cached; call refresh_classes()
    after rebuilding the schedule."""
    if not _CLS_BY_TID:
        from . import schedule
        _CLS_BY_TID.update({r["tournament_id"]: r["cls"] for r in schedule.load()})
    return _CLS_BY_TID


def refresh_classes() -> None:
    _CLS_BY_TID.clear()


def event_pool(tid: int, division: str) -> str | None:
    """Which counting bucket a tournament belongs to (2026 structure), or None
    for events that award no World Standings points (the Championship)."""
    return POOL_BY_CLS.get(_cls_by_tid().get(tid, "elite"), "dgpt")


def season_total(event_results: list[tuple[int, float]], division: str) -> float:
    """Season points from [(tournament_id, points)] under the 2026 per-class
    caps: best 10 DGPT/DGPT+, best 2 playoffs, best 2 majors, plus all Jomez
    bonus points."""
    pools: dict[str, list[float]] = {"dgpt": [], "playoff": [], "major": [], "jomez": []}
    for tid, pts in event_results:
        pool = event_pool(tid, division)
        if pool:
            pools[pool].append(pts)
    total = (
        sum(sorted(pools["dgpt"], reverse=True)[: config.COUNT_DGPT])
        + sum(sorted(pools["playoff"], reverse=True)[: config.COUNT_PLAYOFF])
        + sum(sorted(pools["major"], reverse=True)[: config.COUNT_MAJOR])
        + sum(pools["jomez"])
    )
    return round(total, 2)
