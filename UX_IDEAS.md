# UX ideas — a feedback round on the site

Companion to [MODEL_IDEAS.md](MODEL_IDEAS.md) (model levers) and
[HARDENING.md](HARDENING.md) (pipeline engineering). This one is the **read
side**: the site, the phone, the story. Nothing here is scheduled; it's a
capture of what four different readers noticed, and the short list they
argued their way to at the end.

Method: a moderated conversation between four personas, each given the live
site, the repo, and the published MPO bundle. Numbers quoted below are
measured against `origin/site` as of 2026-09-04 19:57Z, three events left
in the season.

**The room**

- **Reese** — touring MPO pro, third season, rated ~1012, currently living on
  the auto-bid bubble. Reads the site on a phone in a rental car.
- **Nadia** — product designer, data-dense interfaces (trading, logistics).
  Has never watched a round of disc golf.
- **Kwame** — mathematician (stochastic processes). Also has never watched a
  round of disc golf. Read `simulate.py` before reading the site.
- **Trav** — the fan. Watches every Jomez and GK post, runs a 12-person
  fantasy league, has opinions about the 2026 points structure.

---

## 1. First contact, on a phone

**Trav:** I love this. I've been arguing with my league about whether Reese
makes the Cup for a month and this just... answers it. The header line —
"24 of 32 spots effectively locked, 45 still in contention" — that's the
whole season in one sentence. That should be a graphic people can post.

**Nadia:** Agreed on the header. Then I turned my phone to portrait and the
site quietly deleted its best feature. At 560px the responsive tiers strip
everything past `Cup` and `Win Cup` — which means the **finish distribution
and starting-strokes sparklines are gone on every phone.** The signature
visual, the thing that separates this from a standings page, only exists on
desktop.

**Reese:** I never knew those existed. I've only ever seen this on my phone.

**Nadia:** That's the finding. And it gets worse: every explanation on this
site is a hover. The column headers carry `title=` attributes with genuinely
good copy — "P(earns a Cup spot via a top-4 MVP Open finish, outside the
standings cut)" — and on touch, `title` never fires. So the phone reader gets
a column labeled **MVP Bid** with no way, ever, to find out what it means.
Same for the five panels in "What's left": the cloud, the cutline ticks, the
leverage grid, the race chart all say "hover for the exact odds." On a phone
that's an instruction you cannot follow.

**Kwame:** I'll add the version of that complaint from someone who does read
it on a laptop: the tooltips are where the model lives. `title` text is
carrying the actual definitions — what counts, what the caps are, what the
seed ladder does. That's load-bearing documentation hidden behind a mouse.

**Trav:** Also — MPO, FPO, GMC, MVP, DGPT, NT. I know all of them. Nadia and
Kwame didn't know any.

**Nadia:** I guessed MVP was a most-valuable-player award. It's a disc
company.

**Kwame:** I assumed GMC was a truck.

---

## 2. Finding one player

**Reese:** Here's my actual use, every time: I want *my row*. On my phone I
scroll. And scroll. How many players are in this table?

**Nadia:** 560 rendered rows, out of 690 in the bundle. And 45 of those 560
are in contention for anything.

**Reese:** So I scroll past 500 people to find out whether I should book
Idlewild. There's no search box.

**Nadia:** There's no search box, no filter, no jump-to-me, no "contenders
only" toggle. The `Auto / All columns / Advanced` segmented control is the
only tool over the table, and all three of those options make the table
*wider*, not shorter. On mobile the wide mode is the one that helps least.

**Trav:** I want to compare two players side by side. My whole league is
"Reese vs. the 28th spot." Right now I open the row, read it, close it, open
another row, and hold the first one in my head.

**Reese:** There's a permalink though — someone sent me `#mpo-75412` once and
it opened straight to my row, expanded. That was great.

**Nadia:** That's the one deep link that exists, and it's a good one. But the
view and the division and the sort aren't in the URL. So you cannot share
"look at the Event odds tab," or "the FPO cutline panel," or "sorted by Win
Cup." Trav's league is going to paste screenshots forever because the app
can't address its own states.

**Trav:** I paste screenshots forever.

**Nadia:** And the social card is a single static `card.png` for every link.
A player permalink deserves a generated card — name, rank, Cup odds, the
sparkline. That's the single highest-leverage growth change on the list and
it's a build-step change, not a design change.

---

## 3. Kwame reads the model

**Kwame:** I want to say first that the honesty here is unusual. There's a
`predictions/` directory that snapshots every forecast so it can be graded,
a validator that diffs against StatMando and fails CI on drift, and a
calibration module that refit against 6,667 player-rounds. Most sports
models publish a number and no scorecard. This one built the scorecard
first.

**Trav:** So what's wrong with it?

**Kwame:** Nothing *wrong*. Three things I'd want visible, and one I'd want
answered.

Visible, first: **the scorecard is not on the site.** `python -m
dgpt.evaluate` grades Brier and calibration at season's end, locally. A
reader has no way to see whether the model has been any good. Ship a
calibration page — a reliability curve, the running Brier score against a
naive baseline, "when this site said 40%, it happened 38% of the time." That
is what converts a stranger into a believer, and right now it exists only as
a command in a README.

Second: **the noise term is a single constant.** `ROUND_SD = 4.2`, applied
identically to a 1050-rated player and a 950-rated one. MODEL_IDEAS.md
already logs the hunch that variance falls with rating and has measured it —
about 0.8 strokes across the rating range, monotone in MPO — so I'm not
raising anything new. I'm raising it as a *reader-facing* issue: the tails
are where a forecast like this earns its keep, and the site presents win
probabilities to three significant figures on top of a variance assumption
the repo itself considers provisional. Say that out loud on the page.

**Nadia:** You want an uncertainty disclosure in the UI.

**Kwame:** I want the leverage grid's note — "figures carry about a point of
Monte Carlo noise" — to be the house style everywhere, not one panel's
footnote. `>99.9%` and `<0.1%` are already handled beautifully. It's the
middle of the range that oversells.

Third: **the linear rating-to-strokes map.** Six rating points per stroke in
MPO, 7.3 in FPO, one slope for everyone. Fine as a first model, and the fit
is honest. But a reader who sees "Rating" as a column and a win probability
in the next column will infer a precision the linear fit doesn't have.

And the question I actually can't answer from the outside: the what-if
toggles replay a single player against **25,000 frozen per-simulation
cutlines** in under 100ms, which is a lovely trick. But a frozen cutline is
the cutline *from the world where that player did what the model expected.*
If Reese wins Idlewild, she takes points that would otherwise have gone to
whoever the cutline is made of. How large is that error? The repo should
know — it's a comparison against a full re-sim for a handful of players —
and the answer belongs in a footnote either way.

**Reese:** If I win Idlewild the guy at 28 doesn't win Idlewild, yeah.

**Kwame:** That's exactly the term. It's probably small at the bubble and
largest for a favorite. But "probably" is doing work there.

---

## 4. Trav wants a story

**Trav:** Can I do wishes now?

**Nadia:** Please.

**Trav:** The **Event odds** tab is the best thing on the site and it's the
third tab. During a round that chart is the show — you can see who led it and
lost it. Two things it needs. One: I want it to be the *default* tab while an
event is live. Two: the biggest hole of the tournament. You have win
probability on a holes-played axis, so you already have the deltas — tell me
"Hole 14, Reese went from 22% to 61%." Baseball has had win probability added
for twenty years and disc golf has nothing like it. That's a leaderboard I
would check every night: **biggest single-hole swings of the season.**

**Kwame:** That's a defensible statistic and it's free — it's a diff over a
series you already store.

**Trav:** Next: notify me. If someone's Cup odds move 15 points during a
round I want my phone to buzz. You're already recomputing every five minutes
during play.

**Nadia:** That's a real product decision, not a small one — push means
service workers, subscriptions, a sender. There's a cheap intermediate:
publish a JSON feed and an RSS feed of movers. Anyone who wants a buzz can
wire it up themselves, and it costs you a file in the build.

**Trav:** I'd take that. Last one: FPO. The division toggle is there and it
works, but every headline on the page is written from MPO's numbers by
default, and the FPO bundle is 40% smaller because fewer players have
points. It reads like the B side. Whatever you do, do it for both, and
consider whether the default should be "whichever division has an event on
right now."

**Reese:** Seconded, and not just on principle — the FPO bubble is usually
the more interesting race because the field is smaller.

---

## 5. Reese wants a decision

**Reese:** Everything on this site tells me what will happen. Nothing tells
me what to do. Those are different products.

**Nadia:** Say more.

**Reese:** The leverage grid is the closest — swing in auto-bid odds per
event, per player. That's genuinely the most useful number on the site for
me and it's buried in the fifth panel of the second tab, in a grid I can't
read on a phone because it needs a hover. Turn that around. Put it in my
row: "Idlewild is worth ±19 points of auto-bid odds to you. GMC is worth ±4.
If you can only afford one flight, take Idlewild."

**Trav:** That's a caddie.

**Reese:** That's a caddie. And go one further — instead of me toggling
events to see what happens, solve it backwards. **"What's the cheapest path
to 90%?"** Tell me: top 10 at Idlewild and you're at 88%; top 5 and you're
in. Right now I have to hunt for that by flipping checkboxes.

**Kwame:** You want the inverse problem. Given a target probability, find the
minimum-effort set of finishes that reaches it. That's a real optimization
but a small one at this scale — you have the machinery already, it's a search
over the replay you can already do in 100ms.

**Reese:** Two more from the road. One: I'm at events with no signal, in
fields, in the woods. If the page loaded once this morning I want it to load
again in the parking lot. Right now every page load appends a cache-busting
query string, so it re-downloads the entire 1.07 MB bundle every single time
and shows nothing at all when the bars are gone.

**Nadia:** That one's a genuine tension, not an oversight — the comment in
`app.js` explains it. GitHub Pages caches the JSON at the edge for ten
minutes, so without the buster the site could show data ten minutes older
than what it just published. The fix isn't to drop the buster, it's to stop
paying for it with the whole payload: cache the last good bundle in a service
worker, render it instantly with a "showing 08:41, reconnecting" banner, and
swap when the fresh fetch lands. Stale-while-revalidate. You get both.

**Reese:** Two: on a bad connection, 1.07 MB.

**Nadia:** And most of it isn't what you're looking at. 150 KB of that file
is a 25,000-entry cutline array that only the what-if replay and one panel
use. Another 169 KB is per-player finish and seed distributions for all 690
players, when maybe 70 rows ever get expanded. Split the bundle: a light
table payload for first paint, the heavy tail fetched when someone opens a
row or the "What's left" tab. First paint could be well under 200 KB.

---

## 6. Where they disagreed

Worth recording, because these are the calls the site's author has to make,
not consensus items.

**Density.** Nadia wanted mobile to become cards — one player, one card,
stacked. Reese and Trav both refused: they want a table, because a table lets
you compare down a column, and comparing is the point. Kwame sided with the
table. Nadia's fallback — the one all four accepted — is **a table with a
sticky first column and horizontal scroll on the data columns**, so `Player`
stays put while you swipe through `Cup / Win Cup / Auto Bid / MVP Bid`.
Nothing gets deleted at 375px; it moves sideways instead. This replaces the
current tier-hiding approach on phones.

**How many rows.** Reese wanted contenders only. Trav wanted all 690 —
"finding an unrated nobody at 0.2% is half the fun." Compromise: default to
the contenders (`p_champ >= 0.1%` or in a live field — 122 rows today), with
a "show all 560" at the bottom, plus search. Search resolves it either way.

**Light mode.** Kwame asked for it (reads on a bright porch). Nadia noted
`tokens.css` already ships a `.ledger-light` newsprint variant, fully
defined, and *nothing in the app ever applies it* — no toggle, no
`prefers-color-scheme` query. So this is a two-line change to expose a
variant that already exists and is already styled. Trav said dark is the
brand. Recommendation: respect `prefers-color-scheme`, add a toggle,
remember the choice.

**Precision.** Kwame wanted fewer significant figures on mid-range
probabilities. Trav wanted more decimals, not fewer, because his league
argues about tenths. Unresolved; leaning Kwame with a hover for the exact
value, since the point of a forecast is not to be quoted to a tenth.

---

## 7. Blue sky — the ideas nobody costed

Kept deliberately unfiltered.

- **Seed ladder as a tee-off graphic.** Starting strokes are the most novel
  thing in this whole forecast and they're currently a bar chart. Draw the
  actual ladder: eight rungs from −7 to E, players sitting on the rung
  they're projected to tee off from, sliding as the season moves.
- **Read it to me.** A "top 10, spoken" mode for the drive between events.
  The copy on this site is already written in sentences; it would narrate
  well.
- **Pick'em against the model.** Let a reader lock in their own Cup field of
  32 before the playoffs and grade them against the model's Brier score at
  season's end. The grading harness already exists.
- **Rivalry pages.** `#mpo-75412-vs-98283` — two players, both distributions
  overlaid, "who finishes ahead" as one number, head-to-head over the season.
- **Time machine.** `predictions/history_{div}.csv` already holds every
  snapshot. A date slider over the season — "what did the model think in
  June?" — is a read-side feature over data that's already written, and it's
  the single best answer to "why should I trust this."
- **A public API.** `data/*.json` is already static and public; documenting
  it as an API with a stable schema costs a page and invites other people to
  build things. Fantasy tools, bots, overlays.
- **Course fit.** The model is rating-only. Wooded vs. open is the loudest
  disc golf argument there is; even a crude per-course variance multiplier
  would be a story ("Idlewild rewards the field, not the favorites").
- **Broadcast overlay mode.** A URL that renders one giant win-probability
  chart on a dark background, nothing else — for anyone doing a watch-along
  stream.
- **"Why did this move?" per player.** The movers panel answers it for the
  top movers. Put a one-sentence generated version in every expanded row:
  "up 6 points this week: +11 rating, and Nybo entered GMC."

---

## The short list

Ten recommendations. Ordered by (value ÷ effort) as the room saw it, with
the surface each one affects.

| # | Recommendation | Surface | Why it's high on the list |
|---|---|---|---|
| 1 | **Make every tooltip touch-accessible.** Tap-to-reveal on sparklines, charts, and column headers; convert `title=` header copy into a tappable glossary sheet. | mobile | The site's documentation is currently invisible to every phone reader. Nothing else on this list changes as many readers' experience. |
| 2 | **Add a search / jump-to-player box, and default the table to contenders** with "show all" beneath it. | both | 560 rendered rows for a 45-player race; the pro's primary use case is "find my row." |
| 3 | **Stop deleting the sparklines on phones.** Replace tier-hiding with a sticky `Player` column and horizontally scrollable data columns. | mobile | The signature visual is absent at exactly the width most people read at. |
| 4 | **Split the bundle.** Light table payload for first paint; cutline array (150 KB) and per-player distributions (169 KB) fetched on demand. | both | 1.07 MB → under ~200 KB for first paint, on tour-site connections. |
| 5 | **Service-worker cache with stale-while-revalidate**, and a visible "showing 08:41, reconnecting" state. | mobile | Keeps both properties: no edge-stale data, and a site that works in a parking lot. |
| 6 | **Put the whole app state in the URL** — view, division, sort — and generate a per-player social card at build time. | web | Makes the app shareable and quotable; the permalink already proves the pattern works. |
| 7 | **Ship the scorecard.** A public calibration page: reliability curve, running Brier vs. a naive baseline, "when we said 40%." | web | The strongest trust asset in the repo is currently a local command. |
| 8 | **Surface leverage in the player row** — "Idlewild is worth ±19 pts of auto-bid odds to you" — instead of only in a hover-only grid on tab two. | both | Turns a forecast into a decision, which is what the pro actually needs. |
| 9 | **Default to the live tab during play**, and add biggest-hole win-probability swings (per event, and a season leaderboard). | both | Free from a series already stored; the most postable statistic on the site. |
| 10 | **Light mode.** Honor `prefers-color-scheme`, add a toggle, persist it. | both | `.ledger-light` is already written and styled in `tokens.css` and never applied. |

Plus two that are cheap and were unanimous: **make row expansion keyboard
accessible** (`tabindex`, `role="button"`, `aria-expanded` — the SVGs already
carry `role="img"` and good `aria-label`s, so the table is the gap), and
**publish a movers JSON/RSS feed** so notifications can be built by anyone
who wants them.

## The open questions

1. **How wrong is the frozen cutline?** The what-if replay holds 25,000
   cutlines fixed while one player's results change, but a player who wins
   takes points from the field that defines the cutline. Measure the what-if
   against a full re-sim for a few players at different standings positions
   and footnote the answer.
2. **Should mid-range probabilities be quoted to a tenth of a percent?** The
   `>99.9%` / `<0.1%` handling is exemplary. 41.3% implies a precision the
   variance assumption does not support. Round the display, keep the exact
   value on tap?
3. **Is FPO a first-class division or a toggle?** Every headline currently
   reads from whichever division is loaded, and MPO is the default. Should
   the default follow the live event instead?
4. **Who is the site for on a phone — the fan or the pro?** They want
   opposite things (narrative vs. decision). Two entry points, or one
   compromise? The room did not agree.
5. **Would a jargon glossary or inline first-use expansion serve newcomers
   better?** Two of four readers could not decode MPO, FPO, GMC, or MVP.
   "How it works" explains the points system well but assumes the acronyms.
