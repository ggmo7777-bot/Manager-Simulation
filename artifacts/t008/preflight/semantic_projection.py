"""Compare complete multi-coach T007 state and events, excluding package source_sha256 only."""
from copy import deepcopy
from soccer_sim import multi_storage
from soccer_sim.storage import digest


def projection(world):
    data = deepcopy(multi_storage.snapshot(world))
    data["versions"] = {k: v for k, v in data["versions"].items() if k != "source_sha256"}
    return digest(data)
