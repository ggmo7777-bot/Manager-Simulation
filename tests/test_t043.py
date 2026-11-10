"""T043: the shifted expected gains, the arms, the five-part decomposition of err, the mediation by part, the sweep statistics and the pre-registered rules by hand, and the machinery against short worlds. `soccer_sim/` is unchanged."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from experiments import t023, t036, t040, t041, t042, t043
from soccer_sim import league_economy as le

NAME = t023.conditions()[3]
SHORT = 42  # two seasons: includes a match day, a transfer window (28) and the first season end


def short_arm(arm, **kw):
    return t043.run_arm('t031', NAME, 100, 29, t043.ARMS[arm], days=SHORT, **kw)


def plain(mode, days=SHORT):
    return le.run_league_world(seed=100, days=days, config=t036.make_cfg('t031', NAME, 29, mode))


class TestArmsAndShifts(unittest.TestCase):
    def test_twenty_six_arms_with_the_registered_shapes(self):
        self.assertEqual(len(t043.ARMS), 26)
        self.assertEqual(t043.ARMS['fwd_base'], ('fwd', 'plain', None))
        self.assertEqual(t043.ARMS['rev_g'], ('rev', 'swap', None))
        self.assertEqual(t043.ARMS['fwd_L'], ('fwd', 'level', None))
        self.assertEqual(t043.ARMS['rev_S'], ('rev', 'spread', None))
        self.assertEqual(t043.ARMS['fwd_o+0.0075'], ('fwd', 'offset', 0.0075))
        self.assertEqual(sorted(t043.SWEEP), list(t043.SWEEP))
        self.assertEqual(len(t043.SWEEP_ARMS), 9)
        self.assertEqual({spec[1] for spec in t043.ARMS.values() if spec[1] in t043.TRACKED_KINDS}, {'swap', 'spread'})
        self.assertEqual(sum(1 for spec in t043.ARMS.values() if spec[1] in t043.TRACKED_KINDS), 4)

    def test_executed_gains_by_hand(self):
        real, alt, c = {'A': 0.040, 'B': 0.030}, {'A': 0.050, 'B': 0.020}, 0.005
        self.assertEqual(t043.executed_gains('plain', None, real, alt, c), real)
        self.assertEqual(t043.executed_gains('swap', None, real, alt, c), alt)
        self.assertEqual({m: round(v, 9) for m, v in t043.executed_gains('level', None, real, alt, c).items()}, {'A': 0.045, 'B': 0.035})  # the real spread, the shifted mean
        self.assertEqual({m: round(v, 9) for m, v in t043.executed_gains('spread', None, real, alt, c).items()}, {'A': 0.045, 'B': 0.015})  # the alt spread around the real level
        self.assertEqual({m: round(v, 9) for m, v in t043.executed_gains('offset', -0.01, real, alt, c).items()}, {'A': 0.030, 'B': 0.020})
        negative_zero = t043.executed_gains('offset', 0.0, {'A': -0.0, 'B': 0.0}, alt, c)
        self.assertEqual([str(v) for v in negative_zero.values()], ['-0.0', '0.0'])  # a zero offset changes not even the sign of a zero
        self.assertIsNot(t043.executed_gains('swap', None, real, alt, c), alt)  # a copy: the tracker's column is never handed out

    def test_the_level_constants_are_the_registered_ones_from_t042(self):
        self.assertGreater(t043.LEVEL_CONSTANT['fwd'], 0)
        self.assertLess(t043.LEVEL_CONSTANT['rev'], 0)
        got = t043.check_level_constants(ROOT)
        self.assertEqual(got, {'fwd': t043.LEVEL_CONSTANT['fwd'], 'rev': t043.LEVEL_CONSTANT['rev']})


class TestPracticeWrapper(unittest.TestCase):
    def setUp(self):
        self.world = le._build_initial_world(seed=100, config=t036.make_cfg('t031', NAME, 29, 'legacy'))
        self.pid = next(iter(self.world.player_minds))
        self.mind = self.world.player_minds[self.pid]
        self.mind.expected_gains.update({'A': 0.050, 'B': 0.040})
        self.mind.perceived_fatigue = 0.10
        self.acc = {}

    def choose(self):
        return le.decide_player_training(mind=self.mind, club_id='club_a', proposals=[], config=self.world.config)['chosen']['action']

    def test_a_large_offset_moves_the_choice_both_ways_and_the_gains_are_put_back(self):
        gains = self.mind.expected_gains
        before = dict(gains)
        original = le.decide_player_training
        self.assertNotEqual(self.choose(), 'rest')
        with t043.installed(None, 'offset', -0.20, 0.0, self.acc):
            self.assertEqual(self.choose(), 'rest')
        self.assertEqual(gains, before)
        self.assertIs(self.mind.expected_gains, gains)
        self.assertIs(le.decide_player_training, original)
        with self.assertRaises(ValueError):
            with t043.installed(None, 'offset', 0.01, 0.0, self.acc):
                raise ValueError('stop')
        self.assertIs(le.decide_player_training, original)
        self.mind.rest_preference = 0.5  # rest is now preferred, and a large positive offset makes practice better again
        self.assertEqual(self.choose(), 'rest')
        with t043.installed(None, 'offset', 0.50, 0.0, self.acc):
            self.assertNotEqual(self.choose(), 'rest')
        self.assertEqual(gains, before)

    def test_the_decision_record_holds_the_value_that_was_used(self):
        with t043.installed(None, 'offset', 0.01, 0.0, self.acc):
            decision = le.decide_player_training(mind=self.mind, club_id='club_a', proposals=[], config=self.world.config)
        used = {c['action']: c['components']['expected_gain'] for c in decision['candidates'] if c['action'] in ('A', 'B')}
        self.assertEqual(used, {'A': 0.06, 'B': 0.05})

    def test_the_plain_kind_leaves_the_function_alone(self):
        original = le.decide_player_training
        with t043.installed(None, 'plain', None, 0.0, self.acc):
            self.assertIs(le.decide_player_training, original)

    def test_tracked_kinds_read_the_alt_column_and_record_the_description(self):
        tracker = t041.PlayerTracker(self.world, 'centered_v1', self.acc)
        state = t041.mind_state(self.mind)
        tracker.cols[self.pid]['real'] = state
        tracker.cols[self.pid]['alt'] = {**state, 'gains': {'A': 0.070, 'B': 0.020}}
        with t043.installed(tracker, 'spread', None, 0.01, self.acc):
            decision = le.decide_player_training(mind=self.mind, club_id='club_a', proposals=[], config=self.world.config)
            self.assertEqual({c['action']: c['components']['expected_gain'] for c in decision['candidates'] if c['action'] in ('A', 'B')}, {'A': 0.06, 'B': 0.01})
        self.assertEqual(self.mind.expected_gains, {'A': 0.050, 'B': 0.040})
        self.assertAlmostEqual(self.acc[('shift', 'gain')][1], 0.045 - 0.045)
        self.assertAlmostEqual(self.acc[('ab', 'real')][1], 0.010)
        self.assertAlmostEqual(self.acc[('ab', 'alt')][1], 0.050)
        self.assertAlmostEqual(self.acc[('gl', 'alt')][1], 0.045)


class TestDecomposition(unittest.TestCase):
    class Config:
        select_skill_weight, select_match_weight = 0.55, 0.48

    @staticmethod
    def row(pid, skill_term, match_term, fatigue):
        components = {'skill_term': skill_term, 'match_term': match_term, 'fatigue_penalty': -fatigue}
        return {'player_id': pid, 'score': round(skill_term + match_term - fatigue, 6), 'components': components}

    def event(self, ranking, starters):
        return {'kind': 'lineup_decision', 'data': {'ranking': ranking, 'starters': starters}}

    def test_the_parts_by_hand_and_they_add_up_to_err(self):
        truth = {'a': 0.70, 'b': 0.60, 'c': 0.50}
        ranking = [self.row('b', 0.35, 0.30, 0.02), self.row('a', 0.36, 0.31, 0.06), self.row('c', 0.28, 0.24, 0.02)]
        got = t043.decompose(ranking, truth, self.Config)
        # j = a (score 0.61 < b's 0.63), k = c (score 0.50): signal 0.11, gap 0.10, true difference T_a - T_c = 0.20
        self.assertEqual(got['true_set'], {'a', 'b'})
        self.assertAlmostEqual(got['signal'], 0.11)
        self.assertAlmostEqual(got['gap'], 0.10)
        self.assertAlmostEqual(got['err'], 1.03 * 0.10 - 0.11)
        p = got['parts']
        self.assertAlmostEqual(p['sel'], 1.03 * (0.10 - 0.20))
        self.assertAlmostEqual(p['skill'], 0.55 * 0.20 - (0.36 - 0.28))
        self.assertAlmostEqual(p['match'], 0.48 * 0.20 - (0.31 - 0.24))
        self.assertAlmostEqual(p['fatigue'], 0.06 - 0.02)
        self.assertAlmostEqual(p['round'], 0.0)
        self.assertAlmostEqual(sum(p.values()), got['err'])

    def test_a_rounding_remainder_is_its_own_part(self):
        truth = {'a': 0.70, 'b': 0.60, 'c': 0.50}
        ranking = [self.row('b', 0.35, 0.30, 0.02), self.row('a', 0.36, 0.31, 0.06), self.row('c', 0.28, 0.24, 0.02)]
        ranking[2]['score'] += 0.000003  # a score that is not the sum of its terms
        got = t043.decompose(ranking, truth, self.Config)
        self.assertAlmostEqual(got['parts']['round'], 0.000003)
        self.assertAlmostEqual(sum(got['parts'].values()), got['err'])

    def test_a_wrong_lineup_gives_a_negative_signal_with_the_matching_sign(self):
        truth = {'a': 0.70, 'b': 0.60, 'c': 0.50}
        acc = {}
        ranking = [self.row('a', 0.38, 0.33, 0.01), self.row('c', 0.37, 0.32, 0.00), self.row('b', 0.30, 0.25, 0.01)]
        t043.record_mediators(acc, self.Config, [self.event(ranking, ['a', 'c'])], truth.get)
        self.assertLess(acc[('med', 'signal')][1], 0)
        self.assertEqual((acc[('med', 'mismatch')][1], acc[('med', 'zero')][1]), (0.0, 0.0))
        self.assertAlmostEqual(sum(acc[('med', p)][1] for p in t043.PARTS), acc[('med', 'err')][1])
        self.assertEqual(acc[('med', 'gap')][0], 1)

    def test_small_rosters_and_other_events_are_skipped(self):
        acc = {}
        t043.record_mediators(acc, self.Config, [self.event([self.row('a', 0.3, 0.3, 0.0), self.row('b', 0.2, 0.2, 0.0)], ['a', 'b']), {'kind': 'other', 'data': {}}], {'a': 0.5, 'b': 0.4}.get)
        self.assertEqual(acc, {})

    def test_the_practice_events_give_the_action_shares(self):
        acc = {}
        events = [{'kind': 'player_training_decision', 'data': {'chosen': {'action': a}}} for a in ('A', 'rest', 'rest', 'B')] + [{'kind': 'other', 'data': {}}]
        t043.record_practice(acc, events)
        self.assertEqual([acc[('act', a)][:2] for a in t043.ACTIONS], [[4, 1.0], [4, 1.0], [4, 2.0]])


class TestMediationByPart(unittest.TestCase):
    @staticmethod
    def matrix(rows):
        n = t043.MED_N
        s = [0.0] * (n * n)
        for z in rows:
            for i in range(n):
                for j in range(n):
                    s[n * i + j] += z[i] * z[j]
        return s

    def rows(self):
        out = []
        for r, e1, e2, e3 in ((1.0, 0.5, -0.2, 0.1), (2.0, -0.4, 0.3, -0.2), (-1.0, 0.1, 0.4, 0.3), (-2.0, 0.6, -0.5, -0.1), (0.5, -0.3, -0.1, 0.2), (-0.5, 0.2, 0.6, -0.3), (1.5, -0.1, 0.2, 0.4)):
            skill, fatigue = 0.06 * r + 0.1 * e1, 0.02 * r + 0.1 * e2
            sel, match, rnd = -0.01 * r + 0.1 * e3, 0.0, 0.0
            err = sel + skill + match + fatigue + rnd
            gap = 0.3 * r + e1
            out.append((r, gap, err, -0.5 * r + 2.0 * gap - 1.0 * err, sel, skill, match, fatigue, rnd))
        return out

    def test_the_parts_split_the_a_path_of_err_and_the_shares_add_up(self):
        got = t043.mediation(self.matrix(self.rows()))
        self.assertAlmostEqual(sum(got[f'a_{p}'] for p in t043.PARTS), got['a_err'])
        self.assertAlmostEqual(sum(got[f'share_{p}'] for p in t043.PARTS), 1.0)
        self.assertAlmostEqual(sum(got[f'ind_{p}'] for p in t043.PARTS), got['ind_err'])
        self.assertAlmostEqual(got['c'], got['c_direct'] + got['ind_gap'] + got['ind_err'])
        self.assertAlmostEqual(got['c_direct'], -0.5)
        self.assertGreater(got['share_skill'], got['share_fatigue'])
        self.assertEqual(set(got), set(t043.MED_STATS))

    def test_an_empty_matrix_gives_nones(self):
        self.assertEqual(t043.mediation([0.0] * t043.MED_N ** 2), dict.fromkeys(t043.MED_STATS))

    def test_cell_matrices_group_the_arms(self):
        def acc(rest, hit):
            values = {('act', 'rest'): rest, ('med', 'gap'): 0.1, ('med', 'err'): 0.2, ('start', 'top2'): hit, **{('med', p): 0.04 for p in t043.PARTS}}
            return {key: [1, value] for key, value in values.items()}
        cell = {arm: {'acc': acc(0.5, 0.6)} for arm in t043.ARMS}
        for direction in t043.DIRECTIONS:
            for x in t043.ARM_X:
                cell[f'{direction}_{x}']['acc'] = acc(0.4, 0.7)  # every non-base arm: dz = (-0.1, 0, 0, +0.1, 0, ...)
        got = t043.cell_matrices(cell)
        self.assertAlmostEqual(got['all'][0], 24 * 0.01)
        self.assertAlmostEqual(got['sweep'][0], 18 * 0.01)
        self.assertAlmostEqual(got['swap'][0], 6 * 0.01)
        self.assertAlmostEqual(got['fwd'][0], 12 * 0.01)
        self.assertAlmostEqual(got['all'][3], 24 * -0.01)
        self.assertEqual(got['all'][t043.MED_N * 4 + 4], 0.0)  # the parts did not move


def synthetic_levels(base_fwd, base_rev, fwd=None, rev=None, rest_slope=0.5):
    """v[arm][metric]: top2 base levels, per-arm top2 overrides, rest decreasing in delta; every other metric 0."""
    v = {arm: dict.fromkeys(t043.METRICS, 0.0) for arm in t043.ARMS}
    for arm in t043.ARMS:
        v[arm]['top2'] = base_fwd if arm.startswith('fwd') else base_rev
    for direction in t043.DIRECTIONS:
        for arm, delta in t043.curve_arms(direction):
            v[arm]['act_rest'] = 0.6 - rest_slope * delta
    for x, value in (fwd or {}).items():
        v[f'fwd_{x}']['top2'] = value
    for x, value in (rev or {}).items():
        v[f'rev_{x}']['top2'] = value
    return v


def banded(point, width=0.005):
    return {k: [v - width, v + width] if isinstance(v, float) else v for k, v in point.items()}


class TestRules(unittest.TestCase):
    def case(self, fwd, rev, base_fwd=0.60, base_rev=0.70):
        point = t043.effects(synthetic_levels(base_fwd, base_rev, fwd, rev))
        return point, banded(point)

    def test_effects_by_hand(self):
        point, _ = self.case({'g': 0.70, 'L': 0.68, 'S': 0.61}, {'g': 0.62, 'L': 0.64, 'S': 0.69})
        self.assertAlmostEqual(point['D|top2'], 0.10)
        self.assertAlmostEqual(point['F|L|top2'], 0.08)
        self.assertAlmostEqual(point['R|L|top2'], 0.06)
        self.assertAlmostEqual(point['f|L'], 0.8)
        self.assertAlmostEqual(point['shareF|L'], 0.8)
        self.assertAlmostEqual(point['IF|top2'], 0.10 - 0.08 - 0.01)
        self.assertAlmostEqual(point['IR|top2'], 0.08 - 0.06 - 0.01)
        self.assertAlmostEqual(point['d|fwd|L|top2'], 0.08)
        self.assertAlmostEqual(point['d|rev|g|top2'], -0.08)

    def test_level_or_spread_classification_follows_the_registered_order(self):
        level = t043.classify_level_spread(*self.case({'g': 0.68, 'L': 0.66, 'S': 0.60}, {'g': 0.62, 'L': 0.64, 'S': 0.70}))
        self.assertEqual((level['class'], level['main_routes']), ('level', ['L']))
        spread = t043.classify_level_spread(*self.case({'g': 0.68, 'L': 0.60, 'S': 0.66}, {'g': 0.62, 'L': 0.70, 'S': 0.64}))
        self.assertEqual(spread['class'], 'spread')
        both = t043.classify_level_spread(*self.case({'g': 0.74, 'L': 0.66, 'S': 0.66}, {'g': 0.62, 'L': 0.64, 'S': 0.64}))
        self.assertEqual(both['class'], 'level_and_spread')
        partial = t043.classify_level_spread(*self.case({'g': 0.68, 'L': 0.63, 'S': 0.60}, {'g': 0.62, 'L': 0.67, 'S': 0.70}))
        self.assertEqual(partial['class'], 'partial(L)')  # a carrier below half of D
        joint = t043.classify_level_spread(*self.case({'g': 0.68, 'L': 0.60, 'S': 0.60}, {'g': 0.62, 'L': 0.70, 'S': 0.70}))
        self.assertEqual(joint['class'], 'only_jointly')
        none = t043.classify_level_spread(*self.case({'g': 0.60}, {'g': 0.70}))
        self.assertEqual(none['class'], 'no_gain_effect')

    def test_flags(self):
        got = t043.classify_level_spread(*self.case({'g': 0.68, 'L': 0.68, 'S': 0.58}, {'g': 0.62, 'L': 0.62, 'S': 0.72}))
        self.assertIn('opposed(S)', got['flags'])
        self.assertIn('non_additive', got['flags'])  # F_g is 0.08 but L + S is 0.08 - 0.02, and R_g is 0.08 against 0.08 + 0.02
        quiet = t043.classify_level_spread(*self.case({'g': 0.68, 'L': 0.66, 'S': 0.62}, {'g': 0.62, 'L': 0.64, 'S': 0.68}))
        self.assertEqual(quiet['flags'], [])

    def test_err_source_classes(self):
        ci = lambda lo, hi: [lo, hi]
        point = {'c': -0.5, **{f'ind_{p}': 0.0 for p in t043.PARTS}, **{f'share_{p}': 0.0 for p in t043.PARTS}}
        interval_for = lambda **kw: {'c': ci(-0.6, -0.4), **{f'ind_{p}': ci(-0.05, 0.05) for p in t043.PARTS}, **{f'ind_{k}': ci(*v) for k, v in kw.items()}}
        single = {**point, 'ind_skill': -0.2, 'share_skill': 0.6, 'ind_fatigue': -0.1, 'share_fatigue': 0.3}
        self.assertEqual(t043.classify_err_source({'point': single, 'ci95': interval_for(skill=(-0.3, -0.1), fatigue=(-0.2, -0.05))}), 'single(skill)')
        spread = {**point, 'ind_skill': -0.2, 'share_skill': 0.4, 'ind_fatigue': -0.2, 'share_fatigue': 0.4}
        self.assertEqual(t043.classify_err_source({'point': spread, 'ci95': interval_for(skill=(-0.3, -0.1), fatigue=(-0.3, -0.1))}), 'distributed(skill, fatigue)')
        self.assertEqual(t043.classify_err_source({'point': single, 'ci95': interval_for(skill=(-0.3, -0.1))}), 'single(skill)')
        self.assertEqual(t043.classify_err_source({'point': {**point, 'ind_skill': -0.2, 'share_skill': 0.4}, 'ci95': interval_for(skill=(-0.3, -0.1))}), 'partial(skill)')
        self.assertEqual(t043.classify_err_source({'point': point, 'ci95': interval_for()}), 'none')
        self.assertEqual(t043.classify_err_source({'point': single, 'ci95': {**interval_for(skill=(-0.3, -0.1)), 'c': ci(-0.6, 0.1)}}), 'no_total')
        wrong_sign = {**point, 'ind_skill': 0.2, 'share_skill': 0.6}
        self.assertEqual(t043.classify_err_source({'point': wrong_sign, 'ci95': interval_for(skill=(0.1, 0.3))}), 'none')

    def test_curve_helpers(self):
        self.assertAlmostEqual(t043.ols_slope([0.0, 1.0, 2.0], [1.0, 3.0, 5.0]), 2.0)
        self.assertIsNone(t043.ols_slope([1.0, 1.0], [1.0, 2.0]))
        self.assertAlmostEqual(t043.interpolate([0.0, 1.0, 3.0], [0.0, 10.0, 30.0], 2.0), 20.0)
        self.assertIsNone(t043.interpolate([0.0, 1.0], [0.0, 1.0], 1.5))
        self.assertIsNone(t043.interpolate([], [], 0.0))
        fwd, rev = [(0.3, 0.50), (0.5, 0.60), (0.9, 0.70)], [(0.4, 0.56), (0.6, 0.62), (0.8, 0.68)]
        # in range: 0.5 -> rev 0.59, diff 0.01; the others lie outside 0.4..0.8
        self.assertAlmostEqual(t043.collapse(fwd, rev), 0.01)
        self.assertIsNone(t043.collapse([(0.1, 0.5)], rev))

    def test_slope_optimum_plateau_and_collapse_of_the_sweep(self):
        fwd = {name: 0.60 - 0.5 * delta for name, delta in zip(t043.SWEEP_ARMS, t043.SWEEP)}  # lineup match falls with delta, i.e. rises with rest
        v = synthetic_levels(0.60, 0.60, fwd, {name: 0.60 + 0.5 * delta for name, delta in zip(t043.SWEEP_ARMS, t043.SWEEP)})
        point = t043.effects(v)
        self.assertAlmostEqual(point['slope|fwd|top2'], 1.0)  # rest = 0.6 - 0.5 delta, top2 = 0.6 - 0.5 delta
        self.assertAlmostEqual(point['slope|rev|top2'], -1.0)
        self.assertEqual(t043.argmax_refs(v)[('fwd', 'top2')], 'fwd_o-0.0300')
        self.assertEqual(t043.argmax_refs(v)[('rev', 'top2')], 'rev_o+0.0300')
        self.assertEqual(point['amax|fwd|top2|fwd_o-0.0300'], 1.0)
        self.assertEqual(sum(point[f'amax|fwd|top2|{a}'] for a, _ in t043.curve_arms('fwd')), 1.0)
        self.assertAlmostEqual(point['amaxdelta|fwd|top2'], -0.03)
        self.assertAlmostEqual(point['amaxrest|fwd|top2'], 0.6 + 0.015)
        self.assertAlmostEqual(point['plateau|fwd|top2|fwd_base'], 0.015)
        report = t043.curve_report(point, banded(point, 0.01), 'fwd', 'top2')
        self.assertEqual((report['argmax'], report['shape'], report['base_in_plateau']), ('fwd_o-0.0300', 'boundary_high', False))
        self.assertEqual(report['plateau'], ['fwd_o-0.0300', 'fwd_o-0.0200'])  # within 0.01 of the best: the difference to the best is 0.01 (delta -0.02) -> interval [0, 0.02]
        cover = t043.covers(point)
        self.assertTrue(cover['fwd']['monotone_in_delta'])
        self.assertFalse(cover['fwd']['covers'])  # rest only spans 0.585 .. 0.615

    def test_an_interior_optimum_and_a_flat_curve(self):
        hump = {name: 0.60 - 40 * (delta + 0.005) ** 2 for name, delta in zip(t043.SWEEP_ARMS, t043.SWEEP)}
        v = synthetic_levels(0.60, 0.60, hump, {}, rest_slope=15.0)
        v['fwd_base']['top2'] = 0.60 - 40 * 0.005 ** 2
        report = t043.curve_report(t043.effects(v), banded(t043.effects(v), 0.0005), 'fwd', 'top2')
        self.assertEqual(report['shape'], 'interior')
        self.assertEqual(report['argmax'], 'fwd_o-0.0050')
        self.assertTrue(t043.covers(t043.effects(v))['fwd']['covers'])

    def test_summarise_shares_one_resample_and_keeps_the_frequency_of_the_argmax(self):
        def cell(top2_hits, rest):
            keys = {('start', 'top2'): [10, float(top2_hits)], ('act', 'rest'): [10, float(rest)], ('med', 'gap'): [10, 1.0], ('med', 'err'): [10, 1.0], ('fat', 'mean'): [10, 2.0], **{('med', p): [10, 0.2] for p in t043.PARTS}}
            return {**keys, **{('final', k): [1, 1.0] for k in t040.FINALS}}
        accs = {arm: [cell(6, 5)] * 4 for arm in t043.ARMS}
        accs['rev_base'] = [cell(7, 4)] * 4
        mats = [t043.cell_matrices({arm: {'acc': accs[arm][i]} for arm in t043.ARMS}) for i in range(4)]
        out = t043.summarise(accs, mats, boots=6)
        self.assertAlmostEqual(out['point']['D|top2'], 0.1)
        self.assertEqual(out['ci95']['D|top2'], [out['point']['D|top2']] * 2)
        self.assertEqual(out['cells'], 4)
        self.assertEqual(set(out['mediation']), set(t043.MED_GROUPS))
        self.assertTrue(all(k.startswith('amax|') for k in out['argmax_frequency']) and out['argmax_frequency'])
        self.assertTrue(all(0.0 <= f <= 1.0 for f in out['argmax_frequency'].values()))
        self.assertNotIn('amax|fwd|top2|fwd_base', out['ci95'])
        self.assertEqual(set(out['argmax_reference']), {f'{d}|{m}' for d in t043.DIRECTIONS for m in t043.OPTIMUM_METRICS})


class TestMachineryAgainstShortWorlds(unittest.TestCase):
    def test_the_base_arms_are_plain_runs(self):
        for arm, mode in (('fwd_base', 'legacy'), ('rev_base', 'centered_v1')):
            result = short_arm(arm)
            self.assertEqual(result['checked'], {})
            self.assertEqual(t043.plain_digest(result['world']), t043.plain_digest(plain(mode)))

    def test_neutral_shifts_change_nothing_on_the_tracked_and_the_untracked_path(self):
        for direction, mode in (('fwd', 'legacy'), ('rev', 'centered_v1')):
            tracked = t043.run_arm('t031', NAME, 100, 29, (direction, 'spread', None), days=SHORT, identity=True)
            self.assertTrue(all(n > 0 for n in tracked['checked'].values()), tracked['checked'])  # the tracker checked every observation and, every day, the whole real state
            self.assertEqual(t043.plain_digest(tracked['world']), t043.plain_digest(plain(mode)))
            untracked = t043.run_arm('t031', NAME, 100, 29, (direction, 'offset', 0.0), days=SHORT)
            self.assertEqual(t043.plain_digest(untracked['world']), t043.plain_digest(plain(mode)))

    def test_the_g_arm_is_the_g_arm_of_t042(self):
        for direction in ('fwd', 'rev'):
            ours = short_arm(f'{direction}_g')
            theirs = t042.run_arm('t031', NAME, 100, 29, f'{direction}_g', days=SHORT)
            self.assertEqual(t043.plain_digest(ours['world']), t043.plain_digest(theirs['world']), direction)
            self.assertEqual(ours['acc'][('start', 'top2')], theirs['acc'][('start', 'top2')])
            self.assertEqual(ours['acc'][('act', 'rest')], theirs['acc'][('act', 'practice', 'rest')])

    def test_the_patches_are_put_back(self):
        names = ('uniform_from_key', 'observation_noise', 'observe_fatigue', '_append_event', '_step_training_day', '_new_player_mind',
                 'decide_player_training', '_player_advice_response', 'decide_transfer_bidding')
        before = {n: getattr(le, n) for n in names}
        short_arm('fwd_S')
        short_arm('rev_o+0.0100')
        self.assertEqual({n: getattr(le, n) for n in names}, before)

    def test_the_sweep_moves_the_rest_rate_and_the_records_are_consistent(self):
        low, high = short_arm('fwd_o-0.0300')['acc'], short_arm('fwd_o+0.0300')['acc']
        rest = lambda acc: acc[('act', 'rest')][1] / acc[('act', 'rest')][0]
        self.assertGreater(rest(low), rest(high))
        n = low[('act', 'rest')][0]
        self.assertAlmostEqual(sum(low[('act', a)][1] for a in t043.ACTIONS), n)
        self.assertEqual(low[('start', 'top2')][0], 4 * (SHORT // 7))
        self.assertGreater(low[('med', 'gap')][0], 0)
        self.assertEqual(low[('med', 'mismatch')][1], 0.0)
        self.assertEqual(low[('fat', 'mean')][0], SHORT)
        self.assertAlmostEqual(sum(low[('med', p)][1] for p in t043.PARTS), low[('med', 'err')][1])
        self.assertNotIn(('gl', 'real'), low)  # untracked arms describe no alt column

    def test_a_tracked_arm_describes_the_alt_gains(self):
        acc = short_arm('fwd_S')['acc']
        self.assertEqual(acc[('shift', 'gain')][0], acc[('act', 'rest')][0])
        self.assertGreaterEqual(acc[('shiftabs', 'gain')][1], abs(acc[('shift', 'gain')][1]))

    def test_full_length_world_with_newcomers_and_transfers(self):
        result = t043.run_arm('t030', t023.conditions()[9], 101, 33, ('rev', 'spread', None), identity=True)  # 168 days: season ends, retirements, youth intake, a transfer window
        world = result['world']
        self.assertTrue(any(e['kind'] == 'season_youth_joined' for e in world.events))
        self.assertTrue(any(e['kind'] == 'transfer_window_decision' for e in world.events))
        self.assertEqual(t043.plain_digest(world), t043.plain_digest(le.run_league_world(seed=101, days=t043.DAYS, config=t036.make_cfg('t030', t023.conditions()[9], 33, 'centered_v1'))))


if __name__ == '__main__':
    unittest.main()
