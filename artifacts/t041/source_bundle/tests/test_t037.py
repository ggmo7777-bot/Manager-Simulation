"""T037: observation_noise_mode gains shifted_v1 (mean 0, legacy's spread). Exactly legacy = shifted - width and centered = shifted / 2; defaults and the other modes are unchanged."""
from dataclasses import replace
from math import sqrt
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from experiments import t023, t036, t037
from soccer_sim import league_economy as le, league_observation as lo
from soccer_sim.match_2d import uniform_from_key

NAME = t023.conditions()[0]


class TestNoiseByHand(unittest.TestCase):
    def test_skill_noise_formulas_and_identities(self):
        w = 0.024
        for u, legacy, centered, shifted in ((-1.0, -3 * w, -w, -2 * w), (0.0, -w, 0.0, 0.0), (0.25, -0.5 * w, 0.25 * w, 0.5 * w), (1.0, w, w, 2 * w)):
            for mode, want in (('legacy', legacy), ('centered_v1', centered), ('shifted_v1', shifted)):
                self.assertAlmostEqual(le.observation_noise(u, w, mode), want, places=15)
            self.assertAlmostEqual(le.observation_noise(u, w, 'shifted_v1') - le.observation_noise(u, w, 'legacy'), w, places=15)
            self.assertAlmostEqual(2 * le.observation_noise(u, w, 'centered_v1'), le.observation_noise(u, w, 'shifted_v1'), places=15)

    def test_spread_and_mean_of_many_draws(self):
        us = [uniform_from_key(11, i, 'x', 'y') for i in range(1, 20001)]
        stats = {}
        for m in t037.MODES:
            xs = [le.observation_noise(u, 1.0, m) for u in us]
            mu = sum(xs) / len(xs)
            stats[m] = (mu, sqrt(sum((x - mu) ** 2 for x in xs) / len(xs)))
        self.assertAlmostEqual(stats['legacy'][0], -1.0, delta=0.02)
        self.assertAlmostEqual(stats['shifted_v1'][0], 0.0, delta=0.02)
        self.assertAlmostEqual(stats['centered_v1'][0], 0.0, delta=0.02)
        self.assertAlmostEqual(stats['shifted_v1'][1], stats['legacy'][1], delta=1e-9)  # the same spread (the sd of a uniform on [-2, 2] is 1.155)
        self.assertAlmostEqual(stats['legacy'][1], 2 / sqrt(3), delta=0.02)
        self.assertAlmostEqual(stats['centered_v1'][1], stats['legacy'][1] / 2, delta=0.02)

    def test_fatigue_noise_by_hand(self):
        kw = dict(true_fatigue=0.4, noise=0.05, seed=3, day=5, observer_id='c1', player_id='p2', context='scout')
        u = uniform_from_key(3, 5, 'fatigue:scout', 'c1:p2')
        self.assertEqual(lo.observe_fatigue(**kw), round(0.4 + (2 * u - 1) * 0.05, 6))
        self.assertEqual(lo.observe_fatigue(**kw, **le.fatigue_mode('legacy')), lo.observe_fatigue(**kw))
        self.assertEqual(lo.observe_fatigue(**kw, **le.fatigue_mode('centered_v1')), round(0.4 + u * 0.05, 6))
        self.assertEqual(lo.observe_fatigue(**kw, **le.fatigue_mode('shifted_v1')), round(0.4 + u * 2 * 0.05, 6))
        self.assertEqual(le.fatigue_mode('shifted_v1'), {'centered': True, 'shifted': True})
        self.assertEqual(le.fatigue_mode('legacy'), {'centered': False, 'shifted': False})
        self.assertGreaterEqual(lo.observe_fatigue(**{**kw, 'true_fatigue': 0.0}, **le.fatigue_mode('shifted_v1')), 0.0)
        self.assertLessEqual(lo.observe_fatigue(**{**kw, 'true_fatigue': 0.94, 'noise': 0.9}, **le.fatigue_mode('shifted_v1')), 0.95)


class TestConfigAndRng(unittest.TestCase):
    def test_config_key_only_when_not_default(self):
        base = t036.make_cfg('t032', NAME, 29, 'legacy')
        self.assertNotIn('observation_noise_mode', base.to_dict())
        for mode in ('centered_v1', 'shifted_v1'):
            cfg = replace(base, observation_noise_mode=mode)
            self.assertEqual(cfg.to_dict()['observation_noise_mode'], mode)
            self.assertEqual(le.LeagueEconomyConfig.from_dict(cfg.to_dict()), cfg)
        for bad in ('bogus', '', 'none', 1, 'Shifted_v1', 'shifted'):
            with self.assertRaises(ValueError):
                replace(base, observation_noise_mode=bad)

    def test_rng_stream_and_call_counts(self):
        self.assertTrue(t037.check_rng_stream()['identical'])
        t037.check_rng_calls(ROOT)
        self.assertEqual(t037.check_sim_frozen_bundle(ROOT)['changed'], ['league_economy.py', 'league_observation.py'])

    def test_default_equals_frozen_bundle_and_modes_differ(self):
        self.assertTrue(t037.check_vs_frozen_bundle(days=14)['equal'])

    def test_saves(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(set(t037.check_config_and_saves(tmp)['relabelled_snapshot'].values()), {'rejected'})


class TestDecomposition(unittest.TestCase):
    def test_decompose_by_hand(self):
        # per cell: (legacy, centered, shifted); T = c - l, B = s - l, V = c - s, B + V = T
        rows = [({'a': 1.0}, {'a': 0.0}, {'a': 0.7}), ({'a': 2.0}, {'a': 1.0}, {'a': 1.7}), ({'a': 1.0}, {'a': 0.1}, {'a': 0.7}), ({'a': 1.5}, {'a': 0.5}, {'a': 1.2})] * 5
        d = t037.decompose(rows, 'a')
        self.assertAlmostEqual(d['T'], -0.975); self.assertAlmostEqual(d['B_mean_part'], -0.3); self.assertAlmostEqual(d['V_spread_part'], -0.675)
        self.assertAlmostEqual(d['B_mean_part'] + d['V_spread_part'], d['T'])
        self.assertAlmostEqual(d['V_share'], 0.675 / 0.975); self.assertEqual(d['class'], 'mostly_spread')
        self.assertLess(d['V_share_ci95'][0], d['V_share']); self.assertGreater(d['V_share_ci95'][1], d['V_share'])
        flat = t037.decompose([({'a': 1.0}, {'a': 1.0}, {'a': 1.0})] * 4, 'a')
        self.assertEqual(flat['class'], 'T_not_distinguishable_from_0')
        off = t037.decompose([({'a': 0.0}, {'a': 1.0 + 0.02 * i}, {'a': 2.0 + 0.01 * i}) for i in range(5)], 'a')
        self.assertEqual(off['class'], 'offsetting')

    def test_short_world_error_centres_and_spread(self):
        out = {m: t037.run_cell(t036.make_cfg('t032', NAME, 29, m), 100, days=28) for m in t037.MODES}
        s = {m: t036.stat(out[m]['acc'][('obs', 'self')]) for m in t037.MODES}
        self.assertAlmostEqual(s['legacy']['mean'], -0.018, delta=0.003)
        self.assertAlmostEqual(s['shifted_v1']['mean'], 0.0, delta=0.003)
        self.assertAlmostEqual(s['shifted_v1']['sd'] / s['legacy']['sd'], 1.0, delta=0.10)
        self.assertAlmostEqual(s['centered_v1']['sd'] / s['legacy']['sd'], 0.5, delta=0.05)
        # fatigue where nothing clamps (true >= 2 w and true + 2 w <= 0.95): the error moves by the width (shifted - legacy)
        f = {m: t036.stat(out[m]['acc'][('fat_free2', 'training_self')])['mean'] for m in t037.MODES}
        self.assertAlmostEqual(f['shifted_v1'] - f['legacy'], 0.025, delta=0.006)
        self.assertAlmostEqual(f['shifted_v1'], 0.0, delta=0.004)


if __name__ == '__main__':
    unittest.main()
