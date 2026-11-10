"""T012 recorded B interventions and replay-verified research branches."""
import copy
from dataclasses import FrozenInstanceError, asdict, replace
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from soccer_sim import league_economy as le
from soccer_sim.storage import digest


class TestRecordedIntervention(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parent = le.run_league_world(seed=100, days=14)

    def fork(self, world=None, **kwargs):
        options = dict(kind="coach_skill_estimate", player_id="p3", coach_id="c1",
                       value=0.9, reason="counterfactual test")
        options.update(kwargs)
        return le.fork_league_world(self.parent if world is None else world, **options)

    def test_parent_unchanged_and_only_selected_b_and_reference_change(self):
        for kind, value in (("coach_skill_estimate", 0.9), ("player_trust", 0.05)):
            with self.subTest(kind=kind):
                before = copy.deepcopy(self.parent)
                child = self.fork(kind=kind, value=value)
                self.assertEqual(self.parent, before)
                self.assertIsNot(child.players, self.parent.players)
                record = child.interventions[0]
                self.assertEqual(record.parent_checkpoint_sha256, digest(le.snapshot_payload(self.parent)))
                expected = copy.deepcopy(before)
                if kind == "coach_skill_estimate":
                    target = expected.coaches["c1"].evaluations["p3"]
                    target.skill_estimate = value
                    self.assertEqual(child.coaches["c1"].evaluations["p3"].last_seen_day,
                                     target.last_seen_day)
                else:
                    target = expected.player_minds["p3"]
                    target.trust_by_coach["c1"] = value
                self.assertEqual(record.previous_obs_ref, target.last_obs_ref)
                target.last_obs_ref = child.events[-1]["id"]
                expected.events.append(copy.deepcopy(child.events[-1]))
                expected.interventions = (record,)
                self.assertEqual(child, expected)
                self.assertEqual(child.events[-1]["actor"], "researcher")
                self.assertEqual(child.events[-1]["kind"], "research_intervention")
                self.assertEqual(child.events[-1]["data"], asdict(record))
                self.assertEqual(child.events[-1]["cause_refs"], [record.previous_obs_ref])
                with self.assertRaises(FrozenInstanceError):
                    record.after_value = 0.5
                le.validate_league_world(child)

    def test_noop_has_explicit_record_but_same_behaviour_and_economy(self):
        for kind in ("coach_skill_estimate", "player_trust"):
            value = (self.parent.coaches["c1"].evaluations["p3"].skill_estimate
                     if kind == "coach_skill_estimate" else self.parent.player_minds["p3"].trust_by_coach["c1"])
            child = self.fork(kind=kind, value=value)
            self.assertEqual(child.interventions[0].before_value, child.interventions[0].after_value)
            baseline = copy.deepcopy(self.parent)
            le.advance_league_days(baseline, 15)
            le.advance_league_days(child, 15)
            for name in ("players", "club_accounts", "club_rosters", "ledger", "public_last_starters"):
                self.assertEqual(getattr(baseline, name), getattr(child, name))
            decisions = lambda w: [(e["day"], e["actor"], e["kind"], e["data"].get("chosen"))
                                   for e in w.events if "decision" in e["kind"]]
            self.assertEqual(decisions(baseline), decisions(child))
            le.validate_league_world(child)

    def test_day_zero_same_day_multiple_refork_and_saved_resume(self):
        child = self.fork(le.run_league_world(seed=100, days=0))
        child = self.fork(child, value=0.8)
        self.assertEqual([r.day for r in child.interventions], [0, 0])
        self.assertEqual(child.interventions[1].before_value, 0.9)
        self.assertEqual(child.interventions[1].previous_obs_ref, child.events[-2]["id"])
        with tempfile.TemporaryDirectory() as tmp:
            le.save_league_artifacts(child, tmp)
            loaded = le.load_league_snapshot(Path(tmp) / "snapshot.json")
            self.assertEqual(child, loaded)
            manifest = json.loads((Path(tmp) / "manifest.json").read_text())
            self.assertTrue(manifest["research_branch"])
            self.assertEqual(manifest["interventions"], [asdict(r) for r in child.interventions])
            le.advance_league_days(loaded, 9)
            grandchild = self.fork(loaded, kind="player_trust", value=0.95)
            le.save_league_artifacts(grandchild, tmp)
            resumed = le.load_league_snapshot(Path(tmp) / "snapshot.json")
            le.advance_league_days(grandchild, 20)
            le.advance_league_days(resumed, 20)
            self.assertEqual(grandchild, resumed)
            self.assertEqual([r.sequence for r in resumed.interventions], [1, 2, 3])
            le.validate_league_world(resumed)

    def test_arguments_strictly_rejected(self):
        invalid = [dict(kind="players.p3.cap"), dict(kind=True), dict(player_id="p13"),
                   dict(player_id=3), dict(coach_id="c5"), dict(coach_id=True),
                   dict(reason=""), dict(reason=" "), dict(reason=" padded"),
                   dict(reason="two\nlines"), dict(reason="x" * 1001), dict(reason=None),
                   dict(value=True), dict(value="0.9"), dict(value=None),
                   dict(value=float("nan")), dict(value=float("inf")),
                   dict(value=0.099999), dict(value=0.980001), dict(value=0.1234567),
                   dict(kind="player_trust", value=0.049999), dict(kind="player_trust", value=0.950001)]
        for kwargs in invalid:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.fork(**kwargs)
        for kind, values in (("coach_skill_estimate", (0.1, 0.98)), ("player_trust", (0.05, 0.95))):
            for value in values:
                le.validate_league_world(self.fork(kind=kind, value=value))

    def test_corrupt_parent_and_unrecorded_child_patch_rejected(self):
        mutations = [lambda w: setattr(w.players["p3"], "cap", 0.99),
                     lambda w: setattr(w.coaches["c1"].evaluations["p3"], "skill_estimate", 0.87),
                     lambda w: w.events.pop(),
                     lambda w: setattr(w.player_minds["p3"], "last_obs_ref", "missing")]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                corrupt = copy.deepcopy(self.parent)
                mutate(corrupt)
                with self.assertRaises(ValueError):
                    self.fork(corrupt)
                child = self.fork()
                mutate(child)
                with self.assertRaises(ValueError):
                    le.validate_league_world(child)

    def test_internal_record_types_and_unserialized_precision_rejected(self):
        child = self.fork()
        for replacement in (replace(child.interventions[0], sequence=True),
                            replace(child.interventions[0], after_value=0.90000001),
                            replace(child.interventions[0], previous_obs_ref="e_missing")):
            changed = copy.deepcopy(child)
            changed.interventions = (replacement,)
            with self.assertRaises(ValueError):
                le.validate_league_world(changed)
        changed = copy.deepcopy(child)
        changed.interventions = list(child.interventions)
        with self.assertRaises(ValueError):
            le.validate_league_world(changed)
        child.players["p3"].fatigue += 1e-10
        with self.assertRaises(ValueError):
            le.validate_league_world(child)

    def test_corrupt_records_and_state_rejected_before_trusting_patch(self):
        child = self.fork()
        child = self.fork(child, kind="player_trust", value=0.05)
        le.advance_league_days(child, 2)
        payload = le.snapshot_payload(child)
        mutations = [
            lambda p: p["interventions"][0].update(sequence=True),
            lambda p: p["interventions"][0].update(sequence=2),
            lambda p: p["interventions"].reverse(),
            lambda p: p["interventions"][1].update(day=13),
            lambda p: p["interventions"][1].update(day=17),
            lambda p: p["interventions"][0].update(day=14.0),
            lambda p: p["interventions"][0].update(phase="before_training"),
            lambda p: p["interventions"][0].update(parent_checkpoint_sha256="0" * 64),
            lambda p: p["interventions"][0].update(before_value=0.4),
            lambda p: p["interventions"][0].update(before_value=True),
            lambda p: p["interventions"][0].update(after_value="0.9"),
            lambda p: p["interventions"][0].update(after_value=1),
            lambda p: p["interventions"][0].update(after_value=0.1234567),
            lambda p: p["interventions"][0].update(after_value=float("nan")),
            lambda p: p["interventions"][0].update(previous_obs_ref="e_missing"),
            lambda p: p["interventions"][0].update(previous_obs_ref="e1"),
            lambda p: p["interventions"][0].update(player_id="p2"),
            lambda p: p["interventions"][0].update(coach_id="c2"),
            lambda p: p["interventions"][0].update(observer_id="p3"),
            lambda p: p["interventions"][0].update(reason="changed"),
            lambda p: p["interventions"][0].update(extra=1),
            lambda p: p["interventions"][0].pop("reason"),
            lambda p: p.update(interventions=[]),
            lambda p: p.update(research_branch=False),
            lambda p: p.update(research_branch=1),
            lambda p: p["players"]["p3"].update(cap=0.99),
            lambda p: p["config"].update(coach_skill_alpha=0.2),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snapshot.json"
            for i, mutate in enumerate(mutations):
                with self.subTest(i=i):
                    corrupt = copy.deepcopy(payload)
                    mutate(corrupt)
                    path.write_text(json.dumps(corrupt))
                    with self.assertRaises(ValueError):
                        le.load_league_snapshot(path)

    def test_save12_and_unknown_intervention_version_rejected(self):
        for key, value in (("save", 12), ("intervention", "unknown")):
            p = le.snapshot_payload(self.parent)
            p["identity"][key] = value
            with self.assertRaises(ValueError):
                le._replay_snapshot_payload(p)

    def test_invalid_branch_cannot_overwrite_saved_branch(self):
        child = self.fork()
        with tempfile.TemporaryDirectory() as tmp:
            le.save_league_artifacts(child, tmp)
            before = {p.name: p.read_bytes() for p in Path(tmp).iterdir()}
            child.player_minds["p3"].trust_by_coach["c2"] = 0.9
            with self.assertRaises(ValueError):
                le.save_league_artifacts(child, tmp)
            self.assertEqual(before, {p.name: p.read_bytes() for p in Path(tmp).iterdir()})

    def test_cli_branch_and_normal_resume_preserve_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, branch, resumed = root / "source", root / "branch", root / "resumed"
            le.save_league_artifacts(self.parent, source)
            before = {p.name: p.read_bytes() for p in source.iterdir()}
            args = [sys.executable, "-B", "-m", "soccer_sim", "branch", "--snapshot", str(source / "snapshot.json"),
                    "--kind", "player_trust", "--player", "p3", "--coach", "c1", "--value", "0.05",
                    "--reason", "CLI test", "--days", "2", "--out", str(branch)]
            result = subprocess.run(args, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            loaded = le.load_league_snapshot(branch / "snapshot.json")
            expected = self.fork(kind="player_trust", value=0.05, reason="CLI test")
            le.advance_league_days(expected, 2)
            self.assertEqual(loaded, expected)
            result = subprocess.run([sys.executable, "-B", "-m", "soccer_sim", "resume", "--snapshot",
                                     str(branch / "snapshot.json"), "--days", "2", "--out", str(resumed)],
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            le.advance_league_days(expected, 2)
            self.assertEqual(le.load_league_snapshot(resumed / "snapshot.json"), expected)
            args[-1] = str(source)
            result = subprocess.run(args, text=True, capture_output=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn("source", result.stderr)
            alias = root / "alias"
            alias.symlink_to(source, target_is_directory=True)
            args[-1] = str(alias)
            self.assertEqual(subprocess.run(args, capture_output=True).returncode, 2)
            self.assertEqual(before, {p.name: p.read_bytes() for p in source.iterdir()})


if __name__ == "__main__":
    unittest.main()
