"""End-of-season reveal: the only place true values leave the world. Called only once the season is over."""
from .observation import OWN_CLUB, OWN_COACH, squad_view


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
    return {"players": players, "advice_reasons": reasons}
