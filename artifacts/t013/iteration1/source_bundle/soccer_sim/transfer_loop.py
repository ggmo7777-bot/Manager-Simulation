"""T010: Minimal 2-club closed loop connecting development (A/B), lineup selection, head-to-head 2D match core, and mutual-agreement transfers.

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


def _f(val: Any, _name: str = "") -> float:
    return parse_float(str(val))


# Explicit author attribution (distinct from T001-T007 creator Sol 6.1 High)
TRANSFER_AUTHOR = "Antigravity (Gemini 4 Argon)"
TRANSFER_WORLD_KIND = "transfer_loop"
TRANSFER_SAVE_VERSION = 10
TRANSFER_RULE_VERSION = "t010-transfer-loop-v1"
TRANSFER_POLICY_VERSION = "t010-transfer-score-v1"

CLUB_IDS = ("club_a", "club_b")
COACH_IDS = ("c1", "c2")
COACH_CLUB_MAP: dict[str, str] = {"c1": "club_a", "c2": "club_b"}
CLUB_COACH_MAP: dict[str, str] = {"club_a": "c1", "club_b": "c2"}
OTHER_CLUB_MAP: dict[str, str] = {"club_a": "club_b", "club_b": "club_a"}

PLAYER_IDS = ("p1", "p2", "p3", "p4", "p5", "p6")
INITIAL_CLUB_ROSTERS: dict[str, tuple[str, ...]] = {
    "club_a": ("p1", "p2", "p3"),
    "club_b": ("p4", "p5", "p6"),
}
MIN_CLUB_ROSTER = 2
MAX_CLUB_ROSTER = 4

TRAINING_METHODS = ("A", "B")
PLAYER_TRAINING_ACTIONS = ("A", "B", "rest")
COACH_TRAINING_KINDS = ("observe", "propose", "scout", "rest")

COUPLING_MODES = ("decoupled", "coupled")
TRANSFER_MODES = ("disabled", "enabled")
TRANSFER_WINDOW_DAYS = (28, 56)

T010_MAIN_CONDITIONS = (
    "decoupled_stay",
    "coupled_stay",
    "decoupled_transfer",
    "coupled_transfer",
)
T010_EXTRA_CONDITIONS = (
    "fair_c1_prior_p3",
    "low_c2_scouting_p3",
    "high_loyalty_p3",
)
T010_ALL_CONDITIONS = T010_MAIN_CONDITIONS + T010_EXTRA_CONDITIONS

METHOD_TRAIT_WEIGHTS: dict[str, tuple[float, float, float]] = {
    "A": (0.45, 1.45, 1.10),
    "B": (1.45, 0.45, 1.10),
}


@dataclass(frozen=True)
class TransferConfig:
    condition: str = "coupled_transfer"
    training_match_coupling: str = "coupled"
    transfer_mechanism: str = "enabled"
    match_interval_days: int = 7
    match_ticks: int = 40
    coach_daily_slots: int = 2
    self_noise: float = 0.018
    coach_noise: float = 0.024
    scout_noise: float = 0.030
    match_obs_noise: float = 0.020
    match_noise_scale: float = 1.0
    player_skill_alpha: float = 0.32
    player_gain_alpha: float = 0.30
    coach_skill_alpha: float = 0.36
    coach_scout_alpha: float = 0.32
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
    coach_rest_value: float = 0.11
    select_skill_weight: float = 0.55
    select_match_weight: float = 0.48
    select_fatigue_weight: float = 0.22
    c1_p3_skill_prior: float = 0.31
    c1_p3_match_prior: float = 0.29
    c2_p3_skill_prior: float = 0.48
    c2_p3_match_prior: float = 0.51
    p3_loyalty: float = 0.00
    transfer_request_threshold: float = 0.12
    transfer_offer_threshold: float = 0.025
    transfer_accept_threshold: float = 0.05

    def __post_init__(self) -> None:
        if self.condition not in T010_ALL_CONDITIONS:
            raise ValueError(f"unsupported T010 condition: {self.condition!r}")
        if self.training_match_coupling not in COUPLING_MODES:
            raise ValueError(f"unsupported coupling mode: {self.training_match_coupling!r}")
        if self.transfer_mechanism not in TRANSFER_MODES:
            raise ValueError(f"unsupported transfer mode: {self.transfer_mechanism!r}")
        if self.match_interval_days < 2:
            raise ValueError("match_interval_days must be >= 2")
        if self.match_ticks < 10:
            raise ValueError("match_ticks must be >= 10")
        if self.coach_daily_slots != 2:
            raise ValueError("coach_daily_slots must be 2")

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition": self.condition,
            "training_match_coupling": self.training_match_coupling,
            "transfer_mechanism": self.transfer_mechanism,
            "match_interval_days": self.match_interval_days,
            "match_ticks": self.match_ticks,
            "coach_daily_slots": self.coach_daily_slots,
            "self_noise": round6(self.self_noise),
            "coach_noise": round6(self.coach_noise),
            "scout_noise": round6(self.scout_noise),
            "match_obs_noise": round6(self.match_obs_noise),
            "match_noise_scale": round6(self.match_noise_scale),
            "player_skill_alpha": round6(self.player_skill_alpha),
            "player_gain_alpha": round6(self.player_gain_alpha),
            "coach_skill_alpha": round6(self.coach_skill_alpha),
            "coach_scout_alpha": round6(self.coach_scout_alpha),
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
            "c1_p3_skill_prior": round6(self.c1_p3_skill_prior),
            "c1_p3_match_prior": round6(self.c1_p3_match_prior),
            "c2_p3_skill_prior": round6(self.c2_p3_skill_prior),
            "c2_p3_match_prior": round6(self.c2_p3_match_prior),
            "p3_loyalty": round6(self.p3_loyalty),
            "transfer_request_threshold": round6(self.transfer_request_threshold),
            "transfer_offer_threshold": round6(self.transfer_offer_threshold),
            "transfer_accept_threshold": round6(self.transfer_accept_threshold),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TransferConfig:
        return cls(
            condition=str(data["condition"]),
            training_match_coupling=str(data["training_match_coupling"]),
            transfer_mechanism=str(data["transfer_mechanism"]),
            match_interval_days=int(data["match_interval_days"]),
            match_ticks=int(data["match_ticks"]),
            coach_daily_slots=int(data["coach_daily_slots"]),
            self_noise=_f(data["self_noise"], "self_noise"),
            coach_noise=_f(data["coach_noise"], "coach_noise"),
            scout_noise=_f(data["scout_noise"], "scout_noise"),
            match_obs_noise=_f(data["match_obs_noise"], "match_obs_noise"),
            match_noise_scale=_f(data["match_noise_scale"], "match_noise_scale"),
            player_skill_alpha=_f(data["player_skill_alpha"], "player_skill_alpha"),
            player_gain_alpha=_f(data["player_gain_alpha"], "player_gain_alpha"),
            coach_skill_alpha=_f(data["coach_skill_alpha"], "coach_skill_alpha"),
            coach_scout_alpha=_f(data["coach_scout_alpha"], "coach_scout_alpha"),
            coach_residual_alpha=_f(data["coach_residual_alpha"], "coach_residual_alpha"),
            coach_match_alpha=_f(data["coach_match_alpha"], "coach_match_alpha"),
            coach_match_skill_blend=_f(data["coach_match_skill_blend"], "coach_match_skill_blend"),
            trust_alpha=_f(data["trust_alpha"], "trust_alpha"),
            trust_scale=_f(data["trust_scale"], "trust_scale"),
            advice_weight=_f(data["advice_weight"], "advice_weight"),
            training_fatigue_weight=_f(data["training_fatigue_weight"], "training_fatigue_weight"),
            rest_fatigue_weight=_f(data["rest_fatigue_weight"], "rest_fatigue_weight"),
            satisfaction_drive_weight=_f(data["satisfaction_drive_weight"], "satisfaction_drive_weight"),
            coach_need_weight=_f(data["coach_need_weight"], "coach_need_weight"),
            coach_method_fit=_f(data["coach_method_fit"], "coach_method_fit"),
            coach_staleness_weight=_f(data["coach_staleness_weight"], "coach_staleness_weight"),
            coach_rest_value=_f(data["coach_rest_value"], "coach_rest_value"),
            select_skill_weight=_f(data["select_skill_weight"], "select_skill_weight"),
            select_match_weight=_f(data["select_match_weight"], "select_match_weight"),
            select_fatigue_weight=_f(data["select_fatigue_weight"], "select_fatigue_weight"),
            c1_p3_skill_prior=_f(data["c1_p3_skill_prior"], "c1_p3_skill_prior"),
            c1_p3_match_prior=_f(data["c1_p3_match_prior"], "c1_p3_match_prior"),
            c2_p3_skill_prior=_f(data["c2_p3_skill_prior"], "c2_p3_skill_prior"),
            c2_p3_match_prior=_f(data["c2_p3_match_prior"], "c2_p3_match_prior"),
            p3_loyalty=_f(data["p3_loyalty"], "p3_loyalty"),
            transfer_request_threshold=_f(data["transfer_request_threshold"], "transfer_request_threshold"),
            transfer_offer_threshold=_f(data["transfer_offer_threshold"], "transfer_offer_threshold"),
            transfer_accept_threshold=_f(data["transfer_accept_threshold"], "transfer_accept_threshold"),
        )


def config_for_condition(condition: str) -> TransferConfig:
    if condition == "decoupled_stay":
        return TransferConfig(
            condition=condition,
            training_match_coupling="decoupled",
            transfer_mechanism="disabled",
        )
    if condition == "coupled_stay":
        return TransferConfig(
            condition=condition,
            training_match_coupling="coupled",
            transfer_mechanism="disabled",
        )
    if condition == "decoupled_transfer":
        return TransferConfig(
            condition=condition,
            training_match_coupling="decoupled",
            transfer_mechanism="enabled",
        )
    if condition == "coupled_transfer":
        return TransferConfig(
            condition=condition,
            training_match_coupling="coupled",
            transfer_mechanism="enabled",
        )
    if condition == "fair_c1_prior_p3":
        return TransferConfig(
            condition=condition,
            training_match_coupling="coupled",
            transfer_mechanism="enabled",
            c1_p3_skill_prior=0.50,
            c1_p3_match_prior=0.54,
        )
    if condition == "low_c2_scouting_p3":
        return TransferConfig(
            condition=condition,
            training_match_coupling="coupled",
            transfer_mechanism="enabled",
            c2_p3_skill_prior=0.31,
            c2_p3_match_prior=0.29,
        )
    if condition == "high_loyalty_p3":
        return TransferConfig(
            condition=condition,
            training_match_coupling="coupled",
            transfer_mechanism="enabled",
            p3_loyalty=0.35,
        )
    raise ValueError(f"unknown T010 condition: {condition!r}")


@dataclass
class TransferPlayerState:
    player_id: str
    initial_club_id: str
    club_id: str
    initial_traits: MatchTraits
    traits: MatchTraits
    cap: float
    rates: dict[str, float]
    fatigue: float
    last_state_id: int
    last_transfer_id: int

    @property
    def mean_skill(self) -> float:
        return round6((self.traits.pace + self.traits.passing + self.traits.shooting) / 3.0)

    @property
    def initial_mean_skill(self) -> float:
        return round6(
            (self.initial_traits.pace + self.initial_traits.passing + self.initial_traits.shooting) / 3.0
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "player_id": self.player_id,
            "initial_club_id": self.initial_club_id,
            "club_id": self.club_id,
            "initial_traits": self.initial_traits.to_dict(),
            "traits": self.traits.to_dict(),
            "cap": round6(self.cap),
            "rates": {m: round6(v) for m, v in sorted(self.rates.items())},
            "fatigue": round6(self.fatigue),
            "last_state_id": int(self.last_state_id),
            "last_transfer_id": int(self.last_transfer_id),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TransferPlayerState:
        return cls(
            player_id=str(data["player_id"]),
            initial_club_id=str(data["initial_club_id"]),
            club_id=str(data["club_id"]),
            initial_traits=MatchTraits.from_dict(data["initial_traits"]),
            traits=MatchTraits.from_dict(data["traits"]),
            cap=_f(data["cap"], "cap"),
            rates={m: _f(data["rates"][m], f"rates.{m}") for m in TRAINING_METHODS},
            fatigue=_f(data["fatigue"], "fatigue"),
            last_state_id=int(data["last_state_id"]),
            last_transfer_id=int(data["last_transfer_id"]),
        )


@dataclass
class TransferPlayerMind:
    player_id: str
    club_id: str
    self_skill_estimate: float
    self_fatigue_estimate: float
    self_match_rating: float
    expected_gains: dict[str, float]
    preferences: dict[str, float]
    autonomy: float
    loyalty: float
    trust_by_coach: dict[str, float]
    appearances: int
    appearances_by_club: dict[str, int]
    bench_streak: int
    playing_time_satisfaction: float
    perceived_club_starter_bar: dict[str, float]
    match_style: MatchStyle
    skill_memory_id: int
    gain_memory_ids: dict[str, int]
    trust_memory_ids: dict[str, int]
    career_memory_id: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "player_id": self.player_id,
            "club_id": self.club_id,
            "self_skill_estimate": round6(self.self_skill_estimate),
            "self_fatigue_estimate": round6(self.self_fatigue_estimate),
            "self_match_rating": round6(self.self_match_rating),
            "expected_gains": {m: round6(v) for m, v in sorted(self.expected_gains.items())},
            "preferences": {m: round6(v) for m, v in sorted(self.preferences.items())},
            "autonomy": round6(self.autonomy),
            "loyalty": round6(self.loyalty),
            "trust_by_coach": {c: round6(v) for c, v in sorted(self.trust_by_coach.items())},
            "appearances": int(self.appearances),
            "appearances_by_club": {cl: int(v) for cl, v in sorted(self.appearances_by_club.items())},
            "bench_streak": int(self.bench_streak),
            "playing_time_satisfaction": round6(self.playing_time_satisfaction),
            "perceived_club_starter_bar": {
                cl: round6(v) for cl, v in sorted(self.perceived_club_starter_bar.items())
            },
            "match_style": self.match_style.to_dict(),
            "skill_memory_id": int(self.skill_memory_id),
            "gain_memory_ids": {m: int(v) for m, v in sorted(self.gain_memory_ids.items())},
            "trust_memory_ids": {c: int(v) for c, v in sorted(self.trust_memory_ids.items())},
            "career_memory_id": int(self.career_memory_id),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TransferPlayerMind:
        return cls(
            player_id=str(data["player_id"]),
            club_id=str(data["club_id"]),
            self_skill_estimate=_f(data["self_skill_estimate"], "self_skill_estimate"),
            self_fatigue_estimate=_f(data["self_fatigue_estimate"], "self_fatigue_estimate"),
            self_match_rating=_f(data["self_match_rating"], "self_match_rating"),
            expected_gains={
                m: _f(data["expected_gains"][m], f"expected_gains.{m}") for m in TRAINING_METHODS
            },
            preferences={
                m: _f(data["preferences"][m], f"preferences.{m}") for m in TRAINING_METHODS
            },
            autonomy=_f(data["autonomy"], "autonomy"),
            loyalty=_f(data["loyalty"], "loyalty"),
            trust_by_coach={
                c: _f(data["trust_by_coach"][c], f"trust_by_coach.{c}") for c in COACH_IDS
            },
            appearances=int(data["appearances"]),
            appearances_by_club={cl: int(data["appearances_by_club"][cl]) for cl in CLUB_IDS},
            bench_streak=int(data["bench_streak"]),
            playing_time_satisfaction=_f(
                data["playing_time_satisfaction"], "playing_time_satisfaction"
            ),
            perceived_club_starter_bar={
                cl: _f(
                    data["perceived_club_starter_bar"][cl], f"perceived_club_starter_bar.{cl}"
                )
                for cl in CLUB_IDS
            },
            match_style=MatchStyle.from_dict(data["match_style"]),
            skill_memory_id=int(data["skill_memory_id"]),
            gain_memory_ids={m: int(data["gain_memory_ids"][m]) for m in TRAINING_METHODS},
            trust_memory_ids={c: int(data["trust_memory_ids"][c]) for c in COACH_IDS},
            career_memory_id=int(data["career_memory_id"]),
        )


@dataclass
class CoachPlayerBelief:
    skill_estimate: float
    fatigue_estimate: float
    match_rating_estimate: float
    perceived_bench_streak: int
    last_seen: int
    last_match_seen: int
    method_residuals: dict[str, float]
    skill_memory_id: int
    match_memory_id: int
    residual_memory_ids: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_estimate": round6(self.skill_estimate),
            "fatigue_estimate": round6(self.fatigue_estimate),
            "match_rating_estimate": round6(self.match_rating_estimate),
            "perceived_bench_streak": int(self.perceived_bench_streak),
            "last_seen": int(self.last_seen),
            "last_match_seen": int(self.last_match_seen),
            "method_residuals": {m: round6(v) for m, v in sorted(self.method_residuals.items())},
            "skill_memory_id": int(self.skill_memory_id),
            "match_memory_id": int(self.match_memory_id),
            "residual_memory_ids": {m: int(v) for m, v in sorted(self.residual_memory_ids.items())},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CoachPlayerBelief:
        return cls(
            skill_estimate=_f(data["skill_estimate"], "skill_estimate"),
            fatigue_estimate=_f(data["fatigue_estimate"], "fatigue_estimate"),
            match_rating_estimate=_f(data["match_rating_estimate"], "match_rating_estimate"),
            perceived_bench_streak=int(data["perceived_bench_streak"]),
            last_seen=int(data["last_seen"]),
            last_match_seen=int(data["last_match_seen"]),
            method_residuals={
                m: _f(data["method_residuals"][m], f"method_residuals.{m}")
                for m in TRAINING_METHODS
            },
            skill_memory_id=int(data["skill_memory_id"]),
            match_memory_id=int(data["match_memory_id"]),
            residual_memory_ids={m: int(data["residual_memory_ids"][m]) for m in TRAINING_METHODS},
        )


@dataclass
class TransferCoachMind:
    coach_id: str
    club_id: str
    subjective_rates: dict[str, float]
    subjective_cap: float
    beliefs: dict[str, CoachPlayerBelief]

    def to_dict(self) -> dict[str, Any]:
        return {
            "coach_id": self.coach_id,
            "club_id": self.club_id,
            "subjective_rates": {m: round6(v) for m, v in sorted(self.subjective_rates.items())},
            "subjective_cap": round6(self.subjective_cap),
            "beliefs": {pid: b.to_dict() for pid, b in sorted(self.beliefs.items())},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TransferCoachMind:
        return cls(
            coach_id=str(data["coach_id"]),
            club_id=str(data["club_id"]),
            subjective_rates={
                m: _f(data["subjective_rates"][m], f"subjective_rates.{m}")
                for m in TRAINING_METHODS
            },
            subjective_cap=_f(data["subjective_cap"], "subjective_cap"),
            beliefs={
                pid: CoachPlayerBelief.from_dict(data["beliefs"][pid]) for pid in PLAYER_IDS
            },
        )


@dataclass
class TransferWorldState:
    seed: int
    day: int
    config: TransferConfig
    players: dict[str, TransferPlayerState]
    player_minds: dict[str, TransferPlayerMind]
    coaches: dict[str, TransferCoachMind]
    events: list[dict[str, Any]] = field(default_factory=list)
    next_event_id: int = 1

    def club_roster(self, club_id: str) -> tuple[str, ...]:
        return tuple(pid for pid in PLAYER_IDS if self.players[pid].club_id == club_id)

    def record(
        self,
        day: int,
        phase: str,
        kind: str,
        actor: str,
        target: str | None,
        cause_ids: tuple[int, ...] | list[int],
        **data: Any,
    ) -> int:
        eid = self.next_event_id
        self.next_event_id += 1
        entry = {
            "event_id": eid,
            "day": int(day),
            "phase": str(phase),
            "kind": str(kind),
            "actor": str(actor),
            "target": target,
            "cause_ids": [int(c) for c in cause_ids],
            "data": data,
        }
        self.events.append(entry)
        return eid

    def to_dict(self) -> dict[str, Any]:
        return {
            "world_kind": TRANSFER_WORLD_KIND,
            "seed": int(self.seed),
            "day": int(self.day),
            "next_event_id": int(self.next_event_id),
            "config": self.config.to_dict(),
            "players": {pid: p.to_dict() for pid, p in sorted(self.players.items())},
            "player_minds": {pid: m.to_dict() for pid, m in sorted(self.player_minds.items())},
            "coaches": {cid: c.to_dict() for cid, c in sorted(self.coaches.items())},
            "events": list(self.events),
        }


def transfer_identity() -> dict[str, Any]:
    base = package_identity()
    return {
        "world_kind": TRANSFER_WORLD_KIND,
        "save": TRANSFER_SAVE_VERSION,
        "rules": TRANSFER_RULE_VERSION,
        "policy": TRANSFER_POLICY_VERSION,
        "rng": RNG_VERSION,
        "author": TRANSFER_AUTHOR,
        "source_sha256": base["source_sha256"],
    }


def create_transfer_world(
    seed: int,
    *,
    condition: str = "coupled_transfer",
    config: TransferConfig | None = None,
) -> TransferWorldState:
    cfg = config if config is not None else config_for_condition(condition)

    raw_players: dict[str, dict[str, Any]] = {
        "p1": {
            "club_id": "club_a",
            "traits": MatchTraits(0.48, 0.51, 0.48),
            "cap": 0.88,
            "rates": {"A": 0.110, "B": 0.050},
            "fatigue": 0.10,
            "self_skill": 0.490,
            "self_match": 0.520,
            "expected": {"A": 0.045, "B": 0.040},
            "pref": {"A": 0.014, "B": 0.010},
            "autonomy": 0.28,
            "loyalty": 0.12,
            "trust": {"c1": 0.70, "c2": 0.62},
            "style": MatchStyle(directness=0.52, pass_bias=0.62, press_bias=0.48),
            "c1_prior": (0.50, 0.54),
            "c2_prior": (0.49, 0.52),
        },
        "p2": {
            "club_id": "club_a",
            "traits": MatchTraits(0.49, 0.45, 0.49),
            "cap": 0.88,
            "rates": {"A": 0.050, "B": 0.110},
            "fatigue": 0.10,
            "self_skill": 0.477,
            "self_match": 0.510,
            "expected": {"A": 0.040, "B": 0.045},
            "pref": {"A": 0.010, "B": 0.014},
            "autonomy": 0.30,
            "loyalty": 0.12,
            "trust": {"c1": 0.68, "c2": 0.62},
            "style": MatchStyle(directness=0.64, pass_bias=0.44, press_bias=0.56),
            "c1_prior": (0.49, 0.53),
            "c2_prior": (0.48, 0.51),
        },
        "p3": {
            "club_id": "club_a",
            "traits": MatchTraits(0.46, 0.47, 0.46),
            "cap": 0.94,
            "rates": {"A": 0.150, "B": 0.145},
            "fatigue": 0.08,
            "self_skill": 0.470,
            "self_match": 0.500,
            "expected": {"A": 0.048, "B": 0.047},
            "pref": {"A": 0.013, "B": 0.013},
            "autonomy": 0.26,
            "loyalty": cfg.p3_loyalty,
            "trust": {"c1": 0.66, "c2": 0.68},
            "style": MatchStyle(directness=0.58, pass_bias=0.56, press_bias=0.55),
            "c1_prior": (cfg.c1_p3_skill_prior, cfg.c1_p3_match_prior),
            "c2_prior": (cfg.c2_p3_skill_prior, cfg.c2_p3_match_prior),
        },
        "p4": {
            "club_id": "club_b",
            "traits": MatchTraits(0.49, 0.49, 0.50),
            "cap": 0.89,
            "rates": {"A": 0.105, "B": 0.105},
            "fatigue": 0.10,
            "self_skill": 0.493,
            "self_match": 0.520,
            "expected": {"A": 0.044, "B": 0.044},
            "pref": {"A": 0.012, "B": 0.012},
            "autonomy": 0.28,
            "loyalty": 0.14,
            "trust": {"c1": 0.60, "c2": 0.70},
            "style": MatchStyle(directness=0.58, pass_bias=0.54, press_bias=0.54),
            "c1_prior": (0.49, 0.52),
            "c2_prior": (0.50, 0.54),
        },
        "p5": {
            "club_id": "club_b",
            "traits": MatchTraits(0.41, 0.41, 0.41),
            "cap": 0.78,
            "rates": {"A": 0.045, "B": 0.045},
            "fatigue": 0.10,
            "self_skill": 0.410,
            "self_match": 0.420,
            "expected": {"A": 0.028, "B": 0.028},
            "pref": {"A": 0.010, "B": 0.010},
            "autonomy": 0.30,
            "loyalty": 0.14,
            "trust": {"c1": 0.60, "c2": 0.68},
            "style": MatchStyle(directness=0.54, pass_bias=0.50, press_bias=0.50),
            "c1_prior": (0.41, 0.42),
            "c2_prior": (0.42, 0.43),
        },
        "p6": {
            "club_id": "club_b",
            "traits": MatchTraits(0.39, 0.39, 0.39),
            "cap": 0.76,
            "rates": {"A": 0.040, "B": 0.040},
            "fatigue": 0.10,
            "self_skill": 0.390,
            "self_match": 0.390,
            "expected": {"A": 0.025, "B": 0.025},
            "pref": {"A": 0.009, "B": 0.009},
            "autonomy": 0.32,
            "loyalty": 0.14,
            "trust": {"c1": 0.60, "c2": 0.66},
            "style": MatchStyle(directness=0.52, pass_bias=0.48, press_bias=0.48),
            "c1_prior": (0.38, 0.38),
            "c2_prior": (0.39, 0.39),
        },
    }

    world = TransferWorldState(
        seed=int(seed),
        day=0,
        config=cfg,
        players={},
        player_minds={},
        coaches={},
    )

    init_eid = world.record(
        0,
        "init",
        "world_initialized",
        "system",
        None,
        (),
        seed=int(seed),
        condition=cfg.condition,
        author=TRANSFER_AUTHOR,
        config=cfg.to_dict(),
    )

    for pid in PLAYER_IDS:
        spec = raw_players[pid]
        p_eid = world.record(
            0,
            "init",
            "player_initialized",
            "system",
            pid,
            (init_eid,),
            club_id=spec["club_id"],
            traits=spec["traits"].to_dict(),
            cap=spec["cap"],
            self_skill_estimate=spec["self_skill"],
            self_fatigue_estimate=spec["fatigue"],
            self_match_rating=spec["self_match"],
            expected_gains=dict(spec["expected"]),
            preferences=dict(spec["pref"]),
            autonomy=spec["autonomy"],
            loyalty=spec["loyalty"],
            trust_by_coach=dict(spec["trust"]),
            playing_time_satisfaction=0.55,
            perceived_club_starter_bar={"club_a": 0.51, "club_b": 0.44},
        )
        world.players[pid] = TransferPlayerState(
            player_id=pid,
            initial_club_id=spec["club_id"],
            club_id=spec["club_id"],
            initial_traits=spec["traits"],
            traits=spec["traits"],
            cap=spec["cap"],
            rates=dict(spec["rates"]),
            fatigue=spec["fatigue"],
            last_state_id=p_eid,
            last_transfer_id=p_eid,
        )
        world.player_minds[pid] = TransferPlayerMind(
            player_id=pid,
            club_id=spec["club_id"],
            self_skill_estimate=spec["self_skill"],
            self_fatigue_estimate=spec["fatigue"],
            self_match_rating=spec["self_match"],
            expected_gains=dict(spec["expected"]),
            preferences=dict(spec["pref"]),
            autonomy=spec["autonomy"],
            loyalty=spec["loyalty"],
            trust_by_coach=dict(spec["trust"]),
            appearances=0,
            appearances_by_club={"club_a": 0, "club_b": 0},
            bench_streak=0,
            playing_time_satisfaction=0.55,
            perceived_club_starter_bar={"club_a": 0.51, "club_b": 0.44},
            match_style=spec["style"],
            skill_memory_id=p_eid,
            gain_memory_ids={"A": p_eid, "B": p_eid},
            trust_memory_ids={"c1": p_eid, "c2": p_eid},
            career_memory_id=p_eid,
        )

    for cid in COACH_IDS:
        club_id = COACH_CLUB_MAP[cid]
        init_beliefs_payload: dict[str, dict[str, Any]] = {}
        for pid in PLAYER_IDS:
            spec = raw_players[pid]
            sk_prior, m_prior = spec["c1_prior"] if cid == "c1" else spec["c2_prior"]
            init_beliefs_payload[pid] = {
                "skill_estimate": sk_prior,
                "fatigue_estimate": 0.10,
                "match_rating_estimate": m_prior,
            }
        c_eid = world.record(
            0,
            "init",
            "coach_initialized",
            "system",
            cid,
            (init_eid,),
            club_id=club_id,
            subjective_rates={"A": 0.095, "B": 0.095},
            subjective_cap=0.88,
            initial_beliefs=init_beliefs_payload,
        )
        beliefs: dict[str, CoachPlayerBelief] = {}
        for pid in PLAYER_IDS:
            spec = raw_players[pid]
            sk_prior, m_prior = spec["c1_prior"] if cid == "c1" else spec["c2_prior"]
            beliefs[pid] = CoachPlayerBelief(
                skill_estimate=sk_prior,
                fatigue_estimate=0.10,
                match_rating_estimate=m_prior,
                perceived_bench_streak=0,
                last_seen=0,
                last_match_seen=0,
                method_residuals={"A": 0.0, "B": 0.0},
                skill_memory_id=c_eid,
                match_memory_id=c_eid,
                residual_memory_ids={"A": c_eid, "B": c_eid},
            )
        world.coaches[cid] = TransferCoachMind(
            coach_id=cid,
            club_id=club_id,
            subjective_rates={"A": 0.095, "B": 0.095},
            subjective_cap=0.88,
            beliefs=beliefs,
        )

    return world


def coach_expected_gain(
    coach_mind: TransferCoachMind,
    player_id: str,
    method: str,
) -> dict[str, Any]:
    belief = coach_mind.beliefs[player_id]
    headroom = max(0.0, 1.0 - belief.skill_estimate / coach_mind.subjective_cap)
    fatigue_factor = max(0.25, 1.0 - 0.55 * belief.fatigue_estimate)
    base_gain = round6(coach_mind.subjective_rates[method] * headroom * fatigue_factor * 0.25)
    residual = round6(belief.method_residuals[method])
    expected = round6(base_gain + residual)
    return {
        "method": method,
        "base_gain": base_gain,
        "residual": residual,
        "expected_gain": expected,
        "residual_memory_id": int(belief.residual_memory_ids[method]),
    }


def decide_coach_training(
    coach_mind: TransferCoachMind,
    own_roster_ids: tuple[str, ...],
    other_roster_ids: tuple[str, ...],
    day: int,
    slot: int,
    chosen_in_day: list[dict[str, Any]],
    config: TransferConfig,
) -> dict[str, Any]:
    """Coach selects one slot action ('observe', 'propose', 'scout', or 'rest') using private beliefs only."""
    already_targeted = {
        str(c["target"]) for c in chosen_in_day if c.get("target") is not None
    }

    # Rank own club players by perceived selection value to identify starter focus and 2nd starter bar
    own_vals = sorted(
        (
            round6(
                config.select_skill_weight * coach_mind.beliefs[pid].skill_estimate
                + config.select_match_weight * coach_mind.beliefs[pid].match_rating_estimate
            ),
            pid,
        )
        for pid in own_roster_ids
    )
    own_vals.reverse()
    starter_2nd_val = own_vals[min(1, len(own_vals) - 1)][0]

    candidates: list[dict[str, Any]] = []

    for pid in own_roster_ids:
        if pid in already_targeted:
            continue
        b = coach_mind.beliefs[pid]
        staleness = max(0, int(day) - int(b.last_seen))
        dev_need = round6(config.coach_need_weight * max(0.0, coach_mind.subjective_cap - b.skill_estimate))
        starter_focus = round6(0.16 * b.match_rating_estimate)
        fatigue_drag = round6(-0.18 * b.fatigue_estimate)

        # Candidate 1: observe own player
        obs_staleness = round6(config.coach_staleness_weight * min(14, staleness))
        obs_components = {
            "development_need": round6(0.55 * dev_need),
            "starter_focus": starter_focus,
            "staleness_term": obs_staleness,
            "fatigue_drag": fatigue_drag,
        }
        obs_score = round6(sum(obs_components.values()))
        candidates.append(
            {
                "kind": "observe",
                "target": pid,
                "method": None,
                "score": obs_score,
                "components": obs_components,
            }
        )

        # Candidates 2 & 3: propose A or B to own player
        forecasts = {m: coach_expected_gain(coach_mind, pid, m) for m in TRAINING_METHODS}
        for method in TRAINING_METHODS:
            fc = forecasts[method]
            method_term = round6(
                config.coach_method_fit * (0.65 + 8.0 * max(0.0, fc["expected_gain"]))
            )
            freshness_bonus = round6(0.03 * max(0.0, 1.0 - staleness / 14.0))
            prop_components = {
                "development_need": dev_need,
                "starter_focus": starter_focus,
                "method_term": method_term,
                "freshness_bonus": freshness_bonus,
                "fatigue_drag": fatigue_drag,
            }
            prop_score = round6(sum(prop_components.values()))
            candidates.append(
                {
                    "kind": "propose",
                    "target": pid,
                    "method": method,
                    "score": prop_score,
                    "components": prop_components,
                    "forecasts": forecasts,
                }
            )

    # Scouting candidates for players in the other club (when room to sign or scouting is useful)
    if len(own_roster_ids) < MAX_CLUB_ROSTER:
        for pid in other_roster_ids:
            if pid in already_targeted:
                continue
            b = coach_mind.beliefs[pid]
            staleness = max(0, int(day) - int(b.last_seen))
            ext_val = round6(
                config.select_skill_weight * b.skill_estimate
                + config.select_match_weight * b.match_rating_estimate
            )
            upgrade_potential = round6(0.45 * max(0.0, ext_val - starter_2nd_val + 0.04))
            scout_staleness = round6(0.016 * min(21, staleness))
            bench_signal = round6(0.025 * min(4, b.perceived_bench_streak))
            scout_components = {
                "upgrade_potential": upgrade_potential,
                "staleness_term": scout_staleness,
                "bench_signal": bench_signal,
            }
            scout_score = round6(sum(scout_components.values()))
            candidates.append(
                {
                    "kind": "scout",
                    "target": pid,
                    "method": None,
                    "score": scout_score,
                    "components": scout_components,
                }
            )

    rest_components = {"rest_baseline": round6(config.coach_rest_value)}
    candidates.append(
        {
            "kind": "rest",
            "target": None,
            "method": None,
            "score": round6(config.coach_rest_value),
            "components": rest_components,
        }
    )

    chosen = max(candidates, key=lambda c: float(c["score"]))
    belief_refs: list[int] = []
    for pid in (*own_roster_ids, *other_roster_ids):
        b = coach_mind.beliefs[pid]
        belief_refs.extend([b.skill_memory_id, b.match_memory_id])
    return {
        "coach_id": coach_mind.coach_id,
        "club_id": coach_mind.club_id,
        "slot": int(slot),
        "candidates": candidates,
        "chosen": chosen,
        "belief_ids": sorted(set(belief_refs)),
    }


def decide_player_training(
    player_mind: TransferPlayerMind,
    morning_obs: dict[str, Any],
    proposals: list[dict[str, Any]],
    config: TransferConfig,
) -> dict[str, Any]:
    """Player selects training action ('A', 'B', or 'rest') using private beliefs and own-club coach proposals."""
    obs_fatigue = float(morning_obs["observed_fatigue"])
    obs_id = int(morning_obs["observation_id"])

    sat_deficit = max(0.0, 0.50 - player_mind.playing_time_satisfaction)
    effective_autonomy = round6(
        clamp(player_mind.autonomy + 0.18 * max(0.0, 0.45 - player_mind.playing_time_satisfaction), 0.0, 1.0)
    )
    career_drive = round6(config.satisfaction_drive_weight * sat_deficit)

    candidates: list[dict[str, Any]] = []
    memory_ids = [
        player_mind.skill_memory_id,
        player_mind.career_memory_id,
        player_mind.gain_memory_ids["A"],
        player_mind.gain_memory_ids["B"],
    ]

    for method in TRAINING_METHODS:
        matching_props = [p for p in proposals if p["method"] == method]
        advice_bonus = 0.0
        score_source: str | None = None
        if matching_props:
            best_prop = max(
                matching_props,
                key=lambda p: (player_mind.trust_by_coach[str(p["coach_id"])], str(p["coach_id"])),
            )
            cid = str(best_prop["coach_id"])
            trust_val = player_mind.trust_by_coach[cid]
            advice_bonus = round6(config.advice_weight * max(0.0, trust_val - effective_autonomy))
            score_source = cid
            memory_ids.append(player_mind.trust_memory_ids[cid])

        comps = {
            "expected_gain": round6(player_mind.expected_gains[method]),
            "preference": round6(player_mind.preferences[method]),
            "advice_bonus": advice_bonus,
            "career_drive": career_drive,
            "fatigue_cost": round6(-config.training_fatigue_weight * obs_fatigue),
        }
        score = round6(sum(comps.values()))
        candidates.append(
            {
                "action": method,
                "score": score,
                "score_source": score_source,
                "components": comps,
            }
        )

    rest_comps = {
        "fatigue_relief": round6(config.rest_fatigue_weight * obs_fatigue),
        "base_rest": 0.012,
    }
    rest_score = round6(sum(rest_comps.values()))
    candidates.append(
        {
            "action": "rest",
            "score": rest_score,
            "score_source": None,
            "components": rest_comps,
        }
    )

    chosen = max(candidates, key=lambda c: float(c["score"]))
    obs_ids = [obs_id] + [int(p["proposal_event_id"]) for p in proposals]
    return {
        "player_id": player_mind.player_id,
        "club_id": player_mind.club_id,
        "effective_autonomy": effective_autonomy,
        "candidates": candidates,
        "chosen": chosen,
        "observation_ids": sorted(set(obs_ids)),
        "memory_ids": sorted(set(memory_ids)),
    }


def decide_club_lineup(
    coach_mind: TransferCoachMind,
    club_roster_ids: tuple[str, ...],
    day: int,
    config: TransferConfig,
) -> dict[str, Any]:
    """Coach selects top 2 starters and bench from own club roster using private beliefs only."""
    if len(club_roster_ids) < MIN_CLUB_ROSTER:
        raise ValueError(f"club {coach_mind.club_id} has fewer than {MIN_CLUB_ROSTER} players")

    ranked: list[dict[str, Any]] = []
    belief_ids: list[int] = []
    for pid in club_roster_ids:
        b = coach_mind.beliefs[pid]
        belief_ids.extend([b.skill_memory_id, b.match_memory_id])
        comps = {
            "skill_term": round6(config.select_skill_weight * b.skill_estimate),
            "match_rating_term": round6(config.select_match_weight * b.match_rating_estimate),
            "fatigue_penalty": round6(-config.select_fatigue_weight * b.fatigue_estimate),
        }
        score = round6(sum(comps.values()))
        ranked.append(
            {
                "player_id": pid,
                "score": score,
                "components": comps,
                "beliefs_used": {
                    "skill_estimate": round6(b.skill_estimate),
                    "match_rating_estimate": round6(b.match_rating_estimate),
                    "fatigue_estimate": round6(b.fatigue_estimate),
                    "perceived_bench_streak": int(b.perceived_bench_streak),
                },
            }
        )

    ranked.sort(key=lambda r: (-float(r["score"]), str(r["player_id"])))
    starters = [str(r["player_id"]) for r in ranked[:2]]
    bench = [str(r["player_id"]) for r in ranked[2:]]
    return {
        "coach_id": coach_mind.coach_id,
        "club_id": coach_mind.club_id,
        "selection_day": int(day),
        "ranked_candidates": ranked,
        "starters": starters,
        "bench": bench,
        "belief_ids": sorted(set(belief_ids)),
    }


def decide_transfer_negotiation(
    player_mind: TransferPlayerMind,
    from_coach_mind: TransferCoachMind,
    to_coach_mind: TransferCoachMind,
    from_roster_ids: tuple[str, ...],
    to_roster_ids: tuple[str, ...],
    day: int,
    config: TransferConfig,
) -> dict[str, Any]:
    """Evaluate 3-party mutual agreement transfer using only private minds and roster sizes."""
    pid = player_mind.player_id
    from_club = player_mind.club_id
    to_club = to_coach_mind.club_id

    # 1. Player transfer request score
    req_comps = {
        "dissatisfaction_term": round6(0.55 * max(0.0, 0.48 - player_mind.playing_time_satisfaction)),
        "bench_streak_term": round6(0.075 * min(4, player_mind.bench_streak)),
        "ambition_term": round6(0.22 * max(0.0, player_mind.self_skill_estimate - 0.42)),
        "loyalty_drag": round6(-player_mind.loyalty),
    }
    request_score = round6(sum(req_comps.values()))
    wants_transfer = bool(request_score >= config.transfer_request_threshold)

    # 2. Releasing coach valuation and consent
    from_valuations: list[tuple[float, str]] = []
    for r_pid in from_roster_ids:
        b = from_coach_mind.beliefs[r_pid]
        val = round6(
            config.select_skill_weight * b.skill_estimate
            + config.select_match_weight * b.match_rating_estimate
        )
        from_valuations.append((val, r_pid))
    from_valuations.sort(key=lambda x: (-x[0], x[1]))
    from_rank_ids = [x[1] for x in from_valuations]
    player_rank_in_from = from_rank_ids.index(pid) + 1
    from_starter_2nd_val = from_valuations[min(1, len(from_valuations) - 1)][0]
    from_player_val = dict((k, v) for v, k in from_valuations)[pid]
    b_from = from_coach_mind.beliefs[pid]
    rel_comps = {
        "starter_2nd_gap": round6(from_starter_2nd_val - from_player_val),
        "bench_relief": round6(0.015 * min(4, b_from.perceived_bench_streak)),
    }
    release_margin = round6(sum(rel_comps.values()))
    roster_can_release = len(from_roster_ids) > MIN_CLUB_ROSTER
    release_consented = bool(
        roster_can_release and player_rank_in_from >= 3 and release_margin >= 0.0
    )

    # 3. Target coach offer valuation
    to_valuations: list[tuple[float, str]] = []
    for r_pid in to_roster_ids:
        b = to_coach_mind.beliefs[r_pid]
        val = round6(
            config.select_skill_weight * b.skill_estimate
            + config.select_match_weight * b.match_rating_estimate
        )
        to_valuations.append((val, r_pid))
    to_valuations.sort(key=lambda x: (-x[0], x[1]))
    to_starter_2nd_val = to_valuations[min(1, len(to_valuations) - 1)][0]
    b_to = to_coach_mind.beliefs[pid]
    to_player_val = round6(
        config.select_skill_weight * b_to.skill_estimate
        + config.select_match_weight * b_to.match_rating_estimate
    )
    offer_comps = {
        "target_player_valuation": to_player_val,
        "minus_current_starter_2nd": round6(-to_starter_2nd_val),
    }
    offer_margin = round6(sum(offer_comps.values()))
    roster_can_add = len(to_roster_ids) < MAX_CLUB_ROSTER
    offer_made = bool(roster_can_add and offer_margin >= config.transfer_offer_threshold)

    # 4. Player acceptance of target club offer
    opp_bar = player_mind.perceived_club_starter_bar[to_club]
    trust_to = player_mind.trust_by_coach[to_coach_mind.coach_id]
    acc_comps = {
        "opportunity_edge": round6(0.50 * (player_mind.self_skill_estimate - opp_bar)),
        "coach_trust_term": round6(0.30 * (trust_to - 0.50)),
        "push_dissatisfaction": round6(0.25 * max(0.0, 0.48 - player_mind.playing_time_satisfaction)),
        "loyalty_drag": round6(-player_mind.loyalty),
    }
    accept_score = round6(sum(acc_comps.values()))
    offer_accepted = bool(accept_score >= config.transfer_accept_threshold)

    transfer_approved = bool(
        config.transfer_mechanism == "enabled"
        and wants_transfer
        and release_consented
        and offer_made
        and offer_accepted
    )

    cause_ids = sorted(
        set(
            [
                player_mind.skill_memory_id,
                player_mind.career_memory_id,
                player_mind.trust_memory_ids[from_coach_mind.coach_id],
                player_mind.trust_memory_ids[to_coach_mind.coach_id],
                b_from.skill_memory_id,
                b_from.match_memory_id,
                b_to.skill_memory_id,
                b_to.match_memory_id,
            ]
        )
    )

    return {
        "window_day": int(day),
        "player_id": pid,
        "from_club": from_club,
        "to_club": to_club,
        "from_coach": from_coach_mind.coach_id,
        "to_coach": to_coach_mind.coach_id,
        "from_roster_size": len(from_roster_ids),
        "to_roster_size": len(to_roster_ids),
        "player_rank_in_from": player_rank_in_from,
        "request_score": request_score,
        "request_components": req_comps,
        "wants_transfer": wants_transfer,
        "release_margin": release_margin,
        "release_components": rel_comps,
        "release_consented": release_consented,
        "offer_margin": offer_margin,
        "offer_components": offer_comps,
        "offer_made": offer_made,
        "accept_score": accept_score,
        "accept_components": acc_comps,
        "offer_accepted": offer_accepted,
        "transfer_approved": transfer_approved,
        "cause_ids": cause_ids,
    }


def _extract_club_match_stats(
    match_world: m2d.MatchWorldState,
    club_a_starters: list[str],
    club_b_starters: list[str],
) -> dict[str, Any]:
    slot_map = {
        "h1": club_a_starters[0],
        "h2": club_a_starters[1],
        "a1": club_b_starters[0],
        "a2": club_b_starters[1],
    }
    stats_by_pid: dict[str, dict[str, int]] = {
        pid: {
            "goals": 0,
            "shots": 0,
            "pass_attempts": 0,
            "pass_completions": 0,
            "tackles_won": 0,
            "possession_ticks": 0,
        }
        for pid in (*club_a_starters, *club_b_starters)
    }

    home_poss = 0
    away_poss = 0
    for ev in match_world.events:
        kind = ev["kind"]
        if kind == "ball_kick":
            kicker = ev.get("kicker_id")
            if kicker in slot_map:
                pid = slot_map[kicker]
                if ev.get("kick_kind") == "shoot":
                    stats_by_pid[pid]["shots"] += 1
                elif ev.get("kick_kind") == "pass":
                    stats_by_pid[pid]["pass_attempts"] += 1
        elif kind == "ball_control" and ev.get("outcome") == "pass_completed":
            passer = ev.get("from_player_id")
            if passer in slot_map:
                stats_by_pid[slot_map[passer]]["pass_completions"] += 1
        elif kind == "tackle_resolution" and ev.get("outcome") == "tackle_won":
            challenger = ev.get("challenger_id")
            if challenger in slot_map:
                stats_by_pid[slot_map[challenger]]["tackles_won"] += 1
        elif kind == "goal":
            scorer = ev.get("scorer_id")
            if scorer in slot_map:
                stats_by_pid[slot_map[scorer]]["goals"] += 1
        elif kind == "tick_summary":
            owner = ev.get("ball", {}).get("owner_id")
            if owner in ("h1", "h2"):
                home_poss += 1
                stats_by_pid[slot_map[owner]]["possession_ticks"] += 1
            elif owner in ("a1", "a2"):
                away_poss += 1
                stats_by_pid[slot_map[owner]]["possession_ticks"] += 1

    return {
        "slot_map": slot_map,
        "stats_by_pid": stats_by_pid,
        "club_a_goals": int(match_world.score["home"]),
        "club_b_goals": int(match_world.score["away"]),
        "club_a_possession_ticks": home_poss,
        "club_b_possession_ticks": away_poss,
    }


def _run_head_to_head_match(
    world: TransferWorldState,
    day: int,
    club_a_starters: list[str],
    club_b_starters: list[str],
) -> tuple[m2d.MatchWorldState, dict[str, Any]]:
    cfg = world.config
    match_seed = int(world.seed) * 10007 + int(day) * 97
    match_cfg = MatchConfig(
        condition="baseline",
        noise_scale=cfg.match_noise_scale,
    )
    match_world = m2d.create_match_world(seed=match_seed, condition="baseline", config=match_cfg)

    slot_assignments = [
        ("h1", "home", club_a_starters[0]),
        ("h2", "home", club_a_starters[1]),
        ("a1", "away", club_b_starters[0]),
        ("a2", "away", club_b_starters[1]),
    ]
    for slot_id, team_name, pid in slot_assignments:
        p_state = world.players[pid]
        p_mind = world.player_minds[pid]
        base_traits = (
            p_state.traits
            if cfg.training_match_coupling == "coupled"
            else p_state.initial_traits
        )
        if cfg.training_match_coupling == "coupled":
            f = p_state.fatigue
            eff_traits = MatchTraits(
                pace=round6(clamp(base_traits.pace * (1.0 - 0.18 * f), 0.15, 1.0)),
                passing=round6(clamp(base_traits.passing * (1.0 - 0.10 * f), 0.15, 1.0)),
                shooting=round6(clamp(base_traits.shooting * (1.0 - 0.10 * f), 0.15, 1.0)),
            )
        else:
            eff_traits = base_traits

        old_mp = match_world.players[slot_id]
        match_world.players[slot_id] = PlayerMatchState(
            player_id=slot_id,
            team=team_name,
            slot=old_mp.slot,
            x=old_mp.x,
            y=old_mp.y,
            traits=eff_traits,
            mind=PlayerMatchMind(
                player_id=slot_id,
                team=team_name,
                style=p_mind.match_style,
                coach_directive="balanced",
            ),
        )

    m2d.run_match(cfg.match_ticks, world=match_world)

    stats = _extract_club_match_stats(match_world, club_a_starters, club_b_starters)
    return match_world, stats


def _step_training_day(world: TransferWorldState, day: int) -> None:
    cfg = world.config

    # 1. Morning self-observations for all 6 players
    morning_obs_by_pid: dict[str, dict[str, Any]] = {}
    for pid in PLAYER_IDS:
        p = world.players[pid]
        pm = world.player_minds[pid]
        s_noise = uniform_from_key(world.seed, day, "morning_self_skill", pid, low=-cfg.self_noise, high=cfg.self_noise)
        f_noise = uniform_from_key(world.seed, day, "morning_self_fatigue", pid, low=-cfg.self_noise, high=cfg.self_noise)
        obs_skill = round6(clamp(p.mean_skill + s_noise, 0.0, 1.0))
        obs_fatigue = round6(clamp(p.fatigue + f_noise, 0.0, 1.0))
        obs_id = world.record(
            day,
            "morning",
            "self_observation",
            pid,
            pid,
            (p.last_state_id,),
            club_id=p.club_id,
            observed_skill=obs_skill,
            observed_fatigue=obs_fatigue,
        )
        old_est = pm.self_skill_estimate
        pm.self_skill_estimate = round6(
            clamp((1.0 - cfg.player_skill_alpha) * old_est + cfg.player_skill_alpha * obs_skill, 0.0, 1.0)
        )
        pm.self_fatigue_estimate = obs_fatigue
        mem_id = world.record(
            day,
            "morning",
            "self_belief_update",
            pid,
            pid,
            (obs_id, pm.skill_memory_id),
            skill_estimate_before=round6(old_est),
            skill_estimate_after=pm.self_skill_estimate,
            fatigue_estimate_after=obs_fatigue,
        )
        pm.skill_memory_id = mem_id
        morning_obs_by_pid[pid] = {
            "observation_id": obs_id,
            "observed_skill": obs_skill,
            "observed_fatigue": obs_fatigue,
        }

    # 2. Coach slots (2 slots per coach)
    proposals_by_pid: dict[str, list[dict[str, Any]]] = {pid: [] for pid in PLAYER_IDS}
    observed_own_sessions: dict[str, list[tuple[str, int]]] = {pid: [] for pid in PLAYER_IDS}
    scouted_external_sessions: dict[str, list[tuple[str, int]]] = {pid: [] for pid in PLAYER_IDS}

    for cid in COACH_IDS:
        coach = world.coaches[cid]
        own_club = coach.club_id
        other_club = OTHER_CLUB_MAP[own_club]
        own_roster = world.club_roster(own_club)
        other_roster = world.club_roster(other_club)
        chosen_in_day: list[dict[str, Any]] = []

        for slot in (1, 2):
            dec = decide_coach_training(
                coach,
                own_roster,
                other_roster,
                day,
                slot,
                chosen_in_day,
                cfg,
            )
            chosen = dec["chosen"]
            chosen_in_day.append(chosen)
            dec_id = world.record(
                day,
                "coach",
                "coach_training_decision",
                cid,
                chosen["target"],
                dec["belief_ids"],
                club_id=own_club,
                slot=slot,
                chosen=chosen,
                candidates=dec["candidates"],
            )
            kind = chosen["kind"]
            target = chosen["target"]
            if kind == "propose" and target is not None:
                method = str(chosen["method"])
                fc = chosen["forecasts"][method]
                prop_id = world.record(
                    day,
                    "coach",
                    "training_proposal",
                    cid,
                    target,
                    (dec_id,),
                    club_id=own_club,
                    slot=slot,
                    method=method,
                    expected_gain=fc["expected_gain"],
                    base_gain=fc["base_gain"],
                    residual=fc["residual"],
                )
                proposals_by_pid[target].append(
                    {
                        "coach_id": cid,
                        "club_id": own_club,
                        "method": method,
                        "expected_gain": fc["expected_gain"],
                        "proposal_event_id": prop_id,
                    }
                )
            elif kind == "observe" and target is not None:
                observed_own_sessions[target].append((cid, dec_id))
            elif kind == "scout" and target is not None:
                scouted_external_sessions[target].append((cid, dec_id))

    # 3. Player training decisions and resolution
    for pid in PLAYER_IDS:
        p = world.players[pid]
        pm = world.player_minds[pid]
        # Players only receive proposals from their current club's coach
        own_props = [pr for pr in proposals_by_pid[pid] if pr["club_id"] == p.club_id]
        p_dec = decide_player_training(pm, morning_obs_by_pid[pid], own_props, cfg)
        chosen = p_dec["chosen"]
        action = str(chosen["action"])
        p_dec_id = world.record(
            day,
            "player",
            "player_training_decision",
            pid,
            pid,
            tuple(sorted(set(p_dec["observation_ids"] + p_dec["memory_ids"]))),
            club_id=p.club_id,
            effective_autonomy=p_dec["effective_autonomy"],
            chosen=chosen,
            candidates=p_dec["candidates"],
        )

        old_traits = p.traits
        old_mean = p.mean_skill
        old_fatigue = p.fatigue

        if action in TRAINING_METHODS:
            r_m = p.rates[action]
            headroom = max(0.0, 1.0 - old_mean / p.cap)
            fatigue_factor = max(0.25, 1.0 - 0.55 * old_fatigue)
            base_delta = r_m * headroom * fatigue_factor * 0.25
            w_pace, w_pass, w_shoot = METHOD_TRAIT_WEIGHTS[action]
            new_traits = MatchTraits(
                pace=round6(clamp(old_traits.pace + base_delta * w_pace, 0.0, p.cap)),
                passing=round6(clamp(old_traits.passing + base_delta * w_pass, 0.0, p.cap)),
                shooting=round6(clamp(old_traits.shooting + base_delta * w_shoot, 0.0, p.cap)),
            )
            new_fatigue = round6(clamp(old_fatigue + 0.085, 0.0, 1.0))
        else:
            new_traits = old_traits
            new_fatigue = round6(clamp(old_fatigue - 0.14, 0.0, 1.0))

        p.traits = new_traits
        p.fatigue = new_fatigue
        new_mean = p.mean_skill
        true_gain = round6(new_mean - old_mean)

        state_id = world.record(
            day,
            "outcome",
            "state_update",
            pid,
            pid,
            (p_dec_id, p.last_state_id),
            club_id=p.club_id,
            action=action,
            traits_before=old_traits.to_dict(),
            traits_after=new_traits.to_dict(),
            skill_before=old_mean,
            skill_after=new_mean,
            true_gain=true_gain,
            fatigue_before=round6(old_fatigue),
            fatigue_after=new_fatigue,
        )
        p.last_state_id = state_id

        # Player evening observation and belief updates
        if action in TRAINING_METHODS:
            ev_noise = uniform_from_key(
                world.seed, day, "evening_self_gain", pid, low=-cfg.self_noise, high=cfg.self_noise
            )
            obs_gain = round6(true_gain + ev_noise)
            ev_obs_id = world.record(
                day,
                "evening",
                "training_gain_observation",
                pid,
                pid,
                (state_id,),
                method=action,
                observed_gain=obs_gain,
            )

            self_pred = pm.expected_gains[action]
            for pr in own_props:
                if pr["method"] == action:
                    cid = str(pr["coach_id"])
                    c_pred = float(pr["expected_gain"])
                    self_sq = round6((obs_gain - self_pred) ** 2)
                    coach_sq = round6((obs_gain - c_pred) ** 2)
                    delta_err = round6(self_sq - coach_sq)
                    old_trust = pm.trust_by_coach[cid]
                    new_trust = round6(
                        clamp(old_trust + cfg.trust_alpha * cfg.trust_scale * delta_err, 0.05, 0.98)
                    )
                    pm.trust_by_coach[cid] = new_trust
                    t_id = world.record(
                        day,
                        "belief",
                        "advice_feedback",
                        pid,
                        cid,
                        (ev_obs_id, int(pr["proposal_event_id"]), pm.trust_memory_ids[cid]),
                        method=action,
                        observed_gain=obs_gain,
                        self_prediction=round6(self_pred),
                        coach_prediction=round6(c_pred),
                        self_squared_error=self_sq,
                        coach_squared_error=coach_sq,
                        trust_before=round6(old_trust),
                        trust_after=new_trust,
                    )
                    pm.trust_memory_ids[cid] = t_id

            old_g = pm.expected_gains[action]
            new_g = round6(clamp((1.0 - cfg.player_gain_alpha) * old_g + cfg.player_gain_alpha * obs_gain, -0.25, 0.25))
            pm.expected_gains[action] = new_g
            g_id = world.record(
                day,
                "belief",
                "expected_gain_update",
                pid,
                pid,
                (ev_obs_id, pm.gain_memory_ids[action]),
                method=action,
                expected_gain_before=round6(old_g),
                expected_gain_after=new_g,
            )
            pm.gain_memory_ids[action] = g_id

        # Own coach session observation updates
        for cid, dec_id in observed_own_sessions[pid]:
            coach = world.coaches[cid]
            b = coach.beliefs[pid]
            c_s_noise = uniform_from_key(
                world.seed, day, "coach_obs_skill", f"{cid}:{pid}", low=-cfg.coach_noise, high=cfg.coach_noise
            )
            c_f_noise = uniform_from_key(
                world.seed, day, "coach_obs_fatigue", f"{cid}:{pid}", low=-cfg.coach_noise, high=cfg.coach_noise
            )
            c_obs_skill = round6(clamp(new_mean + c_s_noise, 0.0, 1.0))
            c_obs_fat = round6(clamp(new_fatigue + c_f_noise, 0.0, 1.0))
            old_c_skill = b.skill_estimate
            b.skill_estimate = round6(
                clamp((1.0 - cfg.coach_skill_alpha) * old_c_skill + cfg.coach_skill_alpha * c_obs_skill, 0.0, 1.0)
            )
            b.fatigue_estimate = c_obs_fat
            b.last_seen = day
            c_mem_id = world.record(
                day,
                "belief",
                "coach_observation_update",
                cid,
                pid,
                (dec_id, state_id, b.skill_memory_id),
                observed_skill=c_obs_skill,
                observed_fatigue=c_obs_fat,
                skill_estimate_before=round6(old_c_skill),
                skill_estimate_after=b.skill_estimate,
            )
            b.skill_memory_id = c_mem_id

            if action in TRAINING_METHODS:
                c_g_noise = uniform_from_key(
                    world.seed, day, "coach_obs_gain", f"{cid}:{pid}", low=-cfg.coach_noise, high=cfg.coach_noise
                )
                c_obs_gain = round6(true_gain + c_g_noise)
                fc = coach_expected_gain(coach, pid, action)
                target_res = round6(c_obs_gain - fc["base_gain"])
                old_res = b.method_residuals[action]
                new_res = round6(
                    clamp(
                        (1.0 - cfg.coach_residual_alpha) * old_res + cfg.coach_residual_alpha * target_res,
                        -0.15,
                        0.15,
                    )
                )
                b.method_residuals[action] = new_res
                r_mem_id = world.record(
                    day,
                    "belief",
                    "coach_residual_update",
                    cid,
                    pid,
                    (c_mem_id, b.residual_memory_ids[action]),
                    method=action,
                    observed_gain=c_obs_gain,
                    residual_before=round6(old_res),
                    residual_after=new_res,
                )
                b.residual_memory_ids[action] = r_mem_id

        # External coach scouting updates
        for cid, dec_id in scouted_external_sessions[pid]:
            coach = world.coaches[cid]
            b = coach.beliefs[pid]
            sc_s_noise = uniform_from_key(
                world.seed, day, "scout_obs_skill", f"{cid}:{pid}", low=-cfg.scout_noise, high=cfg.scout_noise
            )
            sc_m_noise = uniform_from_key(
                world.seed, day, "scout_obs_match", f"{cid}:{pid}", low=-cfg.scout_noise, high=cfg.scout_noise
            )
            sc_obs_skill = round6(clamp(new_mean + sc_s_noise, 0.0, 1.0))
            sc_obs_match = round6(clamp(new_mean + 0.03 + sc_m_noise, 0.0, 1.0))
            old_sc_skill = b.skill_estimate
            old_sc_match = b.match_rating_estimate
            b.skill_estimate = round6(
                clamp((1.0 - cfg.coach_scout_alpha) * old_sc_skill + cfg.coach_scout_alpha * sc_obs_skill, 0.0, 1.0)
            )
            b.match_rating_estimate = round6(
                clamp((1.0 - cfg.coach_scout_alpha) * old_sc_match + cfg.coach_scout_alpha * sc_obs_match, 0.0, 1.0)
            )
            b.last_seen = day
            sc_mem_id = world.record(
                day,
                "belief",
                "coach_scout_update",
                cid,
                pid,
                (dec_id, state_id, b.skill_memory_id, b.match_memory_id),
                observed_skill=sc_obs_skill,
                observed_match_potential=sc_obs_match,
                skill_estimate_before=round6(old_sc_skill),
                skill_estimate_after=b.skill_estimate,
                match_rating_before=round6(old_sc_match),
                match_rating_after=b.match_rating_estimate,
            )
            b.skill_memory_id = sc_mem_id
            b.match_memory_id = sc_mem_id


def _step_match_day(world: TransferWorldState, day: int) -> None:
    cfg = world.config

    # 1. Morning lineup selections for both clubs
    lineup_by_club: dict[str, dict[str, Any]] = {}
    lineup_event_ids: list[int] = []
    for club_id in CLUB_IDS:
        cid = CLUB_COACH_MAP[club_id]
        coach = world.coaches[cid]
        roster = world.club_roster(club_id)
        lu = decide_club_lineup(coach, roster, day, cfg)
        lu_id = world.record(
            day,
            "match_morning",
            "lineup_decision",
            cid,
            club_id,
            lu["belief_ids"],
            club_id=club_id,
            starters=lu["starters"],
            bench=lu["bench"],
            ranked_candidates=lu["ranked_candidates"],
        )
        lineup_by_club[club_id] = lu
        lineup_event_ids.append(lu_id)

    club_a_starters = lineup_by_club["club_a"]["starters"]
    club_b_starters = lineup_by_club["club_b"]["starters"]

    # 2. Head-to-head 2D match episode
    match_world, match_stats = _run_head_to_head_match(
        world, day, club_a_starters, club_b_starters
    )
    stats_by_pid = match_stats["stats_by_pid"]
    club_a_goals = match_stats["club_a_goals"]
    club_b_goals = match_stats["club_b_goals"]

    match_causes = list(lineup_event_ids) + [
        world.players[pid].last_state_id for pid in (*club_a_starters, *club_b_starters)
    ]
    match_ev_id = world.record(
        day,
        "match",
        "match_completed",
        "system",
        None,
        tuple(sorted(set(match_causes))),
        club_a_starters=club_a_starters,
        club_a_bench=lineup_by_club["club_a"]["bench"],
        club_b_starters=club_b_starters,
        club_b_bench=lineup_by_club["club_b"]["bench"],
        club_a_goals=club_a_goals,
        club_b_goals=club_b_goals,
        club_a_possession_ticks=match_stats["club_a_possession_ticks"],
        club_b_possession_ticks=match_stats["club_b_possession_ticks"],
        starter_stats=stats_by_pid,
        match_ticks=cfg.match_ticks,
    )

    # 3. Post-match physical updates, player career belief updates, and coach match belief updates
    starter_perf_by_club: dict[str, list[float]] = {"club_a": [], "club_b": []}
    for pid in (*club_a_starters, *club_b_starters):
        p_club = world.players[pid].club_id
        opp_g = club_b_goals if p_club == "club_a" else club_a_goals
        st = stats_by_pid[pid]
        raw_p = (
            0.28
            + 0.18 * st["goals"]
            + 0.035 * st["shots"]
            + 0.035 * st["pass_completions"]
            + 0.018 * st["tackles_won"]
            + 0.007 * st["possession_ticks"]
            - 0.095 * opp_g
        )
        starter_perf_by_club[p_club].append(clamp(raw_p, 0.15, 0.95))

    for pid in PLAYER_IDS:
        p = world.players[pid]
        pm = world.player_minds[pid]
        old_traits = p.traits
        old_mean = p.mean_skill
        old_fatigue = p.fatigue
        started = pid in stats_by_pid

        if started:
            st = stats_by_pid[pid]
            exertion = 0.06 + 0.002 * (st["tackles_won"] + st["shots"])
            new_fatigue = round6(clamp(old_fatigue + exertion, 0.0, 1.0))
            activity_factor = (
                0.55
                + 0.08 * st["goals"]
                + 0.03 * st["pass_completions"]
                + 0.025 * st["shots"]
                + 0.02 * st["tackles_won"]
            )
            match_delta = 0.036 * (1.0 - old_mean / p.cap) * activity_factor
            new_traits = MatchTraits(
                pace=round6(clamp(old_traits.pace + match_delta * 0.95, 0.0, p.cap)),
                passing=round6(clamp(old_traits.passing + match_delta * 1.05, 0.0, p.cap)),
                shooting=round6(clamp(old_traits.shooting + match_delta * 1.00, 0.0, p.cap)),
            )
        else:
            new_fatigue = round6(clamp(old_fatigue - 0.05, 0.0, 1.0))
            new_traits = old_traits

        p.traits = new_traits
        p.fatigue = new_fatigue
        state_id = world.record(
            day,
            "outcome",
            "state_update",
            pid,
            pid,
            (match_ev_id, p.last_state_id),
            club_id=p.club_id,
            action="match_starter" if started else "match_bench",
            traits_before=old_traits.to_dict(),
            traits_after=new_traits.to_dict(),
            skill_before=old_mean,
            skill_after=p.mean_skill,
            fatigue_before=round6(old_fatigue),
            fatigue_after=new_fatigue,
        )
        p.last_state_id = state_id

        opp_goals = club_b_goals if p.club_id == "club_a" else club_a_goals
        old_sat = pm.playing_time_satisfaction
        old_self_m = pm.self_match_rating
        if started:
            st = stats_by_pid[pid]
            pm.appearances += 1
            pm.appearances_by_club[p.club_id] += 1
            pm.bench_streak = 0
            new_sat = round6(clamp(old_sat + 0.08, 0.0, 1.0))
            raw_perf = (
                0.28
                + 0.18 * st["goals"]
                + 0.035 * st["shots"]
                + 0.035 * st["pass_completions"]
                + 0.018 * st["tackles_won"]
                + 0.007 * st["possession_ticks"]
                - 0.095 * opp_goals
            )
            p_m_noise = uniform_from_key(
                world.seed, day, "self_match_obs", pid, low=-cfg.match_obs_noise, high=cfg.match_obs_noise
            )
            obs_perf = round6(clamp(raw_perf + p_m_noise, 0.0, 1.0))
            new_self_m = round6(clamp(0.65 * old_self_m + 0.35 * obs_perf, 0.0, 1.0))
        else:
            pm.bench_streak += 1
            new_sat = round6(clamp(old_sat - 0.10, 0.0, 1.0))
            new_self_m = old_self_m

        pm.playing_time_satisfaction = new_sat
        pm.self_match_rating = new_self_m
        for club_id in CLUB_IDS:
            if starter_perf_by_club[club_id]:
                obs_bar = min(starter_perf_by_club[club_id])
                old_bar = pm.perceived_club_starter_bar[club_id]
                pm.perceived_club_starter_bar[club_id] = round6(
                    clamp(0.70 * old_bar + 0.30 * obs_bar, 0.15, 0.90)
                )

        c_mem_id = world.record(
            day,
            "belief",
            "player_career_belief",
            pid,
            pid,
            (match_ev_id, pm.career_memory_id),
            club_id=p.club_id,
            started=started,
            appearances=pm.appearances,
            appearances_by_club=dict(pm.appearances_by_club),
            bench_streak=pm.bench_streak,
            satisfaction_before=round6(old_sat),
            satisfaction_after=new_sat,
            self_match_rating_before=round6(old_self_m),
            self_match_rating_after=new_self_m,
            perceived_club_starter_bar_after=dict(pm.perceived_club_starter_bar),
        )
        pm.career_memory_id = c_mem_id

        # Both coaches observe the 4 starters who played on the pitch and update perceived_bench_streak for all
        for cid in COACH_IDS:
            coach = world.coaches[cid]
            b = coach.beliefs[pid]
            if started:
                st = stats_by_pid[pid]
                c_m_noise = uniform_from_key(
                    world.seed, day, "coach_match_obs", f"{cid}:{pid}", low=-cfg.match_obs_noise, high=cfg.match_obs_noise
                )
                raw_perf = (
                    0.28
                    + 0.18 * st["goals"]
                    + 0.035 * st["shots"]
                    + 0.035 * st["pass_completions"]
                    + 0.018 * st["tackles_won"]
                    + 0.007 * st["possession_ticks"]
                    - 0.095 * opp_goals
                )
                c_obs_rating = round6(clamp(raw_perf + c_m_noise, 0.0, 1.0))
                old_m_est = b.match_rating_estimate
                old_s_est = b.skill_estimate
                b.match_rating_estimate = round6(
                    clamp((1.0 - cfg.coach_match_alpha) * old_m_est + cfg.coach_match_alpha * c_obs_rating, 0.0, 1.0)
                )
                b.skill_estimate = round6(
                    clamp(
                        (1.0 - cfg.coach_match_skill_blend) * old_s_est
                        + cfg.coach_match_skill_blend * c_obs_rating,
                        0.0,
                        1.0,
                    )
                )
                if coach.club_id == p.club_id:
                    f_obs_noise = uniform_from_key(
                        world.seed, day, "coach_match_fat", f"{cid}:{pid}", low=-cfg.coach_noise, high=cfg.coach_noise
                    )
                    b.fatigue_estimate = round6(clamp(p.fatigue + f_obs_noise, 0.0, 1.0))
                b.perceived_bench_streak = 0
                b.last_match_seen = day
                b.last_seen = day
                cm_id = world.record(
                    day,
                    "belief",
                    "coach_match_belief",
                    cid,
                    pid,
                    (match_ev_id, b.match_memory_id, b.skill_memory_id),
                    started=True,
                    observed_match_rating=c_obs_rating,
                    match_rating_before=round6(old_m_est),
                    match_rating_after=b.match_rating_estimate,
                    skill_estimate_before=round6(old_s_est),
                    skill_estimate_after=b.skill_estimate,
                    fatigue_estimate_after=round6(b.fatigue_estimate),
                    perceived_bench_streak=b.perceived_bench_streak,
                )
                b.match_memory_id = cm_id
                b.skill_memory_id = cm_id
            else:
                b.perceived_bench_streak += 1
                if coach.club_id == p.club_id:
                    b.fatigue_estimate = round6(clamp(b.fatigue_estimate - 0.05, 0.0, 1.0))
                cm_id = world.record(
                    day,
                    "belief",
                    "coach_match_belief",
                    cid,
                    pid,
                    (match_ev_id, b.match_memory_id),
                    started=False,
                    perceived_bench_streak=b.perceived_bench_streak,
                    match_rating_before=round6(b.match_rating_estimate),
                    match_rating_after=round6(b.match_rating_estimate),
                    fatigue_estimate_after=round6(b.fatigue_estimate),
                )
                b.match_memory_id = cm_id


def _step_transfer_window(world: TransferWorldState, day: int) -> None:
    cfg = world.config
    for pid in PLAYER_IDS:
        p = world.players[pid]
        pm = world.player_minds[pid]
        from_club = p.club_id
        to_club = OTHER_CLUB_MAP[from_club]
        from_cid = CLUB_COACH_MAP[from_club]
        to_cid = CLUB_COACH_MAP[to_club]
        from_coach = world.coaches[from_cid]
        to_coach = world.coaches[to_cid]
        from_roster = world.club_roster(from_club)
        to_roster = world.club_roster(to_club)

        neg = decide_transfer_negotiation(
            pm,
            from_coach,
            to_coach,
            from_roster,
            to_roster,
            day,
            cfg,
        )
        neg_id = world.record(
            day,
            "transfer_window",
            "transfer_window_decision",
            pid,
            to_club,
            neg["cause_ids"],
            **{k: v for k, v in neg.items() if k != "cause_ids"},
        )
        if neg["transfer_approved"]:
            old_club = p.club_id
            p.club_id = to_club
            pm.club_id = to_club
            pm.bench_streak = 0
            old_sat = pm.playing_time_satisfaction
            pm.playing_time_satisfaction = round6(max(old_sat, 0.52))
            for cid in COACH_IDS:
                world.coaches[cid].beliefs[pid].perceived_bench_streak = 0
            tr_id = world.record(
                day,
                "transfer_window",
                "transfer_completed",
                pid,
                to_club,
                (neg_id, p.last_transfer_id, pm.career_memory_id),
                player_id=pid,
                from_club=old_club,
                to_club=to_club,
                from_coach=from_cid,
                to_coach=to_cid,
                satisfaction_before=round6(old_sat),
                satisfaction_after=pm.playing_time_satisfaction,
            )
            p.last_transfer_id = tr_id
            pm.career_memory_id = tr_id


def step_transfer_day(world: TransferWorldState) -> None:
    day = world.day + 1
    world.day = day
    if day % world.config.match_interval_days == 0:
        _step_match_day(world, day)
        if day in TRANSFER_WINDOW_DAYS:
            _step_transfer_window(world, day)
    else:
        _step_training_day(world, day)


def run_transfer_world(
    days: int,
    *,
    seed: int = 17,
    condition: str = "coupled_transfer",
    config: TransferConfig | None = None,
    world: TransferWorldState | None = None,
) -> TransferWorldState:
    if days < 0:
        raise ValueError("days must be >= 0")
    active = world if world is not None else create_transfer_world(seed, condition=condition, config=config)
    for _ in range(days):
        step_transfer_day(active)
    return active


def summarize_transfer_world(world: TransferWorldState) -> dict[str, Any]:
    matches = [e for e in world.events if e["kind"] == "match_completed"]
    transfers = [e for e in world.events if e["kind"] == "transfer_completed"]
    negotiations = [e for e in world.events if e["kind"] == "transfer_window_decision"]

    club_a_goals = sum(int(m["data"]["club_a_goals"]) for m in matches)
    club_b_goals = sum(int(m["data"]["club_b_goals"]) for m in matches)
    club_a_poss = sum(int(m["data"]["club_a_possession_ticks"]) for m in matches)
    club_b_poss = sum(int(m["data"]["club_b_possession_ticks"]) for m in matches)

    half_idx = len(matches) // 2
    second_half = matches[half_idx:]
    sh_club_a_goals = sum(int(m["data"]["club_a_goals"]) for m in second_half)
    sh_club_b_goals = sum(int(m["data"]["club_b_goals"]) for m in second_half)

    club_a_shots = 0
    club_b_shots = 0
    club_a_passes = 0
    club_b_passes = 0
    sh_club_b_shots = 0
    for idx, m in enumerate(matches):
        st_map = m["data"]["starter_stats"]
        for pid in m["data"]["club_a_starters"]:
            club_a_shots += int(st_map[pid]["shots"])
            club_a_passes += int(st_map[pid]["pass_completions"])
        for pid in m["data"]["club_b_starters"]:
            club_b_shots += int(st_map[pid]["shots"])
            club_b_passes += int(st_map[pid]["pass_completions"])
            if idx >= half_idx:
                sh_club_b_shots += int(st_map[pid]["shots"])

    proposals_by_coach_target: dict[tuple[str, str], int] = {}
    for e in world.events:
        if e["kind"] == "training_proposal":
            key = (str(e["actor"]), str(e["target"]))
            proposals_by_coach_target[key] = proposals_by_coach_target.get(key, 0) + 1

    players_summary: dict[str, Any] = {}
    gains: list[float] = []
    for pid in PLAYER_IDS:
        p = world.players[pid]
        pm = world.player_minds[pid]
        gain = round6(p.mean_skill - p.initial_mean_skill)
        gains.append(gain)
        p_transfers = [t for t in transfers if t["data"]["player_id"] == pid]
        p_requests = [
            n for n in negotiations if n["data"]["player_id"] == pid and n["data"]["wants_transfer"]
        ]
        players_summary[pid] = {
            "initial_club": p.initial_club_id,
            "final_club": p.club_id,
            "transferred": int(len(p_transfers) > 0),
            "transfer_count": len(p_transfers),
            "transfer_requests": len(p_requests),
            "initial_mean_skill": p.initial_mean_skill,
            "final_mean_skill": p.mean_skill,
            "skill_gain": gain,
            "traits": p.traits.to_dict(),
            "fatigue": round6(p.fatigue),
            "appearances": pm.appearances,
            "appearances_by_club": dict(pm.appearances_by_club),
            "bench_streak": pm.bench_streak,
            "playing_time_satisfaction": round6(pm.playing_time_satisfaction),
            "proposals_from_c1": proposals_by_coach_target.get(("c1", pid), 0),
            "proposals_from_c2": proposals_by_coach_target.get(("c2", pid), 0),
            "trust_by_coach": {c: round6(v) for c, v in sorted(pm.trust_by_coach.items())},
        }

    p3_s = players_summary["p3"]
    return {
        "world_kind": TRANSFER_WORLD_KIND,
        "author": TRANSFER_AUTHOR,
        "seed": world.seed,
        "days": world.day,
        "condition": world.config.condition,
        "matches_played": len(matches),
        "total_transfers": len(transfers),
        "club_rosters": {cl: list(world.club_roster(cl)) for cl in CLUB_IDS},
        "club_a_goals": club_a_goals,
        "club_b_goals": club_b_goals,
        "club_b_minus_club_a_gd": club_b_goals - club_a_goals,
        "second_half_club_a_goals": sh_club_a_goals,
        "second_half_club_b_goals": sh_club_b_goals,
        "second_half_club_b_minus_club_a_gd": sh_club_b_goals - sh_club_a_goals,
        "club_a_possession_ticks": club_a_poss,
        "club_b_possession_ticks": club_b_poss,
        "club_a_shots": club_a_shots,
        "club_b_shots": club_b_shots,
        "second_half_club_b_shots": sh_club_b_shots,
        "club_a_pass_completions": club_a_passes,
        "club_b_pass_completions": club_b_passes,
        "mean_skill_gain": round6(sum(gains) / len(gains)),
        "p3_transferred": p3_s["transferred"],
        "p3_transfer_requests": p3_s["transfer_requests"],
        "p3_appearances": p3_s["appearances"],
        "p3_club_a_appearances": p3_s["appearances_by_club"]["club_a"],
        "p3_club_b_appearances": p3_s["appearances_by_club"]["club_b"],
        "p3_skill_gain": p3_s["skill_gain"],
        "p3_playing_time_satisfaction": p3_s["playing_time_satisfaction"],
        "p3_proposals_from_c1": p3_s["proposals_from_c1"],
        "p3_proposals_from_c2": p3_s["proposals_from_c2"],
        "players": players_summary,
    }


def snapshot_payload(world: TransferWorldState) -> dict[str, Any]:
    return {
        "format": "soccer-sim-snapshot",
        "world_kind": TRANSFER_WORLD_KIND,
        "identity": transfer_identity(),
        "state": world.to_dict(),
    }


def validate_transfer_world(state_data: dict[str, Any]) -> TransferWorldState:
    if state_data.get("world_kind") != TRANSFER_WORLD_KIND:
        raise ValueError(f"unexpected world_kind: {state_data.get('world_kind')!r}")
    seed = int(state_data["seed"])
    day = int(state_data["day"])
    next_event_id = int(state_data["next_event_id"])
    if day < 0:
        raise ValueError("day must be >= 0")

    cfg = TransferConfig.from_dict(state_data["config"])
    players_raw = state_data["players"]
    minds_raw = state_data["player_minds"]
    coaches_raw = state_data["coaches"]
    if set(players_raw.keys()) != set(PLAYER_IDS):
        raise ValueError("players keys mismatch")
    if set(minds_raw.keys()) != set(PLAYER_IDS):
        raise ValueError("player_minds keys mismatch")
    if set(coaches_raw.keys()) != set(COACH_IDS):
        raise ValueError("coaches keys mismatch")

    players = {pid: TransferPlayerState.from_dict(players_raw[pid]) for pid in PLAYER_IDS}
    player_minds = {pid: TransferPlayerMind.from_dict(minds_raw[pid]) for pid in PLAYER_IDS}
    coaches = {cid: TransferCoachMind.from_dict(coaches_raw[cid]) for cid in COACH_IDS}

    # Check roster size invariants and player-mind club synchronization
    for pid in PLAYER_IDS:
        p = players[pid]
        pm = player_minds[pid]
        if p.club_id not in CLUB_IDS:
            raise ValueError(f"invalid club_id for {pid}: {p.club_id!r}")
        if pm.club_id != p.club_id:
            raise ValueError(f"player_mind club_id mismatch for {pid}")
        if not (0.0 <= p.fatigue <= 1.0):
            raise ValueError(f"fatigue out of bounds for {pid}")
        for t_val in (p.traits.pace, p.traits.passing, p.traits.shooting):
            if not (0.0 <= t_val <= p.cap + 1e-6):
                raise ValueError(f"trait out of bounds for {pid}")
        if not (0.0 <= pm.playing_time_satisfaction <= 1.0):
            raise ValueError(f"satisfaction out of bounds for {pid}")
        if sum(pm.appearances_by_club.values()) != pm.appearances:
            raise ValueError(f"appearances_by_club sum mismatch for {pid}")

    for club_id in CLUB_IDS:
        r_count = sum(1 for pid in PLAYER_IDS if players[pid].club_id == club_id)
        if not (MIN_CLUB_ROSTER <= r_count <= MAX_CLUB_ROSTER):
            raise ValueError(f"club {club_id} roster size {r_count} violates [{MIN_CLUB_ROSTER}, {MAX_CLUB_ROSTER}]")

    events = list(state_data["events"])
    if next_event_id != len(events) + 1:
        raise ValueError("next_event_id does not match events length + 1")

    by_id: dict[int, dict[str, Any]] = {}
    coach_slots_by_day: dict[tuple[int, str], set[int]] = {}
    player_actions_by_day: dict[tuple[int, str], int] = {}

    for idx, ev in enumerate(events, start=1):
        eid = int(ev["event_id"])
        if eid != idx:
            raise ValueError(f"non-contiguous event_id {eid} at index {idx}")
        ev_day = int(ev["day"])
        if not (0 <= ev_day <= day):
            raise ValueError(f"event {eid} day {ev_day} out of range")
        for cid_ref in ev["cause_ids"]:
            c_int = int(cid_ref)
            if c_int < 1 or c_int >= eid or c_int not in by_id:
                raise ValueError(f"invalid cause_id {c_int} on event {eid}")
        by_id[eid] = ev

        kind = ev["kind"]
        data = ev["data"]
        if kind == "coach_training_decision":
            if ev_day % cfg.match_interval_days == 0:
                raise ValueError(f"coach_training_decision on match day {ev_day}")
            actor = str(ev["actor"])
            slot = int(data["slot"])
            key = (ev_day, actor)
            used = coach_slots_by_day.setdefault(key, set())
            if slot in used or slot not in (1, 2):
                raise ValueError(f"invalid or duplicate coach slot {slot} for {actor} on day {ev_day}")
            used.add(slot)
        elif kind == "player_training_decision":
            if ev_day % cfg.match_interval_days == 0:
                raise ValueError(f"player_training_decision on match day {ev_day}")
            actor = str(ev["actor"])
            pkey = (ev_day, actor)
            player_actions_by_day[pkey] = player_actions_by_day.get(pkey, 0) + 1
            if player_actions_by_day[pkey] > 1:
                raise ValueError(f"duplicate player_training_decision for {actor} on day {ev_day}")
        elif kind == "lineup_decision":
            if ev_day == 0 or ev_day % cfg.match_interval_days != 0:
                raise ValueError(f"lineup_decision on non-match day {ev_day}")
            starters = data["starters"]
            bench = data["bench"]
            if len(starters) != 2 or len(set(starters) & set(bench)) != 0:
                raise ValueError(f"invalid lineup structure on event {eid}")
        elif kind == "transfer_completed":
            if ev_day not in TRANSFER_WINDOW_DAYS:
                raise ValueError(f"transfer_completed on non-window day {ev_day}")

    # Verify memory reference pointers on current states/minds
    for pid in PLAYER_IDS:
        p = players[pid]
        pm = player_minds[pid]
        for ref in (
            p.last_state_id,
            p.last_transfer_id,
            pm.skill_memory_id,
            pm.career_memory_id,
            *pm.gain_memory_ids.values(),
            *pm.trust_memory_ids.values(),
        ):
            if int(ref) not in by_id:
                raise ValueError(f"dangling memory pointer {ref} on player {pid}")

    for cid in COACH_IDS:
        coach = coaches[cid]
        for pid, b in coach.beliefs.items():
            for ref in (b.skill_memory_id, b.match_memory_id, *b.residual_memory_ids.values()):
                if int(ref) not in by_id:
                    raise ValueError(f"dangling memory pointer {ref} on coach {cid} belief {pid}")

    return TransferWorldState(
        seed=seed,
        day=day,
        config=cfg,
        players=players,
        player_minds=player_minds,
        coaches=coaches,
        events=events,
        next_event_id=next_event_id,
    )


def save_transfer_run(
    world: TransferWorldState,
    out_dir: str | Path,
    elapsed_seconds: float = 0.0,
) -> dict[str, Any]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    snap = snapshot_payload(world)
    sim_sha = digest(snap)
    summary = {
        **summarize_transfer_world(world),
        "elapsed_seconds": round6(elapsed_seconds),
        "simulation_sha256": sim_sha,
    }
    manifest = {
        "world_kind": TRANSFER_WORLD_KIND,
        "author": TRANSFER_AUTHOR,
        "condition": world.config.condition,
        "seed": world.seed,
        "days": world.day,
        "event_count": len(world.events),
        "simulation_sha256": sim_sha,
        "versions": transfer_identity(),
        "runtime": runtime(),
    }
    atomic_write(out / "snapshot.json", [canonical(snap), b"\n"])
    write_json(out / "summary.json", summary)
    write_json(out / "manifest.json", manifest)
    lines = [canonical(ev) + b"\n" for ev in world.events]
    atomic_write(out / "events.jsonl", lines)
    return manifest


def write_run(
    out_dir: Path | str,
    world: TransferWorldState,
    elapsed_seconds: float = 0.0,
) -> str:
    manifest = save_transfer_run(world, out_dir, elapsed_seconds=elapsed_seconds)
    return str(manifest["simulation_sha256"])


def load_transfer_snapshot(path: str | Path) -> TransferWorldState:
    try:
        raw = Path(path).read_bytes()
        data = json.loads(raw, parse_constant=reject_nonfinite, parse_float=parse_float)
        if data.get("format") != "soccer-sim-snapshot":
            raise ValueError("invalid snapshot format")
        if data.get("world_kind") != TRANSFER_WORLD_KIND:
            raise ValueError(f"unexpected top-level world_kind: {data.get('world_kind')!r}")
        ident = data.get("identity", {})
        expected = transfer_identity()
        for k in ("world_kind", "save", "rules", "policy", "rng", "author", "source_sha256"):
            if ident.get(k) != expected.get(k):
                raise ValueError(f"snapshot identity mismatch for {k}: {ident.get(k)!r} != {expected.get(k)!r}")
        return validate_transfer_world(data["state"])
    except (KeyError, TypeError, AttributeError, IndexError, OverflowError) as exc:
        raise ValueError("malformed transfer_loop snapshot") from exc


def load_snapshot(path: str | Path) -> TransferWorldState:
    return load_transfer_snapshot(path)


def resume_transfer_run(
    snapshot_path: str | Path,
    extra_days: int,
    out_dir: str | Path | None = None,
) -> TransferWorldState:
    if extra_days < 0:
        raise ValueError("extra_days must be >= 0")
    world = load_transfer_snapshot(snapshot_path)
    for _ in range(extra_days):
        step_transfer_day(world)
    if out_dir is not None:
        save_transfer_run(world, out_dir)
    return world
