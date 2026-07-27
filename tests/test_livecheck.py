"""livecheck is the live loop's gate: its signature must move when a live
score moves and hold still when nothing changed — a wrong 'unchanged' here is
exactly the silent-stale failure mode.

Each production check runs in a fresh process, so the per-process fetch memo
is cleared between signature() calls to match.
"""
from __future__ import annotations

from dgpt import live_api, livecheck


def test_signature_stable_when_nothing_changes(tiny_world):
    before = livecheck.signature()
    live_api._memo.clear()
    assert livecheck.signature() == before


def test_signature_moves_when_a_live_score_moves(tiny_world, fake_api):
    before = livecheck.signature()
    url = next(u for u in fake_api.envelopes
               if f"TournID={tiny_world.live_tid}" in u and "Round=2" in u)
    sheet = fake_api.envelopes[url]["data"]["scores"]
    scored = next(s for s in sheet if s["RoundtoPar"] is not None)
    scored["RoundtoPar"] -= 1
    live_api._memo.clear()
    assert livecheck.signature() != before


def test_signature_ignores_non_live_noise(tiny_world, fake_api):
    """A completed event's sheets aren't refetched — editing them must not
    perturb the signature (the gate would otherwise re-simulate constantly)."""
    before = livecheck.signature()
    url = next(u for u in fake_api.envelopes
               if f"TournID={tiny_world.completed_tid}" in u and "Round=3" in u)
    fake_api.envelopes[url]["data"]["scores"][0]["RoundtoPar"] = -99
    live_api._memo.clear()
    assert livecheck.signature() == before
