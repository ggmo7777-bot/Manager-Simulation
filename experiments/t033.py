"""T033: why newcomers do not start. Analysis only: the recorded lineup_decision rankings (skill_term, match_term, fatigue_penalty) are decomposed offline;
the simulation is not changed and nothing goes back into it. True skills are read by the researcher after the fact.

Protocol: docs/experiments/T033-PROTOCOL.md. Cells: T030 / T031 / T032 settings x 14 worlds x seeds {100,101,102} x start age {29,33}, 168 days.
"""
import argparse
import ast
import json
from pathlib import Path
import sys
from time import perf_counter

CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT))
from experiments import t023, t030, t031, t032
from soccer_sim import league_economy as le
from soccer_sim.storage import runtime, write_json

L, DAYS, SEEDS, STARTS = 21, 168, (100, 101, 102), (29, 33)
SETTINGS = {'t030': lambda n, s: t030.cfg(n, s, 'on'), 't031': lambda n, s: t031.cfg(n, s, 'on'), 't032': lambda n, s: t032.cfg(n, s, 'on')}
TERMS = ('skill_term', 'match_term', 'fatigue_penalty')
CFS = ('a_no_fatigue', 'b_no_match', 'c_no_skill', 'd_true_skill_est', 'e_true_match_est', 'f_true_both', 'g_match_eq_skill_est', 'h_true_only')
TOL = 3e-6
mean = t030.mean


def starters_of(scores):
    """Top two by score, ties by player id (the selection's own tie rule)."""
    return [p for p, _ in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:2]]


def analyse_row(ranking, n, w, truth):
    """ranking: recorded lineup_decision ranking; n: newcomer id; w: (w_skill, w_match, w_fatigue); truth: {player: true composite skill}.
    The newcomer must beat the second best of the others (cutoff c) to start. Returns the exact additive decompositions and the counterfactual starts."""
    ws, wm, wf = w
    comp = {r['player_id']: r['components'] for r in ranking}
    score = {r['player_id']: r['score'] for r in ranking}
    others = sorted((p for p in comp if p != n), key=lambda p: (-score[p], p))
    c = others[1]
    d = {t: comp[n][t] - comp[c][t] for t in TERMS}
    margin = score[n] - score[c]
    est_s = {p: comp[p]['skill_term'] / ws for p in comp}
    est_m = {p: comp[p]['match_term'] / wm for p in comp}
    dT = truth[n] - truth[c]
    parts = {'true_gap': (ws + wm) * dT, 'skill_est_error': ws * ((est_s[n] - truth[n]) - (est_s[c] - truth[c])),
             'match_est_error': wm * ((est_m[n] - truth[n]) - (est_m[c] - truth[c])), 'fatigue': d['fatigue_penalty']}
    sk, mt, fp = ({p: comp[p][t] for p in comp} for t in TERMS)
    alt = {'a_no_fatigue': {p: sk[p] + mt[p] for p in comp}, 'b_no_match': {p: sk[p] + fp[p] for p in comp}, 'c_no_skill': {p: mt[p] + fp[p] for p in comp},
           'd_true_skill_est': {p: ws * truth[p] + mt[p] + fp[p] for p in comp}, 'e_true_match_est': {p: sk[p] + wm * truth[p] + fp[p] for p in comp},
           'f_true_both': {p: (ws + wm) * truth[p] + fp[p] for p in comp}, 'g_match_eq_skill_est': {p: sk[p] + wm * est_s[p] + fp[p] for p in comp},
           'h_true_only': {p: truth[p] for p in comp}}
    top2_true = n in starters_of(alt['h_true_only'])
    return {'started': n in starters_of(score), 'margin': margin, 'cutoff': c, 'd_skill': d['skill_term'], 'd_match': d['match_term'], 'd_fatigue': d['fatigue_penalty'],
            **parts, 'top2_true': top2_true, 'n_skill_err': est_s[n] - truth[n], 'c_skill_err': est_s[c] - truth[c], 'n_match_err': est_m[n] - truth[n], 'c_match_err': est_m[c] - truth[c],
            'n_fatigue_est': -comp[n]['fatigue_penalty'] / wf, 'c_fatigue_est': -comp[c]['fatigue_penalty'] / wf, 'n_match_minus_skill': est_m[n] - est_s[n], 'c_match_minus_skill': est_m[c] - est_s[c],
            'cf': {k: n in starters_of(v) for k, v in alt.items()}}


# ---- gate 3: the recorded ranking is exactly what the weights and estimates give ----
def check_lineup_event(data):
    assert set(data) >= {'ranking', 'starters', 'bench'}
    for r in data['ranking']:
        assert set(r['components']) == set(TERMS), r  # P6: no incumbency/continuity term is ever recorded
        assert abs(r['score'] - sum(r['components'].values())) <= TOL, r
    assert starters_of({r['player_id']: r['score'] for r in data['ranking']}) == data['starters']


def check_selection_source():
    """P6 (static): decide_club_lineup reads only the coach's per-player estimates and the three weights."""
    tree = ast.parse((CODE_ROOT / 'soccer_sim/league_economy.py').read_text())
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'decide_club_lineup')
    names = t030.touched(fn)
    reads = {n for n in names if n.startswith('select_')}
    assert reads == {'select_skill_weight', 'select_match_weight', 'select_fatigue_weight'}, reads
    assert {'skill_estimate', 'match_rating_estimate', 'fatigue_estimate'} <= names
    assert not names & {'last_starters', 'public_last_starters', 'club_rosters', 'days_since_start', 'age', 'player_ages', 'players', 'player_minds'}, names
    return {'select_weights_read': sorted(reads), 'estimates_read': ['fatigue_estimate', 'match_rating_estimate', 'skill_estimate']}


def check_sim_unchanged(root):
    """Gate 1: soccer_sim is byte-identical to T032's frozen bundle; same source hash as T032's run."""
    bundle = root / 'artifacts/t032/source_bundle/soccer_sim'
    files = sorted(p.name for p in (root / 'soccer_sim').glob('*.py'))
    assert files == sorted(p.name for p in bundle.glob('*.py'))
    assert all((root / 'soccer_sim' / f).read_bytes() == (bundle / f).read_bytes() for f in files)
    recorded = json.loads((root / 'artifacts/t032/run/summary.json').read_text())['identity']['source_sha256']
    assert le.league_identity()['source_sha256'] == recorded
    return {'files_compared': len(files), 'source_sha256': recorded}


# ---- one cell ----
def run_cell(config, seed, days=DAYS):
    world = le._build_initial_world(seed=seed, config=config)
    w = (config.select_skill_weight, config.select_match_weight, config.select_fatigue_weight)
    newcomers, lineups, checked = {}, [], 0
    while world.day < days:
        n_events = len(world.events)
        le.advance_league_days(world, 1)
        new = world.events[n_events:]
        for e in new:
            if e['kind'] == 'season_youth_joined':
                newcomers[e['data']['player_id']] = {'club': e['data']['club_id'], 'day': world.day}
            elif e['kind'] == 'lineup_decision':
                check_lineup_event(e['data'])
                checked += 1
        if any(e['kind'] == 'lineup_decision' for e in new):
            truth = {p: world.players[p].composite_skill() for p in world.players}
            lineups.extend({'day': world.day, 'club': e['data']['club_id'], 'ranking': e['data']['ranking'], 'starters': e['data']['starters'], 'truth': truth}
                           for e in new if e['kind'] == 'lineup_decision')
        for pid, info in newcomers.items():
            if info['day'] == world.day - L:  # T031/T032 definition of the "true top two" newcomer at +21 days
                roster = world.club_rosters[info['club']]
                top2 = sorted(roster, key=lambda p: (-world.players[p].composite_skill(), p))[:2]
                info['top2_true_end'] = pid in top2
    le._validate_league_invariants(world)
    rows = []
    for pid, info in newcomers.items():
        if 'top2_true_end' not in info:
            continue
        prior = 0
        mine = [l for l in lineups if l['club'] == info['club'] and info['day'] < l['day'] <= info['day'] + L and any(r['player_id'] == pid for r in l['ranking'])]
        for l in mine:
            row = analyse_row(l['ranking'], pid, w, l['truth'])
            assert row['started'] == (pid in l['starters'])
            assert abs(row['margin'] - (row['d_skill'] + row['d_match'] + row['d_fatigue'])) <= TOL
            assert abs(row['margin'] - (row['true_gap'] + row['skill_est_error'] + row['match_est_error'] + row['fatigue'])) <= TOL
            rows.append({'player': pid, 'day': l['day'], 'since_join': l['day'] - info['day'], 'prior_starts': prior, **row})
            prior += row['started']
    cohort = {pid: i['top2_true_end'] for pid, i in newcomers.items() if 'top2_true_end' in i}
    starts = {pid: any(r['started'] for r in rows if r['player'] == pid) for pid in cohort}
    return {'final': t030.final_metrics(world), 'events': len(world.events), 'lineups_checked': checked, 'rows': rows, 'cohort': cohort, 'starts_in_window': starts}


# ---- aggregation ----
def agg(rows):
    keys = ('margin', 'd_skill', 'd_match', 'd_fatigue', 'true_gap', 'skill_est_error', 'match_est_error', 'n_match_minus_skill', 'c_match_minus_skill',
            'n_skill_err', 'c_skill_err', 'n_match_err', 'c_match_err', 'n_fatigue_est', 'c_fatigue_est')
    return {'rows': len(rows), **{k: mean(r[k] for r in rows) for k in keys}}


def flips(rows):
    return {k: mean(1.0 if r['cf'][k] else 0.0 for r in rows) for k in CFS} if rows else None


def summarise(rows, cohort_cells):
    started = [r for r in rows if r['started']]
    missed = [r for r in rows if not r['started'] and r['top2_true']]
    skipped = [r for r in rows if not r['started']]
    # dominant term among the three recorded terms, for the rows where the newcomer did not start
    terms = {k: mean(r[k] for r in skipped) for k in ('d_skill', 'd_match', 'd_fatigue')}
    prior0, prior1 = [r for r in rows if r['prior_starts'] == 0], [r for r in rows if r['prior_starts'] >= 1]
    by_player = {}
    for r in rows:
        by_player.setdefault(r['uid'], []).append(r)
    never = [v for v in by_player.values() if not any(r['started'] for r in v)]
    some = [v for v in by_player.values() if any(r['started'] for r in v)]
    cohort = {}
    for cell in cohort_cells:
        cohort.update({(cell['key'], p): (t, cell['starts_in_window'][p]) for p, t in cell['cohort'].items()})
    top = [v for v in cohort.values() if v[0]]
    return {'rows': len(rows), 'newcomers': len(by_player), 'start_row_share': len(started) / len(rows),
            'started_rows': agg(started), 'not_started_rows': agg(skipped), 'missed_top2_rows': agg(missed), 'missed_top2_row_count': len(missed),
            'dominant_term_not_started': max(terms, key=lambda k: abs(terms[k])), 'mean_terms_not_started': terms,
            'cf_flip_rate_not_started': flips(skipped), 'cf_flip_rate_missed_top2': flips(missed), 'cf_stay_rate_started': flips(started),
            'by_days_since_join': {f'{lo}-{hi}': {**agg(sub), 'start_share': mean(1.0 if r['started'] else 0.0 for r in sub)} for lo, hi in ((1, 7), (8, 14), (15, 21)) for sub in [[r for r in rows if lo <= r['since_join'] <= hi]] if sub},
            'prior_starts': {'rows_0': len(prior0), 'start_prob_0': mean(1.0 if r['started'] else 0.0 for r in prior0), 'rows_1plus': len(prior1),
                             'start_prob_1plus': mean(1.0 if r['started'] else 0.0 for r in prior1)},
            'newcomer_level': {'never_started_newcomers': len(never), 'never_started_mean_margin': mean(mean(r['margin'] for r in v) for v in never),
                               'never_started_mean_terms': {k: mean(mean(r[k] for r in v) for v in never) for k in ('d_skill', 'd_match', 'd_fatigue')},
                               'started_some_newcomers': len(some), 'started_some_mean_margin': mean(mean(r['margin'] for r in v) for v in some),
                               'started_some_mean_terms': {k: mean(mean(r[k] for r in v) for v in some) for k in ('d_skill', 'd_match', 'd_fatigue')}},
            'cohort': {'newcomers_full_season': len(cohort), 'started_share': mean(1.0 if v[1] else 0.0 for v in cohort.values()), 'top2_true': len(top),
                       'top2_started_share': mean(1.0 if v[1] else 0.0 for v in top), 'missed_talent': sum(1 for v in top if not v[1])}}


def run(out, root):
    out.mkdir(parents=True, exist_ok=False)
    start = perf_counter()
    sim = check_sim_unchanged(root)
    static = check_selection_source()
    print('sim', sim, static, flush=True)
    refs = {t: {(c['world'], c['seed'], c['start_age']): c for c in json.loads((root / f'artifacts/{t}/run/summary.json').read_text())['cells']} for t in SETTINGS}
    ref_pick = {'t030': lambda c: c['on'], 't031': lambda c: c['on'], 't032': lambda c: c['on']}
    per_setting, all_rows, equal = {}, [], {t: 0 for t in SETTINGS}
    for t, make in SETTINGS.items():
        cells = []
        for name in t023.conditions():
            for seed in SEEDS:
                for st in STARTS:
                    cell = run_cell(make(name, st), seed)
                    ref = ref_pick[t](refs[t][(name, seed, st)])
                    assert cell['final'] == ref['final'] and cell['events'] == ref['events'], (t, name, seed, st)  # gate 2
                    equal[t] += 1
                    cell['key'] = (name, seed, st)
                    for r in cell['rows']:
                        r['uid'] = f'{name}/{seed}/{st}/{r["player"]}'  # newcomer ids repeat across worlds
                    for r in cell['rows']:
                        all_rows.append({'setting': t, 'world': name, 'seed': seed, 'start_age': st, **{k: (round(v, 6) if isinstance(v, float) else v) for k, v in r.items() if k != 'uid'}})
                    cells.append(cell)
            print(t, name, flush=True)
        per_setting[t] = {'all': summarise([r for c in cells for r in c['rows']], cells),
                          **{f'start_age_{st}': summarise([r for c in cells if c['key'][2] == st for r in c['rows']], [c for c in cells if c['key'][2] == st]) for st in STARTS},
                          'lineups_checked': sum(c['lineups_checked'] for c in cells), 'max_abs_margin_error_checked': TOL}
    t32 = json.loads((root / 'artifacts/t032/run/summary.json').read_text())['analysis']['newcomers']
    c31, c32 = per_setting['t031']['all']['cohort'], per_setting['t032']['all']['cohort']
    cohort_match = {'t031_equals_t032_off': [c31['started_share'], t32['off']['started_share'], c31['top2_true'], t32['off']['top2_true_newcomers'], c31['missed_talent'], t32['off']['missed_talent']],
                    't032_equals_t032_on': [c32['started_share'], t32['on']['started_share'], c32['top2_true'], t32['on']['top2_true_newcomers'], c32['missed_talent'], t32['on']['missed_talent']]}
    for v in cohort_match.values():
        assert v[0] == v[1] and v[2] == v[3] and v[4] == v[5], cohort_match
    cohort_match['newcomers'] = [c31['newcomers_full_season'], t32['off']['newcomers_with_a_full_season']]
    assert cohort_match['newcomers'][0] == cohort_match['newcomers'][1]
    result = {'task': 'T033', 'identity': le.league_identity(), 'runtime': runtime(), 'seeds': list(SEEDS), 'days': DAYS, 'season_length_days': L,
              'sim_unchanged': sim, 'selection_static': static, 'control_cells_equal': equal, 'cohort_reproduced': cohort_match,
              'rows_total': len(all_rows), 'settings': per_setting, 'elapsed_seconds': perf_counter() - start}
    write_json(out / 'summary.json', result)
    write_json(out / 'rows.json', all_rows)
    print(json.dumps({'rows': len(all_rows), 'elapsed_seconds': result['elapsed_seconds']}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    run(parser.parse_args().out.resolve(), CODE_ROOT)
