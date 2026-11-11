"""game-v10: a declined bid leaves a readable reason on the target row, and the pre-bid openness cue is public-only."""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from game import observation as obs
from game.session import GameSession
from test_game_leak import leaks, true_values
from test_game_transfer import to_window

SEEDS = range(1, 21)


def session_after_declines():
    """First seed whose window bids (the two first targets) draw a decline; returns (session, declined player ids)."""
    for seed in SEEDS:
        session = to_window(GameSession(seed))
        targets = [t["player_id"] for t in session.state()["targets"]][:2]
        result = session.play_match(session.state()["recommended_starters"], targets)
        declined = [e["player_id"] for e in result["events"] if e["kind"] == "transfer_response" and e["response"] == "declined"]
        if declined:
            return session, declined
    raise AssertionError("no seed produced a decline")


class TestDeclinedBidReason(unittest.TestCase):
    def test_decline_keeps_the_players_words_on_the_target_row_without_hidden_values(self):
        session, declined = session_after_declines()
        view = json.loads(json.dumps(session.state()))
        rows = {t["player_id"]: t for t in view["targets"]}
        for pid in declined:
            record = rows[pid]["bid_record"]
            self.assertEqual((record["asked"], record["declined"], record["last_response"]), (1, 1, "declined"))
            self.assertIn(record["last_stated_reason"], ("not_looking", "terms_unattractive", "chose_other_club"))
        self.assertEqual(leaks(view, true_values(session.world)), [])

    def test_bench_streak_is_the_public_lineup_count_of_the_research_counter(self):
        session = GameSession(7)
        for _ in range(30):  # across season ends: newcomers and transferred players start from zero
            if session.phase == "season_end":
                session.next_season()
            session.train(None) if session.phase == "training" else session.play_match(session.state()["recommended_starters"])
            for row in session.state()["targets"]:
                self.assertEqual(row["bench_streak"], min(obs.BENCH_STREAK_CAP, session.world.players[row["player_id"]].bench_streak))


if __name__ == "__main__":
    unittest.main()
