"""The Amateur planner's bundle: every sanctioned amateur event, for the browser.

Usage:
    python -m dgpt.amplan                       # pull from the PDGA API and export
    python -m dgpt.amplan --from-json FILE      # export from saved API records (offline)

The 2027 Tour planner (dgpt/tourplan.py) plans over ~200 A-tier-and-up events
with a real road-time matrix. This is its amateur sibling: every sanctioned
event open to amateurs, B and C tiers included — about 7,000 a year in the
US and Canada. dgpt/tourscan.py measured what that takes (notes in its
docstring); the design that came out of it:

  * the PDGA REST API carries coordinates for 99.8% of events, so there is no
    geocoding;
  * a full road-time matrix (13.7M pairs) is out, so drive time is a
    straight-line estimate calibrated on the Tour planner's real OSRM matrix —
    good to 3-8% on long legs, rough (~30%) under 200 km;
  * the browser searches one home at a time, keeping only the events nearest
    home each weekend, which the scan showed finds the exact best trip.

Runs inside `dgpt.refresh` (daily, not the live loop) and never fails it. The
last good pull is kept in data/cache (which the daily workflow restores), so a
failed pull re-exports yesterday's events rather than dropping the tab.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib

from . import config

BUNDLE = config.REPO_ROOT / "docs" / "data" / "amplan.json"
CACHE = config.CACHE_DIR / "amplan_events.json"
COUNTRIES = {"United States": "US", "Canada": "CA", "Mexico": "MX"}
# Tier codes as the API spells them, folded into the boxes the page offers.
# Split tiers ("C/B") count under every tier they name.
TIER_GROUPS = ["A", "B", "C", "X", "M"]


def seasons(today: dt.date | None = None) -> list[int]:
    """This year and next: the season being played and the one being planned."""
    y = (today or dt.date.today()).year
    return [y, y + 1]


def tier_groups(tier: str) -> list[str]:
    """'C/B' -> ['C', 'B']; 'XC' -> ['X']; 'ES' -> [] (pro-only tour stops)."""
    out = []
    for t in str(tier).upper().split("/"):
        t = t.strip()
        g = "X" if t.startswith("X") else ("M" if t == "M" else t)
        if g in TIER_GROUPS and g not in out:
            out.append(g)
    return out


def weekend_ord(d: dt.date) -> int:
    """The Sunday nearest a date, as a day ordinal (dgpt/tourplan.py's rule)."""
    ahead = (6 - d.weekday()) % 7
    return (d + dt.timedelta(days=ahead if ahead <= 3 else ahead - 7)).toordinal()


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v and v != 0.0 else None


def keep(e: dict) -> bool:
    """Open to amateurs, on, drivable, located, and not a league night."""
    return (str(e.get("status", "")).lower() == "sanctioned"
            and "am" in str(e.get("class", "")).lower()
            and str(e.get("country", "")) in COUNTRIES
            and bool(tier_groups(e.get("tier", "")))
            and _num(e.get("latitude")) is not None and _num(e.get("longitude")) is not None)


def bundle(events: list[dict], years: list[int]) -> dict:
    """Column-packed rows, a few hundred KB for ~7,000 events.

    Each row: [tournament_id, name, tier, am_only (1 Am, 0 Pro-Am), start day,
    end day, weekend index, lat, lon, place index, registration URL].
    Days are days since 1970-01-01 (what JS gets from Date.UTC / 864e5);
    the weekend index counts Sundays from the same epoch.
    """
    epoch = dt.date(1970, 1, 1).toordinal()
    places: dict[str, int] = {}
    rows = []
    for e in events:
        if not keep(e):
            continue
        try:
            sd = dt.date.fromisoformat(str(e["start_date"])[:10])
            ed = dt.date.fromisoformat(str(e.get("end_date") or e["start_date"])[:10])
        except (KeyError, ValueError):
            continue
        if sd.year not in years:
            continue
        place = f"{e.get('city', '').strip()}, {e.get('state_prov', '').strip()}".strip(", ")
        pi = places.setdefault(place, len(places))
        rows.append([
            int(e["tournament_id"]), str(e.get("tournament_name", "")).strip(), str(e.get("tier", "")),
            1 if str(e.get("class", "")).lower() == "am" else 0,
            sd.toordinal() - epoch, ed.toordinal() - epoch, (weekend_ord(ed) - epoch) // 7,
            round(_num(e["latitude"]), 4), round(_num(e["longitude"]), 4), pi,
            str(e.get("registration_url") or ""),
        ])
    rows.sort(key=lambda r: (r[4], r[1]))
    from .tourscan import calibrate
    a, b, bands = calibrate()
    return {
        "meta": {
            "years": years,
            "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "source": "PDGA REST API (api.pdga.com), every sanctioned event",
            "drive": {"a": round(a, 2), "b": round(b, 5),
                      "note": "minutes = a + b x straight-line km, fit to OSRM road times",
                      "error": {k: [round(v[1], 3), round(v[2], 3)] for k, v in bands.items()}},
            "fields": ["id", "name", "tier", "am_only", "start", "end", "wk", "lat", "lon", "place", "reg"],
        },
        "places": list(places),
        "events": rows,
    }


def pull(years: list[int]) -> list[dict]:
    from .tourscan import pull as scan_pull
    return scan_pull(years, pause=0.2)


def build(years: list[int] | None = None, events: list[dict] | None = None) -> str:
    """Pull (or take) events, export the bundle; on a failed pull, re-export
    from the last good one in data/cache."""
    years = years or seasons()
    note = ""
    if events is None:
        try:
            events = pull(years)
            CACHE.parent.mkdir(parents=True, exist_ok=True)
            CACHE.write_text(json.dumps(events))
        except Exception as e:  # noqa: BLE001 - fall back to the last good pull
            if not CACHE.exists():
                raise
            events = json.loads(CACHE.read_text())
            note = f" (pull failed: {e}; re-exported the cached pull)"
    b = bundle(events, years)
    BUNDLE.parent.mkdir(parents=True, exist_ok=True)
    BUNDLE.write_text(json.dumps(b, separators=(",", ":"), ensure_ascii=False))
    return f"amplan: {len(b['events'])} events, {len(b['places'])} places{note}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from-json", help="saved PDGA event records (a list), instead of pulling")
    ap.add_argument("--years", type=int, nargs="+")
    args = ap.parse_args()
    events = json.loads(pathlib.Path(args.from_json).read_text()) if args.from_json else None
    print(build(args.years, events))


if __name__ == "__main__":
    main()
