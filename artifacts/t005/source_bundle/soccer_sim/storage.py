"""Versioned JSON checkpoints and reproducible run artifacts."""
import hashlib
import json
import os
import platform
import sys
import tempfile
from dataclasses import asdict
from math import isfinite
from pathlib import Path

from .advice import coach_experience, coach_forecast, remember_coach_experience
from .observation import RNG_VERSION, ema, observed_gain
from .policy import POLICY_VERSION
from .simulation import COACH_SLOTS, PLAYER_SLOTS, RULES_VERSION
from .state import (
    METHODS, PLAYER_ACTIONS, Assessment, BeliefRules, CoachAdjustment, CoachMind, Config,
    Player, PlayerMind, PolicyRules, TrustRules, WorldState, bounded,
)

SAVE_VERSION = 5
DAILY_SLOTS = {"player": PLAYER_SLOTS, "coach": COACH_SLOTS}


def canonical(value):
    return json.dumps(value, allow_nan=False, ensure_ascii=True,
                      separators=(",", ":"), sort_keys=True).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def identity():
    source = Path(__file__).parent
    sources = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
               for path in sorted(source.glob("*.py"))}
    return {"save": SAVE_VERSION, "rules": RULES_VERSION, "policy": POLICY_VERSION,
            "rng": RNG_VERSION, "source_sha256": digest(sources)}


def runtime():
    return {"python": platform.python_version(), "implementation": sys.implementation.name,
            "system": platform.system(), "machine": platform.machine()}


def event_payload(event):
    return vars(event)


def snapshot(world):
    return {
        "versions": identity(),
        "config": asdict(world.config),
        "daily_slots": DAILY_SLOTS,
        "day": world.day,
        "rng": {"root_seed": world.seed, "key_fields": ["channel", "day", "actor_id", "draw_index"]},
        "next_event_id": len(world.events) + 1,
        "players": {player_id: asdict(player) for player_id, player in world.players.items()},
        "coach": asdict(world.coach),
        "events": [event_payload(event) for event in world.events],
    }


def reject_nonfinite(value):
    raise ValueError(f"invalid JSON number: {value}")


def parse_float(value):
    number = float(value)
    if not isfinite(number):
        reject_nonfinite(value)
    return number


def validate_coach_history(world):
    """Check that private forecasts use the latest beliefs available at their own time."""
    beliefs = world.config.beliefs
    session = beliefs.coach_session_observation
    learn = beliefs.coach_forecast_learning
    rates = world.coach.method_rates
    rate_source = world.coach.forecast_memory_id
    skills, adjustments = {}, {}
    paid, reservations, posts, experiences = {}, {}, {}, set()

    for event in world.events:
        target, row = event.target, event.data
        key = (event.day, target)
        if event.kind in ("initial_assessment", "coach_skill_belief"):
            value = row["skill_estimate"] if event.kind == "initial_assessment" else row["skill_after"]
            skills[target] = (value, event.event_id)
        if event.kind == "initial_coach_adjustment":
            if (target not in world.players or target in adjustments or event.day != 0
                    or row["residuals"] != [0.0] * len(METHODS) or event.causes != (rate_source,)):
                raise ValueError("invalid initial coach adjustment")
            adjustments[target] = CoachAdjustment(target, (0.0, 0.0), (event.event_id, event.event_id))
        if event.kind == "coach_action" and row["action"] == "observe" and session:
            if (event.phase != "coach" or target not in world.players
                    or key in paid or row["slots"] != 1):
                raise ValueError("invalid paid session")
            paid[key] = event.event_id
        if event.kind == "coach_action" and row["action"] == "propose" and session:
            skill, skill_source = skills[target]
            method = row["method"]
            expected = coach_forecast(method, skill, rates, adjustments[target])
            if (event.phase != "coach" or row["perceived_skill"] != skill
                    or row["subjective_rate"] != rates[METHODS.index(method)]
                    or any(row[field] != value for field, value in asdict(expected).items())
                    or event.causes[1:] != (skill_source, rate_source, expected.residual_memory_id)):
                raise ValueError("invalid coach proposal forecast")
        if event.kind == "coach_observation":
            if not session:
                if "forecasts" in row:
                    raise ValueError("session forecast without session observation")
                continue
            skill, skill_source = skills[target]
            adjustment = adjustments[target]
            forecasts = {method: coach_forecast(method, skill, rates, adjustment)
                         for method in METHODS}
            memory_sources = tuple(dict.fromkeys((skill_source, rate_source, *adjustment.memory_ids)))
            prior_state = world.validate_pointer(event.causes[1], target, {"initial_player", "state_update"})
            if (key in reservations or event.phase != "coach" or event.causes[0] != paid[key]
                    or prior_state.day != event.day - 1 or event.causes[2:] != memory_sources
                    or row["perceived_skill"] != skill
                    or row["source"] != "paid_sample" or not bounded(row["skill_signal"])
                    or row["skill_key"] != ["coach-skill", event.day, target, 0]
                    or row["forecasts"] != {method: asdict(value) for method, value in forecasts.items()}):
                raise ValueError("invalid coach reservation forecast")
            reservations[key] = event, forecasts
        if event.kind == "coach_post_observation":
            if not session or key in posts or event.phase != "evening":
                raise ValueError("invalid coach session post observation")
            pre, _ = reservations[key]
            pre_id, action_id, state_id = event.causes
            action = world.validate_pointer(action_id, target, {"player_action"})
            state = world.validate_pointer(state_id, target, {"state_update"})
            if (pre_id != pre.event_id or action.day != event.day or state.day != event.day
                    or state.causes[0] != action_id or row["performed_action"] not in PLAYER_ACTIONS
                    or row["performed_action"] != action.data["action"] or not bounded(row["skill_signal"])
                    or row["skill_key"] != ["coach-skill-post", event.day, target, 0]
                    or row["source"] != "paid_session_post"):
                raise ValueError("invalid coach session post reference")
            posts[key] = event
        if event.kind == "coach_method_experience":
            if not session or key in experiences or event.phase != "evening":
                raise ValueError("invalid coach method experience")
            pre, forecasts = reservations[key]
            post = posts[key]
            method = row["method"]
            if method not in METHODS or method != post.data["performed_action"]:
                raise ValueError("coach experience does not match the performed method")
            forecast = forecasts[method]
            expected = coach_experience(pre.data["skill_signal"], post.data["skill_signal"], forecast, learn)
            if (row["updates_enabled"] is not learn or row["source"] != "paid_session"
                    or event.causes != (pre.event_id, post.event_id, forecast.residual_memory_id)
                    or any(row[field] != value for field, value in {**asdict(forecast), **expected}.items())):
                raise ValueError("invalid coach method experience forecast or references")
            experiences.add(key)
            if learn:
                adjustments[target] = remember_coach_experience(
                    adjustments[target], method, expected["residual_after"], event.event_id,
                )
    if paid.keys() != reservations.keys() or paid.keys() != posts.keys():
        raise ValueError("incomplete paid coach sessions")
    if experiences != {key for key, event in posts.items() if event.data["performed_action"] in METHODS}:
        raise ValueError("missing coach method experience")
    if tuple(adjustments.values()) != world.coach.adjustments:
        raise ValueError("coach adjustments do not match latest history")


def load_snapshot(path):
    try:
        data = json.loads(Path(path).read_bytes(), parse_constant=reject_nonfinite,
                          parse_float=parse_float)
        if data["versions"] != identity():
            raise ValueError("incompatible save, rules, policy, RNG, or source version")
        if data["daily_slots"] != DAILY_SLOTS:
            raise ValueError("incompatible daily slot budgets")
        raw_config = data["config"]
        beliefs = BeliefRules(**raw_config["beliefs"])
        trust = TrustRules(**raw_config["trust"])
        if raw_config["beliefs"].keys() != asdict(beliefs).keys():
            raise ValueError("missing belief controls")
        if raw_config["trust"].keys() != asdict(trust).keys():
            raise ValueError("missing trust controls")
        config = Config(**{**raw_config, "policy": PolicyRules(**raw_config["policy"]),
                           "beliefs": beliefs, "trust": trust})
        config.validate()
        rng = data["rng"]
        if rng["key_fields"] != ["channel", "day", "actor_id", "draw_index"]:
            raise ValueError("incompatible RNG key schema")
        world = WorldState(rng["root_seed"], config, day=data["day"])
        for player_id, raw in data["players"].items():
            mind_data = raw["mind"]
            mind = PlayerMind(**{**mind_data,
                                 "expected_gains": tuple(mind_data["expected_gains"]),
                                 "gain_memory_ids": tuple(mind_data["gain_memory_ids"]),
                                 "preferences": tuple(mind_data["preferences"])})
            world.players[player_id] = Player(**{**raw, "mind": mind, "rates": tuple(raw["rates"])})
        coach = data["coach"]
        world.coach = CoachMind(**{**coach, "method_rates": tuple(coach["method_rates"]),
                                   "assessments": tuple(Assessment(**item) for item in coach["assessments"]),
                                   "adjustments": tuple(
                                       CoachAdjustment(item["player_id"], tuple(item["residuals"]),
                                                       tuple(item["memory_ids"]))
                                       for item in coach["adjustments"]
                                   )})
        for raw in data["events"]:
            event_id = world.record(raw["day"], raw["phase"], raw["kind"], raw["actor"],
                                    raw["target"], tuple(raw["causes"]), **raw["data"])
            if (type(raw["event_id"]) is not int or event_id != raw["event_id"]
                    or world.events[-1].causes != tuple(raw["causes"])):
                raise ValueError("invalid event numbering or duplicate cause")
            if raw["kind"] == "player_gain_belief":
                row = raw["data"]
                mode = beliefs.gain_target_mode
                if not beliefs.player_gain or row["gain_target_mode"] != mode:
                    raise ValueError("snapshot and gain event belief controls differ")
                low = -1.0 if mode == "signed" else 0.0
                signed = row["signed_observed_gain"]
                if (not bounded(signed, low=-1.0)
                        or not all(bounded(row[key], low=low)
                                   for key in ("measured_gain", "expected_before", "expected_after"))
                        or row["measured_gain"] != observed_gain(0.0, signed, mode)
                        or row["expected_after"] != ema(row["expected_before"],
                                                       row["measured_gain"], config.gain_alpha)):
                    raise ValueError("invalid gain belief event")
        if data["next_event_id"] != len(world.events) + 1:
            raise ValueError("invalid next event ID")
        controls = [event for event in world.events if event.kind == "belief_controls"]
        if len(controls) != 1 or controls[0].phase != "setup" or controls[0].data != asdict(beliefs):
            raise ValueError("snapshot and log belief controls differ")
        controls = [event for event in world.events if event.kind == "trust_controls"]
        if len(controls) != 1 or controls[0].phase != "setup" or controls[0].data != asdict(trust):
            raise ValueError("snapshot and log trust controls differ")
        close_days = [event.day for event in world.events if event.kind == "day_summary"]
        if close_days != list(range(1, world.day + 1)):
            raise ValueError("invalid completed-day history")
        last = world.events[-1]
        if last.day != world.day or (world.day and last.kind != "day_summary"):
            raise ValueError("snapshot must end at a day boundary")
        world.validate_state()
        validate_coach_history(world)
        return world
    except (KeyError, TypeError, AttributeError, IndexError) as exc:
        raise ValueError("malformed snapshot") from exc


def atomic_write(path, chunks):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temp_path = Path(stream.name)
        try:
            for chunk in chunks:
                stream.write(chunk)
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temp_path, path)
        finally:
            temp_path.unlink(missing_ok=True)


def write_json(path, value):
    atomic_write(path, [(json.dumps(value, allow_nan=False, ensure_ascii=True,
                                   indent=2, sort_keys=True) + "\n").encode()])


def write_snapshot(path, world):
    atomic_write(path, [canonical(snapshot(world)), b"\n"])


def event_lines(world):
    for event in world.events:
        yield canonical(event_payload(event)) + b"\n"


def write_run(out, world, elapsed_seconds):
    from .experiments import measure

    out = Path(out)
    data = snapshot(world)
    state_hash = digest(data)
    atomic_write(out / "snapshot.json", [canonical(data), b"\n"])
    atomic_write(out / "events.jsonl", event_lines(world))
    log_size = (out / "events.jsonl").stat().st_size
    metrics = measure(world, elapsed_seconds, log_size)
    write_json(out / "summary.json", {**metrics, "simulation_sha256": state_hash})
    write_json(out / "manifest.json", {
        "versions": data["versions"], "runtime": runtime(), "rng": data["rng"],
        "config": data["config"], "daily_slots": data["daily_slots"],
        "day": world.day, "simulation_sha256": state_hash,
    })
    return state_hash
