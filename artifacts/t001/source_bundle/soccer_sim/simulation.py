"""Daily phases, resource checks, and the world-only growth rule."""
from dataclasses import asdict

from .observation import clamp, observe_self, signal, update_assessment, update_player_mind
from .policy import decide_coach, decide_player
from .state import (
    METHODS, PLAYER_ACTIONS, Assessment, CoachMind, CoachObservation, Config,
    Player, PlayerMind, PlayerObservation, Proposal, WorldState, bounded,
)

RULES_VERSION = "t001-training-v1"
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
        mind = PlayerMind(estimate, (0.046, 0.046), prefs, autonomy, trust,
                          len(world.events) + 1)
        initial = world.record(0, "setup", "initial_player", player_id, player_id,
                               state={"skill": skill, "cap": cap, "rates": rates,
                                      "fatigue": fatigue, "mind": asdict(mind)})
        world.players[player_id] = Player(player_id, skill, cap, rates, fatigue, mind, initial)
        memory_id = world.record(0, "setup", "initial_assessment", "coach", player_id,
                                 source="prior", skill_estimate=coach_estimate)
        assessments.append(Assessment(player_id, coach_estimate, 0, memory_id))
    world.coach = CoachMind(tuple(assessments))
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
        action_id = world.record(day, "coach", "coach_action", "coach", target,
                                 (decision_id,), action=action, method=method, slots=1)
        coach_actions.append(action_id)
        if action == "propose":
            proposals[target] = Proposal(action_id, method)
        if action == "observe":
            player = players[target]
            value, key = signal(player.skill, config.coach_noise, world.seed, "coach-skill",
                                day, target)
            observed_id = world.record(day, "coach", "coach_observation", "coach", target,
                                       (action_id, player.last_state_id),
                                       source="paid_sample", skill_signal=value, skill_key=key)
            observations[target] = observed_id, value

    choices = {}
    for player_id, player in players.items():
        decision = decide_player(PlayerObservation(morning[player_id], proposals.get(player_id)),
                                 player.mind, config.policy)
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
        observed_id, after = observe_self(world, player, day, "evening")
        before = morning[player_id]
        action, decision_id = choices[player_id]
        previous = player.mind
        mind = update_player_mind(previous, action, before.skill_signal, after,
                                  config, len(world.events) + 1)
        world.record(day, "evening", "player_belief", player_id, player_id,
                     (previous.memory_id, before.event_id, observed_id, decision_id),
                     source="self_observations", applied=config.belief_updates,
                     skill_before=previous.skill_estimate, skill_after=mind.skill_estimate,
                     expected_before=previous.expected_gains, expected_after=mind.expected_gains)
        player.mind = mind

    assessments = []
    for previous in world.coach.assessments:
        player_id = previous.player_id
        assessment = previous
        if player_id in observations:
            observed_id, value = observations[player_id]
            assessment = update_assessment(previous, value, config, day, len(world.events) + 1)
            world.record(day, "evening", "coach_belief", "coach", player_id,
                         (previous.memory_id, observed_id), source="paid_sample",
                         applied=config.belief_updates, skill_before=previous.skill_estimate,
                         skill_after=assessment.skill_estimate, last_seen=assessment.last_seen)
        assessments.append(assessment)
    world.coach = CoachMind(tuple(assessments))

    world.day = day
    world.validate_state()
    world.record(day, "close", "day_summary",
                 causes=(*coach_actions, *(player.last_state_id for player in players.values()),
                         *(player.mind.memory_id for player in players.values()),
                         *(assessment.memory_id for assessment in assessments)),
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
