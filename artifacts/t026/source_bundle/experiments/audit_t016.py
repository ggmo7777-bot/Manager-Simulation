"""Independent cohort/statistical checks. No simulator imports."""
from collections import Counter
import statistics

PAIRED_METRICS = (
    'academy_initial_cohort_skill_gain', 'mean_skill_gain', 'legacy_four_slot_skill_gain',
)


def cohort_audit(raw, summary, academy_clubs=('club_a', 'club_c')):
    initial, final = raw['initial_players'], raw['players']
    assert initial.keys() == final.keys()
    moves = Counter()
    clubs = {pid: player['club_id'] for pid, player in initial.items()}
    for event in raw['events']:
        if event['kind'] == 'transfer_completed':
            data = event['data']
            pid = data['player_id']
            assert clubs[pid] == data['from_club']
            clubs[pid] = data['to_club']
            moves[pid] += 1
    players = []
    for pid in sorted(initial):
        a, b = initial[pid], final[pid]
        assert b['transfers_count'] == moves[pid]
        assert b['club_id'] == clubs[pid]
        def skill(player):
            t = player['traits']
            return round((t['pace'] + t['passing'] + t['shooting']) / 3, 6)
        players.append({'player_id': pid, 'initial_club': a['club_id'], 'final_club': b['club_id'],
                        'transfers': moves[pid], 'initial_skill': skill(a), 'final_skill': skill(b),
                        'gain': round(skill(b) - skill(a), 6),
                        'initial_academy': a['club_id'] in academy_clubs,
                        'never_transferred': moves[pid] == 0 and a['club_id'] == b['club_id']})
    cohort = [p for p in players if p['initial_academy']]
    never = [p for p in cohort if p['never_transferred']]
    for stem, selected in (('academy_initial_cohort', cohort), ('academy_never_transferred', never)):
        assert summary[stem + '_ids'] == [p['player_id'] for p in selected]
        assert summary[stem + '_count'] == len(selected)
        mean = round(statistics.fmean(p['gain'] for p in selected), 6) if selected else None
        assert summary[stem + '_skill_gain'] == mean
    assert 'academy_retained_skill_gain' not in summary
    return {'members_counts_and_gains_equal': True, 'transfer_facts_equal': True, 'players': players}


def paired_effects(rows, seeds):
    """Only fixed initial groups may enter paired growth comparisons."""
    if len(set(seeds)) != len(seeds) or len(seeds) != 30:
        raise ValueError('Protocol requires 30 distinct world seeds')
    table = {(r['population'], r['institution'], r['seed']): r for r in rows}
    if len(table) != len(rows) or len(rows) != 180:
        raise ValueError('Protocol requires 180 distinct condition/seed rows')
    effects = []
    for mode in ('fixed', 'sampled'):
        for a, b in (('open_solidarity', 'open_no_solidarity'),
                     ('open_no_solidarity', 'closed_no_solidarity')):
            for metric in PAIRED_METRICS:
                differences = []
                for seed in seeds:
                    left, right = table[mode, a, seed], table[mode, b, seed]
                    assert left['initial_agent_state_sha256'] == right['initial_agent_state_sha256']
                    assert left['summary']['academy_initial_cohort_ids'] == right['summary']['academy_initial_cohort_ids']
                    differences.append({'seed': seed, 'difference': round(left['summary'][metric] - right['summary'][metric], 6)})
                values = [d['difference'] for d in differences]
                mean, sd = statistics.fmean(values), statistics.stdev(values)
                half = 2.045 * sd / len(values) ** 0.5
                effects.append({'population': mode, 'contrast': a + '-' + b, 'metric': metric,
                                'n_worlds': len(values), 'mean': round(mean, 6), 'sd': round(sd, 6),
                                'ci95_exploratory': [round(mean-half, 6), round(mean+half, 6)],
                                'positive': sum(x > 0 for x in values), 'negative': sum(x < 0 for x in values),
                                'zero': sum(x == 0 for x in values), 'paired_differences': differences,
                                'role': 'legacy diagnostic' if metric.startswith('legacy') else 'fixed initial population comparison'})
    return effects


def never_transferred_description(rows):
    """Within-condition description; empty worlds never count as zero growth."""
    means, members, empty = [], [], []
    for row in rows:
        s = row['summary']
        count, ids, gain = (s['academy_never_transferred_' + k] for k in ('count', 'ids', 'skill_gain'))
        assert count == len(ids)
        eligible = [p for p in row['cohort_audit']['players'] if p['initial_academy'] and p['never_transferred']]
        assert ids == [p['player_id'] for p in eligible]
        if count == 0:
            assert gain is None
            empty.append(row['seed'])
        else:
            assert gain is not None
            means.append(gain)
            members.extend(p['gain'] for p in eligible)
    return {'role': 'within-condition descriptive only; selection depends on outcomes',
            'worlds': len(rows), 'defined_worlds': len(means), 'empty_world_seeds': empty,
            'total_player_world_observations': len(members),
            'mean_of_nonempty_world_means': round(statistics.fmean(means), 6) if means else None,
            'pooled_player_mean': round(statistics.fmean(members), 6) if members else None}
