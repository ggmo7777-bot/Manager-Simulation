"""Mechanism, non-leakage, symmetry, persistence, and legacy preservation tests for T008.

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
from soccer_sim import multi_storage
from soccer_sim.experiment_t008 import measure_match_run
from soccer_sim.storage import canonical


class T008MatchCoreTests(unittest.TestCase):
    def test_author_and_version_identity(self) -> None:
        ident = m2d.match_identity()
        self.assertEqual(ident["author"], "Antigravity (Gemini 4 Argon)")
        self.assertEqual(ident["world_kind"], "match_2d")
        self.assertEqual(ident["save"], 8)
        self.assertEqual(ident["rules"], "t008-match2d-v1")
        self.assertEqual(ident["policy"], "t008-match2d-score-v1")

    def test_physical_invariants_and_accounting(self) -> None:
        for cond in m2d.T008_ALL_CONDITIONS:
            world = m2d.run_match(ticks=40, seed=3, condition=cond)
            m2d.validate_match_world(world, check_history=True)
            self.assertEqual(world.tick, 40)
            summaries = [e for e in world.events if e["kind"] == "tick_summary"]
            self.assertEqual(len(summaries), 40)
            for summary in summaries:
                bx, by = float(summary["ball"]["x"]), float(summary["ball"]["y"])
                self.assertTrue(-m2d.PITCH_HALF_LENGTH <= bx <= m2d.PITCH_HALF_LENGTH)
                self.assertTrue(-m2d.PITCH_HALF_WIDTH <= by <= m2d.PITCH_HALF_WIDTH)
                owner_id = summary["ball"]["owner_id"]
                if owner_id is not None:
                    self.assertIn(owner_id, world.players)
                    px = float(summary["players"][owner_id]["x"])
                    py = float(summary["players"][owner_id]["y"])
                    self.assertAlmostEqual(bx, px, places=5)
                    self.assertAlmostEqual(by, py, places=5)

    def test_illegal_non_owner_kick_logged_and_demoted_to_hold(self) -> None:
        world = m2d.create_match_world(seed=5, condition="baseline")
        self.assertIsNone(world.ball.owner_id)
        m2d.step_match(
            world,
            intent_overrides={
                "h1": m2d.MatchIntent(kind="shoot", direction_rel=(1.0, 0.0)),
                "a1": m2d.MatchIntent(kind="pass", direction_rel=(1.0, 0.0), target_player_id="a2"),
            },
        )
        decs = {
            e["player_id"]: e
            for e in world.events
            if e["kind"] == "player_decision" and e["tick"] == 0
        }
        self.assertEqual(decs["h1"]["intent_status"], "illegal_without_ball")
        self.assertEqual(decs["h1"]["effective_action"], "hold")
        self.assertEqual(decs["a1"]["intent_status"], "illegal_without_ball")
        self.assertEqual(decs["a1"]["effective_action"], "hold")
        summary = next(e for e in world.events if e["kind"] == "tick_summary" and e["tick"] == 0)
        self.assertEqual(summary["players"]["h1"]["move_distance"], 0.0)
        self.assertEqual(summary["players"]["a1"]["move_distance"], 0.0)

    def test_non_leakage_of_opponent_traits_mind_and_unseen_positions(self) -> None:
        w_base = m2d.create_match_world(seed=11, condition="baseline")
        w_alt = deepcopy(w_base)

        # Mutate opponent true capabilities and private mind styles/directives
        w_alt.players["a1"].traits = m2d.MatchTraits(pace=0.99, passing=0.99, shooting=0.99)
        w_alt.players["a2"].traits = m2d.MatchTraits(pace=0.10, passing=0.10, shooting=0.10)
        w_alt.players["a1"].mind.style = m2d.MatchStyle(directness=0.95, pass_bias=0.05, press_bias=0.95)
        w_alt.players["a2"].mind.coach_directive = "press"

        # Place a2 outside h1's vision_radius (dist > 6.5) in both worlds, but at different unseen coords
        w_base.players["a2"].x, w_base.players["a2"].y = (7.0, 3.0)
        w_alt.players["a2"].x, w_alt.players["a2"].y = (9.2, -4.5)

        obs_base = m2d.observe_match_player(w_base, "h1")
        obs_alt = m2d.observe_match_player(w_alt, "h1")
        self.assertEqual(obs_base, obs_alt)
        a2_perceived = next(opp for opp in obs_base.opponents if opp.player_id == "a2")
        self.assertFalse(a2_perceived.visible)
        self.assertIsNone(a2_perceived.position)

        dec_base = m2d.decide_match_player(obs_base, w_base.players["h1"].mind)
        dec_alt = m2d.decide_match_player(obs_alt, w_alt.players["h1"].mind)
        self.assertEqual(dec_base, dec_alt)

        # Verify that changing true capabilities changes physical execution on tick 0 without changing tick-0 decisions
        w_skill = m2d.create_match_world(seed=11, condition="home_skill_plus")
        w_norm = m2d.create_match_world(seed=11, condition="baseline")
        self.assertEqual(
            m2d.decide_match_player(m2d.observe_match_player(w_skill, "h1"), w_skill.players["h1"].mind),
            m2d.decide_match_player(m2d.observe_match_player(w_norm, "h1"), w_norm.players["h1"].mind),
        )
        m2d.step_match(w_skill)
        m2d.step_match(w_norm)
        self.assertGreater(
            abs(w_skill.players["h1"].x - (-2.2)),
            abs(w_norm.players["h1"].x - (-2.2)),
        )

    def test_mirror_symmetry_and_order_invariance_and_mutual_rebound(self) -> None:
        # 1. Order invariance
        w_fwd = m2d.create_match_world(seed=7, condition="baseline")
        w_rev = m2d.create_match_world(seed=7, condition="baseline")
        for _ in range(40):
            m2d.step_match(w_fwd, player_order=("h1", "h2", "a1", "a2"))
            m2d.step_match(w_rev, player_order=("a2", "a1", "h2", "h1"))
        self.assertEqual(
            canonical(m2d.snapshot_payload(w_fwd)),
            canonical(m2d.snapshot_payload(w_rev)),
        )

        # 2. Mirror symmetry across home and away
        cfg = m2d.match_config_for_condition("baseline", mirror_rng_by_slot=True)
        w_sym = m2d.run_match(ticks=40, seed=4, config=cfg)
        self.assertEqual(w_sym.score["home"], w_sym.score["away"])
        for h_id, a_id in (("h1", "a1"), ("h2", "a2")):
            self.assertAlmostEqual(w_sym.players[h_id].x, -w_sym.players[a_id].x, places=6)
            self.assertAlmostEqual(w_sym.players[h_id].y, -w_sym.players[a_id].y, places=6)
        self.assertAlmostEqual(w_sym.ball.x, 0.0, places=6)
        self.assertAlmostEqual(w_sym.ball.y, 0.0, places=6)

        # 3. Exact inter-team tie triggers mutual_rebound (neither player monopolizes ball by ID)
        w_tie = m2d.run_match(
            ticks=4,
            seed=0,
            config=m2d.match_config_for_condition("baseline", noise_scale=0.0, mirror_rng_by_slot=True),
        )
        rebounds = [
            e for e in w_tie.events if e["kind"] == "ball_control" and e["outcome"] == "mutual_rebound"
        ]
        self.assertGreaterEqual(len(rebounds), 1)
        self.assertEqual(set(rebounds[0]["tied_players"]), {"h1", "a1"})

    def test_single_factor_interventions_produce_distinct_signatures(self) -> None:
        seeds = range(10)
        metrics = {
            cond: [measure_match_run(m2d.run_match(ticks=40, seed=s, condition=cond)) for s in seeds]
            for cond in (
                "baseline",
                "home_skill_plus",
                "home_position_plus",
                "home_pass_style",
                "home_directive_press",
                "home_oracle_view",
            )
        }
        base_goals = sum(r["home_goals"] for r in metrics["baseline"])
        skill_goals = sum(r["home_goals"] for r in metrics["home_skill_plus"])
        pos_goals = sum(r["home_goals"] for r in metrics["home_position_plus"])
        self.assertGreater(skill_goals, base_goals)
        self.assertGreater(pos_goals, base_goals)

        base_passes = sum(r["home_pass_attempts"] for r in metrics["baseline"])
        style_passes = sum(r["home_pass_attempts"] for r in metrics["home_pass_style"])
        base_comp = sum(r["home_pass_completions"] for r in metrics["baseline"])
        style_comp = sum(r["home_pass_completions"] for r in metrics["home_pass_style"])
        self.assertGreater(style_passes, base_passes * 3)
        self.assertGreater(style_comp, base_comp * 3)

        base_unseen = sum(
            r["players"]["h1"]["unseen_opponent_ticks"] + r["players"]["h2"]["unseen_opponent_ticks"]
            for r in metrics["baseline"]
        )
        oracle_unseen = sum(
            r["players"]["h1"]["unseen_opponent_ticks"] + r["players"]["h2"]["unseen_opponent_ticks"]
            for r in metrics["home_oracle_view"]
        )
        self.assertGreater(base_unseen, 0)
        self.assertEqual(oracle_unseen, 0)

    def test_degenerate_strategies_do_not_dominate_baseline(self) -> None:
        seeds = range(10)
        for deg_cond in ("home_always_shoot", "home_ball_camp_wait"):
            runs = [measure_match_run(m2d.run_match(ticks=40, seed=s, condition=deg_cond)) for s in seeds]
            total_goal_diff = sum(r["goal_diff"] for r in runs)
            total_home_poss = sum(r["home_possession_ticks"] for r in runs)
            total_away_poss = sum(r["away_possession_ticks"] for r in runs)
            self.assertLess(total_goal_diff, -10)
            self.assertLess(total_home_poss, total_away_poss)

    def test_checkpoint_save_resume_exact_equality_all_conditions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            for cond in m2d.T008_MAIN_CONDITIONS:
                full = m2d.run_match(ticks=40, seed=19, condition=cond)
                half = m2d.run_match(ticks=20, seed=19, condition=cond)
                snap_file = tmp_path / cond / "snapshot.json"
                m2d.write_snapshot(snap_file, half)
                restored = m2d.load_snapshot(snap_file)
                m2d.run_match(ticks=20, world=restored)
                self.assertEqual(
                    canonical(m2d.snapshot_payload(full)),
                    canonical(m2d.snapshot_payload(restored)),
                )

    def test_corrupted_snapshots_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            snap_path = Path(tmp) / "snapshot.json"
            world = m2d.run_match(ticks=12, seed=2, condition="baseline")
            m2d.write_snapshot(snap_path, world)
            base_data = json.loads(snap_path.read_text(encoding="utf-8"))

            mutations = []
            # 1. Wrong world_kind
            m1 = deepcopy(base_data)
            m1["world_kind"] = "single_coach"
            mutations.append(m1)
            # 2. Wrong save_version
            m2 = deepcopy(base_data)
            m2["identity"]["save_version"] = 7
            mutations.append(m2)
            # 3. Wrong source_sha256
            m3 = deepcopy(base_data)
            m3["identity"]["source_sha256"] = "0" * 64
            mutations.append(m3)
            # 4. Out-of-bounds player x
            m4 = deepcopy(base_data)
            m4["state"]["players"]["h1"]["x"] = 12.5
            mutations.append(m4)
            # 5. Owned ball position mismatch
            m5 = deepcopy(base_data)
            m5["state"]["ball"]["owner_id"] = "h1"
            m5["state"]["ball"]["x"] = 9.9
            mutations.append(m5)
            # 6. Tampered decision score
            m6 = deepcopy(base_data)
            for ev in m6["state"]["events"]:
                if ev["kind"] == "player_decision":
                    ev["scores"]["move"] += 0.123
                    break
            mutations.append(m6)
            # 7. Causeless goal event
            m7 = deepcopy(base_data)
            m7["state"]["score"]["home"] += 1
            m7["state"]["events"].append(
                {
                    "tick": 0,
                    "kind": "goal",
                    "scoring_team": "home",
                    "scorer_id": None,
                    "cause_kind": None,
                    "score": m7["state"]["score"],
                }
            )
            mutations.append(m7)

            for idx, bad in enumerate(mutations):
                bad_path = Path(tmp) / f"bad_{idx}.json"
                bad_path.write_text(json.dumps(bad), encoding="utf-8")
                with self.assertRaises(ValueError, msg=f"mutation {idx} was not rejected"):
                    m2d.load_snapshot(bad_path)

    def test_legacy_t001_to_t007_artifacts_and_frozen_t007_bundle_preserved(self) -> None:
        root = Path(__file__).resolve().parent.parent
        preflight_dir = root / "artifacts" / "t008" / "preflight"
        for task_num in range(1, 8):
            inv_path = preflight_dir / f"t00{task_num}-inventory.json"
            self.assertTrue(inv_path.exists(), f"missing {inv_path}")
            inventory = json.loads(inv_path.read_text(encoding="utf-8"))
            entries = inventory["files"] if isinstance(inventory, dict) else inventory
            for entry in entries:
                fpath = root / "artifacts" / f"t00{task_num}" / entry["path"]
                self.assertTrue(fpath.exists(), f"missing frozen file {fpath}")
                digest = hashlib.sha256(fpath.read_bytes()).hexdigest()
                self.assertEqual(digest, entry["sha256"], f"hash changed for {entry['path']}")

        # Current multi_storage rejects old T007 snapshot because package source_sha256 changed in T008
        t007_half = root / "artifacts" / "t007" / "representative" / "first_half" / "snapshot.json"
        with self.assertRaises(ValueError):
            multi_storage.load_snapshot(t007_half)

        # Frozen T007 source_bundle resumes T007 first_half identically to T007 resumed snapshot
        t007_resumed = root / "artifacts" / "t007" / "representative" / "resumed" / "snapshot.json"
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-m",
                    "soccer_sim",
                    "resume",
                    "--snapshot",
                    str(t007_half),
                    "--days",
                    "42",
                    "--out",
                    tmp,
                ],
                cwd=root / "artifacts" / "t007" / "source_bundle",
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertIn("day 84", proc.stdout)
            self.assertEqual((Path(tmp) / "snapshot.json").read_bytes(), t007_resumed.read_bytes())


if __name__ == "__main__":
    unittest.main()
