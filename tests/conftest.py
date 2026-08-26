"""Shared test infrastructure: hermetic network guard, a fake live-API layer,
payload builders, and a small self-consistent season ("tiny world") that the
end-to-end smoke test runs the real pipeline against.

Nothing here touches the network or the repo's real data/docs outputs: every
module-level path constant is repointed at tmp_path, and urllib is patched to
fail loudly if anything tries a real request.
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from dgpt import config, export, fields, invariants, live_api, movers, points, ratings, schedule, snapshot

PAYLOADS = Path(__file__).parent / "fixtures" / "payloads"


def load_payload(name: str) -> dict:
    """A captured fixture's `data` body (see tests/fixtures/capture.py)."""
    return json.loads((PAYLOADS / f"{name}.json").read_text(encoding="utf-8"))["data"]


# ---------------------------------------------------------------- isolation

@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """Per-test reset of process-level caches, a hard network guard, and
    disk-cache paths pointed away from the repo's real data/cache."""
    live_api._memo.clear()
    live_api._page_memo.clear()
    points.refresh_classes()
    monkeypatch.setattr(ratings, "_memo", None)
    # The last-known-good roster is real committed data; point it at an absent
    # path so a test only sees one if it writes one (see known_fields).
    monkeypatch.setattr(live_api, "_known_memo", None)
    monkeypatch.setattr(live_api, "KNOWN_FIELDS", tmp_path / "_absent" / "known_fields.json")
    # The one fetch that isn't the live API: PDGA's public event page, which
    # is where playoff/play-in signups come from. Default is "no page", so a
    # test only sees signups if it asks for them (see fake_pages).
    monkeypatch.setattr(live_api, "_fetch_page", lambda url: "")
    monkeypatch.setattr(live_api, "LIVE_CACHE", tmp_path / "_cache" / "live")
    monkeypatch.setattr(live_api, "RESULTS_CACHE", tmp_path / "_cache" / "results")
    monkeypatch.setattr(live_api, "FLIGHT_DIR", tmp_path / "_cache" / "flight")

    def _no_network(*args, **kwargs):
        raise AssertionError("network access attempted from a test")

    monkeypatch.setattr(urllib.request, "urlopen", _no_network)
    yield
    live_api._memo.clear()
    live_api._page_memo.clear()
    live_api._known_memo = None
    points.refresh_classes()


# ------------------------------------------------------------- fake live API

class FakeLiveAPI:
    """Serves live_api._get from registered payloads; 404s everything else."""

    def __init__(self):
        self.envelopes: dict[str, dict] = {}
        self.calls: list[str] = []

    def event(self, tid: int, data: dict) -> None:
        self.envelopes[f"{live_api.BASE}/live_results_fetch_event?TournID={tid}"] = {"data": data}

    def round(self, tid: int, division: str, rnd: int, data: dict) -> None:
        url = f"{live_api.BASE}/live_results_fetch_round?TournID={tid}&Division={division}&Round={rnd}"
        self.envelopes[url] = {"data": data}

    def get(self, url: str, cache_file=None):
        self.calls.append(url)
        if url not in self.envelopes:
            raise urllib.error.HTTPError(url, 404, "Not Found", None, None)
        return copy.deepcopy(self.envelopes[url])


@pytest.fixture
def fake_api(monkeypatch) -> FakeLiveAPI:
    api = FakeLiveAPI()
    monkeypatch.setattr(live_api, "_get", api.get)
    return api


class FakePages:
    """Serves live_api._fetch_page; every other URL comes back empty."""

    def __init__(self):
        self.pages: dict[str, str] = {}

    def signups(self, tid: int, pdga_numbers) -> None:
        """A registration page listing `pdga_numbers`, in PDGA's link shape."""
        links = "".join(f'<a href="/player/{p}">P{p}</a>' for p in pdga_numbers)
        self.pages[live_api.EVENT_PAGE.format(tid=tid)] = (
            f'<h4>Registered Players</h4><div id="registered-players">{links}</div>'
        )

    def raw(self, tid: int, html: str) -> None:
        self.pages[live_api.EVENT_PAGE.format(tid=tid)] = html

    def fetch(self, url: str) -> str:
        return self.pages.get(url, "")


@pytest.fixture
def fake_pages(monkeypatch) -> FakePages:
    pages = FakePages()
    monkeypatch.setattr(live_api, "_fetch_page", pages.fetch)
    return pages


# ----------------------------------------------------------------- builders

def row(pdga: int, name: str, rating: int = 1000, *, round_to_par=None, to_par=None,
        played=None, has_score=False, running_place=None, grand_total=None,
        teammates=None, tee_time="") -> dict:
    """One player-row in a round sheet, in the live API's field names.

    `tee_time` mirrors PDGA's TeeTime/HasGroupAssignment pair: set for a player
    scheduled to play this round, empty for one who isn't in it at all (a
    finals non-qualifier). It is what separates "hasn't teed off yet" from
    "not playing this round"."""
    return {
        "PDGANum": pdga, "Name": name, "Rating": rating,
        "RoundtoPar": round_to_par, "ToPar": to_par,
        "Played": played, "HasRoundScore": has_score,
        "RunningPlace": running_place, "GrandTotal": grand_total,
        "Teammates": teammates or [],
        "TeeTime": tee_time, "HasGroupAssignment": 1 if tee_time else 0,
    }


def round_payload(rows: list[dict]) -> dict:
    return {"scores": rows}


def event_payload(name: str, final_round: int, divisions: list[tuple[str, int]],
                  end_date: str | None = None, round_ids: list[int] | None = None,
                  round_labels: dict[int, str] | None = None) -> dict:
    """`round_ids` mirrors PDGA's RoundsList keys. Ids >= 11 are finals, so an
    event with a Final 9 looks like [1, 2, 3, 12] with FinalRound=12 — a round
    ID, not a count (see live_api._round_plan).

    `round_labels` sets the entries' "Label", which is how PDGA distinguishes a
    played round from a sudden-death playoff. Entries carry no label unless one
    is given, which is also a real payload shape worth covering."""
    ids = round_ids if round_ids is not None else list(range(1, final_round + 1))
    labels = round_labels or {}
    return {
        "Name": name, "FinalRound": final_round, "EndDate": end_date,
        "Rounds": len([i for i in ids if i < 11]),
        "RoundsList": {
            str(i): {"Number": i, **({"Label": labels[i]} if i in labels else {})}
            for i in ids
        },
        "Divisions": [{"Division": d, "LatestRound": lr} for d, lr in divisions],
    }


def finished_sheet(players: list[tuple[int, str, int]], places: list[int]) -> list[dict]:
    """A completed round's sheet: everyone posted a score."""
    return [
        row(p, n, r, played=18, has_score=True, running_place=pl, grand_total=200)
        for (p, n, r), pl in zip(players, places)
    ]


# --------------------------------------------------------------- tiny world

@dataclass
class World:
    players: list[tuple[int, str, int]]     # (pdga, name, rating) with a standings row
    star: int                               # won both completed events
    dnf: int                                # missed an R2 score at event 1 -> no points
    newcomer: int                           # registered for the major, no starts yet
    live_tid: int
    completed_tid: int
    expected_players: int                   # standings + roster-added
    live_field_size: int
    live_leader_cur: float
    tmp: Path = field(default=None, repr=False)


@pytest.fixture
def tiny_world(tmp_path, fake_api, monkeypatch) -> World:
    today = dt.date.today()

    def day(offset: int) -> str:
        return (today + dt.timedelta(days=offset)).isoformat()

    data_dir = tmp_path / "data"
    cache_dir = data_dir / "cache"
    docs_data = tmp_path / "docs" / "data"
    for d in (data_dir, cache_dir, docs_data):
        d.mkdir(parents=True)

    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    monkeypatch.setattr(config, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(config, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(schedule, "SCHEDULE_CSV", data_dir / "schedule_2026.csv")
    monkeypatch.setattr(ratings, "RATINGS_FILE", data_dir / "current_ratings.json")
    monkeypatch.setattr(ratings, "FETCH_STAMP", cache_dir / "ratings_fetch_stamp")
    monkeypatch.setattr(live_api, "LIVE_CACHE", cache_dir / "live")
    monkeypatch.setattr(live_api, "RESULTS_CACHE", cache_dir / "results")
    monkeypatch.setattr(live_api, "FLIGHT_DIR", cache_dir / "flight")
    monkeypatch.setattr(invariants, "MARKER", cache_dir / "invariant_violations.txt")
    monkeypatch.setattr(fields, "OVERRIDES_CSV", data_dir / "overrides" / "fields.csv")
    monkeypatch.setattr(export, "DOCS_DATA", docs_data)
    monkeypatch.setattr(snapshot, "SNAP_DIR", tmp_path / "predictions")
    monkeypatch.setattr(movers, "APP_DATA", docs_data)
    monkeypatch.setattr(movers, "OUT", docs_data / "movers.json")

    # --- players ---
    players = [(5000 + i, f"Player {i:02d}", 1040 - 2 * (i - 1)) for i in range(1, 31)]
    star, dnf, newcomer = 5001, 5999, 5777
    dnf_p = (dnf, "Dnf Guy", 1000)

    # --- schedule: 2 completed, 1 live, 1 future major, doubles, playoffs, cup ---
    sched_rows = [
        (900001, "Test Open", "elite", day(-20), day(-18), True),
        (900002, "Test Plus Open", "elite_plus", day(-10), day(-8), True),
        (900003, "Test Live Open", "elite", day(-1), day(+1), False),
        (900004, "Test Major", "major", day(+12), day(+15), False),
        (config.TID_DOUBLES, "Test Doubles", "doubles", day(+20), day(+21), False),
        (config.TID_GMC, "Test GMC", "playoff", day(+40), day(+42), False),
        (config.TID_MVP, "Test MVP", "playoff", day(+47), day(+49), False),
        (config.TID_CHAMPIONSHIP, "Test Cup", "championship", day(+60), day(+62), False),
    ]
    with open(schedule.SCHEDULE_CSV, "w", newline="", encoding="utf-8") as f:
        import csv
        w = csv.DictWriter(f, fieldnames=schedule.FIELDS)
        w.writeheader()
        for tid, name, cls, start, end, completed in sched_rows:
            w.writerow({
                "tournament_id": tid, "name": name, "cls": cls,
                "start_date": start, "end_date": end,
                "mpo": True, "fpo": True, "fpo_points": True, "completed": completed,
            })

    # --- official ratings (fresh stamp so no fetch is attempted) ---
    ratings.RATINGS_FILE.write_text(json.dumps({
        "MPO": {str(p): r for p, _, r in players + [dnf_p]}, "FPO": {},
    }), encoding="utf-8")
    ratings.FETCH_STAMP.write_text(str(time.time()), encoding="utf-8")

    # --- completed event 900001: 30 finishers (T2 tie), one DNF ---
    field_1 = players + [dnf_p]
    places_1 = [1, 2, 2] + list(range(4, 31)) + [31]
    r1 = finished_sheet(field_1, places_1)
    r2 = finished_sheet(field_1, places_1)
    next(r for r in r2 if r["PDGANum"] == dnf).update(HasRoundScore=False, Played=0)
    fake_api.event(900001, event_payload("Test Open", 3, [("MPO", 3)], end_date=day(-18)))
    for rnd, sheet in ((1, r1), (2, r2), (3, finished_sheet(field_1, places_1))):
        fake_api.round(900001, "MPO", rnd, round_payload(sheet))

    # --- completed event 900002: first 25 players, clean 1..25 finish ---
    field_2 = players[:25]
    places_2 = list(range(1, 26))
    fake_api.event(900002, event_payload("Test Plus Open", 3, [("MPO", 3)], end_date=day(-8)))
    for rnd in (1, 2, 3):
        fake_api.round(900002, "MPO", rnd, round_payload(finished_sheet(field_2, places_2)))

    # --- live event 900003: R1 done, R2 in progress ---
    live_players = players[:20]
    rtp1 = {p: -8 + (i - 1) for i, (p, _, _) in enumerate(live_players, 1)}
    rtp2 = {p: -2 + (i % 3) for i, (p, _, _) in enumerate(live_players, 1)}
    wd, not_started = 5018, (5016, 5017)
    live_r1 = [
        row(p, n, r, round_to_par=rtp1[p], to_par=rtp1[p], played=18, has_score=True)
        for p, n, r in live_players
    ]
    cur = {
        p: rtp1[p] + (0 if p in not_started else rtp2[p])
        for p, _, _ in live_players if p != wd
    }
    running = {
        p: 1 + sum(1 for q in cur.values() if q < cur[p] - 1e-9)
        for p in cur
    }
    live_r2 = []
    for p, n, r in live_players:
        if p == wd:
            live_r2.append(row(p, n, r, played=0, grand_total="999"))
        elif p in not_started:
            live_r2.append(row(p, n, r, played=0, running_place=running[p]))
        else:
            live_r2.append(row(p, n, r, round_to_par=rtp2[p], played=9,
                               running_place=running[p]))
    fake_api.event(900003, event_payload("Test Live Open", 3, [("MPO", 2)], end_date=day(+1)))
    fake_api.round(900003, "MPO", 1, round_payload(live_r1))
    fake_api.round(900003, "MPO", 2, round_payload(live_r2))

    # --- future major 900004: registration sheet incl. a first-timer ---
    reg = [row(p, n, r, played=0) for p, n, r in players[:12]]
    reg.append(row(newcomer, "New Comer", 1015, played=0))
    fake_api.round(900004, "MPO", 1, round_payload(reg))
    fake_api.event(900004, event_payload("Test Major", 4, [("MPO", None)], end_date=day(+15)))

    # --- doubles: two teams + one solo entrant of record ---
    dbl = [
        row(5004, "Player 04", 1034, played=0, teammates=[{"PDGANum": 5005, "Name": "Player 05"}]),
        row(5006, "Player 06", 1030, played=0, teammates=[{"PDGANum": 5007, "Name": "Player 07"}]),
        row(5008, "Player 08", 1026, played=0),
    ]
    fake_api.round(config.TID_DOUBLES, "MPO", 1, round_payload(dbl))

    points.refresh_classes()
    return World(
        players=players, star=star, dnf=dnf, newcomer=newcomer,
        live_tid=900003, completed_tid=900001,
        expected_players=31,           # 30 with standings rows + roster-added newcomer
        live_field_size=19,            # 20 in the live event minus the WD
        live_leader_cur=float(cur[star]),
        tmp=tmp_path,
    )
