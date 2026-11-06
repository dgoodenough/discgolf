"""Prototype scan: could the Tour planner cover every sanctioned event?

Usage:
    python -m dgpt.tourscan [--years 2026 2027] [--out results/tourscan]
    python -m dgpt.tourscan --from-json results/tourscan/events.json   # re-analyse offline

The 2027 Tour planner (dgpt/tourplan.py) works on ~200 drivable A-tier-and-up
events. An amateur version would work on every sanctioned event — B and C
tiers included, thousands a year — and this module measures what that would
take before anything is built:

  1. Pull every event in the given years from the PDGA REST API, month by
     month and all tiers, and report what the records carry: counts by year,
     tier, class, country and status, the field names, and how many have
     coordinates (which decides whether geocoding is needed at all).
  2. Report the density an amateur planner would face: amateur-eligible
     events per weekend, and how many fall within a few hours of each other.
  3. Test the pruning the planner would need at this scale: for a sample of
     home towns, find the cheapest "n events within w weekends" trip over all
     events, and again keeping only the K nearest events per weekend, and
     report how often the pruned search finds the same trip and at what cost.
     Drive times here are a straight-line estimate calibrated on the 2027
     planner's real OSRM matrix (data/tourplan/), since that is the plan for
     long legs anyway.

It writes nothing tracked and publishes nothing: raw events and the report go
to --out (gitignored results/), and the report is also printed, so a CI run's
log carries it.
"""
from __future__ import annotations

import argparse
import calendar
import collections
import csv
import datetime as dt
import json
import math
import pathlib
import random
import time

import numpy as np

from . import config

DRIVABLE = {"United States", "USA", "US", "Canada", "CA", "Mexico", "MX"}
DRIVE_HOURS_PER_DAY = 10
# The planner's own settings, for the pruning test.
TRIPS = [(4, 4), (5, 6), (8, 12)]       # (events, weekends on the road)
PRUNE_K = [3, 5, 10, 20]
SAMPLE_HOMES = 40


# ------------------------------------------------------------------- pull

def pull(years: list[int], client=None, pause: float = 0.3) -> list[dict]:
    """Every event in `years`, one month at a time so each query stays well
    inside the API's pagination, de-duplicated on tournament_id."""
    if client is None:
        from .pdga_api import PDGAClient
        client = PDGAClient()
    seen: dict[str, dict] = {}
    for y in years:
        for mo in range(1, 13):
            last = calendar.monthrange(y, mo)[1]
            offset = 0
            while True:
                data = client._get("event", start_date=f"{y}-{mo:02d}-01",
                                   end_date=f"{y}-{mo:02d}-{last}", limit=200, offset=offset)
                batch = data.get("events") or []
                for e in batch:
                    seen[str(e.get("tournament_id"))] = e
                time.sleep(pause)
                if len(batch) < 200:
                    break
                offset += 200
            print(f"  {y}-{mo:02d}: {len(seen)} events so far")
    return list(seen.values())


# ---------------------------------------------------------------- helpers

def _f(x):
    try:
        v = float(x)
        return v if math.isfinite(v) and v != 0.0 else None
    except (TypeError, ValueError):
        return None


def coords(e: dict):
    lat = _f(e.get("latitude"))
    lon = _f(e.get("longitude"))
    return (lat, lon) if lat is not None and lon is not None else None


def is_amateur(e: dict) -> bool:
    """Open to amateurs. The REST `class` field is 'Pro', 'Am' or 'Pro/Am'
    style text (an assumption the report checks by listing every value)."""
    return "am" in str(e.get("class", "")).lower()


def weekend(d: dt.date) -> dt.date:
    """The Sunday nearest a date — the same rule dgpt/tourplan.py keys on."""
    ahead = (6 - d.weekday()) % 7          # days forward to Sunday, 0..6
    return d + dt.timedelta(days=ahead if ahead <= 3 else ahead - 7)


def _date(s):
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    h = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371 * np.arcsin(np.sqrt(h))


def calibrate() -> tuple[float, float, dict]:
    """Fit drive minutes = a + b * km on the 2027 planner's real OSRM matrix,
    and report its error by distance band — the case for real road times only
    on short legs."""
    drive = json.loads((config.DATA_DIR / "tourplan" / "drive_minutes.json").read_text())
    with open(config.DATA_DIR / "tourplan" / "venues.csv", newline="") as f:
        v = {r["key"]: (float(r["lat"]), float(r["lon"])) for r in csv.DictReader(f)}
    keys, m = drive["keys"], np.array(drive["minutes"], float)
    lat = np.array([v[k][0] for k in keys]); lon = np.array([v[k][1] for k in keys])
    iu = np.triu_indices(len(keys), 1)
    km = haversine_km(lat[iu[0]], lon[iu[0]], lat[iu[1]], lon[iu[1]])
    mins = (m[iu] + m.T[iu]) / 2
    A = np.vstack([np.ones_like(km), km]).T
    a, b = np.linalg.lstsq(A, mins, rcond=None)[0]
    rel = np.abs(a + b * km - mins) / np.maximum(mins, 1)
    bands = {}
    for lo, hi in [(0, 200), (200, 600), (600, 1500), (1500, 9e9)]:
        s = (km >= lo) & (km < hi)
        if s.any():
            bands[f"{lo}-{int(hi) if hi < 9e9 else '+'} km"] = (int(s.sum()), float(np.median(rel[s])), float(np.percentile(rel[s], 90)))
    return float(a), float(b), bands


# ------------------------------------------------------------ the search

def best_trip(home, ev, n, weeks, style, est, K=None):
    """Cheapest trip from `home` playing n events within `weeks` weekends,
    the same DP as docs/js/tourplan-search.js (events played x weekends
    skipped), over events grouped by weekend. With K, each weekend keeps only
    the K events nearest home — the pruning under test."""
    hl, ho = home
    B = weeks - n
    by_wk = collections.defaultdict(list)
    for i, e in enumerate(ev):
        by_wk[e["wk"]].append(i)
    if K:
        for w, idx in by_wk.items():
            dist = haversine_km(hl, ho, np.array([ev[i]["lat"] for i in idx]), np.array([ev[i]["lon"] for i in idx]))
            by_wk[w] = [idx[j] for j in np.argsort(dist)[:K]]
    wks = sorted(by_wk)
    lat = np.array([e["lat"] for e in ev]); lon = np.array([e["lon"] for e in ev])
    s = np.array([e["s"] for e in ev]); f = np.array([e["f"] for e in ev])
    home_leg = est(haversine_km(hl, ho, lat, lon))
    INF = np.inf
    # cost[(w, k, o)] -> array over by_wk[w]: cheapest chain of k events ending
    # there having skipped o weekends
    cost = {}
    best = INF
    for w in wks:
        idx = np.array(by_wk[w])
        for k in range(1, n + 1):
            for o in range(B + 1):
                cur = np.full(len(idx), INF)
                if k == 1:
                    if o == 0:
                        cur = home_leg[idx].copy()
                else:
                    for dw in range(1, B + 2):
                        o2 = o - (dw - 1)
                        pw = w - dw
                        if o2 < 0 or (pw, k - 1, o2) not in cost:
                            continue
                        prev = cost[(pw, k - 1, o2)]
                        if not np.isfinite(prev).any():
                            continue
                        pidx = np.array(by_wk[pw])
                        if style == "hub":
                            leg = home_leg[pidx][:, None] + home_leg[idx][None, :]
                        else:
                            leg = est(haversine_km(lat[pidx][:, None], lon[pidx][:, None], lat[idx][None, :], lon[idx][None, :]))
                        budget = (s[idx][None, :] - f[pidx][:, None]) * DRIVE_HOURS_PER_DAY * 60
                        leg = np.where((leg <= budget) & (s[idx][None, :] > f[pidx][:, None]), leg, INF)
                        cur = np.minimum(cur, (prev[:, None] + leg).min(axis=0))
                cost[(w, k, o)] = cur
                if k == n:
                    best = min(best, float((cur + home_leg[idx]).min()))
        # drop layers that can no longer be reached from
        for key in [key for key in cost if key[0] < w - (B + 1)]:
            del cost[key]
    return best


# ------------------------------------------------------------------ report

def analyse(events: list[dict], seed: int = 7) -> str:
    out: list[str] = []
    p = out.append
    p("# Tour planner at full scale — prototype scan\n")
    p(f"{len(events)} events pulled.\n")

    # --- what the records carry
    keys = collections.Counter(k for e in events for k in e)
    p("## Fields\n")
    p(", ".join(f"`{k}` ({c})" for k, c in sorted(keys.items())) + "\n")
    if events:
        sample = dict(sorted(events[len(events) // 2].items()))
        p("Sample record:\n\n```json\n" + json.dumps(sample, indent=1)[:2500] + "\n```\n")

    def table(title, counter, top=25):
        p(f"### {title}\n\n| value | events |\n|---|---|")
        for k, c in counter.most_common(top):
            p(f"| {k or '(blank)'} | {c} |")
        p("")

    years = collections.Counter(str(e.get("start_date", ""))[:4] for e in events)
    table("By year", years)
    table("By tier", collections.Counter(str(e.get("tier", "")) for e in events))
    table("By class", collections.Counter(str(e.get("class", "")) for e in events))
    table("By status", collections.Counter(str(e.get("status", "")) for e in events))
    table("By country", collections.Counter(str(e.get("country", "")) for e in events), top=15)

    with_xy = [e for e in events if coords(e)]
    p(f"## Coordinates\n\n{len(with_xy)} of {len(events)} events "
      f"({100 * len(with_xy) / max(1, len(events)):.1f}%) carry a usable latitude/longitude.\n")

    # --- the amateur planner's universe
    am = []
    for e in events:
        c, sd, ed = coords(e), _date(e.get("start_date")), _date(e.get("end_date") or e.get("start_date"))
        if not (c and sd and ed and is_amateur(e) and str(e.get("country", "")) in DRIVABLE):
            continue
        if str(e.get("tier", "")).upper() in ("L", "LEAGUE"):
            continue
        am.append({"id": e.get("tournament_id"), "name": e.get("tournament_name", ""), "tier": e.get("tier"),
                   "lat": c[0], "lon": c[1], "s": sd.toordinal(), "f": ed.toordinal(),
                   "wk": weekend(ed).toordinal() // 7, "year": sd.year})
    p(f"## Amateur-eligible, drivable, located, not leagues\n\n{len(am)} events.\n")
    if not am:
        return "\n".join(out)
    by_year = collections.Counter(e["year"] for e in am)
    p("| year | events |\n|---|---|\n" + "\n".join(f"| {y} | {c} |" for y, c in sorted(by_year.items())) + "\n")

    # the busiest complete year is the one to study
    year = max(by_year, key=by_year.get)
    ev = [e for e in am if e["year"] == year]
    per_wk = collections.Counter(e["wk"] for e in ev)
    vals = sorted(per_wk.values())
    p(f"### {year}: events per weekend\n\n{len(per_wk)} weekends with events; per weekend "
      f"median {vals[len(vals) // 2]}, 90th percentile {vals[int(len(vals) * .9)]}, max {vals[-1]}.\n")
    towns = {(round(e["lat"], 2), round(e["lon"], 2)) for e in ev}
    p(f"{len(towns)} distinct venue locations (rounded to ~1 km) — a full matrix would be "
      f"{len(towns) ** 2:,} pairs.\n")

    # --- drive-time model
    a, b, bands = calibrate()
    est = lambda km: a + b * km  # noqa: E731
    p(f"## Drive-time estimate\n\nCalibrated on the 2027 planner's real OSRM times: "
      f"**{a:.1f} min + {b:.3f} min/km** of straight-line distance.\n")
    p("| distance | pairs | median error | 90th pct error |\n|---|---|---|---|")
    for band, (cnt, med, p90) in bands.items():
        p(f"| {band} | {cnt} | {med * 100:.0f}% | {p90 * 100:.0f}% |")
    p("")
    lat = np.array([t[0] for t in towns]); lon = np.array([t[1] for t in towns])
    near = []
    for i in range(len(towns)):
        mins = est(haversine_km(lat[i], lon[i], lat, lon))
        near.append(int((mins <= 300).sum()) - 1)
    near.sort()
    p(f"Sparse alternative: venues within ~5 h of each venue — median {near[len(near) // 2]}, "
      f"90th pct {near[int(len(near) * .9)]}; about {sum(near):,} pairs to route for real, "
      f"vs {len(towns) ** 2:,} for a full matrix.\n")

    # --- pruning test
    rng = random.Random(seed)
    homes = rng.sample(sorted(towns), min(SAMPLE_HOMES, len(towns)))
    p(f"## Pruning test\n\n{len(homes)} random home locations from {year}'s venues. For each trip shape, the "
      f"cheapest trip over all {len(ev)} events vs keeping only the K events nearest home each weekend. "
      f"'same' = identical total; 'gap' = how much worse the pruned trip is when it differs.\n")
    p("| style | events/weekends | K | same | median gap | worst gap | full search s/home | pruned s/home |\n|---|---|---|---|---|---|---|---|")
    for style in ("hub", "road"):
        for n, w in TRIPS:
            t0 = time.time()
            full = [best_trip(h, ev, n, w, style, est) for h in homes]
            t_full = (time.time() - t0) / len(homes)
            for K in PRUNE_K:
                t0 = time.time()
                pr = [best_trip(h, ev, n, w, style, est, K=K) for h in homes]
                t_pr = (time.time() - t0) / len(homes)
                pairs = [(x, y) for x, y in zip(full, pr) if math.isfinite(x)]
                same = sum(abs(x - y) < 1e-6 for x, y in pairs)
                gaps = sorted((y - x) / x for x, y in pairs if abs(x - y) >= 1e-6 and x > 0)
                med = f"{gaps[len(gaps) // 2] * 100:.1f}%" if gaps else "—"
                worst = f"{gaps[-1] * 100:.1f}%" if gaps and math.isfinite(gaps[-1]) else ("no trip" if gaps else "—")
                p(f"| {style} | {n}/{w} | {K} | {same}/{len(pairs)} | {med} | {worst} | {t_full:.2f} | {t_pr:.3f} |")
    p("\nTimings are numpy on one CI core; the browser search is a different implementation, so read them "
      "as relative, not absolute.\n")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--years", type=int, nargs="+", default=[2026, 2027])
    ap.add_argument("--out", default=str(config.REPO_ROOT / "results" / "tourscan"))
    ap.add_argument("--from-json", help="analyse a saved events.json instead of pulling")
    args = ap.parse_args()
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if args.from_json:
        events = json.loads(pathlib.Path(args.from_json).read_text())
    else:
        print(f"pulling {args.years} from the PDGA API ...")
        events = pull(args.years)
        (out / "events.json").write_text(json.dumps(events))
    report = analyse(events)
    (out / "report.md").write_text(report)
    print("\n" + "=" * 30 + " REPORT " + "=" * 30 + "\n")
    print(report)


if __name__ == "__main__":
    main()
