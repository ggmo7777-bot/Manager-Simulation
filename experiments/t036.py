"""T036: observation_noise_mode ("legacy" default / "centered_v1"). The same keyed draws; only the noise offset differs. Compares the frozen T030/T031/T032 settings x 84 cells
under both modes. True skills / fatigue are read by the researcher after the fact; nothing goes back into the simulation.

Protocol: docs/experiments/T036-PROTOCOL.md. Cells: 14 worlds x seeds {100,101,102} x start age {29,33}, 168 days, three settings x two modes (504 runs).
"""
import argparse
import ast
from dataclasses import replace
import functools
import hashlib
import json
from math import sqrt
from pathlib import Path
import subprocess
import sys
import tempfile
from time import perf_counter

CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT))
from experiments import t023, t030, t031, t033
from soccer_sim import league_economy as le, league_observation as lo
from soccer_sim.storage import digest, runtime, write_json

L, DAYS, SEEDS, STARTS, SETTINGS = t033.L, t033.DAYS, t033.SEEDS, t033.STARTS, t033.SETTINGS
MODES = ('legacy', 'centered_v1')
FAT_CTX = ('training_self', 'match_self', 'training_coach', 'scout', 'match_coach')
FAT_WIDTH = {'training_self': 'self_fatigue_noise', 'match_self': 'self_fatigue_noise', 'training_coach': 'coach_fatigue_noise', 'scout': 'scout_fatigue_noise', 'match_coach': 'match_fatigue_noise'}
SKILL_WIDTH = {'session': 'coach_noise', 'scout': 'scout_noise', 'self': 'self_noise'}
OBS_KINDS = {'coach_session_observation': 'session', 'coach_scout_observation': 'scout', 'coach_match_observation': 'match', 'player_self_observation': 'self'}
FROZEN = 'artifacts/t035/source_bundle'
mean, sign_test = t030.mean, t031.sign_test


def make_cfg(setting, name, start, mode):
    return replace(SETTINGS[setting](name, start), observation_noise_mode=mode)


# ---- accumulators: key -> [n, sum, sum_abs, sum_sq, flag_count] ----
def add(acc, key, x, flag=0.0):
    r = acc.setdefault(key, [0, 0.0, 0.0, 0.0, 0.0])
    r[0] += 1; r[1] += x; r[2] += abs(x); r[3] += x * x; r[4] += flag


def merge(a, b):
    for k, v in b.items():
        r = a.setdefault(k, [0, 0.0, 0.0, 0.0, 0.0])
        for i in range(5):
            r[i] += v[i]


def stat(r):
    if not r or not r[0]:
        return None
    n = r[0]
    m = r[1] / n
    return {'n': n, 'mean': m, 'mae': r[2] / n, 'sd': sqrt(max(0.0, r[3] / n - m * m)), 'flag_share': r[4] / n}


# ---- one cell ----
def run_cell(config, seed, days=DAYS):
    world = le._build_initial_world(seed=seed, config=config)
    w = (config.select_skill_weight, config.select_match_weight)
    acc, newc, counts = {}, {}, {}
    original = lo.observe_fatigue

    def recording(**kw):
        got = original(**kw)
        add(acc, ('fat', kw['context']), got - kw['true_fatigue'], float(got in (0.0, 0.95)))
        if kw['true_fatigue'] >= kw['noise'] and kw['true_fatigue'] + kw['noise'] <= 0.95:  # clamping cannot happen (the pre-registered "clamp excluded" set)
            add(acc, ('fat_free', kw['context']), got - kw['true_fatigue'])
        if kw['true_fatigue'] >= 3 * kw['noise'] and kw['true_fatigue'] + kw['noise'] <= 0.95:  # post hoc (added after the first full run): also the legacy range [-3 w, w] cannot clamp
            add(acc, ('fat_free_strict', kw['context']), got - kw['true_fatigue'])
        return got
    le.observe_fatigue = recording
    try:
        while world.day < days:
            n_events = len(world.events)
            le.advance_league_days(world, 1)
            new = world.events[n_events:]
            match_day = any(e['kind'] == 'league_match_completed' for e in new)
            truth = {}

            def true_skill(pid):
                if pid not in truth:
                    truth[pid] = world.players[pid].composite_skill()
                return truth[pid]
            for e in new:
                kind, d = e['kind'], e['data']
                if kind == 'season_youth_joined':
                    newc[d['player_id']] = {'club': d['club_id'], 'day': world.day}
                if kind in OBS_KINDS:
                    k = OBS_KINDS[kind]
                    counts[k] = counts.get(k, 0) + 1
                if kind in ('coach_session_observation', 'coach_scout_observation') or (kind == 'player_self_observation' and not match_day):
                    k = OBS_KINDS[kind]
                    pid = d['target_player_id'] if k == 'scout' else d['player_id']
                    obs = d['obs_skill']
                    add(acc, ('obs', k), obs - true_skill(pid), float(obs <= 0.10 or obs >= 0.98))
                    if k != 'self':
                        own = pid in newc and le.CLUB_COACH_MAP[newc[pid]['club']] == e['actor'] and 0 < e['day'] - newc[pid]['day'] <= L
                        add(acc, ('est_after', k, 'new' if own else 'inc'), d['skill_estimate'] - true_skill(pid))
                elif kind == 'lineup_decision':
                    ranking = d['ranking']
                    top2 = sorted((r['player_id'] for r in ranking), key=lambda p: (-true_skill(p), p))[:2]
                    add(acc, ('start', 'true_top2_match'), float(set(d['starters']) == set(top2)))
                    for r in ranking:
                        pid = r['player_id']
                        since = world.day - newc[pid]['day'] if pid in newc and newc[pid]['club'] == d['club_id'] else None
                        groups = ['inc'] if pid not in newc else (['new', f'new_t{(since - 1) // 7}'] if since is not None and 0 < since <= L else [])
                        for g in groups:
                            add(acc, ('lu', g, 'skill'), r['components']['skill_term'] / w[0] - true_skill(pid))
                            add(acc, ('lu', g, 'match'), r['components']['match_term'] / w[1] - true_skill(pid))
                            if g == 'new':
                                add(acc, ('start', 'new'), float(pid in d['starters']))
    finally:
        le.observe_fatigue = original
    le._validate_league_invariants(world)
    flat = {f'final_{k}': v for k, v in t030.final_metrics(world).items()}
    flat['events'] = len(world.events)
    flat.update({f'obs_events_{k}': counts.get(k, 0) for k in ('session', 'scout', 'match', 'self')})
    flat['start_true_top2_match'] = (stat(acc.get(('start', 'true_top2_match'))) or {}).get('mean')
    flat['start_new_share'] = (stat(acc.get(('start', 'new'))) or {}).get('mean')
    for g in ('inc', 'new'):
        for m in ('skill', 'match'):
            s = stat(acc.get(('lu', g, m)))
            flat[f'lu_{g}_{m}_bias'], flat[f'lu_{g}_{m}_mae'] = (s['mean'], s['mae']) if s else (None, None)
    return {'acc': acc, 'flat': flat}


# ---- gates ----
def check_sim_frozen_bundle(root):
    """The frozen T035 bundle is the pre-T036 simulation: only league_economy.py and league_observation.py may differ, and only by the T036 lines."""
    bundle = root / FROZEN / 'soccer_sim'
    names = sorted(p.name for p in (root / 'soccer_sim').glob('*.py'))
    assert names == sorted(p.name for p in bundle.glob('*.py'))
    return {'files': len(names), 'changed': [n for n in names if (root / 'soccer_sim' / n).read_bytes() != (bundle / n).read_bytes()]}


def count_calls(path):
    tree = ast.parse(path.read_text())
    return sum(1 for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, 'id', getattr(n.func, 'attr', None)) == 'uniform_from_key')


def check_rng_calls(root):
    out = {f: {'now': count_calls(root / 'soccer_sim' / f), 'frozen': count_calls(root / FROZEN / 'soccer_sim' / f)} for f in ('league_economy.py', 'league_observation.py')}
    assert all(v['now'] == v['frozen'] for v in out.values()), out
    return out


def day_one_keys(mode):
    """The (seed, tick, channel, token) of every keyed draw on day 1, per mode (before the histories can diverge)."""
    seen, orig_e, orig_o = [], le.uniform_from_key, lo.uniform_from_key

    def spy(original):
        def inner(seed, tick, channel, token, **kw):
            seen.append((seed, tick, channel, token))
            return original(seed, tick, channel, token, **kw)
        return inner
    le.uniform_from_key, lo.uniform_from_key = spy(orig_e), spy(orig_o)
    try:
        world = le._build_initial_world(seed=100, config=make_cfg('t032', t023.conditions()[0], 29, mode))
        le.advance_league_days(world, 1)
    finally:
        le.uniform_from_key, lo.uniform_from_key = orig_e, orig_o
    return seen


def check_rng_stream():
    a, b = day_one_keys('legacy'), day_one_keys('centered_v1')
    assert a == b and len(a) > 0, (len(a), len(b))
    worst = 0.0
    for i in range(1, 2001):
        u, width = (-1.0 + (i - 1) * 2.0 / 2000) , 0.024
        d = le.observation_noise(u, width, 'centered_v1') - le.observation_noise(u, width, 'legacy')
        worst = max(worst, abs(d - width * (1 - u)))
        f = lambda c: lo.observe_fatigue(true_fatigue=0.4, noise=0.05, seed=1, day=i, observer_id='c', player_id='p', context='x', centered=c)
        assert abs((f(True) - f(False)) - 0.05 * (1 - lo.uniform_from_key(1, i, 'fatigue:x', 'c:p'))) <= 2e-6  # round6; the 0.4 +- 0.15 stays inside the clamp
    assert worst <= 1e-15, worst
    return {'day1_keys': len(a), 'identical': True, 'max_dev_noise_difference': worst}


def frozen_hash(setting, name, seed, start, days):
    """sha256 of the whole snapshot payload (all but the identity block) computed by the frozen T035 source_bundle in another process."""
    code = ("import sys,json,hashlib;root=sys.argv[1];sys.path.insert(0,root)\n"
            "from experiments import t033\nfrom soccer_sim import league_economy as le\n"
            "w=le.run_league_world(seed=int(sys.argv[3]),days=int(sys.argv[5]),config=t033.SETTINGS[sys.argv[2]](sys.argv[4],int(sys.argv[6])))\n"
            "print(hashlib.sha256(json.dumps({k:v for k,v in le._snapshot_payload_unchecked(w).items() if k!='identity'},sort_keys=True).encode()).hexdigest())")
    got = subprocess.run([sys.executable, '-B', '-c', code, str(CODE_ROOT / FROZEN), setting, str(seed), name, str(days), str(start)], capture_output=True, text=True, check=True)
    return got.stdout.strip()


def local_hash(setting, name, seed, start, days, mode='legacy'):
    config = SETTINGS[setting](name, start) if mode == 'legacy' else make_cfg(setting, name, start, mode)
    world = le.run_league_world(seed=seed, days=days, config=config)
    payload = le._snapshot_payload_unchecked(world)
    assert ('observation_noise_mode' in payload['config']) == (mode != 'legacy')
    payload.pop('identity')  # the source hash differs by construction (this very change)
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def check_vs_frozen_bundle(days=DAYS):
    names = t023.conditions()
    picks = [('t030', names[0], 100, 29), ('t030', names[7], 102, 33), ('t031', names[3], 101, 33), ('t031', names[12], 100, 29), ('t032', names[5], 102, 29), ('t032', names[9], 101, 33)]
    for setting, name, seed, start in picks:
        assert frozen_hash(setting, name, seed, start, days) == local_hash(setting, name, seed, start, days), (setting, name, seed, start)
    return {'cells': len(picks), 'days': days, 'equal': True}


def check_config_and_saves(directory):
    cls = le.LeagueEconomyConfig
    base = SETTINGS['t032'](t023.conditions()[0], 29)
    assert 'observation_noise_mode' not in base.to_dict() and base.observation_noise_mode == 'legacy'
    centered = replace(base, observation_noise_mode='centered_v1')
    assert centered.to_dict()['observation_noise_mode'] == 'centered_v1' and cls.from_dict(centered.to_dict()) == centered and cls.from_dict(base.to_dict()) == base
    assert cls.has_save_fields(centered.to_dict()) and cls.has_save_fields(base.to_dict()) and not cls.has_save_fields({**base.to_dict(), 'unknown': 1})
    bad = {}
    for label, value in (('unknown', 'bogus'), ('int', 1), ('empty', ''), ('old_none', 'none')):
        try:
            replace(base, observation_noise_mode=value)
            bad[label] = False
        except ValueError:
            bad[label] = True
    assert all(bad.values()), bad
    world = le.run_league_world(seed=100, days=2 * L, config=centered)
    folder = Path(directory) / 'centered'
    le.save_league_artifacts(world, folder)
    for reader in (le.load_league_snapshot, le.load_league_snapshot_trusted):
        le._assert_same(reader(folder / 'snapshot.json'), world, 'world')
    child = le.fork_league_world(world, kind='player_trust', player_id='p3', coach_id='c1', value=0.05, reason='t036')
    le.advance_league_days(child, 30)
    stepped = le._build_initial_world(seed=100, config=centered)
    le.advance_league_days(stepped, 2 * L)
    le._assert_same(stepped, world, 'world')
    snap, manifest = folder / 'snapshot.json', folder / 'manifest.json'
    raw = json.loads(snap.read_text())
    raw['config']['observation_noise_mode'] = 'legacy'  # tampered: a centered world relabelled legacy must not load
    snap.write_text(json.dumps(raw))
    record = json.loads(manifest.read_text())
    record['snapshot_sha256'] = digest(raw)
    manifest.write_text(json.dumps(record))
    try:
        le.load_league_snapshot(snap)
        tamper = 'accepted'
    except ValueError:
        tamper = 'rejected'
    assert tamper == 'rejected'
    return {'invalid_values_rejected': bad, 'round_trip': True, 'fork_steps': len(child.interventions), 'relabelled_snapshot': tamper}


# ---- aggregation ----
def paired(rows, key):
    d = [b[key] - a[key] for a, b in rows if a.get(key) is not None and b.get(key) is not None]
    if not d:
        return None
    m = mean(d)
    sd = sqrt(sum((x - m) ** 2 for x in d) / (len(d) - 1)) if len(d) > 1 else 0.0
    return {'n': len(d), 'mean_legacy': mean(a[key] for a, b in rows if a.get(key) is not None and b.get(key) is not None),
            'mean_centered': mean(b[key] for a, b in rows if a.get(key) is not None and b.get(key) is not None),
            'mean_diff': m, 'sd_diff': sd, 't': (m / (sd / sqrt(len(d))) if sd > 0 else None), 'sign': sign_test(d)}


def summarise(pooled):
    out = {}
    for mode, acc in pooled.items():
        s = {'obs': {k: stat(acc.get(('obs', k))) for k in ('session', 'scout', 'self')},
             'fatigue': {c: stat(acc.get(('fat', c))) for c in FAT_CTX},
             'fatigue_clamp_free': {c: stat(acc.get(('fat_free', c))) for c in FAT_CTX},
             'fatigue_clamp_free_strict_posthoc': {c: stat(acc.get(('fat_free_strict', c))) for c in FAT_CTX},
             'est_after': {f'{k}_{g}': stat(acc.get(('est_after', k, g))) for k in ('session', 'scout') for g in ('inc', 'new')},
             'lineup': {f'{g}_{m}': stat(acc.get(('lu', g, m))) for g in ('inc', 'new', 'new_t0', 'new_t1', 'new_t2') for m in ('skill', 'match')},
             'start': {'true_top2_match': stat(acc.get(('start', 'true_top2_match'))), 'new_share': stat(acc.get(('start', 'new')))}}
        out[mode] = s
    return out


def predictions(summary, diffs):
    leg, cen = summary['legacy'], summary['centered_v1']
    g = lambda s, *p: functools.reduce(lambda a, k: a[k], p, s)
    out = {}
    out['P2_obs_error'] = {'legacy': {k: leg['obs'][k]['mean'] for k in leg['obs']}, 'centered': {k: cen['obs'][k]['mean'] for k in cen['obs']}}
    out['P2_fatigue_difference_all'] = {c: cen['fatigue'][c]['mean'] - leg['fatigue'][c]['mean'] for c in FAT_CTX}
    out['P2_fatigue_difference_clamp_free'] = {c: cen['fatigue_clamp_free'][c]['mean'] - leg['fatigue_clamp_free'][c]['mean'] for c in FAT_CTX}
    out['P2_fatigue_difference_strict_posthoc'] = {c: cen['fatigue_clamp_free_strict_posthoc'][c]['mean'] - leg['fatigue_clamp_free_strict_posthoc'][c]['mean'] for c in FAT_CTX}
    out['P3_incumbents'] = {'bias_legacy': g(leg, 'lineup', 'inc_skill', 'mean'), 'bias_centered': g(cen, 'lineup', 'inc_skill', 'mean'), 'mae_legacy': g(leg, 'lineup', 'inc_skill', 'mae'), 'mae_centered': g(cen, 'lineup', 'inc_skill', 'mae')}
    out['P4_newcomers'] = {'bias_legacy': g(leg, 'lineup', 'new_skill', 'mean'), 'bias_centered': g(cen, 'lineup', 'new_skill', 'mean'), 'mae_legacy': g(leg, 'lineup', 'new_skill', 'mae'), 'mae_centered': g(cen, 'lineup', 'new_skill', 'mae'),
                           'bias_15_21_legacy': g(leg, 'lineup', 'new_t2_skill', 'mean'), 'bias_15_21_centered': g(cen, 'lineup', 'new_t2_skill', 'mean')}
    out['P5_match_incumbents'] = {'bias_legacy': g(leg, 'lineup', 'inc_match', 'mean'), 'bias_centered': g(cen, 'lineup', 'inc_match', 'mean')}
    out['P6_start'] = {'true_top2_match_diff': diffs['start_true_top2_match']['mean_diff'], 'new_start_share_diff': diffs['start_new_share']['mean_diff']}
    out['P7_final'] = {k: {'mean_diff': diffs[f'final_{k}']['mean_diff'], 't': diffs[f'final_{k}']['t']} for k in ('skill', 'satisfaction', 'trust', 'transfers')}
    out['P8_obs_count'] = {'events_relative_change': diffs['events']['mean_centered'] / diffs['events']['mean_legacy'] - 1}
    return out


def gate3(summary, config=None):
    """Pre-registered gate 3: centred skill-observation errors |mean| <= 0.003, clamp-free fatigue errors |mean| <= 0.005; legacy skill errors -width +- 0.003."""
    width = {k: getattr(config or le.LeagueEconomyConfig(), v) for k, v in SKILL_WIDTH.items()}
    c, l = summary['centered_v1'], summary['legacy']
    out = {'centered_obs': {k: c['obs'][k]['mean'] for k in width}, 'legacy_obs_minus_expected': {k: l['obs'][k]['mean'] + width[k] for k in width},
           'centered_fatigue_clamp_free': {k: c['fatigue_clamp_free'][k]['mean'] for k in FAT_CTX}}
    out['ok'] = (all(abs(v) <= 0.003 for v in out['centered_obs'].values()) and all(abs(v) <= 0.003 for v in out['legacy_obs_minus_expected'].values())
                 and all(abs(v) <= 0.005 for v in out['centered_fatigue_clamp_free'].values()))
    return out


DIFF_KEYS = ('final_skill', 'final_satisfaction', 'final_trust', 'final_transfers', 'events', 'obs_events_session', 'obs_events_scout', 'obs_events_match', 'obs_events_self', 'start_true_top2_match', 'start_new_share',
             'lu_inc_skill_bias', 'lu_inc_skill_mae', 'lu_new_skill_bias', 'lu_new_skill_mae', 'lu_inc_match_bias', 'lu_inc_match_mae', 'lu_new_match_bias', 'lu_new_match_mae')


def run(out, root):
    out.mkdir(parents=True, exist_ok=False)
    start = perf_counter()
    gates = {'sim_vs_frozen_t035_bundle': check_sim_frozen_bundle(root), 'rng_call_counts': check_rng_calls(root), 'rng_stream': check_rng_stream(), 'vs_frozen_bundle': check_vs_frozen_bundle()}
    with tempfile.TemporaryDirectory() as tmp:
        gates['config_and_saves'] = check_config_and_saves(tmp)
    print('gates', json.dumps(gates)[:300], flush=True)
    refs = {t: {(c['world'], c['seed'], c['start_age']): c for c in json.loads((root / f'artifacts/{t}/run/summary.json').read_text())['cells']} for t in SETTINGS}
    result_settings, pooled_all, equal, rows_all = {}, {m: {} for m in MODES}, {t: 0 for t in SETTINGS}, {}
    for t in SETTINGS:
        pooled, rows = {m: {} for m in MODES}, []
        for name in t023.conditions():
            for seed in SEEDS:
                for st in STARTS:
                    cells = {m: run_cell(make_cfg(t, name, st, m), seed) for m in MODES}
                    ref = refs[t][(name, seed, st)]['on']
                    leg = cells['legacy']['flat']
                    assert {k[6:]: v for k, v in leg.items() if k.startswith('final_')} == ref['final'] and leg['events'] == ref['events'], (t, name, seed, st)  # gate 1
                    equal[t] += 1
                    for m in MODES:
                        merge(pooled[m], cells[m]['acc'])
                        merge(pooled_all[m], cells[m]['acc'])
                    rows.append((cells['legacy']['flat'], cells['centered_v1']['flat']))
            print(t, name, flush=True)
        rows_all[t] = rows
        summary = summarise(pooled)
        diffs = {k: paired(rows, k) for k in DIFF_KEYS}
        result_settings[t] = {'pooled': summary, 'paired_cells': diffs, 'predictions': predictions(summary, diffs), 'gate3': gate3(summary)}
    all_rows = [r for rows in rows_all.values() for r in rows]
    summary = summarise(pooled_all)
    diffs = {k: paired(all_rows, k) for k in DIFF_KEYS}
    result = {'task': 'T036', 'identity': le.league_identity(), 'runtime': runtime(), 'seeds': list(SEEDS), 'days': DAYS, 'gates': gates, 'legacy_cells_equal_frozen': equal,
              'settings': result_settings, 'all': {'pooled': summary, 'paired_cells': diffs, 'predictions': predictions(summary, diffs), 'gate3': gate3(summary)}, 'elapsed_seconds': perf_counter() - start}
    write_json(out / 'summary.json', result)
    print(json.dumps({'equal': equal, 'elapsed_seconds': result['elapsed_seconds']}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    run(parser.parse_args().out.resolve(), CODE_ROOT)
