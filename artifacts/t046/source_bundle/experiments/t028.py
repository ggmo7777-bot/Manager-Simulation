"""T028: advice response (comply / partial / refuse) decided from the player's own state and latent traits, with a recorded reason.

Protocol: docs/experiments/T028-PROTOCOL.md. 14 worlds x seeds {100,101,102} = 42 cells, 168 days, 84-day seasons; conditions off / on / mid.
"""
import argparse
import ast
from contextlib import contextmanager
from dataclasses import replace
import itertools
import json
from math import comb
from pathlib import Path
import statistics
import sys
import tempfile
from time import perf_counter

CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT))
from experiments import t019, t023, t027
from soccer_sim import league_economy as le
from soccer_sim.storage import digest, runtime, write_json

DAYS, SEASON, SEEDS = 168, 84, (100, 101, 102)
OFF, ON = 'latent_v1', 'personality_v1'
TRAITS = t027.TRAITS
COACH_KEYS = {'record_class', 'coach_id', 'player_id', 'method', 'response', 'stated_reason', 'action_followed'}
RESPONSE_KEYS = {'player_id', 'record_class', 'response', 'score', 'terms', 'reason_codes', 'primary_reason', 'coach_id', 'method'}
LOW_CUT, HIGH_CUT = 0.15 + 0.7 / 3, 0.15 + 1.4 / 3
# Allowed readers of the latent traits = T027's list (which now includes the one T028 reader) -- nothing else, notably no decide_* function.


def cfg(name, mode='off', **kw):
    base = {'personality': 'latent_v1', 'season_length_days': SEASON, 'advice_response': ON if mode != 'off' else 'none'}
    return replace(le.config_for_condition('league_' + name), **{**base, **kw})


# ---- independent re-implementation of the pre-registered rule (literal numbers, not imported) ----
def independent(trust, autonomy, discipline, stability, satisfaction):
    dissatisfaction = max(0.0, 0.48 - satisfaction)
    terms = {'trust_minus_autonomy': round(trust - autonomy, 6), 'discipline_term': round(0.25 * (discipline - 0.5), 6),
             'irritation_term': round(-0.40 * (1.0 - stability) * dissatisfaction, 6)}
    score = round(sum(terms.values()), 6)
    response = 'complied' if score >= 0.0 else ('partial' if score >= -0.20 else 'refused')
    names = {'trust_minus_autonomy': ('trust_above_autonomy', 'trust_below_autonomy'), 'discipline_term': ('self_disciplined', 'self_lax'),
             'irritation_term': (None, 'irritated_by_low_playing_time')}
    listed = sorted([(n, v) for n, v in terms.items() if abs(v) >= 0.02], key=lambda t: (-abs(t[1]), t[0]))
    codes = [names[n][0 if v > 0 else 1] for n, v in listed]
    return {'response': response, 'score': score, 'terms': terms, 'reason_codes': codes, 'primary_reason': codes[0] if codes else 'no_dominant_term'}


def decide(trust, autonomy, discipline, stability, satisfaction):
    return le.decide_advice_response(trust=trust, autonomy=autonomy, discipline=discipline, emotional_stability=stability,
                                     playing_time_satisfaction=satisfaction)


def check_pure():
    trusts, autonomies = (0.05, 0.3, 0.56, 0.95), (0.32, 0.38)
    traits, sats = (0.15, 0.5, 0.85), (0.05, 0.3, 0.48, 0.7)
    n = 0
    for t, a, d, e, s in itertools.product(trusts, autonomies, traits, traits, sats):
        got = decide(t, a, d, e, s)
        assert got == independent(t, a, d, e, s) == decide(t, a, d, e, s), (t, a, d, e, s)
        assert got['score'] == round(sum(got['terms'].values()), 6)
        n += 1
    fine = [i / 20 for i in range(1, 20)]
    for t, d, e, s in itertools.product(fine, (0.15, 0.4, 0.6, 0.85), (0.15, 0.5, 0.85), (0.1, 0.3, 0.45)):
        assert decide(t, 0.34, d, e, s) == independent(t, 0.34, d, e, s)
        n += 1
        assert decide(min(1, t + 0.05), 0.34, d, e, s)['score'] >= decide(t, 0.34, d, e, s)['score'] - 1e-6   # trust
        assert decide(t, 0.34, min(.85, d + .05), e, s)['score'] >= decide(t, 0.34, d, e, s)['score'] - 1e-6   # discipline
        assert decide(t, 0.34, d, min(.85, e + .05), s)['score'] >= decide(t, 0.34, d, e, s)['score'] - 1e-6   # stability
        assert decide(t, 0.34, d, e, s)['score'] >= decide(t, 0.34, d, e, max(0.0, s - 0.05))['score'] - 1e-6 or e == 1  # less satisfied -> lower
    # threshold boundaries (autonomy chosen so that score hits the boundary exactly, discipline 0.5, no dissatisfaction)
    edge = lambda margin: decide(0.5 + margin, 0.5, 0.5, 0.5, 0.9)['response']
    assert (edge(0.0), edge(-0.20), edge(-0.200001)) == ('complied', 'partial', 'refused')
    quiet = decide(0.5, 0.5, 0.5, 0.5, 0.9)
    assert quiet['reason_codes'] == [] and quiet['primary_reason'] == 'no_dominant_term'
    return {'grid_points': n}


def check_invalid():
    ok = {}
    for label, value in {'unknown': 'bogus', 'int': 1, 'empty': ''}.items():
        try:
            cfg(t023.conditions()[0], advice_response=value)
        except ValueError:
            ok[label] = True
        else:
            ok[label] = False
    try:
        replace(le.config_for_condition('league_' + t023.conditions()[0]), advice_response=ON)
    except ValueError:
        ok['without_personality'] = True
    else:
        ok['without_personality'] = False
    cls = le.LeagueEconomyConfig
    on, off = cfg(t023.conditions()[0], 'on').to_dict(), cfg(t023.conditions()[0]).to_dict()
    assert cls.has_save_fields(on) and cls.has_save_fields(off) and 'advice_response' not in off and on['advice_response'] == ON
    assert not cls.has_save_fields({**on, 'unknown': 1})
    return ok


def check_default_unchanged(root):
    base = t027.check_default_unchanged(root)
    mind = next(iter(le.run_league_world(seed=100, days=3, config=le.config_for_condition('league_' + t023.conditions()[0])).player_minds.values()))
    proposals = [{'coach_id': 'c1', 'method': 'A', 'forecast': 0.05, 'decision_ref': 'e1'}, {'coach_id': 'c2', 'method': 'B', 'forecast': 0.04, 'decision_ref': 'e2'}]
    config = le.config_for_condition('league_' + t023.conditions()[0])
    implicit = le.decide_player_training(mind=mind, club_id='x', proposals=proposals, config=config)
    explicit = le.decide_player_training(mind=mind, club_id='x', proposals=proposals, config=config, advice_mode='blend')
    assert implicit == explicit and 'advice_mode' not in implicit
    return {**base, 'advice_mode_default_identical': True}


# ---- verifying wrappers: every response and every decision in a run is re-derived independently ----
@contextmanager
def verified(rows):
    original_reply, original_train = le._player_advice_response, le.decide_player_training

    def reply(world, pid, props):
        got = original_reply(world, pid, props)
        mind = world.player_minds[pid]
        top = max(props, key=lambda p: (mind.trust_by_coach.get(str(p['coach_id']), 0.5), str(p['coach_id'])))
        traits = world.player_personality[pid]
        args = (mind.trust_by_coach[str(top['coach_id'])], mind.autonomy, traits['discipline'], traits['emotional_stability'], mind.playing_time_satisfaction)
        expected = independent(*args)
        assert {k: got[k] for k in expected} == expected and got['coach_id'] == str(top['coach_id']) and got['method'] == str(top['method']), (pid, got, expected)
        rows.append({'day': world.day, 'player': pid, 'trust': args[0], 'autonomy': args[1], 'discipline': args[2], 'stability': args[3],
                     'satisfaction': args[4], 'dissatisfaction': max(0.0, 0.48 - args[4]), 'response': got['response'],
                     'primary': got['primary_reason'], 'score': got['score']})
        return got

    def train(**kw):
        got = original_train(**kw)
        mode = kw.get('advice_mode', 'blend')
        if mode == 'ignore':
            ref = original_train(**{**kw, 'proposals': [], 'advice_mode': 'blend'})
            assert got['candidates'] == ref['candidates'] and got['chosen'] == ref['chosen'] and all(c['score_source'] is None for c in got['candidates'])
        elif mode == 'follow':
            ref = original_train(**{**kw, 'advice_mode': 'blend'})
            top = max(kw['proposals'], key=lambda p: (kw['mind'].trust_by_coach.get(str(p['coach_id']), 0.5), str(p['coach_id'])))
            assert got['candidates'] == ref['candidates'] and got['chosen']['action'] == top['method']
        else:
            assert 'advice_mode' not in got
        return got

    le._player_advice_response, le.decide_player_training = reply, train
    try:
        yield
    finally:
        le._player_advice_response, le.decide_player_training = original_reply, original_train


def check_events(world, rows):
    """Gate 4: order, schema and consistency of the two event kinds, against the training decisions."""
    events, n = world.events, 0
    for i, ev in enumerate(events):
        if ev['kind'] == 'player_training_decision':
            has_props = bool(ev['data']['received_proposals'])
            before = events[i - 1] if i else None
            answered = bool(before) and before['kind'] == 'player_advice_response' and before['actor'] == ev['actor']
            assert answered == (has_props and world.config.advice_response != 'none'), (ev['id'], has_props)
            if answered:
                n += 1
                reply, after = before['data'], events[i + 1]
                assert set(reply) == RESPONSE_KEYS and reply['record_class'] == 'player_reason'
                mode = {'complied': 'follow', 'partial': None, 'refused': 'ignore'}[reply['response']]
                assert ev['data'].get('advice_mode') == mode and ev['cause_refs'][-1] == before['id']
                if reply['response'] == 'refused':
                    assert all(c['components'].get('advice_term', 0.0) == 0.0 and c['score_source'] is None for c in ev['data']['candidates'])
                if reply['response'] == 'complied':
                    assert ev['data']['chosen']['action'] == reply['method']
                assert after['kind'] == 'coach_advice_outcome' and after['actor'] == reply['coach_id'] and after['cause_refs'] == [ev['id']]
                out = after['data']
                assert set(out) == COACH_KEYS and out['record_class'] == 'coach_observation' and out['response'] == reply['response']
                assert out['stated_reason'] in ('trusts_coach', 'distrusts_coach', 'personal') and out['method'] == reply['method']
                assert out['action_followed'] == (ev['data']['chosen']['action'] == reply['method'])
                assert out['stated_reason'] == {'trust_above_autonomy': 'trusts_coach', 'trust_below_autonomy': 'distrusts_coach'}.get(reply['primary_reason'], 'personal')
    kinds = [e['kind'] for e in events]
    assert kinds.count('player_advice_response') == kinds.count('coach_advice_outcome') == n == len(rows)
    return n


def check_coach_events_blind(world):
    for ev in world.events:
        if ev['kind'] == 'coach_advice_outcome':
            data = ev['data']
            assert set(data) == COACH_KEYS and not any(type(v) is float for v in data.values()), ev
            assert not ({'terms', 'score', 'reason_codes', 'primary_reason', 'discipline', 'emotional_stability'} & set(data))
    return True


def check_leak_static():
    source = (CODE_ROOT / 'soccer_sim/league_economy.py').read_text()
    readers = t027.personality_readers(source)
    assert readers <= t027.ALLOWED_READERS and '_player_advice_response' in readers, sorted(readers - t027.ALLOWED_READERS)
    tree = ast.parse(source)
    funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    deciders = [name for name in funcs if name.startswith('decide_')]
    assert 'decide_advice_response' in deciders and not readers & set(deciders)
    args = [a.arg for a in funcs['decide_advice_response'].args.kwonlyargs + funcs['decide_advice_response'].args.args]
    assert 'world' not in args and 'mind' not in args and set(args) == {'trust', 'autonomy', 'discipline', 'emotional_stability', 'playing_time_satisfaction'}
    coach_funcs = [name for name in funcs if 'coach' in name]
    assert not readers & set(coach_funcs)
    # Sensitivity: a coach decision that reads a trait is flagged by the same checker.
    assert t027.personality_readers('def decide_coach_x(world, pid):\n    return world.player_personality[pid]\n') == {'decide_coach_x'}
    outside = [p.name for p in (CODE_ROOT / 'soccer_sim').glob('*.py') if p.name != 'league_economy.py' and any(w in p.read_text() for w in t027.WORDS)]
    assert not outside, outside
    return {'readers': sorted(readers), 'decide_functions_checked': len(deciders), 'coach_functions_checked': len(coach_funcs), 'other_modules_clean': True}


def snapshot(world):
    raw = le._snapshot_payload_unchecked(world)
    raw.pop('player_personality', None)
    # The player's own reason record legitimately carries trait-derived numbers (a 1e-9 shift can flip a 6th decimal); compare it by response/codes only.
    for ev in raw['events']:
        if ev['kind'] == 'player_advice_response':
            ev['data'] = {k: v for k, v in ev['data'].items() if k not in ('terms', 'score')}
    return raw


def responses(world):
    return [(e['id'], e['data']['response']) for e in world.events if e['kind'] == 'player_advice_response']


def build(name, seed, mode):
    world = le._build_initial_world(seed=seed, config=cfg(name, mode))
    if mode == 'mid':
        world.player_personality = {pid: {t: 0.5 for t in TRAITS} for pid in world.players}
    return world


def check_leak_dynamic(name):
    runs = {}
    for label, change in (('generated', None), ('eps', 1e-9), ('low', 0.15), ('high', 0.85)):
        world = build(name, 100, 'on')
        if change is not None:
            world.player_personality = {pid: {t: (v + change if label == 'eps' else change) for t, v in row.items()}
                                        for pid, row in world.player_personality.items()}
        le.advance_league_days(world, DAYS)
        runs[label] = world
    base = runs['generated']
    assert responses(runs['eps']) == responses(base) and snapshot(runs['eps']) == snapshot(base), name  # same responses -> identical history, coaches included
    coach_part = lambda w: ({k: v for k, v in le._snapshot_payload_unchecked(w).items() if k == 'coaches'},
                            [e for e in w.events if e['kind'] == 'coach_advice_outcome'])
    assert coach_part(runs['eps']) == coach_part(base)
    return {'responses': len(responses(base)), 'low_differs': responses(runs['low']) != responses(base), 'high_differs': responses(runs['high']) != responses(base)}


def mean(xs):
    return statistics.fmean(xs)


def metrics(world):
    minds, players = world.player_minds, world.players
    return {'skill': mean(p.composite_skill() for p in players.values()),
            'satisfaction': mean(m.playing_time_satisfaction for m in minds.values()),
            'trust': mean(v for m in minds.values() for v in m.trust_by_coach.values()),
            'transfers': sum(1 for e in world.events if e['kind'] == 'transfer_completed'), 'events': len(world.events)}


def run_cell(name, seed, mode):
    rows = []
    world = build(name, seed, mode)
    initial = {pid: p.composite_skill() for pid, p in world.players.items()}
    out = {}
    with verified(rows) if mode != 'off' else nullcontext():
        for day in (SEASON, DAYS):
            le.advance_league_days(world, day - world.day)
            out[day] = metrics(world)
    if mode != 'off':
        check_events(world, rows)
        check_coach_events_blind(world)
    traits = world.player_personality
    final = {pid: p.composite_skill() - initial[pid] for pid, p in world.players.items()}
    sat = {pid: m.playing_time_satisfaction for pid, m in world.player_minds.items()}
    return world, out, rows, {'gain': final, 'satisfaction': sat, 'traits': traits}


@contextmanager
def nullcontext():
    yield


def ranks(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r, i = [0.0] * len(xs), 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def spearman(xs, ys):
    return statistics.correlation(ranks(xs), ranks(ys)) if len(set(xs)) > 1 and len(set(ys)) > 1 else None


def sign_test(diffs):
    pos, neg = sum(d > 0 for d in diffs), sum(d < 0 for d in diffs)
    n = pos + neg
    p = min(1.0, 2 * sum(comb(n, k) for k in range(min(pos, neg) + 1)) / 2 ** n) if n else 1.0
    return {'mean': mean(diffs), 'positive': pos, 'negative': neg, 'zero': len(diffs) - n, 'p_two_sided': p}


def tercile(value):
    return 'low' if value < LOW_CUT else 'high' if value > HIGH_CUT else 'mid'


def share(rows, key):
    n = len(rows)
    return {r: (sum(1 for x in rows if x[key] == r) / n if n else None) for r in ('complied', 'partial', 'refused')} | {'n': n}


def counterfactual_and_pairs(rows_by_cell):
    """Gate 6: traits really enter the decision. (a) re-decide each recorded input with traits swapped to 0.15 / 0.85; (b) same-state pairs with different responses."""
    flips, total, pairs = 0, 0, 0
    for rows in rows_by_cell.values():
        for r in rows:
            low = decide(r['trust'], r['autonomy'], 0.15, 0.15, r['satisfaction'])['response']
            high = decide(r['trust'], r['autonomy'], 0.85, 0.85, r['satisfaction'])['response']
            total += 1
            flips += low != high
        sample = rows[::3]
        for a, b in itertools.combinations(sample, 2):
            if abs(a['trust'] - a['autonomy'] - (b['trust'] - b['autonomy'])) < 0.01 and abs(a['dissatisfaction'] - b['dissatisfaction']) < 0.01 \
                    and (a['discipline'], a['stability']) != (b['discipline'], b['stability']) and a['response'] != b['response']:
                pairs += 1
    assert flips > 0 and pairs > 0, (flips, pairs)
    return {'decisions': total, 'would_change_under_low_vs_high_traits': flips, 'same_state_pairs_with_different_responses': pairs}


def check_persistence(name, directory):
    world = le.run_league_world(seed=100, days=DAYS, config=cfg(name, 'on'))
    le.save_league_artifacts(world, directory)
    for reader in (le.load_league_snapshot, le.load_league_snapshot_trusted):
        le._assert_same(reader(Path(directory) / 'snapshot.json'), world, 'world')
    return len(responses(world))


def check_branch_and_steps(directory):
    config = cfg(t023.conditions()[0], 'on')
    parent = le.run_league_world(seed=100, days=30, config=config)
    child = le.fork_league_world(parent, kind='player_trust', player_id='p3', coach_id='c1', value=0.05, reason='t028')
    le.advance_league_days(child, 60)
    le.save_league_artifacts(child, directory)
    le._assert_same(le.load_league_snapshot(Path(directory) / 'snapshot.json'), child, 'world')
    stepped = le._build_initial_world(seed=100, config=config)
    le.advance_league_days(stepped, 30)
    le.advance_league_days(stepped, 60)
    le._assert_same(stepped, le.run_league_world(seed=100, days=90, config=config), 'world')
    return {'interventions': len(child.interventions), 'responses': len(responses(child)), 'stepped_equals_oneshot': True}


def check_combined(names):
    done = 0
    for name in names:
        config = cfg(name, 'on', aging_start_age=29, aging_curve='hypothesis_v1')
        config = replace(config, season_length_days=42)
        world = le.run_league_world(seed=100, days=126, config=config)
        with tempfile.TemporaryDirectory() as tmp:
            le.save_league_artifacts(world, Path(tmp) / 'w')
            for reader in (le.load_league_snapshot, le.load_league_snapshot_trusted):
                le._assert_same(reader(Path(tmp) / 'w/snapshot.json'), world, 'world')
        done += 1
    return done


def check_tamper(directory):
    world = le.run_league_world(seed=100, days=60, config=cfg('open_solidarity' + le.POPULATION_SUFFIX, 'on'))
    index = next(i for i, e in enumerate(world.events) if e['kind'] == 'player_advice_response')
    cases = {'response_changed': lambda r: r['events'][index]['data'].__setitem__('response', 'refused' if r['events'][index]['data']['response'] != 'refused' else 'complied'),
             'outcome_removed': lambda r: r['events'].pop(next(i for i, e in enumerate(r['events']) if e['kind'] == 'coach_advice_outcome'))}
    result = {}
    for label, mutate in cases.items():
        folder = Path(directory) / label
        le.save_league_artifacts(world, folder)
        snap, manifest = folder / 'snapshot.json', folder / 'manifest.json'
        raw = json.loads(snap.read_text())
        mutate(raw)
        snap.write_text(json.dumps(raw))
        record = json.loads(manifest.read_text())
        record['snapshot_sha256'] = digest(raw)  # forged manifest: only the full replay can stop it
        manifest.write_text(json.dumps(record))
        for reader in (le.load_league_snapshot, le.load_league_snapshot_trusted):
            try:
                reader(snap)
                result[f'{label}/{reader.__name__}'] = 'accepted'
            except ValueError:
                result[f'{label}/{reader.__name__}'] = 'rejected'
    assert all(v == 'rejected' for k, v in result.items() if k.endswith('load_league_snapshot')), result
    return result


def analyse(cells):
    """cells[(name, seed)] = {mode: {'out': {day: metrics}, 'rows': [...], 'extra': {...}}}"""
    pooled = [r for c in cells.values() for r in c['on']['rows']]
    by_disc = {t: share([r for r in pooled if tercile(r['discipline']) == t], 'response') for t in ('low', 'mid', 'high')}
    dissatisfied = [r for r in pooled if r['dissatisfaction'] > 0.10]
    by_stab = {t: share([r for r in dissatisfied if tercile(r['stability']) == t], 'response') for t in ('low', 'mid', 'high')}
    overall = share(pooled, 'response')
    cell_sign = []
    for c in cells.values():
        hi = [r for r in c['on']['rows'] if tercile(r['discipline']) == 'high']
        lo = [r for r in c['on']['rows'] if tercile(r['discipline']) == 'low']
        if hi and lo:
            cell_sign.append(share(hi, 'response')['complied'] - share(lo, 'response')['complied'])
    p1 = by_disc['low']['complied'] < by_disc['mid']['complied'] < by_disc['high']['complied']
    p2 = by_stab['low']['refused'] >= by_stab['high']['refused']
    p3 = all(overall[r] >= 0.05 for r in ('complied', 'partial', 'refused'))
    contrasts = {}
    for day in (SEASON, DAYS):
        for metric in ('skill', 'satisfaction', 'trust'):
            for label, a, b in (('on_minus_mid', 'on', 'mid'), ('mid_minus_off', 'mid', 'off')):
                contrasts[f'{label}/{metric}/day{day}'] = sign_test([c[a]['out'][day][metric] - c[b]['out'][day][metric] for c in cells.values()])
    primary = {k: v for k, v in contrasts.items() if k.endswith(f'day{DAYS}')}
    p5 = {'discipline_vs_skill_gain': [], 'stability_vs_satisfaction': []}
    for c in cells.values():
        ex = c['on']['extra']
        pids = sorted(ex['gain'])
        p5['discipline_vs_skill_gain'].append(spearman([ex['traits'][p]['discipline'] for p in pids], [ex['gain'][p] for p in pids]))
        p5['stability_vs_satisfaction'].append(spearman([ex['traits'][p]['emotional_stability'] for p in pids], [ex['satisfaction'][p] for p in pids]))
    p5s = {k: {'median': statistics.median([v for v in vs if v is not None]), 'positive_cells': sum(1 for v in vs if v and v > 0), 'cells': len([v for v in vs if v is not None])}
           for k, vs in p5.items()}
    return {'responses_pooled': overall, 'by_discipline_tercile': by_disc, 'dissatisfied_by_stability_tercile': by_stab,
            'dissatisfied_decisions': len(dissatisfied),
            'cells_high_minus_low_complied_positive': {'positive': sum(d > 0 for d in cell_sign), 'cells': len(cell_sign), 'mean': mean(cell_sign)},
            'P1_monotone': p1, 'P2_low_stability_refuses_at_least_high': p2, 'P3_not_degenerate': p3,
            'P4_primary_contrasts_alpha_0.0083': {k: {**v, 'significant': v['p_two_sided'] < 0.05 / 6} for k, v in primary.items()},
            'P4_descriptive_day84': {k: v for k, v in contrasts.items() if k.endswith(f'day{SEASON}')},
            'P5_spearman_within_cell': p5s,
            'primary_reason': {r: sum(1 for x in pooled if x['primary'] == r) for r in sorted({x['primary'] for x in pooled})},
            'by_mode_means_day168': {m: {k: mean(c[m]['out'][DAYS][k] for c in cells.values()) for k in ('skill', 'satisfaction', 'trust', 'transfers', 'events')} for m in ('off', 'mid', 'on')}}


def run(out, root):
    out.mkdir(parents=True, exist_ok=False)
    start = perf_counter()
    default = check_default_unchanged(root)
    regression = t019.check_regression(root)
    print('default', default, regression, flush=True)
    invalid, pure, static = check_invalid(), check_pure(), check_leak_static()
    assert all(invalid.values()), invalid
    print('invalid', invalid, 'pure', pure, 'static', static, flush=True)
    cells, rows_by_cell, personality_only_ok = {}, {}, 0
    for name in t023.conditions():
        for seed in SEEDS:
            cell = {}
            for mode in ('off', 'mid', 'on'):
                world, outcome, rows, extra = run_cell(name, seed, mode)
                cell[mode] = {'out': outcome, 'rows': rows, 'extra': extra}
                if mode == 'off':
                    raw = le._snapshot_payload_unchecked(world)
                    assert 'advice_response' not in raw['config'] and not any(e['kind'] in ('player_advice_response', 'coach_advice_outcome') for e in world.events)
                    personality_only_ok += 1
            rows_by_cell[(name, seed)] = cell['on']['rows']
            cells[(name, seed)] = cell
        print('cells', name, flush=True)
    analysis = analyse(cells)
    gate6 = counterfactual_and_pairs(rows_by_cell)
    print('analysis', json.dumps({k: analysis[k] for k in ('responses_pooled', 'by_discipline_tercile', 'dissatisfied_by_stability_tercile', 'P1_monotone', 'P2_low_stability_refuses_at_least_high', 'P3_not_degenerate')}), gate6, flush=True)
    dynamic = {name: check_leak_dynamic(name) for name in t023.conditions()}
    assert sum(1 for v in dynamic.values() if v['low_differs'] or v['high_differs']) >= 10, dynamic
    print('dynamic leak ok', len(dynamic), flush=True)
    with tempfile.TemporaryDirectory() as tmp:
        persisted = {name: check_persistence(name, Path(tmp) / name) for name in t023.conditions()}
        branch = check_branch_and_steps(Path(tmp) / 'branch')
        tamper = check_tamper(Path(tmp) / 'tamper')
    combined = check_combined(t023.conditions()[:2])
    print('persistence', branch, tamper, combined, flush=True)
    table = [{'world': n, 'seed': s, **{m: {'day84': c[m]['out'][SEASON], 'day168': c[m]['out'][DAYS]} for m in c}, 'responses': share(c['on']['rows'], 'response')}
             for (n, s), c in cells.items()]
    result = {'task': 'T028', 'identity': le.league_identity(), 'runtime': runtime(), 'seeds': list(SEEDS), 'days': DAYS, 'season_length_days': SEASON,
              'default_unchanged': default, 'default_vs_t018': regression, 'invalid_rejected': invalid, 'pure_function': pure, 'leak_static': static,
              'personality_only_cells_without_new_events': personality_only_ok, 'event_checked_cells': len(cells),
              'traits_enter_decision': gate6, 'leak_dynamic': dynamic, 'persistence_responses': persisted, 'branch': branch, 'tamper': tamper,
              'combined_season_aging_worlds': combined, 'analysis': analysis, 'cells': table, 'elapsed_seconds': perf_counter() - start}
    write_json(out / 'summary.json', result)
    print(json.dumps({'cells': len(cells), 'elapsed_seconds': result['elapsed_seconds']}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    run(parser.parse_args().out.resolve(), CODE_ROOT)
