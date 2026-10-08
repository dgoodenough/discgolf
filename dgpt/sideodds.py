"""Live win odds for events that award no points (config.SIDE_EVENTS).

The USDGC and Throw Pink are worth watching and worth nothing to the World
Standings. Win odds at an event normally come out of the season simulation
(`SimResult.live_stats`), but that is built on the points schedule, and
putting a no-points event on it would put the event in the standings, the
participation model, the movers panel and the scorecard too. So these get a
path of their own, and it shares only the score model:

- `build` keeps data/side_events_2026.csv: the points schedule's columns plus a
  `key` naming the config entry. It runs inside schedule.build, so the dates
  and the completed flag follow the same rules (grace night included), and an
  entry named only by its title is looked up in the PDGA listing there.
- `live_events` / `any_live` answer "is anything on?" for the live loop's
  gates, which used to ask the points schedule alone.
- `run` is the remaining-holes model on one event's live field — the same
  formula simulate._draw_singles uses for an event in progress, with no
  points, no attendance and no season around it.
- `record` is the refresh hook. It hands `run`'s result to liveodds.record,
  which already accepts anything shaped like a SimResult, and it never raises:
  a no-points event is a side panel and must not cost the forecast a publish.

Nothing here touches standings, points or the season bundle, so
`python -m dgpt.validate` cannot move because of it.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field

from . import config, live_api, ratings, schedule

SIDE_CSV = config.DATA_DIR / f"side_events_{config.SEASON}.csv"
FIELDS = ["key", *schedule.FIELDS]
CLS = "odds_only"

# Sims per refresh when the caller does not say. The refresh passes its own
# count (100,000 in the live loop), which on a 150-player field is well under a
# second; the default is only for ad-hoc runs.
DEFAULT_SIMS = 20_000
CHUNK = 5_000


# ------------------------------------------------------------------- rows

def load() -> list[dict]:
    """The odds-only events on disk (empty before the first build)."""
    if not SIDE_CSV.exists():
        return []
    with open(SIDE_CSV, newline="", encoding="utf-8") as f:
        out = []
        for r in csv.DictReader(f):
            r["tournament_id"] = int(r["tournament_id"])
            for k in ("mpo", "fpo", "fpo_points", "completed"):
                r[k] = r[k] == "True"
            out.append(r)
        return out


def _save(rows: list[dict]) -> None:
    SIDE_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(SIDE_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def _from_live(tid: int) -> dict:
    """A listing-shaped record from public live scoring (no auth needed)."""
    ev = live_api.fetch_event(tid)
    if not (ev.get("StartDate") and ev.get("EndDate")):
        raise ValueError(f"live scoring for {tid} carries no dates yet")
    return {"tournament_id": tid, "tournament_name": ev.get("Name") or str(tid),
            "start_date": ev["StartDate"], "end_date": ev["EndDate"]}


def _by_name(spec: dict, listed: list[dict], client) -> dict | None:
    """The one listing whose title contains the entry's name, or None.

    None on no match (not listed yet) and on several: picking one of several
    would put somebody's club event on the tab. The warning names each
    candidate's id so the entry can be pinned in config.
    """
    want = spec["name"].lower()
    seen: dict[int, dict] = {}

    def scan(events: list[dict]) -> None:
        for e in events:
            if want in (e.get("tournament_name") or "").lower():
                seen.setdefault(int(e["tournament_id"]), e)

    scan(listed)
    if not seen and client is not None:
        season = config.SEASON
        for tier in config.SIDE_SEARCH_TIERS:
            scan(client.events(tier=tier, start_date=f"{season}-01-01",
                               end_date=f"{season}-12-31"))
    if len(seen) == 1:
        return next(iter(seen.values()))
    if seen:
        found = ", ".join(f"{t} {e.get('tournament_name')}" for t, e in sorted(seen.items()))
        print(f"::warning::odds-only entry {spec['key']!r}: {len(seen)} listings match "
              f"{spec['name']!r} ({found}) — pin its tid in config.SIDE_EVENTS")
    else:
        print(f"  odds-only entry {spec['key']!r}: no listing matches {spec['name']!r} yet")
    return None


def _row(spec: dict, e: dict) -> dict:
    divs = spec["divisions"]
    row = schedule._row(e, CLS, mpo="MPO" in divs, fpo="FPO" in divs, fpo_points=False)
    return {"key": spec["key"], **row}


def _locate(spec: dict, listed: list[dict], prior: dict | None, client) -> dict | None:
    tid = spec.get("tid") or (prior or {}).get("tournament_id")
    if tid:
        return next((e for e in listed if int(e["tournament_id"]) == int(tid)), None) \
            or _from_live(int(tid))
    return _by_name(spec, listed, client)


def build(listed: list[dict], client=None) -> list[dict]:
    """Refresh the odds-only rows. Called from schedule.build with every event
    it already fetched, so an entry in those listings costs no extra request.

    Never raises: an entry that cannot be refreshed keeps its last row, and one
    that never had a row is left out. The points schedule must not fail
    because a no-points event's listing did.
    """
    prior = {r["key"]: r for r in load()}
    out = []
    for spec in config.SIDE_EVENTS:
        try:
            e = _locate(spec, listed, prior.get(spec["key"]), client)
            if e is not None:
                out.append(_row(spec, e))
                continue
        except Exception as exc:  # noqa: BLE001 - a side panel never fails the build
            print(f"::warning::odds-only entry {spec['key']!r} not refreshed "
                  f"({type(exc).__name__}: {exc})")
        if spec["key"] in prior:
            out.append(prior[spec["key"]])
    _save(out)
    return out


def _heal(rows: list[dict]) -> list[dict]:
    """Add any id-pinned entry the file does not have yet, from live scoring.

    The live loop's gate reads the file before anything has built it — the
    first time an entry is added, the event may already be under way — and the
    loop only starts once the gate says something is on. An id-pinned entry
    needs no authenticated listing, so it can be filled in right here. Entries
    known only by name still wait for schedule.build.
    """
    have = {r["key"] for r in rows}
    missing = [s for s in config.SIDE_EVENTS if s.get("tid") and s["key"] not in have]
    if not missing:
        return rows
    added = []
    for spec in missing:
        try:
            added.append(_row(spec, _from_live(int(spec["tid"]))))
        except Exception as exc:  # noqa: BLE001 - unstaged is a normal state
            print(f"  odds-only entry {spec['key']!r} not on live scoring yet ({exc})")
    if added:
        rows = rows + added
        _save(rows)
    return rows


def live_events(rows: list[dict] | None = None) -> list[dict]:
    """Odds-only events in progress, by the points schedule's own window."""
    rows = rows if rows is not None else _heal(load())
    return schedule.live_events(rows)


def any_live() -> list[dict]:
    """Every event the live loop should be running for, points or not."""
    return schedule.live_events() + live_events()


# ---------------------------------------------------------- the simulation

@dataclass
class SideResult:
    """The part of a SimResult liveodds.record reads, for odds-only events."""
    division: str
    pdga_numbers: list[int] = field(default_factory=list)
    names: list[str] = field(default_factory=list)
    live_stats: dict = field(default_factory=dict)   # {tid: {pdga: {cur, rem, thru, place, win, mean_place}}}
    unrated: dict = field(default_factory=dict)      # {tid: [names played at the field's floor]}


def _event_stats(tid: int, division: str, n_sims: int, rng) -> tuple[dict, dict, list[str]]:
    """({pdga: stats}, {pdga: name}, unrated names) for one live event."""
    import numpy as np

    from . import simulate

    state = live_api.live_field(tid, division)
    if not state:
        return {}, {}, []
    official = ratings.current(division)
    pdga = sorted(state)
    rated = {p: official.get(p) or state[p].get("rating") for p in pdga}
    known = [float(r) for r in rated.values() if r]
    # A player with no rating is still on the board with a real score, and
    # dropping them would hand their share to everyone else — a leader among
    # them most of all. They play at the weakest rated player's level, which
    # is what an unrated entrant at an elite event usually is.
    floor = min(known) if known else 1000.0
    unrated = [state[p].get("name") or str(p) for p in pdga if not rated[p]]
    rtg = np.array([float(rated[p] or floor) for p in pdga])
    cur = np.array([float(state[p]["cur"]) for p in pdga])
    rem = np.array([float(state[p]["rem"]) for p in pdga])
    tie = np.array([simulate.TIE_EPS * (state[p].get("place") or 0) for p in pdga])

    rpps = simulate.RATING_PTS_PER_STROKE[division]
    mu = cur - (rtg - rtg.mean()) / rpps * rem + tie
    sd = simulate.ROUND_SD * np.sqrt(rem)
    n = len(pdga)
    wins = np.zeros(n, dtype=np.int64)
    place_sum = np.zeros(n)
    done = 0
    while done < n_sims:
        c = min(CHUNK, n_sims - done)
        scores = mu[None, :] + rng.normal(0.0, 1.0, (c, n)) * sd[None, :]
        order = np.argsort(scores, axis=1)
        wins += np.bincount(order[:, 0], minlength=n)
        place = np.empty_like(order)
        place[np.arange(c)[:, None], order] = np.arange(1, n + 1)[None, :]
        place_sum += place.sum(axis=0)
        done += c

    stats = {
        p: {
            "cur": round(float(cur[j]), 1),
            "rem": round(float(rem[j]), 2),
            "thru": int(state[p].get("thru") or 0),
            "place": state[p].get("place"),
            "win": round(float(wins[j] / n_sims), 4),
            "mean_place": round(float(place_sum[j] / n_sims), 1),
        }
        for j, p in enumerate(pdga)
    }
    names = {p: state[p].get("name") or str(p) for p in pdga}
    return stats, names, unrated


def run(division: str, rows: list[dict], n_sims: int = DEFAULT_SIMS,
        seed: int | None = 2026) -> SideResult:
    """Win odds for each live odds-only event in `rows` that plays `division`.

    Seeded, like simulate.run: unchanged scores give an unchanged block, which
    is what lets liveodds.record drop a refresh in which nothing moved.
    """
    import numpy as np

    rng = np.random.default_rng(seed)
    res = SideResult(division=division)
    names: dict[int, str] = {}
    for row in rows:
        if not row[division.lower()]:
            continue
        tid = row["tournament_id"]
        stats, who, unrated = _event_stats(tid, division, n_sims, rng)
        if stats:
            res.live_stats[tid] = stats
            names.update(who)
        if unrated:
            res.unrated[tid] = unrated
    res.pdga_numbers = sorted(names)
    res.names = [names[p] for p in res.pdga_numbers]
    return res


def record(division: str, n_sims: int = DEFAULT_SIMS) -> str:
    """Simulate and record every live odds-only event in `division`.

    Never raises — the failure is printed as a workflow warning, so it shows on
    the run without turning the forecast's publish red.
    """
    from . import liveodds  # imports this module for write_json

    try:
        live = [r for r in live_events() if r[division.lower()]]
        if not live:
            return f"{division}: no odds-only event live"
        res = run(division, live, n_sims=n_sims)
        for tid, who in res.unrated.items():
            print(f"  odds-only {tid} {division}: {len(who)} unrated, played at the "
                  f"field's lowest rating: {', '.join(who)}")
        if not res.live_stats:
            return f"{division}: odds-only event live, no scores readable yet"
        return "odds-only " + liveodds.record(res, division)
    except Exception as exc:  # noqa: BLE001 - a side panel never fails the run
        print(f"::warning::{division} odds-only events skipped ({type(exc).__name__}: {exc})")
        return f"{division}: odds-only events skipped"
