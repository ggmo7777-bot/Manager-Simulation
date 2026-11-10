"""T035: observation-noise centre shift: noise re-derivation, linear propagation of the estimate shift, explained share, Top2 recomputation (analysis only)."""
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


def changed_sim_files(task):
    """soccer_sim files that differ from a frozen bundle. T036 changed league_economy.py / league_observation.py on purpose (the default path is byte-identical in output: tests/test_t036.py)."""
    bundle = ROOT / f'artifacts/{task}/source_bundle/soccer_sim'
    return {p.name for p in (ROOT / 'soccer_sim').glob('*.py') if p.read_bytes() != (bundle / p.name).read_bytes()}
sys.path.insert(0, str(ROOT))
from experiments import t035, t034, t023
from soccer_sim import league_economy as le
from soccer_sim import match_2d

C = le.LeagueEconomyConfig()
W = (0.55, 0.48, 0.22)


def naive_u(seed, tick, channel, token):
    """Second implementation of the sim's keyed uniform in [-1, 1] (default range)."""
    digest = hashlib.sha256(json.dumps((match_2d.RNG_VERSION, seed, channel, tick, token, 0), separators=(',', ':')).encode()).digest()
    return -1.0 + 2.0 * ((int.from_bytes(digest[:8], 'big') >> 11) / (1 << 53))


class TestNoiseRule(unittest.TestCase):
    def test_sims_rule_is_shifted_and_the_centred_rule_is_symmetric(self):
        N = C.coach_noise
        us = [naive_u(100, d, 'coach_obs', 'c1:p1') for d in range(2000)]
        self.assertTrue(all(-1 <= u <= 1 for u in us))
        nu = [(u - 0.5) * 2.0 * N for u in us]
        nu2 = [u * N for u in us]
        self.assertTrue(-3 * N - 1e-12 <= min(nu) and max(nu) <= N + 1e-12)
        self.assertAlmostEqual(sum(nu) / len(nu) / N, -1.0, delta=0.05)  # mean -noise
        self.assertAlmostEqual(sum(nu2) / len(nu2) / N, 0.0, delta=0.05)
        for d in (0, 1, 2, 500):
            a, b = t035.draw(100, d, 'session', 'c1', 'p1', N)
            u = naive_u(100, d, 'coach_obs', 'c1:p1')
            self.assertAlmostEqual(a, (u - 0.5) * 2 * N, places=15)
            self.assertAlmostEqual(b, u * N, places=15)
            self.assertAlmostEqual(b - a, N * (1 - u), places=15)  # the shift is N(1-u), mean N

    def test_channel_names_match_the_sims_keys(self):
        self.assertEqual(t035.CHANNEL, {'session': 'coach_obs', 'scout': 'scout_obs', 'match': 'match_obs'})
        self.assertEqual(t035.draw(7, 3, 'match', 'c2', 'p9', 0.02)[1], naive_u(7, 3, 'match_obs', 'c2:p9') * 0.02)

    def test_shifted_value_by_hand(self):
        self.assertEqual(t035.shifted(0.60, -0.03, -0.01, 'session'), 0.62)
        self.assertEqual(t035.shifted(0.97, -0.01, 0.02, 'session'), 0.98)  # clamp
        self.assertEqual(t035.shifted(0.16, 0.0, -0.03, 'match'), 0.15)  # match lower bound .15


class TestPropagation(unittest.TestCase):
    def test_recursion_by_hand(self):
        self.assertAlmostEqual(t035.propagate(0.01, 0.36, 0.02), 0.64 * 0.01 + 0.36 * 0.02, places=15)
        self.assertEqual(t035.propagate(0.01, 0.0, 0.5), 0.01)

    def test_propagation_equals_difference_of_two_replays(self):
        ops = [{'kind': 'session', 'day': 1, 'val': 0.60, 'order': 0}, {'kind': 'scout', 'day': 2, 'val': 0.55, 'order': 1}, {'kind': 'match', 'day': 3, 'val': 0.70, 'order': 2},
               {'kind': 'session', 'day': 5, 'val': 0.66, 'order': 3}, {'kind': 'lineup', 'day': 7, 'ref': 0, 'order': 4}]
        delta = {0: 0.02, 1: 0.05, 2: 0.015, 3: 0.03}
        shifted_ops = [dict(o, val=o['val'] + delta[i]) if o['kind'] != 'lineup' else o for i, o in enumerate(ops)]
        for practice in (False, True):
            d_s = d_m = 0.0
            for i, o in enumerate(ops[:4]):
                a_s, a_m = t034.alphas(o['kind'], C, practice)
                d_s, d_m = t035.propagate(d_s, a_s, delta[i]), t035.propagate(d_m, a_m, delta[i])
            a, = t034.replay(ops, C, practice)
            b, = t034.replay(shifted_ops, C, practice)
            self.assertAlmostEqual(b['s'] - a['s'], d_s, places=5)
            self.assertAlmostEqual(b['m'] - a['m'], d_m, places=5)
        # skill by hand, non-practice: weights of the four observations .64*.68*.82*.64... via the chain
        d = 0.0
        for a_, x in ((0.36, 0.02), (0.32, 0.05), (0.18, 0.015), (0.36, 0.03)):
            d = (1 - a_) * d + a_ * x
        self.assertAlmostEqual(d, ((((0.36 * 0.02) * 0.68 + 0.32 * 0.05) * 0.82 + 0.18 * 0.015) * 0.64 + 0.36 * 0.03), places=12)


class TestExplanation(unittest.TestCase):
    def ranking(self, est):
        rows = []
        for p, (s, m, f) in est.items():
            comp = {'skill_term': round(W[0] * s, 6), 'match_term': round(W[1] * m, 6), 'fatigue_penalty': round(-W[2] * f, 6)}
            rows.append({'player_id': p, 'score': round(sum(comp.values()), 6), 'components': comp})
        return rows

    def test_top2_recomputation_by_hand(self):
        rk = self.ranking({'p1': (0.60, 0.60, 0.1), 'p2': (0.58, 0.58, 0.1), 'p3': (0.50, 0.50, 0.1)})
        self.assertEqual(t035.cf_starters(rk, W, {}), ['p1', 'p2'])
        # p3 gains .15/.15 -> .65*1.03-.022=.6475 > p2 .57514 and > p1 .596
        self.assertEqual(t035.cf_starters(rk, W, {'p3': (0.15, 0.15)}), ['p3', 'p1'])

    def test_error_accumulator_and_explained_share_by_hand(self):
        acc = {}
        t035.add_err(acc, ('inc',), -0.10, 0.05, 0.02, 0.01)
        t035.add_err(acc, ('inc',), -0.20, 0.07, -0.02, 0.01)
        e = acc['err']['inc']['s']
        self.assertEqual((e['n'], e['abs']), (2, 0.30000000000000004))
        self.assertAlmostEqual(e['abs_c'], 0.05 + 0.13, places=12)
        merged = t035.merge({}, acc)
        t035.merge(merged, acc)
        self.assertEqual(merged['err']['inc']['s']['n'], 4)
        for g in ('new', 'new_t0', 'new_t1', 'new_t2'):
            t035.add_err(acc, (g,), -0.30, 0.04, -0.30, 0.04)
        out, gap = t035.summarise_err(acc['err'])
        s = out['inc']['s']
        self.assertAlmostEqual(s['mean_error'], -0.15, places=12)
        self.assertAlmostEqual(s['mean_shift'], 0.06, places=12)
        self.assertAlmostEqual(s['explained_share'], 0.06 / 0.15, places=12)  # 0.4
        self.assertAlmostEqual(s['mae'], 0.15, places=12)
        self.assertAlmostEqual(s['mae_centred'], 0.09, places=12)
        self.assertAlmostEqual(s['rmse'], ((0.01 + 0.04) / 2) ** 0.5, places=12)
        # gap: newcomers -0.30 vs incumbents -0.15 -> -0.15 ; shift difference .04 - .06 = -0.02 -> share -0.02 / 0.15 * (-1)... defined as shift_diff / -gap
        self.assertAlmostEqual(gap['new']['s']['gap'], -0.15, places=12)
        self.assertAlmostEqual(gap['new']['s']['explained_share'], (0.04 - 0.06) / 0.15, places=12)
        self.assertAlmostEqual(gap['new']['s']['remaining_new_error_centred'], -0.26, places=12)

    def test_noise_summary_by_hand(self):
        n = {'session': {'n': 2.0, 'nu': -1.0, 'nu_sq': 1.0 + 1.0, 'nu2': 0.0, 'nu2_sq': 0.5, 'clamped': 0.0, 'shift': 0.02}}
        s = t035.summarise_noise(n)['session']
        self.assertEqual((s['mean_nu_over_width'], s['sd_nu_over_width']), (-0.5, (1.0 - 0.25) ** 0.5))


class TestAudit(unittest.TestCase):
    def test_static_audit_finds_the_affine_default_range_calls(self):
        a = t035.audit_code(ROOT / 'artifacts/t035/source_bundle')['by_file_and_class']  # the frozen pre-T036 source (T036 routes these draws through observation_noise)
        self.assertEqual(a['league_economy.py'].get('default_range_affine_(u-0.5)*2*noise'), 4)  # self, coach, scout, match
        self.assertEqual(a['league_observation.py'].get('default_range_affine_(2u-1)*noise'), 1)  # fatigue
        for older in ('selection_loop.py', 'transfer_loop.py'):
            self.assertEqual(set(a[older]), {'explicit_range'})


class TestRealWorld(unittest.TestCase):
    def test_short_cell_checks_pass(self):
        cell = t035.run_cell(t035.SETTINGS['t032'](t023.conditions()[1], 33), 100, days=3 * t035.L)
        c = cell['checks']
        self.assertGreater(c['session_key_checked'], 0)
        self.assertEqual(c['session_key_bad'] + c['scout_key_bad'], 0)
        self.assertEqual(c['chain_bad'], 0)
        self.assertGreater(c['newcomer_propagation_checked'], 0)
        self.assertLessEqual(c['newcomer_propagation_max_dev'], t035.TOL_PROP)
        noise = t035.summarise_noise(cell['acc']['noise'])
        for k, v in noise.items():
            self.assertLess(v['mean_nu_over_width'], -0.5, k)  # shifted towards negative in every kind

    def test_simulation_unchanged(self):
        self.assertLessEqual(changed_sim_files('t032') | changed_sim_files('t033'), {'league_economy.py', 'league_observation.py'})  # only T036's two files differ


if __name__ == '__main__':
    unittest.main()
