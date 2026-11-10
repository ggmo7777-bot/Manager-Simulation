"""Unit tests for T011: 4-club league, finite budget ledger, development solidarity dividends, and multi-club transfer market (M5).

Author: Antigravity (Gemini 4 Argon) — distinct from the creator of T001-T007 (Sol 6.1 High).
"""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from soccer_sim import league_economy as le
from soccer_sim.storage import digest


class TestT011LeagueEconomy(unittest.TestCase):
    def test_author_and_identity_attribution(self) -> None:
        ident = le.league_identity()
        self.assertEqual(ident["author"], "Antigravity (Gemini 4 Argon)")
        self.assertNotIn("Sol 6.1", ident["author"])
        self.assertEqual(ident["world_kind"], "league_economy_loop")
        self.assertEqual(ident["revision_author"], "Sol 6.1 High (T012)")
        self.assertEqual(ident["save"], 13)
        self.assertEqual(ident["rules"], "t011-league-economy-v2")
        self.assertEqual(ident["policy"], "t011-league-economy-score-v2")

    def test_non_leakage_in_all_policy_functions(self) -> None:
        cfg = le.config_for_condition("open_solidarity")
        w1 = le._build_initial_world(seed=17, config=cfg)
        w2 = le._build_initial_world(seed=17, config=cfg)

        # Mutate hidden truth in w2 without changing private minds or cash
        for pid in le.PLAYER_IDS:
            w2.players[pid].traits = le.MatchTraits(pace=0.95, passing=0.95, shooting=0.95)
            w2.players[pid].rates = {"A": 0.25, "B": 0.25}
            w2.players[pid].cap = 0.99
            w2.players[pid].fatigue = 0.85

        for cid in le.COACH_IDS:
            c1 = w1.coaches[cid]
            c2 = w2.coaches[cid]
            own1 = list(w1.club_rosters[c1.club_id])
            other1 = [p for p in le.PLAYER_IDS if p not in own1]
            d1 = le.decide_coach_training(
                coach=c1,
                own_roster=own1,
                other_players=other1,
                club_cash=w1.club_accounts[c1.club_id].cash,
                day=1,
                slot=1,
                already_targeted=set(),
                config=cfg,
            )
            d2 = le.decide_coach_training(
                coach=c2,
                own_roster=own1,
                other_players=other1,
                club_cash=w2.club_accounts[c2.club_id].cash,
                day=1,
                slot=1,
                already_targeted=set(),
                config=cfg,
            )
            self.assertEqual(d1, d2)

            l1 = le.decide_club_lineup(coach=c1, roster=own1, config=cfg)
            l2 = le.decide_club_lineup(coach=c2, roster=own1, config=cfg)
            self.assertEqual(l1, l2)

        for pid in le.PLAYER_IDS:
            p1_dec = le.decide_player_training(
                mind=w1.player_minds[pid],
                club_id=w1.players[pid].club_id,
                proposals=[],
                config=cfg,
            )
            p2_dec = le.decide_player_training(
                mind=w2.player_minds[pid],
                club_id=w2.players[pid].club_id,
                proposals=[],
                config=cfg,
            )
            self.assertEqual(p1_dec, p2_dec)

            tb1 = le.decide_transfer_bidding(
                player_id=pid,
                from_club=w1.players[pid].club_id,
                from_roster=list(w1.club_rosters[w1.players[pid].club_id]),
                previous_clubs=[],
                bench_streak=3,
                player_mind=w1.player_minds[pid],
                coaches=w1.coaches,
                club_rosters=w1.club_rosters,
                club_Cashes={k: v.cash for k, v in w1.club_accounts.items()},
                config=cfg,
            )
            tb2 = le.decide_transfer_bidding(
                player_id=pid,
                from_club=w2.players[pid].club_id,
                from_roster=list(w2.club_rosters[w2.players[pid].club_id]),
                previous_clubs=[],
                bench_streak=3,
                player_mind=w2.player_minds[pid],
                coaches=w2.coaches,
                club_rosters=w2.club_rosters,
                club_Cashes={k: v.cash for k, v in w2.club_accounts.items()},
                config=cfg,
            )
            self.assertEqual(tb1, tb2)

    def test_ledger_conservation_and_roster_invariants_all_conditions(self) -> None:
        for cond in le.T011_ALL_CONDITIONS:
            cfg = le.config_for_condition(cond)
            world = le.run_league_world(seed=100, days=84, config=cfg)
            summ = le.summarize_league_world(world)
            self.assertEqual(summ["ledger_discrepancy"], 0.0)
            self.assertEqual(summ["insolvency_events"], 0)
            self.assertEqual(summ["actual_duplicate_claims"], 0)
            for cid in le.CLUB_IDS:
                self.assertGreaterEqual(world.club_accounts[cid].cash, 0.0)
                self.assertTrue(2 <= len(world.club_rosters[cid]) <= 4)

    def test_multi_party_agreement_and_budget_required_for_transfer(self) -> None:
        cfg = le.config_for_condition("open_solidarity")
        w = le._build_initial_world(seed=17, config=cfg)
        w.player_minds["p3"].playing_time_satisfaction = 0.15

        # Case 1: Baseline satisfied 3-party agreement for p3
        dec_ok = le.decide_transfer_bidding(
            player_id="p3",
            from_club="club_a",
            from_roster=["p1", "p2", "p3"],
            previous_clubs=[],
            bench_streak=4,
            player_mind=w.player_minds["p3"],
            coaches=w.coaches,
            club_rosters=w.club_rosters,
            club_Cashes={k: v.cash for k, v in w.club_accounts.items()},
            config=cfg,
        )
        self.assertTrue(dec_ok["transfer_approved"])
        self.assertIn(dec_ok["chosen_to_club"], ("club_b", "club_d"))

        # Case 2: Releasing club only has 2 players -> release_consented False
        dec_min_roster = le.decide_transfer_bidding(
            player_id="p3",
            from_club="club_a",
            from_roster=["p1", "p3"],
            previous_clubs=[],
            bench_streak=4,
            player_mind=w.player_minds["p3"],
            coaches=w.coaches,
            club_rosters={**w.club_rosters, "club_a": ["p1", "p3"]},
            club_Cashes={k: v.cash for k, v in w.club_accounts.items()},
            config=cfg,
        )
        self.assertFalse(dec_min_roster["release_consented"])
        self.assertFalse(dec_min_roster["transfer_approved"])

        # Case 3: Buyer clubs lack cash -> offer_made False
        dec_no_cash = le.decide_transfer_bidding(
            player_id="p3",
            from_club="club_a",
            from_roster=["p1", "p2", "p3"],
            previous_clubs=[],
            bench_streak=4,
            player_mind=w.player_minds["p3"],
            coaches=w.coaches,
            club_rosters=w.club_rosters,
            club_Cashes={k: 5.0 for k in le.CLUB_IDS},
            config=cfg,
        )
        self.assertFalse(dec_no_cash["transfer_approved"])

        # Case 4: Circular transfer back to previous club is blocked
        dec_circular = le.decide_transfer_bidding(
            player_id="p3",
            from_club="club_a",
            from_roster=["p1", "p2", "p3"],
            previous_clubs=["club_b", "club_c", "club_d"],
            bench_streak=4,
            player_mind=w.player_minds["p3"],
            coaches=w.coaches,
            club_rosters=w.club_rosters,
            club_Cashes={k: v.cash for k, v in w.club_accounts.items()},
            config=cfg,
        )
        self.assertFalse(dec_circular["transfer_approved"])

    def test_checkpoint_save_resume_exact_equality_all_conditions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tpath = Path(tmp)
            for cond in le.T011_MAIN_CONDITIONS:
                cfg = le.config_for_condition(cond)
                w_full = le.run_league_world(seed=17, days=84, config=cfg)
                w_first = le.run_league_world(seed=17, days=42, config=cfg)
                cdir = tpath / cond
                le.save_league_artifacts(w_first, cdir)
                w_resumed = le.load_league_snapshot(cdir / "snapshot.json")
                le.advance_league_days(w_resumed, 42)
                self.assertEqual(
                    digest(le.snapshot_payload(w_full)),
                    digest(le.snapshot_payload(w_resumed)),
                )

    def test_corrupted_snapshots_rejected(self) -> None:
        cfg = le.config_for_condition("open_solidarity")
        w = le.run_league_world(seed=17, days=28, config=cfg)
        base = le.snapshot_payload(w)

        mutations = []
        # 1. Identity version mismatch
        m1 = copy.deepcopy(base)
        m1["identity"]["save"] = 999
        mutations.append(m1)
        # 2. Negative day
        m2 = copy.deepcopy(base)
        m2["day"] = -1
        mutations.append(m2)
        # 3. Missing club roster
        m3 = copy.deepcopy(base)
        del m3["club_rosters"]["club_d"]
        mutations.append(m3)
        # 4. Roster size < 2
        m4 = copy.deepcopy(base)
        p_move = m4["club_rosters"]["club_a"].pop()
        m4["club_rosters"]["club_c"].append(p_move)
        m4["players"][p_move]["club_id"] = "club_c"
        p_move2 = m4["club_rosters"]["club_a"].pop()
        m4["club_rosters"]["club_c"].append(p_move2)
        m4["players"][p_move2]["club_id"] = "club_c"
        mutations.append(m4)
        # 5. Player club_id mismatch with roster
        m5 = copy.deepcopy(base)
        m5["players"]["p1"]["club_id"] = "club_d"
        mutations.append(m5)
        # 6. Negative club cash
        m6 = copy.deepcopy(base)
        m6["club_accounts"]["club_a"]["cash"] = -0.5
        mutations.append(m6)
        # 7. Ledger conservation discrepancy
        m7 = copy.deepcopy(base)
        m7["club_accounts"]["club_b"]["cash"] += 10.0
        mutations.append(m7)
        # 8. Duplicate dividend claim key
        m8 = copy.deepcopy(base)
        m8["ledger"]["claimed_dividend_keys"] = ["tr1:m1", "tr1:m1"]
        mutations.append(m8)
        # 9. Non-monotonic event id
        m9 = copy.deepcopy(base)
        m9["events"][0]["id"] = "e999"
        mutations.append(m9)
        # 10. Invalid event day > world.day
        m10 = copy.deepcopy(base)
        m10["events"][-1]["day"] = 999
        mutations.append(m10)
        # 11. Unknown cause_ref
        m11 = copy.deepcopy(base)
        m11["events"][-1]["cause_refs"] = ["e_nonexistent"]
        mutations.append(m11)
        # 12. Transfer completed when market_mode is closed
        m12 = copy.deepcopy(base)
        m12["config"]["market_mode"] = "closed"
        mutations.append(m12)

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "bad.json"
            for idx, payload in enumerate(mutations, start=1):
                p.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaises(ValueError, msg=f"mutation {idx} was not rejected"):
                    le.load_league_snapshot(p)

    def test_market_and_solidarity_dividend_effects(self) -> None:
        w_closed = le. summarize_league_world(
            le.run_league_world(seed=101, days=84, config=le.config_for_condition("closed_solidarity"))
        )
        w_open_no_sol = le.summarize_league_world(
            le.run_league_world(seed=101, days=84, config=le.config_for_condition("open_no_solidarity"))
        )
        w_open_sol = le.summarize_league_world(
            le.run_league_world(seed=101, days=84, config=le.config_for_condition("open_solidarity"))
        )

        # Open market enables talent transfers & appearances
        self.assertEqual(w_closed["total_transfers"], 0)
        self.assertGreater(w_open_sol["total_transfers"], 0)
        self.assertGreater(w_open_sol["talents_appearances"], w_closed["talents_appearances"])

        # Solidarity dividends fund academy facilities & boost academy retained skill gain
        self.assertEqual(w_open_no_sol["solidarity_dividends_paid"], 0.0)
        self.assertGreater(w_open_sol["solidarity_dividends_paid"], 15.0)
        self.assertGreater(w_open_sol["academy_facility_days"], w_open_no_sol["academy_facility_days"])
        self.assertGreater(
            w_open_sol["academy_retained_skill_gain"],
            w_open_no_sol["academy_retained_skill_gain"],
        )

    def test_adversarial_churn_and_duplicate_claim_guards(self) -> None:
        w_adv = le.summarize_league_world(
            le.run_league_world(seed=101, days=84, config=le.config_for_condition("exploit_churn_guard"))
        )
        self.assertGreater(w_adv["blocked_circular_transfers"], 0)
        self.assertGreater(w_adv["blocked_duplicate_claims"], 0)
        self.assertEqual(w_adv["actual_duplicate_claims"], 0)
        self.assertEqual(w_adv["ledger_discrepancy"], 0.0)


if __name__ == "__main__":
    unittest.main()
