"""T014: exact currency representation, boundaries, and persistence integrity."""
from copy import deepcopy
from dataclasses import fields, replace
import json
from pathlib import Path
import tempfile
import unittest

from soccer_sim import league_economy as le
from soccer_sim.money import MONEY_SCALE, format_money, money_units, validate_money_units


class TestMoneyConversion(unittest.TestCase):
    def test_tenths_and_minimum_unit_are_exact(self):
        self.assertEqual(money_units("0.1") + money_units("0.2"), money_units("0.3"))
        self.assertEqual(money_units("0.000001"), 1)
        self.assertEqual(money_units("1.00000100000"), 1000001)
        self.assertEqual(money_units("0.0000000000"), 0)

    def test_decimal_strings_round_trip_large_integers_without_float(self):
        for units in (0, 1, 100000, 1000001, 2**53 + 1, 10**40 + 123456):
            with self.subTest(units=units):
                self.assertEqual(money_units(format_money(units)), units)
                self.assertIs(type(money_units(format_money(units))), int)
        self.assertEqual(format_money(100000), "0.1")
        self.assertEqual(format_money(1000000), "1")
        self.assertEqual(money_units("9007199254.740993"), 2**53 + 1)

    def test_invalid_decimal_inputs_and_subunit_digits_are_rejected(self):
        for value in (True, 1, 0.1, None, "", "-0", "-1", "+1", " 1", "1 ",
                      "NaN", "Infinity", "1e3", ".1", "1.", "0.0000001", "1.123456001"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                money_units(value)

    def test_integer_apis_reject_bool_float_string_negative(self):
        for value in (True, False, 1.0, 0.0, "1", -1, float("nan"), float("inf"), None):
            with self.subTest(value=value):
                for function in (format_money, validate_money_units):
                    with self.assertRaises(ValueError):
                        function(value)


class TestIntegerLedger(unittest.TestCase):
    def world(self):
        return le.run_league_world(days=0, config=replace(le.LeagueEconomyConfig(),
            initial_academy_cash_units=0, initial_buyer_cash_units=0))

    def tx(self, world, payer, payee, units, reason="transfer_base_fee"):
        return le._record_ledger_tx(world, day=0, payer=payer, payee=payee,
            amount_units=units, reason=reason, cause_refs=["init:0"])

    def test_repeated_transfers_preserve_every_unit_and_cumulative_totals(self):
        w = self.world()
        self.tx(w, "external_league_pool", "club_a", 1001, "matchday_revenue")
        for _ in range(1001):
            self.tx(w, "club_a", "club_b", 1)
        self.assertEqual(w.club_accounts["club_a"].cash_units, 0)
        self.assertEqual(w.club_accounts["club_b"].cash_units, 1001)
        self.assertEqual(w.club_accounts["club_a"].cumulative_transfer_fees_paid_units, 1001)
        self.assertEqual(w.club_accounts["club_b"].cumulative_transfer_fees_received_units, 1001)
        self.assertEqual(le.compute_ledger_discrepancy(w), 0)
        self.assertTrue(all(type(e["data"]["amount_units"]) is int for e in w.events
                            if e["kind"] == "ledger_transaction"))

    def test_exact_balance_and_one_unit_insufficiency_are_atomic(self):
        w = self.world()
        self.tx(w, "external_league_pool", "club_a", 1)
        before = deepcopy((w.club_accounts, w.players, w.events))
        with self.assertRaises(ValueError):
            self.tx(w, "club_a", "club_b", 2)
        self.assertEqual((w.club_accounts, w.players, w.events), before)
        self.tx(w, "club_a", "club_b", 1)
        self.assertEqual(w.club_accounts["club_a"].cash_units, 0)
        self.assertEqual(w.club_accounts["club_b"].cash_units, 1)
        self.assertEqual(le.compute_ledger_discrepancy(w), 0)

    def test_huge_transfer_wages_and_outflow_have_no_float_round_trip(self):
        w = self.world()
        huge = 2**60 + 1
        self.tx(w, "external_league_pool", "club_a", huge, "matchday_revenue")
        self.tx(w, "club_a", "club_b", huge - 2)
        self.tx(w, "club_a", "p1", 1, "player_wage")
        self.tx(w, "club_a", "external_treasury_outflow", 1, "scout_expense")
        self.assertEqual(w.club_accounts["club_b"].cash_units, huge - 2)
        self.assertEqual(w.players["p1"].cumulative_wages_received_units, 1)
        self.assertEqual(w.ledger.cumulative_external_inflow_units, huge)
        self.assertEqual(w.ledger.cumulative_external_outflow_units, 1)
        self.assertIs(type(le.compute_ledger_discrepancy(w)), int)
        self.assertEqual(le.compute_ledger_discrepancy(w), 0)
        w.club_accounts["club_b"].cash_units += 1
        self.assertEqual(le.compute_ledger_discrepancy(w), 1)

    def test_invalid_amount_or_recipient_leaves_all_finances_untouched(self):
        w = self.world()
        for value in (True, 1.0, "1", -1, float("nan"), float("inf")):
            before = deepcopy(w)
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.tx(w, "external_league_pool", "club_a", value)
            self.assertEqual(w, before)
        for payer, payee in (("missing", "club_a"), ("external_league_pool", "missing")):
            before = deepcopy(w)
            with self.assertRaises(ValueError):
                self.tx(w, payer, payee, 1)
            self.assertEqual(w, before)

    def test_facility_and_transfer_budget_one_unit_boundaries(self):
        cfg = le.LeagueEconomyConfig()
        threshold = cfg.facility_reserve_floor_units + cfg.facility_daily_cost_units
        for units, active in ((threshold - 1, 0), (threshold, 1)):
            w = le.run_league_world(days=1, config=replace(cfg, initial_academy_cash_units=units))
            self.assertEqual(w.club_accounts["club_a"].facility_active_days, active)
        args = dict(from_club="club_a", to_club="club_b", previous_clubs=[], roster_size=2,
                    required_cash_units=1)
        self.assertFalse(le.resolve_transfer_offer_request(**args, cash_units=0)["accepted"])
        self.assertTrue(le.resolve_transfer_offer_request(**args, cash_units=1)["accepted"])
        for value in (True, 1.0, "1", -1):
            with self.assertRaises(ValueError):
                le.resolve_transfer_offer_request(**args, cash_units=value)


class TestMinimumUnitClaims(unittest.TestCase):
    def fixture(self, cash_units):
        # Focused admission fixture; no persistence claim for patched membership.
        w = le.run_league_world(days=0, config=replace(le.LeagueEconomyConfig(),
            initial_academy_cash_units=0, initial_buyer_cash_units=0))
        le._record_ledger_tx(w, day=0, payer="external_league_pool", payee="club_b",
                            amount_units=cash_units, reason="matchday_revenue", cause_refs=["init:0"])
        w.day = 35
        w.club_rosters["club_a"].remove("p3")
        w.club_rosters["club_b"].append("p3")
        w.players["p3"].club_id = "club_b"
        w.players["p3"].active_transfer_id = "tr1"
        w.ledger.transfer_contracts["tr1"] = le.TransferContractRecord(
            transfer_id="tr1", day=28, player_id="p3", origin_club_id="club_a",
            from_club="club_a", to_club="club_b", base_fee_units=0,
            solidarity_grant_units=0, dividend_per_start_units=1, max_dividend_triggers=6)
        ref = le._append_event(w, day=35, kind="league_match_completed", actor="world",
            data={"match_id": "m35", "home_club": "club_b", "away_club": "club_c",
                  "home_starters": ["p3", "p4"], "away_starters": ["p7", "p8"]},
            cause_refs=["init:0"])
        return w, dict(transfer_id="tr1", match_id="m35", claimant_club="club_a", match_event_ref=ref)

    def test_one_unit_payment_and_duplicate_share_admission(self):
        w, request = self.fixture(1)
        first = le.process_dividend_claim(w, **request)
        self.assertTrue(first["accepted"])
        self.assertEqual(w.club_accounts["club_b"].cash_units, 0)
        self.assertEqual(w.club_accounts["club_a"].cash_units, 1)
        second = le.process_dividend_claim(w, **request)
        self.assertEqual(second["reason"], "duplicate")
        self.assertEqual(w.club_accounts["club_a"].cash_units, 1)
        self.assertEqual(w.ledger.transfer_contracts["tr1"].dividend_triggers_used, 1)
        self.assertEqual(le.actual_duplicate_payments(w), 0)
        self.assertEqual(le.compute_ledger_discrepancy(w), 0)
        tx = next(e for e in w.events if e["id"] == first["transaction_ref"])
        self.assertEqual(tx["data"]["amount_units"], 1)
        self.assertIs(type(tx["data"]["amount_units"]), int)

    def test_one_unit_short_claim_leaves_balances_and_entitlement_untouched(self):
        w, request = self.fixture(0)
        before = deepcopy((w.club_accounts, w.ledger.transfer_contracts, w.ledger.claimed_dividend_keys))
        result = le.process_dividend_claim(w, **request)
        self.assertEqual(result["reason"], "insufficient_cash")
        self.assertEqual((w.club_accounts, w.ledger.transfer_contracts, w.ledger.claimed_dividend_keys), before)


class TestIntegerMoneyPersistence(unittest.TestCase):
    def test_representative_prices_and_config_schema_are_explicit(self):
        cfg = le.LeagueEconomyConfig()
        self.assertEqual(MONEY_SCALE, 1000000)
        for name, units in (("facility_daily_cost_units", 280000), ("scout_action_cost_units", 50000),
                            ("player_matchday_wage_units", 380000),
                            ("base_transfer_fee_solidarity_units", 9500000),
                            ("development_dividend_per_start_units", 1250000)):
            self.assertEqual(getattr(cfg, name), units)
        for spec in fields(cfg):
            if not spec.name.endswith("_units"):
                continue
            for invalid in (True, 0.0, "0", -1):
                with self.subTest(name=spec.name, invalid=invalid):
                    with self.assertRaises(ValueError):
                        replace(cfg, **{spec.name: invalid})
                    data = cfg.to_dict()
                    data[spec.name] = invalid
                    with self.assertRaises(ValueError):
                        le.LeagueEconomyConfig.from_dict(data)
        self.assertEqual(le.LeagueEconomyConfig.from_dict(cfg.to_dict()), cfg)

    def test_huge_custom_config_save_resume_preserves_integer_types(self):
        cfg = replace(le.LeagueEconomyConfig(), initial_academy_cash_units=2**60 + 1,
                      initial_buyer_cash_units=2**60 + 3)
        w = le.run_league_world(seed=100, days=7, config=cfg)
        with tempfile.TemporaryDirectory() as tmp:
            le.save_league_artifacts(w, tmp)
            loaded = le.load_league_snapshot(Path(tmp) / "snapshot.json")
            self.assertEqual(loaded, w)
            le.advance_league_days(loaded, 7)
            self.assertEqual(loaded, le.run_league_world(seed=100, days=14, config=cfg))
            self.assertEqual(le.compute_ledger_discrepancy(loaded), 0)
            summary = le.summarize_league_world(loaded)
            self.assertTrue(all(type(value) is int for name, value in summary.items()
                                if name.endswith("_units")))

    def test_snapshot_rejects_money_type_one_unit_history_and_old_version_edits(self):
        w = le.run_league_world(days=1)
        payload = le.snapshot_payload(w)
        mutations = [
            lambda p: p["club_accounts"]["club_a"].update(cash_units=float(p["club_accounts"]["club_a"]["cash_units"])),
            lambda p: p["players"]["p1"].update(cumulative_wages_received_units=False),
            lambda p: p["club_accounts"]["club_a"].update(cash_units=p["club_accounts"]["club_a"]["cash_units"] + 1),
            lambda p: next(e for e in p["events"] if e["kind"] == "ledger_transaction")["data"].update(amount_units=280000.0),
            lambda p: p["identity"].update(save=14),
            lambda p: p["identity"].update(money="float"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snapshot.json"
            for i, mutate in enumerate(mutations):
                data = deepcopy(payload)
                mutate(data)
                path.write_text(json.dumps(data))
                with self.subTest(mutation=i), self.assertRaises(ValueError):
                    le.load_league_snapshot(path)

    def test_constructors_and_serializers_reject_invalid_money_fields(self):
        w = le.run_league_world(days=0)
        for record, field in ((w.club_accounts["club_a"], "cash_units"),
                              (w.players["p1"], "cumulative_wages_received_units"),
                              (w.ledger, "initial_total_cash_units"),
                              (le.TransferContractRecord("tr1", 28, "p3", "club_a", "club_a", "club_b",
                                  9500000, 2500000, 1250000, 6), "dividend_per_start_units")):
            for invalid in (True, 0.0, "0", -1):
                data = record.to_dict()
                data[field] = invalid
                with self.assertRaises(ValueError):
                    type(record).from_dict(data)
                bad = deepcopy(record)
                setattr(bad, field, invalid)
                with self.assertRaises(ValueError):
                    bad.to_dict()
                with self.assertRaises(ValueError):
                    replace(record, **{field: invalid})
