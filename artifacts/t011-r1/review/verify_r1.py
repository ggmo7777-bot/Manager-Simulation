"""Reproducible T011-R1 diagnostic experiments; never edits the frozen runtime.

Run from repository: python3 -B artifacts/t011-r1/review/verify_r1.py factorial|audit
"""
from collections import defaultdict
import copy
from dataclasses import asdict, replace
from decimal import Decimal
import hashlib
import importlib.util
import json
from pathlib import Path
from statistics import mean
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from soccer_sim import league_economy as le
from soccer_sim.experiment_t011 import T011_METRICS, _make_resamples
from soccer_sim.experiment_t002 import summarize_differences
from soccer_sim.storage import digest, write_json

OUT = ROOT / "artifacts/t011-r1"
SEEDS = tuple(range(100, 130))


def old_module():
    package = ROOT / "artifacts/t011/source_bundle/soccer_sim"
    spec = importlib.util.spec_from_file_location("_t011_unfixed", package / "__init__.py",
                                                submodule_search_locations=[str(package)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return importlib.import_module("_t011_unfixed.league_economy")


def factorial():
    old = old_module()
    original = old.decide_transfer_bidding

    def public_tie(**kwargs):
        result = original(**kwargs)
        accepted = sorted((o for o in result["club_offers"] if o["offer_accepted"]),
                          key=lambda o: (-o["acceptance_score"], o["to_club"]))
        choice = accepted[0] if accepted and result["wants_transfer"] and result["release_consented"] else None
        result["chosen_to_club"] = None if choice is None else choice["to_club"]
        result["transfer_approved"] = choice is not None
        return result

    rows, by_variant = [], {}
    old_records = json.loads((ROOT / "artifacts/t011/experiments/comparison.json").read_text())["runs"]
    old_reference = {r["seed"]: r["summary"] for r in old_records
                     if r["set"] == "confirmation" and r["condition"] == "open_solidarity"}
    for name, bonus, public in (("old", 0.38, False), ("bonus_removed", 0.0, False),
                                ("public_tie_only", 0.38, True), ("both_fixed", 0.0, True)):
        old.decide_transfer_bidding = public_tie if public else original
        config = replace(old.config_for_condition("open_solidarity"), solidarity_curriculum_bonus=bonus)
        by_variant[name] = {}
        for seed in SEEDS:
            world = old.run_league_world(seed=seed, days=84, config=config)
            summary = old.summarize_league_world(world)
            metrics = {m: summary[m] for m in T011_METRICS}
            if name == "old":
                assert metrics == {m: old_reference[seed][m] for m in T011_METRICS}
            by_variant[name][seed] = metrics
            rows.append({"variant": name, "seed": seed, "days": 84, "metrics": metrics,
                         "runtime_patch": {"solidarity_curriculum_bonus": bonus, "public_tie": public}})
        print(f"factorial {name}: 30 runs complete", flush=True)
    old.decide_transfer_bidding = original
    resamples = _make_resamples(SEEDS)
    contrasts = {}
    for a, b in (("bonus_removed", "old"), ("public_tie_only", "old"),
                 ("both_fixed", "public_tie_only"), ("both_fixed", "bonus_removed"), ("both_fixed", "old")):
        contrasts[f"{a}_minus_{b}"] = {
            m: summarize_differences({s: by_variant[a][s][m] - by_variant[b][s][m] for s in SEEDS}, resamples)
            for m in T011_METRICS
        }
    write_json(OUT / "review/factorial.json", {
        "frozen_identity": old.league_identity(), "diagnostic_after_old_results_seen": True,
        "baseline_30_runs_match_stored_metrics": True, "run_count": len(rows), "runs": rows,
        "means": {v: {m: round(mean(by_variant[v][s][m] for s in SEEDS), 6) for m in T011_METRICS}
                  for v in by_variant}, "paired_contrasts": contrasts,
    })


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


def probes():
    base = le.run_league_world(seed=100, days=0)
    received, poor = copy.deepcopy(base), copy.deepcopy(base)
    for cid in le.ACADEMY_CLUB_IDS:
        received.club_accounts[cid].cumulative_solidarity_received = 20.0
    poor.club_accounts["club_a"].cash = 16.5
    initial = base.players["p1"].composite_skill()
    for w in (base, received, poor):
        le.advance_league_days(w, 1)
    assert base.players == received.players and base.events == received.events
    return {"seed": 100, "day": 1, "in_memory_interventions_not_saved_worlds": True,
            "receipt_history_only": {"players_equal": base.players == received.players, "events_equal": base.events == received.events},
            "facility_funding": {
                "funded_p1_gain": round(base.players["p1"].composite_skill() - initial, 6),
                "unfunded_p1_gain": round(poor.players["p1"].composite_skill() - initial, 6),
                "funded_facility_cost": base.club_accounts["club_a"].cumulative_facility_paid,
                "unfunded_facility_cost": poor.club_accounts["club_a"].cumulative_facility_paid,
            }}


def audit():
    report = {"identity": le.league_identity(), "mechanism_probes": probes(), "conditions": {}}
    for condition in le.T011_MAIN_CONDITIONS:
        cfg = le.config_for_condition(condition)
        directory = OUT / "representative" / condition
        start = time.perf_counter()
        full = le.run_league_world(seed=100, days=84, config=cfg)
        simulation = time.perf_counter() - start
        first = le.run_league_world(seed=100, days=42, config=cfg)
        le.save_league_artifacts(first, directory / "first_half")
        start = time.perf_counter()
        restored = le.load_league_snapshot(directory / "first_half/snapshot.json")
        load42 = time.perf_counter() - start
        le.advance_league_days(restored, 42)
        start = time.perf_counter()
        le.save_league_artifacts(full, directory / "full")
        save84 = time.perf_counter() - start
        le.save_league_artifacts(restored, directory / "resumed")
        start = time.perf_counter()
        loaded84 = le.load_league_snapshot(directory / "full/snapshot.json")
        load84 = time.perf_counter() - start
        assert loaded84 == full
        equality = {name: (directory / "full" / name).read_bytes() == (directory / "resumed" / name).read_bytes()
                    for name in ("snapshot.json", "events.jsonl")}
        assert all(equality.values())
        raw = json.loads((directory / "full/snapshot.json").read_text())
        report["conditions"][condition] = {"seed": 100, "days": 84, "byte_equal_84_vs_42_plus_42": equality,
            "simulation_seconds": simulation, "save84_seconds": save84, "load42_seconds_replay": load42,
            "load84_seconds_replay": load84, "ledger": ledger_audit(raw)}
        print(f"audit {condition}: resume and independent accounts passed", flush=True)
    long_raw = json.loads((OUT / "experiments/stress_1000/day1000/snapshot.json").read_text())
    report["long_horizon_independent_ledger"] = ledger_audit(long_raw)
    del long_raw
    factorial = json.loads((OUT / "review/factorial.json").read_text())
    new = json.loads((OUT / "experiments/comparison.json").read_text())
    expected = {r["seed"]: r["summary"] for r in new["runs"] if r["set"] == "confirmation" and r["condition"] == "open_solidarity"}
    for row in factorial["runs"]:
        if row["variant"] == "both_fixed":
            assert row["metrics"] == {m: expected[row["seed"]][m] for m in T011_METRICS}
    report["factorial_both_fixed_matches_new_30_runs_all_metrics"] = True
    inventory = json.loads((OUT / "preflight/frozen-inventory.json").read_text())
    changed = []
    for rel, original in inventory.items():
        p = ROOT / rel
        if not p.exists() or p.stat().st_size != original["bytes"] or hashlib.sha256(p.read_bytes()).hexdigest() != original["sha256"]:
            changed.append(rel)
    assert not changed, changed
    report["historical_files_unchanged"] = len(inventory)
    report["historical_changes"] = changed
    old = old_module()
    old_saved = old.load_league_snapshot(ROOT / "artifacts/t011/representative/full/snapshot.json")
    assert old_saved == old.run_league_world(seed=100, days=84, config=old.config_for_condition("open_solidarity"))
    report["frozen_old_reader_and_seed100_replay_match"] = True
    write_json(OUT / "review/verification.json", report)


if __name__ == "__main__":
    {"factorial": factorial, "audit": audit}[sys.argv[1]]()
