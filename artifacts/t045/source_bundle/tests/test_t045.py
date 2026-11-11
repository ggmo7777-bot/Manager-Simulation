"""T045: the arms and the swap formulas by hand, the swap against short worlds (identity is read-only, the real function is put back, transfers keep the real estimates, the swap is effective), the contrasts and the pre-registered flattening and late-cost rules by hand.
`soccer_sim/` is unchanged."""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from experiments import t023, t036, t040, t043, t044, t045
from soccer_sim import league_economy as le

NAME = t023.conditions()[3]
SHORT = 84  # four seasons: match days, transfer windows and season ends with youth intake


def short_world(arm, days=SHORT, start=29):
    """t045.run_arm for a short world."""
    return t045.run_arm('t031', NAME, 100, start, arm, days=days)


def plain_world(direction, days=SHORT, start=29):
    return le.run_league_world(seed=100, days=days, config=t036.make_cfg('t031', NAME, start, t045.DIRECTIONS[direction][0]))


ESTIMATES = {'R': (0.50, 0.60), 'A': (0.52, 0.66), 'I': (0.55, 0.62), 'Z': (0.58, 0.64)}


class TestArmsAndFormulas(unittest.TestCase):
    def test_fifty_arms_five_points_five_variants_per_direction(self):
        self.assertEqual(len(t045.ARMS), 50)
        self.assertEqual(t045.split_arm('fwd_o+0.0100__nolag'), ('fwd_o+0.0100', 'nolag'))
        self.assertEqual(t045.split_arm('rev_base__none'), ('rev_base', 'none'))
        for arm in t045.ARMS:
            self.assertIn(t045.split_arm(arm)[0], t043.ARMS)
        self.assertEqual(t045.POINTS, ('o-0.0300', 'o-0.0100', 'base', 'o+0.0100', 'o+0.0300'))
        self.assertEqual(t045.VARIANTS, ('none', 'nolag', 'nodef', 'nonoise', 'nolagdef'))

    def test_the_registered_contrasts_are_t044s(self):
        self.assertEqual(t045.CONTRASTS, {name: t044.CONTRASTS[name] for name in t045.CONTRASTS})
        self.assertEqual(set(t045.CONTRASTS), {'LO', 'HI', 'KN'})

    def test_every_variant_by_hand(self):
        truth = 0.70
        expected = {'none': (0.50, 0.60), 'nolag': (0.50 - 0.58 + 0.70, 0.60 - 0.64 + 0.70), 'nodef': (0.50 - 0.52 + 0.55, 0.60 - 0.66 + 0.62),
                    'nonoise': (0.52, 0.66), 'nolagdef': (0.50 - 0.58 + 0.70 - 0.52 + 0.55, 0.60 - 0.64 + 0.70 - 0.66 + 0.62)}
        for variant, want in expected.items():
            got = t045.executed_estimates(ESTIMATES, truth, variant)
            self.assertAlmostEqual(got[0], want[0], places=12)
            self.assertAlmostEqual(got[1], want[1], places=12)

    def test_nolag_leaves_noise_definition_and_first_impression_in_the_estimate(self):
        terms = t044.telescope(ESTIMATES, 1, 0.70)
        self.assertAlmostEqual(t045.executed_estimates(ESTIMATES, 0.70, 'nolag')[1] - 0.70, terms['noise'] + terms['def'] + terms['prior'], places=12)
        self.assertAlmostEqual(t045.executed_estimates(ESTIMATES, 0.70, 'nodef')[1] - 0.70, terms['noise'] + terms['prior'] + terms['lag'], places=12)
        self.assertAlmostEqual(t045.executed_estimates(ESTIMATES, 0.70, 'nonoise')[1] - 0.70, terms['def'] + terms['prior'] + terms['lag'], places=12)

    def test_a_broken_variant_formula_is_caught(self):
        original = t045.DIRECT['nolag']
        t045.DIRECT['nolag'] = lambda r, a, i, z, t: r - z + t + 0.001
        try:
            with self.assertRaises(AssertionError):
                t045.executed_estimates(ESTIMATES, 0.70, 'nolag')
        finally:
            t045.DIRECT['nolag'] = original

    def test_the_t044_levels_check_passes_on_its_own_saved_values_and_fails_otherwise(self):
        saved = json.loads((ROOT / 'artifacts/t044/run/summary.json').read_text())['summaries']
        levels = {scope: {arm: {m: saved[scope]['point'][f'level|{arm}|{m}'] for m in t045.REPRODUCED} for arm in ('fwd_base', 'rev_o+0.0300')} for scope in saved}
        self.assertEqual(t045.check_t044_reproduced(ROOT, levels)['max_abs_difference'], 0.0)
        levels['all']['fwd_base']['top2'] += 1e-9
        with self.assertRaises(AssertionError):
            t045.check_t044_reproduced(ROOT, levels)


class TestSwapOnShortWorlds(unittest.TestCase):
    def test_the_identity_swap_changes_nothing(self):
        real = le.decide_club_lineup
        for direction in t045.DIRECTIONS:
            got = short_world(f'{direction}_base__none')
            self.assertEqual(t040.plain_digest(got['world']), t040.plain_digest(plain_world(direction)))
        self.assertIs(le.decide_club_lineup, real)

    def test_the_identity_swap_equals_t043s_arm_without_any_recorder(self):
        spec = t043.ARMS['fwd_o+0.0100']
        reference = t043.run_arm('t031', NAME, 100, 29, spec, days=SHORT)['world']
        self.assertEqual(t040.plain_digest(short_world('fwd_o+0.0100__none')['world']), t040.plain_digest(reference))

    def test_the_nolag_swap_changes_the_world_and_every_decision_checks_out(self):
        none = short_world('fwd_base__none')
        for variant in t045.SWAPPED:
            got = short_world(f'fwd_base__{variant}')
            self.assertGreater(got['checked']['swapped_decisions'], 0)
            self.assertGreater(got['checked']['lineup_terms'], 0)
            self.assertGreater(got['checked']['day_estimates'], 0)
            self.assertGreater(got['acc'][('x', 'abs_m')][0], 0)
        nolag = short_world('fwd_base__nolag')
        self.assertNotEqual(t040.plain_digest(nolag['world']), t040.plain_digest(none['world']))

    def test_nolag_shrinks_the_match_estimates_error_against_the_decision_moment_truth(self):
        """Manipulation check: the mean absolute error of the match-rating estimate the lineup reads is smaller with the lag taken out."""
        mean_abs = lambda arm: short_world(arm)['acc'][('x', 'abs_m')][1] / short_world(arm)['acc'][('x', 'abs_m')][0]
        self.assertLess(mean_abs('fwd_base__nolag'), mean_abs('fwd_base__none'))

    def test_transfer_calls_keep_the_real_estimates(self):
        world = le._build_initial_world(seed=100, config=t036.make_cfg('t031', NAME, 29, 'legacy'))
        tracker = t045.Interventions(world, {})
        club = next(iter(world.club_rosters))
        coach = world.coaches[le.CLUB_COACH_MAP[club]]
        roster = list(world.club_rosters[club])
        real = le.decide_club_lineup(coach=coach, roster=roster, config=world.config)
        with t045.lineup_swap(world, tracker, 'nolag'):
            self.assertEqual(le.decide_club_lineup(coach=coach, roster=roster, config=world.config), real)  # the caller is not the match-day step
            self.assertEqual(tracker.checked['swapped_decisions'], 0)

    def test_the_real_function_is_put_back_after_an_exception(self):
        world = le._build_initial_world(seed=100, config=t036.make_cfg('t031', NAME, 29, 'legacy'))
        real = le.decide_club_lineup
        with self.assertRaises(RuntimeError):
            with t045.lineup_swap(world, t045.Interventions(world, {}), 'nolag'):
                raise RuntimeError
        self.assertIs(le.decide_club_lineup, real)

    def test_a_tampered_swapped_term_is_caught(self):
        world = le._build_initial_world(seed=100, config=t036.make_cfg('t031', NAME, 29, 'legacy'))
        tracker = t045.Interventions(world, {})
        pid = next(iter(world.players))
        tracker.executed = {pid: (0.5, 0.5)}
        event = {'id': 'x', 'data': {'ranking': [{'player_id': pid, 'components': {'skill_term': 0.0, 'match_term': 0.0}}]}}
        with self.assertRaises(AssertionError):
            tracker.lineup(world, event)


def synthetic_levels(none, nolag):
    """v[arm][metric] with the lineup match given per point for `none` and `nolag` and the same for the other variants as `none`."""
    v = {}
    for d in t045.DIRECTIONS:
        for variant in t045.VARIANTS:
            values = nolag if variant == 'nolag' else none
            for point, top2 in zip(t045.POINTS, values):
                v[f'{d}_{point}__{variant}'] = {m: 0.0 for m in t045.METRICS} | {'top2': top2, 'med_err': 1.0 - top2, 'abs_m': 0.05 if variant != 'nolag' else 0.01}
    return v


NONE_CURVE = (0.70, 0.60, 0.62, 0.72, 0.74)  # HI end, valley, base, knee, LO end
FLAT_CURVE = (0.76, 0.74, 0.75, 0.77, 0.78)


class TestStatisticsByHand(unittest.TestCase):
    def test_effects_by_hand(self):
        out = t045.effects(synthetic_levels(NONE_CURVE, FLAT_CURVE))
        self.assertAlmostEqual(out['contrast|fwd|none|LO|top2'], 0.14, places=12)
        self.assertAlmostEqual(out['contrast|fwd|none|HI|top2'], 0.10, places=12)
        self.assertAlmostEqual(out['contrast|rev|nolag|KN|top2'], 0.01, places=12)
        self.assertAlmostEqual(out['dcontrast|fwd|nolag|LO|top2'], 0.04 - 0.14, places=12)
        self.assertAlmostEqual(out['ratio|fwd|nolag|LO'], 0.04 / 0.14, places=12)
        self.assertAlmostEqual(out['ratio|fwd|nolag|HI'], 0.02 / 0.10, places=12)
        self.assertAlmostEqual(out['valley_gain|fwd|nolag|top2'], 0.14, places=12)
        self.assertAlmostEqual(out['mean_gain|fwd|nolag|top2'], sum(f - n for f, n in zip(FLAT_CURVE, NONE_CURVE)) / 5, places=12)
        self.assertAlmostEqual(out['range|fwd|none'], 0.14, places=12)
        self.assertAlmostEqual(out['range|fwd|nolag'], 0.04, places=12)
        self.assertAlmostEqual(out['valley_gain|fwd|nolag|abs_m'], 0.01 - 0.05, places=12)
        self.assertAlmostEqual(out['valley_gain|fwd|nodef|top2'], 0.0, places=12)
        self.assertAlmostEqual(out['corr|fwd|none'], -1.0, places=12)  # err = 1 - top2 by construction

    def point_and_ci(self, none, variant_curve, width):
        point = t045.effects(synthetic_levels(none, variant_curve))
        return point, {k: [v - width, v + width] if v is not None else None for k, v in point.items()}

    def test_the_flattening_verdicts(self):
        for curve, width, want in ((FLAT_CURVE, 0.005, 'flattens'), (NONE_CURVE, 0.005, 'unchanged'), ((0.60, 0.60, 0.60, 0.60, 0.60), 0.005, 'removes'),
                                   ((0.70, 0.60, 0.62, 0.78, 0.90), 0.005, 'steepens'), ((0.64, 0.60, 0.62, 0.65, 0.66), 0.005, 'flattens')):
            point, ci = self.point_and_ci(NONE_CURVE, curve, width)
            self.assertEqual(t045.classify_flattening(point, ci, 'nolag')['verdict'], want, curve)

    def test_verdict_partial_inverts_and_undefined(self):
        point, ci = self.point_and_ci(NONE_CURVE, (0.70, 0.60, 0.62, 0.66, 0.66), 0.005)  # LO shrinks, HI unchanged
        self.assertEqual(t045.classify_flattening(point, ci, 'nolag')['verdict'], 'partial')
        point, ci = self.point_and_ci(NONE_CURVE, (0.70, 0.60, 0.62, 0.50, 0.40), 0.005)  # the valley is a peak
        self.assertEqual(t045.classify_flattening(point, ci, 'nolag')['verdict'], 'inverts')
        point, ci = self.point_and_ci((0.6, 0.6, 0.6, 0.6, 0.6), FLAT_CURVE, 0.005)
        self.assertEqual(t045.classify_flattening(point, ci, 'nolag')['verdict'], 'undefined')

    def test_a_wide_interval_blocks_flattens_and_removes(self):
        point, ci = self.point_and_ci(NONE_CURVE, FLAT_CURVE, 0.20)  # the difference to `none` cannot exclude zero
        self.assertEqual(t045.classify_flattening(point, ci, 'nolag')['verdict'], 'partial')

    def test_classify_hurt(self):
        ci = {'k': [-0.02, -0.01]}
        self.assertEqual(t045.classify_hurt(ci, 'k'), 'hurts')
        self.assertEqual(t045.classify_hurt({'k': [0.01, 0.02]}, 'k'), 'helps')
        self.assertEqual(t045.classify_hurt({'k': [-0.01, 0.02]}, 'k'), 'flat')

    def test_the_summary_resamples_cells_with_the_point_equal_to_the_full_sample(self):
        cell_accs = {arm: [{('start', 'top2'): [1, float(i % 2), 0.0, 0.0, 0.0]} for i in range(6)] for arm in ('a', 'b')}
        got = t045.summarise(cell_accs, 20, lambda v: {'diff': v['a']['top2'] - v['b']['top2'], 'a': v['a']['top2']}, {'top2': ('start', 'top2')})
        self.assertAlmostEqual(got['point']['a'], 0.5)
        self.assertEqual(got['point']['diff'], 0.0)
        self.assertEqual(got['cells'], 6)


class TestLongPart(unittest.TestCase):
    def test_windows_hold_the_days_of_the_window_and_the_final_values_at_its_last_day(self):
        got = t045.run_long_arm('t031', NAME, 100, 29, 'fwd_o+0.0100', days=42, window=21)
        first, second = got['windows']
        self.assertEqual(first[('fat', 'mean')][0], 21)
        self.assertEqual(second[('fat', 'mean')][0], 21)
        self.assertEqual(first[('final', 'skill')][0], 1)
        self.assertEqual(second[('final', 'skill')][0], 1)
        self.assertGreater(first[('start', 'top2')][0], 0)

    def test_the_base_arm_is_the_plain_world(self):
        got = t045.run_long_arm('t031', NAME, 100, 29, 'rev_base', days=42, window=21)
        plain = le.run_league_world(seed=100, days=42, config=t036.make_cfg('t031', NAME, 29, 'centered_v1'))
        self.assertEqual(got['flat'], {**{f'final_{k}': v for k, v in t045.t030.final_metrics(plain).items()}, 'events': len(plain.events)})

    def test_the_first_168_days_of_a_longer_run_equal_the_same_run_cut_at_168(self):
        short = t045.run_long_arm('t031', NAME, 100, 29, 'fwd_base', days=42, window=21)
        longer = t045.run_long_arm('t031', NAME, 100, 29, 'fwd_base', days=63, window=21)
        self.assertEqual(short['windows'][0], longer['windows'][0])
        self.assertEqual(short['windows'][1], longer['windows'][1])

    def test_late_is_windows_five_and_six(self):
        windows = [{('fat', 'mean'): [1, float(k), 0.0, 0.0, 0.0]} for k in range(1, 7)]
        groups = t045.grouped(windows)
        self.assertEqual(groups['late'][('fat', 'mean')][:2], [2, 11.0])
        self.assertEqual(groups['w1'], windows[0])

    def test_late_cost_rule(self):
        def ci(kn, drift):
            out = {}
            for d in t045.DIRECTIONS:
                for m in t045.LATE_COST_METRICS:
                    out[f'contrast|{d}|KN|{m}|late'] = kn
                    out[f'drift_contrast|{d}|KN|{m}'] = drift
            return out
        self.assertEqual(t045.late_cost(ci([-0.01, 0.01], [-0.01, 0.01]))['late_cost'], False)
        self.assertEqual(t045.late_cost(ci([-0.02, -0.01], [-0.01, 0.01]))['late_cost'], True)
        self.assertEqual(t045.late_cost(ci([0.01, 0.02], [-0.02, -0.01]))['growing_cost'], True)
        self.assertEqual(t045.late_cost(ci([0.01, 0.02], [-0.01, 0.01]))['growing_cost'], False)
        one = ci([-0.01, 0.01], [-0.01, 0.01])
        one['contrast|rev|KN|final_skill|late'] = [-0.02, -0.01]
        self.assertEqual(t045.late_cost(one)['kn_late']['rev|final_skill'], 'hurts')
        self.assertTrue(t045.late_cost(one)['late_cost'])

    def test_long_effects_by_hand(self):
        metrics = {f'{m}|{g}': 0.0 for m in t045.LONG_METRICS for g in t045.GROUPS}
        v = {arm: dict(metrics) for arm in t045.LONG_ARMS}
        for d in t045.DIRECTIONS:
            v[f'{d}_o+0.0300'].update({'fatigue|w1': 0.10, 'fatigue|w6': 0.12, 'top2|late': 0.70, 'top2|w1': 0.72, 'top2|w6': 0.68})
            v[f'{d}_o+0.0100'].update({'top2|late': 0.69, 'top2|w1': 0.70, 'top2|w6': 0.69})
        out = t045.long_effects(v)
        self.assertAlmostEqual(out['contrast|fwd|KN|top2|late'], 0.01, places=12)
        self.assertAlmostEqual(out['drift|fwd_o+0.0300|top2'], -0.04, places=12)
        self.assertAlmostEqual(out['drift_contrast|fwd|KN|top2'], (0.68 - 0.69) - (0.72 - 0.70), places=12)
        self.assertAlmostEqual(out['fatigue_ratio|rev_o+0.0300'], 1.2, places=12)
        self.assertIsNone(out['fatigue_ratio|rev_base'])


if __name__ == '__main__':
    unittest.main()
