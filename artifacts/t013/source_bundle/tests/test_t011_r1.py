"""Regression tests for the three defects independently reproduced in T011."""
import copy
from dataclasses import FrozenInstanceError, asdict, replace
import json
from pathlib import Path
import tempfile
import unittest

from soccer_sim import league_economy as le


class TestLeagueEconomicMechanism(unittest.TestCase):
    def test_receipt_history_alone_does_not_change_training(self):
        plain = le.run_league_world(seed=100, days=0)
        received = copy.deepcopy(plain)
        # An in-memory one-step intervention, not a persistable branch.
        for cid in le.ACADEMY_CLUB_IDS:
            received.club_accounts[cid].cumulative_solidarity_received = 20.0
        le.advance_league_days(plain, 1)
        le.advance_league_days(received, 1)
        self.assertEqual(plain.players, received.players)
        self.assertEqual(plain.player_minds, received.player_minds)
        self.assertEqual(plain.events, received.events)
        for cid in le.CLUB_IDS:
            self.assertEqual(plain.club_accounts[cid].cash, received.club_accounts[cid].cash)

    def test_facility_payment_mediates_training_gain(self):
        funded = le.run_league_world(seed=100, days=0)
        unfunded = copy.deepcopy(funded)
        unfunded.club_accounts["club_a"].cash = 16.5
        starting_skill = funded.players["p1"].composite_skill()
        le.advance_league_days(funded, 1)
        le.advance_league_days(unfunded, 1)
        self.assertEqual(funded.club_accounts["club_a"].facility_active_days, 1)
        self.assertEqual(unfunded.club_accounts["club_a"].facility_active_days, 0)
        self.assertEqual(funded.club_accounts["club_a"].cumulative_facility_paid, 0.28)
        self.assertEqual(unfunded.club_accounts["club_a"].cumulative_facility_paid, 0.0)
        decisions = lambda w: [(e["actor"], e["data"]["chosen"]) for e in w.events
                               if e["kind"] == "player_training_decision"]
        self.assertEqual(decisions(funded), decisions(unfunded))
        self.assertGreater(funded.players["p1"].composite_skill(), unfunded.players["p1"].composite_skill())
        self.assertGreater(unfunded.players["p1"].composite_skill(), starting_skill)

    def test_removed_bonus_cannot_be_loaded_as_config(self):
        data = le.LeagueEconomyConfig().to_dict()
        data["solidarity_curriculum_bonus"] = 0.38
        with self.assertRaises(ValueError):
            le.LeagueEconomyConfig.from_dict(data)


class TestLeagueOfferBoundary(unittest.TestCase):
    def setUp(self):
        self.world = le.run_league_world(seed=17, days=0)
        self.mind = self.world.player_minds["p3"]
        self.mind.playing_time_satisfaction = 0.15
        self.mind.trust_by_coach["c2"] = self.mind.trust_by_coach["c4"] = 0.7
        self.offers = (le.TransferOffer("p3", "club_b", "c2"), le.TransferOffer("p3", "club_d", "c4"))

    def decide(self):
        w = self.world
        return le.decide_transfer_bidding(
            player_id="p3", from_club="club_a", from_roster=w.club_rosters["club_a"],
            previous_clubs=[], bench_streak=4, player_mind=self.mind, coaches=w.coaches,
            club_rosters=w.club_rosters, club_cashes={cid: a.cash for cid, a in w.club_accounts.items()},
            config=w.config,
        )

    def test_uncommunicated_coach_score_cannot_break_player_tie(self):
        self.world.coaches["c2"].evaluations["p3"].skill_estimate = 0.9
        self.world.coaches["c4"].evaluations["p3"].skill_estimate = 0.8
        before = self.decide()
        self.world.coaches["c4"].evaluations["p3"].skill_estimate = 0.98
        after = self.decide()
        self.assertNotEqual(before["club_offers"], after["club_offers"])
        self.assertEqual(before["player_choice"], after["player_choice"])
        self.assertEqual(before["chosen_to_club"], "club_b")
        self.assertEqual(after["chosen_to_club"], "club_b")

    def test_offer_order_does_not_change_choice_and_payload_is_immutable(self):
        before = copy.deepcopy(self.mind)
        a = le.choose_player_transfer_offer(mind=self.mind, offers=self.offers)
        b = le.choose_player_transfer_offer(mind=self.mind, offers=self.offers[::-1])
        self.assertEqual(a, b)
        self.assertEqual(self.mind, before)
        self.assertEqual(set(asdict(self.offers[0])), {"player_id", "to_club", "to_coach"})
        with self.assertRaises(FrozenInstanceError):
            self.offers[0].to_club = "club_d"

    def test_own_trust_and_offer_availability_affect_choice(self):
        self.mind.trust_by_coach["c4"] = 0.9
        self.assertEqual(le.choose_player_transfer_offer(mind=self.mind, offers=self.offers)["chosen_to_club"], "club_d")
        self.assertEqual(le.choose_player_transfer_offer(mind=self.mind, offers=self.offers[:1])["chosen_to_club"], "club_b")
        self.assertIsNone(le.choose_player_transfer_offer(mind=self.mind, offers=())["chosen_to_club"])

    def test_wrong_recipient_duplicate_and_impersonated_offers_rejected(self):
        for offers in ((le.TransferOffer("p2", "club_b", "c2"),), self.offers * 2):
            with self.assertRaises(ValueError):
                le.choose_player_transfer_offer(mind=self.mind, offers=offers)
        with self.assertRaises(ValueError):
            le.TransferOffer("p3", "club_b", "c4")

    def test_received_messages_are_logged_before_player_choice(self):
        world = le.run_league_world(seed=100, days=28)
        events = {e["id"]: e for e in world.events}
        choices = [e for e in world.events if e["kind"] == "player_transfer_choice"]
        self.assertEqual(len(choices), 12)
        received_count = 0
        for choice in choices:
            received = [events[r] for r in choice["cause_refs"] if r in events
                        and events[r]["kind"] == "transfer_offer_received"]
            self.assertEqual([e["data"]["offer"] for e in received], choice["data"]["received_offers"])
            for event in received:
                received_count += 1
                self.assertEqual(event["data"]["recipient"], choice["actor"])
                self.assertEqual(event["actor"], event["data"]["offer"]["to_coach"])
                self.assertLess(int(event["id"][1:]), int(choice["id"][1:]))
            self.assertNotIn("offer_score", json.dumps(choice["data"]))
        self.assertGreater(received_count, 0)


class TestLeagueCheckpointIntegrity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.world = le.run_league_world(seed=100, days=28)
        cls.payload = le.snapshot_payload(cls.world)

    def assert_rejected(self, payload):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snapshot.json"
            path.write_text(json.dumps(payload))
            with self.assertRaises(ValueError):
                le.load_league_snapshot(path)

    def test_original_four_reader_regressions(self):
        mutations = [
            lambda p: p["players"]["p1"].update(fatigue=-100),
            lambda p: p["player_minds"]["p1"].update(last_obs_ref="e_missing"),
            lambda p: p.update(events=[]),
            lambda p: (p["club_accounts"]["club_a"].update(cash=p["club_accounts"]["club_a"]["cash"] + 10),
                       p["club_accounts"]["club_b"].update(cash=p["club_accounts"]["club_b"]["cash"] - 10)),
        ]
        for i, mutate in enumerate(mutations):
            with self.subTest(i=i):
                p = copy.deepcopy(self.payload)
                mutate(p)
                self.assert_rejected(p)

    def test_valid_range_changes_and_wrong_memory_owner_rejected(self):
        other_ref = next(e["id"] for e in self.payload["events"]
                         if e["actor"] == "p2" and e["kind"] == "player_self_observation")
        mutations = [
            lambda p: p["player_minds"]["p1"].update(last_obs_ref=other_ref),
            lambda p: p["coaches"]["c1"]["evaluations"]["p1"].update(last_obs_ref=other_ref),
            lambda p: p["players"]["p1"].update(fatigue=0.123456),
            lambda p: p["player_minds"]["p1"]["trust_by_coach"].update(c1=0.123456),
            lambda p: p["players"]["p1"]["rates"].update(A=0.1),
            lambda p: p["initial_players"]["p1"].update(cap=0.99),
            lambda p: p["club_accounts"]["club_a"].update(points=123),
            lambda p: p["ledger"].update(cumulative_external_inflow=123.0),
            lambda p: p["ledger"]["transfer_contracts"].clear(),
        ]
        self.assertTrue(self.payload["ledger"]["transfer_contracts"])
        for i, mutate in enumerate(mutations):
            with self.subTest(i=i):
                p = copy.deepcopy(self.payload); mutate(p); self.assert_rejected(p)

    def test_history_edits_missing_boundary_and_accounting_forgery_rejected(self):
        p = copy.deepcopy(self.payload)
        p["events"] = [e for e in p["events"] if e["day"] < 28]
        self.assert_rejected(p)
        p = copy.deepcopy(self.payload)
        tx = next(e for e in p["events"] if e["kind"] == "ledger_transaction")
        tx["data"]["amount"] += 0.1
        # Even correcting totals to agree with a forged transaction is insufficient.
        p["club_accounts"][tx["data"]["payer"]]["cash"] -= 0.1
        p["ledger"]["cumulative_external_outflow"] += 0.1
        self.assert_rejected(p)
        p = copy.deepcopy(self.payload)
        choice = next(e for e in p["events"] if e["kind"] == "player_transfer_choice")
        choice["data"]["tie_rule"] = "private_score"
        self.assert_rejected(p)

    def test_strict_types_ranges_nonfinite_and_fields_rejected(self):
        mutations = [
            lambda p: p.update(day=True), lambda p: p.update(seed="100"),
            lambda p: p["config"].update(match_ticks=True),
            lambda p: p["config"].update(player_gain_alpha=2.0),
            lambda p: p["config"].update(facility_daily_cost=-1.0),
            lambda p: p["config"].update(self_noise=float("nan")),
            lambda p: p["players"]["p1"].update(fatigue=float("inf")),
            lambda p: p["players"]["p1"].update(appearances=True),
            lambda p: p["players"]["p1"].update(cap="0.9"),
            lambda p: p.update(extra_field=0),
            lambda p: p["players"]["p1"].update(extra_field=0),
            lambda p: p.pop("coaches"),
            lambda p: p["identity"].update(save=11),
        ]
        for i, mutate in enumerate(mutations):
            with self.subTest(i=i):
                p = copy.deepcopy(self.payload); mutate(p); self.assert_rejected(p)

    def test_duplicate_json_keys_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snapshot.json"
            path.write_text('{"day":0,' + json.dumps(self.payload)[1:])
            with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
                le.load_league_snapshot(path)

    def test_invalid_world_cannot_overwrite_valid_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "valid"
            le.save_league_artifacts(self.world, out)
            before = {p.name: p.read_bytes() for p in out.iterdir()}
            for mutate in (lambda w: setattr(w.players["p1"], "fatigue", -100),
                           lambda w: w.events.clear(),
                           lambda w: setattr(w.player_minds["p1"], "last_obs_ref", "e_missing"),
                           lambda w: (setattr(w.club_accounts["club_a"], "cash", w.club_accounts["club_a"].cash + 10),
                                      setattr(w.club_accounts["club_b"], "cash", w.club_accounts["club_b"].cash - 10))):
                w = copy.deepcopy(self.world); mutate(w)
                with self.assertRaises(ValueError):
                    le.save_league_artifacts(w, out)
                self.assertEqual(before, {p.name: p.read_bytes() for p in out.iterdir()})

    def test_day_zero_and_custom_config_resume(self):
        config = replace(le.LeagueEconomyConfig(), condition="custom-probe", self_noise=0.0, facility_daily_cost=0.29)
        with tempfile.TemporaryDirectory() as tmp:
            le.save_league_artifacts(le.run_league_world(seed=0, days=0, config=config), tmp)
            loaded = le.load_league_snapshot(Path(tmp) / "snapshot.json")
            le.advance_league_days(loaded, 8)
            self.assertEqual(loaded, le.run_league_world(seed=0, days=8, config=config))


if __name__ == "__main__":
    unittest.main()
