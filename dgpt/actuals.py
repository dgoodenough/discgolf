"""Resolve what actually happened, so dgpt.evaluate can grade the forecast.

`evaluate` needs a CSV of outcomes per player. None of it has to be assembled
by hand — every outcome is a read of data the pipeline already fetches:

    auto_bid         final World Standings rank <= config.STANDINGS_CUT
    gmc_points_cut   standings rank going INTO GMC <= the GMC points cut
    mvp_points_cut   standings rank going INTO the MVP Open <= its points cut
    made_gmc         in the GMC field
    made_mvp         in the MVP Open field
    made_cup         in the Powerball Cup field

The two kinds of outcome have very different shelf lives, and that is the
whole reason this module exists rather than a one-off script in December.

**Standings outcomes keep.** They are pure functions of banked results, and
banked results are cached forever, so `auto_bid` and the two points-cut
columns can be recomputed from scratch at any point after the fact — including
the "rank going into GMC" ones, which are replayed from event end dates the
same way movers._rank_asof reconstructs a past standings table.

**Field membership does not keep.** A PDGA Live roster is readable for a
window around its event and then goes dark: Pro Worlds lost its staged roster
on the morning of the tournament (2026-08-26), which is why
data/known_fields.json had to exist at all. The Powerball Cup is the worst
case — it awards no points, so no other part of the pipeline ever fetches it,
and nothing would have banked its field before the page changed.

So `capture()` runs on every refresh and unions whatever is readable for the
three gated events into data/actual_fields.json, which is committed. It only
ever grows, and `resolve()` prefers that accumulated record over a fresh read,
because a roster that has since gone dark is a worse answer than the one
banked while it was up.

Outcomes that have not happened yet are written blank, which is exactly what
`evaluate` skips — so the file is worth generating from the first run and
fills itself in as the season lands:

    python -m dgpt.actuals                  # refresh predictions/actuals_*.csv
    python -m dgpt.evaluate --division MPO  # grade against it

Stdlib only, like evaluate: grading must not need the simulation installed.
"""
from __future__ import annotations

import csv
import datetime as dt
import json

from . import config, live_api, points, schedule, standings

ACTUALS_DIR = config.REPO_ROOT / "predictions"
CAPTURE_FILE = config.DATA_DIR / "actual_fields.json"

FIELDS = [
    "pdga_number", "name", "division",
    "auto_bid", "made_cup", "made_gmc", "made_mvp",
    "gmc_points_cut", "mvp_points_cut",
    "final_rank", "final_points",
]

# The events whose FIELD is itself something the model forecasts, and the
# actuals column each one resolves. These are the qualification-gated events:
# their fields come from the standings rather than open registration, which is
# what makes membership a prediction rather than a fact known in advance.
GATED = {
    config.TID_GMC: "made_gmc",
    config.TID_MVP: "made_mvp",
    config.TID_CHAMPIONSHIP: "made_cup",
}


# ------------------------------------------------------------------ capture

def _load_capture() -> dict:
    try:
        return json.loads(CAPTURE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _readable_field(tid: int, division: str, scope: set[int]) -> set[int]:
    """Everyone we can currently see in an event's field, from every source.

    Union, not a priority order: each source sees a different slice and none
    is complete on its own. The live roster carries entrants who later
    withdrew (which is what "in the field" means for the model — it draws
    them), final_results carries finishers and survives in the results cache
    long after the roster goes dark, and the event page carries a signup list
    months before either exists.

    `scope` is the division's known players, used to filter the event page,
    which lists every division at once and carries no division of its own.
    """
    found: set[int] = set()
    try:
        found |= set(live_api._live_roster(tid, division))
    except Exception:  # noqa: BLE001 - a dark source is not an error here
        pass
    try:
        found |= {r["pdga_number"] for r in live_api.final_results(tid, division)
                  if r.get("pdga_number")}
    except Exception:  # noqa: BLE001
        pass
    try:
        found |= (live_api.page_registrants(tid) & scope)
    except Exception:  # noqa: BLE001
        pass
    return found


def capture(divisions: tuple[str, ...] = ("MPO", "FPO")) -> list[str]:
    """Bank the readable field of each gated event into CAPTURE_FILE.

    Runs on every refresh (cheap: the fetches are memoized per process, and an
    event that has not started yet is skipped without a request). Merges by
    union and never removes anyone, so a source going dark after we have seen
    it costs nothing — which is the entire point.
    """
    sched = {r["tournament_id"]: r for r in schedule.load()}
    horizon = _horizon()
    data = _load_capture()
    notes: list[str] = []
    changed = False
    scopes: dict[str, set[int]] = {}   # standings.compute is not cheap; once each

    for tid in GATED:
        row = sched.get(tid)
        # Nothing to read before the field exists. The playoffs stage on PDGA
        # Live and publish signups on the event page ahead of time, so open
        # the window early rather than at start_date — but not so early that
        # every refresh all season pays for three dead fetches.
        if not row or row["start_date"] > horizon:
            continue
        for division in divisions:
            if not row[division.lower()]:
                continue
            if division not in scopes:
                scopes[division] = {r["pdga_number"] for r in standings.compute(division)}
            seen = _readable_field(tid, division, scopes[division])
            if not seen:
                continue
            bucket = data.setdefault(str(tid), {})
            before = set(bucket.get(division) or [])
            merged = before | seen
            if merged != before:
                bucket[division] = sorted(merged)
                changed = True
                notes.append(f"{row['name'][:32]} {division}: "
                             f"{len(merged)} in field (+{len(merged - before)})")
    if changed:
        CAPTURE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CAPTURE_FILE.write_text(json.dumps(data, separators=(",", ":"), sort_keys=True),
                                encoding="utf-8")
    return notes


def _horizon(days: int = 45) -> str:
    """The furthest-out start date whose field is worth looking for yet.

    An event starting after this is skipped without a request. Six weeks
    covers the playoff registration waves (config.REG_PHASES opens its last
    one about two weeks before GMC) without paying for three dead fetches on
    every refresh all season.
    """
    return (dt.date.today() + dt.timedelta(days=days)).isoformat()


def field_members(tid: int, division: str, scope: set[int] | None = None) -> set[int] | None:
    """Who was in an event's field, or None if that is still unknown.

    None and an empty set are emphatically different: None means "we cannot
    say", which resolve() writes as a blank for evaluate to skip, while an
    empty set would mean "nobody", which would score every player as a miss.
    """
    banked = (_load_capture().get(str(tid)) or {}).get(division)
    if banked:
        return {int(p) for p in banked}
    live = _readable_field(tid, division, scope or set())
    return live or None


# --------------------------------------------------------------- standings

def _rank_asof(table: list[dict], sched: list[dict], division: str,
               asof: str | None) -> dict[int, int]:
    """Standings rank per player, optionally as it stood before `asof`.

    Exact rather than estimated, for the same reason movers._rank_asof is:
    standings are a pure function of banked points under the per-class caps,
    and every event carries an end date, so filtering to what had finished
    reproduces the table that stood then.
    """
    if asof is None:
        return {r["pdga_number"]: r["rank"] for r in table}
    end_of = {r["tournament_id"]: r["end_date"] for r in sched}
    totals: list[tuple[float, int]] = []
    for p in table:
        evs = [(tid, pts) for tid, pts, _, _ in p["events"]
               if end_of.get(tid, "9999-99-99") < asof]
        if evs:
            totals.append((points.season_total(evs, division), p["pdga_number"]))
    totals.sort(key=lambda t: -t[0])
    return {pdga: i for i, (_, pdga) in enumerate(totals, 1)}


def _settled_before(sched: list[dict], division: str, asof: str) -> bool:
    """Has every points event that ends before `asof` actually banked?

    Guards every standings outcome. A rank computed while the last event
    before the cutoff is still being played is not the rank that set the
    field, and writing it would freeze a half-finished tournament into the
    thing the whole season gets graded against.
    """
    return all(
        r["completed"]
        for r in sched
        if r["end_date"] < asof and r["cls"] != "championship"
        and r[division.lower()] and (division == "MPO" or r["fpo_points"])
    )


def _start_of(sched: list[dict], tid: int) -> str | None:
    return next((r["start_date"] for r in sched if r["tournament_id"] == tid), None)


# ---------------------------------------------------------------- resolving

def _history_players(division: str) -> dict[int, str]:
    """Everyone the model ever published a number for, from the snapshots.

    The row set has to come from the history rather than the final standings:
    a player the model gave 3% and who then missed the cut entirely is exactly
    the prediction worth grading, and they may have no standings row at all.
    """
    path = ACTUALS_DIR / f"history_{division.lower()}.csv"
    out: dict[int, str] = {}
    if not path.exists():
        return out
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out[int(r["pdga_number"])] = r["name"]
    return out


def resolve(division: str) -> tuple[list[dict], list[str]]:
    """(rows, notes) — outcomes per player, blank where still undecided."""
    sched = schedule.load()
    table = standings.compute(division)
    by_pdga = {r["pdga_number"]: r for r in table}
    scope = set(by_pdga)
    notes: list[str] = []

    players = _history_players(division)
    for pdga, rec in by_pdga.items():      # anyone banked but never snapshotted
        players.setdefault(pdga, rec["name"] or "")

    # -- automatic bid: the final standings, once the season has finished --
    season_end = "9999-99-99"
    season_done = _settled_before(sched, division, season_end)
    final_rank = _rank_asof(table, sched, division, None)
    if not season_done:
        notes.append("auto_bid: pending — points events still to play")

    # -- the two points cuts, off the standings that set each playoff field --
    cuts: dict[str, tuple[dict[int, int], int] | None] = {}
    for key, tid, col in (("gmc", config.TID_GMC, "gmc_points_cut"),
                          ("mvp", config.TID_MVP, "mvp_points_cut")):
        start = _start_of(sched, tid)
        if start and _settled_before(sched, division, start):
            cuts[col] = (_rank_asof(table, sched, division, start),
                         config.PLAYOFF_QUAL[key]["cut"][division])
        else:
            cuts[col] = None
            notes.append(f"{col}: pending — events before {start or '?'} not all banked")

    # -- field membership for the three gated events --
    fields_by_col: dict[str, set[int] | None] = {}
    for tid, col in GATED.items():
        members = field_members(tid, division, scope)
        fields_by_col[col] = members
        notes.append(f"{col}: {len(members)} in field" if members is not None
                     else f"{col}: pending — no field readable yet")

    rows = []
    for pdga, name in sorted(players.items()):
        rec = by_pdga.get(pdga)
        row = {
            "pdga_number": pdga,
            "name": name or (rec["name"] if rec else ""),
            "division": division,
            "final_rank": final_rank.get(pdga, ""),
            "final_points": rec["points"] if rec else 0.0,
        }
        row["auto_bid"] = (
            int(final_rank.get(pdga, 10**6) <= config.STANDINGS_CUT[division])
            if season_done else ""
        )
        for col, cut in cuts.items():
            row[col] = "" if cut is None else int(cut[0].get(pdga, 10**6) <= cut[1])
        for col, members in fields_by_col.items():
            row[col] = "" if members is None else int(pdga in members)
        rows.append(row)
    return rows, notes


def write(division: str) -> str:
    rows, notes = resolve(division)
    ACTUALS_DIR.mkdir(parents=True, exist_ok=True)
    out = ACTUALS_DIR / f"actuals_{division.lower()}.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    resolved = sum(1 for c in FIELDS
                   if c not in ("pdga_number", "name", "division", "final_rank", "final_points")
                   and rows and rows[0][c] != "")
    for n in notes:
        print(f"    {n}")
    return f"{division}: wrote {out.name} ({len(rows)} players, {resolved}/6 outcomes resolved)"


def main() -> None:
    for note in capture():
        print(f"  captured {note}")
    for division in ("MPO", "FPO"):
        print("  " + write(division))


if __name__ == "__main__":
    main()
