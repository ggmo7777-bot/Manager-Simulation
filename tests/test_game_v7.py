"""game-v7: failed steps keep state and input log consistent; your move vs automatic; player names; sale notices; advice record."""
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from game import observation as obs
from game.session import GameSession, SEASON_DAYS, _sha256
from test_game_leak import leaks, true_values, play_season, SEEDS, SEASONS


def play_seasons(session, count, collect=lambda payload: None):
    for season in range(count):
        play_season(session, collect)
        if season < count - 1:
            session.next_season()


class TestFailedStepKeepsConsistency(unittest.TestCase):
    def assert_save_replays(self, session):
        resumed = GameSession.resume(json.loads(json.dumps(session.save_payload())))
        self.assertEqual(_sha256(resumed.state()), _sha256(session.state()))

    def test_a_failure_midway_leaves_the_world_where_the_record_says(self):
        for step in ("train", "play_match"):
            session = GameSession(7)
            if step == "play_match":
                session.train(None)
            before = (session.world.day, list(session.inputs), _sha256(session.state()))
            with mock.patch.object(obs, "observed_events", side_effect=RuntimeError("boom")):
                with self.assertRaises(RuntimeError):
                    session.train(None) if step == "train" else session.play_match(session.state()["recommended_starters"])
            self.assertEqual((session.world.day, session.inputs, _sha256(session.state())), before, step)
            self.assert_save_replays(session)

    def test_the_session_keeps_playing_and_saving_after_a_failure(self):
        session = GameSession(7)
        session.train(None)
        with mock.patch.object(obs, "observed_events", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                session.play_match(session.state()["recommended_starters"])
        play_seasons(session, 1)
        self.assert_save_replays(session)
        self.assertEqual(session.phase, "season_end")

    def test_a_failure_at_the_season_end_does_not_leave_a_half_closed_season(self):
        session = GameSession(7)
        while session.phase != "lineup" or session.day_in_season < SEASON_DAYS - 1:
            session.train(None) if session.phase == "training" else session.play_match(session.state()["recommended_starters"])
        with mock.patch.object(GameSession, "_close_season", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                session.play_match(session.state()["recommended_starters"])
        self.assertEqual((session.phase, session.history, session.reveals), ("lineup", [], {}))
        session.play_match(session.state()["recommended_starters"])
        self.assertEqual(len(session.history), 1)
        self.assert_save_replays(session)


class TestYourMoveAndAutomatic(unittest.TestCase):
    def test_events_split_into_yours_and_automatic_without_loss(self):
        session = GameSession(7)
        pid = session.state()["squad"][0]["player_id"]
        result = session.train({"player_id": pid, "method": "B"})
        self.assertEqual(result["your_move"]["advice"], {"player_id": pid, "method": "B"})
        self.assertEqual([e for e in result["events"] if e in result["your_events"] or e in result["auto_events"]], result["events"])
        self.assertEqual(len(result["your_events"]) + len(result["auto_events"]), len(result["events"]))
        self.assertEqual({e["kind"] for e in result["your_events"]}, {"advice_given", "coach_advice_outcome"})
        self.assertTrue(result["auto_events"])  # the assistant coach's daily sessions are automatic

    def test_lineup_move_records_the_recommendation_beside_the_choice(self):
        session = GameSession(7)
        session.train(None)
        recommended = session.state()["recommended_starters"]
        other = next(p["player_id"] for p in session.state()["squad"] if p["player_id"] not in recommended)
        picked = [recommended[0], other]
        result = session.play_match(picked)
        self.assertEqual((result["your_move"]["starters"], result["your_move"]["recommended_starters"]), (picked, recommended))
        self.assertIn("lineup_decision", {e["kind"] for e in result["your_events"]})
        self.assertNotIn("coach_session_observation", {e["kind"] for e in result["your_events"]})


class TestNamesNoticesAndTrust(unittest.TestCase):
    def test_names_are_deterministic_unique_and_cover_every_player(self):
        first, again, other = GameSession(7), GameSession(7), GameSession(8)
        names = first.state()["names"]
        self.assertEqual(names, again.state()["names"])
        self.assertNotEqual(names, other.state()["names"])
        self.assertEqual(set(names), set(first.world.players))
        self.assertEqual(len(set(names.values())), len(names))

    def test_names_do_not_depend_on_the_play(self):
        played = GameSession(7)
        play_seasons(played, 2)
        self.assertEqual({k: v for k, v in played.state()["names"].items() if k in GameSession(7).world.players}, GameSession(7).state()["names"])

    def test_sales_are_announced_with_the_players_own_wording_and_the_cause_only_in_the_reveal(self):
        sales = []
        for seed in SEEDS:
            session = GameSession(seed)
            play_seasons(session, SEASONS, lambda payload: sales.extend(n for n in payload.get("state", payload)["notices"] if n not in sales))
            for notice in sales:
                self.assertEqual(set(notice), {"label", "kind", "day", "player_id", "to_club", "stated_reason"})
                self.assertEqual((notice["kind"], notice["stated_reason"], notice["label"]), ("player_sold", "wants_move", "本人の理由"))
            if sales:
                self.assertTrue(any(session.reveal(season)["sale_reasons"] for season in range(1, SEASONS + 1)))
                return
        self.fail("no sale of an own player in 20 seeds x 4 seasons")

    def test_new_views_carry_no_true_value_in_many_seeds(self):
        seen = set()
        for seed in SEEDS:
            session = GameSession(seed)
            problems = []
            play_seasons(session, SEASONS, lambda payload: (problems.extend(leaks(json.loads(json.dumps(payload)), true_values(session.world))),
                                                           seen.update(e["kind"] for e in payload.get("your_events", []) + payload.get("auto_events", []))))
            self.assertEqual(problems, [], f"seed {seed}")
        self.assertTrue({"advice_given", "coach_advice_outcome", "lineup_decision", "league_match_completed"} <= seen, seen)

    def test_advice_record_counts_refusals_and_the_last_stated_reason(self):
        session = GameSession(7)
        pid = session.state()["squad"][0]["player_id"]
        for _ in range(3):
            session.train({"player_id": pid, "method": "A"})
            session.play_match(session.state()["recommended_starters"])
        row = next(p for p in session.state()["squad"] if p["player_id"] == pid)["advice_record"]
        self.assertGreaterEqual(row["asked"], 3)  # the assistant coach's automatic advice to him counts too: it is the coach's record
        self.assertLessEqual(row["refused"], row["asked"])
        self.assertIn(row["last_stated_reason"], ("trusts_coach", "distrusts_coach", "personal"))


if __name__ == "__main__":
    unittest.main()
