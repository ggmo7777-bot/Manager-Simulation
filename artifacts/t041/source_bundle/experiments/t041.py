"""T041: does the level shift of the observation noise reach the gain through the PLAYERS' self-observation? Analysis only: `soccer_sim/` is unchanged and no config is added.
Each player's self-observed state (self estimate, expected gains, trust in each coach, perceived fatigue) is rebuilt exactly from the recorded events twice: as the world's own noise mode gives it (`real`)
and as the other mode would give it from the same observations and actions (`alt`). For one player decision at a time (practice / response to advice / transfer) the decision function reads the `alt` state.
Arms also switch all three player decisions (`pall`) and all three plus the four coach decisions of T040 (`both`, the closure).
Forward arms: legacy world, centered_v1 state in the decision. Reverse arms: centered_v1 world, legacy state. True skills are read by the researcher after the fact; nothing goes back into the simulation.

Protocol: docs/experiments/T041-PROTOCOL.md. Cells: 14 worlds x seeds {100,101,102} x start age {29,33}, 168 days, three settings x 12 arms (+ one plain centered_v1 run per cell for the gates).
"""
import argparse
from concurrent.futures import ProcessPoolExecutor
from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import random
import sys
from time import perf_counter

CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT))
from experiments import t023, t030, t036, t038, t040
from soccer_sim import league_economy as le
from soccer_sim.match_2d import clamp, round6
from soccer_sim.storage import runtime, write_json

DAYS, SEEDS, STARTS, SETTINGS = t036.DAYS, t036.SEEDS, t036.STARTS, t036.SETTINGS
DIRECTIONS = t040.DIRECTIONS
PCHANNELS = ('practice', 'response', 'transfer')
EFFECT_KEYS = PCHANNELS + ('pall', 'both')
CHANNEL_SETS = {'base': ((), ()), **{c: ((c,), ()) for c in PCHANNELS}, 'pall': (PCHANNELS, ()), 'both': (PCHANNELS, t040.CHANNELS)}
ARMS = {f'{d}_{x}': (d,) + sets for d in DIRECTIONS for x, sets in CHANNEL_SETS.items()}
SELF_FATIGUE = {'training_self': 'self_train', 'match_self': 'self_match'}
ACTS = {'practice': ('A', 'B', 'rest'), 'response': ('complied', 'partial', 'refused'), 'transfer': ('wants', 'stays')}
REASONS = {'practice': ('method:expected_gain', 'method:pref_bonus', 'method:advice_term', 'method:unsatisfied_drive', 'rest:rest_preference', 'rest:fatigue_recovery_need', 'none'),
           'response': ('trust_above_autonomy', 'trust_below_autonomy', 'self_disciplined', 'self_lax', 'irritated_by_low_playing_time', 'no_dominant_term'),
           'transfer': ('unsatisfied', 'bench_streak', 'ambition_x_self_estimate', 'none')}
# metric id -> accumulator key; 'top2' is the primary one
METRICS = {'top2': ('start', 'top2'), **{f'promo_{k}': ('promo', k) for k in t040.PROMO}, **{f'final_{k}': ('final', k) for k in t040.FINALS},
           **{f'choice_{k}': ('choice', k) for k in t040.CHOICES},
           **{f'flip_{c}': ('flip', c) for c in PCHANNELS}, **{f'reasonchg_{c}': ('reasonchg', c) for c in PCHANNELS},
           **{f'act_{c}_{a}': ('act', c, a) for c in PCHANNELS for a in ACTS[c]},
           **{f'rsn_{c}_{r}': ('rsn', c, r) for c in PCHANNELS for r in REASONS[c]},
           **{f'stated_{r}': ('stated', r) for r in le.BELIEF_REASONS}, 'follow': ('follow', 'all')}
SEED_BOOT = 41
add, merge, interval = t036.add, t036.merge, t038.interval
sub, merged, resampled_levels = t040.sub, t040.merged, t040.resampled_levels


# ---- the two state columns of every player ----
def mind_state(mind):
    return {'self': mind.self_estimate, 'gains': dict(mind.expected_gains), 'trust': dict(mind.trust_by_coach), 'fatigue': mind.perceived_fatigue}


def put_state(mind, state):
    mind.self_estimate, mind.perceived_fatigue = state['self'], state['fatigue']
    mind.expected_gains.clear()
    mind.expected_gains.update(state['gains'])
    mind.trust_by_coach.clear()
    mind.trust_by_coach.update(state['trust'])


def self_step(config, state, obs_skill, fatigue):
    """The self estimate and the fatigue perception after one self observation; the simulation's own formulas."""
    smoothed = (1.0 - config.player_skill_alpha) * state['self'] + config.player_skill_alpha * obs_skill
    return {**state, 'self': round6(clamp(smoothed, 0.10, 0.98)), 'fatigue': fatigue}


def gain_step(config, state, action, obs_gain):
    smoothed = (1.0 - config.player_gain_alpha) * state['gains'][action] + config.player_gain_alpha * obs_gain
    return {**state, 'gains': {**state['gains'], action: round6(clamp(smoothed, -0.05, 0.18))}}


def trust_step(config, state, coach_id, action, obs_gain, coach_prediction):
    """Trust in one coach after the player followed his advice: it moves with how much better the coach's forecast was than the player's own."""
    advantage = abs(obs_gain - state['gains'][action]) - abs(obs_gain - coach_prediction)
    new = round6(clamp(state['trust'][coach_id] + config.trust_alpha * config.trust_scale * advantage, 0.05, 0.95))
    return {**state, 'trust': {**state['trust'], coach_id: new}}


class PlayerTracker:
    """Read-only record of the players' self-observation events plus the view switch of the player decisions."""

    def __init__(self, world, view_mode, acc):
        self.config, self.view_mode, self.acc = world.config, view_mode, acc
        self.cols = {pid: {'real': mind_state(m), 'alt': mind_state(m)} for pid, m in world.player_minds.items()}
        self.noise, self.fatigue, self.pending, self.pre_skill = [], [], {}, {}
        self.last_stream = None
        self.checked = {'self': 0, 'match_fatigue': 0, 'feedback': 0, 'days': 0}

    def youth(self, mind):
        self.cols[mind.player_id] = {'real': mind_state(mind), 'alt': mind_state(mind)}

    # -- events --
    def observe(self, world, event):
        data = event['data']
        if event['kind'] == 'player_self_observation':
            self.self_observation(world, data)
        elif event['kind'] == 'player_fatigue_observation':
            self.match_fatigue(data)
        elif event['kind'] == 'advice_feedback':
            self.feedback(data)

    def set_column(self, pid, name, state):
        self.cols[pid][name] = state

    def self_observation(self, world, data):
        pid, action = data['player_id'], data['action']
        stream, noise_real, noise_alt = self.noise.pop(0)
        observer, player, channel, fatigue_real, fatigue_alt = self.fatigue.pop(0)
        assert stream == 'self_obs' and (observer, player, channel, fatigue_real) == (pid, pid, 'self_train', data['perceived_fatigue']), (stream, observer, player, channel)
        post_skill = world.players[pid].composite_skill()
        true_gain = round6(post_skill - self.pre_skill[pid])
        skills = tuple(round6(clamp(post_skill + n, 0.10, 0.98)) for n in (noise_real, noise_alt))
        gains = tuple(round6(true_gain + n * 0.35) if action in le.TRAINING_METHODS else 0.0 for n in (noise_real, noise_alt))
        assert (skills[0], gains[0]) == (data['obs_skill'], data['obs_gain']), (pid, skills[0], gains[0], data)
        for i, (name, fatigue) in enumerate((('real', fatigue_real), ('alt', fatigue_alt))):
            self.set_column(pid, name, self_step(self.config, self.cols[pid][name], skills[i], fatigue))
        self.pending[pid] = (action, gains)  # the expected-gain update comes after the advice feedback of the same day
        self.checked['self'] += 1

    def match_fatigue(self, data):
        pid = data['player_id']
        observer, player, channel, fatigue_real, fatigue_alt = self.fatigue.pop(0)
        assert (observer, player, channel, fatigue_real) == (pid, pid, 'self_match', data['obs_fatigue']), (observer, player, channel)
        for name, fatigue in (('real', fatigue_real), ('alt', fatigue_alt)):
            self.set_column(pid, name, {**self.cols[pid][name], 'fatigue': fatigue})
        self.checked['match_fatigue'] += 1

    def feedback(self, data):
        pid, coach_id, action = data['player_id'], data['coach_id'], data['method']
        pending_action, gains = self.pending[pid]
        assert action == pending_action, (pid, action, pending_action)
        for i, name in enumerate(('real', 'alt')):
            state = self.cols[pid][name]
            after = trust_step(self.config, state, coach_id, action, gains[i], data['coach_prediction'])
            if name == 'real':
                recorded = (data['obs_gain'], data['self_prediction'], data['old_trust'], data['new_trust'])
                assert (gains[0], round6(state['gains'][action]), round6(state['trust'][coach_id]), after['trust'][coach_id]) == recorded, (pid, coach_id, recorded)
            self.set_column(pid, name, after)
        self.checked['feedback'] += 1

    def flush(self, pid):
        pending = self.pending.pop(pid, None)
        if pending is None or pending[0] not in le.TRAINING_METHODS:
            return
        action, gains = pending
        for i, name in enumerate(('real', 'alt')):
            self.set_column(pid, name, gain_step(self.config, self.cols[pid][name], action, gains[i]))

    def end_of_day(self, world):
        """Applies the deferred updates, then the `real` column of every player (retired and new ones too) must equal the simulation's own mind."""
        for pid in list(self.pending):
            self.flush(pid)
        for pid, col in self.cols.items():
            assert col['real'] == mind_state(world.player_minds[pid]), (world.day, pid, col['real'], mind_state(world.player_minds[pid]))
        self.checked['days'] += 1

    # -- the view switch --
    @contextmanager
    def viewing(self, mind):
        """While active the mind holds the `alt` state; it is restored on exit, also after an exception."""
        self.flush(mind.player_id)
        saved = mind_state(mind)
        try:
            put_state(mind, self.cols[mind.player_id]['alt'])
            yield
        finally:
            put_state(mind, saved)

    def compare(self, channel, plain, other, switched):
        """plain / other: (decision, reason) from the real and the alt state. Records whether the decision and the reason differ, and the one actually used."""
        executed = other if switched else plain
        add(self.acc, ('flip', channel), float(plain[0] != other[0]))
        add(self.acc, ('reasonchg', channel), float(plain[1] != other[1]))
        for name in ACTS[channel]:
            add(self.acc, ('act', channel, name), float(executed[0].split('>')[0] == name))
        for code in REASONS[channel]:
            add(self.acc, ('rsn', channel, code), float(executed[1] == code))
        if plain != other:
            add(self.acc, ('trans', channel, *plain, *other), 1.0)


# ---- the decisions and the researcher's labels of them ----
def largest_positive(terms):
    """Name of the largest positive term (ties: alphabetical first), or None."""
    positive = {k: v for k, v in terms.items() if v > 0}
    return min(positive, key=lambda k: (-positive[k], k)) if positive else None


def practice_signature(decision):
    chosen = decision['chosen']
    term = largest_positive(chosen['components'])
    return chosen['action'], 'none' if term is None else f"{'rest' if chosen['action'] == 'rest' else 'method'}:{term}"


def response_signature(reply):
    return reply['response'], reply['primary_reason']


def transfer_signature(decision, mind, bench_streak):
    """The request reason is recomputed by the researcher from the mind that made the decision (the coefficients are copied from decide_player_transfer_request)."""
    terms = {'unsatisfied': 0.58 * max(0.0, 0.48 - mind.playing_time_satisfaction), 'bench_streak': 0.075 * min(4, bench_streak),
             'ambition_x_self_estimate': 0.18 * mind.ambition * max(0.0, mind.self_estimate - 0.42)}
    wants = decision['wants_transfer']
    return (f"wants>{decision['player_choice']['chosen_to_club']}" if wants else 'stays'), largest_positive(terms) or 'none'


@contextmanager
def installed(tracker, channels):
    """Read-only recorders of the self noise, the self fatigue observations, the events and the day's starting skills, plus the player-decision wrappers
    (every arm calls each decision on the real and on the alt state; the switched channels use the alt result). Everything is put back on exit."""
    names = ('uniform_from_key', 'observation_noise', 'observe_fatigue', '_append_event', '_step_training_day', '_new_player_mind',
             'decide_player_training', '_player_advice_response', 'decide_transfer_bidding')
    original = {n: getattr(le, n) for n in names}
    view = tracker.view_mode

    def uniform(*args, **kw):
        tracker.last_stream = args[2]
        return original['uniform_from_key'](*args, **kw)

    def noise(u, width, mode):
        got = original['observation_noise'](u, width, mode)
        if tracker.last_stream == 'self_obs':
            tracker.noise.append(('self_obs', got, original['observation_noise'](u, width, view)))
        tracker.last_stream = None
        return got

    def fatigue(**kw):
        got = original['observe_fatigue'](**kw)
        if kw['context'] in SELF_FATIGUE:
            tracker.fatigue.append((kw['observer_id'], kw['player_id'], SELF_FATIGUE[kw['context']], got, original['observe_fatigue'](**{**kw, **le.fatigue_mode(view)})))
        return got

    def append(world, **kw):
        event_id = original['_append_event'](world, **kw)
        tracker.observe(world, world.events[-1])
        return event_id

    def training_day(world, day):
        tracker.pre_skill = {pid: p.composite_skill() for pid, p in world.players.items()}
        return original['_step_training_day'](world, day)

    def new_mind(*args, **kw):
        mind = original['_new_player_mind'](*args, **kw)
        tracker.youth(mind)
        return mind

    def judged(channel, call, mind, describe):
        plain = call()
        sig_plain = describe(plain)
        with tracker.viewing(mind):
            other = call()
            sig_other = describe(other)
        tracker.compare(channel, sig_plain, sig_other, channel in channels)
        return other if channel in channels else plain

    def practice(**kw):
        return judged('practice', lambda: original['decide_player_training'](**kw), kw['mind'], practice_signature)

    def response(world, pid, props):
        return judged('response', lambda: original['_player_advice_response'](world, pid, props), world.player_minds[pid], response_signature)

    def transfer(**kw):
        mind = kw['player_mind']
        return judged('transfer', lambda: original['decide_transfer_bidding'](**kw), mind, lambda out: transfer_signature(out, mind, kw['bench_streak']))

    replaced = {'uniform_from_key': uniform, 'observation_noise': noise, 'observe_fatigue': fatigue, '_append_event': append, '_step_training_day': training_day,
                '_new_player_mind': new_mind, 'decide_player_training': practice, '_player_advice_response': response, 'decide_transfer_bidding': transfer}
    try:
        for name, function in replaced.items():
            setattr(le, name, function)
        yield
    finally:
        for name, function in original.items():
            setattr(le, name, function)


# ---- one run ----
def record_events(acc, events):
    """What the coach hears of the advice responses (the stated reason) and whether the player followed."""
    for e in events:
        if e['kind'] == 'coach_advice_outcome':
            for reason in le.BELIEF_REASONS:
                add(acc, ('stated', reason), float(e['data']['stated_reason'] == reason))
            add(acc, ('follow', 'all'), float(e['data']['action_followed']))


def play(config, seed, view_mode, player_channels, coach_channels, acc, days=DAYS, observer=None):
    world = le._build_initial_world(seed=seed, config=config)
    players = PlayerTracker(world, view_mode, acc)
    coaches = t040.Tracker(world, view_mode) if coach_channels else None
    with ExitStack() as stack:
        if coaches:
            stack.enter_context(t040.installed(coaches, coach_channels))
        stack.enter_context(installed(players, player_channels))
        while world.day < days:
            n_events = len(world.events)
            le.advance_league_days(world, 1)
            players.end_of_day(world)
            if observer:
                observer(world, world.events[n_events:])
    assert not players.noise and not players.fatigue, (len(players.noise), len(players.fatigue))  # every recorded draw was matched to an event
    assert coaches is None or not (coaches.noise or coaches.fatigue)
    return world, players, coaches


def run_arm(setting, name, seed, start, arm, days=DAYS, identity_view=False):
    direction, player_channels, coach_channels = ARMS[arm]
    world_mode, view_mode = DIRECTIONS[direction]
    config = t036.make_cfg(setting, name, start, world_mode)
    acc, prev = {}, {}

    def observer(world, events):
        truth = {}
        t040.record_day(acc, events, lambda pid: truth.setdefault(pid, world.players[pid].composite_skill()), prev)
        record_events(acc, events)

    world, players, _ = play(config, seed, world_mode if identity_view else view_mode, player_channels, coach_channels, acc, days, observer)
    le._validate_league_invariants(world)
    flat = {f'final_{k}': v for k, v in t030.final_metrics(world).items()}
    flat['events'] = len(world.events)
    for key in t040.FINALS:
        add(acc, ('final', key), flat[f'final_{key}'])
    return {'acc': acc, 'flat': flat, 'checked': players.checked, 'world': world}


def state_numbers(state):
    return [state['self'], state['fatigue'], *(state['gains'][k] for k in sorted(state['gains'])), *(state['trust'][k] for k in sorted(state['trust']))]


def day_one_deviation(setting, name, seed, start):
    """Gate 4: after day 1 the `alt` state equals the other mode's plain world (day-1 decisions are identical in both modes). Returns the worst deviation over both directions."""
    worst = 0.0
    for world_mode, view_mode in DIRECTIONS.values():
        _, players, _ = play(t036.make_cfg(setting, name, start, world_mode), seed, view_mode, (), (), {}, days=1)
        other = le._build_initial_world(seed=seed, config=t036.make_cfg(setting, name, start, view_mode))
        le.advance_league_days(other, 1)
        for pid, col in players.cols.items():
            worst = max(worst, max(abs(a - b) for a, b in zip(state_numbers(col['alt']), state_numbers(mind_state(other.player_minds[pid])))))
    return worst


def run_cell(task):
    setting, name, seed, start = task
    out = {}
    for arm in ARMS:
        result = run_arm(setting, name, seed, start, arm)
        out[arm] = {'acc': result['acc'], 'flat': result['flat'], 'checked': result['checked']}
    plain = le.run_league_world(seed=seed, days=DAYS, config=t036.make_cfg(setting, name, start, 'centered_v1'))
    out['plain_centered'] = {'flat': {**{f'final_{k}': v for k, v in t030.final_metrics(plain).items()}, 'events': len(plain.events)},
                             'decisions': sum(1 for e in plain.events if e['kind'] == 'lineup_decision')}
    out['day_one'] = day_one_deviation(setting, name, seed, start)
    return task, out


# ---- gates ----
check_sim_unchanged, plain_digest, check_t037_reproduced = t040.check_sim_unchanged, t040.plain_digest, t040.check_t037_reproduced


def check_identity_view(days=DAYS):
    """Gate 5: with the view set to the world's own mode, switching all seven decisions (the `both` arms) reproduces the plain world's whole snapshot, in both directions."""
    names, out = t023.conditions(), {}
    for setting, index, seed, start in t040.IDENTITY_PICKS:
        for direction, (world_mode, _) in DIRECTIONS.items():
            plain = le.run_league_world(seed=seed, days=days, config=t036.make_cfg(setting, names[index], start, world_mode))
            result = run_arm(setting, names[index], seed, start, f'{direction}_both', days=days, identity_view=True)
            assert plain_digest(result['world']) == plain_digest(plain), (setting, names[index], seed, start, direction)
            out[f'{setting}_{index}_{seed}_{start}_{direction}'] = True
    return {'runs': len(out), 'all_equal': True}


# ---- statistics ----
def effects(v):
    """v[arm][metric] = level. D, F (sufficiency), R (necessity), the interaction I of the three player channels, the coach-side addition J and, for the primary metric, the ratios to D."""
    out = {f'level|{arm}|{m}': v[arm][m] for arm in ARMS for m in METRICS}
    for m in METRICS:
        out[f'D|{m}'] = sub(v['rev_base'][m], v['fwd_base'][m])
        for x in EFFECT_KEYS:
            out[f'F|{x}|{m}'] = sub(v[f'fwd_{x}'][m], v['fwd_base'][m])
            out[f'R|{x}|{m}'] = sub(v['rev_base'][m], v[f'rev_{x}'][m])
        for side in ('F', 'R'):
            parts = [out[f'{side}|{x}|{m}'] for x in PCHANNELS]
            out[f'I{side}|{m}'] = None if None in parts or out[f'{side}|pall|{m}'] is None else out[f'{side}|pall|{m}'] - sum(parts)
            out[f'J{side}|{m}'] = sub(out[f'{side}|both|{m}'], out[f'{side}|pall|{m}'])
    d = out['D|top2']
    for x in EFFECT_KEYS:
        out[f'f|{x}'] = None if not d or out[f'F|{x}|top2'] is None else out[f'F|{x}|top2'] / d
        out[f'r|{x}'] = None if not d or out[f'R|{x}|top2'] is None else out[f'R|{x}|top2'] / d
    for side in ('F', 'R'):
        out[f'i{side}'] = None if not d or out[f'I{side}|top2'] is None else out[f'I{side}|top2'] / d
    return out


def cell_levels(cell_accs):
    """cell_accs[arm] = list of per-cell accumulators -> {arm: {metric: (n list, sum list)}} for fast resampling."""
    return {arm: {m: ([a[key][0] if key in a else 0 for a in accs], [a[key][1] if key in a else 0.0 for a in accs]) for m, key in METRICS.items()} for arm, accs in cell_accs.items()}


def summarise(cell_accs, boots, seed=SEED_BOOT):
    """One resample of the cells is shared by every arm, statistic and ratio."""
    vectors = cell_levels(cell_accs)
    n_cells = len(next(iter(cell_accs.values())))
    point = effects(resampled_levels(vectors, [1] * n_cells))
    rng, draws = random.Random(seed), []
    for _ in range(boots):
        counts = [0] * n_cells
        for _ in range(n_cells):
            counts[rng.randrange(n_cells)] += 1
        draws.append(effects(resampled_levels(vectors, counts)))
    return {'cells': n_cells, 'point': point, 'ci95': {k: interval([d[k] for d in draws]) for k in point}}


def classify(point, ci):
    """The pre-registered attribution rule (docs/experiments/T041-PROTOCOL.md), first match wins, plus the flags."""
    if ci['D|top2'] is None or ci['D|top2'][0] <= 0:
        return {'class': 'no_gain', 'carriers': [], 'main_routes': [], 'flags': []}
    significant = lambda key: ci[key] is not None and ci[key][0] > 0
    excludes_zero = lambda key: ci[key] is not None and ci[key][0] * ci[key][1] > 0
    carriers = [x for x in PCHANNELS if significant(f'F|{x}|top2') and significant(f'R|{x}|top2')]
    main = [x for x in carriers if point[f'f|{x}'] >= 0.5 and point[f'r|{x}'] >= 0.5]
    flags = []
    if any(abs(point[f'i{s}']) >= 0.25 and excludes_zero(f'I{s}|top2') for s in ('F', 'R')):
        flags.append('interaction')
    flags += [f'harm({x})' for x in PCHANNELS if any(ci[f'{s}|{x}|top2'][1] < 0 for s in ('F', 'R'))]
    if any(excludes_zero(f'J{s}|top2') for s in ('F', 'R')):
        flags.append('coach_adds')
    if ci['f|pall'][1] < 0.5 and ci['r|pall'][1] < 0.5:
        label = 'outside_player_estimates'
        flags.append('closure_both' if point['f|both'] >= 0.5 and point['r|both'] >= 0.5 else 'closure_none')
    elif len(main) == 1:
        label = f'single_route({main[0]})'
    elif len(main) > 1:
        label = f'multiple_routes({", ".join(main)})'
    elif len(carriers) > 1:
        label = f'distributed({", ".join(carriers)})'
    elif carriers:
        label = f'partial_single({carriers[0]})'
    elif significant('F|pall|top2') or significant('R|pall|top2'):
        label = 'only_jointly'
    else:
        label = 'unattributed'
    return {'class': label, 'carriers': carriers, 'main_routes': main, 'flags': flags}


def recommend(attribution, point, ci, positive_settings):
    """The pre-registered default-change recommendation (proposal only). positive_settings: how many settings have a positive point D."""
    skill_ok, trust_ok = ci['D|final_skill'][0] >= 0, ci['D|final_trust'][0] >= 0
    if ci['D|top2'][0] <= 0 or not skill_ok:
        return 'hold'
    attributed = attribution['class'].split('(')[0] in ('single_route', 'multiple_routes', 'distributed', 'partial_single')
    harm = any(f.startswith('harm') for f in attribution['flags'])
    carries = point['f|pall'] >= 0.5 and point['r|pall'] >= 0.5
    if attributed and carries and positive_settings == len(SETTINGS) and skill_ok and trust_ok and not harm:
        return 'recommend'
    return 'conditional'


def transitions(cells, arm):
    """How the decisions that changed under the other mode's state changed, summed over all cells: {channel: [(plain decision, plain reason, alt decision, alt reason, n)]}."""
    pooled = merged(c[arm]['acc'] for t in SETTINGS for c in cells[t])
    out = {c: [] for c in PCHANNELS}
    for key, r in sorted(pooled.items()):
        if key[0] == 'trans':
            out[key[1]].append(list(key[2:]) + [int(r[0])])
    return {'decisions': {c: pooled[('flip', c)][0] for c in PCHANNELS}, 'changed': out}


def run(out, root, boots=1000, workers=None):
    out.mkdir(parents=True, exist_ok=False)
    start = perf_counter()
    gates = {'sim_unchanged_vs_frozen_t037_bundle': check_sim_unchanged(root)}
    gates['identity_view'] = check_identity_view()
    print('gates', json.dumps(gates), flush=True)
    refs = {t: {(c['world'], c['seed'], c['start_age']): c for c in json.loads((root / f'artifacts/{t}/run/summary.json').read_text())['cells']} for t in SETTINGS}
    tasks = [(t, name, seed, st) for t in SETTINGS for name in t023.conditions() for seed in SEEDS for st in STARTS]
    cells = {t: [] for t in SETTINGS}
    equal, closure_exact, worst_day_one = {t: 0 for t in SETTINGS}, {t: 0 for t in SETTINGS}, 0.0
    with ProcessPoolExecutor(max_workers=workers or os.cpu_count()) as pool:
        for count, ((setting, name, seed, st), got) in enumerate(pool.map(run_cell, tasks, chunksize=1), start=1):
            cells[setting].append(got)
            legacy, ref = got['fwd_base']['flat'], refs[setting][(name, seed, st)]['on']
            assert {k[6:]: v for k, v in legacy.items() if k.startswith('final_')} == ref['final'] and legacy['events'] == ref['events'], (setting, name, seed, st)  # gate 2
            centered, plain = got['rev_base']['flat'], got['plain_centered']
            assert centered == plain['flat'] and got['rev_base']['acc'][('start', 'top2')][0] == plain['decisions'], (setting, name, seed, st)  # gate 2
            equal[setting] += 1
            closure_exact[setting] += all(got[a]['acc'][('start', 'top2')] == got[b]['acc'][('start', 'top2')] and got[a]['flat']['events'] == got[b]['flat']['events']
                                          for a, b in (('fwd_both', 'rev_base'), ('rev_both', 'fwd_base')))  # post hoc, not a gate
            worst_day_one = max(worst_day_one, got['day_one'])
            if count % 12 == 0:
                print(count, len(tasks), round(perf_counter() - start), flush=True)
    assert worst_day_one <= t040.DAY_ONE_TOLERANCE, worst_day_one
    gates['base_arms_equal_plain_runs'] = equal
    gates['day_one_alt_equals_other_mode_max_deviation'] = worst_day_one
    gates['t037_reproduced'] = check_t037_reproduced(root, {t: {arm: merged(c[arm]['acc'] for c in cells[t]) for arm in ('fwd_base', 'rev_base')} for t in SETTINGS})
    gates['invariants_validated_runs'] = len(tasks) * len(ARMS)
    summaries = {'all': summarise({arm: [c[arm]['acc'] for t in SETTINGS for c in cells[t]] for arm in ARMS}, boots),
                 **{t: summarise({arm: [c[arm]['acc'] for c in cells[t]] for arm in ARMS}, boots) for t in SETTINGS}}
    t040_d = json.loads((root / 'artifacts/t040/run/summary.json').read_text())['summaries']['all']['point']['D|top2']
    gates['d_equals_t040'] = abs(summaries['all']['point']['D|top2'] - t040_d) <= 1e-12
    assert gates['d_equals_t040']
    attribution = {k: classify(s['point'], s['ci95']) for k, s in summaries.items()}
    positive = sum(1 for t in SETTINGS if summaries[t]['ci95']['D|top2'][0] > 0)
    result = {'task': 'T041', 'identity': le.league_identity(), 'runtime': runtime(), 'seeds': list(SEEDS), 'days': DAYS,
              'arms': {a: {'direction': d, 'player_channels': list(p), 'coach_channels': list(c)} for a, (d, p, c) in ARMS.items()},
              'gates': gates, 'posthoc_closure_arm_equals_other_modes_world_cells': closure_exact, 'summaries': summaries, 'attribution': attribution, 'settings_with_positive_D': positive,
              'recommendation': recommend(attribution['all'], summaries['all']['point'], summaries['all']['ci95'], sum(1 for t in SETTINGS if summaries[t]['point']['D|top2'] > 0)),
              'transitions': {arm: transitions(cells, arm) for arm in ('fwd_base', 'rev_base')},
              'self_events_checked': {arm: {k: sum(cell[arm]['checked'][k] for t in SETTINGS for cell in cells[t]) for k in ('self', 'match_fatigue', 'feedback', 'days')} for arm in ARMS},
              'elapsed_seconds': perf_counter() - start}
    write_json(out / 'summary.json', result)
    print(json.dumps({'equal': equal, 'attribution': attribution['all'], 'recommendation': result['recommendation'], 'elapsed_seconds': result['elapsed_seconds']}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--workers', type=int, default=None)
    arguments = parser.parse_args()
    run(arguments.out.resolve(), CODE_ROOT, workers=arguments.workers)
