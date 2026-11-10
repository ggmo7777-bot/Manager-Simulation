"""Independent private-input/edge audit using only serialized facts and explicit formulas."""
import json
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
METHODS = ("A", "B")
COACHES = ("c1", "c2")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path, default=ROOT / 'artifacts/t007/representative/full/snapshot.json')
    parser.add_argument('--out', type=Path, default=ROOT / 'artifacts/t007/review')
    args = parser.parse_args()
    data = json.loads(args.snapshot.read_text())
    config, events = data["config"], data["events"]
    policy, trust_rules = config["policy"], config["trust"]
    minds, gains, edges, offers, before, after, decisions, actions = {}, {}, {}, {}, {}, {}, {}, {}
    paid, post_observations, private_experiences = set(), set(), set()
    coach_skills, coach_rates, corrections = {}, {}, {}
    expected_feedback, actual_feedback = set(), set()
    coach_count = player_count = feedback_count = non_dominating = 0
    budgets = {}
    pair_trace = None
    for event in events:
        kind, actor, target, day, eid, row = (event[key] for key in ("kind", "actor", "target", "day", "event_id", "data"))
        if kind == "initial_player":
            minds[actor] = row["state"]["mind"]
            gains[actor] = {method: (minds[actor]["expected_gains"][i], eid) for i, method in enumerate(METHODS)}
        elif kind == "initial_trust":
            edges[actor, row["coach_id"]] = (row["trust_after"], eid)
        elif kind == "initial_assessment":
            coach_skills[actor, target] = (row["skill_estimate"], eid)
        elif kind == "coach_forecast_prior":
            coach_rates[actor] = (row["method_rates"], eid)
        elif kind == "initial_coach_adjustment":
            for i, method in enumerate(METHODS):
                corrections[actor, target, method] = (row["residuals"][i], eid)
        elif kind == "coach_skill_belief":
            coach_skills[actor, target] = (row["skill_after"], eid)
        elif kind == "self_observation":
            (before if event["phase"] == "morning" else after)[day, actor] = event
        elif kind == "decision" and actor in COACHES:
            for memory in row["memory_ids"]:
                assert events[memory - 1]["actor"] == actor
                assert events[memory - 1]["day"] < day
            for candidate in row["candidates"]:
                if candidate["action"] != "propose":
                    continue
                player = candidate["target"]
                explanation = candidate["method_selection"]
                skill, skill_memory = coach_skills[actor, player]
                rates, rate_memory = coach_rates[actor]
                assert explanation["perceived_skill"] == skill
                assert explanation["skill_memory_id"] == skill_memory
                assert explanation["rate_memory_id"] == rate_memory
                for index, method in enumerate(METHODS):
                    residual, memory = corrections[actor, player, method]
                    base = rates[index] * (1.0 - skill)
                    assert explanation["forecasts"][method] == {
                        "base_gain": base, "residual_before": residual,
                        "expected_gain": max(-1.0, min(1.0, base + residual)),
                        "residual_memory_id": memory}
            context = events[row["observation_ids"][0] - 1]
            assert context["actor"] == actor and context["kind"] == "coach_context"
            assert context["causes"] == []
            coach_count += 1
        elif kind == "coach_action":
            assert row["slots"] == 1
            key = day, actor
            budgets[key] = budgets.get(key, 0) + 1
            assert budgets[key] <= 1
            if row["action"] == "propose":
                offers[day, target, actor] = event
            elif row["action"] == "observe":
                paid.add((day, actor, target))
        elif kind == "coach_observation":
            assert (day, actor, target) in paid
            assert row["skill_key"] == [f"coach-skill:{actor}", day, target, 0]
        elif kind == "coach_post_observation":
            key = day, actor, target
            assert key in paid and key not in post_observations
            assert row["skill_key"] == [f"coach-skill-post:{actor}", day, target, 0]
            post_observations.add(key)
        elif kind == "coach_method_experience":
            key = day, actor, target
            assert key in post_observations
            for memory in event["causes"]:
                assert events[memory - 1]["actor"] == actor
            assert row["method"] == actions[day, target]["data"]["action"]
            assert row["method"] in METHODS
            private_experiences.add(key)
            if row["updates_enabled"]:
                corrections[actor, target, row["method"]] = (row["residual_after"], eid)
        elif kind == "decision" and actor in minds:
            received = [
                {"coach_id": cid, "event_id": offers[day, actor, cid]["event_id"],
                 "method": offers[day, actor, cid]["data"]["method"],
                 "expected_gain": offers[day, actor, cid]["data"]["expected_gain"]}
                for cid in COACHES if (day, actor, cid) in offers]
            assert row["received"] == received
            pre = before[day, actor]
            fatigue = pre["data"]["fatigue_signal"]
            candidates = [{"action": "rest", "target": None, "method": None,
                           "terms": {"recovery_value": policy["rest_fatigue_weight"] * fatigue}}]
            sources = {}
            for i, method in enumerate(METHODS):
                matching = [proposal for proposal in received if proposal["method"] == method]
                source = max(matching, key=lambda proposal: edges[actor, proposal["coach_id"]][0]) if matching else None
                if source:
                    value, memory = edges[actor, source["coach_id"]]
                    sources[method] = {"coach_id": source["coach_id"], "proposal_id": source["event_id"],
                                       "trust": value, "trust_memory_id": memory}
                    advice = policy["advice_weight"] * (value - minds[actor]["autonomy"])
                else:
                    sources[method], advice = None, 0.0
                candidates.append({"action": method, "target": None, "method": method, "terms": {
                    "expected_gain": gains[actor][method][0], "preference": minds[actor]["preferences"][i],
                    "felt_fatigue_cost": -policy["training_fatigue_weight"] * fatigue, "advice": advice}})
            for candidate in candidates:
                candidate["score"] = sum(candidate["terms"].values())
            chosen = max(candidates, key=lambda candidate: candidate["score"])
            assert row["candidates"] == candidates and row["chosen"] == chosen
            assert row["score_sources"] == sources
            assert row["observation_ids"] == [pre["event_id"], *(proposal["event_id"] for proposal in received)]
            memories = [gains[actor][method][1] for method in METHODS]
            memories.extend(edges[actor, proposal["coach_id"]][1] for proposal in received)
            assert row["memory_ids"] == list(dict.fromkeys(memories))
            assert all(events[memory - 1]["actor"] == actor and events[memory - 1]["day"] < day for memory in memories)
            responses = [{"coach_id": proposal["coach_id"], "proposal_id": proposal["event_id"],
                          "method_match": proposal["method"] == chosen["method"],
                          "feedback_eligible": proposal["method"] == chosen["method"]} for proposal in received]
            assert row["proposal_responses"] == responses
            expected_feedback.update((day, actor, response["coach_id"]) for response in responses if response["feedback_eligible"])
            decisions[day, actor] = event
            player_count += 1
        elif kind == "player_action":
            assert row["slots"] == 1
            assert (day, actor) not in actions
            assert row["action"] == decisions[day, actor]["data"]["chosen"]["action"]
            actions[day, actor] = event
        elif kind == "advice_feedback":
            cid = row["coach_id"]
            key = day, actor, cid
            assert key in expected_feedback and key not in actual_feedback
            proposal = offers[key]
            method = proposal["data"]["method"]
            assert row["proposal_id"] == proposal["event_id"]
            assert row["method"] == actions[day, actor]["data"]["action"] == method
            old_trust, trust_memory = edges[actor, cid]
            q_self, self_memory = gains[actor][method]
            q_coach = proposal["data"]["expected_gain"]
            pre, post = before[day, actor], after[day, actor]
            observed = post["data"]["skill_signal"] - pre["data"]["skill_signal"]
            self_loss, coach_loss = (observed - q_self) ** 2, (observed - q_coach) ** 2
            evidence = max(-1.0, min(1.0, (self_loss - coach_loss) / trust_rules["scale"] ** 2))
            updated = max(0.0, min(1.0, old_trust + trust_rules["step"] * evidence)) if trust_rules["enabled"] else old_trust
            expected = {"trust_before": old_trust, "trust_after": updated,
                        "trust_change": updated - old_trust, "prior_trust_memory_id": trust_memory,
                        "q_self_before_action": q_self, "q_coach_received": q_coach,
                        "self_gain_memory_id": self_memory, "signed_observed_gain": observed,
                        "self_loss": self_loss, "coach_loss": coach_loss, "evidence": evidence}
            assert all(row[field] == value for field, value in expected.items()), (eid, expected)
            assert event["causes"] == list(dict.fromkeys((trust_memory, self_memory, proposal["event_id"],
                decisions[day, actor]["event_id"], actions[day, actor]["event_id"], pre["event_id"], post["event_id"])))
            expected_source = decisions[day, actor]["data"]["score_sources"][method]["coach_id"]
            assert row["score_source"] == expected_source
            non_dominating += cid != expected_source
            if updated != old_trust:
                edges[actor, cid] = (updated, eid)
            actual_feedback.add(key)
            feedback_count += 1
            if cid != expected_source and pair_trace is None:
                pair_trace = {"day": day, "player": actor, "interpretation": "Both received predictions concern the executed method; score source and feedback owner are separate.",
                              "decision": decisions[day, actor], "feedback": [item for item in events if item["kind"] == "advice_feedback" and item["day"] == day and item["actor"] == actor]}
        elif kind == "player_gain_belief":
            gains[actor][row["method"]] = (row["expected_after"], eid)
    assert expected_feedback == actual_feedback
    assert paid == post_observations
    expected_private = {key for key in paid if actions[key[0], key[2]]["data"]["action"] in METHODS}
    assert private_experiences == expected_private
    for pid, player in data["players"].items():
        assert "trust" not in player["mind"]
        for edge in player["mind"]["trust_edges"]:
            assert (edge["value"], edge["memory_id"]) == edges[pid, edge["coach_id"]]
    assert coach_count == player_count == data["day"] * 2
    assert len(budgets) == len(actions) == data["day"] * 2
    result = {"versions": data["versions"], "coach_decisions_checked": coach_count,
              "player_decisions_checked": player_count, "feedback_checked": feedback_count,
              "feedback_to_non_score_source": non_dominating,
              "paid_observations": len(paid), "coach_training_experiences": len(private_experiences),
              "all_attribution_scores_and_prior_references_match": True,
              "uses_production_policy_or_feedback_functions": False, "two_prediction_trace_found": pair_trace is not None}
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    (out / "attribution-audit.json").write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    if pair_trace is not None:
        (out / "causal-trace.json").write_text(json.dumps(pair_trace, sort_keys=True, indent=2) + "\n")
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
