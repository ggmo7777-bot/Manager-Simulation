"""T016: initial membership, descriptive never-movers, and evaluation identity."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from soccer_sim import experiment_t011 as experiment
from soccer_sim import league_economy as le


def sampled(days=0):
    return le.run_league_world(
        seed=100, days=days,
        config=le.config_for_condition("open_solidarity_sampled_population"),
    )


class TestT016Cohorts(unittest.TestCase):
    def test_day_zero_includes_all_six_initial_academy_players(self):
        for mode in ("fixed", "sampled"):
            condition = "open_solidarity" + (le.POPULATION_SUFFIX if mode == "sampled" else "")
            world = le.run_league_world(seed=100, days=0, config=le.config_for_condition(condition))
            summary = le.summarize_league_world(world)
            for group in ("academy_initial_cohort", "academy_never_transferred"):
                self.assertEqual(summary[f"{group}_ids"], ["p1", "p2", "p3", "p7", "p8", "p9"])
                self.assertEqual(summary[f"{group}_count"], 6)
                self.assertEqual(summary[f"{group}_skill_gain"], 0.0)
            self.assertEqual(summary["legacy_four_slot_ids"], ["p1", "p2", "p7", "p8"])
            self.assertEqual(summary["evaluation_version"], "t016-initial-cohort-v1")
            self.assertNotIn("academy_retained_skill_gain", summary)

    def test_real_transfer_stays_in_initial_group_and_p3_is_never_mover(self):
        world = sampled(days=84)
        summary = le.summarize_league_world(world)
        self.assertGreater(world.players["p1"].transfers_count, 0)
        self.assertEqual(world.players["p3"].transfers_count, 0)
        self.assertEqual(world.players["p3"].club_id, "club_a")
        self.assertIn("p1", summary["academy_initial_cohort_ids"])
        self.assertNotIn("p1", summary["academy_never_transferred_ids"])
        self.assertIn("p3", summary["academy_never_transferred_ids"])
        self.assertIn("p9", summary["academy_never_transferred_ids"])
        # This explicitly includes p1's realized growth after transfer.
        gains = [
            round(world.players[pid].composite_skill() - world.initial_players[pid].composite_skill(), 6)
            for pid in ("p1", "p2", "p3", "p7", "p8", "p9")
        ]
        self.assertEqual(summary["academy_initial_cohort_skill_gain"], round(sum(gains) / 6, 6))

    def test_initial_club_defines_membership_not_current_or_origin_club(self):
        world = sampled()
        # Evaluation-only counterexample: a buyer's initial player now joins an academy.
        world.players["p4"].club_id = "club_a"
        world.players["p4"].transfers_count = 1
        world.initial_players["p4"].origin_club_id = "club_a"
        # Conversely, a player's origin does not override initial academy membership.
        world.initial_players["p3"].origin_club_id = "club_b"
        summary = le.summarize_academy_cohorts(world)
        self.assertNotIn("p4", summary["academy_initial_cohort_ids"])
        self.assertNotIn("p4", summary["academy_never_transferred_ids"])
        self.assertIn("p3", summary["academy_initial_cohort_ids"])
        self.assertIn("p3", summary["academy_never_transferred_ids"])
        self.assertEqual(summary["academy_initial_cohort_count"], 6)

    def test_returnee_and_changed_club_without_counter_are_both_excluded(self):
        world = sampled()
        world.players["p1"].transfers_count = 2
        self.assertEqual(world.players["p1"].club_id, world.initial_players["p1"].club_id)
        world.players["p2"].club_id = "club_c"
        summary = le.summarize_academy_cohorts(world)
        self.assertEqual(summary["academy_initial_cohort_count"], 6)
        self.assertNotIn("p1", summary["academy_never_transferred_ids"])
        self.assertNotIn("p2", summary["academy_never_transferred_ids"])
        self.assertEqual(summary["academy_never_transferred_count"], 4)

    def test_all_transferred_gives_empty_descriptive_group_not_zero_growth(self):
        world = sampled()
        for pid in ("p1", "p2", "p3", "p7", "p8", "p9"):
            world.players[pid].transfers_count = 1
        summary = le.summarize_academy_cohorts(world)
        self.assertEqual(summary["academy_initial_cohort_count"], 6)
        self.assertEqual(summary["academy_never_transferred_ids"], [])
        self.assertEqual(summary["academy_never_transferred_count"], 0)
        self.assertIsNone(summary["academy_never_transferred_skill_gain"])

    def test_empty_initial_academy_group_also_has_null_mean(self):
        world = sampled()
        for initial in world.initial_players.values():
            initial.club_id = "club_b"
        summary = le.summarize_academy_cohorts(world)
        for group in ("academy_initial_cohort", "academy_never_transferred"):
            self.assertEqual(summary[f"{group}_ids"], [])
            self.assertEqual(summary[f"{group}_count"], 0)
            self.assertIsNone(summary[f"{group}_skill_gain"])

    def test_relabeling_players_preserves_membership_and_average_growth(self):
        world = sampled()
        # Distinct, known growth for all six members, including a transferred member.
        for index, pid in enumerate(("p1", "p2", "p3", "p7", "p8", "p9")):
            world.initial_players[pid].traits = le.MatchTraits(0.4, 0.4, 0.4)
            value = 0.41 + index * 0.01
            world.players[pid].traits = le.MatchTraits(value, value, value)
        world.players["p1"].transfers_count = 1
        original = le.summarize_academy_cohorts(world)
        self.assertEqual(original["academy_initial_cohort_skill_gain"], 0.035)
        self.assertEqual(original["academy_never_transferred_skill_gain"], 0.04)
        renamed = deepcopy(world)
        labels = {pid: f"renamed-{index:02}" for index, pid in enumerate(reversed(list(world.players)))}
        for field in ("players", "initial_players"):
            records = getattr(renamed, field)
            for pid, record in records.items():
                record.player_id = labels[pid]
            setattr(renamed, field, {labels[pid]: record for pid, record in records.items()})
        actual = le.summarize_academy_cohorts(renamed)
        for group in ("academy_initial_cohort", "academy_never_transferred"):
            self.assertEqual(actual[f"{group}_ids"], sorted(labels[pid] for pid in original[f"{group}_ids"]))
            self.assertEqual(actual[f"{group}_count"], original[f"{group}_count"])
            self.assertEqual(actual[f"{group}_skill_gain"], original[f"{group}_skill_gain"])

    def test_summary_is_non_destructive_and_does_not_alias_world(self):
        world = sampled(days=1)
        before = deepcopy(world)
        summary = le.summarize_league_world(world)
        self.assertEqual(world, before)
        summary["academy_initial_cohort_ids"].clear()
        summary["academy_never_transferred_ids"].clear()
        summary["legacy_four_slot_ids"].clear()
        self.assertEqual(world, before)
        self.assertEqual(le.summarize_league_world(world)["academy_initial_cohort_count"], 6)

    def test_summary_and_manifest_survive_save_and_resume(self):
        world = sampled(days=28)
        with tempfile.TemporaryDirectory() as tmp:
            saved = le.save_league_artifacts(world, tmp)
            summary = json.loads((Path(tmp) / "summary.json").read_text())
            manifest = json.loads((Path(tmp) / "manifest.json").read_text())
            self.assertEqual(saved, summary)
            self.assertEqual(manifest["evaluation_version"], le.LEAGUE_EVALUATION_VERSION)
            self.assertEqual(summary["evaluation_version"], le.LEAGUE_EVALUATION_VERSION)
            snapshot = json.loads((Path(tmp) / "snapshot.json").read_text())
            self.assertNotIn("evaluation_version", snapshot)
            resumed = le.load_league_snapshot(Path(tmp) / "snapshot.json")
        self.assertEqual(le.summarize_league_world(resumed), le.summarize_league_world(world))
        le.advance_league_days(resumed, 7)
        full = sampled(days=35)
        self.assertEqual(resumed, full)
        self.assertEqual(le.summarize_league_world(resumed), le.summarize_league_world(full))

    def test_experiment_reports_version_and_explicit_comparison_metrics(self):
        self.assertNotIn("academy_retained_skill_gain", experiment.T011_METRICS)
        self.assertIn("academy_initial_cohort_skill_gain", experiment.T011_METRICS)
        self.assertIn("legacy_four_slot_skill_gain", experiment.T011_METRICS)
        self.assertNotIn("academy_never_transferred_skill_gain", experiment.T011_METRICS)
        # Exercise report serialization without repeating the historical full suite.
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(experiment, "SEED_SETS", {"confirmation": (100,)}), \
                patch.object(experiment, "CONFIRMATION_SEEDS", (100,)), \
                patch.object(experiment, "_long_horizon_stress", return_value={}):
            report = experiment.run_t011_suite(tmp, days=0)
            comparison = json.loads((Path(tmp) / "comparison.json").read_text())
        self.assertEqual(report["evaluation_version"], le.LEAGUE_EVALUATION_VERSION)
        self.assertEqual(comparison["evaluation_version"], le.LEAGUE_EVALUATION_VERSION)
        for row in comparison["runs"]:
            self.assertEqual(row["summary"]["evaluation_version"], le.LEAGUE_EVALUATION_VERSION)
            self.assertNotIn("academy_retained_skill_gain", row["summary"])


if __name__ == "__main__":
    unittest.main()
