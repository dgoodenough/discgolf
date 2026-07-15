"""Compute current-season DGPT World Standings from actual results."""
from __future__ import annotations

import csv
from collections import defaultdict

from . import config, live_api, points, ratings, schedule


def event_points(row: dict, division: str) -> dict[int, dict]:
    """Points earned at one completed event: {pdga_number: {...}}."""
    if division == "FPO" and not row["fpo_points"]:
        return {}
    if not row[division.lower()]:
        return {}
    if config.MULTIPLIERS[row["cls"]] == 0.0 and row["cls"] != "jomez":
        return {}

    results = live_api.final_results(row["tournament_id"], division)
    results = [r for r in results if r["place"] and r["pdga_number"]]
    pts = points.assign_points([r["place"] for r in results], division, row["cls"])
    return {
        r["pdga_number"]: {
            "name": r["name"],
            "rating": r["rating"],
            "place": r["place"],
            "points": p,
        }
        for r, p in zip(results, pts)
    }


def compute(division: str) -> list[dict]:
    """Season standings for a division from all completed points events."""
    sched = schedule.load()
    per_player: dict[int, dict] = defaultdict(lambda: {"events": [], "name": None, "rating": None})

    for row in sched:
        if not row["completed"]:
            continue
        for pdga, rec in event_points(row, division).items():
            p = per_player[pdga]
            p["events"].append((row["tournament_id"], rec["points"], rec["place"], row["name"]))
            p["name"] = rec["name"]
            if rec["rating"]:
                p["rating"] = rec["rating"]  # most recent event rating

    # Event results carry the rating a player held WHEN THEY PLAYED, which
    # goes stale the moment PDGA publishes the monthly ratings update. Prefer
    # the current official rating wherever we have it ({} without API creds).
    official = ratings.current(division)

    table = []
    for pdga, p in per_player.items():
        total = points.season_total([(tid, pts) for tid, pts, _, _ in p["events"]], division)
        table.append(
            {
                "pdga_number": pdga,
                "name": p["name"],
                "rating": official.get(pdga) or p["rating"],
                "starts": len(p["events"]),
                "points": total,
                "events": p["events"],
            }
        )
    table.sort(key=lambda r: -r["points"])
    for i, r in enumerate(table, 1):
        r["rank"] = i
    return table


def write_csv(division: str, table: list[dict] | None = None) -> None:
    table = table or compute(division)
    out = config.DATA_DIR / f"standings_{division.lower()}_{config.SEASON}.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["rank", "pdga_number", "name", "rating", "starts", "points"])
        for r in table:
            w.writerow([r["rank"], r["pdga_number"], r["name"], r["rating"], r["starts"], r["points"]])
