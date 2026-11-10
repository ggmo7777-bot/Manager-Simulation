"""T024: per-season standings are a pure aggregate over league match events."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from soccer_sim import league_economy as le
from soccer_sim.standings import league_standings
from experiments import t023, t024


class TestStandings(unittest.TestCase):
    def test_hand_computed_case_covers_every_tiebreak_and_ignores_other_events(self):
        self.assertEqual(t024.check_hand_case()['matches'], 13)

    def test_invalid_inputs_are_rejected(self):
        self.assertTrue(all(t024.check_invalid().values()))

    def test_real_world_matches_reference_accounts_and_is_pure(self):
        # 28 days, L=14: two seasons, 4 matchdays, 8 matches; same code path as the 126-day cells.
        world = le.run_league_world(seed=t023.SEED, days=28, config=t023.season_config('open_solidarity', 14))
        before = le._snapshot_payload_unchecked(world)
        table = league_standings(world)
        self.assertEqual(le._snapshot_payload_unchecked(world), before)
        self.assertEqual(t024.reference_standings(before), table)
        for club in t024.CLUBS:
            rows = [r for s in table['seasons'].values() for r in s['table'] if r['club'] == club]
            self.assertEqual(sum(r['points'] for r in rows), world.club_accounts[club].points)
            self.assertEqual(sum(r['played'] for r in rows), world.club_accounts[club].matches_played)
        self.assertEqual(sum(r['played'] for s in table['seasons'].values() for r in s['table']), 16)

    def test_only_standings_module_is_new_and_runtime_identity_is_unchanged(self):
        if (ROOT / 'artifacts/t023/source_bundle/manifest.json').is_file():
            self.assertTrue(t024.check_unchanged_code(ROOT)['identity_equal_to_t023'])


if __name__ == '__main__':
    unittest.main()
