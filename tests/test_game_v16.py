"""game-v16: the season-end second scoring (four axes, no total) and the persistent world history."""
from pathlib import Path
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from game import session as game_session
from game.session import GameSession
from test_game_leak import play_season

HTML = (ROOT / "game" / "static" / "index.html").read_text()


class SecondScoringTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = GameSession(7)
        play_season(cls.session, lambda payload: None)

    def test_history_carries_the_four_axes_without_a_total(self):
        entry = self.session.state()["history"][0]
        judging = entry["judging"]
        self.assertEqual(judging["total"], entry["squad_size"])
        self.assertLessEqual(judging["hits"], judging["total"])
        self.assertEqual({row["player_id"] for row in entry["lives"]}, {row["player_id"] for row in self.session.reveal()["players"]})
        self.assertTrue({"left", "trust_mean", "rank"} <= set(entry))
        self.assertFalse({"score", "total_score"} & set(entry))

    def test_chronicle_is_rebuilt_by_replay(self):
        before = self.session.chronicle_view()
        self.assertTrue(before["events"])
        self.assertEqual(GameSession.resume(self.session.save_payload()).chronicle_view(), before)

    def test_page_tolerance_matches_the_session(self):
        tolerance = float(re.search(r"const JUDGE_TOLERANCE = ([\d.]+);", HTML).group(1))
        self.assertEqual(tolerance, game_session.JUDGE_TOLERANCE)
        self.assertIn("/api/chronicle", HTML)


if __name__ == "__main__":
    unittest.main()
