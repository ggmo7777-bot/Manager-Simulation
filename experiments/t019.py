"""T019: 2^3 factorial decomposition of the fixed/sampled initial population (pre-registered).

Factors (bit order F1 F2 F3): profile, first impression, p3/p9 initial satisfaction.
Seeds 100..129 reproduce T016 (exploratory); seeds 200..229 are the confirmation set.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor
from copy import deepcopy
import hashlib
import itertools
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
from soccer_sim.storage import canonical, runtime, write_json
from experiments.audit_t014 import admission_audit, ledger_audit
from experiments.audit_t016 import cohort_audit

CELLS = tuple(''.join(b) for b in itertools.product('01', repeat=3))
INSTITUTIONS = ('closed_no_solidarity', 'open_no_solidarity', 'open_solidarity')
EXPLORE, CONFIRM = tuple(range(100, 130)), tuple(range(200, 230))
DAYS = 84
METRICS = ('academy_initial_cohort_skill_gain', 'mean_skill_gain', 'academy_clubs_points')
# (label, metric, left institution, right institution); the first is the primary indicator.
CONTRASTS = (('reinvest', METRICS[0], 'open_solidarity', 'open_no_solidarity'),
             ('open_vs_closed', METRICS[0], 'open_no_solidarity', 'closed_no_solidarity'),
             ('reinvest_all12', METRICS[1], 'open_solidarity', 'open_no_solidarity'),
             ('reinvest_academy_points', METRICS[2], 'open_solidarity', 'open_no_solidarity'))
FACTORS = ('F1_profile', 'F2_impression', 'F3_satisfaction')
T_CRIT_29 = 2.045


def condition(base, bits):
    """Corner cells use the existing fixed/sampled names; mixed cells use the factors suffix."""
    return {'000': base, '111': base + le.POPULATION_SUFFIX}.get(bits, f'{base}{le.FACTORS_SUFFIX}{bits}')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalized(value):
    result = deepcopy(value)
    result.pop('identity', None)
    result.pop('snapshot_sha256', None)
    return result


def anonymous(raw):
    """Drop the condition label (config, init event) that differs between equivalent cells."""
    result = deepcopy(normalized(raw))
    result.pop('config')
    for event in result['events']:
        event['data'].pop('condition', None)
    return result


def initial_state(seed, base, bits):
    raw = le._snapshot_payload_unchecked(le.run_league_world(seed=seed, days=0, config=le.config_for_condition(condition(base, bits))))
    return raw


def profile_part(raw):
    return {'players': raw['players'], 'initial_players': raw['initial_players'],
            'minds': {p: {k: v for k, v in m.items() if k != 'playing_time_satisfaction'}
                      for p, m in raw['player_minds'].items()}}


def recompute_states(seed):
    """Independent re-derivation of the three generated parts from keyed draws (not le._sample_*)."""
    def u(channel, token, low, high):
        return round(le._population_draw(seed, channel, token, low, high), 6)
    ranges = {'academy': ((0.42, 0.52), (0.104, 0.126), (0.90, 0.95)), 'buyer': ((0.34, 0.64), (0.020, 0.046), (0.64, 0.88))}
    sampled = {}
    for cid, pids in le.INITIAL_CLUB_ROSTERS.items():
        tr, rt, cp = ranges['academy' if cid in le.ACADEMY_CLUB_IDS else 'buyer']
        for pid in pids:
            t = [u(f'trait{i}', pid, *tr) for i in range(3)]
            sampled[pid] = {'traits': t, 'A': u('rateA', pid, *rt), 'B': u('rateB', pid, *rt),
                            'cap': round(max(u('cap', pid, *cp), min(0.98, max(t) + 0.05)), 6),
                            'self_est': round(min(0.95, max(0.05, sum(t) / 3 + u('self_bias', pid, -0.03, 0.03))), 6)}
    return sampled


def check_cell(seed, base, bits, fixed, sampled):
    """The three factors of one cell equal the definition, using hand-written expectations."""
    raw = initial_state(seed, base, bits)
    f1, f2, f3 = (int(b) for b in bits)
    for pid, player in raw['players'].items():
        if f1:
            s = sampled[pid]
            assert [player['traits'][k] for k in ('pace', 'passing', 'shooting')] == s['traits'], (bits, pid)
            assert player['rates'] == {'A': s['A'], 'B': s['B']} and player['cap'] == s['cap']
            assert raw['player_minds'][pid]['self_estimate'] == s['self_est']
        else:
            assert player == fixed['players'][pid] and raw['player_minds'][pid]['self_estimate'] == fixed['player_minds'][pid]['self_estimate']
        want = 0.42 if (not f3 and pid in le.TALENT_PLAYER_IDS) else 0.50
        assert raw['player_minds'][pid]['playing_time_satisfaction'] == want, (bits, pid)
    levels = {pid: sum(p['traits'][k] for k in ('pace', 'passing', 'shooting')) / 3 for pid, p in raw['players'].items()}
    scripted = {('c1', 'p3'): (0.39, 0.37), ('c2', 'p3'): (0.50, 0.52), ('c4', 'p3'): (0.48, 0.49),
                ('c3', 'p9'): (0.38, 0.36), ('c4', 'p9'): (0.50, 0.52), ('c2', 'p9'): (0.47, 0.48)}
    for coach, mind in raw['coaches'].items():
        for pid, ev in mind['evaluations'].items():
            if f2:
                want = tuple(round(min(0.95, max(0.05, levels[pid] + le._population_draw(seed, ch, f'{coach}:{pid}', -0.06, 0.06))), 6)
                             for ch in ('coach_skill', 'coach_match'))
            else:
                self_est = raw['player_minds'][pid]['self_estimate']
                want = scripted.get((coach, pid), (self_est, self_est))
            assert (ev['skill_estimate'], ev['match_rating_estimate']) == want, (bits, coach, pid)
    return raw


def check_independence(seeds):
    """Flip one factor at a time and verify which parts of the initial world may change."""
    n = 0
    for seed in seeds:
        fixed, sampled = initial_state(seed, 'open_solidarity', '000'), recompute_states(seed)
        cells = {bits: check_cell(seed, 'open_solidarity', bits, fixed, sampled) for bits in CELLS}
        for bits in CELLS:
            for k in range(3):
                other = bits[:k] + str(1 - int(bits[k])) + bits[k + 1:]
                a, b = cells[bits], cells[other]
                same = {'profile': profile_part(a) == profile_part(b),
                        'impression': a['coaches'] == b['coaches'],
                        'satisfaction': {p: m['playing_time_satisfaction'] for p, m in a['player_minds'].items()}
                        == {p: m['playing_time_satisfaction'] for p, m in b['player_minds'].items()}}
                skip = ('players', 'initial_players', 'player_minds', 'coaches')
                assert {k: v for k, v in anonymous(a).items() if k not in skip} == {k: v for k, v in anonymous(b).items() if k not in skip}
                if k == 0:
                    assert not same['profile']
                else:
                    assert same['profile'], (seed, bits, k)
                if k == 2:
                    assert not same['satisfaction'] and same['impression'], (seed, bits)
                else:
                    assert same['satisfaction'], (seed, bits, k)
                if k == 1:
                    assert not same['impression']
                n += 1
    return {'seeds': len(seeds), 'cells': len(CELLS), 'single_factor_flips_checked': n,
            'note': 'F2 depends on F1 by definition (impressions are readings of the chosen profile); F3 changes only p3,p9 satisfaction'}


def check_regression(root):
    reference = root / 'artifacts/t018/rerun-py313'
    summary = json.loads((reference / 'summary.json').read_text())
    rows = []
    for mode, suffix in (('fixed', ''), ('sampled', le.POPULATION_SUFFIX)):
        for base in le.T011_ALL_CONDITIONS:
            name = base + suffix
            directory = reference / 'runs' / name
            world = le.run_league_world(seed=100, days=35, config=le.config_for_condition(name))
            raw = le._snapshot_payload_unchecked(world)
            assert normalized(raw) == normalized(json.loads((directory / 'snapshot.json').read_text())), name
            assert b''.join(canonical(e) + b'\n' for e in world.events) == (directory / 'events.jsonl').read_bytes(), name
            assert normalized(le.summarize_league_world(world)) == normalized(json.loads((directory / 'summary.json').read_text())), name
            assert 'profile_mode' not in raw['config']
            rows.append(name)
    return {'conditions_equal': len(rows), 'excluded': ['identity', 'snapshot_sha256'], 'reference': str(reference.relative_to(root))}


def worker(job):
    cell, institution, seed = job
    world = le.run_league_world(seed=seed, days=DAYS, config=le.config_for_condition(condition(institution, cell)))
    raw = le._snapshot_payload_unchecked(world)
    summary = le.summarize_league_world(world)
    cohort_audit(raw, summary)
    ledger, admission = ledger_audit(raw), admission_audit(raw)
    return {'cell': cell, 'institution': institution, 'seed': seed, 'condition': condition(institution, cell),
            'metrics': {m: summary[m] for m in METRICS}, 'cohort_count': summary['academy_initial_cohort_count'],
            'transactions': ledger['transactions'], 'admission': admission['counts'], 'requests': admission['requests']}


def stats(values):
    mean, sd = statistics.fmean(values), statistics.stdev(values)
    half = T_CRIT_29 * sd / len(values) ** 0.5
    return {'n_worlds': len(values), 'mean': round(mean, 6), 'sd': round(sd, 6),
            'ci95_exploratory': [round(mean - half, 6), round(mean + half, 6)],
            'positive': sum(v > 0 for v in values), 'negative': sum(v < 0 for v in values), 'zero': sum(v == 0 for v in values)}


def analyse(rows, seeds):
    table = {(r['cell'], r['institution'], r['seed']): r for r in rows}
    out: dict[str, Any] = {}
    for label, metric, left, right in CONTRASTS:
        # Per-seed paired difference rounded as in T016, then cell effects per seed.
        y = {c: [round(table[c, left, s]['metrics'][metric] - table[c, right, s]['metrics'][metric], 6) for s in seeds] for c in CELLS}
        part: dict[str, Any] = {'cells': {c: stats(y[c]) for c in CELLS}}
        sign = lambda c, ks: (-1) ** sum(1 - int(c[k]) for k in ks)
        contrasts = {}
        for name, ks in (('F1', (0,)), ('F2', (1,)), ('F3', (2,)), ('F1xF2', (0, 1)), ('F1xF3', (0, 2)),
                         ('F2xF3', (1, 2)), ('F1xF2xF3', (0, 1, 2))):
            vector = [sum(sign(c, ks) * y[c][i] for c in CELLS) / 4 for i in range(len(seeds))]
            contrasts[name] = stats(vector)
        part['mean_contrasts_2cubed'] = contrasts
        edges = {}
        for k, name in enumerate(FACTORS):
            for rest in itertools.product('01', repeat=2):
                lo, hi = (''.join(rest[:k]) + b + ''.join(rest[k:]) for b in '01')
                edges[f'{name}|others={"".join(rest)}'] = stats([y[hi][i] - y[lo][i] for i in range(len(seeds))])
        part['single_factor_flips'] = edges
        total = statistics.fmean(y['111']) - statistics.fmean(y['000'])
        paths = {}
        for order in itertools.permutations(range(3)):
            bits, previous, steps = ['0'] * 3, statistics.fmean(y['000']), []
            for k in order:
                bits[k] = '1'
                now = statistics.fmean(y[''.join(bits)])
                steps.append({'factor': FACTORS[k], 'delta': round(now - previous, 6),
                              'share_of_total': round((now - previous) / total, 4) if total else None})
                previous = now
            paths['>'.join(f.split('_')[0] for f in (FACTORS[k] for k in order))] = steps
        part['order_dependence'] = {'total_111_minus_000': round(total, 6), 'paths': paths}
        out[label] = part
    cohort = sorted({r['cohort_count'] for r in rows})
    out['non_empty'] = {'cohort_counts': cohort, 'worlds_per_cell_institution': len(seeds)}
    return out


def check_cli(out, root):
    """Representative mixed cell through the ordinary CLI, with a recorded save and resume."""
    name = 'league_' + condition('open_solidarity', '101')
    def run(*args):
        result = subprocess.run([sys.executable, '-B', '-m', 'soccer_sim', *map(str, args)], cwd=CODE_ROOT, text=True, capture_output=True, timeout=120)
        assert result.returncode == 0, result.stderr
    run('run', '--condition', name, '--seed', 100, '--days', 14, '--out', out / 'cli/day14')
    run('resume', '--snapshot', out / 'cli/day14/snapshot.json', '--days', 21, '--out', out / 'cli/resumed35')
    run('run', '--condition', name, '--seed', 100, '--days', 35, '--out', out / 'cli/direct35')
    for f in ('snapshot.json', 'events.jsonl', 'summary.json'):
        assert (out / 'cli/resumed35' / f).read_bytes() == (out / 'cli/direct35' / f).read_bytes(), f
    world = le.run_league_world(seed=100, days=35, config=le.config_for_condition(condition('open_solidarity', '101')))
    assert json.loads((out / 'cli/direct35/snapshot.json').read_text()) == le.snapshot_payload(world)
    return {'condition': name, 'save_day14_resume_to35_equals_direct': True, 'cli_equals_api': True}


def run(root, out):
    assert le.LEAGUE_SAVE_VERSION == 17 and le.LEAGUE_EVALUATION_VERSION == 't016-initial-cohort-v1'
    out.mkdir(parents=True, exist_ok=False)
    start = perf_counter()
    identity = le.league_identity()
    regression = check_regression(root)
    print('regression', regression, flush=True)
    independence = check_independence((100, 101, 102, 200))
    print('independence', independence, flush=True)
    cli = check_cli(out, root)
    jobs = [(c, i, s) for c in CELLS for i in INSTITUTIONS for s in EXPLORE + CONFIRM]
    with ProcessPoolExecutor() as pool:
        rows = list(pool.map(worker, jobs, chunksize=12))
    write_json(out / 'runs.json', rows)
    analyses = {}
    for label, seeds in (('explore_100_129', EXPLORE), ('confirm_200_229', CONFIRM)):
        analyses[label] = analyse([r for r in rows if r['seed'] in seeds], seeds)
    # Corner reproduction of T016 (condition 3): exact per-seed equality with the archived paired differences.
    t016 = json.loads((root / 'artifacts/t016/comparison/summary.json').read_text())
    table = {(r['cell'], r['institution'], r['seed']): r for r in rows}
    corners = {}
    for mode, cell in (('fixed', '000'), ('sampled', '111')):
        archived = next(e for e in t016['paired_effects'] if e['population'] == mode and e['metric'] == METRICS[0]
                        and e['contrast'] == 'open_solidarity-open_no_solidarity')
        mine = [{'seed': s, 'difference': round(table[cell, 'open_solidarity', s]['metrics'][METRICS[0]]
                                                 - table[cell, 'open_no_solidarity', s]['metrics'][METRICS[0]], 6)} for s in EXPLORE]
        assert mine == archived['paired_differences'], mode
        assert analyses['explore_100_129']['reinvest']['cells'][cell]['mean'] == archived['mean']
        corners[mode] = {'cell': cell, 'mean': archived['mean'], 'paired_differences_equal_t016': True}
    result = {'task': 'T019', 'identity': identity, 'runtime': runtime(), 'days': DAYS, 'cells': list(CELLS),
              'seeds': {'explore': [EXPLORE[0], EXPLORE[-1]], 'confirm': [CONFIRM[0], CONFIRM[-1]]}, 'runs': len(rows),
              'regression': regression, 'independence': independence, 'cli': cli, 'corner_reproduction': corners,
              'independently_audited_transactions': sum(r['transactions'] for r in rows),
              'analyses': analyses, 'elapsed_seconds': perf_counter() - start,
              'max_rss_raw': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
              'analysis_sha256': {n: sha(CODE_ROOT / n) for n in ('experiments/t019.py', 'experiments/audit_t014.py', 'experiments/audit_t016.py')},
              'limitations': ['uncalibrated ranges', 'F2 is conditional on F1 by definition',
                              'F1 includes academy/buyer type differences by design', 'confidence intervals are exploratory, no multiplicity adjustment']}
    write_json(out / 'summary.json', result)
    print(json.dumps({k: result[k] for k in ('runs', 'independently_audited_transactions', 'elapsed_seconds')}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--data-root', type=Path, default=Path.cwd())
    args = parser.parse_args()
    run(args.data_root.resolve(), args.out.resolve())
