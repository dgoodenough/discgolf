# Model ideas — offseason lever backlog

A running log of score-model / forecast levers to investigate. **Not a
mid-season worklist:** every change here alters published odds, so nothing gets
implemented until the offseason, after the season's predictions are frozen and
gradeable (`python -m dgpt.evaluate`). The point of writing them down now is to
capture the hunch *and the evidence that prompted it* while it's fresh.

Each entry: the hypothesis, why it would move the forecast, how the model treats
it today, how to test it against cached rounds, the confounds to rule out, and a
rough gate for "is it worth the added complexity."

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
- Item 2 below finds the same shape in a different quantity: a player's *rating*
  is also more stable the higher it is (drift SD 7.18 at the bottom of the MPO
  table against 4.77 at the top). Two measures of consistency moving together is
  either one cause or one artifact — fit them looking at each other, not blind.
- The truncation point above argues for capturing a per-round data-quality /
  WD-rate summary during refresh, so the offseason fit isn't blind to what's
  missing.

---

## 2. Rating drift over a forecast horizon (and why the good ones drift down)

**Status:** logged, not scheduled · raised 2026-09, after the 2027 tab shipped ·
deliberately parked until the 2026 Cup is played and this season's predictions
are frozen

**Hypothesis.** A player's rating on the day of a future event is not their
rating today, and the difference is not symmetric noise. It has a *level*
(ratings mean-revert, so the further above the field you are the more the pull
is downward) and a *spread* that grows with the horizon. The model currently
has neither.

**What surfaced it.** The 2027 projection makes Gannon Buhr a 42.9% favourite
to win the Powerball Cup. The live 2026 forecast, with 21 of 24 events banked,
makes him 43.2%. Those are the same number, and they are the same number for a
structural reason worth writing down: the Cup is one four-round event whose
outcome is a function of (rating vs the field) and (starting strokes), the seed
ladder tops out at -7, and he is at the cap in both years. So an entire extra
season of dominance buys him nothing at the Cup — there is nothing above -7 to
win. The whole top of the board repeats too (Wysocki 17→16, Heimburg 11→12,
Robinson 7→7, McMahon 5→5).

None of that is wrong. What *is* wrong is the confidence: a forecast fourteen
months out is exactly as sharp as one taken three events from the end, and it
has no business being. Frozen ratings are the reason. This is the single
assumption most responsible for the 2027 tab reading as an echo of the 2026 one.

**Prior art — we already had a version of this, in 2021.**
`archive/2021/DGPTModelV2.ipynb` (cells 2 and 4) carried an
`EventMeanRegression` term:

```python
# Create a Mean Regression so that Events further out have more uncertainty
EventMeanRegressionValue = np.log10(np.absolute(days_until_event))
SingleRoundExpectedScoreValue = -1 * (
    (Rating - EventDivisionAverageRating)
    / (RatingPointsPerStroke + EventMeanRegressionValue)   # inflated divisor
)
```

It did not survive the rebuild — nothing in `simulate.py` reads a start date
into the score model. Three things to know before porting it back:

- **It shrank the mean, not the variance,** despite the comment. Inflating
  rating-points-per-stroke pulls every player's expected score toward the field
  average, so a distant event gets a flatter, more egalitarian field rather than
  a wider one. `ROUND_SD` never moved. The favourite's edge does dent, which is
  the effect it was remembered for, but it gets there by making everyone more
  alike rather than by admitting we do not know who anyone will be.
- **The units are arbitrary.** It adds log10(days) — a pure number — to a
  rating-points-per-stroke quantity. It works because the magnitudes happen to
  land well, not because it measures anything, and there is nothing for the
  constant to be fit against.
- **It never saturates.** log10 keeps creeping: a decade out gives a divisor of
  9.6, not infinity. And the archived cell's own `###Placeholder` comments flag
  that a past event takes `log10(|negative days|)` and gets a positive shrink.

For scale, the 2021 formula applied to the 2027 calendar (170-407 days out from
today) leaves every event keeping only **70-73%** of the rating edge. Gannon's
~60-point gap over a 1000-rated field would read as ~7 strokes over four rounds
instead of ~10 — easily enough to move 43% into the thirties. So the term is not
a rounding detail; whatever replaces it needs to be defensible.

**First look: the asymmetry is real, and it is in our own data.**
`predictions/history_*.csv` carries a per-snapshot `rating` column, so this
season is already a rating time series. Over 2026-07-04 to 2026-09-08 (58
snapshots, though PDGA publishes monthly so that is really only ~2 rating
updates), per player first-vs-last, by starting-rating quartile:

```
MPO (716 players)          start range   mean change    sd    gained
  bottom quartile           853-978         +3.64      7.18     62%
  2nd                       979-997         +0.31      5.94     47%
  3rd                       997-1010        +0.06      5.73     47%
  top quartile             1010-1062        -0.34      4.77     40%
  slope of change on starting rating: -0.065 per rating point

FPO (222 players)          start range   mean change    sd    gained
  bottom quartile           805-877         +3.24     10.09     56%
  2nd                       879-906         +1.76      9.03     53%
  3rd                       906-935         +1.67      8.18     58%
  top quartile              935-990         +1.54      6.55     53%
  slope of change on starting rating: -0.013 per rating point
```

The bigger they are, the harder they fall — and the less room they had to climb
in the first place. In MPO the top quartile is the only band with a negative
mean and the only one where a minority (40%) gained at all, while the bottom
quartile gained 62%. That is what a rating ceiling looks like: 1030 to 1040 is a
much scarcer move than 970 to 980, because rating is scored against course
rating and the headroom thins out at the top.

Note the second finding hiding in that table: **the spread shrinks with rating
too** (7.18 → 4.77 in MPO). Higher-rated players are more stable in rating, not
just more capped — which rhymes with item 1 above and may share a cause.

FPO is much weaker (slope -0.013, every band positive, whole division up +2.05),
on a third of the sample. Treat the effect as an MPO finding for now.

**Why it matters.** It is the difference between a 2027 tab that echoes 2026 and
one that says something. Symmetric drift would barely move Gannon — he could
drift to 1075 as readily as 1045. Mean-reverting drift puts his *expected* 2027
rating below today's and caps his upside, which is what actually brings 43%
down and widens the field behind him. It should also make the 2026 forecast very
slightly less sharp about its own remaining events, which is correct and which
is why this cannot ship mid-season.

**How to test.**
- Fit an Ornstein-Uhlenbeck / AR(1) form on the history file: `Δrating = -k *
  (rating - mu) * Δt + sigma * sqrt(Δt) * eps`, with `k`, `mu` and `sigma`
  estimated per division. The quartile table above is the crude version of `k`;
  the fit is the real one.
- **Do not extrapolate the 9-week slope linearly.** -0.065 per rating point over
  two months is a *reversion rate*, and over fourteen months reversion saturates
  toward an equilibrium rather than compounding — a naive linear read would pull
  a 1060 player 25 points below a 1000 player, which is nonsense. The OU form
  handles this; a linear fit does not.
- Let `sigma` be rating-dependent, per the spread column above, and check it
  against item 1's variance work rather than fitting the two blind to each other.
- Grade it: re-run the 2027 tab with and without drift and look at whether the
  favourite's odds and the size of the contending pack move the way a
  fourteen-month horizon should.

**Confounds to rule out.**
- *Selection.* Players enter the standings table by playing, and playing well.
  The bottom quartile's +3.64 is partly genuine improvement and partly a low
  starting point regressing up. Fit on the full ratings snapshot, not on the
  standings table.
- *Two rating updates is thin.* 58 snapshots is not 58 observations — PDGA
  publishes monthly. A full season (or two) of history is the honest sample, and
  `data/current_ratings.json` only keeps the current one. Consider retaining a
  dated ratings archive during refresh so the offseason fit is not this short.
- *Age and career stage.* "High rating drifts down" and "young players drift up"
  are different claims that this table cannot separate. Worth a birth-year or
  first-PDGA-year covariate before attributing it all to the ceiling.
- *Is it a ceiling or a cap?* Check whether the top-band effect is smooth in
  rating or a hard shelf near ~1050 where almost nobody has ever gone higher.

**Worth-it gate.** Ship only if the drift materially widens the 2027 tab
(favourite off 43% by more than Monte Carlo noise, and a visibly larger
contending pack) AND the 2026 forecast's own calibration does not degrade —
this touches both, and the live forecast is the product. If the fit comes back
with reversion too slow to matter over a season, log it as "confirmed,
immaterial" and just say so on the tab instead.

---

## Backlog (unfleshed — one-liners to expand later)

_Add new hunches here as they come up; promote to a full section when we dig in._

- **Model the USDGC's actual qualification.** The 2027 schedule brings the
  USDGC into the MPO Powerball World Standings for the first time, so for the
  first time its field decides points. It is an invitational, and its
  invitations are not a standings cut: broadly, past champions, the previous
  year's top 10, and the top 2 at each of a menu of qualifying events — a menu
  that includes DGPT stops but is not limited to them. The 2027 projection
  (`dgpt/project.py`) models attendance there like any other US stop, which is
  the weakest single assumption on that tab and is labelled as such on the
  page. Fixing it needs the 2027 qualifier list, which is not out; it is worth
  doing once it is, because a 300-point major with a field the standings do not
  choose is exactly the kind of event a rate-based attendance model gets wrong
  in both directions — it lets in players who could not qualify, and it leaves
  out the past champion who plays nothing else. Not urgent: the tab is
  experimental and the season is a year away.

- **Model EuroTour Tour Card displacement.** The 2028 card bands are published
  (`season2027.ET_CARDS`: MPO top 6 Full / top 24 EuroTour, FPO top 3 / top
  12), but so is a displacement rule the model ignores. A European who has
  already earned a Full Tour Card through the DGPT World Standings passes their
  EuroTour Card spot down, and a Full Tour Card winner may elect to take a
  EuroTour Card instead if they expect less US travel. Both only ever push
  cards further down the standings, so today's `p_card` is a floor for anyone
  just outside a band and the page says so. Modelling it properly means
  simulating 2028 Full Tour Card qualification off the 2027 World Standings —
  which the DGPT tab already simulates — and then resolving the two races
  together, plus a behavioural guess about who elects down. The join is the
  interesting part and the guess is the reason it has not been done: the two
  tabs currently share a roster and a score model but not a season, and wiring
  them together for a rule that moves one or two spots is a lot of coupling for
  the payoff.

- **[Done, 2026-08] Consume real playoff rosters, then re-enable playoff
  registration tracking.** Shipped, though not the way this entry expected:
  `movers.REG_GATED_CLASSES` was NOT lifted. Playoff attendance stayed excluded
  because it never became a pure registration signal — `p_gmc_field` is the
  signup list unioned with the standings gate while later waves are pending, so
  crossing it still conflates "entered" with "climbed into the top 100".
  Sign-ups are reported off a separate `signed` snapshot column instead, which
  is a registration fact and nothing else. The distinction that matters is
  predictability: a waitlist move or a withdrawal can't be modelled, so it gets
  reported; a player drifting across a qualification cutline is already in the
  forecast and would be double-counted as news.
