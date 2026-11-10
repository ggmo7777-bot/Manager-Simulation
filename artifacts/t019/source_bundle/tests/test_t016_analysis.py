"""Checks of independent membership audits and outcome-selected descriptions."""
from copy import deepcopy
import unittest

from experiments.audit_t016 import cohort_audit, never_transferred_description, paired_effects


class TestT016Analysis(unittest.TestCase):
    def description_row(self, seed, gains):
        ids = [f'x{i}' for i in range(len(gains))]
        return {'seed': seed, 'summary': {
            'academy_never_transferred_count': len(gains), 'academy_never_transferred_ids': ids,
            'academy_never_transferred_skill_gain': sum(gains)/len(gains) if gains else None,
        }, 'cohort_audit': {'players': [dict(player_id=pid, initial_academy=True,
                                           never_transferred=True, gain=gain)
                                       for pid, gain in zip(ids, gains)]}}

    def test_empty_world_is_not_zero_growth_and_weighting_is_explicit(self):
        result = never_transferred_description([
            self.description_row(100, []), self.description_row(101, [0.2, 0.6]),
            self.description_row(102, [0.8])])
        self.assertEqual(result['worlds'], 3)
        self.assertEqual(result['defined_worlds'], 2)
        self.assertEqual(result['empty_world_seeds'], [100])
        self.assertEqual(result['total_player_world_observations'], 3)
        self.assertEqual(result['mean_of_nonempty_world_means'], 0.6)
        self.assertEqual(result['pooled_player_mean'], 0.533333)

    def test_all_empty_group_means_remain_null(self):
        result = never_transferred_description([self.description_row(100, [])])
        self.assertIsNone(result['mean_of_nonempty_world_means'])
        self.assertIsNone(result['pooled_player_mean'])
        self.assertEqual(result['defined_worlds'], 0)

    def paired_rows(self):
        rows = []
        for mode in ('fixed', 'sampled'):
            for institution, gain in [('closed_no_solidarity', 0.1),
                                      ('open_no_solidarity', 0.2), ('open_solidarity', 0.3)]:
                for seed in range(100, 130):
                    rows.append({'population': mode, 'institution': institution, 'seed': seed,
                                 'initial_agent_state_sha256': (mode, seed), 'summary': {
                                     'academy_initial_cohort_ids': ['a', 'b'],
                                     'academy_initial_cohort_skill_gain': gain,
                                     'mean_skill_gain': gain, 'legacy_four_slot_skill_gain': gain,
                                     'academy_never_transferred_skill_gain': None}})
        return rows

    def test_paired_unit_is_world_and_selected_group_is_not_compared(self):
        effects = paired_effects(self.paired_rows(), tuple(range(100, 130)))
        self.assertEqual(len(effects), 12)
        self.assertTrue(all(e['n_worlds'] == 30 for e in effects))
        self.assertTrue(all(e['mean'] == 0.1 for e in effects))
        self.assertFalse(any('never_transferred' in e['metric'] for e in effects))

    def test_changed_initial_population_and_duplicate_rows_rejected(self):
        rows = self.paired_rows()
        rows[0]['initial_agent_state_sha256'] = 'different'
        with self.assertRaises(AssertionError):
            paired_effects(rows, tuple(range(100, 130)))
        rows = self.paired_rows()
        rows[-1] = rows[0]
        with self.assertRaises(ValueError):
            paired_effects(rows, tuple(range(100, 130)))

    def audit_fixture(self):
        def player(club, level, count=0):
            return {'club_id': club, 'transfers_count': count,
                    'traits': dict(pace=level, passing=level, shooting=level)}
        raw = {'initial_players': {'a': player('club_a', 0.3), 'b': player('club_c', 0.3),
                                  'c': player('club_b', 0.3)},
               'players': {'a': player('club_b', 0.4, 1), 'b': player('club_c', 0.5),
                           'c': player('club_a', 0.6, 1)},
               'events': [{'kind': 'transfer_completed', 'data': {
                   'player_id': pid, 'from_club': a, 'to_club': b}}
                   for pid, a, b in [('a', 'club_a', 'club_b'), ('c', 'club_b', 'club_a')]]}
        summary = {'academy_initial_cohort_ids': ['a', 'b'], 'academy_initial_cohort_count': 2,
                   'academy_initial_cohort_skill_gain': 0.15,
                   'academy_never_transferred_ids': ['b'], 'academy_never_transferred_count': 1,
                   'academy_never_transferred_skill_gain': 0.2}
        return raw, summary

    def test_auditor_uses_transfer_events_and_initial_membership(self):
        raw, summary = self.audit_fixture()
        checked = cohort_audit(raw, summary)
        self.assertTrue(checked['members_counts_and_gains_equal'])
        bad = deepcopy(summary); bad['academy_initial_cohort_ids'] = ['b', 'c']
        with self.assertRaises(AssertionError):
            cohort_audit(raw, bad)
        bad = deepcopy(raw); bad['players']['a']['transfers_count'] = 0
        with self.assertRaises(AssertionError):
            cohort_audit(bad, summary)


if __name__ == '__main__':
    unittest.main()
