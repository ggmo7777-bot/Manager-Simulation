"""Seed sharing: a short code (config version + seed) opens the same world; another version is rejected clearly."""
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from game.server import Game
from game.session import GameSession, InvalidInput, config_version, seed_from_code, share_code


class TestSeedSharing(unittest.TestCase):
    def test_code_round_trips_for_any_integer_seed(self):
        for seed in (0, 7, 123456, -5):
            self.assertEqual(seed_from_code(share_code(seed)), seed)

    def test_state_carries_the_code(self):
        self.assertEqual(GameSession(11).state()["share_code"], share_code(11))

    def test_a_code_from_another_version_is_rejected_with_both_versions_named(self):
        with self.assertRaises(InvalidInput) as raised:
            seed_from_code("zzzzzz-7")
        self.assertIn("zzzzzz", str(raised.exception))
        self.assertIn(config_version(), str(raised.exception))

    def test_malformed_codes_are_rejected(self):
        for bad in ("", "7", "abc", f"{config_version()}-", f"{config_version()}-x", 7, None):
            with self.assertRaises(InvalidInput, msg=repr(bad)):
                seed_from_code(bad)

    def test_two_players_with_one_code_get_the_same_world(self):
        with tempfile.TemporaryDirectory() as directory:
            host, guest = Game(7, directory), Game(1, directory)
            code = host.handle("GET", "/api/state", {})["share_code"]
            joined = guest.handle("POST", "/api/new", {"code": code})
            self.assertEqual(joined, host.handle("GET", "/api/state", {}))
            with self.assertRaises(InvalidInput):
                guest.handle("POST", "/api/new", {"code": "zzzzzz-7"})
            self.assertEqual(guest.handle("GET", "/api/state", {})["seed"], 7)


if __name__ == "__main__":
    unittest.main()
