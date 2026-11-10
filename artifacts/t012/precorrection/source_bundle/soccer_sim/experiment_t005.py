"""Compare learning and frozen forecasts under the same paid observations."""
from collections import Counter
from dataclasses import asdict, replace
from pathlib import Path
from statistics import mean
from time import perf_counter

from .advice import (
    COACH_RATE_PRIOR, COACH_RESIDUAL_ALPHA, coach_experience, coach_forecast, evaluate_feedback,
    remember_coach_experience,
)
from .experiment_t002 import (
    BOOTSTRAP_REPLICATES, SEED_SETS, bootstrap_effects, stress_measurement, trajectories,
)
from .experiment_t004 import (
    CONDITIONS as T004_CONDITIONS, DAYS, action_disagreement, action_effects, aggregate_condition,
    measure_condition, outcomes as player_outcomes, without_gain_target_mode,
)
from .policy import decide_player
from .simulation import advance, new_world
from .state import METHODS, CoachAdjustment, PlayerMind, PlayerObservation, Proposal, SelfObservation
from .storage import DAILY_SLOTS, digest, identity, runtime, write_json

BOOTSTRAP_SEED = 5005


def paid_sessions(config, learn):
    return replace(config, beliefs=replace(config.beliefs, coach_session_observation=True,
                                           coach_forecast_learning=learn))


CONDITIONS = {
    "coach_frozen_clipped": paid_sessions(T004_CONDITIONS["clipped_adaptive"], False),
    "coach_learning_clipped": paid_sessions(T004_CONDITIONS["clipped_adaptive"], True),
    "coach_frozen_signed": paid_sessions(T004_CONDITIONS["signed_adaptive"], False),
    "coach_learning_signed": paid_sessions(T004_CONDITIONS["signed_adaptive"], True),
}
SIMPLE_PAIRS = {
    "learning_minus_frozen_clipped": ("coach_learning_clipped", "coach_frozen_clipped"),
    "learning_minus_frozen_signed": ("coach_learning_signed", "coach_frozen_signed"),
    "signed_minus_clipped_frozen": ("coach_frozen_signed", "coach_frozen_clipped"),
    "signed_minus_clipped_learning": ("coach_learning_signed", "coach_learning_clipped"),
}
CONTRASTS = {label: ((left, 1), (right, -1)) for label, (left, right) in SIMPLE_PAIRS.items()}
CONTRASTS["coach_learning_gain_target_interaction"] = (
    ("coach_learning_signed", 1), ("coach_frozen_signed", -1),
    ("coach_learning_clipped", -1), ("coach_frozen_clipped", 1),
)


def observed_errors(rows):
    return {
        "observed_base_mse": mean(row["base_squared_error"] for row in rows) if rows else None,
        "observed_forecast_mse": mean(row["forecast_squared_error"] for row in rows) if rows else None,
        "observed_forecast_minus_base_mse": mean(
            row["forecast_squared_error"] - row["base_squared_error"] for row in rows
        ) if rows else None,
    }


def coach_metrics(world):
    targets = tuple(world.players)
    pre, post, opportunities, used = Counter(), Counter(), Counter(), Counter()
    sessions = {target: [] for target in targets}
    adjustments = {item.player_id: item for item in world.coach.adjustments}
    for event in world.events:
        target, row = event.target, event.data
        if event.kind == "coach_observation":
            pre[target] += 1
        if event.kind == "coach_post_observation":
            post[target] += 1
        if event.kind == "coach_method_experience":
            sessions[target].append({"day": event.day, "experience_id": event.event_id, **row})
        if event.kind == "coach_action" and row["action"] == "propose":
            method = row["method"]
            opportunities[target, method] += 1
            memory = world.events[row["residual_memory_id"] - 1]
            used[target, method] += memory.kind == "coach_method_experience"
    result = {}
    for target in targets:
        rows = sessions[target]
        if pre[target] != post[target]:
            raise ValueError("each paid session must have one pre and one post observation")
        result[target] = {
            "paid_sessions": pre[target], "post_samples": post[target],
            "eligible_training": len(rows), "observed_rest": post[target] - len(rows),
            "updates": sum(row["updates_enabled"] for row in rows),
            "corrected_proposals": sum(used[target, method] for method in METHODS),
            **observed_errors(rows), "training_experiences": rows, "by_method": {},
        }
        for index, method in enumerate(METHODS):
            method_rows = [row for row in rows if row["method"] == method]
            result[target]["by_method"][method] = {
                "eligible_training": len(method_rows),
                "updates": sum(row["updates_enabled"] for row in method_rows),
                "final_residual": adjustments[target].residuals[index],
                "proposals": opportunities[target, method],
                "corrected_proposals": used[target, method],
                **observed_errors(method_rows),
            }
    return result


def measure_run(world, elapsed):
    result = measure_condition(world, elapsed, include_training_samples=False)
    result["coach_learning"] = coach_metrics(world)
    if sum(row["paid_sessions"] for row in result["coach_learning"].values()) != result["coach_action_counts"]["observe"]:
        raise ValueError("paid sessions must match the number of observation slots")
    return result


def outcomes(run):
    result = player_outcomes(run)
    for target, row in run["coach_learning"].items():
        for key, value in row.items():
            if key not in ("training_experiences", "by_method"):
                result[f"coach.{target}.{key}"] = value
        for method, metrics in row["by_method"].items():
            for key, value in metrics.items():
                result[f"coach.{target}.{method}.{key}"] = value
    return result


def compare_seed_set(seeds):
    records = {name: [] for name in CONDITIONS}
    for seed in seeds:
        for name, config in CONDITIONS.items():
            start = perf_counter()
            world = advance(new_world(seed, config), DAYS)
            records[name].append(measure_run(world, perf_counter() - start))
    conditions = {
        name: {"config": asdict(CONDITIONS[name]), "runs": runs,
               "aggregate": aggregate_condition(runs, outcomes)}
        for name, runs in records.items()
    }
    return {"seeds": list(seeds), "conditions": conditions,
            "paired_effects": bootstrap_effects(conditions, seeds, outcomes, CONTRASTS, BOOTSTRAP_SEED),
            "action_disagreement": action_effects(records, seeds, SIMPLE_PAIRS, BOOTSTRAP_SEED)}


def forecast_choice_probe():
    """Change only the coach forecast before two otherwise identical choices."""
    method = "B"
    prior = CoachAdjustment("p1", (0.0, 0.0), (1, 1))
    base = coach_forecast(method, 0.4, COACH_RATE_PRIOR, prior)
    mind = PlayerMind(0.4, (0.05, 0.05), (0.0, 0.0), 0.45, 0.47, (1, 1), 1, 1)
    config = CONDITIONS["coach_learning_signed"]
    results = {}
    for learn in (False, True):
        coach_pre, coach_post = 0.50, 0.44
        result = coach_experience(coach_pre, coach_post, base, learn)
        updated = remember_coach_experience(prior, method, result["residual_after"], 2) if learn else prior
        proposed = coach_forecast(method, 0.4, COACH_RATE_PRIOR, updated)
        view = PlayerObservation(SelfObservation(3, 0.4, 0.0), Proposal(4, method, proposed.expected_gain))
        first = decide_player(view, mind, config.policy)
        feedback = evaluate_feedback(mind.trust, 0.4, 0.4, mind.expected_gains[1],
                                     proposed.expected_gain, config.trust)
        changed = replace(mind, trust=feedback.trust_after, trust_memory_id=5)
        next_view = PlayerObservation(SelfObservation(6, 0.4, 0.0), Proposal(7, method, proposed.expected_gain))
        second = decide_player(next_view, changed, config.policy)
        results["learning" if learn else "frozen"] = {
            "coach_pre": coach_pre, "coach_post": coach_post, "initial_forecast": asdict(base),
            "coach_experience": result, "later_forecast": asdict(proposed),
            "first_decision": first.payload(), "player_feedback": asdict(feedback),
            "next_decision": second.payload(),
        }
    return {"initial_trust": mind.trust, "player_pre_and_post": [0.4, 0.4], "results": results}


def control_probe(*, fixed_trust=False, no_noise=False):
    runs = {}
    for name, config in CONDITIONS.items():
        if fixed_trust:
            config = replace(config, trust=replace(config.trust, enabled=False))
        if no_noise:
            config = replace(config, self_noise=0.0, fatigue_noise=0.0, coach_noise=0.0)
        world = advance(new_world(17, config), DAYS)
        actions, states = trajectories(world)
        scores = [[event.day, event.actor, event.data["chosen"], event.data["candidates"],
                   event.data["proposal_response"]] for event in world.events if event.kind == "decision"]
        forecasts = [[event.day, event.target, event.data["method"], event.data["expected_gain"]]
                     for event in world.events
                     if event.kind == "coach_action" and event.data["action"] == "propose"]
        runs[name] = {"config": asdict(config), "outcomes": outcomes(measure_run(world, 0.0)),
                      "actions": actions, "states_sha256": digest(states), "scores_sha256": digest(scores),
                      "proposal_forecasts": forecasts,
                      "snapshot_without_gain_mode_sha256": digest(without_gain_target_mode(world))}
    comparisons = {}
    for label, (left_name, right_name) in SIMPLE_PAIRS.items():
        left, right = runs[left_name], runs[right_name]
        comparisons[label] = {
            "action_disagreement": action_disagreement(left["actions"], right["actions"]),
            "same_truth_history": left["states_sha256"] == right["states_sha256"],
            "same_decisions_and_scores": left["scores_sha256"] == right["scores_sha256"],
            "same_proposal_forecasts": left["proposal_forecasts"] == right["proposal_forecasts"],
            "same_snapshot_excluding_gain_mode": (
                left["snapshot_without_gain_mode_sha256"] == right["snapshot_without_gain_mode_sha256"]
            ),
        }
    return {"seed": 17, "days": DAYS, "runs": runs, "comparisons": comparisons}


def run_t005(out):
    out = Path(out)
    sets = {name: compare_seed_set(seeds) for name, seeds in SEED_SETS.items()}
    report = {
        "suite": "t005", "versions": identity(), "runtime": runtime(), "days_per_run": DAYS,
        "daily_slots": DAILY_SLOTS,
        "run_count": sum(len(CONDITIONS) * len(seeds) for seeds in SEED_SETS.values()),
        "coach_residual_alpha": COACH_RESIDUAL_ALPHA,
        "bootstrap": {"seed": BOOTSTRAP_SEED, "rng": "python-random-mt19937",
                      "replicates": BOOTSTRAP_REPLICATES, "unit": "paired seed",
                      "interval": "95% inclusive percentile",
                      "missing": "omit seeds missing a run-level measure in any contrasted condition",
                      "numeric_contrasts": CONTRASTS, "direct_action_pairs": SIMPLE_PAIRS},
        "prediction_errors": {
            "observed_forecast_mse": "coach-private signed delta for paid, observed training",
            "mean_coach_squared_error": "player-private signed delta for adopted and executed advice",
            "corrected_proposals": "forecast cites a learned method correction, even when it equals zero",
        },
        "sets": sets,
        "probes": {"forecast_to_choice": forecast_choice_probe(),
                   "fixed_trust": control_probe(fixed_trust=True),
                   "no_noise": control_probe(no_noise=True)},
        "performance_learning_signed": stress_measurement(CONDITIONS["coach_learning_signed"]),
    }
    write_json(out / "comparison.json", report)
    write_json(out / "summary.json", {
        "versions": report["versions"], "run_count": report["run_count"],
        "performance_learning_signed": report["performance_learning_signed"],
        "sets": {label: {name: result["aggregate"] for name, result in seed_set["conditions"].items()}
                 for label, seed_set in sets.items()},
    })
    return report
