"""Pre-registered T007 paired two-coach comparisons; no outcome-based tuning."""
from collections import Counter
from dataclasses import asdict, replace
from pathlib import Path
from random import Random
from statistics import mean
from time import perf_counter
import resource

from .experiment_t002 import bootstrap_effects, summarize_differences
from .multi_coach import (COACH_IDS, COACH_RATES, DAILY_SLOTS, advance, default_config, new_world)
from .multi_storage import identity, load_snapshot, snapshot, write_run
from .state import BeliefRules, Config, PolicyRules, TrustRules
from .storage import canonical, digest, runtime, write_json

DAYS = 84
SEED_SETS = {'exploration': tuple(range(30)), 'confirmation': tuple(range(100, 130))}
BOOTSTRAP_SEED = 7007
CONDITIONS = {
    f'multi_{learning}_{trust}': replace(default_config(),
        beliefs=replace(default_config().beliefs, coach_forecast_learning=learning == 'learning'),
        trust=replace(default_config().trust, enabled=trust == 'adaptive'))
    for learning in ('frozen', 'learning') for trust in ('fixed', 'adaptive')}
SIMPLE_PAIRS = {
    'adaptive_minus_fixed_frozen': ('multi_frozen_adaptive', 'multi_frozen_fixed'),
    'adaptive_minus_fixed_learning': ('multi_learning_adaptive', 'multi_learning_fixed'),
    'learning_minus_frozen_fixed': ('multi_learning_fixed', 'multi_frozen_fixed'),
    'learning_minus_frozen_adaptive': ('multi_learning_adaptive', 'multi_frozen_adaptive')}
CONTRASTS = {label: ((left, 1), (right, -1)) for label, (left, right) in SIMPLE_PAIRS.items()}
CONTRASTS['interaction'] = (('multi_learning_adaptive', 1), ('multi_learning_fixed', -1),
                            ('multi_frozen_adaptive', -1), ('multi_frozen_fixed', 1))


def optional_mean(values):
    return mean(values) if values else None


def trajectories(world):
    actions = {actor: [] for actor in (*world.players, *COACH_IDS)}
    for event in world.events:
        if event.kind == 'player_action':
            actions[event.actor].append(event.data['action'])
        if event.kind == 'coach_action':
            actions[event.actor].append((event.data['action'], event.target, event.data['method']))
    return actions


def action_disagreement(left, right):
    result = {actor: mean(a != b for a, b in zip(left[actor], right[actor], strict=True)) for actor in left}
    result['player'] = mean(result[pid] for pid in ('p1', 'p2'))
    result['coach'] = mean(result[cid] for cid in COACH_IDS)
    return result


def measure_run(world, elapsed):
    coaches, players = {}, {}
    for cid, mind in world.coaches.items():
        actions = [e for e in world.events if e.kind == 'coach_action' and e.actor == cid]
        experiences = [e for e in world.events if e.kind == 'coach_method_experience' and e.actor == cid]
        counts = Counter(e.data['action'] for e in actions)
        coaches[cid] = {
            'slots': len(actions), **{kind: counts[kind] for kind in ('observe', 'propose', 'rest')},
            'by_player_method': {pid: {method: {
                'observed_training': sum(e.target == pid and e.data['method'] == method for e in experiences),
                'updates': sum(e.target == pid and e.data['method'] == method and e.data['updates_enabled'] for e in experiences),
                'proposals': sum(e.target == pid and e.data['method'] == method for e in actions if e.data['action'] == 'propose'),
                'residual': next(row for row in mind.adjustments if row.player_id == pid).residuals[index],
            } for index, method in enumerate(('A', 'B'))} for pid in world.players},
            'observed_training_samples': len(experiences),
            'observed_forecast_mse': optional_mean([e.data['forecast_squared_error'] for e in experiences]),
            'observed_base_mse': optional_mean([e.data['base_squared_error'] for e in experiences]),
        }
    for pid, player in world.players.items():
        decisions = [e for e in world.events if e.kind == 'decision' and e.actor == pid]
        feedback = [e for e in world.events if e.kind == 'advice_feedback' and e.actor == pid]
        received = Counter(len(e.data['received']) for e in decisions)
        actions = Counter(e.data['chosen']['action'] for e in decisions)
        edges = {edge.coach_id: edge.value for edge in player.mind.trust_edges}
        players[pid] = {
            'skill_gain': player.skill - .38, 'final_skill': player.skill,
            'actions': {key: actions[key] for key in ('A', 'B', 'rest')},
            'received': {str(i): received[i] for i in (0, 1, 2)},
            'same_method_two': sum(len(e.data['received']) == 2 and len({r['method'] for r in e.data['received']}) == 1 for e in decisions),
            'conflicting_two': sum(len(e.data['received']) == 2 and len({r['method'] for r in e.data['received']}) == 2 for e in decisions),
            'trust_gap_c1_minus_c2': edges['c1'] - edges['c2'],
            'by_coach': {cid: {
                'trust_final': edges[cid], 'trust_change': edges[cid] - (.75 if pid == 'p1' else .28),
                'received': sum(r['coach_id'] == cid for e in decisions for r in e.data['received']),
                'method_match': sum(r['coach_id'] == cid and r['method_match'] for e in decisions for r in e.data['proposal_responses']),
                'method_mismatch': sum(r['coach_id'] == cid and not r['method_match'] for e in decisions for r in e.data['proposal_responses']),
                'rest_with_proposal': sum(e.data['chosen']['action'] == 'rest' and any(r['coach_id'] == cid for r in e.data['received']) for e in decisions),
                'score_source': {method: sum(e.data['score_sources'][method] is not None and e.data['score_sources'][method]['coach_id'] == cid for e in decisions) for method in ('A', 'B')},
                'feedback_count': sum(e.data['coach_id'] == cid for e in feedback),
                'non_dominating_feedback': sum(e.data['coach_id'] == cid and e.data['score_source'] != cid for e in feedback),
                'received_forecast_mse': optional_mean([e.data['coach_loss'] for e in feedback if e.data['coach_id'] == cid]),
                'self_mse_on_received_sample': optional_mean([e.data['self_loss'] for e in feedback if e.data['coach_id'] == cid]),
            } for cid in COACH_IDS},
        }
    observations = Counter(e.day for e in world.events if e.kind == 'coach_observation')
    duplicate = Counter((e.day, e.target) for e in world.events if e.kind == 'coach_observation')
    return {'seed': world.seed, 'days': world.day, 'elapsed_seconds': elapsed,
            'mean_skill_gain': mean(row['skill_gain'] for row in players.values()),
            'coaches': coaches, 'players': players,
            'duplicate_observation_days': sum(value == 2 for value in duplicate.values()),
            'two_observers_days': sum(value == 2 for value in observations.values()),
            'event_count': len(world.events), 'actions': trajectories(world)}


def outcomes(run):
    result = {'mean_skill_gain': run['mean_skill_gain'],
              'duplicate_observation_days': run['duplicate_observation_days']}
    for cid, row in run['coaches'].items():
        for key in ('observe', 'propose', 'rest', 'observed_training_samples', 'observed_forecast_mse', 'observed_base_mse'):
            result[f'coach.{cid}.{key}'] = row[key]
    for pid, row in run['players'].items():
        for key in ('skill_gain', 'same_method_two', 'conflicting_two', 'trust_gap_c1_minus_c2'):
            result[f'player.{pid}.{key}'] = row[key]
        for count, value in row['received'].items():
            result[f'player.{pid}.received_{count}'] = value
        for cid, edge in row['by_coach'].items():
            for key, value in edge.items():
                if isinstance(value, (float, int)) or value is None:
                    result[f'player.{pid}.{cid}.{key}'] = value
    return result


def aggregate(runs):
    result = {}
    flattened = [outcomes(row) for row in runs]
    for key in flattened[0]:
        values = [row[key] for row in flattened if row[key] is not None]
        result[key] = {'mean': optional_mean(values), 'valid_runs': len(values)}
    return result


def compare_seed_set(seeds, out):
    records = {name: [] for name in CONDITIONS}
    for seed in seeds:
        for name, config in CONDITIONS.items():
            start = perf_counter()
            world = advance(new_world(seed, config), DAYS)
            elapsed = perf_counter() - start
            records[name].append(measure_run(world, elapsed))
    conditions = {name: {'config': asdict(CONDITIONS[name]), 'runs': runs, 'aggregate': aggregate(runs)} for name, runs in records.items()}
    random = Random(BOOTSTRAP_SEED)
    resamples = [tuple(random.randrange(len(seeds)) for _ in seeds) for _ in range(2000)]
    action_effects = {}
    for label, (left, right) in SIMPLE_PAIRS.items():
        paired = [action_disagreement(a['actions'], b['actions']) for a, b in zip(records[left], records[right], strict=True)]
        action_effects[label] = {actor: summarize_differences({seed: row[actor] for seed, row in zip(seeds, paired, strict=True)}, resamples) for actor in paired[0]}
    return {'seeds': list(seeds), 'conditions': conditions,
            'paired_effects': bootstrap_effects(conditions, seeds, outcomes, CONTRASTS, BOOTSTRAP_SEED),
            'action_disagreement': action_effects}


def no_noise_probe(out):
    runs = {}
    for name, config in CONDITIONS.items():
        config = replace(config, self_noise=0.0, fatigue_noise=0.0, coach_noise=0.0)
        world = advance(new_world(17, config), DAYS)
        write_run(out / name, world, 0.0)
        runs[name] = measure_run(world, 0.0)
    return {'seed': 17, 'days': DAYS, 'runs': runs,
            'action_disagreement': {key: action_disagreement(runs[a]['actions'], runs[b]['actions']) for key, (a, b) in SIMPLE_PAIRS.items()}}


def stress_measurement(out):
    config = CONDITIONS['multi_learning_adaptive']
    start = perf_counter()
    world = advance(new_world(17, config), 1000)
    elapsed = perf_counter() - start
    start = perf_counter()
    write_run(out / 'first1000', world, elapsed)
    save_seconds = perf_counter() - start
    start = perf_counter()
    restored = load_snapshot(out / 'first1000' / 'snapshot.json')
    load_seconds = perf_counter() - start
    advance(restored, 14)
    advance(world, 14)
    if canonical(snapshot(world)) != canonical(snapshot(restored)):
        raise ValueError('stress resume mismatch')
    write_run(out / 'resumed1014', restored, 0.0)
    return {'days': 1000, 'resume_days': 14, 'resume_equal': True,
            'simulation_seconds': elapsed, 'write_seconds': save_seconds, 'load_replay_seconds': load_seconds,
            'snapshot_bytes': (out / 'first1000' / 'snapshot.json').stat().st_size,
            'events_jsonl_bytes': (out / 'first1000' / 'events.jsonl').stat().st_size,
            'max_rss_bytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            'note': 'macOS ru_maxrss bytes; process high-water mark includes full suite and two worlds'}


def run_t007(out):
    out = Path(out)
    report = {'suite': 't007', 'versions': identity(), 'runtime': runtime(), 'days_per_run': DAYS,
              'daily_slots': DAILY_SLOTS, 'run_count': 240,
              'bootstrap': {'seed': BOOTSTRAP_SEED, 'replicates': 2000, 'unit': 'paired seed',
                            'interval': '95% inclusive percentile', 'missing': 'omit incomplete paired measures'},
              'sets': {name: compare_seed_set(seeds, out / name) for name, seeds in SEED_SETS.items()},
              'probes': {'no_noise': no_noise_probe(out / 'no-noise')},
              'performance': stress_measurement(out / 'stress')}
    write_json(out / 'comparison.json', report)
    write_json(out / 'summary.json', {key: value for key, value in report.items() if key != 'sets'} | {
        'sets': {label: {name: row['aggregate'] for name, row in seed_set['conditions'].items()} for label, seed_set in report['sets'].items()}})
    return report
