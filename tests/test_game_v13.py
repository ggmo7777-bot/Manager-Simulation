"""game-v13: practice weeks give feedback: a suggested advice target, and a per-season log of the player's own steps."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT)]
from game.session import GameSession

HTML = (ROOT / "game" / "static" / "index.html").read_text()


class PracticeFeedbackTest(unittest.TestCase):
    def test_page_has_season_log_and_resets_it(self):
        self.assertIn('id="seasonLog"', HTML)
        self.assertIn("function leastKnownResponder", HTML)
        self.assertEqual(HTML.count("resetSeasonLog()"), 4)

    def test_advice_result_carries_what_the_log_prints(self):
        session = GameSession(7)
        player = session.state()["squad"][0]["player_id"]
        result = session.train({"player_id": player, "method": "A"})
        kinds = {e["kind"] for e in result["your_events"]}
        self.assertIn("coach_advice_outcome", kinds)

    def test_before_any_advice_every_player_has_no_receptiveness_estimate(self):
        self.assertTrue(all(p["receptiveness"] is None for p in GameSession(7).state()["squad"]))


if __name__ == "__main__":
    unittest.main()
