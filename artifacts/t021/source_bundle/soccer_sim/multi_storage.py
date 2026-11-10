"""Strict T007 day-boundary saves, verified by deterministic full-history replay.

Load cost is proportional to elapsed days. Mid-run interventions which cannot be
reproduced from the saved initial fixture and Config are deliberately unsupported.
"""
import json
from dataclasses import asdict
from pathlib import Path
from time import perf_counter

from .multi_coach import (DAILY_SLOTS, POLICY_VERSION, RULES_VERSION, WORLD_KIND,
                          MultiPlayer, MultiPlayerMind, TrustEdge, advance, new_world, validate_config)
from .state import (Assessment, BeliefRules, CoachAdjustment, CoachMind, Config,
                    Event, PolicyRules, TrustRules)
from .storage import (atomic_write, canonical, digest, event_lines, identity as package_identity,
                      parse_float, reject_nonfinite, runtime, write_json)

SAVE_VERSION = 7


def identity():
    return {**package_identity(), 'save': SAVE_VERSION, 'rules': RULES_VERSION,
            'policy': POLICY_VERSION, 'world_kind': WORLD_KIND}


def snapshot(world):
    return {'world_kind': WORLD_KIND, 'versions': identity(), 'config': asdict(world.config),
            'daily_slots': DAILY_SLOTS, 'day': world.day,
            'rng': {'root_seed': world.seed, 'key_fields': ['channel', 'day', 'actor_id', 'draw_index']},
            'initial_coach_rates': world.initial_coach_rates,
            'next_event_id': len(world.events) + 1,
            'players': {pid: asdict(world.players[pid]) for pid in sorted(world.players)},
            'coaches': {cid: asdict(world.coaches[cid]) for cid in sorted(world.coaches)},
            'events': [vars(event) for event in world.events]}


def strict_config(raw):
    nested = {}
    for key, cls in (('policy', PolicyRules), ('beliefs', BeliefRules), ('trust', TrustRules)):
        nested[key] = cls(**raw[key])
        if set(raw[key]) != set(asdict(nested[key])):
            raise ValueError('missing configuration controls')
    config = Config(**{**raw, **nested})
    if set(raw) != set(asdict(config)):
        raise ValueError('missing configuration coefficients')
    validate_config(config)
    return config


def load_snapshot(path):
    try:
        data = json.loads(Path(path).read_bytes(), parse_constant=reject_nonfinite, parse_float=parse_float)
        if (data['world_kind'] != WORLD_KIND or data['versions'] != identity()
                or data['daily_slots'] != DAILY_SLOTS):
            raise ValueError('incompatible world, versions, source or budgets')
        if (type(data['day']) is not int or data['day'] < 0
                or type(data['rng']['root_seed']) is not int
                or data['rng']['key_fields'] != ['channel', 'day', 'actor_id', 'draw_index']):
            raise ValueError('invalid day or RNG schema')
        config = strict_config(data['config'])
        # Require a completed log before spending time replaying a possibly huge day.
        events = data['events']
        if (not isinstance(events, list) or type(data['next_event_id']) is not int
                or data['next_event_id'] != len(events) + 1
                or (data['day'] and (len(events) < data['day'] or events[-1]['kind'] != 'day_summary'
                                     or events[-1]['day'] != data['day']))):
            raise ValueError('invalid day boundary')
        world = new_world(data['rng']['root_seed'], config, coach_rates=data['initial_coach_rates'])
        # Decode and validate the saved current state before replay. History is
        # then compared in full, including pointers no longer live in state.
        saved = new_world(data['rng']['root_seed'], config, coach_rates=data['initial_coach_rates'])
        saved.day = data['day']
        saved.events = [Event(**{**row, 'causes': tuple(row['causes'])}) for row in events]
        saved.players = {}
        for pid, raw in data['players'].items():
            mind = raw['mind']
            decoded = MultiPlayerMind(**{**mind,
                'expected_gains': tuple(mind['expected_gains']),
                'preferences': tuple(mind['preferences']),
                'gain_memory_ids': tuple(mind['gain_memory_ids']),
                'trust_edges': tuple(TrustEdge(**edge) for edge in mind['trust_edges'])})
            saved.players[pid] = MultiPlayer(**{**raw, 'mind': decoded, 'rates': tuple(raw['rates'])})
        saved.coaches = {}
        for cid, raw in data['coaches'].items():
            saved.coaches[cid] = CoachMind(**{**raw, 'method_rates': tuple(raw['method_rates']),
                'assessments': tuple(Assessment(**row) for row in raw['assessments']),
                'adjustments': tuple(CoachAdjustment(row['player_id'], tuple(row['residuals']), tuple(row['memory_ids']))
                                     for row in raw['adjustments'])})
        saved.validate_state()
        advance(world, data['day'])
        # Canonical bytes distinguish bool/int and reject all nonfinite values.
        if canonical(snapshot(world)) != canonical(data):
            raise ValueError('saved state or history differs from deterministic private-world replay')
        return world
    except (KeyError, TypeError, AttributeError, IndexError, OverflowError) as exc:
        raise ValueError('malformed multi snapshot') from exc


def write_snapshot(path, world):
    world.validate_state()
    atomic_write(path, [canonical(snapshot(world)), b'\n'])


def write_run(out, world, elapsed_seconds):
    from .experiment_t007 import measure_run
    world.validate_state()
    out = Path(out)
    data = snapshot(world)
    state_hash = digest(data)
    atomic_write(out / 'snapshot.json', [canonical(data), b'\n'])
    atomic_write(out / 'events.jsonl', event_lines(world))
    write_json(out / 'summary.json', {**measure_run(world, elapsed_seconds), 'simulation_sha256': state_hash})
    write_json(out / 'manifest.json', {
        'world_kind': WORLD_KIND, 'versions': identity(), 'runtime': runtime(),
        'config': data['config'], 'rng': data['rng'], 'initial_coach_rates': data['initial_coach_rates'],
        'daily_slots': DAILY_SLOTS, 'day': world.day, 'simulation_sha256': state_hash,
        'reader': 'full deterministic replay; cost proportional to days; unreplayable mid-run interventions unsupported'})
    return state_hash
