"""Independent 1-pass audit of T011 Policy decisions, advice feedback, and ledger conservation without calling Policy functions.

Author: Antigravity (Gemini 4 Argon) — distinct from the creator of T001-T007 (Sol 6.1 High).
"""
from __future__ import annotations

from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from soccer_sim import league_economy as le  # noqa: E402
from soccer_sim.match_2d import clamp, round6  # noqa: E402
from soccer_sim.storage import write_json  # noqa: E402


def _audit_single_world(world: le.LeagueWorldState) -> dict[str, int]:
    cfg = world.config
    counts = {
        "coach_training_decisions": 0,
        "player_training_decisions": 0,
        "lineup_decisions": 0,
        "transfer_window_decisions": 0,
        "advice_feedbacks": 0,
        "ledger_transactions": 0,
        "mismatches": 0,
    }

    club_cash = {
        cid: (cfg.initial_academy_cash if cid in le.ACADEMY_CLUB_IDS else cfg.initial_buyer_cash)
        for cid in le.CLUB_IDS
    }
    player_wages = {pid: 0.0 for pid in le.PLAYER_IDS}
    ext_inflow = 0.0
    ext_outflow = 0.0
    init_total = round6(sum(club_cash.values()))

    for ev in world.events:
        kind = ev["kind"]
        data = ev["data"]

        if kind == "coach_training_decision":
            counts["coach_training_decisions"] += 1
            cands = data["candidates"]
            best = max(
                cands,
                key=lambda c: (
                    float(c["score"]),
                    1 if c["kind"] == "propose" else (0 if c["kind"] == "observe" else -1),
                    str(c["target"] or ""),
                    str(c["method"] or ""),
                ),
            )
            for c in cands:
                comp_sum = round6(sum(float(v) for v in c["components"].values()))
                base = (
                    0.12
                    if c["kind"] == "observe"
                    else (0.15 if c["kind"] == "propose" else (0.08 if c["kind"] == "scout" else 0.0))
                )
                if abs(round6(base + comp_sum) - float(c["score"])) > 1e-5:
                    counts["mismatches"] += 1
            if best != data["chosen"]:
                counts["mismatches"] += 1

        elif kind == "player_training_decision":
            counts["player_training_decisions"] += 1
            cands = data["candidates"]
            best = max(cands, key=lambda c: (float(c["score"]), c["action"] != "rest", str(c["action"])))
            for c in cands:
                comp_sum = round6(sum(float(v) for v in c["components"].values()))
                if abs(comp_sum - float(c["score"])) > 1e-5:
                    counts["mismatches"] += 1
            if best != data["chosen"]:
                counts["mismatches"] += 1

        elif kind == "lineup_decision":
            counts["lineup_decisions"] += 1
            ranking = list(data["ranking"])
            for item in ranking:
                comp_sum = round6(sum(float(v) for v in item["components"].values()))
                if abs(comp_sum - float(item["score"])) > 1e-5:
                    counts["mismatches"] += 1
            sorted_r = sorted(ranking, key=lambda x: (-float(x["score"]), str(x["player_id"])))
            exp_starters = [sorted_r[0]["player_id"], sorted_r[1]["player_id"]]
            if exp_starters != list(data["starters"]):
                counts["mismatches"] += 1

        elif kind == "transfer_window_decision":
            counts["transfer_window_decisions"] += 1
            offers = data["club_offers"]
            valid_acc = [
                o
                for o in offers
                if bool(o["offer_made"]) and float(o["acceptance_score"]) > 0.0 and bool(o["offer_accepted"])
            ]
            valid_acc.sort(
                key=lambda o: (-float(o["acceptance_score"]), -float(o["offer_score"]), str(o["to_club"]))
            )
            exp_approved = bool(data["wants_transfer"] and data["release_consented"] and len(valid_acc) > 0)
            exp_to = str(valid_acc[0]["to_club"]) if exp_approved else None
            if exp_approved != bool(data["transfer_approved"]) or exp_to != data["chosen_to_club"]:
                counts["mismatches"] += 1

        elif kind == "advice_feedback":
            counts["advice_feedbacks"] += 1
            obs_g = float(data["obs_gain"])
            s_pred = float(data["self_prediction"])
            c_pred = float(data["coach_prediction"])
            old_t = float(data["old_trust"])
            err_s = abs(obs_g - s_pred)
            err_c = abs(obs_g - c_pred)
            exp_t = round6(clamp(old_t + cfg.trust_alpha * cfg.trust_scale * (err_s - err_c), 0.05, 0.95))
            if abs(exp_t - float(data["new_trust"])) > 1e-5:
                counts["mismatches"] += 1

        elif kind == "ledger_transaction":
            counts["ledger_transactions"] += 1
            payer = str(data["payer"])
            payee = str(data["payee"])
            amt = float(data["amount"])
            if payer in club_cash:
                club_cash[payer] = round6(club_cash[payer] - amt)
                if club_cash[payer] < -1e-9:
                    counts["mismatches"] += 1
            elif payer == "external_league_pool":
                ext_inflow = round6(ext_inflow + amt)
            else:
                counts["mismatches"] += 1

            if payee in club_cash:
                club_cash[payee] = round6(club_cash[payee] + amt)
            elif payee in player_wages:
                player_wages[payee] = round6(player_wages[payee] + amt)
            elif payee == "external_treasury_outflow":
                ext_outflow = round6(ext_outflow + amt)
            else:
                counts["mismatches"] += 1

            lhs = round6(init_total + ext_inflow)
            rhs = round6(sum(club_cash.values()) + sum(player_wages.values()) + ext_outflow)
            if round6(abs(lhs - rhs)) > 1e-5:
                counts["mismatches"] += 1

    return counts


def main() -> None:
    # First generate the 5 worlds before disabling Policy functions
    worlds: list[tuple[str, le.LeagueWorldState]] = []
    worlds.append(
        ("rep_seed17_open_solidarity", le.run_league_world(seed=17, days=84, config=le.config_for_condition("open_solidarity")))
    )
    for cond in le.T011_MAIN_CONDITIONS:
        worlds.append(
            (f"seed100_{cond}", le.run_league_world(seed=100, days=84, config=le.config_for_condition(cond)))
        )

    # Disable all Policy functions to guarantee 0 calls during audit
    policy_calls = 0

    def _forbidden(*_a: Any, **_kw: Any) -> Any:
        nonlocal policy_calls
        policy_calls += 1
        raise AssertionError("Policy function must not be called during independent audit")

    le.decide_coach_training = _forbidden  # type: ignore[assignment]
    le.decide_player_training = _forbidden  # type: ignore[assignment]
    le.decide_club_lineup = _forbidden  # type: ignore[assignment]
    le.decide_transfer_bidding = _forbidden  # type: ignore[assignment]

    totals = {
        "coach_training_decisions": 0,
        "player_training_decisions": 0,
        "lineup_decisions": 0,
        "transfer_window_decisions": 0,
        "advice_feedbacks": 0,
        "ledger_transactions": 0,
        "total_mismatches": 0,
    }
    by_run: dict[str, Any] = {}
    for label, w in worlds:
        res = _audit_single_world(w)
        by_run[label] = res
        for k in (
            "coach_training_decisions",
            "player_training_decisions",
            "lineup_decisions",
            "transfer_window_decisions",
            "advice_feedbacks",
            "ledger_transactions",
        ):
            totals[k] += res[k]
        totals["total_mismatches"] += res["mismatches"]

    total_decisions = (
        totals["coach_training_decisions"]
        + totals["player_training_decisions"]
        + totals["lineup_decisions"]
        + totals["transfer_window_decisions"]
    )
    report = {
        "author": le.LEAGUE_AUTHOR,
        "world_kind": le.LEAGUE_WORLD_KIND,
        "policy_functions_called": policy_calls,
        "total_decisions_audited": total_decisions,
        "total_events_audited": total_decisions + totals["advice_feedbacks"] + totals["ledger_transactions"],
        **totals,
        "runs": by_run,
    }
    if policy_calls != 0 or totals["total_mismatches"] != 0:
        raise AssertionError(f"Audit failed: {report}")

    write_json(ROOT / "artifacts/t011/review/economy-audit.json", report)
    print(
        f"Audited {total_decisions} decisions + {totals['advice_feedbacks']} feedbacks + {totals['ledger_transactions']} ledger txs with 0 mismatches and 0 policy calls."
    )


if __name__ == "__main__":
    main()
