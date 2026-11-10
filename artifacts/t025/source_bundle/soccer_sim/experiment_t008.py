"""Pre-registered T008 2D match core comparisons and mechanism probes.

Author: Antigravity (Gemini 4 Argon) — distinct from the creator of T001-T007 (Sol 6.1 High).
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from random import Random
import resource
from statistics import mean
from time import perf_counter
from typing import Any

from .experiment_t002 import bootstrap_effects, summarize_differences
from .match_2d import (
    MATCH_ACTIONS,
    MATCH_AUTHOR,
    MATCH_WORLD_KIND,
    T008_ALL_CONDITIONS,
    T008_EXTRA_CONDITIONS,
    T008_MAIN_CONDITIONS,
    MatchConfig,
    MatchWorldState,
    create_match_world,
    load_snapshot,
    match_config_for_condition,
    match_identity,
    round6,
    run_match,
    snapshot_payload,
    step_match,
    write_run,
)
from .storage import canonical, runtime, write_json

TICKS = 40
SEED_SETS: dict[str, tuple[int, ...]] = {
    "exploration": tuple(range(30)),
    "confirmation": tuple(range(100, 130)),
}
BOOTSTRAP_SEED = 8008

# CLI condition map (prefixed with 'match_' so legacy T001-T006 'baseline' is untouched)
CONDITIONS: dict[str, MatchConfig] = {
    f"match_{name}": match_config_for_condition(name) for name in T008_ALL_CONDITIONS
}

MAIN_CONFIGS: dict[str, MatchConfig] = {
    name: match_config_for_condition(name) for name in T008_MAIN_CONDITIONS
}

SIMPLE_PAIRS: dict[str, tuple[str, str]] = {
    "skill_plus_minus_baseline": ("home_skill_plus", "baseline"),
    "position_plus_minus_baseline": ("home_position_plus", "baseline"),
    "pass_style_minus_baseline": ("home_pass_style", "baseline"),
}

CONTRASTS: dict[str, tuple[tuple[str, int], ...]] = {
    label: ((left, 1), (right, -1)) for label, (left, right) in SIMPLE_PAIRS.items()
}


def optional_mean(values: list[float | int]) -> float | None:
    return round6(mean(values)) if values else None


def trajectories(world: MatchWorldState) -> dict[str, list[str]]:
    actions: dict[str, list[str]] = {pid: [] for pid in ("h1", "h2", "a1", "a2")}
    for event in world.events:
        if event["kind"] == "player_decision":
            actions[str(event["player_id"])].append(str(event["effective_action"]))
    return actions


def action_disagreement(
    left: dict[str, list[str]],
    right: dict[str, list[str]],
) -> dict[str, float]:
    result = {
        pid: round6(mean(a != b for a, b in zip(left[pid], right[pid], strict=True)))
        for pid in ("h1", "h2", "a1", "a2")
    }
    result["home"] = round6(mean(result[pid] for pid in ("h1", "h2")))
    result["away"] = round6(mean(result[pid] for pid in ("a1", "a2")))
    result["all"] = round6(mean(result[pid] for pid in ("h1", "h2", "a1", "a2")))
    return result


def measure_match_run(world: MatchWorldState, elapsed: float = 0.0) -> dict[str, Any]:
    decisions = [e for e in world.events if e["kind"] == "player_decision"]
    kicks = [e for e in world.events if e["kind"] == "ball_kick"]
    controls = [e for e in world.events if e["kind"] == "ball_control"]
    tackles = [e for e in world.events if e["kind"] == "tackle_resolution"]
    boundaries = [e for e in world.events if e["kind"] == "ball_boundary"]
    summaries = [e for e in world.events if e["kind"] == "tick_summary"]

    players_summary: dict[str, Any] = {}
    for pid in ("h1", "h2", "a1", "a2"):
        p_decs = [e for e in decisions if e["player_id"] == pid]
        act_counts = Counter(str(e["effective_action"]) for e in p_decs)
        status_counts = Counter(str(e["intent_status"]) for e in p_decs)
        unseen_tm = sum(
            1
            for e in p_decs
            for tm in e["observation"]["teammates"]
            if not tm["visible"]
        )
        unseen_opp = sum(
            1
            for e in p_decs
            for opp in e["observation"]["opponents"]
            if not opp["visible"]
        )
        players_summary[pid] = {
            "team": world.players[pid].team,
            "actions": {act: act_counts[act] for act in MATCH_ACTIONS},
            "illegal_without_ball": status_counts["illegal_without_ball"],
            "unseen_teammate_ticks": unseen_tm,
            "unseen_opponent_ticks": unseen_opp,
        }

    home_poss = sum(
        1 for s in summaries if s["ball"]["owner_id"] in ("h1", "h2")
    )
    away_poss = sum(
        1 for s in summaries if s["ball"]["owner_id"] in ("a1", "a2")
    )
    loose_ticks = len(summaries) - home_poss - away_poss
    attacking_half_ticks = sum(1 for s in summaries if float(s["ball"]["x"]) > 0.0)
    mean_ball_x = optional_mean([float(s["ball"]["x"]) for s in summaries]) or 0.0

    home_pass_attempts = sum(
        1 for k in kicks if k["kicker_team"] == "home" and k["kick_kind"] == "pass"
    )
    away_pass_attempts = sum(
        1 for k in kicks if k["kicker_team"] == "away" and k["kick_kind"] == "pass"
    )
    home_pass_completions = sum(
        1
        for c in controls
        if c["outcome"] == "pass_completed" and c.get("winner_team") == "home"
    )
    away_pass_completions = sum(
        1
        for c in controls
        if c["outcome"] == "pass_completed" and c.get("winner_team") == "away"
    )

    home_shots = sum(
        1 for k in kicks if k["kicker_team"] == "home" and k["kick_kind"] == "shoot"
    )
    home_shots_on_target = sum(
        1
        for k in kicks
        if k["kicker_team"] == "home"
        and k["kick_kind"] == "shoot"
        and bool(k.get("on_target"))
    )
    away_shots = sum(
        1 for k in kicks if k["kicker_team"] == "away" and k["kick_kind"] == "shoot"
    )
    away_shots_on_target = sum(
        1
        for k in kicks
        if k["kicker_team"] == "away"
        and k["kick_kind"] == "shoot"
        and bool(k.get("on_target"))
    )

    home_tackle_attempts = sum(1 for t in tackles if t["challenger_team"] == "home")
    home_tackles_won = sum(
        1
        for t in tackles
        if t["challenger_team"] == "home" and t["outcome"] == "tackle_won"
    )
    away_tackle_attempts = sum(1 for t in tackles if t["challenger_team"] == "away")
    away_tackles_won = sum(
        1
        for t in tackles
        if t["challenger_team"] == "away" and t["outcome"] == "tackle_won"
    )

    home_interceptions = sum(
        1
        for c in controls
        if c["outcome"] == "interception" and c.get("winner_team") == "home"
    )
    away_interceptions = sum(
        1
        for c in controls
        if c["outcome"] == "interception" and c.get("winner_team") == "away"
    )
    mutual_rebounds = sum(1 for c in controls if c["outcome"] == "mutual_rebound")

    team_actions: dict[str, dict[str, int]] = {}
    for team, pids in (("home", ("h1", "h2")), ("away", ("a1", "a2"))):
        team_actions[team] = {
            act: sum(players_summary[pid]["actions"][act] for pid in pids)
            for act in MATCH_ACTIONS
        }

    return {
        "seed": world.seed,
        "ticks": world.tick,
        "condition": world.config.condition,
        "elapsed_seconds": round6(elapsed),
        "home_goals": world.score["home"],
        "away_goals": world.score["away"],
        "goal_diff": world.score["home"] - world.score["away"],
        "home_possession_ticks": home_poss,
        "away_possession_ticks": away_poss,
        "loose_ball_ticks": loose_ticks,
        "home_attacking_half_ticks": attacking_half_ticks,
        "mean_ball_x": round6(mean_ball_x),
        "home_pass_attempts": home_pass_attempts,
        "home_pass_completions": home_pass_completions,
        "away_pass_attempts": away_pass_attempts,
        "away_pass_completions": away_pass_completions,
        "home_shots": home_shots,
        "home_shots_on_target": home_shots_on_target,
        "away_shots": away_shots,
        "away_shots_on_target": away_shots_on_target,
        "home_tackle_attempts": home_tackle_attempts,
        "home_tackles_won": home_tackles_won,
        "away_tackle_attempts": away_tackle_attempts,
        "away_tackles_won": away_tackles_won,
        "home_interceptions": home_interceptions,
        "away_interceptions": away_interceptions,
        "mutual_rebounds": mutual_rebounds,
        "out_of_bounds": len(boundaries),
        "team_actions": team_actions,
        "players": players_summary,
        "event_count": len(world.events),
        "actions": trajectories(world),
    }


def outcomes(run: dict[str, Any]) -> dict[str, float | int | None]:
    scalar_keys = (
        "home_goals",
        "away_goals",
        "goal_diff",
        "home_possession_ticks",
        "away_possession_ticks",
        "loose_ball_ticks",
        "home_attacking_half_ticks",
        "mean_ball_x",
        "home_pass_attempts",
        "home_pass_completions",
        "away_pass_attempts",
        "away_pass_completions",
        "home_shots",
        "home_shots_on_target",
        "away_shots",
        "away_shots_on_target",
        "home_tackle_attempts",
        "home_tackles_won",
        "away_tackle_attempts",
        "away_tackles_won",
        "home_interceptions",
        "away_interceptions",
        "mutual_rebounds",
        "out_of_bounds",
    )
    result: dict[str, float | int | None] = {k: run[k] for k in scalar_keys}
    for team in ("home", "away"):
        for act in MATCH_ACTIONS:
            result[f"team.{team}.action.{act}"] = run["team_actions"][team][act]
    for pid in ("h1", "h2", "a1", "a2"):
        result[f"player.{pid}.unseen_opponent_ticks"] = run["players"][pid]["unseen_opponent_ticks"]
        result[f"player.{pid}.unseen_teammate_ticks"] = run["players"][pid]["unseen_teammate_ticks"]
    return result


def aggregate(runs: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    flattened = [outcomes(row) for row in runs]
    for key in flattened[0]:
        values = [row[key] for row in flattened if row[key] is not None]
        result[key] = {"mean": optional_mean(values), "valid_runs": len(values)}
    return result


def compare_seed_set(seeds: tuple[int, ...], out: Path) -> dict[str, Any]:
    records: dict[str, list[dict[str, Any]]] = {name: [] for name in MAIN_CONFIGS}
    for seed in seeds:
        for name, cfg in MAIN_CONFIGS.items():
            start = perf_counter()
            world = run_match(ticks=TICKS, seed=seed, condition=name, config=cfg)
            elapsed = perf_counter() - start
            records[name].append(measure_match_run(world, elapsed))

    conditions = {
        name: {
            "config": MAIN_CONFIGS[name].to_dict(),
            "runs": runs,
            "aggregate": aggregate(runs),
        }
        for name, runs in records.items()
    }
    random = Random(BOOTSTRAP_SEED)
    resamples = [
        tuple(random.randrange(len(seeds)) for _ in seeds) for _ in range(2000)
    ]
    action_effects: dict[str, Any] = {}
    for label, (left, right) in SIMPLE_PAIRS.items():
        paired = [
            action_disagreement(a["actions"], b["actions"])
            for a, b in zip(records[left], records[right], strict=True)
        ]
        action_effects[label] = {
            actor: summarize_differences(
                {seed: row[actor] for seed, row in zip(seeds, paired, strict=True)},
                resamples,
            )
            for actor in paired[0]
        }
    return {
        "seeds": list(seeds),
        "conditions": conditions,
        "paired_effects": bootstrap_effects(
            conditions, seeds, outcomes, CONTRASTS, BOOTSTRAP_SEED
        ),
        "action_disagreement": action_effects,
    }


def extra_controls_probe() -> dict[str, Any]:
    seeds = SEED_SETS["confirmation"]
    probe_names = ("baseline", *T008_EXTRA_CONDITIONS)
    records: dict[str, list[dict[str, Any]]] = {name: [] for name in probe_names}
    for seed in seeds:
        for name in probe_names:
            start = perf_counter()
            world = run_match(ticks=TICKS, seed=seed, condition=name)
            records[name].append(measure_match_run(world, perf_counter() - start))

    conditions = {
        name: {
            "config": match_config_for_condition(name).to_dict(),
            "runs": runs,
            "aggregate": aggregate(runs),
        }
        for name, runs in records.items()
    }
    probe_pairs = {
        "directive_press_minus_baseline": ("home_directive_press", "baseline"),
        "oracle_view_minus_baseline": ("home_oracle_view", "baseline"),
        "always_shoot_minus_baseline": ("home_always_shoot", "baseline"),
        "ball_camp_wait_minus_baseline": ("home_ball_camp_wait", "baseline"),
    }
    probe_contrasts = {
        label: ((left, 1), (right, -1)) for label, (left, right) in probe_pairs.items()
    }
    random = Random(BOOTSTRAP_SEED)
    resamples = [
        tuple(random.randrange(len(seeds)) for _ in seeds) for _ in range(2000)
    ]
    disagreements: dict[str, Any] = {}
    for label, (left, right) in probe_pairs.items():
        paired = [
            action_disagreement(a["actions"], b["actions"])
            for a, b in zip(records[left], records[right], strict=True)
        ]
        disagreements[label] = {
            actor: summarize_differences(
                {seed: row[actor] for seed, row in zip(seeds, paired, strict=True)},
                resamples,
            )
            for actor in paired[0]
        }
    return {
        "seeds": list(seeds),
        "conditions": {name: conditions[name]["aggregate"] for name in probe_names},
        "paired_effects": bootstrap_effects(
            conditions, seeds, outcomes, probe_contrasts, BOOTSTRAP_SEED
        ),
        "action_disagreement": disagreements,
    }


def symmetry_and_order_probe() -> dict[str, Any]:
    order_invariant_seeds = 0
    for seed in range(10):
        w_fwd = create_match_world(seed=seed, condition="baseline")
        w_rev = create_match_world(seed=seed, condition="baseline")
        for _ in range(TICKS):
            step_match(w_fwd, player_order=("h1", "h2", "a1", "a2"))
            step_match(w_rev, player_order=("a2", "a1", "h2", "h1"))
        if canonical(snapshot_payload(w_fwd)) != canonical(snapshot_payload(w_rev)):
            raise ValueError(f"player_order invariance failed on seed {seed}")
        order_invariant_seeds += 1

    mirror_seeds = 0
    for seed in range(10):
        cfg = match_config_for_condition("baseline", mirror_rng_by_slot=True)
        w1 = run_match(ticks=TICKS, seed=seed, config=cfg)
        # In mirror_rng_by_slot mode on symmetric baseline, home and away are exact point-reflections
        if w1.score["home"] != w1.score["away"]:
            raise ValueError(f"mirror symmetry score mismatch on seed {seed}")
        for h_id, a_id in (("h1", "a1"), ("h2", "a2")):
            hp = w1.players[h_id]
            ap = w1.players[a_id]
            if round6(hp.x) != round6(-ap.x) or round6(hp.y) != round6(-ap.y):
                raise ValueError(f"mirror position mismatch for {h_id}/{a_id} on seed {seed}")
        mirror_seeds += 1

    # Zero-noise kickoff tie produces mutual_rebound
    w_tie = run_match(
        ticks=6,
        seed=0,
        config=match_config_for_condition("baseline", noise_scale=0.0, mirror_rng_by_slot=True),
    )
    rebounds = [
        e for e in w_tie.events if e["kind"] == "ball_control" and e["outcome"] == "mutual_rebound"
    ]
    if not rebounds:
        raise ValueError("expected mutual_rebound on symmetric zero-noise kickoff")

    return {
        "order_invariant_seeds_verified": order_invariant_seeds,
        "mirror_symmetric_seeds_verified": mirror_seeds,
        "zero_noise_mutual_rebound_events": len(rebounds),
    }


def no_noise_probe(out: Path) -> dict[str, Any]:
    runs: dict[str, Any] = {}
    for name in T008_MAIN_CONDITIONS:
        cfg = match_config_for_condition(name, noise_scale=0.0)
        world = run_match(ticks=TICKS, seed=17, condition=name, config=cfg)
        write_run(out / name, world, 0.0)
        runs[name] = measure_match_run(world, 0.0)
    return {
        "seed": 17,
        "ticks": TICKS,
        "runs": {name: outcomes(runs[name]) for name in T008_MAIN_CONDITIONS},
        "action_disagreement": {
            label: action_disagreement(runs[left]["actions"], runs[right]["actions"])
            for label, (left, right) in SIMPLE_PAIRS.items()
        },
    }


def stress_measurement(out: Path) -> dict[str, Any]:
    cfg = match_config_for_condition("baseline")
    start = perf_counter()
    world = run_match(ticks=1000, seed=17, condition="baseline", config=cfg)
    sim_seconds = perf_counter() - start

    start = perf_counter()
    write_run(out / "first1000", world, sim_seconds)
    write_seconds = perf_counter() - start

    start = perf_counter()
    restored = load_snapshot(out / "first1000" / "snapshot.json")
    load_seconds = perf_counter() - start

    run_match(ticks=14, world=restored)
    run_match(ticks=14, world=world)
    if canonical(snapshot_payload(world)) != canonical(snapshot_payload(restored)):
        raise ValueError("T008 stress resume mismatch")
    write_run(out / "resumed1014", restored, 0.0)

    return {
        "ticks": 1000,
        "resume_ticks": 14,
        "resume_equal": True,
        "simulation_seconds": round6(sim_seconds),
        "write_seconds": round6(write_seconds),
        "load_seconds": round6(load_seconds),
        "snapshot_bytes": (out / "first1000" / "snapshot.json").stat().st_size,
        "events_jsonl_bytes": (out / "first1000" / "events.jsonl").stat().st_size,
        "max_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "note": "macOS ru_maxrss bytes; 1-pass state+history validation on load",
    }


def run_t008(out: Path | str) -> dict[str, Any]:
    out_path = Path(out)
    report: dict[str, Any] = {
        "suite": "t008",
        "author": MATCH_AUTHOR,
        "world_kind": MATCH_WORLD_KIND,
        "versions": match_identity(),
        "runtime": runtime(),
        "ticks_per_run": TICKS,
        "run_count": 240,
        "bootstrap": {
            "seed": BOOTSTRAP_SEED,
            "replicates": 2000,
            "unit": "paired seed",
            "interval": "95% inclusive percentile",
            "missing": "omit incomplete paired measures",
        },
        "sets": {
            name: compare_seed_set(seeds, out_path / name)
            for name, seeds in SEED_SETS.items()
        },
        "probes": {
            "extra_controls": extra_controls_probe(),
            "symmetry_and_order": symmetry_and_order_probe(),
            "no_noise": no_noise_probe(out_path / "no-noise"),
        },
        "performance": stress_measurement(out_path / "stress"),
    }
    write_json(out_path / "comparison.json", report)
    write_json(
        out_path / "summary.json",
        {key: val for key, val in report.items() if key != "sets"}
        | {
            "sets": {
                label: {
                    name: row["aggregate"]
                    for name, row in seed_set["conditions"].items()
                }
                for label, seed_set in report["sets"].items()
            }
        },
    )
    return report
