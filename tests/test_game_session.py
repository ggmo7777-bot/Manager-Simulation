"""Game session: human input drives the research step functions; seeded and deterministic; server round trip."""
import json
from pathlib import Path
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib import request
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from game.server import Game, make_handler
from game.session import GameSession, InvalidInput
from soccer_sim import league_economy as le


def first_week(session, advice=None, starters=None):
    session.train(advice)
    return session.play_match(starters or session.state()["recommended_starters"])


class TestHumanInput(unittest.TestCase):
    def test_chosen_lineup_is_played_and_recorded(self):
        session = GameSession(7)
        session.train(None)
        roster = session.state()["squad"]
        picked = [roster[2]["player_id"], roster[1]["player_id"]]
        result = session.play_match(picked)
        lineup = next(e for e in result["events"] if e["kind"] == "lineup_decision")
        self.assertEqual(lineup["starters"], picked)
        match = next(e for e in result["events"] if e["kind"] == "league_match_completed")
        own = match["home_starters"] if match["home_club"] == "club_a" else match["away_starters"]
        self.assertEqual(own, picked)

    def test_advice_becomes_a_coach_proposal_with_a_stated_reason(self):
        session = GameSession(7)
        pid = session.state()["squad"][0]["player_id"]
        result = session.train({"player_id": pid, "method": "B"})
        given = [e for e in result["events"] if e["kind"] == "advice_given" and e["day"] == 1]
        self.assertEqual((given[0]["player_id"], given[0]["method"]), (pid, "B"))  # slot 1 is the human; slot 2 stays automatic
        outcome = next(e for e in result["events"] if e["kind"] == "coach_advice_outcome" and e["day"] == 1)
        self.assertEqual(outcome["label"], "本人の理由")
        self.assertIn(outcome["stated_reason"], ("trusts_coach", "distrusts_coach", "personal"))

    def test_research_functions_are_restored_after_a_step(self):
        before = (le.decide_club_lineup, le.decide_coach_training)
        first_week(GameSession(7), {"player_id": "p1", "method": "A"})
        self.assertEqual((le.decide_club_lineup, le.decide_coach_training), before)

    def test_invalid_input_is_rejected_without_advancing(self):
        session = GameSession(7)
        for bad in ({"player_id": "p9", "method": "A"}, {"player_id": "p1", "method": "Z"}, "p1"):
            with self.assertRaises(InvalidInput):
                session.train(bad)
        self.assertEqual(session.world.day, 0)
        with self.assertRaises(InvalidInput):
            session.play_match(["p1", "p2"])  # wrong phase
        session.train(None)
        for bad in (["p1"], ["p1", "p1"], ["p1", "p4"], "p1p2", None):
            with self.assertRaises(InvalidInput):
                session.play_match(bad)
        self.assertEqual(session.world.day, 6)

    def test_same_seed_and_input_is_deterministic_and_input_matters(self):
        a, b, c = GameSession(7), GameSession(7), GameSession(7)
        advice = {"player_id": "p1", "method": "A"}
        first_week(a, advice), first_week(b, advice), first_week(c, None)
        self.assertEqual(a.world.events, b.world.events)
        self.assertNotEqual(a.world.events, c.world.events)


class TestServerRoundTrip(unittest.TestCase):
    def test_play_a_season_over_http(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(Game(7)))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_port}"

        def call(path, body=None):
            data = None if body is None else json.dumps(body).encode()
            with request.urlopen(request.Request(base + path, data=data), timeout=60) as response:
                return json.loads(response.read())

        try:
            with request.urlopen(base + "/") as page:
                self.assertIn("<html", page.read().decode())
            state = call("/api/state")
            with self.assertRaises(HTTPError) as locked:
                call("/api/reveal")
            self.assertEqual(locked.exception.code, 400)
            while state["phase"] != "season_end":
                if state["phase"] == "training":
                    state = call("/api/train", {"advice": None})["state"]
                else:
                    state = call("/api/lineup", {"starters": state["recommended_starters"]})["state"]
            reveal = call("/api/reveal")
            self.assertTrue(reveal["players"])
            self.assertEqual(state["standings"][0]["played"], 6)
        finally:
            server.shutdown()


if __name__ == "__main__":
    unittest.main()
