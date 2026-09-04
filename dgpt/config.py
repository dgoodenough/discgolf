"""Season constants and 2026 DGPT points rules.

Sources:
- Base per-place curves: DGPT/StatMando 2025 curves (data/pointslogic/base_curves_2025.csv),
  unchanged for 2026 per dgpt.com/announcements/2026-points-structure/
- Class multipliers: Elite win=150, DGPT+=200, Playoff=250, Major=300 (straight
  multiples of the base curve, verified against 2026 standings data)
"""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
BASE_CURVES_CSV = DATA_DIR / "pointslogic" / "base_curves_2025.csv"

SEASON = 2026

# Event class -> multiplier applied to the base (Elite Series) curve.
MULTIPLIERS = {
    "elite": 1.0,
    "doubles": 1.0,        # Preserve doubles: base curve, transformed (see points.doubles_curve)
    "elite_plus": 4.0 / 3.0,
    "playoff": 5.0 / 3.0,
    "major": 2.0,
    "jomez": 0.0,          # limited bonus points; scale TBD (reverse-engineer from standings)
    "championship": 0.0,   # Powerball Cup awards no points
}

# Season counting rules (2026): per-class caps, not one pooled best-N.
# Keep the best N of each class; JomezPro Series points are bonus (all count).
COUNT_DGPT = 10     # best 10 of DGPT + DGPT+ (+ the doubles championship)
COUNT_PLAYOFF = 2   # both playoff events (GMC + MVP Open)
COUNT_MAJOR = 2     # best 2 of the division's majors (3 for MPO, 4 for FPO)
MAJORS_COUNTED = COUNT_MAJOR
TOP_N_FINISHES = COUNT_DGPT + COUNT_PLAYOFF + COUNT_MAJOR  # 14 counted (+ Jomez bonus)

# 2026 tournament IDs with special handling
TID_HEINOLA = 96413        # no FPO points (USWDGC travel turnaround)
TID_DOUBLES = 96416        # Doubles Championship at The Preserve
# Team pairings: PDGA Live's Team/Teammates fields are empty until the event
# is staged for live scoring, so until then teams are read from the DGS
# registration page (re-fetched every refresh, so new teams appear
# automatically). live_api.doubles_teams prefers PDGA Live once populated.
DOUBLES_REG_URL = "https://www.discgolfscene.com/tournaments/DGPT_Doubles_Championship_at_The_Preserve_2026/registration"
TID_GMC = 96418            # Green Mountain Championship (playoff 1)
TID_MVP = 96419            # MVP Open x OTB (playoff 2)
TID_CHAMPIONSHIP = 96421   # DGPT Powerball Cup (no points)
TID_USWDGC = 97341         # Major, FPO field only
TID_USDGC = 97346          # XM tier, non-points
TID_WORLDS = 97344         # Pro Worlds (major); part of its field comes from the play-in

# Pro Worlds play-in (pdga.com/tour/event/106696): a single-round qualifier
# played into the spots Worlds left open, for players who did not qualify
# directly. The winners join the Worlds field and can bank major points, so
# it is the one non-points event whose result moves the World Standings.
TID_WORLDS_PLAYIN = 106696
WORLDS_PLAYIN_SPOTS = {"MPO": 6, "FPO": 2}   # open Worlds spots, per event 97344
PLAYIN_ROUNDS = 1

# Playoff qualification (dgpt.com/announcements/playoff-qualification-update).
# "cut" is the points-qualification line, "fill" the number the field expands
# to if the primary window doesn't fill. MVP also admits the top GMC finishers
# who miss the points cut ("perf").
#
# Both cuts read the SAME standings snapshot: the table as it stood after Pro
# Worlds, the last major of the season (2026 Powerball Cup announcement). Not
# the standings immediately before each event — GMC's window closed before
# Idlewild was played, and MVP's before GMC was, which is why both invite
# waves could open on Sep 1. simulate._simulate ranks before each playoff
# event instead, which is a later snapshot; it is inert for 2026 (both signup
# lists went final on Sep 1, so fields.signed_up replaces the gate rather than
# unioning with it) and shows only in the p_gmc_cut / p_mvp_cut advanced
# columns. Fix the snapshot before reusing this for a season where the gate is
# still live.
PLAYOFF_QUAL = {
    "gmc": {"cut": {"MPO": 100, "FPO": 50}, "fill": {"MPO": 120, "FPO": 60}},
    "mvp": {"cut": {"MPO": 72, "FPO": 36}, "perf": {"MPO": 8, "FPO": 4}},
}

# Powerball Cup starting strokes, from the DGPT's published seed table.
# The Cup is stroke play over four rounds at Ivy Hill (Oct 15-18), and every
# qualifier tees off on a score set by their World Standings position after the
# last points event — the season's table, carried onto the first tee.
#
# Bands are (last rank in the band, starting score). Both divisions run the
# same shape and the same boundaries through rank 16; FPO simply starts one
# band lower, which is 2026's change — the top seed gives up a stroke from
# 2025's -8 MPO / -7 FPO.
#
# Ranks past the last band start at even. That is where the four MPO / two FPO
# wildcards land, and where an event winner's special invite lands when they
# miss the standings cut: both are bottom seeds. The last band pays even
# anyway, so callers can clamp rank to the length of the ladder.
CUP_START_STROKES = {
    "MPO": [(1, -7), (2, -6), (4, -5), (8, -4), (12, -3), (16, -2), (24, -1), (28, 0)],
    "FPO": [(1, -6), (2, -5), (4, -4), (8, -3), (12, -2), (16, -1), (18, 0)],
}


def cup_start_strokes(division: str) -> list[int]:
    """Starting score by final World Standings rank, indexed by rank - 1.

    Length is the standings cut (28 MPO / 18 FPO); deeper ranks are bottom
    seeds and start at even, which is what the last band already pays.
    """
    out: list[int] = []
    for last_rank, strokes in CUP_START_STROKES[division]:
        out += [strokes] * (last_rank - len(out))
    return out


# When each playoff registration window OPENS (the PDGA event pages, converted
# from EDT). Windows are cumulative: once one opens it stays open, so today's
# signup list is everyone eligible under every phase that has already passed.
#
# The model does NOT re-derive eligibility from this table — it reads the real
# signup lists and takes them literally (see fields.signups). The phases are
# what justify still carrying a standings gate for players who have NOT signed
# up: a 70th-ranked MPO player could not register for GMC before Sep 1, so
# their absence from the list today says nothing about whether they will play.
# That is also why the tour-card phase needs no probabilistic model — it opened
# in March, so anyone taking it is already on the list.
REG_PHASES = {
    "gmc": [
        {"opens": "2026-03-23T18:00:00Z", "label": "Full Tour Card holders",
         "min_rating": {"MPO": 1010, "FPO": 930}},
        {"opens": "2026-08-13T16:00:00Z", "label": "DGPT standings wave 1",
         "top": {"MPO": 60, "FPO": 30}},
        {"opens": "2026-09-01T16:00:00Z", "label": "DGPT standings wave 2",
         "top": {"MPO": 100, "FPO": 50}},
    ],
    "mvp": [
        {"opens": "2026-08-13T22:00:00Z", "label": "Tier 1 invites (post-Ledgestone)",
         "top": {"MPO": 50, "FPO": 25}},
        {"opens": "2026-09-01T16:00:00Z", "label": "Tier 2 invites (post-Worlds)",
         "top": {"MPO": 72, "FPO": 36}},
    ],
}

MAJOR_TIDS_MPO = {97336, 97339, TID_WORLDS}       # Champions Cup, European Open, Pro Worlds
MAJOR_TIDS_FPO = MAJOR_TIDS_MPO | {TID_USWDGC}

# JomezPro Series 2026 (bonus points, before Powerball Cup). WACO confirmed;
# Cascade Challenge / Champions Landing IDs filled by schedule.refresh().
JOMEZ_TIDS = {102001}


def load_env(path: Path | None = None) -> dict[str, str]:
    """Read KEY=VALUE pairs from .env (no external deps). Env vars win."""
    path = path or REPO_ROOT / ".env"
    out: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            key, _, val = line.strip().partition("=")
            if key and not key.startswith("#"):
                out[key] = val
    out.update({k: v for k, v in os.environ.items() if k.startswith("PDGA_")})
    return out
