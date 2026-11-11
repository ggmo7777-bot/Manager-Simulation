"""T022: trusted direct snapshot reader vs the full-replay reader (equality, continuation, rejections, speed, characterization).

Protocol: docs/experiments/T022-PROTOCOL.md. Seed 100; 14 worlds (7 conditions x fixed/sampled) at 35 days; speed on open_solidarity.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
from statistics import median
import sys
import tempfile
from time import perf_counter

CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT))
from soccer_sim import league_economy as le
from soccer_sim.storage import digest, runtime, write_json

SEED = 100


def conditions():
    return [c + s for c in le.T011_ALL_CONDITIONS for s in ('', le.POPULATION_SUFFIX)]


def saved(world, directory):
    le.save_league_artifacts(world, directory)
    return Path(directory) / 'snapshot.json'


def check_equivalence(name, days, directory):
    """Trusted load == replay load (typed, exact); re-saving both gives byte-identical snapshot/events/summary."""
    world = le.run_league_world(seed=SEED, days=days, config=le.config_for_condition('league_' + name))
    snap = saved(world, Path(directory) / 'src')
    a, b = le.load_league_snapshot_trusted(snap), le.load_league_snapshot(snap)
    le._assert_same(a, b)
    le._assert_same(a, world)
    for label, w in (('trusted', a), ('replay', b)):
        saved(w, Path(directory) / label)
    for f in ('snapshot.json', 'events.jsonl', 'summary.json'):
        assert (Path(directory) / 'trusted' / f).read_bytes() == (Path(directory) / 'replay' / f).read_bytes() == (Path(directory) / 'src' / f).read_bytes(), (name, f)
    return {'condition': name, 'days': days, 'events': len(world.events), 'snapshot_sha256': digest(le.snapshot_payload(a))}


def check_continuation(directory):
    """Resume (14 -> 35) and a two-step recorded branch; the trusted-loaded world behaves exactly like the replay-loaded one."""
    name = 'league_open_solidarity' + le.POPULATION_SUFFIX
    base = le.run_league_world(seed=SEED, days=14, config=le.config_for_condition(name))
    snap = saved(base, Path(directory) / 'base')
    a, b = le.load_league_snapshot_trusted(snap), le.load_league_snapshot(snap)
    le.advance_league_days(a, 21), le.advance_league_days(b, 21)
    le._assert_same(a, b)
    first = le.fork_league_world(base, kind='player_trust', player_id='p3', coach_id='c1', value=0.05, reason='T022 trust edit')
    second = le.fork_league_world(first, kind='coach_skill_estimate', player_id='p3', coach_id='c1', value=0.5, reason='T022 skill edit')
    snap = saved(second, Path(directory) / 'branch')
    c, d = le.load_league_snapshot_trusted(snap), le.load_league_snapshot(snap)
    assert len(c.interventions) == 2 and c.interventions == second.interventions
    third = [le.fork_league_world(w, kind='player_trust', player_id='p1', coach_id='c2', value=0.4, reason='T022 third edit') for w in (c, d)]
    le._assert_same(third[0], third[1])
    assert len(third[0].interventions) == 3
    for w in third:
        le.advance_league_days(w, 7)
    le._assert_same(*third)
    return {'resume_to_35_equal': True, 'interventions_preserved': 2, 'multi_step_branch_equal': True, 'interventions_after_third': 3}


def _mutations(raw):
    """Edits that keep the snapshot structurally plausible. Each takes the parsed dict and returns the edited copy."""
    def edit(f):
        def run():
            copy = deepcopy(raw)
            f(copy)
            return copy
        return run
    first = lambda r: next(iter(r['players']))
    return {
        'value_change': edit(lambda r: r['players'][first(r)].__setitem__('fatigue', r['players'][first(r)]['fatigue'] + 0.5)),
        'type_change_float_to_int': edit(lambda r: r['players'][first(r)].__setitem__('cap', 1)),
        'extra_key': edit(lambda r: r['players'][first(r)].__setitem__('extra', 1)),
        'cash_change': edit(lambda r: r['club_accounts']['club_a'].__setitem__('cash_units', r['club_accounts']['club_a']['cash_units'] + 1)),
        'drop_last_event': edit(lambda r: r['events'].pop()),
        'day_change': edit(lambda r: r.__setitem__('day', r['day'] + 1)),
    }


def tamper(directory, world):
    """Part A: manifest left as written. Part B (characterization): manifest hash recomputed after the edit.

    Returns (a_rejected, b_table) where b_table maps edit -> {replay: rejected?, trusted: rejected?}.
    """
    snap = saved(world, Path(directory) / 'orig')
    raw = json.loads(snap.read_text())
    manifest = json.loads((snap.parent / 'manifest.json').read_text())

    def attempt(reader, payload, man, label):
        d = Path(directory) / label
        d.mkdir(parents=True)
        write_json(d / 'snapshot.json', payload)
        write_json(d / 'manifest.json', man)
        try:
            reader(d / 'snapshot.json')
        except (ValueError, KeyError, TypeError, OverflowError):
            return True
        return False

    part_a, part_b = {}, {}
    for k, make in _mutations(raw).items():
        part_a[k] = attempt(le.load_league_snapshot_trusted, make(), manifest, 'a_' + k)
        forged = make()
        man = {**manifest, 'snapshot_sha256': digest(forged)}
        part_b[k] = {'replay': attempt(le.load_league_snapshot, forged, man, 'br_' + k),
                     'trusted': attempt(le.load_league_snapshot_trusted, forged, man, 'bt_' + k)}
    # Part A structural cases
    cases = {}
    d = Path(directory) / 'no_manifest'
    d.mkdir()
    write_json(d / 'snapshot.json', raw)
    cases['manifest_missing'] = _rejects(le.load_league_snapshot_trusted, d / 'snapshot.json')
    for label, man in (('manifest_hash_mismatch', {**manifest, 'snapshot_sha256': '0' * 64}),
                       ('manifest_identity_mismatch', {**manifest, 'identity': {**manifest['identity'], 'source_sha256': '0' * 64}})):
        d = Path(directory) / label
        d.mkdir()
        write_json(d / 'snapshot.json', raw)
        write_json(d / 'manifest.json', man)
        cases[label] = _rejects(le.load_league_snapshot_trusted, d / 'snapshot.json')
    old = deepcopy(raw)
    old['identity']['save'] = 16
    cases['old_save16_identity'] = attempt(le.load_league_snapshot_trusted, old, {**manifest, 'snapshot_sha256': digest(old)}, 'old16')
    d = Path(directory) / 'dupkey'
    d.mkdir()
    text = snap.read_text().replace('"day":', '"day": 1, "day":', 1)
    (d / 'snapshot.json').write_text(text)
    write_json(d / 'manifest.json', manifest)
    cases['duplicate_json_key'] = _rejects(le.load_league_snapshot_trusted, d / 'snapshot.json')
    return {**part_a, **cases}, part_b


def _rejects(reader, path):
    try:
        reader(path)
    except (ValueError, KeyError, TypeError, OSError):
        return True
    return False


def speed(days_list, repeats=3):
    rows = []
    for cond in ('open_solidarity', 'open_solidarity' + le.POPULATION_SUFFIX):
        for days in days_list:
            world = le.run_league_world(seed=SEED, days=days, config=le.config_for_condition('league_' + cond))
            with tempfile.TemporaryDirectory() as d:
                snap = saved(world, d)
                replay, trusted = [], []
                for _ in range(repeats):  # interleaved so load/background noise hits both readers
                    t = perf_counter(); le.load_league_snapshot(snap); replay.append(perf_counter() - t)
                    t = perf_counter(); le.load_league_snapshot_trusted(snap); trusted.append(perf_counter() - t)
            rows.append({'condition': cond, 'days': days, 'replay_s': [round(x, 3) for x in replay], 'trusted_s': [round(x, 3) for x in trusted],
                         'replay_median': round(median(replay), 3), 'trusted_median': round(median(trusted), 3),
                         'speedup': round(median(replay) / median(trusted), 2)})
            print(rows[-1], flush=True)
    return rows


def run(out):
    out.mkdir(parents=True, exist_ok=False)
    start = perf_counter()
    assert le.LEAGUE_SAVE_VERSION == 17
    with tempfile.TemporaryDirectory() as tmp:
        equivalence = []
        for i, name in enumerate(conditions()):
            equivalence.append(check_equivalence(name, 35, Path(tmp) / f'eq{i}'))
            print('equivalence', equivalence[-1]['condition'], flush=True)
        continuation = check_continuation(Path(tmp) / 'cont')
        print('continuation', continuation, flush=True)
        world = le.run_league_world(seed=SEED, days=14, config=le.config_for_condition('league_open_solidarity' + le.POPULATION_SUFFIX))
        rejected, characterization = tamper(Path(tmp) / 'tamper', world)
    print('rejections', rejected, flush=True)
    assert all(rejected.values()), rejected
    rows = speed((35, 84, 1000))
    result = {'task': 'T022', 'identity': le.league_identity(), 'runtime': runtime(), 'seed': SEED, 'equivalence_worlds': len(equivalence),
              'equivalence': equivalence, 'continuation': continuation, 'rejections': rejected, 'forged_manifest_characterization': characterization,
              'speed': rows, 'targets': {'1000': 3.0, '84': 2.0}, 'elapsed_seconds': perf_counter() - start}
    result['speed_target_met'] = all(r['speedup'] >= {1000: 3.0, 84: 2.0}[r['days']] for r in rows if r['days'] in (84, 1000))
    write_json(out / 'summary.json', result)
    print(json.dumps({k: result[k] for k in ('equivalence_worlds', 'speed_target_met', 'elapsed_seconds')}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    run(parser.parse_args().out.resolve())
