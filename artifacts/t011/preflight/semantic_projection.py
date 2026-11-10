from __future__ import annotations
import copy
from soccer_sim import transfer_loop as tl
from soccer_sim.storage import digest


def projection(world: tl.TransferWorldState) -> str:
    payload = copy.deepcopy(tl.snapshot_payload(world))
    payload["identity"].pop("source_sha256", None)
    return digest(payload)
