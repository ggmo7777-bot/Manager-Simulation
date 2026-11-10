"""T039: how does the mean of the observation noise reach the coach's top-2 lineup, for players who played the last match vs the bench? Analysis only: `soccer_sim/` is unchanged and no config is added.
The legacy / shifted_v1 / centered_v1 runs of T037 (3 settings x 84 cells x 168 days) are replayed. The researcher rebuilds every coach's estimates from the recorded observation events
(training sessions, matches, scouting) and, on the legacy runs, keeps in parallel the estimates the same observations would have given with the noise mean removed (shifted = legacy + width), channel by channel.
True skills are read by the researcher after the fact; nothing goes back into the simulation.

Protocol: docs/experiments/T039-PROTOCOL.md. Measures: observation counts per group and channel, the shift of each estimate per channel (static propagation), the top-2 match rate when the offset is removed
(by channel / by component / by group) against the dynamic gain of the shifted and centered runs.
"""
import argparse
import json
from pathlib import Path
import random
import sys
from time import perf_counter

CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT))
from experiments import t023, t030, t036, t038
from soccer_sim import league_economy as le
from soccer_sim.match_2d import clamp, round6
from soccer_sim.storage import runtime, write_json

DAYS, SEEDS, STARTS, SETTINGS = t036.DAYS, t036.SEEDS, t036.STARTS, t036.SETTINGS
MODES = t038.MODES
GROUPS = ('played', 'benched', 'arrived')
CHANNELS = ('session', 'match', 'scout')
COMPS = ('skill', 'match', 'fat')
SHADOWS = ('actual', 'all') + CHANNELS  # 'actual' is the replay of the real estimates; the others add the width to the observations of the named channels
OBS_EVENTS = {'coach_session_observation': 'session', 'coach_match_observation': 'match', 'coach_scout_observation': 'scout'}
FAT_CHANNEL = {'training_coach': 'session', 'match_coach': 'match', 'scout': 'scout'}
OBS_UPPER = 0.98
# variant -> (which counterfactual estimates, which components use them, which groups use them); everything else keeps the real estimate
VARIANTS = {'all': ('all', COMPS, GROUPS), **{c: (c, COMPS, GROUPS) for c in CHANNELS},
            **{f'comp_{c}': ('all', (c,), GROUPS) for c in COMPS}, **{f'grp_{g}': ('all', COMPS, (g,)) for g in GROUPS}}
PAIR_CLASSES = ('better_benched', 'better_played')
EARLY_DECISIONS = 4  # post hoc A looks at each club's first four matches (days 7, 14, 21, 28)
PROMO_KEYS = ('played', 'benched', 'benched_top2', 'played_not_top2')
MIN_TRUE_GAP = t038.MIN_TRUE_GAP
FROZEN = t038.FROZEN
add, merge, stat, top2, mean_of, interval = t038.add, t038.merge, t038.stat, t038.top2, t038.mean_of, t038.interval


def offsets_of(config):
    """The width added to a channel's observations by the mean correction (shifted_v1 = legacy + width)."""
    return {'session': config.coach_noise, 'match': config.match_obs_noise, 'scout': config.scout_noise}


def group_of(pid, prev_roster, prev_starters):
    return 'arrived' if pid not in prev_roster else 'played' if pid in prev_starters else 'benched'


def observe(state, channel, obs, fatigue, shifted_fatigue, config, offsets):
    """One observation event for one (coach, player). state: {shadow: [skill, match, fatigue]}. The formulas are the simulation's own (same order of operations, clamps and round6)."""
    for name, est in state.items():
        moved = name in ('all', channel)
        o = round6(min(OBS_UPPER, obs + offsets[channel])) if moved else obs
        if channel == 'match':
            est[1] = round6(clamp((1.0 - config.coach_match_alpha) * est[1] + config.coach_match_alpha * o, 0.10, 0.98))
            est[0] = round6(clamp((1.0 - config.coach_match_skill_blend) * est[0] + config.coach_match_skill_blend * o, 0.10, 0.98))
        else:
            alpha = config.coach_skill_alpha if channel == 'session' else config.coach_scout_alpha
            est[0] = round6(clamp((1.0 - alpha) * est[0] + alpha * o, 0.10, 0.98))
            if channel == 'scout' or config.coach_practice_rating != 'none':
                est[1] = round6(clamp(0.70 * est[1] + 0.30 * o, 0.10, 0.98))
        est[2] = shifted_fatigue if moved else fatigue


def total(config, skill, match, fatigue):
    """The lineup score as decide_club_lineup computes it."""
    return round6(round6(config.select_skill_weight * skill) + round6(config.select_match_weight * match) - round6(config.select_fatigue_weight * fatigue))


def variant_scores(config, rows, shadow, comps, groups):
    """rows: (id, group, truth, {shadow: estimates}). Components in `comps` of players in `groups` use the `shadow` estimates; every other number is the real estimate."""
    out = []
    for pid, g, _, ests in rows:
        use = ests[shadow] if g in groups else ests['actual']
        skill, match, fatigue = (use[i] if COMPS[i] in comps else ests['actual'][i] for i in range(3))
        out.append((pid, total(config, skill, match, fatigue)))
    return out


def rank_positions(scores):
    return {pid: i for i, (pid, _) in enumerate(sorted(scores, key=lambda kv: (-kv[1], kv[0])))}


def pair_rows(rows, rank):
    """Yields (class, ordered_right) for pairs of one player who played and one who sat at the last match, true skills >= MIN_TRUE_GAP apart."""
    for i, a in enumerate(rows):
        for b in rows[i + 1:]:
            if {a[1], b[1]} != {'played', 'benched'} or abs(a[2] - b[2]) < MIN_TRUE_GAP:
                continue
            better, worse = (a, b) if a[2] > b[2] else (b, a)
            yield ('better_benched' if better[1] == 'benched' else 'better_played'), rank[better[0]] < rank[worse[0]]


def record_decision(acc, config, rows, shadows, true_set, starters):
    """The per-decision statistics of one club's lineup. rows: (id, group, truth, {shadow: estimates}) for the roster."""
    scores = {}
    for name in ('actual',) + (tuple(VARIANTS) if 'all' in shadows else ()):
        shadow, comps, groups = ('actual', COMPS, GROUPS) if name == 'actual' else VARIANTS[name]
        scores[name] = variant_scores(config, rows, shadow, comps, groups)
        add(acc, ('top2', name), float(top2(scores[name]) == true_set))
    for name in ('actual', 'all') if 'all' in shadows else ('actual',):
        rank = rank_positions(scores[name])
        for cls, right in pair_rows(rows, rank):
            add(acc, ('pair', cls, name), 0.0, float(right))
    for pid, g, truth, ests in rows:
        actual = ests['actual']
        if g != 'arrived':  # post hoc C: promotions and demotions
            started = float(pid in starters)
            add(acc, ('promo', g), started)
            if g == 'benched' and pid in true_set:
                add(acc, ('promo', 'benched_top2'), started)
            if g == 'played' and pid not in true_set:
                add(acc, ('promo', 'played_not_top2'), started)
        for gg in (g, 'all'):
            add(acc, ('err', gg, 'skill'), actual[0] - truth)
            add(acc, ('err', gg, 'match'), actual[1] - truth)
            for shadow in shadows[1:]:
                for i, comp in enumerate(COMPS):
                    add(acc, ('shift', shadow, gg, comp), ests[shadow][i] - actual[i])
                add(acc, ('shift', shadow, gg, 'score'), total(config, *ests[shadow]) - total(config, *actual))


# ---- one cell ----
def run_cell(config, seed, days=DAYS):
    legacy = config.observation_noise_mode == 'legacy'
    shadows = SHADOWS if legacy else SHADOWS[:1]
    world = le._build_initial_world(seed=seed, config=config)
    offsets = offsets_of(config)
    states = {(coach_id, pid): {s: [ev.skill_estimate, ev.match_rating_estimate, ev.fatigue_estimate] for s in shadows}
              for coach_id, mind in world.coaches.items() for pid, ev in mind.evaluations.items()}
    counts = {key: dict.fromkeys(CHANNELS, 0) for key in states}
    prev, fatigue_log, acc, seen_by_club = {}, [], {}, {}
    logged = 0
    original = le.observe_fatigue

    def recording(**kw):  # read-only: the real value is returned unchanged; for legacy runs also the value shifted_v1 would give from the same draw
        got = original(**kw)
        if kw['context'] in FAT_CHANNEL:
            fatigue_log.append((kw['observer_id'], kw['player_id'], FAT_CHANNEL[kw['context']], got, original(**{**kw, 'centered': True, 'shifted': True}) if legacy else got))
        return got
    le.observe_fatigue = recording
    try:
        while world.day < days:
            n_events = len(world.events)
            le.advance_league_days(world, 1)
            truth = {}
            true_skill = lambda pid: truth.setdefault(pid, world.players[pid].composite_skill())
            for e in world.events[n_events:]:
                kind, d = e['kind'], e['data']
                if kind == 'season_youth_joined':
                    for coach_id in world.coaches:
                        states[(coach_id, d['player_id'])] = {s: list(le.YOUTH_PRIOR) for s in shadows}
                        counts[(coach_id, d['player_id'])] = dict.fromkeys(CHANNELS, 0)
                elif kind in OBS_EVENTS:
                    channel = OBS_EVENTS[kind]
                    pid = d['target_player_id'] if channel == 'scout' else d['player_id']
                    observer, player, fat_channel, fatigue, shifted_fatigue = fatigue_log[logged]
                    logged += 1
                    assert (observer, player, fat_channel, fatigue) == (e['actor'], pid, channel, d['obs_fatigue']), (e['id'], observer, player, fat_channel)
                    state = states[(e['actor'], pid)]
                    observe(state, channel, d['rating_obs'] if channel == 'match' else d['obs_skill'], fatigue, shifted_fatigue, config, offsets)
                    counts[(e['actor'], pid)][channel] += 1
                    if 'skill_estimate' in d:
                        assert state['actual'][0] == d['skill_estimate'], (e['id'], state['actual'][0], d['skill_estimate'])
                elif kind == 'lineup_decision':
                    club, coach_id = d['club_id'], d['coach_id']
                    prev_roster, prev_starters = prev.get(club, (frozenset(), frozenset()))
                    rows = []
                    for r in d['ranking']:
                        pid, g = r['player_id'], group_of(r['player_id'], prev_roster, prev_starters)
                        ests = states[(coach_id, pid)]
                        c, skill, match, fatigue = r['components'], *ests['actual']
                        assert (round6(config.select_skill_weight * skill), round6(config.select_match_weight * match), round6(-round6(config.select_fatigue_weight * fatigue))) == \
                            (c['skill_term'], c['match_term'], c['fatigue_penalty']) and total(config, skill, match, fatigue) == r['score'], (e['id'], pid)  # gate 3
                        rows.append((pid, g, true_skill(pid), ests))
                        for gg in (g, 'all'):
                            for channel in CHANNELS:
                                add(acc, ('cnt', gg, channel), counts[(coach_id, pid)][channel])
                        counts[(coach_id, pid)] = dict.fromkeys(CHANNELS, 0)
                    true_set = top2([(pid, truth_value) for pid, _, truth_value, _ in rows])
                    assert top2([(r['player_id'], r['score']) for r in d['ranking']]) == set(d['starters'])
                    add(acc, ('start', 'true_top2_match'), float(set(d['starters']) == true_set))
                    record_decision(acc, config, rows, shadows, true_set, set(d['starters']))
                    if len(seen_by_club.setdefault(club, [])) < EARLY_DECISIONS:  # post hoc A: the state at each club's first decisions, for the legacy-shadow vs shifted-run diagnostic
                        seen_by_club[club].append({pid: {name: list(est) for name, est in ests.items()} for pid, _, _, ests in rows})
                    prev[club] = (frozenset(r['player_id'] for r in d['ranking']), frozenset(d['starters']))
    finally:
        le.observe_fatigue = original
    assert logged == len(fatigue_log)
    le._validate_league_invariants(world)
    assert acc[('top2', 'actual')][:2] == acc[('start', 'true_top2_match')][:2]  # the replayed scores pick the coach's own starters
    flat = {f'final_{k}': v for k, v in t030.final_metrics(world).items()}
    flat['events'] = len(world.events)
    return {'acc': acc, 'flat': flat, 'early': seen_by_club}


# ---- gates ----
check_sim_unchanged = t038.check_sim_unchanged


def check_t037_reproduced(root, pooled_by_setting):
    """Gate 4: the top-2 match rate (n, mean) of every mode and setting equals T037's saved value."""
    saved = json.loads((root / 'artifacts/t037/run/summary.json').read_text())['settings']
    out = {}
    for t, by_mode in pooled_by_setting.items():
        for m, acc in by_mode.items():
            got, want = stat(acc[('start', 'true_top2_match')]), saved[t]['pooled'][m]['start']['true_top2_match']
            out[f'{t}_{m}'] = got['n'] == want['n'] and abs(got['mean'] - want['mean']) <= 1e-9
    assert all(out.values()), [k for k, v in out.items() if not v]
    return {'compared': len(out), 'all_equal': True}


def check_shadow_vs_shifted_run(cells_by_setting, tolerance=2e-6):
    """Post hoc A: at a club's k-th match the legacy 'all' estimates equal the shifted_v1 run's real estimates unless the histories have already diverged (counted per k)."""
    out = {}
    for t, by_mode in cells_by_setting.items():
        counts = [{'rows': 0, 'agree': 0} for _ in range(EARLY_DECISIONS)]
        for legacy_cell, shifted_cell in zip(by_mode['legacy'], by_mode['shifted_v1']):
            for club, snapshots in legacy_cell['early'].items():
                for k, players in enumerate(snapshots):
                    for pid, ests in players.items():
                        counts[k]['rows'] += 1
                        counts[k]['agree'] += max(abs(x - y) for x, y in zip(ests['all'], shifted_cell['early'][club][k][pid]['actual'])) <= tolerance
        out[t] = counts
    return out


# ---- statistics ----
def pool(accs):
    out = {}
    for acc in accs:
        for k, v in acc.items():
            r = out.get(k)
            if r is None:
                out[k] = list(v)
            else:
                r[0] += v[0]; r[1] += v[1]; r[2] += v[2]; r[3] += v[3]; r[4] += v[4]
    return out


def diff(a, b):
    return None if a is None or b is None else a - b


def share(acc, key):
    r = acc.get(key)
    return r[4] / r[0] if r and r[0] else None


def scalars(p):
    """p[mode] = pooled accumulators. Every statistic that gets a bootstrap interval (static effects C, dynamic gains D, shifts, counts, pair accuracy, realized shifts)."""
    leg, shifted, centered = p['legacy'], p['shifted_v1'], p['centered_v1']
    out = {'top2_legacy': mean_of(leg, ('top2', 'actual')), 'top2_shifted': mean_of(shifted, ('top2', 'actual')), 'top2_centered': mean_of(centered, ('top2', 'actual'))}
    out['D_shifted'], out['D_centered'] = diff(out['top2_shifted'], out['top2_legacy']), diff(out['top2_centered'], out['top2_legacy'])
    for v in VARIANTS:
        out[f'top2_{v}'] = mean_of(leg, ('top2', v))
        out[f'C_{v}'] = diff(out[f'top2_{v}'], out['top2_legacy'])
    out['A_additivity'] = sum(out[f'C_{c}'] for c in CHANNELS) - out['C_all']
    out['F_shifted'] = out['C_all'] / out['D_shifted'] if out['D_shifted'] else None
    out['F_centered'] = out['C_all'] / out['D_centered'] if out['D_centered'] else None
    out['Cmatch_minus_Csession'] = out['C_match'] - out['C_session']
    out['absC_fat_minus_max_other'] = abs(out['C_comp_fat']) - max(abs(out['C_comp_skill']), abs(out['C_comp_match']))
    out['Cbenched_minus_Cplayed'] = out['C_grp_benched'] - out['C_grp_played']
    for shadow in SHADOWS[1:]:
        for g in GROUPS + ('all',):
            for comp in COMPS + ('score',):
                out[f'shift_{shadow}_{g}_{comp}'] = mean_of(leg, ('shift', shadow, g, comp))
    for comp in COMPS + ('score',):
        out[f'gap_{comp}'] = diff(out[f'shift_all_benched_{comp}'], out[f'shift_all_played_{comp}'])
    for mode, acc in p.items():
        for g in GROUPS + ('all',):
            for comp in ('skill', 'match'):
                out[f'err_{mode}_{g}_{comp}'] = mean_of(acc, ('err', g, comp))
            for channel in CHANNELS:
                out[f'cnt_{mode}_{g}_{channel}'] = mean_of(acc, ('cnt', g, channel))
        for g in GROUPS:
            out[f'n_{mode}_{g}'] = acc.get(('cnt', g, 'session'), [0])[0]
    for mode in ('shifted_v1', 'centered_v1'):
        for g in GROUPS + ('all',):
            for comp in ('skill', 'match'):
                out[f'real_{mode}_{g}_{comp}'] = diff(out[f'err_{mode}_{g}_{comp}'], out[f'err_legacy_{g}_{comp}'])
        for comp in ('skill', 'match'):
            out[f'real_gap_{mode}_{comp}'] = diff(out[f'real_{mode}_benched_{comp}'], out[f'real_{mode}_played_{comp}'])
    out['cnt_session_ratio_legacy'] = out['cnt_legacy_benched_session'] / out['cnt_legacy_played_session'] if out['cnt_legacy_played_session'] else None
    for cls in PAIR_CLASSES:
        for name, acc in (('legacy', leg), ('shifted_v1', shifted), ('centered_v1', centered)):
            out[f'pair_{cls}_{name}'] = share(acc, ('pair', cls, 'actual'))
        out[f'pair_{cls}_legacy_all'] = share(leg, ('pair', cls, 'all'))
        out[f'pair_{cls}_n'] = leg.get(('pair', cls, 'actual'), [0])[0]
    for mode, acc in p.items():
        for key in PROMO_KEYS:
            out[f'promo_{mode}_{key}'] = mean_of(acc, ('promo', key))
    for mode in ('shifted_v1', 'centered_v1'):
        for key in PROMO_KEYS:
            out[f'real_promo_{mode}_{key}'] = diff(out[f'promo_{mode}_{key}'], out[f'promo_legacy_{key}'])
        for g in ('played', 'benched', 'all'):
            for channel in CHANNELS:
                out[f'real_cnt_{mode}_{g}_{channel}'] = diff(out[f'cnt_{mode}_{g}_{channel}'], out[f'cnt_legacy_{g}_{channel}'])
    out['static_vs_realized_gap_ratio'] = out['gap_skill'] / out['real_gap_shifted_v1_skill'] if out['real_gap_shifted_v1_skill'] else None
    return out


def bootstrap(cell_accs, boots, seed=39):
    """cell_accs[mode] = accumulators aligned across modes (same cell order). Resamples cells; every mode, variant and difference uses the same resample."""
    rng, n = random.Random(seed), len(cell_accs[MODES[0]])
    draws = []
    for _ in range(boots):
        pick = [rng.randrange(n) for _ in range(n)]
        draws.append(scalars({m: pool(cell_accs[m][i] for i in pick) for m in MODES}))
    return draws


def summarise(cell_accs, boots):
    point = scalars({m: pool(cell_accs[m]) for m in MODES})
    draws = bootstrap(cell_accs, boots)
    return {'point': point, 'ci95': {k: interval([d[k] for d in draws]) for k in point}}


def run(out, root, boots=1000):
    out.mkdir(parents=True, exist_ok=False)
    start = perf_counter()
    gates = {'sim_unchanged_vs_frozen_t037_bundle': check_sim_unchanged(root)}
    print('gates', json.dumps(gates), flush=True)
    refs = {t: {(c['world'], c['seed'], c['start_age']): c for c in json.loads((root / f'artifacts/{t}/run/summary.json').read_text())['cells']} for t in SETTINGS}
    cells = {m: {t: [] for t in SETTINGS} for m in MODES}
    equal = {t: 0 for t in SETTINGS}
    for t in SETTINGS:
        for name in t023.conditions():
            for seed in SEEDS:
                for st in STARTS:
                    for m in MODES:
                        cells[m][t].append(run_cell(t036.make_cfg(t, name, st, m), seed))
                    leg, ref = cells['legacy'][t][-1]['flat'], refs[t][(name, seed, st)]['on']
                    assert {k[6:]: v for k, v in leg.items() if k.startswith('final_')} == ref['final'] and leg['events'] == ref['events'], (t, name, seed, st)  # gate 2
                    equal[t] += 1
            print(t, name, flush=True)
    gates['legacy_cells_equal_frozen'] = equal
    gates['replay_equals_recorded_decisions'] = {'runs': 756, 'decisions_per_mode': sum(c['acc'][('top2', 'actual')][0] for t in SETTINGS for c in cells['legacy'][t])}  # run_cell asserts every decision (gate 3)
    diagnostics = {'shadow_all_vs_shifted_run_first_match': check_shadow_vs_shifted_run({t: {m: cells[m][t] for m in MODES} for t in SETTINGS})}
    gates['t037_reproduced'] = check_t037_reproduced(root, {t: {m: pool(c['acc'] for c in cells[m][t]) for m in MODES} for t in SETTINGS})
    result = {'task': 'T039', 'identity': le.league_identity(), 'runtime': runtime(), 'seeds': list(SEEDS), 'days': DAYS, 'gates': gates, 'diagnostics': diagnostics, 'variants': list(VARIANTS),
              'all': summarise({m: [c['acc'] for t in SETTINGS for c in cells[m][t]] for m in MODES}, boots),
              'settings': {t: summarise({m: [c['acc'] for c in cells[m][t]] for m in MODES}, boots) for t in SETTINGS}}
    result['elapsed_seconds'] = perf_counter() - start
    write_json(out / 'summary.json', result)
    print(json.dumps({'equal': equal, 'elapsed_seconds': result['elapsed_seconds']}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    run(parser.parse_args().out.resolve(), CODE_ROOT)
