"""Biggest movers from the prediction snapshots, over day and week windows.

Emits docs/data/movers.json for the app's "Biggest movers" panel, as
{division: {window: {baseline, latest, spark_dates, movers: [...]}}}.

Windows (see MOVERS_DESIGN.md):

- **week** — both endpoints pinned to the most recent snapshot on-or-before a
  Monday: the current week's for "now", the prior week's for the baseline. The
  panel reflects "change from last Monday to this Monday" (the weekend's
  results, which finalize Sunday), stays fixed Monday through Sunday, and rolls
  over only on Mondays, regardless of how often the refresh runs.
- **day** — baseline is the newest snapshot before today; "now" is the CURRENT
  published state, read from the app bundle rather than from today's snapshot.
  That matters: snapshot.record is first-write-wins per calendar day, so during
  a tournament today's snapshot was written by the first refresh of the morning,
  before play. Comparing against it would show a day-old picture during the very
  event the tab exists to cover. Reading the bundle instead makes the daily tab
  track live play at the refresh cadence (~5 min).

Each mover carries a sparkline series over the window's horizon. Gaps in the
snapshot history are forward-filled, which is exact rather than interpolated:
snapshot.record skips a day only when the predictions are unchanged, so a
missing date means the published odds did not move.
"""
from __future__ import annotations

import csv
import datetime as dt
import json

from . import config, points

OUT = config.REPO_ROOT / "docs" / "data" / "movers.json"
APP_DATA = config.REPO_ROOT / "docs" / "data"
TOP_N = 12

WINDOWS = ("day", "week", "season")

# Per-window noise floors. Measured over this season's snapshots: consecutive-day
# per-player |delta| is overwhelmingly simulation jitter (median nonzero 0.0002
# MPO / 0.0005 FPO; p90 0.0055 / 0.0199). A 1% floor sits ~2x above that p90 and
# admits ~13 MPO / ~9 FPO players per day-step, so the daily tab fills on active
# days and stays honestly sparse on quiet ones. The week floor is unchanged.
MIN_DELTA = {"day": 0.01, "week": 0.02}

# Projected-seed story floor. Cup odds saturate: once a player is a lock
# (p_champ 1.0) nothing above can move, and the real race — the projected
# final standings position, i.e. the 2nd-vs-3rd-seed battle — is invisible to
# the odds delta. So a mover also qualifies on |Δ mean_rank| alone, for
# players with real Cup odds. Floor measured over this season's snapshots
# (consecutive-day |Δ mean_rank|, contenders at p_champ >= 0.2): p90 = 0.9,
# p95 = 1.2 — 2.0 places is clear of daily sim jitter in both divisions.
MIN_RANK_DELTA = 2.0
RANK_STORY_MIN_CHAMP = 0.2

# Sparkline horizon per window: last 7 days, last 7 Mondays. (The season window
# spans the whole season, so its axis is derived from the calendar instead.)
SPARK_N = {"day": 7, "week": 7}

# Season window is measured in standings PLACES, not odds — see _season_movers.
MIN_PLACES = 3

# Attendance for these classes is gated by standings qualification, not a
# registration list, so a player crossing the ~100% attendance threshold there
# is a projected-qualification change, not a sign-up — it must NOT appear in the
# "Registration changes" column (that signal already lives in the GMC/MVP % ).
# RE-ADD WHEN PLAYOFF REGISTRATION OPENS: once the model consumes the real
# playoff roster (simulate.run gating the GMC/MVP field on registered_field
# instead of standings rank), drop the relevant class here so genuine playoff
# sign-ups/withdrawals show again. Do NOT auto-lift on roster existence alone —
# that would re-expose standings gating as registration until the model change
# lands. See MODEL_IDEAS.md.
REG_GATED_CLASSES = ("playoff", "championship")


def _load_bundle(division: str) -> dict:
    return json.loads((APP_DATA / f"{division.lower()}.json").read_text(encoding="utf-8"))


def _live_endpoint(bundle: dict) -> dict[int, dict]:
    """The currently published state, shaped like a snapshot row.

    Lets the day window reuse every downstream comparison unchanged — the only
    difference from a real snapshot is that this one is current.
    """
    tids = [e["tid"] for e in bundle.get("events", [])]
    out: dict[int, dict] = {}
    for p in bundle.get("players", []):
        att = p.get("att") or []
        registered = ";".join(
            str(tids[i]) for i in range(min(len(tids), len(att))) if att[i] >= 0.999
        )
        out[p["pdga"]] = {
            "name": p["name"],
            "p_champ": p["p_champ"],
            "cur_rank": p["rank"],
            "rating": p.get("rating") or "",
            "registered": registered,
            "mean_rank": p.get("mean_rank", ""),
        }
    return out


def _context(bundle: dict, baseline: str, latest: str) -> tuple[dict, set[int], set[int]]:
    """Per-player 'why' context: the most recent banked result within the
    (baseline, latest] window, plus the completed and standings-gated event
    sets. Bounding at `latest` keeps the explanation frozen through the window
    even as new events finalize before the next roll-over."""
    end_of = {s["tid"]: s["end"] for s in bundle.get("schedule", [])}
    completed = {s["tid"] for s in bundle.get("schedule", []) if s.get("completed")}
    gated = {s["tid"] for s in bundle.get("schedule", []) if s.get("cls") in REG_GATED_CLASSES}
    last_result: dict[int, dict] = {}
    for p in bundle["players"]:
        recent = [b for b in p["banked"] if baseline < end_of.get(b["tid"], "") <= latest]
        if recent:
            b = max(recent, key=lambda b: end_of.get(b["tid"], ""))
            last_result[p["pdga"]] = {"tid": b["tid"], "pts": b["pts"], "place": b["place"]}
    return last_result, completed, gated


def _anchors(dates: list[str], today: dt.date, window: str) -> tuple[str | None, str | None]:
    """(baseline, latest) snapshot dates. A None `latest` means "use the live
    bundle" — only the day window does that."""
    def newest_on_or_before(cutoff: str) -> str | None:
        older = [d for d in dates if d <= cutoff]
        return older[-1] if older else None

    if window == "day":
        earlier = [d for d in dates if d < today.isoformat()]
        return (earlier[-1] if earlier else None), None

    this_monday = (today - dt.timedelta(days=today.weekday())).isoformat()
    prev_monday = (today - dt.timedelta(days=today.weekday() + 7)).isoformat()
    return newest_on_or_before(prev_monday), newest_on_or_before(this_monday)


def _spark_axis(today: dt.date, window: str) -> list[str]:
    """Calendar dates for the sparkline's x-axis, oldest first."""
    n = SPARK_N[window]
    if window == "day":
        return [(today - dt.timedelta(days=n - 1 - i)).isoformat() for i in range(n)]
    monday = today - dt.timedelta(days=today.weekday())
    return [(monday - dt.timedelta(weeks=n - 1 - i)).isoformat() for i in range(n)]


LIVE = "live"  # sentinel: this axis point reads the live bundle, not a snapshot


def _spark_resolver(axis: list[str], dates: list[str], window: str) -> list[str | None]:
    """What backs each axis point: a snapshot date (forward-filled), LIVE, or
    None where the axis predates the history — which must stay a gap in the
    line, NOT silently fall through to some other value."""
    out: list[str | None] = []
    for i, ax in enumerate(axis):
        if window == "day" and i == len(axis) - 1:
            out.append(LIVE)  # today: current published state
            continue
        older = [d for d in dates if d <= ax]
        out.append(older[-1] if older else None)
    return out


def _division_movers(division: str, window: str, rows: list[dict], dates: list[str],
                     bundle: dict, live: dict[int, dict], today: dt.date) -> dict | None:
    baseline, latest = _anchors(dates, today, window)
    live_latest = latest is None

    if window == "week":
        if baseline is None or latest is None or baseline >= latest:
            # Early season: not two Mondays of snapshots yet, so there's no clean
            # week-over-week window. Degrade to the widest available span so the
            # panel still shows something; it snaps to Monday anchoring (and its
            # stable-through-the-week behavior) once history is deep enough.
            if len(dates) < 2:
                return None
            baseline, latest = dates[0], dates[-1]
            if baseline >= latest:
                return None
    elif baseline is None:
        return None  # day window needs at least one prior snapshot

    by_date: dict[str, dict[int, dict]] = {}

    def snapshot(date: str) -> dict[int, dict]:
        if date not in by_date:
            by_date[date] = {int(r["pdga_number"]): r for r in rows if r["snapshot_date"] == date}
        return by_date[date]

    base = snapshot(baseline)
    cur = live if live_latest else snapshot(latest)
    latest_label = today.isoformat() if live_latest else latest

    last_result, completed_tids, gated_tids = _context(bundle, baseline, latest_label)

    def _rating(r: dict | None) -> int | None:
        try:
            return int(r["rating"]) if r and r.get("rating") not in (None, "") else None
        except (ValueError, TypeError):
            return None

    def _mrank(r: dict | None) -> float | None:
        try:
            return float(r["mean_rank"]) if r and r.get("mean_rank") not in (None, "") else None
        except (ValueError, TypeError):
            return None

    movers = []
    for pdga, c in cur.items():
        b = base.get(pdga)
        p_to = float(c["p_champ"])
        p_from = float(b["p_champ"]) if b else 0.0
        d = p_to - p_from
        mr_from, mr_to = _mrank(b), _mrank(c)
        mr_d = (mr_to - mr_from) if (mr_from is not None and mr_to is not None) else None
        # a big projected-seed move qualifies on its own — the odds delta is
        # blind to seed battles between locked-in players (see MIN_RANK_DELTA)
        seed_story = (mr_d is not None and abs(mr_d) >= MIN_RANK_DELTA
                      and p_to >= RANK_STORY_MIN_CHAMP)
        if abs(d) < MIN_DELTA[window] and not seed_story:
            continue
        # registration changes (why #2) — only when the baseline actually
        # recorded registrations (blank = pre-schema rows, unknowable; showing
        # everything as "added" would be fabrication)
        reg_added: list[int] = []
        reg_removed: list[int] = []
        if b and b.get("registered") and c.get("registered") is not None:
            rb = {int(t) for t in str(b["registered"]).split(";") if t}
            rc = {int(t) for t in str(c["registered"]).split(";") if t}
            # Exclude standings-gated fields (playoffs, Cup): a change there is a
            # qualification swing, not a sign-up. And a removal that's really a
            # COMPLETED event (the player played it) isn't a dropped registration
            # either. What remains: genuine sign-ups/withdrawals for still-open,
            # registration-based events.
            reg_added = sorted(t for t in (rc - rb) if t not in gated_tids)
            reg_removed = sorted(
                t for t in (rb - rc) if t not in gated_tids and t not in completed_tids
            )
        r_from, r_to = _rating(b), _rating(c)
        movers.append({
            "pdga": pdga,
            "name": c["name"],
            "champ_from": round(p_from, 4),
            "champ_to": round(p_to, 4),
            "delta": round(d, 4),
            "rank_from": int(b["cur_rank"]) if b else None,
            "rank_to": int(c["cur_rank"]),
            "last_result": last_result.get(pdga),  # why #1: newest result in window
            "reg_added": reg_added,
            "reg_removed": reg_removed,
            "rating_from": r_from,          # why #3: monthly ratings move
            "rating_to": r_to,
            "rating_delta": (r_to - r_from) if (r_from is not None and r_to is not None) else None,
            # why #4 / story: projected final standings position (mean_rank)
            "proj_rank_from": round(mr_from, 1) if mr_from is not None else None,
            "proj_rank_to": round(mr_to, 1) if mr_to is not None else None,
            "proj_rank_delta": round(mr_d, 1) if mr_d is not None else None,
        })
    # rank a 2-seat projected move like a 4-point odds move, so seed battles
    # between locked players aren't pushed off the list on busy event days
    movers.sort(key=lambda m: -(abs(m["delta"]) + 0.02 * abs(m["proj_rank_delta"] or 0.0)))
    movers = movers[:TOP_N]

    # sparkline: p_champ across the window's horizon, forward-filled
    axis = _spark_axis(today, window)
    backing = _spark_resolver(axis, dates, window)
    for m in movers:
        series: list[float | None] = []
        for src in backing:
            if src == LIVE:
                rec = live.get(m["pdga"])
            elif src is None:
                rec = None  # axis point predates the snapshot history
            else:
                rec = snapshot(src).get(m["pdga"])
            series.append(round(float(rec["p_champ"]), 4) if rec else None)
        m["spark"] = series

    return {
        "baseline": baseline,
        "latest": latest_label,
        "live_latest": live_latest,
        "spark_dates": axis,
        "movers": movers,
    }


def _rank_asof(bundle: dict, division: str, asof: str) -> dict[int, int]:
    """Standings rank as of a date, replayed from banked results.

    This is exact, not an estimate: standings are a pure function of banked
    event points under the per-class caps, and every event carries an end date.
    Filtering to the events finished by `asof` and re-running
    points.season_total reproduces the standings that stood then — which is why
    the season window can reach back to February even though snapshots only
    begin in July. (Cup odds cannot be recovered this way; they would need a
    re-sim against the field and ratings of the day, which were never stored.)
    """
    end_of = {s["tid"]: s["end"] for s in bundle.get("schedule", [])}
    totals: list[tuple[float, int]] = []
    for p in bundle.get("players", []):
        evs = [(b["tid"], b["pts"]) for b in p["banked"]
               if end_of.get(b["tid"], "9999-99-99") <= asof]
        if not evs:
            continue  # not in the standings yet on that date
        totals.append((points.season_total(evs, division), p["pdga"]))
    totals.sort(key=lambda t: -t[0])
    return {pdga: i for i, (_, pdga) in enumerate(totals, 1)}


def _season_axis(today: dt.date) -> list[str]:
    """As-of dates for the season sparkline: the end of each completed month
    this season, then today. Months before the first event resolve to no
    standings and render as a gap."""
    axis = [
        (dt.date(today.year, m + 1, 1) - dt.timedelta(days=1)).isoformat()
        for m in range(1, today.month)
    ]
    axis.append(today.isoformat())
    return axis


def _season_movers(division: str, bundle: dict, rows: list[dict], dates: list[str],
                   today: dt.date) -> dict | None:
    """Month-to-date standings movement, with a whole-season rank arc.

    Measured in places rather than Cup odds: odds can't be reconstructed for
    past dates (see _rank_asof), so a season-long odds chart would be a
    fabrication. Rank is exactly recoverable, and it's the more legible
    "who is climbing" signal anyway — the auto-bid cut is itself a rank.
    """
    month_start = today.replace(day=1)
    baseline_asof = (month_start - dt.timedelta(days=1)).isoformat()

    base_rank = _rank_asof(bundle, division, baseline_asof)
    cur_rank = {p["pdga"]: p["rank"] for p in bundle.get("players", [])}
    champ_now = {p["pdga"]: p["p_champ"] for p in bundle.get("players", [])}
    if not base_rank or not cur_rank:
        return None

    last_result, _, _ = _context(bundle, baseline_asof, today.isoformat())

    # rating move over the month, when a snapshot that old exists
    old_snap: dict[int, dict] = {}
    older = [d for d in dates if d <= baseline_asof]
    if older:
        old_snap = {int(r["pdga_number"]): r for r in rows if r["snapshot_date"] == older[-1]}

    # Restrict to the Cup bubble — within 2x the auto-bid cut (56 MPO / 36 FPO).
    # Raw place deltas are otherwise dominated by the deep field: a player's
    # first event vaults them past everyone still tied on zero, so an unfiltered
    # list reads "#299 -> #158" for players with 0.00 Cup odds while the actual
    # race is invisible. Measured on this season's data, the GMC cut (100/50)
    # was still too loose (top MPO movers all sat at 0.00-0.03 odds); 2x the
    # standings cut surfaces movement that matters (#57->#41 at 0.24, #33->#25
    # at 0.21) and keeps the sparkline's rank domain tight enough that the
    # auto-bid cutline is a meaningful reference inside it. A minimum-starts
    # filter was tried and discarded — it changed almost nothing (84 vs 86).
    contender = 2 * (bundle.get("meta", {}).get("cut") or 28)

    movers = []
    for pdga, r_to in cur_rank.items():
        r_from = base_rank.get(pdga)
        if r_from is None:
            continue  # wasn't ranked at the start of the month; no move to show
        if min(r_from, r_to) > contender:
            continue  # never in the playoff conversation this month
        places = r_from - r_to  # positive = climbed
        if abs(places) < MIN_PLACES:
            continue
        b = old_snap.get(pdga)
        try:
            r_old = int(b["rating"]) if b and b.get("rating") not in (None, "") else None
        except (ValueError, TypeError):
            r_old = None
        r_new = next((p.get("rating") for p in bundle["players"] if p["pdga"] == pdga), None)
        movers.append({
            "pdga": pdga,
            "name": next(p["name"] for p in bundle["players"] if p["pdga"] == pdga),
            "rank_from": r_from,
            "rank_to": r_to,
            "delta": places,          # places climbed; sign drives the arrow
            "champ_now": round(float(champ_now.get(pdga, 0.0)), 4),
            "last_result": last_result.get(pdga),
            "reg_added": [],
            "reg_removed": [],
            "rating_from": r_old,
            "rating_to": int(r_new) if r_new else None,
            "rating_delta": (int(r_new) - r_old) if (r_old and r_new) else None,
        })
    movers.sort(key=lambda m: -abs(m["delta"]))
    movers = movers[:TOP_N]

    axis = _season_axis(today)
    series_by_date = {a: _rank_asof(bundle, division, a) for a in axis[:-1]}
    series_by_date[axis[-1]] = cur_rank
    for m in movers:
        m["spark"] = [series_by_date[a].get(m["pdga"]) for a in axis]
    ranked = [v for m in movers for v in m["spark"] if v]
    return {
        "metric": "rank",
        "baseline": month_start.isoformat(),
        "latest": today.isoformat(),
        "live_latest": True,
        "spark_dates": axis,
        "rank_max": max(ranked) if ranked else 1,
        "movers": movers,
    }


def write_movers() -> None:
    today = dt.date.today()
    out: dict[str, dict] = {}
    for div in ("MPO", "FPO"):
        path = config.REPO_ROOT / "predictions" / f"history_{div.lower()}.csv"
        if not path.exists():
            out[div.lower()] = {w: None for w in WINDOWS}
            continue
        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        dates = sorted({r["snapshot_date"] for r in rows})
        bundle = _load_bundle(div)
        live = _live_endpoint(bundle)
        out[div.lower()] = {
            w: (_season_movers(div, bundle, rows, dates, today) if w == "season"
                else _division_movers(div, w, rows, dates, bundle, live, today))
            for w in WINDOWS
        }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    for div, windows in out.items():
        parts = []
        for w in WINDOWS:
            data = windows.get(w)
            parts.append(f"{w}={len(data['movers'])} (vs {data['baseline']})" if data else f"{w}=n/a")
        # season needs no snapshots at all — it is replayed from banked results
        print(f"  movers {div}: " + ", ".join(parts))
