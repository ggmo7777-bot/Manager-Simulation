"""T041: the player-state columns, the view switch, the researcher's labels of the decisions, the arms and the pre-registered rules by hand, and the machinery against short worlds. `soccer_sim/` is unchanged."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from experiments import t023, t036, t040, t041
from soccer_sim import league_economy as le

NAME = t023.conditions()[3]
CFG = le.LeagueEconomyConfig()
SHORT = 42  # two seasons: includes a match day, a transfer window (28) and the first season end
PATCHED = ('uniform_from_key', 'observation_noise', 'observe_fatigue', '_append_event', '_step_training_day', '_new_player_mind',
           'decide_player_training', '_player_advice_response', 'decide_transfer_bidding')


def short_arm(arm, **kw):
    return t041.run_arm('t031', NAME, 100, 29, arm, days=SHORT, **kw)


def plain(mode, days=SHORT):
    return le.run_league_world(seed=100, days=days, config=t036.make_cfg('t031', NAME, 29, mode))


class TestArms(unittest.TestCase):
    def test_twelve_arms_with_the_registered_channels(self):
        self.assertEqual(len(t041.ARMS), 12)
        self.assertEqual(t041.ARMS['fwd_base'], ('fwd', (), ()))
        self.assertEqual(t041.ARMS['rev_response'], ('rev', ('response',), ()))
        self.assertEqual(t041.ARMS['fwd_pall'], ('fwd', t041.PCHANNELS, ()))
        self.assertEqual(t041.ARMS['rev_both'], ('rev', t041.PCHANNELS, t040.CHANNELS))
        self.assertEqual(t041.PCHANNELS, ('practice', 'response', 'transfer'))


class TestStepsByHand(unittest.TestCase):
    STATE = {'self': 0.50, 'gains': {'A': 0.04, 'B': 0.05}, 'trust': {'c1': 0.56, 'c2': 0.30}, 'fatigue': 0.10}

    def test_self_gain_and_trust_updates(self):
        a = CFG.player_skill_alpha
        got = t041.self_step(CFG, self.STATE, 0.60, 0.20)
        self.assertAlmostEqual(got['self'], 0.5 * (1 - a) + a * 0.60, places=6)
        self.assertEqual((got['fatigue'], got['gains'], got['trust']), (0.20, self.STATE['gains'], self.STATE['trust']))
        b = CFG.player_gain_alpha
        got = t041.gain_step(CFG, self.STATE, 'B', 0.10)
        self.assertAlmostEqual(got['gains']['B'], 0.05 * (1 - b) + b * 0.10, places=6)
        self.assertEqual(got['gains']['A'], 0.04)
        self.assertEqual(t041.gain_step(CFG, self.STATE, 'A', 5.0)['gains']['A'], 0.18)  # clamp
        # the player's own forecast was off by 0.03, the coach's by 0.01: trust in that coach rises by trust_alpha * trust_scale * 0.02
        got = t041.trust_step(CFG, self.STATE, 'c1', 'A', 0.07, 0.06)
        self.assertAlmostEqual(got['trust']['c1'], 0.56 + CFG.trust_alpha * CFG.trust_scale * 0.02, places=6)
        self.assertEqual(got['trust']['c2'], 0.30)
        self.assertEqual(t041.trust_step(CFG, self.STATE, 'c2', 'A', 0.0, 0.18)['trust']['c2'], 0.05)  # the coach's forecast was far worse: clamp at the floor
        self.assertEqual(self.STATE['trust'], {'c1': 0.56, 'c2': 0.30})  # the input is not changed


class TestLabels(unittest.TestCase):
    def test_practice_reason_is_the_largest_positive_term_ties_alphabetical(self):
        method = {'chosen': {'action': 'A', 'components': {'expected_gain': 0.04, 'pref_bonus': 0.012, 'advice_term': 0.0, 'unsatisfied_drive': 0.0, 'fatigue_penalty': -0.02}}}
        self.assertEqual(t041.practice_signature(method), ('A', 'method:expected_gain'))
        tie = {'chosen': {'action': 'B', 'components': {'expected_gain': 0.02, 'pref_bonus': 0.02, 'advice_term': -0.0}}}
        self.assertEqual(t041.practice_signature(tie), ('B', 'method:expected_gain'))
        rest = {'chosen': {'action': 'rest', 'components': {'rest_preference': 0.012, 'fatigue_recovery_need': 0.03}}}
        self.assertEqual(t041.practice_signature(rest), ('rest', 'rest:fatigue_recovery_need'))
        self.assertEqual(t041.practice_signature({'chosen': {'action': 'A', 'components': {'fatigue_penalty': -0.02}}}), ('A', 'none'))

    def test_every_label_is_in_the_registered_list(self):
        reply = le.decide_advice_response(trust=0.6, autonomy=0.35, discipline=0.7, emotional_stability=0.5, playing_time_satisfaction=0.3)
        self.assertIn(t041.response_signature(reply)[1], t041.REASONS['response'])
        self.assertEqual(set(le._ADVICE_REASON_SIGNS['trust_minus_autonomy'] + le._ADVICE_REASON_SIGNS['discipline_term'] + (le._ADVICE_REASON_SIGNS['irritation_term'][1],) + ('no_dominant_term',)),
                         set(t041.REASONS['response']))

    def test_transfer_reason_by_hand(self):
        world = le._build_initial_world(seed=100, config=t036.make_cfg('t031', NAME, 29, 'legacy'))
        mind = next(iter(world.player_minds.values()))
        mind.playing_time_satisfaction, mind.ambition, mind.self_estimate = 0.30, 0.5, 0.60
        request = le.decide_player_transfer_request(mind=mind, bench_streak=4, config=world.config)
        # unsatisfied 0.58*0.18=0.1044, bench 0.075*4=0.30, ambition 0.18*0.5*0.18=0.0162: the bench term is the largest
        self.assertEqual(t041.transfer_signature({'wants_transfer': request['wants_transfer'], 'player_choice': {'chosen_to_club': 'club_b'}}, mind, 4)[1], 'bench_streak')
        self.assertEqual(t041.transfer_signature({'wants_transfer': True, 'player_choice': {'chosen_to_club': 'club_b'}}, mind, 0)[1], 'unsatisfied')
        mind.playing_time_satisfaction, mind.self_estimate = 0.9, 0.30
        self.assertEqual(t041.transfer_signature({'wants_transfer': False, 'player_choice': {'chosen_to_club': None}}, mind, 0), ('stays', 'none'))


class TestTrackerAgainstTheSimulation(unittest.TestCase):
    def test_real_columns_match_every_event_exactly(self):
        for arm in ('fwd_base', 'rev_base', 'fwd_both'):  # run_arm asserts the reconstructed observations, the feedback and, every day, the whole real state
            checked = short_arm(arm)['checked']
            self.assertTrue(all(n > 0 for n in checked.values()), checked)
            self.assertEqual(checked['days'], SHORT)

    def test_tracking_is_read_only(self):
        for arm, mode in (('fwd_base', 'legacy'), ('rev_base', 'centered_v1')):
            self.assertEqual(t041.plain_digest(short_arm(arm)['world']), t041.plain_digest(plain(mode)))

    def test_patches_are_put_back_also_after_an_error(self):
        before = {n: getattr(le, n) for n in PATCHED}
        short_arm('fwd_both')
        self.assertEqual({n: getattr(le, n) for n in PATCHED}, before)
        world = le._build_initial_world(seed=100, config=t036.make_cfg('t031', NAME, 29, 'legacy'))
        with self.assertRaises(RuntimeError):
            with t041.installed(t041.PlayerTracker(world, 'centered_v1', {}), t041.PCHANNELS):
                raise RuntimeError('stop')
        self.assertEqual({n: getattr(le, n) for n in PATCHED}, before)

    def test_alt_equals_the_other_mode_after_day_one(self):
        self.assertLessEqual(t041.day_one_deviation('t031', NAME, 100, 29), t040.DAY_ONE_TOLERANCE)
        self.assertLessEqual(t041.day_one_deviation('t030', NAME, 101, 33), t040.DAY_ONE_TOLERANCE)

    def test_alt_differs_from_real_later_and_newcomers_have_columns(self):
        acc = {}
        world, tracker, _ = t041.play(t036.make_cfg('t031', NAME, 33, 'legacy'), 100, 'centered_v1', (), (), acc, days=4 * 21)
        self.assertTrue(any(c['alt'] != c['real'] for c in tracker.cols.values()))
        self.assertEqual(set(tracker.cols), set(world.player_minds))
        self.assertGreater(len(world.player_minds), 12)  # retirements bring newcomers

    def test_the_closure_arm_is_the_other_modes_world(self):
        """Switching every decision to the other mode's state gives that world's decisions (post hoc observation, see the results)."""
        for both, other in (('fwd_both', 'rev_base'), ('rev_both', 'fwd_base')):
            a, b = short_arm(both), short_arm(other)
            self.assertEqual(a['flat']['events'], b['flat']['events'])
            for key in (('start', 'top2'), ('choice', 'propose'), ('promo', 'played')):
                self.assertEqual(a['acc'][key], b['acc'][key])


class TestViewSwitch(unittest.TestCase):
    def setUp(self):
        self.world = le._build_initial_world(seed=100, config=t036.make_cfg('t031', NAME, 29, 'legacy'))
        self.acc = {}
        self.tracker = t041.PlayerTracker(self.world, 'centered_v1', self.acc)
        self.pid = next(iter(self.world.player_minds))
        self.mind = self.world.player_minds[self.pid]

    def make_alt(self, changes):
        alt = {**t041.mind_state(self.mind), **changes}
        self.tracker.cols[self.pid]['alt'] = alt
        return alt

    def test_viewing_swaps_and_restores_in_place(self):
        before = t041.mind_state(self.mind)
        trust_dict, gains_dict = self.mind.trust_by_coach, self.mind.expected_gains
        alt = self.make_alt({'self': 0.91, 'fatigue': 0.33, 'gains': {'A': 0.1, 'B': 0.02}, 'trust': {c: 0.9 for c in before['trust']}})
        with self.tracker.viewing(self.mind):
            self.assertEqual(t041.mind_state(self.mind), alt)
        self.assertEqual(t041.mind_state(self.mind), before)
        self.assertIs(self.mind.trust_by_coach, trust_dict)
        self.assertIs(self.mind.expected_gains, gains_dict)
        with self.assertRaises(ValueError):
            with self.tracker.viewing(self.mind):
                raise ValueError('stop')
        self.assertEqual(t041.mind_state(self.mind), before)

    def test_practice_channel_switches_only_when_asked_and_records_the_difference(self):
        self.mind.expected_gains.update({'A': 0.05, 'B': 0.04})
        self.mind.perceived_fatigue = 0.10
        self.tracker.cols[self.pid]['real'] = t041.mind_state(self.mind)
        self.make_alt({'fatigue': 0.90})  # the other mode's player feels exhausted
        call = lambda: le.decide_player_training(mind=self.mind, club_id='club_a', proposals=[], config=self.world.config)['chosen']['action']
        real = call()
        self.assertNotEqual(real, 'rest')
        with t041.installed(self.tracker, ('practice',)):
            self.assertEqual(call(), 'rest')
        with t041.installed(self.tracker, ('response', 'transfer')):
            self.assertEqual(call(), real)
        self.assertEqual(call(), real)  # outside the context the plain function is back
        self.assertEqual(self.acc[('flip', 'practice')][:2], [2, 2.0])
        (key,) = [k for k in self.acc if k[:2] == ('trans', 'practice')]  # both calls changed the same way: the chosen method -> rest
        self.assertEqual((key[2], key[4], key[5], self.acc[key][0]), (real, 'rest', 'rest:fatigue_recovery_need', 2))
        # executed shares: the switched call executed `rest`, the other one `real`
        self.assertEqual(self.acc[('act', 'practice', 'rest')][:2], [2, 1.0])

    def test_response_channel_reads_the_alt_trust(self):
        world, coach = self.world, next(iter(self.mind.trust_by_coach))
        props = [{'coach_id': coach, 'method': 'A', 'forecast': 0.05, 'club_id': 'club_a', 'decision_ref': 'e0'}]
        self.mind.trust_by_coach[coach], self.mind.autonomy = 0.10, 0.35
        low = le._player_advice_response(world, self.pid, props)['response']
        self.make_alt({'trust': {**self.mind.trust_by_coach, coach: 0.95}})
        with t041.installed(self.tracker, ('response',)):
            high = le._player_advice_response(world, self.pid, props)['response']
        self.assertEqual((low, high), ('refused', 'complied'))
        self.assertEqual(self.acc[('flip', 'response')][:2], [1, 1.0])


def synthetic_levels(base_fwd, base_rev, fwd, rev):
    """v[arm][metric]: top2 levels from the arguments; every other metric 0."""
    v = {arm: dict.fromkeys(t041.METRICS, 0.0) for arm in t041.ARMS}
    v['fwd_base']['top2'], v['rev_base']['top2'] = base_fwd, base_rev
    for x, value in fwd.items():
        v[f'fwd_{x}']['top2'] = value
    for x, value in rev.items():
        v[f'rev_{x}']['top2'] = value
    return v


class TestRules(unittest.TestCase):
    ZERO_F, ZERO_R = dict.fromkeys(t041.EFFECT_KEYS, 0.60), dict.fromkeys(t041.EFFECT_KEYS, 0.70)

    def test_effects_by_hand(self):
        fwd = {'practice': 0.66, 'response': 0.60, 'transfer': 0.62, 'pall': 0.67, 'both': 0.70}
        rev = {'practice': 0.64, 'response': 0.70, 'transfer': 0.68, 'pall': 0.62, 'both': 0.60}
        e = t041.effects(synthetic_levels(0.60, 0.70, fwd, rev))
        self.assertAlmostEqual(e['D|top2'], 0.10)
        self.assertAlmostEqual(e['F|practice|top2'], 0.06)
        self.assertAlmostEqual(e['R|practice|top2'], 0.06)
        self.assertAlmostEqual(e['f|practice'], 0.6)
        self.assertAlmostEqual(e['IF|top2'], 0.07 - (0.06 + 0 + 0.02))
        self.assertAlmostEqual(e['IR|top2'], 0.08 - (0.06 + 0 + 0.02))
        self.assertAlmostEqual(e['JF|top2'], 0.03)  # what the coach side adds to the player side
        self.assertAlmostEqual(e['JR|top2'], 0.02)
        self.assertAlmostEqual(e['f|both'], 1.0)
        self.assertAlmostEqual(e['r|pall'], 0.8)

    def classify_case(self, fwd, rev, base_rev=0.70, width=0.005):
        point = t041.effects(synthetic_levels(0.60, base_rev, {**self.ZERO_F, **fwd}, {**self.ZERO_R, **rev}))
        return t041.classify(point, {k: None if v is None else [v - width, v + width] for k, v in point.items()})

    def test_classification_follows_the_registered_order(self):
        single = self.classify_case({'practice': 0.66, 'pall': 0.66}, {'practice': 0.64, 'pall': 0.64})
        self.assertEqual(single['class'], 'single_route(practice)')
        multi = self.classify_case({'practice': 0.66, 'response': 0.66, 'pall': 0.70}, {'practice': 0.64, 'response': 0.64, 'pall': 0.60})
        self.assertEqual(multi['class'], 'multiple_routes(practice, response)')
        spread = self.classify_case({'practice': 0.63, 'response': 0.63, 'pall': 0.67}, {'practice': 0.67, 'response': 0.67, 'pall': 0.63})
        self.assertEqual(spread['class'], 'distributed(practice, response)')
        partial = self.classify_case({'practice': 0.63, 'pall': 0.67}, {'practice': 0.67, 'pall': 0.63})
        self.assertEqual(partial['class'], 'partial_single(practice)')
        joint = self.classify_case({'pall': 0.67}, {'pall': 0.63})
        self.assertEqual(joint['class'], 'only_jointly')
        self.assertEqual(self.classify_case({}, {}, base_rev=0.601, width=0.02)['class'], 'no_gain')

    def test_outside_the_player_estimates_and_the_closure_flag(self):
        joint = self.classify_case({'pall': 0.62, 'both': 0.70}, {'pall': 0.68, 'both': 0.60})
        self.assertEqual(joint['class'], 'outside_player_estimates')
        self.assertIn('closure_both', joint['flags'])
        self.assertIn('coach_adds', joint['flags'])
        none = self.classify_case({'pall': 0.62, 'both': 0.62}, {'pall': 0.68, 'both': 0.68})
        self.assertIn('closure_none', none['flags'])
        self.assertNotIn('coach_adds', none['flags'])

    def test_flags(self):
        got = self.classify_case({'practice': 0.66, 'response': 0.66, 'pall': 0.80}, {'practice': 0.64, 'response': 0.64, 'pall': 0.50})
        self.assertIn('interaction', got['flags'])
        harmed = self.classify_case({'transfer': 0.55, 'practice': 0.66, 'pall': 0.66}, {'practice': 0.64, 'pall': 0.64})
        self.assertIn('harm(transfer)', harmed['flags'])

    def test_recommendation_rule(self):
        point = {'f|pall': 0.8, 'r|pall': 0.7}
        ci = {'D|top2': [0.05, 0.12], 'D|final_skill': [0.001, 0.009], 'D|final_trust': [0.0, 0.01]}
        good = {'class': 'single_route(practice)', 'flags': []}
        self.assertEqual(t041.recommend(good, point, ci, 3), 'recommend')
        self.assertEqual(t041.recommend(good, point, ci, 2), 'conditional')  # a setting with a negative D
        self.assertEqual(t041.recommend(good, {**point, 'f|pall': 0.4}, ci, 3), 'conditional')
        self.assertEqual(t041.recommend(good, {**point, 'r|pall': 0.4}, ci, 3), 'conditional')  # sufficient but not necessary
        self.assertEqual(t041.recommend({'class': 'only_jointly', 'flags': []}, point, ci, 3), 'conditional')
        self.assertEqual(t041.recommend({'class': 'outside_player_estimates', 'flags': ['closure_both']}, point, ci, 3), 'conditional')
        self.assertEqual(t041.recommend({'class': 'distributed(practice, response)', 'flags': ['harm(transfer)']}, point, ci, 3), 'conditional')
        self.assertEqual(t041.recommend(good, point, {**ci, 'D|final_trust': [-0.01, 0.01]}, 3), 'conditional')
        self.assertEqual(t041.recommend(good, point, {**ci, 'D|top2': [-0.01, 0.1]}, 3), 'hold')
        self.assertEqual(t041.recommend(good, point, {**ci, 'D|final_skill': [-0.001, 0.009]}, 3), 'hold')

    def test_summarise_shares_one_resample_and_a_constant_has_a_point_interval(self):
        cells = lambda hits: [{('start', 'top2'): [10, float(h), 0.0, 0.0, 0.0], **{('final', k): [1, 1.0, 1.0, 1.0, 0.0] for k in t040.FINALS}} for h in hits]
        accs = {arm: cells([6] * 5) for arm in t041.ARMS}
        accs['rev_base'], accs['rev_pall'] = cells([7] * 5), cells([7] * 5)
        out = t041.summarise(accs, boots=20)
        self.assertAlmostEqual(out['point']['D|top2'], 0.1)
        self.assertEqual(out['ci95']['D|top2'], [out['point']['D|top2']] * 2)
        self.assertEqual(out['cells'], 5)
        self.assertAlmostEqual(out['point']['R|pall|top2'], 0.0)


class TestMachineryAgainstShortWorlds(unittest.TestCase):
    def test_view_equal_to_the_world_changes_nothing_even_with_all_seven_channels_switched(self):
        for arm, mode in (('fwd_both', 'legacy'), ('rev_both', 'centered_v1')):
            self.assertEqual(t041.plain_digest(short_arm(arm, identity_view=True)['world']), t041.plain_digest(plain(mode)))

    def test_full_length_world_with_newcomers_and_transfers(self):
        result = t041.run_arm('t030', t023.conditions()[9], 101, 33, 'rev_both', identity_view=True)  # 168 days: season ends, retirements, youth intake, a transfer window
        world = result['world']
        self.assertTrue(any(e['kind'] == 'season_youth_joined' for e in world.events))
        self.assertTrue(any(e['kind'] == 'transfer_window_decision' for e in world.events))
        self.assertEqual(t041.plain_digest(world), t041.plain_digest(le.run_league_world(seed=101, days=t041.DAYS, config=t036.make_cfg('t030', t023.conditions()[9], 33, 'centered_v1'))))

    def test_switching_with_the_other_mode_changes_the_history(self):
        base = t041.plain_digest(short_arm('fwd_base')['world'])
        for arm in ('fwd_practice', 'fwd_response', 'fwd_pall'):
            self.assertNotEqual(t041.plain_digest(short_arm(arm)['world']), base)

    def test_decision_counts_and_reason_shares_sum_to_one(self):
        acc = short_arm('rev_pall')['acc']
        self.assertEqual(acc[('start', 'top2')][0], 4 * (SHORT // 7))
        for channel in ('practice', 'response'):
            n = acc[('flip', channel)][0]
            for group in (t041.ACTS, t041.REASONS):
                key = 'act' if group is t041.ACTS else 'rsn'
                self.assertAlmostEqual(sum(acc[(key, channel, v)][1] for v in group[channel]), n)


if __name__ == '__main__':
    unittest.main()
