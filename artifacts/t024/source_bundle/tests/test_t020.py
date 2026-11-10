"""T020: range perturbations keep default worlds identical, are refused when invalid, and match an independent recomputation."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from soccer_sim import league_economy as le
from experiments import t019, t020

T018 = ROOT / "artifacts/t018/rerun-py313"
SAMPLED = "open_solidarity" + le.POPULATION_SUFFIX


class TestT020RangePerturbation(unittest.TestCase):
    def test_default_conditions_match_frozen_t018_and_omit_range_key(self):
        for name in ("open_solidarity", "closed_no_solidarity" + le.POPULATION_SUFFIX):
            raw = le._snapshot_payload_unchecked(le.run_league_world(seed=100, days=35, config=le.config_for_condition(name)))
            self.assertNotIn("range_perturbation", raw["config"])
            frozen = T018 / "runs" / name / "snapshot.json"
            if frozen.is_file():
                self.assertEqual(t019.normalized(raw), t019.normalized(json.loads(frozen.read_text())))

    def test_null_perturbation_equals_baseline_sampled(self):
        a = le.run_league_world(seed=101, days=28, config=le.config_for_condition(SAMPLED))
        b = le.run_league_world(seed=101, days=28, config=le.config_for_condition(SAMPLED + le.RANGE_SUFFIX + "null"))
        ra, rb = le._snapshot_payload_unchecked(a), le._snapshot_payload_unchecked(b)
        self.assertEqual(rb["config"].pop("range_perturbation"), "null")
        self.assertEqual({**ra["config"], "condition": 0}, {**rb["config"], "condition": 0})
        self.assertEqual(t019.anonymous(ra), t019.anonymous(rb))

    def test_invalid_perturbations_are_refused_not_clipped(self):
        self.assertEqual(t020.REFUSED, ("cap_w15", "psych_w15", "cap_cp"))
        self.assertEqual(len(t020.PERTURBATIONS), 18)
        for name in t020.REFUSED + ("trait_w25", "none"):
            with self.assertRaises(ValueError, msg=name):
                le.config_for_condition(SAMPLED + le.RANGE_SUFFIX + name)
        for bad in ("open_solidarity_range_trait_w05", "nope_sampled_population_range_trait_w05"):
            with self.assertRaises(ValueError, msg=bad):
                le.config_for_condition(bad)
        with self.assertRaises(ValueError):
            le.LeagueEconomyConfig(range_perturbation="cap_w15")

    def test_accepted_ranges_keep_rules_and_draws_match_independent_recomputation(self):
        for name in t020.EXECUTED:
            for (kind, param), (low, high) in le.sampled_ranges(name).items():
                self.assertLess(low, high)
                if param in le.RANGE_BOUNDED:
                    self.assertTrue(0.05 <= low and high <= 0.95, (name, kind, param))
        info = t020.check_initial((300,))
        self.assertEqual(info["cells_checked"], len(t020.EXECUTED) + 2)
        raw = t020.raw_initial(300, "trait_w15")
        for pid, p in raw["players"].items():
            self.assertGreaterEqual(p["cap"], min(0.98, max(p["traits"].values()) + 0.05) - 1e-6)

    def test_perturbation_changes_only_its_group(self):
        base = t020.raw_initial(300, "baseline")
        noise = t020.raw_initial(300, "noise_w15")
        self.assertEqual(base["players"], noise["players"])
        self.assertNotEqual(base["coaches"], noise["coaches"])
        rate = t020.raw_initial(300, "rate_cp")
        self.assertEqual(base["coaches"], rate["coaches"])
        self.assertNotEqual(base["players"], rate["players"])

    def test_config_round_trip_and_save_resume(self):
        config = le.config_for_condition(SAMPLED + le.RANGE_SUFFIX + "rate_w15")
        self.assertEqual(le.LeagueEconomyConfig.from_dict(config.to_dict()), config)
        full = le.run_league_world(seed=300, days=28, config=config)
        part = le.run_league_world(seed=300, days=14, config=config)
        with tempfile.TemporaryDirectory() as tmp:
            le.save_league_artifacts(part, Path(tmp))
            resumed = le.load_league_snapshot(Path(tmp) / "snapshot.json")
        le.advance_league_days(resumed, 14)
        self.assertEqual(resumed, full)

    def test_cli_accepts_runnable_and_rejects_refused_conditions(self):
        def cli(name, code):
            result = subprocess.run([sys.executable, "-B", "-m", "soccer_sim", "run", "--condition", name, "--seed", "300",
                                     "--days", "0", "--out", tempfile.mkdtemp()], cwd=ROOT, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, code, result.stderr)
        cli("league_" + SAMPLED + "_range_noise_w15", 0)
        cli("league_" + SAMPLED + "_range_cap_w15", 2)


if __name__ == "__main__":
    unittest.main()
