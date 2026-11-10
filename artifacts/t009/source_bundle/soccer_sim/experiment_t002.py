"""Factorial belief interventions, paired inference, and isolated T002 probes."""
from collections import Counter
from dataclasses import asdict, replace
from pathlib import Path
from random import Random
from statistics import mean, quantiles, stdev
from tempfile import TemporaryDirectory
from time import perf_counter

from .experiments import CONDITIONS as T001_CONDITIONS, DAYS, aggregate, measure
from .observation import ema, observed_gain, signal
from .policy import decide_coach, decide_player
from .simulation import advance, coach_candidates, new_world
from .state import Assessment, CoachMind, CoachObservation, Config, PlayerMind
from .state import PlayerObservation, SelfObservation
from .storage import DAILY_SLOTS, digest, event_lines, identity, load_snapshot, runtime
from .storage import snapshot, write_json, write_snapshot

BASE = Config()
CONDITIONS = {
    "baseline": BASE,
    "player_gain_frozen": replace(BASE, beliefs=replace(BASE.beliefs, player_gain=False)),
    "coach_skill_frozen": replace(BASE, beliefs=replace(BASE.beliefs, coach_skill=False)),
    "gain_and_coach_frozen": replace(BASE, beliefs=replace(BASE.beliefs, player_gain=False,
                                                         coach_skill=False)),
    "legacy_all_frozen": T001_CONDITIONS["no_belief_updates"],
    "self_skill_frozen": replace(BASE, beliefs=replace(BASE.beliefs, self_skill=False)),
    "recency_frozen": replace(BASE, beliefs=replace(BASE.beliefs, recency=False)),
}
SEED_SETS = {"exploration": range(30), "confirmation": range(100, 130)}
BOOTSTRAP_SEED = 2002
BOOTSTRAP_REPLICATES = 2_000
INTERACTION = ("gain_and_coach_frozen", "player_gain_frozen", "coach_skill_frozen", "baseline")


def trajectories(world):
    """Analysis-only projection with choice order and true skill/fatigue, without event IDs."""
    choices = {player_id: [] for player_id in world.players}
    choices["coach"] = []
    states = []
    for event in world.events:
        if event.kind == "coach_action":
            choices["coach"].append((event.data["action"], event.target, event.data["method"]))
        if event.kind == "player_action":
            choices[event.actor].append(event.data["action"])
        if event.kind == "state_update":
            states.append((event.day, event.actor, event.data["skill_after"],
                           event.data["fatigue_after"]))
    return choices, states


def mismatch_rate(actions, reference):
    return sum(action != expected for action, expected in zip(actions, reference, strict=True)) / len(reference)


def run_condition(config, seed, baseline=None):
    start = perf_counter()
    world = advance(new_world(seed, config), DAYS)
    elapsed = perf_counter() - start
    run = measure(world, elapsed, sum(len(line) for line in event_lines(world)))
    choices, states = trajectories(world)
    reference, reference_states = (choices, states) if baseline is None else baseline
    player_rates = {player_id: mismatch_rate(choices[player_id], reference[player_id])
                    for player_id in world.players}
    run["baseline_disagreement"] = {
        "player": mean(player_rates.values()),
        "coach": mismatch_rate(choices["coach"], reference["coach"]),
        **player_rates,
    }
    run["baseline_truth_trajectory_equal"] = states == reference_states
    run["truth_trajectory_sha256"] = digest(states)
    run["actions_sha256"] = digest(choices)
    run["mean_skill_gain"] = mean(row["skill_gain"] for row in run["players"].values())
    return run, (choices, states)


def outcomes(run):
    """Run-level metrics for paired contrasts, including individual exposure and behavior."""
    result = {
        "mean_skill_gain": run["mean_skill_gain"],
        "final_skill_gap": run["final_skill_gap"],
        "proposal_count": run["proposal_count"],
        "acceptance_rate": run["acceptance_rate"],
        "coach_observation_count": run["coach_action_counts"]["observe"],
        **{f"action_share.{action}": share for action, share in run["player_action_share"].items()},
        **{f"disagreement.{actor}": rate for actor, rate in run["baseline_disagreement"].items()},
    }
    for player_id, row in run["players"].items():
        for metric in ("skill_gain", "self_absolute_error", "coach_absolute_error", "proposals",
                       "observations", "last_observed_day", "remembered_last_observed_day"):
            result[f"{player_id}.{metric}"] = row[metric]
        for method, value in row["expected_gains"].items():
            result[f"{player_id}.expected_gain.{method}"] = value
        for action, count in row["action_counts"].items():
            result[f"{player_id}.action_share.{action}"] = count / run["days"]
    return result


def aggregate_condition(runs):
    rows = [outcomes(run) for run in runs]
    means = {}
    for key in rows[0]:
        values = [row[key] for row in rows if row[key] is not None]
        means[key] = mean(values) if values else None
    return {**aggregate(runs), "mean_run_outcomes": means,
            "truth_trajectory_equal_to_baseline_runs": sum(
                run["baseline_truth_trajectory_equal"] for run in runs)}


def summarize_differences(differences, resamples):
    if not differences:
        return {"pairs": 0, "mean": None, "interval_95": None, "per_seed": {}}
    values = list(differences.values())
    if all(value == values[0] for value in values):
        interval = [values[0], values[0]]
    else:
        boot_means = [sum(values[index] for index in indices) / len(values)
                      for indices in resamples]
        percentiles = quantiles(boot_means, n=40, method="inclusive")
        interval = [percentiles[0], percentiles[-1]]
    return {"pairs": len(values), "mean": mean(values), "interval_95": interval,
            "per_seed": differences}


def paired_effects(conditions, seeds):
    comparisons = {name: ((name, 1), ("baseline", -1))
                   for name in conditions if name != "baseline"}
    comparisons["gain_coach_interaction"] = tuple(zip(INTERACTION, (1, -1, -1, 1)))
    return bootstrap_effects(conditions, seeds, outcomes, comparisons, BOOTSTRAP_SEED)


def bootstrap_effects(conditions, seeds, measure_outcomes, comparisons, bootstrap_seed,
                      *, excluded_metrics=None):
    metrics = {name: {run["seed"]: measure_outcomes(run) for run in result["runs"]}
               for name, result in conditions.items()}
    random = Random(bootstrap_seed)
    resamples_by_count = {}
    results = {}
    excluded_metrics = excluded_metrics or {}
    for label, terms in comparisons.items():
        results[label] = {}
        excluded = excluded_metrics.get(label, ())
        for metric in metrics[terms[0][0]][seeds[0]]:
            if metric in excluded:
                continue
            differences = {}
            for seed in seeds:
                values = [metrics[name][seed][metric] for name, _ in terms]
                if all(value is not None for value in values):
                    differences[seed] = sum(sign * value for value, (_, sign)
                                            in zip(values, terms, strict=True))
            count = len(differences)
            if count not in resamples_by_count:
                resamples_by_count[count] = [
                    tuple(random.randrange(count) for _ in range(count))
                    for _ in range(BOOTSTRAP_REPLICATES)
                ]
            results[label][metric] = summarize_differences(differences, resamples_by_count[count])
    return results


def compare_seed_set(seeds):
    records = {name: [] for name in CONDITIONS}
    for seed in seeds:
        baseline, projection = run_condition(BASE, seed)
        records["baseline"].append(baseline)
        for name, config in CONDITIONS.items():
            if name != "baseline":
                record, _ = run_condition(config, seed, projection)
                records[name].append(record)
    conditions = {
        name: {"config": asdict(CONDITIONS[name]), "aggregate": aggregate_condition(runs),
               "runs": runs} for name, runs in records.items()
    }
    return {"seeds": list(seeds), "conditions": conditions,
            "paired_effects": paired_effects(conditions, seeds)}


def estimate_observed_gains(samples, alpha):
    clipped = 0.0
    signed = 0.0
    for before, after in samples:
        clipped = ema(clipped, observed_gain(before, after), alpha)
        signed = ema(signed, observed_gain(before, after, "signed"), alpha)
    return {"clipped": clipped, "signed": signed}


def clipping_probe(skill=0.4):
    amplitude, alpha = 0.025, 0.5
    steps = 64
    runs = []
    for seed in range(1000):
        samples = [tuple(signal(skill, amplitude, seed, channel, day, "probe")[0]
                         for channel in ("gain-probe-pre", "gain-probe-post"))
                   for day in range(1, steps + 1)]
        runs.append({"seed": seed, **estimate_observed_gains(samples, alpha)})
    aggregate_final = {}
    for method in ("clipped", "signed"):
        values = [row[method] for row in runs]
        cuts = quantiles(values, n=40, method="inclusive")
        aggregate_final[method] = {"mean": mean(values), "sd": stdev(values),
                                   "empirical_95": [cuts[0], cuts[-1]]}
    return {
        "true_skill": skill, "true_gain": 0, "amplitude": amplitude, "initial_expected_gain": 0,
        "steps": steps, "alpha": alpha, "seeds": [0, 999],
        "channels": ["gain-probe-pre", "gain-probe-post"],
        "final_across_seeds": aggregate_final, "runs": runs,
        "no_noise": estimate_observed_gains([(skill, skill)] * steps, alpha),
        "sign_flip_pair": {
            "positive": estimate_observed_gains([(skill - amplitude, skill + amplitude)] * steps, alpha),
            "negative": estimate_observed_gains([(skill + amplitude, skill - amplitude)] * steps, alpha),
        },
    }


def rotated_maximum(candidates, index):
    top = max(candidate.score for candidate in candidates)
    tied = sorted((candidate for candidate in candidates if candidate.score == top),
                  key=lambda item: (item.action, item.target or "", item.method or ""))
    return tied[index % len(tied)]


def tie_probe():
    policy = BASE.policy
    mind = PlayerMind(0.4, (0.05, 0.05), (0.0, 0.0), 0.45, 0.2, (1, 1), 1, 1)
    observation = PlayerObservation(SelfObservation(2, 0.4, 0.0), None)
    player = decide_player(observation, mind, policy)

    roster = ("p1", "p2")
    coach = []
    for order in (roster, roster[::-1]):
        assessments = tuple(Assessment(player_id, 0.4, 0, 1, 1) for player_id in order)
        context = CoachObservation(3, 6, coach_candidates(order, (), ()))
        coach.append(decide_coach(context, CoachMind(assessments, (0.10, 0.10), 1), policy))
    rotations = [[rotated_maximum(decision.candidates, turn).target for turn in range(12)]
                 for decision in coach]
    return {
        "noise": 0,
        "player": {"scores": {candidate.action: candidate.score for candidate in player.candidates},
                   "actual": player.chosen.action,
                   "reversed_selector": max(reversed(player.candidates),
                                            key=lambda candidate: candidate.score).action},
        "coach": {"rosters": [roster, roster[::-1]],
                  "actual": [decision.chosen.payload() for decision in coach],
                  "scores": [[candidate.payload() for candidate in decision.candidates]
                             for decision in coach],
                  "canonical_cycle_targets": rotations,
                  "canonical_cycle_counts": [dict(Counter(items)) for items in rotations]},
    }


def stress_measurement(config=BASE):
    import resource

    start = perf_counter()
    world = advance(new_world(17, config), 1000)
    elapsed = perf_counter() - start
    log_bytes = sum(len(line) for line in event_lines(world))
    with TemporaryDirectory() as folder:
        path = Path(folder) / "snapshot.json"
        start = perf_counter()
        write_snapshot(path, world)
        write_seconds = perf_counter() - start
        start = perf_counter()
        restored = load_snapshot(path)
        load_seconds = perf_counter() - start
        snapshot_bytes = path.stat().st_size
        start = perf_counter()
        advance(restored, 14)
        resume_seconds = perf_counter() - start
    high_water = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak_bytes = high_water if runtime()["system"] == "Darwin" else high_water * 1024
    return {
        "seed": world.seed, "days": world.day, "event_count": len(world.events),
        "log_bytes": log_bytes, "snapshot_bytes": snapshot_bytes,
        "simulation_seconds": elapsed, "snapshot_write_seconds": write_seconds,
        "snapshot_load_seconds": load_seconds, "resume_14_days_seconds": resume_seconds,
        "process_peak_rss_bytes": peak_bytes,
        "peak_scope": "whole experiment process, including simultaneous original and restored worlds",
        "simulation_sha256": digest(snapshot(world)),
    }


def run_t002(out):
    out = Path(out)
    sets = {name: compare_seed_set(seeds) for name, seeds in SEED_SETS.items()}
    report = {
        "suite": "t002", "versions": identity(), "runtime": runtime(), "days_per_run": DAYS,
        "daily_slots": DAILY_SLOTS,
        "run_count": sum(len(CONDITIONS) * len(seeds) for seeds in SEED_SETS.values()),
        "bootstrap": {"seed": BOOTSTRAP_SEED, "rng": "python-random-mt19937",
                      "replicates": BOOTSTRAP_REPLICATES,
                      "unit": "paired seed", "interval": "95% inclusive percentile",
                      "estimand": "mean paired difference of run-level outcomes",
                      "missing_acceptance": "omit pairs with zero proposals",
                      "interaction": "both_frozen - gain_frozen - coach_frozen + baseline"},
        "sets": sets, "probes": {"clipping": clipping_probe(), "tie_order": tie_probe()},
        "performance": stress_measurement(),
    }
    write_json(out / "comparison.json", report)
    write_json(out / "summary.json", {
        "run_count": report["run_count"], "performance": report["performance"],
        "sets": {label: {name: result["aggregate"] for name, result in seed_set["conditions"].items()}
                 for label, seed_set in sets.items()},
    })
    return report
