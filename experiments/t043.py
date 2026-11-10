"""T043: is the gain carried by the LEVEL or the SPREAD of the players' expected gains, which terms of the coach's scores make the rest rate move the lineup error, and what do the lineup match, the skill and the fatigue look like along a sweep of the rest rate?
Analysis only: `soccer_sim/` is unchanged and no config is added. Only the two expected gains (A, B) that the practice choice reads are shifted, at the moment of the decision, and put back afterwards.
Arms (per direction): base; g (the `alt` gains, T042); L (real gains + c: the level of the other mode); S (alt gains - c: the other mode's spread around this world's level); o(delta) (real gains + delta, the sweep).
Forward arms: legacy world, centered_v1 as `alt`. Reverse arms: centered_v1 world, legacy as `alt`. True skills are read by the researcher after the fact; nothing goes back into the simulation.

Protocol: docs/experiments/T043-PROTOCOL.md. Cells: 14 worlds x seeds {100,101,102} x start age {29,33}, 168 days, three settings x 26 arms (+ one plain centered_v1 run per cell for the gates).
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

CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT))
from experiments import t023, t030, t036, t038, t040, t041, t042
from soccer_sim import league_economy as le
from soccer_sim.storage import runtime, write_json

DAYS, SEEDS, STARTS, SETTINGS = t036.DAYS, t036.SEEDS, t036.STARTS, t036.SETTINGS
DIRECTIONS = t040.DIRECTIONS
# the mean of (alt - real) expected gains over all practice decisions of the T042 base arms (registered; read back from the T042 summary and compared in gate 7)
LEVEL_CONSTANT = {'fwd': 0.005557207419209873, 'rev': -0.005407013834974597}
SWEEP = (-0.030, -0.020, -0.010, -0.005, 0.005, 0.0075, 0.010, 0.020, 0.030)
SWAPS = ('g', 'L', 'S')
SWEEP_ARMS = tuple(f'o{delta:+.4f}' for delta in SWEEP)
ARM_X = SWAPS + SWEEP_ARMS
SHAPES = {'base': ('plain', None), 'g': ('swap', None), 'L': ('level', None), 'S': ('spread', None), **{name: ('offset', delta) for name, delta in zip(SWEEP_ARMS, SWEEP)}}
ARMS = {f'{d}_{x}': (d, *shape) for d in DIRECTIONS for x, shape in SHAPES.items()}
TRACKED_KINDS = ('swap', 'spread')  # need the `alt` column
PARTS = ('sel', 'skill', 'match', 'fatigue', 'round')
ACTIONS = ('A', 'B', 'rest')
CURVE_METRICS = ('top2', 'final_skill', 'fatigue', 'final_trust', 'final_satisfaction')
OPTIMUM_METRICS = ('top2', 'final_skill')
COLLAPSE_METRICS = ('top2', 'final_skill')
COVER_LOW, COVER_HIGH = 0.45, 0.80
# metric id -> accumulator key; 'top2' is the primary one
METRICS = {'top2': ('start', 'top2'), **{f'promo_{k}': ('promo', k) for k in t040.PROMO}, **{f'final_{k}': ('final', k) for k in t040.FINALS},
           **{f'choice_{k}': ('choice', k) for k in t040.CHOICES}, **{f'act_{a}': ('act', a) for a in ACTIONS}, 'follow': ('follow', 'all'), 'fatigue': ('fat', 'mean'),
           'med_gap': ('med', 'gap'), 'med_err': ('med', 'err'), 'med_signal': ('med', 'signal'), **{f'med_{p}': ('med', p) for p in PARTS},
           'gain_real': ('gl', 'real'), 'gain_alt': ('gl', 'alt'), 'ab_real': ('ab', 'real'), 'ab_alt': ('ab', 'alt'), 'shift_gain': ('shift', 'gain'), 'shiftabs_gain': ('shiftabs', 'gain')}
MED_COLUMNS = (('act', 'rest'), ('med', 'gap'), ('med', 'err'), ('start', 'top2')) + tuple(('med', p) for p in PARTS)  # rest, gap, err, the lineup match, the five parts of err
MED_N = len(MED_COLUMNS)
MED_GROUPS = ('all', 'sweep', 'swap', 'fwd', 'rev')
MED_STATS = t042.MED_STATS + tuple(f'{kind}_{p}' for p in PARTS for kind in ('a', 'ind', 'share'))
SEED_BOOT = 41
RESIDUAL_TOLERANCE = 1e-9
add, merge, interval, stat = t036.add, t036.merge, t038.interval, t036.stat
sub, merged, resampled_levels = t040.sub, t040.merged, t040.resampled_levels
excludes_zero = t042.excludes_zero


def mean_of(values):
    values = list(values)
    return sum(values) / len(values)


# ---- the shifted expected gains of the practice choice ----
def executed_gains(kind, param, real, alt, level):
    """The expected gains {method: gain} that the practice choice reads. real: the simulation's own; alt: the other mode's reconstruction (None for untracked arms)."""
    if kind == 'swap':
        return dict(alt)
    if kind == 'level':
        return {m: value + level for m, value in real.items()}
    if kind == 'spread':
        return {m: value - level for m, value in alt.items()}
    if kind == 'offset' and param:  # a zero offset is no shift (adding 0.0 would turn the recorded -0.0 into 0.0)
        return {m: value + param for m, value in real.items()}
    return dict(real)


def record_gains(acc, real, alt):
    """The researcher's description of one decision: mean expected gain (real / alt), |A - B| (real / alt) and alt - real."""
    mean_real, mean_alt = mean_of(real.values()), mean_of(alt.values())
    add(acc, ('gl', 'real'), mean_real)
    add(acc, ('gl', 'alt'), mean_alt)
    add(acc, ('ab', 'real'), abs(real['A'] - real['B']))
    add(acc, ('ab', 'alt'), abs(alt['A'] - alt['B']))
    add(acc, ('shift', 'gain'), mean_alt - mean_real)
    add(acc, ('shiftabs', 'gain'), abs(mean_alt - mean_real))


@contextmanager
def installed(tracker, kind, param, level, acc):
    """Tracked arms: the T041 recorders (noise, fatigue, events, day-start skills, new players) with the advice response and the transfer left plain. Every arm but `base`: the practice choice reads the
    shifted gains and the real ones are put back afterwards, also after an exception."""
    plain_training = le.decide_player_training
    plain_others = {n: getattr(le, n) for n in ('_player_advice_response', 'decide_transfer_bidding')}

    def practice(**kw):
        mind = kw['mind']
        alt = None
        if tracker:
            tracker.flush(mind.player_id)
            alt = tracker.cols[mind.player_id]['alt']['gains']
            record_gains(acc, mind.expected_gains, alt)
        real = dict(mind.expected_gains)
        mind.expected_gains.update(executed_gains(kind, param, real, alt, level))
        try:
            return plain_training(**kw)
        finally:
            mind.expected_gains.update(real)

    with ExitStack() as stack:
        if tracker:
            stack.enter_context(t041.installed(tracker, ()))
            for name, function in plain_others.items():
                setattr(le, name, function)
        if kind != 'plain':
            stack.callback(setattr, le, 'decide_player_training', plain_training)
            le.decide_player_training = practice
        yield


# ---- what the researcher records (true skills; nothing goes back) ----
def decompose(ranking, truth, config):
    """One lineup decision (>= 3 players): gap, signal, err and the five parts of err. j = the true top-two member with the lowest score, k = the other player with the highest score.
    sel + skill + match + fatigue + round = err exactly (checked)."""
    skill_weight, match_weight = config.select_skill_weight, config.select_match_weight
    scale = skill_weight + match_weight
    true_set = t038.top2(list(truth.items()))
    j = min((r for r in ranking if r['player_id'] in true_set), key=lambda r: r['score'])
    k = max((r for r in ranking if r['player_id'] not in true_set), key=lambda r: r['score'])
    signal = j['score'] - k['score']
    ordered = sorted(truth.values(), reverse=True)
    gap = ordered[1] - ordered[2]
    true_diff = truth[j['player_id']] - truth[k['player_id']]
    diff = {term: j['components'][term] - k['components'][term] for term in ('skill_term', 'match_term', 'fatigue_penalty')}
    parts = {'sel': scale * (gap - true_diff), 'skill': skill_weight * true_diff - diff['skill_term'], 'match': match_weight * true_diff - diff['match_term'],
             'fatigue': -diff['fatigue_penalty'], 'round': -(signal - sum(diff.values()))}
    err = scale * gap - signal
    assert abs(sum(parts.values()) - err) <= RESIDUAL_TOLERANCE, (parts, err)
    return {'true_set': true_set, 'gap': gap, 'signal': signal, 'err': err, 'parts': parts}


def record_mediators(acc, config, events, truth_of):
    for e in events:
        ranking = e['data']['ranking'] if e['kind'] == 'lineup_decision' else []
        if len(ranking) < 3:
            continue
        d = decompose(ranking, {r['player_id']: truth_of(r['player_id']) for r in ranking}, config)
        add(acc, ('med', 'gap'), d['gap'])
        add(acc, ('med', 'err'), d['err'])
        add(acc, ('med', 'signal'), d['signal'])
        add(acc, ('med', 'zero'), float(d['signal'] == 0))
        add(acc, ('med', 'mismatch'), float(d['signal'] != 0 and (d['signal'] > 0) != (set(e['data']['starters']) == d['true_set'])))
        for p in PARTS:
            add(acc, ('med', p), d['parts'][p])


def record_practice(acc, events):
    for e in events:
        if e['kind'] == 'player_training_decision':
            for action in ACTIONS:
                add(acc, ('act', action), float(e['data']['chosen']['action'] == action))


# ---- one run ----
def play(config, seed, view_mode, spec, level, acc, days=DAYS, observer=None):
    _, kind, param = spec
    world = le._build_initial_world(seed=seed, config=config)
    tracker = t041.PlayerTracker(world, view_mode, acc) if kind in TRACKED_KINDS else None
    with installed(tracker, kind, param, level, acc):
        while world.day < days:
            n_events = len(world.events)
            le.advance_league_days(world, 1)
            if tracker:
                tracker.end_of_day(world)
            if observer:
                observer(world, world.events[n_events:])
    assert tracker is None or not (tracker.noise or tracker.fatigue), (len(tracker.noise), len(tracker.fatigue))  # every recorded draw was matched to an event
    return world, tracker


def run_arm(setting, name, seed, start, spec, days=DAYS, identity=False):
    """identity: the view is the world's own mode and the level constant 0, so the shift must change nothing."""
    direction = spec[0]
    world_mode, view_mode = DIRECTIONS[direction]
    config = t036.make_cfg(setting, name, start, world_mode)
    acc, prev = {}, {}

    def observer(world, events):
        truth = {}
        truth_of = lambda pid: truth.setdefault(pid, world.players[pid].composite_skill())
        t040.record_day(acc, events, truth_of, prev)
        t041.record_events(acc, events)
        record_practice(acc, events)
        record_mediators(acc, world.config, events, truth_of)
        add(acc, ('fat', 'mean'), mean_of(world.players[pid].fatigue for pid in le._active_pids(world)))

    world, tracker = play(config, seed, world_mode if identity else view_mode, spec, 0.0 if identity else LEVEL_CONSTANT[direction], acc, days, observer)
    le._validate_league_invariants(world)
    flat = {f'final_{k}': v for k, v in t030.final_metrics(world).items()}
    flat['events'] = len(world.events)
    for key in t040.FINALS:
        add(acc, ('final', key), flat[f'final_{key}'])
    return {'acc': acc, 'flat': flat, 'checked': tracker.checked if tracker else {}, 'world': world}


def run_cell(task):
    setting, name, seed, start = task
    out = {arm: {key: value for key, value in run_arm(setting, name, seed, start, spec).items() if key != 'world'} for arm, spec in ARMS.items()}
    plain = le.run_league_world(seed=seed, days=DAYS, config=t036.make_cfg(setting, name, start, 'centered_v1'))
    out['plain_centered'] = {'flat': {**{f'final_{k}': v for k, v in t030.final_metrics(plain).items()}, 'events': len(plain.events)},
                             'decisions': sum(1 for e in plain.events if e['kind'] == 'lineup_decision')}
    out['day_one'] = t041.day_one_deviation(setting, name, seed, start)
    return task, out


# ---- gates ----
check_sim_unchanged, plain_digest, check_t037_reproduced = t040.check_sim_unchanged, t040.plain_digest, t040.check_t037_reproduced


def check_identity(days=DAYS):
    """Gate 6: with the view and the level constant neutral, the spread arm (the tracked path) and a sweep arm with delta 0 (the untracked path) reproduce the plain world's whole snapshot."""
    names, count = t023.conditions(), 0
    for setting, index, seed, start in t040.IDENTITY_PICKS:
        for direction, (world_mode, _) in DIRECTIONS.items():
            plain = le.run_league_world(seed=seed, days=days, config=t036.make_cfg(setting, names[index], start, world_mode))
            for spec, neutral in (((direction, 'spread', None), True), ((direction, 'offset', 0.0), False)):
                result = run_arm(setting, names[index], seed, start, spec, days=days, identity=neutral)
                assert plain_digest(result['world']) == plain_digest(plain), (setting, names[index], seed, start, spec)
                count += 1
    return {'runs': count, 'all_equal': True}


def check_level_constants(root):
    saved = json.loads((root / 'artifacts/t042/run/summary.json').read_text())['summaries']['all']['point']
    got = {d: saved[f'level|{d}_base|shift_gain'] for d in DIRECTIONS}
    assert all(abs(got[d] - LEVEL_CONSTANT[d]) <= 1e-15 for d in DIRECTIONS), got
    return got


def check_t042_reproduced(root, pooled_by_scope):
    """Gate 3: base and g (the arms T042 also ran) give the same lineup-match levels."""
    saved = json.loads((root / 'artifacts/t042/run/summary.json').read_text())['summaries']
    worst = 0.0
    for scope, pools in pooled_by_scope.items():
        for direction in DIRECTIONS:
            for arm in ('base', 'g'):
                n, total = pools[f'{direction}_{arm}'][('start', 'top2')][:2]
                worst = max(worst, abs(total / n - saved[scope]['point'][f'level|{direction}_{arm}|top2']))
    assert worst <= 1e-12, worst
    return {'max_abs_difference': worst}


# ---- the sweep: curves, optimum, plateau, collapse ----
def ols_slope(xs, ys):
    mx, my = mean_of(xs), mean_of(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx if sxx else None


def curve_arms(direction):
    """The ten points of one direction, ordered by delta: (arm, delta)."""
    return sorted([(f'{direction}_base', 0.0)] + [(f'{direction}_{name}', delta) for name, delta in zip(SWEEP_ARMS, SWEEP)], key=lambda point: point[1])


def interpolate(xs, ys, x):
    """Linear interpolation of the curve (xs ascending) at x; None outside the range."""
    if not xs or x < xs[0] or x > xs[-1]:
        return None
    for (x0, y0), (x1, y1) in zip(zip(xs, ys), zip(xs[1:], ys[1:])):
        if x0 <= x <= x1:
            return y0 if x1 == x0 else y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return ys[-1]


def collapse(fwd_points, rev_points):
    """Mean over the forward points whose rest rate lies in the reverse curve's range of (forward value - reverse curve at the same rest rate). points: [(rest, value)]."""
    ordered = sorted(rev_points)
    xs, ys = [p[0] for p in ordered], [p[1] for p in ordered]
    diffs = [value - other for rest, value in fwd_points for other in [interpolate(xs, ys, rest)] if other is not None]
    return mean_of(diffs) if diffs else None


def argmax_refs(v):
    """The arm with the highest level of each optimum metric in each direction (ties: the one with the lowest delta)."""
    return {(d, m): max(curve_arms(d), key=lambda point: v[point[0]][m])[0] for d in DIRECTIONS for m in OPTIMUM_METRICS}


def effects(v, refs=None):
    """v[arm][metric] = level. D, F (sufficiency) and R (necessity) of the three gain swaps, the within-direction change of every arm, the level/spread shares, the sweep curves' slopes, optimum, plateau and collapse."""
    refs = refs or argmax_refs(v)
    out = {f'level|{arm}|{m}': v[arm][m] for arm in ARMS for m in METRICS}
    for m in METRICS:
        out[f'D|{m}'] = sub(v['rev_base'][m], v['fwd_base'][m])
        for d in DIRECTIONS:
            for x in ARM_X:
                out[f'd|{d}|{x}|{m}'] = sub(v[f'{d}_{x}'][m], v[f'{d}_base'][m])
        for x in SWAPS:
            out[f'F|{x}|{m}'] = sub(v[f'fwd_{x}'][m], v['fwd_base'][m])
            out[f'R|{x}|{m}'] = sub(v['rev_base'][m], v[f'rev_{x}'][m])
    d_top2 = out['D|top2']
    ratio = lambda value, base: None if not base or value is None else value / base
    for side in ('F', 'R'):
        for x in SWAPS:
            out[f'{side.lower()}|{x}'] = ratio(out[f'{side}|{x}|top2'], d_top2)
            out[f'share{side}|{x}'] = ratio(out[f'{side}|{x}|top2'], out[f'{side}|g|top2'])
        parts = [out[f'{side}|{x}|top2'] for x in SWAPS]
        out[f'I{side}|top2'] = None if None in parts else parts[0] - parts[1] - parts[2]
        out[f'i{side}'] = ratio(out[f'I{side}|top2'], out[f'{side}|g|top2'])
    for d in DIRECTIONS:
        points = curve_arms(d)
        rests = [v[arm]['act_rest'] for arm, _ in points]
        for m in CURVE_METRICS:
            out[f'slope|{d}|{m}'] = ols_slope(rests, [v[arm][m] for arm, _ in points])
        for m in OPTIMUM_METRICS:
            best = max(points, key=lambda point: v[point[0]][m])
            out[f'amaxrest|{d}|{m}'] = v[best[0]]['act_rest']
            out[f'amaxdelta|{d}|{m}'] = best[1]
            for arm, _ in points:
                out[f'amax|{d}|{m}|{arm}'] = float(arm == best[0])
                out[f'plateau|{d}|{m}|{arm}'] = v[refs[(d, m)]][m] - v[arm][m]
    for m in COLLAPSE_METRICS:
        fwd = [(v[arm]['act_rest'], v[arm][m]) for arm, _ in curve_arms('fwd')]
        rev = [(v[arm]['act_rest'], v[arm][m]) for arm, _ in curve_arms('rev')]
        out[f'collapse|{m}'] = collapse(fwd, rev)
    return out


# ---- mediation: rest -> (gap, err, the parts of err) -> the lineup match ----
def cell_levels(cell_accs):
    """cell_accs[arm] = list of per-cell accumulators -> {arm: {metric: (n list, sum list)}} for fast resampling."""
    return {arm: {m: ([a[key][0] if key in a else 0 for a in accs], [a[key][1] if key in a else 0.0 for a in accs]) for m, key in METRICS.items()} for arm, accs in cell_accs.items()}


def unit_values(acc):
    return tuple(acc[key][1] / acc[key][0] for key in MED_COLUMNS)


def cell_matrices(cell):
    """Sufficient statistics of one cell: for each group the sum of z z^T (MED_N x MED_N, flattened) over the arms' differences z from the base arm of the same direction."""
    sums = {g: [0.0] * MED_N ** 2 for g in MED_GROUPS}
    for direction in DIRECTIONS:
        base = unit_values(cell[f'{direction}_base']['acc'])
        for x in ARM_X:
            z = [a - b for a, b in zip(unit_values(cell[f'{direction}_{x}']['acc']), base)]
            for g in ('all', direction, 'sweep' if SHAPES[x][0] == 'offset' else 'swap'):
                for i in range(MED_N):
                    for j in range(MED_N):
                        sums[g][MED_N * i + j] += z[i] * z[j]
    return sums


def mediation(s):
    """s: flattened sum of z z^T over MED_COLUMNS. The first four columns give T042's fit (rest, gap, err, lineup match); the five parts of err give a_part (the part on rest), ind_part = a_part * b_err and share_part = a_part / a_err."""
    first = [s[MED_N * i + j] for i in range(4) for j in range(4)]
    out = {**t042.mediation(first), **dict.fromkeys(MED_STATS[len(t042.MED_STATS):])}
    if not first[0]:
        return out
    for k, p in enumerate(PARTS):
        a_part = s[4 + k] / s[0]
        out[f'a_{p}'] = a_part
        out[f'ind_{p}'] = None if out['b_err'] is None else a_part * out['b_err']
        out[f'share_{p}'] = None if not out['a_err'] else a_part / out['a_err']
    return out


def summarise(cell_accs, cell_mats, boots, seed=SEED_BOOT):
    """One resample of the cells is shared by every arm, statistic, curve and mediation fit."""
    vectors = cell_levels(cell_accs)
    n_cells = len(cell_mats)
    resampled_mats = lambda counts: {g: [sum(c * m[g][i] for c, m in zip(counts, cell_mats)) for i in range(MED_N ** 2)] for g in MED_GROUPS}
    point_levels = resampled_levels(vectors, [1] * n_cells)
    refs = argmax_refs(point_levels)
    point, med_point = effects(point_levels, refs), {g: mediation(s) for g, s in resampled_mats([1] * n_cells).items()}
    rng, draws, med_draws = random.Random(seed), [], []
    for _ in range(boots):
        counts = [0] * n_cells
        for _ in range(n_cells):
            counts[rng.randrange(n_cells)] += 1
        draws.append(effects(resampled_levels(vectors, counts), refs))
        med_draws.append({g: mediation(s) for g, s in resampled_mats(counts).items()})
    frequency = {k: mean_of(d[k] for d in draws) for k in point if k.startswith('amax|')}
    return {'cells': n_cells, 'point': point, 'ci95': {k: interval([d[k] for d in draws]) for k in point if k not in frequency}, 'argmax_frequency': frequency,
            'argmax_reference': {f'{d}|{m}': arm for (d, m), arm in refs.items()},
            'mediation': {g: {'point': med_point[g], 'ci95': {k: interval([d[g][k] for d in med_draws]) for k in MED_STATS}} for g in MED_GROUPS}}


# ---- the pre-registered classifications ----
def classify_level_spread(point, ci):
    """(a) The attribution of the gain swap to the level (L) or the spread (S) of the expected gains, first match wins, plus the flags."""
    significant = lambda key: ci[key] is not None and ci[key][0] > 0
    if not (significant('F|g|top2') and significant('R|g|top2')):
        return {'class': 'no_gain_effect', 'carriers': [], 'main_routes': [], 'flags': []}
    carriers = [x for x in ('L', 'S') if significant(f'F|{x}|top2') and significant(f'R|{x}|top2')]
    main = [x for x in carriers if point[f'f|{x}'] >= 0.5 and point[f'r|{x}'] >= 0.5]
    flags = []
    if any(abs(point[f'i{s}']) >= 0.25 and excludes_zero(ci[f'I{s}|top2']) for s in ('F', 'R')):
        flags.append('non_additive')
    flags += [f'opposed({x})' for x in ('L', 'S') if any(ci[f'{s}|{x}|top2'][1] < 0 for s in ('F', 'R'))]
    labels = {('L',): 'level', ('S',): 'spread', ('L', 'S'): 'level_and_spread'}
    if main:
        label = labels[tuple(main)]
    elif carriers:
        label = f"partial({', '.join(carriers)})"
    else:
        label = 'only_jointly'
    return {'class': label, 'carriers': carriers, 'main_routes': main, 'flags': flags}


def classify_err_source(med):
    """(b) Which part of err carries the rest -> lineup-match path: eligible = indirect effect significant with the sign of the total; main = eligible with share >= 0.5."""
    point, ci = med['point'], med['ci95']
    if not excludes_zero(ci['c']):
        return 'no_total'
    eligible = [p for p in PARTS if excludes_zero(ci[f'ind_{p}']) and (point[f'ind_{p}'] > 0) == (point['c'] > 0)]
    main = [p for p in eligible if point[f'share_{p}'] is not None and point[f'share_{p}'] >= 0.5]
    if len(main) == 1:
        return f'single({main[0]})'
    if eligible and not main:
        return f"distributed({', '.join(eligible)})" if len(eligible) > 1 else f'partial({eligible[0]})'
    return 'none' if not eligible else f"multiple({', '.join(main)})"


def curve_report(point, ci, direction, metric):
    """(c) One direction's optimum of one metric: the argmax arm, its shape (boundary_low / boundary_high / interior) and the plateau (arms not distinguishable from the argmax arm)."""
    arms = curve_arms(direction)
    best = max(arms, key=lambda a: point[f'level|{a[0]}|{metric}'])
    rests = {arm: point[f'level|{arm}|act_rest'] for arm, _ in arms}
    shape = 'boundary_low' if rests[best[0]] == min(rests.values()) else ('boundary_high' if rests[best[0]] == max(rests.values()) else 'interior')
    plateau = [arm for arm, _ in arms if ci[f'plateau|{direction}|{metric}|{arm}'][0] <= 0 <= ci[f'plateau|{direction}|{metric}|{arm}'][1]]
    return {'argmax': best[0], 'delta': best[1], 'rest': rests[best[0]], 'shape': shape, 'plateau': plateau, 'base_in_plateau': f'{direction}_base' in plateau}


def covers(point):
    out = {}
    for d in DIRECTIONS:
        rests = [point[f'level|{arm}|act_rest'] for arm, _ in curve_arms(d)]
        out[d] = {'min_rest': min(rests), 'max_rest': max(rests), 'monotone_in_delta': all(a > b for a, b in zip(rests, rests[1:])),
                  'covers': min(rests) <= COVER_LOW and max(rests) >= COVER_HIGH}
    return out


# ---- the run ----
def summarise_job(job):
    scope, cell_accs, cell_mats, boots = job
    return scope, summarise(cell_accs, cell_mats, boots)


def run(out, root, boots=1000, workers=None):
    out.mkdir(parents=True, exist_ok=False)
    start = perf_counter()
    gates = {'sim_unchanged_vs_frozen_t037_bundle': check_sim_unchanged(root), 'level_constants_from_t042': check_level_constants(root)}
    gates['identity_view'] = check_identity()
    print('gates', json.dumps(gates), flush=True)
    refs = {t: {(c['world'], c['seed'], c['start_age']): c for c in json.loads((root / f'artifacts/{t}/run/summary.json').read_text())['cells']} for t in SETTINGS}
    tasks = [(t, name, seed, st) for t in SETTINGS for name in t023.conditions() for seed in SEEDS for st in STARTS]
    cells = {t: [] for t in SETTINGS}
    equal, med_mismatch, med_zero, worst_day_one = {t: 0 for t in SETTINGS}, 0.0, 0.0, 0.0
    with ProcessPoolExecutor(max_workers=workers or os.cpu_count()) as pool:
        for count, ((setting, name, seed, st), got) in enumerate(pool.map(run_cell, tasks, chunksize=1), start=1):
            cells[setting].append(got)
            legacy, ref = got['fwd_base']['flat'], refs[setting][(name, seed, st)]['on']
            assert {k[6:]: v for k, v in legacy.items() if k.startswith('final_')} == ref['final'] and legacy['events'] == ref['events'], (setting, name, seed, st)  # gate 2
            centered, plain = got['rev_base']['flat'], got['plain_centered']
            assert centered == plain['flat'] and got['rev_base']['acc'][('start', 'top2')][0] == plain['decisions'], (setting, name, seed, st)  # gate 2
            equal[setting] += 1
            med_mismatch += sum(got[arm]['acc'][('med', 'mismatch')][1] for arm in ARMS)
            med_zero += sum(got[arm]['acc'][('med', 'zero')][1] for arm in ARMS)
            worst_day_one = max(worst_day_one, got['day_one'])
            if count % 12 == 0:
                print(count, len(tasks), round(perf_counter() - start), flush=True)
    assert worst_day_one <= t040.DAY_ONE_TOLERANCE and med_mismatch == 0, (worst_day_one, med_mismatch)
    gates['base_arms_equal_plain_runs'] = equal
    gates['day_one_alt_equals_other_mode_max_deviation'] = worst_day_one
    gates['lineup_signal_sign_mismatches'] = med_mismatch
    gates['lineup_signal_zero_count_report'] = med_zero
    gates['err_parts_sum_to_err_tolerance'] = RESIDUAL_TOLERANCE
    scopes = {'all': [c for t in SETTINGS for c in cells[t]], **cells}
    pooled = {scope: {arm: merged(c[arm]['acc'] for c in group) for arm in ARMS} for scope, group in scopes.items()}
    gates['t037_reproduced'] = check_t037_reproduced(root, {t: {arm: pooled[t][arm] for arm in ('fwd_base', 'rev_base')} for t in SETTINGS})
    gates['t042_reproduced'] = check_t042_reproduced(root, pooled)
    gates['invariants_validated_runs'] = len(tasks) * len(ARMS)
    jobs = [(scope, {arm: [c[arm]['acc'] for c in group] for arm in ARMS}, [cell_matrices(c) for c in group], boots) for scope, group in scopes.items()]
    with ProcessPoolExecutor(max_workers=min(len(jobs), workers or os.cpu_count())) as pool:
        summaries = dict(pool.map(summarise_job, jobs))
    t040_d = json.loads((root / 'artifacts/t040/run/summary.json').read_text())['summaries']['all']['point']['D|top2']
    gates['d_equals_t040'] = abs(summaries['all']['point']['D|top2'] - t040_d) <= 1e-12
    assert gates['d_equals_t040']
    classes = {k: classify_level_spread(s['point'], s['ci95']) for k, s in summaries.items()}
    err_source = {k: {g: classify_err_source(s['mediation'][g]) for g in MED_GROUPS} for k, s in summaries.items()}
    mediation_class = {k: {g: t042.classify_mediation(s['mediation'][g]) for g in MED_GROUPS} for k, s in summaries.items()}
    optimum = {k: {f'{d}|{m}': curve_report(s['point'], s['ci95'], d, m) for d in DIRECTIONS for m in OPTIMUM_METRICS} for k, s in summaries.items()}
    descriptive = {arm: {f'{key[0]}|{key[1]}': stat(pooled['all'][arm].get(key)) for key in (('gl', 'real'), ('gl', 'alt'), ('ab', 'real'), ('ab', 'alt'), ('shift', 'gain'), ('shiftabs', 'gain'))}
                   for arm in ARMS if SHAPES[arm.split('_', 1)[1]][0] in TRACKED_KINDS}
    result = {'task': 'T043', 'identity': le.league_identity(), 'runtime': runtime(), 'seeds': list(SEEDS), 'days': DAYS, 'level_constant': LEVEL_CONSTANT, 'sweep': list(SWEEP),
              'arms': {a: {'direction': d, 'kind': kind, 'parameter': param} for a, (d, kind, param) in ARMS.items()},
              'gates': gates, 'summaries': summaries, 'level_or_spread': classes, 'err_source': err_source, 'mediation_class': mediation_class, 'optimum': optimum,
              'coverage': {k: covers(s['point']) for k, s in summaries.items()}, 'descriptive': descriptive,
              'checked_totals': {arm: {k: sum(cell[arm]['checked'].get(k, 0) for t in SETTINGS for cell in cells[t]) for k in ('self', 'match_fatigue', 'feedback', 'days')} for arm in ARMS},
              'elapsed_seconds': perf_counter() - start}
    write_json(out / 'summary.json', result)
    print(json.dumps({'equal': equal, 'level_or_spread': classes['all'], 'err_source': err_source['all'], 'mediation': mediation_class['all'], 'coverage': result['coverage']['all'],
                      'optimum': optimum['all'], 'elapsed_seconds': result['elapsed_seconds']}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=None)
    arguments = parser.parse_args()
    run(arguments.out.resolve(), CODE_ROOT, workers=arguments.workers)
