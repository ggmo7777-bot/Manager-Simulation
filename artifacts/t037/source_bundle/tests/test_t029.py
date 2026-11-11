"""T029: the coach's receptiveness belief (B layer) from coach_advice_outcome only; default off, observe changes nothing, no trait reaches the belief."""
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from soccer_sim import league_economy as le
from experiments import t029

NAME = 'open_solidarity_sampled_population'


class ShortRun:
    def __enter__(self):
        t029.DAYS, t029.SEASON = 40, 20

    def __exit__(self, *exc):
        t029.DAYS, t029.SEASON = 168, 84


class TestCoachBelief(unittest.TestCase):
    def test_invalid_config_pure_update_and_static_leak_check(self):
        self.assertTrue(all(t029.check_invalid().values()))
        self.assertGreater(t029.check_pure()['sequences'], 4000)
        self.assertIn('update_receptiveness_belief', t029.check_static()['checked_functions'])

    def test_belief_events_are_the_fold_of_outcomes_and_use_scales_only_proposals(self):
        with ShortRun():
            for mode in ('observe', 'use'):
                world, _, stats = t029.run_cell(NAME, 100, mode)
                self.assertGreater(len(world.coach_beliefs['c1']) + len(world.coach_beliefs['c2']), 0)
            self.assertGreater(stats['propose_candidates'], 0)
            observed, _, _ = t029.run_cell(NAME, 100, 'observe')
            base, _, _ = t029.run_cell(NAME, 100, 'base')
            self.assertEqual(t029.strip_belief(observed), le._snapshot_payload_unchecked(base))

    def test_traits_reach_the_belief_only_through_outcomes(self):
        with ShortRun():
            self.assertTrue(t029.check_leak_dynamic(NAME)['low_beliefs_differ'])

    def test_default_off_adds_nothing_roundtrips_and_tampering_is_rejected(self):
        world = le.run_league_world(seed=100, days=20, config=t029.cfg(NAME, 'base'))
        self.assertFalse(any(e['kind'] == 'coach_belief_update' for e in world.events))
        self.assertNotIn('coach_beliefs', le._snapshot_payload_unchecked(world))
        with ShortRun(), tempfile.TemporaryDirectory() as d:
            self.assertGreater(t029.check_persistence(NAME, Path(d) / 'p', 'use'), 0)
            result = t029.check_tamper(Path(d) / 't')
            self.assertTrue(all(v == 'rejected' for k, v in result.items() if k.endswith('load_league_snapshot')))
            self.assertEqual(result['belief_value_changed/load_league_snapshot_trusted'], 'rejected')

    def test_default_unchanged(self):
        if (ROOT / 'artifacts/t023/baseline/baseline.json').is_file():
            self.assertTrue(t029.check_default_unchanged(ROOT)['receptiveness_default_identical'])


if __name__ == '__main__':
    unittest.main()
