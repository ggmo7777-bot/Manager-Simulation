"""game-v11: a declined bid can be queued for the next window; the rules accept a re-bid and the page offers the queue."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from test_game_v10 import session_after_declines


def advance_to_next_window(session):
    """Play on (training weeks, recommended lineups, next seasons) until the next match day that has a window after it."""
    while True:
        state = session.state()
        window = state["market"]["window"]
        if state["phase"] == "lineup" and window and window["open_now"]:
            return
        if state["phase"] == "training":
            session.train(None)
        elif state["phase"] == "lineup":
            session.play_match(state["recommended_starters"])
        else:
            session.next_season()


class RequeueTest(unittest.TestCase):
    def test_declined_player_can_be_bid_for_again_at_the_next_window(self):
        session, declined = session_after_declines()
        row = next(t for t in session.state()["targets"] if t["player_id"] == declined[0])
        self.assertEqual(row["bid_record"]["last_response"], "declined")
        advance_to_next_window(session)
        result = session.play_match(session.state()["recommended_starters"], [declined[0]])
        resolved = next(e for e in result["events"] if e["kind"] == "offer_resolved" and e["player_id"] == declined[0])
        self.assertNotEqual(resolved.get("reason"), "circular_transfer")

    def test_page_has_the_queue_button_and_clears_it_on_new_game(self):
        html = (ROOT / "game" / "static" / "index.html").read_text()
        self.assertIn("data-requeue", html)
        self.assertEqual(html.count("plan.requeue.clear()"), 2)


if __name__ == "__main__":
    unittest.main()
