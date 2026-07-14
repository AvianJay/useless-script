"""Transactional casino service shared by Explore and Discord surfaces."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Optional

import casino_rules as rules
import Economy
from globalenv import db


ROUND_TIMEOUT = timedelta(seconds=120)
STATEFUL_GAMES = frozenset({"tower", "highlow", "blackjack"})
INSTANT_GAMES = frozenset({"roulette", "dice", "coinflip", "scratchcard", "slots"})


class CasinoError(Exception):
    def __init__(self, message: str, status_code: int = 400, payload: Optional[dict] = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.payload = payload or {}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _loads(value: Any, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def init_casino_tables(connection=None) -> None:
    owns_connection = connection is None
    conn = connection or db.get_connection()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS explore_casino_rounds (
                round_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                game TEXT NOT NULL,
                bet REAL NOT NULL,
                status TEXT NOT NULL,
                state_json TEXT NOT NULL,
                result_json TEXT,
                payout REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                expires_at TEXT,
                settled_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_explore_casino_rounds_user_status
            ON explore_casino_rounds(user_id, status, game);

            CREATE TABLE IF NOT EXISTS explore_casino_requests (
                request_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                endpoint TEXT NOT NULL,
                round_id TEXT,
                response_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_explore_casino_requests_created
            ON explore_casino_requests(created_at);
            """
        )
        if owns_connection:
            conn.commit()
    finally:
        if owns_connection:
            conn.close()


class CasinoService:
    def __init__(self, *, rng=None, db_path: Optional[str] = None):
        self.rng = rng
        self.db_path = db_path or db.db_path

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _connection(self):
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _request_id(value: Any) -> str:
        request_id = str(value or "").strip()
        if not request_id or len(request_id) > 128:
            raise CasinoError("缺少有效的 request_id。")
        return request_id

    @staticmethod
    def _existing_request(conn, request_id: str, user_id: int) -> Optional[dict]:
        row = conn.execute(
            "SELECT user_id, response_json FROM explore_casino_requests WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        if not row:
            return None
        if int(row["user_id"]) != int(user_id):
            raise CasinoError("request_id 已被其他玩家使用。", 409)
        return _loads(row["response_json"], {})

    @staticmethod
    def _store_request(conn, request_id: str, user_id: int, endpoint: str, response: dict, round_id=None):
        conn.execute(
            """
            INSERT INTO explore_casino_requests
                (request_id, user_id, endpoint, round_id, response_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (request_id, int(user_id), endpoint, round_id, _dumps(response), _iso(utcnow())),
        )

    @staticmethod
    def _balance(conn, user_id: int) -> float:
        row = conn.execute(
            """
            SELECT data_value FROM user_data
            WHERE user_id = ? AND guild_id = ? AND data_key = 'economy_balance'
            """,
            (int(user_id), Economy.GLOBAL_GUILD_ID),
        ).fetchone()
        try:
            return float(row[0]) if row else 0.0
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _log_events(events: Iterable[tuple[int, str, float, str]]) -> None:
        for user_id, tx_type, amount, detail in events:
            try:
                Economy.log_transaction(
                    Economy.GLOBAL_GUILD_ID,
                    user_id,
                    tx_type,
                    amount,
                    Economy.GLOBAL_CURRENCY_NAME,
                    detail,
                )
            except Exception:
                pass

    @staticmethod
    def _lottery_state_from_conn(conn, guild_id: int = Economy.GLOBAL_GUILD_ID) -> dict:
        row = conn.execute(
            "SELECT config_value FROM server_configs WHERE guild_id = ? AND config_key = ?",
            (guild_id, rules.LOTTERY_CONFIG_KEY),
        ).fetchone()
        return rules.normalize_lottery_state(_loads(row[0], {}) if row else {})

    @staticmethod
    def _save_lottery_state(conn, state: dict, guild_id: int = Economy.GLOBAL_GUILD_ID) -> None:
        conn.execute(
            """
            INSERT INTO server_configs (guild_id, config_key, config_value)
            VALUES (?, ?, ?)
            ON CONFLICT(guild_id, config_key)
            DO UPDATE SET config_value = excluded.config_value
            """,
            (guild_id, rules.LOTTERY_CONFIG_KEY, _dumps(state)),
        )

    @staticmethod
    def lottery_public(state: dict, user_id: int) -> dict:
        user_key = str(int(user_id))
        my_tickets = {
            number: float(entries[user_key])
            for number, entries in state.get("tickets", {}).items()
            if user_key in entries
        }
        return {
            "jackpot": float(state.get("jackpot", 0.0)),
            "draw_at": state.get("draw_at"),
            "round_id": state.get("round_id"),
            "my_tickets": my_tickets,
            "last_result": state.get("last_result"),
            "settling": bool(state.get("pending_settlement")),
        }

    def get_lottery_state(self, guild_id: int = Economy.GLOBAL_GUILD_ID) -> dict:
        with self._connection() as conn:
            return self._lottery_state_from_conn(conn, guild_id)

    def save_lottery_state(self, guild_id: int, state: dict) -> bool:
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._save_lottery_state(conn, rules.normalize_lottery_state(state), guild_id)
            conn.commit()
        return True

    def play(self, user_id: int, payload: dict) -> dict:
        request_id = self._request_id(payload.get("request_id"))
        game = str(payload.get("game") or "").strip().lower()
        if game not in INSTANT_GAMES:
            raise CasinoError("無效的即時遊戲。")
        try:
            bet = rules.validate_bet(payload.get("bet"))
            outcome = self._instant_outcome(game, payload)
        except ValueError as exc:
            raise CasinoError(str(exc)) from exc

        logs = []
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = self._existing_request(conn, request_id, user_id)
            if existing is not None:
                conn.rollback()
                return existing
            success, before, after_bet = Economy.mutate_balance_atomic(
                Economy.GLOBAL_GUILD_ID,
                user_id,
                -bet,
                connection=conn,
            )
            if not success:
                raise CasinoError("全域幣餘額不足。", 400, {"balance": before})
            multiplier = float(outcome.get("multiplier", 0.0))
            payout = round(bet * multiplier, 2) if outcome.get("won") else 0.0
            balance = after_bet
            if payout > 0:
                _, _, balance = Economy.mutate_balance_atomic(
                    Economy.GLOBAL_GUILD_ID,
                    user_id,
                    payout,
                    connection=conn,
                )
            response = {
                "success": True,
                "game": game,
                "bet": bet,
                "payout": payout,
                "profit": round(payout - bet, 2),
                "balance": balance,
                "currency_name": Economy.GLOBAL_CURRENCY_NAME,
                "result": outcome,
            }
            self._store_request(conn, request_id, user_id, "play", response)
            conn.commit()
            logs.append((user_id, f"Explore {game} 下注", -bet, f"Explore 賭場 {game} 下注"))
            if payout > 0:
                logs.append((user_id, f"Explore {game} 派彩", payout, f"Explore 賭場 {game} 派彩"))
        self._log_events(logs)
        return response

    def _instant_outcome(self, game: str, payload: dict) -> dict:
        if game == "roulette":
            result = rules.play_roulette(
                str(payload.get("bet_type") or ""),
                payload.get("number"),
                self.rng,
            )
            result["bet_type"] = str(payload.get("bet_type"))
            result["number"] = payload.get("number")
            return result
        if game == "dice":
            return rules.play_dice(payload.get("guess"), self.rng)
        if game == "coinflip":
            return rules.play_coinflip(str(payload.get("side") or ""), self.rng)
        if game == "scratchcard":
            result = rules.play_scratchcard(self.rng)
            result["won"] = result["multiplier"] > 0
            return result
        if game == "slots":
            return rules.play_slots(self.rng)
        raise ValueError("無效的遊戲。")

    def buy_lottery(self, user_id: int, payload: dict, *, source: str = "Explore") -> dict:
        request_id = self._request_id(payload.get("request_id"))
        try:
            bet = rules.validate_bet(payload.get("bet"))
            number = int(payload.get("number"))
        except (TypeError, ValueError) as exc:
            raise CasinoError("彩票號碼必須介於 00～99。") from exc
        if not 0 <= number <= 99:
            raise CasinoError("彩票號碼必須介於 00～99。")

        logs = []
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = self._existing_request(conn, request_id, user_id)
            if existing is not None:
                conn.rollback()
                return existing
            state = self._lottery_state_from_conn(conn)
            draw_at = rules.parse_lottery_draw_at(state.get("draw_at"))
            now = utcnow()
            if state.get("pending_settlement") or (draw_at is not None and draw_at <= now):
                raise CasinoError("彩票正在開獎，請稍後再試。", 409)
            success, before, balance = Economy.mutate_balance_atomic(
                Economy.GLOBAL_GUILD_ID,
                user_id,
                -bet,
                connection=conn,
            )
            if not success:
                raise CasinoError("全域幣餘額不足。", 400, {"balance": before})
            if draw_at is None or not state.get("round_id"):
                draw_at = now + rules.LOTTERY_DRAW_DELAY
                state["draw_at"] = _iso(draw_at)
                state["round_id"] = uuid.uuid4().hex
            number_key = f"{number:02d}"
            entries = state.setdefault("tickets", {}).setdefault(number_key, {})
            user_key = str(int(user_id))
            entries[user_key] = round(float(entries.get(user_key, 0.0)) + bet, 2)
            state["jackpot"] = round(float(state.get("jackpot", 0.0)) + bet, 2)
            self._save_lottery_state(conn, state)
            response = {
                "success": True,
                "game": "lottery",
                "bet": bet,
                "number": number_key,
                "result": {"number": number_key},
                "payout": 0.0,
                "balance": balance,
                "currency_name": Economy.GLOBAL_CURRENCY_NAME,
                "lottery": self.lottery_public(state, user_id),
            }
            self._store_request(conn, request_id, user_id, "lottery", response, state.get("round_id"))
            conn.commit()
            logs.append((user_id, f"{source} 彩票下注", -bet, f"購買 {number_key} 號彩票"))
        self._log_events(logs)
        return response

    def prepare_lottery_settlement(
        self,
        guild_id: int,
        winning_number: int,
        *,
        drawn_at: Optional[datetime] = None,
    ) -> tuple[dict, bool]:
        """Atomically freeze a due lottery round and create its payout plan."""
        winning_number = int(winning_number)
        if not 0 <= winning_number <= 99:
            raise ValueError("winning_number must be between 0 and 99")
        drawn_at = drawn_at or utcnow()
        if drawn_at.tzinfo is None:
            drawn_at = drawn_at.replace(tzinfo=timezone.utc)
        drawn_at = drawn_at.astimezone(timezone.utc)

        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            state = self._lottery_state_from_conn(conn, guild_id)
            if state.get("pending_settlement"):
                conn.rollback()
                return state, False
            draw_at = rules.parse_lottery_draw_at(state.get("draw_at"))
            if draw_at is None or draw_at > drawn_at:
                conn.rollback()
                return state, False

            tickets = state.get("tickets", {}) or {}
            round_id = str(state.get("round_id") or uuid.uuid4().hex)
            jackpot = round(float(state.get("jackpot", 0.0) or 0.0), 2)
            number_key = f"{winning_number:02d}"
            winning_entries = tickets.get(number_key, {}) if isinstance(tickets, dict) else {}
            winning_stakes = {
                int(user_id): float(stake)
                for user_id, stake in winning_entries.items()
                if float(stake) > 0
            }

            if not winning_stakes:
                state.update({
                    "draw_at": None,
                    "round_id": None,
                    "tickets": {},
                    "pending_settlement": None,
                    "last_result": {
                        "round_id": round_id,
                        "number": number_key,
                        "jackpot": jackpot,
                        "payout_total": 0.0,
                        "winner_count": 0,
                        "rolled_over": True,
                        "drawn_at": _iso(drawn_at),
                    },
                })
            else:
                payouts = rules.allocate_lottery_payouts(jackpot, winning_stakes)
                payout_total = round(sum(payouts.values()), 2)
                pending = {
                    "round_id": round_id,
                    "number": number_key,
                    "payouts": {str(user_id): payout for user_id, payout in payouts.items()},
                    "paid_user_ids": [],
                    "last_result": {
                        "round_id": round_id,
                        "number": number_key,
                        "jackpot": jackpot,
                        "payout_total": payout_total,
                        "house_cut": round(jackpot - payout_total, 2),
                        "winner_count": len(payouts),
                        "rolled_over": False,
                        "drawn_at": _iso(drawn_at),
                    },
                }
                state.update({
                    "jackpot": 0.0,
                    "draw_at": None,
                    "round_id": None,
                    "tickets": {},
                    "pending_settlement": pending,
                })

            self._save_lottery_state(conn, state, guild_id)
            conn.commit()
            return state, True

    def start_round(self, user_id: int, payload: dict) -> dict:
        request_id = self._request_id(payload.get("request_id"))
        game = str(payload.get("game") or "").strip().lower()
        if game not in STATEFUL_GAMES:
            raise CasinoError("無效的回合遊戲。")
        try:
            bet = rules.validate_bet(payload.get("bet"))
        except ValueError as exc:
            raise CasinoError(str(exc)) from exc

        logs = []
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = self._existing_request(conn, request_id, user_id)
            if existing is not None:
                conn.rollback()
                return existing
            logs.extend(self._expire_user_rounds(conn, user_id))
            active = conn.execute(
                """
                SELECT * FROM explore_casino_rounds
                WHERE user_id = ? AND game = ? AND status = 'active'
                ORDER BY created_at DESC LIMIT 1
                """,
                (int(user_id), game),
            ).fetchone()
            if active:
                response = self._response_from_row(active, self._balance(conn, user_id))
                raise CasinoError("你已有尚未結束的同類遊戲。", 409, response)
            success, before, balance = Economy.mutate_balance_atomic(
                Economy.GLOBAL_GUILD_ID,
                user_id,
                -bet,
                connection=conn,
            )
            if not success:
                raise CasinoError("全域幣餘額不足。", 400, {"balance": before})
            now = utcnow()
            round_id = uuid.uuid4().hex
            state = self._new_round_state(game, bet)
            expires_at = now + ROUND_TIMEOUT
            conn.execute(
                """
                INSERT INTO explore_casino_rounds
                    (round_id, user_id, game, bet, status, state_json, created_at, updated_at, expires_at)
                VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?)
                """,
                (round_id, int(user_id), game, bet, _dumps(state), _iso(now), _iso(now), _iso(expires_at)),
            )
            result = None
            payout = 0.0
            status = "active"
            if game == "blackjack" and (
                rules.bj_is_blackjack(state["player_hand"]) or rules.bj_is_blackjack(state["dealer_hand"])
            ):
                result = self._blackjack_result(state, bet)
                payout = float(result["payout"])
                balance = self._settle_round(conn, round_id, user_id, state, result, payout, "settled")
                status = "settled"
            response = self._round_response(
                round_id, game, bet, status, state, payout, balance, expires_at, result
            )
            self._store_request(conn, request_id, user_id, "round_start", response, round_id)
            conn.commit()
            logs.append((user_id, f"Explore {game} 下注", -bet, f"Explore 賭場 {game} 開始"))
            if payout > 0:
                logs.append((user_id, f"Explore {game} 派彩", payout, f"Explore 賭場 {game} 結算"))
        self._log_events(logs)
        return response

    def _new_round_state(self, game: str, bet: float) -> dict:
        source = self.rng or __import__("random")
        if game == "tower":
            return {"grid": rules.create_tower_grid(self.rng), "current_level": 1, "safe_level": 0, "picked": {}}
        if game == "highlow":
            return {
                "current_rank": source.randint(1, 13),
                "current_suit": source.choice(rules.BJ_SUITS),
                "streak": 0,
                "pot": bet,
            }
        if game == "blackjack":
            deck = rules.bj_new_deck(self.rng)
            return {
                "deck": deck,
                "player_hand": [deck.pop(), deck.pop()],
                "dealer_hand": [deck.pop(), deck.pop()],
                "doubled": False,
                "total_bet": bet,
            }
        raise CasinoError("無效的回合遊戲。")

    def act_round(self, user_id: int, round_id: str, payload: dict) -> dict:
        request_id = self._request_id(payload.get("request_id"))
        round_id = str(round_id or "").strip()
        logs = []
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = self._existing_request(conn, request_id, user_id)
            if existing is not None:
                conn.rollback()
                return existing
            logs.extend(self._expire_user_rounds(conn, user_id))
            row = conn.execute(
                "SELECT * FROM explore_casino_rounds WHERE round_id = ?",
                (round_id,),
            ).fetchone()
            if not row or int(row["user_id"]) != int(user_id):
                raise CasinoError("找不到這個遊戲回合。", 404)
            if row["status"] != "active":
                response = self._response_from_row(row, self._balance(conn, user_id))
                self._store_request(conn, request_id, user_id, "round_action", response, round_id)
                conn.commit()
                self._log_events(logs)
                return response
            state = _loads(row["state_json"], {})
            game = row["game"]
            bet = float(row["bet"])
            action = str(payload.get("action") or "").strip().lower()
            result, payout, settled, extra_bet = self._apply_action(
                conn, user_id, game, bet, state, action, payload
            )
            total_bet = round(bet + extra_bet, 2)
            if extra_bet > 0:
                conn.execute(
                    "UPDATE explore_casino_rounds SET bet = ? WHERE round_id = ? AND status = 'active'",
                    (total_bet, round_id),
                )
            now = utcnow()
            expires_at = now + ROUND_TIMEOUT
            balance = self._balance(conn, user_id)
            status = "active"
            if settled:
                balance = self._settle_round(conn, round_id, user_id, state, result, payout, "settled")
                status = "settled"
            else:
                conn.execute(
                    """
                    UPDATE explore_casino_rounds
                    SET state_json = ?, updated_at = ?, expires_at = ?
                    WHERE round_id = ? AND status = 'active'
                    """,
                    (_dumps(state), _iso(now), _iso(expires_at), round_id),
                )
            response = self._round_response(
                round_id, game, total_bet, status, state, payout, balance,
                None if settled else expires_at, result
            )
            self._store_request(conn, request_id, user_id, "round_action", response, round_id)
            conn.commit()
            if extra_bet > 0:
                logs.append((user_id, "Explore blackjack 加倍", -extra_bet, "Explore 21 點加倍"))
            if settled and payout > 0:
                logs.append((user_id, f"Explore {game} 派彩", payout, f"Explore 賭場 {game} 結算"))
        self._log_events(logs)
        return response

    def _apply_action(self, conn, user_id, game, bet, state, action, payload):
        if game == "tower":
            if action == "cashout":
                safe_level = int(state.get("safe_level", 0))
                payout = round(bet * rules.TOWER_MULTIPLIERS[safe_level], 2)
                return {"outcome": "cashout", "safe_level": safe_level, "payout": payout}, payout, True, 0.0
            if action != "pick":
                raise CasinoError("爬塔只能選格或提現。")
            try:
                tile = int(payload.get("tile"))
            except (TypeError, ValueError) as exc:
                raise CasinoError("格子必須介於 0～2。") from exc
            level = int(state.get("current_level", 1))
            if not 0 <= tile < rules.TOWER_TILES_PER_LEVEL or not 1 <= level <= rules.TOWER_LEVELS:
                raise CasinoError("無效的爬塔格子。")
            hit_cactus = bool(state["grid"][level - 1][tile])
            state.setdefault("picked", {})[str(level)] = tile
            if hit_cactus:
                return {"outcome": "cactus", "level": level, "tile": tile}, 0.0, True, 0.0
            state["safe_level"] = level
            if level >= rules.TOWER_LEVELS:
                payout = round(bet * rules.TOWER_MULTIPLIERS[level], 2)
                return {"outcome": "top", "level": level, "tile": tile, "payout": payout}, payout, True, 0.0
            state["current_level"] = level + 1
            return {"outcome": "safe", "level": level, "tile": tile}, 0.0, False, 0.0

        if game == "highlow":
            if action == "cashout":
                if int(state.get("streak", 0)) < 1:
                    raise CasinoError("至少猜中一次後才能提現。")
                payout = round(float(state["pot"]), 2)
                return {"outcome": "cashout", "streak": state["streak"], "payout": payout}, payout, True, 0.0
            if action != "guess":
                raise CasinoError("比大小只能猜牌或提現。")
            guess = str(payload.get("guess") or "").lower()
            current_rank = int(state["current_rank"])
            p_high, p_low = rules.hl_probs(current_rank)
            if guess not in ("high", "low") or (guess == "high" and p_high <= 0) or (guess == "low" and p_low <= 0):
                raise CasinoError("目前牌面不能選擇這個方向。")
            next_rank = rules.hl_draw_next(current_rank, self.rng)
            source = self.rng or __import__("random")
            next_suit = source.choice(rules.BJ_SUITS)
            won = next_rank > current_rank if guess == "high" else next_rank < current_rank
            previous = {"rank": current_rank, "suit": state["current_suit"]}
            state["current_rank"] = next_rank
            state["current_suit"] = next_suit
            if not won:
                return {
                    "outcome": "lose", "guess": guess, "previous": previous,
                    "next": {"rank": next_rank, "suit": next_suit},
                }, 0.0, True, 0.0
            state["streak"] = int(state.get("streak", 0)) + 1
            state["pot"], capped = rules.hl_apply_win(float(state["pot"]), bet, current_rank, guess)
            result = {
                "outcome": "win", "guess": guess, "previous": previous,
                "next": {"rank": next_rank, "suit": next_suit}, "pot": state["pot"],
            }
            if capped:
                result["outcome"] = "cap_cashout"
                result["payout"] = state["pot"]
                return result, float(state["pot"]), True, 0.0
            return result, 0.0, False, 0.0

        if game == "blackjack":
            if action == "hit":
                state["player_hand"].append(state["deck"].pop())
                total, _ = rules.bj_hand_value(state["player_hand"])
                if total >= 21:
                    result = self._blackjack_result(state, bet)
                    return result, float(result["payout"]), True, 0.0
                return {"outcome": "hit", "player_total": total}, 0.0, False, 0.0
            if action == "stand":
                result = self._blackjack_result(state, bet)
                return result, float(result["payout"]), True, 0.0
            if action == "double":
                if len(state["player_hand"]) != 2 or state.get("doubled"):
                    raise CasinoError("目前不能加倍。")
                success, before, _ = Economy.mutate_balance_atomic(
                    Economy.GLOBAL_GUILD_ID, user_id, -bet, connection=conn
                )
                if not success:
                    raise CasinoError("全域幣餘額不足，無法加倍。", 400, {"balance": before})
                state["doubled"] = True
                state["player_hand"].append(state["deck"].pop())
                doubled_bet = round(bet * 2, 2)
                result = self._blackjack_result(state, doubled_bet)
                state["total_bet"] = doubled_bet
                return result, float(result["payout"]), True, bet
            raise CasinoError("21 點只能要牌、停牌或加倍。")
        raise CasinoError("無效的遊戲。")

    @staticmethod
    def _blackjack_result(state: dict, bet: Optional[float] = None) -> dict:
        wager = float(bet if bet is not None else state.get("total_bet") or 0.0)
        if wager <= 0:
            wager = float(state.get("bet", 0.0))
        result = rules.bj_settle(state["player_hand"], state["dealer_hand"], state["deck"], wager)
        return result

    def _settle_round(self, conn, round_id, user_id, state, result, payout, status):
        now = utcnow()
        cursor = conn.execute(
            """
            UPDATE explore_casino_rounds
            SET status = ?, state_json = ?, result_json = ?, payout = ?,
                updated_at = ?, settled_at = ?, expires_at = NULL
            WHERE round_id = ? AND status = 'active'
            """,
            (status, _dumps(state), _dumps(result), payout, _iso(now), _iso(now), round_id),
        )
        if cursor.rowcount != 1:
            raise CasinoError("牌局已經結算。", 409)
        balance = self._balance(conn, user_id)
        if payout > 0:
            _, _, balance = Economy.mutate_balance_atomic(
                Economy.GLOBAL_GUILD_ID, user_id, payout, connection=conn
            )
        return balance

    def _expire_user_rounds(self, conn, user_id: int):
        now = utcnow()
        rows = conn.execute(
            """
            SELECT * FROM explore_casino_rounds
            WHERE user_id = ? AND status = 'active' AND expires_at <= ?
            """,
            (int(user_id), _iso(now)),
        ).fetchall()
        logs = []
        for row in rows:
            state = _loads(row["state_json"], {})
            game = row["game"]
            bet = float(row["bet"])
            if game == "tower":
                result, payout = {"outcome": "timeout_forfeit"}, 0.0
            elif game == "highlow":
                payout = round(float(state.get("pot", bet)), 2)
                result = {
                    "outcome": "timeout_refund" if int(state.get("streak", 0)) < 1 else "timeout_cashout",
                    "payout": payout,
                }
            else:
                result = self._blackjack_result(state, bet)
                result["timeout"] = True
                payout = float(result["payout"])
            self._settle_round(conn, row["round_id"], user_id, state, result, payout, "expired")
            if payout > 0:
                logs.append((user_id, f"Explore {game} 逾時結算", payout, "Explore 賭場逾時自動結算"))
        return logs

    def get_state(self, user_id: int) -> dict:
        logs = []
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            logs.extend(self._expire_user_rounds(conn, user_id))
            rows = conn.execute(
                """
                SELECT * FROM explore_casino_rounds
                WHERE user_id = ? AND status = 'active'
                ORDER BY created_at
                """,
                (int(user_id),),
            ).fetchall()
            lottery = self._lottery_state_from_conn(conn)
            response = {
                "success": True,
                "balance": self._balance(conn, user_id),
                "currency_name": Economy.GLOBAL_CURRENCY_NAME,
                "result": None,
                "payout": 0.0,
                "active_rounds": [self._public_round(row, _loads(row["state_json"], {})) for row in rows],
                "lottery": self.lottery_public(lottery, user_id),
                "bet_min": rules.BET_MIN,
                "bet_max": rules.BET_MAX,
            }
            conn.commit()
        self._log_events(logs)
        return response

    def _response_from_row(self, row, balance: float) -> dict:
        state = _loads(row["state_json"], {})
        result = _loads(row["result_json"], None)
        return self._round_response(
            row["round_id"], row["game"], float(row["bet"]), row["status"], state,
            float(row["payout"]), balance, row["expires_at"], result,
        )

    def _round_response(self, round_id, game, bet, status, state, payout, balance, expires_at, result):
        public = self._public_round_values(round_id, game, bet, status, state, payout, expires_at, result)
        return {
            "success": True,
            "game": game,
            "bet": bet,
            "payout": payout,
            "balance": balance,
            "currency_name": Economy.GLOBAL_CURRENCY_NAME,
            "result": result,
            "round": public,
        }

    def _public_round(self, row, state):
        return self._public_round_values(
            row["round_id"], row["game"], float(row["bet"]), row["status"], state,
            float(row["payout"]), row["expires_at"], _loads(row["result_json"], None),
        )

    @staticmethod
    def _public_round_values(round_id, game, bet, status, state, payout, expires_at, result):
        if isinstance(expires_at, datetime):
            expires_at = _iso(expires_at)
        public = {
            "round_id": round_id,
            "game": game,
            "bet": bet,
            "status": status,
            "payout": payout,
            "expires_at": expires_at,
            "result": result,
        }
        if game == "tower":
            public.update({
                "current_level": int(state.get("current_level", 1)),
                "safe_level": int(state.get("safe_level", 0)),
                "picked": state.get("picked", {}),
                "multipliers": rules.TOWER_MULTIPLIERS,
            })
        elif game == "highlow":
            rank = int(state.get("current_rank", 1))
            p_high, p_low = rules.hl_probs(rank)
            public.update({
                "card": {"rank": rank, "rank_name": rules.hl_rank_name(rank), "suit": state.get("current_suit")},
                "streak": int(state.get("streak", 0)),
                "pot": float(state.get("pot", bet)),
                "probabilities": {"high": p_high, "low": p_low},
            })
        elif game == "blackjack":
            player_total, player_soft = rules.bj_hand_value(state.get("player_hand", []))
            dealer_hand = state.get("dealer_hand", [])
            reveal = status != "active"
            public.update({
                "player_hand": state.get("player_hand", []),
                "player_total": player_total,
                "player_soft": player_soft,
                "dealer_hand": dealer_hand if reveal else dealer_hand[:1],
                "dealer_hidden": 0 if reveal else max(0, len(dealer_hand) - 1),
                "dealer_total": rules.bj_hand_value(dealer_hand)[0] if reveal else None,
                "can_double": status == "active" and len(state.get("player_hand", [])) == 2 and not state.get("doubled"),
                "doubled": bool(state.get("doubled")),
            })
        return public


init_casino_tables()
default_casino_service = CasinoService()
