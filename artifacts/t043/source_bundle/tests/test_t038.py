"""T038: the analysis code (bins, pair accuracy, re-ranking, correction fit) by hand, and the recorder against T036's own reading of the same short world. `soccer_sim/` is unchanged."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from experiments import t023, t036, t038
from soccer_sim import league_economy as le

NAME = t023.conditions()[0]


class TestByHand(unittest.TestCase):
    def test_age_bins(self):
        self.assertEqual([t038.age_bin(a) for a in (0, 1, 6, 7, 8, 13, 14, 27, 28, 400)], [0, 0, 0, 1, 2, 2, 3, 3, 4, 4])

    def test_pair_accuracy_classes_and_gap(self):
        rows = [('a', 3, 0.60), ('b', 14, 0.50), ('c', 7, 0.505), ('d', 7, 0.30)]  # ranking order a, b, c, d
        got = {}
        for cls, right in t038.pair_accuracy(rows):
            got.setdefault(cls, []).append(right)
        # a>b: better is newer by 11 days (right); a>c: newer by 4 -> close (right); a>d: close (right); b>c: gap 0.005 skipped; b>d: older better? b is 14 days, d 7 -> better b is older (right); c>d: close, and ranked c before d (right)
        self.assertEqual({k: sorted(v) for k, v in got.items()}, {'better_newer': [True], 'close': [True, True, True], 'better_older': [True]})
        wrong = list(t038.pair_accuracy([('x', 3, 0.30), ('y', 20, 0.60)]))  # x is ranked first although y is truly better and 17 days older
        self.assertEqual(wrong, [('better_older', False)])

    def test_rerank_constant_changes_nothing_and_difference_does(self):
        lineup = [('inc', 0, 0.90, 0.5, 'p1'), ('inc', 1, 0.80, 0.9, 'p2'), ('inc', 4, 0.70, 0.8, 'p3'), ('inc', 4, 0.60, 0.1, 'p4')]
        self.assertEqual(t038.rerank(lineup, {}), {'p1', 'p2'})
        self.assertEqual(t038.rerank(lineup, {('inc', b): 0.3 for b in range(5)}), {'p1', 'p2'})  # the same amount for everyone
        self.assertEqual(t038.rerank(lineup, {('inc', 0): 0.25}), {'p2', 'p3'})  # p1 falls from 0.90 to 0.65, below p3's 0.70
        self.assertEqual(t038.rerank(lineup, {('new', 0): 0.5}), {'p1', 'p2'})  # a key that matches no row
        self.assertEqual(t038.top2([('b', 1.0), ('a', 1.0), ('c', 0.5)]), {'a', 'b'})  # ties by id

    def test_fit_delta_by_hand(self):
        cfg = le.LeagueEconomyConfig()
        acc = {}
        for b, skill, match, n in ((0, -0.02, 0.0, 40), (4, 0.0, 0.0, 60), (2, 0.5, 0.5, 5)):  # bin 2 has too few rows: gets 0 and is still in the level
            for _ in range(n):
                t038.add(acc, ('err', 'inc', b, 'skill'), skill)
                t038.add(acc, ('err', 'inc', b, 'match'), match)
        delta = t038.fit_delta([acc], cfg)
        level = (-0.02 * 40) / 105 + 0.5 * 5 / 105
        self.assertAlmostEqual(delta[('inc', 0)], cfg.select_skill_weight * (-0.02 - (-0.8 + 2.5) / 105) + cfg.select_match_weight * (0.0 - 2.5 / 105))
        self.assertAlmostEqual(delta[('inc', 4)], cfg.select_skill_weight * (0.0 - level) + cfg.select_match_weight * (0.0 - 2.5 / 105))
        self.assertNotIn(('inc', 2), delta)
        self.assertNotIn(('new', 0), delta)

    def test_scalars_by_hand(self):
        acc = {}
        for key, xs in ((('err', 'inc', 0, 'skill'), [-0.02, -0.02]), (('err', 'inc', 3, 'skill'), [0.00, 0.02]), (('err', 'inc', 4, 'skill'), [0.04]), (('err', 'inc', 0, 'match'), [0.0]), (('err', 'inc', 3, 'match'), [0.1])):
            for x in xs:
                t038.add(acc, key, x)
        for cls, flags in (('better_newer', [0, 0, 1, 0]), ('better_older', [1, 1, 1, 0]), ('close', [1, 1])):
            for f in flags:
                t038.add(acc, ('pair', cls), 0.0, float(f))
        for key, xs in ((('mm', 'wrongly_in', 'age'), [20, 10]), (('mm', 'left_out', 'age'), [7, 8]), (('cf', 'actual'), [0, 0, 1, 0]), (('cf', 'corrected'), [1, 0, 1, 0]),
                        (('start', 'true_top2_match'), [1, 0]), (('age', 'starters'), [7, 7]), (('age', 'roster'), [7, 21])):
            for x in xs:
                t038.add(acc, key, x)
        s = t038.scalars(acc)
        self.assertAlmostEqual(s['G_inc_skill'], (0.00 + 0.02 + 0.04) / 3 - (-0.02), places=12)  # stale (bins 3, 4) minus new (bins 0, 1)
        self.assertAlmostEqual(s['G_inc_match'], 0.1 - 0.0, places=12)
        self.assertIsNone(s['G_new_skill'])
        self.assertAlmostEqual(s['D_pair'], 0.25 - 0.75); self.assertAlmostEqual(s['E_age'], 15 - 7.5)
        self.assertAlmostEqual(s['C_gain'], 0.5 - 0.25); self.assertAlmostEqual(s['age_roster'], 14.0)


class TestRecorderMatchesT036(unittest.TestCase):
    def test_short_world_same_rows_as_t036_and_same_history(self):
        for mode in ('legacy', 'shifted_v1'):
            cfg = t036.make_cfg('t032', NAME, 29, mode)
            mine, theirs = t038.run_cell(cfg, 100, days=105), t036.run_cell(cfg, 100, days=105)
            self.assertEqual(mine['flat']['events'], theirs['flat']['events'])
            self.assertEqual({k: v for k, v in mine['flat'].items() if k.startswith('final_')}, {k: v for k, v in theirs['flat'].items() if k.startswith('final_')})
            for group in ('inc', 'new'):
                for kind in ('skill', 'match'):
                    rows = [mine['acc'][('err', group, b, kind)] for b in range(5) if ('err', group, b, kind) in mine['acc']]
                    want = theirs['acc'].get(('lu', group, kind), [0, 0.0, 0.0, 0.0, 0.0])
                    self.assertEqual(sum(r[0] for r in rows), want[0])
                    self.assertAlmostEqual(sum(r[1] for r in rows), want[1], places=9)
                    self.assertAlmostEqual(sum(r[2] for r in rows), want[2], places=9)
            self.assertEqual(mine['acc'][('start', 'true_top2_match')][:2], theirs['acc'][('start', 'true_top2_match')][:2])
            self.assertEqual(len(mine['lineups']), mine['acc'][('start', 'true_top2_match')][0])
            # the all-class rows are the inc + new + other rows
            for b in range(5):
                parts = sum(mine['acc'].get(('err', c, b, 'skill'), [0])[0] for c in t038.CLASSES)
                self.assertEqual(mine['acc'].get(('err', 'all', b, 'skill'), [0])[0], parts)

    def test_wrapper_is_removed_and_sim_is_frozen(self):
        before = le.decide_club_lineup
        t038.run_cell(t036.make_cfg('t032', NAME, 29, 'legacy'), 100, days=14)
        self.assertIs(le.decide_club_lineup, before)
        self.assertEqual(t038.check_sim_unchanged(ROOT)['changed'], [])

    def test_counterfactual_with_no_difference_between_bins_equals_actual(self):
        cfg = t036.make_cfg('t032', NAME, 29, 'legacy')
        cells = {'a': [t038.run_cell(cfg, 100, days=42)], 'b': [t038.run_cell(cfg, 101, days=42)]}
        t038.counterfactual(cells, le.LeagueEconomyConfig())
        for cell in cells['a']:
            self.assertEqual(cell['acc'][('cf', 'actual')][0], cell['acc'][('cf', 'corrected')][0])
            self.assertAlmostEqual(cell['acc'][('cf', 'actual')][1] / cell['acc'][('cf', 'actual')][0], cell['acc'][('start', 'true_top2_match')][1] / cell['acc'][('start', 'true_top2_match')][0])


if __name__ == '__main__':
    unittest.main()
