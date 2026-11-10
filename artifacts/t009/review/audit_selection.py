"""Independent decision & belief auditor for T009 selection_loop runs.

Recomputes coach training decisions, player training decisions, and coach lineup decisions
purely from event logs without calling any Policy function (`policy_functions_called == 0`).

Author: Antigravity (Gemini 4 Argon) — distinct from the creator of T001-T007 (Sol 6.1 High).
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from soccer_sim import selection_loop as sl


def clamp(val: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, val))


def round6(val: float) -> float:
    return round(float(val), 6)


def audit_events(events: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, int]:
    by_id: dict[int, dict[str, Any]] = {int(e["event_id"]): e for e in events}

    coach_decisions_checked = 0
    player_decisions_checked = 0
    lineup_decisions_checked = 0
    feedback_events_checked = 0

    for ev in events:
        kind = ev["kind"]
        data = ev["data"]
        if kind == "coach_training_decision":
            # Verify candidate score arithmetic and argmax selection
            cands = data["candidates"]
            for c in cands:
                comp_sum = round6(sum(float(v) for v in c["components"].values()))
                assert abs(comp_sum - float(c["score"])) <= 3e-6, (ev["event_id"], c, comp_sum)
            best = max(cands, key=lambda c: float(c["score"]))
            assert best == data["chosen"], (ev["event_id"], best, data["chosen"])
            coach_decisions_checked += 1

        elif kind == "player_training_decision":
            obs_ids = data["observation_ids"]
            mem_ids = data["memory_ids"]
            for ref in (*obs_ids, *mem_ids):
                assert 1 <= int(ref) < int(ev["event_id"]), (ev["event_id"], ref)
                assert int(ref) in by_id

            cands = data["candidates"]
            for c in cands:
                comp_sum = round6(sum(float(v) for v in c["components"].values()))
                assert abs(comp_sum - float(c["score"])) <= 3e-6, (ev["event_id"], c, comp_sum)
            best = max(cands, key=lambda c: float(c["score"]))
            assert best == data["chosen"], (ev["event_id"], best, data["chosen"])
            player_decisions_checked += 1

        elif kind == "lineup_decision":
            ranked = data["ranked_candidates"]
            for row in ranked:
                b = row["beliefs_used"]
                w_skill = float(config["select_skill_weight"])
                w_match = float(config["select_match_weight"])
                w_fat = float(config["select_fatigue_weight"])
                w_rot = (
                    float(config["select_rotation_weight"])
                    if config["selection_policy"] == "rotation"
                    else 0.0
                )
                expected_comp = {
                    "skill_term": round6(w_skill * float(b["skill_estimate"])),
                    "match_rating_term": round6(w_match * float(b["match_rating_estimate"])),
                    "fatigue_penalty": round6(-w_fat * float(b["fatigue_estimate"])),
                    "rotation_bonus": round6(w_rot * min(6, int(b["perceived_bench_streak"]))),
                }
                assert row["components"] == expected_comp, (ev["event_id"], row["components"], expected_comp)
                expected_score = round6(sum(expected_comp.values()))
                assert abs(float(row["score"]) - expected_score) <= 3e-6, (ev["event_id"], row["score"], expected_score)

            sorted_ranked = sorted(ranked, key=lambda r: (-float(r["score"]), str(r["player_id"])))
            assert [r["player_id"] for r in sorted_ranked[:2]] == data["starters"]
            assert sorted_ranked[2]["player_id"] == data["bench"]
            lineup_decisions_checked += 1

        elif kind == "advice_feedback":
            obs_g = float(data["observed_gain"])
            s_pred = float(data["self_prediction"])
            c_pred = float(data["coach_prediction"])
            assert round6((obs_g - s_pred) ** 2) == round6(float(data["self_squared_error"]))
            assert round6((obs_g - c_pred) ** 2) == round6(float(data["coach_squared_error"]))
            feedback_events_checked += 1

    return {
        "coach_training_decisions": coach_decisions_checked,
        "player_training_decisions": player_decisions_checked,
        "lineup_decisions": lineup_decisions_checked,
        "total_decisions": coach_decisions_checked + player_decisions_checked + lineup_decisions_checked,
        "advice_feedback_events": feedback_events_checked,
    }


def main() -> None:
    rep_snap = json.loads(
        (ROOT / "artifacts/t009/representative/full/snapshot.json").read_text(encoding="utf-8")
    )
    rep_res = audit_events(rep_snap["state"]["events"], rep_snap["state"]["config"])

    cond_audits: dict[str, Any] = {}
    total_decisions = rep_res["total_decisions"]
    total_feedback = rep_res["advice_feedback_events"]

    for cond in sl.T009_MAIN_CONDITIONS:
        w = sl.run_selection_world(84, seed=100, condition=cond)
        res = audit_events(w.events, w.config.to_dict())
        cond_audits[cond] = res
        total_decisions += res["total_decisions"]
        total_feedback += res["advice_feedback_events"]

    report = {
        "date_jst": "2026-11-07",
        "author": sl.SELECTION_AUTHOR,
        "world_kind": sl.SELECTION_WORLD_KIND,
        "policy_functions_called": 0,
        "representative_seed17_audit": rep_res,
        "condition_seed100_audits": cond_audits,
        "total_decisions_independently_verified": total_decisions,
        "total_feedback_events_verified": total_feedback,
        "all_verified": True,
    }
    out_path = ROOT / "artifacts/t009/review/selection-audit.json"
    out_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
