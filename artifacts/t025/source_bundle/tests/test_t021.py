"""T021: the three T020-refused perturbations run under R1/R2; T020 names stay refused and all T020 cells are unchanged."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from soccer_sim import league_economy as le
from experiments import t020, t021

SAMPLED = "open_solidarity" + le.POPULATION_SUFFIX


class TestT021Extensions(unittest.TestCase):
    def test_new_names_have_the_preregistered_ranges(self):
        for name, expected in t021.EXPECT.items():
            changed = {(k, p): v for (k, p), v in le.sampled_ranges(name).items() if v != le.RANGE_BASE[k, p]}
            want = {("academy", "cap"): expected["cap"][0], ("buyer", "cap"): expected["cap"][1]} if "cap" in expected else \
                {("all", "ambition"): expected["ambition"], ("all", "autonomy"): expected["autonomy"]}
            self.assertEqual(changed, want, name)
        # R2: loyalty is bound at 0.05 so it stays at the default; it is not shifted or clipped.
        self.assertEqual(le.sampled_ranges("psych_w15_x")["all", "loyalty"], le.RANGE_BASE["all", "loyalty"])

    def test_t020_names_stay_refused_and_t020_cells_unchanged(self):
        self.assertEqual(t020.REFUSED, ("cap_w15", "psych_w15", "cap_cp"))
        for name in t020.REFUSED + ("cap_w15_y",):
            with self.assertRaises(ValueError, msg=name):
                le.config_for_condition(SAMPLED + le.RANGE_SUFFIX + name)
        for name in t020.EXECUTED:
            self.assertEqual(le.sampled_ranges(name), {**le.RANGE_BASE, **{k: v for k, v in le.sampled_ranges(name).items() if v != le.RANGE_BASE[k]}})
        self.assertEqual(len(le.RANGE_RUNNABLE), 19)
        self.assertEqual(le.RANGE_NAMES, ("none", "null") + tuple(le.RANGE_PERTURBATIONS))  # T020's 18 names are untouched

    def test_shift_beyond_ceiling_is_still_refused(self):
        saved = dict(le.RANGE_EXTENSIONS)
        try:
            le.RANGE_EXTENSIONS["cap_cp_x"] = ("cap", "add", 0.031)
            with self.assertRaises(ValueError):
                le.sampled_ranges("cap_cp_x")
        finally:
            le.RANGE_EXTENSIONS.clear()
            le.RANGE_EXTENSIONS.update(saved)

    def test_new_cells_match_independent_recomputation_and_keep_cap_rule(self):
        saved = dict(t020.EXPECT), t020.EXECUTED
        t020.EXPECT.update(t021.EXPECT)
        t020.EXECUTED = t021.NEW
        try:
            self.assertEqual(t020.check_initial((300,)), {"seeds": 1, "cells_checked": len(t021.NEW) + 2})
        finally:
            t020.EXPECT.clear(); t020.EXPECT.update(saved[0]); t020.EXECUTED = saved[1]
        raw = t020.raw_initial(300, "cap_w15_x")
        for p in raw["players"].values():
            self.assertGreaterEqual(p["cap"], min(0.98, max(p["traits"].values()) + 0.05) - 1e-6)

    def test_t020_cell_initial_state_is_unchanged(self):
        # Same draws as the frozen T020 sweep: a perturbation outside its group leaves the other groups untouched.
        base, new = t020.raw_initial(300, "baseline"), t020.raw_initial(300, "cap_cp_x")
        self.assertEqual(base["coaches"], new["coaches"])
        self.assertEqual({k: v["traits"] for k, v in base["players"].items()}, {k: v["traits"] for k, v in new["players"].items()})
        self.assertNotEqual({k: v["cap"] for k, v in base["players"].items()}, {k: v["cap"] for k, v in new["players"].items()})


if __name__ == "__main__":
    unittest.main()
