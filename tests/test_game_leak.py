"""Game observation boundary: no true value of any player may reach the player's view or step results."""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from game import observation as obs
from game.session import GameSession
from soccer_sim import league_economy as le

FORBIDDEN_KEYS = {"traits", "cap", "rates", "trust", "trust_by_coach", "autonomy", "ambition", "loyalty_bias",
                  "discipline", "emotional_stability", "personality", "terms", "reason_codes", "primary_reason",
                  "skill", "composite_skill", "fatigue", "self_estimate", "expected_gains", "true_reason",
                  "true_cause", "request_score", "acceptance_score", "release_surplus_score", "wants_transfer", "release_consented"}
CLAMPED_FATIGUE = (0.0, 0.95)
PUBLIC_START_FATIGUE = le.YOUTH_PRIOR[2]  # the coach's constant first impression equals the common starting fatigue: no individual information
PRECISION = 6
SAVE_KEYS = {"version", "code", "seed", "inputs", "view_digest", "kind", "advice", "player_id", "method", "starters", "scout", "offers"}


def true_values(world):
    """Every hidden number of each player, rounded as the world rounds it: {player_id: set of numbers}."""
    values = {}
    for pid, truth in world.players.items():
        mind = world.player_minds[pid]
        numbers = [truth.composite_skill(), truth.cap, truth.traits.pace, truth.traits.passing, truth.traits.shooting,
                   *truth.rates.values(), mind.autonomy, mind.ambition, mind.loyalty_bias, mind.rest_preference,
                   *mind.trust_by_coach.values(), *mind.expected_gains.values(), *world.player_personality.get(pid, {}).values()]
        if truth.fatigue not in CLAMPED_FATIGUE and truth.fatigue != PUBLIC_START_FATIGUE:
            numbers.append(truth.fatigue)
        # zero is a clamp bound for observed fatigue (scouting reads can hit it): equality with it identifies nothing
        values[pid] = {round(number, PRECISION) for number in numbers} - {0.0}
    return values


def leaks(node, hidden, path="view", player=None):
    """Structural check first (allowlist + forbidden names); then a float equal to a hidden number of the
    player the surrounding record is about. Numbers outside any player's record are never compared, so another
    player's value cannot collide by chance."""
    found = []
    if isinstance(node, dict):
        player = node["player_id"] if node.get("player_id") in hidden else player
        for key, child in node.items():
            if key in FORBIDDEN_KEYS:
                found.append(f"{path}.{key}: forbidden key")
            elif key not in obs.VISIBLE_KEYS | SAVE_KEYS and not obs._ID_KEY.fullmatch(key):
                found.append(f"{path}.{key}: not on the allowlist")
            found += leaks(child, hidden, f"{path}.{key}", key if key in hidden else player)
    elif isinstance(node, list):
        for index, child in enumerate(node):
            found += leaks(child, hidden, f"{path}[{index}]", player)
    elif type(node) is float and player is not None and round(node, PRECISION) in hidden[player]:
        found.append(f"{path}: {node} equals a hidden true value of {player}")
    return found


def human_week(session):
    """One week with every human input in use: an advice, a scouting target, and bids for two players of other clubs at a window."""
    state = session.state()
    if session.phase == "training":
        scoutable = [t["player_id"] for t in state["targets"] if t["scoutable"]]
        return session.train({"player_id": state["squad"][0]["player_id"], "method": "A"}, scoutable[session.world.day % len(scoutable)] if scoutable else None)
    offers = [t["player_id"] for t in state["targets"]][:2] if state["market"]["window"] and state["market"]["window"]["open_now"] else []
    return session.play_match(state["recommended_starters"], offers)


def play_season(session, collect):
    collect(session.state())
    while session.phase != "season_end":
        collect(human_week(session))


def player_views(session):
    return [session.chronicle_view()] + [session.player_view(row["player_id"]) for row in session.state()["squad"]]


SEEDS = range(1, 21)
TRANSFER_KINDS = ("offer_resolved", "transfer_response", "coach_scout_observation")
SEASONS = 4  # transfer windows recur every season (game-v6), so bids are made in seasons 3 and 4 too


class TestPlayerViewHasNoTruth(unittest.TestCase):
    def test_no_true_value_reaches_the_view_or_save_in_any_step_of_many_seeds(self):
        for seed in SEEDS:
            session = GameSession(seed)
            problems = []
            for season in range(SEASONS):
                play_season(session, lambda payload: problems.extend(
                    leaks(json.loads(json.dumps([payload, player_views(session)])), true_values(session.world))))
                if season < SEASONS - 1:
                    session.next_season()
            save = json.loads(json.dumps(session.save_payload()))
            self.assertLessEqual(set(save) | {k for i in save["inputs"] for k in i}, SAVE_KEYS, f"seed {seed}")
            self.assertEqual(problems, [], f"seed {seed}")

    def test_scouting_and_transfer_inputs_are_exercised_by_the_sweep(self):
        """The sweep above must actually reach the new views: scouted targets, resolved offers and the players' responses."""
        seen = set()
        for seed in SEEDS:
            session = GameSession(seed)
            for season in range(SEASONS):
                play_season(session, lambda payload: seen.update(e["kind"] for e in payload.get("events", [])))
                if season < SEASONS - 1:
                    session.next_season()
        self.assertTrue(set(TRANSFER_KINDS) <= seen, seen)

    def test_bids_complete_in_season_three_and_later(self):
        late = 0
        for seed in SEEDS:
            session = GameSession(seed)
            for season in range(SEASONS):
                play_season(session, lambda payload: None)
                if season < SEASONS - 1:
                    session.next_season()
            late += sum(1 for e in session.world.events if e["kind"] == "transfer_completed" and e["data"]["to_club"] == "club_a" and e["day"] > 2 * 42)
        self.assertGreater(late, 0)

    def test_player_responses_carry_a_stated_reason_and_no_scores(self):
        for seed in SEEDS:
            session = GameSession(seed)
            responses = []
            play_season(session, lambda payload: responses.extend(e for e in payload.get("events", []) if e["kind"] == "transfer_response"))
            for item in responses:
                self.assertEqual(set(item), {"label", "kind", "day", "player_id", "response", "stated_reason"})
                self.assertIn(item["stated_reason"], ("accepts", "not_looking", "terms_unattractive", "chose_other_club", None))
                self.assertEqual(item["label"], "事実" if item["stated_reason"] is None else "本人の理由")

    def test_detector_catches_injected_leaks(self):
        """Self-test: each kind of deliberate leak must be reported, so a silent detector cannot pass the test above."""
        session = GameSession(7)
        hidden = true_values(session.world)
        view = json.loads(json.dumps(session.state()))
        self.assertEqual(leaks(view, hidden), [])
        row = view["squad"][0]
        truth = hidden[row["player_id"]]
        true_skill = session.world.players[row["player_id"]].composite_skill()

        def caught(mutate):
            copy = json.loads(json.dumps(view))
            mutate(copy["squad"][0])
            return bool(leaks(copy, hidden))
        self.assertTrue(caught(lambda r: r.update(skill=0.5)), "forbidden key")
        self.assertTrue(caught(lambda r: r.update(harmless_name=0.5)), "unknown key is outside the allowlist")
        self.assertTrue(caught(lambda r: r.update(skill_estimate=true_skill)), "true value under an allowed key")
        self.assertTrue(caught(lambda r: r.update(receptiveness={"mean": max(truth), "sd": 0.1, "outcomes": 1})), "nested true value")
        with self.assertRaises(ValueError):
            obs.check_visible({"squad": [{"player_id": "p1", "cap": 0.9}]})

    def test_another_players_value_is_not_a_leak(self):
        session = GameSession(7)
        hidden = true_values(session.world)
        view = json.loads(json.dumps(session.state()))
        first, second = (r["player_id"] for r in view["squad"][:2])
        coincidence = next(iter(hidden[second] - hidden[first]))
        view["squad"][0]["skill_estimate"] = coincidence
        self.assertEqual(leaks(view, hidden), [])

    def test_reveal_is_locked_until_the_season_ends(self):
        session = GameSession(7)
        with self.assertRaises(ValueError):
            session.reveal()
        play_season(session, lambda payload: None)
        self.assertIn("skill_true", session.reveal()["players"][0])


if __name__ == "__main__":
    unittest.main()
