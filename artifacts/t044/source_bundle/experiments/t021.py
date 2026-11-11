"""T021: run the three T020-refused perturbations under the pre-registered rules R1 (cap ceiling 0.98) and R2 (symmetric width truncation).

Reruns the 16 T020 cells as a byte-for-byte reproduction check and joins the 3 new cells into the 19-cell table.
Seeds 300..329; closed/open without reinvestment and open with reinvestment, 84 days. Ranges are not changed.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path
import resource
import sys
from time import perf_counter

CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT))
from soccer_sim import league_economy as le
from soccer_sim.storage import runtime, write_json
from experiments import t019, t020

NEW = tuple(le.RANGE_EXTENSIONS)
CELLS = t020.CELLS + NEW
# Hand-written effective ranges (pre-registered in T021-PROTOCOL; academy, buyer), not derived from le.RANGE_*.
EXPECT = {
    'cap_w15_x': {'cap': ((0.8875, 0.9625), (0.58, 0.94))},
    'cap_cp_x': {'cap': ((0.93, 0.98), (0.67, 0.91))},
    'psych_w15_x': {'loyalty': (0.05, 0.30), 'ambition': (0.25, 0.85), 'autonomy': (0.305, 0.395)},
}


def configure_t020():
    """Point the T020 helpers at the 19-cell table (process-local; T020 files are untouched)."""
    t020.EXPECT.update(EXPECT)
    le.RANGE_PERTURBATIONS.update(le.RANGE_EXTENSIONS)  # group lookup for the hypothesis rules; sampled_ranges still uses RANGE_EXTENSIONS
    t020.CELLS, t020.EXECUTED, t020.REFUSED = CELLS, t020.EXECUTED + NEW, ()


def check_refusals():
    """T020's refused names stay refused; the new names are accepted with exactly the pre-registered ranges; shifts are never shrunk."""
    for name in ('cap_w15', 'psych_w15', 'cap_cp', 'trait_w25', 'none', 'cap_w15_y'):
        try:
            le.config_for_condition(f'open_solidarity{le.POPULATION_SUFFIX}{le.RANGE_SUFFIX}{name}')
        except ValueError:
            continue
        raise AssertionError(name)
    table = {}
    for name in NEW:
        ranges = le.sampled_ranges(name)
        table[name] = {f'{k}:{p}': list(v) for (k, p), v in ranges.items() if v != le.RANGE_BASE[k, p]}
    assert table == {'cap_w15_x': {'academy:cap': [0.8875, 0.9625], 'buyer:cap': [0.58, 0.94]},
                     'cap_cp_x': {'academy:cap': [0.93, 0.98], 'buyer:cap': [0.67, 0.91]},
                     'psych_w15_x': {'all:ambition': [0.25, 0.85], 'all:autonomy': [0.305, 0.395]}}, table
    return {'refused_unchanged': ['cap_w15', 'psych_w15', 'cap_cp'], 'accepted_ranges': table}


def reproduce(rows, reference):
    old = json.loads(reference.read_text())
    key = lambda r: (r['cell'], r['institution'], r['seed'])
    new = {key(r): r for r in rows if r['cell'] in t020.CELLS[:16]}
    assert len(old) == len(new) == 1440 and sorted(map(key, old)) == sorted(new), (len(old), len(new))
    assert all(json.dumps(r, sort_keys=True) == json.dumps(new[key(r)], sort_keys=True) for r in old)
    return {'rows_compared': len(old), 'identical': True}


def run(root, out):
    assert le.LEAGUE_SAVE_VERSION == 17 and le.LEAGUE_EVALUATION_VERSION == 't016-initial-cohort-v1'
    out.mkdir(parents=True, exist_ok=False)
    start = perf_counter()
    identity = le.league_identity()
    default_and_null = t020.check_null_and_default(root)
    print('default_and_null', default_and_null, flush=True)
    refusals = check_refusals()
    configure_t020()
    initial = t020.check_initial((300, 301))  # baseline, null and all 18 executed cells
    print('initial', initial, flush=True)
    cli = t020.check_cli(out)  # rate_w15: the T020 representative; the new names go through the same path as well
    name = 'league_' + t020.condition('open_solidarity', 'psych_w15_x')
    for args in (('run', '--condition', name, '--seed', 300, '--days', 14, '--out', out / 'cli_new/day14'),
                 ('resume', '--snapshot', out / 'cli_new/day14/snapshot.json', '--days', 21, '--out', out / 'cli_new/resumed35'),
                 ('run', '--condition', name, '--seed', 300, '--days', 35, '--out', out / 'cli_new/direct35')):
        t020.subprocess.run([sys.executable, '-B', '-m', 'soccer_sim', *map(str, args)], cwd=CODE_ROOT, check=True, capture_output=True)
    for f in ('snapshot.json', 'events.jsonl', 'summary.json'):
        assert (out / 'cli_new/resumed35' / f).read_bytes() == (out / 'cli_new/direct35' / f).read_bytes(), f
    cli_new = {'condition': name, 'save_day14_resume_to35_equals_direct': True}
    jobs = [(c, i, s) for c in CELLS for i in t020.INSTITUTIONS for s in t020.SEEDS]
    with ProcessPoolExecutor() as pool:
        rows = list(pool.map(t020.worker, jobs, chunksize=12))
    write_json(out / 'runs.json', rows)
    repro = reproduce(rows, CODE_ROOT / 'artifacts/t020/run/runs.json')
    print('reproduction', repro, flush=True)
    analysis = t020.analyse(rows)
    result = {'task': 'T021', 'identity': identity, 'runtime': runtime(), 'days': t020.DAYS, 'seeds': [t020.SEEDS[0], t020.SEEDS[-1]], 'cells': list(CELLS),
              'new_cells': list(NEW), 'runs': len(rows), 'default_and_null': default_and_null, 'refusals': refusals, 'initial_recomputation': initial,
              'cli': cli, 'cli_new': cli_new, 'reproduction_of_t020': repro, 'independently_audited_transactions': sum(r['transactions'] for r in rows),
              'analysis': analysis, 'elapsed_seconds': perf_counter() - start, 'max_rss_raw': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
              'analysis_sha256': {n: t019.sha(CODE_ROOT / n) for n in ('experiments/t021.py', 'experiments/t020.py', 'experiments/t019.py', 'experiments/audit_t014.py', 'experiments/audit_t016.py')},
              'limitations': ['ranges are uncalibrated hypotheses', 'cap ceiling 0.98 is an internal convention, not an external basis',
                              'psych_w15_x widens ambition and autonomy only (loyalty is bound at 0.05)', 'one-at-a-time, no interactions',
                              'intervals are exploratory, no multiplicity adjustment']}
    write_json(out / 'summary.json', result)
    print(json.dumps({k: result[k] for k in ('runs', 'independently_audited_transactions', 'elapsed_seconds')}), flush=True)
    print(json.dumps(analysis['hypotheses'], ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--data-root', type=Path, default=Path.cwd())
    args = parser.parse_args()
    run(args.data_root.resolve(), args.out.resolve())
