import hashlib
import inspect
import json
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from soccer_sim.advice import evaluate_feedback
from soccer_sim.experiment_t003 import (
    ADAPTIVE, CONDITIONS, GAIN_ONLY_CONTRAST, compare_seed_set, trust_choice_probe,
)
from soccer_sim.observation import signal
from soccer_sim.policy import decide_player
from soccer_sim.simulation import advance, new_world, record_advice_feedback
from soccer_sim.state import PlayerObservation, Proposal, SelfObservation
from soccer_sim.storage import event_lines, load_snapshot, write_snapshot

from test_t002 import numeric_state, projection

ROOT = Path(__file__).resolve().parents[1]
LEGACY = ROOT / "artifacts" / "t002"
BUNDLE = LEGACY / "source_bundle"


class T003Tests(unittest.TestCase):
    def test_relative_error_updates_both_directions_and_ties_are_exact(self):
        rules = ADAPTIVE.trust
        positive = evaluate_feedback(0.5, 0.4, 0.5, 0.05, 0.1, rules)
        negative = evaluate_feedback(0.5, 0.4, 0.45, 0.05, 0.1, rules)
        tie = evaluate_feedback(0.5, 0.4, 0.32, 0.05, 0.05, rules)
        self.assertGreater(positive.trust_after, 0.5)
        self.assertLess(negative.trust_after, 0.5)
        self.assertEqual(tie.trust_after, 0.5)
        self.assertEqual(tie.evidence, 0.0)
        self.assertLess(tie.signed_observed_gain, 0)
        high = evaluate_feedback(0.99, 0.4, 0.5, 0.05, 0.1, rules)
        low = evaluate_feedback(0.01, 0.4, 0.45, 0.05, 0.1, rules)
        self.assertEqual((high.trust_after, low.trust_after), (1.0, 0.0))

    def test_refused_absent_and_rest_do_not_evaluate_feedback(self):
        world = new_world(17, ADAPTIVE)
        mind = world.players["p1"].mind
        pre = SelfObservation(8, 0.4, 0.2)
        offered = Proposal(9, "A", 0.1)
        event_count = len(world.events)
        for method, proposal in (("A", None), ("B", offered), ("rest", offered), ("rest", None)):
            with self.subTest(method=method, proposal=proposal):
                result = record_advice_feedback(world, 1, "p1", mind,
                                                PlayerObservation(pre, proposal),
                                                (10, 0.46), (method, 11), 12)
                self.assertIs(result, mind)
                self.assertEqual(len(world.events), event_count)

    def test_feedback_links_to_actual_advice_observations_and_preupdate_gain(self):
        for name in ("adaptive_trust", "adaptive_trust_gain_frozen", "fixed_trust"):
            with self.subTest(condition=name):
                world = advance(new_world(17, CONDITIONS[name]), 84)
                events = {event.event_id: event for event in world.events}
                gain_updates = {(event.day, event.target): event for event in world.events
                                if event.kind == "player_gain_belief"}
                feedback = [event for event in world.events if event.kind == "advice_feedback"]
                accepted = [event for event in world.events if event.kind == "decision"
                            and event.data["proposal_response"] == "accepted"]
                self.assertEqual(len(feedback), len(accepted))
                self.assertTrue(feedback)
                saw_changed_g = False
                for event in feedback:
                    row = event.data
                    causes = [events[ref] for ref in event.causes]
                    links = {cause.kind: cause for cause in causes}
                    post, pre = (next(cause for cause in causes
                                      if cause.kind == "self_observation" and cause.phase == phase)
                                 for phase in ("evening", "morning"))
                    proposal, choice, execution = (links[kind] for kind in (
                        "coach_action", "decision", "player_action"))
                    method = execution.data["action"]
                    self.assertEqual((method, choice.data["chosen"]["action"], proposal.data["method"]),
                                     (row["method"],) * 3)
                    self.assertEqual((event.target, proposal.target, execution.target, pre.target, post.target),
                                     (event.actor,) * 5)
                    self.assertEqual(proposal.data["expected_gain"], row["q_coach_received"])
                    self.assertEqual(proposal.data["expected_gain"],
                                     proposal.data["subjective_rate"] * (1 - proposal.data["perceived_skill"]))
                    self.assertIn(world.coach.forecast_memory_id, proposal.causes)
                    self.assertTrue(any(events[ref].kind in ("initial_assessment", "coach_skill_belief")
                                        for ref in proposal.causes))
                    source = events[row["self_gain_memory_id"]]
                    self.assertIn(source.event_id, event.causes)
                    self.assertLess(source.day, event.day)
                    if source.kind == "initial_player":
                        self.assertEqual(row["q_self_before_action"], 0.046)
                    else:
                        self.assertEqual(source.data["method"], method)
                        self.assertEqual(source.data["expected_after"], row["q_self_before_action"])
                    current_gain = gain_updates.get((event.day, event.target))
                    if current_gain is not None:
                        self.assertLess(event.event_id, current_gain.event_id)
                        self.assertEqual(row["q_self_before_action"], current_gain.data["expected_before"])
                        saw_changed_g |= row["q_self_before_action"] != current_gain.data["expected_after"]
                    self.assertIn(row["prior_trust_memory_id"], event.causes)
                    expected = evaluate_feedback(row["trust_before"], pre.data["skill_signal"],
                                                 post.data["skill_signal"], row["q_self_before_action"],
                                                 row["q_coach_received"], world.config.trust)
                    self.assertEqual((row["signed_observed_gain"], row["trust_after"],
                                      row["self_loss"], row["coach_loss"]),
                                     (expected.signed_observed_gain, expected.trust_after,
                                      expected.self_loss, expected.coach_loss))
                self.assertEqual(saw_changed_g, world.config.beliefs.player_gain)

    def test_fixed_subjective_inputs_hide_true_traits_from_forecasts_and_trust(self):
        reference = new_world(17, ADAPTIVE)
        changed = new_world(17, ADAPTIVE)
        changed.players["p1"].rates = (0.8, 0.01)
        changed.players["p1"].cap = 0.99

        def fixed_post(value, amplitude, seed, channel, day, player_id):
            if channel == "self-post":
                value = 0.44
            return signal(value, amplitude, seed, channel, day, player_id)

        with patch("soccer_sim.observation.signal", side_effect=fixed_post):
            for world in (reference, changed):
                advance(world, 1)

        def subjective_events(world):
            return [event for event in world.events
                    if event.kind in ("coach_action", "decision", "advice_feedback")]
        self.assertEqual(subjective_events(reference), subjective_events(changed))
        self.assertTrue(any(event.kind == "advice_feedback" for event in subjective_events(changed)))
        self.assertNotEqual(reference.players["p1"].skill, changed.players["p1"].skill)

    def test_same_next_observation_after_different_feedback_can_change_choice(self):
        result = trust_choice_probe()
        self.assertEqual(result["first_decision"]["proposal_response"], "accepted")
        closer, worse = (result["results"][label] for label in ("coach_closer", "self_closer"))
        self.assertEqual(closer["next_decision"]["proposal_response"], "accepted")
        self.assertEqual(worse["next_decision"]["proposal_response"], "rejected")
        self.assertGreater(closer["feedback"]["trust_after"], result["initial_trust"])
        self.assertLess(worse["feedback"]["trust_after"], result["initial_trust"])

    def test_proposal_forecast_does_not_directly_change_decision(self):
        world = new_world(17, ADAPTIVE)
        mind = world.players["p1"].mind
        pre = SelfObservation(8, 0.4, 0.2)
        low = decide_player(PlayerObservation(pre, Proposal(9, "A", 0.0)),
                            mind, world.config.policy)
        high = decide_player(PlayerObservation(pre, Proposal(9, "A", 1.0)),
                             mind, world.config.policy)
        self.assertEqual(low, high)
        self.assertIn(mind.trust_memory_id, low.memory_ids)

    def test_trust_ancestry_enters_next_relevant_decision(self):
        world = advance(new_world(17, ADAPTIVE), 84)
        trust_sources = {}
        used_updated_trust = False
        for event in world.events:
            if event.kind == "initial_player":
                trust_sources[event.target] = event.event_id
            if event.kind == "advice_feedback" and event.data["trust_change"] != 0:
                trust_sources[event.target] = event.event_id
            if event.kind == "decision" and event.phase == "choice":
                trust_ref = trust_sources[event.actor]
                refs = event.data["memory_ids"]
                if event.data["proposal_response"] is not None:
                    self.assertIn(trust_ref, refs)
                    used_updated_trust |= world.events[trust_ref - 1].kind == "advice_feedback"
                elif world.events[trust_ref - 1].kind == "advice_feedback":
                    self.assertNotIn(trust_ref, refs)
        self.assertTrue(used_updated_trust)

    def test_four_conditions_resume_identically(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "half.json"
            for name, config in CONDITIONS.items():
                with self.subTest(condition=name):
                    full = advance(new_world(17, config), 84)
                    write_snapshot(path, advance(new_world(17, config), 42))
                    restored = load_snapshot(path)
                    self.assertEqual(restored.config, config)
                    advance(restored, 42)
                    self.assertEqual(full.players, restored.players)
                    self.assertEqual(full.coach, restored.coach)
                    self.assertEqual(list(event_lines(full)), list(event_lines(restored)))
                    write_snapshot(path, full)
                    expected = path.read_bytes()
                    write_snapshot(path, restored)
                    self.assertEqual(expected, path.read_bytes())

    def test_action_disagreement_is_only_compared_against_a_matched_reference(self):
        contrasts = compare_seed_set((17,))["paired_effects"]
        self.assertNotIn("disagreement.player", contrasts[GAIN_ONLY_CONTRAST])
        self.assertIn("disagreement.player", contrasts["adaptive_minus_fixed"])
        self.assertIn("disagreement.player", contrasts["adaptive_minus_fixed_gain_frozen"])
        self.assertIn("disagreement.player", contrasts["trust_gain_interaction"])

    def test_adaptive_long_run_respects_state_budgets_and_event_order(self):
        world = advance(new_world(17, ADAPTIVE), 1000)
        slots = Counter()
        for number, event in enumerate(world.events, start=1):
            self.assertEqual(event.event_id, number)
            self.assertTrue(all(1 <= cause < number for cause in event.causes))
            if event.kind in ("coach_action", "player_action"):
                slots[event.day, event.actor] += event.data["slots"]
        for day in range(1, 1001):
            self.assertEqual((slots[day, "p1"], slots[day, "p2"], slots[day, "coach"]), (1, 1, 2))
        world.validate_state()

    def test_fixed_trust_matches_frozen_t002_numeric_state_and_choices(self):
        code = ("import json\n"
                "from soccer_sim.experiment_t002 import CONDITIONS\n"
                "from soccer_sim.simulation import advance, new_world\n"
                + inspect.getsource(numeric_state) + "\n" + inspect.getsource(projection)
                + "\nprint(json.dumps([projection(new_world(17, CONDITIONS[name]))"
                  " for name in ('baseline', 'player_gain_frozen')]))")
        completed = subprocess.run([sys.executable, "-B", "-c", code], cwd=BUNDLE,
                                   capture_output=True, text=True, check=True)
        old = json.loads(completed.stdout)
        current = [projection(new_world(17, CONDITIONS[name]))
                   for name in ("fixed_trust", "fixed_trust_gain_frozen")]
        self.assertEqual(old, json.loads(json.dumps(current)))

    def test_t002_snapshot_requires_frozen_reader_and_replays(self):
        first_half = LEGACY / "first_half" / "snapshot.json"
        with self.assertRaisesRegex(ValueError, "incompatible"):
            load_snapshot(first_half)
        with tempfile.TemporaryDirectory() as folder:
            subprocess.run([sys.executable, "-B", "-m", "soccer_sim", "resume", "--snapshot",
                            str(first_half), "--days", "14", "--out", folder],
                           cwd=BUNDLE, capture_output=True, text=True, check=True)
            for filename in ("snapshot.json", "events.jsonl"):
                self.assertEqual((Path(folder) / filename).read_bytes(),
                                 (LEGACY / "baseline" / filename).read_bytes())

    def test_trust_settings_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "snapshot.json"
            write_snapshot(path, advance(new_world(17, ADAPTIVE), 2))
            data = json.loads(path.read_bytes())
            data["config"]["trust"]["enabled"] = False
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "trust controls"):
                load_snapshot(path)

    def test_legacy_inventories_are_unchanged(self):
        for label in ("t001", "t002"):
            with self.subTest(label=label):
                artifacts = ROOT / "artifacts"
                frozen = artifacts / label
                inventory = artifacts / "t003" / "preflight" / f"{label}-inventory.json"
                expected = json.loads(inventory.read_text())
                actual = []
                for path in sorted(frozen.rglob("*")):
                    if path.is_file():
                        data = path.read_bytes()
                        actual.append({"path": str(path.relative_to(frozen)), "bytes": len(data),
                                       "sha256": hashlib.sha256(data).hexdigest()})
                self.assertEqual(expected, sorted(actual, key=lambda item: item["path"]))
