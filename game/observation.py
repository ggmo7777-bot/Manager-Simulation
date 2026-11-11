"""Observation boundary: world state -> only what the player's club coach may know.

Every field is picked by name from the coach's own mind, public records, or events the coach
witnessed. Nothing is copied wholesale from PlayerTruth / PlayerMind / personality.
Labels follow the charter: 事実 / 観測 / 本人の理由 / 推定.
"""
from soccer_sim import league_economy as le

OWN_CLUB = "club_a"
OWN_COACH = "c1"
FACT, OBSERVED, REASON, ESTIMATE = "事実", "観測", "本人の理由", "推定"
_OBSERVATION_KINDS = ("coach_session_observation", "coach_match_observation", "coach_scout_observation")


def _observed_player(event):
    return event["data"].get("player_id") or event["data"].get("target_player_id")


def _observation_counts(world):
    counts = {}
    for event in world.events:
        if event["kind"] in _OBSERVATION_KINDS and event["actor"] == OWN_COACH:
            pid = _observed_player(event)
            counts[pid] = counts.get(pid, 0) + 1
    return counts


def _public_match_records(world):
    """Appearances and goals come from public match reports, not from PlayerTruth counters."""
    records = {}
    for event in world.events:
        if event["kind"] != "league_match_completed":
            continue
        for pid, stats in event["data"]["player_stats"].items():
            row = records.setdefault(pid, {"appearances": 0, "goals": 0})
            row["appearances"] += 1
            row["goals"] += stats["goals"]
    return records


def squad_view(world):
    coach = world.coaches[OWN_COACH]
    counts = _observation_counts(world)
    records = _public_match_records(world)
    beliefs = world.coach_beliefs.get(OWN_COACH, {})
    rows = []
    for pid in world.club_rosters[OWN_CLUB]:
        evaluation = coach.evaluations[pid]
        row = {
            "player_id": pid,
            "skill_estimate": evaluation.skill_estimate,
            "rating_estimate": evaluation.match_rating_estimate,
            "fatigue_estimate": evaluation.fatigue_estimate,
            "days_unseen": world.day - evaluation.last_seen_day,
            "observation_count": counts.get(pid, 0),
            "measurement_error": world.config.coach_noise,
            "appearances": records.get(pid, {}).get("appearances", 0),
            "goals": records.get(pid, {}).get("goals", 0),
            "receptiveness": None,
        }
        if pid in beliefs:
            mean, sd = le.belief_mean_sd(beliefs[pid])
            row["receptiveness"] = {"mean": mean, "sd": sd, "outcomes": beliefs[pid]["n"]}
        rows.append(row)
    return rows


def standings_view(world):
    rows = [{"club_id": cid, "points": a.points, "wins": a.wins, "draws": a.draws, "losses": a.losses,
             "goals_for": a.goals_for, "goals_against": a.goals_against, "played": a.matches_played}
            for cid, a in world.club_accounts.items()]
    return sorted(rows, key=lambda r: (-r["points"], -(r["goals_for"] - r["goals_against"]), r["club_id"]))


def recommended_starters(world):
    """The assistant coach's pick: the research lineup rule applied to the coach's own estimates."""
    decision = le.decide_club_lineup(coach=world.coaches[OWN_COACH], roster=list(world.club_rosters[OWN_CLUB]),
                                     config=world.config)
    return decision["starters"]


def _event_item(event):
    """Return a labelled item for an event the own coach may see, or None."""
    kind, data, day = event["kind"], event["data"], event["day"]
    if kind == "league_match_completed" and OWN_CLUB in (data["home_club"], data["away_club"]):
        keep = ("match_id", "home_club", "away_club", "home_starters", "away_starters", "home_goals", "away_goals",
                "home_shots", "away_shots", "player_stats")
        return {"label": FACT, "kind": kind, "day": day, **{k: data[k] for k in keep}}
    if kind == "lineup_decision" and event["actor"] == OWN_COACH:
        return {"label": FACT, "kind": kind, "day": day, "starters": data["starters"], "bench": data["bench"]}
    if kind == "transfer_completed" and OWN_CLUB in (data["from_club"], data["to_club"]):
        return {"label": FACT, "kind": kind, "day": day, "player_id": data["player_id"],
                "from_club": data["from_club"], "to_club": data["to_club"]}
    if kind == "coach_training_decision" and event["actor"] == OWN_COACH and data["chosen"]["kind"] == "propose":
        chosen = data["chosen"]
        return {"label": FACT, "kind": "advice_given", "day": day, "player_id": chosen["target"], "method": chosen["method"],
                "forecast": chosen["forecast"]}
    if kind == "coach_advice_outcome" and data["coach_id"] == OWN_COACH:
        return {"label": REASON, "kind": kind, "day": day, "player_id": data["player_id"], "method": data["method"],
                "response": data["response"], "stated_reason": data["stated_reason"],
                "action_followed": data["action_followed"]}
    if kind == "coach_session_observation" and event["actor"] == OWN_COACH:
        return {"label": OBSERVED, "kind": kind, "day": day, "player_id": data["player_id"], "action": data["action"],
                "obs_skill": data["obs_skill"], "obs_gain": data["obs_gain"], "obs_fatigue": data["obs_fatigue"]}
    if kind == "coach_match_observation" and event["actor"] == OWN_COACH:
        return {"label": OBSERVED, "kind": kind, "day": day, "player_id": data["player_id"], "match_id": data["match_id"],
                "rating_obs": data["rating_obs"], "obs_fatigue": data["obs_fatigue"]}
    if kind == "coach_scout_observation" and event["actor"] == OWN_COACH:
        return {"label": OBSERVED, "kind": kind, "day": day, "player_id": data["target_player_id"],
                "obs_skill": data["obs_skill"], "obs_fatigue": data["obs_fatigue"]}
    return None


def observed_events(world, since_index):
    items = (_event_item(event) for event in world.events[since_index:])
    return [item for item in items if item is not None]


def estimate_changes(before, after):
    """推定: how the coach's own estimates moved over a step (both inputs are squad_view rows)."""
    old = {row["player_id"]: row for row in before}
    changes = []
    for row in after:
        previous = old.get(row["player_id"])
        if previous is None:
            continue
        changes.append({"label": ESTIMATE, "player_id": row["player_id"],
                        "skill_before": previous["skill_estimate"], "skill_after": row["skill_estimate"],
                        "fatigue_before": previous["fatigue_estimate"], "fatigue_after": row["fatigue_estimate"]})
    return changes
