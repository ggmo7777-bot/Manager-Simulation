"""T011 experiment suite: 4-club league, finite budget ledger, development solidarity dividends, and multi-club transfer market (M5).

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
from . import league_economy as le

DAYS = 84
SEED_SETS: dict[str, tuple[int, ...]] = {
    "exploration": tuple(range(1, 31)),
    "confirmation": tuple(range(100, 130)),
}
EXPLORATION_SEEDS = SEED_SETS["exploration"]
CONFIRMATION_SEEDS = SEED_SETS["confirmation"]
BOOTSTRAP_SEED = 10011

CONDITIONS: dict[str, le.LeagueEconomyConfig] = {
    f"league_{name}": le.config_for_condition(name) for name in le.T011_ALL_CONDITIONS
}

T011_METRICS = (
    "total_transfers",
    "talents_transferred_rate",
    "talents_appearances",
    "talents_satisfaction",
    "talents_skill_gain",
    "academy_retained_skill_gain",
    "mean_skill_gain",
    "solidarity_dividends_paid_units",
    "academy_facility_days",
    "buyer_facility_days",
    "academy_clubs_points",
    "buyer_clubs_points",
    "buyer_minus_academy_points",
    "academy_clubs_gd",
    "buyer_clubs_gd",
    "buyer_minus_academy_gd",
    "academy_clubs_cash_units",
    "buyer_clubs_cash_units",
    "buyer_minus_academy_cash_units",
    "ledger_discrepancy_units",
    "insolvency_events",
    "blocked_circular_transfers",
    "blocked_duplicate_claims",
    "actual_duplicate_claims",
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


def _extract_action_sequences(world: le.LeagueWorldState) -> dict[str, list[Any]]:
    coach_seq: dict[str, list[Any]] = {cid: [] for cid in le.COACH_IDS}
    lineup_seq: dict[str, list[Any]] = {cl: [] for cl in le.CLUB_IDS}
    player_seq: dict[str, list[Any]] = {pid: [] for pid in le.PLAYER_IDS}
    transfer_seq: list[Any] = []

    for ev in world.events:
        kind = ev["kind"]
        data = ev["data"]
        if kind == "coach_training_decision":
            c = data["chosen"]
            coach_seq[str(ev["actor"])].append(
                (int(ev["day"]), int(data["slot"]), c["kind"], c["target"], c["method"])
            )
        elif kind == "lineup_decision":
            lineup_seq[str(data["club_id"])].append((int(ev["day"]), tuple(data["starters"])))
        elif kind == "player_training_decision":
            player_seq[str(ev["actor"])].append((int(ev["day"]), data["chosen"]["action"]))
        elif kind == "transfer_window_decision":
            transfer_seq.append(
                (
                    int(ev["day"]),
                    str(data["player_id"]),
                    bool(data["transfer_approved"]),
                    data["chosen_to_club"],
                )
            )

    return {
        "transfers": transfer_seq,
        **{f"lineup_{cl}": lineup_seq[cl] for cl in le.CLUB_IDS},
        **{f"coach_{cid}": coach_seq[cid] for cid in le.COACH_IDS},
        **{f"player_{pid}": player_seq[pid] for pid in le.PLAYER_IDS},
    }


def _disagreement_rate(seq_a: list[Any], seq_b: list[Any]) -> float:
    if not seq_a and not seq_b:
        return 0.0
    n = max(len(seq_a), len(seq_b))
    diffs = sum(
        1
        for i in range(n)
        if (seq_a[i] if i < len(seq_a) else None) != (seq_b[i] if i < len(seq_b) else None)
    )
    return round(diffs / n, 6)


def _compute_disagreement_summary(
    seqs_by_cond: dict[str, dict[int, dict[str, list[Any]]]],
    cond_a: str,
    cond_b: str,
    seeds: tuple[int, ...],
    resamples: list[tuple[int, ...]],
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    per_seed_tr = {
        s: _disagreement_rate(seqs_by_cond[cond_a][s]["transfers"], seqs_by_cond[cond_b][s]["transfers"])
        for s in seeds
    }
    out["transfers"] = summarize_differences(per_seed_tr, resamples)

    lineup_means: dict[int, float] = {}
    coach_means: dict[int, float] = {}
    player_means: dict[int, float] = {}
    for s in seeds:
        l_rates = [
            _disagreement_rate(
                seqs_by_cond[cond_a][s][f"lineup_{cl}"],
                seqs_by_cond[cond_b][s][f"lineup_{cl}"],
            )
            for cl in le.CLUB_IDS
        ]
        lineup_means[s] = round(sum(l_rates) / len(l_rates), 6)

        c_rates = [
            _disagreement_rate(
                seqs_by_cond[cond_a][s][f"coach_{cid}"],
                seqs_by_cond[cond_b][s][f"coach_{cid}"],
            )
            for cid in le.COACH_IDS
        ]
        coach_means[s] = round(sum(c_rates) / len(c_rates), 6)

        p_rates = [
            _disagreement_rate(
                seqs_by_cond[cond_a][s][f"player_{pid}"],
                seqs_by_cond[cond_b][s][f"player_{pid}"],
            )
            for pid in le.PLAYER_IDS
        ]
        player_means[s] = round(sum(p_rates) / len(p_rates), 6)

    out["lineups_mean"] = summarize_differences(lineup_means, resamples)
    out["coaches_mean"] = summarize_differences(coach_means, resamples)
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
    for m in T011_METRICS:
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
    for m in T011_METRICS:
        diffs = {
            s: round(
                (float(runs_by_cond["open_solidarity"][s][m]) - float(runs_by_cond["open_no_solidarity"][s][m]))
                - (float(runs_by_cond["closed_solidarity"][s][m]) - float(runs_by_cond["closed_no_solidarity"][s][m])),
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
        for m in T011_METRICS
    }


def _long_horizon_stress(out_dir: Path) -> dict[str, Any]:
    stress_dir = out_dir / "stress_1000"
    stress_dir.mkdir(parents=True, exist_ok=True)
    cfg = le.config_for_condition("open_solidarity")
    t0 = time.perf_counter()
    w1000 = le.run_league_world(seed=17, days=1000, config=cfg)
    sim_sec = round(time.perf_counter() - t0, 6)

    t_w0 = time.perf_counter()
    le.save_league_artifacts(w1000, stress_dir / "day1000")
    write_sec = round(time.perf_counter() - t_w0, 6)

    snap_path = stress_dir / "day1000" / "snapshot.json"
    jsonl_path = stress_dir / "day1000" / "events.jsonl"

    t_l0 = time.perf_counter()
    loaded = le.load_league_snapshot(snap_path)
    load_sec = round(time.perf_counter() - t_l0, 6)

    le.advance_league_days(loaded, 14)
    w1014 = le.run_league_world(seed=17, days=1014, config=cfg)

    byte_equal = digest(le.snapshot_payload(loaded)) == digest(le.snapshot_payload(w1014))
    matches_1000 = sum(1 for e in w1000.events if e["kind"] == "league_match_completed")
    max_rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return {
        "condition": "open_solidarity",
        "seed": 17,
        "days": 1000,
        "resumed_extra_days": 14,
        "total_days_verified": 1014,
        "events_in_1000_days": len(w1000.events),
        "matches_in_1000_days": matches_1000,
        "simulation_seconds": sim_sec,
        "write_seconds": write_sec,
        "reader": "full_history_replay",
        "load_seconds_replay": load_sec,
        "snapshot_bytes": snap_path.stat().st_size,
        "events_jsonl_bytes": jsonl_path.stat().st_size,
        "max_rss_raw": max_rss,
        "ledger_discrepancy_1000_units": le.compute_ledger_discrepancy(w1000),
        "resume_1000_plus_14_byte_equal": byte_equal,
    }


def run_t011_suite(out_dir: str | Path, *, days: int = DAYS) -> dict[str, Any]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    t_start = time.perf_counter()

    sets_payload: dict[str, Any] = {}
    conf_runs: dict[str, dict[int, dict[str, Any]]] = {c: {} for c in le.T011_MAIN_CONDITIONS}
    conf_seqs: dict[str, dict[int, dict[str, list[Any]]]] = {c: {} for c in le.T011_MAIN_CONDITIONS}
    all_run_rows: list[dict[str, Any]] = []

    for set_name, seeds in SEED_SETS.items():
        by_cond_series: dict[str, dict[str, Any]] = {}
        by_cond_means: dict[str, dict[str, float]] = {}
        for cond in le.T011_MAIN_CONDITIONS:
            cfg = le.config_for_condition(cond)
            cond_runs: dict[int, dict[str, Any]] = {}
            for s in seeds:
                w = le.run_league_world(seed=s, days=days, config=cfg)
                summ = le.summarize_league_world(w)
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
                for m in T011_METRICS
            }
        sets_payload[set_name] = {
            "seeds": list(seeds),
            "means": by_cond_means,
            "series": by_cond_series,
        }

    resamples_conf = _make_resamples(CONFIRMATION_SEEDS, seed_offset=0)

    conf_contrasts = {
        "open_minus_closed_solidarity": _contrast_metrics(
            conf_runs, "open_solidarity", "closed_solidarity", CONFIRMATION_SEEDS, resamples_conf
        ),
        "open_minus_closed_no_solidarity": _contrast_metrics(
            conf_runs, "open_no_solidarity", "closed_no_solidarity", CONFIRMATION_SEEDS, resamples_conf
        ),
        "solidarity_minus_no_solidarity_open": _contrast_metrics(
            conf_runs, "open_solidarity", "open_no_solidarity", CONFIRMATION_SEEDS, resamples_conf
        ),
        "solidarity_minus_no_solidarity_closed": _contrast_metrics(
            conf_runs, "closed_solidarity", "closed_no_solidarity", CONFIRMATION_SEEDS, resamples_conf
        ),
        "interaction_market_by_solidarity": _interaction_contrast(
            conf_runs, CONFIRMATION_SEEDS, resamples_conf
        ),
    }

    conf_disagreement = {
        "open_minus_closed_solidarity": _compute_disagreement_summary(
            conf_seqs, "open_solidarity", "closed_solidarity", CONFIRMATION_SEEDS, resamples_conf
        ),
        "solidarity_minus_no_solidarity_open": _compute_disagreement_summary(
            conf_seqs, "open_solidarity", "open_no_solidarity", CONFIRMATION_SEEDS, resamples_conf
        ),
    }

    # Extra single-factor & adversarial controls on confirmation seeds
    extra_runs: dict[str, dict[int, dict[str, Any]]] = {
        "open_solidarity": conf_runs["open_solidarity"]
    }
    extra_seqs: dict[str, dict[int, dict[str, list[Any]]]] = {
        "open_solidarity": conf_seqs["open_solidarity"]
    }
    for cond in le.T011_EXTRA_CONDITIONS:
        cfg = le.config_for_condition(cond)
        extra_runs[cond] = {}
        extra_seqs[cond] = {}
        for s in CONFIRMATION_SEEDS:
            w = le.run_league_world(seed=s, days=days, config=cfg)
            extra_runs[cond][s] = le.summarize_league_world(w)
            all_run_rows.append({
                "set": "confirmation_extra", "condition": cond,
                "seed": s, "days": days, "summary": extra_runs[cond][s],
            })
            extra_seqs[cond][s] = _extract_action_sequences(w)

    extra_means = {
        cond: _condition_means(extra_runs[cond], CONFIRMATION_SEEDS)
        for cond in ("open_solidarity", *le.T011_EXTRA_CONDITIONS)
    }
    extra_effects = {
        f"{cond}_minus_open_solidarity": _contrast_metrics(
            extra_runs, cond, "open_solidarity", CONFIRMATION_SEEDS, resamples_conf
        )
        for cond in le.T011_EXTRA_CONDITIONS
    }
    extra_disagreement = {
        f"{cond}_minus_open_solidarity": _compute_disagreement_summary(
            extra_seqs, cond, "open_solidarity", CONFIRMATION_SEEDS, resamples_conf
        )
        for cond in le.T011_EXTRA_CONDITIONS
    }

    long_horizon = _long_horizon_stress(out)
    elapsed = round(time.perf_counter() - t_start, 6)

    comparison = {
        "suite": "t011",
        "world_kind": le.LEAGUE_WORLD_KIND,
        "author": le.LEAGUE_AUTHOR,
        "days": days,
        "run_count": len(all_run_rows),
        "main_run_count": sum(len(seeds) for seeds in SEED_SETS.values()) * len(le.T011_MAIN_CONDITIONS),
        "extra_run_count": len(CONFIRMATION_SEEDS) * len(le.T011_EXTRA_CONDITIONS),
        "versions": le.league_identity(),
        "runtime": runtime(),
        "runs": all_run_rows,
    }

    summary = {
        "suite": "t011",
        "world_kind": le.LEAGUE_WORLD_KIND,
        "author": le.LEAGUE_AUTHOR,
        "days": days,
        "run_count": len(all_run_rows),
        "main_run_count": sum(len(seeds) for seeds in SEED_SETS.values()) * len(le.T011_MAIN_CONDITIONS),
        "extra_run_count": len(CONFIRMATION_SEEDS) * len(le.T011_EXTRA_CONDITIONS),
        "elapsed_seconds": elapsed,
        "versions": le.league_identity(),
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
    atomic_write(out / "runs.jsonl", [canonical(r) + b"\n" for r in all_run_rows])
    return summary


def run_t011(out_dir: str | Path) -> dict[str, Any]:
    return run_t011_suite(out_dir, days=DAYS)
