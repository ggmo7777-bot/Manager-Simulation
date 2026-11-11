"""T036: observation_noise_mode (legacy default / centered_v1): same draws, only the offset differs; default histories unchanged."""
from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from experiments import t023, t036
from soccer_sim import league_economy as le, league_observation as lo
from soccer_sim.match_2d import uniform_from_key

NAME = t023.conditions()[0]


class TestNoiseOffsetByHand(unittest.TestCase):
    def test_skill_noise_formulas(self):
        w = 0.024
        for u, legacy, centered in ((-1.0, -3 * w, -w), (0.0, -w, 0.0), (0.25, -0.5 * w, 0.25 * w), (1.0, w, w)):
            self.assertAlmostEqual(le.observation_noise(u, w, 'legacy'), legacy, places=15)
            self.assertAlmostEqual(le.observation_noise(u, w, 'centered_v1'), centered, places=15)
            self.assertAlmostEqual(le.observation_noise(u, w, 'centered_v1') - le.observation_noise(u, w, 'legacy'), w * (1 - u), places=15)

    def test_legacy_is_the_old_expression_bit_for_bit(self):
        for i in range(1, 200):
            u = uniform_from_key(7, i, 'coach_obs', 'c1:p3')
            self.assertEqual(le.observation_noise(u, 0.03, 'legacy'), (u - 0.5) * 2.0 * 0.03)

    def test_fatigue_noise_by_hand(self):
        kw = dict(true_fatigue=0.4, noise=0.05, seed=3, day=5, observer_id='c1', player_id='p2', context='scout')
        u = uniform_from_key(3, 5, 'fatigue:scout', 'c1:p2')
        self.assertEqual(lo.observe_fatigue(**kw), round(0.4 + (2 * u - 1) * 0.05, 6))
        self.assertEqual(lo.observe_fatigue(**kw, centered=False), lo.observe_fatigue(**kw))
        self.assertEqual(lo.observe_fatigue(**kw, centered=True), round(0.4 + u * 0.05, 6))
        # the clamp is the same in both: a true fatigue of 0 cannot read below 0
        self.assertGreaterEqual(lo.observe_fatigue(**{**kw, 'true_fatigue': 0.0}, centered=True), 0.0)

    def test_independent_mean_of_many_draws(self):
        us = [uniform_from_key(11, i, 'x', 'y') for i in range(1, 20001)]
        m = lambda f: sum(f(u) for u in us) / len(us)
        self.assertAlmostEqual(m(lambda u: le.observation_noise(u, 1.0, 'legacy')), -1.0, delta=0.02)
        self.assertAlmostEqual(m(lambda u: le.observation_noise(u, 1.0, 'centered_v1')), 0.0, delta=0.02)


class TestConfigAndRng(unittest.TestCase):
    def test_config_key_only_when_not_default(self):
        base = t036.make_cfg('t032', NAME, 29, 'legacy')
        self.assertNotIn('observation_noise_mode', base.to_dict())
        centered = replace(base, observation_noise_mode='centered_v1')
        self.assertEqual(centered.to_dict()['observation_noise_mode'], 'centered_v1')
        self.assertEqual(le.LeagueEconomyConfig.from_dict(centered.to_dict()), centered)
        self.assertEqual(le.LeagueEconomyConfig.from_dict(base.to_dict()), base)
        for bad in ('bogus', '', 'none', 1):
            with self.assertRaises(ValueError):
                replace(base, observation_noise_mode=bad)

    def test_rng_stream_and_call_counts(self):
        self.assertTrue(t036.check_rng_stream()['identical'])
        t036.check_rng_calls(ROOT)

    def test_default_run_equals_frozen_bundle_and_centered_differs(self):
        self.assertTrue(t036.check_vs_frozen_bundle(days=21)['equal'])
        self.assertNotEqual(t036.local_hash('t032', NAME, 100, 29, 14, 'legacy'), t036.local_hash('t032', NAME, 100, 29, 14, 'centered_v1'))

    def test_saves(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(t036.check_config_and_saves(tmp)['relabelled_snapshot'], 'rejected')


class TestExperimentHelpers(unittest.TestCase):
    def test_accumulators_by_hand(self):
        acc = {}
        for x, f in ((-0.02, 0.0), (0.04, 1.0)):
            t036.add(acc, 'k', x, f)
        s = t036.stat(acc['k'])
        self.assertEqual((s['n'], s['flag_share']), (2, 0.5))
        self.assertAlmostEqual(s['mean'], 0.01); self.assertAlmostEqual(s['mae'], 0.03); self.assertAlmostEqual(s['sd'], 0.03)
        other = {}
        t036.add(other, 'k', 0.0)
        t036.merge(acc, other)
        self.assertEqual(acc['k'][0], 3)
        d = t036.paired([({'a': 1.0}, {'a': 3.0}), ({'a': 2.0}, {'a': 2.0}), ({'a': 1.0}, {'a': 4.0})], 'a')
        self.assertAlmostEqual(d['mean_diff'], 5 / 3)
        self.assertEqual(d['sign'], {'positive': 2, 'negative': 0, 'ties': 1, 'p_two_sided': 0.5})

    def test_short_world_error_centres(self):
        out = {m: t036.run_cell(t036.make_cfg('t032', NAME, 29, m), 100, days=28) for m in t036.MODES}
        legacy, centered = (t036.stat(out[m]['acc'][('obs', 'self')]) for m in t036.MODES)
        self.assertAlmostEqual(legacy['mean'], -0.018, delta=0.003)
        self.assertAlmostEqual(centered['mean'], 0.0, delta=0.003)
        # fatigue (observations where clamping cannot happen): the error moves by about the noise width
        f_leg, f_cen = (t036.stat(out[m]['acc'][('fat_free', 'training_self')])['mean'] for m in t036.MODES)
        self.assertAlmostEqual(f_cen - f_leg, 0.025, delta=0.006)


if __name__ == '__main__':
    unittest.main()
