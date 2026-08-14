"""Compute current-season DGPT World Standings from actual results."""
from __future__ import annotations

import csv
from collections import defaultdict

from . import config, live_api, points, ratings, schedule


def _doubles_points(results: list[dict], division: str, tid: int) -> dict[int, dict]:
    """Points earned at the doubles championship, paid to BOTH team members.

    A team throws one card and PDGA publishes one row for it — the entrant of
    record's — so the partner is nowhere on the results sheet. Banking the
    sheet as-is paid the entrant and gave their partner nothing for an event
    they played and, at the top of the leaderboard, won.

    The pairing (live_api.doubles_teams — PDGA Live's Teammates fields, or the
    registration page) is what recovers them. It is also why the points are
    computed over TEAMS rather than rows: the doubles curve is indexed by team
    place, so tie averaging has to see each team once. Paying members off an
    expanded row list would read every team as a two-way tie and average the
    curve across the place below it (a solo win would bank 123.75 instead of
    137.5) — and that is exactly what would happen on a sheet that does list
    both members. Grouping first is correct under either shape.

    With no pairing available (both sources dark) every row stands alone,
    which is the behaviour this replaces.
    """
    pairing = live_api.doubles_teams(tid, division)
    teams: dict[frozenset, dict] = {}
    for r in results:
        pdga = r["pdga_number"]
        partner = (pairing.get(pdga) or {}).get("partner")
        team = teams.setdefault(
            frozenset({pdga, partner} if partner else {pdga}),
            {"place": r["place"], "members": {}},
        )
        team["place"] = min(team["place"], r["place"])
        team["members"][pdga] = {"name": r["name"], "rating": r["rating"]}
    for key, team in teams.items():
        # the partner has no row: name comes from the pairing, and the rating
        # from the official snapshot compute() overlays afterwards
        listed = next(iter(team["members"]))
        for pdga in key - set(team["members"]):
            team["members"][pdga] = {
                "name": (pairing.get(listed) or {}).get("partner_name"),
                "rating": None,
            }

    ordered = sorted(teams.values(), key=lambda t: t["place"])
    pts = points.assign_points([t["place"] for t in ordered], division, "doubles")
    out: dict[int, dict] = {}
    for team, p in zip(ordered, pts):
        for pdga, member in team["members"].items():
            out[pdga] = {
                "name": member["name"],
                "rating": member["rating"],
                "place": team["place"],
                "points": p,
            }
    return out


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
    if row["cls"] == "doubles":
        return _doubles_points(results, division, row["tournament_id"])
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
            # a doubles partner is known by their pairing entry, which can be
            # nameless; never let that blank out a name another event supplied
            p["name"] = rec["name"] or p["name"]
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
