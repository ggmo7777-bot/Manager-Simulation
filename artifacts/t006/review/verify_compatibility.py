"""Independent T006 review; run after code and experiments are final."""
import json
import runpy
import sys
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from soccer_sim.experiment_t005 import CONDITIONS as LEGACY_CONDITIONS
from soccer_sim.simulation import advance, new_world
from soccer_sim.storage import digest, identity, load_snapshot, snapshot


def main():
    preflight = ROOT / "artifacts/t006/preflight"
    counts = {}
    for label in ("t001", "t002", "t003", "t004", "t005"):
        base = ROOT / "artifacts" / label
        expected = json.loads((preflight / f"{label}-inventory.json").read_text())
        actual = []
        for path in sorted(base.rglob("*")):
            if path.is_file():
                data = path.read_bytes()
                actual.append({"path": str(path.relative_to(base)), "bytes": len(data),
                               "sha256": sha256(data).hexdigest()})
        assert actual == expected, f"{label} artifacts changed"
        counts[label] = len(actual)
    project = runpy.run_path(str(preflight / "semantic_projection.py"))["projection"]
    legacy = json.loads((preflight / "legacy_reference.json").read_text())
    for row in legacy["runs"]:
        world = advance(new_world(row["seed"], LEGACY_CONDITIONS[row["condition"]]), row["days"])
        before = digest(snapshot(world))
        assert project(world) == row["projection_sha256"], (row["condition"], row["seed"])
        assert digest(snapshot(world)) == before, "projection mutated live state/history"
    report = json.loads((ROOT / "artifacts/t006/experiments/comparison.json").read_text())
    assert report["versions"] == identity(), "experiments use different source"
    assert report["run_count"] == 240
    representative = ROOT / "artifacts/t006/representative"
    for name in ("snapshot.json", "events.jsonl"):
        assert (representative / "full" / name).read_bytes() == (representative / "resumed" / name).read_bytes()
    manifest = json.loads((representative / "full/manifest.json").read_text())
    restored = load_snapshot(representative / "full/snapshot.json")
    assert restored.config.policy.coach_method_mode == "forecast_rank"
    assert restored.config.beliefs.coach_forecast_learning is True
    state_hash = digest(snapshot(restored))
    assert state_hash == manifest["simulation_sha256"]
    verification = {
        "date_jst": "2026-11-05", "versions": identity(),
        "old_files_unchanged": counts, "legacy_full_history_comparisons": len(legacy["runs"]),
        "main_comparison_runs": report["run_count"],
        "representative_resume_byte_equal": True, "representative_hash": state_hash,
        "comparison_excludes_only": ["versions", "config.policy.coach_method_mode", "decision.policy_version"],
    }
    path = ROOT / "artifacts/t006/review/verification.json"
    path.write_text(json.dumps(verification, sort_keys=True, indent=2) + "\n")
    print(json.dumps(verification, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
