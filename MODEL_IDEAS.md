# Model ideas — offseason lever backlog

A running log of score-model / forecast levers to investigate. **Not a
mid-season worklist:** every change here alters published odds, so nothing gets
implemented until the offseason, after the season's predictions are frozen and
gradeable (`python -m dgpt.evaluate`). The point of writing them down now is to
capture the hunch *and the evidence that prompted it* while it's fresh.

Each entry: the hypothesis, why it would move the forecast, how the model treats
it today, how to test it against cached rounds, the confounds to rule out, and a
rough gate for "is it worth the added complexity."

**Most of these can now be settled without waiting for October.**
`python -m dgpt.grade_live` replays every completed event from the cached round
sheets, runs the live projection at each round boundary, and grades the predicted
finish distributions against what happened — thousands of resolved forecasts
instead of one season-end verdict, and it scores alternative parameterisations
side by side without touching what the site publishes. Testing a lever there
breaks no freeze: nothing it does reaches `docs/`.

Engineering hardening (pipeline robustness, testing, alerting — changes that
*don't* alter published odds) has its own backlog in [HARDENING.md](HARDENING.md).

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
- First look is already wired up: `python -m dgpt.calibrate --by-rating` prints a
  round-SD-by-rating histogram (equal-count buckets) for MPO and FPO straight
  from the cached rounds. A clean downward trend across buckets is the
  first-order confirmation. (It reuses `calibrate.fit()`'s per-player-round
  residuals `(score - field_mean) - b*(rating - field_mean)`.)
- Firmer: regress `log(residual^2)` on `rating` (Breusch–Pagan-style) for a slope + significance, pooled within-event so course/conditions difference out (the regression is already de-meaned within each event-round).
- Cross-check with the existing PIT diagnostic: if the pooled SD is ~right on average but the tails are miscalibrated *asymmetrically by tier* — elite totals landing too near their predicted median (peaked PIT for high-rating), field totals too fat in the ends — that's the signature of a rating-varying SD the single constant can't capture. Worth adding a per-tier PIT table to `calibrate.py` to see this directly.

**First look (2026-07, mid-season cache — 94 rounds, 6,667 player-rounds).**
Ran `calibrate --by-rating`. The hunch holds and is material — round-SD falls
~0.8 strokes (~20% relative) from the bottom to the top rating bucket in both
divisions, well past the materiality gate below.

```
MPO (pooled round-SD 3.62)         FPO (pooled round-SD 3.67)
 928-996    sd 4.10                 810-913    sd 4.03
 996-1009   sd 3.85                 913-931    sd 4.07
1009-1020   sd 3.58                 931-948    sd 3.53
1020-1028   sd 3.37                 948-959    sd 3.66
1028-1034   sd 3.46                 959-966    sd 3.30
1034-1062   sd 3.29                 966-990    sd 3.32
```

MPO declines smoothly / ~monotonically; FPO looks more like a threshold (flat
~4.05 below ~930, then a step down) — though FPO buckets hold ~325 rounds vs
MPO's ~785, so FPO estimates are ~1.6x noisier. Offseason: fit both a linear
slope (likely MPO) and a two-tier step (likely FPO), cross-validate across
events, and re-run the PIT check split by tier.

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
- **Errors in the rating itself** *(raised in review 2026-08, unchecked)*. The
  residual is `(score - field_mean) - b*(rating - field_mean)`, so any noise in
  `rating` lands in the residual, contributing `|b| * sd(rating error)` strokes —
  and `b` is about 1/6, so a rating carrying 15 points of error contributes 2.5
  strokes against a pooled SD of 3.65. Bottom-of-field players at Elite stops are
  Monday qualifiers and locals whose ratings rest on far fewer rated rounds than a
  touring pro's, so the error is plausibly *much* larger at the bottom. Illustrative
  arithmetic: hold the true SD flat at 3.5 for everyone and give the bottom bucket
  15 points of rating error and the top 5, and the observed SDs come out 4.30 and
  3.60. The measured pattern is 4.10 → 3.29. **A model with zero true
  heteroskedasticity reproduces the finding**, so as it stands the effect is not
  identified. Test: get a proxy for rating precision (rated-round count, or DGPT
  starts) and check whether the gradient survives conditioning on it; better,
  subtract the estimated `b^2 * var(rating error)` from each bucket's variance
  analytically. If it vanishes, this is "confirmed, immaterial" and a season of
  overfitting is saved.
- *Mean misspecification.* Bucket residuals use one pooled linear slope, so any
  curvature in the rating→score relation leaves mean error in the end buckets and
  inflates their SD. Probably second-order — PDGA round ratings are near-linear in
  score by construction — but it costs nothing to refit the mean with bucket
  dummies before measuring the spread. *(review 2026-08, unchecked)*

**The gate above cannot currently do its job.** "More than ~0.4–0.5 strokes across
the range" was written to separate signal from a rounding artifact, but the
rating-error confound plausibly manufactures 0.7+ on its own. The materiality gate
needs to be *preceded* by an identification step, not just met. Note the statistics
are not the problem: bucket SEs are ~0.09, and even allowing for clustering by
player the pattern is 5+ SE. The pattern is real; what produces it is open.

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

## Review backlog (raised 2026-08, **unreviewed**)

> These came out of an outside review of the model and **none of them are signed
> off.** The evidence cited is the reviewer's, the arithmetic is theirs, and the
> gates are proposals. Each needs the same "is this actually real?" pass section 1
> got — and section 1 is the cautionary tale for why: it looked solid for a month
> before anyone thought about rating error. Ordered by claimed effect on published
> numbers, which is itself a claim to check.

### 2. Attendance is drawn independently, and the shrinkage constant was never fit

**Hypothesis.** Two separate problems in `fields.py` / `simulate._simulate`, both
pushing the same way.

*Independence.* Remaining-event attendance is drawn per event per sim
(`rng.random((c, n)) < event_probs[ev_i]`), so "how many events does this player
play" has binomial variance. Real attendance is lumpy and correlated within a
player — an injury, a decision to skip the fall swing, a season written off — so
the true spread is much wider. The model is therefore too confident about the
season point totals of exactly the bubble players who decide the cutline.

*An unfit constant.* `SHRINKAGE = 3.0` pulls a player's observed rate toward the
cohort prior. It is the one consequential number in the model that was set by hand;
`RATING_PTS_PER_STROKE` and `ROUND_SD` both got a careful refit.

**Why it matters.** For "who makes the Cup," whether a bubble player starts three
more events or one swamps any stroke-level refinement. It compounds with the
counting caps: best-10-of-N is a max, so an extra start is nearly free option
value and the payoff is convex in the number of starts. Understating the variance
of starts understates the variance of the quantity that sets the cutline.

**How to test.** Shrinkage is directly fittable with data already on disk: hold out
completed events, predict who started, tune shrinkage (and the cohort definition)
by log loss. Overdispersion is measurable the same way — compare the observed
spread of starts-per-player against what independent Bernoullis with the model's
own probabilities produce.

**Implementation sketch.** Draw a per-player latent engagement factor once per sim
and condition the per-event Bernoullis on it (beta-binomial), preserving each
player's marginal probability. Localised change in `_simulate`.

**Confounds.** Attendance and performance may not be independent — a player still
in contention plays more — and modelling the lumpiness without that could
mis-shape the tails. Also, from ~4 events out the registration lists are real, so
the shrinkage prior only governs the far end of the schedule; check how much of the
remaining schedule it actually touches before treating it as the top lever.

**Gate.** Ship if the fitted shrinkage differs materially from 3.0, or if the
overdispersion check shows observed spread well outside the binomial. Ordinary
grading can't referee this one — the season-level outcome resolves once — so the
holdout attendance log loss is the metric.

### 3. The live model has a form parameter and refuses to use it

**Hypothesis.** `ROUND_SD = 4.2` is documented as event-level: ~3.65 of round noise
plus within-event form correlation. Back that out and the implied within-event
correlation is rho ≈ 0.16 (form variance ≈ 2.2, per-round idiosyncratic ≈ 11.2,
since 4.2·sqrt(3) = 7.27). The live path then behaves as if form does not exist:

- *The mean never updates.* `simulate.py:433` projects the remaining holes at the
  rating-implied pace regardless of how the player has scored. At rho = 0.16, after
  two rounds the posterior puts ~0.28 weight on the observed residual — a player
  averaging 3 strokes under expectation should be projected ~0.85 strokes better
  over the final round.
- *The SD is too wide exactly when it is most visible.* Conditional on two rounds
  played, remaining variance is 1.56 (posterior form) + 11.2 (round noise), so
  SD ≈ 3.57 against the 4.2 in use — ~17% too wide on the final round.

The same single constant errs the other way for majors: folding rho into `ROUND_SD`
and scaling by `sqrt(rounds)` gives 8.4 for a 4-round event where the correlation
structure implies ~8.9, so majors are ~6% too narrow, and they are double-weighted
in points.

**Falsifiable prediction.** Both live errors point one way: 54-hole leaders should
win more often than the model says. `dgpt.grade_live` was built to check exactly
this — see its "win rate by standing at the checkpoint" table, and
`--variant both` scores the form-conditioned model against the published one.

**Note before believing rho.** `grade_live.fit_round_structure` reports
`rho_within` and `rho_across`. Within-event correlation includes *persistent*
rating error, not only today's form — a player whose rating is stale beats it in
every round of the event. `rho_across` (same player, different events) measures
that persistent part. For projecting the rest of an event `rho_within` is the
right conditioner either way, but a large `rho_across` means the real fix is
entry 4, not a better noise model.

**Implementation sketch.** Carry `sd_round` and `rho` as the model's constants and
derive the event-level and conditional SDs from them, rather than shipping one
pre-multiplied number. `grade_live.project`'s `form` branch is the arithmetic.

**Gate.** Form-variant Brier beats the current model on win and top-5 across the
full replay corpus, and the leader row's obs/pred moves toward 1.00.

### 4. Rating is treated as known, current truth

**Hypothesis.** Every player is a point estimate with no uncertainty, frozen at
today's value for the rest of the season. So a 1035 trending up and a 1035 trending
down are the same player, low-sample ratings are trusted as much as touring pros',
and nobody's rating moves in-sim although they will in reality.

**Why it matters.** Reviewer's claim is that this is the highest-ceiling change in
the model: the input matters more than the machinery. It is also upstream of
section 1 — if ratings are noisy in a rating-dependent way, that *is* the
heteroskedasticity finding.

**How to test.** All the cached rounds are already there. Build a form-weighted
recent-rounds skill estimate, blend it with the official rating, and run a straight
out-of-sample horse race on predicting round score. `grade_live` grades the
downstream effect end to end.

**Related, smaller, and precise: the fitted slope is the wrong slope for
forecasting.** `calibrate.fit` regresses score on the rating a player held *that
day*; the sim applies that slope to a rating up to two months old, projected
forward. Skill drifts, so the predictive relation at horizon k is weaker than the
contemporaneous one and the sim should use a flatter slope — one that flattens
further out. As built, the model is mildly overconfident in ratings order for
distant events. Test: regress event score on rating as of k weeks earlier, by k,
and see whether the slope decays.

### 5. Everything is differenced against the field, so course fit cannot exist

**Hypothesis.** Scores are modelled relative to the field mean, which cleanly
removes course difficulty and weather — and also removes any way to say that a
player is much better on tight wooded courses than on open bombers. Section 1's
spin-off notes course type as a *variance* lever; the *mean* interaction is
probably the larger signal.

**How to test.** Tag events by course type, fit a player × course-type random
effect on the cached rounds, and check out-of-sample whether it beats rating alone.
Cheap to try, and the same tagging feeds the variance question.

**Confound.** Course type is confounded with time of season and with which players
enter, and with ~19 events a season there is not much to fit on. Expect this to
need more than one season before it clears any gate.

### 6. Smaller, logged for completeness

- **No withdrawal or injury path.** A player who "plays" always finishes every
  round. WDs are dropped from the fit rather than modelled as censored bad results,
  which is also the truncation confound in section 1. Fixing both at once is the
  argument for doing it.
- **Doubles is knowingly wrong.** Team score = mean of the two ratings with
  singles-level SD. Best-shot doubles has materially lower variance and does not
  combine ratings linearly. One event, in the DGPT counting pool — low stakes, but
  it should not be undocumented.
- **Normal residuals.** Disc golf scores are right-skewed and the right tail is
  censored by dropping WDs. Matters much less for `p_cut` — a sum over ~10 counting
  events is near-normal whatever the per-event shape, and the best-N cap already
  truncates downside — than for live win % and event-win numbers.

### 7. Grading needs a baseline, and the season history starts too late

*Partly shipped: `dgpt.grade_live` covers the live projections. What is left is
the season-level forecast.*

- **`dgpt.evaluate` has no reference forecast.** A Brier score compared to nothing
  is uninterpretable: ~600 of ~650 MPO players sit near 0% and resolve to 0, so the
  number will look excellent and mean nothing. Add a baseline — "sort by current
  points, no simulation" is the obvious one — and report skill against it.
  `grade_live` does this with `score_only` / `rating_only` and the pattern carries
  over directly.
- **The snapshot history starts 2026-07-04, at 13 events completed.** The genuinely
  hard forecasts — March, April — were never recorded, so the season grade will be
  flattering and low-information. Nothing to do for 2026 beyond knowing it; start
  snapshotting from event 1 in 2027.
- **Grading trusts every snapshot equally**, including ones written while the
  pipeline was known broken (HARDENING item 8 has the same note).

### 8. The freeze rule should be versioning, not freezing *(process, needs a decision)*

The rule at the top of this file — nothing ships until the season's predictions are
frozen and gradeable — has the right instinct and, the reviewer argues, the wrong
mechanism.

It is already porous: `RATING_PTS_PER_STROKE` went from 6.0/6.82 to 6.0/7.3 and
`ROUND_SD` was nearly halved mid-season, and the Ledgestone `_round_plan` fix moved
live odds enormously. So it holds for things labelled "levers" and not for things
labelled "fixes," and that line is doing unexamined work.

Gradeability does not require freezing, it requires versioning: stamp a
`model_version` on every snapshot row (`snapshot.FIELDS` already carries
`taken_at` and `events_completed`) and a mid-season change stops destroying the
season's grade — it gives two series to compare instead. The counter-argument is
that a published forecast that changes under readers is its own problem, separate
from whether it can be scored. Worth deciding deliberately rather than by default.

## Backlog (unfleshed — one-liners to expand later)

_Add new hunches here as they come up; promote to a full section when we dig in._

- **[In-season TODO, ~Aug/Sep] Consume real playoff rosters, then re-enable playoff
  registration tracking.** `simulate.run` gates the GMC/MVP fields on standings
  rank, never on a registration list, so the model assumes every qualifier
  attends. When DGPT playoff sign-ups open, teach the sim to use
  `registered_field` for those events, then remove `playoff`/`championship` from
  `movers.REG_GATED_CLASSES` so genuine playoff sign-ups/withdrawals show in the
  Biggest Movers "Registration changes" column again. Until both land, that
  column correctly hides playoff/Cup attendance (it's a qualification swing, not
  a sign-up). Don't lift the exclusion on roster existence alone — PDGA may open
  the roster before the model change, which would re-expose standings gating as
  fake registration churn.
