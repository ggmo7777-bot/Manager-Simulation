"""T020: one-at-a-time sensitivity sweep of the sampled initial ranges (pre-registered, ranges are not changed).

Cells: baseline sampled + every pre-registered perturbation that passes the range validity rule.
Seeds 300..329; institutions closed/open without reinvestment and open with reinvestment, 84 days.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path
import resource
import statistics
import subprocess
import sys
from time import perf_counter
from typing import Any

CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT))
from soccer_sim import league_economy as le
from soccer_sim.storage import runtime, write_json
from experiments import t019
from experiments.audit_t014 import admission_audit, ledger_audit
from experiments.audit_t016 import cohort_audit

SEEDS, DAYS = tuple(range(300, 330)), 84
INSTITUTIONS = t019.INSTITUTIONS
PRIMARY, ALL12 = t019.METRICS[0], t019.METRICS[1]
POPULATION = le.POPULATION_SUFFIX
PERTURBATIONS = tuple(n for n in le.RANGE_NAMES[2:])  # the 18 pre-registered names (excludes none, null)
EXECUTED = tuple(n for n in PERTURBATIONS if n in le.RANGE_RUNNABLE)
REFUSED = tuple(n for n in PERTURBATIONS if n not in le.RANGE_RUNNABLE)
CELLS = ('baseline',) + EXECUTED
F2_CONTRAST_T019 = 0.003551
# Hand-written expectations (academy, buyer) for the independent check; not derived from le.RANGE_*.
EXPECT = {
    'trait_w05': {'trait': ((0.445, 0.495), (0.415, 0.565))}, 'trait_w15': {'trait': ((0.395, 0.545), (0.265, 0.715))},
    'trait_cm': {'trait': ((0.37, 0.47), (0.29, 0.59))}, 'trait_cp': {'trait': ((0.47, 0.57), (0.39, 0.69))},
    'rate_w05': {'rate': ((0.1095, 0.1205), (0.0265, 0.0395))}, 'rate_w15': {'rate': ((0.0985, 0.1315), (0.0135, 0.0525))},
    'rate_cm': {'rate': ((0.0832, 0.1008), (0.016, 0.0368))}, 'rate_cp': {'rate': ((0.1248, 0.1512), (0.024, 0.0552))},
    'cap_w05': {'cap': ((0.9125, 0.9375), (0.7, 0.82))}, 'cap_cm': {'cap': ((0.87, 0.92), (0.61, 0.85))},
    'psych_w05': {'loyalty': (0.1125, 0.2375), 'ambition': (0.45, 0.65), 'autonomy': (0.335, 0.365)},
    'self_bias_w05': {'self_bias': (-0.015, 0.015)}, 'self_bias_w15': {'self_bias': (-0.045, 0.045)},
    'noise_w05': {'noise': (-0.03, 0.03)}, 'noise_w15': {'noise': (-0.09, 0.09)},
}


def condition(institution, cell):
    return institution + POPULATION + ('' if cell == 'baseline' else le.RANGE_SUFFIX + cell)


def raw_initial(seed, cell, institution='open_solidarity'):
    return le._snapshot_payload_unchecked(le.run_league_world(seed=seed, days=0, config=le.config_for_condition(condition(institution, cell))))


def recompute(seed, name):
    """Independent re-derivation of the sampled initial state from keyed draws and the literal expectations."""
    ex = EXPECT.get(name, {})
    def u(channel, token, low, high):
        return round(le._population_draw(seed, channel, token, low, high), 6)
    base = {'trait': ((0.42, 0.52), (0.34, 0.64)), 'rate': ((0.104, 0.126), (0.020, 0.046)), 'cap': ((0.90, 0.95), (0.64, 0.88)),
            'loyalty': (0.05, 0.30), 'ambition': (0.35, 0.75), 'autonomy': (0.32, 0.38), 'self_bias': (-0.03, 0.03), 'noise': (-0.06, 0.06)}
    r = {k: ex.get(k, v) for k, v in base.items()}
    players = {}
    for cid, pids in le.INITIAL_CLUB_ROSTERS.items():
        k = 0 if cid in le.ACADEMY_CLUB_IDS else 1
        for pid in pids:
            t = [u(f'trait{i}', pid, *r['trait'][k]) for i in range(3)]
            players[pid] = {'traits': t, 'A': u('rateA', pid, *r['rate'][k]), 'B': u('rateB', pid, *r['rate'][k]),
                            'cap': round(max(u('cap', pid, *r['cap'][k]), min(0.98, max(t) + 0.05)), 6),
                            'pref': 'A' if u('pref', pid, 0.0, 1.0) < 0.5 else 'B',
                            'loyalty': u('loyalty', pid, *r['loyalty']), 'ambition': u('ambition', pid, *r['ambition']),
                            'autonomy': u('autonomy', pid, *r['autonomy']),
                            'self_est': round(min(0.95, max(0.05, sum(t) / 3 + u('self_bias', pid, *r['self_bias']))), 6)}
    impressions = {(coach, pid): tuple(round(min(0.95, max(0.05, sum(p['traits']) / 3 + le._population_draw(seed, ch, f'{coach}:{pid}', *r['noise']))), 6)
                                         for ch in ('coach_skill', 'coach_match'))
                   for coach in le.COACH_IDS for pid, p in players.items()}
    return players, impressions


def check_initial(seeds):
    """Every executed perturbation: generated initial state equals the independent recomputation."""
    count = 0
    for seed in seeds:
        for cell in ('baseline', 'null') + EXECUTED:
            raw = raw_initial(seed, cell)
            players, impressions = recompute(seed, cell)
            for pid, p in players.items():
                truth, mind = raw['players'][pid], raw['player_minds'][pid]
                assert [truth['traits'][k] for k in ('pace', 'passing', 'shooting')] == p['traits'], (seed, cell, pid)
                assert truth['rates'] == {'A': p['A'], 'B': p['B']} and truth['cap'] == p['cap'], (seed, cell, pid)
                assert (mind['self_estimate'], mind['loyalty_bias'], mind['ambition'], mind['autonomy'], mind['preferred_method']) == \
                    (p['self_est'], p['loyalty'], p['ambition'], p['autonomy'], p['pref']), (seed, cell, pid)
            for (coach, pid), want in impressions.items():
                ev = raw['coaches'][coach]['evaluations'][pid]
                assert (ev['skill_estimate'], ev['match_rating_estimate']) == want, (seed, cell, coach, pid)
            count += 1
    return {'seeds': len(seeds), 'cells_checked': count}


def check_null_and_default(root):
    """Contract 1: default conditions equal T018/T019; the explicit null perturbation equals baseline sampled."""
    regression = t019.check_regression(root)
    for name in le.T011_ALL_CONDITIONS:
        assert 'range_perturbation' not in le.config_for_condition(name).to_dict()
        assert 'range_perturbation' not in le.config_for_condition(name + POPULATION).to_dict()
    pairs = 0
    for institution in INSTITUTIONS:
        for seed in (100, 300):
            a = le._snapshot_payload_unchecked(le.run_league_world(seed=seed, days=35, config=le.config_for_condition(condition(institution, 'baseline'))))
            b = le._snapshot_payload_unchecked(le.run_league_world(seed=seed, days=35, config=le.config_for_condition(condition(institution, 'null'))))
            assert b['config'].pop('range_perturbation') == 'null'
            assert t019.anonymous(a) == t019.anonymous(b) and {**a['config'], 'condition': 0} == {**b['config'], 'condition': 0}
            pairs += 1
    return {'default_regression': regression, 'null_equals_baseline_pairs': pairs}


def check_refusals():
    """Contract 2: out-of-domain, malformed and unsupported perturbations are refused, never clipped."""
    for name in REFUSED + ('trait_w25', 'nope', 'none'):
        for stem in ('open_solidarity' + POPULATION,):
            try:
                le.config_for_condition(f'{stem}{le.RANGE_SUFFIX}{name}')
            except ValueError:
                continue
            raise AssertionError(name)
    for bad in ('open_solidarity_range_trait_w05', 'nope_sampled_population_range_trait_w05'):
        try:
            le.config_for_condition(bad)
        except ValueError:
            continue
        raise AssertionError(bad)
    table = {}
    for name in PERTURBATIONS:
        try:
            ranges = le.sampled_ranges(name)
            table[name] = {'status': 'accepted', 'changed': {f'{k}:{p}': list(v) for (k, p), v in ranges.items() if v != le.RANGE_BASE[k, p]}}
        except ValueError as exc:
            table[name] = {'status': 'refused', 'reason': str(exc)}
    assert tuple(n for n, v in table.items() if v['status'] == 'accepted') == EXECUTED
    return {'perturbations': table, 'refused': list(REFUSED), 'accepted': list(EXECUTED)}


def check_cli(out):
    name = 'league_' + condition('open_solidarity', 'rate_w15')
    def run(*args):
        result = subprocess.run([sys.executable, '-B', '-m', 'soccer_sim', *map(str, args)], cwd=CODE_ROOT, text=True, capture_output=True, timeout=120)
        assert result.returncode == 0, result.stderr
    run('run', '--condition', name, '--seed', 300, '--days', 14, '--out', out / 'cli/day14')
    run('resume', '--snapshot', out / 'cli/day14/snapshot.json', '--days', 21, '--out', out / 'cli/resumed35')
    run('run', '--condition', name, '--seed', 300, '--days', 35, '--out', out / 'cli/direct35')
    for f in ('snapshot.json', 'events.jsonl', 'summary.json'):
        assert (out / 'cli/resumed35' / f).read_bytes() == (out / 'cli/direct35' / f).read_bytes(), f
    world = le.run_league_world(seed=300, days=35, config=le.config_for_condition(condition('open_solidarity', 'rate_w15')))
    assert json.loads((out / 'cli/direct35/snapshot.json').read_text()) == le.snapshot_payload(world)
    return {'condition': name, 'save_day14_resume_to35_equals_direct': True, 'cli_equals_api': True}


def sd(values):
    return statistics.stdev(values)


def worker(job):
    cell, institution, seed = job
    world = le.run_league_world(seed=seed, days=DAYS, config=le.config_for_condition(condition(institution, cell)))
    raw = le._snapshot_payload_unchecked(world)
    summary = le.summarize_league_world(world)
    cohort_audit(raw, summary)
    ledger, admission = ledger_audit(raw), admission_audit(raw)
    level = {pid: sum(p['traits'].values()) / 3 for pid, p in raw['initial_players'].items()}
    final = {pid: sum(p['traits'].values()) / 3 for pid, p in raw['players'].items()}
    academy = [pid for pid, p in raw['initial_players'].items() if p['club_id'] in ('club_a', 'club_c')]
    buyer = [pid for pid in level if pid not in academy]
    saturated = lambda ids: sum(raw['players'][pid]['cap'] - final[pid] <= 0.01 for pid in ids) / len(ids)
    initial = raw_initial(seed, cell, institution)
    metrics = {m: summary[m] for m in t019.METRICS}
    metrics.update({'level_sd': sd(list(level.values())), 'level_sd_academy': sd([level[p] for p in academy]),
                    'level_sd_buyer': sd([level[p] for p in buyer]), 'saturated': saturated(list(level)),
                    'saturated_academy': saturated(academy), 'saturated_buyer': saturated(buyer),
                    'transfers': sum(e['kind'] == 'transfer_completed' for e in raw['events']),
                    'impression_mae_initial': statistics.fmean(abs(e['skill_estimate'] - level[pid]) for c in initial['coaches'].values() for pid, e in c['evaluations'].items()),
                    'self_mae_initial': statistics.fmean(abs(m['self_estimate'] - level[pid]) for pid, m in initial['player_minds'].items())})
    return {'cell': cell, 'institution': institution, 'seed': seed, 'condition': condition(institution, cell), 'metrics': metrics,
            'cohort_count': summary['academy_initial_cohort_count'], 'transactions': ledger['transactions'],
            'admission': admission['counts'], 'requests': admission['requests']}


def vector(table, cell, metric, left, right):
    return [round(table[cell, left, s]['metrics'][metric] - table[cell, right, s]['metrics'][metric], 6) for s in SEEDS]


def analyse(rows):
    table = {(r['cell'], r['institution'], r['seed']): r for r in rows}
    open_s, open_n, closed = 'open_solidarity', 'open_no_solidarity', 'closed_no_solidarity'
    contrasts = {'reinvest': (PRIMARY, open_s, open_n), 'open_vs_closed': (PRIMARY, open_n, closed), 'reinvest_all12': (ALL12, open_s, open_n)}
    out: dict[str, Any] = {'cells': {}}
    base_vec = {k: vector(table, 'baseline', *v) for k, v in contrasts.items()}
    base_stats = t019.stats(base_vec['reinvest'])
    half = (base_stats['ci95_exploratory'][1] - base_stats['ci95_exploratory'][0]) / 2
    out['baseline_detectable_difference_halfwidth'] = round(half, 6)
    for cell in CELLS:
        entry: dict[str, Any] = {}
        for key, args in contrasts.items():
            v = vector(table, cell, *args)
            entry[key] = t019.stats(v)
            entry[key + '_minus_baseline'] = t019.stats([round(a - b, 6) for a, b in zip(v, base_vec[key])])
        for name in ('saturated', 'saturated_academy', 'saturated_buyer', 'level_sd', 'level_sd_academy', 'level_sd_buyer',
                     'transfers', 'impression_mae_initial', 'self_mae_initial'):
            values = [table[cell, open_s, s]['metrics'][name] for s in SEEDS]
            entry[name] = round(statistics.fmean(values), 6)
            entry[name + '_minus_baseline'] = round(entry[name] - statistics.fmean(table['baseline', open_s, s]['metrics'][name] for s in SEEDS), 6)
        entry['non_empty_worlds'] = {i: sum(table[cell, i, s]['cohort_count'] > 0 for s in SEEDS) for i in INSTITUTIONS}
        out['cells'][cell] = entry
    out['hypotheses'] = hypotheses(out['cells'], half)
    return out


def trichotomy(estimate, halfwidth, threshold):
    """supported: the whole interval is below the threshold; refuted: the whole interval is above; else indeterminate."""
    return 'supported' if estimate + halfwidth < threshold else 'refuted' if estimate - halfwidth > threshold else 'indeterminate'


def hypotheses(cells, half):
    # Rules fixed before the run. Delta = perturbed minus baseline reinvest effect; interval = paired exploratory t interval.
    def delta(cell):
        s = cells[cell]['reinvest_minus_baseline']
        return s['mean'], (s['ci95_exploratory'][1] - s['ci95_exploratory'][0]) / 2
    ranks = {}
    for name in EXECUTED:
        group = le.RANGE_PERTURBATIONS[name][0]
        d, h = delta(name)
        if abs(d) > abs(ranks.get(group, (0, 0, ''))[0]):
            ranks[group] = (d, h, name)
    h1_cells = {c: cells[c]['reinvest'] for c in CELLS}
    nonpositive = [c for c, s in h1_cells.items() if s['mean'] <= 0]
    includes_zero = [c for c, s in h1_cells.items() if s['mean'] > 0 and s['ci95_exploratory'][0] <= 0]
    h1 = 'refuted' if nonpositive else 'indeterminate' if includes_zero else 'supported'
    top = max(ranks, key=lambda g: abs(ranks[g][0]))
    d, h, name = ranks[top]
    beyond = abs(d) > half
    h2 = ('supported' if beyond else 'indeterminate') if top == 'rate' else ('refuted' if beyond and abs(d) - h > abs(ranks['rate'][0]) else 'indeterminate')
    d3, h3, name3 = ranks['noise']
    return {'H1': {'result': h1, 'scope': f'{len(EXECUTED)} executed perturbations + baseline; refused ones are not judged: {list(REFUSED)}',
                   'not_positive': nonpositive, 'mean_positive_interval_includes_zero': includes_zero},
            'H2': {'result': h2, 'largest_group': top, 'group_max_abs_delta': {g: {'perturbation': v[2], 'delta': v[0], 'paired_halfwidth': round(v[1], 6)}
                                                                                for g, v in sorted(ranks.items(), key=lambda kv: -abs(kv[1][0]))},
                   'beyond_baseline_detectable_difference': beyond},
            'H3': {'result': trichotomy(abs(d3), h3, F2_CONTRAST_T019), 'perturbation': name3, 'abs_delta': round(abs(d3), 6),
                   'paired_halfwidth': round(h3, 6), 'threshold': F2_CONTRAST_T019}}


def run(root, out):
    assert le.LEAGUE_SAVE_VERSION == 17 and le.LEAGUE_EVALUATION_VERSION == 't016-initial-cohort-v1'
    out.mkdir(parents=True, exist_ok=False)
    start = perf_counter()
    identity = le.league_identity()
    default_and_null = check_null_and_default(root)
    print('default_and_null', default_and_null, flush=True)
    refusals = check_refusals()
    print('refused', refusals['refused'], flush=True)
    initial = check_initial((300, 301))
    print('initial', initial, flush=True)
    cli = check_cli(out)
    jobs = [(c, i, s) for c in CELLS for i in INSTITUTIONS for s in SEEDS]
    with ProcessPoolExecutor() as pool:
        rows = list(pool.map(worker, jobs, chunksize=12))
    write_json(out / 'runs.json', rows)
    analysis = analyse(rows)
    result = {'task': 'T020', 'identity': identity, 'runtime': runtime(), 'days': DAYS, 'seeds': [SEEDS[0], SEEDS[-1]], 'cells': list(CELLS),
              'runs': len(rows), 'default_and_null': default_and_null, 'refusals': refusals, 'initial_recomputation': initial, 'cli': cli,
              'independently_audited_transactions': sum(r['transactions'] for r in rows), 'analysis': analysis,
              'elapsed_seconds': perf_counter() - start, 'max_rss_raw': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
              'analysis_sha256': {n: t019.sha(CODE_ROOT / n) for n in ('experiments/t020.py', 'experiments/t019.py', 'experiments/audit_t014.py', 'experiments/audit_t016.py')},
              'limitations': ['ranges are uncalibrated hypotheses', 'perturbation sizes are arbitrary', 'one-at-a-time, no interactions',
                              'intervals are exploratory, no multiplicity adjustment', f'refused by the validity rule, not run: {list(REFUSED)}']}
    write_json(out / 'summary.json', result)
    print(json.dumps({k: result[k] for k in ('runs', 'independently_audited_transactions', 'elapsed_seconds')}), flush=True)
    print(json.dumps(analysis['hypotheses'], ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--data-root', type=Path, default=Path.cwd())
    args = parser.parse_args()
    run(args.data_root.resolve(), args.out.resolve())
