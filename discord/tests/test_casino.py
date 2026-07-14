import sqlite3
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path


DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))

import casino_rules as rules
from casino_service import CasinoError, CasinoService, init_casino_tables


class FixedRng:
    def __init__(self):
        self._lock = threading.Lock()

    def randint(self, start, end):
        return start

    def choice(self, values):
        return list(values)[0]

    def choices(self, values, weights=None, k=1):
        return [list(values)[0] for _ in range(k)]

    def shuffle(self, values):
        return None


class QuietCasinoService(CasinoService):
    @staticmethod
    def _log_events(events):
        return None


class CasinoRulesTests(unittest.TestCase):
    def test_bet_limits(self):
        self.assertEqual(rules.validate_bet(50), 50)
        self.assertEqual(rules.validate_bet(2000), 2000)
        with self.assertRaises(ValueError):
            rules.validate_bet(49)
        with self.assertRaises(ValueError):
            rules.validate_bet(2001)

    def test_roulette_and_instant_multipliers(self):
        rng = FixedRng()
        roulette = rules.play_roulette("number", 0, rng)
        self.assertTrue(roulette["won"])
        self.assertEqual(roulette["multiplier"], 36.0)
        self.assertEqual(rules.play_dice(1, rng)["multiplier"], 5.7)
        self.assertEqual(rules.play_coinflip("heads", rng)["multiplier"], 1.9)

    def test_lottery_payout_rounding(self):
        payouts = rules.allocate_lottery_payouts(101, {1: 1, 2: 2})
        self.assertEqual(sum(payouts.values()), 95.95)
        self.assertGreater(payouts[2], payouts[1])

    def test_blackjack_three_to_two_and_dealer_stands_on_seventeen(self):
        player = [["A", "♠"], ["K", "♥"]]
        dealer = [["10", "♣"], ["7", "♦"]]
        result = rules.bj_settle(player, dealer, [], 100)
        self.assertEqual(result["outcome"], "blackjack")
        self.assertEqual(result["payout"], 250)
        self.assertEqual(len(dealer), 2)

    def test_remaining_shared_game_rules(self):
        rng = FixedRng()
        scratch = rules.play_scratchcard(rng)
        self.assertEqual(scratch["multiplier"], 0)
        self.assertEqual(len(scratch["grid"]), 9)
        slots = rules.play_slots(rng)
        self.assertEqual(slots["reels"], ["🍒", "🍒", "🍒"])
        self.assertEqual(slots["multiplier"], 3)
        tower = rules.create_tower_grid(rng)
        self.assertEqual(tower[0], [0, 0, 1])
        pot, capped = rules.hl_apply_win(100, 100, 7, "high")
        self.assertEqual(pot, 190)
        self.assertFalse(capped)


class CasinoServiceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tempdir.name) / "casino.db")
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
            init_casino_tables(conn)
        self.service = QuietCasinoService(rng=FixedRng(), db_path=self.db_path)
        self.set_balance(1, 1000)

    def tearDown(self):
        self.tempdir.cleanup()

    def set_balance(self, user_id, amount):
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO user_data VALUES (?, 0, 'economy_balance', ?)",
                (user_id, str(amount)),
            )
            conn.commit()

    def balance(self, user_id=1):
        with closing(sqlite3.connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT data_value FROM user_data WHERE user_id = ? AND guild_id = 0 AND data_key = 'economy_balance'",
                (user_id,),
            ).fetchone()
            return float(row[0])

    def test_idempotent_instant_play_only_charges_once(self):
        payload = {"request_id": "same", "game": "dice", "bet": 50, "guess": 2}
        first = self.service.play(1, payload)
        second = self.service.play(1, payload)
        self.assertEqual(first, second)
        self.assertEqual(self.balance(), 950)

    def test_parallel_bets_do_not_lose_updates(self):
        self.set_balance(1, 500)

        def play(index):
            return self.service.play(1, {
                "request_id": f"parallel-{index}",
                "game": "dice",
                "bet": 50,
                "guess": 2,
            })

        with ThreadPoolExecutor(max_workers=10) as pool:
            list(pool.map(play, range(10)))
        self.assertEqual(self.balance(), 0)

    def test_tower_round_resumes_and_settles_once(self):
        started = self.service.start_round(1, {"request_id": "tower-start", "game": "tower", "bet": 100})
        round_id = started["round"]["round_id"]
        safe = self.service.act_round(1, round_id, {"request_id": "tower-safe", "action": "pick", "tile": 0})
        self.assertEqual(safe["round"]["safe_level"], 1)
        state = self.service.get_state(1)
        self.assertEqual(state["active_rounds"][0]["round_id"], round_id)
        settled = self.service.act_round(1, round_id, {"request_id": "tower-cash", "action": "cashout"})
        repeated = self.service.act_round(1, round_id, {"request_id": "tower-cash", "action": "cashout"})
        self.assertEqual(settled, repeated)
        self.assertEqual(settled["payout"], 140)
        self.assertEqual(self.balance(), 1040)

        replay_with_new_request = self.service.act_round(
            1,
            round_id,
            {"request_id": "tower-after-settlement", "action": "cashout"},
        )
        self.assertEqual(replay_with_new_request["round"]["status"], "settled")
        self.assertEqual(self.balance(), 1040)

    def test_round_ownership_is_enforced(self):
        self.set_balance(2, 1000)
        started = self.service.start_round(1, {"request_id": "owner-start", "game": "tower", "bet": 100})
        round_id = started["round"]["round_id"]
        with self.assertRaises(CasinoError):
            self.service.act_round(2, round_id, {"request_id": "wrong-owner", "action": "cashout"})
        self.assertEqual(self.balance(1), 900)
        self.assertEqual(self.balance(2), 1000)

    def test_public_rounds_hide_grid_and_deck(self):
        tower = self.service.start_round(1, {"request_id": "tower-secret", "game": "tower", "bet": 50})
        self.assertNotIn("grid", tower["round"])
        blackjack = self.service.start_round(1, {"request_id": "bj-secret", "game": "blackjack", "bet": 50})
        self.assertNotIn("deck", blackjack["round"])
        self.assertEqual(blackjack["round"]["dealer_hidden"], 1)

    def test_blackjack_round_uses_shared_settlement(self):
        started = self.service.start_round(1, {"request_id": "bj-start", "game": "blackjack", "bet": 100})
        round_id = started["round"]["round_id"]
        settled = self.service.act_round(1, round_id, {"request_id": "bj-stand", "action": "stand"})
        self.assertEqual(settled["round"]["status"], "settled")
        self.assertEqual(settled["result"]["outcome"], "push")
        self.assertEqual(settled["payout"], 100)
        self.assertEqual(self.balance(), 1000)

    def test_highlow_win_and_cashout(self):
        started = self.service.start_round(1, {"request_id": "hl-play", "game": "highlow", "bet": 100})
        round_id = started["round"]["round_id"]
        won = self.service.act_round(1, round_id, {"request_id": "hl-win", "action": "guess", "guess": "high"})
        self.assertEqual(won["round"]["streak"], 1)
        settled = self.service.act_round(1, round_id, {"request_id": "hl-cash", "action": "cashout"})
        self.assertEqual(settled["round"]["status"], "settled")
        self.assertEqual(settled["payout"], 95)
        self.assertEqual(self.balance(), 995)

    def test_invalid_bet_and_insufficient_balance_do_not_mutate(self):
        with self.assertRaises(CasinoError):
            self.service.play(1, {"request_id": "bad-bet", "game": "slots", "bet": 49})
        self.assertEqual(self.balance(), 1000)
        self.set_balance(1, 10)
        with self.assertRaises(CasinoError):
            self.service.play(1, {"request_id": "poor", "game": "slots", "bet": 50})
        self.assertEqual(self.balance(), 10)

    def test_highlow_timeout_refunds_first_unplayed_round(self):
        started = self.service.start_round(1, {"request_id": "hl-start", "game": "highlow", "bet": 100})
        round_id = started["round"]["round_id"]
        expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("UPDATE explore_casino_rounds SET expires_at = ? WHERE round_id = ?", (expired, round_id))
            conn.commit()
        state = self.service.get_state(1)
        self.assertEqual(state["active_rounds"], [])
        self.assertEqual(self.balance(), 1000)

    def test_blackjack_timeout_auto_stands(self):
        started = self.service.start_round(1, {"request_id": "bj-timeout", "game": "blackjack", "bet": 100})
        round_id = started["round"]["round_id"]
        expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("UPDATE explore_casino_rounds SET expires_at = ? WHERE round_id = ?", (expired, round_id))
            conn.commit()
        state = self.service.get_state(1)
        self.assertEqual(state["active_rounds"], [])
        with closing(sqlite3.connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT status, result_json FROM explore_casino_rounds WHERE round_id = ?",
                (round_id,),
            ).fetchone()
        self.assertEqual(row[0], "expired")
        self.assertIn('"timeout":true', row[1])

    def test_lottery_uses_global_minigames_state(self):
        result = self.service.buy_lottery(1, {"request_id": "lottery", "bet": 50, "number": 7})
        self.assertEqual(result["number"], "07")
        self.assertEqual(result["result"], {"number": "07"})
        self.assertEqual(result["payout"], 0)
        self.assertEqual(result["lottery"]["my_tickets"]["07"], 50)
        with closing(sqlite3.connect(self.db_path)) as conn:
            row = conn.execute(
                "SELECT config_value FROM server_configs WHERE guild_id = 0 AND config_key = ?",
                (rules.LOTTERY_CONFIG_KEY,),
            ).fetchone()
        state = rules.normalize_lottery_state(__import__("json").loads(row[0]))
        self.assertEqual(state["tickets"]["07"]["1"], 50)
        self.assertEqual(self.balance(), 950)

    def test_state_response_uses_common_balance_result_payout_contract(self):
        state = self.service.get_state(1)
        self.assertEqual(state["balance"], 1000)
        self.assertIsNone(state["result"])
        self.assertEqual(state["payout"], 0)

    def test_global_lottery_settlement_freezes_ticket_sales(self):
        self.service.buy_lottery(1, {"request_id": "lottery-due", "bet": 100, "number": 7})
        state = self.service.get_lottery_state()
        state["draw_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        self.service.save_lottery_state(0, state)

        frozen, prepared = self.service.prepare_lottery_settlement(0, 7)
        self.assertTrue(prepared)
        self.assertEqual(frozen["pending_settlement"]["payouts"]["1"], 95)
        balance_before = self.balance()
        with self.assertRaises(CasinoError):
            self.service.buy_lottery(1, {"request_id": "lottery-too-late", "bet": 50, "number": 8})
        self.assertEqual(self.balance(), balance_before)


if __name__ == "__main__":
    unittest.main()
