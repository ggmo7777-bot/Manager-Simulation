"""T046: how does the U-shape of the lineup match shrink as the coach's estimate lag is removed in steps (dose-response), for which players does removing the lag matter (starters, bench, the two boundary players),
and does the coach's fatigue estimate explain the small slope that stays after the lag is fully removed?
Analysis only: `soccer_sim/` is unchanged and no config is added. T045's swap, generalised: at the match-day lineup decision only, a copy of the coach's evaluations is read in which the chosen players' skill and match-rating estimates
are R - lam * lag (lag = Z - T, T = the true skill at the decision; Z from T044's shadow estimates), the other players keep their real estimates, and (variant nolagfat) the fatigue estimate is the true fatigue at the decision.
The players are chosen by the ranking the real estimates give (starters, bench = the rest, boundary = last starter and first bench). True values reach the lineup only through this registered swap.

Variants: none, d25, d50, d75, nolag (all players, lam 0.25/0.5/0.75/1), st, be, bd (lam 1 for the starters / the bench / the boundary pair), nolagfat (nolag + true fatigue).
Protocol: docs/experiments/T046-PROTOCOL.md. Cells: 14 worlds x seeds {100,101,102} x start age {29,33}, three settings, 168 days.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
from time import perf_counter
from types import SimpleNamespace

CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT))
from experiments import t023, t030, t036, t038, t040, t043, t044, t045
from soccer_sim import league_economy as le
from soccer_sim.match_2d import round6
from soccer_sim.storage import runtime, write_json

DAYS, SEEDS, STARTS, SETTINGS = t045.DAYS, t045.SEEDS, t045.STARTS, t045.SETTINGS
DIRECTIONS = t045.DIRECTIONS
POINTS_FULL = t045.POINTS
POINTS_SWAPPED = tuple(p for p in POINTS_FULL if p != 'base')
VALLEY, HIGH_END, LOW_END = t045.VALLEY, t045.HIGH_END, t045.LOW_END
CONTRASTS = t045.CONTRASTS
# variant -> (share of the lag removed, whose estimates, true fatigue instead of the fatigue estimate)
VARIANT_SPEC = {'none': (0.0, 'all', False), 'd25': (0.25, 'all', False), 'd50': (0.50, 'all', False), 'd75': (0.75, 'all', False), 'nolag': (1.0, 'all', False),
                'st': (1.0, 'starters', False), 'be': (1.0, 'bench', False), 'bd': (1.0, 'boundary', False), 'nolagfat': (1.0, 'all', True)}
VARIANTS = tuple(VARIANT_SPEC)
SWAPPED = tuple(v for v in VARIANTS if v != 'none')
DOSE_VARIANTS = ('d25', 'd50', 'd75')
DOSE_ORDER = ('none', *DOSE_VARIANTS, 'nolag')
TARGET_VARIANTS = ('st', 'be', 'bd')
STARTERS = 2
ARM_SEP = '__'
ARMS = {f'{d}_{p}{ARM_SEP}{v}': (d, p, v) for d in DIRECTIONS for v in VARIANTS for p in (POINTS_FULL if v == 'none' else POINTS_SWAPPED)}
SWAP_TOLERANCE = t045.SWAP_TOLERANCE
REPRODUCED_T045 = tuple(t045.METRICS)
DEV_LIMIT, CARRIES, SL_EXPLAINS = 0.10, 0.70, 0.5
SEED_BOOT = 41
add, sub, mean_of, ratio, interval = t045.add, t045.sub, t045.mean_of, t045.ratio, t045.interval
merged, excludes_zero = t045.merged, t045.excludes_zero
plain_digest, check_sim_unchanged, check_t037_reproduced = t045.plain_digest, t045.check_sim_unchanged, t045.check_t037_reproduced
METRICS = {**t045.METRICS, 'abs_f': ('x', 'abs_f'), 'bias_f': ('x', 'bias_f')}


# ---- the swap ----
def lagged_estimates(estimates, truth, lam):
    """The (skill, match) estimates the lineup reads for a player whose lag is removed by the share `lam`: R - lam * (Z - T). lam = 1 is written as R - Z + T (T045's `nolag`, bit for bit).
    `estimates` = the shadow estimates {R, A, I, Z} -> (skill, match). Checked against the four-term split at every use."""
    out = []
    for index in range(2):
        real, ideal_start = estimates['R'][index], estimates['Z'][index]
        value = real - ideal_start + truth if lam == 1 else real - lam * (ideal_start - truth)
        assert abs(value - (real - lam * t044.telescope(estimates, index, truth)['lag'])) <= SWAP_TOLERANCE, (lam, index, value)
        out.append(value)
    return tuple(out)


def lag_targets(ranked, who):
    """The players whose lag is removed. `ranked` = the player ids in the order the real estimates rank them (best first)."""
    return {'all': ranked, 'starters': ranked[:STARTERS], 'bench': ranked[STARTERS:], 'boundary': ranked[STARTERS - 1:STARTERS + 1]}[who]


class Lineups(t045.Interventions):
    """T045's read-only record, plus the fatigue term of the ranking and the decision-moment true fatigue."""

    def __init__(self, world, acc):
        super().__init__(world, acc)
        self.executed_fatigue, self.true_fatigue = {}, {}
        self.checked.update(fatigue_terms=0, target_sets=0, untouched_players=0)

    def lineup(self, world, event):
        for row in event['data']['ranking']:
            pid = row['player_id']
            assert self.true_fatigue[pid] == world.players[pid].fatigue, (event['id'], pid)  # the state at the lineup event is the state at the decision
            assert round6(-round6(self.config.select_fatigue_weight * self.executed_fatigue[pid])) == row['components']['fatigue_penalty'], (event['id'], pid)
            self.checked['fatigue_terms'] += 1
        super().lineup(world, event)
        self.executed_fatigue, self.true_fatigue = {}, {}


@contextmanager
def lineup_swap(world, tracker, variant):
    """On match days, the lineup reads a copy of the coach's evaluations with the swapped estimates (see the module doc). Everything else, and the transfer decisions, call the real function.
    The real function is put back on exit, also after an exception."""
    lam, who, true_fatigue_swap = VARIANT_SPEC[variant]
    original = le.decide_club_lineup

    def decide(*, coach, roster, config):
        if sys._getframe(1).f_code.co_name != t045.MATCH_DAY_CALLER:
            return original(coach=coach, roster=roster, config=config)
        real = original(coach=coach, roster=roster, config=config)  # no side effects: the ranking the real estimates give
        targets = set(lag_targets([row['player_id'] for row in real['ranking']], who)) if lam else set()
        assert len(targets) == ({'all': len(roster), 'starters': STARTERS, 'bench': len(roster) - STARTERS, 'boundary': 2}[who] if lam else 0)
        evaluations, executed, truths, true_fatigue, executed_fatigue = {}, {}, {}, {}, {}
        errors = ([], [], [])
        for pid in roster:
            ev = coach.evaluations[pid]
            truths[pid], true_fatigue[pid] = world.players[pid].composite_skill(), world.players[pid].fatigue
            estimates = tracker.cells[coach.coach_id, pid]['est']
            if pid in targets:
                executed[pid] = lagged_estimates(estimates, truths[pid], lam)
            else:  # untouched: the real estimates, which the shadow record must equal
                executed[pid] = (ev.skill_estimate, ev.match_rating_estimate)
                assert all(abs(estimates['R'][i] - executed[pid][i]) <= SWAP_TOLERANCE for i in range(2)), (coach.coach_id, pid)
                tracker.checked['untouched_players'] += 1
            executed_fatigue[pid] = true_fatigue[pid] if true_fatigue_swap else ev.fatigue_estimate
            evaluations[pid] = SimpleNamespace(skill_estimate=executed[pid][0], match_rating_estimate=executed[pid][1], fatigue_estimate=executed_fatigue[pid], last_obs_ref=ev.last_obs_ref)
            errors[0].append(abs(executed[pid][0] - truths[pid]))
            errors[1].append(abs(executed[pid][1] - truths[pid]))
            errors[2].append(executed_fatigue[pid] - true_fatigue[pid])
        tracker.executed, tracker.executed_fatigue, tracker.true_fatigue = executed, executed_fatigue, true_fatigue
        tracker.checked['swapped_decisions'] += 1
        tracker.checked['target_sets'] += 1
        lineup = original(coach=SimpleNamespace(club_id=coach.club_id, coach_id=coach.coach_id, evaluations=evaluations), roster=roster, config=config)
        add(tracker.acc, ('start', 'top2_now'), float(set(lineup['starters']) == t038.top2(list(truths.items()))))
        add(tracker.acc, ('x', 'abs_s'), mean_of(errors[0]))
        add(tracker.acc, ('x', 'abs_m'), mean_of(errors[1]))
        add(tracker.acc, ('x', 'abs_f'), mean_of(abs(e) for e in errors[2]))
        add(tracker.acc, ('x', 'bias_f'), mean_of(errors[2]))
        return lineup

    le.decide_club_lineup = decide
    try:
        yield
    finally:
        le.decide_club_lineup = original


# ---- one run ----
def split_arm(arm):
    direction, point, variant = ARMS[arm]
    return f'{direction}_{point}', variant


def run_arm(setting, name, seed, start, arm, days=DAYS):
    base_arm, variant = split_arm(arm)
    direction, kind, param = t043.ARMS[base_arm]
    config = t036.make_cfg(setting, name, start, DIRECTIONS[direction][0])
    acc, prev, real_lineup = {}, {}, le.decide_club_lineup
    world = le._build_initial_world(seed=seed, config=config)
    tracker = Lineups(world, acc)
    with t043.installed(None, kind, param, 0.0, acc), t044.recording(tracker), lineup_swap(world, tracker, variant):
        while world.day < days:
            n_events = len(world.events)
            le.advance_league_days(world, 1)
            events = world.events[n_events:]
            tracker.end_of_day(world)
            truth = {}
            truth_of = lambda pid: truth.setdefault(pid, world.players[pid].composite_skill())
            t040.record_day(acc, events, truth_of, prev)
            t043.record_practice(acc, events)
            t043.record_mediators(acc, config, events, truth_of)
            add(acc, ('fat', 'mean'), mean_of(world.players[pid].fatigue for pid in le._active_pids(world)))
    assert not tracker.executed
    assert le.decide_club_lineup is real_lineup  # the swap has been put back
    le._validate_league_invariants(world)
    flat = {f'final_{k}': v for k, v in t030.final_metrics(world).items()}
    flat['events'] = len(world.events)
    for key in t040.FINALS:
        add(acc, ('final', key), flat[f'final_{key}'])
    return {'acc': acc, 'flat': flat, 'checked': tracker.checked, 'world': world}


def run_cell(task):
    setting, name, seed, start = task
    out = {arm: {key: value for key, value in run_arm(setting, name, seed, start, arm).items() if key != 'world'} for arm in ARMS}
    plain = le.run_league_world(seed=seed, days=DAYS, config=t036.make_cfg(setting, name, start, 'centered_v1'))
    out['plain_centered'] = {'flat': {**{f'final_{k}': v for k, v in t030.final_metrics(plain).items()}, 'events': len(plain.events)},
                             'decisions': sum(1 for e in plain.events if e['kind'] == 'lineup_decision')}
    return task, out


# ---- gates ----
def check_swap_is_a_read_only_copy(days=DAYS):
    """With the identity swap, base equals the plain world and o(+0.010) equals T043's arm without any recorder, snapshot for snapshot; every swap puts the real function back;
    every other variant changes some world (the swap is effective)."""
    names, count, changed = t023.conditions(), 0, dict.fromkeys(SWAPPED, 0)
    for setting, index, seed, start in t040.IDENTITY_PICKS:
        for direction, (world_mode, _) in DIRECTIONS.items():
            real = le.decide_club_lineup
            plain = le.run_league_world(seed=seed, days=days, config=t036.make_cfg(setting, names[index], start, world_mode))
            assert plain_digest(run_arm(setting, names[index], seed, start, f'{direction}_base{ARM_SEP}none', days=days)['world']) == plain_digest(plain), (setting, index, seed, start, direction)
            reference = plain_digest(t043.run_arm(setting, names[index], seed, start, t043.ARMS[f'{direction}_o+0.0100'], days=days)['world'])
            assert plain_digest(run_arm(setting, names[index], seed, start, f'{direction}_o+0.0100{ARM_SEP}none', days=days)['world']) == reference, (setting, index, seed, start, direction)
            count += 3
            for variant in SWAPPED:
                swapped = run_arm(setting, names[index], seed, start, f'{direction}_o+0.0100{ARM_SEP}{variant}', days=days)['world']
                changed[variant] += plain_digest(swapped) != reference
                assert le.decide_club_lineup is real
                count += 1
    assert all(changed.values()), changed
    return {'runs': count, 'all_equal': True, 'changed_worlds_by_variant': changed, 'worlds': len(t040.IDENTITY_PICKS) * len(DIRECTIONS)}


def check_saved_levels(root, task, levels_by_scope, variants, metrics):
    """Every metric of the given variants' arms has the level the earlier task saved (settings and total), to 1e-12. The earlier task names its arms `{direction}_{point}__{variant}` too (T045)."""
    saved = json.loads((root / f'artifacts/{task}/run/summary.json').read_text())['summaries']
    worst, arms = 0.0, set()
    for scope, by_arm in levels_by_scope.items():
        for arm, levels in by_arm.items():
            if split_arm(arm)[1] not in variants:
                continue
            arms.add(arm)
            for metric in metrics:
                worst = max(worst, abs(levels[metric] - saved[scope]['point'][f'level|{arm}|{metric}']))
    assert worst <= 1e-12, worst
    return {'max_abs_difference': worst, 'metrics': len(metrics), 'arms': len(arms)}


def check_fatigue_swap_is_exact(levels):
    """In `nolagfat` the fatigue estimate the lineup reads is the true fatigue: error exactly 0 in every decision."""
    for arm, by_metric in levels.items():
        if split_arm(arm)[1] == 'nolagfat':
            assert by_metric['abs_f'] == 0.0 and by_metric['bias_f'] == 0.0, (arm, by_metric['abs_f'])
    return {'nolagfat_arms': sum(split_arm(arm)[1] == 'nolagfat' for arm in levels)}


# ---- statistics ----
def arm_of(direction, point, variant):
    return f'{direction}_{point}{ARM_SEP}{variant}'


def effects(v):
    """v[arm][metric] = level. Levels; the three contrasts of every metric per direction and variant; U = the mean of LO and HI (top2); SL = top2(o-0.030) - top2(o+0.030); the removed share of U, LO, HI and of the valley level
    relative to `nolag`; the deviation of the dose variants from the proportional share; the additivity of starters and bench; the change of SL by the true fatigue estimate."""
    out = {f'level|{arm}|{m}': value for arm, levels in v.items() for m, value in levels.items()}
    for d in DIRECTIONS:
        level = lambda variant, point, m='top2': v[arm_of(d, point, variant)][m]
        for variant in VARIANTS:
            for name, (high, low) in CONTRASTS.items():
                for m in METRICS:
                    out[f'contrast|{d}|{variant}|{name}|{m}'] = sub(level(variant, high, m), level(variant, low, m))
            out[f'U|{d}|{variant}'] = mean_of([out[f'contrast|{d}|{variant}|LO|top2'], out[f'contrast|{d}|{variant}|HI|top2']])
            out[f'SL|{d}|{variant}'] = sub(level(variant, HIGH_END), level(variant, LOW_END))
            curve = [level(variant, p) for p in POINTS_SWAPPED]
            out[f'range4|{d}|{variant}'] = max(curve) - min(curve)
            out[f'mean4|{d}|{variant}'] = mean_of(curve)
        out[f'den|{d}|U'] = sub(out[f'U|{d}|none'], out[f'U|{d}|nolag'])
        parts = {'U': lambda x: out[f'U|{d}|{x}'], 'LO': lambda x: out[f'contrast|{d}|{x}|LO|top2'], 'HI': lambda x: out[f'contrast|{d}|{x}|HI|top2']}
        for variant in SWAPPED:
            for part, value_of in parts.items():
                out[f'share|{d}|{variant}|{part}'] = ratio(sub(value_of('none'), value_of(variant)), sub(value_of('none'), value_of('nolag')))
            out[f'vshare|{d}|{variant}'] = ratio(sub(level(variant, VALLEY), level('none', VALLEY)), sub(level('nolag', VALLEY), level('none', VALLEY)))
            out[f'dU|{d}|{variant}'] = sub(out[f'U|{d}|{variant}'], out[f'U|{d}|none'])
        for variant in DOSE_VARIANTS:
            out[f'dev|{d}|{variant}'] = sub(out[f'share|{d}|{variant}|U'], VARIANT_SPEC[variant][0])
        shares = {variant: out[f'share|{d}|{variant}|U'] for variant in ('st', 'be', 'bd')}
        out[f'additivity|{d}'] = sub(shares['st'] + shares['be'], 1.0) if None not in (shares['st'], shares['be']) else None
        out[f'bd_minus_other|{d}'] = sub(shares['bd'], max(shares['st'], shares['be'])) if None not in shares.values() else None
        out[f'dSL|{d}'] = sub(out[f'SL|{d}|nolagfat'], out[f'SL|{d}|nolag'])
        out[f'SLratio|{d}'] = ratio(out[f'SL|{d}|nolagfat'], out[f'SL|{d}|nolag'])
        out[f'mean4diff|{d}'] = sub(out[f'mean4|{d}|nolagfat'], out[f'mean4|{d}|nolag'])
        out[f'abs_f_slope|{d}'] = sub(level('none', LOW_END, 'abs_f'), level('none', HIGH_END, 'abs_f'))
    return out


# ---- the pre-registered rules ----
def verdict_across_directions(by_direction):
    labels = set(by_direction.values())
    return labels.pop() if len(labels) == 1 else ('undefined' if 'undefined' in labels else 'differs')


def classify_dose(point):
    """linear / front_loaded / back_loaded / mixed per direction from the deviations of the removed share of U from the dose; the overall verdict when both directions agree, `differs` otherwise."""
    by_direction, monotone = {}, {}
    for d in DIRECTIONS:
        monotone[d] = all(point[f'U|{d}|{a}'] > point[f'U|{d}|{b}'] for a, b in zip(DOSE_ORDER, DOSE_ORDER[1:]))
        if point[f'den|{d}|U'] is None or point[f'den|{d}|U'] <= 0:
            by_direction[d] = 'undefined'
            continue
        devs = [point[f'dev|{d}|{variant}'] for variant in DOSE_VARIANTS]
        if all(abs(x) <= DEV_LIMIT for x in devs):
            by_direction[d] = 'linear'
        elif all(x >= -DEV_LIMIT for x in devs):
            by_direction[d] = 'front_loaded'
        elif all(x <= DEV_LIMIT for x in devs):
            by_direction[d] = 'back_loaded'
        else:
            by_direction[d] = 'mixed'
    return {'verdict': verdict_across_directions(by_direction), 'by_direction': by_direction, 'monotone': monotone}


def classify_carrier(point):
    """The target with the largest removed share of U; it `carries` the effect when that share is at least 0.70, else `diffuse`."""
    by_direction, top = {}, {}
    for d in DIRECTIONS:
        if point[f'den|{d}|U'] is None or point[f'den|{d}|U'] <= 0:
            by_direction[d] = 'undefined'
            continue
        shares = {variant: point[f'share|{d}|{variant}|U'] for variant in TARGET_VARIANTS}
        top[d] = max(shares, key=shares.get)
        by_direction[d] = top[d] if shares[top[d]] >= CARRIES else 'diffuse'
    return {'verdict': verdict_across_directions(by_direction), 'by_direction': by_direction}


def classify_residual(point, ci):
    """The candidate (the fatigue estimate): `no_residual` when SL after the full lag removal has no interval above 0 in both directions; else explains / partial / grows / unchanged."""
    if not all(ci[f'SL|{d}|nolag'][0] > 0 for d in DIRECTIONS):
        return {'verdict': 'no_residual'}
    shrinks = all(ci[f'dSL|{d}'][1] < 0 for d in DIRECTIONS)
    if shrinks and all(point[f'SLratio|{d}'] <= SL_EXPLAINS for d in DIRECTIONS):
        return {'verdict': 'explains'}
    if shrinks:
        return {'verdict': 'partial'}
    return {'verdict': 'grows' if any(ci[f'dSL|{d}'][0] > 0 for d in DIRECTIONS) else 'unchanged'}


def summarise_job(job):
    scope, cell_accs, boots = job
    return scope, t045.summarise(cell_accs, boots, effects, METRICS)


def level_of(acc, key):
    return acc[key][1] / acc[key][0]


def run_decide(out, root, boots=1000, workers=None):
    out.mkdir(parents=True, exist_ok=False)
    start = perf_counter()
    gates = {'sim_unchanged_vs_frozen_t037_bundle': check_sim_unchanged(root), 'swap_is_a_read_only_copy': check_swap_is_a_read_only_copy()}
    print('gates', json.dumps(gates), flush=True)
    refs = {t: {(c['world'], c['seed'], c['start_age']): c for c in json.loads((root / f'artifacts/{t}/run/summary.json').read_text())['cells']} for t in SETTINGS}
    tasks = [(t, name, seed, st) for t in SETTINGS for name in t023.conditions() for seed in SEEDS for st in STARTS]
    cells = {t: [] for t in SETTINGS}
    equal = {t: 0 for t in SETTINGS}
    with ProcessPoolExecutor(max_workers=workers or os.cpu_count()) as pool:
        for count, ((setting, name, seed, st), got) in enumerate(pool.map(run_cell, tasks, chunksize=1), start=1):
            cells[setting].append(got)
            legacy, ref = got[f'fwd_base{ARM_SEP}none']['flat'], refs[setting][(name, seed, st)]['on']
            assert {k[6:]: v for k, v in legacy.items() if k.startswith('final_')} == ref['final'] and legacy['events'] == ref['events'], (setting, name, seed, st)
            centered, plain = got[f'rev_base{ARM_SEP}none']['flat'], got['plain_centered']
            assert centered == plain['flat'] and got[f'rev_base{ARM_SEP}none']['acc'][('start', 'top2')][0] == plain['decisions'], (setting, name, seed, st)
            equal[setting] += 1
            if count % 12 == 0:
                print(count, len(tasks), round(perf_counter() - start), flush=True)
    gates['base_arms_equal_plain_runs'] = equal
    scopes = {'all': [c for t in SETTINGS for c in cells[t]], **cells}
    pooled = {scope: {arm: merged(c[arm]['acc'] for c in group) for arm in ARMS} for scope, group in scopes.items()}
    levels = {scope: {arm: {m: level_of(by_arm[arm], key) for m, key in METRICS.items()} for arm in ARMS} for scope, by_arm in pooled.items()}
    gates['t037_reproduced'] = check_t037_reproduced(root, {t: {'fwd_base': pooled[t][f'fwd_base{ARM_SEP}none'], 'rev_base': pooled[t][f'rev_base{ARM_SEP}none']} for t in SETTINGS})
    gates['t044_reproduced'] = t045.check_t044_reproduced(root, {scope: {f'{d}_{p}': {m: by_arm[f'{d}_{p}{ARM_SEP}none'][m] for m in t045.REPRODUCED} for d in DIRECTIONS for p in POINTS_FULL} for scope, by_arm in levels.items()})
    gates['t045_reproduced'] = check_saved_levels(root, 't045', levels, ('none', 'nolag'), REPRODUCED_T045)
    gates['fatigue_swap_exact'] = check_fatigue_swap_is_exact(levels['all'])
    gates['invariants_validated_runs'] = len(tasks) * len(ARMS)
    gates['shadow_checks_total'] = {key: sum(cell[arm]['checked'][key] for t in SETTINGS for cell in cells[t] for arm in ARMS) for key in cells['t030'][0][f'fwd_base{ARM_SEP}none']['checked']}
    jobs = [(scope, {arm: [c[arm]['acc'] for c in group] for arm in ARMS}, boots) for scope, group in scopes.items()]
    with ProcessPoolExecutor(max_workers=min(len(jobs), workers or os.cpu_count())) as pool:
        summaries = dict(pool.map(summarise_job, jobs))
    verdicts = {scope: {'dose': classify_dose(s['point']), 'carrier': classify_carrier(s['point']), 'residual': classify_residual(s['point'], s['ci95'])} for scope, s in summaries.items()}
    result = {'task': 'T046', 'identity': le.league_identity(), 'runtime': runtime(), 'seeds': list(SEEDS), 'days': DAYS, 'variants': {v: dict(zip(('lam', 'who', 'true_fatigue'), spec)) for v, spec in VARIANT_SPEC.items()},
              'contrasts': CONTRASTS, 'arms': {a: {'direction': d, 'point': p, 'variant': v} for a, (d, p, v) in ARMS.items()}, 'gates': gates, 'summaries': summaries, 'verdicts': verdicts,
              'elapsed_seconds': perf_counter() - start}
    write_json(out / 'summary.json', result)
    print(json.dumps({'equal': equal, 'verdicts': verdicts['all'], 'elapsed_seconds': result['elapsed_seconds']}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=None)
    arguments = parser.parse_args()
    run_decide(arguments.out.resolve(), CODE_ROOT, workers=arguments.workers)
