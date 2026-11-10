"""T027: two latent personality traits as true player state only (default off, used by no decision, hidden from Policy).

Protocol: docs/experiments/T027-PROTOCOL.md. Seed 100/101/102; 14 worlds (7 conditions x fixed/sampled), 126 days.
"""
import argparse
import ast
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import statistics
import sys
import tempfile
from time import perf_counter

CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT))
from experiments import t019, t023
from soccer_sim import league_economy as le
from soccer_sim.observation import RNG_VERSION
from soccer_sim.storage import digest, runtime, write_json

DAYS, SEEDS, MODE = t023.DAYS, (100, 101, 102), 'latent_v1'
TRAITS = ('discipline', 'emotional_stability')
WORDS = ('personality', 'emotional_stability', 'discipline')
# Functions of league_economy.py allowed to touch the latent traits (everything else, notably decide_*/step functions, must not).
ALLOWED_READERS = {'__post_init__', 'to_dict', 'has_save_fields', '_build_initial_world', '_validate_league_invariants',
                   '_snapshot_payload_unchecked', 'load_league_snapshot_trusted', '_personality_traits',
                   '_player_advice_response'}  # last: T028's single decision reader (before T028 it was not present)
PLAYER_IDS = sorted(p for pids in le.INITIAL_CLUB_ROSTERS.values() for p in pids)


def cfg(name, **kw):
    return replace(le.config_for_condition('league_' + name), **kw)


def strip(world_or_raw):
    raw = le._snapshot_payload_unchecked(world_or_raw) if not isinstance(world_or_raw, dict) else json.loads(json.dumps(world_or_raw))
    raw['config'].pop('personality', None)
    raw.pop('player_personality', None)
    return raw


def independent_draw(seed, trait, pid):
    """Literal re-implementation of the pre-registered generator (not imported from the implementation)."""
    encoded = json.dumps((RNG_VERSION, seed, f'personality:{trait}', 0, pid, 0), separators=(',', ':')).encode('utf-8')
    u = (int.from_bytes(hashlib.sha256(encoded).digest()[:8], 'big') >> 11) / (1 << 53)
    return round(0.15 + (0.85 - 0.15) * u, 6)


def check_default_unchanged(root):
    base = t023.check_default_unchanged(root / 'artifacts/t023/baseline/baseline.json')
    for name in t023.conditions():
        raw = le._snapshot_payload_unchecked(le.run_league_world(seed=100, days=DAYS, config=cfg(name)))
        explicit = le._snapshot_payload_unchecked(le.run_league_world(seed=100, days=DAYS, config=cfg(name, personality='none')))
        assert 'personality' not in raw['config'] and 'player_personality' not in raw and raw == explicit, name
    return {**base, 'explicit_none_equals_default': 14}


def check_history(name, seed, **extra):
    off = le.run_league_world(seed=seed, days=DAYS, config=cfg(name, **extra))
    on = le.run_league_world(seed=seed, days=DAYS, config=cfg(name, personality=MODE, **extra))
    raw_on = le._snapshot_payload_unchecked(on)
    assert raw_on['config']['personality'] == MODE and set(raw_on['player_personality']) == set(on.players)
    assert strip(on) == strip(off), (name, seed)
    assert le.summarize_league_world(on) == le.summarize_league_world(off) and len(on.events) == len(off.events)
    return len(on.events)


def check_leak_dynamic(name):
    """Overwrite the traits with extremes right after construction; the history must not move."""
    results = {}
    for label, value in (('generated', None), ('all_low', 0.15), ('all_high', 0.85)):
        world = le._build_initial_world(seed=100, config=cfg(name, personality=MODE))
        if value is not None:
            world.player_personality = {pid: {t: value for t in TRAITS} for pid in world.players}
        le.advance_league_days(world, DAYS)
        results[label] = strip(world)
    off = strip(le.run_league_world(seed=100, days=DAYS, config=cfg(name)))
    assert results['generated'] == results['all_low'] == results['all_high'] == off, name
    return True


def personality_readers(source):
    readers = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for sub in ast.walk(node):
                if (isinstance(sub, ast.Attribute) and sub.attr in ('player_personality', 'personality')) or \
                        (isinstance(sub, ast.Name) and (sub.id.startswith('PERSONALITY_') or sub.id == '_personality_traits')) or \
                        (isinstance(sub, ast.keyword) and sub.arg in ('player_personality', 'personality')) or \
                        (isinstance(sub, ast.Constant) and isinstance(sub.value, str) and sub.value in WORDS + ('player_personality',)):
                    readers.add(node.name)
    return readers


def check_leak_static():
    readers = personality_readers((CODE_ROOT / 'soccer_sim/league_economy.py').read_text())
    assert readers <= ALLOWED_READERS, sorted(readers - ALLOWED_READERS)
    others = {}
    for path in sorted((CODE_ROOT / 'soccer_sim').glob('*.py')):
        if path.name != 'league_economy.py':
            text = path.read_text()
            assert not any(w in text for w in WORDS), path.name
            others[path.name] = True
    # Sensitivity: the same checker flags a decision function that reads a trait.
    bad = 'def decide_x(world, pid):\n    return world.player_personality[pid]["discipline"]\n'
    assert personality_readers(bad) == {'decide_x'}
    assert personality_readers('def decide_y(mind):\n    return mind.ambition\n') == set()
    return {'readers': sorted(readers), 'other_modules_checked': len(others)}


def pearson(xs, ys):
    return statistics.correlation(xs, ys)


def check_generator():
    rows = [(seed, pid, le._personality_traits(seed, [pid])[pid]) for seed in range(1000) for pid in PLAYER_IDS]
    for seed in SEEDS:  # (a) independent implementation, exact
        for pid, got in le._personality_traits(seed, PLAYER_IDS).items():
            assert got == {t: independent_draw(seed, t, pid) for t in TRAITS}, (seed, pid)
    values = {t: [r[2][t] for r in rows] for t in TRAITS}
    n = len(rows)
    ks_critical = 1.63 / n ** 0.5
    stats = {}
    for t, xs in values.items():
        srt = sorted(xs)
        ks = max(max((i + 1) / n - (x - 0.15) / 0.7, (x - 0.15) / 0.7 - i / n) for i, x in enumerate(srt))
        stats[t] = {'min': srt[0], 'max': srt[-1], 'mean': statistics.fmean(xs), 'sd': statistics.pstdev(xs), 'ks': ks}
        assert 0.15 <= srt[0] and srt[-1] <= 0.85 and abs(stats[t]['mean'] - 0.5) < 0.01 and abs(stats[t]['sd'] - 0.2021) < 0.006 and ks <= ks_critical, (t, stats[t])
    corr = {'between_traits': pearson(*values.values())}
    specs = {seed: le._sample_profiles(seed) for seed in range(1000)}
    for key in ('ambition', 'loyalty', 'autonomy'):
        existing = [specs[seed][pid][key] for seed, pid, _ in rows]
        for t in TRAITS:
            corr[f'{t}_vs_{key}'] = pearson(values[t], existing)
    assert all(abs(v) < 0.03 for v in corr.values()), corr
    academy_ids = {p for c in le.ACADEMY_CLUB_IDS for p in le.INITIAL_CLUB_ROSTERS[c]}
    club_gap = {t: statistics.fmean(v for v, r in zip(values[t], rows) if r[1] in academy_ids)
                - statistics.fmean(v for v, r in zip(values[t], rows) if r[1] not in academy_ids) for t in TRAITS}
    assert all(abs(g) < 0.02 for g in club_gap.values()), club_gap
    by_pid = {t: {pid: statistics.fmean(r[2][t] for r in rows if r[1] == pid) for pid in PLAYER_IDS} for t in TRAITS}
    assert all(abs(m - 0.5) < 0.03 for d in by_pid.values() for m in d.values()), by_pid
    a, b = le._personality_traits(100, PLAYER_IDS), le._personality_traits(101, PLAYER_IDS)
    assert all(a[pid][t] != b[pid][t] for pid in PLAYER_IDS for t in TRAITS)
    # (c) existing population draws are unaffected by enabling the traits
    same = 0
    for name in t023.conditions():
        if not name.endswith(le.POPULATION_SUFFIX):
            continue
        for seed in range(100):
            assert strip(le._build_initial_world(seed, cfg(name))) == strip(le._build_initial_world(seed, cfg(name, personality=MODE))), (name, seed)
            same += 1
    return {'values': n, 'stats': stats, 'correlations': corr, 'club_gap': club_gap, 'ks_critical': ks_critical,
            'pid_mean_range': [min(m for d in by_pid.values() for m in d.values()), max(m for d in by_pid.values() for m in d.values())],
            'build_comparisons': same, 'fixed_seed100': le._personality_traits(100, PLAYER_IDS)}


def check_persistence(name, directory):
    world = le.run_league_world(seed=100, days=DAYS, config=cfg(name, personality=MODE))
    le.save_league_artifacts(world, directory)
    for reader in (le.load_league_snapshot, le.load_league_snapshot_trusted):
        loaded = reader(Path(directory) / 'snapshot.json')
        le._assert_same(loaded, world, 'world')
        assert loaded.player_personality == world.player_personality
    default = le.run_league_world(seed=100, days=DAYS, config=cfg(name))
    other = Path(str(directory) + '_default')
    le.save_league_artifacts(default, other)
    assert 'player_personality' not in json.loads((other / 'snapshot.json').read_text())
    for reader in (le.load_league_snapshot, le.load_league_snapshot_trusted):
        le._assert_same(reader(other / 'snapshot.json'), default, 'world')


def check_branch_and_steps(directory):
    config = cfg(t023.conditions()[0], personality=MODE)
    parent = le.run_league_world(seed=100, days=30, config=config)
    child = le.fork_league_world(parent, kind='player_trust', player_id='p3', coach_id='c1', value=0.05, reason='t027')
    le.advance_league_days(child, 60)
    assert child.player_personality == parent.player_personality and child.day == 90
    le.save_league_artifacts(child, directory)
    le._assert_same(le.load_league_snapshot(Path(directory) / 'snapshot.json'), child, 'world')
    stepped = le._build_initial_world(seed=100, config=config)
    le.advance_league_days(stepped, 30)
    le.advance_league_days(stepped, 60)
    oneshot = le.run_league_world(seed=100, days=90, config=config)
    le._assert_same(stepped, oneshot, 'world')
    ok = LeagueConfigAccepts()
    return {'interventions': len(child.interventions), 'stepped_equals_oneshot': True, **ok}


def LeagueConfigAccepts():
    cls = le.LeagueEconomyConfig
    off, on = cfg(t023.conditions()[0]).to_dict(), cfg(t023.conditions()[0], personality=MODE).to_dict()
    assert cls.has_save_fields(off) and cls.has_save_fields(on) and 'personality' not in off and not cls.has_save_fields({**on, 'unknown': 1})
    return {'has_save_fields': True}


def check_tamper(directory):
    name = 'open_solidarity' + le.POPULATION_SUFFIX
    enabled = le.run_league_world(seed=100, days=DAYS, config=cfg(name, personality=MODE))
    default = le.run_league_world(seed=100, days=DAYS, config=cfg(name))
    cases = {
        'value_changed': (enabled, lambda r: r['player_personality']['p1'].__setitem__('discipline', 0.5)),
        'player_removed': (enabled, lambda r: r['player_personality'].pop('p1')),
        'trait_removed': (enabled, lambda r: r['player_personality']['p1'].pop('discipline')),
        'trait_added': (enabled, lambda r: r['player_personality']['p1'].__setitem__('ambition', 0.5)),
        'out_of_range': (enabled, lambda r: r['player_personality']['p1'].__setitem__('discipline', 0.9)),
        'added_while_none': (default, lambda r: r.__setitem__('player_personality', {p: {t: 0.5 for t in TRAITS} for p in default.players})),
        'removed_while_enabled': (enabled, lambda r: r.pop('player_personality'))}
    result = {}
    for label, (world, mutate) in cases.items():
        folder = Path(directory) / label
        le.save_league_artifacts(world, folder)
        snap, manifest = folder / 'snapshot.json', folder / 'manifest.json'
        original = json.loads(snap.read_text())
        raw = json.loads(snap.read_text())
        mutate(raw)
        snap.write_text(json.dumps(raw))
        for reader, rehash in ((le.load_league_snapshot, False), (le.load_league_snapshot_trusted, False), (le.load_league_snapshot_trusted, True)):
            if rehash:  # forge the manifest digest as well: only the invariant can stop this
                record = json.loads(manifest.read_text())
                record['snapshot_sha256'] = digest(raw)
                manifest.write_text(json.dumps(record))
            try:
                reader(snap)
                result[f'{label}/{reader.__name__}{"/manifest_forged" if rehash else ""}'] = False
            except ValueError:
                result[f'{label}/{reader.__name__}{"/manifest_forged" if rehash else ""}'] = True
        assert json.loads(snap.read_text()) != original
    return result


def check_invalid():
    ok = {}
    for label, value in {'unknown': 'bogus', 'int': 1, 'empty': ''}.items():
        try:
            cfg(t023.conditions()[0], personality=value)
        except ValueError:
            ok[label] = True
        else:
            ok[label] = False
    return ok


def run(out, root):
    out.mkdir(parents=True, exist_ok=False)
    start = perf_counter()
    default = check_default_unchanged(root)
    regression = t019.check_regression(root)
    print('default', default, regression, flush=True)
    static, invalid = check_leak_static(), check_invalid()
    assert all(invalid.values()), invalid
    generator = check_generator()
    print('static', static, 'generator ok', generator['stats'], generator['correlations'], flush=True)
    history = {f'{name}@{seed}': check_history(name, seed) for name in t023.conditions() for seed in SEEDS}
    combined = {name: check_history(name, 100, season_length_days=42, aging_start_age=29, aging_curve='hypothesis_v1') for name in t023.conditions()}
    print('history ok', len(history), len(combined), flush=True)
    dynamic = {name: check_leak_dynamic(name) for name in t023.conditions()}
    print('dynamic leak ok', len(dynamic), flush=True)
    with tempfile.TemporaryDirectory() as tmp:
        for name in t023.conditions():
            check_persistence(name, Path(tmp) / name)
        branch = check_branch_and_steps(Path(tmp) / 'branch')
        tamper = check_tamper(Path(tmp) / 'tamper')
    assert all(tamper.values()), tamper
    print('persistence ok 14, branch', branch, 'tamper', tamper, flush=True)
    result = {'task': 'T027', 'identity': le.league_identity(), 'runtime': runtime(), 'seeds': list(SEEDS), 'days': DAYS, 'mode': MODE,
              'default_unchanged': default, 'default_vs_t018': regression, 'leak_static': static, 'generator': generator,
              'history_identical_cells': len(history), 'history_events': history, 'history_identical_with_season_and_aging': combined,
              'leak_dynamic_worlds': len(dynamic), 'persistence_worlds': 14, 'branch': branch, 'tamper_rejected': tamper,
              'invalid_rejected': invalid, 'elapsed_seconds': perf_counter() - start}
    write_json(out / 'summary.json', result)
    print(json.dumps({'history_cells': len(history), 'tamper_cases': len(tamper), 'elapsed_seconds': result['elapsed_seconds']}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    run(parser.parse_args().out.resolve(), CODE_ROOT)
