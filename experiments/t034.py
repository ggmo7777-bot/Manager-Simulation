"""T034: is the newcomers' estimate lag a matter of observation FREQUENCY or of the per-observation UPDATE SIZE? Analysis only: the recorded coach observation events are counted
and replayed offline; the simulation is not changed and nothing goes back into it. True skills are read by the researcher after the fact.

Protocol: docs/experiments/T034-PROTOCOL.md. Cells: T030 / T031 / T032 settings x 14 worlds x seeds {100,101,102} x start age {29,33}, 168 days (same as T033).
"""
import argparse
import json
from pathlib import Path
import sys
from time import perf_counter

CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT))
from experiments import t023, t030, t033
from soccer_sim import league_economy as le
from soccer_sim.match_2d import clamp, round6, uniform_from_key
from soccer_sim.storage import runtime, write_json

L, DAYS, SEEDS, STARTS, SETTINGS = t033.L, t033.DAYS, t033.SEEDS, t033.STARTS, t033.SETTINGS
THIRDS = ((1, 7), (8, 14), (15, 21))
KINDS = ('session', 'scout', 'match')
EVENT_KIND = {'coach_session_observation': 'session', 'coach_scout_observation': 'scout', 'coach_match_observation': 'match'}
PHASE = {'session': 0, 'scout': 0, 'lineup': 1, 'match': 2}  # inside a day: training/scouting, then the selection, then the match
CFS = ('F+', 'U+', 'Fmax', 'Umax')
TOL_R = 1e-4
mean = t030.mean


def third_of(since):
    return next(i for i, (lo, hi) in enumerate(THIRDS) if lo <= since <= hi)


# ---- the replay of a coach's estimate from observations (literal rules of the table in the protocol) ----
def alphas(kind, c, practice, scale=1.0):
    """(update rate of the skill estimate, update rate of the match-rating estimate) for one observation; 0 = that estimate is not touched."""
    base = {'session': (c.coach_skill_alpha, 0.30 if practice else 0.0), 'scout': (c.coach_scout_alpha, 0.30), 'match': (c.coach_match_skill_blend, c.coach_match_alpha)}[kind]
    return tuple(min(1.0, a * scale) for a in base)


def mix(old, a, value):
    return old if a == 0 else round6(clamp((1.0 - a) * old + a * value, 0.10, 0.98))


def replay(ops, c, practice, scale=1.0):
    """ops: time-ordered list of {'kind': session|scout|match|lineup, 'day', 'val'}. Returns one dict per lineup marker: the estimates just before the selection and the
    observations that led to them [(a_s, a_m, value, day)]."""
    s, m, seen, out = 0.5, 0.5, [], []
    for op in ops:
        if op['kind'] == 'lineup':
            out.append({'day': op['day'], 'ref': op['ref'], 's': s, 'm': m, 'seen': list(seen)})
            continue
        a_s, a_m = alphas(op['kind'], c, practice, scale)
        s, m = mix(s, a_s, op['val']), mix(m, a_m, op['val'])
        seen.append((a_s, a_m, op['val'], op['day']))
    return out


def decompose(seen, idx, T_L, truth, est):
    """est - T_L = P (0.5 - T_L) + sum w_i (T_i - T_L) + sum w_i (o_i - T_i) + r, with w_i = a_i prod_{j>i}(1 - a_j) and P = prod(1 - a_i); r is the rounding residual."""
    obs = [(o[idx], o[2], truth[o[3]]) for o in seen if o[idx] > 0]
    suffix, weights = 1.0, []
    for a, _, _ in reversed(obs):
        weights.append(a * suffix)
        suffix *= 1.0 - a
    weights.reverse()
    prior = suffix * (0.5 - T_L)
    growth = sum(w * (T - T_L) for w, (_, _, T) in zip(weights, obs))
    bias = sum(w * (v - T) for w, (_, v, T) in zip(weights, obs))
    return {'prior': prior, 'growth': growth, 'bias': bias, 'r': (est - T_L) - (prior + growth + bias)}


def added_sessions(ops_obs, join, truth, seed, coach, pid, c, mode):
    """F+: one extra practice-day observation in the first day without a recorded one in each week; Fmax: on every such day. The value is what the sim's own rule would have given."""
    have = {o['day'] for o in ops_obs if o['kind'] == 'session'}
    days = [d for lo, hi in THIRDS for d in range(join + lo, join + hi + 1) if d not in have]
    if mode == 'F+':
        days = [next((d for d in range(join + lo, join + hi + 1) if d not in have), None) for lo, hi in THIRDS]
        days = [d for d in days if d is not None]
    out = []
    for d in days:
        noise = (uniform_from_key(seed, d, 'coach_obs', f'{coach}:{pid}') - 0.5) * 2.0 * c.coach_noise
        out.append({'kind': 'session', 'day': d, 'val': round6(clamp(truth[d] + noise, 0.10, 0.98)), 'order': -1})
    return out


def order(ops):
    return sorted(ops, key=lambda o: (o['day'], PHASE[o['kind']], o.get('order', 0)))


def start_after(ranking, n, s, m, w):
    """Newcomer-only counterfactual selection: replace the newcomer's two estimate terms, keep every other recorded score; ties by player id."""
    rk = {r['player_id']: r for r in ranking}
    comp = dict(rk[n]['components'])
    comp['skill_term'], comp['match_term'] = round6(w[0] * s), round6(w[1] * m)
    scores = {p: r['score'] for p, r in rk.items()}
    scores[n] = round6(sum(comp.values()))
    return n in t033.starters_of(scores)


# ---- gates ----
def check_sim_unchanged(root):
    """Gate 1: soccer_sim is byte-identical to T032's and T033's frozen bundles; same source hash as T032's run."""
    out = {}
    for t in ('t032', 't033'):
        bundle = root / f'artifacts/{t}/source_bundle/soccer_sim'
        files = sorted(p.name for p in (root / 'soccer_sim').glob('*.py'))
        assert files == sorted(p.name for p in bundle.glob('*.py'))
        assert all((root / 'soccer_sim' / f).read_bytes() == (bundle / f).read_bytes() for f in files)
        out[t] = len(files)
    recorded = json.loads((root / 'artifacts/t032/run/summary.json').read_text())['identity']['source_sha256']
    assert le.league_identity()['source_sha256'] == recorded
    return {'files_compared': out, 'source_sha256': recorded}


# ---- one cell ----
def run_cell(config, seed, days=DAYS):
    world = le._build_initial_world(seed=seed, config=config)
    w = (config.select_skill_weight, config.select_match_weight, config.select_fatigue_weight)
    practice = config.coach_practice_rating != 'none'
    newc, log, lineups, checks = {}, {}, [], {'lineup_estimates': 0, 'recorded_session_scout_estimates': 0}
    while world.day < days:
        n_events = len(world.events)
        le.advance_league_days(world, 1)
        joined, snapshot = [], None
        for e in world.events[n_events:]:
            kind, d = e['kind'], e['data']
            if kind == 'season_youth_joined':
                club = d['club_id']
                newc[d['player_id']] = {'club': club, 'coach': le.CLUB_COACH_MAP[club], 'day': world.day, 'ops': [], 'truth': {}}
                joined.append(d['player_id'])
            elif kind in EVENT_KIND:
                k = EVENT_KIND[kind]
                pid, coach = d['target_player_id' if k == 'scout' else 'player_id'], e['actor']
                log.setdefault((coach, pid), []).append((world.day, k))
                if pid in newc and newc[pid]['coach'] == coach:
                    val = d['rating_obs'] if k == 'match' else d['obs_skill']
                    op = {'kind': k, 'day': world.day, 'val': val, 'order': len(newc[pid]['ops'])}
                    if k != 'match':
                        op['rec_s'], op['rec_m'] = d['skill_estimate'], d.get('match_rating_estimate')
                    newc[pid]['ops'].append(op)
            elif kind == 'lineup_decision':
                if snapshot is None:
                    snapshot = {p: world.players[p].composite_skill() for p in world.players}
                t033.check_lineup_event(d)
                lineups.append({'day': world.day, 'club': d['club_id'], 'ranking': d['ranking'], 'truth': snapshot})
                for r in d['ranking']:
                    p = r['player_id']
                    if p in newc and newc[p]['club'] == d['club_id']:
                        newc[p]['ops'].append({'kind': 'lineup', 'day': world.day, 'ref': len(lineups) - 1, 'order': len(newc[p]['ops'])})
        for pid in joined:
            newc[pid]['inc'] = [p for p in world.club_rosters[newc[pid]['club']] if p not in joined]
        for pid, info in newc.items():
            if world.day <= info['day'] + L:
                info['truth'][world.day] = world.players[pid].composite_skill()
    le._validate_league_invariants(world)
    rows, newcomers = [], []
    for pid, info in newc.items():
        join = info['day']
        if join + L > days:
            continue
        ops = [o for o in info['ops'] if o['day'] <= join + L and not (o['kind'] == 'lineup' and o['day'] <= join)]
        keys = [(o['day'], PHASE[o['kind']]) for o in ops]
        assert keys == sorted(keys), (pid, keys)  # recorded order is (day, phase)
        rec = replay(ops, config, practice)
        # gate 3: the replay reproduces the recorded estimates
        s = m = 0.5
        for o in ops:
            if o['kind'] == 'lineup':
                continue
            a_s, a_m = alphas(o['kind'], config, practice)
            s, m = mix(s, a_s, o['val']), mix(m, a_m, o['val'])
            if o['kind'] != 'match':
                assert s == o['rec_s'] and (o['rec_m'] is None or m == o['rec_m']), (pid, o, s, m)
                checks['recorded_session_scout_estimates'] += 1
        for r in rec:
            lu = lineups[r['ref']]
            comp = next(x['components'] for x in lu['ranking'] if x['player_id'] == pid)
            assert round6(w[0] * r['s']) == comp['skill_term'] and round6(w[1] * r['m']) == comp['match_term'], (pid, r, comp)
            checks['lineup_estimates'] += 1
        coach, truth = info['coach'], info['truth']
        # counts
        events = [(d, k) for d, k in log.get((coach, pid), []) if join < d <= join + L]
        inc = info['inc']
        inc_events = [[(d, k) for d, k in log.get((coach, p), []) if join < d <= join + L] for p in inc]
        cnt = lambda ev, kind=None, lo=1, hi=L: sum(1 for d, k in ev if (kind is None or k == kind) and lo <= d - join <= hi)
        # counterfactual replays
        recorded_obs = [o for o in ops if o['kind'] != 'lineup']
        f_plus = added_sessions(recorded_obs, join, truth, seed, coach, pid, config, 'F+')
        f_max = added_sessions(recorded_obs, join, truth, seed, coach, pid, config, 'Fmax')
        mass = sum(alphas(o['kind'], config, practice)[0] for o in recorded_obs if join < o['day'] <= join + L)
        s_plus = 1.0 + len(f_plus) * config.coach_skill_alpha / mass if mass > 0 and f_plus else 1.0
        cf = {'F+': replay(order(ops + f_plus), config, practice), 'U+': replay(ops, config, practice, s_plus),
              'Fmax': replay(order(ops + f_max), config, practice), 'Umax': replay(ops, config, practice, 1e9)}
        row_list = []
        for i, r in enumerate(rec):
            lu = lineups[r['ref']]
            base = t033.analyse_row(lu['ranking'], pid, w, lu['truth'])
            T_L = lu['truth'][pid]
            assert abs(truth[r['day']] - T_L) <= 1e-12
            assert start_after(lu['ranking'], pid, r['s'], r['m'], w) == base['started']
            row = {'player': pid, 'day': r['day'], 'since_join': r['day'] - join, 'started': base['started'], 'top2_true': base['top2_true'], 'n_obs_before': len(r['seen']),
                   'e_s': r['s'] - T_L, 'e_m': r['m'] - T_L}
            for idx, name, est in ((0, 's', r['s']), (1, 'm', r['m'])):
                dec = decompose(r['seen'], idx, T_L, truth, est)
                assert abs(dec['r']) <= TOL_R, (pid, dec)
                row.update({f'{k}_{name}': v for k, v in dec.items()})
            for name, res in cf.items():
                x = res[i]
                assert x['day'] == r['day']
                row[f'cf_e_s_{name}'], row[f'cf_e_m_{name}'] = x['s'] - T_L, x['m'] - T_L
                row[f'cf_start_{name}'] = start_after(lu['ranking'], pid, x['s'], x['m'], w)
            row_list.append(row)
        rows.extend(row_list)
        newcomers.append({'player': pid, 'joined': join, 'n_added_F+': len(f_plus), 'n_added_Fmax': len(f_max), 'scale_U+': s_plus,
                          'started_in_window': any(r['started'] for r in row_list), 'rows': len(row_list), 'incumbents': len(inc),
                          'n': {k: cnt(events, k) for k in KINDS}, 'n_total': cnt(events),
                          'n_by_third': [{'total': cnt(events, None, lo, hi), 'session': cnt(events, 'session', lo, hi), 'match': cnt(events, 'match', lo, hi)} for lo, hi in THIRDS],
                          'inc_n': {k: mean(cnt(ev, k) for ev in inc_events) for k in KINDS}, 'inc_n_total': mean(cnt(ev) for ev in inc_events)})
    return {'final': t030.final_metrics(world), 'events': len(world.events), 'rows': rows, 'newcomers': newcomers, 'checks': checks}


# ---- aggregation ----
def closure(rows, key):
    """Share of the recorded mean error removed by a counterfactual: (cf - recorded) / (-recorded)."""
    rec = mean(r[f'e_{key}'] for r in rows)
    return {name: (mean(r[f'cf_e_{key}_{name}'] for r in rows) - rec) / (-rec) for name in CFS} if rows and rec else None


def agg_rows(rows):
    out = {'rows': len(rows)}
    if not rows:
        return out
    for key in ('s', 'm'):
        out[key] = {'error': mean(r[f'e_{key}'] for r in rows), **{c: mean(r[f'{c}_{key}'] for r in rows) for c in ('prior', 'growth', 'bias', 'r')},
                    'cf_error': {name: mean(r[f'cf_e_{key}_{name}'] for r in rows) for name in CFS}, 'closure': closure(rows, key)}
    out['n_obs_before'] = mean(r['n_obs_before'] for r in rows)
    out['start_share'] = mean(1.0 if r['started'] else 0.0 for r in rows)
    out['max_abs_r'] = max(abs(r[f'r_{k}']) for r in rows for k in ('s', 'm'))
    return out


def flip_rates(rows):
    return {name: mean(1.0 if r[f'cf_start_{name}'] else 0.0 for r in rows) for name in CFS} if rows else None


def freq(newcomers):
    f = {'newcomers': len(newcomers), 'mean_n': {k: mean(n['n'][k] for n in newcomers) for k in KINDS}, 'mean_total': mean(n['n_total'] for n in newcomers),
         'incumbent_mean_n': {k: mean(n['inc_n'][k] for n in newcomers) for k in KINDS}, 'incumbent_mean_total': mean(n['inc_n_total'] for n in newcomers),
         'by_third': [{k: mean(n['n_by_third'][i][k] for n in newcomers) for k in ('total', 'session', 'match')} for i in range(3)],
         'mean_added_F+': mean(n['n_added_F+'] for n in newcomers), 'mean_added_Fmax': mean(n['n_added_Fmax'] for n in newcomers), 'mean_scale_U+': mean(n['scale_U+'] for n in newcomers)}
    for label, grp in (('started', [n for n in newcomers if n['started_in_window']]), ('never_started', [n for n in newcomers if not n['started_in_window']])):
        f[label] = {'newcomers': len(grp), 'mean_total': mean(n['n_total'] for n in grp), 'mean_n': {k: mean(n['n'][k] for n in grp) for k in KINDS}} if grp else None
    return f


def summarise(rows, newcomers):
    missed = [r for r in rows if not r['started'] and r['top2_true']]
    skipped = [r for r in rows if not r['started']]
    return {'frequency': freq(newcomers), 'all_rows': agg_rows(rows), 'by_third': [agg_rows([r for r in rows if third_of(r['since_join']) == i]) for i in range(3)],
            'flip_rate_not_started': flip_rates(skipped), 'flip_rate_missed_top2': flip_rates(missed), 'rows_not_started': len(skipped), 'rows_missed_top2': len(missed)}


def predictions(sm):
    """The pre-registered predictions, evaluated mechanically from the summary of one setting (all cells)."""
    f, thirds = sm['frequency'], sm['by_third']
    comp = lambda t: {k: thirds[t]['s'][k] for k in ('prior', 'growth', 'bias')}
    late, early = comp(2), comp(0)
    return {'P1a_newcomers_observed_less': f['mean_total'] < f['incumbent_mean_total'],
            'P1b_session_ratio_in_0.8_1.25': 0.8 <= f['mean_n']['session'] / f['incumbent_mean_n']['session'] <= 1.25, 'session_ratio': f['mean_n']['session'] / f['incumbent_mean_n']['session'],
            'P2_never_started_30pct_fewer': f['never_started']['mean_total'] <= 0.7 * f['started']['mean_total'],
            'P3_late_growth_is_largest': max(late, key=lambda k: abs(late[k])) == 'growth', 'P3_early_prior_over_growth': abs(early['prior']) > abs(early['growth']),
            'P4_Fplus_beats_Uplus_late': thirds[2]['s']['closure']['F+'] > thirds[2]['s']['closure']['U+'],
            'P5_Fmax_closes_70pct_late': thirds[2]['s']['closure']['Fmax'] >= 0.70,
            'P6_best_of_Fplus_Uplus_15pct_missed': max(sm['flip_rate_missed_top2'][c] for c in ('F+', 'U+')) >= 0.15, 'P6_Fmax_40pct_missed': sm['flip_rate_missed_top2']['Fmax'] >= 0.40}


def run(out, root):
    out.mkdir(parents=True, exist_ok=False)
    start = perf_counter()
    sim = check_sim_unchanged(root)
    print('sim', sim, flush=True)
    refs = {t: {(c['world'], c['seed'], c['start_age']): c for c in json.loads((root / f'artifacts/{t}/run/summary.json').read_text())['cells']} for t in SETTINGS}
    t33 = json.loads((root / 'artifacts/t033/run/summary.json').read_text())
    per_setting, all_rows, equal, checks = {}, [], {t: 0 for t in SETTINGS}, {'lineup_estimates': 0, 'recorded_session_scout_estimates': 0}
    for t, make in SETTINGS.items():
        rows, newcomers = [], []
        for name in t023.conditions():
            for seed in SEEDS:
                for st in STARTS:
                    cell = run_cell(make(name, st), seed)
                    ref = refs[t][(name, seed, st)]['on']
                    assert cell['final'] == ref['final'] and cell['events'] == ref['events'], (t, name, seed, st)  # gate 2
                    equal[t] += 1
                    for k, v in cell['checks'].items():
                        checks[k] += v
                    tag = {'setting': t, 'world': name, 'seed': seed, 'start_age': st}
                    for r in cell['rows']:
                        r.update(tag)
                    for n in cell['newcomers']:
                        n.update(tag)
                    rows.extend(cell['rows'])
                    newcomers.extend(cell['newcomers'])
            print(t, name, flush=True)
        per_setting[t] = {'all': summarise(rows, newcomers), **{f'start_age_{st}': summarise([r for r in rows if r['start_age'] == st], [n for n in newcomers if n['start_age'] == st]) for st in STARTS}}
        per_setting[t]['predictions'] = predictions(per_setting[t]['all'])
        ref33 = t33['settings'][t]['all']  # gate 2: the same rows and the same start share as T033
        assert len(rows) == ref33['rows'] and len(newcomers) == ref33['newcomers'], (t, len(rows), ref33['rows'])
        assert sum(1 for r in rows if r['started']) / len(rows) == ref33['start_row_share']
        assert sum(1 for r in rows if not r['started'] and r['top2_true']) == ref33['missed_top2_row_count']
        all_rows.extend({k: (round(v, 6) if isinstance(v, float) else v) for k, v in r.items()} for r in rows)
    result = {'task': 'T034', 'identity': le.league_identity(), 'runtime': runtime(), 'seeds': list(SEEDS), 'days': DAYS, 'season_length_days': L, 'sim_unchanged': sim,
              'control_cells_equal': equal, 'replay_checks': checks, 'rows_total': len(all_rows), 'settings': per_setting, 'elapsed_seconds': perf_counter() - start}
    write_json(out / 'summary.json', result)
    write_json(out / 'rows.json', all_rows)
    print(json.dumps({'rows': len(all_rows), 'checks': checks, 'elapsed_seconds': result['elapsed_seconds']}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    run(parser.parse_args().out.resolve(), CODE_ROOT)
