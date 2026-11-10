"""game-v3 (age_spread): opt-in age_spread (uncalibrated) staggers initial and newcomer ages; default off changes nothing, replay verification follows the ages."""
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from experiments import t030
from soccer_sim import league_economy as le

NAME = 'open_solidarity_sampled_population'
SPREAD = 6
START = 33
SEASONS = 4


def config(spread):
    return replace(t030.cfg(NAME, START, 'on'), age_spread=spread)


class TestAgeSpread(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.world = le.run_league_world(seed=100, days=SEASONS * t030.L, config=config(SPREAD))

    def test_invalid_config(self):
        for bad in (-1, 1.5, True, START - le.YOUTH_INTAKE_AGE):
            with self.assertRaises(ValueError):
                config(bad)
        with self.assertRaises(ValueError):  # needs generations
            replace(config(0), generations='none', age_spread=1)

    def test_default_off_is_byte_identical(self):
        explicit = le.run_league_world(seed=100, days=2 * t030.L, config=config(0))
        default = le.run_league_world(seed=100, days=2 * t030.L, config=t030.cfg(NAME, START, 'on'))
        self.assertEqual(json.dumps(le._snapshot_payload_unchecked(explicit), sort_keys=True),
                         json.dumps(le._snapshot_payload_unchecked(default), sort_keys=True))
        self.assertNotIn('age_spread', le._snapshot_payload_unchecked(default)['config'])

    def test_initial_ages_are_staggered_and_newcomer_ages_are_young(self):
        first = next(e for e in self.world.events if e['kind'] == 'season_aged')
        initial = {pid: age - 1 for pid, age in first['data']['ages'].items() if pid in le.PLAYER_IDS}
        self.assertTrue(set(initial.values()) <= set(range(START - SPREAD, START + 1)))
        self.assertGreater(len(set(initial.values())), 2)
        joins = [e['data']['age'] for e in self.world.events if e['kind'] == 'season_youth_joined']
        self.assertTrue(joins and set(joins) <= set(range(le.YOUTH_INTAKE_AGE, le.YOUTH_INTAKE_AGE + SPREAD + 1)))

    def test_retirements_are_spread_over_several_seasons(self):
        seasons = {e['data']['season'] for e in self.world.events if e['kind'] == 'season_retired'}
        self.assertGreaterEqual(len(seasons), 2)

    def test_the_draw_is_a_separate_channel(self):
        """Same players and traits as the default; only the age counter differs."""
        spread = le._build_initial_world(seed=100, config=config(SPREAD))
        plain = le._build_initial_world(seed=100, config=config(0))
        self.assertEqual({k: v.to_dict() for k, v in spread.players.items()}, {k: v.to_dict() for k, v in plain.players.items()})
        self.assertNotEqual(spread.player_ages, plain.player_ages)

    def test_round_trip_and_age_tamper_rejection(self):
        with tempfile.TemporaryDirectory() as d:
            le.save_league_artifacts(self.world, Path(d) / 'w')
            for reader in (le.load_league_snapshot, le.load_league_snapshot_trusted):
                le._assert_same(reader(Path(d) / 'w' / 'snapshot.json'), self.world, 'world')
            raw = json.loads((Path(d) / 'w' / 'snapshot.json').read_text())
            joined = next(e for e in raw['events'] if e['kind'] == 'season_youth_joined')
            joined['data']['age'] += 1
            (Path(d) / 'w' / 'snapshot.json').write_text(json.dumps(raw))
            with self.assertRaises(Exception):
                le.load_league_snapshot_trusted(Path(d) / 'w' / 'snapshot.json')


if __name__ == '__main__':
    unittest.main()
