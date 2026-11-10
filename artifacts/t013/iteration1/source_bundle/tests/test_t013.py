"""Fast boundary and rejection tests; long runs belong in experiments/."""
import copy
from dataclasses import replace
import inspect
import unittest
from unittest.mock import patch

from soccer_sim import league_economy as le
from soccer_sim.league_observation import observe_fatigue


class TestFatigueObservation(unittest.TestCase):
    def test_measurement_is_repeatable_observer_specific_and_can_be_controlled(self):
        args = dict(true_fatigue=0.5, noise=0.05, seed=100, day=2,
                    observer_id="c1", player_id="p3", context="scout")
        value = observe_fatigue(**args)
        self.assertEqual(value, observe_fatigue(**args))
        self.assertNotEqual(value, 0.5)
        self.assertNotEqual(value, observe_fatigue(**{**args, "observer_id": "c2"}))
        self.assertEqual(observe_fatigue(**{**args, "noise": 0}), 0.5)

    def test_all_observation_routes_use_measurements_bench_coach_keeps_prior(self):
        world = le.run_league_world(seed=100, days=0)
        kinds = set()
        nonidentical = set()
        for day in range(1, 15):
            before = copy.deepcopy(world.coaches)
            start = len(world.events)
            le.advance_league_days(world, 1)
            for event in world.events[start:]:
                kind, data = event["kind"], event["data"]
                if kind in ("player_self_observation", "player_fatigue_observation",
                            "coach_session_observation", "coach_scout_observation", "coach_match_observation"):
                    pid = data.get("player_id", data.get("target_player_id"))
                    measured = data.get("obs_fatigue", data.get("perceived_fatigue"))
                    self.assertIsNotNone(measured)
                    kinds.add(kind)
                    if 0.05 < world.players[pid].fatigue < 0.9:
                        self.assertNotEqual(measured, world.players[pid].fatigue)
                        nonidentical.add(kind)
                    if event["actor"] in world.coaches:
                        evaluation = world.coaches[event["actor"]].evaluations[pid]
                        self.assertEqual(measured, evaluation.fatigue_estimate)
                        self.assertEqual(event["id"], evaluation.last_obs_ref)
                    else:
                        self.assertEqual(measured, world.player_minds[pid].perceived_fatigue)
                if kind == "lineup_decision":
                    coach_id = data["coach_id"]
                    for pid in data["bench"]:
                        self.assertEqual(world.coaches[coach_id].evaluations[pid],
                                         before[coach_id].evaluations[pid])
        # The fixed baseline cohort spends these slots on proposals/scouting.
        # Exercise paid session observation explicitly instead of claiming coverage.
        session_world = le.run_league_world(seed=100, days=0)
        def observe_session(**kwargs):
            return {"chosen": {"kind": "observe", "target": kwargs["own_roster"][kwargs["slot"]],
                               "method": None}, "obs_refs": ["init:0"]}
        with patch.object(le, "decide_coach_training", side_effect=observe_session):
            le.advance_league_days(session_world, 1)
        for event in session_world.events:
            if event["kind"] == "coach_session_observation":
                pid = event["data"]["player_id"]
                measured = event["data"]["obs_fatigue"]
                self.assertNotEqual(measured, session_world.players[pid].fatigue)
                self.assertEqual(measured, session_world.coaches[event["actor"]].evaluations[pid].fatigue_estimate)
                kinds.add(event["kind"])
                nonidentical.add(event["kind"])
        expected = {"player_self_observation", "player_fatigue_observation",
                    "coach_session_observation", "coach_scout_observation", "coach_match_observation"}
        self.assertEqual(kinds, expected)
        self.assertEqual(nonidentical, expected)

    def test_measurement_boundary_can_be_fixed_without_exposing_truth(self):
        world = le.run_league_world(seed=100, days=0)
        with patch.object(le, "observe_fatigue", return_value=0.4321) as measure:
            le.advance_league_days(world, 7)
        self.assertGreater(measure.call_count, 12)
        for mind in world.player_minds.values():
            self.assertEqual(mind.perceived_fatigue, 0.4321)
        for coach in world.coaches.values():
            for evaluation in coach.evaluations.values():
                if evaluation.last_seen_day > 0:
                    self.assertEqual(evaluation.fatigue_estimate, 0.4321)
        lineup = le.decide_club_lineup(coach=world.coaches["c1"], roster=world.club_rosters["club_a"], config=world.config)
        for player in world.players.values():
            player.fatigue = 0.95
        self.assertEqual(lineup, le.decide_club_lineup(coach=world.coaches["c1"],
                         roster=world.club_rosters["club_a"], config=world.config))


class TestActualClaimAdmission(unittest.TestCase):
    def setUp(self):
        # A focused rule fixture, not a persistable simulated history.
        self.world = le.run_league_world(seed=1, days=0)
        w = self.world
        w.day = 35
        w.club_rosters["club_a"].remove("p3")
        w.club_rosters["club_b"].append("p3")
        w.players["p3"].club_id = "club_b"
        w.players["p3"].active_transfer_id = "tr1"
        w.ledger.transfer_contracts["tr1"] = le.TransferContractRecord(
            transfer_id="tr1", day=28, player_id="p3", origin_club_id="club_a",
            from_club="club_a", to_club="club_b", base_fee=9.5, solidarity_grant=2.5,
            dividend_per_start=1.25, max_dividend_triggers=6)
        ref = le._append_event(w, day=35, kind="league_match_completed", actor="world",
                              data={"match_id": "m35", "home_club": "club_b", "away_club": "club_c",
                                    "home_starters": ["p3", "p4"], "away_starters": ["p7", "p8"]},
                              cause_refs=["e1"])
        self.request = dict(transfer_id="tr1", match_id="m35", claimant_club="club_a", match_event_ref=ref)

    def financial_state(self):
        w = self.world
        return copy.deepcopy((w.club_accounts, w.ledger.transfer_contracts, w.ledger.claimed_dividend_keys))

    def test_same_request_submitted_twice_pays_once_and_consumes_one_trigger(self):
        first = le.process_dividend_claim(self.world, **self.request)
        after = self.financial_state()
        second = le.process_dividend_claim(self.world, **self.request)
        self.assertTrue(first["accepted"])
        self.assertEqual(second["reason"], "duplicate")
        self.assertEqual(after, self.financial_state())
        self.assertEqual(self.world.ledger.transfer_contracts["tr1"].dividend_triggers_used, 1)
        self.assertEqual(self.world.ledger.blocked_duplicate_claims, 1)
        self.assertEqual(len([e for e in self.world.events if e["kind"] == "dividend_claim_submitted"]), 2)
        payments = [e for e in self.world.events if e["kind"] == "ledger_transaction"]
        self.assertEqual(len(payments), 1)
        self.assertEqual(payments[0]["data"]["amount"], 1.25)
        self.assertEqual(le.compute_ledger_discrepancy(self.world), 0)

    def test_rejected_requests_do_not_change_financial_state(self):
        for change, reason in (({"match_id": "guessed"}, "invalid_match"),
                               ({"match_event_ref": "e999"}, "invalid_match"),
                               ({"transfer_id": "fake"}, "unknown_contract"),
                               ({"claimant_club": "club_c"}, "wrong_claimant")):
            with self.subTest(reason=reason):
                before = self.financial_state()
                result = le.process_dividend_claim(self.world, **{**self.request, **change})
                self.assertEqual(result["reason"], reason)
                self.assertEqual(before, self.financial_state())

    def test_budget_and_cap_rejections_allow_no_payment(self):
        w = self.world
        for condition in ("insufficient_cash", "trigger_limit", "inactive_contract", "not_a_starter"):
            original = copy.deepcopy(w)
            if condition == "insufficient_cash":
                w.club_accounts["club_b"].cash = 1.24
            elif condition == "trigger_limit":
                w.ledger.transfer_contracts["tr1"].dividend_triggers_used = 6
            elif condition == "inactive_contract":
                w.players["p3"].active_transfer_id = None
            else:
                w.events[1]["data"]["home_starters"] = ["p4", "p5"]
            before = self.financial_state()
            self.assertEqual(le.process_dividend_claim(w, **self.request)["reason"], condition)
            self.assertEqual(before, self.financial_state())
            self.world = w = original

    def test_duplicate_metric_is_derived_from_paid_transactions(self):
        le.process_dividend_claim(self.world, **self.request)
        self.assertEqual(le.actual_duplicate_payments(self.world), 0)
        payment = next(e for e in self.world.events if e["kind"] == "ledger_transaction")
        self.world.events.append(copy.deepcopy(payment))
        self.assertEqual(le.actual_duplicate_payments(self.world), 1)

    def test_invalid_ledger_arguments_are_atomic(self):
        for change in ({"payee": "missing"}, {"payer": "missing"}, {"amount": float("nan")},
                       {"amount": float("inf")}, {"amount": True}, {"amount": -0.0000001}):
            args = dict(day=35, payer="club_a", payee="club_b", amount=1.0,
                        reason="transfer_base_fee", cause_refs=["e1"])
            before = copy.deepcopy(self.world)
            with self.assertRaises(ValueError):
                le._record_ledger_tx(self.world, **{**args, **change})
            self.assertEqual(before, self.world)


class TestIndividualTransferPolicies(unittest.TestCase):
    def test_private_minds_stay_with_their_owners(self):
        world = le.run_league_world(seed=1, days=0)
        for policy in (le.decide_transfer_release, le.decide_transfer_acquisition):
            self.assertEqual(set(inspect.signature(policy).parameters), {"coach", "roster", "player_id", "config"})
        args = dict(coach=world.coaches["c1"], roster=world.club_rosters["club_a"], player_id="p3", config=world.config)
        release = le.decide_transfer_release(**args)
        args2 = dict(coach=world.coaches["c2"], roster=world.club_rosters["club_b"], player_id="p3", config=world.config)
        purchase = le.decide_transfer_acquisition(**args2)
        original = copy.deepcopy(world)
        world.coaches["c3"].evaluations["p3"].skill_estimate = 0.98
        world.player_minds["p3"].self_estimate = 0.1
        world.players["p3"].fatigue = 0.9
        self.assertEqual(release, le.decide_transfer_release(**args))
        self.assertEqual(purchase, le.decide_transfer_acquisition(**args2))
        self.assertEqual(world.coaches["c1"], original.coaches["c1"])
        self.assertEqual(world.coaches["c2"], original.coaches["c2"])
        world.coaches["c2"].evaluations["p3"].skill_estimate = 0.98
        self.assertNotEqual(purchase, le.decide_transfer_acquisition(**args2))

    def test_circular_offer_is_a_real_admission_request(self):
        args = dict(from_club="club_b", to_club="club_a", previous_clubs=[],
                    roster_size=2, cash=100.0, required_cash=20.0)
        self.assertTrue(le.resolve_transfer_offer_request(**args)["accepted"])
        self.assertEqual(le.resolve_transfer_offer_request(**{**args, "previous_clubs": ["club_a"]})["reason"],
                         "circular_transfer")
        world = le.run_league_world(seed=1, days=0, config=le.config_for_condition("exploit_churn_guard"))
        world.players["p3"].previous_clubs = ["club_b"]
        with patch.object(le, "resolve_transfer_offer_request", wraps=le.resolve_transfer_offer_request) as admission:
            le._step_transfer_window(world, 28)
        self.assertTrue(any(c.kwargs["to_club"] == "club_b" and c.kwargs["previous_clubs"] == ["club_b"]
                            for c in admission.call_args_list))
        requests = {e["id"]: e for e in world.events if e["kind"] == "transfer_offer_requested"}
        rejected = [e for e in world.events if e["kind"] == "transfer_offer_resolved"
                    and e["data"]["reason"] == "circular_transfer"]
        self.assertGreater(len(rejected), 0)
        self.assertEqual(len(rejected), world.ledger.blocked_circular_transfers)
        for event in rejected:
            self.assertIn(event["cause_refs"][0], requests)


if __name__ == "__main__":
    unittest.main()
