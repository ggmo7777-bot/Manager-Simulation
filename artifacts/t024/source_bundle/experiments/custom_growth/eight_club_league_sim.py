import json
import random
from experiments.custom_growth.growth_engine import (
    CustomPlayer,
    TraitState,
    calc_match_trait_delta,
    calc_trait_delta,
)
from experiments.custom_growth.coach_policy import TeamCoach
from experiments.custom_growth.run_verification import run_experiment_1_pure_curves

CLUBS = [f"club_{c}" for c in "abcdefgh"]
ACADEMY_CLUBS = ["club_a", "club_c", "club_e", "club_g"]
BUYER_CLUBS = ["club_b", "club_d", "club_f", "club_h"]
GROWTH_TYPES = ["early", "steady", "late", "burst"]

def generate_specs(seed=100):
    rng = random.Random(seed)
    specs = {}
    pid_counter = 1
    for cid in CLUBS:
        for _ in range(4):
            pid = f"p{pid_counter}"
            cap = round(rng.uniform(0.75, 0.94), 2)
            traits = {}
            for tname in ["pace", "passing", "shooting"]:
                init_val = round(rng.uniform(0.33, 0.48), 2)
                gtype = rng.choice(GROWTH_TYPES)
                traits[tname] = (init_val, gtype)
            loyalty = round(rng.uniform(0.15, 0.55), 2)
            ambition = round(rng.uniform(0.40, 0.85), 2)
            specs[pid] = {
                "club": cid, "cap": cap, "traits": traits,
                "loyalty": loyalty, "ambition": ambition
            }
            pid_counter += 1
    return specs

def generate_round_robin_fixtures(teams):
    n = len(teams)
    pool = list(teams)
    rounds = []
    # 1st Half: 7 rounds
    for round_idx in range(n - 1):
        r_matches = []
        for i in range(n // 2):
            t1 = pool[i]
            t2 = pool[n - 1 - i]
            if (round_idx + i) % 2 == 0:
                r_matches.append((t1, t2))
            else:
                r_matches.append((t2, t1))
        rounds.append(r_matches)
        pool = [pool[0]] + [pool[-1]] + pool[1:-1]
    
    # 2nd Half: 7 rounds (reverse home & away)
    second_half = []
    for r in rounds:
        second_half.append([(a, h) for h, a in r])
    
    all_14_rounds = rounds + second_half
    schedule = {}
    for idx, r_matches in enumerate(all_14_rounds, 1):
        day = idx * 7
        schedule[day] = r_matches
    return schedule

def run_8club_season(seed=100):
    rng = random.Random(seed)
    player_specs = generate_specs(seed)
    schedule = generate_round_robin_fixtures(CLUBS)
    total_days = max(schedule.keys()) # Day 98

    players = {}
    player_minds = {}
    initial_players = {}
    rosters = {cid: [] for cid in CLUBS}

    for pid, spec in player_specs.items():
        t_states = {tname: TraitState(skill=val[0], growth_type=val[1]) for tname, val in spec["traits"].items()}
        players[pid] = CustomPlayer(player_id=pid, traits=t_states, cap=spec["cap"])
        initial_players[pid] = {
            "pace": spec["traits"]["pace"][0],
            "passing": spec["traits"]["passing"][0],
            "shooting": spec["traits"]["shooting"][0],
            "composite": round((spec["traits"]["pace"][0] + spec["traits"]["passing"][0] + spec["traits"]["shooting"][0]) / 3.0, 4),
        }
        player_minds[pid] = {
            "loyalty": spec["loyalty"],
            "ambition": spec["ambition"],
            "satisfaction": 0.50,
            "origin_club": spec["club"],
            "club_id": spec["club"],
            "appearances": 0,
            "goals": 0,
        }
        rosters[spec["club"]].append(pid)

    all_pids = list(player_specs.keys())
    coaches = {}
    for cid in CLUBS:
        style = "possession" if cid in ACADEMY_CLUBS else "direct"
        coaches[cid] = TeamCoach(coach_id=f"coach_{cid}", roster=all_pids, preferred_style=style)

    club_accounts = {
        cid: {
            "points": 0, "played": 0, "won": 0, "drawn": 0, "lost": 0,
            "gf": 0, "ga": 0,
            "cash": 25.0 if cid in ACADEMY_CLUBS else 65.0,
            "initial_cash": 25.0 if cid in ACADEMY_CLUBS else 65.0,
            "facility_days": 0,
        }
        for cid in CLUBS
    }

    matches_log = []
    transfers_log = []
    transactions_log = []
    days_summary = []

    club_history = {cid: {"rank": [], "points": [], "gd": [], "cash": []} for cid in CLUBS}
    player_history = {
        pid: {"composite": [], "pace": [], "passing": [], "shooting": [], "fatigue": [], "club_id": []}
        for pid in player_specs
    }

    def record_snapshot():
        ranked = sorted(CLUBS, key=lambda c: (
            club_accounts[c]["points"],
            club_accounts[c]["gf"] - club_accounts[c]["ga"],
            club_accounts[c]["gf"]
        ), reverse=True)
        for r_idx, cid in enumerate(ranked, 1):
            club_history[cid]["rank"].append(r_idx)
            club_history[cid]["points"].append(club_accounts[cid]["points"])
            club_history[cid]["gd"].append(club_accounts[cid]["gf"] - club_accounts[cid]["ga"])
            club_history[cid]["cash"].append(round(club_accounts[cid]["cash"], 2))
        for pid in player_specs:
            p = players[pid]
            player_history[pid]["composite"].append(round(p.composite_skill(), 4))
            player_history[pid]["pace"].append(round(p.traits["pace"].skill, 4))
            player_history[pid]["passing"].append(round(p.traits["passing"].skill, 4))
            player_history[pid]["shooting"].append(round(p.traits["shooting"].skill, 4))
            player_history[pid]["fatigue"].append(round(p.fatigue, 2))
            player_history[pid]["club_id"].append(player_minds[pid]["club_id"])

    record_snapshot()

    # Mid-season transfer window at Day 49 (after 1st round-robin completes)
    for day in range(1, total_days + 1):
        is_matchday = day in schedule
        day_active_facilities = []
        day_matches = []
        day_transfers = []

        # 1. Facility Cost & Operations
        for cid in CLUBS:
            acc = club_accounts[cid]
            if acc["cash"] >= 16.50:
                acc["cash"] -= 0.28
                acc["facility_days"] += 1
                day_active_facilities.append(cid)
                transactions_log.append({
                    "day": day, "reason": "training_facility_operation",
                    "payer": cid, "payee": "facility_operator", "amount": 0.28,
                    "note": f"{cid.upper()} 育成設備運営費"
                })
                fac_mult = 1.25
            else:
                fac_mult = 0.52

        # 2. Daily Training on non-matchdays
        if not is_matchday:
            for cid in CLUBS:
                coach = coaches[cid]
                roster = rosters[cid]
                menu_dec = coach.decide_team_training_menu(day)
                menu = menu_dec["action"]
                fac_mult = 1.25 if cid in day_active_facilities else 0.52

                deltas_by_pid = {}
                for pid in roster:
                    player = players[pid]
                    if menu in ("A", "B"):
                        res = player.step_training(method=menu, k_base=0.003 * (fac_mult / 1.0))
                        if res["action"].startswith("train_"):
                            deltas_by_pid[pid] = res["deltas"]
                    else:
                        player.fatigue = max(0.0, round(player.fatigue - 0.14, 4))

                coach.observe_and_update(day, menu, players, deltas_by_pid, rng_seed=seed + day)

        # 3. Matchday Execution
        if is_matchday:
            day_matches_sched = schedule[day]
            for h_cid, a_cid in day_matches_sched:
                h_coach = coaches[h_cid]
                a_coach = coaches[a_cid]

                def pick_starters(coach, cid):
                    r = rosters[cid]
                    scored = []
                    for pid in r:
                        ev = coach.evaluations[pid]
                        sc = 0.58 * ev.composite() - 0.22 * ev.est_fatigue
                        scored.append((sc, pid))
                    scored.sort(reverse=True)
                    num_starters = min(3, max(1, len(scored) - 1))
                    starters = [s[1] for s in scored[:num_starters]]
                    bench = [s[1] for s in scored[num_starters:]]
                    return starters, bench

                h_starters, h_bench = pick_starters(h_coach, h_cid)
                a_starters, a_bench = pick_starters(a_coach, a_cid)

                h_skill = sum(players[p].composite_skill() * (1.0 - 0.15 * players[p].fatigue) for p in h_starters)
                a_skill = sum(players[p].composite_skill() * (1.0 - 0.15 * players[p].fatigue) for p in a_starters)

                skill_diff = (h_skill - a_skill) / 3.0
                h_lambda = max(0.2, 1.3 + 2.0 * skill_diff)
                a_lambda = max(0.2, 1.3 - 2.0 * skill_diff)

                h_goals = (1 if rng.random() < h_lambda / 2.2 else 0) + (1 if rng.random() < h_lambda / 3.2 else 0) + (1 if rng.random() < h_lambda / 4.5 else 0)
                a_goals = (1 if rng.random() < a_lambda / 2.2 else 0) + (1 if rng.random() < a_lambda / 3.2 else 0) + (1 if rng.random() < a_lambda / 4.5 else 0)

                h_acc = club_accounts[h_cid]
                a_acc = club_accounts[a_cid]
                h_acc["played"] += 1; a_acc["played"] += 1
                h_acc["gf"] += h_goals; h_acc["ga"] += a_goals
                a_acc["gf"] += a_goals; a_acc["ga"] += h_goals

                h_acc["cash"] += 1.20; a_acc["cash"] += 1.20
                transactions_log.append({"day": day, "reason": "matchday_revenue", "payer": "league", "payee": h_cid, "amount": 1.20, "note": "試合開催配分金"})
                transactions_log.append({"day": day, "reason": "matchday_revenue", "payer": "league", "payee": a_cid, "amount": 1.20, "note": "アウェイ開催配分金"})

                if h_goals > a_goals:
                    h_acc["won"] += 1; h_acc["points"] += 3; h_acc["cash"] += 0.40; a_acc["lost"] += 1
                    transactions_log.append({"day": day, "reason": "matchday_revenue", "payer": "league", "payee": h_cid, "amount": 0.40, "note": "勝利ボーナス"})
                elif a_goals > h_goals:
                    a_acc["won"] += 1; a_acc["points"] += 3; a_acc["cash"] += 0.40; h_acc["lost"] += 1
                    transactions_log.append({"day": day, "reason": "matchday_revenue", "payer": "league", "payee": a_cid, "amount": 0.40, "note": "アウェイ勝利ボーナス"})
                else:
                    h_acc["drawn"] += 1; a_acc["drawn"] += 1
                    h_acc["points"] += 1; a_acc["points"] += 1
                    h_acc["cash"] += 0.15; a_acc["cash"] += 0.15

                # Starters match experience & fatigue
                for pid in (*h_starters, *a_starters):
                    p = players[pid]
                    pm = player_minds[pid]
                    pm["appearances"] += 1
                    pm["satisfaction"] = min(0.98, pm["satisfaction"] + 0.12)
                    p.fatigue = min(0.95, round(p.fatigue + 0.08, 4))
                    for tname, tstate in p.traits.items():
                        dm = calc_match_trait_delta(skill=tstate.skill, cap=p.cap, growth_type=tstate.growth_type, exp=tstate.exp, q_factor=0.85)
                        tstate.skill = min(p.cap, round(tstate.skill + dm, 5))
                        tstate.exp += 0.8

                # Bench recovery
                for pid in (*h_bench, *a_bench):
                    players[pid].fatigue = max(0.0, round(players[pid].fatigue - 0.08, 4))

                m_data = {
                    "day": day, "home": h_cid, "away": a_cid,
                    "home_goals": h_goals, "away_goals": a_goals,
                    "home_starters": h_starters, "away_starters": a_starters,
                    "home_bench": h_bench, "away_bench": a_bench
                }
                matches_log.append(m_data)
                day_matches.append(m_data)

        # 4. Mid-season Transfer Window (Day 49)
        if day == 49:
            # Buyer clubs attempt to acquire top performing players from Academy clubs
            for buyer_cid in BUYER_CLUBS:
                b_acc = club_accounts[buyer_cid]
                if b_acc["cash"] >= 25.0:
                    b_coach = coaches[buyer_cid]
                    candidates = []
                    for acad_cid in ACADEMY_CLUBS:
                        for pid in rosters[acad_cid]:
                            p = players[pid]
                            pm = player_minds[pid]
                            ev = b_coach.evaluations[pid]
                            if ev.composite() > 0.46 and pm["ambition"] > 0.55:
                                score = ev.composite() * pm["ambition"] - pm["loyalty"] * 0.3
                                candidates.append((score, pid, acad_cid))
                    if candidates:
                        candidates.sort(reverse=True)
                        score, target_pid, seller_cid = candidates[0]
                        b_acc["cash"] -= 12.0
                        club_accounts[seller_cid]["cash"] += 12.0
                        rosters[seller_cid].remove(target_pid)
                        rosters[buyer_cid].append(target_pid)
                        player_minds[target_pid]["club_id"] = buyer_cid

                        tr_info = {
                            "day": day, "player_id": target_pid,
                            "from_club": seller_cid, "to_club": buyer_cid,
                            "base_fee": 12.0, "score": round(score, 4)
                        }
                        transfers_log.append({"day": day, "data": tr_info})
                        day_transfers.append(tr_info)
                        transactions_log.append({
                            "day": day, "reason": "transfer_base_fee",
                            "payer": buyer_cid, "payee": seller_cid, "amount": 12.0,
                            "note": f"{target_pid.upper()} 移籍金 ({seller_cid.upper()} ➔ {buyer_cid.upper()})"
                        })

        days_summary.append({
            "day": day, "is_matchday": is_matchday,
            "matches": day_matches, "transfers": day_transfers,
            "facility_active_clubs": day_active_facilities
        })
        record_snapshot()

    # Final Standings
    ranked = sorted(CLUBS, key=lambda c: (club_accounts[c]["points"], club_accounts[c]["gf"] - club_accounts[c]["ga"], club_accounts[c]["gf"]), reverse=True)
    standings = []
    for rank, cid in enumerate(ranked, 1):
        acc = club_accounts[cid]
        standings.append({
            "rank": rank, "club_id": cid,
            "role": "Academy (育成型)" if cid in ACADEMY_CLUBS else "Buyer (補強型)",
            "played": acc["played"], "won": acc["won"], "drawn": acc["drawn"], "lost": acc["lost"],
            "gf": acc["gf"], "ga": acc["ga"], "gd": acc["gf"] - acc["ga"],
            "points": acc["points"], "cash": round(acc["cash"], 2),
            "initial_cash": acc["initial_cash"],
            "net_profit": round(acc["cash"] - acc["initial_cash"], 2),
            "facility_days": acc["facility_days"],
        })

    # Final Players
    players_list = []
    for pid in player_specs:
        p = players[pid]
        p0 = initial_players[pid]
        pm = player_minds[pid]
        gain = p.composite_skill() - p0["composite"]
        players_list.append({
            "player_id": pid, "club_id": pm["club_id"], "origin_club": pm["origin_club"],
            "appearances": pm["appearances"], "cap": p.cap,
            "initial": p0,
            "final": {
                "pace": round(p.traits["pace"].skill, 4),
                "passing": round(p.traits["passing"].skill, 4),
                "shooting": round(p.traits["shooting"].skill, 4),
                "composite": round(p.composite_skill(), 4),
                "avg": round(p.composite_skill(), 3),
            },
            "growth_types": {tname: p.traits[tname].growth_type for tname in p.traits},
            "exp": {tname: round(p.traits[tname].exp, 1) for tname in p.traits},
            "gain": round(gain, 4),
        })

    pure_exp1 = run_experiment_1_pure_curves()
    curve_data = {
        "steps": [d["step"] for d in pure_exp1],
        "early": [d["skills"]["early"] for d in pure_exp1],
        "steady": [d["skills"]["steady"] for d in pure_exp1],
        "late": [d["skills"]["late"] for d in pure_exp1],
        "burst": [d["skills"]["burst"] for d in pure_exp1],
    }

    return {
        "standings": standings,
        "matches": matches_log,
        "transfers": transfers_log,
        "players": players_list,
        "transactions": transactions_log,
        "days_summary": days_summary,
        "curve_data": curve_data,
        "history": {
            "days": list(range(total_days + 1)),
            "clubs": club_history,
            "players": player_history,
        },
        "seed": seed,
    }

print("Running 8-club, 32-player, 14-matchday round-robin simulation...")
data8 = run_8club_season(100)
print(f"Simulation completed successfully!")
print(f"Clubs: {len(data8['standings'])}, Matches: {len(data8['matches'])}, Players: {len(data8['players'])}, Days: {len(data8['history']['days'])}")

print("\n--- Final 8-Club Standings ---")
for s in data8["standings"]:
    print(f"  {s['rank']}位: {s['club_id'].upper()} ({s['role']}): {s['points']} pts (W{s['won']} D{s['drawn']} L{s['lost']}) GD={s['gd']:+d} Cash={s['cash']:.2f}")

with open("/tmp/eight_club_sim_data.json", "w") as f:
    json.dump(data8, f)
print("\nSaved to /tmp/eight_club_sim_data.json")
