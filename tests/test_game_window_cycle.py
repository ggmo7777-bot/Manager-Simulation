"""game-v6 (transfer_window_cycle): opt-in per-season transfer windows; default off changes nothing, every season has its windows."""
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
SEASONS = 4


def config(cycle):
    return replace(t030.cfg(NAME, START, 'on'), transfer_window_cycle=cycle)


def window_days(world):
    return sorted({e['day'] for e in world.events if e['kind'] == 'transfer_window_decision'})


class TestWindowCycle(unittest.TestCase):
    def test_invalid_config(self):
        for bad in ('', 'every_season', 5):
            with self.assertRaises(ValueError):
                config(bad)
        with self.assertRaises(ValueError):  # needs a season length
            replace(le.config_for_condition(NAME), transfer_window_cycle='per_season_v1')

    def test_default_off_is_byte_identical_and_keeps_the_fixed_days(self):
        explicit = le.run_league_world(seed=100, days=3 * t030.L, config=config('fixed'))
        default = le.run_league_world(seed=100, days=3 * t030.L, config=t030.cfg(NAME, START, 'on'))
        self.assertEqual(json.dumps(le._snapshot_payload_unchecked(explicit), sort_keys=True),
                         json.dumps(le._snapshot_payload_unchecked(default), sort_keys=True))
        self.assertNotIn('transfer_window_cycle', le._snapshot_payload_unchecked(default)['config'])
        self.assertEqual(window_days(default), list(le.TRANSFER_WINDOW_DAYS))

    def test_per_season_windows_recur_at_the_same_in_season_days(self):
        world = le.run_league_world(seed=100, days=SEASONS * t030.L, config=config('per_season_v1'))
        offsets = {(day - 1) % t030.L + 1 for day in window_days(world)}
        self.assertEqual(offsets, {(day - 1) % t030.L + 1 for day in le.TRANSFER_WINDOW_DAYS})
        self.assertEqual(len(window_days(world)), 2 * SEASONS)
        self.assertEqual(le._snapshot_payload_unchecked(world)['config']['transfer_window_cycle'], 'per_season_v1')

    def test_the_first_fixed_window_is_unchanged_and_no_rng_is_added(self):
        """Up to the first window the histories are identical; the cycle only adds window steps, never a new random call site."""
        long_season = replace(config('fixed'), season_length_days=42)  # first cycled window is then day 14
        fixed = le.run_league_world(seed=100, days=13, config=long_season)
        cycled = le.run_league_world(seed=100, days=13, config=replace(long_season, transfer_window_cycle='per_season_v1'))
        self.assertEqual(json.dumps(le._snapshot_payload_unchecked(fixed)['events'], sort_keys=True),
                         json.dumps(le._snapshot_payload_unchecked(cycled)['events'], sort_keys=True))


if __name__ == '__main__':
    unittest.main()
