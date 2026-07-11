"""Recalibrate the score model against actual 2026 rounds.

The simulation's two physics constants are inherited from the 2021 model:
RATING_PTS_PER_STROKE = 6 (how many rating points equal one stroke per round)
and ROUND_SD = 6.82 (per-round noise). This fits both from every completed
2026 round in the local cache and checks the event model's calibration:

    python -m dgpt.calibrate

Within each (event, division, round) we regress score-vs-field-mean on
rating-vs-field-mean: the slope gives strokes per rating point, the residual
spread the true round SD. The PIT check asks whether actual event totals land
uniformly within each player's predicted distribution (too peaked a
histogram = our SD is too wide; U-shaped = too narrow).
"""
from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from pathlib import Path

from . import config, schedule, simulate

LIVE_CACHE = config.CACHE_DIR / "live"


def _round_files():
    for p in sorted(LIVE_CACHE.glob("round_*.json")):
        m = re.match(r"round_(\d+)_(MPO|FPO)_(\d+)\.json", p.name)
        if m:
            yield int(m.group(1)), m.group(2), int(m.group(3)), p


def collect_rounds() -> list[dict]:
    """One record per (event, division, round): paired (rating, score) lists."""
    sched = {r["tournament_id"]: r for r in schedule.load()}
    out = []
    for tid, div, rnum, path in _round_files():
        row = sched.get(tid)
        if not row or not row["completed"] or row["cls"] == "doubles":
            continue
        scores = (json.loads(path.read_text(encoding="utf-8")).get("data") or {}).get("scores") or []
        pairs = [
            (float(s["Rating"]), float(s["RoundScore"]))
            for s in scores
            if s.get("HasRoundScore") and s.get("Rating") and s.get("RoundScore")
            and str(s.get("GrandTotal")) != "999"
        ]
        if len(pairs) >= 20:
            out.append({"tid": tid, "div": div, "round": rnum, "cls": row["cls"], "pairs": pairs})
    return out


def fit(rounds: list[dict]) -> dict:
    """Pooled within-round regression: dscore = b * drating + eps."""
    sxy = sxx = 0.0
    resid: list[float] = []
    n_obs = 0
    for r in rounds:
        ratings = [x[0] for x in r["pairs"]]
        scores = [x[1] for x in r["pairs"]]
        mr, ms = sum(ratings) / len(ratings), sum(scores) / len(scores)
        for rat, sc in r["pairs"]:
            sxy += (rat - mr) * (sc - ms)
            sxx += (rat - mr) ** 2
        n_obs += len(r["pairs"])
    b = sxy / sxx
    for r in rounds:
        ratings = [x[0] for x in r["pairs"]]
        scores = [x[1] for x in r["pairs"]]
        mr, ms = sum(ratings) / len(ratings), sum(scores) / len(scores)
        resid += [(sc - ms) - b * (rat - mr) for rat, sc in r["pairs"]]
    sd = math.sqrt(sum(x * x for x in resid) / (len(resid) - 1))
    return {"slope": b, "rating_pts_per_stroke": -1.0 / b, "round_sd": sd, "n_rounds": len(rounds), "n_obs": n_obs}


def pit(rounds: list[dict], rpps: float, sd: float) -> list[float]:
    """Percentile of each actual round score within its predicted normal."""
    out = []
    for r in rounds:
        ratings = [x[0] for x in r["pairs"]]
        scores = [x[1] for x in r["pairs"]]
        mr, ms = sum(ratings) / len(ratings), sum(scores) / len(scores)
        for rat, sc in r["pairs"]:
            mu = ms - (rat - mr) / rpps
            z = (sc - mu) / sd
            out.append(0.5 * (1 + math.erf(z / math.sqrt(2))))
    return out


def pit_table(values: list[float], bins: int = 10) -> list[tuple[str, float]]:
    counts = [0] * bins
    for v in values:
        counts[min(bins - 1, int(v * bins))] += 1
    return [(f"{i/bins:.0%}-{(i+1)/bins:.0%}", c / len(values)) for i, c in enumerate(counts)]


def residuals_by_rating(rounds: list[dict], nbuckets: int = 6) -> list[dict]:
    """Round-score residual SD in equal-count rating buckets, for one division.

    Uses the division's own within-round slope to strip the rating-expected mean
    (and the field mean, which absorbs course/conditions), then reports the SD of
    what's left within each rating band. If the offseason hunch holds — better
    players are more consistent — SD should fall as the bucket rating rises.
    Equal-count buckets keep every SD estimate on the same footing.
    """
    b = fit(rounds)["slope"]
    obs: list[tuple[float, float]] = []  # (rating, residual)
    for r in rounds:
        ratings = [x[0] for x in r["pairs"]]
        scores = [x[1] for x in r["pairs"]]
        mr, ms = sum(ratings) / len(ratings), sum(scores) / len(scores)
        obs += [(rat, (sc - ms) - b * (rat - mr)) for rat, sc in r["pairs"]]
    obs.sort(key=lambda t: t[0])
    n = len(obs)
    out = []
    for i in range(nbuckets):
        lo, hi = i * n // nbuckets, (i + 1) * n // nbuckets
        chunk = obs[lo:hi]
        if len(chunk) < 2:
            continue
        res = [t[1] for t in chunk]
        m = sum(res) / len(res)
        sd = math.sqrt(sum((x - m) ** 2 for x in res) / (len(res) - 1))
        out.append({
            "lo_rating": chunk[0][0], "hi_rating": chunk[-1][0],
            "n": len(chunk), "sd": sd,
        })
    return out


def print_variance_by_rating(rounds: list[dict]) -> None:
    """Text histogram of round-score SD by rating bucket, per division."""
    print("\nROUND-SCORE SD BY RATING BUCKET "
          "(equal-count buckets; hunch: SD falls as rating rises)")
    for div in ("MPO", "FPO"):
        sub = [r for r in rounds if r["div"] == div]
        if not sub:
            continue
        buckets = residuals_by_rating(sub)
        overall = fit(sub)["round_sd"]
        print(f"\n  {div}  (pooled SD {overall:.2f}, {sum(len(r['pairs']) for r in sub)} player-rounds)")
        hi = max((bk["sd"] for bk in buckets), default=1.0)
        for bk in buckets:
            bar = "█" * round(bk["sd"] / hi * 40)
            print(f"    {bk['lo_rating']:>4.0f}-{bk['hi_rating']:<4.0f}  "
                  f"n={bk['n']:>4}  sd={bk['sd']:5.2f}  {bar}")


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="Recalibrate / probe the score model.")
    ap.add_argument("--by-rating", action="store_true",
                    help="only print the round-SD-by-rating-bucket histogram (MPO + FPO)")
    args = ap.parse_args()

    rounds = collect_rounds()
    divs = sorted({r["div"] for r in rounds})
    print(f"completed 2026 rounds in cache: {len(rounds)} "
          f"({sum(len(r['pairs']) for r in rounds)} player-rounds)\n")

    if args.by_rating:
        print_variance_by_rating(rounds)
        return

    print(f"current constants: RATING_PTS_PER_STROKE={simulate.RATING_PTS_PER_STROKE}  ROUND_SD={simulate.ROUND_SD}")
    overall = fit(rounds)
    print(f"\nOVERALL fit: rating_pts_per_stroke={overall['rating_pts_per_stroke']:.2f}  "
          f"round_sd={overall['round_sd']:.2f}  ({overall['n_rounds']} rounds, {overall['n_obs']} obs)")

    for div in divs:
        f = fit([r for r in rounds if r["div"] == div])
        print(f"  {div}: rpps={f['rating_pts_per_stroke']:.2f}  sd={f['round_sd']:.2f}  ({f['n_rounds']} rounds)")
    for cls in sorted({r["cls"] for r in rounds}):
        f = fit([r for r in rounds if r["cls"] == cls])
        print(f"  {cls:<11}: rpps={f['rating_pts_per_stroke']:.2f}  sd={f['round_sd']:.2f}  ({f['n_rounds']} rounds)")

    cur_rpps = simulate.RATING_PTS_PER_STROKE
    cur_rpps = cur_rpps.get("MPO", 6.0) if isinstance(cur_rpps, dict) else cur_rpps
    print("\nPIT (should be ~10% per bucket; peaked middle = SD too wide, fat ends = too narrow)")
    for label, cur in (("current", (cur_rpps, simulate.ROUND_SD)),
                       ("refit", (overall["rating_pts_per_stroke"], overall["round_sd"]))):
        vals = pit(rounds, *cur)
        cells = " ".join(f"{frac:.0%}" for _, frac in pit_table(vals))
        print(f"  {label:<8} rpps={cur[0]:.2f} sd={cur[1]:.2f}: {cells}")


if __name__ == "__main__":
    main()
