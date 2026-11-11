"""Observation boundary: world state -> only what the player's club coach may know.

Every field is picked by name from the coach's own mind, public records, or events the coach
witnessed. Nothing is copied wholesale from PlayerTruth / PlayerMind / personality.
Labels follow the charter: 事実 / 観測 / 本人の理由 / 推定.
"""
import random
import re

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


def advice_records(world):
    """The coach's own record of advice answers per player: how often asked and refused, and the last wording the player gave."""
    records = {}
    for event in world.events:
        if event["kind"] == "coach_advice_outcome" and event["data"]["coach_id"] == OWN_COACH:
            row = records.setdefault(event["data"]["player_id"], {"asked": 0, "refused": 0, "last_stated_reason": None})
            row["asked"] += 1
            row["refused"] += event["data"]["response"] == "refused"
            row["last_stated_reason"] = event["data"]["stated_reason"]
    return records


_SURNAMES = "佐藤 鈴木 高橋 田中 伊藤 渡辺 山本 中村 小林 加藤 吉田 山田 松本 井上 木村 林 斎藤 清水 山口 池田".split()
_GIVEN_NAMES = "蒼 陽翔 湊 悠真 颯 大和 樹 律 蓮 朝陽 駿 隼人 拓海 翔太 凌 健太 瑛太 海斗 慎 遼".split()


def display_names(seed, world):
    """Game-side nicknames for every player id: a function of the seed and the id number only, unique within 400 ids, carrying no hidden value."""
    surnames, givens = random.Random(seed).sample(_SURNAMES, len(_SURNAMES)), random.Random(~seed).sample(_GIVEN_NAMES, len(_GIVEN_NAMES))
    width = len(surnames)
    names = {}
    for pid in world.players:
        number = int(pid[1:]) - 1
        names[pid] = surnames[number % width] + givens[(number // width + number) % width]
    return names


def notices_view(world, since_day):
    """Sales of own players since a day: what happened and the wording the player gave (the cause stays in the reveal)."""
    return [item for item in observed_events(world, 0) if item["kind"] == "player_sold" and item["day"] > since_day]


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
            "age": world.player_ages[pid],
            "skill_estimate": evaluation.skill_estimate,
            "rating_estimate": evaluation.match_rating_estimate,
            "fatigue_estimate": evaluation.fatigue_estimate,
            "days_unseen": world.day - evaluation.last_seen_day,
            "observation_count": counts.get(pid, 0),
            "measurement_error": world.config.coach_noise,
            "appearances": records.get(pid, {}).get("appearances", 0),
            "goals": records.get(pid, {}).get("goals", 0),
            "receptiveness": None,
            "advice_record": advice_records(world).get(pid, {"asked": 0, "refused": 0, "last_stated_reason": None}),
        }
        if pid in beliefs:
            mean, sd = le.belief_mean_sd(beliefs[pid])
            row["receptiveness"] = {"mean": mean, "sd": sd, "outcomes": beliefs[pid]["n"]}
        rows.append(row)
    return rows


def other_club_players(world):
    return [pid for pid in le._active_pids(world) if world.players[pid].club_id != OWN_CLUB]


def targets_view(world):
    """Players of other clubs as the coach knows them: his own estimate (scouting and match observations) and public records."""
    coach = world.coaches[OWN_COACH]
    counts, records = _observation_counts(world), _public_match_records(world)
    scoutable = set(scoutable_targets(world))
    rows = []
    for pid in other_club_players(world):
        evaluation = coach.evaluations[pid]
        rows.append({
            "player_id": pid, "club_id": world.players[pid].club_id, "age": world.player_ages[pid],
            "skill_estimate": evaluation.skill_estimate, "fatigue_estimate": evaluation.fatigue_estimate,
            "days_unseen": world.day - evaluation.last_seen_day, "observation_count": counts.get(pid, 0),
            "measurement_error": world.config.scout_noise,
            "appearances": records.get(pid, {}).get("appearances", 0), "goals": records.get(pid, {}).get("goals", 0),
            "scoutable": pid in scoutable})
    return rows


def scoutable_targets(world):
    """Who the coach could scout next training day: the research rule itself (the club must afford the cost plus its reserve)."""
    decision = le.decide_coach_training(
        coach=world.coaches[OWN_COACH], own_roster=list(world.club_rosters[OWN_CLUB]), other_players=other_club_players(world),
        club_cash_units=world.club_accounts[OWN_CLUB].cash_units, day=world.day + 1, slot=2, already_targeted=set(), config=world.config)
    return [c["target"] for c in decision["candidates"] if c["kind"] == "scout"]


def required_offer_fee(config):
    """Fee the research market asks of a buyer up front (same expression as decide_transfer_bidding; a test pins them together)."""
    if config.solidarity_mode == "enabled":
        return config.base_transfer_fee_solidarity_units + config.solidarity_transfer_grant_units
    return config.base_transfer_fee_no_solidarity_units


def market_view(world, season_days):
    """Own budget and the public market rules; window is the next transfer window (the research rule's days)."""
    config, roster = world.config, world.club_rosters[OWN_CLUB]
    upcoming = next((day for day in range(world.day + 1, world.day + season_days + 1) if le.is_transfer_window_day(config, day)), None)
    window = None if upcoming is None else {
        "season": (upcoming - 1) // season_days + 1, "day": (upcoming - 1) % season_days + 1, "open_now": upcoming == world.day + 1}
    return {"cash": world.club_accounts[OWN_CLUB].cash_units, "scout_cost": config.scout_action_cost_units,
            "reserve": config.buyer_min_cash_reserve_units, "offer_fee": required_offer_fee(config),
            "roster_size": len(roster), "roster_min": le.MIN_CLUB_ROSTER, "roster_max": le.club_roster_max(config), "window": window}


def recommended_offers(world):
    """The assistant coach's picks: the research acquisition rule applied to the coach's own estimates."""
    roster = list(world.club_rosters[OWN_CLUB])
    return [pid for pid in other_club_players(world)
            if le.decide_transfer_acquisition(coach=world.coaches[OWN_COACH], roster=roster, player_id=pid, config=world.config)["wants_offer"]]


_ACCOUNT_FIELDS = ("points", "wins", "draws", "losses", "goals_for", "goals_against", "matches_played")


def account_totals(world):
    """Cumulative club records; a season's table is the difference from the totals at its start."""
    return {cid: {name: getattr(account, name) for name in _ACCOUNT_FIELDS} for cid, account in world.club_accounts.items()}


def standings_view(world, baseline):
    rows = []
    for cid, totals in account_totals(world).items():
        season = {name: totals[name] - baseline[cid][name] for name in _ACCOUNT_FIELDS}
        rows.append({"club_id": cid, "points": season["points"], "wins": season["wins"], "draws": season["draws"],
                     "losses": season["losses"], "goals_for": season["goals_for"], "goals_against": season["goals_against"],
                     "played": season["matches_played"]})
    return sorted(rows, key=lambda r: (-r["points"], -(r["goals_for"] - r["goals_against"]), r["club_id"]))


def recommended_starters(world):
    """The assistant coach's pick: the research lineup rule applied to the coach's own estimates."""
    decision = le.decide_club_lineup(coach=world.coaches[OWN_COACH], roster=list(world.club_rosters[OWN_CLUB]),
                                     config=world.config)
    return decision["starters"]


def transfer_response(decision):
    """(response, stated reason) of the player who received club_a's offer, from a transfer_window_decision record; None if no offer.
    Only the recorded gates are read. The stated reason is the coarse wording of the gate that stopped him; the scores stay in the reveal."""
    own = next((o for o in decision["club_offers"] if o["to_club"] == OWN_CLUB and o["offer_made"]), None)
    if own is None:
        return None
    if decision["chosen_to_club"] == OWN_CLUB:
        return "accepted", "accepts"
    if not decision["wants_transfer"]:
        return "declined", "not_looking"
    if not decision["release_consented"]:
        return "blocked", None  # the selling club did not let him go: a fact of the market, not his reason
    return "declined", "terms_unattractive" if not own["offer_accepted"] else "chose_other_club"


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
    if kind == "transfer_offer_resolved" and data["to_club"] == OWN_CLUB:
        return {"label": FACT, "kind": "offer_resolved", "day": day, "player_id": data["player_id"],
                "accepted": data["accepted"], "reason": data["reason"]}
    if kind == "transfer_window_decision" and transfer_response(data):
        response, stated = transfer_response(data)
        return {"label": FACT if stated is None else REASON, "kind": "transfer_response", "day": day,
                "player_id": data["player_id"], "response": response, "stated_reason": stated}
    if kind == "transfer_window_decision" and data["from_club"] == OWN_CLUB and data["chosen_to_club"] not in (None, OWN_CLUB):
        # the research rule sells a player only when he asked to leave, so the stated reason is always his own request
        return {"label": REASON, "kind": "player_sold", "day": day, "player_id": data["player_id"],
                "to_club": data["chosen_to_club"], "stated_reason": "wants_move"}
    if kind == "season_retired" and data["club_id"] == OWN_CLUB:
        return {"label": REASON, "kind": kind, "day": day, "player_id": data["player_id"], "age": data["age"],
                "reason": data["reason"]}
    if kind == "season_youth_joined" and data["club_id"] == OWN_CLUB:
        return {"label": FACT, "kind": kind, "day": day, "player_id": data["player_id"], "age": data["age"]}
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


def is_your_event(item, move, first_day):
    """Whether an observed item is the direct consequence of the player's own input (the rest is the assistant coach and the market)."""
    kind, pid = item["kind"], item.get("player_id")
    if move["kind"] == "train":
        advice, scout = move["advice"], move["scout"]
        if kind in ("advice_given", "coach_advice_outcome"):
            return bool(advice) and item["day"] == first_day and pid == advice["player_id"] \
                and (kind == "coach_advice_outcome" or item["method"] == advice["method"])
        return kind == "coach_scout_observation" and item["day"] == first_day and pid == scout
    if move["kind"] == "lineup":
        if kind in ("lineup_decision", "league_match_completed"):
            return True
        return kind in ("offer_resolved", "transfer_response", "transfer_completed") and pid in move["offers"]
    return False


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


def season_turnover(world, since_index):
    """Own-club retirements and newcomers recorded since an event index (public records, no hidden state)."""
    items = observed_events(world, since_index)
    return ([i for i in items if i["kind"] == "season_retired"], [i for i in items if i["kind"] == "season_youth_joined"])


# Structural leak guard: every key a player-visible payload may contain. A new field must be added here on purpose.
# Dynamic keys (player ids in player_stats, club ids) are matched by _ID_KEY.
VISIBLE_KEYS = frozenset("""
action action_followed age appearances away_club away_goals away_shots away_starters bench champion club_id day days_unseen
draws estimate_changes events fatigue_after fatigue_before fatigue_estimate forecast from_club goals goals_against goals_for
history home_club home_goals home_shots home_starters joined kind label losses match_id mean measurement_error method methods
obs_fatigue obs_gain obs_skill observation_count outcomes passes_completed phase played player_id player_stats points rank
rating_estimate rating_obs receptiveness recommended_starters response retired sd season season_days seed shots skill_after
reason skill_before skill_estimate skill_mae squad squad_size standings starters state stated_reason tackles_won to_club week wins
share_code
accepted market cash scout_cost reserve offer_fee roster_size roster_min roster_max window open_now targets scoutable
recommended_offers
your_move your_events auto_events advice scout offers names notices advice_record asked refused last_stated_reason
""".split())
_ID_KEY = re.compile(r"(p|c)\d+|club_[a-z]+")


def check_visible(node, path="view"):
    """Raise if a player-visible payload carries a key outside the allowlist (a new or hidden field)."""
    if isinstance(node, dict):
        for key, child in node.items():
            if key not in VISIBLE_KEYS and not _ID_KEY.fullmatch(key):
                raise ValueError(f"{path}.{key}: field is not on the player-visible allowlist")
            check_visible(child, f"{path}.{key}")
    elif isinstance(node, list):
        for index, child in enumerate(node):
            check_visible(child, f"{path}[{index}]")
    return node
