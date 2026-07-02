"""Project who plays each remaining event.

Priority order:
1. Manual overrides (data/overrides/fields.csv: tournament_id,pdga_number,plays)
2. The real registered field from PDGA Live, once the event is loaded there
   (usually a few days out): round 1 exists with the full player list.
3. Participation rates from this season's actual starts, shrunk toward a
   cohort prior. Cohorts are (2026 tour-card qualified x European), per
   event group (US stops / European swing / JomezPro Series) — card holders
   play nearly everything stateside; only some Europeans cross the pond.
"""
from __future__ import annotations

import csv
import unicodedata
import urllib.error
from collections import defaultdict

from . import config, live_api

OVERRIDES_CSV = config.DATA_DIR / "overrides" / "fields.csv"

# The 2026 European swing: European Open (major), Swedish, Ale, Heinola.
# Grouping completed EU events separately keeps a European player's EU starts
# from inflating their projected participation at US fall events.
EU_TIDS = {97339, 96411, 96412, 96413}

EU_COUNTRIES = {
    "FI", "SE", "EE", "NO", "DK", "LV", "LT", "CZ", "IS", "GB", "DE", "NL",
    "BE", "FR", "AT", "CH", "PL", "ES", "PT", "IT", "IE", "SK", "HU", "SI",
}

SHRINKAGE = 3.0  # pseudo-events pulling a player's observed rate toward the cohort prior


def _norm_name(name: str) -> str:
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    return " ".join(s.lower().split())


def _event_group(row: dict) -> str:
    if row["cls"] == "jomez":
        return "jomez"
    return "eu" if row["tournament_id"] in EU_TIDS else "us"


def load_tour_card_names(division: str) -> set[str]:
    """Normalized names of 2026 tour-card *qualified* players (StatMando).

    Qualification, not purchase — the purchase list isn't public — but it is
    the population eligible for guaranteed entry to every Elite event.
    """
    path = config.DATA_DIR / f"tourcard_2026_{division.lower()}.csv"
    if not path.exists():
        return set()
    with open(path, newline="", encoding="utf-8") as f:
        return {_norm_name(r["name"]) for r in csv.DictReader(f)}


def load_countries() -> dict[int, str]:
    path = config.DATA_DIR / "player_countries.csv"
    if not path.exists():
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        return {int(r["pdga_number"]): r["country"] for r in csv.DictReader(f)}


def participation_rates(sched: list[dict], player_events: dict[int, set[int]], division: str,
                        player_names: dict[int, str] | None = None) -> dict[int, dict[str, float]]:
    """Per-player P(plays) by event group: observed rate shrunk to cohort prior."""
    completed_by_group: dict[str, set[int]] = defaultdict(set)
    for row in sched:
        if row["completed"] and row[division.lower()] and row["cls"] != "championship":
            completed_by_group[_event_group(row)].add(row["tournament_id"])

    card_names = load_tour_card_names(division)
    countries = load_countries()
    player_names = player_names or {}

    def cohort(pdga: int) -> tuple[bool, bool]:
        has_card = _norm_name(player_names.get(pdga, "")) in card_names
        is_euro = countries.get(pdga, "US") in EU_COUNTRIES
        return has_card, is_euro

    # cohort priors: average starts per event across the cohort, per group
    starts_sum: dict[tuple, float] = defaultdict(float)
    members: dict[tuple, int] = defaultdict(int)
    for pdga, tids in player_events.items():
        c = cohort(pdga)
        for group, group_tids in completed_by_group.items():
            starts_sum[(c, group)] += len(tids & group_tids) / max(len(group_tids), 1)
        members[c] += 1
    prior = {
        (c, g): starts_sum[(c, g)] / members[c]
        for c in {cohort(p) for p in player_events}
        for g in completed_by_group
    }

    rates: dict[int, dict[str, float]] = {}
    for pdga, tids in player_events.items():
        c = cohort(pdga)
        rates[pdga] = {}
        for group, group_tids in completed_by_group.items():
            n = len(group_tids)
            observed = len(tids & group_tids)
            p0 = prior.get((c, group), 0.0)
            rates[pdga][group] = (observed + SHRINKAGE * p0) / (n + SHRINKAGE)
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
