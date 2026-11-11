"""Multi-season play: generations carry the same world forward, resume is exact, and truth stays out of every view."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from game.server import Game
from game.session import GameSession, InvalidInput
from soccer_sim import league_economy as le
from test_game_leak import leaks, true_values

SEED = 7  # retirements fall in all four seasons (age_spread staggers them)
SEASONS = 4


def play_season(session, collect=lambda payload: None):
    """Play to the season's end, then (like the UI) start the next season only when asked by the caller."""
    while session.phase != "season_end":
        if session.phase == "training":
            result = session.train({"player_id": session.world.club_rosters["club_a"][0], "method": "A"})
        else:
            result = session.play_match(session.state()["recommended_starters"])
        collect(result)


def play_seasons(session, seasons, collect=lambda payload: None):
    for index in range(seasons):
        play_season(session, collect)
        if index < seasons - 1:
            collect(session.next_season())


def comparable(session):
    return (le._snapshot_payload_unchecked(session.world), session.history, session.reveals, session.state())


class TestGenerations(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = GameSession(SEED)
        cls.leaks = []
        cls.states_before_turnover = []

        def collect(payload):
            cls.leaks.extend(leaks(json.loads(json.dumps(payload)), true_values(cls.session.world)))
        collect(cls.session.state())
        play_seasons(cls.session, SEASONS, collect)
        collect({"history": cls.session.history, **cls.session.save_payload()})

    def test_the_same_world_continues_with_aging_and_turnover(self):
        session = self.session
        self.assertEqual((session.season, session.world.day), (SEASONS, SEASONS * 42))
        self.assertEqual([h["season"] for h in session.history], [1, 2, 3, 4])
        retired = [r for h in session.history for r in h["retired"]]
        joined = [j for h in session.history for j in h["joined"]]
        self.assertTrue(retired and joined)
        squad = {row["player_id"]: row for row in session.state()["squad"]}
        self.assertFalse({r["player_id"] for r in retired} & set(squad))
        for newcomer in (j["player_id"] for j in session.history[-1]["joined"]):
            self.assertEqual(squad[newcomer]["observation_count"], 0)
            self.assertEqual(squad[newcomer]["skill_estimate"], le.YOUTH_PRIOR[0])  # unknown: the coach's constant first impression

    def test_retirements_are_staggered_over_seasons(self):
        world = self.session.world
        seasons = {e["data"]["season"] for e in world.events if e["kind"] == "season_retired"}
        self.assertGreaterEqual(len(seasons), 3)  # not one cohort retiring together
        first_ages = next(e for e in world.events if e["kind"] == "season_aged")["data"]["ages"]
        self.assertGreater(len(set(first_ages.values())), 2)  # the founders do not share one age

    def test_estimates_carry_over_the_season_break_unchanged(self):
        session = GameSession(SEED)
        play_season(session)
        before = session.state()["squad"]
        after = session.next_season()["state"]
        self.assertEqual(after["squad"], before)
        self.assertEqual((after["season"], after["day"], after["phase"]), (2, 0, "training"))
        self.assertTrue(all(row["points"] == 0 and row["played"] == 0 for row in after["standings"]))

    def test_season_boundary_is_guarded(self):
        session = GameSession(SEED)
        with self.assertRaises(InvalidInput):
            session.next_season()
        play_season(session)
        session.next_season()
        with self.assertRaises(InvalidInput):
            session.reveal()  # the new season has not ended
        with self.assertRaises(InvalidInput):
            session.reveal(2)
        self.assertEqual(session.reveal(1)["season"], 1)

    def test_no_true_value_reaches_any_view_result_history_or_save(self):
        self.assertEqual(self.leaks, [])

    def test_retired_players_true_values_appear_only_in_that_seasons_reveal(self):
        session = self.session
        season = next(h for h in reversed(session.history) if h["retired"])  # own-club retirements are staggered across seasons
        retired = {r["player_id"] for r in season["retired"]}
        revealed = {p["player_id"]: p for p in session.reveal(season["season"])["players"]}
        self.assertTrue(retired <= set(revealed))
        hidden = true_values(session.world)
        # the reveal (true values just before the season-end aging) is the one place they show up; every other view, the history and the save are clean
        self.assertEqual(leaks(json.loads(json.dumps([session.state(), session.history, session.save_payload()])), hidden), [])
        self.assertNotEqual(leaks(json.loads(json.dumps(session.reveal(season["season"]))), hidden), [])

    def test_reveal_of_a_finished_season_stays_available_in_later_seasons(self):
        session = GameSession(SEED)
        play_seasons(session, 2)
        first = session.reveal(1)
        self.assertEqual(session.reveal(2)["season"], 2)
        self.assertEqual(first, session.reveals[1] | {"season": 1})


class TestSeasonSave(unittest.TestCase):
    def test_resumed_multi_season_equals_uninterrupted(self):
        interrupted = GameSession(SEED)
        play_seasons(interrupted, 2)
        interrupted.next_season()
        for _ in range(2):  # two weeks into season 3
            interrupted.train(None)
            interrupted.play_match(interrupted.state()["recommended_starters"])
        resumed = GameSession.resume(json.loads(json.dumps(interrupted.save_payload())))
        self.assertEqual(comparable(resumed), comparable(interrupted))
        play_season(resumed)
        uninterrupted = GameSession(SEED)
        play_seasons(uninterrupted, 2)
        uninterrupted.next_season()
        for _ in range(2):
            uninterrupted.train(None)
            uninterrupted.play_match(uninterrupted.state()["recommended_starters"])
        play_season(uninterrupted)
        self.assertEqual(comparable(resumed), comparable(uninterrupted))
        self.assertEqual(resumed.history[-1]["retired"], uninterrupted.history[-1]["retired"])

    def test_save_at_a_season_break_has_the_marker_and_rejects_tampering(self):
        session = GameSession(SEED)
        play_season(session)
        session.next_season()
        good = json.loads(json.dumps(session.save_payload()))
        self.assertEqual(good["inputs"][-1], {"kind": "next_season"})
        self.assertEqual(GameSession.resume(good).season, 2)
        dropped = {**good, "inputs": good["inputs"][:-1]}
        with self.assertRaises(InvalidInput):
            GameSession.resume(dropped)  # the view no longer matches
        early = {**good, "inputs": [{"kind": "next_season"}] + good["inputs"]}
        with self.assertRaises(InvalidInput):
            GameSession.resume(early)  # a season cannot be skipped
        with self.assertRaises(InvalidInput):
            GameSession.resume({**good, "version": 1})


class TestSeasonApi(unittest.TestCase):
    def test_next_season_and_past_reveal_over_the_game_api(self):
        with tempfile.TemporaryDirectory() as directory:
            game = Game(SEED, save_dir=directory)
            while game.session.phase != "season_end":
                if game.session.phase == "training":
                    game.handle("POST", "/api/train", {"advice": None})
                else:
                    game.handle("POST", "/api/lineup", {"starters": game.session.state()["recommended_starters"]})
            state = game.handle("POST", "/api/next_season", {})["state"]
            self.assertEqual((state["season"], len(state["history"])), (2, 1))
            self.assertEqual(game.handle("GET", "/api/reveal/1", {})["season"], 1)
            game.handle("POST", "/api/save", {"name": "s2"})
            self.assertEqual(game.handle("GET", "/api/saves", {})["saves"][0]["season"], 2)
            self.assertEqual(game.handle("POST", "/api/load", {"name": "s2"})["season"], 2)


if __name__ == "__main__":
    unittest.main()
