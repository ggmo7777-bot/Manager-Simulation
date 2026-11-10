"""T039: the replay, the offset shadows, the variants and the groups by hand, and the recorder against a short world. `soccer_sim/` is unchanged."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from experiments import t023, t036, t039
from soccer_sim import league_economy as le

NAME = t023.conditions()[0]
CFG = le.LeagueEconomyConfig()
OFFSETS = t039.offsets_of(CFG)


def state():
    return {s: [0.5, 0.5, 0.1] for s in t039.SHADOWS}


class TestByHand(unittest.TestCase):
    def test_shifted_noise_is_legacy_plus_width(self):
        for u in (-1.0, -0.3, 0.0, 0.7, 1.0):
            for width in (0.020, 0.024, 0.030):
                self.assertAlmostEqual(le.observation_noise(u, width, 'shifted_v1') - le.observation_noise(u, width, 'legacy'), width, places=15)

    def test_groups(self):
        roster, starters = frozenset({'a', 'b', 'c'}), frozenset({'a', 'b'})
        self.assertEqual([t039.group_of(p, roster, starters) for p in ('a', 'c', 'z')], ['played', 'benched', 'arrived'])
        self.assertEqual(t039.group_of('a', frozenset(), frozenset()), 'arrived')  # the first match has no previous lineup

    def test_session_observation_by_hand(self):
        st = state()
        t039.observe(st, 'session', 0.60, 0.20, 0.25, CFG, OFFSETS)
        a = CFG.coach_skill_alpha
        self.assertAlmostEqual(st['actual'][0], 0.5 * (1 - a) + a * 0.60, places=6)
        self.assertEqual(st['actual'][1], 0.5)  # no practice rating in the default config
        self.assertEqual(st['actual'][2], 0.20)
        self.assertAlmostEqual(st['all'][0] - st['actual'][0], a * OFFSETS['session'], places=6)
        self.assertEqual(st['session'], st['all'])
        self.assertEqual(st['match'], st['actual'])  # only the named channel moves
        self.assertEqual(st['all'][2], 0.25)

    def test_match_observation_moves_match_rating_and_blends_skill(self):
        st = state()
        t039.observe(st, 'match', 0.70, 0.30, 0.35, CFG, OFFSETS)
        self.assertAlmostEqual(st['actual'][1], 0.5 * (1 - CFG.coach_match_alpha) + CFG.coach_match_alpha * 0.70, places=6)
        self.assertAlmostEqual(st['actual'][0], 0.5 * (1 - CFG.coach_match_skill_blend) + CFG.coach_match_skill_blend * 0.70, places=6)
        self.assertAlmostEqual(st['match'][1] - st['actual'][1], CFG.coach_match_alpha * OFFSETS['match'], places=6)
        self.assertEqual(st['session'], st['actual'])

    def test_upper_clamp_absorbs_the_offset(self):
        st = state()
        t039.observe(st, 'match', 0.98, 0.30, 0.35, CFG, OFFSETS)  # the observation is already at the ceiling: the shifted one is too
        self.assertEqual(st['all'][1], st['actual'][1])

    def test_zero_offset_shadows_equal_actual(self):
        st = state()
        for channel, obs in (('session', 0.61), ('match', 0.7), ('scout', 0.55), ('session', 0.4)):
            t039.observe(st, channel, obs, 0.2, 0.2, CFG, dict.fromkeys(t039.CHANNELS, 0.0))
        self.assertTrue(all(st[s] == st['actual'] for s in t039.SHADOWS))

    def test_variants_constant_shift_changes_nothing_and_group_shift_does(self):
        def rows(shifts):  # id, group, truth, estimates: three players, equal-ish
            base = {'p1': [0.60, 0.60, 0.1], 'p2': [0.58, 0.58, 0.1], 'p3': [0.57, 0.57, 0.1]}
            groups = {'p1': 'played', 'p2': 'played', 'p3': 'benched'}
            return [(p, groups[p], 0.5, {'actual': e, 'all': [x + shifts[p] for x in e]}) for p, e in base.items()]
        truth_free = lambda r, *spec: t039.top2(t039.variant_scores(CFG, r, *spec))
        same = rows({'p1': 0.05, 'p2': 0.05, 'p3': 0.05})
        self.assertEqual(truth_free(same, 'all', t039.COMPS, t039.GROUPS), {'p1', 'p2'})
        bench_up = rows({'p1': 0.0, 'p2': 0.0, 'p3': 0.10})
        self.assertEqual(truth_free(bench_up, 'all', t039.COMPS, t039.GROUPS), {'p1', 'p3'})
        self.assertEqual(truth_free(bench_up, 'all', t039.COMPS, ('played',)), {'p1', 'p2'})  # the bench shift is switched off for this variant
        self.assertEqual(truth_free(bench_up, 'all', ('fat',), t039.GROUPS), {'p1', 'p2'})  # a skill/match shift in a fatigue-only variant is ignored

    def test_pairs_only_between_played_and_benched(self):
        rows = [('a', 'played', 0.60, {}), ('b', 'benched', 0.70, {}), ('c', 'played', 0.30, {}), ('d', 'arrived', 0.90, {})]
        rank = t039.rank_positions([('a', 0.9), ('b', 0.8), ('c', 0.1), ('d', 0.95)])
        got = sorted(t039.pair_rows(rows, rank))
        # a/b: the better is benched (b) but a is ranked first -> wrong; b/c: better is benched, ranked above c -> right; a/c: both played -> skipped; d is neither
        self.assertEqual(got, [('better_benched', False), ('better_benched', True)])

    def test_scalars_by_hand(self):
        def acc(top2_actual, **variants):
            a = {}
            for x in top2_actual:
                t039.add(a, ('top2', 'actual'), float(x))
            for name in t039.VARIANTS:
                for x in variants.get(name, top2_actual):
                    t039.add(a, ('top2', name), float(x))
            for shadow in t039.SHADOWS[1:]:
                for g in t039.GROUPS + ('all',):
                    for comp in t039.COMPS + ('score',):
                        t039.add(a, ('shift', shadow, g, comp), 0.01 if g == 'benched' else 0.005)
            for g in t039.GROUPS + ('all',):
                for comp in ('skill', 'match'):
                    t039.add(a, ('err', g, comp), 0.0)
                for ch in t039.CHANNELS:
                    t039.add(a, ('cnt', g, ch), 2.0 if (g, ch) == ('benched', 'session') else 1.0)
            return a
        leg = acc([0, 0, 1, 0], all=[1, 0, 1, 0], session=[1, 0, 1, 0])
        p = {'legacy': leg, 'shifted_v1': acc([1, 1, 1, 0]), 'centered_v1': acc([1, 1, 1, 1])}
        s = t039.scalars(p)
        self.assertAlmostEqual(s['D_shifted'], 0.75 - 0.25); self.assertAlmostEqual(s['D_centered'], 0.75)
        self.assertAlmostEqual(s['C_all'], 0.25); self.assertAlmostEqual(s['F_shifted'], 0.5); self.assertAlmostEqual(s['F_centered'], 1 / 3)
        self.assertAlmostEqual(s['A_additivity'], 0.25 + 0 + 0 - 0.25)
        self.assertAlmostEqual(s['gap_score'], 0.005); self.assertAlmostEqual(s['cnt_session_ratio_legacy'], 2.0)


class TestRecorder(unittest.TestCase):
    def test_short_worlds_replay_exactly_in_all_settings_and_modes(self):
        for setting in ('t030', 't031', 't032'):
            for mode in ('legacy', 'shifted_v1', 'centered_v1'):
                cell = t039.run_cell(t036.make_cfg(setting, NAME, 29, mode), 100, days=70)  # run_cell asserts every decision against the recorded ranking, scores and events
                acc = cell['acc']
                self.assertEqual(acc[('top2', 'actual')][:2], acc[('start', 'true_top2_match')][:2])
                self.assertEqual(('top2', 'all') in acc, mode == 'legacy')
                self.assertEqual(acc[('cnt', 'played', 'match')][1], acc[('cnt', 'played', 'match')][0])  # every player who played got exactly one match observation by the own coach
                self.assertEqual(acc[('cnt', 'benched', 'match')][1], 0.0)  # and the bench none

    def test_history_equals_t036_and_wrapper_is_removed(self):
        before = le.observe_fatigue
        cfg = t036.make_cfg('t032', NAME, 29, 'legacy')
        mine, theirs = t039.run_cell(cfg, 100, days=56), t036.run_cell(cfg, 100, days=56)
        self.assertIs(le.observe_fatigue, before)
        self.assertEqual(mine['flat'], {k: v for k, v in theirs['flat'].items() if k in mine['flat']})
        self.assertEqual(t039.check_sim_unchanged(ROOT)['changed'], [])

    def test_legacy_shadow_all_shifts_estimates_up(self):
        acc = t039.run_cell(t036.make_cfg('t032', NAME, 29, 'legacy'), 100, days=70)['acc']
        self.assertGreater(acc[('shift', 'all', 'all', 'skill')][1], 0.0)  # the added width pushes skill estimates up
        self.assertGreater(acc[('shift', 'all', 'all', 'fat')][1], 0.0)  # and fatigue estimates (legacy errors are negative)


if __name__ == '__main__':
    unittest.main()
