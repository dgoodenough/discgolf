"""Monte Carlo season simulation.

Carries over the original model's core: a player's expected round score
relative to the field average is -(rating - field_avg) / RATING_PTS_PER_STROKE
strokes, with per-round noise N(0, ROUND_SD). Each sim draws fields for the
remaining events from participation probabilities, ranks the finishers, and
awards 2026 points; banked points from completed events are fixed.

Shape of a run (`run` is the orchestration; everything else is a phase):

    _build_roster        who is in the table, and their ratings
    _split_banked        completed-event points, routed to counting pools
    _remaining_events    what is left to simulate
    _attendance_probs    P(plays) per event per player
    _build_doubles       team pairings for the doubles championship
    _build_qual          playoff qualification: signup lists + standings gates
    _build_playin        the Worlds play-in field, and who it can promote
    _load_live_state     current scores for an event in progress
    _Drawer              draws one event's points + places for a chunk of sims
    _simulate            the chunk loop: draws events, applies the caps, ranks
    _live_projections    per-player finish distribution for a live event
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field

import numpy as np

from . import config, fields, live_api, points, ratings, schedule, standings

# Score-model constants, recalibrated against all completed 2026 rounds
# (94 rounds / 6,667 player-rounds; see dgpt/calibrate.py). The 2021 values
# were 6.0 / 6.82: the MPO rating slope was spot-on (fit 6.01) but FPO
# rating gaps are worth fewer strokes (fit 7.34), and the old SD nearly
# doubled the real per-round noise. 4.2 is the event-level figure (pure
# round noise is 3.65; the extra is within-event form correlation, which
# matters because the sim draws event totals in one shot).
RATING_PTS_PER_STROKE = {"MPO": 6.0, "FPO": 7.3}
ROUND_SD = 4.2
ROUNDS = {"major": 4}         # default 3 regular rounds otherwise
DEFAULT_SIMS = 10_000

# Powerball Cup qualification (dgpt.com playoff-qualification-update)
STANDINGS_CUT = {"MPO": 28, "FPO": 18}
FIELD_SIZE = {"MPO": 32, "FPO": 20}


MAX_HIST_RANK = 50   # per-position histogram depth for the app
# Place-histogram depth for a live event's projection. It has to cover the
# whole field: every place past it collapses into the last bucket, so a player
# the model has finishing 170th of 200 reads back as 220th-capped — a
# mean_place pinned to the cap and a mean_pts read off the curve at the wrong
# depth. At 130 that was already short of Ledgestone's 156 MPO; Pro Worlds
# runs ~200 across two courses.
LIVE_CAP = 230


@dataclass
class SimResult:
    division: str
    n_sims: int
    names: list[str]
    pdga_numbers: list[int]
    ratings: list[float]
    current_points: list[float]
    current_rank: list[int]
    mean_points: np.ndarray
    mean_rank: np.ndarray
    p_cut: np.ndarray        # P(final standings rank <= cut) = automatic bid
    p_field: np.ndarray      # P(rank <= championship field size)
    p_first: np.ndarray
    p_gmc: np.ndarray        # P(top gmc_cut in standings before GMC — the points cut)
    p_mvp: np.ndarray        # P(top mvp_cut in standings before MVP — the points cut)
    p_gmc_field: np.ndarray  # P(actually in the GMC field, incl. fill)
    p_mvp_field: np.ndarray  # P(actually in the MVP field, incl. GMC-performance path)
    p_mvp_qual: np.ndarray   # P(earns a championship spot via MVP-performance path)
    p_champ: np.ndarray      # P(in the championship field) = p_cut + p_mvp_qual
    p_playin: np.ndarray     # P(wins a Worlds spot out of the play-in); 0 if not entered
    reg_gmc: np.ndarray      # already signed up for GMC (all False if no list yet)
    reg_mvp: np.ndarray      # already signed up for the MVP Open
    gmc_final: bool          # the GMC list is the field, not a floor
    mvp_final: bool
    # extras for the web app / what-if replay
    rank_hist: np.ndarray    # (n_players, MAX_HIST_RANK) counts of final rank
    cutline: np.ndarray      # per sim: points of the last direct-qualification spot
    cutline2: np.ndarray     # per sim: points of the first spot outside the cut
    att_probs: np.ndarray    # (n_events, n_players) realized P(plays) incl. playoff gating
    events_meta: list[dict]  # remaining events: id/name/cls/rounds/major + score stats
    banked: list[list]       # per player: [(tid, points, is_major, place), ...]
    live_stats: dict         # {tid: {pdga: {cur, rem, win, mean_place, p10/p50/p90, mean_pts}}}
    dbl_info: dict           # {pdga: {partner_name, team_rating}} for the doubles championship


def _curve_vector(division: str, cls: str, size: int) -> np.ndarray:
    """Points indexed by place 1..size (0 index unused).

    Past the published curve's last place the floor keeps being paid, exactly
    as points.assign_points does when an event banks. Ledgestone 2026 was the
    first field deep enough to prove that is the official behaviour (156 MPO;
    StatMando paid 1.33 where the curve table stops at 144) and the banking
    side was fixed then — but the simulation kept paying zero, so the model
    predicts one number for a deep finish and the standings then record
    another. Pro Worlds is the deepest field of the season by some way.
    """
    vec = np.zeros(size + 2)
    if cls == "jomez":
        for place in range(1, size + 1):
            vec[place] = points.jomez_bonus(place)
        return vec
    curve = points.event_curve(division, cls)
    for place, val in curve.items():
        if place <= size:
            vec[place] = val
    deepest = max(curve)
    if size > deepest:
        vec[deepest + 1 : size + 1] = curve[deepest]
    return vec


# ------------------------------------------------------------------ roster

@dataclass
class _Roster:
    """The player set for a run, fixed once and read everywhere after."""
    table: list[dict]
    pdga_numbers: list[int]
    player_ratings: np.ndarray     # (n,) official rating per table position
    idx: dict[int, int]            # pdga number -> table position

    @property
    def n(self) -> int:
        return len(self.table)


def _build_roster(division: str, sched: list[dict]) -> _Roster:
    """Standings rows with a known rating, plus registered first-timers.

    Players with no standings row yet but a spot on a remaining event's
    roster (or in a live field) get a zero-points row so the app can show
    them.
    """
    table = [r for r in standings.compute(division) if r["rating"]]

    have = {r["pdga_number"] for r in table}
    max_rank = max((r["rank"] for r in table), default=0)
    upcoming_rows = [
        r for r in sched
        if not r["completed"] and r[division.lower()] and r["cls"] not in ("championship", "playoff")
    ]
    official = ratings.current(division)  # standings rows already overlaid
    for ev in upcoming_rows:
        roster = live_api.registered_roster(ev["tournament_id"], division)
        for pdga, info in roster.items():
            if pdga in have or not (official.get(pdga) or info.get("rating")):
                continue
            have.add(pdga)
            max_rank += 1
            table.append({
                "pdga_number": pdga, "name": info["name"],
                "rating": official.get(pdga) or info["rating"],
                "rank": max_rank, "points": 0.0, "events": [],
            })

    pdga_numbers = [r["pdga_number"] for r in table]
    return _Roster(
        table=table,
        pdga_numbers=pdga_numbers,
        player_ratings=np.array([float(r["rating"]) for r in table]),
        idx={p: i for i, p in enumerate(pdga_numbers)},
    )


# ------------------------------------------------------------------ banked

@dataclass
class _Banked:
    """Completed-event points, split by 2026 counting pool."""
    majors: np.ndarray             # (n, n_major_tids) one column per major slot
    dgpt: np.ndarray               # (n, max_slots) padded with zeros
    jomez_sum: np.ndarray          # (n,) all Jomez bonus points count
    playoff_sum: np.ndarray        # (n,) both playoffs count
    dgpt_tids: list[list[int]]     # which event fills each DGPT slot
    major_tids: list[list[int]]    # which event fills each major slot
    has_win: np.ndarray            # (n,) already earned the Cup winner invite
    player_events: dict[int, set]  # pdga -> tids started (for participation rates)

    @property
    def max_slots(self) -> int:
        return self.dgpt.shape[1]


def _split_banked(roster: _Roster, division: str, major_tids: set[int]) -> _Banked:
    n = roster.n
    majors = np.zeros((n, len(major_tids)))
    dgpt_lists: list[list[float]] = [[] for _ in range(n)]
    jomez_sum = np.zeros(n)
    playoff_sum = np.zeros(n)
    dgpt_tids: list[list[int]] = [[] for _ in range(n)]
    major_slot_tids: list[list[int]] = [[] for _ in range(n)]
    for i, r in enumerate(roster.table):
        m = 0
        for tid, pts, _, _ in r["events"]:
            pool = points.event_pool(tid, division)
            if pool == "major":
                majors[i, m] = pts
                major_slot_tids[i].append(tid)
                m += 1
            elif pool == "jomez":
                jomez_sum[i] += pts
            elif pool == "playoff":
                playoff_sum[i] += pts
            else:  # dgpt / dgpt+ / doubles
                dgpt_lists[i].append(pts)
                dgpt_tids[i].append(tid)
    max_slots = max((len(b) for b in dgpt_lists), default=1)
    dgpt = np.zeros((n, max_slots))
    for i, b in enumerate(dgpt_lists):
        dgpt[i, : len(b)] = b

    # already-won a DGPT/Major singles event -> guaranteed Cup special invite.
    # Jomez Series and the doubles championship do NOT grant the invite.
    no_invite_tids = config.JOMEZ_TIDS | {config.TID_DOUBLES}
    has_win = np.array([
        any(place == 1 and tid not in no_invite_tids for tid, _, place, _ in r["events"])
        for r in roster.table
    ])
    return _Banked(
        majors=majors, dgpt=dgpt, jomez_sum=jomez_sum, playoff_sum=playoff_sum,
        dgpt_tids=dgpt_tids, major_tids=major_slot_tids, has_win=has_win,
        player_events={r["pdga_number"]: {tid for tid, *_ in r["events"]} for r in roster.table},
    )


# ------------------------------------------------------- remaining schedule

def _remaining_events(sched: list[dict], division: str) -> list[dict]:
    return [
        row for row in sched
        if not row["completed"]
        and row[division.lower()]
        and row["cls"] != "championship"
        and (division == "MPO" or row["fpo_points"])
    ]


def _attendance_probs(sched: list[dict], roster: _Roster, remaining: list[dict],
                      division: str, player_events: dict[int, set]) -> list[np.ndarray]:
    """P(plays) per remaining event, aligned to roster.pdga_numbers."""
    player_names = {r["pdga_number"]: r["name"] for r in roster.table}
    rates = fields.participation_rates(sched, player_events, division, player_names)
    overrides = fields.load_overrides()
    out = []
    for row in remaining:
        probs = fields.play_probabilities(row, division, roster.pdga_numbers, rates, overrides)
        out.append(np.array([probs[p] for p in roster.pdga_numbers]))
    return out


# ----------------------------------------------------------------- doubles

@dataclass
class _Doubles:
    """Team pairings for the doubles championship (absent = ei is None)."""
    ei: int | None = None                              # index into `remaining`
    pairs: list[tuple[int, int]] = field(default_factory=list)
    solos: list[int] = field(default_factory=list)
    team_ratings: np.ndarray | None = None
    info: dict[int, dict] = field(default_factory=dict)  # pdga -> partner/team rating
    # roster indices per team, in team_ratings order (pairs, then solos), and
    # the reverse lookup. The draw and the live projection both work in teams;
    # pairs/solos stay because the info block is built per pairing.
    teams: list[list[int]] = field(default_factory=list)
    team_of: dict[int, int] = field(default_factory=dict)

    @property
    def staged(self) -> bool:
        return self.ei is not None and self.team_ratings is not None


def _build_doubles(remaining: list[dict], roster: _Roster,
                   event_probs: list[np.ndarray], division: str) -> _Doubles:
    """Pair the doubles field; team rating = mean of the two players' ratings.

    Pairings come from live_api.doubles_teams (PDGA Live once staged, DGS
    registration until then — re-fetched every refresh so new teams appear
    automatically). Registered players without a listed partner get a
    field-average partner.
    """
    ei = next((i for i, r in enumerate(remaining) if r["tournament_id"] == config.TID_DOUBLES), None)
    if ei is None:
        return _Doubles()

    pairing = live_api.doubles_teams(config.TID_DOUBLES, division)
    registered = [i for i in range(roster.n) if event_probs[ei][i] >= 0.999]
    seen: set[int] = set()
    pairs: list[tuple[int, int]] = []
    solos: list[int] = []
    for i in registered:
        if i in seen:
            continue
        partner = pairing.get(roster.pdga_numbers[i], {}).get("partner")
        j = roster.idx.get(partner) if partner else None
        # Pair on the roster's own Teammates data alone. Do NOT also
        # require the partner to appear in the registered list: PDGA Live
        # lists ONE row per team (the entrant of record), so the partner
        # is usually absent from it — requiring both collapsed every team
        # into a "solo" with a phantom field-average partner and halved
        # the field. A partner with no row in our table (unrated/am) still
        # falls through to the solo model, which is what it's for.
        if j is not None and j != i and j not in seen:
            pairs.append((i, j))
            seen.update((i, j))
        else:
            solos.append(i)
            seen.add(i)

    # field average over everyone actually playing (both team members),
    # not just the entrants of record — it stands in for a solo's partner
    rtg = roster.player_ratings
    involved = sorted({i for pr in pairs for i in pr} | set(solos))
    favg_players = float(rtg[involved].mean()) if involved else 1000.0
    team_ratings = np.array(
        [(rtg[i] + rtg[j]) / 2 for i, j in pairs]
        + [(rtg[i] + favg_players) / 2 for i in solos]
    )

    names_by_i = {i: r["name"] for i, r in enumerate(roster.table)}
    info: dict[int, dict] = {}
    for t, (i, j) in enumerate(pairs):
        info[roster.pdga_numbers[i]] = {"partner_name": names_by_i.get(j), "team_rating": round(float(team_ratings[t]), 1)}
        info[roster.pdga_numbers[j]] = {"partner_name": names_by_i.get(i), "team_rating": round(float(team_ratings[t]), 1)}
    for t2, i in enumerate(solos):
        info[roster.pdga_numbers[i]] = {"partner_name": None, "team_rating": round(float(team_ratings[len(pairs) + t2]), 1)}

    teams = [[i, j] for i, j in pairs] + [[i] for i in solos]
    team_of = {i: t for t, members in enumerate(teams) for i in members}
    return _Doubles(ei=ei, pairs=pairs, solos=solos, team_ratings=team_ratings,
                    info=info, teams=teams, team_of=team_of)


# -------------------------------------------------------------- live state

@dataclass
class _LiveState:
    """Current scores for events in progress, keyed by index into `remaining`."""
    data: dict[int, tuple] = field(default_factory=dict)   # ev_i -> (cur, rem, in_field, favg)
    place: dict[int, dict[int, int]] = field(default_factory=dict)  # ev_i -> {i: standing}
    thru: dict[int, dict[int, int]] = field(default_factory=dict)   # ev_i -> {i: holes played}
    tie: dict[int, np.ndarray] = field(default_factory=dict)        # ev_i -> (n,) tie-break


# A nudge, in strokes, applied per unit of the sheet's own RunningPlace, so
# that players level on score are ranked the way PDGA ranks them.
#
# It exists for the end of an event: a sudden-death PLAYOFF is scored on its
# own sheet and its strokes are never folded into anyone's total, so the
# players who went to it stay tied on score forever. Ranking them on score
# alone therefore left the result a coin flip after it had been decided — the
# 2026 doubles championship published Saarinen/Allen, who won the playoff and
# hold RunningPlace 1, at 49.78% with the team they beat at 50.22%
# (2026-08-16). The sheet's RunningPlace is the resolved answer, which is why
# live_field already prefers it for the standing it displays; this carries the
# same ordering into the ranking.
#
# Small enough to change nothing else: one stroke is 10,000 of these, and even
# a 130-deep field moves the last row by 0.013 against a per-round SD of 4.2.
# It decides an exact tie and nothing more.
TIE_EPS = 1e-4


def _share_team_state(cur: np.ndarray, rem: np.ndarray, in_field: np.ndarray,
                      place_now: dict[int, int], thru_now: dict[int, int],
                      pairs: list[tuple[int, int]]) -> None:
    """Copy a doubles team's live position onto both of its members.

    A team throws one scorecard, and PDGA Live carries it on the entrant of
    record's row alone — the partner has no row on the sheet at all (the same
    one-row-per-team shape `_build_doubles` pairs against). So live_field
    returns half the players actually on the course, and without this the
    partner reads as not-in-the-field: no locked-in score, no row in the day
    tracker, and no points from a team the model has finishing first.
    """
    for i, j in pairs:
        for a, b in ((i, j), (j, i)):
            if not in_field[a] or in_field[b]:
                continue
            cur[b], rem[b], in_field[b] = cur[a], rem[a], True
            thru_now[b] = thru_now.get(a, 0)
            if a in place_now:
                place_now[b] = place_now[a]


def _load_live_state(remaining: list[dict], roster: _Roster, live_tids: set[int],
                     division: str, doubles: _Doubles) -> _LiveState:
    """Lock in the score so far so only the holes left get simulated."""
    live = _LiveState()
    n = roster.n
    for ev_i, row in enumerate(remaining):
        if row["tournament_id"] not in live_tids:
            continue
        state = live_api.live_field(row["tournament_id"], division)
        if not state:
            continue
        cur = np.zeros(n)
        rem = np.zeros(n)
        in_field = np.zeros(n, dtype=bool)
        place_now: dict[int, int] = {}
        thru_now: dict[int, int] = {}
        for pdga, info in state.items():
            if pdga in roster.idx:
                j = roster.idx[pdga]
                cur[j], rem[j], in_field[j] = info["cur"], info["rem"], True
                if info.get("place"):
                    place_now[j] = info["place"]
                thru_now[j] = int(info.get("thru") or 0)
        if ev_i == doubles.ei and doubles.staged:
            _share_team_state(cur, rem, in_field, place_now, thru_now, doubles.pairs)
        favg = float(roster.player_ratings[in_field].mean()) if in_field.any() else 1000.0
        tie = np.zeros(n)
        for j, standing in place_now.items():
            tie[j] = TIE_EPS * standing
        live.data[ev_i] = (cur, rem, in_field, favg)
        live.place[ev_i] = place_now
        live.thru[ev_i] = thru_now
        live.tie[ev_i] = tie
    return live


# ---------------------------------------------------------- the event draw

class _Drawer:
    """Draws one event's points and finishing places for a chunk of sims.

    Built once per run from inputs that never change, and it owns the
    accumulators it writes: per-event attendance counts, the score-model
    stats reported to the app, and the place histogram for a live event.
    Chunk-varying values (`c`, `rows_ix`, `first_chunk`) are explicit
    arguments — this used to be a closure nested in the chunk loop, capturing
    twenty locals implicitly and being rebuilt on every iteration.
    """

    def __init__(self, division: str, roster: _Roster, remaining: list[dict],
                 curves: dict[int, np.ndarray], doubles: _Doubles,
                 live: _LiveState, rng: np.random.Generator, events_meta: list[dict]):
        self.roster = roster
        self.remaining = remaining
        self.curves = curves
        self.doubles = doubles
        self.live = live
        self.rng = rng
        self.rpps = RATING_PTS_PER_STROKE[division]
        self.events_meta = events_meta
        self.att_count = np.zeros((len(remaining), roster.n))
        self.place_hist = {ev_i: np.zeros((roster.n, LIVE_CAP), dtype=np.int64) for ev_i in live.data}

    def draw(self, ev_i: int, plays: np.ndarray, c: int, rows_ix: np.ndarray,
             first_chunk: bool) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(points, place, plays) for `c` sims.

        The returned `plays` is the participation actually used: the doubles
        championship derives its own from the team pairings and ignores the
        drawn one, so callers must use what comes back rather than what they
        passed in.
        """
        if ev_i == self.doubles.ei and self.doubles.staged:
            return self._draw_doubles(ev_i, c, rows_ix, first_chunk)
        return self._draw_singles(ev_i, plays, c, rows_ix, first_chunk)

    def _draw_doubles(self, ev_i: int, c: int, rows_ix: np.ndarray,
                      first_chunk: bool) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Team-level draw: one score per team, both members share it.

        Live-aware, exactly as _draw_singles is. This branch used to draw the
        whole event from team ratings whatever the scoreboard said, because it
        is chosen ahead of the live path in `draw` — so the one event with a
        team draw was the one event whose live scores were ignored. The
        doubles championship then sat frozen at its pre-event odds for three
        days, and (since nothing filled place_hist) shipped no live projection
        at all: `live_stats` for it was an empty dict, which the app reads as
        "no event in progress" and hides the day tracker (2026-08-14).
        """
        n = self.roster.n
        row = self.remaining[ev_i]
        teams = self.doubles.teams
        team_ratings = self.doubles.team_ratings
        T = len(team_ratings)
        live = self.live.data.get(ev_i)
        if live is not None:
            # in progress: lock in the team's card, project the holes left.
            # Both members carry the team's state (see _share_team_state), so
            # either member's entry describes the team.
            cur, rem, in_field, _ = live
            t_in = np.array([any(in_field[i] for i in m) for m in teams], dtype=bool)
            t_cur = np.array([next((cur[i] for i in m if in_field[i]), 0.0) for m in teams])
            t_rem = np.array([next((rem[i] for i in m if in_field[i]), 0.0) for m in teams])
            # field average over the teams actually playing, mirroring the
            # singles live path (a registered team that never teed off is out)
            tavg = float(team_ratings[t_in].mean()) if t_in.any() else 1000.0
            # the team's standing, off either member (see _share_team_state)
            tie = self.live.tie[ev_i]
            t_tie = np.array([next((tie[i] for i in m if in_field[i]), 0.0) for m in teams])
            mu_t = t_cur - (team_ratings - tavg) / self.rpps * t_rem + t_tie
            sd_t = ROUND_SD * np.sqrt(t_rem)
            tscores = mu_t[None, :] + self.rng.normal(0.0, 1.0, (c, T)) * sd_t[None, :]
            tscores = np.where(t_in[None, :], tscores, np.inf)
        else:
            n_rounds = ROUNDS.get(row["cls"], 3)
            t_in = np.ones(T, dtype=bool)
            tavg = team_ratings.mean() if T else 1000.0
            mu_t = -(team_ratings - tavg) / self.rpps * n_rounds
            tscores = mu_t[None, :] + self.rng.normal(0.0, ROUND_SD * np.sqrt(n_rounds), (c, T))
        torder = np.argsort(tscores, axis=1)
        tplace = np.empty_like(torder)
        tplace[rows_ix, torder] = np.arange(1, T + 1)[None, :]
        place = np.full((c, n), n + 1, dtype=np.int64)
        plays = np.zeros((c, n), dtype=bool)
        for t, members in enumerate(teams):
            if not t_in[t]:
                continue
            for i2 in members:
                place[:, i2] = tplace[:, t]
                plays[:, i2] = True
        if first_chunk:
            meta = self.events_meta[ev_i]
            meta["field_avg_rating"] = round(float(tavg), 1)
            # std of an empty slice is NaN, which json.dump emits bare and
            # JSON.parse rejects — see the note in _draw_singles. Teams out of
            # the field hold inf scores, so the spread is over the field only.
            meta["opp_score_sd"] = round(float(np.std(tscores[:, t_in])), 2) if t_in.any() else 0.0
            meta["field_size"] = float(int(t_in.sum()))
        self.att_count[ev_i] += plays.sum(axis=0)
        self._record_live_places(ev_i, place)
        pts = self.curves[row["tournament_id"]][np.minimum(place, n + 1)]
        pts[~plays] = 0.0
        return pts, place, plays

    def _record_live_places(self, ev_i: int, place: np.ndarray) -> None:
        """Accumulate the projected finish distribution for an event in
        progress — what _live_projections turns into the day tracker."""
        hist = self.place_hist.get(ev_i)
        if hist is None:
            return
        n = self.roster.n
        in_field = self.live.data[ev_i][2]
        cp = np.where(in_field[None, :], np.minimum(place, LIVE_CAP), 0)
        stride = LIVE_CAP + 1
        flat = (np.arange(n) * stride)[None, :] + cp
        hist += np.bincount(flat.ravel(), minlength=n * stride).reshape(n, stride)[:, 1:]

    def _draw_singles(self, ev_i: int, plays: np.ndarray, c: int, rows_ix: np.ndarray,
                      first_chunk: bool) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        n = self.roster.n
        rtg = self.roster.player_ratings
        row = self.remaining[ev_i]
        if ev_i in self.live.data:
            # in progress: lock in the score so far, project the holes left
            cur, rem, in_field, favg = self.live.data[ev_i]
            plays = np.broadcast_to(in_field, (c, n))
            mu = (cur[None, :] - (rtg[None, :] - favg) / self.rpps * rem[None, :]
                  + self.live.tie[ev_i][None, :])
            sd = ROUND_SD * np.sqrt(rem)
            scores = mu + self.rng.normal(0.0, 1.0, (c, n)) * sd[None, :]
            scores = np.where(in_field[None, :], scores, np.inf)
        else:
            n_rounds = ROUNDS.get(row["cls"], 3)
            fsum = (plays * rtg).sum(axis=1)
            fcnt = plays.sum(axis=1)
            avg = np.where(fcnt > 0, fsum / np.maximum(fcnt, 1), 1000.0)
            mu = -(rtg[None, :] - avg[:, None]) / self.rpps * n_rounds
            scores = mu + self.rng.normal(0.0, ROUND_SD * np.sqrt(n_rounds), (c, n))
            scores[~plays] = np.inf
        if first_chunk:
            played = np.where(plays, scores, np.nan)
            meta = self.events_meta[ev_i]
            meta["field_avg_rating"] = round(float((rtg[plays[0]].mean()) if plays[0].any() else 1000.0), 1)
            # nanstd over an all-NaN slice is NaN, and json.dump writes that as
            # a bare NaN: valid to Python, a SyntaxError to JSON.parse, so the
            # browser drops the whole bundle and the page renders nothing. An
            # event nobody is projected to play has no spread to report; the
            # app only reads opp_score_sd when field_size is 0 too, and that
            # path clamps to first place regardless.
            meta["opp_score_sd"] = round(float(np.nanstd(played)), 2) if plays.any() else 0.0
            meta["field_size"] = round(float(plays.sum(axis=1).mean()), 1)
        order = np.argsort(scores, axis=1)
        place = np.empty_like(order)
        place[rows_ix, order] = np.arange(1, n + 1)[None, :]
        if row["cls"] == "doubles":
            place = (place + 1) // 2
        self.att_count[ev_i] += plays.sum(axis=0)
        self._record_live_places(ev_i, place)
        pts = self.curves[row["tournament_id"]][np.minimum(place, n + 1)]
        pts[~plays] = 0.0
        return pts, place, plays


def _rank_of(totals: np.ndarray, rows_ix: np.ndarray, n: int) -> np.ndarray:
    """Standings rank (1 = most points) for each sim row."""
    order = np.argsort(-totals, axis=1)
    r = np.empty_like(order)
    r[rows_ix, order] = np.arange(1, n + 1)[None, :]
    return r


def _top_k_by_place(place: np.ndarray, eligible: np.ndarray, k: int, n: int) -> np.ndarray:
    """Mask of the k best finishers among `eligible` — the performance paths.

    Ineligible players are padded to n + 1 so they can never take a slot. When
    fewer than k players are eligible, the k-th smallest value *is* that
    padding, so every eligible player qualifies — which is the intended rule.

    k is clamped to n because np.partition indexes into the array's own width,
    not the eligible count. A division holding fewer players than the
    qualification constant (FPO's MVP-performance path admits 4) used to raise
    `IndexError: index 3 is out of bounds for axis 1 with size 0` from inside
    numpy. At k = n the k-th smallest is the row maximum, so every eligible
    player qualifies — the same answer the padding already gives.
    """
    padded = np.where(eligible, place, n + 1)
    kth = np.partition(padded, min(k, n) - 1, axis=1)[:, min(k, n) - 1]
    return (padded <= kth[:, None]) & eligible


# ------------------------------------------------------- playoff structure

@dataclass
class _Qual:
    """Playoff/championship qualification rules and event positions."""
    gmc_ei: int | None
    mvp_ei: int | None
    pre_eis: list[int]     # every non-playoff remaining event, drawn first
    gmc_cut: int
    gmc_fill: int
    mvp_cut: int
    mvp_perf: int
    perf_champ: int        # championship spots earned on MVP performance
    standings_cut: int
    # Published signup lists, as (n,) masks — None until a list exists. A
    # player on one is in that field in every sim regardless of the standings;
    # a player off one still qualifies through the gate, because the later
    # registration waves (config.REG_PHASES) have not opened yet. Once the
    # list is final (*_final) the gate is gone: the field is the list.
    gmc_reg: np.ndarray | None = None
    mvp_reg: np.ndarray | None = None
    gmc_final: bool = False
    mvp_final: bool = False


def _signup_mask(tid: int, division: str, roster: _Roster) -> tuple[np.ndarray | None, bool]:
    signed = fields.signed_up(tid, division, roster.pdga_numbers)
    if signed is None:
        return None, False
    mask = np.array([p in signed.players for p in roster.pdga_numbers], dtype=bool)
    return mask, signed.final


def _playoff_field(reg: np.ndarray | None, final: bool, gate: np.ndarray) -> np.ndarray:
    """Who is in a playoff field: the signup list, the standings gate, or both.

    Three states, and the middle one is the whole point of the exercise:

    - no list published        -> the gate alone (how this ran all season)
    - a list, still growing    -> the union; the waves that would reach an
                                  unsigned contender have not opened yet, so
                                  their absence from it means nothing
    - a final list             -> the list alone; unioning a settled
                                  120-player field with everyone the gate
                                  would admit invents entrants, and GMC
                                  points count, so it invents points too

    "Final" is a staged PDGA Live roster AND every registration wave open
    (fields._waves_all_open) — staging alone is not enough, because the DGPT
    stages events weeks ahead of the last wave.
    """
    if reg is None:
        return gate
    if final:
        return np.tile(reg, (gate.shape[0], 1))
    return gate | reg[None, :]


def _build_qual(remaining: list[dict], division: str, roster: _Roster) -> _Qual:
    gmc_ei = next((i for i, r in enumerate(remaining) if r["tournament_id"] == config.TID_GMC), None)
    mvp_ei = next((i for i, r in enumerate(remaining) if r["tournament_id"] == config.TID_MVP), None)
    playoff_eis = {gmc_ei, mvp_ei} - {None}
    gmc_reg, gmc_final = (_signup_mask(config.TID_GMC, division, roster)
                          if gmc_ei is not None else (None, False))
    mvp_reg, mvp_final = (_signup_mask(config.TID_MVP, division, roster)
                          if mvp_ei is not None else (None, False))
    return _Qual(
        gmc_ei=gmc_ei,
        mvp_ei=mvp_ei,
        pre_eis=[i for i in range(len(remaining)) if i not in playoff_eis],
        gmc_cut=config.PLAYOFF_QUAL["gmc"]["cut"][division],
        gmc_fill=config.PLAYOFF_QUAL["gmc"]["fill"][division],
        mvp_cut=config.PLAYOFF_QUAL["mvp"]["cut"][division],
        mvp_perf=config.PLAYOFF_QUAL["mvp"]["perf"][division],
        perf_champ=FIELD_SIZE[division] - STANDINGS_CUT[division],
        standings_cut=STANDINGS_CUT[division],
        gmc_reg=gmc_reg, gmc_final=gmc_final,
        mvp_reg=mvp_reg, mvp_final=mvp_final,
    )


# ------------------------------------------------------- Worlds play-in

@dataclass
class _Playin:
    """The single-round qualifier into the spots Pro Worlds left open.

    It awards no World Standings points itself, but it is a door into a major
    that does, so it has to be drawn: a player who wins through can bank major
    points that change the Cup race. Everything else about it is invisible to
    the model — no schedule row, no points curve, no what-if toggle.

    Entrants without a standings row are not added to the roster (which is
    what the app renders): they have no realistic path to the Cup, and 2026's
    MPO list alone would have added 44 of them. They still have to be *raced*,
    or the rostered entrants would be competing for six spots among
    themselves — `other_ratings` is the rest of the field, drawn as unnamed
    opponents purely to set the bar.

    The two divisions are shaped nothing alike, which is why neither the
    field size nor the outside pool is assumed anywhere: 2026 MPO drew 53
    entrants for 6 spots, 9 of them rostered and none near the Cup, while FPO
    drew 5 for 2 spots — all rostered, no outside field at all, and one of
    them (29th in the standings) at 77% to play their way in.

    Once it has actually been played, `decided` replaces the race with the
    result (see _playin_result) — the spots are won on the course, and after
    that a probability is just a wrong answer that looks like a forecast.
    """
    ei: int                       # index of Worlds in `remaining`
    entered: np.ndarray           # (n,) rostered players in the play-in
    other_ratings: np.ndarray     # ratings of entrants without a roster row
    spots: int
    rpps: float
    hits: np.ndarray              # (n,) sims in which the player advanced
    decided: np.ndarray | None = None   # (n,) who actually won, once it's played

    def advance(self, ratings_arr: np.ndarray, c: int, rng: np.random.Generator) -> np.ndarray:
        """(c, n) mask of rostered entrants who win a Worlds spot."""
        n = ratings_arr.shape[0]
        ix = np.flatnonzero(self.entered)
        out = np.zeros((c, n), dtype=bool)
        if self.decided is not None:
            # Played: the result is a fact, not a race. Racing it anyway is
            # not merely imprecise, it double-counts — the winners are already
            # in the Worlds roster and drop out of `entered`, so the six spots
            # get handed out a second time, to the players who just lost them.
            out[:] = self.decided[None, :]
            self.hits += self.decided * c
            return out
        if ix.size == 0 or self.spots <= 0:
            return out
        field_rtg = np.concatenate([ratings_arr[ix], self.other_ratings])
        favg = float(field_rtg.mean())
        mu = -(field_rtg - favg) / self.rpps * config.PLAYIN_ROUNDS
        sd = ROUND_SD * np.sqrt(config.PLAYIN_ROUNDS)
        scores = mu[None, :] + rng.normal(0.0, sd, (c, field_rtg.size))
        order = np.argsort(scores, axis=1)
        place = np.empty_like(order)
        place[np.arange(c)[:, None], order] = np.arange(1, field_rtg.size + 1)[None, :]
        out[:, ix] = place[:, : ix.size] <= self.spots
        self.hits[ix] += out[:, ix].sum(axis=0)
        return out


def _build_playin(remaining: list[dict], roster: _Roster, division: str,
                  event_probs: list[np.ndarray]) -> _Playin | None:
    """Read the play-in entry list, split it into rostered and everyone else."""
    ei = next((i for i, r in enumerate(remaining)
               if r["tournament_id"] == config.TID_WORLDS), None)
    if ei is None:
        return None
    listed = live_api.registration_list(config.TID_WORLDS_PLAYIN, division)
    if listed is None:
        return None
    entries, _final = listed

    # Division scoping for the opponent pool. A staged PDGA Live roster is
    # already one division; the event page is not, and carries no ratings at
    # all — so the only rating an unrostered page entrant can pick up is the
    # official one, and ratings.current() is per-division. An entrant from
    # the other division simply finds nothing and drops out of the race,
    # which is what should happen. The cost is that a page entrant this
    # season's ratings sweep has never seen drops out too, shrinking the
    # modelled field slightly; PDGA Live carries the real roster by event
    # week, which is when these odds start to matter.
    official = ratings.current(division)
    already_in = event_probs[ei] >= 0.999   # qualified for Worlds directly
    entered = np.zeros(roster.n, dtype=bool)
    others: list[float] = []
    for pdga, info in entries.items():
        i = roster.idx.get(pdga)
        if i is not None:
            # Registered for Worlds itself as well: the direct spot is the one
            # they use, and letting them also take a play-in spot would hand
            # the field a free qualifier.
            if not already_in[i]:
                entered[i] = True
            continue
        rtg = official.get(pdga) or info.get("rating")
        if rtg:
            others.append(float(rtg))
    if not entered.any():
        return None
    return _Playin(
        ei=ei, entered=entered, other_ratings=np.array(others, dtype=float),
        spots=config.WORLDS_PLAYIN_SPOTS[division],
        rpps=RATING_PTS_PER_STROKE[division], hits=np.zeros(roster.n),
        decided=_playin_result(division, roster, entered),
    )


def _playin_result(division: str, roster: _Roster, entered: np.ndarray) -> np.ndarray | None:
    """Who won a Worlds spot, or None while the play-in is still to be decided.

    The play-in is played the day before Worlds, and nothing else in the
    pipeline notices when it ends: the entry list it is built from stays
    published, so the race would be re-run on every refresh for the rest of
    the week. PDGA then updates the Worlds roster with the qualifiers, which
    moves them out of `entered` (they read as directly qualified) and leaves
    the LOSERS racing each other for six spots that no longer exist — the
    model would put six eliminated players in the Worlds field and bank major
    points for them.

    Gated on event_complete rather than on the leaderboard, because
    final_results happily returns a partial order mid-round and freezing the
    overnight leaders as winners would be its own wrong answer. Anything
    unconfirmed — no event payload, no rows, a fetch that failed — keeps the
    race, which is the correct answer right up until the moment it isn't.

    An all-False mask is a real result, not a failure: it means every winner
    was already in the Worlds roster (or was never on ours), so the play-in
    has nobody left to promote.
    """
    try:
        if not live_api.event_complete(config.TID_WORLDS_PLAYIN, (division,)):
            return None
        results = live_api.final_results(config.TID_WORLDS_PLAYIN, division)
    except Exception:  # noqa: BLE001 - unconfirmed is "keep racing", never a crash
        return None
    if not results:
        return None
    spots = config.WORLDS_PLAYIN_SPOTS[division]
    won = np.zeros(roster.n, dtype=bool)
    for r in results:
        if 0 < int(r.get("place") or 0) <= spots:
            i = roster.idx.get(r["pdga_number"])
            if i is not None and entered[i]:
                won[i] = True
    return won


# --------------------------------------------------------- the chunk loop

@dataclass
class _Totals:
    """Everything the chunk loop accumulates across all sims."""
    total_pts: np.ndarray
    total_rank: np.ndarray
    rank_hist: np.ndarray
    cutline: np.ndarray
    cutline2: np.ndarray
    p_gmc_hits: np.ndarray        # rank before GMC within its points cut
    p_mvp_hits: np.ndarray        # rank before MVP within its points cut
    p_gmc_field_hits: np.ndarray  # actually in the GMC field (incl. fill)
    p_mvp_field_hits: np.ndarray  # actually in the MVP field (incl. GMC-perf path)
    p_mvp_qual_hits: np.ndarray   # earns championship via MVP performance
    p_champ_hits: np.ndarray      # in the championship field (auto bid or MVP perf)
    drop_dgpt_hits: np.ndarray    # banked DGPT slot fell outside the counted best-N
    drop_major_hits: np.ndarray   # banked major slot fell outside the counted best-N


def _apply_caps(banked: _Banked, sim_major: np.ndarray, dgpt_cols: list[np.ndarray],
                jomez_cols: list[np.ndarray], c: int, n: int, t: _Totals) -> np.ndarray:
    """Pre-playoff season points under the 2026 per-class caps.

    Best COUNT_DGPT of the DGPT pool, best COUNT_MAJOR majors, all Jomez
    bonus, plus banked playoff points. Also accumulates per-banked-event drop
    odds into `t`: a banked event is dropped when it lands strictly below the
    pool's k-th largest value (one holding the k-th slot still counts).
    """
    majors_all = np.concatenate(
        [np.broadcast_to(banked.majors, (c, n, banked.majors.shape[1])), sim_major], axis=2
    )
    sorted_major = -np.sort(-majors_all, axis=2)
    best_major = sorted_major[:, :, : config.COUNT_MAJOR].sum(axis=2)
    dgpt_all = np.concatenate(
        [np.broadcast_to(banked.dgpt, (c, n, banked.max_slots))] + [x[:, :, None] for x in dgpt_cols], axis=2
    )
    sorted_dgpt = -np.sort(-dgpt_all, axis=2)
    best_dgpt = sorted_dgpt[:, :, : config.COUNT_DGPT].sum(axis=2)

    if dgpt_all.shape[2] > config.COUNT_DGPT:
        thr_d = sorted_dgpt[:, :, config.COUNT_DGPT - 1]
        for j in range(banked.max_slots):
            t.drop_dgpt_hits[:, j] += (banked.dgpt[None, :, j] < thr_d).sum(axis=0)
    if majors_all.shape[2] > config.COUNT_MAJOR:
        thr_m = sorted_major[:, :, config.COUNT_MAJOR - 1]
        for j in range(banked.majors.shape[1]):
            t.drop_major_hits[:, j] += (banked.majors[None, :, j] < thr_m).sum(axis=0)

    jomez_total = banked.jomez_sum[None, :] + sum(jomez_cols, np.zeros((c, n)))
    return best_dgpt + best_major + jomez_total + banked.playoff_sum[None, :]


def _simulate(n_sims: int, chunk: int, roster: _Roster, banked: _Banked,
              drawer: _Drawer, event_probs: list[np.ndarray], live: _LiveState,
              qual: _Qual, rng: np.random.Generator,
              playin: _Playin | None = None) -> _Totals:
    """Draw every remaining event, apply the counting caps, rank the season."""
    n = roster.n
    t = _Totals(
        total_pts=np.zeros((n_sims, n)),
        total_rank=np.zeros((n_sims, n), dtype=np.int32),
        rank_hist=np.zeros((n, MAX_HIST_RANK), dtype=np.int64),
        cutline=np.zeros(n_sims),
        cutline2=np.zeros(n_sims),
        p_gmc_hits=np.zeros(n),
        p_mvp_hits=np.zeros(n),
        p_gmc_field_hits=np.zeros(n),
        p_mvp_field_hits=np.zeros(n),
        p_mvp_qual_hits=np.zeros(n),
        p_champ_hits=np.zeros(n),
        drop_dgpt_hits=np.zeros((n, banked.max_slots)),
        drop_major_hits=np.zeros((n, banked.majors.shape[1])),
    )
    n_sim_majors = sum(1 for i in qual.pre_eis if drawer.remaining[i]["cls"] == "major")

    done = 0
    while done < n_sims:
        c = min(chunk, n_sims - done)
        rows_ix = np.arange(c)[:, None]
        first_chunk = done == 0

        # -- pre-playoff events, routed to their counting pool --
        sim_major = np.zeros((c, n, n_sim_majors))
        dgpt_cols, jomez_cols = [], []
        mi = 0
        sim_win = np.zeros((c, n), dtype=bool)  # won a DGPT/Major event this sim
        for ev_i in qual.pre_eis:
            if ev_i in live.data:
                drawn = np.broadcast_to(live.data[ev_i][2], (c, n))
            else:
                drawn = rng.random((c, n)) < event_probs[ev_i]
            if playin is not None and ev_i == playin.ei:
                # the play-in is decided immediately before Worlds and adds
                # its winners to that field (they are drawn with everyone else)
                drawn = drawn | playin.advance(roster.player_ratings, c, rng)
            pts, place, plays = drawer.draw(ev_i, drawn, c, rows_ix, first_chunk)
            cls = drawer.remaining[ev_i]["cls"]
            # a singles DGPT/Major win earns the Cup special invite; Jomez and
            # the doubles championship do not
            if cls not in ("jomez", "doubles"):
                sim_win |= (place == 1) & plays
            if cls == "major":
                sim_major[:, :, mi] = pts
                mi += 1
            elif cls == "jomez":
                jomez_cols.append(pts)
            else:  # dgpt / dgpt+ / doubles
                dgpt_cols.append(pts)

        base_total = _apply_caps(banked, sim_major, dgpt_cols, jomez_cols, c, n, t)

        def season_totals(extra: list[np.ndarray]) -> np.ndarray:
            # extra = playoff point columns (GMC, then MVP) — both count
            return base_total + sum(extra, np.zeros((c, n)))

        extra: list[np.ndarray] = []  # playoff point columns, added as we go
        # -- Green Mountain: field = top gmc_fill in pre-GMC standings --
        gmc_plays = gmc_place = None
        if qual.gmc_ei is not None:
            rank_pre_gmc = _rank_of(season_totals(extra), rows_ix, n)
            t.p_gmc_hits += (rank_pre_gmc <= qual.gmc_cut).sum(axis=0)
            # A union while signups are still open, not a race for a fixed
            # number of slots: the DGPT sizes the field around its invitees,
            # and a tour-card holder who entered in March never consumed one
            # of the standings slots.
            gmc_plays = _playoff_field(qual.gmc_reg, qual.gmc_final,
                                       rank_pre_gmc <= qual.gmc_fill)
            t.p_gmc_field_hits += gmc_plays.sum(axis=0)
            gmc_pts, gmc_place, gmc_plays = drawer.draw(qual.gmc_ei, gmc_plays, c, rows_ix, first_chunk)
            sim_win |= (gmc_place == 1) & gmc_plays
            extra.append(gmc_pts)

        # -- MVP Open: top mvp_cut in pre-MVP standings + top GMC performers --
        mvp_plays = None
        mvp_place = None
        if qual.mvp_ei is not None:
            rank_pre_mvp = _rank_of(season_totals(extra), rows_ix, n)
            t.p_mvp_hits += (rank_pre_mvp <= qual.mvp_cut).sum(axis=0)
            mvp_plays = _playoff_field(qual.mvp_reg, qual.mvp_final,
                                       rank_pre_mvp <= qual.mvp_cut)
            if qual.gmc_ei is not None and not qual.mvp_final:  # GMC top-perf finishers outside the field advance
                # Eligible = played GMC and is not in the MVP field by any
                # other route. Keyed off the field rather than the points cut
                # so a signed-up player never burns one of the 8/4 spots they
                # do not need.
                elig = gmc_plays & ~mvp_plays
                mvp_plays = mvp_plays | _top_k_by_place(gmc_place, elig, qual.mvp_perf, n)
            t.p_mvp_field_hits += mvp_plays.sum(axis=0)
            mvp_pts, mvp_place, mvp_plays = drawer.draw(qual.mvp_ei, mvp_plays, c, rows_ix, first_chunk)
            sim_win |= (mvp_place == 1) & mvp_plays
            extra.append(mvp_pts)

        cut_n = qual.standings_cut
        totals = season_totals(extra)
        ranks = _rank_of(totals, rows_ix, n)
        t.total_pts[done : done + c] = totals
        t.total_rank[done : done + c] = ranks

        # -- Championship field: auto bid + MVP-performance + event-winner invite --
        auto_bid = ranks <= cut_n
        champ_field = auto_bid.copy()
        if mvp_place is not None:
            # top perf_champ MVP finishers outside the standings cut earn a spot
            elig = mvp_plays & ~auto_bid
            mvp_qual = _top_k_by_place(mvp_place, elig, qual.perf_champ, n)
            t.p_mvp_qual_hits += mvp_qual.sum(axis=0)
            champ_field |= mvp_qual
        # DGPT/Major winners get a special invite (bottom seed) if not already in
        champ_field |= banked.has_win[None, :] | sim_win
        t.p_champ_hits += champ_field.sum(axis=0)

        sorted_totals = -np.sort(-totals, axis=1)
        # A division can hold fewer players than there are qualifying spots
        # (a small early-season FPO field). Then everyone is in: the last
        # filled spot is the lowest-ranked player, and there is no first spot
        # outside the cut for cutline2 to report.
        t.cutline[done : done + c] = sorted_totals[:, min(cut_n, n) - 1]
        t.cutline2[done : done + c] = sorted_totals[:, cut_n] if n > cut_n else 0.0
        capped = np.minimum(ranks, MAX_HIST_RANK)  # one bincount for all players
        stride = MAX_HIST_RANK + 1
        flat = capped + (np.arange(n) * stride)[None, :]
        t.rank_hist += np.bincount(flat.ravel(), minlength=n * stride).reshape(n, stride)[:, 1:]
        done += c

    return t


# ------------------------------------------------------ live projections

def _live_projections(drawer: _Drawer, roster: _Roster, live: _LiveState,
                      remaining: list[dict], curves: dict[int, np.ndarray],
                      division: str, doubles: _Doubles) -> dict[int, dict]:
    """Per-player finish + points distribution for each event in progress."""
    rpps = RATING_PTS_PER_STROKE[division]
    rtg = roster.player_ratings
    live_stats: dict[int, dict] = {}
    places = np.arange(1, LIVE_CAP + 1)
    PRE_DRAWS = 4000
    rng_pre = np.random.default_rng(11)
    for ev_i, hist in drawer.place_hist.items():
        tid = remaining[ev_i]["tournament_id"]
        cur, rem, in_field, favg_live = live.data[ev_i]
        cv = curves[tid]
        pts_by_place = np.zeros(LIVE_CAP)
        m = min(LIVE_CAP, len(cv) - 1)
        pts_by_place[:m] = cv[1 : 1 + m]
        v_asc = pts_by_place[::-1]
        # One shared draw of the field's pre-event scores, reused for every
        # player's "what did we expect of them here?" baseline. Whole rounds,
        # not remaining holes: this is the projection as it stood at tee-off.
        #
        # The unit of competition, at the doubles championship, is the TEAM:
        # the place histogram holds team places and the points curve is
        # indexed by them, so a baseline drawn player-against-player would be
        # compared against twice as many opponents as there are teams and land
        # at roughly double the place — reading the pre-event expectation off
        # the team curve at the wrong depth.
        ev_rounds = ROUNDS.get(remaining[ev_i]["cls"], 3)
        unit_rtg, opp_r = rtg, rtg[in_field]
        if ev_i == doubles.ei and doubles.staged:
            t_in = np.array([any(in_field[i] for i in m) for m in doubles.teams], dtype=bool)
            opp_r = doubles.team_ratings[t_in]
            unit_rtg = np.array([
                doubles.team_ratings[doubles.team_of[j]] if j in doubles.team_of else rtg[j]
                for j in range(roster.n)
            ])
        # for singles this is exactly favg_live; for doubles, the team average
        favg_unit = float(opp_r.mean()) if len(opp_r) else favg_live
        opp_draws = (-(opp_r - favg_unit) / rpps * ev_rounds
                     + rng_pre.normal(0.0, ROUND_SD * np.sqrt(ev_rounds), (PRE_DRAWS, len(opp_r))))
        per: dict[int, dict] = {}
        for j in range(roster.n):
            if not in_field[j]:
                continue
            w = hist[j]
            tot = int(w.sum())
            if tot == 0:
                continue
            cum = np.cumsum(w[::-1])

            def q(f: float, cum=cum, tot=tot) -> float:
                return float(v_asc[min(int(np.searchsorted(cum, f * tot)), LIVE_CAP - 1)])

            # pre-event expectation: what this player's rating alone projected
            # against this field, ignoring the score so far. Comparing it to
            # mean_pts is the over/under-performing story the day tracker tells.
            pre_mu = -(unit_rtg[j] - favg_unit) / rpps * ev_rounds
            pre_scores = pre_mu + rng_pre.normal(0.0, ROUND_SD * np.sqrt(ev_rounds), PRE_DRAWS)
            beat = (opp_draws < pre_scores[:, None]).sum(axis=1)
            pre_place = np.clip(beat + 1, 1, LIVE_CAP)
            per[roster.pdga_numbers[j]] = {
                "cur": round(float(cur[j]), 1),
                "rem": round(float(rem[j]), 2),
                "thru": live.thru.get(ev_i, {}).get(j, 0),  # holes played
                "place": live.place.get(ev_i, {}).get(j),   # current standing
                "win": round(float(w[0] / tot), 4),
                "mean_place": round(float((places * w).sum() / tot), 1),
                "mean_pts": round(float((pts_by_place * w).sum() / tot), 1),
                "pre_place": int(np.median(pre_place)),
                "pre_pts": round(float(np.median(pts_by_place[pre_place - 1])), 1),
                "p10": q(0.10), "p50": q(0.50), "p90": q(0.90),
            }
        live_stats[tid] = per
    return live_stats


# ------------------------------------------------------------------- run

def run(division: str, n_sims: int = DEFAULT_SIMS, seed: int | None = 2026,
        chunk: int = 500) -> SimResult:
    rng = np.random.default_rng(seed)
    sched = schedule.load()
    major_tids = config.MAJOR_TIDS_MPO if division == "MPO" else config.MAJOR_TIDS_FPO

    roster = _build_roster(division, sched)
    if roster.n == 0:
        # Distinct from a small division, which simulates fine. Zero means
        # standings.compute returned nothing AND every remaining event's
        # registration roster was empty — both sources dark at once, which is
        # an outage rather than a season state. Fail loudly here: the
        # alternative is publishing an empty division to a live page, and a
        # blank forecast that looks deliberate is worse than a red run.
        raise ValueError(
            f"{division}: no players with a known rating — standings are empty "
            "and no remaining event returned a registration roster"
        )
    banked = _split_banked(roster, division, major_tids)
    remaining = _remaining_events(sched, division)
    event_probs = _attendance_probs(sched, roster, remaining, division, banked.player_events)
    curves = {row["tournament_id"]: _curve_vector(division, row["cls"], roster.n) for row in remaining}
    doubles = _build_doubles(remaining, roster, event_probs, division)
    live = _load_live_state(
        remaining, roster, {r["tournament_id"] for r in schedule.live_events(sched)},
        division, doubles,
    )
    qual = _build_qual(remaining, division, roster)
    playin = _build_playin(remaining, roster, division, event_probs)

    events_meta = [
        {
            "tid": row["tournament_id"], "name": row["name"], "cls": row["cls"],
            "start_date": row["start_date"],
            "rounds": ROUNDS.get(row["cls"], 3),
            "is_major": row["tournament_id"] in major_tids,
            "field_avg_rating": 0.0, "opp_score_sd": 0.0, "field_size": 0.0,
        }
        for row in remaining
    ]
    drawer = _Drawer(division, roster, remaining, curves, doubles, live, rng, events_meta)

    t = _simulate(n_sims, chunk, roster, banked, drawer, event_probs, live, qual, rng, playin)

    # per-banked-event P(dropped by season end); playoff/jomez never drop
    p_drop: list[dict[int, float]] = [{} for _ in range(roster.n)]
    for i in range(roster.n):
        for j, tid in enumerate(banked.dgpt_tids[i]):
            p_drop[i][tid] = round(float(t.drop_dgpt_hits[i, j] / n_sims), 4)
        for j, tid in enumerate(banked.major_tids[i]):
            p_drop[i][tid] = round(float(t.drop_major_hits[i, j] / n_sims), 4)

    live_stats = _live_projections(drawer, roster, live, remaining, curves, division, doubles)

    cut = STANDINGS_CUT[division]
    fsz = FIELD_SIZE[division]
    return SimResult(
        division=division,
        n_sims=n_sims,
        names=[r["name"] for r in roster.table],
        pdga_numbers=roster.pdga_numbers,
        ratings=list(roster.player_ratings),
        current_points=[r["points"] for r in roster.table],
        current_rank=[r["rank"] for r in roster.table],
        mean_points=t.total_pts.mean(axis=0),
        mean_rank=t.total_rank.mean(axis=0),
        p_cut=(t.total_rank <= cut).mean(axis=0),
        p_field=(t.total_rank <= fsz).mean(axis=0),
        p_first=(t.total_rank == 1).mean(axis=0),
        p_gmc=t.p_gmc_hits / n_sims,
        p_mvp=t.p_mvp_hits / n_sims,
        p_gmc_field=t.p_gmc_field_hits / n_sims,
        p_mvp_field=t.p_mvp_field_hits / n_sims,
        p_mvp_qual=t.p_mvp_qual_hits / n_sims,
        p_champ=t.p_champ_hits / n_sims,
        p_playin=(playin.hits / n_sims) if playin else np.zeros(roster.n),
        reg_gmc=qual.gmc_reg if qual.gmc_reg is not None else np.zeros(roster.n, dtype=bool),
        reg_mvp=qual.mvp_reg if qual.mvp_reg is not None else np.zeros(roster.n, dtype=bool),
        gmc_final=qual.gmc_final,
        mvp_final=qual.mvp_final,
        rank_hist=t.rank_hist,
        cutline=t.cutline,
        cutline2=t.cutline2,
        att_probs=drawer.att_count / n_sims,
        events_meta=events_meta,
        banked=[
            [(tid, pts, tid in major_tids, place, p_drop[i].get(tid, 0.0)) for tid, pts, place, _ in r["events"]]
            for i, r in enumerate(roster.table)
        ],
        live_stats=live_stats,
        dbl_info=doubles.info,
    )


def write_csv(res: SimResult) -> None:
    out_dir = config.REPO_ROOT / "results" / str(config.SEASON)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"projections_{res.division.lower()}.csv"
    order = np.argsort(-res.p_cut, kind="stable")
    cut = STANDINGS_CUT[res.division]
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "name", "pdga_number", "rating", "current_rank", "current_points",
            "mean_final_points", "mean_final_rank", f"p_top{cut}_standings",
            f"p_top{FIELD_SIZE[res.division]}", "p_no1_seed",
        ])
        for i in order:
            w.writerow([
                res.names[i], res.pdga_numbers[i], res.ratings[i],
                res.current_rank[i], res.current_points[i],
                round(float(res.mean_points[i]), 1), round(float(res.mean_rank[i]), 1),
                round(float(res.p_cut[i]), 4), round(float(res.p_field[i]), 4),
                round(float(res.p_first[i]), 4),
            ])
    print(f"wrote {out}")
