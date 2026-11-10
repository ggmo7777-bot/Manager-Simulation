"""T032: a practice-day observation also updates the coach's match-rating estimate (0.70 old + 0.30 observed), so a benched player who is seen can rise in selection (coach_practice_rating).

Protocol: docs/experiments/T032-PROTOCOL.md. Cells: 14 worlds x seeds {100,101,102} x start age {29,33}, T031's coach_exploration-on setting, L=21, 168 days,
coach_practice_rating off (control = the T031 cell) / on. Every practice-day update is re-derived independently; costs are reported against the control.
"""
import argparse
import ast
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
from time import perf_counter

CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT))
from experiments import t019, t023, t026, t027, t029, t030, t031
from soccer_sim import league_economy as le
from soccer_sim.storage import digest, runtime, write_json

L, DAYS, SEEDS, STARTS = 21, 168, (100, 101, 102), (29, 33)
MODES = {'off': 'none', 'on': 'blend_v1'}
mean, sign_test, touched = t030.mean, t031.sign_test, t030.touched
FORBIDDEN = t031.FORBIDDEN | {'cap', 'rates'}
BLOCK_ALLOWED = {'cfg', 'coach_practice_rating', 'c_ev', 'match_rating_estimate', 'round6', 'clamp', 'c_obs_skill', 'none'}
WORDS = {'coach_practice_rating', 'COACH_PRACTICE_RATING_MODES', 'blend_v1'}


def cfg(name, start, mode, **kw):
    return replace(t031.cfg(name, start, 'on'), coach_practice_rating=MODES[mode], **kw)


def ind_blend(old, observed):
    """Independent re-implementation of the pre-registered update (literal numbers)."""
    return round(min(0.98, max(0.10, 0.70 * old + 0.30 * observed)), 6)


# ---- gates 2, 5a ----
def check_invalid():
    ok = {}
    base = cfg(t023.conditions()[0], 29, 'off')
    for label, value in (('unknown', 'bogus'), ('int', 1), ('empty', '')):
        try:
            replace(base, coach_practice_rating=value)
            ok[label] = False
        except ValueError:
            ok[label] = True
    cls, on = le.LeagueEconomyConfig, cfg(t023.conditions()[0], 29, 'on').to_dict()
    ok['save_fields'] = cls.has_save_fields(on) and cls.has_save_fields({k: v for k, v in on.items() if k != 'coach_practice_rating'}) and not cls.has_save_fields({**on, 'unknown': 1})
    assert all(ok.values()), ok
    return ok


def check_static():
    t026.check_policy_blind()
    t027.check_leak_static()
    source = (CODE_ROOT / 'soccer_sim/league_economy.py').read_text()
    tree = ast.parse(source)
    funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    readers = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and touched(n) & WORDS}
    allowed = {'__post_init__', 'to_dict', 'has_save_fields', '_step_training_day'}
    assert readers <= allowed and not any(r.startswith('decide_') for r in readers), sorted(readers)
    blocks = [n for n in ast.walk(funcs['_step_training_day']) if isinstance(n, (ast.If, ast.IfExp)) and 'coach_practice_rating' in touched(n.test)]
    assert len(blocks) == 2, len(blocks)  # the update, and the optional key of the recorded event
    for block in blocks:
        assert touched(block) <= BLOCK_ALLOWED | {'coach_practice_rating'}, touched(block) - BLOCK_ALLOWED
    assert not any(touched(b) & FORBIDDEN for b in blocks)
    for module in t026.POLICY_MODULES + ('match_2d.py', 'selection_loop.py', 'transfer_loop.py'):
        assert not any(w in (CODE_ROOT / 'soccer_sim' / module).read_text() for w in WORDS), module
    bad = ast.parse('if cfg.coach_practice_rating != "none":\n    c_ev.match_rating_estimate = world.players[pid].composite_skill()\n').body[0]
    assert touched(bad) & FORBIDDEN >= {'players', 'composite_skill', 'world'} and not touched(bad) <= BLOCK_ALLOWED
    return {'practice_rating_readers': sorted(readers)}


# ---- gate 1 ----
def check_default_unchanged(root):
    base = t031.check_default_unchanged(root)
    for name in t023.conditions():
        explicit = le._snapshot_payload_unchecked(le.run_league_world(seed=100, days=2 * L, config=cfg(name, 29, 'off')))
        default = le._snapshot_payload_unchecked(le.run_league_world(seed=100, days=2 * L, config=t031.cfg(name, 29, 'on')))
        assert explicit == default and 'coach_practice_rating' not in explicit['config'], name
        assert not any('match_rating_estimate' in e['data'] for e in explicit['events'] if e['kind'] == 'coach_session_observation')
    return {**base, 'explicit_none_equals_default_with_exploration': 14}


# ---- gate 4: isolation on day 1, and the first diverging event ----
def snap_estimates(world):
    return {(c, pid): ev for c, coach in world.coaches.items() for pid, ev in coach.evaluations.items()}


def normalize(world):
    raw = json.loads(json.dumps(le._snapshot_payload_unchecked(world)))
    raw['config'].pop('coach_practice_rating', None)
    for e in raw['events']:
        if e['kind'] == 'coach_session_observation':
            e['data'].pop('match_rating_estimate', None)
    return raw


def check_isolation(name, seed, start):
    """Until the first practice-day observation the two worlds are identical; on that day only the observed (coach, player) match estimates differ."""
    off = le._build_initial_world(seed=seed, config=cfg(name, start, 'off'))
    on = le._build_initial_world(seed=seed, config=cfg(name, start, 'on'))
    seen, first_day = set(), None
    while not seen and on.day < DAYS:
        n = len(on.events)
        le.advance_league_days(off, 1)
        le.advance_league_days(on, 1)
        seen = {(e['data']['coach_id'], e['data']['player_id']) for e in on.events[n:] if e['kind'] == 'coach_session_observation'}
        assert normalize(off)['events'] == normalize(on)['events'], (name, seed, on.day)
    assert seen, (name, seed)
    first_day = on.day
    a, b = snap_estimates(off), snap_estimates(on)
    assert a.keys() == b.keys()
    changed = 0
    for key in a:
        for field in ('skill_estimate', 'fatigue_estimate', 'last_seen_day', 'method_residuals'):
            assert getattr(a[key], field) == getattr(b[key], field), (key, field)
        if a[key].match_rating_estimate != b[key].match_rating_estimate:
            assert key in seen, key
            changed += 1
    return {'first_practice_observation_day': first_day, 'observed_pairs_that_day': len(seen), 'pairs_with_changed_match_estimate': changed}


def first_divergence(off_world, on_world):
    a, b = normalize(off_world)['events'], normalize(on_world)['events']
    k = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), None)
    return None if k is None else {'index': k, 'day': b[k]['day'], 'kind': b[k]['kind']}


# ---- one verified, stepped cell ----
def run_cell(name, seed, start, mode, days, validate_every=2):
    config = cfg(name, start, mode)
    world = le._build_initial_world(seed=seed, config=config)
    newcomers, min_roster, skill_rows, checked, previous, swaps, lineups = {}, 3, [], 0, None, 0, 0
    while world.day < days:
        n_events = len(world.events)
        running = {k: ev.match_rating_estimate for k, ev in snap_estimates(world).items()}
        le.advance_league_days(world, 1)
        min_roster = min(min_roster, min(len(r) for r in world.club_rosters.values()))
        new = world.events[n_events:]
        for e in new:  # gate 3: every practice-day update re-derived from the day's earlier value and the recorded observation
            if e['kind'] != 'coach_session_observation':
                continue
            d, key = e['data'], (e['data']['coach_id'], e['data']['player_id'])
            if mode == 'on':
                want = ind_blend(running[key], d['obs_skill'])
                assert d['match_rating_estimate'] == want, (name, seed, world.day, key, d, want)
                running[key] = want
                checked += 1
            else:
                assert 'match_rating_estimate' not in d
        for e in new:
            if e['kind'] == 'season_youth_joined':
                newcomers[e['data']['player_id']] = {'club': e['data']['club_id'], 'day': world.day}
        if any(e['kind'] == 'league_match_completed' for e in new):
            starters = [pid for e in new if e['kind'] == 'lineup_decision' for pid in e['data']['starters']]
            skill_rows.append(mean(world.players[p].composite_skill() for p in starters))
            now = {e['data']['club_id']: set(e['data']['starters']) for e in new if e['kind'] == 'lineup_decision'}
            if previous is not None:
                swaps += sum(len(now[c] - previous[c]) for c in now if c in previous)
                lineups += len(now)
            previous = now
        if world.day % L == 0:
            assert all(len(r) >= 3 for r in world.club_rosters.values()), (name, seed, start, world.day)
            if (world.day // L) % validate_every == 0 or world.day == days:
                le._validate_league_invariants(world)
        for pid, info in newcomers.items():
            if info['day'] == world.day - L:
                ev = world.coaches[le.CLUB_COACH_MAP[info['club']]].evaluations[pid]
                roster = world.club_rosters[info['club']]
                true = world.players[pid].composite_skill()
                top2 = sorted(roster, key=lambda p: (-world.players[p].composite_skill(), p))[:2]
                info.update({'observed': ev.last_seen_day > info['day'], 'error': abs(ev.skill_estimate - true), 'match_error': abs(ev.match_rating_estimate - true), 'top2_true': pid in top2})
    le._validate_league_invariants(world)
    matchdays = days // world.config.match_interval_days
    matches = [e for e in world.events if e['kind'] == 'league_match_completed']
    assert len(matches) == 2 * matchdays
    for pid, info in newcomers.items():
        if 'observed' in info:
            mine = [e for e in matches if info['day'] < e['day'] <= info['day'] + L]
            info['matchdays'] = len({e['day'] for e in mine})
            info['starts'] = sum(1 for e in mine if pid in e['data']['home_starters'] or pid in e['data']['away_starters'])
    decisions = [e['data']['chosen']['kind'] for e in world.events if e['kind'] == 'coach_training_decision']
    return world, {'final': t030.final_metrics(world), 'events': len(world.events), 'min_roster_any_day': min_roster, 'newcomers': newcomers, 'practice_updates_checked': checked,
                   'slots': {k: decisions.count(k) / len(decisions) for k in ('propose', 'observe', 'scout', 'rest')}, 'starter_skill': mean(skill_rows),
                   'lineup_swap_rate': swaps / lineups if lineups else 0.0}


# ---- gate 5b: a benched player's true skill cannot reach selection before he is first observed ----
def check_leak_dynamic():
    name = t023.conditions()[1]
    out = {}
    for mode in ('off', 'on'):
        worlds = []
        for bump in (0.0, 0.08):
            w = le._build_initial_world(seed=100, config=cfg(name, 33, mode))
            club = next(c for c in le.CLUB_IDS)
            lineup = le.decide_club_lineup(coach=w.coaches[le.CLUB_COACH_MAP[club]], roster=list(w.club_rosters[club]), config=w.config)
            pid = lineup['bench'][0]
            p = w.players[pid]
            p.traits = replace(p.traits, pace=min(p.cap, p.traits.pace + bump), passing=min(p.cap, p.traits.passing + bump), shooting=min(p.cap, p.traits.shooting + bump))
            worlds.append((w, pid))
        (a, pid), (b, pid_b) = worlds
        assert pid == pid_b and a.players[pid].composite_skill() != b.players[pid].composite_skill()
        compared = 0
        for _ in range(60):
            n_a, n_b = len(a.events), len(b.events)
            le.advance_league_days(a, 1)
            le.advance_league_days(b, 1)
            kinds = ('coach_training_decision', 'lineup_decision')
            da = [e['data'] for e in a.events[n_a:] if e['kind'] in kinds]
            db = [e['data'] for e in b.events[n_b:] if e['kind'] in kinds]
            if any(c.evaluations[pid].last_seen_day in (a.day, b.day) for c in (*a.coaches.values(), *b.coaches.values())):
                break
            assert da == db, (mode, a.day)
            compared += 1
        assert compared >= 5, (mode, compared)
        out[mode] = {'days_compared_before_first_observation': compared}
    return out


# ---- gate 7 ----
def check_persistence(directory):
    done = 0
    for name in t023.conditions():
        world = le.run_league_world(seed=100, days=DAYS, config=cfg(name, 33, 'on'))
        folder = Path(directory) / name
        le.save_league_artifacts(world, folder)
        for reader in (le.load_league_snapshot, le.load_league_snapshot_trusted):
            le._assert_same(reader(folder / 'snapshot.json'), world, 'world')
        done += sum(1 for e in world.events if e['kind'] == 'coach_session_observation')
    return {'worlds': 14, 'coach_session_observations': done}


def check_branch_and_steps(directory):
    config = cfg(t023.conditions()[0], 33, 'on')
    parent = le.run_league_world(seed=100, days=30, config=config)
    child = le.fork_league_world(parent, kind='player_trust', player_id='p3', coach_id='c1', value=0.05, reason='t032')
    le.advance_league_days(child, 60)
    le.save_league_artifacts(child, directory)
    le._assert_same(le.load_league_snapshot(Path(directory) / 'snapshot.json'), child, 'world')
    stepped = le._build_initial_world(seed=100, config=config)
    for _ in range(4):
        le.advance_league_days(stepped, L)
    le.advance_league_days(stepped, 6)
    le._assert_same(stepped, le.run_league_world(seed=100, days=90, config=config), 'world')
    return {'interventions': len(child.interventions), 'stepped_equals_oneshot': True}


def check_tamper(directory):
    world = le.run_league_world(seed=100, days=DAYS, config=cfg(t023.conditions()[1], 33, 'on'))
    folder = Path(directory) / 'config_practice_rating_removed'
    le.save_league_artifacts(world, folder)
    snap, manifest = folder / 'snapshot.json', folder / 'manifest.json'
    raw = json.loads(snap.read_text())
    raw['config'].pop('coach_practice_rating')
    snap.write_text(json.dumps(raw))
    record = json.loads(manifest.read_text())
    record['snapshot_sha256'] = digest(raw)
    manifest.write_text(json.dumps(record))
    out = {}
    for reader in (le.load_league_snapshot, le.load_league_snapshot_trusted):
        try:
            reader(snap)
            out[reader.__name__] = 'accepted'
        except ValueError:
            out[reader.__name__] = 'rejected'
    assert out['load_league_snapshot'] == 'rejected', out  # the trusted reader is recorded, not gated
    return out


# ---- analysis ----
def analyse(main):
    def rows(mode, start=None):
        return [i for (n, s, st), c in main.items() if start in (None, st) for i in c[mode]['newcomers'].values() if 'observed' in i]
    out, summary = {}, {}
    for mode in ('off', 'on'):
        r = rows(mode)
        top = [i for i in r if i['top2_true']]
        summary[mode] = {'newcomers_with_a_full_season': len(r), 'observed_share': mean(1.0 if i['observed'] else 0.0 for i in r), 'started_share': mean(1.0 if i['starts'] else 0.0 for i in r),
                         'start_rate_per_matchday': mean(i['starts'] / i['matchdays'] for i in r if i['matchdays']), 'skill_error': mean(i['error'] for i in r), 'match_error': mean(i['match_error'] for i in r),
                         'top2_true_newcomers': len(top), 'top2_true_started_share': mean(1.0 if i['starts'] else 0.0 for i in top), 'missed_talent': sum(1 for i in top if not i['starts'])}
    out['newcomers'] = summary
    lower = better = 0
    for c in main.values():
        a, b = [i['match_error'] for i in c['on']['newcomers'].values() if 'observed' in i], [i['match_error'] for i in c['off']['newcomers'].values() if 'observed' in i]
        if a and b:
            lower += 1
            better += mean(a) < mean(b)
    out['P1_match_error'] = {'on': summary['on']['match_error'], 'off': summary['off']['match_error'], 'cells_with_newcomers': lower, 'cells_on_lower': better, 'supported': better > lower / 2}
    d2 = summary['on']['started_share'] - summary['off']['started_share']
    out['P2_started'] = {'on': summary['on']['started_share'], 'off': summary['off']['started_share'], 'diff': d2, 'supported': d2 >= 0.03}
    d3 = summary['on']['top2_true_started_share'] - summary['off']['top2_true_started_share']
    out['P3_top2_started'] = {'on': summary['on']['top2_true_started_share'], 'off': summary['off']['top2_true_started_share'], 'diff': d3,
                              'missed_talent_on': summary['on']['missed_talent'], 'missed_talent_off': summary['off']['missed_talent'], 'supported': d3 >= 0.03}
    costs = {}
    for metric in ('skill', 'satisfaction', 'trust', 'transfers'):
        d = [c['on']['final'][metric] - c['off']['final'][metric] for c in main.values()]
        costs[metric] = {'mean_on_minus_off': mean(d), 'sign_test': sign_test(d)}
    for metric in ('starter_skill', 'lineup_swap_rate'):
        d = [c['on'][metric] - c['off'][metric] for c in main.values()]
        costs[metric] = {'mean_on_minus_off': mean(d), 'sign_test': sign_test(d), 'off': mean(c['off'][metric] for c in main.values()), 'on': mean(c['on'][metric] for c in main.values())}
    for slot in ('propose', 'observe', 'scout', 'rest'):
        costs[f'slot_share_{slot}'] = {'off': mean(c['off']['slots'][slot] for c in main.values()), 'on': mean(c['on']['slots'][slot] for c in main.values())}
    costs['events_on_minus_off'] = mean(c['on']['events'] - c['off']['events'] for c in main.values())
    out['P4_costs'] = costs
    out['P4_material'] = {'skill_below_-0.005': costs['skill']['mean_on_minus_off'] < -0.005, 'trust_below_-0.010': costs['trust']['mean_on_minus_off'] < -0.010,
                          'satisfaction_below_-0.010': costs['satisfaction']['mean_on_minus_off'] < -0.010, 'skill_not_negative': costs['skill']['mean_on_minus_off'] >= 0,
                          'starter_skill_positive': costs['starter_skill']['mean_on_minus_off'] > 0}
    out['by_start'] = {str(st): {m: {'started_share': mean(1.0 if i['starts'] else 0.0 for i in rows(m, st)), 'newcomers': len(rows(m, st)), 'match_error': mean(i['match_error'] for i in rows(m, st))}
                                for m in ('off', 'on')} for st in STARTS}
    return out


def run(out, root):
    out.mkdir(parents=True, exist_ok=False)
    start = perf_counter()
    default = check_default_unchanged(root)
    regression = t019.check_regression(root)
    print('default', default, regression, flush=True)
    invalid, static = check_invalid(), check_static()
    print('invalid', invalid, 'static', static, flush=True)
    leak = check_leak_dynamic()
    print('leak', leak, flush=True)
    t031_cells = {(c['world'], c['seed'], c['start_age']): c['on'] for c in json.loads((root / 'artifacts/t031/run/summary.json').read_text())['cells']}
    main, isolation, divergence, control_equal = {}, {}, {}, 0
    for name in t023.conditions():
        for seed in SEEDS:
            for st in STARTS:
                off_world, off = run_cell(name, seed, st, 'off', DAYS)
                on_world, on = run_cell(name, seed, st, 'on', DAYS)
                ref = t031_cells[(name, seed, st)]
                assert off['final'] == ref['final'] and off['events'] == ref['events'], (name, seed, st)  # gate 1: the control is the T031 cell
                control_equal += 1
                divergence[(name, seed, st)] = first_divergence(off_world, on_world)
                isolation[(name, seed, st)] = check_isolation(name, seed, st)
                assert divergence[(name, seed, st)] is not None and divergence[(name, seed, st)]['day'] >= isolation[(name, seed, st)]['first_practice_observation_day'], (name, seed, st, divergence[(name, seed, st)])
                main[(name, seed, st)] = {'off': off, 'on': on}
        print('main', name, flush=True)
    analysis = analyse(main)
    print('analysis', json.dumps({k: v for k, v in analysis.items() if k.startswith('P')}), flush=True)
    with tempfile.TemporaryDirectory() as tmp:
        persisted = check_persistence(Path(tmp) / 'p')
        branch = check_branch_and_steps(Path(tmp) / 'branch')
        tamper = check_tamper(Path(tmp) / 'tamper')
    print('persistence', persisted, branch, tamper, flush=True)
    table = [{'world': n, 'seed': s, 'start_age': st, 'first_divergence': divergence[(n, s, st)], 'isolation_day1': isolation[(n, s, st)],
              **{m: {k: v for k, v in c[m].items() if k != 'newcomers'} for m in c}} for (n, s, st), c in main.items()]
    days = [v['day'] for v in divergence.values()]
    result = {'task': 'T032', 'identity': le.league_identity(), 'runtime': runtime(), 'seeds': list(SEEDS), 'season_length_days': L, 'days': DAYS,
              'default_unchanged': default, 'default_vs_t018': regression, 'invalid_rejected': invalid, 'static': static, 'leak_dynamic': leak,
              'control_cells_equal_to_t031': control_equal, 'first_divergence_day_min_max': [min(days), max(days)], 'first_divergence_kinds': {k: sum(1 for v in divergence.values() if v['kind'] == k) for k in sorted({v['kind'] for v in divergence.values()})},
              'persistence': persisted, 'branch': branch, 'tamper': tamper, 'practice_updates_checked': sum(c['on']['practice_updates_checked'] for c in main.values()),
              'analysis': analysis, 'cells': table, 'elapsed_seconds': perf_counter() - start}
    write_json(out / 'summary.json', result)
    print(json.dumps({'cells': len(main), 'elapsed_seconds': result['elapsed_seconds']}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    run(parser.parse_args().out.resolve(), CODE_ROOT)
