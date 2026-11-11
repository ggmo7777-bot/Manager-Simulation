"""T047: how much would the coach-estimate lag shrink under realistic observation policies (more observations of the own roster, or the same extra observations focused on the two boundary players),
what effective share lambda of the lag does that remove, what lineup-match gain does T046's dose-response predict from it, and does a direct run agree?
Analysis only: `soccer_sim/` is unchanged and no config is added. T044's read-only shadow estimates get policy lanes: per (coach, player) two more estimates `R:L` (the real estimate plus extra practice-type observations with the
simulation's own noise) and `Z:L` (the lag-only estimate plus the same extra observations, which read the true skill). Extra observations are given to the shadow lanes only; nothing is written to the simulation.
Shadow lanes (arm policy `none`, all six at once, the world is the real world) give the lag of each lane against the real lag; the read lanes (x2 x4 b2 b4) are also read by the match-day lineup (the lineup reads `R:L`).
True values reach the lineup only through that registered lane read (a counterfactual instrument of the researcher).

Lanes: xK = K times the rate of real observations of the own roster, given to the least recently seen roster player; bK = the same count given to the least recently seen of the boundary pair (ranks 2 and 3 of the lane's scores).
Protocol: docs/experiments/T047-PROTOCOL.md. Cells: 14 worlds x seeds {100,101,102} x start age {29,33}, three settings, 168 days.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor
from contextlib import contextmanager
from functools import partial
import json
import os
from pathlib import Path
import sys
from time import perf_counter
from types import SimpleNamespace

CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT))
from experiments import t023, t030, t036, t038, t040, t043, t044, t045, t046
from soccer_sim import league_economy as le
from soccer_sim.match_2d import clamp, round6, uniform_from_key
from soccer_sim.storage import runtime, write_json

DAYS, SEEDS, STARTS, SETTINGS = t046.DAYS, t046.SEEDS, t046.STARTS, t046.SETTINGS
DIRECTIONS = t046.DIRECTIONS
POINTS_FULL, POINTS_SWAPPED = t046.POINTS_FULL, t046.POINTS_SWAPPED
VALLEY, HIGH_END, LOW_END = t046.VALLEY, t046.HIGH_END, t046.LOW_END
ARM_SEP = '__'
# lane -> (multiple of the real own-roster observation rate, whose observations: the whole roster / the boundary pair)
LANES = {'x1': (1, 'all'), 'x2': (2, 'all'), 'x4': (4, 'all'), 'x8': (8, 'all'), 'b2': (2, 'boundary'), 'b4': (4, 'boundary'), 'b8': (8, 'boundary')}
SHADOW_LANES = ('x2', 'x4', 'x8', 'b2', 'b4', 'b8')
READ_LANES = ('x2', 'x4', 'b2', 'b4')
POLICIES = ('none', *READ_LANES)
ARMS = {f'{d}_{p}{ARM_SEP}{policy}': (d, p, policy) for d in DIRECTIONS for policy in POLICIES for p in (POINTS_FULL if policy == 'none' else POINTS_SWAPPED)}
GROUPS = (('all', 'abs'), ('all', 'sq'), ('r1', 'abs'), ('r2', 'abs'), ('r3', 'abs'), ('bd', 'abs'), ('bd', 'sq'), ('pair', 'abs'))
LAMBDA_GROUPS = (('all', 'abs'), ('r1', 'abs'), ('r2', 'abs'), ('r3', 'abs'), ('bd', 'abs'), ('pair', 'abs'), ('all', 'sq'), ('bd', 'sq'))
RANKS = ('r1', 'r2', 'r3')
STARTERS = t046.STARTERS
SWAP_TOLERANCE = t045.SWAP_TOLERANCE
MATCH_DAY_CALLER = t045.MATCH_DAY_CALLER
# registered thresholds
SHRINK_BINS = ((0.10, 'small'), (0.30, 'moderate'), (0.50, 'large'))
ORIGIN_BINS = ((0.40, 'spread'), (0.50, 'bench_plural'))
FOCUS_MARGIN, HOLDS_PT, HALF = 0.10, 1.5, 0.5
DOSE_X = (0.0, 0.25, 0.5, 0.75, 1.0)
DOSE_REFERENCE = ('none', 'd25', 'd50', 'd75', 'nolag')
REFERENCE_VARIANTS = (*DOSE_REFERENCE, 'bd')
SIZES = (2, 4, 8)
SEED_BOOT = 41
add, sub, mean_of, ratio, interval, merged, excludes_zero = t046.add, t046.sub, t046.mean_of, t046.ratio, t046.interval, t046.merged, t046.excludes_zero
plain_digest, check_sim_unchanged, check_t037_reproduced = t046.plain_digest, t046.check_sim_unchanged, t046.check_t037_reproduced
METRICS = {**t046.METRICS, 'obs|real': ('obs', 'real'), **{f'obs|ghost|{lane}': ('obs', f'ghost|{lane}') for lane in LANES},
           **{f'lag|{lane}|{group}|{kind}': ('lag', f'{lane}|{group}|{kind}') for lane in ('none', *LANES) for group, kind in GROUPS}}


def lanes_of(policy):
    """The lanes an arm runs: all six shadow lanes for the unread world, else only the lane the lineup reads."""
    assert policy == 'none' or policy in LANES, policy
    return SHADOW_LANES if policy == 'none' else (policy,)


def split_arm(arm):
    base_arm, policy = arm.split(ARM_SEP)
    return base_arm, policy


# ---- the policy lanes ----
class Lanes(t046.Lineups):
    """T046's read-only record with policy lanes: per (coach, player) cell, `R:L` and `Z:L` receive every real observation and the lane's own extra observations."""

    def __init__(self, world, acc, lanes):
        super().__init__(world, acc)
        self.lanes, self.serial = tuple(lanes), 0
        self.own_today, self.own_total = dict.fromkeys(world.coaches, 0), dict.fromkeys(world.coaches, 0)
        self.credit = {(lane, coach): 0.0 for lane in self.lanes for coach in world.coaches}
        self.checked.update(ghost_observations=0, lane_cells_equal=0, lane_reads=0)

    # -- lane state --
    def ensure(self, cell):
        """A lane starts as a copy of the real filters: until its first extra observation it must equal them."""
        for lane in self.lanes:
            if f'R:{lane}' not in cell['est']:
                cell['est'][f'R:{lane}'], cell['est'][f'Z:{lane}'] = list(cell['est']['R']), list(cell['est']['Z'])
                cell.setdefault('lane_seen', {})[lane] = max(cell['seen'])
                cell.setdefault('ghosts', {})[lane] = 0

    def update(self, cell, day, inputs, skill, match):
        self.ensure(cell)
        lane_inputs = {name: inputs[filter_name] for lane in self.lanes for name, filter_name in ((f'R:{lane}', 'R'), (f'Z:{lane}', 'Z'))}
        super().update(cell, day, {**inputs, **lane_inputs}, skill, match)
        for lane in self.lanes:
            cell['lane_seen'][lane] = day

    def observe(self, world, event):
        """Counts the real observations of a coach's own roster (practice observations and match observations of the own club's starters)."""
        kind, data = event['kind'], event['data']
        if kind == 'coach_session_observation':
            self.own_today[data['coach_id']] += 1
        elif kind == 'coach_match_observation' and world.players[data['player_id']].club_id == world.coaches[event['actor']].club_id:
            self.own_today[event['actor']] += 1
        super().observe(world, event)

    # -- extra observations --
    def scores(self, world, coach_id, roster, lane):
        """The lane's lineup scores (no true value): the lane's estimates and the coach's real fatigue estimate."""
        cfg, mind = world.config, world.coaches[coach_id]
        out = {}
        for pid in roster:
            cell = self.cells[coach_id, pid]
            self.ensure(cell)
            skill, match = cell['est'][f'R:{lane}']
            out[pid] = cfg.select_skill_weight * skill + cfg.select_match_weight * match - cfg.select_fatigue_weight * mind.evaluations[pid].fatigue_estimate
        return out

    def pick(self, world, coach_id, roster, lane):
        """The least recently seen player of the lane's target (ties: the smaller player id), None when the target is empty."""
        candidates = list(roster)
        if LANES[lane][1] == 'boundary':
            scores = self.scores(world, coach_id, roster, lane)
            candidates = t046.lag_targets(sorted(roster, key=lambda pid: (-scores[pid], pid)), 'boundary')
        for pid in candidates:
            self.ensure(self.cells[coach_id, pid])
        return min(candidates, key=lambda pid: (self.cells[coach_id, pid]['lane_seen'][lane], pid), default=None)

    def ghost(self, world, coach_id, pid, lane):
        """One extra practice-type observation of the lane: the real session's update, the simulation's noise width and mode, a separate keyed noise channel."""
        cfg, day, cell = world.config, world.day, self.cells[coach_id, pid]
        truth = world.players[pid].composite_skill()
        self.serial += 1
        noise = le.observation_noise(uniform_from_key(world.seed, day, f'ghost_{lane}', f'{coach_id}:{pid}:{self.serial}'), cfg.coach_noise, cfg.observation_noise_mode)
        observed = round6(clamp(truth + noise, *t044.SKILL_BOUNDS))
        self.check_input(observed, truth, cfg.coach_noise, t044.SKILL_BOUNDS)
        skill = (1.0 - cfg.coach_skill_alpha, cfg.coach_skill_alpha)
        match = t044.PRACTICE_BLEND if cfg.coach_practice_rating != 'none' else None
        for name, value in ((f'R:{lane}', observed), (f'Z:{lane}', truth)):
            estimate = cell['est'][name]
            estimate[0] = t044.blend(estimate[0], skill, value)
            if match:
                estimate[1] = t044.blend(estimate[1], match, value)
        cell['lane_seen'][lane], cell['ghosts'][lane] = day, cell['ghosts'][lane] + 1
        self.checked['ghost_observations'] += 1

    def end_of_day(self, world):
        """The real estimates are checked first (T044); then every coach spends the lane's credit: (K - 1) x the running mean rate of real own-roster observations per day."""
        super().end_of_day(world)
        for coach_id, mind in world.coaches.items():
            own, self.own_today[coach_id] = self.own_today[coach_id], 0
            self.own_total[coach_id] += own
            add(self.acc, ('obs', 'real'), own)
            roster = list(world.club_rosters[mind.club_id])
            for lane in self.lanes:
                self.credit[lane, coach_id] += (LANES[lane][0] - 1) * self.own_total[coach_id] / world.day
                given = 0
                while self.credit[lane, coach_id] >= 1.0:
                    pid = self.pick(world, coach_id, roster, lane)
                    if pid is None:
                        self.credit[lane, coach_id] = 0.0
                        break
                    self.credit[lane, coach_id] -= 1.0
                    self.ghost(world, coach_id, pid, lane)
                    given += 1
                add(self.acc, ('obs', f'ghost|{lane}'), given)
        self.check_lanes()

    def check_lanes(self):
        """A lane cell with no extra observation equals the real filters, exactly."""
        for cell in self.cells.values():
            for lane, count in cell.get('ghosts', {}).items():
                if count == 0:
                    assert cell['est'][f'R:{lane}'] == cell['est']['R'] and cell['est'][f'Z:{lane}'] == cell['est']['Z'], lane
                    self.checked['lane_cells_equal'] += 1


def record_lags(tracker, world, coach_id, ranked, truths):
    """The lag of the lineup score of every roster player (T046's estimate lag Z - T, weighted as the lineup weighs it), per lane, by rank of the real estimates."""
    cfg = world.config
    for lane in ('none', *tracker.lanes):
        name = 'Z' if lane == 'none' else f'Z:{lane}'
        lag = {}
        for pid in ranked:
            skill, match = tracker.cells[coach_id, pid]['est'][name]
            lag[pid] = cfg.select_skill_weight * (skill - truths[pid]) + cfg.select_match_weight * (match - truths[pid])
        absolute = [abs(lag[pid]) for pid in ranked]
        add(tracker.acc, ('lag', f'{lane}|all|abs'), mean_of(absolute))
        add(tracker.acc, ('lag', f'{lane}|all|sq'), mean_of(lag[pid] ** 2 for pid in ranked))
        for rank, value in zip(RANKS, absolute):
            add(tracker.acc, ('lag', f'{lane}|{rank}|abs'), value)
        if len(ranked) >= 3:
            second, third = ranked[1], ranked[2]
            add(tracker.acc, ('lag', f'{lane}|bd|abs'), mean_of((absolute[1], absolute[2])))
            add(tracker.acc, ('lag', f'{lane}|bd|sq'), mean_of((lag[second] ** 2, lag[third] ** 2)))
            add(tracker.acc, ('lag', f'{lane}|pair|abs'), abs(lag[second] - lag[third]))


@contextmanager
def lineup_read(world, tracker, reader):
    """On match days, the lineup reads a copy of the coach's evaluations whose skill and match-rating estimates are the lane `reader`'s `R:L` (None = the real estimates, the identity). The fatigue estimate stays the real one.
    Everything else, and the transfer decisions, call the real function. The real function is put back on exit, also after an exception."""
    original = le.decide_club_lineup

    def decide(*, coach, roster, config):
        if sys._getframe(1).f_code.co_name != MATCH_DAY_CALLER:
            return original(coach=coach, roster=roster, config=config)
        ranked = [row['player_id'] for row in original(coach=coach, roster=roster, config=config)['ranking']]  # no side effects: the ranking the real estimates give
        evaluations, executed, truths, true_fatigue, executed_fatigue = {}, {}, {}, {}, {}
        errors = ([], [], [])
        for pid in roster:
            ev, cell = coach.evaluations[pid], tracker.cells[coach.coach_id, pid]
            tracker.ensure(cell)
            truths[pid], true_fatigue[pid] = world.players[pid].composite_skill(), world.players[pid].fatigue
            if reader is None:  # the shadow record must equal the real estimates
                executed[pid] = (ev.skill_estimate, ev.match_rating_estimate)
                assert all(abs(cell['est']['R'][i] - executed[pid][i]) <= SWAP_TOLERANCE for i in range(2)), (coach.coach_id, pid)
                tracker.checked['untouched_players'] += 1
            else:
                executed[pid] = tuple(cell['est'][f'R:{reader}'])
                tracker.checked['lane_reads'] += 1
            executed_fatigue[pid] = ev.fatigue_estimate
            evaluations[pid] = SimpleNamespace(skill_estimate=executed[pid][0], match_rating_estimate=executed[pid][1], fatigue_estimate=executed_fatigue[pid], last_obs_ref=ev.last_obs_ref)
            errors[0].append(abs(executed[pid][0] - truths[pid]))
            errors[1].append(abs(executed[pid][1] - truths[pid]))
            errors[2].append(executed_fatigue[pid] - true_fatigue[pid])
        tracker.executed, tracker.executed_fatigue, tracker.true_fatigue = executed, executed_fatigue, true_fatigue
        tracker.checked['swapped_decisions'] += 1
        record_lags(tracker, world, coach.coach_id, ranked, truths)
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
def run_arm(setting, name, seed, start, arm, days=DAYS):
    base_arm, policy = split_arm(arm)
    direction, kind, param = t043.ARMS[base_arm]
    config = t036.make_cfg(setting, name, start, DIRECTIONS[direction][0])
    acc, prev, real_lineup = {}, {}, le.decide_club_lineup
    world = le._build_initial_world(seed=seed, config=config)
    tracker = Lanes(world, acc, lanes_of(policy))
    with t043.installed(None, kind, param, 0.0, acc), t044.recording(tracker), lineup_read(world, tracker, None if policy == 'none' else policy):
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
    assert le.decide_club_lineup is real_lineup  # the lane read has been put back
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
def check_lane_read_is_a_read_only_copy(days=DAYS):
    """With the identity (`none`, and the lane x1 which adds nothing), base equals the plain world and o(+0.010) equals T043's arm without any recorder, snapshot for snapshot;
    every read puts the real function back; every read lane changes some world (the read is effective)."""
    names, count, changed = t023.conditions(), 0, dict.fromkeys(READ_LANES, 0)
    for setting, index, seed, start in t040.IDENTITY_PICKS:
        for direction, (world_mode, _) in DIRECTIONS.items():
            real = le.decide_club_lineup
            plain = le.run_league_world(seed=seed, days=days, config=t036.make_cfg(setting, names[index], start, world_mode))
            assert plain_digest(run_arm(setting, names[index], seed, start, f'{direction}_base{ARM_SEP}none', days=days)['world']) == plain_digest(plain), (setting, index, seed, start, direction)
            reference = plain_digest(t043.run_arm(setting, names[index], seed, start, t043.ARMS[f'{direction}_o+0.0100'], days=days)['world'])
            for policy in ('none', 'x1'):
                assert plain_digest(run_arm(setting, names[index], seed, start, f'{direction}_o+0.0100{ARM_SEP}{policy}', days=days)['world']) == reference, (setting, index, seed, start, direction, policy)
            count += 4
            for policy in READ_LANES:
                changed[policy] += plain_digest(run_arm(setting, names[index], seed, start, f'{direction}_o+0.0100{ARM_SEP}{policy}', days=days)['world']) != reference
                assert le.decide_club_lineup is real
                count += 1
    assert all(changed.values()), changed
    return {'runs': count, 'all_equal': True, 'changed_worlds_by_policy': changed, 'worlds': len(t040.IDENTITY_PICKS) * len(DIRECTIONS)}


def check_t046_reproduced(root, levels_by_scope):
    """Every metric of the `none` arms has the level T046 saved (settings and total), to 1e-12. T046 names these arms the same way."""
    saved = json.loads((root / 'artifacts/t046/run/summary.json').read_text())['summaries']
    worst, arms = 0.0, set()
    for scope, by_arm in levels_by_scope.items():
        for arm, levels in by_arm.items():
            if split_arm(arm)[1] != 'none':
                continue
            arms.add(arm)
            for metric in t046.METRICS:
                worst = max(worst, abs(levels[metric] - saved[scope]['point'][f'level|{arm}|{metric}']))
    assert worst <= 1e-12, worst
    return {'max_abs_difference': worst, 'metrics': len(t046.METRICS), 'arms': len(arms)}


def load_reference(root):
    """T046's saved `top2` levels: {scope: {direction: {variant: {point: level}}}} for the dose variants, `nolag`, `bd` (swept points) and `none`."""
    saved = json.loads((root / 'artifacts/t046/run/summary.json').read_text())['summaries']
    return {scope: {d: {v: {p: s['point'][f'level|{d}_{p}{ARM_SEP}{v}|top2'] for p in POINTS_SWAPPED} for v in REFERENCE_VARIANTS} for d in DIRECTIONS} for scope, s in saved.items()}


# ---- the conversion: effective lambda -> lineup match through T046's dose-response ----
def interpolate(xs, ys, x):
    """Piecewise linear, x cut to the range of xs."""
    x = min(max(x, xs[0]), xs[-1])
    for (x0, y0), (x1, y1) in zip(zip(xs, ys), zip(xs[1:], ys[1:])):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


def dose_level(reference, point, lam):
    """The lineup match at `point` when the share `lam` of the lag is removed everywhere: T046's none/d25/d50/d75/nolag joined by lines."""
    return interpolate(DOSE_X, [reference[v][point] for v in DOSE_REFERENCE], lam)


def dose_curve(reference):
    """m4(lambda) at the knots: the mean of the four swept points."""
    return [mean_of(reference[v][p] for p in POINTS_SWAPPED) for v in DOSE_REFERENCE]


def dose_share(reference, lam):
    """g(lambda): the share of the full-removal gain of the four-point mean that removing `lam` gives."""
    curve = dose_curve(reference)
    return (interpolate(DOSE_X, curve, lam) - curve[0]) / (curve[-1] - curve[0])


def implied_lambda(reference, gain):
    """The lambda at which the four-point mean gain of the dose curve equals `gain` (None outside the curve)."""
    curve = dose_curve(reference)
    rises = [value - curve[0] for value in curve]
    if not rises[0] <= gain <= rises[-1]:
        return None
    for (x0, y0), (x1, y1) in zip(zip(DOSE_X, rises), zip(DOSE_X[1:], rises[1:])):
        if gain <= y1:
            return x0 + (x1 - x0) * (gain - y0) / (y1 - y0)


def predicted_levels(reference, method, lam_all, lam_bd):
    """{point: level} the dose-response predicts. A: the lag removed everywhere by lam_all. B: T046's boundary-pair removal scaled by the dose share of lam_bd."""
    if method == 'A':
        return {p: None if lam_all[p] is None else dose_level(reference, p, lam_all[p]) for p in POINTS_SWAPPED}
    return {p: None if lam_bd[p] is None else reference['none'][p] + (reference['bd'][p] - reference['none'][p]) * dose_share(reference, lam_bd[p]) for p in POINTS_SWAPPED}


def depth(levels):
    """U = (LO + HI)/2 of a {point: level} curve (T046's definition)."""
    return None if None in levels.values() else ((levels[LOW_END] - levels[VALLEY]) + (levels[HIGH_END] - levels[VALLEY])) / 2


def gain_of(levels, base_levels):
    return None if None in levels.values() or None in base_levels.values() else mean_of(levels.values()) - mean_of(base_levels.values())


# ---- statistics ----
def arm_of(direction, point, policy):
    return f'{direction}_{point}{ARM_SEP}{policy}'


def pooled(values):
    return None if None in values else mean_of(values)


def lambda_of(v, d, point, lane, group, kind):
    """1 - (lane lag)/(real lag) in the unread arm at `point`: absolute levels, or the root of the squared levels."""
    arm = v[arm_of(d, point, 'none')]
    lane_level, real_level = arm[f'lag|{lane}|{group}|{kind}'], arm[f'lag|none|{group}|{kind}']
    if lane_level is None or not real_level:
        return None
    return 1 - lane_level / real_level if kind == 'abs' else 1 - (lane_level / real_level) ** 0.5


def effects(v, reference):
    """v[arm][metric] = level. Levels; the effective lambda of every shadow lane by group (per swept point and pooled); the realised budget ratio; the origin of the lag (shares by rank);
    for each read lane the measured gain and depth, the two conversions' predictions, their errors, the lambda the gain implies and the lambda realised in the read arm."""
    out = {f'level|{arm}|{m}': value for arm, levels in v.items() for m, value in levels.items()}
    for d in DIRECTIONS:
        ref = reference[d]
        for lane in SHADOW_LANES:
            for group, kind in LAMBDA_GROUPS:
                per_point = [lambda_of(v, d, p, lane, group, kind) for p in POINTS_SWAPPED]
                for p, value in zip(POINTS_SWAPPED, per_point):
                    out[f'lam|{d}|{lane}|{group}|{kind}|{p}'] = value
                out[f'lam|{d}|{lane}|{group}|{kind}'] = pooled(per_point)
            real_rate = [v[arm_of(d, p, 'none')]['obs|real'] for p in POINTS_SWAPPED]
            out[f'budget|{d}|{lane}'] = pooled([ratio(v[arm_of(d, p, 'none')][f'obs|ghost|{lane}'], real) for p, real in zip(POINTS_SWAPPED, real_rate)])
        out[f'real_rate|{d}'] = pooled([v[arm_of(d, p, 'none')]['obs|real'] for p in POINTS_SWAPPED])
        shares = []
        for p in POINTS_SWAPPED:
            ranks = [v[arm_of(d, p, 'none')][f'lag|none|{r}|abs'] for r in RANKS]
            shares.append(None if None in ranks else [x / sum(ranks) for x in ranks])
        for index, rank in enumerate(RANKS):
            out[f'share|{d}|{rank}'] = None if None in shares else mean_of(s[index] for s in shares)
        out[f'lag_level|{d}|none|all'] = pooled([v[arm_of(d, p, 'none')]['lag|none|all|abs'] for p in POINTS_SWAPPED])
        none_levels = {p: v[arm_of(d, p, 'none')]['top2'] for p in POINTS_SWAPPED}
        out[f'U|{d}|none'] = depth(none_levels)
        out[f'mean4|{d}|none'] = mean_of(none_levels.values())
        for lane in READ_LANES:
            levels = {p: v[arm_of(d, p, lane)]['top2'] for p in POINTS_SWAPPED}
            gain = gain_of(levels, none_levels)
            out[f'mean4|{d}|{lane}'], out[f'gain4|{d}|{lane}'] = mean_of(levels.values()), gain
            out[f'U|{d}|{lane}'] = depth(levels)
            out[f'dU|{d}|{lane}'] = sub(out[f'U|{d}|{lane}'], out[f'U|{d}|none'])
            lam_all = {p: out[f'lam|{d}|{lane}|all|abs|{p}'] for p in POINTS_SWAPPED}
            lam_bd = {p: out[f'lam|{d}|{lane}|bd|abs|{p}'] for p in POINTS_SWAPPED}
            for method in ('A', 'B'):
                predicted = predicted_levels(ref, method, lam_all, lam_bd)
                reference_gain = None if None in predicted.values() else mean_of(predicted.values()) - mean_of(ref['none'][p] for p in POINTS_SWAPPED)
                out[f'pred_gain4|{d}|{lane}|{method}'] = reference_gain
                out[f'pred_U|{d}|{lane}|{method}'] = depth(predicted)
                out[f'err4|{d}|{lane}|{method}'] = sub(gain, reference_gain)
                out[f'errU|{d}|{lane}|{method}'] = sub(out[f'U|{d}|{lane}'], depth(predicted))
            out[f'implied_lambda|{d}|{lane}'] = None if gain is None else implied_lambda(ref, gain)
            for group in ('all', 'bd'):
                arms = [v[arm_of(d, p, lane)] for p in POINTS_SWAPPED]
                out[f'lam_realised|{d}|{lane}|{group}'] = pooled([None if not arm[f'lag|none|{group}|abs'] else 1 - arm[f'lag|{lane}|{group}|abs'] / arm[f'lag|none|{group}|abs'] for arm in arms])
    return out


# ---- the pre-registered rules ----
def verdict_across_directions(by_direction):
    labels = set(by_direction.values())
    return labels.pop() if len(labels) == 1 else ('undefined' if 'undefined' in labels else 'differs')


def binned(value, bins, top):
    if value is None:
        return 'undefined'
    return next((label for limit, label in bins if value < limit), top)


def classify_shrink(point):
    """The label of lambda_all(x2) in the two directions: both in the same bin, else `straddles`."""
    by_direction = {d: binned(point[f'lam|{d}|x2|all|abs'], SHRINK_BINS, 'half') for d in DIRECTIONS}
    labels = set(by_direction.values())
    return {'verdict': labels.pop() if len(labels) == 1 else 'straddles', 'by_direction': by_direction}


def classify_origin(point):
    """The share of the lag mass in the third-ranked player (the bench)."""
    by_direction = {d: binned(point[f'share|{d}|r3'], ORIGIN_BINS, 'bench') for d in DIRECTIONS}
    labels = set(by_direction.values())
    return {'verdict': labels.pop() if len(labels) == 1 else 'straddles', 'by_direction': by_direction}


def classify_focus(point):
    """lambda_bd(b2) - lambda_bd(x2)."""
    diffs = {d: sub(point[f'lam|{d}|b2|bd|abs'], point[f'lam|{d}|x2|bd|abs']) for d in DIRECTIONS}
    if None in diffs.values():
        return {'verdict': 'undefined', 'diffs': diffs}
    if all(x > FOCUS_MARGIN for x in diffs.values()):
        verdict = 'focus_helps'
    elif all(x < -FOCUS_MARGIN for x in diffs.values()):
        verdict = 'focus_hurts'
    elif all(abs(x) <= FOCUS_MARGIN for x in diffs.values()):
        verdict = 'same'
    else:
        verdict = 'differs'
    return {'verdict': verdict, 'diffs': diffs}


def classify_size(point):
    """The smallest registered multiple whose pooled lambda reaches one half in both directions, per family (x: all players, b: the boundary pair)."""
    out = {}
    for family, group in (('x', 'all'), ('b', 'bd')):
        reached = {d: next((k for k in SIZES if (point[f'lam|{d}|{family}{k}|{group}|abs'] or 0) >= HALF), '>8') for d in DIRECTIONS}
        labels = set(reached.values())
        out[family] = {'verdict': labels.pop() if len(labels) == 1 else 'differs', 'by_direction': reached}
    return out


def classify_transfer(point, lane):
    """holds / over / under / mixed from err4 of the registered conversion (A for the whole-roster lanes, B for the boundary lanes), both directions."""
    method = 'A' if lane.startswith('x') else 'B'
    errors = {d: point[f'err4|{d}|{lane}|{method}'] for d in DIRECTIONS}
    if None in errors.values():
        return {'verdict': 'undefined', 'method': method, 'errors': errors}
    if all(abs(e) <= HOLDS_PT for e in errors.values()):
        verdict = 'holds'
    elif any(e < -HOLDS_PT for e in errors.values()) and not any(e > HOLDS_PT for e in errors.values()):
        verdict = 'over'
    elif any(e > HOLDS_PT for e in errors.values()) and not any(e < -HOLDS_PT for e in errors.values()):
        verdict = 'under'
    else:
        verdict = 'mixed'
    return {'verdict': verdict, 'method': method, 'errors': errors}


def classify_all(point):
    return {'shrink': classify_shrink(point), 'origin': classify_origin(point), 'focus': classify_focus(point), 'size': classify_size(point),
            'transfer': {lane: classify_transfer(point, lane) for lane in READ_LANES}}


def summarise_job(job):
    scope, cell_accs, boots, reference = job
    return scope, t045.summarise(cell_accs, boots, partial(effects, reference=reference), METRICS)


def level_of(acc, key):
    return acc[key][1] / acc[key][0]


def run_decide(out, root, boots=1000, workers=None):
    out.mkdir(parents=True, exist_ok=False)
    start = perf_counter()
    gates = {'sim_unchanged_vs_frozen_t037_bundle': check_sim_unchanged(root), 'lane_read_is_a_read_only_copy': check_lane_read_is_a_read_only_copy()}
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
    pooled_accs = {scope: {arm: merged(c[arm]['acc'] for c in group) for arm in ARMS} for scope, group in scopes.items()}
    levels = {scope: {arm: {m: level_of(by_arm[arm], key) if key in by_arm[arm] else None for m, key in METRICS.items()} for arm in ARMS} for scope, by_arm in pooled_accs.items()}
    gates['t037_reproduced'] = check_t037_reproduced(root, {t: {'fwd_base': pooled_accs[t][f'fwd_base{ARM_SEP}none'], 'rev_base': pooled_accs[t][f'rev_base{ARM_SEP}none']} for t in SETTINGS})
    gates['t044_reproduced'] = t045.check_t044_reproduced(root, {scope: {f'{d}_{p}': {m: by_arm[f'{d}_{p}{ARM_SEP}none'][m] for m in t045.REPRODUCED} for d in DIRECTIONS for p in POINTS_FULL} for scope, by_arm in levels.items()})
    gates['t046_reproduced'] = check_t046_reproduced(root, levels)
    gates['invariants_validated_runs'] = len(tasks) * len(ARMS)
    gates['shadow_checks_total'] = {key: sum(cell[arm]['checked'][key] for t in SETTINGS for cell in cells[t] for arm in ARMS) for key in cells['t030'][0][f'fwd_base{ARM_SEP}none']['checked']}
    reference = load_reference(root)
    jobs = [(scope, {arm: [c[arm]['acc'] for c in group] for arm in ARMS}, boots, reference[scope]) for scope, group in scopes.items()]
    with ProcessPoolExecutor(max_workers=min(len(jobs), workers or os.cpu_count())) as pool:
        summaries = dict(pool.map(summarise_job, jobs))
    verdicts = {scope: classify_all(s['point']) for scope, s in summaries.items()}
    result = {'task': 'T047', 'identity': le.league_identity(), 'runtime': runtime(), 'seeds': list(SEEDS), 'days': DAYS, 'lanes': {k: dict(zip(('multiple', 'target'), spec)) for k, spec in LANES.items()},
              'shadow_lanes': list(SHADOW_LANES), 'read_lanes': list(READ_LANES), 'arms': {a: {'direction': d, 'point': p, 'policy': policy} for a, (d, p, policy) in ARMS.items()},
              'gates': gates, 'reference_t046': reference, 'summaries': summaries, 'verdicts': verdicts, 'elapsed_seconds': perf_counter() - start}
    write_json(out / 'summary.json', result)
    print(json.dumps({'equal': equal, 'verdicts': verdicts['all'], 'elapsed_seconds': result['elapsed_seconds']}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=None)
    arguments = parser.parse_args()
    run_decide(arguments.out.resolve(), CODE_ROOT, workers=arguments.workers)
