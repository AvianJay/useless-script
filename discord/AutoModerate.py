import discord
from discord.ext import commands
from discord import app_commands
from globalenv import bot, start_bot, get_user_data, set_user_data, get_all_user_data, get_server_config, set_server_config, modules
from datetime import datetime, timezone, timedelta
import asyncio
from typing import Optional
import re
import emoji

if "Moderate" in modules:
    import Moderate
else:
    Moderate = None


all_settings = [
    "escape_punish-punishment",
    "escape_punish-duration",
    "too_many_h1-max_length",
    "too_many_h1-action",
    "too_many_emojis-max_emojis",
    "too_many_emojis-action",
]

async def settings_autocomplete(interaction: discord.Interaction, current: str):
    return [
        app_commands.Choice(name=app_commands.locale_str(key), value=key)
        for key in all_settings if current.lower() in key.lower()
    ][:25]  # Discord 限制最多 25 個選項


async def do_action_str(action: str, guild: Optional[discord.Guild] = None, user: Optional[discord.Member] = None, message: Optional[discord.Message] = None):
    # if user is none just check if action is valid
    actions = action.split(",")
    actions = [a.strip() for a in actions]
    logs = []
    for a in actions:
        cmd = a.split(" ")
        if cmd[0] == "ban":
            # ban <duration> <reason> <delete_messages>
            if len(cmd) == 1:
                cmd.append("0s")
            if len(cmd) == 2:
                cmd.append("自動管理執行")
            if len(cmd) == 3:
                cmd.append("0s")

            if Moderate:
                duration_seconds = Moderate.timestr_to_seconds(cmd[1]) if cmd[1] != "0" else 0
                delete_messages = Moderate.timestr_to_seconds(cmd[3]) if cmd[3] != "0" else 0
                logs.append(f"封禁用戶，原因: {cmd[2]}，持續秒數: {duration_seconds}秒，刪除訊息時間: {delete_messages}秒")
                if user:
                    await Moderate.ban_user(guild, user, reason=cmd[2], duration=duration_seconds, delete_message_seconds=delete_messages)
            else:
                print("[!] Moderate module not loaded, cannot ban user.")
                raise Exception("Moderate module not loaded")
        elif cmd[0] == "kick":
            # kick <reason>
            if len(cmd) == 1:
                cmd.append("自動管理執行")
            logs.append(f"踢出用戶，原因: {cmd[1]}")
            if user:
                await user.kick(reason=cmd[1])
        elif cmd[0] == "mute" or cmd[0] == "timeout":
            # mute <duration> <reason>
            if len(cmd) == 1:
                cmd.append("10m")
            if len(cmd) == 2:
                cmd.append("自動管理執行")
            if Moderate:
                duration_seconds = Moderate.timestr_to_seconds(cmd[1]) if cmd[1] != "0" else 0
                logs.append(f"禁言用戶，原因: {cmd[2]}，持續秒數: {duration_seconds}秒")
                if user:
                    await user.timeout(datetime.now(timezone.utc) + timedelta(seconds=duration_seconds), reason=cmd[2])
            else:
                print("[!] Moderate module not loaded, cannot mute user.")
                raise Exception("Moderate module not loaded")
        elif cmd[0] == "delete" or cmd[0] == "delete_dm":
            # delete <warn_message>
            logs.append("刪除訊息")
            if message:
                await message.delete()
            if len(cmd) > 1:
                msg = cmd.copy()
                msg.pop(0)
                warn_message = " ".join(msg)
                warn_message = warn_message.replace("{user}", user.mention if user else "用戶")
                logs.append(f"並警告: {warn_message}")
                if cmd[0] == "delete_dm" and user:
                    await user.send(warn_message)
                elif message:
                    await message.channel.send(warn_message)
        elif cmd[0] == "warn" or cmd[0] == "warn_dm":
            # warn <warn_message>
            if len(cmd) == 1:
                cmd.append(f"{user.mention if user else '用戶'}，請注意你的行為。")
            msg = cmd.copy()
            msg.pop(0)
            warn_message = " ".join(msg)
            warn_message = warn_message.replace("{user}", user.mention if user else "用戶")
            logs.append(f"傳送警告訊息: {warn_message}")
            if cmd[0] == "warn_dm" and user:
                await user.send(warn_message)
            elif message:
                await message.reply(warn_message)
    return logs


@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
class AutoModerate(commands.GroupCog, name=app_commands.locale_str("automod")):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        super().__init__()
        
    @app_commands.command(name=app_commands.locale_str("view"), description="查看自動管理設定")
    async def view_automod_settings(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id if interaction.guild else None
        automod_settings = get_server_config(guild_id, "automod", {})
        if not automod_settings:
            await interaction.response.send_message("自動管理尚未啟用。", ephemeral=True)
            return

        embed = discord.Embed(title="自動管理設定", color=0x00ff00)
        desc = ""
        for key, value in automod_settings.items():
            desc += f"**{key}**:"
            for subkey, subvalue in value.items():
                desc += f"\n - {subkey}: {subvalue}"
            desc += "\n"
        embed.description = desc
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name=app_commands.locale_str("toggle"), description="啟用或停用自動管理設定")
    @app_commands.describe(setting="要啟用或停用的自動管理設定名稱", enable="是否啟用該設定")
    @app_commands.choices(
        setting=[
            app_commands.Choice(name="逃避責任懲處", value="escape_punish"),
            app_commands.Choice(name="標題過多", value="too_many_h1"),
            app_commands.Choice(name="表情符號過多", value="too_many_emojis"),
        ],
        enable=[
            app_commands.Choice(name="啟用", value="True"),
            app_commands.Choice(name="停用", value="False"),
        ]
    )
    async def toggle_automod_setting(self, interaction: discord.Interaction, setting: str, enable: str):
        guild_id = interaction.guild.id if interaction.guild else None
        automod_settings = get_server_config(guild_id, "automod", {})
        automod_settings.setdefault(setting, {})["enabled"] = (enable == "True")
        set_server_config(guild_id, "automod", automod_settings)
        await interaction.response.send_message(f"已將自動管理設定 '{setting}' 設為 {'啟用' if enable == 'True' else '停用'}。")
    
    @app_commands.command(name=app_commands.locale_str("settings"), description="設定自動管理選項")
    @app_commands.describe(
        setting="要設定的自動管理選項",
        value="選項的值"
    )
    @app_commands.autocomplete(setting=settings_autocomplete)
    async def set_automod_setting(self, interaction: discord.Interaction, setting: str, value: str):
        guild_id = interaction.guild.id if interaction.guild else None
        automod_settings = get_server_config(guild_id, "automod", {})
        setting_base = setting.split("-")[0]
        setting_key = setting.split("-")[1] if len(setting.split("-")) > 1 else None
        if setting_base not in automod_settings:
            automod_settings[setting_base] = {}
        automod_settings[setting_base][setting_key] = value
        set_server_config(guild_id, "automod", automod_settings)
        await interaction.response.send_message(f"已將自動管理設定 '{setting}' 設為 {value}。")
    
    @app_commands.command(name=app_commands.locale_str("check-action"), description="檢查自動管理動作指令是否有效")
    @app_commands.describe(action="要檢查的動作指令")
    async def check_automod_action(self, interaction: discord.Interaction, action: str):
        try:
            actions = await do_action_str(action)
        except Exception as e:
            await interaction.response.send_message(f"無法解析動作指令: {e}", ephemeral=True)
            return
        actions = [f"- {a}" for a in actions]
        actions_str = "\n".join(actions) if actions else "無動作"
        msg = f"指令有效，解析出的動作:\n{actions_str}"
        await interaction.response.send_message(content=msg)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        if not after.guild:
            return
        set_user_data(guild_id=after.guild.id, user_id=after.id, key="communication_disabled_until", value=after.timed_out_until.isoformat() if after.timed_out_until else None)
        
    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if not member.guild:
            return
        server_config = get_server_config(member.guild.id, "automod", {})
        if not server_config.get("escape_punish", {}).get("enabled", False):
            return
        communication_disabled_until = get_user_data(guild_id=member.guild.id, user_id=member.id, key="communication_disabled_until")
        if communication_disabled_until:
            communication_disabled_until = datetime.fromisoformat(communication_disabled_until)
            if communication_disabled_until > datetime.now(timezone.utc):
                # 用戶在禁言期間離開，進行懲處
                punishment = server_config["escape_punish"].get("punishment", "ban")
                duration = server_config["escape_punish"].get("duration", "0")
                duration_seconds = Moderate.timestr_to_seconds(duration) if Moderate else 0
                try:
                    if punishment == "ban":
                        if Moderate:
                            await Moderate.ban_user(member.guild, member, reason="逃避禁言", duration=duration_seconds if duration_seconds > 0 else 0)
                        else:
                            print("[!] Moderate module not loaded, cannot ban user.")
                            raise Exception("Moderate module not loaded")
                    # 好像也就只有 ban 可以用了，我在做什麼呀
                    print(f"[+] 用戶 {member} 因逃避禁言被 {punishment}")
                except Exception as e:
                    print(f"[!] 無法對用戶 {member} 執行懲處: {e}")
        
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return
        guild_id = message.guild.id
        automod_settings = get_server_config(guild_id, "automod", {})
        
        # 標題過多檢查
        if automod_settings.get("too_many_h1", {}).get("enabled", False):
            max_length = int(automod_settings["too_many_h1"].get("max_length", 20))
            action = automod_settings["too_many_h1"].get("action", "warn")
            h1_count = 0
            split_lines = message.content.split("\n")
            for line in split_lines:
                line = line.lstrip()
                if line.startswith("# "):
                    # find custom emoji and replace with single character
                    while re.search(r'<a?:\w+:\d+>', line):
                        line = re.sub(r'<a?:\w+:\d+>', 'E', line, count=1)
                    line = line[2:]
                    h1_count += len(line)
            if h1_count > max_length:
                try:
                    await do_action_str(action, guild=message.guild, user=message.author, message=message)
                    print(f"[+] 用戶 {message.author} 因標題長度過長被處理: {action}")
                except Exception as e:
                    print(f"[!] 無法對用戶 {message.author} 執行標題過多的處理: {e}")
        
        # 表情符號過多檢查
        if automod_settings.get("too_many_emojis", {}).get("enabled", False):
            max_emojis = int(automod_settings["too_many_emojis"].get("max_emojis", 10))
            action = automod_settings["too_many_emojis"].get("action", "warn")
            emoji_count = len(re.findall(r'<a?:\w+:\d+>', message.content))
            emoji_count += len([c for c in message.content if emoji.is_emoji(c)])
            if emoji_count > max_emojis:
                try:
                    await do_action_str(action, guild=message.guild, user=message.author, message=message)
                    print(f"[+] 用戶 {message.author} 因表情符號過多被處理: {action}")
                except Exception as e:
                    print(f"[!] 無法對用戶 {message.author} 執行表情符號過多的處理: {e}")

asyncio.run(bot.add_cog(AutoModerate(bot)))

if __name__ == "__main__":
    start_bot()
