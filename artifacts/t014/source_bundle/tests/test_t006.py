import math
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from soccer_sim.advice import coach_experience, coach_forecast, remember_coach_experience
from soccer_sim.experiment_t005 import CONDITIONS as OLD_CONDITIONS
from soccer_sim.policy import decide_coach, decide_player
from soccer_sim.simulation import advance, coach_candidates, new_world
from soccer_sim.state import CoachObservation, PlayerObservation, Proposal, SelfObservation
from soccer_sim.storage import canonical, event_lines, load_snapshot, snapshot, write_snapshot

RANK = replace(OLD_CONDITIONS['coach_learning_signed'], policy=replace(
    OLD_CONDITIONS['coach_learning_signed'].policy, coach_method_mode='forecast_rank'))


class T006Tests(unittest.TestCase):
    def private_fixture(self, skill=0.3, residuals=(0.0, 0.01)):
        world = new_world(17, RANK)
        mind = replace(world.coach, assessments=(replace(world.coach.assessments[0], skill_estimate=skill),),
                       adjustments=(replace(world.coach.adjustments[0], residuals=residuals),))
        view = CoachObservation(20, 1, coach_candidates(('p1',), {'p1'}, {}))
        return world, mind, view

    def test_rank_changes_method_and_same_rank_ignores_magnitude(self):
        world, mind, view = self.private_fixture()
        self.assertEqual(decide_coach(view, mind, RANK.policy).chosen.method, 'B')
        legacy = replace(RANK.policy, coach_method_mode='legacy')
        self.assertEqual(decide_coach(view, mind, legacy).chosen.method, 'A')
        first = decide_coach(view, mind, RANK.policy)
        changed = replace(mind, adjustments=(replace(mind.adjustments[0], residuals=(-0.1, 0.2)),))
        second = decide_coach(view, changed, RANK.policy)
        self.assertEqual([c.terms for c in first.candidates], [c.terms for c in second.candidates])
        opposite = replace(mind, adjustments=(replace(mind.adjustments[0], residuals=(0.02, 0.0)),))
        self.assertEqual(decide_coach(view, opposite, RANK.policy).chosen.method, 'A')

    def test_maximum_and_kind_target_preserved_for_available_candidate_sets(self):
        for skill in (0.0, 0.3, 0.5, 0.8, 1.0):
            world, one, _ = self.private_fixture(skill)
            mind = replace(world.coach, assessments=tuple(replace(a, skill_estimate=skill)
                                                          for a in world.coach.assessments),
                           adjustments=tuple(replace(a, residuals=(0.0, 0.01))
                                             for a in world.coach.adjustments))
            for day in (1, 3, 6, 12):
                for observed in (set(), {'p1'}, {'p1', 'p2'}):
                    for proposed in (set(), {'p1'}, {'p2'}, {'p1', 'p2'}):
                        view = CoachObservation(20, day, coach_candidates(world.players, observed, proposed))
                        old = decide_coach(view, mind, replace(RANK.policy, coach_method_mode='legacy'))
                        new = decide_coach(view, mind, RANK.policy)
                        self.assertEqual((new.chosen.action, new.chosen.target),
                                         (old.chosen.action, old.chosen.target))
                        for target in world.players.keys() - proposed:
                            self.assertEqual(max(c.score for c in old.candidates if c.action == 'propose' and c.target == target),
                                             max(c.score for c in new.candidates if c.action == 'propose' and c.target == target))

    def test_ties_negative_half_skill_zero_weight_and_tiny_difference(self):
        for skill in (0.3, 0.5, 0.8):
            _, mind, view = self.private_fixture(skill, (0.0, 0.0))
            old = decide_coach(view, mind, replace(RANK.policy, coach_method_mode='legacy'))
            new = decide_coach(view, mind, RANK.policy)
            self.assertEqual(old.chosen.method, new.chosen.method)
            self.assertEqual(new.chosen.method_selection['basis'], 'tie_legacy')
        _, mind, view = self.private_fixture(0.5, (-0.3, -0.2))
        chosen = decide_coach(view, mind, RANK.policy).chosen
        self.assertEqual(chosen.method, 'B')
        self.assertTrue(all(q['expected_gain'] < 0 for q in chosen.method_selection['forecasts'].values()))
        _, mind, view = self.private_fixture(0.5, (0.0, 1e-15))
        self.assertEqual(decide_coach(view, mind, RANK.policy).chosen.method, 'B')
        zero = decide_coach(view, mind, replace(RANK.policy, coach_method_fit=0.0))
        self.assertTrue(all(math.isfinite(c.score) for c in zero.candidates))

    def test_fixed_private_experience_learns_then_changes_method(self):
        _, mind, view = self.private_fixture(0.3, (0.0, 0.0))
        adjustment = mind.adjustments[0]
        forecast = coach_forecast('A', 0.3, mind.method_rates, adjustment)
        for learn, method in ((False, 'A'), (True, 'B')):
            experience = coach_experience(0.5, 0.4, forecast, learn)
            changed = remember_coach_experience(adjustment, 'A', experience['residual_after'], 18) if learn else adjustment
            learned = replace(mind, adjustments=(changed,))
            self.assertEqual(decide_coach(view, learned, RANK.policy).chosen.method, method)

    def test_true_traits_and_private_player_g_are_not_coach_inputs(self):
        world, mind, view = self.private_fixture()
        before = decide_coach(view, mind, RANK.policy).payload()
        world.players['p1'].rates = (0.99, 0.01)
        world.players['p1'].cap = 0.99
        world.players['p1'].mind = replace(world.players['p1'].mind, expected_gains=(-0.9, 0.9))
        self.assertEqual(before, decide_coach(view, mind, RANK.policy).payload())

    def test_player_still_independently_accepts_or_rejects_same_proposal(self):
        world, mind, view = self.private_fixture()
        method = decide_coach(view, mind, RANK.policy).chosen.method
        observation = PlayerObservation(SelfObservation(20, 0.4, 0.0), Proposal(21, method, -0.3))
        for trust, response in ((0.0, 'rejected'), (1.0, 'accepted')):
            player = replace(world.players['p1'].mind, expected_gains=(0.05, 0.05),
                             preferences=(0.0, 0.0), autonomy=0.5, trust=trust)
            rank = decide_player(observation, player, RANK.policy)
            old = decide_player(observation, player, replace(RANK.policy, coach_method_mode='legacy'))
            self.assertEqual(rank.payload(), old.payload())
            self.assertEqual(rank.response, response)

    def test_rank_candidates_log_both_forecasts_and_earlier_causes(self):
        world = advance(new_world(17, RANK), 84)
        for event in world.events:
            if event.kind == 'decision' and event.actor == 'coach':
                for candidate in event.data['candidates']:
                    if candidate['action'] == 'propose':
                        evidence = candidate['method_selection']
                        self.assertEqual(set(evidence['forecasts']), {'A', 'B'})
                        for memory in (evidence['skill_memory_id'], evidence['rate_memory_id'],
                                       *(q['residual_memory_id'] for q in evidence['forecasts'].values())):
                            self.assertIn(memory, event.causes)
                            self.assertLess(world.events[memory - 1].day, event.day)

    def test_four_conditions_resume_and_reject_tampered_selection(self):
        for learn in (False, True):
            for mode in ('legacy', 'forecast_rank'):
                config = replace(RANK, beliefs=replace(RANK.beliefs, coach_forecast_learning=learn),
                                 policy=replace(RANK.policy, coach_method_mode=mode))
                full = advance(new_world(17, config), 84)
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / 'save.json'
                    half = advance(new_world(17, config), 42)
                    write_snapshot(path, half)
                    resumed = advance(load_snapshot(path), 42)
                    self.assertEqual(canonical(snapshot(full)), canonical(snapshot(resumed)))
                    self.assertEqual(list(event_lines(full)), list(event_lines(resumed)))
                    if mode == 'forecast_rank':
                        data = snapshot(full)
                        row = next(e['data'] for e in data['events'] if e['kind'] == 'decision' and e['actor'] == 'coach')
                        candidate = next(c for c in row['candidates'] if c['action'] == 'propose')
                        candidate['method_selection']['forecasts']['B']['expected_gain'] += 0.01
                        path.write_bytes(canonical(data))
                        with self.assertRaises(ValueError):
                            load_snapshot(path)

    def test_mode_validation_and_missing_or_old_save_rejected(self):
        for mode in ('unknown', True, 1, None):
            with self.assertRaises(ValueError):
                new_world(17, replace(RANK, policy=replace(RANK.policy, coach_method_mode=mode)))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'save.json'
            data = snapshot(new_world(17, RANK))
            del data['config']['policy']['coach_method_mode']
            path.write_bytes(canonical(data))
            with self.assertRaises(ValueError):
                load_snapshot(path)
            data = snapshot(new_world(17, RANK))
            data['versions']['save'] = 5
            path.write_bytes(canonical(data))
            with self.assertRaises(ValueError):
                load_snapshot(path)

    def test_experiment_records_missing_experience_and_direct_components(self):
        from soccer_sim.experiment_t006 import CONDITIONS, action_disagreement, measure_run
        frozen = advance(new_world(17, CONDITIONS['frozen_rank']), 84)
        row = measure_run(frozen, 0.0)
        self.assertEqual(row['method_selection']['ranked_proposals'], 0)
        self.assertEqual(row['method_selection']['tie_fallback_proposals'], row['proposal_count'])
        self.assertEqual(row['method_selection']['same_state_kind_target_changes'], 0)
        zero = measure_run(new_world(17, CONDITIONS['learning_rank']), 0.0)
        self.assertIsNone(zero['coach_learning']['p1']['by_method']['B']['observed_forecast_mse'])
        left = {'p1': ['A'], 'p2': ['B'], 'coach': [('propose', 'p1', 'A')]}
        right = {'p1': ['A'], 'p2': ['A'], 'coach': [('propose', 'p1', 'B')]}
        mismatch = action_disagreement(left, right)
        self.assertEqual(mismatch['coach_kind'], 0)
        self.assertEqual(mismatch['coach_target'], 0)
        self.assertEqual(mismatch['coach_method'], 1)

    def test_reader_rejects_changed_rank_score_memory_and_action(self):
        from copy import deepcopy
        original = snapshot(advance(new_world(17, RANK), 14))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'save.json'
            for operation in ('score', 'basis', 'skill_source', 'unselected_forecast', 'action'):
                data = deepcopy(original)
                event = next(e for e in data['events'] if e['kind'] == 'decision' and e['actor'] == 'coach')
                candidate = next(c for c in event['data']['candidates'] if c['action'] == 'propose')
                evidence = candidate['method_selection']
                if operation == 'score':
                    candidate['score'] += 0.01
                elif operation == 'basis':
                    evidence['basis'] = 'forecast_rank'
                elif operation == 'skill_source':
                    evidence['skill_memory_id'] = 1
                elif operation == 'unselected_forecast':
                    evidence['forecasts']['B']['residual_before'] += 0.001
                else:
                    action = next(e for e in data['events'] if e['kind'] == 'coach_action')
                    action['data']['method'] = 'B'
                path.write_bytes(canonical(data))
                with self.subTest(operation=operation), self.assertRaises(ValueError):
                    load_snapshot(path)
