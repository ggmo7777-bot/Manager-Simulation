"""T046: the arms and the swap formulas by hand, the swap against short worlds (identity is read-only, the real function is put back, transfers keep the real estimates, a partial swap touches only its targets,
the true-fatigue swap is exact), the statistics and the pre-registered rules (dose response, carrier, residual slope) by hand, and the reproduction checks of T044/T045 saved values.
`soccer_sim/` is unchanged."""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from experiments import t023, t036, t040, t043, t045, t046
from soccer_sim import league_economy as le

NAME = t023.conditions()[3]
SHORT = 42  # two seasons: match days and one season end


def short_world(arm, days=SHORT, start=29):
    return t046.run_arm('t031', NAME, 100, start, arm, days=days)


def plain_world(direction, days=SHORT, start=29):
    return le.run_league_world(seed=100, days=days, config=t036.make_cfg('t031', NAME, start, t046.DIRECTIONS[direction][0]))


ESTIMATES = {'R': (0.50, 0.60), 'A': (0.52, 0.66), 'I': (0.55, 0.62), 'Z': (0.58, 0.64)}


class TestArmsAndFormulas(unittest.TestCase):
    def test_seventy_four_arms(self):
        self.assertEqual(len(t046.ARMS), 74)
        self.assertEqual(t046.VARIANTS, ('none', 'd25', 'd50', 'd75', 'nolag', 'st', 'be', 'bd', 'nolagfat'))
        self.assertEqual(sum(1 for _, _, v in t046.ARMS.values() if v == 'none'), 10)
        self.assertEqual(sum(1 for _, p, _ in t046.ARMS.values() if p == 'base'), 2)  # `base` only for `none`
        self.assertEqual(t046.split_arm('rev_o+0.0300__nolagfat'), ('rev_o+0.0300', 'nolagfat'))
        for arm in t046.ARMS:
            self.assertIn(t046.split_arm(arm)[0], t043.ARMS)
        self.assertEqual(t046.CONTRASTS, t045.CONTRASTS)

    def test_lagged_estimates_by_hand(self):
        truth = 0.70
        for lam in (0.0, 0.25, 0.5, 0.75):
            got = t046.lagged_estimates(ESTIMATES, truth, lam)
            self.assertAlmostEqual(got[0], 0.50 - lam * (0.58 - 0.70), places=12)
            self.assertAlmostEqual(got[1], 0.60 - lam * (0.64 - 0.70), places=12)
        self.assertEqual(t046.lagged_estimates(ESTIMATES, truth, 0.0), (0.50, 0.60))

    def test_full_removal_is_t045s_nolag_bit_for_bit(self):
        for truth in (0.70, 0.3141592653589793):
            self.assertEqual(t046.lagged_estimates(ESTIMATES, truth, 1), t045.executed_estimates(ESTIMATES, truth, 'nolag'))

    def test_the_lag_terms_removal_is_linear_in_the_share(self):
        truth = 0.70
        r, half, full = (t046.lagged_estimates(ESTIMATES, truth, lam)[1] for lam in (0.0, 0.5, 1.0))
        self.assertAlmostEqual(half, (r + full) / 2, places=12)

    def test_targets_by_rank(self):
        ranked = ['a', 'b', 'c']
        self.assertEqual(t046.lag_targets(ranked, 'all'), ['a', 'b', 'c'])
        self.assertEqual(t046.lag_targets(ranked, 'starters'), ['a', 'b'])
        self.assertEqual(t046.lag_targets(ranked, 'bench'), ['c'])
        self.assertEqual(t046.lag_targets(ranked, 'boundary'), ['b', 'c'])
        self.assertEqual(t046.lag_targets(['a', 'b', 'c', 'd'], 'bench'), ['c', 'd'])

    def test_variant_specs_are_the_registered_table(self):
        self.assertEqual(t046.VARIANT_SPEC['d50'], (0.5, 'all', False))
        self.assertEqual(t046.VARIANT_SPEC['bd'], (1.0, 'boundary', False))
        self.assertEqual(t046.VARIANT_SPEC['nolagfat'], (1.0, 'all', True))
        self.assertEqual([t046.VARIANT_SPEC[v][0] for v in t046.DOSE_ORDER], [0.0, 0.25, 0.5, 0.75, 1.0])

    def test_saved_level_checks_pass_on_saved_values_and_fail_otherwise(self):
        saved = json.loads((ROOT / 'artifacts/t045/run/summary.json').read_text())['summaries']
        arms = ('fwd_o+0.0300__nolag', 'rev_base__none')
        levels = {scope: {arm: {m: saved[scope]['point'][f'level|{arm}|{m}'] for m in t046.REPRODUCED_T045} for arm in arms} for scope in saved}
        self.assertEqual(t046.check_saved_levels(ROOT, 't045', levels, ('none', 'nolag'), t046.REPRODUCED_T045)['max_abs_difference'], 0.0)
        levels['all']['fwd_o+0.0300__nolag']['top2'] += 1e-9
        with self.assertRaises(AssertionError):
            t046.check_saved_levels(ROOT, 't045', levels, ('none', 'nolag'), t046.REPRODUCED_T045)

    def test_the_true_fatigue_check_fails_on_an_inexact_swap(self):
        exact = {'fwd_o+0.0100__nolagfat': {'abs_f': 0.0, 'bias_f': 0.0}, 'fwd_o+0.0100__nolag': {'abs_f': 0.1, 'bias_f': -0.1}}
        self.assertEqual(t046.check_fatigue_swap_is_exact(exact)['nolagfat_arms'], 1)
        exact['fwd_o+0.0100__nolagfat']['abs_f'] = 1e-9
        with self.assertRaises(AssertionError):
            t046.check_fatigue_swap_is_exact(exact)


class TestSwapOnShortWorlds(unittest.TestCase):
    def test_the_identity_swap_changes_nothing_and_puts_the_function_back(self):
        real = le.decide_club_lineup
        for direction in t046.DIRECTIONS:
            got = short_world(f'{direction}_base__none')
            self.assertEqual(t040.plain_digest(got['world']), t040.plain_digest(plain_world(direction)))
            self.assertGreater(got['checked']['untouched_players'], 0)
        self.assertIs(le.decide_club_lineup, real)

    def test_the_identity_swap_equals_t043s_arm_without_any_recorder(self):
        reference = t043.run_arm('t031', NAME, 100, 29, t043.ARMS['fwd_o+0.0100'], days=SHORT)['world']
        self.assertEqual(t040.plain_digest(short_world('fwd_o+0.0100__none')['world']), t040.plain_digest(reference))

    def test_every_variant_runs_checks_out_and_the_swaps_change_the_world(self):
        none = t040.plain_digest(short_world('fwd_o+0.0100__none')['world'])
        changed = 0
        for variant in t046.SWAPPED:
            got = short_world(f'fwd_o+0.0100__{variant}')
            self.assertGreater(got['checked']['swapped_decisions'], 0)
            self.assertGreater(got['checked']['fatigue_terms'], 0)
            self.assertGreater(got['checked']['lineup_terms'], 0)
            self.assertGreater(got['checked']['day_estimates'], 0)
            changed += t040.plain_digest(got['world']) != none
        self.assertGreater(changed, 0)

    def test_nolag_with_full_removal_equals_t045s_nolag_world(self):
        ours = short_world('fwd_o+0.0100__nolag')
        theirs = t045.run_arm('t031', NAME, 100, 29, 'fwd_o+0.0100__nolag', days=SHORT)
        self.assertEqual(t040.plain_digest(ours['world']), t040.plain_digest(theirs['world']))
        self.assertEqual(ours['acc'][('start', 'top2')], theirs['acc'][('start', 'top2')])

    def test_a_partial_swap_touches_only_its_targets(self):
        everyone = short_world('fwd_o+0.0100__nolag')['checked']
        for variant, untouched_per_decision in (('st', 1), ('be', 2), ('bd', 1)):
            checked = short_world(f'fwd_o+0.0100__{variant}')['checked']
            self.assertEqual(checked['untouched_players'], untouched_per_decision * checked['swapped_decisions'], variant)  # the 3-player rosters
        self.assertEqual(everyone['untouched_players'], 0)

    def test_the_true_fatigue_swap_is_exact_and_the_real_fatigue_estimate_is_not(self):
        exact = short_world('fwd_o+0.0100__nolagfat')['acc']
        self.assertEqual((exact[('x', 'abs_f')][1], exact[('x', 'bias_f')][1]), (0.0, 0.0))
        real = short_world('fwd_o+0.0100__nolag')['acc']
        self.assertGreater(real[('x', 'abs_f')][1] / real[('x', 'abs_f')][0], 0.0)

    def test_a_larger_share_shrinks_the_match_estimates_error(self):
        mean_abs = lambda arm: short_world(arm)['acc'][('x', 'abs_m')][1] / short_world(arm)['acc'][('x', 'abs_m')][0]
        errors = [mean_abs(f'fwd_o+0.0100__{v}') for v in ('none', 'd50', 'nolag')]
        self.assertGreater(errors[0], errors[1])
        self.assertGreater(errors[1], errors[2])

    def test_transfer_calls_keep_the_real_estimates(self):
        world = le._build_initial_world(seed=100, config=t036.make_cfg('t031', NAME, 29, 'legacy'))
        tracker = t046.Lineups(world, {})
        club = next(iter(world.club_rosters))
        coach = world.coaches[le.CLUB_COACH_MAP[club]]
        roster = list(world.club_rosters[club])
        real = le.decide_club_lineup(coach=coach, roster=roster, config=world.config)
        with t046.lineup_swap(world, tracker, 'nolagfat'):
            self.assertEqual(le.decide_club_lineup(coach=coach, roster=roster, config=world.config), real)  # the caller is not the match-day step
            self.assertEqual(tracker.checked['swapped_decisions'], 0)

    def test_the_real_function_is_put_back_after_an_exception(self):
        world = le._build_initial_world(seed=100, config=t036.make_cfg('t031', NAME, 29, 'legacy'))
        real = le.decide_club_lineup
        with self.assertRaises(RuntimeError):
            with t046.lineup_swap(world, t046.Lineups(world, {}), 'bd'):
                raise RuntimeError
        self.assertIs(le.decide_club_lineup, real)

    def test_a_tampered_fatigue_term_is_caught(self):
        world = le._build_initial_world(seed=100, config=t036.make_cfg('t031', NAME, 29, 'legacy'))
        tracker = t046.Lineups(world, {})
        pid = next(iter(world.players))
        tracker.executed, tracker.executed_fatigue, tracker.true_fatigue = {pid: (0.5, 0.5)}, {pid: 0.1}, {pid: world.players[pid].fatigue}
        cfg = world.config
        row = {'player_id': pid, 'components': {'skill_term': round(cfg.select_skill_weight * 0.5, 6), 'match_term': round(cfg.select_match_weight * 0.5, 6), 'fatigue_penalty': 0.0}}
        with self.assertRaises(AssertionError):
            tracker.lineup(world, {'id': 'x', 'data': {'ranking': [row]}})


def synthetic_levels(top2_of, abs_f=0.0):
    """v[arm][metric] with `top2` = top2_of(variant, point)."""
    return {arm: {m: 0.0 for m in t046.METRICS} | {'top2': top2_of(variant, point), 'abs_f': abs_f} for arm, (_, point, variant) in t046.ARMS.items()}


NONE_CURVE = {'o-0.0300': 0.70, 'o-0.0100': 0.60, 'base': 0.62, 'o+0.0100': 0.72, 'o+0.0300': 0.74}
FULL_CURVE = {'o-0.0300': 0.86, 'o-0.0100': 0.84, 'base': 0.82, 'o+0.0100': 0.81, 'o+0.0300': 0.82}  # a residual slope of 0.04 (rest-rich end minus rest-poor end)


def blended(share):
    """Every point of the curve moved the share `share` of the way from `none` to the full removal."""
    return lambda point: NONE_CURVE[point] + share * (FULL_CURVE[point] - NONE_CURVE[point])


def levels_with(shares, fat_curve=None):
    """shares: variant -> share of the way from none to nolag; nolagfat takes `fat_curve` (point -> top2) when given."""
    def top2_of(variant, point):
        if variant == 'nolagfat' and fat_curve:
            return fat_curve[point]
        return blended(shares.get(variant, 1.0 if variant == 'nolag' else 0.0))(point)
    return synthetic_levels(top2_of)


def point_and_ci(v, width=0.005):
    point = t046.effects(v)
    return point, {k: [x - width, x + width] if x is not None else None for k, x in point.items()}


class TestStatisticsByHand(unittest.TestCase):
    def test_effects_by_hand(self):
        out = t046.effects(levels_with({'d50': 0.5}))
        self.assertAlmostEqual(out['contrast|fwd|none|LO|top2'], 0.14, places=12)
        self.assertAlmostEqual(out['contrast|fwd|none|HI|top2'], 0.10, places=12)
        self.assertAlmostEqual(out['U|fwd|none'], 0.12, places=12)
        self.assertAlmostEqual(out['U|rev|nolag'], (-0.02 + 0.02) / 2, places=12)  # FULL: LO = 0.82 - 0.84, HI = 0.86 - 0.84
        self.assertAlmostEqual(out['SL|fwd|none'], 0.70 - 0.74, places=12)
        self.assertAlmostEqual(out['SL|fwd|nolag'], 0.04, places=12)
        self.assertAlmostEqual(out['share|fwd|d50|U'], 0.5, places=12)
        self.assertAlmostEqual(out['dev|fwd|d50'], 0.0, places=12)
        self.assertAlmostEqual(out['share|fwd|nolag|U'], 1.0, places=12)
        self.assertAlmostEqual(out['vshare|fwd|d50'], 0.5, places=12)
        self.assertAlmostEqual(out['range4|fwd|none'], 0.14, places=12)
        self.assertAlmostEqual(out['mean4|fwd|nolag'], (0.86 + 0.84 + 0.81 + 0.82) / 4, places=12)
        self.assertAlmostEqual(out['den|fwd|U'], 0.12 - 0.0, places=12)

    def test_additivity_and_the_boundary_margin(self):
        out = t046.effects(levels_with({'st': 0.3, 'be': 0.5, 'bd': 0.9}))
        self.assertAlmostEqual(out['additivity|fwd'], 0.3 + 0.5 - 1.0, places=12)
        self.assertAlmostEqual(out['bd_minus_other|fwd'], 0.9 - 0.5, places=12)

    def test_dose_verdicts(self):
        cases = (({'d25': 0.25, 'd50': 0.5, 'd75': 0.75}, 'linear'), ({'d25': 0.5, 'd50': 0.75, 'd75': 0.9}, 'front_loaded'),
                 ({'d25': 0.1, 'd50': 0.3, 'd75': 0.6}, 'back_loaded'), ({'d25': 0.5, 'd50': 0.5, 'd75': 0.5}, 'mixed'))
        for shares, want in cases:
            got = t046.classify_dose(t046.effects(levels_with(shares)))
            self.assertEqual(got['verdict'], want, shares)
        self.assertTrue(t046.classify_dose(t046.effects(levels_with({'d25': 0.25, 'd50': 0.5, 'd75': 0.75})))['monotone']['fwd'])
        self.assertFalse(t046.classify_dose(t046.effects(levels_with({'d25': 0.5, 'd50': 0.5, 'd75': 0.5})))['monotone']['fwd'])

    def test_dose_verdict_when_the_directions_differ_or_nothing_to_remove(self):
        point = t046.effects(levels_with({'d25': 0.25, 'd50': 0.5, 'd75': 0.75}))
        point['dev|rev|d50'] = 0.3
        self.assertEqual(t046.classify_dose(point)['verdict'], 'differs')
        flat = t046.effects(synthetic_levels(lambda variant, point: 0.6))
        self.assertEqual(t046.classify_dose(flat)['verdict'], 'undefined')

    def test_carrier_verdicts(self):
        self.assertEqual(t046.classify_carrier(t046.effects(levels_with({'st': 0.5, 'be': 0.4, 'bd': 0.8})))['verdict'], 'bd')
        self.assertEqual(t046.classify_carrier(t046.effects(levels_with({'st': 0.9, 'be': 0.2, 'bd': 0.6})))['verdict'], 'st')
        self.assertEqual(t046.classify_carrier(t046.effects(levels_with({'st': 0.5, 'be': 0.5, 'bd': 0.5})))['verdict'], 'diffuse')
        point = t046.effects(levels_with({'st': 0.5, 'be': 0.4, 'bd': 0.8}))
        point['share|rev|bd|U'] = 0.3
        self.assertEqual(t046.classify_carrier(point)['by_direction'], {'fwd': 'bd', 'rev': 'st' if point['share|rev|st|U'] >= 0.7 else 'diffuse'})
        self.assertEqual(t046.classify_carrier(point)['verdict'], 'differs')

    def test_residual_verdicts(self):
        def verdict(fat_curve, full_curve=None, width=0.005):
            v = levels_with({}, fat_curve)
            if full_curve:
                for arm, (_, point, variant) in t046.ARMS.items():
                    if variant == 'nolag':
                        v[arm]['top2'] = full_curve[point]
            point, ci = point_and_ci(v, width)
            return t046.classify_residual(point, ci)['verdict']
        flat = dict.fromkeys(FULL_CURVE, 0.80)
        self.assertEqual(verdict(flat), 'explains')  # SL 0.04 -> 0
        self.assertEqual(verdict({**FULL_CURVE, 'o-0.0300': 0.85}), 'partial')  # SL 0.03, ratio 0.75
        self.assertEqual(verdict({**FULL_CURVE, 'o-0.0300': 0.92}), 'grows')  # SL 0.10
        self.assertEqual(verdict(FULL_CURVE), 'unchanged')
        self.assertEqual(verdict(flat, full_curve=flat), 'no_residual')
        self.assertEqual(verdict(flat, width=0.05), 'no_residual')  # the interval of SL after the full removal includes 0

    def test_the_summary_resamples_cells_with_the_point_equal_to_the_full_sample(self):
        cell_accs = {arm: [{('start', 'top2'): [1, float(i % 2), 0.0, 0.0, 0.0]} for i in range(6)] for arm in ('a', 'b')}
        got = t045.summarise(cell_accs, 20, lambda v: {'diff': v['a']['top2'] - v['b']['top2'], 'a': v['a']['top2']}, {'top2': ('start', 'top2')})
        self.assertAlmostEqual(got['point']['a'], 0.5)
        self.assertEqual(got['cells'], 6)


if __name__ == '__main__':
    unittest.main()
