"""Human-coached seasons, one after another in the same world. Rules are the research simulation's own step functions
(aging and retire_replace_v1 generations included).

The human replaces exactly two decisions of coach c1 (club_a), by wrapping the research decision
functions for the duration of one step: the lineup, and the first coaching slot of a week (advice).
Everything else, including the assistant coach's remaining slots, stays with the research Policy.
"""
import hashlib
import json
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
    aging_start_age=34, age_spread=5, aging_curve="hypothesis_v1", generations="retire_replace_v1")


SAVE_VERSION = 2


class InvalidInput(ValueError):
    pass


def _sha256(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


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
        world = self.world
        return {
            "seed": self.seed, "season": self.season, "day": self.day_in_season, "season_days": SEASON_DAYS,
            "phase": self.phase, "week": self.day_in_season // GAME_CONFIG.match_interval_days + 1,
            "club_id": obs.OWN_CLUB, "squad": obs.squad_view(world),
            "standings": obs.standings_view(world, self.season_start_totals),
            "recommended_starters": obs.recommended_starters(world), "methods": list(METHODS), "history": self.history,
        }

    def _run(self, days, **hooks):
        before_events, before_squad = len(self.world.events), obs.squad_view(self.world)
        with _replaced(**hooks):
            le.advance_league_days(self.world, days)
        if self.phase == "season_end":
            le._validate_league_invariants(self.world)
            self._close_season(before_events)
        after_squad = obs.squad_view(self.world)
        return {"events": obs.observed_events(self.world, before_events),
                "estimate_changes": obs.estimate_changes(before_squad, after_squad), "state": self.state()}

    def train(self, advice):
        """Run one week of training. advice: None or {"player_id", "method"} given on the week's first day."""
        self._require_phase("training")
        if advice is not None:
            self._check_advice(advice)
        result = self._run(TRAINING_DAYS_PER_WEEK, decide_coach_training=self._advice_hook(advice))
        self.inputs.append({"kind": "train", "advice": advice})
        return result

    def play_match(self, starters):
        self._require_phase("lineup")
        roster = self.world.club_rosters[obs.OWN_CLUB]
        if type(starters) is not list or len(starters) != 2 or len(set(starters)) != 2 or not set(starters) <= set(roster):
            raise InvalidInput(f"starters must be 2 distinct players of {roster}")
        original = le.decide_club_lineup

        def human_lineup(*, coach, roster, config):
            decision = original(coach=coach, roster=roster, config=config)
            if coach.coach_id != obs.OWN_COACH:
                return decision
            return {**decision, "starters": list(starters), "bench": [pid for pid in roster if pid not in starters]}
        result = self._run(1, decide_club_lineup=human_lineup, _age_players=self._capture_before_aging(le._age_players))
        self.inputs.append({"kind": "lineup", "starters": list(starters)})
        return result

    def next_season(self):
        """Continue the same world: the next season starts from the state the last one ended in."""
        self._require_phase("season_end")
        self.season += 1
        self.season_start_totals = obs.account_totals(self.world)
        self.inputs.append({"kind": "next_season"})
        return {"events": [], "estimate_changes": [], "state": self.state()}

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
        for item in inputs:
            kind = item.get("kind") if type(item) is dict else None
            if kind == "train":
                session.train(item.get("advice"))
            elif kind == "lineup":
                session.play_match(item.get("starters"))
            elif kind == "next_season":
                session.next_season()
            else:
                raise InvalidInput("save contains an unknown input")
        if _sha256(session.state()) != payload.get("view_digest"):
            raise InvalidInput("save was altered: the replay does not reach the saved state")
        return session

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

    def _advice_hook(self, advice):
        original, first_day = le.decide_coach_training, self.world.day + 1

        def hook(**kwargs):
            decision = original(**kwargs)
            if advice is None or kwargs["coach"].coach_id != obs.OWN_COACH or kwargs["day"] != first_day or kwargs["slot"] != 1:
                return decision
            chosen = next(c for c in decision["candidates"] if c["kind"] == "propose"
                          and c["target"] == advice["player_id"] and c["method"] == advice["method"])
            return {**decision, "chosen": chosen}
        return hook
