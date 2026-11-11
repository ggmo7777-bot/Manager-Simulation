"""game-v8 (squad_size): opt-in bigger squads (uncalibrated); default 3 changes nothing, extras are recorded day-0 intake, replay verification follows."""
from dataclasses import replace
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from experiments import t030
from soccer_sim import league_economy as le

NAME = 'open_solidarity_sampled_population'
START = 33
SQUAD = 5
SEASONS = 3


def config(size):
    return replace(t030.cfg(NAME, START, 'on'), squad_size=size)


def payload(world):
    return json.dumps(le._snapshot_payload_unchecked(world), sort_keys=True)


class TestSquadSize(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.world = le.run_league_world(seed=100, days=SEASONS * t030.L, config=config(SQUAD))

    def test_invalid_config(self):
        for bad in (2, 9, 4.0, True, '5'):
            with self.assertRaises(ValueError):
                config(bad)
        with self.assertRaises(ValueError):  # needs generations
            replace(config(3), generations='none', squad_size=4)

    def test_default_off_is_byte_identical(self):
        explicit = le.run_league_world(seed=100, days=2 * t030.L, config=config(3))
        default = le.run_league_world(seed=100, days=2 * t030.L, config=t030.cfg(NAME, START, 'on'))
        self.assertEqual(payload(explicit), payload(default))
        self.assertNotIn('squad_size', le._snapshot_payload_unchecked(default)['config'])
        self.assertEqual(le._snapshot_payload_unchecked(self.world)['config']['squad_size'], SQUAD)

    def test_every_club_starts_and_stays_at_the_squad_size(self):
        start = le._build_initial_world(seed=100, config=config(SQUAD))
        self.assertEqual({len(r) for r in start.club_rosters.values()}, {SQUAD})
        self.assertEqual(len(start.players), 4 * SQUAD)
        self.assertTrue(all(e['kind'] != 'season_youth_joined' or e['day'] == 0 for e in start.events))
        self.assertTrue(all(le.MIN_CLUB_ROSTER <= len(r) <= le.club_roster_max(self.world.config) for r in self.world.club_rosters.values()))

    def test_original_twelve_are_unchanged(self):
        """Extras are newcomers with ids after p12; the founders and their traits match the default world."""
        big = le._build_initial_world(seed=100, config=config(SQUAD))
        plain = le._build_initial_world(seed=100, config=config(3))
        for pid in le.PLAYER_IDS:
            self.assertEqual(big.players[pid].to_dict(), plain.players[pid].to_dict())

    def test_replay_verified_history_follows_the_extras(self):
        le._validate_league_invariants(self.world)
        self.assertEqual(le._generation_fold(self.world)[1][:12], list(le.PLAYER_IDS))
