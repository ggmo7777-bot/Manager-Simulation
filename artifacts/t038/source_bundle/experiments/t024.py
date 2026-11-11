"""T024: per-season league standings as a pure derived aggregate over events.

Protocol: docs/experiments/T024-PROTOCOL.md. Seed 100; 14 worlds x L in {21,42,84} = 42 cells, 126 days.
"""
import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
from time import perf_counter

CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT))
from experiments import t019, t023
from soccer_sim import league_economy as le
from soccer_sim.standings import league_standings
from soccer_sim.storage import digest, runtime, write_json

SEED, DAYS, LENGTHS = t023.SEED, t023.DAYS, t023.LENGTHS
INTERVAL = 7
CLUBS = ('club_a', 'club_b', 'club_c', 'club_d')
FIELDS = ('played', 'wins', 'draws', 'losses', 'goals_for', 'goals_against', 'goal_difference', 'points', 'rank', 'tied_with_previous')


def reference_standings(raw):
    """Second implementation: raw snapshot dict only, ceil-division seasons, per-club result lists, comparator sort."""
    from functools import cmp_to_key
    length, day = raw['config']['season_length_days'], raw['day']
    n_seasons = max(1, -(-day // length))
    results = {s: {c: [] for c in CLUBS} for s in range(1, n_seasons + 1)}  # (scored, conceded)
    for ev in raw['events']:
        if ev['kind'] == 'league_match_completed':
            d = ev['data']
            s = -(-ev['day'] // length)
            results[s][d['home_club']].append((d['home_goals'], d['away_goals']))
            results[s][d['away_club']].append((d['away_goals'], d['home_goals']))

    def stats(club, games):
        w, dr = sum(a > b for a, b in games), sum(a == b for a, b in games)
        gf, ga = sum(a for a, _ in games), sum(b for _, b in games)
        return {'club': club, 'played': len(games), 'wins': w, 'draws': dr, 'losses': len(games) - w - dr, 'goals_for': gf,
                'goals_against': ga, 'goal_difference': gf - ga, 'points': 3 * w + dr}

    def key(r):
        return (r['points'], r['goal_difference'], r['goals_for'], r['wins'])

    def compare(a, b):
        if key(a) != key(b):
            return -1 if key(a) > key(b) else 1
        return -1 if a['club'] < b['club'] else 1

    out = {}
    for s, per_club in results.items():
        rows = sorted((stats(c, g) for c, g in per_club.items()), key=cmp_to_key(compare))
        for i, r in enumerate(rows):
            r['rank'] = i + 1
            r['tied_with_previous'] = i > 0 and key(r) == key(rows[i - 1])
        out[s] = {'complete': day >= s * length, 'table': rows}
    return {'seasons': out}


def seasoned(name, length):
    return le.run_league_world(seed=SEED, days=DAYS, config=t023.season_config(name, length))


def ev(day, home, away, hg, ag, kind='league_match_completed'):
    return {'id': 'e0', 'day': day, 'kind': kind, 'actor': 'world', 'cause_refs': [],
            'data': {'home_club': home, 'away_club': away, 'home_goals': hg, 'away_goals': ag} if kind == 'league_match_completed' else {'x': 1}}


# Hand-computed before implementation (see PROTOCOL success condition 3). L=21, day 80 -> seasons 1..4, season 4 incomplete and empty.
HAND_EVENTS = [
    ev(7, 'club_a', 'club_b', 2, 0), ev(7, 'club_c', 'club_d', 1, 1), ev(7, 'club_a', 'club_a', 9, 9, kind='coach_match_observation'),
    ev(14, 'club_a', 'club_c', 1, 1), ev(14, 'club_b', 'club_d', 3, 0),
    ev(21, 'club_d', 'club_a', 1, 0), ev(21, 'club_b', 'club_c', 0, 0),
    ev(28, 'club_a', 'club_b', 3, 2), ev(28, 'club_c', 'club_d', 1, 0), ev(28, 'club_a', 'club_c', 5, 5, kind='lineup_decision'),
    ev(49, 'club_a', 'club_c', 2, 1), ev(49, 'club_b', 'club_d', 1, 1),
    ev(56, 'club_d', 'club_a', 2, 1), ev(56, 'club_b', 'club_c', 1, 1), ev(63, 'club_b', 'club_d', 1, 1)]
# (club, played, wins, draws, losses, gf, ga, gd, points, rank, tied_with_previous)
HAND_EXPECTED = {
    1: (True, [('club_a', 3, 1, 1, 1, 3, 2, 1, 4, 1, False), ('club_b', 3, 1, 1, 1, 3, 2, 1, 4, 2, True),
               ('club_d', 3, 1, 1, 1, 2, 4, -2, 4, 3, False), ('club_c', 3, 0, 3, 0, 2, 2, 0, 3, 4, False)]),
    2: (True, [('club_a', 1, 1, 0, 0, 3, 2, 1, 3, 1, False), ('club_c', 1, 1, 0, 0, 1, 0, 1, 3, 2, False),
               ('club_b', 1, 0, 0, 1, 2, 3, -1, 0, 3, False), ('club_d', 1, 0, 0, 1, 0, 1, -1, 0, 4, False)]),
    3: (True, [('club_d', 3, 1, 2, 0, 4, 3, 1, 5, 1, False), ('club_a', 2, 1, 0, 1, 3, 3, 0, 3, 2, False),
               ('club_b', 3, 0, 3, 0, 3, 3, 0, 3, 3, False), ('club_c', 2, 0, 1, 1, 2, 3, -1, 1, 4, False)]),
    4: (False, [('club_a', 0, 0, 0, 0, 0, 0, 0, 0, 1, False), ('club_b', 0, 0, 0, 0, 0, 0, 0, 0, 2, True),
                ('club_c', 0, 0, 0, 0, 0, 0, 0, 0, 3, True), ('club_d', 0, 0, 0, 0, 0, 0, 0, 0, 4, True)])}


def check_hand_case():
    world = SimpleNamespace(events=HAND_EVENTS, day=80, config=replace(le.LeagueEconomyConfig(), season_length_days=21))
    got = league_standings(world)
    assert sorted(got['seasons']) == [1, 2, 3, 4], got['seasons'].keys()
    for season, (complete, rows) in HAND_EXPECTED.items():
        assert got['seasons'][season]['complete'] is complete, season
        actual = [(r['club'], r['played'], r['wins'], r['draws'], r['losses'], r['goals_for'], r['goals_against'],
                   r['goal_difference'], r['points'], r['rank'], r['tied_with_previous']) for r in got['seasons'][season]['table']]
        assert actual == rows, (season, actual)
    raw = {'config': {'season_length_days': 21}, 'day': 80, 'events': HAND_EVENTS}
    assert reference_standings(raw) == got
    return {'seasons': 4, 'matches': sum(e['kind'] == 'league_match_completed' for e in HAND_EVENTS)}


def check_cell(name, length, directory):
    world = seasoned(name, length)
    before = digest(le._snapshot_payload_unchecked(world))
    table = league_standings(world)
    assert digest(le._snapshot_payload_unchecked(world)) == before, 'standings mutated the world'
    raw = le._snapshot_payload_unchecked(world)
    assert reference_standings(raw) == table, (name, length)
    n_seasons = -(-DAYS // length)  # L=84 leaves season 2 in progress at day 126
    assert sorted(table['seasons']) == list(range(1, n_seasons + 1))
    assert {s: e['complete'] for s, e in table['seasons'].items()} == {s: s * length <= DAYS for s in range(1, n_seasons + 1)}
    # Accounting: sum over seasons equals the world's own club accounts; match counts follow from the calendar.
    matchdays = [d for d in range(1, DAYS + 1) if d % INTERVAL == 0]
    assert sum(e['kind'] == 'league_match_completed' for e in world.events) == 2 * len(matchdays) == 36
    for club in CLUBS:
        acc = world.club_accounts[club]
        rows = [r for s in table['seasons'].values() for r in s['table'] if r['club'] == club]
        for field, attr in (('points', 'points'), ('played', 'matches_played'), ('wins', 'wins'), ('draws', 'draws'),
                            ('losses', 'losses'), ('goals_for', 'goals_for'), ('goals_against', 'goals_against')):
            assert sum(r[field] for r in rows) == getattr(acc, attr), (name, length, club, field)
    for s, entry in table['seasons'].items():
        expected = 2 * sum(1 for d in matchdays if (d - 1) // length + 1 == s)
        assert sum(r['played'] for r in entry['table']) == 2 * expected, (name, length, s)
    le.save_league_artifacts(world, directory)
    snap = Path(directory) / 'snapshot.json'
    for reader in (le.load_league_snapshot, le.load_league_snapshot_trusted):
        loaded = reader(snap)
        assert league_standings(loaded) == table, (name, length, reader.__name__)
    ties = sum(r['tied_with_previous'] for s in table['seasons'].values() for r in s['table'])
    return {'seasons': n_seasons, 'tied_rows': ties, 'champions': [s['table'][0]['club'] for s in table['seasons'].values()]}


def check_follows_events():
    a, b = seasoned(t023.conditions()[0], 42), seasoned(t023.conditions()[5], 42)
    swapped = SimpleNamespace(events=b.events, config=a.config, day=a.day)
    assert league_standings(swapped) == league_standings(b) != league_standings(a)
    return True


def check_invalid():
    cfg, ok = replace(le.LeagueEconomyConfig(), season_length_days=21), {}
    cases = {'length_zero': SimpleNamespace(events=[], day=0, config=le.LeagueEconomyConfig()),
             'unknown_club': SimpleNamespace(events=[ev(7, 'club_a', 'club_z', 1, 0)], day=7, config=cfg),
             'same_club': SimpleNamespace(events=[ev(7, 'club_a', 'club_a', 1, 0)], day=7, config=cfg),
             'goals_negative': SimpleNamespace(events=[ev(7, 'club_a', 'club_b', -1, 0)], day=7, config=cfg),
             'goals_bool': SimpleNamespace(events=[ev(7, 'club_a', 'club_b', True, 0)], day=7, config=cfg),
             'goals_float': SimpleNamespace(events=[ev(7, 'club_a', 'club_b', 1.0, 0)], day=7, config=cfg),
             'match_after_world_day': SimpleNamespace(events=[ev(30, 'club_a', 'club_b', 1, 0)], day=7, config=cfg)}
    for label, w in cases.items():
        try:
            league_standings(w)
        except ValueError:
            ok[label] = True
        else:
            ok[label] = False
    return ok


def check_unchanged_code(root):
    bundle = root / 'artifacts/t023/source_bundle'
    manifest = json.loads((bundle / 'manifest.json').read_text())
    assert le.league_identity() == manifest['identity']  # same source hash as T023
    changed = [p.name for p in sorted((root / 'soccer_sim').glob('*.py'))
               if not (bundle / 'soccer_sim' / p.name).is_file() or hashlib.sha256(p.read_bytes()).digest() != hashlib.sha256((bundle / 'soccer_sim' / p.name).read_bytes()).digest()]
    assert changed == ['standings.py'], changed
    return {'identity_equal_to_t023': True, 'new_or_changed_package_files': changed, 'source_sha256': manifest['identity']['source_sha256']}


def run(out, root):
    out.mkdir(parents=True, exist_ok=False)
    start = perf_counter()
    unchanged = check_unchanged_code(root)
    default = t023.check_default_unchanged(root / 'artifacts/t023/baseline/baseline.json')
    regression = t019.check_regression(root)
    print('unchanged', unchanged, default, regression, flush=True)
    hand = check_hand_case()
    follows = check_follows_events()
    invalid = check_invalid()
    assert all(invalid.values()), invalid
    print('hand', hand, 'follows_events', follows, 'invalid', invalid, flush=True)
    cells = {}
    for name in t023.conditions():
        for length in LENGTHS:
            with tempfile.TemporaryDirectory() as tmp:
                cells[f'{name}@{length}'] = check_cell(name, length, Path(tmp) / 'p')
        print('cells done', name, flush=True)
    assert len(cells) == 42
    result = {'task': 'T024', 'identity': le.league_identity(), 'runtime': runtime(), 'seed': SEED, 'days': DAYS, 'lengths': list(LENGTHS),
              'unchanged_code': unchanged, 'default_unchanged': default, 'default_vs_t018': regression, 'hand_case': hand,
              'standings_follow_events': follows, 'invalid_rejected': invalid, 'cells': cells,
              'tied_rows_total': sum(c['tied_rows'] for c in cells.values()), 'elapsed_seconds': perf_counter() - start}
    write_json(out / 'summary.json', result)
    print(json.dumps({'cells': len(cells), 'tied_rows_total': result['tied_rows_total'], 'elapsed_seconds': result['elapsed_seconds']}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, required=True)
    run(parser.parse_args().out.resolve(), CODE_ROOT)
