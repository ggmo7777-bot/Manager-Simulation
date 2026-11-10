"""T015: sampled initial population, fixed fixture preserved, no named-player script."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from soccer_sim import league_economy as le


def sampled(seed=100, days=0):
    cfg = le.config_for_condition("open_solidarity_sampled_population")
    return le.run_league_world(seed=seed, days=days, config=cfg)


class TestSampledPopulation(unittest.TestCase):
    def test_condition_suffix_only_changes_population(self):
        base = le.config_for_condition("open_solidarity")
        cfg = le.config_for_condition("open_solidarity_sampled_population")
        self.assertEqual(base.population_mode, "fixed")
        self.assertEqual(cfg.population_mode, "sampled")
        self.assertEqual(replace(cfg, condition=base.condition, population_mode="fixed"), base)
        with self.assertRaises(ValueError):
            replace(base, population_mode="scripted")

    def test_fixed_mode_keeps_the_scripted_fixture(self):
        w = le.run_league_world(seed=100, days=0)
        self.assertEqual(w.players["p3"].traits.pace, 0.50)
        self.assertEqual(w.coaches["c1"].evaluations["p3"].skill_estimate, 0.39)
        self.assertEqual(w.coaches["c3"].evaluations["p9"].skill_estimate, 0.38)
        self.assertEqual(w.player_minds["p3"].playing_time_satisfaction, 0.42)

    def test_sampling_is_reproducible_and_depends_on_seed(self):
        a, b, c = sampled(100), sampled(100), sampled(101)
        self.assertEqual(a, b)
        self.assertNotEqual({k: v.to_dict() for k, v in a.players.items()},
                            {k: v.to_dict() for k, v in c.players.items()})

    def test_ranges_roster_and_script_removal(self):
        under = 0
        for seed in range(100, 120):
            w = sampled(seed)
            self.assertEqual({k: list(v) for k, v in w.club_rosters.items()},
                             {k: list(v) for k, v in le.INITIAL_CLUB_ROSTERS.items()})
            for pid, truth in w.players.items():
                level = truth.composite_skill()
                self.assertTrue(0.3 <= level <= 0.7, (seed, pid, level))
                self.assertGreaterEqual(truth.cap, max(vars(truth.traits).values()))
                self.assertEqual(w.player_minds[pid].playing_time_satisfaction, 0.50)
                for coach in w.coaches.values():
                    self.assertLessEqual(abs(coach.evaluations[pid].skill_estimate - level), 0.0601)
            if w.coaches["c1"].evaluations["p3"].skill_estimate <= w.players["p3"].composite_skill() - 0.08:
                under += 1
        self.assertEqual(under, 0)

    def test_academy_and_buyer_archetypes_differ_without_naming_players(self):
        w = sampled(100)
        academy = [w.players[p].rates["A"] for c in le.ACADEMY_CLUB_IDS for p in le.INITIAL_CLUB_ROSTERS[c]]
        buyer = [w.players[p].rates["A"] for c in le.BUYER_CLUB_IDS for p in le.INITIAL_CLUB_ROSTERS[c]]
        self.assertGreater(min(academy), max(buyer))

    def test_sampled_world_runs_saves_and_resumes_exactly(self):
        full = sampled(100, days=28)
        half = sampled(100, days=14)
        with tempfile.TemporaryDirectory() as tmp:
            le.save_league_artifacts(half, tmp)
            loaded = le.load_league_snapshot(Path(tmp) / "snapshot.json")
        le.advance_league_days(loaded, 14)
        self.assertEqual(loaded, full)
        self.assertEqual(loaded.config.population_mode, "sampled")

    def test_snapshot_population_mode_cannot_be_silently_flipped(self):
        w = sampled(100, days=3)
        with tempfile.TemporaryDirectory() as tmp:
            le.save_league_artifacts(w, tmp)
            path = Path(tmp) / "snapshot.json"
            text = path.read_text().replace('"population_mode": "sampled"', '"population_mode": "fixed"')
            path.write_text(text)
            with self.assertRaises(ValueError):
                le.load_league_snapshot(path)


if __name__ == "__main__":
    unittest.main()
