"""T045: does the U-shape of the lineup match flatten when, at the moment of each lineup decision only, the coach's estimates lose their lag (or their definition gap, or their observation noise)? And does a rest rate of 36-45% cost anything late (1,008 days, 48 seasons with generations)?
Analysis only: `soccer_sim/` is unchanged and no config is added. Part A wraps `le.decide_club_lineup` (the match-day call only; the transfer decisions that call it too keep the real estimates) so that it reads, from a copy, each roster player's two estimates
with one error term (or two) taken out. The four terms come from T044's shadow estimates `R`/`A`/`I`/`Z`, rebuilt from the recorded observations of the world that is actually played: noise R-A, definition A-I, first impression I-Z, lag Z-T (T = the true skill at the decision).
Variants: none (identity), nolag (R-Z+T), nodef (R-A+I), nonoise (A), nolagdef (R-Z+T-A+I). Part B runs the sweep points base, o(+0.010), o(+0.030) for 1,008 days with 168-day windows and no swap.
True skills are read by the researcher; they enter the simulation only through the registered swap at the lineup decision.

Protocol: docs/experiments/T045-PROTOCOL.md. Cells: 14 worlds x seeds {100,101,102} x start age {29,33}, three settings.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor
from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import random
import sys
from time import perf_counter
from types import SimpleNamespace

CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT))
from experiments import t023, t030, t036, t038, t040, t043, t044
from soccer_sim import league_economy as le
from soccer_sim.match_2d import round6
from soccer_sim.storage import runtime, write_json

DAYS, SEEDS, STARTS, SETTINGS = t036.DAYS, t036.SEEDS, t036.STARTS, t036.SETTINGS
DIRECTIONS = t040.DIRECTIONS
POINTS = ('o-0.0300', 'o-0.0100', 'base', 'o+0.0100', 'o+0.0300')
VALLEY, HIGH_END, LOW_END, KNEE = 'o-0.0100', 'o-0.0300', 'o+0.0300', 'o+0.0100'
CONTRASTS = {'LO': (LOW_END, VALLEY), 'HI': (HIGH_END, VALLEY), 'KN': (LOW_END, KNEE)}
U_CONTRASTS = ('LO', 'HI')
# the error terms each variant takes out of the estimate, and the same estimate written directly (the two are checked against each other at every decision)
REMOVED = {'none': (), 'nolag': ('lag',), 'nodef': ('def',), 'nonoise': ('noise',), 'nolagdef': ('lag', 'def')}
DIRECT = {'none': lambda r, a, i, z, t: r, 'nolag': lambda r, a, i, z, t: r - z + t, 'nodef': lambda r, a, i, z, t: r - a + i,
          'nonoise': lambda r, a, i, z, t: a, 'nolagdef': lambda r, a, i, z, t: r - z + t - a + i}
VARIANTS = tuple(REMOVED)
SWAPPED = tuple(v for v in VARIANTS if v != 'none')
ARM_SEP = '__'
ARMS = {f'{d}_{p}{ARM_SEP}{v}': (d, p, v) for d in DIRECTIONS for p in POINTS for v in VARIANTS}
POINT_ARMS = {'base': 'base', **{p: p for p in POINTS if p != 'base'}}
SWAP_TOLERANCE = 1e-12
REPRODUCED = t044.REPRODUCED
MATCH_DAY_CALLER = '_step_matchday'
RATIO_REMOVES, RATIO_FLATTENS, RATIO_UNCHANGED = 0.2, 0.5, (0.85, 1.15)
SEED_BOOT = 41
add, merge, interval = t036.add, t036.merge, t038.interval
sub, merged, resampled_levels = t040.sub, t040.merged, t040.resampled_levels
excludes_zero, mean_of, pearson, ratio = t044.excludes_zero, t043.mean_of, t044.pearson, t044.ratio
check_sim_unchanged, plain_digest, check_t037_reproduced = t040.check_sim_unchanged, t040.plain_digest, t040.check_t037_reproduced
# metric id -> accumulator key
METRICS = {'top2': ('start', 'top2'), 'top2_now': ('start', 'top2_now'), 'act_rest': ('act', 'rest'), 'fatigue': ('fat', 'mean'),
           'med_gap': ('med', 'gap'), 'med_err': ('med', 'err'), 'med_signal': ('med', 'signal'), **{f'med_{p}': ('med', p) for p in t043.PARTS},
           'abs_s': ('x', 'abs_s'), 'abs_m': ('x', 'abs_m'), **{f'final_{k}': ('final', k) for k in t040.FINALS}}
GAIN_METRICS = ('top2', 'top2_now', 'med_err', 'abs_s', 'abs_m')

# ---- part B ----
LONG_DAYS, WINDOW = 1008, 168
WINDOWS = tuple(f'w{k}' for k in range(1, LONG_DAYS // WINDOW + 1))
LATE_WINDOWS = ('w5', 'w6')
GROUPS = (*WINDOWS, 'late')
LONG_POINTS = ('base', KNEE, LOW_END)
LONG_ARMS = {f'{d}_{p}': (d, p) for d in DIRECTIONS for p in LONG_POINTS}
LONG_CONTRASTS = {'K45': (KNEE, 'base'), 'K36': (LOW_END, 'base'), 'KN': (LOW_END, KNEE)}
LONG_METRICS = {'top2': ('start', 'top2'), 'act_rest': ('act', 'rest'), 'fatigue': ('fat', 'mean'), **{f'final_{k}': ('final', k) for k in t040.FINALS}}
LATE_COST_METRICS = ('top2', 'final_skill')


# ---- the swap ----
def executed_estimates(estimates, truth, variant):
    """The (skill, match) estimates the lineup reads for one (coach, player). `estimates` = the shadow estimates {R, A, I, Z} -> (skill, match); `truth` = the true skill at the decision.
    The direct formula is checked against 'R minus the removed terms' of the four-term split."""
    out = []
    for index in range(2):
        r, a, i, z = (estimates[name][index] for name in t044.FILTERS)
        terms = t044.telescope(estimates, index, truth)
        value = DIRECT[variant](r, a, i, z, truth)
        assert abs(value - (r - sum(terms[c] for c in REMOVED[variant]))) <= SWAP_TOLERANCE, (variant, index, value)
        out.append(value)
    return tuple(out)


class Interventions(t044.Shadows):
    """T044's read-only shadow record, for a world whose lineups read the swapped estimates: the lineup check compares the recorded terms with the swapped estimates."""

    def __init__(self, world, acc):
        super().__init__(world, acc)
        self.executed = {}
        self.checked['swapped_decisions'] = 0

    def lineup(self, world, event):
        d, cfg = event['data'], self.config
        for row in d['ranking']:
            pid = row['player_id']
            skill, match = self.executed[pid]
            assert round6(cfg.select_skill_weight * skill) == row['components']['skill_term'] and round6(cfg.select_match_weight * match) == row['components']['match_term'], (event['id'], pid)
            self.fatigue_before[pid] = world.players[pid].fatigue
            self.checked['lineup_terms'] += 1
        self.executed = {}


@contextmanager
def lineup_swap(world, tracker, variant):
    """On match days, the lineup reads a copy of the coach's evaluations with the swapped estimates (fatigue estimate and observation reference unchanged). Everything else, and the transfer decisions, call the real function.
    The real function is put back on exit, also after an exception."""
    original = le.decide_club_lineup

    def decide(*, coach, roster, config):
        if sys._getframe(1).f_code.co_name != MATCH_DAY_CALLER:
            return original(coach=coach, roster=roster, config=config)
        evaluations, executed, truths, abs_errors = {}, {}, {}, ([], [])
        for pid in roster:
            ev = coach.evaluations[pid]
            truths[pid] = world.players[pid].composite_skill()
            executed[pid] = executed_estimates(tracker.cells[coach.coach_id, pid]['est'], truths[pid], variant)
            evaluations[pid] = SimpleNamespace(skill_estimate=executed[pid][0], match_rating_estimate=executed[pid][1], fatigue_estimate=ev.fatigue_estimate, last_obs_ref=ev.last_obs_ref)
            for index in range(2):
                abs_errors[index].append(abs(executed[pid][index] - truths[pid]))
        tracker.executed = executed
        tracker.checked['swapped_decisions'] += 1
        lineup = original(coach=SimpleNamespace(club_id=coach.club_id, coach_id=coach.coach_id, evaluations=evaluations), roster=roster, config=config)
        true_now = t038.top2(list(truths.items()))
        add(tracker.acc, ('start', 'top2_now'), float(set(lineup['starters']) == true_now))
        add(tracker.acc, ('x', 'abs_s'), mean_of(abs_errors[0]))
        add(tracker.acc, ('x', 'abs_m'), mean_of(abs_errors[1]))
        return lineup

    le.decide_club_lineup = decide
    try:
        yield
    finally:
        le.decide_club_lineup = original


# ---- part A: one run ----
def split_arm(arm):
    direction, point, variant = ARMS[arm]
    return f'{direction}_{point}', variant


def run_arm(setting, name, seed, start, arm, days=DAYS):
    base_arm, variant = split_arm(arm)
    direction, kind, param = t043.ARMS[base_arm]
    config = t036.make_cfg(setting, name, start, DIRECTIONS[direction][0])
    acc, prev, real_lineup = {}, {}, le.decide_club_lineup
    world = le._build_initial_world(seed=seed, config=config)
    tracker = Interventions(world, acc)
    with t043.installed(None, kind, param, 0.0, acc), t044.recording(tracker), lineup_swap(world, tracker, variant):
        while world.day < days:
            n_events = len(world.events)
            le.advance_league_days(world, 1)
            events = world.events[n_events:]
            tracker.end_of_day(world)
            truth = {}
            truth_of = lambda pid: truth.setdefault(pid, world.players[pid].composite_skill())
            t040.record_day(acc, events, truth_of, prev)
            t043.record_practice(acc, events)
            t043.record_mediators(acc, config, events, truth_of)
            add(acc, ('fat', 'mean'), mean_of(world.players[pid].fatigue for pid in le._active_pids(world)))
    assert not tracker.executed
    assert le.decide_club_lineup is real_lineup  # the swap has been put back
    le._validate_league_invariants(world)
    flat = {f'final_{k}': v for k, v in t030.final_metrics(world).items()}
    flat['events'] = len(world.events)
    for key in t040.FINALS:
        add(acc, ('final', key), flat[f'final_{key}'])
    return {'acc': acc, 'flat': flat, 'checked': tracker.checked, 'world': world}


def run_cell(task):
    setting, name, seed, start = task
    out = {arm: {key: value for key, value in run_arm(setting, name, seed, start, arm).items() if key != 'world'} for arm in ARMS}
    plain = le.run_league_world(seed=seed, days=DAYS, config=t036.make_cfg(setting, name, start, 'centered_v1'))
    out['plain_centered'] = {'flat': {**{f'final_{k}': v for k, v in t030.final_metrics(plain).items()}, 'events': len(plain.events)},
                             'decisions': sum(1 for e in plain.events if e['kind'] == 'lineup_decision')}
    return task, out


# ---- part A: gates ----
def check_swap_is_a_read_only_copy(days=DAYS):
    """Gate A5: with the identity swap, base equals the plain world and o(+0.010) equals T043's arm without any recorder, snapshot for snapshot; every swap puts the real function back;
    the nolag swap does change some world (the swap is effective)."""
    names, count, changed = t023.conditions(), 0, 0
    for setting, index, seed, start in t040.IDENTITY_PICKS:
        for direction, (world_mode, _) in DIRECTIONS.items():
            real = le.decide_club_lineup
            plain = le.run_league_world(seed=seed, days=days, config=t036.make_cfg(setting, names[index], start, world_mode))
            none_base = run_arm(setting, names[index], seed, start, f'{direction}_base{ARM_SEP}none', days=days)['world']
            assert plain_digest(none_base) == plain_digest(plain), (setting, index, seed, start, direction)
            spec = t043.ARMS[f'{direction}_o+0.0100']
            reference = t043.run_arm(setting, names[index], seed, start, spec, days=days)['world']
            assert plain_digest(run_arm(setting, names[index], seed, start, f'{direction}_o+0.0100{ARM_SEP}none', days=days)['world']) == plain_digest(reference), (setting, index, seed, start, direction)
            nolag = run_arm(setting, names[index], seed, start, f'{direction}_base{ARM_SEP}nolag', days=days)['world']
            changed += plain_digest(nolag) != plain_digest(plain)
            assert le.decide_club_lineup is real
            count += 3
    assert changed, 'the nolag swap changed no world'
    return {'runs': count, 'all_equal': True, 'nolag_changed_worlds': changed, 'nolag_worlds': len(t040.IDENTITY_PICKS) * len(DIRECTIONS)}


def check_t044_reproduced(root, levels_by_scope):
    """Gate A3: every shared metric of every `none` arm has T044's level (settings and total)."""
    saved = json.loads((root / 'artifacts/t044/run/summary.json').read_text())['summaries']
    worst = 0.0
    for scope, by_arm in levels_by_scope.items():
        for arm, levels in by_arm.items():
            for metric in REPRODUCED:
                worst = max(worst, abs(levels[metric] - saved[scope]['point'][f'level|{arm}|{metric}']))
    assert worst <= 1e-12, worst
    return {'max_abs_difference': worst, 'metrics': len(REPRODUCED), 'arms': len(next(iter(levels_by_scope.values())))}


# ---- part A: statistics ----
def curve(direction, variant):
    return [f'{direction}_{p}{ARM_SEP}{variant}' for p in POINTS]


def effects(v):
    """v[arm][metric] = level. Levels, the three contrasts of every metric per direction and variant, the contrasts' difference to `none` and ratio (primary metric), the gain of each variant over `none`
    at the valley and on average, the range of the lineup match over the five points and corr(top2, err)."""
    out = {f'level|{arm}|{m}': value for arm, levels in v.items() for m, value in levels.items()}
    for d in DIRECTIONS:
        for variant in VARIANTS:
            for name, (high, low) in CONTRASTS.items():
                for m in METRICS:
                    out[f'contrast|{d}|{variant}|{name}|{m}'] = sub(v[f'{d}_{high}{ARM_SEP}{variant}'][m], v[f'{d}_{low}{ARM_SEP}{variant}'][m])
            arms = curve(d, variant)
            out[f'corr|{d}|{variant}'] = pearson([v[a]['top2'] for a in arms], [v[a]['med_err'] for a in arms])
            out[f'range|{d}|{variant}'] = max(v[a]['top2'] for a in arms) - min(v[a]['top2'] for a in arms)
        for variant in SWAPPED:
            for name in CONTRASTS:
                for m in METRICS:
                    out[f'dcontrast|{d}|{variant}|{name}|{m}'] = sub(out[f'contrast|{d}|{variant}|{name}|{m}'], out[f'contrast|{d}|none|{name}|{m}'])
            for name in U_CONTRASTS:
                out[f'ratio|{d}|{variant}|{name}'] = ratio(out[f'contrast|{d}|{variant}|{name}|top2'], out[f'contrast|{d}|none|{name}|top2'])
            for m in GAIN_METRICS:
                out[f'valley_gain|{d}|{variant}|{m}'] = sub(v[f'{d}_{VALLEY}{ARM_SEP}{variant}'][m], v[f'{d}_{VALLEY}{ARM_SEP}none'][m])
                out[f'mean_gain|{d}|{variant}|{m}'] = mean_of(v[f'{d}_{p}{ARM_SEP}{variant}'][m] - v[f'{d}_{p}{ARM_SEP}none'][m] for p in POINTS)
    return out


def cell_levels(cell_accs, metrics):
    """cell_accs[arm] = list of per-cell accumulators -> {arm: {metric: (n list, sum list)}} for fast resampling."""
    return {arm: {m: ([a[key][0] if key in a else 0 for a in accs], [a[key][1] if key in a else 0.0 for a in accs]) for m, key in metrics.items()} for arm, accs in cell_accs.items()}


def summarise(cell_accs, boots, effects_of, metrics, seed=SEED_BOOT):
    """One resample of the cells is shared by every arm, statistic and contrast."""
    vectors = cell_levels(cell_accs, metrics)
    n_cells = len(next(iter(cell_accs.values())))
    point = effects_of(resampled_levels(vectors, [1] * n_cells))
    rng, draws = random.Random(seed), []
    for _ in range(boots):
        counts = [0] * n_cells
        for _ in range(n_cells):
            counts[rng.randrange(n_cells)] += 1
        draws.append(effects_of(resampled_levels(vectors, counts)))
    return {'cells': n_cells, 'point': point, 'ci95': {k: interval([d[k] for d in draws]) for k in point}}


# ---- part A: the pre-registered flattening rule ----
def classify_flattening(point, ci, variant):
    """The verdict on the U-shape of the lineup match for one swap variant, from the four cells (direction x LO/HI): see the protocol. `undefined` when `none` shows no U in some cell."""
    cells = [(d, c) for d in DIRECTIONS for c in U_CONTRASTS]
    for d, c in cells:
        if point[f'contrast|{d}|none|{c}|top2'] is None or point[f'contrast|{d}|none|{c}|top2'] <= 0:
            return {'verdict': 'undefined', 'ratios': {}}
    ratios = {f'{d}|{c}': point[f'ratio|{d}|{variant}|{c}'] for d, c in cells}
    own = {(d, c): ci[f'contrast|{d}|{variant}|{c}|top2'] for d, c in cells}
    diff = {(d, c): ci[f'dcontrast|{d}|{variant}|{c}|top2'] for d, c in cells}
    lower = lambda d, c: diff[d, c][1] < 0
    if any(own[cell][1] < 0 for cell in cells):
        verdict = 'inverts'
    elif all((ratios[f'{d}|{c}'] <= RATIO_REMOVES or not excludes_zero(own[d, c])) and lower(d, c) for d, c in cells):
        verdict = 'removes'
    elif all(ratios[f'{d}|{c}'] <= RATIO_FLATTENS and lower(d, c) for d, c in cells):
        verdict = 'flattens'
    elif all(RATIO_UNCHANGED[0] <= ratios[f'{d}|{c}'] <= RATIO_UNCHANGED[1] for d, c in cells):
        verdict = 'unchanged'
    elif any(ratios[f'{d}|{c}'] > RATIO_UNCHANGED[1] and diff[d, c][0] > 0 for d, c in cells):
        verdict = 'steepens'
    else:
        verdict = 'partial'
    return {'verdict': verdict, 'ratios': ratios}


def classify_hurt(ci, key):
    """hurts (upper < 0), helps (lower > 0) or flat."""
    pair = ci[key]
    return 'hurts' if pair[1] < 0 else ('helps' if pair[0] > 0 else 'flat')


# ---- part B ----
def run_long_arm(setting, name, seed, start, arm, days=LONG_DAYS, window=WINDOW):
    """No swap. Per-window accumulators: the lineup match, the rest rate and the mean fatigue over the days of the window; the final metrics (skill, satisfaction, trust, transfers) at the window's last day."""
    direction, point = LONG_ARMS[arm]
    _, kind, param = t043.ARMS[f'{direction}_{point}']
    config = t036.make_cfg(setting, name, start, DIRECTIONS[direction][0])
    windows, prev = [{} for _ in range(days // window)], {}
    world = le._build_initial_world(seed=seed, config=config)
    with t043.installed(None, kind, param, 0.0, {}):
        while world.day < days:
            n_events = len(world.events)
            le.advance_league_days(world, 1)
            acc = windows[(world.day - 1) // window]
            truth = {}
            truth_of = lambda pid: truth.setdefault(pid, world.players[pid].composite_skill())
            events = world.events[n_events:]
            t040.record_day(acc, events, truth_of, prev)
            t043.record_practice(acc, events)
            add(acc, ('fat', 'mean'), mean_of(world.players[pid].fatigue for pid in le._active_pids(world)))
            if world.day % window == 0:
                for key, value in t030.final_metrics(world).items():
                    add(acc, ('final', key), value)
    le._validate_league_invariants(world)
    flat = {f'final_{k}': v for k, v in t030.final_metrics(world).items()}
    flat['events'] = len(world.events)
    return {'windows': windows, 'flat': flat}


def grouped(windows):
    """{group: accumulator}: the windows and `late` (windows 5 and 6 together)."""
    groups = {f'w{k}': acc for k, acc in enumerate(windows, start=1)}
    groups['late'] = merged(groups[w] for w in LATE_WINDOWS)
    return groups


def run_long_cell(task):
    setting, name, seed, start = task
    out = {arm: run_long_arm(setting, name, seed, start, arm) for arm in LONG_ARMS}
    for direction, (world_mode, _) in DIRECTIONS.items():
        plain = le.run_league_world(seed=seed, days=LONG_DAYS, config=t036.make_cfg(setting, name, start, world_mode))
        out[f'plain_{direction}'] = {'flat': {**{f'final_{k}': v for k, v in t030.final_metrics(plain).items()}, 'events': len(plain.events)}}
    return task, out


def long_level_vectors(cell_groups):
    """cell_groups[arm] = list of per-cell {group: acc} -> {arm: {'metric|group': (n list, sum list)}}."""
    return {arm: {f'{m}|{g}': ([c[g][key][0] if key in c[g] else 0 for c in cells], [c[g][key][1] if key in c[g] else 0.0 for c in cells]) for m, key in LONG_METRICS.items() for g in GROUPS}
            for arm, cells in cell_groups.items()}


def long_effects(v):
    """v[arm]['metric|group'] = level. Levels, contrasts per direction, the drift (window 6 - window 1) per arm and the drift of each contrast, the fatigue ratio window 6 / window 1."""
    out = {f'level|{arm}|{key}': value for arm, levels in v.items() for key, value in levels.items()}
    for d in DIRECTIONS:
        for name, (high, low) in LONG_CONTRASTS.items():
            for key in v[f'{d}_{high}']:
                out[f'contrast|{d}|{name}|{key}'] = sub(v[f'{d}_{high}'][key], v[f'{d}_{low}'][key])
            for m in LONG_METRICS:
                out[f'drift_contrast|{d}|{name}|{m}'] = sub(out[f'contrast|{d}|{name}|{m}|w6'], out[f'contrast|{d}|{name}|{m}|w1'])
        for point in LONG_POINTS:
            for m in LONG_METRICS:
                out[f'drift|{d}_{point}|{m}'] = sub(v[f'{d}_{point}'][f'{m}|w6'], v[f'{d}_{point}'][f'{m}|w1'])
            out[f'fatigue_ratio|{d}_{point}'] = ratio(v[f'{d}_{point}']['fatigue|w6'], v[f'{d}_{point}']['fatigue|w1'])
    return out


def summarise_long(cell_groups, boots, seed=SEED_BOOT):
    vectors = long_level_vectors(cell_groups)
    n_cells = len(next(iter(cell_groups.values())))
    point = long_effects(resampled_levels(vectors, [1] * n_cells))
    rng, draws = random.Random(seed), []
    for _ in range(boots):
        counts = [0] * n_cells
        for _ in range(n_cells):
            counts[rng.randrange(n_cells)] += 1
        draws.append(long_effects(resampled_levels(vectors, counts)))
    return {'cells': n_cells, 'point': point, 'ci95': {k: interval([d[k] for d in draws]) for k in point}}


def late_cost(ci):
    """Registered: late cost = `hurts` for the lineup match or the final skill across KN (late) in either direction; growing cost = the drift of the KN contrast has an upper bound < 0 for either metric in either direction."""
    hurts = {f'{d}|{m}': classify_hurt(ci, f'contrast|{d}|KN|{m}|late') for d in DIRECTIONS for m in LATE_COST_METRICS}
    growing = {f'{d}|{m}': ci[f'drift_contrast|{d}|KN|{m}'][1] < 0 for d in DIRECTIONS for m in LATE_COST_METRICS}
    return {'kn_late': hurts, 'late_cost': 'hurts' in hurts.values(), 'growing': growing, 'growing_cost': any(growing.values())}


def check_window_one_reproduced(root, levels_by_scope):
    """Gate B3: window 1 (days 1-168 of the same run) has T044's level of the lineup match, rest rate, fatigue and the final metrics for the three arms of each direction."""
    saved = json.loads((root / 'artifacts/t044/run/summary.json').read_text())['summaries']
    worst = 0.0
    for scope, by_arm in levels_by_scope.items():
        for arm, levels in by_arm.items():
            for m in LONG_METRICS:
                worst = max(worst, abs(levels[f'{m}|w1'] - saved[scope]['point'][f'level|{arm}|{m}']))
    assert worst <= 1e-12, worst
    return {'max_abs_difference': worst, 'metrics': len(LONG_METRICS), 'arms': len(LONG_ARMS)}


# ---- the runs ----
def summarise_job(job):
    scope, cell_accs, boots = job
    return scope, summarise(cell_accs, boots, effects, METRICS)


def summarise_long_job(job):
    scope, cell_groups, boots = job
    return scope, summarise_long(cell_groups, boots)


def level_of(acc, key):
    return acc[key][1] / acc[key][0]


def run_decide(out, root, boots=1000, workers=None):
    out.mkdir(parents=True, exist_ok=False)
    start = perf_counter()
    gates = {'sim_unchanged_vs_frozen_t037_bundle': check_sim_unchanged(root), 'swap_is_a_read_only_copy': check_swap_is_a_read_only_copy()}
    print('gates', json.dumps(gates), flush=True)
    refs = {t: {(c['world'], c['seed'], c['start_age']): c for c in json.loads((root / f'artifacts/{t}/run/summary.json').read_text())['cells']} for t in SETTINGS}
    tasks = [(t, name, seed, st) for t in SETTINGS for name in t023.conditions() for seed in SEEDS for st in STARTS]
    cells = {t: [] for t in SETTINGS}
    equal = {t: 0 for t in SETTINGS}
    with ProcessPoolExecutor(max_workers=workers or os.cpu_count()) as pool:
        for count, ((setting, name, seed, st), got) in enumerate(pool.map(run_cell, tasks, chunksize=1), start=1):
            cells[setting].append(got)
            legacy, ref = got[f'fwd_base{ARM_SEP}none']['flat'], refs[setting][(name, seed, st)]['on']
            assert {k[6:]: v for k, v in legacy.items() if k.startswith('final_')} == ref['final'] and legacy['events'] == ref['events'], (setting, name, seed, st)  # gate A2
            centered, plain = got[f'rev_base{ARM_SEP}none']['flat'], got['plain_centered']
            assert centered == plain['flat'] and got[f'rev_base{ARM_SEP}none']['acc'][('start', 'top2')][0] == plain['decisions'], (setting, name, seed, st)  # gate A2
            equal[setting] += 1
            if count % 12 == 0:
                print(count, len(tasks), round(perf_counter() - start), flush=True)
    gates['base_arms_equal_plain_runs'] = equal
    scopes = {'all': [c for t in SETTINGS for c in cells[t]], **cells}
    pooled = {scope: {arm: merged(c[arm]['acc'] for c in group) for arm in ARMS} for scope, group in scopes.items()}
    none_arms = {f'{d}_{p}': f'{d}_{p}{ARM_SEP}none' for d in DIRECTIONS for p in POINTS}
    gates['t037_reproduced'] = check_t037_reproduced(root, {t: {'fwd_base': pooled[t][f'fwd_base{ARM_SEP}none'], 'rev_base': pooled[t][f'rev_base{ARM_SEP}none']} for t in SETTINGS})
    gates['t044_reproduced'] = check_t044_reproduced(root, {scope: {t044_arm: {m: level_of(by_arm[arm], METRICS[m]) for m in REPRODUCED} for t044_arm, arm in none_arms.items()} for scope, by_arm in pooled.items()})
    gates['invariants_validated_runs'] = len(tasks) * len(ARMS)
    gates['shadow_checks_total'] = {key: sum(cell[arm]['checked'][key] for t in SETTINGS for cell in cells[t] for arm in ARMS) for key in cells['t030'][0][f'fwd_base{ARM_SEP}none']['checked']}
    jobs = [(scope, {arm: [c[arm]['acc'] for c in group] for arm in ARMS}, boots) for scope, group in scopes.items()]
    with ProcessPoolExecutor(max_workers=min(len(jobs), workers or os.cpu_count())) as pool:
        summaries = dict(pool.map(summarise_job, jobs))
    verdicts = {k: {v: classify_flattening(s['point'], s['ci95'], v) for v in SWAPPED} for k, s in summaries.items()}
    kn_helps = {k: {f'{d}|{v}': classify_hurt(s['ci95'], f'contrast|{d}|{v}|KN|top2') for d in DIRECTIONS for v in VARIANTS} for k, s in summaries.items()}
    result = {'task': 'T045', 'part': 'decide', 'identity': le.league_identity(), 'runtime': runtime(), 'seeds': list(SEEDS), 'days': DAYS, 'points': list(POINTS), 'variants': list(VARIANTS), 'contrasts': CONTRASTS,
              'arms': {a: {'direction': d, 'point': p, 'variant': v} for a, (d, p, v) in ARMS.items()}, 'gates': gates, 'summaries': summaries, 'verdicts': verdicts, 'kn_top2': kn_helps,
              'elapsed_seconds': perf_counter() - start}
    write_json(out / 'summary.json', result)
    print(json.dumps({'equal': equal, 'verdicts': {v: verdicts['all'][v]['verdict'] for v in SWAPPED}, 'kn_top2': kn_helps['all'], 'elapsed_seconds': result['elapsed_seconds']}), flush=True)


def run_long(out, root, boots=1000, workers=None):
    out.mkdir(parents=True, exist_ok=False)
    start = perf_counter()
    gates = {'sim_unchanged_vs_frozen_t037_bundle': check_sim_unchanged(root)}
    print('gates', json.dumps(gates), flush=True)
    tasks = [(t, name, seed, st) for t in SETTINGS for name in t023.conditions() for seed in SEEDS for st in STARTS]
    cells = {t: [] for t in SETTINGS}
    with ProcessPoolExecutor(max_workers=workers or os.cpu_count()) as pool:
        for count, ((setting, name, seed, st), got) in enumerate(pool.map(run_long_cell, tasks, chunksize=1), start=1):
            cells[setting].append(got)
            for direction in DIRECTIONS:
                assert got[f'{direction}_base']['flat'] == got[f'plain_{direction}']['flat'], (setting, name, seed, st, direction)  # gate B2
            if count % 12 == 0:
                print(count, len(tasks), round(perf_counter() - start), flush=True)
    gates['base_arms_equal_plain_runs'] = len(tasks) * len(DIRECTIONS)
    scopes = {'all': [c for t in SETTINGS for c in cells[t]], **cells}
    jobs = [(scope, {arm: [grouped(c[arm]['windows']) for c in group] for arm in LONG_ARMS}, boots) for scope, group in scopes.items()]
    window_one = {}
    for scope, cell_groups, _ in jobs:
        window_one[scope] = {arm: {f'{m}|w1': level_of(merged(g['w1'] for g in groups), key) for m, key in LONG_METRICS.items()} for arm, groups in cell_groups.items()}
    gates['window_one_equals_t044'] = check_window_one_reproduced(root, window_one)
    gates['invariants_validated_runs'] = len(tasks) * len(LONG_ARMS)
    with ProcessPoolExecutor(max_workers=min(len(jobs), workers or os.cpu_count())) as pool:
        summaries = dict(pool.map(summarise_long_job, jobs))
    costs = {k: late_cost(s['ci95']) for k, s in summaries.items()}
    result = {'task': 'T045', 'part': 'long', 'identity': le.league_identity(), 'runtime': runtime(), 'seeds': list(SEEDS), 'days': LONG_DAYS, 'window': WINDOW, 'groups': list(GROUPS), 'contrasts': LONG_CONTRASTS,
              'arms': {a: {'direction': d, 'point': p} for a, (d, p) in LONG_ARMS.items()}, 'gates': gates, 'summaries': summaries, 'late_cost': costs, 'elapsed_seconds': perf_counter() - start}
    write_json(out / 'summary.json', result)
    print(json.dumps({'late_cost': costs['all'], 'elapsed_seconds': result['elapsed_seconds']}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--part', choices=('decide', 'long'), required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=None)
    arguments = parser.parse_args()
    {'decide': run_decide, 'long': run_long}[arguments.part](arguments.out.resolve(), CODE_ROOT, workers=arguments.workers)
