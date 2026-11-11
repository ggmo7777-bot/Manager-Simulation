"""T044: which causes of the coach's estimate error move along the rest-rate sweep (the U-shape of the lineup match), and does too little rest hurt through fatigue -> match performance -> evaluation?
Analysis only: `soccer_sim/` is unchanged and no config is added. The arms are T043's sweep (base and the nine uniform shifts of the expected gain, both directions). A read-only recorder rebuilds, from the recorded
observation events, each coach's skill estimate and match-rating estimate of each player exactly (`R`) and runs three shadow estimates on the same observation sequence: `A` (noise removed), `I` (observation target
replaced by the true skill at observation time) and `Z` (also the first impression replaced by the true skill). The error E_R - T then splits exactly into noise (R-A), definition (A-I), first impression (I-Z) and lag (Z-T).
True skills are read by the researcher after the fact; nothing goes back into the simulation.

Protocol: docs/experiments/T044-PROTOCOL.md. Cells: 14 worlds x seeds {100,101,102} x start age {29,33}, 168 days, three settings x 20 arms (+ one plain centered_v1 run per cell for the gates).
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
from experiments import t023, t030, t036, t038, t040, t042, t043
from soccer_sim import league_economy as le
from soccer_sim.match_2d import clamp, round6
from soccer_sim.storage import runtime, write_json

DAYS, SEEDS, STARTS, SETTINGS = t036.DAYS, t036.SEEDS, t036.STARTS, t036.SETTINGS
DIRECTIONS = t040.DIRECTIONS
ARMS = {arm: spec for arm, spec in t043.ARMS.items() if spec[1] in ('plain', 'offset')}  # base + the nine sweep shifts, per direction
FILTERS = ('R', 'A', 'I', 'Z')
CAUSES = ('noise', 'def', 'prior', 'lag')
ESTIMATES = ('skill', 'match')
COMPONENTS = tuple(f'{estimate}_{cause}' for estimate in ESTIMATES for cause in CAUSES)
CAUSE_PARTS = {'noise': ('skill_noise', 'match_noise'), 'def': ('skill_def', 'match_def'), 'prior': ('skill_prior', 'match_prior'), 'lag': ('skill_lag', 'match_lag')}
CAUSE_PARTS['stale'] = CAUSE_PARTS['prior'] + CAUSE_PARTS['lag']
MAIN_CAUSES = ('noise', 'def', 'stale')  # the three causes of the registered classification
OTHER_PARTS = ('med_sel', 'med_fatigue', 'cmp_round')
PRACTICE_BLEND = (0.70, 0.30)  # the simulation's literal weights of the match-rating estimate for practice and scouting observations
SKILL_BOUNDS, RATING_BOUNDS = (0.10, 0.98), (0.15, 0.98)
ROUNDING_TOLERANCE, TELESCOPE_TOLERANCE, SLACK = 5e-6, 1e-9, 1e-6
# contrasts of two sweep arms (high, low), fixed before the run
CONTRASTS = {'LO': ('o+0.0300', 'o-0.0100'), 'HI': ('o-0.0300', 'o-0.0100'), 'KN': ('o+0.0300', 'o+0.0100')}
SHARE_PARTS = tuple(f'cause_{c}' for c in (*MAIN_CAUSES, 'prior', 'lag', 'other')) + ('est_skill', 'est_match') + tuple(f'cmp_{c}' for c in COMPONENTS) + OTHER_PARTS
REPRODUCED = ('top2', 'act_rest', 'fatigue', 'med_gap', 'med_err', 'med_signal', 'med_sel', 'med_skill', 'med_match', 'med_fatigue', 'med_round', *(f'final_{k}' for k in t040.FINALS))
# metric id -> accumulator key; the first group has the ids of T043
METRICS = {'top2': ('start', 'top2'), **{f'final_{k}': ('final', k) for k in t040.FINALS}, 'act_rest': ('act', 'rest'), 'fatigue': ('fat', 'mean'),
           'med_gap': ('med', 'gap'), 'med_err': ('med', 'err'), 'med_signal': ('med', 'signal'), **{f'med_{p}': ('med', p) for p in t043.PARTS},
           **{f'cmp_{c}': ('cmp', c) for c in (*COMPONENTS, 'round')},
           'bonus_f': ('mf', 'f'), 'bonus_b': ('mf', 'b'), 'bonus_ff': ('mf', 'ff'), 'bonus_fb': ('mf', 'fb'), 'age_s': ('age', 's'), 'age_m': ('age', 'm')}
SEED_BOOT = 41
add, merge, interval, stat = t036.add, t036.merge, t038.interval, t036.stat
sub, merged, resampled_levels = t040.sub, t040.merged, t040.resampled_levels
excludes_zero, mean_of = t042.excludes_zero, t043.mean_of
check_sim_unchanged, plain_digest, check_t037_reproduced = t040.check_sim_unchanged, t040.plain_digest, t040.check_t037_reproduced


# ---- the shadow estimates ----
def blend(old, weights, value):
    """One update of an estimate, the simulation's own formula."""
    return round6(clamp(weights[0] * old + weights[1] * value, *SKILL_BOUNDS))


def fresh_cell(skill, match, truth, day):
    """R, A, I start at the first impression; Z starts at the true skill."""
    return {'est': {'R': [skill, match], 'A': [skill, match], 'I': [skill, match], 'Z': [truth, truth]}, 'seen': [day, day]}


def telescope(estimates, index, truth):
    """The error of the real estimate split into four terms; the terms add up to R - truth."""
    real, no_noise, ideal, ideal_start = (estimates[f][index] for f in FILTERS)
    terms = {'noise': real - no_noise, 'def': no_noise - ideal, 'prior': ideal - ideal_start, 'lag': ideal_start - truth}
    assert abs(sum(terms.values()) - (real - truth)) <= TELESCOPE_TOLERANCE, (terms, real, truth)
    return terms


def components(snapshot, j, k, truth_of, config):
    """The eight contributions of the estimates' error to err: -w x (term_j - term_k) per estimate and cause."""
    out = {}
    for index, (estimate, weight) in enumerate((('skill', config.select_skill_weight), ('match', config.select_match_weight))):
        errors = {pid: telescope(snapshot[pid][0], index, truth_of(pid)) for pid in (j, k)}
        for cause in CAUSES:
            out[f'{estimate}_{cause}'] = -weight * (errors[j][cause] - errors[k][cause])
    return out


def boundary_pair(ranking, true_set):
    """T043's j (true top-two member with the lowest score) and k (the other player with the highest score)."""
    j = min((r for r in ranking if r['player_id'] in true_set), key=lambda r: r['score'])
    k = max((r for r in ranking if r['player_id'] not in true_set), key=lambda r: r['score'])
    return j['player_id'], k['player_id']


def rating_performance(truth, goals, passes, tackles, won):
    """The unnoised match-rating observation of the simulation (same order of operations)."""
    return truth + 0.06 * goals + 0.015 * passes + 0.02 * tackles + (0.03 if won else -0.01)


class Shadows:
    """Read-only record of every coach's two estimates of every player, rebuilt from the events, with the three shadow estimates."""

    def __init__(self, world, acc):
        self.config, self.acc = world.config, acc
        truth = {pid: p.composite_skill() for pid, p in world.players.items()}
        self.cells = {(c, pid): fresh_cell(ev.skill_estimate, ev.match_rating_estimate, truth[pid], 0) for c, mind in world.coaches.items() for pid, ev in mind.evaluations.items()}
        self.performance, self.fatigue_before, self.snapshots, self.joined = {}, {}, {}, []
        self.checked = dict.fromkeys(('lineup_terms', 'observation_events', 'day_estimates', 'bounded_inputs', 'clamped_inputs'), 0)

    # -- events --
    def observe(self, world, event):
        handler = {'coach_session_observation': self.session, 'coach_scout_observation': self.scout, 'league_match_completed': self.match_result,
                   'coach_match_observation': self.match, 'lineup_decision': self.lineup, 'season_youth_joined': self.youth}.get(event['kind'])
        if handler:
            handler(world, event)

    def update(self, cell, day, inputs, skill, match):
        for name, value in inputs.items():
            estimate = cell['est'][name]
            if skill:
                estimate[0] = blend(estimate[0], skill, value)
            if match:
                estimate[1] = blend(estimate[1], match, value)
        for index, weights in enumerate((skill, match)):
            if weights:
                cell['seen'][index] = day

    def check_input(self, observed, target, width, bounds):
        """The observation minus its target is a noise draw: in [-3 width, width] (legacy) or [-width, width] (centered). Inputs pinned at a clamp bound are counted, not checked."""
        if observed in bounds:
            self.checked['clamped_inputs'] += 1
            return
        assert -3 * width - SLACK <= observed - target <= width + SLACK, (observed, target, width)
        self.checked['bounded_inputs'] += 1

    def practice_like(self, world, event, coach, pid, width, skill_weights, match_weights, with_match):
        data, truth = event['data'], world.players[pid].composite_skill()
        self.check_input(data['obs_skill'], truth, width, SKILL_BOUNDS)
        cell = self.cells[coach, pid]
        self.update(cell, event['day'], {'R': data['obs_skill'], 'A': truth, 'I': truth, 'Z': truth}, skill_weights, match_weights)
        real = cell['est']['R']
        assert real[0] == data['skill_estimate'] and (not with_match or real[1] == data['match_rating_estimate']), (event['id'], real, data)
        self.checked['observation_events'] += 1

    def session(self, world, event):
        d, cfg = event['data'], self.config
        with_match = cfg.coach_practice_rating != 'none'
        self.practice_like(world, event, d['coach_id'], d['player_id'], cfg.coach_noise, (1.0 - cfg.coach_skill_alpha, cfg.coach_skill_alpha), PRACTICE_BLEND if with_match else None, with_match)

    def scout(self, world, event):
        d, cfg = event['data'], self.config
        self.practice_like(world, event, d['coach_id'], d['target_player_id'], cfg.scout_noise, (1.0 - cfg.coach_scout_alpha, cfg.coach_scout_alpha), PRACTICE_BLEND, True)

    def match_result(self, world, event):
        d = event['data']
        for side, other in (('home', 'away'), ('away', 'home')):
            won = d[f'{side}_goals'] > d[f'{other}_goals']
            for pid in d[f'{side}_starters']:
                stats = d['player_stats'][pid]
                self.performance[pid] = (stats['goals'], stats['passes_completed'], stats['tackles_won'], won)

    def match(self, world, event):
        d, cfg, pid = event['data'], self.config, event['data']['player_id']
        truth = world.players[pid].composite_skill()
        perf = rating_performance(truth, *self.performance[pid])
        self.check_input(d['rating_obs'], perf, cfg.match_obs_noise, RATING_BOUNDS)
        cell = self.cells[event['actor'], pid]
        inputs = {'R': d['rating_obs'], 'A': round6(clamp(perf, *RATING_BOUNDS)), 'I': truth, 'Z': truth}
        self.update(cell, event['day'], inputs, (1.0 - cfg.coach_match_skill_blend, cfg.coach_match_skill_blend), (1.0 - cfg.coach_match_alpha, cfg.coach_match_alpha))
        fatigue, bonus = self.fatigue_before[pid], perf - truth
        for key, value in (('f', fatigue), ('b', bonus), ('ff', fatigue * fatigue), ('fb', fatigue * bonus)):
            add(self.acc, ('mf', key), value)

    def lineup(self, world, event):
        d, cfg, day = event['data'], self.config, event['day']
        snapshot = {}
        for row in d['ranking']:
            pid = row['player_id']
            cell = self.cells[d['coach_id'], pid]
            skill, match = cell['est']['R']
            assert round6(cfg.select_skill_weight * skill) == row['components']['skill_term'] and round6(cfg.select_match_weight * match) == row['components']['match_term'], (event['id'], pid)
            snapshot[pid] = ({name: tuple(estimate) for name, estimate in cell['est'].items()}, (day - cell['seen'][0], day - cell['seen'][1]))
            self.fatigue_before[pid] = world.players[pid].fatigue
            self.checked['lineup_terms'] += 1
        self.snapshots[event['id']] = snapshot

    def youth(self, world, event):
        self.joined.append((event['data']['player_id'], event['day']))  # the player is created right after this event: resolved at the end of the day

    def end_of_day(self, world):
        """Joins are resolved with the true skill of the day (a newcomer is not changed before the next day); then every real shadow must equal the simulation's own estimate."""
        for pid, day in self.joined:
            truth = world.players[pid].composite_skill()
            for coach in world.coaches:
                self.cells[coach, pid] = fresh_cell(le.YOUTH_PRIOR[0], le.YOUTH_PRIOR[1], truth, day)
        self.joined.clear()
        for (coach, pid), cell in self.cells.items():
            ev = world.coaches[coach].evaluations[pid]
            assert cell['est']['R'] == [ev.skill_estimate, ev.match_rating_estimate], (world.day, coach, pid, cell['est']['R'], ev)
            self.checked['day_estimates'] += 1


@contextmanager
def recording(tracker):
    """Pass-through wrapper of the event log; put back on exit."""
    original = le._append_event

    def append(world, **kw):
        event_id = original(world, **kw)
        tracker.observe(world, world.events[-1])
        return event_id

    le._append_event = append
    try:
        yield
    finally:
        le._append_event = original


# ---- what the researcher records (true skills; nothing goes back) ----
def record_components(acc, config, events, tracker, truth_of):
    """The eight contributions to err of every lineup decision (>= 3 players), the remaining rounding and the age of the estimates."""
    for e in events:
        if e['kind'] != 'lineup_decision':
            continue
        snapshot = tracker.snapshots.pop(e['id'])
        ranking = e['data']['ranking']
        if len(ranking) < 3:
            continue
        decision = t043.decompose(ranking, {r['player_id']: truth_of(r['player_id']) for r in ranking}, config)
        j, k = boundary_pair(ranking, decision['true_set'])
        parts = components(snapshot, j, k, truth_of, config)
        rounding = decision['err'] - decision['parts']['sel'] - decision['parts']['fatigue'] - sum(parts.values())
        assert abs(rounding) <= ROUNDING_TOLERANCE, (e['id'], rounding)
        for name, value in (*parts.items(), ('round', rounding)):
            add(acc, ('cmp', name), value)
        for index, key in enumerate(('s', 'm')):
            add(acc, ('age', key), mean_of(ages[index] for _, ages in snapshot.values()))


# ---- one run ----
def run_arm(setting, name, seed, start, spec, days=DAYS):
    direction, kind, param = spec
    config = t036.make_cfg(setting, name, start, DIRECTIONS[direction][0])
    acc, prev = {}, {}
    world = le._build_initial_world(seed=seed, config=config)
    tracker = Shadows(world, acc)
    with t043.installed(None, kind, param, 0.0, acc), recording(tracker):
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
            record_components(acc, config, events, tracker, truth_of)
            add(acc, ('fat', 'mean'), mean_of(world.players[pid].fatigue for pid in le._active_pids(world)))
    assert not tracker.snapshots, len(tracker.snapshots)
    le._validate_league_invariants(world)
    flat = {f'final_{k}': v for k, v in t030.final_metrics(world).items()}
    flat['events'] = len(world.events)
    for key in t040.FINALS:
        add(acc, ('final', key), flat[f'final_{key}'])
    return {'acc': acc, 'flat': flat, 'checked': tracker.checked, 'world': world}


def run_cell(task):
    setting, name, seed, start = task
    out = {arm: {key: value for key, value in run_arm(setting, name, seed, start, spec).items() if key != 'world'} for arm, spec in ARMS.items()}
    plain = le.run_league_world(seed=seed, days=DAYS, config=t036.make_cfg(setting, name, start, 'centered_v1'))
    out['plain_centered'] = {'flat': {**{f'final_{k}': v for k, v in t030.final_metrics(plain).items()}, 'events': len(plain.events)},
                             'decisions': sum(1 for e in plain.events if e['kind'] == 'lineup_decision')}
    return task, out


# ---- gates ----
def check_recorder_is_read_only(days=DAYS):
    """Gate 5: with the recorder, the base arm equals the plain world and a sweep arm equals T043's arm without the recorder, snapshot for snapshot."""
    names, count = t023.conditions(), 0
    for setting, index, seed, start in t040.IDENTITY_PICKS:
        for direction, (world_mode, _) in DIRECTIONS.items():
            plain = le.run_league_world(seed=seed, days=days, config=t036.make_cfg(setting, names[index], start, world_mode))
            assert plain_digest(run_arm(setting, names[index], seed, start, ARMS[f'{direction}_base'], days=days)['world']) == plain_digest(plain), (setting, index, seed, start, direction)
            spec = ARMS[f'{direction}_o+0.0100']
            assert plain_digest(run_arm(setting, names[index], seed, start, spec, days=days)['world']) == plain_digest(t043.run_arm(setting, names[index], seed, start, spec, days=days)['world']), (setting, index, seed, start, direction)
            count += 2
    return {'runs': count, 'all_equal': True}


def check_t043_reproduced(root, levels_by_scope):
    """Gate 3: every shared metric of every arm has T043's level (settings and total)."""
    saved = json.loads((root / 'artifacts/t043/run/summary.json').read_text())['summaries']
    worst = 0.0
    for scope, by_arm in levels_by_scope.items():
        for arm, levels in by_arm.items():
            for metric in REPRODUCED:
                worst = max(worst, abs(levels[metric] - saved[scope]['point'][f'level|{arm}|{metric}']))
    assert worst <= 1e-12, worst
    return {'max_abs_difference': worst, 'metrics': len(REPRODUCED), 'arms': len(ARMS)}


# ---- statistics ----
def ratio(value, base):
    return None if value is None or not base else value / base


def pearson(xs, ys):
    mx, my = mean_of(xs), mean_of(ys)
    sxx, syy = sum((x - mx) ** 2 for x in xs), sum((y - my) ** 2 for y in ys)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sxx * syy) ** 0.5 if sxx and syy else None


def derive(levels):
    """Sums of the contributions by cause and by estimate, and the slope of the rating bonus on the pre-match fatigue."""
    out = dict(levels)
    if any(levels[f'cmp_{c}'] is None for c in COMPONENTS):
        return out
    for cause, names in CAUSE_PARTS.items():
        out[f'cause_{cause}'] = sum(levels[f'cmp_{n}'] for n in names)
    out['cause_other'] = sum(levels[m] for m in OTHER_PARTS)
    for estimate in ESTIMATES:
        out[f'est_{estimate}'] = sum(levels[f'cmp_{estimate}_{c}'] for c in CAUSES)
    variance = levels['bonus_ff'] - levels['bonus_f'] ** 2
    out['bonus_slope'] = (levels['bonus_fb'] - levels['bonus_f'] * levels['bonus_b']) / variance if variance else None
    return out


def curve(direction):
    """The ten arms of a direction in the order of the shift."""
    return [f'{direction}_base'] + [f'{direction}_{x}' for x in t043.SWEEP_ARMS]


def effects(v):
    """v[arm][metric] = level. Levels (with the derived sums), the three contrasts of every metric per direction, the share of each part in the change of err, and corr(top2, err) over the ten points."""
    v = {arm: derive(levels) for arm, levels in v.items()}
    out = {f'level|{arm}|{m}': value for arm, levels in v.items() for m, value in levels.items()}
    for d in DIRECTIONS:
        for name, (high, low) in CONTRASTS.items():
            a, b = f'{d}_{high}', f'{d}_{low}'
            for m in v[a]:
                out[f'contrast|{d}|{name}|{m}'] = sub(v[a][m], v[b][m])
            for part in SHARE_PARTS:
                out[f'share|{d}|{name}|{part}'] = ratio(out.get(f'contrast|{d}|{name}|{part}'), out[f'contrast|{d}|{name}|med_err'])
        arms = curve(d)
        out[f'corr|{d}'] = pearson([v[a]['top2'] for a in arms], [v[a]['med_err'] for a in arms])
    return out


def cell_levels(cell_accs):
    """cell_accs[arm] = list of per-cell accumulators -> {arm: {metric: (n list, sum list)}} for fast resampling."""
    return {arm: {m: ([a[key][0] if key in a else 0 for a in accs], [a[key][1] if key in a else 0.0 for a in accs]) for m, key in METRICS.items()} for arm, accs in cell_accs.items()}


def summarise(cell_accs, boots, seed=SEED_BOOT):
    """One resample of the cells is shared by every arm, statistic and contrast."""
    vectors = cell_levels(cell_accs)
    n_cells = len(next(iter(cell_accs.values())))
    point = effects(resampled_levels(vectors, [1] * n_cells))
    rng, draws = random.Random(seed), []
    for _ in range(boots):
        counts = [0] * n_cells
        for _ in range(n_cells):
            counts[rng.randrange(n_cells)] += 1
        draws.append(effects(resampled_levels(vectors, counts)))
    return {'cells': n_cells, 'point': point, 'ci95': {k: interval([d[k] for d in draws]) for k in point}}


# ---- the pre-registered classifications ----
def classify_cause(point, ci, direction, contrast):
    """Which of the three causes carries the change of err across a contrast: eligible = change significant with the sign of the err change; main = eligible with a share >= 0.5."""
    key = f'contrast|{direction}|{contrast}|'
    if not excludes_zero(ci[key + 'med_err']):
        return {'class': 'no_err_change', 'main': [], 'eligible': [], 'flags': []}
    sign = point[key + 'med_err'] > 0
    eligible = [c for c in MAIN_CAUSES if excludes_zero(ci[key + f'cause_{c}']) and (point[key + f'cause_{c}'] > 0) == sign]
    main = [c for c in eligible if point[f'share|{direction}|{contrast}|cause_{c}'] >= 0.5]
    flags = [f'opposed({c})' for c in MAIN_CAUSES if excludes_zero(ci[key + f'cause_{c}']) and (point[key + f'cause_{c}'] > 0) != sign]
    if len(main) == 1:
        label = f'single({main[0]})'
    elif main:
        label = f"multiple({', '.join(main)})"
    elif len(eligible) > 1:
        label = f"distributed({', '.join(eligible)})"
    elif eligible:
        label = f'partial({eligible[0]})'
    else:
        label = 'none'
    return {'class': label, 'main': main, 'eligible': eligible, 'flags': flags}


def stale_split(point, direction, contrast):
    """Which part of the stale change is larger: the first impression ('prior') or the lag."""
    share = lambda part: point[f'share|{direction}|{contrast}|cause_{part}']
    shares = {'prior': share('prior'), 'lag': share('lag')}
    stale = share('stale')
    main = [p for p, s in shares.items() if stale and s is not None and s / stale >= 0.5]
    return {'shares': shares, 'main': main}


def classify_hurt(ci, direction, metric):
    """'Too little rest hurts' for one metric across KN: hurts (upper < 0), helps (lower > 0) or flat."""
    pair = ci[f'contrast|{direction}|KN|{metric}']
    return 'hurts' if pair[1] < 0 else ('helps' if pair[0] > 0 else 'flat')


# ---- the run ----
def summarise_job(job):
    scope, cell_accs, boots = job
    return scope, summarise(cell_accs, boots)


def run(out, root, boots=1000, workers=None):
    out.mkdir(parents=True, exist_ok=False)
    start = perf_counter()
    gates = {'sim_unchanged_vs_frozen_t037_bundle': check_sim_unchanged(root), 'recorder_read_only': check_recorder_is_read_only()}
    print('gates', json.dumps(gates), flush=True)
    refs = {t: {(c['world'], c['seed'], c['start_age']): c for c in json.loads((root / f'artifacts/{t}/run/summary.json').read_text())['cells']} for t in SETTINGS}
    tasks = [(t, name, seed, st) for t in SETTINGS for name in t023.conditions() for seed in SEEDS for st in STARTS]
    cells = {t: [] for t in SETTINGS}
    equal = {t: 0 for t in SETTINGS}
    with ProcessPoolExecutor(max_workers=workers or os.cpu_count()) as pool:
        for count, ((setting, name, seed, st), got) in enumerate(pool.map(run_cell, tasks, chunksize=1), start=1):
            cells[setting].append(got)
            legacy, ref = got['fwd_base']['flat'], refs[setting][(name, seed, st)]['on']
            assert {k[6:]: v for k, v in legacy.items() if k.startswith('final_')} == ref['final'] and legacy['events'] == ref['events'], (setting, name, seed, st)  # gate 2
            centered, plain = got['rev_base']['flat'], got['plain_centered']
            assert centered == plain['flat'] and got['rev_base']['acc'][('start', 'top2')][0] == plain['decisions'], (setting, name, seed, st)  # gate 2
            equal[setting] += 1
            if count % 12 == 0:
                print(count, len(tasks), round(perf_counter() - start), flush=True)
    gates['base_arms_equal_plain_runs'] = equal
    scopes = {'all': [c for t in SETTINGS for c in cells[t]], **cells}
    pooled = {scope: {arm: merged(c[arm]['acc'] for c in group) for arm in ARMS} for scope, group in scopes.items()}
    gates['t037_reproduced'] = check_t037_reproduced(root, {t: {arm: pooled[t][arm] for arm in ('fwd_base', 'rev_base')} for t in SETTINGS})
    level = lambda acc, key: acc[key][1] / acc[key][0]
    gates['t043_reproduced'] = check_t043_reproduced(root, {scope: {arm: {m: level(acc, METRICS[m]) for m in REPRODUCED} for arm, acc in by_arm.items()} for scope, by_arm in pooled.items()})
    gates['invariants_validated_runs'] = len(tasks) * len(ARMS)
    gates['shadow_checks_total'] = {key: sum(cell[arm]['checked'][key] for t in SETTINGS for cell in cells[t] for arm in ARMS) for key in cells['t030'][0]['fwd_base']['checked']}
    jobs = [(scope, {arm: [c[arm]['acc'] for c in group] for arm in ARMS}, boots) for scope, group in scopes.items()]
    with ProcessPoolExecutor(max_workers=min(len(jobs), workers or os.cpu_count())) as pool:
        summaries = dict(pool.map(summarise_job, jobs))
    classes = {k: {f'{d}|{c}': classify_cause(s['point'], s['ci95'], d, c) for d in DIRECTIONS for c in CONTRASTS} for k, s in summaries.items()}
    stale = {k: {f'{d}|{c}': stale_split(s['point'], d, c) for d in DIRECTIONS for c in CONTRASTS} for k, s in summaries.items()}
    hurt = {k: {f'{d}|{m}': classify_hurt(s['ci95'], d, m) for d in DIRECTIONS for m in ('top2', 'final_skill')} for k, s in summaries.items()}
    result = {'task': 'T044', 'identity': le.league_identity(), 'runtime': runtime(), 'seeds': list(SEEDS), 'days': DAYS, 'sweep': list(t043.SWEEP), 'contrasts': CONTRASTS,
              'arms': {a: {'direction': d, 'kind': kind, 'parameter': param} for a, (d, kind, param) in ARMS.items()},
              'gates': gates, 'summaries': summaries, 'cause_class': classes, 'stale_split': stale, 'too_little_rest': hurt, 'elapsed_seconds': perf_counter() - start}
    write_json(out / 'summary.json', result)
    print(json.dumps({'equal': equal, 'cause_class': classes['all'], 'stale_split': stale['all'], 'too_little_rest': hurt['all'], 'elapsed_seconds': result['elapsed_seconds']}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=None)
    arguments = parser.parse_args()
    run(arguments.out.resolve(), CODE_ROOT, workers=arguments.workers)
