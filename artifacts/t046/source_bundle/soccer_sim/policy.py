"""Deterministic policies over immutable private inputs and public coefficients."""
from dataclasses import asdict, dataclass

from .advice import coach_forecast

from .state import METHODS, CoachMind, CoachObservation, PlayerMind, PlayerObservation, PolicyRules

POLICY_VERSION = "t006-score-v2"


@dataclass(frozen=True)
class Candidate:
    action: str
    target: str | None
    method: str | None
    terms: tuple[tuple[str, float], ...]
    method_selection: dict | None = None

    @property
    def score(self):
        return sum(value for _, value in self.terms)

    def payload(self):
        result = {"action": self.action, "target": self.target, "method": self.method,
                  "terms": dict(self.terms), "score": self.score}
        if self.method_selection is not None:
            result["method_selection"] = self.method_selection
        return result


@dataclass(frozen=True)
class Decision:
    chosen: Candidate
    candidates: tuple[Candidate, ...]
    observation_ids: tuple[int, ...]
    memory_ids: tuple[int, ...]
    response: str | None = None

    def payload(self):
        return {"policy_version": POLICY_VERSION, "chosen": self.chosen.payload(),
                "candidates": [candidate.payload() for candidate in self.candidates],
                "observation_ids": self.observation_ids, "memory_ids": self.memory_ids,
                "proposal_response": self.response}


def decide_player(observation: PlayerObservation, mind: PlayerMind, config: PolicyRules) -> Decision:
    fatigue = observation.self_view.fatigue_signal
    proposal = observation.proposal
    candidates = [Candidate("rest", None, None,
                            (("recovery_value", config.rest_fatigue_weight * fatigue),))]
    for index, method in enumerate(METHODS):
        follows = proposal is not None and proposal.method == method
        advice = config.advice_weight * (mind.trust - mind.autonomy) if follows else 0.0
        candidates.append(Candidate(method, None, method, (
            ("expected_gain", mind.expected_gains[index]),
            ("preference", mind.preferences[index]),
            ("felt_fatigue_cost", -config.training_fatigue_weight * fatigue),
            ("advice", advice),
        )))
    chosen = max(candidates, key=lambda candidate: candidate.score)
    observations = (observation.self_view.event_id,)
    memories = mind.gain_memory_ids
    response = None
    if proposal is not None:
        observations += (proposal.event_id,)
        memories += (mind.trust_memory_id,)
        response = "accepted" if chosen.method == proposal.method else "rejected"
    return Decision(chosen, tuple(candidates), observations, tuple(dict.fromkeys(memories)), response)


def decide_coach(observation: CoachObservation, mind: CoachMind, config: PolicyRules) -> Decision:
    by_player = {assessment.player_id: assessment for assessment in mind.assessments}
    candidates = []
    memories = []
    adjustments = {item.player_id: item for item in mind.adjustments}
    for action, target, method in observation.available:
        selection = None
        if action == "rest":
            terms = (("rest_baseline", config.coach_rest_value),)
        else:
            assessment = by_player[target]
            memories.append(assessment.skill_memory_id)
            skill = assessment.skill_estimate
            if action == "observe":
                memories.append(assessment.recency_memory_id)
                staleness = min(1.0, (observation.day - assessment.last_seen)
                                / config.coach_freshness_days)
                terms = (("staleness", config.coach_observation_weight * staleness),
                         ("uncertain_development_need", config.coach_observation_need * (1.0 - skill)))
            else:
                method_fit = config.coach_method_fit * ((1.0 - skill) if method == "A" else skill)
                if config.coach_method_mode == "forecast_rank":
                    adjustment = adjustments[target]
                    forecasts = {name: coach_forecast(name, skill, mind.method_rates, adjustment)
                                 for name in METHODS}
                    q_a, q_b = (forecasts[name].expected_gain for name in METHODS)
                    tied = q_a == q_b
                    best = None if tied else ("A" if q_a > q_b else "B")
                    if not tied:
                        method_fit = (config.coach_method_fit * max(skill, 1.0 - skill)
                                      if method == best else 0.0)
                    selection = {
                        "mode": "forecast_rank", "basis": "tie_legacy" if tied else "forecast_rank",
                        "preferred_method": best, "perceived_skill": skill,
                        "method_rates": dict(zip(METHODS, mind.method_rates)),
                        "skill_memory_id": assessment.skill_memory_id,
                        "rate_memory_id": mind.forecast_memory_id,
                        "forecasts": {name: asdict(value) for name, value in forecasts.items()},
                    }
                    memories.extend((mind.forecast_memory_id, *adjustment.memory_ids))
                terms = (("development_need", config.coach_need_weight * (1.0 - skill)),
                         ("method_fit", method_fit))
        candidates.append(Candidate(action, target, method, terms, selection))
    chosen = max(candidates, key=lambda candidate: candidate.score)
    memory_ids = tuple(dict.fromkeys(memories))
    return Decision(chosen, tuple(candidates), (observation.event_id,), memory_ids)
