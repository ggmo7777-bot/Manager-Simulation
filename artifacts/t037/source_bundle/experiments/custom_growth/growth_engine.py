"""Independent Trait Growth Engine with Custom Curves.

This module is part of the custom growth experiments (isolated from official T-tasks).
It implements:
1. Trait-independent headroom r_j = (Cap - S_j) / (Cap - 0.15).
2. Four custom growth types: Early, Steady, Late, Burst.
3. Experience-dependent growth multiplier h(E) using sigmoid & gaussian forms.
4. Cumulative experience tracking E_j per trait.
5. Team training with individual rest threshold (Plan B).
"""

import math
from dataclasses import dataclass, field
from typing import Literal

GrowthType = Literal["early", "steady", "late", "burst"]

METHOD_TRAIT_WEIGHTS = {
    "A": {"pace": 0.45, "passing": 1.45, "shooting": 1.10},
    "B": {"pace": 1.45, "passing": 0.45, "shooting": 1.10},
}

GROWTH_CONFIGS = {
    "early": {"gain": 2.3, "gamma": 2.6},
    "steady": {"gain": 1.0, "gamma": 1.2},
    "late": {"gain": 1.0, "gamma": 1.8},
    "burst": {"gain": 1.0, "gamma": 1.5},
}


def calc_h_multiplier(kind: GrowthType, exp: float) -> float:
    """Experience-dependent growth rate multiplier h(E)."""
    if kind in ("early", "steady"):
        return 1.0
    if kind == "late":
        z = max(-60.0, min(60.0, (exp - 115.0) / 17.0))
        return 0.23 + 2.65 / (1.0 + math.exp(-z))
    if kind == "burst":
        z = (exp - 165.0) / 27.0
        return 0.6 + 3.0 * math.exp(-0.5 * (z ** 2))
    return 1.0


def calc_independent_headroom(skill: float, cap: float, min_val: float = 0.15) -> float:
    """Calculate independent growth headroom r_j in [0.0, 1.0]."""
    if cap <= min_val:
        return 0.0
    val = (cap - skill) / (cap - min_val)
    return max(0.0, min(1.0, val))


def calc_trait_delta(
    skill: float,
    cap: float,
    k_factor: float,
    weight: float,
    growth_type: GrowthType,
    exp: float,
) -> float:
    """Calculate Delta S_j = K * w * g * (r^gamma) * h(E)."""
    if skill >= cap:
        return 0.0
    r = calc_independent_headroom(skill, cap)
    cfg = GROWTH_CONFIGS[growth_type]
    g = cfg["gain"]
    gamma = cfg["gamma"]
    h = calc_h_multiplier(growth_type, exp)
    delta = k_factor * weight * g * (r ** gamma) * h
    return max(0.0, min(cap - skill, delta))


@dataclass
class TraitState:
    skill: float
    growth_type: GrowthType
    exp: float = 0.0


@dataclass
class CustomPlayer:
    player_id: str
    traits: dict[str, TraitState]
    cap: float = 0.90
    fatigue: float = 0.10
    rest_threshold: float = 0.75  # Plan B: rest if fatigue exceeds this

    def composite_skill(self) -> float:
        vals = [t.skill for t in self.traits.values()]
        return sum(vals) / len(vals)

    def step_training(
        self,
        method: str,
        k_base: float = 0.003,
        fatigue_inc: float = 0.075,
        rest_rec: float = 0.14,
    ) -> dict[str, float]:
        """Execute one day step under team training with individual rest (Plan B)."""
        # Plan B check: if fatigue >= rest_threshold, player rests individually
        if self.fatigue >= self.rest_threshold:
            self.fatigue = max(0.0, self.fatigue - rest_rec)
            return {"action": "rest", "fatigue": self.fatigue, "delta_avg": 0.0}

        # Otherwise execute team training
        weights = METHOD_TRAIT_WEIGHTS.get(method, {"pace": 1.0, "passing": 1.0, "shooting": 1.0})
        fatigue_factor = max(0.25, 1.0 - 0.55 * self.fatigue)
        k_factor = k_base * fatigue_factor

        deltas = {}
        for trait_name, trait_state in self.traits.items():
            w = weights.get(trait_name, 1.0)
            d = calc_trait_delta(
                skill=trait_state.skill,
                cap=self.cap,
                k_factor=k_factor,
                weight=w,
                growth_type=trait_state.growth_type,
                exp=trait_state.exp,
            )
            trait_state.skill = round(min(self.cap, trait_state.skill + d), 6)
            trait_state.exp = round(trait_state.exp + w, 4)
            deltas[trait_name] = d

        self.fatigue = min(0.95, round(self.fatigue + fatigue_inc, 6))
        return {
            "action": f"train_{method}",
            "fatigue": self.fatigue,
            "deltas": deltas,
            "delta_avg": sum(deltas.values()) / len(deltas),
        }

def calc_match_trait_delta(
    skill: float,
    cap: float,
    growth_type: GrowthType,
    exp: float,
    q_factor: float = 1.0,
) -> float:
    """Calculate match experience growth Delta S_j^match = 0.032 * (Cap - 0.15) * q * g * (r^gamma) * h(E)."""
    if skill >= cap:
        return 0.0
    r = calc_independent_headroom(skill, cap)
    cfg = GROWTH_CONFIGS[growth_type]
    g = cfg["gain"]
    gamma = cfg["gamma"]
    h = calc_h_multiplier(growth_type, exp)
    delta = 0.032 * (cap - 0.15) * q_factor * g * (r ** gamma) * h
    return max(0.0, min(cap - skill, delta))
