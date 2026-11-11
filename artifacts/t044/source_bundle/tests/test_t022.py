"""T022: the trusted direct reader equals the full-replay reader, rejects mismatched manifests/edits, and leaves the default reader alone."""
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from soccer_sim import league_economy as le
from experiments import t022


class TestTrustedReader(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)

    def test_equals_replay_for_all_conditions_fixed_and_sampled(self):
        for i, name in enumerate(t022.conditions()):
            with self.subTest(name):
                t022.check_equivalence(name, 7, self.dir / str(i))

    def test_resume_and_multi_step_branch(self):
        self.assertTrue(t022.check_continuation(self.dir)['multi_step_branch_equal'])

    def test_rejections_and_forged_manifest_characterization(self):
        world = le.run_league_world(seed=t022.SEED, days=7, config=le.config_for_condition('league_open_solidarity' + le.POPULATION_SUFFIX))
        rejected, forged = t022.tamper(self.dir, world)
        self.assertTrue(all(rejected.values()), rejected)
        # A full replay rejects every forged-manifest edit; the trusted reader's exact re-serialization catches type/shape edits.
        self.assertTrue(all(row['replay'] for row in forged.values()), forged)
        for key in ('type_change_float_to_int', 'extra_key'):
            self.assertTrue(forged[key]['trusted'], key)

    def test_default_reader_unchanged_and_requires_no_manifest(self):
        world = le.run_league_world(seed=t022.SEED, days=3, config=le.config_for_condition('league_closed_solidarity'))
        snap = t022.saved(world, self.dir / 'w')
        (snap.parent / 'manifest.json').unlink()
        le._assert_same(le.load_league_snapshot(snap), world)
        with self.assertRaises(OSError):
            le.load_league_snapshot_trusted(snap)


if __name__ == '__main__':
    unittest.main()
