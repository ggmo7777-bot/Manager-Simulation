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

from .observation import RNG_VERSION
from .policy import POLICY_VERSION
from .simulation import COACH_SLOTS, PLAYER_SLOTS, RULES_VERSION
from .state import Assessment, BeliefRules, CoachMind, Config, Player, PlayerMind, PolicyRules, WorldState

SAVE_VERSION = 2
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
        if raw_config["beliefs"].keys() != asdict(beliefs).keys():
            raise ValueError("missing belief controls")
        config = Config(**{**raw_config, "policy": PolicyRules(**raw_config["policy"]),
                           "beliefs": beliefs})
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
        world.coach = CoachMind(tuple(Assessment(**item) for item in data["coach"]["assessments"]))
        for raw in data["events"]:
            event_id = world.record(raw["day"], raw["phase"], raw["kind"], raw["actor"],
                                    raw["target"], tuple(raw["causes"]), **raw["data"])
            if (type(raw["event_id"]) is not int or event_id != raw["event_id"]
                    or world.events[-1].causes != tuple(raw["causes"])):
                raise ValueError("invalid event numbering or duplicate cause")
        if data["next_event_id"] != len(world.events) + 1:
            raise ValueError("invalid next event ID")
        controls = [event for event in world.events if event.kind == "belief_controls"]
        if len(controls) != 1 or controls[0].phase != "setup" or controls[0].data != asdict(beliefs):
            raise ValueError("snapshot and log belief controls differ")
        close_days = [event.day for event in world.events if event.kind == "day_summary"]
        if close_days != list(range(1, world.day + 1)):
            raise ValueError("invalid completed-day history")
        last = world.events[-1]
        if last.day != world.day or (world.day and last.kind != "day_summary"):
            raise ValueError("snapshot must end at a day boundary")
        world.validate_state()
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
