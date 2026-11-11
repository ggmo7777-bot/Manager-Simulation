"""T037: legacy / centered_v1 / shifted_v1 on the frozen T030/T031/T032 settings x 84 cells, and the split of centered_v1's effect into a mean part (legacy -> shifted_v1, same spread)
and a spread part (shifted_v1 -> centered_v1, mean 0). Exactly: legacy = shifted_v1 - width, centered_v1 = shifted_v1 / 2 (same keyed draw).
True skills / fatigue are read by the researcher after the fact; nothing goes back into the simulation.

Protocol: docs/experiments/T037-PROTOCOL.md. Cells: 14 worlds x seeds {100,101,102} x start age {29,33}, 168 days, three settings x three modes (756 runs).
"""
import argparse
from dataclasses import replace
import json
from math import sqrt
from pathlib import Path
import random
import subprocess
import sys
import tempfile
from time import perf_counter

CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT))
from experiments import t023, t030, t033, t036
from soccer_sim import league_economy as le, league_observation as lo
from soccer_sim.storage import digest, runtime, write_json

L, DAYS, SEEDS, STARTS, SETTINGS = t036.L, t036.DAYS, t036.SEEDS, t036.STARTS, t036.SETTINGS
MODES = ('legacy', 'centered_v1', 'shifted_v1')
FAT_CTX, SKILL_WIDTH = t036.FAT_CTX, t036.SKILL_WIDTH
FROZEN = 'artifacts/t036/source_bundle'
stat, add, merge, paired_t036 = t036.stat, t036.add, t036.merge, t036.paired
make_cfg = t036.make_cfg
mean = t030.mean


# ---- one cell: t036.run_cell plus the clamp-free set for the doubled width ----
def run_cell(config, seed, days=DAYS):
    extra, original, original_le = {}, lo.observe_fatigue, le.observe_fatigue

    def recording(**kw):
        got = original(**kw)
        if kw['true_fatigue'] >= 2 * kw['noise'] and kw['true_fatigue'] + 2 * kw['noise'] <= 0.95:  # no clamp even for the widest range (shifted_v1, [-2w, 2w])
            add(extra, ('fat_free2', kw['context']), got - kw['true_fatigue'])
        return got
    lo.observe_fatigue = recording
    try:
        out = t036.run_cell(config, seed, days)
    finally:
        lo.observe_fatigue, le.observe_fatigue = original, original_le
    out['acc'].update(extra)
    return out


# ---- gates ----
def check_sim_frozen_bundle(root):
    """The frozen T036 bundle is the pre-T037 simulation: only league_economy.py and league_observation.py may differ."""
    bundle = root / FROZEN / 'soccer_sim'
    names = sorted(p.name for p in (root / 'soccer_sim').glob('*.py'))
    assert names == sorted(p.name for p in bundle.glob('*.py'))
    return {'files': len(names), 'changed': [n for n in names if (root / 'soccer_sim' / n).read_bytes() != (bundle / n).read_bytes()]}


def check_rng_calls(root):
    out = {f: {'now': t036.count_calls(root / 'soccer_sim' / f), 'frozen': t036.count_calls(root / FROZEN / 'soccer_sim' / f)} for f in ('league_economy.py', 'league_observation.py')}
    assert all(v['now'] == v['frozen'] for v in out.values()), out
    return out


def check_rng_stream():
    keys = [t036.day_one_keys(m) for m in MODES]
    assert keys[0] == keys[1] == keys[2] and len(keys[0]) > 0
    worst = [0.0, 0.0]
    n = 0.05
    for i in range(1, 2001):
        u, w = -1.0 + (i - 1) * 2.0 / 2000, 0.024
        nl, nc, ns = (le.observation_noise(u, w, m) for m in MODES)
        worst[0] = max(worst[0], abs((ns - nl) - w), abs(2 * nc - ns))
        f = lambda m: lo.observe_fatigue(true_fatigue=0.4, noise=n, seed=1, day=i, observer_id='c', player_id='p', context='x', **le.fatigue_mode(m))
        fl, fc, fs = (f(m) for m in MODES)  # round6; 0.4 +- 0.1 stays inside the clamp
        assert abs((fs - fl) - n) <= 2e-6 and abs(2 * (fc - 0.4) - (fs - 0.4)) <= 2e-6, (i, fl, fc, fs)
    assert max(worst) <= 1e-15, worst
    return {'day1_keys': len(keys[0]), 'identical': True, 'max_dev_noise_identities': max(worst)}


def frozen_hash(setting, name, seed, start, days, mode):
    """sha256 of the whole snapshot payload (all but the identity block) computed by the frozen T036 source_bundle in another process."""
    code = ("import sys,json,hashlib,dataclasses;root=sys.argv[1];sys.path.insert(0,root)\n"
            "from experiments import t033\nfrom soccer_sim import league_economy as le\n"
            "c=t033.SETTINGS[sys.argv[2]](sys.argv[4],int(sys.argv[6]))\n"
            "c=dataclasses.replace(c,observation_noise_mode=sys.argv[7])\n"
            "w=le.run_league_world(seed=int(sys.argv[3]),days=int(sys.argv[5]),config=c)\n"
            "print(hashlib.sha256(json.dumps({k:v for k,v in le._snapshot_payload_unchecked(w).items() if k!='identity'},sort_keys=True).encode()).hexdigest())")
    got = subprocess.run([sys.executable, '-B', '-c', code, str(CODE_ROOT / FROZEN), setting, str(seed), name, str(days), str(start), mode], capture_output=True, text=True, check=True)
    return got.stdout.strip()


def check_vs_frozen_bundle(days=DAYS):
    """legacy and centered_v1 reproduce the frozen T036 bundle (another process); shifted_v1 is new, so it must differ from both."""
    names = t023.conditions()
    picks = [('t030', names[0], 100, 29), ('t030', names[7], 102, 33), ('t031', names[3], 101, 33), ('t031', names[12], 100, 29), ('t032', names[5], 102, 29), ('t032', names[9], 101, 33)]
    for setting, name, seed, start in picks:
        for mode in ('legacy', 'centered_v1'):
            assert frozen_hash(setting, name, seed, start, days, mode) == t036.local_hash(setting, name, seed, start, days, mode), (setting, name, seed, start, mode)
        assert len({t036.local_hash(setting, name, seed, start, days, m) for m in MODES}) == 3
    return {'cells': len(picks), 'days': days, 'modes': ['legacy', 'centered_v1'], 'equal': True, 'shifted_differs': True}


def check_config_and_saves(directory):
    cls = le.LeagueEconomyConfig
    base = SETTINGS['t032'](t023.conditions()[0], 29)
    assert 'observation_noise_mode' not in base.to_dict() and base.observation_noise_mode == 'legacy'
    shifted = replace(base, observation_noise_mode='shifted_v1')
    assert shifted.to_dict()['observation_noise_mode'] == 'shifted_v1' and cls.from_dict(shifted.to_dict()) == shifted and cls.from_dict(base.to_dict()) == base
    assert cls.has_save_fields(shifted.to_dict()) and not cls.has_save_fields({**base.to_dict(), 'unknown': 1})
    bad = {}
    for label, value in (('unknown', 'bogus'), ('int', 1), ('empty', ''), ('old_none', 'none'), ('case', 'Shifted_v1')):
        try:
            replace(base, observation_noise_mode=value)
            bad[label] = False
        except ValueError:
            bad[label] = True
    assert all(bad.values()), bad
    world = le.run_league_world(seed=100, days=2 * L, config=shifted)
    folder = Path(directory) / 'shifted'
    le.save_league_artifacts(world, folder)
    for reader in (le.load_league_snapshot, le.load_league_snapshot_trusted):
        le._assert_same(reader(folder / 'snapshot.json'), world, 'world')
    child = le.fork_league_world(world, kind='player_trust', player_id='p3', coach_id='c1', value=0.05, reason='t037')
    le.advance_league_days(child, 30)
    stepped = le._build_initial_world(seed=100, config=shifted)
    le.advance_league_days(stepped, 2 * L)
    le._assert_same(stepped, world, 'world')
    snap, manifest = folder / 'snapshot.json', folder / 'manifest.json'
    original = snap.read_text()
    tamper = {}
    for relabel in ('legacy', 'centered_v1'):
        raw = json.loads(original)
        raw['config']['observation_noise_mode'] = relabel  # a shifted world relabelled must not load
        if relabel == 'legacy':
            raw['config'].pop('observation_noise_mode')
        snap.write_text(json.dumps(raw))
        record = json.loads(manifest.read_text())
        record['snapshot_sha256'] = digest(raw)
        manifest.write_text(json.dumps(record))
        try:
            le.load_league_snapshot(snap)
            tamper[relabel] = 'accepted'
        except ValueError:
            tamper[relabel] = 'rejected'
    assert set(tamper.values()) == {'rejected'}, tamper
    return {'invalid_values_rejected': bad, 'round_trip': True, 'fork_steps': len(child.interventions), 'relabelled_snapshot': tamper}


# ---- aggregation ----
def summarise(pooled):
    out = t036.summarise(pooled)
    for m, acc in pooled.items():
        out[m]['fatigue_clamp_free_2w'] = {c: stat(acc.get(('fat_free2', c))) for c in FAT_CTX}
    return out


def gate3(summary, config=None):
    """Pre-registered gate 3: shifted_v1 skill-observation errors |mean| <= 0.003 and their SD within 10% of legacy's."""
    c = config or le.LeagueEconomyConfig()
    s, l = summary['shifted_v1']['obs'], summary['legacy']['obs']
    out = {'shifted_mean': {k: s[k]['mean'] for k in SKILL_WIDTH}, 'sd_ratio_shifted_over_legacy': {k: s[k]['sd'] / l[k]['sd'] for k in SKILL_WIDTH}}
    out['ok'] = all(abs(v) <= 0.003 for v in out['shifted_mean'].values()) and all(abs(v - 1) <= 0.10 for v in out['sd_ratio_shifted_over_legacy'].values())
    return out


DIFF_KEYS = t036.DIFF_KEYS


def decompose(rows, key, boots=1000, seed=37):
    """rows: (legacy, centered, shifted) flats per cell. T = c - l, B = s - l (mean part), V = c - s (spread part); B + V = T exactly."""
    d = [(c[key] - l[key], s[key] - l[key], c[key] - s[key]) for l, c, s in rows if all(r.get(key) is not None for r in (l, c, s))]
    if not d:
        return None
    n = len(d)

    def agg(sample):
        t, b, v = (sum(x[i] for x in sample) / len(sample) for i in range(3))
        return t, b, v

    t, b, v = agg(d)
    sd = sqrt(sum((x[0] - t) ** 2 for x in d) / (n - 1)) if n > 1 else 0.0
    t_stat = t / (sd / sqrt(n)) if sd > 0 else None
    out = {'n': n, 'mean_legacy': mean(l[key] for l, c, s in rows if l.get(key) is not None), 'mean_centered': mean(c[key] for l, c, s in rows if c.get(key) is not None),
           'mean_shifted': mean(s[key] for l, c, s in rows if s.get(key) is not None), 'T': t, 'B_mean_part': b, 'V_spread_part': v, 't_T': t_stat}
    if t_stat is not None and abs(t_stat) >= 3:
        rng, shares = random.Random(seed), []
        for _ in range(boots):
            bt, bb, bv = agg([d[rng.randrange(n)] for _ in range(n)])
            if bt != 0:
                shares.append(bv / bt)
        shares.sort()
        out['V_share'], out['B_share'] = v / t, b / t
        out['V_share_ci95'] = [shares[int(0.025 * len(shares))], shares[int(0.975 * len(shares)) - 1]]
        if b * t < 0 or v * t < 0:
            out['class'] = 'offsetting'
        else:
            out['class'] = 'mostly_spread' if v / t >= 2 / 3 else 'mostly_mean' if b / t >= 2 / 3 else 'mixed'
    else:
        out['class'] = 'T_not_distinguishable_from_0'
    return out


def pooled_split(summary, group, field):
    """Point decomposition of a pooled statistic (e.g. incumbents' skill-estimate MAE): T, B, V from the three pooled values."""
    g = lambda m: summary[m]['lineup'][group][field]
    l, c, s = (g(m) for m in MODES)
    return {'legacy': l, 'centered': c, 'shifted': s, 'T': c - l, 'B_mean_part': s - l, 'V_spread_part': c - s}


POOLED_GROUPS = [(g, f) for g in ('inc_skill', 'inc_match', 'new_skill', 'new_match', 'new_t0_skill', 'new_t1_skill', 'new_t2_skill') for f in ('mean', 'mae')]


def evaluate(summary, dec):
    """Numbers behind the pre-registered predictions (judged in the RESULTS, not here)."""
    pl = lambda g, f: pooled_split(summary, g, f)
    return {
        'P2_obs_sd': {k: {m: summary[m]['obs'][k]['sd'] for m in MODES} for k in SKILL_WIDTH},
        'P3_inc_skill_mae': pl('inc_skill', 'mae'), 'P4_inc_skill_bias': pl('inc_skill', 'mean'), 'P5_new_skill_mae': pl('new_skill', 'mae'),
        'P6_top2': {k: dec['start_true_top2_match'][k] for k in ('mean_legacy', 'mean_centered', 'mean_shifted', 'T', 'B_mean_part', 'V_spread_part')},
        'P7_final': {k: {x: dec[f'final_{k}'][x] for x in ('mean_legacy', 'mean_centered', 'mean_shifted', 'T', 'B_mean_part', 'V_spread_part', 't_T')} for k in ('skill', 'trust')},
        'P8_events': {k: {x: dec[f'obs_events_{k}'][x] for x in ('mean_legacy', 'mean_centered', 'mean_shifted', 'T', 'B_mean_part', 'V_spread_part')} for k in ('scout', 'session', 'match', 'self')},
        'P8_relative_change_vs_legacy': {k: {'centered': dec[f'obs_events_{k}']['mean_centered'] / dec[f'obs_events_{k}']['mean_legacy'] - 1, 'shifted': dec[f'obs_events_{k}']['mean_shifted'] / dec[f'obs_events_{k}']['mean_legacy'] - 1} for k in ('scout', 'session', 'match', 'self')},
        'P9_inc_match_bias': pl('inc_match', 'mean'), 'P10_new_t2_bias': pl('new_t2_skill', 'mean')}


def t036_reproduced(root, summary_by_setting):
    """Gate 1b: the centered_v1 (and legacy) pooled statistics equal the saved T036 summary exactly (same code path, same cells)."""
    saved = json.loads((root / 'artifacts/t036/run/summary.json').read_text())['settings']
    out = {}
    for t, got in summary_by_setting.items():
        for m in ('legacy', 'centered_v1'):
            want = saved[t]['pooled'][m]
            have = {k: got[m][k] for k in want}
            out[f'{t}_{m}'] = json.loads(json.dumps(have)) == want
    assert all(out.values()), out
    return out


def run(out, root):
    out.mkdir(parents=True, exist_ok=False)
    start = perf_counter()
    gates = {'sim_vs_frozen_t036_bundle': check_sim_frozen_bundle(root), 'rng_call_counts': check_rng_calls(root), 'rng_stream': check_rng_stream(), 'vs_frozen_bundle': check_vs_frozen_bundle()}
    with tempfile.TemporaryDirectory() as tmp:
        gates['config_and_saves'] = check_config_and_saves(tmp)
    print('gates', json.dumps(gates)[:300], flush=True)
    refs = {t: {(c['world'], c['seed'], c['start_age']): c for c in json.loads((root / f'artifacts/{t}/run/summary.json').read_text())['cells']} for t in SETTINGS}
    result_settings, pooled_all, equal, rows_all, by_setting = {}, {m: {} for m in MODES}, {t: 0 for t in SETTINGS}, {}, {}
    for t in SETTINGS:
        pooled, rows = {m: {} for m in MODES}, []
        for name in t023.conditions():
            for seed in SEEDS:
                for st in STARTS:
                    cells = {m: run_cell(make_cfg(t, name, st, m), seed) for m in MODES}
                    ref = refs[t][(name, seed, st)]['on']
                    leg = cells['legacy']['flat']
                    assert {k[6:]: v for k, v in leg.items() if k.startswith('final_')} == ref['final'] and leg['events'] == ref['events'], (t, name, seed, st)  # gate 1
                    equal[t] += 1
                    for m in MODES:
                        merge(pooled[m], cells[m]['acc'])
                        merge(pooled_all[m], cells[m]['acc'])
                    rows.append(tuple(cells[m]['flat'] for m in MODES))
            print(t, name, flush=True)
        rows_all[t] = rows
        summary = summarise(pooled)
        by_setting[t] = summary
        dec = {k: decompose(rows, k) for k in DIFF_KEYS}
        result_settings[t] = {'pooled': summary, 'decomposition_cells': dec, 'evaluation': evaluate(summary, dec), 'gate3': gate3(summary)}
    gates['t036_reproduced'] = t036_reproduced(root, by_setting)
    all_rows = [r for rows in rows_all.values() for r in rows]
    summary = summarise(pooled_all)
    dec = {k: decompose(all_rows, k) for k in DIFF_KEYS}
    split = {f'{g}_{f}': pooled_split(summary, g, f) for g, f in POOLED_GROUPS}
    result = {'task': 'T037', 'identity': le.league_identity(), 'runtime': runtime(), 'seeds': list(SEEDS), 'days': DAYS, 'gates': gates, 'legacy_cells_equal_frozen': equal,
              'settings': result_settings, 'all': {'pooled': summary, 'pooled_split': split, 'decomposition_cells': dec, 'evaluation': evaluate(summary, dec), 'gate3': gate3(summary)},
              'elapsed_seconds': perf_counter() - start}
    write_json(out / 'summary.json', result)
    print(json.dumps({'equal': equal, 'elapsed_seconds': result['elapsed_seconds']}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    run(parser.parse_args().out.resolve(), CODE_ROOT)
