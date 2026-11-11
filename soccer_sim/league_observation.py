"""Environment-side fatigue measurements, separate from agent decisions.

Noise amplitudes are game hypotheses. Zero is an explicit perfect-measurement
control; clipping may equal a true boundary even with nonzero noise.
"""
from math import isfinite

from .match_2d import clamp, round6, uniform_from_key


def observe_fatigue(*, true_fatigue: float, noise: float, seed: int, day: int,
                    observer_id: str, player_id: str, context: str, centered: bool = False, shifted: bool = False) -> float:
    for name, value, maximum in (("fatigue", true_fatigue, 0.95), ("noise", noise, 0.95)):
        if type(value) not in (int, float) or not isfinite(value) or not 0 <= value <= maximum:
            raise ValueError(f"invalid {name}")
    u = uniform_from_key(seed, day, f"fatigue:{context}", f"{observer_id}:{player_id}")
    # T036: u is in [-1, 1]; legacy (2u-1) has mean -1 (error in [-3, 1]*noise), centered is symmetric. Same single draw either way.
    error = u * noise * (2 if shifted else 1) if centered else (2 * u - 1) * noise  # T037: shifted (with centered) = u*2*noise, legacy spread with mean 0
    return round6(clamp(true_fatigue + error, 0.0, 0.95))
