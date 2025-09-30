# Powered by ChatGPT lol
import discord
import random
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timedelta, timezone
from globalenv import bot, start_bot, get_user_data, set_user_data, get_all_user_data


@bot.tree.command(name="dsize", description="量屌長")
async def dsize(interaction: discord.Interaction):
    user_id = interaction.user.id
    now = datetime.utcnow().astimezone(timezone(timedelta(hours=8))).date()  # 台灣時間
    last = get_user_data(interaction.guild.id, user_id, "last_dsize")
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
        # Convert last (date) to datetime at midnight in Taiwan timezone
        next_day = datetime.combine(last + timedelta(days=1), datetime.min.time()).replace(tzinfo=timezone(timedelta(hours=8)))
        timestamp_next = next_day.astimezone(timezone.utc)  # Convert to UTC for Discord timestamp
        await interaction.response.send_message(f"一天只能量一次屌長。<t:{int(timestamp_next.timestamp())}:R> 才能再次使用。", ephemeral=True)
        return

    # 隨機產生長度 (2-30)
    size = random.randint(2, 30)
    d_string = "=" * (size - 2)

    # 建立 Embed 訊息
    embed = discord.Embed(title=f"{interaction.user.name} 的長度：", color=0x00ff00)
    embed.add_field(name=f"{size} cm", value=f"8{d_string}D", inline=False)

    await interaction.response.send_message(embed=embed)

    # 更新使用時間
    set_user_data(interaction.guild.id, user_id, "last_dsize", now)
    set_user_data(interaction.guild.id, user_id, "last_dsize_size", size)


@bot.tree.command(name="dsize-排行榜", description="查看屌長排行榜")
async def dsize_leaderboard(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    leaderboard = []

    all_data = get_all_user_data(guild_id, "last_dsize_size")
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
        if user_date is not None and user_date != datetime.utcnow().astimezone(timezone(timedelta(hours=8))).date():
            continue
        if size is not None:
            leaderboard.append((user_id, size))

    if not leaderboard:
        await interaction.response.send_message("今天還沒有任何人量過屌長。", ephemeral=True)
        return

    # 按照大小排序並取前10名
    leaderboard.sort(key=lambda x: x[1], reverse=True)
    top10 = leaderboard[:10]

    # 建立排行榜訊息
    description = ""
    for rank, (user_id, size) in enumerate(top10, start=1):
        user = interaction.guild.get_member(user_id)
        if user:
            description += f"**{rank}. {user.name}** - {size} cm\n"
        else:
            description += f"**{rank}. 用戶ID {user_id}** - {size} cm\n"

    embed = discord.Embed(title="今天的長度排行榜", description=description, color=0x00ff00)
    # server info
    embed.set_footer(text=interaction.guild.name, icon_url=interaction.guild.icon.url if interaction.guild.icon else None)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="dsize-對決", description="比屌長(需要雙方今天沒有量過)")
@app_commands.describe(opponent="要比屌長的對象")
async def dsize_battle(interaction: discord.Interaction, opponent: discord.Member):
    original_user = interaction.user
    user_id = interaction.user.id
    opponent_id = opponent.id
    now = datetime.utcnow().astimezone(timezone(timedelta(hours=8))).date()  # 台灣時間

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
        await interaction.response.send_message("你今天已經量過屌長了，不能再比了。", ephemeral=True)
        return
    if now == last_opponent:
        await interaction.response.send_message(f"{opponent.name} 今天已經量過屌長了，不能比。", ephemeral=True)
        return
    
    class dsize_Confirm(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=30)
            self.value = None

        @discord.ui.button(label="✅ 同意", style=discord.ButtonStyle.success)
        async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user.id != opponent_id:
                await interaction.response.send_message("這不是你的對決邀請。", ephemeral=True)
                return
            self.value = True
            self.stop()
            await interaction.response.edit_message(content="開始對決。", view=None)
            size_user = random.randint(2, 30)
            size_opponent = random.randint(2, 30)
            
            # 取得訊息物件
            msg = await interaction.original_response()

            for i in range(1, max(size_user, size_opponent) - 1):
                d_string_user = "=" * min(i, size_user - 2)
                d_string_opponent = "=" * min(i, size_opponent - 2)
                embed = discord.Embed(title="比長度", color=0x00ff00)
                embed.add_field(
                    name=f"{original_user.name} 的長度：",
                    value=f"{size_user if i >= size_user - 2 else '??'} cm\n8{d_string_user}D",
                    inline=False,
                )
                embed.add_field(
                    name=f"{opponent.name} 的長度：",
                    value=f"{size_opponent if i >= size_opponent - 2 else '??'} cm\n8{d_string_opponent}D",
                    inline=False,
                )
                await msg.edit(embed=embed)
                await discord.utils.sleep_until(datetime.utcnow() + timedelta(milliseconds=80))  # 約0.08秒

            # 最終結果
            if size_user > size_opponent:
                result = f"🎉 {original_user.name} 勝利！"
            elif size_user < size_opponent:
                result = f"🎉 {opponent.name} 勝利！"
            else:
                result = "🤝 平手！"

            d_string_user = "=" * (size_user - 2)
            d_string_opponent = "=" * (size_opponent - 2)
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


if __name__ == "__main__":
    start_bot()
