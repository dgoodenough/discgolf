"""The standings if the live event ended right now.

The forecast assumes every scheduled round gets played. When weather
threatens to end an event early, that assumption is the one thing the reader
most needs to see past: a called-off event is scored on what has been
counted so far, and — at the last event before the Cup — that settles the
regular season on the spot. This is that scenario, computed rather than
simulated: no draws, one answer.

Everything that decides points is reused, not re-implemented: the counted
leaderboard comes from live_api.counted_standing (which applies the
holes-everyone-completed rule), the event's points from
points.assign_points, and the season totals from points.season_total, so the
per-class caps are the ones the official standings use.

It is a side panel to the forecast, not part of it, and must never take the
forecast down with it: run() catches everything and returns None.
"""
from __future__ import annotations

import sys

from . import config, live_api, points, schedule

# no singles DGPT/Major win invite from these (mirrors simulate._split_banked)
_NO_INVITE_CLS = {"jomez", "doubles"}


def compute(division: str, table: list[dict], sched: list[dict], cut: int,
            field_size: int) -> dict | None:
    """{"meta": {...}, "players": {pdga: {...}}} for the live event(s), or None.

    `table` is standings.compute's rows (a player's banked `events`). Only
    singles events are frozen: doubles would need the team pairing, and no
    live doubles event can end the regular season.
    """
    live_rows = [r for r in schedule.live_events(sched)
                 if r[division.lower()] and r["cls"] not in ("doubles", "championship")
                 and not (division == "FPO" and not r["fpo_points"])]
    if not live_rows:
        return None

    frozen: dict[int, dict] = {}   # tid -> {"row", "standing", "pts": {pdga: pts}}
    for row in live_rows:
        standing = live_api.counted_standing(row["tournament_id"], division)
        if not standing:
            continue
        ordered = sorted(standing["players"].items(), key=lambda kv: kv[1]["place"])
        pts = points.assign_points([v["place"] for _, v in ordered], division, row["cls"])
        frozen[row["tournament_id"]] = {
            "row": row, "standing": standing,
            "pts": {p: v for (p, _), v in zip(ordered, pts)},
        }
    if not frozen:
        return None

    # season totals with the frozen results banked alongside the real ones
    by_pdga = {r["pdga_number"]: r for r in table}
    everyone = set(by_pdga)
    for f in frozen.values():
        everyone |= set(f["standing"]["players"])
    rows = []
    for pdga in everyone:
        banked = [(tid, pts) for tid, pts, *_ in (by_pdga.get(pdga) or {}).get("events", [])]
        extra = [(tid, f["pts"][pdga]) for tid, f in frozen.items() if pdga in f["pts"]]
        rows.append({
            "pdga": pdga,
            "total": points.season_total(banked + extra, division),
            "was": (by_pdga.get(pdga) or {}).get("rank", 10**6),
        })
    rows.sort(key=lambda r: (-r["total"], r["was"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i

    # Cup field — only meaningful when nothing but the Cup is left after the
    # frozen event(s): then this IS the final regular-season table.
    frozen_tids = set(frozen)
    decides = all(r["completed"] or r["tournament_id"] in frozen_tids or r["cls"] == "championship"
                  for r in sched if r[division.lower()])
    cup: dict[int, str] = {}
    strokes: dict[int, int] = {}
    if decides:
        rank_of = {r["pdga"]: r["rank"] for r in rows}
        for r in rows:
            if r["rank"] <= cut:
                cup[r["pdga"]] = "auto"
        # MVP-performance bids: the best MVP Open finishers outside the cut
        mvp = frozen.get(config.TID_MVP)
        if mvp:
            elig = sorted(
                ((v["place"], rank_of[p], p) for p, v in mvp["standing"]["players"].items()
                 if p not in cup),
            )
            for _, _, p in elig[: field_size - cut]:
                cup[p] = "mvp"
        # event winners' special invite: banked wins, plus a frozen outright win
        for r in table:
            if r["pdga_number"] in cup:
                continue
            if any(place == 1 and _invite_tid(tid, sched) for tid, _, place, _ in r["events"]):
                cup[r["pdga_number"]] = "invite"
        for f in frozen.values():
            if f["row"]["cls"] in _NO_INVITE_CLS:
                continue
            winners = [p for p, v in f["standing"]["players"].items() if v["place"] == 1]
            if len(winners) == 1 and winners[0] not in cup:
                cup[winners[0]] = "invite"
        ladder = config.cup_start_strokes(division)
        for p in cup:
            strokes[p] = ladder[min(rank_of[p], len(ladder)) - 1]

    players: dict[int, dict] = {}
    for r in rows:
        p = r["pdga"]
        ev = {tid: {"place": f["standing"]["players"][p]["place"],
                    "cur": f["standing"]["players"][p]["cur"],
                    "pts": f["pts"][p]}
              for tid, f in frozen.items() if p in f["pts"]}
        players[p] = {
            "rank": r["rank"],
            "points": r["total"],
            "event": ev,
            **({"cup": cup.get(p), "strokes": strokes.get(p)} if decides else {}),
        }
    return {
        "meta": {
            "events": [
                {"tid": tid, "name": f["row"]["name"], **f["standing"]["basis"]}
                for tid, f in frozen.items()
            ],
            # true when the frozen table is the final regular-season table
            "decides_cup": decides,
        },
        "players": players,
    }


def _invite_tid(tid: int, sched: list[dict]) -> bool:
    cls = next((r["cls"] for r in sched if r["tournament_id"] == tid), "elite")
    return cls not in _NO_INVITE_CLS and cls != "championship"


def run(division: str, table: list[dict], sched: list[dict], cut: int,
        field_size: int) -> dict | None:
    """compute(), but a failure here is a missing panel, never a failed run."""
    try:
        return compute(division, table, sched, cut, field_size)
    except Exception as e:  # noqa: BLE001 — a side panel must not wedge the publish
        print(f"WARNING as-is standings ({division}) skipped: {e!r}", file=sys.stderr)
        return None
