"""T040: the estimate columns, the view switch, the composition of the training candidates, the arms and the pre-registered rules by hand, and the machinery against short worlds. `soccer_sim/` is unchanged."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from experiments import t023, t036, t038, t040
from soccer_sim import league_economy as le

NAME = t023.conditions()[0]
CFG = le.LeagueEconomyConfig()
SHORT = 42  # two seasons: includes a match day, a transfer window (28) and the first season end


def short_arm(arm, **kw):
    return t040.run_arm('t031', NAME, 100, 29, arm, days=SHORT, **kw)


def plain(mode, days=SHORT):
    return le.run_league_world(seed=100, days=days, config=t036.make_cfg('t031', NAME, 29, mode))


class TestArms(unittest.TestCase):
    def test_twelve_arms_with_the_registered_channels(self):
        self.assertEqual(len(t040.ARMS), 12)
        self.assertEqual({a for a, (_, c) in t040.ARMS.items() if not c}, {'fwd_base', 'rev_base'})
        self.assertEqual(t040.ARMS['fwd_all'][1], t040.CHANNELS)
        self.assertEqual(t040.ARMS['rev_advice'], ('rev', ('advice',)))
        self.assertEqual(t040.DIRECTIONS, {'fwd': ('legacy', 'centered_v1'), 'rev': ('centered_v1', 'legacy')})


class TestColumnsByHand(unittest.TestCase):
    def test_session_scout_match_updates_and_the_residual(self):
        vec = [0.5, 0.5, 0.10, 0.0, 0.0]
        got = t040.advance(CFG, vec, 'session', 0.60, 0.20, ('A', 0.04, 0.03))
        a, r = CFG.coach_skill_alpha, CFG.coach_residual_alpha
        self.assertAlmostEqual(got[0], 0.5 * (1 - a) + a * 0.60, places=6)
        self.assertEqual(got[1:3], [0.5, 0.20])  # no practice rating in the default config
        self.assertAlmostEqual(got[3], r * (0.04 - 0.03), places=6)
        self.assertEqual(got[4], 0.0)
        scout = t040.advance(CFG, vec, 'scout', 0.60, 0.20)
        self.assertAlmostEqual(scout[1], 0.7 * 0.5 + 0.3 * 0.60, places=6)
        match = t040.advance(CFG, vec, 'match', 0.70, 0.30)
        self.assertAlmostEqual(match[1], 0.5 * (1 - CFG.coach_match_alpha) + CFG.coach_match_alpha * 0.70, places=6)
        self.assertAlmostEqual(match[0], 0.5 * (1 - CFG.coach_match_skill_blend) + CFG.coach_match_skill_blend * 0.70, places=6)
        self.assertEqual(vec, [0.5, 0.5, 0.10, 0.0, 0.0])  # the input is not changed
        self.assertEqual(t040.advance(CFG, vec, 'session', 0.6, 0.2, ('B', 5.0, 0.0))[4], 0.08)  # the residual clamp

    def test_the_two_modes_differ_by_the_noise_identity(self):
        for u in (-1.0, -0.3, 0.0, 0.7, 1.0):
            for width in (0.020, 0.024, 0.030):
                legacy, centered = (le.observation_noise(u, width, m) for m in ('legacy', 'centered_v1'))
                self.assertAlmostEqual(legacy, 2 * centered - width, places=15)


class TestTrackerAgainstTheSimulation(unittest.TestCase):
    def test_real_columns_match_every_observation_exactly(self):
        for arm in ('fwd_base', 'rev_base'):  # run_arm asserts the reconstructed observations and the real column at every event
            result = short_arm(arm)
            self.assertTrue(all(n > 0 for n in result['checked'].values()), result['checked'])

    def test_tracking_is_read_only(self):
        for arm, mode in (('fwd_base', 'legacy'), ('rev_base', 'centered_v1')):
            result = short_arm(arm)
            self.assertEqual(t040.plain_digest(result['world']), t040.plain_digest(plain(mode)))

    def test_patches_are_put_back(self):
        names = ('uniform_from_key', 'observation_noise', 'observe_fatigue', '_append_event', '_step_training_day', 'decide_club_lineup',
                 'decide_transfer_release', 'decide_transfer_acquisition', 'decide_coach_training')
        before = {n: getattr(le, n) for n in names}
        short_arm('fwd_all')
        self.assertEqual({n: getattr(le, n) for n in names}, before)
        world = le._build_initial_world(seed=100, config=t036.make_cfg('t031', NAME, 29, 'legacy'))
        with self.assertRaises(RuntimeError):
            with t040.installed(t040.Tracker(world, 'centered_v1'), t040.CHANNELS):
                raise RuntimeError('stop')
        self.assertEqual({n: getattr(le, n) for n in names}, before)

    def test_alt_equals_the_other_mode_after_day_one(self):
        self.assertLessEqual(t040.day_one_deviation('t031', NAME, 100, 29), t040.DAY_ONE_TOLERANCE)
        self.assertLessEqual(t040.day_one_deviation('t030', NAME, 101, 33), t040.DAY_ONE_TOLERANCE)

    def test_alt_differs_from_real_later_on(self):
        world, tracker = t040.play(t036.make_cfg('t031', NAME, 29, 'legacy'), 100, 'centered_v1', (), days=SHORT)
        self.assertTrue(any(c['alt'] != c['real'] for c in tracker.cols.values()))
        self.assertEqual(tracker.cols.keys() >= {(c, p) for c, m in world.coaches.items() for p in m.evaluations}, True)  # newcomers have columns too

    def test_the_recorder_matches_t038_on_the_same_world(self):
        acc = short_arm('fwd_base')['acc']
        other = t038.run_cell(t036.make_cfg('t031', NAME, 29, 'legacy'), 100, days=SHORT)['acc']
        self.assertEqual(acc[('start', 'top2')], other[('start', 'true_top2_match')])


class TestViewSwitch(unittest.TestCase):
    def setUp(self):
        self.world = le._build_initial_world(seed=100, config=t036.make_cfg('t031', NAME, 29, 'legacy'))
        self.coach = next(iter(self.world.coaches.values()))
        self.tracker = t040.Tracker(self.world, 'centered_v1')
        self.pids = list(self.world.club_rosters[self.coach.club_id])

    def test_viewing_swaps_and_restores(self):
        pid = self.pids[0]
        before = t040.vector(self.coach.evaluations[pid])
        self.tracker.cols[(self.coach.coach_id, pid)]['alt'] = [0.91, 0.92, 0.93, 0.01, 0.02]
        with self.tracker.viewing(self.coach):
            self.assertEqual(t040.vector(self.coach.evaluations[pid]), [0.91, 0.92, 0.93, 0.01, 0.02])
        self.assertEqual(t040.vector(self.coach.evaluations[pid]), before)
        with self.assertRaises(ValueError):
            with self.tracker.viewing(self.coach):
                raise ValueError('stop')
        self.assertEqual(t040.vector(self.coach.evaluations[pid]), before)

    def lineup_from(self, caller_name):
        call = {'_step_matchday': lambda: le.decide_club_lineup(coach=self.coach, roster=self.pids, config=self.world.config),
                'other': lambda: le.decide_club_lineup(coach=self.coach, roster=self.pids, config=self.world.config)}
        call['_step_matchday'].__code__ = call['_step_matchday'].__code__.replace(co_name='_step_matchday')
        return call[caller_name]()

    def test_lineup_channel_switches_only_for_the_matchday_caller(self):
        pid = self.pids[-1]  # the player the real estimates rank last
        for other in self.pids:
            self.coach.evaluations[other].skill_estimate = 0.50
        self.coach.evaluations[pid].skill_estimate = 0.10
        self.tracker.cols[(self.coach.coach_id, pid)]['alt'] = [0.98, 0.98, 0.0, 0.0, 0.0]
        plain_lineup = self.lineup_from('other')
        self.assertNotIn(pid, plain_lineup['starters'])
        with t040.installed(self.tracker, ('lineup',)):
            self.assertIn(pid, self.lineup_from('_step_matchday')['starters'])
            self.assertNotIn(pid, self.lineup_from('other')['starters'])  # the transfer decisions' calls are not the lineup channel
        with t040.installed(self.tracker, ('allocation',)):
            self.assertNotIn(pid, self.lineup_from('_step_matchday')['starters'])
        self.assertEqual(self.coach.evaluations[pid].skill_estimate, 0.10)

    def test_transfer_channel_switches_the_whole_call(self):
        pid = self.pids[-1]
        outsider = next(p for p in self.world.coaches[next(iter(self.world.coaches))].evaluations if p not in self.pids)
        self.tracker.cols[(self.coach.coach_id, outsider)]['alt'] = [0.98, 0.98, 0.0, 0.0, 0.0]
        args = dict(coach=self.coach, roster=self.pids, player_id=outsider, config=self.world.config)
        real = le.decide_transfer_acquisition(**args)['target_valuation']
        with t040.installed(self.tracker, ('transfer',)):
            switched = le.decide_transfer_acquisition(**args)['target_valuation']
        with t040.installed(self.tracker, ('lineup',)):
            self.assertEqual(le.decide_transfer_acquisition(**args)['target_valuation'], real)
        self.assertGreater(switched, real)


class TestCompose(unittest.TestCase):
    def candidates(self, scores):
        kinds = [('observe', 'p1', None), ('propose', 'p1', 'A'), ('scout', 'p9', None), ('rest', None, None)]
        return [{'kind': k, 'target': t, 'method': m, 'score': s} for (k, t, m), s in zip(kinds, scores)]

    def test_candidates_come_from_the_switched_view_by_kind(self):
        base = {'coach_id': 'c1', 'slot': 1, 'chosen': None, 'candidates': self.candidates([0.30, 0.20, 0.10, 0.25]), 'obs_refs': ['e1']}
        alt = {'coach_id': 'c1', 'slot': 1, 'chosen': None, 'candidates': self.candidates([0.05, 0.50, 0.40, 0.25]), 'obs_refs': ['e1']}
        pick = lambda alloc, advice: t040.compose_training(base, alt, alloc, advice)
        self.assertEqual(pick(False, False)['chosen']['kind'], 'observe')  # 0.30 beats 0.20, 0.10, 0.25
        self.assertEqual(pick(True, False)['chosen']['kind'], 'scout')  # observe 0.05, scout 0.40, propose 0.20 (plain)
        self.assertEqual(pick(False, True)['chosen']['kind'], 'propose')  # propose 0.50 (alt), observe 0.30 (plain)
        both = pick(True, True)
        self.assertEqual([c['score'] for c in both['candidates']], [0.05, 0.50, 0.40, 0.25])
        self.assertEqual(both['chosen']['kind'], 'propose')
        self.assertEqual(pick(False, False)['candidates'], base['candidates'])  # nothing switched = the plain call
        self.assertEqual(list(both), list(base))

    def test_tie_break_is_the_simulations(self):
        tie = [{'kind': k, 'target': 'p1', 'method': None, 'score': 0.3} for k in ('observe', 'scout', 'propose')]
        self.assertEqual(max(tie, key=t040.candidate_rank)['kind'], 'propose')
        self.assertEqual(max(tie[:2], key=t040.candidate_rank)['kind'], 'observe')


class TestMachineryAgainstShortWorlds(unittest.TestCase):
    def test_view_equal_to_the_world_changes_nothing_even_with_all_channels_switched(self):
        for arm, mode in (('fwd_all', 'legacy'), ('rev_all', 'centered_v1')):
            result = short_arm(arm, identity_view=True)
            self.assertEqual(t040.plain_digest(result['world']), t040.plain_digest(plain(mode)))

    def test_full_length_world_with_newcomers_and_transfers(self):
        result = t040.run_arm('t030', NAME, 102, 33, 'rev_all', identity_view=True)  # 168 days: season ends, retirements, youth intake, both transfer windows
        world = result['world']
        self.assertTrue(any(e['kind'] == 'season_youth_joined' for e in world.events))
        self.assertEqual(t040.plain_digest(world), t040.plain_digest(le.run_league_world(seed=102, days=t040.DAYS, config=t036.make_cfg('t030', NAME, 33, 'centered_v1'))))

    def test_switching_with_the_other_mode_changes_the_history(self):
        base = t040.plain_digest(short_arm('fwd_base')['world'])
        self.assertNotEqual(t040.plain_digest(short_arm('fwd_all')['world']), base)
        self.assertNotEqual(t040.plain_digest(short_arm('fwd_lineup')['world']), base)

    def test_invariants_and_the_decision_count(self):
        result = short_arm('rev_all')
        self.assertEqual(result['acc'][('start', 'top2')][0], 4 * (SHORT // 7))
        self.assertEqual(result['acc'][('choice', 'observe')][0], result['acc'][('choice', 'rest')][0])


def synthetic_levels(base_fwd, base_rev, fwd, rev):
    """v[arm][metric]: top2 levels from the arguments; every other metric 0."""
    v = {arm: dict.fromkeys(t040.METRICS, 0.0) for arm in t040.ARMS}
    v['fwd_base']['top2'], v['rev_base']['top2'] = base_fwd, base_rev
    for x, value in fwd.items():
        v[f'fwd_{x}']['top2'] = value
    for x, value in rev.items():
        v[f'rev_{x}']['top2'] = value
    return v


class TestRules(unittest.TestCase):
    def test_effects_by_hand(self):
        v = synthetic_levels(0.60, 0.70, {'lineup': 0.66, 'transfer': 0.60, 'allocation': 0.62, 'advice': 0.60, 'all': 0.67}, {'lineup': 0.64, 'transfer': 0.70, 'allocation': 0.68, 'advice': 0.70, 'all': 0.62})
        e = t040.effects(v)
        self.assertAlmostEqual(e['D|top2'], 0.10)
        self.assertAlmostEqual(e['F|lineup|top2'], 0.06)
        self.assertAlmostEqual(e['R|lineup|top2'], 0.06)
        self.assertAlmostEqual(e['f|lineup'], 0.6)
        self.assertAlmostEqual(e['IF|top2'], 0.67 - 0.60 - (0.06 + 0 + 0.02 + 0))  # F_all minus the sum of the singles
        self.assertAlmostEqual(e['iF'], e['IF|top2'] / 0.10)
        self.assertAlmostEqual(e['IR|top2'], 0.08 - (0.06 + 0 + 0.02 + 0))
        self.assertAlmostEqual(e['f|all'], 0.7)

    def interval_for(self, point, width=0.005):
        return {k: None if v is None else [v - width, v + width] for k, v in point.items()}

    def classify_case(self, fwd, rev, base_fwd=0.60, base_rev=0.70, width=0.005):
        point = t040.effects(synthetic_levels(base_fwd, base_rev, fwd, rev))
        return t040.classify(point, self.interval_for(point, width)), point

    def test_classification_follows_the_registered_order(self):
        zero = dict.fromkeys(t040.CHANNELS, 0.60)
        zero_r = dict.fromkeys(t040.CHANNELS, 0.70)
        single, _ = self.classify_case({**zero, 'lineup': 0.66, 'all': 0.66}, {**zero_r, 'lineup': 0.64, 'all': 0.64})
        self.assertEqual(single['class'], 'single_route(lineup)')
        multi, _ = self.classify_case({**zero, 'lineup': 0.66, 'allocation': 0.66, 'all': 0.70}, {**zero_r, 'lineup': 0.64, 'allocation': 0.64, 'all': 0.60})
        self.assertEqual(multi['class'], 'multiple_routes(lineup, allocation)')
        spread, _ = self.classify_case({**zero, 'lineup': 0.63, 'allocation': 0.63, 'all': 0.67}, {**zero_r, 'lineup': 0.67, 'allocation': 0.67, 'all': 0.63})
        self.assertEqual(spread['class'], 'distributed(lineup, allocation)')
        partial, _ = self.classify_case({**zero, 'lineup': 0.63, 'all': 0.67}, {**zero_r, 'lineup': 0.67, 'all': 0.63})
        self.assertEqual(partial['class'], 'partial_single(lineup)')
        joint, _ = self.classify_case({**zero, 'all': 0.67}, {**zero_r, 'all': 0.63})
        self.assertEqual(joint['class'], 'only_jointly')
        outside, _ = self.classify_case({**zero, 'all': 0.62}, {**zero_r, 'all': 0.68})
        self.assertEqual(outside['class'], 'outside_coach_estimates')
        nothing, _ = self.classify_case({**zero, 'all': 0.60}, {**zero_r, 'all': 0.70})
        self.assertEqual(nothing['class'], 'outside_coach_estimates')  # the all-four arm recovers none of D: the gain is outside the four decisions
        flat, _ = self.classify_case(zero, zero_r, base_rev=0.601, width=0.02)
        self.assertEqual(flat['class'], 'no_gain')

    def test_flags(self):
        zero, zero_r = dict.fromkeys(t040.CHANNELS, 0.60), dict.fromkeys(t040.CHANNELS, 0.70)
        got, _ = self.classify_case({**zero, 'lineup': 0.66, 'allocation': 0.66, 'all': 0.80}, {**zero_r, 'lineup': 0.64, 'allocation': 0.64, 'all': 0.50})
        self.assertIn('interaction', got['flags'])  # F_all exceeds the sum of the singles by more than a quarter of D
        harmed, _ = self.classify_case({**zero, 'transfer': 0.55, 'lineup': 0.66, 'all': 0.66}, {**zero_r, 'lineup': 0.64, 'all': 0.64})
        self.assertIn('harm(transfer)', harmed['flags'])

    def test_recommendation_rule(self):
        point = {'f|all': 0.8, 'D|final_skill': 0.005}
        ci = {'D|top2': [0.05, 0.12], 'D|final_skill': [0.001, 0.009], 'D|final_trust': [0.0, 0.01]}
        good = {'class': 'single_route(lineup)', 'flags': []}
        self.assertEqual(t040.recommend(good, point, ci, 3), 'recommend')
        self.assertEqual(t040.recommend(good, point, ci, 2), 'conditional')  # a setting with a negative D
        self.assertEqual(t040.recommend(good, {**point, 'f|all': 0.4}, ci, 3), 'conditional')
        self.assertEqual(t040.recommend({'class': 'only_jointly', 'flags': []}, point, ci, 3), 'conditional')
        self.assertEqual(t040.recommend({'class': 'distributed(lineup, allocation)', 'flags': ['harm(transfer)']}, point, ci, 3), 'conditional')
        self.assertEqual(t040.recommend(good, point, {**ci, 'D|final_trust': [-0.01, 0.01]}, 3), 'conditional')
        self.assertEqual(t040.recommend(good, point, {**ci, 'D|top2': [-0.01, 0.1]}, 3), 'hold')
        self.assertEqual(t040.recommend(good, point, {**ci, 'D|final_skill': [-0.001, 0.009]}, 3), 'hold')

    def test_summarise_shares_one_resample_and_a_constant_has_a_point_interval(self):
        cells = lambda n_hits: [{('start', 'top2'): [10, float(h), 0.0, 0.0, 0.0], **{('final', k): [1, 1.0, 1.0, 1.0, 0.0] for k in t040.FINALS}} for h in n_hits]
        accs = {arm: cells([6] * 5) for arm in t040.ARMS}
        accs['rev_base'] = cells([7] * 5)
        accs['rev_all'] = cells([7] * 5)
        out = t040.summarise(accs, boots=20)
        self.assertAlmostEqual(out['point']['D|top2'], 0.1)
        self.assertEqual(out['ci95']['D|top2'], [out['point']['D|top2']] * 2)
        self.assertEqual(out['cells'], 5)
        self.assertAlmostEqual(out['point']['R|all|top2'], 0.0)


if __name__ == '__main__':
    unittest.main()
