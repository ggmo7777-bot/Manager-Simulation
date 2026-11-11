"""Human-coached seasons, one after another in the same world. Rules are the research simulation's own step functions
(aging and retire_replace_v1 generations included).

The human replaces exactly four decisions of coach c1 (club_a), by wrapping the research decision
functions for the duration of one step: the lineup, the first coaching slot of a week (advice), the second slot of the
week's first day (scouting one player of another club), and which players to bid for in a transfer window.
Everything else (the assistant coach's remaining slots, the players' answers, the market rules, other clubs) stays research.
"""
import hashlib
import json
import re
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

from soccer_sim import league_economy as le
from soccer_sim.fingerprints import league_runtime_fingerprint

from . import observation as obs
from .reveal import season_reveal
from statistics import fmean

SEASON_DAYS = 42
TRAINING_DAYS_PER_WEEK = 6
METHODS = ("A", "B")
# Season rules and the research features the game needs (stated reasons need advice_response + belief records).
GAME_CONFIG = replace(
    le.config_for_condition("league_open_solidarity_sampled_population"),
    season_length_days=SEASON_DAYS, personality="latent_v1", advice_response="personality_v1",
    coach_belief="observe_v1", observation_noise_mode="centered_v1", coach_practice_rating="blend_v1",
    coach_exploration="unseen_bonus_v1",
    # Uncalibrated game defaults: initial ages 29..34 and newcomers 18..23 (age_spread=5, game-v3) so retirements are staggered; no real-world data behind these.
    aging_start_age=34, age_spread=5, transfer_window_cycle="per_season_v1", squad_size=4, aging_curve="hypothesis_v1", generations="retire_replace_v1",
    # Uncalibrated game default (G6): the academy clubs (club_a, club_c) start with 25M instead of the research 21M so a season-1 bid (12M fee + 8M reserve) can be afforded.
    initial_academy_cash_units=25_000_000)


ESTIMATE_FIELDS = ("skill_estimate", "fatigue_estimate", "observation_count", "measurement_error", "receptiveness")
SAVE_VERSION = 3
CONFIG_VERSION_LENGTH = 6
MAX_SEED_DIGITS = 18  # int() refuses very long digit strings with a ValueError


class InvalidInput(ValueError):
    pass


def _sha256(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def config_version():
    """Short id of the season rules and research features: two players share a world only on the same one."""
    return _sha256(GAME_CONFIG.to_dict())[:CONFIG_VERSION_LENGTH]


def share_code(seed):
    return f"{config_version()}-{seed}"


def seed_from_code(code):
    """Seed of a shared code, or InvalidInput (wrong shape, or made under a different config version)."""
    version, separator, seed = code.partition("-") if type(code) is str else ("", "", "")
    if not separator or not re.fullmatch(r"-?\d{1,%d}" % MAX_SEED_DIGITS, seed):
        raise InvalidInput("シードコードの形式が正しくありません (例: abc123-7)")
    if version != config_version():
        raise InvalidInput(f"このコードは別のゲーム設定バージョン({version})のもので、このゲーム({config_version()})では同じ世界になりません")
    return int(seed)


def code_fingerprint():
    """Research runtime + game step rules + season config: a save only replays on identical rules."""
    return _sha256({"runtime": league_runtime_fingerprint()["sha256"], "config": GAME_CONFIG.to_dict(),
                    "session": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})


@contextmanager
def _replaced(**functions):
    # shortcut: patches module globals, so not thread-safe; the server serialises requests with a lock.
    originals = {name: getattr(le, name) for name in functions}
    for name, function in functions.items():
        setattr(le, name, function)
    try:
        yield
    finally:
        for name, function in originals.items():
            setattr(le, name, function)


class GameSession:
    def __init__(self, seed):
        if type(seed) is not int:
            raise InvalidInput("seed must be an integer")
        self.seed = seed
        self.world = le._build_initial_world(seed=seed, config=GAME_CONFIG)
        self.inputs = []  # every accepted player input incl. "next_season" markers, in order: the whole save besides the seed
        self.season = 1
        self.season_start_totals = obs.account_totals(self.world)
        self.reveals = {}  # season -> true values of that season's squad, taken just before the season-end aging
        self.history = []  # one public summary per completed season
        self.estimate_log = {}  # player -> the coach's own estimate after each step (rebuilt by replay, never saved)
        self._record_estimates()

    @property
    def day_in_season(self):
        return self.world.day - (self.season - 1) * SEASON_DAYS

    @property
    def phase(self):
        day = self.day_in_season
        if day >= SEASON_DAYS:
            return "season_end"
        return "lineup" if day % GAME_CONFIG.match_interval_days == TRAINING_DAYS_PER_WEEK else "training"

    def state(self):
        return obs.check_visible(self._state())

    def _state(self):
        world = self.world
        return {
            "seed": self.seed, "share_code": share_code(self.seed), "season": self.season, "day": self.day_in_season, "season_days": SEASON_DAYS,
            "phase": self.phase, "week": self.day_in_season // GAME_CONFIG.match_interval_days + 1,
            "club_id": obs.OWN_CLUB, "squad": obs.squad_view(world),
            "standings": obs.standings_view(world, self.season_start_totals),
            "recommended_starters": obs.recommended_starters(world), "methods": list(METHODS), "history": self.history,
            "names": obs.display_names(self.seed, world), "notices": obs.notices_view(world, (self.season - 1) * SEASON_DAYS),
            "targets": obs.targets_view(world), "market": obs.market_view(world, SEASON_DAYS), "recommended_offers": obs.recommended_offers(world),
        }

    def _run(self, days, move, first_day, **hooks):
        before_events, before_known = len(self.world.events), self._known_players()
        with _replaced(**hooks):
            le.advance_league_days(self.world, days)
        if self.phase == "season_end":
            le._validate_league_invariants(self.world)
            self._close_season(before_events)
        events = obs.observed_events(self.world, before_events)
        yours = [e for e in events if obs.is_your_event(e, move, first_day)]
        self._record_estimates()
        return obs.check_visible({"events": events, "your_move": move, "your_events": yours,
                                  "auto_events": [e for e in events if e not in yours],
                                  "estimate_changes": obs.estimate_changes(before_known, self._known_players()), "state": self.state()})

    def _record_estimates(self):
        for row in obs.squad_view(self.world):
            self.estimate_log.setdefault(row["player_id"], []).append({
                "day": self.world.day, "season": self.season, **{k: row[k] for k in ESTIMATE_FIELDS}})

    def player_view(self, player_id):
        """One player of the squad as the coach knows him: his estimate over time and every event about him, with labels."""
        if player_id not in self.world.club_rosters[obs.OWN_CLUB]:
            raise InvalidInput(f"player must be one of {self.world.club_rosters[obs.OWN_CLUB]}")
        return obs.check_visible({"player_id": player_id, "history": self.estimate_log[player_id],
                                  "events": obs.player_events(self.world, player_id)})

    def _step(self, record, move, days, **hooks):
        """Run one step and record the input only once it completed. If it fails midway the world has advanced
        without a record, so rebuild it from the recorded inputs: state and input log never disagree."""
        try:
            result = self._run(days, move, self.world.day + 1, **hooks)
        except Exception:
            self._rebuild()
            raise
        self.inputs.append(record)
        return result

    def _rebuild(self):
        rebuilt = GameSession(self.seed)
        rebuilt._replay(self.inputs)
        self.__dict__.update(rebuilt.__dict__)

    def _known_players(self):
        return obs.squad_view(self.world) + obs.targets_view(self.world)

    def train(self, advice, scout=None):
        """Run one week of training. advice: None or {"player_id", "method"} given on the week's first day;
        scout: None or a player of another club the coach scouts on that day (the research rule decides who can be afforded)."""
        self._require_phase("training")
        if advice is not None:
            self._check_advice(advice)
        if scout is not None and scout not in obs.scoutable_targets(self.world):
            raise InvalidInput(f"scout must be one of {obs.scoutable_targets(self.world)} (funds or player)")
        move = {"kind": "train", "advice": advice, "scout": scout}
        return self._step(move, move, TRAINING_DAYS_PER_WEEK, decide_coach_training=self._training_hook(advice, scout))

    def play_match(self, starters, offers=None):
        """Play the match day. offers: players of other clubs to bid for if a transfer window follows this match (else none)."""
        self._require_phase("lineup")
        offers = [] if offers is None else offers
        self._check_offers(offers)
        roster = self.world.club_rosters[obs.OWN_CLUB]
        if type(starters) is not list or not all(type(pid) is str for pid in starters) or len(starters) != 2 \
                or len(set(starters)) != 2 or not set(starters) <= set(roster):
            raise InvalidInput(f"starters must be 2 distinct players of {roster}")
        original = le.decide_club_lineup

        def human_lineup(*, coach, roster, config):
            decision = original(coach=coach, roster=roster, config=config)
            if coach.coach_id != obs.OWN_COACH:
                return decision
            return {**decision, "starters": list(starters), "bench": [pid for pid in roster if pid not in starters]}
        original_acquisition = le.decide_transfer_acquisition

        def human_acquisition(*, coach, roster, player_id, config):
            decision = original_acquisition(coach=coach, roster=roster, player_id=player_id, config=config)
            return decision if coach.coach_id != obs.OWN_COACH else {**decision, "wants_offer": player_id in offers}
        move = {"kind": "lineup", "starters": list(starters), "recommended_starters": obs.recommended_starters(self.world),
                "offers": list(offers),
                "recommended_offers": obs.recommended_offers(self.world) if le.is_transfer_window_day(self.world.config, self.world.day + 1) else []}
        return self._step({"kind": "lineup", "starters": list(starters), "offers": list(offers)}, move, 1,
                          decide_club_lineup=human_lineup, decide_transfer_acquisition=human_acquisition,
                          _age_players=self._capture_before_aging(le._age_players))

    def next_season(self):
        """Continue the same world: the next season starts from the state the last one ended in."""
        self._require_phase("season_end")
        self.season += 1
        self.season_start_totals = obs.account_totals(self.world)
        self.inputs.append({"kind": "next_season"})
        return {"events": [], "your_move": {"kind": "next_season"}, "your_events": [], "auto_events": [], "estimate_changes": [], "state": self.state()}

    def _capture_before_aging(self, original):
        def capture(world, season):
            self.reveals[season] = season_reveal(world, (season - 1) * SEASON_DAYS)
            original(world, season)
        return capture

    def _close_season(self, before_events):
        """Public summary of the season just played; the accuracy figure is derived from the reveal, an aggregate only."""
        reveal = self.reveals[self.season]
        table = obs.standings_view(self.world, self.season_start_totals)
        rank, own = next((i + 1, row) for i, row in enumerate(table) if row["club_id"] == obs.OWN_CLUB)
        retired, joined = obs.season_turnover(self.world, before_events)
        errors = [abs(p["skill_estimate"] - p["skill_true"]) for p in reveal["players"]]
        self.history.append({
            "season": self.season, "rank": rank, "champion": table[0]["club_id"], "points": own["points"],
            "wins": own["wins"], "draws": own["draws"], "losses": own["losses"],
            "goals_for": own["goals_for"], "goals_against": own["goals_against"],
            "retired": [{"player_id": r["player_id"], "age": r["age"], "reason": r["reason"]} for r in retired],
            "joined": [{"player_id": j["player_id"], "age": j["age"]} for j in joined],
            "skill_mae": fmean(errors), "squad_size": len(errors)})

    def save_payload(self):
        """Seed + player inputs only (no world state, so no true value). view_digest is over the player's view."""
        return {"version": SAVE_VERSION, "code": code_fingerprint(), "seed": self.seed, "inputs": self.inputs,
                "view_digest": _sha256(self.state())}

    @classmethod
    def resume(cls, payload):
        """Replay the saved inputs from the seed; reject a save from other code or one that does not reproduce."""
        if type(payload) is not dict or payload.get("version") != SAVE_VERSION:
            raise InvalidInput("save file format is not supported")
        if payload.get("code") != code_fingerprint():
            raise InvalidInput("save was made by different game/research code and cannot be replayed")
        session = cls(payload.get("seed"))
        inputs = payload.get("inputs")
        if type(inputs) is not list:
            raise InvalidInput("save inputs must be a list")
        session._replay(inputs)
        if _sha256(session.state()) != payload.get("view_digest"):
            raise InvalidInput("save was altered: the replay does not reach the saved state")
        return session

    def _replay(self, inputs):
        for item in inputs:
            kind = item.get("kind") if type(item) is dict else None
            if kind == "train":
                self.train(item.get("advice"), item.get("scout"))
            elif kind == "lineup":
                self.play_match(item.get("starters"), item.get("offers"))
            elif kind == "next_season":
                self.next_season()
            else:
                raise InvalidInput("save contains an unknown input")

    def reveal(self, season=None):
        """True values of a completed season's squad (the current one only once it has ended)."""
        season = self.season if season is None else season
        if season == self.season:
            self._require_phase("season_end")
        if season not in self.reveals:
            raise InvalidInput(f"season {season} has no reveal")
        return {"season": season, **self.reveals[season]}

    def _require_phase(self, expected):
        if self.phase != expected:
            raise InvalidInput(f"phase is {self.phase}, expected {expected}")

    def _check_advice(self, advice):
        roster = self.world.club_rosters[obs.OWN_CLUB]
        if type(advice) is not dict or advice.get("player_id") not in roster or advice.get("method") not in METHODS:
            raise InvalidInput(f"advice needs player_id in {roster} and method in {METHODS}")

    def _check_offers(self, offers):
        others = obs.other_club_players(self.world)
        if type(offers) is not list or len(set(map(str, offers))) != len(offers) or not all(o in others for o in offers):
            raise InvalidInput(f"offers must be distinct players of other clubs: {others}")
        if offers and not le.is_transfer_window_day(self.world.config, self.world.day + 1):
            raise InvalidInput("no transfer window follows this match day, so no offers can be made")

    def _training_hook(self, advice, scout):
        """Slot 1 of the week's first day carries the advice, slot 2 the scouting; both only if the human gave them."""
        original, first_day = le.decide_coach_training, self.world.day + 1
        wanted = {1: lambda c: advice and c["kind"] == "propose" and c["target"] == advice["player_id"] and c["method"] == advice["method"],
                  2: lambda c: scout and c["kind"] == "scout" and c["target"] == scout}

        def hook(**kwargs):
            decision = original(**kwargs)
            matches = wanted.get(kwargs["slot"])
            if kwargs["coach"].coach_id != obs.OWN_COACH or kwargs["day"] != first_day or not matches:
                return decision
            chosen = next((c for c in decision["candidates"] if matches(c)), None)
            return decision if chosen is None else {**decision, "chosen": chosen}
        return hook
