"""Monte Carlo season simulation.

Carries over the original model's core: a player's expected round score
relative to the field average is -(rating - field_avg) / RATING_PTS_PER_STROKE
strokes, with per-round noise N(0, ROUND_SD). Each sim draws fields for the
remaining events from participation probabilities, ranks the finishers, and
awards 2026 points; banked points from completed events are fixed.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass

import numpy as np

from . import config, fields, points, schedule, standings

RATING_PTS_PER_STROKE = 6.0   # from DGPTModelV2
ROUND_SD = 6.82               # strokes per round, from DGPTModelV2
ROUNDS = {"major": 4}         # default 3 regular rounds otherwise
DEFAULT_SIMS = 10_000

# Powerball Cup qualification (dgpt.com playoff-qualification-update)
STANDINGS_CUT = {"MPO": 28, "FPO": 18}
FIELD_SIZE = {"MPO": 32, "FPO": 20}


MAX_HIST_RANK = 50  # per-position histogram depth for the app


@dataclass
class SimResult:
    division: str
    n_sims: int
    names: list[str]
    pdga_numbers: list[int]
    ratings: list[float]
    current_points: list[float]
    current_rank: list[int]
    mean_points: np.ndarray
    mean_rank: np.ndarray
    p_cut: np.ndarray       # P(final standings rank <= standings cut)
    p_field: np.ndarray     # P(rank <= championship field size) ~ upper bound incl. playoff path
    p_first: np.ndarray
    # extras for the web app / what-if replay
    rank_hist: np.ndarray   # (n_players, MAX_HIST_RANK) counts of final rank
    cutline: np.ndarray     # per sim: points of the last direct-qualification spot
    cutline2: np.ndarray    # per sim: points of the first spot outside the cut
    att_probs: np.ndarray   # (n_events, n_players) baseline P(plays)
    events_meta: list[dict]  # remaining events: id/name/cls/rounds/major + score stats
    banked: list[list]      # per player: [(tid, points, is_major), ...]


def _curve_vector(division: str, cls: str, size: int) -> np.ndarray:
    """Points indexed by place 1..size (0 index unused)."""
    vec = np.zeros(size + 2)
    if cls == "jomez":
        for place in range(1, size + 1):
            vec[place] = points.jomez_bonus(place)
        return vec
    curve = points.event_curve(division, cls)
    for place, val in curve.items():
        if place <= size:
            vec[place] = val
    return vec


def run(division: str, n_sims: int = DEFAULT_SIMS, seed: int | None = 2026,
        chunk: int = 500) -> SimResult:
    rng = np.random.default_rng(seed)
    sched = schedule.load()
    table = standings.compute(division)

    # players: anyone with a start this season and a known rating
    table = [r for r in table if r["rating"]]
    n = len(table)
    pdga_numbers = [r["pdga_number"] for r in table]
    ratings = np.array([float(r["rating"]) for r in table])
    idx = {p: i for i, p in enumerate(pdga_numbers)}

    major_tids = config.MAJOR_TIDS_MPO if division == "MPO" else config.MAJOR_TIDS_FPO
    banked_majors = np.zeros((n, len(major_tids)))
    banked_others: list[list[float]] = [[] for _ in range(n)]
    player_events = {r["pdga_number"]: {tid for tid, *_ in r["events"]} for r in table}
    for i, r in enumerate(table):
        m = 0
        for tid, pts, _, _ in r["events"]:
            if tid in major_tids:
                banked_majors[i, m] = pts
                m += 1
            else:
                banked_others[i].append(pts)
    max_banked = max(len(b) for b in banked_others)
    banked_arr = np.zeros((n, max_banked))
    for i, b in enumerate(banked_others):
        banked_arr[i, : len(b)] = b

    remaining = [
        row for row in sched
        if not row["completed"]
        and row[division.lower()]
        and row["cls"] != "championship"
        and (division == "MPO" or row["fpo_points"])
    ]
    player_names = {r["pdga_number"]: r["name"] for r in table}
    rates = fields.participation_rates(sched, player_events, division, player_names)
    overrides = fields.load_overrides()
    event_probs = []
    for row in remaining:
        probs = fields.play_probabilities(row, division, pdga_numbers, rates, overrides)
        event_probs.append(np.array([probs[p] for p in pdga_numbers]))

    curves = {row["tournament_id"]: _curve_vector(division, row["cls"], n) for row in remaining}

    total_pts = np.zeros((n_sims, n))
    total_rank = np.zeros((n_sims, n), dtype=np.int32)
    rank_hist = np.zeros((n, MAX_HIST_RANK), dtype=np.int64)
    cutline = np.zeros(n_sims)
    cutline2 = np.zeros(n_sims)
    events_meta = [
        {
            "tid": row["tournament_id"], "name": row["name"], "cls": row["cls"],
            "start_date": row["start_date"],
            "rounds": ROUNDS.get(row["cls"], 3),
            "is_major": row["tournament_id"] in major_tids,
            "field_avg_rating": 0.0, "opp_score_sd": 0.0, "field_size": 0.0,
        }
        for row in remaining
    ]

    done = 0
    while done < n_sims:
        c = min(chunk, n_sims - done)
        sim_major = np.zeros((c, n, sum(1 for r in remaining if r["cls"] == "major")))
        sim_other = np.zeros((c, n, sum(1 for r in remaining if r["cls"] != "major")))
        mi = oi = 0
        for ev_i, (row, probs) in enumerate(zip(remaining, event_probs)):
            plays = rng.random((c, n)) < probs  # field draw per sim
            n_rounds = ROUNDS.get(row["cls"], 3)
            # field-average rating per sim (guard empty fields)
            fsum = (plays * ratings).sum(axis=1)
            fcnt = plays.sum(axis=1)
            avg = np.where(fcnt > 0, fsum / np.maximum(fcnt, 1), 1000.0)
            mu = -(ratings[None, :] - avg[:, None]) / RATING_PTS_PER_STROKE * n_rounds
            scores = mu + rng.normal(0.0, ROUND_SD * np.sqrt(n_rounds), (c, n))
            scores[~plays] = np.inf
            if done == 0:  # score-distribution stats for the client-side replay
                played_scores = np.where(plays, scores, np.nan)
                events_meta[ev_i]["field_avg_rating"] = round(float(avg.mean()), 1)
                events_meta[ev_i]["opp_score_sd"] = round(float(np.nanstd(played_scores)), 2)
                events_meta[ev_i]["field_size"] = round(float(fcnt.mean()), 1)
            order = np.argsort(scores, axis=1)
            place = np.empty_like(order)
            rows_ix = np.arange(c)[:, None]
            place[rows_ix, order] = np.arange(1, n + 1)[None, :]
            if row["cls"] == "doubles":
                place = (place + 1) // 2  # individual rank -> implied team place
            pts = curves[row["tournament_id"]][np.minimum(place, n + 1)]
            pts[~plays] = 0.0
            if row["cls"] == "major":
                sim_major[:, :, mi] = pts
                mi += 1
            else:
                sim_other[:, :, oi] = pts
                oi += 1

        # season totals: top-2 majors kept, then best TOP_N overall
        majors_all = np.concatenate(
            [np.broadcast_to(banked_majors, (c, n, banked_majors.shape[1])), sim_major], axis=2
        )
        top2 = -np.sort(-majors_all, axis=2)[:, :, : config.MAJORS_COUNTED]
        pool = np.concatenate(
            [np.broadcast_to(banked_arr, (c, n, max_banked)), sim_other, top2], axis=2
        )
        k = config.TOP_N_FINISHES
        best = -np.sort(-pool, axis=2)[:, :, :k]
        totals = best.sum(axis=2)

        order = np.argsort(-totals, axis=1)
        ranks = np.empty_like(order)
        ranks[np.arange(c)[:, None], order] = np.arange(1, n + 1)[None, :]
        total_pts[done : done + c] = totals
        total_rank[done : done + c] = ranks

        cut_n = STANDINGS_CUT[division]
        sorted_totals = -np.sort(-totals, axis=1)
        cutline[done : done + c] = sorted_totals[:, cut_n - 1]
        cutline2[done : done + c] = sorted_totals[:, cut_n]
        capped = np.minimum(ranks, MAX_HIST_RANK)
        for i in range(n):
            rank_hist[i] += np.bincount(capped[:, i], minlength=MAX_HIST_RANK + 1)[1:]
        done += c

    cut = STANDINGS_CUT[division]
    fsz = FIELD_SIZE[division]
    return SimResult(
        division=division,
        n_sims=n_sims,
        names=[r["name"] for r in table],
        pdga_numbers=pdga_numbers,
        ratings=list(ratings),
        current_points=[r["points"] for r in table],
        current_rank=[r["rank"] for r in table],
        mean_points=total_pts.mean(axis=0),
        mean_rank=total_rank.mean(axis=0),
        p_cut=(total_rank <= cut).mean(axis=0),
        p_field=(total_rank <= fsz).mean(axis=0),
        p_first=(total_rank == 1).mean(axis=0),
        rank_hist=rank_hist,
        cutline=cutline,
        cutline2=cutline2,
        att_probs=np.array(event_probs),
        events_meta=events_meta,
        banked=[
            [(tid, pts, tid in major_tids) for tid, pts, _, _ in r["events"]]
            for r in table
        ],
    )


def write_csv(res: SimResult) -> None:
    out_dir = config.REPO_ROOT / "results" / str(config.SEASON)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"projections_{res.division.lower()}.csv"
    order = np.argsort(-res.p_cut, kind="stable")
    cut = STANDINGS_CUT[res.division]
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "name", "pdga_number", "rating", "current_rank", "current_points",
            "mean_final_points", "mean_final_rank", f"p_top{cut}_standings",
            f"p_top{FIELD_SIZE[res.division]}", "p_no1_seed",
        ])
        for i in order:
            w.writerow([
                res.names[i], res.pdga_numbers[i], res.ratings[i],
                res.current_rank[i], res.current_points[i],
                round(float(res.mean_points[i]), 1), round(float(res.mean_rank[i]), 1),
                round(float(res.p_cut[i]), 4), round(float(res.p_field[i]), 4),
                round(float(res.p_first[i]), 4),
            ])
    print(f"wrote {out}")
