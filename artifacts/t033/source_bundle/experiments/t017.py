"""T017: verify sampled CLI entry points against APIs and frozen T016."""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from time import perf_counter

CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT))
from soccer_sim import league_economy as le
from soccer_sim.storage import canonical, digest, runtime, write_json
from experiments.audit_t014 import admission_audit, ledger_audit
from experiments.audit_t016 import cohort_audit


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def normalized(value):
    result = deepcopy(value)
    result.pop('identity', None)
    result.pop('snapshot_sha256', None)
    return result


def run(data_root, out):
    identity = le.league_identity()
    reference = data_root / 'artifacts/t016/source_bundle'
    manifest = read(reference / 'manifest.json')
    for name, expected in manifest['files'].items():
        assert sha(reference / name) == expected, name
    old_files = {p.name: sha(p) for p in (reference / 'soccer_sim').glob('*.py')}
    now_files = {p.name: sha(p) for p in (CODE_ROOT / 'soccer_sim').glob('*.py')}
    assert old_files.keys() == now_files.keys()
    changed = sorted(k for k in old_files if old_files[k] != now_files[k])
    assert changed == ['__main__.py'], changed
    spec = importlib.util.spec_from_file_location('_t017_reference', reference / 'soccer_sim/__init__.py',
                                                 submodule_search_locations=[str(reference / 'soccer_sim')])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    old = importlib.import_module('_t017_reference.league_economy')
    assert old.league_identity() == manifest['identity']
    out.mkdir(parents=True, exist_ok=False)
    start = perf_counter()
    calls = []

    def cli(*args, expected=0):
        command = [sys.executable, '-B', '-m', 'soccer_sim', *map(str, args)]
        began = perf_counter()
        result = subprocess.run(command, cwd=CODE_ROOT, text=True, capture_output=True, timeout=60)
        calls.append({'argv': command, 'returncode': result.returncode,
                      'stdout': result.stdout, 'stderr': result.stderr, 'seconds': perf_counter()-began})
        assert result.returncode == expected, calls[-1]
        return result

    help_text = cli('run', '--help').stdout
    sampled_names = ['league_' + name + le.POPULATION_SUFFIX for name in le.T011_ALL_CONDITIONS]
    assert all(name in help_text for name in sampled_names)
    rows = []
    transactions, admissions = 0, Counter()
    for mode, suffix in (('fixed', ''), ('sampled', le.POPULATION_SUFFIX)):
        for base in le.T011_ALL_CONDITIONS:
            condition = base + suffix
            directory = out / 'runs' / condition
            cli('run', '--condition', 'league_' + condition, '--seed', 100, '--days', 35, '--out', directory)
            raw, summary = read(directory / 'snapshot.json'), read(directory / 'summary.json')
            config = le.config_for_condition(condition)
            assert raw['config'] == config.to_dict()
            assert raw['config']['population_mode'] == mode
            current = le.run_league_world(seed=100, days=35, config=config)
            previous = old.run_league_world(seed=100, days=35, config=old.config_for_condition(condition))
            expected = le._snapshot_payload_unchecked(current)
            assert canonical(raw) == canonical(expected)
            assert (directory / 'events.jsonl').read_bytes() == b''.join(canonical(e) + b'\n' for e in current.events)
            current_summary = le.summarize_league_world(current)
            assert summary == dict(current_summary, snapshot_sha256=digest(raw))
            assert normalized(raw) == normalized(old._snapshot_payload_unchecked(previous))
            assert normalized(summary) == normalized(old.summarize_league_world(previous))
            ledger, admission, cohort = ledger_audit(raw), admission_audit(raw), cohort_audit(raw, summary)
            transactions += ledger['transactions']; admissions.update(admission['counts'])
            admissions['requests'] += admission['requests']
            rows.append({'condition': condition, 'population': mode, 'seed': 100, 'days': 35,
                         'cli_api_state_events_summary_equal': True, 'frozen_t016_state_and_summary_equal': True,
                         'snapshot_sha256': digest(raw), 'ledger_audit': ledger,
                         'admission_audit': admission, 'cohort_audit': cohort})
        print(mode, 'seven CLI/API/frozen comparisons verified', flush=True)

    condition = 'open_solidarity' + le.POPULATION_SUFFIX
    parent, resumed = out / 'parent14', out / 'resumed35'
    cli('run', '--condition', 'league_' + condition, '--seed', 100, '--days', 14, '--out', parent)
    parent_before = {p.name: sha(p) for p in parent.iterdir()}
    cli('resume', '--snapshot', parent / 'snapshot.json', '--days', 21, '--out', resumed)
    resume_equal = {name: (resumed / name).read_bytes() == (out / 'runs' / condition / name).read_bytes()
                    for name in ('snapshot.json', 'events.jsonl', 'summary.json')}
    assert all(resume_equal.values())
    branch, branch_resumed = out / 'branch28', out / 'branch_resumed35'
    reason = 'T017 sampled CLI trust branch'
    cli('branch', '--snapshot', parent / 'snapshot.json', '--kind', 'player_trust', '--player', 'p3',
        '--coach', 'c1', '--value', '0.05', '--reason', reason, '--days', 14, '--out', branch)
    expected = le.fork_league_world(le.load_league_snapshot(parent / 'snapshot.json'),
        kind='player_trust', player_id='p3', coach_id='c1', value=0.05, reason=reason)
    le.advance_league_days(expected, 14)
    assert canonical(read(branch / 'snapshot.json')) == canonical(le.snapshot_payload(expected))
    cli('resume', '--snapshot', branch / 'snapshot.json', '--days', 7, '--out', branch_resumed)
    le.advance_league_days(expected, 7)
    assert canonical(read(branch_resumed / 'snapshot.json')) == canonical(le.snapshot_payload(expected))
    assert read(branch_resumed / 'snapshot.json')['config']['population_mode'] == 'sampled'
    assert parent_before == {p.name: sha(p) for p in parent.iterdir()}

    invalids = ['baseline_sampled_population', 'league_unknown_sampled_population',
                'league_open_solidarity_sampled_population_sampled_population']
    for index, bad in enumerate(invalids):
        target = out / f'invalid-{index}'
        cli('run', '--condition', bad, '--seed', 100, '--days', 0, '--out', target, expected=2)
        assert not target.exists()
    old_snapshot = data_root / 'artifacts/t016/comparison/representative/open_solidarity_sampled_population/checkpoint/snapshot.json'
    old_hash = sha(old_snapshot)
    rejected = out / 'rejected-old-checkpoint'
    response = cli('resume', '--snapshot', old_snapshot, '--days', 0, '--out', rejected, expected=2)
    assert 'identity' in response.stderr and not rejected.exists() and sha(old_snapshot) == old_hash
    cli('run', '--seed', 100, '--days', 0, '--out', out / 'default')
    cli('run', '--seed', 100, '--condition', 'baseline', '--days', 0, '--out', out / 'baseline')
    for filename in ('snapshot.json', 'events.jsonl'):
        assert (out / 'default' / filename).read_bytes() == (out / 'baseline' / filename).read_bytes()
    assert le.league_identity() == identity
    result = {'task': 'T017', 'identity': identity, 'evaluation_version': le.LEAGUE_EVALUATION_VERSION,
              'old_identity': old.league_identity(), 'runtime': runtime(), 'changed_package_files': changed,
              'comparisons': rows, 'cli_api_equal': len(rows), 'frozen_t016_equal': len(rows),
              'sampled_resume_equal': resume_equal, 'sampled_branch_and_resume_api_equal': True,
              'parent_unchanged': True, 'help_has_all_seven_sampled_conditions': True,
              'invalid_conditions_rejected_without_output': invalids, 'old_checkpoint_rejected_without_output': True,
              'default_baseline_unchanged': True, 'independently_audited_transactions': transactions,
              'admission_totals': dict(admissions), 'elapsed_seconds': perf_counter()-start,
              'analysis_sha256': {str(p.relative_to(CODE_ROOT)): sha(p) for p in
                 (Path(__file__), CODE_ROOT/'experiments/audit_t014.py', CODE_ROOT/'experiments/audit_t016.py')},
              'limits': ['one seed and 35-day CLI integration, not a new robustness study',
                         'same save16 but older source hashes still rejected', 'no population calibration or new world rules']}
    write_json(out / 'summary.json', result)
    write_json(out / 'commands.json', calls)
    print(json.dumps({k: result[k] for k in ('cli_api_equal', 'frozen_t016_equal', 'independently_audited_transactions', 'elapsed_seconds')}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--data-root', type=Path, default=Path.cwd())
    args = parser.parse_args()
    run(args.data_root.resolve(), args.out.resolve())
