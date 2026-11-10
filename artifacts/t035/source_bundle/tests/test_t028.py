"""T028: advice response (comply / partial / refuse) from the player's own state and latent traits; default off, coach sees only the outcome."""
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from soccer_sim import league_economy as le
from experiments import t028

NAME = 'open_solidarity_sampled_population'


class TestAdviceResponse(unittest.TestCase):
    def test_invalid_config_pure_rule_and_static_leak_check(self):
        self.assertTrue(all(t028.check_invalid().values()))
        self.assertGreater(t028.check_pure()['grid_points'], 900)
        self.assertIn('_player_advice_response', t028.check_leak_static()['readers'])

    def test_events_are_consistent_and_coach_event_is_blind(self):
        t028.DAYS, t028.SEASON = 40, 20
        try:
            for mode in ('on', 'mid'):
                world, _, rows, _ = t028.run_cell(NAME, 100, mode)
                self.assertGreater(len(rows), 0)
            self.assertEqual({r['response'] for r in rows} <= {'complied', 'partial', 'refused'}, True)
            t028.run_cell(NAME, 100, 'off')
        finally:
            t028.DAYS, t028.SEASON = 168, 84

    def test_perturbation_leaves_coach_side_unchanged_and_large_change_flips_responses(self):
        t028.DAYS, t028.SEASON = 40, 20
        try:
            self.assertTrue(t028.check_leak_dynamic(NAME)['low_differs'])
        finally:
            t028.DAYS, t028.SEASON = 168, 84

    def test_default_off_adds_nothing_and_roundtrips(self):
        world = le.run_league_world(seed=100, days=20, config=t028.cfg(NAME))
        self.assertFalse(any(e['kind'] in ('player_advice_response', 'coach_advice_outcome') for e in world.events))
        self.assertNotIn('advice_response', le._snapshot_payload_unchecked(world)['config'])
        t028.DAYS, t028.SEASON = 40, 20
        try:
            with tempfile.TemporaryDirectory() as d:
                self.assertGreater(t028.check_persistence(NAME, Path(d) / 'p'), 0)
                self.assertTrue(all(v == 'rejected' for k, v in t028.check_tamper(Path(d) / 't').items() if k.endswith('load_league_snapshot')))
        finally:
            t028.DAYS, t028.SEASON = 168, 84

    def test_default_unchanged(self):
        if (ROOT / 'artifacts/t023/baseline/baseline.json').is_file():
            self.assertTrue(t028.check_default_unchanged(ROOT)['advice_mode_default_identical'])


if __name__ == '__main__':
    unittest.main()
