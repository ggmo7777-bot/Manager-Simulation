"""4-Club League Simulation with Custom Trait-Independent Growth Curves.

Runs a full 84-day season (24 matches, 4 clubs, 12 players) featuring:
- Team-wide training with Plan B individual rest threshold.
- 4 growth curves per trait (Early, Steady, Late, Burst).
- Capability-specific match experience growth Delta S_j^match.
- Adaptive coach lineup selection and menu decisions.
- Transfers on Day 28 and Day 56.
"""

import json
import random
from dataclasses import dataclass, field
from typing import Any
from experiments.custom_growth.growth_engine import (
    CustomPlayer,
    TraitState,
    calc_match_trait_delta,
    GrowthType,
)
from experiments.custom_growth.coach_policy import TeamCoach

CLUBS = ["club_a", "club_b", "club_c", "club_d"]
ACADEMY_CLUBS = ["club_a", "club_c"]
BUYER_CLUBS = ["club_b", "club_d"]

ROSTERS_INIT = {
    "club_a": ["p1", "p2", "p3"],
    "club_b": ["p4", "p5", "p6"],
    "club_c": ["p7", "p8", "p9"],
    "club_d": ["p10", "p11", "p12"],
}

# Diverse personality & growth types per trait
PLAYER_SPECS = {
    "p1": {"club": "club_a", "cap": 0.90, "traits": {"pace": (0.42, "early"), "passing": (0.35, "late"), "shooting": (0.38, "steady")}, "loyalty": 0.20, "ambition": 0.75},
    "p2": {"club": "club_a", "cap": 0.88, "traits": {"pace": (0.38, "steady"), "passing": (0.36, "burst"), "shooting": (0.40, "early")}, "loyalty": 0.35, "ambition": 0.60},
    "p3": {"club": "club_a", "cap": 0.92, "traits": {"pace": (0.35, "burst"), "passing": (0.40, "steady"), "shooting": (0.36, "late")}, "loyalty": 0.55, "ambition": 0.80},
    "p4": {"club": "club_b", "cap": 0.78, "traits": {"pace": (0.48, "late"), "passing": (0.45, "early"), "shooting": (0.50, "burst")}, "loyalty": 0.15, "ambition": 0.70},
    "p5": {"club": "club_b", "cap": 0.75, "traits": {"pace": (0.40, "steady"), "passing": (0.42, "steady"), "shooting": (0.44, "steady")}, "loyalty": 0.25, "ambition": 0.50},
    "p6": {"club": "club_b", "cap": 0.80, "traits": {"pace": (0.52, "early"), "passing": (0.40, "steady"), "shooting": (0.46, "late")}, "loyalty": 0.30, "ambition": 0.65},
    "p7": {"club": "club_c", "cap": 0.89, "traits": {"pace": (0.36, "late"), "passing": (0.38, "burst"), "shooting": (0.42, "early")}, "loyalty": 0.40, "ambition": 0.65},
    "p8": {"club": "club_c", "cap": 0.94, "traits": {"pace": (0.45, "early"), "passing": (0.38, "late"), "shooting": (0.48, "burst")}, "loyalty": 0.25, "ambition": 0.85},
    "p9": {"club": "club_c", "cap": 0.88, "traits": {"pace": (0.40, "burst"), "passing": (0.42, "steady"), "shooting": (0.36, "steady")}, "loyalty": 0.50, "ambition": 0.55},
    "p10": {"club": "club_d", "cap": 0.76, "traits": {"pace": (0.46, "steady"), "passing": (0.48, "early"), "shooting": (0.44, "burst")}, "loyalty": 0.10, "ambition": 0.80},
    "p11": {"club": "club_d", "cap": 0.74, "traits": {"pace": (0.42, "early"), "passing": (0.40, "steady"), "shooting": (0.40, "steady")}, "loyalty": 0.45, "ambition": 0.40},
    "p12": {"club": "club_d", "cap": 0.75, "traits": {"pace": (0.38, "late"), "passing": (0.38, "late"), "shooting": (0.46, "steady")}, "loyalty": 0.35, "ambition": 0.50},
}


def create_league_world(seed: int = 100):
    rng = random.Random(seed)
    players: dict[str, CustomPlayer] = {}
    player_minds: dict[str, dict[str, Any]] = {}
    initial_players: dict[str, dict[str, Any]] = {}

    for pid, spec in PLAYER_SPECS.items():
        t_states = {
            tname: TraitState(skill=val[0], growth_type=val[1])
            for tname, val in spec["traits"].items()
        }
        player = CustomPlayer(player_id=pid, traits=t_states, cap=spec["cap"])
        players[pid] = player
        initial_players[pid] = {
            "pace": spec["traits"]["pace"][0],
            "passing": spec["traits"]["passing"][0],
            "shooting": spec["traits"]["shooting"][0],
            "composite": (spec["traits"]["pace"][0] + spec["traits"]["passing"][0] + spec["traits"]["shooting"][0]) / 3.0,
        }
        player_minds[pid] = {
            "loyalty": spec["loyalty"],
            "ambition": spec["ambition"],
            "satisfaction": 0.50,
            "club_id": spec["club"],
            "appearances": 0,
            "goals": 0,
        }

    coaches: dict[str, TeamCoach] = {}
    all_pids = list(PLAYER_SPECS.keys())
    for cid in CLUBS:
        style = "possession" if cid in ACADEMY_CLUBS else "direct"
        coaches[cid] = TeamCoach(coach_id=f"coach_{cid}", roster=all_pids, preferred_style=style)

    club_accounts = {
        cid: {
            "points": 0, "played": 0, "won": 0, "drawn": 0, "lost": 0,
            "gf": 0, "ga": 0, "cash": 21.0 if cid in ACADEMY_CLUBS else 59.0,
            "facility_days": 0,
        }
        for cid in CLUBS
    }

    rosters = {cid: list(pids) for cid, pids in ROSTERS_INIT.items()}
    return {
        "seed": seed,
        "players": players,
        "initial_players": initial_players,
        "player_minds": player_minds,
        "coaches": coaches,
        "club_accounts": club_accounts,
        "rosters": rosters,
        "events": [],
    }


def run_full_league_simulation(seed: int = 100):
    world = create_league_world(seed)
    rng = random.Random(seed)

    # Fixture schedule: 12 matchdays (Day 7, 14, 21, ..., 84)
    # Pairs rotate round-robin
    fixtures_schedule = {
        7: [("club_a", "club_b"), ("club_c", "club_d")],
        14: [("club_a", "club_c"), ("club_b", "club_d")],
        21: [("club_a", "club_d"), ("club_b", "club_c")],
        28: [("club_b", "club_a"), ("club_d", "club_c")],
        35: [("club_c", "club_a"), ("club_d", "club_b")],
        42: [("club_d", "club_a"), ("club_c", "club_b")],
        49: [("club_a", "club_b"), ("club_c", "club_d")],
        56: [("club_a", "club_c"), ("club_b", "club_d")],
        63: [("club_a", "club_d"), ("club_b", "club_c")],
        70: [("club_b", "club_a"), ("club_d", "club_c")],
        77: [("club_c", "club_a"), ("club_d", "club_b")],
        84: [("club_d", "club_a"), ("club_c", "club_b")],
    }

    matches_log = []
    transfers_log = []
    daily_log = []

    club_history = {cid: {"rank": [], "points": [], "gd": [], "cash": []} for cid in CLUBS}
    player_history = {
        pid: {"composite": [], "pace": [], "passing": [], "shooting": [], "fatigue": [], "club_id": []}
        for pid in world["players"]
    }

    def record_snapshot():
        ranked = sorted(CLUBS, key=lambda c: (
            world["club_accounts"][c]["points"],
            world["club_accounts"][c]["gf"] - world["club_accounts"][c]["ga"],
            world["club_accounts"][c]["gf"]
        ), reverse=True)
        for r_idx, cid in enumerate(ranked, 1):
            club_history[cid]["rank"].append(r_idx)
            club_history[cid]["points"].append(world["club_accounts"][cid]["points"])
            club_history[cid]["gd"].append(world["club_accounts"][cid]["gf"] - world["club_accounts"][cid]["ga"])
            club_history[cid]["cash"].append(round(world["club_accounts"][cid]["cash"], 2))
        for pid, p in world["players"].items():
            player_history[pid]["composite"].append(round(p.composite_skill(), 4))
            player_history[pid]["pace"].append(round(p.traits["pace"].skill, 4))
            player_history[pid]["passing"].append(round(p.traits["passing"].skill, 4))
            player_history[pid]["shooting"].append(round(p.traits["shooting"].skill, 4))
            player_history[pid]["fatigue"].append(round(p.fatigue, 2))
            player_history[pid]["club_id"].append(world["player_minds"][pid]["club_id"])

    record_snapshot()

    for day in range(1, 85):
        is_matchday = day in fixtures_schedule
        day_events = []

        # 1. Facility cost & operations
        for cid in CLUBS:
            acc = world["club_accounts"][cid]
            if acc["cash"] >= 16.50:
                acc["cash"] -= 0.28
                acc["facility_days"] += 1
                fac_mult = 1.25
            else:
                fac_mult = 0.52

        # 2. Daily Training (Only on non-matchdays)
        if not is_matchday:
            for cid in CLUBS:
                coach = world["coaches"][cid]
                roster = world["rosters"][cid]
                menu_dec = coach.decide_team_training_menu(day)
                menu = menu_dec["action"]

                deltas_by_pid = {}
                for pid in roster:
                    player = world["players"][pid]
                    if menu in ("A", "B"):
                        res = player.step_training(method=menu, k_base=0.003 * (fac_mult / 1.0))
                        if res["action"].startswith("train_"):
                            deltas_by_pid[pid] = res["deltas"]
                    else:  # team rest
                        player.fatigue = max(0.0, round(player.fatigue - 0.14, 4))

                coach.observe_and_update(day, menu, world["players"], deltas_by_pid, rng_seed=seed + day)

        # 3. Matchday Execution
        if is_matchday:
            day_matches = fixtures_schedule[day]
            for h_cid, a_cid in day_matches:
                # Coaches select lineups
                h_coach = world["coaches"][h_cid]
                a_coach = world["coaches"][a_cid]

                def pick_starters(coach, cid):
                    r = world["rosters"][cid]
                    scored = []
                    for pid in r:
                        ev = coach.evaluations[pid]
                        sc = 0.58 * ev.composite() - 0.22 * ev.est_fatigue
                        scored.append((sc, pid))
                    scored.sort(reverse=True)
                    starters = [scored[0][1], scored[1][1]]
                    bench = [s[1] for s in scored[2:]]
                    return starters, bench

                h_starters, h_bench = pick_starters(h_coach, h_cid)
                a_starters, a_bench = pick_starters(a_coach, a_cid)

                # Match simulation: compare starter skills & fatigue
                h_skill = sum(world["players"][p].composite_skill() * (1.0 - 0.15 * world["players"][p].fatigue) for p in h_starters)
                a_skill = sum(world["players"][p].composite_skill() * (1.0 - 0.15 * world["players"][p].fatigue) for p in a_starters)

                # Goal generation based on skill differential
                h_lambda = max(0.2, 1.2 + 2.5 * (h_skill - a_skill))
                a_lambda = max(0.2, 1.2 + 2.5 * (a_skill - h_skill))

                h_goals = rng.poisson(h_lambda) if hasattr(rng, "poisson") else (1 if rng.random() < h_lambda / 2.5 else 0) + (1 if rng.random() < h_lambda / 3.5 else 0)
                a_goals = rng.poisson(a_lambda) if hasattr(rng, "poisson") else (1 if rng.random() < a_lambda / 2.5 else 0) + (1 if rng.random() < a_lambda / 3.5 else 0)

                # Update Club Standings & Accounts
                h_acc = world["club_accounts"][h_cid]
                a_acc = world["club_accounts"][a_cid]
                h_acc["played"] += 1
                a_acc["played"] += 1
                h_acc["gf"] += h_goals
                h_acc["ga"] += a_goals
                a_acc["gf"] += a_goals
                a_acc["ga"] += h_goals

                # Cash & points
                h_acc["cash"] += 1.20  # matchday base revenue
                a_acc["cash"] += 1.20
                if h_goals > a_goals:
                    h_acc["won"] += 1
                    h_acc["points"] += 3
                    h_acc["cash"] += 0.40  # win bonus
                    a_acc["lost"] += 1
                elif a_goals > h_goals:
                    a_acc["won"] += 1
                    a_acc["points"] += 3
                    a_acc["cash"] += 0.40  # win bonus
                    h_acc["lost"] += 1
                else:
                    h_acc["drawn"] += 1
                    a_acc["drawn"] += 1
                    h_acc["points"] += 1
                    a_acc["points"] += 1
                    h_acc["cash"] += 0.15
                    a_acc["cash"] += 0.15

                # Player Match Growth using Delta S_j^match formula
                # Starters
                for pid in (*h_starters, *a_starters):
                    p = world["players"][pid]
                    pm = world["player_minds"][pid]
                    pm["appearances"] += 1
                    pm["satisfaction"] = min(0.98, pm["satisfaction"] + 0.14)
                    p.fatigue = min(0.95, round(p.fatigue + 0.08, 4))

                    # Apply trait-specific match growth Delta S_j^match
                    for tname, tstate in p.traits.items():
                        delta_m = calc_match_trait_delta(
                            skill=tstate.skill,
                            cap=p.cap,
                            growth_type=tstate.growth_type,
                            exp=tstate.exp,
                            q_factor=1.0,
                        )
                        tstate.skill = round(min(p.cap, tstate.skill + delta_m), 6)
                        tstate.exp = round(tstate.exp + 1.0, 4)

                # Bench players: rest fatigue, lose satisfaction
                for pid in (*h_bench, *a_bench):
                    p = world["players"][pid]
                    pm = world["player_minds"][pid]
                    p.fatigue = max(0.0, round(p.fatigue - 0.06, 4))
                    pm["satisfaction"] = max(0.05, round(pm["satisfaction"] - 0.11, 4))

                matches_log.append({
                    "day": day,
                    "home": h_cid, "away": a_cid,
                    "h_goals": h_goals, "a_goals": a_goals,
                    "h_starters": h_starters, "a_starters": a_starters,
                    "h_bench": h_bench, "a_bench": a_bench,
                })

        # 4. Transfer Windows (Day 28 & Day 56)
        if day in (28, 56):
            # Check buyer clubs looking for players
            for buyer_cid in BUYER_CLUBS:
                b_coach = world["coaches"][buyer_cid]
                b_acc = world["club_accounts"][buyer_cid]
                if b_acc["cash"] < 12.0 + 8.0:  # Need 12.0 fee + 8.0 reserve floor
                    continue

                # Find candidates from academy clubs
                candidates = []
                for acad_cid in ACADEMY_CLUBS:
                    for pid in world["rosters"][acad_cid]:
                        p = world["players"][pid]
                        pm = world["player_minds"][pid]
                        ev = b_coach.evaluations[pid]
                        # Offer condition: high evaluated skill + player ambition
                        if ev.composite() > 0.48 and pm["ambition"] > 0.60:
                            candidates.append((ev.composite() * pm["ambition"] - pm["loyalty"] * 0.4, pid, acad_cid))

                if candidates:
                    candidates.sort(reverse=True)
                    score, target_pid, seller_cid = candidates[0]
                    # Execute Transfer
                    b_acc["cash"] -= 12.0
                    world["club_accounts"][seller_cid]["cash"] += 12.0
                    world["rosters"][seller_cid].remove(target_pid)
                    world["rosters"][buyer_cid].append(target_pid)
                    world["player_minds"][target_pid]["club_id"] = buyer_cid

                    transfers_log.append({
                        "day": day,
                        "player_id": target_pid,
                        "from_club": seller_cid,
                        "to_club": buyer_cid,
                        "fee": 12.0,
                        "score": round(score, 4),
                    })

        record_snapshot()

    # Compile Final Standings
    standings = []
    ranked = sorted(CLUBS, key=lambda c: (world["club_accounts"][c]["points"], world["club_accounts"][c]["gf"] - world["club_accounts"][c]["ga"]), reverse=True)
    for rank, cid in enumerate(ranked, 1):
        acc = world["club_accounts"][cid]
        standings.append({
            "rank": rank,
            "club_id": cid,
            "role": "Academy (育成型)" if cid in ACADEMY_CLUBS else "Buyer (補強型)",
            "played": acc["played"],
            "won": acc["won"],
            "drawn": acc["drawn"],
            "lost": acc["lost"],
            "gf": acc["gf"],
            "ga": acc["ga"],
            "gd": acc["gf"] - acc["ga"],
            "points": acc["points"],
            "cash": round(acc["cash"], 2),
            "facility_days": acc["facility_days"],
        })

    # Compile Final Player Stats
    player_summaries = {}
    for pid, p in world["players"].items():
        p0 = world["initial_players"][pid]
        pm = world["player_minds"][pid]
        player_summaries[pid] = {
            "player_id": pid,
            "club_id": pm["club_id"],
            "appearances": pm["appearances"],
            "satisfaction": round(pm["satisfaction"], 2),
            "cap": p.cap,
            "initial": p0,
            "final": {
                "pace": round(p.traits["pace"].skill, 4),
                "passing": round(p.traits["passing"].skill, 4),
                "shooting": round(p.traits["shooting"].skill, 4),
                "composite": round(p.composite_skill(), 4),
            },
            "growth_types": {t: p.traits[t].growth_type for t in p.traits},
            "exp": {t: round(p.traits[t].exp, 1) for t in p.traits},
            "gain": round(p.composite_skill() - p0["composite"], 4),
        }

    return {
        "standings": standings,
        "matches": matches_log,
        "transfers": transfers_log,
        "players": player_summaries,
        "history": {
            "days": list(range(85)),
            "clubs": club_history,
            "players": player_history,
        },
    }


def main():
    print("=== Running Full 4-Club League Season with Custom Trait-Specific Growth ===")
    results = run_full_league_simulation(seed=100)

    print("\n--- Final League Standings (Day 84) ---")
    for s in results["standings"]:
        print(f"{s['rank']}位: {s['club_id'].upper()} ({s['role']}): {s['points']} pts (W{s['won']} D{s['drawn']} L{s['lost']}), GD={s['gd']:+d}, Cash={s['cash']:.2f}")

    print(f"\n--- Transfers Completed ({len(results['transfers'])}件) ---")
    for t in results["transfers"]:
        print(f"  Day {t['day']}: {t['player_id'].upper()} ({t['from_club']} -> {t['to_club']}), Fee={t['fee']}")

    print("\n--- Player Growth Highlights ---")
    for pid, p in sorted(results["players"].items(), key=lambda x: x[1]["gain"], reverse=True):
        print(f"  {pid.upper()} ({p['club_id']}): Init={p['initial']['composite']:.3f} -> Final={p['final']['composite']:.3f} (Gain={p['gain']:+.4f}) | Apps={p['appearances']} | Types: Pace={p['growth_types']['pace']}, Pass={p['growth_types']['passing']}, Shoot={p['growth_types']['shooting']}")

    out_file = "/tmp/custom_league_season_results.json"
    with open(out_file, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {out_file}")


if __name__ == "__main__":
    main()
