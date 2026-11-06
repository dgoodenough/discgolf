"""The tour planner's offline half: schedule parsing, eligibility, the bundle."""
import datetime as dt

import pytest

from dgpt import tourplan


def _row(start, end, tier, cls, name, loc):
    return (f"<tr><td>{start}</td><td>{end}</td><td>{tier}</td><td>{cls}</td>"
            f"<td><a href='#'>{name}</a></td><td>{loc}</td></tr>")


def _page(n_filler=60):
    rows = [
        _row("12-Mar", "14-Mar", "ES", "MPO/FPO Only", "Supreme Flight Open", "Brooksville, FL&nbsp;&nbsp;USA"),
        _row("7-Oct", "10-Oct", "XM", "MPO Only", "United States Disc Golf Championships", "Rock Hill, SC&nbsp;&nbsp;USA"),
        _row("19-Aug", "22-Aug", "M", "Pro/Am", "United States Womens Disc Golf Championships", "Columbia, MO&nbsp;&nbsp;USA"),
        _row("28-Sep", "2-Oct", "M", "Pro/Am", "PDGA Masters Disc Golf World Championships", "Tulsa, OK&nbsp;&nbsp;USA"),
        _row("20-Feb", "21-Feb", "B/A", "Pro/Am", "DGPT Companion: Cherry Blossom Open (Non-MPO/FPO)", "Conyers, GA&nbsp;&nbsp;USA"),
        _row("20-Aug", "22-Aug", "A", "Pro/Am", "Eastern Canadian Amateur National", "Bourget, ON&nbsp;&nbsp;Canada"),
        _row("9-Jul", "11-Jul", "ES", "MPO/FPO Only", "European Disc Golf Festival", "Tallinn, Estonia"),
        _row("16-Jul", "18-Jul", "ES", "MPO/FPO Only", "TBA", "Krokhol,&nbsp;&nbsp;Norway"),
        _row("16-Oct", "17-Oct", "A", "Pro/Am", "Norcal Series Championships", "TBD, CA&nbsp;&nbsp;USA"),
        _row("10-Sep", "12-Sep", "A", "Pro/Am", "Greater Toronto Open", "Milton, ON&nbsp;&nbsp;Canada"),
    ]
    rows += [_row("9-Jan", "10-Jan", "A", "Am only", f"Filler {i}", "Estero, FL&nbsp;&nbsp;USA")
             for i in range(n_filler)]
    head = "<tr><th>Start</th><th>End</th><th>Tier</th><th>Class</th><th>Name</th><th>Location</th></tr>"
    return f"<html><table>{head}{''.join(rows)}</table></html>"


@pytest.fixture
def rows(monkeypatch, tmp_path):
    monkeypatch.setattr(tourplan, "OVERRIDES_CSV", tmp_path / "none.csv")
    return {r["name"]: r for r in tourplan.parse_schedule(_page())}


def test_parse_dates_and_location(rows):
    r = rows["Supreme Flight Open"]
    assert (r["start"], r["end"]) == ("2027-03-12", "2027-03-14")
    assert (r["city"], r["region"], r["country"]) == ("Brooksville", "FL", "USA")
    assert rows["European Disc Golf Festival"]["country"] == "Estonia"
    assert rows["TBA"]["country"] == "Norway"


def test_eligibility(rows):
    sfo = rows["Supreme Flight Open"]
    assert (sfo["cat"], sfo["mpo"], sfo["fpo"]) == ("elite", True, True)  # "MPO/FPO Only"
    usdgc = rows["United States Disc Golf Championships"]
    assert (usdgc["cat"], usdgc["mpo"], usdgc["fpo"]) == ("major", True, False)
    womens = rows["United States Womens Disc Golf Championships"]
    assert (womens["mpo"], womens["fpo"]) == (False, True)
    for excluded in ("PDGA Masters Disc Golf World Championships",
                     "DGPT Companion: Cherry Blossom Open (Non-MPO/FPO)",
                     "Eastern Canadian Amateur National", "Filler 0"):
        assert rows[excluded]["cat"] == "", excluded


def test_plannable_drops_overseas_and_unplaced(rows):
    names = {r["name"] for r in tourplan.plannable(list(rows.values()))}
    assert "Greater Toronto Open" in names                 # Canada drives
    assert "European Disc Golf Festival" not in names
    assert "Norcal Series Championships" not in names      # venue TBD


def test_overrides(monkeypatch, tmp_path):
    f = tmp_path / "ov.csv"
    f.write_text("name,restrict,note\nGreater Toronto Open,fpo,\nSupreme Flight Open,juniors,\n")
    monkeypatch.setattr(tourplan, "OVERRIDES_CSV", f)
    rows = {r["name"]: r for r in tourplan.parse_schedule(_page())}
    assert (rows["Greater Toronto Open"]["mpo"], rows["Greater Toronto Open"]["fpo"]) == (False, True)
    assert rows["Supreme Flight Open"]["cat"] == ""


def test_layout_change_is_loud():
    with pytest.raises(ValueError):
        tourplan.parse_schedule("<html><p>nothing here</p></html>")


@pytest.mark.parametrize("end,wk", [
    (dt.date(2027, 1, 3), 0),    # a Sunday
    (dt.date(2027, 1, 9), 1),    # Saturday -> the next day's Sunday
    (dt.date(2027, 1, 11), 1),   # holiday Monday -> the Sunday before
    (dt.date(2027, 10, 2), 39),  # Tue-Sat Masters Worlds
])
def test_weekend_index(end, wk):
    assert tourplan.weekend_index(end) == wk


def test_bundle(rows):
    plan = tourplan.plannable(list(rows.values()))
    keys = sorted({tourplan.venue_key(r) for r in plan})
    venues = {k: {"lat": 1.0, "lon": 2.0} for k in keys}
    n = len(keys)
    drive = {"keys": keys, "minutes": [[0 if i == j else 60 for j in range(n)] for i in range(n)]}
    b = tourplan.bundle(list(rows.values()), venues, drive)
    assert len(b["minutes"]) == n * n
    assert len(b["events"]) == len(plan)
    assert all(0 <= e["v"] < n for e in b["events"])
    assert [e["start"] for e in b["events"]] == sorted(e["start"] for e in b["events"])
    drive["keys"] = keys[1:]
    with pytest.raises(ValueError):
        tourplan.bundle(list(rows.values()), venues, drive)


def test_tracked_data_exports():
    """The committed data/tourplan files are consistent with each other."""
    b = tourplan.bundle(tourplan.load_events(), tourplan.load_venues(),
                        __import__("json").loads(tourplan.DRIVE_JSON.read_text()))
    assert b["meta"]["n_venues"] > 100
    assert {e["cat"] for e in b["events"]} == {"elite", "major", "a"}
    assert all(m >= 0 for m in b["minutes"])
