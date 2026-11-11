"""T047: the arms and lanes by hand, the lanes against short worlds (the identity lane is read-only, a lane equals the real filters until its first extra observation, the budget, the lane read is exactly the lane's estimate,
the real function is put back, `none` reproduces T046's accumulators), the conversion (dose response -> lineup match) and the pre-registered rules by hand.
`soccer_sim/` is unchanged."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from experiments import t023, t036, t043, t046, t047
from soccer_sim import league_economy as le

NAME = t023.conditions()[3]
SHORT = 42  # two seasons: match days and one season end


def short_world(arm, days=SHORT, start=29, setting='t031'):
    return t047.run_arm(setting, NAME, 100, start, arm, days=days)


def plain_world(direction, days=SHORT, start=29, setting='t031'):
    return le.run_league_world(seed=100, days=days, config=t036.make_cfg(setting, NAME, start, t047.DIRECTIONS[direction][0]))


def level(acc, key):
    return acc[key][1] / acc[key][0]


# a reference whose four-point mean rises linearly with the share removed: none 70, d25 72, d50 74, d75 76, nolag 78 (every point the same), bd 77
def linear_reference(none=70.0, step=2.0, boundary=77.0):
    levels = {'none': none, 'd25': none + step, 'd50': none + 2 * step, 'd75': none + 3 * step, 'nolag': none + 4 * step, 'bd': boundary}
    return {v: {p: levels[v] for p in t047.POINTS_SWAPPED} for v in t047.REFERENCE_VARIANTS}


class TestArmsAndLanes(unittest.TestCase):
    def test_forty_two_arms(self):
        self.assertEqual(len(t047.ARMS), 42)
        self.assertEqual(sum(1 for _, _, policy in t047.ARMS.values() if policy == 'none'), 10)
        self.assertEqual(sum(1 for _, p, _ in t047.ARMS.values() if p == 'base'), 2)  # `base` only for `none`
        self.assertEqual(t047.split_arm('rev_o+0.0300__b4'), ('rev_o+0.0300', 'b4'))
        for arm in t047.ARMS:
            self.assertIn(t047.split_arm(arm)[0], t043.ARMS)
        none_arms = {arm for arm, (_, _, policy) in t047.ARMS.items() if policy == 'none'}
        self.assertTrue(none_arms <= set(t046.ARMS))  # T046 names its unread arms the same way

    def test_lanes(self):
        self.assertEqual(t047.lanes_of('none'), ('x2', 'x4', 'x8', 'b2', 'b4', 'b8'))
        self.assertEqual(t047.lanes_of('b2'), ('b2',))
        self.assertEqual(t047.lanes_of('x1'), ('x1',))
        with self.assertRaises(AssertionError):
            t047.lanes_of('x3')
        self.assertEqual(t047.LANES['x4'], (4, 'all'))
        self.assertEqual(t047.LANES['b8'], (8, 'boundary'))

    def test_the_lag_metrics_exist_for_every_lane(self):
        for lane in ('none', *t047.LANES):
            self.assertIn(f'lag|{lane}|bd|abs', t047.METRICS)
        self.assertEqual(t047.METRICS['obs|ghost|x2'], ('obs', 'ghost|x2'))


class TestConversion(unittest.TestCase):
    def test_interpolate(self):
        self.assertAlmostEqual(t047.interpolate(t047.DOSE_X, [0, 1, 2, 3, 4], 0.375), 1.5)
        self.assertEqual(t047.interpolate(t047.DOSE_X, [0, 1, 2, 3, 4], 1.7), 4)  # cut to the range
        self.assertEqual(t047.interpolate(t047.DOSE_X, [0, 1, 2, 3, 4], -0.2), 0)

    def test_dose_level_and_share(self):
        ref = linear_reference()
        point = t047.POINTS_SWAPPED[0]
        self.assertAlmostEqual(t047.dose_level(ref, point, 0.0), 70.0)
        self.assertAlmostEqual(t047.dose_level(ref, point, 0.5), 74.0)
        self.assertAlmostEqual(t047.dose_level(ref, point, 0.625), 75.0)
        self.assertAlmostEqual(t047.dose_level(ref, point, 1.0), 78.0)
        self.assertAlmostEqual(t047.dose_share(ref, 0.25), 0.25)
        self.assertAlmostEqual(t047.dose_share(ref, 1.0), 1.0)

    def test_implied_lambda_inverts_the_curve(self):
        ref = linear_reference()
        for lam in (0.0, 0.1, 0.5, 0.9, 1.0):
            gain = t047.dose_level(ref, t047.POINTS_SWAPPED[0], lam) - 70.0
            self.assertAlmostEqual(t047.implied_lambda(ref, gain), lam, places=9)
        self.assertIsNone(t047.implied_lambda(ref, -1.0))
        self.assertIsNone(t047.implied_lambda(ref, 9.0))

    def test_predicted_levels_by_method(self):
        ref = linear_reference()
        lam_all = {p: 0.5 for p in t047.POINTS_SWAPPED}
        lam_bd = {p: 0.25 for p in t047.POINTS_SWAPPED}
        for level_a in t047.predicted_levels(ref, 'A', lam_all, lam_bd).values():
            self.assertAlmostEqual(level_a, 74.0)
        for level_b in t047.predicted_levels(ref, 'B', lam_all, lam_bd).values():
            self.assertAlmostEqual(level_b, 70.0 + (77.0 - 70.0) * 0.25)  # the dose share of 0.25 is 0.25
        self.assertEqual(t047.predicted_levels(ref, 'A', {p: None for p in t047.POINTS_SWAPPED}, lam_bd)[t047.LOW_END], None)

    def test_depth_and_gain(self):
        levels = {'o-0.0300': 75.0, 'o-0.0100': 65.0, 'o+0.0100': 70.0, 'o+0.0300': 73.0}
        self.assertEqual(t047.depth(levels), ((73.0 - 65.0) + (75.0 - 65.0)) / 2)
        self.assertEqual(t047.gain_of(levels, {p: 60.0 for p in levels}), 70.75 - 60.0)
        self.assertIsNone(t047.depth({**levels, 'o+0.0100': None}))

    def test_lambda_of(self):
        v = {t047.arm_of('fwd', 'o-0.0100', 'none'): {'lag|x2|all|abs': 0.03, 'lag|none|all|abs': 0.05, 'lag|x2|all|sq': 0.0009, 'lag|none|all|sq': 0.0025}}
        self.assertAlmostEqual(t047.lambda_of(v, 'fwd', 'o-0.0100', 'x2', 'all', 'abs'), 0.4)
        self.assertAlmostEqual(t047.lambda_of(v, 'fwd', 'o-0.0100', 'x2', 'all', 'sq'), 1 - 0.6)
        v[t047.arm_of('fwd', 'o-0.0100', 'none')]['lag|none|all|abs'] = 0.0
        self.assertIsNone(t047.lambda_of(v, 'fwd', 'o-0.0100', 'x2', 'all', 'abs'))


class TestRules(unittest.TestCase):
    def test_shrink_labels(self):
        for value, label in ((0.05, 'small'), (0.2, 'moderate'), (0.4, 'large'), (0.6, 'half')):
            self.assertEqual(t047.classify_shrink({'lam|fwd|x2|all|abs': value, 'lam|rev|x2|all|abs': value})['verdict'], label)
        self.assertEqual(t047.classify_shrink({'lam|fwd|x2|all|abs': 0.05, 'lam|rev|x2|all|abs': 0.4})['verdict'], 'straddles')
        self.assertEqual(t047.classify_shrink({'lam|fwd|x2|all|abs': None, 'lam|rev|x2|all|abs': None})['verdict'], 'undefined')

    def test_origin_labels(self):
        for value, label in ((0.3, 'spread'), (0.45, 'bench_plural'), (0.6, 'bench')):
            self.assertEqual(t047.classify_origin({'share|fwd|r3': value, 'share|rev|r3': value})['verdict'], label)
        self.assertEqual(t047.classify_origin({'share|fwd|r3': 0.3, 'share|rev|r3': 0.6})['verdict'], 'straddles')

    def test_focus_labels(self):
        def point(fwd, rev):
            return {'lam|fwd|b2|bd|abs': 0.5 + fwd, 'lam|fwd|x2|bd|abs': 0.5, 'lam|rev|b2|bd|abs': 0.5 + rev, 'lam|rev|x2|bd|abs': 0.5}
        self.assertEqual(t047.classify_focus(point(0.2, 0.15))['verdict'], 'focus_helps')
        self.assertEqual(t047.classify_focus(point(0.05, -0.1))['verdict'], 'same')
        self.assertEqual(t047.classify_focus(point(-0.2, -0.15))['verdict'], 'focus_hurts')
        self.assertEqual(t047.classify_focus(point(0.2, -0.15))['verdict'], 'differs')

    def test_size_is_the_smallest_multiple_reaching_one_half_in_both_directions(self):
        point = {}
        for d, lams in (('fwd', (0.3, 0.6, 0.9)), ('rev', (0.45, 0.55, 0.8))):
            for k, lam in zip(t047.SIZES, lams):
                point[f'lam|{d}|x{k}|all|abs'] = lam
                point[f'lam|{d}|b{k}|bd|abs'] = lam + 0.3
        size = t047.classify_size(point)
        self.assertEqual(size['x']['verdict'], 4)
        self.assertEqual(size['b']['by_direction'], {'fwd': 2, 'rev': 2})
        self.assertEqual(size['b']['verdict'], 2)
        weak = {f'lam|{d}|x{k}|all|abs': 0.1 for d in t047.DIRECTIONS for k in t047.SIZES} | {f'lam|{d}|b{k}|bd|abs': 0.1 for d in t047.DIRECTIONS for k in t047.SIZES}
        self.assertEqual(t047.classify_size(weak)['x']['verdict'], '>8')

    def test_transfer_labels_and_the_registered_method(self):
        def point(fwd, rev, lane='x2', method='A'):
            return {f'err4|fwd|{lane}|{method}': fwd, f'err4|rev|{lane}|{method}': rev}
        self.assertEqual(t047.classify_transfer(point(1.0, -1.5), 'x2')['verdict'], 'holds')  # the edge counts as holding
        self.assertEqual(t047.classify_transfer(point(-2.0, 0.5), 'x2')['verdict'], 'over')
        self.assertEqual(t047.classify_transfer(point(2.0, 0.5), 'x2')['verdict'], 'under')
        self.assertEqual(t047.classify_transfer(point(2.0, -2.0), 'x2')['verdict'], 'mixed')
        self.assertEqual(t047.classify_transfer(point(0.0, 0.0, 'b2', 'B'), 'b2')['method'], 'B')
        self.assertEqual(t047.classify_transfer(point(0.0, 0.0), 'x2')['method'], 'A')
        self.assertEqual(t047.classify_transfer(point(None, 0.0), 'x2')['verdict'], 'undefined')


class TestLanesInShortWorlds(unittest.TestCase):
    def test_the_identity_lane_and_none_are_read_only(self):
        for direction in t047.DIRECTIONS:
            plain = t047.plain_digest(plain_world(direction))
            for policy in ('none', 'x1'):
                real = le.decide_club_lineup
                world = short_world(f'{direction}_base{t047.ARM_SEP}{policy}')['world']
                self.assertEqual(t047.plain_digest(world), plain, (direction, policy))
                self.assertIs(le.decide_club_lineup, real)

    def test_none_reproduces_t046s_accumulators(self):
        mine = short_world('fwd_o+0.0100__none')
        theirs = t046.run_arm('t031', NAME, 100, 29, 'fwd_o+0.0100__none', days=SHORT)
        for key, value in theirs['acc'].items():
            self.assertEqual(mine['acc'][key], value, key)

    def test_a_read_lane_changes_the_world_and_the_real_function_comes_back(self):
        reference = t047.plain_digest(plain_world('fwd'))
        real = le.decide_club_lineup
        changed = 0
        for policy in t047.READ_LANES:
            result = short_world(f'fwd_base{t047.ARM_SEP}{policy}', days=84)
            changed += t047.plain_digest(result['world']) != t047.plain_digest(plain_world('fwd', days=84))
            self.assertGreater(result['checked']['lane_reads'], 0)
            self.assertIs(le.decide_club_lineup, real)
        self.assertGreater(changed, 0)
        self.assertTrue(reference)

    def test_lane_cells_equal_the_real_filters_until_the_first_extra_observation(self):
        result = short_world('fwd_base__none')
        self.assertGreater(result['checked']['lane_cells_equal'], 0)  # the per-day check ran (and passed) over the six lanes
        self.assertGreater(result['checked']['ghost_observations'], 0)
        self.assertEqual(result['checked']['lane_reads'], 0)
        self.assertGreater(result['checked']['untouched_players'], 0)

    def test_no_extra_observations_without_a_multiple(self):
        result = short_world('fwd_base__x1')
        self.assertEqual(result['checked']['ghost_observations'], 0)
        self.assertEqual(level(result['acc'], ('lag', 'x1|all|abs')), level(result['acc'], ('lag', 'none|all|abs')))  # the identity lane has the real lag

    def test_the_budget_is_about_the_registered_multiple(self):
        acc = short_world('fwd_base__none', days=84)['acc']
        real = level(acc, ('obs', 'real'))
        self.assertGreater(real, 0)
        for lane, (multiple, _) in t047.LANES.items():
            if lane in t047.SHADOW_LANES:
                self.assertAlmostEqual(level(acc, ('obs', f'ghost|{lane}')) / real, multiple - 1, delta=0.25 * (multiple - 1), msg=lane)  # the running mean rate starts late: a deficit in a short world

    def test_more_observations_shrink_the_lag(self):
        acc = short_world('fwd_base__none', days=84)['acc']
        lag = lambda lane: level(acc, ('lag', f'{lane}|all|abs'))
        self.assertLess(lag('x8'), lag('x4'))
        self.assertLess(lag('x4'), lag('x2'))
        self.assertLess(lag('x2'), lag('none'))
        self.assertLess(level(acc, ('lag', 'b8|bd|abs')), level(acc, ('lag', 'none|bd|abs')))

    def test_a_ghost_observation_reads_the_truth_in_the_lag_only_lane_and_a_noisy_value_in_the_real_lane(self):
        config = t036.make_cfg('t031', NAME, 29, 'legacy')
        world = le._build_initial_world(seed=100, config=config)
        tracker = t047.Lanes(world, {}, ('x2',))
        coach_id = next(iter(world.coaches))
        pid = world.club_rosters[world.coaches[coach_id].club_id][0]
        cell = tracker.cells[coach_id, pid]
        tracker.ensure(cell)
        before_r, before_z = list(cell['est']['R:x2']), list(cell['est']['Z:x2'])
        world.day = 5
        truth = world.players[pid].composite_skill()
        tracker.ghost(world, coach_id, pid, 'x2')
        alpha = config.coach_skill_alpha
        self.assertAlmostEqual(cell['est']['Z:x2'][0], (1 - alpha) * before_z[0] + alpha * truth, places=6)
        self.assertNotEqual(cell['est']['R:x2'][0], before_r[0])
        self.assertEqual(cell['est']['R'], before_r)  # the real filter is untouched
        self.assertEqual((cell['lane_seen']['x2'], cell['ghosts']['x2']), (5, 1))

    def test_effects_on_a_short_run(self):
        v = {arm: {} for arm in t047.ARMS}
        for arm in t047.ARMS:
            acc = short_world(arm, days=21)['acc']
            v[arm] = {m: (level(acc, key) if key in acc else None) for m, key in t047.METRICS.items()}
        reference = {d: linear_reference() for d in t047.DIRECTIONS}
        out = t047.effects(v, reference)
        for d in t047.DIRECTIONS:
            for lane in t047.READ_LANES:
                for method in ('A', 'B'):
                    self.assertAlmostEqual(out[f'err4|{d}|{lane}|{method}'], out[f'gain4|{d}|{lane}'] - out[f'pred_gain4|{d}|{lane}|{method}'], places=12)
                self.assertGreaterEqual(out[f'pred_gain4|{d}|{lane}|A'], 0.0)  # the dose curve rises
            for lane in t047.SHADOW_LANES:
                self.assertIn(f'lam|{d}|{lane}|all|abs', out)
        self.assertEqual(set(t047.classify_all(out)), {'shrink', 'origin', 'focus', 'size', 'transfer'})


if __name__ == '__main__':
    unittest.main()
