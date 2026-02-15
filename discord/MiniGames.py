from globalenv import bot, start_bot, get_user_data, set_user_data
import discord
from Economy import (
    get_balance,
    add_balance,
    remove_balance,
    get_currency_name,
    record_transaction,
    GLOBAL_GUILD_ID,
)
from discord.ext import commands
from discord import app_commands
from logger import log
import logging

import random
import asyncio
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple, Any
from collections import Counter


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
# Discord Views
# -----------------------------

class LobbyView(discord.ui.View):
    def __init__(self, cog: "MiniGamesCog", game: Game):
        super().__init__(timeout=600)
        self.cog = cog
        self.game = game

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

    @discord.ui.button(label="✅ 確認開始", style=discord.ButtonStyle.success)
    async def confirm_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.start_tower(interaction, self.bet)


class TowerGameView(discord.ui.View):
    """Tower 遊戲主介面：5 層按鈕恆顯 + 結束按鈕（第一層右邊）"""

    def __init__(self, cog: "MiniGamesCog", game: TowerGame):
        super().__init__(timeout=120)
        self.cog = cog
        self.game = game
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


class TableView(discord.ui.View):
    def __init__(self, cog: "MiniGamesCog", game: Game):
        super().__init__(timeout=None)
        self.cog = cog
        self.game = game

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

    @discord.ui.button(label="🛑 結束（房主）", style=discord.ButtonStyle.danger)
    async def end_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.game.owner_id:
            return await interaction.response.send_message("只有房主可以結束。", ephemeral=True)
        self.cog.games.pop(self.game.channel_id, None)
        await interaction.response.edit_message(content="此局已結束。", embed=None, view=None)
        self.stop()


class HandView(discord.ui.View):
    def __init__(self, cog: "MiniGamesCog", game: Game, player_id: int):
        super().__init__(timeout=60)
        self.cog = cog
        self.game = game
        self.player_id = player_id
        self.selected: List[str] = []

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

    @app_commands.command(name="big2", description="建立一桌大老二")
    async def startbig2(self, interaction: discord.Interaction):
        cid = interaction.channel_id
        if cid in self.games:
            return await interaction.response.send_message("此頻道已經有一桌了。", ephemeral=True)

        guild_id = interaction.guild.id if interaction.guild else GLOBAL_GUILD_ID
        g = Game(channel_id=cid, owner_id=interaction.user.id, guild_id=guild_id)
        g.players.append(PlayerState(user_id=interaction.user.id))
        self.games[cid] = g

        view = LobbyView(self, g)
        await interaction.response.send_message(embed=self.lobby_embed(g), view=view)
        sent = await interaction.original_response()
        g.lobby_message_id = sent.id
        g.lobby_message = sent  # 存參考，之後都用 .edit() 不 fetch，user-install 才穩

    # -----------------------------
    # Tower 遊戲
    # -----------------------------

    @app_commands.command(name="tower", description="爬塔遊戲")
    @app_commands.describe(bet="賭注金額（50～2000）")
    async def tower(self, interaction: discord.Interaction, bet: int):
        if bet < 50 or bet > 2000:
            return await interaction.response.send_message(
                "❌ 賭注金額需介於 **50**～**2000** 之間。",
                ephemeral=True,
            )
        bet_val = bet
        key = (interaction.channel_id, interaction.user.id)
        if key in self.tower_games:
            return await interaction.response.send_message("你已經有一局 Tower 遊戲正在進行。", ephemeral=True)

        guild_id = interaction.guild.id if interaction.guild else GLOBAL_GUILD_ID
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

    async def start_tower(self, interaction: discord.Interaction, bet: float):
        """選擇賭注後開始遊戲"""
        guild_id = interaction.guild.id if interaction.guild else GLOBAL_GUILD_ID
        key = (interaction.channel_id, interaction.user.id)
        if key in self.tower_games:
            return await interaction.response.send_message("你已經有一局 Tower 遊戲。", ephemeral=True)

        if get_balance(guild_id, interaction.user.id) < bet:
            currency = get_currency_name(guild_id)
            return await interaction.response.send_message(f"餘額不足 {bet:,.0f} {currency}。", ephemeral=True)

        if not remove_balance(guild_id, interaction.user.id, bet):
            return await interaction.response.send_message("扣除賭注失敗。", ephemeral=True)
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
        msg = await interaction.response.edit_message(embed=embed, view=view)
        game.message_id = msg.id
        game.message = msg

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
            await interaction.response.edit_message(embed=embed, view=view)
            return

        safe = game.safe_level()
        if level >= TOWER_LEVELS:
            game.game_over_reveal_all = True
            mult = TOWER_MULTIPLIERS[TOWER_LEVELS]
            payout = round(game.bet * mult, 2)
            add_balance(game.guild_id, game.user_id, payout)
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
            await interaction.response.edit_message(embed=embed, view=view)
            return

        game.awaiting_continue = True
        embed = self._tower_embed(game, phase="result_safe")
        view = TowerGameView(self, game)
        await interaction.response.edit_message(embed=embed, view=view)

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
        add_balance(game.guild_id, game.user_id, payout)
        if game.guild_id != GLOBAL_GUILD_ID:
            record_transaction(game.guild_id)
        self.tower_games.pop(key, None)

        embed = self._tower_embed(game, phase="cashout")
        view = TowerGameView(self, game)
        await interaction.response.edit_message(embed=embed, view=view)

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
                await g.lobby_message.edit(embed=self.lobby_embed(g), view=LobbyView(self, g))
                return
            if g.lobby_message_id is None:
                return
            channel = interaction.channel
            if hasattr(channel, "fetch_message"):
                msg = await channel.fetch_message(g.lobby_message_id)
                g.lobby_message = msg
                await msg.edit(embed=self.lobby_embed(g), view=LobbyView(self, g))
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
                if not remove_balance(g.guild_id, p.user_id, g.stake):
                    for uid in collected:
                        add_balance(g.guild_id, uid, g.stake)
                    return await interaction.response.send_message(
                        f"<@{p.user_id}> 扣款失敗，已退還已扣玩家。",
                        ephemeral=True,
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
                await g.lobby_message.edit(embed=self.table_embed(g), view=TableView(self, g))
            elif g.lobby_message_id is not None and hasattr(interaction.channel, "fetch_message"):
                msg = await interaction.channel.fetch_message(g.lobby_message_id)
                g.lobby_message = msg
                await msg.edit(embed=self.table_embed(g), view=TableView(self, g))
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
                add_balance(g.guild_id, winner_id, prize)
                if g.guild_id != GLOBAL_GUILD_ID:
                    record_transaction(g.guild_id)
            view = None if game_over else TableView(self, g)
            if g.lobby_message is not None:
                await g.lobby_message.edit(embed=self.table_embed(g), view=view)
                if game_over:
                    self.games.pop(g.channel_id, None)
                return
            if g.lobby_message_id is None:
                return
            if hasattr(channel, "fetch_message"):
                msg = await channel.fetch_message(g.lobby_message_id)
                g.lobby_message = msg
                await msg.edit(embed=self.table_embed(g), view=view)
                if game_over:
                    self.games.pop(g.channel_id, None)
        except Exception:
            pass

asyncio.run(bot.add_cog(MiniGamesCog(bot)))