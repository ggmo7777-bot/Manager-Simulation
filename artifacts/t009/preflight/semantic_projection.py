"""Compare complete T008 match_2d state and events, excluding package source_sha256 only."""
from copy import deepcopy
from soccer_sim import match_2d as m2d
from soccer_sim.storage import digest


def projection(world):
    data = deepcopy(m2d.snapshot_payload(world))
    data["identity"] = {k: v for k, v in data["identity"].items() if k != "source_sha256"}
    return digest(data)
