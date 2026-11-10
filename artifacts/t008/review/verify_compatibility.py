"""Root review of T001-T007 frozen artifacts, T007 240-run semantic projections, and T008 runs.

Author: Antigravity (Gemini 4 Argon) — distinct from the creator of T001-T007 (Sol 6.1 High).
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from soccer_sim import match_2d as m2d
from soccer_sim import multi_coach, multi_storage
from soccer_sim.experiment_t007 import CONDITIONS as T007_CONDITIONS
from soccer_sim.storage import digest


def main() -> None:
    preflight = ROOT / "artifacts/t008/preflight"
    counts: dict[str, int] = {}
    for label in ("t001", "t002", "t003", "t004", "t005", "t006", "t007"):
        base = ROOT / "artifacts" / label
        expected = json.loads((preflight / f"{label}-inventory.json").read_text(encoding="utf-8"))
        actual = []
        for path in sorted(base.rglob("*")):
            if path.is_file():
                data = path.read_bytes()
                actual.append(
                    {
                        "path": str(path.relative_to(base)),
                        "bytes": len(data),
                        "sha256": sha256(data).hexdigest(),
                    }
                )
        assert actual == expected, f"{label} artifacts changed"
        counts[label] = len(actual)

    project = runpy.run_path(str(preflight / "semantic_projection.py"))["projection"]
    legacy = json.loads((preflight / "legacy_reference.json").read_text(encoding="utf-8"))
    for row in legacy["runs"]:
        world = multi_coach.advance(
            multi_coach.new_world(row["seed"], T007_CONDITIONS[row["condition"]]),
            row["days"],
        )
        before = digest(multi_storage.snapshot(world))
        assert project(world) == row["projection_sha256"], (row["condition"], row["seed"])
        assert digest(multi_storage.snapshot(world)) == before, "projection mutated live world"

    report = json.loads((ROOT / "artifacts/t008/experiments/comparison.json").read_text(encoding="utf-8"))
    assert report["versions"] == m2d.match_identity()
    assert report["author"] == m2d.MATCH_AUTHOR
    assert report["run_count"] == 240

    representative = ROOT / "artifacts/t008/representative"
    for name in ("snapshot.json", "events.jsonl"):
        assert (representative / "full" / name).read_bytes() == (
            representative / "resumed" / name
        ).read_bytes()

    restored = m2d.load_snapshot(representative / "full/snapshot.json")
    state_hash = digest(m2d.snapshot_payload(restored))
    manifest = json.loads((representative / "full/manifest.json").read_text(encoding="utf-8"))
    assert state_hash == manifest["simulation_sha256"]

    verification = {
        "date_jst": "2026-11-07",
        "author": m2d.MATCH_AUTHOR,
        "versions": m2d.match_identity(),
        "old_files_unchanged": counts,
        "total_frozen_files_verified": sum(counts.values()),
        "legacy_t007_full_history_comparisons": len(legacy["runs"]),
        "comparison_excludes_only": ["versions.source_sha256"],
        "main_comparison_runs": report["run_count"],
        "representative_resume_byte_equal": True,
        "representative_hash": state_hash,
    }
    out_path = ROOT / "artifacts/t008/review/verification.json"
    out_path.write_text(json.dumps(verification, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(verification, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
