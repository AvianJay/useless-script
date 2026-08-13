import json
import sqlite3
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock


DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))

import economy_integrity as economy


class EconomyIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tempdir.name) / "economy.db")
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.executescript(
                """
                CREATE TABLE user_data (
                    user_id INTEGER NOT NULL,
                    guild_id INTEGER NOT NULL,
                    data_key TEXT NOT NULL,
                    data_value TEXT NOT NULL,
                    UNIQUE(user_id, guild_id, data_key)
                );
                CREATE TABLE server_configs (
                    guild_id INTEGER NOT NULL,
                    config_key TEXT NOT NULL,
                    config_value TEXT NOT NULL,
                    UNIQUE(guild_id, config_key)
                );
                """
            )
        self.guild_id = 123
        self.set_config("economy_exchange_rate", 1.0)
        self.set_config("economy_total_supply", 1000.0)
        self.set_balance(self.guild_id, 1, 1000.0)

    def tearDown(self):
        self.tempdir.cleanup()

    def set_config(self, key, value):
        raw = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO server_configs VALUES (?, ?, ?)",
                (self.guild_id, key, raw),
            )
            conn.commit()

    def get_config(self, key):
        with closing(sqlite3.connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT config_value FROM server_configs WHERE guild_id = ? AND config_key = ?",
                (self.guild_id, key),
            ).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            return row[0]

    def set_balance(self, guild_id, user_id, value):
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO user_data VALUES (?, ?, 'economy_balance', ?)",
                (user_id, guild_id, str(value)),
            )
            conn.commit()

    def balance(self, guild_id, user_id):
        with closing(sqlite3.connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT data_value FROM user_data WHERE user_id = ? AND guild_id = ? AND data_key = 'economy_balance'",
                (user_id, guild_id),
            ).fetchone()
        return float(row[0]) if row else 0.0

    def inventory(self, guild_id, user_id):
        with closing(sqlite3.connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT data_value FROM user_data WHERE user_id = ? AND guild_id = ? AND data_key = 'items'",
                (user_id, guild_id),
            ).fetchone()
        return json.loads(row[0]) if row else {}

    def set_inventory(self, guild_id, user_id, value, key="items"):
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO user_data VALUES (?, ?, ?, ?)",
                (user_id, guild_id, key, json.dumps(value)),
            )
            conn.commit()

    def test_money_validation_rejects_nan_infinity_and_subcent(self):
        for value in (float("nan"), float("inf"), float("-inf"), 0.001, -1):
            with self.subTest(value=value), self.assertRaises(economy.EconomyIntegrityError):
                economy.money(value)
        self.assertEqual(economy.money(0.009), 0.01)
        with self.assertRaises(economy.EconomyIntegrityError):
            economy.positive_item_quantity(1.5)

    def test_exchange_rejects_unknown_direction_without_changes(self):
        with self.assertRaises(economy.EconomyIntegrityError):
            economy.settle_exchange(self.db_path, self.guild_id, 1, 10, "sideways", 5)
        self.assertEqual(self.balance(self.guild_id, 1), 1000.0)
        self.assertEqual(self.balance(0, 1), 0.0)

    def test_exchange_updates_both_balances_and_supply_once(self):
        result = economy.settle_exchange(
            self.db_path, self.guild_id, 1, 100, "to_global", 5, market_weight=0.003,
        )
        self.assertEqual(result.received, 95.0)
        self.assertEqual(self.balance(self.guild_id, 1), 900.0)
        self.assertEqual(self.balance(0, 1), 95.0)
        self.assertEqual(float(self.get_config("economy_total_supply")), 900.0)
        self.assertGreater(float(self.get_config("economy_exchange_rate")), 1.0)
        self.assertEqual(int(self.get_config("economy_transaction_count")), 1)

    def test_parallel_exchange_cannot_double_spend(self):
        self.set_balance(self.guild_id, 1, 100.0)
        self.set_config("economy_total_supply", 100.0)

        def exchange(_):
            try:
                economy.settle_exchange(
                    self.db_path, self.guild_id, 1, 100, "to_global", 5,
                )
                return True
            except economy.EconomyInsufficientFunds:
                return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(exchange, range(2)))
        self.assertEqual(results.count(True), 1)
        self.assertEqual(self.balance(self.guild_id, 1), 0.0)
        self.assertEqual(self.balance(0, 1), 95.0)

    def test_parallel_transfer_cannot_double_spend(self):
        self.set_balance(self.guild_id, 1, 100.0)
        self.set_balance(self.guild_id, 2, 0.0)
        self.set_config("economy_total_supply", 100.0)

        def pay(_):
            try:
                economy.settle_transfer(self.db_path, self.guild_id, 1, 2, 100, 0)
                return True
            except economy.EconomyInsufficientFunds:
                return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(pay, range(2)))
        self.assertEqual(results.count(True), 1)
        self.assertEqual(self.balance(self.guild_id, 1), 0.0)
        self.assertEqual(self.balance(self.guild_id, 2), 100.0)
        self.assertEqual(float(self.get_config("economy_total_supply")), 100.0)

    def test_risky_rate_blacklists_before_credit(self):
        self.set_config("economy_exchange_rate", 10.0)
        with self.assertRaises(economy.EconomyRiskError) as raised:
            economy.settle_exchange(
                self.db_path, self.guild_id, 1, 100, "to_global", 5,
            )
        self.assertTrue(raised.exception.blacklist_created)
        self.assertEqual(self.balance(self.guild_id, 1), 1000.0)
        self.assertEqual(self.balance(0, 1), 0.0)
        info = self.get_config(economy.FLOW_BLACKLIST_KEY)
        self.assertEqual(info["source"], "automatic")
        self.assertEqual(info["trigger"], "exchange_rate_at_or_above_risk_threshold")

    def test_automatic_risk_never_overwrites_manual_blacklist(self):
        manual = {
            "reason": "manual review",
            "source": "manual",
            "set_by": 9,
            "set_at": "now",
        }
        self.set_config(economy.FLOW_BLACKLIST_KEY, manual)
        self.set_config("economy_exchange_rate", "NaN")
        with self.assertRaises(economy.EconomyIntegrityError):
            economy.guard_cross_domain(self.db_path, self.guild_id)
        self.assertEqual(self.get_config(economy.FLOW_BLACKLIST_KEY), manual)

    def test_automatic_risk_never_overwrites_existing_automatic_blacklist(self):
        automatic = {
            "reason": "first automatic event",
            "source": "automatic",
            "trigger": "first_trigger",
            "observed": {"value": 10},
            "set_at": "now",
        }
        self.set_config(economy.FLOW_BLACKLIST_KEY, automatic)
        with self.assertRaises(economy.EconomyIntegrityError):
            economy.guard_cross_domain(self.db_path, self.guild_id)
        self.assertEqual(self.get_config(economy.FLOW_BLACKLIST_KEY), automatic)

    def test_manual_unblacklist_restores_cross_domain_guard(self):
        self.set_config("economy_exchange_rate", 10.0)
        with self.assertRaises(economy.EconomyRiskError):
            economy.guard_cross_domain(self.db_path, self.guild_id)
        self.set_config(economy.FLOW_BLACKLIST_KEY, None)
        self.set_config("economy_exchange_rate", 0.5)
        self.assertEqual(economy.guard_cross_domain(self.db_path, self.guild_id), 0.5)

    def test_non_finite_exchange_input_auto_blacklists_before_changes(self):
        with self.assertRaises(economy.EconomyRiskError) as raised:
            economy.settle_exchange(
                self.db_path, self.guild_id, 1, float("nan"), "to_global", 5,
            )
        self.assertEqual(raised.exception.trigger, "non_finite_cross_domain_value")
        self.assertEqual(self.balance(self.guild_id, 1), 1000.0)
        self.assertEqual(self.balance(0, 1), 0.0)
        self.assertEqual(self.get_config(economy.FLOW_BLACKLIST_KEY)["source"], "automatic")

    def test_transfer_is_atomic_and_only_burns_fee(self):
        self.set_balance(self.guild_id, 2, 0.0)
        result = economy.settle_transfer(
            self.db_path, self.guild_id, 1, 2, 100, 3, market_weight=0.003,
        )
        self.assertEqual((result.sender_after, result.receiver_after), (897.0, 100.0))
        self.assertEqual(float(self.get_config("economy_total_supply")), 997.0)
        self.assertGreater(float(self.get_config("economy_exchange_rate")), 1.0)

    def test_item_trade_rejects_zero_quantity_and_settles_once(self):
        self.set_inventory(self.guild_id, 1, {"apple": 1})
        with self.assertRaises(economy.EconomyIntegrityError):
            economy.settle_trade(
                self.db_path,
                self.guild_id,
                1,
                2,
                offer_item="apple",
                offer_item_amount=0,
                offer_money=0,
                request_item=None,
                request_item_amount=1,
                request_money=0,
            )
        economy.settle_trade(
            self.db_path,
            self.guild_id,
            1,
            2,
            offer_item="apple",
            offer_item_amount=1,
            offer_money=0,
            request_item=None,
            request_item_amount=1,
            request_money=0,
        )
        self.assertEqual(self.inventory(self.guild_id, 1), {})
        self.assertEqual(self.inventory(self.guild_id, 2), {"apple": 1})
        with self.assertRaises(economy.EconomyInsufficientFunds):
            economy.settle_trade(
                self.db_path,
                self.guild_id,
                1,
                2,
                offer_item="apple",
                offer_item_amount=1,
                offer_money=0,
                request_item=None,
                request_item_amount=1,
                request_money=0,
            )

    def test_parallel_trade_moves_item_at_most_once(self):
        self.set_inventory(self.guild_id, 1, {"apple": 1})

        def trade(_):
            try:
                economy.settle_trade(
                    self.db_path,
                    self.guild_id,
                    1,
                    2,
                    offer_item="apple",
                    offer_item_amount=1,
                    offer_money=0,
                    request_item=None,
                    request_item_amount=1,
                    request_money=0,
                )
                return True
            except economy.EconomyInsufficientFunds:
                return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(trade, range(2)))
        self.assertEqual(results.count(True), 1)
        self.assertEqual(self.inventory(self.guild_id, 1), {})
        self.assertEqual(self.inventory(self.guild_id, 2), {"apple": 1})
        self.assertEqual(int(self.get_config("economy_transaction_count")), 1)

    def test_global_store_only_uses_global_inventory(self):
        self.set_balance(0, 1, 100.0)
        self.set_inventory(self.guild_id, 1, {"apple": 2})
        economy.buy_item(self.db_path, 0, 1, "apple", 1, 10)
        self.assertEqual(self.inventory(self.guild_id, 1), {"apple": 2})
        self.assertEqual(self.inventory(0, 1), {"apple": 1})
        self.assertEqual(self.balance(0, 1), 90.0)

    def test_global_store_rechecks_source_guild_risk_before_credit(self):
        self.set_balance(0, 1, 100.0)
        rate = economy.guard_cross_domain(self.db_path, self.guild_id)
        self.set_config("economy_exchange_rate", 10.0)
        with self.assertRaises(economy.EconomyRiskError):
            economy.buy_item(
                self.db_path,
                0,
                1,
                "apple",
                1,
                10,
                expected_rate=rate,
                risk_guild_id=self.guild_id,
            )
        self.assertEqual(self.balance(0, 1), 100.0)
        self.assertEqual(self.inventory(0, 1), {})
        self.assertEqual(self.get_config(economy.FLOW_BLACKLIST_KEY)["source"], "automatic")

    def test_server_shop_records_one_amount_relative_rate_change(self):
        before_rate = float(self.get_config("economy_exchange_rate"))
        economy.buy_item(
            self.db_path,
            self.guild_id,
            1,
            "apple",
            1,
            100,
            market_weight=0.005,
        )
        after_buy = float(self.get_config("economy_exchange_rate"))
        self.assertGreater(after_buy, before_rate)
        self.assertEqual(int(self.get_config("economy_transaction_count")), 1)
        economy.sell_item(
            self.db_path,
            self.guild_id,
            1,
            "apple",
            1,
            70,
            exclude_admin_items=True,
            market_weight=0.003,
        )
        after_sell = float(self.get_config("economy_exchange_rate"))
        self.assertLess(after_sell, after_buy)
        self.assertEqual(int(self.get_config("economy_transaction_count")), 2)

    def test_parallel_shop_purchase_and_sale_settle_at_most_once(self):
        self.set_balance(self.guild_id, 1, 10.0)
        self.set_config("economy_total_supply", 10.0)

        def buy(_):
            try:
                economy.buy_item(self.db_path, self.guild_id, 1, "apple", 1, 10)
                return True
            except economy.EconomyInsufficientFunds:
                return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            buy_results = list(pool.map(buy, range(2)))
        self.assertEqual(buy_results.count(True), 1)
        self.assertEqual(self.balance(self.guild_id, 1), 0.0)
        self.assertEqual(self.inventory(self.guild_id, 1), {"apple": 1})

        def sell(_):
            try:
                economy.sell_item(
                    self.db_path,
                    self.guild_id,
                    1,
                    "apple",
                    1,
                    7,
                    exclude_admin_items=True,
                )
                return True
            except economy.EconomyInsufficientFunds:
                return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            sell_results = list(pool.map(sell, range(2)))
        self.assertEqual(sell_results.count(True), 1)
        self.assertEqual(self.balance(self.guild_id, 1), 7.0)
        self.assertEqual(self.inventory(self.guild_id, 1), {})

    def test_cheque_risk_blocks_before_item_removal_or_payout(self):
        self.set_inventory(self.guild_id, 1, {"cheque": 1})
        self.set_config("economy_exchange_rate", 10.0)
        with self.assertRaises(economy.EconomyRiskError):
            economy.cash_cheque(
                self.db_path,
                self.guild_id,
                1,
                "cheque",
                100,
                enforce_rate_guard=True,
                observed_values={"cheque_worth": 100},
            )
        self.assertEqual(self.inventory(self.guild_id, 1), {"cheque": 1})
        self.assertEqual(self.balance(self.guild_id, 1), 1000.0)

    def test_parallel_cheque_redemption_settles_at_most_once(self):
        self.set_inventory(self.guild_id, 1, {"cheque": 1})

        def redeem(_):
            try:
                economy.cash_cheque(self.db_path, self.guild_id, 1, "cheque", 100)
                return True
            except economy.EconomyInsufficientFunds:
                return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(redeem, range(2)))
        self.assertEqual(results.count(True), 1)
        self.assertEqual(self.balance(self.guild_id, 1), 1100.0)
        self.assertEqual(self.inventory(self.guild_id, 1), {})
        self.assertEqual(float(self.get_config("economy_total_supply")), 1100.0)

    def test_invariant_failure_rolls_back_and_auto_blacklists(self):
        original_write = economy.write_user_value

        def drop_receiver_write(conn, guild_id, user_id, key, value):
            if int(user_id) == 2 and key == "economy_balance":
                return
            original_write(conn, guild_id, user_id, key, value)

        with mock.patch.object(economy, "write_user_value", side_effect=drop_receiver_write):
            with self.assertRaises(economy.EconomyRiskError) as raised:
                economy.settle_transfer(self.db_path, self.guild_id, 1, 2, 100, 0)
        self.assertEqual(raised.exception.trigger, "settlement_balance_invariant_failed")
        self.assertEqual(self.balance(self.guild_id, 1), 1000.0)
        self.assertEqual(self.balance(self.guild_id, 2), 0.0)
        self.assertEqual(self.get_config(economy.FLOW_BLACKLIST_KEY)["source"], "automatic")

    def test_record_transaction_does_not_change_exchange_rate(self):
        before = float(self.get_config("economy_exchange_rate"))
        economy.increment_transaction_atomic(self.db_path, self.guild_id)
        self.assertEqual(float(self.get_config("economy_exchange_rate")), before)
        self.assertEqual(int(self.get_config("economy_transaction_count")), 1)

    def test_risky_global_migration_blacklists_without_clearing_assets(self):
        self.set_inventory(self.guild_id, 1, {"apple": 2})
        self.set_config("economy_exchange_rate", 10.0)
        state = {
            1: {"balance": 1000.0, "items": {"apple": 2}, "admin_items": {}},
        }
        with self.assertRaises(economy.EconomyRiskError):
            economy.settle_global_migration(
                self.db_path,
                self.guild_id,
                {1: 1000.0},
                expected_rate=10.0,
                expected_server_state=state,
                global_mode_key="economy_global_mode",
            )
        self.assertEqual(self.balance(self.guild_id, 1), 1000.0)
        self.assertEqual(self.balance(0, 1), 0.0)
        self.assertEqual(self.inventory(self.guild_id, 1), {"apple": 2})
        self.assertIsNone(self.get_config("economy_global_mode"))

    def test_server_shop_risk_blocks_before_purchase(self):
        self.set_config("economy_exchange_rate", 10.0)
        with self.assertRaises(economy.EconomyRiskError):
            economy.buy_item(
                self.db_path,
                self.guild_id,
                1,
                "apple",
                1,
                10,
                market_weight=0.005,
                expected_rate=10.0,
            )
        self.assertEqual(self.balance(self.guild_id, 1), 1000.0)
        self.assertEqual(self.inventory(self.guild_id, 1), {})

    def test_daily_and_hourly_claims_are_idempotent(self):
        today = date(2026, 8, 13)

        def daily(_):
            return economy.claim_daily(self.db_path, self.guild_id, 1, today, 100, 0)

        with ThreadPoolExecutor(max_workers=2) as pool:
            daily_results = list(pool.map(daily, range(2)))
        self.assertEqual(sum(not result.already_claimed for result in daily_results), 1)
        self.assertEqual(self.balance(self.guild_id, 1), 1100.0)

        hour = datetime(2026, 8, 13, 10, tzinfo=timezone.utc)
        first = economy.claim_hourly(self.db_path, self.guild_id, 1, hour, 10, 0)
        second = economy.claim_hourly(self.db_path, self.guild_id, 1, hour, 10, 0)
        self.assertFalse(first.already_claimed)
        self.assertTrue(second.already_claimed)
        self.assertEqual(self.balance(self.guild_id, 1), 1110.0)

    def test_reward_invariant_failure_rolls_back_claim_and_blacklists(self):
        today = date(2026, 8, 13)
        original_write = economy.write_user_value

        def drop_balance_write(conn, guild_id, user_id, key, value):
            if key == "economy_balance":
                return
            original_write(conn, guild_id, user_id, key, value)

        with mock.patch.object(economy, "write_user_value", side_effect=drop_balance_write):
            with self.assertRaises(economy.EconomyRiskError):
                economy.claim_daily(
                    self.db_path,
                    self.guild_id,
                    1,
                    today,
                    100,
                    0,
                    inflation_weight=0.0005,
                )
        self.assertEqual(self.balance(self.guild_id, 1), 1000.0)
        with closing(sqlite3.connect(self.db_path)) as conn:
            last_daily = economy.read_user_value(
                conn, self.guild_id, 1, "economy_last_daily",
            )
        self.assertIsNone(last_daily)
        self.assertEqual(self.get_config(economy.FLOW_BLACKLIST_KEY)["source"], "automatic")


if __name__ == "__main__":
    unittest.main()
