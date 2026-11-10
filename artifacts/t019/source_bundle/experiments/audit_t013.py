"""Independent audits. ledger_audit retained verbatim from frozen T011-R1. No output side effects."""
from collections import Counter, defaultdict
from decimal import Decimal


def ledger_audit(raw):
    """Independent integer micro-unit accounting; no simulation accounting API."""
    def units(value):
        scaled = Decimal(str(value)) * 1_000_000
        assert scaled == scaled.to_integral_value()
        return int(scaled)

    accounts = raw["club_accounts"]
    balances = {cid: units(a["initial_cash"]) for cid, a in accounts.items()}
    wages = {pid: 0 for pid in raw["players"]}
    spent, received = defaultdict(int), defaultdict(int)
    inflow = outflow = count = 0
    initial = sum(balances.values())
    for ev in raw["events"]:
        if ev["kind"] != "ledger_transaction":
            continue
        count += 1
        d = ev["data"]; payer, payee, amount, reason = d["payer"], d["payee"], units(d["amount"]), d["reason"]
        assert amount >= 0
        if payer in balances:
            balances[payer] -= amount
            assert balances[payer] >= 0
            spent[payer, reason] += amount
        else:
            assert payer == "external_league_pool"
            inflow += amount
        if payee in balances:
            balances[payee] += amount
            received[payee, reason] += amount
        elif payee in wages:
            wages[payee] += amount
        else:
            assert payee == "external_treasury_outflow"
            outflow += amount
        assert initial + inflow == sum(balances.values()) + sum(wages.values()) + outflow
    paid_fields = {"cumulative_wages_paid": ("player_wage",), "cumulative_facility_paid": ("training_facility_operation",),
                   "cumulative_scouting_paid": ("scout_expense",), "cumulative_transfer_fees_paid": ("transfer_base_fee",),
                   "cumulative_solidarity_paid": ("transfer_solidarity_grant", "development_appearance_dividend")}
    received_fields = {"cumulative_inflow": ("matchday_revenue",), "cumulative_transfer_fees_received": ("transfer_base_fee",),
                       "cumulative_solidarity_received": ("transfer_solidarity_grant", "development_appearance_dividend")}
    for cid, account in accounts.items():
        assert balances[cid] == units(account["cash"])
        for field, reasons in paid_fields.items():
            assert units(account[field]) == sum(spent[cid, r] for r in reasons)
        for field, reasons in received_fields.items():
            assert units(account[field]) == sum(received[cid, r] for r in reasons)
    for pid in wages:
        assert wages[pid] == units(raw["players"][pid]["cumulative_wages_received"])
    assert initial == units(raw["ledger"]["initial_total_cash"])
    assert inflow == units(raw["ledger"]["cumulative_external_inflow"])
    assert outflow == units(raw["ledger"]["cumulative_external_outflow"])
    return {"transactions": count, "account_reconciliation": True, "conservation_at_each_transaction": True}


def admission_audit(raw):
    """Reconstruct payments/requests from facts; never call the guarded runtime."""
    events = {e["id"]: e for e in raw["events"]}
    paid_keys, paid_requests, resolved_requests = set(), set(), set()
    triggers, counts = Counter(), Counter()
    previous = {pid: [] for pid in raw["players"]}
    for e in raw["events"]:
        d = e["data"]
        if e["kind"] == "transfer_completed":
            assert d["to_club"] not in previous[d["player_id"]]
            previous[d["player_id"]].append(d["from_club"])
        elif e["kind"] == "ledger_transaction" and d["reason"] == "development_appearance_dividend":
            request = events[e["cause_refs"][0]]
            assert request["kind"] == "dividend_claim_submitted"
            r = request["data"]
            contract = raw["ledger"]["transfer_contracts"][r["transfer_id"]]
            match = events[r["match_event_ref"]]
            assert match["kind"] == "league_match_completed"
            assert match["data"]["match_id"] == r["match_id"]
            assert contract["day"] < match["day"] == e["day"]
            assert d["payer"] == contract["to_club"]
            assert d["payee"] == r["claimant_club"] == contract["origin_club_id"]
            assert d["amount"] == contract["dividend_per_start"] > 0
            assert any(match["data"][f"{side}_club"] == contract["to_club"] and
                       contract["player_id"] in match["data"][f"{side}_starters"] for side in ("home", "away"))
            key = f'{r["transfer_id"]}:{r["match_id"]}'
            assert key not in paid_keys and request["id"] not in paid_requests
            paid_keys.add(key); paid_requests.add(request["id"])
            triggers[r["transfer_id"]] += 1
            assert triggers[r["transfer_id"]] <= contract["max_dividend_triggers"]
            counts["payments"] += 1
        elif e["kind"] == "dividend_claim_resolved":
            request = events[d["request_ref"]]
            assert request["kind"] == "dividend_claim_submitted"
            assert request["id"] not in resolved_requests
            resolved_requests.add(request["id"])
            r = request["data"]
            assert d["claim_key"] == f'{r["transfer_id"]}:{r["match_id"]}'
            assert d["accepted"] == (request["id"] in paid_requests)
            if d["accepted"]:
                assert events[d["transaction_ref"]]["cause_refs"] == [request["id"]]
            else:
                assert d["transaction_ref"] is None
            if d["reason"] == "duplicate":
                assert d["claim_key"] in paid_keys and not d["accepted"]
            counts[f'claim_{d["reason"]}'] += 1
        elif e["kind"] == "transfer_offer_resolved":
            request = events[e["cause_refs"][0]]
            assert request["kind"] == "transfer_offer_requested"
            assert request["id"] not in resolved_requests
            resolved_requests.add(request["id"])
            assert request["data"]["to_club"] == d["to_club"]
            if d["reason"] == "circular_transfer":
                assert d["to_club"] in previous[d["player_id"]] and not d["accepted"]
                counts["circular_rejected"] += 1
    submitted = {e["id"] for e in raw["events"] if e["kind"] in
                 ("dividend_claim_submitted", "transfer_offer_requested")}
    assert submitted == resolved_requests
    assert sorted(paid_keys) == raw["ledger"]["claimed_dividend_keys"]
    for tid, contract in raw["ledger"]["transfer_contracts"].items():
        assert contract["dividend_triggers_used"] == triggers[tid]
    assert counts["claim_duplicate"] == raw["ledger"]["blocked_duplicate_claims"]
    assert counts["circular_rejected"] == raw["ledger"]["blocked_circular_transfers"]
    return {"requests": len(submitted), "counts": dict(counts),
            "all_resolved_once": True, "no_duplicate_payment": True}

