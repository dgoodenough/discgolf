"""Emit the JSON bundle the web app (docs/) reads.

One file per division. Contains current standings with event breakdowns,
projection odds + per-position histograms, and everything the client-side
cutline-replay what-if needs: per-sim cutlines, per-event score-distribution
stats, per-place points curves, and each player's banked results.
"""
from __future__ import annotations

import datetime as dt
import json

import numpy as np

from . import config, points, schedule, simulate

DOCS_DATA = config.REPO_ROOT / "docs" / "data"
CUTLINE_SAMPLE = 25_000


def export(res: simulate.SimResult, seed: int = 7) -> None:
    division = res.division
    sched = schedule.load()
    sched_by_tid = {row["tournament_id"]: row for row in sched}
    n = len(res.names)

    # per-place points curves for remaining events (trimmed; index 0 = 1st)
    curves = {}
    for ev in res.events_meta:
        cls = ev["cls"]
        if cls == "jomez":
            vec = [points.jomez_bonus(p) for p in range(1, 151)]
        else:
            # doubles is TEAM-place indexed: the sim runs it at team level and
            # exports field_size as the team count, so client draws index it
            # directly like any other event
            curve = points.event_curve(division, cls)
            vec = [curve.get(p, 0.0) for p in range(1, 151)]
        curves[ev["tid"]] = vec

    rng = np.random.default_rng(seed)
    if len(res.cutline) > CUTLINE_SAMPLE:
        ix = rng.choice(len(res.cutline), CUTLINE_SAMPLE, replace=False)
        cutline, cutline2 = res.cutline[ix], res.cutline2[ix]
    else:
        cutline, cutline2 = res.cutline, res.cutline2

    hist_frac = res.rank_hist / res.n_sims

    players = []
    for i in range(n):
        players.append(
            {
                "name": res.names[i],
                "pdga": res.pdga_numbers[i],
                "rating": res.ratings[i],
                "rank": res.current_rank[i],
                "points": res.current_points[i],
                "banked": [
                    {
                        "tid": tid,
                        "pts": pts,
                        "major": major,
                        "place": place,
                        "cls": sched_by_tid[tid]["cls"] if tid in sched_by_tid else "",
                        "event": sched_by_tid[tid]["name"] if tid in sched_by_tid else str(tid),
                    }
                    for tid, pts, major, place in res.banked[i]
                ],
                # 5 decimals so a true lock (exactly 1.0 / 0 failures) stays
                # distinct from 0.99999 — the app shows "100%" only for the former
                "p_cut": round(float(res.p_cut[i]), 5),
                "p_gmc": round(float(res.p_gmc_field[i]), 5),      # P(in the GMC field)
                "p_mvp": round(float(res.p_mvp_field[i]), 5),      # P(in the MVP field)
                "p_gmc_cut": round(float(res.p_gmc[i]), 5),        # P(makes the points cut)
                "p_mvp_cut": round(float(res.p_mvp[i]), 5),
                "p_mvp_qual": round(float(res.p_mvp_qual[i]), 5),
                "p_champ": round(float(res.p_champ[i]), 5),
                "p_first": round(float(res.p_first[i]), 5),
                "mean_pts": round(float(res.mean_points[i]), 1),
                "mean_rank": round(float(res.mean_rank[i]), 1),
                "hist": [round(float(x), 4) for x in hist_frac[i]],
                # realized attendance per remaining event (playoffs reflect gating)
                "att": [round(float(res.att_probs[e, i]), 3) for e in range(len(res.events_meta))],
                # live-event projections (current position + projected finish), if any
                "live": {
                    tid: stats[res.pdga_numbers[i]]
                    for tid, stats in res.live_stats.items()
                    if res.pdga_numbers[i] in stats
                },
                # doubles championship pairing (None partner = solo, avg-partner model)
                "dbl": res.dbl_info.get(res.pdga_numbers[i]),
            }
        )
    players.sort(key=lambda p: (-p["points"], p["rank"]))

    bundle = {
        "meta": {
            "division": division,
            "season": config.SEASON,
            "generated": dt.datetime.now().isoformat(timespec="seconds"),
            "n_sims": res.n_sims,
            "cut": simulate.STANDINGS_CUT[division],
            "field_size": simulate.FIELD_SIZE[division],
            "max_hist_rank": simulate.MAX_HIST_RANK,
            "top_n_finishes": config.TOP_N_FINISHES,
            "majors_counted": config.MAJORS_COUNTED,
            "count_dgpt": config.COUNT_DGPT,
            "count_playoff": config.COUNT_PLAYOFF,
            "rating_pts_per_stroke": simulate.RATING_PTS_PER_STROKE,
            "round_sd": simulate.ROUND_SD,
            "gmc_tid": config.TID_GMC,
            "mvp_tid": config.TID_MVP,
            "dbl_tid": config.TID_DOUBLES,
            "gmc_cut": config.PLAYOFF_QUAL["gmc"]["cut"][division],
            "mvp_cut": config.PLAYOFF_QUAL["mvp"]["cut"][division],
        },
        "schedule": [
            {
                "tid": row["tournament_id"], "name": row["name"], "cls": row["cls"],
                "start": row["start_date"], "end": row["end_date"],
                "completed": row["completed"],
            }
            for row in sched
            if row[division.lower()] and (division == "MPO" or row["fpo_points"] or row["completed"])
        ],
        "events": [
            {**ev, "curve": curves[ev["tid"]]} for ev in res.events_meta
        ],
        "cutline": [round(float(x), 1) for x in cutline],
        "cutline2": [round(float(x), 1) for x in cutline2],
        "players": players,
    }

    DOCS_DATA.mkdir(parents=True, exist_ok=True)
    out = DOCS_DATA / f"{division.lower()}.json"
    out.write_text(json.dumps(bundle, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size // 1024} KB)")
