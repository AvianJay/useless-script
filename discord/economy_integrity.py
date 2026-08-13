import json
import math
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any


GLOBAL_GUILD_ID = 0
MAX_GLOBAL_BALANCE = 10_000_000.0
MIN_EXCHANGE_RATE = 0.01
AUTO_BLACKLIST_RATE = 10.0
FLOW_BLACKLIST_KEY = "economy_flow_blacklist"


class EconomyIntegrityError(ValueError):
    """Invalid input or corrupt state that must not be committed."""


class EconomyInsufficientFunds(EconomyIntegrityError):
    pass


class EconomyRiskError(EconomyIntegrityError):
    def __init__(self, trigger: str, observed: dict[str, Any], *, blacklist_created: bool = False):
        self.trigger = trigger
        self.observed = observed
        self.blacklist_created = blacklist_created
        super().__init__(f"economy risk detected: {trigger}")


def finite_number(value: Any, *, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise EconomyIntegrityError(f"{field} must be numeric") from exc
    if not math.isfinite(number):
        raise EconomyIntegrityError(f"{field} must be finite")
    return number


def money(value: Any, *, field: str = "amount", allow_zero: bool = False) -> float:
    number = round(finite_number(value, field=field), 2)
    if number < 0 or (number == 0 and not allow_zero):
        qualifier = "non-negative" if allow_zero else "at least 0.01"
        raise EconomyIntegrityError(f"{field} must be {qualifier}")
    return number


def signed_money(value: Any, *, field: str = "amount") -> float:
    number = finite_number(value, field=field)
    rounded = round(number, 2)
    if number != 0 and rounded == 0:
        raise EconomyIntegrityError(f"{field} is below the minimum precision")
    return rounded


def positive_item_quantity(value: Any, *, field: str = "quantity") -> int:
    if isinstance(value, bool):
        raise EconomyIntegrityError(f"{field} must be an integer")
    try:
        number = finite_number(value, field=field)
    except EconomyIntegrityError as exc:
        raise EconomyIntegrityError(f"{field} must be an integer") from exc
    if not number.is_integer():
        raise EconomyIntegrityError(f"{field} must be an integer")
    quantity = int(number)
    if quantity < 1:
        raise EconomyIntegrityError(f"{field} must be at least 1")
    return quantity


def item_mapping(value: Any, *, field: str = "items") -> dict[str, int]:
    if not isinstance(value, dict):
        raise EconomyIntegrityError(f"{field} must be a mapping")
    result: dict[str, int] = {}
    for item_id, raw_count in value.items():
        if isinstance(raw_count, bool):
            raise EconomyIntegrityError(f"{field} count must be an integer")
        count = finite_number(raw_count, field=f"{field} count")
        if not count.is_integer() or count < 0:
            raise EconomyIntegrityError(f"{field} count must be a non-negative integer")
        if count > 0:
            result[str(item_id)] = int(count)
    return result


def _decode(raw: Any, default: Any = None) -> Any:
    if raw is None:
        return default
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw


def _encode(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def read_server_value(conn: sqlite3.Connection, guild_id: int, key: str, default: Any = None) -> Any:
    row = conn.execute(
        "SELECT config_value FROM server_configs WHERE guild_id = ? AND config_key = ?",
        (int(guild_id), key),
    ).fetchone()
    return _decode(row[0], default) if row else default


def write_server_value(conn: sqlite3.Connection, guild_id: int, key: str, value: Any) -> None:
    conn.execute(
        """
        INSERT INTO server_configs (guild_id, config_key, config_value)
        VALUES (?, ?, ?)
        ON CONFLICT(guild_id, config_key)
        DO UPDATE SET config_value = excluded.config_value
        """,
        (int(guild_id), key, _encode(value)),
    )


def read_user_value(
    conn: sqlite3.Connection,
    guild_id: int,
    user_id: int,
    key: str,
    default: Any = None,
) -> Any:
    row = conn.execute(
        "SELECT data_value FROM user_data WHERE user_id = ? AND guild_id = ? AND data_key = ?",
        (int(user_id), int(guild_id or GLOBAL_GUILD_ID), key),
    ).fetchone()
    return _decode(row[0], default) if row else default


def write_user_value(
    conn: sqlite3.Connection,
    guild_id: int,
    user_id: int,
    key: str,
    value: Any,
) -> None:
    conn.execute(
        """
        INSERT INTO user_data (user_id, guild_id, data_key, data_value)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id, guild_id, data_key)
        DO UPDATE SET data_value = excluded.data_value
        """,
        (int(user_id), int(guild_id or GLOBAL_GUILD_ID), key, _encode(value)),
    )


def balance_in(conn: sqlite3.Connection, guild_id: int, user_id: int) -> float:
    value = read_user_value(conn, guild_id, user_id, "economy_balance", 0.0)
    number = finite_number(value, field="stored balance")
    if number < 0:
        raise EconomyIntegrityError("stored balance cannot be negative")
    return round(number, 2)


def supply_in(conn: sqlite3.Connection, guild_id: int) -> float:
    value = read_server_value(conn, guild_id, "economy_total_supply", 0.0)
    number = finite_number(value, field="stored supply")
    if number < 0:
        raise EconomyIntegrityError("stored supply cannot be negative")
    return round(number, 2)


def _set_supply(conn: sqlite3.Connection, guild_id: int, value: float) -> float:
    value = round(finite_number(value, field="supply result"), 2)
    if value < 0:
        raise EconomyIntegrityError("supply result cannot be negative")
    write_server_value(conn, guild_id, "economy_total_supply", value)
    return value


def mutate_balance_in(
    conn: sqlite3.Connection,
    guild_id: int,
    user_id: int,
    delta: Any,
    *,
    update_supply: bool = True,
) -> tuple[bool, float, float]:
    guild_id = int(guild_id or GLOBAL_GUILD_ID)
    delta = signed_money(delta, field="balance delta")
    before = balance_in(conn, guild_id, user_id)
    requested = round(before + delta, 2)
    if requested < 0:
        return False, before, before
    if guild_id == GLOBAL_GUILD_ID and requested > MAX_GLOBAL_BALANCE:
        raise EconomyIntegrityError("global balance limit would be exceeded")

    write_user_value(conn, guild_id, user_id, "economy_balance", requested)
    stored_after = balance_in(conn, guild_id, user_id)
    if stored_after != requested:
        raise _risk(
            "settlement_balance_invariant_failed",
            guild_id=guild_id,
            user_id=user_id,
            balance_before=before,
            expected_balance_after=requested,
            balance_after=stored_after,
        )
    if guild_id != GLOBAL_GUILD_ID and update_supply:
        supply_before = supply_in(conn, guild_id)
        expected_supply = max(0.0, round(supply_before + (stored_after - before), 2))
        supply_after = _set_supply(conn, guild_id, expected_supply)
        if supply_after != expected_supply:
            raise _risk(
                "settlement_supply_invariant_failed",
                operation="balance_mutation",
                supply_before=supply_before,
                expected_supply=expected_supply,
                supply_after=supply_after,
                actual_balance_delta=round(stored_after - before, 2),
            )
    return True, before, stored_after


def mutate_balance_atomic(
    db_path: str,
    guild_id: int,
    user_id: int,
    delta: Any,
    *,
    connection: sqlite3.Connection | None = None,
) -> tuple[bool, float, float]:
    owns_connection = connection is None
    conn = connection or sqlite3.connect(db_path, timeout=30)
    is_server = int(guild_id or GLOBAL_GUILD_ID) != GLOBAL_GUILD_ID
    try:
        if owns_connection:
            conn.execute("BEGIN IMMEDIATE")
        if is_server:
            conn.execute("SAVEPOINT economy_settlement")
        result = mutate_balance_in(conn, guild_id, user_id, delta)
        if is_server:
            conn.execute("RELEASE economy_settlement")
        if owns_connection:
            conn.commit()
        return result
    except EconomyRiskError as exc:
        if is_server:
            if owns_connection:
                _raise_risk_with_blacklist(conn, guild_id, exc)
            conn.execute("ROLLBACK TO economy_settlement")
            auto_blacklist_in(conn, guild_id, exc.trigger, exc.observed)
            conn.execute("RELEASE economy_settlement")
        raise
    except Exception:
        if owns_connection:
            conn.rollback()
        raise
    finally:
        if owns_connection:
            conn.close()


def set_balance_atomic(
    db_path: str,
    guild_id: int,
    user_id: int,
    target: Any,
) -> tuple[float, float]:
    target = money(target, field="target balance", allow_zero=True)
    with closing(sqlite3.connect(db_path, timeout=30)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        is_server = int(guild_id or GLOBAL_GUILD_ID) != GLOBAL_GUILD_ID
        if is_server:
            conn.execute("SAVEPOINT economy_settlement")
        try:
            before = balance_in(conn, guild_id, user_id)
            success, _, after = mutate_balance_in(conn, guild_id, user_id, round(target - before, 2))
            if not success:
                raise EconomyIntegrityError("target balance is invalid")
            if is_server:
                conn.execute("RELEASE economy_settlement")
            conn.commit()
        except EconomyRiskError as exc:
            if is_server:
                _raise_risk_with_blacklist(conn, guild_id, exc)
            conn.rollback()
            raise
        except Exception:
            conn.rollback()
            raise
    return before, after


def mutate_many_atomic(
    db_path: str,
    guild_id: int,
    deltas_by_user: dict[int, Any],
) -> dict[int, tuple[float, float]]:
    """Apply several balance changes together; useful for multiplayer stakes."""
    deltas = {
        int(user_id): signed_money(delta, field="balance delta")
        for user_id, delta in deltas_by_user.items()
    }
    guild_id = int(guild_id or GLOBAL_GUILD_ID)
    is_server = guild_id != GLOBAL_GUILD_ID
    results: dict[int, tuple[float, float]] = {}
    with closing(sqlite3.connect(db_path, timeout=30)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        if is_server:
            conn.execute("SAVEPOINT economy_settlement")
        supply_before = supply_in(conn, guild_id) if is_server else None
        try:
            actual_delta = 0.0
            for user_id, delta in deltas.items():
                success, before, after = mutate_balance_in(
                    conn, guild_id, user_id, delta, update_supply=not is_server,
                )
                if not success:
                    raise EconomyInsufficientFunds(f"insufficient balance for user {user_id}")
                results[user_id] = (before, after)
                actual_delta += after - before
            if is_server:
                expected_delta = round(sum(deltas.values()), 2)
                expected = max(0.0, round(supply_before + expected_delta, 2))
                supply_after = _set_supply(conn, guild_id, expected)
                verify_server_settlement_invariant(
                    conn,
                    guild_id,
                    operation="multi_balance_mutation",
                    supply_before=supply_before,
                    supply_after=supply_after,
                    actual_balance_delta=actual_delta,
                    expected_balance_delta=expected_delta,
                )
                conn.execute("RELEASE economy_settlement")
            conn.commit()
        except EconomyRiskError as exc:
            if is_server:
                _raise_risk_with_blacklist(conn, guild_id, exc)
            conn.rollback()
            raise
        except Exception:
            conn.rollback()
            raise
    return results


def increment_transaction_in(conn: sqlite3.Connection, guild_id: int, amount: int = 1) -> int:
    current = read_server_value(conn, guild_id, "economy_transaction_count", 0)
    try:
        current = int(current)
    except (TypeError, ValueError) as exc:
        raise EconomyIntegrityError("stored transaction count is invalid") from exc
    updated = current + int(amount)
    write_server_value(conn, guild_id, "economy_transaction_count", updated)
    return updated


def increment_transaction_atomic(db_path: str, guild_id: int, amount: int = 1) -> int:
    with closing(sqlite3.connect(db_path, timeout=30)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        updated = increment_transaction_in(conn, guild_id, amount)
        conn.commit()
        return updated


def record_market_event_in(
    conn: sqlite3.Connection,
    guild_id: int,
    amount: float,
    weight: float,
    *,
    inflation: bool,
) -> float:
    """Record a shop event and apply one amount-relative rate adjustment."""
    amount = money(amount, field="market amount")
    weight = finite_number(weight, field="market weight")
    if weight < 0:
        raise EconomyIntegrityError("market weight cannot be negative")
    raw_rate = read_server_value(conn, guild_id, "economy_exchange_rate", 1.0)
    rate = finite_number(raw_rate, field="exchange rate")
    supply = supply_in(conn, guild_id)
    increment_transaction_in(conn, guild_id)
    if supply <= 0 or weight == 0:
        return rate
    ratio = abs(amount) / supply
    impact = min(math.log2(1 + ratio) * weight, 0.05)
    updated = rate * (1 - impact if inflation else 1 + impact)
    updated = round(max(MIN_EXCHANGE_RATE, min(100.0, updated)), 6)
    write_server_value(conn, guild_id, "economy_exchange_rate", updated)
    return updated


def record_inflation_event_in(
    conn: sqlite3.Connection,
    guild_id: int,
    amount: Any,
    weight: Any,
) -> float:
    amount = money(amount, field="inflation amount")
    weight = finite_number(weight, field="inflation weight")
    if weight < 0:
        raise EconomyIntegrityError("inflation weight cannot be negative")
    rate = finite_number(
        read_server_value(conn, guild_id, "economy_exchange_rate", 1.0),
        field="exchange rate",
    )
    supply = supply_in(conn, guild_id)
    admin_injected = finite_number(
        read_server_value(conn, guild_id, "economy_admin_injected", 0.0),
        field="admin injected",
    )
    organic = max(supply - admin_injected, 10_000.0, 1.0)
    abuse_penalty = 1 + (admin_injected / supply) ** 2 * 8 if supply > 0 else 9
    impact = min(math.log2(1 + abs(amount) / organic) * weight * abuse_penalty, 0.6)
    updated = round(max(MIN_EXCHANGE_RATE, min(100.0, rate * (1 - impact))), 6)
    write_server_value(conn, guild_id, "economy_exchange_rate", updated)
    return updated


def record_inflation_event_atomic(
    db_path: str,
    guild_id: int,
    amount: Any,
    weight: Any,
) -> float:
    with closing(sqlite3.connect(db_path, timeout=30)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        updated = record_inflation_event_in(conn, guild_id, amount, weight)
        conn.commit()
        return updated


def record_admin_injection_atomic(
    db_path: str,
    guild_id: int,
    amount: Any,
    weight: Any,
) -> float:
    amount = money(abs(finite_number(amount, field="admin injection")), field="admin injection")
    weight = finite_number(weight, field="admin injection weight")
    if weight < 0:
        raise EconomyIntegrityError("admin injection weight cannot be negative")
    with closing(sqlite3.connect(db_path, timeout=30)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = finite_number(
            read_server_value(conn, guild_id, "economy_admin_injected", 0.0),
            field="admin injected",
        )
        write_server_value(conn, guild_id, "economy_admin_injected", round(current + amount, 2))
        rate = finite_number(
            read_server_value(conn, guild_id, "economy_exchange_rate", 1.0),
            field="exchange rate",
        )
        supply = supply_in(conn, guild_id)
        admin_after = current + amount
        organic = max(supply - admin_after, 10_000.0, 1.0)
        impact = min(math.log2(1 + amount / organic) * weight * (1 + (admin_after / supply) ** 2 * 8 if supply > 0 else 9), 0.6)
        updated = round(max(MIN_EXCHANGE_RATE, min(100.0, rate * (1 - impact))), 6)
        write_server_value(conn, guild_id, "economy_exchange_rate", updated)
        conn.commit()
        return updated


def flow_blacklist_info_in(conn: sqlite3.Connection, guild_id: int) -> dict[str, Any]:
    value = read_server_value(conn, guild_id, FLOW_BLACKLIST_KEY, {}) or {}
    return value if isinstance(value, dict) and str(value.get("reason", "")).strip() else {}


def auto_blacklist_in(
    conn: sqlite3.Connection,
    guild_id: int,
    trigger: str,
    observed: dict[str, Any],
) -> bool:
    existing = flow_blacklist_info_in(conn, guild_id)
    if existing:
        # Records without a source predate automatic risk control and are manual.
        return False
    write_server_value(
        conn,
        guild_id,
        FLOW_BLACKLIST_KEY,
        {
            "reason": "經濟風控自動停用跨域貨幣流通",
            "source": "automatic",
            "trigger": trigger,
            "observed": observed,
            "set_by": None,
            "set_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return True


def _risk(trigger: str, **observed: Any) -> EconomyRiskError:
    return EconomyRiskError(trigger, observed)


def guarded_exchange_rate_in(conn: sqlite3.Connection, guild_id: int) -> float:
    blacklist = flow_blacklist_info_in(conn, guild_id)
    if blacklist:
        raise EconomyIntegrityError("cross-domain flow is blacklisted")
    raw = read_server_value(conn, guild_id, "economy_exchange_rate", 1.0)
    try:
        rate = float(raw)
    except (TypeError, ValueError):
        raise _risk("invalid_exchange_rate", exchange_rate=repr(raw))
    if not math.isfinite(rate):
        raise _risk("non_finite_exchange_rate", exchange_rate=repr(raw))
    if rate < MIN_EXCHANGE_RATE:
        raise _risk("exchange_rate_below_minimum", exchange_rate=rate)
    if rate >= AUTO_BLACKLIST_RATE:
        raise _risk("exchange_rate_at_or_above_risk_threshold", exchange_rate=rate)
    return rate


def verify_server_settlement_invariant(
    conn: sqlite3.Connection,
    guild_id: int,
    *,
    operation: str,
    supply_before: Any,
    supply_after: Any,
    actual_balance_delta: Any,
    expected_balance_delta: Any,
) -> None:
    """Verify only the current settlement, without treating legacy drift as fraud."""
    before = round(finite_number(supply_before, field="supply before"), 2)
    after = round(finite_number(supply_after, field="supply after"), 2)
    actual_delta = round(finite_number(actual_balance_delta, field="actual balance delta"), 2)
    expected_delta = round(finite_number(expected_balance_delta, field="expected balance delta"), 2)
    expected_supply = max(0.0, round(before + expected_delta, 2))
    if actual_delta != expected_delta or after != expected_supply:
        raise _risk(
            "settlement_supply_invariant_failed",
            operation=operation,
            supply_before=before,
            supply_after=after,
            expected_supply=expected_supply,
            actual_balance_delta=actual_delta,
            expected_balance_delta=expected_delta,
        )


def guard_cross_domain(
    db_path: str,
    guild_id: int,
    *,
    observed_values: dict[str, Any] | None = None,
) -> float:
    """Validate a cross-domain calculation and persist a risk block if invalid."""
    with closing(sqlite3.connect(db_path, timeout=30)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("SAVEPOINT economy_settlement")
        try:
            rate = guarded_exchange_rate_in(conn, guild_id)
            normalized: dict[str, float] = {}
            for key, value in (observed_values or {}).items():
                try:
                    normalized[key] = finite_number(value, field=key)
                except EconomyIntegrityError:
                    raise _risk("non_finite_cross_domain_value", field=key, value=repr(value))
            if normalized.get("global_balance_after", 0.0) > MAX_GLOBAL_BALANCE:
                raise _risk(
                    "global_balance_limit_exceeded",
                    global_balance_after=normalized["global_balance_after"],
                    maximum=MAX_GLOBAL_BALANCE,
                )
            conn.execute("RELEASE economy_settlement")
            conn.commit()
            return rate
        except EconomyRiskError as exc:
            _raise_risk_with_blacklist(conn, guild_id, exc)
        except Exception:
            conn.rollback()
            raise


def _raise_risk_with_blacklist(
    conn: sqlite3.Connection,
    guild_id: int,
    exc: EconomyRiskError,
) -> None:
    conn.execute("ROLLBACK TO economy_settlement")
    created = auto_blacklist_in(conn, guild_id, exc.trigger, exc.observed)
    conn.execute("RELEASE economy_settlement")
    conn.commit()
    exc.blacklist_created = created
    raise exc


@dataclass(frozen=True)
class TransferResult:
    amount: float
    fee: float
    sender_before: float
    sender_after: float
    receiver_before: float
    receiver_after: float


def settle_transfer(
    db_path: str,
    guild_id: int,
    sender_id: int,
    receiver_id: int,
    amount: Any,
    fee: Any,
    *,
    market_weight: float = 0.0,
) -> TransferResult:
    amount = money(amount)
    fee = money(fee, field="fee", allow_zero=True)
    total = round(amount + fee, 2)
    with closing(sqlite3.connect(db_path, timeout=30)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        is_server = int(guild_id or GLOBAL_GUILD_ID) != GLOBAL_GUILD_ID
        if is_server:
            conn.execute("SAVEPOINT economy_settlement")
        try:
            supply_before = supply_in(conn, guild_id) if is_server else None
            sender_before = balance_in(conn, guild_id, sender_id)
            receiver_before = balance_in(conn, guild_id, receiver_id)
            success, _, sender_after = mutate_balance_in(
                conn, guild_id, sender_id, -total, update_supply=not is_server,
            )
            if not success:
                raise EconomyInsufficientFunds("insufficient balance")
            _, _, receiver_after = mutate_balance_in(
                conn, guild_id, receiver_id, amount, update_supply=not is_server,
            )
            if is_server:
                expected_supply = max(0.0, round(supply_before - fee, 2))
                _set_supply(conn, guild_id, expected_supply)
                supply_after = supply_in(conn, guild_id)
                verify_server_settlement_invariant(
                    conn,
                    guild_id,
                    operation="transfer",
                    supply_before=supply_before,
                    supply_after=supply_after,
                    actual_balance_delta=(sender_after - sender_before) + (receiver_after - receiver_before),
                    expected_balance_delta=-fee,
                )
                if fee > 0 and market_weight > 0:
                    record_market_event_in(
                        conn, guild_id, fee, market_weight, inflation=False,
                    )
                else:
                    increment_transaction_in(conn, guild_id)
                conn.execute("RELEASE economy_settlement")
            conn.commit()
        except EconomyRiskError as exc:
            if is_server:
                _raise_risk_with_blacklist(conn, guild_id, exc)
            conn.rollback()
            raise
        except Exception:
            conn.rollback()
            raise
    return TransferResult(amount, fee, sender_before, sender_after, receiver_before, receiver_after)


@dataclass(frozen=True)
class ExchangeResult:
    direction: str
    rate: float
    spent: float
    received: float
    fee: float
    source_before: float
    source_after: float
    target_before: float
    target_after: float


def settle_exchange(
    db_path: str,
    guild_id: int,
    user_id: int,
    amount: Any,
    direction: str,
    fee_percent: Any,
    *,
    market_weight: float = 0.0,
) -> ExchangeResult:
    if direction not in {"to_global", "to_server"}:
        raise EconomyIntegrityError("unknown exchange direction")

    with closing(sqlite3.connect(db_path, timeout=30)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("SAVEPOINT economy_settlement")
        try:
            rate = guarded_exchange_rate_in(conn, guild_id)
            try:
                finite_number(amount, field="amount")
                finite_number(fee_percent, field="fee percent")
            except EconomyIntegrityError:
                raise _risk(
                    "non_finite_cross_domain_value",
                    amount=repr(amount),
                    fee_percent=repr(fee_percent),
                )
            amount = money(amount)
            fee_percent = finite_number(fee_percent, field="fee percent")
            if fee_percent < 0 or fee_percent >= 100:
                raise EconomyIntegrityError("fee percent is invalid")
            supply_before = supply_in(conn, guild_id)
            if direction == "to_global":
                gross = finite_number(amount * rate, field="global gross")
                fee = round(gross * fee_percent / 100, 2)
                received = round(gross - fee, 2)
                if not math.isfinite(fee) or not math.isfinite(received) or received < 0.01:
                    raise _risk(
                        "invalid_exchange_result",
                        direction=direction,
                        amount=amount,
                        exchange_rate=rate,
                        fee=repr(fee),
                        received=repr(received),
                    )
                source_before = balance_in(conn, guild_id, user_id)
                target_before = balance_in(conn, GLOBAL_GUILD_ID, user_id)
                if target_before + received > MAX_GLOBAL_BALANCE:
                    raise _risk(
                        "global_balance_limit_exceeded",
                        global_balance_before=target_before,
                        received=received,
                        global_balance_after=round(target_before + received, 2),
                        maximum=MAX_GLOBAL_BALANCE,
                    )
                success, _, source_after = mutate_balance_in(
                    conn, guild_id, user_id, -amount, update_supply=False,
                )
                if not success:
                    raise EconomyInsufficientFunds("insufficient server balance")
                _, _, target_after = mutate_balance_in(conn, GLOBAL_GUILD_ID, user_id, received)
                expected_supply = max(0.0, round(supply_before - amount, 2))
            else:
                gross = finite_number(amount / rate, field="server gross")
                fee = round(gross * fee_percent / 100, 2)
                received = round(gross - fee, 2)
                if not math.isfinite(fee) or not math.isfinite(received) or received < 0.01:
                    raise _risk(
                        "invalid_exchange_result",
                        direction=direction,
                        amount=amount,
                        exchange_rate=rate,
                        fee=repr(fee),
                        received=repr(received),
                    )
                source_before = balance_in(conn, GLOBAL_GUILD_ID, user_id)
                target_before = balance_in(conn, guild_id, user_id)
                success, _, source_after = mutate_balance_in(conn, GLOBAL_GUILD_ID, user_id, -amount)
                if not success:
                    raise EconomyInsufficientFunds("insufficient global balance")
                _, _, target_after = mutate_balance_in(
                    conn, guild_id, user_id, received, update_supply=False,
                )
                expected_supply = round(supply_before + received, 2)

            _set_supply(conn, guild_id, expected_supply)
            supply_after = supply_in(conn, guild_id)
            if direction == "to_global":
                actual_server_delta = source_after - source_before
                expected_server_delta = -amount
                actual_global_delta = target_after - target_before
                expected_global_delta = received
            else:
                actual_server_delta = target_after - target_before
                expected_server_delta = received
                actual_global_delta = source_after - source_before
                expected_global_delta = -amount
            if round(actual_global_delta, 2) != round(expected_global_delta, 2):
                raise _risk(
                    "settlement_balance_invariant_failed",
                    operation="exchange",
                    direction=direction,
                    actual_global_delta=round(actual_global_delta, 2),
                    expected_global_delta=round(expected_global_delta, 2),
                )
            verify_server_settlement_invariant(
                conn,
                guild_id,
                operation="exchange",
                supply_before=supply_before,
                supply_after=supply_after,
                actual_balance_delta=actual_server_delta,
                expected_balance_delta=expected_server_delta,
            )
            market_amount = amount if direction == "to_global" else received
            if market_amount > 0 and market_weight > 0:
                record_market_event_in(
                    conn,
                    guild_id,
                    market_amount,
                    market_weight,
                    inflation=(direction == "to_server"),
                )
            else:
                increment_transaction_in(conn, guild_id)
            conn.execute("RELEASE economy_settlement")
            conn.commit()
        except EconomyRiskError as exc:
            _raise_risk_with_blacklist(conn, guild_id, exc)
        except Exception:
            conn.rollback()
            raise

    return ExchangeResult(
        direction, rate, amount, received, fee,
        source_before, source_after, target_before, target_after,
    )


def settle_global_migration(
    db_path: str,
    guild_id: int,
    converted_global_by_user: dict[int, Any],
    *,
    expected_rate: float,
    expected_server_state: dict[int, dict[str, Any]] | None = None,
    global_mode_key: str | None = None,
) -> dict[int, tuple[float, float]]:
    """Clear a guild economy and credit precomputed global values atomically."""
    results: dict[int, tuple[float, float]] = {}
    with closing(sqlite3.connect(db_path, timeout=30)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("SAVEPOINT economy_settlement")
        try:
            locked_rate = guarded_exchange_rate_in(conn, guild_id)
            expected_rate = finite_number(expected_rate, field="expected exchange rate")
            if not math.isclose(locked_rate, expected_rate, rel_tol=0.0, abs_tol=0.0000005):
                raise EconomyIntegrityError("exchange rate changed before migration settlement")
            normalized: dict[int, float] = {}
            for user_id, value in converted_global_by_user.items():
                try:
                    finite_number(value, field="migration credit")
                except EconomyIntegrityError:
                    raise _risk(
                        "non_finite_cross_domain_value",
                        user_id=int(user_id),
                        migration_credit=repr(value),
                    )
                normalized[int(user_id)] = money(value, field="migration credit", allow_zero=True)

            if expected_server_state is not None:
                expected_ids = {int(user_id) for user_id in expected_server_state}
                current_ids: set[int] = set()
                for raw_user_id, key, raw_value in conn.execute(
                    """
                    SELECT user_id, data_key, data_value
                    FROM user_data
                    WHERE guild_id = ?
                      AND data_key IN ('economy_balance', 'items', 'admin_items')
                    """,
                    (int(guild_id),),
                ):
                    value = _decode(raw_value)
                    if (key == "economy_balance" and finite_number(value, field="stored balance") != 0) or (
                        key != "economy_balance" and isinstance(value, dict) and bool(value)
                    ):
                        current_ids.add(int(raw_user_id))
                if not current_ids.issubset(expected_ids):
                    raise EconomyIntegrityError("guild assets changed before migration settlement")
                for user_id, expected in expected_server_state.items():
                    user_id = int(user_id)
                    expected_balance = money(
                        expected.get("balance", 0), field="expected server balance", allow_zero=True,
                    )
                    if balance_in(conn, guild_id, user_id) != expected_balance:
                        raise EconomyIntegrityError("guild balance changed before migration settlement")
                    for key in ("items", "admin_items"):
                        expected_mapping = {
                            str(item_id): positive_item_quantity(count)
                            for item_id, count in (expected.get(key, {}) or {}).items()
                        }
                        if _mapping_in(conn, guild_id, user_id, key) != expected_mapping:
                            raise EconomyIntegrityError("guild inventory changed before migration settlement")

            expected_server_delta = 0.0
            actual_server_delta = 0.0
            expected_global_delta = 0.0
            actual_global_delta = 0.0
            for user_id, global_credit in normalized.items():
                global_before = balance_in(conn, GLOBAL_GUILD_ID, user_id)
                if global_before + global_credit > MAX_GLOBAL_BALANCE:
                    raise _risk(
                        "global_balance_limit_exceeded",
                        user_id=user_id,
                        global_balance_before=global_before,
                        received=global_credit,
                        global_balance_after=round(global_before + global_credit, 2),
                        maximum=MAX_GLOBAL_BALANCE,
                    )
                server_before = balance_in(conn, guild_id, user_id)
                if server_before:
                    _, _, server_after = mutate_balance_in(
                        conn, guild_id, user_id, -server_before, update_supply=False,
                    )
                else:
                    server_after = server_before
                write_user_value(conn, guild_id, user_id, "items", {})
                write_user_value(conn, guild_id, user_id, "admin_items", {})
                if global_credit:
                    _, _, global_after = mutate_balance_in(
                        conn, GLOBAL_GUILD_ID, user_id, global_credit,
                    )
                else:
                    global_after = global_before
                results[user_id] = (global_before, global_after)
                expected_server_delta -= server_before
                actual_server_delta += server_after - server_before
                expected_global_delta += global_credit
                actual_global_delta += global_after - global_before
            if round(actual_server_delta, 2) != round(expected_server_delta, 2) or round(
                actual_global_delta, 2,
            ) != round(expected_global_delta, 2):
                raise _risk(
                    "settlement_balance_invariant_failed",
                    operation="global_migration",
                    actual_server_delta=round(actual_server_delta, 2),
                    expected_server_delta=round(expected_server_delta, 2),
                    actual_global_delta=round(actual_global_delta, 2),
                    expected_global_delta=round(expected_global_delta, 2),
                )
            write_server_value(conn, guild_id, "economy_total_supply", 0.0)
            write_server_value(conn, guild_id, "economy_admin_injected", 0.0)
            write_server_value(conn, guild_id, "economy_transaction_count", 0)
            if global_mode_key:
                write_server_value(conn, guild_id, global_mode_key, True)
            conn.execute("RELEASE economy_settlement")
            conn.commit()
        except EconomyRiskError as exc:
            _raise_risk_with_blacklist(conn, guild_id, exc)
        except Exception:
            conn.rollback()
            raise
    return results


def _mapping_in(conn: sqlite3.Connection, guild_id: int, user_id: int, key: str) -> dict[str, int]:
    value = read_user_value(conn, guild_id, user_id, key, {}) or {}
    return item_mapping(value, field=f"stored {key}")


def _transfer_item_in(
    conn: sqlite3.Connection,
    guild_id: int,
    sender_id: int,
    receiver_id: int,
    item_id: str,
    quantity: int,
) -> None:
    quantity = positive_item_quantity(quantity)
    sender = _mapping_in(conn, guild_id, sender_id, "items")
    receiver = _mapping_in(conn, guild_id, receiver_id, "items")
    if sender.get(item_id, 0) < quantity:
        raise EconomyInsufficientFunds("insufficient item quantity")
    sender[item_id] -= quantity
    if sender[item_id] <= 0:
        sender.pop(item_id, None)
    receiver[item_id] = receiver.get(item_id, 0) + quantity
    write_user_value(conn, guild_id, sender_id, "items", sender)
    write_user_value(conn, guild_id, receiver_id, "items", receiver)

    sender_admin = _mapping_in(conn, guild_id, sender_id, "admin_items")
    admin_moved = min(sender_admin.get(item_id, 0), quantity)
    if admin_moved:
        receiver_admin = _mapping_in(conn, guild_id, receiver_id, "admin_items")
        sender_admin[item_id] -= admin_moved
        if sender_admin[item_id] <= 0:
            sender_admin.pop(item_id, None)
        receiver_admin[item_id] = receiver_admin.get(item_id, 0) + admin_moved
        write_user_value(conn, guild_id, sender_id, "admin_items", sender_admin)
        write_user_value(conn, guild_id, receiver_id, "admin_items", receiver_admin)


@dataclass(frozen=True)
class TradeResult:
    initiator_before: float
    initiator_after: float
    target_before: float
    target_after: float


def settle_trade(
    db_path: str,
    guild_id: int,
    initiator_id: int,
    target_id: int,
    *,
    offer_item: str | None,
    offer_item_amount: int,
    offer_money: Any,
    request_item: str | None,
    request_item_amount: int,
    request_money: Any,
) -> TradeResult:
    offer_money = money(offer_money, field="offer money", allow_zero=True)
    request_money = money(request_money, field="request money", allow_zero=True)
    if offer_item:
        offer_item_amount = positive_item_quantity(offer_item_amount, field="offer item quantity")
    if request_item:
        request_item_amount = positive_item_quantity(request_item_amount, field="request item quantity")
    if not offer_item and not request_item and offer_money == 0 and request_money == 0:
        raise EconomyIntegrityError("empty trade")

    with closing(sqlite3.connect(db_path, timeout=30)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        is_server = int(guild_id or GLOBAL_GUILD_ID) != GLOBAL_GUILD_ID
        if is_server:
            conn.execute("SAVEPOINT economy_settlement")
        supply_before = supply_in(conn, guild_id) if is_server else None
        try:
            initiator_before = balance_in(conn, guild_id, initiator_id)
            target_before = balance_in(conn, guild_id, target_id)
            if offer_item:
                _transfer_item_in(conn, guild_id, initiator_id, target_id, offer_item, offer_item_amount)
            if request_item:
                _transfer_item_in(conn, guild_id, target_id, initiator_id, request_item, request_item_amount)
            if offer_money:
                success, _, _ = mutate_balance_in(
                    conn, guild_id, initiator_id, -offer_money, update_supply=not is_server,
                )
                if not success:
                    raise EconomyInsufficientFunds("initiator balance is insufficient")
                mutate_balance_in(
                    conn, guild_id, target_id, offer_money, update_supply=not is_server,
                )
            if request_money:
                success, _, _ = mutate_balance_in(
                    conn, guild_id, target_id, -request_money, update_supply=not is_server,
                )
                if not success:
                    raise EconomyInsufficientFunds("target balance is insufficient")
                mutate_balance_in(
                    conn, guild_id, initiator_id, request_money, update_supply=not is_server,
                )
            initiator_after = balance_in(conn, guild_id, initiator_id)
            target_after = balance_in(conn, guild_id, target_id)
            if is_server:
                _set_supply(conn, guild_id, supply_before)
                supply_after = supply_in(conn, guild_id)
                verify_server_settlement_invariant(
                    conn,
                    guild_id,
                    operation="trade",
                    supply_before=supply_before,
                    supply_after=supply_after,
                    actual_balance_delta=(initiator_after - initiator_before) + (target_after - target_before),
                    expected_balance_delta=0.0,
                )
                increment_transaction_in(conn, guild_id)
                conn.execute("RELEASE economy_settlement")
            conn.commit()
        except EconomyRiskError as exc:
            if is_server:
                _raise_risk_with_blacklist(conn, guild_id, exc)
            conn.rollback()
            raise
        except Exception:
            conn.rollback()
            raise
    return TradeResult(initiator_before, initiator_after, target_before, target_after)


@dataclass(frozen=True)
class ItemSettlementResult:
    quantity: int
    total: float
    balance_before: float
    balance_after: float


def buy_item(
    db_path: str,
    guild_id: int,
    user_id: int,
    item_id: str,
    quantity: Any,
    total_price: Any,
    *,
    market_weight: float = 0.0,
    expected_rate: float | None = None,
    risk_guild_id: int | None = None,
) -> ItemSettlementResult:
    quantity = positive_item_quantity(quantity)
    total_price = money(total_price, field="total price")
    with closing(sqlite3.connect(db_path, timeout=30)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        is_server = int(guild_id or GLOBAL_GUILD_ID) != GLOBAL_GUILD_ID
        guard_guild_id = int(risk_guild_id) if risk_guild_id is not None else (
            int(guild_id) if expected_rate is not None else None
        )
        if is_server or guard_guild_id is not None:
            conn.execute("SAVEPOINT economy_settlement")
        try:
            if expected_rate is not None:
                if guard_guild_id is None:
                    raise EconomyIntegrityError("missing risk guild for expected exchange rate")
                locked_rate = guarded_exchange_rate_in(conn, guard_guild_id)
                expected_rate = finite_number(expected_rate, field="expected exchange rate")
                if not math.isclose(locked_rate, expected_rate, rel_tol=0.0, abs_tol=0.0000005):
                    raise EconomyIntegrityError("exchange rate changed before shop settlement")
            supply_before = supply_in(conn, guild_id) if is_server else None
            before = balance_in(conn, guild_id, user_id)
            success, _, after = mutate_balance_in(conn, guild_id, user_id, -total_price)
            if not success:
                raise EconomyInsufficientFunds("insufficient balance")
            inventory = _mapping_in(conn, guild_id, user_id, "items")
            inventory[item_id] = inventory.get(item_id, 0) + quantity
            write_user_value(conn, guild_id, user_id, "items", inventory)
            if is_server:
                supply_after = supply_in(conn, guild_id)
                verify_server_settlement_invariant(
                    conn,
                    guild_id,
                    operation="shop_buy",
                    supply_before=supply_before,
                    supply_after=supply_after,
                    actual_balance_delta=after - before,
                    expected_balance_delta=-total_price,
                )
                record_market_event_in(
                    conn, guild_id, total_price, market_weight, inflation=False,
                )
            if is_server or guard_guild_id is not None:
                conn.execute("RELEASE economy_settlement")
            conn.commit()
        except EconomyRiskError as exc:
            blacklist_guild_id = guard_guild_id if guard_guild_id is not None else (
                int(guild_id) if is_server else None
            )
            if blacklist_guild_id is not None:
                _raise_risk_with_blacklist(conn, blacklist_guild_id, exc)
            conn.rollback()
            raise
        except Exception:
            conn.rollback()
            raise
    return ItemSettlementResult(quantity, total_price, before, after)


def sell_item(
    db_path: str,
    guild_id: int,
    user_id: int,
    item_id: str,
    quantity: Any,
    total_price: Any,
    *,
    exclude_admin_items: bool,
    market_weight: float = 0.0,
    expected_rate: float | None = None,
    risk_guild_id: int | None = None,
) -> ItemSettlementResult:
    quantity = positive_item_quantity(quantity)
    total_price = money(total_price, field="total price", allow_zero=True)
    with closing(sqlite3.connect(db_path, timeout=30)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        is_server = int(guild_id or GLOBAL_GUILD_ID) != GLOBAL_GUILD_ID
        guard_guild_id = int(risk_guild_id) if risk_guild_id is not None else (
            int(guild_id) if expected_rate is not None else None
        )
        if is_server or guard_guild_id is not None:
            conn.execute("SAVEPOINT economy_settlement")
        try:
            if expected_rate is not None:
                if guard_guild_id is None:
                    raise EconomyIntegrityError("missing risk guild for expected exchange rate")
                locked_rate = guarded_exchange_rate_in(conn, guard_guild_id)
                expected_rate = finite_number(expected_rate, field="expected exchange rate")
                if not math.isclose(locked_rate, expected_rate, rel_tol=0.0, abs_tol=0.0000005):
                    raise EconomyIntegrityError("exchange rate changed before shop settlement")
            supply_before = supply_in(conn, guild_id) if is_server else None
            inventory = _mapping_in(conn, guild_id, user_id, "items")
            available = inventory.get(item_id, 0)
            if exclude_admin_items:
                available -= _mapping_in(conn, guild_id, user_id, "admin_items").get(item_id, 0)
            if available < quantity:
                raise EconomyInsufficientFunds("insufficient sellable item quantity")
            inventory[item_id] -= quantity
            if inventory[item_id] <= 0:
                inventory.pop(item_id, None)
            write_user_value(conn, guild_id, user_id, "items", inventory)
            before = balance_in(conn, guild_id, user_id)
            _, _, after = mutate_balance_in(conn, guild_id, user_id, total_price)
            if is_server:
                supply_after = supply_in(conn, guild_id)
                verify_server_settlement_invariant(
                    conn,
                    guild_id,
                    operation="shop_sell",
                    supply_before=supply_before,
                    supply_after=supply_after,
                    actual_balance_delta=after - before,
                    expected_balance_delta=total_price,
                )
                record_market_event_in(
                    conn, guild_id, total_price, market_weight, inflation=True,
                )
            if is_server or guard_guild_id is not None:
                conn.execute("RELEASE economy_settlement")
            conn.commit()
        except EconomyRiskError as exc:
            blacklist_guild_id = guard_guild_id if guard_guild_id is not None else (
                int(guild_id) if is_server else None
            )
            if blacklist_guild_id is not None:
                _raise_risk_with_blacklist(conn, blacklist_guild_id, exc)
            conn.rollback()
            raise
        except Exception:
            conn.rollback()
            raise
    return ItemSettlementResult(quantity, total_price, before, after)


def cash_cheque(
    db_path: str,
    guild_id: int,
    user_id: int,
    item_id: str,
    payout: Any,
    *,
    enforce_rate_guard: bool = False,
    observed_values: dict[str, Any] | None = None,
) -> ItemSettlementResult:
    supplied_value = payout
    if not enforce_rate_guard:
        payout = money(payout, field="cheque payout")
    with closing(sqlite3.connect(db_path, timeout=30)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        is_server = int(guild_id or GLOBAL_GUILD_ID) != GLOBAL_GUILD_ID
        if is_server:
            conn.execute("SAVEPOINT economy_settlement")
        try:
            if enforce_rate_guard:
                rate = guarded_exchange_rate_in(conn, guild_id)
                for key, value in (observed_values or {}).items():
                    try:
                        finite_number(value, field=key)
                    except EconomyIntegrityError:
                        raise _risk("non_finite_cross_domain_value", field=key, value=repr(value))
                face_value = finite_number(supplied_value, field="cheque face value")
                payout = round(face_value / rate, 2)
                if not math.isfinite(payout) or payout < 0.01:
                    raise _risk(
                        "invalid_cheque_result",
                        face_value=face_value,
                        exchange_rate=rate,
                        payout=repr(payout),
                    )
            inventory = _mapping_in(conn, guild_id, user_id, "items")
            admin = _mapping_in(conn, guild_id, user_id, "admin_items")
            if admin.get(item_id, 0) > 0:
                raise EconomyIntegrityError("admin cheque cannot be redeemed")
            if inventory.get(item_id, 0) < 1:
                raise EconomyInsufficientFunds("cheque not owned")
            inventory[item_id] -= 1
            if inventory[item_id] <= 0:
                inventory.pop(item_id, None)
            write_user_value(conn, guild_id, user_id, "items", inventory)
            supply_before = supply_in(conn, guild_id) if is_server else None
            before = balance_in(conn, guild_id, user_id)
            _, _, after = mutate_balance_in(conn, guild_id, user_id, payout)
            if is_server:
                supply_after = supply_in(conn, guild_id)
                verify_server_settlement_invariant(
                    conn,
                    guild_id,
                    operation="cheque_cashout",
                    supply_before=supply_before,
                    supply_after=supply_after,
                    actual_balance_delta=after - before,
                    expected_balance_delta=payout,
                )
                conn.execute("RELEASE economy_settlement")
            conn.commit()
        except EconomyRiskError as exc:
            if is_server:
                _raise_risk_with_blacklist(conn, guild_id, exc)
            conn.rollback()
            raise
        except Exception:
            conn.rollback()
            raise
    return ItemSettlementResult(1, payout, before, after)


@dataclass(frozen=True)
class RewardResult:
    already_claimed: bool
    balance_before: float
    balance_after: float
    base: float
    streak_bonus: float
    support_bonus: float
    streak: int


def claim_daily(
    db_path: str,
    guild_id: int,
    user_id: int,
    current_date: date,
    base_amount: Any,
    support_bonus_count: int,
    *,
    inflation_weight: Any = 0.0,
) -> RewardResult:
    base = money(base_amount, field="daily amount")
    with closing(sqlite3.connect(db_path, timeout=30)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        is_server = int(guild_id or GLOBAL_GUILD_ID) != GLOBAL_GUILD_ID
        if is_server:
            conn.execute("SAVEPOINT economy_settlement")
        try:
            raw_last = read_user_value(conn, guild_id, user_id, "economy_last_daily")
            try:
                last = date.fromisoformat(str(raw_last)[:10]) if raw_last else None
            except (TypeError, ValueError):
                last = None
            before = balance_in(conn, guild_id, user_id)
            if last == current_date:
                conn.rollback()
                return RewardResult(True, before, before, base, 0.0, 0.0, 0)
            old_streak = read_user_value(conn, guild_id, user_id, "economy_daily_streak", 0)
            try:
                old_streak = int(old_streak)
            except (TypeError, ValueError):
                old_streak = 0
            streak = old_streak + 1 if last and (current_date - last).days == 1 else 1
            streak_bonus = float(int(base * 0.5 + ((streak - 7) // 3) * 10)) if streak >= 7 else 0.0
            support_bonus = round((base + streak_bonus) * (0.1 * max(0, int(support_bonus_count))), 2)
            total = round(base + streak_bonus + support_bonus, 2)
            _, _, after = mutate_balance_in(conn, guild_id, user_id, total)
            write_user_value(conn, guild_id, user_id, "economy_last_daily", current_date.isoformat())
            write_user_value(conn, guild_id, user_id, "economy_daily_streak", streak)
            if is_server:
                record_inflation_event_in(conn, guild_id, total, inflation_weight)
                conn.execute("RELEASE economy_settlement")
            conn.commit()
        except EconomyRiskError as exc:
            if is_server:
                _raise_risk_with_blacklist(conn, guild_id, exc)
            conn.rollback()
            raise
        except Exception:
            conn.rollback()
            raise
    return RewardResult(False, before, after, base, streak_bonus, support_bonus, streak)


def claim_hourly(
    db_path: str,
    guild_id: int,
    user_id: int,
    current_hour: datetime,
    base_amount: Any,
    support_bonus_count: int,
    *,
    inflation_weight: Any = 0.0,
) -> RewardResult:
    base = money(base_amount, field="hourly amount")
    hour_key = current_hour.replace(minute=0, second=0, microsecond=0).isoformat()
    with closing(sqlite3.connect(db_path, timeout=30)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        is_server = int(guild_id or GLOBAL_GUILD_ID) != GLOBAL_GUILD_ID
        if is_server:
            conn.execute("SAVEPOINT economy_settlement")
        try:
            raw_last = read_user_value(conn, guild_id, user_id, "economy_last_hourly")
            try:
                last = datetime.fromisoformat(str(raw_last)).replace(minute=0, second=0, microsecond=0) if raw_last else None
            except (TypeError, ValueError):
                last = None
            before = balance_in(conn, guild_id, user_id)
            if last is not None and last == current_hour.replace(minute=0, second=0, microsecond=0):
                conn.rollback()
                return RewardResult(True, before, before, base, 0.0, 0.0, 0)
            support_bonus = round(base * (0.1 * max(0, int(support_bonus_count))), 2)
            total = round(base + support_bonus, 2)
            _, _, after = mutate_balance_in(conn, guild_id, user_id, total)
            write_user_value(conn, guild_id, user_id, "economy_last_hourly", hour_key)
            if is_server:
                record_inflation_event_in(conn, guild_id, total, inflation_weight)
                conn.execute("RELEASE economy_settlement")
            conn.commit()
        except EconomyRiskError as exc:
            if is_server:
                _raise_risk_with_blacklist(conn, guild_id, exc)
            conn.rollback()
            raise
        except Exception:
            conn.rollback()
            raise
    return RewardResult(False, before, after, base, 0.0, support_bonus, 0)
