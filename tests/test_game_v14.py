"""game-v14: a ホーム tab (progress, status, inbox with reasons) is the landing screen; it reads only fields already visible."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT)]
from game.session import GameSession

HTML = (ROOT / "game" / "static" / "index.html").read_text()


class HomeTabTest(unittest.TestCase):
    def test_home_is_nav_landing_and_rendered(self):
        self.assertIn('data-tab="home"', HTML)
        self.assertIn("function renderHome", HTML)
        self.assertEqual(HTML.count('show("home")'), 4)  # first load, new game, load, next season

    def test_state_has_what_the_home_tab_reads(self):
        state = GameSession(7).state()
        for key in ("day", "season_days", "phase", "week", "notices", "standings", "market"):
            self.assertIn(key, state)
        self.assertIn("window", state["market"])


if __name__ == "__main__":
    unittest.main()
