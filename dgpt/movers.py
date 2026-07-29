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

from . import config

OUT = config.REPO_ROOT / "docs" / "data" / "movers.json"
APP_DATA = config.REPO_ROOT / "docs" / "data"
TOP_N = 12

WINDOWS = ("day", "week")

# Per-window noise floors. Measured over this season's snapshots: consecutive-day
# per-player |delta| is overwhelmingly simulation jitter (median nonzero 0.0002
# MPO / 0.0005 FPO; p90 0.0055 / 0.0199). A 1% floor sits ~2x above that p90 and
# admits ~13 MPO / ~9 FPO players per day-step, so the daily tab fills on active
# days and stays honestly sparse on quiet ones. The week floor is unchanged.
MIN_DELTA = {"day": 0.01, "week": 0.02}

# Sparkline horizon per window: last 7 days, last 7 Mondays.
SPARK_N = {"day": 7, "week": 7}

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

    movers = []
    for pdga, c in cur.items():
        b = base.get(pdga)
        p_to = float(c["p_champ"])
        p_from = float(b["p_champ"]) if b else 0.0
        d = p_to - p_from
        if abs(d) < MIN_DELTA[window]:
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
        })
    movers.sort(key=lambda m: -abs(m["delta"]))
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
            w: _division_movers(div, w, rows, dates, bundle, live, today) for w in WINDOWS
        }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    for div, windows in out.items():
        parts = []
        for w in WINDOWS:
            data = windows.get(w)
            parts.append(f"{w}={len(data['movers'])} (vs {data['baseline']})" if data else f"{w}=n/a")
        print(f"  movers {div}: " + ", ".join(parts))
