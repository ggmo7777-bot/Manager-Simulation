"""T012 paired branch experiments. Require a fresh output directory.

python3 -B experiments/t012.py --out artifacts/t013/branches
Frozen historical artifacts are read only; no fixed old output paths are used.
"""
import argparse
from collections import deque
import copy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from random import Random
import resource
from statistics import mean
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from soccer_sim import league_economy as le
from soccer_sim.experiment_t002 import summarize_differences
from soccer_sim.storage import canonical, digest, runtime, write_json

SEEDS = {"exploration": tuple(range(1, 11)), "confirmation": tuple(range(100, 130))}
VARIANTS = ("baseline", "noop", "coach_high", "trust_high", "trust_low")
METRICS = ("p3_appearances_after14", "p3_transfers", "p3_gain_after14", "p3_satisfaction",
           "p3_trust_c1", "mean_skill_gain", "academy_clubs_points", "academy_clubs_cash",
           "p3_training_disagreement", "lineup_disagreement", "all_decisions_disagreement")


def make_branch(parent, variant):
    if variant == "baseline":
        return copy.deepcopy(parent)
    kind = "coach_skill_estimate" if variant in ("noop", "coach_high") else "player_trust"
    value = {"noop": parent.coaches["c1"].evaluations["p3"].skill_estimate,
             "coach_high": 0.90, "trust_high": 0.95, "trust_low": 0.05}[variant]
    return le.fork_league_world(parent, kind=kind, player_id="p3", coach_id="c1", value=value,
                                reason=f"T012 preregistered {variant} at day14")


def semantic_projection(world):
    """No-op comparison: remove ONLY research metadata and evidence pointers.

    Keep numerical beliefs, candidate scores, physical state, accounting and
    all nonresearch events. This is not used to validate a saved checkpoint.
    """
    def strip(value):
        if isinstance(value, dict):
            return {k: strip(v) for k, v in value.items()
                    if k not in ("id", "identity", "research_branch", "interventions")
                    and not k.endswith(("_ref", "_refs"))}
        if isinstance(value, (list, tuple)):
            return [strip(v) for v in value]
        return value
    payload = le._snapshot_payload_unchecked(world)
    payload = {**payload, "events": [e for e in world.events if e["kind"] != "research_intervention"]}
    return strip(payload)


def decisions(world, after=14):
    rows = {}
    for event in world.events:
        if event["day"] <= after:
            continue
        data, kind, actor = event["data"], event["kind"], event["actor"]
        slot = data.get("slot", 0)
        if kind == "player_training_decision":
            choice = data["chosen"]["action"]
        elif kind == "coach_training_decision":
            choice = [data["chosen"][x] for x in ("kind", "target", "method")]
        elif kind == "lineup_decision":
            choice = list(data["starters"])
        elif kind == "transfer_window_decision":
            slot = data["player_id"]
            choice = [data["transfer_approved"], data["chosen_to_club"]]
        else:
            continue
        key = (event["day"], kind, actor, slot)
        assert key not in rows
        rows[key] = {"choice": choice, "id": event["id"], "cause_refs": event["cause_refs"]}
    return rows


def differences(base, other, predicate=lambda key: True):
    keys = [k for k in base if predicate(k)] + [k for k in other if k not in base and predicate(k)]
    count = sum(base.get(k, {}).get("choice") != other.get(k, {}).get("choice") for k in keys)
    return count / len(keys) if keys else 0.0


def first_difference(base, other, world):
    candidates = []
    for key in set(base) | set(other):
        b, o = base.get(key), other.get(key)
        if (b or {}).get("choice") != (o or {}).get("choice"):
            event_index = int((o or b)["id"][1:])
            candidates.append((key[0], event_index, key, b, o))
    if not candidates:
        return None
    _, _, key, b, o = min(candidates)
    events = {e["id"]: e for e in world.events}
    path = None
    queue = deque([(o["id"], [o["id"]])]) if o else deque()
    seen = set()
    while queue:
        ref, chain = queue.popleft()
        if ref in seen or ref not in events:
            continue
        seen.add(ref)
        event = events[ref]
        if event["kind"] == "research_intervention":
            path = chain
            break
        queue.extend((p, chain + [p]) for p in event["cause_refs"])
    return {"day": key[0], "kind": key[1], "actor": key[2], "baseline": b, "branch": o,
            "evidence_path_to_research_intervention": path,
            "interpretation": "earliest changed chosen action; graph link alone is not a causal proof"}


def verify_immediate(parent, branch, variant):
    for field in ("players", "initial_players", "club_rosters", "club_accounts", "ledger", "public_last_starters", "config"):
        assert getattr(parent, field) == getattr(branch, field), (variant, field)
    if variant == "baseline":
        assert parent == branch
        return
    assert branch.events[:-1] == parent.events
    assert branch.events[-1]["kind"] == "research_intervention"
    rec = branch.interventions[-1]
    assert rec.parent_checkpoint_sha256 == digest(le._snapshot_payload_unchecked(parent))
    # Independent expected state: exactly one belief plus its evidence pointer.
    expected_coaches, expected_minds = copy.deepcopy(parent.coaches), copy.deepcopy(parent.player_minds)
    if rec.kind == "coach_skill_estimate":
        expected_coaches["c1"].evaluations["p3"].skill_estimate = rec.after_value
        expected_coaches["c1"].evaluations["p3"].last_obs_ref = branch.events[-1]["id"]
    else:
        expected_minds["p3"].trust_by_coach["c1"] = rec.after_value
        expected_minds["p3"].last_obs_ref = branch.events[-1]["id"]
    assert branch.coaches == expected_coaches and branch.player_minds == expected_minds


def verify_resume(parent, variant, continuous, out):
    branch = make_branch(parent, variant)
    le.save_league_artifacts(branch, out / "at14")
    at14 = le.load_league_snapshot(out / "at14/snapshot.json")
    assert branch == at14
    le.advance_league_days(at14, 28)
    le.save_league_artifacts(at14, out / "at42")
    resumed = le.load_league_snapshot(out / "at42/snapshot.json")
    le.advance_league_days(resumed, 42)
    assert resumed == continuous
    le.save_league_artifacts(resumed, out / "resumed84")
    le.save_league_artifacts(continuous, out / "full84")
    for name in ("snapshot.json", "events.jsonl"):
        assert (out / "resumed84" / name).read_bytes() == (out / "full84" / name).read_bytes()
    return {"day14_save_equal": True, "84_vs_14_28_42_byte_equal": True}


def compatibility(out):
    reference = json.loads((ROOT / "artifacts/t012/preflight/r1-reference.json").read_text())
    results = []
    for row in reference["runs"]:
        w = le.run_league_world(seed=row["seed"], days=row["days"], config=le.config_for_condition(row["condition"]))
        payload = le.snapshot_payload(w)
        for key in ("identity", "research_branch", "interventions"):
            payload.pop(key)
        actual = digest(payload)
        assert actual == row["full_projection_sha256"], row
        results.append({"seed": row["seed"], "condition": row["condition"], "projection_sha256": actual})
    write_json(out / "compatibility.json", {"old_identity": reference["identity"], "new_identity": le.league_identity(),
        "excludes": ["identity", "new empty interventions", "new false research_branch"], "matched": len(results), "runs": results})


def long_horizon(out):
    parent = le.run_league_world(seed=17, days=14)
    t = perf_counter()
    branch = make_branch(parent, "coach_high")
    le.advance_league_days(branch, 986)
    sim = perf_counter() - t
    t = perf_counter(); le.save_league_artifacts(branch, out / "day1000"); save = perf_counter() - t
    t = perf_counter(); loaded = le.load_league_snapshot(out / "day1000/snapshot.json"); load = perf_counter() - t
    assert loaded == branch
    le.advance_league_days(loaded, 14)
    le.advance_league_days(branch, 14)
    assert loaded == branch
    # A second intervention on the already branched world, then a same-day third.
    second = le.fork_league_world(loaded, kind="player_trust", player_id="p3", coach_id="c1", value=0.05, reason="T012 chained branch")
    third = le.fork_league_world(second, kind="coach_skill_estimate", player_id="p3", coach_id="c1", value=0.90, reason="T012 same-day third intervention")
    le.save_league_artifacts(third, out / "chained1014")
    restored = le.load_league_snapshot(out / "chained1014/snapshot.json")
    assert restored == third
    le.advance_league_days(third, 7); le.advance_league_days(restored, 7)
    assert restored == third
    return {"seed": 17, "first_intervention_day": 14, "days": 1000,
            "simulation_seconds_including_fork": sim, "save_seconds": save, "load_seconds_replay": load,
            "resume_1000_plus_14_equal": True, "chained_and_same_day_interventions_plus7_equal": True,
            "snapshot_bytes": (out / "day1000/snapshot.json").stat().st_size,
            "events_jsonl_bytes": (out / "day1000/events.jsonl").stat().st_size,
            "max_rss_raw": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}


def run(out):
    identity = le.league_identity()
    all_rows, by_set, examples, resume_results = [], {}, {}, {}
    t = perf_counter()
    # D028 amendment: runtime rules changed after the separate pre-correction
    # T012 compatibility check; do not assert equality with the defective R1 rules.
    reference = ROOT / "artifacts/t012/precorrection/compatibility.json"
    assert json.loads(reference.read_text())["matched"] == 24
    for set_name, seeds in SEEDS.items():
        by_set[set_name] = {v: {} for v in VARIANTS}
        for seed in seeds:
            parent = le.run_league_world(seed=seed, days=14)
            parent_hash = digest(le.snapshot_payload(parent))
            branches = {v: make_branch(parent, v) for v in VARIANTS}
            for variant, world in branches.items():
                verify_immediate(parent, world, variant)
                le.advance_league_days(world, 70)
            assert digest(le.snapshot_payload(parent)) == parent_hash
            assert semantic_projection(branches["baseline"]) == semantic_projection(branches["noop"])
            base_dec = decisions(branches["baseline"])
            for variant, w in branches.items():
                seq = decisions(w)
                summary = le.summarize_league_world(w)
                assert summary["ledger_discrepancy"] == 0 and summary["insolvency_events"] == 0
                metrics = {"p3_appearances_after14": w.players["p3"].appearances - parent.players["p3"].appearances,
                    "p3_transfers": w.players["p3"].transfers_count,
                    "p3_gain_after14": round(w.players["p3"].composite_skill() - parent.players["p3"].composite_skill(), 6),
                    "p3_satisfaction": w.player_minds["p3"].playing_time_satisfaction,
                    "p3_trust_c1": w.player_minds["p3"].trust_by_coach["c1"],
                    **{m: summary[m] for m in ("mean_skill_gain", "academy_clubs_points", "academy_clubs_cash")},
                    "p3_training_disagreement": differences(base_dec, seq, lambda k: k[1] == "player_training_decision" and k[2] == "p3"),
                    "lineup_disagreement": differences(base_dec, seq, lambda k: k[1] == "lineup_decision"),
                    "all_decisions_disagreement": differences(base_dec, seq)}
                first = first_difference(base_dec, seq, w)
                row = {"set": set_name, "seed": seed, "variant": variant, "parent_day": 14, "end_day": 84,
                       "parent_checkpoint_sha256": parent_hash, "metrics": metrics, "p3_end_club": w.players["p3"].club_id,
                       "interventions": [asdict(r) for r in w.interventions], "first_behavior_difference": first,
                       "semantic_projection_sha256": digest(semantic_projection(w)), "ledger_discrepancy": summary["ledger_discrepancy"]}
                all_rows.append(row); by_set[set_name][variant][seed] = row
                if seed == 100:
                    resume_results[variant] = verify_resume(parent, variant, w, out / "representative" / variant)
                    examples[variant] = row
            if seed == seeds[-1]:
                print(f"{set_name}: {len(seeds) * len(VARIANTS)} series complete", flush=True)
    rng = Random(12012)
    resamples = [tuple(rng.randrange(30) for _ in range(30)) for _ in range(2000)]
    confirmation = by_set["confirmation"]
    effects = {v: {m: summarize_differences({s: confirmation[v][s]["metrics"][m] - confirmation["baseline"][s]["metrics"][m]
                                            for s in SEEDS["confirmation"]}, resamples) for m in METRICS}
               for v in VARIANTS if v != "baseline"}
    series = {v: {"means": {m: mean(confirmation[v][s]["metrics"][m] for s in SEEDS["confirmation"]) for m in METRICS},
                   "seeds_with_behavior_difference": sum(confirmation[v][s]["first_behavior_difference"] is not None for s in SEEDS["confirmation"]),
                   "seeds_with_evidence_path": sum(bool((confirmation[v][s]["first_behavior_difference"] or {}).get("evidence_path_to_research_intervention")) for s in SEEDS["confirmation"])}
              for v in VARIANTS}
    long = long_horizon(out / "stress_1000")
    assert le.league_identity() == identity, "Source changed during comparison"
    summary = {"task": "T012-on-T013", "protocol_amendment": "D028", "identity": identity, "runtime": runtime(), "run_count": len(all_rows),
        "sets": {k: list(v) for k, v in SEEDS.items()}, "confirmation": series, "paired_effects_vs_baseline": effects,
        "noop_semantics_equal_all_40_seeds": True, "parent_and_A_preserved_at_intervention_all_series": True,
        "representative_resume": resume_results, "long_horizon": long, "elapsed_seconds": perf_counter() - t}
    write_json(out / "summary.json", summary)
    write_json(out / "comparison.json", {"identity": identity, "runs": all_rows})
    write_json(out / "examples.json", examples)
    (out / "runs.jsonl").write_bytes(b"".join(canonical(row) + b"\n" for row in all_rows))
    print(f"T012 complete: {len(all_rows)} series, all requested persistence checks", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if le.LEAGUE_SAVE_VERSION != 14:
        parser.error("This historical T012-on-T013 protocol requires save14. Use the frozen T013 source_bundle, or experiments/t014.py for the current migration checks.")
    args.out.mkdir(parents=True, exist_ok=False)
    run(args.out)
