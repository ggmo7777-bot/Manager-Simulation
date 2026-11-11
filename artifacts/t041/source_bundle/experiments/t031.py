"""T031: the coach's observe value keeps growing with days unseen (cap 10 -> 40), so benched players get seen (coach_exploration).

Protocol: docs/experiments/T031-PROTOCOL.md. Cells: 14 worlds x seeds {100,101,102} x start age {29,33}, T030 generations on, L=21, 168 days,
coach_exploration off (control) / on. Every observe score is re-derived independently; costs are reported against the control.
"""
import argparse
import ast
from copy import deepcopy
from dataclasses import replace
import json
from math import comb
from pathlib import Path
import statistics
import sys
import tempfile
from time import perf_counter

CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT))
from experiments import t019, t023, t026, t027, t029, t030
from soccer_sim import league_economy as le
from soccer_sim.storage import digest, runtime, write_json

L, DAYS, SEEDS, STARTS = 21, 168, (100, 101, 102), (29, 33)
MODES = {'off': 'none', 'on': 'unseen_bonus_v1'}
mean = t030.mean
FORBIDDEN = {'players', 'initial_players', 'traits', 'composite_skill', 'player_ages', 'player_personality', 'aging_curve', 'world', 'player_minds'}


def cfg(name, start, mode, **kw):
    return replace(t030.cfg(name, start, 'on'), coach_exploration=MODES[mode], **kw)


def ind_observe(day, last_seen, skill_estimate, cap, repeat):
    """Independent re-implementation of the observe candidate's score (literal numbers)."""
    need = max(0.0, 0.78 - skill_estimate)
    return round(0.12 + 0.018 * min(cap, max(0, day - last_seen)) + 0.22 * need - (0.25 if repeat else 0.0), 6)


def touched(node):
    return t030.touched(node)


# ---- gates 2, 3, 5a ----
def check_invalid():
    ok = {}
    base = cfg(t023.conditions()[0], 29, 'off')
    for label, value in (('unknown', 'bogus'), ('int', 1), ('empty', '')):
        try:
            replace(base, coach_exploration=value)
            ok[label] = False
        except ValueError:
            ok[label] = True
    cls, on = le.LeagueEconomyConfig, cfg(t023.conditions()[0], 29, 'on').to_dict()
    ok['save_fields'] = cls.has_save_fields(on) and cls.has_save_fields({k: v for k, v in on.items() if k != 'coach_exploration'}) and not cls.has_save_fields({**on, 'unknown': 1})
    assert all(ok.values()), ok
    return ok


def check_pure():
    world = le.run_league_world(seed=100, days=14, config=cfg(t023.conditions()[0], 33, 'off'))
    args = t029.synthetic_decision_inputs(world)
    n = 0
    for stale in range(0, 61):
        coach = deepcopy(args['coach'])
        for pid in list(coach.evaluations):
            coach.evaluations[pid].last_seen_day = args['day'] - stale
        for repeat in (set(), set(args['own_roster'][:1])):
            run = {c: le.decide_coach_training(**{**args, 'coach': coach, 'already_targeted': repeat}, **({} if c is None else {'staleness_cap': c})) for c in (None, 10, 40)}
            assert run[None] == run[10]  # the default argument is the old behaviour
            for cap in (10, 40):
                for cand in run[cap]['candidates']:
                    if cand['kind'] == 'observe':
                        want = ind_observe(args['day'], args['day'] - stale, coach.evaluations[cand['target']].skill_estimate, cap, cand['target'] in repeat)
                        assert cand['score'] == want, (stale, cap, cand, want)
                        n += 1
            if stale <= 10:
                assert run[10]['candidates'] == run[40]['candidates']
            else:
                assert [c for c in run[10]['candidates'] if c['kind'] != 'observe'] == [c for c in run[40]['candidates'] if c['kind'] != 'observe']
                assert all(b['score'] > a['score'] for a, b in zip(run[10]['candidates'], run[40]['candidates']) if a['kind'] == 'observe')
    first = args['own_roster'][0]
    scores = [next(c['score'] for c in le.decide_coach_training(**{**args, 'coach': coach_at(args['coach'], args['day'] - s)}, staleness_cap=40)['candidates'] if c['kind'] == 'observe' and c['target'] == first) for s in range(61)]
    assert all(b >= a for a, b in zip(scores, scores[1:]))  # non-decreasing in days unseen
    return {'observe_scores_checked': n}


def coach_at(coach, last_seen):
    coach = deepcopy(coach)
    for ev in coach.evaluations.values():
        ev.last_seen_day = last_seen
    return coach


def check_static():
    t026.check_policy_blind()
    t027.check_leak_static()
    source = (CODE_ROOT / 'soccer_sim/league_economy.py').read_text()
    funcs = {n.name: n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.FunctionDef)}
    names = touched(funcs['decide_coach_training'])
    assert not names & FORBIDDEN, names & FORBIDDEN
    assert 'staleness_cap' in names and 'EXPLORE_STALENESS_CAP' not in names and 'coach_exploration' not in names
    words = {'coach_exploration', 'EXPLORE_STALENESS_CAP', 'COACH_EXPLORATION_MODES', 'unseen_bonus_v1'}
    readers = {n.name for n in ast.walk(ast.parse(source)) if isinstance(n, ast.FunctionDef) and touched(n) & words}  # every def, also same-named methods
    allowed = {'__post_init__', 'to_dict', 'has_save_fields', '_step_training_day'}
    assert readers <= allowed and not any(r.startswith('decide_') for r in readers), sorted(readers)
    for module in t026.POLICY_MODULES + ('match_2d.py', 'selection_loop.py', 'transfer_loop.py'):
        assert not any(w in (CODE_ROOT / 'soccer_sim' / module).read_text() for w in words), module
    bad = ast.parse('def decide_coach_training(coach, world, staleness_cap=10):\n    return world.players\n')
    assert touched(bad) & FORBIDDEN == {'players', 'world'}
    assert [a.arg for a in funcs['decide_coach_training'].args.kwonlyargs] == ['coach', 'own_roster', 'other_players', 'club_cash_units', 'day', 'slot', 'already_targeted', 'config', 'staleness_cap', 'receptiveness']
    return {'exploration_readers': sorted(readers)}


# ---- gate 1 ----
def check_default_unchanged(root):
    base = t030.check_default_unchanged(root)
    for name in t023.conditions():
        explicit = le._snapshot_payload_unchecked(le.run_league_world(seed=100, days=2 * L, config=replace(cfg(name, 29, 'off'), coach_exploration='none')))
        default = le._snapshot_payload_unchecked(le.run_league_world(seed=100, days=2 * L, config=t030.cfg(name, 29, 'on')))
        assert explicit == default and 'coach_exploration' not in explicit['config'], name
    world = le.run_league_world(seed=100, days=14, config=cfg(t023.conditions()[0], 33, 'off'))
    args = t029.synthetic_decision_inputs(world)
    assert le.decide_coach_training(**args) == le.decide_coach_training(**args, staleness_cap=10)
    return {**base, 'explicit_none_equals_default': 14}


# ---- one verified, stepped cell ----
def run_cell(name, seed, start, mode, days, validate_every=2):
    config = cfg(name, start, mode)
    cap = 40 if mode == 'on' else 10
    world = le._build_initial_world(seed=seed, config=config)
    calls, original = [], le.decide_coach_training

    def recording(**kw):
        seen = {pid: kw['coach'].evaluations[pid].last_seen_day for pid in (*kw['own_roster'], *kw['other_players'])}
        evals = {pid: kw['coach'].evaluations[pid].skill_estimate for pid in kw['own_roster']}
        got = original(**kw)
        calls.append((kw['day'], seen, evals, set(kw['already_targeted']), got))
        return got
    le.decide_coach_training = recording
    newcomers, min_roster, skill_rows, checked, long_unseen_observes, observes = {}, 3, [], 0, 0, 0
    try:
        while world.day < days:
            n_events = len(world.events)
            calls.clear()
            le.advance_league_days(world, 1)
            min_roster = min(min_roster, min(len(r) for r in world.club_rosters.values()))
            for day, seen, evals, repeat, got in calls:  # gate 4: every observe score and its staleness term re-derived
                for cand in got['candidates']:
                    if cand['kind'] == 'observe':
                        want = ind_observe(day, seen[cand['target']], evals[cand['target']], cap, cand['target'] in repeat)
                        assert cand['score'] == want and cand['components']['staleness'] == round(0.018 * min(cap, max(0, day - seen[cand['target']])), 6), (name, seed, day, cand)
                        checked += 1
                ch = got['chosen']
                if ch['kind'] == 'observe':
                    observes += 1
                    long_unseen_observes += day - seen[ch['target']] > 10
            new = world.events[n_events:]
            joined = [e for e in new if e['kind'] == 'season_youth_joined']
            for e in joined:
                d = e['data']
                newcomers[d['player_id']] = {'club': d['club_id'], 'day': world.day}
            if new and any(e['kind'] == 'league_match_completed' for e in new):  # true skill of the starters just selected
                starters = [pid for e in new if e['kind'] == 'lineup_decision' for pid in e['data']['starters']]
                skill_rows.append(mean(world.players[p].composite_skill() for p in starters))
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
                    info.update({'observed': ev.last_seen_day > info['day'], 'error': abs(ev.skill_estimate - true), 'top2_true': pid in top2})
    finally:
        le.decide_coach_training = original
    le._validate_league_invariants(world)
    matchdays = days // world.config.match_interval_days
    assert len([e for e in world.events if e['kind'] == 'league_match_completed']) == 2 * matchdays
    matches = [e for e in world.events if e['kind'] == 'league_match_completed']
    for pid, info in newcomers.items():
        if 'observed' in info:
            mine = [e for e in matches if info['day'] < e['day'] <= info['day'] + L]
            info['matchdays'] = len({e['day'] for e in mine})
            info['starts'] = sum(1 for e in mine if pid in e['data']['home_starters'] or pid in e['data']['away_starters'])
    decisions = [e['data']['chosen']['kind'] for e in world.events if e['kind'] == 'coach_training_decision']
    final = t030.final_metrics(world)
    return world, {'final': final, 'events': len(world.events), 'min_roster_any_day': min_roster, 'newcomers': newcomers, 'observe_scores_checked': checked,
                   'observes': observes, 'observes_on_players_unseen_over_10_days': long_unseen_observes,
                   'slots': {k: decisions.count(k) / len(decisions) for k in ('propose', 'observe', 'scout', 'rest')}, 'starter_skill': mean(skill_rows)}


# ---- gate 5b: a benched player's true skill cannot reach the coaches' decisions before he is first observed ----
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
            da = [e['data'] for e in a.events[n_a:] if e['kind'] == 'coach_training_decision']
            db = [e['data'] for e in b.events[n_b:] if e['kind'] == 'coach_training_decision']
            observed = any(c.evaluations[pid].last_seen_day == a.day or c.evaluations[pid].last_seen_day == b.day for c in (*a.coaches.values(), *b.coaches.values()))
            if observed:
                break
            assert da == db, (mode, a.day)
            compared += 1
        assert compared >= 5, (mode, compared)
        out[mode] = {'days_compared_before_first_observation': compared}
    return out


# ---- gate 7 ----
def normalize(world):
    raw = json.loads(json.dumps(le._snapshot_payload_unchecked(world)))
    raw['config'].pop('coach_exploration', None)
    return raw


def check_prefix(on_world, off_world):
    """Everything up to day 10 (when no player can yet have been unseen for more than 10 days) equals the control."""
    on, off = normalize(on_world)['events'], normalize(off_world)['events']
    k = next((i for i, (x, y) in enumerate(zip(on, off)) if x != y), min(len(on), len(off)))
    assert all(e['day'] > 10 for e in on[k:k + 1]) and k > 0, (k, on[k]['day'] if k < len(on) else None)
    return {'first_differing_event': k, 'day': on[k]['day'] if k < len(on) else None}


# ---- gate 8 ----
def check_persistence(directory):
    done = 0
    for name in t023.conditions():
        world = le.run_league_world(seed=100, days=DAYS, config=cfg(name, 33, 'on'))
        folder = Path(directory) / name
        le.save_league_artifacts(world, folder)
        for reader in (le.load_league_snapshot, le.load_league_snapshot_trusted):
            le._assert_same(reader(folder / 'snapshot.json'), world, 'world')
        done += sum(1 for e in world.events if e['kind'] == 'coach_training_decision')
    return {'worlds': 14, 'coach_training_decisions': done}


def check_branch_and_steps(directory):
    config = cfg(t023.conditions()[0], 33, 'on')
    parent = le.run_league_world(seed=100, days=30, config=config)
    child = le.fork_league_world(parent, kind='player_trust', player_id='p3', coach_id='c1', value=0.05, reason='t031')
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
    folder = Path(directory) / 'config_exploration_removed'
    le.save_league_artifacts(world, folder)
    snap, manifest = folder / 'snapshot.json', folder / 'manifest.json'
    raw = json.loads(snap.read_text())
    raw['config'].pop('coach_exploration')
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
    assert out['load_league_snapshot'] == 'rejected', out  # the full-replay reader re-derives the decisions; the trusted one is recorded, not gated
    return out


# ---- analysis ----
def sign_test(diffs):
    nonzero = [d for d in diffs if d != 0]
    pos, n = sum(1 for d in nonzero if d > 0), len(nonzero)
    p = min(1.0, 2 * sum(comb(n, k) for k in range(0, min(pos, n - pos) + 1)) / 2 ** n) if n else 1.0
    return {'positive': pos, 'negative': n - pos, 'ties': len(diffs) - n, 'p_two_sided': p}


def analyse(main):
    out = {}

    def rows(mode, start=None):
        return [i for (n, s, st), c in main.items() if start in (None, st) for i in c[mode]['newcomers'].values() if 'observed' in i]
    summary = {}
    for mode in ('off', 'on'):
        r = rows(mode)
        summary[mode] = {'newcomers_with_a_full_season': len(r), 'observed_share': mean(1.0 if i['observed'] else 0.0 for i in r), 'started_share': mean(1.0 if i['starts'] else 0.0 for i in r),
                         'start_rate_per_matchday': mean(i['starts'] / i['matchdays'] for i in r if i['matchdays']), 'error_after_season': mean(i['error'] for i in r),
                         'top2_true_newcomers': sum(1 for i in r if i['top2_true']),
                         'top2_true_started_share': mean(1.0 if i['starts'] else 0.0 for i in r if i['top2_true'])}
    out['newcomers'] = summary
    out['P1_observed'] = {'on': summary['on']['observed_share'], 'off': summary['off']['observed_share'], 'supported': summary['on']['observed_share'] >= 0.90}
    out['P2_started'] = {'diff': summary['on']['started_share'] - summary['off']['started_share'], 'supported': summary['on']['started_share'] - summary['off']['started_share'] >= 0.03}
    cells = [(c['on']['newcomers'], c['off']['newcomers']) for c in main.values()]
    lower = better = 0
    for on, off in cells:
        a, b = [i['error'] for i in on.values() if 'observed' in i], [i['error'] for i in off.values() if 'observed' in i]
        if a and b:
            lower += 1
            better += mean(a) < mean(b)
    out['P3_error'] = {'on': summary['on']['error_after_season'], 'off': summary['off']['error_after_season'], 'cells_with_newcomers': lower, 'cells_on_lower': better, 'supported': better > lower / 2}
    costs = {}
    for metric in ('skill', 'satisfaction', 'trust', 'transfers'):
        d = [c['on']['final'][metric] - c['off']['final'][metric] for c in main.values()]
        costs[metric] = {'mean_on_minus_off': mean(d), 'sign_test': sign_test(d)}
    d = [c['on']['starter_skill'] - c['off']['starter_skill'] for c in main.values()]
    costs['starter_skill'] = {'mean_on_minus_off': mean(d), 'sign_test': sign_test(d)}
    for slot in ('propose', 'observe', 'scout', 'rest'):
        costs[f'slot_share_{slot}'] = {'off': mean(c['off']['slots'][slot] for c in main.values()), 'on': mean(c['on']['slots'][slot] for c in main.values())}
    costs['events_on_minus_off'] = mean(c['on']['events'] - c['off']['events'] for c in main.values())
    costs['observes_on_players_unseen_over_10_days'] = {'off': sum(c['off']['observes_on_players_unseen_over_10_days'] for c in main.values()), 'on': sum(c['on']['observes_on_players_unseen_over_10_days'] for c in main.values()),
                                                         'observes_off': sum(c['off']['observes'] for c in main.values()), 'observes_on': sum(c['on']['observes'] for c in main.values())}
    out['P4_costs'] = costs
    out['P4_material'] = {'skill_below_-0.005': costs['skill']['mean_on_minus_off'] < -0.005, 'trust_below_-0.010': costs['trust']['mean_on_minus_off'] < -0.010,
                          'skill_negative': costs['skill']['mean_on_minus_off'] < 0, 'trust_negative': costs['trust']['mean_on_minus_off'] < 0, 'starter_skill_not_positive': costs['starter_skill']['mean_on_minus_off'] <= 0}
    out['by_start'] = {str(st): {m: {'observed_share': mean(1.0 if i['observed'] else 0.0 for i in rows(m, st)), 'started_share': mean(1.0 if i['starts'] else 0.0 for i in rows(m, st)),
                                    'newcomers': len(rows(m, st))} for m in ('off', 'on')} for st in STARTS}
    return out


def run(out, root):
    out.mkdir(parents=True, exist_ok=False)
    start = perf_counter()
    default = check_default_unchanged(root)
    regression = t019.check_regression(root)
    print('default', default, regression, flush=True)
    invalid, pure, static = check_invalid(), check_pure(), check_static()
    print('invalid', invalid, 'pure', pure, 'static', static, flush=True)
    leak = check_leak_dynamic()
    print('leak', leak, flush=True)
    t030_cells = {(c['world'], c['seed'], c['start_age']): c['on'] for c in json.loads((root / 'artifacts/t030/run/summary.json').read_text())['cells']}
    main, prefix, control_equal = {}, {}, 0
    for name in t023.conditions():
        for seed in SEEDS:
            for st in STARTS:
                off_world, off = run_cell(name, seed, st, 'off', DAYS)
                on_world, on = run_cell(name, seed, st, 'on', DAYS)
                ref = t030_cells[(name, seed, st)]
                assert off['final'] == ref['final'] and off['events'] == ref['events'], (name, seed, st)  # gate 1: the control is the T030 cell
                control_equal += 1
                prefix[(name, seed, st)] = check_prefix(on_world, off_world)
                main[(name, seed, st)] = {'off': off, 'on': on}
        print('main', name, flush=True)
    analysis = analyse(main)
    print('analysis', json.dumps({k: v for k, v in analysis.items() if k.startswith('P')}), flush=True)
    with tempfile.TemporaryDirectory() as tmp:
        persisted = check_persistence(Path(tmp) / 'p')
        branch = check_branch_and_steps(Path(tmp) / 'branch')
        tamper = check_tamper(Path(tmp) / 'tamper')
    print('persistence', persisted, branch, tamper, flush=True)
    table = [{'world': n, 'seed': s, 'start_age': st, **{m: {k: v for k, v in c[m].items() if k != 'newcomers'} for m in c}} for (n, s, st), c in main.items()]
    result = {'task': 'T031', 'identity': le.league_identity(), 'runtime': runtime(), 'seeds': list(SEEDS), 'season_length_days': L, 'days': DAYS,
              'default_unchanged': default, 'default_vs_t018': regression, 'invalid_rejected': invalid, 'pure_function': pure, 'static': static, 'leak_dynamic': leak,
              'control_cells_equal_to_t030': control_equal, 'first_differing_event_day_min_max': [min(v['day'] for v in prefix.values() if v['day'] is not None), max(v['day'] for v in prefix.values() if v['day'] is not None)],
              'cells_identical_to_control': sum(1 for v in prefix.values() if v['day'] is None), 'persistence': persisted, 'branch': branch, 'tamper': tamper,
              'observe_scores_checked': sum(c[m]['observe_scores_checked'] for c in main.values() for m in c), 'analysis': analysis, 'cells': table,
              'elapsed_seconds': perf_counter() - start}
    write_json(out / 'summary.json', result)
    print(json.dumps({'cells': len(main), 'elapsed_seconds': result['elapsed_seconds']}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    run(parser.parse_args().out.resolve(), CODE_ROOT)
