import hashlib
import inspect
import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from itertools import product
from pathlib import Path

from soccer_sim.experiment_t002 import CONDITIONS, estimate_observed_gains, tie_probe, trajectories
from soccer_sim.simulation import advance, new_world, update_coach_beliefs, update_player_beliefs
from soccer_sim.state import BeliefRules, Config, SelfObservation
from soccer_sim.storage import load_snapshot, write_snapshot

ROOT = Path(__file__).resolve().parents[1]
LEGACY = ROOT / "artifacts" / "t001"
BUNDLE = LEGACY / "source_bundle"


def numeric_state(world):
    return [
        [(p.player_id, p.skill, p.fatigue, p.cap, p.rates, p.mind.skill_estimate,
          p.mind.expected_gains, p.mind.trust, p.mind.preferences, p.mind.autonomy)
         for p in world.players.values()],
        [(a.player_id, a.skill_estimate, a.last_seen) for a in world.coach.assessments],
    ]


def projection(world):
    states = [numeric_state(world)]
    for _ in range(28):
        advance(world, 1)
        states.append(numeric_state(world))
    events = []
    for event in world.events:
        if event.kind == "decision":
            events.append((event.day, event.actor, event.data["chosen"], event.data["candidates"],
                           event.data["proposal_response"]))
    return {"states": states, "choices_and_scores": events}


class T002Tests(unittest.TestCase):
    def test_all_flag_combinations_are_independent_with_identical_observations(self):
        for flags in product((False, True), repeat=4):
            g, s, c, r = flags
            with self.subTest(g=g, s=s, c=c, r=r):
                world = new_world(17, replace(Config(), beliefs=BeliefRules(*flags)))
                player = world.players["p1"]
                mind = player.mind
                prior = world.coach.assessments[0]
                pre = world.record(1, "morning", "self_observation", "p1", "p1")
                sample = world.record(1, "coach", "coach_observation", "coach", "p1")
                decision = world.record(1, "choice", "decision", "p1", "p1")
                post = world.record(1, "evening", "self_observation", "p1", "p1")
                update_player_beliefs(world, 1, player, SelfObservation(pre, 0.4, 0.08),
                                      (post, 0.52), ("A", decision))
                assessment = update_coach_beliefs(world, 1, prior, (sample, 0.58))
                world.coach = replace(world.coach,
                                      assessments=(assessment, world.coach.assessments[1]))
                world.day = 1
                world.validate_state()
                result = player.mind
                self.assertAlmostEqual(result.expected_gains[0],
                                       0.083 if g else mind.expected_gains[0])
                self.assertEqual(result.expected_gains[1], mind.expected_gains[1])
                self.assertEqual(result.gain_memory_ids[1], mind.gain_memory_ids[1])
                self.assertAlmostEqual(result.skill_estimate, 0.421 if s else mind.skill_estimate)
                self.assertAlmostEqual(assessment.skill_estimate, 0.4448 if c else prior.skill_estimate)
                self.assertEqual(assessment.last_seen, 1 if r else prior.last_seen)
                checks = (
                    (g, result.gain_memory_ids[0], mind.gain_memory_ids[0],
                     "player_gain_belief", (pre, post, decision)),
                    (s, result.skill_memory_id, mind.skill_memory_id, "player_skill_belief", (post,)),
                    (c, assessment.skill_memory_id, prior.skill_memory_id,
                     "coach_skill_belief", (sample,)),
                    (r, assessment.recency_memory_id, prior.recency_memory_id,
                     "coach_recency", (sample,)),
                )
                for enabled, memory, original, kind, causes in checks:
                    if enabled:
                        event = world.events[memory - 1]
                        self.assertEqual((event.kind, event.causes), (kind, (original, *causes)))
                    else:
                        self.assertEqual(memory, original)
                        self.assertFalse(any(event.kind == kind for event in world.events))

    def test_decisions_use_only_actual_belief_inputs(self):
        world = advance(new_world(17), 28)
        for event in world.events:
            if event.kind != "decision":
                continue
            refs = [world.events[ref - 1] for ref in event.data["memory_ids"]]
            if event.actor == "coach":
                for memory in refs:
                    self.assertIn(memory.kind, {"initial_assessment", "coach_skill_belief",
                                                "coach_recency"})
                    if memory.kind == "coach_recency":
                        self.assertTrue(any(candidate["action"] == "observe"
                                            and candidate["target"] == memory.target
                                            for candidate in event.data["candidates"]))
            else:
                for memory in refs:
                    self.assertEqual(memory.target, event.actor)
                    self.assertIn(memory.kind, {"initial_player", "player_gain_belief"})
            self.assertTrue(all(memory.event_id in event.causes for memory in refs))

    def test_all_seven_conditions_resume_byte_identically(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            for condition, config in CONDITIONS.items():
                with self.subTest(condition=condition):
                    write_snapshot(path / "continuous.json", advance(new_world(17, config), 28))
                    write_snapshot(path / "half.json", advance(new_world(17, config), 14))
                    world = load_snapshot(path / "half.json")
                    self.assertEqual(world.config.beliefs, config.beliefs)
                    write_snapshot(path / "resumed.json", advance(world, 14))
                    self.assertEqual((path / "continuous.json").read_bytes(),
                                     (path / "resumed.json").read_bytes())

    def test_rejects_snapshot_with_missing_or_mismatched_flag(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "snapshot.json"
            write_snapshot(path, advance(new_world(17), 2))
            raw = path.read_bytes()
            for operation in ("missing", "mismatch"):
                with self.subTest(operation=operation):
                    data = json.loads(raw)
                    if operation == "missing":
                        del data["config"]["beliefs"]["player_gain"]
                    else:
                        data["config"]["beliefs"]["player_gain"] = False
                    path.write_text(json.dumps(data))
                    with self.assertRaisesRegex(ValueError, "belief controls"):
                        load_snapshot(path)

    def test_self_skill_negative_control(self):
        baseline, frozen = (advance(new_world(17, CONDITIONS[name]), 28)
                            for name in ("baseline", "self_skill_frozen"))
        self.assertEqual(trajectories(baseline), trajectories(frozen))
        self.assertNotEqual(baseline.players["p1"].mind.skill_estimate,
                            frozen.players["p1"].mind.skill_estimate)

    def test_clipping_symmetry_and_no_noise(self):
        rising = [(0.375, 0.425)] * 64
        falling = [(after, before) for before, after in rising]
        positive = estimate_observed_gains(rising, 0.5)
        negative = estimate_observed_gains(falling, 0.5)
        self.assertAlmostEqual(positive["clipped"], 0.05)
        self.assertEqual(negative["clipped"], 0)
        self.assertEqual(positive["signed"], -negative["signed"])
        self.assertEqual(estimate_observed_gains([(0.4, 0.4)] * 64, 0.5),
                         {"clipped": 0, "signed": 0})

    def test_tie_probe_reverses_and_toy_cycle_is_invariant(self):
        result = tie_probe()
        self.assertEqual(result["player"]["actual"], "A")
        self.assertEqual(result["player"]["reversed_selector"], "B")
        coach = result["coach"]
        self.assertEqual([(item["action"], item["target"]) for item in coach["actual"]],
                         [("observe", "p1"), ("observe", "p2")])
        original, reversed_roster = coach["canonical_cycle_targets"]
        self.assertEqual(original, reversed_roster)
        self.assertCountEqual(original, ["p1", "p2"] * 6)

    def test_frozen_t001_baseline_and_all_frozen_numerics_and_decisions(self):
        code = ("import json\nfrom dataclasses import replace\n"
                "from soccer_sim.simulation import advance, new_world\n"
                "from soccer_sim.state import Config\n"
                + inspect.getsource(numeric_state) + "\n" + inspect.getsource(projection)
                + "\nprint(json.dumps([projection(new_world(17, replace(Config(), belief_updates=b)))"
                  " for b in (True, False)]))")
        completed = subprocess.run([sys.executable, "-B", "-c", code], cwd=BUNDLE,
                                   capture_output=True, text=True, check=True)
        old = json.loads(completed.stdout)
        current = [projection(new_world(17, CONDITIONS[name]))
                   for name in ("baseline", "legacy_all_frozen")]
        self.assertEqual(old, json.loads(json.dumps(current)))

    def test_frozen_t001_checkpoint_requires_legacy_reader_and_replays(self):
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

    def test_t001_inventory_is_unchanged(self):
        expected = json.loads((ROOT / "artifacts/t002/preflight/t001-inventory.json").read_text())
        actual = []
        for path in sorted(LEGACY.rglob("*")):
            if path.is_file():
                data = path.read_bytes()
                actual.append({"path": str(path.relative_to(LEGACY)), "bytes": len(data),
                               "sha256": hashlib.sha256(data).hexdigest()})
        self.assertEqual(expected, sorted(actual, key=lambda item: item["path"]))
