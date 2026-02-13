import discord
from discord.ext import commands
from discord import app_commands
from globalenv import config, bot, start_bot, modules, get_server_config, set_server_config
import asyncio
import logging
import traceback
from datetime import datetime, timezone, timedelta
import os
from pathlib import Path

# Track pending log tasks for graceful shutdown
_pending_log_tasks: set = set()
_shutting_down = False

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

async def _log(*messages, level = logging.INFO, module_name: str = "General", user: discord.User = None, guild: discord.Guild = None):
    global _shutting_down
    
    # 確保 logs 資料夾存在
    logs_dir = Path("logs")
    if not logs_dir.exists():
        logs_dir.mkdir(parents=True)
    
    # 使用日期命名日誌檔案
    log_filename = f"logs/bot-{datetime.now().strftime('%Y-%m-%d')}.log"
    
    logger = logging.getLogger(module_name)
    if not logger.hasHandlers():
        logger.setLevel(logging.DEBUG)
        handler = logging.FileHandler(log_filename, encoding='utf-8')
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)

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
    print(f"[{module_name}] {message}", f"guild={guild.id}" if guild else "", f"user={user.id}" if user else "")
    
    # 如果正在關閉，不要嘗試發送到 Discord
    if _shutting_down or bot.is_closed():
        return
    
    # try to send to a specific discord channel if configured
    try:
        await asyncio.wait_for(bot.wait_until_ready(), timeout=5.0)
    except (Exception, asyncio.TimeoutError, asyncio.CancelledError):
        return
    log_channel_id = config("log_channel_id", None)
    if log_channel_id:
        channel = bot.get_channel(log_channel_id)
        if channel:
            try:
                # embed message
                color = 0x00ff00 if level == logging.INFO else 0xffff00 if level == logging.WARNING else 0xff0000 if level == logging.ERROR else 0x0000ff
                embed = discord.Embed(title=module_name, description=message, color=color)
                embed.timestamp = datetime.now(timezone.utc)
                if user:
                    embed.add_field(name="使用者ID", value=user.id, inline=False)  # easy to copy user id
                    to_show_name = f"{user.display_name} ({user.name})" if user.display_name != user.name else user.name
                    embed.set_author(name=to_show_name, icon_url=user.display_avatar.url if user.display_avatar else None)
                if guild:
                    embed.add_field(name="伺服器ID", value=guild.id, inline=False)  # easy to copy guild id
                    embed.set_footer(text=guild.name if guild.name else guild.id, icon_url=guild.icon.url if guild.icon else None)
                # get webhook url
                webhook_url = get_server_config(channel.guild.id, "log_webhook_url") if guild else None
                discord_webhook = None
                
                if webhook_url:
                    try:
                        discord_webhook = discord.SyncWebhook.from_url(webhook_url)
                        discord_webhook.fetch()  # test if webhook is valid
                    except Exception:
                        discord_webhook = None
                
                if not discord_webhook:
                    # 嘗試創建新 webhook，如果失敗則嘗試重用現有的
                    try:
                        webhook = await channel.create_webhook(name=bot.user.name, avatar=await bot.user.default_avatar.read())
                        webhook_url = webhook.url
                        if guild:
                            set_server_config(channel.guild.id, "log_webhook_url", webhook_url)
                        discord_webhook = discord.SyncWebhook.from_url(webhook_url)
                    except discord.HTTPException as e:
                        if e.code == 30007:  # Maximum number of webhooks reached
                            # 嘗試重用現有的 webhook
                            webhooks = await channel.webhooks()
                            for wh in webhooks:
                                try:
                                    discord_webhook = discord.SyncWebhook.from_url(wh.url)
                                    webhook_url = wh.url
                                    if guild:
                                        set_server_config(channel.guild.id, "log_webhook_url", webhook_url)
                                    break
                                except Exception:
                                    continue
                            if not discord_webhook and webhooks:
                                # 如果還是沒有可用的，使用第一個
                                discord_webhook = discord.SyncWebhook.from_url(webhooks[0].url)
                                webhook_url = webhooks[0].url
                                if guild:
                                    set_server_config(channel.guild.id, "log_webhook_url", webhook_url)
                        else:
                            raise
                
                if not discord_webhook:
                    return  # 無法獲取 webhook，放棄發送
                discord_webhook.send(embed=embed, username=bot.user.name, avatar_url=bot.user.default_avatar.url)
                if guild:
                    log_channel_id = get_server_config(guild.id, "log_channel_id")
                    if log_channel_id and log_channel_id != channel.id:
                        guild_channel = bot.get_channel(log_channel_id)
                        if guild_channel:
                            guild_webhook_url = get_server_config(guild.id, "log_webhook_url")
                            guild_discord_webhook = None
                            
                            if guild_webhook_url:
                                try:
                                    guild_discord_webhook = discord.SyncWebhook.from_url(guild_webhook_url)
                                    guild_discord_webhook.fetch()  # test if webhook is valid
                                except Exception:
                                    guild_discord_webhook = None
                            
                            if not guild_discord_webhook:
                                # 嘗試創建新 webhook，如果失敗則嘗試重用現有的
                                try:
                                    webhook = await guild_channel.create_webhook(name=bot.user.name, avatar=await bot.user.default_avatar.read())
                                    guild_webhook_url = webhook.url
                                    set_server_config(guild.id, "log_webhook_url", guild_webhook_url)
                                    guild_discord_webhook = discord.SyncWebhook.from_url(guild_webhook_url)
                                except discord.HTTPException as e:
                                    if e.code == 30007:  # Maximum number of webhooks reached
                                        # 嘗試重用現有的 webhook
                                        webhooks = await guild_channel.webhooks()
                                        for wh in webhooks:
                                            try:
                                                guild_discord_webhook = discord.SyncWebhook.from_url(wh.url)
                                                guild_webhook_url = wh.url
                                                set_server_config(guild.id, "log_webhook_url", guild_webhook_url)
                                                break
                                            except Exception:
                                                continue
                                        if not guild_discord_webhook and webhooks:
                                            # 如果還是沒有可用的，使用第一個
                                            guild_discord_webhook = discord.SyncWebhook.from_url(webhooks[0].url)
                                            guild_webhook_url = webhooks[0].url
                                            set_server_config(guild.id, "log_webhook_url", guild_webhook_url)
                                    else:
                                        raise
                            
                            if guild_discord_webhook:
                                guild_discord_webhook.send(embed=embed, username=bot.user.name, avatar_url=bot.user.default_avatar.url)
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
        _pending_log_tasks.add(task)
        task.add_done_callback(_pending_log_tasks.discard)
    except RuntimeError:
        # No running loop, just print to console
        message = ' '.join(str(m) for m in messages)
        print(f"[{module_name}] {message}", f"guild={guild.id}" if guild else "", f"user={user.id}" if user else "")


async def flush_logs():
    """等待所有待處理的 log 任務完成"""
    global _shutting_down
    _shutting_down = True
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
        self.bot.tree.error(self.on_app_command_error)
        # 清理舊日誌檔案
        cleanup_old_logs(days=7)

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
            await log(f"收到了私訊 {message.author}: {message.content}", module_name="Logger", level=logging.INFO, user=message.author)
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
            await ctx.send(f"❌ 你沒有執行此指令的權限！缺少權限: {missing}" + ('\n-# 你傻逼吧你以為你是開發者你就可以濫權？' if ctx.author.id in config('owners') else ''))
            log(f"指令 {ctx.command} 由 {ctx.author} 觸發時權限不足: {missing}", module_name="Logger", level=logging.WARNING, user=ctx.author, guild=ctx.guild)
            return
        
        if isinstance(error, commands.BotMissingPermissions):
            missing = ', '.join(error.missing_permissions)
            await ctx.send(f"❌ 我沒有足夠的權限執行此操作！缺少權限: {missing}")
            log(f"指令 {ctx.command} 機器人權限不足: {missing}", module_name="Logger", level=logging.WARNING, user=ctx.author, guild=ctx.guild)
            return
        
        # 處理 Check 失敗
        if isinstance(error, commands.CheckFailure):
            await ctx.send("❌ 你不符合執行此指令的條件。")
            log(f"指令 {ctx.command} 由 {ctx.author} 觸發時 Check 失敗: {error}", module_name="Logger", level=logging.WARNING, user=ctx.author, guild=ctx.guild)
            return
        
        # 處理其他錯誤
        log(f"指令 {ctx.command} 由 {ctx.author} 觸發時發生錯誤: {error}", module_name="Logger", level=logging.ERROR, user=ctx.author, guild=ctx.guild)
        await ctx.send(f"糟糕！發生了一些錯誤: {error}")

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
        # 取得完整的錯誤堆疊追蹤
        error_traceback = ''.join(traceback.format_exception(type(error), error, error.__traceback__))
        
        # 記錄詳細錯誤
        log(
            f"應用程式指令錯誤:\n指令: {interaction.command.name if interaction.command else '未知'}\n"
            f"錯誤類型: {type(error).__name__}\n"
            f"錯誤訊息: {str(error)}\n"
            f"堆疊追蹤:\n{error_traceback}",
            module_name="Logger",
            level=logging.ERROR,
            user=interaction.user,
            guild=interaction.guild
        )
        
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
            error_message = f"❌ 發生錯誤: {str(error)}"
        
        try:
            if interaction.response.is_done():
                await interaction.followup.send(error_message, ephemeral=True)
            else:
                await interaction.response.send_message(error_message, ephemeral=True)
        except Exception as e:
            # 如果無法發送錯誤訊息，至少記錄下來
            log(f"無法向用戶發送錯誤訊息: {e}", module_name="Logger", level=logging.ERROR)

    # @commands.Cog.listener()
    # async def on_ready(self):
    #     log("機器人已準備就緒。", module_name="Logger", level=logging.INFO)
        
    @commands.Cog.listener()
    async def on_error(self, event_method, *args, **kwargs):
        log(f"事件 {event_method} 發生錯誤。", module_name="Logger", level=logging.ERROR)

if "logger" in modules:
    asyncio.run(bot.add_cog(LoggerCog(bot)))

if __name__ == "__main__":
    start_bot()
