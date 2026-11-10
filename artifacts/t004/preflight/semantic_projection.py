"""Review-only projection shared by frozen T003 and later compatibility checks."""
from dataclasses import asdict
from hashlib import sha256
import json

REFERENCE_FIELDS = {
    "last_state_id", "gain_memory_ids", "skill_memory_id", "trust_memory_id",
    "recency_memory_id", "forecast_memory_id", "observation_ids", "memory_ids",
    "self_gain_memory_id", "prior_trust_memory_id",
}


def without_references(value):
    if isinstance(value, dict):
        return {key: without_references(item) for key, item in value.items()
                if key not in REFERENCE_FIELDS}
    if isinstance(value, (list, tuple)):
        return [without_references(item) for item in value]
    return value


def fingerprint(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         allow_nan=False).encode()
    return sha256(encoded).hexdigest()


def projection(world):
    final = without_references({"players": {key: asdict(value) for key, value in world.players.items()},
                                "coach": asdict(world.coach)})
    choices = [[event.day, event.phase, event.actor, event.target,
                without_references(event.data)]
               for event in world.events if event.kind == "decision"]
    trust = [[event.day, event.actor, event.target, without_references(event.data)]
             for event in world.events if event.kind == "advice_feedback"]
    states = [[event.day, event.actor, event.data]
              for event in world.events if event.kind == "state_update"]
    return {"final_state": final, "decisions_sha256": fingerprint(choices),
            "trust_history_sha256": fingerprint(trust),
            "truth_history_sha256": fingerprint(states)}
