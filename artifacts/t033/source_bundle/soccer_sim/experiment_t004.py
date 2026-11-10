"""Paired gain-target interventions with direct action comparisons."""
from dataclasses import asdict, replace
from pathlib import Path
from random import Random
from statistics import mean
from time import perf_counter

from .experiment_t002 import (
    BOOTSTRAP_REPLICATES, SEED_SETS, bootstrap_effects, clipping_probe, mismatch_rate,
    stress_measurement, summarize_differences, trajectories,
)
from .experiment_t003 import ADAPTIVE, FIXED, trust_metrics
from .experiments import aggregate, measure
from .observation import observed_gain, update_player_gain
from .policy import decide_player
from .simulation import advance, new_world
from .state import METHODS, PlayerMind, PlayerObservation, SelfObservation
from .storage import DAILY_SLOTS, digest, event_lines, identity, runtime, snapshot, write_json

DAYS = 84
BOOTSTRAP_SEED = 4004
SIGNED = replace(FIXED.beliefs, gain_target_mode="signed")
CONDITIONS = {
    "clipped_fixed": FIXED,
    "clipped_adaptive": ADAPTIVE,
    "signed_fixed": replace(FIXED, beliefs=SIGNED),
    "signed_adaptive": replace(ADAPTIVE, beliefs=SIGNED),
}
SIMPLE_PAIRS = {
    "signed_minus_clipped_fixed": ("signed_fixed", "clipped_fixed"),
    "signed_minus_clipped_adaptive": ("signed_adaptive", "clipped_adaptive"),
    "adaptive_minus_fixed_clipped": ("clipped_adaptive", "clipped_fixed"),
    "adaptive_minus_fixed_signed": ("signed_adaptive", "signed_fixed"),
}
CONTRASTS = {label: ((left, 1), (right, -1)) for label, (left, right) in SIMPLE_PAIRS.items()}
CONTRASTS["gain_target_trust_interaction"] = (
    ("signed_adaptive", 1), ("clipped_adaptive", -1),
    ("signed_fixed", -1), ("clipped_fixed", 1),
)

GAIN_OUTCOMES = (
    "training_count", "negative_gain_updates", "negative_gain_update_rate",
    "negative_observation_count", "negative_observation_rate", "clipping_total",
    "clipping_per_training", "all_training_self_mean_squared_error",
    "all_training_self_mean_signed_error",
)
TRUST_OUTCOMES = (
    "final_trust", "trust_change", "feedback_count", "trust_update_attempts",
    "trust_increases", "trust_decreases", "acceptance_rate",
    "mean_self_squared_error", "mean_coach_squared_error",
)


def gain_metrics(world):
    experiences = {player_id: [] for player_id in world.players}
    for event in world.events:
        if event.kind == "player_gain_belief":
            row = event.data
            error = row["expected_before"] - row["signed_observed_gain"]
            experiences[event.target].append({
                "day": event.day, "gain_event_id": event.event_id,
                "method": row["method"], "gain_target_mode": row["gain_target_mode"],
                "signed_observed_gain": row["signed_observed_gain"],
                "observed_target": row["measured_gain"], "prediction_before": row["expected_before"],
                "prediction_after": row["expected_after"], "signed_error": error,
                "squared_error": error ** 2,
            })
    result = {}
    for player_id, rows in experiences.items():
        n = len(rows)
        negative_gains = sum(row["prediction_after"] < 0 for row in rows)
        negative_observations = sum(row["signed_observed_gain"] < 0 for row in rows)
        clipping = sum(row["observed_target"] - row["signed_observed_gain"] for row in rows)
        result[player_id] = {
            "gain_experiences": rows, "training_count": n,
            "negative_gain_updates": negative_gains,
            "negative_gain_update_rate": negative_gains / n if n else None,
            "negative_observation_count": negative_observations,
            "negative_observation_rate": negative_observations / n if n else None,
            "clipping_total": clipping, "clipping_per_training": clipping / n if n else None,
            "all_training_self_mean_squared_error": mean(row["squared_error"] for row in rows) if n else None,
            "all_training_self_mean_signed_error": mean(row["signed_error"] for row in rows) if n else None,
        }
    return result


def measure_condition(world, elapsed, *, include_training_samples=True):
    record = measure(world, elapsed, sum(len(line) for line in event_lines(world)))
    record["actions"], _ = trajectories(world)
    record["mean_skill_gain"] = mean(row["skill_gain"] for row in record["players"].values())
    gains = gain_metrics(world)
    trust = trust_metrics(world)
    if sum(row["feedback_count"] for row in trust.values()) != record["accepted_count"]:
        raise ValueError("each accepted proposal must have one feedback event")
    for player_id, row in record["players"].items():
        player_gain = gains[player_id]
        if player_gain["training_count"] != sum(row["action_counts"][method] for method in METHODS):
            raise ValueError("each executed training must have one prediction record")
        if not include_training_samples:
            del player_gain["gain_experiences"]
        player_trust = trust[player_id]
        proposals = row["proposals"]
        row.update(player_gain, **player_trust,
                   acceptance_rate=player_trust["feedback_count"] / proposals if proposals else None,
                   remaining_to_cap=world.players[player_id].cap - row["final_skill"])
    return record


def outcomes(run):
    result = {
        "mean_skill_gain": run["mean_skill_gain"],
        "final_skill_gap": run["final_skill_gap"],
        "acceptance_rate": run["acceptance_rate"],
        **{f"action_share.{action}": share for action, share in run["player_action_share"].items()},
        **{f"coach_slots.{action}": count for action, count in run["coach_action_counts"].items()},
    }
    for player_id, row in run["players"].items():
        for metric in (*GAIN_OUTCOMES, *TRUST_OUTCOMES, "proposals", "observations",
                       "skill_gain", "final_skill", "remaining_to_cap"):
            result[f"{player_id}.{metric}"] = row[metric]
        for method, gain in row["expected_gains"].items():
            result[f"{player_id}.expected_gain.{method}"] = gain
        for action, count in row["action_counts"].items():
            result[f"{player_id}.action_share.{action}"] = count / run["days"]
    return result


def aggregate_condition(runs, measure_outcomes=outcomes):
    records = [measure_outcomes(run) for run in runs]
    eligible = {metric: [record[metric] for record in records if record[metric] is not None]
                for metric in records[0]}
    totals = {player_id: {
        key: sum(run["players"][player_id][key] for run in runs)
        for key in ("training_count", "negative_gain_updates", "negative_observation_count",
                    "clipping_total", "proposals", "feedback_count", "trust_update_attempts")
    } for player_id in runs[0]["players"]}
    return {**aggregate(runs), "mean_run_outcomes": {
        metric: mean(values) if values else None for metric, values in eligible.items()
    }, "eligible_run_counts": {metric: len(values) for metric, values in eligible.items()},
        "player_totals": totals}


def action_disagreement(left, right):
    """Compare actual actions by individual/day and coach day/slot."""
    rates = {actor: mismatch_rate(actions, right[actor]) for actor, actions in left.items()}
    return {**rates, "player": mean(rate for actor, rate in rates.items() if actor != "coach")}


def action_effects(records, seeds, pairs=SIMPLE_PAIRS, bootstrap_seed=BOOTSTRAP_SEED):
    random = Random(bootstrap_seed)
    n = len(seeds)
    resamples = [tuple(random.randrange(n) for _ in seeds) for _ in range(BOOTSTRAP_REPLICATES)]
    results = {}
    for label, (left, right) in pairs.items():
        paired = {seed: action_disagreement(a["actions"], b["actions"])
                  for seed, a, b in zip(seeds, records[left], records[right], strict=True)}
        results[label] = {
            actor: summarize_differences({seed: row[actor] for seed, row in paired.items()}, resamples)
            for actor in paired[seeds[0]]
        }
    return results


def compare_seed_set(seeds):
    records = {name: [] for name in CONDITIONS}
    for seed in seeds:
        for name, config in CONDITIONS.items():
            start = perf_counter()
            world = advance(new_world(seed, config), DAYS)
            records[name].append(measure_condition(world, perf_counter() - start))
    conditions = {
        name: {"config": asdict(CONDITIONS[name]), "runs": runs, "aggregate": aggregate_condition(runs)}
        for name, runs in records.items()
    }
    return {"seeds": list(seeds), "conditions": conditions,
            "paired_effects": bootstrap_effects(conditions, seeds, outcomes, CONTRASTS, BOOTSTRAP_SEED),
            "action_disagreement": action_effects(records, seeds)}


def gain_choice_probe():
    """Give both minds the same negative experience of A, then the same private input."""
    mind = PlayerMind(0.4, (0.05, 0.02), (0.0, 0.0), 0.45, 0.47, (1, 1), 1, 1)
    observation = PlayerObservation(SelfObservation(3, 0.4, 0.05), None)
    before, after = 0.5, 0.4
    results = {}
    for mode in ("clipped", "signed"):
        target = observed_gain(before, after, mode)
        updated = update_player_gain(mind, "A", target, FIXED.gain_alpha, 2)
        results[mode] = {"target": target, "expected_gains": updated.expected_gains,
                         "decision": decide_player(observation, updated, FIXED.policy).payload()}
    return {"signed_observation": after - before, "initial_expected_gains": mind.expected_gains,
            "next_observation": asdict(observation), "results": results}


def without_gain_target_mode(world):
    data = snapshot(world)
    del data["config"]["beliefs"]["gain_target_mode"]
    data["events"] = [
        {**event, "data": {key: value for key, value in event["data"].items()
                           if key != "gain_target_mode"}}
        for event in data["events"]
    ]
    return data


def no_noise_probe():
    runs = {}
    for name, config in CONDITIONS.items():
        noiseless = replace(config, self_noise=0.0, fatigue_noise=0.0, coach_noise=0.0)
        world = advance(new_world(17, noiseless), DAYS)
        record = measure_condition(world, 0.0)
        runs[name] = {"outcomes": outcomes(record), "actions": record["actions"],
                      "without_mode_sha256": digest(without_gain_target_mode(world))}
    pairs = {}
    for label in ("signed_minus_clipped_fixed", "signed_minus_clipped_adaptive"):
        left, right = (runs[name] for name in SIMPLE_PAIRS[label])
        pairs[label] = {
            "same_snapshot_excluding_mode": left["without_mode_sha256"] == right["without_mode_sha256"],
            "action_disagreement": action_disagreement(left["actions"], right["actions"]),
        }
    return {"seed": 17, "days": DAYS, "noise_amplitude": 0.0, "runs": runs, "comparisons": pairs}


def run_t004(out):
    out = Path(out)
    sets = {name: compare_seed_set(seeds) for name, seeds in SEED_SETS.items()}
    report = {
        "suite": "t004", "versions": identity(), "runtime": runtime(), "days_per_run": DAYS,
        "daily_slots": DAILY_SLOTS,
        "run_count": sum(len(CONDITIONS) * len(seeds) for seeds in SEED_SETS.values()),
        "bootstrap": {"seed": BOOTSTRAP_SEED, "rng": "python-random-mt19937",
                      "replicates": BOOTSTRAP_REPLICATES, "unit": "paired seed",
                      "interval": "95% inclusive percentile",
                      "missing": "omit seeds missing a run-level measure in any contrasted condition",
                      "numeric_contrasts": CONTRASTS, "direct_action_pairs": SIMPLE_PAIRS},
        "prediction_errors": {"signed_error": "prediction before action minus signed observation delta",
                              "all_training": "all executed training, excluding rest",
                              "advice": "mean_self_squared_error and mean_coach_squared_error "
                                        "restrict to adopted and executed advice"},
        "sets": sets,
        "probes": {"gain_choice": gain_choice_probe(), "no_noise": no_noise_probe(),
                   "zero_true_gain": clipping_probe(skill=0.5)},
        "performance_signed_adaptive": stress_measurement(CONDITIONS["signed_adaptive"]),
    }
    write_json(out / "comparison.json", report)
    write_json(out / "summary.json", {
        "versions": report["versions"], "run_count": report["run_count"],
        "performance_signed_adaptive": report["performance_signed_adaptive"],
        "sets": {label: {name: result["aggregate"] for name, result in seed_set["conditions"].items()}
                 for label, seed_set in sets.items()},
    })
    return report
