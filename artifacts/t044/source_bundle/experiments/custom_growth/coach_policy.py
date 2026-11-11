"""Coach Evaluation and Team Training Decision Engine.

Adapts coach evaluations and instructions to:
1. Team-wide training menu decision (Method A or Method B, or Team Rest).
2. Subjective evaluation per trait (Pace, Pass, Shoot) with observation noise (non-leakage).
3. Adaptive learning of player method gains (discovering Late bloomers and Burst awakenings).
"""

import math
from dataclasses import dataclass, field
from typing import Literal
from experiments.custom_growth.growth_engine import (
    CustomPlayer,
    METHOD_TRAIT_WEIGHTS,
)


@dataclass
class CoachPlayerEval:
    player_id: str
    est_pace: float
    est_passing: float
    est_shooting: float
    est_fatigue: float = 0.10
    # Learned expected gain per method (Coach's subjective belief)
    method_gains: dict[str, float] = field(default_factory=lambda: {"A": 0.005, "B": 0.005})
    last_seen_day: int = 0

    def composite(self) -> float:
        return (self.est_pace + self.est_passing + self.est_shooting) / 3.0


class TeamCoach:
    def __init__(
        self,
        coach_id: str,
        roster: list[str],
        preferred_style: Literal["balanced", "possession", "direct"] = "balanced",
        obs_noise: float = 0.03,
        learn_rate_skill: float = 0.25,
        learn_rate_gain: float = 0.20,
    ):
        self.coach_id = coach_id
        self.roster = roster
        self.preferred_style = preferred_style
        self.obs_noise = obs_noise
        self.learn_rate_skill = learn_rate_skill
        self.learn_rate_gain = learn_rate_gain

        # Initial prior evaluation for each player (noisy reading around 0.30)
        self.evaluations: dict[str, CoachPlayerEval] = {
            pid: CoachPlayerEval(
                player_id=pid,
                est_pace=0.30,
                est_passing=0.30,
                est_shooting=0.30,
                est_fatigue=0.10,
            )
            for pid in roster
        }

    def decide_team_training_menu(self, day: int) -> dict[str, any]:
        """Decide today's team training menu: 'A' (Passing/Tech) or 'B' (Pace/Physical).
        
        Evaluates squad-wide needs, expected yield from learned method gains,
        and tactical preferences.
        """
        # 1. Check team average fatigue
        avg_fatigue = sum(ev.est_fatigue for ev in self.evaluations.values()) / len(self.evaluations)
        if avg_fatigue >= 0.70:
            return {"action": "team_rest", "reason": f"team_fatigue_too_high_{avg_fatigue:.2f}"}

        # 2. Evaluate squad capability deficits
        avg_pace = sum(ev.est_pace for ev in self.evaluations.values()) / len(self.evaluations)
        avg_pass = sum(ev.est_passing for ev in self.evaluations.values()) / len(self.evaluations)
        avg_shoot = sum(ev.est_shooting for ev in self.evaluations.values()) / len(self.evaluations)

        need_pace = max(0.0, 0.80 - avg_pace)
        need_pass = max(0.0, 0.80 - avg_pass)
        need_shoot = max(0.0, 0.80 - avg_shoot)

        # 3. Score Method A vs Method B
        # Method A weights: Pace 0.45, Pass 1.45, Shoot 1.10
        # Method B weights: Pace 1.45, Pass 0.45, Shoot 1.10
        score_a = 0.45 * need_pace + 1.45 * need_pass + 1.10 * need_shoot
        score_b = 1.45 * need_pace + 0.45 * need_pass + 1.10 * need_shoot

        # Add coach's learned expected gain bonus across the squad
        expected_gain_a = sum(ev.method_gains["A"] for ev in self.evaluations.values())
        expected_gain_b = sum(ev.method_gains["B"] for ev in self.evaluations.values())
        score_a += expected_gain_a * 15.0
        score_b += expected_gain_b * 15.0

        # Add tactical style bias
        if self.preferred_style == "possession":
            score_a += 0.08
        elif self.preferred_style == "direct":
            score_b += 0.08

        chosen = "A" if score_a >= score_b else "B"
        return {
            "action": chosen,
            "score_a": round(score_a, 4),
            "score_b": round(score_b, 4),
            "avg_pace": round(avg_pace, 3),
            "avg_pass": round(avg_pass, 3),
            "avg_shoot": round(avg_shoot, 3),
            "expected_gain_a": round(expected_gain_a, 4),
            "expected_gain_b": round(expected_gain_b, 4),
        }

    def observe_and_update(
        self,
        day: int,
        executed_menu: str,
        players: dict[str, CustomPlayer],
        deltas_by_player: dict[str, dict[str, float]],
        rng_seed: int = 100,
    ):
        """Coach observes players after training and updates subjective evaluations and method gains."""
        for pid, player in players.items():
            if pid not in self.evaluations:
                continue
            ev = self.evaluations[pid]
            ev.last_seen_day = day

            # Observe fatigue with noise
            ev.est_fatigue = round(
                max(0.0, min(0.95, player.fatigue + (hash((pid, day, "fatigue")) % 100 / 1000.0 - 0.05))),
                3,
            )

            # Observe traits with noise
            noise_p = (hash((pid, day, "p")) % 100 / 1000.0 - 0.05)
            noise_pass = (hash((pid, day, "pass")) % 100 / 1000.0 - 0.05)
            noise_s = (hash((pid, day, "s")) % 100 / 1000.0 - 0.05)

            obs_p = max(0.15, min(player.cap, player.traits["pace"].skill + noise_p))
            obs_pass = max(0.15, min(player.cap, player.traits["passing"].skill + noise_pass))
            obs_shoot = max(0.15, min(player.cap, player.traits["shooting"].skill + noise_s))

            ev.est_pace = round((1.0 - self.learn_rate_skill) * ev.est_pace + self.learn_rate_skill * obs_p, 4)
            ev.est_passing = round((1.0 - self.learn_rate_skill) * ev.est_passing + self.learn_rate_skill * obs_pass, 4)
            ev.est_shooting = round((1.0 - self.learn_rate_skill) * ev.est_shooting + self.learn_rate_skill * obs_shoot, 4)

            # Update learned method gain from observed delta
            if executed_menu in ("A", "B") and pid in deltas_by_player:
                p_deltas = deltas_by_player[pid]
                obs_gain = sum(p_deltas.values()) / len(p_deltas)
                old_gain = ev.method_gains[executed_menu]
                ev.method_gains[executed_menu] = round(
                    (1.0 - self.learn_rate_gain) * old_gain + self.learn_rate_gain * obs_gain,
                    5,
                )
