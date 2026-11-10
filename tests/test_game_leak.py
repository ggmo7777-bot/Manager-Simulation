"""Game observation boundary: no true value of any player may reach the player's view or step results."""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from game.session import GameSession
from soccer_sim import league_economy as le

FORBIDDEN_KEYS = {"traits", "cap", "rates", "trust", "trust_by_coach", "autonomy", "ambition", "loyalty_bias",
                  "discipline", "emotional_stability", "personality", "terms", "reason_codes", "primary_reason",
                  "skill", "composite_skill", "fatigue", "self_estimate", "expected_gains", "true_reason"}
CLAMPED_FATIGUE = (0.0, 0.95)
PUBLIC_START_FATIGUE = le.YOUTH_PRIOR[2]  # the coach's constant first impression equals the common starting fatigue: no individual information
PRECISION = 6


def true_values(world):
    """Every hidden number of every player, rounded as the world rounds it."""
    values = set()
    for pid, truth in world.players.items():
        mind = world.player_minds[pid]
        numbers = [truth.composite_skill(), truth.cap, truth.traits.pace, truth.traits.passing, truth.traits.shooting,
                   *truth.rates.values(), mind.autonomy, mind.ambition, mind.loyalty_bias, mind.rest_preference,
                   *mind.trust_by_coach.values(), *mind.expected_gains.values(), *world.player_personality.get(pid, {}).values()]
        if truth.fatigue not in CLAMPED_FATIGUE and truth.fatigue != PUBLIC_START_FATIGUE:
            numbers.append(truth.fatigue)
        values.update(round(number, PRECISION) for number in numbers)
    return values


def leaks(node, hidden, path="view"):
    """List every forbidden key and every float equal to a hidden number."""
    found = []
    if isinstance(node, dict):
        for key, child in node.items():
            if key in FORBIDDEN_KEYS:
                found.append(f"{path}.{key}: forbidden key")
            found += leaks(child, hidden, f"{path}.{key}")
    elif isinstance(node, list):
        for index, child in enumerate(node):
            found += leaks(child, hidden, f"{path}[{index}]")
    elif type(node) is float and round(node, PRECISION) in hidden:
        found.append(f"{path}: {node} equals a hidden true value")
    return found


def play_season(session, collect):
    collect(session.state())
    while session.phase != "season_end":
        if session.phase == "training":
            result = session.train({"player_id": session.world.club_rosters["club_a"][0], "method": "A"})
        else:
            result = session.play_match(session.state()["recommended_starters"])
        collect(result)


class TestPlayerViewHasNoTruth(unittest.TestCase):
    def test_no_true_value_reaches_the_view_in_any_step(self):
        for seed in (7, 11):
            session = GameSession(seed)
            problems = []
            play_season(session, lambda payload: problems.extend(
                leaks(json.loads(json.dumps(payload)), true_values(session.world))))
            self.assertEqual(problems, [], f"seed {seed}")

    def test_detector_catches_a_forbidden_key_and_a_renamed_value(self):
        session = GameSession(7)
        hidden = true_values(session.world)
        view = json.loads(json.dumps(session.state()))
        self.assertEqual(leaks(view, hidden), [])
        pid = view["squad"][0]["player_id"]
        view["squad"][0]["skill"] = 0.5
        self.assertTrue(leaks(view, hidden))
        del view["squad"][0]["skill"]
        view["squad"][0]["harmless_name"] = session.world.players[pid].composite_skill()
        self.assertTrue(leaks(view, hidden))

    def test_reveal_is_locked_until_the_season_ends(self):
        session = GameSession(7)
        with self.assertRaises(ValueError):
            session.reveal()
        play_season(session, lambda payload: None)
        self.assertIn("skill_true", session.reveal()["players"][0])


if __name__ == "__main__":
    unittest.main()
