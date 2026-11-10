"""Subjective advice forecasts and trust updates from private feedback."""
from dataclasses import dataclass, replace

from .observation import clamp, ema
from .state import METHODS, TrustRules

COACH_RATE_PRIOR = (0.10, 0.10)
COACH_RESIDUAL_ALPHA = 0.5


def forecast_gain(method, perceived_skill, method_rates):
    return method_rates[METHODS.index(method)] * (1.0 - perceived_skill)


@dataclass(frozen=True)
class CoachForecast:
    base_gain: float
    residual_before: float
    expected_gain: float
    residual_memory_id: int


def coach_forecast(method, perceived_skill, method_rates, adjustment):
    index = METHODS.index(method)
    base = forecast_gain(method, perceived_skill, method_rates)
    residual = adjustment.residuals[index]
    return CoachForecast(base, residual, max(-1.0, min(1.0, base + residual)),
                         adjustment.memory_ids[index])


def coach_experience(before, after, forecast, learn):
    """Evaluate the pre-action forecast against two private coach signals."""
    observed = after - before
    target = observed - forecast.base_gain
    residual = forecast.residual_before
    return {
        "signed_observed_gain": observed,
        "target_residual": target,
        "residual_after": ema(residual, target, COACH_RESIDUAL_ALPHA) if learn else residual,
        "base_squared_error": target ** 2,
        "forecast_squared_error": (observed - forecast.expected_gain) ** 2,
    }


def remember_coach_experience(adjustment, method, residual, memory_id):
    index = METHODS.index(method)
    residuals = list(adjustment.residuals)
    memories = list(adjustment.memory_ids)
    residuals[index] = residual
    memories[index] = memory_id
    return replace(adjustment, residuals=tuple(residuals), memory_ids=tuple(memories))


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
