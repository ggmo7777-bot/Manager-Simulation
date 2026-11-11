"""T044 post-hoc probe (exploratory, registered after the main result): do the two boundary players of a lineup decision differ most in their recent true-skill change at the middle of the rest-rate sweep?
Same 20 arms and 252 cells as T044, no recorder. Per lineup decision (>= 3 players): the 14-day change of the true skill of every roster player, |change_j - change_k| of T043's boundary pair, the SD over the roster and its mean.
Protocol: docs/experiments/T044-PROTOCOL.md (section on the post-hoc addition).
"""
import argparse
from concurrent.futures import ProcessPoolExecutor
import json
import os
from pathlib import Path
import random
import sys
from time import perf_counter

CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT))
from experiments import t023, t036, t040, t043, t044
from soccer_sim import league_economy as le
from soccer_sim.storage import runtime, write_json

DAYS, SEEDS, DIRECTIONS, ARMS = t044.DAYS, t044.SEEDS, t044.DIRECTIONS, t044.ARMS
WINDOW_DAYS = 14
GROWTH_METRICS = {'het_pair': ('gr', 'pair'), 'het_sd': ('gr', 'sd'), 'growth': ('gr', 'mean')}
METRICS = {'top2': ('start', 'top2'), 'act_rest': ('act', 'rest'), 'med_err': ('med', 'err'), **GROWTH_METRICS}
add, interval, sub = t044.add, t044.interval, t044.sub
mean_of = t044.mean_of


def population_sd(values):
    values = list(values)
    mean = mean_of(values)
    return (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5


def record_growth(acc, config, events, history, day, truth_of):
    """history[d] = {player: true skill at the end of day d}; the change of the true skill over the last WINDOW_DAYS days (newcomers: since their first value)."""
    past = history[max(day - WINDOW_DAYS, 0)]
    for e in events:
        ranking = e['data']['ranking'] if e['kind'] == 'lineup_decision' else []
        if len(ranking) < 3:
            continue
        pids = [r['player_id'] for r in ranking]
        change = {pid: truth_of(pid) - past.get(pid, truth_of(pid)) for pid in pids}
        decision = t043.decompose(ranking, {pid: truth_of(pid) for pid in pids}, config)
        j, k = t044.boundary_pair(ranking, decision['true_set'])
        add(acc, ('gr', 'pair'), abs(change[j] - change[k]))
        add(acc, ('gr', 'sd'), population_sd(change.values()))
        add(acc, ('gr', 'mean'), mean_of(change.values()))


def run_arm(setting, name, seed, start, spec, days=DAYS):
    direction, kind, param = spec
    config = t036.make_cfg(setting, name, start, DIRECTIONS[direction][0])
    acc, prev = {}, {}
    world = le._build_initial_world(seed=seed, config=config)
    history = {0: {pid: p.composite_skill() for pid, p in world.players.items()}}
    with t043.installed(None, kind, param, 0.0, acc):
        while world.day < days:
            n_events = len(world.events)
            le.advance_league_days(world, 1)
            events = world.events[n_events:]
            history[world.day] = {pid: p.composite_skill() for pid, p in world.players.items()}
            truth_of = lambda pid: history[world.day][pid]
            t040.record_day(acc, events, truth_of, prev)
            t043.record_practice(acc, events)
            t043.record_mediators(acc, config, events, truth_of)
            record_growth(acc, config, events, history, world.day, truth_of)
    le._validate_league_invariants(world)
    return acc


def run_cell(task):
    setting, name, seed, start = task
    return task, {arm: run_arm(setting, name, seed, start, spec) for arm, spec in ARMS.items()}


def effects(v):
    out = {f'level|{arm}|{m}': value for arm, levels in v.items() for m, value in levels.items()}
    for d in DIRECTIONS:
        for name, (high, low) in t044.CONTRASTS.items():
            for m in GROWTH_METRICS:
                out[f'contrast|{d}|{name}|{m}'] = sub(v[f'{d}_{high}'][m], v[f'{d}_{low}'][m])
    return out


def summarise(cell_accs, boots, seed=t044.SEED_BOOT):
    vectors = {arm: {m: ([a[key][0] if key in a else 0 for a in accs], [a[key][1] if key in a else 0.0 for a in accs]) for m, key in METRICS.items()} for arm, accs in cell_accs.items()}
    n_cells = len(next(iter(cell_accs.values())))
    point = effects(t040.resampled_levels(vectors, [1] * n_cells))
    rng, draws = random.Random(seed), []
    for _ in range(boots):
        counts = [0] * n_cells
        for _ in range(n_cells):
            counts[rng.randrange(n_cells)] += 1
        draws.append(effects(t040.resampled_levels(vectors, counts)))
    return {'cells': n_cells, 'point': point, 'ci95': {k: interval([d[k] for d in draws]) for k in point}}


def run(out, root, boots=1000, workers=None):
    out.mkdir(parents=True, exist_ok=False)
    start = perf_counter()
    tasks = [(t, name, seed, st) for t in t044.SETTINGS for name in t023.conditions() for seed in SEEDS for st in t044.STARTS]
    cells = []
    with ProcessPoolExecutor(max_workers=workers or os.cpu_count()) as pool:
        for count, (_, got) in enumerate(pool.map(run_cell, tasks, chunksize=1), start=1):
            cells.append(got)
            if count % 12 == 0:
                print(count, len(tasks), round(perf_counter() - start), flush=True)
    saved = json.loads((root / 'artifacts/t044/run/summary.json').read_text())['summaries']['all']['point']
    summary = summarise({arm: [c[arm] for c in cells] for arm in ARMS}, boots)
    worst = max(abs(summary['point'][f'level|{arm}|{m}'] - saved[f'level|{arm}|{m}']) for arm in ARMS for m in ('top2', 'act_rest', 'med_err'))
    assert worst <= 1e-12, worst  # the probe changes nothing: T044's levels are reproduced
    lag = {d: [saved[f'level|{a}|cause_lag'] for a in t044.curve(d)] for d in DIRECTIONS}
    correlation = {d: t044.pearson([summary['point'][f'level|{a}|het_pair'] for a in t044.curve(d)], lag[d]) for d in DIRECTIONS}
    result = {'task': 'T044-posthoc-growth', 'identity': le.league_identity(), 'runtime': runtime(), 'window_days': WINDOW_DAYS, 'summary': summary,
              't044_reproduced_max_abs_difference': worst, 'corr_het_pair_with_cause_lag': correlation, 'elapsed_seconds': perf_counter() - start}
    write_json(out / 'summary.json', result)
    print(json.dumps({'reproduced': worst, 'corr_het_pair_with_cause_lag': correlation, 'elapsed_seconds': result['elapsed_seconds']}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=None)
    arguments = parser.parse_args()
    run(arguments.out.resolve(), CODE_ROOT, workers=arguments.workers)
