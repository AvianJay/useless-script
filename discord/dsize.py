# Powered by ChatGPT lol
import discord
import random
import asyncio
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timedelta, timezone
from globalenv import bot, start_bot, get_user_data, set_user_data, get_all_user_data, get_server_config, set_server_config, modules


def percent_random(percent: int) -> bool:
    try:
        percent = int(percent)
        if percent <= 0:
            return False
        return random.random() < percent / 100
    except Exception:
        return False


@bot.tree.command(name="dsize", description="量屌長")
@app_commands.describe(global_dsize="是否使用全域紀錄 (預設否)")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def dsize(interaction: discord.Interaction, global_dsize: bool = False):
    user_id = interaction.user.id
    # Use timezone-aware UTC and convert to Taiwan time (UTC+8)
    # ew broken
    now = (datetime.utcnow() + timedelta(hours=8)).date()  # 台灣時間

    # If invoked in DM (user-installed command), use None as the guild_key.
    # Otherwise use the guild id to keep per-server records.
    guild_key = interaction.guild.id if interaction.guild else None
    if global_dsize:
        guild_key = None  # override to global
    
    if guild_key:
        max_size = get_server_config(guild_key, "dsize_max", 30)
    else:
        max_size = 30

    last = get_user_data(guild_key, user_id, "last_dsize")
    if last is not None and not isinstance(last, datetime):
        # If last is a string (e.g., from JSON), convert to date
        try:
            last = datetime.fromisoformat(str(last)).date()
        except Exception:
            last = datetime(1970, 1, 1).date()
    elif isinstance(last, datetime):
        last = last.date()
    if last is None:
        last = datetime(1970, 1, 1).date()  # 如果沒有紀錄，設為很久以前

    # 檢查是否已經使用過指令，並且是否已超過一天
    if now == last:
        # calculate time left
        next_day = datetime.combine(last + timedelta(days=1), datetime.min.time()).replace(tzinfo=timezone(timedelta(hours=8)))
        timestamp_next = next_day.astimezone(timezone.utc)  # Convert to UTC for Discord timestamp
        # ephemeral only works in guild interactions; for DMs just send a normal message
        ephemeral_flag = True if interaction.guild else False
        await interaction.response.send_message(f"一天只能量一次屌長。<t:{int(timestamp_next.timestamp())}:R> 才能再次使用。", ephemeral=ephemeral_flag)
        return

    # 隨機產生長度
    size = random.randint(1, max_size)
    fake_size = None
    if "ItemSystem" in modules:
        fake_ruler_used = get_user_data(guild_key, user_id, "dsize_fake_ruler_used", False)
        if fake_ruler_used:
            extra_size = random.randint(10, 20)
            fake_size = size + extra_size
            # reset fake ruler usage
            set_user_data(guild_key, user_id, "dsize_fake_ruler_used", False)
            set_user_data(guild_key, user_id, "last_dsize_fake_size", fake_size)
    final_size = fake_size if fake_size is not None else size

    # 建立 Embed 訊息
    embed = discord.Embed(title=f"{interaction.user.name} 的長度：", color=0x00ff00)
    embed.add_field(name="1 cm", value=f"8D", inline=False)

    await interaction.response.send_message(embed=embed)
    # animate to size
    for i in range(1, size + 1):
        d_string = "=" * (i - 1)
        current_size = i
        if i == size:  # final size
            current_size = final_size
        embed.set_field_at(0, name=f"{current_size} cm", value=f"8{d_string}D", inline=False)
        await interaction.edit_original_response(embed=embed)
        await asyncio.sleep(0.1)

    # 更新使用時間 — 存到對應的 guild_key（若為 user-install 則是 None）
    set_user_data(guild_key, user_id, "last_dsize", now)
    set_user_data(guild_key, user_id, "last_dsize_size", size)
    
    surgery_percent = get_server_config(guild_key, "dsize_surgery_percent", 10)
    # check if user got surgery chance
    if percent_random(surgery_percent):
        fail_chance = random.randint(1, 100)
        class dsize_SurgeryView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=60)  # 60 seconds to click
            
            async def on_timeout(self):
                for child in self.children:
                    child.disabled = True
                await interaction.edit_original_response(content="手術機會已過期。", view=self)

            @discord.ui.button(label="拒絕手術", style=discord.ButtonStyle.secondary)
            async def surgery(self, interaction: discord.Interaction, button: discord.ui.Button):
                if interaction.user.id != user_id:
                    await interaction.response.send_message("這不是你的手術機會。", ephemeral=True)
                    return
                self.stop()
                await interaction.response.edit_message(content="已拒絕手術。", view=None)

            @discord.ui.button(label="同意手術", style=discord.ButtonStyle.danger)
            async def surgery(self, interaction: discord.Interaction, button: discord.ui.Button):
                if interaction.user.id != user_id:
                    await interaction.response.send_message("這不是你的手術機會。", ephemeral=True)
                    return
                self.stop()
                new_size = random.randint(1, get_server_config(guild_key, "dsize_surgery_max", 10))
                will_fail = percent_random(fail_chance)
                on_fail_size = random.randint(1, new_size) if will_fail else 0
                embed = discord.Embed(title=f"{interaction.user.name} 的新長度：", color=0xff0000)
                embed.add_field(name=f"{size} cm", value=f"8{d_string}D", inline=False)
                await interaction.response.edit_message(embed=embed, view=None)
                # animate to new size
                for i in range(1, new_size + 1):
                    if will_fail and i == on_fail_size:
                        d_string_new = "?" * (size + i - 1)
                        embed = discord.Embed(title=f"{interaction.user.name} 的新長度：", color=0xff0000)
                        embed.add_field(name=f"{size + i} cm", value=f"8{d_string_new}D", inline=False)
                        await interaction.edit_original_response(content="正在手術中...？", embed=embed)
                        await asyncio.sleep(3)
                        d_string_new = "💥" * (size + i - 1)
                        embed.set_field_at(0, name=f"{size + i} cm", value=f"8{d_string_new}D", inline=False)
                        await interaction.edit_original_response(content="正在手術中...💥", embed=embed)
                        await asyncio.sleep(1)
                        ori = size + i - 2
                        while ori > 0:
                            d_string_new = "💥" * ori
                            embed.set_field_at(0, name=f"{size + i} cm", value=f"8{d_string_new}", inline=False)
                            await interaction.edit_original_response(content="正在手術中...💥", embed=embed)
                            await discord.utils.sleep_until(datetime.utcnow() + timedelta(seconds=1))
                            ori -= min(3, ori)
                        embed.set_field_at(0, name=f"-1 cm", value=f"8", inline=False)
                        await interaction.edit_original_response(content="手術失敗，你變男娘了。", embed=embed)
                        set_user_data(guild_key, user_id, "last_dsize_size", -1)
                        return
                    d_string_new = "=" * (size + i - 2)
                    current_size = size + i
                    embed = discord.Embed(title=f"{interaction.user.name} 的新長度：", color=0xff0000)
                    embed.add_field(name=f"{current_size} cm", value=f"8{d_string_new}D", inline=False)
                    await interaction.edit_original_response(content="正在手術中...", embed=embed)
                    await asyncio.sleep(1)
                embed = discord.Embed(title=f"{interaction.user.name} 的新長度：", color=0x00ff00)
                embed.add_field(name=f"{size + new_size} cm", value=f"8{'=' * (size + new_size - 2)}D", inline=False)
                await interaction.edit_original_response(content="手術成功。", embed=embed)
                set_user_data(guild_key, user_id, "last_dsize_size", new_size + size)
        await interaction.followup.send(f"你獲得了一次做手術的機會。\n請問你是否同意手術？\n-# 失敗機率：{fail_chance}%", view=dsize_SurgeryView())


@bot.tree.command(name=app_commands.locale_str("dsize-leaderboard"), description="查看屌長排行榜")
@app_commands.describe(limit="顯示前幾名 (預設10)", global_leaderboard="顯示全域排行榜 (預設否)")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def dsize_leaderboard(interaction: discord.Interaction, limit: int = 10, global_leaderboard: bool = False):
    if global_leaderboard:
        guild_id = None  # global
    else:
        guild_id = interaction.guild.id if interaction.guild else None  # None for global
    leaderboard = []
    if limit < 1 or limit > 50:
        await interaction.response.send_message("限制必須在 1 到 50 之間。", ephemeral=True)
        return

    all_data = get_all_user_data(guild_id, "last_dsize_size")
    all_data_fake = get_all_user_data(guild_id, "last_dsize_fake_size")
    for user_id, data in all_data.items():
        size = data.get("last_dsize_size")
        # check dsize date is today
        user_date = get_user_data(guild_id, user_id, "last_dsize")
        if user_date is not None and not isinstance(user_date, datetime):
            # If user_date is a string (e.g., from JSON), convert to date
            try:
                user_date = datetime.fromisoformat(str(user_date)).date()
            except Exception:
                user_date = datetime(1970, 1, 1).date()
        elif isinstance(user_date, datetime):
            user_date = user_date.date()
        if user_date is not None and user_date != (datetime.utcnow() + timedelta(hours=8)).date():
            continue
        if size is not None:
            leaderboard.append((user_id, size))

    if not leaderboard:
        await interaction.response.send_message("今天還沒有任何人量過屌長。", ephemeral=True)
        return

    # 按照大小排序並取前limit名
    leaderboard.sort(key=lambda x: x[1], reverse=True)
    top_users = leaderboard[:limit]

    # 建立排行榜訊息
    description = ""
    for rank, (user_id, size) in enumerate(top_users, start=1):
        if size == -1:
            size = "**男娘！**"
        else:
            if all_data_fake.get(user_id) and all_data_fake[user_id].get("last_dsize_fake_size") is not None:
                size = f"{size} {all_data_fake[user_id].get('last_dsize_fake_size')} cm..?"
            else:
                size = f"{size} cm"
        if global_leaderboard:
            user = await bot.fetch_user(user_id)
        else:
            user = interaction.guild.get_member(user_id) if interaction.guild else await bot.fetch_user(user_id)
        if user:
            description += f"**{rank}. {user.name}** - {size}\n"
        else:
            description += f"**{rank}. 用戶ID {user_id}** - {size}\n"

    embed = discord.Embed(title="今天的長度排行榜", description=description, color=0x00ff00)
    # server info
    if interaction.guild and not global_leaderboard:
        embed.set_footer(text=interaction.guild.name, icon_url=interaction.guild.icon.url if interaction.guild.icon else None)
    else:
        embed.set_footer(text="全域排行榜")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name=app_commands.locale_str("dsize-battle"), description="比屌長(需要雙方今天沒有量過)")
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
@app_commands.describe(opponent="要比屌長的對象")
async def dsize_battle(interaction: discord.Interaction, opponent: discord.Member):
    original_user = interaction.user
    user_id = interaction.user.id
    opponent_id = opponent.id
    now = (datetime.utcnow() + timedelta(hours=8)).date()  # 台灣時間
    max_size = get_server_config(interaction.guild.id, "dsize_max", 30)

    if user_id == opponent_id:
        await interaction.response.send_message("不能跟自己比屌長。", ephemeral=True)
        return

    last_user = get_user_data(interaction.guild.id, user_id, "last_dsize")
    last_opponent = get_user_data(interaction.guild.id, opponent_id, "last_dsize")
    
    if last_user is not None and not isinstance(last_user, datetime):
        # If last_user is a string (e.g., from JSON), convert to date
        try:
            last_user = datetime.fromisoformat(str(last_user)).date()
        except Exception:
            last_user = datetime(1970, 1, 1).date()
    elif isinstance(last_user, datetime):
        last_user = last_user.date()
    if last_opponent is not None and not isinstance(last_opponent, datetime):
        # If last_opponent is a string (e.g., from JSON), convert to date
        try:
            last_opponent = datetime.fromisoformat(str(last_opponent)).date()
        except Exception:
            last_opponent = datetime(1970, 1, 1).date()
    elif isinstance(last_opponent, datetime):
        last_opponent = last_opponent.date()

    if last_user is None:
        last_user = datetime(1970, 1, 1).date()
    if last_opponent is None:
        last_opponent = datetime(1970, 1, 1).date()

    if now == last_user:
        await interaction.response.send_message("你今天已經量過屌長了。", ephemeral=True)
        return
    if now == last_opponent:
        await interaction.response.send_message(f"{opponent.name} 今天已經量過屌長了。", ephemeral=True)
        return
    
    class dsize_Confirm(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=30)
            self.value = None
        
        async def on_timeout(self):
            for child in self.children:
                child.disabled = True
            await interaction.edit_original_response(content="對決邀請已過期。", view=self)

        @discord.ui.button(label="✅ 同意", style=discord.ButtonStyle.success)
        async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user.id != opponent_id:
                await interaction.response.send_message("這不是你的對決邀請。", ephemeral=True)
                return
            self.value = True
            self.stop()
            await interaction.response.edit_message(content="開始對決。", view=None)
            size_user = random.randint(1, max_size)
            size_opponent = random.randint(1, max_size)

            # 取得訊息物件
            msg = await interaction.original_response()

            for i in range(1, max(size_user, size_opponent) - 1):
                d_string_user = "=" * min(i, size_user - 1)
                d_string_opponent = "=" * min(i, size_opponent - 1)
                embed = discord.Embed(title="比長度", color=0x00ff00)
                embed.add_field(
                    name=f"{original_user.name} 的長度：",
                    value=f"{size_user if i >= size_user - 1 else '??'} cm\n8{d_string_user}D",
                    inline=False,
                )
                embed.add_field(
                    name=f"{opponent.name} 的長度：",
                    value=f"{size_opponent if i >= size_opponent - 1 else '??'} cm\n8{d_string_opponent}D",
                    inline=False,
                )
                await msg.edit(embed=embed)
                await asyncio.sleep(0.1)

            # 最終結果
            if size_user > size_opponent:
                result = f"🎉 {original_user.name} 勝利！"
            elif size_user < size_opponent:
                result = f"🎉 {opponent.name} 勝利！"
            else:
                result = "🤝 平手！"

            d_string_user = "=" * (size_user - 1)
            d_string_opponent = "=" * (size_opponent - 1)
            embed = discord.Embed(title="比長度", color=0x00ff00)
            embed.add_field(name=f"{original_user.name} 的長度：", value=f"{size_user} cm\n8{d_string_user}D", inline=False)
            embed.add_field(name=f"{opponent.name} 的長度：", value=f"{size_opponent} cm\n8{d_string_opponent}D", inline=False)
            embed.add_field(name="結果：", value=result, inline=False)
            await msg.edit(embed=embed)

            set_user_data(interaction.guild.id, user_id, "last_dsize", now)
            set_user_data(interaction.guild.id, user_id, "last_dsize_size", size_user)
            set_user_data(interaction.guild.id, opponent_id, "last_dsize", now)
            set_user_data(interaction.guild.id, opponent_id, "last_dsize_size", size_opponent)

        @discord.ui.button(label="❌ 拒絕", style=discord.ButtonStyle.danger)
        async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user.id != opponent_id:
                await interaction.response.send_message("這不是你的對決邀請。", ephemeral=True)
                return
            self.value = False
            self.stop()
            await interaction.response.edit_message(content="已拒絕對決邀請。", view=None)

    # 徵求對方同意
    await interaction.response.send_message(f"{opponent.mention}，{interaction.user.name} 想跟你比長度。\n請在 30 秒內按下 ✅ 同意 或 ❌ 拒絕。", ephemeral=False, view=dsize_Confirm())


# server settings command
@bot.tree.command(name=app_commands.locale_str("dsize-settings"), description="設定dsize")
@app_commands.describe(setting="要設定的項目", value="設定的值")
@app_commands.choices(setting=[
    app_commands.Choice(name="最大長度", value="dsize_max"),
    app_commands.Choice(name="手術機率(%)", value="dsize_surgery_percent"),
    app_commands.Choice(name="手術最大長度", value="dsize_surgery_max"),
])
@app_commands.default_permissions(administrator=True)
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
async def dsize_settings(interaction: discord.Interaction, setting: str, value: str):
    guild_key = interaction.guild.id
    if setting == "dsize_max":
        # check between 1 and 1000
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


# setup items
async def use_fake_ruler(interaction: discord.Interaction):
    user_id = interaction.user.id
    guild_key = interaction.guild.id if interaction.guild else None
    if get_user_data(guild_key, user_id, "dsize_fake_ruler_used", False):
        await interaction.response.send_message("你今天已經使用過自欺欺人尺了。", ephemeral=True)
        return
    ItemSystem.remove_item_from_user(interaction.user.id, "fake_ruler", 1)
    set_user_data(guild_key, user_id, "dsize_fake_ruler_used", True)
    await interaction.response.send_message("你使用了自欺欺人尺！\n下次量長度時或許會更長？")

if "ItemSystem" in modules:
    items = [
        {
            "id": "fake_ruler",
            "name": "自欺欺人尺",
            "description": "使用後下次量長度時或許會更長？",
            "callback": use_fake_ruler,
        }
    ]
    import ItemSystem
    ItemSystem.items.extend(items)


if __name__ == "__main__":
    start_bot()
