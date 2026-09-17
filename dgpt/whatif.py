"""The drop-in kit: what the 2027 what-if tab needs to add a player to the field.

The site already has one what-if — `docs/js/sim.js`, which replays the 2026
cutline for a player who exists. This is the other question, and it is only
askable about a season nobody has played: *drop a rating into the 2027 field,
send it to some number of events, and where does it come out?*

Answering that in the browser needs two things the projection bundle does not
otherwise carry:

  the field's own ladder   the season totals a hypothetical player is ranked
                           against, per simulated season and per rank
  the field's shape        who is standing on each tee, and what a place is
                           worth there

Both are summaries, not raw simulation output. The whole kit is about 25 KB
against a 155 KB bundle, which is what makes the tab a fetch rather than a
service.

## The ladder, and why quantiles are enough

The honest object is a (n_sims x n_ranks) matrix: in each simulated season,
the total held by each standings position. At 20,000 seasons that is far too
big to ship, so what goes out is its *marginal* quantiles — for each rank, the
distribution of the total sitting at it.

The client reconstructs a season by drawing one uniform and reading every rank
off at that quantile. That is a comonotone approximation: it ties rank 1's
season to rank 50's more tightly than the simulation does. It is also exactly
right for every number the tab prints, because each one is a statement about a
single rank —

    P(rank <= r)  ==  P(my total > the r-th best total)

— and the r-th best total keeps its true marginal distribution under that
construction. What the approximation loses is joint structure across ranks,
which nothing here reads. Quantiles also preserve the ladder's ordering: if
L_r >= L_(r+1) in every season, the same holds at every quantile, so the
reconstructed ladder never crosses itself.

Ranks are shipped one at a time down to the histogram's depth and then
log-spaced to the bottom of the roster, so "you finish about 180th" stays a
sentence the tab can say without shipping 689 rungs.

## The performance paths

Two Cup doors are not thresholds at all: the top finishers at the first
playoff event advance to the second, and the top finishers at the second take
the Cup places the standings cut did not. Whether a given finish walks through
depends on how many already-qualified players finished ahead of it — a fact
about the whole field that a client-side draw cannot reconstruct.

So it is measured instead, where it actually happened: P(advances | finished
here, needed it), counted over every simulated season. The client draws a
place and reads the odds off.

## What stays approximate

The field is frozen. A player dropped into it takes places off everyone else,
which would move the ladder they are measured against, and the kit is built
from seasons they were not in. That is the same trade `sim.js` makes and the
same reason: correcting it needs a re-simulation of the field, which is not a
thing a slider can do between frames. The tab says so above the numbers.
"""
from __future__ import annotations

import numpy as np

# Quantile grid for every shipped distribution: 0, 1/50, ... 1. The client
# draws a uniform and interpolates, so the endpoints are the observed min and
# max and everything between is a 2%-wide band — finer than the tab's own
# display precision, which rounds above a tenth of a point.
LADDER_Q = 51

# Rating bands per event field. Twenty equal-weight bins describe a bimodal
# field well enough to rank against and cost 20 numbers; a single mean and
# standard deviation cost two and are exactly what `sim.js` had to stop using
# when an open field broke them (see its `drawPlace`).
BAND_N = 20

# Places covered by each class's exported points curve. Has to outrun the
# deepest 2027 field (the first playoff event fills to 120) for the same
# reason `export.CURVE_DEPTH` does: the client reads a drawn place straight
# off it and scores anything past the end as zero.
CURVE_DEPTH = 200


def ladder_ranks(depth: int, n: int, growth: float = 1.15) -> list[int]:
    """Standings positions the ladder is shipped at.

    Every rank through `depth` — that is the finishing histogram's own depth,
    so the tab can draw the same sparkline every other row on the page draws —
    and then geometrically to the bottom of the roster, which is what keeps
    "you finish about 180th" answerable without shipping 689 rungs.
    """
    top = min(depth, n)
    ranks = list(range(1, top + 1))
    r = top
    while r < n:
        r = min(n, max(r + 1, int(round(r * growth))))
        ranks.append(r)
    return ranks


def field_bands(ratings: np.ndarray, attendance: np.ndarray,
                nbins: int = BAND_N) -> list[float]:
    """The projected field of one event, as `nbins` equal-weight ratings.

    Attendance-weighted quantiles of the roster's ratings at the midpoint of
    each bin, so the list reads as "one nbins-th of the field is about this
    good" — which is how the client uses it, as a discrete opponent pool.
    """
    w = np.clip(np.asarray(attendance, dtype=float), 0.0, None)
    total = w.sum()
    if total <= 0:
        return []
    order = np.argsort(ratings)
    r, cw = np.asarray(ratings)[order], np.cumsum(w[order])
    targets = (np.arange(nbins) + 0.5) / nbins * total
    ix = np.clip(np.searchsorted(cw, targets), 0, r.size - 1)
    return [round(float(x), 1) for x in r[ix]]


def _quantiles(samples: np.ndarray) -> list[float]:
    """The shipped quantile grid of one series, rounded to a tenth of a point."""
    qs = np.quantile(samples, np.linspace(0.0, 1.0, LADDER_Q))
    return [round(float(x), 1) for x in qs]


class Kit:
    """Accumulates the drop-in summary alongside a projection run.

    One instance per projected season; `project._one_chunk` feeds it at the
    four points where the standings are ranked and the two where a performance
    path is decided. It holds one float32 row per simulated season per stream
    — about 6 MB at 20,000 seasons — and collapses to quantiles at the end.
    """

    def __init__(self, ranks: list[int], field_size: int,
                 perf_depth: tuple[int, int]) -> None:
        self.ranks = np.array(ranks, dtype=np.int64)
        # `ladder_ranks` ends at the roster, so the last rung is its size. Every
        # depth below is clamped to it, because the qualification constants are
        # written for a full tour and a division can be smaller than its own
        # Cup field — FPO's performance path already admits more players than
        # `tests/test_small_division.py` puts in the table.
        n = int(self.ranks[-1])
        self.field_size = min(field_size, n)
        self.play1_depth, self.play2_depth = (min(d, n) for d in perf_depth)
        self._ladder: list[np.ndarray] = []
        self._gates: dict[str, list[np.ndarray]] = {"play1": [], "play2": []}
        self._num = {"play2": np.zeros(self.play1_depth + 1),
                     "champ": np.zeros(self.play2_depth + 1)}
        self._den = {"play2": np.zeros(self.play1_depth + 1),
                     "champ": np.zeros(self.play2_depth + 1)}
        self._seed_rating = np.zeros(self.field_size)
        self._rows = 0

    # -- the three ranked moments in a simulated season ------------------

    def gate(self, which: str, totals: np.ndarray, rank: int) -> None:
        """The total sitting at `rank` — the line a playoff field is cut on."""
        k = min(rank, totals.shape[1]) - 1
        v = -np.partition(-totals, k, axis=1)[:, k]
        self._gates[which].append(v.astype(np.float32))

    def final(self, totals: np.ndarray, ratings: np.ndarray) -> None:
        """The finished standings: the ladder, and who is holding each seed."""
        order = np.argsort(-totals, axis=1)
        srt = np.take_along_axis(totals, order, axis=1)
        self._ladder.append(srt[:, self.ranks - 1].astype(np.float32))
        self._seed_rating += ratings[order[:, : self.field_size]].sum(axis=0)
        self._rows += totals.shape[0]

    # -- the two performance paths ---------------------------------------

    def perf(self, which: str, place: np.ndarray, eligible: np.ndarray,
             gained: np.ndarray) -> None:
        """Count a performance path by the finishing place that walked it."""
        depth = self._num[which].size - 1
        pl = np.clip(place, 1, depth)
        self._num[which] += np.bincount(pl[gained].ravel(), minlength=depth + 1)
        self._den[which] += np.bincount(pl[eligible].ravel(), minlength=depth + 1)

    def _perf_rates(self, which: str) -> list[float]:
        """P(walks the path | finished here), index 0 = first place.

        An empty bin is a place nobody who needed the path ever finished in,
        not evidence that finishing there stopped counting — so it carries a
        neighbour rather than a zero. The rate falls monotonically with place,
        so a gap in the middle takes the place above it and a gap at the top
        takes the best rate that was measured.

        The top of this curve is always a gap, and for a reason worth naming:
        winning the first playoff event banks enough to clear the second one's
        points cut outright, so a winner is never someone the performance path
        has to carry. Reading that as "a win is worth nothing" would be the
        one place a zero-fill could actually mislead the tab.
        """
        num, den = self._num[which], self._den[which]
        rates = []
        last = None
        for p in range(1, num.size):
            if den[p] > 0:
                last = num[p] / den[p]
            rates.append(last)
        head = next((r for r in rates if r is not None), 0.0)
        return [round(float(head if r is None else r), 4) for r in rates]

    # -- what goes in the bundle -----------------------------------------

    def build(self, curves: dict[str, list[float]], stroke_by_rank: list[int],
              invite: tuple[str, ...]) -> dict:
        """The `meta.whatif` block, whole.

        `curves`, `stroke_by_rank` and `invite` come from the caller because
        they are the projection's own points, seed and invitation tables —
        this module summarises a run, it does not get a second opinion about
        what a place is worth or which wins open a door.
        """
        ladder = np.concatenate(self._ladder, axis=0)
        return {
            "q": LADDER_Q,
            "ranks": [int(r) for r in self.ranks],
            # per rank, the quantiles of the season total sitting at it
            "ladder": [_quantiles(ladder[:, j]) for j in range(self.ranks.size)],
            # the lines the two playoff fields are cut on, same grid
            "play1_line": _quantiles(np.concatenate(self._gates["play1"])),
            "play2_line": _quantiles(np.concatenate(self._gates["play2"])),
            # P(advances) by finishing place, index 0 = first
            "play2_perf": self._perf_rates("play2"),
            "champ_perf": self._perf_rates("champ"),
            # mean rating of whoever finishes at each standings position, for
            # the Cup field a dropped-in player would tee off against
            "cup_field": [round(float(x), 1) for x in self._seed_rating / max(self._rows, 1)],
            "cup_strokes": list(stroke_by_rank),
            # classes whose winner takes the Cup's special invite
            "invite": list(invite),
            "curves": curves,
            "bands": BAND_N,
        }
