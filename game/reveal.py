"""End-of-season reveal: the only place true values leave the world. Called only once the season is over."""
from .observation import OWN_CLUB, OWN_COACH, squad_view, transfer_response


def season_reveal(world, season_start_day):
    """Own squad of the season that starts after `season_start_day`; call before the season-end aging and turnover."""
    estimates = {row["player_id"]: row for row in squad_view(world)}
    players = []
    for pid in world.club_rosters[OWN_CLUB]:
        truth, mind = world.players[pid], world.player_minds[pid]
        traits = world.player_personality.get(pid, {})
        players.append({
            "player_id": pid, "age": world.player_ages[pid],
            "skill_estimate": estimates[pid]["skill_estimate"], "skill_true": truth.composite_skill(),
            "fatigue_estimate": estimates[pid]["fatigue_estimate"], "fatigue_true": truth.fatigue,
            "cap_true": truth.cap, "trust_in_you_true": mind.trust_by_coach.get(OWN_COACH),
            "discipline_true": traits.get("discipline"), "emotional_stability_true": traits.get("emotional_stability"),
        })
    stated = {(e["day"], e["data"]["player_id"]): e["data"]["stated_reason"] for e in world.events
              if e["kind"] == "coach_advice_outcome" and e["data"]["coach_id"] == OWN_COACH}
    reasons = [{"day": e["day"], "player_id": e["data"]["player_id"], "response": e["data"]["response"],
                "stated_reason": stated[(e["day"], e["data"]["player_id"])], "true_reason": e["data"]["primary_reason"],
                "true_terms": e["data"]["terms"]}
               for e in world.events if e["kind"] == "player_advice_response" and e["data"]["coach_id"] == OWN_COACH
               and e["day"] > season_start_day]
    return {"players": players, "advice_reasons": reasons, "transfer_reasons": _transfer_reasons(world, season_start_day),
            "sale_reasons": _sale_reasons(world, season_start_day)}


def _transfer_reasons(world, season_start_day):
    """For each of club_a's offers this season: what the player said, beside the recorded gates and scores that decided it."""
    rows = []
    for event in world.events:
        decision = event["data"]
        if event["kind"] != "transfer_window_decision" or event["day"] <= season_start_day or not transfer_response(decision):
            continue
        own = next(o for o in decision["club_offers"] if o["to_club"] == OWN_CLUB)
        response, stated = transfer_response(decision)
        rows.append({"day": event["day"], "player_id": decision["player_id"], "response": response, "stated_reason": stated,
                     "true_cause": {"wants_transfer": decision["wants_transfer"], "request_score": decision["request_score"],
                                    "release_consented": decision["release_consented"], "release_surplus_score": decision["release_surplus_score"],
                                    "player_rank_in_from": decision["player_rank_in_from"], "acceptance_score": own["acceptance_score"],
                                    "chosen_to_club": decision["chosen_to_club"]}})
    return rows


def _sale_reasons(world, season_start_day):
    """For each own player sold this season: his stated reason beside the recorded request score and his rank in the squad."""
    return [{"day": e["day"], "player_id": e["data"]["player_id"], "to_club": e["data"]["chosen_to_club"], "stated_reason": "wants_move",
             "true_cause": {"request_score": e["data"]["request_score"], "player_rank_in_from": e["data"]["player_rank_in_from"],
                            "release_surplus_score": e["data"]["release_surplus_score"]}}
            for e in world.events if e["kind"] == "transfer_window_decision" and e["day"] > season_start_day
            and e["data"]["from_club"] == OWN_CLUB and e["data"]["chosen_to_club"] not in (None, OWN_CLUB)]
