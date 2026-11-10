"""Channels from world truth to private, lossy observations."""
import hashlib
import json
from dataclasses import replace

from .state import METHODS, SelfObservation

RNG_VERSION = "sha256-keyed-uniform53-v1"


def clamp(value):
    return max(0.0, min(1.0, value))


def signal(value, amplitude, seed, channel, day, player_id):
    key = (channel, day, player_id, 0)
    encoded = json.dumps((RNG_VERSION, seed, *key), separators=(",", ":")).encode()
    digest = hashlib.sha256(encoded).digest()
    uniform = (int.from_bytes(digest[:8], "big") >> 11) / (1 << 53)
    return clamp(value + amplitude * (2 * uniform - 1)), key


def observe_self(world, player, day, phase):
    channel = "self-pre" if phase == "morning" else "self-post"
    value, skill_key = signal(player.skill, world.config.self_noise, world.seed,
                              channel, day, player.player_id)
    data = {"source": channel, "skill_signal": value, "skill_key": skill_key}
    if phase == "morning":
        fatigue, key = signal(player.fatigue, world.config.fatigue_noise, world.seed,
                              "felt-fatigue", day, player.player_id)
        event_id = world.record(day, phase, "self_observation", player.player_id,
                                player.player_id, (player.last_state_id,),
                                **data, fatigue_signal=fatigue, fatigue_key=key)
        return SelfObservation(event_id, value, fatigue)
    event_id = world.record(day, phase, "self_observation", player.player_id,
                            player.player_id, (player.last_state_id,), **data)
    return event_id, value


def ema(previous, observed, alpha):
    return previous + alpha * (observed - previous)


def observed_gain(before, after, mode="clipped"):
    if mode == "signed":
        return after - before
    if mode == "clipped":
        return clamp(after - before)
    raise ValueError("invalid gain target mode")


def update_player_gain(mind, method, measured, alpha, memory_id):
    index = METHODS.index(method)
    expected = list(mind.expected_gains)
    expected[index] = ema(expected[index], measured, alpha)
    memories = list(mind.gain_memory_ids)
    memories[index] = memory_id
    return replace(mind, expected_gains=tuple(expected), gain_memory_ids=tuple(memories))
