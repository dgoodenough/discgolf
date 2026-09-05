"""RSS feed of the biggest movers, from the JSON the app already reads.

Why a feed at all: the ask behind it was "buzz my phone when someone's Cup
odds swing during a round", and web push is a genuinely large piece of work —
a service worker, a subscription store, something to send from. A feed is the
cheap 90%: one more file in the build, it notifies through whatever reader
someone already runs, and it needs no server. Anyone who wants the phone buzz
can wire this up themselves.

Shape. One item per mover per window, for `day` and `week`. The season window
is deliberately excluded: it is measured in standings places rather than odds
and restates the whole season on every publish, which is a table, not news.

Item identity is what decides whether a reader nags. The guid is
`{division}-{pdga}-{window}-{latest date}`, so a player's day item is ONE item
that updates as the tournament plays — the odds inside it change every refresh,
and readers key on the guid, so they show the newest text rather than firing a
fresh notification every five minutes. On Monday the week guid rolls over and
the move is reported once against the new baseline.

`docs/data/movers.json` is the input rather than the snapshot CSVs: it is
already computed, already carries every "why" the items quote, and this way the
feed cannot disagree with the panel on the site.
"""
from __future__ import annotations

import datetime as dt
import json
from xml.sax.saxutils import escape

from . import config

SITE = "https://dgoodenough.github.io/discgolf"
APP_DATA = config.REPO_ROOT / "docs" / "data"
MOVERS_JSON = APP_DATA / "movers.json"
OUT = APP_DATA / "movers.xml"

# `season` is excluded on purpose (see the module docstring)
FEED_WINDOWS = ("day", "week")
WINDOW_LABEL = {"day": "today", "week": "this week"}
MAX_ITEMS = 60

# Built by hand rather than with strftime: %a and %b are locale-dependent, and
# a CI runner in another locale would emit a date no reader can parse.
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _rfc822(date: dt.date) -> str:
    """RFC 822 date, which is what RSS 2.0 wants."""
    return (f"{DAYS[date.weekday()]}, {date.day:02d} {MONTHS[date.month - 1]} "
            f"{date.year} 12:00:00 +0000")


def _iso(value: str, fallback: dt.date | None = None) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except (TypeError, ValueError):
        return fallback or dt.date.today()


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


def _short(name: str) -> str:
    """The app's own event-name shortening, in the two forms that matter here."""
    for prefix in ("DGPT Playoffs - ", "DGPT - ", "DGPT+ ", "DGPT "):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    for sep in (" presented by ", " Presented by ", " powered by ", " by MVP"):
        if sep in name:
            name = name.split(sep)[0]
    return name


def _event_names() -> dict[int, str]:
    """Event ids to names, off whichever division bundle is present. The panel
    resolves these against the bundle it already has in memory; the feed reads
    one from disk so its items can name a tournament rather than print an id."""
    names: dict[int, str] = {}
    for div in ("mpo", "fpo"):
        path = APP_DATA / f"{div}.json"
        if not path.exists():
            continue
        try:
            bundle = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        for row in bundle.get("schedule", []):
            names.setdefault(row["tid"], _short(row["name"]))
    return names


def _why(m: dict, names: dict[int, str]) -> list[str]:
    """The same three explanations the movers panel gives, as sentences."""
    out = []
    lr = m.get("last_result")
    if lr:
        place = f" ({_ordinal(lr['place'])})" if lr.get("place") else ""
        out.append(f"Last result: {names.get(lr['tid'], lr['tid'])} "
                   f"{round(lr['pts'])} pts{place}.")
    rd = m.get("rating_delta")
    if rd:
        out.append(f"Rating {'+' if rd > 0 else '−'}{abs(rd)} "
                   f"({m.get('rating_from')} → {m.get('rating_to')}).")
    added = [str(names.get(t, t)) for t in (m.get("reg_added") or [])]
    removed = [str(names.get(t, t)) for t in (m.get("reg_removed") or [])]
    if added:
        out.append("Entered: " + ", ".join(added) + ".")
    if removed:
        out.append("Withdrew: " + ", ".join(removed) + ".")
    return out


def _items(data: dict, names: dict[int, str]) -> list[dict]:
    items = []
    for div in ("mpo", "fpo"):
        windows = data.get(div) or {}
        for window in FEED_WINDOWS:
            block = windows.get(window)
            # a window with no movers is real information on the site ("nothing
            # moved") but it is not an entry in a feed
            if not block or not block.get("movers"):
                continue
            latest = _iso(block.get("latest"))
            for m in block["movers"]:
                # Deliberately no "+15 pts" here. In disc golf "points" means
                # DGPT points, and reusing it for percentage points made a
                # title that read like a scoring change. The arrow carries the
                # move; a unit that needs explaining does not belong in it.
                move = f"{round(m['champ_from'] * 100)}% → {round(m['champ_to'] * 100)}%"
                verb = "up" if m["delta"] > 0 else "down"
                body = [f"Powerball Cup odds {verb} from "
                        f"{round(m['champ_from'] * 100)}% to {round(m['champ_to'] * 100)}% "
                        f"{WINDOW_LABEL[window]}. "
                        f"Now {_ordinal(m['rank_to'])} in the {div.upper()} standings."]
                body += _why(m, names)
                items.append({
                    "guid": f"{div}-{m['pdga']}-{window}-{latest.isoformat()}",
                    "title": (f"{div.upper()} · {m['name']}: Cup odds {move} "
                              f"({WINDOW_LABEL[window]})"),
                    "link": f"{SITE}/#{div}-{m['pdga']}",
                    "desc": " ".join(body),
                    "date": latest,
                    "swing": abs(m["delta"]),
                })
    # newest window first, biggest move first inside it, so a reader that
    # truncates the feed keeps the news rather than an alphabet
    items.sort(key=lambda it: (-it["date"].toordinal(), -it["swing"]))
    return items[:MAX_ITEMS]


def build(data: dict, names: dict[int, str] | None = None,
          now: dt.datetime | None = None) -> str:
    now = now or dt.datetime.now(dt.timezone.utc)
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
        "<channel>",
        "<title>DGPT Standings Forecast — biggest movers</title>",
        f"<link>{SITE}/</link>",
        "<description>Players whose Powerball Cup odds moved most, from a "
        "100,000-run simulation of the 2026 DGPT season. Updated within "
        "minutes of a scoring change during live play.</description>",
        "<language>en-us</language>",
        f"<lastBuildDate>{_rfc822(now.date())}</lastBuildDate>",
        f'<atom:link href="{SITE}/data/movers.xml" rel="self" '
        'type="application/rss+xml"/>',
    ]
    for it in _items(data, names if names is not None else {}):
        parts += [
            "<item>",
            f"<title>{escape(it['title'])}</title>",
            f"<link>{escape(it['link'])}</link>",
            f'<guid isPermaLink="false">{escape(it["guid"])}</guid>',
            f"<pubDate>{_rfc822(it['date'])}</pubDate>",
            f"<description>{escape(it['desc'])}</description>",
            "</item>",
        ]
    parts += ["</channel>", "</rss>"]
    return "\n".join(parts) + "\n"


def write_feed() -> str:
    """Regenerate docs/data/movers.xml, returning a line for the refresh log.

    Never raises on missing or half-written input: the feed is a convenience
    and it must not be able to fail a publish that produced good odds."""
    if not MOVERS_JSON.exists():
        return "movers feed: skipped (no movers.json)"
    try:
        data = json.loads(MOVERS_JSON.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return f"movers feed: skipped ({e})"
    xml = build(data, _event_names())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(xml, encoding="utf-8")
    return f"movers feed: {xml.count('<item>')} items -> {OUT.name}"


if __name__ == "__main__":
    print(write_feed())
