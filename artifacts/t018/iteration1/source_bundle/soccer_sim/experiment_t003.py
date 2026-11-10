"""Paired trust interventions and a controlled trust-to-choice probe."""
from dataclasses import asdict, replace
from pathlib import Path
from statistics import mean
from time import perf_counter

from .advice import evaluate_feedback
from .experiment_t002 import (
    BOOTSTRAP_REPLICATES, SEED_SETS, bootstrap_effects, mismatch_rate, outcomes as training_outcomes,
    stress_measurement, trajectories,
)
from .experiments import aggregate, measure
from .policy import decide_player
from .simulation import advance, new_world
from .state import Config, PlayerMind, PlayerObservation, Proposal, SelfObservation
from .storage import DAILY_SLOTS, event_lines, identity, runtime, write_json

DAYS = 84
BOOTSTRAP_SEED = 3003
FIXED = Config()
ADAPTIVE = replace(FIXED, trust=replace(FIXED.trust, enabled=True))
GAIN_FROZEN = replace(FIXED.beliefs, player_gain=False)
CONDITIONS = {
    "fixed_trust": FIXED,
    "adaptive_trust": ADAPTIVE,
    "fixed_trust_gain_frozen": replace(FIXED, beliefs=GAIN_FROZEN),
    "adaptive_trust_gain_frozen": replace(ADAPTIVE, beliefs=GAIN_FROZEN),
}
CONTRASTS = {
    "adaptive_minus_fixed": (("adaptive_trust", 1), ("fixed_trust", -1)),
    "adaptive_minus_fixed_gain_frozen": (
        ("adaptive_trust_gain_frozen", 1), ("fixed_trust_gain_frozen", -1),
    ),
    "gain_frozen_minus_learning_fixed_trust": (
        ("fixed_trust_gain_frozen", 1), ("fixed_trust", -1),
    ),
    "trust_gain_interaction": (
        ("adaptive_trust_gain_frozen", 1), ("fixed_trust_gain_frozen", -1),
        ("adaptive_trust", -1), ("fixed_trust", 1),
    ),
}
GAIN_ONLY_CONTRAST = "gain_frozen_minus_learning_fixed_trust"
DISAGREEMENT_METRICS = tuple(f"disagreement.{actor}" for actor in ("player", "coach", "p1", "p2"))


def trust_metrics(world):
    curves = {}
    samples = {player_id: [] for player_id in world.players}
    for event in world.events:
        if event.kind == "initial_player":
            curves[event.target] = [event.data["state"]["mind"]["trust"]]
        if event.kind == "advice_feedback":
            samples[event.target].append(event.data)
        if event.kind == "day_summary":
            for player_id, curve in curves.items():
                latest = samples[player_id][-1]["trust_after"] if samples[player_id] else curve[0]
                curve.append(latest)
    result = {}
    for player_id, curve in curves.items():
        feedback = samples[player_id]
        result[player_id] = {
            "daily_trust_day0_to_end": curve,
            "final_trust": curve[-1],
            "trust_change": curve[-1] - curve[0],
            "feedback_count": len(feedback),
            "trust_update_attempts": sum(row["trust_enabled"] for row in feedback),
            "trust_increases": sum(row["trust_change"] > 0 for row in feedback),
            "trust_decreases": sum(row["trust_change"] < 0 for row in feedback),
            "mean_self_squared_error": mean(row["self_loss"] for row in feedback) if feedback else None,
            "mean_coach_squared_error": mean(row["coach_loss"] for row in feedback) if feedback else None,
        }
    return result


def condition_run(config, seed, reference_choices):
    start = perf_counter()
    world = advance(new_world(seed, config), DAYS)
    elapsed = perf_counter() - start
    record = measure(world, elapsed, sum(len(line) for line in event_lines(world)))
    choices, _ = trajectories(world)
    if reference_choices is None:
        reference_choices = choices
    rates = {actor: mismatch_rate(actions, reference_choices[actor])
             for actor, actions in choices.items()}
    # Reuse the T002 outcome projection. For T003 this baseline is matched on G.
    record["baseline_disagreement"] = {**rates, "player": mean(rates[p] for p in world.players)}
    record["mean_skill_gain"] = mean(row["skill_gain"] for row in record["players"].values())
    trust = trust_metrics(world)
    if sum(row["feedback_count"] for row in trust.values()) != record["accepted_count"]:
        raise ValueError("each accepted and executed proposal must have one feedback event")
    for player_id, row in trust.items():
        player_record = record["players"][player_id]
        proposals = player_record["proposals"]
        player_record.update(row, acceptance_rate=row["feedback_count"] / proposals if proposals else None)
    return record, choices


def outcomes(run):
    result = training_outcomes(run)
    for player_id, row in run["players"].items():
        for field in ("final_trust", "trust_change", "feedback_count", "trust_update_attempts",
                      "trust_increases", "trust_decreases", "acceptance_rate", "mean_self_squared_error",
                      "mean_coach_squared_error"):
            result[f"{player_id}.{field}"] = row[field]
    return result


def aggregate_condition(runs):
    records = [outcomes(run) for run in runs]
    averages = {}
    for metric in records[0]:
        values = [record[metric] for record in records if record[metric] is not None]
        averages[metric] = mean(values) if values else None
    return {**aggregate(runs), "mean_run_outcomes": averages}


def compare_seed_set(seeds):
    records = {name: [] for name in CONDITIONS}
    for seed in seeds:
        fixed_choices = {}
        for name, config in CONDITIONS.items():
            gain_updates = config.beliefs.player_gain
            reference = fixed_choices.get(gain_updates)
            record, choices = condition_run(config, seed, reference)
            records[name].append(record)
            if not config.trust.enabled:
                fixed_choices[gain_updates] = choices
    conditions = {
        name: {"config": asdict(CONDITIONS[name]), "runs": runs, "aggregate": aggregate_condition(runs)}
        for name, runs in records.items()
    }
    effects = bootstrap_effects(
        conditions, seeds, outcomes, CONTRASTS, BOOTSTRAP_SEED,
        excluded_metrics={GAIN_ONLY_CONTRAST: DISAGREEMENT_METRICS},
    )
    return {"seeds": list(seeds), "conditions": conditions, "paired_effects": effects}


def trust_choice_probe():
    """Two experienced outcomes of an executed B, followed by the same next choice."""
    mind = PlayerMind(0.4, (0.05, 0.05), (0.0, 0.0), 0.45, 0.47, (1, 1), 1, 1)
    first = PlayerObservation(SelfObservation(2, 0.4, 0.0), Proposal(3, "B", 0.10))
    second = PlayerObservation(SelfObservation(5, 0.4, 0.0), Proposal(6, "B", 0.10))
    decision = decide_player(first, mind, FIXED.policy)
    results = {}
    for label, after in (("coach_closer", 0.50), ("self_closer", 0.45)):
        feedback = evaluate_feedback(mind.trust, first.self_view.skill_signal, after,
                                     mind.expected_gains[1], first.proposal.expected_gain, ADAPTIVE.trust)
        changed = replace(mind, trust=feedback.trust_after, trust_memory_id=4)
        results[label] = {"feedback": asdict(feedback),
                          "next_decision": decide_player(second, changed, FIXED.policy).payload()}
    return {"initial_trust": mind.trust, "first_decision": decision.payload(),
            "self_gain_updates": False, "results": results}


def run_t003(out):
    out = Path(out)
    sets = {name: compare_seed_set(seeds) for name, seeds in SEED_SETS.items()}
    report = {
        "suite": "t003", "versions": identity(), "runtime": runtime(), "days_per_run": DAYS,
        "daily_slots": DAILY_SLOTS,
        "run_count": sum(len(CONDITIONS) * len(seeds) for seeds in SEED_SETS.values()),
        "bootstrap": {"seed": BOOTSTRAP_SEED, "rng": "python-random-mt19937",
                      "replicates": BOOTSTRAP_REPLICATES,
                      "unit": "paired seed", "interval": "95% inclusive percentile",
                      "estimand": "mean paired difference of run-level outcomes",
                      "missing": "omit pairs without eligible advice for acceptance and prediction errors",
                      "contrasts": CONTRASTS},
        "action_disagreement_reference": "fixed trust condition with matching G setting",
        "action_disagreement_contrasts": {
            GAIN_ONLY_CONTRAST: "omitted because the two fixed-trust conditions use different references",
            "trust_gain_interaction": "difference between the two within-G action mismatch rates",
        },
        "prediction_error_scope": "squared loss against private signed signal difference for adopted advice",
        "sets": sets, "probes": {"trust_choice": trust_choice_probe()},
        "performance_adaptive_trust": stress_measurement(ADAPTIVE),
    }
    write_json(out / "comparison.json", report)
    write_json(out / "summary.json", {
        "run_count": report["run_count"],
        "performance_adaptive_trust": report["performance_adaptive_trust"],
        "sets": {label: {name: result["aggregate"] for name, result in seed_set["conditions"].items()}
                 for label, seed_set in sets.items()},
    })
    return report
