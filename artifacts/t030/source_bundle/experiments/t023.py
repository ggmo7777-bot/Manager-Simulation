"""T023: season boundary is a pure time unit (history identical to default), hooks fire at season ends, persistence round-trips.

Protocol: docs/experiments/T023-PROTOCOL.md. Seed 100; 14 worlds (7 conditions x fixed/sampled).
"""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
from time import perf_counter

CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT))
from experiments import t019
from soccer_sim import league_economy as le
from soccer_sim.storage import digest, runtime, write_json

SEED, DAYS, LENGTHS = 100, 126, (21, 42, 84)
SAMPLED = 'open_solidarity' + le.POPULATION_SUFFIX


def conditions():
    return [c + s for c in le.T011_ALL_CONDITIONS for s in ('', le.POPULATION_SUFFIX)]


def season_config(name, length):
    return replace(le.config_for_condition('league_' + name), season_length_days=length)


def raw_without_season(world):
    raw = le._snapshot_payload_unchecked(world)
    raw['config'].pop('season_length_days', None)
    return raw


def check_default_unchanged(baseline_path):
    base = json.loads(Path(baseline_path).read_text())['digests_excluding_identity']
    for name in conditions():
        raw = le._snapshot_payload_unchecked(le.run_league_world(seed=SEED, days=DAYS, config=le.config_for_condition('league_' + name)))
        assert 'season_length_days' not in raw['config'], name
        raw.pop('identity')
        assert digest(raw) == base[name], name
    return {'worlds': len(base), 'equal_to_pre_implementation_digest': True}


def check_history_identical(name, length):
    default = le.run_league_world(seed=SEED, days=DAYS, config=le.config_for_condition('league_' + name))
    seasoned = le.run_league_world(seed=SEED, days=DAYS, config=season_config(name, length))
    assert seasoned.config.season_length_days == length and default.config.season_length_days == 0
    assert le._snapshot_payload_unchecked(seasoned)['config']['season_length_days'] == length
    assert raw_without_season(seasoned) == raw_without_season(default), (name, length)
    return len(seasoned.events)


def expected_progress(day, length):
    return {'season': (day - 1) // length + 1 if day else 1, 'day_in_season': (day - 1) % length + 1 if day else 0, 'seasons_completed': day // length}


def check_hooks_and_progress():
    length, cfg = 42, season_config(SAMPLED, 42)
    world = le.run_league_world(seed=SEED, days=0, config=cfg)
    for day in range(0, 131):
        world.day = day  # progress is derived from day only
        assert le.season_progress(world) == expected_progress(day, length), day
    out = {}
    plain = le.run_league_world(seed=SEED, days=126, config=cfg)
    for label, start, seasons, days_expected in (('from_0_three', 0, 3, [42, 84, 126]), ('from_10_two', 10, 2, [42, 84]),
                                                  ('from_boundary_one', 42, 1, [84]), ('zero', 0, 0, [])):
        w = le.run_league_world(seed=SEED, days=start, config=cfg)
        calls = []
        le.advance_league_seasons(w, seasons, lambda world, done: calls.append((world.day, done)))
        assert [d for d, _ in calls] == days_expected, (label, calls)
        assert [c for _, c in calls] == [d // length for d in days_expected], (label, calls)
        ref = le.run_league_world(seed=SEED, days=days_expected[-1] if days_expected else start, config=cfg)
        le._assert_same(w, ref)
        out[label] = calls
    w = le.run_league_world(seed=SEED, days=0, config=cfg)
    le.advance_league_seasons(w, 3)  # no hook
    le._assert_same(w, plain)
    return out


def check_persistence(directory):
    world = le.run_league_world(seed=SEED, days=DAYS, config=season_config(SAMPLED, 42))
    le.save_league_artifacts(world, directory)
    snap = Path(directory) / 'snapshot.json'
    a, b = le.load_league_snapshot(snap), le.load_league_snapshot_trusted(snap)
    for w in (a, b):
        le._assert_same(w, world)
        assert le.season_progress(w) == le.season_progress(world) == {'season': 3, 'day_in_season': 42, 'seasons_completed': 3}
    return {'replay_reader_equal': True, 'trusted_reader_equal': True, 'progress': le.season_progress(world)}


def check_invalid():
    rejected = {}
    for bad in (-1, True, 1.5, '42', None):
        try:
            replace(le.LeagueEconomyConfig(), season_length_days=bad)
        except ValueError:
            rejected[f'length_{bad!r}'] = True
        else:
            rejected[f'length_{bad!r}'] = False
    off = le.run_league_world(seed=SEED, days=3, config=le.config_for_condition('league_' + SAMPLED))
    on = le.run_league_world(seed=SEED, days=3, config=season_config(SAMPLED, 42))
    before = digest(le._snapshot_payload_unchecked(on))
    cases = {'progress_without_length': (le.season_progress, off), 'advance_without_length': (le.advance_league_seasons, off, 1)}
    for label, bad in (('seasons_negative', -1), ('seasons_bool', True), ('seasons_float', 1.0)):
        cases[label] = (le.advance_league_seasons, on, bad)
    cases['hook_not_callable'] = (le.advance_league_seasons, on, 1, 'x')
    for label, (fn, *args) in cases.items():
        try:
            fn(*args)
        except ValueError:
            rejected[label] = True
        else:
            rejected[label] = False
    assert digest(le._snapshot_payload_unchecked(on)) == before and off.day == 3
    return rejected


def run(out, root):
    out.mkdir(parents=True, exist_ok=False)
    start = perf_counter()
    assert le.LEAGUE_SAVE_VERSION == 17
    default = check_default_unchanged(root / 'artifacts/t023/baseline/baseline.json')
    print('default digests', default, flush=True)
    regression = t019.check_regression(root)
    print('default vs T018', regression, flush=True)
    events = {}
    for name in conditions():
        for length in LENGTHS:
            events[f'{name}@{length}'] = check_history_identical(name, length)
        print('history identical', name, flush=True)
    hooks = check_hooks_and_progress()
    print('hooks', hooks, flush=True)
    with tempfile.TemporaryDirectory() as tmp:
        persistence = check_persistence(Path(tmp) / 'p')
    print('persistence', persistence, flush=True)
    invalid = check_invalid()
    print('invalid', invalid, flush=True)
    assert all(invalid.values()), invalid
    result = {'task': 'T023', 'identity': le.league_identity(), 'runtime': runtime(), 'seed': SEED, 'days': DAYS, 'lengths': list(LENGTHS),
              'default_unchanged': default, 'default_vs_t018': regression, 'history_identical_cells': len(events), 'events_per_cell': events,
              'hooks': hooks, 'persistence': persistence, 'invalid_rejected': invalid, 'elapsed_seconds': perf_counter() - start}
    write_json(out / 'summary.json', result)
    print(json.dumps({'cells': len(events), 'elapsed_seconds': result['elapsed_seconds']}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    run(parser.parse_args().out.resolve(), CODE_ROOT)
