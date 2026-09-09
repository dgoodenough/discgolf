"""Forecast a season that has not started yet.

`simulate.py` forecasts 2026: it has banked results, live scores, published
registration lists and real qualification waves, and most of its complexity
exists to respect them. None of that exists for 2027. What is left when you
take it all away is the part that was always the model — ratings in, scores
out, points off a curve, caps applied, standings ranked — run over a schedule
instead of over the remainder of one.

So this is that part, and only that part: every player starts on zero, every
field is drawn from participation rates, and nothing is ever locked in. It is
deliberately a separate module rather than a mode of `simulate.run`. The 2026
forecast is the site's product and updates every fifteen minutes during play;
threading a "no results yet" flag through its live-scoring, doubles-pairing
and signup-list paths would put an experimental tab's bugs in its way.

What it does share is the parts that must not fork: the score model
(`simulate.RATING_PTS_PER_STROKE`, `simulate.ROUND_SD`), the points curves
(`points.base_curves`), the participation model (`fields.participation_rates`)
and the ranking helpers. If the calibration moves, both forecasts move.

Two tours run through it, described by `season2027.TourSpec`:

    DGPT      a full season plus the postseason — playoff gates, the
              Cup field, the seed ladder, and the Cup itself played out
    EuroTour  a standings race whose prize is 2028 Tour Cards

The engine is the same for both; the spec says which pools count, whether
there is a postseason, and who is eligible to be in the table at all.
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass

import numpy as np

from . import config, fields, season2027, simulate

DEFAULT_SIMS = 20_000
MAX_HIST_RANK = simulate.MAX_HIST_RANK
DOCS_DATA = config.REPO_ROOT / "docs" / "data"


def _hist_depth(cut: int, n: int) -> int:
    """How deep the finishing-position histogram goes, per tour.

    The 2026 forecast fixes this at 50, which suits a 28-deep cut in a table
    of hundreds. The EuroTour is a different shape — a 10-card band in a table
    of ~60 — and 50 buckets there spend four fifths of the sparkline on
    positions nobody is racing for while squashing the ones they are. Scale it
    to the cut instead, floored so a race is never drawn on a dozen buckets
    and capped by both the 2026 depth and the size of the table itself.
    """
    return max(2, min(MAX_HIST_RANK, max(20, 2 * cut), n))

# How many players reach the published bundle. The full 2026 standings run to
# several hundred rows, and a forecast that starts everyone on zero gives the
# tail nothing to say: no banked points to show, no cut to be near. Keeping the
# top slice by projected points holds the file to a size a phone will fetch,
# and the union below makes sure nobody with a live path to the Cup is cut
# from it on a technicality.
EXPORT_LIMIT = 200

# A national championship is a home event: no flights, no visas, no week off.
# So a player's European-travel rate is a floor for it rather than the answer —
# somebody who crosses the continent for one A-Tier a year still turns up to
# their own national championship. Assumed, like everything else on that tab.
ET_NAT_FLOOR = 0.5

# Smallest country field the model will call a national championship. Below
# it, the players are pooled into a regional one — see _national_fields.
ET_NAT_MIN_FIELD = 4


@dataclass
class ProjResult:
    """A projected season. DGPT-only fields are zero-filled for the EuroTour."""
    spec: season2027.TourSpec
    division: str
    n_sims: int
    # roster
    names: list[str]
    pdga_numbers: list[int]
    countries: list[str]
    ratings: np.ndarray
    rank_2026: list[int]
    points_2026: list[float]
    # outcomes
    mean_points: np.ndarray
    mean_rank: np.ndarray
    p_first: np.ndarray
    rank_hist: np.ndarray          # (n, hist_depth); last bucket = that place or worse
    hist_depth: int
    att_probs: np.ndarray          # (n_events, n) realized P(plays)
    events_meta: list[dict]
    # DGPT postseason
    p_cut: np.ndarray              # P(top `standings_cut` = automatic Cup bid)
    p_perf: np.ndarray             # P(Cup spot via the second playoff event)
    p_champ: np.ndarray            # P(in the Cup field at all)
    p_play1: np.ndarray            # P(in the first playoff event's field)
    p_play2: np.ndarray            # P(in the second playoff event's field)
    p_cup_win: np.ndarray
    strokes_hist: np.ndarray
    stroke_values: tuple[int, ...]
    # EuroTour cards
    p_full: np.ndarray             # P(finishes inside the Full Tour Card band)
    p_card: np.ndarray             # P(finishes inside the EuroTour Card band)


# ----------------------------------------------------------------- roster

def _roster(spec: season2027.TourSpec, table: list[dict],
            countries: dict[int, str]) -> list[dict]:
    """Who is in the table: last season's standings, with a rating.

    Nobody is added and nobody ages. A 2027 rookie, a player who quits, a
    2026 also-ran who spends the winter getting good — none of them exist
    here, which is the honest limit of forecasting a season from the one
    before it and is said as much on the page.
    """
    rows = [r for r in table if r.get("rating")]
    if spec.european_only:
        rows = [r for r in rows
                if countries.get(r["pdga_number"], "") in fields.EU_COUNTRIES]
    return rows


# ------------------------------------------------------------- attendance

def _attendance(spec: season2027.TourSpec, evs: list[season2027.Event],
                roster: list[dict], rates: dict[int, dict[str, float]],
                countries: dict[int, str]) -> np.ndarray:
    """(n_events, n_players) P(plays), from this season's observed rates.

    The brief is "a similar share of events to this year", so a player's rate
    is carried across per event GROUP, not per event: their US rate applies to
    2027's US stops, their European rate to the European swing, their JomezPro
    rate to the JomezPro Series. That last one is where the schedule change
    bites — seven Jomez stops instead of three means someone who played one of
    three in 2026 is projected into roughly two of seven in 2027, and their
    bonus pool grows accordingly.
    """
    out = np.zeros((len(evs), len(roster)))
    for ei, ev in enumerate(evs):
        group = season2027.rate_group(ev)
        for i, r in enumerate(roster):
            p = rates.get(r["pdga_number"], {}).get(group, 0.0)
            if ev.cls == "et_nat":
                # home event, and only your own country's
                p = max(p, ET_NAT_FLOOR) if countries.get(r["pdga_number"]) else 0.0
            out[ei, i] = min(max(p, 0.0), 1.0)
    return out


def _national_fields(roster: list[dict], countries: dict[int, str]) -> list[np.ndarray]:
    """Split the table into the fields the championship weekend would play.

    One field per country, except that a federation with almost nobody in
    this table does not get one. Left alone, a country with a single known
    player hands them a national title in every simulated season — a
    guaranteed 75 points against a five-result counting rule, which would be
    the largest single artifact on the tab.

    Those players are pooled into one regional field instead, which is the
    announcement's own second category ("national and regional
    championships"): they still have a title to play for, and they have to
    beat somebody to get it.
    """
    by_country: dict[str, list[int]] = {}
    for i, r in enumerate(roster):
        cc = countries.get(r["pdga_number"])
        if cc:
            by_country.setdefault(cc, []).append(i)
    national = [v for v in by_country.values() if len(v) >= ET_NAT_MIN_FIELD]
    regional = [i for v in by_country.values() if len(v) < ET_NAT_MIN_FIELD for i in v]
    if len(regional) > 1:
        national.append(regional)
    return [np.array(sorted(v)) for v in national]


# ------------------------------------------------------------- the draw

def _curve_vector(division: str, ev: season2027.Event, tour: str, n: int) -> np.ndarray:
    """Points indexed by place 1..n, with the curve's floor paid past its end.

    Same shape and the same tail rule as simulate._curve_vector — a deep
    finish has to be worth the same number in both forecasts.
    """
    vec = np.zeros(n + 2)
    if ev.cls == "jomez":
        from . import points
        for place in range(1, n + 1):
            vec[place] = points.jomez_bonus(place)
        return vec
    curve = season2027.curve(division, ev.cls, tour)
    for place, val in curve.items():
        if place <= n:
            vec[place] = val
    deepest = max(curve)
    if n > deepest:
        vec[deepest + 1 : n + 1] = curve[deepest]
    return vec


class _Drawer:
    """Draws one event's points and places for a chunk of sims."""

    def __init__(self, division: str, evs: list[season2027.Event], tour: str,
                 ratings: np.ndarray, rng: np.random.Generator,
                 groups: list[np.ndarray] | None, meta: list[dict]):
        self.evs = evs
        self.rtg = ratings
        self.rng = rng
        self.rpps = simulate.RATING_PTS_PER_STROKE[division]
        self.groups = groups          # per-country index arrays, for et_nat
        self.meta = meta
        n = ratings.shape[0]
        self.curves = [_curve_vector(division, e, tour, n) for e in evs]
        self.att_count = np.zeros((len(evs), n))

    def draw(self, ei: int, plays: np.ndarray, rows_ix: np.ndarray,
             first_chunk: bool) -> tuple[np.ndarray, np.ndarray]:
        ev = self.evs[ei]
        c, n = plays.shape
        scores = self._scores(ev, plays, c, n)
        if ev.cls == "et_nat" and self.groups:
            place = self._place_by_country(scores, plays, n)
        else:
            order = np.argsort(scores, axis=1)
            place = np.empty_like(order)
            place[rows_ix, order] = np.arange(1, n + 1)[None, :]
        if ev.cls == "doubles":
            # Unpaired doubles: rank the entrants as singles, then read the
            # team place off that ranking. Exactly simulate._draw_singles'
            # fallback for a doubles event whose pairings aren't published —
            # and 2027's never will be this far out, so it is the only branch
            # this event can take.
            place = (place + 1) // 2
        if first_chunk:
            self._record_meta(ei, plays, scores)
        self.att_count[ei] += plays.sum(axis=0)
        pts = self.curves[ei][np.minimum(place, n + 1)]
        pts[~plays] = 0.0
        return pts, place

    def _scores(self, ev: season2027.Event, plays: np.ndarray, c: int, n: int) -> np.ndarray:
        """Event totals relative to the field: the 2026 score model, exactly."""
        rnds = ev.rounds
        if ev.cls == "et_nat" and self.groups:
            # each country plays its own championship, so "the field" a player
            # is measured against is their compatriots, not the continent
            mu = np.zeros(n)
            for ix in self.groups:
                mu[ix] = -(self.rtg[ix] - self.rtg[ix].mean()) / self.rpps * rnds
            mu = np.broadcast_to(mu, (c, n))
        else:
            fsum = (plays * self.rtg).sum(axis=1)
            fcnt = plays.sum(axis=1)
            avg = np.where(fcnt > 0, fsum / np.maximum(fcnt, 1), 1000.0)
            mu = -(self.rtg[None, :] - avg[:, None]) / self.rpps * rnds
        scores = mu + self.rng.normal(0.0, simulate.ROUND_SD * np.sqrt(rnds), (c, n))
        return np.where(plays, scores, np.inf)

    def _place_by_country(self, scores: np.ndarray, plays: np.ndarray, n: int) -> np.ndarray:
        """Finishing place inside each country's own championship."""
        c = scores.shape[0]
        place = np.full((c, n), n + 1, dtype=np.int64)
        rows = np.arange(c)[:, None]
        for ix in self.groups:
            sub = scores[:, ix]
            order = np.argsort(sub, axis=1)
            sub_place = np.empty_like(order)
            sub_place[rows, order] = np.arange(1, ix.size + 1)[None, :]
            place[:, ix] = sub_place
        return np.where(plays, place, n + 1)

    def _record_meta(self, ei: int, plays: np.ndarray, scores: np.ndarray) -> None:
        m = self.meta[ei]
        m["field_size"] = round(float(plays.sum(axis=1).mean()), 1)
        played = plays[0]
        m["field_avg_rating"] = round(float(self.rtg[played].mean()), 1) if played.any() else 0.0


# ------------------------------------------------------------ counting

def _pool_total(cols: dict[str, list[np.ndarray]], spec: season2027.TourSpec,
                c: int, n: int) -> np.ndarray:
    """Season points under the spec's per-pool counting caps."""
    total = np.zeros((c, n))
    for pool in spec.pools:
        pts = cols.get(pool.name)
        if not pts:
            continue
        if pool.keep is None or len(pts) <= pool.keep:
            total += sum(pts, np.zeros((c, n)))
        else:
            stack = np.stack(pts, axis=2)
            total += (-np.sort(-stack, axis=2))[:, :, : pool.keep].sum(axis=2)
    return total


def _pool_of(spec: season2027.TourSpec, cls: str) -> str | None:
    for pool in spec.pools:
        if cls in pool.classes:
            return pool.name
    return None


# ---------------------------------------------------------------- the run

def run(spec: season2027.TourSpec, division: str, table: list[dict],
        sched_2026: list[dict], n_sims: int = DEFAULT_SIMS,
        seed: int | None = 2027, chunk: int = 500) -> ProjResult:
    countries = fields.load_countries()
    roster = _roster(spec, table, countries)
    n = len(roster)
    if n == 0:
        raise ValueError(f"{spec.label} {division}: nobody eligible for the table")

    rtg = np.array([float(r["rating"]) for r in roster])
    rng = np.random.default_rng(seed)

    # participation measured over the WHOLE 2026 table, then indexed — the
    # cohort priors fields.participation_rates shrinks toward should describe
    # the tour, not whichever slice of it this tab happens to show
    rates = fields.participation_rates(
        sched_2026,
        {r["pdga_number"]: {tid for tid, *_ in r["events"]} for r in table if r.get("rating")},
        division,
        {r["pdga_number"]: r["name"] for r in table},
    )

    all_evs = [e for e in season2027.load() if e.on(spec.tour) and e.plays(division)]
    evs = [e for e in all_evs if e.cls != "championship"]
    events_meta = [
        {"id": e.event_id, "name": e.name, "short": e.short_name, "loc": e.location,
         "cls": e.cls, "tour": e.tour, "start": e.start_date, "end": e.end_date,
         "rounds": e.rounds, "field_size": 0.0, "field_avg_rating": 0.0,
         "pool": _pool_of(spec, e.cls) or ""}
        for e in evs
    ]

    groups = _national_fields(roster, countries) if any(e.cls == "et_nat" for e in evs) else None

    att = _attendance(spec, evs, roster, rates, countries)
    drawer = _Drawer(division, evs, spec.tour, rtg, rng, groups, events_meta)

    post = _Postseason.build(spec, evs, division) if spec.championship else None
    cards = season2027.ET_CARDS.get(division, {"full": 0, "card": 0})
    cut = post.standings_cut if post else cards["full"] + cards["card"]
    acc = _Accumulators(n, n_sims, post, _hist_depth(cut, n))

    done = 0
    while done < n_sims:
        c = min(chunk, n_sims - done)
        rows_ix = np.arange(c)[:, None]
        first = done == 0
        _one_chunk(spec, drawer, att, post, acc, rng, c, n, rows_ix, first)
        done += c

    return _finish(spec, division, roster, countries, rtg, n_sims, acc, post,
                   drawer, events_meta)


# ------------------------------------------------------------ postseason

@dataclass
class _Postseason:
    """The DGPT ladder: two playoff events, then the Cup."""
    ei1: int
    ei2: int
    pre: list[int]
    cut1: int
    fill1: int
    cut2: int
    perf2: int
    perf_champ: int
    standings_cut: int
    stroke_values: tuple[int, ...]
    stroke_bucket: np.ndarray
    invite_classes: tuple[str, ...]

    @classmethod
    def build(cls, spec: season2027.TourSpec, evs: list[season2027.Event],
              division: str) -> "_Postseason":
        def find(short: str) -> int:
            for i, e in enumerate(evs):
                if e.short_name == short:
                    return i
            raise ValueError(
                f"{spec.label} {division}: the schedule has no event named "
                f"{short!r} — TourSpec and data/schedule_2027.csv disagree"
            )

        ei1, ei2 = find(spec.playoff1), find(spec.playoff2)
        q1 = season2027.PLAYOFF_QUAL["playoff1"]
        q2 = season2027.PLAYOFF_QUAL["playoff2"]
        values, bucket = simulate._stroke_ladder(division)
        return cls(
            ei1=ei1, ei2=ei2,
            pre=[i for i in range(len(evs)) if i not in (ei1, ei2)],
            cut1=q1["cut"][division], fill1=q1["fill"][division],
            cut2=q2["cut"][division], perf2=q2["perf"][division],
            perf_champ=season2027.FIELD_SIZE[division] - season2027.STANDINGS_CUT[division],
            standings_cut=season2027.STANDINGS_CUT[division],
            stroke_values=values, stroke_bucket=bucket,
            invite_classes=spec.invite_classes,
        )


@dataclass
class _Accumulators:
    n: int
    n_sims: int
    post: _Postseason | None
    depth: int

    def __post_init__(self) -> None:
        n = self.n
        nb = len(self.post.stroke_values) + 1 if self.post else 1
        self.total_pts = np.zeros(n)
        self.total_rank = np.zeros(n)
        self.rank_hist = np.zeros((n, self.depth), dtype=np.int64)
        self.first = np.zeros(n)
        self.cut = np.zeros(n)
        self.perf = np.zeros(n)
        self.champ = np.zeros(n)
        self.play1 = np.zeros(n)
        self.play2 = np.zeros(n)
        self.cup_win = np.zeros(n, dtype=np.int64)
        self.strokes = np.zeros((n, nb), dtype=np.int64)


def _one_chunk(spec, drawer, att, post, acc, rng, c, n, rows_ix, first) -> None:
    pre = post.pre if post else list(range(len(drawer.evs)))
    cols: dict[str, list[np.ndarray]] = {}
    sim_win = np.zeros((c, n), dtype=bool)

    for ei in pre:
        plays = rng.random((c, n)) < att[ei]
        pts, place = drawer.draw(ei, plays, rows_ix, first)
        ev = drawer.evs[ei]
        pool = _pool_of(spec, ev.cls)
        if pool:
            cols.setdefault(pool, []).append(pts)
        if post and ev.cls in post.invite_classes:
            sim_win |= (place == 1) & plays

    base = _pool_total(cols, spec, c, n)
    if post is None:
        _rank_and_record(base, acc, rows_ix, n)
        return

    # -- playoff 1: field = top `fill1` in the standings as they stand --
    rank_pre1 = simulate._rank_of(base, rows_ix, n)
    plays1 = rank_pre1 <= post.fill1
    acc.play1 += plays1.sum(axis=0)
    pts1, place1 = drawer.draw(post.ei1, plays1, rows_ix, first)
    sim_win |= (place1 == 1) & plays1

    # -- playoff 2: top `cut2` on points, plus the top `perf2` from playoff 1 --
    after1 = base + pts1
    rank_pre2 = simulate._rank_of(after1, rows_ix, n)
    plays2 = rank_pre2 <= post.cut2
    elig = plays1 & ~plays2
    plays2 = plays2 | simulate._top_k_by_place(place1, elig, post.perf2, n)
    acc.play2 += plays2.sum(axis=0)
    pts2, place2 = drawer.draw(post.ei2, plays2, rows_ix, first)
    sim_win |= (place2 == 1) & plays2

    # Pools are capped independently, so the season total is the sum of their
    # totals — and `base` is already the non-playoff half. Re-running
    # _pool_total over everything would re-stack and re-sort a 14-deep pool a
    # second time per chunk for an answer it has.
    totals = base + _pool_total({"playoff": [pts1, pts2]}, spec, c, n)
    ranks = _rank_and_record(totals, acc, rows_ix, n)

    # -- Cup field: automatic bids, the playoff-2 performance path, invites --
    auto = ranks <= post.standings_cut
    champ = auto.copy()
    perf = simulate._top_k_by_place(place2, plays2 & ~auto, post.perf_champ, n)
    acc.perf += perf.sum(axis=0)
    champ |= perf
    champ |= sim_win
    acc.cut += auto.sum(axis=0)
    acc.champ += champ.sum(axis=0)

    # -- the seed ladder, and the Cup played out on it --
    nb = len(post.stroke_values) + 1
    depth = post.stroke_bucket.shape[0]
    seed = post.stroke_bucket[np.minimum(ranks, depth) - 1]
    bucket = np.where(champ, seed, nb - 1)
    flat = bucket + (np.arange(n) * nb)[None, :]
    acc.strokes += np.bincount(flat.ravel(), minlength=n * nb).reshape(n, nb)

    rtg = drawer.rtg
    fcnt = champ.sum(axis=1)
    favg = np.where(fcnt > 0, (champ * rtg[None, :]).sum(axis=1) / np.maximum(fcnt, 1), 1000.0)
    mu = -(rtg[None, :] - favg[:, None]) / drawer.rpps * season2027.CUP_ROUNDS
    total = mu + rng.normal(0.0, simulate.ROUND_SD * np.sqrt(season2027.CUP_ROUNDS), (c, n))
    total = total + np.array(post.stroke_values, dtype=float)[seed]
    total = np.where(champ, total, np.inf)
    acc.cup_win += np.bincount(total.argmin(axis=1)[fcnt > 0], minlength=n)


def _rank_and_record(totals: np.ndarray, acc: _Accumulators, rows_ix: np.ndarray,
                     n: int) -> np.ndarray:
    ranks = simulate._rank_of(totals, rows_ix, n)
    acc.total_pts += totals.sum(axis=0)
    acc.total_rank += ranks.sum(axis=0)
    acc.first += (ranks == 1).sum(axis=0)
    capped = np.minimum(ranks, acc.depth)
    stride = acc.depth + 1
    flat = capped + (np.arange(n) * stride)[None, :]
    acc.rank_hist += np.bincount(flat.ravel(), minlength=n * stride).reshape(n, stride)[:, 1:]
    return ranks


def _finish(spec, division, roster, countries, rtg, n_sims, acc, post,
            drawer, events_meta) -> ProjResult:
    n = len(roster)
    zeros = np.zeros(n)
    cards = season2027.ET_CARDS.get(division, {"full": 0, "card": 0})
    full_to = cards["full"]
    card_to = cards["full"] + cards["card"]

    # P(finish inside the top k) is the finishing histogram read cumulatively —
    # the histogram already counts every season, so there is nothing to
    # accumulate separately and no way for the two to disagree.
    cum = np.cumsum(acc.rank_hist, axis=1) / n_sims

    def le(k: int) -> np.ndarray:
        if k <= 0:
            return zeros
        return cum[:, min(k, acc.depth) - 1]

    p_full = le(full_to) if spec.card_bands else zeros
    p_card = (le(card_to) - p_full) if spec.card_bands else zeros

    return ProjResult(
        spec=spec, division=division, n_sims=n_sims,
        names=[r["name"] for r in roster],
        pdga_numbers=[r["pdga_number"] for r in roster],
        countries=[countries.get(r["pdga_number"], "") for r in roster],
        ratings=rtg,
        rank_2026=[r["rank"] for r in roster],
        points_2026=[r["points"] for r in roster],
        mean_points=acc.total_pts / n_sims,
        mean_rank=acc.total_rank / n_sims,
        p_first=acc.first / n_sims,
        rank_hist=acc.rank_hist,
        hist_depth=acc.depth,
        att_probs=drawer.att_count / n_sims,
        events_meta=events_meta,
        p_cut=acc.cut / n_sims,
        p_perf=acc.perf / n_sims,
        p_champ=acc.champ / n_sims,
        p_play1=acc.play1 / n_sims,
        p_play2=acc.play2 / n_sims,
        p_cup_win=acc.cup_win / n_sims,
        strokes_hist=acc.strokes,
        stroke_values=post.stroke_values if post else (),
        p_full=p_full, p_card=p_card,
    )


# ------------------------------------------------------------------ export

def export(res: ProjResult) -> None:
    """Write the bundle the experimental tabs read."""
    spec = res.spec
    n_sims = res.n_sims
    hist = res.rank_hist / n_sims
    strokes = res.strokes_hist / n_sims if len(res.stroke_values) else None
    cards = season2027.ET_CARDS.get(res.division, {"full": 0, "card": 0})

    players = []
    for i in range(len(res.names)):
        p = {
            "name": res.names[i], "pdga": res.pdga_numbers[i],
            "country": res.countries[i], "rating": int(res.ratings[i]),
            "rank26": res.rank_2026[i], "pts26": res.points_2026[i],
            "mean_pts": round(float(res.mean_points[i]), 1),
            "mean_rank": round(float(res.mean_rank[i]), 1),
            "p_first": round(float(res.p_first[i]), 5),
            "exp_starts": round(float(res.att_probs[:, i].sum()), 1),
            "hist": [round(float(x), 4) for x in hist[i]],
            "att": [round(float(res.att_probs[e, i]), 3) for e in range(len(res.events_meta))],
        }
        if spec.championship:
            p.update({
                "p_champ": round(float(res.p_champ[i]), 5),
                "p_cut": round(float(res.p_cut[i]), 5),
                "p_perf": round(float(res.p_perf[i]), 5),
                "p_play1": round(float(res.p_play1[i]), 5),
                "p_play2": round(float(res.p_play2[i]), 5),
                "p_cup_win": round(float(res.p_cup_win[i]), 5),
                "strokes": [round(float(x), 4) for x in strokes[i]],
            })
        if spec.card_bands:
            p.update({
                "p_full": round(float(res.p_full[i]), 5),
                "p_card": round(float(res.p_card[i]), 5),
                "p_any": round(float(res.p_full[i] + res.p_card[i]), 5),
            })
        players.append(p)

    key = "p_champ" if spec.championship else "p_any" if spec.card_bands else "mean_pts"
    players.sort(key=lambda q: (-q.get(key, 0), -q["mean_pts"]))
    keep = players[:EXPORT_LIMIT]
    if spec.championship:
        seen = {q["pdga"] for q in keep}
        keep += [q for q in players[EXPORT_LIMIT:] if q["p_champ"] >= 0.0005 and q["pdga"] not in seen]

    meta = {
        "tour": spec.key,
        "label": spec.label,
        "division": res.division,
        "season": season2027.SEASON,
        "base_season": config.SEASON,
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "n_sims": n_sims,
        "max_hist_rank": res.hist_depth,
        "roster_size": len(res.names),
        "shown": len(keep),
        "notes": list(spec.notes),
        "pools": [{"name": p.name, "keep": p.keep, "classes": list(p.classes)}
                  for p in spec.pools],
        "rating_pts_per_stroke": simulate.RATING_PTS_PER_STROKE[res.division],
        "round_sd": simulate.ROUND_SD,
    }
    if spec.championship:
        meta.update({
            "cut": season2027.STANDINGS_CUT[res.division],
            "field_size": season2027.FIELD_SIZE[res.division],
            "perf_spots": season2027.FIELD_SIZE[res.division] - season2027.STANDINGS_CUT[res.division],
            "playoff1": spec.playoff1, "playoff2": spec.playoff2,
            "championship": spec.championship,
            "play1_cut": season2027.PLAYOFF_QUAL["playoff1"]["cut"][res.division],
            "play1_fill": season2027.PLAYOFF_QUAL["playoff1"]["fill"][res.division],
            "play2_cut": season2027.PLAYOFF_QUAL["playoff2"]["cut"][res.division],
            "play2_perf": season2027.PLAYOFF_QUAL["playoff2"]["perf"][res.division],
            "cup_rounds": season2027.CUP_ROUNDS,
            "start_strokes": {
                "values": list(res.stroke_values),
                "bands": [[rank, adv] for rank, adv in config.CUP_START_STROKES[res.division]],
            },
        })
    if spec.card_bands:
        meta.update({
            "cut": cards["full"] + cards["card"],   # what the sparkline greens
            "full_cards": cards["full"],
            "cards": cards["card"],
            "et_count": season2027.ET_COUNT,
            "multipliers": season2027.ET_MULTIPLIERS,
        })

    bundle = {
        "meta": meta,
        # `ei` indexes a player's `att` array. The Cup has no entry there — it
        # is not drawn as a points event — so it carries a null instead, and
        # the page reads attendance off `ei` rather than off row order.
        "schedule": [
            {**m, "ei": i, "counts": bool(m["pool"])}
            for i, m in enumerate(res.events_meta)
        ] + [
            {"id": e.event_id, "name": e.name, "short": e.short_name, "loc": e.location,
             "cls": e.cls, "tour": e.tour, "start": e.start_date, "end": e.end_date,
             "rounds": e.rounds, "field_size": 0.0, "field_avg_rating": 0.0,
             "pool": "", "ei": None, "counts": False}
            for e in season2027.load()
            if e.cls == "championship" and e.on(spec.tour) and e.plays(res.division)
        ],
        "players": keep,
    }
    DOCS_DATA.mkdir(parents=True, exist_ok=True)
    out = DOCS_DATA / f"{spec.key}_{res.division.lower()}.json"
    out.write_text(json.dumps(bundle, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size // 1024} KB)")
