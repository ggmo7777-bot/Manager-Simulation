"""Unit and integration tests for T009 (Minimal Development-Selection-2D Match Closed Loop).

Author: Antigravity (Gemini 4 Argon) — distinct from the creator of T001-T007 (Sol 6.1 High).
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from soccer_sim import match_2d as m2d
from soccer_sim import selection_loop as sl


class T009SelectionLoopTests(unittest.TestCase):
    def test_author_and_version_identity(self) -> None:
        ident = sl.selection_identity()
        self.assertEqual(ident["author"], "Antigravity (Gemini 4 Argon)")
        self.assertNotEqual(ident["author"], "Sol 6.1 High")
        self.assertEqual(ident["world_kind"], "selection_loop")
        self.assertEqual(ident["save"], 9)
        self.assertEqual(ident["rules"], "t009-selection-loop-v1")
        self.assertEqual(ident["policy"], "t009-selection-score-v1")

    def test_non_leakage_in_coach_and_player_and_lineup_policies(self) -> None:
        w_a = sl.create_selection_world(17, "coupled_feedback")
        w_b = sl.create_selection_world(17, "coupled_feedback")

        # Mutate true traits, true learning rates, and true caps in w_b without changing private minds
        for pid in sl.ROSTER_IDS:
            w_b.players[pid].traits = m2d.MatchTraits(pace=0.85, passing=0.86, shooting=0.87)
            w_b.players[pid].rates = (0.01, 0.01)
            w_b.players[pid].cap = 0.99

        assert w_a.coach is not None and w_b.coach is not None
        c_dec_a = sl.decide_coach_training(w_a.coach, 1, 1, (), w_a.config)
        c_dec_b = sl.decide_coach_training(w_b.coach, 1, 1, (), w_b.config)
        self.assertEqual(c_dec_a, c_dec_b)

        l_dec_a = sl.decide_lineup(w_a.coach, 7, w_a.config)
        l_dec_b = sl.decide_lineup(w_b.coach, 7, w_b.config)
        self.assertEqual(l_dec_a, l_dec_b)

        obs = sl.PlayerTrainingObservation(
            player_id="p1",
            day=1,
            event_id=1,
            skill_signal=0.45,
            fatigue_signal=0.08,
            proposals=(
                sl.TrainingProposal(
                    coach_id="c1",
                    event_id=12,
                    target="p1",
                    method="A",
                    expected_gain=0.02,
                ),
            ),
        )
        p_dec_a = sl.decide_player_training(obs, w_a.players["p1"].mind, w_a.config)
        p_dec_b = sl.decide_player_training(obs, w_b.players["p1"].mind, w_b.config)
        self.assertEqual(p_dec_a, p_dec_b)

    def test_schedule_budgets_and_starter_bench_invariants(self) -> None:
        world = sl.run_selection_world(84, seed=17, condition="coupled_feedback")
        sl.validate_selection_world(world, check_history=True)
        self.assertEqual(world.day, 84)
        self.assertEqual(len(world.match_summaries), 12)

        training_summaries = [
            e for e in world.events if e["kind"] == "day_summary" and e["data"]["day_type"] == "training"
        ]
        match_summaries = [
            e for e in world.events if e["kind"] == "day_summary" and e["data"]["day_type"] == "match"
        ]
        self.assertEqual(len(training_summaries), 72)
        self.assertEqual(len(match_summaries), 12)

        for ev in training_summaries:
            self.assertEqual(ev["data"]["coach_slots"], 2)
            self.assertEqual(set(ev["data"]["player_actions"].keys()), set(sl.ROSTER_IDS))

        for ev in world.events:
            if ev["kind"] == "coach_match_belief":
                if not ev["data"]["started"]:
                    self.assertIsNone(ev["data"]["observed_rating"])
                    self.assertEqual(
                        ev["data"]["match_rating_before"],
                        ev["data"]["match_rating_after"],
                    )
                    self.assertEqual(
                        ev["data"]["skill_before"],
                        ev["data"]["skill_after"],
                    )

        total_appearances = sum(world.players[pid].mind.appearances for pid in sl.ROSTER_IDS)
        self.assertEqual(total_appearances, 24)  # 12 matches * 2 starters per match

    def test_training_to_match_coupling_effect(self) -> None:
        decoupled_gds = []
        coupled_gds = []
        decoupled_shots = []
        coupled_shots = []
        for seed in range(100, 108):
            w_dec = sl.run_selection_world(84, seed=seed, condition="decoupled_prior")
            w_coup = sl.run_selection_world(84, seed=seed, condition="coupled_prior")
            decoupled_gds.append(sum(m["goal_diff"] for m in w_dec.match_summaries))
            coupled_gds.append(sum(m["goal_diff"] for m in w_coup.match_summaries))
            decoupled_shots.append(sum(m["home_shots"] for m in w_dec.match_summaries))
            coupled_shots.append(sum(m["home_shots"] for m in w_coup.match_summaries))
            # In prior mode, starter sets are identical between decoupled and coupled
            self.assertEqual(
                [set(m["starters"]) for m in w_dec.match_summaries],
                [set(m["starters"]) for m in w_coup.match_summaries],
            )
        self.assertGreater(sum(coupled_gds), sum(decoupled_gds) + 40)
        self.assertGreater(sum(coupled_shots), sum(decoupled_shots) + 30)

    def test_match_feedback_and_initial_prior_and_rotation_shape_p3_career(self) -> None:
        p3_apps_prior = 0
        p3_apps_feedback = 0
        p3_apps_rotation = 0
        p3_apps_fair = 0
        p3_gain_prior = 0.0
        p3_gain_fair = 0.0

        for seed in range(100, 108):
            w_prior = sl.run_selection_world(84, seed=seed, condition="coupled_prior")
            w_fb = sl.run_selection_world(84, seed=seed, condition="coupled_feedback")
            w_rot = sl.run_selection_world(84, seed=seed, condition="rotation_feedback")
            w_fair = sl.run_selection_world(84, seed=seed, condition="fair_prior_p3")

            p3_apps_prior += w_prior.players["p3"].mind.appearances
            p3_apps_feedback += w_fb.players["p3"].mind.appearances
            p3_apps_rotation += w_rot.players["p3"].mind.appearances
            p3_apps_fair += w_fair.players["p3"].mind.appearances

            p3_gain_prior += w_prior.players["p3"].mean_skill - 0.43
            p3_gain_fair += w_fair.players["p3"].mean_skill - 0.43

        self.assertEqual(p3_apps_prior, 0)
        self.assertGreater(p3_apps_feedback, p3_apps_prior)
        self.assertGreater(p3_apps_rotation, p3_apps_feedback)
        self.assertGreater(p3_apps_fair, p3_apps_feedback)
        self.assertGreater(p3_gain_fair, p3_gain_prior)

    def test_checkpoint_save_resume_exact_equality_all_conditions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            for cond in sl.T009_ALL_CONDITIONS:
                with self.subTest(condition=cond):
                    full = sl.run_selection_world(84, seed=17, condition=cond)
                    first_half = sl.run_selection_world(42, seed=17, condition=cond)
                    snap_file = tmp_path / f"{cond}_half.json"
                    full_file = tmp_path / f"{cond}_full.json"
                    resumed_file = tmp_path / f"{cond}_resumed.json"
                    full_ev = tmp_path / f"{cond}_full.jsonl"
                    resumed_ev = tmp_path / f"{cond}_resumed.jsonl"

                    sl.write_snapshot(snap_file, first_half)
                    restored = sl.load_snapshot(snap_file)
                    sl.run_selection_world(42, world=restored)

                    sl.write_snapshot(full_file, full)
                    sl.write_snapshot(resumed_file, restored)
                    sl.write_event_log(full_ev, full)
                    sl.write_event_log(resumed_ev, restored)

                    self.assertEqual(full_file.read_bytes(), resumed_file.read_bytes())
                    self.assertEqual(full_ev.read_bytes(), resumed_ev.read_bytes())

    def test_corrupted_snapshots_rejected(self) -> None:
        world = sl.run_selection_world(21, seed=17, condition="coupled_feedback")
        base_payload = sl.snapshot_payload(world)

        mutations: list[dict[str, Any]] = []

        m0 = deepcopy(base_payload)
        m0["world_kind"] = "match_2d"
        mutations.append(m0)

        m1 = deepcopy(base_payload)
        m1["identity"]["save_version"] = 8
        mutations.append(m1)

        m2 = deepcopy(base_payload)
        m2["identity"]["rule_version"] = "tampered-rule"
        mutations.append(m2)

        m3 = deepcopy(base_payload)
        m3["identity"]["policy_version"] = "tampered-policy"
        mutations.append(m3)

        m4 = deepcopy(base_payload)
        m4["identity"]["source_sha256"] = "0" * 64
        mutations.append(m4)

        m5 = deepcopy(base_payload)
        m5["state"]["players"]["p1"]["traits"]["pace"] = 0.99
        mutations.append(m5)

        m6 = deepcopy(base_payload)
        m6["state"]["players"]["p2"]["mind"]["skill_estimate"] = 0.95
        mutations.append(m6)

        m7 = deepcopy(base_payload)
        m7["state"]["coach"]["assessments"]["p3"]["match_rating_estimate"] = 0.99
        mutations.append(m7)

        m8 = deepcopy(base_payload)
        m8["state"]["events"][-1]["causes"].append(999999)
        mutations.append(m8)

        with tempfile.TemporaryDirectory() as tmp:
            for idx, bad in enumerate(mutations):
                p = Path(tmp) / f"bad_{idx}.json"
                p.write_text(json.dumps(bad), encoding="utf-8")
                with self.assertRaises(ValueError, msg=f"mutation {idx} not rejected"):
                    sl.load_snapshot(p)

    def test_legacy_t001_to_t008_artifacts_and_frozen_t008_bundle_preserved(self) -> None:
        root = Path(__file__).resolve().parent.parent
        preflight_dir = root / "artifacts" / "t009" / "preflight"
        for task_num in range(1, 9):
            inv_path = preflight_dir / f"t00{task_num}-inventory.json"
            self.assertTrue(inv_path.exists(), f"missing {inv_path}")
            entries = json.loads(inv_path.read_text(encoding="utf-8"))
            for entry in entries:
                fpath = root / "artifacts" / f"t00{task_num}" / entry["path"]
                self.assertTrue(fpath.exists(), f"missing frozen file {fpath}")
                digest = hashlib.sha256(fpath.read_bytes()).hexdigest()
                self.assertEqual(digest, entry["sha256"], f"hash changed for {fpath}")

        # Current match_2d.load_snapshot rejects old T008 snapshot because package source_sha256 changed in T009
        t008_half = root / "artifacts" / "t008" / "representative" / "first_half" / "snapshot.json"
        with self.assertRaises(ValueError):
            m2d.load_snapshot(t008_half)

        # Frozen T008 source_bundle resumes T008 first_half identically to T008 resumed snapshot
        t008_resumed = root / "artifacts" / "t008" / "representative" / "resumed" / "snapshot.json"
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-m",
                    "soccer_sim",
                    "resume",
                    "--snapshot",
                    str(t008_half),
                    "--days",
                    "20",
                    "--out",
                    tmp,
                ],
                cwd=root / "artifacts" / "t008" / "source_bundle",
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertIn("tick 40", proc.stdout)
            self.assertEqual((Path(tmp) / "snapshot.json").read_bytes(), t008_resumed.read_bytes())


if __name__ == "__main__":
    unittest.main()
