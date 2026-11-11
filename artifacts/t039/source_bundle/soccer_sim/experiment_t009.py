"""Pre-registered T009 selection-loop comparisons, extra controls, and long-horizon probes.

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
from .selection_loop import (
    COACH_ID,
    ROSTER_IDS,
    SELECTION_AUTHOR,
    SELECTION_WORLD_KIND,
    T009_ALL_CONDITIONS,
    T009_EXTRA_CONDITIONS,
    T009_MAIN_CONDITIONS,
    TRAINING_METHODS,
    SelectionConfig,
    SelectionWorldState,
    config_for_condition,
    create_selection_world,
    load_snapshot,
    round6,
    run_selection_world,
    selection_identity,
    snapshot_payload,
    write_run,
)
from .storage import atomic_write, canonical, runtime, write_json

DAYS = 84
SEED_SETS: dict[str, tuple[int, ...]] = {
    "exploration": tuple(range(1, 31)),
    "confirmation": tuple(range(100, 130)),
}
BOOTSTRAP_SEED = 9009

# CLI condition map (prefixed with 'select_' so legacy T001-T008 conditions remain untouched)
CONDITIONS: dict[str, SelectionConfig] = {
    f"select_{name}": config_for_condition(name) for name in T009_ALL_CONDITIONS
}

MAIN_CONFIGS: dict[str, SelectionConfig] = {
    name: config_for_condition(name) for name in T009_MAIN_CONDITIONS
}

SIMPLE_PAIRS: dict[str, tuple[str, str]] = {
    "coupled_minus_decoupled_prior": ("coupled_prior", "decoupled_prior"),
    "coupled_minus_decoupled_feedback": ("coupled_feedback", "decoupled_feedback"),
    "feedback_minus_prior_decoupled": ("decoupled_feedback", "decoupled_prior"),
    "feedback_minus_prior_coupled": ("coupled_feedback", "coupled_prior"),
}

CONTRASTS: dict[str, tuple[tuple[str, int], ...]] = {
    **{label: ((left, 1), (right, -1)) for label, (left, right) in SIMPLE_PAIRS.items()},
    "interaction_coupling_by_feedback": (
        ("coupled_feedback", 1),
        ("decoupled_feedback", -1),
        ("coupled_prior", -1),
        ("decoupled_prior", 1),
    ),
}


def trajectories(world: SelectionWorldState) -> dict[str, list[str]]:
    seqs: dict[str, list[str]] = {
        "lineup": [],
        "coach_training": [],
        "p1": [],
        "p2": [],
        "p3": [],
    }
    for ev in world.events:
        kind = ev["kind"]
        if kind == "lineup_decision":
            starters = sorted(ev["data"]["starters"])
            seqs["lineup"].append(f"{starters[0]}+{starters[1]}")
        elif kind == "coach_training_decision":
            ch = ev["data"]["chosen"]
            seqs["coach_training"].append(f"{ch['kind']}:{ch['target']}:{ch['method']}")
        elif kind == "player_training_decision":
            pid = str(ev["actor"])
            seqs[pid].append(str(ev["data"]["chosen"]["action"]))
    return seqs


def sequence_disagreement(
    left: dict[str, list[str]],
    right: dict[str, list[str]],
) -> dict[str, float]:
    out = {
        key: round6(mean(a != b for a, b in zip(left[key], right[key], strict=True)))
        for key in ("lineup", "coach_training", "p1", "p2", "p3")
    }
    out["players_mean"] = round6(mean(out[pid] for pid in ROSTER_IDS))
    return out


def measure_selection_run(world: SelectionWorldState, elapsed: float = 0.0) -> dict[str, Any]:
    assert world.coach is not None
    matches = world.match_summaries
    second_half = matches[len(matches) // 2 :] if matches else []

    home_goals = sum(int(m["home_goals"]) for m in matches)
    away_goals = sum(int(m["away_goals"]) for m in matches)
    goal_diff = home_goals - away_goals
    second_half_gd = sum(int(m["goal_diff"]) for m in second_half)
    home_poss = sum(int(m["home_possession_ticks"]) for m in matches)
    home_passes = sum(int(m["home_pass_attempts"]) for m in matches)
    home_pass_comp = sum(int(m["home_pass_completions"]) for m in matches)
    home_shots = sum(int(m["home_shots"]) for m in matches)
    home_tackles = sum(int(m["home_tackles_won"]) for m in matches)

    lineup_switches = sum(
        1
        for idx in range(1, len(matches))
        if set(matches[idx]["starters"]) != set(matches[idx - 1]["starters"])
    )

    coach_obs_counts = Counter(
        str(e["target"]) for e in world.events if e["kind"] == "coach_observation"
    )
    coach_prop_counts = Counter(
        str(e["target"]) for e in world.events if e["kind"] == "training_proposal"
    )

    players_summary: dict[str, Any] = {}
    gains: list[float] = []
    apps: list[int] = []

    for pid in ROSTER_IDS:
        p = world.players[pid]
        init_mean = round6(
            (p.initial_traits.pace + p.initial_traits.passing + p.initial_traits.shooting) / 3.0
        )
        gain = round6(p.mean_skill - init_mean)
        gains.append(gain)
        apps.append(p.mind.appearances)

        p_decs = [
            e for e in world.events if e["kind"] == "player_training_decision" and e["actor"] == pid
        ]
        act_counts = Counter(str(e["data"]["chosen"]["action"]) for e in p_decs)
        accepted = sum(
            1
            for e in p_decs
            for r in e["data"]["proposal_responses"]
            if r["accepted"]
        )
        career_evs = [
            e for e in world.events if e["kind"] == "player_career_belief" and e["actor"] == pid
        ]
        max_bench_streak = max((int(e["data"]["bench_streak"]) for e in career_evs), default=0)

        players_summary[pid] = {
            "initial_mean_skill": init_mean,
            "final_mean_skill": p.mean_skill,
            "skill_gain": gain,
            "final_traits": p.traits.to_dict(),
            "pace_gain": round6(p.traits.pace - p.initial_traits.pace),
            "passing_gain": round6(p.traits.passing - p.initial_traits.passing),
            "shooting_gain": round6(p.traits.shooting - p.initial_traits.shooting),
            "final_fatigue": round6(p.fatigue),
            "appearances": int(p.mind.appearances),
            "final_bench_streak": int(p.mind.bench_streak),
            "max_bench_streak": int(max_bench_streak),
            "playing_time_satisfaction": round6(p.mind.playing_time_satisfaction),
            "self_match_rating": round6(p.mind.self_match_rating),
            "final_trust": round6(p.mind.trust),
            "training_actions": {
                "A": act_counts["A"],
                "B": act_counts["B"],
                "rest": act_counts["rest"],
            },
            "proposals_received": coach_prop_counts[pid],
            "proposals_accepted": accepted,
            "coach_observations": coach_obs_counts[pid],
            "coach_final_skill_estimate": round6(world.coach.assessments[pid].skill_estimate),
            "coach_final_match_rating": round6(world.coach.assessments[pid].match_rating_estimate),
        }

    metrics: dict[str, float | int] = {
        "home_goals": home_goals,
        "away_goals": away_goals,
        "goal_diff": goal_diff,
        "second_half_goal_diff": second_half_gd,
        "home_possession_ticks": home_poss,
        "home_pass_attempts": home_passes,
        "home_pass_completions": home_pass_comp,
        "home_shots": home_shots,
        "home_tackles_won": home_tackles,
        "lineup_switches": lineup_switches,
        "min_player_appearances": min(apps),
        "max_player_appearances": max(apps),
        "mean_skill_gain": round6(mean(gains)),
        "p1_appearances": players_summary["p1"]["appearances"],
        "p2_appearances": players_summary["p2"]["appearances"],
        "p3_appearances": players_summary["p3"]["appearances"],
        "p1_skill_gain": players_summary["p1"]["skill_gain"],
        "p2_skill_gain": players_summary["p2"]["skill_gain"],
        "p3_skill_gain": players_summary["p3"]["skill_gain"],
        "p3_max_bench_streak": players_summary["p3"]["max_bench_streak"],
        "p3_playing_time_satisfaction": players_summary["p3"]["playing_time_satisfaction"],
        "p3_proposals_received": players_summary["p3"]["proposals_received"],
        "p3_coach_observations": players_summary["p3"]["coach_observations"],
        "p3_coach_final_match_rating": players_summary["p3"]["coach_final_match_rating"],
        "p1_rest_days": players_summary["p1"]["training_actions"]["rest"],
        "p2_rest_days": players_summary["p2"]["training_actions"]["rest"],
        "p3_rest_days": players_summary["p3"]["training_actions"]["rest"],
    }

    return {
        "world_kind": SELECTION_WORLD_KIND,
        "author": SELECTION_AUTHOR,
        "condition": world.config.condition,
        "seed": world.seed,
        "days": world.day,
        "matches_played": len(matches),
        "elapsed_seconds": round6(elapsed),
        "event_count": len(world.events),
        "players": players_summary,
        "metrics": metrics,
        "trajectories": trajectories(world),
    }


def aggregate_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    metric_keys = list(runs[0]["metrics"].keys())
    return {
        "run_count": len(runs),
        "metrics_mean": {
            k: round6(mean(float(r["metrics"][k]) for r in runs)) for k in metric_keys
        },
        "players_mean": {
            pid: {
                "appearances": round6(mean(r["players"][pid]["appearances"] for r in runs)),
                "skill_gain": round6(mean(r["players"][pid]["skill_gain"] for r in runs)),
                "final_mean_skill": round6(mean(r["players"][pid]["final_mean_skill"] for r in runs)),
                "pace_gain": round6(mean(r["players"][pid]["pace_gain"] for r in runs)),
                "passing_gain": round6(mean(r["players"][pid]["passing_gain"] for r in runs)),
                "shooting_gain": round6(mean(r["players"][pid]["shooting_gain"] for r in runs)),
                "playing_time_satisfaction": round6(
                    mean(r["players"][pid]["playing_time_satisfaction"] for r in runs)
                ),
                "proposals_received": round6(
                    mean(r["players"][pid]["proposals_received"] for r in runs)
                ),
                "coach_observations": round6(
                    mean(r["players"][pid]["coach_observations"] for r in runs)
                ),
                "coach_final_match_rating": round6(
                    mean(r["players"][pid]["coach_final_match_rating"] for r in runs)
                ),
                "training_actions": {
                    act: round6(mean(r["players"][pid]["training_actions"][act] for r in runs))
                    for act in ("A", "B", "rest")
                },
            }
            for pid in ROSTER_IDS
        },
    }


def _make_resamples(seeds: tuple[int, ...], seed_offset: int = 0) -> list[tuple[int, ...]]:
    rng = Random(BOOTSTRAP_SEED + len(seeds) + seed_offset)
    return [tuple(rng.randrange(len(seeds)) for _ in seeds) for _ in range(2000)]


def summarize_disagreements(
    by_condition: dict[str, dict[int, dict[str, Any]]],
    seeds: tuple[int, ...],
    pairs: dict[str, tuple[str, str]],
) -> dict[str, Any]:
    resamples = _make_resamples(seeds, seed_offset=11)
    out: dict[str, Any] = {}
    for label, (left_name, right_name) in pairs.items():
        per_seed = {
            seed: sequence_disagreement(
                by_condition[left_name][seed]["trajectories"],
                by_condition[right_name][seed]["trajectories"],
            )
            for seed in seeds
        }
        out[label] = {
            actor: summarize_differences({s: per_seed[s][actor] for s in seeds}, resamples)
            for actor in ("lineup", "coach_training", "p1", "p2", "p3", "players_mean")
        }
    return out


def compare_seed_set(
    seeds: tuple[int, ...] | list[int],
    *,
    days: int = DAYS,
) -> dict[str, Any]:
    seed_tuple = tuple(int(s) for s in seeds)
    by_condition: dict[str, dict[int, dict[str, Any]]] = {
        name: {} for name in T009_MAIN_CONDITIONS
    }
    condition_summaries: dict[str, Any] = {}

    for name in T009_MAIN_CONDITIONS:
        runs: list[dict[str, Any]] = []
        for seed in seed_tuple:
            t0 = perf_counter()
            world = run_selection_world(days, seed=seed, condition=name)
            measured = measure_selection_run(world, perf_counter() - t0)
            by_condition[name][seed] = measured
            runs.append(measured)
        condition_summaries[name] = {
            "config": MAIN_CONFIGS[name].to_dict(),
            "aggregate": aggregate_runs(runs),
            "runs": runs,
        }

    metric_keys = list(next(iter(by_condition["coupled_feedback"].values()))["metrics"].keys())
    resamples = _make_resamples(seed_tuple, seed_offset=0)
    paired_contrasts: dict[str, Any] = {}
    for label, terms in CONTRASTS.items():
        paired_contrasts[label] = {}
        for m_key in metric_keys:
            diffs = {
                seed: round6(
                    sum(
                        sign * float(by_condition[cond][seed]["metrics"][m_key])
                        for cond, sign in terms
                    )
                )
                for seed in seed_tuple
            }
            paired_contrasts[label][m_key] = summarize_differences(diffs, resamples)

    return {
        "seeds": list(seed_tuple),
        "days": days,
        "conditions": condition_summaries,
        "paired_contrasts": paired_contrasts,
        "sequence_disagreement": summarize_disagreements(by_condition, seed_tuple, SIMPLE_PAIRS),
    }


def run_extra_controls(
    seeds: tuple[int, ...] = SEED_SETS["confirmation"],
    *,
    days: int = DAYS,
    baseline_runs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    by_cond: dict[str, dict[int, dict[str, Any]]] = {
        name: {} for name in ("coupled_feedback", *T009_EXTRA_CONDITIONS)
    }
    summaries: dict[str, Any] = {}
    if baseline_runs is not None:
        by_cond["coupled_feedback"] = {int(r["seed"]): r for r in baseline_runs}
        summaries["coupled_feedback"] = {
            "config": config_for_condition("coupled_feedback").to_dict(),
            "aggregate": aggregate_runs(baseline_runs),
        }
        conds_to_run = T009_EXTRA_CONDITIONS
    else:
        conds_to_run = ("coupled_feedback", *T009_EXTRA_CONDITIONS)

    for name in conds_to_run:
        runs: list[dict[str, Any]] = []
        for seed in seeds:
            t0 = perf_counter()
            world = run_selection_world(days, seed=seed, condition=name)
            measured = measure_selection_run(world, perf_counter() - t0)
            by_cond[name][seed] = measured
            runs.append(measured)
        summaries[name] = {
            "config": config_for_condition(name).to_dict(),
            "aggregate": aggregate_runs(runs),
        }

    pairs = {
        "rotation_minus_coupled_feedback": ("rotation_feedback", "coupled_feedback"),
        "fair_prior_p3_minus_coupled_feedback": ("fair_prior_p3", "coupled_feedback"),
        "p1_pass_style_minus_coupled_feedback": ("p1_pass_style_plus", "coupled_feedback"),
    }
    metric_keys = list(next(iter(by_cond["coupled_feedback"].values()))["metrics"].keys())
    resamples = _make_resamples(seeds, seed_offset=77)
    paired_effects: dict[str, Any] = {}
    for label, (left, right) in pairs.items():
        paired_effects[label] = {}
        for m_key in metric_keys:
            diffs = {
                s: round6(
                    float(by_cond[left][s]["metrics"][m_key])
                    - float(by_cond[right][s]["metrics"][m_key])
                )
                for s in seeds
            }
            paired_effects[label][m_key] = summarize_differences(diffs, resamples)

    return {
        "seeds": list(seeds),
        "days": days,
        "conditions": summaries,
        "paired_effects": paired_effects,
        "sequence_disagreement": summarize_disagreements(by_cond, seeds, pairs),
    }


def run_long_horizon_stress(out_dir: Path | str, seed: int = 17) -> dict[str, Any]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    t_sim = perf_counter()
    world_1000 = run_selection_world(1000, seed=seed, condition="coupled_feedback")
    sim_seconds = perf_counter() - t_sim

    snap_path = out / "snapshot_1000.json"
    ev_path = out / "events_1000.jsonl"
    t_write = perf_counter()
    payload = snapshot_payload(world_1000)
    atomic_write(snap_path, [canonical(payload), b"\n"])
    lines = [canonical(ev) + b"\n" for ev in world_1000.events]
    atomic_write(ev_path, lines)
    write_seconds = perf_counter() - t_write

    t_load = perf_counter()
    restored = load_snapshot(snap_path)
    load_seconds = perf_counter() - t_load

    run_selection_world(14, world=restored)
    continuous_1014 = run_selection_world(1014, seed=seed, condition="coupled_feedback")
    resume_equal = canonical(snapshot_payload(restored)) == canonical(snapshot_payload(continuous_1014))

    snap_bytes = snap_path.stat().st_size
    ev_bytes = ev_path.stat().st_size
    # Clean up large temporary 1000-day files inside stress check folder after recording metrics
    snap_path.unlink(missing_ok=True)
    ev_path.unlink(missing_ok=True)

    max_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {
        "seed": seed,
        "condition": "coupled_feedback",
        "days": 1000,
        "resumed_extra_days": 14,
        "total_days_verified": 1014,
        "matches_in_1000_days": len(world_1000.match_summaries),
        "events_in_1000_days": len(world_1000.events),
        "simulation_seconds": round6(sim_seconds),
        "write_seconds": round6(write_seconds),
        "load_seconds_1pass": round6(load_seconds),
        "snapshot_bytes": snap_bytes,
        "events_jsonl_bytes": ev_bytes,
        "resume_1000_plus_14_byte_equal": resume_equal,
        "max_rss_raw": max_rss,
    }


def run_t009_suite(out_dir: Path | str) -> dict[str, Any]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    t0 = perf_counter()

    exploration = compare_seed_set(SEED_SETS["exploration"], days=DAYS)
    confirmation = compare_seed_set(SEED_SETS["confirmation"], days=DAYS)
    all_seeds = SEED_SETS["exploration"] + SEED_SETS["confirmation"]

    combined_by_cond: dict[str, dict[int, dict[str, Any]]] = {}
    combined_agg: dict[str, Any] = {}
    for name in T009_MAIN_CONDITIONS:
        merged_runs = (
            exploration["conditions"][name]["runs"]
            + confirmation["conditions"][name]["runs"]
        )
        combined_agg[name] = aggregate_runs(merged_runs)
        combined_by_cond[name] = {int(r["seed"]): r for r in merged_runs}

    metric_keys = list(
        next(iter(combined_by_cond["coupled_feedback"].values()))["metrics"].keys()
    )
    resamples_all = _make_resamples(all_seeds, seed_offset=0)
    combined_contrasts: dict[str, Any] = {}
    for label, terms in CONTRASTS.items():
        combined_contrasts[label] = {}
        for m_key in metric_keys:
            diffs = {
                seed: round6(
                    sum(
                        sign * float(combined_by_cond[cond][seed]["metrics"][m_key])
                        for cond, sign in terms
                    )
                )
                for seed in all_seeds
            }
            combined_contrasts[label][m_key] = summarize_differences(diffs, resamples_all)

    extra_controls = run_extra_controls(
        SEED_SETS["confirmation"],
        days=DAYS,
        baseline_runs=confirmation["conditions"]["coupled_feedback"]["runs"],
    )
    stress = run_long_horizon_stress(out / "stress_tmp", seed=17)
    if (out / "stress_tmp").exists():
        (out / "stress_tmp").rmdir()

    elapsed = round6(perf_counter() - t0)
    comparison = {
        "suite": "t009",
        "world_kind": SELECTION_WORLD_KIND,
        "author": SELECTION_AUTHOR,
        "versions": selection_identity(),
        "runtime": runtime(),
        "days": DAYS,
        "run_count": len(T009_MAIN_CONDITIONS) * len(all_seeds),
        "elapsed_seconds": elapsed,
        "seed_sets": {k: list(v) for k, v in SEED_SETS.items()},
        "exploration": exploration,
        "confirmation": confirmation,
        "all_60_seeds": {
            "conditions": combined_agg,
            "paired_contrasts": combined_contrasts,
            "sequence_disagreement": summarize_disagreements(
                combined_by_cond, all_seeds, SIMPLE_PAIRS
            ),
        },
        "extra_controls": extra_controls,
        "long_horizon_1000_plus_14": stress,
    }

    summary = {
        "suite": "t009",
        "world_kind": SELECTION_WORLD_KIND,
        "author": SELECTION_AUTHOR,
        "versions": selection_identity(),
        "days": DAYS,
        "run_count": comparison["run_count"],
        "sets": {
            "exploration": exploration["conditions"],
            "confirmation": confirmation["conditions"],
        },
        "elapsed_seconds": elapsed,
        "confirmation_means": {
            k: v["aggregate"]["metrics_mean"]
            for k, v in confirmation["conditions"].items()
        },
        "confirmation_players": {
            k: v["aggregate"]["players_mean"]
            for k, v in confirmation["conditions"].items()
        },
        "confirmation_contrasts": confirmation["paired_contrasts"],
        "confirmation_disagreement": confirmation["sequence_disagreement"],
        "extra_controls_means": {
            k: v["aggregate"]["metrics_mean"]
            for k, v in extra_controls["conditions"].items()
        },
        "extra_controls_effects": extra_controls["paired_effects"],
        "extra_controls_disagreement": extra_controls["sequence_disagreement"],
        "long_horizon_1000_plus_14": stress,
    }

    write_json(out / "comparison.json", comparison)
    write_json(out / "summary.json", summary)
    write_json(
        out / "manifest.json",
        {
            "suite": "t009",
            "world_kind": SELECTION_WORLD_KIND,
            "author": SELECTION_AUTHOR,
            "versions": selection_identity(),
            "runtime": runtime(),
            "run_count": comparison["run_count"],
            "days": DAYS,
            "elapsed_seconds": elapsed,
        },
    )
    return summary


run_t009 = run_t009_suite
