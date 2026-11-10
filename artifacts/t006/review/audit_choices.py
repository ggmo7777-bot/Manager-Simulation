"""Reconstruct coach scoring directly from chronological evidence, without importing Policy."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
METHODS = ("A", "B")


def main():
    data = json.loads((ROOT / "artifacts/t006/representative/full/snapshot.json").read_text())
    config = data["config"]["policy"]
    assert config["coach_method_mode"] == "forecast_rank"
    skills, recency, residuals = {}, {}, {}
    rates, rate_id = None, None
    events = data["events"]
    checked_decisions = checked_proposals = rank_changed = 0
    trace = None
    for event in events:
        row, target, eid = event["data"], event["target"], event["event_id"]
        kind = event["kind"]
        if kind == "initial_assessment":
            skills[target] = (row["skill_estimate"], eid)
            recency[target] = (0, eid)
        elif kind == "coach_skill_belief":
            skills[target] = (row["skill_after"], eid)
        elif kind == "coach_recency":
            recency[target] = (row["last_seen_after"], eid)
        elif kind == "coach_forecast_prior":
            rates, rate_id = dict(zip(METHODS, row["method_rates"])), eid
        elif kind == "initial_coach_adjustment":
            residuals[target] = {method: (row["residuals"][i], eid) for i, method in enumerate(METHODS)}
        elif kind == "coach_method_experience" and row["updates_enabled"]:
            residuals[target][row["method"]] = (row["residual_after"], eid)
        elif kind == "decision" and event["actor"] == "coach":
            context_id, = row["observation_ids"]
            context = events[context_id - 1]
            assert context["kind"] == "coach_context" and context["event_id"] < eid
            assert context["day"] == event["day"]
            available = context["data"]["available"]
            expected = []
            memories = set()
            for action, player, method in available:
                selection = None
                if action == "rest":
                    terms = {"rest_baseline": config["coach_rest_value"]}
                else:
                    skill, skill_id = skills[player]
                    memories.add(skill_id)
                    if action == "observe":
                        last, recency_id = recency[player]
                        memories.add(recency_id)
                        terms = {
                            "staleness": config["coach_observation_weight"] * min(1.0, (event["day"] - last) / config["coach_freshness_days"]),
                            "uncertain_development_need": config["coach_observation_need"] * (1.0 - skill),
                        }
                    else:
                        forecasts = {}
                        for name in METHODS:
                            residual, memory = residuals[player][name]
                            base = rates[name] * (1.0 - skill)
                            forecasts[name] = {"base_gain": base, "residual_before": residual,
                                               "expected_gain": max(-1.0, min(1.0, base + residual)),
                                               "residual_memory_id": memory}
                            memories.add(memory)
                        memories.add(rate_id)
                        qa, qb = (forecasts[name]["expected_gain"] for name in METHODS)
                        preferred = None if qa == qb else ("A" if qa > qb else "B")
                        if preferred is None:
                            fit = config["coach_method_fit"] * ((1.0 - skill) if method == "A" else skill)
                        else:
                            fit = config["coach_method_fit"] * max(skill, 1.0 - skill) if method == preferred else 0.0
                        terms = {"development_need": config["coach_need_weight"] * (1.0 - skill), "method_fit": fit}
                        selection = {"mode": "forecast_rank", "basis": "tie_legacy" if preferred is None else "forecast_rank",
                                     "preferred_method": preferred, "perceived_skill": skill, "method_rates": rates,
                                     "skill_memory_id": skill_id, "rate_memory_id": rate_id, "forecasts": forecasts}
                        checked_proposals += 1
                candidate = {"action": action, "target": player, "method": method,
                             "terms": terms, "score": sum(terms.values())}
                if selection is not None:
                    candidate["method_selection"] = selection
                expected.append(candidate)
            assert row["candidates"] == expected, eid
            chosen = max(expected, key=lambda candidate: candidate["score"])
            assert row["chosen"] == chosen, eid
            assert set(row["memory_ids"]) == memories
            assert set(event["causes"]) == {context_id, *memories}
            assert all(events[memory - 1]["day"] < event["day"] for memory in memories)
            checked_decisions += 1
            if chosen["action"] == "propose":
                selection = chosen["method_selection"]
                skill = selection["perceived_skill"]
                legacy_fits = {"A": config["coach_method_fit"] * (1.0 - skill),
                               "B": config["coach_method_fit"] * skill}
                legacy_method = max(legacy_fits, key=legacy_fits.get)
                if chosen["method"] != legacy_method:
                    rank_changed += 1
                    player_choice = next(item for item in events if item["kind"] == "decision"
                                         and item["actor"] == chosen["target"] and item["day"] == event["day"])
                    if trace is None and player_choice["data"]["proposal_response"] == "accepted":
                        proposal = next(item for item in events if item["kind"] == "coach_action"
                                        and item["causes"][0] == eid)
                        player_action = next(item for item in events if item["kind"] == "player_action"
                                             and item["actor"] == chosen["target"] and item["day"] == event["day"])
                        update = next(item for item in events if item["kind"] == "state_update"
                                      and item["actor"] == chosen["target"] and item["day"] == event["day"])
                        sources = {prediction["residual_memory_id"] for prediction in selection["forecasts"].values()}
                        experience_sources = [events[source - 1] for source in sorted(sources)]
                        trace = {"interpretation": "Same-state legacy method is a scoring comparison, not an alternate full history.",
                                 "same_state_legacy_method": legacy_method, "prior_experiences": experience_sources,
                                 "coach_decision": event, "proposal": proposal,
                                 "player_decision": player_choice, "player_action": player_action, "true_update": update}
    assert checked_decisions == data["day"] * 2
    assert trace is not None
    out = ROOT / "artifacts/t006/review"
    (out / "causal-trace.json").write_text(json.dumps(trace, sort_keys=True, indent=2) + "\n")
    result = {"versions": data["versions"], "days": data["day"], "coach_decisions_checked": checked_decisions,
              "proposal_candidates_checked": checked_proposals, "proposals_differing_from_same_state_legacy": rank_changed,
              "all_scores_and_prior_references_match": True, "uses_production_policy_function": False,
              "trace_day": trace["coach_decision"]["day"], "trace_player": trace["coach_decision"]["target"]}
    (out / "choice-audit.json").write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
