"""T009: Minimal closed loop connecting development (A/B), coach lineup selection, and 2D match core.

Author: Antigravity (Gemini 4 Argon) — distinct from the creator of T001-T007 (Sol 6.1 High).
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

from . import match_2d as m2d
from .match_2d import (
    MatchConfig,
    MatchStyle,
    MatchTraits,
    PlayerMatchMind,
    PlayerMatchState,
    clamp,
    round6,
    uniform_from_key,
)
from .observation import RNG_VERSION
from .storage import (
    atomic_write,
    canonical,
    digest,
    identity as package_identity,
    parse_float,
    reject_nonfinite,
    runtime,
    write_json,
)

# Explicit author attribution (distinct from T001-T007 creator Sol 6.1 High)
SELECTION_AUTHOR = "Antigravity (Gemini 4 Argon)"
SELECTION_WORLD_KIND = "selection_loop"
SELECTION_SAVE_VERSION = 9
SELECTION_RULE_VERSION = "t009-selection-loop-v1"
SELECTION_POLICY_VERSION = "t009-selection-score-v1"

ROSTER_IDS = ("p1", "p2", "p3")
COACH_ID = "c1"
TRAINING_METHODS = ("A", "B")
PLAYER_TRAINING_ACTIONS = ("A", "B", "rest")
COACH_TRAINING_KINDS = ("observe", "propose", "rest")

COUPLING_MODES = ("decoupled", "coupled")
FEEDBACK_MODES = ("prior", "feedback")
SELECTION_POLICIES = ("merit", "rotation")

T009_MAIN_CONDITIONS = (
    "decoupled_prior",
    "coupled_prior",
    "decoupled_feedback",
    "coupled_feedback",
)
T009_EXTRA_CONDITIONS = (
    "rotation_feedback",
    "fair_prior_p3",
    "p1_pass_style_plus",
)
T009_ALL_CONDITIONS = T009_MAIN_CONDITIONS + T009_EXTRA_CONDITIONS

# Trait growth weights for Method A (technical/passing) and Method B (athletic/pace)
# Mean of each weight triple is 1.0: (0.45 + 1.45 + 1.10)/3 = 1.0
METHOD_TRAIT_WEIGHTS: dict[str, tuple[float, float, float]] = {
    "A": (0.45, 1.45, 1.10),
    "B": (1.45, 0.45, 1.10),
}


@dataclass(frozen=True)
class SelectionConfig:
    condition: str = "coupled_feedback"
    training_match_coupling: str = "coupled"
    match_feedback: str = "feedback"
    selection_policy: str = "merit"
    match_interval_days: int = 7
    match_ticks: int = 40
    coach_daily_slots: int = 2
    self_noise: float = 0.018
    coach_noise: float = 0.024
    match_obs_noise: float = 0.020
    match_noise_scale: float = 1.0
    player_skill_alpha: float = 0.32
    player_gain_alpha: float = 0.30
    coach_skill_alpha: float = 0.36
    coach_residual_alpha: float = 0.15
    coach_match_alpha: float = 0.35
    coach_match_skill_blend: float = 0.18
    trust_alpha: float = 0.16
    trust_scale: float = 18.0
    advice_weight: float = 0.055
    training_fatigue_weight: float = 0.095
    rest_fatigue_weight: float = 0.125
    satisfaction_drive_weight: float = 0.022
    coach_need_weight: float = 0.42
    coach_method_fit: float = 0.24
    coach_staleness_weight: float = 0.018
    coach_rest_value: float = 0.16
    select_skill_weight: float = 0.55
    select_match_weight: float = 0.48
    select_fatigue_weight: float = 0.22
    select_rotation_weight: float = 0.065
    fair_p3_prior: bool = False
    p1_pass_style_boost: bool = False
    policy_version: str = SELECTION_POLICY_VERSION

    def validate(self) -> None:
        if self.condition not in T009_ALL_CONDITIONS:
            raise ValueError(f"unknown selection condition: {self.condition}")
        if self.training_match_coupling not in COUPLING_MODES:
            raise ValueError(f"invalid training_match_coupling: {self.training_match_coupling}")
        if self.match_feedback not in FEEDBACK_MODES:
            raise ValueError(f"invalid match_feedback: {self.match_feedback}")
        if self.selection_policy not in SELECTION_POLICIES:
            raise ValueError(f"invalid selection_policy: {self.selection_policy}")
        if self.match_interval_days < 2 or self.match_ticks < 4 or self.coach_daily_slots != 2:
            raise ValueError("invalid schedule or slot config")
        if self.policy_version != SELECTION_POLICY_VERSION:
            raise ValueError(f"unsupported policy_version: {self.policy_version}")
        for val in (
            self.self_noise,
            self.coach_noise,
            self.match_obs_noise,
            self.match_noise_scale,
            self.player_skill_alpha,
            self.player_gain_alpha,
            self.coach_skill_alpha,
            self.coach_residual_alpha,
            self.coach_match_alpha,
            self.coach_match_skill_blend,
            self.trust_alpha,
            self.trust_scale,
            self.advice_weight,
            self.training_fatigue_weight,
            self.rest_fatigue_weight,
            self.satisfaction_drive_weight,
            self.coach_need_weight,
            self.coach_method_fit,
            self.coach_staleness_weight,
            self.coach_rest_value,
            self.select_skill_weight,
            self.select_match_weight,
            self.select_fatigue_weight,
            self.select_rotation_weight,
        ):
            if not (isinstance(val, (int, float)) and 0.0 <= float(val) <= 50.0):
                raise ValueError("invalid numeric parameter in SelectionConfig")

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition": self.condition,
            "training_match_coupling": self.training_match_coupling,
            "match_feedback": self.match_feedback,
            "selection_policy": self.selection_policy,
            "match_interval_days": self.match_interval_days,
            "match_ticks": self.match_ticks,
            "coach_daily_slots": self.coach_daily_slots,
            "self_noise": round6(self.self_noise),
            "coach_noise": round6(self.coach_noise),
            "match_obs_noise": round6(self.match_obs_noise),
            "match_noise_scale": round6(self.match_noise_scale),
            "player_skill_alpha": round6(self.player_skill_alpha),
            "player_gain_alpha": round6(self.player_gain_alpha),
            "coach_skill_alpha": round6(self.coach_skill_alpha),
            "coach_residual_alpha": round6(self.coach_residual_alpha),
            "coach_match_alpha": round6(self.coach_match_alpha),
            "coach_match_skill_blend": round6(self.coach_match_skill_blend),
            "trust_alpha": round6(self.trust_alpha),
            "trust_scale": round6(self.trust_scale),
            "advice_weight": round6(self.advice_weight),
            "training_fatigue_weight": round6(self.training_fatigue_weight),
            "rest_fatigue_weight": round6(self.rest_fatigue_weight),
            "satisfaction_drive_weight": round6(self.satisfaction_drive_weight),
            "coach_need_weight": round6(self.coach_need_weight),
            "coach_method_fit": round6(self.coach_method_fit),
            "coach_staleness_weight": round6(self.coach_staleness_weight),
            "coach_rest_value": round6(self.coach_rest_value),
            "select_skill_weight": round6(self.select_skill_weight),
            "select_match_weight": round6(self.select_match_weight),
            "select_fatigue_weight": round6(self.select_fatigue_weight),
            "select_rotation_weight": round6(self.select_rotation_weight),
            "fair_p3_prior": bool(self.fair_p3_prior),
            "p1_pass_style_boost": bool(self.p1_pass_style_boost),
            "policy_version": self.policy_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SelectionConfig:
        cfg = cls(
            condition=str(data["condition"]),
            training_match_coupling=str(data["training_match_coupling"]),
            match_feedback=str(data["match_feedback"]),
            selection_policy=str(data["selection_policy"]),
            match_interval_days=int(data["match_interval_days"]),
            match_ticks=int(data["match_ticks"]),
            coach_daily_slots=int(data["coach_daily_slots"]),
            self_noise=float(data["self_noise"]),
            coach_noise=float(data["coach_noise"]),
            match_obs_noise=float(data["match_obs_noise"]),
            match_noise_scale=float(data["match_noise_scale"]),
            player_skill_alpha=float(data["player_skill_alpha"]),
            player_gain_alpha=float(data["player_gain_alpha"]),
            coach_skill_alpha=float(data["coach_skill_alpha"]),
            coach_residual_alpha=float(data["coach_residual_alpha"]),
            coach_match_alpha=float(data["coach_match_alpha"]),
            coach_match_skill_blend=float(data["coach_match_skill_blend"]),
            trust_alpha=float(data["trust_alpha"]),
            trust_scale=float(data["trust_scale"]),
            advice_weight=float(data["advice_weight"]),
            training_fatigue_weight=float(data["training_fatigue_weight"]),
            rest_fatigue_weight=float(data["rest_fatigue_weight"]),
            satisfaction_drive_weight=float(data["satisfaction_drive_weight"]),
            coach_need_weight=float(data["coach_need_weight"]),
            coach_method_fit=float(data["coach_method_fit"]),
            coach_staleness_weight=float(data["coach_staleness_weight"]),
            coach_rest_value=float(data["coach_rest_value"]),
            select_skill_weight=float(data["select_skill_weight"]),
            select_match_weight=float(data["select_match_weight"]),
            select_fatigue_weight=float(data["select_fatigue_weight"]),
            select_rotation_weight=float(data["select_rotation_weight"]),
            fair_p3_prior=bool(data.get("fair_p3_prior", False)),
            p1_pass_style_boost=bool(data.get("p1_pass_style_boost", False)),
            policy_version=str(data.get("policy_version", SELECTION_POLICY_VERSION)),
        )
        cfg.validate()
        return cfg


def config_for_condition(condition: str) -> SelectionConfig:
    if condition == "decoupled_prior":
        return SelectionConfig(
            condition=condition,
            training_match_coupling="decoupled",
            match_feedback="prior",
            selection_policy="merit",
        )
    if condition == "coupled_prior":
        return SelectionConfig(
            condition=condition,
            training_match_coupling="coupled",
            match_feedback="prior",
            selection_policy="merit",
        )
    if condition == "decoupled_feedback":
        return SelectionConfig(
            condition=condition,
            training_match_coupling="decoupled",
            match_feedback="feedback",
            selection_policy="merit",
        )
    if condition == "coupled_feedback":
        return SelectionConfig(
            condition=condition,
            training_match_coupling="coupled",
            match_feedback="feedback",
            selection_policy="merit",
        )
    if condition == "rotation_feedback":
        return SelectionConfig(
            condition=condition,
            training_match_coupling="coupled",
            match_feedback="feedback",
            selection_policy="rotation",
        )
    if condition == "fair_prior_p3":
        return SelectionConfig(
            condition=condition,
            training_match_coupling="coupled",
            match_feedback="feedback",
            selection_policy="merit",
            fair_p3_prior=True,
        )
    if condition == "p1_pass_style_plus":
        return SelectionConfig(
            condition=condition,
            training_match_coupling="coupled",
            match_feedback="feedback",
            selection_policy="merit",
            p1_pass_style_boost=True,
        )
    raise ValueError(f"unknown T009 condition: {condition}")


@dataclass
class PlayerSelectionMind:
    player_id: str
    skill_estimate: float
    expected_gains: tuple[float, float]
    preferences: tuple[float, float]
    autonomy: float
    trust: float
    style: MatchStyle
    appearances: int
    bench_streak: int
    playing_time_satisfaction: float
    self_match_rating: float
    skill_memory_id: int
    gain_memory_ids: tuple[int, int]
    trust_memory_id: int
    career_memory_id: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "player_id": self.player_id,
            "skill_estimate": round6(self.skill_estimate),
            "expected_gains": [round6(x) for x in self.expected_gains],
            "preferences": [round6(x) for x in self.preferences],
            "autonomy": round6(self.autonomy),
            "trust": round6(self.trust),
            "style": self.style.to_dict(),
            "appearances": int(self.appearances),
            "bench_streak": int(self.bench_streak),
            "playing_time_satisfaction": round6(self.playing_time_satisfaction),
            "self_match_rating": round6(self.self_match_rating),
            "skill_memory_id": int(self.skill_memory_id),
            "gain_memory_ids": [int(x) for x in self.gain_memory_ids],
            "trust_memory_id": int(self.trust_memory_id),
            "career_memory_id": int(self.career_memory_id),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlayerSelectionMind:
        eg = tuple(float(x) for x in data["expected_gains"])
        pr = tuple(float(x) for x in data["preferences"])
        gm = tuple(int(x) for x in data["gain_memory_ids"])
        if len(eg) != 2 or len(pr) != 2 or len(gm) != 2:
            raise ValueError("expected length-2 tuples in PlayerSelectionMind")
        return cls(
            player_id=str(data["player_id"]),
            skill_estimate=float(data["skill_estimate"]),
            expected_gains=(eg[0], eg[1]),
            preferences=(pr[0], pr[1]),
            autonomy=float(data["autonomy"]),
            trust=float(data["trust"]),
            style=MatchStyle.from_dict(dict(data["style"])),
            appearances=int(data["appearances"]),
            bench_streak=int(data["bench_streak"]),
            playing_time_satisfaction=float(data["playing_time_satisfaction"]),
            self_match_rating=float(data["self_match_rating"]),
            skill_memory_id=int(data["skill_memory_id"]),
            gain_memory_ids=(gm[0], gm[1]),
            trust_memory_id=int(data["trust_memory_id"]),
            career_memory_id=int(data["career_memory_id"]),
        )


@dataclass
class SelectionPlayer:
    player_id: str
    traits: MatchTraits
    initial_traits: MatchTraits
    cap: float
    rates: tuple[float, float]
    fatigue: float
    mind: PlayerSelectionMind
    last_state_id: int

    @property
    def mean_skill(self) -> float:
        return round6((self.traits.pace + self.traits.passing + self.traits.shooting) / 3.0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "player_id": self.player_id,
            "traits": self.traits.to_dict(),
            "initial_traits": self.initial_traits.to_dict(),
            "mean_skill": self.mean_skill,
            "cap": round6(self.cap),
            "rates": [round6(x) for x in self.rates],
            "fatigue": round6(self.fatigue),
            "mind": self.mind.to_dict(),
            "last_state_id": int(self.last_state_id),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SelectionPlayer:
        rates = tuple(float(x) for x in data["rates"])
        if len(rates) != 2:
            raise ValueError("rates must have length 2")
        player = cls(
            player_id=str(data["player_id"]),
            traits=MatchTraits.from_dict(dict(data["traits"])),
            initial_traits=MatchTraits.from_dict(dict(data["initial_traits"])),
            cap=float(data["cap"]),
            rates=(rates[0], rates[1]),
            fatigue=float(data["fatigue"]),
            mind=PlayerSelectionMind.from_dict(dict(data["mind"])),
            last_state_id=int(data["last_state_id"]),
        )
        if "mean_skill" in data and abs(float(data["mean_skill"]) - player.mean_skill) > 1e-5:
            raise ValueError("mean_skill does not match traits")
        return player


@dataclass
class CoachPlayerAssessment:
    player_id: str
    skill_estimate: float
    fatigue_estimate: float
    match_rating_estimate: float
    last_seen: int
    last_match_seen: int
    perceived_bench_streak: int
    residuals: tuple[float, float]
    skill_memory_id: int
    recency_memory_id: int
    match_memory_id: int
    residual_memory_ids: tuple[int, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "player_id": self.player_id,
            "skill_estimate": round6(self.skill_estimate),
            "fatigue_estimate": round6(self.fatigue_estimate),
            "match_rating_estimate": round6(self.match_rating_estimate),
            "last_seen": int(self.last_seen),
            "last_match_seen": int(self.last_match_seen),
            "perceived_bench_streak": int(self.perceived_bench_streak),
            "residuals": [round6(x) for x in self.residuals],
            "skill_memory_id": int(self.skill_memory_id),
            "recency_memory_id": int(self.recency_memory_id),
            "match_memory_id": int(self.match_memory_id),
            "residual_memory_ids": [int(x) for x in self.residual_memory_ids],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CoachPlayerAssessment:
        res = tuple(float(x) for x in data["residuals"])
        rm = tuple(int(x) for x in data["residual_memory_ids"])
        if len(res) != 2 or len(rm) != 2:
            raise ValueError("residuals must have length 2")
        return cls(
            player_id=str(data["player_id"]),
            skill_estimate=float(data["skill_estimate"]),
            fatigue_estimate=float(data["fatigue_estimate"]),
            match_rating_estimate=float(data["match_rating_estimate"]),
            last_seen=int(data["last_seen"]),
            last_match_seen=int(data["last_match_seen"]),
            perceived_bench_streak=int(data["perceived_bench_streak"]),
            residuals=(res[0], res[1]),
            skill_memory_id=int(data["skill_memory_id"]),
            recency_memory_id=int(data["recency_memory_id"]),
            match_memory_id=int(data["match_memory_id"]),
            residual_memory_ids=(rm[0], rm[1]),
        )


@dataclass
class CoachSelectionMind:
    coach_id: str
    method_rates: tuple[float, float]
    forecast_memory_id: int
    assessments: dict[str, CoachPlayerAssessment]

    def to_dict(self) -> dict[str, Any]:
        return {
            "coach_id": self.coach_id,
            "method_rates": [round6(x) for x in self.method_rates],
            "forecast_memory_id": int(self.forecast_memory_id),
            "assessments": {pid: self.assessments[pid].to_dict() for pid in ROSTER_IDS},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CoachSelectionMind:
        mr = tuple(float(x) for x in data["method_rates"])
        if len(mr) != 2:
            raise ValueError("method_rates must have length 2")
        raw_assess = dict(data["assessments"])
        if set(raw_assess.keys()) != set(ROSTER_IDS):
            raise ValueError("coach assessments must cover ROSTER_IDS")
        return cls(
            coach_id=str(data["coach_id"]),
            method_rates=(mr[0], mr[1]),
            forecast_memory_id=int(data["forecast_memory_id"]),
            assessments={pid: CoachPlayerAssessment.from_dict(dict(raw_assess[pid])) for pid in ROSTER_IDS},
        )


@dataclass(frozen=True)
class TrainingProposal:
    coach_id: str
    event_id: int
    target: str
    method: str
    expected_gain: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "coach_id": self.coach_id,
            "event_id": int(self.event_id),
            "target": self.target,
            "method": self.method,
            "expected_gain": round6(self.expected_gain),
        }


@dataclass(frozen=True)
class PlayerTrainingObservation:
    player_id: str
    day: int
    event_id: int
    skill_signal: float
    fatigue_signal: float
    proposals: tuple[TrainingProposal, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "player_id": self.player_id,
            "day": int(self.day),
            "event_id": int(self.event_id),
            "skill_signal": round6(self.skill_signal),
            "fatigue_signal": round6(self.fatigue_signal),
            "proposals": [p.to_dict() for p in self.proposals],
        }


def coach_method_forecast(
    assessment: CoachPlayerAssessment,
    method_rates: tuple[float, float],
    method: str,
) -> dict[str, Any]:
    idx = TRAINING_METHODS.index(method)
    base_rate = method_rates[idx]
    est_skill = assessment.skill_estimate
    est_fatigue = assessment.fatigue_estimate
    # Subjective diminishing-returns forecast using only coach private beliefs
    base_gain = round6(base_rate * (1.0 - est_skill / 0.90) * (1.0 - 0.55 * est_fatigue) * 0.25)
    residual = assessment.residuals[idx]
    expected_gain = round6(clamp(base_gain + residual, -1.0, 1.0))
    return {
        "method": method,
        "base_gain": base_gain,
        "residual": round6(residual),
        "expected_gain": expected_gain,
        "residual_memory_id": assessment.residual_memory_ids[idx],
    }


def decide_coach_training(
    mind: CoachSelectionMind,
    day: int,
    slot: int,
    already_chosen: tuple[tuple[str, str | None], ...],
    config: SelectionConfig,
) -> dict[str, Any]:
    """Pure private-input coach training policy (never reads World truth)."""
    if not isinstance(mind, CoachSelectionMind):
        raise TypeError("decide_coach_training requires CoachSelectionMind")
    used_observe = {target for kind, target in already_chosen if kind == "observe"}
    used_propose = {target for kind, target in already_chosen if kind == "propose"}

    candidates: list[dict[str, Any]] = []
    memory_ids: list[int] = [mind.forecast_memory_id]

    for pid in ROSTER_IDS:
        assess = mind.assessments[pid]
        memory_ids.extend(
            [
                assess.skill_memory_id,
                assess.recency_memory_id,
                assess.match_memory_id,
                *assess.residual_memory_ids,
            ]
        )
        staleness = max(0, day - assess.last_seen)
        need = round6(config.coach_need_weight * (1.0 - assess.skill_estimate))
        starter_focus = round6(0.34 * assess.match_rating_estimate)

        if pid not in used_observe:
            obs_value = round6(0.04 + config.coach_staleness_weight * min(8, staleness) + starter_focus)
            obs_score = round6(obs_value + 0.12 * need)
            candidates.append(
                {
                    "kind": "observe",
                    "target": pid,
                    "method": None,
                    "score": obs_score,
                    "components": {
                        "staleness_and_focus": obs_value,
                        "development_need": round6(0.12 * need),
                    },
                    "forecasts": None,
                }
            )

        if pid not in used_propose:
            f_a = coach_method_forecast(assess, mind.method_rates, "A")
            f_b = coach_method_forecast(assess, mind.method_rates, "B")
            forecasts = {"A": f_a, "B": f_b}
            max_fit = round6(config.coach_method_fit * max(assess.skill_estimate, 1.0 - assess.skill_estimate))
            freshness_bonus = round6(0.03 * max(0.0, 1.0 - staleness / 7.0))
            fatigue_Drag = round6(-0.08 * assess.fatigue_estimate)

            for m_idx, method in enumerate(TRAINING_METHODS):
                other = "B" if method == "A" else "A"
                if forecasts[method]["expected_gain"] > forecasts[other]["expected_gain"]:
                    method_term = max_fit
                elif forecasts[method]["expected_gain"] < forecasts[other]["expected_gain"]:
                    method_term = 0.0
                else:
                    legacy_fit = assess.skill_estimate if method == "B" else (1.0 - assess.skill_estimate)
                    method_term = round6(config.coach_method_fit * legacy_fit)

                prop_score = round6(0.45 * need + starter_focus + method_term + freshness_bonus + fatigue_Drag)
                candidates.append(
                    {
                        "kind": "propose",
                        "target": pid,
                        "method": method,
                        "score": prop_score,
                        "components": {
                            "development_need": round6(0.45 * need),
                            "starter_focus": starter_focus,
                            "method_term": method_term,
                            "freshness_bonus": freshness_bonus,
                            "fatigue_drag": fatigue_Drag,
                        },
                        "forecasts": forecasts,
                    }
                )

    candidates.append(
        {
            "kind": "rest",
            "target": None,
            "method": None,
            "score": round6(config.coach_rest_value),
            "components": {"rest_value": round6(config.coach_rest_value)},
            "forecasts": None,
        }
    )

    chosen = max(candidates, key=lambda c: c["score"])
    unique_memories = tuple(dict.fromkeys(memory_ids))
    return {
        "policy_version": config.policy_version,
        "slot": int(slot),
        "chosen": chosen,
        "candidates": candidates,
        "memory_ids": list(unique_memories),
    }


def decide_player_training(
    observation: PlayerTrainingObservation,
    mind: PlayerSelectionMind,
    config: SelectionConfig,
) -> dict[str, Any]:
    """Pure private-input player training policy (never reads World truth)."""
    if not isinstance(observation, PlayerTrainingObservation) or not isinstance(mind, PlayerSelectionMind):
        raise TypeError("decide_player_training requires PlayerTrainingObservation and PlayerSelectionMind")
    fatigue = observation.fatigue_signal
    proposals = tuple(sorted(observation.proposals, key=lambda p: (p.coach_id, p.method)))

    if config.match_feedback == "feedback":
        drive_bonus = round6(
            config.satisfaction_drive_weight * max(0.0, 0.50 - mind.playing_time_satisfaction)
        )
        effective_autonomy = round6(
            clamp(mind.autonomy + 0.18 * max(0.0, 0.45 - mind.playing_time_satisfaction), 0.0, 1.0)
        )
    else:
        drive_bonus = 0.0
        effective_autonomy = round6(mind.autonomy)

    rest_score = round6(config.rest_fatigue_weight * fatigue)
    candidates: list[dict[str, Any]] = [
        {
            "action": "rest",
            "method": None,
            "score": rest_score,
            "components": {
                "recovery_value": rest_score,
            },
        }
    ]

    for idx, method in enumerate(TRAINING_METHODS):
        matching = [p for p in proposals if p.method == method]
        if matching:
            advice_term = round6(config.advice_weight * (mind.trust - effective_autonomy))
        else:
            advice_term = 0.0
        exp_gain = round6(mind.expected_gains[idx])
        pref = round6(mind.preferences[idx])
        fatigue_cost = round6(-config.training_fatigue_weight * fatigue)
        total = round6(exp_gain + pref + fatigue_cost + advice_term + drive_bonus)
        candidates.append(
            {
                "action": method,
                "method": method,
                "score": total,
                "components": {
                    "expected_gain": exp_gain,
                    "preference": pref,
                    "felt_fatigue_cost": fatigue_cost,
                    "advice": advice_term,
                    "playing_time_drive": drive_bonus,
                },
            }
        )

    chosen = max(candidates, key=lambda c: c["score"])
    responses = [
        {
            "coach_id": p.coach_id,
            "proposal_id": p.event_id,
            "proposed_method": p.method,
            "expected_gain": p.expected_gain,
            "accepted": chosen["action"] == p.method,
            "feedback_eligible": chosen["action"] == p.method,
        }
        for p in proposals
    ]
    memory_ids = list(
        dict.fromkeys(
            [
                mind.skill_memory_id,
                *mind.gain_memory_ids,
                mind.trust_memory_id,
                mind.career_memory_id,
            ]
        )
    )
    obs_ids = [observation.event_id, *[p.event_id for p in proposals]]
    return {
        "policy_version": config.policy_version,
        "effective_autonomy": effective_autonomy,
        "playing_time_drive": drive_bonus,
        "chosen": chosen,
        "candidates": candidates,
        "proposal_responses": responses,
        "observation_ids": obs_ids,
        "memory_ids": memory_ids,
    }


def decide_lineup(
    mind: CoachSelectionMind,
    day: int,
    config: SelectionConfig,
) -> dict[str, Any]:
    """Pure private-input coach lineup selection policy (never reads World truth)."""
    if not isinstance(mind, CoachSelectionMind):
        raise TypeError("decide_lineup requires CoachSelectionMind")
    w_skill = config.select_skill_weight
    w_match = config.select_match_weight
    w_fatigue = config.select_fatigue_weight
    w_rot = config.select_rotation_weight if config.selection_policy == "rotation" else 0.0

    player_scores: list[dict[str, Any]] = []
    memory_ids: list[int] = [mind.forecast_memory_id]

    for pid in ROSTER_IDS:
        assess = mind.assessments[pid]
        memory_ids.extend(
            [
                assess.skill_memory_id,
                assess.recency_memory_id,
                assess.match_memory_id,
            ]
        )
        skill_term = round6(w_skill * assess.skill_estimate)
        match_term = round6(w_match * assess.match_rating_estimate)
        fatigue_term = round6(-w_fatigue * assess.fatigue_estimate)
        rot_term = round6(w_rot * min(6, assess.perceived_bench_streak))
        total = round6(skill_term + match_term + fatigue_term + rot_term)
        player_scores.append(
            {
                "player_id": pid,
                "score": total,
                "components": {
                    "skill_term": skill_term,
                    "match_rating_term": match_term,
                    "fatigue_penalty": fatigue_term,
                    "rotation_bonus": rot_term,
                },
                "beliefs_used": {
                    "skill_estimate": round6(assess.skill_estimate),
                    "match_rating_estimate": round6(assess.match_rating_estimate),
                    "fatigue_estimate": round6(assess.fatigue_estimate),
                    "perceived_bench_streak": int(assess.perceived_bench_streak),
                },
            }
        )

    ranked = sorted(player_scores, key=lambda r: (-r["score"], r["player_id"]))
    starters = [ranked[0]["player_id"], ranked[1]["player_id"]]
    bench = ranked[2]["player_id"]
    return {
        "policy_version": config.policy_version,
        "selection_day": int(day),
        "starters": starters,
        "bench": bench,
        "ranked_candidates": ranked,
        "memory_ids": list(dict.fromkeys(memory_ids)),
    }


@dataclass
class SelectionWorldState:
    seed: int
    config: SelectionConfig
    day: int = 0
    players: dict[str, SelectionPlayer] = field(default_factory=dict)
    coach: CoachSelectionMind | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    match_summaries: list[dict[str, Any]] = field(default_factory=list)

    def record(
        self,
        day: int,
        phase: str,
        kind: str,
        actor: str,
        target: str | None = None,
        causes: tuple[int, ...] | list[int] = (),
        **data: Any,
    ) -> int:
        event_id = len(self.events) + 1
        cause_list = [int(c) for c in causes]
        for c in cause_list:
            if c < 1 or c >= event_id:
                raise ValueError(f"invalid cause reference {c} for event {event_id}")
        entry = {
            "event_id": event_id,
            "day": int(day),
            "phase": str(phase),
            "kind": str(kind),
            "actor": str(actor),
            "target": str(target) if target is not None else None,
            "causes": cause_list,
            "data": data,
        }
        self.events.append(entry)
        return event_id

    def to_dict(self, *, include_events: bool = True) -> dict[str, Any]:
        if self.coach is None:
            raise ValueError("coach state missing")
        payload: dict[str, Any] = {
            "seed": int(self.seed),
            "day": int(self.day),
            "config": self.config.to_dict(),
            "players": {pid: self.players[pid].to_dict() for pid in ROSTER_IDS},
            "coach": self.coach.to_dict(),
            "match_summaries": list(self.match_summaries),
        }
        if include_events:
            payload["events"] = list(self.events)
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SelectionWorldState:
        config = SelectionConfig.from_dict(dict(data["config"]))
        raw_players = dict(data["players"])
        if set(raw_players.keys()) != set(ROSTER_IDS):
            raise ValueError("players must match ROSTER_IDS")
        players = {pid: SelectionPlayer.from_dict(dict(raw_players[pid])) for pid in ROSTER_IDS}
        coach = CoachSelectionMind.from_dict(dict(data["coach"]))
        events = [dict(e) for e in data.get("events", [])]
        match_summaries = [dict(m) for m in data.get("match_summaries", [])]
        world = cls(
            seed=int(data["seed"]),
            config=config,
            day=int(data["day"]),
            players=players,
            coach=coach,
            events=events,
            match_summaries=match_summaries,
        )
        validate_selection_world(world, check_history=True)
        return world


def create_selection_world(
    seed: int,
    condition: str = "coupled_feedback",
    *,
    config: SelectionConfig | None = None,
) -> SelectionWorldState:
    cfg = config or config_for_condition(condition)
    cfg.validate()
    world = SelectionWorldState(seed=int(seed), config=cfg, day=0)

    p1_style = (
        MatchStyle(directness=0.36, pass_bias=0.84, press_bias=0.48)
        if cfg.p1_pass_style_boost
        else MatchStyle(directness=0.52, pass_bias=0.62, press_bias=0.48)
    )
    player_specs = (
        (
            "p1",
            MatchTraits(pace=0.45, passing=0.48, shooting=0.45),
            0.88,
            (0.115, 0.045),
            0.08,
            0.45,
            (0.046, 0.042),
            (0.014, 0.010),
            0.28,
            0.70,
            p1_style,
            0.52,
        ),
        (
            "p2",
            MatchTraits(pace=0.46, passing=0.42, shooting=0.47),
            0.88,
            (0.050, 0.115),
            0.08,
            0.44,
            (0.042, 0.046),
            (0.010, 0.015),
            0.32,
            0.66,
            MatchStyle(directness=0.64, pass_bias=0.44, press_bias=0.56),
            0.51,
        ),
        (
            "p3",
            MatchTraits(pace=0.43, passing=0.43, shooting=0.43),
            0.93,
            (0.145, 0.135),
            0.08,
            0.42,
            (0.018, 0.018),
            (0.006, 0.006),
            0.26,
            0.72,
            MatchStyle(directness=0.58, pass_bias=0.56, press_bias=0.55),
            0.48,
        ),
    )

    for (
        pid,
        traits,
        cap,
        rates,
        fatigue,
        skill_est,
        exp_gains,
        prefs,
        autonomy,
        trust,
        style,
        self_match_rating,
    ) in player_specs:
        init_id = len(world.events) + 1
        trust_id = init_id + 1
        career_id = init_id + 2
        mind = PlayerSelectionMind(
            player_id=pid,
            skill_estimate=skill_est,
            expected_gains=exp_gains,
            preferences=prefs,
            autonomy=autonomy,
            trust=trust,
            style=style,
            appearances=0,
            bench_streak=0,
            playing_time_satisfaction=0.55,
            self_match_rating=self_match_rating,
            skill_memory_id=init_id,
            gain_memory_ids=(init_id, init_id),
            trust_memory_id=trust_id,
            career_memory_id=career_id,
        )
        player = SelectionPlayer(
            player_id=pid,
            traits=traits,
            initial_traits=traits,
            cap=cap,
            rates=rates,
            fatigue=fatigue,
            mind=mind,
            last_state_id=init_id,
        )
        world.record(
            0,
            "setup",
            "initial_player",
            pid,
            pid,
            state=player.to_dict(),
        )
        world.record(
            0,
            "setup",
            "initial_trust",
            pid,
            pid,
            (init_id,),
            coach_id=COACH_ID,
            trust_after=round6(trust),
        )
        world.record(
            0,
            "setup",
            "initial_career",
            pid,
            pid,
            (init_id,),
            appearances=0,
            bench_streak=0,
            playing_time_satisfaction=0.55,
            self_match_rating=round6(self_match_rating),
        )
        world.players[pid] = player

    method_rates = (0.105, 0.100)
    prior_id = world.record(
        0,
        "setup",
        "coach_forecast_prior",
        COACH_ID,
        COACH_ID,
        method_rates=[round6(x) for x in method_rates],
    )
    p3_skill_prior = 0.47 if cfg.fair_p3_prior else 0.32
    p3_match_prior = 0.53 if cfg.fair_p3_prior else 0.30
    coach_priors = (
        ("p1", 0.47, 0.08, 0.52),
        ("p2", 0.46, 0.08, 0.51),
        ("p3", p3_skill_prior, 0.08, p3_match_prior),
    )
    assessments: dict[str, CoachPlayerAssessment] = {}
    for pid, s_est, f_est, m_est in coach_priors:
        assess_id = world.record(
            0,
            "setup",
            "initial_assessment",
            COACH_ID,
            pid,
            skill_estimate=round6(s_est),
            fatigue_estimate=round6(f_est),
            last_seen=0,
        )
        match_id = world.record(
            0,
            "setup",
            "initial_match_assessment",
            COACH_ID,
            pid,
            (assess_id,),
            match_rating_estimate=round6(m_est),
            last_match_seen=0,
            perceived_bench_streak=0,
        )
        res_id = world.record(
            0,
            "setup",
            "initial_coach_adjustment",
            COACH_ID,
            pid,
            (prior_id,),
            residuals=[0.0, 0.0],
        )
        assessments[pid] = CoachPlayerAssessment(
            player_id=pid,
            skill_estimate=s_est,
            fatigue_estimate=f_est,
            match_rating_estimate=m_est,
            last_seen=0,
            last_match_seen=0,
            perceived_bench_streak=0,
            residuals=(0.0, 0.0),
            skill_memory_id=assess_id,
            recency_memory_id=assess_id,
            match_memory_id=match_id,
            residual_memory_ids=(res_id, res_id),
        )
    world.coach = CoachSelectionMind(
        coach_id=COACH_ID,
        method_rates=method_rates,
        forecast_memory_id=prior_id,
        assessments=assessments,
    )
    world.record(0, "setup", "selection_controls", COACH_ID, None, **cfg.to_dict())
    validate_selection_world(world, check_history=True)
    return world


def _step_training_day(world: SelectionWorldState, day: int) -> None:
    assert world.coach is not None
    cfg = world.config
    coach = world.coach

    # 1. Morning player self-observations
    morning_obs: dict[str, tuple[int, float, float]] = {}
    for pid in ROSTER_IDS:
        player = world.players[pid]
        s_noise = uniform_from_key(
            world.seed, day, "self_skill_obs", pid, low=-cfg.self_noise, high=cfg.self_noise
        )
        f_noise = uniform_from_key(
            world.seed, day, "self_fatigue_obs", pid, low=-cfg.self_noise, high=cfg.self_noise
        )
        s_sig = round6(clamp(player.mean_skill + s_noise, 0.0, 1.0))
        f_sig = round6(clamp(player.fatigue + f_noise, 0.0, 1.0))
        obs_id = world.record(
            day,
            "morning",
            "self_observation",
            pid,
            pid,
            (player.last_state_id,),
            skill_signal=s_sig,
            fatigue_signal=f_sig,
        )
        morning_obs[pid] = (obs_id, s_sig, f_sig)

    # 2. Coach 2 daily slots (observe / propose / rest)
    already_chosen: list[tuple[str, str | None]] = []
    reserved_observations: dict[str, dict[str, Any]] = {}
    proposals_by_player: dict[str, list[TrainingProposal]] = {pid: [] for pid in ROSTER_IDS}
    coach_slot_actions: list[dict[str, Any]] = []

    for slot in range(1, cfg.coach_daily_slots + 1):
        decision = decide_coach_training(
            coach, day, slot, tuple(already_chosen), cfg
        )
        chosen = decision["chosen"]
        dec_id = world.record(
            day,
            "coach",
            "coach_training_decision",
            COACH_ID,
            chosen["target"] or COACH_ID,
            decision["memory_ids"],
            **decision,
        )
        kind = chosen["kind"]
        target = chosen["target"]
        already_chosen.append((kind, target))
        coach_slot_actions.append({"slot": slot, "kind": kind, "target": target, "method": chosen["method"]})

        if kind == "observe" and target is not None:
            player = world.players[target]
            assess = coach.assessments[target]
            pre_noise = uniform_from_key(
                world.seed, day, f"coach_pre_obs_{slot}", target, low=-cfg.coach_noise, high=cfg.coach_noise
            )
            f_noise = uniform_from_key(
                world.seed, day, f"coach_fat_obs_{slot}", target, low=-cfg.coach_noise, high=cfg.coach_noise
            )
            pre_sig = round6(clamp(player.mean_skill + pre_noise, 0.0, 1.0))
            fat_sig = round6(clamp(player.fatigue + f_noise, 0.0, 1.0))
            pre_forecasts = {
                m: coach_method_forecast(assess, coach.method_rates, m) for m in TRAINING_METHODS
            }
            obs_ev_id = world.record(
                day,
                "coach",
                "coach_observation",
                COACH_ID,
                target,
                (dec_id, player.last_state_id),
                slot=slot,
                skill_signal=pre_sig,
                fatigue_signal=fat_sig,
                forecasts=pre_forecasts,
            )
            new_skill_est = round6(
                clamp(
                    (1.0 - cfg.coach_skill_alpha) * assess.skill_estimate + cfg.coach_skill_alpha * pre_sig,
                    0.0,
                    1.0,
                )
            )
            new_fat_est = round6(
                clamp(
                    (1.0 - cfg.coach_skill_alpha) * assess.fatigue_estimate + cfg.coach_skill_alpha * fat_sig,
                    0.0,
                    1.0,
                )
            )
            belief_id = world.record(
                day,
                "coach",
                "coach_skill_belief",
                COACH_ID,
                target,
                (obs_ev_id, assess.skill_memory_id),
                skill_before=round6(assess.skill_estimate),
                skill_after=new_skill_est,
                fatigue_before=round6(assess.fatigue_estimate),
                fatigue_after=new_fat_est,
            )
            rec_id = world.record(
                day,
                "coach",
                "coach_recency",
                COACH_ID,
                target,
                (obs_ev_id, assess.recency_memory_id),
                last_seen_before=assess.last_seen,
                last_seen_after=day,
            )
            assess.skill_estimate = new_skill_est
            assess.fatigue_estimate = new_fat_est
            assess.last_seen = day
            assess.skill_memory_id = belief_id
            assess.recency_memory_id = rec_id
            reserved_observations[target] = {
                "obs_event_id": obs_ev_id,
                "pre_signal": pre_sig,
                "forecasts": pre_forecasts,
            }
        elif kind == "propose" and target is not None and chosen["method"] is not None:
            method = str(chosen["method"])
            f_info = chosen["forecasts"][method]
            prop_id = world.record(
                day,
                "coach",
                "training_proposal",
                COACH_ID,
                target,
                (dec_id,),
                slot=slot,
                method=method,
                expected_gain=f_info["expected_gain"],
                base_gain=f_info["base_gain"],
                residual=f_info["residual"],
            )
            proposals_by_player[target].append(
                TrainingProposal(
                    coach_id=COACH_ID,
                    event_id=prop_id,
                    target=target,
                    method=method,
                    expected_gain=f_info["expected_gain"],
                )
            )
        else:
            world.record(day, "coach", "coach_rest", COACH_ID, COACH_ID, (dec_id,), slot=slot)

    # 3. Players independently decide training action (A / B / rest) and execute state update A + belief updates B
    player_actions_summary: dict[str, str] = {}
    for pid in ROSTER_IDS:
        player = world.players[pid]
        mind = player.mind
        m_obs_id, m_skill_sig, m_fat_sig = morning_obs[pid]
        obs_obj = PlayerTrainingObservation(
            player_id=pid,
            day=day,
            event_id=m_obs_id,
            skill_signal=m_skill_sig,
            fatigue_signal=m_fat_sig,
            proposals=tuple(proposals_by_player[pid]),
        )
        p_dec = decide_player_training(obs_obj, mind, cfg)
        chosen_action = str(p_dec["chosen"]["action"])
        player_actions_summary[pid] = chosen_action
        p_dec_id = world.record(
            day,
            "player",
            "player_training_decision",
            pid,
            pid,
            (*p_dec["observation_ids"], *p_dec["memory_ids"]),
            **p_dec,
        )

        old_traits = player.traits
        old_mean = player.mean_skill
        old_fatigue = player.fatigue
        if chosen_action in TRAINING_METHODS:
            m_idx = TRAINING_METHODS.index(chosen_action)
            rate = player.rates[m_idx]
            w_pace, w_pass, w_shoot = METHOD_TRAIT_WEIGHTS[chosen_action]
            base_delta = rate * (1.0 - old_mean / player.cap) * (1.0 - 0.55 * old_fatigue) * 0.25
            new_pace = round6(clamp(old_traits.pace + base_delta * w_pace, 0.0, player.cap))
            new_pass = round6(clamp(old_traits.passing + base_delta * w_pass, 0.0, player.cap))
            new_shoot = round6(clamp(old_traits.shooting + base_delta * w_shoot, 0.0, player.cap))
            new_traits = MatchTraits(pace=new_pace, passing=new_pass, shooting=new_shoot)
            new_fatigue = round6(clamp(old_fatigue + 0.13, 0.0, 1.0))
        else:
            new_traits = old_traits
            new_fatigue = round6(clamp(old_fatigue - 0.19, 0.0, 1.0))

        player.traits = new_traits
        player.fatigue = new_fatigue
        state_id = world.record(
            day,
            "outcome",
            "state_update",
            pid,
            pid,
            (p_dec_id, player.last_state_id),
            action=chosen_action,
            traits_before=old_traits.to_dict(),
            traits_after=new_traits.to_dict(),
            skill_before=old_mean,
            skill_after=player.mean_skill,
            fatigue_before=round6(old_fatigue),
            fatigue_after=new_fatigue,
        )
        player.last_state_id = state_id

        # Evening player self-observation
        ev_noise = uniform_from_key(
            world.seed, day, "evening_skill_obs", pid, low=-cfg.self_noise, high=cfg.self_noise
        )
        ev_sig = round6(clamp(player.mean_skill + ev_noise, 0.0, 1.0))
        ev_obs_id = world.record(
            day,
            "evening",
            "evening_observation",
            pid,
            pid,
            (state_id,),
            skill_signal=ev_sig,
        )
        observed_gain = round6(ev_sig - m_skill_sig)

        # Trust update on accepted proposal (before updating player's own expected_gains)
        if chosen_action in TRAINING_METHODS:
            m_idx = TRAINING_METHODS.index(chosen_action)
            self_pred = mind.expected_gains[m_idx]
            for resp in p_dec["proposal_responses"]:
                if resp["feedback_eligible"]:
                    c_pred = float(resp["expected_gain"])
                    self_sq = round6((observed_gain - self_pred) ** 2)
                    coach_sq = round6((observed_gain - c_pred) ** 2)
                    adv = round6(self_sq - coach_sq)
                    delta_trust = round6(cfg.trust_alpha * clamp(adv * cfg.trust_scale, -1.0, 1.0))
                    new_trust = round6(clamp(mind.trust + delta_trust, 0.0, 1.0))
                    fb_id = world.record(
                        day,
                        "belief",
                        "advice_feedback",
                        pid,
                        pid,
                        (m_obs_id, ev_obs_id, int(resp["proposal_id"]), mind.trust_memory_id),
                        coach_id=COACH_ID,
                        method=chosen_action,
                        observed_gain=observed_gain,
                        self_prediction=round6(self_pred),
                        coach_prediction=round6(c_pred),
                        self_squared_error=self_sq,
                        coach_squared_error=coach_sq,
                        trust_before=round6(mind.trust),
                        trust_after=new_trust,
                    )
                    mind.trust = new_trust
                    mind.trust_memory_id = fb_id

            # Update player's own expected gain G (signed)
            old_g = mind.expected_gains[m_idx]
            new_g = round6(
                clamp((1.0 - cfg.player_gain_alpha) * old_g + cfg.player_gain_alpha * observed_gain, -1.0, 1.0)
            )
            g_id = world.record(
                day,
                "belief",
                "player_gain_belief",
                pid,
                pid,
                (m_obs_id, ev_obs_id, mind.gain_memory_ids[m_idx]),
                method=chosen_action,
                observed_gain=observed_gain,
                expected_before=round6(old_g),
                expected_after=new_g,
            )
            eg_list = list(mind.expected_gains)
            eg_list[m_idx] = new_g
            mind.expected_gains = (eg_list[0], eg_list[1])
            gm_list = list(mind.gain_memory_ids)
            gm_list[m_idx] = g_id
            mind.gain_memory_ids = (gm_list[0], gm_list[1])

        # Update player's self skill estimate
        old_s_est = mind.skill_estimate
        new_s_est = round6(
            clamp((1.0 - cfg.player_skill_alpha) * old_s_est + cfg.player_skill_alpha * ev_sig, 0.0, 1.0)
        )
        s_mem_id = world.record(
            day,
            "belief",
            "player_skill_belief",
            pid,
            pid,
            (ev_obs_id, mind.skill_memory_id),
            skill_before=round6(old_s_est),
            skill_after=new_s_est,
        )
        mind.skill_estimate = new_s_est
        mind.skill_memory_id = s_mem_id

        # If coach observed this player today, record post-session observation & residual update
        if pid in reserved_observations:
            res_info = reserved_observations[pid]
            assess = coach.assessments[pid]
            post_noise = uniform_from_key(
                world.seed, day, "coach_post_obs", pid, low=-cfg.coach_noise, high=cfg.coach_noise
            )
            post_sig = round6(clamp(player.mean_skill + post_noise, 0.0, 1.0))
            post_obs_id = world.record(
                day,
                "evening",
                "coach_post_observation",
                COACH_ID,
                pid,
                (int(res_info["obs_event_id"]), state_id),
                performed_action=chosen_action,
                post_skill_signal=post_sig,
            )
            if chosen_action in TRAINING_METHODS:
                m_idx = TRAINING_METHODS.index(chosen_action)
                f_entry = res_info["forecasts"][chosen_action]
                coach_obs_gain = round6(post_sig - float(res_info["pre_signal"]))
                target_res = round6(coach_obs_gain - float(f_entry["base_gain"]))
                old_res = assess.residuals[m_idx]
                new_res = round6(
                    clamp(
                        (1.0 - cfg.coach_residual_alpha) * old_res + cfg.coach_residual_alpha * target_res,
                        -1.0,
                        1.0,
                    )
                )
                exp_id = world.record(
                    day,
                    "belief",
                    "coach_method_experience",
                    COACH_ID,
                    pid,
                    (int(res_info["obs_event_id"]), post_obs_id, assess.residual_memory_ids[m_idx]),
                    method=chosen_action,
                    observed_gain=coach_obs_gain,
                    base_gain=float(f_entry["base_gain"]),
                    forecast_before=float(f_entry["expected_gain"]),
                    residual_before=round6(old_res),
                    residual_after=new_res,
                )
                res_list = list(assess.residuals)
                res_list[m_idx] = new_res
                assess.residuals = (res_list[0], res_list[1])
                rm_list = list(assess.residual_memory_ids)
                rm_list[m_idx] = exp_id
                assess.residual_memory_ids = (rm_list[0], rm_list[1])

    world.record(
        day,
        "summary",
        "day_summary",
        "world",
        None,
        day_type="training",
        coach_slots=len(coach_slot_actions),
        coach_actions=coach_slot_actions,
        player_actions=player_actions_summary,
    )


def _effective_match_traits(player: SelectionPlayer, config: SelectionConfig) -> MatchTraits:
    if config.training_match_coupling == "coupled":
        base = player.traits
        f = player.fatigue
        return MatchTraits(
            pace=round6(clamp(base.pace * (1.0 - 0.18 * f), 0.15, 1.0)),
            passing=round6(clamp(base.passing * (1.0 - 0.10 * f), 0.15, 1.0)),
            shooting=round6(clamp(base.shooting * (1.0 - 0.10 * f), 0.15, 1.0)),
        )
    return player.initial_traits


def _extract_starter_match_stats(
    match_world: m2d.MatchWorldState,
    slot_to_pid: dict[str, str],
) -> dict[str, dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {
        slot_id: {
            "roster_id": pid,
            "goals": 0,
            "shots": 0,
            "pass_attempts": 0,
            "pass_completions": 0,
            "tackles_won": 0,
            "possession_ticks": 0,
        }
        for slot_id, pid in slot_to_pid.items()
    }
    for ev in match_world.events:
        kind = ev["kind"]
        if kind == "ball_kick":
            kicker = ev.get("kicker_id")
            kick_kind = ev.get("kick_kind")
            if kicker in stats:
                if kick_kind == "pass":
                    stats[kicker]["pass_attempts"] += 1
                elif kick_kind == "shoot":
                    stats[kicker]["shots"] += 1
        elif kind == "ball_control":
            if ev.get("outcome") == "pass_completed":
                from_pid = ev.get("from_player_id")
                if from_pid in stats:
                    stats[from_pid]["pass_completions"] += 1
        elif kind == "tackle_resolution":
            challenger = ev.get("challenger_id")
            if ev.get("outcome") == "tackle_won" and challenger in stats:
                stats[challenger]["tackles_won"] += 1
        elif kind == "goal":
            scorer = ev.get("scorer_id")
            if ev.get("scoring_team") == "home" and scorer in stats:
                stats[scorer]["goals"] += 1
        elif kind == "tick_summary":
            owner = ev.get("ball", {}).get("owner_id")
            if owner in stats:
                stats[owner]["possession_ticks"] += 1
    return stats


def _step_match_day(world: SelectionWorldState, day: int) -> None:
    assert world.coach is not None
    cfg = world.config
    coach = world.coach

    # 1. Coach selects 2 starters and 1 bench player from private beliefs only
    lineup_dec = decide_lineup(coach, day, cfg)
    starters = list(lineup_dec["starters"])
    bench_pid = str(lineup_dec["bench"])
    lineup_id = world.record(
        day,
        "selection",
        "lineup_decision",
        COACH_ID,
        None,
        lineup_dec["memory_ids"],
        **lineup_dec,
    )

    # 2. Construct and run 40-tick 2D match episode using starters h1=starters[0], h2=starters[1]
    match_seed = world.seed * 1009 + day * 37
    m_cfg = MatchConfig(
        condition="baseline",
        noise_scale=cfg.match_noise_scale,
    )
    match_world = m2d.create_match_world(seed=match_seed, condition="baseline", config=m_cfg)
    slot_to_pid = {"h1": starters[0], "h2": starters[1]}
    effective_traits_log: dict[str, dict[str, float]] = {}

    for m_slot, pid in slot_to_pid.items():
        p_obj = world.players[pid]
        eff_traits = _effective_match_traits(p_obj, cfg)
        effective_traits_log[pid] = eff_traits.to_dict()
        old_p = match_world.players[m_slot]
        match_world.players[m_slot] = PlayerMatchState(
            player_id=m_slot,
            team="home",
            slot=old_p.slot,
            x=old_p.x,
            y=old_p.y,
            traits=eff_traits,
            mind=PlayerMatchMind(
                player_id=m_slot,
                team="home",
                style=p_obj.mind.style,
                coach_directive="balanced",
            ),
        )
    m2d.run_match(cfg.match_ticks, world=match_world)

    starter_stats_by_slot = _extract_starter_match_stats(match_world, slot_to_pid)
    starter_stats_by_pid = {
        slot_to_pid[slot]: starter_stats_by_slot[slot] for slot in ("h1", "h2")
    }

    # Count team-level metrics from match_world
    home_goals = int(match_world.score["home"])
    away_goals = int(match_world.score["away"])
    home_poss = sum(s["possession_ticks"] for s in starter_stats_by_pid.values())
    home_passes = sum(s["pass_attempts"] for s in starter_stats_by_pid.values())
    home_pass_comp = sum(s["pass_completions"] for s in starter_stats_by_pid.values())
    home_shots = sum(s["shots"] for s in starter_stats_by_pid.values())
    home_tackles = sum(s["tackles_won"] for s in starter_stats_by_pid.values())

    match_ev_id = world.record(
        day,
        "match",
        "match_episode",
        "world",
        None,
        (lineup_id, *[world.players[pid].last_state_id for pid in starters]),
        match_seed=match_seed,
        ticks=cfg.match_ticks,
        starters=starters,
        bench=bench_pid,
        effective_traits=effective_traits_log,
        home_goals=home_goals,
        away_goals=away_goals,
        goal_diff=home_goals - away_goals,
        home_possession_ticks=home_poss,
        home_pass_attempts=home_passes,
        home_pass_completions=home_pass_comp,
        home_shots=home_shots,
        home_tackles_won=home_tackles,
        starter_stats=starter_stats_by_pid,
    )

    world.match_summaries.append(
        {
            "day": day,
            "match_event_id": match_ev_id,
            "starters": starters,
            "bench": bench_pid,
            "home_goals": home_goals,
            "away_goals": away_goals,
            "goal_diff": home_goals - away_goals,
            "home_possession_ticks": home_poss,
            "home_pass_attempts": home_passes,
            "home_pass_completions": home_pass_comp,
            "home_shots": home_shots,
            "home_tackles_won": home_tackles,
            "starter_stats": starter_stats_by_pid,
        }
    )

    # 3. Post-match physical fatigue & competitive match growth update (A) and career/recognition updates (B)
    for pid in ROSTER_IDS:
        player = world.players[pid]
        mind = player.mind
        assess = coach.assessments[pid]
        old_traits = player.traits
        old_mean = player.mean_skill
        old_fatigue = player.fatigue
        started = pid in starter_stats_by_pid

        if started:
            st = starter_stats_by_pid[pid]
            exertion = 0.06 + 0.002 * (st["tackles_won"] + st["shots"])
            new_fatigue = round6(clamp(old_fatigue + exertion, 0.0, 1.0))
            activity_factor = (
                0.55
                + 0.08 * st["goals"]
                + 0.03 * st["pass_completions"]
                + 0.025 * st["shots"]
                + 0.02 * st["tackles_won"]
            )
            match_delta = 0.036 * (1.0 - old_mean / player.cap) * activity_factor
            new_traits = MatchTraits(
                pace=round6(clamp(old_traits.pace + match_delta * 0.95, 0.0, player.cap)),
                passing=round6(clamp(old_traits.passing + match_delta * 1.05, 0.0, player.cap)),
                shooting=round6(clamp(old_traits.shooting + match_delta * 1.00, 0.0, player.cap)),
            )
        else:
            new_fatigue = round6(clamp(old_fatigue - 0.05, 0.0, 1.0))
            new_traits = old_traits

        player.traits = new_traits
        player.fatigue = new_fatigue
        state_id = world.record(
            day,
            "outcome",
            "state_update",
            pid,
            pid,
            (match_ev_id, player.last_state_id),
            action="match_starter" if started else "match_bench",
            traits_before=old_traits.to_dict(),
            traits_after=new_traits.to_dict(),
            skill_before=old_mean,
            skill_after=player.mean_skill,
            fatigue_before=round6(old_fatigue),
            fatigue_after=new_fatigue,
        )
        player.last_state_id = state_id

        # Player career belief update
        old_sat = mind.playing_time_satisfaction
        old_self_m = mind.self_match_rating
        if started:
            st = starter_stats_by_pid[pid]
            mind.appearances += 1
            mind.bench_streak = 0
            new_sat = round6(clamp(old_sat + 0.08, 0.0, 1.0))
            raw_perf = (
                0.28
                + 0.18 * st["goals"]
                + 0.035 * st["shots"]
                + 0.035 * st["pass_completions"]
                + 0.018 * st["tackles_won"]
                + 0.007 * st["possession_ticks"]
                - 0.095 * away_goals
            )
            p_m_noise = uniform_from_key(
                world.seed, day, "self_match_obs", pid, low=-cfg.match_obs_noise, high=cfg.match_obs_noise
            )
            obs_perf = round6(clamp(raw_perf + p_m_noise, 0.0, 1.0))
            new_self_m = round6(clamp(0.65 * old_self_m + 0.35 * obs_perf, 0.0, 1.0))
        else:
            mind.bench_streak += 1
            new_sat = round6(clamp(old_sat - 0.09, 0.0, 1.0))
            new_self_m = old_self_m

        mind.playing_time_satisfaction = new_sat
        mind.self_match_rating = new_self_m
        c_mem_id = world.record(
            day,
            "belief",
            "player_career_belief",
            pid,
            pid,
            (match_ev_id, mind.career_memory_id),
            started=started,
            appearances=mind.appearances,
            bench_streak=mind.bench_streak,
            satisfaction_before=round6(old_sat),
            satisfaction_after=new_sat,
            self_match_rating_before=round6(old_self_m),
            self_match_rating_after=new_self_m,
        )
        mind.career_memory_id = c_mem_id

        # Coach post-match belief update (starters observed; bench only updates perceived_bench_streak)
        if started:
            st = starter_stats_by_pid[pid]
            c_m_noise = uniform_from_key(
                world.seed, day, "coach_match_obs", pid, low=-cfg.match_obs_noise, high=cfg.match_obs_noise
            )
            raw_perf = (
                0.28
                + 0.18 * st["goals"]
                + 0.035 * st["shots"]
                + 0.035 * st["pass_completions"]
                + 0.018 * st["tackles_won"]
                + 0.007 * st["possession_ticks"]
                - 0.095 * away_goals
            )
            c_obs_rating = round6(clamp(raw_perf + c_m_noise, 0.0, 1.0))
            f_obs_noise = uniform_from_key(
                world.seed, day, "coach_match_fat_obs", pid, low=-cfg.coach_noise, high=cfg.coach_noise
            )
            c_obs_fat = round6(clamp(player.fatigue + f_obs_noise, 0.0, 1.0))

            old_m_est = assess.match_rating_estimate
            old_s_est = assess.skill_estimate
            old_f_est = assess.fatigue_estimate

            if cfg.match_feedback == "feedback":
                new_m_est = round6(
                    clamp((1.0 - cfg.coach_match_alpha) * old_m_est + cfg.coach_match_alpha * c_obs_rating, 0.0, 1.0)
                )
                new_s_est = round6(
                    clamp(
                        (1.0 - cfg.coach_match_skill_blend) * old_s_est
                        + cfg.coach_match_skill_blend * c_obs_rating,
                        0.0,
                        1.0,
                    )
                )
            else:
                new_m_est = old_m_est
                new_s_est = old_s_est

            new_f_est = round6(clamp(0.50 * old_f_est + 0.50 * c_obs_fat, 0.0, 1.0))
            m_bel_id = world.record(
                day,
                "belief",
                "coach_match_belief",
                COACH_ID,
                pid,
                (match_ev_id, assess.match_memory_id, assess.skill_memory_id),
                started=True,
                observed_rating=c_obs_rating,
                match_rating_before=round6(old_m_est),
                match_rating_after=new_m_est,
                skill_before=round6(old_s_est),
                skill_after=new_s_est,
                fatigue_before=round6(old_f_est),
                fatigue_after=new_f_est,
                perceived_bench_streak=0,
            )
            assess.match_rating_estimate = new_m_est
            assess.skill_estimate = new_s_est
            assess.fatigue_estimate = new_f_est
            if cfg.match_feedback == "feedback":
                assess.last_seen = day
                assess.recency_memory_id = m_bel_id
            assess.last_match_seen = day
            assess.perceived_bench_streak = 0
            assess.match_memory_id = m_bel_id
            assess.skill_memory_id = m_bel_id
        else:
            new_streak = assess.perceived_bench_streak + 1
            new_f_est = round6(clamp(assess.fatigue_estimate - 0.08, 0.0, 1.0))
            m_bel_id = world.record(
                day,
                "belief",
                "coach_match_belief",
                COACH_ID,
                pid,
                (match_ev_id, assess.match_memory_id),
                started=False,
                observed_rating=None,
                match_rating_before=round6(assess.match_rating_estimate),
                match_rating_after=round6(assess.match_rating_estimate),
                skill_before=round6(assess.skill_estimate),
                skill_after=round6(assess.skill_estimate),
                fatigue_before=round6(assess.fatigue_estimate),
                fatigue_after=new_f_est,
                perceived_bench_streak=new_streak,
            )
            assess.fatigue_estimate = new_f_est
            assess.perceived_bench_streak = new_streak
            assess.match_memory_id = m_bel_id

    world.record(
        day,
        "summary",
        "day_summary",
        "world",
        None,
        (match_ev_id,),
        day_type="match",
        starters=starters,
        bench=bench_pid,
        home_goals=home_goals,
        away_goals=away_goals,
    )


def step_selection_day(world: SelectionWorldState) -> SelectionWorldState:
    next_day = world.day + 1
    if next_day % world.config.match_interval_days == 0:
        _step_match_day(world, next_day)
    else:
        _step_training_day(world, next_day)
    world.day = next_day
    validate_selection_world(world, check_history=False)
    return world


def run_selection_world(
    days: int = 84,
    seed: int = 17,
    condition: str = "coupled_feedback",
    *,
    world: SelectionWorldState | None = None,
    config: SelectionConfig | None = None,
) -> SelectionWorldState:
    if days < 0:
        raise ValueError("days cannot be negative")
    state = world or create_selection_world(seed=seed, condition=condition, config=config)
    validate_selection_world(state, check_history=True)
    for _ in range(days):
        step_selection_day(state)
    validate_selection_world(state, check_history=True)
    return state


def _check_pointer(
    events: list[dict[str, Any]],
    event_id: int,
    actor: str,
    target: str | None,
    allowed_kinds: set[str],
) -> dict[str, Any]:
    if not (isinstance(event_id, int) and 1 <= event_id <= len(events)):
        raise ValueError(f"invalid pointer event_id: {event_id}")
    ev = events[event_id - 1]
    if ev["event_id"] != event_id:
        raise ValueError("event_id sequence mismatch")
    if ev["kind"] not in allowed_kinds:
        raise ValueError(f"pointer {event_id} has unexpected kind {ev['kind']}")
    if ev["actor"] != actor:
        raise ValueError(f"pointer {event_id} belongs to {ev['actor']}, expected {actor}")
    if target is not None and ev["target"] != target:
        raise ValueError(f"pointer {event_id} targets {ev['target']}, expected {target}")
    return ev


def validate_selection_world(world: SelectionWorldState, *, check_history: bool = True) -> None:
    world.config.validate()
    if not isinstance(world.seed, int) or not isinstance(world.day, int) or world.day < 0:
        raise ValueError("invalid seed or day")
    if set(world.players.keys()) != set(ROSTER_IDS) or world.coach is None:
        raise ValueError("invalid roster or missing coach")
    if world.coach.coach_id != COACH_ID or set(world.coach.assessments.keys()) != set(ROSTER_IDS):
        raise ValueError("invalid coach state")

    events = world.events
    if not events:
        raise ValueError("SelectionWorldState events cannot be empty")

    for pid in ROSTER_IDS:
        p = world.players[pid]
        mind = p.mind
        if p.player_id != pid or mind.player_id != pid:
            raise ValueError("player_id mismatch")
        if not (0.0 < p.cap <= 1.0):
            raise ValueError("invalid player cap")
        for val in (p.traits.pace, p.traits.passing, p.traits.shooting):
            if not (0.0 <= val <= p.cap + 1e-6):
                raise ValueError(f"player {pid} trait out of bounds: {val}")
        if not (0.0 <= p.fatigue <= 1.0):
            raise ValueError(f"player {pid} fatigue out of bounds: {p.fatigue}")
        if not (0.0 <= mind.skill_estimate <= 1.0 and 0.0 <= mind.trust <= 1.0 and 0.0 <= mind.autonomy <= 1.0):
            raise ValueError(f"player {pid} mind values out of bounds")
        if not (0.0 <= mind.playing_time_satisfaction <= 1.0 and 0.0 <= mind.self_match_rating <= 1.0):
            raise ValueError(f"player {pid} career beliefs out of bounds")
        if any(not (-1.0 <= g <= 1.0) for g in mind.expected_gains):
            raise ValueError(f"player {pid} expected_gains out of bounds")

        state_ev = _check_pointer(
            events, p.last_state_id, pid, pid, {"initial_player", "state_update"}
        )
        if state_ev["kind"] == "state_update":
            if state_ev["data"]["traits_after"] != p.traits.to_dict():
                raise ValueError(f"player {pid} traits do not match last state_update")
            if round6(float(state_ev["data"]["fatigue_after"])) != round6(p.fatigue):
                raise ValueError(f"player {pid} fatigue does not match last state_update")

        s_ev = _check_pointer(
            events, mind.skill_memory_id, pid, pid, {"initial_player", "player_skill_belief"}
        )
        s_val = (
            s_ev["data"]["state"]["mind"]["skill_estimate"]
            if s_ev["kind"] == "initial_player"
            else s_ev["data"]["skill_after"]
        )
        if round6(float(s_val)) != round6(mind.skill_estimate):
            raise ValueError(f"player {pid} skill_memory_id value mismatch")

        for idx, m_name in enumerate(TRAINING_METHODS):
            g_ev = _check_pointer(
                events, mind.gain_memory_ids[idx], pid, pid, {"initial_player", "player_gain_belief"}
            )
            if g_ev["kind"] == "initial_player":
                g_val = g_ev["data"]["state"]["mind"]["expected_gains"][idx]
            else:
                if g_ev["data"]["method"] != m_name:
                    raise ValueError("gain_memory_id points to wrong method")
                g_val = g_ev["data"]["expected_after"]
            if round6(float(g_val)) != round6(mind.expected_gains[idx]):
                raise ValueError(f"player {pid} expected_gain mismatch on {m_name}")

        t_ev = _check_pointer(
            events, mind.trust_memory_id, pid, pid, {"initial_trust", "advice_feedback"}
        )
        if round6(float(t_ev["data"]["trust_after"])) != round6(mind.trust):
            raise ValueError(f"player {pid} trust_memory_id mismatch")

        c_ev = _check_pointer(
            events, mind.career_memory_id, pid, pid, {"initial_career", "player_career_belief"}
        )
        if c_ev["kind"] == "player_career_belief":
            if (
                int(c_ev["data"]["appearances"]) != mind.appearances
                or int(c_ev["data"]["bench_streak"]) != mind.bench_streak
                or round6(float(c_ev["data"]["satisfaction_after"])) != round6(mind.playing_time_satisfaction)
            ):
                raise ValueError(f"player {pid} career_memory_id mismatch")

    coach = world.coach
    prior_ev = _check_pointer(
        events, coach.forecast_memory_id, COACH_ID, COACH_ID, {"coach_forecast_prior"}
    )
    if [round6(x) for x in prior_ev["data"]["method_rates"]] != [round6(x) for x in coach.method_rates]:
        raise ValueError("coach method_rates mismatch")

    for pid in ROSTER_IDS:
        assess = coach.assessments[pid]
        if assess.player_id != pid:
            raise ValueError("coach assessment player_id mismatch")
        if not (
            0.0 <= assess.skill_estimate <= 1.0
            and 0.0 <= assess.fatigue_estimate <= 1.0
            and 0.0 <= assess.match_rating_estimate <= 1.0
        ):
            raise ValueError(f"coach assessment out of bounds for {pid}")
        if not (0 <= assess.last_seen <= world.day and 0 <= assess.last_match_seen <= world.day):
            raise ValueError(f"coach last_seen out of bounds for {pid}")

        cs_ev = _check_pointer(
            events,
            assess.skill_memory_id,
            COACH_ID,
            pid,
            {"initial_assessment", "coach_skill_belief", "coach_match_belief"},
        )
        cs_val = (
            cs_ev["data"]["skill_estimate"]
            if cs_ev["kind"] == "initial_assessment"
            else cs_ev["data"]["skill_after"]
        )
        if round6(float(cs_val)) != round6(assess.skill_estimate):
            raise ValueError(f"coach skill_memory_id mismatch for {pid}")

        cm_ev = _check_pointer(
            events,
            assess.match_memory_id,
            COACH_ID,
            pid,
            {"initial_match_assessment", "coach_match_belief"},
        )
        cm_val = (
            cm_ev["data"]["match_rating_estimate"]
            if cm_ev["kind"] == "initial_match_assessment"
            else cm_ev["data"]["match_rating_after"]
        )
        if round6(float(cm_val)) != round6(assess.match_rating_estimate):
            raise ValueError(f"coach match_memory_id mismatch for {pid}")

        for idx, m_name in enumerate(TRAINING_METHODS):
            r_ev = _check_pointer(
                events,
                assess.residual_memory_ids[idx],
                COACH_ID,
                pid,
                {"initial_coach_adjustment", "coach_method_experience"},
            )
            if r_ev["kind"] == "initial_coach_adjustment":
                r_val = r_ev["data"]["residuals"][idx]
            else:
                if r_ev["data"]["method"] != m_name:
                    raise ValueError("coach residual memory method mismatch")
                r_val = r_ev["data"]["residual_after"]
            if round6(float(r_val)) != round6(assess.residuals[idx]):
                raise ValueError(f"coach residual mismatch for {pid} method {m_name}")

    if not check_history:
        return

    prev_day = 0
    summary_days: list[int] = []
    match_days: list[int] = []
    for idx, ev in enumerate(events, start=1):
        if ev.get("event_id") != idx:
            raise ValueError(f"non-sequential event_id at index {idx}")
        ev_day = int(ev.get("day", -1))
        if ev_day < prev_day or ev_day > world.day:
            raise ValueError(f"non-monotonic event day {ev_day} at event {idx}")
        prev_day = ev_day
        for cause in ev.get("causes", []):
            if not (isinstance(cause, int) and 1 <= cause < idx):
                raise ValueError(f"invalid cause {cause} in event {idx}")
        if ev["kind"] == "day_summary":
            summary_days.append(ev_day)
            if ev["data"].get("day_type") == "training":
                if ev["data"].get("coach_slots") != world.config.coach_daily_slots:
                    raise ValueError("coach daily slot budget violation")
                if set(ev["data"].get("player_actions", {}).keys()) != set(ROSTER_IDS):
                    raise ValueError("player daily slot budget violation")
            elif ev["data"].get("day_type") == "match":
                starters = ev["data"].get("starters", [])
                bench = ev["data"].get("bench")
                if len(starters) != 2 or len(set(starters)) != 2 or set(starters + [bench]) != set(ROSTER_IDS):
                    raise ValueError("invalid lineup partition in match day_summary")
        elif ev["kind"] == "match_episode":
            match_days.append(ev_day)

    if summary_days != list(range(1, world.day + 1)):
        raise ValueError("day_summary sequence does not match 1..world.day")
    expected_match_days = [d for d in range(1, world.day + 1) if d % world.config.match_interval_days == 0]
    if match_days != expected_match_days or len(world.match_summaries) != len(expected_match_days):
        raise ValueError("match_episode count/days mismatch")


def selection_identity() -> dict[str, Any]:
    return {
        **package_identity(),
        "world_kind": SELECTION_WORLD_KIND,
        "save": SELECTION_SAVE_VERSION,
        "rules": SELECTION_RULE_VERSION,
        "policy": SELECTION_POLICY_VERSION,
        "author": SELECTION_AUTHOR,
    }


def snapshot_payload(world: SelectionWorldState) -> dict[str, Any]:
    validate_selection_world(world, check_history=True)
    return {
        "world_kind": SELECTION_WORLD_KIND,
        "identity": {
            **selection_identity(),
            "save_version": SELECTION_SAVE_VERSION,
            "rule_version": SELECTION_RULE_VERSION,
            "policy_version": world.config.policy_version,
        },
        "state": world.to_dict(include_events=True),
    }


def write_snapshot(path: Path | str, world: SelectionWorldState) -> None:
    p = Path(path)
    payload = snapshot_payload(world)
    atomic_write(p, [canonical(payload), b"\n"])


def write_event_log(path: Path | str, world: SelectionWorldState) -> None:
    p = Path(path)
    lines = [canonical(event) + b"\n" for event in world.events]
    atomic_write(p, lines)


def write_run(
    out_dir: Path | str,
    world: SelectionWorldState,
    elapsed_seconds: float = 0.0,
) -> str:
    from .experiment_t009 import measure_selection_run

    out = Path(out_dir)
    payload = snapshot_payload(world)
    state_hash = digest(payload)
    atomic_write(out / "snapshot.json", [canonical(payload), b"\n"])
    write_event_log(out / "events.jsonl", world)
    write_json(
        out / "summary.json",
        {**measure_selection_run(world, elapsed_seconds), "simulation_sha256": state_hash},
    )
    write_json(
        out / "manifest.json",
        {
            "world_kind": SELECTION_WORLD_KIND,
            "author": SELECTION_AUTHOR,
            "versions": selection_identity(),
            "runtime": runtime(),
            "config": world.config.to_dict(),
            "seed": world.seed,
            "days": world.day,
            "simulation_sha256": state_hash,
        },
    )
    return state_hash


def load_snapshot(path: Path | str) -> SelectionWorldState:
    try:
        payload = json.loads(
            Path(path).read_bytes(),
            parse_constant=reject_nonfinite,
            parse_float=parse_float,
        )
        if payload.get("world_kind") != SELECTION_WORLD_KIND:
            raise ValueError(f"unsupported world_kind: {payload.get('world_kind')}")
        ident = dict(payload.get("identity", {}))
        current = package_identity()
        if ident.get("world_kind") != SELECTION_WORLD_KIND:
            raise ValueError(f"unsupported identity world_kind: {ident.get('world_kind')}")
        if ident.get("save_version") != SELECTION_SAVE_VERSION:
            raise ValueError(f"unsupported save_version: {ident.get('save_version')}")
        if ident.get("rule_version") != SELECTION_RULE_VERSION:
            raise ValueError(f"unsupported rule_version: {ident.get('rule_version')}")
        if ident.get("policy_version") != SELECTION_POLICY_VERSION:
            raise ValueError(f"unsupported policy_version: {ident.get('policy_version')}")
        if ident.get("source_sha256") != current["source_sha256"]:
            raise ValueError("snapshot source_sha256 does not match current package")
        return SelectionWorldState.from_dict(dict(payload["state"]))
    except (KeyError, TypeError, AttributeError, IndexError, OverflowError) as exc:
        raise ValueError("malformed selection_loop snapshot") from exc
