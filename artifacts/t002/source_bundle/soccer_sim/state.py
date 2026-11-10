"""Persistent world state and immutable decision inputs."""
from dataclasses import dataclass, field, fields
from math import isfinite

METHODS = ("A", "B")
PLAYER_ACTIONS = (*METHODS, "rest")
PHASES = ("setup", "morning", "coach", "choice", "resolve", "evening", "close")


@dataclass(frozen=True)
class PolicyRules:
    rest_fatigue_weight: float = 0.14
    training_fatigue_weight: float = 0.045
    advice_weight: float = 0.055
    coach_observation_weight: float = 0.15
    coach_need_weight: float = 0.12
    coach_observation_need: float = 0.04
    coach_method_fit: float = 0.05
    coach_rest_value: float = 0.035
    coach_freshness_days: int = 6


@dataclass(frozen=True)
class BeliefRules:
    player_gain: bool = True
    self_skill: bool = True
    coach_skill: bool = True
    recency: bool = True


@dataclass(frozen=True)
class Config:
    duration: float = 1.0
    focus: float = 1.0
    training_fatigue: float = 0.25
    rest_recovery: float = 0.42
    sleep_recovery: float = 0.13
    self_noise: float = 0.025
    fatigue_noise: float = 0.025
    coach_noise: float = 0.065
    self_alpha: float = 0.45
    gain_alpha: float = 0.50
    coach_alpha: float = 0.48
    homogeneous_rates: bool = False
    beliefs: BeliefRules = BeliefRules()
    policy: PolicyRules = PolicyRules()

    def validate(self):
        if type(self.policy) is not PolicyRules:
            raise ValueError("invalid policy coefficients")
        if type(self.beliefs) is not BeliefRules:
            raise ValueError("invalid belief controls")
        for coefficients in (self, self.policy, self.beliefs):
            for item in fields(coefficients):
                value = getattr(coefficients, item.name)
                if type(value) is not type(item.default):
                    raise ValueError(f"invalid type for {item.name}")
                if type(item.default) is float and not bounded(value):
                    raise ValueError(f"out-of-range coefficient: {item.name}")
        if self.duration == 0 or self.policy.coach_freshness_days < 1:
            raise ValueError("duration and freshness window must be positive")


def bounded(value, low=0.0, high=1.0):
    return type(value) in (int, float) and low <= value <= high and isfinite(value)


@dataclass(frozen=True)
class PlayerMind:
    skill_estimate: float
    expected_gains: tuple[float, float]
    preferences: tuple[float, float]
    autonomy: float
    trust: float
    gain_memory_ids: tuple[int, int]
    skill_memory_id: int


@dataclass(frozen=True)
class Assessment:
    player_id: str
    skill_estimate: float
    last_seen: int
    skill_memory_id: int
    recency_memory_id: int


@dataclass(frozen=True)
class CoachMind:
    assessments: tuple[Assessment, ...]


@dataclass(frozen=True)
class SelfObservation:
    event_id: int
    skill_signal: float
    fatigue_signal: float


@dataclass(frozen=True)
class Proposal:
    event_id: int
    method: str


@dataclass(frozen=True)
class PlayerObservation:
    self_view: SelfObservation
    proposal: Proposal | None


@dataclass(frozen=True)
class CoachObservation:
    event_id: int
    day: int
    available: tuple[tuple[str, str | None, str | None], ...]


@dataclass
class Player:
    player_id: str
    skill: float
    cap: float
    rates: tuple[float, float]
    fatigue: float
    mind: PlayerMind
    last_state_id: int


def json_data(value):
    if isinstance(value, dict):
        return {key: json_data(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_data(item) for item in value]
    return value


@dataclass(frozen=True)
class Event:
    event_id: int
    day: int
    phase: str
    kind: str
    actor: str | None
    target: str | None
    causes: tuple[int, ...]
    data: dict


@dataclass
class WorldState:
    seed: int
    config: Config
    day: int = 0
    players: dict[str, Player] = field(default_factory=dict)
    coach: CoachMind = field(default_factory=lambda: CoachMind(()))
    events: list[Event] = field(default_factory=list)

    def record(self, day, phase, kind, actor=None, target=None, causes=(), **data):
        event = Event(len(self.events) + 1, day, phase, kind, actor, target,
                      tuple(dict.fromkeys(causes)), json_data(data))
        previous = self.events[-1] if self.events else None
        if (phase not in PHASES or type(day) is not int or not 0 <= day <= self.day + 1
                or (day == 0) != (phase == "setup")
                or any(type(cause) is not int or not 1 <= cause < event.event_id
                       for cause in event.causes)
                or (previous and (day, PHASES.index(phase))
                    < (previous.day, PHASES.index(previous.phase)))):
            raise ValueError("invalid event order or cause")
        self.events.append(event)
        return event.event_id

    def validate_state(self):
        if type(self.seed) is not int or type(self.day) is not int or self.day < 0:
            raise ValueError("invalid seed or day")
        if set(self.players) != {"p1", "p2"}:
            raise ValueError("invalid player roster")
        for player_id, player in self.players.items():
            mind = player.mind
            if (player_id != player.player_id
                    or not bounded(player.cap)
                    or not bounded(player.skill, high=player.cap)
                    or len(player.rates) != len(METHODS)
                    or len(mind.expected_gains) != len(METHODS)
                    or len(mind.gain_memory_ids) != len(METHODS)
                    or len(mind.preferences) != len(METHODS)
                    or not all(bounded(value) for value in (
                        player.fatigue, *player.rates, mind.skill_estimate,
                        *mind.expected_gains, *mind.preferences, mind.autonomy, mind.trust))):
                raise ValueError(f"invalid state for {player_id}")
            self.validate_pointer(player.last_state_id, player_id, {"initial_player", "state_update"})
            self.validate_pointer(mind.skill_memory_id, player_id,
                                  {"initial_player", "player_skill_belief"})
            for method, event_id in zip(METHODS, mind.gain_memory_ids):
                event = self.validate_pointer(event_id, player_id,
                                              {"initial_player", "player_gain_belief"})
                if event.kind == "player_gain_belief" and event.data.get("method") != method:
                    raise ValueError("invalid training method in gain memory")
        if tuple(assessment.player_id for assessment in self.coach.assessments) != tuple(self.players):
            raise ValueError("invalid coach roster")
        for assessment in self.coach.assessments:
            if (not bounded(assessment.skill_estimate) or type(assessment.last_seen) is not int
                    or not 0 <= assessment.last_seen <= self.day):
                raise ValueError("invalid coach assessment")
            self.validate_pointer(assessment.skill_memory_id, assessment.player_id,
                                  {"initial_assessment", "coach_skill_belief"})
            event = self.validate_pointer(assessment.recency_memory_id, assessment.player_id,
                                          {"initial_assessment", "coach_recency"})
            if event.day != assessment.last_seen:
                raise ValueError("invalid observation date in recency memory")

    def validate_pointer(self, event_id, target, kinds):
        if type(event_id) is not int or not 1 <= event_id <= len(self.events):
            raise ValueError("invalid state pointer")
        event = self.events[event_id - 1]
        if event.target != target or event.kind not in kinds:
            raise ValueError("invalid state pointer target")
        return event
