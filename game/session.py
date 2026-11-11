"""One human-coached season. Rules are the research simulation's own step functions.

The human replaces exactly two decisions of coach c1 (club_a), by wrapping the research decision
functions for the duration of one step: the lineup, and the first coaching slot of a week (advice).
Everything else, including the assistant coach's remaining slots, stays with the research Policy.
"""
from contextlib import contextmanager
from dataclasses import replace

from soccer_sim import league_economy as le

from . import observation as obs
from .reveal import season_reveal

SEASON_DAYS = 42
TRAINING_DAYS_PER_WEEK = 6
METHODS = ("A", "B")
# Season rules and the research features the game needs (stated reasons need advice_response + belief records).
GAME_CONFIG = replace(
    le.config_for_condition("league_open_solidarity_sampled_population"),
    season_length_days=SEASON_DAYS, personality="latent_v1", advice_response="personality_v1",
    coach_belief="observe_v1", observation_noise_mode="centered_v1", coach_practice_rating="blend_v1",
    coach_exploration="unseen_bonus_v1")


class InvalidInput(ValueError):
    pass


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

    @property
    def phase(self):
        day = self.world.day
        if day >= SEASON_DAYS:
            return "season_end"
        return "lineup" if day % GAME_CONFIG.match_interval_days == TRAINING_DAYS_PER_WEEK else "training"

    def state(self):
        world = self.world
        return {
            "seed": self.seed, "day": world.day, "season_days": SEASON_DAYS, "phase": self.phase,
            "week": world.day // GAME_CONFIG.match_interval_days + 1,
            "club_id": obs.OWN_CLUB, "squad": obs.squad_view(world), "standings": obs.standings_view(world),
            "recommended_starters": obs.recommended_starters(world), "methods": list(METHODS),
        }

    def _run(self, days, **hooks):
        before_events, before_squad = len(self.world.events), obs.squad_view(self.world)
        with _replaced(**hooks):
            le.advance_league_days(self.world, days)
        if self.phase == "season_end":
            le._validate_league_invariants(self.world)
        after_squad = obs.squad_view(self.world)
        return {"events": obs.observed_events(self.world, before_events),
                "estimate_changes": obs.estimate_changes(before_squad, after_squad), "state": self.state()}

    def train(self, advice):
        """Run one week of training. advice: None or {"player_id", "method"} given on the week's first day."""
        self._require_phase("training")
        if advice is not None:
            self._check_advice(advice)
        return self._run(TRAINING_DAYS_PER_WEEK, decide_coach_training=self._advice_hook(advice))

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
        return self._run(1, decide_club_lineup=human_lineup)

    def reveal(self):
        self._require_phase("season_end")
        return season_reveal(self.world)

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
