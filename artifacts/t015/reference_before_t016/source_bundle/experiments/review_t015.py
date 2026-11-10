"""Read-only status review. Keeps simulation and existing results unchanged."""
import argparse
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'experiments')]
from soccer_sim import league_economy as le
from t015 import frozen_t014, METRICS, INSTITUTIONS
from audit_t014 import ledger_audit, admission_audit

parser = argparse.ArgumentParser()
parser.add_argument('--out', required=True, type=Path)
OUT = parser.parse_args().out
OUT.mkdir(parents=True, exist_ok=False)
start = time.perf_counter()
identity = le.league_identity()
old = frozen_t014()
recorded_path = ROOT / 'artifacts/t015/run1/comparison.json'
recorded = json.loads(recorded_path.read_text())
assert recorded['identity'] == identity
expected = {(r['population'], r['institution'], r['seed']): r for r in recorded['rows']}

def source_digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def normalized(world, module):
    payload = module._snapshot_payload_unchecked(world)
    payload.pop('identity')
    payload['config'].pop('population_mode', None)
    return payload

def payload_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

equivalence = []
for condition in le.T011_ALL_CONDITIONS:
    for seed in range(100, 105):
        past = old.run_league_world(seed=seed, days=84, config=old.config_for_condition(condition))
        now = le.run_league_world(seed=seed, days=84, config=le.config_for_condition(condition))
        a, b = normalized(past, old), normalized(now, le)
        assert payload_digest(a) == payload_digest(b), (condition, seed)
        equivalence.append({'condition': condition, 'seed': seed, 'full_state_and_events_equal': True})
    print('fixed equivalence:', condition, flush=True)

rows = []
transactions = 0
admissions = Counter()
for mode, suffix in (('fixed', ''), ('sampled', le.POPULATION_SUFFIX)):
    for institution in INSTITUTIONS:
        for seed in range(100, 130):
            world = le.run_league_world(seed=seed, days=84, config=le.config_for_condition(institution + suffix))
            summary = le.summarize_league_world(world)
            original = expected[mode, institution, seed]
            assert {k: summary[k] for k in METRICS} == {k: original[k] for k in METRICS}, (mode, institution, seed)
            raw = le._snapshot_payload_unchecked(world)
            transactions += ledger_audit(raw)['transactions']
            admission = admission_audit(raw)
            admissions.update(admission['counts'])
            admissions['requests'] += admission['requests']
            selected = ('p1', 'p2', 'p7', 'p8')
            initial_academy = tuple(p for c in le.ACADEMY_CLUB_IDS for p in le.INITIAL_CLUB_ROSTERS[c])
            movers = [p for p in selected if world.players[p].transfers_count > 0]
            retained = [p for p in initial_academy if world.players[p].transfers_count == 0]
            all_initial_mean = statistics.fmean(world.players[p].composite_skill() - world.initial_players[p].composite_skill() for p in initial_academy)
            rows.append({'population': mode, 'institution': institution, 'seed': seed,
                         'recorded_metrics_reproduced': True, 'reported_retained_slots': list(selected),
                         'selected_slots_that_transferred': movers, 'never_transferred_initial_academy': retained,
                         'reported_four_slot_gain': summary['academy_retained_skill_gain'],
                         'all_six_initial_academy_mean_gain': all_initial_mean})
        print('replayed metrics and audit:', mode, institution, flush=True)

issues = {}
for mode in ('fixed', 'sampled'):
    for institution in INSTITUTIONS:
        selected_rows = [r for r in rows if r['population'] == mode and r['institution'] == institution]
        issues[mode + '|' + institution] = {
            'runs_with_transferred_player_in_retained_metric': sum(bool(r['selected_slots_that_transferred']) for r in selected_rows),
            'transferred_player_run_occurrences': sum(len(r['selected_slots_that_transferred']) for r in selected_rows),
            'runs': len(selected_rows)}

assert le.league_identity() == identity
source_ast = ast.parse((ROOT / 'soccer_sim/league_economy.py').read_text())
old_ast = ast.parse((ROOT / 'artifacts/t014/source_bundle/soccer_sim/league_economy.py').read_text())
funcs = lambda tree: {n.name: ast.dump(n, include_attributes=False) for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
before, after = funcs(old_ast), funcs(source_ast)
changed_functions = sorted(k for k in before if before[k] != after.get(k))
result = {'identity': identity, 'source_unchanged_during_review': True,
          'recorded_result_sha256': source_digest(recorded_path), 'review_script_sha256': source_digest(Path(__file__)),
          'fixed_full_state_equivalence': equivalence, 'reproduced_metric_runs': len(rows),
          'transactions_independently_audited': transactions, 'admission_audit_totals': dict(admissions),
          'retained_metric_membership_checks': issues, 'row_diagnostics': rows,
          'changed_runtime_functions': changed_functions,
          'new_runtime_functions': sorted(after.keys() - before.keys()),
          'elapsed_seconds': time.perf_counter() - start}
(OUT / 'audit.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({k: result[k] for k in ('reproduced_metric_runs', 'transactions_independently_audited', 'retained_metric_membership_checks', 'changed_runtime_functions', 'elapsed_seconds')}, ensure_ascii=False, indent=2), flush=True)
