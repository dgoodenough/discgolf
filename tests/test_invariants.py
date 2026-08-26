"""The publish-gate invariant checks must flag exactly the failure signatures
this season actually produced — and stay quiet on clean data."""
from __future__ import annotations

from dgpt import invariants, live_api
from .conftest import event_payload, load_payload, round_payload, row

JOMEZ = 100195


def _serve(fake_api, latest_rows, r1_rows=None, final_round=3, latest=2):
    fake_api.event(1, event_payload("Mini", final_round, [("MPO", latest)]))
    if r1_rows is not None:
        fake_api.round(1, "MPO", 1, round_payload(r1_rows))
    fake_api.round(1, "MPO", latest, round_payload(latest_rows))


def test_clean_live_event_has_no_violations(fake_api):
    _serve(
        fake_api,
        r1_rows=[row(1, "A", 1030, round_to_par=-4, played=18, has_score=True),
                 row(2, "B", 1020, round_to_par=-1, played=18, has_score=True)],
        latest_rows=[row(1, "A", 1030, round_to_par=-2, played=9, running_place=1),
                     row(2, "B", 1020, round_to_par=-1, played=9, running_place=2)],
    )
    assert invariants.check_event(1, "MPO") == []


def test_leaderboard_inversion_is_flagged(fake_api):
    """The Jomez signature: the model's reconstructed order disagrees with the
    sheet's own RunningPlace."""
    _serve(
        fake_api,
        r1_rows=[row(1, "A", 1030, round_to_par=-4, played=18, has_score=True),
                 row(2, "B", 1020, round_to_par=-1, played=18, has_score=True)],
        latest_rows=[row(1, "A", 1030, round_to_par=-2, played=9, running_place=2),
                     row(2, "B", 1020, round_to_par=-1, played=9, running_place=1)],
    )
    violations = invariants.check_event(1, "MPO")
    assert any("inversion" in v for v in violations)


def test_absurd_totals_are_flagged(fake_api):
    """Parsing the wrong field (the ToPar-latch class of bug) shows up as an
    impossible event total."""
    _serve(
        fake_api,
        r1_rows=[row(1, "A", 1030, round_to_par=-60, played=18, has_score=True)],
        latest_rows=[row(1, "A", 1030, played=0, running_place=1)],
    )
    violations = invariants.check_event(1, "MPO")
    assert any("bounds cur" in v for v in violations)


def test_the_to_par_bound_scales_with_the_round_count(fake_api):
    """A 4-round major has a round more to accumulate to-par in, and Worlds
    fields it against qualifiers rated far below a DGPT stop. -50 and +95 are
    real scores there; against a window sized for three rounds they were
    violations, and a false alarm reds the run every six minutes all weekend."""
    for rtp in (-50, 95):
        _serve(
            fake_api,
            r1_rows=[row(1, "A", 1030, round_to_par=rtp, played=18, has_score=True)],
            latest_rows=[row(1, "A", 1030, played=0, running_place=1)],
            final_round=4,
        )
        assert invariants.check_event(1, "MPO") == []
        live_api._memo.clear()

    # still catches the parse errors it exists for, at four rounds too
    _serve(
        fake_api,
        r1_rows=[row(1, "A", 1030, round_to_par=-62, played=18, has_score=True)],
        latest_rows=[row(1, "A", 1030, played=0, running_place=1)],
        final_round=4,
    )
    assert any("bounds cur" in v for v in invariants.check_event(1, "MPO"))


def test_bogus_final_round_inflates_rem_and_is_flagged(fake_api):
    """USWDGC-style FinalRound=12: remaining-rounds math vs a 12-round event
    would inflate projection variance by sqrt(9 rounds) — the bounds check
    must catch it while the event is live."""
    _serve(
        fake_api,
        r1_rows=[row(1, "A", 1030, round_to_par=-4, played=18, has_score=True)],
        latest_rows=[row(1, "A", 1030, round_to_par=-2, played=9, running_place=1)],
        final_round=12,
    )
    violations = invariants.check_event(1, "MPO")
    assert any("bounds rem" in v for v in violations)


def test_real_jomez_final_state_is_clean(fake_api):
    fake_api.event(JOMEZ, load_payload(f"event_{JOMEZ}"))
    for rnd in (1, 2, 3):
        fake_api.round(JOMEZ, "MPO", rnd, load_payload(f"round_{JOMEZ}_MPO_{rnd}"))
    assert invariants.check_event(JOMEZ, "MPO") == []
