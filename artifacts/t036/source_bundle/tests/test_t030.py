"""T030: season-end retirement from the player's own state and same-day youth intake; default off, newcomers' true values never reach the coach."""
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from soccer_sim import league_economy as le
from experiments import t030

NAME = 'open_solidarity_sampled_population'


class TestGenerations(unittest.TestCase):
    def test_invalid_config_pure_rule_and_static_leak_check(self):
        self.assertTrue(all(t030.check_invalid().values()))
        self.assertEqual(t030.check_pure()['grid_points'], 3030)
        self.assertIn('_generational_turnover', t030.check_static()['generation_readers'])

    def test_everyone_retires_and_is_replaced_the_same_day(self):
        world, cell = t030.run_cell(NAME, 100, 35, 'on', 2 * t030.L)  # age 36 at the first season end: all twelve retire
        first, second = cell['seasons']
        self.assertEqual((first['retired'], first['joined'], first['n_active'], first['founders_active']), (12, 12, 12, 0))
        self.assertEqual(second['retired'], 0)
        self.assertGreaterEqual(cell['min_roster_any_day'], 2)
        self.assertEqual(sorted(world.player_ages.values()), [19] * 12)
        self.assertEqual(len(world.players), 24)

    def test_partial_retirement_by_satisfaction_and_isolation_before_it(self):
        world, cell = t030.run_cell(NAME, 100, 33, 'on', 2 * t030.L)
        off, _ = t030.run_cell(NAME, 100, 33, 'off', 2 * t030.L)
        self.assertGreater(t030.check_prefix(world, off), 0)
        self.assertEqual(cell['seasons'][0]['retired_reasons']['age_limit'], 0)  # 34 years old: only the satisfaction path can retire
        self.assertTrue(t030.check_isolation_state()['founders_unchanged'])

    def test_no_true_value_reaches_the_coach_impression(self):
        self.assertTrue(t030.check_leak_dynamic()['impressions_equal_constant'])

    def test_round_trip_and_tamper_rejection(self):
        with tempfile.TemporaryDirectory() as d:
            world = le.run_league_world(seed=100, days=2 * t030.L, config=t030.cfg(NAME, 35, 'on'))
            le.save_league_artifacts(world, Path(d) / 'w')
            for reader in (le.load_league_snapshot, le.load_league_snapshot_trusted):
                le._assert_same(reader(Path(d) / 'w' / 'snapshot.json'), world, 'world')
            self.assertEqual(len(t030.check_tamper(Path(d) / 't')), 12)

    def test_default_off_adds_nothing(self):
        world = le.run_league_world(seed=100, days=2 * t030.L, config=t030.cfg(NAME, 35, 'off'))
        self.assertFalse(any(e['kind'] in ('season_retired', 'season_youth_joined') for e in world.events))
        self.assertNotIn('generations', le._snapshot_payload_unchecked(world)['config'])
        self.assertEqual(le._active_pids(world), list(le.PLAYER_IDS))

    def test_default_unchanged(self):
        if (ROOT / 'artifacts/t023/baseline/baseline.json').is_file():
            self.assertEqual(t030.check_default_unchanged(ROOT)['explicit_none_equals_default_aging_config'], 14)


if __name__ == '__main__':
    unittest.main()
