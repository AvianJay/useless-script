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