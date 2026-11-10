"""Independent review: run after T005 code and representative experiments are final."""
from pathlib import Path
from hashlib import sha256
import json
import runpy
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from soccer_sim.experiment_t004 import CONDITIONS as LEGACY_CONDITIONS
from soccer_sim.experiment_t005 import CONDITIONS
from soccer_sim.simulation import advance, new_world
from soccer_sim.storage import digest, identity, load_snapshot, snapshot


def main():
    preflight = ROOT / "artifacts/t005/preflight"
    counts = {}
    for label in ("t001", "t002", "t003", "t004"):
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
    collection_names = {"clipped_adaptive": "coach_frozen_clipped",
                        "signed_adaptive": "coach_frozen_signed"}
    collection_checks = 0
    for row in legacy["runs"]:
        expected = {key: value for key, value in row.items() if key not in ("seed", "days", "condition")}
        world = advance(new_world(row["seed"], LEGACY_CONDITIONS[row["condition"]]), row["days"])
        assert project(world) == expected, ("legacy", row["condition"], row["seed"])
        if row["condition"] in collection_names:
            world = advance(new_world(row["seed"], CONDITIONS[collection_names[row["condition"]]]), row["days"])
            assert project(world) == expected, ("collection-only", row["condition"], row["seed"])
            collection_checks += 1
    report = json.loads((ROOT / "artifacts/t005/experiments/comparison.json").read_text())
    assert report["versions"] == identity(), "experiments use different code"
    assert report["run_count"] == 240
    representative = ROOT / "artifacts/t005/representative"
    for name in ("snapshot.json", "events.jsonl"):
        assert (representative / "full" / name).read_bytes() == (representative / "resumed" / name).read_bytes()
    manifest = json.loads((representative / "full/manifest.json").read_text())
    restored = load_snapshot(representative / "full/snapshot.json")
    assert restored.config == CONDITIONS["coach_learning_signed"]
    state_hash = digest(snapshot(restored))
    assert state_hash == manifest["simulation_sha256"]
    verification = {
        "date_jst": "2026-11-05", "versions": identity(),
        "old_files_unchanged": counts, "legacy_semantic_comparisons": len(legacy["runs"]),
        "observation_only_legacy_comparisons": collection_checks,
        "main_comparison_runs": report["run_count"],
        "representative_resume_byte_equal": True, "representative_hash": state_hash,
    }
    path = ROOT / "artifacts/t005/review/verification.json"
    path.write_text(json.dumps(verification, sort_keys=True, indent=2) + "\n")
    print(json.dumps(verification, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
