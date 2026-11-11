"""T042: the subset view switch, the arms, the lineup decomposition, the mediation fit, Shapley shares and the pre-registered rules by hand, and the machinery against short worlds. `soccer_sim/` is unchanged."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from experiments import t023, t036, t040, t041, t042
from soccer_sim import league_economy as le

NAME = t023.conditions()[3]
SHORT = 42  # two seasons: includes a match day, a transfer window (28) and the first season end
PATCHED = ('uniform_from_key', 'observation_noise', 'observe_fatigue', '_append_event', '_step_training_day', '_new_player_mind',
           'decide_player_training', '_player_advice_response', 'decide_transfer_bidding')


def short_arm(arm, **kw):
    return t042.run_arm('t031', NAME, 100, 29, arm, days=SHORT, **kw)


def plain(mode, days=SHORT):
    return le.run_league_world(seed=100, days=days, config=t036.make_cfg('t031', NAME, 29, mode))


class TestArms(unittest.TestCase):
    def test_twenty_two_arms_with_the_registered_shapes(self):
        self.assertEqual(len(t042.ARMS), 22)
        self.assertEqual(t042.ARMS['fwd_base'], ('fwd', '', False))
        self.assertEqual(t042.ARMS['rev_gft'], ('rev', 'gft', False))
        self.assertEqual(t042.ARMS['fwd_R'], ('fwd', '', True))
        self.assertEqual(t042.ARMS['rev_tR'], ('rev', 't', True))
        self.assertEqual(t042.ARMS['fwd_gftR'], ('fwd', 'gft', True))
        self.assertEqual(set(shape[1] for shape in t042.ARMS.values() if shape[2]), {'', 't', 'gft'})
        self.assertEqual(sorted(shape[1] for a, shape in t042.ARMS.items() if a.startswith('fwd_')), sorted(['', 'g', 'f', 't', 'gf', 'gt', 'ft', 'gft', '', 't', 'gft']))


class TestSubsetView(unittest.TestCase):
    def setUp(self):
        self.world = le._build_initial_world(seed=100, config=t036.make_cfg('t031', NAME, 29, 'legacy'))
        self.acc = {}
        self.tracker = t042.Tracker(self.world, 'centered_v1', self.acc)
        self.pid = next(iter(self.world.player_minds))
        self.mind = self.world.player_minds[self.pid]
        self.mind.expected_gains.update({'A': 0.05, 'B': 0.04})
        self.mind.perceived_fatigue = 0.10
        self.tracker.cols[self.pid]['real'] = t041.mind_state(self.mind)
        self.coach = next(iter(self.mind.trust_by_coach))

    def make_alt(self, changes=None):
        alt = {**t041.mind_state(self.mind), **(changes or {})}
        self.tracker.cols[self.pid]['alt'] = alt
        return alt

    def choose(self, proposals=()):
        return le.decide_player_training(mind=self.mind, club_id='club_a', proposals=list(proposals), config=self.world.config)['chosen']['action']

    def test_only_the_named_states_are_swapped_and_all_are_restored(self):
        before = t041.mind_state(self.mind)
        alt = self.make_alt({'self': 0.91, 'fatigue': 0.33, 'gains': {'A': 0.1, 'B': 0.02}, 'trust': {c: 0.9 for c in before['trust']}})
        trust_dict, gains_dict = self.mind.trust_by_coach, self.mind.expected_gains
        for keys, expected in (('f', {**before, 'fatigue': 0.33}), ('g', {**before, 'gains': alt['gains']}), ('t', {**before, 'trust': alt['trust']}),
                               ('s', {**before, 'self': 0.91}), ('gft', {**alt, 'self': before['self']}), ('', before), ('gfts', alt)):
            with self.tracker.viewing(self.mind, keys):
                self.assertEqual(t041.mind_state(self.mind), expected, keys)
            self.assertEqual(t041.mind_state(self.mind), before)
        self.assertIs(self.mind.trust_by_coach, trust_dict)
        self.assertIs(self.mind.expected_gains, gains_dict)
        with self.assertRaises(ValueError):
            with self.tracker.viewing(self.mind, 'gf'):
                raise ValueError('stop')
        self.assertEqual(t041.mind_state(self.mind), before)

    def test_the_practice_choice_executes_only_the_registered_subset_and_records_each_one(self):
        self.make_alt({'fatigue': 0.90})  # the other mode's player feels exhausted; nothing else differs
        real = self.choose()
        self.assertNotEqual(real, 'rest')
        for keys, rests in (('f', True), ('gft', True), ('gf', True), ('g', False), ('t', False), ('', False)):
            with t042.installed(self.tracker, keys, False):
                self.assertEqual(self.choose() == 'rest', rests, keys)
        self.assertEqual(self.choose(), real)  # outside the context the plain function is back
        # six contexts x one call each: the flip against the plain choice is counted for fatigue, gft (and not for gains, trust, the self estimate)
        self.assertEqual([self.acc[('flip', f'P{k}')][:2] for k in ('g', 'f', 't', 'gft', 's')], [[6, 0.0], [6, 6.0], [6, 0.0], [6, 6.0], [6, 0.0]])
        self.assertEqual(self.acc[('act', 'practice', 'rest')][:2], [6, 3.0])  # the executed choice was rest in three of them (f, gft, gf)
        (key,) = [k for k in self.acc if k[:2] == ('trans', 'Pf')]
        self.assertEqual((key[2], key[4], key[5], self.acc[key][0]), (real, 'rest', 'rest:fatigue_recovery_need', 6))

    def test_the_self_estimate_is_not_read_by_the_practice_choice(self):
        self.make_alt({'self': 0.98})
        with t042.installed(self.tracker, 'gft', False):
            self.choose()
        self.assertEqual(self.acc[('flip', 'Ps')][:2], [1, 0.0])

    def test_the_shift_is_alt_minus_real(self):
        self.make_alt({'fatigue': 0.40, 'gains': {'A': 0.07, 'B': 0.06}})
        with t042.installed(self.tracker, '', False):
            self.choose()
        self.assertAlmostEqual(self.acc[('shift', 'fatigue')][1], 0.30)
        self.assertAlmostEqual(self.acc[('shift', 'gain')][1], 0.02)
        self.assertAlmostEqual(self.acc[('shift', 'trust')][1], 0.0)
        self.assertAlmostEqual(self.acc[('shiftabs', 'fatigue')][1], 0.30)

    def test_following_overrides_the_own_choice_and_the_rate_is_recorded(self):
        self.make_alt()
        self.mind.perceived_fatigue = 0.95
        self.tracker.cols[self.pid]['real'] = t041.mind_state(self.mind)
        self.assertEqual(self.choose(), 'rest')
        call = lambda mode: le.decide_player_training(mind=self.mind, club_id='club_a', config=self.world.config, advice_mode=mode,
                                                      proposals=[{'coach_id': self.coach, 'method': 'A', 'forecast': 0.05, 'club_id': 'club_a', 'decision_ref': 'e0'}])['chosen']['action']
        with t042.installed(self.tracker, '', False):
            self.assertEqual(call('follow'), 'A')
            self.assertEqual(call('blend'), 'rest')
        self.assertEqual(self.acc[('override', 'rest')][:2], [2, 1.0])  # only the follow call overrode a rest
        self.assertEqual(self.acc[('override', 'method')][:2], [2, 0.0])

    def test_response_channel_reads_the_alt_trust_only_when_switched(self):
        world = self.world
        props = [{'coach_id': self.coach, 'method': 'A', 'forecast': 0.05, 'club_id': 'club_a', 'decision_ref': 'e0'}]
        self.mind.trust_by_coach[self.coach], self.mind.autonomy = 0.10, 0.35
        low = le._player_advice_response(world, self.pid, props)['response']
        self.make_alt({'trust': {**self.mind.trust_by_coach, self.coach: 0.95}})
        with t042.installed(self.tracker, '', True):
            high = le._player_advice_response(world, self.pid, props)['response']
        with t042.installed(self.tracker, '', False):
            unchanged = le._player_advice_response(world, self.pid, props)['response']
        self.assertEqual((low, high, unchanged), ('refused', 'complied', 'refused'))
        self.assertEqual(self.acc[('flip', 'R')][:2], [2, 2.0])  # both contexts evaluated the alt state; only one executed it
        self.assertEqual(self.acc[('act', 'response', 'complied')][:2], [2, 1.0])


class TestLineupDecomposition(unittest.TestCase):
    class Config:
        select_skill_weight, select_match_weight = 0.55, 0.48

    def event(self, scores, starters):
        return {'kind': 'lineup_decision', 'data': {'ranking': [{'player_id': p, 'score': s} for p, s in scores.items()], 'starters': starters}}

    def test_gap_signal_and_err_by_hand(self):
        acc, truth = {}, {'a': 0.70, 'b': 0.60, 'c': 0.50}
        # the coach ranks b, a, c: right set {a, b}. signal = min(score a, score b) - score c = 0.58 - 0.40
        t042.record_mediators(acc, self.Config, [self.event({'a': 0.58, 'b': 0.60, 'c': 0.40}, ['b', 'a'])], truth.get)
        self.assertAlmostEqual(acc[('med', 'gap')][1], 0.10)
        self.assertAlmostEqual(acc[('med', 'signal')][1], 0.18)
        self.assertAlmostEqual(acc[('med', 'err')][1], 1.03 * 0.10 - 0.18)
        self.assertEqual((acc[('med', 'mismatch')][1], acc[('med', 'zero')][1]), (0.0, 0.0))
        # the coach prefers c over b: wrong set, signal < 0, and the sign matches the outcome
        t042.record_mediators(acc, self.Config, [self.event({'a': 0.58, 'b': 0.30, 'c': 0.40}, ['a', 'c'])], truth.get)
        self.assertAlmostEqual(acc[('med', 'signal')][1], 0.18 + (0.30 - 0.40))
        self.assertEqual(acc[('med', 'mismatch')][1], 0.0)

    def test_small_rosters_and_other_events_are_skipped(self):
        acc = {}
        t042.record_mediators(acc, self.Config, [self.event({'a': 0.5, 'b': 0.4}, ['a', 'b']), {'kind': 'other', 'data': {}}], {'a': 0.5, 'b': 0.4}.get)
        self.assertEqual(acc, {})


class TestShapleyAndMediation(unittest.TestCase):
    def test_shapley_sums_to_the_full_effect_and_splits_an_interaction_equally(self):
        additive = {'g': 2.0, 'f': 1.0, 't': 0.5, 'gf': 3.0, 'gt': 2.5, 'ft': 1.5, 'gft': 3.5}
        got = t042.shapley(additive)
        self.assertAlmostEqual(got['g'], 2.0)
        self.assertAlmostEqual(got['f'], 1.0)
        self.assertAlmostEqual(got['t'], 0.5)
        synergy = {'g': 0.0, 'f': 0.0, 't': 0.0, 'gf': 4.0, 'gt': 0.0, 'ft': 0.0, 'gft': 4.0}  # only g and f together work
        got = t042.shapley(synergy)
        self.assertAlmostEqual(got['g'], 2.0)
        self.assertAlmostEqual(got['f'], 2.0)
        self.assertAlmostEqual(got['t'], 0.0)
        self.assertAlmostEqual(sum(t042.shapley({'g': 3.0, 'f': 1.0, 't': 0.0, 'gf': 5.0, 'gt': 3.0, 'ft': 1.0, 'gft': 5.0}).values()), 5.0)

    @staticmethod
    def matrix(rows):
        s = [0.0] * 16
        for z in rows:
            for i in range(4):
                for j in range(4):
                    s[4 * i + j] += z[i] * z[j]
        return s

    def test_the_mediation_fit_recovers_the_paths_and_the_total_is_direct_plus_indirect(self):
        rows = []
        for r, e1, e2 in ((1.0, 0.5, -0.2), (2.0, -0.4, 0.3), (-1.0, 0.1, 0.4), (-2.0, 0.6, -0.5), (0.5, -0.3, -0.1), (-0.5, 0.2, 0.6), (1.5, -0.1, 0.2)):
            gap, err = 0.3 * r + e1, -0.1 * r + e2
            rows.append((r, gap, err, -0.5 * r + 2.0 * gap - 1.0 * err))
        got = t042.mediation(self.matrix(rows))
        self.assertAlmostEqual(got['c_direct'], -0.5)
        self.assertAlmostEqual(got['b_gap'], 2.0)
        self.assertAlmostEqual(got['b_err'], -1.0)
        self.assertAlmostEqual(got['c'], got['c_direct'] + got['ind_gap'] + got['ind_err'])
        self.assertAlmostEqual(got['prop'], (got['ind_gap'] + got['ind_err']) / got['c'])
        self.assertEqual(t042.mediation([0.0] * 16), dict.fromkeys(t042.MED_STATS))

    def test_a_singular_fit_keeps_the_total_and_the_a_paths(self):
        rows = [(r, 2.0 * r, 3.0 * r, r) for r in (1.0, 2.0, -1.0)]  # gap and err are exact multiples of rest
        got = t042.mediation(self.matrix(rows))
        self.assertAlmostEqual(got['c'], 1.0)
        self.assertAlmostEqual(got['a_gap'], 2.0)
        self.assertIsNone(got['b_gap'])

    def test_cell_matrices_group_the_arms(self):
        def acc(rest, gap, err, hit):
            return {('act', 'practice', 'rest'): [1, rest], ('med', 'gap'): [1, gap], ('med', 'err'): [1, err], ('start', 'top2'): [1, hit]}
        cell = {arm: {'acc': acc(0.5, 0.1, 0.2, 0.6)} for arm in t042.ARMS}
        for direction in t042.DIRECTIONS:
            for x in t042.ARM_X:
                cell[f'{direction}_{x}']['acc'] = acc(0.4, 0.2, 0.1, 0.7)  # every non-base arm: z = (-0.1, 0.1, -0.1, 0.1)
        got = t042.cell_matrices(cell)
        zz = (-0.1) * (-0.1)
        self.assertAlmostEqual(got['all'][0], 20 * zz)
        self.assertAlmostEqual(got['practice_only'][0], 14 * zz)  # R, tR, gftR (x2 directions) are not in it
        self.assertAlmostEqual(got['fwd'][0], 10 * zz)
        self.assertAlmostEqual(got['rev'][0], 10 * zz)
        self.assertAlmostEqual(got['all'][3], 20 * (-0.1) * 0.1)


def synthetic_levels(base_fwd, base_rev, fwd, rev):
    """v[arm][metric]: top2 levels from the arguments (an unnamed arm sits at the base); every other metric 0."""
    v = {arm: dict.fromkeys(t042.METRICS, 0.0) for arm in t042.ARMS}
    for arm in t042.ARMS:
        v[arm]['top2'] = base_fwd if arm.startswith('fwd') else base_rev
    for x, value in fwd.items():
        v[f'fwd_{x}']['top2'] = value
    for x, value in rev.items():
        v[f'rev_{x}']['top2'] = value
    return v


def banded(point, width=0.005):
    return {k: None if v is None else [v - width, v + width] for k, v in point.items()}


class TestRules(unittest.TestCase):
    def case(self, fwd, rev, base_fwd=0.60, base_rev=0.70):
        point = t042.effects(synthetic_levels(base_fwd, base_rev, fwd, rev))
        return point, banded(point)

    def test_effects_by_hand(self):
        point, _ = self.case({'g': 0.64, 'f': 0.58, 't': 0.60, 'gft': 0.66, 'R': 0.60, 'gftR': 0.62}, {'g': 0.66, 'gft': 0.64, 'R': 0.72, 'gftR': 0.70})
        self.assertAlmostEqual(point['D|top2'], 0.10)
        self.assertAlmostEqual(point['F|g|top2'], 0.04)
        self.assertAlmostEqual(point['R|g|top2'], 0.04)
        self.assertAlmostEqual(point['f|gft'], 0.6)
        self.assertAlmostEqual(point['IVF|top2'], 0.06 - (0.04 - 0.02 + 0.0))
        self.assertAlmostEqual(point['Fr|gft|top2'], 0.62 - 0.66)
        self.assertAlmostEqual(point['Rr|none|top2'], 0.70 - 0.72)  # rev_base - rev_R
        self.assertAlmostEqual(point['Rr|gft|top2'], 0.64 - 0.70)
        self.assertAlmostEqual(point['IrR|top2'], (0.64 - 0.70) - (0.70 - 0.72))
        self.assertAlmostEqual(sum(point[f'shapF|{v}'] for v in t042.VARS), point['F|gft|top2'])

    def test_variable_classification_follows_the_registered_order(self):
        single = self.case({'g': 0.66, 'gf': 0.66, 'gt': 0.66, 'gft': 0.66}, {'g': 0.64, 'gf': 0.64, 'gt': 0.64, 'gft': 0.64})
        got = t042.classify_variables(*single)
        self.assertEqual((got['class'], got['flags'], got['pair_carriers']), ('single_variable(gain)', [], ['gf', 'gt']))
        multi = t042.classify_variables(*self.case({'g': 0.66, 'f': 0.66, 'gft': 0.70}, {'g': 0.64, 'f': 0.64, 'gft': 0.60}))
        self.assertEqual(multi['class'], 'multiple_variables(gain, fatigue)')
        spread = t042.classify_variables(*self.case({'g': 0.63, 'f': 0.63, 'gft': 0.67}, {'g': 0.67, 'f': 0.67, 'gft': 0.63}))
        self.assertEqual(spread['class'], 'distributed(gain, fatigue)')
        partial = t042.classify_variables(*self.case({'g': 0.63, 'gft': 0.67}, {'g': 0.67, 'gft': 0.63}))
        self.assertEqual(partial['class'], 'partial_single(gain)')
        joint = t042.classify_variables(*self.case({'gft': 0.67}, {'gft': 0.63}))
        self.assertEqual(joint['class'], 'only_jointly')
        none = t042.classify_variables(*self.case({'g': 0.62}, {'g': 0.68}))
        self.assertEqual(none['class'], 'no_practice_gain')

    def test_flags(self):
        got = t042.classify_variables(*self.case({'g': 0.66, 'f': 0.57, 'gft': 0.70}, {'g': 0.64, 'f': 0.72, 'gft': 0.60}))
        self.assertIn('opposed(fatigue)', got['flags'])
        self.assertIn('interaction_vars', got['flags'])  # F_gft = 0.10 but the sum of the singles is 0.03
        quiet = t042.classify_variables(*self.case({'g': 0.66, 'gft': 0.66}, {'g': 0.64, 'gft': 0.64}))
        self.assertEqual(quiet['flags'], [])

    def test_harm_of_the_response(self):
        classify = lambda rev: t042.classify_harm(self.case({}, rev)[1])
        self.assertEqual(classify({'R': 0.72, 'gft': 0.64, 'gftR': 0.64}), 'practice_dependent')  # the harm vanishes when the practice state is switched too
        self.assertEqual(classify({'R': 0.72, 'gft': 0.64, 'gftR': 0.66}), 'practice_independent')
        self.assertEqual(classify({'R': 0.70, 'gft': 0.64, 'gftR': 0.64}), 'not_reproduced')
        self.assertEqual(classify({'R': 0.72, 'gft': 0.64, 'gftR': 0.65}), 'partly_dependent')

    def test_mediation_classes(self):
        ci = lambda lo, hi: [lo, hi]
        point = {'c': -0.5, 'ind_gap': -0.3, 'ind_err': -0.01, 'c_direct': -0.1, 'prop': 0.8}
        ci95 = {'c': ci(-0.6, -0.4), 'ind_gap': ci(-0.4, -0.2), 'ind_err': ci(-0.05, 0.03), 'c_direct': ci(-0.3, 0.1)}
        self.assertEqual(t042.classify_mediation({'point': point, 'ci95': ci95}), 'mediated(gap):complete')
        self.assertEqual(t042.classify_mediation({'point': {**point, 'prop': 0.5}, 'ci95': ci95}), 'mediated(gap):partial')
        self.assertEqual(t042.classify_mediation({'point': point, 'ci95': {**ci95, 'c': ci(-0.6, 0.1)}}), 'no_total')
        self.assertEqual(t042.classify_mediation({'point': point, 'ci95': {**ci95, 'ind_gap': ci(-0.4, 0.1)}}), 'unmediated')
        wrong_sign = {**point, 'ind_gap': 0.3}  # an indirect effect against the total does not count
        self.assertEqual(t042.classify_mediation({'point': wrong_sign, 'ci95': {**ci95, 'ind_gap': ci(0.2, 0.4)}}), 'unmediated')

    def test_recommendation_rule(self):
        point = {'f|gft': 0.9, 'r|gft': 0.8}
        ci = {'D|top2': [0.05, 0.12], 'D|final_skill': [0.001, 0.009], 'D|final_trust': [0.0, 0.01]}
        good = {'class': 'single_variable(gain)'}
        mediated = 'mediated(gap):partial'
        self.assertEqual(t042.recommend(good, 'practice_dependent', mediated, point, ci, 3), ('recommend', []))
        self.assertEqual(t042.recommend(good, 'not_reproduced', mediated, point, ci, 3)[0], 'recommend')
        self.assertEqual(t042.recommend(good, 'practice_independent', mediated, point, ci, 3), ('conditional', ['harm_response_unexplained']))
        self.assertEqual(t042.recommend(good, 'practice_dependent', 'unmediated', point, ci, 3), ('conditional', ['rest_to_lineup_unexplained']))
        self.assertEqual(t042.recommend({'class': 'only_jointly'}, 'practice_dependent', mediated, point, ci, 2), ('conditional', ['settings_with_positive_D', 'attribution_to_variables']))
        self.assertEqual(t042.recommend(good, 'practice_dependent', mediated, {**point, 'r|gft': 0.4}, ci, 3), ('conditional', ['gft_carries_gain']))
        self.assertEqual(t042.recommend(good, 'practice_dependent', mediated, point, {**ci, 'D|final_trust': [-0.01, 0.01]}, 3), ('conditional', ['final_trust']))
        self.assertEqual(t042.recommend(good, 'practice_dependent', mediated, point, {**ci, 'D|top2': [-0.01, 0.1]}, 3)[0], 'hold')
        self.assertEqual(t042.recommend(good, 'practice_dependent', mediated, point, {**ci, 'D|final_skill': [-0.001, 0.009]}, 3)[0], 'hold')

    def test_summarise_shares_one_resample_and_a_constant_has_a_point_interval(self):
        def cell(top2_hits, rest):
            acc = {('start', 'top2'): [10, float(top2_hits)], ('act', 'practice', 'rest'): [10, float(rest)], ('med', 'gap'): [10, 1.0], ('med', 'err'): [10, 1.0]}
            return {**acc, **{('final', k): [1, 1.0] for k in t040.FINALS}}
        accs = {arm: [cell(6, 5)] * 4 for arm in t042.ARMS}
        accs['rev_base'], accs['rev_gft'] = [cell(7, 4)] * 4, [cell(7, 4)] * 4
        mats = [t042.cell_matrices({arm: {'acc': accs[arm][i]} for arm in t042.ARMS}) for i in range(4)]
        out = t042.summarise(accs, mats, boots=6)
        self.assertAlmostEqual(out['point']['D|top2'], 0.1)
        self.assertEqual(out['ci95']['D|top2'], [out['point']['D|top2']] * 2)
        self.assertAlmostEqual(out['point']['R|gft|top2'], 0.0)
        self.assertEqual(out['cells'], 4)
        self.assertEqual(set(out['mediation']), set(t042.MED_GROUPS))


class TestMachineryAgainstShortWorlds(unittest.TestCase):
    def test_tracking_and_the_decision_wrappers_are_read_only_in_the_base_arms(self):
        for arm, mode in (('fwd_base', 'legacy'), ('rev_base', 'centered_v1')):
            result = short_arm(arm)  # run_arm asserts the reconstructed observations and, every day, the whole real state
            self.assertTrue(all(n > 0 for n in result['checked'].values()), result['checked'])
            self.assertEqual(t042.plain_digest(result['world']), t042.plain_digest(plain(mode)))

    def test_the_patches_are_put_back(self):
        before = {n: getattr(le, n) for n in PATCHED}
        short_arm('fwd_gftR')
        self.assertEqual({n: getattr(le, n) for n in PATCHED}, before)
        world = le._build_initial_world(seed=100, config=t036.make_cfg('t031', NAME, 29, 'legacy'))
        with self.assertRaises(RuntimeError):
            with t042.installed(t042.Tracker(world, 'centered_v1', {}), 'gf', True):
                raise RuntimeError('stop')
        self.assertEqual({n: getattr(le, n) for n in PATCHED}, before)

    def test_view_equal_to_the_world_changes_nothing_even_with_everything_switched(self):
        for arm, mode in (('fwd_gftR', 'legacy'), ('rev_gftR', 'centered_v1')):
            self.assertEqual(t042.plain_digest(short_arm(arm, identity_view=True)['world']), t042.plain_digest(plain(mode)))

    def test_the_gft_and_R_arms_are_the_practice_and_response_arms_of_t041(self):
        for ours, theirs in (('fwd_gft', 'fwd_practice'), ('rev_gft', 'rev_practice'), ('fwd_R', 'fwd_response'), ('rev_R', 'rev_response')):
            a, b = short_arm(ours), t041.run_arm('t031', NAME, 100, 29, theirs, days=SHORT)
            self.assertEqual(t042.plain_digest(a['world']), t042.plain_digest(b['world']), ours)
            self.assertEqual(a['acc'][('start', 'top2')], b['acc'][('start', 'top2')])
            self.assertEqual(a['acc'][('act', 'practice', 'rest')], b['acc'][('act', 'practice', 'rest')])

    def test_switching_changes_the_history(self):
        base = t042.plain_digest(short_arm('fwd_base')['world'])
        for arm in ('fwd_gft', 'fwd_gftR', 'fwd_R'):
            self.assertNotEqual(t042.plain_digest(short_arm(arm)['world']), base)

    def test_decision_counts_shares_and_the_lineup_decomposition(self):
        acc = short_arm('rev_gf')['acc']
        n = acc[('flip', 'Pg')][0]
        self.assertEqual(n, acc[('act', 'practice', 'rest')][0])
        for group, key in ((t042.ACTS, 'act'), (t042.REASONS, 'rsn')):
            self.assertAlmostEqual(sum(acc[(key, 'practice', v)][1] for v in group['practice']), n)
        self.assertEqual(acc[('flip', 'Ps')][1], 0.0)  # the self estimate is never read by the practice choice
        self.assertEqual(acc[('start', 'top2')][0], 4 * (SHORT // 7))
        self.assertLessEqual(acc[('med', 'gap')][0], acc[('start', 'top2')][0])  # rosters of fewer than three are skipped
        self.assertGreater(acc[('med', 'gap')][0], 0)
        self.assertEqual(acc[('med', 'mismatch')][1], 0.0)  # signal > 0 exactly when the lineup is the true top two
        self.assertEqual(t042.unit_values(acc)[3], acc[('start', 'top2')][1] / acc[('start', 'top2')][0])

    def test_full_length_world_with_newcomers_and_transfers(self):
        result = t042.run_arm('t030', t023.conditions()[9], 101, 33, 'rev_gftR', identity_view=True)  # 168 days: season ends, retirements, youth intake, a transfer window
        world = result['world']
        self.assertTrue(any(e['kind'] == 'season_youth_joined' for e in world.events))
        self.assertTrue(any(e['kind'] == 'transfer_window_decision' for e in world.events))
        self.assertEqual(t042.plain_digest(world), t042.plain_digest(le.run_league_world(seed=101, days=t042.DAYS, config=t036.make_cfg('t030', t023.conditions()[9], 33, 'centered_v1'))))


if __name__ == '__main__':
    unittest.main()
