"""Human control of scouting and transfer bids: inputs replace only the coach's choice; the research rules do the rest."""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from game import observation as obs
from game.session import GameSession, InvalidInput
from soccer_sim import league_economy as le
from test_game_save import world_state

WINDOW_DAY = 28


def to_window(session):
    """Play weeks with no scouting or bids until the lineup before the in-season day 28 window (by day 14 the club cannot yet pay the fee and reserve)."""
    while not (lambda w: w["open_now"] and w["day"] == WINDOW_DAY)(session.state()["market"]["window"]):
        session.train(None) if session.phase == "training" else session.play_match(session.state()["recommended_starters"])
    return session


class TestScouting(unittest.TestCase):
    def test_chosen_target_is_scouted_on_the_first_day(self):
        session = GameSession(7)
        result = session.train(None, scout="p8")
        scouted = [e for e in result["events"] if e["kind"] == "coach_scout_observation" and e["day"] == 1]
        self.assertEqual([e["player_id"] for e in scouted], ["p8"])
        self.assertEqual(scouted[0]["label"], "観測")
        change = next(c for c in result["estimate_changes"] if c["player_id"] == "p8")
        self.assertNotEqual(change["skill_before"], change["skill_after"])

    def test_scouting_costs_the_research_price(self):
        plain, scouting = GameSession(7), GameSession(7)
        plain.train(None), scouting.train(None, scout="p8")
        cost = scouting.state()["market"]["scout_cost"]
        self.assertEqual(plain.world.club_accounts["club_a"].cash_units - scouting.world.club_accounts["club_a"].cash_units, cost)

    def test_unaffordable_or_unknown_target_is_rejected_without_advancing(self):
        session = GameSession(7)
        for bad in ("p1", "p99", "", 5):  # own player, nobody, empty, wrong type
            with self.assertRaises(InvalidInput):
                session.train(None, scout=bad)
        session.world.club_accounts["club_a"].cash_units = 1  # the research rule offers no scouting without funds
        self.assertFalse(any(t["scoutable"] for t in session.state()["targets"]))
        with self.assertRaises(InvalidInput):
            session.train(None, scout="p8")
        self.assertEqual(session.world.day, 0)

    def test_target_view_is_the_coachs_estimate_not_the_truth(self):
        session = GameSession(7)
        session.train(None, scout="p8")
        target = next(t for t in session.state()["targets"] if t["player_id"] == "p8")
        coach_estimate = session.world.coaches["c1"].evaluations["p8"].skill_estimate
        self.assertEqual(target["skill_estimate"], coach_estimate)
        self.assertNotEqual(target["skill_estimate"], session.world.players["p8"].composite_skill())
        self.assertGreaterEqual(target["observation_count"], 1)


class TestOffers(unittest.TestCase):
    def test_offers_are_refused_outside_a_window_and_for_bad_targets(self):
        session = GameSession(7)
        session.train(None)
        for bad in (["p5"], "p5", ["p1"], ["p99"]):  # no window yet / not a list / own player / nobody
            with self.assertRaises(InvalidInput):
                session.play_match(session.state()["recommended_starters"], bad)
        self.assertEqual(session.world.day, 6)
        to_window(session)
        for bad in (["p5", "p5"], [["p5"]]):
            with self.assertRaises(InvalidInput):
                session.play_match(session.state()["recommended_starters"], bad)

    def test_human_bids_replace_the_coachs_and_nothing_else(self):
        session = to_window(GameSession(7))
        result = session.play_match(session.state()["recommended_starters"], ["p5", "p8"])
        own_requests = [e for e in session.world.events if e["kind"] == "transfer_offer_requested" and e["actor"] == "c1"]
        self.assertEqual(sorted(e["data"]["player_id"] for e in own_requests), ["p5", "p8"])
        resolved = [e for e in result["events"] if e["kind"] == "offer_resolved"]
        self.assertEqual([e["player_id"] for e in resolved], ["p5", "p8"])
        none = to_window(GameSession(7))
        none.play_match(none.state()["recommended_starters"], [])
        self.assertEqual([e for e in none.world.events if e["kind"] == "transfer_offer_requested" and e["actor"] == "c1"], [])

    def test_player_answers_with_a_stated_reason_and_the_reveal_holds_the_cause(self):
        responses = {}
        for seed in range(1, 11):
            session = to_window(GameSession(seed))
            result = session.play_match(session.state()["recommended_starters"], [t["player_id"] for t in session.state()["targets"]])
            for item in result["events"]:
                if item["kind"] == "transfer_response":
                    responses[(seed, item["player_id"])] = item
            while session.phase != "season_end":
                session.train(None) if session.phase == "training" else session.play_match(session.state()["recommended_starters"])
            for row in session.reveal()["transfer_reasons"]:
                item = responses[(seed, row["player_id"])]
                self.assertEqual((row["response"], row["stated_reason"]), (item["response"], item["stated_reason"]))
                self.assertIn("acceptance_score", row["true_cause"])
        self.assertTrue({"accepted", "declined"} <= {r["response"] for r in responses.values()}, "the sweep should see both answers")

    def test_a_completed_bid_brings_the_player_into_the_squad(self):
        for seed in range(1, 21):
            session = to_window(GameSession(seed))
            result = session.play_match(session.state()["recommended_starters"], [t["player_id"] for t in session.state()["targets"]])
            arrivals = [e["player_id"] for e in result["events"] if e["kind"] == "transfer_completed" and e["to_club"] == "club_a"]
            if arrivals:
                self.assertIn(arrivals[0], [p["player_id"] for p in result["state"]["squad"]])
                return
        self.fail("no bid was accepted in 20 seeds")

    def test_required_fee_matches_the_research_record(self):
        session = to_window(GameSession(7))
        session.play_match(session.state()["recommended_starters"], [])
        recorded = next(e for e in session.world.events if e["kind"] == "transfer_window_decision")["data"]["required_upfront_fee_units"]
        self.assertEqual(obs.required_offer_fee(session.world.config), recorded)

    def test_research_functions_are_restored(self):
        before = (le.decide_club_lineup, le.decide_coach_training, le.decide_transfer_acquisition)
        session = to_window(GameSession(7))
        session.play_match(session.state()["recommended_starters"], ["p5"])
        self.assertEqual((le.decide_club_lineup, le.decide_coach_training, le.decide_transfer_acquisition), before)


def play_with_inputs(session, seasons):
    """Every input kind, in a fixed pattern, over several seasons (windows recur in every season)."""
    for season in range(seasons):
        while session.phase != "season_end":
            state = session.state()
            if session.phase == "training":
                scoutable = [t["player_id"] for t in state["targets"] if t["scoutable"]]
                session.train({"player_id": state["squad"][0]["player_id"], "method": "B"}, scoutable[state["week"] % len(scoutable)] if scoutable else None)
            else:
                window = state["market"]["window"]
                session.play_match(state["recommended_starters"], [t["player_id"] for t in state["targets"]][:2] if window and window["open_now"] else [])
        if season < seasons - 1:
            session.next_season()
    return session


class TestReplayWithTransferInputs(unittest.TestCase):
    def test_resume_reproduces_the_world_and_continues_identically(self):
        played = play_with_inputs(GameSession(7), 2)
        payload = json.loads(json.dumps(played.save_payload()))
        self.assertTrue(any(i.get("scout") for i in payload["inputs"]) and any(i.get("offers") for i in payload["inputs"]))
        resumed = GameSession.resume(payload)
        self.assertEqual(resumed.world.events, played.world.events)
        self.assertEqual(world_state(resumed.world), world_state(played.world))
        resumed.next_season(), played.next_season()
        play_with_inputs(resumed, 1), play_with_inputs(played, 1)
        self.assertEqual(world_state(resumed.world), world_state(played.world))
        self.assertEqual(resumed.reveal(1), played.reveal(1))

    def test_changed_scout_or_offers_are_not_replayable(self):
        played = play_with_inputs(GameSession(7), 1)
        good = json.loads(json.dumps(played.save_payload()))
        for field, value in (("scout", "p4"), ("offers", ["p5"])):  # p5: a bid whose outcome differs from the recorded one in the squad_size=4 world
            index = max(i for i, item in enumerate(good["inputs"]) if item.get(field))  # the last one: the earliest bids can fail for lack of cash whatever they name
            bad = json.loads(json.dumps(good))
            bad["inputs"][index][field] = value
            with self.subTest(field), self.assertRaises(InvalidInput):
                GameSession.resume(bad)

    def test_bids_in_season_three_complete_and_replay(self):
        played = play_with_inputs(GameSession(3), 4)
        late = [e for e in played.world.events if e["kind"] == "transfer_completed" and e["data"]["to_club"] == "club_a" and e["day"] > 2 * 42]
        self.assertTrue(late)
        resumed = GameSession.resume(json.loads(json.dumps(played.save_payload())))
        self.assertEqual(world_state(resumed.world), world_state(played.world))

    def test_other_seeds_replay_too(self):
        for seed in (1, 2, 3, 12):
            played = play_with_inputs(GameSession(seed), 2)
            resumed = GameSession.resume(json.loads(json.dumps(played.save_payload())))
            self.assertEqual(world_state(resumed.world), world_state(played.world), f"seed {seed}")


if __name__ == "__main__":
    unittest.main()
