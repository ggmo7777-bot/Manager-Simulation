"""T031: the coach's observe value grows with days unseen up to 40 (default 10); default off, inputs are the coach's own B-layer only."""
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from soccer_sim import league_economy as le
from experiments import t031

NAME = 'open_solidarity_sampled_population'


class TestCoachExploration(unittest.TestCase):
    def test_invalid_config_pure_rule_and_static_leak_check(self):
        self.assertTrue(all(t031.check_invalid().values()))
        self.assertGreater(t031.check_pure()['observe_scores_checked'], 0)
        self.assertIn('_step_training_day', t031.check_static()['exploration_readers'])

    def test_observe_scores_match_the_independent_rule_and_only_diverge_after_day_10(self):
        on_world, on = t031.run_cell(NAME, 100, 33, 'on', 2 * t031.L)
        off_world, off = t031.run_cell(NAME, 100, 33, 'off', 2 * t031.L)
        self.assertGreater(on['observe_scores_checked'], 0)
        self.assertEqual(t031.check_prefix(on_world, off_world)['day'], 11)
        self.assertGreaterEqual(on['observes'], off['observes'])

    def test_benched_true_skill_does_not_reach_decisions_before_first_observation(self):
        self.assertTrue(all(v['days_compared_before_first_observation'] >= 5 for v in t031.check_leak_dynamic().values()))

    def test_round_trip_and_tamper(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(t031.check_tamper(Path(tmp))['load_league_snapshot'], 'rejected')
            self.assertTrue(t031.check_branch_and_steps(Path(tmp) / 'b')['stepped_equals_oneshot'])

    def test_default_off_adds_nothing(self):
        world = le.run_league_world(seed=100, days=2 * t031.L, config=t031.cfg(NAME, 33, 'off'))
        self.assertNotIn('coach_exploration', le._snapshot_payload_unchecked(world)['config'])

    def test_default_unchanged(self):
        self.assertEqual(t031.check_default_unchanged(ROOT)['explicit_none_equals_default'], 14)


if __name__ == '__main__':
    unittest.main()
