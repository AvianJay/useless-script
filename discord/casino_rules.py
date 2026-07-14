"""Pure, shared rules for Discord and Explore casino games."""

from __future__ import annotations

import random
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple


BET_MIN = 50
BET_MAX = 2000

DICE_MULTIPLIER = 5.7
COINFLIP_MULTIPLIER = 1.9

ROULETTE_RED_NUMBERS = frozenset({
    1, 3, 5, 7, 9, 12, 14, 16, 18,
    19, 21, 23, 25, 27, 30, 32, 34, 36,
})
ROULETTE_BET_LABELS = {
    "red": "紅色",
    "black": "黑色",
    "odd": "單數",
    "even": "雙數",
    "low": "小（1～18）",
    "high": "大（19～36）",
    "number": "單一號碼",
}

SCRATCH_PRIZE_TABLE = [
    ("none", None, 5500, 0.0, "未中獎"),
    ("refund", "🍋", 2500, 1.0, "回本"),
    ("small", "🍒", 1200, 1.5, "小獎"),
    ("medium", "🔔", 600, 3.0, "中獎"),
    ("major", "7️⃣", 180, 10.0, "大獎"),
    ("jackpot", "💎", 20, 80.0, "頭獎"),
]
SCRATCH_SYMBOLS = ["🍋", "🍒", "🔔", "7️⃣", "💎", "⭐"]

LOTTERY_CONFIG_KEY = "minigames_lottery_state"
LOTTERY_DRAW_DELAY = timedelta(hours=1)
LOTTERY_PAYOUT_RATIO = 0.95

TOWER_LEVELS = 5
TOWER_TILES_PER_LEVEL = 3
TOWER_CACTUS_PER_LEVEL = 1
TOWER_MULTIPLIERS = [1.0, 1.4, 1.8, 2.2, 2.6, 3.0]

SLOT_SYMBOLS = [
    ("🍒", 30),
    ("🍋", 30),
    ("🔔", 20),
    ("💎", 15),
    ("7️⃣", 5),
]
SLOT_TRIPLE_PAYOUT = {
    "🍒": 3.0,
    "🍋": 4.0,
    "🔔": 8.0,
    "💎": 15.0,
    "7️⃣": 40.0,
}
SLOT_PAIR_PAYOUT = 1.2

HL_RANK_NAMES = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
HL_HOUSE_FACTOR = 0.95
HL_MAX_MULT = 50.0

BJ_SUITS = ["♠", "♥", "♦", "♣"]
BJ_RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]


def _rng(rng=None):
    return rng if rng is not None else random


def validate_bet(bet: Any) -> float:
    try:
        value = round(float(bet), 2)
    except (TypeError, ValueError) as exc:
        raise ValueError("賭注必須是數字。") from exc
    if value < BET_MIN or value > BET_MAX:
        raise ValueError(f"賭注金額需介於 {BET_MIN}～{BET_MAX} 之間。")
    return value


def roulette_color(number: int) -> str:
    if number == 0:
        return "green"
    return "red" if number in ROULETTE_RED_NUMBERS else "black"


def roulette_is_win(result: int, bet_type: str, chosen_number: Optional[int] = None) -> bool:
    if bet_type == "number":
        return chosen_number == result
    if result == 0:
        return False
    if bet_type == "red":
        return roulette_color(result) == "red"
    if bet_type == "black":
        return roulette_color(result) == "black"
    if bet_type == "odd":
        return result % 2 == 1
    if bet_type == "even":
        return result % 2 == 0
    if bet_type == "low":
        return 1 <= result <= 18
    if bet_type == "high":
        return 19 <= result <= 36
    return False


def play_roulette(bet_type: str, chosen_number: Optional[int] = None, rng=None) -> Dict[str, Any]:
    if bet_type not in ROULETTE_BET_LABELS:
        raise ValueError("無效的輪盤投注方式。")
    if bet_type == "number":
        if chosen_number is None or not 0 <= int(chosen_number) <= 36:
            raise ValueError("單號投注必須選擇 0～36。")
        chosen_number = int(chosen_number)
    elif chosen_number is not None:
        raise ValueError("只有單號投注能指定號碼。")
    result = _rng(rng).randint(0, 36)
    won = roulette_is_win(result, bet_type, chosen_number)
    return {
        "result": result,
        "color": roulette_color(result),
        "won": won,
        "multiplier": 36.0 if bet_type == "number" else 2.0,
    }


def play_dice(guess: int, rng=None) -> Dict[str, Any]:
    guess = int(guess)
    if not 1 <= guess <= 6:
        raise ValueError("骰子點數必須介於 1～6。")
    result = _rng(rng).randint(1, 6)
    return {"guess": guess, "result": result, "won": result == guess, "multiplier": DICE_MULTIPLIER}


def play_coinflip(side: str, rng=None) -> Dict[str, Any]:
    if side not in ("heads", "tails"):
        raise ValueError("硬幣選項必須是 heads 或 tails。")
    result = _rng(rng).choice(("heads", "tails"))
    return {"side": side, "result": result, "won": result == side, "multiplier": COINFLIP_MULTIPLIER}


def draw_scratch_prize(rng=None) -> Tuple[str, Optional[str], float, str]:
    source = _rng(rng)
    prize = source.choices(
        SCRATCH_PRIZE_TABLE,
        weights=[entry[2] for entry in SCRATCH_PRIZE_TABLE],
        k=1,
    )[0]
    return prize[0], prize[1], prize[3], prize[4]


def create_scratch_grid(winning_symbol: Optional[str], rng=None) -> List[str]:
    source = _rng(rng)
    cells = [winning_symbol] * 3 if winning_symbol else []
    counts = Counter(cells)
    while len(cells) < 9:
        available = [
            symbol for symbol in SCRATCH_SYMBOLS
            if symbol != winning_symbol and counts[symbol] < 2
        ]
        symbol = source.choice(available)
        cells.append(symbol)
        counts[symbol] += 1
    source.shuffle(cells)
    return cells


def scratch_grid_text(grid: Sequence[str], hidden: bool = False) -> str:
    cells = ["⬜" for _ in grid] if hidden else list(grid)
    return "\n".join("  ".join(cells[i:i + 3]) for i in range(0, 9, 3))


def play_scratchcard(rng=None) -> Dict[str, Any]:
    prize_key, winning_symbol, multiplier, prize_name = draw_scratch_prize(rng)
    return {
        "prize_key": prize_key,
        "prize_name": prize_name,
        "multiplier": multiplier,
        "grid": create_scratch_grid(winning_symbol, rng),
    }


def default_lottery_state() -> Dict[str, Any]:
    return {
        "jackpot": 0.0,
        "draw_at": None,
        "round_id": None,
        "tickets": {},
        "last_result": None,
        "pending_settlement": None,
    }


def normalize_lottery_state(raw_state: Any) -> Dict[str, Any]:
    state = default_lottery_state()
    if not isinstance(raw_state, dict):
        return state
    try:
        state["jackpot"] = max(0.0, round(float(raw_state.get("jackpot", 0.0)), 2))
    except (TypeError, ValueError):
        pass
    draw_at = raw_state.get("draw_at")
    if isinstance(draw_at, str):
        state["draw_at"] = draw_at
    round_id = raw_state.get("round_id")
    if isinstance(round_id, str):
        state["round_id"] = round_id
    tickets: Dict[str, Dict[str, float]] = {}
    raw_tickets = raw_state.get("tickets", {})
    if isinstance(raw_tickets, dict):
        for raw_number, raw_entries in raw_tickets.items():
            try:
                number = int(raw_number)
            except (TypeError, ValueError):
                continue
            if not 0 <= number <= 99 or not isinstance(raw_entries, dict):
                continue
            entries: Dict[str, float] = {}
            for raw_user_id, raw_stake in raw_entries.items():
                try:
                    user_id = str(int(raw_user_id))
                    stake = round(float(raw_stake), 2)
                except (TypeError, ValueError):
                    continue
                if stake > 0:
                    entries[user_id] = stake
            if entries:
                tickets[f"{number:02d}"] = entries
    state["tickets"] = tickets
    if isinstance(raw_state.get("last_result"), dict):
        state["last_result"] = raw_state["last_result"]
    if isinstance(raw_state.get("pending_settlement"), dict):
        state["pending_settlement"] = raw_state["pending_settlement"]
    return state


def parse_lottery_draw_at(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def allocate_lottery_payouts(jackpot: float, winning_stakes: Dict[int, float]) -> Dict[int, float]:
    valid_stakes = {uid: float(stake) for uid, stake in winning_stakes.items() if stake > 0}
    if not valid_stakes:
        return {}
    total_stake = sum(valid_stakes.values())
    payout_cents = int(round(round(jackpot * LOTTERY_PAYOUT_RATIO, 2) * 100))
    allocations_cents = {
        uid: int(payout_cents * stake / total_stake)
        for uid, stake in valid_stakes.items()
    }
    remainder = payout_cents - sum(allocations_cents.values())
    remainder_user = sorted(valid_stakes, key=lambda uid: (-valid_stakes[uid], uid))[0]
    allocations_cents[remainder_user] += remainder
    return {uid: cents / 100 for uid, cents in allocations_cents.items()}


def create_tower_grid(rng=None) -> List[List[int]]:
    source = _rng(rng)
    grid = []
    for _ in range(TOWER_LEVELS):
        row = [0] * (TOWER_TILES_PER_LEVEL - TOWER_CACTUS_PER_LEVEL) + [1] * TOWER_CACTUS_PER_LEVEL
        source.shuffle(row)
        grid.append(row)
    return grid


def spin_slots(rng=None) -> List[str]:
    source = _rng(rng)
    names = [symbol for symbol, _ in SLOT_SYMBOLS]
    weights = [weight for _, weight in SLOT_SYMBOLS]
    return source.choices(names, weights=weights, k=3)


def slots_multiplier(reels: Sequence[str]) -> Tuple[float, str]:
    if len(reels) != 3:
        raise ValueError("拉霸結果必須包含三個符號。")
    if reels[0] == reels[1] == reels[2]:
        return SLOT_TRIPLE_PAYOUT[reels[0]], "三連線！"
    if reels[0] == reels[1] or reels[1] == reels[2] or reels[0] == reels[2]:
        return SLOT_PAIR_PAYOUT, "一對"
    return 0.0, "未中獎"


def play_slots(rng=None) -> Dict[str, Any]:
    reels = spin_slots(rng)
    multiplier, prize_name = slots_multiplier(reels)
    return {"reels": reels, "multiplier": multiplier, "prize_name": prize_name, "won": multiplier > 0}


def hl_rank_name(rank: int) -> str:
    return HL_RANK_NAMES[rank - 1]


def hl_probs(rank: int) -> Tuple[float, float]:
    if not 1 <= rank <= 13:
        raise ValueError("牌面點數必須介於 1～13。")
    return (13 - rank) / 12, (rank - 1) / 12


def hl_draw_next(rank: int, rng=None) -> int:
    choices = [candidate for candidate in range(1, 14) if candidate != rank]
    return _rng(rng).choice(choices)


def hl_apply_win(pot: float, bet: float, current_rank: int, guess: str) -> Tuple[float, bool]:
    p_high, p_low = hl_probs(current_rank)
    if guess == "high":
        probability = p_high
    elif guess == "low":
        probability = p_low
    else:
        raise ValueError("比大小選項必須是 high 或 low。")
    if probability <= 0:
        raise ValueError("目前牌面不能選擇這個方向。")
    next_pot = round(pot * HL_HOUSE_FACTOR / probability, 2)
    cap = round(bet * HL_MAX_MULT, 2)
    return min(next_pot, cap), next_pot >= cap


def bj_new_deck(rng=None) -> List[Tuple[str, str]]:
    deck = [(rank, suit) for rank in BJ_RANKS for suit in BJ_SUITS]
    _rng(rng).shuffle(deck)
    return deck


def bj_hand_value(hand: Sequence[Sequence[str]]) -> Tuple[int, bool]:
    total = 0
    aces = 0
    for rank, _ in hand:
        if rank == "A":
            aces += 1
            total += 1
        elif rank in ("J", "Q", "K", "10"):
            total += 10
        else:
            total += int(rank)
    soft = False
    if aces and total + 10 <= 21:
        total += 10
        soft = True
    return total, soft


def bj_is_blackjack(hand: Sequence[Sequence[str]]) -> bool:
    return len(hand) == 2 and bj_hand_value(hand)[0] == 21


def bj_hand_str(hand: Sequence[Sequence[str]], code_ticks: bool = True) -> str:
    cards = [f"{rank}{suit}" for rank, suit in hand]
    if code_ticks:
        cards = [f"`{card}`" for card in cards]
    return " ".join(cards)


def bj_settle(
    player_hand: List[Sequence[str]],
    dealer_hand: List[Sequence[str]],
    deck: List[Sequence[str]],
    bet: float,
) -> Dict[str, Any]:
    player_total, _ = bj_hand_value(player_hand)
    player_blackjack = bj_is_blackjack(player_hand)
    dealer_blackjack = bj_is_blackjack(dealer_hand)
    if player_total <= 21 and not player_blackjack and not dealer_blackjack:
        while bj_hand_value(dealer_hand)[0] < 17:
            dealer_hand.append(deck.pop())
    dealer_total, _ = bj_hand_value(dealer_hand)
    if player_total > 21:
        outcome, payout = "player_bust", 0.0
    elif player_blackjack and dealer_blackjack:
        outcome, payout = "push", bet
    elif player_blackjack:
        outcome, payout = "blackjack", round(bet * 2.5, 2)
    elif dealer_blackjack:
        outcome, payout = "dealer_blackjack", 0.0
    elif dealer_total > 21:
        outcome, payout = "dealer_bust", round(bet * 2, 2)
    elif player_total > dealer_total:
        outcome, payout = "win", round(bet * 2, 2)
    elif player_total == dealer_total:
        outcome, payout = "push", bet
    else:
        outcome, payout = "lose", 0.0
    return {
        "outcome": outcome,
        "payout": payout,
        "player_total": player_total,
        "dealer_total": dealer_total,
        "player_blackjack": player_blackjack,
        "dealer_blackjack": dealer_blackjack,
    }
