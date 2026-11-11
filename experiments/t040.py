"""T040: which of the coach's decisions carries the level shift of the observation noise? Analysis only: `soccer_sim/` is unchanged and no config is added.
Each coach's estimates are rebuilt exactly from the recorded observation events twice: as the world's own noise mode gives them (`real`) and as the other mode would give them from the same observations (`alt`).
For one decision type at a time (lineup / transfer / allocation = observe+scout candidates / advice = propose candidates) the decision function reads the `alt` estimates instead of the real ones.
Forward arms: legacy world, centered_v1 estimates in the decision. Reverse arms: centered_v1 world, legacy estimates. True skills are read by the researcher after the fact; nothing goes back into the simulation.

Protocol: docs/experiments/T040-PROTOCOL.md. Cells: 14 worlds x seeds {100,101,102} x start age {29,33}, 168 days, three settings x 12 arms (+ one plain centered_v1 run per cell for the gates).
"""
import argparse
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
import json
import os
from pathlib import Path
import random
import sys
from time import perf_counter

CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT))
from experiments import t023, t030, t036, t038, t039
from soccer_sim import league_economy as le
from soccer_sim.match_2d import clamp, round6
from soccer_sim.storage import digest, runtime, write_json

DAYS, SEEDS, STARTS, SETTINGS = t036.DAYS, t036.SEEDS, t036.STARTS, t036.SETTINGS
CHANNELS = ('lineup', 'transfer', 'allocation', 'advice')
SINGLE_AND_ALL = CHANNELS + ('all',)
# direction -> (the world's noise mode, the mode whose estimates the decisions read)
DIRECTIONS = {'fwd': ('legacy', 'centered_v1'), 'rev': ('centered_v1', 'legacy')}
ARMS = {f'{d}_{x}': (d, () if x == 'base' else CHANNELS if x == 'all' else (x,)) for d in DIRECTIONS for x in ('base',) + SINGLE_AND_ALL}
OBS_EVENTS = {'coach_session_observation': 'session', 'coach_match_observation': 'match', 'coach_scout_observation': 'scout'}
NOISE_KEY = {'session': 'coach_obs', 'match': 'match_obs', 'scout': 'scout_obs'}
FAT_CHANNEL = {'training_coach': 'session', 'match_coach': 'match', 'scout': 'scout'}
PROMO = ('played', 'benched', 'benched_top2', 'played_not_top2')
FINALS = ('skill', 'satisfaction', 'trust', 'transfers')
CHOICES = ('observe', 'propose', 'scout', 'rest')
# metric id -> accumulator key; 'top2' is the primary one
METRICS = {'top2': ('start', 'top2'), **{f'promo_{k}': ('promo', k) for k in PROMO}, **{f'final_{k}': ('final', k) for k in FINALS},
           **{f'choice_{k}': ('choice', k) for k in CHOICES}, **{f'obs_{c}': ('obs', c) for c in OBS_EVENTS.values()}}
DAY_ONE_TOLERANCE = 2e-6
IDENTITY_PICKS = (('t030', 0, 100, 29), ('t030', 7, 102, 33), ('t031', 3, 101, 33), ('t031', 12, 100, 29), ('t032', 5, 102, 29), ('t032', 9, 101, 33))
FROZEN = t038.FROZEN
add, merge, stat, top2, interval = t036.add, t036.merge, t036.stat, t038.top2, t038.interval
group_of = t039.group_of


# ---- the two estimate columns of every (coach, player) ----
def vector(ev):
    return [ev.skill_estimate, ev.match_rating_estimate, ev.fatigue_estimate, ev.method_residuals['A'], ev.method_residuals['B']]


def advance(config, vec, channel, obs, fatigue, residual=None):
    """One observation event applied to [skill, match, fatigue, residual A, residual B]; the simulation's own formulas (order of operations, clamps, round6).
    residual: (method, observed gain, the coach's base gain) for a practice observation of a training method."""
    skill, match, _, res_a, res_b = vec
    if channel == 'match':
        match = round6(clamp((1.0 - config.coach_match_alpha) * match + config.coach_match_alpha * obs, 0.10, 0.98))
        skill = round6(clamp((1.0 - config.coach_match_skill_blend) * skill + config.coach_match_skill_blend * obs, 0.10, 0.98))
    else:
        alpha = config.coach_skill_alpha if channel == 'session' else config.coach_scout_alpha
        skill = round6(clamp((1.0 - alpha) * skill + alpha * obs, 0.10, 0.98))
        if channel == 'scout' or config.coach_practice_rating != 'none':
            match = round6(clamp(0.70 * match + 0.30 * obs, 0.10, 0.98))
    if residual is not None:
        method, gain, base_gain = residual
        old = res_a if method == 'A' else res_b
        new = round6(clamp((1.0 - config.coach_residual_alpha) * old + config.coach_residual_alpha * (gain - base_gain), -0.05, 0.08))
        res_a, res_b = (new, res_b) if method == 'A' else (res_a, new)
    return [skill, match, fatigue, res_a, res_b]


class Tracker:
    """Read-only record of the observation events plus the view switch of the decision functions."""

    def __init__(self, world, view_mode):
        self.config, self.view_mode = world.config, view_mode
        self.cols = {(c.coach_id, pid): {'real': vector(ev), 'alt': vector(ev)} for c in world.coaches.values() for pid, ev in c.evaluations.items()}
        self.coach_ids = tuple(world.coaches)
        self.noise, self.fatigue, self.matches, self.pre_skill = [], [], {}, {}
        self.last_stream = None
        self.checked = dict.fromkeys(OBS_EVENTS.values(), 0)

    # -- observation events --
    def youth(self, pid):
        for coach_id in self.coach_ids:
            self.cols[(coach_id, pid)] = {'real': list(le.YOUTH_PRIOR) + [0.0, 0.0], 'alt': list(le.YOUTH_PRIOR) + [0.0, 0.0]}

    def observed_values(self, world, channel, pid, data, noise_real, noise_alt):
        """(observation, gain, method) for the real and the alt noise; asserts the real ones equal the recorded event exactly."""
        player = world.players[pid]
        if channel == 'match':
            match = self.matches[data['match_id']]
            stats = match['player_stats'][pid]
            home_won, away_won = match['home_goals'] > match['away_goals'], match['away_goals'] > match['home_goals']
            club_won = home_won if player.club_id == match['home_club'] else away_won
            raw = player.composite_skill() + 0.06 * stats['goals'] + 0.015 * stats['passes_completed'] + 0.02 * stats['tackles_won'] + (0.03 if club_won else -0.01)
            lo, hi, recorded = 0.15, 0.98, data['rating_obs']
        else:
            raw = player.composite_skill()
            lo, hi, recorded = 0.10, 0.98, data['obs_skill']
        real, alt = (round6(clamp(raw + n, lo, hi)) for n in (noise_real, noise_alt))
        assert real == recorded, (channel, pid, real, recorded)
        gains, method = (None, None), data.get('action')
        if channel == 'session' and method in le.TRAINING_METHODS:
            true_gain = round6(raw - self.pre_skill[pid])
            gains = tuple(round6(true_gain + n * 0.35) for n in (noise_real, noise_alt))
            assert gains[0] == data['obs_gain'], (pid, gains[0], data['obs_gain'])
        return (real, alt), gains, method

    def observe(self, world, event):
        data, channel = event['data'], OBS_EVENTS[event['kind']]
        pid, coach_id = (data['target_player_id'] if channel == 'scout' else data['player_id']), event['actor']
        stream, noise_real, noise_alt = self.noise.pop(0)
        observer, player, fat_channel, fatigue_real, fatigue_alt = self.fatigue.pop(0)
        assert stream == NOISE_KEY[channel] and (observer, player, fat_channel, fatigue_real) == (coach_id, pid, channel, data['obs_fatigue']), (event['id'], stream, observer, player, fat_channel)
        obs, gains, method = self.observed_values(world, channel, pid, data, noise_real, noise_alt)
        col, base_gain = self.cols[(coach_id, pid)], (world.coaches[coach_id].base_method_gains[method] if gains[0] is not None else None)
        for i, name, fatigue in ((0, 'real', fatigue_real), (1, 'alt', fatigue_alt)):
            col[name] = advance(self.config, col[name], channel, obs[i], fatigue, None if gains[i] is None else (method, gains[i], base_gain))
        assert col['real'] == vector(world.coaches[coach_id].evaluations[pid]), (event['id'], col['real'])
        self.checked[channel] += 1

    # -- the view switch --
    @contextmanager
    def viewing(self, coach):
        """While active the coach's evaluations hold the `alt` estimates; they are restored on exit, also after an exception."""
        saved = []
        try:
            for pid, ev in coach.evaluations.items():
                saved.append((ev, vector(ev)))
                alt = self.cols[(coach.coach_id, pid)]['alt']
                ev.skill_estimate, ev.match_rating_estimate, ev.fatigue_estimate = alt[:3]
                ev.method_residuals['A'], ev.method_residuals['B'] = alt[3:]
            yield
        finally:
            for ev, vec in saved:
                ev.skill_estimate, ev.match_rating_estimate, ev.fatigue_estimate = vec[:3]
                ev.method_residuals['A'], ev.method_residuals['B'] = vec[3:]


def candidate_rank(c):
    """The simulation's own tie-break of decide_coach_training."""
    return (c['score'], 1 if c['kind'] == 'propose' else (0 if c['kind'] == 'observe' else -1), str(c['target'] or ''), str(c['method'] or ''))


def compose_training(base, alt, use_alt_allocation, use_alt_advice):
    """observe/scout candidates come from `alt` when allocation is switched, propose candidates when advice is; rest is always the plain one. Then the same arg-max."""
    assert len(base['candidates']) == len(alt['candidates'])
    candidates = []
    for plain, other in zip(base['candidates'], alt['candidates']):
        assert (plain['kind'], plain['target'], plain['method']) == (other['kind'], other['target'], other['method'])
        use_alt = use_alt_allocation if plain['kind'] in ('observe', 'scout') else use_alt_advice if plain['kind'] == 'propose' else False
        candidates.append(other if use_alt else plain)
    return {**base, 'chosen': max(candidates, key=candidate_rank), 'candidates': candidates}


@contextmanager
def installed(tracker, channels):
    """Read-only recorders of the noise, the fatigue observation, the events and the day's starting skills, plus the decision wrappers of the switched channels. Everything is put back on exit."""
    names = ('uniform_from_key', 'observation_noise', 'observe_fatigue', '_append_event', '_step_training_day', 'decide_club_lineup',
             'decide_transfer_release', 'decide_transfer_acquisition', 'decide_coach_training')
    original = {n: getattr(le, n) for n in names}
    view = tracker.view_mode

    def uniform(*args, **kw):
        tracker.last_stream = args[2]
        return original['uniform_from_key'](*args, **kw)

    def noise(u, width, mode):
        got = original['observation_noise'](u, width, mode)
        if tracker.last_stream in NOISE_KEY.values():
            tracker.noise.append((tracker.last_stream, got, original['observation_noise'](u, width, view)))
        tracker.last_stream = None
        return got

    def fatigue(**kw):
        got = original['observe_fatigue'](**kw)
        if kw['context'] in FAT_CHANNEL:
            tracker.fatigue.append((kw['observer_id'], kw['player_id'], FAT_CHANNEL[kw['context']], got, original['observe_fatigue'](**{**kw, **le.fatigue_mode(view)})))
        return got

    def append(world, **kw):
        event_id = original['_append_event'](world, **kw)
        if kw['kind'] in OBS_EVENTS:
            tracker.observe(world, world.events[-1])
        elif kw['kind'] == 'league_match_completed':
            tracker.matches[kw['data']['match_id']] = kw['data']
        elif kw['kind'] == 'season_youth_joined':
            tracker.youth(kw['data']['player_id'])
        return event_id

    def training_day(world, day):
        tracker.pre_skill = {pid: p.composite_skill() for pid, p in world.players.items()}
        return original['_step_training_day'](world, day)

    def lineup(*, coach, roster, config):
        if 'lineup' in channels and sys._getframe(1).f_code.co_name == '_step_matchday':  # the transfer decisions also call it
            with tracker.viewing(coach):
                return original['decide_club_lineup'](coach=coach, roster=roster, config=config)
        return original['decide_club_lineup'](coach=coach, roster=roster, config=config)

    def transfer(name):
        def wrapped(**kw):
            if 'transfer' not in channels:
                return original[name](**kw)
            with tracker.viewing(kw['coach']):
                return original[name](**kw)
        return wrapped

    def training(**kw):
        plain = original['decide_coach_training'](**kw)
        switched = [c for c in ('allocation', 'advice') if c in channels]
        if not switched:
            return plain
        with tracker.viewing(kw['coach']):
            other = original['decide_coach_training'](**kw)
        return compose_training(plain, other, 'allocation' in switched, 'advice' in switched)

    replaced = {'uniform_from_key': uniform, 'observation_noise': noise, 'observe_fatigue': fatigue, '_append_event': append, '_step_training_day': training_day}
    if channels:
        replaced.update({'decide_club_lineup': lineup, 'decide_transfer_release': transfer('decide_transfer_release'),
                         'decide_transfer_acquisition': transfer('decide_transfer_acquisition'), 'decide_coach_training': training})
    try:
        for name, function in replaced.items():
            setattr(le, name, function)
        yield
    finally:
        for name, function in original.items():
            setattr(le, name, function)


# ---- one run ----
def record_day(acc, events, truth_of, prev):
    """The per-decision statistics of one day's new events (same definitions as T037/T039)."""
    for e in events:
        kind, d = e['kind'], e['data']
        if kind == 'coach_training_decision':
            for choice in CHOICES:
                add(acc, ('choice', choice), float(d['chosen']['kind'] == choice))
        if kind != 'lineup_decision':
            continue
        club = d['club_id']
        prev_roster, prev_starters = prev.get(club, (frozenset(), frozenset()))
        rows = [(r['player_id'], group_of(r['player_id'], prev_roster, prev_starters), truth_of(r['player_id'])) for r in d['ranking']]
        true_set, starters = top2([(pid, truth) for pid, _, truth in rows]), set(d['starters'])
        add(acc, ('start', 'top2'), float(starters == true_set))
        for pid, group, _ in rows:
            if group == 'arrived':
                continue
            started = float(pid in starters)
            add(acc, ('promo', group), started)
            if group == 'benched' and pid in true_set:
                add(acc, ('promo', 'benched_top2'), started)
            if group == 'played' and pid not in true_set:
                add(acc, ('promo', 'played_not_top2'), started)
        prev[club] = (frozenset(pid for pid, _, _ in rows), frozenset(starters))


def play(config, seed, view_mode, channels, days=DAYS, observer=None):
    world = le._build_initial_world(seed=seed, config=config)
    tracker = Tracker(world, view_mode)
    with installed(tracker, channels):
        while world.day < days:
            n_events = len(world.events)
            le.advance_league_days(world, 1)
            if observer:
                observer(world, world.events[n_events:])
    assert not tracker.noise and not tracker.fatigue, (len(tracker.noise), len(tracker.fatigue))  # every recorded draw was matched to an event
    return world, tracker


def run_arm(setting, name, seed, start, arm, days=DAYS, identity_view=False):
    direction, channels = ARMS[arm]
    world_mode, view_mode = DIRECTIONS[direction]
    config = t036.make_cfg(setting, name, start, world_mode)
    acc, prev = {}, {}

    def observer(world, events):
        truth = {}
        record_day(acc, events, lambda pid: truth.setdefault(pid, world.players[pid].composite_skill()), prev)

    world, tracker = play(config, seed, world_mode if identity_view else view_mode, channels, days, observer)
    le._validate_league_invariants(world)
    flat = {f'final_{k}': v for k, v in t030.final_metrics(world).items()}
    flat['events'] = len(world.events)
    for key in FINALS:
        add(acc, ('final', key), flat[f'final_{key}'])
    for channel in OBS_EVENTS.values():
        add(acc, ('obs', channel), float(tracker.checked[channel]))
    return {'acc': acc, 'flat': flat, 'checked': tracker.checked, 'world': world}


def plain_digest(world):
    return digest({k: v for k, v in le._snapshot_payload_unchecked(world).items() if k != 'identity'})


def day_one_deviation(setting, name, seed, start):
    """Gate 4: after day 1 the `alt` column equals the other mode's plain world (day-1 decisions are identical in both modes). Returns the worst deviation over both directions."""
    worst = 0.0
    for world_mode, view_mode in DIRECTIONS.values():
        mine, tracker = play(t036.make_cfg(setting, name, start, world_mode), seed, view_mode, (), days=1)
        other = le._build_initial_world(seed=seed, config=t036.make_cfg(setting, name, start, view_mode))
        le.advance_league_days(other, 1)
        for (coach_id, pid), col in tracker.cols.items():
            worst = max(worst, max(abs(a - b) for a, b in zip(col['alt'], vector(other.coaches[coach_id].evaluations[pid]))))
    return worst


def run_cell(task):
    setting, name, seed, start = task
    out = {}
    for arm in ARMS:
        result = run_arm(setting, name, seed, start, arm)
        out[arm] = {'acc': result['acc'], 'flat': result['flat'], 'checked': result['checked']}
    plain = le.run_league_world(seed=seed, days=DAYS, config=t036.make_cfg(setting, name, start, 'centered_v1'))
    out['plain_centered'] = {'flat': {**{f'final_{k}': v for k, v in t030.final_metrics(plain).items()}, 'events': len(plain.events)},
                             'decisions': sum(1 for e in plain.events if e['kind'] == 'lineup_decision')}
    out['day_one'] = day_one_deviation(setting, name, seed, start)
    return task, out


# ---- gates ----
check_sim_unchanged = t038.check_sim_unchanged


def check_identity_view(days=DAYS):
    """Gate 5: with the view set to the world's own mode, switching all four channels reproduces the plain world's whole snapshot (both directions)."""
    names, out = t023.conditions(), {}
    for setting, index, seed, start in IDENTITY_PICKS:
        for direction, (world_mode, _) in DIRECTIONS.items():
            config = t036.make_cfg(setting, names[index], start, world_mode)
            plain = le.run_league_world(seed=seed, days=days, config=config)
            result = run_arm(setting, names[index], seed, start, f'{direction}_all', days=days, identity_view=True)
            assert plain_digest(result['world']) == plain_digest(plain), (setting, names[index], seed, start, direction)
            out[f'{setting}_{index}_{seed}_{start}_{direction}'] = True
    return {'runs': len(out), 'all_equal': True}


def check_t037_reproduced(root, pooled_base):
    """Gate 6: the top-2 match rate (n, mean) of the two base arms equals T037's saved value, per setting."""
    saved = json.loads((root / 'artifacts/t037/run/summary.json').read_text())['settings']
    out = {}
    for setting, by_arm in pooled_base.items():
        for arm, mode in (('fwd_base', 'legacy'), ('rev_base', 'centered_v1')):
            got, want = stat(by_arm[arm][('start', 'top2')]), saved[setting]['pooled'][mode]['start']['true_top2_match']
            out[f'{setting}_{mode}'] = got['n'] == want['n'] and abs(got['mean'] - want['mean']) <= 1e-9
    assert all(out.values()), [k for k, v in out.items() if not v]
    return {'compared': len(out), 'all_equal': True}


# ---- statistics ----
def level(acc, key):
    r = acc.get(key)
    return r[1] / r[0] if r and r[0] else None


def sub(a, b):
    return None if a is None or b is None else a - b


def effects(v):
    """v[arm][metric] = level. D, F (sufficiency), R (necessity), the interaction I and, for the primary metric, the ratios to D."""
    out = {f'level|{arm}|{m}': v[arm][m] for arm in ARMS for m in METRICS}
    for m in METRICS:
        out[f'D|{m}'] = sub(v['rev_base'][m], v['fwd_base'][m])
        for x in SINGLE_AND_ALL:
            out[f'F|{x}|{m}'] = sub(v[f'fwd_{x}'][m], v['fwd_base'][m])
            out[f'R|{x}|{m}'] = sub(v['rev_base'][m], v[f'rev_{x}'][m])
        for side in ('F', 'R'):
            parts = [out[f'{side}|{x}|{m}'] for x in CHANNELS]
            out[f'I{side}|{m}'] = None if None in parts or out[f'{side}|all|{m}'] is None else out[f'{side}|all|{m}'] - sum(parts)
    d = out['D|top2']
    for x in SINGLE_AND_ALL:
        out[f'f|{x}'] = None if not d or out[f'F|{x}|top2'] is None else out[f'F|{x}|top2'] / d
        out[f'r|{x}'] = None if not d or out[f'R|{x}|top2'] is None else out[f'R|{x}|top2'] / d
    for side in ('F', 'R'):
        out[f'i{side}'] = None if not d or out[f'I{side}|top2'] is None else out[f'I{side}|top2'] / d
    return out


def cell_levels(cell_accs):
    """cell_accs[arm] = list of per-cell accumulators -> {arm: {metric: (n list, sum list)}} for fast resampling."""
    return {arm: {m: ([a[key][0] if key in a else 0 for a in accs], [a[key][1] if key in a else 0.0 for a in accs]) for m, key in METRICS.items()} for arm, accs in cell_accs.items()}


def resampled_levels(vectors, counts):
    out = {}
    for arm, by_metric in vectors.items():
        out[arm] = {}
        for m, (n, s) in by_metric.items():
            total = sum(c * x for c, x in zip(counts, n))
            out[arm][m] = sum(c * x for c, x in zip(counts, s)) / total if total else None
    return out


def summarise(cell_accs, boots, seed=40):
    """cell_accs[arm] = per-cell accumulators in the same cell order for every arm. One resample of the cells is shared by every arm, statistic and ratio."""
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


def classify(point, ci):
    """The pre-registered attribution rule (docs/experiments/T040-PROTOCOL.md), first match wins, plus the flags."""
    if ci['D|top2'] is None or ci['D|top2'][0] <= 0:
        return {'class': 'no_gain', 'carriers': [], 'main_routes': [], 'flags': []}
    significant = lambda key: ci[key] is not None and ci[key][0] > 0
    carriers = [x for x in CHANNELS if significant(f'F|{x}|top2') and significant(f'R|{x}|top2')]
    main = [x for x in carriers if point[f'f|{x}'] >= 0.5 and point[f'r|{x}'] >= 0.5]
    flags = []
    if any(abs(point[f'i{s}']) >= 0.25 and ci[f'I{s}|top2'][0] * ci[f'I{s}|top2'][1] > 0 for s in ('F', 'R')):
        flags.append('interaction')
    flags += [f'harm({x})' for x in CHANNELS if any(ci[f'{s}|{x}|top2'][1] < 0 for s in ('F', 'R'))]
    if ci['f|all'][1] < 0.5 and ci['r|all'][1] < 0.5:
        label = 'outside_coach_estimates'
    elif len(main) == 1:
        label = f'single_route({main[0]})'
    elif len(main) > 1:
        label = f'multiple_routes({", ".join(main)})'
    elif len(carriers) > 1:
        label = f'distributed({", ".join(carriers)})'
    elif carriers:
        label = f'partial_single({carriers[0]})'
    elif significant('F|all|top2') or significant('R|all|top2'):
        label = 'only_jointly'
    else:
        label = 'unattributed'
    return {'class': label, 'carriers': carriers, 'main_routes': main, 'flags': flags}


def recommend(attribution, point, ci, positive_settings):
    """The pre-registered default-change recommendation (proposal only). positive_settings: how many settings have a positive point D."""
    skill_ok, trust_ok = ci['D|final_skill'][0] >= 0, ci['D|final_trust'][0] >= 0
    if ci['D|top2'][0] <= 0 or not skill_ok:
        return 'hold'
    attributed = attribution['class'].split('(')[0] in ('single_route', 'multiple_routes', 'distributed', 'partial_single')
    harm = any(f.startswith('harm') for f in attribution['flags'])
    if attributed and point['f|all'] >= 0.5 and positive_settings == len(SETTINGS) and skill_ok and trust_ok and not harm:
        return 'recommend'
    return 'conditional'


def run(out, root, boots=1000, workers=None):
    out.mkdir(parents=True, exist_ok=False)
    start = perf_counter()
    gates = {'sim_unchanged_vs_frozen_t037_bundle': check_sim_unchanged(root)}
    gates['identity_view'] = check_identity_view()
    print('gates', json.dumps(gates), flush=True)
    refs = {t: {(c['world'], c['seed'], c['start_age']): c for c in json.loads((root / f'artifacts/{t}/run/summary.json').read_text())['cells']} for t in SETTINGS}
    tasks = [(t, name, seed, st) for t in SETTINGS for name in t023.conditions() for seed in SEEDS for st in STARTS]
    cells = {t: [] for t in SETTINGS}
    equal, worst_day_one = {t: 0 for t in SETTINGS}, 0.0
    with ProcessPoolExecutor(max_workers=workers or os.cpu_count()) as pool:
        for count, ((setting, name, seed, st), got) in enumerate(pool.map(run_cell, tasks, chunksize=1), start=1):
            cells[setting].append(got)
            legacy, ref = got['fwd_base']['flat'], refs[setting][(name, seed, st)]['on']
            assert {k[6:]: v for k, v in legacy.items() if k.startswith('final_')} == ref['final'] and legacy['events'] == ref['events'], (setting, name, seed, st)  # gate 2
            centered, plain = got['rev_base']['flat'], got['plain_centered']
            assert centered == plain['flat'] and got['rev_base']['acc'][('start', 'top2')][0] == plain['decisions'], (setting, name, seed, st)  # gate 2
            equal[setting] += 1
            worst_day_one = max(worst_day_one, got['day_one'])
            if count % 12 == 0:
                print(count, len(tasks), round(perf_counter() - start), flush=True)
    assert worst_day_one <= DAY_ONE_TOLERANCE, worst_day_one
    gates['base_arms_equal_plain_runs'] = equal
    gates['day_one_alt_equals_other_mode_max_deviation'] = worst_day_one
    gates['t037_reproduced'] = check_t037_reproduced(root, {t: {arm: merged(c[arm]['acc'] for c in cells[t]) for arm in ('fwd_base', 'rev_base')} for t in SETTINGS})
    gates['invariants_validated_runs'] = len(tasks) * len(ARMS)
    summaries = {'all': summarise({arm: [c[arm]['acc'] for t in SETTINGS for c in cells[t]] for arm in ARMS}, boots),
                 **{t: summarise({arm: [c[arm]['acc'] for c in cells[t]] for arm in ARMS}, boots) for t in SETTINGS}}
    attribution = {k: classify(s['point'], s['ci95']) for k, s in summaries.items()}
    positive = sum(1 for t in SETTINGS if summaries[t]['ci95']['D|top2'][0] > 0)
    result = {'task': 'T040', 'identity': le.league_identity(), 'runtime': runtime(), 'seeds': list(SEEDS), 'days': DAYS, 'arms': {a: {'direction': d, 'channels': list(c)} for a, (d, c) in ARMS.items()},
              'gates': gates, 'summaries': summaries, 'attribution': attribution, 'settings_with_positive_D': positive,
              'recommendation': recommend(attribution['all'], summaries['all']['point'], summaries['all']['ci95'], sum(1 for t in SETTINGS if summaries[t]['point']['D|top2'] > 0)),
              'tracker_events_checked': {arm: {c: sum(cell[arm]['checked'][c] for t in SETTINGS for cell in cells[t]) for c in OBS_EVENTS.values()} for arm in ARMS},
              'elapsed_seconds': perf_counter() - start}
    write_json(out / 'summary.json', result)
    print(json.dumps({'equal': equal, 'attribution': attribution['all'], 'recommendation': result['recommendation'], 'elapsed_seconds': result['elapsed_seconds']}), flush=True)


def merged(accs):
    out = {}
    for acc in accs:
        merge(out, acc)
    return out


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=None)
    arguments = parser.parse_args()
    run(arguments.out.resolve(), CODE_ROOT, workers=arguments.workers)
