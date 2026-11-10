"""T018: isolated-process fingerprint compatibility and frozen T017 parity.

Every source mutation occurs in a temporary copy, before its subprocess starts.
The live package and archived results are read-only inputs. Existing output
directories are refused; no snapshot identity is rewritten to bypass a reader.
"""
import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from time import perf_counter

CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT))
from soccer_sim import league_economy as le
from soccer_sim.storage import canonical, digest, runtime, write_json
from experiments.audit_t014 import admission_audit, ledger_audit
from experiments.audit_t016 import cohort_audit

ARTIFACT_FILES = ('snapshot.json', 'events.jsonl', 'summary.json')
VARIANTS = {
    'cli_comment': ('soccer_sim/__main__.py', 'append', {'package', 'cli_entry'}),
    'package_analysis_comment': ('soccer_sim/experiment_t011.py', 'append', {'package', 'analysis'}),
    'package_analysis_add': ('soccer_sim/experiment_t018_probe.py', 'add', {'package', 'analysis'}),
    'root_analysis_comment': ('experiments/audit_t016.py', 'append', {'analysis'}),
    'root_analysis_add': ('experiments/t018_probe.py', 'add', {'analysis'}),
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def normalized(value):
    result = deepcopy(value)
    result.pop('identity', None)
    result.pop('snapshot_sha256', None)
    return result


def source_copy(target):
    for directory in ('soccer_sim', 'experiments'):
        for source in sorted((CODE_ROOT / directory).rglob('*.py')):
            if '__pycache__' in source.parts:
                continue
            dest = target / source.relative_to(CODE_ROOT)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, dest)


def file_hashes(directory):
    return {p.name: sha(p) for p in sorted(directory.iterdir()) if p.is_file()}


def same_artifacts(actual, expected):
    for name in ARTIFACT_FILES:
        assert (actual / name).read_bytes() == (expected / name).read_bytes(), (actual, name)


def run(data_root, out):
    if le.LEAGUE_SAVE_VERSION != 17:
        raise SystemExit('T018 requires its save17 source bundle; use the frozen T018 source for reproduction')
    identity = le.league_identity()
    assert identity['source_scope'] == 'league-runtime-files-v1'
    from soccer_sim.fingerprints import league_runtime_fingerprint, source_provenance
    provenance = source_provenance()
    assert provenance['runtime']['sha256'] == identity['source_sha256']
    reference = data_root / 'artifacts/t017/source_bundle'
    manifest = read(reference / 'manifest.json')
    for name, expected in manifest['files'].items():
        assert sha(reference / name) == expected, name
    spec = importlib.util.spec_from_file_location('_t018_reference', reference / 'soccer_sim/__init__.py',
        submodule_search_locations=[str(reference / 'soccer_sim')])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    old = importlib.import_module('_t018_reference.league_economy')
    assert old.league_identity() == manifest['identity']
    old_package = {p.name: sha(p) for p in (reference / 'soccer_sim').glob('*.py')}
    new_package = {p.name: sha(p) for p in (CODE_ROOT / 'soccer_sim').glob('*.py')}
    changed = sorted(k for k in old_package.keys() | new_package.keys() if old_package.get(k) != new_package.get(k))
    assert changed == ['fingerprints.py', 'league_economy.py'], changed
    out.mkdir(parents=True, exist_ok=False)
    started = perf_counter()
    calls = []

    def cli(code_root, *args, expected=0):
        command = [sys.executable, '-B', '-m', 'soccer_sim', *map(str, args)]
        began = perf_counter()
        result = subprocess.run(command, cwd=code_root, text=True, capture_output=True, timeout=60)
        calls.append({'cwd': str(code_root), 'argv': command, 'returncode': result.returncode,
                      'stdout': result.stdout, 'stderr': result.stderr, 'seconds': perf_counter()-began})
        # Preserve diagnostics even when the experiment stops at an assertion.
        write_json(out / 'commands.json', calls)
        assert result.returncode == expected, calls[-1]
        return result

    rows, transactions, admissions = [], 0, Counter()
    for mode, suffix in (('fixed', ''), ('sampled', le.POPULATION_SUFFIX)):
        for base in le.T011_ALL_CONDITIONS:
            condition = base + suffix
            directory = out / 'runs' / condition
            cli(CODE_ROOT, 'run', '--condition', 'league_' + condition, '--seed', 100,
                '--days', 35, '--out', directory)
            raw, summary = read(directory / 'snapshot.json'), read(directory / 'summary.json')
            current = le.run_league_world(seed=100, days=35, config=le.config_for_condition(condition))
            previous = old.run_league_world(seed=100, days=35, config=old.config_for_condition(condition))
            assert canonical(raw) == canonical(le._snapshot_payload_unchecked(current))
            assert (directory / 'events.jsonl').read_bytes() == b''.join(canonical(e) + b'\n' for e in current.events)
            assert summary == dict(le.summarize_league_world(current), snapshot_sha256=digest(raw))
            assert normalized(raw) == normalized(old._snapshot_payload_unchecked(previous))
            assert normalized(summary) == normalized(old.summarize_league_world(previous))
            assert read(directory / 'manifest.json')['source_provenance'] == provenance
            ledger, admission, cohort = ledger_audit(raw), admission_audit(raw), cohort_audit(raw, summary)
            transactions += ledger['transactions']; admissions.update(admission['counts'])
            admissions['requests'] += admission['requests']
            rows.append({'condition': condition, 'population': mode, 'days': 35, 'seed': 100,
                'cli_api_equal': True, 'frozen_t017_equal': True, 'snapshot_sha256': digest(raw),
                'ledger_audit': ledger, 'admission_audit': admission, 'cohort_audit': cohort})
        print(mode, 'seven CLI/API/T017 comparisons passed', flush=True)

    condition = 'open_solidarity' + le.POPULATION_SUFFIX
    parent = out / 'parent14'
    cli(CODE_ROOT, 'run', '--condition', 'league_' + condition, '--seed', 100, '--days', 14, '--out', parent)
    original_parent = file_hashes(parent)
    baseline = out / 'base'

    def chain(code_root, destination):
        paths = {key: destination / key for key in ('resumed35', 'branch28', 'branch_resumed35', 'double35', 'double_resumed42')}
        cli(code_root, 'resume', '--snapshot', parent / 'snapshot.json', '--days', 21, '--out', paths['resumed35'])
        cli(code_root, 'branch', '--snapshot', parent / 'snapshot.json', '--kind', 'player_trust',
            '--player', 'p3', '--coach', 'c1', '--value', '0.05', '--reason', 'T018 trust comparison',
            '--days', 14, '--out', paths['branch28'])
        # Use the baseline saved branch for changed-source copies: this checks
        # loading already-recorded parent hashes across non-runtime edits.
        branch_input = (baseline / 'branch28' if destination != baseline else paths['branch28']) / 'snapshot.json'
        cli(code_root, 'resume', '--snapshot', branch_input, '--days', 7, '--out', paths['branch_resumed35'])
        cli(code_root, 'branch', '--snapshot', branch_input, '--kind', 'coach_skill_estimate',
            '--player', 'p3', '--coach', 'c1', '--value', '0.40', '--reason', 'T018 second recorded intervention',
            '--days', 7, '--out', paths['double35'])
        cli(code_root, 'resume', '--snapshot', paths['double35'] / 'snapshot.json',
            '--days', 7, '--out', paths['double_resumed42'])
        assert file_hashes(parent) == original_parent
        return paths

    base_paths = chain(CODE_ROOT, baseline)
    same_artifacts(base_paths['resumed35'], out / 'runs' / condition)
    expected = le.fork_league_world(le.load_league_snapshot(parent / 'snapshot.json'),
        kind='player_trust', player_id='p3', coach_id='c1', value=0.05, reason='T018 trust comparison')
    le.advance_league_days(expected, 14)
    assert read(base_paths['branch28'] / 'snapshot.json') == le.snapshot_payload(expected)
    double = le.fork_league_world(expected, kind='coach_skill_estimate', player_id='p3', coach_id='c1',
        value=0.4, reason='T018 second recorded intervention')
    le.advance_league_days(expected, 7)
    le.advance_league_days(double, 14)
    assert read(base_paths['branch_resumed35'] / 'snapshot.json') == le.snapshot_payload(expected)
    assert read(base_paths['double_resumed42'] / 'snapshot.json') == le.snapshot_payload(double)
    base_guard = {name: file_hashes(path) for name, path in base_paths.items()}

    variants, runtime_mutations, rejected = [], [], []
    with tempfile.TemporaryDirectory(prefix='soccer-t018-') as temporary:
        for label, (filename, operation, affected) in VARIANTS.items():
            copy = Path(temporary) / label
            source_copy(copy)
            target = copy / filename
            if operation == 'add':
                assert not target.exists()
            else:
                assert target.is_file()
            before = sha(target) if target.exists() else None
            with target.open('a') as stream:
                stream.write('\n# T018 isolated non-runtime compatibility probe.\n')
            paths = chain(copy, out / 'variants' / label)
            for name, directory in paths.items():
                same_artifacts(directory, base_paths[name])
                actual = read(directory / 'manifest.json')
                reference_manifest = read(base_paths[name] / 'manifest.json')
                actual_provenance = actual.pop('source_provenance')
                reference_provenance = reference_manifest.pop('source_provenance')
                assert actual == reference_manifest, (label, name, 'manifest controls')
                differing = {key for key in reference_provenance if actual_provenance[key] != reference_provenance[key]}
                assert differing == affected, (label, name, differing)
            variants.append({'variant': label, 'path': filename, 'operation': operation,
                'before_sha256': before, 'after_sha256': sha(target),
                'changed_provenance_sections': sorted(affected), 'operations_equal': list(paths),
                'snapshot_events_summary_byte_equal': True, 'recorded_parent_hashes_equal': True})
            print(label, 'five resume/branch operations byte-equal', flush=True)

        # File-level guard: every member changes the identity even on a comment.
        # Run fingerprinting in a fresh interpreter; never describe new bytes
        # as if they were code already loaded in the current process.
        for index, filename in enumerate(provenance['runtime']['files']):
            copy = Path(temporary) / f'runtime-{index}'
            source_copy(copy)
            with (copy / 'soccer_sim' / filename).open('a') as stream:
                stream.write('\n# T018 runtime file mutation probe.\n')
            result = cli(copy, 'resume', '--snapshot', parent / 'snapshot.json', '--days', 0,
                         '--out', out / 'rejected' / f'runtime-{index}', expected=2)
            assert 'snapshot.identity.source_sha256' in result.stderr, filename
            assert not (out / 'rejected' / f'runtime-{index}').exists()
            runtime_mutations.append(filename)

        negative_mutations = {
            'actual_rule': ('soccer_sim/league_economy.py',
                lambda text: text.replace('match_ticks: int = 40', 'match_ticks: int = 41', 1), 'snapshot.identity.source_sha256'),
            'undeclared_internal': ('soccer_sim/money.py',
                lambda text: text + '\nfrom . import t018_unlisted\n', 'dependency'),
            'dynamic_import': ('soccer_sim/money.py',
                lambda text: text + '\ndef t018_probe():\n    return __import__("soccer_sim.t018_unlisted")\n', 'dynamic'),
        }
        for label, (filename, mutate, expected_text) in negative_mutations.items():
            copy = Path(temporary) / label
            source_copy(copy)
            target = copy / filename
            before = target.read_text(); after = mutate(before)
            assert before != after
            target.write_text(after)
            (copy / 'soccer_sim/t018_unlisted.py').write_text('# deliberately unregistered module\n')
            target_out = out / 'rejected' / label
            response = cli(copy, 'resume', '--snapshot', parent / 'snapshot.json', '--days', 0,
                '--out', target_out, expected=2)
            assert expected_text in response.stderr.lower(), response.stderr
            assert not target_out.exists()
            rejected.append({'case': label, 'output_absent': True, 'error': response.stderr.strip()})

    for label, field, value in (('unknown_scope', 'source_scope', 'unknown-runtime-scope'),
                                ('bad_hash', 'source_sha256', '0' * 64), ('old_save', 'save', 16)):
        malformed = read(parent / 'snapshot.json')
        malformed['identity'][field] = value
        path = out / 'negative_inputs' / (label + '.json')
        write_json(path, malformed)
        target = out / 'rejected' / label
        response = cli(CODE_ROOT, 'resume', '--snapshot', path, '--days', 0, '--out', target, expected=2)
        assert 'snapshot.identity' in response.stderr and not target.exists()
        rejected.append({'case': label, 'output_absent': True, 'error': response.stderr.strip()})
    legacy = data_root / 'artifacts/t017/comparison/parent14/snapshot.json'
    legacy_hash = sha(legacy)
    response = cli(CODE_ROOT, 'resume', '--snapshot', legacy, '--days', 0,
        '--out', out / 'rejected' / 't017', expected=2)
    assert 'snapshot.identity' in response.stderr and not (out / 'rejected' / 't017').exists()
    assert sha(legacy) == legacy_hash
    rejected.append({'case': 'actual_t017_save16', 'output_absent': True, 'error': response.stderr.strip()})
    assert file_hashes(parent) == original_parent
    assert all(file_hashes(path) == base_guard[name] for name, path in base_paths.items())
    assert le.league_identity() == identity
    assert source_provenance() == provenance, 'source must remain unchanged during this experiment'
    result = {'task': 'T018', 'identity': identity, 'evaluation_version': le.LEAGUE_EVALUATION_VERSION,
        'previous_identity': old.league_identity(), 'runtime': runtime(), 'source_provenance': provenance,
        'changed_package_files': changed, 'comparisons': rows, 'cli_api_equal': len(rows),
        'frozen_t017_equal': len(rows), 'non_runtime_variants': variants,
        'runtime_file_edits_rejected': runtime_mutations, 'negative_cases': rejected,
        'parent_and_base_branches_unchanged': True, 'baseline_chain_api_equal': True,
        'independently_audited_transactions': transactions, 'admission_totals': dict(admissions),
        'elapsed_seconds': perf_counter() - started,
        'limits': ['35-day one-seed comparison is not new robustness evidence',
                   'file-level conservative scope; co-located analysis changes still invalidate',
                   'static dependency contract, not arbitrary Python or malicious code safety',
                   'save16 and older require their frozen source; no automatic migration',
                   'source immutable during a run; no external data/model dependency support']}
    write_json(out / 'summary.json', result)
    print(json.dumps({k: result[k] for k in ('cli_api_equal', 'frozen_t017_equal',
        'independently_audited_transactions', 'elapsed_seconds')}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--data-root', type=Path, default=Path.cwd())
    args = parser.parse_args()
    run(args.data_root.resolve(), args.out.resolve())
