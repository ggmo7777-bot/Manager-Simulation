"""game-v15: the player detail screen reads the coach's own estimate history and the events about one player."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT)]
from game.session import GameSession, InvalidInput
from game.server import Game

HTML = (ROOT / "game" / "static" / "index.html").read_text()


class PlayerDetailTest(unittest.TestCase):
    def test_history_grows_one_point_per_step_and_events_are_labelled(self):
        session = GameSession(7)
        player = session.state()["squad"][0]["player_id"]
        self.assertEqual(len(session.player_view(player)["history"]), 1)
        session.train({"player_id": player, "method": "A"})
        view = session.player_view(player)
        self.assertEqual([p["day"] for p in view["history"]], [0, 6])
        self.assertTrue(all(e["label"] in ("事実", "観測", "本人の理由", "推定") for e in view["events"]))
        self.assertIn("coach_advice_outcome", {e["kind"] for e in view["events"]})

    def test_history_is_rebuilt_by_replay_and_unknown_players_are_refused(self):
        session = GameSession(7)
        session.train(None)
        resumed = GameSession.resume(session.save_payload())
        player = session.state()["squad"][0]["player_id"]
        self.assertEqual(resumed.player_view(player), session.player_view(player))
        with self.assertRaises(InvalidInput):
            session.player_view("p999")

    def test_api_route_and_page(self):
        game = Game(7)
        player = game.session.state()["squad"][0]["player_id"]
        self.assertEqual(game.handle("GET", f"/api/player/{player}", {})["player_id"], player)
        self.assertIn("function renderPlayer", HTML)
        self.assertIn("/api/player/", HTML)


if __name__ == "__main__":
    unittest.main()
