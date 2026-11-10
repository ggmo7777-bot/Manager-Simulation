"""Verify T001-T010 frozen file preservation (545 files), T010 240-run semantic equivalence, and T011 representative checkpoint byte equality.

Author: Antigravity (Gemini 4 Argon) — distinct from the creator of T001-T007 (Sol 6.1 High).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "artifacts/t011/preflight"))

from semantic_projection import projection  # noqa: E402
from soccer_sim import league_economy as le  # noqa: E402
from soccer_sim import transfer_loop as tl  # noqa: E402
from soccer_sim.storage import write_json  # noqa: E402


def main() -> None:
    preflight = ROOT / "artifacts/t011/preflight"
    old_counts: dict[str, int] = {}
    for idx in range(1, 11):
        task = f"t{idx:03d}"
        inv_path = preflight / f"{task}-inventory.json"
        expected = json.loads(inv_path.read_text(encoding="utf-8"))
        actual: dict[str, str] = {}
        tdir = ROOT / "artifacts" / task
        for p in sorted(tdir.rglob("*")):
            if p.is_file():
                rel = str(p.relative_to(ROOT))
                actual[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
        if actual != expected:
            raise AssertionError(f"Frozen inventory mismatch in {task}")
        old_counts[task] = len(actual)

    # Verify T010 240 main comparison runs match preflight semantic projection
    legacy_ref = json.loads((preflight / "legacy_reference.json").read_text(encoding="utf-8"))
    compared = 0
    for cond, by_seed in legacy_ref.items():
        cfg = tl.config_for_condition(cond)
        for seed_str, exp_digest in by_seed.items():
            w = tl.run_transfer_world(days=84, seed=int(seed_str), config=cfg)
            act_digest = projection(w)
            if act_digest != exp_digest:
                raise AssertionError(f"T010 semantic projection mismatch on {cond} seed={seed_str}")
            compared += 1

    # Verify representative run full vs resumed byte-for-byte equality
    rep_full = (ROOT / "artifacts/t011/representative/full/snapshot.json").read_bytes()
    rep_res = (ROOT / "artifacts/t011/representative/resumed/snapshot.json").read_bytes()
    rep_jsonl_full = (ROOT / "artifacts/t011/representative/full/events.jsonl").read_bytes()
    rep_jsonl_res = (ROOT / "artifacts/t011/representative/resumed/events.jsonl").read_bytes()
    rep_equal = (rep_full == rep_res) and (rep_jsonl_full == rep_jsonl_res)
    if not rep_equal:
        raise AssertionError("Representative full vs resumed snapshots are not byte-equal")
    rep_hash = json.loads(rep_full)["identity"]
    full_sum = json.loads((ROOT / "artifacts/t011/representative/full/summary.json").read_text(encoding="utf-8"))

    payload = {
        "author": le.LEAGUE_AUTHOR,
        "comparison_excludes_only": ["identity.source_sha256"],
        "date_jst": "2026-11-07",
        "legacy_t010_full_history_comparisons": compared,
        "main_comparison_runs": 240,
        "old_files_unchanged": old_counts,
        "representative_hash": full_sum["snapshot_sha256"],
        "representative_resume_byte_equal": rep_equal,
        "total_frozen_files_verified": sum(old_counts.values()),
        "versions": le.league_identity(),
    }
    write_json(ROOT / "artifacts/t011/review/verification.json", payload)
    print(
        f"Verified {payload['total_frozen_files_verified']} frozen files and {compared} T010 runs; rep_hash={payload['representative_hash']}"
    )


if __name__ == "__main__":
    main()
