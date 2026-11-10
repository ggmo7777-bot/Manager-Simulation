"""T030: season-end retirement from the player's own state and same-day youth intake (generations), recorded as events.

Protocol: docs/experiments/T030-PROTOCOL.md. Main grid: 14 worlds x seeds {100,101,102} x start age {18,29,33}, L=21, 168 days (8 seasons),
generations off (control) / on. Long run: start age 29, on, 630 days (30 seasons). Every rule application is re-derived independently.
"""
import argparse
import ast
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from math import comb
from pathlib import Path
import statistics
import sys
import tempfile
from time import perf_counter

CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT))
from experiments import t019, t023, t026, t027, t028, t029
from soccer_sim import league_economy as le
from soccer_sim.observation import RNG_VERSION
from soccer_sim.storage import digest, runtime, write_json

L, DAYS, LONG_DAYS, SEEDS, STARTS = 21, 168, 630, (100, 101, 102), (18, 29, 33)
MODES = {'off': 'none', 'on': 'retire_replace_v1'}
RETIRE_KEYS = {'record_class', 'season', 'player_id', 'club_id', 'age', 'playing_time_satisfaction', 'reason', 'rule'}
JOIN_KEYS = {'season', 'player_id', 'club_id', 'age', 'reason', 'roster_size_before'}
FORBIDDEN = t029.FORBIDDEN | {'player_ages', 'aging_curve', 'traits', 'cap', 'rates', 'composite_skill', 'world', 'coaches'}
TRUE_VALUE_WORDS = {'traits', 'cap', 'rates', 'composite_skill', 'player_personality', 'discipline', 'emotional_stability'}
# Literal copy of the academy ranges (hypothesis; not imported from the implementation).
RANGE = {'trait': (0.42, 0.52), 'rate': (0.104, 0.126), 'cap': (0.90, 0.95), 'loyalty': (0.05, 0.30), 'ambition': (0.35, 0.75),
         'autonomy': (0.32, 0.38), 'self_bias': (-0.03, 0.03)}
CLUBS = ('club_a', 'club_b', 'club_c', 'club_d')


def cfg(name, start, mode, **kw):
    return replace(t028.cfg(name, 'on', season_length_days=L), aging_start_age=start, aging_curve='hypothesis_v1', generations=MODES[mode], **kw)


# ---- independent re-implementation of the pre-registered rule and generator (literal numbers, not imported) ----
def ind_retire(age, satisfaction):
    if age >= 36:
        return {'retire': True, 'reason': 'age_limit'}
    if age >= 33 and satisfaction < 0.5:
        return {'retire': True, 'reason': 'veteran_low_playing_time'}
    return {'retire': False, 'reason': None}


def ind_draw(seed, name, pid, low, high):
    encoded = json.dumps((RNG_VERSION, seed, 'population:youth_' + name, 0, pid, 0), separators=(',', ':')).encode('utf-8')
    return round(low + (high - low) * ((int.from_bytes(hashlib.sha256(encoded).digest()[:8], 'big') >> 11) / (1 << 53)), 6)


def ind_youth(seed, pid):
    traits = [ind_draw(seed, f'trait{i}', pid, *RANGE['trait']) for i in range(3)]
    level = sum(traits) / 3.0
    cap = round(max(ind_draw(seed, 'cap', pid, *RANGE['cap']), min(0.98, max(traits) + 0.05)), 6)
    return {'traits': traits, 'rates': {'A': ind_draw(seed, 'rateA', pid, *RANGE['rate']), 'B': ind_draw(seed, 'rateB', pid, *RANGE['rate'])},
            'cap': cap, 'pref': 'A' if ind_draw(seed, 'pref', pid, 0.0, 1.0) < 0.5 else 'B',
            'loyalty': ind_draw(seed, 'loyalty', pid, *RANGE['loyalty']), 'ambition': ind_draw(seed, 'ambition', pid, *RANGE['ambition']),
            'autonomy': ind_draw(seed, 'autonomy', pid, *RANGE['autonomy']),
            'self_est': round(min(0.95, max(0.05, level + ind_draw(seed, 'self_bias', pid, *RANGE['self_bias']))), 6)}


def check_youth_state(world, pid, join_event):
    """A newcomer's true state, mind, coach impressions and personality equal the independent pre-registered construction."""
    ind, truth, mind = ind_youth(world.seed, pid), world.initial_players[pid], world.player_minds
    club = join_event['data']['club_id']
    assert [truth.traits.pace, truth.traits.passing, truth.traits.shooting] == ind['traits'] and truth.rates == ind['rates'] and truth.cap == ind['cap'], pid
    assert truth.origin_club_id == club and truth.fatigue == 0.10 and truth.appearances == 0 and truth.transfers_count == 0, pid
    assert world.players[pid].origin_club_id == club
    m = mind[pid]
    # The mind may have evolved since joining; the fixed fields must equal the construction.
    assert (m.autonomy, m.ambition, m.loyalty_bias, m.preferred_method, m.rest_preference) == (ind['autonomy'], ind['ambition'], ind['loyalty'], ind['pref'], 0.012), pid
    if world.config.personality != 'none':
        assert world.player_personality[pid] == {t: t027.independent_draw(world.seed, t, pid) for t in t027.TRAITS}, pid


# ---- gates 2, 3, 8a ----
def check_invalid():
    ok = {}
    base = cfg(t023.conditions()[0], 29, 'off')
    for label, value in (('unknown', 'bogus'), ('int', 1), ('empty', '')):
        try:
            replace(base, generations=value)
            ok[label] = False
        except ValueError:
            ok[label] = True
    try:
        replace(le.config_for_condition('league_' + t023.conditions()[0]), generations='retire_replace_v1')
        ok['without_aging'] = False
    except ValueError:
        ok['without_aging'] = True
    cls, on = le.LeagueEconomyConfig, cfg(t023.conditions()[0], 29, 'on').to_dict()
    ok['save_fields'] = cls.has_save_fields(on) and cls.has_save_fields({k: v for k, v in on.items() if k != 'generations'}) and not cls.has_save_fields({**on, 'unknown': 1})
    assert all(ok.values()), ok
    return ok


def check_pure():
    n = 0
    for age in range(16, 46):
        previous = None
        for step in range(0, 101):
            sat = step / 100
            got = le.decide_retirement(age=age, playing_time_satisfaction=sat)
            assert got == ind_retire(age, sat) and set(got) == {'retire', 'reason'}, (age, sat)
            if age >= 36:
                assert got['retire'] and got['reason'] == 'age_limit'
            elif age >= 33:
                assert got['retire'] == (sat < 0.5)
            else:
                assert not got['retire']
            if previous is not None:  # a lower satisfaction never retires less
                assert got['retire'] <= previous
            previous = got['retire']
            n += 1
        assert all(le.decide_retirement(age=age, playing_time_satisfaction=s / 10)['retire'] <= le.decide_retirement(age=age + 1, playing_time_satisfaction=s / 10)['retire'] for s in range(11))
    return {'grid_points': n}


def touched(node):
    return {s.id for s in ast.walk(node) if isinstance(s, ast.Name)} | {s.attr for s in ast.walk(node) if isinstance(s, ast.Attribute)} | \
        {s.value for s in ast.walk(node) if isinstance(s, ast.Constant) and isinstance(s.value, str)}


def check_static():
    t026.check_policy_blind()
    t027.check_leak_static()
    source = (CODE_ROOT / 'soccer_sim/league_economy.py').read_text()
    funcs = {n.name: n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.FunctionDef)}
    assert [a.arg for a in funcs['decide_retirement'].args.kwonlyargs] == ['age', 'playing_time_satisfaction'] and not funcs['decide_retirement'].args.args
    own_state = FORBIDDEN - {'playing_time_satisfaction'}  # the rule's second input is the player's own satisfaction value, passed in as an argument
    assert not touched(funcs['decide_retirement']) & own_state, touched(funcs['decide_retirement']) & own_state
    assert [a.arg for a in funcs['_new_coach_evaluation'].args.args] == ['day', 'ref']
    assert not touched(funcs['_new_coach_evaluation']) & (FORBIDDEN | TRUE_VALUE_WORDS), touched(funcs['_new_coach_evaluation'])
    words = {'generations', 'season_retired', 'season_youth_joined', 'decide_retirement', '_generational_turnover', 'YOUTH_INTAKE_AGE', 'CLUB_TARGET_ROSTER'}
    allowed = {'decide_retirement', '_generational_turnover', '_generation_fold', '__post_init__', 'to_dict', 'has_save_fields', '_validate_league_invariants',
               'advance_league_days', '_age_players', '_youth_truth', '_intake_age'}
    readers = {name for name, node in funcs.items() if touched(node) & words}
    assert readers <= allowed, sorted(readers - allowed)
    assert not any(name.startswith('decide_') and name != 'decide_retirement' or ('coach' in name and name != '_new_coach_evaluation') for name in readers)
    for module in t026.POLICY_MODULES + ('match_2d.py', 'selection_loop.py', 'transfer_loop.py'):
        text = (CODE_ROOT / 'soccer_sim' / module).read_text()
        assert not any(w in text for w in ('season_retired', 'season_youth_joined', 'retire_replace', 'decide_retirement')), module
    bad = ast.parse("def decide_coach_x(world):\n    return world.config.generations\n")
    assert touched(bad) & words == {'generations'} and {n.name for n in ast.walk(bad) if isinstance(n, ast.FunctionDef)} == {'decide_coach_x'}
    bad_eval = ast.parse("def _new_coach_evaluation(day, ref, world, pid):\n    return world.players[pid].traits\n")
    assert touched(bad_eval) & (FORBIDDEN | TRUE_VALUE_WORDS) >= {'traits', 'players', 'world'}
    return {'generation_readers': sorted(readers)}


# ---- gate 1 ----
def check_default_unchanged(root):
    base = t029.check_default_unchanged(root)
    curve = t026.check_default_unchanged(root)
    for name in t023.conditions():
        explicit = le._snapshot_payload_unchecked(le.run_league_world(seed=100, days=2 * L, config=replace(cfg(name, 29, 'off'), generations='none')))
        default = le._snapshot_payload_unchecked(le.run_league_world(seed=100, days=2 * L, config=cfg(name, 29, 'off')))
        assert explicit == default and 'generations' not in explicit['config'], name
        assert not any(e['kind'] in ('season_retired', 'season_youth_joined') for e in explicit['events'])
    return {**base, 'explicit_none_equals_default_aging_config': 14, 't026_default_check': bool(curve)}


# ---- verified, stepped run of one cell ----
def mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else None


def active_skill(world):
    return mean(world.players[pid].composite_skill() for pid in le._active_pids(world))


def run_cell(name, seed, start, mode, days, validate_every=1):
    """Advance day by day to `days`, re-deriving every retirement call and every newcomer at each season end."""
    config = cfg(name, start, mode)
    world = le._build_initial_world(seed=seed, config=config)
    calls, original = [], le.decide_retirement

    def recording(**kw):
        got = original(**kw)
        calls.append((kw['age'], kw['playing_time_satisfaction'], got))
        return got
    seasons, min_roster, newcomers = [], 3, {}
    le.decide_retirement = recording
    try:
        while world.day < days:
            before_active = le._active_pids(world)
            n_events = len(world.events)
            calls.clear()
            le.advance_league_days(world, 1)
            min_roster = min(min_roster, min(len(r) for r in world.club_rosters.values()))
            if world.day % L:
                continue
            season = world.day // L
            new = world.events[n_events:]
            retired = [e for e in new if e['kind'] == 'season_retired']
            joined = [e for e in new if e['kind'] == 'season_youth_joined']
            if mode == 'off':
                assert not calls and not retired and not joined
            else:
                # (4) one call per active player with its (new age, current satisfaction), result equal to the independent rule.
                survivors = [(world.player_ages[p], world.player_minds[p].playing_time_satisfaction) for p in le._active_pids(world) if p in before_active]
                seen = sorted((e['data']['age'], e['data']['playing_time_satisfaction']) for e in retired) + sorted(survivors)
                assert len(calls) == len(before_active) and sorted((a, s) for a, s, _ in calls) == sorted(seen), (name, seed, start, season)
                assert all(got == ind_retire(a, s) for a, s, got in calls)
                assert sum(1 for _, _, got in calls if got['retire']) == len(retired)
                for e in retired:
                    d = e['data']
                    assert set(d) == RETIRE_KEYS and d['reason'] == ind_retire(d['age'], d['playing_time_satisfaction'])['reason'] and d['season'] == season
                    assert e['actor'] == d['player_id'] and e['day'] == world.day and d['player_id'] not in world.player_ages
                    assert all(d['player_id'] not in r for r in world.club_rosters.values())
                assert all(a <= 35 for a in world.player_ages.values())
                # (5) newcomers and (6) club viability right after the boundary.
                expected_ids = [f'p{12 + len(newcomers) + i + 1}' for i in range(len(joined))]
                assert [e['data']['player_id'] for e in joined] == expected_ids, (name, seed, start, season)
                for e in joined:
                    d = e['data']
                    assert set(d) == JOIN_KEYS and d['age'] == 18 and d['reason'] == 'roster_below_target' and world.player_ages[d['player_id']] == 18
                    assert not TRUE_VALUE_WORDS & set(d)
                    check_youth_state(world, d['player_id'], e)
                    newcomers[d['player_id']] = {'club': d['club_id'], 'day': world.day, 'season': season,
                                                 'true_at_join': world.players[d['player_id']].composite_skill(),
                                                 'est_at_join': {c: m.evaluations[d['player_id']].skill_estimate for c, m in world.coaches.items()}}
                    assert all(m.evaluations[d['player_id']].skill_estimate == 0.5 and m.evaluations[d['player_id']].match_rating_estimate == 0.5
                               and m.evaluations[d['player_id']].fatigue_estimate == 0.10 and m.evaluations[d['player_id']].last_seen_day == world.day
                               for m in world.coaches.values())
                assert all(len(r) >= 3 for r in world.club_rosters.values()), (name, seed, start, season)
            if season % validate_every == 0 or world.day == days:
                le._validate_league_invariants(world)
            # P3 / description: newcomers that joined exactly one season ago.
            for pid, info in newcomers.items():
                if info['day'] == world.day - L:
                    coach = world.coaches[le.CLUB_COACH_MAP[info['club']]]
                    ev = coach.evaluations[pid]
                    info['observed_by_own_coach_after_a_season'] = ev.last_seen_day > info['day']
                    info['err_at_join'] = abs(0.5 - info['true_at_join'])
                    info['err_after_season'] = abs(ev.skill_estimate - world.players[pid].composite_skill())
            active = le._active_pids(world)
            ages = [world.player_ages[p] for p in active]
            seasons.append({'day': world.day, 'season': season, 'n_active': len(active), 'roster': {c: len(r) for c, r in world.club_rosters.items()},
                            'age_mean': mean(ages), 'age_sd': statistics.pstdev(ages), 'age_min': min(ages), 'age_max': max(ages),
                            'share_33_plus': mean(1.0 if a >= 33 else 0.0 for a in ages), 'skill': active_skill(world),
                            'retired': len(retired), 'retired_reasons': {r: sum(1 for e in retired if e['data']['reason'] == r) for r in ('age_limit', 'veteran_low_playing_time')},
                            'joined': len(joined), 'founders_active': sum(1 for p in active if int(p[1:]) <= 12)})
    finally:
        le.decide_retirement = original
    le._validate_league_invariants(world)
    matchdays = days // world.config.match_interval_days  # (6) every matchday: four lineups of two distinct starters, two completed matches
    lineups = [e for e in world.events if e['kind'] == 'lineup_decision']
    matches = [e for e in world.events if e['kind'] == 'league_match_completed']
    assert len(lineups) == 4 * matchdays and len(matches) == 2 * matchdays and all(len(set(e['data']['starters'])) == 2 for e in lineups)
    assert all(len(set(e['data']['home_starters'])) == 2 and len(set(e['data']['away_starters'])) == 2 for e in matches)
    return world, {'seasons': seasons, 'min_roster_any_day': min_roster, 'newcomers': newcomers, 'events': len(world.events), 'final': final_metrics(world)}


def final_metrics(world):
    active = le._active_pids(world)
    return {'skill': active_skill(world), 'satisfaction': mean(world.player_minds[p].playing_time_satisfaction for p in active),
            'trust': mean(v for p in active for v in world.player_minds[p].trust_by_coach.values()),
            'transfers': sum(1 for e in world.events if e['kind'] == 'transfer_completed')}


def starts_and_matchdays(world, newcomers):
    """Newcomer starts in their first season (research measurement from the match records)."""
    rows = []
    for pid, info in newcomers.items():
        if 'observed_by_own_coach_after_a_season' not in info:
            continue
        lo, hi = info['day'], info['day'] + L
        matches = [e for e in world.events if e['kind'] == 'league_match_completed' and lo < e['day'] <= hi]
        days = sorted({e['day'] for e in matches})
        started = sum(1 for e in matches if pid in e['data']['home_starters'] or pid in e['data']['away_starters'])
        rows.append({'player': pid, 'matchdays': len(days), 'starts': started})
    return rows


# ---- gate 7: isolation ----
def normalize(world_or_raw):
    raw = json.loads(json.dumps(le._snapshot_payload_unchecked(world_or_raw) if not isinstance(world_or_raw, dict) else world_or_raw))
    raw['config'].pop('generations', None)
    for e in raw['events']:
        if e['kind'] == 'season_skill_aged' and isinstance(e['data']['delta'], dict):
            values = set(e['data']['delta'].values())
            if len(values) == 1:  # uniform ages (before any turnover): the control's scalar form
                e['data']['delta'] = values.pop()
    return raw


def check_prefix(on_world, off_world):
    """Everything before the first retirement is identical to the control (after normalising the delta format)."""
    on, off = normalize(on_world)['events'], normalize(off_world)['events']
    k = next((i for i, e in enumerate(on) if e['kind'] in ('season_retired', 'season_youth_joined')), len(on))
    assert on[:k] == off[:k] and k > 0
    return k


def check_isolation_state():
    """Only rosters, ages, newcomers and events change at the boundary (everyone retires: start 35, first season end)."""
    name = 'open_solidarity_sampled_population'
    on = le.run_league_world(seed=100, days=L, config=cfg(name, 35, 'on'))
    off = le.run_league_world(seed=100, days=L, config=cfg(name, 35, 'off'))
    founders = le.PLAYER_IDS
    for pid in founders:
        assert on.players[pid].to_dict() == off.players[pid].to_dict()
        assert on.initial_players[pid].to_dict() == off.initial_players[pid].to_dict()
        assert on.player_minds[pid].to_dict() == off.player_minds[pid].to_dict()
        assert all(on.coaches[c].evaluations[pid].to_dict() == off.coaches[c].evaluations[pid].to_dict() for c in on.coaches)
    assert on.club_accounts == off.club_accounts and on.ledger == off.ledger and on.public_last_starters == off.public_last_starters
    assert all(on.club_rosters[c] != off.club_rosters[c] for c in CLUBS) and set(on.players) == set(f'p{i}' for i in range(1, 25))
    assert [e for e in normalize(on)['events'] if e['kind'] not in ('season_retired', 'season_youth_joined')] == normalize(off)['events']
    return {'founders_unchanged': True, 'new_players': 12}


# ---- gate 8b: the coach's first impression does not depend on the newcomer's true value ----
def check_leak_dynamic():
    name = 'open_solidarity'
    worlds = {}
    for label, perturbation in (('low', 'trait_cm'), ('high', 'trait_cp')):
        config = replace(cfg(name, 35, 'on'), range_perturbation=perturbation)
        worlds[label] = le.run_league_world(seed=100, days=L, config=config)
    low, high = worlds['low'], worlds['high']
    joined = [e['data']['player_id'] for e in low.events if e['kind'] == 'season_youth_joined']
    assert joined == [e['data']['player_id'] for e in high.events if e['kind'] == 'season_youth_joined'] and len(joined) == 12
    pre = lambda w: [e for e in w.events if e['kind'] not in ('season_youth_joined',) and e['day'] < L]
    assert pre(low) == pre(high)  # the same history up to the boundary
    gap = mean(high.players[p].composite_skill() - low.players[p].composite_skill() for p in joined)
    assert gap > 0.05, gap  # the true skills really differ
    for w in worlds.values():
        for pid in joined:
            for coach in w.coaches.values():
                e = coach.evaluations[pid]
                assert (e.skill_estimate, e.match_rating_estimate, e.fatigue_estimate, e.last_seen_day, e.method_residuals) == (0.5, 0.5, 0.10, L, {'A': 0.0, 'B': 0.0})
    return {'newcomers': 12, 'true_skill_gap_high_minus_low': gap, 'impressions_equal_constant': True}


# ---- gate 9 ----
def check_persistence(directory):
    out = {}
    for label, start, days in (('S29', 29, DAYS), ('S33', 33, DAYS), ('S35_all_retire', 35, 2 * L)):
        done = 0
        for name in t023.conditions():
            world = le.run_league_world(seed=100, days=days, config=cfg(name, start, 'on'))
            folder = Path(directory) / f'{label}_{name}'
            le.save_league_artifacts(world, folder)
            for reader in (le.load_league_snapshot, le.load_league_snapshot_trusted):
                le._assert_same(reader(folder / 'snapshot.json'), world, 'world')
            done += sum(1 for e in world.events if e['kind'] == 'season_retired')
        out[label] = done
    return out


def check_branch_and_steps(directory):
    config = cfg(t023.conditions()[0], 33, 'on')
    parent = le.run_league_world(seed=100, days=30, config=config)
    child = le.fork_league_world(parent, kind='player_trust', player_id='p3', coach_id='c1', value=0.05, reason='t030')
    le.advance_league_days(child, 60)
    le.save_league_artifacts(child, directory)
    le._assert_same(le.load_league_snapshot(Path(directory) / 'snapshot.json'), child, 'world')
    stepped = le._build_initial_world(seed=100, config=config)
    for _ in range(4):
        le.advance_league_days(stepped, L)
    le.advance_league_days(stepped, 6)
    le._assert_same(stepped, le.run_league_world(seed=100, days=90, config=config), 'world')
    return {'interventions': len(child.interventions), 'retired_in_branch': sum(1 for e in child.events if e['kind'] == 'season_retired'), 'stepped_equals_oneshot': True}


def check_tamper(directory):
    world = le.run_league_world(seed=100, days=3 * L, config=cfg('open_solidarity_sampled_population', 35, 'on'))
    first = lambda r, kind: next(i for i, e in enumerate(r['events']) if e['kind'] == kind)
    cases = {
        'age_value_changed': lambda r: r['player_ages'].__setitem__('p13', r['player_ages']['p13'] + 1),
        'retired_event_removed': lambda r: r['events'].pop(first(r, 'season_retired')),
        'retired_reason_changed': lambda r: r['events'][first(r, 'season_retired')]['data'].__setitem__('reason', 'veteran_low_playing_time'),
        'newcomer_initial_trait_changed': lambda r: r['initial_players']['p13']['traits'].__setitem__('pace', r['initial_players']['p13']['traits']['pace'] + 0.01),
        'joined_age_changed': lambda r: r['events'][first(r, 'season_youth_joined')]['data'].__setitem__('age', 19),
        'joined_roster_size_changed': lambda r: r['events'][first(r, 'season_youth_joined')]['data'].__setitem__('roster_size_before', 2)}
    result = {}
    for label, mutate in cases.items():
        folder = Path(directory) / label
        le.save_league_artifacts(world, folder)
        snap, manifest = folder / 'snapshot.json', folder / 'manifest.json'
        raw = json.loads(snap.read_text())
        mutate(raw)
        snap.write_text(json.dumps(raw))
        record = json.loads(manifest.read_text())
        record['snapshot_sha256'] = digest(raw)  # forged manifest
        manifest.write_text(json.dumps(record))
        for reader in (le.load_league_snapshot, le.load_league_snapshot_trusted):
            try:
                reader(snap)
                result[f'{label}/{reader.__name__}'] = 'accepted'
            except ValueError:
                result[f'{label}/{reader.__name__}'] = 'rejected'
    assert all(v == 'rejected' for v in result.values()), result
    return result


# ---- analysis ----
def sign_test(diffs):
    nonzero = [d for d in diffs if d != 0]
    pos, n = sum(1 for d in nonzero if d > 0), len(nonzero)
    p = min(1.0, 2 * sum(comb(n, k) for k in range(0, min(pos, n - pos) + 1)) / 2 ** n) if n else 1.0
    return {'positive': pos, 'negative': n - pos, 'ties': len(diffs) - n, 'p_two_sided': p}


def analyse(main, long_cells, start_rows):
    out = {}
    for start in (29, 33):
        pairs = [(c['on'], c['off']) for (n, s, st), c in main.items() if st == start]
        diffs = [on['final']['skill'] - off['final']['skill'] for on, off in pairs]
        out[f'P2_skill_S{start}'] = {'cells': len(diffs), 'positive': sum(1 for d in diffs if d > 0), 'mean_diff': mean(diffs), 'sign_test': sign_test(diffs),
                                     'supported': sum(1 for d in diffs if d > 0) >= 36}
    out['P2_min_skill_over_all_on_cells'] = min(s['skill'] for c in list(main.values()) for s in c['on']['seasons']) if main else None
    out['P2_min_skill_over_long_cells'] = min(s['skill'] for c in long_cells.values() for s in c['seasons'])
    out['P2_collapse_any'] = out['P2_min_skill_over_all_on_cells'] < 0.30 or out['P2_min_skill_over_long_cells'] < 0.30
    sds = [statistics.pstdev([s['age_mean'] for s in c['seasons'] if 21 <= s['season'] <= 30]) for c in long_cells.values()]
    out['P1_age_instability_long'] = {'cells': len(sds), 'cells_sd_gt_2_5': sum(1 for x in sds if x > 2.5), 'median_sd': statistics.median(sds),
                                      'supported': sum(1 for x in sds if x > 2.5) >= 21}
    full = [i for c in main.values() for i in c['on']['newcomers'].values() if 'observed_by_own_coach_after_a_season' in i]
    full += [i for c in long_cells.values() for i in c['newcomers'].values() if 'observed_by_own_coach_after_a_season' in i]
    share = mean(1.0 if i['observed_by_own_coach_after_a_season'] else 0.0 for i in full)
    out['P3_newcomers_observed'] = {'newcomers_with_a_full_season': len(full), 'share_observed': share, 'supported': share >= 0.90}
    out['descriptive'] = {
        'newcomer_error_at_join': mean(i['err_at_join'] for i in full), 'newcomer_error_after_first_season': mean(i['err_after_season'] for i in full),
        'newcomer_first_season_start_rate': mean(r['starts'] / r['matchdays'] for r in start_rows if r['matchdays']),
        'newcomers_never_started_share': mean(1.0 if r['starts'] == 0 else 0.0 for r in start_rows),
        'min_roster_any_day_over_all_on_cells': min(min(c['on']['min_roster_any_day'] for c in main.values()), min(c['min_roster_any_day'] for c in long_cells.values()))}
    for start in STARTS:
        rows = [c['on']['seasons'] for (n, s, st), c in main.items() if st == start]
        out['descriptive'][f'S{start}_by_season'] = [{
            'season': k + 1, 'age_mean': mean(r[k]['age_mean'] for r in rows), 'age_max': max(r[k]['age_max'] for r in rows), 'share_33_plus': mean(r[k]['share_33_plus'] for r in rows),
            'skill_on': mean(r[k]['skill'] for r in rows), 'skill_off': mean(c['off']['seasons'][k]['skill'] for (n, s, st), c in main.items() if st == start),
            'retired': mean(r[k]['retired'] for r in rows), 'joined': mean(r[k]['joined'] for r in rows), 'founders_active': mean(r[k]['founders_active'] for r in rows)}
            for k in range(8)]
        out['descriptive'][f'S{start}_final_on_minus_off'] = {
            m: mean(c['on']['final'][m] - c['off']['final'][m] for (n, s, st), c in main.items() if st == start) for m in ('skill', 'satisfaction', 'trust', 'transfers')}
        out['descriptive'][f'S{start}_events_on_minus_off'] = mean(c['on']['events'] - c['off']['events'] for (n, s, st), c in main.items() if st == start)
    rows = [c['seasons'] for c in long_cells.values()]
    out['descriptive']['long_by_season'] = [{'season': k + 1, 'age_mean': mean(r[k]['age_mean'] for r in rows), 'age_sd': mean(r[k]['age_sd'] for r in rows),
                                             'age_max': max(r[k]['age_max'] for r in rows), 'share_33_plus': mean(r[k]['share_33_plus'] for r in rows),
                                             'skill': mean(r[k]['skill'] for r in rows), 'retired': mean(r[k]['retired'] for r in rows), 'joined': mean(r[k]['joined'] for r in rows),
                                             'founders_active': mean(r[k]['founders_active'] for r in rows)} for k in range(30)]
    for start in STARTS:
        rows = [c['on'] for (n, s, st), c in main.items() if st == start]
        out['descriptive'][f'S{start}_on'] = {
            'n_active_at_season_8_mean': mean(r['seasons'][-1]['n_active'] for r in rows), 'n_active_at_season_8_min_max': [min(r['seasons'][-1]['n_active'] for r in rows), max(r['seasons'][-1]['n_active'] for r in rows)],
            'max_roster_at_any_season_end': max(max(s['roster'].values()) for r in rows for s in r['seasons']),
            'min_roster_at_any_season_end': min(min(s['roster'].values()) for r in rows for s in r['seasons']),
            'cells_with_a_club_of_2_on_some_day': sum(1 for r in rows if r['min_roster_any_day'] == 2), 'cells': len(rows),
            'retired_total': sum(s['retired'] for r in rows for s in r['seasons']), 'joined_total': sum(s['joined'] for r in rows for s in r['seasons']),
            'retired_reasons': {k: sum(s['retired_reasons'][k] for r in rows for s in r['seasons']) for k in ('age_limit', 'veteran_low_playing_time')}}
    out['descriptive']['long_on'] = {
        'n_active_at_season_30_min_max': [min(c['seasons'][-1]['n_active'] for c in long_cells.values()), max(c['seasons'][-1]['n_active'] for c in long_cells.values())],
        'min_roster_at_any_season_end': min(min(s['roster'].values()) for c in long_cells.values() for s in c['seasons']),
        'cells_with_a_club_of_2_on_some_day': sum(1 for c in long_cells.values() if c['min_roster_any_day'] == 2),
        'retired_total': sum(s['retired'] for c in long_cells.values() for s in c['seasons']), 'joined_total': sum(s['joined'] for c in long_cells.values() for s in c['seasons']),
        'sd_of_mean_age_seasons_21_30_min_median_max': [min(sds), statistics.median(sds), max(sds)],
        'mean_age_by_season_21_30': [mean(c['seasons'][k]['age_mean'] for c in long_cells.values()) for k in range(20, 30)]}
    reasons = {'age_limit': 0, 'veteran_low_playing_time': 0}
    for c in long_cells.values():
        for s in c['seasons']:
            for r, v in s['retired_reasons'].items():
                reasons[r] += v
    out['descriptive']['long_retirement_reasons'] = reasons
    return out


def run(out, root):
    out.mkdir(parents=True, exist_ok=False)
    start = perf_counter()
    default = check_default_unchanged(root)
    regression = t019.check_regression(root)
    print('default', default, regression, flush=True)
    invalid, pure, static = check_invalid(), check_pure(), check_static()
    print('invalid', invalid, 'pure', pure, 'static', static, flush=True)
    iso = check_isolation_state()
    leak = check_leak_dynamic()
    print('isolation', iso, 'leak', leak, flush=True)
    main, start_rows, prefix, equal_s18, intake_s18 = {}, [], {}, 0, 0
    for name in t023.conditions():
        for seed in SEEDS:
            for st in STARTS:
                off_world, off = run_cell(name, seed, st, 'off', DAYS)
                on_world, on = run_cell(name, seed, st, 'on', DAYS)
                prefix[(name, seed, st)] = check_prefix(on_world, off_world)
                if st == 18:  # nobody reaches 33 in eight seasons, so no retirement; an intake can still follow a transfer (補正A1)
                    assert not any(e['kind'] == 'season_retired' for e in on_world.events)
                    if not any(e['kind'] == 'season_youth_joined' for e in on_world.events):
                        assert normalize(on_world) == normalize(off_world) and le.summarize_league_world(on_world) == le.summarize_league_world(off_world)
                        equal_s18 += 1
                    else:
                        intake_s18 += 1
                start_rows += starts_and_matchdays(on_world, on['newcomers'])
                main[(name, seed, st)] = {'off': off, 'on': on}
        print('main', name, flush=True)
    long_cells = {}
    for name in t023.conditions():
        for seed in SEEDS:
            world, cell = run_cell(name, seed, 29, 'on', LONG_DAYS, validate_every=5)
            start_rows += starts_and_matchdays(world, cell['newcomers'])
            long_cells[(name, seed)] = cell
        print('long', name, flush=True)
    analysis = analyse(main, long_cells, start_rows)
    print('analysis', json.dumps({k: v for k, v in analysis.items() if k.startswith('P') or k == 'P2_collapse_any'}), flush=True)
    with tempfile.TemporaryDirectory() as tmp:
        persisted = check_persistence(Path(tmp) / 'p')
        branch = check_branch_and_steps(Path(tmp) / 'branch')
        tamper = check_tamper(Path(tmp) / 'tamper')
    print('persistence', persisted, branch, tamper, flush=True)
    table = [{'world': n, 'seed': s, 'start_age': st, **{m: {k: v for k, v in c[m].items() if k != 'newcomers'} for m in c}} for (n, s, st), c in main.items()]
    long_table = [{'world': n, 'seed': s, **{k: v for k, v in c.items() if k != 'newcomers'}} for (n, s), c in long_cells.items()]
    result = {'task': 'T030', 'identity': le.league_identity(), 'runtime': runtime(), 'seeds': list(SEEDS), 'season_length_days': L, 'days': DAYS, 'long_days': LONG_DAYS,
              'default_unchanged': default, 'default_vs_t018': regression, 'invalid_rejected': invalid, 'pure_function': pure, 'static': static, 'isolation_state': iso,
              'leak_dynamic': leak, 'main_cells': len(main), 'long_cells': len(long_cells), 'cells_with_prefix_equal_to_control': len(prefix),
              'first_turnover_event_index_min_max': [min(prefix.values()), max(prefix.values())], 'start18_identical_to_control_cells': equal_s18, 'start18_cells_with_transfer_caused_intake': intake_s18,
              'persistence_retirements': persisted, 'branch': branch, 'tamper': tamper, 'analysis': analysis, 'cells': table, 'long': long_table,
              'newcomers_total': sum(len(c['on']['newcomers']) for c in main.values()) + sum(len(c['newcomers']) for c in long_cells.values()),
              'elapsed_seconds': perf_counter() - start}
    write_json(out / 'summary.json', result)
    print(json.dumps({'cells': len(main), 'long': len(long_cells), 'elapsed_seconds': result['elapsed_seconds']}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    run(parser.parse_args().out.resolve(), CODE_ROOT)
