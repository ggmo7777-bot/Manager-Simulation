"""game-v9 (G6): the game raises only the academy clubs' starting cash through an existing research key; the research default is untouched and a season-1 bid can be afforded."""
from dataclasses import replace
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from game import session
from game.session import GAME_CONFIG, GameSession
from soccer_sim import league_economy as le

RESEARCH_ACADEMY_CASH = 21_000_000
GAME_ACADEMY_CASH = 25_000_000
SEED_WITH_SEASON_ONE_BID = 2  # its first window bid fails at the research cash and passes at the game cash


def first_bid_reasons(seed):
    """Play to the first transfer window, bid for the assistant coach's first pick, return how the market resolved it."""
    game = GameSession(seed)
    while True:
        if game.phase == "training":
            game.train(None)
            continue
        state = game.state()
        window = state["market"]["window"]
        offers = state["recommended_offers"][:1] if window and window["open_now"] else []
        result = game.play_match(state["recommended_starters"], offers)
        if offers:
            return [e["reason"] for e in result["your_events"] if e["kind"] == "offer_resolved"]


class TestGameCash(unittest.TestCase):
    def test_research_default_is_unchanged(self):
        self.assertEqual(le.LeagueEconomyConfig().initial_academy_cash_units, RESEARCH_ACADEMY_CASH)
        self.assertEqual(GAME_CONFIG.initial_academy_cash_units, GAME_ACADEMY_CASH)

    def test_only_academy_clubs_get_more_cash(self):
        world = GameSession(SEED_WITH_SEASON_ONE_BID).world
        for club_id, account in world.club_accounts.items():
            expected = GAME_ACADEMY_CASH if club_id in le.ACADEMY_CLUB_IDS else GAME_CONFIG.initial_buyer_cash_units
            self.assertEqual(account.cash_units, expected)

    def test_a_season_one_bid_is_accepted_only_with_the_game_cash(self):
        self.assertEqual(first_bid_reasons(SEED_WITH_SEASON_ONE_BID), ["accepted"])
        research = replace(GAME_CONFIG, initial_academy_cash_units=RESEARCH_ACADEMY_CASH)
        with mock.patch.object(session, "GAME_CONFIG", research):
            self.assertEqual(first_bid_reasons(SEED_WITH_SEASON_ONE_BID), ["insufficient_cash"])
