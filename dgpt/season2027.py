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
      The DGPT published this one in full: three points categories paying 250 /
      200 / 150 for a win, counted 1-of-2, 2-of-3 and 3-of-3, which is where
      both "keep your top 6 finishes" and the 1,100-point perfect season come
      from. ET_CATEGORY is that table, and the card bands are theirs too. Two
      things are still ours and are labelled as such on the page: the shape of
      each curve BELOW first place (only the win value is published, so the
      DGPT's own curve is scaled to it), and who is in the table at all.

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
    et_cat: int | None  # EuroTour points category (1-3); None off that tour
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
                et_cat=int(r["et_cat"]) if r["et_cat"] else None,
                start_date=r["start_date"], end_date=r["end_date"],
                mpo=r["mpo"] == "True", fpo=r["fpo"] == "True",
            )
            for r in csv.DictReader(f)
        ]
    return sorted(rows, key=lambda e: (e.start_date, e.event_id))


# --------------------------------------------------------------- points

# The 2027 EuroTour points structure, as published. Three categories, each
# paying a fixed amount for a win and each counting a fixed number of results:
#
#   1   250   European Championships, European Open           best 1 of 2
#   2   200   both European Elite Series stops, Pärnu Open    best 2 of 3
#   3   150   RPM Open, Turku Open, your National Championship    3 of 3
#
# Six results counted in total, which is the announcement's "players keep
# their top 6 finishes", and 250 + 400 + 450 = 1,100 for the perfect season it
# quotes. That arithmetic is the check that this table has been read right, and
# `max_points` below asserts it.
#
# `keep` is what makes the categories more than a points scale. Category 1 is
# the only place a European can bank 250, and only once: winning both the
# European Championships and the European Open is worth no more than winning
# either. Category 3 discards nothing, so its three events are pure addition —
# which is what gives a player who cannot travel much a floor to build on, and
# is the stated point of the structure.
ET_CATEGORY = {
    1: {"win": 250.0, "keep": 1,
        "label": "European Championships & European Open"},
    2: {"win": 200.0, "keep": 2,
        "label": "European Elite Series stops & the Pärnu Open"},
    3: {"win": 150.0, "keep": 3,
        "label": "RPM Open, Turku Open & National Championships"},
}

# Only the win value is published, so the shape below first place is ours: the
# DGPT's own per-place curve, scaled so that first pays the category's number.
# Category 3's scale factor comes out at exactly 1.0 — a EuroTour category-3
# win is an Elite Series win — and 2 and 3 land on 4/3 and 5/3, which are the
# DGPT's own DGPT+ and Playoff multipliers. Three exact hits is unlikely to be
# a coincidence, but it is still an inference, and it is labelled as one.
def et_multiplier(division: str, category: int) -> float:
    return ET_CATEGORY[category]["win"] / points.base_curves()[division][1]


def max_points() -> float:
    """The perfect season: every counted result a win. The DGPT says 1,100."""
    return sum(c["win"] * c["keep"] for c in ET_CATEGORY.values())


# 2028 Tour Cards off the final EuroTour standings, as published. Both bands
# are cumulative ranks, not counts: in MPO the top 6 take a Full Tour Card and
# everyone through 24th takes a EuroTour Card, so the EuroTour Card band is
# 7th-24th once the Full cards are dealt.
#
# NOT modelled: displacement. A European who has already earned a Full Tour
# Card through the DGPT World Standings passes their EuroTour Card spot down,
# and a Full Tour Card winner may elect to take a EuroTour Card instead. Both
# only ever push cards FURTHER down the standings, so the odds this model
# publishes are a floor for players just outside a band, never a ceiling.
ET_CARDS = {
    "MPO": {"full": 6, "card_through": 24},
    "FPO": {"full": 3, "card_through": 12},
}


def curve(division: str, ev: "Event", tour: str) -> dict[int, float]:
    """Points by finishing place for one event, on one tour's ledger.

    An event can pay into both, at different values: the European Open is a
    DGPT major (300 for a win) and a EuroTour category-1 event (250), so the
    tour being scored is part of the question. DGPT scoring is 2026's engine
    untouched; EuroTour scoring is the published category value on the same
    base curve.
    """
    if tour == "dgpt":
        return points.event_curve(division, ev.cls)
    mult = et_multiplier(division, ev.et_cat)
    return {p: v * mult for p, v in points.base_curves()[division].items()}


# --------------------------------------------------------- tour rulesets

@dataclass(frozen=True)
class Pool:
    """One counting bucket: what feeds it, and how many results count.

    The two tours bucket by different things and both are first-class here.
    The DGPT counts by event CLASS — best 10 of anything Elite-ish, best 2
    majors — while the EuroTour counts by published points CATEGORY, which
    cuts across class: the Pärnu Open and the Turku Open are both A-Tiers and
    they sit in different categories.
    """
    name: str
    keep: int | None                  # None = every result counts (bonus pools)
    classes: tuple[str, ...] = ()     # DGPT: event classes feeding this pool
    cats: tuple[int, ...] = ()        # EuroTour: points categories feeding it
    label: str = ""                   # what the page calls it

    def holds(self, ev: "Event") -> bool:
        return ev.cls in self.classes or (ev.et_cat is not None and ev.et_cat in self.cats)


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
    # True when the tour's prize is the 2028 card bands rather than a Cup.
    cards: bool = False
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
        Pool("dgpt", config.COUNT_DGPT, classes=("elite", "elite_plus", "doubles"),
             label="DGPT & DGPT+ (incl. the doubles championship)"),
        Pool("playoff", config.COUNT_PLAYOFF, classes=("playoff",), label="Playoffs"),
        Pool("major", config.COUNT_MAJOR, classes=("major",), label="Majors"),
        Pool("jomez", None, classes=("jomez",), label="JomezPro Series bonus"),
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
    # The published category table, verbatim. Pool names carry the category
    # number so the page can label them from ET_CATEGORY rather than repeat it.
    pools=tuple(
        Pool(f"et{cat}", spec["keep"], cats=(cat,),
             label=f"Category {cat} — {spec['label']} ({spec['win']:.0f} for a win)")
        for cat, spec in sorted(ET_CATEGORY.items())
    ),
    european_only=True,
    cards=True,
    notes=(
        "The points structure here is the published one: three categories "
        f"paying {ET_CATEGORY[1]['win']:.0f}, {ET_CATEGORY[2]['win']:.0f} and "
        f"{ET_CATEGORY[3]['win']:.0f} for a win, counted best 1 of 2, best 2 "
        "of 3 and all 3 — six results in total, and 1,100 points for a perfect "
        "season. So are the 2028 card bands.",
        "What is assumed is the shape of each curve BELOW first place. Only "
        "the win value is published, so the DGPT's own per-place curve is "
        "scaled to it. That scaling lands category 3 on exactly the Elite "
        "Series curve and categories 2 and 1 on the DGPT+ and Playoff "
        "multipliers, which is a good sign but is still an inference.",
        "The national championship weekend is modelled as a set of parallel "
        "single-country fields, which is what the eligibility rules describe: "
        "entry restricted by citizenship or residence, one championship per "
        "player. Countries with almost nobody in this table are pooled into "
        "one regional championship — itself an eligible category — so a lone "
        "entrant cannot win a national title unopposed every season. Which "
        "federations will apply for sanctioning by the December 2026 deadline "
        "is not yet known, so every group here is assumed to run one.",
        "The table is built from players who appear in the 2026 DGPT World "
        "Standings, and only European ones. A European who plays purely "
        "domestic events has no row here and in a real EuroTour standings "
        "would; a non-European who plays the European Open and both Elite "
        "Series stops could bank category 1 and 2 points and is left out too. "
        "This is the tab's biggest blind spot.",
        "Displacement is not modelled. A European who earns a Full Tour Card "
        "through the DGPT World Standings passes their EuroTour Card down, and "
        "a Full Tour Card winner may take a EuroTour Card instead. Both push "
        "cards further down the standings, so these odds are a floor for "
        "players just outside a band, never a ceiling.",
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
