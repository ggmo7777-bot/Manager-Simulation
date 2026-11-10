"""T007: two private coaches and player-to-coach prediction trust.

Only this world's scheduling and attribution differ. A and the elementary B
updates use the same functions as the retained single-coach world; C is absent.
"""
from dataclasses import asdict, dataclass, field, replace

from .advice import coach_experience, coach_forecast, evaluate_feedback, remember_coach_experience
from .observation import ema, observe_self, signal
from .policy import Candidate, decide_coach
from .simulation import coach_candidates, resolve_training, update_player_beliefs
from .state import (METHODS, Assessment, CoachAdjustment, CoachMind, CoachObservation,
                    BeliefRules, Config, Event, PolicyRules, SelfObservation, TrustRules, WorldState, bounded)

WORLD_KIND = 'multi_coach'
POLICY_VERSION = 't007-private-score-v1'
RULES_VERSION = 't007-private-training-v1'
COACH_IDS = ('c1', 'c2')
COACH_RATES = {'c1': (0.12, 0.04), 'c2': (0.04, 0.12)}
DAILY_SLOTS = {'player': 1, 'coach_each': 1, 'coach_total': 2}


@dataclass(frozen=True)
class TrustEdge:
    coach_id: str
    value: float
    memory_id: int


@dataclass(frozen=True)
class MultiPlayerMind:
    skill_estimate: float
    expected_gains: tuple[float, float]
    preferences: tuple[float, float]
    autonomy: float
    gain_memory_ids: tuple[int, int]
    skill_memory_id: int
    trust_edges: tuple[TrustEdge, ...]


@dataclass
class MultiPlayer:
    player_id: str
    skill: float
    cap: float
    rates: tuple[float, float]
    fatigue: float
    mind: MultiPlayerMind
    last_state_id: int


@dataclass(frozen=True)
class ReceivedProposal:
    coach_id: str
    event_id: int
    method: str
    expected_gain: float


@dataclass(frozen=True)
class MultiPlayerObservation:
    self_view: SelfObservation
    proposals: tuple[ReceivedProposal, ...]


@dataclass(frozen=True)
class MultiDecision:
    chosen: Candidate
    candidates: tuple[Candidate, ...]
    observation_ids: tuple[int, ...]
    memory_ids: tuple[int, ...]
    received: tuple[ReceivedProposal, ...]
    score_sources: dict
    proposal_responses: tuple[dict, ...]

    def payload(self):
        return {'policy_version': POLICY_VERSION, 'chosen': self.chosen.payload(),
                'candidates': [row.payload() for row in self.candidates],
                'observation_ids': self.observation_ids, 'memory_ids': self.memory_ids,
                'received': [asdict(row) for row in self.received],
                'score_sources': self.score_sources,
                'proposal_responses': self.proposal_responses}


def decide_player(observation, mind, config):
    """Pure private-input policy. Advice sources are selected without summing votes."""
    proposals = tuple(sorted(observation.proposals, key=lambda row: row.coach_id))
    if (len({row.coach_id for row in proposals}) != len(proposals)
            or any(row.coach_id not in COACH_IDS or row.method not in METHODS
                   or not bounded(row.expected_gain, low=-1.0) for row in proposals)):
        raise ValueError('invalid received proposals')
    edges = {edge.coach_id: edge for edge in mind.trust_edges}
    fatigue = observation.self_view.fatigue_signal
    sources, memories = {}, list(mind.gain_memory_ids)
    candidates = [Candidate('rest', None, None,
                            (('recovery_value', config.rest_fatigue_weight * fatigue),))]
    for index, method in enumerate(METHODS):
        matching = [row for row in proposals if row.method == method]
        source = max(matching, key=lambda row: edges[row.coach_id].value) if matching else None
        edge = edges[source.coach_id] if source else None
        sources[method] = ({'coach_id': source.coach_id, 'proposal_id': source.event_id,
                            'trust': edge.value, 'trust_memory_id': edge.memory_id} if source else None)
        advice = config.advice_weight * (edge.value - mind.autonomy) if source else 0.0
        candidates.append(Candidate(method, None, method, (
            ('expected_gain', mind.expected_gains[index]), ('preference', mind.preferences[index]),
            ('felt_fatigue_cost', -config.training_fatigue_weight * fatigue), ('advice', advice))))
    chosen = max(candidates, key=lambda row: row.score)
    # All received edge memories are recorded, including non-dominating predictions.
    memories.extend(edges[row.coach_id].memory_id for row in proposals)
    responses = tuple({'coach_id': row.coach_id, 'proposal_id': row.event_id,
                       'method_match': chosen.method == row.method,
                       'feedback_eligible': chosen.method == row.method} for row in proposals)
    return MultiDecision(chosen, tuple(candidates),
                         (observation.self_view.event_id, *(row.event_id for row in proposals)),
                         tuple(dict.fromkeys(memories)), proposals, sources, responses)


@dataclass
class MultiWorld:
    seed: int
    config: Config
    day: int = 0
    players: dict[str, MultiPlayer] = field(default_factory=dict)
    coaches: dict[str, CoachMind] = field(default_factory=dict)
    events: list[Event] = field(default_factory=list)
    initial_coach_rates: dict = field(default_factory=lambda: dict(COACH_RATES))

    record = WorldState.record
    validate_pointer = WorldState.validate_pointer

    def owner_pointer(self, event_id, actor, target, kinds):
        event = self.validate_pointer(event_id, target, kinds)
        if event.actor != actor:
            raise ValueError('memory belongs to another individual')
        return event

    def validate_state(self):
        validate_config(self.config)
        if (type(self.seed) is not int or type(self.day) is not int or self.day < 0
                or set(self.players) != {'p1', 'p2'} or set(self.coaches) != set(COACH_IDS)):
            raise ValueError('invalid multi world roster or time')
        for pid, player in self.players.items():
            mind = player.mind
            if (pid != player.player_id or not bounded(player.cap)
                    or not bounded(player.skill, high=player.cap)
                    or len(player.rates) != 2 or len(mind.expected_gains) != 2
                    or len(mind.preferences) != 2 or len(mind.gain_memory_ids) != 2
                    or not all(bounded(x) for x in (player.fatigue, *player.rates,
                                                   mind.skill_estimate, *mind.preferences, mind.autonomy))
                    or not all(bounded(x, low=-1.0) for x in mind.expected_gains)
                    or tuple(edge.coach_id for edge in mind.trust_edges) != COACH_IDS):
                raise ValueError('invalid player state')
            state = self.owner_pointer(player.last_state_id, pid, pid, {'initial_player', 'state_update'})
            if state.kind == 'state_update' and (state.data['skill_after'], state.data['fatigue_after']) != (player.skill, player.fatigue):
                raise ValueError('true state differs from history')
            self.owner_pointer(mind.skill_memory_id, pid, pid, {'initial_player', 'player_skill_belief'})
            for index, memory_id in enumerate(mind.gain_memory_ids):
                event = self.owner_pointer(memory_id, pid, pid, {'initial_player', 'player_gain_belief'})
                value = (event.data['state']['mind']['expected_gains'][index] if event.kind == 'initial_player'
                         else event.data['expected_after'])
                if (value != mind.expected_gains[index] or (event.kind == 'player_gain_belief'
                                                            and event.data['method'] != METHODS[index])):
                    raise ValueError('gain memory mismatch')
            for edge in mind.trust_edges:
                event = self.owner_pointer(edge.memory_id, pid, pid, {'initial_trust', 'advice_feedback'})
                if (not bounded(edge.value) or event.data['coach_id'] != edge.coach_id
                        or event.data['trust_after'] != edge.value):
                    raise ValueError('trust edge memory mismatch')
        for cid, mind in self.coaches.items():
            if (tuple(row.player_id for row in mind.assessments) != ('p1', 'p2')
                    or tuple(row.player_id for row in mind.adjustments) != ('p1', 'p2')
                    or len(mind.method_rates) != 2 or not all(bounded(x) for x in mind.method_rates)):
                raise ValueError('invalid coach state')
            prior = self.owner_pointer(mind.forecast_memory_id, cid, cid, {'coach_forecast_prior'})
            if tuple(prior.data['method_rates']) != mind.method_rates:
                raise ValueError('coach rate memory mismatch')
            for row in mind.assessments:
                if not bounded(row.skill_estimate) or type(row.last_seen) is not int or not 0 <= row.last_seen <= self.day:
                    raise ValueError('invalid private assessment')
                self.owner_pointer(row.skill_memory_id, cid, row.player_id, {'initial_assessment', 'coach_skill_belief'})
                self.owner_pointer(row.recency_memory_id, cid, row.player_id, {'initial_assessment', 'coach_recency'})
            for row in mind.adjustments:
                if len(row.residuals) != 2 or len(row.memory_ids) != 2:
                    raise ValueError('invalid coach adjustment')
                for index, memory_id in enumerate(row.memory_ids):
                    event = self.owner_pointer(memory_id, cid, row.player_id, {'initial_coach_adjustment', 'coach_method_experience'})
                    value = (event.data['residuals'][index] if event.kind == 'initial_coach_adjustment'
                             else event.data['residual_after'])
                    if (not bounded(row.residuals[index], low=-2.0) or value != row.residuals[index]
                            or (event.kind == 'coach_method_experience' and event.data['method'] != METHODS[index])):
                        raise ValueError('private residual memory mismatch')


def validate_config(config):
    config.validate()
    if (config.beliefs.gain_target_mode != 'signed' or not config.beliefs.coach_session_observation
            or config.policy.coach_method_mode != 'forecast_rank'):
        raise ValueError('multi world requires signed gain, paid session, forecast rank')


def default_config():
    return Config(beliefs=BeliefRules(gain_target_mode='signed', coach_session_observation=True,
                                     coach_forecast_learning=True),
                  trust=TrustRules(enabled=True), policy=PolicyRules(coach_method_mode='forecast_rank'))


def new_world(seed, config=None, *, coach_rates=None):
    if config is None:
        config = default_config()
    validate_config(config)
    rates = dict(COACH_RATES if coach_rates is None else coach_rates)
    if set(rates) != set(COACH_IDS) or any(len(row) != 2 or not all(bounded(x) for x in row) for row in rates.values()):
        raise ValueError('invalid coach profile')
    world = MultiWorld(seed, config, initial_coach_rates={cid: tuple(rates[cid]) for cid in COACH_IDS})
    profiles = (('p1', .87, (.145, .035), (.009, .018), .24, .75, .34),
                ('p2', .91, (.045, .13), (.022, .014), .58, .28, .43))
    for pid, cap, true_rates, prefs, autonomy, trust, estimate in profiles:
        if config.homogeneous_rates:
            true_rates = (.095, .0825)
        initial_id = len(world.events) + 1
        edges = tuple(TrustEdge(cid, trust, initial_id + 1 + index) for index, cid in enumerate(COACH_IDS))
        mind = MultiPlayerMind(estimate, (.046, .046), prefs, autonomy,
                               (initial_id, initial_id), initial_id, edges)
        world.record(0, 'setup', 'initial_player', pid, pid,
                     state={'skill': .38, 'cap': cap, 'rates': true_rates, 'fatigue': .08, 'mind': asdict(mind)})
        world.players[pid] = MultiPlayer(pid, .38, cap, true_rates, .08, mind, initial_id)
        for edge in edges:
            world.record(0, 'setup', 'initial_trust', pid, pid, (initial_id,),
                         coach_id=edge.coach_id, trust_after=edge.value)
    for cid in COACH_IDS:
        assessments, adjustments = [], []
        prior_id = world.record(0, 'setup', 'coach_forecast_prior', cid, cid,
                                source='subjective_prior', method_rates=rates[cid])
        for pid, estimate in (('p1', .32), ('p2', .46)):
            memory = world.record(0, 'setup', 'initial_assessment', cid, pid,
                                  source='prior', skill_estimate=estimate)
            assessments.append(Assessment(pid, estimate, 0, memory, memory))
            memory = world.record(0, 'setup', 'initial_coach_adjustment', cid, pid,
                                  (prior_id,), residuals=(0.0, 0.0))
            adjustments.append(CoachAdjustment(pid, (0.0, 0.0), (memory, memory)))
        world.coaches[cid] = CoachMind(tuple(assessments), tuple(rates[cid]), prior_id, tuple(adjustments))
    world.record(0, 'setup', 'belief_controls', **asdict(config.beliefs))
    world.record(0, 'setup', 'trust_controls', **asdict(config.trust))
    world.validate_state()
    return world


def record_decision(world, day, phase, actor, decision):
    return world.record(day, phase, 'decision', actor, decision.chosen.target or actor,
                        (*decision.observation_ids, *decision.memory_ids), **decision.payload())


def record_feedback(world, day, player, view, post, decision, decision_id, action_id):
    """Each eligible prediction uses the same pre-action self forecast and edge."""
    mind = player.mind
    edges = {edge.coach_id: edge for edge in mind.trust_edges}
    updated = dict(edges)
    for proposal in decision.received:
        if proposal.method != decision.chosen.method:
            continue
        index = METHODS.index(proposal.method)
        edge = edges[proposal.coach_id]
        feedback = evaluate_feedback(edge.value, view.self_view.skill_signal, post[1],
                                     mind.expected_gains[index], proposal.expected_gain, world.config.trust)
        memory = world.record(day, 'evening', 'advice_feedback', player.player_id, player.player_id,
                              (edge.memory_id, mind.gain_memory_ids[index], proposal.event_id,
                               decision_id, action_id, view.self_view.event_id, post[0]),
                              coach_id=proposal.coach_id, proposal_id=proposal.event_id,
                              method=proposal.method, source='self_observations',
                              score_source=decision.score_sources[proposal.method]['coach_id'],
                              feedback_eligible=True, trust_enabled=world.config.trust.enabled,
                              q_self_before_action=mind.expected_gains[index],
                              q_coach_received=proposal.expected_gain,
                              prior_trust_memory_id=edge.memory_id,
                              self_gain_memory_id=mind.gain_memory_ids[index], trust_before=edge.value,
                              trust_change=feedback.trust_after-edge.value, **asdict(feedback))
        if feedback.trust_after != edge.value:
            updated[proposal.coach_id] = TrustEdge(proposal.coach_id, feedback.trust_after, memory)
    player.mind = replace(mind, trust_edges=tuple(updated[cid] for cid in COACH_IDS))


def step(world, *, coach_decision_order=COACH_IDS):
    if tuple(sorted(coach_decision_order)) != COACH_IDS:
        raise ValueError('invalid coach decision order')
    day, config = world.day + 1, world.config
    morning = {pid: observe_self(world, world.players[pid], day, 'morning') for pid in sorted(world.players)}
    # Compute *every* private decision before recording/executing any coach action.
    available = coach_candidates(sorted(world.players), (), ())
    contexts = {cid: world.record(day, 'coach', 'coach_context', cid, source='own_schedule',
                                  slots_left=1, available=available) for cid in COACH_IDS}
    decisions = {cid: decide_coach(CoachObservation(contexts[cid], day, available),
                                    world.coaches[cid], config.policy)
                 for cid in coach_decision_order}
    proposals = {pid: [] for pid in world.players}
    sessions, coach_actions = {}, []
    for cid in COACH_IDS:
        mind, decision = world.coaches[cid], decisions[cid]
        decision_id = record_decision(world, day, 'coach', cid, decision)
        action, target, method = decision.chosen.action, decision.chosen.target, decision.chosen.method
        if (action, target, method) not in coach_candidates(sorted(world.players), (), ()):
            raise ValueError('invalid coach action')
        assessments = {row.player_id: row for row in mind.assessments}
        adjustments = {row.player_id: row for row in mind.adjustments}
        forecast, causes = {}, (decision_id,)
        if action == 'propose':
            assessment = assessments[target]
            prediction = coach_forecast(method, assessment.skill_estimate, mind.method_rates, adjustments[target])
            forecast = {**asdict(prediction), 'perceived_skill': assessment.skill_estimate,
                        'subjective_rate': mind.method_rates[METHODS.index(method)]}
            causes += (assessment.skill_memory_id, mind.forecast_memory_id, prediction.residual_memory_id)
        action_id = world.record(day, 'coach', 'coach_action', cid, target, causes,
                                 action=action, method=method, slots=1, **forecast)
        coach_actions.append(action_id)
        if action == 'propose':
            proposals[target].append(ReceivedProposal(cid, action_id, method, prediction.expected_gain))
        elif action == 'observe':
            player, assessment, adjustment = world.players[target], assessments[target], adjustments[target]
            value, key = signal(player.skill, config.coach_noise, world.seed, f'coach-skill:{cid}', day, target)
            forecasts = {method: coach_forecast(method, assessment.skill_estimate, mind.method_rates, adjustment)
                         for method in METHODS}
            observed_id = world.record(day, 'coach', 'coach_observation', cid, target,
                                       (action_id, player.last_state_id, assessment.skill_memory_id,
                                        mind.forecast_memory_id, *adjustment.memory_ids),
                                       source='paid_sample', skill_signal=value, skill_key=key,
                                       perceived_skill=assessment.skill_estimate,
                                       forecasts={key: asdict(row) for key, row in forecasts.items()})
            sessions[cid] = target, observed_id, value, forecasts
    views, choices = {}, {}
    for pid in sorted(world.players):
        player = world.players[pid]
        views[pid] = MultiPlayerObservation(morning[pid], tuple(proposals[pid]))
        decision = decide_player(views[pid], player.mind, config.policy)
        decision_id = record_decision(world, day, 'choice', pid, decision)
        choices[pid] = decision, decision_id
    action_ids = {}
    for pid in sorted(world.players):
        player, (decision, decision_id) = world.players[pid], choices[pid]
        skill, fatigue = resolve_training(player.skill, player.cap, player.rates, player.fatigue, decision.chosen.action, config)
        action_id = world.record(day, 'resolve', 'player_action', pid, pid, (decision_id,), action=decision.chosen.action, slots=1)
        state_id = world.record(day, 'resolve', 'state_update', pid, pid, (action_id, player.last_state_id),
                                source='training_rule', skill_before=player.skill, skill_after=skill,
                                fatigue_before=player.fatigue, fatigue_after=fatigue, skill_gain=skill-player.skill)
        player.skill, player.fatigue, player.last_state_id = skill, fatigue, state_id
        action_ids[pid] = action_id
    for pid in sorted(world.players):
        player, (decision, decision_id) = world.players[pid], choices[pid]
        post = observe_self(world, player, day, 'evening')
        record_feedback(world, day, player, views[pid], post, decision, decision_id, action_ids[pid])
        update_player_beliefs(world, day, player, morning[pid], post, (decision.chosen.action, decision_id))
    for cid in COACH_IDS:
        if cid not in sessions:
            continue
        target, pre_id, before, forecasts = sessions[cid]
        mind, player = world.coaches[cid], world.players[target]
        performed = choices[target][0].chosen.action
        after, key = signal(player.skill, config.coach_noise, world.seed, f'coach-skill-post:{cid}', day, target)
        post_id = world.record(day, 'evening', 'coach_post_observation', cid, target,
                               (pre_id, action_ids[target], player.last_state_id), source='paid_session_post',
                               skill_signal=after, skill_key=key, performed_action=performed)
        adjustments = {row.player_id: row for row in mind.adjustments}
        if performed in METHODS:
            forecast = forecasts[performed]
            learn = config.beliefs.coach_forecast_learning
            experience = coach_experience(before, after, forecast, learn)
            memory = world.record(day, 'evening', 'coach_method_experience', cid, target,
                                  (pre_id, post_id, forecast.residual_memory_id), source='paid_session',
                                  method=performed, updates_enabled=learn, **asdict(forecast), **experience)
            if learn:
                adjustments[target] = remember_coach_experience(adjustments[target], performed, experience['residual_after'], memory)
        assessments = []
        for row in mind.assessments:
            if row.player_id == target:
                if config.beliefs.coach_skill:
                    estimate = ema(row.skill_estimate, before, config.coach_alpha)
                    memory = world.record(day, 'evening', 'coach_skill_belief', cid, target,
                                          (row.skill_memory_id, pre_id), source='paid_sample',
                                          skill_before=row.skill_estimate, skill_after=estimate)
                    row = replace(row, skill_estimate=estimate, skill_memory_id=memory)
                if config.beliefs.recency:
                    memory = world.record(day, 'evening', 'coach_recency', cid, target,
                                          (row.recency_memory_id, pre_id), source='paid_sample',
                                          last_seen_before=row.last_seen, last_seen_after=day)
                    row = replace(row, last_seen=day, recency_memory_id=memory)
            assessments.append(row)
        world.coaches[cid] = replace(mind, assessments=tuple(assessments), adjustments=tuple(adjustments.values()))
    world.day = day
    world.validate_state()
    world.record(day, 'close', 'day_summary', causes=(*coach_actions, *(world.players[pid].last_state_id for pid in sorted(world.players))),
                 coach_slots={cid: 1 for cid in COACH_IDS}, player_slots={pid: 1 for pid in sorted(world.players)},
                 player_actions={pid: row[0].chosen.action for pid, row in choices.items()})


def advance(world, days, *, coach_decision_order=COACH_IDS):
    if type(days) is not int or days < 0:
        raise ValueError('days must be a nonnegative integer')
    world.validate_state()
    for _ in range(days):
        step(world, coach_decision_order=coach_decision_order)
    return world
