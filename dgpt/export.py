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

from . import config, fields, points, schedule, simulate

DOCS_DATA = config.REPO_ROOT / "docs" / "data"
CUTLINE_SAMPLE = 25_000
# Places covered by each event's exported points curve. The client what-if
# reads a drawn place straight off it and scores anything past the end as
# zero, so this has to outrun the deepest field on the calendar — Pro Worlds
# runs ~200 — and it carries the curve's floor past place 144 for the same
# reason simulate._curve_vector does.
CURVE_DEPTH = 250


def curve_vector(division: str, cls: str) -> list[float]:
    """Points by place, 1..CURVE_DEPTH, for the client-side what-if.

    Doubles is TEAM-place indexed: the sim runs it at team level and exports
    field_size as the team count, so client draws index it directly like any
    other event.

    Past the published table's last place the floor keeps being paid, matching
    points.assign_points and simulate._curve_vector — all three have to agree
    or a deep finish is worth one number in the forecast and another in the
    standings.
    """
    if cls == "jomez":
        return [points.jomez_bonus(p) for p in range(1, CURVE_DEPTH + 1)]
    curve = points.event_curve(division, cls)
    deepest = max(curve)
    return [curve.get(p, curve[deepest] if p > deepest else 0.0)
            for p in range(1, CURVE_DEPTH + 1)]


def export(res: simulate.SimResult, seed: int = 7) -> None:
    division = res.division
    sched = schedule.load()
    sched_by_tid = {row["tournament_id"]: row for row in sched}
    live_tids = {row["tournament_id"] for row in schedule.live_events(sched)}
    n = len(res.names)

    # per-place points curves for remaining events (index 0 = 1st)
    curves = {ev["tid"]: curve_vector(division, ev["cls"]) for ev in res.events_meta}

    rng = np.random.default_rng(seed)
    if len(res.cutline) > CUTLINE_SAMPLE:
        ix = rng.choice(len(res.cutline), CUTLINE_SAMPLE, replace=False)
        cutline, cutline2 = res.cutline[ix], res.cutline2[ix]
    else:
        cutline, cutline2 = res.cutline, res.cutline2

    hist_frac = res.rank_hist / res.n_sims
    countries = fields.load_countries()  # pdga -> ISO-3166 alpha-2 (blank if unknown)

    players = []
    for i in range(n):
        players.append(
            {
                "name": res.names[i],
                "pdga": res.pdga_numbers[i],
                "country": countries.get(res.pdga_numbers[i], ""),
                "rating": res.ratings[i],
                "rank": res.current_rank[i],
                "points": res.current_points[i],
                "banked": [
                    {
                        "tid": tid,
                        "pts": pts,
                        "major": major,
                        "place": place,
                        "p_drop": p_drop,
                        "cls": sched_by_tid[tid]["cls"] if tid in sched_by_tid else "",
                        "event": sched_by_tid[tid]["name"] if tid in sched_by_tid else str(tid),
                    }
                    for tid, pts, major, place, p_drop in res.banked[i]
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
                # 1 = on the published playoff signup list, so in that field
                # whatever the standings do. 0 covers both "not signed up" and
                # "no list published yet"; meta.gmc_signups says which.
                "reg_gmc": int(bool(res.reg_gmc[i])),
                "reg_mvp": int(bool(res.reg_mvp[i])),
                # P(plays their way into Worlds through the play-in). Present
                # only for entrants — everyone else is a flat 0.
                "p_playin": round(float(res.p_playin[i]), 5),
                "p_first": round(float(res.p_first[i]), 5),
                "mean_pts": round(float(res.mean_points[i]), 1),
                "mean_rank": round(float(res.mean_rank[i]), 1),
                # projected remaining starts + expected banked points that get dropped
                "exp_starts": round(float(res.att_probs[:, i].sum()), 1),
                "proj_dropped": round(sum(pts * pd for _, pts, _, _, pd in res.banked[i]), 1),
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
            # Written only after a refresh completed, so this is the age of the
            # numbers themselves — the site reports it as "last update". Stamped
            # UTC-aware: it used to be a naive now(), which is UTC on the runner
            # but which a browser reads as the VIEWER's local time, shifting the
            # reported age by their offset.
            "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "n_sims": res.n_sims,
            "cut": simulate.STANDINGS_CUT[division],
            "field_size": simulate.FIELD_SIZE[division],
            "max_hist_rank": simulate.MAX_HIST_RANK,
            "top_n_finishes": config.TOP_N_FINISHES,
            "majors_counted": config.MAJORS_COUNTED,
            "count_dgpt": config.COUNT_DGPT,
            "count_playoff": config.COUNT_PLAYOFF,
            "rating_pts_per_stroke": simulate.RATING_PTS_PER_STROKE[division],
            "round_sd": simulate.ROUND_SD,
            "gmc_tid": config.TID_GMC,
            "mvp_tid": config.TID_MVP,
            "dbl_tid": config.TID_DOUBLES,
            "gmc_cut": config.PLAYOFF_QUAL["gmc"]["cut"][division],
            "gmc_fill": config.PLAYOFF_QUAL["gmc"]["fill"][division],
            "mvp_cut": config.PLAYOFF_QUAL["mvp"]["cut"][division],
            "mvp_perf": config.PLAYOFF_QUAL["mvp"]["perf"][division],
            # How many players are on each published playoff signup list. 0
            # means no list yet, which is what the page says instead of
            # implying nobody entered.
            "gmc_signups": int(res.reg_gmc.sum()),
            "mvp_signups": int(res.reg_mvp.sum()),
            # true once the list is the field rather than a floor: nobody else
            # gets in on points any more
            "gmc_field_set": bool(res.gmc_final),
            "mvp_field_set": bool(res.mvp_final),
            "reg_phases": {
                key: [
                    {"opens": ph["opens"], "label": ph["label"],
                     **({"top": ph["top"][division]} if "top" in ph else {}),
                     **({"min_rating": ph["min_rating"][division]} if "min_rating" in ph else {})}
                    for ph in phases
                ]
                for key, phases in config.REG_PHASES.items()
            },
            # Pro Worlds play-in: a one-round qualifier for the spots Worlds
            # left open. Awards no points itself, so it has no schedule row —
            # players who can win through carry a p_playin instead.
            "worlds_tid": config.TID_WORLDS,
            "playin_tid": config.TID_WORLDS_PLAYIN,
            "playin_spots": config.WORLDS_PLAYIN_SPOTS[division],
            "playin_entrants": int((res.p_playin > 0).sum()),
        },
        # `live` is schedule.live_events()' answer, shipped rather than
        # re-derived. It is the single definition of "in progress" for the
        # whole system: the front end used to compare start/end against a UTC
        # date, which has no grace day, so a US Sunday final round read as over
        # from midnight UTC while the pipeline was still tracking it. That was
        # the third copy of this window (schedule, livecheck, the page) and the
        # third place it was wrong.
        "schedule": [
            {
                "tid": row["tournament_id"], "name": row["name"], "cls": row["cls"],
                "start": row["start_date"], "end": row["end_date"],
                "completed": row["completed"],
                "live": row["tournament_id"] in live_tids,
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
