"""Independent match audit using only serialized observations, minds, and explicit arithmetic.

Does not call decide_match_player or step_match.
Author: Antigravity (Gemini 4 Argon) — distinct from the creator of T001-T007 (Sol 6.1 High).
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
PITCH_HALF_LENGTH = 10.0
PITCH_HALF_WIDTH = 6.0
GOAL_HALF_WIDTH = 1.8
TACKLE_RADIUS = 1.35
BASE_MOVE_SPEED = 0.58
PACE_MOVE_SCALE = 0.36
ACTIONS = ("move", "pass", "shoot", "tackle", "hold")


def clamp(val: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, val))


def round6(val: float) -> float:
    return round(float(val), 6)


def unit_dir(dx: float, dy: float) -> list[float]:
    n = math.hypot(dx, dy)
    if n <= 1e-9:
        return [0.0, 0.0]
    return [round6(dx / n), round6(dy / n)]


def seg_clear(ax: float, ay: float, bx: float, by: float, vis_opps: list[dict[str, Any]]) -> float:
    dx = bx - ax
    dy = by - ay
    l2 = dx * dx + dy * dy
    if l2 <= 1e-9:
        return 9.0
    best = 9.0
    for opp in vis_opps:
        ox, oy = float(opp["position"][0]), float(opp["position"][1])
        t = ((ox - ax) * dx + (oy - ay) * dy) / l2
        if 0.05 <= t <= 0.98:
            px = ax + t * dx
            py = ay + t * dy
            d = math.hypot(ox - px, oy - py)
            if d < best:
                best = d
    return round6(best)


def recompute_from_serialized(obs: dict[str, Any], mind: dict[str, Any]) -> tuple[dict[str, float], dict[str, Any]]:
    px, py = float(obs["self_position"][0]), float(obs["self_position"][1])
    bx, by = float(obs["ball_position"][0]), float(obs["ball_position"][1])
    bvx, bvy = float(obs["ball_velocity"][0]), float(obs["ball_velocity"][1])
    dist_ball = math.hypot(bx - px, by - py)
    dist_goal = math.hypot(PITCH_HALF_LENGTH - px, 0.0 - py)

    # Verify non-leakage in serialized observation
    for key in obs:
        assert key not in ("traits", "opponent_traits", "opponent_minds"), f"leaked key {key}"
    for group in ("teammates", "opponents"):
        for ent in obs[group]:
            assert "traits" not in ent and "mind" not in ent and "style" not in ent
            if not ent["visible"]:
                assert ent["position"] is None, "unseen entity must have position=None"

    vis_opps = [o for o in obs["opponents"] if o["visible"] and o["position"] is not None]
    if vis_opps:
        nearest_opp_dist = min(
            math.hypot(float(o["position"][0]) - px, float(o["position"][1]) - py)
            for o in vis_opps
        )
    else:
        nearest_opp_dist = float(obs["vision_radius"])

    vis_tms = [t for t in obs["teammates"] if t["visible"] and t["position"] is not None]
    if vis_tms:
        tm_id = vis_tms[0]["player_id"]
        tm_pos = (float(vis_tms[0]["position"][0]), float(vis_tms[0]["position"][1]))
        tm_dist_ball = math.hypot(tm_pos[0] - bx, tm_pos[1] - by)
    else:
        tm_id = obs["teammates"][0]["player_id"] if obs["teammates"] else None
        tm_pos = (
            round6(clamp(px - 1.2, -PITCH_HALF_LENGTH + 1.0, PITCH_HALF_LENGTH - 1.0)),
            round6(1.5 if py <= 0.0 else -1.5),
        )
        tm_dist_ball = 99.0

    style = mind["style"]
    directness = float(style["directness"])
    pass_bias = float(style["pass_bias"])
    press_bias = float(style["press_bias"])
    directive = str(mind["coach_directive"])

    if bool(obs["owns_ball"]):
        pass_clearance = seg_clear(px, py, tm_pos[0], tm_pos[1], vis_opps)
        shot_clearance = seg_clear(px, py, PITCH_HALF_LENGTH, 0.0, vis_opps)
        range_factor = clamp(1.0 - dist_goal / 8.6, 0.0, 1.0)
        in_range_bonus = 0.44 if dist_goal <= 6.8 else -0.26
        shot_block_pen = 0.24 if shot_clearance < 0.80 else 0.0
        shoot_score = round6(
            0.22 + 0.92 * range_factor + in_range_bonus + 0.30 * directness - 0.14 * pass_bias - shot_block_pen
        )

        advance_gain = clamp((tm_pos[0] - px) / 4.5, -0.35, 0.90)
        lane_bonus = 0.22 if pass_clearance >= 0.72 else -0.32
        backward_pen = 0.28 if (tm_pos[0] < px - 1.6 and pass_bias < 0.75) else 0.0
        pressure_pass_bonus = 0.24 if (nearest_opp_dist < 2.8 and pass_clearance >= 0.70) else 0.08
        close_goal_pass_pen = 0.30 if dist_goal <= 5.6 else 0.0
        pass_score = round6(
            0.22
            + 0.66 * pass_bias
            + 0.28 * advance_gain
            + lane_bonus
            + pressure_pass_bonus
            - backward_pen
            - 0.18 * directness
            - close_goal_pass_pen
        )

        dribble_space_bonus = 0.16 if (dist_goal > 6.2 and nearest_opp_dist >= 1.5) else 0.02
        move_score = round6(0.44 + 0.36 * directness - 0.16 * pass_bias + dribble_space_bonus)
        hold_score = round6(0.14 - 0.08 * directness)
        scores = {
            "move": move_score,
            "pass": pass_score,
            "shoot": shoot_score,
            "tackle": round6(-1.0),
            "hold": hold_score,
        }
        best = max(ACTIONS, key=lambda k: scores[k])
        if best == "shoot":
            if vis_opps and shot_clearance < 1.1:
                blocker_y = float(vis_opps[0]["position"][1])
                aim_y = -0.95 if blocker_y >= 0.0 else 0.95
            else:
                aim_y = clamp(-0.25 * py, -0.80, 0.80)
            intent = {
                "kind": "shoot",
                "direction_rel": unit_dir(PITCH_HALF_LENGTH - px, aim_y - py),
                "target_player_id": None,
                "target_rel": [PITCH_HALF_LENGTH, round6(aim_y)],
            }
        elif best == "pass":
            lt = [
                round6(clamp(tm_pos[0] + 0.72, -PITCH_HALF_LENGTH + 0.8, PITCH_HALF_LENGTH - 0.8)),
                round6(clamp(tm_pos[1], -PITCH_HALF_WIDTH + 0.6, PITCH_HALF_WIDTH - 0.6)),
            ]
            intent = {
                "kind": "pass",
                "direction_rel": unit_dir(lt[0] - px, lt[1] - py),
                "target_player_id": tm_id,
                "target_rel": lt,
            }
        elif best == "move":
            tx = min(PITCH_HALF_LENGTH - 1.0, px + 2.8)
            ty = clamp(0.35 * py, -1.4, 1.4)
            intent = {
                "kind": "move",
                "direction_rel": unit_dir(tx - px, ty - py),
                "target_player_id": None,
                "target_rel": [round6(tx), round6(ty)],
            }
        else:
            intent = {"kind": "hold", "direction_rel": [0.0, 0.0], "target_player_id": None, "target_rel": None}
    else:
        press_bonus = 0.28 if directive == "press" else 0.0
        if obs["ball_owner_relation"] == "opponent" and dist_ball <= TACKLE_RADIUS:
            tackle_score = round6(
                0.78 + 0.34 * press_bias + press_bonus + 0.16 * (1.0 - dist_ball / TACKLE_RADIUS)
            )
        else:
            tackle_score = round6(-1.0)

        pred_bx = clamp(bx + bvx, -PITCH_HALF_LENGTH + 0.4, PITCH_HALF_LENGTH - 0.4)
        pred_by = clamp(by + bvy, -PITCH_HALF_WIDTH + 0.4, PITCH_HALF_WIDTH - 0.4)
        dist_pred = math.hypot(pred_bx - px, pred_by - py)
        tm_dist_pred = math.hypot(pred_bx - tm_pos[0], pred_by - tm_pos[1]) if vis_tms else 99.0

        if obs["ball_owner_relation"] == "teammate":
            sx = round6(clamp(max(bx + 2.4, px + 1.8), -2.5, PITCH_HALF_LENGTH - 1.5))
            sy = round6(-1.4 if by >= 0.0 else 1.4)
            mt = [sx, sy]
        elif obs["ball_owner_relation"] == "none" and dist_pred <= tm_dist_pred + 0.35:
            mt = [round6(pred_bx), round6(pred_by)]
        elif (
            dist_ball <= tm_dist_ball + 0.25
            or (directive == "press" and dist_ball <= 3.2)
            or (not vis_tms and int(obs["slot"]) == 1)
        ):
            mt = [round6(bx), round6(by)]
        else:
            c_off = 2.2 if directive == "press" else 3.4
            c_max = 0.8 if directive == "press" else -1.5
            cx = round6(clamp(bx - c_off, -PITCH_HALF_LENGTH + 0.9, c_max))
            cy = round6(clamp(0.30 * by, -0.9, 0.9))
            mt = [cx, cy]

        dist_mt = math.hypot(mt[0] - px, mt[1] - py)
        move_score = round6(
            0.64 + 0.22 * press_bias + press_bonus + (0.10 if dist_mt > 0.35 else -0.25)
        )
        hold_score = round6(0.20 - 0.10 * press_bias - 0.5 * press_bonus)
        scores = {
            "move": move_score,
            "pass": round6(-1.0),
            "shoot": round6(-1.0),
            "tackle": tackle_score,
            "hold": hold_score,
        }
        best = max(ACTIONS, key=lambda k: scores[k])
        if best == "tackle":
            intent = {
                "kind": "tackle",
                "direction_rel": unit_dir(bx - px, by - py),
                "target_player_id": obs["ball_owner_id"],
                "target_rel": [round6(bx), round6(by)],
            }
        elif best == "move":
            intent = {
                "kind": "move",
                "direction_rel": unit_dir(mt[0] - px, mt[1] - py),
                "target_player_id": None,
                "target_rel": mt,
            }
        else:
            intent = {"kind": "hold", "direction_rel": [0.0, 0.0], "target_player_id": None, "target_rel": None}

    return scores, intent


def audit_snapshot(path: Path) -> dict[str, int]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    state = payload["state"]
    players = state["players"]
    events = state["events"]

    decisions_checked = 0
    unseen_entities_checked = 0
    summaries_checked = 0
    goals_checked = 0

    for ev in events:
        kind = ev["kind"]
        if kind == "player_decision":
            pid = ev["player_id"]
            mind = players[pid]["mind"]
            obs = ev["observation"]
            scores, intent = recompute_from_serialized(obs, mind)
            assert ev["scores"] == scores, f"score mismatch at tick {ev['tick']} for {pid}"
            assert ev["intent"] == intent, f"intent mismatch at tick {ev['tick']} for {pid}"
            for group in ("teammates", "opponents"):
                for ent in obs[group]:
                    if not ent["visible"]:
                        assert ent["position"] is None
                        unseen_entities_checked += 1
            decisions_checked += 1
        elif kind == "tick_summary":
            for pid, pframe in ev["players"].items():
                pace = float(players[pid]["traits"]["pace"])
                max_step = round6(BASE_MOVE_SPEED + PACE_MOVE_SCALE * pace)
                assert float(pframe["move_distance"]) <= max_step + 1e-5
                assert -PITCH_HALF_LENGTH <= float(pframe["x"]) <= PITCH_HALF_LENGTH
                assert -PITCH_HALF_WIDTH <= float(pframe["y"]) <= PITCH_HALF_WIDTH
            bx, by = float(ev["ball"]["x"]), float(ev["ball"]["y"])
            assert -PITCH_HALF_LENGTH <= bx <= PITCH_HALF_LENGTH
            assert -PITCH_HALF_WIDTH <= by <= PITCH_HALF_WIDTH
            summaries_checked += 1
        elif kind == "goal":
            assert ev["scorer_id"] in players
            assert ev["cause_kind"] in ("shoot", "pass", "carry", "pickup", "tackle_won", "interception")
            goals_checked += 1

    return {
        "decisions_checked": decisions_checked,
        "unseen_entities_checked": unseen_entities_checked,
        "summaries_checked": summaries_checked,
        "goals_checked": goals_checked,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=ROOT / "artifacts/t008/representative/full/snapshot.json",
    )
    parser.add_argument("--out", type=Path, default=ROOT / "artifacts/t008/review")
    args = parser.parse_args()

    rep_stats = audit_snapshot(args.snapshot)
    no_noise_dir = ROOT / "artifacts/t008/experiments/no-noise"
    cond_stats: dict[str, Any] = {}
    for cond in ("baseline", "home_skill_plus", "home_position_plus", "home_pass_style"):
        cond_stats[cond] = audit_snapshot(no_noise_dir / cond / "snapshot.json")

    total_decisions = rep_stats["decisions_checked"] + sum(
        c["decisions_checked"] for c in cond_stats.values()
    )
    report = {
        "author": "Antigravity (Gemini 4 Argon)",
        "representative_snapshot": str(args.snapshot.relative_to(ROOT)),
        "representative_audit": rep_stats,
        "condition_audits": cond_stats,
        "total_decisions_independently_recomputed": total_decisions,
        "policy_functions_called": 0,
        "all_invariants_passed": True,
    }
    args.out.mkdir(parents=True, exist_ok=True)
    out_file = args.out / "match-audit.json"
    out_file.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
