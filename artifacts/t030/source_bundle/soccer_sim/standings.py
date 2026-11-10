"""T024: per-season league table as a pure aggregate over `league_match_completed` events.

Deliberately outside the league fingerprint scope (nothing in the runtime imports it): it reads, never writes,
and is not part of the snapshot. Rules: docs/experiments/T024-PROTOCOL.md.
"""
from types import SimpleNamespace
from typing import Any

from soccer_sim.league_economy import CLUB_IDS, season_progress

MATCH_KIND = "league_match_completed"
_ZERO = ("played", "wins", "draws", "losses", "goals_for", "goals_against")


def _goals(value: Any) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("goals must be nonnegative integers")
    return value


def league_standings(world: Any) -> dict[str, Any]:
    """Return {"seasons": {season: {"complete": bool, "table": [row, ...]}}} from world.events only (T023 seasons)."""
    last = season_progress(world)["season"]  # raises when season_length_days is 0
    length = world.config.season_length_days
    clubs = {s: {c: dict.fromkeys(_ZERO, 0) for c in CLUB_IDS} for s in range(1, last + 1)}
    for ev in world.events:
        if ev["kind"] != MATCH_KIND:
            continue
        data = ev["data"]
        home, away = data["home_club"], data["away_club"]
        if home not in CLUB_IDS or away not in CLUB_IDS or home == away:
            raise ValueError("unknown or identical clubs in league match")
        hg, ag = _goals(data["home_goals"]), _goals(data["away_goals"])
        season = season_progress(SimpleNamespace(day=ev["day"], config=world.config))["season"]
        if not 1 <= season <= last:
            raise ValueError("league match outside the world's seasons")
        for club, gf, ga in ((home, hg, ag), (away, ag, hg)):
            row = clubs[season][club]
            row["played"] += 1
            row["goals_for"] += gf
            row["goals_against"] += ga
            row["wins" if gf > ga else "draws" if gf == ga else "losses"] += 1
    seasons = {}
    for season, rows in clubs.items():
        table = [{"club": c, **r, "goal_difference": r["goals_for"] - r["goals_against"], "points": 3 * r["wins"] + r["draws"]}
                 for c, r in rows.items()]
        table.sort(key=lambda r: (-r["points"], -r["goal_difference"], -r["goals_for"], -r["wins"], r["club"]))
        keys = [(r["points"], r["goal_difference"], r["goals_for"], r["wins"]) for r in table]
        for i, row in enumerate(table):
            row["rank"] = i + 1
            row["tied_with_previous"] = i > 0 and keys[i] == keys[i - 1]
        seasons[season] = {"complete": world.day >= season * length, "table": table}
    return {"seasons": seasons}
