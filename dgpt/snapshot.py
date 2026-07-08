"""Append-only prediction snapshots for end-of-season backtesting.

Each refresh records the model's current probabilities per player so the
forecast can be scored later (Brier, log-loss, calibration) against the
actual outcomes, which are derivable from the final standings.

Cadence: at most one snapshot per calendar day, and only when the predictions
differ from the last snapshot — the sim is deterministic given its inputs, so
midweek days with no new results add nothing.
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib

from . import config, schedule

SNAP_DIR = config.REPO_ROOT / "predictions"
FIELDS = [
    "snapshot_date", "taken_at", "events_completed", "division",
    "pdga_number", "name", "rating", "cur_rank", "cur_points",
    "p_champ", "p_cut", "p_gmc", "p_mvp", "p_mvp_qual", "p_first",
    "mean_pts", "mean_rank", "registered",
]
# columns whose change makes a snapshot "new" (exclude timestamps/names)
_PRED_KEYS = ["pdga_number", "cur_points", "p_champ", "p_cut", "p_gmc",
              "p_mvp", "p_mvp_qual", "p_first", "mean_pts", "mean_rank"]


def _rows(res, division: str, n_completed: int, date: str, taken: str) -> list[dict]:
    rows = []
    # remaining events each player is registered for (att prob pinned to 1),
    # so later snapshots can explain odds moves via registration changes
    reg_tids = [ev["tid"] for ev in res.events_meta]
    for i in range(len(res.names)):
        registered = ";".join(
            str(reg_tids[e]) for e in range(len(reg_tids)) if res.att_probs[e, i] >= 0.999
        )
        rows.append({
            "snapshot_date": date,
            "taken_at": taken,
            "events_completed": n_completed,
            "division": division,
            "pdga_number": res.pdga_numbers[i],
            "name": res.names[i],
            "rating": int(res.ratings[i]) if res.ratings[i] else "",
            "cur_rank": res.current_rank[i],
            "cur_points": round(float(res.current_points[i]), 2),
            "p_champ": round(float(res.p_champ[i]), 5),
            "p_cut": round(float(res.p_cut[i]), 5),
            "p_gmc": round(float(res.p_gmc[i]), 5),
            "p_mvp": round(float(res.p_mvp[i]), 5),
            "p_mvp_qual": round(float(res.p_mvp_qual[i]), 5),
            "p_first": round(float(res.p_first[i]), 5),
            "mean_pts": round(float(res.mean_points[i]), 1),
            "mean_rank": round(float(res.mean_rank[i]), 1),
            "registered": registered,
        })
    rows.sort(key=lambda r: r["pdga_number"])
    return rows


def _content_hash(rows: list[dict]) -> str:
    h = hashlib.md5()
    for r in rows:
        h.update("|".join(str(r[k]) for k in _PRED_KEYS).encode())
    return h.hexdigest()


def record(res, division: str) -> str:
    """Append a snapshot for this division if today's predictions are new."""
    SNAP_DIR.mkdir(exist_ok=True)
    path = SNAP_DIR / f"history_{division.lower()}.csv"
    today = dt.date.today().isoformat()
    n_completed = sum(1 for r in schedule.load() if r["completed"] and r[division.lower()])
    rows = _rows(res, division, n_completed, today, dt.datetime.now().isoformat(timespec="seconds"))
    cur_hash = _content_hash(rows)

    if path.exists():
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            old_fields = reader.fieldnames or []
            existing = list(reader)
        if old_fields != FIELDS:  # schema grew: rewrite history with new columns blank
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=FIELDS)
                w.writeheader()
                for r in existing:
                    w.writerow({k: r.get(k, "") for k in FIELDS})
        if existing:
            last_date = existing[-1]["snapshot_date"]
            if last_date == today:
                return f"{division}: snapshot already taken today"
            last_block = [r for r in existing if r["snapshot_date"] == last_date]
            if _content_hash(last_block) == cur_hash:
                return f"{division}: predictions unchanged — skipped"

    write_header = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if write_header:
            w.writeheader()
        w.writerows(rows)
    return f"{division}: recorded snapshot ({today}, {len(rows)} players, {n_completed} events in)"
