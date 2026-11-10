"""T032: a practice-day observation also updates the coach's match-rating estimate (0.70/0.30); default off, B-layer only."""
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from soccer_sim import league_economy as le
from experiments import t032

NAME = 'open_solidarity_sampled_population'


class TestCoachPracticeRating(unittest.TestCase):
    def test_invalid_config_and_static_leak_check(self):
        self.assertTrue(all(t032.check_invalid().values()))
        self.assertIn('_step_training_day', t032.check_static()['practice_rating_readers'])

    def test_update_matches_independent_rule_and_first_divergence_follows_it(self):
        on_world, on = t032.run_cell(NAME, 100, 33, 'on', 2 * t032.L)
        off_world, off = t032.run_cell(NAME, 100, 33, 'off', 2 * t032.L)
        self.assertGreater(on['practice_updates_checked'], 0)
        self.assertEqual(off['practice_updates_checked'], 0)
        isolation = t032.check_isolation(NAME, 100, 33)
        self.assertGreater(isolation['pairs_with_changed_match_estimate'], 0)
        self.assertGreaterEqual(t032.first_divergence(off_world, on_world)['day'], isolation['first_practice_observation_day'])

    def test_benched_true_skill_does_not_reach_selection_before_first_observation(self):
        self.assertTrue(all(v['days_compared_before_first_observation'] >= 5 for v in t032.check_leak_dynamic().values()))

    def test_round_trip_and_tamper(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(t032.check_tamper(Path(tmp))['load_league_snapshot'], 'rejected')
            self.assertTrue(t032.check_branch_and_steps(Path(tmp) / 'b')['stepped_equals_oneshot'])

    def test_default_off_adds_nothing(self):
        world = le.run_league_world(seed=100, days=2 * t032.L, config=t032.cfg(NAME, 33, 'off'))
        self.assertNotIn('coach_practice_rating', le._snapshot_payload_unchecked(world)['config'])

    def test_default_unchanged(self):
        self.assertEqual(t032.check_default_unchanged(ROOT)['explicit_none_equals_default_with_exploration'], 14)


if __name__ == '__main__':
    unittest.main()
