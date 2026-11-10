"""T027: latent personality traits are true player state only: default off, used by no decision, hidden from Policy."""
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from soccer_sim import league_economy as le
from experiments import t027


class TestPersonality(unittest.TestCase):
    def test_invalid_config_and_static_leak_check(self):
        self.assertTrue(all(t027.check_invalid().values()))
        self.assertIn('_build_initial_world', t027.check_leak_static()['readers'])

    def test_generator_matches_independent_implementation_and_distribution(self):
        stats = t027.check_generator()['stats']
        self.assertEqual(set(stats), set(t027.TRAITS))

    def test_enabling_changes_no_history_and_extreme_traits_change_nothing(self):
        t027.DAYS = 28
        try:
            self.assertGreater(t027.check_history('open_solidarity', 100), 0)
            self.assertTrue(t027.check_leak_dynamic('open_solidarity_sampled_population'))
        finally:
            t027.DAYS = 126

    def test_persistence_branch_and_tamper(self):
        t027.DAYS = 28
        try:
            with tempfile.TemporaryDirectory() as d:
                t027.check_persistence('open_solidarity_sampled_population', Path(d) / 'p')
                self.assertEqual(t027.check_branch_and_steps(Path(d) / 'b')['interventions'], 1)
                self.assertTrue(all(t027.check_tamper(Path(d) / 't').values()))
        finally:
            t027.DAYS = 126

    def test_default_unchanged(self):
        if (ROOT / 'artifacts/t023/baseline/baseline.json').is_file():
            self.assertEqual(t027.check_default_unchanged(ROOT)['worlds'], 14)


if __name__ == '__main__':
    unittest.main()
