"""Separate coach forecast learning from its use for method ranking."""
from collections import Counter
from dataclasses import asdict, replace
from pathlib import Path
from random import Random
from statistics import mean
from time import perf_counter

from .experiment_t002 import (
    BOOTSTRAP_REPLICATES, SEED_SETS, bootstrap_effects, stress_measurement,
    summarize_differences, trajectories,
)
from .experiment_t004 import DAYS, aggregate_condition
from .experiment_t005 import CONDITIONS as T005_CONDITIONS, measure_run as prior_measure, outcomes as prior_outcomes
from .storage import DAILY_SLOTS, digest, identity, runtime, write_json
from .simulation import advance, new_world
from .state import METHODS

BOOTSTRAP_SEED = 6006
CONDITIONS = {
    f'{learning}_{mode}': replace(
        T005_CONDITIONS[f'coach_{learning}_signed'],
        policy=replace(T005_CONDITIONS[f'coach_{learning}_signed'].policy,
                       coach_method_mode='forecast_rank' if mode == 'rank' else 'legacy'))
    for learning in ('frozen', 'learning') for mode in ('legacy', 'rank')
}
SIMPLE_PAIRS = {
    'rank_minus_legacy_frozen': ('frozen_rank', 'frozen_legacy'),
    'rank_minus_legacy_learning': ('learning_rank', 'learning_legacy'),
    'learning_minus_frozen_legacy': ('learning_legacy', 'frozen_legacy'),
    'learning_minus_frozen_rank': ('learning_rank', 'frozen_rank'),
}
CONTRASTS = {label: ((left, 1), (right, -1)) for label, (left, right) in SIMPLE_PAIRS.items()}
CONTRASTS['learning_rank_interaction'] = (
    ('learning_rank', 1), ('learning_legacy', -1), ('frozen_rank', -1), ('frozen_legacy', 1),
)


def selection_metrics(world):
    counts = Counter()
    proposals, implemented = Counter(), Counter()
    by_day = {}
    same_state_comparisons = 0
    kind_target_changes = 0
    for event in world.events:
        row = event.data
        if event.kind == 'coach_action' and row['action'] == 'propose':
            proposals[event.target, row['method']] += 1
            by_day[event.day, event.target] = row['method']
        if event.kind == 'player_action' and row['action'] == by_day.get((event.day, event.target)):
            implemented[event.target, row['action']] += 1
        if event.kind == 'decision' and event.actor == 'coach' and world.config.policy.coach_method_mode == 'forecast_rank':
            candidates = row['candidates']
            def legacy_score(candidate):
                if candidate['action'] != 'propose':
                    return candidate['score']
                skill = candidate['method_selection']['perceived_skill']
                return (candidate['terms']['development_need'] + world.config.policy.coach_method_fit
                        * ((1.0 - skill) if candidate['method'] == 'A' else skill))
            legacy = max(candidates, key=legacy_score)
            selected = row['chosen']
            same_state_comparisons += 1
            changed = (selected['action'], selected['target']) != (legacy['action'], legacy['target'])
            kind_target_changes += changed
            if selected['action'] == 'propose':
                evidence = selected['method_selection']
                counts['ranked_proposals' if evidence['basis'] == 'forecast_rank' else 'tie_fallback_proposals'] += 1
                counts['same_state_method_changes'] += selected['method'] != legacy['method']
                counts['both_forecasts_negative_proposals'] += all(
                    forecast['expected_gain'] < 0 for forecast in evidence['forecasts'].values())
    if kind_target_changes:
        raise ValueError('method ranking directly changed coach kind or target priority')
    return {
        **{key: counts[key] for key in ('ranked_proposals', 'tie_fallback_proposals',
                                      'same_state_method_changes', 'both_forecasts_negative_proposals')},
        'same_state_comparisons': same_state_comparisons,
        'same_state_kind_target_changes': kind_target_changes,
        'by_target_method': {
            target: {method: {'proposals': proposals[target, method],
                              'implemented_advice': implemented[target, method]}
                     for method in METHODS} for target in world.players},
    }


def measure_run(world, elapsed):
    result = prior_measure(world, elapsed)
    result['method_selection'] = selection_metrics(world)
    return result


def outcomes(run):
    result = prior_outcomes(run)
    for key, value in run['method_selection'].items():
        if key != 'by_target_method':
            result[f'selection.{key}'] = value
    for target, methods in run['method_selection']['by_target_method'].items():
        for method, metrics in methods.items():
            for metric, value in metrics.items():
                result[f'selection.{target}.{method}.{metric}'] = value
    return result


def action_disagreement(left, right):
    result = {actor: mean(a != b for a, b in zip(actions, right[actor], strict=True))
              for actor, actions in left.items()}
    result['player'] = mean(value for actor, value in result.items() if actor != 'coach')
    pairs = list(zip(left['coach'], right['coach'], strict=True))
    for index, label in enumerate(('kind', 'target', 'method')):
        result[f'coach_{label}'] = mean(a[index] != b[index] for a, b in pairs)
    return result


def action_effects(records, seeds):
    random = Random(BOOTSTRAP_SEED)
    n = len(seeds)
    resamples = [tuple(random.randrange(n) for _ in seeds) for _ in range(BOOTSTRAP_REPLICATES)]
    results = {}
    for label, (left, right) in SIMPLE_PAIRS.items():
        paired = {seed: action_disagreement(a['actions'], b['actions'])
                  for seed, a, b in zip(seeds, records[left], records[right], strict=True)}
        results[label] = {
            actor: summarize_differences({seed: row[actor] for seed, row in paired.items()}, resamples)
            for actor in paired[seeds[0]]}
    return results


def compare_seed_set(seeds):
    records = {name: [] for name in CONDITIONS}
    for seed in seeds:
        for name, config in CONDITIONS.items():
            start = perf_counter()
            world = advance(new_world(seed, config), DAYS)
            records[name].append(measure_run(world, perf_counter() - start))
    conditions = {name: {'config': asdict(CONDITIONS[name]), 'runs': runs,
                         'aggregate': aggregate_condition(runs, outcomes)}
                  for name, runs in records.items()}
    return {'seeds': list(seeds), 'conditions': conditions,
            'paired_effects': bootstrap_effects(conditions, seeds, outcomes, CONTRASTS, BOOTSTRAP_SEED),
            'action_disagreement': action_effects(records, seeds)}


def control_probe(*, fixed_trust=False, no_noise=False, clipped=False):
    runs = {}
    for name, config in CONDITIONS.items():
        if fixed_trust:
            config = replace(config, trust=replace(config.trust, enabled=False))
        if no_noise:
            config = replace(config, self_noise=0.0, fatigue_noise=0.0, coach_noise=0.0)
        if clipped:
            config = replace(config, beliefs=replace(config.beliefs, gain_target_mode='clipped'))
        world = advance(new_world(17, config), DAYS)
        actions, states = trajectories(world)
        runs[name] = {'config': asdict(config), 'outcomes': outcomes(measure_run(world, 0.0)),
                      'actions': actions, 'states_sha256': digest(states)}
    comparisons = {}
    for label, (left, right) in SIMPLE_PAIRS.items():
        comparisons[label] = {
            'action_disagreement': action_disagreement(runs[left]['actions'], runs[right]['actions']),
            'same_truth_history': runs[left]['states_sha256'] == runs[right]['states_sha256']}
    return {'seed': 17, 'days': DAYS, 'runs': runs, 'comparisons': comparisons}


def run_t006(out):
    out = Path(out)
    sets = {name: compare_seed_set(seeds) for name, seeds in SEED_SETS.items()}
    report = {
        'suite': 't006', 'versions': identity(), 'runtime': runtime(), 'days_per_run': DAYS,
        'daily_slots': DAILY_SLOTS,
        'run_count': sum(len(CONDITIONS) * len(seeds) for seeds in SEED_SETS.values()),
        'bootstrap': {'seed': BOOTSTRAP_SEED, 'rng': 'python-random-mt19937',
                      'replicates': BOOTSTRAP_REPLICATES, 'unit': 'paired seed',
                      'interval': '95% inclusive percentile',
                      'missing': 'omit seeds missing a run-level measure in any contrasted condition',
                      'numeric_contrasts': CONTRASTS, 'direct_action_pairs': SIMPLE_PAIRS},
        'interpretation': {
            'same_state_method_changes': 'old formula rescored at the very same private state, not an outcome counterfactual',
            'coach_components': 'per-slot disagreement of action kind, target, or method; can overlap',
            'rank_metrics_legacy': 'zero denotes ranking mode unused, not tied forecasts',
            'observed_forecast_mse': 'coach-private delta for observed training',
            'mean_coach_squared_error': 'player-private delta for adopted and executed advice',
            'zero_method_counts': 'zero exposure or update counts, with MSE null when no experience',
        },
        'sets': sets,
        'probes': {'fixed_trust': control_probe(fixed_trust=True),
                   'no_noise': control_probe(no_noise=True), 'clipped': control_probe(clipped=True)},
        'performance_learning_rank': stress_measurement(CONDITIONS['learning_rank']),
    }
    write_json(out / 'comparison.json', report)
    write_json(out / 'summary.json', {
        'versions': report['versions'], 'run_count': report['run_count'],
        'performance_learning_rank': report['performance_learning_rank'],
        'sets': {label: {name: result['aggregate'] for name, result in seed_set['conditions'].items()}
                 for label, seed_set in sets.items()},
    })
    return report
