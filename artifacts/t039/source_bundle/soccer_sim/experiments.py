"""Paired-seed comparisons and controlled probes for T001."""
from collections import Counter
from dataclasses import asdict, replace
from pathlib import Path
from statistics import mean
from time import perf_counter

from .observation import observed_gain, update_player_gain
from .policy import decide_player
from .simulation import advance, new_world, resolve_training
from .state import BeliefRules, Config, PlayerMind, PlayerObservation, Proposal, SelfObservation
from .storage import DAILY_SLOTS, event_lines, identity, runtime, write_json

CONDITIONS = {
    "baseline": Config(),
    "homogeneous_rates": replace(Config(), homogeneous_rates=True),
    "no_belief_updates": replace(Config(), beliefs=BeliefRules(False, False, False, False)),
    "no_noise": replace(Config(), self_noise=0.0, fatigue_noise=0.0, coach_noise=0.0),
}
SEEDS = range(30)
DAYS = 28


def measure(world, elapsed_seconds, log_bytes):
    actions = Counter()
    coach_actions = Counter()
    responses = Counter()
    player_actions = Counter()
    player_opportunities = Counter()
    last_observed = {}
    for event in world.events:
        if event.kind == "player_action":
            actions[event.data["action"]] += 1
            player_actions[event.actor, event.data["action"]] += 1
        if event.kind == "coach_action":
            coach_actions[event.data["action"]] += 1
            player_opportunities[event.target, event.data["action"]] += 1
        if event.kind == "coach_observation":
            last_observed[event.target] = event.day
        if event.kind == "decision" and event.phase == "choice":
            response = event.data["proposal_response"]
            if response is not None:
                responses[response] += 1
    player_count = sum(actions.values())
    proposal_count = coach_actions["propose"]
    if sum(responses.values()) != proposal_count:
        raise ValueError("each proposal must receive one response")
    assessments = {item.player_id: item for item in world.coach.assessments}
    players = {}
    for player_id, player in world.players.items():
        initial = next(event.data["state"]["skill"] for event in world.events
                       if event.kind == "initial_player" and event.target == player_id)
        players[player_id] = {
            "final_skill": player.skill, "skill_gain": player.skill - initial,
            "self_absolute_error": abs(player.mind.skill_estimate - player.skill),
            "coach_absolute_error": abs(assessments[player_id].skill_estimate - player.skill),
            "expected_gains": dict(zip(("A", "B"), player.mind.expected_gains)),
            "action_counts": {action: player_actions[player_id, action]
                              for action in ("A", "B", "rest")},
            "proposals": player_opportunities[player_id, "propose"],
            "observations": player_opportunities[player_id, "observe"],
            "last_observed_day": last_observed.get(player_id, 0),
            "remembered_last_observed_day": assessments[player_id].last_seen,
        }
    return {
        "seed": world.seed,
        "days": world.day,
        "seconds": elapsed_seconds,
        "log_bytes": log_bytes,
        "event_count": len(world.events),
        "player_action_counts": {key: actions[key] for key in ("A", "B", "rest")},
        "player_action_share": {key: actions[key] / player_count if player_count else 0.0
                                for key in ("A", "B", "rest")},
        "coach_action_counts": {key: coach_actions[key] for key in ("observe", "propose", "rest")},
        "proposal_count": proposal_count,
        "accepted_count": responses["accepted"],
        "rejected_count": responses["rejected"],
        "acceptance_rate": responses["accepted"] / proposal_count if proposal_count else None,
        "players": players,
        "final_skill_gap": abs(players["p1"]["final_skill"] - players["p2"]["final_skill"]),
    }


def aggregate(runs):
    proposals = sum(run["proposal_count"] for run in runs)
    accepted = sum(run["accepted_count"] for run in runs)
    players = [metrics for run in runs for metrics in run["players"].values()]
    return {
        "runs": len(runs),
        "days": sum(run["days"] for run in runs),
        "seconds": sum(run["seconds"] for run in runs),
        "log_bytes": sum(run["log_bytes"] for run in runs),
        "event_count": sum(run["event_count"] for run in runs),
        "mean_skill_gain": mean(item["skill_gain"] for item in players),
        "mean_self_absolute_error": mean(item["self_absolute_error"] for item in players),
        "mean_coach_absolute_error": mean(item["coach_absolute_error"] for item in players),
        "mean_final_skill_gap": mean(run["final_skill_gap"] for run in runs),
        "player_action_share": {key: mean(run["player_action_share"][key] for run in runs)
                                for key in ("A", "B", "rest")},
        "proposal_count": proposals,
        "accepted_count": accepted,
        "rejected_count": proposals - accepted,
        "acceptance_rate": accepted / proposals if proposals else None,
    }


def growth_probe():
    config = CONDITIONS["no_noise"]
    skill, cap, fatigue = 0.38, 0.9, 0.08
    groups = {"different": (0.145, 0.045), "equal": (0.095, 0.095)}
    return {key: [resolve_training(skill, cap, (rate, 0.08), fatigue, "A", config)[0] - skill
                  for rate in rates]
            for key, rates in groups.items()}


def trust_probe():
    mind = PlayerMind(0.4, (0.05, 0.05), (0.005, 0.0), 0.45, 0.2, (1, 1), 1, 1)
    observation = PlayerObservation(SelfObservation(2, 0.4, 0.05), Proposal(3, "B", 0.06))
    return {key: decide_player(observation, replace(mind, trust=trust), Config().policy).payload()
            for key, trust in (("low", 0.2), ("high", 0.8))}


def experience_probe():
    mind = PlayerMind(0.4, (0.05, 0.05), (0.0, 0.0), 0.45, 0.2, (1, 1), 1, 1)
    next_observation = PlayerObservation(SelfObservation(4, 0.4, 0.05), None)
    histories = {"A_higher": (("A", 0.49), ("B", 0.41)),
                 "B_higher": (("A", 0.41), ("B", 0.49))}
    results = {}
    for label, config in (("updates", Config()), ("frozen", CONDITIONS["no_belief_updates"])):
        results[label] = {}
        for name, history in histories.items():
            belief = mind
            if config.beliefs.player_gain:
                for memory_id, (method, after) in enumerate(history, start=2):
                    belief = update_player_gain(belief, method, observed_gain(0.4, after),
                                                config.gain_alpha, memory_id)
            decision = decide_player(next_observation, belief, config.policy)
            results[label][name] = {"expected_gains": belief.expected_gains,
                                    "decision": decision.payload()}
    return results


def run_experiment(out):
    out = Path(out)
    conditions = {}
    for name, config in CONDITIONS.items():
        runs = []
        for seed in SEEDS:
            start = perf_counter()
            world = advance(new_world(seed, config), DAYS)
            elapsed = perf_counter() - start
            runs.append(measure(world, elapsed, sum(len(line) for line in event_lines(world))))
        conditions[name] = {"config": asdict(config), "runs": runs, "aggregate": aggregate(runs)}

    report = {"versions": identity(), "runtime": runtime(), "seeds": list(SEEDS),
              "days_per_run": DAYS, "daily_slots": DAILY_SLOTS, "conditions": conditions,
              "probes": {"growth": growth_probe(), "trust": trust_probe(),
                         "experience": experience_probe()}}
    write_json(out / "comparison.json", report)
    write_json(out / "summary.json", {
        name: result["aggregate"] for name, result in conditions.items()
    })
    return report
