import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from soccer_sim.experiment_t002 import clipping_probe, estimate_observed_gains
from soccer_sim.experiment_t004 import (
    CONDITIONS, SIMPLE_PAIRS, compare_seed_set, gain_choice_probe, gain_metrics, no_noise_probe,
)
from soccer_sim.observation import signal
from soccer_sim.simulation import advance, new_world
from soccer_sim.storage import event_lines, load_snapshot, snapshot, write_snapshot

ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS = ROOT / "artifacts"
LEGACY = ARTIFACTS / "t003"
SIGNED = CONDITIONS["signed_adaptive"]


class T004Tests(unittest.TestCase):
    def test_signed_and_clipped_update_on_same_positive_negative_and_zero_observations(self):
        rising = estimate_observed_gains([(0.4, 0.6)] * 64, 0.5)
        falling = estimate_observed_gains([(0.6, 0.4)] * 64, 0.5)
        still = estimate_observed_gains([(0.4, 0.4)] * 64, 0.5)
        self.assertAlmostEqual(rising["clipped"], 0.2)
        self.assertEqual(rising["signed"], rising["clipped"])
        self.assertAlmostEqual(falling["signed"], -0.2)
        self.assertEqual(falling["clipped"], 0)
        self.assertEqual(still, {"clipped": 0, "signed": 0})

    def test_same_experience_changes_scores_and_choice_through_gain_only(self):
        results = gain_choice_probe()["results"]
        self.assertEqual(results["clipped"]["decision"]["chosen"]["action"], "A")
        self.assertEqual(results["signed"]["decision"]["chosen"]["action"], "B")
        clipped, signed = (results[mode]["decision"]["candidates"] for mode in ("clipped", "signed"))
        self.assertEqual(clipped[0], signed[0])
        self.assertEqual(clipped[2], signed[2])
        self.assertAlmostEqual(clipped[1]["terms"]["expected_gain"], 0.025)
        self.assertAlmostEqual(signed[1]["terms"]["expected_gain"], -0.025)

    def test_trust_feedback_uses_negative_prior_gain_before_update(self):
        world = advance(new_world(17, SIGNED), 84)
        negative_feedback = [e for e in world.events if e.kind == "advice_feedback"
                             and e.data["q_self_before_action"] < 0]
        self.assertTrue(negative_feedback)
        updates = {(e.day, e.target): e for e in world.events if e.kind == "player_gain_belief"}
        for event in negative_feedback:
            row = event.data
            prior = world.events[row["self_gain_memory_id"] - 1]
            updated = updates[event.day, event.target]
            self.assertIn(prior.event_id, event.causes)
            self.assertLess(prior.day, event.day)
            self.assertLess(event.event_id, updated.event_id)
            self.assertEqual(row["q_self_before_action"], prior.data["expected_after"])
            self.assertEqual(row["q_self_before_action"], updated.data["expected_before"])
            self.assertEqual(row["self_loss"],
                             (row["q_self_before_action"] - row["signed_observed_gain"]) ** 2)

    def test_all_training_records_use_the_two_private_observations_and_pre_action_score(self):
        world = advance(new_world(17, SIGNED), 84)
        records = gain_metrics(world)
        for player_id, record in records.items():
            actions = [e for e in world.events if e.kind == "player_action" and e.actor == player_id
                       and e.data["action"] != "rest"]
            self.assertEqual(len(actions), record["training_count"])
            for row, action in zip(record["gain_experiences"], actions, strict=True):
                event = world.events[row["gain_event_id"] - 1]
                causes = [world.events[cause - 1] for cause in event.causes]
                self.assertEqual((row["day"], row["method"]), (action.day, action.data["action"]))
                before, after = [e.data["skill_signal"] for e in causes if e.kind == "self_observation"]
                decision = world.events[action.causes[0] - 1]
                expected = decision.data["chosen"]["terms"]["expected_gain"]
                self.assertEqual(row["prediction_before"], expected)
                self.assertEqual(row["signed_observed_gain"], after - before)
                self.assertEqual(row["signed_error"], expected - (after - before))
                self.assertEqual(row["squared_error"], row["signed_error"] ** 2)

    def test_same_private_inputs_hide_true_rates_and_cap_from_gain_and_scoring(self):
        def private_signal(value, amplitude, seed, channel, day, player_id):
            observed, key = signal(value, amplitude, seed, channel, day, player_id)
            return {"self-pre": 0.44, "self-post": 0.34}.get(channel, observed), key

        base = new_world(17, SIGNED)
        changed = new_world(17, SIGNED)
        changed.players["p1"].rates = (0.40, 0.30)
        changed.players["p1"].cap = 0.90
        with patch("soccer_sim.observation.signal", side_effect=private_signal):
            advance(base, 1)
            advance(changed, 1)
        self.assertNotEqual(base.players["p1"].skill, changed.players["p1"].skill)
        self.assertEqual(base.players["p1"].mind.expected_gains, changed.players["p1"].mind.expected_gains)
        self.assertTrue(any(gain < 0 for gain in changed.players["p1"].mind.expected_gains))
        subjective = {"decision", "player_gain_belief", "advice_feedback"}
        self.assertEqual([e for e in base.events if e.kind in subjective],
                         [e for e in changed.events if e.kind in subjective])

    def test_four_conditions_resume_at_42_days_with_negative_gains_intact(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "snapshot.json"
            full_path = Path(folder) / "full.json"
            for name, config in CONDITIONS.items():
                with self.subTest(condition=name):
                    full = advance(new_world(17, config), 84)
                    first = advance(new_world(17, config), 42)
                    write_snapshot(path, first)
                    resumed = load_snapshot(path)
                    self.assertEqual(snapshot(first), snapshot(resumed))
                    if name.startswith("signed"):
                        self.assertTrue(any(gain < 0 for p in resumed.players.values()
                                            for gain in p.mind.expected_gains))
                    advance(resumed, 42)
                    write_snapshot(path, resumed)
                    write_snapshot(full_path, full)
                    self.assertEqual(path.read_bytes(), full_path.read_bytes())
                    self.assertEqual(b"".join(event_lines(full)), b"".join(event_lines(resumed)))

    def test_snapshot_rejects_invalid_mode_and_setup_or_gain_event_mismatch(self):
        world = advance(new_world(17, SIGNED), 1)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "snapshot.json"
            for operation, error in (("invalid", "gain target mode"), ("missing", "belief controls"),
                                     ("config_mismatch", "belief controls"),
                                     ("event_mismatch", "belief controls")):
                with self.subTest(operation=operation):
                    write_snapshot(path, world)
                    data = json.loads(path.read_bytes())
                    controls = data["config"]["beliefs"]
                    if operation == "invalid":
                        controls["gain_target_mode"] = "unknown"
                    elif operation == "missing":
                        del controls["gain_target_mode"]
                    elif operation == "config_mismatch":
                        controls["gain_target_mode"] = "clipped"
                    else:
                        gain_event = next(e for e in data["events"] if e["kind"] == "player_gain_belief")
                        gain_event["data"]["gain_target_mode"] = "clipped"
                    path.write_text(json.dumps(data))
                    with self.assertRaisesRegex(ValueError, error):
                        load_snapshot(path)

    def test_snapshot_rejects_bad_gain_and_negative_world_skill_or_rates(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "snapshot.json"
            for name, field, bad_value in (
                ("signed_adaptive", "gain", -1.01), ("signed_adaptive", "gain", 1.01),
                ("signed_adaptive", "gain", float("nan")), ("signed_adaptive", "gain", float("inf")),
                ("clipped_fixed", "gain", -0.01), ("signed_adaptive", "skill", -0.1),
                ("signed_adaptive", "rate", -0.1),
            ):
                with self.subTest(condition=name, field=field, value=bad_value):
                    write_snapshot(path, new_world(17, CONDITIONS[name]))
                    data = json.loads(path.read_bytes())
                    player = data["players"]["p1"]
                    if field == "gain":
                        player["mind"]["expected_gains"][0] = bad_value
                    elif field == "skill":
                        player["skill"] = bad_value
                    else:
                        player["rates"][0] = bad_value
                    path.write_text(json.dumps(data))
                    with self.assertRaisesRegex(ValueError, "invalid state|invalid JSON number"):
                        load_snapshot(path)

    def test_zero_noise_and_zero_true_gain_controls(self):
        no_noise = no_noise_probe()
        for result in no_noise["comparisons"].values():
            self.assertTrue(result["same_snapshot_excluding_mode"])
            self.assertEqual(set(result["action_disagreement"].values()), {0.0})
        zero_gain = clipping_probe(skill=0.5)
        self.assertEqual(len(zero_gain["runs"]), 1000)
        self.assertEqual(zero_gain["steps"], 64)
        values = zero_gain["final_across_seeds"]
        self.assertGreater(values["clipped"]["mean"], 0.008)
        self.assertLess(abs(values["signed"]["mean"]), 0.001)

    def test_action_disagreement_compares_each_registered_pair_directly(self):
        seed = 17
        result = compare_seed_set([seed])
        self.assertEqual(set(result["action_disagreement"]), set(SIMPLE_PAIRS))
        self.assertTrue(all("disagreement" not in metric
                            for row in result["paired_effects"].values() for metric in row))
        for label, (left, right) in SIMPLE_PAIRS.items():
            with self.subTest(pair=label):
                left_actions, right_actions = [
                    result["conditions"][condition]["runs"][0]["actions"] for condition in (left, right)
                ]
                for actor in left_actions:
                    self.assertTrue(left_actions[actor])
                    mismatches = sum(a != b for a, b in zip(
                        left_actions[actor], right_actions[actor], strict=True))
                    rate = mismatches / len(left_actions[actor])
                    self.assertEqual(result["action_disagreement"][label][actor]["per_seed"], {seed: rate})
        self.assertGreater(result["action_disagreement"]["signed_minus_clipped_fixed"]["player"]["mean"], 0)

    def test_signed_adaptive_long_run_has_valid_budgets_references_and_bounds(self):
        world = advance(new_world(17, SIGNED), 1000)
        world.validate_state()
        closes = [e for e in world.events if e.kind == "day_summary"]
        self.assertEqual([e.day for e in closes], list(range(1, 1001)))
        for event in closes:
            self.assertEqual(event.data["coach_slots"], 2)
            self.assertEqual(event.data["player_slots"], {"p1": 1, "p2": 1})
        for event in world.events:
            self.assertTrue(all(1 <= cause < event.event_id for cause in event.causes))
            if event.kind == "player_gain_belief":
                self.assertLessEqual(-1, event.data["expected_after"])
                self.assertLessEqual(event.data["expected_after"], 1)
            if event.kind == "state_update":
                self.assertGreaterEqual(event.data["skill_gain"], 0)

    def test_legacy_t003_reader_replays_and_artifacts_are_unchanged(self):
        first = LEGACY / "representative" / "first_half" / "snapshot.json"
        with self.assertRaisesRegex(ValueError, "incompatible"):
            load_snapshot(first)
        with tempfile.TemporaryDirectory() as folder:
            subprocess.run([sys.executable, "-B", "-m", "soccer_sim", "resume", "--snapshot", str(first),
                            "--days", "42", "--out", folder], cwd=LEGACY / "source_bundle",
                           capture_output=True, text=True, check=True)
            for filename in ("snapshot.json", "events.jsonl"):
                self.assertEqual((Path(folder) / filename).read_bytes(),
                                 (LEGACY / "representative" / "full" / filename).read_bytes())
        for label in ("t001", "t002", "t003"):
            with self.subTest(bundle=label):
                inventory = ARTIFACTS / "t004" / "preflight" / f"{label}-inventory.json"
                expected = json.loads(inventory.read_text())
                frozen = ARTIFACTS / label
                actual = []
                for path in sorted(frozen.rglob("*")):
                    if path.is_file():
                        data = path.read_bytes()
                        actual.append({"path": str(path.relative_to(frozen)), "bytes": len(data),
                                       "sha256": hashlib.sha256(data).hexdigest()})
                self.assertEqual(expected, sorted(actual, key=lambda row: row["path"]))
