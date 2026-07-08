"""Week-over-week movers from the prediction snapshots.

Compares the latest snapshot against a baseline (the most recent snapshot at
least 5 days older, else the earliest) and emits the biggest Cup-odds movers
per division to docs/data/movers.json for the app's "Biggest movers" panel.
"""
from __future__ import annotations

import csv
import datetime as dt
import json

from . import config

OUT = config.REPO_ROOT / "docs" / "data" / "movers.json"
MIN_DELTA = 0.02   # ignore noise-level changes
TOP_N = 12
BASELINE_MIN_AGE_DAYS = 5


def _division_movers(division: str) -> dict | None:
    path = config.REPO_ROOT / "predictions" / f"history_{division.lower()}.csv"
    if not path.exists():
        return None
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    dates = sorted({r["snapshot_date"] for r in rows})
    if len(dates) < 2:
        return None
    latest = dates[-1]
    latest_d = dt.date.fromisoformat(latest)
    old_enough = [d for d in dates[:-1] if (latest_d - dt.date.fromisoformat(d)).days >= BASELINE_MIN_AGE_DAYS]
    baseline = old_enough[-1] if old_enough else dates[0]

    def by_pdga(date: str) -> dict[int, dict]:
        return {int(r["pdga_number"]): r for r in rows if r["snapshot_date"] == date}

    base, cur = by_pdga(baseline), by_pdga(latest)
    movers = []
    for pdga, c in cur.items():
        b = base.get(pdga)
        p_to = float(c["p_champ"])
        p_from = float(b["p_champ"]) if b else 0.0
        d = p_to - p_from
        if abs(d) < MIN_DELTA:
            continue
        movers.append({
            "pdga": pdga,
            "name": c["name"],
            "champ_from": round(p_from, 4),
            "champ_to": round(p_to, 4),
            "delta": round(d, 4),
            "pts_from": float(b["cur_points"]) if b else 0.0,
            "pts_to": float(c["cur_points"]),
            "rank_from": int(b["cur_rank"]) if b else None,
            "rank_to": int(c["cur_rank"]),
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
