from globalenv import (
    bot, config, get_server_config, set_server_config, get_user_data, set_user_data,
    get_all_user_data, interaction_uses_guild_scope, ECONOMY_GLOBAL_MODE_CONFIG_KEY,
)
import discord
from discord.ext import commands
from discord import app_commands
from logger import log
import logging
import asyncio
import time
import math
from datetime import datetime, timezone
from ItemSystem import (
    items, give_item_to_user, remove_item_from_user, get_user_items,
    all_items_autocomplete, get_user_items_autocomplete,
    admin_action_callbacks, get_item_by_id, get_all_items_for_guild
)
from OwnerTools import is_owner


# ==================== Constants ====================
GLOBAL_GUILD_ID = 0
DEFAULT_EXCHANGE_RATE = 1.0
DEFAULT_DAILY_AMOUNT = 100
DEFAULT_HOURLY_AMOUNT = 10
DEFAULT_SELL_RATIO = 0.7   # 賣出價為買入價的 70%
EXCHANGE_FEE_PERCENT = 5   # 兌換手續費 5%
TRADE_FEE_PERCENT = 3      # 轉帳手續費 3%
EXCHANGE_RATE_MIN = 0.01
EXCHANGE_RATE_MAX = 100.0
MAX_GLOBAL_BALANCE = 10_000_000.0  # 全域幣上限：1000萬

# 通膨/通縮權重
ADMIN_INJECTION_WEIGHT = 0.015   # 管理員注入造成的貶值權重
TRADE_HEALTH_WEIGHT = 0.003      # 交易（手續費銷毀）帶來的升值權重
PURCHASE_DEFLATION_WEIGHT = 0.005  # 購買（貨幣銷毀）帶來的升值權重
SALE_INFLATION_WEIGHT = 0.003    # 賣出（貨幣新增）造成的通膨權重
DAILY_INFLATION_WEIGHT = 0.0005  # 每日獎勵造成的微量通膨
HOURLY_INFLATION_WEIGHT = 0.00005  # 每小時獎勵造成的極小通膨

GLOBAL_CURRENCY_NAME = "全域幣"
GLOBAL_CURRENCY_EMOJI = "🌐"
SERVER_CURRENCY_EMOJI = "🏦"

# ==================== 防濫用機制說明 ====================
# 1. 管理員物品追蹤：所有管理員給予的物品都會被標記
# 2. 嚴重通膨懲罰：賣出管理員物品時使用 ADMIN_INJECTION_WEIGHT 而非 SALE_INFLATION_WEIGHT
# 3. 全域商店限制：管理員物品無法賣到全域商店（防止洗錢）
# 4. 支票限制：管理員給予的支票無法兌現
# 5. 全域幣上限：防止無限刷錢
# 6. 交易追蹤：交易時會轉移管理員物品標記
# 7. 移除補償：管理員移除貨幣時會減少 admin_injected 記錄


# ==================== Economy Helper Functions ====================

def get_balance(guild_id: int, user_id: int) -> float:
    """取得用戶在特定伺服器的餘額"""
    return get_user_data(guild_id, user_id, "economy_balance", 0.0)


def set_balance(guild_id: int, user_id: int, amount: float):
    """設定用戶在特定伺服器的餘額"""
    set_user_data(guild_id, user_id, "economy_balance", round(amount, 2))


def get_global_balance(user_id: int) -> float:
    """取得用戶的全域幣餘額"""
    return get_user_data(GLOBAL_GUILD_ID, user_id, "economy_balance", 0.0)


def set_global_balance(user_id: int, amount: float):
    """設定用戶的全域幣餘額（有上限保護）"""
    if amount > MAX_GLOBAL_BALANCE:
        log(f"Global balance cap applied for user {user_id}: {amount:.2f} -> {MAX_GLOBAL_BALANCE:.2f}",
            module_name="Economy", level=logging.WARNING)
        amount = MAX_GLOBAL_BALANCE
    set_user_data(GLOBAL_GUILD_ID, user_id, "economy_balance", round(amount, 2))


def get_exchange_rate(guild_id: int) -> float:
    """取得伺服器匯率（1 伺服幣 = X 全域幣）"""
    return get_server_config(guild_id, "economy_exchange_rate", DEFAULT_EXCHANGE_RATE)


def set_exchange_rate(guild_id: int, rate: float):
    """設定伺服器匯率"""
    rate = max(EXCHANGE_RATE_MIN, min(EXCHANGE_RATE_MAX, round(rate, 6)))
    set_server_config(guild_id, "economy_exchange_rate", rate)


def get_currency_name(guild_id: int) -> str:
    """取得伺服器的貨幣名稱"""
    if not guild_id:
        return GLOBAL_CURRENCY_NAME
    return get_server_config(guild_id, "economy_currency_name", "伺服幣")


def get_daily_amount(guild_id: int) -> int:
    """取得每日獎勵金額（固定值，不隨匯率變動）"""
    return DEFAULT_DAILY_AMOUNT


def get_hourly_amount(guild_id: int) -> int:
    """取得每小時獎勵金額（固定值，不隨匯率變動）"""
    return DEFAULT_HOURLY_AMOUNT


def get_sell_ratio(guild_id: int) -> float:
    """取得賣出比率"""
    return get_server_config(guild_id, "economy_sell_ratio", DEFAULT_SELL_RATIO)


def get_allow_global_flow(guild_id: int) -> bool:
    """取得是否允許伺服幣與全域幣流通（兌換、全域商店等）"""
    return get_server_config(guild_id, "economy_allow_global_flow", True)


def set_allow_global_flow(guild_id: int, allow: bool):
    """設定是否允許伺服幣與全域幣流通"""
    set_server_config(guild_id, "economy_allow_global_flow", allow)


def get_total_supply(guild_id: int) -> float:
    """取得伺服器的貨幣總供給"""
    return get_server_config(guild_id, "economy_total_supply", 0.0)


def adjust_supply(guild_id: int, delta: float):
    """調整貨幣總供給"""
    current = get_total_supply(guild_id)
    set_server_config(guild_id, "economy_total_supply", max(0, round(current + delta, 2)))


def get_admin_injected(guild_id: int) -> float:
    """取得管理員注入的總金額"""
    return get_server_config(guild_id, "economy_admin_injected", 0.0)


def get_transaction_count(guild_id: int) -> int:
    """取得交易次數"""
    return get_server_config(guild_id, "economy_transaction_count", 0)


# ==================== Exchange Rate Mechanics ====================

def apply_inflation(guild_id: int, amount: float, weight: float = ADMIN_INJECTION_WEIGHT):
    """
    對伺服器貨幣施加通膨效果（匯率下降）

    使用「有機經濟基準」+ 對數縮放 + 濫權複利懲罰：
    - 小額注入（≈每日獎勵）= 幾乎無感
    - 中額注入（10-100倍每日）= 明顯貶值
    - 大額注入（1000倍+）= 嚴重貶值
    - 重複濫權 = 複利懲罰，經濟加速崩潰
    """
    rate = get_exchange_rate(guild_id)
    supply = get_total_supply(guild_id)
    admin_injected = get_admin_injected(guild_id)
    daily_amount = get_daily_amount(guild_id)

    # 有機經濟規模 = 總供給 - 管理員注入，至少為 daily*100
    # 這樣管理員注入不會「稀釋」自己的影響
    organic = max(supply - admin_injected, daily_amount * 100, 1)

    # 注入相對於有機經濟的比例
    ratio = abs(amount) / organic

    # 對數縮放：大額注入依然有顯著影響
    # log2(2)=1, log2(11)=3.46, log2(101)=6.66, log2(10001)=13.3
    log_impact = math.log2(1 + ratio)
    base_impact = log_impact * weight

    # 濫權複利懲罰：管理員注入佔總供給越多，每次新注入懲罰越重
    if supply > 0:
        abuse_fraction = admin_injected / supply
    else:
        abuse_fraction = 1.0
    # 10% → 1.08x, 50% → 3x, 80% → 6.1x, 100% → 9x
    abuse_penalty = 1 + (abuse_fraction ** 2) * 8

    # 最終影響：單次最多 60% 貶值（不再是 10%）
    impact = min(base_impact * abuse_penalty, 0.6)

    rate *= (1 - impact)
    set_exchange_rate(guild_id, rate)
    return rate


def apply_deflation(guild_id: int, weight: float = TRADE_HEALTH_WEIGHT):
    """
    對伺服器貨幣施加通縮效果（匯率上升）

    通縮因素：
    - 玩家間交易（手續費銷毀貨幣）
    - 兌換貨幣（手續費銷毀）
    """
    rate = get_exchange_rate(guild_id)
    rate *= (1 + weight)
    set_exchange_rate(guild_id, rate)
    return rate


def apply_market_deflation(guild_id: int, amount: float, weight: float = PURCHASE_DEFLATION_WEIGHT):
    """
    購買物品導致貨幣離開流通 → 通縮（匯率上升）
    影響程度與金額相對於供給量的比例成正比
    """
    rate = get_exchange_rate(guild_id)
    supply = get_total_supply(guild_id)
    if supply <= 0:
        return rate
    ratio = abs(amount) / supply
    impact = math.log2(1 + ratio) * weight
    impact = min(impact, 0.05)  # 單次最多 5% 升值
    rate *= (1 + impact)
    set_exchange_rate(guild_id, rate)
    return rate


def apply_market_inflation(guild_id: int, amount: float, weight: float = SALE_INFLATION_WEIGHT):
    """
    賣出物品導致新貨幣進入流通 → 通膨（匯率下降）
    影響程度與金額相對於供給量的比例成正比
    """
    rate = get_exchange_rate(guild_id)
    supply = get_total_supply(guild_id)
    if supply <= 0:
        return rate
    ratio = abs(amount) / supply
    impact = math.log2(1 + ratio) * weight
    impact = min(impact, 0.05)  # 單次最多 5% 貶值
    rate *= (1 - impact)
    set_exchange_rate(guild_id, rate)
    return rate


def record_admin_injection(guild_id: int, amount: float):
    """記錄管理員注入並觸發通膨"""
    current = get_admin_injected(guild_id)
    set_server_config(guild_id, "economy_admin_injected", round(current + abs(amount), 2))
    new_rate = apply_inflation(guild_id, amount)
    log(f"Admin injection of {amount} in guild {guild_id}, rate now {new_rate:.6f}", module_name="Economy")


def record_transaction(guild_id: int):
    """記錄一筆交易並增加交易次數（手續費銷毀 → 通縮）"""
    count = get_transaction_count(guild_id)
    set_server_config(guild_id, "economy_transaction_count", count + 1)
    apply_deflation(guild_id, TRADE_HEALTH_WEIGHT)


def record_purchase(guild_id: int, amount: float):
    """記錄一筆購買（貨幣被銷毀 → 通縮，按金額比例計算）"""
    count = get_transaction_count(guild_id)
    set_server_config(guild_id, "economy_transaction_count", count + 1)
    apply_market_deflation(guild_id, amount, PURCHASE_DEFLATION_WEIGHT)


def record_sale(guild_id: int, amount: float, is_admin_item: bool = False):
    """記錄一筆賣出（貨幣被創造 → 通膨，按金額比例計算）

    Args:
        guild_id: 伺服器 ID
        amount: 賣出金額
        is_admin_item: 是否為管理員給予的物品（會觸發更嚴重的通膨）
    """
    count = get_transaction_count(guild_id)
    set_server_config(guild_id, "economy_transaction_count", count + 1)

    # 如果是管理員給予的物品被賣出，視為嚴重的經濟漏洞，使用管理員注入的懲罰
    if is_admin_item:
        apply_inflation(guild_id, amount, ADMIN_INJECTION_WEIGHT)
        # 額外記錄為管理員注入（因為這等同於管理員直接給錢）
        current = get_admin_injected(guild_id)
        set_server_config(guild_id, "economy_admin_injected", round(current + abs(amount), 2))
        log(f"Admin-sourced item sold for {amount}, treated as admin injection in guild {guild_id}", module_name="Economy")
    else:
        apply_market_inflation(guild_id, amount, SALE_INFLATION_WEIGHT)


# ==================== Transaction Log ====================

ECONOMY_WEBHOOK_CONFIG_KEY = "economy_log_webhook_url"


def _economy_scope_label(guild_id: int) -> str:
    return "Global" if guild_id == GLOBAL_GUILD_ID else "Server"


def is_global_mode_enabled(guild_id: int) -> bool:
    return bool(get_server_config(guild_id, ECONOMY_GLOBAL_MODE_CONFIG_KEY, False))


def set_global_mode_enabled(guild_id: int, enabled: bool):
    set_server_config(guild_id, ECONOMY_GLOBAL_MODE_CONFIG_KEY, enabled)


def interaction_uses_server_scope(interaction: discord.Interaction) -> bool:
    return interaction_uses_guild_scope(interaction)


def _economy_user_label(user) -> str:
    if not user:
        return "N/A"
    display_name = getattr(user, "display_name", None) or getattr(user, "name", "Unknown")
    username = getattr(user, "name", display_name)
    return f"{display_name} ({username}) | {user.id}"


async def _get_economy_log_channel():
    channel_id = config("economy_log_channel_id", 0)
    if not channel_id:
        return None

    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except Exception:
            return None

    if isinstance(channel, discord.TextChannel):
        return channel
    return None


async def _get_or_create_economy_webhook(channel: discord.TextChannel):
    webhook_url = get_server_config(channel.guild.id, ECONOMY_WEBHOOK_CONFIG_KEY)
    webhook = None

    if webhook_url:
        try:
            webhook = discord.SyncWebhook.from_url(webhook_url)
            webhook.fetch()
        except Exception:
            webhook = None

    if webhook:
        return webhook

    try:
        webhook_obj = await channel.create_webhook(
            name=f"{bot.user.name}-Economy",
            avatar=await bot.user.default_avatar.read(),
        )
        webhook_url = webhook_obj.url
        set_server_config(channel.guild.id, ECONOMY_WEBHOOK_CONFIG_KEY, webhook_url)
        return discord.SyncWebhook.from_url(webhook_url)
    except discord.HTTPException as e:
        if e.code != 30007:
            raise

    webhooks = await channel.webhooks()
    for existing in webhooks:
        try:
            webhook = discord.SyncWebhook.from_url(existing.url)
            webhook.fetch()
            set_server_config(channel.guild.id, ECONOMY_WEBHOOK_CONFIG_KEY, existing.url)
            return webhook
        except Exception:
            continue
    return None


async def send_economy_audit_log(
    action: str,
    *,
    guild_id: int,
    actor=None,
    target=None,
    interaction: discord.Interaction = None,
    ctx = None,
    currency: str = None,
    amount: float = None,
    fee: float = None,
    balance_before: float = None,
    balance_after: float = None,
    target_balance_before: float = None,
    target_balance_after: float = None,
    rate_before: float = None,
    rate_after: float = None,
    item_name: str = None,
    item_amount: int = None,
    detail: str = "",
    color: int = 0xF1C40F,
    extra_fields = None,
):
    if bot.is_closed():
        return

    try:
        await asyncio.wait_for(bot.wait_until_ready(), timeout=5.0)
    except (Exception, asyncio.TimeoutError, asyncio.CancelledError):
        return

    channel = await _get_economy_log_channel()
    if not channel:
        return

    try:
        webhook = await _get_or_create_economy_webhook(channel)
    except Exception as e:
        log(f"Failed to prepare economy webhook: {e}", module_name="Economy", level=logging.ERROR)
        return

    if not webhook:
        return

    source_guild = None
    if interaction and interaction.guild:
        source_guild = interaction.guild
    elif ctx and getattr(ctx, "guild", None):
        source_guild = ctx.guild
    elif guild_id not in (None, GLOBAL_GUILD_ID):
        source_guild = bot.get_guild(guild_id)

    embed = discord.Embed(title=f"Economy Audit | {action}", color=color)
    embed.timestamp = datetime.now(timezone.utc)
    embed.description = detail or "No detail provided."
    embed.add_field(name="Scope", value=_economy_scope_label(guild_id), inline=True)
    embed.add_field(name="Guild ID", value=str(guild_id), inline=True)
    embed.add_field(name="Currency", value=currency or "N/A", inline=True)

    if actor:
        embed.add_field(name="Actor", value=_economy_user_label(actor), inline=False)
    if target:
        embed.add_field(name="Target", value=_economy_user_label(target), inline=False)
    if amount is not None:
        embed.add_field(name="Amount", value=f"{amount:,.2f}", inline=True)
    if fee is not None:
        embed.add_field(name="Fee", value=f"{fee:,.2f}", inline=True)
    if item_name:
        embed.add_field(name="Item", value=f"{item_name} x{item_amount or 1}", inline=True)

    if balance_before is not None or balance_after is not None:
        embed.add_field(
            name="Actor Balance",
            value=f"{balance_before if balance_before is not None else 0:,.2f} -> {balance_after if balance_after is not None else 0:,.2f}",
            inline=False,
        )
    if target_balance_before is not None or target_balance_after is not None:
        embed.add_field(
            name="Target Balance",
            value=f"{target_balance_before if target_balance_before is not None else 0:,.2f} -> {target_balance_after if target_balance_after is not None else 0:,.2f}",
            inline=False,
        )
    if rate_before is not None or rate_after is not None:
        embed.add_field(
            name="Exchange Rate",
            value=f"{rate_before if rate_before is not None else 0:,.6f} -> {rate_after if rate_after is not None else 0:,.6f}",
            inline=False,
        )

    if source_guild:
        embed.add_field(name="Source Guild", value=f"{source_guild.name} | {source_guild.id}", inline=False)
    elif guild_id == GLOBAL_GUILD_ID:
        embed.add_field(name="Source Guild", value="Global Economy", inline=False)

    if interaction:
        channel_name = getattr(interaction.channel, "name", type(interaction.channel).__name__) if interaction.channel else "Unknown"
        command_name = interaction.command.qualified_name if interaction.command else "unknown"
        embed.add_field(
            name="Interaction",
            value=f"User: {interaction.user.id}\nChannel: {channel_name}\nCommand: /{command_name}",
            inline=False,
        )
    elif ctx:
        channel_name = getattr(ctx.channel, "name", type(ctx.channel).__name__) if ctx.channel else "Unknown"
        embed.add_field(
            name="Context",
            value=f"User: {ctx.author.id}\nChannel: {channel_name}\nCommand: {ctx.message.content[:200]}",
            inline=False,
        )

    if guild_id not in (None, GLOBAL_GUILD_ID):
        embed.add_field(name="Server Supply", value=f"{get_total_supply(guild_id):,.2f}", inline=True)
        embed.add_field(name="Tx Count", value=str(get_transaction_count(guild_id)), inline=True)
        embed.add_field(name="Admin Injected", value=f"{get_admin_injected(guild_id):,.2f}", inline=True)

    for field in extra_fields or []:
        embed.add_field(name=field[0], value=field[1], inline=field[2])

    try:
        webhook.send(embed=embed, username=bot.user.name, avatar_url=bot.user.default_avatar.url)
    except Exception as e:
        log(f"Failed to send economy audit log: {e}", module_name="Economy", level=logging.ERROR)


def queue_economy_audit_log(*args, **kwargs):
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(send_economy_audit_log(*args, **kwargs))
    except RuntimeError:
        pass


async def migrate_guild_economy_to_global(guild_id: int) -> dict:
    user_item_rows = get_all_user_data(guild_id, "items")
    user_balance_rows = get_all_user_data(guild_id, "economy_balance")
    affected_user_ids = set(user_item_rows.keys()) | set(user_balance_rows.keys())

    total_server_balance_converted = 0.0
    total_server_item_value = 0.0
    total_global_added = 0.0
    sold_item_units = 0
    rate = get_exchange_rate(guild_id)

    for user_id in affected_user_ids:
        user_items = get_user_data(guild_id, user_id, "items", {}) or {}
        sell_total = 0.0
        user_sold_units = 0

        for item_id, amount in list(user_items.items()):
            if amount <= 0:
                continue
            sell_price = get_item_sell_price(item_id, guild_id)
            if sell_price > 0:
                sell_total += sell_price * amount
            user_sold_units += amount

        if user_items:
            set_user_data(guild_id, user_id, "items", {})
        set_user_data(guild_id, user_id, "admin_items", {})

        server_balance = float(get_balance(guild_id, user_id) or 0.0)
        if server_balance > 0:
            set_balance(guild_id, user_id, 0.0)

        total_server_value = round(server_balance + sell_total, 2)
        if total_server_value > 0:
            converted_global = round(total_server_value * rate, 2)
            set_global_balance(user_id, get_global_balance(user_id) + converted_global)
            log_transaction(
                GLOBAL_GUILD_ID,
                user_id,
                "伺服器轉全域",
                converted_global,
                GLOBAL_CURRENCY_NAME,
                f"From guild {guild_id}, server value {total_server_value:,.2f}",
            )
            total_global_added += converted_global

        total_server_balance_converted += server_balance
        total_server_item_value += round(sell_total, 2)
        sold_item_units += user_sold_units

    set_server_config(guild_id, "economy_total_supply", 0.0)
    set_server_config(guild_id, "economy_admin_injected", 0.0)
    set_server_config(guild_id, "economy_transaction_count", 0)

    return {
        "affected_users": len(affected_user_ids),
        "sold_item_units": sold_item_units,
        "server_balance_converted": round(total_server_balance_converted, 2),
        "server_item_value": round(total_server_item_value, 2),
        "global_added": round(total_global_added, 2),
        "exchange_rate": rate,
    }

def log_transaction(guild_id: int, user_id: int, tx_type: str, amount: float, currency: str, detail: str = ""):
    """記錄一筆交易到用戶的交易紀錄"""
    history = get_user_data(guild_id, user_id, "economy_history", [])
    history.append({
        "type": tx_type,
        "amount": amount,
        "currency": currency,
        "detail": detail,
        "time": datetime.now(timezone.utc).isoformat(),
        "balance_after": get_balance(guild_id, user_id),
    })
    # 只保留最近 50 筆
    if len(history) > 50:
        history = history[-50:]
    set_user_data(guild_id, user_id, "economy_history", history)


def add_balance(guild_id: int, user_id: int, amount: float):
    """增加用戶餘額並追蹤供給量"""
    current = get_balance(guild_id, user_id)
    new_balance = current + amount

    # 全域幣上限檢查
    if guild_id == GLOBAL_GUILD_ID:
        if new_balance > MAX_GLOBAL_BALANCE:
            log(f"Global balance cap reached for user {user_id}: {new_balance:.2f} -> {MAX_GLOBAL_BALANCE:.2f}",
                module_name="Economy", level=logging.WARNING)
            new_balance = MAX_GLOBAL_BALANCE

    set_balance(guild_id, user_id, new_balance)
    if guild_id != GLOBAL_GUILD_ID:
        adjust_supply(guild_id, amount)


def remove_balance(guild_id: int, user_id: int, amount: float) -> bool:
    """扣除用戶餘額，餘額不足時回傳 False"""
    current = get_balance(guild_id, user_id)
    if current < amount:
        return False
    set_balance(guild_id, user_id, current - amount)
    if guild_id != GLOBAL_GUILD_ID:
        adjust_supply(guild_id, -amount)
    return True


# ==================== Admin Action Callback ====================

def get_admin_item_count(guild_id: int, user_id: int, item_id: str) -> int:
    """取得用戶擁有的管理員給予物品數量"""
    admin_items = get_user_data(guild_id, user_id, "admin_items", {})
    return admin_items.get(item_id, 0)


def add_admin_item(guild_id: int, user_id: int, item_id: str, amount: int):
    """記錄管理員給予的物品"""
    admin_items = get_user_data(guild_id, user_id, "admin_items", {})
    admin_items[item_id] = admin_items.get(item_id, 0) + amount
    set_user_data(guild_id, user_id, "admin_items", admin_items)


def remove_admin_item(guild_id: int, user_id: int, item_id: str, amount: int) -> int:
    """移除管理員給予的物品，返回實際移除數量"""
    admin_items = get_user_data(guild_id, user_id, "admin_items", {})
    current = admin_items.get(item_id, 0)
    removed = min(current, amount)
    if removed > 0:
        admin_items[item_id] = current - removed
        if admin_items[item_id] <= 0:
            del admin_items[item_id]
        set_user_data(guild_id, user_id, "admin_items", admin_items)
    return removed


async def on_admin_item_action(guild_id: int, action: str, item_id: str, amount: int, user_id: int = None):
    """
    由 ItemSystem 的管理員操作觸發
    當管理員使用 /itemmod give 時，根據物品價值觸發通膨並標記為管理員物品
    """
    if action == "give" and guild_id and user_id:
        item = get_item_by_id(item_id, guild_id)
        worth = item.get("worth", 0) if item else 0
        total_value = worth * amount
        if total_value > 0:
            # 標記為管理員給予的物品
            add_admin_item(guild_id, user_id, item_id, amount)
            # 觸發通膨
            record_admin_injection(guild_id, total_value)
            log(f"Admin item injection: {item_id} x{amount} (worth {total_value}) to user {user_id} in guild {guild_id}",
                module_name="Economy")

# Register callback
admin_action_callbacks.append(on_admin_item_action)


# ==================== Item Price Helpers ====================

def get_item_worth(item_id: str, guild_id: int = None) -> float:
    """取得物品的全域幣價值。自定義物品僅在提供 guild_id 時能取得定價。"""
    item = get_item_by_id(item_id, guild_id)
    if item:
        return item.get("worth", 0)
    return 0


def get_item_buy_price(item_id: str, guild_id: int) -> float:
    """取得物品在特定伺服器的購買價格（伺服幣）。自定義物品的 worth 即為伺服幣定價。"""
    item = get_item_by_id(item_id, guild_id)
    if not item:
        return 0
    worth = item.get("worth", 0)
    if worth <= 0:
        return 0
    if str(item_id).startswith("custom_"):
        return round(worth, 2)
    rate = get_exchange_rate(guild_id)
    return round(worth / rate, 2)


def get_item_sell_price(item_id: str, guild_id: int) -> float:
    """取得物品在特定伺服器的賣出價格（伺服幣）。自定義物品依定價與賣出比率計算。"""
    item = get_item_by_id(item_id, guild_id)
    if not item:
        return 0
    worth = item.get("worth", 0)
    if worth <= 0:
        return 0
    sell_ratio = get_sell_ratio(guild_id)
    if str(item_id).startswith("custom_"):
        return round(worth * sell_ratio, 2)
    rate = get_exchange_rate(guild_id)
    return round(worth * sell_ratio / rate, 2)


# ==================== Autocomplete ====================

async def purchasable_items_autocomplete(interaction: discord.Interaction, current: str):
    """可購買物品的自動完成（在伺服器內一律顯示含自定義物品的完整清單）"""
    guild_id = interaction.guild.id if interaction_uses_server_scope(interaction) else None
    if guild_id:
        all_items_list = get_all_items_for_guild(guild_id)
        purchasable = [i for i in all_items_list if (i.get("worth") or 0) > 0]
    else:
        purchasable = [i for i in items if (i.get("worth") or 0) > 0]
    if current:
        purchasable = [i for i in purchasable if current.lower() in i["name"].lower() or current.lower() in i["id"].lower()]
    choices = []
    for item in purchasable[:25]:
        price = get_item_buy_price(item["id"], guild_id) if guild_id else (item.get("worth") or 0)
        choices.append(app_commands.Choice(name=f"{item['name']} - 💰{price:,.0f}", value=item["id"]))
    return choices


async def sellable_items_autocomplete(interaction: discord.Interaction, current: str):
    """可賣出物品的自動完成（含伺服器自定義物品）"""
    guild_id = interaction.guild.id if interaction_uses_server_scope(interaction) else None
    user_id = interaction.user.id
    user_items_data = get_user_data(guild_id, user_id, "items", {})
    owned_ids = {item_id for item_id, count in user_items_data.items() if count > 0}
    if guild_id:
        all_items_list = get_all_items_for_guild(guild_id)
    else:
        all_items_list = items
    sellable = [item for item in all_items_list if item["id"] in owned_ids and item.get("worth", 0) > 0]
    if current:
        sellable = [i for i in sellable if current.lower() in i["name"].lower()]
    choices = []
    for item in sellable[:25]:
        price = get_item_sell_price(item["id"], guild_id) if guild_id else round(item.get("worth", 0) * DEFAULT_SELL_RATIO, 2)
        count = user_items_data.get(item["id"], 0)
        choices.append(app_commands.Choice(name=f"{item['name']} x{count} - 💰{price:,.0f}/個", value=item["id"]))
    return choices


# ==================== Shop View ====================

class ShopView(discord.ui.View):
    def __init__(self, interaction: discord.Interaction, purchasable: list):
        super().__init__(timeout=180)
        self.original_interaction = interaction
        self.purchasable = purchasable

        # 建立 Select 選單
        options = []
        for item in purchasable[:25]:  # Discord 限制 25 個選項
            if interaction_uses_server_scope(interaction):
                guild_id = interaction.guild.id
                price = get_item_buy_price(item["id"], guild_id)
                currency = get_currency_name(guild_id)
            else:
                price = item.get("worth", 0)
                currency = GLOBAL_CURRENCY_NAME

            options.append(discord.SelectOption(
                label=item["name"],
                value=item["id"],
                description=f"💰 {price:,.0f} {currency}",
                emoji="🛒"
            ))

        if options:
            self.item_select = discord.ui.Select(
                placeholder="選擇要購買的商品...",
                options=options,
                custom_id="shop_item_select"
            )
            self.item_select.callback = self.on_item_select
            self.add_item(self.item_select)

    async def on_item_select(self, interaction: discord.Interaction):
        selected_item_id = self.item_select.values[0]
        item = get_item_by_id(selected_item_id, interaction.guild.id if interaction_uses_server_scope(interaction) else 0)

        if not item:
            await interaction.response.send_message("❌ 無效的物品。", ephemeral=True)
            return

        # 顯示購買選項（伺服器商店或全域商店）
        if interaction_uses_server_scope(interaction):
            guild_id = interaction.guild.id
            allow_flow = get_allow_global_flow(guild_id)
            is_custom = str(item["id"]).startswith("custom_")

            # 如果是自定義物品或不允許全域流通，只顯示伺服器商店
            if is_custom or not allow_flow:
                modal = PurchaseModal(item, "server")
                await interaction.response.send_modal(modal)
            else:
                # 顯示選擇商店類型的按鈕
                view = ShopTypeView(item)
                server_price = get_item_buy_price(item["id"], guild_id)
                global_price = item.get("worth", 0)
                currency_name = get_currency_name(guild_id)

                embed = discord.Embed(
                    title=f"🛒 購買 {item['name']}",
                    description=item.get('description', '無描述'),
                    color=0x9b59b6
                )
                embed.add_field(
                    name="🏦 伺服器商店",
                    value=f"**{server_price:,.2f}** {currency_name}\n物品到伺服器背包",
                    inline=True
                )
                embed.add_field(
                    name="🌐 全域商店",
                    value=f"**{global_price:,.2f}** {GLOBAL_CURRENCY_NAME}\n物品到全域背包",
                    inline=True
                )
                await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        else:
            # 全域上下文，只能用全域商店
            modal = PurchaseModal(item, "global")
            await interaction.response.send_modal(modal)


class ShopTypeView(discord.ui.View):
    def __init__(self, item: dict):
        super().__init__(timeout=60)
        self.item = item

    @discord.ui.button(label="伺服器商店", style=discord.ButtonStyle.primary, emoji="🏦")
    async def server_shop(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = PurchaseModal(self.item, "server")
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="全域商店", style=discord.ButtonStyle.success, emoji="🌐")
    async def global_shop(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = PurchaseModal(self.item, "global")
        await interaction.response.send_modal(modal)


class PurchaseModal(discord.ui.Modal):
    def __init__(self, item: dict, scope: str):
        super().__init__(title=f"購買 {item['name']}")
        self.item = item
        self.scope = scope

        self.quantity_input = discord.ui.TextInput(
            label="數量",
            placeholder="輸入購買數量...",
            default="1",
            min_length=1,
            max_length=10,
            required=True
        )
        self.add_item(self.quantity_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            amount = int(self.quantity_input.value)
        except ValueError:
            await interaction.response.send_message("❌ 請輸入有效的數量。", ephemeral=True)
            return

        if amount <= 0:
            await interaction.response.send_message("❌ 數量必須大於 0。", ephemeral=True)
            return

        # 執行購買邏輯
        if not interaction_uses_server_scope(interaction):
            scope = "global"
            guild_id = GLOBAL_GUILD_ID
        else:
            guild_id = interaction.guild.id
            scope = self.scope
            if scope == "global" and not get_allow_global_flow(guild_id):
                await interaction.response.send_message("❌ 此伺服器已關閉伺服幣與全域幣的流通功能，無法使用全域商店。", ephemeral=True)
                return

        user_id = interaction.user.id
        item = get_item_by_id(self.item["id"], guild_id if scope == "server" else 0)

        if not item:
            await interaction.response.send_message("❌ 無效的物品 ID。", ephemeral=True)
            return

        worth = item.get("worth", 0)
        if worth <= 0:
            await interaction.response.send_message("❌ 這個物品無法購買。", ephemeral=True)
            return

        if scope == "server":
            currency_name = get_currency_name(guild_id)
            price_per = get_item_buy_price(self.item["id"], guild_id)
            total_price = round(price_per * amount, 2)
            bal = get_balance(guild_id, user_id)
            if bal < total_price:
                await interaction.response.send_message(
                    f"❌ 餘額不足。需要 **{total_price:,.2f}** {currency_name}，但只有 **{bal:,.2f}**。",
                    ephemeral=True
                )
                return
            set_balance(guild_id, user_id, bal - total_price)
            adjust_supply(guild_id, -total_price)
            await give_item_to_user(guild_id, user_id, self.item["id"], amount)
            record_purchase(guild_id, total_price)
        else:
            currency_name = GLOBAL_CURRENCY_NAME
            price_per = worth
            total_price = round(price_per * amount, 2)
            bal = get_global_balance(user_id)
            if bal < total_price:
                await interaction.response.send_message(
                    f"❌ 餘額不足。需要 **{total_price:,.2f}** {currency_name}，但只有 **{bal:,.2f}**。",
                    ephemeral=True
                )
                return
            set_global_balance(user_id, bal - total_price)
            await give_item_to_user(0, user_id, self.item["id"], amount)

        scope_label = "伺服器" if scope == "server" else "全域"
        embed = discord.Embed(
            title=f"🛒 購買成功（{scope_label}）",
            description=f"你購買了 **{item['name']}** x{amount}！",
            color=0x2ecc71
        )
        embed.add_field(name="單價", value=f"{price_per:,.2f} {currency_name}", inline=True)
        embed.add_field(name="總價", value=f"{total_price:,.2f} {currency_name}", inline=True)
        remaining = get_balance(guild_id, user_id) if scope == "server" else get_global_balance(user_id)
        dest = "伺服器背包" if scope == "server" else "全域背包"
        embed.set_footer(text=f"剩餘餘額：{remaining:,.2f} {currency_name} | 物品已放入{dest}")
        buy_guild = guild_id if scope == "server" else GLOBAL_GUILD_ID
        log_transaction(buy_guild, user_id, "購買物品", -total_price, currency_name, f"{item['name']} x{amount}")
        queue_economy_audit_log(
            "buy_item",
            guild_id=buy_guild,
            actor=interaction.user,
            interaction=interaction,
            currency=currency_name,
            amount=total_price,
            balance_before=remaining + total_price,
            balance_after=remaining,
            item_name=item["name"],
            item_amount=amount,
            detail=f"Shop modal purchase via {scope} scope.",
            color=0x27AE60,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


# ==================== Economy Cog ====================

@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
class Economy(commands.GroupCog, name="economy", description="經濟系統指令"):
    def __init__(self):
        super().__init__()

    @app_commands.command(name="balance", description="查看餘額")
    @app_commands.describe(user="查看其他用戶的餘額")
    async def balance(self, interaction: discord.Interaction, user: discord.User = None):
        target = user or interaction.user
        global_bal = get_global_balance(target.id)

        if interaction_uses_server_scope(interaction):
            # 伺服器上下文：同時顯示伺服幣和全域幣
            guild_id = interaction.guild.id
            server_bal = get_balance(guild_id, target.id)
            rate = get_exchange_rate(guild_id)
            currency_name = get_currency_name(guild_id)
            total_global = global_bal + (server_bal * rate)

            embed = discord.Embed(title=f"💰 {target.display_name} 的錢包", color=0xf1c40f)
            embed.add_field(
                name=f"{SERVER_CURRENCY_EMOJI} {currency_name}",
                value=f"**{server_bal:,.2f}**",
                inline=True
            )
            embed.add_field(
                name=f"{GLOBAL_CURRENCY_EMOJI} {GLOBAL_CURRENCY_NAME}",
                value=f"**{global_bal:,.2f}**",
                inline=True
            )
            embed.add_field(
                name="📊 匯率",
                value=f"1 {currency_name} = {rate:.4f} {GLOBAL_CURRENCY_NAME}",
                inline=True
            )
            embed.add_field(
                name="💎 總資產（全域幣計）",
                value=f"**{total_global:,.2f}** {GLOBAL_CURRENCY_NAME}",
                inline=False
            )
            embed.set_footer(
                text=interaction.guild.name,
                icon_url=interaction.guild.icon.url if interaction.guild.icon else None
            )
        else:
            # 全域上下文：僅顯示全域幣
            embed = discord.Embed(title=f"💰 {target.display_name} 的全域錢包", color=0xf1c40f)
            embed.add_field(
                name=f"{GLOBAL_CURRENCY_EMOJI} {GLOBAL_CURRENCY_NAME}",
                value=f"**{global_bal:,.2f}**",
                inline=False
            )
            embed.set_footer(text="全域用戶錢包")
        
        embed.set_thumbnail(url=target.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="daily", description="領取每日獎勵")
    @app_commands.describe(global_daily="是否領取全域獎勵")
    async def daily(self, interaction: discord.Interaction, global_daily: bool = False):
        from datetime import datetime, timezone, timedelta
        
        user_id = interaction.user.id
        
        if global_daily or not interaction_uses_server_scope(interaction):
            # 全域簽到
            guild_id = GLOBAL_GUILD_ID
        else:
            # 伺服器簽到
            guild_id = interaction.guild.id

        # 使用日期檢測（台灣時間）
        now = datetime.now(timezone(timedelta(hours=8))).date()
        
        last_daily = get_user_data(guild_id, user_id, "economy_last_daily")
        if last_daily is not None and not isinstance(last_daily, datetime):
            try:
                last_daily = datetime.fromisoformat(str(last_daily)).date()
            except Exception:
                last_daily = None
        elif isinstance(last_daily, datetime):
            last_daily = last_daily.date()
        
        # 檢查是否今天已簽到
        if last_daily == now:
            # 計算明天的時間
            tomorrow = now + timedelta(days=1)
            next_checkin = datetime.combine(tomorrow, datetime.min.time()).replace(tzinfo=timezone(timedelta(hours=8)))
            next_checkin_utc = next_checkin.astimezone(timezone.utc)
            timestamp_next = int(next_checkin_utc.timestamp())
            await interaction.response.send_message(
                f"⏰ 你已經領取過每日獎勵了！請在 <t:{timestamp_next}:R> 再來。",
                ephemeral=True
            )
            return

        daily_amount = get_daily_amount(guild_id)
        currency_name = get_currency_name(guild_id)

        # 發放獎勵
        add_balance(guild_id, user_id, daily_amount)
        set_user_data(guild_id, user_id, "economy_last_daily", now.isoformat())

        # 每日獎勵造成的微量通膨（全域不需要通膨計算）
        if guild_id != GLOBAL_GUILD_ID:
            apply_inflation(guild_id, daily_amount, DAILY_INFLATION_WEIGHT)

        # 連續登入
        streak = get_user_data(guild_id, user_id, "economy_daily_streak", 0)
        if last_daily is not None and last_daily == now - timedelta(days=1):  # 昨天簽到 = 連續
            streak += 1
        else:
            streak = 1
        set_user_data(guild_id, user_id, "economy_daily_streak", streak)

        bonus = 0
        if streak >= 7:
            bonus = int(daily_amount * 0.5)
            add_balance(guild_id, user_id, bonus)

        scope_label = "全域" if guild_id == GLOBAL_GUILD_ID else "伺服器"
        embed = discord.Embed(
            title=f"📅 每日獎勵（{scope_label}）",
            description=f"你獲得了 **{daily_amount:,.0f}** {currency_name}！",
            color=0x2ecc71
        )
        if bonus > 0:
            embed.add_field(
                name="🔥 連續登入獎勵",
                value=f"+{bonus:,.0f} {currency_name}（連續 {streak} 天）",
                inline=False
            )
        embed.add_field(
            name="📊 目前餘額",
            value=f"{get_balance(guild_id, user_id):,.2f} {currency_name}",
            inline=False
        )
        embed.set_footer(text=f"連續登入：{streak} 天")
        embed.timestamp = datetime.now(timezone(timedelta(hours=8)))
        total_earned = daily_amount + bonus
        log_transaction(guild_id, user_id, "每日簽到", total_earned, currency_name, f"連續 {streak} 天" + (f"，含獎勵 {bonus}" if bonus > 0 else ""))
        queue_economy_audit_log(
            "daily",
            guild_id=guild_id,
            actor=interaction.user,
            interaction=interaction,
            currency=currency_name,
            amount=total_earned,
            balance_before=get_balance(guild_id, user_id) - total_earned,
            balance_after=get_balance(guild_id, user_id),
            detail=f"Daily reward claimed. Streak={streak}, bonus={bonus:,.2f}",
            color=0x2ECC71,
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="hourly", description="領取每小時獎勵")
    @app_commands.describe(global_hourly="是否領取全域獎勵")
    async def hourly(self, interaction: discord.Interaction, global_hourly: bool = False):
        from datetime import datetime, timezone, timedelta
        
        user_id = interaction.user.id
        
        if global_hourly or not interaction_uses_server_scope(interaction):
            # 全域簽到
            guild_id = GLOBAL_GUILD_ID
        else:
            # 伺服器簽到
            guild_id = interaction.guild.id

        # 使用小時檢測（台灣時間）
        now = datetime.now(timezone(timedelta(hours=8)))
        current_hour = now.replace(minute=0, second=0, microsecond=0)
        
        last_hourly = get_user_data(guild_id, user_id, "economy_last_hourly")
        if last_hourly is not None and not isinstance(last_hourly, datetime):
            try:
                last_hourly = datetime.fromisoformat(str(last_hourly))
            except Exception:
                last_hourly = None
        elif isinstance(last_hourly, str):
            try:
                last_hourly = datetime.fromisoformat(last_hourly)
            except Exception:
                last_hourly = None
        
        # 檢查是否同一小時已簽到
        if last_hourly is not None:
            last_hourly_hour = last_hourly.replace(minute=0, second=0, microsecond=0) if isinstance(last_hourly, datetime) else None
            if last_hourly_hour == current_hour:
                # 計算下一小時的時間
                next_hour = current_hour + timedelta(hours=1)
                next_hour_utc = next_hour.astimezone(timezone.utc)
                timestamp_next = int(next_hour_utc.timestamp())
                await interaction.response.send_message(
                    f"⏰ 你已經領取過每小時獎勵了！請在 <t:{timestamp_next}:R> 再來。",
                    ephemeral=True
                )
                return

        hourly_amount = get_hourly_amount(guild_id)
        currency_name = get_currency_name(guild_id)

        # 發放獎勵
        add_balance(guild_id, user_id, hourly_amount)
        set_user_data(guild_id, user_id, "economy_last_hourly", current_hour.isoformat())

        # 每小時獎勵造成的極小通膨（全域不需要通膨計算）
        if guild_id != GLOBAL_GUILD_ID:
            apply_inflation(guild_id, hourly_amount, HOURLY_INFLATION_WEIGHT)

        scope_label = "全域" if guild_id == GLOBAL_GUILD_ID else "伺服器"
        embed = discord.Embed(
            title=f"⏱️ 每小時獎勵（{scope_label}）",
            description=f"你獲得了 **{hourly_amount:,.0f}** {currency_name}！",
            color=0x3498db
        )
        embed.add_field(
            name="📊 目前餘額",
            value=f"{get_balance(guild_id, user_id):,.2f} {currency_name}",
            inline=False
        )
        # embed.set_footer(text="AwA")
        embed.timestamp = now
        log_transaction(guild_id, user_id, "每小時簽到", hourly_amount, currency_name)
        queue_economy_audit_log(
            "hourly",
            guild_id=guild_id,
            actor=interaction.user,
            interaction=interaction,
            currency=currency_name,
            amount=hourly_amount,
            balance_before=get_balance(guild_id, user_id) - hourly_amount,
            balance_after=get_balance(guild_id, user_id),
            detail="Hourly reward claimed.",
            color=0x3498DB,
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="pay", description="轉帳給其他用戶")
    @app_commands.describe(user="收款人", amount="金額", currency="貨幣類型")
    @app_commands.choices(currency=[
        app_commands.Choice(name="伺服幣", value="server"),
        app_commands.Choice(name="全域幣", value="global"),
    ])
    async def pay(self, interaction: discord.Interaction, user: discord.User, amount: float, currency: str = "global"):
        # 全域安裝時強制使用全域幣
        if not interaction_uses_server_scope(interaction):
            currency = "global"
        if amount <= 0:
            await interaction.response.send_message("❌ 金額必須大於 0。", ephemeral=True)
            return
        if user.id == interaction.user.id:
            await interaction.response.send_message("❌ 你不能轉帳給自己。", ephemeral=True)
            return
        if user.bot:
            await interaction.response.send_message("❌ 你不能轉帳給機器人。", ephemeral=True)
            return

        await interaction.response.defer()

        sender_id = interaction.user.id
        receiver_id = user.id

        fee = round(amount * TRADE_FEE_PERCENT / 100, 2)
        total_deduct = round(amount + fee, 2)
        
        if currency == "server" and interaction_uses_server_scope(interaction):
            guild_id = interaction.guild.id
            currency_name = get_currency_name(guild_id)
            sender_bal = get_balance(guild_id, sender_id)
            if sender_bal < total_deduct:
                await interaction.followup.send(
                    f"❌ 餘額不足。需要 **{total_deduct:,.2f}** {currency_name}"
                    f"（含 {TRADE_FEE_PERCENT}% 手續費），但只有 **{sender_bal:,.2f}**。",
                    ephemeral=True
                )
                return
            set_balance(guild_id, sender_id, sender_bal - total_deduct)
            add_balance(guild_id, receiver_id, amount)
            adjust_supply(guild_id, -fee)  # 手續費銷毀
        else:
            currency_name = GLOBAL_CURRENCY_NAME
            sender_bal = get_global_balance(sender_id)
            if sender_bal < total_deduct:
                await interaction.followup.send(
                    f"❌ 餘額不足。需要 **{total_deduct:,.2f}** {currency_name}"
                    f"（含 {TRADE_FEE_PERCENT}% 手續費），但只有 **{sender_bal:,.2f}**。",
                    ephemeral=True
                )
                return
            set_global_balance(sender_id, sender_bal - total_deduct)
            set_global_balance(receiver_id, get_global_balance(receiver_id) + amount)

        if interaction_uses_server_scope(interaction):
            record_transaction(interaction.guild.id)

        # 記錄雙方交易紀錄
        pay_guild = guild_id if (currency == "server" and interaction_uses_server_scope(interaction)) else GLOBAL_GUILD_ID
        log_transaction(pay_guild, sender_id, "轉帳支出", -(amount + fee), currency_name, f"→ {user.display_name}，手續費 {fee:,.2f}")
        log_transaction(pay_guild, receiver_id, "轉帳收入", amount, currency_name, f"← {interaction.user.display_name}")

        embed = discord.Embed(title="轉帳成功", color=0x2ecc71)
        embed.add_field(name="收款人", value=user.display_name, inline=True)
        embed.add_field(name="金額", value=f"{amount:,.2f} {currency_name}", inline=True)
        embed.add_field(name="手續費", value=f"{fee:,.2f} {currency_name} ({TRADE_FEE_PERCENT}%)", inline=True)
        embed.set_footer(text=f"交易由 {interaction.user.display_name} 發起")
        receiver_after = get_balance(pay_guild, receiver_id) if pay_guild != GLOBAL_GUILD_ID else get_global_balance(receiver_id)
        queue_economy_audit_log(
            "pay",
            guild_id=pay_guild,
            actor=interaction.user,
            target=user,
            interaction=interaction,
            currency=currency_name,
            amount=amount,
            fee=fee,
            balance_before=sender_bal,
            balance_after=sender_bal - total_deduct,
            target_balance_before=receiver_after - amount,
            target_balance_after=receiver_after,
            detail=f"Transfer completed. Fee rate={TRADE_FEE_PERCENT}%",
            color=0x2ECC71,
        )
        await interaction.followup.send(embed=embed)

        try:
            await user.send(
                f"你從 **{interaction.user.display_name}** 收到了 **{amount:,.2f}** {currency_name}！\n"
                f"-# {'伺服器: ' + interaction.guild.name if pay_guild else '全域經濟系統'}"
            )
        except Exception:
            pass

    @app_commands.command(name="exchange", description="兌換伺服幣和全域幣")
    @app_commands.guild_only()
    @app_commands.describe(amount="金額", direction="兌換方向")
    @app_commands.choices(direction=[
        app_commands.Choice(name="伺服幣 → 全域幣", value="to_global"),
        app_commands.Choice(name="全域幣 → 伺服幣", value="to_server"),
    ])
    async def exchange(self, interaction: discord.Interaction, amount: float, direction: str):
        if amount <= 0:
            await interaction.response.send_message("❌ 金額必須大於 0。", ephemeral=True)
            return
        
        if not interaction_uses_server_scope(interaction):
            await interaction.response.send_message("❌ 這個指令只能在**有邀請此機器人的伺服器**中使用。", ephemeral=True)
            return

        guild_id = interaction.guild.id
        if not get_allow_global_flow(guild_id):
            await interaction.response.send_message("❌ 此伺服器已關閉伺服幣與全域幣的流通功能。", ephemeral=True)
            return

        user_id = interaction.user.id
        rate = get_exchange_rate(guild_id)
        currency_name = get_currency_name(guild_id)
        fee_percent = EXCHANGE_FEE_PERCENT

        if direction == "to_global":
            server_bal = get_balance(guild_id, user_id)
            if server_bal < amount:
                await interaction.response.send_message(f"❌ {currency_name}餘額不足。", ephemeral=True)
                return

            global_amount = amount * rate
            fee = round(global_amount * fee_percent / 100, 2)
            received = round(global_amount - fee, 2)

            set_balance(guild_id, user_id, server_bal - amount)
            set_global_balance(user_id, get_global_balance(user_id) + received)
            adjust_supply(guild_id, -amount)

            embed = discord.Embed(title="💱 兌換成功", color=0x3498db)
            embed.add_field(name="支出", value=f"{amount:,.2f} {currency_name}", inline=True)
            embed.add_field(name="獲得", value=f"{received:,.2f} {GLOBAL_CURRENCY_NAME}", inline=True)
            embed.add_field(name="手續費", value=f"{fee:,.2f} {GLOBAL_CURRENCY_NAME} ({fee_percent}%)", inline=True)
        else:  # to_server
            global_bal = get_global_balance(user_id)
            if global_bal < amount:
                await interaction.response.send_message(f"❌ {GLOBAL_CURRENCY_NAME}餘額不足。", ephemeral=True)
                return

            server_amount = amount / rate
            fee = round(server_amount * fee_percent / 100, 2)
            received = round(server_amount - fee, 2)

            set_global_balance(user_id, global_bal - amount)
            add_balance(guild_id, user_id, received)

            embed = discord.Embed(title="💱 兌換成功", color=0x3498db)
            embed.add_field(name="支出", value=f"{amount:,.2f} {GLOBAL_CURRENCY_NAME}", inline=True)
            embed.add_field(name="獲得", value=f"{received:,.2f} {currency_name}", inline=True)
            embed.add_field(name="手續費", value=f"{fee:,.2f} {currency_name} ({fee_percent}%)", inline=True)

        embed.add_field(
            name="匯率",
            value=f"1 {currency_name} = {rate:.4f} {GLOBAL_CURRENCY_NAME}",
            inline=False
        )

        record_transaction(guild_id)
        if direction == "to_global":
            queue_economy_audit_log(
                "exchange_to_global",
                guild_id=guild_id,
                actor=interaction.user,
                interaction=interaction,
                currency=currency_name,
                amount=amount,
                fee=fee,
                balance_before=server_bal,
                balance_after=server_bal - amount,
                target_balance_before=get_global_balance(user_id) - received,
                target_balance_after=get_global_balance(user_id),
                rate_before=rate,
                rate_after=get_exchange_rate(guild_id),
                detail=f"Server currency exchanged to global. Received {received:,.2f} {GLOBAL_CURRENCY_NAME}.",
                color=0x3498DB,
            )
        else:
            queue_economy_audit_log(
                "exchange_to_server",
                guild_id=guild_id,
                actor=interaction.user,
                interaction=interaction,
                currency=currency_name,
                amount=received,
                fee=fee,
                balance_before=get_balance(guild_id, user_id) - received,
                balance_after=get_balance(guild_id, user_id),
                target_balance_before=global_bal,
                target_balance_after=global_bal - amount,
                rate_before=rate,
                rate_after=get_exchange_rate(guild_id),
                detail=f"Global currency exchanged to server. Spent {amount:,.2f} {GLOBAL_CURRENCY_NAME}.",
                color=0x3498DB,
            )

        if direction == "to_global":
            log_transaction(guild_id, user_id, "兌換支出", -amount, currency_name, f"→ {received:,.2f} {GLOBAL_CURRENCY_NAME}")
            log_transaction(GLOBAL_GUILD_ID, user_id, "兌換收入", received, GLOBAL_CURRENCY_NAME, f"← {amount:,.2f} {currency_name}")
        else:
            log_transaction(GLOBAL_GUILD_ID, user_id, "兌換支出", -amount, GLOBAL_CURRENCY_NAME, f"→ {received:,.2f} {currency_name}")
            log_transaction(guild_id, user_id, "兌換收入", received, currency_name, f"← {amount:,.2f} {GLOBAL_CURRENCY_NAME}")

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="buy", description="從商店購買物品")
    @app_commands.describe(item_id="要購買的物品", amount="購買數量", scope="商店類型")
    @app_commands.autocomplete(item_id=purchasable_items_autocomplete)
    @app_commands.choices(scope=[
        app_commands.Choice(name="伺服器商店（伺服幣）", value="server"),
        app_commands.Choice(name="全域商店（全域幣）", value="global"),
    ])
    async def buy(self, interaction: discord.Interaction, item_id: str, amount: int = 1, scope: str = "server"):
        # 全域安裝時強制使用全域商店
        if not interaction_uses_server_scope(interaction):
            scope = "global"
            guild_id = GLOBAL_GUILD_ID
        else:
            guild_id = interaction.guild.id
            if scope == "global" and not get_allow_global_flow(guild_id):
                await interaction.response.send_message("❌ 此伺服器已關閉伺服幣與全域幣的流通功能，無法使用全域商店。", ephemeral=True)
                return
        if amount <= 0:
            await interaction.response.send_message("❌ 數量必須大於 0。", ephemeral=True)
            return

        user_id = interaction.user.id

        item = get_item_by_id(item_id, guild_id if scope == "server" else 0)
        if not item:
            await interaction.response.send_message("❌ 無效的物品 ID。", ephemeral=True)
            return

        worth = item.get("worth", 0)
        if worth <= 0:
            await interaction.response.send_message("❌ 這個物品無法購買。", ephemeral=True)
            return

        if scope == "server":
            currency_name = get_currency_name(guild_id)
            price_per = get_item_buy_price(item_id, guild_id)
            total_price = round(price_per * amount, 2)
            bal = get_balance(guild_id, user_id)
            if bal < total_price:
                await interaction.response.send_message(
                    f"❌ 餘額不足。需要 **{total_price:,.2f}** {currency_name}，但只有 **{bal:,.2f}**。",
                    ephemeral=True
                )
                return
            set_balance(guild_id, user_id, bal - total_price)
            adjust_supply(guild_id, -total_price)
            # 伺服器商店：物品到伺服器背包
            await give_item_to_user(guild_id, user_id, item_id, amount)
            record_purchase(guild_id, total_price)
        else:
            currency_name = GLOBAL_CURRENCY_NAME
            price_per = worth
            total_price = round(price_per * amount, 2)
            bal = get_global_balance(user_id)
            if bal < total_price:
                await interaction.response.send_message(
                    f"❌ 餘額不足。需要 **{total_price:,.2f}** {currency_name}，但只有 **{bal:,.2f}**。",
                    ephemeral=True
                )
                return
            new_balance = bal - total_price
            set_global_balance(user_id, new_balance)
            # 全域商店：物品到全域背包 (guild_id=0)
            await give_item_to_user(0, user_id, item_id, amount)

        scope_label = "伺服器" if scope == "server" else "全域"
        embed = discord.Embed(
            title=f"🛒 購買成功（{scope_label}）",
            description=f"你購買了 **{item['name']}** x{amount}！",
            color=0x2ecc71
        )
        embed.add_field(name="單價", value=f"{price_per:,.2f} {currency_name}", inline=True)
        embed.add_field(name="總價", value=f"{total_price:,.2f} {currency_name}", inline=True)
        remaining = get_balance(guild_id, user_id) if scope == "server" else get_global_balance(user_id)
        dest = "伺服器背包" if scope == "server" else "全域背包"
        embed.set_footer(text=f"剩餘餘額：{remaining:,.2f} {currency_name} | 物品已放入{dest}")
        buy_guild = guild_id if scope == "server" else GLOBAL_GUILD_ID
        log_transaction(buy_guild, user_id, "購買物品", -total_price, currency_name, f"{item['name']} x{amount}")
        queue_economy_audit_log(
            "buy_item",
            guild_id=buy_guild,
            actor=interaction.user,
            interaction=interaction,
            currency=currency_name,
            amount=total_price,
            balance_before=remaining + total_price,
            balance_after=remaining,
            item_name=item["name"],
            item_amount=amount,
            detail=f"Slash command purchase via {scope} scope.",
            color=0x27AE60,
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="sell", description="賣出物品給商店")
    @app_commands.describe(item_id="要賣出的物品", amount="賣出數量", scope="商店類型")
    @app_commands.choices(scope=[
        app_commands.Choice(name="伺服器商店（伺服幣）", value="server"),
        app_commands.Choice(name="全域商店（全域幣）", value="global"),
    ])
    @app_commands.autocomplete(item_id=sellable_items_autocomplete)
    async def sell(self, interaction: discord.Interaction, item_id: str, amount: int = 1, scope: str = "server"):
        if amount <= 0:
            await interaction.response.send_message("❌ 數量必須大於 0。", ephemeral=True)
            return

        if not interaction_uses_server_scope(interaction):
            scope = "global"
            guild_id = GLOBAL_GUILD_ID
        else:
            guild_id = interaction.guild.id
            if scope == "global" and not get_allow_global_flow(guild_id):
                await interaction.response.send_message("❌ 此伺服器已關閉伺服幣與全域幣的流通功能，無法使用全域商店。", ephemeral=True)
                return
        user_id = interaction.user.id

        item = get_item_by_id(item_id, guild_id if scope == "server" else 0)
        if not item:
            await interaction.response.send_message("❌ 無效的物品 ID。", ephemeral=True)
            return

        worth = item.get("worth", 0)
        if worth <= 0:
            await interaction.response.send_message("❌ 這個物品無法賣出。", ephemeral=True)
            return

        user_item_count = await get_user_items(guild_id, user_id, item_id)
        if user_item_count < amount:
            await interaction.response.send_message(
                f"❌ 你只有 **{user_item_count}** 個 {item['name']}。",
                ephemeral=True
            )
            return

        removed = await remove_item_from_user(guild_id, user_id, item_id, amount)

        # 檢查有多少是管理員給予的物品
        admin_count = get_admin_item_count(guild_id, user_id, item_id)
        admin_removed = min(admin_count, removed)
        if admin_removed > 0:
            remove_admin_item(guild_id, user_id, item_id, admin_removed)

        currency_name = get_currency_name(guild_id) if scope == "server" else GLOBAL_CURRENCY_NAME
        sell_ratio = get_sell_ratio(guild_id)
        if scope == "server":
            price_per = get_item_sell_price(item_id, guild_id)
        else:
            # 全域商店也要套用折扣
            price_per = round(item.get("worth", 0) * sell_ratio, 2)
        total_price = round(price_per * removed, 2)

        if scope == "server":
            add_balance(guild_id, user_id, total_price)
            # 如果賣出的物品中有管理員給予的，按比例計算並施加嚴重通膨
            if admin_removed > 0:
                admin_price = round(price_per * admin_removed, 2)
                normal_price = total_price - admin_price
                if admin_price > 0:
                    record_sale(guild_id, admin_price, is_admin_item=True)
                if normal_price > 0:
                    record_sale(guild_id, normal_price, is_admin_item=False)
            else:
                record_sale(guild_id, total_price, is_admin_item=False)
        else:
            # 全域幣賣出：禁止賣出管理員給予的物品到全域商店（防止洗錢）
            if admin_removed > 0:
                await interaction.response.send_message(
                    f"❌ 你不能將管理員給予的物品賣到全域商店。\n"
                    f"你有 {admin_removed} 個此物品是管理員給予的，請在伺服器商店賣出。",
                    ephemeral=True
                )
                # 退還物品
                await give_item_to_user(guild_id, user_id, item_id, removed)
                return
            set_global_balance(user_id, get_global_balance(user_id) + total_price)

        embed = discord.Embed(
            title="💰 賣出成功",
            description=f"你賣出了 **{item['name']}** x{removed}！",
            color=0xe67e22
        )
        embed.add_field(name="單價", value=f"{price_per:,.2f} {currency_name}", inline=True)
        embed.add_field(name="總收入", value=f"{total_price:,.2f} {currency_name}", inline=True)

        # 如果有管理員給予的物品被賣出，顯示警告
        if scope == "server" and admin_removed > 0:
            embed.add_field(
                name="⚠️ 管理員物品",
                value=f"其中 {admin_removed} 個為管理員給予\n已觸發嚴重通膨懲罰",
                inline=False
            )

        if scope == "server":
            buy_price = get_item_buy_price(item_id, guild_id)
        else:
            buy_price = item.get("worth", 0)
        embed.set_footer(
            text=f"賣出價為買入價的 {sell_ratio*100:.0f}%（買入: {buy_price:,.2f}）",
        )
        embed.timestamp = datetime.now(timezone.utc)
        sell_guild = guild_id if scope == "server" else GLOBAL_GUILD_ID
        log_transaction(sell_guild, user_id, "賣出物品", total_price, currency_name, f"{item['name']} x{removed}")
        remaining_balance = get_balance(guild_id, user_id) if scope == "server" else get_global_balance(user_id)
        queue_economy_audit_log(
            "sell_item",
            guild_id=sell_guild,
            actor=interaction.user,
            interaction=interaction,
            currency=currency_name,
            amount=total_price,
            balance_before=remaining_balance - total_price,
            balance_after=remaining_balance,
            item_name=item["name"],
            item_amount=removed,
            detail=f"Item sold via {scope} scope. Admin item portion={admin_removed}.",
            color=0xE67E22,
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="shop", description="查看商店")
    async def shop(self, interaction: discord.Interaction):
        if interaction_uses_server_scope(interaction):
            purchasable = [item for item in get_all_items_for_guild(interaction.guild.id) if item.get("worth", 0) > 0]
        else:
            purchasable = [item for item in items if item.get("worth", 0) > 0]
        if not purchasable:
            await interaction.response.send_message("🏪 商店目前沒有任何商品。", ephemeral=True)
            return

        if interaction_uses_server_scope(interaction):
            # 伺服器：顯示兩個商店
            guild_id = interaction.guild.id
            currency_name = get_currency_name(guild_id)
            rate = get_exchange_rate(guild_id)

            allow_flow = get_allow_global_flow(guild_id)
            flow_label = "\n🔓 全域幣流通：已開啟" if allow_flow else "\n🔒 全域幣流通：已關閉"
            desc_parts = [
                f"當前匯率: 1 {currency_name} = {rate:.4f} {GLOBAL_CURRENCY_NAME}",
                f"🏦 伺服器商店 = 伺服幣付款，物品到伺服器背包",
            ]
            if allow_flow:
                desc_parts.append(f"🌐 全域商店 = 全域幣付款，物品到全域背包")
            desc_parts.append(flow_label)
            embed = discord.Embed(
                title="🏪 商店",
                description="\n".join(desc_parts),
                color=0x9b59b6
            )
            for item in purchasable:
                buy_price = get_item_buy_price(item["id"], guild_id)
                sell_price = get_item_sell_price(item["id"], guild_id)
                item_lines = [
                    item.get('description', '無描述'),
                    f"🏦 伺服器商店: **{buy_price:,.2f}** {currency_name}",
                ]
                if allow_flow and not str(item["id"]).startswith("custom_"):
                    item_lines.append(f"🌐 全域商店: **{item['worth']:,.2f}** {GLOBAL_CURRENCY_NAME}")
                item_lines.append(f"💰 賣出: **{sell_price:,.2f}** {currency_name}")
                embed.add_field(
                    name=item["name"],
                    value="\n".join(item_lines),
                    inline=False
                )

            embed.set_footer(
                text=f"{interaction.guild.name} | 賣出價為買入價的 {get_sell_ratio(guild_id)*100:.0f}%",
                icon_url=interaction.guild.icon.url if interaction.guild.icon else None
            )
        else:
            # 全域：只顯示全域商店
            embed = discord.Embed(
                title="🏪 全域商店",
                description=f"🌐 全域商店 = {GLOBAL_CURRENCY_NAME}付款，物品到全域背包",
                color=0x9b59b6
            )
            for item in purchasable:
                embed.add_field(
                    name=item["name"],
                    value=(
                        f"{item.get('description', '無描述')}\n"
                        f"💰 價格: **{item['worth']:,.2f}** {GLOBAL_CURRENCY_NAME}"
                    ),
                    inline=False
                )
            embed.set_footer(text="全域商店")

        # 建立購買 View
        view = ShopView(interaction, purchasable)
        await interaction.response.send_message(embed=embed, view=view)

    @app_commands.command(name="trade", description="與其他用戶交易")
    @app_commands.describe(
        user="交易對象",
        offer_item="你要提供的物品",
        offer_item_amount="提供的物品數量",
        offer_money="你要提供的金額",
        request_item="你想要的物品",
        request_item_amount="想要的物品數量",
        request_money="你想要的金額",
        global_trade="使用全域幣/全域物品交易（跨伺服器）"
    )
    @app_commands.autocomplete(offer_item=get_user_items_autocomplete, request_item=all_items_autocomplete)
    async def trade(self, interaction: discord.Interaction, user: discord.User,
                    offer_item: str = None, offer_item_amount: int = 1,
                    offer_money: float = 0.0,
                    request_item: str = None, request_item_amount: int = 1,
                    request_money: float = 0.0,
                    global_trade: bool = False):
        if user.id == interaction.user.id:
            await interaction.response.send_message("❌ 你不能跟自己交易。", ephemeral=True)
            return
        if user.bot:
            await interaction.response.send_message("❌ 你不能跟機器人交易。", ephemeral=True)
            return
        if not offer_item and offer_money <= 0 and not request_item and request_money <= 0:
            await interaction.response.send_message("❌ 你需要提供或要求至少一樣東西。", ephemeral=True)
            return

        # 全域安裝時強制使用全域交易
        if not interaction_uses_server_scope(interaction):
            global_trade = True

        guild_id = GLOBAL_GUILD_ID if global_trade else interaction.guild.id
        initiator_id = interaction.user.id
        target_id = user.id
        currency_name = GLOBAL_CURRENCY_NAME if global_trade else get_currency_name(guild_id)

        # 驗證發起者的提供
        offer_item_data = None
        if offer_item:
            offer_item_data = get_item_by_id(offer_item)
            if not offer_item_data:
                await interaction.response.send_message("❌ 無效的提供物品。", ephemeral=True)
                return
            initiator_count = await get_user_items(guild_id, initiator_id, offer_item)
            if initiator_count < offer_item_amount:
                await interaction.response.send_message(
                    f"❌ 你只有 {initiator_count} 個 {offer_item_data['name']}。",
                    ephemeral=True
                )
                return

        if offer_money > 0:
            if get_balance(guild_id, initiator_id) < offer_money:
                await interaction.response.send_message(f"❌ 你的 {currency_name} 餘額不足。", ephemeral=True)
                return

        request_item_data = None
        if request_item:
            request_item_data = get_item_by_id(request_item)
            if not request_item_data:
                await interaction.response.send_message("❌ 無效的要求物品。", ephemeral=True)
                return

        # 建構交易 Embed
        embed = discord.Embed(
            title="🤝 交易請求" + (f" {GLOBAL_CURRENCY_EMOJI} 全域" if global_trade else ""),
            description=f"{interaction.user.mention} 想和 {user.mention} 交易",
            color=0xf39c12
        )

        offer_text = ""
        if offer_item_data:
            offer_text += f"📦 {offer_item_data['name']} x{offer_item_amount}\n"
        if offer_money > 0:
            offer_text += f"💰 {offer_money:,.2f} {currency_name}\n"
        embed.add_field(
            name=f"📤 {interaction.user.display_name} 提供",
            value=offer_text or "無",
            inline=True
        )

        request_text = ""
        if request_item_data:
            request_text += f"📦 {request_item_data['name']} x{request_item_amount}\n"
        if request_money > 0:
            request_text += f"💰 {request_money:,.2f} {currency_name}\n"
        embed.add_field(
            name=f"📥 {interaction.user.display_name} 要求",
            value=request_text or "無",
            inline=True
        )

        trade_data = {
            "guild_id": guild_id,
            "initiator_id": initiator_id,
            "target_id": target_id,
            "offer_item": offer_item,
            "offer_item_amount": offer_item_amount,
            "offer_money": offer_money,
            "request_item": request_item,
            "request_item_amount": request_item_amount,
            "request_money": request_money,
            "global_trade": global_trade,
        }

        class TradeView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=120)

            async def on_timeout(self):
                for child in self.children:
                    child.disabled = True
                try:
                    await interaction.edit_original_response(content="⏰ 交易已超時。", view=self)
                except Exception:
                    pass

            @discord.ui.button(label="接受交易", style=discord.ButtonStyle.green, emoji="✅")
            async def accept(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
                if btn_interaction.user.id != target_id:
                    await btn_interaction.response.send_message("❌ 只有交易對象才能接受。", ephemeral=True)
                    return

                td = trade_data
                errors = []

                # 重新驗證雙方資源
                if td["offer_item"]:
                    count = await get_user_items(td["guild_id"], td["initiator_id"], td["offer_item"])
                    if count < td["offer_item_amount"]:
                        errors.append("發起者的物品數量不足")
                if td["offer_money"] > 0:
                    if get_balance(td["guild_id"], td["initiator_id"]) < td["offer_money"]:
                        errors.append("發起者的餘額不足")
                if td["request_item"]:
                    count = await get_user_items(td["guild_id"], td["target_id"], td["request_item"])
                    if count < td["request_item_amount"]:
                        errors.append("你的物品數量不足")
                if td["request_money"] > 0:
                    if get_balance(td["guild_id"], td["target_id"]) < td["request_money"]:
                        errors.append("你的餘額不足")

                if errors:
                    await btn_interaction.response.send_message(
                        "❌ 交易失敗：\n" + "\n".join(f"• {e}" for e in errors),
                        ephemeral=True
                    )
                    return

                # 執行交易
                if td["offer_item"]:
                    await remove_item_from_user(td["guild_id"], td["initiator_id"], td["offer_item"], td["offer_item_amount"])
                    await give_item_to_user(td["guild_id"], td["target_id"], td["offer_item"], td["offer_item_amount"])
                    # 轉移管理員物品標記
                    admin_count = get_admin_item_count(td["guild_id"], td["initiator_id"], td["offer_item"])
                    if admin_count > 0:
                        transferred = min(admin_count, td["offer_item_amount"])
                        remove_admin_item(td["guild_id"], td["initiator_id"], td["offer_item"], transferred)
                        add_admin_item(td["guild_id"], td["target_id"], td["offer_item"], transferred)
                if td["offer_money"] > 0:
                    set_balance(td["guild_id"], td["initiator_id"],
                                get_balance(td["guild_id"], td["initiator_id"]) - td["offer_money"])
                    add_balance(td["guild_id"], td["target_id"], td["offer_money"])
                if td["request_item"]:
                    await remove_item_from_user(td["guild_id"], td["target_id"], td["request_item"], td["request_item_amount"])
                    await give_item_to_user(td["guild_id"], td["initiator_id"], td["request_item"], td["request_item_amount"])
                    # 轉移管理員物品標記
                    admin_count = get_admin_item_count(td["guild_id"], td["target_id"], td["request_item"])
                    if admin_count > 0:
                        transferred = min(admin_count, td["request_item_amount"])
                        remove_admin_item(td["guild_id"], td["target_id"], td["request_item"], transferred)
                        add_admin_item(td["guild_id"], td["initiator_id"], td["request_item"], transferred)
                if td["request_money"] > 0:
                    set_balance(td["guild_id"], td["target_id"],
                                get_balance(td["guild_id"], td["target_id"]) - td["request_money"])
                    add_balance(td["guild_id"], td["initiator_id"], td["request_money"])

                if td["guild_id"] != GLOBAL_GUILD_ID:
                    record_transaction(td["guild_id"])
                    record_transaction(td["guild_id"])  # 兩方交易 = 兩筆經濟活動

                for child in self.children:
                    child.disabled = True
                # 記錄交易紀錄
                trade_currency = get_currency_name(td["guild_id"])
                offer_parts = []
                request_parts = []
                if td["offer_item"]:
                    oi = get_item_by_id(td["offer_item"])
                    offer_parts.append(f"{oi['name'] if oi else td['offer_item']} x{td['offer_item_amount']}")
                if td["offer_money"] > 0:
                    offer_parts.append(f"{td['offer_money']:,.2f} {trade_currency}")
                if td["request_item"]:
                    ri = get_item_by_id(td["request_item"])
                    request_parts.append(f"{ri['name'] if ri else td['request_item']} x{td['request_item_amount']}")
                if td["request_money"] > 0:
                    request_parts.append(f"{td['request_money']:,.2f} {trade_currency}")
                offer_str = ", ".join(offer_parts) or "無"
                request_str = ", ".join(request_parts) or "無"
                if td["offer_money"] > 0:
                    log_transaction(td["guild_id"], td["initiator_id"], "交易支出", -td["offer_money"], trade_currency, f"提供: {offer_str} → 換取: {request_str}")
                if td["request_money"] > 0:
                    log_transaction(td["guild_id"], td["initiator_id"], "交易收入", td["request_money"], trade_currency, f"提供: {offer_str} → 換取: {request_str}")
                if td["request_money"] > 0:
                    log_transaction(td["guild_id"], td["target_id"], "交易支出", -td["request_money"], trade_currency, f"提供: {request_str} → 換取: {offer_str}")
                if td["offer_money"] > 0:
                    log_transaction(td["guild_id"], td["target_id"], "交易收入", td["offer_money"], trade_currency, f"提供: {request_str} → 換取: {offer_str}")

                await btn_interaction.response.edit_message(content="✅ 交易完成！", view=self)
                initiator_after = get_balance(td["guild_id"], td["initiator_id"])
                target_after = get_balance(td["guild_id"], td["target_id"])
                queue_economy_audit_log(
                    "trade_completed",
                    guild_id=td["guild_id"],
                    actor=interaction.user,
                    target=user,
                    interaction=btn_interaction,
                    currency=trade_currency,
                    amount=td["offer_money"] + td["request_money"],
                    balance_before=initiator_after + td["offer_money"] - td["request_money"],
                    balance_after=initiator_after,
                    target_balance_before=target_after - td["offer_money"] + td["request_money"],
                    target_balance_after=target_after,
                    detail=f"Trade completed. Offer={offer_str} | Request={request_str}",
                    color=0xF39C12,
                    extra_fields=[
                        ("Offer", offer_str, False),
                        ("Request", request_str, False),
                        ("Global Trade", str(td.get("global_trade", False)), True),
                    ],
                )
                log(f"{'Global t' if td.get('global_trade') else 'T'}rade between {td['initiator_id']} and {td['target_id']} in guild {td['guild_id']}",
                    module_name="Economy")

            @discord.ui.button(label="拒絕交易", style=discord.ButtonStyle.red, emoji="❌")
            async def decline(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
                if btn_interaction.user.id not in (initiator_id, target_id):
                    await btn_interaction.response.send_message("❌ 只有交易雙方才能取消。", ephemeral=True)
                    return
                for child in self.children:
                    child.disabled = True
                who = "發起者" if btn_interaction.user.id == initiator_id else "對方"
                await btn_interaction.response.edit_message(content=f"❌ 交易已被{who}取消。", view=self)

        await interaction.response.send_message(content=user.mention, embed=embed, view=TradeView(), allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False))

    @app_commands.command(name="leaderboard", description="查看財富排行榜")
    @app_commands.describe(currency="排行類型")
    @app_commands.choices(currency=[
        app_commands.Choice(name="伺服幣", value="server"),
        app_commands.Choice(name="全域幣", value="global"),
        app_commands.Choice(name="總資產", value="total"),
    ])
    async def leaderboard(self, interaction: discord.Interaction, currency: str = "server"):
        # 全域安裝時強制使用全域幣
        if not interaction_uses_server_scope(interaction):
            currency = "global"
        await interaction.response.defer()

        guild_id = interaction.guild.id
        currency_name = get_currency_name(guild_id)
        rate = get_exchange_rate(guild_id)

        if currency == "server":
            all_users = get_all_user_data(guild_id, "economy_balance")
            sorted_users = sorted(
                all_users.items(),
                key=lambda x: x[1].get("economy_balance", 0),
                reverse=True
            )
            title = f"🏆 {currency_name} 排行榜"
            key_name = "economy_balance"
        elif currency == "global":
            all_users = get_all_user_data(GLOBAL_GUILD_ID, "economy_balance")
            sorted_users = sorted(
                all_users.items(),
                key=lambda x: x[1].get("economy_balance", 0),
                reverse=True
            )
            title = f"🏆 {GLOBAL_CURRENCY_NAME} 排行榜"
            key_name = "economy_balance"
        else:
            all_server = get_all_user_data(guild_id, "economy_balance")
            all_global = get_all_user_data(GLOBAL_GUILD_ID, "economy_balance")
            combined = {}
            all_ids = set(all_server.keys()) | set(all_global.keys())
            for uid in all_ids:
                s_bal = all_server.get(uid, {}).get("economy_balance", 0)
                g_bal = all_global.get(uid, {}).get("economy_balance", 0)
                combined[uid] = {"total": s_bal * rate + g_bal}
            sorted_users = sorted(combined.items(), key=lambda x: x[1].get("total", 0), reverse=True)
            title = "🏆 總資產排行榜"
            key_name = "total"

        embed = discord.Embed(title=title, color=0xf1c40f)
        medals = ["🥇", "🥈", "🥉"]

        displayed = 0
        for i, (user_id, data) in enumerate(sorted_users[:15]):
            bal = data.get(key_name, 0)
            if bal <= 0:
                continue

            if currency == "server":
                display = f"{bal:,.2f} {currency_name}"
            elif currency == "global":
                display = f"{bal:,.2f} {GLOBAL_CURRENCY_NAME}"
            else:
                display = f"{bal:,.2f} {GLOBAL_CURRENCY_NAME}"

            medal = medals[displayed] if displayed < 3 else f"**#{displayed+1}**"
            try:
                fetched_user = await bot.fetch_user(user_id)
                name = fetched_user.display_name
            except Exception:
                name = f"用戶 {user_id}"

            embed.add_field(name=f"{medal} {name}", value=display, inline=False)
            displayed += 1
            if displayed >= 10:
                break

        if displayed == 0:
            embed.description = "目前沒有任何用戶有餘額。"

        embed.set_footer(
            text=interaction.guild.name,
            icon_url=interaction.guild.icon.url if interaction.guild.icon else None
        )
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="info", description="查看伺服器經濟資訊")
    @app_commands.guild_only()
    async def info(self, interaction: discord.Interaction):
        if not interaction_uses_server_scope(interaction):
            await interaction.response.send_message("❌ 這個指令只能在伺服器中使用。", ephemeral=True)
            return
        guild_id = interaction.guild.id
        rate = get_exchange_rate(guild_id)
        currency_name = get_currency_name(guild_id)
        total_supply = get_total_supply(guild_id)
        admin_injected = get_admin_injected(guild_id)
        tx_count = get_transaction_count(guild_id)
        daily_amount = get_daily_amount(guild_id)
        sell_ratio = get_sell_ratio(guild_id)

        # 經濟健康度指標
        if rate >= 1.5:
            health = "🟢 非常健康（強勢貨幣）"
        elif rate >= 1.0:
            health = "🟢 健康"
        elif rate >= 0.7:
            health = "🟡 普通"
        elif rate >= 0.4:
            health = "🟠 通膨中"
        elif rate >= 0.1:
            health = "🔴 嚴重通膨"
        else:
            health = "💀 經濟崩潰"

        # 管理員濫權指標
        if total_supply > 0:
            admin_ratio = admin_injected / total_supply * 100
        else:
            admin_ratio = 0

        if admin_ratio > 50:
            admin_indicator = "🔴 嚴重濫權"
        elif admin_ratio > 20:
            admin_indicator = "🟠 中度干預"
        elif admin_ratio > 5:
            admin_indicator = "🟡 輕度干預"
        else:
            admin_indicator = "🟢 正常"

        embed = discord.Embed(
            title=f"📊 {interaction.guild.name} 經濟報告",
            color=0x3498db
        )
        embed.add_field(name="💵 貨幣名稱", value=currency_name, inline=True)
        embed.add_field(
            name="💱 匯率",
            value=f"1 {currency_name} = {rate:.4f} {GLOBAL_CURRENCY_NAME}",
            inline=True
        )
        embed.add_field(name="📈 經濟健康度", value=health, inline=True)
        embed.add_field(
            name="💰 貨幣總供給",
            value=f"{total_supply:,.2f} {currency_name}",
            inline=True
        )
        embed.add_field(
            name="🔧 管理員注入",
            value=f"{admin_injected:,.2f}（{admin_ratio:.1f}%）\n{admin_indicator}",
            inline=True
        )
        embed.add_field(name="📊 交易次數", value=f"{tx_count:,}", inline=True)
        embed.add_field(name="📅 每日獎勵", value=f"{daily_amount:,} {currency_name}", inline=True)
        embed.add_field(name="🏪 賣出比率", value=f"{sell_ratio*100:.0f}%", inline=True)

        embed.add_field(
            name="ℹ️ 匯率影響因素",
            value=(
                "**📉 通膨（貶值）因素：**\n"
                "• 管理員用 `/itemmod give` 送出物品\n"
                "• 每日/每小時獎勵導致貨幣增發\n"
                "• 賣出物品給商店（新幣進入流通）\n\n"
                "**📈 通縮（升值）因素：**\n"
                "• 從商店購買物品（貨幣被銷毀）\n"
                "• 玩家間交易（手續費銷毀貨幣）\n"
                "• 兌換貨幣（手續費銷毀）"
            ),
            inline=False
        )

        embed.set_footer(
            text=interaction.guild.name,
            icon_url=interaction.guild.icon.url if interaction.guild.icon else None
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="adminitems", description="查看你擁有的管理員給予物品")
    async def adminitems(self, interaction: discord.Interaction):
        if not interaction_uses_server_scope(interaction):
            await interaction.response.send_message("❌ 此指令只能在伺服器中使用。", ephemeral=True)
            return

        guild_id = interaction.guild.id
        user_id = interaction.user.id
        admin_items = get_user_data(guild_id, user_id, "admin_items", {})

        if not admin_items:
            await interaction.response.send_message("✅ 你沒有任何管理員給予的物品。", ephemeral=True)
            return

        embed = discord.Embed(
            title="⚠️ 管理員給予的物品",
            description="這些物品由管理員直接給予，受到以下限制：",
            color=0xe74c3c
        )

        total_value = 0
        for item_id, count in admin_items.items():
            if count <= 0:
                continue
            item = get_item_by_id(item_id, guild_id)
            if item:
                worth = item.get("worth", 0)
                total_value += worth * count
                embed.add_field(
                    name=f"{item['name']} x{count}",
                    value=f"價值: {worth:,.2f} x {count} = {worth * count:,.2f}",
                    inline=False
                )

        embed.add_field(
            name="📊 總價值",
            value=f"{total_value:,.2f} {get_currency_name(guild_id)}",
            inline=False
        )

        embed.add_field(
            name="🚫 限制說明",
            value=(
                "• 賣出時會觸發嚴重通膨懲罰\n"
                "• 無法賣到全域商店\n"
                "• 支票無法兌現\n"
                "• 交易時會轉移管理員標記"
            ),
            inline=False
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="history", description="查看個人交易紀錄")
    @app_commands.describe(scope="查看範圍", page="頁數")
    @app_commands.choices(scope=[
        app_commands.Choice(name="伺服器", value="server"),
        app_commands.Choice(name="全域", value="global"),
    ])
    async def history(self, interaction: discord.Interaction, scope: str = None, page: int = 1):
        user_id = interaction.user.id
        if scope is None:
            scope = "server" if interaction_uses_server_scope(interaction) else "global"
        if scope == "global" or not interaction_uses_server_scope(interaction):
            guild_id = GLOBAL_GUILD_ID
            scope_name = "全域"
        else:
            guild_id = interaction.guild.id
            scope_name = interaction.guild.name

        history_data = get_user_data(guild_id, user_id, "economy_history", [])
        if not history_data:
            await interaction.response.send_message(f"📜 你在 {scope_name} 沒有任何交易紀錄。", ephemeral=True)
            return

        # 由新到舊排序
        history_data = list(reversed(history_data))
        per_page = 10
        total_pages = max(1, (len(history_data) + per_page - 1) // per_page)
        page = max(1, min(page, total_pages))
        start = (page - 1) * per_page
        end = start + per_page
        page_data = history_data[start:end]

        embed = discord.Embed(
            title=f"📜 {interaction.user.display_name} 的交易紀錄（{scope_name}）",
            color=0x3498db
        )

        for entry in page_data:
            tx_type = entry.get("type", "未知")
            amount = entry.get("amount", 0)
            currency = entry.get("currency", "")
            detail = entry.get("detail", "")
            tx_time = entry.get("time", "")

            # 格式化時間為 Discord 時間戳
            try:
                dt = datetime.fromisoformat(tx_time)
                timestamp = int(dt.timestamp())
                time_str = f"<t:{timestamp}:R>"
            except Exception:
                time_str = tx_time

            # 金額顯示
            if amount >= 0:
                amount_str = f"+{amount:,.2f}"
                emoji = "📈"
            else:
                amount_str = f"{amount:,.2f}"
                emoji = "📉"

            name = f"{emoji} {tx_type}"
            value = f"{amount_str} {currency}"
            if detail:
                value += f"\n{detail}"
            value += f"\n{time_str}"

            embed.add_field(name=name, value=value, inline=False)

        embed.set_footer(text=f"第 {page}/{total_pages} 頁 · 共 {len(history_data)} 筆紀錄")
        await interaction.response.send_message(embed=embed, ephemeral=True)


asyncio.run(bot.add_cog(Economy()))


# ==================== Economy Mod Cog ====================

class ConfirmGlobalModeView(discord.ui.View):
    def __init__(self, guild_id: int, actor_id: int):
        super().__init__(timeout=180)
        self.guild_id = guild_id
        self.actor_id = actor_id

    async def _reject_if_not_actor(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.actor_id:
            await interaction.response.send_message("只有發起指令的人可以確認這次切換。", ephemeral=True)
            return True
        return False

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True

    @discord.ui.button(label="確認改為全域", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await self._reject_if_not_actor(interaction):
            return

        if is_global_mode_enabled(self.guild_id):
            await interaction.response.send_message("這個伺服器已經是全域模式了。", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        migration = await migrate_guild_economy_to_global(self.guild_id)
        set_global_mode_enabled(self.guild_id, True)

        queue_economy_audit_log(
            "global_mode_enabled",
            guild_id=self.guild_id,
            actor=interaction.user,
            interaction=interaction,
            currency=GLOBAL_CURRENCY_NAME,
            amount=migration["global_added"],
            detail=(
                f"Forced global mode enabled. Users={migration['affected_users']}, "
                f"sold_items={migration['sold_item_units']}, server_balance={migration['server_balance_converted']:,.2f}, "
                f"item_value={migration['server_item_value']:,.2f}, rate={migration['exchange_rate']:.6f}"
            ),
            color=0xE74C3C,
        )

        embed = discord.Embed(
            title="已切換為全域模式",
            description="這個伺服器之後的經濟、物品、dsize 範圍都會強制走全域。",
            color=0xE74C3C,
        )
        embed.add_field(name="影響人數", value=str(migration["affected_users"]), inline=True)
        embed.add_field(name="賣出物品數", value=str(migration["sold_item_units"]), inline=True)
        embed.add_field(name="匯率", value=f"1 伺服幣 = {migration['exchange_rate']:.4f} 全域幣", inline=False)
        embed.add_field(name="原伺服幣", value=f"{migration['server_balance_converted']:,.2f}", inline=True)
        embed.add_field(name="物品折現", value=f"{migration['server_item_value']:,.2f}", inline=True)
        embed.add_field(name="轉入全域幣", value=f"{migration['global_added']:,.2f} {GLOBAL_CURRENCY_NAME}", inline=False)
        for child in self.children:
            child.disabled = True
        await interaction.followup.send(embed=embed, ephemeral=True)
        self.stop()

    @discord.ui.button(label="取消", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await self._reject_if_not_actor(interaction):
            return
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="已取消切換全域模式。", embed=None, view=self)
        self.stop()

@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
class EconomyMod(commands.GroupCog, name="economymod", description="經濟系統管理指令"):
    def __init__(self):
        super().__init__()

    # @app_commands.command(name="give", description="給予用戶伺服幣（會嚴重通膨）")
    # @app_commands.describe(user="目標用戶", amount="金額")
    # async def give_money(self, interaction: discord.Interaction, user: discord.User, amount: float):
    #     if amount <= 0:
    #         await interaction.response.send_message("❌ 金額必須大於 0。", ephemeral=True)
    #         return
    #     elif amount > 1_000_000:
    #         await interaction.response.send_message("❌ 金額不能超過 1,000,000。", ephemeral=True)
    #         return
    #     if user.bot:
    #         await interaction.response.send_message("❌ 不能給機器人金錢。", ephemeral=True)
    #         return

    #     guild_id = interaction.guild.id
    #     currency_name = get_currency_name(guild_id)

    #     # 顯示警告
    #     old_rate = get_exchange_rate(guild_id)
    #     add_balance(guild_id, user.id, amount)
    #     record_admin_injection(guild_id, amount)
    #     new_rate = get_exchange_rate(guild_id)

    #     rate_change_percent = ((new_rate - old_rate) / old_rate * 100) if old_rate > 0 else 0

    #     await interaction.response.send_message(
    #         f"✅ 已給予 {user.display_name} **{amount:,.2f}** {currency_name}。\n"
    #         f"⚠️ **警告：管理員注入導致貨幣貶值 {abs(rate_change_percent):.2f}%**\n"
    #         f"匯率：{old_rate:.6f} → {new_rate:.6f}\n"
    #         f"-# 建議使用每日獎勵或活動系統發放貨幣，而非直接給予"
    #     )
    #     log(f"Admin {interaction.user} gave {amount} server currency to {user} in guild {guild_id}, rate {old_rate:.6f} -> {new_rate:.6f}",
    #         module_name="Economy", user=interaction.user, guild=interaction.guild)

    # @app_commands.command(name="remove", description="移除用戶伺服幣")
    # @app_commands.describe(user="目標用戶", amount="金額")
    # async def remove_money(self, interaction: discord.Interaction, user: discord.User, amount: float):
    #     if amount <= 0:
    #         await interaction.response.send_message("❌ 金額必須大於 0。", ephemeral=True)
    #         return

    #     guild_id = interaction.guild.id
    #     currency_name = get_currency_name(guild_id)
    #     bal = get_balance(guild_id, user.id)
    #     removed = min(bal, amount)
    #     set_balance(guild_id, user.id, bal - removed)
    #     adjust_supply(guild_id, -removed)

    #     # 移除貨幣時，按比例減少管理員注入記錄（避免懲罰累積）
    #     admin_injected = get_admin_injected(guild_id)
    #     total_supply = get_total_supply(guild_id)
    #     if total_supply > 0 and admin_injected > 0:
    #         # 按移除比例減少管理員注入記錄
    #         reduction = min(admin_injected, removed)
    #         set_server_config(guild_id, "economy_admin_injected", max(0, admin_injected - reduction))

    #     await interaction.response.send_message(
    #         f"✅ 已移除 {user.display_name} 的 **{removed:,.2f}** {currency_name}。"
    #     )
    #     log(f"Admin {interaction.user} removed {removed} server currency from {user} in guild {guild_id}",
    #         module_name="Economy", user=interaction.user, guild=interaction.guild)

    # @app_commands.command(name="setrate", description="手動設定匯率")
    # @app_commands.describe(rate="新匯率（1 伺服幣 = X 全域幣）")
    # async def setrate(self, interaction: discord.Interaction, rate: float):
    #     if rate < EXCHANGE_RATE_MIN or rate > EXCHANGE_RATE_MAX:
    #         await interaction.response.send_message(
    #             f"❌ 匯率必須在 {EXCHANGE_RATE_MIN} 到 {EXCHANGE_RATE_MAX} 之間。",
    #             ephemeral=True
    #         )
    #         return

    #     guild_id = interaction.guild.id
    #     old_rate = get_exchange_rate(guild_id)
    #     set_exchange_rate(guild_id, rate)

    #     await interaction.response.send_message(
    #         f"✅ 匯率已從 **{old_rate:.4f}** 更改為 **{rate:.4f}**。",
    #         ephemeral=True
    #     )
    #     log(f"Admin {interaction.user} set rate {old_rate} -> {rate} in guild {guild_id}",
    #         module_name="Economy", user=interaction.user, guild=interaction.guild)

    # @app_commands.command(name="clearadmin", description="清除用戶的管理員物品標記（不影響物品本身）")
    # @app_commands.describe(user="目標用戶", item_id="物品ID（留空清除所有）")
    # @app_commands.autocomplete(item_id=all_items_autocomplete)
    # async def clearadmin(self, interaction: discord.Interaction, user: discord.User, item_id: str = None):
    #     guild_id = interaction.guild.id
    #     admin_items = get_user_data(guild_id, user.id, "admin_items", {})

    #     if not admin_items:
    #         await interaction.response.send_message(f"✅ {user.display_name} 沒有任何管理員物品標記。", ephemeral=True)
    #         return

    #     if item_id:
    #         # 清除特定物品的標記
    #         if item_id in admin_items:
    #             count = admin_items[item_id]
    #             del admin_items[item_id]
    #             set_user_data(guild_id, user.id, "admin_items", admin_items)
    #             item = get_item_by_id(item_id, guild_id)
    #             item_name = item['name'] if item else item_id
    #             await interaction.response.send_message(
    #                 f"✅ 已清除 {user.display_name} 的 **{item_name}** x{count} 的管理員標記。\n"
    #                 f"-# 物品本身不受影響，但現在可以正常交易和賣出",
    #                 ephemeral=True
    #             )
    #         else:
    #             await interaction.response.send_message(f"❌ {user.display_name} 沒有該物品的管理員標記。", ephemeral=True)
    #     else:
    #         # 清除所有標記
    #         total_items = sum(admin_items.values())
    #         set_user_data(guild_id, user.id, "admin_items", {})
    #         await interaction.response.send_message(
    #             f"✅ 已清除 {user.display_name} 的所有管理員物品標記（共 {total_items} 個物品）。\n"
    #             f"-# 物品本身不受影響，但現在可以正常交易和賣出",
    #             ephemeral=True
    #         )

    #     log(f"Admin {interaction.user} cleared admin item markers for {user} in guild {guild_id}",
    #         module_name="Economy", user=interaction.user, guild=interaction.guild)

    @app_commands.command(name="global-mode", description="切換這個伺服器是否強制使用全域經濟/物品/dsize")
    @app_commands.describe(enabled="True = 強制全域，False = 恢復伺服器模式")
    async def global_mode(self, interaction: discord.Interaction, enabled: bool):
        guild_id = interaction.guild.id
        current = is_global_mode_enabled(guild_id)

        if enabled == current:
            status = "全域模式" if enabled else "伺服器模式"
            await interaction.response.send_message(f"目前已經是{status}。", ephemeral=True)
            return

        if not enabled:
            set_global_mode_enabled(guild_id, False)
            queue_economy_audit_log(
                "global_mode_disabled",
                guild_id=guild_id,
                actor=interaction.user,
                interaction=interaction,
                detail="Forced global mode disabled.",
                color=0x3498DB,
            )
            await interaction.response.send_message(
                "已關閉全域模式。之後這個伺服器會恢復使用伺服器經濟、物品與 dsize 範圍。",
                ephemeral=True,
            )
            return

        warning = discord.Embed(
            title="警告：即將改為全域模式",
            description=(
                "這會讓這個伺服器後續所有經濟、物品、dsize 的 guild 判斷都強制視為全域。\n"
                "系統也會自動把目前伺服器內所有使用者的物品賣掉換成伺服幣，再依目前匯率轉成全域幣。"
            ),
            color=0xE67E22,
        )
        warning.add_field(name="會發生的事", value="伺服器物品清空、伺服幣清空、轉入全域幣", inline=False)
        warning.add_field(name="不會自動還原", value="切回 False 只會恢復未來判斷，不會把已搬走的資料搬回來", inline=False)
        await interaction.response.send_message(
            embed=warning,
            view=ConfirmGlobalModeView(guild_id, interaction.user.id),
            ephemeral=True,
        )

    @app_commands.command(name="toggle-flow", description="切換是否允許伺服幣與全域幣流通（兌換、全域商店等）")
    async def toggle_flow(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        current = get_allow_global_flow(guild_id)
        new_value = not current
        set_allow_global_flow(guild_id, new_value)
        status = "🔓 已開啟" if new_value else "🔒 已關閉"
        desc = (
            "用戶可以使用兌換、全域商店買賣及支票兌現功能。"
            if new_value else
            "用戶無法使用兌換、全域商店買賣及支票兌現功能。"
        )
        await interaction.response.send_message(
            f"✅ 全域幣流通已切換為 **{status}**\n{desc}",
            ephemeral=True
        )
        log(f"Admin {interaction.user} toggled global flow to {new_value} in guild {guild_id}",
            module_name="Economy", user=interaction.user, guild=interaction.guild)

    @app_commands.command(name="setname", description="設定伺服器貨幣名稱")
    @app_commands.describe(name="新的貨幣名稱")
    async def setname(self, interaction: discord.Interaction, name: str):
        if len(name) > 20:
            await interaction.response.send_message("❌ 貨幣名稱不能超過 20 個字元。", ephemeral=True)
            return

        guild_id = interaction.guild.id
        set_server_config(guild_id, "economy_currency_name", name)
        await interaction.response.send_message(f"✅ 貨幣名稱已更改為 **{name}**。", ephemeral=True)

    # @app_commands.command(name="setdaily", description="設定每日獎勵金額")
    # @app_commands.describe(amount="每日獎勵金額")
    # async def setdaily(self, interaction: discord.Interaction, amount: int):
    #     if amount < 0 or amount > 1000:
    #         await interaction.response.send_message("❌ 金額必須在 0 到 1,000 之間。", ephemeral=True)
    #         return

    #     guild_id = interaction.guild.id
    #     set_server_config(guild_id, "economy_daily_amount", amount)
    #     await interaction.response.send_message(f"✅ 每日獎勵已設定為 **{amount:,}**。", ephemeral=True)

    # @app_commands.command(name="setsellratio", description="設定物品賣出比率")
    # @app_commands.describe(ratio="賣出比率（0.1-1.0，例如 0.7 = 70%）")
    # async def setsellratio(self, interaction: discord.Interaction, ratio: float):
    #     if ratio < 0.1 or ratio > 1.0:
    #         await interaction.response.send_message("❌ 比率必須在 0.1 到 1.0 之間。", ephemeral=True)
    #         return

    #     guild_id = interaction.guild.id
    #     set_server_config(guild_id, "economy_sell_ratio", ratio)
    #     await interaction.response.send_message(f"✅ 賣出比率已設定為 **{ratio*100:.0f}%**。", ephemeral=True)

    @app_commands.command(name="info", description="詳細經濟管理面板")
    async def mod_info(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        rate = get_exchange_rate(guild_id)
        currency_name = get_currency_name(guild_id)
        total_supply = get_total_supply(guild_id)
        admin_injected = get_admin_injected(guild_id)
        tx_count = get_transaction_count(guild_id)

        # 計算所有用戶的餘額總和
        all_users = get_all_user_data(guild_id, "economy_balance")
        actual_supply = sum(d.get("economy_balance", 0) for d in all_users.values())

        # 計算管理員物品的總價值
        admin_item_value = 0
        for uid in all_users.keys():
            admin_items = get_user_data(guild_id, uid, "admin_items", {})
            for item_id, count in admin_items.items():
                item = get_item_by_id(item_id, guild_id)
                if item:
                    admin_item_value += item.get("worth", 0) * count

        embed = discord.Embed(
            title=f"🔧 {interaction.guild.name} 經濟管理面板",
            color=0xe74c3c
        )
        embed.add_field(name="匯率", value=f"{rate:.6f}", inline=True)
        embed.add_field(name="追蹤供給量", value=f"{total_supply:,.2f}", inline=True)
        embed.add_field(name="實際供給量", value=f"{actual_supply:,.2f}", inline=True)
        embed.add_field(name="管理員注入（貨幣）", value=f"{admin_injected:,.2f}", inline=True)
        embed.add_field(name="管理員物品價值", value=f"{admin_item_value:,.2f}", inline=True)
        embed.add_field(name="交易次數", value=f"{tx_count:,}", inline=True)
        embed.add_field(name="用戶數", value=f"{len(all_users):,}", inline=True)
        allow_flow = get_allow_global_flow(guild_id)
        embed.add_field(name="全域幣流通", value="🔓 已開啟" if allow_flow else "🔒 已關閉", inline=True)
        embed.add_field(name="全域模式", value="🌐 已啟用" if is_global_mode_enabled(guild_id) else "🏦 已關閉", inline=True)

        # 濫權指標
        if total_supply > 0:
            admin_ratio = (admin_injected + admin_item_value) / total_supply * 100
            if admin_ratio > 50:
                abuse_indicator = "🔴 嚴重濫權"
            elif admin_ratio > 20:
                abuse_indicator = "🟠 中度干預"
            elif admin_ratio > 5:
                abuse_indicator = "🟡 輕度干預"
            else:
                abuse_indicator = "🟢 正常"
            embed.add_field(
                name="⚠️ 管理員干預程度",
                value=f"{admin_ratio:.1f}% - {abuse_indicator}",
                inline=True
            )

        if abs(actual_supply - total_supply) > 0.01:
            embed.add_field(
                name="⚠️ 供給差異",
                value=f"{actual_supply - total_supply:,.2f}（正常應為 0）",
                inline=False
            )

        embed.add_field(
            name="💡 提示",
            value=(
                "• 管理員給予的物品/金錢會被追蹤\n"
                "• 賣出管理員物品會觸發嚴重通膨\n"
                "• 管理員物品無法兌現為全域幣\n"
                "• 建議使用活動系統而非直接給予"
            ),
            inline=False
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # @app_commands.command(name="reset", description="⚠️ 重置伺服器經濟系統")
    # async def reset(self, interaction: discord.Interaction):
    #     guild_id = interaction.guild.id

    #     class ResetConfirmView(discord.ui.View):
    #         def __init__(self):
    #             super().__init__(timeout=30)

    #         @discord.ui.button(label="確認重置", style=discord.ButtonStyle.danger, emoji="⚠️")
    #         async def confirm(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
    #             if btn_interaction.user.id != interaction.user.id:
    #                 await btn_interaction.response.send_message("❌ 只有發起者才能確認。", ephemeral=True)
    #                 return

    #             set_server_config(guild_id, "economy_exchange_rate", DEFAULT_EXCHANGE_RATE)
    #             set_server_config(guild_id, "economy_total_supply", 0)
    #             set_server_config(guild_id, "economy_admin_injected", 0)
    #             set_server_config(guild_id, "economy_transaction_count", 0)

    #             all_users = get_all_user_data(guild_id, "economy_balance")
    #             for uid in all_users:
    #                 set_user_data(guild_id, uid, "economy_balance", 0)
    #                 set_user_data(guild_id, uid, "economy_last_daily", 0)
    #                 set_user_data(guild_id, uid, "economy_daily_streak", 0)

    #             for child in self.children:
    #                 child.disabled = True
    #             await btn_interaction.response.edit_message(content="✅ 經濟系統已重置。", view=self)
    #             log(f"Admin {interaction.user} reset economy for guild {guild_id}",
    #                 module_name="Economy", user=interaction.user, guild=interaction.guild)

    #         @discord.ui.button(label="取消", style=discord.ButtonStyle.secondary)
    #         async def cancel(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
    #             for child in self.children:
    #                 child.disabled = True
    #             await btn_interaction.response.edit_message(content="❌ 已取消重置。", view=self)

    #     await interaction.response.send_message(
    #         "⚠️ **警告：** 這將重置所有經濟數據，包括所有用戶餘額、匯率等。此操作不可逆！",
    #         view=ResetConfirmView(),
    #         ephemeral=True
    #     )


asyncio.run(bot.add_cog(EconomyMod()))


@bot.command(name="dev-economyhistory", description="查看用戶的經濟交易紀錄", aliases=["deh"])
@is_owner()
async def dev_economy_history(ctx, user: discord.User, scope: str = "server", server_id: int = None):
    scope = (scope or "server").lower()
    if scope == "global":
        guild_id = GLOBAL_GUILD_ID
    elif scope == "server":
        if server_id:
            guild_id = server_id
        elif ctx.guild:
            guild_id = ctx.guild.id
        else:
            await ctx.send("❌ 請提供伺服器ID或在伺服器中使用此指令。")
            return
    else:
        await ctx.send("❌ 範圍必須是 'server' 或 'global'。")
        return

    history_data = get_user_data(guild_id, user.id, "economy_history", [])
    if not history_data:
        await ctx.send(f"📜 用戶 {user} 在 {scope} 沒有任何交易紀錄。")
        return

    history_data = list(reversed(history_data))
    lines = []
    for entry in history_data:
        tx_type = entry.get("type", "未知")
        amount = entry.get("amount", 0)
        currency = entry.get("currency", "")
        detail = entry.get("detail", "")
        tx_time = entry.get("time", "")
        lines.append(f"{tx_time} | {tx_type} | {amount} {currency} | {detail}")

    # 分批發送訊息
    batch_size = 20
    for i in range(0, len(lines), batch_size):
        batch = lines[i:i+batch_size]
        await ctx.send(f"```{chr(10).join(batch)}```")


@bot.command(name="dev-economygive", description="開發者直接加錢給用戶", aliases=["deg", "degive"])
@is_owner()
async def dev_economy_give(ctx, user: discord.User, amount: float, scope: str = "server", server_id: int = None):
    if amount <= 0:
        await ctx.send("❌ 金額必須大於 0。")
        return

    scope = (scope or "server").lower()
    if scope == "global":
        guild_id = GLOBAL_GUILD_ID
        before = get_global_balance(user.id)
        set_global_balance(user.id, before + amount)
        after = get_global_balance(user.id)
        currency_name = GLOBAL_CURRENCY_NAME
    elif scope == "server":
        if server_id:
            guild_id = server_id
        elif ctx.guild:
            guild_id = ctx.guild.id
        else:
            await ctx.send("❌ 請提供伺服器ID或在伺服器中使用此指令。")
            return

        currency_name = get_currency_name(guild_id)
        before = get_balance(guild_id, user.id)
        add_balance(guild_id, user.id, amount)
        after = get_balance(guild_id, user.id)
    else:
        await ctx.send("❌ 範圍必須是 'server' 或 'global'。")
        return

    actual_added = round(after - before, 2)
    log_transaction(
        guild_id,
        user.id,
        "開發者加錢",
        actual_added,
        currency_name,
        f"操作者: {ctx.author} ({ctx.author.id})"
    )

    queue_economy_audit_log(
        "dev_give",
        guild_id=guild_id,
        actor=ctx.author,
        target=user,
        ctx=ctx,
        currency=currency_name,
        amount=actual_added,
        balance_before=before,
        balance_after=after,
        detail=f"Developer give in {scope} scope.",
        color=0x16A085,
    )
    await ctx.send(
        f"✅ 已為 {user} 增加 **{actual_added:,.2f}** {currency_name}（{scope}）。\n"
        f"餘額：{before:,.2f} → {after:,.2f}"
    )


@bot.command(name="dev-economyremove", description="開發者直接扣錢給用戶", aliases=["der", "deremove"])
@is_owner()
async def dev_economy_remove(ctx, user: discord.User, amount: float, scope: str = "server", server_id: int = None):
    if amount <= 0:
        await ctx.send("❌ 金額必須大於 0。")
        return

    scope = (scope or "server").lower()
    if scope == "global":
        guild_id = GLOBAL_GUILD_ID
        currency_name = GLOBAL_CURRENCY_NAME
        before = get_global_balance(user.id)
        removed = min(before, amount)
        set_global_balance(user.id, before - removed)
        after = get_global_balance(user.id)
    elif scope == "server":
        if server_id:
            guild_id = server_id
        elif ctx.guild:
            guild_id = ctx.guild.id
        else:
            await ctx.send("❌ 請提供伺服器ID或在伺服器中使用此指令。")
            return

        currency_name = get_currency_name(guild_id)
        before = get_balance(guild_id, user.id)
        removed = min(before, amount)
        set_balance(guild_id, user.id, before - removed)
        adjust_supply(guild_id, -removed)
        after = get_balance(guild_id, user.id)
    else:
        await ctx.send("❌ 範圍必須是 'server' 或 'global'。")
        return

    if removed > 0:
        log_transaction(
            guild_id,
            user.id,
            "開發者扣錢",
            -removed,
            currency_name,
            f"操作者: {ctx.author} ({ctx.author.id})"
        )

    queue_economy_audit_log(
        "dev_remove",
        guild_id=guild_id,
        actor=ctx.author,
        target=user,
        ctx=ctx,
        currency=currency_name,
        amount=removed,
        balance_before=before,
        balance_after=after,
        detail=f"Developer remove in {scope} scope.",
        color=0xC0392B,
    )
    await ctx.send(
        f"✅ 已從 {user} 扣除 **{removed:,.2f}** {currency_name}（{scope}）。\n"
        f"餘額：{before:,.2f} → {after:,.2f}"
    )


@bot.command(name="dev-economyset", description="開發者直接設定用戶餘額", aliases=["des", "deset"])
@is_owner()
async def dev_economy_set(ctx, user: discord.User, target_amount: float, scope: str = "server", server_id: int = None):
    if target_amount < 0:
        await ctx.send("❌ 目標餘額不能小於 0。")
        return

    scope = (scope or "server").lower()
    if scope == "global":
        guild_id = GLOBAL_GUILD_ID
        currency_name = GLOBAL_CURRENCY_NAME
        before = get_global_balance(user.id)
        set_global_balance(user.id, target_amount)
        after = get_global_balance(user.id)
    elif scope == "server":
        if server_id:
            guild_id = server_id
        elif ctx.guild:
            guild_id = ctx.guild.id
        else:
            await ctx.send("❌ 請提供伺服器ID或在伺服器中使用此指令。")
            return

        currency_name = get_currency_name(guild_id)
        before = get_balance(guild_id, user.id)
        set_balance(guild_id, user.id, target_amount)
        adjust_supply(guild_id, target_amount - before)
        after = get_balance(guild_id, user.id)
    else:
        await ctx.send("❌ 範圍必須是 'server' 或 'global'。")
        return

    delta = round(after - before, 2)
    if delta != 0:
        log_transaction(
            guild_id,
            user.id,
            "開發者設置餘額",
            delta,
            currency_name,
            f"操作者: {ctx.author} ({ctx.author.id})"
        )

    delta_text = f"+{delta:,.2f}" if delta >= 0 else f"{delta:,.2f}"
    queue_economy_audit_log(
        "dev_set",
        guild_id=guild_id,
        actor=ctx.author,
        target=user,
        ctx=ctx,
        currency=currency_name,
        amount=delta,
        balance_before=before,
        balance_after=after,
        detail=f"Developer set balance in {scope} scope.",
        color=0x8E44AD,
    )
    await ctx.send(
        f"✅ 已將 {user} 的餘額設為 **{after:,.2f}** {currency_name}（{scope}）。\n"
        f"變動：{delta_text} | 餘額：{before:,.2f} → {after:,.2f}"
    )


def make_cheque_use_callback(item_id: str, worth: int):
    """產生支票兌現用的 callback，使用後扣除 1 張支票並將 worth 加入餘額（依匯率轉換至伺服幣或直接加全域幣）。"""

    async def callback(interaction: discord.Interaction):
        guild_id = getattr(interaction, "guild_id", 0)
        user_id = interaction.user.id

        # 檢查是否為管理員給予的支票
        admin_count = get_admin_item_count(guild_id, user_id, item_id)
        if admin_count > 0:
            await interaction.response.send_message(
                "❌ 這張支票是管理員給予的，無法兌現。\n"
                "管理員給予的物品不能轉換為貨幣，以防止經濟系統被濫用。",
                ephemeral=True
            )
            return

        # 伺服器背包中兌現支票屬於全域幣流通，需檢查開關
        if guild_id and guild_id != GLOBAL_GUILD_ID and not get_allow_global_flow(guild_id):
            await interaction.response.send_message("❌ 此伺服器已關閉伺服幣與全域幣的流通功能，無法兌現支票。", ephemeral=True)
            return

        removed = await remove_item_from_user(guild_id, user_id, item_id, 1)
        if removed < 1:
            await interaction.response.send_message("你沒有這張支票。", ephemeral=True)
            return

        # 支票面額是全域幣，兌現到伺服器時需依匯率轉換，避免套利洗錢
        if guild_id and guild_id != GLOBAL_GUILD_ID:
            rate = get_exchange_rate(guild_id)
            payout = round(worth / rate, 2)
        else:
            payout = float(worth)
        balance_before = get_balance(guild_id, user_id)
        add_balance(guild_id, user_id, payout)
        currency_name = get_currency_name(guild_id)
        queue_economy_audit_log(
            "cheque_cashout",
            guild_id=guild_id,
            actor=interaction.user,
            interaction=interaction,
            currency=currency_name,
            amount=payout,
            balance_before=balance_before,
            balance_after=get_balance(guild_id, user_id),
            rate_before=(rate if guild_id and guild_id != GLOBAL_GUILD_ID else None),
            rate_after=(get_exchange_rate(guild_id) if guild_id and guild_id != GLOBAL_GUILD_ID else None),
            item_name=item_id,
            item_amount=1,
            detail=f"Cheque redeemed. Face value={worth}.",
            color=0x1ABC9C,
        )
        await interaction.response.send_message(
            f"你兌現了支票，獲得 **{payout:,.2f}** {currency_name}。",
            ephemeral=True,
        )

    return callback


economy_items = [
    {
        "id": "cheque_100",
        "name": "100元支票",
        "description": "這是一張100元支票，可以用來支付給其他用戶。",
        "worth": 0,
        "callback": make_cheque_use_callback("cheque_100", 100),
    },
    {
        "id": "cheque_500",
        "name": "500元支票",
        "description": "這是一張500元支票，可以用來支付給其他用戶。",
        "worth": 0,
        "callback": make_cheque_use_callback("cheque_500", 500),
    },
    {
        "id": "cheque_1000",
        "name": "1000元支票",
        "description": "這是一張1000元支票，可以用來支付給其他用戶。",
        "worth": 0,
        "callback": make_cheque_use_callback("cheque_1000", 1000),
    },
]

items.extend(economy_items)
