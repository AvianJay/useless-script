import discord
from discord.ext import commands
from discord import app_commands
from globalenv import config, bot, start_bot, modules, get_server_config, set_server_config, add_app_command_error_handler
import asyncio
import logging
import traceback
from datetime import datetime, timezone, timedelta
import os
from pathlib import Path
from expiring_dict import ExpiringDict
import random

# Track pending log tasks for graceful shutdown
_pending_log_tasks: set = set()
_shutting_down = False
_webhook_bridge_installed = False
_ui_error_bridge_installed = False
_webhook_cache = {}
_pending_discord_batches = {}
_pending_discord_batch_task = None

DISCORD_LOG_BATCH_DELAY = 1.0
DISCORD_LOG_BATCH_SIZE = 10

def cleanup_old_logs(days=7):
    """清理超過指定天數的舊日誌檔案"""
    logs_dir = Path("logs")
    if not logs_dir.exists():
        logs_dir.mkdir(parents=True)
        return
    
    cutoff_date = datetime.now() - timedelta(days=days)
    deleted_count = 0
    
    for log_file in logs_dir.glob("bot-*.log"):
        try:
            # 獲取檔案的修改時間
            file_time = datetime.fromtimestamp(log_file.stat().st_mtime)
            if file_time < cutoff_date:
                log_file.unlink()
                deleted_count += 1
                print(f"[Logger] 已刪除舊日誌: {log_file.name}")
        except Exception as e:
            print(f"[Logger] 刪除日誌檔案時發生錯誤 {log_file.name}: {e}")
    
    if deleted_count > 0:
        print(f"[Logger] 共刪除 {deleted_count} 個舊日誌檔案")


def _unique_guild_ids(*guild_ids):
    unique_ids = []
    for guild_id in guild_ids:
        if not guild_id:
            continue
        if guild_id not in unique_ids:
            unique_ids.append(guild_id)
    return unique_ids


def _get_cached_sync_webhook(webhook_url: str | None):
    if not webhook_url:
        return None

    webhook = _webhook_cache.get(webhook_url)
    if webhook:
        return webhook

    try:
        webhook = discord.SyncWebhook.from_url(webhook_url)
    except Exception:
        return None

    _webhook_cache[webhook_url] = webhook
    return webhook


def _load_stored_sync_webhook(*storage_guild_ids, config_key: str = "log_webhook_url"):
    for guild_id in _unique_guild_ids(*storage_guild_ids):
        webhook_url = get_server_config(guild_id, config_key)
        webhook = _get_cached_sync_webhook(webhook_url)
        if webhook:
            return webhook, webhook_url
    return None, None


def _remember_webhook_url(webhook_url: str, *storage_guild_ids, config_key: str = "log_webhook_url"):
    for guild_id in _unique_guild_ids(*storage_guild_ids):
        set_server_config(guild_id, config_key, webhook_url)


def _forget_webhook_url(webhook_url: str | None, *storage_guild_ids, config_key: str = "log_webhook_url"):
    if webhook_url:
        _webhook_cache.pop(webhook_url, None)

    for guild_id in _unique_guild_ids(*storage_guild_ids):
        if get_server_config(guild_id, config_key) == webhook_url:
            set_server_config(guild_id, config_key, None)


def _build_log_embed(message: str, level: int, module_name: str, user: discord.User = None, guild: discord.Guild = None):
    color = 0x00ff00 if level == logging.INFO else 0xffff00 if level == logging.WARNING else 0xff0000 if level == logging.ERROR else 0x0000ff
    embed = discord.Embed(title=module_name, description=message, color=color)
    embed.timestamp = datetime.now(timezone.utc)
    if user:
        embed.add_field(name="使用者ID", value=user.id, inline=False)
        to_show_name = f"{user.display_name} ({user.name})" if user.display_name != user.name else user.name
        embed.set_author(name=to_show_name, icon_url=user.display_avatar.url if user.display_avatar else None)
    if guild:
        embed.add_field(name="伺服器ID", value=guild.id, inline=False)
        embed.set_footer(text=guild.name if guild.name else guild.id, icon_url=guild.icon.url if guild.icon else None)
    return embed


def _create_webhook_send_kwargs(*, embed=None, embeds=None):
    send_kwargs = {
        "username": bot.user.name if bot.user else "Logger",
    }
    if embed is not None:
        send_kwargs["embed"] = embed
    if embeds is not None:
        send_kwargs["embeds"] = embeds
    if bot.user:
        send_kwargs["avatar_url"] = bot.user.default_avatar.url
    return send_kwargs


async def _resolve_sync_webhook(channel_id: int | None, *storage_guild_ids, config_key: str = "log_webhook_url"):
    candidate_guild_ids = _unique_guild_ids(*storage_guild_ids)

    webhook, webhook_url = _load_stored_sync_webhook(*candidate_guild_ids, config_key=config_key)
    if webhook:
        return webhook, webhook_url, tuple(candidate_guild_ids)

    if not channel_id:
        return None, None, tuple(candidate_guild_ids)

    channel = bot.get_channel(channel_id)
    if not channel and not bot.is_ready():
        try:
            await asyncio.wait_for(bot.wait_until_ready(), timeout=15.0)
        except (Exception, asyncio.TimeoutError, asyncio.CancelledError):
            return None, None, tuple(candidate_guild_ids)
        channel = bot.get_channel(channel_id)

    candidate_guild_ids = _unique_guild_ids(
        *candidate_guild_ids,
        channel.guild.id if channel and getattr(channel, "guild", None) else None,
    )

    webhook, webhook_url = _load_stored_sync_webhook(*candidate_guild_ids, config_key=config_key)
    if webhook:
        return webhook, webhook_url, tuple(candidate_guild_ids)

    if not channel or not hasattr(channel, "create_webhook"):
        return None, None, tuple(candidate_guild_ids)

    try:
        create_kwargs = {"name": bot.user.name if bot.user else "Logger"}
        if bot.user:
            create_kwargs["avatar"] = await bot.user.default_avatar.read()
        webhook = await channel.create_webhook(**create_kwargs)
        webhook_url = webhook.url
        _remember_webhook_url(webhook_url, *candidate_guild_ids, config_key=config_key)
        return discord.SyncWebhook.from_url(webhook_url), webhook_url, tuple(candidate_guild_ids)
    except discord.HTTPException as e:
        if e.code != 30007:  # Maximum number of webhooks reached
            raise

    webhooks = await channel.webhooks()
    for existing_webhook in webhooks:
        existing_url = getattr(existing_webhook, "url", None)
        candidate = _get_cached_sync_webhook(existing_url)
        if candidate:
            _remember_webhook_url(existing_url, *candidate_guild_ids, config_key=config_key)
            return candidate, existing_url, tuple(candidate_guild_ids)

    if webhooks:
        existing_url = getattr(webhooks[0], "url", None)
        if existing_url:
            _remember_webhook_url(existing_url, *candidate_guild_ids, config_key=config_key)
            return discord.SyncWebhook.from_url(existing_url), existing_url, tuple(candidate_guild_ids)

    return None, None, tuple(candidate_guild_ids)


async def _send_log_embeds(channel_id: int | None, embeds: list[discord.Embed], *storage_guild_ids, config_key: str = "log_webhook_url"):
    if not channel_id:
        return False

    candidate_guild_ids = tuple(_unique_guild_ids(*storage_guild_ids))
    webhook, webhook_url = _load_stored_sync_webhook(*candidate_guild_ids, config_key=config_key)
    if not webhook:
        webhook, webhook_url, candidate_guild_ids = await _resolve_sync_webhook(channel_id, *candidate_guild_ids, config_key=config_key)
    if not webhook:
        return False

    for index in range(0, len(embeds), DISCORD_LOG_BATCH_SIZE):
        chunk = embeds[index:index + DISCORD_LOG_BATCH_SIZE]
        try:
            await asyncio.to_thread(webhook.send, **_create_webhook_send_kwargs(embeds=chunk))
        except (discord.NotFound, discord.Forbidden):
            _forget_webhook_url(webhook_url, *candidate_guild_ids, config_key=config_key)
            webhook, webhook_url, candidate_guild_ids = await _resolve_sync_webhook(channel_id, *candidate_guild_ids, config_key=config_key)
            if not webhook:
                return False
            await asyncio.to_thread(webhook.send, **_create_webhook_send_kwargs(embeds=chunk))
    return True


def _send_log_embed_now(channel_id: int | None, embed: discord.Embed, *storage_guild_ids, config_key: str = "log_webhook_url"):
    return _send_log_embeds_now(channel_id, [embed], *storage_guild_ids, config_key=config_key)


def _send_log_embeds_now(channel_id: int | None, embeds: list[discord.Embed], *storage_guild_ids, config_key: str = "log_webhook_url"):
    if not channel_id:
        return False

    webhook, _ = _load_stored_sync_webhook(*storage_guild_ids, config_key=config_key)
    if not webhook:
        return False

    for index in range(0, len(embeds), DISCORD_LOG_BATCH_SIZE):
        chunk = embeds[index:index + DISCORD_LOG_BATCH_SIZE]
        webhook.send(**_create_webhook_send_kwargs(embeds=chunk))
    return True


def _track_log_task(task: asyncio.Task):
    _pending_log_tasks.add(task)
    task.add_done_callback(_pending_log_tasks.discard)


async def _flush_discord_batches():
    global _pending_discord_batches
    if not _pending_discord_batches:
        return

    batches = _pending_discord_batches
    _pending_discord_batches = {}

    for (channel_id, storage_guild_ids, config_key), embeds in batches.items():
        try:
            await _send_log_embeds(channel_id, embeds, *storage_guild_ids, config_key=config_key)
        except Exception as e:
            print(f"[!] Error flushing Discord log batch: {e}")
            traceback.print_exc()


async def _discord_batch_worker():
    global _pending_discord_batch_task
    try:
        while _pending_discord_batches:
            if not _shutting_down:
                await asyncio.sleep(DISCORD_LOG_BATCH_DELAY)
            await _flush_discord_batches()
    finally:
        _pending_discord_batch_task = None
        if _pending_discord_batches and not _shutting_down:
            loop = asyncio.get_running_loop()
            _pending_discord_batch_task = loop.create_task(_discord_batch_worker())
            _track_log_task(_pending_discord_batch_task)


def _queue_discord_embed(channel_id: int | None, embed: discord.Embed, *storage_guild_ids, config_key: str = "log_webhook_url"):
    global _pending_discord_batch_task
    if not channel_id:
        return

    destination_key = (channel_id, tuple(_unique_guild_ids(*storage_guild_ids)), config_key)
    _pending_discord_batches.setdefault(destination_key, []).append(embed)

    if _pending_discord_batch_task and not _pending_discord_batch_task.done():
        return

    loop = asyncio.get_running_loop()
    _pending_discord_batch_task = loop.create_task(_discord_batch_worker())
    _track_log_task(_pending_discord_batch_task)


def _queue_startup_discord_embed(channel_id: int | None, embed: discord.Embed, *storage_guild_ids, config_key: str = "log_webhook_url"):
    if not channel_id:
        return

    destination_key = (channel_id, tuple(_unique_guild_ids(*storage_guild_ids)), config_key)
    batch = _pending_discord_batches.setdefault(destination_key, [])
    batch.append(embed)

    if len(batch) < DISCORD_LOG_BATCH_SIZE:
        return

    if _send_log_embeds_now(channel_id, batch, *storage_guild_ids, config_key=config_key):
        _pending_discord_batches.pop(destination_key, None)


def _unwrap_app_command_error(error: app_commands.AppCommandError):
    return getattr(error, "original", error)


def _format_exception_trace(error: BaseException):
    return ''.join(traceback.format_exception(type(error), error, error.__traceback__)).strip()


def _format_interaction_context(interaction: discord.Interaction):
    details = []

    interaction_type = getattr(getattr(interaction, "type", None), "name", None)
    if interaction_type:
        details.append(f"互動類型: {interaction_type}")

    command = getattr(interaction, "command", None)
    if command:
        if isinstance(command, app_commands.ContextMenu):
            details.append(f"指令: {command.qualified_name} ({command.type.name})")
        else:
            details.append(f"指令: /{command.qualified_name}")
    elif isinstance(getattr(interaction, "data", None), dict):
        interaction_name = interaction.data.get("name")
        if interaction_name:
            details.append(f"互動名稱: {interaction_name}")

    if interaction.channel:
        channel_name = getattr(interaction.channel, "name", type(interaction.channel).__name__)
        details.append(f"頻道: {channel_name} ({interaction.channel.id})")

    if hasattr(interaction, "namespace") and interaction.namespace:
        params = []
        for key, value in interaction.namespace.__dict__.items():
            if key.startswith('_'):
                continue
            params.append(f"{key}={value}")
        if params:
            details.append(f"參數: {', '.join(params)}")

    return "\n".join(details)


def _extract_user_and_guild(*context_objects):
    for obj in context_objects:
        if isinstance(obj, discord.Interaction):
            return obj.user, obj.guild
        if isinstance(obj, commands.Context):
            return obj.author, obj.guild
        if isinstance(obj, discord.Message):
            return obj.author, obj.guild
    return None, None


def _log_interaction_exception(title: str, interaction: discord.Interaction, error: BaseException, *, item=None):
    details = _format_interaction_context(interaction)
    if item is not None:
        item_name = getattr(item, "custom_id", None) or getattr(item, "label", None) or getattr(item, "placeholder", None)
        item_summary = item.__class__.__name__ if not item_name else f"{item.__class__.__name__} ({item_name})"
        details = f"{details}\n元件: {item_summary}" if details else f"元件: {item_summary}"

    trace_text = _format_exception_trace(error)
    message = (
        f"{title}\n"
        f"{details}\n"
        f"錯誤類型: {type(error).__name__}\n"
        f"錯誤訊息: {error}\n"
        f"堆疊追蹤:\n{trace_text}"
    ).strip()

    log(
        message,
        module_name="Logger",
        level=logging.ERROR,
        user=interaction.user,
        guild=interaction.guild,
    )

async def _log(*messages, level = logging.INFO, module_name: str = "General", user: discord.User = None, guild: discord.Guild = None, echo_console: bool = True):
    global _shutting_down
    
    # 確保 logs 資料夾存在
    logs_dir = Path("logs")
    if not logs_dir.exists():
        logs_dir.mkdir(parents=True)
    
    # 使用日期命名日誌檔案
    log_filename = f"logs/bot-{datetime.now().strftime('%Y-%m-%d')}.log"
    
    logger = logging.getLogger(module_name)
    if not logger.handlers:
        logger.setLevel(logging.DEBUG)
        handler = logging.FileHandler(log_filename, encoding='utf-8')
        handler.setLevel(logging.INFO)
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    logger.propagate = False

    message = ' '.join(str(m) for m in messages)
    if level == logging.INFO:
        logger.info(message)
    elif level == logging.WARNING:
        logger.warning(message)
    elif level == logging.ERROR:
        logger.error(message)
    elif level == logging.DEBUG:
        logger.debug(message)
    else:
        logger.log(level, message)

    # Also print to console
    if echo_console:
        print(f"[{module_name}] {message}", f"guild={guild.id}" if guild else "", f"user={user.id}" if user else "")
    
    # 如果正在關閉，不要嘗試發送到 Discord
    if _shutting_down or bot.is_closed():
        return

    log_channel_id = config("log_channel_id", None)
    if log_channel_id:
        try:
            embed = _build_log_embed(message, level, module_name, user=user, guild=guild)
            backend_guild_id = config("backend_guild_id", 0)
            _queue_discord_embed(log_channel_id, embed, backend_guild_id)

            if guild:
                guild_log_channel_id = get_server_config(guild.id, "log_channel_id")
                if guild_log_channel_id and guild_log_channel_id != log_channel_id:
                    _queue_discord_embed(guild_log_channel_id, embed.copy(), guild.id)
        except Exception as e:
            print(f"[!] Error sending log message to Discord channel: {e}")
            traceback.print_exc()

def log(*messages, level = logging.INFO, module_name: str = "General", user: discord.User = None, guild: discord.Guild = None):
    global _shutting_down
    if "logger" not in modules:
        return
    # 關閉時只同步輸出到 console，不建立新的 async task
    if _shutting_down:
        message = ' '.join(str(m) for m in messages)
        print(f"[{module_name}] {message}", f"guild={guild.id}" if guild else "", f"user={user.id}" if user else "")
        return
    try:
        loop = asyncio.get_running_loop()
        task = loop.create_task(_log(*messages, level=level, module_name=module_name, user=user, guild=guild))
        _track_log_task(task)
    except RuntimeError:
        # No running loop, just print to console
        message = ' '.join(str(m) for m in messages)
        print(f"[{module_name}] {message}", f"guild={guild.id}" if guild else "", f"user={user.id}" if user else "")
        if _shutting_down or bot.is_closed():
            return

        try:
            embed = _build_log_embed(message, level, module_name, user=user, guild=guild)
            backend_guild_id = config("backend_guild_id", 0)
            log_channel_id = config("log_channel_id", None)
            _queue_startup_discord_embed(log_channel_id, embed, backend_guild_id)

            if guild:
                guild_log_channel_id = get_server_config(guild.id, "log_channel_id")
                if guild_log_channel_id and guild_log_channel_id != log_channel_id:
                    _queue_startup_discord_embed(guild_log_channel_id, embed.copy(), guild.id)
        except Exception as e:
            print(f"[!] Error sending startup log message to Discord channel: {e}")
            traceback.print_exc()


def _enqueue_webhook_log_from_record(record: logging.LogRecord):
    if _shutting_down or "logger" not in modules:
        return

    if getattr(record, "_skip_webhook_bridge", False):
        return

    try:
        message = record.getMessage()
    except Exception:
        message = str(record.msg)

    if record.exc_info:
        exc_text = ''.join(traceback.format_exception(*record.exc_info)).strip()
        if exc_text:
            message = f"{message}\n{exc_text}" if message else exc_text
    elif record.stack_info:
        message = f"{message}\n{record.stack_info}" if message else record.stack_info

    module_name = record.name or "General"

    try:
        loop = asyncio.get_running_loop()
        task = loop.create_task(_log(message, level=record.levelno, module_name=module_name, echo_console=False))
        _track_log_task(task)
    except RuntimeError:
        print(f"[{module_name}] {message}")


class WebhookBridgeHandler(logging.Handler):
    def emit(self, record: logging.LogRecord):
        if record.name.startswith("General") and getattr(record, "_skip_webhook_bridge", False):
            return
        _enqueue_webhook_log_from_record(record)


def install_webhook_bridge():
    global _webhook_bridge_installed
    if _webhook_bridge_installed:
        return

    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        if isinstance(handler, WebhookBridgeHandler):
            _webhook_bridge_installed = True
            return

    bridge_handler = WebhookBridgeHandler(level=logging.INFO)
    root_logger.addHandler(bridge_handler)
    _webhook_bridge_installed = True


def install_ui_error_bridge():
    global _ui_error_bridge_installed
    if _ui_error_bridge_installed:
        return

    async def _view_on_error(self, interaction: discord.Interaction, error: Exception, item):
        _log_interaction_exception("互動元件發生錯誤", interaction, error, item=item)

    async def _modal_on_error(self, interaction: discord.Interaction, error: Exception):
        _log_interaction_exception("Modal 互動發生錯誤", interaction, error)

    discord.ui.View.on_error = _view_on_error
    discord.ui.Modal.on_error = _modal_on_error
    _ui_error_bridge_installed = True


async def flush_logs():
    """等待所有待處理的 log 任務完成"""
    global _shutting_down
    _shutting_down = True
    await _flush_discord_batches()
    if _pending_log_tasks:
        # 給每個任務最多 2 秒完成
        try:
            await asyncio.wait_for(
                asyncio.gather(*_pending_log_tasks, return_exceptions=True),
                timeout=2.0
            )
        except asyncio.TimeoutError:
            # 取消還沒完成的任務
            for task in _pending_log_tasks:
                task.cancel()
        _pending_log_tasks.clear()

@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
class LoggerCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # 設置應用程式指令錯誤處理
        add_app_command_error_handler(self.on_app_command_error)
        # 清理舊日誌檔案
        cleanup_old_logs(days=7)
        self.error_user_cache = ExpiringDict(ttl=60)  # 用於記錄已經提示過錯誤的用戶，避免重複提示

    @app_commands.command(name="set-log-channel", description="設置日誌頻道 (若不設置則不發送到頻道)")
    @app_commands.describe(channel="選擇日誌頻道")
    async def set_log_channel(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild.id
        set_server_config(guild_id, "log_channel_id", channel.id if channel else None)
        await interaction.followup.send(f"日誌頻道已設置為: {channel.mention}", ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author == self.bot.user:
            return
        # only log dm messages
        if isinstance(message.channel, discord.DMChannel):
            log(f"收到了私訊 {message.author}: {message.content}", module_name="Logger", level=logging.INFO, user=message.author)
        # else:
        #     await log(f"收到了訊息 {message.author}: {message.content}", module_name="Logger", level=logging.INFO, user=message.author, guild=message.guild)

    @commands.Cog.listener()
    async def on_command(self, ctx):
        log(f"指令被觸發: {ctx.command} 由 {ctx.author}", module_name="Logger", level=logging.INFO, user=ctx.author, guild=ctx.guild)

    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        # 處理未知指令
        if isinstance(error, commands.CommandNotFound):
            return  # 忽略未知指令，不回應
        
        # 處理權限不足
        if isinstance(error, commands.MissingPermissions):
            missing = ', '.join(error.missing_permissions)
            if ctx.author.id not in self.error_user_cache:
                await ctx.send(f"❌ 你沒有執行此指令的權限！缺少權限: {missing}" + ('\n-# 你傻逼吧你以為你是開發者你就可以濫權？' if ctx.author.id in config('owners') else ''), allowed_mentions=discord.AllowedMentions.none())
                self.error_user_cache[ctx.author.id] = True
            log(f"指令 {ctx.command} 由 {ctx.author} 觸發時權限不足: {missing}", module_name="Logger", level=logging.WARNING, user=ctx.author, guild=ctx.guild)
            return
        
        if isinstance(error, commands.BotMissingPermissions):
            missing = ', '.join(error.missing_permissions)
            if ctx.author.id not in self.error_user_cache:
                await ctx.send(f"❌ 我沒有足夠的權限執行此操作！缺少權限: {missing}", allowed_mentions=discord.AllowedMentions.none())
                self.error_user_cache[ctx.author.id] = True
            log(f"指令 {ctx.command} 機器人權限不足: {missing}", module_name="Logger", level=logging.WARNING, user=ctx.author, guild=ctx.guild)
            return
        
        # 處理 Check 失敗
        if isinstance(error, commands.CheckFailure):
            if ctx.author.id not in self.error_user_cache:
                messages = [
                    "❌ 你不符合執行此指令的條件！",
                    "❌ 請支付你的女裝照來解鎖使用權限。",
                    "❌ 請交出洋蔥的**新**女裝照來解鎖使用權限。",
                    "❌ 請交出**小金金的女裝照**來解鎖使用權限。",
                    "❌ 你在幹嘛？",
                ]
                await ctx.send(random.choice(messages), allowed_mentions=discord.AllowedMentions.none())
                self.error_user_cache[ctx.author.id] = True
            log(f"指令 {ctx.command} 由 {ctx.author} 觸發時 Check 失敗: {error}", module_name="Logger", level=logging.WARNING, user=ctx.author, guild=ctx.guild)
            return
        
        # 處理其他錯誤
        if ctx.author.id not in self.error_user_cache:
            await ctx.send(f"❌ 執行指令時發生錯誤！", allowed_mentions=discord.AllowedMentions.none())
            self.error_user_cache[ctx.author.id] = True
        log(f"指令 {ctx.command} 由 {ctx.author} 觸發時發生錯誤: {error}", module_name="Logger", level=logging.ERROR, user=ctx.author, guild=ctx.guild)
        # await ctx.send(f"糟糕！發生了一些錯誤: {error}")

    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        log(f"加入了新的伺服器: {guild.name} (ID: {guild.id})", module_name="Logger", level=logging.INFO)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild):
        log(f"離開了伺服器: {guild.name} (ID: {guild.id})", module_name="Logger", level=logging.INFO)

    @commands.Cog.listener()
    async def on_app_command_completion(self, interaction: discord.Interaction, application_command: discord.app_commands.Command):
        # 收集詳細資訊
        details = []
        
        # 基本指令資訊
        if isinstance(application_command, discord.app_commands.ContextMenu):
            details.append(f"類型: 右鍵選單 ({application_command.type.name})")
            details.append(f"名稱: {application_command.qualified_name}")
        else:
            details.append(f"類型: 斜線指令")
            details.append(f"名稱: /{application_command.qualified_name}")
        
        # 指令參數
        if hasattr(interaction, 'namespace') and interaction.namespace:
            params = []
            for key, value in interaction.namespace.__dict__.items():
                if not key.startswith('_'):
                    # 格式化參數值
                    if isinstance(value, (discord.User, discord.Member)):
                        params.append(f"{key}=@{value.name} ({value.id})")
                    elif isinstance(value, (discord.TextChannel, discord.VoiceChannel, discord.Thread)):
                        params.append(f"{key}=#{value.name} ({value.id})")
                    elif isinstance(value, discord.Role):
                        params.append(f"{key}=@{value.name} ({value.id})")
                    else:
                        params.append(f"{key}={value}")
            if params:
                details.append(f"參數: {', '.join(params)}")
        
        # 頻道資訊
        if interaction.channel:
            channel_type = type(interaction.channel).__name__
            if hasattr(interaction.channel, 'name'):
                details.append(f"頻道: #{interaction.channel.name} ({channel_type}, ID: {interaction.channel.id})")
            else:
                details.append(f"頻道: {channel_type} (ID: {interaction.channel.id})")
        
        # 組合所有詳細資訊
        log_message = "應用程式指令執行完成\n" + "\n".join(details)
        
        log(log_message, module_name="Logger", level=logging.INFO, user=interaction.user, guild=interaction.guild)

    async def on_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        """處理應用程式指令錯誤"""
        original_error = _unwrap_app_command_error(error)
        _log_interaction_exception("應用程式指令錯誤", interaction, original_error)
        
        # 向用戶顯示友善的錯誤訊息
        error_message = "❌ 執行指令時發生錯誤！"
        
        # 根據錯誤類型提供更具體的訊息
        if isinstance(error, app_commands.MissingPermissions):
            missing = ', '.join(error.missing_permissions)
            error_message = f"❌ 你沒有執行此指令的權限！缺少權限: {missing}"
        elif isinstance(error, app_commands.BotMissingPermissions):
            missing = ', '.join(error.missing_permissions)
            error_message = f"❌ 我沒有足夠的權限執行此操作！缺少權限: {missing}"
        elif isinstance(error, app_commands.CommandOnCooldown):
            error_message = f"❌ 此指令冷卻中，請在 {error.retry_after:.1f} 秒後再試。"
        elif isinstance(error, app_commands.CheckFailure):
            error_message = "❌ 你不符合執行此指令的條件。"
        else:
            # 對於其他錯誤，顯示錯誤訊息
            error_message = f"❌ 發生錯誤: {str(original_error)}"
        
        try:
            if interaction.response.is_done():
                await interaction.followup.send(error_message, ephemeral=True)
            else:
                await interaction.response.send_message(error_message, ephemeral=True)
        except Exception as e:
            # 如果無法發送錯誤訊息，至少記錄下來
            log(
                f"無法向用戶發送錯誤訊息: {e}\n原始錯誤: {original_error}",
                module_name="Logger",
                level=logging.ERROR,
                user=interaction.user,
                guild=interaction.guild,
            )

    # @commands.Cog.listener()
    # async def on_ready(self):
    #     log("機器人已準備就緒。", module_name="Logger", level=logging.INFO)
        
    @commands.Cog.listener()
    async def on_error(self, event_method, *args, **kwargs):
        user, guild = _extract_user_and_guild(*args, *kwargs.values())
        log(
            f"事件 {event_method} 發生錯誤。\n堆疊追蹤:\n{traceback.format_exc().strip()}",
            module_name="Logger",
            level=logging.ERROR,
            user=user,
            guild=guild,
        )

if "logger" in modules:
    install_webhook_bridge()
    install_ui_error_bridge()
    asyncio.run(bot.add_cog(LoggerCog(bot)))

if __name__ == "__main__":
    start_bot()
