"""Mechanism checks independent of replay and strict T007 persistence checks."""
from copy import deepcopy
from dataclasses import asdict, replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from soccer_sim import multi_coach as multi
from soccer_sim import multi_storage as storage
from soccer_sim.advice import coach_forecast, evaluate_feedback
from soccer_sim.experiment_t007 import CONDITIONS
from soccer_sim.observation import signal
from soccer_sim.policy import decide_coach, decide_player as old_decide_player
from soccer_sim.state import CoachObservation, PlayerMind, PlayerObservation, Proposal, SelfObservation
from soccer_sim.storage import canonical


class T007Tests(unittest.TestCase):
    def world(self, name='multi_learning_adaptive', seed=17):
        return multi.new_world(seed, CONDITIONS[name])

    def observation(self, proposals=(), skill=.4, fatigue=.1):
        return multi.MultiPlayerObservation(SelfObservation(100, skill, fatigue), tuple(proposals))

    def test_no_shadow_trust_and_fixed_initial_edges(self):
        world = self.world()
        for pid, player in world.players.items():
            self.assertNotIn('trust', asdict(player.mind))
            self.assertNotIn('trust_memory_id', asdict(player.mind))
            self.assertEqual([edge.value for edge in player.mind.trust_edges], [.75, .75] if pid == 'p1' else [.28, .28])
        self.assertEqual(world.coaches['c1'].method_rates, (.12, .04))
        self.assertEqual(world.coaches['c2'].method_rates, (.04, .12))

    def test_single_proposal_matches_legacy_scores(self):
        world = self.world()
        for pid, player in world.players.items():
            mind = player.mind
            for method in ('A', 'B'):
                proposal = multi.ReceivedProposal('c1', 102, method, .031)
                view = self.observation((proposal,))
                decision = multi.decide_player(view, mind, world.config.policy)
                edge = mind.trust_edges[0]
                old_mind = PlayerMind(mind.skill_estimate, mind.expected_gains, mind.preferences,
                                      mind.autonomy, edge.value, mind.gain_memory_ids,
                                      mind.skill_memory_id, edge.memory_id)
                old = old_decide_player(PlayerObservation(view.self_view, Proposal(102, method, .031)), old_mind, world.config.policy)
                self.assertEqual([c.payload() for c in decision.candidates], [c.payload() for c in old.candidates])
                self.assertEqual(decision.chosen, old.chosen)

    def test_advice_not_summed_and_order_tie_and_negative_preserved(self):
        world = self.world()
        proposals = (multi.ReceivedProposal('c2', 102, 'A', .12), multi.ReceivedProposal('c1', 101, 'A', .03))
        one = multi.decide_player(self.observation(proposals[:1]), world.players['p1'].mind, world.config.policy)
        two = multi.decide_player(self.observation(proposals), world.players['p1'].mind, world.config.policy)
        reversed_two = multi.decide_player(self.observation(proposals[::-1]), world.players['p1'].mind, world.config.policy)
        self.assertEqual(one.candidates, two.candidates)
        self.assertEqual(two, reversed_two)
        self.assertEqual(two.score_sources['A']['coach_id'], 'c1')
        self.assertAlmostEqual(dict(two.candidates[1].terms)['advice'], .055*(.75-.24))
        negative = multi.decide_player(self.observation(proposals), world.players['p2'].mind, world.config.policy)
        self.assertAlmostEqual(dict(negative.candidates[1].terms)['advice'], .055*(.28-.58))

    def test_received_conflict_can_be_rejected_or_rest(self):
        world = self.world()
        proposals = (multi.ReceivedProposal('c1', 101, 'A', .03), multi.ReceivedProposal('c2', 102, 'B', .1))
        choice = multi.decide_player(self.observation(proposals), world.players['p1'].mind, world.config.policy)
        self.assertEqual(sum(row['method_match'] for row in choice.proposal_responses), 1)
        mind = replace(world.players['p1'].mind, expected_gains=(-.1, -.1))
        rested = multi.decide_player(self.observation(proposals, fatigue=1.), mind, world.config.policy)
        self.assertEqual(rested.chosen.action, 'rest')
        self.assertTrue(all(not row['feedback_eligible'] for row in rested.proposal_responses))

    def test_two_predictions_feedback_handcalculated_and_unrelated_edge_untouched(self):
        world = self.world()
        player = world.players['p1']
        # Real causal IDs for the fixture; only evaluation, no save of interventions.
        morning_id = world.record(1, 'morning', 'self_observation', 'p1', 'p1', skill_signal=.4, fatigue_signal=0.)
        proposal_ids = [world.record(1, 'coach', 'coach_action', cid, 'p1', action='propose', method='A') for cid in multi.COACH_IDS]
        proposals = tuple(multi.ReceivedProposal(cid, eid, 'A', q) for cid, eid, q in zip(multi.COACH_IDS, proposal_ids, (.05, .3)))
        view = multi.MultiPlayerObservation(SelfObservation(morning_id, .4, 0.), proposals)
        mind = replace(player.mind, expected_gains=(.1, -.1))
        player.mind = mind
        decision = multi.decide_player(view, mind, world.config.policy)
        did = multi.record_decision(world, 1, 'choice', 'p1', decision)
        aid = world.record(1, 'resolve', 'player_action', 'p1', 'p1', (did,), action='A')
        post = world.record(1, 'evening', 'self_observation', 'p1', 'p1', skill_signal=.45)
        multi.record_feedback(world, 1, player, view, (post, .45), decision, did, aid)
        self.assertEqual([round(edge.value, 8) for edge in player.mind.trust_edges], [.8, .7])
        feedback = [e for e in world.events if e.kind == 'advice_feedback']
        self.assertEqual(len(feedback), 2)
        self.assertAlmostEqual(feedback[0].data['self_loss'], (.05-.1)**2)
        self.assertAlmostEqual(feedback[0].data['coach_loss'], 0.)
        self.assertEqual(feedback[1].data['score_source'], 'c1')
        self.assertEqual(world.players['p2'].mind.trust_edges[0].value, .28)
        # A missing or different-method proposal supplies no feedback.
        for alternatives in ((), (multi.ReceivedProposal('c2', proposal_ids[1], 'B', .3),)):
            player.mind = mind
            alternate = replace(decision, received=alternatives)
            n = len(world.events)
            multi.record_feedback(world, 1, player, view, (post, .45), alternate, did, aid)
            self.assertEqual(n, len(world.events))
            self.assertEqual(player.mind, mind)

    def test_private_coach_change_does_not_reach_other_policy(self):
        world = self.world()
        available = multi.coach_candidates(world.players, (), ())
        view = CoachObservation(100, 10, available)
        before = decide_coach(view, world.coaches['c2'], world.config.policy)
        own = world.coaches['c1']
        adjustment = replace(own.adjustments[0], residuals=(-.3, .4))
        world.coaches['c1'] = replace(own, adjustments=(adjustment, own.adjustments[1]))
        after = decide_coach(view, world.coaches['c2'], world.config.policy)
        self.assertEqual(before, after)
        # Player policy receives no world truth or unsent coach memory.
        mind = world.players['p1'].mind
        player_view = self.observation((multi.ReceivedProposal('c2', 101, 'A', .03),))
        first = multi.decide_player(player_view, mind, world.config.policy)
        world.players['p1'].skill = .8
        world.players['p2'].rates = (.1, .2)
        self.assertEqual(first, multi.decide_player(player_view, mind, world.config.policy))

    def test_private_observation_keys_and_paid_update_ownership(self):
        world = multi.advance(self.world(), 30)
        paid = {(e.day, e.actor, e.target) for e in world.events if e.kind == 'coach_observation'}
        for event in world.events:
            if event.kind == 'coach_observation':
                self.assertEqual(event.data['skill_key'], [f'coach-skill:{event.actor}', event.day, event.target, 0])
            if event.kind in ('coach_method_experience', 'coach_post_observation', 'coach_skill_belief', 'coach_recency'):
                self.assertIn((event.day, event.actor, event.target), paid)
                for cause in event.causes:
                    previous = world.events[cause-1]
                    if previous.kind.startswith('coach_') or previous.kind == 'initial_coach_adjustment':
                        self.assertEqual(previous.actor, event.actor)
        self.assertNotEqual(signal(.5, .065, 17, 'coach-skill:c1', 1, 'p1')[0], signal(.5, .065, 17, 'coach-skill:c2', 1, 'p1')[0])
        self.assertNotEqual(world.coaches['c1'].assessments, world.coaches['c2'].assessments)

    def test_budget_and_frozen_learning_and_no_experience_transmission(self):
        world = multi.advance(self.world('multi_frozen_fixed'), 84)
        for day in range(1, 85):
            rows = [e for e in world.events if e.day == day]
            for cid in multi.COACH_IDS:
                self.assertEqual(sum(e.kind == 'coach_action' and e.actor == cid for e in rows), 1)
            for pid in world.players:
                self.assertEqual(sum(e.kind == 'player_action' and e.actor == pid for e in rows), 1)
        self.assertTrue(all(row.residuals == (0., 0.) for mind in world.coaches.values() for row in mind.adjustments))
        for pid, player in world.players.items():
            self.assertEqual([edge.value for edge in player.mind.trust_edges], [.75, .75] if pid == 'p1' else [.28, .28])
        first = multi.advance(self.world(), 1)
        # Both propose at day one: neither learns from the other's sent prediction.
        self.assertTrue(all(row.residuals == (0., 0.) for mind in first.coaches.values() for row in mind.adjustments))

    def test_processing_dictionary_order_and_noise_zero_profile_swap(self):
        left, right = self.world(), self.world()
        right.coaches = dict(reversed(list(right.coaches.items())))
        right.players = dict(reversed(list(right.players.items())))
        multi.advance(left, 84)
        observed_order = []
        def capture(view, mind, config):
            observed_order.append(next(cid for cid in multi.COACH_IDS if mind is right.coaches[cid]))
            self.assertFalse(any(e.day == view.day and e.kind == 'coach_action' for e in right.events))
            return decide_coach(view, mind, config)
        with patch.object(multi, 'decide_coach', side_effect=capture):
            multi.advance(right, 84, coach_decision_order=('c2', 'c1'))
        self.assertEqual(observed_order, ['c2', 'c1'] * 84)
        self.assertEqual(canonical(storage.snapshot(left)), canonical(storage.snapshot(right)))
        config = replace(CONDITIONS['multi_learning_adaptive'], self_noise=0., fatigue_noise=0., coach_noise=0.)
        normal = multi.advance(multi.new_world(17, config), 84)
        swapped = multi.advance(multi.new_world(17, config, coach_rates={'c1': multi.COACH_RATES['c2'], 'c2': multi.COACH_RATES['c1']}), 84)
        for pid in normal.players:
            a, b = normal.players[pid], swapped.players[pid]
            self.assertEqual((a.skill, a.fatigue, a.mind.expected_gains), (b.skill, b.fatigue, b.mind.expected_gains))
            self.assertEqual([e.value for e in a.mind.trust_edges], [e.value for e in reversed(b.mind.trust_edges)])
        for cid, other in (('c1', 'c2'), ('c2', 'c1')):
            a, b = normal.coaches[cid], swapped.coaches[other]
            self.assertEqual([r.residuals for r in a.adjustments], [r.residuals for r in b.adjustments])

    def test_all_conditions_resume_exact_snapshot_and_jsonl(self):
        with tempfile.TemporaryDirectory() as directory:
            for name in CONDITIONS:
                full = multi.advance(self.world(name), 84)
                split = multi.advance(self.world(name), 42)
                path = Path(directory)/'snapshot.json'
                storage.write_snapshot(path, split)
                loaded = storage.load_snapshot(path)
                multi.advance(loaded, 42)
                self.assertEqual(canonical(storage.snapshot(full)), canonical(storage.snapshot(loaded)))
                self.assertEqual(b''.join(storage.event_lines(full)), b''.join(storage.event_lines(loaded)))

    def test_reader_rejects_corruption_and_cross_owner_memories(self):
        world = multi.advance(self.world(), 20)
        good = json.loads(canonical(storage.snapshot(world)))
        mutations = []
        def mutation(edit):
            data = deepcopy(good)
            edit(data)
            mutations.append(data)
        mutation(lambda d: d.update(world_kind='unknown'))
        mutation(lambda d: d.update(day=-1))
        mutation(lambda d: d.update(day=True))
        mutation(lambda d: d['config']['trust'].pop('enabled'))
        mutation(lambda d: d['config']['policy'].update(coach_method_mode='unknown'))
        mutation(lambda d: d['config'].update(self_noise=float('nan')))
        mutation(lambda d: d['players']['p1'].update(skill=1.1))
        mutation(lambda d: d['players']['p1']['mind'].update(trust=.5))
        mutation(lambda d: d['players']['p1']['mind']['trust_edges'][0].update(coach_id='c2'))
        mutation(lambda d: d['coaches']['c1']['adjustments'][0].update(memory_ids=d['coaches']['c2']['adjustments'][0]['memory_ids']))
        feedback_index = next(i for i,e in enumerate(good['events']) if e['kind']=='advice_feedback')
        mutation(lambda d: d['events'][feedback_index]['data'].update(coach_id='c2' if d['events'][feedback_index]['data']['coach_id']=='c1' else 'c1'))
        observation_index = next(i for i,e in enumerate(good['events']) if e['kind']=='coach_observation')
        mutation(lambda d: d['events'][observation_index]['data']['skill_key'].__setitem__(0, 'coach-skill:c2'))
        mutation(lambda d: d['events'][observation_index]['data']['forecasts']['A'].update(expected_gain=.999))
        mutation(lambda d: d['events'][observation_index].update(causes=[1]))
        coach_action_index = next(i for i,e in enumerate(good['events']) if e['kind']=='coach_action')
        mutation(lambda d: d['events'][coach_action_index]['data'].update(slots=2))
        mutation(lambda d: d['events'][coach_action_index]['data'].update(action='unpaid_observe'))
        player_action_index = next(i for i,e in enumerate(good['events']) if e['kind']=='player_action')
        mutation(lambda d: d['events'][player_action_index]['data'].update(slots=2))
        mutation(lambda d: d['config'].pop('self_noise'))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'bad.json'
            for index, data in enumerate(mutations):
                path.write_text(json.dumps(data))
                with self.subTest(index=index), self.assertRaises(ValueError):
                    storage.load_snapshot(path)

    def test_bad_config_profile_and_actions_rejected(self):
        config = CONDITIONS['multi_learning_adaptive']
        for invalid in (replace(config, self_noise=float('inf')),
                        replace(config, beliefs=replace(config.beliefs, coach_session_observation=False))):
            with self.assertRaises(ValueError):
                multi.new_world(17, invalid)
        with self.assertRaises(ValueError):
            multi.new_world(17, config, coach_rates={'c1': (.1, .2)})
        with self.assertRaises(ValueError):
            multi.advance(self.world(), -1)
        with self.assertRaises(ValueError):
            multi.decide_player(self.observation((multi.ReceivedProposal('c1', 100, 'C', .1),)), self.world().players['p1'].mind, config.policy)


if __name__ == '__main__':
    unittest.main()
