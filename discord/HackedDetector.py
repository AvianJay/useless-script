from globalenv import bot, set_user_data, get_user_data, start_bot, get_command_mention
import discord
from discord.ext import commands
from discord import app_commands
from logger import log
import logging
from Moderate import check_member_hierarchy
from ModerationNotify import ignore_user
from datetime import datetime, timedelta, timezone
from pyfiglet import Figlet
import re
import random
import asyncio

fonts = [
    "6x9",
    "helvb",
    "5x8",
    "pagga",
    "xhelv",
    "keyboard",
    "future_5",
    "mono9",
    "ntgreek",
    "slant",
    "yie_ar_k",
    "chunky",
    "fuzzy",
    "xcouri",
    "script",
    "beer_pub",
    "tec_7000",
    "new_asci",
    "clr5x6",
    "utopiab",
    "xtty",
    "4max",
    "xsbooki",
    "tavl____",
    "calghpy2",
    "modern__"
]

class HackedDetector(commands.Cog):
    HACKED_DATA_GUILD_ID = 0
    DETECTION_WINDOW_SECONDS = 10
    DETECTION_MIN_CHANNELS = 3
    DEFAULT_UNLOCK_FONT = "slant"

    def __init__(self):
        super().__init__()
        # {user_id: [{"time": float, "channel_id": int, "message": discord.Message, "message_id": int}]}
        self.usercache = {}
        log(
            f"HackedDetector initialized with detection_window={self.DETECTION_WINDOW_SECONDS}s min_channels={self.DETECTION_MIN_CHANNELS}.",
            level=logging.DEBUG,
            module_name="HackedDetector",
        )

    def _get_hacked_user_data(self, user_id: int, key: str, default=None):
        return get_user_data(self.HACKED_DATA_GUILD_ID, user_id, key, default)

    def _set_hacked_user_data(self, user_id: int, key: str, value):
        return set_user_data(self.HACKED_DATA_GUILD_ID, user_id, key, value)

    def _render_unlock_code_art(self, code: str):
        font_name = random.choice(fonts or [self.DEFAULT_UNLOCK_FONT])
        try:
            return Figlet(font=font_name).renderText(code), font_name
        except Exception as e:
            log(
                f"Failed to render unlock code with font {font_name}: {e}",
                level=logging.WARNING,
                module_name="HackedDetector",
            )
            fallback_font = self.DEFAULT_UNLOCK_FONT
            return Figlet(font=fallback_font).renderText(code), fallback_font

    def _prune_suspicious_events(self, user_id: int, now: float):
        window_start = now - self.DETECTION_WINDOW_SECONDS
        events = [
            event for event in self.usercache.get(user_id, [])
            if event.get("time", 0) >= window_start
        ]
        if events:
            self.usercache[user_id] = events
        else:
            self.usercache.pop(user_id, None)
        return events

    def _record_suspicious_event(self, message: discord.Message):
        now = asyncio.get_running_loop().time()
        events = self._prune_suspicious_events(message.author.id, now)
        events.append({
            "time": now,
            "channel_id": message.channel.id,
            "message": message,
            "message_id": message.id,
        })
        self.usercache[message.author.id] = events
        return events

    async def _delete_detected_messages(self, events: list[dict]):
        deleted = 0
        failed = 0
        seen_message_ids = set()

        for event in events:
            message = event.get("message")
            message_id = event.get("message_id")
            if message is None or message_id in seen_message_ids:
                continue
            seen_message_ids.add(message_id)
            try:
                await message.delete()
                deleted += 1
            except discord.NotFound:
                continue
            except Exception as e:
                failed += 1
                log(
                    f"Failed to delete suspicious message {message_id} from user {message.author.id} in channel {message.channel.id}: {e}",
                    level=logging.ERROR,
                    module_name="HackedDetector",
                    user=message.author,
                    guild=message.guild,
                )

        return deleted, failed

    async def _notify_unlock_in_channel(self, user: discord.User, channel):
        if channel is None or not hasattr(channel, "send"):
            return False

        try:
            command_mention = await get_command_mention("imhacked") or "/imhacked"
        except Exception as e:
            command_mention = "/imhacked"
            log(
                f"Failed to resolve /imhacked mention for user {user.id}: {e}",
                level=logging.DEBUG,
                module_name="HackedDetector",
                user=user,
                guild=getattr(channel, "guild", None),
            )

        try:
            await channel.send(
                f"{user.mention} 我無法私訊你。請先私訊我，然後使用 {command_mention} 開始解除禁言流程。",
                allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
            )
            log(
                f"Fallback unlock instructions sent in channel {channel.id} for user {user.id}.",
                level=logging.DEBUG,
                module_name="HackedDetector",
                user=user,
                guild=getattr(channel, "guild", None),
            )
            return True
        except Exception as e:
            log(
                f"Failed to send fallback unlock instructions in channel {getattr(channel, 'id', 'unknown')} for user {user.id}: {e}",
                level=logging.ERROR,
                module_name="HackedDetector",
                user=user,
                guild=getattr(channel, "guild", None),
            )
            return False

    async def unlock_user(self, user: discord.User):
        # untimeout the user in all mutual guilds
        guilds = self._get_hacked_user_data(user.id, "hacked_timed_out_channel", [])
        admin_removed = self._get_hacked_user_data(user.id, "hacked_admin_removed", {}) or {}
        log(f"Unlock flow started for user {user.id}. timed_out_guilds={len(guilds)}, admin_roles={len(admin_removed)}", level=logging.DEBUG, module_name="HackedDetector", user=user)
        if not guilds:
            log(f"Unlock flow aborted for user {user.id}: no timed out guild records.", level=logging.DEBUG, module_name="HackedDetector", user=user)
            return False
        for guild_id in guilds:
            guild = bot.get_guild(int(guild_id)) if guild_id is not None else None
            if not guild:
                log(f"Unlock skipped guild_id={guild_id}: guild not found in cache.", level=logging.DEBUG, module_name="HackedDetector", user=user)
                continue
            member = guild.get_member(user.id)
            if not member:
                log(f"Unlock skipped guild={guild.id}: member {user.id} not found.", level=logging.DEBUG, module_name="HackedDetector", user=user)
                continue
            try:
                # discord.Member.timeout is a coroutine in discord.py v2 → await it
                await member.timeout(None, reason="解除預防性禁言。")
                # log(f"User {user} has been unmuted in guild {guild.name} ({guild.id}).", level=logging.INFO, module_name="HackedDetector", user=user) ##anti 429##
            except Exception as e:
                log(f"Failed to untimeout user {user} in guild {guild.name} ({guild.id}): {e}", level=logging.ERROR, module_name="HackedDetector", user=user)
            # admin_removed may have string keys if persisted via JSON, be tolerant
            admin_role_id = admin_removed.get(guild_id) or admin_removed.get(str(guild_id))
            if admin_role_id:
                admin_role = guild.get_role(int(admin_role_id)) if admin_role_id is not None else None
                if admin_role:
                    try:
                        await member.add_roles(admin_role, reason="恢復管理員角色。")
                        log(f"User {user} has been restored admin role in guild {guild.name} ({guild.id}).", level=logging.INFO, module_name="HackedDetector", user=user)
                    except Exception as e:
                        log(f"Failed to restore admin role to user {user} in guild {guild.name} ({guild.id}): {e}", level=logging.ERROR, module_name="HackedDetector", user=user)
                else:
                    log(f"Restore admin skipped for user {user.id} in guild={guild.id}: role_id={admin_role_id} not found.", level=logging.DEBUG, module_name="HackedDetector", user=user)
        # 清理資料
        self._set_hacked_user_data(user.id, "hacked_timed_out_channel", [])
        self._set_hacked_user_data(user.id, "hacked_admin_removed", {})
        log(f"Unlock flow finished for user {user.id}. Records cleared.", level=logging.DEBUG, module_name="HackedDetector", user=user)
        return True

    async def handle_hacked_user(self, user: discord.User, channel: discord.TextChannel = None):
        # Handle the hacked user (e.g., send a warning, log the incident, etc.)
        log(f"User {user} is suspected to be hacked. Sent messages in multiple channels within a short time frame.", level=logging.WARNING, module_name="HackedDetector", user=user)
        # get mutual guilds
        guilds = user.mutual_guilds
        until = datetime.now(timezone.utc) + timedelta(days=1)
        log(f"Start handling suspected hacked user {user.id}: mutual_guilds={len(guilds)} timeout_until={until.isoformat()}", level=logging.DEBUG, module_name="HackedDetector", user=user)
        try:
            ignore_user(user.id)
        except Exception:
            # ignore_user 失敗不應阻止後續流程
            log(f"ignore_user failed for {user.id}", level=logging.DEBUG, module_name="HackedDetector", user=user)
        muted = []
        admin_ids = {}
        failed = 0
        for guild in guilds:
            member = guild.get_member(user.id)
            if not member:
                log(f"Skip guild {guild.id} for user {user.id}: member not found.", level=logging.DEBUG, module_name="HackedDetector", user=user)
                continue
            ok, msg = check_member_hierarchy(guild.me, member, guild.me)
            already_muted = member.is_timed_out()
            is_admin = member.guild_permissions.administrator
            log(
                f"Guild {guild.id} check for user {user.id}: hierarchy_ok={ok}, already_muted={already_muted}, is_admin={is_admin}",
                level=logging.DEBUG,
                module_name="HackedDetector",
                user=user,
                guild=guild,
            )
            if ok and not already_muted and not is_admin:
                try:
                    await member.timeout(until, reason="檢測到被盜帳戶，預防性禁言。")
                    muted.append(guild.id)
                except Exception as e:
                    log(f"Failed to timeout user {user} in guild {guild.name} ({guild.id}): {e}", level=logging.ERROR, module_name="HackedDetector", user=user)
                    failed += 1
            elif ok and is_admin:
                # try to remove admin role and timeout again
                try:
                    admin_role = discord.utils.find(lambda r: r.permissions.administrator, guild.roles)
                    if admin_role:
                        try:
                            await member.remove_roles(admin_role, reason="檢測到被盜帳戶，移除管理員角色以進行預防性禁言。")
                        except Exception as e:
                            log(f"Failed to remove admin role from user {user} in guild {guild.name} ({guild.id}): {e}", level=logging.ERROR, module_name="HackedDetector", user=user)
                        await member.timeout(until, reason="檢測到被盜帳戶，預防性禁言。")
                        muted.append(guild.id)
                        admin_ids[guild.id] = admin_role.id
                    else:
                        log(f"Failed to find admin role in guild {guild.name} ({guild.id}) to remove from user {user}.", level=logging.ERROR, module_name="HackedDetector", user=user)
                        failed += 1
                except Exception as e:
                    log(f"Failed to remove admin role and timeout user {user} in guild {guild.name} ({guild.id}): {e}", level=logging.ERROR, module_name="HackedDetector", user=user)
            else:
                failed += 1
                if not ok:
                    log(f"Skip timeout in guild {guild.id} for user {user.id}: hierarchy check failed ({msg}).", level=logging.DEBUG, module_name="HackedDetector", user=user, guild=guild)
        if not muted:
            log(f"Failed to timeout user {user} in any mutual guilds. No guilds were muted.", level=logging.WARNING, module_name="HackedDetector", user=user)
            return
        # 儲存被禁言的伺服器 id 及移除過的 admin role id
        self._set_hacked_user_data(user.id, "hacked_timed_out_channel", muted)
        self._set_hacked_user_data(user.id, "hacked_admin_removed", admin_ids)
        log(f"Timed out user in {muted} guild(s), removed {len(admin_ids)} admins, {failed} failed.", level=logging.INFO, module_name="HackedDetector", user=user)
        embed = discord.Embed(
            title="系統警告",
            description="我們檢測到您的帳戶可能被盜用，已對您進行預防性禁言，請盡快檢查您的帳戶安全。",
            color=discord.Color.red()
        )
        embed.add_field(name="被禁言的伺服器數量", value=str(len(muted)), inline=False)
        embed.add_field(name="未能禁言的伺服器數量", value=str(failed), inline=False)
        embed.timestamp = datetime.now()
        try:
            # 傳送帶有按鈕的私訊，StartUnlockView 需要 parent
            await user.send(embed=embed, view=self.StartUnlockView(self))
            log(f"Warning DM sent to suspected hacked user {user.id}.", level=logging.DEBUG, module_name="HackedDetector", user=user)
        except Exception as e:
            log(f"Failed to send DM to user {user}: {e}", level=logging.ERROR, module_name="HackedDetector", user=user)
            await self._notify_unlock_in_channel(user, channel)

    class UnlockModal(discord.ui.Modal, title="解除禁言"):
        # enter code to unlock account
        code = discord.ui.TextInput(label="請輸入解除禁言的驗證碼", placeholder="驗證碼", required=True)

        def __init__(self, user: discord.User, parent, code: str):
            super().__init__()
            self.user = user
            self.parent = parent
            self.original_code = code
    
        async def on_submit(self, interaction: discord.Interaction):
            log(f"Unlock modal submitted by user {interaction.user.id} for target {self.user.id}.", level=logging.DEBUG, module_name="HackedDetector", user=interaction.user, guild=interaction.guild)
            if interaction.user.id != self.user.id:
                await interaction.response.send_message("這個按鈕不是為你設置的。", ephemeral=True)
                return
            if self.original_code != self.code.value:
                log(f"Unlock code mismatch for user {interaction.user.id}.", level=logging.DEBUG, module_name="HackedDetector", user=interaction.user, guild=interaction.guild)
                await interaction.response.send_message("驗證碼錯誤，請重新嘗試。", ephemeral=True)
                return
            success = await self.parent.unlock_user(self.user)
            if success:
                await interaction.response.send_message("你的帳戶已經解除禁言。", ephemeral=True)
                log(f"Unlock success for user {interaction.user.id}.", level=logging.DEBUG, module_name="HackedDetector", user=interaction.user, guild=interaction.guild)
            else:
                await interaction.response.send_message("解除禁言失敗，你可能已經解除解除禁言了。", ephemeral=True)
                log(f"Unlock failed for user {interaction.user.id} (no records or no guild actions).", level=logging.DEBUG, module_name="HackedDetector", user=interaction.user, guild=interaction.guild)

    class UnlockView(discord.ui.View):
        def __init__(self, user: discord.User, parent, code: str):
            super().__init__()
            self.user = user
            self.parent = parent
            self.code = code

        @discord.ui.button(label="輸入驗證碼", style=discord.ButtonStyle.green, custom_id="hacked:unlock_code_input")
        async def unlock_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user.id != self.user.id:
                await interaction.response.send_message("這個按鈕不是為你設置的。", ephemeral=True)
                return
            # parent 為 cog instance
            modal = self.parent.UnlockModal(self.user, self.parent, self.code)
            await interaction.response.send_modal(modal)
            log(f"Unlock modal opened for user {interaction.user.id}.", level=logging.DEBUG, module_name="HackedDetector", user=interaction.user, guild=interaction.guild)

    class StartUnlockView(discord.ui.View):
        def __init__(self, parent):
            super().__init__(timeout=None)
            # parent 必須是 cog instance
            self.parent = parent

        @discord.ui.button(label="開始解除禁言流程", style=discord.ButtonStyle.green, custom_id="hacked:start_unlock")
        async def start_unlock_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            user = interaction.user
            code = str(random.randint(1, 9999)).zfill(4)
            rendered_code, font_name = self.parent._render_unlock_code_art(code)
            embed = discord.Embed(
                title="請輸入驗證碼",
                description=f"```\n{rendered_code}\n```\n請在下面的按鈕中輸入上方的驗證碼以解除禁言。",
                color=discord.Color.blue()
            )
            embed.timestamp = datetime.now()
            # 傳入正確的 parent (cog instance)
            view = self.parent.UnlockView(user, self.parent, code)
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            log(f"Sent unlock challenge to user {user.id} with font={font_name}.", level=logging.DEBUG, module_name="HackedDetector", user=user)


    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or message.guild is None:
            return

        # check message is matched some pattern that indicates the user might be hacked
        # discord invite links or 4 attachment images in a message
        has_invite = re.search(r"(https?://)?(www\.)?(discord\.gg|discordapp\.com/invite)/[a-zA-Z0-9]+", message.content)
        # 4 attachment images in a message
        if not (has_invite or len(message.attachments) == 4):
            return
        log(
            f"Suspicious pattern matched from user {message.author.id} in channel {message.channel.id}: has_invite={bool(has_invite)} attachments={len(message.attachments)}",
            level=logging.DEBUG,
            module_name="HackedDetector",
            user=message.author,
            guild=message.guild,
        )

        events = self._record_suspicious_event(message)
        channel_ids = sorted({event["channel_id"] for event in events})
        log(
            f"User {message.author.id} suspicious window events={len(events)} unique_channels={channel_ids}",
            level=logging.DEBUG,
            module_name="HackedDetector",
            user=message.author,
            guild=message.guild,
        )

        if len(channel_ids) < self.DETECTION_MIN_CHANNELS:
            return

        detected_events = list(events)
        self.usercache.pop(message.author.id, None)

        deleted, delete_failed = await self._delete_detected_messages(detected_events)
        log(
            f"Suspicious message cleanup for user {message.author.id}: deleted={deleted}, failed={delete_failed}",
            level=logging.INFO,
            module_name="HackedDetector",
            user=message.author,
            guild=message.guild,
        )

        # check if user is already timed out
        timed_out = self._get_hacked_user_data(message.author.id, "hacked_timed_out_channel", [])
        if timed_out:
            log(f"Skip handling user {message.author.id}: already has timeout records {timed_out}.", level=logging.DEBUG, module_name="HackedDetector", user=message.author, guild=message.guild)
            return

        log(
            f"Trigger hacked handling for user {message.author.id} with channels={channel_ids} in {self.DETECTION_WINDOW_SECONDS}s window.",
            level=logging.DEBUG,
            module_name="HackedDetector",
            user=message.author,
            guild=message.guild,
        )
        await self.handle_hacked_user(message.author, channel=message.channel)

    async def cog_load(self):
        # 註冊 persistent view：timeout=None + 穩定 custom_id
        bot.add_view(self.StartUnlockView(self))
        log("HackedDetector cog loaded and persistent view registered.", level=logging.DEBUG, module_name="HackedDetector")

    @app_commands.command(name="imhacked", description="開始解除被盜帳戶的流程")
    async def imhacked(self, interaction: discord.Interaction):
        user = interaction.user
        timed_out = self._get_hacked_user_data(user.id, "hacked_timed_out_channel", [])
        log(f"/imhacked invoked by user {user.id}, timeout_records={len(timed_out)}", level=logging.DEBUG, module_name="HackedDetector", user=user, guild=interaction.guild)
        if not timed_out:
            log(f"User {user} used /imhacked but has no active timeouts.", level=logging.DEBUG, module_name="HackedDetector", user=user)
            await interaction.response.send_message("我們沒有檢測到你的帳戶有被盜用的跡象，無需解除禁言。", ephemeral=True)
            return
        await interaction.response.send_message("按下下面的按鈕開始解除禁言流程。", view=self.StartUnlockView(self), ephemeral=True)


asyncio.run(bot.add_cog(HackedDetector()))

if __name__ == "__main__":
    start_bot()
