"""Unit tests for T010: 2-club development, lineup selection, head-to-head 2D match core, and mutual-agreement transfers.

Author: Antigravity (Gemini 4 Argon) — distinct from the creator of T001-T007 (Sol 6.1 High).
"""
from __future__ import annotations

import copy
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

from soccer_sim import transfer_loop as tl
from soccer_sim.storage import digest

ROOT = Path(__file__).resolve().parents[1]


class T010TransferLoopTests(unittest.TestCase):
    def test_author_and_version_identity(self) -> None:
        ident = tl.transfer_identity()
        self.assertEqual(ident["author"], "Antigravity (Gemini 4 Argon)")
        self.assertNotIn("Sol 6.1 High", ident["author"])
        self.assertEqual(ident["world_kind"], "transfer_loop")
        self.assertEqual(ident["save"], 10)
        self.assertEqual(ident["rules"], "t010-transfer-loop-v1")
        self.assertEqual(ident["policy"], "t010-transfer-score-v1")

    def test_schedule_budgets_and_club_roster_invariants(self) -> None:
        w = tl.run_transfer_world(84, seed=17, condition="coupled_transfer")
        self.assertEqual(w.day, 84)
        for club_id in tl.CLUB_IDS:
            r = w.club_roster(club_id)
            self.assertGreaterEqual(len(r), tl.MIN_CLUB_ROSTER)
            self.assertLessEqual(len(r), tl.MAX_CLUB_ROSTER)

        matches = [e for e in w.events if e["kind"] == "match_completed"]
        self.assertEqual(len(matches), 12)
        self.assertEqual([e["day"] for e in matches], list(range(7, 85, 7)))

        windows = sorted(
            {e["day"] for e in w.events if e["kind"] == "transfer_window_decision"}
        )
        self.assertEqual(windows, [28, 56])

        # Verify players only ever received proposals from their own club's coach on that day
        proposals_by_id = {
            e["event_id"]: e for e in w.events if e["kind"] == "training_proposal"
        }
        for ev in w.events:
            if ev["kind"] == "advice_feedback":
                prop_ids = [c for c in ev["cause_ids"] if c in proposals_by_id]
                self.assertEqual(len(prop_ids), 1)
                prop_ev = proposals_by_id[prop_ids[0]]
                self.assertEqual(prop_ev["target"], ev["actor"])

        # Validate full 1-pass state and event invariants
        validated = tl.validate_transfer_world(w.to_dict())
        self.assertEqual(validated.day, 84)

    def test_non_leakage_in_coach_player_lineup_and_transfer_policies(self) -> None:
        w1 = tl.create_transfer_world(101, condition="coupled_transfer")
        w2 = copy.deepcopy(w1)

        # Mutate hidden truths on w2 (traits, cap, rates, true fatigue)
        for pid in tl.PLAYER_IDS:
            w2.players[pid].traits = tl.MatchTraits(0.99, 0.99, 0.99)
            w2.players[pid].cap = 0.99
            w2.players[pid].rates = {"A": 0.99, "B": 0.99}
            w2.players[pid].fatigue = 0.95

        # 1. Coach training decision non-leakage
        for cid in tl.COACH_IDS:
            own_club = tl.COACH_CLUB_MAP[cid]
            other_club = tl.OTHER_CLUB_MAP[own_club]
            d1 = tl.decide_coach_training(
                w1.coaches[cid],
                w1.club_roster(own_club),
                w1.club_roster(other_club),
                1,
                1,
                [],
                w1.config,
            )
            d2 = tl.decide_coach_training(
                w2.coaches[cid],
                w2.club_roster(own_club),
                w2.club_roster(other_club),
                1,
                1,
                [],
                w2.config,
            )
            self.assertEqual(d1, d2)

        # 2. Player training decision non-leakage
        obs = {"observation_id": 2, "observed_skill": 0.47, "observed_fatigue": 0.12}
        props = [{"coach_id": "c1", "club_id": "club_a", "method": "A", "expected_gain": 0.04, "proposal_event_id": 5}]
        p_d1 = tl.decide_player_training(w1.player_minds["p3"], obs, props, w1.config)
        p_d2 = tl.decide_player_training(w2.player_minds["p3"], obs, props, w2.config)
        self.assertEqual(p_d1, p_d2)

        # 3. Lineup selection non-leakage
        l1 = tl.decide_club_lineup(w1.coaches["c1"], w1.club_roster("club_a"), 7, w1.config)
        l2 = tl.decide_club_lineup(w2.coaches["c1"], w2.club_roster("club_a"), 7, w2.config)
        self.assertEqual(l1, l2)

        # 4. Transfer negotiation non-leakage
        t1 = tl.decide_transfer_negotiation(
            w1.player_minds["p3"],
            w1.coaches["c1"],
            w1.coaches["c2"],
            w1.club_roster("club_a"),
            w1.club_roster("club_b"),
            28,
            w1.config,
        )
        t2 = tl.decide_transfer_negotiation(
            w2.player_minds["p3"],
            w2.coaches["c1"],
            w2.coaches["c2"],
            w2.club_roster("club_a"),
            w2.club_roster("club_b"),
            28,
            w2.config,
        )
        self.assertEqual(t1, t2)

    def test_checkpoint_save_resume_exact_equality_all_conditions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            for cond in tl.T010_MAIN_CONDITIONS:
                full = tl.run_transfer_world(84, seed=17, condition=cond)
                tl.save_transfer_run(full, base / cond / "full")

                half = tl.run_transfer_world(42, seed=17, condition=cond)
                tl.save_transfer_run(half, base / cond / "half")

                tl.resume_transfer_run(
                    base / cond / "half" / "snapshot.json",
                    42,
                    base / cond / "resumed",
                )
                for fname in ("snapshot.json", "events.jsonl"):
                    self.assertEqual(
                        (base / cond / "full" / fname).read_bytes(),
                        (base / cond / "resumed" / fname).read_bytes(),
                        f"mismatch on {cond} {fname}",
                    )

    def test_corrupted_snapshots_rejected(self) -> None:
        w = tl.run_transfer_world(28, seed=17, condition="coupled_transfer")
        base_snap = tl.snapshot_payload(w)

        def check_rejects(mutator) -> None:
            snap = copy.deepcopy(base_snap)
            mutator(snap)
            with tempfile.TemporaryDirectory() as tmp:
                p = Path(tmp) / "bad.json"
                p.write_text(json.dumps(snap), encoding="utf-8")
                with self.assertRaises(ValueError):
                    tl.load_transfer_snapshot(p)

        # 1. Wrong top-level world_kind
        check_rejects(lambda s: s.__setitem__("world_kind", "selection_loop"))
        # 2. Wrong identity save version
        check_rejects(lambda s: s["identity"].__setitem__("save", 999))
        # 3. Wrong identity rules
        check_rejects(lambda s: s["identity"].__setitem__("rules", "wrong"))
        # 4. Wrong identity policy
        check_rejects(lambda s: s["identity"].__setitem__("policy", "wrong"))
        # 5. Wrong identity author
        check_rejects(lambda s: s["identity"].__setitem__("author", "Sol 6.1 High"))
        # 6. Wrong source_sha256
        check_rejects(lambda s: s["identity"].__setitem__("source_sha256", "0" * 64))
        # 7. Club roster size violation (all 6 players in club_a)
        def corrupt_rosters(s):
            for pid in tl.PLAYER_IDS:
                s["state"]["players"][pid]["club_id"] = "club_a"
                s["state"]["player_minds"][pid]["club_id"] = "club_a"
        check_rejects(corrupt_rosters)
        # 8. Player mind club_id desync
        check_rejects(lambda s: s["state"]["player_minds"]["p1"].__setitem__("club_id", "club_b"))
        # 9. Non-contiguous event_id
        check_rejects(lambda s: s["state"]["events"][-1].__setitem__("event_id", 99999))
        # 10. Dangling cause_id
        check_rejects(lambda s: s["state"]["events"][-1].__setitem__("cause_ids", [99999]))
        # 11. Dangling memory pointer on player_mind
        check_rejects(lambda s: s["state"]["player_minds"]["p3"].__setitem__("career_memory_id", 99999))
        # 12. Non-finite float (NaN)
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "nan.json"
            raw_text = json.dumps(base_snap).replace('"fatigue": ', '"fatigue": NaN, "old_f": ', 1)
            p.write_text(raw_text, encoding="utf-8")
            with self.assertRaises(ValueError):
                tl.load_transfer_snapshot(p)

    def test_mutual_agreement_transfer_and_three_party_controls(self) -> None:
        seeds = range(100, 112)
        stay = [tl.summarize_transfer_world(tl.run_transfer_world(84, seed=s, condition="coupled_stay")) for s in seeds]
        tr = [tl.summarize_transfer_world(tl.run_transfer_world(84, seed=s, condition="coupled_transfer")) for s in seeds]
        fair = [tl.summarize_transfer_world(tl.run_transfer_world(84, seed=s, condition="fair_c1_prior_p3")) for s in seeds]
        low_c2 = [tl.summarize_transfer_world(tl.run_transfer_world(84, seed=s, condition="low_c2_scouting_p3")) for s in seeds]
        loyal = [tl.summarize_transfer_world(tl.run_transfer_world(84, seed=s, condition="high_loyalty_p3")) for s in seeds]

        def avg(rows, k):
            return sum(float(r[k]) for r in rows) / len(rows)

        # Transfers enabled vs disabled
        self.assertEqual(avg(stay, "p3_transferred"), 0.0)
        self.assertGreater(avg(tr, "p3_transferred"), 0.80)
        self.assertGreater(avg(tr, "p3_appearances"), avg(stay, "p3_appearances") + 3.0)
        self.assertGreater(avg(tr, "p3_skill_gain"), avg(stay, "p3_skill_gain") + 0.005)
        self.assertGreater(avg(tr, "club_b_minus_club_a_gd"), avg(stay, "club_b_minus_club_a_gd") + 8.0)

        # Single-factor controls each reduce p3 transfers
        self.assertLess(avg(fair, "p3_transferred"), avg(tr, "p3_transferred") - 0.50)
        self.assertGreater(avg(fair, "p3_club_a_appearances"), avg(tr, "p3_club_a_appearances") + 5.0)
        self.assertLess(avg(low_c2, "p3_transferred"), avg(tr, "p3_transferred") - 0.30)
        self.assertLess(avg(loyal, "p3_transferred"), avg(tr, "p3_transferred") - 0.50)

    def test_training_to_match_coupling_effect_in_two_club_world(self) -> None:
        seeds = range(100, 112)
        dec = [tl.summarize_transfer_world(tl.run_transfer_world(84, seed=s, condition="decoupled_transfer")) for s in seeds]
        coup = [tl.summarize_transfer_world(tl.run_transfer_world(84, seed=s, condition="coupled_transfer")) for s in seeds]

        def avg(rows, k):
            return sum(float(r[k]) for r in rows) / len(rows)

        self.assertGreater(avg(coup, "club_b_shots"), avg(dec, "club_b_shots") + 20.0)
        self.assertGreater(avg(coup, "club_b_minus_club_a_gd"), avg(dec, "club_b_minus_club_a_gd") + 8.0)

    def test_legacy_t001_to_t009_artifacts_and_frozen_t009_bundle_preserved(self) -> None:
        preflight = ROOT / "artifacts/t010/preflight"
        for label in ("t001", "t002", "t003", "t004", "t005", "t006", "t007", "t008", "t009"):
            expected = json.loads((preflight / f"{label}-inventory.json").read_text(encoding="utf-8"))
            base = ROOT / "artifacts" / label
            actual = []
            for p in sorted(base.rglob("*")):
                if p.is_file() and p.name != ".DS_Store":
                    raw = p.read_bytes()
                    actual.append(
                        {
                            "path": str(p.relative_to(base)),
                            "bytes": len(raw),
                            "sha256": sha256(raw).hexdigest(),
                        }
                    )
            self.assertEqual(actual, expected, f"frozen artifacts modified in {label}")


if __name__ == "__main__":
    unittest.main()
