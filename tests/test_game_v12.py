"""game-v12: the training card carries the scouting choice (it was only on the transfer tab) and both stay in sync."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT)]
from game.session import GameSession


class ScoutInTrainingCardTest(unittest.TestCase):
    def test_page_has_scout_select_in_the_training_card(self):
        html = (ROOT / "game" / "static" / "index.html").read_text()
        self.assertIn('id="scoutTarget"', html)
        self.assertIn("plan.scout = $(\"scoutTarget\").value || null", html)
        self.assertNotIn("偵察は「移籍」タブで選ぶ", html)

    def test_scoutable_targets_are_listed_in_state_for_the_select(self):
        state = GameSession(7).state()
        scoutable = [t["player_id"] for t in state["targets"] if t["scoutable"]]
        self.assertTrue(scoutable)
        result = GameSession(7).train(None, scoutable[0])
        self.assertTrue(any(e["kind"] == "coach_scout_observation" for e in result["events"]))


if __name__ == "__main__":
    unittest.main()
