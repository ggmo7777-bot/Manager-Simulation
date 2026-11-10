"""Compare complete T005 state/history, excluding only explicit version and new mode labels."""
from copy import deepcopy
from soccer_sim.storage import digest, snapshot


def projection(world):
    data = deepcopy(snapshot(world))
    data.pop("versions")
    data["config"]["policy"].pop("coach_method_mode", None)
    for event in data["events"]:
        event["data"].pop("policy_version", None)
    return digest(data)
