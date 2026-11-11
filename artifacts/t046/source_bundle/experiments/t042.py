"""T042: which of the three states that the players' practice choice reads carries the gain, how does the rest rate reach the starting-lineup match, and is the harm of the advice response an interaction with the practice choice?
Analysis only: `soccer_sim/` is unchanged and no config is added. The players' two state columns (`real`, `alt`) are those of T041 (`t041.PlayerTracker`).
The practice decision is evaluated on the plain state and on the `alt` value of each subset of {g: expected gains, f: perceived fatigue, t: trust} (2^3 arms) and the advice response optionally on the `alt` trust (R, tR, gftR).
Forward arms: legacy world, centered_v1 state in the decision. Reverse arms: centered_v1 world, legacy state. True skills are read by the researcher after the fact; nothing goes back into the simulation.

Protocol: docs/experiments/T042-PROTOCOL.md. Cells: 14 worlds x seeds {100,101,102} x start age {29,33}, 168 days, three settings x 22 arms (+ one plain centered_v1 run per cell for the gates).
"""
import argparse
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
from itertools import combinations
import json
import os
from pathlib import Path
import random
import sys
from time import perf_counter

CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT))
from experiments import t023, t030, t036, t038, t040, t041
from soccer_sim import league_economy as le
from soccer_sim.storage import runtime, write_json

DAYS, SEEDS, STARTS, SETTINGS = t036.DAYS, t036.SEEDS, t036.STARTS, t036.SETTINGS
DIRECTIONS = t040.DIRECTIONS
VARS = ('g', 'f', 't')
LONG = {'g': 'gain', 'f': 'fatigue', 't': 'trust'}
STATE_PART = {'g': 'gains', 'f': 'fatigue', 't': 'trust', 's': 'self'}
# arm key -> (the subset of the practice states read from the `alt` column, whether the advice response reads it)
SHAPES = {'base': ('', False), 'g': ('g', False), 'f': ('f', False), 't': ('t', False), 'gf': ('gf', False), 'gt': ('gt', False), 'ft': ('ft', False), 'gft': ('gft', False),
          'R': ('', True), 'tR': ('t', True), 'gftR': ('gft', True)}
ARM_X = tuple(x for x in SHAPES if x != 'base')
ARMS = {f'{d}_{x}': (d,) + shape for d in DIRECTIONS for x, shape in SHAPES.items()}
RECORDED_SETS = ('g', 'f', 't', 'gft', 's')  # evaluated in every arm, whatever the arm executes
FLIP_LABELS = tuple(f'P{k}' for k in RECORDED_SETS) + ('R',)
TRANS_LABELS = ('Pg', 'Pf', 'Pt', 'Pgft', 'R')
ACTS = {'practice': t041.ACTS['practice'], 'response': t041.ACTS['response']}
REASONS = {'practice': t041.REASONS['practice'], 'response': t041.REASONS['response']}
SHIFT_VARS = ('gain', 'fatigue', 'trust')
# metric id -> accumulator key; 'top2' is the primary one
METRICS = {'top2': ('start', 'top2'), **{f'promo_{k}': ('promo', k) for k in t040.PROMO}, **{f'final_{k}': ('final', k) for k in t040.FINALS},
           **{f'choice_{k}': ('choice', k) for k in t040.CHOICES},
           **{f'flip_{c}': ('flip', c) for c in FLIP_LABELS}, **{f'reasonchg_{c}': ('reasonchg', c) for c in FLIP_LABELS},
           **{f'act_{c}_{a}': ('act', c, a) for c in ACTS for a in ACTS[c]}, **{f'rsn_{c}_{r}': ('rsn', c, r) for c in REASONS for r in REASONS[c]},
           **{f'stated_{r}': ('stated', r) for r in le.BELIEF_REASONS}, 'follow': ('follow', 'all'),
           'override_rest': ('override', 'rest'), 'override_method': ('override', 'method'),
           **{f'shift_{v}': ('shift', v) for v in SHIFT_VARS}, **{f'shiftabs_{v}': ('shiftabs', v) for v in SHIFT_VARS},
           'med_gap': ('med', 'gap'), 'med_err': ('med', 'err'), 'med_signal': ('med', 'signal')}
MED_COLUMNS = (('act', 'practice', 'rest'), ('med', 'gap'), ('med', 'err'), ('start', 'top2'))  # rest, gap, err, the lineup match
MED_GROUPS = ('all', 'practice_only', 'fwd', 'rev')
MED_STATS = ('c', 'a_gap', 'a_err', 'c_direct', 'b_gap', 'b_err', 'ind_gap', 'ind_err', 'prop')
SEED_BOOT = 41
add, merge, interval = t036.add, t036.merge, t038.interval
sub, merged, resampled_levels = t040.sub, t040.merged, t040.resampled_levels


# ---- the view switch of subsets of the player state, and the researcher's records of the decisions ----
def put_part(mind, state, key):
    if key == 'g':
        mind.expected_gains.clear()
        mind.expected_gains.update(state['gains'])
    elif key == 'f':
        mind.perceived_fatigue = state['fatigue']
    elif key == 't':
        mind.trust_by_coach.clear()
        mind.trust_by_coach.update(state['trust'])
    else:
        mind.self_estimate = state['self']


def mean_of(values):
    values = list(values)
    return sum(values) / len(values)


class Tracker(t041.PlayerTracker):
    @contextmanager
    def viewing(self, mind, keys='gfts'):
        """While active the mind holds the `alt` value of every state named in `keys`; everything is restored on exit, also after an exception."""
        self.flush(mind.player_id)
        saved = t041.mind_state(mind)
        try:
            for key in keys:
                put_part(mind, self.cols[mind.player_id]['alt'], key)
            yield
        finally:
            t041.put_state(mind, saved)

    def shift(self, mind):
        """alt - real of the three practice states of one decision (mean expected gain, perceived fatigue, mean trust)."""
        alt, real = self.cols[mind.player_id]['alt'], t041.mind_state(mind)
        diffs = {'gain': mean_of(alt['gains'].values()) - mean_of(real['gains'].values()), 'fatigue': alt['fatigue'] - real['fatigue'],
                 'trust': mean_of(alt['trust'].values()) - mean_of(real['trust'].values())}
        for name, diff in diffs.items():
            add(self.acc, ('shift', name), diff)
            add(self.acc, ('shiftabs', name), abs(diff))

    def compare(self, channel, signatures, executed_key):
        """signatures[key] = (decision, reason) from the plain ('') or an alt state. Records the differences from the plain one and the distributions of the executed one."""
        plain, executed = signatures[''], signatures[executed_key]
        for key, signature in signatures.items():
            label = f'P{key}' if channel == 'practice' else 'R'
            if not key or label not in FLIP_LABELS:
                continue
            add(self.acc, ('flip', label), float(plain[0] != signature[0]))
            add(self.acc, ('reasonchg', label), float(plain[1] != signature[1]))
            if label in TRANS_LABELS and plain != signature:
                add(self.acc, ('trans', label, *plain, *signature), 1.0)
        for name in ACTS[channel]:
            add(self.acc, ('act', channel, name), float(executed[0].split('>')[0] == name))
        for code in REASONS[channel]:
            add(self.acc, ('rsn', channel, code), float(executed[1] == code))


@contextmanager
def installed(tracker, keys, switch_response):
    """The recorders of T041 (noise, fatigue, events, day-start skills, new players) with the T042 decision wrappers on top: transfers run plain, the practice choice and the advice response are
    evaluated on the plain and on the alt states, and the registered subset is the one executed."""
    names = ('decide_player_training', '_player_advice_response', 'decide_transfer_bidding')
    plain_functions = {n: getattr(le, n) for n in names}
    needed = RECORDED_SETS + (() if keys in RECORDED_SETS or not keys else (keys,))
    with t041.installed(tracker, ()):
        t041_wrappers = {n: getattr(le, n) for n in names}

        def practice(**kw):
            mind, original = kw['mind'], plain_functions['decide_player_training']
            tracker.shift(mind)
            results = {'': original(**kw)}
            for subset in needed:
                with tracker.viewing(mind, subset):
                    results[subset] = original(**kw)
            tracker.compare('practice', {k: t041.practice_signature(r) for k, r in results.items()}, keys)
            executed = results[keys]
            following = kw.get('advice_mode') == 'follow'
            if following:
                with tracker.viewing(mind, keys):
                    own_choice = original(**{**kw, 'advice_mode': 'blend'})['chosen']['action']
            followed = executed['chosen']['action']
            add(tracker.acc, ('override', 'rest'), float(following and own_choice == 'rest' and followed != 'rest'))
            add(tracker.acc, ('override', 'method'), float(following and own_choice != 'rest' and followed != own_choice))
            return executed

        def response(world, pid, props):
            original = plain_functions['_player_advice_response']
            plain = original(world, pid, props)
            with tracker.viewing(world.player_minds[pid]):
                other = original(world, pid, props)
            tracker.compare('response', {'': t041.response_signature(plain), 'R': t041.response_signature(other)}, 'R' if switch_response else '')
            return other if switch_response else plain

        try:
            le.decide_player_training, le._player_advice_response, le.decide_transfer_bidding = practice, response, plain_functions['decide_transfer_bidding']
            yield
        finally:
            for name, function in t041_wrappers.items():
                setattr(le, name, function)


# ---- what the researcher records at every lineup decision (true skills; nothing goes back) ----
def record_mediators(acc, config, events, truth_of):
    """gap = true skill of the 2nd best minus the 3rd best; signal = lowest coach score of the true top two minus the highest of the others (> 0 exactly when the lineup is right);
    err = (skill weight + match weight) * gap - signal, i.e. how much the coach's scores shrink or distort the true difference."""
    scale = config.select_skill_weight + config.select_match_weight
    for e in events:
        ranking = e['data']['ranking'] if e['kind'] == 'lineup_decision' else []
        if len(ranking) < 3:
            continue
        truth = {r['player_id']: truth_of(r['player_id']) for r in ranking}
        true_set = t038.top2(list(truth.items()))
        signal = min(r['score'] for r in ranking if r['player_id'] in true_set) - max(r['score'] for r in ranking if r['player_id'] not in true_set)
        ordered = sorted(truth.values(), reverse=True)
        gap = ordered[1] - ordered[2]
        add(acc, ('med', 'gap'), gap)
        add(acc, ('med', 'err'), scale * gap - signal)
        add(acc, ('med', 'signal'), signal)
        add(acc, ('med', 'zero'), float(signal == 0))
        add(acc, ('med', 'mismatch'), float(signal != 0 and (signal > 0) != (set(e['data']['starters']) == true_set)))


# ---- one run ----
def play(config, seed, view_mode, keys, switch_response, acc, days=DAYS, observer=None):
    world = le._build_initial_world(seed=seed, config=config)
    tracker = Tracker(world, view_mode, acc)
    with installed(tracker, keys, switch_response):
        while world.day < days:
            n_events = len(world.events)
            le.advance_league_days(world, 1)
            tracker.end_of_day(world)
            if observer:
                observer(world, world.events[n_events:])
    assert not tracker.noise and not tracker.fatigue, (len(tracker.noise), len(tracker.fatigue))  # every recorded draw was matched to an event
    return world, tracker


def run_arm(setting, name, seed, start, arm, days=DAYS, identity_view=False):
    direction, keys, switch_response = ARMS[arm]
    world_mode, view_mode = DIRECTIONS[direction]
    config = t036.make_cfg(setting, name, start, world_mode)
    acc, prev = {}, {}

    def observer(world, events):
        truth = {}
        truth_of = lambda pid: truth.setdefault(pid, world.players[pid].composite_skill())
        t040.record_day(acc, events, truth_of, prev)
        t041.record_events(acc, events)
        record_mediators(acc, world.config, events, truth_of)

    world, tracker = play(config, seed, world_mode if identity_view else view_mode, keys, switch_response, acc, days, observer)
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
    out['day_one'] = t041.day_one_deviation(setting, name, seed, start)
    return task, out


# ---- gates ----
check_sim_unchanged, plain_digest, check_t037_reproduced = t040.check_sim_unchanged, t040.plain_digest, t040.check_t037_reproduced


def check_identity_view(days=DAYS):
    """Gate 5: with the view set to the world's own mode, switching everything (gftR) reproduces the plain world's whole snapshot, in both directions."""
    names, out = t023.conditions(), {}
    for setting, index, seed, start in t040.IDENTITY_PICKS:
        for direction, (world_mode, _) in DIRECTIONS.items():
            plain = le.run_league_world(seed=seed, days=days, config=t036.make_cfg(setting, names[index], start, world_mode))
            result = run_arm(setting, names[index], seed, start, f'{direction}_gftR', days=days, identity_view=True)
            assert plain_digest(result['world']) == plain_digest(plain), (setting, names[index], seed, start, direction)
            out[f'{setting}_{index}_{seed}_{start}_{direction}'] = True
    return {'runs': len(out), 'all_equal': True}


def level_of(pooled, key):
    n, s = pooled[key][0], pooled[key][1]
    return n, s / n


def check_t041_reproduced(root, pooled_by_setting):
    """Gate 6: the arms that T041 also ran give the same lineup-match levels (T041 names: gft = practice, R = response)."""
    t041_points = json.loads((root / 'artifacts/t041/run/summary.json').read_text())['summaries']
    names = {'base': 'base', 'gft': 'practice', 'R': 'response'}
    worst = 0.0
    for scope in (*SETTINGS, 'all'):
        pools = pooled_by_setting[scope]
        for direction in DIRECTIONS:
            for ours, theirs in names.items():
                _, level = level_of(pools[f'{direction}_{ours}'], ('start', 'top2'))
                worst = max(worst, abs(level - t041_points[scope]['point'][f'level|{direction}_{theirs}|top2']))
    assert worst <= 1e-12, worst
    return {'max_abs_difference': worst}


# ---- statistics ----
def shapley(values):
    """values[subset string in the order g, f, t] -> the Shapley share of each of the three variables (sums to values['gft'])."""
    full = {'': 0.0, **values}
    key = lambda members: ''.join(sorted(members, key='gft'.index))
    out = {}
    for v in VARS:
        others = [u for u in VARS if u != v]
        total = 0.0
        for size in range(len(others) + 1):
            weight = 1.0 / (len(VARS) * len(list(combinations(others, size))))
            for members in combinations(others, size):
                total += weight * (full[key((*members, v))] - full[key(members)])
        out[v] = total
    return out


def effects(v):
    """v[arm][metric] = level. D, F (sufficiency), R (necessity) per arm and metric, and for the lineup match the ratios to D, the interactions, the Shapley shares and the effect of the advice response by practice state."""
    out = {f'level|{arm}|{m}': v[arm][m] for arm in ARMS for m in METRICS}
    for m in METRICS:
        out[f'D|{m}'] = sub(v['rev_base'][m], v['fwd_base'][m])
        for x in ARM_X:
            out[f'F|{x}|{m}'] = sub(v[f'fwd_{x}'][m], v['fwd_base'][m])
            out[f'R|{x}|{m}'] = sub(v['rev_base'][m], v[f'rev_{x}'][m])
    d = out['D|top2']
    ratio = lambda value: None if not d or value is None else value / d
    parts = lambda side, *xs: [out[f'{side}|{x}|top2'] for x in xs]
    combine = lambda values, fn: None if None in values else fn(*values)
    for side in ('F', 'R'):
        for x in ARM_X:
            out[f'{side.lower()}|{x}'] = ratio(out[f'{side}|{x}|top2'])
        out[f'IV{side}|top2'] = combine(parts(side, 'gft', 'g', 'f', 't'), lambda gft, g, f, t: gft - (g + f + t))
        out[f'iV{side}'] = ratio(out[f'IV{side}|top2'])
        for pair in ('gf', 'gt', 'ft'):
            out[f'I{pair}{side}|top2'] = combine(parts(side, pair, *pair), lambda both, a, b: both - a - b)
        singles = parts(side, *ARM_X[:7])
        shares = None if None in singles else shapley(dict(zip(ARM_X[:7], singles)))
        for v_ in VARS:
            out[f'shap{side}|{v_}'] = None if shares is None else shares[v_]
            out[f'shapshare{side}|{v_}'] = None if shares is None or not out[f'{side}|gft|top2'] else shares[v_] / out[f'{side}|gft|top2']
        # the effect of the advice response with the practice states at plain / trust only / all three
        out[f'{side}r|none|top2'] = out[f'{side}|R|top2']
        out[f'{side}r|t|top2'] = combine(parts(side, 'tR', 't'), lambda a, b: a - b)
        out[f'{side}r|gft|top2'] = combine(parts(side, 'gftR', 'gft'), lambda a, b: a - b)
        out[f'Ir{side}|top2'] = combine([out[f'{side}r|gft|top2'], out[f'{side}r|none|top2']], lambda a, b: a - b)
        out[f'It{side}|top2'] = combine([out[f'{side}r|t|top2'], out[f'{side}r|none|top2']], lambda a, b: a - b)
    return out


def cell_levels(cell_accs):
    """cell_accs[arm] = list of per-cell accumulators -> {arm: {metric: (n list, sum list)}} for fast resampling."""
    return {arm: {m: ([a[key][0] if key in a else 0 for a in accs], [a[key][1] if key in a else 0.0 for a in accs]) for m, key in METRICS.items()} for arm, accs in cell_accs.items()}


def unit_values(acc):
    """(rest share, gap, err, lineup match) of one (cell, arm)."""
    return tuple(acc[key][1] / acc[key][0] for key in MED_COLUMNS)


def cell_matrices(cell):
    """Sufficient statistics of the mediation regressions of one cell: for each group the sum of z z^T (4x4, flattened) over the arms' differences z from the base arm of the same direction."""
    sums = {g: [0.0] * 16 for g in MED_GROUPS}
    for direction in DIRECTIONS:
        base = unit_values(cell[f'{direction}_base']['acc'])
        for x in ARM_X:
            z = [a - b for a, b in zip(unit_values(cell[f'{direction}_{x}']['acc']), base)]
            groups = ['all', direction] + ([] if SHAPES[x][1] else ['practice_only'])
            for g in groups:
                for i in range(4):
                    for j in range(4):
                        sums[g][4 * i + j] += z[i] * z[j]
    return sums


def solve(matrix, rhs):
    """Gauss-Jordan with partial pivoting; None if singular."""
    n = len(rhs)
    rows = [list(matrix[i]) + [rhs[i]] for i in range(n)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(rows[r][col]))
        if abs(rows[pivot][col]) < 1e-18:
            return None
        rows[col], rows[pivot] = rows[pivot], rows[col]
        rows[col] = [x / rows[col][col] for x in rows[col]]
        for r in range(n):
            if r != col:
                rows[r] = [x - rows[r][col] * y for x, y in zip(rows[r], rows[col])]
    return [rows[i][n] for i in range(n)]


def mediation(s):
    """s: flattened 4x4 sum of z z^T over (rest, gap, err, lineup match). Regressions without intercept (the differences from the base arm): total c, a paths, and the joint fit with the direct c' and the b paths."""
    at = lambda i, j: s[4 * i + j]
    if not at(0, 0):
        return dict.fromkeys(MED_STATS)
    a_gap, a_err, c = at(0, 1) / at(0, 0), at(0, 2) / at(0, 0), at(0, 3) / at(0, 0)
    fit = solve([[at(i, j) for j in range(3)] for i in range(3)], [at(i, 3) for i in range(3)])
    if fit is None:
        return {**dict.fromkeys(MED_STATS), 'c': c, 'a_gap': a_gap, 'a_err': a_err}
    c_direct, b_gap, b_err = fit
    indirect = (a_gap * b_gap, a_err * b_err)
    return {'c': c, 'a_gap': a_gap, 'a_err': a_err, 'c_direct': c_direct, 'b_gap': b_gap, 'b_err': b_err, 'ind_gap': indirect[0], 'ind_err': indirect[1],
            'prop': None if not c else sum(indirect) / c}


def summarise(cell_accs, cell_mats, boots, seed=SEED_BOOT):
    """One resample of the cells is shared by every arm, statistic, ratio and mediation fit."""
    vectors = cell_levels(cell_accs)
    n_cells = len(cell_mats)
    resampled_mats = lambda counts: {g: [sum(c * m[g][i] for c, m in zip(counts, cell_mats)) for i in range(16)] for g in MED_GROUPS}
    point_counts = [1] * n_cells
    point, med_point = effects(resampled_levels(vectors, point_counts)), {g: mediation(s) for g, s in resampled_mats(point_counts).items()}
    rng, draws, med_draws = random.Random(seed), [], []
    for _ in range(boots):
        counts = [0] * n_cells
        for _ in range(n_cells):
            counts[rng.randrange(n_cells)] += 1
        draws.append(effects(resampled_levels(vectors, counts)))
        med_draws.append({g: mediation(s) for g, s in resampled_mats(counts).items()})
    return {'cells': n_cells, 'point': point, 'ci95': {k: interval([d[k] for d in draws]) for k in point},
            'mediation': {g: {'point': med_point[g], 'ci95': {k: interval([d[g][k] for d in med_draws]) for k in MED_STATS}} for g in MED_GROUPS}}


def excludes_zero(ci_pair):
    return ci_pair is not None and ci_pair[0] * ci_pair[1] > 0


def classify_variables(point, ci):
    """The pre-registered attribution over the three practice states (docs/experiments/T042-PROTOCOL.md), first match wins, plus the flags."""
    significant = lambda key: ci[key] is not None and ci[key][0] > 0
    pair_carriers = [p for p in ('gf', 'gt', 'ft') if significant(f'F|{p}|top2') and significant(f'R|{p}|top2')]
    if not (significant('F|gft|top2') and significant('R|gft|top2')):
        return {'class': 'no_practice_gain', 'carriers': [], 'main_routes': [], 'pair_carriers': pair_carriers, 'flags': []}
    carriers = [v for v in VARS if significant(f'F|{v}|top2') and significant(f'R|{v}|top2')]
    main = [v for v in carriers if point[f'f|{v}'] >= 0.5 and point[f'r|{v}'] >= 0.5]
    flags = []
    if any(abs(point[f'iV{s}']) >= 0.25 and excludes_zero(ci[f'IV{s}|top2']) for s in ('F', 'R')):
        flags.append('interaction_vars')
    flags += [f'opposed({LONG[v]})' for v in VARS if any(ci[f'{s}|{v}|top2'][1] < 0 for s in ('F', 'R'))]
    names = lambda vs: ', '.join(LONG[v] for v in vs)
    if len(main) == 1:
        label = f'single_variable({names(main)})'
    elif len(main) > 1:
        label = f'multiple_variables({names(main)})'
    elif len(carriers) > 1:
        label = f'distributed({names(carriers)})'
    elif carriers:
        label = f'partial_single({names(carriers)})'
    else:
        label = 'only_jointly'
    return {'class': label, 'carriers': carriers, 'main_routes': main, 'pair_carriers': pair_carriers, 'flags': flags}


def classify_harm(ci):
    """The pre-registered classification of harm(response) in the reverse direction."""
    r_plain, r_practice, interaction = ci['Rr|none|top2'], ci['Rr|gft|top2'], ci['IrR|top2']
    if r_plain[1] >= 0:
        return 'not_reproduced'
    if interaction[0] > 0 and r_practice[1] >= 0:
        return 'practice_dependent'
    if r_practice[1] < 0 and not excludes_zero(interaction):
        return 'practice_independent'
    return 'partly_dependent'


def classify_mediation(med):
    """med: {'point', 'ci95'} of one group. First match: no_total, unmediated, mediated(M) with complete / partial."""
    point, ci = med['point'], med['ci95']
    if not excludes_zero(ci['c']):
        return 'no_total'
    through = [k for k in ('gap', 'err') if excludes_zero(ci[f'ind_{k}']) and (point[f'ind_{k}'] > 0) == (point['c'] > 0)]
    if not through:
        return 'unmediated'
    complete = point['prop'] is not None and point['prop'] >= 0.8 and not excludes_zero(ci['c_direct'])
    return f"mediated({', '.join(through)}):{'complete' if complete else 'partial'}"


def recommend(attribution, harm, mediation_class, point, ci, positive_settings):
    """The pre-registered default-change recommendation (proposal only): (verdict, unmet conditions)."""
    if ci['D|top2'][0] <= 0 or ci['D|final_skill'][0] < 0:
        return 'hold', ['D_or_final_skill']
    unmet = []
    if ci['D|final_trust'][0] < 0:
        unmet.append('final_trust')
    if positive_settings < len(SETTINGS):
        unmet.append('settings_with_positive_D')
    if not (point['f|gft'] >= 0.5 and point['r|gft'] >= 0.5):
        unmet.append('gft_carries_gain')
    if attribution['class'].split('(')[0] not in ('single_variable', 'multiple_variables', 'distributed', 'partial_single'):
        unmet.append('attribution_to_variables')
    if harm not in ('practice_dependent', 'not_reproduced'):
        unmet.append('harm_response_unexplained')
    if not mediation_class.startswith('mediated'):
        unmet.append('rest_to_lineup_unexplained')
    return ('recommend' if not unmet else 'conditional'), unmet


def transitions(cells, arm):
    """How the decisions that changed under the other mode's state changed, summed over all cells: {label: [(plain decision, plain reason, alt decision, alt reason, n)]}."""
    pooled = merged(c[arm]['acc'] for t in SETTINGS for c in cells[t])
    out = {label: [] for label in TRANS_LABELS}
    for key, r in sorted(pooled.items()):
        if key[0] == 'trans':
            out[key[1]].append(list(key[2:]) + [int(r[0])])
    return {'decisions': {label: pooled[('flip', label)][0] for label in TRANS_LABELS}, 'changed': out}


def summarise_job(job):
    scope, cell_accs, cell_mats, boots = job
    return scope, summarise(cell_accs, cell_mats, boots)


def run(out, root, boots=1000, workers=None):
    out.mkdir(parents=True, exist_ok=False)
    start = perf_counter()
    gates = {'sim_unchanged_vs_frozen_t037_bundle': check_sim_unchanged(root)}
    gates['identity_view'] = check_identity_view()
    print('gates', json.dumps(gates), flush=True)
    refs = {t: {(c['world'], c['seed'], c['start_age']): c for c in json.loads((root / f'artifacts/{t}/run/summary.json').read_text())['cells']} for t in SETTINGS}
    tasks = [(t, name, seed, st) for t in SETTINGS for name in t023.conditions() for seed in SEEDS for st in STARTS]
    cells = {t: [] for t in SETTINGS}
    equal, self_flips, med_mismatch, med_zero, worst_day_one = {t: 0 for t in SETTINGS}, 0.0, 0.0, 0.0, 0.0
    with ProcessPoolExecutor(max_workers=workers or os.cpu_count()) as pool:
        for count, ((setting, name, seed, st), got) in enumerate(pool.map(run_cell, tasks, chunksize=1), start=1):
            cells[setting].append(got)
            legacy, ref = got['fwd_base']['flat'], refs[setting][(name, seed, st)]['on']
            assert {k[6:]: v for k, v in legacy.items() if k.startswith('final_')} == ref['final'] and legacy['events'] == ref['events'], (setting, name, seed, st)  # gate 2
            centered, plain = got['rev_base']['flat'], got['plain_centered']
            assert centered == plain['flat'] and got['rev_base']['acc'][('start', 'top2')][0] == plain['decisions'], (setting, name, seed, st)  # gate 2
            equal[setting] += 1
            self_flips += sum(got[arm]['acc'][('flip', 'Ps')][1] for arm in ARMS)
            med_mismatch += sum(got[arm]['acc'][('med', 'mismatch')][1] for arm in ARMS)
            med_zero += sum(got[arm]['acc'][('med', 'zero')][1] for arm in ARMS)
            worst_day_one = max(worst_day_one, got['day_one'])
            if count % 12 == 0:
                print(count, len(tasks), round(perf_counter() - start), flush=True)
    assert worst_day_one <= t040.DAY_ONE_TOLERANCE, worst_day_one
    assert self_flips == 0 and med_mismatch == 0, (self_flips, med_mismatch)
    gates['base_arms_equal_plain_runs'] = equal
    gates['day_one_alt_equals_other_mode_max_deviation'] = worst_day_one
    gates['self_estimate_flips_of_the_practice_choice'] = self_flips
    gates['lineup_signal_sign_mismatches'] = med_mismatch
    gates['lineup_signal_zero_count_report'] = med_zero
    scopes = {'all': [c for t in SETTINGS for c in cells[t]], **cells}
    pooled = {scope: {arm: merged(c[arm]['acc'] for c in group) for arm in ARMS} for scope, group in scopes.items()}
    gates['t037_reproduced'] = check_t037_reproduced(root, {t: {arm: pooled[t][arm] for arm in ('fwd_base', 'rev_base')} for t in SETTINGS})
    gates['t041_reproduced'] = check_t041_reproduced(root, pooled)
    gates['invariants_validated_runs'] = len(tasks) * len(ARMS)
    jobs = [(scope, {arm: [c[arm]['acc'] for c in group] for arm in ARMS}, [cell_matrices(c) for c in group], boots) for scope, group in scopes.items()]
    with ProcessPoolExecutor(max_workers=min(len(jobs), workers or os.cpu_count())) as pool:
        summaries = dict(pool.map(summarise_job, jobs))
    t040_d = json.loads((root / 'artifacts/t040/run/summary.json').read_text())['summaries']['all']['point']['D|top2']
    gates['d_equals_t040'] = abs(summaries['all']['point']['D|top2'] - t040_d) <= 1e-12
    assert gates['d_equals_t040']
    attribution = {k: classify_variables(s['point'], s['ci95']) for k, s in summaries.items()}
    harm = {k: classify_harm(s['ci95']) for k, s in summaries.items()}
    mediated = {k: {g: classify_mediation(s['mediation'][g]) for g in MED_GROUPS} for k, s in summaries.items()}
    verdict, unmet = recommend(attribution['all'], harm['all'], mediated['all']['all'], summaries['all']['point'], summaries['all']['ci95'],
                               sum(1 for t in SETTINGS if summaries[t]['point']['D|top2'] > 0))
    result = {'task': 'T042', 'identity': le.league_identity(), 'runtime': runtime(), 'seeds': list(SEEDS), 'days': DAYS,
              'arms': {a: {'direction': d, 'practice_states': k, 'response': r} for a, (d, k, r) in ARMS.items()},
              'gates': gates, 'summaries': summaries, 'attribution': attribution, 'harm_response': harm, 'mediation_class': mediated,
              'settings_with_positive_D': sum(1 for t in SETTINGS if summaries[t]['ci95']['D|top2'][0] > 0),
              'recommendation': {'verdict': verdict, 'unmet': unmet},
              'transitions': {arm: transitions(cells, arm) for arm in ('fwd_base', 'rev_base')},
              'checked_totals': {arm: {k: sum(cell[arm]['checked'][k] for t in SETTINGS for cell in cells[t]) for k in ('self', 'match_fatigue', 'feedback', 'days')} for arm in ARMS},
              'elapsed_seconds': perf_counter() - start}
    write_json(out / 'summary.json', result)
    print(json.dumps({'equal': equal, 'attribution': attribution['all'], 'harm_response': harm['all'], 'mediation': mediated['all'], 'recommendation': result['recommendation'],
                      'elapsed_seconds': result['elapsed_seconds']}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=None)
    arguments = parser.parse_args()
    run(arguments.out.resolve(), CODE_ROOT, workers=arguments.workers)
