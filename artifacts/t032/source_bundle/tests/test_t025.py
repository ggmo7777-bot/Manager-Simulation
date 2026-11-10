"""T025: aging is a recorded season-end event (age counter only); defaults, replay and persistence are intact."""
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from soccer_sim import league_economy as le
from experiments import t023, t025


class TestAging(unittest.TestCase):
    def test_hand_case_and_invalid_config(self):
        self.assertEqual(len(t025.check_hand_case()), 3)
        self.assertTrue(all(t025.check_invalid().values()))

    def test_cell_equals_no_aging_world_after_stripping_and_round_trips(self):
        # 28 days, L=14: two season ends; same code path as the 126-day cells.
        t025.DAYS, days = 28, t025.DAYS
        try:
            with tempfile.TemporaryDirectory() as d:
                self.assertEqual(t025.check_cell('open_solidarity', 14, Path(d) / 'p')['season_aged_events'], 2)
        finally:
            t025.DAYS = days

    def test_branch_and_tamper(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(t025.check_branch_round_trip(Path(d) / 'b')['interventions'], 1)
            world = le.run_league_world(seed=t025.SEED, days=84, config=t025.aged_config('open_solidarity', 42))
            self.assertTrue(all(t025.check_tamper(world, Path(d) / 't').values()))

    def test_default_unchanged(self):
        if (ROOT / 'artifacts/t023/baseline/baseline.json').is_file():
            self.assertEqual(t025.check_default_unchanged(ROOT)['worlds'], 14)


if __name__ == '__main__':
    unittest.main()
