"""The Powerball Cup seed ladder, the distribution built from it, and the
championship itself.

The ladder is the DGPT's published seed table (config.CUP_START_STROKES). The
simulation turns it into a per-player distribution over the score each player
would tee off the Cup on — the one output that depends on *where* in the field
a player lands rather than merely whether they make it.
"""
from __future__ import annotations

import numpy as np
import pytest

from dgpt import config, simulate, standings

N_SIMS = 400


@pytest.mark.parametrize("division", ["MPO", "FPO"])
def test_ladder_covers_exactly_the_standings_cut(division):
    """Bands run to the auto-bid line and no further.

    Past it a player is a bottom seed at even — that is where the wildcards and
    the winner's invites sit — so the ladder has nothing left to say and the
    simulation clamps to its last entry instead of extending it.
    """
    ladder = config.cup_start_strokes(division)
    assert len(ladder) == simulate.STANDINGS_CUT[division]
    assert ladder[-1] == 0, "the last band starts at even"
    assert ladder == sorted(ladder), "the advantage never grows as the seed drops"


@pytest.mark.parametrize("division, expected", [
    ("MPO", {1: -7, 2: -6, 3: -5, 4: -5, 5: -4, 8: -4, 9: -3, 12: -3,
             13: -2, 16: -2, 17: -1, 24: -1, 25: 0, 28: 0}),
    ("FPO", {1: -6, 2: -5, 3: -4, 4: -4, 5: -3, 8: -3, 9: -2, 12: -2,
             13: -1, 16: -1, 17: 0, 18: 0}),
])
def test_ladder_matches_the_published_table(division, expected):
    """Both edges of every band, read off the DGPT's own seed graphic."""
    ladder = config.cup_start_strokes(division)
    assert {rank: ladder[rank - 1] for rank in expected} == expected


def test_buckets_are_the_distinct_scores_best_first():
    values, bucket = simulate._stroke_ladder("MPO")
    assert values == (-7, -6, -5, -4, -3, -2, -1, 0)
    assert bucket[0] == 0, "the No. 1 seed lands in the best bucket"
    assert bucket[-1] == len(values) - 1, "the last auto-bid spot starts at even"
    assert len(bucket) == simulate.STANDINGS_CUT["MPO"]


@pytest.fixture
def res(tiny_world):
    standings.write_csv("MPO", standings.compute("MPO"))
    return tiny_world, simulate.run("MPO", n_sims=N_SIMS, chunk=200)


def test_every_player_starts_somewhere_or_misses(res):
    """Each row is a partition of the simulated seasons, so it sums to n_sims.

    The trailing bucket is "not in the field at all", which is what makes the
    column readable for a bubble player: the same picture carries how good a
    start they get and how often they get none.
    """
    _, r = res
    assert r.strokes_hist.shape == (len(r.names), len(r.stroke_values) + 1)
    assert np.all(r.strokes_hist.sum(axis=1) == N_SIMS)
    assert np.all(r.strokes_hist >= 0)


def test_the_field_bucket_agrees_with_the_cup_odds(res):
    """P(starts anywhere) is P(in the Cup), computed two independent ways."""
    _, r = res
    in_field = r.strokes_hist[:, :-1].sum(axis=1) / N_SIMS
    assert in_field == pytest.approx(r.p_champ, abs=1e-12)


def test_an_event_winner_always_gets_a_start(res):
    """The star banked a win, so the special invite guarantees a tee time.

    They are never in the miss bucket even in seasons where the standings
    abandon them — and if the standings do abandon them they are a bottom seed,
    so the row has to reach the even bucket too.
    """
    world, r = res
    ix = r.pdga_numbers.index(world.star)
    assert r.strokes_hist[ix, -1] == 0
    assert r.strokes_hist[ix, :-1].sum() == N_SIMS


def test_the_points_leader_starts_further_under_par_than_the_tail(res):
    """Mean starting score has to track the standings, or the ladder is upside down."""
    _, r = res
    values = np.array(r.stroke_values, dtype=float)

    def mean_start(i):
        counts = r.strokes_hist[i, :-1]
        return float((values * counts).sum() / max(counts.sum(), 1))

    order = np.argsort(-np.array(r.current_points))
    assert mean_start(order[0]) < mean_start(order[-1])


# ------------------------------------------------- the championship itself

def test_exactly_one_player_wins_each_simulated_cup(res):
    """p_cup_win is a race, not a set of independent chances.

    Every simulated season crowns exactly one champion, so the column sums to
    1.0 across the whole roster — the property that separates it from p_champ,
    where 32 players can all be at 100%.
    """
    _, r = res
    assert r.p_cup_win.sum() == pytest.approx(1.0)
    assert np.all(r.p_cup_win >= 0.0)


def test_you_cannot_win_a_cup_you_are_not_in(res):
    """Win odds are bounded by the odds of being there at all."""
    _, r = res
    assert np.all(r.p_cup_win <= r.p_champ + 1e-12)


def test_the_seed_advantage_actually_moves_the_result(tiny_world, monkeypatch):
    """A flat ladder and a steep one must not produce the same champion odds.

    This is the whole reason the Cup is simulated rather than assumed: the
    strokes are real strokes, so handing the top seed a bigger head start has
    to raise their win odds. Run the same world twice, changing only the
    ladder.
    """
    standings.write_csv("MPO", standings.compute("MPO"))
    top = simulate.run("MPO", n_sims=N_SIMS, chunk=200).pdga_numbers

    def win_odds_with(bands):
        monkeypatch.setattr(config, "CUP_START_STROKES", {**config.CUP_START_STROKES, "MPO": bands})
        r = simulate.run("MPO", n_sims=N_SIMS, chunk=200)
        return r.p_cup_win[r.pdga_numbers.index(top[0])]

    flat = win_odds_with([(28, 0)])                      # nobody gets a head start
    steep = win_odds_with([(1, -12), (28, 0)])           # the No. 1 seed gets a big one
    assert steep > flat


def test_an_overwhelming_head_start_collapses_onto_the_top_seed(tiny_world, monkeypatch):
    """Give the No. 1 seed 30 strokes and the Cup stops being a tournament.

    The advantage follows the SEED, not a player — whoever tops the standings
    in a given season carries it — so the champion becomes whoever wins the
    points race, and p_cup_win has to converge on p_first. That it does is the
    end-to-end check that each simulated season's strokes are applied to that
    season's own finishing order rather than to today's table.
    """
    monkeypatch.setattr(config, "CUP_START_STROKES",
                        {**config.CUP_START_STROKES, "MPO": [(1, -30), (28, 0)]})
    standings.write_csv("MPO", standings.compute("MPO"))
    r = simulate.run("MPO", n_sims=N_SIMS, chunk=200)
    assert r.p_cup_win.sum() == pytest.approx(1.0)
    assert np.abs(r.p_cup_win - r.p_first).max() < 0.05
