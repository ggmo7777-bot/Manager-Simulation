"""T035: is the observation-noise centre shift (mean -noise) a design or a defect, and how much of the coach's estimate error does it explain? Analysis only: recorded
coach observation events are re-derived offline; the simulation is not changed and nothing goes back into it. True skills are read by the researcher after the fact.

Protocol: docs/experiments/T035-PROTOCOL.md. Cells: T030 / T031 / T032 settings x 14 worlds x seeds {100,101,102} x start age {29,33}, 168 days (same as T034).
"""
import argparse
import ast
import json
from pathlib import Path
import re
import subprocess
import sys
from time import perf_counter

CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT))
from experiments import t023, t030, t033, t034
from soccer_sim import league_economy as le
from soccer_sim.match_2d import clamp, round6, uniform_from_key
from soccer_sim.storage import runtime, write_json

L, DAYS, SEEDS, STARTS, SETTINGS = t033.L, t033.DAYS, t033.SEEDS, t033.STARTS, t033.SETTINGS
KIND_OF = t034.EVENT_KIND
CHANNEL = {'session': 'coach_obs', 'scout': 'scout_obs', 'match': 'match_obs'}
WIDTH = {'session': 'coach_noise', 'scout': 'scout_noise', 'match': 'match_obs_noise'}
BOUNDS = {'session': (0.10, 0.98), 'scout': (0.10, 0.98), 'match': (0.15, 0.98)}
TOL_KEY, TOL_PROP = 2e-6, 2e-4
GROUPS = ('inc', 'new', 'new_t0', 'new_t1', 'new_t2')
mean = t030.mean


def draw(seed, day, kind, coach, pid, width):
    """The realized noise of the sim's own rule (nu) and of the centred rule (nu2 = u * width, u in [-1, 1], symmetric uniform [-width, +width])."""
    u = uniform_from_key(seed, day, CHANNEL[kind], f'{coach}:{pid}')
    return (u - 0.5) * 2.0 * width, u * width


def shifted(val, nu, nu2, kind):
    """The observation value in the centred world: same base, centred noise."""
    lo, hi = BOUNDS[kind]
    return round6(clamp(val - nu + nu2, lo, hi))


def propagate(d, a, delta):
    """d: estimate shift (centred world - recorded world); one observation with update rate a (0 = estimate not touched) and observation shift delta."""
    return d if a == 0 else (1.0 - a) * d + a * delta


def cf_starters(ranking, w, shift):
    """Top two after adding w x (estimate shift) to each player's two estimate terms; the recorded other terms stay (ties by player id)."""
    scores = {r['player_id']: r['score'] + w[0] * shift.get(r['player_id'], (0.0, 0.0))[0] + w[1] * shift.get(r['player_id'], (0.0, 0.0))[1] for r in ranking}
    return t033.starters_of(scores)


# ---- static audit of every noise draw in the package ----
def audit_code(root):
    out, by_class = [], {}
    for path in sorted((root / 'soccer_sim').glob('*.py')):
        text = path.read_text()
        tree = ast.parse(text)
        stmts = [n for n in ast.walk(tree) if isinstance(n, ast.stmt)]
        for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, 'id', getattr(n.func, 'attr', None)) == 'uniform_from_key'):
            explicit = any(k.arg in ('low', 'high') for k in call.keywords)
            inner = min((s for s in stmts if s.lineno <= call.lineno <= s.end_lineno and not isinstance(s, (ast.FunctionDef, ast.ClassDef))), key=lambda s: s.end_lineno - s.lineno, default=None)
            seg = ' '.join((ast.get_source_segment(text, inner) or '').split()) if inner else ''
            if explicit:
                cls = 'explicit_range'
            elif re.search(r'-\s*0\.5\)\s*\*\s*2\.0', seg) or re.search(r'-\s*0\.5\)\s*\*\s*2\s', seg) or '- 0.5)' in seg:
                cls = 'default_range_affine_(u-0.5)*2*noise'
            elif re.search(r'2\s*\*\s*uniform_from_key', seg) and re.search(r'\)\s*-\s*1\)', seg):
                cls = 'default_range_affine_(2u-1)*noise'
            else:
                cls = 'default_range_other'
            by_class.setdefault(path.name, {}).setdefault(cls, 0)
            by_class[path.name][cls] += 1
            if cls.startswith('default_range'):
                out.append({'file': path.name, 'line': call.lineno, 'class': cls, 'statement': seg[:160]})
    return {'by_file_and_class': by_class, 'default_range_calls': out}


def doc_mentions(root):
    hits = {}
    for name in ('DESIGN.md', 'OPEN-QUESTIONS.md', 'RESEARCH.md', 'CHARTER.md', 'GOVERNANCE.md', 'ROADMAP.md'):
        lines = (root / 'docs' / name).read_text().splitlines()
        hits[name] = [{'line': i + 1, 'text': ln[:120]} for i, ln in enumerate(lines) if re.search(r'雑音|noise|偏り|\bbias\b', ln, re.I)]
    dec = (root / 'docs/DECISIONS.md').read_text().splitlines()
    hits['DECISIONS.md'] = [{'line': i + 1, 'text': ln[:120]} for i, ln in enumerate(dec) if re.search(r'雑音|\bnoise\b', ln, re.I) and not re.search(r'D049|D050|T034|T035', ln)]
    return hits


def git_history(root):
    cmds = {'league_economy_affine': ['git', 'log', '--oneline', '-S- 0.5) * 2.0', '--', 'soccer_sim/league_economy.py'],
            'league_observation_affine': ['git', 'log', '--oneline', '-S(2 * uniform_from_key', '--', 'soccer_sim/league_observation.py'],
            'commit_count': ['git', 'rev-list', '--count', 'HEAD']}
    try:
        return {k: subprocess.run(c, cwd=root, capture_output=True, text=True, check=True).stdout.strip().splitlines() for k, c in cmds.items()}
    except Exception as exc:  # no git in a source bundle
        return {'unavailable': str(exc)}


# ---- accumulators: nested dicts of numbers, summed across cells ----
def bump(acc, path, value=1.0):
    for p in path[:-1]:
        acc = acc.setdefault(p, {})
    acc[path[-1]] = acc.get(path[-1], 0.0) + value


def merge(a, b):
    for k, v in b.items():
        if isinstance(v, dict):
            merge(a.setdefault(k, {}), v)
        else:
            a[k] = a.get(k, 0.0) + v
    return a


def add_err(acc, groups, e_s, d_s, e_m, d_m):
    for g in groups:
        for key, e, d in (('s', e_s, d_s), ('m', e_m, d_m)):
            for name, v in (('n', 1.0), ('e', e), ('d', d), ('abs', abs(e)), ('abs_c', abs(e + d)), ('sq', e * e), ('sq_c', (e + d) ** 2)):
                bump(acc, ('err', g, key, name), v)


# ---- one cell ----
def run_cell(config, seed, days=DAYS):
    world = le._build_initial_world(seed=seed, config=config)
    w = (config.select_skill_weight, config.select_match_weight, config.select_fatigue_weight)
    practice = config.coach_practice_rating != 'none'
    acc, newc, shift, chain, newrows = {}, {}, {}, {}, []
    checks = {'session_key_checked': 0, 'session_key_bad': 0, 'scout_key_checked': 0, 'scout_key_bad': 0, 'chain_checked': 0, 'chain_bad': 0, 'lineups': 0,
              'newcomer_lineup_estimates': 0, 'newcomer_propagation_checked': 0, 'newcomer_propagation_max_dev': 0.0}
    while world.day < days:
        n_events = len(world.events)
        le.advance_league_days(world, 1)
        truth = {}
        for e in world.events[n_events:]:
            kind, data = e['kind'], e['data']
            if kind == 'season_youth_joined':
                club = data['club_id']
                newc[data['player_id']] = {'club': club, 'coach': le.CLUB_COACH_MAP[club], 'day': world.day, 'ops': [], 'lu': []}
            elif kind in KIND_OF:
                k = KIND_OF[kind]
                pid, coach = data['target_player_id' if k == 'scout' else 'player_id'], e['actor']
                width = getattr(world.config, WIDTH[k])
                val = data['rating_obs'] if k == 'match' else data['obs_skill']
                if width == 0:
                    bump(acc, ('noise', k, 'zero_width'))
                    continue
                nu, nu2 = draw(world.seed, e['day'], k, coach, pid, width)
                vc = shifted(val, nu, nu2, k)
                lo, hi = BOUNDS[k]
                clamped = val <= lo or val >= hi
                for name, v in (('n', 1.0), ('nu', nu / width), ('nu_sq', (nu / width) ** 2), ('nu2', nu2 / width), ('nu2_sq', (nu2 / width) ** 2), ('clamped', float(clamped)), ('shift', vc - val)):
                    bump(acc, ('noise', k, name), v)
                if k in ('session', 'scout') and not clamped:
                    ok = abs(val - nu - world.players[pid].composite_skill()) <= TOL_KEY
                    checks[f'{k}_key_checked'] += 1
                    checks[f'{k}_key_bad'] += 0 if ok else 1
                a_s, a_m = t034.alphas(k, config, practice)
                key = (coach, pid)
                ds, dm = shift.get(key, (0.0, 0.0))
                shift[key] = (propagate(ds, a_s, vc - val), propagate(dm, a_m, vc - val))
                if a_s:  # chain gate: the recorded post-update skill estimate follows from the previous one and this observation
                    prev = chain.get(key)
                    step = None if prev is None else t034.mix(prev, a_s, val)
                    if k != 'match':
                        if prev is not None:
                            checks['chain_checked'] += 1
                            checks['chain_bad'] += 0 if step == data['skill_estimate'] else 1
                        chain[key] = data['skill_estimate']
                    elif step is not None:
                        chain[key] = step
                if pid in newc and newc[pid]['coach'] == coach:
                    newc[pid]['ops'].append({'kind': k, 'day': e['day'], 'val': val, 'vc': vc, 'order': len(newc[pid]['ops'])})
            elif kind == 'lineup_decision':
                t033.check_lineup_event(data)
                checks['lineups'] += 1
                club, coach = data['club_id'], le.CLUB_COACH_MAP[data['club_id']]
                sh = {r['player_id']: shift.get((coach, r['player_id']), (0.0, 0.0)) for r in data['ranking']}
                changed = set(cf_starters(data['ranking'], w, sh)) != set(data['starters'])
                bump(acc, ('flip', 'lineups'))
                bump(acc, ('flip', 'changed'), float(changed))
                cf = set(cf_starters(data['ranking'], w, sh))
                for r in data['ranking']:
                    p = r['player_id']
                    if p not in truth:
                        truth[p] = world.players[p].composite_skill()
                    e_s, e_m = r['components']['skill_term'] / w[0] - truth[p], r['components']['match_term'] / w[1] - truth[p]
                    d_s, d_m = sh[p]
                    info = newc.get(p)
                    if info is None:
                        add_err(acc, ('inc',), e_s, d_s, e_m, d_m)
                    elif info['club'] == club and 1 <= world.day - info['day'] <= L:
                        t = t034.third_of(world.day - info['day'])
                        started, cf_started = p in data['starters'], p in cf
                        newrows.append((info['day'], t, e_s, d_s, e_m, d_m, started, cf_started))
                        info['ops'].append({'kind': 'lineup', 'day': e['day'], 'ref': len(info['lu']), 'order': len(info['ops'])})
                        info['lu'].append((r['components']['skill_term'], r['components']['match_term'], d_s, d_m))
    le._validate_league_invariants(world)
    for pid, info in newc.items():
        join = info['day']
        if join + L > days:
            continue
        ops = [o for o in info['ops'] if o['day'] <= join + L]
        keys = [(o['day'], t034.PHASE[o['kind']]) for o in ops]
        assert keys == sorted(keys), (pid, keys)
        rec = t034.replay(ops, config, practice)
        recc = t034.replay([dict(o, val=o['vc']) if o['kind'] != 'lineup' else o for o in ops], config, practice)
        for r, rc in zip(rec, recc):
            sk, mt, d_s, d_m = info['lu'][r['ref']]
            assert round6(w[0] * r['s']) == sk and round6(w[1] * r['m']) == mt, (pid, r)
            dev = max(abs((rc['s'] - r['s']) - d_s), abs((rc['m'] - r['m']) - d_m))
            checks['newcomer_lineup_estimates'] += 1
            checks['newcomer_propagation_checked'] += 1
            checks['newcomer_propagation_max_dev'] = max(checks['newcomer_propagation_max_dev'], dev)
            assert dev <= TOL_PROP, (pid, r, rc, d_s, d_m)
    for join, t, e_s, d_s, e_m, d_m, started, cf_started in newrows:
        if join + L > days:
            continue
        add_err(acc, ('new', f'new_t{t}'), e_s, d_s, e_m, d_m)
        bump(acc, ('start', 'rows'))
        bump(acc, ('start', 'recorded'), float(started))
        bump(acc, ('start', 'centred'), float(cf_started))
    return {'final': t030.final_metrics(world), 'events': len(world.events), 'acc': acc, 'checks': checks}


# ---- summaries ----
def std(n, s, ss):
    return max(0.0, ss / n - (s / n) ** 2) ** 0.5


def summarise_noise(noise):
    out = {}
    for k, v in noise.items():
        n = v['n']
        out[k] = {'events': int(n), 'mean_nu_over_width': v['nu'] / n, 'sd_nu_over_width': std(n, v['nu'], v['nu_sq']), 'mean_centred_over_width': v['nu2'] / n,
                  'sd_centred_over_width': std(n, v['nu2'], v['nu2_sq']), 'clamped_share': v['clamped'] / n, 'mean_observation_shift': v['shift'] / n, 'zero_width_events': int(v.get('zero_width', 0))}
    return out


def summarise_err(err):
    out = {}
    for g, by in err.items():
        out[g] = {}
        for key, v in by.items():
            n = v['n']
            e, d = v['e'] / n, v['d'] / n
            out[g][key] = {'rows': int(n), 'mean_error': e, 'mean_shift': d, 'explained_share': d / -e if e < 0 else None, 'mae': v['abs'] / n, 'mae_centred': v['abs_c'] / n,
                           'rmse': (v['sq'] / n) ** 0.5, 'rmse_centred': (v['sq_c'] / n) ** 0.5}
    gap = {}
    for g in ('new', 'new_t0', 'new_t1', 'new_t2'):
        gap[g] = {}
        for key in ('s', 'm'):
            a, b = out[g][key], out['inc'][key]
            delta = a['mean_error'] - b['mean_error']
            gap[g][key] = {'gap': delta, 'gap_shift': a['mean_shift'] - b['mean_shift'], 'explained_share': (a['mean_shift'] - b['mean_shift']) / -delta if delta < 0 else None,
                           'remaining_new_error_centred': a['mean_error'] + a['mean_shift']}
    return out, gap


def summarise(acc):
    err, gap = summarise_err(acc['err'])
    f, s = acc['flip'], acc['start']
    return {'noise': summarise_noise(acc['noise']), 'error': err, 'gap': gap,
            'top2_changed_share': f['changed'] / f['lineups'], 'lineups': int(f['lineups']), 'newcomer_rows': int(s['rows']),
            'newcomer_start_share_recorded': s['recorded'] / s['rows'], 'newcomer_start_share_centred': s['centred'] / s['rows']}


def predictions(sm):
    nz, e, g = sm['noise'], sm['error'], sm['gap']
    return {'P1_means_and_sds': all(abs(nz[k]['mean_nu_over_width'] + 1) <= 0.03 and abs(nz[k]['sd_nu_over_width'] - 1.1547) <= 0.03 and abs(nz[k]['mean_centred_over_width']) <= 0.03 for k in nz),
            'P4_incumbent_error_negative_and_50pct_explained': e['inc']['s']['mean_error'] < 0 and (e['inc']['s']['explained_share'] or 0) >= 0.5,
            'P5_newcomer_t2_explained_15_to_50pct': 0.15 <= (e['new_t2']['s']['explained_share'] or 0) < 0.50,
            'P6_gap_explained_under_25pct': (g['new']['s']['explained_share'] if g['new']['s']['explained_share'] is not None else 0) < 0.25,
            'P7_top2_under_10pct': sm['top2_changed_share'] < 0.10, 'P7_newcomer_start_within_3pt': abs(sm['newcomer_start_share_centred'] - sm['newcomer_start_share_recorded']) <= 0.03,
            'P8_mae_down_both': all(e[x]['s']['mae_centred'] < e[x]['s']['mae'] for x in ('new', 'inc')),
            'decision_i_gap_under_50pct': (g['new']['s']['explained_share'] if g['new']['s']['explained_share'] is not None else 0) < 0.50,
            'decision_ii_remaining_t2_error_le_-0.05': g['new_t2']['s']['remaining_new_error_centred'] <= -0.05}


def run(out, root):
    out.mkdir(parents=True, exist_ok=False)
    start = perf_counter()
    sim = t034.check_sim_unchanged(root)
    audit = {'static_calls': audit_code(root), 'doc_mentions': doc_mentions(root), 'git': git_history(root)}
    print('sim', sim, flush=True)
    refs = {t: {(c['world'], c['seed'], c['start_age']): c for c in json.loads((root / f'artifacts/{t}/run/summary.json').read_text())['cells']} for t in SETTINGS}
    t34 = json.loads((root / 'artifacts/t034/run/summary.json').read_text())
    per_setting, equal, checks, total = {}, {t: 0 for t in SETTINGS}, {}, {}
    for t, make in SETTINGS.items():
        acc, by_start = {}, {st: {} for st in STARTS}
        for name in t023.conditions():
            for seed in SEEDS:
                for st in STARTS:
                    cell = run_cell(make(name, st), seed)
                    ref = refs[t][(name, seed, st)]['on']
                    assert cell['final'] == ref['final'] and cell['events'] == ref['events'], (t, name, seed, st)  # gate 2
                    equal[t] += 1
                    for kk, v in cell['checks'].items():
                        checks[kk] = max(checks.get(kk, 0.0), v) if kk.endswith('max_dev') else checks.get(kk, 0) + v
                    merge(acc, cell['acc'])
                    merge(by_start[st], cell['acc'])
            print(t, name, flush=True)
        per_setting[t] = {'all': summarise(acc), **{f'start_age_{st}': summarise(by_start[st]) for st in STARTS}}
        per_setting[t]['predictions'] = predictions(per_setting[t]['all'])
        ref34 = t34['settings'][t]['all']['all_rows']  # gate 2: the same newcomer rows as T034
        assert per_setting[t]['all']['newcomer_rows'] == ref34['rows'], (t, per_setting[t]['all']['newcomer_rows'], ref34['rows'])
        assert abs(per_setting[t]['all']['newcomer_start_share_recorded'] - ref34['start_share']) < 1e-12
        merge(total, acc)
    assert checks['session_key_bad'] == 0 and checks['newcomer_propagation_max_dev'] <= TOL_PROP  # gate 3
    result = {'task': 'T035', 'identity': le.league_identity(), 'runtime': runtime(), 'seeds': list(SEEDS), 'days': DAYS, 'sim_unchanged': sim, 'control_cells_equal': equal,
              'checks': checks, 'audit': audit, 'settings': per_setting, 'pooled': summarise(total), 'elapsed_seconds': perf_counter() - start}
    write_json(out / 'summary.json', result)
    print(json.dumps({'checks': checks, 'elapsed_seconds': result['elapsed_seconds']}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    run(parser.parse_args().out.resolve(), CODE_ROOT)
