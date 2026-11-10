from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
from pathlib import Path
from typing import Any

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


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def round6(value: float) -> float:
    return round(float(value), 6)


def uniform_from_key(
    seed: int,
    tick: int,
    channel: str,
    actor_token: str,
    *,
    low: float = -1.0,
    high: float = 1.0,
) -> float:
    encoded = json.dumps(
        (RNG_VERSION, seed, channel, tick, actor_token, 0),
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).digest()
    u = (int.from_bytes(digest[:8], "big") >> 11) / (1 << 53)
    return low + (high - low) * u

# Author attribution:
# T008 (Minimal 2D Match Core) was designed and implemented by Antigravity (Gemini 4 Argon),
# a distinct author from the creator of T001-T007 (Sol 6.1 High).
MATCH_AUTHOR = "Antigravity (Gemini 4 Argon)"
MATCH_WORLD_KIND = "match_2d"
MATCH_SAVE_VERSION = 8
MATCH_RULE_VERSION = "t008-match2d-v1"
MATCH_POLICY_VERSION = "t008-match2d-score-v1"

PITCH_HALF_LENGTH = 10.0
PITCH_HALF_WIDTH = 6.0
GOAL_HALF_WIDTH = 1.8

CONTROL_RADIUS = 0.85
TACKLE_RADIUS = 1.35
DEFAULT_VISION_RADIUS = 6.5

BALL_DECAY = 0.72
BASE_MOVE_SPEED = 0.58
PACE_MOVE_SCALE = 0.36

BASE_PASS_SPEED = 2.15
PASS_SPEED_SCALE = 0.85
PASS_ANGLE_NOISE_SCALE = 0.18

BASE_SHOOT_SPEED = 2.45
SHOOT_SPEED_SCALE = 1.00
SHOOT_Y_NOISE_SCALE = 0.80

OBS_POSITION_NOISE_SCALE = 0.14

MATCH_TEAMS = ("home", "away")
MATCH_ACTIONS = ("move", "pass", "shoot", "tackle", "hold")
DIRECTIVE_MODES = ("balanced", "press", "always_shoot", "ball_camp_wait")
OBSERVATION_MODES = ("local", "oracle")

T008_MAIN_CONDITIONS = (
    "baseline",
    "home_skill_plus",
    "home_position_plus",
    "home_pass_style",
)
T008_EXTRA_CONDITIONS = (
    "home_directive_press",
    "home_oracle_view",
    "home_always_shoot",
    "home_ball_camp_wait",
)
T008_ALL_CONDITIONS = T008_MAIN_CONDITIONS + T008_EXTRA_CONDITIONS


def team_attack_sign(team: str) -> float:
    if team == "home":
        return 1.0
    if team == "away":
        return -1.0
    raise ValueError(f"unknown team: {team}")


def opposite_team(team: str) -> str:
    if team == "home":
        return "away"
    if team == "away":
        return "home"
    raise ValueError(f"unknown team: {team}")


@dataclass(frozen=True)
class MatchTraits:
    pace: float
    passing: float
    shooting: float

    def to_dict(self) -> dict[str, float]:
        return {
            "pace": round6(self.pace),
            "passing": round6(self.passing),
            "shooting": round6(self.shooting),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MatchTraits:
        return cls(
            pace=float(data["pace"]),
            passing=float(data["passing"]),
            shooting=float(data["shooting"]),
        )


@dataclass(frozen=True)
class MatchStyle:
    directness: float
    pass_bias: float
    press_bias: float

    def to_dict(self) -> dict[str, float]:
        return {
            "directness": round6(self.directness),
            "pass_bias": round6(self.pass_bias),
            "press_bias": round6(self.press_bias),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MatchStyle:
        return cls(
            directness=float(data["directness"]),
            pass_bias=float(data["pass_bias"]),
            press_bias=float(data["press_bias"]),
        )


@dataclass
class PlayerMatchMind:
    player_id: str
    team: str
    style: MatchStyle
    coach_directive: str = "balanced"

    def to_dict(self) -> dict[str, Any]:
        return {
            "player_id": self.player_id,
            "team": self.team,
            "style": self.style.to_dict(),
            "coach_directive": self.coach_directive,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlayerMatchMind:
        return cls(
            player_id=str(data["player_id"]),
            team=str(data["team"]),
            style=MatchStyle.from_dict(dict(data["style"])),
            coach_directive=str(data.get("coach_directive", "balanced")),
        )


@dataclass
class PlayerMatchState:
    player_id: str
    team: str
    slot: int
    x: float
    y: float
    traits: MatchTraits
    mind: PlayerMatchMind

    def to_dict(self) -> dict[str, Any]:
        return {
            "player_id": self.player_id,
            "team": self.team,
            "slot": self.slot,
            "x": round6(self.x),
            "y": round6(self.y),
            "traits": self.traits.to_dict(),
            "mind": self.mind.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlayerMatchState:
        return cls(
            player_id=str(data["player_id"]),
            team=str(data["team"]),
            slot=int(data["slot"]),
            x=float(data["x"]),
            y=float(data["y"]),
            traits=MatchTraits.from_dict(dict(data["traits"])),
            mind=PlayerMatchMind.from_dict(dict(data["mind"])),
        )


@dataclass
class BallState:
    x: float
    y: float
    vx: float = 0.0
    vy: float = 0.0
    owner_id: str | None = None
    last_touch_id: str | None = None
    last_touch_team: str | None = None
    last_kick_kind: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "x": round6(self.x),
            "y": round6(self.y),
            "vx": round6(self.vx),
            "vy": round6(self.vy),
            "owner_id": self.owner_id,
            "last_touch_id": self.last_touch_id,
            "last_touch_team": self.last_touch_team,
            "last_kick_kind": self.last_kick_kind,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BallState:
        return cls(
            x=float(data["x"]),
            y=float(data["y"]),
            vx=float(data.get("vx", 0.0)),
            vy=float(data.get("vy", 0.0)),
            owner_id=str(data["owner_id"]) if data.get("owner_id") is not None else None,
            last_touch_id=str(data["last_touch_id"]) if data.get("last_touch_id") is not None else None,
            last_touch_team=str(data["last_touch_team"]) if data.get("last_touch_team") is not None else None,
            last_kick_kind=str(data["last_kick_kind"]) if data.get("last_kick_kind") is not None else None,
        )


@dataclass(frozen=True)
class PerceivedEntity:
    player_id: str
    team_relation: str
    slot: int
    visible: bool
    position: tuple[float, float] | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "player_id": self.player_id,
            "team_relation": self.team_relation,
            "slot": self.slot,
            "visible": self.visible,
            "position": [round6(self.position[0]), round6(self.position[1])]
            if self.position is not None
            else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PerceivedEntity:
        raw_pos = data.get("position")
        pos = (float(raw_pos[0]), float(raw_pos[1])) if raw_pos is not None else None
        return cls(
            player_id=str(data["player_id"]),
            team_relation=str(data["team_relation"]),
            slot=int(data["slot"]),
            visible=bool(data["visible"]),
            position=pos,
        )


@dataclass(frozen=True)
class LocalMatchObservation:
    tick: int
    player_id: str
    team: str
    slot: int
    observation_mode: str
    vision_radius: float
    self_position: tuple[float, float]
    ball_position: tuple[float, float]
    ball_velocity: tuple[float, float]
    owns_ball: bool
    ball_owner_relation: str
    ball_owner_id: str | None
    teammates: tuple[PerceivedEntity, ...]
    opponents: tuple[PerceivedEntity, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "tick": self.tick,
            "player_id": self.player_id,
            "team": self.team,
            "slot": self.slot,
            "observation_mode": self.observation_mode,
            "vision_radius": round6(self.vision_radius),
            "self_position": [round6(self.self_position[0]), round6(self.self_position[1])],
            "ball_position": [round6(self.ball_position[0]), round6(self.ball_position[1])],
            "ball_velocity": [round6(self.ball_velocity[0]), round6(self.ball_velocity[1])],
            "owns_ball": self.owns_ball,
            "ball_owner_relation": self.ball_owner_relation,
            "ball_owner_id": self.ball_owner_id,
            "teammates": [item.to_dict() for item in self.teammates],
            "opponents": [item.to_dict() for item in self.opponents],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LocalMatchObservation:
        sp = data["self_position"]
        bp = data["ball_position"]
        bv = data["ball_velocity"]
        return cls(
            tick=int(data["tick"]),
            player_id=str(data["player_id"]),
            team=str(data["team"]),
            slot=int(data["slot"]),
            observation_mode=str(data["observation_mode"]),
            vision_radius=float(data["vision_radius"]),
            self_position=(float(sp[0]), float(sp[1])),
            ball_position=(float(bp[0]), float(bp[1])),
            ball_velocity=(float(bv[0]), float(bv[1])),
            owns_ball=bool(data["owns_ball"]),
            ball_owner_relation=str(data["ball_owner_relation"]),
            ball_owner_id=str(data["ball_owner_id"]) if data.get("ball_owner_id") is not None else None,
            teammates=tuple(PerceivedEntity.from_dict(dict(item)) for item in data["teammates"]),
            opponents=tuple(PerceivedEntity.from_dict(dict(item)) for item in data["opponents"]),
        )


@dataclass(frozen=True)
class MatchIntent:
    kind: str
    direction_rel: tuple[float, float] = (0.0, 0.0)
    target_player_id: str | None = None
    target_rel: tuple[float, float] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "direction_rel": [round6(self.direction_rel[0]), round6(self.direction_rel[1])],
            "target_player_id": self.target_player_id,
            "target_rel": [round6(self.target_rel[0]), round6(self.target_rel[1])]
            if self.target_rel is not None
            else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MatchIntent:
        dr = data.get("direction_rel", [0.0, 0.0])
        tr = data.get("target_rel")
        return cls(
            kind=str(data["kind"]),
            direction_rel=(float(dr[0]), float(dr[1])),
            target_player_id=str(data["target_player_id"])
            if data.get("target_player_id") is not None
            else None,
            target_rel=(float(tr[0]), float(tr[1])) if tr is not None else None,
        )


@dataclass(frozen=True)
class MatchDecision:
    intent: MatchIntent
    scores: dict[str, float]
    explanation: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent.to_dict(),
            "scores": {k: round6(v) for k, v in self.scores.items()},
            "explanation": self.explanation,
        }


@dataclass(frozen=True)
class MatchConfig:
    condition: str = "baseline"
    home_observation_mode: str = "local"
    away_observation_mode: str = "local"
    vision_radius: float = DEFAULT_VISION_RADIUS
    noise_scale: float = 1.0
    mirror_rng_by_slot: bool = False
    policy_version: str = MATCH_POLICY_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition": self.condition,
            "home_observation_mode": self.home_observation_mode,
            "away_observation_mode": self.away_observation_mode,
            "vision_radius": round6(self.vision_radius),
            "noise_scale": round6(self.noise_scale),
            "mirror_rng_by_slot": self.mirror_rng_by_slot,
            "policy_version": self.policy_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MatchConfig:
        required = {
            "condition",
            "home_observation_mode",
            "away_observation_mode",
            "vision_radius",
            "noise_scale",
            "policy_version",
        }
        missing = required - set(data.keys())
        if missing:
            raise ValueError(f"missing match config keys: {sorted(missing)}")
        return cls(
            condition=str(data["condition"]),
            home_observation_mode=str(data["home_observation_mode"]),
            away_observation_mode=str(data["away_observation_mode"]),
            vision_radius=float(data["vision_radius"]),
            noise_scale=float(data["noise_scale"]),
            mirror_rng_by_slot=bool(data.get("mirror_rng_by_slot", False)),
            policy_version=str(data["policy_version"]),
        )


@dataclass
class MatchWorldState:
    seed: int
    tick: int
    config: MatchConfig
    players: dict[str, PlayerMatchState]
    ball: BallState
    score: dict[str, int]
    kickoff_positions: dict[str, tuple[float, float]]
    events: list[dict[str, Any]] = field(default_factory=list)

    def record(self, kind: str, **payload: Any) -> None:
        self.events.append({"tick": self.tick, "kind": kind, **payload})

    def to_dict(self, include_events: bool = True) -> dict[str, Any]:
        data: dict[str, Any] = {
            "world_kind": MATCH_WORLD_KIND,
            "save_version": MATCH_SAVE_VERSION,
            "rule_version": MATCH_RULE_VERSION,
            "author": MATCH_AUTHOR,
            "seed": self.seed,
            "tick": self.tick,
            "config": self.config.to_dict(),
            "players": {pid: player.to_dict() for pid, player in self.players.items()},
            "ball": self.ball.to_dict(),
            "score": {team: int(val) for team, val in self.score.items()},
            "kickoff_positions": {
                pid: [round6(pos[0]), round6(pos[1])]
                for pid, pos in self.kickoff_positions.items()
            },
        }
        if include_events:
            data["events"] = list(self.events)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MatchWorldState:
        if data.get("world_kind") != MATCH_WORLD_KIND:
            raise ValueError(f"unexpected world_kind: {data.get('world_kind')}")
        if data.get("save_version") != MATCH_SAVE_VERSION:
            raise ValueError(f"unsupported match save_version: {data.get('save_version')}")
        if data.get("rule_version") != MATCH_RULE_VERSION:
            raise ValueError(f"unsupported match rule_version: {data.get('rule_version')}")
        config = MatchConfig.from_dict(dict(data["config"]))
        players = {
            pid: PlayerMatchState.from_dict(dict(pdata))
            for pid, pdata in data["players"].items()
        }
        ball = BallState.from_dict(dict(data["ball"]))
        score = {str(k): int(v) for k, v in data["score"].items()}
        kickoff_positions = {
            str(pid): (float(pos[0]), float(pos[1]))
            for pid, pos in data["kickoff_positions"].items()
        }
        events = [dict(ev) for ev in data.get("events", [])]
        world = cls(
            seed=int(data["seed"]),
            tick=int(data["tick"]),
            config=config,
            players=players,
            ball=ball,
            score=score,
            kickoff_positions=kickoff_positions,
            events=events,
        )
        validate_match_world(world)
        return world


def normalize_condition_name(condition: str) -> str:
    return condition.removeprefix("match_")


def match_config_for_condition(
    condition: str,
    *,
    noise_scale: float = 1.0,
    mirror_rng_by_slot: bool = False,
) -> MatchConfig:
    norm = normalize_condition_name(condition)
    if norm not in T008_ALL_CONDITIONS:
        raise ValueError(f"unknown T008 condition: {condition}")
    home_obs = "oracle" if norm == "home_oracle_view" else "local"
    return MatchConfig(
        condition=norm,
        home_observation_mode=home_obs,
        away_observation_mode="local",
        vision_radius=DEFAULT_VISION_RADIUS,
        noise_scale=noise_scale,
        mirror_rng_by_slot=mirror_rng_by_slot,
        policy_version=MATCH_POLICY_VERSION,
    )


def create_match_world(
    seed: int = 0,
    condition: str = "baseline",
    *,
    config: MatchConfig | None = None,
) -> MatchWorldState:
    cfg = config or match_config_for_condition(condition)
    if cfg.condition not in T008_ALL_CONDITIONS:
        raise ValueError(f"unknown T008 condition: {cfg.condition}")

    # Baseline symmetric positions, traits, and styles
    home_positions = {
        "h1": (-2.2, -1.2),
        "h2": (-3.8, 1.4),
    }
    away_positions = {
        "a1": (2.2, 1.2),
        "a2": (3.8, -1.4),
    }
    home_traits = {
        "h1": MatchTraits(pace=0.60, passing=0.60, shooting=0.60),
        "h2": MatchTraits(pace=0.55, passing=0.65, shooting=0.55),
    }
    away_traits = {
        "a1": MatchTraits(pace=0.60, passing=0.60, shooting=0.60),
        "a2": MatchTraits(pace=0.55, passing=0.65, shooting=0.55),
    }
    home_styles = {
        "h1": MatchStyle(directness=0.55, pass_bias=0.50, press_bias=0.50),
        "h2": MatchStyle(directness=0.45, pass_bias=0.65, press_bias=0.55),
    }
    away_styles = {
        "a1": MatchStyle(directness=0.55, pass_bias=0.50, press_bias=0.50),
        "a2": MatchStyle(directness=0.45, pass_bias=0.65, press_bias=0.55),
    }
    home_directive = "balanced"
    away_directive = "balanced"

    # Single-factor interventions
    if cfg.condition == "home_skill_plus":
        home_traits = {
            "h1": MatchTraits(pace=0.82, passing=0.82, shooting=0.82),
            "h2": MatchTraits(pace=0.77, passing=0.87, shooting=0.77),
        }
    elif cfg.condition == "home_position_plus":
        home_positions = {
            "h1": (-0.4, -1.2),
            "h2": (-2.0, 1.4),
        }
    elif cfg.condition == "home_pass_style":
        home_styles = {
            "h1": MatchStyle(directness=0.25, pass_bias=0.88, press_bias=0.50),
            "h2": MatchStyle(directness=0.20, pass_bias=0.92, press_bias=0.55),
        }
    elif cfg.condition == "home_directive_press":
        home_directive = "press"
    elif cfg.condition == "home_always_shoot":
        home_directive = "always_shoot"
    elif cfg.condition == "home_ball_camp_wait":
        home_directive = "ball_camp_wait"

    players = {
        "h1": PlayerMatchState(
            player_id="h1",
            team="home",
            slot=1,
            x=home_positions["h1"][0],
            y=home_positions["h1"][1],
            traits=home_traits["h1"],
            mind=PlayerMatchMind("h1", "home", home_styles["h1"], home_directive),
        ),
        "h2": PlayerMatchState(
            player_id="h2",
            team="home",
            slot=2,
            x=home_positions["h2"][0],
            y=home_positions["h2"][1],
            traits=home_traits["h2"],
            mind=PlayerMatchMind("h2", "home", home_styles["h2"], home_directive),
        ),
        "a1": PlayerMatchState(
            player_id="a1",
            team="away",
            slot=1,
            x=away_positions["a1"][0],
            y=away_positions["a1"][1],
            traits=away_traits["a1"],
            mind=PlayerMatchMind("a1", "away", away_styles["a1"], away_directive),
        ),
        "a2": PlayerMatchState(
            player_id="a2",
            team="away",
            slot=2,
            x=away_positions["a2"][0],
            y=away_positions["a2"][1],
            traits=away_traits["a2"],
            mind=PlayerMatchMind("a2", "away", away_styles["a2"], away_directive),
        ),
    }
    kickoff_positions = {
        pid: (player.x, player.y) for pid, player in players.items()
    }
    world = MatchWorldState(
        seed=seed,
        tick=0,
        config=cfg,
        players=players,
        ball=BallState(x=0.0, y=0.0, vx=0.0, vy=0.0, owner_id=None),
        score={"home": 0, "away": 0},
        kickoff_positions=kickoff_positions,
    )
    validate_match_world(world)
    return world


def _rng_actor_token(world: MatchWorldState, player: PlayerMatchState) -> str:
    if world.config.mirror_rng_by_slot:
        return f"slot{player.slot}"
    return player.player_id


def _scaled_uniform(
    world: MatchWorldState,
    channel: str,
    actor_token: str,
    low: float,
    high: float,
) -> float:
    if world.config.noise_scale == 0.0:
        return 0.0
    raw = uniform_from_key(
        world.seed,
        world.tick,
        channel,
        actor_token,
        low=low,
        high=high,
    )
    return round6(raw * world.config.noise_scale)


def observe_match_player(
    world: MatchWorldState,
    player_id: str,
) -> LocalMatchObservation:
    observer = world.players[player_id]
    sign = team_attack_sign(observer.team)
    obs_mode = (
        world.config.home_observation_mode
        if observer.team == "home"
        else world.config.away_observation_mode
    )
    if obs_mode not in OBSERVATION_MODES:
        raise ValueError(f"invalid observation_mode: {obs_mode}")

    self_rel = (round6(sign * observer.x), round6(sign * observer.y))

    # Ball position and velocity in observer's team-relative frame
    actor_token = _rng_actor_token(world, observer)
    if obs_mode == "oracle" or observer.player_id == world.ball.owner_id:
        ball_noise_x = 0.0
        ball_noise_y = 0.0
    else:
        ball_dist = math.hypot(world.ball.x - observer.x, world.ball.y - observer.y)
        dist_scale = clamp(ball_dist / max(1.0, world.config.vision_radius), 0.0, 1.5)
        amp = OBS_POSITION_NOISE_SCALE * dist_scale
        ball_noise_x = _scaled_uniform(world, "obs_ball_x", actor_token, -amp, amp)
        ball_noise_y = _scaled_uniform(world, "obs_ball_y", actor_token, -amp, amp)

    ball_rel = (
        round6(clamp(sign * world.ball.x + ball_noise_x, -PITCH_HALF_LENGTH, PITCH_HALF_LENGTH)),
        round6(clamp(sign * world.ball.y + ball_noise_y, -PITCH_HALF_WIDTH, PITCH_HALF_WIDTH)),
    )
    ball_vel_rel = (
        round6(sign * world.ball.vx),
        round6(sign * world.ball.vy),
    )

    if world.ball.owner_id is None:
        owner_relation = "none"
    elif world.ball.owner_id == observer.player_id:
        owner_relation = "self"
    elif world.players[world.ball.owner_id].team == observer.team:
        owner_relation = "teammate"
    else:
        owner_relation = "opponent"

    teammates: list[PerceivedEntity] = []
    opponents: list[PerceivedEntity] = []

    # Sort by slot so ordering never depends on dict iteration order or raw ID strings
    other_players = sorted(
        (p for p in world.players.values() if p.player_id != observer.player_id),
        key=lambda p: (0 if p.team == observer.team else 1, p.slot),
    )
    for other in other_players:
        relation = "teammate" if other.team == observer.team else "opponent"
        dist = math.hypot(other.x - observer.x, other.y - observer.y)
        visible = True if obs_mode == "oracle" else (dist <= world.config.vision_radius)
        if visible:
            if obs_mode == "oracle":
                nx = 0.0
                ny = 0.0
            else:
                amp = OBS_POSITION_NOISE_SCALE * clamp(
                    dist / max(1.0, world.config.vision_radius), 0.0, 1.0
                )
                target_token = f"{actor_token}:{relation}{other.slot}"
                nx = _scaled_uniform(world, "obs_pos_x", target_token, -amp, amp)
                ny = _scaled_uniform(world, "obs_pos_y", target_token, -amp, amp)
            rel_pos: tuple[float, float] | None = (
                round6(clamp(sign * other.x + nx, -PITCH_HALF_LENGTH, PITCH_HALF_LENGTH)),
                round6(clamp(sign * other.y + ny, -PITCH_HALF_WIDTH, PITCH_HALF_WIDTH)),
            )
        else:
            rel_pos = None

        entity = PerceivedEntity(
            player_id=other.player_id,
            team_relation=relation,
            slot=other.slot,
            visible=visible,
            position=rel_pos,
        )
        if relation == "teammate":
            teammates.append(entity)
        else:
            opponents.append(entity)

    return LocalMatchObservation(
        tick=world.tick,
        player_id=observer.player_id,
        team=observer.team,
        slot=observer.slot,
        observation_mode=obs_mode,
        vision_radius=world.config.vision_radius,
        self_position=self_rel,
        ball_position=ball_rel,
        ball_velocity=ball_vel_rel,
        owns_ball=(world.ball.owner_id == observer.player_id),
        ball_owner_relation=owner_relation,
        ball_owner_id=world.ball.owner_id,
        teammates=tuple(teammates),
        opponents=tuple(opponents),
    )


def _unit_direction(dx: float, dy: float) -> tuple[float, float]:
    norm = math.hypot(dx, dy)
    if norm <= 1e-9:
        return (0.0, 0.0)
    return (round6(dx / norm), round6(dy / norm))


def _segment_clearance(
    ax: float,
    ay: float,
    bx: float,
    by: float,
    opponents: list[PerceivedEntity],
) -> float:
    seg_dx = bx - ax
    seg_dy = by - ay
    seg_len_sq = seg_dx * seg_dx + seg_dy * seg_dy
    if seg_len_sq <= 1e-9:
        return 9.0
    min_clear = 9.0
    for opp in opponents:
        if not opp.visible or opp.position is None:
            continue
        ox, oy = opp.position
        t = ((ox - ax) * seg_dx + (oy - ay) * seg_dy) / seg_len_sq
        if 0.05 <= t <= 0.98:
            proj_x = ax + t * seg_dx
            proj_y = ay + t * seg_dy
            dist = math.hypot(ox - proj_x, oy - proj_y)
            if dist < min_clear:
                min_clear = dist
    return round6(min_clear)


def decide_match_player(
    observation: LocalMatchObservation,
    mind: PlayerMatchMind,
) -> MatchDecision:
    """Pure individual policy function.

    Receives ONLY the player's LocalMatchObservation and their own PlayerMatchMind.
    Never accesses MatchWorldState, opponent MatchTraits, opponent minds, or unseen positions.
    """
    if mind.coach_directive not in DIRECTIVE_MODES:
        raise ValueError(f"unsupported coach_directive: {mind.coach_directive}")

    px, py = observation.self_position
    bx, by = observation.ball_position
    dist_ball = math.hypot(bx - px, by - py)
    dist_goal = math.hypot(PITCH_HALF_LENGTH - px, 0.0 - py)

    visible_opponents = [opp for opp in observation.opponents if opp.visible and opp.position is not None]
    if visible_opponents:
        nearest_opp_dist = min(
            math.hypot(opp.position[0] - px, opp.position[1] - py)  # type: ignore[index]
            for opp in visible_opponents
        )
    else:
        nearest_opp_dist = observation.vision_radius

    visible_teammates = [tm for tm in observation.teammates if tm.visible and tm.position is not None]
    if visible_teammates:
        tm_entity = visible_teammates[0]
        tm_pos = tm_entity.position  # type: ignore[assignment]
        tm_id: str | None = tm_entity.player_id
        tm_dist_ball = math.hypot(tm_pos[0] - bx, tm_pos[1] - by)
    else:
        default_tm_id = observation.teammates[0].player_id if observation.teammates else None
        tm_id = default_tm_id
        tm_pos = (
            round6(clamp(px - 1.2, -PITCH_HALF_LENGTH + 1.0, PITCH_HALF_LENGTH - 1.0)),
            round6(1.5 if py <= 0.0 else -1.5),
        )
        tm_dist_ball = 99.0

    # Degenerate control 1: always_shoot
    if mind.coach_directive == "always_shoot":
        if observation.owns_ball:
            scores = {
                "move": round6(0.20),
                "pass": round6(0.10),
                "shoot": round6(1.50),
                "tackle": round6(-1.0),
                "hold": round6(0.05),
            }
            goal_dir = _unit_direction(PITCH_HALF_LENGTH - px, 0.0 - py)
            intent = MatchIntent(
                kind="shoot",
                direction_rel=goal_dir,
                target_player_id=None,
                target_rel=(PITCH_HALF_LENGTH, 0.0),
            )
        else:
            chase_dir = _unit_direction(bx - px, by - py)
            scores = {
                "move": round6(1.20),
                "pass": round6(-1.0),
                "shoot": round6(-1.0),
                "tackle": round6(-0.5),
                "hold": round6(0.10),
            }
            intent = MatchIntent(
                kind="move",
                direction_rel=chase_dir,
                target_player_id=None,
                target_rel=(bx, by),
            )
        return MatchDecision(
            intent=intent,
            scores=scores,
            explanation={
                "directive": mind.coach_directive,
                "owns_ball": observation.owns_ball,
                "dist_ball": round6(dist_ball),
                "dist_goal": round6(dist_goal),
                "nearest_opp_dist": round6(nearest_opp_dist),
            },
        )

    # Degenerate control 2: ball_camp_wait
    if mind.coach_directive == "ball_camp_wait":
        if observation.owns_ball or dist_ball <= 1.25:
            scores = {
                "move": round6(0.15),
                "pass": round6(-0.5 if observation.owns_ball else -1.0),
                "shoot": round6(-0.5 if observation.owns_ball else -1.0),
                "tackle": round6(-0.5),
                "hold": round6(1.50),
            }
            intent = MatchIntent(kind="hold", direction_rel=(0.0, 0.0))
        else:
            chase_dir = _unit_direction(bx - px, by - py)
            scores = {
                "move": round6(0.95),
                "pass": round6(-1.0),
                "shoot": round6(-1.0),
                "tackle": round6(-1.0),
                "hold": round6(0.40),
            }
            intent = MatchIntent(
                kind="move",
                direction_rel=chase_dir,
                target_player_id=None,
                target_rel=(bx, by),
            )
        return MatchDecision(
            intent=intent,
            scores=scores,
            explanation={
                "directive": mind.coach_directive,
                "owns_ball": observation.owns_ball,
                "dist_ball": round6(dist_ball),
                "dist_goal": round6(dist_goal),
                "nearest_opp_dist": round6(nearest_opp_dist),
            },
        )

    # Standard scoring policy ('balanced' or 'press')
    if observation.owns_ball:
        pass_clearance = _segment_clearance(px, py, tm_pos[0], tm_pos[1], visible_opponents)
        shot_clearance = _segment_clearance(px, py, PITCH_HALF_LENGTH, 0.0, visible_opponents)

        # 1. Shoot score: favored when inside effective shooting range (dist_goal <= 7.4)
        range_factor = clamp(1.0 - dist_goal / 8.6, 0.0, 1.0)
        in_range_bonus = 0.44 if dist_goal <= 6.8 else -0.26
        shot_block_pen = 0.24 if shot_clearance < 0.80 else 0.0
        shoot_score = round6(
            0.22
            + 0.92 * range_factor
            + in_range_bonus
            + 0.30 * mind.style.directness
            - 0.14 * mind.style.pass_bias
            - shot_block_pen
        )

        # 2. Pass score: favored when passing lane is open and teammate is in support or pass_bias is high
        advance_gain = clamp((tm_pos[0] - px) / 4.5, -0.35, 0.90)
        lane_bonus = 0.22 if pass_clearance >= 0.72 else -0.32
        backward_pen = 0.28 if (tm_pos[0] < px - 1.6 and mind.style.pass_bias < 0.75) else 0.0
        pressure_pass_bonus = 0.24 if (nearest_opp_dist < 2.8 and pass_clearance >= 0.70) else 0.08
        close_goal_pass_pen = 0.30 if dist_goal <= 5.6 else 0.0
        pass_score = round6(
            0.22
            + 0.66 * mind.style.pass_bias
            + 0.28 * advance_gain
            + lane_bonus
            + pressure_pass_bonus
            - backward_pen
            - 0.18 * mind.style.directness
            - close_goal_pass_pen
        )

        # 3. Move (carry/dribble) score: primary way to advance or open passing/shooting angle
        dribble_space_bonus = 0.16 if (dist_goal > 6.2 and nearest_opp_dist >= 1.5) else 0.02
        move_score = round6(
            0.44
            + 0.36 * mind.style.directness
            - 0.16 * mind.style.pass_bias
            + dribble_space_bonus
        )

        # 4. Hold score
        hold_score = round6(0.14 - 0.08 * mind.style.directness)
        tackle_score = round6(-1.0)

        scores = {
            "move": move_score,
            "pass": pass_score,
            "shoot": shoot_score,
            "tackle": tackle_score,
            "hold": hold_score,
        }
        best_kind = max(MATCH_ACTIONS, key=lambda k: scores[k])
        if best_kind == "shoot":
            if visible_opponents and shot_clearance < 1.1:
                blocker_y = visible_opponents[0].position[1]  # type: ignore[index]
                aim_y = -0.95 if blocker_y >= 0.0 else 0.95
            else:
                aim_y = clamp(-0.25 * py, -0.80, 0.80)
            shoot_dir = _unit_direction(PITCH_HALF_LENGTH - px, aim_y - py)
            intent = MatchIntent(
                kind="shoot",
                direction_rel=shoot_dir,
                target_player_id=None,
                target_rel=(PITCH_HALF_LENGTH, round6(aim_y)),
            )
        elif best_kind == "pass":
            lead_target = (
                round6(clamp(tm_pos[0] + 0.72, -PITCH_HALF_LENGTH + 0.8, PITCH_HALF_LENGTH - 0.8)),
                round6(clamp(tm_pos[1], -PITCH_HALF_WIDTH + 0.6, PITCH_HALF_WIDTH - 0.6)),
            )
            pass_dir = _unit_direction(lead_target[0] - px, lead_target[1] - py)
            intent = MatchIntent(
                kind="pass",
                direction_rel=pass_dir,
                target_player_id=tm_id,
                target_rel=lead_target,
            )
        elif best_kind == "move":
            target_x = min(PITCH_HALF_LENGTH - 1.0, px + 2.8)
            target_y = clamp(0.35 * py, -1.4, 1.4)
            move_dir = _unit_direction(target_x - px, target_y - py)
            intent = MatchIntent(
                kind="move",
                direction_rel=move_dir,
                target_player_id=None,
                target_rel=(round6(target_x), round6(target_y)),
            )
        else:
            intent = MatchIntent(kind="hold", direction_rel=(0.0, 0.0))

    else:
        # Off-ball decision
        press_directive_bonus = 0.28 if mind.coach_directive == "press" else 0.0
        can_tackle = (
            observation.ball_owner_relation == "opponent" and dist_ball <= TACKLE_RADIUS
        )
        if can_tackle:
            tackle_score = round6(
                0.78
                + 0.34 * mind.style.press_bias
                + press_directive_bonus
                + 0.16 * (1.0 - dist_ball / TACKLE_RADIUS)
            )
        else:
            tackle_score = round6(-1.0)

        # Off-ball target position
        bvx, bvy = observation.ball_velocity
        pred_bx = clamp(bx + bvx, -PITCH_HALF_LENGTH + 0.4, PITCH_HALF_LENGTH - 0.4)
        pred_by = clamp(by + bvy, -PITCH_HALF_WIDTH + 0.4, PITCH_HALF_WIDTH - 0.4)
        dist_pred_ball = math.hypot(pred_bx - px, pred_by - py)
        tm_dist_pred_ball = (
            math.hypot(pred_bx - tm_pos[0], pred_by - tm_pos[1])
            if visible_teammates
            else 99.0
        )

        if observation.ball_owner_relation == "teammate":
            # Support run ahead of the ball on the opposite lateral lane
            support_x = clamp(max(bx + 2.4, px + 1.8), -2.5, PITCH_HALF_LENGTH - 1.5)
            support_y = -1.4 if by >= 0.0 else 1.4
            move_target = (round6(support_x), round6(support_y))
        elif (
            observation.ball_owner_relation == "none"
            and dist_pred_ball <= tm_dist_pred_ball + 0.35
        ):
            # Move to predicted landing point of loose/passed ball
            move_target = (round6(pred_bx), round6(pred_by))
        elif (
            dist_ball <= tm_dist_ball + 0.25
            or (mind.coach_directive == "press" and dist_ball <= 3.2)
            or (not visible_teammates and observation.slot == 1)
        ):
            # Primary presser / ball winner moves directly to ball
            move_target = (round6(bx), round6(by))
        else:
            # Cover defender protects the central goal lane between ball and own goal
            cover_offset = 2.2 if mind.coach_directive == "press" else 3.4
            cover_max_x = 0.8 if mind.coach_directive == "press" else -1.5
            cover_x = clamp(bx - cover_offset, -PITCH_HALF_LENGTH + 0.9, cover_max_x)
            cover_y = clamp(0.30 * by, -0.9, 0.9)
            move_target = (round6(cover_x), round6(cover_y))

        dist_to_target = math.hypot(move_target[0] - px, move_target[1] - py)
        move_score = round6(
            0.64
            + 0.22 * mind.style.press_bias
            + press_directive_bonus
            + (0.10 if dist_to_target > 0.35 else -0.25)
        )
        hold_score = round6(0.20 - 0.10 * mind.style.press_bias - 0.5 * press_directive_bonus)
        scores = {
            "move": move_score,
            "pass": round6(-1.0),
            "shoot": round6(-1.0),
            "tackle": tackle_score,
            "hold": hold_score,
        }
        best_kind = max(MATCH_ACTIONS, key=lambda k: scores[k])
        if best_kind == "tackle":
            tackle_dir = _unit_direction(bx - px, by - py)
            intent = MatchIntent(
                kind="tackle",
                direction_rel=tackle_dir,
                target_player_id=observation.ball_owner_id,
                target_rel=(bx, by),
            )
        elif best_kind == "move":
            move_dir = _unit_direction(move_target[0] - px, move_target[1] - py)
            intent = MatchIntent(
                kind="move",
                direction_rel=move_dir,
                target_player_id=None,
                target_rel=move_target,
            )
        else:
            intent = MatchIntent(kind="hold", direction_rel=(0.0, 0.0))

    return MatchDecision(
        intent=intent,
        scores=scores,
        explanation={
            "directive": mind.coach_directive,
            "owns_ball": observation.owns_ball,
            "ball_owner_relation": observation.ball_owner_relation,
            "dist_ball": round6(dist_ball),
            "dist_goal": round6(dist_goal),
            "nearest_opp_dist": round6(nearest_opp_dist),
            "visible_teammates": len(visible_teammates),
            "visible_opponents": len(visible_opponents),
        },
    )


def _rotate_vector(vx: float, vy: float, angle_rad: float) -> tuple[float, float]:
    c = math.cos(angle_rad)
    s = math.sin(angle_rad)
    return (round6(vx * c - vy * s), round6(vx * s + vy * c))


def step_match(
    world: MatchWorldState,
    *,
    intent_overrides: dict[str, MatchIntent] | None = None,
    player_order: tuple[str, ...] | None = None,
) -> MatchWorldState:
    """Advance the 2D match world by 1 tick with simultaneous observations, decisions, and symmetric resolution."""
    validate_match_world(world, check_history=False)
    order = player_order or tuple(world.players.keys())
    if set(order) != set(world.players.keys()) or len(order) != len(world.players):
        raise ValueError(f"invalid player_order: {order}")

    # 1. Generate local observations and independent decisions for all players simultaneously
    observations: dict[str, LocalMatchObservation] = {}
    decisions: dict[str, MatchDecision] = {}
    for pid in order:
        obs = observe_match_player(world, pid)
        observations[pid] = obs
        decision = decide_match_player(obs, world.players[pid].mind)
        if intent_overrides and pid in intent_overrides:
            decision = MatchDecision(
                intent=intent_overrides[pid],
                scores=decision.scores,
                explanation={**decision.explanation, "override": True},
            )
        decisions[pid] = decision

    # Canonical recording order (home slot 1,2 then away slot 1,2) so logs never depend on caller's iteration order
    canonical_pids = tuple(
        p.player_id
        for p in sorted(
            world.players.values(),
            key=lambda p: (0 if p.team == "home" else 1, p.slot),
        )
    )

    start_positions = {
        pid: (world.players[pid].x, world.players[pid].y) for pid in canonical_pids
    }
    start_owner_id = world.ball.owner_id
    effective_actions: dict[str, str] = {}
    intent_statuses: dict[str, str] = {}

    for pid in canonical_pids:
        player = world.players[pid]
        obs = observations[pid]
        dec = decisions[pid]
        raw_kind = dec.intent.kind
        if raw_kind not in MATCH_ACTIONS:
            raise ValueError(f"invalid action kind: {raw_kind}")

        if raw_kind in ("pass", "shoot") and pid != start_owner_id:
            effective_kind = "hold"
            intent_status = "illegal_without_ball"
        elif raw_kind == "tackle" and (
            start_owner_id is None
            or world.players[start_owner_id].team == player.team
            or math.hypot(world.ball.x - player.x, world.ball.y - player.y) > TACKLE_RADIUS + 1e-6
        ):
            effective_kind = "hold"
            intent_status = "out_of_tackle_range"
        else:
            effective_kind = raw_kind
            intent_status = "accepted"

        effective_actions[pid] = effective_kind
        intent_statuses[pid] = intent_status
        world.record(
            "player_decision",
            player_id=pid,
            team=player.team,
            slot=player.slot,
            observation=obs.to_dict(),
            intent=dec.intent.to_dict(),
            effective_action=effective_kind,
            intent_status=intent_status,
            scores={k: round6(v) for k, v in dec.scores.items()},
            explanation=dec.explanation,
        )

    # 2. Simultaneous movement update for all players choosing move or tackle
    new_positions: dict[str, tuple[float, float]] = {}
    move_distances: dict[str, float] = {}
    for pid in canonical_pids:
        player = world.players[pid]
        eff = effective_actions[pid]
        intent = decisions[pid].intent
        sign = team_attack_sign(player.team)
        max_step = round6(BASE_MOVE_SPEED + PACE_MOVE_SCALE * player.traits.pace)

        if eff in ("move", "tackle"):
            dx_world = sign * intent.direction_rel[0]
            dy_world = sign * intent.direction_rel[1]
            ux, uy = _unit_direction(dx_world, dy_world)
            step_len = max_step
            if intent.target_rel is not None:
                tx_world = sign * intent.target_rel[0]
                ty_world = sign * intent.target_rel[1]
                dist_target = math.hypot(tx_world - player.x, ty_world - player.y)
                step_len = min(max_step, dist_target)
            nx = round6(clamp(player.x + ux * step_len, -PITCH_HALF_LENGTH, PITCH_HALF_LENGTH))
            ny = round6(clamp(player.y + uy * step_len, -PITCH_HALF_WIDTH, PITCH_HALF_WIDTH))
        else:
            nx = round6(player.x)
            ny = round6(player.y)

        actual_dist = round6(math.hypot(nx - player.x, ny - player.y))
        new_positions[pid] = (nx, ny)
        move_distances[pid] = actual_dist
        player.x = nx
        player.y = ny

    # 3. Resolve tackles if the ball is owned at start of tick
    prev_bx, prev_by = world.ball.x, world.ball.y
    tackle_winner_id: str | None = None
    kicked_this_tick_by: str | None = None

    if start_owner_id is not None:
        owner = world.players[start_owner_id]
        challengers = [
            world.players[pid]
            for pid in canonical_pids
            if effective_actions[pid] == "tackle" and world.players[pid].team != owner.team
        ]
        if challengers:
            owner_token = _rng_actor_token(world, owner)
            owner_noise = _scaled_uniform(world, "tackle_keep", owner_token, -0.06, 0.06)
            hold_bonus = 0.08 if effective_actions[start_owner_id] == "hold" else 0.0
            owner_keep_score = round6(
                0.50 * (owner.traits.pace + owner.traits.passing) + hold_bonus + owner_noise
            )
            challenger_scores: list[tuple[float, float, int, str]] = []
            for ch in challengers:
                sx, sy = start_positions[ch.player_id]
                dist = math.hypot(prev_bx - sx, prev_by - sy)
                ch_token = _rng_actor_token(world, ch)
                ch_noise = _scaled_uniform(world, "tackle_win", ch_token, -0.06, 0.06)
                c_score = round6(
                    0.56 * ch.traits.pace
                    + 0.34 * clamp(1.0 - dist / TACKLE_RADIUS, 0.0, 1.0)
                    + ch_noise
                )
                challenger_scores.append((c_score, -dist, -ch.slot, ch.player_id))

            challenger_scores.sort(reverse=True)
            best_c_score, _, _, best_cid = challenger_scores[0]
            if best_c_score > owner_keep_score:
                tackle_winner_id = best_cid
                winner = world.players[best_cid]
                world.ball.owner_id = best_cid
                world.ball.x = winner.x
                world.ball.y = winner.y
                world.ball.vx = 0.0
                world.ball.vy = 0.0
                world.ball.last_touch_id = best_cid
                world.ball.last_touch_team = winner.team
                world.ball.last_kick_kind = "tackle_won"
                world.record(
                    "tackle_resolution",
                    outcome="tackle_won",
                    challenger_id=best_cid,
                    challenger_team=winner.team,
                     dispossessed_id=start_owner_id,
                    challenger_score=best_c_score,
                    owner_keep_score=owner_keep_score,
                )
            else:
                world.record(
                    "tackle_resolution",
                    outcome="tackle_evaded",
                    challenger_id=best_cid,
                    challenger_team=world.players[best_cid].team,
                    owner_id=start_owner_id,
                    challenger_score=best_c_score,
                    owner_keep_score=owner_keep_score,
                )

    # 4. Ball movement (kick by owner, carry by owner, or loose ball continuation)
    if start_owner_id is not None and tackle_winner_id is None:
        owner = world.players[start_owner_id]
        owner_eff = effective_actions[start_owner_id]
        owner_intent = decisions[start_owner_id].intent
        sign = team_attack_sign(owner.team)
        owner_token = _rng_actor_token(world, owner)

        if owner_eff == "pass":
            base_dx = sign * owner_intent.direction_rel[0]
            base_dy = sign * owner_intent.direction_rel[1]
            ux, uy = _unit_direction(base_dx, base_dy)
            if ux == 0.0 and uy == 0.0:
                ux, uy = (sign * 1.0, 0.0)
            angle_amp = PASS_ANGLE_NOISE_SCALE * (1.0 - owner.traits.passing)
            angle_err = _scaled_uniform(world, "pass_angle", owner_token, -angle_amp, angle_amp)
            kux, kuy = _rotate_vector(ux, uy, angle_err)
            max_pass_speed = BASE_PASS_SPEED + PASS_SPEED_SCALE * owner.traits.passing
            if owner_intent.target_rel is not None:
                tx_world = sign * owner_intent.target_rel[0]
                ty_world = sign * owner_intent.target_rel[1]
                dist_target = math.hypot(tx_world - prev_bx, ty_world - prev_by)
                speed = round6(clamp(0.96 * dist_target, 1.15, max_pass_speed))
            else:
                speed = round6(max_pass_speed)
            world.ball.owner_id = None
            world.ball.last_touch_id = owner.player_id
            world.ball.last_touch_team = owner.team
            world.ball.last_kick_kind = "pass"
            world.ball.vx = round6(kux * speed)
            world.ball.vy = round6(kuy * speed)
            world.ball.x = round6(prev_bx + world.ball.vx)
            world.ball.y = round6(prev_by + world.ball.vy)
            world.ball.vx = round6(world.ball.vx * BALL_DECAY)
            world.ball.vy = round6(world.ball.vy * BALL_DECAY)
            kicked_this_tick_by = owner.player_id
            world.record(
                "ball_kick",
                kick_kind="pass",
                kicker_id=owner.player_id,
                kicker_team=owner.team,
                target_player_id=owner_intent.target_player_id,
                speed=speed,
                angle_error=angle_err,
                ball_to=[world.ball.x, world.ball.y],
            )
        elif owner_eff == "shoot":
            dist_to_goal = math.hypot(sign * PITCH_HALF_LENGTH - prev_bx, 0.0 - prev_by)
            dist_factor = clamp((dist_to_goal / 5.5) ** 1.35, 0.75, 2.60)
            long_range_bias = sign * 1.45 if dist_to_goal > 8.2 else 0.0
            y_amp = SHOOT_Y_NOISE_SCALE * (1.0 - owner.traits.shooting) * dist_factor
            # Preserve exact mirror symmetry under sign flip by multiplying y error by sign
            y_err = sign * _scaled_uniform(world, "shoot_y", owner_token, -y_amp, y_amp) + long_range_bias
            aim_y_rel = owner_intent.target_rel[1] if owner_intent.target_rel is not None else 0.0
            target_x = sign * PITCH_HALF_LENGTH
            target_y = round6(sign * aim_y_rel + y_err)
            ux, uy = _unit_direction(target_x - prev_bx, target_y - prev_by)
            range_speed_scale = 0.74 if dist_to_goal > 8.2 else 1.0
            speed = round6((BASE_SHOOT_SPEED + SHOOT_SPEED_SCALE * owner.traits.shooting) * range_speed_scale)
            on_target = abs(target_y) <= GOAL_HALF_WIDTH
            world.ball.owner_id = None
            world.ball.last_touch_id = owner.player_id
            world.ball.last_touch_team = owner.team
            world.ball.last_kick_kind = "shoot"
            world.ball.vx = round6(ux * speed)
            world.ball.vy = round6(uy * speed)
            world.ball.x = round6(prev_bx + world.ball.vx)
            world.ball.y = round6(prev_by + world.ball.vy)
            world.ball.vx = round6(world.ball.vx * BALL_DECAY)
            world.ball.vy = round6(world.ball.vy * BALL_DECAY)
            kicked_this_tick_by = owner.player_id
            world.record(
                "ball_kick",
                kick_kind="shoot",
                kicker_id=owner.player_id,
                kicker_team=owner.team,
                target_y=target_y,
                on_target=on_target,
                speed=speed,
                ball_to=[world.ball.x, world.ball.y],
            )
        else:
            # Owner carries or holds the ball
            world.ball.x = owner.x
            world.ball.y = owner.y
            world.ball.vx = 0.0
            world.ball.vy = 0.0
            world.ball.last_touch_id = owner.player_id
            world.ball.last_touch_team = owner.team
            world.ball.last_kick_kind = "carry"
    elif start_owner_id is None:
        # Loose ball continues traveling
        world.ball.x = round6(prev_bx + world.ball.vx)
        world.ball.y = round6(prev_by + world.ball.vy)
        world.ball.vx = round6(world.ball.vx * BALL_DECAY)
        world.ball.vy = round6(world.ball.vy * BALL_DECAY)

    # 5. Check goal or out-of-bounds
    goal_scored_by: str | None = None
    if world.ball.x >= PITCH_HALF_LENGTH:
        dx = world.ball.x - prev_bx
        t_cross = (PITCH_HALF_LENGTH - prev_bx) / dx if abs(dx) > 1e-9 else 1.0
        y_cross = round6(prev_by + clamp(t_cross, 0.0, 1.0) * (world.ball.y - prev_by))
        if abs(y_cross) <= GOAL_HALF_WIDTH:
            goal_scored_by = "home"
        else:
            world.ball.x = PITCH_HALF_LENGTH
            world.ball.y = round6(clamp(world.ball.y, -PITCH_HALF_WIDTH, PITCH_HALF_WIDTH))
            world.ball.vx = 0.0
            world.ball.vy = 0.0
            world.ball.owner_id = None
            world.record("ball_boundary", outcome="out_of_bounds_stop", position=[world.ball.x, world.ball.y])
    elif world.ball.x <= -PITCH_HALF_LENGTH:
        dx = world.ball.x - prev_bx
        t_cross = (-PITCH_HALF_LENGTH - prev_bx) / dx if abs(dx) > 1e-9 else 1.0
        y_cross = round6(prev_by + clamp(t_cross, 0.0, 1.0) * (world.ball.y - prev_by))
        if abs(y_cross) <= GOAL_HALF_WIDTH:
            goal_scored_by = "away"
        else:
            world.ball.x = -PITCH_HALF_LENGTH
            world.ball.y = round6(clamp(world.ball.y, -PITCH_HALF_WIDTH, PITCH_HALF_WIDTH))
            world.ball.vx = 0.0
            world.ball.vy = 0.0
            world.ball.owner_id = None
            world.record("ball_boundary", outcome="out_of_bounds_stop", position=[world.ball.x, world.ball.y])

    if goal_scored_by is None and abs(world.ball.y) > PITCH_HALF_WIDTH:
        world.ball.y = round6(clamp(world.ball.y, -PITCH_HALF_WIDTH, PITCH_HALF_WIDTH))
        world.ball.vx = 0.0
        world.ball.vy = 0.0
        world.ball.owner_id = None
        world.record("ball_boundary", outcome="out_of_bounds_stop", position=[world.ball.x, world.ball.y])

    if goal_scored_by is not None:
        if world.ball.last_touch_id is None or world.ball.last_kick_kind is None:
            raise ValueError("causeless goal detected: missing last_touch_id or last_kick_kind")
        world.score[goal_scored_by] += 1
        world.record(
            "goal",
            scoring_team=goal_scored_by,
            scorer_id=world.ball.last_touch_id,
            last_touch_team=world.ball.last_touch_team,
            cause_kind=world.ball.last_kick_kind,
            score=dict(world.score),
        )
        # Symmetric kickoff reset
        for pid, (kx, ky) in world.kickoff_positions.items():
            world.players[pid].x = round6(kx)
            world.players[pid].y = round6(ky)
        world.ball = BallState(
            x=0.0,
            y=0.0,
            vx=0.0,
            vy=0.0,
            owner_id=None,
            last_touch_id=world.ball.last_touch_id,
            last_touch_team=world.ball.last_touch_team,
            last_kick_kind="kickoff_reset",
        )
    elif world.ball.owner_id is None:
        # 6. Loose ball control & symmetric tie resolution
        candidates: list[tuple[float, PlayerMatchState]] = []
        for pid in canonical_pids:
            if pid == kicked_this_tick_by:
                continue
            player = world.players[pid]
            dist = math.hypot(world.ball.x - player.x, world.ball.y - player.y)
            if dist <= CONTROL_RADIUS:
                p_token = _rng_actor_token(world, player)
                c_noise = _scaled_uniform(world, "loose_control", p_token, -0.05, 0.05)
                c_score = round6(
                    0.60 * (1.0 - dist / CONTROL_RADIUS)
                    + 0.25 * player.traits.pace
                    + 0.15 * player.traits.passing
                    + c_noise
                )
                candidates.append((c_score, player))

        if candidates:
            best_score = max(c[0] for c in candidates)
            top_players = [c[1] for c in candidates if c[0] == best_score]
            top_teams = {p.team for p in top_players}
            if len(top_teams) > 1:
                # Exact inter-team tie: resolve symmetrically without ID/order bias
                mid_x = round6(sum(p.x for p in top_players) / len(top_players))
                mid_y = round6(sum(p.y for p in top_players) / len(top_players))
                world.ball.x = clamp(mid_x, -PITCH_HALF_LENGTH, PITCH_HALF_LENGTH)
                world.ball.y = clamp(mid_y, -PITCH_HALF_WIDTH, PITCH_HALF_WIDTH)
                world.ball.vx = 0.0
                world.ball.vy = 0.0
                world.ball.owner_id = None
                world.record(
                    "ball_control",
                    outcome="mutual_rebound",
                    tied_players=[p.player_id for p in top_players],
                    control_score=best_score,
                )
            else:
                # Single winner (or same-team tie broken by slot)
                top_players.sort(key=lambda p: p.slot)
                winner = top_players[0]
                prev_touch_id = world.ball.last_touch_id
                prev_touch_team = world.ball.last_touch_team
                prev_kick_kind = world.ball.last_kick_kind
                if (
                    prev_kick_kind == "pass"
                    and prev_touch_team == winner.team
                    and prev_touch_id is not None
                    and prev_touch_id != winner.player_id
                ):
                    outcome = "pass_completed"
                elif prev_kick_kind in ("pass", "shoot") and prev_touch_team not in (None, winner.team):
                    outcome = "interception"
                else:
                    outcome = "pickup"

                world.ball.owner_id = winner.player_id
                world.ball.x = winner.x
                world.ball.y = winner.y
                world.ball.vx = 0.0
                world.ball.vy = 0.0
                world.ball.last_touch_id = winner.player_id
                world.ball.last_touch_team = winner.team
                world.ball.last_kick_kind = outcome
                world.record(
                    "ball_control",
                    outcome=outcome,
                    winner_id=winner.player_id,
                    winner_team=winner.team,
                    from_player_id=prev_touch_id,
                    control_score=best_score,
                )

    # 7. Record end-of-tick summary frame for full trajectory replay
    world.record(
        "tick_summary",
        score=dict(world.score),
        ball=world.ball.to_dict(),
        players={
            pid: {
                "x": round6(world.players[pid].x),
                "y": round6(world.players[pid].y),
                "action": effective_actions[pid],
                "intent_status": intent_statuses[pid],
                "move_distance": move_distances[pid],
            }
            for pid in canonical_pids
        },
        goal_scored_by=goal_scored_by,
    )

    world.tick += 1
    validate_match_world(world, check_history=False)
    return world


def run_match(
    ticks: int = 40,
    seed: int = 0,
    condition: str = "baseline",
    *,
    world: MatchWorldState | None = None,
    config: MatchConfig | None = None,
) -> MatchWorldState:
    state = world or create_match_world(seed=seed, condition=condition, config=config)
    validate_match_world(state, check_history=True)
    for _ in range(ticks):
        step_match(state)
    validate_match_world(state, check_history=True)
    return state


def validate_match_world(world: MatchWorldState, *, check_history: bool = True) -> None:
    if world.tick < 0:
        raise ValueError("tick cannot be negative")
    if world.config.condition not in T008_ALL_CONDITIONS:
        raise ValueError(f"unknown match condition: {world.config.condition}")
    if world.config.home_observation_mode not in OBSERVATION_MODES:
        raise ValueError(f"invalid home_observation_mode: {world.config.home_observation_mode}")
    if world.config.away_observation_mode not in OBSERVATION_MODES:
        raise ValueError(f"invalid away_observation_mode: {world.config.away_observation_mode}")
    if world.config.vision_radius <= 0.0:
        raise ValueError("vision_radius must be positive")
    if world.config.policy_version != MATCH_POLICY_VERSION:
        raise ValueError(f"unsupported match policy_version: {world.config.policy_version}")

    expected_pids = {"h1", "h2", "a1", "a2"}
    if set(world.players.keys()) != expected_pids:
        raise ValueError(f"expected players {sorted(expected_pids)}, got {sorted(world.players.keys())}")
    if set(world.kickoff_positions.keys()) != expected_pids:
        raise ValueError("kickoff_positions keys mismatch")
    if set(world.score.keys()) != {"home", "away"}:
        raise ValueError("score keys must be {'home', 'away'}")
    if any(v < 0 for v in world.score.values()):
        raise ValueError("negative score is invalid")

    for pid, player in world.players.items():
        if player.player_id != pid or player.mind.player_id != pid:
            raise ValueError(f"player_id mismatch for {pid}")
        if player.team not in MATCH_TEAMS or player.mind.team != player.team:
            raise ValueError(f"invalid team for {pid}")
        if player.slot not in (1, 2):
            raise ValueError(f"invalid slot for {pid}: {player.slot}")
        if not (-PITCH_HALF_LENGTH <= player.x <= PITCH_HALF_LENGTH):
            raise ValueError(f"player {pid} x out of bounds: {player.x}")
        if not (-PITCH_HALF_WIDTH <= player.y <= PITCH_HALF_WIDTH):
            raise ValueError(f"player {pid} y out of bounds: {player.y}")
        for name, val in (
            ("pace", player.traits.pace),
            ("passing", player.traits.passing),
            ("shooting", player.traits.shooting),
            ("directness", player.mind.style.directness),
            ("pass_bias", player.mind.style.pass_bias),
            ("press_bias", player.mind.style.press_bias),
        ):
            if not (0.0 <= val <= 1.0):
                raise ValueError(f"{pid} {name} out of [0, 1]: {val}")
        if player.mind.coach_directive not in DIRECTIVE_MODES:
            raise ValueError(f"invalid coach_directive for {pid}: {player.mind.coach_directive}")

    if not (-PITCH_HALF_LENGTH <= world.ball.x <= PITCH_HALF_LENGTH):
        raise ValueError(f"ball x out of bounds: {world.ball.x}")
    if not (-PITCH_HALF_WIDTH <= world.ball.y <= PITCH_HALF_WIDTH):
        raise ValueError(f"ball y out of bounds: {world.ball.y}")
    if world.ball.owner_id is not None:
        if world.ball.owner_id not in world.players:
            raise ValueError(f"unknown ball owner_id: {world.ball.owner_id}")
        owner = world.players[world.ball.owner_id]
        if abs(world.ball.x - owner.x) > 1e-5 or abs(world.ball.y - owner.y) > 1e-5:
            raise ValueError("owned ball position must match owner position")

    if not check_history:
        return

    # Validate event history accounting and decision consistency
    decisions_per_tick: dict[int, set[str]] = {}
    summaries_per_tick: set[int] = set()
    tallied_goals = {"home": 0, "away": 0}

    for event in world.events:
        t = int(event["tick"])
        if t < 0 or t >= world.tick:
            raise ValueError(f"event tick {t} out of range for world.tick={world.tick}")
        kind = str(event["kind"])
        if kind == "player_decision":
            pid = str(event["player_id"])
            if pid not in world.players:
                raise ValueError(f"decision for unknown player {pid}")
            seen = decisions_per_tick.setdefault(t, set())
            if pid in seen:
                raise ValueError(f"duplicate player_decision for {pid} at tick {t}")
            seen.add(pid)
            obs = LocalMatchObservation.from_dict(dict(event["observation"]))
            recomputed = decide_match_player(obs, world.players[pid].mind)
            recorded_scores = {str(k): round6(float(v)) for k, v in event["scores"].items()}
            expected_scores = {k: round6(v) for k, v in recomputed.scores.items()}
            if recorded_scores != expected_scores:
                raise ValueError(f"tampered decision scores for {pid} at tick {t}")
            if not event.get("explanation", {}).get("override", False):
                if dict(event["intent"]) != recomputed.intent.to_dict():
                    raise ValueError(f"tampered decision intent for {pid} at tick {t}")
        elif kind == "goal":
            team = str(event["scoring_team"])
            scorer_id = event.get("scorer_id")
            cause_kind = event.get("cause_kind")
            if team not in tallied_goals:
                raise ValueError(f"invalid scoring_team: {team}")
            if scorer_id not in world.players or not cause_kind:
                raise ValueError(f"invalid goal attribution at tick {t}")
            tallied_goals[team] += 1
        elif kind == "tick_summary":
            if t in summaries_per_tick:
                raise ValueError(f"duplicate tick_summary at tick {t}")
            summaries_per_tick.add(t)
            players_frame = dict(event["players"])
            if set(players_frame.keys()) != expected_pids:
                raise ValueError(f"incomplete tick_summary players at tick {t}")
            for pid, pframe in players_frame.items():
                max_step = round6(BASE_MOVE_SPEED + PACE_MOVE_SCALE * world.players[pid].traits.pace)
                if float(pframe["move_distance"]) > max_step + 1e-5:
                    raise ValueError(f"player {pid} exceeded max move step at tick {t}")

    if world.events:
        for t in range(world.tick):
            if decisions_per_tick.get(t) != expected_pids:
                raise ValueError(f"missing player decisions at tick {t}")
            if t not in summaries_per_tick:
                raise ValueError(f"missing tick_summary at tick {t}")
        if tallied_goals != world.score:
            raise ValueError(
                f"score mismatch between state {world.score} and goal events {tallied_goals}"
            )


def match_identity() -> dict[str, Any]:
    return {
        **package_identity(),
        "world_kind": MATCH_WORLD_KIND,
        "save": MATCH_SAVE_VERSION,
        "rules": MATCH_RULE_VERSION,
        "policy": MATCH_POLICY_VERSION,
        "author": MATCH_AUTHOR,
    }


def snapshot_payload(world: MatchWorldState) -> dict[str, Any]:
    validate_match_world(world)
    return {
        "world_kind": MATCH_WORLD_KIND,
        "identity": {
            **match_identity(),
            "save_version": MATCH_SAVE_VERSION,
            "rule_version": MATCH_RULE_VERSION,
            "policy_version": world.config.policy_version,
        },
        "state": world.to_dict(include_events=True),
    }


def write_snapshot(path: Path | str, world: MatchWorldState) -> None:
    p = Path(path)
    payload = snapshot_payload(world)
    atomic_write(p, [canonical(payload), b"\n"])


def write_event_log(path: Path | str, world: MatchWorldState) -> None:
    p = Path(path)
    lines = [canonical(event) + b"\n" for event in world.events]
    atomic_write(p, lines)


def write_run(
    out_dir: Path | str,
    world: MatchWorldState,
    elapsed_seconds: float = 0.0,
) -> str:
    from .experiment_t008 import measure_match_run

    out = Path(out_dir)
    payload = snapshot_payload(world)
    state_hash = digest(payload)
    atomic_write(out / "snapshot.json", [canonical(payload), b"\n"])
    write_event_log(out / "events.jsonl", world)
    write_json(
        out / "summary.json",
        {**measure_match_run(world, elapsed_seconds), "simulation_sha256": state_hash},
    )
    write_json(
        out / "manifest.json",
        {
            "world_kind": MATCH_WORLD_KIND,
            "author": MATCH_AUTHOR,
            "versions": match_identity(),
            "runtime": runtime(),
            "config": world.config.to_dict(),
            "seed": world.seed,
            "ticks": world.tick,
            "simulation_sha256": state_hash,
        },
    )
    return state_hash


def load_snapshot(path: Path | str) -> MatchWorldState:
    try:
        payload = json.loads(
            Path(path).read_bytes(),
            parse_constant=reject_nonfinite,
            parse_float=parse_float,
        )
        if payload.get("world_kind") not in (None, MATCH_WORLD_KIND):
            raise ValueError(f"unsupported world_kind: {payload.get('world_kind')}")
        ident = dict(payload.get("identity", {}))
        current = package_identity()
        if ident.get("world_kind") != MATCH_WORLD_KIND:
            raise ValueError(f"unsupported world_kind: {ident.get('world_kind')}")
        if ident.get("save_version") != MATCH_SAVE_VERSION:
            raise ValueError(f"unsupported save_version: {ident.get('save_version')}")
        if ident.get("rule_version") != MATCH_RULE_VERSION:
            raise ValueError(f"unsupported rule_version: {ident.get('rule_version')}")
        if ident.get("policy_version") != MATCH_POLICY_VERSION:
            raise ValueError(f"unsupported policy_version: {ident.get('policy_version')}")
        if ident.get("source_sha256") != current["source_sha256"]:
            raise ValueError("snapshot source_sha256 does not match current package")
        return MatchWorldState.from_dict(dict(payload["state"]))
    except (KeyError, TypeError, AttributeError, IndexError, OverflowError) as exc:
        raise ValueError("malformed match_2d snapshot") from exc


def state_sha256(world: MatchWorldState) -> str:
    return digest(snapshot_payload(world))
