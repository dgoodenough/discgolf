"""Build the 2026 points-eligible schedule from the PDGA API.

Elite Series events are tier=ES with the class encoded in the event name
(DGPT- / DGPT+ / DGPT Playoffs / Powerball Cup / Doubles). Pro Majors are
tier=M. JomezPro Series events are found by name (they carry 'JomezPro' in
their PDGA listing).
"""
from __future__ import annotations

import csv
import datetime as dt
import re

from . import config, live_api
from .pdga_api import PDGAClient

SCHEDULE_CSV = config.DATA_DIR / "schedule_2026.csv"
FIELDS = [
    "tournament_id", "name", "cls", "start_date", "end_date",
    "mpo", "fpo", "fpo_points", "completed",
]


def classify_es(name: str, tournament_id: int) -> str:
    if tournament_id == config.TID_CHAMPIONSHIP or "Powerball Cup" in name:
        return "championship"
    if tournament_id == config.TID_DOUBLES or "Doubles" in name:
        return "doubles"
    if re.match(r"DGPT\s*\+", name):
        return "elite_plus"
    if "Playoffs" in name:
        return "playoff"
    return "elite"


def build(client: PDGAClient | None = None) -> list[dict]:
    client = client or PDGAClient()
    season = config.SEASON
    rows: list[dict] = []

    for e in client.events(tier="ES", start_date=f"{season}-01-01", end_date=f"{season}-12-31"):
        tid = int(e["tournament_id"])
        cls = classify_es(e["tournament_name"], tid)
        rows.append(_row(e, cls, mpo=True, fpo=True, fpo_points=tid != config.TID_HEINOLA))

    for e in client.events(tier="M", start_date=f"{season}-01-01", end_date=f"{season}-12-31"):
        tid = int(e["tournament_id"])
        if tid in config.MAJOR_TIDS_MPO:
            rows.append(_row(e, "major", mpo=True, fpo=True, fpo_points=True))
        elif tid == config.TID_USWDGC:
            rows.append(_row(e, "major", mpo=False, fpo=True, fpo_points=True))
        # all other M-tier events (Am/Masters/Junior worlds, USDGC=XM) are non-points

    # JomezPro Series: A-tier listings carrying "JomezPro" in the name
    jomez = [
        e for e in client.events(
            tier="A", start_date=f"{season}-01-01", end_date=f"{season}-12-31",
        )
        if "JomezPro" in e["tournament_name"]
        and "Finale" not in e["tournament_name"]  # Finale awards no points
    ]
    for e in jomez:
        config.JOMEZ_TIDS.add(int(e["tournament_id"]))
        rows.append(_row(e, "jomez", mpo=True, fpo=True, fpo_points=True))

    rows.sort(key=lambda r: r["start_date"])
    SCHEDULE_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(SCHEDULE_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    return rows


def live_events(rows: list[dict] | None = None) -> list[dict]:
    """Points events currently in progress.

    The window runs from the start date to one grace day past the end date,
    and closes the moment a refresh banks the event. Both halves are needed,
    and they pull in opposite directions.

    The grace day extends the window past the calendar. Sunday final rounds in
    US time zones finish after 00:00 UTC (6pm CDT = 23:00Z), so a purely
    date-based window shuts the live loop down mid-final-round: Ledgestone
    2026 froze on the second-to-last hole at 23:17Z and nothing recomputed
    until the Monday cron (the site showed the winner at 23% all night).

    `completed` closes it early, and this half used to be missing: an event
    finishes hours before its own last day is over, and the in-window test
    ignored the flag entirely, so a banked event went on reporting itself
    live. The 2026 doubles championship banked on the Sunday afternoon and the
    page kept flying the "LIVE now" banner over it — while the freshness
    check, held to the 25-minute threshold it uses during play, reddened the
    header for staleness when nothing further was coming (2026-08-16). It also
    stranded the post-event StatMando cross-check, which the live loop only
    runs once nothing is live.

    So: the scoreboard closes the window and the calendar only bounds it —
    the same order of authority `_row` applies when it decides to bank.
    """
    rows = rows if rows is not None else load()
    today = dt.date.today()
    out = []
    for r in rows:
        if r["completed"]:
            continue
        start = dt.date.fromisoformat(r["start_date"])
        end = dt.date.fromisoformat(r["end_date"])
        if start <= today <= end + dt.timedelta(days=1):
            out.append(r)
    return out


def load() -> list[dict]:
    with open(SCHEDULE_CSV, newline="", encoding="utf-8") as f:
        rows = []
        for r in csv.DictReader(f):
            r["tournament_id"] = int(r["tournament_id"])
            for k in ("mpo", "fpo", "fpo_points", "completed"):
                r[k] = r[k] == "True"
            rows.append(r)
        return rows


def _utc_now() -> dt.datetime:  # seam so tests can pin the grace-night clock
    return dt.datetime.now(dt.timezone.utc)


def _row(e: dict, cls: str, *, mpo: bool, fpo: bool, fpo_points: bool) -> dict:
    tid = int(e["tournament_id"])
    today = dt.date.today()
    start = dt.date.fromisoformat(e["start_date"])
    end = dt.date.fromisoformat(e["end_date"])
    completed = end < today
    # In progress: an event may finish before its end date passes. Grace
    # night: the reverse — a US Sunday finish runs past 00:00 UTC, and banking
    # on the date alone would cache a mid-final-round sheet as the permanent
    # result (final_results caches once the end date passes). In both cases
    # the scoreboard outranks the calendar. The grace override stops at 06:00
    # UTC — by then even a west-coast night finish is long done — so a player
    # abandoned mid-round without a WD marker (which reads as "still playing"
    # forever) can only delay banking a few hours, not past the Monday cron.
    in_grace_night = (
        end < today <= end + dt.timedelta(days=1) and _utc_now().hour < 6
    )
    if start <= today and (not completed or in_grace_night):
        try:
            completed = live_api.event_complete(tid)
        except Exception:
            pass  # keep the date-based answer
    return {
        "tournament_id": tid,
        "name": e["tournament_name"],
        "cls": cls,
        "start_date": e["start_date"],
        "end_date": e["end_date"],
        "mpo": mpo,
        "fpo": fpo,
        "fpo_points": fpo_points,
        "completed": completed,
    }
