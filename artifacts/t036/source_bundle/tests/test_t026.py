"""T026: age changes true skill once per season end via a recorded event; the Policy never reads the age."""
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from soccer_sim import league_economy as le
from experiments import t026


class TestAgingCurve(unittest.TestCase):
    def test_hand_case_invalid_config_isolation_and_policy_blindness(self):
        self.assertEqual(t026.check_hand_case()['S29_L42'], [-0.01, -0.02, -0.03])
        self.assertTrue(all(t026.check_invalid().values()))
        self.assertTrue(t026.check_isolation()['only_traits_changed'])
        self.assertIn('_age_players', t026.check_policy_blind()['readers'])

    def test_cell_follows_curve_and_round_trips(self):
        # 28 days, L=14: two season ends, both start ages; same code path as the 126-day cells.
        with tempfile.TemporaryDirectory() as d:
            for start in (18, 29):
                cell = t026.check_cell('open_solidarity', 14, start, Path(d) / str(start), days=28)
                self.assertEqual(len(cell['seasons']), 2)

    def test_branch_and_tamper(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(t026.check_branch_round_trip(Path(d) / 'b')['interventions'], 1)
            world = le.run_league_world(seed=t026.SEED, days=84, config=t026.curve_config('open_solidarity', 42, 29))
            result = t026.check_tamper(world, Path(d) / 't')
            self.assertTrue(all(v for k, v in result.items() if 'snapshot_player_traits_changed/load_league_snapshot_trusted' not in k), result)

    def test_default_unchanged(self):
        if (ROOT / 'artifacts/t023/baseline/baseline.json').is_file():
            base = t026.t023.check_default_unchanged(ROOT / 'artifacts/t023/baseline/baseline.json')
            self.assertEqual(base['worlds'], 14)


if __name__ == '__main__':
    unittest.main()
