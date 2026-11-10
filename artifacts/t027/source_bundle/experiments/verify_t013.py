"""Final T013 evidence checks. Requires completed runs; never writes old outputs."""
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
from audit_t013 import ledger_audit, admission_audit


def read(path):
    return json.loads(path.read_text())


def verify(task):
    report = {"identity": le.league_identity()}
    final = task / "final"
    for suite in ("economy", "branches"):
        old = read(task / suite / "comparison.json")["runs"]
        current = read(final / suite / "comparison.json")
        assert current["identity"] == report["identity"]
        now = current["runs"]
        assert len(old) == len(now)
        for a, b in zip(old, now):
            assert a["seed"] == b["seed"]
            if suite == "economy":
                for field in ("condition", "config", "ledger_audit", "admission_audit"):
                    assert a[field] == b[field], field
                # Identity is expected to differ after the evidence-reference edit.
                assert {k: v for k, v in a["summary"].items() if k != "identity"} == {
                    k: v for k, v in b["summary"].items() if k != "identity"}
            else:
                for field in ("set", "variant", "metrics", "p3_end_club", "semantic_projection_sha256"):
                    assert a[field] == b[field], field
                first = b["first_behavior_difference"]
                if first:
                    assert first["evidence_path_to_research_intervention"]
        report[f"{suite}_unchanged_numeric_and_semantic_runs"] = len(now)
    report["all_first_changed_choices_have_intervention_path"] = True
    report["independent_audits"] = {}
    snapshots = list((final / "economy/representative").glob("*/full/snapshot.json"))
    snapshots += [final / "branches/stress_1000" / p / "snapshot.json" for p in ("day1000", "chained1014")]
    for path in snapshots:
        raw = read(path)
        report["independent_audits"][str(path.relative_to(task))] = {
            "ledger": ledger_audit(raw), "admission": admission_audit(raw)}
    raw = read(final / "economy/representative/exploit_churn_guard/full/snapshot.json")
    payment = next(e for e in raw["events"] if e["kind"] == "ledger_transaction"
                   and e["data"]["reason"] == "development_appearance_dividend")
    raw["events"].insert(raw["events"].index(payment) + 1, copy.deepcopy(payment))
    try:
        admission_audit(raw)
    except AssertionError:
        report["injected_duplicate_payment_rejected_by_independent_audit"] = True
    else:
        raise AssertionError("duplicate payment not detected")
    inventory = read(ROOT / "artifacts/t012/preflight/historical-inventory.json")
    for path, entry in inventory.items():
        assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == entry["sha256"], path
    report["historical_files_unchanged"] = len(inventory)
    tests = (task / "review/tests-final.txt").read_text()
    assert "Ran 153 tests" in tests and tests.rstrip().endswith("OK")
    report["full_tests_passed"] = 153
    report["runtime_identity_unchanged"] = le.league_identity() == report["identity"]
    assert report["runtime_identity_unchanged"]
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-root", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if args.out.exists():
        parser.error("verification output already exists; use a fresh path")
    report = verify(args.task_root)
    write_json(args.out, report)
    print(json.dumps({k: v for k, v in report.items() if k != "independent_audits"}))
