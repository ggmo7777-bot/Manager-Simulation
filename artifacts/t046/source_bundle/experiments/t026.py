"""T026: age changes true skill once per season end through a recorded event (uncalibrated hypothesis curve); defaults and replay are intact.

Protocol: docs/experiments/T026-PROTOCOL.md. Seed 100; 14 worlds x L in {21,42,84} x start age in {18,29} = 84 cells, 126 days.
"""
import argparse
import ast
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
from time import perf_counter

CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT))
from experiments import t019, t023, t025
from soccer_sim import league_economy as le
from soccer_sim.storage import runtime, write_json

SEED, DAYS, LENGTHS, STARTS, CURVE = t023.SEED, t023.DAYS, t023.LENGTHS, (18, 29), 'hypothesis_v1'
# Independent literal copy of the pre-registered curve (reached age -> per-season delta), not imported from the implementation.
EXPECTED_DELTA = {**{a: 0.02 for a in range(18, 25)}, **{a: 0.0 for a in range(25, 30)}, **{a: -0.01 * (a - 29) for a in range(30, 41)}}
# Functions allowed to read the true age state (everything else, notably decision/observation code, must not).
ALLOWED_READERS = {'__post_init__', 'to_dict', 'has_save_fields', '_build_initial_world', '_age_players', '_validate_league_invariants',
                   '_snapshot_payload_unchecked', 'load_league_snapshot_trusted', '_aging_delta', '_aged_traits',
                   '_generational_turnover', '_generation_fold'}  # last two: T030 (the only retirement/intake code; reads ages, never a decision)
POLICY_MODULES = ('policy.py', 'observation.py', 'league_observation.py', 'advice.py', 'standings.py')
TRAITS = ('pace', 'passing', 'shooting')


def curve_config(name, length, start):
    return replace(t023.season_config(name, length), aging_start_age=start, aging_curve=CURVE)


def expect_after(before, delta, cap):
    return [round(min(cap, max(0.15, v + delta)), 6) for v in before]


def composite(world):
    return sum(p.composite_skill() for p in world.players.values()) / len(world.players)


def stepped(config, days, length):
    """Run season by season; return the world and mean composite skill at every season end (after that day's processing)."""
    world = le._build_initial_world(seed=SEED, config=config)
    trajectory = {0: composite(world)}
    while world.day < days:
        le.advance_league_days(world, min(length, days - world.day))
        trajectory[world.day] = composite(world)
    le._validate_league_invariants(world)
    return world, trajectory


def check_default_unchanged(root):
    base = t023.check_default_unchanged(root / 'artifacts/t023/baseline/baseline.json')
    for name in t023.conditions():
        raw = le._snapshot_payload_unchecked(le.run_league_world(seed=SEED, days=DAYS, config=le.config_for_condition('league_' + name)))
        assert 'aging_curve' not in raw['config'] and not any(e['kind'] == 'season_skill_aged' for e in raw['events'])
    with tempfile.TemporaryDirectory() as tmp:  # curve "none" with aging on is exactly T025 (equal to the no-aging world after stripping)
        for name in t023.conditions():
            cell = t025.check_cell(name, 42, Path(tmp) / name)
            raw = le._snapshot_payload_unchecked(le.run_league_world(seed=SEED, days=DAYS, config=t025.aged_config(name, 42)))
            assert 'aging_curve' not in raw['config'] and not any(e['kind'] == 'season_skill_aged' for e in raw['events']) and cell['season_aged_events'] == 3
    return {**base, 'aging_on_curve_none_matches_t025': 14}


def check_hand_case():
    """Hand-written before implementation."""
    world = le.run_league_world(seed=SEED, days=126, config=curve_config(t023.conditions()[0], 42, 29))
    got = [(e['day'], e['data']['season'], e['data']['delta']) for e in world.events if e['kind'] == 'season_skill_aged']
    assert got == [(42, 1, -0.01), (84, 2, -0.02), (126, 3, -0.03)], got
    world = le.run_league_world(seed=SEED, days=126, config=curve_config(t023.conditions()[0], 21, 18))
    got = [(e['data']['season'], e['data']['delta']) for e in world.events if e['kind'] == 'season_skill_aged']
    assert got == [(k, 0.02) for k in range(1, 7)], got
    return {'S29_L42': [-0.01, -0.02, -0.03], 'S18_L21': [0.02] * 6}


def check_isolation():
    """(b) _age_players on a mid-run world changes only traits, player_ages and two new events."""
    world = le.run_league_world(seed=SEED, days=41, config=curve_config('open_solidarity', 42, 29))
    before_raw = deepcopy(le._snapshot_payload_unchecked(world))
    world.day = 42
    le._age_players(world, 1)
    after_raw = le._snapshot_payload_unchecked(world)
    assert len(after_raw['events']) == len(before_raw['events']) + 2
    changed = {k for k in before_raw if k not in ('events', 'players', 'player_ages', 'day') and before_raw[k] != after_raw[k]}
    assert not changed, changed
    for pid, p in before_raw['players'].items():
        q = after_raw['players'][pid]
        assert {k: v for k, v in p.items() if k != 'traits'} == {k: v for k, v in q.items() if k != 'traits'}, pid
        assert p['traits'] != q['traits']
    return {'events_added': 2, 'only_traits_changed': True}


def check_policy_blind():
    tree = ast.parse((CODE_ROOT / 'soccer_sim/league_economy.py').read_text())
    readers = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for sub in ast.walk(node):
                if (isinstance(sub, ast.Attribute) and sub.attr in ('player_ages', 'aging_curve')) or \
                        (isinstance(sub, ast.Name) and sub.id in ('_aging_delta', '_aged_traits')):
                    readers.add(node.name)
    # Nested defs are counted once under their own name and again under their parent; both must be allowed.
    assert readers <= ALLOWED_READERS, sorted(readers - ALLOWED_READERS)
    assert not any(name.startswith('decide_') for name in readers)
    for module in POLICY_MODULES:
        text = (CODE_ROOT / 'soccer_sim' / module).read_text()
        assert 'player_ages' not in text and 'aging_curve' not in text, module
    return {'readers': sorted(readers)}


def check_cell(name, length, start, directory, days=DAYS):
    world, trajectory = stepped(curve_config(name, length, start), days, length)
    control, control_trajectory = stepped(t023.season_config(name, length), days, length)
    n = days // length
    skill = [e for e in world.events if e['kind'] == 'season_skill_aged']
    assert len(skill) == n  # condition 2
    first_end = length
    assert [e for e in world.events if e['day'] < first_end] == [e for e in control.events if e['day'] < first_end]  # condition 3a
    clamped = total = 0
    seasons = []
    for k, e in enumerate(skill, start=1):
        delta = EXPECTED_DELTA[start + k]
        assert e['data']['delta'] == delta and e['data']['season'] == k and e['day'] == k * length
        assert set(e['data']['traits']) == set(world.players)
        gained = []
        for pid, row in e['data']['traits'].items():
            assert row['after'] == expect_after(row['before'], delta, world.players[pid].cap), (pid, k)
            total += 3
            clamped += sum(a != round(b + delta, 6) for a, b in zip(row['after'], row['before']))
            gained.append(sum(row['after']) / 3 - sum(row['before']) / 3)
        seasons.append({'season': k, 'age': start + k, 'delta': delta, 'mean_composite_change': sum(gained) / len(gained)})
    transfers = lambda w: sum(e['kind'] == 'transfer_completed' for e in w.events)
    if directory is not None:
        le.save_league_artifacts(world, directory)  # condition 5
        for reader in (le.load_league_snapshot, le.load_league_snapshot_trusted):
            le._assert_same(reader(Path(directory) / 'snapshot.json'), world, 'world')
    final, ctrl = composite(world), composite(control)
    nominal = sum(EXPECTED_DELTA[start + k] for k in range(1, n + 1))
    return {'final_composite': final, 'control_composite': ctrl, 'gap': final - ctrl, 'nominal_cumulative_delta': nominal,
            'gap_over_nominal': (final - ctrl) / nominal if nominal else None, 'clamped_traits': clamped, 'trait_updates': total,
            'seasons': seasons, 'trajectory': trajectory, 'control_trajectory': control_trajectory,
            'transfers': transfers(world), 'control_transfers': transfers(control), 'events': len(world.events), 'control_events': len(control.events)}


def check_stepped_equals_oneshot():
    cfg = curve_config('open_solidarity', 42, 29)
    world, _ = stepped(cfg, DAYS, 42)
    le._assert_same(world, le.run_league_world(seed=SEED, days=DAYS, config=cfg), 'world')
    return True


def check_branch_round_trip(directory):
    parent = le.run_league_world(seed=SEED, days=30, config=curve_config(t023.conditions()[0], 42, 29))
    child = le.fork_league_world(parent, kind='player_trust', player_id='p3', coach_id='c1', value=0.05, reason='t026')
    le.advance_league_days(child, 60)
    assert child.day == 90 and len([e for e in child.events if e['kind'] == 'season_skill_aged']) == 2
    le.save_league_artifacts(child, directory)
    le._assert_same(le.load_league_snapshot(Path(directory) / 'snapshot.json'), child, 'world')
    return {'interventions': len(child.interventions)}


def check_tamper(world, directory):
    le.save_league_artifacts(world, directory)
    snap = Path(directory) / 'snapshot.json'
    original = snap.read_text()
    first = lambda raw: next(e for e in raw['events'] if e['kind'] == 'season_skill_aged')
    cases = {'event_deleted': lambda r: r['events'].remove(first(r)),
             'delta_changed': lambda r: first(r)['data'].__setitem__('delta', -0.5),
             'after_changed': lambda r: first(r)['data']['traits']['p1']['after'].__setitem__(0, 0.9),
             'snapshot_player_traits_changed': lambda r: r['players']['p1']['traits'].__setitem__('pace', 0.9)}
    result = {}
    for label, fn in cases.items():
        for reader in (le.load_league_snapshot, le.load_league_snapshot_trusted):
            raw = json.loads(original)
            fn(raw)
            snap.write_text(json.dumps(raw))
            try:
                reader(snap)
                result[f'{label}/{reader.__name__}'] = False
            except ValueError:
                result[f'{label}/{reader.__name__}'] = True
            snap.write_text(original)
    return result


def check_invalid():
    base = replace(t023.season_config(t023.conditions()[0], 42), aging_start_age=18)
    ok = {}
    for label, kwargs in {'unknown': dict(aging_curve='bogus'), 'int': dict(aging_curve=1), 'empty': dict(aging_curve=''),
                          'without_start_age': dict(aging_curve=CURVE, aging_start_age=0)}.items():
        try:
            replace(base, **kwargs)
        except ValueError:
            ok[label] = True
        else:
            ok[label] = False
    return ok


def summarize(cells):
    out = {}
    for start in STARTS:
        group = [v for k, v in cells.items() if k.endswith(f'@S{start}')]
        gaps = [v['gap'] for v in group]
        out[f'S{start}'] = {'cells': len(group), 'gap_positive': sum(g > 0 for g in gaps), 'gap_negative': sum(g < 0 for g in gaps),
                            'mean_gap': sum(gaps) / len(gaps), 'clamped_share': sum(v['clamped_traits'] for v in group) / sum(v['trait_updates'] for v in group),
                            'mean_gap_over_nominal': sum(v['gap_over_nominal'] for v in group) / len(group),
                            'mean_transfer_difference': sum(v['transfers'] - v['control_transfers'] for v in group) / len(group)}
    p = {'P1': out['S18']['gap_positive'] >= 36, 'P2': out['S29']['gap_negative'] >= 36,
         'P3': out['S18']['clamped_share'] <= 0.2 and out['S29']['clamped_share'] <= 0.2}
    return out, p


def run(out, root):
    out.mkdir(parents=True, exist_ok=False)
    start = perf_counter()
    default = check_default_unchanged(root)
    regression = t019.check_regression(root)
    print('default', default, regression, flush=True)
    hand, invalid, isolation, blind = check_hand_case(), check_invalid(), check_isolation(), check_policy_blind()
    assert all(invalid.values()), invalid
    print('hand', hand, 'isolation', isolation, 'readers', blind['readers'], 'stepped_eq_oneshot', check_stepped_equals_oneshot(), flush=True)
    with tempfile.TemporaryDirectory() as tmp:
        branch = check_branch_round_trip(Path(tmp) / 'b')
        world = le.run_league_world(seed=SEED, days=DAYS, config=curve_config(t023.conditions()[0], 42, 29))
        tamper = check_tamper(world, Path(tmp) / 't')
    print('branch', branch, 'tamper', tamper, flush=True)
    cells = {}
    for name in t023.conditions():
        for length in LENGTHS:
            for s in STARTS:
                with tempfile.TemporaryDirectory() as tmp:
                    cells[f'{name}@{length}@S{s}'] = check_cell(name, length, s, Path(tmp) / 'p')
        print('cells done', name, flush=True)
    assert len(cells) == 84
    summary, predictions = summarize(cells)
    result = {'task': 'T026', 'identity': le.league_identity(), 'runtime': runtime(), 'seed': SEED, 'days': DAYS, 'lengths': list(LENGTHS),
              'start_ages': list(STARTS), 'curve': CURVE, 'expected_delta': {str(k): v for k, v in EXPECTED_DELTA.items()},
              'default_unchanged': default, 'default_vs_t018': regression, 'hand_case': hand, 'isolation': isolation, 'policy_blind': blind,
              'branch': branch, 'tamper_rejected': tamper, 'invalid_rejected': invalid, 'cells': cells, 'summary': summary,
              'predictions': predictions, 'elapsed_seconds': perf_counter() - start}
    write_json(out / 'summary.json', result)
    print(json.dumps({'cells': len(cells), 'summary': summary, 'predictions': predictions, 'elapsed_seconds': result['elapsed_seconds']}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    run(parser.parse_args().out.resolve(), CODE_ROOT)
