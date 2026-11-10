"""Finish the T014 evidence audit, including deliberately corrupted ledgers."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from soccer_sim import league_economy as le
from soccer_sim.storage import write_json
from audit_t014 import ledger_audit, admission_audit


def read(path):
    return json.loads(path.read_text())


def verify(task):
    results = task / "comparison"
    summary = read(results / "summary.json")
    assert summary["identity"] == le.league_identity()
    assert summary["economic_runs"] == summary["economic_full_history_equal"] == 240
    assert summary["branch_series"] == summary["branch_full_history_equal"] == 30
    assert summary["long_horizon"]["comparison"]["equal"]
    assert summary["long_horizon"]["resume_plus14_equal"]
    assert summary["long_horizon"]["chained_same_day_plus7_equal"]
    tests = (task / "review/tests-sol.txt").read_text()
    assert "Ran 168 tests" in tests and tests.rstrip().endswith("OK")
    fast = (task / "review/tests-fast.txt").read_text()
    assert "Ran 33 tests" in fast and fast.rstrip().endswith("OK")
    report = {"identity": le.league_identity(), "full_tests": 168, "fast_tests": 33,
              "economic_full_history_equal": 240, "branch_full_history_equal": 30,
              "long_1000_full_history_equal": True, "negative_audits": {}}
    representative = read(results / "representative/open_solidarity/full/snapshot.json")
    for name in ("one_unit_transaction_change", "float_transaction", "duplicate_dividend"):
        corrupted = copy.deepcopy(representative)
        if name == "duplicate_dividend":
            payment = next(e for e in corrupted["events"] if e["kind"] == "ledger_transaction"
                           and e["data"]["reason"] == "development_appearance_dividend")
            corrupted["events"].insert(corrupted["events"].index(payment) + 1, copy.deepcopy(payment))
            check = admission_audit
        else:
            payment = next(e for e in corrupted["events"] if e["kind"] == "ledger_transaction")
            payment["data"]["amount_units"] = (payment["data"]["amount_units"] + 1
                if name == "one_unit_transaction_change" else float(payment["data"]["amount_units"]))
            check = ledger_audit
        try:
            check(corrupted)
        except AssertionError:
            report["negative_audits"][name] = "rejected"
        else:
            raise AssertionError(f"Independent auditor accepted {name}")
    world = le.run_league_world(seed=100, days=1)
    world.club_accounts["club_a"].cash_units += 1
    try:
        le._validate_league_invariants(world)
    except ValueError:
        report["runtime_one_unit_error_rejected"] = True
    else:
        raise AssertionError("Runtime conservation check tolerated a unit error")
    long = read(results / "stress_1000/chained1014/snapshot.json")
    report["chained1014_independent_audits"] = {"ledger": ledger_audit(long), "admission": admission_audit(long)}
    inventory = read(task / "preflight/historical-inventory.json")
    for name, entry in inventory.items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == entry["sha256"], name
    report["historical_files_unchanged"] = len(inventory)
    assert all(read(task / "review/policy-ast.json")["unchanged_function_ast"].values())
    report["unchanged_policy_competition_intervention_ast"] = 11
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        parser.error("output exists; use a new verification file")
    report = verify(args.task_root)
    write_json(args.out, report)
    print(json.dumps({k: v for k, v in report.items() if k != "chained1014_independent_audits"}))
