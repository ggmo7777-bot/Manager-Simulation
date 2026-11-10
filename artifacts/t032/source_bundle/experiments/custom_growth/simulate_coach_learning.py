"""Simulation of Coach Evaluation, Learning, and Menu Decision (Step 3 & Step 4).

Compares:
1. Fixed Method A (Possession/Pass focus)
2. Fixed Method B (Direct/Pace focus)
3. Adaptive Coach AI (dynamically selects menu based on learned yields and team deficits)
"""

import json
from experiments.custom_growth.growth_engine import CustomPlayer, TraitState
from experiments.custom_growth.coach_policy import TeamCoach


def create_standard_squad():
    return {
        "P1_Playmaker": CustomPlayer(
            player_id="P1_Playmaker",
            cap=0.90,
            traits={
                "pace": TraitState(0.30, "early"),
                "passing": TraitState(0.30, "late"),  # Late bloomer in passing
                "shooting": TraitState(0.30, "steady"),
            },
        ),
        "P2_Winger": CustomPlayer(
            player_id="P2_Winger",
            cap=0.90,
            traits={
                "pace": TraitState(0.30, "burst"),  # Burst in pace
                "passing": TraitState(0.30, "early"),
                "shooting": TraitState(0.30, "late"),
            },
        ),
        "P3_Midfielder": CustomPlayer(
            player_id="P3_Midfielder",
            cap=0.90,
            traits={
                "pace": TraitState(0.30, "steady"),
                "passing": TraitState(0.30, "burst"),  # Burst in passing
                "shooting": TraitState(0.30, "early"),
            },
        ),
        "P4_Striker": CustomPlayer(
            player_id="P4_Striker",
            cap=0.90,
            traits={
                "pace": TraitState(0.30, "late"),
                "passing": TraitState(0.30, "steady"),
                "shooting": TraitState(0.30, "burst"),  # Burst in shooting
            },
        ),
    }


def run_regime(regime_name: str, days: int = 300):
    """Run 300 days of team training under a specific coaching regime."""
    players = create_standard_squad()
    roster_ids = list(players.keys())
    coach = TeamCoach(coach_id="HeadCoach", roster=roster_ids, preferred_style="balanced")

    history = []
    menu_counts = {"A": 0, "B": 0, "team_rest": 0}

    for day in range(1, days + 1):
        # 1. Coach decides team menu
        if regime_name == "fixed_A":
            decision = {"action": "A", "score_a": 1.0, "score_b": 0.0}
        elif regime_name == "fixed_B":
            decision = {"action": "B", "score_a": 0.0, "score_b": 1.0}
        else:  # "adaptive"
            decision = coach.decide_team_training_menu(day)

        menu = decision["action"]
        menu_counts[menu] = menu_counts.get(menu, 0) + 1

        # 2. Players execute training (Plan B: individual rest if fatigued)
        deltas_by_player = {}
        for pid, player in players.items():
            if menu in ("A", "B"):
                res = player.step_training(method=menu, k_base=0.003)
                if res["action"].startswith("train_"):
                    deltas_by_player[pid] = res["deltas"]
            else:  # team rest
                player.fatigue = max(0.0, round(player.fatigue - 0.14, 4))

        # 3. Coach observes and learns
        coach.observe_and_update(day, menu, players, deltas_by_player, rng_seed=100 + day)

        # Snapshot every 10 days
        if day in (1, 30, 60, 100, 115, 140, 165, 200, 250, 300):
            history.append({
                "day": day,
                "chosen_menu": menu,
                "decision_info": decision,
                "avg_pace": round(sum(p.traits["pace"].skill for p in players.values()) / 4.0, 4),
                "avg_pass": round(sum(p.traits["passing"].skill for p in players.values()) / 4.0, 4),
                "avg_shoot": round(sum(p.traits["shooting"].skill for p in players.values()) / 4.0, 4),
                "coach_est_composite": round(sum(coach.evaluations[pid].composite() for pid in roster_ids) / 4.0, 4),
                "learned_gain_A": round(sum(coach.evaluations[pid].method_gains["A"] for pid in roster_ids) / 4.0, 5),
                "learned_gain_B": round(sum(coach.evaluations[pid].method_gains["B"] for pid in roster_ids) / 4.0, 5),
            })

    final_stats = {
        pid: {
            "pace": round(p.traits["pace"].skill, 4),
            "passing": round(p.traits["passing"].skill, 4),
            "shooting": round(p.traits["shooting"].skill, 4),
            "composite": round(p.composite_skill(), 4),
            "coach_est_composite": round(coach.evaluations[pid].composite(), 4),
            "learned_gains": {m: round(v, 5) for m, v in coach.evaluations[pid].method_gains.items()},
        }
        for pid, p in players.items()
    }

    return {
        "regime": regime_name,
        "menu_counts": menu_counts,
        "history": history,
        "final_stats": final_stats,
    }


def main():
    print("=== Running Step 3 & Step 4: Coach Evaluation & Learning Simulations ===")
    res_a = run_regime("fixed_A", 300)
    print("Fixed Method A completed.")

    res_b = run_regime("fixed_B", 300)
    print("Fixed Method B completed.")

    res_adaptive = run_regime("adaptive", 300)
    print("Adaptive Coach AI completed.")

    print("\n--- Final Squad Averages at Day 300 ---")
    for r in [res_a, res_b, res_adaptive]:
        p_avg = sum(s["pace"] for s in r["final_stats"].values()) / 4.0
        pass_avg = sum(s["passing"] for s in r["final_stats"].values()) / 4.0
        s_avg = sum(s["shooting"] for s in r["final_stats"].values()) / 4.0
        comp_avg = sum(s["composite"] for s in r["final_stats"].values()) / 4.0
        print(f"[{r['regime'].upper()}]: Pace={p_avg:.3f}, Pass={pass_avg:.3f}, Shoot={s_avg:.3f} | Comp={comp_avg:.3f} (Menus: {r['menu_counts']})")

    # Save results to JSON
    out_json = "/tmp/coach_learning_results.json"
    with open(out_json, "w") as f:
        json.dump({"fixed_A": res_a, "fixed_B": res_b, "adaptive": res_adaptive}, f, indent=2)
    print(f"\nSaved coach learning results to {out_json}")


if __name__ == "__main__":
    main()
