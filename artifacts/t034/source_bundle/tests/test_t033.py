"""T033: decomposition of the recorded lineup rankings (analysis only; the simulation is unchanged)."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from experiments import t033, t023

W = (0.55, 0.48, 0.22)


def ranking(est):
    """Build a lineup_decision-style ranking from {player: (skill_est, match_est, fatigue_est)} (rounded as the sim does)."""
    rows = []
    for p, (s, m, f) in est.items():
        c = {'skill_term': round(W[0] * s, 6), 'match_term': round(W[1] * m, 6), 'fatigue_penalty': round(-W[2] * f, 6)}
        rows.append({'player_id': p, 'score': round(sum(c.values()), 6), 'components': c})
    return sorted(rows, key=lambda r: (-r['score'], r['player_id']))


class TestHandMadeCase(unittest.TestCase):
    # p13 is a newcomer at the prior (0.5, 0.5, 0.1); p1 and p2 are incumbents. p13 is truly the best.
    est = {'p1': (0.70, 0.80, 0.20), 'p2': (0.60, 0.70, 0.10), 'p13': (0.50, 0.50, 0.10)}
    truth = {'p1': 0.60, 'p2': 0.55, 'p13': 0.65}

    def test_decomposition_by_hand(self):
        # scores: p1 = .385+.384-.044 = .725 ; p2 = .33+.336-.022 = .644 ; p13 = .275+.24-.022 = .493. cutoff = p2 (second best of the others).
        row = t033.analyse_row(ranking(self.est), 'p13', W, self.truth)
        self.assertEqual(row['cutoff'], 'p2')
        self.assertFalse(row['started'])
        self.assertAlmostEqual(row['margin'], 0.493 - 0.644, places=6)
        self.assertAlmostEqual(row['d_skill'], 0.275 - 0.33, places=6)
        self.assertAlmostEqual(row['d_match'], 0.24 - 0.336, places=6)
        self.assertAlmostEqual(row['d_fatigue'], 0.0, places=6)
        # truth split: (0.55+0.48)*(0.65-0.55)=0.103 ; skill error 0.55*((0.5-.65)-(0.6-.55))=-0.11 ; match error 0.48*((0.5-.65)-(0.7-.55))=-0.144 ; fatigue 0
        self.assertAlmostEqual(row['true_gap'], 0.103, places=6)
        self.assertAlmostEqual(row['skill_est_error'], -0.11, places=6)
        self.assertAlmostEqual(row['match_est_error'], -0.144, places=6)
        self.assertAlmostEqual(row['true_gap'] + row['skill_est_error'] + row['match_est_error'] + row['fatigue'], row['margin'], places=6)
        self.assertTrue(row['top2_true'])  # truly best of the three
        # counterfactuals by hand: replacing either estimate alone with the truth is not enough here; both is.
        cf = row['cf']
        self.assertTrue(cf['a_no_fatigue'] is False and cf['b_no_match'] is False and cf['c_no_skill'] is False)
        self.assertFalse(cf['d_true_skill_est'])   # .5445... p13: .3575+.24-.022=.5755 vs p2 .33+.336-.022 -> .5755<.644
        self.assertFalse(cf['e_true_match_est'])   # p13: .275+.312-.022=.565 < p2 .644
        self.assertTrue(cf['f_true_both'])         # p13: .6695-.022=.6475 > p2: .5665-.022... (.55+.48)*.55=.5665-.022=.5445
        self.assertTrue(cf['h_true_only'])
        self.assertFalse(cf['g_match_eq_skill_est'])  # p13: .275+.24-.022 same as recorded; p2: .33+.288-.022=.596 > .493

    def test_independent_reimplementation(self):
        """A second, deliberately naive implementation (no shared helper) agrees on the cutoff, the start flag and the three term differences."""
        rk, n = ranking(self.est), 'p13'
        row = t033.analyse_row(rk, n, W, self.truth)
        sc = {r['player_id']: r['score'] for r in rk}
        others = sorted([p for p in sc if p != n], key=lambda p: (-sc[p], p))
        self.assertEqual(row['cutoff'], others[1])
        starts = [p for p in sorted(sc, key=lambda p: (-sc[p], p))][:2]
        self.assertEqual(row['started'], n in starts)
        for term, key in (('skill_term', 'd_skill'), ('match_term', 'd_match'), ('fatigue_penalty', 'd_fatigue')):
            a = [r for r in rk if r['player_id'] == n][0]['components'][term]
            b = [r for r in rk if r['player_id'] == others[1]][0]['components'][term]
            self.assertAlmostEqual(row[key], a - b, places=9)

    def test_a_starting_newcomer(self):
        est = {'p1': (0.50, 0.50, 0.10), 'p2': (0.45, 0.45, 0.10), 'p13': (0.60, 0.60, 0.10)}
        row = t033.analyse_row(ranking(est), 'p13', W, {'p1': 0.5, 'p2': 0.45, 'p13': 0.6})
        self.assertTrue(row['started'])
        self.assertGreater(row['margin'], 0)

    def test_tie_goes_to_player_id(self):
        est = {'p1': (0.5, 0.5, 0.1), 'p2': (0.5, 0.5, 0.1), 'p13': (0.5, 0.5, 0.1)}
        row = t033.analyse_row(ranking(est), 'p13', W, {'p1': .5, 'p2': .5, 'p13': .5})
        self.assertTrue(row['started'])  # the sim sorts ids as strings: 'p1' < 'p13' < 'p2', so p13 beats the cutoff p2 on the tie
        self.assertEqual((row['cutoff'], row['margin']), ('p2', 0.0))


class TestRealWorld(unittest.TestCase):
    def test_short_cell_checks_every_row_and_recorded_lineup(self):
        cell = t033.run_cell(t033.SETTINGS['t032'](t023.conditions()[1], 33), 100, days=3 * t033.L)
        self.assertGreater(cell['lineups_checked'], 0)
        self.assertGreater(len(cell['rows']), 0)
        for r in cell['rows']:
            self.assertAlmostEqual(r['margin'], r['d_skill'] + r['d_match'] + r['d_fatigue'], delta=t033.TOL)

    def test_selection_source_has_only_three_terms(self):
        self.assertEqual(len(t033.check_selection_source()['select_weights_read']), 3)

    def test_simulation_unchanged(self):
        self.assertGreater(t033.check_sim_unchanged(ROOT)['files_compared'], 10)


if __name__ == '__main__':
    unittest.main()
