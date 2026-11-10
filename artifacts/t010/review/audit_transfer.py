"""T010 Independent 1-Pass Event-Log Auditor (policy_functions_called == 0).

Author: Antigravity (Gemini 4 Argon) — distinct from the creator of T001-T007 (Sol 6.1 High).

Reconstructs private beliefs and recomputes all 4 Policy decision event classes
(coach_training_decision, player_training_decision, lineup_decision, transfer_window_decision)
plus advice_feedback purely from event log records, without calling any Policy functions
or reading World truth.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from soccer_sim import transfer_loop as tl
from soccer_sim.match_2d import clamp, round6
from soccer_sim.storage import write_json

PLAYER_IDS = ("p1", "p2", "p3", "p4", "p5", "p6")
COACH_IDS = ("c1", "c2")
CLUB_IDS = ("club_a", "club_b")
OTHER_CLUB = {"club_a": "club_b", "club_b": "club_a"}
TRAINING_METHODS = ("A", "B")
MIN_CLUB_ROSTER = 2
MAX_CLUB_ROSTER = 4


def _recompute_coach_expected_gain(
    coach_state: dict[str, Any],
    pid: str,
    method: str,
) -> dict[str, Any]:
    b = coach_state["beliefs"][pid]
    headroom = max(0.0, 1.0 - b["skill_estimate"] / coach_state["subjective_cap"])
    fatigue_factor = max(0.25, 1.0 - 0.55 * b["fatigue_estimate"])
    base_gain = round6(coach_state["subjective_rates"][method] * headroom * fatigue_factor * 0.25)
    residual = round6(b["method_residuals"][method])
    expected = round6(base_gain + residual)
    return {
        "method": method,
        "base_gain": base_gain,
        "residual": residual,
        "expected_gain": expected,
    }


def audit_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    cfg: dict[str, Any] = {}
    player_minds: dict[str, dict[str, Any]] = {}
    coach_minds: dict[str, dict[str, Any]] = {}
    morning_obs: dict[tuple[int, str], dict[str, Any]] = {}
    day_proposals: dict[tuple[int, str], list[dict[str, Any]]] = {}
    day_coach_chosen: dict[tuple[int, str], list[dict[str, Any]]] = {}

    counts = {
        "coach_training_decision": 0,
        "player_training_decision": 0,
        "lineup_decision": 0,
        "transfer_window_decision": 0,
        "advice_feedback": 0,
    }
    mismatches: list[dict[str, Any]] = []

    def club_roster(club_id: str) -> tuple[str, ...]:
        return tuple(pid for pid in PLAYER_IDS if player_minds[pid]["club_id"] == club_id)

    for ev in events:
        eid = int(ev["event_id"])
        day = int(ev["day"])
        kind = str(ev["kind"])
        actor = ev["actor"]
        target = ev["target"]
        data = ev["data"]

        if kind == "world_initialized":
            cfg = dict(data["config"])
        elif kind == "player_initialized":
            pid = str(target)
            player_minds[pid] = {
                "player_id": pid,
                "club_id": str(data["club_id"]),
                "self_skill_estimate": float(data["self_skill_estimate"]),
                "self_fatigue_estimate": float(data["self_fatigue_estimate"]),
                "self_match_rating": float(data["self_match_rating"]),
                "expected_gains": {m: float(data["expected_gains"][m]) for m in TRAINING_METHODS},
                "preferences": {m: float(data["preferences"][m]) for m in TRAINING_METHODS},
                "autonomy": float(data["autonomy"]),
                "loyalty": float(data["loyalty"]),
                "trust_by_coach": {c: float(data["trust_by_coach"][c]) for c in COACH_IDS},
                "bench_streak": 0,
                "playing_time_satisfaction": float(data["playing_time_satisfaction"]),
                "perceived_club_starter_bar": {
                    cl: float(data["perceived_club_starter_bar"][cl]) for cl in CLUB_IDS
                },
            }
        elif kind == "coach_initialized":
            cid = str(target)
            beliefs: dict[str, dict[str, Any]] = {}
            for pid in PLAYER_IDS:
                ib = data["initial_beliefs"][pid]
                beliefs[pid] = {
                    "skill_estimate": float(ib["skill_estimate"]),
                    "fatigue_estimate": float(ib["fatigue_estimate"]),
                    "match_rating_estimate": float(ib["match_rating_estimate"]),
                    "perceived_bench_streak": 0,
                    "last_seen": 0,
                    "last_match_seen": 0,
                    "method_residuals": {"A": 0.0, "B": 0.0},
                }
            coach_minds[cid] = {
                "coach_id": cid,
                "club_id": str(data["club_id"]),
                "subjective_rates": {m: float(data["subjective_rates"][m]) for m in TRAINING_METHODS},
                "subjective_cap": float(data["subjective_cap"]),
                "beliefs": beliefs,
            }
        elif kind == "self_observation":
            pid = str(actor)
            morning_obs[(day, pid)] = {
                "observation_id": eid,
                "observed_skill": float(data["observed_skill"]),
                "observed_fatigue": float(data["observed_fatigue"]),
            }
        elif kind == "self_belief_update":
            pid = str(actor)
            player_minds[pid]["self_skill_estimate"] = float(data["skill_estimate_after"])
            player_minds[pid]["self_fatigue_estimate"] = float(data["fatigue_estimate_after"])
        elif kind == "coach_training_decision":
            counts["coach_training_decision"] += 1
            cid = str(actor)
            cm = coach_minds[cid]
            own_club = cm["club_id"]
            other_club = OTHER_CLUB[own_club]
            own_roster = club_roster(own_club)
            other_roster = club_roster(other_club)
            chosen_so_far = day_coach_chosen.setdefault((day, cid), [])
            already_targeted = {
                str(c["target"]) for c in chosen_so_far if c.get("target") is not None
            }

            own_vals = sorted(
                (
                    round6(
                        float(cfg["select_skill_weight"]) * cm["beliefs"][p]["skill_estimate"]
                        + float(cfg["select_match_weight"]) * cm["beliefs"][p]["match_rating_estimate"]
                    ),
                    p,
                )
                for p in own_roster
            )
            own_vals.reverse()
            starter_2nd_val = own_vals[min(1, len(own_vals) - 1)][0]

            recomputed_candidates: list[dict[str, Any]] = []
            for p in own_roster:
                if p in already_targeted:
                    continue
                b = cm["beliefs"][p]
                staleness = max(0, day - int(b["last_seen"]))
                dev_need = round6(
                    float(cfg["coach_need_weight"]) * max(0.0, cm["subjective_cap"] - b["skill_estimate"])
                )
                starter_focus = round6(0.16 * b["match_rating_estimate"])
                fatigue_drag = round6(-0.18 * b["fatigue_estimate"])
                obs_staleness = round6(float(cfg["coach_staleness_weight"]) * min(14, staleness))
                obs_comps = {
                    "development_need": round6(0.55 * dev_need),
                    "starter_focus": starter_focus,
                    "staleness_term": obs_staleness,
                    "fatigue_drag": fatigue_drag,
                }
                recomputed_candidates.append(
                    {
                        "kind": "observe",
                        "target": p,
                        "method": None,
                        "score": round6(sum(obs_comps.values())),
                        "components": obs_comps,
                    }
                )
                forecasts = {m: _recompute_coach_expected_gain(cm, p, m) for m in TRAINING_METHODS}
                for method in TRAINING_METHODS:
                    fc = forecasts[method]
                    method_term = round6(
                        float(cfg["coach_method_fit"]) * (0.65 + 8.0 * max(0.0, fc["expected_gain"]))
                    )
                    freshness_bonus = round6(0.03 * max(0.0, 1.0 - staleness / 14.0))
                    prop_comps = {
                        "development_need": dev_need,
                        "starter_focus": starter_focus,
                        "method_term": method_term,
                        "freshness_bonus": freshness_bonus,
                        "fatigue_drag": fatigue_drag,
                    }
                    recomputed_candidates.append(
                        {
                            "kind": "propose",
                            "target": p,
                            "method": method,
                            "score": round6(sum(prop_comps.values())),
                            "components": prop_comps,
                        }
                    )

            if len(own_roster) < MAX_CLUB_ROSTER:
                for p in other_roster:
                    if p in already_targeted:
                        continue
                    b = cm["beliefs"][p]
                    staleness = max(0, day - int(b["last_seen"]))
                    ext_val = round6(
                        float(cfg["select_skill_weight"]) * b["skill_estimate"]
                        + float(cfg["select_match_weight"]) * b["match_rating_estimate"]
                    )
                    upgrade_potential = round6(0.45 * max(0.0, ext_val - starter_2nd_val + 0.04))
                    scout_staleness = round6(0.016 * min(21, staleness))
                    bench_signal = round6(0.025 * min(4, b["perceived_bench_streak"]))
                    scout_comps = {
                        "upgrade_potential": upgrade_potential,
                        "staleness_term": scout_staleness,
                        "bench_signal": bench_signal,
                    }
                    recomputed_candidates.append(
                        {
                            "kind": "scout",
                            "target": p,
                            "method": None,
                            "score": round6(sum(scout_comps.values())),
                            "components": scout_comps,
                        }
                    )

            rest_val = round6(float(cfg["coach_rest_value"]))
            recomputed_candidates.append(
                {
                    "kind": "rest",
                    "target": None,
                    "method": None,
                    "score": rest_val,
                    "components": {"rest_baseline": rest_val},
                }
            )
            recomputed_chosen = max(recomputed_candidates, key=lambda c: float(c["score"]))
            logged_chosen = data["chosen"]
            if (
                recomputed_chosen["kind"] != logged_chosen["kind"]
                or recomputed_chosen["target"] != logged_chosen["target"]
                or recomputed_chosen["method"] != logged_chosen["method"]
                or recomputed_chosen["score"] != logged_chosen["score"]
                or recomputed_chosen["components"] != logged_chosen["components"]
            ):
                mismatches.append(
                    {
                        "event_id": eid,
                        "kind": kind,
                        "recomputed": recomputed_chosen,
                        "logged": logged_chosen,
                    }
                )
            chosen_so_far.append(logged_chosen)
        elif kind == "training_proposal":
            cid = str(actor)
            pid = str(target)
            day_proposals.setdefault((day, pid), []).append(
                {
                    "coach_id": cid,
                    "club_id": str(data["club_id"]),
                    "method": str(data["method"]),
                    "expected_gain": float(data["expected_gain"]),
                    "proposal_event_id": eid,
                }
            )
        elif kind == "player_training_decision":
            counts["player_training_decision"] += 1
            pid = str(actor)
            pm = player_minds[pid]
            m_obs = morning_obs[(day, pid)]
            obs_fatigue = float(m_obs["observed_fatigue"])
            props = [
                pr for pr in day_proposals.get((day, pid), []) if pr["club_id"] == pm["club_id"]
            ]

            sat_deficit = max(0.0, 0.50 - pm["playing_time_satisfaction"])
            eff_autonomy = round6(
                clamp(pm["autonomy"] + 0.18 * max(0.0, 0.45 - pm["playing_time_satisfaction"]), 0.0, 1.0)
            )
            career_drive = round6(float(cfg["satisfaction_drive_weight"]) * sat_deficit)

            rec_cands: list[dict[str, Any]] = []
            for method in TRAINING_METHODS:
                matching = [pr for pr in props if pr["method"] == method]
                advice_bonus = 0.0
                score_source = None
                if matching:
                    best_pr = max(
                        matching,
                        key=lambda pr: (pm["trust_by_coach"][pr["coach_id"]], pr["coach_id"]),
                    )
                    cid = str(best_pr["coach_id"])
                    t_val = pm["trust_by_coach"][cid]
                    advice_bonus = round6(
                        float(cfg["advice_weight"]) * max(0.0, t_val - eff_autonomy)
                    )
                    score_source = cid
                comps = {
                    "expected_gain": round6(pm["expected_gains"][method]),
                    "preference": round6(pm["preferences"][method]),
                    "advice_bonus": advice_bonus,
                    "career_drive": career_drive,
                    "fatigue_cost": round6(-float(cfg["training_fatigue_weight"]) * obs_fatigue),
                }
                rec_cands.append(
                    {
                        "action": method,
                        "score": round6(sum(comps.values())),
                        "score_source": score_source,
                        "components": comps,
                    }
                )
            rest_comps = {
                "fatigue_relief": round6(float(cfg["rest_fatigue_weight"]) * obs_fatigue),
                "base_rest": 0.012,
            }
            rec_cands.append(
                {
                    "action": "rest",
                    "score": round6(sum(rest_comps.values())),
                    "score_source": None,
                    "components": rest_comps,
                }
            )
            rec_chosen = max(rec_cands, key=lambda c: float(c["score"]))
            if rec_chosen != data["chosen"] or rec_cands != data["candidates"]:
                mismatches.append(
                    {
                        "event_id": eid,
                        "kind": kind,
                        "recomputed": rec_chosen,
                        "logged": data["chosen"],
                    }
                )
        elif kind == "advice_feedback":
            counts["advice_feedback"] += 1
            pid = str(actor)
            cid = str(target)
            pm = player_minds[pid]
            obs_gain = float(data["observed_gain"])
            self_pred = float(data["self_prediction"])
            coach_pred = float(data["coach_prediction"])
            self_sq = round6((obs_gain - self_pred) ** 2)
            coach_sq = round6((obs_gain - coach_pred) ** 2)
            delta_err = round6(self_sq - coach_sq)
            old_trust = pm["trust_by_coach"][cid]
            new_trust = round6(
                clamp(
                    old_trust + float(cfg["trust_alpha"]) * float(cfg["trust_scale"]) * delta_err,
                    0.05,
                    0.98,
                )
            )
            if (
                self_sq != float(data["self_squared_error"])
                or coach_sq != float(data["coach_squared_error"])
                or round6(old_trust) != float(data["trust_before"])
                or new_trust != float(data["trust_after"])
            ):
                mismatches.append(
                    {
                        "event_id": eid,
                        "kind": kind,
                        "recomputed_trust_after": new_trust,
                        "logged_trust_after": data["trust_after"],
                    }
                )
            pm["trust_by_coach"][cid] = new_trust
        elif kind == "expected_gain_update":
            pid = str(actor)
            m = str(data["method"])
            player_minds[pid]["expected_gains"][m] = float(data["expected_gain_after"])
        elif kind == "coach_observation_update":
            cid = str(actor)
            pid = str(target)
            b = coach_minds[cid]["beliefs"][pid]
            b["skill_estimate"] = float(data["skill_estimate_after"])
            b["fatigue_estimate"] = float(data["observed_fatigue"])
            b["last_seen"] = day
        elif kind == "coach_residual_update":
            cid = str(actor)
            pid = str(target)
            m = str(data["method"])
            coach_minds[cid]["beliefs"][pid]["method_residuals"][m] = float(data["residual_after"])
        elif kind == "coach_scout_update":
            cid = str(actor)
            pid = str(target)
            b = coach_minds[cid]["beliefs"][pid]
            b["skill_estimate"] = float(data["skill_estimate_after"])
            b["match_rating_estimate"] = float(data["match_rating_after"])
            b["last_seen"] = day
        elif kind == "lineup_decision":
            counts["lineup_decision"] += 1
            cid = str(actor)
            club_id = str(target)
            cm = coach_minds[cid]
            roster = club_roster(club_id)
            ranked: list[dict[str, Any]] = []
            for p in roster:
                b = cm["beliefs"][p]
                comps = {
                    "skill_term": round6(float(cfg["select_skill_weight"]) * b["skill_estimate"]),
                    "match_rating_term": round6(
                        float(cfg["select_match_weight"]) * b["match_rating_estimate"]
                    ),
                    "fatigue_penalty": round6(
                        -float(cfg["select_fatigue_weight"]) * b["fatigue_estimate"]
                    ),
                }
                ranked.append(
                    {
                        "player_id": p,
                        "score": round6(sum(comps.values())),
                        "components": comps,
                    }
                )
            ranked.sort(key=lambda r: (-float(r["score"]), str(r["player_id"])))
            starters = [r["player_id"] for r in ranked[:2]]
            bench = [r["player_id"] for r in ranked[2:]]
            if starters != data["starters"] or bench != data["bench"]:
                mismatches.append(
                    {
                        "event_id": eid,
                        "kind": kind,
                        "recomputed_starters": starters,
                        "logged_starters": data["starters"],
                    }
                )
        elif kind == "player_career_belief":
            pid = str(actor)
            pm = player_minds[pid]
            pm["bench_streak"] = int(data["bench_streak"])
            pm["playing_time_satisfaction"] = float(data["satisfaction_after"])
            pm["self_match_rating"] = float(data["self_match_rating_after"])
            pm["perceived_club_starter_bar"] = {
                cl: float(data["perceived_club_starter_bar_after"][cl]) for cl in CLUB_IDS
            }
        elif kind == "coach_match_belief":
            cid = str(actor)
            pid = str(target)
            b = coach_minds[cid]["beliefs"][pid]
            if bool(data["started"]):
                b["match_rating_estimate"] = float(data["match_rating_after"])
                b["skill_estimate"] = float(data["skill_estimate_after"])
                b["fatigue_estimate"] = float(data["fatigue_estimate_after"])
                b["perceived_bench_streak"] = int(data["perceived_bench_streak"])
                b["last_match_seen"] = day
                b["last_seen"] = day
            else:
                b["perceived_bench_streak"] = int(data["perceived_bench_streak"])
                b["match_rating_estimate"] = float(data["match_rating_after"])
                b["fatigue_estimate"] = float(data["fatigue_estimate_after"])
        elif kind == "transfer_window_decision":
            counts["transfer_window_decision"] += 1
            pid = str(actor)
            pm = player_minds[pid]
            from_club = pm["club_id"]
            to_club = OTHER_CLUB[from_club]
            from_cid = "c1" if from_club == "club_a" else "c2"
            to_cid = "c2" if from_club == "club_a" else "c1"
            from_cm = coach_minds[from_cid]
            to_cm = coach_minds[to_cid]
            from_roster = club_roster(from_club)
            to_roster = club_roster(to_club)

            req_comps = {
                "dissatisfaction_term": round6(
                    0.55 * max(0.0, 0.48 - pm["playing_time_satisfaction"])
                ),
                "bench_streak_term": round6(0.075 * min(4, pm["bench_streak"])),
                "ambition_term": round6(0.22 * max(0.0, pm["self_skill_estimate"] - 0.42)),
                "loyalty_drag": round6(-pm["loyalty"]),
            }
            req_score = round6(sum(req_comps.values()))
            wants_tr = bool(req_score >= float(cfg["transfer_request_threshold"]))

            from_vals = sorted(
                (
                    (
                        round6(
                            float(cfg["select_skill_weight"]) * from_cm["beliefs"][rp]["skill_estimate"]
                            + float(cfg["select_match_weight"])
                            * from_cm["beliefs"][rp]["match_rating_estimate"]
                        ),
                        rp,
                    )
                    for rp in from_roster
                ),
                key=lambda x: (-x[0], x[1]),
            )
            rank_ids = [x[1] for x in from_vals]
            p_rank = rank_ids.index(pid) + 1
            from_2nd = from_vals[min(1, len(from_vals) - 1)][0]
            from_p_val = dict((k, v) for v, k in from_vals)[pid]
            b_from = from_cm["beliefs"][pid]
            rel_comps = {
                "starter_2nd_gap": round6(from_2nd - from_p_val),
                "bench_relief": round6(0.015 * min(4, b_from["perceived_bench_streak"])),
            }
            rel_margin = round6(sum(rel_comps.values()))
            rel_ok = bool(
                len(from_roster) > MIN_CLUB_ROSTER and p_rank >= 3 and rel_margin >= 0.0
            )

            to_vals = sorted(
                (
                    (
                        round6(
                            float(cfg["select_skill_weight"]) * to_cm["beliefs"][rp]["skill_estimate"]
                            + float(cfg["select_match_weight"])
                            * to_cm["beliefs"][rp]["match_rating_estimate"]
                        ),
                        rp,
                    )
                    for rp in to_roster
                ),
                key=lambda x: (-x[0], x[1]),
            )
            to_2nd = to_vals[min(1, len(to_vals) - 1)][0]
            b_to = to_cm["beliefs"][pid]
            to_p_val = round6(
                float(cfg["select_skill_weight"]) * b_to["skill_estimate"]
                + float(cfg["select_match_weight"]) * b_to["match_rating_estimate"]
            )
            off_comps = {
                "target_player_valuation": to_p_val,
                "minus_current_starter_2nd": round6(-to_2nd),
            }
            off_margin = round6(sum(off_comps.values()))
            off_ok = bool(
                len(to_roster) < MAX_CLUB_ROSTER
                and off_margin >= float(cfg["transfer_offer_threshold"])
            )

            opp_bar = pm["perceived_club_starter_bar"][to_club]
            trust_to = pm["trust_by_coach"][to_cid]
            acc_comps = {
                "opportunity_edge": round6(0.50 * (pm["self_skill_estimate"] - opp_bar)),
                "coach_trust_term": round6(0.30 * (trust_to - 0.50)),
                "push_dissatisfaction": round6(
                    0.25 * max(0.0, 0.48 - pm["playing_time_satisfaction"])
                ),
                "loyalty_drag": round6(-pm["loyalty"]),
            }
            acc_score = round6(sum(acc_comps.values()))
            acc_ok = bool(acc_score >= float(cfg["transfer_accept_threshold"]))

            approved = bool(
                cfg["transfer_mechanism"] == "enabled"
                and wants_tr
                and rel_ok
                and off_ok
                and acc_ok
            )
            if (
                req_score != float(data["request_score"])
                or rel_margin != float(data["release_margin"])
                or off_margin != float(data["offer_margin"])
                or acc_score != float(data["accept_score"])
                or approved != bool(data["transfer_approved"])
            ):
                mismatches.append(
                    {
                        "event_id": eid,
                        "kind": kind,
                        "recomputed": {
                            "request_score": req_score,
                            "release_margin": rel_margin,
                            "offer_margin": off_margin,
                            "accept_score": acc_score,
                            "transfer_approved": approved,
                        },
                        "logged": {
                            "request_score": data["request_score"],
                            "release_margin": data["release_margin"],
                            "offer_margin": data["offer_margin"],
                            "accept_score": data["accept_score"],
                            "transfer_approved": data["transfer_approved"],
                        },
                    }
                )
        elif kind == "transfer_completed":
            pid = str(actor)
            to_club = str(data["to_club"])
            pm = player_minds[pid]
            pm["club_id"] = to_club
            pm["bench_streak"] = 0
            pm["playing_time_satisfaction"] = float(data["satisfaction_after"])
            for cid in COACH_IDS:
                coach_minds[cid]["beliefs"][pid]["perceived_bench_streak"] = 0

    return {
        "counts": counts,
        "total_decisions_and_feedback_audited": sum(counts.values()),
        "mismatches": mismatches,
        "mismatch_count": len(mismatches),
    }


def main() -> int:
    rep_events_path = ROOT / "artifacts" / "t010" / "representative" / "full" / "events.jsonl"
    if not rep_events_path.exists():
        world = tl.run_transfer_world(84, seed=17, condition="coupled_transfer")
        rep_events = world.events
    else:
        rep_events = [
            json.loads(line) for line in rep_events_path.read_text(encoding="utf-8").splitlines() if line.strip()
        ]

    # Pre-generate condition event streams for seed=100
    condition_events: dict[str, list[dict[str, Any]]] = {}
    for cond in tl.T010_MAIN_CONDITIONS:
        w = tl.run_transfer_world(84, seed=100, condition=cond)
        condition_events[cond] = w.events

    policy_calls = 0

    def _forbidden(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal policy_calls
        policy_calls += 1
        raise RuntimeError("Policy function must not be called during event-log audit")

    with (
        patch.object(tl, "decide_coach_training", side_effect=_forbidden),
        patch.object(tl, "decide_player_training", side_effect=_forbidden),
        patch.object(tl, "decide_club_lineup", side_effect=_forbidden),
        patch.object(tl, "decide_transfer_negotiation", side_effect=_forbidden),
    ):
        rep_audit = audit_events(rep_events)
        cond_audits = {cond: audit_events(evs) for cond, evs in condition_events.items()}

    total_mismatches = rep_audit["mismatch_count"] + sum(
        ca["mismatch_count"] for ca in cond_audits.values()
    )
    report = {
        "task": "T010",
        "author": tl.TRANSFER_AUTHOR,
        "policy_functions_called": policy_calls,
        "representative_seed_17_coupled_transfer": rep_audit,
        "conditions_seed_100": cond_audits,
        "total_mismatches": total_mismatches,
        "all_passed": policy_calls == 0 and total_mismatches == 0,
    }
    out_path = ROOT / "artifacts" / "t010" / "review" / "transfer-audit.json"
    write_json(out_path, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
