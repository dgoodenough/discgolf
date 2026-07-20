"""Week-over-week movers from the prediction snapshots.

Emits the biggest Cup-odds movers per division to docs/data/movers.json for the
app's "Biggest movers" panel.

Cadence is weekly, anchored on Mondays: both endpoints are pinned to the most
recent snapshot on-or-before a Monday — the current week's Monday for "now" and
the prior week's for the baseline. That means the panel reflects "change from
last Monday to this Monday" (i.e. the weekend's results, which finalize Sunday),
stays fixed Monday through Sunday, and only rolls over on Mondays — regardless
of how often the refresh runs during live play.
"""
from __future__ import annotations

import csv
import datetime as dt
import json

from . import config

OUT = config.REPO_ROOT / "docs" / "data" / "movers.json"
APP_DATA = config.REPO_ROOT / "docs" / "data"
MIN_DELTA = 0.02   # ignore noise-level changes
TOP_N = 12


def _context(division: str, baseline: str, latest: str) -> tuple[dict, dict]:
    """Per-player 'why' context from the app bundle (written just before us):
    the most recent banked result within the (baseline, latest] window, and the
    schedule. Bounding at `latest` keeps the explanation frozen through the week
    even as new events finalize before the next Monday roll-over."""
    bundle = json.loads((APP_DATA / f"{division.lower()}.json").read_text(encoding="utf-8"))
    end_of = {s["tid"]: s["end"] for s in bundle.get("schedule", [])}
    last_result: dict[int, dict] = {}
    for p in bundle["players"]:
        recent = [b for b in p["banked"] if baseline < end_of.get(b["tid"], "") <= latest]
        if recent:
            b = max(recent, key=lambda b: end_of.get(b["tid"], ""))
            last_result[p["pdga"]] = {"tid": b["tid"], "pts": b["pts"], "place": b["place"]}
    return last_result, end_of


def _division_movers(division: str) -> dict | None:
    path = config.REPO_ROOT / "predictions" / f"history_{division.lower()}.csv"
    if not path.exists():
        return None
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    dates = sorted({r["snapshot_date"] for r in rows})
    if len(dates) < 2:
        return None

    # Anchor both endpoints to Mondays so the panel only rolls over weekly.
    today = dt.date.today()
    this_monday = (today - dt.timedelta(days=today.weekday())).isoformat()
    prev_monday = (today - dt.timedelta(days=today.weekday() + 7)).isoformat()

    def newest_on_or_before(cutoff: str) -> str | None:
        older = [d for d in dates if d <= cutoff]
        return older[-1] if older else None

    latest = newest_on_or_before(this_monday)
    baseline = newest_on_or_before(prev_monday)
    if latest is None or baseline is None or baseline >= latest:
        # Early season: not two Mondays of snapshots yet, so there's no clean
        # week-over-week window. Degrade to the widest available span so the
        # panel still shows something; it snaps to Monday anchoring (and its
        # stable-through-the-week behavior) as soon as the history is deep enough.
        latest, baseline = dates[-1], dates[0]
        if baseline >= latest:
            return None

    def by_pdga(date: str) -> dict[int, dict]:
        return {int(r["pdga_number"]): r for r in rows if r["snapshot_date"] == date}

    base, cur = by_pdga(baseline), by_pdga(latest)
    last_result, _ = _context(division, baseline, latest)
    movers = []
    for pdga, c in cur.items():
        b = base.get(pdga)
        p_to = float(c["p_champ"])
        p_from = float(b["p_champ"]) if b else 0.0
        d = p_to - p_from
        if abs(d) < MIN_DELTA:
            continue
        # registration changes since baseline (why #2) — only when the
        # baseline actually recorded registrations (blank = pre-schema rows,
        # unknowable; showing everything as "added" would be fabrication)
        reg_added: list[int] = []
        reg_removed: list[int] = []
        if b and b.get("registered") and c.get("registered") is not None:
            rb = {int(t) for t in b["registered"].split(";") if t}
            rc = {int(t) for t in c["registered"].split(";") if t}
            reg_added = sorted(rc - rb)
            reg_removed = sorted(rb - rc)
        # rating move over the window (why #3): PDGA's monthly ratings update
        # can shift Cup odds with no event played — often the whole story for a
        # quiet week. Snapshots record the rating in force at each date.
        def _rating(r: dict | None) -> int | None:
            try:
                return int(r["rating"]) if r and r.get("rating") not in (None, "") else None
            except (ValueError, TypeError):
                return None
        r_from, r_to = _rating(b), _rating(c)
        rating_delta = (r_to - r_from) if (r_from is not None and r_to is not None) else None
        movers.append({
            "pdga": pdga,
            "name": c["name"],
            "champ_from": round(p_from, 4),
            "champ_to": round(p_to, 4),
            "delta": round(d, 4),
            "rank_from": int(b["cur_rank"]) if b else None,
            "rank_to": int(c["cur_rank"]),
            "last_result": last_result.get(pdga),  # why #1: newest result since baseline
            "reg_added": reg_added,
            "reg_removed": reg_removed,
            "rating_from": r_from,          # why #3: monthly ratings move
            "rating_to": r_to,
            "rating_delta": rating_delta,
        })
    movers.sort(key=lambda m: -abs(m["delta"]))
    return {"baseline": baseline, "latest": latest, "movers": movers[:TOP_N]}


def write_movers() -> None:
    out = {div.lower(): _division_movers(div) for div in ("MPO", "FPO")}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    for div, data in out.items():
        n = len(data["movers"]) if data else 0
        print(f"  movers {div}: {n}" + (f" (vs {data['baseline']})" if data else " (need 2+ snapshots)"))
