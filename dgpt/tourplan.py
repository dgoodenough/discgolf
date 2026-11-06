"""The 2027 driving-tour planner: schedule, venues, and real road times.

Usage:
    python -m dgpt.tourplan            # fetch schedule, geocode new towns, route, export
    python -m dgpt.tourplan --export   # export only, from the tracked data (offline)

This is a one-shot builder, not a stage of the live run. Everything that
touches the network happens here, by hand, and lands in tracked files under
data/tourplan/:

  events_2027.csv     the PDGA's published 2027 Major / Elite / A-tier table
                      (pdga.com/news/2027schedule), normalised
  venues.csv          one row per town, with coordinates (OpenStreetMap
                      Nominatim). A row is only ever added, never re-fetched,
                      so a hand-corrected coordinate stays corrected.
  drive_minutes.json  the town-to-town road-time matrix (OSRM, OpenStreetMap
                      data), in whole minutes

`export()` reads only those files and writes docs/data/tourplan.json, so the
refresh can call it every run at no network cost and with no way to wedge the
publish: a missing or broken file just leaves the last published bundle up.

The search itself — best home base, best trip — runs in the browser
(docs/js/tourplan.js), because every slider position is a different question
and the whole problem is a few million additions.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import json
import re
import sys
import time
import urllib.parse
import urllib.request

from . import config

SEASON = 2027
SOURCE_URL = "https://www.pdga.com/news/2027schedule"
TOURPLAN_DIR = config.DATA_DIR / "tourplan"
EVENTS_CSV = TOURPLAN_DIR / "events_2027.csv"
VENUES_CSV = TOURPLAN_DIR / "venues.csv"
DRIVE_JSON = TOURPLAN_DIR / "drive_minutes.json"
BUNDLE = config.REPO_ROOT / "docs" / "data" / "tourplan.json"

# A generic client name. Nominatim's policy asks for an identifying
# User-Agent; the project URL identifies us without putting anyone's address
# into a request.
UA = {"User-Agent": "dgpt-forecast-tourplan/1.0 (+https://github.com/dgoodenough/discgolf)"}
NOMINATIM = "https://nominatim.openstreetmap.org/search"
OSRM = "https://router.project-osrm.org/table/v1/driving/"
# The public OSRM server caps a table request at 100 coordinates in total.
OSRM_BLOCK = 50

# Everything a pro can drive to from the US. The 2027 table has nothing in
# Mexico or further south; listed so an event there is picked up if one appears.
DRIVABLE = {"USA", "Canada", "Mexico"}
EVENT_FIELDS = ["start", "end", "tier", "cls", "name", "location", "city",
                "region", "country", "cat", "mpo", "fpo", "wk"]

# Town spellings the geocoder can't place as written. Keyed on the town as
# the PDGA lists it; the value is what we ask Nominatim for instead.
CITY_ALIASES = {
    "Cranberry Twnshp": "Cranberry Township",
    "Greater Peoria": "Peoria",
    "Hartstelle": "Hartselle",          # PDGA typo
    "Marerro": "Marrero",               # PDGA typo
    "Rainer": "Rainier",                # PDGA typo
    "Jefferson Parish": "Metairie",     # Lafreniere Park
    "Mountain View County": "Didsbury",
    "Superior, WI/Duluth": "Duluth",    # listed as one town spanning the state line
}
# Venue not announced yet: nothing to route to, so the event is left out.
UNPLACED = {"TBD", "TBA", ""}

# Who may enter an event, where the PDGA's "class" column doesn't say. The
# published table under-labels restricted events: a women-only or juniors-only
# A-tier can be listed plain "Pro/Am". Keyed on the event name exactly as the
# PDGA lists it; `restrict` is one of fpo, mpo, juniors, masters, exclude.
OVERRIDES_CSV = TOURPLAN_DIR / "eligibility_overrides.csv"


# --------------------------------------------------------------- schedule

def parse_schedule(page: str, season: int = SEASON) -> list[dict]:
    """The event table on the PDGA schedule page, as normalised rows.

    The page is a news post with one six-column table: start, end, tier,
    class, name, location. Dates are "1-Jan" with no year. A layout change
    that leaves no rows is an error, not an empty schedule.
    """
    overrides = load_overrides()
    rows = []
    for tr in re.findall(r"<tr.*?</tr>", page, re.S):
        cells = [_text(c) for c in re.findall(r"<td.*?</td>", tr, re.S)]
        if len(cells) != 6:
            continue
        start, end, tier, cls, name, location = cells
        try:
            sd, ed = _date(start, season), _date(end, season)
        except ValueError:
            continue
        city, region, country = split_location(location)
        row = {"start": sd.isoformat(), "end": ed.isoformat(), "tier": tier,
               "cls": cls, "name": name, "location": location, "city": city,
               "region": region, "country": country}
        row.update(classify(row, overrides))
        row["wk"] = weekend_index(ed, season)
        rows.append(row)
    if len(rows) < 50:
        raise ValueError(f"schedule page parsed to {len(rows)} events; the layout has probably changed")
    return rows


def _text(cell: str) -> str:
    t = html.unescape(re.sub(r"<[^>]+>", "", cell))
    return t.replace("\xa0", " ").strip()


def _date(s: str, season: int) -> dt.date:
    return dt.datetime.strptime(f"{s.strip()}-{season}", "%d-%b-%Y").date()


def split_location(loc: str) -> tuple[str, str, str]:
    """'Charlotte, NC  USA' -> ('Charlotte', 'NC', 'USA').

    The PDGA separates the country with a run of spaces (non-breaking in the
    source). International rows are ragged — 'Krokhol,  Norway',
    'Ale Disc Golf Center, Sweden' — and come back with what they have.
    """
    parts = re.split(r"\s{2,}", loc.strip())
    if len(parts) >= 2:
        place, country = " ".join(parts[:-1]), parts[-1]
    else:
        m = re.match(r"(.*?)\s+(USA|Canada)$", loc.strip())
        place, country = (m.group(1), m.group(2)) if m else (loc.strip(), "")
    if "," in place:
        city, region = (p.strip() for p in place.rsplit(",", 1))
    else:
        city, region = place.strip(), ""
    if not country and region and not re.fullmatch(r"[A-Z]{2}", region):
        country, region = region, ""
    return city, region, country


def load_overrides() -> dict[str, str]:
    if not OVERRIDES_CSV.exists():
        return {}
    with open(OVERRIDES_CSV, newline="") as f:
        return {r["name"].strip(): r["restrict"].strip().lower()
                for r in csv.DictReader(f) if r.get("name") and not r["name"].startswith("#")}


def classify(row: dict, overrides: dict[str, str] | None = None) -> dict:
    """Which planner set an event belongs to, and who may play it.

    cat is 'elite' (DGPT Elite Series), 'major' (PDGA Majors and the USDGC)
    or 'a' (any A-tier, including the A/B and B/A splits). Anything else, or
    anything an open MPO / FPO pro can't enter, gets cat ''.

    Excluded on purpose: amateur-only, collegiate and junior events; the
    DGPT Companion events, which are explicitly non-MPO/FPO; and the masters
    events (US Masters, Masters Worlds), which are age-protected divisions,
    not the open ones this tab plans for. Masters is a known gap: a 40+ pro
    planning a masters tour would want those events and not the open ones,
    which needs a masters division here and on the page.

    Also excluded: anything with "Amateur" in its name or run by the NADGT
    (the amateur tour), whatever its class column says, and any event
    eligibility_overrides.csv restricts away from the open divisions.
    """
    tier, cls, name = row["tier"].upper(), row["cls"].lower(), row["name"]
    low = name.lower()
    out = {"cat": "", "mpo": False, "fpo": False}
    restrict = (overrides or {}).get(name.strip(), "")
    if ("am only" in cls or "collegiate" in cls or "junior" in cls
            or "non-mpo/fpo" in low or "masters" in low
            or "amateur" in low or "nadgt" in low
            or restrict in ("juniors", "masters", "exclude")):
        return out
    if tier == "ES":
        cat = "elite"
    elif tier in ("M", "XM"):
        cat = "major"
    elif "A" in tier.split("/"):
        cat = "a"
    else:
        return out
    # Exact matches: "MPO/FPO Only" contains both "mpo" and "fpo only", and a
    # substring test once read every Elite Series stop as women-only.
    mpo = fpo = True
    if cls == "mpo only" or restrict == "mpo":
        fpo = False
    if cls == "fpo only" or "women" in low or restrict == "fpo":
        mpo = False
    return {"cat": cat, "mpo": mpo, "fpo": fpo}


# Week 0 is the weekend of Sunday 3 Jan 2027.
def weekend_index(end: dt.date, season: int = SEASON) -> int:
    """Which weekend an event belongs to: the Sunday nearest its last day.

    Back-to-back means consecutive weekend indices. Keying on the nearest
    Sunday (not the ISO week) keeps a Tue–Sat Masters Worlds and a Fri–Mon
    holiday event on the weekend they are actually played.
    """
    jan1 = dt.date(season, 1, 1)
    first_sunday = jan1 + dt.timedelta(days=(6 - jan1.weekday()) % 7)
    return round((end - first_sunday).days / 7)


def fetch_schedule() -> list[dict]:
    req = urllib.request.Request(SOURCE_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return parse_schedule(r.read().decode("utf-8", "replace"))


def write_events(rows: list[dict]) -> None:
    TOURPLAN_DIR.mkdir(parents=True, exist_ok=True)
    with open(EVENTS_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=EVENT_FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def load_events() -> list[dict]:
    with open(EVENTS_CSV, newline="") as f:
        rows = list(csv.DictReader(f))
    # Re-classified on every load, so an edit to the overrides file takes
    # effect at the next export without re-fetching the schedule.
    overrides = load_overrides()
    for r in rows:
        r.update(classify(r, overrides))
        r["wk"] = int(r["wk"])
    return rows


def plannable(rows: list[dict]) -> list[dict]:
    """Events the planner offers: in a planner set, and drivable."""
    return [r for r in rows
            if r["cat"] and r["country"] in DRIVABLE and r["city"] not in UNPLACED]


def venue_key(row: dict) -> str:
    return f"{row['city']}, {row['region']}, {row['country']}"


# ----------------------------------------------------------------- venues

def load_venues() -> dict[str, dict]:
    if not VENUES_CSV.exists():
        return {}
    with open(VENUES_CSV, newline="") as f:
        return {r["key"]: {**r, "lat": float(r["lat"]), "lon": float(r["lon"])}
                for r in csv.DictReader(f)}


def write_venues(venues: dict[str, dict]) -> None:
    TOURPLAN_DIR.mkdir(parents=True, exist_ok=True)
    with open(VENUES_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["key", "lat", "lon", "matched"], lineterminator="\n")
        w.writeheader()
        for k in sorted(venues):
            v = venues[k]
            w.writerow({"key": k, "lat": f"{v['lat']:.5f}", "lon": f"{v['lon']:.5f}",
                        "matched": v.get("matched", "")})


def _get_json(url: str, tries: int = 4):
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                return json.load(r)
        except Exception:  # noqa: BLE001 - retried, then re-raised
            if attempt == tries - 1:
                raise
            time.sleep(2 ** (attempt + 1))


def geocode(row: dict) -> dict:
    """One town via Nominatim, checked against the state / province we asked for."""
    city = CITY_ALIASES.get(row["city"], row["city"]).split("/")[0].strip()
    cc = {"USA": "us", "Canada": "ca", "Mexico": "mx"}[row["country"]]
    q = urllib.parse.urlencode({"city": city, "state": row["region"], "countrycodes": cc,
                                "format": "jsonv2", "addressdetails": 1, "limit": 5})
    want = f"{cc.upper()}-{row['region']}"
    for hit in _get_json(f"{NOMINATIM}?{q}"):
        if hit.get("address", {}).get("ISO3166-2-lvl4") == want:
            return {"lat": float(hit["lat"]), "lon": float(hit["lon"]),
                    "matched": hit.get("display_name", "")[:120]}
    raise LookupError(f"no geocode in {want} for {row['city']!r}")


def ensure_venues(rows: list[dict]) -> dict[str, dict]:
    """Geocode every town not already in venues.csv; fail listing the misses."""
    venues = load_venues()
    missing, failed = {}, []
    for r in plannable(rows):
        k = venue_key(r)
        if k not in venues:
            missing.setdefault(k, r)
    for i, (k, r) in enumerate(sorted(missing.items())):
        try:
            venues[k] = geocode(r)
            print(f"  geocoded {k} -> {venues[k]['lat']:.3f},{venues[k]['lon']:.3f}")
        except Exception as e:  # noqa: BLE001 - collected and reported together
            failed.append(f"{k}: {e}")
        time.sleep(1.1)  # Nominatim: at most one request a second
        if i % 20 == 19:
            write_venues(venues)  # keep progress across a crash
    write_venues(venues)
    if failed:
        raise RuntimeError("could not geocode (add a row to data/tourplan/venues.csv by hand, or an alias):\n  "
                           + "\n  ".join(failed))
    return venues


# ----------------------------------------------------------------- routing

def build_matrix(keys: list[str], venues: dict[str, dict]) -> list[list[int]]:
    """Road minutes between every pair of towns, via OSRM, in blocks."""
    n = len(keys)
    m = [[-1] * n for _ in range(n)]
    blocks = [list(range(i, min(i + OSRM_BLOCK, n))) for i in range(0, n, OSRM_BLOCK)]
    for src in blocks:
        for dst in blocks:
            idx = src + [j for j in dst if j not in src]
            coords = ";".join(f"{venues[keys[i]]['lon']:.5f},{venues[keys[i]]['lat']:.5f}" for i in idx)
            pos = {g: p for p, g in enumerate(idx)}
            q = urllib.parse.urlencode({"sources": ";".join(str(pos[i]) for i in src),
                                        "destinations": ";".join(str(pos[j]) for j in dst),
                                        "annotations": "duration"})
            res = _get_json(f"{OSRM}{coords}?{q}")
            if res.get("code") != "Ok":
                raise RuntimeError(f"OSRM: {res.get('code')} {res.get('message', '')}")
            for a, i in enumerate(src):
                for b, j in enumerate(dst):
                    d = res["durations"][a][b]
                    m[i][j] = -1 if d is None else round(d / 60)
            time.sleep(1.0)
    return m


def ensure_matrix(rows: list[dict], venues: dict[str, dict], force: bool = False) -> dict:
    keys = sorted({venue_key(r) for r in plannable(rows)})
    if DRIVE_JSON.exists() and not force:
        cur = json.loads(DRIVE_JSON.read_text())
        if cur.get("keys") == keys:
            return cur
    print(f"  routing {len(keys)} towns ({len(keys) ** 2} pairs) ...")
    out = {"keys": keys, "minutes": build_matrix(keys, venues),
           "source": "OSRM (router.project-osrm.org), OpenStreetMap data",
           "built": dt.date.today().isoformat()}
    unrouted = sum(v < 0 for row in out["minutes"] for v in row)
    if unrouted:
        print(f"  warning: {unrouted} pairs have no road route")
    DRIVE_JSON.write_text(json.dumps(out, separators=(",", ":")) + "\n")
    return out


# ------------------------------------------------------------------ export

def bundle(rows: list[dict], venues: dict[str, dict], drive: dict) -> dict:
    """The site's view: venues, the matrix (flat, row-major), and events."""
    keys = drive["keys"]
    pos = {k: i for i, k in enumerate(keys)}
    events = []
    for r in plannable(rows):
        k = venue_key(r)
        if k not in pos:
            raise ValueError(f"{r['name']}: venue {k!r} is not in the drive-time matrix; rerun dgpt.tourplan")
        events.append({"name": r["name"], "start": r["start"], "end": r["end"],
                       "tier": r["tier"], "cat": r["cat"], "mpo": r["mpo"], "fpo": r["fpo"],
                       "wk": r["wk"], "v": pos[k]})
    events.sort(key=lambda e: (e["start"], e["name"]))
    n = len(keys)
    flat = [v for row in drive["minutes"] for v in row]
    if len(flat) != n * n:
        raise ValueError("drive-time matrix is not square")
    return {
        "meta": {
            "season": SEASON,
            "source": SOURCE_URL,
            "routing": drive.get("source", ""),
            "routed": drive.get("built", ""),
            "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "n_venues": n,
        },
        "venues": [{"name": f"{k.split(', ')[0]}, {k.split(', ')[1]}",
                    "lat": venues[k]["lat"], "lon": venues[k]["lon"]} for k in keys],
        "minutes": flat,
        "events": events,
    }


def export() -> str:
    """Write docs/data/tourplan.json from the tracked files. No network."""
    rows = load_events()
    drive = json.loads(DRIVE_JSON.read_text())
    b = bundle(rows, load_venues(), drive)
    BUNDLE.parent.mkdir(parents=True, exist_ok=True)
    BUNDLE.write_text(json.dumps(b, separators=(",", ":")))
    return f"tourplan: {len(b['events'])} events, {b['meta']['n_venues']} towns"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--export", action="store_true", help="export only, from the tracked files")
    ap.add_argument("--reroute", action="store_true", help="rebuild the drive-time matrix even if unchanged")
    args = ap.parse_args()
    if not args.export:
        print(f"fetching {SOURCE_URL} ...")
        rows = fetch_schedule()
        write_events(rows)
        print(f"  {len(rows)} events, {len(plannable(rows))} plannable")
        venues = ensure_venues(rows)
        ensure_matrix(rows, venues, force=args.reroute)
    print(export())


if __name__ == "__main__":
    sys.exit(main())
