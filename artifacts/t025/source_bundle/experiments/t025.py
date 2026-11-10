"""T025: season-end aging is a recorded event that changes only an age counter; defaults, replay and persistence are intact.

Protocol: docs/experiments/T025-PROTOCOL.md. Seed 100; 14 worlds x L in {21,42,84} = 42 cells, 126 days, start age 18.
"""
import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile
from time import perf_counter

CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT))
from experiments import t019, t023
from soccer_sim import league_economy as le
from soccer_sim.storage import runtime, write_json

SEED, DAYS, LENGTHS, START = t023.SEED, t023.DAYS, t023.LENGTHS, 18
ID = re.compile(r'^e\d+$')


def aged_config(name, length):
    return replace(t023.season_config(name, length), aging_start_age=START)


def strip_aging(raw):
    """Aged snapshot -> the snapshot a no-aging world would have: drop season_aged, renumber event ids, drop the two aging keys."""
    kept = [e for e in raw['events'] if e['kind'] != 'season_aged']
    mapping, n_kept = {}, 0
    for e in raw['events']:  # a season_aged id maps to the event before it: the next ledger tx cites events[-1] as its cause (amendment A1)
        n_kept += e['kind'] != 'season_aged'
        mapping[e['id']] = f'e{n_kept}'
    redirected = sum(c in {e['id'] for e in raw['events'] if e['kind'] == 'season_aged'} for e in kept for c in e['cause_refs'])

    def remap(v):
        if isinstance(v, str):
            return mapping[v] if ID.match(v) else v
        if isinstance(v, list):
            return [remap(x) for x in v]
        if isinstance(v, dict):
            return {k: remap(x) for k, x in v.items()}
        return v
    out = {k: remap(v) for k, v in raw.items() if k not in ('player_ages', 'events')}
    out['events'] = remap(kept)
    out['config'].pop('aging_start_age')
    return out, redirected


def check_default_unchanged(root):
    base = t023.check_default_unchanged(root / 'artifacts/t023/baseline/baseline.json')
    for name in t023.conditions():
        raw = le._snapshot_payload_unchecked(le.run_league_world(seed=SEED, days=DAYS, config=le.config_for_condition('league_' + name)))
        assert 'player_ages' not in raw and 'aging_start_age' not in raw['config'] and not any(e['kind'] == 'season_aged' for e in raw['events'])
    return base


def check_cell(name, length, directory):
    world = le.run_league_world(seed=SEED, days=DAYS, config=aged_config(name, length))
    control = le.run_league_world(seed=SEED, days=DAYS, config=t023.season_config(name, length))
    raw = le._snapshot_payload_unchecked(world)
    stripped, redirected = strip_aging(raw)
    assert stripped == le._snapshot_payload_unchecked(control), (name, length)  # condition 2
    n = DAYS // length
    aged = [e for e in world.events if e['kind'] == 'season_aged']  # condition 3
    assert len(aged) == n and [e['day'] for e in aged] == [length * k for k in range(1, n + 1)]
    for k, e in enumerate(aged, start=1):
        assert e['data'] == {'season': k, 'ages': {p: START + k for p in sorted(world.players)}} and e['cause_refs'] == [] and e['actor'] == 'league'
    assert raw['player_ages'] == {p: START + n for p in world.players}
    le.save_league_artifacts(world, directory)  # condition 4
    for reader in (le.load_league_snapshot, le.load_league_snapshot_trusted):
        le._assert_same(reader(Path(directory) / 'snapshot.json'), world, 'world')
    return {'season_aged_events': n, 'final_age': START + n, 'events': len(world.events), 'control_events': len(control.events),
            'cause_refs_to_season_aged': redirected}


def check_hand_case():
    """Hand-written before implementation: L=42 over 126 days -> ages 19, 20, 21 recorded on days 42, 84, 126."""
    world = le.run_league_world(seed=SEED, days=126, config=aged_config(t023.conditions()[0], 42))
    got = [(e['day'], e['data']['season'], set(e['data']['ages'].values())) for e in world.events if e['kind'] == 'season_aged']
    assert got == [(42, 1, {19}), (84, 2, {20}), (126, 3, {21})], got
    return [[d, k, sorted(ages)] for d, k, ages in got]


def check_branch_round_trip(directory):
    """A recorded intervention before a season end; the fork must replay and round-trip (full-replay reader)."""
    name = t023.conditions()[0]
    parent = le.run_league_world(seed=SEED, days=30, config=aged_config(name, 42))
    child = le.fork_league_world(parent, kind='player_trust', player_id='p3', coach_id='c1', value=0.05, reason='t025')
    le.advance_league_days(child, 60)
    assert child.day == 90 and len([e for e in child.events if e['kind'] == 'season_aged']) == 2 and set(child.player_ages.values()) == {20}
    le.save_league_artifacts(child, directory)
    le._assert_same(le.load_league_snapshot(Path(directory) / 'snapshot.json'), child, 'world')
    return {'interventions': len(child.interventions)}


def mutate(snapshot_path, fn):
    raw = json.loads(snapshot_path.read_text())
    fn(raw)
    snapshot_path.write_text(json.dumps(raw))


def check_tamper(world, directory):
    le.save_league_artifacts(world, directory)
    snap = Path(directory) / 'snapshot.json'
    original = snap.read_text()
    first_aged = lambda raw: next(e for e in raw['events'] if e['kind'] == 'season_aged')
    cases = {'age_value': lambda r: r['player_ages'].__setitem__('p1', 99),
             'age_key_missing': lambda r: r['player_ages'].pop('p1'),
             'event_deleted': lambda r: r['events'].remove(first_aged(r)),
             'event_ages_changed': lambda r: first_aged(r)['data']['ages'].__setitem__('p1', 99),
             'ages_removed': lambda r: r.pop('player_ages')}
    result = {}
    for label, fn in cases.items():
        for reader in (le.load_league_snapshot, le.load_league_snapshot_trusted):
            mutate(snap, fn)
            try:
                reader(snap)
            except ValueError:
                result[f'{label}/{reader.__name__}'] = True
            else:
                result[f'{label}/{reader.__name__}'] = False
            snap.write_text(original)
    return result


def tamper_with_forged_manifest(world, directory):
    """Characterisation only (not pass/fail): snapshot and manifest forged together under the trusted reader."""
    le.save_league_artifacts(world, directory)
    snap, manifest = Path(directory) / 'snapshot.json', Path(directory) / 'manifest.json'
    mutate(snap, lambda r: r['player_ages'].__setitem__('p1', 99))
    m = json.loads(manifest.read_text())
    m['snapshot_sha256'] = le.digest(json.loads(snap.read_text()))
    manifest.write_text(json.dumps(m))
    try:
        le.load_league_snapshot_trusted(snap)
    except ValueError:
        return 'rejected'
    return 'accepted'


def check_invalid():
    base = t023.season_config(t023.conditions()[0], 42)
    ok = {}
    for label, kwargs in {'negative': dict(aging_start_age=-1), 'bool': dict(aging_start_age=True), 'float': dict(aging_start_age=18.0),
                          'str': dict(aging_start_age='18'), 'without_season': dict(aging_start_age=18, season_length_days=0)}.items():
        try:
            replace(base, **kwargs)
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
    hand, invalid = check_hand_case(), check_invalid()
    assert all(invalid.values()), invalid
    with tempfile.TemporaryDirectory() as tmp:
        branch = check_branch_round_trip(Path(tmp) / 'b')
        world = le.run_league_world(seed=SEED, days=DAYS, config=aged_config(t023.conditions()[0], 42))
        tamper = check_tamper(world, Path(tmp) / 't')
        forged = tamper_with_forged_manifest(world, Path(tmp) / 'f')
    assert all(tamper.values()), tamper
    print('hand', hand, 'branch', branch, 'tamper', len(tamper), 'forged_manifest_trusted_reader', forged, 'invalid', invalid, flush=True)
    cells = {}
    for name in t023.conditions():
        for length in LENGTHS:
            with tempfile.TemporaryDirectory() as tmp:
                cells[f'{name}@{length}'] = check_cell(name, length, Path(tmp) / 'p')
        print('cells done', name, flush=True)
    assert len(cells) == 42
    result = {'task': 'T025', 'identity': le.league_identity(), 'runtime': runtime(), 'seed': SEED, 'days': DAYS, 'lengths': list(LENGTHS),
              'start_age': START, 'default_unchanged': default, 'default_vs_t018': regression, 'hand_case': hand, 'branch': branch,
              'tamper_rejected': tamper, 'forged_manifest_trusted_reader': forged, 'invalid_rejected': invalid, 'cells': cells,
              'elapsed_seconds': perf_counter() - start}
    write_json(out / 'summary.json', result)
    print(json.dumps({'cells': len(cells), 'elapsed_seconds': result['elapsed_seconds']}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    run(parser.parse_args().out.resolve(), CODE_ROOT)
