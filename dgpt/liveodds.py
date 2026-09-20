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
# "dead": took none of the 10,000 simulated tournaments. Not a round number
# chosen for looks — with `win` rounded to four places it is exactly this
# model's resolution floor, so under it is the strongest thing the simulation
# can say about a player. Climbing back out takes CHART_MIN, ten times more.
DEAD_MIN = 0.0001
PEAK_MIN = 0.15     # "was alive": actually led the race at some point
MAX_FALLEN = 3
MAX_LINES = 12

# The fork line, from the Upshot podcast's question: at what score can you
# stick a fork in them, because they are done? It is the complement of the race
# chart above — not who is winning, but how far back the tournament is still
# live.
#
# A line is a SCORE, and which score depends on how wrong you are willing to
# be. Walk the board from the leader down, adding up the win probability
# sitting on each score, and stop once you have covered this much of it.
# Everything below is the rest of the distribution, and that rest IS the chance
# the call is wrong — so each line's label is its own error rate. Over a
# 25-event season, 1-in-20 is being wrong about once a year, 1-in-200 about
# once a decade.
#
# That calibration is the whole reason for cutting this way. The first version
# cut on each player's own win probability — "the worst score still above 1%" —
# which reads the same and is not: how much of the field sits below such a line
# depends on how many players clear the bar, so measured over this season's
# recorded blocks the ">10%" line carried a median 13% chance of being wrong
# and 47% at its worst. A label that swings four-fold behind a fixed number is
# not telling the reader anything.
#
# Accumulated over SCORES, best first, so the covered set is downward-closed.
# Taking the top players by win probability until they summed to 95% instead
# would let a -8 on 1.2% be skipped while a -6 on 1.5% was kept, which leaves
# mass above the line uncounted and makes the label a lie.
#
# Outermost first: lines[0] is the most conservative cut, which is the BOTTOM
# of the chart, so the app's bands and its colour ramp both run with the index.
FORK_COVER = (0.995, 0.95, 0.80)


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

def _fork(blocks: dict[int, list[dict]], n: int) -> dict:
    """Per observation, the winner's-score quantile at each of FORK_COVER."""
    # One pass per block: the win probability sitting on each score, and the
    # strongest player on it — the one to name if that score becomes a line.
    # A quantile cut lands on a score, not on a player, so the whole tied group
    # is in or out together and "who is on the line" has to be chosen.
    boards = []
    for i in range(n):
        mass: dict[float, float] = {}
        face: dict[float, dict] = {}
        for r in blocks[i]:
            score, win = float(r["cur"]), float(r["win"])
            mass[score] = mass.get(score, 0.0) + win
            if score not in face or win > float(face[score]["win"]):
                face[score] = r
        boards.append((mass, face, sorted(mass), sum(mass.values())))

    lines, who, alive, names = [], [], [], {}
    for cover in FORK_COVER:
        ys: list[float | None] = []
        holders: list[int | None] = []
        counts: list[int] = []
        for i, (mass, face, scores, total) in enumerate(boards):
            # No equity anywhere in the block is not a state the live feed
            # produces — somebody wins every simulated tournament — but a
            # quantile of nothing has no answer, so say so rather than divide.
            if total <= 0:
                ys.append(None)
                holders.append(None)
                counts.append(0)
                continue
            run, line = 0.0, scores[-1]
            for score in scores:
                # Normalised by the block's own total: `win` is rounded to four
                # places per player, so a block lands within ~0.001 of 1 rather
                # than on it, and at 0.995 that rounding alone is worth a stroke.
                run += mass[score] / total
                if run >= cover - 1e-9:
                    line = score
                    break
            p = int(face[line]["pdga_number"])
            names[str(p)] = face[line]["name"]
            ys.append(line)
            holders.append(p)
            counts.append(sum(1 for r in blocks[i] if float(r["cur"]) <= line))
        lines.append(ys)
        who.append(holders)
        alive.append(counts)
    return {
        "cover": list(FORK_COVER),
        "lines": lines,
        "who": who,
        # how many players are at or better than the line — the count is what
        # makes a risk level concrete ("thirty-four are still in it at 1-in-20")
        "alive": alive,
        # names for the players on a line only, which is a few dozen over an
        # event rather than the whole recorded field
        "names": names,
        # the best score on the board, so the chart has a top edge and the
        # reader can see the gap the fork line is being measured back from
        "lead": [min(float(r["cur"]) for r in blocks[i]) for i in range(n)],
    }


def _marks(by_player: dict[int, dict[int, float]], names: dict[int, str],
           n: int) -> tuple[list, dict]:
    """Deaths and comebacks: the chart's tally, and each player's verdict.

    A player dies when their own odds fall under DEAD_MIN and is alive again
    only once they climb back to CHART_MIN. The ten-fold gap is the whole
    mechanism — on a single threshold anyone sitting on it flickers between
    marks every few minutes. Measured over the four recorded races it holds
    the worst case to two deaths for one player, and keeps the comeback rare:
    three across four races, against 27 of them and three-deaths-per-player
    flapping if the rise were set at 0.02%.

    The thresholds are the tab's own numbers rather than new ones. DEAD_MIN is
    the model's resolution floor, so falling under it is not "unlikely" but
    "took none of the ten thousand". CHART_MIN is what every other panel here
    means by alive, so a comeback means the player is listed again.

    Not the fork chart's cuts, which the same question could have been asked
    of: those are scores. A player slips under one of them without playing a
    bad hole whenever the field ahead birdies, so they answer "is this score
    still live", not "is this player".
    """
    marks: list[list] = []
    out: dict[str, list] = {}
    for pdga, seen in by_player.items():
        start = min(seen)
        name = names.get(pdga, str(pdga))
        alive = seen.get(start, 0.0) >= DEAD_MIN
        death = None
        for i in range(start, n):
            v = seen.get(i, 0.0)
            if alive and v < DEAD_MIN:
                alive, death = False, i
                marks.append([i, 0, name])
            elif not alive and v >= CHART_MIN:
                alive = True
                marks.append([i, 1, name])
        # Anyone the run ends on is out, and `death` separates the two ways of
        # being so: a moment the reader watched, or never having been in it —
        # recorded for a top-25 place and never once above the floor.
        if not alive:
            out[str(pdga)] = [death, name]
    marks.sort(key=lambda m: (m[0], m[1]))
    return marks, out


def _series(rows: list[dict], live_tids: set[int], names: dict[int, str]) -> dict | None:
    """One division's chart payload from its rows for a single event."""
    if not rows:
        return None
    stamps = sorted({r["taken_at"] for r in rows})
    at = {s: i for i, s in enumerate(stamps)}
    n = len(stamps)

    # X is holes played, not wall-clock: it puts the overnight gaps at zero
    # width and makes the round boundaries real gridlines.
    #
    # It is the field's MEAN progress, not the front of the field. Reading the
    # front pins the axis the moment the first card finishes: at Idlewild R1
    # the max sat at 18 for six hours and 54 consecutive observations while
    # the late cards played and the odds moved, so an afternoon of the
    # tournament collapsed onto one x and the chart drew a vertical line.
    #
    # The lead card is worse, not better, and it is worth writing down why:
    # it tees off LAST, so anchoring there pins the axis through the whole
    # morning wave instead. Measured over the same event, the share of
    # observations that land on top of their predecessor is 80% for the front
    # of the field, 82% for the lead card, and 3% for the mean.
    #
    # Still forced non-decreasing: the recorded set shrinks as players fall
    # out of contention, and a change in who is in it must not walk the
    # tournament backwards.
    blocks: dict[int, list[dict]] = {}
    for r in rows:
        blocks.setdefault(at[r["taken_at"]], []).append(r)

    x = [0] * n
    for i, blk in blocks.items():
        x[i] = round(sum(int(r["thru"]) for r in blk) / len(blk))
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

    marks, out = _marks(by_player, names, n)

    tid = int(rows[0]["tid"])
    # Total holes from the feed's own remaining-rounds count rather than the
    # model's per-class round constant, which is a class default and lands a
    # round short at events that play more (see docs/js/cells.js liveThru).
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
        "fork": _fork(blocks, n),
        # The chart tally, and the table's verdict. Both carry their own names
        # because neither is drawn with the bundle of players to hand, the same
        # way the fork lines do.
        "marks": marks,
        "out": out,
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
