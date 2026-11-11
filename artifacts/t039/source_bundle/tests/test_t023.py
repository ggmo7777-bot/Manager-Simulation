"""T023: season boundary is a pure time unit; defaults are untouched; hooks fire at season ends; persistence round-trips."""
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from soccer_sim import league_economy as le
from experiments import t023


class TestSeasonBoundary(unittest.TestCase):
    def test_default_config_has_no_season_key_and_history_is_unchanged_by_enabling(self):
        for name in ('open_solidarity', 'closed_no_solidarity' + le.POPULATION_SUFFIX):
            raw = le._snapshot_payload_unchecked(le.run_league_world(seed=t023.SEED, days=7, config=le.config_for_condition('league_' + name)))
            self.assertNotIn('season_length_days', raw['config'])
            for length in (3, 7):
                t023.DAYS, days = 14, t023.DAYS
                try:
                    t023.check_history_identical(name, length)
                finally:
                    t023.DAYS = days

    def test_progress_hooks_and_invalid_inputs(self):
        hooks = t023.check_hooks_and_progress()
        self.assertEqual([d for d, _ in hooks['from_0_three']], [42, 84, 126])
        self.assertTrue(all(t023.check_invalid().values()))

    def test_save_and_both_readers_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertTrue(t023.check_persistence(Path(d))['trusted_reader_equal'])

    def test_default_digests_match_pre_implementation_baseline(self):
        baseline = ROOT / 'artifacts/t023/baseline/baseline.json'
        if baseline.is_file():
            self.assertTrue(t023.check_default_unchanged(baseline)['equal_to_pre_implementation_digest'])


if __name__ == '__main__':
    unittest.main()
