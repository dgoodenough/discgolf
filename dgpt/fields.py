"""Project who plays each remaining event.

Priority order:
1. Manual overrides (data/overrides/fields.csv: tournament_id,pdga_number,plays)
2. The real registered field from PDGA Live, once the event is loaded there
   (usually a few days out): round 1 exists with the full player list.
3. Participation rates from this season's actual starts, split by event
   group — US tour stops, the European swing, and JomezPro Series events
   draw meaningfully different fields.
"""
from __future__ import annotations

import csv
import urllib.error
from collections import defaultdict

from . import config, live_api

OVERRIDES_CSV = config.DATA_DIR / "overrides" / "fields.csv"

# Remaining 2026 European swing (Swedish Open / European Open already banked)
EU_TIDS = {96412, 96413}  # Ale Open, Heinola Open


def _event_group(row: dict) -> str:
    if row["cls"] == "jomez":
        return "jomez"
    return "eu" if row["tournament_id"] in EU_TIDS else "us"


def participation_rates(sched: list[dict], player_events: dict[int, set[int]], division: str) -> dict[int, dict[str, float]]:
    """Per-player P(plays) by event group, from completed-event starts."""
    completed_by_group: dict[str, set[int]] = defaultdict(set)
    for row in sched:
        if row["completed"] and row[division.lower()] and row["cls"] != "championship":
            completed_by_group[_event_group(row)].add(row["tournament_id"])

    rates: dict[int, dict[str, float]] = {}
    for pdga, tids in player_events.items():
        rates[pdga] = {}
        for group, group_tids in completed_by_group.items():
            n = len(group_tids)
            rates[pdga][group] = (len(tids & group_tids) / n) if n else 0.0
        # No completed events in a group yet -> fall back to US rate
        for group in ("us", "eu", "jomez"):
            rates[pdga].setdefault(group, rates[pdga].get("us", 0.0))
    return rates


def registered_field(tournament_id: int, division: str) -> set[int] | None:
    """Registered players from PDGA Live if the event is already loaded."""
    try:
        scores = live_api.fetch_round(tournament_id, division, 1).get("scores") or []
    except (urllib.error.HTTPError, KeyError):
        return None
    field = {s["PDGANum"] for s in scores if s.get("PDGANum")}
    return field or None


def load_overrides() -> dict[tuple[int, int], bool]:
    out: dict[tuple[int, int], bool] = {}
    if OVERRIDES_CSV.exists():
        with open(OVERRIDES_CSV, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                out[(int(r["tournament_id"]), int(r["pdga_number"]))] = r["plays"].strip() in ("1", "true", "True")
    return out


def play_probabilities(row: dict, division: str, players: list[int],
                       rates: dict[int, dict[str, float]],
                       overrides: dict[tuple[int, int], bool]) -> dict[int, float]:
    """P(plays) for each player at one remaining event."""
    # Registration lists for far-out events still include the waitlist
    # (e.g. 146 "registered" a month early vs ~108 field spots), so only
    # trust them close to the event.
    import datetime as dt

    days_out = (dt.date.fromisoformat(row["start_date"]) - dt.date.today()).days
    known = registered_field(row["tournament_id"], division) if days_out <= 14 else None
    group = _event_group(row)
    probs: dict[int, float] = {}
    for pdga in players:
        if (row["tournament_id"], pdga) in overrides:
            probs[pdga] = 1.0 if overrides[(row["tournament_id"], pdga)] else 0.0
        elif known is not None:
            probs[pdga] = 1.0 if pdga in known else 0.0
        else:
            probs[pdga] = rates.get(pdga, {}).get(group, 0.0)
    return probs
