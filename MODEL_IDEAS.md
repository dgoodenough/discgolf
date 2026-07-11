# Model ideas — offseason lever backlog

A running log of score-model / forecast levers to investigate. **Not a
mid-season worklist:** every change here alters published odds, so nothing gets
implemented until the offseason, after the season's predictions are frozen and
gradeable (`python -m dgpt.evaluate`). The point of writing them down now is to
capture the hunch *and the evidence that prompted it* while it's fresh.

Each entry: the hypothesis, why it would move the forecast, how the model treats
it today, how to test it against cached rounds, the confounds to rule out, and a
rough gate for "is it worth the added complexity."

---

## 1. Rating-dependent round-score variance (heteroskedasticity)

**Status:** logged, not scheduled · raised 2026-07 (mid-season hunch)

**Hypothesis.** Round-score standard deviation *decreases* with rating. Players
at every rating level have blow-up/outlier rounds, but the very best play more
consistently, so their round-to-round spread is tighter. I.e. the model's noise
term should shrink as rating rises, not stay flat.

**How the model treats it today.** `ROUND_SD` is a single pooled constant (4.2
event-level, of which ~3.65 is pure per-round noise and the rest within-event
form correlation; see `simulate.py`). It's applied identically to every player:
`score = mu + N(0, ROUND_SD*sqrt(rounds))`, with `mu` the only rating-dependent
term. `calibrate.fit()` estimates one residual SD pooled across all
player-rounds — homoskedastic by construction. So this hunch is exactly the
claim that `ROUND_SD` should become `ROUND_SD(rating)`.

**Why it matters.** SD drives the *tails*, which is where this forecast earns
its keep — win %, podium, and the automatic-bid cutline all live in the tails,
not the mean. If elite players are genuinely more consistent than a flat SD
assumes, the current model is over-dispersing the favorites (giving away too
much of their win probability to the field) and simultaneously handing longshots
too fat a tail. Heins-style "anyone can win a single event" is real, but if it's
weaker for the top tier than we model, elite win odds are understated and field
win odds overstated. Net effect concentrates most on marquee players' event-win
and No.1-seed numbers.

**How to test (cached rounds already have what we need).**
- `calibrate.fit()` already produces per-player-round residuals `(score - field_mean) - b*(rating - field_mean)`. Bin those residuals by rating (e.g. 970/990/1010/1030+ buckets) and compute residual SD per bucket. A clean downward trend across buckets is the first-order confirmation.
- Firmer: regress `log(residual^2)` on `rating` (Breusch–Pagan-style) for a slope + significance, pooled within-event so course/conditions difference out (the regression is already de-meaned within each event-round).
- Cross-check with the existing PIT diagnostic: if the pooled SD is ~right on average but the tails are miscalibrated *asymmetrically by tier* — elite totals landing too near their predicted median (peaked PIT for high-rating), field totals too fat in the ends — that's the signature of a rating-varying SD the single constant can't capture. Worth adding a per-tier PIT table to `calibrate.py` to see this directly.

**Confounds to rule out.**
- *Withdrawals truncate blow-ups.* DNF/999 rounds are dropped from the fit
  (`collect_rounds` filters `GrandTotal==999`), so the worst rounds are missing —
  and they may be missing *unevenly* by tier (a 1000-rated am grinds out a +12;
  a touring pro WDs). That biases estimated SD downward, plausibly more for the
  field than the elite, which could *mask* or *invert* the true effect. Need to
  check WD rates by rating before trusting the raw bucket SDs.
- *Few-rounds noise.* Per-player SD is noisy with few rounds; use rating buckets
  or a shrinkage/hierarchical estimate, not raw per-player variances.
- *No regular-round cut* on the DGPT means low selection bias (everyone plays all
  rounds) — a genuine advantage over trying this in ball golf. Good.
- *Is it rating, or tier?* The effect might be a threshold ("touring elite play
  consistently") rather than smooth in rating. Fit both a linear slope and a
  simple two-tier step; prefer the simpler one that fits.

**Implementation sketch if it holds.** Make the noise term
`sd(rating) = max(floor, a - b*(rating - 1000))` (or a gentle `exp` form),
refit `a, b` in `calibrate.py` alongside `rating_pts_per_stroke`, and swap the
scalar for the function in `simulate.draw_event`'s two score-draw sites. Keep the
event-level correlation structure as-is. Localized change; the risk is
overfitting `b` on one season, so cross-validate across events and gate on PIT.

**Worth-it gate.** Only ship if (a) the slope is material — say, more than
~0.4–0.5 strokes of round-SD across the observed rating range, not a rounding
artifact — and (b) per-tier PIT calibration visibly improves out-of-sample.
If the effect is real but tiny, log it as "confirmed, immaterial" and move on.

**Spin-off threads (from this discussion, not yet fleshed out):**
- Variance may track *course type* (tight wooded vs open bomber courses) or
  weather more than rating — a wooded-course SD multiplier could be the better
  lever, or a confound to control here.
- The truncation point above argues for capturing a per-round data-quality /
  WD-rate summary during refresh, so the offseason fit isn't blind to what's
  missing.

---

## Backlog (unfleshed — one-liners to expand later)

_Add new hunches here as they come up; promote to a full section when we dig in._

- (none yet)
