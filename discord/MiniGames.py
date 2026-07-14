from globalenv import (
    bot,
    start_bot,
    get_user_data,
    set_user_data,
    get_server_config,
    set_server_config,
    get_all_server_config_key,
    get_emoji_mention_by_name,
)
import discord
from Economy import (
    get_balance,
    add_balance,
    remove_balance,
    get_currency_name,
    record_transaction,
    log_transaction,
    queue_economy_audit_log,
    GLOBAL_GUILD_ID,
    interaction_uses_server_scope,
)
from discord.ext import commands, tasks
from discord import app_commands
from logger import log
import logging

import random
import secrets
import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Dict, Tuple, Any
from collections import Counter


# -----------------------------
# DOOM (from doomcord.py)
# -----------------------------

def generate_doom_embed(link="https://doom.p2r3.com/i.webp", step=None):
    embed = discord.Embed(color=0xff0000)
    if step:
        if step not in ["w", "a", "s", "d", "q", "e"]:
            raise ValueError("Invalid step")
        link = link.replace("i", step + "i")
    embed.set_image(url=link)
    embed.set_footer(text="Doomcord by PortalRunner", icon_url="https://yt3.ggpht.com/ytc/AIdro_mWb-zYQYCfaIC0pRsxHQqxQiIIpDLXOhB1YZXPgMKGsQ=s68-c-k-c0x00ffffff-no-rj")
    return embed, link


# -----------------------------
# Card / Rules
# -----------------------------

SUITS = ["♣", "♦", "♥", "♠"]          # low -> high
RANKS = ["3","4","5","6","7","8","9","10","J","Q","K","A","2"]  # low -> high

def r_value(rank: str) -> int:
    return RANKS.index(rank)

def s_value(suit: str) -> int:
    return SUITS.index(suit)

@dataclass(frozen=True)
class Card:
    rank: str
    suit: str

    @property
    def power(self) -> Tuple[int, int]:
        # rank then suit
        return (r_value(self.rank), s_value(self.suit))

    def __str__(self) -> str:
        return f"{self.rank}{self.suit}"

@dataclass
class Ruleset:
    must_start_with_3d: bool = True      # 首手必包含 3♦
    allow_2_in_straight: bool = False    # 一般規則：2 不算順子
    # 之後可擴充：花色順序、同花比較規則等


# -----------------------------
# Game State
# -----------------------------

@dataclass
class PlayerState:
    user_id: int
    hand: List[Card] = field(default_factory=list)
    passed: bool = False
    finished: bool = False

@dataclass
class Game:
    channel_id: int
    owner_id: int
    guild_id: int = 0  # 伺服器 ID，DM 為 0 用全域幣
    rules: Ruleset = field(default_factory=Ruleset)
    players: List[PlayerState] = field(default_factory=list)
    stake: float = 0  # 賭注（每人），0 = 不賭

    started: bool = False
    first_trick: bool = True

    turn_index: int = 0
    table_cards: Optional[List[Card]] = None
    table_owner: Optional[int] = None

    lobby_message_id: Optional[int] = None
    lobby_message: Optional[discord.Message] = None  # 存參考，user-install 時 fetch_message 常失敗
    finish_order: List[int] = field(default_factory=list)  # 依出完牌順序：第 1 名、第 2 名…
    stake_paid: bool = False  # 賭注局獎金是否已發放（只發一次）
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    active_view: Optional[discord.ui.View] = field(default=None, init=False, repr=False)

    def current_player(self) -> PlayerState:
        return self.players[self.turn_index]

    def is_game_over(self) -> bool:
        return len(self.alive()) <= 1

    def ensure_turn_alive(self) -> None:
        """確保 turn_index 指向未出完的玩家，若全出完則不變。"""
        n = len(self.players)
        for _ in range(n):
            if not self.players[self.turn_index].finished:
                return
            self.turn_index = (self.turn_index + 1) % n

    def find_player(self, uid: int) -> PlayerState:
        return next(p for p in self.players if p.user_id == uid)

    def index_of(self, uid: int) -> int:
        for i, p in enumerate(self.players):
            if p.user_id == uid:
                return i
        return -1

    def alive(self) -> List[PlayerState]:
        return [p for p in self.players if not p.finished]

    def next_turn(self):
        n = len(self.players)
        for _ in range(n):
            self.turn_index = (self.turn_index + 1) % n
            if not self.players[self.turn_index].finished:
                return

    def trick_passed_count(self) -> int:
        # count passed players among unfinished players excluding table_owner
        c = 0
        for p in self.players:
            if p.finished:
                continue
            if self.table_owner is not None and p.user_id == self.table_owner:
                continue
            if p.passed:
                c += 1
        return c

    def trick_active_count(self) -> int:
        # number of unfinished players excluding table_owner
        c = 0
        for p in self.players:
            if p.finished:
                continue
            if self.table_owner is not None and p.user_id == self.table_owner:
                continue
            c += 1
        return c

    def reset_trick(self):
        # Clear table and reset passed flags (new trick starts)
        self.table_cards = None
        self.table_owner = None
        for p in self.players:
            p.passed = False


# -----------------------------
# Hand evaluation and comparison
# -----------------------------

# Hand Types: higher number = stronger
HT_SINGLE = 1
HT_PAIR = 2
HT_TRIPLE = 3
HT_STRAIGHT = 4
HT_FLUSH = 5
HT_FULLHOUSE = 6
HT_FOUROK = 7
HT_STRAIGHTFLUSH = 8

def sort_cards(cards: List[Card]) -> List[Card]:
    return sorted(cards, key=lambda c: c.power)

def max_card(cards: List[Card]) -> Card:
    return max(cards, key=lambda c: c.power)

def is_straight_5(cards: List[Card], rules: Ruleset) -> Tuple[bool, Card]:
    # Big Two typical: 2 not allowed in straight (unless rules allow).
    sc = sort_cards(cards)
    ranks = [r_value(c.rank) for c in sc]

    if not rules.allow_2_in_straight and r_value("2") in ranks:
        return False, sc[-1]

    # must be consecutive ranks
    for i in range(4):
        if ranks[i+1] - ranks[i] != 1:
            return False, sc[-1]
    # highest card determines straight strength (rank then suit)
    return True, sc[-1]

def hand_signature(cards: List[Card], rules: Ruleset) -> Tuple[int, Any]:
    """
    Return (hand_type, key) where key is comparable tuple for tie-break.
    Higher (hand_type, key) means stronger.
    """
    n = len(cards)
    cards_sorted = sort_cards(cards)

    if n == 1:
        c = cards_sorted[0]
        return (HT_SINGLE, c.power)

    if n == 2:
        if cards_sorted[0].rank != cards_sorted[1].rank:
            raise ValueError("不是對子")
        # compare by rank, then highest suit among the pair
        rank = r_value(cards_sorted[0].rank)
        high_suit = max(s_value(cards_sorted[0].suit), s_value(cards_sorted[1].suit))
        return (HT_PAIR, (rank, high_suit))

    if n == 3:
        if not (cards_sorted[0].rank == cards_sorted[1].rank == cards_sorted[2].rank):
            raise ValueError("不是三條")
        rank = r_value(cards_sorted[0].rank)
        high_suit = max(s_value(c.suit) for c in cards_sorted)
        return (HT_TRIPLE, (rank, high_suit))

    if n != 5:
        raise ValueError("張數必須為 1/2/3/5")

    # count ranks/suits
    rank_counts = Counter(c.rank for c in cards_sorted)
    suit_counts = Counter(c.suit for c in cards_sorted)

    is_flush = (len(suit_counts) == 1)
    is_straight, top = is_straight_5(cards_sorted, rules)

    if is_straight and is_flush:
        # highest card decides
        return (HT_STRAIGHTFLUSH, top.power)

    # Four of a kind: 4 + 1
    if sorted(rank_counts.values()) == [1,4]:
        quad_rank = None
        kicker = None
        for r, cnt in rank_counts.items():
            if cnt == 4:
                quad_rank = r
            else:
                kicker = r
        # tie-break: quad rank, then kicker highest suit (for completeness)
        quad_val = r_value(quad_rank)
        kicker_cards = [c for c in cards_sorted if c.rank == kicker]
        return (HT_FOUROK, (quad_val, max_card(kicker_cards).power))

    # Full house: 3 + 2
    if sorted(rank_counts.values()) == [2,3]:
        trip_rank = None
        pair_rank = None
        for r, cnt in rank_counts.items():
            if cnt == 3:
                trip_rank = r
            else:
                pair_rank = r
        return (HT_FULLHOUSE, (r_value(trip_rank), r_value(pair_rank)))

    if is_flush:
        # flush: compare the sorted powers from highest to lowest
        powers_desc = sorted((c.power for c in cards_sorted), reverse=True)
        return (HT_FLUSH, tuple(powers_desc))

    if is_straight:
        return (HT_STRAIGHT, top.power)

    raise ValueError("不是合法五張牌型（順/同花/葫蘆/鐵支/同花順）")

def legal_size(cards: List[Card]) -> bool:
    return len(cards) in (1,2,3,5)

def must_follow_table(prev: Optional[List[Card]], new: List[Card]) -> bool:
    if prev is None:
        return True
    return len(prev) == len(new)

def can_pass(game: Game, player: PlayerState) -> Tuple[bool, str]:
    # cannot pass when table is empty (lead required)
    if game.table_cards is None:
        return False, "空桌不能 Pass，必須先領出。"
    return True, ""

def _has_3d(cards: List[Card]) -> bool:
    """用數字索引判斷，避免字元比對問題。"""
    return any(r_value(c.rank) == 0 and s_value(c.suit) == 1 for c in cards)

def is_first_move_requires_3d(game: Game, chosen: List[Card]) -> Tuple[bool, str]:
    if game.rules.must_start_with_3d and game.first_trick and game.table_cards is None:
        if not _has_3d(chosen):
            return False, "首手必須包含 3♦。"
    return True, ""

def beats(prev: Optional[List[Card]], new: List[Card], rules: Ruleset) -> bool:
    # if no previous, always ok
    if prev is None:
        return True

    if len(prev) != len(new):
        return False

    prev_type, prev_key = hand_signature(prev, rules)
    new_type, new_key = hand_signature(new, rules)

    # For same size: 1/2/3 must be same type; 5 can be different but compares by type strength.
    if len(new) in (1,2,3):
        if prev_type != new_type:
            return False
        return (new_key > prev_key)

    # 5-card: compare type first, then key
    if new_type != prev_type:
        return new_type > prev_type
    return new_key > prev_key


# -----------------------------
# Tower Game
# -----------------------------

TOWER_LEVELS = 5
TOWER_TILES_PER_LEVEL = 3
TOWER_CACTUS_PER_LEVEL = 1  # 每層 1 個仙人掌
TOWER_MULTIPLIERS = [1.0, 1.4, 1.8, 2.2, 2.6, 3.0]  # level 0=退還本金, 1~5 對應倍率（頂層 3.0x）
EMOJI_SAFE = "🟦"
EMOJI_REVEALED_SAFE = "✅"
EMOJI_CACTUS = "🌵"


@dataclass
class TowerGame:
    """Tower 爬塔遊戲狀態"""
    user_id: int
    channel_id: int
    guild_id: int
    bet: float
    current_level: int  # 1-based，1~5
    grid: List[List[int]]  # grid[level][tile_idx] = 0=安全, 1=仙人掌
    picked_per_level: Dict[int, Tuple[int, bool]] = field(default_factory=dict)  # level -> (tile_idx, is_cactus)
    awaiting_continue: bool = False  # 選到安全格後等待 繼續/提現
    game_over_cactus: bool = False  # 踩到仙人掌，仙人掌按鈕變紅
    game_over_reveal_all: bool = False  # 遊戲結束後揭露全部仙人掌
    message_id: Optional[int] = None
    message: Optional[discord.Message] = None
    active_view: Optional[discord.ui.View] = field(default=None, init=False, repr=False)

    def safe_level(self) -> int:
        """目前已安全達到的層數（可提現的倍率層）"""
        safe_levels = [lv for lv, (_, is_cactus) in self.picked_per_level.items() if not is_cactus]
        return max(safe_levels) if safe_levels else 0


def create_tower_grid() -> List[List[int]]:
    """建立隨機塔層：每層 3 格，其中 1 格為仙人掌"""
    grid = []
    for _ in range(TOWER_LEVELS):
        row = [0] * (TOWER_TILES_PER_LEVEL - TOWER_CACTUS_PER_LEVEL) + [1] * TOWER_CACTUS_PER_LEVEL
        random.shuffle(row)
        grid.append(row)
    return grid


# -----------------------------
# 賭博遊戲共用
# -----------------------------

BET_MIN = 50
BET_MAX = 2000


def resolve_game_guild_id(interaction: discord.Interaction, use_global: bool = False) -> int:
    """決定遊戲使用的經濟範圍：use_global 或非伺服器情境走全域幣。"""
    if use_global or not interaction_uses_server_scope(interaction):
        return GLOBAL_GUILD_ID
    return interaction.guild.id


async def game_emoji(name: str, fallback: str) -> str:
    """取得已上傳的 app emoji，沒有就用 Unicode fallback。"""
    mention = await get_emoji_mention_by_name(name)
    if mention == f":{name}:":
        return fallback
    return mention


# -----------------------------
# Roulette / Dice / Coinflip / Scratchcard / Lottery
# -----------------------------

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


# (key, winning symbol, weight / 10000, total payout multiplier, label)
SCRATCH_PRIZE_TABLE = [
    ("none", None, 5500, 0.0, "未中獎"),
    ("refund", "🍋", 2500, 1.0, "回本"),
    ("small", "🍒", 1200, 1.5, "小獎"),
    ("medium", "🔔", 600, 3.0, "中獎"),
    ("major", "7️⃣", 180, 10.0, "大獎"),
    ("jackpot", "💎", 20, 80.0, "頭獎"),
]
SCRATCH_SYMBOLS = ["🍋", "🍒", "🔔", "7️⃣", "💎", "⭐"]


def draw_scratch_prize() -> Tuple[str, Optional[str], float, str]:
    prize = random.choices(
        SCRATCH_PRIZE_TABLE,
        weights=[entry[2] for entry in SCRATCH_PRIZE_TABLE],
        k=1,
    )[0]
    return prize[0], prize[1], prize[3], prize[4]


def create_scratch_grid(winning_symbol: Optional[str]) -> List[str]:
    """建立只會出現指定三連符號的票面；未中獎票面每種符號最多兩個。"""
    cells = [winning_symbol] * 3 if winning_symbol else []
    counts = Counter(cells)
    while len(cells) < 9:
        available = [
            symbol for symbol in SCRATCH_SYMBOLS
            if symbol != winning_symbol and counts[symbol] < 2
        ]
        symbol = random.choice(available)
        cells.append(symbol)
        counts[symbol] += 1
    random.shuffle(cells)
    return cells


def scratch_grid_text(grid: List[str], hidden: bool = False) -> str:
    cells = ["⬜" for _ in grid] if hidden else grid
    return "\n".join("  ".join(cells[i:i + 3]) for i in range(0, 9, 3))


@dataclass
class ScratchcardGame:
    user_id: int
    channel_id: int
    guild_id: int
    bet: float
    prize_key: str
    prize_name: str
    multiplier: float
    grid: List[str]
    settled: bool = False
    message: Optional[discord.Message] = None
    active_view: Optional[discord.ui.View] = field(default=None, init=False, repr=False)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


LOTTERY_CONFIG_KEY = "minigames_lottery_state"
LOTTERY_DRAW_DELAY = timedelta(hours=1)
LOTTERY_PAYOUT_RATIO = 0.95


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
    """按投注比例分配 95% 獎池；不足一分的餘額給最大投注者。"""
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


# -----------------------------
# Slots 拉霸機
# -----------------------------

# (符號, 權重)
SLOT_SYMBOLS = [
    ("🍒", 30),
    ("🍋", 30),
    ("🔔", 20),
    ("💎", 15),
    ("7️⃣", 5),
]
# 三同賠率（總回傳倍率）
SLOT_TRIPLE_PAYOUT = {
    "🍒": 3.0,
    "🍋": 4.0,
    "🔔": 8.0,
    "💎": 15.0,
    "7️⃣": 40.0,
}
SLOT_PAIR_PAYOUT = 1.2  # 任意一對


def spin_slots() -> List[str]:
    names = [s[0] for s in SLOT_SYMBOLS]
    weights = [s[1] for s in SLOT_SYMBOLS]
    return random.choices(names, weights=weights, k=3)


def slots_multiplier(reels: List[str]) -> Tuple[float, str]:
    """回傳 (總回傳倍率, 獎項名稱)"""
    if reels[0] == reels[1] == reels[2]:
        return SLOT_TRIPLE_PAYOUT[reels[0]], "三連線！"
    if reels[0] == reels[1] or reels[1] == reels[2] or reels[0] == reels[2]:
        return SLOT_PAIR_PAYOUT, "一對"
    return 0.0, "未中獎"


# -----------------------------
# HighLow 比大小
# -----------------------------

HL_RANK_NAMES = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
HL_HOUSE_FACTOR = 0.95  # 每步 RTP 95%
HL_MAX_MULT = 50.0  # pot 上限 50x 自動提現


def hl_rank_name(rank: int) -> str:
    return HL_RANK_NAMES[rank - 1]


def hl_probs(rank: int) -> Tuple[float, float]:
    """回傳 (p_high, p_low)：下一張從 12 個不同點數中抽。"""
    return (13 - rank) / 12, (rank - 1) / 12


def hl_draw_next(rank: int) -> int:
    choices = [r for r in range(1, 14) if r != rank]
    return random.choice(choices)


@dataclass
class HighLowGame:
    user_id: int
    channel_id: int
    guild_id: int
    bet: float
    current_rank: int
    current_suit: str
    streak: int = 0
    pot: float = 0.0
    message: Optional[discord.Message] = None
    active_view: Optional[discord.ui.View] = field(default=None, init=False, repr=False)


# -----------------------------
# Blackjack 21點
# -----------------------------

BJ_SUITS = ["♠", "♥", "♦", "♣"]
BJ_RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]


def bj_new_deck() -> List[Tuple[str, str]]:
    deck = [(r, s) for r in BJ_RANKS for s in BJ_SUITS]
    random.shuffle(deck)
    return deck


def bj_hand_value(hand: List[Tuple[str, str]]) -> Tuple[int, bool]:
    """回傳 (最佳點數, 是否軟牌)。A 可為 1 或 11。"""
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


def bj_is_blackjack(hand: List[Tuple[str, str]]) -> bool:
    return len(hand) == 2 and bj_hand_value(hand)[0] == 21


def bj_hand_str(hand: List[Tuple[str, str]]) -> str:
    return " ".join(f"`{r}{s}`" for r, s in hand)


@dataclass
class BlackjackGame:
    user_id: int
    channel_id: int
    guild_id: int
    bet: float  # 總下注（Double 後翻倍）
    deck: List[Tuple[str, str]]
    player_hand: List[Tuple[str, str]] = field(default_factory=list)
    dealer_hand: List[Tuple[str, str]] = field(default_factory=list)
    doubled: bool = False
    settled: bool = False
    message: Optional[discord.Message] = None
    active_view: Optional[discord.ui.View] = field(default=None, init=False, repr=False)


# -----------------------------
# Discord Views
# -----------------------------

class LobbyView(discord.ui.View):
    def __init__(self, cog: "MiniGamesCog", game: Game):
        super().__init__(timeout=600)
        self.cog = cog
        self.game = game
        self.message = None

        self.rule_select = discord.ui.Select(
            placeholder="規則（房主可選）",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label="一般規則（首手必含3♦，2不可成順）", value="classic"),
                discord.SelectOption(label="自由先手（不強制3♦）", value="free_start"),
            ]
        )
        self.rule_select.callback = self.on_rule_change
        self.add_item(self.rule_select)

        self.stake_select = discord.ui.Select(
            placeholder="賭注（房主可選）",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label="不賭", value="0"),
                discord.SelectOption(label="賭 10", value="10"),
                discord.SelectOption(label="賭 50", value="50"),
                discord.SelectOption(label="賭 100", value="100"),
                discord.SelectOption(label="賭 500", value="500"),
            ]
        )
        self.stake_select.callback = self.on_stake_change
        self.add_item(self.stake_select)

    @discord.ui.button(label="📜 規則玩法", style=discord.ButtonStyle.secondary, row=1)
    async def rules_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            embed=self.cog.big2_rules_embed(),
            ephemeral=True,
        )

    async def on_rule_change(self, interaction: discord.Interaction):
        if interaction.user.id != self.game.owner_id:
            return await interaction.response.send_message("只有房主可以改規則。", ephemeral=True)

        v = self.rule_select.values[0]
        if v == "classic":
            self.game.rules.must_start_with_3d = True
        elif v == "free_start":
            self.game.rules.must_start_with_3d = False

        await interaction.response.send_message("已更新規則。", ephemeral=True)
        await self.cog.edit_lobby_message(interaction, self.game)

    async def on_stake_change(self, interaction: discord.Interaction):
        if interaction.user.id != self.game.owner_id:
            return await interaction.response.send_message("只有房主可以改賭注。", ephemeral=True)
        v = self.stake_select.values[0]
        self.game.stake = float(v)
        await interaction.response.send_message("已更新賭注。", ephemeral=True)
        await self.cog.edit_lobby_message(interaction, self.game)

    @discord.ui.button(label="✅ 加入", style=discord.ButtonStyle.primary)
    async def join_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.join(interaction, self.game)

    @discord.ui.button(label="▶ 開始", style=discord.ButtonStyle.success)
    async def start_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.start(interaction, self.game)

    @discord.ui.button(label="❌ 取消", style=discord.ButtonStyle.danger)
    async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.game.owner_id:
            return await interaction.response.send_message("只有房主可以取消。", ephemeral=True)
        self.cog.games.pop(self.game.channel_id, None)
        await interaction.response.edit_message(content="此桌已取消。", embed=None, view=None)
        self.stop()

    async def on_timeout(self):
        if self.game.active_view is not self:
            return
        self.game.active_view = None
        self.cog.games.pop(self.game.channel_id, None)
        for child in self.children:
            child.disabled = True
        if self.game.lobby_message is not None:
            try:
                await self.game.lobby_message.edit(content="大廳已逾時。", embed=None, view=self)
            except (discord.NotFound, discord.HTTPException):
                pass
        self.stop()


# -----------------------------
# Tower Views
# -----------------------------

class TowerConfirmView(discord.ui.View):
    """確認開始 Tower 遊戲"""

    def __init__(self, cog: "MiniGamesCog", guild_id: int, bet: float):
        super().__init__(timeout=60)
        self.cog = cog
        self.guild_id = guild_id
        self.bet = bet
        self.message = None

    @discord.ui.button(label="✅ 確認開始", style=discord.ButtonStyle.success)
    async def confirm_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await self.cog.start_tower(interaction, self.bet, self.guild_id):
            self.stop()

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(content="已逾時，請重新下指令開始。", embed=None, view=self)
            except (discord.NotFound, discord.HTTPException):
                pass


class TowerGameView(discord.ui.View):
    """Tower 遊戲主介面：5 層按鈕恆顯 + 結束按鈕（第一層右邊）"""

    def __init__(self, cog: "MiniGamesCog", game: TowerGame):
        super().__init__(timeout=120)
        self.cog = cog
        self.game = game
        self.message = None
        self._build_buttons()

    def _build_buttons(self):
        self.clear_items()
        g = self.game
        safe = g.safe_level()
        current = g.current_level
        awaiting = g.awaiting_continue

        # 建立 5 層（L5 在上、L1 在下），每層 3 格，L1 右側加結束按鈕
        for level in range(TOWER_LEVELS, 0, -1):
            row_idx = TOWER_LEVELS - level
            for i in range(TOWER_TILES_PER_LEVEL):
                btn = discord.ui.Button(
                    label=EMOJI_SAFE,
                    custom_id=f"tile_{level}_{i}",
                    style=discord.ButtonStyle.primary,
                    row=row_idx,
                )
                self._set_tile_state(btn, level, i, safe, current, awaiting)
                self.add_item(btn)
            if level == 1:
                end_btn = discord.ui.Button(
                    label="💰 結束",
                    custom_id="tower_end",
                    style=discord.ButtonStyle.success,
                    row=row_idx,
                )
                self.add_item(end_btn)

        self._attach_callbacks()

    def _set_tile_state(self, btn: discord.ui.Button, level: int, tile_idx: int,
                        safe: int, current: int, awaiting: bool):
        """依狀態設定 label、style 與 disabled"""
        g = self.game
        is_cactus_tile = g.grid[level - 1][tile_idx] == 1

        # 遊戲結束：揭露全部仙人掌
        if g.game_over_reveal_all:
            btn.disabled = True
            if is_cactus_tile:
                btn.label = EMOJI_CACTUS
                if g.game_over_cactus:
                    btn.style = discord.ButtonStyle.danger
            else:
                if level in g.picked_per_level:
                    picked_idx, _ = g.picked_per_level[level]
                    btn.label = EMOJI_REVEALED_SAFE if tile_idx == picked_idx else EMOJI_SAFE
                else:
                    btn.label = EMOJI_SAFE
            return

        # 已通過的層：揭露仙人掌位置（安全格 ✅、仙人掌 🌵）
        if level in g.picked_per_level:
            picked_idx, hit_cactus = g.picked_per_level[level]
            btn.disabled = True
            if is_cactus_tile:
                btn.label = EMOJI_CACTUS
            elif tile_idx == picked_idx:
                btn.label = EMOJI_REVEALED_SAFE
            else:
                btn.label = EMOJI_REVEALED_SAFE
            return

        if awaiting:
            if level == current + 1:
                btn.label = EMOJI_SAFE
                btn.disabled = False
            else:
                btn.label = EMOJI_SAFE
                btn.disabled = True
        else:
            if level == current:
                btn.label = EMOJI_SAFE
                btn.disabled = False
            else:
                btn.label = EMOJI_SAFE
                btn.disabled = True

    def _attach_callbacks(self):
        async def on_tile_click(interaction: discord.Interaction, button: discord.ui.Button):
            parts = button.custom_id.split("_")
            if parts[0] == "tile":
                level = int(parts[1])
                tile_idx = int(parts[2])
                await self.cog.tower_pick_tile(interaction, self.game, level, tile_idx)
            elif button.custom_id == "tower_end":
                await self.cog.tower_cashout(interaction, self.game)

        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.callback = lambda i, b=child: on_tile_click(i, b)

    async def on_timeout(self):
        if self.game.active_view is not self:
            return
        self.game.active_view = None
        key = (self.game.channel_id, self.game.user_id)
        self.cog.tower_games.pop(key, None)
        for child in self.children:
            child.disabled = True
        if self.game.message is not None:
            try:
                await self.game.message.edit(content="遊戲已逾時。", embed=None, view=self)
            except (discord.NotFound, discord.HTTPException):
                pass
        self.stop()


class TableView(discord.ui.View):
    def __init__(self, cog: "MiniGamesCog", game: Game):
        super().__init__(timeout=600)
        self.cog = cog
        self.game = game
        self.message = None

    @discord.ui.button(label="🂠 我的手牌", style=discord.ButtonStyle.primary)
    async def myhand_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.game.started:
            return await interaction.response.send_message("遊戲尚未開始。", ephemeral=True)
        if not any(p.user_id == interaction.user.id for p in self.game.players):
            return await interaction.response.send_message("你不在這桌。", ephemeral=True)

        player = self.game.find_player(interaction.user.id)
        if player.finished:
            return await interaction.response.send_message("你已經出完牌了。", ephemeral=True)
        if self.game.is_game_over():
            return await interaction.response.send_message("遊戲已結束。", ephemeral=True)

        view = HandView(self.cog, self.game, player.user_id)
        view.build_options(player.hand)

        embed = discord.Embed(
            title="🂠 你的手牌",
            description=" ".join(map(str, player.hand)),
            color=discord.Color.gold(),
        )
        embed.set_footer(text=f"共 {len(player.hand)} 張｜用下拉選牌後按「出牌」或直接「Pass」。")
        await interaction.response.send_message(embed=embed, ephemeral=True, view=view)
        sent = await interaction.original_response()
        view.message = sent

    @discord.ui.button(label="🛑 結束（房主）", style=discord.ButtonStyle.danger)
    async def end_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.game.owner_id:
            return await interaction.response.send_message("只有房主可以結束。", ephemeral=True)
        self.cog.games.pop(self.game.channel_id, None)
        await interaction.response.edit_message(content="此局已結束。", embed=None, view=None)
        self.stop()

    async def on_timeout(self):
        if self.game.active_view is not self:
            return
        self.game.active_view = None
        # timeout=None，理論上不會觸發；若將來改為有 timeout 則停用按鈕
        for child in self.children:
            child.disabled = True
        if self.game.lobby_message is not None:
            try:
                await self.game.lobby_message.edit(content="遊戲因超時而結束。", view=self)
                self.cog.games.pop(self.game.channel_id, None)
            except (discord.NotFound, discord.HTTPException):
                pass
        self.stop()


class HandView(discord.ui.View):
    def __init__(self, cog: "MiniGamesCog", game: Game, player_id: int):
        super().__init__(timeout=60)
        self.cog = cog
        self.game = game
        self.player_id = player_id
        self.selected: List[str] = []
        self.message = None

        self.select = discord.ui.Select(
            placeholder="選牌（1/2/3/5張，最多 5）",
            min_values=1,
            max_values=5,
            options=[]
        )
        self.select.callback = self.on_select
        self.add_item(self.select)

    def build_options(self, cards: List[Card]):
        # 用數字索引當 value，避免 Discord 回傳時 ♦ 等符號編碼跑掉導致對不到 3♦
        # Discord Select 規定至少 5 個選項，不足時用佔位項補滿（parse 時會略過）
        self.select.options = [
            discord.SelectOption(label=str(c), value=f"{r_value(c.rank)}|{s_value(c.suit)}")
            for c in cards
        ]
        while len(self.select.options) < 5:
            i = len(self.select.options)
            self.select.options.append(
                discord.SelectOption(label="—", value=f"pad|{i}")
            )

    async def on_select(self, interaction: discord.Interaction):
        self.selected = self.select.values
        await interaction.response.defer(ephemeral=True)

    def parse_selected_cards(self, player: PlayerState) -> List[Card]:
        chosen: List[Card] = []
        for v in self.selected:
            try:
                ri, si = v.strip().split("|")
                if ri == "pad":
                    continue  # 佔位選項，略過
                rank, suit = RANKS[int(ri)], SUITS[int(si)]
            except (ValueError, IndexError):
                continue
            for c in player.hand:
                if c.rank == rank and c.suit == suit:
                    chosen.append(c)
                    break
        return chosen

    async def _edit_ephemeral_result(self, interaction: discord.Interaction, text: str, is_error: bool = False):
        """用 edit_original_response 編輯本次互動的 ephemeral 回覆。"""
        embed = discord.Embed(
            description=text,
            color=discord.Color.red() if is_error else discord.Color.green(),
        )
        try:
            await interaction.edit_original_response(content=None, embed=embed, view=None)
        except (discord.NotFound, discord.HTTPException):
            await interaction.followup.send(text, ephemeral=True)

    @discord.ui.button(label="出牌", style=discord.ButtonStyle.success)
    async def play_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        async with self.game.lock:
            if interaction.user.id != self.player_id:
                return await self._edit_ephemeral_result(interaction, "這不是你的介面。", is_error=True)

            if self.game.current_player().user_id != self.player_id:
                return await self._edit_ephemeral_result(interaction, "還沒輪到你。", is_error=True)

            player = self.game.find_player(self.player_id)

            chosen = self.parse_selected_cards(player)
            if not chosen:
                return await self._edit_ephemeral_result(interaction, "你還沒選牌。", is_error=True)

            if not legal_size(chosen):
                return await self._edit_ephemeral_result(interaction, "一次只能出 1 / 2 / 3 / 5 張。", is_error=True)

            if not must_follow_table(self.game.table_cards, chosen):
                return await self._edit_ephemeral_result(interaction, "必須跟桌面相同張數才能壓。", is_error=True)

            # validate shape
            try:
                _ = hand_signature(chosen, self.game.rules)
            except ValueError as e:
                return await self._edit_ephemeral_result(interaction, f"牌型不合法：{e}", is_error=True)

            ok, reason = is_first_move_requires_3d(self.game, chosen)
            if not ok:
                return await self._edit_ephemeral_result(interaction, reason, is_error=True)

            # beat check
            try:
                if not beats(self.game.table_cards, chosen, self.game.rules):
                    return await self._edit_ephemeral_result(interaction, "你出的牌沒有壓過桌面。", is_error=True)
            except ValueError as e:
                return await self._edit_ephemeral_result(interaction, f"比較失敗：{e}", is_error=True)

            # apply play
            for c in chosen:
                player.hand.remove(c)

            player.passed = False
            self.game.table_cards = sort_cards(chosen)
            self.game.table_owner = player.user_id
            self.game.first_trick = False

            if len(player.hand) == 0:
                player.finished = True
                self.game.finish_order.append(player.user_id)

            # after a valid play, next turn continues（會跳過已出完的人）
            self.game.next_turn()
            self.game.ensure_turn_alive()
            if self.game.is_game_over() and len(self.game.finish_order) < len(self.game.players):
                for p in self.game.players:
                    if not p.finished:
                        self.game.finish_order.append(p.user_id)
                        break

            await self._edit_ephemeral_result(interaction, f"✅ 你出了：{' '.join(map(str, chosen))}")
            await self.cog.update_table_message(interaction.channel, self.game)
            self.stop()

    @discord.ui.button(label="Pass", style=discord.ButtonStyle.secondary)
    async def pass_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        async with self.game.lock:
            if interaction.user.id != self.player_id:
                return await self._edit_ephemeral_result(interaction, "這不是你的介面。", is_error=True)

            if self.game.current_player().user_id != self.player_id:
                return await self._edit_ephemeral_result(interaction, "還沒輪到你。", is_error=True)

            player = self.game.find_player(self.player_id)
            ok, reason = can_pass(self.game, player)
            if not ok:
                return await self._edit_ephemeral_result(interaction, reason, is_error=True)

            player.passed = True

            # If everyone else passed, reset trick and return to table_owner
            if self.game.table_owner is not None:
                if self.game.trick_active_count() > 0 and self.game.trick_passed_count() >= self.game.trick_active_count():
                    owner_idx = self.game.index_of(self.game.table_owner)
                    self.game.reset_trick()
                    if owner_idx >= 0:
                        self.game.turn_index = owner_idx
                    self.game.ensure_turn_alive()
                    if self.game.is_game_over() and len(self.game.finish_order) < len(self.game.players):
                        for p in self.game.players:
                            if not p.finished:
                                self.game.finish_order.append(p.user_id)
                                break
                    await self._edit_ephemeral_result(
                        interaction, "所有人都 Pass，清空桌面，回到上一位出牌者領出。"
                    )
                    await self.cog.update_table_message(interaction.channel, self.game)
                    self.stop()
                    return

            self.game.next_turn()
            self.game.ensure_turn_alive()
            if self.game.is_game_over() and len(self.game.finish_order) < len(self.game.players):
                for p in self.game.players:
                    if not p.finished:
                        self.game.finish_order.append(p.user_id)
                        break
            await self._edit_ephemeral_result(interaction, "你選擇 Pass。")
            await self.cog.update_table_message(interaction.channel, self.game)
            self.stop()

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(content="選擇已逾時。", view=self)
            except (discord.NotFound, discord.HTTPException):
                pass


# -----------------------------
# Dice / Coinflip / Scratchcard / Slots / HighLow / Blackjack Views
# -----------------------------


class DiceChoiceView(discord.ui.View):
    def __init__(self, cog: "MiniGamesCog", user_id: int, guild_id: int, bet: float):
        super().__init__(timeout=60)
        self.cog = cog
        self.user_id = user_id
        self.guild_id = guild_id
        self.bet = bet
        self.message = None
        self.resolved = False
        self.lock = asyncio.Lock()

        for guess, face in enumerate(("⚀", "⚁", "⚂", "⚃", "⚄", "⚅"), start=1):
            button = discord.ui.Button(
                label=f"{face} {guess}",
                style=discord.ButtonStyle.primary,
                row=0 if guess <= 3 else 1,
            )

            async def callback(interaction: discord.Interaction, value: int = guess):
                await self.cog.play_dice_choice(interaction, self, value)

            button.callback = callback
            self.add_item(button)

    async def on_timeout(self):
        if self.resolved:
            return
        for child in self.children:
            child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except (discord.NotFound, discord.HTTPException):
                pass
        self.stop()


class CoinflipChoiceView(discord.ui.View):
    def __init__(self, cog: "MiniGamesCog", user_id: int, guild_id: int, bet: float):
        super().__init__(timeout=60)
        self.cog = cog
        self.user_id = user_id
        self.guild_id = guild_id
        self.bet = bet
        self.message = None
        self.resolved = False
        self.lock = asyncio.Lock()

    @discord.ui.button(label="正面", emoji="🪙", style=discord.ButtonStyle.primary)
    async def heads_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.play_coinflip_choice(interaction, self, "heads")

    @discord.ui.button(label="反面", emoji="🌙", style=discord.ButtonStyle.primary)
    async def tails_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.play_coinflip_choice(interaction, self, "tails")

    async def on_timeout(self):
        if self.resolved:
            return
        for child in self.children:
            child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except (discord.NotFound, discord.HTTPException):
                pass
        self.stop()


class BetAgainView(discord.ui.View):
    def __init__(self, cog: "MiniGamesCog", game_type: str, user_id: int, guild_id: int, bet: float):
        super().__init__(timeout=60)
        self.cog = cog
        self.game_type = game_type
        self.user_id = user_id
        self.guild_id = guild_id
        self.bet = bet
        self.message = None

    @discord.ui.button(label="再賭一次", emoji="🔁", style=discord.ButtonStyle.success)
    async def again_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("這不是你的遊戲。", ephemeral=True)
        if self.game_type == "dice":
            await self.cog.show_dice_choices(interaction, self.guild_id, self.bet, edit=True)
        else:
            await self.cog.show_coinflip_choices(interaction, self.guild_id, self.bet, edit=True)
        self.stop()

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except (discord.NotFound, discord.HTTPException):
                pass
        self.stop()


class ScratchcardView(discord.ui.View):
    def __init__(self, cog: "MiniGamesCog", game: ScratchcardGame):
        super().__init__(timeout=90)
        self.cog = cog
        self.game = game
        self.message = None

    @discord.ui.button(label="🪙 刮開", style=discord.ButtonStyle.primary)
    async def reveal_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.settle_scratchcard(interaction, self.game, self)

    async def on_timeout(self):
        if self.game.active_view is not self:
            return
        await self.cog.scratchcard_timeout(self.game, self)
        self.stop()


class SlotsAgainView(discord.ui.View):
    """拉霸結果附「再轉一次」按鈕"""

    def __init__(self, cog: "MiniGamesCog", user_id: int, guild_id: int, bet: float):
        super().__init__(timeout=60)
        self.cog = cog
        self.user_id = user_id
        self.guild_id = guild_id
        self.bet = bet
        self.message = None

    @discord.ui.button(label="🔁 再轉一次", style=discord.ButtonStyle.primary)
    async def again_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("這不是你的遊戲。", ephemeral=True)
        button.disabled = True
        await self.cog.do_slots_spin(interaction, self.guild_id, self.bet, edit=True)
        self.stop()

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except (discord.NotFound, discord.HTTPException):
                pass


class HighLowView(discord.ui.View):
    def __init__(self, cog: "MiniGamesCog", game: HighLowGame):
        super().__init__(timeout=120)
        self.cog = cog
        self.game = game
        self.message = None
        p_high, p_low = hl_probs(game.current_rank)
        self.high_btn.disabled = p_high <= 0
        self.low_btn.disabled = p_low <= 0
        self.cashout_btn.disabled = game.streak < 1

    @discord.ui.button(label="🔼 較大", style=discord.ButtonStyle.primary)
    async def high_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.highlow_guess(interaction, self.game, "high", self)

    @discord.ui.button(label="🔽 較小", style=discord.ButtonStyle.primary)
    async def low_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.highlow_guess(interaction, self.game, "low", self)

    @discord.ui.button(label="💰 提現", style=discord.ButtonStyle.success)
    async def cashout_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.highlow_cashout(interaction, self.game, self)

    async def on_timeout(self):
        if self.game.active_view is not self:
            return
        self.game.active_view = None
        await self.cog.highlow_timeout(self.game)
        self.stop()


class BlackjackView(discord.ui.View):
    def __init__(self, cog: "MiniGamesCog", game: BlackjackGame):
        super().__init__(timeout=120)
        self.cog = cog
        self.game = game
        self.message = None
        # Double 僅首兩張可用
        self.double_btn.disabled = len(game.player_hand) != 2

    @discord.ui.button(label="🎯 要牌 Hit", style=discord.ButtonStyle.primary)
    async def hit_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.bj_hit(interaction, self.game, self)

    @discord.ui.button(label="✋ 停牌 Stand", style=discord.ButtonStyle.success)
    async def stand_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.bj_stand(interaction, self.game, self)

    @discord.ui.button(label="⏫ 加倍 Double", style=discord.ButtonStyle.danger)
    async def double_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.bj_double(interaction, self.game, self)

    async def on_timeout(self):
        if self.game.active_view is not self:
            return
        self.game.active_view = None
        await self.cog.bj_timeout(self.game)
        self.stop()


# -----------------------------
# Cog
# -----------------------------

@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
class MiniGamesCog(commands.GroupCog, group_name="games", description="迷你遊戲"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.games: Dict[int, Game] = {}
        self.tower_games: Dict[Tuple[int, int], TowerGame] = {}  # (channel_id, user_id) -> TowerGame
        self.highlow_games: Dict[Tuple[int, int], HighLowGame] = {}
        self.blackjack_games: Dict[Tuple[int, int], BlackjackGame] = {}
        self.scratchcard_games: Dict[Tuple[int, int], ScratchcardGame] = {}
        self.slots_spinning: set = set()  # (channel_id, user_id) 轉動中防連點
        self.lottery_locks: Dict[int, asyncio.Lock] = {}

    async def cog_load(self):
        # 模組由 asyncio.run(add_cog) 載入；只有 bot 已在正式 loop ready 時才在這裡啟動。
        if self.bot.is_ready() and not self.lottery_draw_task.is_running():
            self.lottery_draw_task.start()

    async def cog_unload(self):
        if self.lottery_draw_task.is_running():
            self.lottery_draw_task.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.lottery_draw_task.is_running():
            self.lottery_draw_task.start()

    async def _edit_game_message(
        self,
        game: Game,
        message: discord.Message,
        *,
        content: Any = discord.utils.MISSING,
        embed: Any = discord.utils.MISSING,
        view: Optional[discord.ui.View],
    ) -> None:
        old_view = game.active_view
        edit_kwargs: Dict[str, Any] = {"view": view}
        if content is not discord.utils.MISSING:
            edit_kwargs["content"] = content
        if embed is not discord.utils.MISSING:
            edit_kwargs["embed"] = embed
        await message.edit(**edit_kwargs)
        game.lobby_message = message
        game.lobby_message_id = message.id
        game.active_view = view
        if view is not None and hasattr(view, "message"):
            view.message = message
        if old_view is not None and old_view is not view:
            old_view.stop()

    def _resolve_audit_user(self, guild_id: int, user_id: int):
        if guild_id != GLOBAL_GUILD_ID:
            guild = self.bot.get_guild(guild_id)
            if guild is not None:
                member = guild.get_member(user_id)
                if member is not None:
                    return member
        return self.bot.get_user(user_id)

    def _log_economy_history(self, guild_id: int, user_id: int, tx_type: str, amount: float, detail: str = "") -> str:
        currency = get_currency_name(guild_id)
        log_transaction(guild_id, user_id, tx_type, amount, currency, detail)
        return currency

    def _charge_bet(
        self,
        interaction: discord.Interaction,
        guild_id: int,
        bet: float,
        tx_label: str,
        audit_event: str,
        detail: str,
    ) -> bool:
        """扣注 + 記錄。回傳是否成功（餘額不足回 False）。"""
        currency = get_currency_name(guild_id)
        balance_before = get_balance(guild_id, interaction.user.id)
        if balance_before < bet or not remove_balance(guild_id, interaction.user.id, bet):
            return False
        balance_after = get_balance(guild_id, interaction.user.id)
        self._log_economy_history(guild_id, interaction.user.id, tx_label, -bet, detail)
        queue_economy_audit_log(
            audit_event,
            guild_id=guild_id,
            actor=interaction.user,
            interaction=interaction,
            currency=currency,
            amount=bet,
            balance_before=balance_before,
            balance_after=balance_after,
            detail=detail,
            color=0xE67E22,
        )
        if guild_id != GLOBAL_GUILD_ID:
            record_transaction(guild_id)
        return True

    def _pay_out(
        self,
        guild_id: int,
        user_id: int,
        amount: float,
        tx_label: str,
        audit_event: str,
        detail: str,
        interaction: Optional[discord.Interaction] = None,
        color: int = 0x2ECC71,
    ):
        """發放獎金 + 記錄。"""
        if amount <= 0:
            return
        currency = get_currency_name(guild_id)
        balance_before = get_balance(guild_id, user_id)
        add_balance(guild_id, user_id, amount)
        balance_after = get_balance(guild_id, user_id)
        self._log_economy_history(guild_id, user_id, tx_label, amount, detail)
        queue_economy_audit_log(
            audit_event,
            guild_id=guild_id,
            actor=interaction.user if interaction else self._resolve_audit_user(guild_id, user_id),
            interaction=interaction,
            currency=currency,
            amount=amount,
            balance_before=balance_before,
            balance_after=balance_after,
            detail=detail,
            color=color,
        )
        if guild_id != GLOBAL_GUILD_ID:
            record_transaction(guild_id)

    @staticmethod
    def _validate_bet(bet: int) -> Optional[str]:
        if bet < BET_MIN or bet > BET_MAX:
            return f"❌ 賭注金額需介於 **{BET_MIN}**～**{BET_MAX}** 之間。"
        return None

    @staticmethod
    def _roulette_result_label(number: int) -> str:
        color = roulette_color(number)
        if color == "green":
            return f"🟢 **{number}**"
        if color == "red":
            return f"🔴 **{number}**"
        return f"⚫ **{number}**"

    def _lottery_lock(self, guild_id: int) -> asyncio.Lock:
        lock = self.lottery_locks.get(guild_id)
        if lock is None:
            lock = asyncio.Lock()
            self.lottery_locks[guild_id] = lock
        return lock

    @staticmethod
    def _load_lottery_state(guild_id: int) -> Dict[str, Any]:
        return normalize_lottery_state(get_server_config(guild_id, LOTTERY_CONFIG_KEY, {}))

    @staticmethod
    def _save_lottery_state(guild_id: int, state: Dict[str, Any]) -> bool:
        return set_server_config(guild_id, LOTTERY_CONFIG_KEY, state)

    @staticmethod
    def _lottery_payout_recorded(guild_id: int, user_id: int, round_id: str) -> bool:
        history = get_user_data(guild_id, user_id, "economy_history", []) or []
        marker = f"彩票輪次 {round_id}"
        return any(
            isinstance(entry, dict)
            and entry.get("type") == "彩票派彩"
            and marker in str(entry.get("detail", ""))
            for entry in history
        )

    def _lottery_status_embed(self, guild_id: int, state: Dict[str, Any]) -> discord.Embed:
        currency = get_currency_name(guild_id)
        jackpot = float(state.get("jackpot", 0.0) or 0.0)
        tickets = state.get("tickets", {}) or {}
        unique_users = {
            user_id
            for entries in tickets.values()
            if isinstance(entries, dict)
            for user_id in entries
        }
        current_stake = sum(
            float(stake)
            for entries in tickets.values()
            if isinstance(entries, dict)
            for stake in entries.values()
        )

        pending = state.get("pending_settlement")
        draw_at = parse_lottery_draw_at(state.get("draw_at"))
        if pending:
            timing = "本輪正在結算，暫停購票。"
        elif draw_at is not None:
            timing = f"開獎時間：<t:{int(draw_at.timestamp())}:F>（<t:{int(draw_at.timestamp())}:R>）"
        elif jackpot > 0:
            timing = "累積獎池等待下一張票；購票後 60 分鐘開獎。"
        else:
            timing = "尚未開始；第一張票售出後 60 分鐘開獎。"

        embed = discord.Embed(
            title="🎟️ 累積彩票",
            description=(
                f"目前獎池：**{jackpot:,.2f}** {currency}\n"
                f"本輪新增投注：**{current_stake:,.2f}** {currency}\n"
                f"參與玩家：**{len(unique_users)}** 人\n{timing}"
            ),
            color=discord.Color.gold(),
        )
        last_result = state.get("last_result")
        if isinstance(last_result, dict):
            number = str(last_result.get("number", "??")).zfill(2)
            winner_count = int(last_result.get("winner_count", 0) or 0)
            draw_jackpot = float(last_result.get("jackpot", 0.0) or 0.0)
            payout_total = float(last_result.get("payout_total", 0.0) or 0.0)
            if winner_count:
                result_line = (
                    f"中獎號碼：**{number}**\n中獎人數：**{winner_count}**\n"
                    f"派彩總額：**{payout_total:,.2f}** {currency}"
                )
            else:
                result_line = (
                    f"中獎號碼：**{number}**\n無人命中，"
                    f"**{draw_jackpot:,.2f}** {currency} 已累積至下一輪。"
                )
            drawn_at = parse_lottery_draw_at(last_result.get("drawn_at"))
            if drawn_at is not None:
                result_line += f"\n開獎時間：<t:{int(drawn_at.timestamp())}:f>"
            embed.add_field(name="上期結果", value=result_line, inline=False)
        embed.set_footer(text="票券每輪到期；若無人命中，只累積獎池，不保留舊票。")
        return embed

    async def _notify_lottery_winner(
        self,
        guild_id: int,
        user_id: int,
        number: str,
        payout: float,
        round_id: str,
    ) -> None:
        currency = get_currency_name(guild_id)
        try:
            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
            embed = discord.Embed(
                title="🎉 彩票中獎！",
                description=(
                    f"中獎號碼：**{number}**\n"
                    f"你的派彩：**{payout:,.2f}** {currency}\n"
                    f"目前餘額：**{get_balance(guild_id, user_id):,.2f}** {currency}"
                ),
                color=discord.Color.gold(),
            )
            embed.set_footer(text=f"輪次：{round_id}")
            await user.send(embed=embed)
        except (discord.Forbidden, discord.NotFound, discord.HTTPException, AttributeError) as exc:
            log(
                f"Failed to DM lottery winner {user_id} for round {round_id}: {exc}",
                module_name="MiniGames",
                level=logging.WARNING,
            )

    async def _resume_lottery_settlement_locked(self, guild_id: int, state: Dict[str, Any]) -> None:
        pending = state.get("pending_settlement")
        if not isinstance(pending, dict):
            return
        round_id = str(pending.get("round_id", ""))
        number = str(pending.get("number", "??")).zfill(2)
        raw_payouts = pending.get("payouts", {})
        if not round_id or not isinstance(raw_payouts, dict):
            log(
                f"Discarding malformed lottery settlement in scope {guild_id}.",
                module_name="MiniGames",
                level=logging.ERROR,
            )
            state["pending_settlement"] = None
            self._save_lottery_state(guild_id, state)
            return

        paid_user_ids = {str(user_id) for user_id in pending.get("paid_user_ids", [])}
        currency = get_currency_name(guild_id)
        for raw_user_id, raw_payout in sorted(raw_payouts.items(), key=lambda item: str(item[0])):
            try:
                user_id = int(raw_user_id)
                payout = round(float(raw_payout), 2)
            except (TypeError, ValueError):
                continue
            if payout <= 0:
                continue
            user_key = str(user_id)
            if user_key not in paid_user_ids and self._lottery_payout_recorded(guild_id, user_id, round_id):
                paid_user_ids.add(user_key)
            if user_key in paid_user_ids:
                continue
            try:
                self._pay_out(
                    guild_id,
                    user_id,
                    payout,
                    "彩票派彩",
                    "lottery_payout",
                    f"彩票輪次 {round_id}，中獎號碼 {number}，派彩 {payout:,.2f} {currency}",
                )
            except Exception as exc:
                log(
                    f"Lottery payout failed in scope {guild_id}, round {round_id}, user {user_id}: {exc}",
                    module_name="MiniGames",
                    level=logging.ERROR,
                )
                return
            paid_user_ids.add(user_key)
            pending["paid_user_ids"] = sorted(paid_user_ids, key=int)
            state["pending_settlement"] = pending
            if not self._save_lottery_state(guild_id, state):
                log(
                    f"Failed to persist lottery payout progress in scope {guild_id}, round {round_id}.",
                    module_name="MiniGames",
                    level=logging.ERROR,
                )
                return
            asyncio.create_task(self._notify_lottery_winner(guild_id, user_id, number, payout, round_id))

        state["last_result"] = pending.get("last_result")
        state["pending_settlement"] = None
        if not self._save_lottery_state(guild_id, state):
            log(
                f"Failed to finalize lottery settlement in scope {guild_id}, round {round_id}.",
                module_name="MiniGames",
                level=logging.ERROR,
            )

    async def _settle_lottery_scope(self, guild_id: int) -> None:
        async with self._lottery_lock(guild_id):
            state = self._load_lottery_state(guild_id)
            if state.get("pending_settlement"):
                await self._resume_lottery_settlement_locked(guild_id, state)
                return

            draw_at = parse_lottery_draw_at(state.get("draw_at"))
            if draw_at is None or draw_at > datetime.now(timezone.utc):
                return

            tickets = state.get("tickets", {}) or {}
            round_id = str(state.get("round_id") or uuid.uuid4().hex)
            jackpot = round(float(state.get("jackpot", 0.0) or 0.0), 2)
            drawn_at = datetime.now(timezone.utc)
            winning_number = secrets.randbelow(100)
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
                    "last_result": {
                        "round_id": round_id,
                        "number": number_key,
                        "jackpot": jackpot,
                        "payout_total": 0.0,
                        "winner_count": 0,
                        "rolled_over": True,
                        "drawn_at": drawn_at.isoformat(),
                    },
                })
                if self._save_lottery_state(guild_id, state):
                    log(
                        f"Lottery round {round_id} in scope {guild_id} drew {number_key}; no winners, jackpot rolled over.",
                        module_name="MiniGames",
                        level=logging.INFO,
                    )
                return

            payouts = allocate_lottery_payouts(jackpot, winning_stakes)
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
                    "drawn_at": drawn_at.isoformat(),
                },
            }
            state.update({
                "jackpot": 0.0,
                "draw_at": None,
                "round_id": None,
                "tickets": {},
                "pending_settlement": pending,
            })
            if not self._save_lottery_state(guild_id, state):
                log(
                    f"Failed to persist lottery settlement in scope {guild_id}, round {round_id}.",
                    module_name="MiniGames",
                    level=logging.ERROR,
                )
                return
            await self._resume_lottery_settlement_locked(guild_id, state)

    @tasks.loop(minutes=1)
    async def lottery_draw_task(self):
        try:
            states = get_all_server_config_key(LOTTERY_CONFIG_KEY) or {}
        except Exception as exc:
            log(
                f"Failed to load lottery states: {exc}",
                module_name="MiniGames",
                level=logging.ERROR,
            )
            return
        now = datetime.now(timezone.utc)
        for raw_guild_id, raw_state in states.items():
            try:
                guild_id = int(raw_guild_id)
            except (TypeError, ValueError):
                continue
            state = normalize_lottery_state(raw_state)
            draw_at = parse_lottery_draw_at(state.get("draw_at"))
            if state.get("pending_settlement") or (draw_at is not None and draw_at <= now):
                try:
                    await self._settle_lottery_scope(guild_id)
                except Exception as exc:
                    log(
                        f"Lottery draw failed in scope {guild_id}: {exc}",
                        module_name="MiniGames",
                        level=logging.ERROR,
                    )

    @lottery_draw_task.before_loop
    async def before_lottery_draw_task(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="roulette", description="俄羅斯輪盤：選擇投注方式並下注")
    @app_commands.describe(
        bet="賭注金額（50～2000）",
        bet_type="投注方式",
        number="單號投注時選擇 0～36",
        use_global="是否使用全域幣（預設依伺服器設定）",
    )
    @app_commands.choices(bet_type=[
        app_commands.Choice(name="紅色", value="red"),
        app_commands.Choice(name="黑色", value="black"),
        app_commands.Choice(name="單數", value="odd"),
        app_commands.Choice(name="雙數", value="even"),
        app_commands.Choice(name="小（1～18）", value="low"),
        app_commands.Choice(name="大（19～36）", value="high"),
        app_commands.Choice(name="單一號碼", value="number"),
    ])
    async def roulette(
        self,
        interaction: discord.Interaction,
        bet: int,
        bet_type: app_commands.Choice[str],
        number: Optional[app_commands.Range[int, 0, 36]] = None,
        use_global: bool = False,
    ):
        err = self._validate_bet(bet)
        if err:
            return await interaction.response.send_message(err, ephemeral=True)
        if bet_type.value == "number" and number is None:
            return await interaction.response.send_message("❌ 單號投注必須選擇 0～36 的號碼。", ephemeral=True)
        if bet_type.value != "number" and number is not None:
            return await interaction.response.send_message("❌ 只有單號投注需要填寫號碼。", ephemeral=True)

        guild_id = resolve_game_guild_id(interaction, use_global)
        currency = get_currency_name(guild_id)
        if not self._charge_bet(
            interaction,
            guild_id,
            float(bet),
            "輪盤下注",
            "roulette_bet",
            f"輪盤 {ROULETTE_BET_LABELS[bet_type.value]} 下注 {bet:,.0f} {currency}",
        ):
            balance = get_balance(guild_id, interaction.user.id)
            return await interaction.response.send_message(
                f"❌ 餘額不足！\n你的餘額：**{balance:,.0f}** {currency}\n所需賭注：**{bet:,.0f}** {currency}",
                ephemeral=True,
            )

        result = random.randint(0, 36)
        won = roulette_is_win(result, bet_type.value, int(number) if number is not None else None)
        multiplier = 36.0 if bet_type.value == "number" else 2.0
        payout = round(bet * multiplier, 2) if won else 0.0
        selection = f"號碼 {int(number)}" if bet_type.value == "number" else ROULETTE_BET_LABELS[bet_type.value]
        if payout:
            self._pay_out(
                guild_id,
                interaction.user.id,
                payout,
                "輪盤派彩",
                "roulette_payout",
                f"投注 {selection}，開出 {result}，倍率 x{multiplier:.0f}",
                interaction=interaction,
            )

        spinning = discord.Embed(
            title="🎡 俄羅斯輪盤",
            description="輪盤轉動中……",
            color=discord.Color.blurple(),
        )
        spinning.set_footer(text=f"投注：{selection}｜下注：{bet:,.0f} {currency}")
        await interaction.response.send_message(embed=spinning)
        message = await interaction.original_response()
        await asyncio.sleep(1.5)

        profit = payout - bet
        if won:
            result_text = (
                f"開出 {self._roulette_result_label(result)}\n\n🎉 **你贏了！**\n"
                f"派彩：**{payout:,.2f}** {currency}（+{profit:,.2f}）"
            )
            color = discord.Color.green()
        else:
            result_text = (
                f"開出 {self._roulette_result_label(result)}\n\n💥 **未中獎**，"
                f"損失 **{bet:,.0f}** {currency}"
            )
            color = discord.Color.red()
        result_text += f"\n新餘額：**{get_balance(guild_id, interaction.user.id):,.2f}** {currency}"
        embed = discord.Embed(title="🎡 俄羅斯輪盤", description=result_text, color=color)
        embed.set_footer(text=f"投注：{selection}｜下注：{bet:,.0f} {currency}")
        try:
            await message.edit(embed=embed)
        except (discord.NotFound, discord.HTTPException):
            pass

    @app_commands.command(name="dice", description="骰子遊戲：猜中 1～6 的點數")
    @app_commands.describe(bet="賭注金額（50～2000）")
    async def dice(self, interaction: discord.Interaction, bet: int):
        err = self._validate_bet(bet)
        if err:
            return await interaction.response.send_message(err, ephemeral=True)
        guild_id = resolve_game_guild_id(interaction)
        await self.show_dice_choices(interaction, guild_id, float(bet), edit=False)

    async def show_dice_choices(
        self,
        interaction: discord.Interaction,
        guild_id: int,
        bet: float,
        edit: bool,
    ):
        currency = get_currency_name(guild_id)
        embed = discord.Embed(
            title="🎲 骰子遊戲",
            description="選擇你要猜的骰子點數。",
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"下注：{bet:,.0f} {currency}｜按下點數後才會扣款")
        view = DiceChoiceView(self, interaction.user.id, guild_id, bet)
        if edit:
            await interaction.response.edit_message(embed=embed, view=view)
        else:
            await interaction.response.send_message(embed=embed, view=view)
        view.message = await interaction.original_response()

    async def play_dice_choice(
        self,
        interaction: discord.Interaction,
        view: DiceChoiceView,
        guess: int,
    ):
        if interaction.user.id != view.user_id:
            return await interaction.response.send_message("這不是你的遊戲。", ephemeral=True)
        async with view.lock:
            if view.resolved:
                return await interaction.response.send_message("這局已經結算。", ephemeral=True)
            currency = get_currency_name(view.guild_id)
            if not self._charge_bet(
                interaction,
                view.guild_id,
                view.bet,
                "骰子下注",
                "dice_bet",
                f"猜 {guess}，下注 {view.bet:,.0f} {currency}",
            ):
                balance = get_balance(view.guild_id, interaction.user.id)
                return await interaction.response.send_message(
                    f"❌ 餘額不足！\n你的餘額：**{balance:,.0f}** {currency}\n所需賭注：**{view.bet:,.0f}** {currency}",
                    ephemeral=True,
                )

            result = random.randint(1, 6)
            won = result == guess
            payout = round(view.bet * 5.7, 2) if won else 0.0
            if payout:
                self._pay_out(
                    view.guild_id,
                    interaction.user.id,
                    payout,
                    "骰子派彩",
                    "dice_payout",
                    f"猜中點數 {result}，倍率 x5.70",
                    interaction=interaction,
                )
            view.resolved = True
            view.stop()

        await interaction.response.edit_message(
            embed=discord.Embed(title="🎲 骰子遊戲", description="骰子滾動中……", color=discord.Color.blurple()),
            view=None,
        )
        message = await interaction.original_response()

        await asyncio.sleep(1.0)
        if won:
            text = (
                f"你猜：**{guess}**｜骰子：# **{result}**\n\n🎉 **猜中了！**\n"
                f"派彩：**{payout:,.2f}** {currency}（+{payout - view.bet:,.2f}）"
            )
            color = discord.Color.green()
        else:
            text = (
                f"你猜：**{guess}**｜骰子：# **{result}**\n\n💥 **猜錯了！** "
                f"損失 **{view.bet:,.0f}** {currency}"
            )
            color = discord.Color.red()
        text += f"\n新餘額：**{get_balance(view.guild_id, interaction.user.id):,.2f}** {currency}"
        embed = discord.Embed(title="🎲 骰子遊戲", description=text, color=color)
        embed.set_footer(text=f"下注：{view.bet:,.0f} {currency}｜猜中倍率 x5.70")
        again_view = BetAgainView(self, "dice", interaction.user.id, view.guild_id, view.bet)
        try:
            await message.edit(embed=embed, view=again_view)
            again_view.message = message
        except (discord.NotFound, discord.HTTPException):
            pass

    @app_commands.command(name="coinflip", description="擲硬幣：猜正面或反面")
    @app_commands.describe(bet="賭注金額（50～2000）")
    async def coinflip(self, interaction: discord.Interaction, bet: int):
        err = self._validate_bet(bet)
        if err:
            return await interaction.response.send_message(err, ephemeral=True)
        guild_id = resolve_game_guild_id(interaction)
        await self.show_coinflip_choices(interaction, guild_id, float(bet), edit=False)

    async def show_coinflip_choices(
        self,
        interaction: discord.Interaction,
        guild_id: int,
        bet: float,
        edit: bool,
    ):
        currency = get_currency_name(guild_id)
        embed = discord.Embed(
            title="🪙 擲硬幣",
            description="選擇 **正面** 或 **反面**。",
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"下注：{bet:,.0f} {currency}｜按下選項後才會扣款")
        view = CoinflipChoiceView(self, interaction.user.id, guild_id, bet)
        if edit:
            await interaction.response.edit_message(embed=embed, view=view)
        else:
            await interaction.response.send_message(embed=embed, view=view)
        view.message = await interaction.original_response()

    async def play_coinflip_choice(
        self,
        interaction: discord.Interaction,
        view: CoinflipChoiceView,
        side: str,
    ):
        if interaction.user.id != view.user_id:
            return await interaction.response.send_message("這不是你的遊戲。", ephemeral=True)
        side_label = "正面" if side == "heads" else "反面"
        async with view.lock:
            if view.resolved:
                return await interaction.response.send_message("這局已經結算。", ephemeral=True)
            currency = get_currency_name(view.guild_id)
            if not self._charge_bet(
                interaction,
                view.guild_id,
                view.bet,
                "擲硬幣下注",
                "coinflip_bet",
                f"選擇 {side_label}，下注 {view.bet:,.0f} {currency}",
            ):
                balance = get_balance(view.guild_id, interaction.user.id)
                return await interaction.response.send_message(
                    f"❌ 餘額不足！\n你的餘額：**{balance:,.0f}** {currency}\n所需賭注：**{view.bet:,.0f}** {currency}",
                    ephemeral=True,
                )

            result = random.choice(("heads", "tails"))
            won = result == side
            payout = round(view.bet * 1.9, 2) if won else 0.0
            result_label = "正面" if result == "heads" else "反面"
            if payout:
                self._pay_out(
                    view.guild_id,
                    interaction.user.id,
                    payout,
                    "擲硬幣派彩",
                    "coinflip_payout",
                    f"選擇 {side_label}，開出 {result_label}，倍率 x1.90",
                    interaction=interaction,
                )
            view.resolved = True
            view.stop()

        await interaction.response.edit_message(
            embed=discord.Embed(title="🪙 擲硬幣", description="硬幣翻轉中……", color=discord.Color.blurple()),
            view=None,
        )
        message = await interaction.original_response()

        await asyncio.sleep(1.0)
        if won:
            text = (
                f"你選：**{side_label}**｜結果：# **{result_label}**\n\n🎉 **猜中了！**\n"
                f"派彩：**{payout:,.2f}** {currency}（+{payout - view.bet:,.2f}）"
            )
            color = discord.Color.green()
        else:
            text = (
                f"你選：**{side_label}**｜結果：# **{result_label}**\n\n💥 **猜錯了！** "
                f"損失 **{view.bet:,.0f}** {currency}"
            )
            color = discord.Color.red()
        text += f"\n新餘額：**{get_balance(view.guild_id, interaction.user.id):,.2f}** {currency}"
        embed = discord.Embed(title="🪙 擲硬幣", description=text, color=color)
        embed.set_footer(text=f"下注：{view.bet:,.0f} {currency}｜猜中倍率 x1.90")
        again_view = BetAgainView(self, "coinflip", interaction.user.id, view.guild_id, view.bet)
        try:
            await message.edit(embed=embed, view=again_view)
            again_view.message = message
        except (discord.NotFound, discord.HTTPException):
            pass

    @app_commands.command(name="scratchcard", description="購買並刮開一張 3x3 刮刮樂")
    @app_commands.describe(
        bet="賭注金額（50～2000）",
        use_global="是否使用全域幣（預設依伺服器設定）",
    )
    async def scratchcard(self, interaction: discord.Interaction, bet: int, use_global: bool = False):
        err = self._validate_bet(bet)
        if err:
            return await interaction.response.send_message(err, ephemeral=True)
        key = (interaction.channel_id, interaction.user.id)
        if key in self.scratchcard_games:
            return await interaction.response.send_message("你已經有一張尚未刮開的刮刮樂。", ephemeral=True)

        guild_id = resolve_game_guild_id(interaction, use_global)
        currency = get_currency_name(guild_id)
        if not self._charge_bet(
            interaction,
            guild_id,
            float(bet),
            "刮刮樂下注",
            "scratchcard_bet",
            f"購買刮刮樂，下注 {bet:,.0f} {currency}",
        ):
            balance = get_balance(guild_id, interaction.user.id)
            return await interaction.response.send_message(
                f"❌ 餘額不足！\n你的餘額：**{balance:,.0f}** {currency}\n所需賭注：**{bet:,.0f}** {currency}",
                ephemeral=True,
            )

        prize_key, winning_symbol, multiplier, prize_name = draw_scratch_prize()
        game = ScratchcardGame(
            user_id=interaction.user.id,
            channel_id=interaction.channel_id,
            guild_id=guild_id,
            bet=float(bet),
            prize_key=prize_key,
            prize_name=prize_name,
            multiplier=multiplier,
            grid=create_scratch_grid(winning_symbol),
        )
        self.scratchcard_games[key] = game
        view = ScratchcardView(self, game)
        embed = discord.Embed(
            title="🎫 刮刮樂",
            description=f"# {scratch_grid_text(game.grid, hidden=True)}\n\n按下 **刮開** 揭曉票面。",
            color=discord.Color.gold(),
        )
        embed.set_footer(text=f"下注：{bet:,.0f} {currency}｜逾時將自動刮開")
        await interaction.response.send_message(embed=embed, view=view)
        message = await interaction.original_response()
        game.message = message
        game.active_view = view
        view.message = message

    def _settle_scratchcard_finances(
        self,
        game: ScratchcardGame,
        interaction: Optional[discord.Interaction] = None,
    ) -> discord.Embed:
        currency = get_currency_name(game.guild_id)
        payout = round(game.bet * game.multiplier, 2)
        if payout > 0:
            self._pay_out(
                game.guild_id,
                game.user_id,
                payout,
                "刮刮樂派彩",
                "scratchcard_payout",
                f"{game.prize_name}，倍率 x{game.multiplier:.2f}，下注 {game.bet:,.0f} {currency}",
                interaction=interaction,
                color=0xF1C40F if game.prize_key == "jackpot" else 0x2ECC71,
            )
        profit = payout - game.bet
        if payout > 0:
            result = (
                f"🎉 **{game.prize_name}！** 倍率 **x{game.multiplier:.2f}**\n"
                f"派彩：**{payout:,.2f}** {currency}（{'+' if profit >= 0 else ''}{profit:,.2f}）"
            )
            color = discord.Color.gold() if game.prize_key == "jackpot" else discord.Color.green()
        else:
            result = f"💥 **未中獎**，損失 **{game.bet:,.0f}** {currency}"
            color = discord.Color.red()
        description = (
            f"# {scratch_grid_text(game.grid)}\n\n{result}\n"
            f"新餘額：**{get_balance(game.guild_id, game.user_id):,.2f}** {currency}"
        )
        embed = discord.Embed(title="🎫 刮刮樂", description=description, color=color)
        embed.set_footer(text=f"下注：{game.bet:,.0f} {currency}")
        return embed

    async def settle_scratchcard(
        self,
        interaction: discord.Interaction,
        game: ScratchcardGame,
        view: ScratchcardView,
    ):
        if interaction.user.id != game.user_id:
            return await interaction.response.send_message("這不是你的刮刮樂。", ephemeral=True)
        async with game.lock:
            key = (game.channel_id, game.user_id)
            if game.settled or self.scratchcard_games.get(key) is not game:
                return await interaction.response.send_message("這張刮刮樂已經結算。", ephemeral=True)
            game.settled = True
            game.active_view = None
            self.scratchcard_games.pop(key, None)
            embed = self._settle_scratchcard_finances(game, interaction)
            await interaction.response.edit_message(embed=embed, view=None)
            view.stop()

    async def scratchcard_timeout(self, game: ScratchcardGame, view: ScratchcardView):
        async with game.lock:
            key = (game.channel_id, game.user_id)
            if game.settled or self.scratchcard_games.get(key) is not game:
                return
            game.settled = True
            game.active_view = None
            self.scratchcard_games.pop(key, None)
            embed = self._settle_scratchcard_finances(game)
            embed.description += "\n\n⏰ 已逾時，自動刮開並結算。"
            if game.message is not None:
                try:
                    await game.message.edit(embed=embed, view=None)
                except (discord.NotFound, discord.HTTPException):
                    pass
            view.stop()

    @app_commands.command(name="lottery", description="購買 00～99 彩票或查看目前獎池")
    @app_commands.describe(
        bet="每張票的投注金額（50～2000；查詢狀態時留空）",
        number="選擇 00～99（查詢狀態時留空）",
        use_global="是否使用全域幣（預設依伺服器設定）",
    )
    async def lottery(
        self,
        interaction: discord.Interaction,
        bet: Optional[int] = None,
        number: Optional[app_commands.Range[int, 0, 99]] = None,
        use_global: bool = False,
    ):
        if (bet is None) != (number is None):
            return await interaction.response.send_message(
                "❌ 購票時必須同時填寫 `bet` 與 `number`；兩者都留空則查詢狀態。",
                ephemeral=True,
            )
        guild_id = resolve_game_guild_id(interaction, use_global)
        if bet is None and number is None:
            state = self._load_lottery_state(guild_id)
            return await interaction.response.send_message(embed=self._lottery_status_embed(guild_id, state))

        err = self._validate_bet(int(bet))
        if err:
            return await interaction.response.send_message(err, ephemeral=True)

        async with self._lottery_lock(guild_id):
            state = self._load_lottery_state(guild_id)
            draw_at = parse_lottery_draw_at(state.get("draw_at"))
            if state.get("pending_settlement") or (draw_at is not None and draw_at <= datetime.now(timezone.utc)):
                return await interaction.response.send_message("本輪正在等待開獎或結算，請稍後再購票。", ephemeral=True)

            currency = get_currency_name(guild_id)
            if not self._charge_bet(
                interaction,
                guild_id,
                float(bet),
                "彩票購票",
                "lottery_bet",
                f"購買號碼 {int(number):02d}，投注 {int(bet):,.0f} {currency}",
            ):
                balance = get_balance(guild_id, interaction.user.id)
                return await interaction.response.send_message(
                    f"❌ 餘額不足！\n你的餘額：**{balance:,.0f}** {currency}\n所需賭注：**{int(bet):,.0f}** {currency}",
                    ephemeral=True,
                )

            now = datetime.now(timezone.utc)
            if draw_at is None or not state.get("round_id"):
                draw_at = now + LOTTERY_DRAW_DELAY
                state["draw_at"] = draw_at.isoformat()
                state["round_id"] = uuid.uuid4().hex

            number_key = f"{int(number):02d}"
            tickets = state.setdefault("tickets", {})
            entries = tickets.setdefault(number_key, {})
            user_key = str(interaction.user.id)
            entries[user_key] = round(float(entries.get(user_key, 0.0)) + float(bet), 2)
            state["jackpot"] = round(float(state.get("jackpot", 0.0)) + float(bet), 2)

            if not self._save_lottery_state(guild_id, state):
                self._pay_out(
                    guild_id,
                    interaction.user.id,
                    float(bet),
                    "彩票退款",
                    "lottery_refund",
                    "彩票狀態寫入失敗，退還本次購票金額",
                    interaction=interaction,
                    color=0x95A5A6,
                )
                return await interaction.response.send_message("彩票狀態儲存失敗，已退還本次購票金額。", ephemeral=True)

            embed = discord.Embed(
                title="🎟️ 彩票購買成功",
                description=(
                    f"號碼：# **{number_key}**\n"
                    f"本次投注：**{int(bet):,.0f}** {currency}\n"
                    f"你在此號碼的累積投注：**{entries[user_key]:,.2f}** {currency}\n"
                    f"目前獎池：**{state['jackpot']:,.2f}** {currency}\n"
                    f"開獎時間：<t:{int(draw_at.timestamp())}:F>（<t:{int(draw_at.timestamp())}:R>）"
                ),
                color=discord.Color.gold(),
            )
            embed.set_footer(text="中獎者依命中號碼的投注額比例分配 95% 獎池。")
            await interaction.response.send_message(embed=embed)

    @app_commands.command(name="big2", description="建立一桌大老二")
    @app_commands.describe(use_global="是否使用全域幣（預設依伺服器設定）")
    async def startbig2(self, interaction: discord.Interaction, use_global: bool = False):
        cid = interaction.channel_id
        if cid in self.games:
            return await interaction.response.send_message("此頻道已經有一桌了。", ephemeral=True)

        guild_id = resolve_game_guild_id(interaction, use_global)
        g = Game(channel_id=cid, owner_id=interaction.user.id, guild_id=guild_id)
        g.players.append(PlayerState(user_id=interaction.user.id))
        self.games[cid] = g

        view = LobbyView(self, g)
        await interaction.response.send_message(embed=self.lobby_embed(g), view=view)
        sent = await interaction.original_response()
        g.lobby_message_id = sent.id
        g.lobby_message = sent  # 存參考，之後都用 .edit() 不 fetch，user-install 才穩
        g.active_view = view
        view.message = sent

    # -----------------------------
    # Tower 遊戲
    # -----------------------------

    @app_commands.command(name="tower", description="爬塔遊戲")
    @app_commands.describe(bet="賭注金額（50～2000）", use_global="是否使用全域幣（預設依伺服器設定）")
    async def tower(self, interaction: discord.Interaction, bet: int, use_global: bool = False):
        err = self._validate_bet(bet)
        if err:
            return await interaction.response.send_message(err, ephemeral=True)
        bet_val = bet
        key = (interaction.channel_id, interaction.user.id)
        if key in self.tower_games:
            return await interaction.response.send_message("你已經有一局 Tower 遊戲正在進行。", ephemeral=True)

        guild_id = resolve_game_guild_id(interaction, use_global)
        currency = get_currency_name(guild_id)
        balance = get_balance(guild_id, interaction.user.id)

        # 檢查餘額
        if balance < bet_val:
            return await interaction.response.send_message(
                f"❌ 餘額不足！\n你的餘額：**{balance:,.0f}** {currency}\n所需賭注：**{bet_val:,.0f}** {currency}",
                ephemeral=True,
            )

        # 顯示注意事項與確認
        notices = (
            "• 每層 3 格中隨機 **2 格 🟦 安全**、**1 格 🌵 仙人掌**\n"
            "• 踩到仙人掌 = **遊戲結束，失去全部賭注**\n"
            "• 選到安全格可選擇 **繼續攀登**（倍率更高）或 **提現**（鎖定當前倍率獎金）\n"
            "• 倍率：L1 x1.4 → L2 x1.8 → L3 x2.2 → L4 x2.6 → L5 x3.0\n"
            "• 抵達頂層將自動提現"
        )
        embed = discord.Embed(
            title="🗼 爬塔",
            description=f"賭注：**{bet_val:,.0f}** {currency}\n你的餘額：**{balance:,.0f}** {currency}\n\n**⚠️ 注意事項**\n{notices}",
            color=discord.Color.blue(),
        )
        embed.set_footer(text="確認後將扣除賭注並開始遊戲")
        view = TowerConfirmView(self, guild_id, float(bet_val))
        await interaction.response.send_message(embed=embed, view=view)
        sent = await interaction.original_response()
        view.message = sent

    async def start_tower(self, interaction: discord.Interaction, bet: float, guild_id: Optional[int] = None) -> bool:
        """選擇賭注後開始遊戲"""
        if guild_id is None:
            guild_id = resolve_game_guild_id(interaction)
        currency = get_currency_name(guild_id)
        key = (interaction.channel_id, interaction.user.id)
        if key in self.tower_games:
            await interaction.response.send_message("你已經有一局 Tower 遊戲。", ephemeral=True)
            return False

        balance_before = get_balance(guild_id, interaction.user.id)
        if balance_before < bet:
            await interaction.response.send_message(f"餘額不足 {bet:,.0f} {currency}。", ephemeral=True)
            return False

        if not remove_balance(guild_id, interaction.user.id, bet):
            await interaction.response.send_message("扣除賭注失敗。", ephemeral=True)
            return False
        balance_after = get_balance(guild_id, interaction.user.id)
        self._log_economy_history(
            guild_id,
            interaction.user.id,
            "Tower 下注",
            -bet,
            f"下注 {bet:,.0f} {currency}",
        )
        queue_economy_audit_log(
            "tower_bet",
            guild_id=guild_id,
            actor=interaction.user,
            interaction=interaction,
            currency=currency,
            amount=bet,
            balance_before=balance_before,
            balance_after=balance_after,
            detail=f"Tower game started with bet {bet:,.0f} {currency}.",
            color=0xE67E22,
        )
        if guild_id != GLOBAL_GUILD_ID:
            record_transaction(guild_id)

        game = TowerGame(
            user_id=interaction.user.id,
            channel_id=interaction.channel_id,
            guild_id=guild_id,
            bet=bet,
            current_level=1,
            grid=create_tower_grid(),
        )
        self.tower_games[key] = game

        embed = self._tower_embed(game, phase="pick")
        view = TowerGameView(self, game)
        await interaction.response.edit_message(embed=embed, view=view)
        msg = await interaction.original_response()
        game.message_id = msg.id
        game.message = msg
        game.active_view = view
        view.message = msg
        return True

    def _tower_embed(self, game: TowerGame, phase: str = "pick") -> discord.Embed:
        """phase: pick=選格中, result_safe=選到安全可繼續/提現, result_cactus=踩到仙人掌, cashout=提現成功"""
        currency = get_currency_name(game.guild_id)
        level = game.current_level
        safe = game.safe_level()
        mult = TOWER_MULTIPLIERS[safe] if phase == "cashout" else TOWER_MULTIPLIERS[level]

        if phase == "pick":
            desc = f"**第 {level}/{TOWER_LEVELS} 層**\n選擇一個格子！\n可隨時點「💰 結束」提現（倍率 x{TOWER_MULTIPLIERS[safe]:.2f}）"
        elif phase == "result_safe":
            desc = f"**第 {level}/{TOWER_LEVELS} 層** ✅ 安全！\n點下一層繼續，或點「💰 結束」提現。"
        elif phase == "result_cactus":
            desc = f"🌵 踩到仙人掌！遊戲結束，損失 **{game.bet:,.0f}** {currency}"
        elif phase == "cashout":
            mult_actual = TOWER_MULTIPLIERS[safe]
            payout = round(game.bet * mult_actual, 2)
            profit = round(payout - game.bet, 2)
            desc = (
                f"**Cashed Out!**\n"
                f"達到的關卡：**{safe}/{TOWER_LEVELS}**\n"
                f"下注：**{game.bet:,.0f}** {currency}｜倍率：**x{mult_actual:.2f}**\n"
                f"派彩：**{payout:,.0f}** {currency}｜利潤：**+{profit:,.0f}**\n"
                f"新餘額：**{get_balance(game.guild_id, game.user_id):,.0f}** {currency}"
            )
        else:
            desc = ""

        embed = discord.Embed(
            title="🗼 爬塔",
            description=desc,
            color=discord.Color.green() if phase in ("result_safe", "cashout") else discord.Color.red() if phase == "result_cactus" else discord.Color.blue(),
        )
        embed.set_footer(text=f"下注：{game.bet:,.0f} {currency}")
        return embed

    async def tower_pick_tile(self, interaction: discord.Interaction, game: TowerGame, level: int, tile_idx: int):
        if interaction.user.id != game.user_id:
            return await interaction.response.send_message("這不是你的遊戲。", ephemeral=True)
        key = (game.channel_id, game.user_id)
        if key not in self.tower_games or self.tower_games[key] is not game:
            return await interaction.response.send_message("遊戲已結束。", ephemeral=True)

        if level < 1 or level > TOWER_LEVELS or tile_idx < 0 or tile_idx >= TOWER_TILES_PER_LEVEL:
            return await interaction.response.send_message("無效的選擇。", ephemeral=True)

        if game.awaiting_continue:
            if level != game.current_level + 1:
                return await interaction.response.send_message("請點擊下一層或結束。", ephemeral=True)
            game.awaiting_continue = False
            game.current_level = level
        else:
            if level != game.current_level:
                return await interaction.response.send_message("請選擇當前層的格子。", ephemeral=True)

        is_cactus = game.grid[level - 1][tile_idx] == 1
        game.picked_per_level[level] = (tile_idx, is_cactus)

        if is_cactus:
            game.game_over_cactus = True
            game.game_over_reveal_all = True
            self.tower_games.pop(key, None)
            embed = self._tower_embed(game, phase="result_cactus")
            view = TowerGameView(self, game)
            for child in view.children:
                if isinstance(child, discord.ui.Button):
                    child.disabled = True
            old_view = game.active_view
            await interaction.response.edit_message(embed=embed, view=view)
            msg = await interaction.original_response()
            game.message = msg
            game.message_id = msg.id
            game.active_view = None
            view.message = msg
            view.stop()
            if old_view is not None and old_view is not view:
                old_view.stop()
            return

        safe = game.safe_level()
        if level >= TOWER_LEVELS:
            game.game_over_reveal_all = True
            mult = TOWER_MULTIPLIERS[TOWER_LEVELS]
            payout = round(game.bet * mult, 2)
            currency = get_currency_name(game.guild_id)
            balance_before = get_balance(game.guild_id, game.user_id)
            add_balance(game.guild_id, game.user_id, payout)
            balance_after = get_balance(game.guild_id, game.user_id)
            self._log_economy_history(
                game.guild_id,
                game.user_id,
                "Tower 提現",
                payout,
                f"通關自動提現，倍率 x{mult:.2f}，下注 {game.bet:,.0f} {currency}",
            )
            queue_economy_audit_log(
                "tower_cashout",
                guild_id=game.guild_id,
                actor=interaction.user,
                interaction=interaction,
                currency=currency,
                amount=payout,
                balance_before=balance_before,
                balance_after=balance_after,
                detail=f"Tower auto cashout at top level with multiplier x{mult:.2f}.",
                color=0x2ECC71,
            )
            if game.guild_id != GLOBAL_GUILD_ID:
                record_transaction(game.guild_id)
            self.tower_games.pop(key, None)
            embed = self._tower_embed(game, phase="cashout")
            embed.description = (
                f"**抵達頂層！** 自動提現！\n\n"
                f"達到的關卡：**{TOWER_LEVELS}/{TOWER_LEVELS}**\n"
                f"下注：**{game.bet:,.0f}** {get_currency_name(game.guild_id)}\n"
                f"倍率：**x{mult:.2f}**\n"
                f"派彩：**{payout:,.0f}** {get_currency_name(game.guild_id)}\n"
                f"新餘額：**{get_balance(game.guild_id, game.user_id):,.0f}** {get_currency_name(game.guild_id)}"
            )
            view = TowerGameView(self, game)
            for child in view.children:
                if isinstance(child, discord.ui.Button):
                    child.disabled = True
            old_view = game.active_view
            await interaction.response.edit_message(embed=embed, view=view)
            msg = await interaction.original_response()
            game.message = msg
            game.message_id = msg.id
            game.active_view = None
            view.message = msg
            view.stop()
            if old_view is not None and old_view is not view:
                old_view.stop()
            return

        game.awaiting_continue = True
        embed = self._tower_embed(game, phase="result_safe")
        view = TowerGameView(self, game)
        old_view = game.active_view
        await interaction.response.edit_message(embed=embed, view=view)
        msg = await interaction.original_response()
        game.message = msg
        game.message_id = msg.id
        game.active_view = view
        view.message = msg
        if old_view is not None and old_view is not view:
            old_view.stop()

    async def tower_cashout(self, interaction: discord.Interaction, game: TowerGame):
        if interaction.user.id != game.user_id:
            return await interaction.response.send_message("這不是你的遊戲。", ephemeral=True)
        key = (game.channel_id, game.user_id)
        if key not in self.tower_games or self.tower_games[key] is not game:
            return await interaction.response.send_message("遊戲已結束。", ephemeral=True)

        game.game_over_reveal_all = True
        safe = game.safe_level()
        mult = TOWER_MULTIPLIERS[safe]
        payout = round(game.bet * mult, 2)
        currency = get_currency_name(game.guild_id)
        balance_before = get_balance(game.guild_id, game.user_id)
        add_balance(game.guild_id, game.user_id, payout)
        balance_after = get_balance(game.guild_id, game.user_id)
        self._log_economy_history(
            game.guild_id,
            game.user_id,
            "Tower 提現",
            payout,
            f"手動提現，倍率 x{mult:.2f}，下注 {game.bet:,.0f} {currency}",
        )
        queue_economy_audit_log(
            "tower_cashout",
            guild_id=game.guild_id,
            actor=interaction.user,
            interaction=interaction,
            currency=currency,
            amount=payout,
            balance_before=balance_before,
            balance_after=balance_after,
            detail=f"Tower manual cashout at safe level {safe} with multiplier x{mult:.2f}.",
            color=0x2ECC71,
        )
        if game.guild_id != GLOBAL_GUILD_ID:
            record_transaction(game.guild_id)
        self.tower_games.pop(key, None)

        embed = self._tower_embed(game, phase="cashout")
        view = TowerGameView(self, game)
        for child in view.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        old_view = game.active_view
        await interaction.response.edit_message(embed=embed, view=view)
        msg = await interaction.original_response()
        game.message = msg
        game.message_id = msg.id
        game.active_view = None
        view.message = msg
        view.stop()
        if old_view is not None and old_view is not view:
            old_view.stop()

    # -----------------------------
    # Slots 拉霸機
    # -----------------------------

    @app_commands.command(name="slots", description="拉霸機")
    @app_commands.describe(bet="賭注金額（50～2000）", use_global="是否使用全域幣（預設依伺服器設定）")
    async def slots(self, interaction: discord.Interaction, bet: int, use_global: bool = False):
        err = self._validate_bet(bet)
        if err:
            return await interaction.response.send_message(err, ephemeral=True)
        guild_id = resolve_game_guild_id(interaction, use_global)
        await self.do_slots_spin(interaction, guild_id, float(bet), edit=False)

    async def do_slots_spin(self, interaction: discord.Interaction, guild_id: int, bet: float, edit: bool):
        currency = get_currency_name(guild_id)
        key = (interaction.channel_id, interaction.user.id)
        if key in self.slots_spinning:
            return await interaction.response.send_message("拉霸機還在轉，等它停下來！", ephemeral=True)

        if not self._charge_bet(
            interaction, guild_id, bet,
            "拉霸下注", "slots_bet",
            f"拉霸下注 {bet:,.0f} {currency}",
        ):
            balance = get_balance(guild_id, interaction.user.id)
            msg = f"❌ 餘額不足！\n你的餘額：**{balance:,.0f}** {currency}\n所需賭注：**{bet:,.0f}** {currency}"
            return await interaction.response.send_message(msg, ephemeral=True)

        self.slots_spinning.add(key)
        try:
            reels = spin_slots()
            mult, prize_name = slots_multiplier(reels)
            payout = round(bet * mult, 2)
            if payout > 0:
                self._pay_out(
                    guild_id, interaction.user.id, payout,
                    "拉霸派彩", "slots_payout",
                    f"{prize_name}，倍率 x{mult:.2f}，下注 {bet:,.0f} {currency}",
                    interaction=interaction,
                )

            emoji_pool = [s[0] for s in SLOT_SYMBOLS]

            def reels_line(revealed: int) -> str:
                parts = []
                for i in range(3):
                    if i < revealed:
                        parts.append(reels[i])
                    else:
                        parts.append(random.choice(emoji_pool))
                return " | ".join(parts)

            def spin_embed(revealed: int) -> discord.Embed:
                e = discord.Embed(
                    title="🎰 拉霸機",
                    description=f"# {reels_line(revealed)}\n\n🌀 轉動中…",
                    color=discord.Color.blurple(),
                )
                e.set_footer(text=f"下注：{bet:,.0f} {currency}")
                return e

            # 動畫：先全轉，再逐輪停下
            if edit:
                await interaction.response.edit_message(embed=spin_embed(0), view=None)
            else:
                await interaction.response.send_message(embed=spin_embed(0))
            msg = await interaction.original_response()
            for revealed in (1, 2):
                await asyncio.sleep(0.9)
                try:
                    await msg.edit(embed=spin_embed(revealed))
                except (discord.NotFound, discord.HTTPException):
                    pass
            await asyncio.sleep(0.9)

            profit = payout - bet
            balance = get_balance(guild_id, interaction.user.id)
            final_line = " | ".join(reels)
            if payout > 0:
                desc = (
                    f"# {final_line}\n\n"
                    f"🎉 **{prize_name}** 倍率 **x{mult:.2f}**\n"
                    f"派彩：**{payout:,.0f}** {currency}（{'+' if profit >= 0 else ''}{profit:,.0f}）"
                )
                color = discord.Color.green()
            else:
                desc = f"# {final_line}\n\n💥 **{prize_name}**，損失 **{bet:,.0f}** {currency}"
                color = discord.Color.red()
            desc += f"\n新餘額：**{balance:,.0f}** {currency}"

            embed = discord.Embed(title="🎰 拉霸機", description=desc, color=color)
            embed.set_footer(text=f"下注：{bet:,.0f} {currency}")
            view = SlotsAgainView(self, interaction.user.id, guild_id, bet)
            try:
                await msg.edit(embed=embed, view=view)
                view.message = msg
            except (discord.NotFound, discord.HTTPException):
                pass
        finally:
            self.slots_spinning.discard(key)

    # -----------------------------
    # HighLow 比大小
    # -----------------------------

    @app_commands.command(name="highlow", description="比大小：猜下一張牌較大或較小")
    @app_commands.describe(bet="賭注金額（50～2000）", use_global="是否使用全域幣（預設依伺服器設定）")
    async def highlow(self, interaction: discord.Interaction, bet: int, use_global: bool = False):
        err = self._validate_bet(bet)
        if err:
            return await interaction.response.send_message(err, ephemeral=True)
        key = (interaction.channel_id, interaction.user.id)
        if key in self.highlow_games:
            return await interaction.response.send_message("你已經有一局比大小正在進行。", ephemeral=True)

        guild_id = resolve_game_guild_id(interaction, use_global)
        currency = get_currency_name(guild_id)
        if not self._charge_bet(
            interaction, guild_id, float(bet),
            "比大小下注", "highlow_bet",
            f"比大小下注 {bet:,.0f} {currency}",
        ):
            balance = get_balance(guild_id, interaction.user.id)
            return await interaction.response.send_message(
                f"❌ 餘額不足！\n你的餘額：**{balance:,.0f}** {currency}\n所需賭注：**{bet:,.0f}** {currency}",
                ephemeral=True,
            )

        game = HighLowGame(
            user_id=interaction.user.id,
            channel_id=interaction.channel_id,
            guild_id=guild_id,
            bet=float(bet),
            current_rank=random.randint(1, 13),
            current_suit=random.choice(BJ_SUITS),
            pot=float(bet),
        )
        self.highlow_games[key] = game

        view = HighLowView(self, game)
        await interaction.response.send_message(embed=self._highlow_embed(game), view=view)
        sent = await interaction.original_response()
        game.message = sent
        game.active_view = view
        view.message = sent

    def _highlow_embed(self, game: HighLowGame, result_text: str = "") -> discord.Embed:
        currency = get_currency_name(game.guild_id)
        p_high, p_low = hl_probs(game.current_rank)
        mult_high = HL_HOUSE_FACTOR / p_high if p_high > 0 else 0
        mult_low = HL_HOUSE_FACTOR / p_low if p_low > 0 else 0
        card = f"`{hl_rank_name(game.current_rank)}{game.current_suit}`"
        lines = []
        if result_text:
            lines.append(result_text)
        lines.append(f"目前的牌：# {card}")
        lines.append(f"連勝：**{game.streak}**｜目前彩池：**{game.pot:,.1f}** {currency}")
        opts = []
        if p_high > 0:
            opts.append(f"🔼 較大 → x{mult_high:.2f}")
        if p_low > 0:
            opts.append(f"🔽 較小 → x{mult_low:.2f}")
        lines.append("｜".join(opts))
        embed = discord.Embed(
            title="🃏 比大小",
            description="\n".join(lines),
            color=discord.Color.blue(),
        )
        embed.set_footer(text=f"下注：{game.bet:,.0f} {currency}｜下一張必為不同點數（A 最小、K 最大）｜彩池上限 {HL_MAX_MULT:.0f}x 自動提現")
        return embed

    async def highlow_guess(self, interaction: discord.Interaction, game: HighLowGame, guess: str, view: HighLowView):
        if interaction.user.id != game.user_id:
            return await interaction.response.send_message("這不是你的遊戲。", ephemeral=True)
        key = (game.channel_id, game.user_id)
        if self.highlow_games.get(key) is not game:
            return await interaction.response.send_message("遊戲已結束。", ephemeral=True)

        currency = get_currency_name(game.guild_id)
        p_high, p_low = hl_probs(game.current_rank)
        p_chosen = p_high if guess == "high" else p_low
        if p_chosen <= 0:
            return await interaction.response.send_message("無效的選擇。", ephemeral=True)

        next_rank = hl_draw_next(game.current_rank)
        next_suit = random.choice(BJ_SUITS)
        correct = (next_rank > game.current_rank) if guess == "high" else (next_rank < game.current_rank)
        old_card = f"`{hl_rank_name(game.current_rank)}{game.current_suit}`"
        new_card = f"`{hl_rank_name(next_rank)}{next_suit}`"
        game.current_rank = next_rank
        game.current_suit = next_suit

        if not correct:
            self.highlow_games.pop(key, None)
            game.active_view = None
            embed = discord.Embed(
                title="🃏 比大小",
                description=(
                    f"{old_card} → # {new_card}\n\n"
                    f"💥 猜錯了！損失 **{game.pot:,.1f}** {currency}\n"
                    f"連勝止於 **{game.streak}**"
                ),
                color=discord.Color.red(),
            )
            embed.set_footer(text=f"下注：{game.bet:,.0f} {currency}")
            await interaction.response.edit_message(embed=embed, view=None)
            view.stop()
            return

        game.streak += 1
        game.pot = round(game.pot * HL_HOUSE_FACTOR / p_chosen, 2)

        if game.pot >= game.bet * HL_MAX_MULT:
            game.pot = min(game.pot, game.bet * HL_MAX_MULT)
            await self._highlow_do_cashout(interaction, game, view, auto=True)
            return

        result = f"✅ {old_card} → {new_card} 猜對了！"
        new_view = HighLowView(self, game)
        old_view = game.active_view
        await interaction.response.edit_message(embed=self._highlow_embed(game, result), view=new_view)
        msg = await interaction.original_response()
        game.message = msg
        game.active_view = new_view
        new_view.message = msg
        if old_view is not None and old_view is not new_view:
            old_view.stop()

    async def highlow_cashout(self, interaction: discord.Interaction, game: HighLowGame, view: HighLowView):
        if interaction.user.id != game.user_id:
            return await interaction.response.send_message("這不是你的遊戲。", ephemeral=True)
        key = (game.channel_id, game.user_id)
        if self.highlow_games.get(key) is not game:
            return await interaction.response.send_message("遊戲已結束。", ephemeral=True)
        if game.streak < 1:
            return await interaction.response.send_message("至少要猜對一次才能提現。", ephemeral=True)
        await self._highlow_do_cashout(interaction, game, view, auto=False)

    async def _highlow_do_cashout(self, interaction: discord.Interaction, game: HighLowGame, view: HighLowView, auto: bool):
        key = (game.channel_id, game.user_id)
        self.highlow_games.pop(key, None)
        game.active_view = None
        currency = get_currency_name(game.guild_id)
        payout = round(game.pot, 2)
        self._pay_out(
            game.guild_id, game.user_id, payout,
            "比大小提現", "highlow_cashout",
            f"{'達彩池上限自動' if auto else '手動'}提現，連勝 {game.streak}，下注 {game.bet:,.0f} {currency}",
            interaction=interaction,
        )
        profit = payout - game.bet
        embed = discord.Embed(
            title="🃏 比大小",
            description=(
                f"💰 **{'達上限自動提現！' if auto else 'Cashed Out!'}**\n"
                f"連勝：**{game.streak}**\n"
                f"派彩：**{payout:,.1f}** {currency}｜利潤：**{'+' if profit >= 0 else ''}{profit:,.1f}**\n"
                f"新餘額：**{get_balance(game.guild_id, game.user_id):,.0f}** {currency}"
            ),
            color=discord.Color.green(),
        )
        embed.set_footer(text=f"下注：{game.bet:,.0f} {currency}")
        await interaction.response.edit_message(embed=embed, view=None)
        view.stop()

    async def highlow_timeout(self, game: HighLowGame):
        """超時：streak 0 退注，否則自動提現。"""
        key = (game.channel_id, game.user_id)
        if self.highlow_games.get(key) is not game:
            return
        self.highlow_games.pop(key, None)
        currency = get_currency_name(game.guild_id)
        payout = round(game.pot, 2)
        if game.streak < 1:
            label, detail = "比大小退還", f"超時退還賭注 {game.bet:,.0f} {currency}"
            event = "highlow_refund"
        else:
            label, detail = "比大小提現", f"超時自動提現，連勝 {game.streak}，下注 {game.bet:,.0f} {currency}"
            event = "highlow_cashout"
        self._pay_out(game.guild_id, game.user_id, payout, label, event, detail, color=0x95A5A6)
        if game.message is not None:
            embed = discord.Embed(
                title="🃏 比大小",
                description=f"⏰ 遊戲逾時，自動{'退還賭注' if game.streak < 1 else '提現'} **{payout:,.1f}** {currency}。",
                color=discord.Color.light_grey(),
            )
            try:
                await game.message.edit(embed=embed, view=None)
            except (discord.NotFound, discord.HTTPException):
                pass

    # -----------------------------
    # Blackjack 21點
    # -----------------------------

    @app_commands.command(name="blackjack", description="21點：對抗莊家")
    @app_commands.describe(bet="賭注金額（50～2000）", use_global="是否使用全域幣（預設依伺服器設定）")
    async def blackjack(self, interaction: discord.Interaction, bet: int, use_global: bool = False):
        err = self._validate_bet(bet)
        if err:
            return await interaction.response.send_message(err, ephemeral=True)
        key = (interaction.channel_id, interaction.user.id)
        if key in self.blackjack_games:
            return await interaction.response.send_message("你已經有一局 21 點正在進行。", ephemeral=True)

        guild_id = resolve_game_guild_id(interaction, use_global)
        currency = get_currency_name(guild_id)
        if not self._charge_bet(
            interaction, guild_id, float(bet),
            "21點下注", "blackjack_bet",
            f"21點下注 {bet:,.0f} {currency}",
        ):
            balance = get_balance(guild_id, interaction.user.id)
            return await interaction.response.send_message(
                f"❌ 餘額不足！\n你的餘額：**{balance:,.0f}** {currency}\n所需賭注：**{bet:,.0f}** {currency}",
                ephemeral=True,
            )

        deck = bj_new_deck()
        game = BlackjackGame(
            user_id=interaction.user.id,
            channel_id=interaction.channel_id,
            guild_id=guild_id,
            bet=float(bet),
            deck=deck,
        )
        game.player_hand = [deck.pop(), deck.pop()]
        game.dealer_hand = [deck.pop(), deck.pop()]
        self.blackjack_games[key] = game

        # 任一方 Blackjack：立即結算
        if bj_is_blackjack(game.player_hand) or bj_is_blackjack(game.dealer_hand):
            await interaction.response.send_message(embed=discord.Embed(title="🂡 21點", description="發牌中…", color=discord.Color.blue()))
            sent = await interaction.original_response()
            game.message = sent
            await self._bj_settle(interaction, game, view=None, immediate=True)
            return

        view = BlackjackView(self, game)
        await interaction.response.send_message(embed=await self._bj_embed(game, reveal=False), view=view)
        sent = await interaction.original_response()
        game.message = sent
        game.active_view = view
        view.message = sent

    async def _bj_embed(self, game: BlackjackGame, reveal: bool, result_text: str = "", color: Optional[discord.Color] = None) -> discord.Embed:
        currency = get_currency_name(game.guild_id)
        p_total, p_soft = bj_hand_value(game.player_hand)
        p_label = f"{'軟 ' if p_soft else ''}{p_total}"
        if reveal:
            d_total, d_soft = bj_hand_value(game.dealer_hand)
            dealer_line = f"{bj_hand_str(game.dealer_hand)}（{'軟 ' if d_soft else ''}{d_total}）"
        else:
            back = await game_emoji("card_back", "🂠")
            up = game.dealer_hand[0]
            dealer_line = f"`{up[0]}{up[1]}` {back}"
        desc = (
            f"**莊家**：{dealer_line}\n"
            f"**你**：{bj_hand_str(game.player_hand)}（{p_label}）"
        )
        if result_text:
            desc += f"\n\n{result_text}"
        embed = discord.Embed(
            title="🂡 21點",
            description=desc,
            color=color or discord.Color.blue(),
        )
        embed.set_footer(text=f"下注：{game.bet:,.0f} {currency}" + ("（已加倍）" if game.doubled else ""))
        return embed

    async def bj_hit(self, interaction: discord.Interaction, game: BlackjackGame, view: BlackjackView):
        if interaction.user.id != game.user_id:
            return await interaction.response.send_message("這不是你的遊戲。", ephemeral=True)
        key = (game.channel_id, game.user_id)
        if self.blackjack_games.get(key) is not game:
            return await interaction.response.send_message("遊戲已結束。", ephemeral=True)

        game.player_hand.append(game.deck.pop())
        total, _ = bj_hand_value(game.player_hand)
        if total >= 21:
            # 爆牌或 21 點：自動結算（21 自動停牌）
            await self._bj_settle(interaction, game, view)
            return

        new_view = BlackjackView(self, game)
        old_view = game.active_view
        await interaction.response.edit_message(embed=await self._bj_embed(game, reveal=False), view=new_view)
        msg = await interaction.original_response()
        game.message = msg
        game.active_view = new_view
        new_view.message = msg
        if old_view is not None and old_view is not new_view:
            old_view.stop()

    async def bj_stand(self, interaction: discord.Interaction, game: BlackjackGame, view: BlackjackView):
        if interaction.user.id != game.user_id:
            return await interaction.response.send_message("這不是你的遊戲。", ephemeral=True)
        key = (game.channel_id, game.user_id)
        if self.blackjack_games.get(key) is not game:
            return await interaction.response.send_message("遊戲已結束。", ephemeral=True)
        await self._bj_settle(interaction, game, view)

    async def bj_double(self, interaction: discord.Interaction, game: BlackjackGame, view: BlackjackView):
        if interaction.user.id != game.user_id:
            return await interaction.response.send_message("這不是你的遊戲。", ephemeral=True)
        key = (game.channel_id, game.user_id)
        if self.blackjack_games.get(key) is not game:
            return await interaction.response.send_message("遊戲已結束。", ephemeral=True)
        if len(game.player_hand) != 2 or game.doubled:
            return await interaction.response.send_message("只有首兩張牌時可以加倍。", ephemeral=True)

        currency = get_currency_name(game.guild_id)
        extra = game.bet
        if not self._charge_bet(
            interaction, game.guild_id, extra,
            "21點加倍", "blackjack_bet",
            f"21點加倍，追加下注 {extra:,.0f} {currency}",
        ):
            balance = get_balance(game.guild_id, interaction.user.id)
            return await interaction.response.send_message(
                f"❌ 餘額不足以加倍！\n你的餘額：**{balance:,.0f}** {currency}\n加倍需要：**{extra:,.0f}** {currency}",
                ephemeral=True,
            )
        game.bet += extra
        game.doubled = True
        game.player_hand.append(game.deck.pop())
        await self._bj_settle(interaction, game, view)

    async def _bj_settle(
        self,
        interaction: Optional[discord.Interaction],
        game: BlackjackGame,
        view: Optional[BlackjackView],
        immediate: bool = False,
    ):
        """結算：莊家補牌到 17（含軟17）停，派彩並更新訊息。"""
        if game.settled:
            return
        game.settled = True
        key = (game.channel_id, game.user_id)
        self.blackjack_games.pop(key, None)
        game.active_view = None
        currency = get_currency_name(game.guild_id)

        p_total, _ = bj_hand_value(game.player_hand)
        player_bj = bj_is_blackjack(game.player_hand)
        dealer_bj = bj_is_blackjack(game.dealer_hand)

        if p_total <= 21 and not player_bj and not dealer_bj:
            while bj_hand_value(game.dealer_hand)[0] < 17:
                game.dealer_hand.append(game.deck.pop())
        d_total, _ = bj_hand_value(game.dealer_hand)

        # 判定
        if p_total > 21:
            payout, result, color = 0.0, f"💥 **爆牌！** 損失 **{game.bet:,.0f}** {currency}", discord.Color.red()
        elif player_bj and dealer_bj:
            payout, result, color = game.bet, "🤝 **雙方 Blackjack，平手！** 退還賭注", discord.Color.light_grey()
        elif player_bj:
            payout = round(game.bet * 2.5, 2)
            result, color = f"🎉 **Blackjack！** 3:2 派彩 **{payout:,.0f}** {currency}", discord.Color.gold()
        elif dealer_bj:
            payout, result, color = 0.0, f"💥 **莊家 Blackjack！** 損失 **{game.bet:,.0f}** {currency}", discord.Color.red()
        elif d_total > 21:
            payout = round(game.bet * 2, 2)
            result, color = f"🎉 **莊家爆牌！** 獲得 **{payout:,.0f}** {currency}", discord.Color.green()
        elif p_total > d_total:
            payout = round(game.bet * 2, 2)
            result, color = f"🎉 **你贏了！** 獲得 **{payout:,.0f}** {currency}", discord.Color.green()
        elif p_total == d_total:
            payout, result, color = game.bet, "🤝 **平手！** 退還賭注", discord.Color.light_grey()
        else:
            payout, result, color = 0.0, f"💥 **你輸了！** 損失 **{game.bet:,.0f}** {currency}", discord.Color.red()

        if payout > 0:
            is_push = payout == game.bet
            self._pay_out(
                game.guild_id, game.user_id, payout,
                "21點退還" if is_push else "21點派彩",
                "blackjack_push" if is_push else "blackjack_payout",
                f"{'平手退還' if is_push else '勝利派彩'} {payout:,.0f} {currency}，下注 {game.bet:,.0f} {currency}",
                interaction=interaction,
                color=0x95A5A6 if is_push else 0x2ECC71,
            )

        result += f"\n新餘額：**{get_balance(game.guild_id, game.user_id):,.0f}** {currency}"
        embed = await self._bj_embed(game, reveal=True, result_text=result, color=color)

        if interaction is not None and not interaction.response.is_done():
            await interaction.response.edit_message(embed=embed, view=None)
        elif game.message is not None:
            try:
                await game.message.edit(embed=embed, view=None)
            except (discord.NotFound, discord.HTTPException):
                pass
        if view is not None:
            view.stop()

    async def bj_timeout(self, game: BlackjackGame):
        """超時自動停牌結算，避免資金卡住。"""
        key = (game.channel_id, game.user_id)
        if self.blackjack_games.get(key) is not game:
            return
        await self._bj_settle(None, game, None)

    def big2_rules_embed(self) -> discord.Embed:
        """大老二規則玩法說明（給規則按鈕用）"""
        embed = discord.Embed(
            title="🎴 大老二 規則玩法",
            color=discord.Color.blue(),
            description="2～4 人，每人 13 張牌，先出完者勝。",
        )
        embed.add_field(
            name="牌型大小",
            value=(
                "單張、對子、三條、順子、同花、葫蘆、鐵支、同花順。\n"
                "牌點：3～10、J、Q、K、A、2（2 最大）。\n"
                "花色：♣ < ♦ < ♥ < ♠（同點數時比花色）。"
            ),
            inline=False,
        )
        embed.add_field(
            name="出牌",
            value=(
                "輪到你時可出 **1 / 2 / 3 / 5 張** 合法牌型，或 **Pass**。\n"
                "出的牌必須 **壓過** 上一手（同牌型比大小）；空桌時任意合法牌型皆可領出。\n"
                "所有人 Pass 則清空桌面，由上一手出牌者重新領出。"
            ),
            inline=False,
        )
        embed.add_field(
            name="本桌規則",
            value=(
                "**一般規則**：首手必須包含 3♦，2 不可組成順子。\n"
                "**自由先手**：不強制首手 3♦，房主可在大廳下拉選單切換。"
            ),
            inline=False,
        )
        embed.set_footer(text="點「我的手牌」以選牌出牌。")
        return embed

    def lobby_embed(self, g: Game) -> discord.Embed:
        rule = "必出3♦" if g.rules.must_start_with_3d else "自由先手"
        plist = "\n".join([f"- <@{p.user_id}>" for p in g.players]) or "（無）"
        desc = f"規則：**{rule}**｜人數：{len(g.players)}/4"
        if g.stake > 0:
            currency = get_currency_name(g.guild_id)
            desc += f"\n💰 賭注：每人 **{g.stake:,.0f}** {currency}"
        embed = discord.Embed(
            title="🎴 大老二 開房中",
            color=discord.Color.green(),
            description=desc,
        )
        embed.add_field(name="玩家", value=plist, inline=False)
        embed.set_footer(text="按 ✅加入，房主按 ▶開始。")
        return embed

    async def edit_lobby_message(self, interaction: discord.Interaction, g: Game):
        try:
            if g.lobby_message is not None:
                view = LobbyView(self, g)
                await self._edit_game_message(g, g.lobby_message, embed=self.lobby_embed(g), view=view)
                return
            if g.lobby_message_id is None:
                return
            channel = interaction.channel
            if hasattr(channel, "fetch_message"):
                msg = await channel.fetch_message(g.lobby_message_id)
                g.lobby_message = msg
                view = LobbyView(self, g)
                await self._edit_game_message(g, msg, embed=self.lobby_embed(g), view=view)
        except Exception:
            pass

    async def join(self, interaction: discord.Interaction, g: Game):
        if g.started:
            return await interaction.response.send_message("遊戲已開始，不能加入。", ephemeral=True)
        if any(p.user_id == interaction.user.id for p in g.players):
            return await interaction.response.send_message("你已經在桌上了。", ephemeral=True)
        if len(g.players) >= 4:
            return await interaction.response.send_message("最多 4 人。", ephemeral=True)

        g.players.append(PlayerState(user_id=interaction.user.id))
        await interaction.response.send_message("加入成功！", ephemeral=True)
        await self.edit_lobby_message(interaction, g)

    async def start(self, interaction: discord.Interaction, g: Game):
        if interaction.user.id != g.owner_id:
            return await interaction.response.send_message("只有房主可以開始。", ephemeral=True)
        if g.started:
            return await interaction.response.send_message("已開始。", ephemeral=True)
        if len(g.players) < 2:
            return await interaction.response.send_message("至少需要 2 人才能開始。", ephemeral=True)

        # 有賭注時：檢查餘額並先扣款（失敗則全數退還）
        if g.stake > 0:
            currency = get_currency_name(g.guild_id)
            insufficient = [
                p for p in g.players
                if get_balance(g.guild_id, p.user_id) < g.stake
            ]
            if insufficient:
                names = "、".join(f"<@{p.user_id}>" for p in insufficient)
                return await interaction.response.send_message(
                    f"以下玩家餘額不足 **{g.stake:,.0f}** {currency}：{names}",
                    ephemeral=True,
                )
            collected: List[int] = []
            for p in g.players:
                balance_before = get_balance(g.guild_id, p.user_id)
                if not remove_balance(g.guild_id, p.user_id, g.stake):
                    for uid in collected:
                        refund_before = get_balance(g.guild_id, uid)
                        add_balance(g.guild_id, uid, g.stake)
                        refund_after = get_balance(g.guild_id, uid)
                        self._log_economy_history(
                            g.guild_id,
                            uid,
                            "大老二退款",
                            g.stake,
                            f"房間開局扣款失敗，退還賭注 {g.stake:,.0f} {currency}",
                        )
                        queue_economy_audit_log(
                            "big2_refund",
                            guild_id=g.guild_id,
                            actor=interaction.user,
                            target=self._resolve_audit_user(g.guild_id, uid),
                            interaction=interaction,
                            currency=currency,
                            amount=g.stake,
                            balance_before=refund_before,
                            balance_after=refund_after,
                            detail=f"Refunded Big2 stake because player {p.user_id} deduction failed.",
                            color=0x95A5A6,
                        )
                    return await interaction.response.send_message(
                        f"<@{p.user_id}> 扣款失敗，已退還已扣玩家。",
                        ephemeral=True,
                    )
                balance_after = get_balance(g.guild_id, p.user_id)
                self._log_economy_history(
                    g.guild_id,
                    p.user_id,
                    "大老二下注",
                    -g.stake,
                    f"房主 {g.owner_id} 開局，下注 {g.stake:,.0f} {currency}",
                )
                queue_economy_audit_log(
                    "big2_bet",
                    guild_id=g.guild_id,
                    actor=interaction.user,
                    target=self._resolve_audit_user(g.guild_id, p.user_id),
                    interaction=interaction,
                    currency=currency,
                    amount=g.stake,
                    balance_before=balance_before,
                    balance_after=balance_after,
                    detail=f"Collected Big2 stake from player {p.user_id}.",
                    color=0xF39C12,
                )
                collected.append(p.user_id)
            if g.guild_id != GLOBAL_GUILD_ID:
                record_transaction(g.guild_id)

        # 發牌：每人 13 張，若規則必出 3♦ 則保證至少一人手上有 3♦（重發至多 10 次）
        deck = [Card(r, s) for r in RANKS for s in SUITS]
        for _ in range(10):
            random.shuffle(deck)
            for i, p in enumerate(g.players):
                p.hand = sorted(deck[i * 13 : (i + 1) * 13], key=lambda c: c.power)
                p.passed = False
                p.finished = False
            if not g.rules.must_start_with_3d or any(_has_3d(p.hand) for p in g.players):
                break

        g.started = True
        g.first_trick = True
        g.reset_trick()

        # 先手：有 3♦ 的人先出（用 _has_3d 判斷）
        g.turn_index = 0
        if g.rules.must_start_with_3d:
            for idx, p in enumerate(g.players):
                if _has_3d(p.hand):
                    g.turn_index = idx
                    break
        else:
            g.turn_index = g.index_of(g.owner_id) if g.index_of(g.owner_id) >= 0 else 0

        # 用已存的 lobby_message 編輯，不 fetch，user-install 才穩
        await interaction.response.send_message("遊戲開始！", ephemeral=True)
        try:
            if g.lobby_message is not None:
                view = TableView(self, g)
                await self._edit_game_message(g, g.lobby_message, embed=self.table_embed(g), view=view)
            elif g.lobby_message_id is not None and hasattr(interaction.channel, "fetch_message"):
                msg = await interaction.channel.fetch_message(g.lobby_message_id)
                g.lobby_message = msg
                view = TableView(self, g)
                await self._edit_game_message(g, msg, embed=self.table_embed(g), view=view)
            else:
                await interaction.followup.send("無法取得桌面訊息。", ephemeral=True)
        except Exception:
            await interaction.followup.send("無法更新桌面訊息。", ephemeral=True)

    def table_embed(self, g: Game) -> discord.Embed:
        if g.is_game_over() and g.finish_order:
            # 遊戲結束：公布名次與獎金
            rank_text = "\n".join(
                f"**第 {i} 名**：<@{uid}>"
                for i, uid in enumerate(g.finish_order, 1)
            )
            embed = discord.Embed(
                title="🎴 大老二 遊戲結束",
                color=discord.Color.gold(),
                description="名次如下：",
            )
            embed.add_field(name="排名", value=rank_text, inline=False)
            if g.stake > 0:
                winner_id = g.finish_order[0]
                prize = g.stake * len(g.players)
                currency = get_currency_name(g.guild_id)
                embed.add_field(
                    name="💰 獎金",
                    value=f"🏆 冠軍 <@{winner_id}> 獲得 **{prize:,.0f}** {currency}！",
                    inline=False,
                )
            embed.set_footer(text="房主可再開新局。")
            return embed
        cur = g.current_player().user_id
        table = "（無）" if g.table_cards is None else " ".join(map(str, g.table_cards))
        statuses = []
        for p in g.players:
            tag = f"<@{p.user_id}> [{len(p.hand)}張]"
            if p.finished:
                tag += " ✅"
            elif p.passed:
                tag += " ⛔"
            statuses.append(tag)
        rule = "必出3♦" if g.rules.must_start_with_3d else "自由先手"
        desc = f"規則：**{rule}**"
        if g.stake > 0:
            desc += f"｜💰 賭注：{g.stake:,.0f} {get_currency_name(g.guild_id)}"
        embed = discord.Embed(
            title="🎴 大老二 進行中",
            color=discord.Color.blue(),
            description=desc,
        )
        embed.add_field(name="上一手", value=table, inline=False)
        embed.add_field(name="輪到", value=f"<@{cur}>", inline=True)
        embed.add_field(name="狀態", value=" ".join(statuses), inline=False)
        embed.set_footer(text="點 🂠 我的手牌來出牌。")
        return embed

    async def update_table_message(self, channel: discord.abc.Messageable, g: Game):
        try:
            game_over = g.is_game_over() and g.finish_order
            if game_over and g.stake > 0 and g.finish_order and not g.stake_paid:
                # 賭注局：獎金發給冠軍（只發一次）
                g.stake_paid = True
                winner_id = g.finish_order[0]
                prize = g.stake * len(g.players)
                currency = get_currency_name(g.guild_id)
                balance_before = get_balance(g.guild_id, winner_id)
                add_balance(g.guild_id, winner_id, prize)
                balance_after = get_balance(g.guild_id, winner_id)
                self._log_economy_history(
                    g.guild_id,
                    winner_id,
                    "大老二獎金",
                    prize,
                    f"冠軍獎金，{len(g.players)} 人局，底注 {g.stake:,.0f} {currency}",
                )
                queue_economy_audit_log(
                    "big2_payout",
                    guild_id=g.guild_id,
                    target=self._resolve_audit_user(g.guild_id, winner_id),
                    currency=currency,
                    amount=prize,
                    balance_before=balance_before,
                    balance_after=balance_after,
                    detail=f"Big2 payout sent to winner {winner_id}.",
                    color=0xF1C40F,
                    extra_fields=[
                        ("Winner", f"<@{winner_id}>", False),
                        ("Stake / Player", f"{g.stake:,.0f} {currency}", True),
                        ("Players", str(len(g.players)), True),
                    ],
                )
                if g.guild_id != GLOBAL_GUILD_ID:
                    record_transaction(g.guild_id)
            view = None if game_over else TableView(self, g)
            if g.lobby_message is not None:
                await self._edit_game_message(g, g.lobby_message, embed=self.table_embed(g), view=view)
                if game_over:
                    self.games.pop(g.channel_id, None)
                return
            if g.lobby_message_id is None:
                return
            if hasattr(channel, "fetch_message"):
                msg = await channel.fetch_message(g.lobby_message_id)
                g.lobby_message = msg
                await self._edit_game_message(g, msg, embed=self.table_embed(g), view=view)
                if game_over:
                    self.games.pop(g.channel_id, None)
        except Exception:
            pass

    # -----------------------------
    # DOOM Command
    # -----------------------------

    @app_commands.command(name="doom", description="開始玩 DOOM")
    async def doom(self, interaction: discord.Interaction):
        link = "https://doom.p2r3.com/i.webp"
        user = interaction.user

        class StepButton(discord.ui.Button):
            def __init__(self, step, label=None, emoji=None, style=discord.ButtonStyle.primary, row: int = 0):
                super().__init__(style=style, row=row, emoji=emoji)
                self.step = step

            async def callback(self, interaction: discord.Interaction):
                nonlocal link
                if interaction.user.id != user.id:
                    await interaction.response.send_message("這不是你的遊戲。", ephemeral=True)
                    return
                embed, link = generate_doom_embed(link=link, step=self.step)
                await interaction.response.edit_message(embed=embed, view=self.view)

        class SaveButton(discord.ui.Button):
            def __init__(self):
                super().__init__(label=None, style=discord.ButtonStyle.primary, row=0, emoji="💾")

            async def callback(self, interaction: discord.Interaction):
                user_id = str(interaction.user.id)
                if interaction.user.id != user.id:
                    await interaction.response.send_message("這不是你的遊戲。", ephemeral=True)
                    return
                guild_id = None
                set_user_data(guild_id, user_id, "doom_link", link)
                await interaction.response.send_message("存檔成功！", ephemeral=True)

        class LoadButton(discord.ui.Button):
            def __init__(self):
                super().__init__(label=None, style=discord.ButtonStyle.primary, row=1, emoji="📂")

            async def callback(self, interaction: discord.Interaction):
                user_id = str(interaction.user.id)
                if interaction.user.id != user.id:
                    await interaction.response.send_message("這不是你的遊戲。", ephemeral=True)
                    return
                guild_id = None
                saved_link = get_user_data(guild_id, user_id, "doom_link")
                if not saved_link:
                    await interaction.response.send_message("錯誤：沒有存檔。", ephemeral=True)
                    return
                nonlocal link
                link = saved_link
                embed, link = generate_doom_embed(link=link)
                await interaction.response.edit_message(embed=embed, view=self.view)
                await interaction.followup.send("讀檔成功！", ephemeral=True)

        embed, link = generate_doom_embed()

        emoji_map = {
            "q": "🔫",
            "w": "⬆️",
            "e": "🖐️",
            "a": "⬅️",
            "s": "⬇️",
            "d": "➡️",
        }

        view = discord.ui.View(timeout=None)
        for i, s in enumerate(["q", "w", "e", "a", "s", "d"]):
            row = 0 if i < 3 else 1
            view.add_item(StepButton(step=s, emoji=emoji_map.get(s), row=row))
        view.add_item(SaveButton())
        view.add_item(LoadButton())

        await interaction.response.send_message(embed=embed, view=view)


asyncio.run(bot.add_cog(MiniGamesCog(bot)))
