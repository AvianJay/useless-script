import discord
from discord.ext import commands
from discord import app_commands
from globalenv import bot, start_bot, get_user_data, set_user_data, get_all_user_data, get_server_config, set_server_config, modules
from datetime import datetime, timezone
import asyncio

if "Moderate" in modules:
    import Moderate
else:
    Moderate = None


all_settings = [
    "escape_punish-punishment",
    "escape_punish-duration",
]

async def settings_autocomplete(interaction: discord.Interaction, current: str):
    return [
        app_commands.Choice(name=app_commands.locale_str(key), value=key)
        for key in all_settings if current.lower() in key.lower()
    ][:25]  # Discord 限制最多 25 個選項


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

asyncio.run(bot.add_cog(AutoModerate(bot)))

if __name__ == "__main__":
    start_bot()
