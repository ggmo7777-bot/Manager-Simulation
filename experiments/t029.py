"""T029: the coach's belief (B layer) about each player's receptiveness, updated only from coach_advice_outcome records.

Protocol: docs/experiments/T029-PROTOCOL.md. 14 worlds x seeds {100,101,102} = 42 cells, 168 days, 84-day seasons; modes base / observe / use.
"""
import argparse
import ast
from contextlib import contextmanager, nullcontext
from dataclasses import replace
import itertools
import json
from pathlib import Path
import re
import statistics
import sys
import tempfile
from time import perf_counter

CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT))
from experiments import t019, t023, t028
from soccer_sim import league_economy as le
from soccer_sim.storage import digest, runtime, write_json

DAYS, SEASON, SEEDS = 168, 84, (100, 101, 102)
MODES = {'base': 'none', 'observe': 'observe_v1', 'use': 'use_v1'}
OUTCOME_KEYS, BELIEF_EVENT_KEYS = t028.COACH_KEYS, {'record_class', 'coach_id', 'player_id', 'mean', 'sd', 'n'}
FORBIDDEN = {'player_personality', 'personality', 'discipline', 'emotional_stability', 'player_minds', 'players', 'initial_players',
             'trust_by_coach', 'autonomy', 'playing_time_satisfaction', 'PlayerMind', 'PlayerTruth'}
SIGNAL = {'complied': 1.0, 'partial': 0.5, 'refused': 0.0}
REASONS = ('trusts_coach', 'distrusts_coach', 'personal')
ID = re.compile(r'^e\d+$')


def cfg(name, mode):
    return replace(t028.cfg(name, 'on', season_length_days=SEASON), coach_belief=MODES[mode])


# ---- independent re-implementation of the pre-registered belief (literal numbers, not imported) ----
def ind_new():
    return {'a': 1.0, 'b': 1.0, 'n': 0, 'reasons': {r: 0 for r in REASONS}}


def ind_update(belief, response, reason):
    s = SIGNAL[response]
    reasons = dict(belief['reasons'])
    reasons[reason] += 1
    return {'a': round(1.0 + 0.97 * (belief['a'] - 1.0) + s, 6), 'b': round(1.0 + 0.97 * (belief['b'] - 1.0) + (1.0 - s), 6),
            'n': belief['n'] + 1, 'reasons': reasons}


def ind_mean_sd(belief):
    a, b = belief['a'], belief['b']
    return round(a / (a + b), 6), round((a * b / ((a + b) ** 2 * (a + b + 1.0))) ** 0.5, 6)


def ind_uptake(mean):
    return round(min(1.0, mean / 0.5), 6)


class Fold:
    """Incremental independent fold of the outcome events of a world (never reads anything but those events)."""
    def __init__(self, world):
        self.world, self.i, self.beliefs = world, 0, {}

    def advance(self):
        events = self.world.events
        while self.i < len(events):
            ev = events[self.i]
            if ev['kind'] == 'coach_advice_outcome':
                d = ev['data']
                row = self.beliefs.setdefault(d['coach_id'], {})
                row[d['player_id']] = ind_update(row.get(d['player_id']) or ind_new(), d['response'], d['stated_reason'])
            self.i += 1
        return self.beliefs

    def means(self, coach_id):
        return {p: ind_mean_sd(b)[0] for p, b in self.advance().get(coach_id, {}).items()}


def check_invalid():
    ok = {}
    name = t023.conditions()[0]
    for label, value in {'unknown': 'bogus', 'int': 1, 'empty': ''}.items():
        try:
            replace(cfg(name, 'observe'), coach_belief=value)
        except ValueError:
            ok[label] = True
        else:
            ok[label] = False
    try:
        replace(le.config_for_condition('league_' + name), personality='latent_v1', coach_belief='observe_v1')
    except ValueError:
        ok['without_advice_response'] = True
    else:
        ok['without_advice_response'] = False
    cls = le.LeagueEconomyConfig
    on, off = cfg(name, 'use').to_dict(), cfg(name, 'base').to_dict()
    assert cls.has_save_fields(on) and cls.has_save_fields(off) and 'coach_belief' not in off and on['coach_belief'] == 'use_v1'
    assert not cls.has_save_fields({**on, 'unknown': 1})
    return ok


def check_pure():
    n = 0
    symbols = [(r, q) for r in SIGNAL for q in REASONS]
    sequences = [list(zip(seq, [q] * len(seq))) for length in range(1, 7) for seq in itertools.product(SIGNAL, repeat=length) for q in REASONS]
    sequences += [list(seq) for length in range(1, 4) for seq in itertools.product(symbols, repeat=length)]
    for seq in sequences:
        got, want = le.new_receptiveness_belief(), ind_new()
        for response, reason in seq:
            before = le.belief_mean_sd(got)[0]
            outcome = {'response': response, 'stated_reason': reason}
            got, want = le.update_receptiveness_belief(got, outcome), ind_update(want, response, reason)
            assert got == want and le.belief_mean_sd(got) == ind_mean_sd(want), (seq, got, want)
            mean, sd = le.belief_mean_sd(got)
            assert 0 < sd <= 0.2887
            if response == 'complied':
                assert mean > before
            elif response == 'refused':
                assert mean < before
            else:
                assert abs(mean - 0.5) <= abs(before - 0.5) + 1e-9
            # only response and stated_reason are read: other keys cannot matter
            noisy = {**outcome, 'action_followed': True, 'method': 'A', 'player_id': 'p1', 'coach_id': 'c1', 'record_class': 'x', 'extra': 1.5}
            assert le.update_receptiveness_belief(got, noisy) == le.update_receptiveness_belief(got, outcome)
        assert got['n'] == len(seq)
        n += 1
    grid = [i / 100 for i in range(101)]
    ups = [le.receptiveness_uptake(m) for m in grid]
    assert all(le.receptiveness_uptake(m) == ind_uptake(m) for m in grid) and all(y >= x for x, y in zip(ups, ups[1:]))
    assert all(u == 1.0 for m, u in zip(grid, ups) if m >= 0.5) and all(0 <= u <= 1 for u in ups)
    return {'sequences': n, 'uptake_points': len(grid)}


def synthetic_decision_inputs(world):
    coach = next(iter(world.coaches.values()))
    own = list(world.club_rosters[coach.club_id])
    others = [pid for pid in world.players if world.players[pid].club_id != coach.club_id]
    return dict(coach=coach, own_roster=own, other_players=others, club_cash_units=world.club_accounts[coach.club_id].cash_units,
                day=world.day, slot=1, already_targeted=set(), config=world.config)


def check_default_unchanged(root):
    base = t028.check_default_unchanged(root)
    name = t023.conditions()[0]
    for n in t023.conditions():
        explicit = le._snapshot_payload_unchecked(le.run_league_world(seed=100, days=SEASON, config=replace(cfg(n, 'base'), coach_belief='none')))
        default = le._snapshot_payload_unchecked(le.run_league_world(seed=100, days=SEASON, config=t028.cfg(n, 'on', season_length_days=SEASON)))
        assert explicit == default and 'coach_belief' not in explicit['config'] and 'coach_beliefs' not in explicit, n
    world = le.run_league_world(seed=100, days=40, config=cfg(name, 'base'))
    args = synthetic_decision_inputs(world)
    implicit, none, empty = le.decide_coach_training(**args), le.decide_coach_training(**args, receptiveness=None), le.decide_coach_training(**args, receptiveness={})
    assert implicit == none and all('uptake' not in c['components'] for c in implicit['candidates'])
    stripped = json.loads(json.dumps(empty))
    for c in stripped['candidates'] + [stripped['chosen']]:
        c['components'].pop('uptake', None)
    assert stripped == json.loads(json.dumps(implicit)) and any('uptake' in c['components'] for c in empty['candidates'])
    return {**base, 'explicit_none_equals_default_advice_on': 14, 'receptiveness_default_identical': True}


def reference_summary(root):
    path = root / 'artifacts/t028/run/summary.json'
    if not path.is_file():
        return None
    return {(c['world'], c['seed']): c['on'] for c in json.loads(path.read_text())['cells']}


# ---- verifying wrappers ----
@contextmanager
def verified(world_box, stats):
    """Every decide_coach_training call (use mode) is re-derived from the independent fold; every belief update sees only outcome keys."""
    original_decide, original_update = le.decide_coach_training, le.update_receptiveness_belief

    def update(belief, outcome):
        assert set(outcome) == OUTCOME_KEYS and set(belief) == {'a', 'b', 'n', 'reasons'}, (sorted(outcome), sorted(belief))
        stats['update_calls'] += 1
        return original_update(belief, outcome)

    def decide(**kw):
        got = original_decide(**kw)
        rec = kw.get('receptiveness')
        world = world_box[0]
        if world.config.coach_belief != 'use_v1':
            assert rec is None and all('uptake' not in c['components'] for c in got['candidates'])
            return got
        coach_id = kw['coach'].coach_id
        assert rec == fold_cache[id(world)].means(coach_id), (coach_id, rec)
        ref = original_decide(**{**kw, 'receptiveness': None})
        assert [(c['kind'], c['target'], c['method']) for c in got['candidates']] == [(c['kind'], c['target'], c['method']) for c in ref['candidates']]
        for c, r in zip(got['candidates'], ref['candidates']):
            if c['kind'] != 'propose':
                assert c == r and 'uptake' not in c['components']
                continue
            u = ind_uptake(rec.get(c['target'], 0.5))
            comp = c['components']
            assert comp['uptake'] == u and abs(comp['need_term'] - r['components']['need_term'] * u) <= 2e-6 \
                and abs(comp['forecast_term'] - r['components']['forecast_term'] * u) <= 2e-6, (c, r, u)
            parts = sum(comp[k] for k in ('need_term', 'forecast_term', 'pref_bonus', 'fatigue_penalty', 'repeat_penalty'))
            assert abs(c['score'] - round(0.15 + parts, 6)) <= 3e-6, c
            stats['propose_candidates'] += 1
            stats['scaled'] += u < 1.0
            stats['uptakes'].append(u)
        return got

    fold_cache.clear()
    fold_cache[id(world_box[0])] = Fold(world_box[0])
    le.decide_coach_training, le.update_receptiveness_belief = decide, update
    try:
        yield
    finally:
        le.decide_coach_training, le.update_receptiveness_belief = original_decide, original_update


fold_cache = {}


def check_events(world):
    """Gate 4: each outcome is followed by exactly one belief update equal to the independent fold at that moment; the final state is the fold."""
    events, beliefs, n = world.events, {}, 0
    for i, ev in enumerate(events):
        if ev['kind'] == 'coach_advice_outcome':
            d = ev['data']
            row = beliefs.setdefault(d['coach_id'], {})
            row[d['player_id']] = ind_update(row.get(d['player_id']) or ind_new(), d['response'], d['stated_reason'])
            after = events[i + 1]
            mean, sd = ind_mean_sd(row[d['player_id']])
            assert after['kind'] == 'coach_belief_update' and after['cause_refs'] == [ev['id']] and after['actor'] == d['coach_id'], (ev['id'], after)
            assert set(after['data']) == BELIEF_EVENT_KEYS and after['data']['record_class'] == 'coach_belief'
            assert (after['data']['coach_id'], after['data']['player_id'], after['data']['mean'], after['data']['sd'], after['data']['n']) == \
                (d['coach_id'], d['player_id'], mean, sd, row[d['player_id']]['n']), (after, mean, sd)
            n += 1
    kinds = [e['kind'] for e in events]
    assert kinds.count('coach_belief_update') == kinds.count('coach_advice_outcome') == n
    assert world.coach_beliefs == {**{cid: {} for cid in world.coaches}, **beliefs}
    return n


def strip_belief(world):
    """Observe-mode snapshot -> the snapshot of coach_belief='none': drop belief events/keys, renumber ids (a dropped id maps to the event before it)."""
    raw = le._snapshot_payload_unchecked(world)
    kept = [e for e in raw['events'] if e['kind'] != 'coach_belief_update']
    mapping, n_kept = {}, 0
    for e in raw['events']:
        n_kept += e['kind'] != 'coach_belief_update'
        mapping[e['id']] = f'e{n_kept}'

    def remap(v):
        if isinstance(v, str):
            return mapping[v] if ID.match(v) else v
        if isinstance(v, list):
            return [remap(x) for x in v]
        if isinstance(v, dict):
            # obs_refs are stored as sorted(set(ids)) as strings; renumbering changes string order, so re-sort them (not a behavioural difference)
            return {k: (sorted(remap(x)) if k == 'obs_refs' else remap(x)) for k, x in v.items()}
        return v

    def remap_event(e):
        out_e = remap(e)
        data = e['data']
        for source in (data.get('obs_refs'), (data.get('player_choice') or {}).get('obs_refs')):
            if source and e['cause_refs'][:len(source)] == source:  # cause_refs copied from a decision's sorted obs_refs (+ other refs after them)
                out_e['cause_refs'] = sorted(out_e['cause_refs'][:len(source)]) + out_e['cause_refs'][len(source):]
        if e['kind'] == 'transfer_offer_requested':  # cause_refs = the offer's sorted obs_refs
            out_e['cause_refs'] = sorted(out_e['cause_refs'])
        return out_e
    out = {k: remap(v) for k, v in raw.items() if k not in ('coach_beliefs', 'events')}
    out['events'] = [remap_event(e) for e in kept]
    out['config'].pop('coach_belief')
    return out


def check_static():
    t028.check_leak_static()
    source = (CODE_ROOT / 'soccer_sim/league_economy.py').read_text()
    tree = ast.parse(source)
    funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}

    def touched(node):
        return {s.id for s in ast.walk(node) if isinstance(s, ast.Name)} | {s.attr for s in ast.walk(node) if isinstance(s, ast.Attribute)} | \
            {s.value for s in ast.walk(node) if isinstance(s, ast.Constant) and isinstance(s.value, str)}

    names = sorted(n for n in funcs if 'belief' in n or 'receptiveness' in n or n == 'decide_coach_training')
    assert {'update_receptiveness_belief', '_beliefs_from_events', '_coach_receptiveness', 'decide_coach_training', 'receptiveness_uptake'} <= set(names), names
    for n in names:
        assert not touched(funcs[n]) & FORBIDDEN, (n, touched(funcs[n]) & FORBIDDEN)
    args = [a.arg for a in funcs['update_receptiveness_belief'].args.args]
    assert args == ['belief', 'outcome']
    subs = {s.slice.value for s in ast.walk(funcs['update_receptiveness_belief']) if isinstance(s, ast.Subscript) and isinstance(s.slice, ast.Constant)}
    assert {'response', 'stated_reason'} <= subs and not subs & {'action_followed', 'method', 'player_id', 'terms', 'score'}, subs
    # Sensitivity: the same checker flags belief code that reads the player's state.
    bad = ast.parse('def _coach_receptiveness(world, c):\n    return world.player_minds[c].trust_by_coach\n')
    assert touched(bad) & FORBIDDEN == {'player_minds', 'trust_by_coach'}
    return {'checked_functions': names}


def build(name, seed, mode):
    return le._build_initial_world(seed=seed, config=cfg(name, mode))


def metrics(world):
    return t028.metrics(world)


def run_cell(name, seed, mode, check=True):
    world = build(name, seed, mode)
    box, out = [world], {}
    stats = {'update_calls': 0, 'propose_candidates': 0, 'scaled': 0, 'uptakes': []}
    with (verified(box, stats) if check and mode != 'base' else nullcontext()):
        for day in (SEASON, DAYS):
            le.advance_league_days(world, day - world.day)
            out[day] = metrics(world)
    if check and mode != 'base':
        check_events(world)
        assert stats['update_calls'] >= sum(1 for e in world.events if e['kind'] == 'coach_advice_outcome')
    return world, out, stats


def behaviour(world):
    ev = world.events
    decisions = [e['data']['chosen']['kind'] for e in ev if e['kind'] == 'coach_training_decision']
    outcomes = [e['data'] for e in ev if e['kind'] == 'coach_advice_outcome']
    return {'propose_share': decisions.count('propose') / len(decisions), 'responses': len(outcomes),
            'refused_share': sum(o['response'] == 'refused' for o in outcomes) / len(outcomes) if outcomes else None,
            'followed_share': sum(o['action_followed'] for o in outcomes) / len(outcomes) if outcomes else None,
            'complied_share': sum(o['response'] == 'complied' for o in outcomes) / len(outcomes) if outcomes else None}


def truth_rows(world):
    """Researcher-only: belief vs the player's true current stance toward the coach (never fed back into the world)."""
    rows = []
    last = {}
    for e in world.events:
        if e['kind'] == 'coach_advice_outcome':
            last[(e['data']['coach_id'], e['data']['player_id'])] = e['day']
    for cid, row in world.coach_beliefs.items():
        for pid, belief in row.items():
            if world.players[pid].club_id != world.coaches[cid].club_id:
                continue
            mind, traits = world.player_minds[pid], world.player_personality[pid]
            now = le.decide_advice_response(trust=mind.trust_by_coach.get(cid, 0.5), autonomy=mind.autonomy, discipline=traits['discipline'],
                                           emotional_stability=traits['emotional_stability'], playing_time_satisfaction=mind.playing_time_satisfaction)
            mean, sd = le.belief_mean_sd(belief)
            rows.append({'coach': cid, 'player': pid, 'mean': mean, 'sd': sd, 'n': belief['n'], 's_now': SIGNAL[now['response']],
                         'discipline': traits['discipline'], 'stability': traits['emotional_stability'], 'last_advice_day': last[(cid, pid)]})
    return rows


def cell_truth(rows):
    use = [r for r in rows if r['n'] >= 5]
    out = {'pairs': len(use)}
    if len(use) >= 4:
        means = [r['mean'] for r in use]
        out |= {'spearman_s_now': t028.spearman(means, [r['s_now'] for r in use]),
                'mae_belief': statistics.fmean(abs(r['mean'] - r['s_now']) for r in use),
                'mae_prior_half': statistics.fmean(abs(0.5 - r['s_now']) for r in use),
                'spearman_discipline': t028.spearman(means, [r['discipline'] for r in use]),
                'spearman_stability': t028.spearman(means, [r['stability'] for r in use]),
                'within_one_sd': statistics.fmean(abs(r['mean'] - r['s_now']) <= r['sd'] for r in use)}
    return {k: v for k, v in out.items() if v is not None}  # a constant series has no rank correlation


def stagnation(world, rows):
    low = [r for r in rows if r['mean'] < 0.2]
    return {'low_belief_pairs': len(low), 'low_pairs_advised_in_last_28_days': sum(r['last_advice_day'] > world.day - 28 for r in low)}


def check_leak_dynamic(name):
    runs = {}
    for label, change in (('generated', None), ('eps', 1e-9), ('low', 0.15), ('high', 0.85)):
        world = build(name, 100, 'use')
        if change is not None:
            world.player_personality = {pid: {t: (v + change if label == 'eps' else change) for t, v in row.items()}
                                        for pid, row in world.player_personality.items()}
        le.advance_league_days(world, DAYS)
        runs[label] = world
    base = runs['generated']
    assert t028.responses(runs['eps']) == t028.responses(base) and t028.snapshot(runs['eps']) == t028.snapshot(base), name
    assert runs['eps'].coach_beliefs == base.coach_beliefs
    coach_part = lambda w: [e for e in w.events if e['kind'] in ('coach_advice_outcome', 'coach_belief_update', 'coach_training_decision')]
    assert coach_part(runs['eps']) == coach_part(base)
    return {'responses': len(t028.responses(base)), 'low_beliefs_differ': runs['low'].coach_beliefs != base.coach_beliefs,
            'high_beliefs_differ': runs['high'].coach_beliefs != base.coach_beliefs}


def check_persistence(name, directory, mode):
    world = le.run_league_world(seed=100, days=DAYS, config=cfg(name, mode))
    le.save_league_artifacts(world, directory)
    for reader in (le.load_league_snapshot, le.load_league_snapshot_trusted):
        le._assert_same(reader(Path(directory) / 'snapshot.json'), world, 'world')
    return sum(1 for e in world.events if e['kind'] == 'coach_belief_update')


def check_branch_and_steps(directory):
    config = cfg(t023.conditions()[0], 'use')
    parent = le.run_league_world(seed=100, days=30, config=config)
    child = le.fork_league_world(parent, kind='player_trust', player_id='p3', coach_id='c1', value=0.05, reason='t029')
    le.advance_league_days(child, 60)
    le.save_league_artifacts(child, directory)
    le._assert_same(le.load_league_snapshot(Path(directory) / 'snapshot.json'), child, 'world')
    stepped = le._build_initial_world(seed=100, config=config)
    le.advance_league_days(stepped, 30)
    le.advance_league_days(stepped, 60)
    le._assert_same(stepped, le.run_league_world(seed=100, days=90, config=config), 'world')
    return {'interventions': len(child.interventions), 'beliefs': sum(len(r) for r in child.coach_beliefs.values()), 'stepped_equals_oneshot': True}


def check_combined(names):
    done = 0
    for name in names:
        config = replace(cfg(name, 'use'), aging_start_age=29, aging_curve='hypothesis_v1', season_length_days=42)
        world = le.run_league_world(seed=100, days=126, config=config)
        with tempfile.TemporaryDirectory() as tmp:
            le.save_league_artifacts(world, Path(tmp) / 'w')
            for reader in (le.load_league_snapshot, le.load_league_snapshot_trusted):
                le._assert_same(reader(Path(tmp) / 'w/snapshot.json'), world, 'world')
        done += 1
    return done


def check_tamper(directory):
    world = le.run_league_world(seed=100, days=60, config=cfg('open_solidarity' + le.POPULATION_SUFFIX, 'use'))
    first = lambda r, kind: next(i for i, e in enumerate(r['events']) if e['kind'] == kind)

    def change_belief(r):
        cid = next(c for c, row in r['coach_beliefs'].items() if row)
        pid = next(iter(r['coach_beliefs'][cid]))
        r['coach_beliefs'][cid][pid]['a'] += 0.5

    cases = {'belief_value_changed': change_belief,
             'belief_update_removed': lambda r: r['events'].pop(first(r, 'coach_belief_update')),
             'outcome_response_changed': lambda r: r['events'][first(r, 'coach_advice_outcome')]['data'].__setitem__(
                 'response', 'refused' if r['events'][first(r, 'coach_advice_outcome')]['data']['response'] != 'refused' else 'complied')}
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
    assert all(v == 'rejected' for k, v in result.items() if k.endswith('load_league_snapshot')), result
    assert result['belief_value_changed/load_league_snapshot_trusted'] == 'rejected', result
    return result


def analyse(cells):
    """cells[(name, seed)][mode] = {'out', 'behaviour', 'truth', 'stats', 'stag'}"""
    truth = [c['observe']['truth'] for c in cells.values()]
    med = lambda key: statistics.median([t[key] for t in truth if key in t])
    pos = lambda key: sum(1 for t in truth if t.get(key, 0) > 0)
    p1 = {'median_spearman': med('spearman_s_now'), 'positive_cells': pos('spearman_s_now'), 'cells_with_estimate': sum(1 for t in truth if 'spearman_s_now' in t)}
    p1['supported'] = p1['median_spearman'] > 0.30 and p1['positive_cells'] >= 30
    better = sum(1 for t in truth if 'mae_belief' in t and t['mae_belief'] < t['mae_prior_half'])
    p2 = {'cells_belief_closer_than_prior': better, 'mean_mae_belief': statistics.fmean(t['mae_belief'] for t in truth if 'mae_belief' in t),
          'mean_mae_prior_half': statistics.fmean(t['mae_prior_half'] for t in truth if 'mae_prior_half' in t), 'supported': better >= 30}
    p3 = {'median_spearman_discipline': med('spearman_discipline'), 'median_spearman_stability': med('spearman_stability')}
    p3['supported'] = abs(p3['median_spearman_discipline']) < 0.30 and abs(p3['median_spearman_stability']) < 0.30
    p3['median_within_one_sd'] = med('within_one_sd')
    diff = lambda key: [c['use']['behaviour'][key] - c['observe']['behaviour'][key] for c in cells.values() if c['use']['behaviour'][key] is not None and c['observe']['behaviour'][key] is not None]
    p4 = {key: t028.sign_test(diff(key)) for key in ('propose_share', 'responses', 'refused_share', 'followed_share', 'complied_share')}
    p4['supported'] = {'propose_share_lower': p4['propose_share']['mean'] < 0 and p4['propose_share']['negative'] > p4['propose_share']['positive'],
                       'responses_lower': p4['responses']['mean'] < 0 and p4['responses']['negative'] > p4['responses']['positive'],
                       'refused_share_lower': p4['refused_share']['mean'] < 0 and p4['refused_share']['negative'] > p4['refused_share']['positive']}
    contrasts = {}
    for day in (SEASON, DAYS):
        for metric in ('skill', 'satisfaction', 'trust'):
            contrasts[f'use_minus_observe/{metric}/day{day}'] = t028.sign_test([c['use']['out'][day][metric] - c['observe']['out'][day][metric] for c in cells.values()])
    primary = {k: {**v, 'significant': v['p_two_sided'] < 0.05 / 3} for k, v in contrasts.items() if k.endswith(f'day{DAYS}')}
    uptakes = [u for c in cells.values() for u in c['use']['stats']['uptakes']]
    stag = {k: sum(c['use']['stag'][k] for c in cells.values()) for k in ('low_belief_pairs', 'low_pairs_advised_in_last_28_days')}
    obs_stag = {k: sum(c['observe']['stag'][k] for c in cells.values()) for k in ('low_belief_pairs', 'low_pairs_advised_in_last_28_days')}
    all_means = [r['mean'] for c in cells.values() for r in c['use']['rows']]
    return {'P1_belief_tracks_current_receptiveness': p1, 'P2_belief_beats_constant_prior': p2, 'P3_traits_only_via_outcomes': p3, 'P4_mechanism_use_minus_observe': p4,
            'P5_primary_alpha_0.0167': primary, 'P5_descriptive_day84': {k: v for k, v in contrasts.items() if k.endswith(f'day{SEASON}')},
            'uptake': {'propose_candidates': sum(c['use']['stats']['propose_candidates'] for c in cells.values()),
                       'scaled_share': (sum(1 for u in uptakes if u < 1.0) / len(uptakes)) if uptakes else None,
                       'mean_uptake': statistics.fmean(uptakes) if uptakes else None},
            'stagnation_use': stag, 'stagnation_observe': obs_stag,
            'use_belief_mean_distribution': {'mean': statistics.fmean(all_means), 'below_0.2': sum(m < 0.2 for m in all_means) / len(all_means), 'n': len(all_means)},
            'by_mode_means_day168': {m: {k: statistics.fmean(c[m]['out'][DAYS][k] for c in cells.values()) for k in ('skill', 'satisfaction', 'trust', 'transfers', 'events')} for m in ('base', 'observe', 'use')},
            'behaviour_means': {m: {k: statistics.fmean(c[m]['behaviour'][k] for c in cells.values() if c[m]['behaviour'][k] is not None) for k in ('propose_share', 'responses', 'refused_share', 'followed_share', 'complied_share')} for m in ('base', 'observe', 'use')}}


def run(out, root):
    out.mkdir(parents=True, exist_ok=False)
    start = perf_counter()
    default = check_default_unchanged(root)
    regression = t019.check_regression(root)
    print('default', default, regression, flush=True)
    invalid, pure, static = check_invalid(), check_pure(), check_static()
    assert all(invalid.values()), invalid
    print('invalid', invalid, 'pure', pure, 'static', static, flush=True)
    reference = reference_summary(root)
    cells, base_matches_t028, observe_equals_base = {}, 0, 0
    for name in t023.conditions():
        for seed in SEEDS:
            cell = {}
            for mode in ('base', 'observe', 'use'):
                world, outcome, stats = run_cell(name, seed, mode)
                rows = truth_rows(world) if mode != 'base' else []
                cell[mode] = {'out': outcome, 'behaviour': behaviour(world), 'truth': cell_truth(rows), 'stats': stats, 'rows': rows,
                              'stag': stagnation(world, rows) if mode != 'base' else {}}
                if mode == 'base':
                    raw_base = le._snapshot_payload_unchecked(world)
                    assert 'coach_belief' not in raw_base['config'] and 'coach_beliefs' not in raw_base and not any(e['kind'] == 'coach_belief_update' for e in world.events)
                    if reference is not None:
                        ref = reference[(name, seed)]
                        assert {d: outcome[d] for d in (SEASON, DAYS)} == {SEASON: ref['day84'], DAYS: ref['day168']}, (name, seed)
                        base_matches_t028 += 1
                if mode == 'observe':
                    assert strip_belief(world) == raw_base, (name, seed)
                    observe_equals_base += 1
            cells[(name, seed)] = cell
        print('cells', name, flush=True)
    analysis = analyse(cells)
    print('analysis', json.dumps({k: analysis[k] for k in ('P1_belief_tracks_current_receptiveness', 'P2_belief_beats_constant_prior', 'P3_traits_only_via_outcomes')}), flush=True)
    dynamic = {name: check_leak_dynamic(name) for name in t023.conditions()}
    sensitive = sum(1 for v in dynamic.values() if v['low_beliefs_differ'] or v['high_beliefs_differ'])
    assert sensitive >= 10, dynamic
    print('dynamic leak ok', sensitive, flush=True)
    with tempfile.TemporaryDirectory() as tmp:
        persisted = {f'{mode}/{name}': check_persistence(name, Path(tmp) / f'{mode}{name}', mode) for mode in ('observe', 'use') for name in t023.conditions()}
        branch = check_branch_and_steps(Path(tmp) / 'branch')
        tamper = check_tamper(Path(tmp) / 'tamper')
    combined = check_combined(t023.conditions()[:2])
    print('persistence', branch, tamper, combined, flush=True)
    table = [{'world': n, 'seed': s, **{m: {'day84': c[m]['out'][SEASON], 'day168': c[m]['out'][DAYS], 'behaviour': c[m]['behaviour'], 'truth': c[m]['truth']} for m in c}}
             for (n, s), c in cells.items()]
    result = {'task': 'T029', 'identity': le.league_identity(), 'runtime': runtime(), 'seeds': list(SEEDS), 'days': DAYS, 'season_length_days': SEASON,
              'default_unchanged': default, 'default_vs_t018': regression, 'invalid_rejected': invalid, 'pure_function': pure, 'static': static,
              'base_cells_equal_frozen_t028_on': base_matches_t028, 'observe_cells_equal_base_after_strip': observe_equals_base, 'event_checked_cells': len(cells) * 2,
              'leak_dynamic': dynamic, 'leak_dynamic_sensitive_worlds': sensitive, 'persistence_belief_updates': persisted, 'branch': branch, 'tamper': tamper,
              'combined_season_aging_worlds': combined, 'analysis': analysis, 'cells': table, 'elapsed_seconds': perf_counter() - start}
    write_json(out / 'summary.json', result)
    print(json.dumps({'cells': len(cells), 'elapsed_seconds': result['elapsed_seconds']}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    run(parser.parse_args().out.resolve(), CODE_ROOT)
