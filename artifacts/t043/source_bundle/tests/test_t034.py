"""T034: replay of a coach's estimate from observations, the three-term error decomposition and the F+/U+ counterfactuals (analysis only; the simulation is unchanged)."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


def changed_sim_files(task):
    """soccer_sim files that differ from a frozen bundle. T036 changed league_economy.py / league_observation.py on purpose (the default path is byte-identical in output: tests/test_t036.py)."""
    bundle = ROOT / f'artifacts/{task}/source_bundle/soccer_sim'
    return {p.name for p in (ROOT / 'soccer_sim').glob('*.py') if p.read_bytes() != (bundle / p.name).read_bytes()}
sys.path.insert(0, str(ROOT))
from experiments import t034, t023
from soccer_sim import league_economy as le

C = le.LeagueEconomyConfig()  # coach_skill_alpha 0.36, scout 0.32, match blend 0.18, match alpha 0.35, coach_noise 0.024
W = (0.55, 0.48, 0.22)
TRUTH = {d: 0.50 + 0.005 * d for d in range(0, 22)}
TRUTH.update({0: 0.50, 1: 0.52, 3: 0.55, 5: 0.58, 7: 0.60})
OPS = [{'kind': 'session', 'day': 1, 'val': 0.60, 'order': 0}, {'kind': 'lineup', 'day': 3, 'ref': 0, 'order': 1},
       {'kind': 'match', 'day': 3, 'val': 0.70, 'order': 2}, {'kind': 'session', 'day': 5, 'val': 0.66, 'order': 3}, {'kind': 'lineup', 'day': 7, 'ref': 1, 'order': 4}]


def naive(ops, truth, a_session=0.36, a_match=0.18, a_match_rating=0.35, scale=1.0):
    """Deliberately naive second implementation: forward-propagated contributions of every term, no shared helper."""
    est = {'s': 0.5, 'm': 0.5}
    out = []
    contrib = {'s': [], 'm': []}  # [weight, value, truth at observation]
    prior = {'s': 1.0, 'm': 1.0}
    for op in ops:
        if op['kind'] == 'lineup':
            out.append((dict(est), {k: (prior[k], [list(c) for c in contrib[k]]) for k in contrib}))
            continue
        a = {'s': min(1.0, (a_session if op['kind'] == 'session' else a_match) * scale), 'm': 0.0 if op['kind'] == 'session' else min(1.0, a_match_rating * scale)}
        for k in ('s', 'm'):
            if a[k] == 0:
                continue
            est[k] = round(min(0.98, max(0.10, (1 - a[k]) * est[k] + a[k] * op['val'])), 6)
            prior[k] *= 1 - a[k]
            for c in contrib[k]:
                c[0] *= 1 - a[k]
            contrib[k].append([a[k], op['val'], truth[op['day']]])
    return out


class TestHandMadeCase(unittest.TestCase):
    def test_replay_by_hand(self):
        # skill: .5 -> session .64*.5+.36*.60=.536 -> match .82*.536+.18*.70=.56552 -> session .64*.56552+.36*.66=.599533 ; match rating: .5 -> .65*.5+.35*.70=.57
        a, b = t034.replay(OPS, C, practice=False)
        self.assertEqual((a['s'], a['m']), (0.536, 0.5))
        self.assertEqual((b['s'], b['m']), (0.599533, 0.57))
        self.assertEqual([o[3] for o in b['seen']], [1, 3, 5])

    def test_practice_mode_also_moves_the_match_rating_estimate(self):
        a, _ = t034.replay(OPS, C, practice=True)
        self.assertEqual(a['m'], round(0.7 * 0.5 + 0.3 * 0.60, 6))  # 0.53

    def test_decomposition_by_hand_and_against_the_naive_version(self):
        _, b = t034.replay(OPS, C, practice=False)
        T_L = TRUTH[7]
        d = t034.decompose(b['seen'], 0, T_L, TRUTH, b['s'])
        # weights .188928 / .1152 / .36 ; P = .335872
        self.assertAlmostEqual(d['prior'], 0.335872 * (0.5 - 0.6), places=9)
        self.assertAlmostEqual(d['growth'], 0.188928 * (0.52 - 0.6) + 0.1152 * (0.55 - 0.6) + 0.36 * (0.58 - 0.6), places=9)
        self.assertAlmostEqual(d['bias'], 0.188928 * 0.08 + 0.1152 * 0.15 + 0.36 * 0.08, places=9)
        self.assertAlmostEqual(d['prior'] + d['growth'] + d['bias'] + d['r'], b['s'] - T_L, places=12)
        self.assertLess(abs(d['r']), 1e-6)
        est, parts = naive(OPS, TRUTH)[1]
        self.assertEqual(est['s'], b['s'])
        p, contrib = parts['s']
        self.assertAlmostEqual(d['prior'], p * (0.5 - T_L), places=9)
        self.assertAlmostEqual(d['growth'], sum(w * (t - T_L) for w, _, t in contrib), places=9)
        self.assertAlmostEqual(d['bias'], sum(w * (v - t) for w, v, t in contrib), places=9)
        # the match-rating estimate: only the match observation touched it
        dm = t034.decompose(b['seen'], 1, T_L, TRUTH, b['m'])
        self.assertAlmostEqual(dm['prior'], 0.65 * (0.5 - T_L), places=9)
        self.assertAlmostEqual(dm['bias'], 0.35 * (0.70 - 0.55), places=9)

    def test_f_plus_adds_one_session_per_week_on_the_first_free_day(self):
        obs = [o for o in OPS if o['kind'] != 'lineup']
        added = t034.added_sessions(obs, 0, TRUTH, 100, 'c1', 'p13', C, 'F+')
        self.assertEqual([o['day'] for o in added], [2, 8, 15])
        for o in added:
            # the sim's own rule (uniform_from_key is in [-1, 1], then (u - 0.5) * 2 * noise) gives noise in [-3, +1] x coach_noise
            self.assertTrue(-3 * C.coach_noise - 1e-6 <= o['val'] - TRUTH[o['day']] <= C.coach_noise + 1e-6)
        self.assertEqual([o['day'] for o in t034.added_sessions(obs, 0, TRUTH, 100, 'c1', 'p13', C, 'Fmax')], [d for d in range(1, 22) if d not in (1, 5)])
        merged = t034.order(OPS + added)
        self.assertEqual([(o['day'], o['kind']) for o in merged][:3], [(1, 'session'), (2, 'session'), (3, 'lineup')])

    def test_u_plus_uses_the_same_nominal_mass_and_caps_at_one(self):
        # recorded skill-side rates .36 + .18 + .36 = .90 ; three extra sessions add 3 x .36 = 1.08 -> scale 2.2
        self.assertAlmostEqual(1.0 + 3 * 0.36 / 0.90, 2.2)
        self.assertAlmostEqual(t034.alphas('session', C, False, 2.2)[0], 0.792)
        self.assertEqual(t034.alphas('session', C, False, 1e9), (1.0, 0.0))
        self.assertEqual(t034.alphas('match', C, False, 2.2), (min(1.0, 0.18 * 2.2), min(1.0, 0.35 * 2.2)))
        # replay with U-max: the estimate is the latest observation
        _, b = t034.replay(OPS, C, practice=False, scale=1e9)
        self.assertEqual((b['s'], b['m']), (0.66, 0.70))
        # and against the naive version at scale 2.2
        self.assertEqual(t034.replay(OPS, C, practice=False, scale=2.2)[1]['s'], naive(OPS, TRUTH, scale=2.2)[1][0]['s'])

    def test_newcomer_only_selection_flip(self):
        def ranking(est):
            rows = []
            for p, (s, m, f) in est.items():
                comp = {'skill_term': round(W[0] * s, 6), 'match_term': round(W[1] * m, 6), 'fatigue_penalty': round(-W[2] * f, 6)}
                rows.append({'player_id': p, 'score': round(sum(comp.values()), 6), 'components': comp})
            return rows
        rk = ranking({'p1': (0.60, 0.60, 0.1), 'p2': (0.58, 0.58, 0.1), 'p13': (0.50, 0.50, 0.1)})
        self.assertFalse(t034.start_after(rk, 'p13', 0.50, 0.50, W))
        self.assertTrue(t034.start_after(rk, 'p13', 0.65, 0.65, W))  # beats p2 (.58*(.55+.48)-.022 = .57514 vs .65*1.03-.022 = .6475)
        self.assertFalse(t034.start_after(rk, 'p13', 0.60, 0.50, W))  # .33+.24-.022 = .548 < .57514


class TestRealWorld(unittest.TestCase):
    def test_short_cell_replays_every_recorded_estimate(self):
        cell = t034.run_cell(t034.SETTINGS['t032'](t023.conditions()[1], 33), 100, days=3 * t034.L)
        self.assertGreater(cell['checks']['lineup_estimates'], 0)
        self.assertGreater(cell['checks']['recorded_session_scout_estimates'], 0)
        self.assertGreater(len(cell['rows']), 0)
        for r in cell['rows']:
            for k in ('s', 'm'):
                self.assertLess(abs(r[f'r_{k}']), t034.TOL_R)
                self.assertAlmostEqual(r[f'prior_{k}'] + r[f'growth_{k}'] + r[f'bias_{k}'] + r[f'r_{k}'], r[f'e_{k}'], places=9)
        for n in cell['newcomers']:
            self.assertEqual(n['n_total'], sum(n['n'].values()))
            self.assertEqual(n['n_total'], sum(t['total'] for t in n['n_by_third']))

    def test_simulation_unchanged(self):
        self.assertLessEqual(changed_sim_files('t032') | changed_sim_files('t033'), {'league_economy.py', 'league_observation.py'})  # only T036's two files differ


if __name__ == '__main__':
    unittest.main()
