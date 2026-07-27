"""The flight recorder must archive changed payloads, skip unchanged ones,
prune old snapshots, and never break a fetch when the disk misbehaves."""
from __future__ import annotations

import io
import json
import urllib.request

import pytest

from dgpt import live_api

URL = f"{live_api.BASE}/live_results_fetch_round?TournID=42&Division=MPO&Round=2"


@pytest.fixture
def recorder(tmp_path, monkeypatch):
    monkeypatch.setattr(live_api, "FLIGHT_DIR", tmp_path / "flight")
    monkeypatch.setattr(live_api, "_MIN_INTERVAL", 0.0)
    return tmp_path / "flight"


def _serve(monkeypatch, payload: dict):
    def fake_urlopen(req, timeout=None):
        return io.BytesIO(json.dumps(payload).encode())
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)


def test_records_named_snapshot_on_fetch(recorder, monkeypatch):
    _serve(monkeypatch, {"data": {"scores": [1]}})
    live_api._get(URL)
    snaps = list(recorder.glob("round_42_MPO_2.*.json"))
    assert len(snaps) == 1
    assert json.loads(snaps[0].read_text())["data"]["scores"] == [1]


def test_unchanged_payload_not_duplicated(recorder, monkeypatch):
    _serve(monkeypatch, {"data": {"scores": [1]}})
    live_api._get(URL)
    live_api._memo.clear()
    live_api._get(URL)
    assert len(list(recorder.glob("round_42_MPO_2.*.json"))) == 1


def test_retention_prunes_oldest(recorder, monkeypatch):
    recorder.mkdir(parents=True)
    for i in range(live_api.FLIGHT_KEEP + 5):
        (recorder / f"round_42_MPO_2.2026010100{i:04d}Z.json").write_text("{}")
    _serve(monkeypatch, {"data": {"scores": ["new"]}})
    live_api._get(URL)
    snaps = sorted(recorder.glob("round_42_MPO_2.*.json"))
    assert len(snaps) == live_api.FLIGHT_KEEP
    assert "new" in snaps[-1].read_text()


def test_disable_via_env(recorder, monkeypatch):
    monkeypatch.setenv("DGPT_FLIGHT_OFF", "1")
    _serve(monkeypatch, {"data": {}})
    live_api._get(URL)
    assert not recorder.exists()


def test_recorder_failure_never_breaks_the_fetch(recorder, monkeypatch):
    _serve(monkeypatch, {"data": {"ok": True}})
    # make the flight dir an unwritable *file* so mkdir raises OSError
    recorder.parent.mkdir(parents=True, exist_ok=True)
    recorder.write_text("not a directory")
    assert live_api._get(URL)["data"]["ok"] is True


def test_non_live_api_urls_are_ignored(recorder, monkeypatch):
    _serve(monkeypatch, {"data": {}})
    live_api._get("https://example.com/other?x=1")
    assert not recorder.exists()
