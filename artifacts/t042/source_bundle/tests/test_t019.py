"""T019: factor fields keep default worlds identical and set each factor independently."""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from soccer_sim import league_economy as le
from experiments import t019

T018 = ROOT / "artifacts/t018/rerun-py313"


class TestT019Factors(unittest.TestCase):
    def test_default_conditions_match_frozen_t018_and_omit_factor_keys(self):
        for name in ("open_solidarity", "closed_no_solidarity" + le.POPULATION_SUFFIX):
            world = le.run_league_world(seed=100, days=35, config=le.config_for_condition(name))
            raw = le._snapshot_payload_unchecked(world)
            self.assertNotIn("profile_mode", raw["config"])
            frozen = json.loads((T018 / "runs" / name / "snapshot.json").read_text()) if (T018 / "runs" / name).is_dir() else None
            if frozen:
                self.assertEqual(t019.normalized(raw), t019.normalized(frozen))

    def test_explicit_corner_cells_equal_legacy_populations(self):
        for bits, suffix in (("000", ""), ("111", le.POPULATION_SUFFIX)):
            a = le.run_league_world(seed=101, days=20, config=le.config_for_condition("open_solidarity" + suffix))
            b = le.run_league_world(seed=101, days=20, config=le.config_for_condition("open_solidarity" + le.FACTORS_SUFFIX + bits))
            ra, rb = le._snapshot_payload_unchecked(a), le._snapshot_payload_unchecked(b)
            self.assertEqual(t019.anonymous(ra), t019.anonymous(rb))

    def test_factor_independence_and_independent_recomputation(self):
        info = t019.check_independence((100, 117))
        self.assertEqual(info["single_factor_flips_checked"], 48)

    def test_corner_cells_reproduce_t016_reinvest_effects(self):
        # Exact per-seed T016 values for three seeds; the full 30-seed equality is in the experiment.
        t016 = json.loads((ROOT / "artifacts/t016/comparison/summary.json").read_text())
        for mode, cell in (("fixed", "000"), ("sampled", "111")):
            archived = next(e for e in t016["paired_effects"] if e["population"] == mode
                            and e["metric"] == t019.METRICS[0] and e["contrast"] == "open_solidarity-open_no_solidarity")
            for item in archived["paired_differences"][:3]:
                gain = [t019.worker((cell, i, item["seed"]))["metrics"][t019.METRICS[0]] for i in ("open_solidarity", "open_no_solidarity")]
                self.assertEqual(round(gain[0] - gain[1], 6), item["difference"])

    def test_invalid_inputs_are_refused(self):
        for bad in ("open_solidarity_factors_12", "open_solidarity_factors_102", "nope_factors_101",
                    "open_solidarity_sampled_population_factors_101", "open_solidarity_factors_101_factors_101"):
            with self.assertRaises(ValueError, msg=bad):
                le.config_for_condition(bad)
        for field in le.FACTOR_FIELDS:
            with self.assertRaises(ValueError):
                le.LeagueEconomyConfig(**{field: "random"})

    def test_config_round_trip_with_and_without_factors(self):
        for name in ("open_solidarity", "open_solidarity" + le.FACTORS_SUFFIX + "010"):
            config = le.config_for_condition(name)
            self.assertEqual(le.LeagueEconomyConfig.from_dict(config.to_dict()), config)
        self.assertEqual(le.config_for_condition("open_solidarity_factors_010").to_dict()["impression_mode"], "sampled")

    def test_mixed_cell_saves_and_resumes(self):
        config = le.config_for_condition("open_solidarity_factors_101")
        full = le.run_league_world(seed=100, days=28, config=config)
        part = le.run_league_world(seed=100, days=14, config=config)
        with self.subTest("snapshot"):
            import tempfile
            with tempfile.TemporaryDirectory() as tmp:
                le.save_league_artifacts(part, Path(tmp))
                resumed = le.load_league_snapshot(Path(tmp) / "snapshot.json")
        le.advance_league_days(resumed, 14)
        self.assertEqual(resumed, full)


if __name__ == "__main__":
    unittest.main()
