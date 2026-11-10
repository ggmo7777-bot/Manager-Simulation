"""T014 whole-history migration checks against isolated frozen T013 runtime.

New output directories only. No changes to frozen code or historical outputs.
"""
import argparse
import copy
from dataclasses import replace
from decimal import Decimal, localcontext
import hashlib
import importlib.util
import json
from pathlib import Path
import resource
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from soccer_sim import league_economy as le
from soccer_sim.storage import canonical, digest, runtime, write_json
from audit_t014 import admission_audit, ledger_audit

# Independently enumerated against the frozen schema, NOT imported from the
# implementation's converter. Unknown legacy money fields are not auto-scaled.
LEGACY_MONEY_FIELDS = frozenset("""
initial_academy_cash initial_buyer_cash facility_reserve_floor facility_daily_cost
matchday_base_inflow matchday_win_bonus matchday_draw_bonus player_matchday_wage
scout_action_cost base_transfer_fee_solidarity solidarity_transfer_grant
base_transfer_fee_no_solidarity development_dividend_per_start buyer_min_cash_reserve
cash initial_cash cumulative_inflow cumulative_wages_paid cumulative_facility_paid
cumulative_scouting_paid cumulative_transfer_fees_paid cumulative_transfer_fees_received
cumulative_solidarity_paid cumulative_solidarity_received cumulative_wages_received
base_fee solidarity_grant dividend_per_start initial_total_cash cumulative_external_inflow
cumulative_external_outflow amount cash_after required_upfront_fee solidarity_dividends_paid
academy_clubs_cash buyer_clubs_cash buyer_minus_academy_cash ledger_discrepancy
""".split())
SEEDS = tuple(range(100, 130))
BRANCH_SEEDS = (1, 2, 3, 100, 101, 102)
VARIANTS = ("baseline", "noop", "coach_high", "trust_high", "trust_low")


def read(path):
    return json.loads(path.read_text())


def legacy_units(value):
    assert type(value) in (int, float), value
    # Plenty of precision even if the fixture later uses large recorded amounts.
    with localcontext() as context:
        context.prec = max(50, len(str(value)) + 20)
        scaled = Decimal(str(value)) * 1_000_000
        assert scaled.is_finite() and scaled == scaled.to_integral_value()
        return int(scaled)


def normalized(value, *, legacy):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key in ("identity", "parent_checkpoint_sha256"):
                continue
            if legacy and key in LEGACY_MONEY_FIELDS:
                result[key + "_units"] = legacy_units(item)
            else:
                if not legacy and key.endswith("_units"):
                    assert type(item) is int, (key, item)
                if not legacy and key in LEGACY_MONEY_FIELDS:
                    raise AssertionError(f"unconverted monetary key: {key}")
                result[key] = normalized(item, legacy=legacy)
        return result
    if isinstance(value, (list, tuple)):
        return [normalized(v, legacy=legacy) for v in value]
    return value


def first_difference(a, b, path="root"):
    if type(a) is not type(b):
        return {"path": path, "old": str(a)[:200], "new": str(b)[:200], "kind": "type"}
    if isinstance(a, dict):
        if a.keys() != b.keys():
            return {"path": path, "old_only": sorted(a.keys() - b.keys()), "new_only": sorted(b.keys() - a.keys())}
        for key in a:
            difference = first_difference(a[key], b[key], f"{path}.{key}")
            if difference:
                return difference
    elif isinstance(a, list):
        if len(a) != len(b):
            return {"path": path, "old_length": len(a), "new_length": len(b)}
        for i, (x, y) in enumerate(zip(a, b)):
            difference = first_difference(x, y, f"{path}[{i}]")
            if difference:
                return difference
    elif a != b:
        return {"path": path, "old": a, "new": b, "kind": "value"}
    return None


def compare(old, new):
    before, after = normalized(old, legacy=True), normalized(new, legacy=False)
    old_hash, new_hash = digest(before), digest(after)
    return {"equal": old_hash == new_hash, "old_normalized_sha256": old_hash,
            "new_normalized_sha256": new_hash,
            "first_difference": None if old_hash == new_hash else first_difference(before, after)}


def frozen_runtime():
    directory = ROOT / "artifacts/t013/source_bundle"
    manifest = read(directory / "manifest.json")
    for name, expected in manifest["files"].items():
        assert hashlib.sha256((directory / name).read_bytes()).hexdigest() == expected, name
    package = directory / "soccer_sim"
    spec = importlib.util.spec_from_file_location("_t014_reference", package / "__init__.py",
                                                 submodule_search_locations=[str(package)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    reference = importlib.import_module("_t014_reference.league_economy")
    assert reference.league_identity() == manifest["identity"]
    return reference


def config(module, condition):
    cfg = module.config_for_condition(condition if condition in module.T011_ALL_CONDITIONS else "open_solidarity")
    if condition.endswith("perfect_fatigue"):
        cfg = replace(cfg, self_fatigue_noise=0.0, coach_fatigue_noise=0.0,
                      scout_fatigue_noise=0.0, match_fatigue_noise=0.0)
    return cfg


def branch(module, parent, variant):
    if variant == "baseline":
        return copy.deepcopy(parent)
    kind = "coach_skill_estimate" if variant in ("noop", "coach_high") else "player_trust"
    value = {"noop": parent.coaches["c1"].evaluations["p3"].skill_estimate,
             "coach_high": 0.9, "trust_high": 0.95, "trust_low": 0.05}[variant]
    return module.fork_league_world(parent, kind=kind, player_id="p3", coach_id="c1", value=value,
                                   reason=f"T012 preregistered {variant} at day14")


def audit(world):
    payload = le._snapshot_payload_unchecked(world)
    return {"ledger": ledger_audit(payload), "admission": admission_audit(payload)}


def resume_pair(full, start, days, directory):
    le.save_league_artifacts(start, directory / "start")
    restored = le.load_league_snapshot(directory / "start/snapshot.json")
    le.advance_league_days(restored, days)
    assert restored == full
    le.save_league_artifacts(full, directory / "full")
    le.save_league_artifacts(restored, directory / "resumed")
    equal = {f: (directory / "full" / f).read_bytes() == (directory / "resumed" / f).read_bytes()
             for f in ("snapshot.json", "events.jsonl")}
    assert all(equal.values())
    return equal


def run(out):
    identity = le.league_identity()
    if identity["save"] != 15:
        raise SystemExit("Historical T014 protocol requires save15. Use the frozen artifacts/t014/source_bundle, or experiments/t015.py for current checks.")
    assert identity["money_scale"] == 1_000_000
    old = frozen_runtime()
    previous = read(ROOT / "artifacts/t013/final/economy/comparison.json")["runs"]
    previous = {(r["condition"], r["seed"]): r["summary"] for r in previous}
    start = perf_counter()
    rows, resumes = [], {}
    for condition in (*le.T011_ALL_CONDITIONS, "open_solidarity_perfect_fatigue"):
        for seed in SEEDS:
            past = old.run_league_world(seed=seed, days=84, config=config(old, condition))
            old_summary = old.summarize_league_world(past)
            assert old_summary == previous[condition, seed], "Frozen replay differs from recorded T013"
            current = le.run_league_world(seed=seed, days=84, config=config(le, condition))
            result = compare(old._snapshot_payload_unchecked(past), le._snapshot_payload_unchecked(current))
            row = {"condition": condition, "seed": seed, "days": 84, "comparison": result,
                   "summary": le.summarize_league_world(current), "audits": audit(current)}
            rows.append(row)
            if seed == 100 and condition in le.T011_MAIN_CONDITIONS:
                half = le.run_league_world(seed=seed, days=42, config=config(le, condition))
                resumes[condition] = resume_pair(current, half, 42, out / "representative" / condition)
        print(f"{condition}: 30 full-history comparisons and audits complete", flush=True)
    write_json(out / "economy.json", {"identity": identity, "old_identity": old.league_identity(), "runs": rows})
    branch_rows, branch_resumes = [], {}
    for seed in BRANCH_SEEDS:
        old_parent = old.run_league_world(seed=seed, days=14)
        parent = le.run_league_world(seed=seed, days=14)
        for variant in VARIANTS:
            before, after = branch(old, old_parent, variant), branch(le, parent, variant)
            old.advance_league_days(before, 70)
            le.advance_league_days(after, 70)
            branch_rows.append({"seed": seed, "variant": variant,
                "comparison": compare(old._snapshot_payload_unchecked(before), le._snapshot_payload_unchecked(after)),
                "summary": le.summarize_league_world(after), "audits": audit(after)})
            if seed == 100:
                half = branch(le, parent, variant)
                le.advance_league_days(half, 28)
                branch_resumes[variant] = resume_pair(after, half, 42, out / "branches" / variant)
        print(f"branch seed {seed}: five whole-history comparisons complete", flush=True)
    write_json(out / "branches.json", {"identity": identity, "old_identity": old.league_identity(), "runs": branch_rows})
    parent = le.run_league_world(seed=17, days=14)
    t = perf_counter(); long = branch(le, parent, "coach_high"); le.advance_league_days(long, 986)
    simulation = perf_counter() - t
    old_raw = read(ROOT / "artifacts/t013/final/branches/stress_1000/day1000/snapshot.json")
    long_comparison = compare(old_raw, le._snapshot_payload_unchecked(long)); del old_raw
    long_audit = audit(long)
    t = perf_counter(); le.save_league_artifacts(long, out / "stress_1000/day1000"); save = perf_counter() - t
    t = perf_counter(); restored = le.load_league_snapshot(out / "stress_1000/day1000/snapshot.json"); load = perf_counter() - t
    assert restored == long
    le.advance_league_days(long, 14); le.advance_league_days(restored, 14)
    assert restored == long
    second = le.fork_league_world(restored, kind="player_trust", player_id="p3", coach_id="c1", value=0.05, reason="T012 chained branch")
    third = le.fork_league_world(second, kind="coach_skill_estimate", player_id="p3", coach_id="c1", value=0.9, reason="T012 same-day third intervention")
    le.save_league_artifacts(third, out / "stress_1000/chained1014")
    again = le.load_league_snapshot(out / "stress_1000/chained1014/snapshot.json")
    assert again == third
    le.advance_league_days(third, 7); le.advance_league_days(again, 7)
    assert again == third
    assert le.league_identity() == identity, "Runtime source changed during experiment"
    report = {"task": "T014", "identity": identity, "old_identity": old.league_identity(), "runtime": runtime(),
        "economic_runs": len(rows), "economic_full_history_equal": sum(r["comparison"]["equal"] for r in rows),
        "branch_series": len(branch_rows), "branch_full_history_equal": sum(r["comparison"]["equal"] for r in branch_rows),
        "resumed84": resumes, "branch_resumed84": branch_resumes,
        "long_horizon": {"comparison": long_comparison, "audit": long_audit, "days": 1000,
                         "simulation_seconds": simulation, "save_seconds": save, "load_seconds": load,
                         "resume_plus14_equal": True, "chained_same_day_plus7_equal": True,
                         "snapshot_bytes": (out / "stress_1000/day1000/snapshot.json").stat().st_size},
        "elapsed_seconds": perf_counter() - start, "max_rss_raw": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "normalization": {"scale": 1_000_000, "excludes": ["identity", "parent_checkpoint_sha256"],
                          "legacy_monetary_fields": sorted(LEGACY_MONEY_FIELDS)},
        "all_old_runs_match_T013_stored_summaries": True}
    write_json(out / "summary.json", report)
    print(json.dumps({k: report[k] for k in ("economic_runs", "economic_full_history_equal", "branch_series", "branch_full_history_equal", "elapsed_seconds")}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    run(args.out)
