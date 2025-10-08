import asyncio
import random
from datetime import datetime, timedelta
import discord
from discord import app_commands
from discord.ext import commands
from globalenv import bot, start_bot, get_user_data, set_user_data, get_all_user_data, get_server_config, set_server_config


def percent_random(percent: int) -> bool:
    try:
        percent = int(percent)
        if percent <= 0:
            return False
        return random.randint(1, max(1, 100 // percent)) == 1
    except Exception:
        return False


class DSize(commands.GroupCog, group_name="dsize"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="量", description="量屌長")
    @app_commands.describe(global_dsize="是否使用全域紀錄 (預設否)")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    async def dsize(self, interaction: discord.Interaction, global_dsize: bool = False):
        user_id = interaction.user.id
        now = (datetime.utcnow() + timedelta(hours=8)).date()  # Taiwan date

        guild_key = None if global_dsize else (interaction.guild.id if interaction.guild else None)
        max_size = get_server_config(guild_key, "dsize_max", 30)

        last = get_user_data(guild_key, user_id, "last_dsize")
        if last is not None and not isinstance(last, datetime):
            try:
                last = datetime.fromisoformat(str(last)).date()
            except Exception:
                last = datetime(1970, 1, 1).date()
        elif isinstance(last, datetime):
            last = last.date()
        if last is None:
            last = datetime(1970, 1, 1).date()

        if now == last:
            next_day = datetime.combine(last + timedelta(days=1), datetime.min.time())
            timestamp_next = int(next_day.timestamp())
            ephemeral_flag = True if interaction.guild else False
            await interaction.response.send_message(f"一天只能量一次屌長。<t:{timestamp_next}:R> 才能再次使用。", ephemeral=ephemeral_flag)
            return

        size = random.randint(1, max_size)
        d_string = "=" * (size - 1) if size > 1 else ""

        embed = discord.Embed(title=f"{interaction.user.name} 的長度：", color=0x00FF00)
        embed.add_field(name=f"{size} cm", value=f"8{d_string}D", inline=False)

        await interaction.response.send_message(embed=embed)

        set_user_data(guild_key, user_id, "last_dsize", now)
        set_user_data(guild_key, user_id, "last_dsize_size", size)

        surgery_percent = get_server_config(guild_key, "dsize_surgery_percent", 2)
        if percent_random(surgery_percent):
            fail_chance = random.randint(1, 100)

            class SurgeryView(discord.ui.View):
                def __init__(self):
                    super().__init__(timeout=60)

                async def on_timeout(self):
                    for c in self.children:
                        c.disabled = True
                    try:
                        await interaction.edit_original_response(content="你錯過了手術機會。", view=self)
                    except Exception:
                        pass

                @discord.ui.button(label="拒絕手術", style=discord.ButtonStyle.secondary)
                async def refuse(self, button_interaction: discord.Interaction, button: discord.ui.Button):
                    if button_interaction.user.id != user_id:
                        await button_interaction.response.send_message("這不是你的手術機會。", ephemeral=True)
                        return
                    self.stop()
                    await button_interaction.response.edit_message(content="已拒絕手術。", view=None)

                @discord.ui.button(label="同意手術", style=discord.ButtonStyle.danger)
                async def accept(self, button_interaction: discord.Interaction, button: discord.ui.Button):
                    if button_interaction.user.id != user_id:
                        await button_interaction.response.send_message("這不是你的手術機會。", ephemeral=True)
                        return
                    self.stop()
                    new_size = random.randint(1, get_server_config(guild_key, "dsize_surgery_max", 10))
                    will_fail = percent_random(fail_chance)
                    on_fail_size = random.randint(1, new_size) if will_fail else 0

                    embed2 = discord.Embed(title=f"{button_interaction.user.name} 的新長度：", color=0xFF0000)
                    embed2.add_field(name=f"{size} cm", value=f"8{d_string}D", inline=False)
                    try:
                        await button_interaction.response.edit_message(embed=embed2, view=None)
                    except Exception:
                        pass

                    for i in range(1, new_size + 1):
                        if will_fail and i == on_fail_size:
                            d_string_new = "?" * (size + i - 1)
                            embed = discord.Embed(title=f"{button_interaction.user.name} 的新長度：", color=0xff0000)
                            embed.add_field(name=f"{size + i} cm", value=f"8{d_string_new}D", inline=False)
                            await button_interaction.edit_original_response(content="正在手術中...？", embed=embed)
                            await discord.utils.sleep_until(datetime.utcnow() + timedelta(seconds=5))
                            d_string_new = "💥" * (size + i - 1)
                            embed.set_field_at(0, name=f"{size + i} cm", value=f"8{d_string_new}D", inline=False)
                            await button_interaction.edit_original_response(content="正在手術中...💥", embed=embed)
                            await discord.utils.sleep_until(datetime.utcnow() + timedelta(seconds=3))
                            ori = size + i - 2
                            while ori > 0:
                                d_string_new = "💥" * ori
                                embed.set_field_at(0, name=f"{size + i} cm", value=f"8{d_string_new}", inline=False)
                                await button_interaction.edit_original_response(content="正在手術中...💥", embed=embed)
                                await discord.utils.sleep_until(datetime.utcnow() + timedelta(milliseconds=500))
                                ori -= min(5, ori)
                            embed.set_field_at(0, name=f"-1 cm", value=f"8", inline=False)
                            await button_interaction.edit_original_response(content="手術失敗，你變男娘了。", embed=embed)
                            set_user_data(guild_key, user_id, "last_dsize_size", -1)
                            return

                        current_size = size + i
                        d_string_new = "=" * (current_size - 1) if current_size > 1 else ""
                        embed_prog = discord.Embed(title=f"{button_interaction.user.name} 的新長度：", color=0xFF0000)
                        embed_prog.add_field(name=f"{current_size} cm", value=f"8{d_string_new}D", inline=False)
                        try:
                            await button_interaction.edit_original_response(content="正在手術中...", embed=embed_prog)
                        except Exception:
                            pass
                        await asyncio.sleep(1)

                    embed_ok = discord.Embed(title=f"{button_interaction.user.name} 的新長度：", color=0x00FF00)
                    embed_ok.add_field(name=f"{size + new_size} cm", value=f"8{'=' * (size + new_size - 1)}D", inline=False)
                    try:
                        await button_interaction.edit_original_response(content="手術成功。", embed=embed_ok)
                    except Exception:
                        pass
                    set_user_data(guild_key, user_id, "last_dsize_size", size + new_size)

            try:
                await interaction.followup.send(f"你獲得了一次做手術的機會。\n請問你是否同意手術？\n-# 失敗機率：{fail_chance}%", view=SurgeryView())
            except Exception:
                try:
                    await interaction.channel.send(f"你獲得了一次做手術的機會。失敗機率：{fail_chance}%")
                except Exception:
                    pass

    @app_commands.command(name=app_commands.locale_str("leaderboard"), description="查看屌長排行榜")
    @app_commands.describe(limit="顯示前幾名 (預設10)", global_leaderboard="顯示全域排行榜 (預設否)")
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    async def dsize_leaderboard(self, interaction: discord.Interaction, limit: int = 10, global_leaderboard: bool = False):
        if limit < 1 or limit > 50:
            await interaction.response.send_message("限制必須在 1 到 50 之間。", ephemeral=True)
            return

        guild_id = None if global_leaderboard else (interaction.guild.id if interaction.guild else None)
        leaderboard = []

        all_data = get_all_user_data(guild_id, "last_dsize_size") or {}
        today = (datetime.utcnow() + timedelta(hours=8)).date()
        for user_id, data in all_data.items():
            try:
                uid = int(user_id)
            except Exception:
                uid = user_id
            size = data.get("last_dsize_size")
            user_date = get_user_data(guild_id, uid, "last_dsize")
            if user_date is not None and not isinstance(user_date, datetime):
                try:
                    user_date = datetime.fromisoformat(str(user_date)).date()
                except Exception:
                    user_date = datetime(1970, 1, 1).date()
            elif isinstance(user_date, datetime):
                user_date = user_date.date()
            if user_date is None or user_date != today:
                continue
            if size is not None:
                leaderboard.append((uid, size))

        if not leaderboard:
            await interaction.response.send_message("今天還沒有任何人量過屌長。", ephemeral=True)
            return

        leaderboard.sort(key=lambda x: x[1], reverse=True)
        top_users = leaderboard[:limit]

        description = ""
        for rank, (uid, size) in enumerate(top_users, start=1):
            size_display = "**男娘！**" if size == -1 else f"{size} cm"
            if global_leaderboard:
                user = await self.bot.fetch_user(uid)
            else:
                user = interaction.guild.get_member(uid) if interaction.guild else await self.bot.fetch_user(uid)
            if user:
                description += f"**{rank}. {user.name}** - {size_display}\n"
            else:
                description += f"**{rank}. 用戶ID {uid}** - {size_display}\n"

        embed = discord.Embed(title="今天的長度排行榜", description=description, color=0x00FF00)
        if interaction.guild and not global_leaderboard:
            embed.set_footer(text=interaction.guild.name, icon_url=interaction.guild.icon.url if interaction.guild and interaction.guild.icon else None)
        else:
            embed.set_footer(text="全域排行榜")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name=app_commands.locale_str("battle"), description="比屌長(需要雙方今天沒量過)")
    @app_commands.describe(opponent="要比屌長的對象")
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.allowed_installs(guilds=True, users=False)
    async def dsize_battle(self, interaction: discord.Interaction, opponent: discord.Member):
        original_user = interaction.user
        user_id = interaction.user.id
        opponent_id = opponent.id
        now = (datetime.utcnow() + timedelta(hours=8)).date()
        max_size = get_server_config(interaction.guild.id, "dsize_max", 30)

        if user_id == opponent_id:
            await interaction.response.send_message("不能跟自己比屌長。", ephemeral=True)
            return

        def to_date(val):
            if val is None:
                return datetime(1970, 1, 1).date()
            if isinstance(val, datetime):
                return val.date()
            try:
                return datetime.fromisoformat(str(val)).date()
            except Exception:
                return datetime(1970, 1, 1).date()

        last_user = to_date(get_user_data(interaction.guild.id, user_id, "last_dsize"))
        last_opponent = to_date(get_user_data(interaction.guild.id, opponent_id, "last_dsize"))

        if now == last_user:
            await interaction.response.send_message("你今天已經量過屌長了，不能再比了。", ephemeral=True)
            return
        if now == last_opponent:
            await interaction.response.send_message(f"{opponent.name} 今天已經量過屌長了，不能比。", ephemeral=True)
            return

        class ConfirmView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=30)
                self.value = None

            async def on_timeout(self):
                for c in self.children:
                    c.disabled = True
                try:
                    await interaction.edit_original_response(content="對決邀請已過期。", view=self)
                except Exception:
                    pass

            @discord.ui.button(label="✅ 同意", style=discord.ButtonStyle.success)
            async def confirm(self, button_interaction: discord.Interaction, button: discord.ui.Button):
                if button_interaction.user.id != opponent_id:
                    await button_interaction.response.send_message("這不是你的對決邀請。", ephemeral=True)
                    return
                self.stop()
                await button_interaction.response.edit_message(content="開始對決。", view=None)
                size_user = random.randint(1, max_size)
                size_opponent = random.randint(1, max_size)

                try:
                    msg = await button_interaction.original_response()
                except Exception:
                    msg = None

                for i in range(1, max(size_user, size_opponent)):
                    d_string_user = "=" * min(i, size_user - 1)
                    d_string_opponent = "=" * min(i, size_opponent - 1)
                    embed = discord.Embed(title="比長度", color=0x00FF00)
                    embed.add_field(name=f"{original_user.name} 的長度：", value=f"{size_user if i >= size_user - 1 else '??'} cm\n8{d_string_user}D", inline=False)
                    embed.add_field(name=f"{opponent.name} 的長度：", value=f"{size_opponent if i >= size_opponent - 1 else '??'} cm\n8{d_string_opponent}D", inline=False)
                    if msg:
                        try:
                            await msg.edit(embed=embed)
                        except Exception:
                            pass
                    await asyncio.sleep(0.08)

                if size_user > size_opponent:
                    result = f"🎉 {original_user.name} 勝利！"
                elif size_user < size_opponent:
                    result = f"🎉 {opponent.name} 勝利！"
                else:
                    result = "🤝 平手！"

                final_embed = discord.Embed(title="比長度", color=0x00FF00)
                final_embed.add_field(name=f"{original_user.name} 的長度：", value=f"{size_user} cm\n8{'=' * (size_user - 1)}D", inline=False)
                final_embed.add_field(name=f"{opponent.name} 的長度：", value=f"{size_opponent} cm\n8{'=' * (size_opponent - 1)}D", inline=False)
                final_embed.add_field(name="結果：", value=result, inline=False)
                if msg:
                    try:
                        await msg.edit(embed=final_embed)
                    except Exception:
                        pass

                set_user_data(interaction.guild.id, user_id, "last_dsize", now)
                set_user_data(interaction.guild.id, user_id, "last_dsize_size", size_user)
                set_user_data(interaction.guild.id, opponent_id, "last_dsize", now)
                set_user_data(interaction.guild.id, opponent_id, "last_dsize_size", size_opponent)

            @discord.ui.button(label="❌ 拒絕", style=discord.ButtonStyle.danger)
            async def cancel(self, button_interaction: discord.Interaction, button: discord.ui.Button):
                if button_interaction.user.id != opponent_id:
                    await button_interaction.response.send_message("這不是你的對決邀請。", ephemeral=True)
                    return
                self.stop()
                await button_interaction.response.edit_message(content="已拒絕對決邀請。", view=None)

        await interaction.response.send_message(f"{opponent.mention}，{interaction.user.name} 想跟你比長度。\n請在 30 秒內按下 ✅ 同意 或 ❌ 拒絕。", view=ConfirmView())

    @app_commands.command(name=app_commands.locale_str("settings"), description="設定 dsize")
    @app_commands.describe(setting="要設定的項目", value="設定的值")
    @app_commands.choices(setting=[
        app_commands.Choice(name="最大長度", value="dsize_max"),
        app_commands.Choice(name="手術機率(%)", value="dsize_surgery_percent"),
        app_commands.Choice(name="手術最大長度", value="dsize_surgery_max"),
    ])
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.default_permissions(administrator=True)
    async def dsize_settings(self, interaction: discord.Interaction, setting: str, value: str):
        guild_key = interaction.guild.id
        if setting == "dsize_max":
            if not value.isdigit() or int(value) < 1 or int(value) > 1000:
                await interaction.response.send_message("最大長度必須是介於 1 到 1000 之間的整數。", ephemeral=True)
                return
            set_server_config(guild_key, "dsize_max", int(value))
            await interaction.response.send_message(f"已設定最大長度為 {value} cm")
        elif setting == "dsize_surgery_percent":
            if not value.isdigit() or int(value) < 1 or int(value) > 100:
                await interaction.response.send_message("手術機率必須是介於 1 到 100 之間的整數。", ephemeral=True)
                return
            set_server_config(guild_key, "dsize_surgery_percent", int(value))
            await interaction.response.send_message(f"已設定手術機率為 {str(int(value))}%")
        elif setting == "dsize_surgery_max":
            set_server_config(guild_key, "dsize_surgery_max", int(value))
            await interaction.response.send_message(f"已設定手術最大長度為 {value} cm")
        else:
            await interaction.response.send_message("未知的設定項目。")


asyncio.run(bot.add_cog(DSize(bot)))


if __name__ == "__main__":
    start_bot()
    
