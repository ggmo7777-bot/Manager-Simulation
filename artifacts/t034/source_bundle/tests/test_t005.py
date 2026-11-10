import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import patch

from soccer_sim.advice import COACH_RATE_PRIOR, coach_experience, coach_forecast, remember_coach_experience
from soccer_sim.experiment_t005 import (
    CONDITIONS, SIMPLE_PAIRS, compare_seed_set, control_probe, forecast_choice_probe,
)
from soccer_sim.observation import signal
from soccer_sim.simulation import advance, new_world
from soccer_sim.state import CoachAdjustment, Config
from soccer_sim.storage import event_lines, load_snapshot, snapshot, write_snapshot

ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS = ROOT / "artifacts"
LEARNING = CONDITIONS["coach_learning_signed"]


class T005Tests(unittest.TestCase):
    def test_fixed_inputs_update_only_the_observed_method_and_keep_original_base(self):
        prior = CoachAdjustment("p1", (0.0, 0.0), (6, 6))
        forecast = coach_forecast("B", 0.4, COACH_RATE_PRIOR, prior)
        self.assertEqual(forecast.base_gain, 0.06)
        for after in (0.25, 0.5, 0.75):
            with self.subTest(after=after):
                experience = coach_experience(0.5, after, forecast, True)
                self.assertEqual(experience["signed_observed_gain"], after - 0.5)
                residual = (after - 0.5 - 0.06) / 2
                self.assertEqual(experience["residual_after"], residual)
                learned = remember_coach_experience(prior, "B", residual, 8)
                self.assertEqual(learned.residuals, (0, residual))
                self.assertEqual(learned.memory_ids, (6, 8))
                self.assertEqual(coach_forecast("A", 0.4, COACH_RATE_PRIOR, learned),
                                 coach_forecast("A", 0.4, COACH_RATE_PRIOR, prior))
                self.assertEqual(coach_experience(0.5, after, forecast, False)["residual_after"], 0)
        low = replace(prior, residuals=(-2.0, 0.0))
        high = replace(prior, residuals=(1.0, 0.0))
        self.assertEqual(coach_forecast("A", 0.4, COACH_RATE_PRIOR, low).expected_gain, -1.0)
        self.assertEqual(coach_forecast("A", 0.4, COACH_RATE_PRIOR, high).expected_gain, 1.0)

    def test_default_has_morning_samples_only_and_requires_observation_for_learning(self):
        world = advance(new_world(17), 14)
        self.assertFalse(world.config.beliefs.coach_session_observation)
        self.assertFalse(world.config.beliefs.coach_forecast_learning)
        self.assertTrue(any(e.kind == "coach_observation" for e in world.events))
        self.assertFalse(any(e.kind in ("coach_post_observation", "coach_method_experience")
                             for e in world.events))
        config = Config()
        with self.assertRaisesRegex(ValueError, "requires session observation"):
            new_world(17, replace(config, beliefs=replace(config.beliefs, coach_forecast_learning=True)))

    def test_each_paid_slot_observes_a_pair_and_only_actual_training_is_eligible(self):
        world = advance(new_world(17, LEARNING), 84)
        indexed = lambda events: {(e.day, e.target): e for e in events}
        paid = indexed(e for e in world.events if e.kind == "coach_action" and e.data["action"] == "observe")
        proposals = indexed(e for e in world.events if e.kind == "coach_action" and e.data["action"] == "propose")
        before = indexed(e for e in world.events if e.kind == "coach_observation")
        after = indexed(e for e in world.events if e.kind == "coach_post_observation")
        experiences = indexed(e for e in world.events if e.kind == "coach_method_experience")
        performed = indexed(e for e in world.events if e.kind == "player_action")
        self.assertEqual(paid.keys(), before.keys())
        self.assertEqual(paid.keys(), after.keys())
        self.assertLess(len(paid), len(performed))
        self.assertEqual(experiences.keys(), {key for key in paid if performed[key].data["action"] != "rest"})
        self.assertTrue(set(paid) - set(experiences))
        refused = 0
        for key, post in after.items():
            pre = before[key]
            self.assertEqual(paid[key].data["slots"], 1)
            self.assertIn(paid[key].event_id, pre.causes)
            self.assertIn(pre.event_id, post.causes)
            self.assertIn(performed[key].event_id, post.causes)
            self.assertEqual(pre.data["skill_key"], ["coach-skill", *key, 0])
            self.assertEqual(post.data["skill_key"], ["coach-skill-post", *key, 0])
            self.assertEqual(post.data["performed_action"], performed[key].data["action"])
            if key in experiences:
                row = experiences[key].data
                self.assertEqual(row["method"], performed[key].data["action"])
                self.assertEqual(row["signed_observed_gain"], post.data["skill_signal"] - pre.data["skill_signal"])
                if key in proposals and proposals[key].data["method"] != row["method"]:
                    decision = world.events[performed[key].causes[0] - 1]
                    self.assertEqual(decision.data["proposal_response"], "rejected")
                    refused += 1
        self.assertGreater(refused, 0)

    def test_forecasts_use_reservation_beliefs_and_future_proposal_links_to_update(self):
        world = advance(new_world(17, LEARNING), 84)
        lookup = {e.event_id: e for e in world.events}
        latest = {}
        observed = {}
        saw_updated_proposal = False
        saw_same_day_prior = False
        proposed = {}
        for event in world.events:
            row = event.data
            if event.kind == "initial_coach_adjustment":
                latest[event.target, "A"] = latest[event.target, "B"] = event
            if event.kind == "coach_observation":
                observed[event.day, event.target] = event
                for method, forecast in row["forecasts"].items():
                    source = latest[event.target, method]
                    self.assertEqual(forecast["residual_memory_id"], source.event_id)
                    self.assertIn(source.event_id, event.causes)
                    self.assertEqual(forecast["base_gain"], 0.1 * (1.0 - row["perceived_skill"]))
            if event.kind == "coach_action" and row["action"] == "propose":
                source = latest[event.target, row["method"]]
                self.assertEqual(row["residual_memory_id"], source.event_id)
                self.assertIn(source.event_id, event.causes)
                self.assertLess(source.day, event.day)
                proposed[event.day, event.target] = event
                saw_updated_proposal |= source.kind == "coach_method_experience"
            if event.kind == "coach_method_experience":
                pre = observed[event.day, event.target]
                forecast = pre.data["forecasts"][row["method"]]
                self.assertEqual({key: row[key] for key in forecast}, forecast)
                self.assertEqual(row["base_squared_error"],
                                 (row["signed_observed_gain"] - forecast["base_gain"]) ** 2)
                self.assertEqual(row["forecast_squared_error"],
                                 (row["signed_observed_gain"] - forecast["expected_gain"]) ** 2)
                proposal = proposed.get((event.day, event.target))
                if proposal and proposal.data["method"] == row["method"]:
                    self.assertEqual(proposal.data["residual_memory_id"], forecast["residual_memory_id"])
                    self.assertLess(proposal.event_id, event.event_id)
                    saw_same_day_prior = True
                latest[event.target, row["method"]] = event
            if event.kind == "advice_feedback":
                proposal = next(lookup[cause] for cause in event.causes
                                if lookup[cause].kind == "coach_action")
                self.assertEqual(row["q_coach_received"], proposal.data["expected_gain"])
        self.assertTrue(saw_updated_proposal)
        self.assertTrue(saw_same_day_prior)

    def test_changing_true_traits_and_player_signal_keeps_same_private_coach_update(self):
        base = advance(new_world(17, LEARNING), 2)
        changed = advance(new_world(17, LEARNING), 2)
        changed.players["p1"].rates = (0.40, 0.30)
        changed.players["p1"].cap = 0.95

        def coach_signal(value, amplitude, seed, channel, day, player_id):
            private = {"coach-skill": 0.48, "coach-skill-post": 0.44}[channel]
            return private, (channel, day, player_id, 0)

        def player_signal(value, amplitude, seed, channel, day, player_id):
            default, key = signal(value, amplitude, seed, channel, day, player_id)
            return {"self-pre": 0.62, "self-post": 0.72}.get(channel, default), key

        with patch("soccer_sim.simulation.signal", side_effect=coach_signal):
            advance(base, 1)
            with patch("soccer_sim.observation.signal", side_effect=player_signal):
                advance(changed, 1)
        self.assertNotEqual(base.players["p1"].skill, changed.players["p1"].skill)
        self.assertNotEqual(base.players["p1"].mind.expected_gains, changed.players["p1"].mind.expected_gains)
        self.assertEqual(base.coach.adjustments, changed.coach.adjustments)
        coach_kinds = {"coach_action", "coach_observation", "coach_post_observation", "coach_method_experience"}
        def evidence(world):
            return [(event.kind, event.target, event.data) for event in world.events
                    if event.day == 3 and event.kind in coach_kinds]
        self.assertTrue(any(kind == "coach_method_experience" for kind, _, _ in evidence(base)))
        self.assertEqual(evidence(base), evidence(changed))

    def test_learning_changes_forecast_feedback_and_next_choice_in_fixed_fixture(self):
        results = forecast_choice_probe()["results"]
        frozen, learned = results["frozen"], results["learning"]
        self.assertEqual(frozen["first_decision"], learned["first_decision"])
        self.assertEqual(frozen["first_decision"]["proposal_response"], "accepted")
        self.assertEqual(frozen["later_forecast"]["expected_gain"], 0.06)
        self.assertEqual(learned["later_forecast"]["expected_gain"], 0)
        self.assertAlmostEqual(frozen["player_feedback"]["trust_after"], 0.448)
        self.assertEqual(learned["player_feedback"]["trust_after"], 0.52)
        self.assertEqual(frozen["next_decision"]["proposal_response"], "rejected")
        self.assertEqual(learned["next_decision"]["proposal_response"], "accepted")

    def test_fixed_trust_and_no_noise_controls(self):
        fixed = control_probe(fixed_trust=True)["comparisons"]
        for name in ("learning_minus_frozen_clipped", "learning_minus_frozen_signed"):
            pair = fixed[name]
            self.assertEqual(set(pair["action_disagreement"].values()), {0.0})
            self.assertTrue(pair["same_truth_history"])
            self.assertTrue(pair["same_decisions_and_scores"])
            self.assertFalse(pair["same_proposal_forecasts"])
        noise_free = control_probe(no_noise=True)["comparisons"]
        for name in ("signed_minus_clipped_frozen", "signed_minus_clipped_learning"):
            pair = noise_free[name]
            self.assertTrue(pair["same_snapshot_excluding_gain_mode"])
            self.assertEqual(set(pair["action_disagreement"].values()), {0.0})

    def test_held_forecasts_acquire_the_same_session_information_and_match_t004_semantics(self):
        for name in ("coach_frozen_clipped", "coach_frozen_signed"):
            config = CONDITIONS[name]
            morning = replace(config, beliefs=replace(config.beliefs, coach_session_observation=False))
            old = advance(new_world(17, morning), 84)
            session = advance(new_world(17, config), 84)
            def comparable(world):
                return [(e.kind, e.target, e.data) for e in world.events
                        if e.kind in ("player_action", "state_update")]
            self.assertEqual(comparable(old), comparable(session))
            self.assertEqual(old.coach.adjustments, session.coach.adjustments)
            observations = [e for e in session.events if e.kind == "coach_post_observation"]
            self.assertEqual(len(observations), 55)
            for event in session.events:
                if event.kind == "coach_method_experience":
                    row = event.data
                    self.assertFalse(row["updates_enabled"])
                    self.assertEqual(row["residual_after"], 0.0)
                    self.assertEqual(row["base_squared_error"], row["forecast_squared_error"])

    def test_four_conditions_resume_with_corrections_and_references_intact(self):
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
                    changed = any(r != 0 for item in resumed.coach.adjustments for r in item.residuals)
                    self.assertEqual(changed, config.beliefs.coach_forecast_learning)
                    advance(resumed, 42)
                    write_snapshot(path, resumed)
                    write_snapshot(full_path, full)
                    self.assertEqual(path.read_bytes(), full_path.read_bytes())
                    self.assertEqual(b"".join(event_lines(full)), b"".join(event_lines(resumed)))

    def test_snapshot_rejects_malformed_coach_controls_and_adjustments(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "snapshot.json"
            original = advance(new_world(17, LEARNING), 14)
            for operation in ("missing_control", "unpaid_learning", "changed_controls", "missing_adjustments",
                              "high", "low", "nonfinite", "wrong_target", "wrong_method", "wrong_post_method",
                              "wrong_reservation_forecast", "wrong_experience_forecast"):
                with self.subTest(operation=operation):
                    write_snapshot(path, original)
                    data = json.loads(path.read_bytes())
                    beliefs = data["config"]["beliefs"]
                    if operation == "missing_control":
                        del beliefs["coach_session_observation"]
                    elif operation == "unpaid_learning":
                        beliefs["coach_session_observation"] = False
                    elif operation == "changed_controls":
                        beliefs["coach_forecast_learning"] = False
                    elif operation == "missing_adjustments":
                        del data["coach"]["adjustments"]
                    elif operation in ("high", "low", "nonfinite"):
                        values = {"high": 1.01, "low": -2.01, "nonfinite": float("inf")}
                        data["coach"]["adjustments"][0]["residuals"][0] = values[operation]
                    elif operation == "wrong_target":
                        data["coach"]["adjustments"][0]["memory_ids"][0] = data["coach"]["adjustments"][1]["memory_ids"][0]
                    elif operation == "wrong_method":
                        item = data["coach"]["adjustments"][0]
                        item["memory_ids"][1] = item["memory_ids"][0]
                    elif operation == "wrong_post_method":
                        event = next(e for e in data["events"] if e["kind"] == "coach_post_observation")
                        event["data"]["performed_action"] = "rest" if event["data"]["performed_action"] != "rest" else "A"
                    elif operation == "wrong_reservation_forecast":
                        event = next(e for e in data["events"] if e["kind"] == "coach_observation")
                        event["data"]["forecasts"]["A"]["expected_gain"] = 0.99
                    else:
                        event = next(e for e in data["events"] if e["kind"] == "coach_method_experience")
                        event["data"]["base_squared_error"] = 0.99
                    path.write_text(json.dumps(data))
                    with self.assertRaises(ValueError):
                        load_snapshot(path)

    def test_action_disagreement_is_direct_and_errors_have_separate_scopes(self):
        result = compare_seed_set([17])
        self.assertEqual(set(result["action_disagreement"]), set(SIMPLE_PAIRS))
        for label, (left, right) in SIMPLE_PAIRS.items():
            a, b = (result["conditions"][name]["runs"][0]["actions"] for name in (left, right))
            for actor in a:
                mismatch = sum(x != y for x, y in zip(a[actor], b[actor], strict=True)) / len(a[actor])
                self.assertEqual(result["action_disagreement"][label][actor]["per_seed"], {17: mismatch})
        effect = result["paired_effects"]["learning_minus_frozen_signed"]
        self.assertIn("coach.p1.observed_forecast_mse", effect)
        self.assertIn("p1.mean_coach_squared_error", effect)
        self.assertFalse(any("disagreement" in metric
                             for row in result["paired_effects"].values() for metric in row))

    def test_signed_learning_1000_days_preserves_budgets_state_and_event_references(self):
        world = advance(new_world(17, LEARNING), 1000)
        world.validate_state()
        closes = [e for e in world.events if e.kind == "day_summary"]
        self.assertEqual([e.day for e in closes], list(range(1, 1001)))
        for e in closes:
            self.assertEqual(e.data["coach_slots"], 2)
            self.assertEqual(e.data["player_slots"], {"p1": 1, "p2": 1})
        for e in world.events:
            self.assertTrue(all(1 <= cause < e.event_id for cause in e.causes))
            if e.kind == "coach_method_experience":
                self.assertLessEqual(-2.0, e.data["residual_after"])
                self.assertLessEqual(e.data["residual_after"], 1.0)

    def test_t004_snapshot_requires_frozen_reader_and_prior_artifacts_are_unchanged(self):
        frozen = ARTIFACTS / "t004"
        first = frozen / "representative" / "first_half" / "snapshot.json"
        with self.assertRaisesRegex(ValueError, "incompatible"):
            load_snapshot(first)
        with tempfile.TemporaryDirectory() as folder:
            subprocess.run([sys.executable, "-B", "-m", "soccer_sim", "resume", "--snapshot", str(first),
                            "--days", "42", "--out", folder], cwd=frozen / "source_bundle",
                           capture_output=True, text=True, check=True)
            for name in ("snapshot.json", "events.jsonl"):
                self.assertEqual((Path(folder) / name).read_bytes(),
                                 (frozen / "representative" / "full" / name).read_bytes())
        for label in ("t001", "t002", "t003", "t004"):
            with self.subTest(task=label):
                expected = json.loads((ARTIFACTS / "t005/preflight" / f"{label}-inventory.json").read_text())
                base = ARTIFACTS / label
                actual = []
                for path in sorted(base.rglob("*")):
                    if path.is_file():
                        data = path.read_bytes()
                        actual.append({"path": str(path.relative_to(base)), "bytes": len(data),
                                       "sha256": hashlib.sha256(data).hexdigest()})
                self.assertEqual(expected, sorted(actual, key=lambda row: row["path"]))
