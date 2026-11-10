"""T044: the arms, the shadow estimates and the four-term error split by hand, the recorder against short worlds (read-only, exact, identity with T043's arms), the contrasts and the pre-registered rules by hand. `soccer_sim/` is unchanged."""
from contextlib import ExitStack
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from experiments import t023, t036, t040, t043, t044, t044_growth
from soccer_sim import league_economy as le

NAME = t023.conditions()[3]
SHORT = 42  # two seasons: includes a match day, a transfer window and the first season end with its youth intake


def short_world(arm, days=SHORT, start=29):
    """The run loop of t044.run_arm, returning the tracker as well."""
    direction, kind, param = t044.ARMS[arm]
    config = t036.make_cfg('t031', NAME, start, t044.DIRECTIONS[direction][0])
    acc = {}
    world = le._build_initial_world(seed=100, config=config)
    tracker = t044.Shadows(world, acc)
    with ExitStack() as stack:
        stack.enter_context(t043.installed(None, kind, param, 0.0, acc))
        stack.enter_context(t044.recording(tracker))
        while world.day < days:
            le.advance_league_days(world, 1)
            tracker.end_of_day(world)
    return world, tracker, acc


class TestArmsAndConstants(unittest.TestCase):
    def test_twenty_arms_are_t043s_sweep_with_the_same_shapes(self):
        self.assertEqual(len(t044.ARMS), 20)
        self.assertEqual(t044.ARMS['fwd_base'], ('fwd', 'plain', None))
        self.assertEqual(t044.ARMS['rev_o-0.0300'], ('rev', 'offset', -0.03))
        self.assertEqual({spec[1] for spec in t044.ARMS.values()}, {'plain', 'offset'})
        self.assertTrue(all(t043.ARMS[arm] == spec for arm, spec in t044.ARMS.items()))
        self.assertEqual(len(t044.curve('fwd')), 10)
        self.assertTrue(all(arm in t044.ARMS for d in t044.DIRECTIONS for arm in t044.curve(d)))

    def test_the_registered_contrast_arms_exist_in_every_direction(self):
        self.assertEqual(t044.CONTRASTS, {'LO': ('o+0.0300', 'o-0.0100'), 'HI': ('o-0.0300', 'o-0.0100'), 'KN': ('o+0.0300', 'o+0.0100')})
        for high, low in t044.CONTRASTS.values():
            for d in t044.DIRECTIONS:
                self.assertIn(f'{d}_{high}', t044.ARMS)
                self.assertIn(f'{d}_{low}', t044.ARMS)

    def test_eight_components_and_the_cause_sums(self):
        self.assertEqual(len(t044.COMPONENTS), 8)
        self.assertEqual(sorted(t044.CAUSE_PARTS['stale']), sorted(['skill_prior', 'skill_lag', 'match_prior', 'match_lag']))
        self.assertEqual(sorted(sum((t044.CAUSE_PARTS[c] for c in ('noise', 'def', 'stale')), ())), sorted(t044.COMPONENTS))

    def test_the_t043_levels_check_passes_on_its_own_saved_values_and_fails_otherwise(self):
        saved = json.loads((ROOT / 'artifacts/t043/run/summary.json').read_text())['summaries']
        levels = {scope: {arm: {m: saved[scope]['point'][f'level|{arm}|{m}'] for m in t044.REPRODUCED} for arm in t044.ARMS} for scope in saved}
        self.assertEqual(t044.check_t043_reproduced(ROOT, levels)['max_abs_difference'], 0.0)
        levels['all']['fwd_base']['top2'] += 1e-9
        with self.assertRaises(AssertionError):
            t044.check_t043_reproduced(ROOT, levels)


class TestShadowArithmetic(unittest.TestCase):
    def test_blend_is_the_simulations_formula(self):
        self.assertEqual(t044.blend(0.50, (0.64, 0.36), 0.60), round(0.64 * 0.50 + 0.36 * 0.60, 6))
        self.assertEqual(t044.blend(0.50, (0.70, 0.30), 3.0), 0.98)  # clamped to the upper bound
        self.assertEqual(t044.fresh_cell(0.5, 0.5, 0.62, 21), {'est': {'R': [0.5, 0.5], 'A': [0.5, 0.5], 'I': [0.5, 0.5], 'Z': [0.62, 0.62]}, 'seen': [21, 21]})
        self.assertEqual(t044.blend(0.12, (0.5, 0.5), 0.0), 0.10)  # and the lower bound

    def test_the_four_terms_add_up_to_the_error(self):
        estimates = {'R': (0.50, 0.60), 'A': (0.52, 0.66), 'I': (0.55, 0.62), 'Z': (0.58, 0.64)}
        terms = t044.telescope(estimates, 1, 0.70)
        self.assertEqual({k: round(v, 12) for k, v in terms.items()}, {'noise': -0.06, 'def': 0.04, 'prior': -0.02, 'lag': -0.06})
        self.assertAlmostEqual(sum(terms.values()), 0.60 - 0.70, places=12)

    def test_components_by_hand(self):
        config = t036.make_cfg('t031', NAME, 29, 'legacy')
        # j under-estimated (noise -0.03 on skill, lag -0.02), k over-estimated (definition +0.04 on the match estimate)
        snapshot = {'p1': ({'R': (0.47, 0.60), 'A': (0.50, 0.60), 'I': (0.50, 0.60), 'Z': (0.50, 0.60)}, (1, 1)),
                    'p2': ({'R': (0.60, 0.64), 'A': (0.60, 0.64), 'I': (0.60, 0.60), 'Z': (0.60, 0.60)}, (1, 1))}
        truth = {'p1': 0.52, 'p2': 0.60}
        got = t044.components(snapshot, 'p1', 'p2', truth.get, config)
        self.assertAlmostEqual(got['skill_noise'], -config.select_skill_weight * (-0.03 - 0.0), places=12)
        self.assertAlmostEqual(got['skill_lag'], -config.select_skill_weight * ((0.50 - 0.52) - (0.60 - 0.60)), places=12)
        self.assertAlmostEqual(got['match_def'], -config.select_match_weight * (0.0 - 0.04), places=12)
        self.assertEqual(got['match_noise'], 0.0)
        err_skill = -config.select_skill_weight * ((0.47 - 0.52) - (0.60 - 0.60))  # the whole skill error of the pair
        self.assertAlmostEqual(sum(got[f'skill_{c}'] for c in t044.CAUSES), err_skill, places=12)

    def test_boundary_pair_is_t043s_choice(self):
        ranking = [{'player_id': 'a', 'score': 0.9}, {'player_id': 'b', 'score': 0.5}, {'player_id': 'c', 'score': 0.7}]
        self.assertEqual(t044.boundary_pair(ranking, {'a', 'b'}), ('b', 'c'))
        self.assertEqual(t044.boundary_pair(ranking, {'a', 'c'}), ('c', 'b'))

    def test_rating_performance_by_hand(self):
        self.assertAlmostEqual(t044.rating_performance(0.60, 1, 4, 2, True), 0.60 + 0.06 + 0.06 + 0.04 + 0.03, places=12)
        self.assertAlmostEqual(t044.rating_performance(0.60, 0, 0, 0, False), 0.59, places=12)

    def test_check_input_counts_clamped_values_and_rejects_values_outside_the_noise_range(self):
        world = le._build_initial_world(seed=100, config=t036.make_cfg('t031', NAME, 29, 'legacy'))
        tracker = t044.Shadows(world, {})
        tracker.check_input(0.62, 0.60, 0.02, t044.SKILL_BOUNDS)  # +width: allowed
        tracker.check_input(0.54, 0.60, 0.02, t044.SKILL_BOUNDS)  # -3 width: allowed (legacy)
        tracker.check_input(0.98, 0.60, 0.02, t044.SKILL_BOUNDS)  # pinned at the bound: counted, not checked
        self.assertEqual((tracker.checked['bounded_inputs'], tracker.checked['clamped_inputs']), (2, 1))
        with self.assertRaises(AssertionError):
            tracker.check_input(0.70, 0.60, 0.02, t044.SKILL_BOUNDS)
        with self.assertRaises(AssertionError):
            tracker.check_input(0.50, 0.60, 0.02, t044.SKILL_BOUNDS)


class TestRecorderOnShortWorlds(unittest.TestCase):
    def test_the_shadows_stay_exact_and_the_recorder_sees_every_kind_of_observation(self):
        world, tracker, acc = short_world('fwd_o+0.0100')
        checked = tracker.checked
        self.assertGreater(checked['lineup_terms'], 0)
        self.assertGreater(checked['observation_events'], 0)
        self.assertGreater(checked['day_estimates'], 0)
        self.assertGreater(acc[('mf', 'f')][0], 0)
        self.assertEqual(acc[('mf', 'f')][0], acc[('mf', 'fb')][0])
        self.assertFalse(tracker.snapshots == {} and acc[('cmp', 'round')][0] == 0)
        self.assertTrue(any(e['kind'] == 'coach_scout_observation' for e in world.events))

    def test_a_tampered_real_shadow_is_caught_at_the_end_of_the_day(self):
        world, tracker, _ = short_world('fwd_base', days=3)
        key = next(iter(tracker.cells))
        tracker.cells[key]['est']['R'][1] += 0.001
        with self.assertRaises(AssertionError):
            tracker.end_of_day(world)

    def test_newcomers_get_shadows_at_the_first_impression_and_a_true_start(self):
        world, tracker, _ = short_world('fwd_base', days=21, start=33)  # start age 33: the first season end retires veterans and brings youth
        newcomers = [pid for pid in world.players if int(pid[1:]) > 12]
        self.assertTrue(newcomers, 'the first season end brings youth')
        for coach in world.coaches:
            self.assertIn((coach, newcomers[0]), tracker.cells)  # end_of_day checked each of them against the simulation's own estimate
        joined = next(e for e in world.events if e['kind'] == 'season_youth_joined' and e['data']['player_id'] == newcomers[0])
        self.assertEqual(joined['day'], 21)

    def test_the_contributions_add_up_to_err_with_the_t043_terms(self):
        acc = t044.run_arm('t031', NAME, 100, 29, t044.ARMS['rev_o-0.0100'], days=SHORT)['acc']
        total = lambda key: acc[key][1]
        parts = sum(total(('cmp', c)) for c in (*t044.COMPONENTS, 'round')) + total(('med', 'sel')) + total(('med', 'fatigue'))
        self.assertAlmostEqual(parts, total(('med', 'err')), places=9)
        self.assertEqual(acc[('cmp', 'round')][0], acc[('med', 'err')][0])
        self.assertLess(abs(total(('cmp', 'round'))) / acc[('cmp', 'round')][0], t044.ROUNDING_TOLERANCE)

    def test_the_recorder_does_not_change_the_world_or_the_t043_numbers(self):
        for arm in ('fwd_base', 'rev_o+0.0100'):
            ours = t044.run_arm('t031', NAME, 100, 29, t044.ARMS[arm], days=SHORT)
            world, acc = ours['world'], ours['acc']
            theirs = t043.run_arm('t031', NAME, 100, 29, t044.ARMS[arm], days=SHORT)
            self.assertEqual(t040.plain_digest(world), t040.plain_digest(theirs['world']))
            for key in (('start', 'top2'), ('act', 'rest'), ('fat', 'mean'), ('med', 'err'), ('med', 'sel'), ('med', 'skill'), ('med', 'match')):
                self.assertEqual(acc[key], theirs['acc'][key], key)

    def test_run_arm_is_the_registered_arm_and_returns_the_gate_fields(self):
        result = t044.run_arm('t031', NAME, 100, 29, t044.ARMS['fwd_base'], days=SHORT)
        self.assertEqual(result['flat']['events'], len(result['world'].events))
        self.assertIn(('cmp', 'match_def'), result['acc'])

    def test_the_recorder_gate_function_passes_on_short_worlds(self):
        self.assertEqual(t044.check_recorder_is_read_only(days=21), {'runs': 24, 'all_equal': True})

    def test_the_event_log_function_is_put_back_also_after_an_exception(self):
        original = le._append_event
        world = le._build_initial_world(seed=100, config=t036.make_cfg('t031', NAME, 29, 'legacy'))
        with self.assertRaises(ValueError):
            with t044.recording(t044.Shadows(world, {})):
                self.assertIsNot(le._append_event, original)
                raise ValueError('stop')
        self.assertIs(le._append_event, original)


def synthetic_levels(slope=1.0):
    """Levels of one arm with every metric defined, for the statistics by hand."""
    levels = {m: 0.0 for m in t044.METRICS}
    levels.update({'top2': 0.7, 'med_err': 0.03, 'med_sel': -0.02, 'med_fatigue': 0.001, 'cmp_round': 0.0, 'bonus_f': 0.1, 'bonus_b': 0.06, 'bonus_ff': 0.012, 'bonus_fb': 0.0058})
    levels.update({f'cmp_{c}': 0.001 * i for i, c in enumerate(t044.COMPONENTS, start=1)})
    return levels


class TestStatistics(unittest.TestCase):
    def test_pearson_and_slope_by_hand(self):
        self.assertAlmostEqual(t044.pearson([1, 2, 3], [2, 4, 6]), 1.0)
        self.assertAlmostEqual(t044.pearson([1, 2, 3], [3, 2, 1]), -1.0)
        self.assertIsNone(t044.pearson([1, 1, 1], [1, 2, 3]))
        derived = t044.derive(synthetic_levels())
        self.assertAlmostEqual(derived['bonus_slope'], (0.0058 - 0.1 * 0.06) / (0.012 - 0.01), places=9)
        self.assertAlmostEqual(derived['cause_noise'], 0.001 * 1 + 0.001 * 5, places=12)
        self.assertAlmostEqual(derived['cause_stale'], 0.001 * (3 + 4 + 7 + 8), places=12)
        self.assertAlmostEqual(derived['cause_prior'] + derived['cause_lag'], derived['cause_stale'], places=12)
        self.assertAlmostEqual(derived['est_skill'] + derived['est_match'], sum(0.001 * i for i in range(1, 9)), places=12)

    def test_contrasts_and_shares_by_hand(self):
        v = {arm: synthetic_levels() for arm in t044.ARMS}
        for d in t044.DIRECTIONS:
            v[f'{d}_o+0.0300'].update({'med_err': 0.01, 'top2': 0.74, 'cmp_match_def': 0.001 * 6 - 0.012, 'med_sel': -0.02 - 0.008})  # err down by 0.02: the match definition 0.012 and the selection 0.008
        out = t044.effects(v)
        self.assertAlmostEqual(out['contrast|fwd|LO|med_err'], -0.02, places=12)
        self.assertAlmostEqual(out['contrast|fwd|LO|top2'], 0.04, places=12)
        self.assertAlmostEqual(out['share|rev|LO|cause_def'], 0.012 / 0.02, places=9)
        self.assertAlmostEqual(out['contrast|fwd|KN|med_err'], -0.02, places=12)  # the knee arm is the unchanged one
        self.assertAlmostEqual(out['contrast|fwd|HI|med_err'], 0.0, places=12)
        self.assertIsNone(out['share|fwd|HI|cause_def'])  # a zero change of err has no shares
        explained = sum(out[f'share|fwd|LO|cause_{c}'] for c in ('noise', 'def', 'stale', 'other'))
        self.assertAlmostEqual(explained, 1.0, places=9)  # the changes of the parts add up to the change of err
        self.assertIn('corr|fwd', out)

    def test_cause_classification_rules(self):
        def run(err, causes, shares):
            key = 'contrast|fwd|LO|'
            point = {key + 'med_err': err[0], **{key + f'cause_{c}': causes[c][0] for c in t044.MAIN_CAUSES}, **{f'share|fwd|LO|cause_{c}': shares[c] for c in t044.MAIN_CAUSES}}
            ci = {key + 'med_err': err[1], **{key + f'cause_{c}': causes[c][1] for c in t044.MAIN_CAUSES}}
            return t044.classify_cause(point, ci, 'fwd', 'LO')
        neg, zero = [-0.02, -0.01], [-0.01, 0.01]
        none_moved = {c: (0.0, zero) for c in t044.MAIN_CAUSES}
        self.assertEqual(run((-0.02, zero), none_moved, dict.fromkeys(t044.MAIN_CAUSES, 0.0))['class'], 'no_err_change')
        self.assertEqual(run((-0.02, neg), none_moved, dict.fromkeys(t044.MAIN_CAUSES, 0.0))['class'], 'none')
        one = {**none_moved, 'def': (-0.015, neg)}
        self.assertEqual(run((-0.02, neg), one, {'noise': 0.0, 'def': 0.75, 'stale': 0.25})['class'], 'single(def)')
        self.assertEqual(run((-0.02, neg), one, {'noise': 0.0, 'def': 0.4, 'stale': 0.25})['class'], 'partial(def)')
        two = {**one, 'stale': (-0.010, neg)}
        self.assertEqual(run((-0.02, neg), two, {'noise': 0.0, 'def': 0.55, 'stale': 0.55})['class'], 'multiple(def, stale)')
        self.assertEqual(run((-0.02, neg), two, {'noise': 0.0, 'def': 0.4, 'stale': 0.3})['class'], 'distributed(def, stale)')
        opposed = {**one, 'noise': (0.004, [0.002, 0.006])}
        got = run((-0.02, neg), opposed, {'noise': -0.2, 'def': 0.75, 'stale': 0.0})
        self.assertEqual((got['class'], got['flags']), ('single(def)', ['opposed(noise)']))
        up = run((0.02, [0.01, 0.02]), {**none_moved, 'stale': (0.015, [0.01, 0.02])}, {'noise': 0.0, 'def': 0.0, 'stale': 0.75})
        self.assertEqual(up['class'], 'single(stale)')  # the sign of the err change decides which direction counts

    def test_stale_split_and_the_too_little_rest_label(self):
        point = {'share|fwd|LO|cause_prior': 0.1, 'share|fwd|LO|cause_lag': 0.3, 'share|fwd|LO|cause_stale': 0.4}
        self.assertEqual(t044.stale_split(point, 'fwd', 'LO'), {'shares': {'prior': 0.1, 'lag': 0.3}, 'main': ['lag']})
        ci = {'contrast|fwd|KN|top2': [-0.02, -0.005], 'contrast|rev|KN|top2': [0.002, 0.01], 'contrast|fwd|KN|final_skill': [-0.01, 0.01]}
        self.assertEqual(t044.classify_hurt(ci, 'fwd', 'top2'), 'hurts')
        self.assertEqual(t044.classify_hurt(ci, 'rev', 'top2'), 'helps')
        self.assertEqual(t044.classify_hurt(ci, 'fwd', 'final_skill'), 'flat')

    def test_summarise_runs_on_synthetic_cells_and_shares_one_resample(self):
        def cell(shift):
            accs = {}
            for arm in t044.ARMS:
                acc = {}
                for m, key in t044.METRICS.items():
                    base = synthetic_levels()[m] + shift * 0.001 * (hash(arm) % 3)
                    for _ in range(2):
                        t044.add(acc, key, base)
                accs[arm] = acc
            return accs
        cells = [cell(i) for i in range(3)]
        out = t044.summarise({arm: [c[arm] for c in cells] for arm in t044.ARMS}, boots=5)
        self.assertEqual(out['cells'], 3)
        self.assertEqual(set(out['point']), set(out['ci95']))
        self.assertIn('contrast|rev|KN|top2', out['point'])


class TestGrowthProbe(unittest.TestCase):
    def test_population_sd_and_the_recorded_growth_by_hand(self):
        self.assertAlmostEqual(t044_growth.population_sd([1.0, 3.0]), 1.0)
        config = t036.make_cfg('t031', NAME, 29, 'legacy')
        ranking = [{'player_id': 'a', 'score': 0.9, 'components': {'skill_term': 0.0, 'match_term': 0.0, 'fatigue_penalty': 0.0}},
                   {'player_id': 'b', 'score': 0.5, 'components': {'skill_term': 0.0, 'match_term': 0.0, 'fatigue_penalty': 0.0}},
                   {'player_id': 'c', 'score': 0.7, 'components': {'skill_term': 0.0, 'match_term': 0.0, 'fatigue_penalty': 0.0}}]
        now = {'a': 0.80, 'b': 0.70, 'c': 0.60}  # true top two: a, b; the boundary pair is b (lowest score of the top two) and c
        history = {0: {'a': 0.70, 'b': 0.68, 'c': 0.50}}
        acc = {}
        t044_growth.record_growth(acc, config, [{'kind': 'lineup_decision', 'data': {'ranking': ranking}}, {'kind': 'other', 'data': {}}], history, 14, now.get)
        # day 14 is compared with day 0: a +0.10, b +0.02, c +0.10 -> the boundary pair b, c differ by 0.08
        self.assertAlmostEqual(acc[('gr', 'pair')][1], 0.08, places=12)
        self.assertAlmostEqual(acc[('gr', 'mean')][1], (0.10 + 0.02 + 0.10) / 3, places=12)
        self.assertEqual(acc[('gr', 'pair')][0], 1)  # only the lineup decision is recorded
        history = {0: {'a': 0.70, 'b': 0.68}, 6: {'a': 0.75, 'b': 0.60}}
        acc = {}
        t044_growth.record_growth(acc, config, [{'kind': 'lineup_decision', 'data': {'ranking': ranking}}], history, 20, now.get)  # day 20 vs day 6; the newcomer c has no past (change 0)
        self.assertAlmostEqual(acc[('gr', 'pair')][1], abs((0.70 - 0.60) - 0.0), places=12)

    def test_the_probe_changes_nothing(self):
        theirs = t044.run_arm('t031', NAME, 100, 29, t044.ARMS['rev_o-0.0100'], days=SHORT)['acc']
        ours = t044_growth.run_arm('t031', NAME, 100, 29, t044.ARMS['rev_o-0.0100'], days=SHORT)
        for key in (('start', 'top2'), ('act', 'rest'), ('med', 'err')):
            self.assertEqual(ours[key], theirs[key], key)
        self.assertEqual(ours[('gr', 'pair')][0], ours[('med', 'err')][0])


if __name__ == '__main__':
    unittest.main()
