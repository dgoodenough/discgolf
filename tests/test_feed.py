"""The movers RSS feed.

The properties worth pinning are the ones a feed reader depends on and a
casual refactor can silently break: that a guid is stable across the refreshes
inside one day (so a tournament does not fire a notification every five
minutes) and rolls over when the window does, that the season window stays out,
and that names and text carrying `&`, `<` or a quote come out as valid XML.
"""
from __future__ import annotations

import datetime as dt
import xml.etree.ElementTree as ET

import pytest

from dgpt import feed


def mover(pdga=12345, name="Test Player", champ_from=0.34, champ_to=0.49,
          rank_to=19, **extra):
    m = {
        "pdga": pdga, "name": name,
        "champ_from": champ_from, "champ_to": champ_to,
        "delta": round(champ_to - champ_from, 4),
        "rank_from": rank_to + 3, "rank_to": rank_to,
        "last_result": None, "reg_added": [], "reg_removed": [],
        "rating_from": None, "rating_to": None, "rating_delta": None,
    }
    m.update(extra)
    return m


def data(day=None, week=None, season=None):
    block = lambda latest, movers: {"baseline": "2026-08-30", "latest": latest,
                                    "movers": movers}
    return {
        "mpo": {
            "day": block("2026-09-04", day if day is not None else []),
            "week": block("2026-09-01", week if week is not None else []),
            "season": season,
        },
        "fpo": {"day": None, "week": None, "season": None},
    }


def items(xml):
    return ET.fromstring(xml).find("channel").findall("item")


def test_parses_as_xml_and_carries_channel_metadata():
    xml = feed.build(data(day=[mover()]))
    channel = ET.fromstring(xml).find("channel")
    assert channel.find("title").text.startswith("DGPT Standings Forecast")
    assert channel.find("link").text == feed.SITE + "/"
    assert len(channel.findall("item")) == 1


def test_item_links_to_the_player_permalink():
    it = items(feed.build(data(day=[mover(pdga=75412)])))[0]
    assert it.find("link").text == f"{feed.SITE}/#mpo-75412"


def test_guid_is_stable_within_a_day_but_not_across_windows():
    """The whole point of the guid: a reader must not re-notify for the same
    player's same day as the odds move through a round."""
    early = feed.build(data(day=[mover(champ_to=0.49)]))
    later = feed.build(data(day=[mover(champ_to=0.71)]))
    g = lambda xml: items(xml)[0].find("guid").text
    assert g(early) == g(later) == "mpo-12345-day-2026-09-04"
    # ...but the text does change, so the reader shows the newer odds
    assert items(early)[0].find("title").text != items(later)[0].find("title").text

    rolled = feed.build(data(day=[]), )  # next day's block, same player
    assert not items(rolled)
    tomorrow = data(day=[mover()])
    tomorrow["mpo"]["day"]["latest"] = "2026-09-05"
    assert g(feed.build(tomorrow)) == "mpo-12345-day-2026-09-05"


def test_season_window_is_excluded():
    season = {"metric": "rank", "latest": "2026-09-04",
              "movers": [mover(name="Season Only")]}
    xml = feed.build(data(day=[mover(name="Day Mover")], season=season))
    titles = " ".join(it.find("title").text for it in items(xml))
    assert "Day Mover" in titles
    assert "Season Only" not in titles


def test_empty_windows_produce_a_valid_empty_feed():
    xml = feed.build(data())
    assert not items(xml)
    ET.fromstring(xml)   # still well-formed


def test_escapes_xml_metacharacters_in_names_and_events():
    """Event names really do contain `&` (…Presented by Discmania & OTB), and
    an unescaped one makes the whole feed unparseable rather than one bad item."""
    m = mover(name='Bob "Bobby" O\'Neill & Son',
              last_result={"tid": 1, "pts": 812.5, "place": 3})
    xml = feed.build(data(day=[m]), {1: "Ledgestone <Open> & Co"})
    it = items(xml)[0]
    assert "O'Neill & Son" in it.find("title").text
    assert "Ledgestone <Open> & Co" in it.find("description").text


def test_description_carries_the_three_whys():
    m = mover(
        last_result={"tid": 7, "pts": 500.0, "place": 1},
        rating_from=1012, rating_to=1019, rating_delta=7,
        reg_added=[8], reg_removed=[9],
    )
    desc = items(feed.build(data(day=[m]), {7: "Idlewild", 8: "GMC", 9: "MVP Open"}))[0]
    text = desc.find("description").text
    assert "Idlewild 500 pts (1st)" in text
    assert "Rating +7 (1012 → 1019)" in text
    assert "Entered: GMC." in text
    assert "Withdrew: MVP Open." in text


def test_odds_are_reported_as_whole_percents():
    it = items(feed.build(data(day=[mover(champ_from=0.336, champ_to=0.492)])))[0]
    assert "Cup odds 34% → 49%" in it.find("title").text


def test_titles_carry_no_ambiguous_points_unit():
    """"Points" means DGPT points to every reader of this feed, so a swing must
    not be quoted in them — the arrow carries the move instead."""
    it = items(feed.build(data(day=[mover(champ_from=0.336, champ_to=0.492)])))[0]
    assert "pts" not in it.find("title").text


def test_a_fall_reads_as_a_fall():
    it = items(feed.build(data(day=[mover(champ_from=0.60, champ_to=0.41)])))[0]
    assert "60% → 41%" in it.find("title").text
    assert "down from 60% to 41%" in it.find("description").text


def test_newest_window_first_then_biggest_swing():
    xml = feed.build(data(
        day=[mover(pdga=1, name="Small Day", champ_from=0.5, champ_to=0.53),
             mover(pdga=2, name="Big Day", champ_from=0.2, champ_to=0.7)],
        week=[mover(pdga=3, name="Huge Week", champ_from=0.1, champ_to=0.9)],
    ))
    order = [it.find("title").text for it in items(xml)]
    assert "Big Day" in order[0]
    assert "Small Day" in order[1]
    assert "Huge Week" in order[2]   # older window, however big the move


def test_item_cap_is_honoured():
    many = [mover(pdga=i, champ_to=0.34 + i / 1000) for i in range(200)]
    assert len(items(feed.build(data(day=many)))) == feed.MAX_ITEMS


@pytest.mark.parametrize("date,expected", [
    (dt.date(2026, 9, 4), "Fri, 04 Sep 2026 12:00:00 +0000"),
    (dt.date(2026, 1, 1), "Thu, 01 Jan 2026 12:00:00 +0000"),
    (dt.date(2026, 7, 13), "Mon, 13 Jul 2026 12:00:00 +0000"),
])
def test_rfc822_dates(date, expected):
    """Hand-built rather than strftime'd, so a CI runner's locale cannot make
    the dates unreadable to a feed reader."""
    assert feed._rfc822(date) == expected


def test_write_feed_skips_quietly_without_input(tmp_path, monkeypatch):
    """The feed is a convenience — it must never be able to fail a publish that
    produced good odds."""
    monkeypatch.setattr(feed, "MOVERS_JSON", tmp_path / "nope.json")
    assert "skipped" in feed.write_feed()

    bad = tmp_path / "movers.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(feed, "MOVERS_JSON", bad)
    assert "skipped" in feed.write_feed()


def test_write_feed_emits_the_file(tmp_path, monkeypatch):
    import json
    src = tmp_path / "movers.json"
    src.write_text(json.dumps(data(day=[mover()])), encoding="utf-8")
    monkeypatch.setattr(feed, "MOVERS_JSON", src)
    monkeypatch.setattr(feed, "OUT", tmp_path / "movers.xml")
    monkeypatch.setattr(feed, "APP_DATA", tmp_path)
    msg = feed.write_feed()
    assert "1 items" in msg
    assert len(items((tmp_path / "movers.xml").read_text(encoding="utf-8"))) == 1


@pytest.mark.parametrize("raw,short", [
    ("DGPT - LWS Open at Idlewild", "LWS Open at Idlewild"),
    ("DGPT Playoffs - Green Mountain Championship presented by Discmania",
     "Green Mountain Championship"),
    ("Music City Open", "Music City Open"),
])
def test_event_names_are_shortened_like_the_app_does(raw, short):
    assert feed._short(raw) == short
