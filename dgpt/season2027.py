"""The 2027 schedule, and every assumption the 2027 forecast rests on.

The 2026 season is modelled from facts: real results, real registration lists,
real published qualification rules. 2027 has none of that. All that exists is
the schedule announcement (dgpt.com, 2026), so this module is where the gap
between "announced" and "modellable" is written down explicitly, in one place,
rather than being spread through the engine as magic numbers.

Two tours are described here and they are experimental to different degrees:

  DGPT 2027 (`DGPT`)
      The schedule is published, and the announcement says the postseason
      changes shape (Playoffs move behind the USDGC, which joins the MPO
      points race) but says nothing about the points structure changing. So
      the assumption is the null one: 2026's curves, multipliers, counting
      caps, qualification ladder and Cup seed table, applied to 2027's
      calendar. What is genuinely new is the calendar itself — seven JomezPro
      stops instead of three, a fourth MPO major, two new playoff venues —
      and that is data, not modelling.

  EuroTour 2027 (`EUROTOUR`)
      The announcement describes a *structure* (which events belong, and that
      the standings award 2028 Tour Cards) and publishes no points table, no
      counting rule and no card allocation. Every number in EUROTOUR below is
      therefore an assumption of ours, not a rule of theirs. They are grouped
      in ET_* constants so they can be corrected in one edit when the DGPT
      publishes the real thing, and every one of them is surfaced on the page
      under "what this assumes" rather than presented as a fact.

Event ids are OURS. The PDGA has not assigned 2027 tournament ids, so the
schedule carries synthetic 2700xx ids: stable keys for the pipeline, never
shown as PDGA links, and replaced wholesale once the real schedule appears in
the API.
"""
from __future__ import annotations

import csv
import datetime as dt
from dataclasses import dataclass, field
from functools import lru_cache

from . import config, points

SEASON = 2027
SCHEDULE_CSV = config.DATA_DIR / "schedule_2027.csv"

# Rounds are read off the calendar: a three-day stop plays three rounds, a
# four-day one four, and Pro Worlds' five-day window five. That is the rule
# the 2026 schedule obeys at every single event, and with no PDGA round plan
# to ask (the events do not exist yet) the dates are the only evidence there
# is. It matters more than it looks: round count is the sole term separating
# a 3-round Elite stop from a 4-round major in the score model's variance.
def rounds_for(start_date: str, end_date: str) -> int:
    return (dt.date.fromisoformat(end_date) - dt.date.fromisoformat(start_date)).days + 1


@dataclass(frozen=True)
class Event:
    """One 2027 event, as announced."""
    event_id: int
    name: str
    short_name: str
    location: str
    cls: str          # elite / elite_plus / doubles / major / jomez / playoff /
                      # championship / et_a / et_champs / et_nat
    tour: str         # dgpt / eurotour / both
    start_date: str
    end_date: str
    mpo: bool
    fpo: bool

    @property
    def rounds(self) -> int:
        return rounds_for(self.start_date, self.end_date)

    def on(self, tour: str) -> bool:
        return self.tour == tour or self.tour == "both"

    def plays(self, division: str) -> bool:
        return self.mpo if division == "MPO" else self.fpo


@lru_cache(maxsize=1)
def load() -> list[Event]:
    with open(SCHEDULE_CSV, newline="", encoding="utf-8") as f:
        rows = [
            Event(
                event_id=int(r["event_id"]), name=r["name"], short_name=r["short_name"],
                location=r["location"], cls=r["cls"], tour=r["tour"],
                start_date=r["start_date"], end_date=r["end_date"],
                mpo=r["mpo"] == "True", fpo=r["fpo"] == "True",
            )
            for r in csv.DictReader(f)
        ]
    return sorted(rows, key=lambda e: (e.start_date, e.event_id))


# --------------------------------------------------------------- points

# EuroTour points, entirely assumed (see the module docstring). Expressed on
# the same base curve as everything else so the two tours stay comparable: a
# EuroTour A-Tier win is worth an Elite Series win, and the two events the
# announcement singles out as the tour's peaks — the European Open and the
# European Disc Golf Championships, which "will award EuroTour points
# alongside" it — are worth a major.
#
# The national/regional championship weekend is the one deliberately low
# multiplier. Those are country-level fields of wildly different depth, they
# are opt-in for the organisers ("can apply for EuroTour points sanctioning"),
# and treating a national title as half an A-Tier is the conservative reading.
ET_MULTIPLIERS = {
    "et_a": 1.0,          # RPM Open, Turku Open, the Pärnu Finale
    "et_champs": 2.0,     # European Disc Golf Championships
    "et_nat": 0.5,        # the national/regional championship weekend
    "elite": 1.0,         # the two DGPT European Elite Series stops
    "major": 2.0,         # the European Open
}

# How many results count toward the EuroTour standings. Assumed. Eight events
# are reachable in 2027 (three A-Tiers, two Elite Series stops, the European
# Open, the European Championships, a national title); counting the best five
# keeps the race open to players who cannot travel to all of them, which is
# the stated point of the structure.
ET_COUNT = 5

# 2028 card allocation off the final standings. The announcement says the
# EuroTour "will continue to award Full Tour Cards and EuroTour Cards" and
# names no numbers, so these are ours. Shown on the page as assumed.
ET_CARDS = {
    "MPO": {"full": 3, "card": 7},
    "FPO": {"full": 2, "card": 4},
}


def curve(division: str, cls: str, tour: str) -> dict[int, float]:
    """Points by finishing place for one event, on one tour's ledger.

    An event can pay into both: the European Open is a DGPT major AND a
    EuroTour event, and the two ledgers value it differently, so the tour
    being scored is part of the question. DGPT scoring is 2026's engine
    untouched; EuroTour scoring is ET_MULTIPLIERS on the same base curve.
    """
    if tour == "dgpt":
        return points.event_curve(division, cls)
    base = points.base_curves()[division]
    mult = ET_MULTIPLIERS[cls]
    return {p: v * mult for p, v in base.items()}


# --------------------------------------------------------- tour rulesets

@dataclass(frozen=True)
class Pool:
    """One counting bucket: which classes feed it, and how many count."""
    name: str
    classes: tuple[str, ...]
    keep: int | None      # None = every result counts (bonus pools)


@dataclass(frozen=True)
class TourSpec:
    """Everything that separates one tour's season from another's."""
    key: str                       # url/file slug
    label: str
    tour: str                      # which schedule rows belong to it
    pools: tuple[Pool, ...]
    # DGPT-style postseason. None for a tour that just runs a standings race.
    playoff1: str | None = None    # short_name of the first playoff event
    playoff2: str | None = None
    championship: str | None = None
    european_only: bool = False    # roster restricted to European players
    # Which pools' classes grant the Cup's event-winner special invite.
    invite_classes: tuple[str, ...] = ()
    # EuroTour card bands, as (label, last rank in the band).
    card_bands: tuple[tuple[str, str], ...] = ()
    notes: tuple[str, ...] = field(default=())


DGPT = TourSpec(
    key="2027",
    label="2027 DGPT",
    tour="dgpt",
    # 2026's per-class caps, unchanged. The pools are the same shape; only the
    # calendar feeding them is new. 14 DGPT-pool events in 2027 against 15 in
    # 2026, so "best 10" still drops four or five; seven JomezPro stops
    # against three makes the bonus pool much larger, which is the single
    # biggest structural change to a player's total.
    pools=(
        Pool("dgpt", ("elite", "elite_plus", "doubles"), config.COUNT_DGPT),
        Pool("playoff", ("playoff",), config.COUNT_PLAYOFF),
        Pool("major", ("major",), config.COUNT_MAJOR),
        Pool("jomez", ("jomez",), None),
    ),
    playoff1="Ivy Hill",
    playoff2="Kansas City Wide Open",
    championship="Powerball Cup",
    invite_classes=("elite", "elite_plus", "major", "playoff"),
    notes=(
        "Points curves, class multipliers and the per-class counting caps are "
        "2026's, applied unchanged — the announcement changes the calendar and "
        "the postseason cadence, not the points structure.",
        "The playoff ladder keeps its 2026 shape at its new venues: Ivy Hill "
        "inherits the Green Mountain Championship's qualification window, the "
        "Kansas City Wide Open inherits the MVP Open's, and the top finishers "
        "at the first playoff event advance to the second.",
        "The USDGC counts as an MPO major for the first time, so MPO now has "
        "four majors and still counts its best two. Its field is invitational "
        "and its invitation rules are not out, so attendance there is modelled "
        "like any other US stop — that is the weakest single assumption on "
        "this tab.",
    ),
)

EUROTOUR = TourSpec(
    key="et",
    label="2027 EuroTour",
    tour="eurotour",
    pools=(Pool("et", ("et_a", "et_champs", "et_nat", "elite", "major"), ET_COUNT),),
    european_only=True,
    card_bands=(("Full Tour Card", "full"), ("EuroTour Card", "card")),
    notes=(
        "No EuroTour points table has been published. Every event here is "
        "scored off the same base curve the DGPT uses, at an assumed "
        "multiplier: A-Tier and Elite Series stops at Elite Series scale, the "
        "European Open and the European Championships at major scale, and a "
        "national or regional championship at half an A-Tier.",
        f"The standings are assumed to count each player's best {ET_COUNT} "
        "results of the eight reachable events. No counting rule has been "
        "announced.",
        "The national championship weekend is modelled as a set of parallel "
        "single-country fields: a player can only enter their own country's "
        "championship, and is ranked against their compatriots alone. "
        "Countries with almost nobody in this table are pooled into one "
        "regional championship instead, so a lone entrant cannot win a "
        "national title unopposed every season. Which federations will "
        "actually apply for points sanctioning is unknown.",
        "The table is built from players who appear in the 2026 DGPT World "
        "Standings. A European player who plays only domestic events has no "
        "row here at all, and in a real EuroTour standings they certainly "
        "would — this is the tab's biggest blind spot.",
        "2028 card allocations are assumed, not announced.",
    ),
)

SPECS = {spec.key: spec for spec in (DGPT, EUROTOUR)}


# ------------------------------------------------------- qualification

# 2026's ladder, re-pointed at 2027's venues. `cut` is the points-qualification
# line, `fill` the size the field expands to if the primary window does not
# fill, `perf` the number of top finishers at the first playoff event who
# advance to the second without qualifying on points.
#
# There is no signup list to read for any of this — 2027 registration has not
# opened and will not for a year — so unlike the live 2026 model these gates
# are the whole field model, with every qualifier assumed to attend.
PLAYOFF_QUAL = {
    "playoff1": config.PLAYOFF_QUAL["gmc"],
    "playoff2": config.PLAYOFF_QUAL["mvp"],
}

STANDINGS_CUT = {"MPO": 28, "FPO": 18}   # direct Cup qualification
FIELD_SIZE = {"MPO": 32, "FPO": 20}      # Cup field, incl. the performance path
CUP_ROUNDS = 4                           # Nov 4-7, four days

# Which 2026 participation-rate group a 2027 event borrows. The groups are
# fields._event_group's: US stops, the European swing, and the JomezPro
# Series. Everything on the European side of the calendar — DGPT or EuroTour —
# reads the European rate, because that is the rate that measures whether a
# given player crosses an ocean or does not have to.
def rate_group(ev: Event) -> str:
    if ev.cls == "jomez":
        return "jomez"
    if ev.tour in ("eurotour", "both"):
        return "eu"
    return "us"
