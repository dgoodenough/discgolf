"""Win-probability history for the event in progress, and the app's chart data.

The simulation already answers "who wins this tournament" every time it runs —
`SimResult.live_stats[tid][pdga]["win"]` — but that number was only ever
published as a snapshot, so the app could say Buhr is at 26% and never that he
was at 8% two hours ago. This module keeps the series.

Two files:

- `data/live_odds.csv` — append-mostly history, one row per (refresh, player).
  Cross-run state the pipeline reads back, so it lives in data/ and stays
  tracked, next to the livecheck signature and the ratings snapshot. Bounded
  two ways: only players who are actually in the picture get a row (see
  `_block`), and only the newest `KEEP_EVENTS` tournaments are retained, so
  the file shrinks back after every event instead of growing all season.
- `docs/data/liveodds.json` — the per-division series the "Event odds" tab
  draws, built from the history each refresh (the movers.json pattern).

Cadence is the live loop's: it re-simulates within ~5 minutes of any scoring
change and skips the sim entirely when nothing moved, so a row block per
refresh is a row block per change. Identical blocks are dropped anyway — the
sim is deterministic given its inputs, so an unchanged block means unchanged
scores, not a second observation.
"""
from __future__ import annotations

import csv
import datetime as dt
import json

from . import config, schedule

HISTORY = config.DATA_DIR / "live_odds.csv"
OUT = config.REPO_ROOT / "docs" / "data" / "liveodds.json"

FIELDS = ["taken_at", "tid", "division", "pdga_number", "name",
          "thru", "rem", "place", "cur", "win"]

# Tournaments kept in the history. 1 would drop the just-finished event the
# moment the next one is staged for live scoring — which is days before it
# tees off, so the tab would go blank mid-week with a finished race still
# worth showing. 2 keeps that race until the new one has actually started.
KEEP_EVENTS = 2

# Who gets a row. `win > 0` alone would truncate a collapsing leader's line at
# exactly the interesting moment and leave the app unable to tell "fell to
# zero" from "no data"; carrying the top of the leaderboard regardless means a
# charted player missing from a block was genuinely out of it, which is what
# `_series` reads a gap as. ~40 rows a block at a 200-player major.
RECORD_PLACE = 25

# Which of them get a line. Everyone still alive, in order, plus a few players
# who are out of it now but were genuinely in it earlier — the collapse is half
# the story of a tournament and a chart that only shows the survivors cannot
# tell it. Both bounded, because a dozen lines is already the readable limit,
# and the peak bar is high (15%, not "was briefly above the noise") so those
# slots go to real leads rather than to the flat 1/N band every player sits in
# on Thursday morning.
CHART_MIN = 0.001   # "still alive": 0.1%, the app's own floor for a real number
PEAK_MIN = 0.15     # "was alive": actually led the race at some point
MAX_FALLEN = 3
MAX_LINES = 12


def _now() -> str:
    """UTC, offset-stamped — the app parses these as absolute instants.

    Milliseconds, not seconds: the timestamp is this file's block key, and two
    refreshes landing in the same second would merge into one observation with
    half its rows silently dropped as duplicates.
    """
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")


def _read() -> list[dict]:
    if not HISTORY.exists():
        return []
    with open(HISTORY, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _write(rows: list[dict]) -> None:
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def _block(res, division: str, tid: int, taken: str) -> list[dict]:
    """One refresh's rows for one live event, ordered for a stable diff."""
    out = []
    stats = res.live_stats.get(tid, {})
    for i, pdga in enumerate(res.pdga_numbers):
        st = stats.get(pdga)
        if st is None:
            continue
        place = st.get("place")
        if not (st["win"] > 0 or (place is not None and place <= RECORD_PLACE)):
            continue
        out.append({
            "taken_at": taken, "tid": tid, "division": division,
            "pdga_number": pdga, "name": res.names[i],
            "thru": st.get("thru", 0), "rem": st["rem"],
            "place": "" if place is None else place,
            "cur": st["cur"], "win": st["win"],
        })
    out.sort(key=lambda r: r["pdga_number"])
    return out


def _shape(rows) -> list[tuple]:
    """The part of a block that makes it a distinct observation."""
    return [(str(r["pdga_number"]), str(r["thru"]), str(r["place"]),
             str(r["cur"]), str(r["win"])) for r in rows]


def _last_seen(rows: list[dict]) -> dict[str, tuple]:
    """Per tid, the position of its newest row: (taken_at, file order).

    File order is the tie-break rather than decoration — equal timestamps are
    rare but a coin-flip on which event is "current" is not something to leave
    to sort stability, and rows are only ever appended.
    """
    last: dict[str, tuple] = {}
    for i, r in enumerate(rows):
        tid, key = str(r["tid"]), (r["taken_at"], i)
        if key > last.get(tid, ("", -1)):
            last[tid] = key
    return last


def _keep_tids(rows: list[dict]) -> set[str]:
    """The newest KEEP_EVENTS tids, newest = latest row anywhere in the file."""
    last = _last_seen(rows)
    return set(sorted(last, key=lambda t: last[t], reverse=True)[:KEEP_EVENTS])


def record(res, division: str) -> str:
    """Append this refresh's live win probabilities. No live event = no-op."""
    if not res.live_stats:
        return f"{division}: no live event — no odds recorded"
    taken = _now()
    existing = _read()
    added, skipped = [], []
    for tid in sorted(res.live_stats):
        block = _block(res, division, tid, taken)
        if not block:
            continue
        prior = [r for r in existing
                 if str(r["tid"]) == str(tid) and r["division"] == division]
        if prior:
            newest = max(r["taken_at"] for r in prior)
            if _shape([r for r in prior if r["taken_at"] == newest]) == _shape(block):
                skipped.append(tid)
                continue
        added += block
    if not added:
        return f"{division}: live odds unchanged — skipped"

    # Retention is per division, and the filter preserves file order: the two
    # divisions do not always play the same calendar (Heinola awards no FPO
    # points), and a global cut could drop a division's only event. Reordering
    # would also turn every write into a whole-file diff.
    rows = existing + added
    keep = {div: _keep_tids([r for r in rows if r["division"] == div])
            for div in {r["division"] for r in rows}}
    rows = [r for r in rows if str(r["tid"]) in keep[r["division"]]]
    _write(rows)
    note = f" ({len(skipped)} unchanged)" if skipped else ""
    return f"{division}: recorded live odds ({len(added)} players){note}"


# ------------------------------------------------------------------ export

def _series(rows: list[dict], live_tids: set[int], names: dict[int, str]) -> dict | None:
    """One division's chart payload from its rows for a single event."""
    if not rows:
        return None
    stamps = sorted({r["taken_at"] for r in rows})
    at = {s: i for i, s in enumerate(stamps)}
    n = len(stamps)

    # X is holes played, not wall-clock: it puts the overnight gaps at zero
    # width and makes the round boundaries real gridlines. Read off the front
    # of the field and forced non-decreasing, because the recorded set changes
    # between blocks and a leader dropping out of it must not walk time back.
    x = [0] * n
    for r in rows:
        i = at[r["taken_at"]]
        x[i] = max(x[i], int(r["thru"]))
    for i in range(1, n):
        x[i] = max(x[i], x[i - 1])

    by_player: dict[int, dict[int, float]] = {}
    latest: dict[int, dict] = {}
    for r in rows:
        p = int(r["pdga_number"])
        by_player.setdefault(p, {})[at[r["taken_at"]]] = float(r["win"])
        if r["taken_at"] == stamps[-1]:
            latest[p] = r

    def now_win(p: int) -> float:
        return by_player[p].get(n - 1, 0.0)

    peak = {p: max(v.values()) for p, v in by_player.items()}
    alive = sorted((p for p in by_player if now_win(p) > CHART_MIN),
                   key=lambda p: (-now_win(p), -peak[p]))
    fallen = sorted((p for p in by_player
                     if now_win(p) <= CHART_MIN and peak[p] >= PEAK_MIN),
                    key=lambda p: -peak[p])[:MAX_FALLEN]
    charted = alive[:MAX_LINES - len(fallen)] + fallen

    series = []
    for p in charted:
        seen = by_player[p]
        last = latest.get(p)
        series.append({
            "pdga": p,
            "name": names.get(p, str(p)),
            # A gap is not missing data: a player only drops out of a block by
            # falling below RECORD_PLACE with no win equity left, so the line
            # goes to the floor rather than breaking.
            "y": [round(seen.get(i, 0.0), 5) for i in range(n)],
            "win": round(now_win(p), 5),
            "peak": round(peak[p], 5),
            "place": (last or {}).get("place", ""),
            "cur": float(last["cur"]) if last else None,
        })

    tid = int(rows[0]["tid"])
    # Total holes from the feed's own remaining-rounds count rather than the
    # model's per-class round constant, which is a class default and lands a
    # round short at events that play more (see app.js liveThru).
    holes = max(int(r["thru"]) + round(float(r["rem"]) * 18) for r in rows)
    return {
        "tid": tid,
        "live": tid in live_tids,
        "updated": stamps[-1],
        "holes": holes,
        "x": x,
        "t": stamps,
        "series": series,
        # players above the 0.1% line that the cap left off the chart
        "others": max(0, len(alive) - sum(1 for p in charted if p in alive)),
        # the two bars the app quotes when it explains which lines it drew
        "chart_min": CHART_MIN,
        "peak_min": PEAK_MIN,
        # win at the previous observation, for EVERY recorded player — the
        # table's move column covers the whole >0.1% list, not just the
        # dozen the chart had room to draw
        "prev": {str(p): round(v.get(n - 2, 0.0), 5) for p, v in by_player.items()}
                if n > 1 else {},
        "tracked_from": x[0],
    }


def write_json() -> str:
    """Build docs/data/liveodds.json from the history. Newest event per division."""
    rows = _read()
    sched = {r["tournament_id"]: r for r in schedule.load()}
    live_tids = {r["tournament_id"] for r in schedule.live_events()}
    out: dict[str, dict | None] = {}
    for division in ("MPO", "FPO"):
        mine = [r for r in rows if r["division"] == division]
        payload = None
        if mine:
            last = _last_seen(mine)
            newest = max(last, key=lambda t: last[t])
            ev = [r for r in mine if str(r["tid"]) == newest]
            names = {int(r["pdga_number"]): r["name"] for r in ev}
            payload = _series(ev, live_tids, names)
        if payload:
            row = sched.get(payload["tid"])
            payload["event"] = row["name"] if row else str(payload["tid"])
            payload["cls"] = row["cls"] if row else ""
        out[division.lower()] = payload
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    have = [k for k, v in out.items() if v]
    return f"wrote {OUT.name} ({', '.join(have) if have else 'no live history yet'})"
