"""Compare complete single-coach T006 state and events, excluding package identity only."""
from copy import deepcopy
from soccer_sim.storage import digest, snapshot


def projection(world):
    data = deepcopy(snapshot(world))
    data.pop("versions")
    return digest(data)
