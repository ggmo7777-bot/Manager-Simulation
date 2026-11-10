"""Subjective advice forecasts and trust updates from private feedback."""
from dataclasses import dataclass

from .observation import clamp
from .state import METHODS, TrustRules

COACH_RATE_PRIOR = (0.10, 0.10)


def forecast_gain(method, perceived_skill, method_rates):
    return method_rates[METHODS.index(method)] * (1.0 - perceived_skill)


@dataclass(frozen=True)
class Feedback:
    signed_observed_gain: float
    self_loss: float
    coach_loss: float
    evidence: float
    trust_after: float


def evaluate_feedback(trust, before, after, q_self, q_coach, rules: TrustRules):
    observed = after - before
    self_loss = (observed - q_self) ** 2
    coach_loss = (observed - q_coach) ** 2
    evidence = max(-1.0, min(1.0, (self_loss - coach_loss) / rules.scale ** 2))
    trust_after = clamp(trust + rules.step * evidence) if rules.enabled else trust
    return Feedback(observed, self_loss, coach_loss, evidence, trust_after)
