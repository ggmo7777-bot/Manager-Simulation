from __future__ import annotations
import copy
from soccer_sim import selection_loop as sl
from soccer_sim.storage import digest

def projection(world: sl.SelectionWorldState) -> str:
    payload = copy.deepcopy(sl.snapshot_payload(world))
    payload["identity"].pop("source_sha256", None)
    return digest(payload)
