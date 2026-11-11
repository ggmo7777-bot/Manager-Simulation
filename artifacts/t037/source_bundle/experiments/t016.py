"""T016: paired initial-cohort evaluation with an archived T015 reference."""
import argparse
import ast
from collections import Counter
from copy import deepcopy
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import resource
import sys
from time import perf_counter

CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT))
from soccer_sim import league_economy as le
from soccer_sim.storage import canonical, digest, runtime, write_json
from experiments.audit_t014 import admission_audit, ledger_audit
from experiments.audit_t016 import cohort_audit, never_transferred_description, paired_effects

SEEDS = tuple(range(100, 130))
INSTITUTIONS = ('closed_no_solidarity', 'open_no_solidarity', 'open_solidarity')
LEGACY_FIELDS = ('academy_retained_skill_gain', 'mean_skill_gain', 'solidarity_dividends_paid_units',
                 'academy_clubs_points', 'buyer_clubs_points', 'total_transfers',
                 'academy_facility_days', 'buyer_facility_days')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_reference(root):
    directory = root / 'artifacts/t015/reference_before_t016/source_bundle'
    manifest = json.loads((directory / 'manifest.json').read_text())
    for name, expected in manifest['files'].items():
        assert sha(directory / name) == expected, name
    package = directory / 'soccer_sim'
    spec = importlib.util.spec_from_file_location('_t016_reference', package / '__init__.py',
                                                 submodule_search_locations=[str(package)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    reference = importlib.import_module('_t016_reference.league_economy')
    assert reference.league_identity() == manifest['identity']
    return reference, directory


def compare_states(past, now, branch=False):
    a, b = deepcopy(past), deepcopy(now)
    a.pop('identity'); b.pop('identity')
    if branch:
        # Intervention parents include the runtime identity, which differs by source version.
        def strip(value):
            if isinstance(value, dict):
                value.pop('parent_checkpoint_sha256', None)
                for child in value.values(): strip(child)
            elif isinstance(value, list):
                for child in value: strip(child)
        strip(a); strip(b)
    assert canonical(a) == canonical(b), 'Non-evaluation state or history changed'
    return {'full_state_and_events_equal': True, 'normalized_sha256': digest(a),
            'excluded': ['identity'] + (['parent_checkpoint_sha256'] if branch else [])}


def resume_check(full, partial, out):
    le.save_league_artifacts(partial, out / 'checkpoint')
    resumed = le.load_league_snapshot(out / 'checkpoint/snapshot.json')
    le.advance_league_days(resumed, full.day - partial.day)
    assert resumed == full
    le.save_league_artifacts(full, out / 'full')
    le.save_league_artifacts(resumed, out / 'resumed')
    results = {name: (out / 'full' / name).read_bytes() == (out / 'resumed' / name).read_bytes()
               for name in ('snapshot.json', 'events.jsonl', 'summary.json')}
    assert all(results.values())
    return results


def run(root, out):
    identity = le.league_identity()
    assert le.LEAGUE_EVALUATION_VERSION == 't016-initial-cohort-v1'
    old, reference_directory = load_reference(root)
    recorded_file = root / 'artifacts/t015/run1/comparison.json'
    recorded_hash = sha(recorded_file)
    recorded = json.loads(recorded_file.read_text())
    assert recorded['identity'] == old.league_identity()
    table = {(r['population'], r['institution'], r['seed']): r for r in recorded['rows']}
    out.mkdir(parents=True, exist_ok=False)
    start = perf_counter()
    rows, resumes, transactions, admission_counts = [], {}, 0, Counter()
    for mode, suffix in (('fixed', ''), ('sampled', le.POPULATION_SUFFIX)):
        for institution in INSTITUTIONS:
            condition = institution + suffix
            for seed in SEEDS:
                world = le.run_league_world(seed=seed, days=0, config=le.config_for_condition(condition))
                initial = le._snapshot_payload_unchecked(world)
                initial_hash = digest({k: initial[k] for k in ('players', 'player_minds', 'coaches')})
                le.advance_league_days(world, 84)
                previous = old.run_league_world(seed=seed, days=84, config=old.config_for_condition(condition))
                raw, old_raw = le._snapshot_payload_unchecked(world), old._snapshot_payload_unchecked(previous)
                equality = compare_states(old_raw, raw)
                summary, past_summary = le.summarize_league_world(world), old.summarize_league_world(previous)
                record = table[mode, institution, seed]
                for key in LEGACY_FIELDS:
                    replacement = 'legacy_four_slot_skill_gain' if key == 'academy_retained_skill_gain' else key
                    assert summary[replacement] == past_summary[key] == record[key], (mode, institution, seed, key)
                cohort = cohort_audit(raw, summary)
                finance, admission = ledger_audit(raw), admission_audit(raw)
                transactions += finance['transactions']
                admission_counts.update(admission['counts']); admission_counts['requests'] += admission['requests']
                rows.append({'population': mode, 'institution': institution, 'seed': seed,
                             'initial_agent_state_sha256': initial_hash, 'summary': summary,
                             'old_state_equality': equality, 'old_metrics_reproduced': True,
                             'cohort_audit': cohort, 'ledger_audit': finance, 'admission_audit': admission})
                if seed == 100 and institution in ('open_no_solidarity', 'open_solidarity'):
                    partial = le.run_league_world(seed=seed, days=42, config=le.config_for_condition(condition))
                    resumes[condition] = resume_check(world, partial, out / 'representative' / condition)
            print(mode, institution, '30 histories and cohorts verified', flush=True)
    write_json(out / 'runs.json', rows)
    effects = paired_effects(rows, SEEDS)
    descriptions = {mode + '|' + institution: never_transferred_description(
        [r for r in rows if r['population'] == mode and r['institution'] == institution])
        for mode in ('fixed', 'sampled') for institution in INSTITUTIONS}
    parent = le.run_league_world(seed=100, days=14, config=le.config_for_condition('open_solidarity_sampled_population'))
    old_parent = old.run_league_world(seed=100, days=14, config=old.config_for_condition('open_solidarity_sampled_population'))
    intervention = dict(kind='player_trust', player_id='p3', coach_id='c1', value=0.05, reason='T016 recorded sampled branch')
    branch = le.fork_league_world(parent, **intervention)
    old_branch = old.fork_league_world(old_parent, **intervention)
    le.advance_league_days(branch, 70); old.advance_league_days(old_branch, 70)
    branch_equal = compare_states(old._snapshot_payload_unchecked(old_branch), le._snapshot_payload_unchecked(branch), branch=True)
    partial = le.fork_league_world(parent, **intervention); le.advance_league_days(partial, 28)
    branch_resume = resume_check(branch, partial, out / 'sampled_branch')
    cohort_audit(le._snapshot_payload_unchecked(branch), le.summarize_league_world(branch))
    def function_asts(path):
        tree = ast.parse(path.read_text())
        return {n.name: ast.dump(n, include_attributes=False) for n in tree.body if isinstance(n, ast.FunctionDef)}
    before = function_asts(reference_directory / 'soccer_sim/league_economy.py')
    after = function_asts(CODE_ROOT / 'soccer_sim/league_economy.py')
    changed = [name for name in before if before[name] != after.get(name)]
    assert set(changed) <= {'league_identity', 'summarize_league_world', 'save_league_artifacts'}, changed
    assert le.league_identity() == identity, 'Runtime changed during comparison'
    assert sha(recorded_file) == recorded_hash, 'Archived T015 result changed during analysis'
    result = {'task': 'T016', 'identity': identity, 'evaluation_version': le.LEAGUE_EVALUATION_VERSION,
              'old_identity': old.league_identity(), 'runtime': runtime(), 'seeds': list(SEEDS), 'days': 84,
              'runs': len(rows), 'full_state_equal_runs': sum(r['old_state_equality']['full_state_and_events_equal'] for r in rows),
              'old_metrics_reproduced_runs': sum(r['old_metrics_reproduced'] for r in rows),
              'independently_audited_transactions': transactions, 'admission_totals': dict(admission_counts),
              'paired_effects': effects, 'never_transferred_descriptions': descriptions,
              'resumes': resumes, 'branch_equality': branch_equal, 'branch_resume': branch_resume,
              'changed_runtime_functions': changed, 'new_runtime_functions': sorted(after.keys() - before.keys()),
              'elapsed_seconds': perf_counter() - start, 'max_rss_raw': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
              'recorded_result_sha256': sha(recorded_file),
              'analysis_sha256': {name: sha(CODE_ROOT / name) for name in ('experiments/t016.py', 'experiments/audit_t016.py', 'experiments/audit_t014.py')},
              'limitations': ['exploratory reanalysis of the same 30 world seeds', 'uncalibrated initial ranges',
                              'never-transferred groups are descriptive, not a paired causal estimand']}
    write_json(out / 'summary.json', result)
    print(json.dumps({k: result[k] for k in ('runs', 'full_state_equal_runs', 'independently_audited_transactions', 'elapsed_seconds')}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--data-root', type=Path, default=Path.cwd(), help='workspace containing archived artifacts')
    args = parser.parse_args()
    run(args.data_root.resolve(), args.out)
