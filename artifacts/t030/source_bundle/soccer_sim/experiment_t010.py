"""T010 experiment suite: 2-club development, lineup selection, head-to-head 2D match core, and mutual-agreement transfers.

Author: Antigravity (Gemini 4 Argon) — distinct from the creator of T001-T007 (Sol 6.1 High).
"""
from __future__ import annotations

from pathlib import Path
from random import Random
import resource
from statistics import mean
import time
from typing import Any

from .experiment_t002 import summarize_differences
from .storage import atomic_write, canonical, digest, runtime, write_json
from . import transfer_loop as tl

DAYS = 84
SEED_SETS: dict[str, tuple[int, ...]] = {
    "exploration": tuple(range(1, 31)),
    "confirmation": tuple(range(100, 130)),
}
EXPLORATION_SEEDS = SEED_SETS["exploration"]
CONFIRMATION_SEEDS = SEED_SETS["confirmation"]
BOOTSTRAP_SEED = 10010

CONDITIONS: dict[str, tl.TransferConfig] = {
    f"transfer_{name}": tl.config_for_condition(name) for name in tl.T010_ALL_CONDITIONS
}

T010_METRICS = (
    "total_transfers",
    "p3_transferred",
    "p3_transfer_requests",
    "p3_appearances",
    "p3_club_a_appearances",
    "p3_club_b_appearances",
    "p3_skill_gain",
    "p3_playing_time_satisfaction",
    "p3_proposals_from_c1",
    "p3_proposals_from_c2",
    "mean_skill_gain",
    "club_a_goals",
    "club_b_goals",
    "club_b_minus_club_a_gd",
    "second_half_club_a_goals",
    "second_half_club_b_goals",
    "second_half_club_b_minus_club_a_gd",
    "club_a_possession_ticks",
    "club_b_possession_ticks",
    "club_a_shots",
    "club_b_shots",
    "second_half_club_b_shots",
    "club_a_pass_completions",
    "club_b_pass_completions",
)


def _make_resamples(seeds: tuple[int, ...], seed_offset: int = 0) -> list[tuple[int, ...]]:
    rng = Random(BOOTSTRAP_SEED + len(seeds) + seed_offset)
    return [tuple(rng.randrange(len(seeds)) for _ in seeds) for _ in range(2000)]


def _summarize_series(values: list[float]) -> dict[str, float]:
    s = sorted(float(v) for v in values)
    n = len(s)
    return {
        "mean": round(mean(s), 6),
        "min": round(s[0], 6),
        "max": round(s[-1], 6),
        "median": round(s[n // 2], 6),
    }


def _extract_action_sequences(world: tl.TransferWorldState) -> dict[str, list[Any]]:
    coach_seq: dict[str, list[Any]] = {cid: [] for cid in tl.COACH_IDS}
    lineup_seq: dict[str, list[Any]] = {cl: [] for cl in tl.CLUB_IDS}
    player_seq: dict[str, list[Any]] = {pid: [] for pid in tl.PLAYER_IDS}
    transfer_seq: list[Any] = []

    for ev in world.events:
        kind = ev["kind"]
        data = ev["data"]
        if kind == "coach_training_decision":
            c = data["chosen"]
            coach_seq[str(ev["actor"])].append((int(ev["day"]), int(data["slot"]), c["kind"], c["target"], c["method"]))
        elif kind == "lineup_decision":
            lineup_seq[str(data["club_id"])].append((int(ev["day"]), tuple(data["starters"])))
        elif kind == "player_training_decision":
            player_seq[str(ev["actor"])].append((int(ev["day"]), data["chosen"]["action"]))
        elif kind == "transfer_window_decision":
            transfer_seq.append((int(ev["day"]), str(data["player_id"]), bool(data["transfer_approved"])))

    return {
        "coach_c1": coach_seq["c1"],
        "coach_c2": coach_seq["c2"],
        "lineup_club_a": lineup_seq["club_a"],
        "lineup_club_b": lineup_seq["club_b"],
        "transfers": transfer_seq,
        **{f"player_{pid}": player_seq[pid] for pid in tl.PLAYER_IDS},
    }


def _disagreement_rate(seq_a: list[Any], seq_b: list[Any]) -> float:
    if not seq_a and not seq_b:
        return 0.0
    n = max(len(seq_a), len(seq_b))
    diffs = sum(1 for i in range(n) if (seq_a[i] if i < len(seq_a) else None) != (seq_b[i] if i < len(seq_b) else None))
    return round(diffs / n, 6)


def _compute_disagreement_summary(
    seqs_by_cond: dict[str, dict[int, dict[str, list[Any]]]],
    cond_a: str,
    cond_b: str,
    seeds: tuple[int, ...],
    resamples: list[tuple[int, ...]],
) -> dict[str, Any]:
    channels = (
        "transfers",
        "lineup_club_a",
        "lineup_club_b",
        "coach_c1",
        "coach_c2",
    )
    out: dict[str, Any] = {}
    for ch in channels:
        per_seed = {
            s: _disagreement_rate(seqs_by_cond[cond_a][s][ch], seqs_by_cond[cond_b][s][ch])
            for s in seeds
        }
        out[ch] = summarize_differences(per_seed, resamples)

    player_means: dict[int, float] = {}
    for s in seeds:
        p_rates = [
            _disagreement_rate(
                seqs_by_cond[cond_a][s][f"player_{pid}"],
                seqs_by_cond[cond_b][s][f"player_{pid}"],
            )
            for pid in tl.PLAYER_IDS
        ]
        player_means[s] = round(sum(p_rates) / len(p_rates), 6)
    out["players_mean"] = summarize_differences(player_means, resamples)
    return out


def _contrast_metrics(
    runs_by_cond: dict[str, dict[int, dict[str, Any]]],
    cond_a: str,
    cond_b: str,
    seeds: tuple[int, ...],
    resamples: list[tuple[int, ...]],
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for m in T010_METRICS:
        diffs = {
            s: round(float(runs_by_cond[cond_a][s][m]) - float(runs_by_cond[cond_b][s][m]), 6)
            for s in seeds
        }
        out[m] = summarize_differences(diffs, resamples)
    return out


def _interaction_contrast(
    runs_by_cond: dict[str, dict[int, dict[str, Any]]],
    seeds: tuple[int, ...],
    resamples: list[tuple[int, ...]],
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for m in T010_METRICS:
        diffs = {
            s: round(
                (float(runs_by_cond["coupled_transfer"][s][m]) - float(runs_by_cond["coupled_stay"][s][m]))
                - (float(runs_by_cond["decoupled_transfer"][s][m]) - float(runs_by_cond["decoupled_stay"][s][m])),
                6,
            )
            for s in seeds
        }
        out[m] = summarize_differences(diffs, resamples)
    return out


def _condition_means(
    runs_for_cond: dict[int, dict[str, Any]],
    seeds: tuple[int, ...],
) -> dict[str, float]:
    return {
        m: round(sum(float(runs_for_cond[s][m]) for s in seeds) / len(seeds), 6)
        for m in T010_METRICS
    }


def _long_horizon_stress(out_dir: Path) -> dict[str, Any]:
    stress_dir = out_dir / "stress_1000"
    stress_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    w1000 = tl.run_transfer_world(1000, seed=17, condition="coupled_transfer")
    sim_sec = round(time.perf_counter() - t0, 6)

    t_w0 = time.perf_counter()
    tl.save_transfer_run(w1000, stress_dir / "day1000")
    write_sec = round(time.perf_counter() - t_w0, 6)

    snap_path = stress_dir / "day1000" / "snapshot.json"
    jsonl_path = stress_dir / "day1000" / "events.jsonl"

    t_l0 = time.perf_counter()
    loaded = tl.load_transfer_snapshot(snap_path)
    load_sec = round(time.perf_counter() - t_l0, 6)

    for _ in range(14):
        tl.step_transfer_day(loaded)
    w1014 = tl.run_transfer_world(1014, seed=17, condition="coupled_transfer")

    byte_equal = digest(tl.snapshot_payload(loaded)) == digest(tl.snapshot_payload(w1014))
    matches_1000 = sum(1 for e in w1000.events if e["kind"] == "match_completed")
    max_rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return {
        "condition": "coupled_transfer",
        "seed": 17,
        "days": 1000,
        "resumed_extra_days": 14,
        "total_days_verified": 1014,
        "events_in_1000_days": len(w1000.events),
        "matches_in_1000_days": matches_1000,
        "simulation_seconds": sim_sec,
        "write_seconds": write_sec,
        "load_seconds_1pass": load_sec,
        "snapshot_bytes": snap_path.stat().st_size,
        "events_jsonl_bytes": jsonl_path.stat().st_size,
        "max_rss_raw": max_rss,
        "resume_1000_plus_14_byte_equal": byte_equal,
    }


def run_t010_suite(out_dir: str | Path, *, days: int = DAYS) -> dict[str, Any]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    t_start = time.perf_counter()

    sets_payload: dict[str, Any] = {}
    conf_runs: dict[str, dict[int, dict[str, Any]]] = {c: {} for c in tl.T010_MAIN_CONDITIONS}
    conf_seqs: dict[str, dict[int, dict[str, list[Any]]]] = {c: {} for c in tl.T010_MAIN_CONDITIONS}
    all_run_rows: list[dict[str, Any]] = []

    for set_name, seeds in SEED_SETS.items():
        by_cond_series: dict[str, dict[str, Any]] = {}
        by_cond_means: dict[str, dict[str, float]] = {}
        for cond in tl.T010_MAIN_CONDITIONS:
            cond_runs: dict[int, dict[str, Any]] = {}
            for s in seeds:
                w = tl.run_transfer_world(days, seed=s, condition=cond)
                summ = tl.summarize_transfer_world(w)
                cond_runs[s] = summ
                all_run_rows.append(
                    {
                        "set": set_name,
                        "condition": cond,
                        "seed": s,
                        "days": days,
                        "summary": summ,
                    }
                )
                if set_name == "confirmation":
                    conf_runs[cond][s] = summ
                    conf_seqs[cond][s] = _extract_action_sequences(w)

            by_cond_means[cond] = _condition_means(cond_runs, seeds)
            by_cond_series[cond] = {
                m: _summarize_series([float(cond_runs[s][m]) for s in seeds])
                for m in T010_METRICS
            }
        sets_payload[set_name] = {
            "seeds": list(seeds),
            "means": by_cond_means,
            "series": by_cond_series,
        }

    resamples_conf = _make_resamples(CONFIRMATION_SEEDS, seed_offset=0)

    conf_contrasts = {
        "transfer_minus_stay_coupled": _contrast_metrics(
            conf_runs, "coupled_transfer", "coupled_stay", CONFIRMATION_SEEDS, resamples_conf
        ),
        "transfer_minus_stay_decoupled": _contrast_metrics(
            conf_runs, "decoupled_transfer", "decoupled_stay", CONFIRMATION_SEEDS, resamples_conf
        ),
        "coupled_minus_decoupled_transfer": _contrast_metrics(
            conf_runs, "coupled_transfer", "decoupled_transfer", CONFIRMATION_SEEDS, resamples_conf
        ),
        "coupled_minus_decoupled_stay": _contrast_metrics(
            conf_runs, "coupled_stay", "decoupled_stay", CONFIRMATION_SEEDS, resamples_conf
        ),
        "interaction_coupling_by_transfer": _interaction_contrast(
            conf_runs, CONFIRMATION_SEEDS, resamples_conf
        ),
    }

    conf_disagreement = {
        "transfer_minus_stay_coupled": _compute_disagreement_summary(
            conf_seqs, "coupled_transfer", "coupled_stay", CONFIRMATION_SEEDS, resamples_conf
        ),
        "transfer_minus_stay_decoupled": _compute_disagreement_summary(
            conf_seqs, "decoupled_transfer", "decoupled_stay", CONFIRMATION_SEEDS, resamples_conf
        ),
        "coupled_minus_decoupled_transfer": _compute_disagreement_summary(
            conf_seqs, "coupled_transfer", "decoupled_transfer", CONFIRMATION_SEEDS, resamples_conf
        ),
    }

    # Extra single-factor controls on confirmation seeds
    extra_runs: dict[str, dict[int, dict[str, Any]]] = {
        "coupled_transfer": conf_runs["coupled_transfer"]
    }
    extra_seqs: dict[str, dict[int, dict[str, list[Any]]]] = {
        "coupled_transfer": conf_seqs["coupled_transfer"]
    }
    for cond in tl.T010_EXTRA_CONDITIONS:
        extra_runs[cond] = {}
        extra_seqs[cond] = {}
        for s in CONFIRMATION_SEEDS:
            w = tl.run_transfer_world(days, seed=s, condition=cond)
            extra_runs[cond][s] = tl.summarize_transfer_world(w)
            extra_seqs[cond][s] = _extract_action_sequences(w)

    extra_means = {
        cond: _condition_means(extra_runs[cond], CONFIRMATION_SEEDS)
        for cond in ("coupled_transfer", *tl.T010_EXTRA_CONDITIONS)
    }
    extra_effects = {
        f"{cond}_minus_coupled_transfer": _contrast_metrics(
            extra_runs, cond, "coupled_transfer", CONFIRMATION_SEEDS, resamples_conf
        )
        for cond in tl.T010_EXTRA_CONDITIONS
    }
    extra_disagreement = {
        f"{cond}_minus_coupled_transfer": _compute_disagreement_summary(
            extra_seqs, cond, "coupled_transfer", CONFIRMATION_SEEDS, resamples_conf
        )
        for cond in tl.T010_EXTRA_CONDITIONS
    }

    long_horizon = _long_horizon_stress(out)
    elapsed = round(time.perf_counter() - t_start, 6)

    comparison = {
        "suite": "t010",
        "world_kind": tl.TRANSFER_WORLD_KIND,
        "author": tl.TRANSFER_AUTHOR,
        "days": days,
        "run_count": len(all_run_rows),
        "versions": tl.transfer_identity(),
        "runtime": runtime(),
        "runs": all_run_rows,
    }

    summary = {
        "suite": "t010",
        "world_kind": tl.TRANSFER_WORLD_KIND,
        "author": tl.TRANSFER_AUTHOR,
        "days": days,
        "run_count": len(all_run_rows),
        "elapsed_seconds": elapsed,
        "versions": tl.transfer_identity(),
        "sets": sets_payload,
        "confirmation_means": sets_payload["confirmation"]["means"],
        "confirmation_contrasts": conf_contrasts,
        "confirmation_disagreement": conf_disagreement,
        "extra_controls_means": extra_means,
        "extra_controls_effects": extra_effects,
        "extra_controls_disagreement": extra_disagreement,
        "long_horizon_1000_plus_14": long_horizon,
    }

    write_json(out / "comparison.json", comparison)
    write_json(out / "summary.json", summary)
    atomic_write(
        out / "runs.jsonl",
        [canonical(r) + b"\n" for r in all_run_rows],
    )
    return summary


def run_t010(out_dir: str | Path) -> dict[str, Any]:
    return run_t010_suite(out_dir, days=DAYS)
