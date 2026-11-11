"""T038: why does a mean-corrected observation noise make the coach's top-2 lineup more accurate? Analysis only: `soccer_sim/` is unchanged and no config is added.
The legacy / shifted_v1 / centered_v1 runs of T037 (3 settings x 84 cells x 168 days) are replayed; a read-only wrapper notes each player's `last_seen_day` when the coach picks the lineup.
True skills are read by the researcher after the fact; nothing goes back into the simulation.

Protocol: docs/experiments/T038-PROTOCOL.md. Measures: error by time since the last observation (M1), pair-order accuracy (M2), top-2 mistakes (M3), a re-ranking with an age-bin correction fitted on the other settings (M4).
"""
import argparse
import json
from pathlib import Path
import random
import sys
from time import perf_counter

CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT))
from experiments import t023, t030, t033, t036, t037
from soccer_sim import league_economy as le
from soccer_sim.storage import runtime, write_json

L, DAYS, SEEDS, STARTS, SETTINGS = t036.L, t036.DAYS, t036.SEEDS, t036.STARTS, t036.SETTINGS
MODES = ('legacy', 'shifted_v1', 'centered_v1')
BIN_NAMES = ('a<=6', 'a=7', '8-13', '14-27', 'a>=28')
FRESH, STALE = (0, 1), (3, 4)  # bins that make the "new" (a<=7) and "old" (a>=14) observation classes
CLASSES = ('inc', 'new', 'other')  # disjoint; "inc"/"new" are T036's groups, "other" are newcomers outside their first 21 days or at another club
MIN_TRUE_GAP, MIN_AGE_GAP, MIN_BIN_N = 0.02, 7, 30
FROZEN = 'artifacts/t037/source_bundle'
add, merge, stat = t036.add, t036.merge, t036.stat
make_cfg = t036.make_cfg


def age_bin(age):
    return 0 if age <= 6 else 1 if age == 7 else 2 if age <= 13 else 3 if age <= 27 else 4


def top2(scores):
    """The two best (id, value) by value, ties by id (the selection's own rule)."""
    return set(p for p, _ in sorted(scores, key=lambda kv: (-kv[1], kv[0]))[:2])


def pair_class(age_better, age_worse, min_age_gap=MIN_AGE_GAP):
    gap = age_worse - age_better
    return 'better_newer' if gap >= min_age_gap else 'better_older' if gap <= -min_age_gap else 'close'


def pair_accuracy(rows, min_age_gap=MIN_AGE_GAP):
    """rows: (id, age, truth) in the coach's ranking order. Yields (class, ordered_right) for every pair whose true skills differ by >= MIN_TRUE_GAP."""
    for i, a in enumerate(rows):
        for b in rows[i + 1:]:
            if abs(a[2] - b[2]) < MIN_TRUE_GAP:
                continue
            better, worse = (a, b) if a[2] > b[2] else (b, a)
            yield pair_class(better[1], worse[1], min_age_gap), better is a


def rerank(lineup, delta):
    """lineup: (class, bin, score, truth, id) rows. delta[(class, bin)] is subtracted from the score; unknown keys subtract 0. Returns the new starters."""
    return top2([(r[4], r[2] - delta.get((r[0], r[1]), 0.0)) for r in lineup])


# ---- one cell: the lineup rows t036.run_cell reads, plus the time since the last observation ----
def run_cell(config, seed, days=DAYS):
    world = le._build_initial_world(seed=seed, config=config)
    ws, wm = config.select_skill_weight, config.select_match_weight
    acc, newc, lineups, seen_days = {}, {}, [], []
    original = le.decide_club_lineup

    def reading(*, coach, roster, config):
        out = original(coach=coach, roster=roster, config=config)
        if sys._getframe(1).f_code.co_name == '_step_matchday':  # the transfer decisions also call it, on hypothetical rosters
            seen_days.append({pid: coach.evaluations[pid].last_seen_day for pid in roster})
        return out
    le.decide_club_lineup = reading
    try:
        while world.day < days:
            n_events, n_seen = len(world.events), len(seen_days)
            le.advance_league_days(world, 1)
            new, truth = world.events[n_events:], {}
            true_skill = lambda pid: truth.setdefault(pid, world.players[pid].composite_skill())
            decisions = iter(seen_days[n_seen:])
            for e in new:
                d = e['data']
                if e['kind'] == 'season_youth_joined':
                    newc[d['player_id']] = {'club': d['club_id'], 'day': world.day}
                if e['kind'] != 'lineup_decision':
                    continue
                seen = next(decisions)
                rows = []
                for r in d['ranking']:
                    pid = r['player_id']
                    since = world.day - newc[pid]['day'] if pid in newc and newc[pid]['club'] == d['club_id'] else None
                    cls = 'inc' if pid not in newc else 'new' if since is not None and 0 < since <= L else 'other'
                    age = world.day - seen[pid]
                    b = age_bin(age)
                    for kind, est in (('skill', r['components']['skill_term'] / ws), ('match', r['components']['match_term'] / wm)):
                        for g in (cls, 'all'):
                            add(acc, ('err', g, b, kind), est - true_skill(pid))
                    rows.append((pid, age, true_skill(pid), cls, b, r['score']))
                true_set, starters = top2([(p, true_skill(p)) for p, *_ in rows]), set(d['starters'])
                add(acc, ('start', 'true_top2_match'), float(starters == true_set))
                for pid, age, *_ in rows:
                    if pid in starters:
                        add(acc, ('age', 'starters'), age)
                    add(acc, ('age', 'roster'), age)
                for cls, right in pair_accuracy([(p, a, t) for p, a, t, *_ in rows]):
                    add(acc, ('pair', cls), 0.0, float(right))
                for cls, right in pair_accuracy([(p, a, t) for p, a, t, *_ in rows], min_age_gap=1):  # post hoc: the registered 7-day gap rarely occurs (see RESULTS)
                    add(acc, ('pair_ph', cls), 0.0, float(right))
                if starters != true_set:
                    for p, a, t, *_ in rows:
                        if p in true_set - starters:
                            add(acc, ('mm', 'left_out', 'age'), a)
                        if p in starters - true_set:
                            add(acc, ('mm', 'wrongly_in', 'age'), a)
                lineup = [(c, b, s, t, p) for p, a, t, c, b, s in rows]
                assert rerank(lineup, {}) == starters  # the re-ranking code with nothing subtracted is the coach's own selection
                lineups.append(lineup)
    finally:
        le.decide_club_lineup = original
    le._validate_league_invariants(world)
    flat = {f'final_{k}': v for k, v in t030.final_metrics(world).items()}
    flat['events'] = len(world.events)
    return {'acc': acc, 'lineups': lineups, 'flat': flat}


# ---- M4: the age-bin correction, fitted on the other settings ----
def fit_delta(accs, config):
    """accs: the cell accumulators of the fitting settings (one mode). delta[(class, bin)] = ws * (skill error of the bin - the class mean) + wm * (match error ...); bins with n < MIN_BIN_N get 0."""
    pooled = {}
    for a in accs:
        merge(pooled, {k: v for k, v in a.items() if k[0] == 'err'})
    delta = {}
    for cls in CLASSES:
        part = {}
        for kind, w in (('skill', config.select_skill_weight), ('match', config.select_match_weight)):
            rows = [pooled.get(('err', cls, b, kind), [0, 0.0, 0.0, 0.0, 0.0]) for b in range(len(BIN_NAMES))]
            n_all = sum(r[0] for r in rows)
            if not n_all:
                continue
            level = sum(r[1] for r in rows) / n_all
            for b, r in enumerate(rows):
                if r[0] >= MIN_BIN_N:
                    part[b] = part.get(b, 0.0) + w * (r[1] / r[0] - level)
        delta.update({(cls, b): v for b, v in part.items()})
    return delta


def counterfactual(cells_by_setting, config):
    """Adds ('cf','actual') / ('cf','corrected') to every cell: whether the coach's starters / the re-ranked starters are the true top 2, per lineup."""
    for setting, cells in cells_by_setting.items():
        delta = fit_delta([c['acc'] for s, other in cells_by_setting.items() if s != setting for c in other], config)
        for c in cells:
            for lineup in c['lineups']:
                true_set = top2([(r[4], r[3]) for r in lineup])
                add(c['acc'], ('cf', 'actual'), float(top2([(r[4], r[2]) for r in lineup]) == true_set))
                add(c['acc'], ('cf', 'corrected'), float(rerank(lineup, delta) == true_set))


# ---- gates ----
GAME_OPT_IN_FILES = {'league_economy.py'}  # DECISIONS G3/G4/G5: game opt-in keys age_spread/transfer_window_cycle/squad_size (default off, default history byte-identical)


def check_sim_unchanged(root):
    bundle, now = root / FROZEN / 'soccer_sim', root / 'soccer_sim'
    names = sorted(p.name for p in now.glob('*.py'))
    assert names == sorted(p.name for p in bundle.glob('*.py'))
    changed = [n for n in names if (now / n).read_bytes() != (bundle / n).read_bytes()]
    unexpected = [n for n in changed if n not in GAME_OPT_IN_FILES]
    assert not unexpected, unexpected
    return {'files': len(names), 'changed': unexpected}


def check_t037_reproduced(root, pooled_by_setting):
    """Gate 3: lineup statistics rebuilt from the age bins equal T037's saved pooled values."""
    saved = json.loads((root / 'artifacts/t037/run/summary.json').read_text())['settings']
    out = {}
    for t, by_mode in pooled_by_setting.items():
        for m, acc in by_mode.items():
            for g, kind in (('inc', 'skill'), ('inc', 'match'), ('new', 'skill'), ('new', 'match')):
                rows = [acc.get(('err', g, b, kind), [0, 0.0, 0.0, 0.0, 0.0]) for b in range(len(BIN_NAMES))]
                n = sum(r[0] for r in rows)
                want = saved[t]['pooled'][m]['lineup'][f'{g}_{kind}']
                got = {'n': n, 'mean': sum(r[1] for r in rows) / n, 'mae': sum(r[2] for r in rows) / n}
                out[f'{t}_{m}_{g}_{kind}'] = got['n'] == want['n'] and all(abs(got[k] - want[k]) <= 1e-9 for k in ('mean', 'mae'))
            start, want = stat(acc[('start', 'true_top2_match')]), saved[t]['pooled'][m]['start']['true_top2_match']
            out[f'{t}_{m}_start'] = start['n'] == want['n'] and abs(start['mean'] - want['mean']) <= 1e-9
    assert all(out.values()), [k for k, v in out.items() if not v]
    return {'compared': len(out), 'all_equal': True}


# ---- statistics ----
def mean_of(acc, key):
    r = acc.get(key)
    return r[1] / r[0] if r and r[0] else None


def bin_error(acc, cls, bins, kind):
    rows = [acc[('err', cls, b, kind)] for b in bins if ('err', cls, b, kind) in acc]
    n = sum(r[0] for r in rows)
    return sum(r[1] for r in rows) / n if n else None


def scalars(acc):
    """The statistics that get a bootstrap interval (M1 gradient G, M2 difference D, M3 difference E, M4 gain C) plus the top-2 match rate and the ages."""
    def share(key, c):
        r = acc.get((key, c))
        return r[4] / r[0] if r and r[0] else None
    diff = lambda a, b: None if a is None or b is None else a - b
    gain = lambda x, y: diff(mean_of(acc, x), mean_of(acc, y))
    out = {'top2_match': mean_of(acc, ('start', 'true_top2_match')), 'age_starters': mean_of(acc, ('age', 'starters')), 'age_roster': mean_of(acc, ('age', 'roster')),
           'pair_better_newer': share('pair', 'better_newer'), 'pair_better_older': share('pair', 'better_older'), 'pair_close': share('pair', 'close'),
           'D_pair': diff(share('pair', 'better_newer'), share('pair', 'better_older')),
           'ph_D_pair': diff(share('pair_ph', 'better_newer'), share('pair_ph', 'better_older')), 'ph_pair_better_newer': share('pair_ph', 'better_newer'), 'ph_pair_better_older': share('pair_ph', 'better_older'),
           'E_age': gain(('mm', 'wrongly_in', 'age'), ('mm', 'left_out', 'age')), 'C_gain': gain(('cf', 'corrected'), ('cf', 'actual'))}
    for kind in ('skill', 'match'):  # post hoc: benched at the last match (bin 8-13) minus played at the last match (a=7)
        out[f'ph_G_inc_{kind}'] = diff(bin_error(acc, 'inc', (2,), kind), bin_error(acc, 'inc', (1,), kind))
    for cls in ('inc', 'new', 'all'):
        for kind in ('skill', 'match'):
            fresh, stale = bin_error(acc, cls, FRESH, kind), bin_error(acc, cls, STALE, kind)
            out[f'G_{cls}_{kind}'] = diff(stale, fresh)
    return out


def bin_table(acc):
    return {cls: {kind: [{'bin': BIN_NAMES[b], **(stat(acc.get(('err', cls, b, kind))) or {'n': 0})} for b in range(len(BIN_NAMES))] for kind in ('skill', 'match')} for cls in ('inc', 'new', 'other', 'all')}


def bootstrap(cell_accs, boots=1000, seed=38):
    """cell_accs[mode] = accumulators aligned across modes (same cell order). Resamples cells; every mode and difference uses the same resample."""
    rng, n = random.Random(seed), len(cell_accs[MODES[0]])
    draws = {m: [] for m in MODES}
    for _ in range(boots):
        pick = [rng.randrange(n) for _ in range(n)]
        for m in MODES:
            pooled = {}
            for i in pick:
                merge(pooled, cell_accs[m][i])
            draws[m].append(scalars(pooled))
    return draws


def interval(values):
    values = sorted(v for v in values if v is not None)
    return [values[int(0.025 * len(values))], values[int(0.975 * len(values)) - 1]] if values else None


def summarise(cell_accs, boots):
    pooled = {}
    for m in MODES:
        pooled[m] = {}
        for a in cell_accs[m]:
            merge(pooled[m], a)
    point = {m: scalars(pooled[m]) for m in MODES}
    draws = bootstrap(cell_accs, boots)
    out = {'bins': {m: bin_table(pooled[m]) for m in MODES}, 'point': point,
           'ci95': {m: {k: interval([d[k] for d in draws[m]]) for k in point[m]} for m in MODES}, 'difference_vs_legacy': {}}
    for m in MODES[1:]:
        out['difference_vs_legacy'][m] = {k: {'point': None if point[m][k] is None or point['legacy'][k] is None else point[m][k] - point['legacy'][k], 'ci95': interval([None if a[k] is None or b[k] is None else a[k] - b[k] for a, b in zip(draws[m], draws['legacy'])])}
                                          for k in point[m]}
    return out


def evaluate(s):
    """The numbers behind the pre-registered predictions P2-P8 (judged in the RESULTS, not here)."""
    p, d = s['point'], s['difference_vs_legacy']
    inc = lambda m, kind: [x.get('mean') for x in s['bins'][m]['inc'][kind]]
    new_shift = [None if a.get('mean') is None or b.get('mean') is None else a['mean'] - b['mean'] for a, b in zip(s['bins']['shifted_v1']['new']['skill'], s['bins']['legacy']['new']['skill'])]
    enough = [i for i, (a, b) in enumerate(zip(s['bins']['shifted_v1']['new']['skill'], s['bins']['legacy']['new']['skill'])) if min(a['n'], b['n']) >= 200]
    shift_inc = [None if a is None or b is None else a - b for a, b in zip(inc('shifted_v1', 'skill'), inc('legacy', 'skill'))]
    return {'P2': {'G_inc_skill': {m: p[m]['G_inc_skill'] for m in MODES}, 'G_shifted_minus_legacy': d['shifted_v1'].get('G_inc_skill'), 'shift_by_bin_inc_skill': shift_inc},
            'P3': {'D_pair': {m: p[m]['D_pair'] for m in MODES}, 'D_shifted_minus_legacy': d['shifted_v1'].get('D_pair'), 'ph': {'D_pair': {m: p[m]['ph_D_pair'] for m in MODES}, 'G_inc_skill': {m: p[m]['ph_G_inc_skill'] for m in MODES}, 'G_inc_match': {m: p[m]['ph_G_inc_match'] for m in MODES}}},
            'P4': {'E_age': {m: p[m]['E_age'] for m in MODES}, },
            'P5': {'C_gain_legacy': p['legacy']['C_gain'], 'top2_legacy_to_shifted': d['shifted_v1']['top2_match']['point']},
            'P6': {'C_gain': {m: p[m]['C_gain'] for m in MODES[1:]}},
            'P7': {'age_starters': {m: p[m]['age_starters'] for m in MODES}, 'age_roster': {m: p[m]['age_roster'] for m in MODES}},
            'P8': {'new_skill_shift_by_bin': new_shift, 'range_over_bins_with_n>=200': (max(new_shift[i] for i in enough) - min(new_shift[i] for i in enough)) if len(enough) > 1 else None}}


def run(out, root, boots=1000):
    out.mkdir(parents=True, exist_ok=False)
    start = perf_counter()
    gates = {'sim_unchanged_vs_frozen_t037_bundle': check_sim_unchanged(root)}
    print('gates', json.dumps(gates), flush=True)
    refs = {t: {(c['world'], c['seed'], c['start_age']): c for c in json.loads((root / f'artifacts/{t}/run/summary.json').read_text())['cells']} for t in SETTINGS}
    config = le.LeagueEconomyConfig()
    cells = {m: {t: [] for t in SETTINGS} for m in MODES}
    equal = {t: 0 for t in SETTINGS}
    for t in SETTINGS:
        for name in t023.conditions():
            for seed in SEEDS:
                for st in STARTS:
                    for m in MODES:
                        cfg = make_cfg(t, name, st, m)
                        assert (cfg.select_skill_weight, cfg.select_match_weight) == (config.select_skill_weight, config.select_match_weight)
                        cells[m][t].append(run_cell(cfg, seed))
                    leg, ref = cells['legacy'][t][-1]['flat'], refs[t][(name, seed, st)]['on']
                    assert {k[6:]: v for k, v in leg.items() if k.startswith('final_')} == ref['final'] and leg['events'] == ref['events'], (t, name, seed, st)  # gate 2
                    equal[t] += 1
            print(t, name, flush=True)
    gates['legacy_cells_equal_frozen'] = equal
    pooled_by_setting = {t: {m: {} for m in MODES} for t in SETTINGS}
    for m in MODES:
        for t in SETTINGS:
            for c in cells[m][t]:
                merge(pooled_by_setting[t][m], c['acc'])
        counterfactual(cells[m], config)
    gates['t037_reproduced'] = check_t037_reproduced(root, pooled_by_setting)
    cell_accs = {m: [c['acc'] for t in SETTINGS for c in cells[m][t]] for m in MODES}
    setting_summary = {t: summarise({m: [c['acc'] for c in cells[m][t]] for m in MODES}, boots) for t in SETTINGS}
    overall = summarise(cell_accs, boots)
    result = {'task': 'T038', 'identity': le.league_identity(), 'runtime': runtime(), 'seeds': list(SEEDS), 'days': DAYS, 'gates': gates, 'bin_names': BIN_NAMES,
              'all': {**overall, 'evaluation': evaluate(overall)}, 'settings': {t: {**v, 'evaluation': evaluate(v)} for t, v in setting_summary.items()}, 'elapsed_seconds': perf_counter() - start}
    write_json(out / 'summary.json', result)
    print(json.dumps({'equal': equal, 'elapsed_seconds': result['elapsed_seconds']}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    run(parser.parse_args().out.resolve(), CODE_ROOT)
