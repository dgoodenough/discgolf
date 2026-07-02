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

from . import config
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


def load() -> list[dict]:
    with open(SCHEDULE_CSV, newline="", encoding="utf-8") as f:
        rows = []
        for r in csv.DictReader(f):
            r["tournament_id"] = int(r["tournament_id"])
            for k in ("mpo", "fpo", "fpo_points", "completed"):
                r[k] = r[k] == "True"
            rows.append(r)
        return rows


def _row(e: dict, cls: str, *, mpo: bool, fpo: bool, fpo_points: bool) -> dict:
    return {
        "tournament_id": int(e["tournament_id"]),
        "name": e["tournament_name"],
        "cls": cls,
        "start_date": e["start_date"],
        "end_date": e["end_date"],
        "mpo": mpo,
        "fpo": fpo,
        "fpo_points": fpo_points,
        "completed": dt.date.fromisoformat(e["end_date"]) < dt.date.today(),
    }
