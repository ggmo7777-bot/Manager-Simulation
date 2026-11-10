"""T011: Minimal 4-club league, finite budget ledger, development solidarity dividends, and multi-club transfer market (M5).

Author: Antigravity (Gemini 4 Argon) — distinct from the creator of T001-T007 (Sol 6.1 High).
T011-R1 corrections: Codex. See docs/DECISIONS.md D026.
T012 recorded belief interventions: Sol 6.1 High. See D027.
T013 observation/admission corrections: Codex. See D028.
T014 integer money: Sol 6.1 High. See D029.
T016 research evaluation cohorts: Sol 6.1 High. See D031.
T018 runtime compatibility/provenance: Sol 6.1 High. See D033.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field, fields, is_dataclass, replace
import json
from math import fsum, isfinite
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
from .league_observation import observe_fatigue
from .money import MONEY_SCALE, MONEY_VERSION, validate_money_units
from .fingerprints import league_runtime_fingerprint, source_provenance
from .storage import (
    atomic_write,
    canonical,
    digest,
    parse_float,
    reject_nonfinite,
    runtime,
    write_json,
)


def _f(val: Any, _name: str = "") -> float:
    if type(val) not in (int, float) or not isfinite(val):
        raise ValueError(f"{_name or 'value'} must be a finite number")
    return float(val)


def _validate_money_fields(record: Any) -> None:
    for spec in fields(record):
        if spec.name.endswith("_units"):
            validate_money_units(getattr(record, spec.name), spec.name)


def _validate_event_money(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key.endswith("_units"):
                validate_money_units(item, key)
            else:
                _validate_event_money(item)
    elif isinstance(value, list):
        for item in value:
            _validate_event_money(item)


# Explicit author attribution (distinct from T001-T007 creator Sol 6.1 High)
LEAGUE_AUTHOR = "Antigravity (Gemini 4 Argon)"
LEAGUE_WORLD_KIND = "league_economy_loop"
LEAGUE_SAVE_VERSION = 17
LEAGUE_INTERVENTION_VERSION = "t012-recorded-belief-intervention-v1"
LEAGUE_RULE_VERSION = "t015-sampled-population-v1"
LEAGUE_POLICY_VERSION = "t013-independent-transfer-score-v1"
LEAGUE_EVALUATION_VERSION = "t016-initial-cohort-v1"

CLUB_IDS = ("club_a", "club_b", "club_c", "club_d")
ACADEMY_CLUB_IDS = ("club_a", "club_c")
BUYER_CLUB_IDS = ("club_b", "club_d")
COACH_IDS = ("c1", "c2", "c3", "c4")
COACH_CLUB_MAP: dict[str, str] = {
    "c1": "club_a",
    "c2": "club_b",
    "c3": "club_c",
    "c4": "club_d",
}
CLUB_COACH_MAP: dict[str, str] = {v: k for k, v in COACH_CLUB_MAP.items()}

PLAYER_IDS = (
    "p1", "p2", "p3",
    "p4", "p5", "p6",
    "p7", "p8", "p9",
    "p10", "p11", "p12",
)
INITIAL_CLUB_ROSTERS: dict[str, tuple[str, ...]] = {
    "club_a": ("p1", "p2", "p3"),
    "club_b": ("p4", "p5", "p6"),
    "club_c": ("p7", "p8", "p9"),
    "club_d": ("p10", "p11", "p12"),
}
TALENT_PLAYER_IDS = ("p3", "p9")
# In fixed mode these two slots are the scripted under-evaluated talents. In sampled
# mode they are only two ordinary player slots; summaries keep the key names for comparison.
POPULATION_MODES = ("fixed", "sampled")
POPULATION_SUFFIX = "_sampled_population"
# T019: "follow" uses population_mode; "fixed"/"sampled" overrides one factor of the initial population.
FACTOR_FIELDS = ("profile_mode", "impression_mode", "satisfaction_mode")
FACTOR_MODES = ("follow", "fixed", "sampled")
FACTORS_SUFFIX = "_factors_"

# T020: a named single perturbation of the sampled initial ranges (fixed table, no runtime numbers).
RANGE_SUFFIX = "_range_"
RANGE_BASE = {
    ("academy", "trait"): (0.42, 0.52), ("buyer", "trait"): (0.34, 0.64),
    ("academy", "rate"): (0.104, 0.126), ("buyer", "rate"): (0.020, 0.046),
    ("academy", "cap"): (0.90, 0.95), ("buyer", "cap"): (0.64, 0.88),
    ("all", "loyalty"): (0.05, 0.30), ("all", "ambition"): (0.35, 0.75), ("all", "autonomy"): (0.32, 0.38),
    ("all", "self_bias"): (-0.03, 0.03), ("all", "noise"): (-0.06, 0.06),
}
RANGE_GROUPS = {"trait": ("trait",), "rate": ("rate",), "cap": ("cap",), "psych": ("loyalty", "ambition", "autonomy"),
                "self_bias": ("self_bias",), "noise": ("noise",)}
# name -> (group, op, amount): width scales the half-width about the centre, add/mul move the whole range.
RANGE_PERTURBATIONS = {
    **{f"{g}_w{tag}": (g, "width", k) for g in RANGE_GROUPS for tag, k in (("05", 0.5), ("15", 1.5))},
    "trait_cm": ("trait", "add", -0.05), "trait_cp": ("trait", "add", 0.05),
    "rate_cm": ("rate", "mul", 0.8), "rate_cp": ("rate", "mul", 1.2),
    "cap_cm": ("cap", "add", -0.03), "cap_cp": ("cap", "add", 0.03),
}
RANGE_NAMES = ("none", "null") + tuple(RANGE_PERTURBATIONS)
# T021: the three T020-refused perturbations under pre-registered rules. R1: cap ceiling 0.98 (the generation rule's own
# maximum). R2: a width perturbation still outside the bound is shrunk symmetrically about its centre to the largest fit.
RANGE_EXTENSIONS = {"cap_w15_x": ("cap", "width", 1.5), "cap_cp_x": ("cap", "add", 0.03), "psych_w15_x": ("psych", "width", 1.5)}
RANGE_ALL_NAMES = RANGE_NAMES + tuple(RANGE_EXTENSIONS)
# Value-type ranges must stay inside [0.05, 0.95]; violations are refused, never clipped.
RANGE_BOUNDED = ("trait", "cap", "loyalty", "ambition", "autonomy")


def sampled_ranges(name: str) -> dict[tuple[str, str], tuple[float, float]]:
    """Ranges used to draw the sampled population; "none"/"null" return the defaults unchanged."""
    if name not in RANGE_ALL_NAMES:
        raise ValueError(f"invalid range_perturbation: {name}")
    ranges = dict(RANGE_BASE)
    extended = name in RANGE_EXTENSIONS
    if name in RANGE_PERTURBATIONS or extended:
        group, op, amount = RANGE_EXTENSIONS[name] if extended else RANGE_PERTURBATIONS[name]
        for (kind, param), (low, high) in RANGE_BASE.items():
            if param not in RANGE_GROUPS[group]:
                continue
            centre, half = (low + high) / 2, (high - low) / 2
            top = 0.98 if extended and param == "cap" else 0.95
            if op == "width":
                if extended and param in RANGE_BOUNDED:
                    k = min(amount, (centre - 0.05) / half, (top - centre) / half)
                else:
                    k = amount
                low, high = centre - half * k, centre + half * k
            elif op == "add":
                low, high = low + amount, high + amount
            else:
                low, high = low * amount, high * amount
            ranges[kind, param] = (round(low, 6), round(high, 6))
    for (kind, param), (low, high) in ranges.items():
        if not low < high or (param in RANGE_BOUNDED and not (0.05 <= low and high <= (0.98 if extended and param == "cap" else 0.95))) or (param == "rate" and low <= 0):
            raise ValueError(f"range_perturbation {name} makes {kind} {param} range invalid: ({low}, {high})")
    return ranges


def _range_valid(name: str) -> bool:
    try:
        sampled_ranges(name)
    except ValueError:
        return False
    return True


# Perturbations whose ranges pass the validity rule; the others are refused (T020 records which).
RANGE_RUNNABLE = tuple(name for name in RANGE_ALL_NAMES[1:] if _range_valid(name))

MIN_CLUB_ROSTER = 2
MAX_CLUB_ROSTER = 4

TRAINING_METHODS = ("A", "B")
PLAYER_TRAINING_ACTIONS = ("A", "B", "rest")
COACH_TRAINING_KINDS = ("observe", "propose", "scout", "rest")

MARKET_MODES = ("closed", "open")
SOLIDARITY_MODES = ("disabled", "enabled")
INFLOW_MODES = ("standard", "zero_inflow")
BUYER_STRATEGIES = ("standard", "monopolist_hoarder")
ADVERSARIAL_MODES = ("none", "churn_and_double_claim_attempt")

TRANSFER_WINDOW_DAYS = (28, 56)

# 4-club round-robin pairings per 3-round cycle
ROUND_ROBIN_PAIRINGS: tuple[tuple[tuple[str, str], tuple[str, str]], ...] = (
    (("club_a", "club_b"), ("club_c", "club_d")),
    (("club_a", "club_c"), ("club_b", "club_d")),
    (("club_a", "club_d"), ("club_b", "club_c")),
)

T011_MAIN_CONDITIONS = (
    "closed_no_solidarity",
    "closed_solidarity",
    "open_no_solidarity",
    "open_solidarity",
)
T011_EXTRA_CONDITIONS = (
    "fixed_budget_no_inflow",
    "hoarder_buyer_policy",
    "exploit_churn_guard",
)
T011_ALL_CONDITIONS = T011_MAIN_CONDITIONS + T011_EXTRA_CONDITIONS

METHOD_TRAIT_WEIGHTS: dict[str, tuple[float, float, float]] = {
    "A": (0.45, 1.45, 1.10),
    "B": (1.45, 0.45, 1.10),
}


def league_identity() -> dict[str, Any]:
    source = league_runtime_fingerprint()
    return {
        "world_kind": LEAGUE_WORLD_KIND,
        "save": LEAGUE_SAVE_VERSION,
        "rules": LEAGUE_RULE_VERSION,
        "policy": LEAGUE_POLICY_VERSION,
        "rng": RNG_VERSION,
        "money": MONEY_VERSION,
        "money_scale": MONEY_SCALE,
        "author": LEAGUE_AUTHOR,
        "revision_author": "Sol 6.1 High (T018 fingerprints/T016 evaluation); Claude Sonnet 5.5 (T015; not the author of T001-T014)",
        "previous_revision_author": "Sol 6.1 High (T014); Codex (T011-R1/T013); Sol 6.1 High (T012)",
        "intervention": LEAGUE_INTERVENTION_VERSION,
        "source_scope": source["scope"],
        "source_sha256": source["sha256"],
    }


@dataclass(frozen=True)
class LeagueEconomyConfig:
    condition: str = "open_solidarity"
    market_mode: str = "open"
    solidarity_mode: str = "enabled"
    inflow_mode: str = "standard"
    buyer_strategy: str = "standard"
    adversarial_mode: str = "none"
    population_mode: str = "fixed"
    profile_mode: str = "follow"
    impression_mode: str = "follow"
    satisfaction_mode: str = "follow"
    range_perturbation: str = "none"
    season_length_days: int = 0  # T023: 0 = no season boundary; a time unit only, it changes no rule or history
    match_interval_days: int = 7
    match_ticks: int = 40
    coach_daily_slots: int = 2
    self_noise: float = 0.018
    coach_noise: float = 0.024
    scout_noise: float = 0.030
    match_obs_noise: float = 0.020
    self_fatigue_noise: float = 0.025
    coach_fatigue_noise: float = 0.045
    scout_fatigue_noise: float = 0.065
    match_fatigue_noise: float = 0.050
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
    # Financial & market parameters
    initial_academy_cash_units: int = 21000000
    initial_buyer_cash_units: int = 59000000
    facility_reserve_floor_units: int = 16500000
    facility_daily_cost_units: int = 280000
    facility_Active_multiplier: float = 1.25
    facility_inactive_multiplier: float = 0.52
    matchday_base_inflow_units: int = 1200000
    matchday_win_bonus_units: int = 400000
    matchday_draw_bonus_units: int = 150000
    player_matchday_wage_units: int = 380000
    scout_action_cost_units: int = 50000
    base_transfer_fee_solidarity_units: int = 9500000
    solidarity_transfer_grant_units: int = 2500000
    base_transfer_fee_no_solidarity_units: int = 1500000
    development_dividend_per_start_units: int = 1250000
    max_dividend_triggers: int = 6
    buyer_min_cash_reserve_units: int = 8000000
    transfer_request_threshold: float = 0.08
    transfer_offer_threshold: float = 0.022

    def __post_init__(self) -> None:
        # Validate before any int/str coercion or simulation/replay work.
        for spec in fields(self):
            value = getattr(self, spec.name)
            if type(spec.default) is str:
                if type(value) is not str or not value:
                    raise ValueError(f"{spec.name} must be a nonempty string")
            elif type(spec.default) is int:
                minimum = 0 if spec.name.endswith("_units") or spec.name in ("max_dividend_triggers", "season_length_days") else 1
                if type(value) is not int or value < minimum:
                    raise ValueError(f"{spec.name} must be an integer >= {minimum}")
            else:
                if type(value) not in (int, float) or not isfinite(value):
                    raise ValueError(f"{spec.name} must be a finite number")
                if spec.name not in ("transfer_request_threshold", "transfer_offer_threshold") and value < 0:
                    raise ValueError(f"{spec.name} must be nonnegative")
                if (spec.name.endswith("_alpha") or spec.name == "coach_match_skill_blend") and value > 1:
                    raise ValueError(f"{spec.name} must be in [0, 1]")
        for name in ("self_fatigue_noise", "coach_fatigue_noise", "scout_fatigue_noise", "match_fatigue_noise"):
            if getattr(self, name) > 0.95:
                raise ValueError(f"{name} must be <= 0.95")
        if self.market_mode not in MARKET_MODES:
            raise ValueError(f"invalid market_mode: {self.market_mode}")
        if self.solidarity_mode not in SOLIDARITY_MODES:
            raise ValueError(f"invalid solidarity_mode: {self.solidarity_mode}")
        if self.inflow_mode not in INFLOW_MODES:
            raise ValueError(f"invalid inflow_mode: {self.inflow_mode}")
        if self.buyer_strategy not in BUYER_STRATEGIES:
            raise ValueError(f"invalid buyer_strategy: {self.buyer_strategy}")
        if self.adversarial_mode not in ADVERSARIAL_MODES:
            raise ValueError(f"invalid adversarial_mode: {self.adversarial_mode}")
        if self.population_mode not in POPULATION_MODES:
            raise ValueError(f"invalid population_mode: {self.population_mode}")
        for name in FACTOR_FIELDS:
            if getattr(self, name) not in FACTOR_MODES:
                raise ValueError(f"invalid {name}: {getattr(self, name)}")
        sampled_ranges(self.range_perturbation)
        if self.match_interval_days <= 0 or self.match_ticks <= 0 or self.coach_daily_slots <= 0:
            raise ValueError("intervals, ticks, and slots must be positive")

    def to_dict(self) -> dict[str, Any]:
        _validate_money_fields(self)
        data = self._base_dict()
        # Factor keys appear only when set, so default saves stay byte-identical to save17 before T019.
        data.update({name: getattr(self, name) for name in FACTOR_FIELDS if getattr(self, name) != "follow"})
        if self.range_perturbation != "none":
            data["range_perturbation"] = self.range_perturbation
        if self.season_length_days:
            data["season_length_days"] = self.season_length_days
        return data

    def _base_dict(self) -> dict[str, Any]:
        return {
            "condition": self.condition,
            "market_mode": self.market_mode,
            "solidarity_mode": self.solidarity_mode,
            "inflow_mode": self.inflow_mode,
            "buyer_strategy": self.buyer_strategy,
            "adversarial_mode": self.adversarial_mode,
            "population_mode": self.population_mode,
            "match_interval_days": self.match_interval_days,
            "match_ticks": self.match_ticks,
            "coach_daily_slots": self.coach_daily_slots,
            "self_noise": self.self_noise,
            "coach_noise": self.coach_noise,
            "scout_noise": self.scout_noise,
            "match_obs_noise": self.match_obs_noise,
            "self_fatigue_noise": self.self_fatigue_noise,
            "coach_fatigue_noise": self.coach_fatigue_noise,
            "scout_fatigue_noise": self.scout_fatigue_noise,
            "match_fatigue_noise": self.match_fatigue_noise,
            "match_noise_scale": self.match_noise_scale,
            "player_skill_alpha": self.player_skill_alpha,
            "player_gain_alpha": self.player_gain_alpha,
            "coach_skill_alpha": self.coach_skill_alpha,
            "coach_scout_alpha": self.coach_scout_alpha,
            "coach_residual_alpha": self.coach_residual_alpha,
            "coach_match_alpha": self.coach_match_alpha,
            "coach_match_skill_blend": self.coach_match_skill_blend,
            "trust_alpha": self.trust_alpha,
            "trust_scale": self.trust_scale,
            "advice_weight": self.advice_weight,
            "training_fatigue_weight": self.training_fatigue_weight,
            "rest_fatigue_weight": self.rest_fatigue_weight,
            "satisfaction_drive_weight": self.satisfaction_drive_weight,
            "coach_need_weight": self.coach_need_weight,
            "coach_method_fit": self.coach_method_fit,
            "coach_staleness_weight": self.coach_staleness_weight,
            "coach_rest_value": self.coach_rest_value,
            "select_skill_weight": self.select_skill_weight,
            "select_match_weight": self.select_match_weight,
            "select_fatigue_weight": self.select_fatigue_weight,
            "initial_academy_cash_units": self.initial_academy_cash_units,
            "initial_buyer_cash_units": self.initial_buyer_cash_units,
            "facility_reserve_floor_units": self.facility_reserve_floor_units,
            "facility_daily_cost_units": self.facility_daily_cost_units,
            "facility_Active_multiplier": self.facility_Active_multiplier,
            "facility_inactive_multiplier": self.facility_inactive_multiplier,
            "matchday_base_inflow_units": self.matchday_base_inflow_units,
            "matchday_win_bonus_units": self.matchday_win_bonus_units,
            "matchday_draw_bonus_units": self.matchday_draw_bonus_units,
            "player_matchday_wage_units": self.player_matchday_wage_units,
            "scout_action_cost_units": self.scout_action_cost_units,
            "base_transfer_fee_solidarity_units": self.base_transfer_fee_solidarity_units,
            "solidarity_transfer_grant_units": self.solidarity_transfer_grant_units,
            "base_transfer_fee_no_solidarity_units": self.base_transfer_fee_no_solidarity_units,
            "development_dividend_per_start_units": self.development_dividend_per_start_units,
            "max_dividend_triggers": self.max_dividend_triggers,
            "buyer_min_cash_reserve_units": self.buyer_min_cash_reserve_units,
            "transfer_request_threshold": self.transfer_request_threshold,
            "transfer_offer_threshold": self.transfer_offer_threshold,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LeagueEconomyConfig:
        if not cls.has_save_fields(data):
            raise ValueError("config fields do not match save17 schema")
        return cls(**data)

    @classmethod
    def has_save_fields(cls, data: Any) -> bool:
        optional = set(FACTOR_FIELDS) | {"range_perturbation", "season_length_days"}
        base = {spec.name for spec in fields(cls)} - optional
        return type(data) is dict and base <= set(data) <= base | optional


def config_for_condition(condition: str) -> LeagueEconomyConfig:
    c = condition.removeprefix("league_")
    if RANGE_SUFFIX in c:
        # T020: a perturbation applies to a sampled population only.
        stem, _, name = c.rpartition(RANGE_SUFFIX)
        if name not in RANGE_ALL_NAMES[1:] or not stem.endswith(POPULATION_SUFFIX):
            raise ValueError(f"Unknown T011 condition: {condition}")
        return replace(config_for_condition(stem), condition=c, range_perturbation=name)
    if FACTORS_SUFFIX in c:
        # Same institution as the base condition; each factor of the initial population is set explicitly.
        stem, _, bits = c.rpartition(FACTORS_SUFFIX)
        if stem not in T011_ALL_CONDITIONS or len(bits) != 3 or set(bits) - {"0", "1"}:
            raise ValueError(f"Unknown T011 condition: {condition}")
        return replace(config_for_condition(stem), condition=c, **{
            name: ("fixed", "sampled")[int(bit)] for name, bit in zip(FACTOR_FIELDS, bits)})
    if c.endswith(POPULATION_SUFFIX):
        # Same institution as the base condition; only the initial population differs.
        base = config_for_condition(c.removesuffix(POPULATION_SUFFIX))
        return replace(base, condition=c, population_mode="sampled")
    if c == "closed_no_solidarity":
        return LeagueEconomyConfig(condition=c, market_mode="closed", solidarity_mode="disabled")
    if c == "closed_solidarity":
        return LeagueEconomyConfig(condition=c, market_mode="closed", solidarity_mode="enabled")
    if c == "open_no_solidarity":
        return LeagueEconomyConfig(condition=c, market_mode="open", solidarity_mode="disabled")
    if c == "open_solidarity":
        return LeagueEconomyConfig(condition=c, market_mode="open", solidarity_mode="enabled")
    if c == "fixed_budget_no_inflow":
        return LeagueEconomyConfig(
            condition=c,
            market_mode="open",
            solidarity_mode="enabled",
            inflow_mode="zero_inflow",
        )
    if c == "hoarder_buyer_policy":
        return LeagueEconomyConfig(
            condition=c,
            market_mode="open",
            solidarity_mode="enabled",
            buyer_strategy="monopolist_hoarder",
            transfer_offer_threshold=-0.06,
        )
    if c == "exploit_churn_guard":
        return LeagueEconomyConfig(
            condition=c,
            market_mode="open",
            solidarity_mode="enabled",
            adversarial_mode="churn_and_double_claim_attempt",
        )
    raise ValueError(f"Unknown T011 condition: {condition}")


@dataclass
class PlayerTruth:
    player_id: str
    origin_club_id: str
    club_id: str
    traits: MatchTraits
    rates: dict[str, float]
    cap: float
    fatigue: float
    appearances: int = 0
    appearances_by_club: dict[str, int] = field(default_factory=dict)
    bench_streak: int = 0
    max_bench_streak: int = 0
    goals_scored: int = 0
    passes_completed: int = 0
    shots_taken: int = 0
    tackles_won: int = 0
    transfers_count: int = 0
    previous_clubs: list[str] = field(default_factory=list)
    active_transfer_id: str | None = None
    cumulative_wages_received_units: int = 0

    def composite_skill(self) -> float:
        return round6((self.traits.pace + self.traits.passing + self.traits.shooting) / 3.0)

    def __post_init__(self) -> None:
        _validate_money_fields(self)

    def to_dict(self) -> dict[str, Any]:
        _validate_money_fields(self)
        return {
            "player_id": self.player_id,
            "origin_club_id": self.origin_club_id,
            "club_id": self.club_id,
            "traits": self.traits.to_dict(),
            "rates": {k: round6(v) for k, v in self.rates.items()},
            "cap": round6(self.cap),
            "fatigue": round6(self.fatigue),
            "appearances": self.appearances,
            "appearances_by_club": dict(self.appearances_by_club),
            "bench_streak": self.bench_streak,
            "max_bench_streak": self.max_bench_streak,
            "goals_scored": self.goals_scored,
            "passes_completed": self.passes_completed,
            "shots_taken": self.shots_taken,
            "tackles_won": self.tackles_won,
            "transfers_count": self.transfers_count,
            "previous_clubs": list(self.previous_clubs),
            "active_transfer_id": self.active_transfer_id,
            "cumulative_wages_received_units": self.cumulative_wages_received_units,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlayerTruth:
        return cls(
            player_id=str(data["player_id"]),
            origin_club_id=str(data["origin_club_id"]),
            club_id=str(data["club_id"]),
            traits=MatchTraits.from_dict(data["traits"]),
            rates={str(k): _f(v) for k, v in data["rates"].items()},
            cap=_f(data["cap"]),
            fatigue=_f(data["fatigue"]),
            appearances=int(data["appearances"]),
            appearances_by_club={str(k): int(v) for k, v in data["appearances_by_club"].items()},
            bench_streak=int(data["bench_streak"]),
            max_bench_streak=int(data["max_bench_streak"]),
            goals_scored=int(data["goals_scored"]),
            passes_completed=int(data["passes_completed"]),
            shots_taken=int(data["shots_taken"]),
            tackles_won=int(data["tackles_won"]),
            transfers_count=int(data["transfers_count"]),
            previous_clubs=[str(x) for x in data["previous_clubs"]],
            active_transfer_id=None if data["active_transfer_id"] is None else str(data["active_transfer_id"]),
            cumulative_wages_received_units=validate_money_units(data["cumulative_wages_received_units"]),
        )


@dataclass
class PlayerMind:
    player_id: str
    self_estimate: float
    expected_gains: dict[str, float]
    trust_by_coach: dict[str, float]
    autonomy: float
    ambition: float
    loyalty_bias: float
    rest_preference: float
    preferred_method: str
    playing_time_satisfaction: float
    perceived_fatigue: float
    style: MatchStyle
    last_obs_ref: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "player_id": self.player_id,
            "self_estimate": round6(self.self_estimate),
            "expected_gains": {k: round6(v) for k, v in self.expected_gains.items()},
            "trust_by_coach": {k: round6(v) for k, v in self.trust_by_coach.items()},
            "autonomy": round6(self.autonomy),
            "ambition": round6(self.ambition),
            "loyalty_bias": round6(self.loyalty_bias),
            "rest_preference": round6(self.rest_preference),
            "preferred_method": self.preferred_method,
            "playing_time_satisfaction": round6(self.playing_time_satisfaction),
            "perceived_fatigue": round6(self.perceived_fatigue),
            "style": self.style.to_dict(),
            "last_obs_ref": self.last_obs_ref,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlayerMind:
        return cls(
            player_id=str(data["player_id"]),
            self_estimate=_f(data["self_estimate"]),
            expected_gains={str(k): _f(v) for k, v in data["expected_gains"].items()},
            trust_by_coach={str(k): _f(v) for k, v in data["trust_by_coach"].items()},
            autonomy=_f(data["autonomy"]),
            ambition=_f(data["ambition"]),
            loyalty_bias=_f(data["loyalty_bias"]),
            rest_preference=_f(data["rest_preference"]),
            preferred_method=str(data["preferred_method"]),
            playing_time_satisfaction=_f(data["playing_time_satisfaction"]),
            perceived_fatigue=_f(data["perceived_fatigue"]),
            style=MatchStyle.from_dict(data["style"]),
            last_obs_ref=str(data["last_obs_ref"]),
        )


@dataclass
class CoachPlayerEvaluation:
    skill_estimate: float
    match_rating_estimate: float
    fatigue_estimate: float
    last_seen_day: int
    method_residuals: dict[str, float]
    last_obs_ref: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_estimate": round6(self.skill_estimate),
            "match_rating_estimate": round6(self.match_rating_estimate),
            "fatigue_estimate": round6(self.fatigue_estimate),
            "last_seen_day": self.last_seen_day,
            "method_residuals": {k: round6(v) for k, v in self.method_residuals.items()},
            "last_obs_ref": self.last_obs_ref,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CoachPlayerEvaluation:
        return cls(
            skill_estimate=_f(data["skill_estimate"]),
            match_rating_estimate=_f(data["match_rating_estimate"]),
            fatigue_estimate=_f(data["fatigue_estimate"]),
            last_seen_day=int(data["last_seen_day"]),
            method_residuals={str(k): _f(v) for k, v in data["method_residuals"].items()},
            last_obs_ref=str(data["last_obs_ref"]),
        )


@dataclass
class CoachMind:
    coach_id: str
    club_id: str
    philosophy: str  # "academy" or "buyer"
    preferred_method: str
    base_method_gains: dict[str, float]
    evaluations: dict[str, CoachPlayerEvaluation]

    def to_dict(self) -> dict[str, Any]:
        return {
            "coach_id": self.coach_id,
            "club_id": self.club_id,
            "philosophy": self.philosophy,
            "preferred_method": self.preferred_method,
            "base_method_gains": {k: round6(v) for k, v in self.base_method_gains.items()},
            "evaluations": {k: v.to_dict() for k, v in self.evaluations.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CoachMind:
        return cls(
            coach_id=str(data["coach_id"]),
            club_id=str(data["club_id"]),
            philosophy=str(data["philosophy"]),
            preferred_method=str(data["preferred_method"]),
            base_method_gains={str(k): _f(v) for k, v in data["base_method_gains"].items()},
            evaluations={str(k): CoachPlayerEvaluation.from_dict(v) for k, v in data["evaluations"].items()},
        )


@dataclass
class ClubAccountState:
    club_id: str
    philosophy: str
    cash_units: int
    initial_cash_units: int
    facility_active_days: int = 0
    cumulative_inflow_units: int = 0
    cumulative_wages_paid_units: int = 0
    cumulative_facility_paid_units: int = 0
    cumulative_scouting_paid_units: int = 0
    cumulative_transfer_fees_paid_units: int = 0
    cumulative_transfer_fees_received_units: int = 0
    cumulative_solidarity_paid_units: int = 0
    cumulative_solidarity_received_units: int = 0
    points: int = 0
    matches_played: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0
    goals_for: int = 0
    goals_against: int = 0
    shots_for: int = 0
    possession_ticks: int = 0
    pass_completions: int = 0

    def __post_init__(self) -> None:
        _validate_money_fields(self)

    def to_dict(self) -> dict[str, Any]:
        _validate_money_fields(self)
        return {
            "club_id": self.club_id,
            "philosophy": self.philosophy,
            "cash_units": self.cash_units,
            "initial_cash_units": self.initial_cash_units,
            "facility_active_days": self.facility_active_days,
            "cumulative_inflow_units": self.cumulative_inflow_units,
            "cumulative_wages_paid_units": self.cumulative_wages_paid_units,
            "cumulative_facility_paid_units": self.cumulative_facility_paid_units,
            "cumulative_scouting_paid_units": self.cumulative_scouting_paid_units,
            "cumulative_transfer_fees_paid_units": self.cumulative_transfer_fees_paid_units,
            "cumulative_transfer_fees_received_units": self.cumulative_transfer_fees_received_units,
            "cumulative_solidarity_paid_units": self.cumulative_solidarity_paid_units,
            "cumulative_solidarity_received_units": self.cumulative_solidarity_received_units,
            "points": self.points,
            "matches_played": self.matches_played,
            "wins": self.wins,
            "draws": self.draws,
            "losses": self.losses,
            "goals_for": self.goals_for,
            "goals_against": self.goals_against,
            "shots_for": self.shots_for,
            "possession_ticks": self.possession_ticks,
            "pass_completions": self.pass_completions,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ClubAccountState:
        return cls(
            club_id=str(data["club_id"]),
            philosophy=str(data["philosophy"]),
            cash_units=validate_money_units(data["cash_units"]),
            initial_cash_units=validate_money_units(data["initial_cash_units"]),
            facility_active_days=int(data["facility_active_days"]),
            cumulative_inflow_units=validate_money_units(data["cumulative_inflow_units"]),
            cumulative_wages_paid_units=validate_money_units(data["cumulative_wages_paid_units"]),
            cumulative_facility_paid_units=validate_money_units(data["cumulative_facility_paid_units"]),
            cumulative_scouting_paid_units=validate_money_units(data["cumulative_scouting_paid_units"]),
            cumulative_transfer_fees_paid_units=validate_money_units(data["cumulative_transfer_fees_paid_units"]),
            cumulative_transfer_fees_received_units=validate_money_units(data["cumulative_transfer_fees_received_units"]),
            cumulative_solidarity_paid_units=validate_money_units(data["cumulative_solidarity_paid_units"]),
            cumulative_solidarity_received_units=validate_money_units(data["cumulative_solidarity_received_units"]),
            points=int(data["points"]),
            matches_played=int(data["matches_played"]),
            wins=int(data["wins"]),
            draws=int(data["draws"]),
            losses=int(data["losses"]),
            goals_for=int(data["goals_for"]),
            goals_against=int(data["goals_against"]),
            shots_for=int(data["shots_for"]),
            possession_ticks=int(data["possession_ticks"]),
            pass_completions=int(data["pass_completions"]),
        )


@dataclass
class TransferContractRecord:
    transfer_id: str
    day: int
    player_id: str
    origin_club_id: str
    from_club: str
    to_club: str
    base_fee_units: int
    solidarity_grant_units: int
    dividend_per_start_units: int
    max_dividend_triggers: int
    dividend_triggers_used: int = 0

    def __post_init__(self) -> None:
        _validate_money_fields(self)

    def to_dict(self) -> dict[str, Any]:
        _validate_money_fields(self)
        return {
            "transfer_id": self.transfer_id,
            "day": self.day,
            "player_id": self.player_id,
            "origin_club_id": self.origin_club_id,
            "from_club": self.from_club,
            "to_club": self.to_club,
            "base_fee_units": self.base_fee_units,
            "solidarity_grant_units": self.solidarity_grant_units,
            "dividend_per_start_units": self.dividend_per_start_units,
            "max_dividend_triggers": self.max_dividend_triggers,
            "dividend_triggers_used": self.dividend_triggers_used,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TransferContractRecord:
        return cls(
            transfer_id=str(data["transfer_id"]),
            day=int(data["day"]),
            player_id=str(data["player_id"]),
            origin_club_id=str(data["origin_club_id"]),
            from_club=str(data["from_club"]),
            to_club=str(data["to_club"]),
            base_fee_units=validate_money_units(data["base_fee_units"]),
            solidarity_grant_units=validate_money_units(data["solidarity_grant_units"]),
            dividend_per_start_units=validate_money_units(data["dividend_per_start_units"]),
            max_dividend_triggers=int(data["max_dividend_triggers"]),
            dividend_triggers_used=int(data["dividend_triggers_used"]),
        )


@dataclass
class WorldFinanceLedger:
    initial_total_cash_units: int
    cumulative_external_inflow_units: int = 0
    cumulative_external_outflow_units: int = 0
    insolvency_events: int = 0
    blocked_circular_transfers: int = 0
    blocked_duplicate_claims: int = 0
    claimed_dividend_keys: set[str] = field(default_factory=set)
    transfer_contracts: dict[str, TransferContractRecord] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_money_fields(self)

    def to_dict(self) -> dict[str, Any]:
        _validate_money_fields(self)
        return {
            "initial_total_cash_units": self.initial_total_cash_units,
            "cumulative_external_inflow_units": self.cumulative_external_inflow_units,
            "cumulative_external_outflow_units": self.cumulative_external_outflow_units,
            "insolvency_events": self.insolvency_events,
            "blocked_circular_transfers": self.blocked_circular_transfers,
            "blocked_duplicate_claims": self.blocked_duplicate_claims,
            "claimed_dividend_keys": sorted(self.claimed_dividend_keys),
            "transfer_contracts": {k: v.to_dict() for k, v in self.transfer_contracts.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorldFinanceLedger:
        return cls(
            initial_total_cash_units=validate_money_units(data["initial_total_cash_units"]),
            cumulative_external_inflow_units=validate_money_units(data["cumulative_external_inflow_units"]),
            cumulative_external_outflow_units=validate_money_units(data["cumulative_external_outflow_units"]),
            insolvency_events=int(data["insolvency_events"]),
            blocked_circular_transfers=int(data["blocked_circular_transfers"]),
            blocked_duplicate_claims=int(data["blocked_duplicate_claims"]),
            claimed_dividend_keys=set(data["claimed_dividend_keys"]),
            transfer_contracts={
                str(k): TransferContractRecord.from_dict(v)
                for k, v in data["transfer_contracts"].items()
            },
        )


@dataclass(frozen=True)
class ResearchIntervention:
    """Researcher's explicit boundary operation, never a natural observation."""
    sequence: int
    day: int
    phase: str
    kind: str
    player_id: str
    coach_id: str
    observer_id: str
    before_value: float
    after_value: float
    reason: str
    parent_checkpoint_sha256: str
    previous_obs_ref: str


@dataclass
class LeagueWorldState:
    seed: int
    day: int
    config: LeagueEconomyConfig
    club_rosters: dict[str, list[str]]
    club_accounts: dict[str, ClubAccountState]
    players: dict[str, PlayerTruth]
    initial_players: dict[str, PlayerTruth]
    player_minds: dict[str, PlayerMind]
    coaches: dict[str, CoachMind]
    ledger: WorldFinanceLedger
    public_last_starters: dict[str, list[str]]
    events: list[dict[str, Any]] = field(default_factory=list)
    interventions: tuple[ResearchIntervention, ...] = ()


def _population_draw(seed: int, channel: str, token: str, low: float, high: float) -> float:
    """Keyed uniform draw for building the initial population (world construction only)."""
    return uniform_from_key(seed, 0, f"population:{channel}", token, low=low, high=high)


def _sample_population(seed: int, perturbation: str = "none") -> tuple[dict[str, dict[str, Any]], dict[tuple[str, str], tuple[float, float]]]:
    """Draw player profiles and each coach's first impression from archetype ranges.

    Academy clubs receive young, fast-growing, similar prospects. Buyer clubs receive a
    wider, higher-level, slower-growing group. Nothing here names a talent or an
    under-evaluated player. Coach impressions are independent noisy readings of each
    player's visible level; they are not copied from truth and not scripted by player id.
    These ranges are hypotheses, not calibrated to real football.
    """
    specs = _sample_profiles(seed, perturbation)
    return specs, _sample_impressions(seed, specs, perturbation)


def _sample_profiles(seed: int, perturbation: str = "none") -> dict[str, dict[str, Any]]:
    ranges = sampled_ranges(perturbation)
    specs: dict[str, dict[str, Any]] = {}
    for cid, pids in INITIAL_CLUB_ROSTERS.items():
        kind = "academy" if cid in ACADEMY_CLUB_IDS else "buyer"
        r = {p: ranges[kind, p] for p in ("trait", "rate", "cap")}
        for pid in pids:
            def draw(name: str, low: float, high: float) -> float:
                return round6(_population_draw(seed, name, pid, low, high))
            traits = tuple(draw(f"trait{i}", *r["trait"]) for i in range(3))
            level = sum(traits) / 3.0
            cap = max(draw("cap", *r["cap"]), min(0.98, max(traits) + 0.05))
            specs[pid] = {
                "club": cid,
                "traits": traits,
                "rates": {"A": draw("rateA", *r["rate"]), "B": draw("rateB", *r["rate"])},
                "cap": round6(cap),
                "pref": "A" if draw("pref", 0.0, 1.0) < 0.5 else "B",
                "loyalty": draw("loyalty", *ranges["all", "loyalty"]),
                "ambition": draw("ambition", *ranges["all", "ambition"]),
                "autonomy": draw("autonomy", *ranges["all", "autonomy"]),
                "self_est": round6(min(0.95, max(0.05, level + draw("self_bias", *ranges["all", "self_bias"])))),
            }
    return specs


def _sample_impressions(seed: int, specs: dict[str, dict[str, Any]], perturbation: str = "none") -> dict[tuple[str, str], tuple[float, float]]:
    """Each coach's noisy first reading of the level of the given profiles (T019: any profile set)."""
    impressions: dict[tuple[str, str], tuple[float, float]] = {}
    low, high = sampled_ranges(perturbation)["all", "noise"]
    for coach_id in COACH_IDS:
        for pid, spec in specs.items():
            level = sum(spec["traits"]) / 3.0
            token = f"{coach_id}:{pid}"
            skill = level + _population_draw(seed, "coach_skill", token, low, high)
            match = level + _population_draw(seed, "coach_match", token, low, high)
            impressions[coach_id, pid] = (round6(min(0.95, max(0.05, skill))),
                                          round6(min(0.95, max(0.05, match))))
    return impressions


def _build_initial_world(seed: int, config: LeagueEconomyConfig) -> LeagueWorldState:
    club_rosters: dict[str, list[str]] = {
        cid: list(pids) for cid, pids in INITIAL_CLUB_ROSTERS.items()
    }
    club_accounts: dict[str, ClubAccountState] = {}
    for cid in CLUB_IDS:
        phil = "academy" if cid in ACADEMY_CLUB_IDS else "buyer"
        init_cash_units = config.initial_academy_cash_units if phil == "academy" else config.initial_buyer_cash_units
        club_accounts[cid] = ClubAccountState(
            club_id=cid,
            philosophy=phil,
            cash_units=(init_cash_units),
            initial_cash_units=(init_cash_units),
        )

    # 12 players across 4 clubs
    player_specs: dict[str, dict[str, Any]] = {
        # club_a (Academy)
        "p1": {
            "club": "club_a",
            "traits": (0.46, 0.48, 0.46),
            "rates": {"A": 0.114, "B": 0.108},
            "cap": 0.92,
            "pref": "A",
            "loyalty": 0.25,
            "ambition": 0.52,
            "autonomy": 0.34,
            "self_est": 0.47,
        },
        "p2": {
            "club": "club_a",
            "traits": (0.45, 0.47, 0.46),
            "rates": {"A": 0.118, "B": 0.112},
            "cap": 0.92,
            "pref": "A",
            "loyalty": 0.28,
            "ambition": 0.55,
            "autonomy": 0.34,
            "self_est": 0.46,
        },
        "p3": {
            "club": "club_a",
            "traits": (0.50, 0.51, 0.51),
            "rates": {"A": 0.122, "B": 0.120},
            "cap": 0.94,
            "pref": "B",
            "loyalty": 0.05,
            "ambition": 0.72,
            "autonomy": 0.36,
            "self_est": 0.53,
        },
        # club_b (Buyer)
        "p4": {
            "club": "club_b",
            "traits": (0.62, 0.61, 0.63),
            "rates": {"A": 0.042, "B": 0.045},
            "cap": 0.86,
            "pref": "B",
            "loyalty": 0.25,
            "ambition": 0.50,
            "autonomy": 0.35,
            "self_est": 0.62,
        },
        "p5": {
            "club": "club_b",
            "traits": (0.39, 0.40, 0.39),
            "rates": {"A": 0.024, "B": 0.025},
            "cap": 0.68,
            "pref": "A",
            "loyalty": 0.22,
            "ambition": 0.40,
            "autonomy": 0.35,
            "self_est": 0.40,
        },
        "p6": {
            "club": "club_b",
            "traits": (0.36, 0.37, 0.36),
            "rates": {"A": 0.022, "B": 0.024},
            "cap": 0.65,
            "pref": "B",
            "loyalty": 0.22,
            "ambition": 0.38,
            "autonomy": 0.35,
            "self_est": 0.37,
        },
        # club_c (Academy)
        "p7": {
            "club": "club_c",
            "traits": (0.45, 0.47, 0.46),
            "rates": {"A": 0.108, "B": 0.114},
            "cap": 0.92,
            "pref": "B",
            "loyalty": 0.25,
            "ambition": 0.52,
            "autonomy": 0.34,
            "self_est": 0.46,
        },
        "p8": {
            "club": "club_c",
            "traits": (0.44, 0.46, 0.45),
            "rates": {"A": 0.112, "B": 0.118},
            "cap": 0.92,
            "pref": "B",
            "loyalty": 0.28,
            "ambition": 0.55,
            "autonomy": 0.34,
            "self_est": 0.45,
        },
        "p9": {
            "club": "club_c",
            "traits": (0.49, 0.50, 0.51),
            "rates": {"A": 0.118, "B": 0.122},
            "cap": 0.94,
            "pref": "A",
            "loyalty": 0.05,
            "ambition": 0.72,
            "autonomy": 0.36,
            "self_est": 0.52,
        },
        # club_d (Buyer)
        "p10": {
            "club": "club_d",
            "traits": (0.61, 0.62, 0.61),
            "rates": {"A": 0.044, "B": 0.042},
            "cap": 0.86,
            "pref": "A",
            "loyalty": 0.25,
            "ambition": 0.50,
            "autonomy": 0.35,
            "self_est": 0.61,
        },
        "p11": {
            "club": "club_d",
            "traits": (0.40, 0.39, 0.40),
            "rates": {"A": 0.025, "B": 0.024},
            "cap": 0.68,
            "pref": "B",
            "loyalty": 0.22,
            "ambition": 0.40,
            "autonomy": 0.35,
            "self_est": 0.40,
        },
        "p12": {
            "club": "club_d",
            "traits": (0.37, 0.36, 0.37),
            "rates": {"A": 0.024, "B": 0.022},
            "cap": 0.65,
            "pref": "A",
            "loyalty": 0.22,
            "ambition": 0.38,
            "autonomy": 0.35,
            "self_est": 0.37,
        },
    }

    # T019: each factor follows population_mode unless its own field says otherwise.
    f1, f2, f3 = (config.population_mode if getattr(config, name) == "follow" else getattr(config, name) for name in FACTOR_FIELDS)
    if f1 == "sampled":
        player_specs = _sample_profiles(seed, config.range_perturbation)
    sampled_impressions = _sample_impressions(seed, player_specs, config.range_perturbation) if f2 == "sampled" else None

    players: dict[str, PlayerTruth] = {}
    initial_players: dict[str, PlayerTruth] = {}
    player_minds: dict[str, PlayerMind] = {}

    for pid in PLAYER_IDS:
        spec = player_specs[pid]
        cid = str(spec["club"])
        t0, t1, t2 = spec["traits"]
        truth = PlayerTruth(
            player_id=pid,
            origin_club_id=cid,
            club_id=cid,
            traits=MatchTraits(pace=t0, passing=t1, shooting=t2),
            rates=dict(spec["rates"]),
            cap=float(spec["cap"]),
            fatigue=0.10,
            appearances_by_club={cl: 0 for cl in CLUB_IDS},
        )
        players[pid] = truth
        initial_players[pid] = PlayerTruth.from_dict(truth.to_dict())

        player_minds[pid] = PlayerMind(
            player_id=pid,
            self_estimate=float(spec["self_est"]),
            expected_gains={"A": 0.042, "B": 0.042},
            trust_by_coach={coach_id: 0.56 for coach_id in COACH_IDS},
            autonomy=float(spec["autonomy"]),
            ambition=float(spec["ambition"]),
            loyalty_bias=float(spec["loyalty"]),
            rest_preference=0.012,
            preferred_method=str(spec["pref"]),
            playing_time_satisfaction=0.50 if (f3 == "sampled" or pid not in TALENT_PLAYER_IDS) else 0.42,
            perceived_fatigue=0.10,
            style=MatchStyle(
                directness=0.55 if spec["pref"] == "B" else 0.48,
                pass_bias=0.55 if spec["pref"] == "A" else 0.46,
                press_bias=0.52,
            ),
            last_obs_ref="init:0",
        )

    coaches: dict[str, CoachMind] = {}
    for coach_id in COACH_IDS:
        cid = COACH_CLUB_MAP[coach_id]
        phil = "academy" if cid in ACADEMY_CLUB_IDS else "buyer"
        pref_m = "A" if coach_id in ("c1", "c4") else "B"
        evals: dict[str, CoachPlayerEvaluation] = {}
        for pid in PLAYER_IDS:
            base_s = player_specs[pid]["self_est"]
            base_m = base_s
            # Fixed fixture only: under-evaluation of p3 by c1 and p9 by c3, while buyer coaches scout them higher
            if sampled_impressions is not None:
                base_s, base_m = sampled_impressions[coach_id, pid]
            elif pid == "p3":
                if coach_id == "c1":
                    base_s, base_m = 0.39, 0.37
                elif coach_id == "c2":
                    base_s, base_m = 0.50, 0.52
                elif coach_id == "c4":
                    base_s, base_m = 0.48, 0.49
            elif pid == "p9":
                if coach_id == "c3":
                    base_s, base_m = 0.38, 0.36
                elif coach_id == "c4":
                    base_s, base_m = 0.50, 0.52
                elif coach_id == "c2":
                    base_s, base_m = 0.47, 0.48
            evals[pid] = CoachPlayerEvaluation(
                skill_estimate=round6(base_s),
                match_rating_estimate=round6(base_m),
                fatigue_estimate=0.10,
                last_seen_day=0,
                method_residuals={"A": 0.0, "B": 0.0},
                last_obs_ref="init:0",
            )
        coaches[coach_id] = CoachMind(
            coach_id=coach_id,
            club_id=cid,
            philosophy=phil,
            preferred_method=pref_m,
            base_method_gains={"A": 0.044, "B": 0.044},
            evaluations=evals,
        )

    total_init_cash_units = (sum(acc.initial_cash_units for acc in club_accounts.values()))
    ledger = WorldFinanceLedger(initial_total_cash_units=total_init_cash_units)
    public_last_starters = {
        "club_a": ["p1", "p2"],
        "club_b": ["p4", "p5"],
        "club_c": ["p7", "p8"],
        "club_d": ["p10", "p11"],
    }

    world = LeagueWorldState(
        seed=seed,
        day=0,
        config=config,
        club_rosters=club_rosters,
        club_accounts=club_accounts,
        players=players,
        initial_players=initial_players,
        player_minds=player_minds,
        coaches=coaches,
        ledger=ledger,
        public_last_starters=public_last_starters,
        events=[],
    )
    _append_event(
        world,
        day=0,
        kind="world_initialized",
        actor="world",
        data={
            "seed": seed,
            "condition": config.condition,
            "initial_total_cash_units": total_init_cash_units,
            "club_rosters": {k: list(v) for k, v in club_rosters.items()},
        },
        cause_refs=[],
    )
    return world


def _append_event(
    world: LeagueWorldState,
    *,
    day: int,
    kind: str,
    actor: str,
    data: dict[str, Any],
    cause_refs: list[str],
) -> str:
    ev_id = f"e{len(world.events) + 1}"
    world.events.append(
        {
            "id": ev_id,
            "day": day,
            "kind": kind,
            "actor": actor,
            "data": data,
            "cause_refs": list(cause_refs),
        }
    )
    return ev_id


def _record_ledger_tx(
    world: LeagueWorldState,
    *,
    day: int,
    payer: str,
    payee: str,
    amount_units: int,
    reason: str,
    cause_refs: list[str],
) -> str:
    # Validate both ends before mutating either account.
    validate_money_units(amount_units, "transaction amount_units")
    if payer not in world.club_accounts and payer != "external_league_pool":
        raise ValueError(f"unknown payer: {payer}")
    if (payee not in world.club_accounts and payee not in world.players
            and payee != "external_treasury_outflow"):
        raise ValueError(f"unknown payee: {payee}")
    amt_units = amount_units
    _validate_money_fields(world.ledger)
    for account_id in (payer, payee):
        if account_id in world.club_accounts:
            _validate_money_fields(world.club_accounts[account_id])
        elif account_id in world.players:
            _validate_money_fields(world.players[account_id])
    if payer in world.club_accounts:
        acc = world.club_accounts[payer]
        if acc.cash_units < amt_units:
            world.ledger.insolvency_events += 1
            raise ValueError(f"insolvency: club {payer} cash_units {acc.cash_units} < {amt_units}")
        acc.cash_units = acc.cash_units - amt_units
        if reason == "player_wage":
            acc.cumulative_wages_paid_units = acc.cumulative_wages_paid_units + amt_units
        elif reason == "training_facility_operation":
            acc.cumulative_facility_paid_units = acc.cumulative_facility_paid_units + amt_units
        elif reason == "scout_expense":
            acc.cumulative_scouting_paid_units = acc.cumulative_scouting_paid_units + amt_units
        elif reason == "transfer_base_fee":
            acc.cumulative_transfer_fees_paid_units = acc.cumulative_transfer_fees_paid_units + amt_units
        elif reason in ("transfer_solidarity_grant", "development_appearance_dividend"):
            acc.cumulative_solidarity_paid_units = acc.cumulative_solidarity_paid_units + amt_units
    elif payer == "external_league_pool":
        world.ledger.cumulative_external_inflow_units = world.ledger.cumulative_external_inflow_units + amt_units
    else:
        raise ValueError(f"unknown payer: {payer}")

    if payee in world.club_accounts:
        acc_to = world.club_accounts[payee]
        acc_to.cash_units = acc_to.cash_units + amt_units
        if reason == "matchday_revenue":
            acc_to.cumulative_inflow_units = acc_to.cumulative_inflow_units + amt_units
        elif reason == "transfer_base_fee":
            acc_to.cumulative_transfer_fees_received_units = acc_to.cumulative_transfer_fees_received_units + amt_units
        elif reason in ("transfer_solidarity_grant", "development_appearance_dividend"):
            acc_to.cumulative_solidarity_received_units = acc_to.cumulative_solidarity_received_units + amt_units
    elif payee in world.players:
        p = world.players[payee]
        p.cumulative_wages_received_units = p.cumulative_wages_received_units + amt_units
    elif payee == "external_treasury_outflow":
        world.ledger.cumulative_external_outflow_units = world.ledger.cumulative_external_outflow_units + amt_units
    else:
        raise ValueError(f"unknown payee: {payee}")

    return _append_event(
        world,
        day=day,
        kind="ledger_transaction",
        actor=payer,
        data={
            "payer": payer,
            "payee": payee,
            "amount_units": amt_units,
            "reason": reason,
        },
        cause_refs=cause_refs,
    )


def process_dividend_claim(world: LeagueWorldState, *, transfer_id: str, match_id: str,
                           claimant_club: str, match_event_ref: str) -> dict[str, Any]:
    """World-side admission, used identically for normal claims and re-submission.

    Rejection does not spend cash_units, consume a trigger, or mark a key as paid.
    Match evidence must describe this day's eligible appearance for the active
    contract; merely guessing an unused key does not establish entitlement.
    """
    if any(type(v) is not str or not v for v in
           (transfer_id, match_id, claimant_club, match_event_ref)):
        raise ValueError("claim identifiers must be nonempty strings")
    match = None
    if match_event_ref.startswith("e") and match_event_ref[1:].isdigit():
        index = int(match_event_ref[1:]) - 1
        if 0 <= index < len(world.events):
            match = world.events[index]
    request_ref = _append_event(
        world, day=world.day, kind="dividend_claim_submitted", actor=claimant_club,
        data={"transfer_id": transfer_id, "match_id": match_id,
              "claimant_club": claimant_club, "match_event_ref": match_event_ref},
        cause_refs=[match["id"]] if match else [])
    contract = world.ledger.transfer_contracts.get(transfer_id)
    key = f"{transfer_id}:{match_id}"
    reason = "accepted"
    if contract is None:
        reason = "unknown_contract"
    elif claimant_club != contract.origin_club_id:
        reason = "wrong_claimant"
    elif (world.config.solidarity_mode != "enabled" or contract.dividend_per_start_units <= 0
          or contract.to_club == contract.origin_club_id):
        reason = "not_entitled"
    elif (world.players[contract.player_id].active_transfer_id != transfer_id
          or world.players[contract.player_id].club_id != contract.to_club):
        reason = "inactive_contract"
    elif (match is None or match["kind"] != "league_match_completed"
          or match["day"] != world.day or match["day"] <= contract.day
          or match["data"]["match_id"] != match_id):
        reason = "invalid_match"
    else:
        data = match["data"]
        eligible = any(data[f"{side}_club"] == contract.to_club
                       and contract.player_id in data[f"{side}_starters"]
                       for side in ("home", "away"))
        if not eligible:
            reason = "not_a_starter"
        elif key in world.ledger.claimed_dividend_keys:
            reason = "duplicate"
        elif contract.dividend_triggers_used >= contract.max_dividend_triggers:
            reason = "trigger_limit"
        elif world.club_accounts[contract.to_club].cash_units < contract.dividend_per_start_units:
            reason = "insufficient_cash"

    tx_ref = None
    if reason == "accepted":
        tx_ref = _record_ledger_tx(
            world, day=world.day, payer=contract.to_club, payee=contract.origin_club_id,
            amount_units=contract.dividend_per_start_units, reason="development_appearance_dividend",
            cause_refs=[request_ref])
        world.ledger.claimed_dividend_keys.add(key)
        contract.dividend_triggers_used += 1
    elif reason == "duplicate":
        world.ledger.blocked_duplicate_claims += 1
    result = {"claim_key": key, "accepted": reason == "accepted", "reason": reason,
              "request_ref": request_ref, "transaction_ref": tx_ref}
    _append_event(world, day=world.day, kind="dividend_claim_resolved", actor="world",
                  data=result.copy(), cause_refs=[request_ref] + ([tx_ref] if tx_ref else []))
    return result


def actual_duplicate_payments(world: LeagueWorldState) -> int:
    """Derived from transactions, not a counter with an unreachable increment."""
    requests = {e["id"]: e["data"] for e in world.events if e["kind"] == "dividend_claim_submitted"}
    keys = []
    for event in world.events:
        if event["kind"] == "ledger_transaction" and event["data"]["reason"] == "development_appearance_dividend":
            request = requests[event["cause_refs"][0]]
            keys.append((request["transfer_id"], request["match_id"]))
    return len(keys) - len(set(keys))


def compute_ledger_discrepancy(world: LeagueWorldState) -> int:
    for record in (world.ledger, *world.club_accounts.values(), *world.players.values()):
        _validate_money_fields(record)
    lhs = world.ledger.initial_total_cash_units + world.ledger.cumulative_external_inflow_units
    club_sum = sum(acc.cash_units for acc in world.club_accounts.values())
    wage_sum = sum(p.cumulative_wages_received_units for p in world.players.values())
    rhs = club_sum + wage_sum + world.ledger.cumulative_external_outflow_units
    return abs(lhs - rhs)


# ============================================================================
# Policy Functions (Pure functions of private beliefs & observations; NO truth)
# ============================================================================

def decide_coach_training(
    *,
    coach: CoachMind,
    own_roster: list[str],
    other_players: list[str],
    club_cash_units: int,
    day: int,
    slot: int,
    already_targeted: set[str],
    config: LeagueEconomyConfig,
) -> dict[str, Any]:
    validate_money_units(club_cash_units, "club_cash_units")
    candidates: list[dict[str, Any]] = []
    obs_refs: list[str] = []

    for pid in own_roster:
        ev = coach.evaluations[pid]
        obs_refs.append(ev.last_obs_ref)
        staleness = max(0, day - ev.last_seen_day)
        need = max(0.0, 0.78 - ev.skill_estimate)
        repeat_pen = 0.25 if pid in already_targeted else 0.0

        # Observe candidate
        obs_score = round6(
            0.12
            + config.coach_staleness_weight * min(10, staleness)
            + 0.22 * need
            - repeat_pen
        )
        candidates.append(
            {
                "kind": "observe",
                "target": pid,
                "method": None,
                "score": obs_score,
                "components": {
                    "staleness": round6(config.coach_staleness_weight * min(10, staleness)),
                    "need": round6(0.22 * need),
                    "repeat_penalty": round6(-repeat_pen),
                },
            }
        )

        # Propose A / B
        for m in TRAINING_METHODS:
            base_g = coach.base_method_gains[m]
            res = ev.method_residuals[m]
            forecast = round6(clamp(base_g + res, 0.005, 0.14))
            pref_bonus = 0.015 if m == coach.preferred_method else 0.0
            fatigue_pen = 0.18 * ev.fatigue_estimate
            prop_score = round6(
                0.15
                + config.coach_need_weight * need
                + config.coach_method_fit * (forecast * 10.0)
                + pref_bonus
                - fatigue_pen
                - repeat_pen
            )
            candidates.append(
                {
                    "kind": "propose",
                    "target": pid,
                    "method": m,
                    "forecast": forecast,
                    "score": prop_score,
                    "components": {
                        "need_term": round6(config.coach_need_weight * need),
                        "forecast_term": round6(config.coach_method_fit * (forecast * 10.0)),
                        "pref_bonus": round6(pref_bonus),
                        "fatigue_penalty": round6(-fatigue_pen),
                        "repeat_penalty": round6(-repeat_pen),
                    },
                }
            )

    # Scout candidate on other clubs if club can afford scout_action_cost_units
    if club_cash_units >= config.scout_action_cost_units + config.buyer_min_cash_reserve_units:
        for pid in other_players:
            ev = coach.evaluations[pid]
            obs_refs.append(ev.last_obs_ref)
            staleness = max(0, day - ev.last_seen_day)
            repeat_pen = 0.25 if pid in already_targeted else 0.0
            buyer_bonus = 0.055 if coach.philosophy == "buyer" else 0.010
            scout_score = round6(
                0.08
                + buyer_bonus
                + 0.016 * min(12, staleness)
                + 0.16 * ev.skill_estimate
                - repeat_pen
            )
            candidates.append(
                {
                    "kind": "scout",
                    "target": pid,
                    "method": None,
                    "score": scout_score,
                    "components": {
                        "buyer_bonus": round6(buyer_bonus),
                        "staleness": round6(0.016 * min(12, staleness)),
                        "skill_interest": round6(0.16 * ev.skill_estimate),
                        "repeat_penalty": round6(-repeat_pen),
                    },
                }
            )

    candidates.append(
        {
            "kind": "rest",
            "target": None,
            "method": None,
            "score": round6(config.coach_rest_value),
            "components": {"base_rest": round6(config.coach_rest_value)},
        }
    )

    best = max(
        candidates,
        key=lambda c: (
            c["score"],
            1 if c["kind"] == "propose" else (0 if c["kind"] == "observe" else -1),
            str(c["target"] or ""),
            str(c["method"] or ""),
        ),
    )
    return {
        "coach_id": coach.coach_id,
        "club_id": coach.club_id,
        "slot": slot,
        "chosen": best,
        "candidates": candidates,
        "obs_refs": sorted(set(obs_refs)),
    }


def decide_player_training(
    *,
    mind: PlayerMind,
    club_id: str,
    proposals: list[dict[str, Any]],
    config: LeagueEconomyConfig,
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    unsatisfied_drive = round6(
        config.satisfaction_drive_weight * max(0.0, 0.65 - mind.playing_time_satisfaction)
    )
    for m in TRAINING_METHODS:
        exp_g = mind.expected_gains[m]
        pref_bonus = 0.012 if m == mind.preferred_method else 0.0
        fatigue_pen = round6(config.training_fatigue_weight * mind.perceived_fatigue)

        matching_props = [p for p in proposals if p["method"] == m]
        advice_term = 0.0
        score_source: str | None = None
        if matching_props:
            best_prop = max(
                matching_props,
                key=lambda p: (mind.trust_by_coach.get(str(p["coach_id"]), 0.5), str(p["coach_id"])),
            )
            cid = str(best_prop["coach_id"])
            trust = mind.trust_by_coach.get(cid, 0.5)
            advice_term = round6(config.advice_weight * max(0.0, trust - mind.autonomy))
            score_source = cid

        score = round6(
            exp_g
            + pref_bonus
            + advice_term
            + unsatisfied_drive * (1.0 + 0.5 * mind.ambition)
            - fatigue_pen
        )
        candidates.append(
            {
                "action": m,
                "score": score,
                "score_source": score_source,
                "components": {
                    "expected_gain": round6(exp_g),
                    "pref_bonus": round6(pref_bonus),
                    "advice_term": round6(advice_term),
                    "unsatisfied_drive": round6(unsatisfied_drive * (1.0 + 0.5 * mind.ambition)),
                    "fatigue_penalty": round6(-fatigue_pen),
                },
            }
        )

    rest_score = round6(
        mind.rest_preference + config.rest_fatigue_weight * mind.perceived_fatigue
    )
    candidates.append(
        {
            "action": "rest",
            "score": rest_score,
            "score_source": None,
            "components": {
                "rest_preference": round6(mind.rest_preference),
                "fatigue_recovery_need": round6(config.rest_fatigue_weight * mind.perceived_fatigue),
            },
        }
    )

    best = max(candidates, key=lambda c: (c["score"], c["action"] != "rest", c["action"]))
    return {
        "player_id": mind.player_id,
        "club_id": club_id,
        "chosen": best,
        "candidates": candidates,
        "received_proposals": list(proposals),
        "obs_refs": [mind.last_obs_ref],
    }


def decide_club_lineup(
    *,
    coach: CoachMind,
    roster: list[str],
    config: LeagueEconomyConfig,
) -> dict[str, Any]:
    scored: list[dict[str, Any]] = []
    obs_refs: list[str] = []
    for pid in roster:
        ev = coach.evaluations[pid]
        obs_refs.append(ev.last_obs_ref)
        s_term = round6(config.select_skill_weight * ev.skill_estimate)
        m_term = round6(config.select_match_weight * ev.match_rating_estimate)
        f_pen = round6(config.select_fatigue_weight * ev.fatigue_estimate)
        total = round6(s_term + m_term - f_pen)
        scored.append(
            {
                "player_id": pid,
                "score": total,
                "components": {
                    "skill_term": s_term,
                    "match_term": m_term,
                    "fatigue_penalty": round6(-f_pen),
                },
            }
        )
    scored.sort(key=lambda x: (-x["score"], x["player_id"]))
    starters = [scored[0]["player_id"], scored[1]["player_id"]]
    bench = [x["player_id"] for x in scored[2:]]
    return {
        "club_id": coach.club_id,
        "coach_id": coach.coach_id,
        "starters": starters,
        "bench": bench,
        "ranking": scored,
        "obs_refs": sorted(set(obs_refs)),
    }


@dataclass(frozen=True)
class TransferOffer:
    """The received message; no private coach score or world state is exposed."""

    player_id: str
    to_club: str
    to_coach: str

    def __post_init__(self) -> None:
        if self.player_id not in PLAYER_IDS or self.to_club not in CLUB_IDS:
            raise ValueError("invalid transfer offer recipient or club")
        if CLUB_COACH_MAP[self.to_club] != self.to_coach:
            raise ValueError("transfer offer sender does not represent its club")


def choose_player_transfer_offer(
    *, mind: PlayerMind, offers: tuple[TransferOffer, ...]
) -> dict[str, Any]:
    """Player policy: own beliefs and explicitly received offers only.

    All current offers have identical terms. Club ID is a public, deterministic
    tie rule, not an estimate of playing time or the coach's private valuation.
    """
    seen: set[str] = set()
    evaluations = []
    dissatisfaction = max(0.0, 0.48 - mind.playing_time_satisfaction)
    for offer in sorted(offers, key=lambda o: o.to_club):
        if offer.player_id != mind.player_id or offer.to_club in seen:
            raise ValueError("wrong recipient or duplicate club offer")
        seen.add(offer.to_club)
        trust_to = mind.trust_by_coach.get(offer.to_coach, 0.5)
        expected_role_margin = round6(mind.self_estimate - 0.44)
        acceptance_score = round6(
            0.45 * expected_role_margin
            + 0.35 * (trust_to - 0.40)
            + 0.25 * dissatisfaction
            - 0.40 * mind.loyalty_bias
        )
        evaluations.append({
            "to_club": offer.to_club,
            "acceptance_score": acceptance_score,
            "offer_accepted": acceptance_score > 0.0,
        })
    accepted = sorted(
        (row for row in evaluations if row["offer_accepted"]),
        key=lambda row: (-row["acceptance_score"], row["to_club"]),
    )
    return {
        "player_id": mind.player_id,
        "received_offers": [asdict(o) for o in sorted(offers, key=lambda o: o.to_club)],
        "evaluations": evaluations,
        "chosen_to_club": accepted[0]["to_club"] if accepted else None,
        "tie_rule": "acceptance_score_desc_then_public_club_id",
        "obs_refs": [mind.last_obs_ref],
    }


def decide_player_transfer_request(*, mind: PlayerMind, bench_streak: int,
                                   config: LeagueEconomyConfig) -> dict[str, Any]:
    dissatisfaction = max(0.0, 0.48 - mind.playing_time_satisfaction)
    score = round6(0.58 * dissatisfaction + 0.075 * min(4, bench_streak)
                   + 0.18 * mind.ambition * max(0.0, mind.self_estimate - 0.42)
                   - mind.loyalty_bias)
    return {"request_score": score, "wants_transfer": score > config.transfer_request_threshold,
            "obs_refs": [mind.last_obs_ref]}


def decide_transfer_release(*, coach: CoachMind, roster: list[str], player_id: str,
                            config: LeagueEconomyConfig) -> dict[str, Any]:
    """Releasing coach policy: own knowledge and own public roster only."""
    lineup = decide_club_lineup(coach=coach, roster=roster, config=config)
    ranking = lineup["ranking"]
    player_rank = next(i + 1 for i, row in enumerate(ranking) if row["player_id"] == player_id)
    surplus = round6(ranking[1]["score"] - ranking[player_rank - 1]["score"] + 0.04)
    return {"release_consented": len(roster) > MIN_CLUB_ROSTER and (player_rank >= 3 or surplus >= 0),
            "player_rank_in_from": player_rank, "release_surplus_score": surplus,
            "obs_refs": lineup["obs_refs"]}


def decide_transfer_acquisition(*, coach: CoachMind, roster: list[str], player_id: str,
                                config: LeagueEconomyConfig) -> dict[str, Any]:
    """Buying coach policy: no releasing coach or player Mind is accessible."""
    lineup = decide_club_lineup(coach=coach, roster=roster, config=config)
    second = lineup["ranking"][1]["score"]
    target = coach.evaluations[player_id]
    value = round6(config.select_skill_weight * target.skill_estimate
                   + config.select_match_weight * target.match_rating_estimate
                   - config.select_fatigue_weight * target.fatigue_estimate)
    bias = 0.035 if coach.philosophy == "buyer" else -0.025
    if config.buyer_strategy == "monopolist_hoarder" and coach.philosophy == "buyer":
        bias += 0.065
    score = round6(value - second + bias)
    return {"offer_score": score, "target_valuation": value, "second_starter_score": second,
            "wants_offer": score > config.transfer_offer_threshold,
            "obs_refs": sorted(set(lineup["obs_refs"] + [target.last_obs_ref]))}


def resolve_transfer_offer_request(*, from_club: str, to_club: str, previous_clubs: list[str],
                                   roster_size: int, cash_units: int, required_cash_units: int) -> dict[str, Any]:
    """World market rule, used for ordinary offers AND attempted churn offers."""
    validate_money_units(cash_units, "cash_units")
    validate_money_units(required_cash_units, "required_cash_units")
    if from_club not in CLUB_IDS or to_club not in CLUB_IDS:
        raise ValueError("unknown club in transfer request")
    circular = to_club in previous_clubs
    room = roster_size < MAX_CLUB_ROSTER
    budget = cash_units >= required_cash_units
    if to_club == from_club:
        reason = "same_club"
    elif circular:
        reason = "circular_transfer"
    elif not room:
        reason = "roster_full"
    elif not budget:
        reason = "insufficient_cash"
    else:
        reason = "accepted"
    return {"accepted": reason == "accepted", "reason": reason,
            "is_circular": circular, "has_roster_room": room, "has_budget": budget}


def decide_transfer_bidding(
    *,
    player_id: str,
    from_club: str,
    from_roster: list[str],
    previous_clubs: list[str],
    bench_streak: int,
    player_mind: PlayerMind,
    coaches: dict[str, CoachMind],
    club_rosters: dict[str, list[str]],
    club_cashes_units: dict[str, int],
    config: LeagueEconomyConfig,
) -> dict[str, Any]:
    for value in club_cashes_units.values():
        validate_money_units(value, "club_cashes_units")
    # World-side coordinator. The following pure policies receive ONE Mind each.
    # This combined private diagnostic is never passed to an agent.
    request = decide_player_transfer_request(mind=player_mind, bench_streak=bench_streak, config=config)
    from_coach = coaches[CLUB_COACH_MAP[from_club]]
    release = decide_transfer_release(coach=from_coach, roster=from_roster,
                                      player_id=player_id, config=config)

    required_upfront_units = (
        (config.base_transfer_fee_solidarity_units + config.solidarity_transfer_grant_units)
        if config.solidarity_mode == "enabled"
        else (config.base_transfer_fee_no_solidarity_units)
    )

    club_offers: list[dict[str, Any]] = []
    circular_attempts_blocked = 0
    obs_refs = request["obs_refs"] + release["obs_refs"]
    for to_club in CLUB_IDS:
        if to_club == from_club:
            continue
        to_coach = coaches[CLUB_COACH_MAP[to_club]]
        private = decide_transfer_acquisition(coach=to_coach, roster=club_rosters[to_club],
                                              player_id=player_id, config=config)
        is_circular = to_club in previous_clubs
        forced = is_circular and config.adversarial_mode == "churn_and_double_claim_attempt"
        submitted = private["wants_offer"] or forced
        resolution = None
        if submitted:
            resolution = resolve_transfer_offer_request(
                from_club=from_club, to_club=to_club, previous_clubs=previous_clubs,
                roster_size=len(club_rosters[to_club]), cash_units=club_cashes_units[to_club],
                required_cash_units=(required_upfront_units + config.buyer_min_cash_reserve_units))
            if resolution["reason"] == "circular_transfer":
                circular_attempts_blocked += 1
        obs_refs.extend(private["obs_refs"])
        club_offers.append({
            "to_club": to_club, "to_coach": to_coach.coach_id,
            "offer_score": private["offer_score"], "target_valuation": private["target_valuation"],
            "second_starter_score": private["second_starter_score"],
            "has_roster_room": len(club_rosters[to_club]) < MAX_CLUB_ROSTER,
            "has_budget": club_cashes_units[to_club] >= (required_upfront_units + config.buyer_min_cash_reserve_units),
            "is_circular": is_circular, "request_submitted": submitted,
            "request_source": "adversarial_probe" if forced else "coach_policy",
            "resolution": resolution, "obs_refs": private["obs_refs"],
            "offer_made": resolution is not None and resolution["accepted"],
        })

    received = tuple(
        TransferOffer(player_id, o["to_club"], o["to_coach"])
        for o in club_offers if o["offer_made"]
    )
    player_choice = choose_player_transfer_offer(mind=player_mind, offers=received)
    scores = {row["to_club"]: row for row in player_choice["evaluations"]}
    for offer in club_offers:
        evaluation = scores.get(offer["to_club"])
        offer["acceptance_score"] = None if evaluation is None else evaluation["acceptance_score"]
        offer["offer_accepted"] = evaluation is not None and evaluation["offer_accepted"]
    chosen_to_club = player_choice["chosen_to_club"] if request["wants_transfer"] and release["release_consented"] else None
    transfer_approved = chosen_to_club is not None

    return {
        "player_id": player_id,
        "from_club": from_club,
        "wants_transfer": request["wants_transfer"],
        "request_score": request["request_score"],
        "release_consented": release["release_consented"],
        "player_rank_in_from": release["player_rank_in_from"],
        "release_surplus_score": release["release_surplus_score"],
        "required_upfront_fee_units": required_upfront_units,
        "club_offers": club_offers,
        "player_choice": player_choice,
        "chosen_to_club": chosen_to_club,
        "transfer_approved": transfer_approved,
        "circular_attempts_blocked": circular_attempts_blocked,
        "obs_refs": sorted(set(obs_refs)),
    }


# ============================================================================
# Daily Simulation Step: Training Days, Matchdays, and Transfer Windows
# ============================================================================

def _step_training_day(world: LeagueWorldState, day: int) -> None:
    cfg = world.config
    # 1. Club training facility operation check (uses club cash_units)
    facility_active_by_club: dict[str, bool] = {}
    for cid in CLUB_IDS:
        acc = world.club_accounts[cid]
        if acc.cash_units >= (cfg.facility_reserve_floor_units + cfg.facility_daily_cost_units):
            tx_ev = _record_ledger_tx(
                world,
                day=day,
                payer=cid,
                payee="external_treasury_outflow",
                amount_units=cfg.facility_daily_cost_units,
                reason="training_facility_operation",
                cause_refs=[world.events[-1]["id"]],
            )
            acc.facility_active_days += 1
            facility_active_by_club[cid] = True
            _append_event(
                world,
                day=day,
                kind="club_facility_status",
                actor=cid,
                data={"club_id": cid, "facility_active": True, "cash_after_units": acc.cash_units},
                cause_refs=[tx_ev],
            )
        else:
            facility_active_by_club[cid] = False
            _append_event(
                world,
                day=day,
                kind="club_facility_status",
                actor=cid,
                data={"club_id": cid, "facility_active": False, "cash_after_units": acc.cash_units},
                cause_refs=[world.events[-1]["id"]],
            )

    # 2. Coaches choose 2 daily slots (observe / propose / scout / rest)
    proposals_by_player: dict[str, list[dict[str, Any]]] = {pid: [] for pid in PLAYER_IDS}
    observed_sessions: dict[str, list[tuple[str, str]]] = {pid: [] for pid in PLAYER_IDS}
    scouted_targets: list[tuple[str, str, str]] = []

    for coach_id in COACH_IDS:
        coach = world.coaches[coach_id]
        own_roster = list(world.club_rosters[coach.club_id])
        other_players = [
            pid for pid in PLAYER_IDS if world.players[pid].club_id != coach.club_id
        ]
        already_targeted: set[str] = set()
        for slot in range(1, cfg.coach_daily_slots + 1):
            club_cash_units = world.club_accounts[coach.club_id].cash_units
            dec = decide_coach_training(
                coach=coach,
                own_roster=own_roster,
                other_players=other_players,
                club_cash_units=club_cash_units,
                day=day,
                slot=slot,
                already_targeted=already_targeted,
                config=cfg,
            )
            ev_id = _append_event(
                world,
                day=day,
                kind="coach_training_decision",
                actor=coach_id,
                data=dec,
                cause_refs=dec["obs_refs"],
            )
            chosen = dec["chosen"]
            target = chosen["target"]
            if target is not None:
                already_targeted.add(str(target))
            if chosen["kind"] == "observe" and target is not None:
                observed_sessions[str(target)].append((coach_id, ev_id))
            elif chosen["kind"] == "propose" and target is not None:
                proposals_by_player[str(target)].append(
                    {
                        "coach_id": coach_id,
                        "club_id": coach.club_id,
                        "method": chosen["method"],
                        "forecast": chosen["forecast"],
                        "decision_ref": ev_id,
                    }
                )
            elif chosen["kind"] == "scout" and target is not None:
                tx_id = _record_ledger_tx(
                    world,
                    day=day,
                    payer=coach.club_id,
                    payee="external_treasury_outflow",
                    amount_units=cfg.scout_action_cost_units,
                    reason="scout_expense",
                    cause_refs=[ev_id],
                )
                scouted_targets.append((coach_id, str(target), tx_id))

    # 3. Players decide A / B / rest and update A (traits/fatigue) & B (beliefs/trust)
    for pid in PLAYER_IDS:
        p = world.players[pid]
        mind = world.player_minds[pid]
        cid = p.club_id
        props = proposals_by_player[pid]
        p_dec = decide_player_training(
            mind=mind,
            club_id=cid,
            proposals=props,
            config=cfg,
        )
        p_dec_ev = _append_event(
            world,
            day=day,
            kind="player_training_decision",
            actor=pid,
            data=p_dec,
            cause_refs=p_dec["obs_refs"] + [pr["decision_ref"] for pr in props],
        )

        action = str(p_dec["chosen"]["action"])
        pre_skill = p.composite_skill()

        if action in TRAINING_METHODS:
            w_pace, w_pass, w_shoot = METHOD_TRAIT_WEIGHTS[action]
            rate = p.rates[action]
            headroom = max(0.0, 1.0 - pre_skill / p.cap)
            fatigue_factor = max(0.25, 1.0 - 0.55 * p.fatigue)
            fac_mult = (
                cfg.facility_Active_multiplier
                if facility_active_by_club[cid]
                else cfg.facility_inactive_multiplier
            )
            base_delta = rate * headroom * fatigue_factor * fac_mult * 0.25
            d_pace = base_delta * w_pace
            d_pass = base_delta * w_pass
            d_shoot = base_delta * w_shoot
            p.traits = MatchTraits(
                pace=round6(clamp(p.traits.pace + d_pace, 0.15, p.cap)),
                passing=round6(clamp(p.traits.passing + d_pass, 0.15, p.cap)),
                shooting=round6(clamp(p.traits.shooting + d_shoot, 0.15, p.cap)),
            )
            p.fatigue = round6(clamp(p.fatigue + 0.075, 0.0, 0.95))
        else:
            p.fatigue = round6(clamp(p.fatigue - 0.14, 0.0, 0.95))

        post_skill = p.composite_skill()
        true_gain = round6(post_skill - pre_skill)

        # Self observation & B update
        noise = (uniform_from_key(world.seed, day, "self_obs", pid) - 0.5) * 2.0 * cfg.self_noise
        obs_skill = round6(clamp(post_skill + noise, 0.10, 0.98))
        obs_gain = round6(true_gain + noise * 0.35) if action in TRAINING_METHODS else 0.0
        obs_fatigue = observe_fatigue(true_fatigue=p.fatigue, noise=cfg.self_fatigue_noise,
                                      seed=world.seed, day=day, observer_id=pid,
                                      player_id=pid, context="training_self")
        obs_ev = _append_event(
            world,
            day=day,
            kind="player_self_observation",
            actor=pid,
            data={
                "player_id": pid,
                "action": action,
                "obs_skill": obs_skill,
                "obs_gain": obs_gain,
                "perceived_fatigue": obs_fatigue,
            },
            cause_refs=[p_dec_ev],
        )

        # Advice feedback on matching proposals before updating expected_gains
        if action in TRAINING_METHODS:
            self_pred = mind.expected_gains[action]
            for pr in props:
                if pr["method"] == action:
                    c_id = str(pr["coach_id"])
                    coach_pred = float(pr["forecast"])
                    err_self = abs(obs_gain - self_pred)
                    err_coach = abs(obs_gain - coach_pred)
                    old_trust = mind.trust_by_coach[c_id]
                    delta_trust = cfg.trust_alpha * cfg.trust_scale * (err_self - err_coach)
                    new_trust = round6(clamp(old_trust + delta_trust, 0.05, 0.95))
                    mind.trust_by_coach[c_id] = new_trust
                    _append_event(
                        world,
                        day=day,
                        kind="advice_feedback",
                        actor=pid,
                        data={
                            "player_id": pid,
                            "coach_id": c_id,
                            "method": action,
                            "obs_gain": obs_gain,
                            "self_prediction": round6(self_pred),
                            "coach_prediction": round6(coach_pred),
                            "old_trust": round6(old_trust),
                            "new_trust": new_trust,
                        },
                        cause_refs=[obs_ev, str(pr["decision_ref"])],
                    )
            mind.expected_gains[action] = round6(
                clamp(
                    (1.0 - cfg.player_gain_alpha) * mind.expected_gains[action]
                    + cfg.player_gain_alpha * obs_gain,
                    -0.05,
                    0.18,
                )
            )

        mind.self_estimate = round6(
            clamp(
                (1.0 - cfg.player_skill_alpha) * mind.self_estimate
                + cfg.player_skill_alpha * obs_skill,
                0.10,
                0.98,
            )
        )
        mind.perceived_fatigue = obs_fatigue
        mind.last_obs_ref = obs_ev

        # Coach session observations
        for c_id, session_ref in observed_sessions[pid]:
            c_mind = world.coaches[c_id]
            c_ev = c_mind.evaluations[pid]
            c_noise = (
                (uniform_from_key(world.seed, day, "coach_obs", f"{c_id}:{pid}") - 0.5)
                * 2.0
                * cfg.coach_noise
            )
            c_obs_skill = round6(clamp(post_skill + c_noise, 0.10, 0.98))
            c_obs_gain = round6(true_gain + c_noise * 0.35) if action in TRAINING_METHODS else 0.0
            c_ev.skill_estimate = round6(
                clamp(
                    (1.0 - cfg.coach_skill_alpha) * c_ev.skill_estimate
                    + cfg.coach_skill_alpha * c_obs_skill,
                    0.10,
                    0.98,
                )
            )
            c_ev.fatigue_estimate = observe_fatigue(
                true_fatigue=p.fatigue, noise=cfg.coach_fatigue_noise, seed=world.seed,
                day=day, observer_id=c_id, player_id=pid, context="training_coach")
            c_ev.last_seen_day = day
            if action in TRAINING_METHODS:
                base_g = c_mind.base_method_gains[action]
                target_res = c_obs_gain - base_g
                c_ev.method_residuals[action] = round6(
                    clamp(
                        (1.0 - cfg.coach_residual_alpha) * c_ev.method_residuals[action]
                        + cfg.coach_residual_alpha * target_res,
                        -0.05,
                        0.08,
                    )
                )
            c_obs_id = _append_event(
                world,
                day=day,
                kind="coach_session_observation",
                actor=c_id,
                data={
                    "coach_id": c_id,
                    "player_id": pid,
                    "action": action,
                    "obs_skill": c_obs_skill,
                    "obs_gain": c_obs_gain,
                    "obs_fatigue": c_ev.fatigue_estimate,
                    "skill_estimate": c_ev.skill_estimate,
                },
                cause_refs=[p_dec_ev, session_ref, c_ev.last_obs_ref],
            )
            c_ev.last_obs_ref = c_obs_id

    # 4. External scouting observations
    for c_id, target_pid, tx_id in scouted_targets:
        c_mind = world.coaches[c_id]
        c_ev = c_mind.evaluations[target_pid]
        p_target = world.players[target_pid]
        s_noise = (
            (uniform_from_key(world.seed, day, "scout_obs", f"{c_id}:{target_pid}") - 0.5)
            * 2.0
            * cfg.scout_noise
        )
        s_obs_skill = round6(clamp(p_target.composite_skill() + s_noise, 0.10, 0.98))
        c_ev.skill_estimate = round6(
            clamp(
                (1.0 - cfg.coach_scout_alpha) * c_ev.skill_estimate
                + cfg.coach_scout_alpha * s_obs_skill,
                0.10,
                0.98,
            )
        )
        c_ev.match_rating_estimate = round6(
            clamp(
                0.70 * c_ev.match_rating_estimate + 0.30 * s_obs_skill,
                0.10,
                0.98,
            )
        )
        c_ev.fatigue_estimate = observe_fatigue(
            true_fatigue=p_target.fatigue, noise=cfg.scout_fatigue_noise, seed=world.seed,
            day=day, observer_id=c_id, player_id=target_pid, context="scout")
        c_ev.last_seen_day = day
        s_obs_id = _append_event(
            world,
            day=day,
            kind="coach_scout_observation",
            actor=c_id,
            data={
                "coach_id": c_id,
                "target_player_id": target_pid,
                "obs_skill": s_obs_skill,
                "obs_fatigue": c_ev.fatigue_estimate,
                "skill_estimate": c_ev.skill_estimate,
                "match_rating_estimate": c_ev.match_rating_estimate,
            },
            cause_refs=[tx_id, c_ev.last_obs_ref],
        )
        c_ev.last_obs_ref = s_obs_id


def _run_2d_fixture(
    world: LeagueWorldState,
    *,
    day: int,
    fixture_idx: int,
    home_club: str,
    away_club: str,
    home_starters: list[str],
    away_starters: list[str],
) -> dict[str, Any]:
    cfg = world.config
    match_seed = int(world.seed) * 10007 + int(day) * 97 + int(fixture_idx) * 13
    match_cfg = MatchConfig(
        condition="baseline",
        noise_scale=cfg.match_noise_scale,
    )
    match_world = m2d.create_match_world(seed=match_seed, condition="baseline", config=match_cfg)

    slot_to_pid: dict[str, str] = {
        "h1": home_starters[0],
        "h2": home_starters[1],
        "a1": away_starters[0],
        "a2": away_starters[1],
    }

    for slot_id, team_name in (("h1", "home"), ("h2", "home"), ("a1", "away"), ("a2", "away")):
        pid = slot_to_pid[slot_id]
        p = world.players[pid]
        pm = world.player_minds[pid]
        f = p.fatigue
        eff_traits = MatchTraits(
            pace=round6(clamp(p.traits.pace * (1.0 - 0.18 * f), 0.15, 1.0)),
            passing=round6(clamp(p.traits.passing * (1.0 - 0.10 * f), 0.15, 1.0)),
            shooting=round6(clamp(p.traits.shooting * (1.0 - 0.10 * f), 0.15, 1.0)),
        )
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
                style=pm.style,
                coach_directive="balanced",
            ),
        )

    m2d.run_match(cfg.match_ticks, world=match_world)

    stats_by_pid: dict[str, dict[str, int]] = {
        pid: {"goals": 0, "shots": 0, "passes_completed": 0, "tackles_won": 0}
        for pid in (*home_starters, *away_starters)
    }
    home_poss = 0
    away_poss = 0
    home_shots = 0
    away_shots = 0
    home_passes = 0
    away_passes = 0

    for ev in match_world.events:
        kind = ev["kind"]
        if kind == "ball_kick":
            slot_id = str(ev.get("kicker_id", ""))
            if slot_id in slot_to_pid and ev.get("kick_kind") == "shoot":
                stats_by_pid[slot_to_pid[slot_id]]["shots"] += 1
                if slot_id in ("h1", "h2"):
                    home_shots += 1
                else:
                    away_shots += 1
        elif kind == "ball_control" and ev.get("outcome") == "pass_completed":
            from_slot = str(ev.get("from_player_id") or "")
            if from_slot in slot_to_pid:
                stats_by_pid[slot_to_pid[from_slot]]["passes_completed"] += 1
                if from_slot in ("h1", "h2"):
                    home_passes += 1
                else:
                    away_passes += 1
        elif kind == "tackle_resolution" and ev.get("outcome") == "tackle_won":
            ch_slot = str(ev.get("challenger_id") or "")
            if ch_slot in slot_to_pid:
                stats_by_pid[slot_to_pid[ch_slot]]["tackles_won"] += 1
        elif kind == "goal":
            sc_slot = str(ev.get("scorer_id") or "")
            if sc_slot in slot_to_pid:
                stats_by_pid[slot_to_pid[sc_slot]]["goals"] += 1
        elif kind == "tick_summary":
            owner = ev.get("ball", {}).get("owner_id")
            if owner in ("h1", "h2"):
                home_poss += 1
            elif owner in ("a1", "a2"):
                away_poss += 1

    return {
        "home_club": home_club,
        "away_club": away_club,
        "home_starters": list(home_starters),
        "away_starters": list(away_starters),
        "home_goals": int(match_world.score["home"]),
        "away_goals": int(match_world.score["away"]),
        "home_shots": home_shots,
        "away_shots": away_shots,
        "home_possession_ticks": home_poss,
        "away_possession_ticks": away_poss,
        "home_pass_completions": home_passes,
        "away_pass_completions": away_passes,
        "player_stats": stats_by_pid,
    }


def _step_matchday(world: LeagueWorldState, day: int) -> None:
    cfg = world.config
    round_idx = (day // cfg.match_interval_days) - 1
    pairings = ROUND_ROBIN_PAIRINGS[round_idx % len(ROUND_ROBIN_PAIRINGS)]
    flip_home_away = (round_idx // len(ROUND_ROBIN_PAIRINGS)) % 2 == 1

    # 1. Lineup selection for all 4 clubs
    lineups_by_club: dict[str, dict[str, Any]] = {}
    lineup_ev_by_club: dict[str, str] = {}
    for cid in CLUB_IDS:
        coach = world.coaches[CLUB_COACH_MAP[cid]]
        roster = list(world.club_rosters[cid])
        l_dec = decide_club_lineup(coach=coach, roster=roster, config=cfg)
        l_ev = _append_event(
            world,
            day=day,
            kind="lineup_decision",
            actor=coach.coach_id,
            data=l_dec,
            cause_refs=l_dec["obs_refs"],
        )
        lineups_by_club[cid] = l_dec
        lineup_ev_by_club[cid] = l_ev
        world.public_last_starters[cid] = list(l_dec["starters"])

    # 2. Play 2 head-to-head 2D matches
    for fix_idx, (c_left, c_right) in enumerate(pairings, start=1):
        home_club, away_club = (c_right, c_left) if flip_home_away else (c_left, c_right)
        home_starters = lineups_by_club[home_club]["starters"]
        away_starters = lineups_by_club[away_club]["starters"]
        m_res = _run_2d_fixture(
            world,
            day=day,
            fixture_idx=fix_idx,
            home_club=home_club,
            away_club=away_club,
            home_starters=home_starters,
            away_starters=away_starters,
        )
        match_id = f"m_d{day}_f{fix_idx}"
        m_ev = _append_event(
            world,
            day=day,
            kind="league_match_completed",
            actor="world",
            data={"match_id": match_id, **m_res},
            cause_refs=[lineup_ev_by_club[home_club], lineup_ev_by_club[away_club]],
        )

        # Update club standings
        h_acc = world.club_accounts[home_club]
        a_acc = world.club_accounts[away_club]
        hg = m_res["home_goals"]
        ag = m_res["away_goals"]
        h_acc.matches_played += 1
        a_acc.matches_played += 1
        h_acc.goals_for += hg
        h_acc.goals_against += ag
        a_acc.goals_for += ag
        a_acc.goals_against += hg
        h_acc.shots_for += m_res["home_shots"]
        a_acc.shots_for += m_res["away_shots"]
        h_acc.possession_ticks += m_res["home_possession_ticks"]
        a_acc.possession_ticks += m_res["away_possession_ticks"]
        h_acc.pass_completions += m_res["home_pass_completions"]
        a_acc.pass_completions += m_res["away_pass_completions"]

        if hg > ag:
            h_acc.wins += 1
            h_acc.points += 3
            a_acc.losses += 1
            h_inflow_units = cfg.matchday_base_inflow_units + cfg.matchday_win_bonus_units
            a_inflow_units = cfg.matchday_base_inflow_units
        elif ag > hg:
            a_acc.wins += 1
            a_acc.points += 3
            h_acc.losses += 1
            h_inflow_units = cfg.matchday_base_inflow_units
            a_inflow_units = cfg.matchday_base_inflow_units + cfg.matchday_win_bonus_units
        else:
            h_acc.draws += 1
            a_acc.draws += 1
            h_acc.points += 1
            a_acc.points += 1
            h_inflow_units = cfg.matchday_base_inflow_units + cfg.matchday_draw_bonus_units
            a_inflow_units = cfg.matchday_base_inflow_units + cfg.matchday_draw_bonus_units

        if cfg.inflow_mode == "standard":
            _record_ledger_tx(
                world,
                day=day,
                payer="external_league_pool",
                payee=home_club,
                amount_units=h_inflow_units,
                reason="matchday_revenue",
                cause_refs=[m_ev],
            )
            _record_ledger_tx(
                world,
                day=day,
                payer="external_league_pool",
                payee=away_club,
                amount_units=a_inflow_units,
                reason="matchday_revenue",
                cause_refs=[m_ev],
            )

        # Update player match stats, experience growth, satisfaction, and coach post-match ratings
        for cid, opp_cid in ((home_club, away_club), (away_club, home_club)):
            starters = set(lineups_by_club[cid]["starters"])
            club_won = (hg > ag) if cid == home_club else (ag > hg)
            for pid in world.club_rosters[cid]:
                p = world.players[pid]
                pm = world.player_minds[pid]
                if pid in starters:
                    st = m_res["player_stats"][pid]
                    p.appearances += 1
                    p.appearances_by_club[cid] = p.appearances_by_club.get(cid, 0) + 1
                    p.bench_streak = 0
                    p.goals_scored += st["goals"]
                    p.shots_taken += st["shots"]
                    p.passes_completed += st["passes_completed"]
                    p.tackles_won += st["tackles_won"]

                    # Match experience growth
                    headroom = max(0.0, p.cap - p.composite_skill())
                    exp_gain = 0.032 * headroom
                    p.traits = MatchTraits(
                        pace=round6(clamp(p.traits.pace + exp_gain, 0.15, p.cap)),
                        passing=round6(clamp(p.traits.passing + exp_gain, 0.15, p.cap)),
                        shooting=round6(clamp(p.traits.shooting + exp_gain, 0.15, p.cap)),
                    )
                    p.fatigue = round6(
                        clamp(p.fatigue + 0.055 + 0.002 * (st["shots"] + st["tackles_won"]), 0.0, 0.95)
                    )
                    pm.playing_time_satisfaction = round6(
                        clamp(pm.playing_time_satisfaction + 0.14, 0.05, 0.98)
                    )

                    # Development appearance dividend if transferred under solidarity
                    if (
                        cfg.solidarity_mode == "enabled"
                        and p.active_transfer_id is not None
                        and p.origin_club_id != cid
                    ):
                        request = dict(transfer_id=p.active_transfer_id, match_id=match_id,
                                       claimant_club=p.origin_club_id, match_event_ref=m_ev)
                        result = process_dividend_claim(world, **request)
                        if result["accepted"] and cfg.adversarial_mode == "churn_and_double_claim_attempt":
                            # A real second request through the same admission and payment code.
                            process_dividend_claim(world, **request)

                    # Both participating clubs' coaches observe starter performance
                    perf_raw = (
                        p.composite_skill()
                        + 0.06 * st["goals"]
                        + 0.015 * st["passes_completed"]
                        + 0.02 * st["tackles_won"]
                        + (0.03 if club_won else -0.01)
                    )
                    for obs_cid in (cid, opp_cid):
                        c_id = CLUB_COACH_MAP[obs_cid]
                        c_mind = world.coaches[c_id]
                        c_ev = c_mind.evaluations[pid]
                        m_noise = (
                            (uniform_from_key(world.seed, day, "match_obs", f"{c_id}:{pid}") - 0.5)
                            * 2.0
                            * cfg.match_obs_noise
                        )
                        rating_obs = round6(clamp(perf_raw + m_noise, 0.15, 0.98))
                        c_ev.match_rating_estimate = round6(
                            clamp(
                                (1.0 - cfg.coach_match_alpha) * c_ev.match_rating_estimate
                                + cfg.coach_match_alpha * rating_obs,
                                0.10,
                                0.98,
                            )
                        )
                        c_ev.skill_estimate = round6(
                            clamp(
                                (1.0 - cfg.coach_match_skill_blend) * c_ev.skill_estimate
                                + cfg.coach_match_skill_blend * rating_obs,
                                0.10,
                                0.98,
                            )
                        )
                        c_ev.fatigue_estimate = observe_fatigue(
                            true_fatigue=p.fatigue, noise=cfg.match_fatigue_noise, seed=world.seed,
                            day=day, observer_id=c_id, player_id=pid, context="match_coach")
                        c_ev.last_seen_day = day
                        c_ev.last_obs_ref = _append_event(
                            world, day=day, kind="coach_match_observation", actor=c_id,
                            data={"player_id": pid, "match_id": match_id, "rating_obs": rating_obs,
                                  "obs_fatigue": c_ev.fatigue_estimate},
                            cause_refs=[m_ev, c_ev.last_obs_ref])
                else:
                    # Bench player rests and loses satisfaction
                    p.bench_streak += 1
                    if p.bench_streak > p.max_bench_streak:
                        p.max_bench_streak = p.bench_streak
                    p.fatigue = round6(clamp(p.fatigue - 0.06, 0.0, 0.95))
                    pm.playing_time_satisfaction = round6(
                        clamp(pm.playing_time_satisfaction - 0.11, 0.05, 0.98)
                    )
                    # No paid/direct observation of this bench player: coach keeps prior.

                pm.perceived_fatigue = observe_fatigue(
                    true_fatigue=p.fatigue, noise=cfg.self_fatigue_noise, seed=world.seed,
                    day=day, observer_id=pid, player_id=pid, context="match_self")
                pm.last_obs_ref = _append_event(
                    world, day=day, kind="player_fatigue_observation", actor=pid,
                    data={"player_id": pid, "match_id": match_id, "started": pid in starters,
                          "obs_fatigue": pm.perceived_fatigue},
                    cause_refs=[m_ev, pm.last_obs_ref])

    # 3. Pay matchday wages to all players from club cash_units
    for cid in CLUB_IDS:
        for pid in world.club_rosters[cid]:
            acc = world.club_accounts[cid]
            wage_units = min(acc.cash_units, cfg.player_matchday_wage_units)
            if wage_units > 0:
                _record_ledger_tx(
                    world,
                    day=day,
                    payer=cid,
                    payee=pid,
                    amount_units=wage_units,
                    reason="player_wage",
                    cause_refs=[lineup_ev_by_club[cid]],
                )

    # 4. Transfer window processing on Day 28 and Day 56
    if day in TRANSFER_WINDOW_DAYS:
        _step_transfer_window(world, day)


def _step_transfer_window(world: LeagueWorldState, day: int) -> None:
    cfg = world.config
    if cfg.market_mode == "closed":
        _append_event(
            world,
            day=day,
            kind="transfer_window_closed_skip",
            actor="world",
            data={"day": day, "market_mode": cfg.market_mode},
            cause_refs=[world.events[-1]["id"]],
        )
        return

    for pid in PLAYER_IDS:
        p = world.players[pid]
        pm = world.player_minds[pid]
        from_club = p.club_id
        from_roster = list(world.club_rosters[from_club])
        club_cashes_units = {cid: world.club_accounts[cid].cash_units for cid in CLUB_IDS}

        t_dec = decide_transfer_bidding(
            player_id=pid,
            from_club=from_club,
            from_roster=from_roster,
            previous_clubs=p.previous_clubs,
            bench_streak=p.bench_streak,
            player_mind=pm,
            coaches=world.coaches,
            club_rosters=world.club_rosters,
            club_cashes_units=club_cashes_units,
            config=cfg,
        )
        offer_refs = []
        for offer in t_dec["club_offers"]:
            if not offer["request_submitted"]:
                continue
            req_ref = _append_event(
                world, day=day, kind="transfer_offer_requested", actor=offer["to_coach"],
                data={"player_id": pid, "from_club": from_club, "to_club": offer["to_club"],
                      "source": offer["request_source"]}, cause_refs=offer["obs_refs"])
            res_ref = _append_event(
                world, day=day, kind="transfer_offer_resolved", actor="world",
                data={"player_id": pid, "to_club": offer["to_club"], **offer["resolution"]},
                cause_refs=[req_ref])
            if offer["resolution"]["reason"] == "circular_transfer":
                world.ledger.blocked_circular_transfers += 1
            if offer["offer_made"]:
                message = asdict(TransferOffer(pid, offer["to_club"], offer["to_coach"]))
                offer_refs.append(_append_event(
                    world, day=day, kind="transfer_offer_received", actor=offer["to_coach"],
                    data={"recipient": pid, "offer": message}, cause_refs=[res_ref]))
        choice_ref = _append_event(
            world, day=day, kind="player_transfer_choice", actor=pid,
            data=t_dec["player_choice"],
            cause_refs=t_dec["player_choice"]["obs_refs"] + offer_refs,
        )
        t_ev = _append_event(
            world,
            day=day,
            kind="transfer_window_decision",
            actor="world",
            data=t_dec,
            cause_refs=t_dec["obs_refs"] + [choice_ref],
        )

        if t_dec["transfer_approved"] and t_dec["chosen_to_club"] is not None:
            to_club = str(t_dec["chosen_to_club"])
            base_fee_units = (
                cfg.base_transfer_fee_solidarity_units
                if cfg.solidarity_mode == "enabled"
                else cfg.base_transfer_fee_no_solidarity_units
            )
            sol_grant_units = (
                cfg.solidarity_transfer_grant_units
                if cfg.solidarity_mode == "enabled"
                else 0
            )

            tx_refs = [t_ev]
            tx1 = _record_ledger_tx(
                world,
                day=day,
                payer=to_club,
                payee=from_club,
                amount_units=base_fee_units,
                reason="transfer_base_fee",
                cause_refs=[t_ev],
            )
            tx_refs.append(tx1)
            if sol_grant_units > 0:
                tx2 = _record_ledger_tx(
                    world,
                    day=day,
                    payer=to_club,
                    payee=p.origin_club_id,
                    amount_units=sol_grant_units,
                    reason="transfer_solidarity_grant",
                    cause_refs=[t_ev],
                )
                tx_refs.append(tx2)

            # Atomic roster update
            world.club_rosters[from_club].remove(pid)
            world.club_rosters[to_club].append(pid)
            p.previous_clubs.append(from_club)
            p.club_id = to_club
            p.transfers_count += 1
            p.bench_streak = 0
            pm.playing_time_satisfaction = round6(
                clamp(pm.playing_time_satisfaction + 0.18, 0.05, 0.98)
            )

            tr_id = f"tr_d{day}_{pid}_{from_club}_to_{to_club}"
            p.active_transfer_id = tr_id
            world.ledger.transfer_contracts[tr_id] = TransferContractRecord(
                transfer_id=tr_id,
                day=day,
                player_id=pid,
                origin_club_id=p.origin_club_id,
                from_club=from_club,
                to_club=to_club,
                base_fee_units=base_fee_units,
                solidarity_grant_units=sol_grant_units,
                dividend_per_start_units=(
                    cfg.development_dividend_per_start_units
                    if cfg.solidarity_mode == "enabled"
                    else 0
                ),
                max_dividend_triggers=cfg.max_dividend_triggers,
                dividend_triggers_used=0,
            )

            comp_ev = _append_event(
                world,
                day=day,
                kind="transfer_completed",
                actor=pid,
                data={
                    "transfer_id": tr_id,
                    "player_id": pid,
                    "origin_club_id": p.origin_club_id,
                    "from_club": from_club,
                    "to_club": to_club,
                    "base_fee_units": (base_fee_units),
                    "solidarity_grant_units": (sol_grant_units),
                },
                cause_refs=tx_refs,
            )
            pm.last_obs_ref = comp_ev


def advance_league_days(world: LeagueWorldState, days: int) -> None:
    if type(days) is not int or days < 0:
        raise ValueError("days must be a nonnegative integer")
    for _ in range(days):
        world.day += 1
        if world.day % world.config.match_interval_days == 0:
            _step_matchday(world, world.day)
        else:
            _step_training_day(world, world.day)


def season_progress(world: LeagueWorldState) -> dict[str, int]:
    """T023: season counter derived from the day (no stored state). Season ends on days that are multiples of the length."""
    length = world.config.season_length_days
    if length <= 0:
        raise ValueError("season_length_days is not set")
    day = world.day
    return {"season": (day - 1) // length + 1 if day else 1, "day_in_season": (day - 1) % length + 1 if day else 0,
            "seasons_completed": day // length}


def advance_league_seasons(world: LeagueWorldState, seasons: int, on_season_end: Any = None) -> None:
    """T023: advance to `seasons` further season ends, calling on_season_end(world, seasons_completed) at each.

    The hook is the place a later task will add aging/retirement/intake as recorded events; it must not mutate the world otherwise.
    """
    if type(seasons) is not int or seasons < 0:
        raise ValueError("seasons must be a nonnegative integer")
    if on_season_end is not None and not callable(on_season_end):
        raise ValueError("on_season_end must be callable")
    season_progress(world)  # raises when no season length is set
    length = world.config.season_length_days
    for _ in range(seasons):
        advance_league_days(world, length - world.day % length)
        if on_season_end is not None:
            on_season_end(world, season_progress(world)["seasons_completed"])


def run_league_world(
    *,
    seed: int = 17,
    days: int = 84,
    config: LeagueEconomyConfig | None = None,
) -> LeagueWorldState:
    if type(seed) is not int:
        raise ValueError("seed must be an integer")
    cfg = config or LeagueEconomyConfig()
    world = _build_initial_world(seed=seed, config=cfg)
    advance_league_days(world, days)
    _validate_league_invariants(world)
    return world


# ============================================================================
# Cheap runtime invariants, replay-verified persistence, and summary
# ============================================================================

def _validate_league_invariants(world: LeagueWorldState) -> None:
    """Fast checks during experiments; these alone do not certify a checkpoint."""
    if type(world.day) is not int or world.day < 0 or type(world.seed) is not int:
        raise ValueError("invalid world day or seed")
    # Roster partition & size invariants
    seen_pids: list[str] = []
    for cid in CLUB_IDS:
        roster = world.club_rosters.get(cid)
        if roster is None:
            raise ValueError(f"missing club roster: {cid}")
        if not (MIN_CLUB_ROSTER <= len(roster) <= MAX_CLUB_ROSTER):
            raise ValueError(f"club {cid} roster size {len(roster)} violates [{MIN_CLUB_ROSTER}, {MAX_CLUB_ROSTER}]")
        for pid in roster:
            if pid not in world.players:
                raise ValueError(f"unknown player {pid} in {cid}")
            if world.players[pid].club_id != cid:
                raise ValueError(f"player {pid} club_id {world.players[pid].club_id} != roster club {cid}")
            seen_pids.append(pid)
    if sorted(seen_pids) != sorted(PLAYER_IDS):
        raise ValueError("club rosters do not form an exact partition of PLAYER_IDS")

    # All monetary state is exact and nonnegative, including unused contracts.
    _validate_money_fields(world.config)
    for record in (*world.club_accounts.values(), *world.players.values(),
                   *world.initial_players.values(), world.ledger,
                   *world.ledger.transfer_contracts.values()):
        _validate_money_fields(record)

    # Non-negative cash_units & strict ledger conservation invariant
    for cid, acc in world.club_accounts.items():
        if acc.cash_units < 0:
            raise ValueError(f"negative club cash_units for {cid}: {acc.cash_units}")
    disc = compute_ledger_discrepancy(world)
    if disc != 0:
        raise ValueError(f"ledger conservation violated: discrepancy={disc}")
    if actual_duplicate_payments(world) > 0:
        raise ValueError("duplicate dividend claim detected in ledger")
    if type(world.ledger.claimed_dividend_keys) is not set:
        raise ValueError("claimed_dividend_keys must be a set")

    # 1-pass event causal chain & transfer invariant check
    seen_event_ids: set[str] = {"init:0"}
    for idx, ev in enumerate(world.events, start=1):
        _validate_event_money(ev.get("data"))
        ev_id = str(ev.get("id", ""))
        if ev_id != f"e{idx}":
            raise ValueError(f"non-monotonic event id at {idx}: {ev_id}")
        ev_day = int(ev.get("day", -1))
        if ev_day < 0 or ev_day > world.day:
            raise ValueError(f"invalid event day {ev_day} in {ev_id}")
        for ref in ev.get("cause_refs", []):
            if str(ref) not in seen_event_ids:
                raise ValueError(f"event {ev_id} references unknown or future cause_ref {ref}")
        if ev.get("kind") == "transfer_completed":
            if world.config.market_mode == "closed":
                raise ValueError("transfer_completed event present when market_mode is closed")
            d = ev["data"]
            if d["from_club"] == d["to_club"]:
                raise ValueError("self-transfer in transfer_completed")
        seen_event_ids.add(ev_id)


def summarize_academy_cohorts(world: LeagueWorldState) -> dict[str, Any]:
    """Research-only growth: fixed initial cohort and descriptive never-movers.

    Initial membership follows initial club_id, independently of player labels or
    current affiliation. The never-transferred subset is condition-dependent and
    must not be interpreted as a fixed group for causal comparisons.
    """
    initial_ids = sorted(
        pid for pid, initial in world.initial_players.items()
        if initial.club_id in ACADEMY_CLUB_IDS
    )
    never_transferred_ids = [
        pid for pid in initial_ids
        if world.players[pid].transfers_count == 0
        and world.players[pid].club_id == world.initial_players[pid].club_id
    ]
    result: dict[str, Any] = {}
    for group, ids in (
        ("academy_initial_cohort", initial_ids),
        ("academy_never_transferred", never_transferred_ids),
    ):
        gains = [
            round6(world.players[pid].composite_skill() - world.initial_players[pid].composite_skill())
            for pid in ids
        ]
        result[f"{group}_ids"] = ids
        result[f"{group}_count"] = len(ids)
        result[f"{group}_skill_gain"] = round6(fsum(gains) / len(gains)) if gains else None
    return result


def summarize_league_world(world: LeagueWorldState) -> dict[str, Any]:
    _validate_league_invariants(world)
    talent_gains = [
        round6(world.players[pid].composite_skill() - world.initial_players[pid].composite_skill())
        for pid in TALENT_PLAYER_IDS
    ]
    talent_transferred = [
        1.0 if world.players[pid].transfers_count > 0 else 0.0
        for pid in TALENT_PLAYER_IDS
    ]
    talent_apps = [float(world.players[pid].appearances) for pid in TALENT_PLAYER_IDS]
    talent_sat = [
        float(world.player_minds[pid].playing_time_satisfaction)
        for pid in TALENT_PLAYER_IDS
    ]

    legacy_four_slot_ids = ("p1", "p2", "p7", "p8")
    legacy_four_slot_gains = [
        round6(world.players[pid].composite_skill() - world.initial_players[pid].composite_skill())
        for pid in legacy_four_slot_ids
    ]
    all_gains = [
        round6(world.players[pid].composite_skill() - world.initial_players[pid].composite_skill())
        for pid in PLAYER_IDS
    ]

    ac_a = world.club_accounts["club_a"]
    ac_b = world.club_accounts["club_b"]
    ac_c = world.club_accounts["club_c"]
    ac_d = world.club_accounts["club_d"]

    academy_points = ac_a.points + ac_c.points
    buyer_points = ac_b.points + ac_d.points
    academy_gd = (ac_a.goals_for - ac_a.goals_against) + (ac_c.goals_for - ac_c.goals_against)
    buyer_gd = (ac_b.goals_for - ac_b.goals_against) + (ac_d.goals_for - ac_d.goals_against)
    academy_facility_days = ac_a.facility_active_days + ac_c.facility_active_days
    buyer_facility_days = ac_b.facility_active_days + ac_d.facility_active_days
    solidarity_dividends_paid_units = ac_a.cumulative_solidarity_received_units + ac_c.cumulative_solidarity_received_units
    academy_cash_units = ac_a.cash_units + ac_c.cash_units
    buyer_cash_units = ac_b.cash_units + ac_d.cash_units

    standings = sorted(
        CLUB_IDS,
        key=lambda cid: (
            -world.club_accounts[cid].points,
            -(world.club_accounts[cid].goals_for - world.club_accounts[cid].goals_against),
            -world.club_accounts[cid].goals_for,
            cid,
        ),
    )

    return {
        "identity": league_identity(),
        "evaluation_version": LEAGUE_EVALUATION_VERSION,
        "seed": world.seed,
        "days": world.day,
        "condition": world.config.condition,
        "total_transfers": sum(p.transfers_count for p in world.players.values()),
        "talents_transferred_rate": round6(sum(talent_transferred) / len(talent_transferred)),
        "talents_appearances": round6(sum(talent_apps) / len(talent_apps)),
        "talents_satisfaction": round6(sum(talent_sat) / len(talent_sat)),
        "talents_skill_gain": round6(sum(talent_gains) / len(talent_gains)),
        **summarize_academy_cohorts(world),
        "legacy_four_slot_ids": list(legacy_four_slot_ids),
        "legacy_four_slot_skill_gain": round6(sum(legacy_four_slot_gains) / len(legacy_four_slot_gains)),
        "mean_skill_gain": round6(sum(all_gains) / len(all_gains)),
        "solidarity_dividends_paid_units": solidarity_dividends_paid_units,
        "academy_facility_days": academy_facility_days,
        "buyer_facility_days": buyer_facility_days,
        "academy_clubs_points": academy_points,
        "buyer_clubs_points": buyer_points,
        "buyer_minus_academy_points": buyer_points - academy_points,
        "academy_clubs_gd": academy_gd,
        "buyer_clubs_gd": buyer_gd,
        "buyer_minus_academy_gd": buyer_gd - academy_gd,
        "academy_clubs_cash_units": academy_cash_units,
        "buyer_clubs_cash_units": buyer_cash_units,
        "buyer_minus_academy_cash_units": (buyer_cash_units - academy_cash_units),
        "ledger_discrepancy_units": compute_ledger_discrepancy(world),
        "insolvency_events": world.ledger.insolvency_events,
        "blocked_circular_transfers": world.ledger.blocked_circular_transfers,
        "blocked_duplicate_claims": world.ledger.blocked_duplicate_claims,
        "actual_duplicate_claims": actual_duplicate_payments(world),
        "standings": standings,
        "event_count": len(world.events),
    }


def _snapshot_payload_unchecked(world: LeagueWorldState) -> dict[str, Any]:
    return {
        "identity": league_identity(),
        "seed": world.seed,
        "day": world.day,
        "config": world.config.to_dict(),
        "club_rosters": {k: list(v) for k, v in world.club_rosters.items()},
        "club_accounts": {k: v.to_dict() for k, v in world.club_accounts.items()},
        "players": {k: v.to_dict() for k, v in world.players.items()},
        "initial_players": {k: v.to_dict() for k, v in world.initial_players.items()},
        "player_minds": {k: v.to_dict() for k, v in world.player_minds.items()},
        "coaches": {k: v.to_dict() for k, v in world.coaches.items()},
        "ledger": world.ledger.to_dict(),
        "public_last_starters": {k: list(v) for k, v in world.public_last_starters.items()},
        "events": world.events,
        "research_branch": bool(world.interventions),
        "interventions": [asdict(record) for record in world.interventions],
    }


def _assert_same(actual: Any, expected: Any, path: str = "snapshot") -> None:
    """Exact typed comparison, with a useful first mismatch (no hash-only audit)."""
    if type(actual) is not type(expected):
        raise ValueError(f"replay mismatch at {path}: type")
    if is_dataclass(expected):
        _assert_same(vars(actual), vars(expected), path)
    elif isinstance(expected, dict):
        if actual.keys() != expected.keys():
            raise ValueError(f"replay mismatch at {path}: fields")
        for key in expected:
            _assert_same(actual[key], expected[key], f"{path}.{key}")
    elif isinstance(expected, (list, tuple)):
        if len(actual) != len(expected):
            raise ValueError(f"replay mismatch at {path}: length")
        for i, value in enumerate(expected):
            _assert_same(actual[i], value, f"{path}[{i}]")
    elif actual != expected:
        raise ValueError(f"replay mismatch at {path}: value")


def _replay_snapshot_payload(raw: dict[str, Any]) -> LeagueWorldState:
    """Reconstruct only from validated controls, then compare EVERY stored field.

    Do not coerce state values through from_dict: that could hide bool/int,
    rounding, extra-key and other corruptions. No stored mind is trusted.
    """
    if type(raw) is not dict:
        raise ValueError("snapshot root must be a dict")
    _assert_same(raw.get("identity"), league_identity(), "snapshot.identity")
    seed, day, config_data = raw.get("seed"), raw.get("day"), raw.get("config")
    if type(seed) is not int or type(day) is not int or day < 0:
        raise ValueError("snapshot seed/day must be integers; day must be nonnegative")
    if not LeagueEconomyConfig.has_save_fields(config_data):
        raise ValueError("config fields do not match save17 schema")
    config = LeagueEconomyConfig(**config_data)
    records = raw.get("interventions")
    if type(records) is not list or type(raw.get("research_branch")) is not bool:
        raise ValueError("invalid research branch metadata")
    replayed = _build_initial_world(seed=seed, config=config)
    for index, data in enumerate(records, start=1):
        record = _record_from_payload(data)
        if record.sequence != index or record.day < replayed.day or record.day > day:
            raise ValueError("intervention sequence/day is out of order or in the future")
        advance_league_days(replayed, record.day - replayed.day)
        expected = _prepare_intervention(
            replayed, kind=record.kind, player_id=record.player_id,
            coach_id=record.coach_id, value=record.after_value, reason=record.reason,
        )
        _assert_same(record, expected, f"interventions[{index - 1}]")
        _apply_intervention(replayed, expected)
    advance_league_days(replayed, day - replayed.day)
    _validate_league_invariants(replayed)
    _assert_same(raw, _snapshot_payload_unchecked(replayed))
    return replayed


def _intervention_arguments(*, kind: Any, player_id: Any, coach_id: Any,
                            value: Any, reason: Any) -> float:
    if type(kind) is not str or kind not in ("coach_skill_estimate", "player_trust"):
        raise ValueError("unknown intervention kind")
    if type(player_id) is not str or player_id not in PLAYER_IDS:
        raise ValueError("invalid intervention player_id")
    if type(coach_id) is not str or coach_id not in COACH_IDS:
        raise ValueError("invalid intervention coach_id")
    if (type(reason) is not str or not 1 <= len(reason) <= 1000
            or reason != reason.strip() or not reason.isprintable()):
        raise ValueError("reason must be 1..1000 printable characters without surrounding whitespace")
    if type(value) not in (int, float):
        raise ValueError("intervention value must be a finite number, not bool")
    low, high = (0.10, 0.98) if kind == "coach_skill_estimate" else (0.05, 0.95)
    if not low <= value <= high or not isfinite(value) or round6(value) != value:
        raise ValueError("intervention value is outside bounds or exceeds six decimal places")
    return float(value)


def _record_from_payload(data: Any) -> ResearchIntervention:
    if type(data) is not dict or set(data) != {f.name for f in fields(ResearchIntervention)}:
        raise ValueError("invalid intervention record fields")
    _intervention_arguments(kind=data["kind"], player_id=data["player_id"],
                            coach_id=data["coach_id"], value=data["after_value"], reason=data["reason"])
    if (type(data["sequence"]) is not int or data["sequence"] < 1
            or type(data["day"]) is not int or data["day"] < 0
            or type(data["phase"]) is not str or data["phase"] != "day_boundary"):
        raise ValueError("invalid intervention sequence/day/phase")
    observer = data["coach_id"] if data["kind"] == "coach_skill_estimate" else data["player_id"]
    if type(data["observer_id"]) is not str or data["observer_id"] != observer:
        raise ValueError("invalid intervention observer")
    for key in ("before_value", "after_value"):
        if type(data[key]) is not float:
            raise ValueError(f"intervention {key} must have float type")
        _intervention_arguments(kind=data["kind"], player_id=data["player_id"],
                                coach_id=data["coach_id"], value=data[key], reason=data["reason"])
    parent_hash = data["parent_checkpoint_sha256"]
    if (type(parent_hash) is not str or len(parent_hash) != 64
            or any(ch not in "0123456789abcdef" for ch in parent_hash)):
        raise ValueError("invalid intervention parent checkpoint hash")
    if type(data["previous_obs_ref"]) is not str or not data["previous_obs_ref"]:
        raise ValueError("invalid intervention previous observation reference")
    return ResearchIntervention(**data)


def _prepare_intervention(world: LeagueWorldState, *, kind: str, player_id: str,
                          coach_id: str, value: float, reason: str) -> ResearchIntervention:
    value = _intervention_arguments(kind=kind, player_id=player_id, coach_id=coach_id,
                                    value=value, reason=reason)
    if kind == "coach_skill_estimate":
        mind = world.coaches[coach_id].evaluations[player_id]
        before = mind.skill_estimate
        observer = coach_id
    else:
        mind = world.player_minds[player_id]
        before = mind.trust_by_coach[coach_id]
        observer = player_id
    return ResearchIntervention(
        sequence=len(world.interventions) + 1, day=world.day, phase="day_boundary",
        kind=kind, player_id=player_id, coach_id=coach_id, observer_id=observer,
        before_value=before, after_value=value, reason=reason,
        parent_checkpoint_sha256=digest(_snapshot_payload_unchecked(world)),
        previous_obs_ref=mind.last_obs_ref,
    )


def _apply_intervention(world: LeagueWorldState, record: ResearchIntervention) -> None:
    event_ref = _append_event(
        world, day=record.day, kind="research_intervention", actor="researcher",
        data=asdict(record), cause_refs=[record.previous_obs_ref],
    )
    if record.kind == "coach_skill_estimate":
        mind = world.coaches[record.coach_id].evaluations[record.player_id]
        mind.skill_estimate = record.after_value
    else:
        mind = world.player_minds[record.player_id]
        mind.trust_by_coach[record.coach_id] = record.after_value
    mind.last_obs_ref = event_ref
    world.interventions += (record,)


def fork_league_world(world: LeagueWorldState, *, kind: str, player_id: str,
                      coach_id: str, value: float, reason: str) -> LeagueWorldState:
    """Validate and clone a checkpoint, then record exactly one research B edit.

    The parent is untouched, including its random keys and complete history.
    The operation does not refresh last_seen_day or imply new experience.
    """
    validate_league_world(world)
    record = _prepare_intervention(world, kind=kind, player_id=player_id,
                                   coach_id=coach_id, value=value, reason=reason)
    child = deepcopy(world)
    _apply_intervention(child, record)
    return child


def validate_league_world(world: LeagueWorldState) -> None:
    """Certify all state and history; cost grows with elapsed simulation days.

    Only the two recorded B interventions are reconstructed; arbitrary patches
    cannot be persisted under this contract.
    Replay tests consistency with these rules, not the validity of the rules.
    """
    if type(world) is not LeagueWorldState:
        raise ValueError("expected LeagueWorldState")
    if type(world.interventions) is not tuple:
        raise ValueError("interventions must be an immutable tuple")
    for record in world.interventions:
        if type(record) is not ResearchIntervention:
            raise ValueError("invalid intervention record type")
        _record_from_payload(asdict(record))
    _validate_league_invariants(world)
    replayed = _replay_snapshot_payload(_snapshot_payload_unchecked(world))
    # Also reject changes below serialization precision or internal type changes.
    _assert_same(world, replayed, "world")


def snapshot_payload(world: LeagueWorldState) -> dict[str, Any]:
    validate_league_world(world)
    return _snapshot_payload_unchecked(world)


def save_league_artifacts(world: LeagueWorldState, out_dir: str | Path) -> dict[str, Any]:
    # Complete validation before creating or replacing any artifact.
    snap = snapshot_payload(world)
    provenance = source_provenance()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    snap_hash = digest(snap)
    write_json(out / "snapshot.json", snap)

    atomic_write(out / "events.jsonl", [canonical(ev) + b"\n" for ev in world.events])

    summary = summarize_league_world(world)
    summary["snapshot_sha256"] = snap_hash
    write_json(out / "summary.json", summary)

    manifest = {
        "identity": league_identity(),
        "source_provenance": provenance,
        "evaluation_version": LEAGUE_EVALUATION_VERSION,
        "seed": world.seed,
        "days": world.day,
        "condition": world.config.condition,
        "config": world.config.to_dict(),
        "snapshot_sha256": snap_hash,
        "event_count": len(world.events),
        "runtime": runtime(),
        "research_branch": bool(world.interventions),
        "interventions": [asdict(record) for record in world.interventions],
    }
    write_json(out / "manifest.json", manifest)
    return summary


def _read_snapshot_json(path: str | Path) -> Any:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    return json.loads(
        Path(path).read_text(encoding="utf-8"), parse_float=parse_float,
        parse_constant=reject_nonfinite, object_pairs_hook=unique_object,
    )


def load_league_snapshot(path: str | Path) -> LeagueWorldState:
    return _replay_snapshot_payload(_read_snapshot_json(path))


def load_league_snapshot_trusted(path: str | Path) -> LeagueWorldState:
    """T022: restore state directly, without replaying history.

    Accepts only a snapshot whose digest equals the snapshot_sha256 that a full-replay save
    wrote to the sibling manifest.json, under the current identity. This is an integrity
    check, not a signature: a snapshot AND manifest forged together can hide a history that
    violates the rules. Use load_league_snapshot when that matters.
    """
    raw = _read_snapshot_json(path)
    if type(raw) is not dict:
        raise ValueError("snapshot root must be a dict")
    manifest = _read_snapshot_json(Path(path).with_name("manifest.json"))
    if type(manifest) is not dict or manifest.get("snapshot_sha256") != digest(raw):
        raise ValueError("snapshot does not match its manifest snapshot_sha256")
    _assert_same(raw.get("identity"), league_identity(), "snapshot.identity")
    _assert_same(manifest.get("identity"), league_identity(), "manifest.identity")
    try:
        if not LeagueEconomyConfig.has_save_fields(raw["config"]) or type(raw["events"]) is not list:
            raise ValueError("config fields do not match save17 schema")
        world = LeagueWorldState(
            seed=raw["seed"], day=raw["day"], config=LeagueEconomyConfig(**raw["config"]),
            club_rosters={k: list(v) for k, v in raw["club_rosters"].items()},
            club_accounts={k: ClubAccountState.from_dict(v) for k, v in raw["club_accounts"].items()},
            players={k: PlayerTruth.from_dict(v) for k, v in raw["players"].items()},
            initial_players={k: PlayerTruth.from_dict(v) for k, v in raw["initial_players"].items()},
            player_minds={k: PlayerMind.from_dict(v) for k, v in raw["player_minds"].items()},
            coaches={k: CoachMind.from_dict(v) for k, v in raw["coaches"].items()},
            ledger=WorldFinanceLedger.from_dict(raw["ledger"]),
            public_last_starters={k: list(v) for k, v in raw["public_last_starters"].items()},
            events=raw["events"],
            interventions=tuple(_record_from_payload(r) for r in raw["interventions"]),
        )
        _validate_league_invariants(world)
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError(f"trusted restore failed: {error!r}") from error
    # Re-serializing must reproduce the file exactly (types, extra keys, rounding). Events are the file's own list.
    _assert_same({k: v for k, v in raw.items() if k != "events"},
                 {k: v for k, v in _snapshot_payload_unchecked(world).items() if k != "events"})
    return world
