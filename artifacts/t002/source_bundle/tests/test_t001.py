import json
import tempfile
import unittest
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

from soccer_sim.experiments import experience_probe, growth_probe, trust_probe
from soccer_sim.policy import decide_player
from soccer_sim.simulation import (
    COACH_SLOTS, PLAYER_SLOTS, advance, check_coach_action, new_world, resolve_training,
)
from soccer_sim.state import Config, PlayerMind, PlayerObservation, SelfObservation
from soccer_sim.storage import digest, load_snapshot, snapshot, write_snapshot


class T001Tests(unittest.TestCase):
    def test_same_seed_and_checkpoint_are_exact(self):
        full = advance(new_world(17), 28)
        repeated = advance(new_world(17), 28)
        with tempfile.TemporaryDirectory() as out:
            path = Path(out) / "snapshot.json"
            write_snapshot(path, advance(new_world(17), 14))
            resumed = advance(load_snapshot(path), 14)
        for world in (repeated, resumed):
            self.assertEqual(world.players, full.players)
            self.assertEqual(world.coach, full.coach)
            self.assertEqual(world.events, full.events)
            self.assertEqual(digest(snapshot(world)), digest(snapshot(full)))

    def test_unknown_versions_and_invalid_cause_are_rejected(self):
        with tempfile.TemporaryDirectory() as out:
            path = Path(out) / "snapshot.json"
            write_snapshot(path, advance(new_world(17), 2))
            original = path.read_bytes()
            for field in ("save", "rules", "policy", "rng", "source_sha256"):
                with self.subTest(field=field):
                    data = json.loads(original)
                    data["versions"][field] = "unknown"
                    path.write_text(json.dumps(data))
                    with self.assertRaisesRegex(ValueError, "incompatible"):
                        load_snapshot(path)
            data = json.loads(original)
            data["events"][-1]["causes"] = [data["next_event_id"]]
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "event order or cause"):
                load_snapshot(path)

    def test_daily_budgets_and_valid_event_refs_for_1000_days(self):
        world = advance(new_world(17), 1000)
        by_day = defaultdict(list)
        for number, event in enumerate(world.events, start=1):
            by_day[event.day].append(event)
            self.assertEqual(event.event_id, number)
            self.assertTrue(all(1 <= cause < number for cause in event.causes))
            self.assertEqual(len(event.causes), len(set(event.causes)))
        for day in range(1, 1001):
            events = by_day[day]
            player_actions = [event for event in events if event.kind == "player_action"]
            self.assertCountEqual([event.actor for event in player_actions], ["p1", "p2"])
            self.assertTrue(all(event.data["slots"] == PLAYER_SLOTS for event in player_actions))
            coach_actions = [event for event in events if event.kind == "coach_action"]
            self.assertLessEqual(sum(event.data["slots"] for event in coach_actions), COACH_SLOTS)
            paid_samples = [event for event in events if event.kind == "coach_observation"]
            self.assertCountEqual([event.target for event in paid_samples],
                                  [event.target for event in coach_actions
                                   if event.data["action"] == "observe"])
            for action in ("observe", "propose"):
                targets = [event.target for event in coach_actions if event.data["action"] == action]
                self.assertEqual(len(targets), len(set(targets)))
        world.validate_state()

    def test_invalid_actions_budgets_and_config_are_rejected(self):
        for action, slots in (("unknown", 1), ("A", 2), ("rest", 0), ("B", True)):
            with self.subTest(action=action, slots=slots), self.assertRaises(ValueError):
                resolve_training(0.38, 0.9, (0.14, 0.04), 0.08, action, Config(), slots)
        for choice in (
            ("observe", "p3", None, 2), ("observe", "p1", None, 2),
            ("propose", "p2", "B", 1), ("propose", "p1", "C", 1),
            ("rest", None, None, 0), ("rest", None, None, 3),
        ):
            with self.subTest(choice=choice), self.assertRaises(ValueError):
                check_coach_action(*choice, ("p1", "p2"), {"p1"}, {"p2"})
        with self.assertRaises(ValueError):
            advance(new_world(0), -1)
        with self.assertRaises(ValueError):
            new_world(0, replace(Config(), self_noise=float("nan")))

    def test_hidden_traits_do_not_change_immediate_decisions(self):
        baseline = new_world(17)
        changed = new_world(17)
        changed.players["p1"].rates = (0.8, 0.01)
        changed.players["p2"].cap = 0.96
        for world in (baseline, changed):
            advance(world, 1)
        self.assertEqual([event for event in baseline.events if event.kind == "decision"],
                         [event for event in changed.events if event.kind == "decision"])
        self.assertNotEqual(baseline.players["p1"].skill, changed.players["p1"].skill)

    def test_growth_probe_controls_rate_and_every_other_input(self):
        result = growth_probe()
        self.assertGreater(result["different"][0], result["different"][1])
        self.assertAlmostEqual(*result["equal"], delta=1e-12)

    def test_trust_only_changes_choice_near_boundary(self):
        result = trust_probe()
        self.assertEqual(result["low"]["chosen"]["action"], "A")
        self.assertEqual(result["low"]["proposal_response"], "rejected")
        self.assertEqual(result["high"]["chosen"]["action"], "B")
        self.assertEqual(result["high"]["proposal_response"], "accepted")

    def test_expected_gains_alone_change_choice(self):
        observation = PlayerObservation(SelfObservation(2, 0.4, 0.05), None)
        mind = PlayerMind(0.4, (0.051, 0.05), (0.0, 0.0), 0.4, 0.5, (1, 1), 1)
        self.assertEqual(decide_player(observation, mind, Config().policy).chosen.action, "A")
        switched = replace(mind, expected_gains=(0.049, 0.05))
        self.assertEqual(decide_player(observation, switched, Config().policy).chosen.action, "B")

    def test_experience_changes_choice_only_with_updates(self):
        result = experience_probe()
        self.assertEqual(result["updates"]["A_higher"]["decision"]["chosen"]["action"], "A")
        self.assertEqual(result["updates"]["B_higher"]["decision"]["chosen"]["action"], "B")
        self.assertEqual(result["frozen"]["A_higher"], result["frozen"]["B_higher"])
        self.assertEqual(result["frozen"]["A_higher"]["expected_gains"], (0.05, 0.05))

    def test_beliefs_are_updated_from_obs_and_paid_coach_samples_only(self):
        world = advance(new_world(17), 28)
        for event in world.events:
            if event.kind == "player_gain_belief":
                observations = [world.events[cause - 1] for cause in event.causes
                                if world.events[cause - 1].kind == "self_observation"]
                self.assertEqual([item.phase for item in observations], ["morning", "evening"])
                self.assertTrue(all(item.target == event.target for item in observations))
                measured = max(0.0, observations[1].data["skill_signal"]
                               - observations[0].data["skill_signal"])
                self.assertEqual(event.data["measured_gain"], measured)
                expected = event.data["expected_before"] + world.config.gain_alpha * (
                    measured - event.data["expected_before"])
                self.assertEqual(event.data["expected_after"], expected)
            if event.kind == "player_skill_belief":
                source = world.events[event.causes[-1] - 1]
                self.assertEqual((source.kind, source.phase), ("self_observation", "evening"))
                self.assertEqual(source.target, event.target)
                expected = event.data["skill_before"] + world.config.self_alpha * (
                    source.data["skill_signal"] - event.data["skill_before"])
                self.assertEqual(event.data["skill_after"], expected)
            if event.kind in ("coach_skill_belief", "coach_recency"):
                source = world.events[event.causes[-1] - 1]
                self.assertEqual(source.kind, "coach_observation")
                self.assertEqual(source.data["source"], "paid_sample")
                self.assertEqual(source.target, event.target)
        self.assertTrue(any(event.kind == "coach_skill_belief" for event in world.events))

    def test_growth_traces_through_choice_to_private_inputs_and_coach_decision(self):
        world = advance(new_world(17), 28)
        event_by_id = {event.event_id: event for event in world.events}
        for growth in world.events:
            if growth.kind != "state_update" or growth.data["skill_gain"] <= 0:
                continue
            action = event_by_id[growth.causes[0]]
            choice = event_by_id[action.causes[0]]
            inputs = [event_by_id[cause] for cause in choice.data["observation_ids"]]
            if len(inputs) == 2:
                break
        else:
            self.fail("no training growth with a proposal")
        self.assertEqual(action.kind, "player_action")
        self.assertEqual(choice.kind, "decision")
        self.assertEqual(choice.data["chosen"]["action"], action.data["action"])
        self.assertEqual(choice.data["chosen"]["score"],
                         max(candidate["score"] for candidate in choice.data["candidates"]))
        self.assertEqual(inputs[0].data["source"], "self-pre")
        self.assertEqual(inputs[1].data["action"], "propose")
        self.assertEqual(inputs[1].target, choice.actor)
        self.assertEqual(event_by_id[inputs[1].causes[0]].actor, "coach")
        self.assertTrue(all(event.event_id in choice.causes for event in inputs))
