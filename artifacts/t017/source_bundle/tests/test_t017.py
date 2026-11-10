"""T017: sampled league conditions through the ordinary command-line interface."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from soccer_sim import experiment_t011
from soccer_sim import league_economy as le


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_FILES = ("snapshot.json", "events.jsonl", "summary.json", "manifest.json")
SAMPLED = "open_solidarity" + le.POPULATION_SUFFIX


class TestSampledLeagueCLI(unittest.TestCase):
    def cli(self, *args, code=0):
        result = subprocess.run(
            [sys.executable, "-B", "-m", "soccer_sim", *map(str, args)],
            cwd=ROOT, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return result

    def run_cli(self, condition, days, out):
        self.cli("run", "--condition", "league_" + condition, "--seed", 100,
                 "--days", days, "--out", out)

    def assert_api_artifacts(self, actual, world, expected):
        le.save_league_artifacts(world, expected)
        for filename in ARTIFACT_FILES:
            with self.subTest(artifact=filename):
                self.assertEqual((actual / filename).read_bytes(),
                                 (expected / filename).read_bytes())

    def test_help_lists_all_fixed_and_sampled_league_conditions(self):
        result = self.cli("run", "--help")
        for name in le.T011_ALL_CONDITIONS:
            self.assertIn("league_" + name, result.stdout)
            self.assertIn("league_" + name + le.POPULATION_SUFFIX, result.stdout)
        self.assertEqual(set(experiment_t011.CONDITIONS),
                         {"league_" + name for name in le.T011_ALL_CONDITIONS})
        self.assertTrue(all(config.population_mode == "fixed"
                            for config in experiment_t011.CONDITIONS.values()))

    def test_all_sampled_and_fixed_conditions_start_with_api_config_and_state(self):
        # Day zero covers dispatch and initialization; the paired 35-day experiment
        # belongs to the task's separate validation run.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for suffix in ("", le.POPULATION_SUFFIX):
                for name in le.T011_ALL_CONDITIONS:
                    condition = name + suffix
                    with self.subTest(condition=condition):
                        actual = root / condition
                        self.run_cli(condition, 0, actual)
                        config = le.config_for_condition(condition)
                        world = le.run_league_world(seed=100, days=0, config=config)
                        self.assert_api_artifacts(actual, world, root / (condition + "-api"))
                        manifest = json.loads((actual / "manifest.json").read_text())
                        self.assertEqual(manifest["config"], config.to_dict())
                        self.assertEqual(manifest["condition"], condition)
                        self.assertEqual(manifest["evaluation_version"], le.LEAGUE_EVALUATION_VERSION)

    def test_default_baseline_matches_explicit_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            implicit, explicit = root / "implicit", root / "explicit"
            self.cli("run", "--seed", 100, "--days", 2, "--out", implicit)
            self.cli("run", "--condition", "baseline", "--seed", 100,
                     "--days", 2, "--out", explicit)
            for filename in ("snapshot.json", "events.jsonl", "manifest.json"):
                self.assertEqual((implicit / filename).read_bytes(),
                                 (explicit / filename).read_bytes())
            first = json.loads((implicit / "summary.json").read_text())
            second = json.loads((explicit / "summary.json").read_text())
            first.pop("seconds")
            second.pop("seconds")
            self.assertEqual(first, second)

    def test_unknown_nonleague_and_duplicate_suffix_conditions_make_no_output(self):
        conditions = (
            "league_unknown" + le.POPULATION_SUFFIX,
            "baseline" + le.POPULATION_SUFFIX,
            "league_" + SAMPLED + le.POPULATION_SUFFIX,
        )
        with tempfile.TemporaryDirectory() as tmp:
            for index, condition in enumerate(conditions):
                with self.subTest(condition=condition):
                    out = Path(tmp) / str(index)
                    result = self.cli("run", "--condition", condition, "--seed", 100,
                                      "--days", 0, "--out", out, code=2)
                    self.assertIn("invalid choice", result.stderr)
                    self.assertFalse(out.exists())

    def test_sampled_resume_and_branch_match_api_and_preserve_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent, resumed, direct = root / "parent", root / "resumed", root / "direct"
            self.run_cli(SAMPLED, 14, parent)
            original = {name: (parent / name).read_bytes() for name in ARTIFACT_FILES}
            snapshot = parent / "snapshot.json"
            self.cli("resume", "--snapshot", snapshot, "--days", 21, "--out", resumed)
            self.run_cli(SAMPLED, 35, direct)
            for filename in ARTIFACT_FILES:
                self.assertEqual((resumed / filename).read_bytes(), (direct / filename).read_bytes())
            config = le.config_for_condition(SAMPLED)
            full = le.run_league_world(seed=100, days=35, config=config)
            self.assert_api_artifacts(resumed, full, root / "full-api")

            child, continued = root / "child", root / "continued"
            self.cli("branch", "--snapshot", snapshot, "--kind", "player_trust",
                     "--player", "p3", "--coach", "c1", "--value", 0.05,
                     "--reason", "T017 CLI trust comparison", "--days", 7, "--out", child)
            api_parent = le.run_league_world(seed=100, days=14, config=config)
            api_child = le.fork_league_world(
                api_parent, kind="player_trust", player_id="p3", coach_id="c1",
                value=0.05, reason="T017 CLI trust comparison",
            )
            le.advance_league_days(api_child, 7)
            self.assert_api_artifacts(child, api_child, root / "child-api")
            self.cli("resume", "--snapshot", child / "snapshot.json",
                     "--days", 14, "--out", continued)
            le.advance_league_days(api_child, 14)
            self.assert_api_artifacts(continued, api_child, root / "continued-api")
            self.assertEqual({name: (parent / name).read_bytes() for name in ARTIFACT_FILES}, original)

    def test_t016_snapshot_is_rejected_without_changing_its_run(self):
        parent = ROOT / "artifacts/t016/comparison/representative" / SAMPLED / "checkpoint"
        original = {name: (parent / name).read_bytes() for name in ARTIFACT_FILES}
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "rejected"
            result = self.cli("resume", "--snapshot", parent / "snapshot.json",
                              "--days", 0, "--out", out, code=2)
            self.assertIn("snapshot.identity.source_sha256", result.stderr)
            self.assertFalse(out.exists())
        self.assertEqual({name: (parent / name).read_bytes() for name in ARTIFACT_FILES}, original)


if __name__ == "__main__":
    unittest.main()
