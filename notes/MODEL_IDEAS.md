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
- The truncation point above argues for capturing a per-round data-quality /
  WD-rate summary during refresh, so the offseason fit isn't blind to what's
  missing.

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
