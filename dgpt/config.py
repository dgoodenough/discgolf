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
# TODO(doubles teams): the Preserve is a doubles event scored with a team
# curve, but we currently model each player solo vs a field-average partner.
# Some pairings have been announced; once the full team list is public, join
# partners so a player's projected Preserve points reflect their actual team.
TID_DOUBLES = 96416        # Doubles Championship at The Preserve
TID_GMC = 96418            # Green Mountain Championship (playoff 1)
TID_MVP = 96419            # MVP Open x OTB (playoff 2)
TID_CHAMPIONSHIP = 96421   # DGPT Powerball Cup (no points)
TID_USWDGC = 97341         # Major, FPO field only
TID_USDGC = 97346          # XM tier, non-points

# Playoff qualification (dgpt.com/announcements/playoff-qualification-update).
# Field is set by World Standings rank *before* each playoff event; "cut" is
# the points-qualification line, "fill" the number the field expands to if the
# primary window doesn't fill. MVP also admits the top GMC finishers who miss
# the points cut ("perf").
PLAYOFF_QUAL = {
    "gmc": {"cut": {"MPO": 100, "FPO": 50}, "fill": {"MPO": 120, "FPO": 60}},
    "mvp": {"cut": {"MPO": 72, "FPO": 36}, "perf": {"MPO": 8, "FPO": 4}},
}

MAJOR_TIDS_MPO = {97336, 97339, 97344}            # Champions Cup, European Open, Pro Worlds
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
