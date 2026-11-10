"""Daily phases, resource checks, and the world-only growth rule."""
from dataclasses import asdict, replace

from .advice import COACH_RATE_PRIOR, evaluate_feedback, forecast_gain
from .observation import clamp, ema, observed_gain, observe_self, signal, update_player_gain
from .policy import decide_coach, decide_player
from .state import (
    METHODS, PLAYER_ACTIONS, Assessment, CoachMind, CoachObservation, Config,
    Player, PlayerMind, PlayerObservation, Proposal, WorldState, bounded,
)

RULES_VERSION = "t003-training-v3"
PLAYER_SLOTS = 1
COACH_SLOTS = 2


def new_world(seed, config=None):
    config = Config() if config is None else config
    config.validate()
    world = WorldState(seed, config)
    profiles = (
        ("p1", 0.87, (0.145, 0.035), (0.009, 0.018), 0.24, 0.75, 0.34, 0.32),
        ("p2", 0.91, (0.045, 0.13), (0.022, 0.014), 0.58, 0.28, 0.43, 0.46),
    )
    assessments = []
    for player_id, cap, rates, prefs, autonomy, trust, estimate, coach_estimate in profiles:
        if config.homogeneous_rates:
            rates = (0.095, 0.0825)
        skill = 0.38
        fatigue = 0.08
        prior_id = len(world.events) + 1
        mind = PlayerMind(estimate, (0.046, 0.046), prefs, autonomy, trust,
                          (prior_id, prior_id), prior_id, prior_id)
        initial = world.record(0, "setup", "initial_player", player_id, player_id,
                               state={"skill": skill, "cap": cap, "rates": rates,
                                      "fatigue": fatigue, "mind": asdict(mind)})
        world.players[player_id] = Player(player_id, skill, cap, rates, fatigue, mind, initial)
        memory_id = world.record(0, "setup", "initial_assessment", "coach", player_id,
                                 source="prior", skill_estimate=coach_estimate)
        assessments.append(Assessment(player_id, coach_estimate, 0, memory_id, memory_id))
    prior_id = world.record(0, "setup", "coach_forecast_prior", "coach", "coach",
                            source="subjective_prior", method_rates=COACH_RATE_PRIOR)
    world.coach = CoachMind(tuple(assessments), COACH_RATE_PRIOR, prior_id)
    world.record(0, "setup", "belief_controls", **asdict(config.beliefs))
    world.record(0, "setup", "trust_controls", **asdict(config.trust))
    world.validate_state()
    return world


def resolve_training(skill, cap, rates, fatigue, action, config, slots=PLAYER_SLOTS):
    """Compute one slot's true skill and next-morning fatigue, without mutating state."""
    if type(slots) is not int or slots != PLAYER_SLOTS or action not in PLAYER_ACTIONS:
        raise ValueError("invalid player action or slot count")
    if (not bounded(cap) or not bounded(skill, high=cap) or not bounded(fatigue)
            or len(rates) != len(METHODS) or not all(bounded(rate) for rate in rates)):
        raise ValueError("invalid true training state")
    if action == "rest":
        return skill, clamp(fatigue - config.rest_recovery - config.sleep_recovery)
    remaining = cap - skill
    gain = min(remaining, rates[METHODS.index(action)] * config.duration
               * config.focus * (1.0 - fatigue) * remaining)
    return min(cap, skill + gain), clamp(fatigue + config.training_fatigue - config.sleep_recovery)


def coach_candidates(player_ids, observed, proposed):
    available = [("rest", None, None)]
    for player_id in player_ids:
        if player_id not in observed:
            available.append(("observe", player_id, None))
        if player_id not in proposed:
            available.extend(("propose", player_id, method) for method in METHODS)
    return tuple(available)


def check_coach_action(action, target, method, remaining, player_ids, observed, proposed):
    if (type(remaining) is not int or not 1 <= remaining <= COACH_SLOTS
            or (action, target, method) not in coach_candidates(player_ids, observed, proposed)):
        raise ValueError("invalid coach action, target, repetition, or slot count")


def record_decision(world, day, phase, actor, decision):
    return world.record(day, phase, "decision", actor, decision.chosen.target or actor,
                        (*decision.observation_ids, *decision.memory_ids),
                        **decision.payload())


def record_advice_feedback(world, day, player_id, mind, view, post, choice, action_id):
    method, decision_id = choice
    proposal = view.proposal
    if proposal is None or method not in METHODS or proposal.method != method:
        return mind
    index = METHODS.index(method)
    q_self = mind.expected_gains[index]
    q_coach = proposal.expected_gain
    observed_id, after = post
    feedback = evaluate_feedback(mind.trust, view.self_view.skill_signal, after,
                                 q_self, q_coach, world.config.trust)
    self_memory = mind.gain_memory_ids[index]
    memory_id = world.record(
        day, "evening", "advice_feedback", player_id, player_id,
        (mind.trust_memory_id, self_memory, proposal.event_id, decision_id, action_id,
         view.self_view.event_id, observed_id),
        method=method, source="self_observations", trust_enabled=world.config.trust.enabled,
        q_self_before_action=q_self, q_coach_received=q_coach,
        self_gain_memory_id=self_memory, prior_trust_memory_id=mind.trust_memory_id,
        trust_before=mind.trust, trust_change=feedback.trust_after - mind.trust,
        **asdict(feedback),
    )
    if feedback.trust_after == mind.trust:
        return mind
    return replace(mind, trust=feedback.trust_after, trust_memory_id=memory_id)


def update_player_beliefs(world, day, player, before, post, choice):
    config = world.config
    player_id = player.player_id
    observed_id, after = post
    method, decision_id = choice
    mind = player.mind
    if config.beliefs.player_gain and method in METHODS:
        index = METHODS.index(method)
        measured = observed_gain(before.skill_signal, after)
        updated = update_player_gain(mind, method, measured, config.gain_alpha,
                                     len(world.events) + 1)
        world.record(day, "evening", "player_gain_belief", player_id, player_id,
                     (mind.gain_memory_ids[index], before.event_id, observed_id, decision_id),
                     method=method, source="self_observations", measured_gain=measured,
                     expected_before=mind.expected_gains[index],
                     expected_after=updated.expected_gains[index])
        mind = updated
    if config.beliefs.self_skill:
        estimate = ema(mind.skill_estimate, after, config.self_alpha)
        memory_id = world.record(day, "evening", "player_skill_belief", player_id, player_id,
                                 (mind.skill_memory_id, observed_id), source="self-post",
                                 skill_before=mind.skill_estimate, skill_after=estimate)
        mind = replace(mind, skill_estimate=estimate, skill_memory_id=memory_id)
    player.mind = mind


def update_coach_beliefs(world, day, assessment, sample):
    config = world.config
    player_id = assessment.player_id
    observed_id, value = sample
    if config.beliefs.coach_skill:
        estimate = ema(assessment.skill_estimate, value, config.coach_alpha)
        memory_id = world.record(day, "evening", "coach_skill_belief", "coach", player_id,
                                 (assessment.skill_memory_id, observed_id), source="paid_sample",
                                 skill_before=assessment.skill_estimate, skill_after=estimate)
        assessment = replace(assessment, skill_estimate=estimate, skill_memory_id=memory_id)
    if config.beliefs.recency:
        memory_id = world.record(day, "evening", "coach_recency", "coach", player_id,
                                 (assessment.recency_memory_id, observed_id), source="paid_sample",
                                 last_seen_before=assessment.last_seen, last_seen_after=day)
        assessment = replace(assessment, last_seen=day, recency_memory_id=memory_id)
    return assessment


def step(world):
    day = world.day + 1
    players = world.players
    config = world.config
    # All morning signals and coach samples precede every player action.
    morning = {player_id: observe_self(world, player, day, "morning")
               for player_id, player in players.items()}
    proposals = {}
    observations = {}
    coach_actions = []
    by_target = {assessment.player_id: assessment for assessment in world.coach.assessments}
    for slot in range(COACH_SLOTS):
        available = coach_candidates(players, observations, proposals)
        context_id = world.record(day, "coach", "coach_context", "coach",
                                  causes=tuple(coach_actions), source="schedule",
                                  slots_left=COACH_SLOTS - slot, available=available)
        decision = decide_coach(CoachObservation(context_id, day, available),
                               world.coach, config.policy)
        chosen = decision.chosen
        action, target, method = chosen.action, chosen.target, chosen.method
        check_coach_action(action, target, method, COACH_SLOTS - slot,
                           players, observations, proposals)
        decision_id = record_decision(world, day, "coach", "coach", decision)
        causes = (decision_id,)
        forecast = {}
        if action == "propose":
            assessment = by_target[target]
            rates = world.coach.method_rates
            forecast = {"expected_gain": forecast_gain(method, assessment.skill_estimate, rates),
                        "perceived_skill": assessment.skill_estimate,
                        "subjective_rate": rates[METHODS.index(method)]}
            causes += (assessment.skill_memory_id, world.coach.forecast_memory_id)
        action_id = world.record(day, "coach", "coach_action", "coach", target,
                                 causes, action=action, method=method, slots=1, **forecast)
        coach_actions.append(action_id)
        if action == "propose":
            proposals[target] = Proposal(action_id, method, forecast["expected_gain"])
        if action == "observe":
            player = players[target]
            value, key = signal(player.skill, config.coach_noise, world.seed, "coach-skill",
                                day, target)
            observed_id = world.record(day, "coach", "coach_observation", "coach", target,
                                       (action_id, player.last_state_id),
                                       source="paid_sample", skill_signal=value, skill_key=key)
            observations[target] = observed_id, value

    views = {player_id: PlayerObservation(morning[player_id], proposals.get(player_id))
             for player_id in players}
    choices = {}
    for player_id, player in players.items():
        decision = decide_player(views[player_id], player.mind, config.policy)
        decision_id = record_decision(world, day, "choice", player_id, decision)
        choices[player_id] = decision.chosen.action, decision_id

    updates = {}
    for player_id, player in players.items():
        action, decision_id = choices[player_id]
        skill, fatigue = resolve_training(player.skill, player.cap, player.rates,
                                          player.fatigue, action, config)
        action_id = world.record(day, "resolve", "player_action", player_id, player_id,
                                 (decision_id,), action=action, slots=PLAYER_SLOTS)
        updates[player_id] = skill, fatigue, action_id

    for player_id, player in players.items():
        skill, fatigue, action_id = updates[player_id]
        state_id = world.record(day, "resolve", "state_update", player_id, player_id,
                                (action_id, player.last_state_id), source="training_rule",
                                skill_before=player.skill, skill_after=skill,
                                fatigue_before=player.fatigue, fatigue_after=fatigue,
                                skill_gain=skill - player.skill)
        player.skill = skill
        player.fatigue = fatigue
        player.last_state_id = state_id

    for player_id, player in players.items():
        post = observe_self(world, player, day, "evening")
        player.mind = record_advice_feedback(world, day, player_id, player.mind, views[player_id],
                                             post, choices[player_id], updates[player_id][2])
        update_player_beliefs(world, day, player, morning[player_id], post, choices[player_id])

    assessments = []
    for previous in world.coach.assessments:
        player_id = previous.player_id
        assessment = previous
        if player_id in observations:
            assessment = update_coach_beliefs(world, day, previous, observations[player_id])
        assessments.append(assessment)
    world.coach = replace(world.coach, assessments=tuple(assessments))

    world.day = day
    world.validate_state()
    world.record(day, "close", "day_summary",
                 causes=(*coach_actions, *(player.last_state_id for player in players.values()),
                         *(event_id for player in players.values()
                           for event_id in (*player.mind.gain_memory_ids,
                                            player.mind.skill_memory_id, player.mind.trust_memory_id)),
                         *(event_id for assessment in assessments
                           for event_id in (assessment.skill_memory_id, assessment.recency_memory_id))),
                 coach_slots=len(coach_actions),
                 player_slots={player_id: PLAYER_SLOTS for player_id in players},
                 player_actions={player_id: action for player_id, (action, _) in choices.items()})


def advance(world, days):
    if type(days) is not int or days < 0:
        raise ValueError("days must be a nonnegative integer")
    world.config.validate()
    world.validate_state()
    for _ in range(days):
        step(world)
    return world
