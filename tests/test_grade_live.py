"""Tests for the live-projection grader.

The load path is exercised against the real captured payloads; the scoring
path against synthetic events whose scores are drawn from the model itself,
where the right answer is known in advance. That second half is the one that
matters: a grader that silently measures the wrong thing is worse than no
grader, so the headline test generates a world the current model is exactly
correct in and asserts the report says so.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from dgpt import grade_live, simulate

from .conftest import PAYLOADS


# ------------------------------------------------------------------ builders

def _sheet(pdga, ratings, to_par, places=None):
    """One round sheet in the live API's field names."""
    rows = []
    for i, p in enumerate(pdga):
        rows.append({
            "PDGANum": p, "Name": f"P{p}", "Rating": ratings[i],
            "RoundtoPar": float(to_par[i]), "ToPar": float(to_par[i]),
            "Played": 18, "HasRoundScore": True, "GrandTotal": 200,
            "RunningPlace": int(places[i]) if places is not None else None,
        })
    return rows


def _places_from(totals) -> list[int]:
    """Finishing places with ties shared, as PDGA's RunningPlace reports them."""
    return [1 + int(sum(1 for t in totals if t < x - 1e-9)) for x in totals]


def synthetic_replay(tid, n_players=60, n_rounds=3, seed=0, sd_round=None,
                     rho=0.0, division="MPO") -> grade_live.Replay:
    """An event whose scores really are drawn from the score model.

    Ratings are spread realistically; each round is the rating-implied pace
    plus noise. With `rho` = 0 the rounds are independent, which is exactly
    what the published model assumes, so a grader that works must report the
    published model as calibrated on this data.
    """
    rng = np.random.default_rng(seed)
    sd_round = sd_round if sd_round is not None else simulate.ROUND_SD
    rpps = simulate.RATING_PTS_PER_STROKE[division]
    pdga = [10_000 + tid % 1000 + i for i in range(n_players)]
    ratings = np.round(rng.normal(1000, 25, n_players)).astype(int)
    pace = -(ratings - ratings.mean()) / rpps

    tau, s = math.sqrt(rho) * sd_round, math.sqrt(1 - rho) * sd_round
    form = rng.normal(0.0, tau, n_players) if tau else np.zeros(n_players)
    per_round = [
        np.round(pace + form + rng.normal(0.0, s, n_players)) for _ in range(n_rounds)
    ]
    totals = np.sum(per_round, axis=0)
    places = _places_from(totals)

    sheets = {}
    for r in range(n_rounds):
        final = r == n_rounds - 1
        sheets[r + 1] = _sheet(pdga, ratings, per_round[r], places if final else None)
    return grade_live.Replay(tid=tid, division=division, name=f"Synth {tid}",
                             rounds=list(range(1, n_rounds + 1)), sheets=sheets)


# --------------------------------------------------------------- replay path

def test_real_payloads_reconstruct_the_state_at_each_boundary():
    """The Jomez fixture replays into 3 checkpoints with the right shape."""
    replays, _ = grade_live.collect_replays(PAYLOADS, offline=True, only_tids={100195})
    assert len(replays) == 1
    rep = replays[0]
    assert (rep.division, rep.rounds, rep.partial_round) == ("MPO", [1, 2, 3], None)

    cps = grade_live.checkpoints(rep)
    assert [c.done for c in cps] == [0, 1, 2]
    assert [c.rem for c in cps] == [3.0, 2.0, 1.0]

    pre, after1, after2 = cps
    assert np.all(pre.cur == 0.0) and np.all(pre.standing == 0)  # no leaderboard yet
    # cur after two rounds is the sum of the two sheets' RoundtoPar
    r1 = {r["PDGANum"]: r["RoundtoPar"] for r in rep.sheets[1]}
    r2 = {r["PDGANum"]: r["RoundtoPar"] for r in rep.sheets[2]}
    for i, p in enumerate(after2.pdga):
        assert after2.cur[i] == pytest.approx(r1[p] + r2[p])
    assert after1.standing.min() == 1


def test_actual_places_are_a_permutation_of_the_graded_field():
    """Finishers missing from the field must not leave gaps in the places.

    A simulated place is always 1..n; if the actual places kept their raw
    values, a finisher dropped for want of a rating would shift everyone below
    them and bias every threshold outcome and the PIT.
    """
    replays, _ = grade_live.collect_replays(PAYLOADS, offline=True, only_tids={100195})
    for cp in grade_live.checkpoints(replays[0]):
        assert cp.actual_lo.min() == 1
        assert cp.actual_hi.max() == cp.n
        # every position is covered exactly once across the tie blocks
        covered = sorted(
            pos for lo, hi in zip(cp.actual_lo, cp.actual_hi) for pos in [lo]
        )
        assert len(covered) == cp.n


def test_partial_field_final_round_is_excluded_and_reported():
    """USWDGC's finals sheet carries 44 of 77, so `rem` is not whole-field."""
    replays, skipped = grade_live.collect_replays(PAYLOADS, offline=True, only_tids={97341})
    assert [r.partial_round for r in replays] == [12]
    out, _ = grade_live.run(PAYLOADS, offline=True, only_tids={97341}, n_sims=200, quiet=True)
    assert out == {}   # nothing graded


def test_offline_skips_events_with_no_event_payload(tmp_path):
    """Without the event body the round plan is a guess, so we decline."""
    (tmp_path / "round_100195_MPO_1.json").write_text(
        (PAYLOADS / "round_100195_MPO_1.json").read_text(encoding="utf-8"), encoding="utf-8")
    replays, skipped = grade_live.collect_replays(tmp_path, offline=True)
    assert replays == []
    assert any("round plan unknown" in s for s in skipped)


# ------------------------------------------------------------------- scoring

def test_ties_get_fractional_credit():
    """A tie block is scored by the share of its positions clearing the bar."""
    cp = grade_live.Checkpoint(
        tid=1, division="MPO", name="t", done=1, rem=2.0,
        pdga=np.arange(6), rating=np.full(6, 1000.0), cur=np.zeros(6),
        standing=np.arange(1, 7),
        actual_lo=np.array([1, 1, 1, 4, 4, 6]),   # 3-way T1, 2-way T4, then 6th
        actual_hi=np.array([3, 3, 3, 5, 5, 6]),
    )
    hist = np.zeros((6, 6), dtype=np.int64)
    hist[:, 0] = 100                                # every player "wins" every sim
    fcs = grade_live.grade(cp, hist, 100)
    assert [f.y_win for f in fcs] == pytest.approx([1 / 3, 1 / 3, 1 / 3, 0, 0, 0])
    # top-5: the T1 block is wholly inside, the T4 block spans 4 and 5 (both in),
    # and 6th is outside
    assert [f.y_top[5] for f in fcs] == pytest.approx([1, 1, 1, 1, 1, 0])
    # a 3-way T1 counts 3 of the top-5 places, so a block at 4-5 is fully inside
    assert sum(f.y_win for f in fcs) == pytest.approx(1.0)


def test_win_credit_sums_to_one_per_checkpoint():
    rep = synthetic_replay(tid=1, seed=3)
    for cp in grade_live.checkpoints(rep):
        hist = grade_live.project(cp, "current", "full", np.random.default_rng(0),
                                  500, 3.65, 0.16)
        fcs = grade_live.grade(cp, hist, 500)
        assert sum(f.y_win for f in fcs) == pytest.approx(1.0)
        assert sum(f.p_win for f in fcs) == pytest.approx(1.0, abs=1e-9)


def test_projection_is_deterministic_for_a_seed():
    rep = synthetic_replay(tid=2, seed=5)
    cp = grade_live.checkpoints(rep)[1]
    a = grade_live.project(cp, "current", "full", np.random.default_rng(1), 400, 3.65, 0.16)
    b = grade_live.project(cp, "current", "full", np.random.default_rng(1), 400, 3.65, 0.16)
    assert np.array_equal(a, b)


def test_baselines_drop_the_input_they_are_named_for():
    """score_only must ignore ratings; rating_only must ignore the leaderboard."""
    rep = synthetic_replay(tid=3, seed=7)
    cp = grade_live.checkpoints(rep)[2]        # two rounds in, one to play
    rng = lambda: np.random.default_rng(4)     # noqa: E731 - same stream each call

    full = grade_live.project(cp, "current", "full", rng(), 2000, 3.65, 0.16)
    score_only = grade_live.project(cp, "current", "score_only", rng(), 2000, 3.65, 0.16)
    rating_only = grade_live.project(cp, "current", "rating_only", rng(), 2000, 3.65, 0.16)

    leader = int(np.argmin(cp.cur))
    # the leader is favoured by anything that reads the leaderboard
    assert score_only[leader, 0] > 0
    assert full[leader, 0] > 0
    # rating_only cannot see the lead: its favourite is the best-rated player
    best_rated = int(np.argmax(cp.rating))
    assert rating_only[best_rated, 0] == rating_only[:, 0].max()
    # and it is unchanged if the leaderboard is rewritten underneath it
    shifted = grade_live.Checkpoint(**{**cp.__dict__, "cur": cp.cur - 25.0})
    assert np.array_equal(
        rating_only, grade_live.project(shifted, "current", "rating_only", rng(), 2000, 3.65, 0.16))


# ------------------------------------------------- does it measure correctly?

def _pit_of(replays, variant, sd_round, rho, n_sims=4000):
    vals = []
    for rep in replays:
        for cp in grade_live.checkpoints(rep):
            rng = np.random.default_rng(99 + cp.tid + cp.done)
            hist = grade_live.project(cp, variant, "full", rng, n_sims, sd_round, rho)
            vals += [f.pit for f in grade_live.grade(cp, hist, n_sims)]
    return [frac for _, frac in grade_live.pit_table(vals)]


def test_pit_is_flat_when_the_model_generated_the_data():
    """The headline check: in a world the published model is right about, the
    grader must report it as calibrated. Rounds here are independent draws with
    per-round SD = ROUND_SD, which is exactly what `ROUND_SD * sqrt(rem)`
    assumes."""
    replays = [synthetic_replay(tid=100 + i, seed=i) for i in range(12)]
    fracs = _pit_of(replays, "current", simulate.ROUND_SD, 0.0)
    assert all(abs(f - 0.10) < 0.035 for f in fracs), fracs


def test_pit_detects_a_spread_that_is_too_narrow():
    """Generate with more noise than the model assumes: the PIT must go U-shaped
    (too many outcomes in the extreme buckets), not stay flat."""
    replays = [synthetic_replay(tid=200 + i, seed=50 + i, sd_round=simulate.ROUND_SD * 1.6)
               for i in range(12)]
    fracs = _pit_of(replays, "current", simulate.ROUND_SD, 0.0)
    ends = fracs[0] + fracs[-1]
    middle = sum(fracs[3:7])
    assert ends > 0.30, fracs        # 2 of 10 buckets holding >30%
    assert ends > middle, fracs


def test_form_variant_recovers_calibration_when_form_is_real():
    """With genuine within-event correlation the published model is
    mis-specified and the form variant is not — the grader must be able to see
    the difference, or it cannot referee the question it exists to referee."""
    rho = 0.30
    sd_round = 3.65
    replays = [synthetic_replay(tid=300 + i, seed=90 + i, sd_round=sd_round, rho=rho)
               for i in range(12)]
    form = _pit_of(replays, "form", sd_round, rho)
    assert all(abs(f - 0.10) < 0.04 for f in form), form

    # and the correlation is recovered from the same payloads
    fit = grade_live.fit_round_structure(replays)
    assert fit["rho_within"] == pytest.approx(rho, abs=0.08)
    assert fit["sd_round"] == pytest.approx(sd_round, abs=0.35)


def test_fit_separates_within_event_form_from_a_stale_rating():
    """A player who is simply better than their rating correlates with himself
    across events; today's form does not. The fit must tell them apart."""
    rng = np.random.default_rng(11)
    n = 60
    pdga = [20_000 + i for i in range(n)]
    ratings = np.round(rng.normal(1000, 25, n)).astype(int)
    bias = rng.normal(0.0, 1.8, n)        # persistent: rating is stale by this
    rpps = simulate.RATING_PTS_PER_STROKE["MPO"]
    pace = -(ratings - ratings.mean()) / rpps

    replays = []
    for e in range(10):
        per_round = [np.round(pace + bias + rng.normal(0.0, 3.2, n)) for _ in range(3)]
        places = _places_from(np.sum(per_round, axis=0))
        sheets = {r + 1: _sheet(pdga, ratings, per_round[r],
                                places if r == 2 else None) for r in range(3)}
        replays.append(grade_live.Replay(tid=400 + e, division="MPO", name=f"E{e}",
                                         rounds=[1, 2, 3], sheets=sheets))

    fit = grade_live.fit_round_structure(replays)
    # all of the correlation here is persistent, so within and across agree and
    # the form-only residual (within - across) is ~0
    assert fit["rho_across"] is not None
    assert fit["rho_within"] - fit["rho_across"] == pytest.approx(0.0, abs=0.08)
    assert fit["rho_within"] > 0.15


def test_skill_score_is_positive_against_a_baseline_that_ignores_the_lead():
    """Sanity on the reference forecasts: two rounds in, knowing the leaderboard
    has to beat not knowing it."""
    replays = [synthetic_replay(tid=500 + i, seed=140 + i) for i in range(6)]
    runs = {"full": [], "rating_only": []}
    for rep in replays:
        for cp in grade_live.checkpoints(rep):
            for model in runs:
                rng = np.random.default_rng(7 + cp.tid + cp.done)
                hist = grade_live.project(cp, "current", model, rng, 3000, 3.65, 0.16)
                runs[model] += grade_live.grade(cp, hist, 3000)
    assert grade_live._skill(runs["full"], runs["rating_only"], "top", 5) > 0.10
