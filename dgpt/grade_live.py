"""Grade the live-event projections against what actually happened.

`dgpt.evaluate` scores the season-long qualification odds, which resolve once a
year. This grades the *other* forecast the site publishes — the per-player
finish distribution for an event in progress (`simulate._live_projections`:
win %, mean place, p10/p50/p90) — which resolves every weekend. Every completed
event is ~80-160 players x a full predicted distribution, so the cached rounds
already hold thousands of resolved forecasts. This replays them.

    python -m dgpt.grade_live                    # every completed event
    python -m dgpt.grade_live --variant both     # current model vs the form model
    python -m dgpt.grade_live --csv out.csv      # per-forecast rows

How a replay works. For a completed event we hold every round sheet, so the
state the live model saw at each round boundary can be rebuilt exactly: sum
`RoundtoPar` over rounds 1..k for `cur`, and the event's real round count minus
k for `rem`. Feeding that through the same projection math as
`simulate._draw_singles`'s live branch reproduces the numbers the site served
at that moment, and the final sheet says who was right. Checkpoint k = 0 is
the pre-event projection (the from-scratch draw used for every future event),
so the same run grades both the live model and the event model.

What it reports:

- Calibration of win / top-5 / top-10 probabilities, plus Brier and log loss.
- **Skill scores against two baselines**, which is the point of the exercise:
  a Brier score alone says nothing. `score_only` drops the rating term
  (project everyone at field-average pace from here); `rating_only` drops the
  score so far (the pre-event projection). Beating both is the evidence that
  the two inputs each earn their place; failing to beat one localises the
  problem.
- PIT on the finishing place: uniform means the predicted spread is right,
  peaked means it is too wide, U-shaped too narrow.
- A **standing-at-checkpoint table**: predicted vs observed win rate for the
  leader, the chase, and the pack, split by rounds left. A model that never
  updates on form should show up here as underrating whoever is ahead.

Ties. Sim places are strictly ordered, so an actual tie has no exact analogue.
Every threshold outcome gets fractional credit — the share of the tie block's
positions that clear the bar (a 3-way T5 is 1/3 of a top-5) — and the PIT uses
the block's mid-point. One rule, unbiased, applied everywhere.

Excluded from grading, and reported rather than silently dropped: events with a
partial-field round (USWDGC's finals sheet carries 44 of 77), where `rem` is
not the same for everyone and the production model's whole-field assumption is
itself wrong; and players without a final place (withdrawals), who are dropped
from the field as well as the grading, so the simulated and actual place
denominators match.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

from . import config, live_api, schedule, simulate

LIVE_CACHE = config.CACHE_DIR / "live"

# A round every player is expected to be in. Below this share of the biggest
# sheet, the event plays a restricted final (finals qualifiers only) and the
# whole-field `rem` the model assumes does not hold — see the module docstring.
FULL_FIELD_SHARE = 0.8

TOPK = (5, 10)          # threshold outcomes graded alongside the win
DEFAULT_SIMS = 10_000
CHUNK = 2_000

# Per-round SD and within-event round-to-round correlation, used by the `form`
# variant. Defaults are only a fallback: `fit_round_structure` measures both
# from the same replayed rounds and the CLI prefers the measurement.
SD_ROUND = 3.65
RHO = 0.16


# ------------------------------------------------------------------ payloads

@dataclass
class Replay:
    """Every round sheet for one completed event-division, plus its plan."""
    tid: int
    division: str
    name: str
    rounds: list[int]                  # sheet ids the field plays, in order
    sheets: dict[int, list[dict]]      # sheet id -> score rows
    partial_round: int | None = None   # a round not everyone plays, if any


def _envelope(path) -> dict:
    """A cached live-API response body. Disk cache and fixtures both keep the
    `{"data": ...}` envelope the API returns; every reader unwraps it."""
    return json.loads(path.read_text(encoding="utf-8")).get("data") or {}


def _cached_rounds(payload_dir) -> dict[tuple[int, str], dict[int, list[dict]]]:
    """{(tid, division): {round: scores}} from a directory of round payloads."""
    out: dict[tuple[int, str], dict[int, list[dict]]] = defaultdict(dict)
    for p in sorted(payload_dir.glob("round_*.json")):
        m = re.match(r"round_(\d+)_(MPO|FPO)_(\d+)\.json", p.name)
        if not m:
            continue
        scores = _envelope(p).get("scores") or []
        if scores:
            out[(int(m.group(1)), m.group(2))][int(m.group(3))] = scores
    return out


def _event_payload(tid: int, payload_dir, offline: bool) -> dict | None:
    """The event body, from disk if it is there and the API if it is not.

    The round plan cannot be inferred from the sheets alone: Ledgestone's
    fourth round is sheet id 12 ("Finals") and USWDGC's id 13 is a sudden-death
    playoff that is not a round at all (see `live_api._round_plan`). Guessing
    from the ids present gets both wrong in opposite directions, so an event
    whose payload we cannot obtain is skipped rather than replayed on a guess.
    """
    cached = payload_dir / f"event_{tid}.json"
    if cached.exists():
        return _envelope(cached)
    if offline:
        return None
    try:  # completed events never change, so cache=True is safe and permanent
        return live_api.fetch_event(tid, cache=True)
    except Exception:
        return None


def _posted(rows: list[dict]) -> set[int]:
    """Players with a real score on a sheet (not a tee time, not a withdrawal)."""
    return {
        r["PDGANum"] for r in rows
        if r.get("PDGANum") and str(r.get("GrandTotal")) != "999"
        and (r.get("HasRoundScore") or (r.get("Played") or 0) > 0)
    }


def collect_replays(payload_dir, offline: bool = False,
                    only_tids: set[int] | None = None) -> tuple[list[Replay], list[str]]:
    """Replayable event-divisions from the cache, plus why the rest were skipped."""
    sched = {r["tournament_id"]: r for r in schedule.load()}
    by_event = _cached_rounds(payload_dir)
    replays: list[Replay] = []
    skipped: list[str] = []

    for (tid, div), sheets in sorted(by_event.items()):
        row = sched.get(tid)
        label = f"{tid} {div}"
        if only_tids and tid not in only_tids:
            continue
        if not row:
            skipped.append(f"{label}: not in the schedule")
            continue
        label = f"{row['name'][:34]} ({div})"
        if not row["completed"]:
            skipped.append(f"{label}: not completed")
            continue
        # Doubles is a team event with its own score model; the Cup awards no
        # points and is never simulated as a field.
        if row["cls"] in ("doubles", "championship") or not row[div.lower()]:
            skipped.append(f"{label}: {row['cls']} event, not graded")
            continue
        event = _event_payload(tid, payload_dir, offline)
        if not event:
            skipped.append(f"{label}: no event payload (round plan unknown)")
            continue

        ids, total = live_api._round_plan(event, max(sheets))
        rounds = [i for i in ids[:total] if i in sheets]
        if len(rounds) < 2:
            skipped.append(f"{label}: only {len(rounds)} round sheet(s) cached")
            continue
        if len(rounds) != total:
            skipped.append(f"{label}: {len(rounds)} of {total} round sheets cached")
            continue

        counts = {r: len(_posted(sheets[r])) for r in rounds}
        biggest = max(counts.values())
        partial = next((r for r, c in counts.items() if c < FULL_FIELD_SHARE * biggest), None)
        replays.append(Replay(tid, div, row["name"], rounds,
                              {r: sheets[r] for r in rounds}, partial))
    return replays, skipped


# --------------------------------------------------------------- checkpoints

@dataclass
class Checkpoint:
    """The state the live model saw at one round boundary, and the outcome."""
    tid: int
    division: str
    name: str
    done: int                  # rounds completed (0 = pre-event)
    rem: float                 # rounds left
    pdga: np.ndarray
    rating: np.ndarray
    cur: np.ndarray            # to-par through `done` rounds
    standing: np.ndarray       # competition rank on `cur` at the checkpoint
    actual_lo: np.ndarray      # first position of the player's final tie block
    actual_hi: np.ndarray      # last position of it (== lo when untied)

    # actual_lo/hi are re-ranked *within this checkpoint's field*, not the raw
    # finishing places. The two differ whenever a finisher is missing from the
    # field — no rating on the round-1 sheet, or no score in one of the played
    # rounds — and if the raw places were kept, they would not be a permutation
    # of 1..n while the simulated places always are. Comparing the two then
    # biases the PIT and every threshold outcome for everyone below the gap.

    @property
    def n(self) -> int:
        return len(self.pdga)


def _competition_rank(values: np.ndarray) -> np.ndarray:
    """1-based rank of each value ascending; ties share the lowest rank."""
    order = np.argsort(values, kind="stable")
    ranks = np.empty(len(values), dtype=np.int64)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        ranks[order[i : j + 1]] = i + 1
        i = j + 1
    return ranks


def _final_places(rows: list[dict]) -> dict[int, tuple[int, int]]:
    """{pdga: (first, last)} positions of each finisher's tie block.

    `RunningPlace` on the final sheet is the finishing place with ties shared
    (2, 2, 4), so a block's span is recoverable from how many players report
    the same place. Withdrawals keep a RunningPlace in the live data but are
    not finishers, and are excluded here exactly as `final_results` does.
    """
    places: dict[int, int] = {}
    for r in rows:
        if not r.get("RunningPlace") or not r.get("PDGANum"):
            continue
        if str(r.get("GrandTotal")) == "999" or not r.get("HasRoundScore"):
            continue
        places[r["PDGANum"]] = int(r["RunningPlace"])
    sizes = defaultdict(int)
    for p in places.values():
        sizes[p] += 1
    return {pdga: (pl, pl + sizes[pl] - 1) for pdga, pl in places.items()}


def _cur_through(rep: Replay, upto: int) -> dict[int, float]:
    """Each player's to-par after the first `upto` rounds of the plan.

    Sums per-round `RoundtoPar`, never the sheet's `ToPar` total: at a
    multi-layout event each sheet reports `ToPar` against its own round's par,
    so the same player reads differently on each sheet and the totals are not
    comparable (`live_api.live_field` carries the full account). A player
    missing a score in any round of the window is left out — they were not in
    the field the model would have projected forward from.
    """
    running: dict[int, float] = {}
    for rnd in rep.rounds[:upto]:
        posted = _posted(rep.sheets[rnd])
        by_pdga = {r["PDGANum"]: r for r in rep.sheets[rnd] if r.get("PDGANum")}
        for pdga in list(running):
            if pdga not in posted:
                running.pop(pdga, None)
        for pdga in posted:
            rtp = by_pdga[pdga].get("RoundtoPar")
            if rtp is None:
                rtp = by_pdga[pdga].get("ToPar")
            if rtp is None:
                running.pop(pdga, None)
                continue
            if rnd == rep.rounds[0]:
                running[pdga] = float(rtp)
            elif pdga in running:
                running[pdga] += float(rtp)
    return running


def checkpoints(rep: Replay) -> list[Checkpoint]:
    """One checkpoint per round boundary, k = 0 (pre-event) .. n_rounds - 1."""
    total = len(rep.rounds)
    finals = _final_places(rep.sheets[rep.rounds[-1]])
    # Ratings as held going into the event — the number the model had, with no
    # hindsight from a mid-season PDGA update.
    ratings = {
        r["PDGANum"]: float(r["Rating"])
        for r in rep.sheets[rep.rounds[0]]
        if r.get("PDGANum") and r.get("Rating")
    }

    out: list[Checkpoint] = []
    for done in range(total):
        cur = _cur_through(rep, done) if done else {}
        pool = sorted(
            p for p in (cur if done else _posted(rep.sheets[rep.rounds[0]]))
            if p in finals and p in ratings
        )
        if len(pool) < 20:
            continue
        vals = np.array([cur.get(p, 0.0) for p in pool])
        # Re-rank the finish within this field so the actual places are a
        # permutation of 1..n, as the simulated ones are. Players tied in the
        # real result stay tied.
        raw = np.array([float(finals[p][0]) for p in pool])
        lo = _competition_rank(raw)
        block = np.array([int((raw == r).sum()) for r in raw])
        out.append(Checkpoint(
            tid=rep.tid, division=rep.division, name=rep.name, done=done,
            rem=float(total - done),
            pdga=np.array(pool),
            rating=np.array([ratings[p] for p in pool]),
            cur=vals,
            # Pre-event there is no leaderboard: every `cur` is 0, and ranking
            # them would file the whole field under "leader". 0 means "no
            # standing", and the standing table skips those rows.
            standing=_competition_rank(vals) if done else np.zeros(len(pool), dtype=np.int64),
            actual_lo=lo,
            actual_hi=lo + block - 1,
        ))
    return out


# --------------------------------------------------------------- projections

def project(cp: Checkpoint, variant: str, model: str, rng: np.random.Generator,
            n_sims: int, sd_round: float, rho: float) -> np.ndarray:
    """Place histogram, shape (n, n): counts of each finishing position.

    `model` selects which inputs are live — "full", "score_only" (drop the
    rating term), "rating_only" (drop the score so far). `variant` selects the
    noise model: "current" is the published one, `ROUND_SD * sqrt(rem)` around
    a mean that ignores how the player has scored so far; "form" splits the
    round SD into a per-event form component and per-round noise, then
    conditions both the mean and the variance on the rounds already played.
    """
    rpps = simulate.RATING_PTS_PER_STROKE[cp.division]
    favg = float(cp.rating.mean())
    pace = -(cp.rating - favg) / rpps          # strokes per round vs the field
    if model == "score_only":
        pace = np.zeros_like(pace)
    base = np.zeros_like(cp.cur) if model == "rating_only" else cp.cur
    rem = cp.rem if model != "rating_only" else float(cp.done) + cp.rem
    done = 0 if model == "rating_only" else cp.done

    if variant == "current":
        mu = base + pace * rem
        sd = np.full(cp.n, simulate.ROUND_SD * math.sqrt(rem))
    else:
        tau2 = rho * sd_round**2          # per-event form variance
        s2 = (1.0 - rho) * sd_round**2    # per-round idiosyncratic variance
        if done:
            # What the played rounds say about today's form, shrunk by how much
            # of the spread form actually explains. resid is per round, so the
            # posterior mean applies to each remaining round alike.
            resid = ((base - base.mean()) - pace * done) / done
            precision = 1.0 / tau2 + done / s2
            form = (done / s2) / precision * resid
            form_var = 1.0 / precision
        else:
            form = np.zeros(cp.n)
            form_var = tau2
        mu = base + (pace + form) * rem
        # form is common to every remaining round (rem^2), round noise is not
        sd = np.full(cp.n, math.sqrt(form_var * rem**2 + s2 * rem))

    n = cp.n
    hist = np.zeros((n, n), dtype=np.int64)
    left = n_sims
    while left > 0:
        c = min(CHUNK, left)
        rows_ix = np.arange(c)[:, None]
        scores = mu[None, :] + rng.normal(0.0, 1.0, (c, n)) * sd[None, :]
        order = np.argsort(scores, axis=1)
        place = np.empty_like(order)
        place[rows_ix, order] = np.arange(n)[None, :]     # 0-based position
        flat = (np.arange(n) * n)[None, :] + place
        hist += np.bincount(flat.ravel(), minlength=n * n).reshape(n, n)
        left -= c
    return hist


# ------------------------------------------------------------------ outcomes

@dataclass
class Forecast:
    """One graded player-checkpoint: predictions and fractional outcomes."""
    tid: int
    division: str
    name: str
    done: int
    rem: float
    pdga: int
    rating: float
    standing: int
    p_win: float
    p_top: dict[int, float]
    y_win: float
    y_top: dict[int, float]
    pit: float
    pred_mean_place: float
    actual_place: int


def grade(cp: Checkpoint, hist: np.ndarray, n_sims: int) -> list[Forecast]:
    cdf = np.cumsum(hist, axis=1) / n_sims          # cdf[i, j] = P(place <= j+1)
    positions = np.arange(1, cp.n + 1)
    mean_place = (hist * positions[None, :]).sum(axis=1) / n_sims
    out: list[Forecast] = []
    for i in range(cp.n):
        lo, hi = int(cp.actual_lo[i]), int(cp.actual_hi[i])
        block = hi - lo + 1
        # Fractional credit: the share of the tie block's positions clearing
        # the bar. Untied, this is the plain indicator.
        y_win = 1.0 / block if lo == 1 else 0.0
        y_top = {
            k: min(max(k - lo + 1, 0), block) / block for k in TOPK
        }
        below = cdf[i, lo - 2] if lo >= 2 else 0.0
        at = cdf[i, min(hi, cp.n) - 1]
        out.append(Forecast(
            tid=cp.tid, division=cp.division, name=cp.name, done=cp.done, rem=cp.rem,
            pdga=int(cp.pdga[i]), rating=float(cp.rating[i]), standing=int(cp.standing[i]),
            p_win=float(cdf[i, 0]),
            p_top={k: float(cdf[i, min(k, cp.n) - 1]) for k in TOPK},
            y_win=y_win, y_top=y_top,
            pit=float((below + at) / 2.0),
            pred_mean_place=float(mean_place[i]),
            actual_place=lo,
        ))
    return out


# ------------------------------------------------------------------- scoring

def brier(pairs: list[tuple[float, float]]) -> float:
    return sum((p - y) ** 2 for p, y in pairs) / len(pairs)


def log_loss(pairs: list[tuple[float, float]], eps: float = 1e-6) -> float:
    total = 0.0
    for p, y in pairs:
        q = min(max(p, eps), 1 - eps)
        total -= y * math.log(q) + (1 - y) * math.log(1 - q)
    return total / len(pairs)


def calibration(pairs: list[tuple[float, float]], edges=(0.0, 0.01, 0.05, 0.1, 0.2, 0.4, 0.7, 1.01)):
    """Observed rate by predicted bucket. Uneven edges on purpose: almost every
    forecast in a 150-player field is near zero, so equal-width bins put
    everything in the first one and show nothing."""
    buckets: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for p, y in pairs:
        b = max(i for i in range(len(edges) - 1) if p >= edges[i])
        buckets[b].append((p, y))
    rows = []
    for b in sorted(buckets):
        vals = buckets[b]
        rows.append((
            f"{edges[b]:.0%}-{min(edges[b + 1], 1.0):.0%}",
            len(vals),
            sum(p for p, _ in vals) / len(vals),
            sum(y for _, y in vals) / len(vals),
        ))
    return rows


def pit_table(values: list[float], bins: int = 10) -> list[tuple[str, float]]:
    counts = [0] * bins
    for v in values:
        counts[min(bins - 1, int(v * bins))] += 1
    return [(f"{i / bins:.0%}-{(i + 1) / bins:.0%}", c / len(values)) for i, c in enumerate(counts)]


# ------------------------------------------------- round structure from data

def _mean_product(groups: list[list[float]]) -> tuple[float, int]:
    """Mean product over all within-group pairs, and how many there were."""
    total, pairs = 0.0, 0
    for vals in groups:
        for a in range(len(vals)):
            for b in range(a + 1, len(vals)):
                total += vals[a] * vals[b]
                pairs += 1
    return (total / pairs if pairs else 0.0), pairs


def fit_round_structure(replays: list[Replay]) -> dict | None:
    """Measure per-round SD and the correlation structure of the residuals.

    The published `ROUND_SD = 4.2` is an event-level constant that folds the
    within-event correlation into the round noise, so neither piece is
    separately visible. Both are directly estimable: strip each round's field
    mean and the rating-implied edge, then correlate the same player's
    residuals across rounds.

    Two correlations, and the difference between them is the interesting part:

    - `rho_within` — same player, same event, different rounds. This is what
      the `form` variant conditions on, and it is the right number for that:
      whatever makes a player beat their rating through two rounds, it is
      still true in round three.
    - `rho_across` — same player, *different* events. Form cannot persist
      across months, so this is the part explained by the rating being a
      stale or noisy estimate of the player rather than by anything about
      today.

    So `rho_within - rho_across` is event-specific form, and `rho_across` on
    its own is a direct measurement of how much skill the current rating is
    failing to capture. A large `rho_across` is an argument for a better skill
    input, not a better noise model.
    """
    resid: dict[tuple[int, str], dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    for rep in replays:
        rpps = simulate.RATING_PTS_PER_STROKE[rep.division]
        for rnd in rep.rounds:
            rows = [
                r for r in rep.sheets[rnd]
                if r.get("PDGANum") and r.get("Rating") and r.get("HasRoundScore")
                and r.get("RoundtoPar") is not None and str(r.get("GrandTotal")) != "999"
            ]
            if len(rows) < 20:
                continue
            rat = np.array([float(r["Rating"]) for r in rows])
            sc = np.array([float(r["RoundtoPar"]) for r in rows])
            # residual around the model as deployed: the rating-implied edge
            # uses the shipped constant, not a slope refitted here, so the
            # spread measured is the spread the live model actually faces
            e = (sc - sc.mean()) + (rat - rat.mean()) / rpps
            for r, v in zip(rows, e):
                resid[(rep.division, r["PDGANum"])][rep.tid].append(float(v))

    flat = [v for by_event in resid.values() for vals in by_event.values() for v in vals]
    if len(flat) < 100:
        return None
    var = float(np.var(flat, ddof=1))
    if var <= 0:
        return None

    within, n_within = _mean_product(
        [vals for by_event in resid.values() for vals in by_event.values()]
    )
    # across-event pairs: one player's mean residual per event, paired between
    # events. Using per-event means rather than raw rounds keeps a player with
    # many starts from dominating, and averages out the round noise.
    across, n_across = _mean_product([
        [float(np.mean(vals)) for vals in by_event.values()]
        for by_event in resid.values() if len(by_event) > 1
    ])
    return {
        "sd_round": math.sqrt(var),
        "rho_within": within / var,
        "rho_across": (across / var) if n_across else None,
        "n_obs": len(flat), "n_within": n_within, "n_across": n_across,
        "n_events": len({tid for by_event in resid.values() for tid in by_event}),
    }


# -------------------------------------------------------------------- report

def _pairs(fcs: list[Forecast], outcome: str, k: int | None = None):
    if outcome == "win":
        return [(f.p_win, f.y_win) for f in fcs]
    return [(f.p_top[k], f.y_top[k]) for f in fcs]


def _skill(model: list[Forecast], base: list[Forecast], outcome: str, k: int | None) -> float:
    bm, bb = brier(_pairs(model, outcome, k)), brier(_pairs(base, outcome, k))
    return 1.0 - bm / bb if bb else float("nan")


STANDING_BANDS = (("leader", 1, 1), ("2nd-3rd", 2, 3), ("4th-10th", 4, 10),
                  ("11th-25th", 11, 25), ("26th+", 26, 10**6))


def print_report(runs: dict[str, list[Forecast]], variant: str) -> None:
    model = runs["full"]
    events = len({(f.tid, f.division) for f in model})
    print(f"\n{'=' * 74}\n{variant.upper()} MODEL — {len(model)} graded forecasts "
          f"from {events} event-division{'s' if events != 1 else ''}\n{'=' * 74}")
    if events < 8:
        print("  NOTE: the win metrics resolve one winner per event-division, so with\n"
              "  this few events they are dominated by sampling noise. The top-5 /\n"
              "  top-10 columns and the PIT carry far more information at this size.")

    print(f"\n{'outcome':<10}{'brier':>10}{'log loss':>11}"
          f"{'skill vs score-only':>21}{'skill vs rating-only':>22}")
    for k in (None,) + TOPK:
        outcome = "win" if k is None else "top"
        name = "win" if k is None else f"top-{k}"
        print(f"{name:<10}{brier(_pairs(model, outcome, k)):>10.5f}"
              f"{log_loss(_pairs(model, outcome, k)):>11.5f}"
              f"{_skill(model, runs['score_only'], outcome, k):>20.1%}"
              f"{_skill(model, runs['rating_only'], outcome, k):>21.1%}")
    print("  (skill > 0 means the model beats that baseline; < 0 means it is worse)")

    print("\nWIN-PROBABILITY CALIBRATION")
    print(f"  {'predicted':<12}{'n':>7}{'mean pred':>12}{'observed':>11}")
    for label, n, pred, obs in calibration(_pairs(model, "win")):
        print(f"  {label:<12}{n:>7}{pred:>12.1%}{obs:>11.1%}")

    print("\nPIT ON FINISHING PLACE (flat ~10% per bucket; "
          "peaked = spread too wide, U = too narrow)")
    cells = "  ".join(f"{frac:.0%}" for _, frac in pit_table([f.pit for f in model]))
    print(f"  {cells}")

    print("\nWIN RATE BY STANDING AT THE CHECKPOINT (pre-event checkpoints excluded)")
    print(f"  {'rounds left':<13}{'standing':<11}{'n':>6}{'predicted':>11}{'observed':>11}{'obs/pred':>10}")
    live = [f for f in model if f.done]
    shown = 0
    for rem in sorted({f.rem for f in live}):
        for label, lo, hi in STANDING_BANDS:
            sub = [f for f in live if f.rem == rem and lo <= f.standing <= hi]
            if len(sub) < 10:
                continue
            pred = sum(f.p_win for f in sub) / len(sub)
            obs = sum(f.y_win for f in sub) / len(sub)
            ratio = obs / pred if pred > 1e-9 else float("nan")
            shown += 1
            print(f"  {rem:<13.0f}{label:<11}{len(sub):>6}{pred:>11.1%}{obs:>11.1%}{ratio:>10.2f}")
    if not shown:
        print("  (no band reached 10 forecasts — needs more events)")
    print("  (obs/pred above 1.00 in the leader rows means whoever is ahead wins more\n"
          "   often than the model says, which is what form-blindness looks like)")


def write_csv(path: str, runs_by_variant: dict[str, dict[str, list[Forecast]]]) -> None:
    cols = ["variant", "model", "tid", "event", "division", "rounds_done", "rem",
            "pdga_number", "rating", "standing", "p_win", "y_win",
            *[f"p_top{k}" for k in TOPK], *[f"y_top{k}" for k in TOPK],
            "pit", "pred_mean_place", "actual_place"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for variant, runs in runs_by_variant.items():
            for model_name, fcs in runs.items():
                for x in fcs:
                    w.writerow([
                        variant, model_name, x.tid, x.name, x.division, x.done, x.rem,
                        x.pdga, x.rating, x.standing, round(x.p_win, 5), round(x.y_win, 4),
                        *[round(x.p_top[k], 5) for k in TOPK],
                        *[round(x.y_top[k], 4) for k in TOPK],
                        round(x.pit, 5), round(x.pred_mean_place, 2), x.actual_place,
                    ])
    print(f"\nwrote {path}")


# ----------------------------------------------------------------------- CLI

def run(payload_dir=None, *, variant: str = "current", n_sims: int = DEFAULT_SIMS,
        seed: int = 7, offline: bool = False, only_tids: set[int] | None = None,
        division: str | None = None, sd_round: float | None = None,
        rho: float | None = None, quiet: bool = False):
    """Replay every cached completed event and grade the projections."""
    payload_dir = payload_dir or LIVE_CACHE
    replays, skipped = collect_replays(payload_dir, offline, only_tids)
    if division:
        replays = [r for r in replays if r.division == division]

    graded = [r for r in replays if not r.partial_round]
    for r in replays:
        if r.partial_round:
            skipped.append(f"{r.name[:34]} ({r.division}): round {r.partial_round} "
                           "is not played by the full field")

    if not quiet:
        print(f"replayable event-divisions: {len(graded)}")
        for s in skipped:
            print(f"  skipped  {s}")
    if not graded:
        return {}, None

    fit = fit_round_structure(graded)
    if fit and not quiet:
        # ROUND_SD is quoted per round but applied as ROUND_SD*sqrt(rounds), so
        # the like-for-like comparison is the event-level SD each implies.
        measured = fit["sd_round"] * math.sqrt(3 + 6 * fit["rho_within"])
        print(f"\nround structure from {fit['n_obs']} player-rounds over "
              f"{fit['n_events']} event{'s' if fit['n_events'] != 1 else ''}:")
        print(f"  per-round SD          {fit['sd_round']:.2f}")
        print(f"  rho within an event   {fit['rho_within']:.3f}   "
              f"({fit['n_within']} same-event round pairs)")
        if fit["rho_across"] is not None:
            print(f"  rho across events     {fit['rho_across']:.3f}   "
                  f"({fit['n_across']} cross-event pairs) — the part that is a stale")
            print(f"  {'':22}rating rather than today's form")
        print(f"  -> 3-round event SD   {measured:.2f} measured vs "
              f"{simulate.ROUND_SD * math.sqrt(3):.2f} from ROUND_SD={simulate.ROUND_SD}")
    sd_round = sd_round or (fit["sd_round"] if fit else SD_ROUND)
    rho = rho if rho is not None else (fit["rho_within"] if fit else RHO)

    variants = ("current", "form") if variant == "both" else (variant,)
    out: dict[str, dict[str, list[Forecast]]] = {}
    for v in variants:
        runs: dict[str, list[Forecast]] = {"full": [], "score_only": [], "rating_only": []}
        for rep in graded:
            for cp in checkpoints(rep):
                for model_name in runs:
                    rng = np.random.default_rng(seed + cp.tid + cp.done)
                    hist = project(cp, v, model_name, rng, n_sims, sd_round, rho)
                    runs[model_name] += grade(cp, hist, n_sims)
        out[v] = runs
        if not quiet:
            print_report(runs, v)

    if len(out) > 1 and not quiet:
        print(f"\n{'=' * 74}\nVARIANT COMPARISON (Brier, lower is better)\n{'=' * 74}")
        print(f"{'outcome':<10}" + "".join(f"{v:>12}" for v in out) + f"{'form skill':>13}")
        for outcome, k in [("win", None)] + [("top", k) for k in TOPK]:
            name = "win" if k is None else f"top-{k}"
            scores = {v: brier(_pairs(out[v]["full"], outcome, k)) for v in out}
            skill = 1 - scores["form"] / scores["current"] if scores["current"] else float("nan")
            print(f"{name:<10}" + "".join(f"{scores[v]:>12.5f}" for v in out) + f"{skill:>12.1%}")
    return out, fit


def main() -> None:
    ap = argparse.ArgumentParser(description="Grade live-event projections against actual finishes.")
    ap.add_argument("--variant", choices=["current", "form", "both"], default="current",
                    help="noise model: the published one, the form-conditioned one, or both")
    ap.add_argument("--sims", type=int, default=DEFAULT_SIMS)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--division", choices=["MPO", "FPO"])
    ap.add_argument("--event", type=int, action="append", dest="events",
                    help="restrict to a tournament id (repeatable)")
    ap.add_argument("--payloads", help="directory of round payloads (default: the live cache)")
    ap.add_argument("--offline", action="store_true",
                    help="never fetch; skip events whose event payload is not cached")
    ap.add_argument("--sd-round", type=float, help="override the fitted per-round SD")
    ap.add_argument("--rho", type=float, help="override the fitted within-event correlation")
    ap.add_argument("--csv", help="write per-forecast rows here")
    args = ap.parse_args()

    from pathlib import Path

    runs, _ = run(
        Path(args.payloads) if args.payloads else None,
        variant=args.variant, n_sims=args.sims, seed=args.seed, offline=args.offline,
        only_tids=set(args.events) if args.events else None, division=args.division,
        sd_round=args.sd_round, rho=args.rho,
    )
    if args.csv and runs:
        write_csv(args.csv, runs)


if __name__ == "__main__":
    main()
