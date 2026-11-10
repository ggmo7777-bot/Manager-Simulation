"""Root review of T001-T008 frozen artifacts, T008 240-run semantic projections, and T009 runs.

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
from soccer_sim import selection_loop as sl
from soccer_sim.storage import digest


def main() -> None:
    preflight = ROOT / "artifacts/t009/preflight"
    counts: dict[str, int] = {}
    for label in ("t001", "t002", "t003", "t004", "t005", "t006", "t007", "t008"):
        base = ROOT / "artifacts" / label
        expected = json.loads((preflight / f"{label}-inventory.json").read_text(encoding="utf-8"))
        actual = []
        for path in sorted(base.rglob("*")):
            if path.is_file() and path.name != ".DS_Store":
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
        world = m2d.run_match(row["ticks"], seed=row["seed"], condition=row["condition"])
        before = digest(m2d.snapshot_payload(world))
        assert project(world) == row["projection_sha256"], (row["condition"], row["seed"])
        assert digest(m2d.snapshot_payload(world)) == before, "projection mutated live world"

    report = json.loads((ROOT / "artifacts/t009/experiments/comparison.json").read_text(encoding="utf-8"))
    assert report["versions"] == sl.selection_identity()
    assert report["author"] == sl.SELECTION_AUTHOR
    assert report["run_count"] == 240

    representative = ROOT / "artifacts/t009/representative"
    for name in ("snapshot.json", "events.jsonl"):
        assert (representative / "full" / name).read_bytes() == (
            representative / "resumed" / name
        ).read_bytes()

    restored = sl.load_snapshot(representative / "full/snapshot.json")
    state_hash = digest(sl.snapshot_payload(restored))
    manifest = json.loads((representative / "full/manifest.json").read_text(encoding="utf-8"))
    assert state_hash == manifest["simulation_sha256"]

    verification = {
        "date_jst": "2026-11-07",
        "author": sl.SELECTION_AUTHOR,
        "versions": sl.selection_identity(),
        "old_files_unchanged": counts,
        "total_frozen_files_verified": sum(counts.values()),
        "legacy_t008_full_history_comparisons": len(legacy["runs"]),
        "comparison_excludes_only": ["identity.source_sha256"],
        "main_comparison_runs": report["run_count"],
        "representative_resume_byte_equal": True,
        "representative_hash": state_hash,
    }
    out_path = ROOT / "artifacts/t009/review/verification.json"
    out_path.write_text(json.dumps(verification, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(verification, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
