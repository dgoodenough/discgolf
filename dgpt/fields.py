"""Project who plays each remaining event.

Priority order:
1. Manual overrides (data/overrides/fields.csv: tournament_id,pdga_number,plays)
2. The real registered field from PDGA Live, once the event is loaded there
   (usually a few days out): round 1 exists with the full player list.
3. Participation rates from this season's actual starts, shrunk toward a
   cohort prior. Cohorts are (2026 tour-card qualified x European), per
   event group (US stops / European swing / JomezPro Series) — card holders
   play nearly everything stateside; only some Europeans cross the pond.

The qualification-gated events (the two playoffs, the Worlds play-in) take
none of that: they are invited in waves off the standings, so simulate.py
builds their fields from the published signup list (`signed_up`) unioned with
the standings gate for players whose window has not opened yet.
"""
from __future__ import annotations

import csv
import unicodedata
import urllib.error
from collections import defaultdict
from typing import NamedTuple

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


class Signups(NamedTuple):
    """Who has entered a qualification-gated event, and how done that list is.

    `final` is the difference between "these players are in, and others may
    still join" and "this is the field". Callers union a non-final list with
    whatever other route into the field they model; a final one replaces it.
    """
    players: set[int]
    final: bool


def signed_up(tournament_id: int, division: str, players: list[int]) -> Signups | None:
    """Which of `players` have actually signed up for a qualification-gated event.

    The playoffs and the Worlds play-in do not take open registration: the
    DGPT and the PDGA invite in waves, and the list is public on the event
    page long before PDGA Live knows the event exists. Once a list exists we
    take it literally — a player on it plays, whatever the standings say, and
    a tour-card holder who entered GMC in March needs no separate model.

    Returns None when no list has been published, which is the signal to keep
    the pre-signup assumption (a pure standings gate) rather than to read an
    empty list as "nobody is playing". Manual overrides in
    data/overrides/fields.csv win over the published list, in both directions
    — and an override makes the list non-final, since the reason to write one
    is that the published list is wrong or incomplete.
    """
    listed = live_api.registration_list(tournament_id, division)
    ov = {pdga: plays for (tid, pdga), plays in load_overrides().items()
          if tid == tournament_id}
    if listed is None and not ov:
        return None
    entries, final = listed if listed is not None else ({}, False)
    seen = set(entries)
    return Signups({p for p in players if ov.get(p, p in seen)}, final and not ov)


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
    # Trust the PDGA Live registration list whenever it exists. Large fields
    # (e.g. Ledgestone ~150, Pro Worlds ~200) are real: those events split
    # MPO/FPO across separate courses, so the field runs bigger than a normal
    # Elite stop. Only the playoffs + Powerball Cup lack one here (they are
    # qualification-based), where we fall back to participation rates — and
    # for the playoffs that answer is discarded anyway: simulate.py rebuilds
    # their fields from signups + the standings gate, which it can only do
    # once the standings for that sim exist.
    known = registered_field(row["tournament_id"], division)
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
