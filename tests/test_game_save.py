"""Save/resume: a save is seed + inputs, replays to the identical state, holds no true value, rejects tampering."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from game.server import Game
from game.session import GameSession, InvalidInput
from soccer_sim import league_economy as le
from test_game_leak import leaks, true_values



def world_state(world):
    # the research snapshot_payload re-validates by replaying research-only inputs, which a human-played world is not
    return le._snapshot_payload_unchecked(world)


ADVICE = {"player_id": "p1", "method": "B"}


def play_weeks(session, weeks):
    for _ in range(weeks):
        session.train(ADVICE)
        session.play_match(session.state()["recommended_starters"])


class TestSaveResume(unittest.TestCase):
    def test_resumed_state_equals_uninterrupted_state(self):
        interrupted = GameSession(7)
        play_weeks(interrupted, 2)
        payload = json.loads(json.dumps(interrupted.save_payload()))
        resumed = GameSession.resume(payload)
        self.assertEqual(resumed.world.events, interrupted.world.events)
        self.assertEqual(world_state(resumed.world), world_state(interrupted.world))
        play_weeks(resumed, 4)
        uninterrupted = GameSession(7)
        play_weeks(uninterrupted, 6)
        self.assertEqual(world_state(resumed.world), world_state(uninterrupted.world))
        self.assertEqual(resumed.reveal(), uninterrupted.reveal())

    def test_save_holds_no_true_value_and_no_forbidden_key(self):
        session = GameSession(7)
        play_weeks(session, 3)
        payload = json.loads(json.dumps(session.save_payload()))
        self.assertEqual(leaks(payload, true_values(session.world)), [])
        self.assertEqual(set(payload), {"version", "code", "seed", "inputs", "view_digest"})

    def test_tampered_or_mismatched_saves_are_rejected(self):
        session = GameSession(7)
        play_weeks(session, 1)
        good = json.loads(json.dumps(session.save_payload()))

        def changed(**fields):
            return {**json.loads(json.dumps(good)), **fields}
        other_advice = json.loads(json.dumps(good["inputs"]))
        other_advice[0]["advice"]["method"] = "A"
        bad_cases = {
            "code": changed(code="0" * 64), "version": changed(version=99), "seed": changed(seed=8),
            "advice edited": changed(inputs=other_advice), "truncated": changed(inputs=good["inputs"][:1]),
            "invalid step": changed(inputs=[{"kind": "lineup", "starters": ["p1", "p2"]}]),
            "unknown step": changed(inputs=[{"kind": "cheat"}]), "not a dict": [],
        }
        for label, payload in bad_cases.items():
            with self.subTest(label), self.assertRaises(InvalidInput):
                GameSession.resume(payload)


class TestServerSave(unittest.TestCase):
    def test_save_load_list_and_bad_names(self):
        with tempfile.TemporaryDirectory() as directory:
            game = Game(7, save_dir=Path(directory) / "game-save")
            game.handle("POST", "/api/train", {"advice": None})
            game.handle("POST", "/api/save", {"name": "slot-1"})
            day = game.session.world.day
            game.handle("POST", "/api/new", {"seed": 11})
            self.assertEqual(game.handle("GET", "/api/saves", {}), {"saves": [{"name": "slot-1", "seed": 7, "steps": 1, "season": 1}]})
            state = game.handle("POST", "/api/load", {"name": "slot-1"})
            self.assertEqual((state["seed"], state["day"]), (7, day))
            for bad in ("../x", "", None, "a" * 41):
                with self.assertRaises(InvalidInput):
                    game.handle("POST", "/api/save", {"name": bad})
            with self.assertRaises(InvalidInput):
                game.handle("POST", "/api/load", {"name": "missing"})
            (Path(directory) / "game-save" / "broken.json").write_text("{nope")
            with self.assertRaises(InvalidInput):
                game.handle("POST", "/api/load", {"name": "broken"})

    def test_concurrent_requests_match_a_serial_season(self):
        game = Game(7, save_dir=tempfile.gettempdir())
        before = (le.decide_club_lineup, le.decide_coach_training)
        errors = []

        def worker():
            try:
                for _ in range(20):
                    state = game.handle("GET", "/api/state", {})
                    if state["phase"] == "training":
                        game.handle("POST", "/api/train", {"advice": None})
                    elif state["phase"] == "lineup":
                        game.handle("POST", "/api/lineup", {"starters": state["recommended_starters"]})
            except InvalidInput:
                pass  # another thread already took this phase's step
            except Exception as error:
                errors.append(error)
        threads = [threading.Thread(target=worker) for _ in range(6)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(errors, [])
        self.assertEqual((le.decide_club_lineup, le.decide_coach_training), before)
        serial = GameSession(7)
        while serial.phase != "season_end":
            serial.train(None) if serial.phase == "training" else serial.play_match(serial.state()["recommended_starters"])
        self.assertEqual(game.session.world.day, serial.world.day)
        self.assertEqual(world_state(game.session.world), world_state(serial.world))


if __name__ == "__main__":
    unittest.main()
