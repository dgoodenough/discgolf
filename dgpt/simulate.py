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

from . import config, fields, live_api, points, schedule, standings

RATING_PTS_PER_STROKE = 6.0   # from DGPTModelV2
ROUND_SD = 6.82               # strokes per round, from DGPTModelV2
ROUNDS = {"major": 4}         # default 3 regular rounds otherwise
DEFAULT_SIMS = 10_000

# Powerball Cup qualification (dgpt.com playoff-qualification-update)
STANDINGS_CUT = {"MPO": 28, "FPO": 18}
FIELD_SIZE = {"MPO": 32, "FPO": 20}


MAX_HIST_RANK = 50   # per-position histogram depth for the app
LIVE_CAP = 130       # place-histogram depth for a live event's projection


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
    p_cut: np.ndarray        # P(final standings rank <= cut) = automatic bid
    p_field: np.ndarray      # P(rank <= championship field size)
    p_first: np.ndarray
    p_gmc: np.ndarray        # P(top gmc_cut in standings before GMC — the points cut)
    p_mvp: np.ndarray        # P(top mvp_cut in standings before MVP — the points cut)
    p_gmc_field: np.ndarray  # P(actually in the GMC field, incl. fill)
    p_mvp_field: np.ndarray  # P(actually in the MVP field, incl. GMC-performance path)
    p_mvp_qual: np.ndarray   # P(earns a championship spot via MVP-performance path)
    p_champ: np.ndarray      # P(in the championship field) = p_cut + p_mvp_qual
    # extras for the web app / what-if replay
    rank_hist: np.ndarray    # (n_players, MAX_HIST_RANK) counts of final rank
    cutline: np.ndarray      # per sim: points of the last direct-qualification spot
    cutline2: np.ndarray     # per sim: points of the first spot outside the cut
    att_probs: np.ndarray    # (n_events, n_players) realized P(plays) incl. playoff gating
    events_meta: list[dict]  # remaining events: id/name/cls/rounds/major + score stats
    banked: list[list]       # per player: [(tid, points, is_major, place), ...]
    live_stats: dict         # {tid: {pdga: {cur, rem, win, mean_place, p10/p50/p90, mean_pts}}}


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

    # add players making their first start at a live event (no standings row yet)
    live_now = schedule.live_events(sched)
    have = {r["pdga_number"] for r in table}
    max_rank = max((r["rank"] for r in table), default=0)
    for ev in live_now:
        field = live_api.live_field(ev["tournament_id"], division)
        for pdga, info in (field or {}).items():
            if pdga in have or not info.get("rating"):
                continue
            have.add(pdga)
            max_rank += 1
            table.append({
                "pdga_number": pdga, "name": info["name"], "rating": info["rating"],
                "rank": max_rank, "points": 0.0, "events": [],
            })

    n = len(table)
    pdga_numbers = [r["pdga_number"] for r in table]
    ratings = np.array([float(r["rating"]) for r in table])
    idx = {p: i for i, p in enumerate(pdga_numbers)}

    major_tids = config.MAJOR_TIDS_MPO if division == "MPO" else config.MAJOR_TIDS_FPO
    # banked points split by counting pool (2026 per-class caps)
    banked_majors = np.zeros((n, len(major_tids)))
    banked_dgpt: list[list[float]] = [[] for _ in range(n)]
    banked_jomez_sum = np.zeros(n)    # Jomez bonus — all count
    banked_playoff_sum = np.zeros(n)  # both playoffs count (0 until they happen)
    player_events = {r["pdga_number"]: {tid for tid, *_ in r["events"]} for r in table}
    for i, r in enumerate(table):
        m = 0
        for tid, pts, _, _ in r["events"]:
            pool = points.event_pool(tid, division)
            if pool == "major":
                banked_majors[i, m] = pts
                m += 1
            elif pool == "jomez":
                banked_jomez_sum[i] += pts
            elif pool == "playoff":
                banked_playoff_sum[i] += pts
            else:  # dgpt / dgpt+ / doubles
                banked_dgpt[i].append(pts)
    max_banked = max((len(b) for b in banked_dgpt), default=1)
    banked_dgpt_arr = np.zeros((n, max_banked))
    for i, b in enumerate(banked_dgpt):
        banked_dgpt_arr[i, : len(b)] = b

    # already-won a DGPT/Major singles event -> guaranteed Cup special invite.
    # Jomez Series and the doubles championship do NOT grant the invite.
    no_invite_tids = config.JOMEZ_TIDS | {config.TID_DOUBLES}
    has_banked_win = np.array([
        any(place == 1 and tid not in no_invite_tids for tid, _, place, _ in r["events"])
        for r in table
    ])

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

    # live events in progress: model each player's current score plus a
    # rating-based projection of the holes they have left, instead of
    # simulating the whole event from scratch
    live_tids = {r["tournament_id"] for r in live_now}
    live_data: dict[int, tuple] = {}
    for ev_i, row in enumerate(remaining):
        if row["tournament_id"] not in live_tids:
            continue
        state = live_api.live_field(row["tournament_id"], division)
        if not state:
            continue
        cur = np.zeros(n)
        rem = np.zeros(n)
        in_field = np.zeros(n, dtype=bool)
        for pdga, info in state.items():
            if pdga in idx:
                j = idx[pdga]
                cur[j], rem[j], in_field[j] = info["cur"], info["rem"], True
        favg = float(ratings[in_field].mean()) if in_field.any() else 1000.0
        live_data[ev_i] = (cur, rem, in_field, favg)

    # playoff events (drawn last, with attendance gated on standings)
    gmc_ei = next((i for i, r in enumerate(remaining) if r["tournament_id"] == config.TID_GMC), None)
    mvp_ei = next((i for i, r in enumerate(remaining) if r["tournament_id"] == config.TID_MVP), None)
    playoff_eis = {gmc_ei, mvp_ei} - {None}
    pre_eis = [i for i in range(len(remaining)) if i not in playoff_eis]
    gmc_cut = config.PLAYOFF_QUAL["gmc"]["cut"][division]
    gmc_fill = config.PLAYOFF_QUAL["gmc"]["fill"][division]
    mvp_cut = config.PLAYOFF_QUAL["mvp"]["cut"][division]
    mvp_perf = config.PLAYOFF_QUAL["mvp"]["perf"][division]

    perf_champ = FIELD_SIZE[division] - STANDINGS_CUT[division]  # MVP-performance spots

    total_pts = np.zeros((n_sims, n))
    total_rank = np.zeros((n_sims, n), dtype=np.int32)
    rank_hist = np.zeros((n, MAX_HIST_RANK), dtype=np.int64)
    live_place_hist = {ev_i: np.zeros((n, LIVE_CAP), dtype=np.int64) for ev_i in live_data}
    att_count = np.zeros((len(remaining), n))  # realized plays per event per player
    cutline = np.zeros(n_sims)
    cutline2 = np.zeros(n_sims)
    p_gmc_hits = np.zeros(n)        # rank before GMC within its points cut
    p_mvp_hits = np.zeros(n)        # rank before MVP within its points cut
    p_gmc_field_hits = np.zeros(n)  # actually in the GMC field (incl. fill)
    p_mvp_field_hits = np.zeros(n)  # actually in the MVP field (incl. GMC-perf path)
    p_mvp_qual_hits = np.zeros(n)  # earns championship via MVP performance
    p_champ_hits = np.zeros(n)     # in the championship field (auto bid or MVP perf)
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
        rows_ix = np.arange(c)[:, None]

        def draw_event(ev_i, plays):
            """Draw one event's points + place (c, n); update place-hist + meta."""
            row = remaining[ev_i]
            if ev_i in live_data:
                # in progress: lock in the score so far, project the holes left
                cur, rem, in_field, favg = live_data[ev_i]
                plays = np.broadcast_to(in_field, (c, n))
                mu = cur[None, :] - (ratings[None, :] - favg) / RATING_PTS_PER_STROKE * rem[None, :]
                sd = ROUND_SD * np.sqrt(np.maximum(rem, 1e-9))
                scores = mu + rng.normal(0.0, 1.0, (c, n)) * sd[None, :]
                scores = np.where(in_field[None, :], scores, np.inf)
            else:
                n_rounds = ROUNDS.get(row["cls"], 3)
                fsum = (plays * ratings).sum(axis=1)
                fcnt = plays.sum(axis=1)
                avg = np.where(fcnt > 0, fsum / np.maximum(fcnt, 1), 1000.0)
                mu = -(ratings[None, :] - avg[:, None]) / RATING_PTS_PER_STROKE * n_rounds
                scores = mu + rng.normal(0.0, ROUND_SD * np.sqrt(n_rounds), (c, n))
                scores[~plays] = np.inf
            if done == 0:
                played = np.where(plays, scores, np.nan)
                events_meta[ev_i]["field_avg_rating"] = round(float((ratings[plays[0]].mean()) if plays[0].any() else 1000.0), 1)
                events_meta[ev_i]["opp_score_sd"] = round(float(np.nanstd(played)), 2)
                events_meta[ev_i]["field_size"] = round(float(plays.sum(axis=1).mean()), 1)
            order = np.argsort(scores, axis=1)
            place = np.empty_like(order)
            place[rows_ix, order] = np.arange(1, n + 1)[None, :]
            if row["cls"] == "doubles":
                place = (place + 1) // 2
            att_count[ev_i] += plays.sum(axis=0)
            if ev_i in live_place_hist:  # projected finish distribution for a live event
                inf = live_data[ev_i][2]
                cp = np.where(inf[None, :], np.minimum(place, LIVE_CAP), 0)
                stride = LIVE_CAP + 1
                flat = (np.arange(n) * stride)[None, :] + cp
                live_place_hist[ev_i] += np.bincount(
                    flat.ravel(), minlength=n * stride
                ).reshape(n, stride)[:, 1:]
            pts = curves[row["tournament_id"]][np.minimum(place, n + 1)]
            pts[~plays] = 0.0
            return pts, place

        def rank_of(totals):
            order = np.argsort(-totals, axis=1)
            r = np.empty_like(order)
            r[rows_ix, order] = np.arange(1, n + 1)[None, :]
            return r

        # -- pre-playoff events, routed to their counting pool --
        sim_major = np.zeros((c, n, sum(1 for i in pre_eis if remaining[i]["cls"] == "major")))
        dgpt_cols, jomez_cols = [], []
        mi = 0
        sim_win = np.zeros((c, n), dtype=bool)  # won a DGPT/Major event this sim
        for ev_i in pre_eis:
            if ev_i in live_data:
                plays = np.broadcast_to(live_data[ev_i][2], (c, n))
            else:
                plays = rng.random((c, n)) < event_probs[ev_i]
            pts, place = draw_event(ev_i, plays)
            cls = remaining[ev_i]["cls"]
            # a singles DGPT/Major win earns the Cup special invite; Jomez and
            # the doubles championship do not
            if cls not in ("jomez", "doubles"):
                sim_win |= (place == 1) & plays
            if cls == "major":
                sim_major[:, :, mi] = pts
                mi += 1
            elif cls == "jomez":
                jomez_cols.append(pts)
            else:  # dgpt / dgpt+ / doubles
                dgpt_cols.append(pts)

        # 2026 per-class caps: best COUNT_DGPT DGPT, best COUNT_MAJOR majors,
        # both playoffs, all Jomez bonus (fixed once pre-events are drawn)
        majors_all = np.concatenate(
            [np.broadcast_to(banked_majors, (c, n, banked_majors.shape[1])), sim_major], axis=2
        )
        best_major = (-np.sort(-majors_all, axis=2)[:, :, : config.COUNT_MAJOR]).sum(axis=2)
        dgpt_all = np.concatenate(
            [np.broadcast_to(banked_dgpt_arr, (c, n, max_banked))] + [x[:, :, None] for x in dgpt_cols], axis=2
        )
        best_dgpt = (-np.sort(-dgpt_all, axis=2)[:, :, : config.COUNT_DGPT]).sum(axis=2)
        jomez_total = banked_jomez_sum[None, :] + sum((x for x in jomez_cols), np.zeros((c, n)))
        base_total = best_dgpt + best_major + jomez_total + banked_playoff_sum[None, :]

        def season_totals(extra):
            # extra = playoff point columns (GMC, then MVP) — both count
            return base_total + sum((x for x in extra), np.zeros((c, n)))

        extra = []  # playoff point columns, added as we go
        # -- Green Mountain: field = top gmc_fill in pre-GMC standings --
        if gmc_ei is not None:
            rank_pre_gmc = rank_of(season_totals(extra))
            p_gmc_hits += (rank_pre_gmc <= gmc_cut).sum(axis=0)
            gmc_plays = rank_pre_gmc <= gmc_fill
            p_gmc_field_hits += gmc_plays.sum(axis=0)
            gmc_pts, gmc_place = draw_event(gmc_ei, gmc_plays)
            sim_win |= (gmc_place == 1) & gmc_plays
            extra.append(gmc_pts)

        # -- MVP Open: top mvp_cut in pre-MVP standings + top GMC performers --
        mvp_plays = None
        mvp_place = None
        if mvp_ei is not None:
            rank_pre_mvp = rank_of(season_totals(extra))
            p_mvp_hits += (rank_pre_mvp <= mvp_cut).sum(axis=0)
            mvp_plays = rank_pre_mvp <= mvp_cut
            if gmc_ei is not None:  # GMC top-perf finishers outside the points cut advance
                elig = gmc_plays & (rank_pre_mvp > mvp_cut)
                gp = np.where(elig, gmc_place, n + 1)
                kth = np.partition(gp, mvp_perf - 1, axis=1)[:, mvp_perf - 1]
                mvp_plays = mvp_plays | ((gp <= kth[:, None]) & elig)
            p_mvp_field_hits += mvp_plays.sum(axis=0)
            mvp_pts, mvp_place = draw_event(mvp_ei, mvp_plays)
            sim_win |= (mvp_place == 1) & mvp_plays
            extra.append(mvp_pts)

        cut_n = STANDINGS_CUT[division]
        totals = season_totals(extra)
        ranks = rank_of(totals)
        total_pts[done : done + c] = totals
        total_rank[done : done + c] = ranks

        # -- Championship field: auto bid + MVP-performance + event-winner invite --
        auto_bid = ranks <= cut_n
        champ_field = auto_bid.copy()
        if mvp_place is not None:
            # top perf_champ MVP finishers outside the standings cut earn a spot
            elig = mvp_plays & ~auto_bid
            mp = np.where(elig, mvp_place, n + 1)
            kth = np.partition(mp, perf_champ - 1, axis=1)[:, perf_champ - 1]
            mvp_qual = (mp <= kth[:, None]) & elig
            p_mvp_qual_hits += mvp_qual.sum(axis=0)
            champ_field |= mvp_qual
        # DGPT/Major winners get a special invite (bottom seed) if not already in
        champ_field |= has_banked_win[None, :] | sim_win
        p_champ_hits += champ_field.sum(axis=0)

        sorted_totals = -np.sort(-totals, axis=1)
        cutline[done : done + c] = sorted_totals[:, cut_n - 1]
        cutline2[done : done + c] = sorted_totals[:, cut_n]
        capped = np.minimum(ranks, MAX_HIST_RANK)
        for i in range(n):
            rank_hist[i] += np.bincount(capped[:, i], minlength=MAX_HIST_RANK + 1)[1:]
        done += c

    p_gmc = p_gmc_hits / n_sims
    p_mvp = p_mvp_hits / n_sims
    p_gmc_field = p_gmc_field_hits / n_sims
    p_mvp_field = p_mvp_field_hits / n_sims
    p_mvp_qual = p_mvp_qual_hits / n_sims
    p_champ = p_champ_hits / n_sims
    att_probs = att_count / n_sims

    # per-player live-event projections (finish + points) from the place hist
    live_stats: dict[int, dict] = {}
    places = np.arange(1, LIVE_CAP + 1)
    for ev_i, hist in live_place_hist.items():
        tid = remaining[ev_i]["tournament_id"]
        cur, rem, in_field, _ = live_data[ev_i]
        cv = curves[tid]
        pts_by_place = np.zeros(LIVE_CAP)
        m = min(LIVE_CAP, len(cv) - 1)
        pts_by_place[:m] = cv[1 : 1 + m]
        v_asc = pts_by_place[::-1]
        per: dict[int, dict] = {}
        for j in range(n):
            if not in_field[j]:
                continue
            w = hist[j]
            tot = int(w.sum())
            if tot == 0:
                continue
            cum = np.cumsum(w[::-1])
            def q(f: float) -> float:
                return float(v_asc[min(int(np.searchsorted(cum, f * tot)), LIVE_CAP - 1)])
            per[pdga_numbers[j]] = {
                "cur": round(float(cur[j]), 1),
                "rem": round(float(rem[j]), 2),
                "win": round(float(w[0] / tot), 4),
                "mean_place": round(float((places * w).sum() / tot), 1),
                "mean_pts": round(float((pts_by_place * w).sum() / tot), 1),
                "p10": q(0.10), "p50": q(0.50), "p90": q(0.90),
            }
        live_stats[tid] = per

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
        p_gmc=p_gmc,
        p_mvp=p_mvp,
        p_gmc_field=p_gmc_field,
        p_mvp_field=p_mvp_field,
        p_mvp_qual=p_mvp_qual,
        p_champ=p_champ,
        rank_hist=rank_hist,
        cutline=cutline,
        cutline2=cutline2,
        att_probs=att_probs,
        events_meta=events_meta,
        banked=[
            [(tid, pts, tid in major_tids, place) for tid, pts, place, _ in r["events"]]
            for r in table
        ],
        live_stats=live_stats,
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
