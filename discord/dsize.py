# Powered by ChatGPT lol
import discord
import random
import asyncio
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timedelta, timezone
from globalenv import bot, start_bot, get_user_data, set_user_data, get_all_user_data, get_server_config, set_server_config, modules, get_command_mention
from PIL import Image, ImageDraw
from io import BytesIO
from logger import log
import os
import json
if "OwnerTools" in modules:
    import OwnerTools
else:
    OwnerTools = None


def percent_random(percent: int) -> bool:
    try:
        percent = int(percent)
        if percent <= 0:
            return False
        return random.random() < percent / 100
    except Exception:
        return False


async def process_checkin(user_id: int) -> tuple[bool, int]:
    """
    Process daily check-in for a user (always global).
    Returns: (is_new_checkin, checkin_streak)
    """
    now = (datetime.utcnow() + timedelta(hours=8)).date()  # 台灣時間
    
    # Get last checkin date (always global - guild_id = 0)
    last_checkin = get_user_data(0, user_id, "last_checkin")
    if last_checkin is not None and not isinstance(last_checkin, datetime):
        try:
            last_checkin = datetime.fromisoformat(str(last_checkin)).date()
        except Exception:
            last_checkin = None
    elif isinstance(last_checkin, datetime):
        last_checkin = last_checkin.date()
    
    # Check if already checked in today
    if last_checkin == now:
        # Already checked in today
        statistics = get_user_data(0, user_id, "dsize_statistics", {})
        return False, statistics.get("checkin_streak", 0), False, None

    # reset claim reward unsuccessful flag
    set_user_data(0, user_id, "claim_reward_unsuccessful", False)

    # Calculate streak
    statistics = get_user_data(0, user_id, "dsize_statistics", {})
    checkin_streak = statistics.get("checkin_streak", 0)
    broke_streak = False
    broke_streak_on = None
    
    # Check if streak continues (last checkin was yesterday)
    if last_checkin and last_checkin == now - timedelta(days=1):
        checkin_streak += 1
    else:
        # Reset streak
        if last_checkin and last_checkin < now - timedelta(days=1):
            broke_streak = True
            broke_streak_on = checkin_streak
        checkin_streak = 1  # start new streak
    
    # Update statistics
    statistics["total_checkins"] = statistics.get("total_checkins", 0) + 1
    statistics["checkin_streak"] = checkin_streak
    set_user_data(0, user_id, "dsize_statistics", statistics)
    set_user_data(0, user_id, "last_checkin", now)
    
    return True, checkin_streak, broke_streak, broke_streak_on


async def handle_checkin_rewards(interaction: discord.Interaction, user_id: int, checkin_streak: int, guild_key: int = None):
    """
    Handle check-in rewards and goal selection.
    Shows rewards only on milestone days (7, and user-selected goals).
    """
    if checkin_streak < 7:
        # No rewards shown until day 7
        return
    
    # Get user's current goal
    current_goal = get_user_data(0, user_id, "checkin_goal")
    
    # Check if this is a milestone day
    is_milestone = False
    if checkin_streak == 7 or (current_goal and checkin_streak >= current_goal):
        is_milestone = True
    
    if not is_milestone:
        return
    
    # check not global
    if guild_key is None:
        set_user_data(0, user_id, "claim_reward_unsuccessful", True)
        await interaction.followup.send(f"{interaction.user.mention}\n你獲得了簽到獎勵！\n請在有此機器人的伺服器中使用 {await get_command_mention('dsize')} 以領取獎勵。")
        return
    set_user_data(0, user_id, "claim_reward_unsuccessful", False)
    
    # Give random reward
    if "ItemSystem" in modules:
        # Random reward pool
        # use list instead of tuple to make it mutable
        level_1_rewards = [
            ["grass", 5, "草 x5"],
            ["fake_ruler", 1, "自欺欺人尺 x1"],
            ["anti_surgery", 1, "抗手術藥物 x1"],
        ]

        level_2_rewards = [
            ["grass", 20, "草 x20"],
            ["fake_ruler", 5, "自欺欺人尺 x5"],
            ["anti_surgery", 5, "抗手術藥物 x5"],
            ["surgery", 1, "手術刀 x1"],
            ["rusty_surgery", 1, "生鏽的手術刀 x1"],
        ]

        level_3_rewards = [
            ["grass", 100, "草 x100"],
            ["fake_ruler", 20, "自欺欺人尺 x20"],
            ["anti_surgery", 20, "抗手術藥物 x20"],
            ["surgery", 3, "手術刀 x3"],
            ["rusty_surgery", 3, "生鏽的手術刀 x3"],
        ]
        
        if checkin_streak == 7:
            reward = random.choice(level_1_rewards)
        else:
            reward = get_user_data(0, user_id, "checkin_reward")
        await ItemSystem.give_item_to_user(guild_key, user_id, reward[0], reward[1])

        # Update statistics
        statistics = get_user_data(0, user_id, "dsize_statistics", {})
        statistics["total_checkins"] = statistics.get("total_checkins", 0) + 1
        statistics["checkin_streak"] = checkin_streak
        set_user_data(0, user_id, "dsize_statistics", statistics)
        level_1_reward = random.choice(level_1_rewards)
        level_2_reward = random.choice(level_2_rewards)
        level_3_reward = random.choice(level_3_rewards)
        
        # Create goal selection view
        class GoalSelectionView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=300)  # 5 minutes
                self.selected_goal = None
            
            @discord.ui.button(label=f"+7 天 ({level_1_reward[2]} x {level_1_reward[1]})", style=discord.ButtonStyle.primary)
            async def goal_7(self, interaction: discord.Interaction, button: discord.ui.Button):
                if interaction.user.id != user_id:
                    await interaction.response.send_message("這不是你的目標選擇。", ephemeral=True)
                    return
                self.selected_goal = checkin_streak + 7
                set_user_data(0, user_id, "checkin_goal", self.selected_goal)
                set_user_data(0, user_id, "checkin_reward", level_1_reward)
                await interaction.response.edit_message(
                    content=f"✅ 已選擇目標：{self.selected_goal} 天！繼續加油！",
                    view=None
                )
                self.stop()
            
            @discord.ui.button(label=f"+14 天 ({level_2_reward[2]} x {level_2_reward[1]})", style=discord.ButtonStyle.success)
            async def goal_14(self, interaction: discord.Interaction, button: discord.ui.Button):
                if interaction.user.id != user_id:
                    await interaction.response.send_message("這不是你的目標選擇。", ephemeral=True)
                    return
                self.selected_goal = checkin_streak + 14
                set_user_data(0, user_id, "checkin_goal", self.selected_goal)
                set_user_data(0, user_id, "checkin_reward", level_2_reward)
                await interaction.response.edit_message(
                    content=f"✅ 已選擇目標：{self.selected_goal} 天！繼續加油！",
                    view=None
                )
                self.stop()
            
            @discord.ui.button(label=f"+30 天 ({level_3_reward[2]} x {level_3_reward[1]})", style=discord.ButtonStyle.danger)
            async def goal_30(self, interaction: discord.Interaction, button: discord.ui.Button):
                if interaction.user.id != user_id:
                    await interaction.response.send_message("這不是你的目標選擇。", ephemeral=True)
                    return
                self.selected_goal = checkin_streak + 30
                set_user_data(0, user_id, "checkin_goal", self.selected_goal)
                set_user_data(0, user_id, "checkin_reward", level_3_reward)
                await interaction.response.edit_message(
                    content=f"✅ 已選擇目標：{self.selected_goal} 天！繼續加油！",
                    view=None
                )
                self.stop()
        
        # Send reward notification with goal selection
        embed = discord.Embed(
            title="🎉 簽到獎勵！",
            description=f"恭喜達成 {checkin_streak} 天連續簽到！\n獲得：{reward[2]} x {reward[1]}！",
            color=0xffd700
        )
        embed.add_field(
            name="選擇下一個目標",
            value="請選擇你的下一個簽到目標天數：",
            inline=False
        )
        await interaction.followup.send(embed=embed, view=GoalSelectionView())


@bot.tree.command(name="dsize", description="量屌長")
@app_commands.describe(global_dsize="是否使用全域紀錄 (預設否)")
@app_commands.choices(global_dsize=[
    app_commands.Choice(name="否", value=0),
    app_commands.Choice(name="是", value=1),
])
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def dsize(interaction: discord.Interaction, global_dsize: int = 0):
    global_dsize = bool(global_dsize)
    user_id = interaction.user.id
    # Use timezone-aware UTC and convert to Taiwan time (UTC+8)
    # ew broken
    now = (datetime.utcnow() + timedelta(hours=8)).date()  # 台灣時間

    # If invoked in DM (user-installed command), use None as the guild_key.
    # Otherwise use the guild id to keep per-server records.
    guild_key = interaction.guild.id if interaction.guild else None
    if global_dsize:
        guild_key = None  # override to global
    if not interaction.is_guild_integration():
        guild_key = None
        global_dsize = True
    
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

    set_user_data(guild_key, user_id, "last_dsize", now)
    statistics = get_user_data(0, user_id, "dsize_statistics", {})
    statistics["total_uses"] = statistics.get("total_uses", 0) + 1
    set_user_data(0, user_id, "dsize_statistics", statistics)
    
    # Process daily check-in (always global)
    is_new_checkin, checkin_streak, broke_streak, broke_streak_on = await process_checkin(user_id)

    # 隨機產生長度
    size = random.randint(1, max_size)
    fake_size = None
    if "ItemSystem" in modules:
        fake_ruler_used = get_user_data(guild_key, user_id, "dsize_fake_ruler_used", "False") == "True"
        if fake_ruler_used:
            extra_size = random.randint(10, 20)
            fake_size = size + extra_size
            # reset fake ruler usage
            set_user_data(guild_key, user_id, "dsize_fake_ruler_used", False)
            set_user_data(guild_key, user_id, "dsize_fake_ruler_used_date", now)
            set_user_data(guild_key, user_id, "last_dsize_fake_size", fake_size)
    final_size = fake_size if fake_size is not None else size

    # 建立 Embed 訊息
    embed = discord.Embed(title=f"{interaction.user.display_name} 的長度：", color=0x00ff00)
    embed.add_field(name="1 cm", value=f"8D", inline=False)
    embed.timestamp = datetime.now(timezone.utc)
    
    # Set footer with check-in info
    if is_new_checkin:
        if broke_streak:
            footer_text = f"你在第 {broke_streak_on} 天打破了簽到紀錄，重新開始簽到！ | 簽到第 {checkin_streak} 天！"
        else:
            footer_text = f"簽到第 {checkin_streak} 天！"
        if not guild_key:
            footer_text += " | 此次量測為全域紀錄。"
    else:
        if not guild_key:
            footer_text = "此次量測為全域紀錄。"
        else:
            footer_text = None
    
    if footer_text:
        embed.set_footer(text=footer_text)

    await interaction.response.send_message(embed=embed)
    # animate to size
    speed = size // 50 + 1
    for i in range(1, size + 1, speed):
        d_string = "=" * (i - 1)
        current_size = i
        embed.set_field_at(0, name=f"{current_size} cm", value=f"8{d_string}D", inline=False)
        await interaction.edit_original_response(embed=embed)
        await asyncio.sleep(0.1)
    # final
    d_string = "=" * (size - 1)
    embed.set_field_at(0, name=f"{final_size} cm", value=f"8{d_string}D", inline=False)
    await interaction.edit_original_response(embed=embed)

    # 更新使用時間 — 存到對應的 guild_key（若為 user-install 則是 None）
    set_user_data(guild_key, user_id, "last_dsize_size", size)
    
    # Save to history
    history = get_user_data(guild_key, user_id, "dsize_history", [])
    history.append({
        "date": now.isoformat(),
        "size": final_size,
        "type": "測量"
    })
    # Keep only last 100 records to avoid database bloat
    if len(history) > 100:
        history = history[-100:]
    set_user_data(guild_key, user_id, "dsize_history", history)
    
    # print(f"[DSize] {interaction.user} measured {size} cm in guild {guild_key if guild_key else 'DM/Global'}")
    log(f"量了 {size} cm, 伺服器: {guild_key if guild_key else '全域'}", module_name="dsize", user=interaction.user, guild=interaction.guild)
    
    # Handle check-in rewards if applicable (milestone days only)
    claimed_unsuccessful = get_user_data(0, user_id, "claim_reward_unsuccessful", False)
    if is_new_checkin:
        await handle_checkin_rewards(interaction, user_id, checkin_streak, guild_key)
        log(f"簽到成功，連續 {checkin_streak} 天", module_name="dsize", user=interaction.user, guild=interaction.guild)
    elif claimed_unsuccessful:
        await handle_checkin_rewards(interaction, user_id, checkin_streak, guild_key)
        set_user_data(0, user_id, "claim_reward_unsuccessful", False)
        log(f"簽到成功，連續 {checkin_streak} 天 (補發獎勵)", module_name="dsize", user=interaction.user, guild=interaction.guild)

    surgery_percent = get_server_config(guild_key, "dsize_surgery_percent", 10)
    drop_item_chance = get_server_config(guild_key, "dsize_drop_item_chance", 5)
    # check if user got surgery chance
    if percent_random(surgery_percent):
        if get_user_data(guild_key, user_id, "dsize_anti_surgery") == str(now):
            await interaction.followup.send(f"{interaction.user.mention}\n由於你使用了抗手術藥物，你無法進行手術。")
            return
        log("獲得了手術機會", module_name="dsize", user=interaction.user, guild=interaction.guild)
        fail_chance = random.randint(1, 100)
        class dsize_SurgeryView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=60)  # 60 seconds to click
            
            async def on_timeout(self):
                for child in self.children:
                    child.disabled = True
                await surgery_msg.edit(content="手術機會已過期。", view=self)

            @discord.ui.button(label="拒絕手術", style=discord.ButtonStyle.secondary)
            async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
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
                statistics = get_user_data(0, user_id, "dsize_statistics", {})
                statistics["total_surgeries"] = statistics.get("total_surgeries", 0) + 1
                set_user_data(0, user_id, "dsize_statistics", statistics)
                new_size = random.randint(1, get_server_config(guild_key, "dsize_surgery_max", 10))
                will_fail = percent_random(fail_chance)
                on_fail_size = random.randint(1, new_size) if will_fail else 0
                # print(f"[DSize] {interaction.user} surgery: +{new_size} cm, fail chance: {fail_chance}%, will_fail: {will_fail}, on_fail_size: {on_fail_size}")
                log(f"{interaction.user} 手術: +{new_size} cm, 失敗機率: {fail_chance}%, 是否失敗: {will_fail}", module_name="dsize", user=interaction.user, guild=interaction.guild)
                embed = discord.Embed(title=f"{interaction.user.display_name} 的新長度：", color=0xff0000)
                embed.add_field(name=f"{size} cm", value=f"8{d_string}D", inline=False)
                await interaction.response.edit_message(embed=embed, view=None)
                # animate to new size
                for i in range(1, new_size + 1):
                    if will_fail and i == on_fail_size:
                        d_string_new = "?" * (size + i - 1)
                        embed = discord.Embed(title=f"{interaction.user.display_name} 的新長度：", color=0xff0000)
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
                            ori -= min(random.randint(2, 5), ori)
                        embed.set_field_at(0, name=f"-1 cm", value=f"8", inline=False)
                        await interaction.edit_original_response(content="手術失敗，你變男娘了。", embed=embed)
                        set_user_data(guild_key, user_id, "last_dsize_size", -1)
                        statistics["failed_surgeries"] = statistics.get("failed_surgeries", 0) + 1
                        statistics["mangirl_count"] = statistics.get("mangirl_count", 0) + 1
                        set_user_data(0, user_id, "dsize_statistics", statistics)
                        
                        # Save to history
                        history = get_user_data(guild_key, user_id, "dsize_history", [])
                        history.append({
                            "date": now.isoformat(),
                            "size": -1,
                            "type": "手術失敗"
                        })
                        if len(history) > 100:
                            history = history[-100:]
                        set_user_data(guild_key, user_id, "dsize_history", history)
                        return
                    d_string_new = "=" * (size + i - 2)
                    current_size = size + i
                    embed = discord.Embed(title=f"{interaction.user.display_name} 的新長度：", color=0xff0000)
                    embed.add_field(name=f"{current_size} cm", value=f"8{d_string_new}D", inline=False)
                    await interaction.edit_original_response(content="正在手術中...", embed=embed)
                    await asyncio.sleep(1)
                embed = discord.Embed(title=f"{interaction.user.display_name} 的新長度：", color=0x00ff00)
                embed.add_field(name=f"{size + new_size} cm", value=f"8{'=' * (size + new_size - 2)}D", inline=False)
                await interaction.edit_original_response(content="手術成功。", embed=embed)
                set_user_data(guild_key, user_id, "last_dsize_size", new_size + size)
                # update user statistics
                statistics["successful_surgeries"] = statistics.get("successful_surgeries", 0) + 1
                set_user_data(0, user_id, "dsize_statistics", statistics)
                
                # Save to history
                history = get_user_data(guild_key, user_id, "dsize_history", [])
                history.append({
                    "date": now.isoformat(),
                    "size": new_size + size,
                    "type": "手術成功"
                })
                if len(history) > 100:
                    history = history[-100:]
                set_user_data(guild_key, user_id, "dsize_history", history)
        surgery_msg = await interaction.followup.send(f"{interaction.user.mention}\n你獲得了一次做手術的機會。\n請問你是否同意手術？\n-# 失敗機率：{fail_chance}%", view=dsize_SurgeryView())
    if not global_dsize:
        if ItemSystem and percent_random(drop_item_chance):
            # print(f"[DSize] {interaction.user} got item drop chance")
            log("獲得了物品掉落機會", module_name="dsize", user=interaction.user, guild=interaction.guild)
            statistics = get_user_data(0, user_id, "dsize_statistics", {})
            statistics["total_drops"] = statistics.get("total_drops", 0) + 1
            set_user_data(0, user_id, "dsize_statistics", statistics)
            msg = await interaction.followup.send("...?")
            await asyncio.sleep(1)
            await msg.edit(content="......?")
            await asyncio.sleep(1)
            await msg.edit(content=".........?")
            await asyncio.sleep(1)
            rand = random.randint(1, 100)
            if rand <= 30:
                await ItemSystem.give_item_to_user(interaction.guild.id, interaction.user.id, "fake_ruler", 1)
                item_use_command = await get_command_mention("item", "use")
                await msg.edit(content=f"{interaction.user.mention}\n你撿到了一把自欺欺人尺！\n使用 {item_use_command} 自欺欺人尺 可能可以讓下次量長度時變長？")
            elif rand > 30 and rand <= 70:
                amount = random.randint(1, 10)
                await ItemSystem.give_item_to_user(interaction.guild.id, interaction.user.id, "grass", amount)
                grass_command = await get_command_mention("dsize-feedgrass")
                await msg.edit(content=f"{interaction.user.mention}\n你撿到了草 x{amount}！\n使用 {grass_command} 可以草飼男娘。")
            elif rand > 70 and rand <= 97:
                # give anti surgery item
                await ItemSystem.give_item_to_user(interaction.guild.id, interaction.user.id, "anti_surgery", 1)
                item_use_command = await get_command_mention("item", "use")
                await msg.edit(content=f"{interaction.user.mention}\n你撿到了一顆抗手術藥物！\n使用 {item_use_command} 抗手術藥物 可以防止一天被手術。")
            else:
                if rand == 98:
                    await ItemSystem.give_item_to_user(interaction.guild.id, interaction.user.id, "cloud_ruler", 1)
                    item_use_command = await get_command_mention("item", "use")
                    await msg.edit(content=f"{interaction.user.mention}\n你撿到了一把雲端尺！\n使用 {item_use_command} 雲端尺 可以進行手術。")
                elif rand == 99:
                    await ItemSystem.give_item_to_user(interaction.guild.id, interaction.user.id, "scalpel", 1)
                    item_use_command = await get_command_mention("item", "use")
                    await msg.edit(content=f"{interaction.user.mention}\n你撿到了一把手術刀！\n使用 {item_use_command} 手術刀 可以進行手術。")
                else:
                    await ItemSystem.give_item_to_user(interaction.guild.id, interaction.user.id, "rusty_scalpel", 1)
                    item_use_command = await get_command_mention("item", "use")
                    await msg.edit(content=f"{interaction.user.mention}\n你撿到了一把生鏽的手術刀！\n使用 {item_use_command} 生鏽的手術刀 可以進行手術。")

@bot.tree.command(name=app_commands.locale_str("dsize-leaderboard"), description="查看屌長排行榜")
@app_commands.describe(limit="顯示前幾名 (預設10)", global_leaderboard="顯示全域排行榜 (預設否)", reverse="反轉排行榜 (預設否)")
@app_commands.choices(
    global_leaderboard=[
        app_commands.Choice(name="否", value=0),
        app_commands.Choice(name="是", value=1),
    ],
    reverse=[
        app_commands.Choice(name="否", value=0),
        app_commands.Choice(name="是", value=1),
    ]
)
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def dsize_leaderboard(interaction: discord.Interaction, limit: int = 10, global_leaderboard: int = 0, reverse: int = 0):
    global_leaderboard = bool(global_leaderboard)
    reverse = bool(reverse)
    if global_leaderboard:
        guild_id = None  # global
    else:
        if not interaction.is_guild_integration():
            global_leaderboard = True
            guild_id = None
        else:
            global_leaderboard = False if interaction.guild else True
            guild_id = interaction.guild.id if interaction.guild else None  # None for global
    leaderboard = []
    if limit < 1 or limit > 50:
        await interaction.response.send_message("限制必須在 1 到 50 之間。", ephemeral=True)
        return
    await interaction.response.defer()

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
    
    for user_id, data in all_data_fake.copy().items():
        size = data.get("last_dsize_fake_size")
        # check dsize date is today
        user_date = get_user_data(guild_id, user_id, "dsize_fake_ruler_used_date")
        if user_date is None:
            all_data_fake.pop(user_id)
            continue
        if user_date is not None and not isinstance(user_date, datetime):
            # If user_date is a string (e.g., from JSON), convert to date
            try:
                user_date = datetime.fromisoformat(str(user_date)).date()
            except Exception:
                user_date = datetime(1970, 1, 1).date()
        elif isinstance(user_date, datetime):
            user_date = user_date.date()
        if user_date is not None and user_date != (datetime.utcnow() + timedelta(hours=8)).date():
            all_data_fake.pop(user_id)
            continue

    if not leaderboard:
        await interaction.followup.send("今天還沒有任何人量過屌長。")
        return

    # 按照大小排序並取前limit名
    leaderboard.sort(key=lambda x: x[1], reverse=not reverse)
    top_users = leaderboard[:limit]

    # 建立排行榜訊息
    description = ""
    for rank, (user_id, size) in enumerate(top_users, start=1):
        if size == -1:
            size = "**男娘！**"
        else:
            if all_data_fake.get(user_id) and all_data_fake[user_id].get("last_dsize_fake_size") is not None:
                size = f"{all_data_fake[user_id].get('last_dsize_fake_size')} cm..?"
            else:
                size = f"{size} cm"
        if global_leaderboard:
            user = await bot.fetch_user(user_id)
        else:
            user = interaction.guild.get_member(user_id) if interaction.guild else await bot.fetch_user(user_id)
        if user:
            description += f"**{rank}. {user.display_name}**({user.name}) - {size}\n"
        else:
            description += f"**{rank}. 用戶ID {user_id}** - {size}\n"

    embed = discord.Embed(title="今天的長度排行榜", description=description, color=0x00ff00)
    # server info
    if interaction.guild and not global_leaderboard:
        embed.set_footer(text=interaction.guild.name, icon_url=interaction.guild.icon.url if interaction.guild.icon else None)
    else:
        embed.set_footer(text="全域排行榜")
    await interaction.followup.send(embed=embed)


user_using_dsize_battle = set()  # to prevent spamming the command
@bot.tree.command(name=app_commands.locale_str("dsize-battle"), description="比屌長(需要雙方今天沒有量過)")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(opponent="要比屌長的對象")
async def dsize_battle(interaction: discord.Interaction, opponent: discord.User):
    original_user = interaction.user
    user_id = interaction.user.id
    opponent_id = opponent.id
    now = (datetime.utcnow() + timedelta(hours=8)).date()  # 台灣時間

    if user_id == opponent_id:
        await interaction.response.send_message("不能跟自己比屌長。", ephemeral=True)
        return
    
    guild_key = interaction.guild.id if interaction.guild else None
    if not interaction.is_guild_integration():
        guild_key = None
        # global_dsize = True
    else:
        # convert opponent to guild member if possible
        if interaction.guild:
            opponent = interaction.guild.get_member(opponent_id) or opponent
    max_size = get_server_config(guild_key, "dsize_max", 30)

    last_user = get_user_data(guild_key, user_id, "last_dsize")
    last_opponent = get_user_data(guild_key, opponent_id, "last_dsize")

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
        await interaction.response.send_message(f"{opponent.display_name} 今天已經量過屌長了。", ephemeral=True)
        return
    
    if user_id in user_using_dsize_battle:
        await interaction.response.send_message("你已經在進行一場對決了，請先結束目前的對決。", ephemeral=True)
        return
    if opponent_id in user_using_dsize_battle:
        await interaction.response.send_message(f"{opponent.display_name} 正在進行一場對決，請稍後再試。", ephemeral=True)
        return
    
    # print(f"[DSize] {interaction.user} is challenging {opponent} to a dsize battle in guild {interaction.guild.id}")
    log(f"{interaction.user} 正在對 {opponent} 進行屌長對決，伺服器: {interaction.guild.id if guild_key else '全域'}", module_name="dsize", user=interaction.user, guild=interaction.guild)
    
    user_using_dsize_battle.add(user_id)
    user_using_dsize_battle.add(opponent_id)
    
    class dsize_Confirm(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=30)
            self.value = None
        
        async def on_timeout(self):
            user_using_dsize_battle.discard(user_id)
            user_using_dsize_battle.discard(opponent_id)
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
            user_statistics = get_user_data(0, user_id, "dsize_statistics", {})
            user_statistics["total_battles"] = user_statistics.get("total_battles", 0) + 1
            user_statistics["total_uses"] = user_statistics.get("total_uses", 0) + 1
            set_user_data(0, user_id, "dsize_statistics", user_statistics)
            opponent_statistics = get_user_data(0, opponent_id, "dsize_statistics", {})
            opponent_statistics["total_battles"] = opponent_statistics.get("total_battles", 0) + 1
            opponent_statistics["total_uses"] = opponent_statistics.get("total_uses", 0) + 1
            set_user_data(0, opponent_id, "dsize_statistics", opponent_statistics)

            set_user_data(guild_key, user_id, "last_dsize", now)
            set_user_data(guild_key, opponent_id, "last_dsize", now)

            await interaction.response.edit_message(content="開始對決。", view=None)
            size_user = random.randint(1, max_size)
            size_opponent = random.randint(1, max_size)
            # print(f"[DSize] {interaction.user} vs {opponent} - {size_user} cm vs {size_opponent} cm")
            log(f"{original_user} vs {opponent} - {size_user} cm vs {size_opponent} cm", module_name="dsize", user=interaction.user, guild=interaction.guild)
            speed = max(size_user, size_opponent) // 50 + 1

            # 取得訊息物件
            msg = await interaction.original_response()
            t = datetime.now(timezone.utc)

            for i in range(1, max(size_user, size_opponent) - 1, speed):
                d_string_user = "=" * min(i, size_user - 1)
                d_string_opponent = "=" * min(i, size_opponent - 1)
                embed = discord.Embed(title="比長度", color=0x00ff00)
                embed.add_field(
                    name=f"{original_user.display_name} 的長度：",
                    value=f"{size_user if i >= size_user - 1 else '??'} cm\n8{d_string_user}D",
                    inline=False,
                )
                embed.add_field(
                    name=f"{opponent.display_name} 的長度：",
                    value=f"{size_opponent if i >= size_opponent - 1 else '??'} cm\n8{d_string_opponent}D",
                    inline=False,
                )
                embed.timestamp = t
                if not guild_key:
                    embed.set_footer(text="此次對決將記錄到全域排行榜。")
                await msg.edit(embed=embed)
                await asyncio.sleep(0.1)

            # 最終結果
            if size_user > size_opponent:
                user_statistics["wins"] = user_statistics.get("wins", 0) + 1
                set_user_data(0, user_id, "dsize_statistics", user_statistics)
                opponent_statistics["losses"] = opponent_statistics.get("losses", 0) + 1
                set_user_data(0, opponent_id, "dsize_statistics", opponent_statistics)
                result = f"🎉 {original_user.display_name} 勝利！"
            elif size_user < size_opponent:
                opponent_statistics["wins"] = opponent_statistics.get("wins", 0) + 1
                set_user_data(0, opponent_id, "dsize_statistics", opponent_statistics)
                user_statistics["losses"] = user_statistics.get("losses", 0) + 1
                set_user_data(0, user_id, "dsize_statistics", user_statistics)
                result = f"🎉 {opponent.display_name} 勝利！"
            else:
                result = "🤝 平手！"

            d_string_user = "=" * (size_user - 1)
            d_string_opponent = "=" * (size_opponent - 1)
            embed = discord.Embed(title="比長度", color=0x00ff00)
            embed.add_field(name=f"{original_user.display_name} 的長度：", value=f"{size_user} cm\n8{d_string_user}D", inline=False)
            embed.add_field(name=f"{opponent.display_name} 的長度：", value=f"{size_opponent} cm\n8{d_string_opponent}D", inline=False)
            embed.add_field(name="結果：", value=result, inline=False)
            embed.timestamp = t
            
            # Process daily check-in for both users (always global)
            user_is_new_checkin, user_checkin_streak, user_broke_streak, user_broke_streak_on = await process_checkin(user_id)
            opponent_is_new_checkin, opponent_checkin_streak, opponent_broke_streak, opponent_broke_streak_on = await process_checkin(opponent_id)
            
            # Set footer with check-in info
            footer_parts = []
            if user_is_new_checkin:
                if user_broke_streak:
                    footer_parts.append(f"{original_user.display_name} 在第 {user_broke_streak_on} 天打破了簽到紀錄，重新開始！")
                else:
                    footer_parts.append(f"{original_user.display_name} 簽到第 {user_checkin_streak} 天！")
            if opponent_is_new_checkin:
                if opponent_broke_streak:
                    footer_parts.append(f"{opponent.display_name} 在第 {opponent_broke_streak_on} 天打破了簽到紀錄，重新開始！")
                else:
                    footer_parts.append(f"{opponent.display_name} 簽到第 {opponent_checkin_streak} 天！")
            
            if not guild_key:
                footer_parts.append("此次對決將記錄到全域排行榜。")
            
            if footer_parts:
                embed.set_footer(text=" | ".join(footer_parts))
            
            await msg.edit(embed=embed)

            set_user_data(guild_key, user_id, "last_dsize_size", size_user)
            set_user_data(guild_key, opponent_id, "last_dsize_size", size_opponent)
            
            # Handle check-in rewards if applicable (milestone days only)
            if user_is_new_checkin:
                # Create a temporary interaction-like object for user rewards
                # We'll send it as a followup message
                await handle_checkin_rewards(interaction, user_id, user_checkin_streak, guild_key)
                log(f"{original_user.display_name} 簽到成功，連續 {user_checkin_streak} 天", module_name="dsize", user=original_user, guild=interaction.guild)
            
            if opponent_is_new_checkin:
                # For opponent, we need to note this but can't show interactive buttons
                # since they're not the one who triggered the interaction
                await handle_checkin_rewards(interaction, opponent_id, opponent_checkin_streak, guild_key)
                log(f"{opponent.display_name} 簽到成功，連續 {opponent_checkin_streak} 天", module_name="dsize", user=opponent, guild=interaction.guild)
            
            # Save to history for both users
            user_history = get_user_data(guild_key, user_id, "dsize_history", [])
            user_history.append({
                "date": now.isoformat(),
                "size": size_user,
                "type": "對決"
            })
            if len(user_history) > 100:
                user_history = user_history[-100:]
            set_user_data(guild_key, user_id, "dsize_history", user_history)
            
            opponent_history = get_user_data(guild_key, opponent_id, "dsize_history", [])
            opponent_history.append({
                "date": now.isoformat(),
                "size": size_opponent,
                "type": "對決"
            })
            if len(opponent_history) > 100:
                opponent_history = opponent_history[-100:]
            set_user_data(guild_key, opponent_id, "dsize_history", opponent_history)
            
            user_using_dsize_battle.discard(user_id)
            user_using_dsize_battle.discard(opponent_id)

        @discord.ui.button(label="❌ 拒絕", style=discord.ButtonStyle.danger)
        async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user.id != opponent_id:
                await interaction.response.send_message("這不是你的對決邀請。", ephemeral=True)
                return
            self.value = False
            self.stop()
            await interaction.response.edit_message(content="已拒絕對決邀請。", view=None)
            user_using_dsize_battle.discard(user_id)
            user_using_dsize_battle.discard(opponent_id)
            # print(f"[DSize] {interaction.user} canceled the dsize battle")
            log(f"{interaction.user} 取消了屌長對決", module_name="dsize", user=interaction.user, guild=interaction.guild)

    # 徵求對方同意
    await interaction.response.send_message(f"{opponent.mention}，{interaction.user.name} 想跟你比長度。\n請在 30 秒內按下 ✅ 同意 或 ❌ 拒絕。", ephemeral=False, view=dsize_Confirm())


# server settings command
@bot.tree.command(name=app_commands.locale_str("dsize-settings"), description="設定dsize")
@app_commands.describe(setting="要設定的項目", value="設定的值")
@app_commands.choices(setting=[
    app_commands.Choice(name="最大長度", value="dsize_max"),
    app_commands.Choice(name="手術機率(%)", value="dsize_surgery_percent"),
    app_commands.Choice(name="手術最大長度", value="dsize_surgery_max"),
    app_commands.Choice(name="撿到物品機率(%)", value="dsize_drop_item_chance"),
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
        await interaction.response.send_message(f"已設定最大長度為 {value} cm。")
    elif setting == "dsize_surgery_percent":
        if not value.isdigit() or int(value) < 1 or int(value) > 100:
            await interaction.response.send_message("手術機率必須是介於 1 到 100 之間的整數。", ephemeral=True)
            return
        set_server_config(guild_key, "dsize_surgery_percent", int(value))
        await interaction.response.send_message(f"已設定手術機率為 {str(int(value))}%。")
    elif setting == "dsize_surgery_max":
        # limit 100
        if not value.isdigit() or int(value) < 1 or int(value) > 100:
            await interaction.response.send_message("手術最大長度必須是介於 1 到 100 之間的整數。", ephemeral=True)
            return
        set_server_config(guild_key, "dsize_surgery_max", int(value))
        await interaction.response.send_message(f"已設定手術最大長度為 {value} cm。")
    elif setting == "dsize_drop_item_chance":
        if not value.isdigit() or int(value) < 0 or int(value) > 100:
            await interaction.response.send_message("撿到物品機率必須是介於 0 到 100 之間的整數。", ephemeral=True)
            return
        set_server_config(guild_key, "dsize_drop_item_chance", int(value))
        await interaction.response.send_message(f"已設定撿到物品機率為 {str(int(value))}%。")
    else:
        await interaction.response.send_message("未知的設定項目。")
    log(f"Set {setting} to {value} in guild {guild_key}", module_name="dsize", user=interaction.user, guild=interaction.guild)


@bot.tree.command(name=app_commands.locale_str("dsize-stats"), description="查看你的屌長統計資料")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def dsize_stats(interaction: discord.Interaction):
    # print(f"[DSize] {interaction.user} is viewing dsize stats")
    log(f"{interaction.user} is viewing dsize stats", module_name="dsize", user=interaction.user, guild=interaction.guild)
    user_id = interaction.user.id
    statistics = get_user_data(0, user_id, "dsize_statistics", {})
    total_uses = statistics.get("total_uses", 0)
    total_battles = statistics.get("total_battles", 0)
    wins = statistics.get("wins", 0)
    losses = statistics.get("losses", 0)
    total_surgeries = statistics.get("total_surgeries", 0)
    successful_surgeries = statistics.get("successful_surgeries", 0)
    failed_surgeries = statistics.get("failed_surgeries", 0)
    mangirl_count = statistics.get("mangirl_count", 0)
    total_feedgrass = statistics.get("total_feedgrass", 0)
    total_been_feedgrass = statistics.get("total_been_feedgrass", 0)
    total_drops = statistics.get("total_drops", 0)
    total_checkins = statistics.get("total_checkins", 0)
    checkin_streak = statistics.get("checkin_streak", 0)

    embed = discord.Embed(title=f"{interaction.user.display_name} 的 dsize 統計資料", color=0x00ff00)
    embed.add_field(name="量屌次數", value=str(total_uses), inline=False)
    embed.add_field(name="對決次數", value=str(total_battles), inline=False)
    embed.add_field(name="勝利次數", value=str(wins), inline=True)
    embed.add_field(name="失敗次數", value=str(losses), inline=True)
    embed.add_field(name="手術次數", value=str(total_surgeries), inline=False)
    embed.add_field(name="成功手術次數", value=str(successful_surgeries), inline=True)
    # embed.add_field(name="失敗手術次數", value=str(failed_surgeries), inline=True)
    embed.add_field(name="變成男娘次數", value=str(mangirl_count), inline=False)
    embed.add_field(name="草飼次數", value=str(total_feedgrass), inline=True)
    embed.add_field(name="被草飼次數", value=str(total_been_feedgrass), inline=True)
    embed.add_field(name="撊到物品次數", value=str(total_drops), inline=False)
    embed.add_field(name="簽到次數", value=str(total_checkins), inline=True)
    embed.add_field(name="連續簽到天數", value=str(checkin_streak), inline=True)
    embed.timestamp = datetime.now(timezone.utc)

    await interaction.response.send_message(embed=embed)


@bot.tree.command(name=app_commands.locale_str("dsize-history"), description="查看歷史紀錄")
@app_commands.describe(
    user="查看指定用戶的歷史紀錄 (預設為自己)",
    global_history="是否顯示全域紀錄 (預設否)"
)
@app_commands.choices(global_history=[
    app_commands.Choice(name="否", value=0),
    app_commands.Choice(name="是", value=1),
])
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def dsize_history(interaction: discord.Interaction, user: discord.User = None, global_history: int = 0):
    global_history = bool(global_history)
    target_user = user if user else interaction.user
    user_id = target_user.id
    
    # Determine guild_key
    if global_history:
        guild_key = None
    else:
        if not interaction.is_guild_integration():
            global_history = True
            guild_key = None
        else:
            guild_key = interaction.guild.id if interaction.guild else None
    
    await interaction.response.defer()
    
    # Get history from user data
    history = get_user_data(guild_key, user_id, "dsize_history", [])
    
    if not history:
        scope = "全域" if global_history else "此伺服器"
        name = target_user.display_name if user else "你"
        await interaction.followup.send(f"{name}在{scope}還沒有任何紀錄。")
        return
    
    # Sort history by date (most recent first)
    history_sorted = sorted(history, key=lambda x: x.get("date", ""), reverse=True)
    
    # Take only the last 10 records
    history_display = history_sorted[:10]
    
    # Build embed
    embed = discord.Embed(
        title=f"{target_user.display_name} 的歷史紀錄",
        description=f"顯示最近 {len(history_display)} 筆紀錄",
        color=0x00ff00
    )
    
    for record in history_display:
        date_str = record.get("date", "未知日期")
        size = record.get("size", 0)
        record_type = record.get("type", "測量")
        
        # Parse date for display
        try:
            date_obj = datetime.fromisoformat(date_str).date()
            date_display = date_obj.strftime("%Y-%m-%d")
        except:
            date_display = date_str
        
        if size == -1:
            size_display = "**男娘！**"
        else:
            size_display = f"{size} cm"
        
        field_value = f"{size_display} ({record_type})"
        embed.add_field(name=date_display, value=field_value, inline=True)
    
    if not global_history and interaction.guild:
        embed.set_footer(text=f"伺服器：{interaction.guild.name}")
    else:
        embed.set_footer(text="全域紀錄")
    
    embed.timestamp = datetime.now(timezone.utc)
    
    await interaction.followup.send(embed=embed)
    log(f"查看了 {target_user.display_name} 的歷史紀錄", module_name="dsize", user=interaction.user, guild=interaction.guild)


@bot.tree.command(name=app_commands.locale_str("dsize-feedgrass"), description="草飼男娘")
@app_commands.describe(user="要草飼的對象")
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
async def dsize_feedgrass(interaction: discord.Interaction, user: discord.Member = None):
    if "ItemSystem" not in modules:
        await interaction.response.send_message("此功能需要 ItemSystem 模組。", ephemeral=True)
        return
    if not user:
        user = interaction.user  # self-feedgrass
    # if user.id == interaction.user.id:
        # await interaction.response.send_message("不能草飼自己。", ephemeral=True)
        # return
    if get_user_data(interaction.guild.id, user.id, "last_dsize_size", 0) != -1:
        name = user.display_name + " " if user.id != interaction.user.id else "你"
        await interaction.response.send_message(f"{name}不是男娘，無法草飼。", ephemeral=True)
        return
    removed = await ItemSystem.remove_item_from_user(interaction.guild.id, interaction.user.id, "grass", 1)
    if not removed:
        await interaction.response.send_message("你沒有草，無法草飼。", ephemeral=True)
        return
    await interaction.response.defer()
    # update user statistics
    statistics = get_user_data(0, interaction.user.id, "dsize_statistics", {})
    statistics["total_feedgrass"] = statistics.get("total_feedgrass", 0) + 1
    set_user_data(0, interaction.user.id, "dsize_statistics", statistics)
    target_user_statistics = get_user_data(0, user.id, "dsize_statistics", {})
    target_user_statistics["total_been_feedgrass"] = target_user_statistics.get("total_been_feedgrass", 0) + 1
    set_user_data(0, user.id, "dsize_statistics", target_user_statistics)
    # get random users from last 25 messages
    random_users = set()
    async for msg in interaction.channel.history(limit=25):
        if msg.author.id != interaction.user.id and msg.author.id != user.id:
            random_users.add(msg.author)
    random_users = list(random_users)
    image_bytes = await generate_feedgrass_image(user, interaction.user, random_users)
    if interaction.user.id != user.id:
        embed = discord.Embed(title=f"{interaction.user.display_name} 草飼了 {user.display_name}！", color=0x00ff00)
    else:
        embed = discord.Embed(title=f"{interaction.user.display_name} 草飼了自己！", color=0x00ff00)
    embed.set_image(url="attachment://feed_grass.png")
    embed.timestamp = datetime.now(timezone.utc)
    await interaction.followup.send(embed=embed, file=discord.File(image_bytes, "feed_grass.png"))
    # print(f"[DSize] {interaction.user} fed grass to {user} in guild {interaction.guild.id}")
    log(f"草飼了 {user}", module_name="dsize", user=interaction.user, guild=interaction.guild)


# from folder
feedgrass_folder = "dsize-feedgrass-images"
# scan the folder json files
feedgrass_images = []
self_feedgrass_images = []

def load_feedgrass_images():
    global feedgrass_images
    global self_feedgrass_images
    feedgrass_images = []
    self_feedgrass_images = []
    loaded = 0
    for filename in os.listdir(feedgrass_folder):
        if filename.endswith(".json"):
            with open(os.path.join(feedgrass_folder, filename), "r", encoding="utf-8") as f:
                data = json.load(f)
                image_path = os.path.join(feedgrass_folder, data["file"])
                if not os.path.isfile(image_path):
                    continue
                data["file"] = image_path
                if data.get("self", False):
                    self_feedgrass_images.append(data)
                    loaded += 1
                else:
                    feedgrass_images.append(data)
                    loaded += 1
    log(f"載入了 {loaded} 張草飼圖片。", module_name="dsize")
    return loaded
load_feedgrass_images()

@bot.command(aliases=["reloadfeedgrassimages", "refgi", "rfg"])
@OwnerTools.is_owner()
async def reload_feedgrass_images(ctx: commands.Context):
    loaded = load_feedgrass_images()
    await ctx.reply(f"已重新載入 {loaded} 張草飼圖片！")


async def generate_feedgrass_image(target: discord.User, feeder: discord.User, random_users: list[discord.User] = [], nsfw: bool = False) -> BytesIO:
    if feeder.id != target.id:
        # check nsfw channel
        if nsfw:
            new_feedgrass_images = feedgrass_images.copy()
        else:
            new_feedgrass_images = [img for img in feedgrass_images if not img.get("nsfw", False)]
        img = random.choice(new_feedgrass_images)
    else:
        # check nsfw channel
        if nsfw:
            new_self_feedgrass_images = self_feedgrass_images.copy()
        else:
            new_self_feedgrass_images = [img for img in self_feedgrass_images if not img.get("nsfw", False)]
        img = random.choice(new_self_feedgrass_images)
    image = Image.open(img["file"]).convert("RGBA")
    # width, height = image.size
    # fetch avatars
    target_avatar_asset = target.display_avatar.with_size(128).with_static_format('png')
    # to circle
    target_avatar_bytes = await target_avatar_asset.read()
    target_avatar = Image.open(BytesIO(target_avatar_bytes)).convert("RGBA").resize(img["target"]["size"])
    mask_target = Image.new("L", img["target"]["size"], 0)
    draw_target = ImageDraw.Draw(mask_target)
    draw_target.ellipse((0, 0, img["target"]["size"][0], img["target"]["size"][1]), fill=255)
    target_avatar.putalpha(mask_target)
    image.paste(target_avatar, img["target"]["position"], target_avatar)
    if feeder.id != target.id:
        feeder_avatar_asset = feeder.display_avatar.with_size(128).with_static_format('png')
        feeder_avatar_bytes = await feeder_avatar_asset.read()
        feeder_avatar = Image.open(BytesIO(feeder_avatar_bytes)).convert("RGBA").resize(img["feeder"]["size"])
        mask_feeder = Image.new("L", img["feeder"]["size"], 0)
        draw_feeder = ImageDraw.Draw(mask_feeder)
        draw_feeder.ellipse((0, 0, img["feeder"]["size"][0], img["feeder"]["size"][1]), fill=255)
        feeder_avatar.putalpha(mask_feeder)
        image.paste(feeder_avatar, img["feeder"]["position"], feeder_avatar)
    # print(random_users)
    if img.get("extras"):
        for extra in img["extras"]:
            if not random_users:
                break
            extra_user = random.choice(random_users)
            random_users.remove(extra_user)
            extra_avatar_asset = extra_user.display_avatar.with_size(128).with_static_format('png')
            extra_avatar_bytes = await extra_avatar_asset.read()
            extra_avatar = Image.open(BytesIO(extra_avatar_bytes)).convert("RGBA").resize(extra["size"])
            mask_extra = Image.new("L", extra["size"], 0)
            draw_extra = ImageDraw.Draw(mask_extra)
            draw_extra.ellipse((0, 0, extra["size"][0], extra["size"][1]), fill=255)
            extra_avatar.putalpha(mask_extra)
            image.paste(extra_avatar, extra["position"], extra_avatar)
    # save to bytes
    byte_io = BytesIO()
    image.save(byte_io, 'PNG')
    byte_io.seek(0)
    return byte_io


# setup items
async def use_fake_ruler(interaction: discord.Interaction):
    user_id = interaction.user.id
    guild_key = interaction.guild.id if interaction.guild else None
    if get_user_data(guild_key, user_id, "dsize_fake_ruler_used", "False") == "True":
        await interaction.response.send_message("你今天已經使用過自欺欺人尺了。", ephemeral=True)
        return
    await ItemSystem.remove_item_from_user(guild_key, interaction.user.id, "fake_ruler", 1)
    set_user_data(guild_key, user_id, "dsize_fake_ruler_used", True)
    await interaction.response.send_message("你使用了自欺欺人尺！\n下次量長度時或許會更長？")
    # print(f"[DSize] {interaction.user} used fake ruler in guild {guild_key}")
    log(f"{interaction.user} 使用了自欺欺人尺", module_name="dsize", user=interaction.user, guild=interaction.guild)

async def use_scalpel(interaction: discord.Interaction):
    user_id = interaction.user.id
    guild_key = interaction.guild.id if interaction.guild else None
    
    class SelectUserModal(discord.ui.Modal, title="要幫誰手術？"):
        target_user = discord.ui.Label(text="選擇用戶", component=discord.ui.UserSelect(placeholder="選擇一個用戶", min_values=1, max_values=1))

        async def on_submit(self, interaction: discord.Interaction):
            target_user = self.target_user.component.values[0]
            target_id = target_user.id
            target_id = int(target_id)
            now = (datetime.utcnow() + timedelta(hours=8)).date()
            last = get_user_data(guild_key, target_id, "last_dsize")
            if last is not None and not isinstance(last, datetime):
                # If last is a string (e.g., from JSON), convert to date
                try:
                    last = datetime.fromisoformat(str(last)).date()
                except Exception:
                    last = datetime(1970, 1, 1).date()
            elif isinstance(last, datetime):
                last = last.date()
            if last is None:
                last = datetime(1970, 1, 1).date()
            if not now == last:
                await interaction.response.send_message(f"{target_user.display_name} 今天還沒有量過屌長，無法進行手術。", ephemeral=True)
                return
            if get_user_data(guild_key, target_id, "last_dsize_size", 0) == -1:
                await interaction.response.send_message(f"{target_user.display_name} 是男娘，無法進行手術。", ephemeral=True)
                return
            if get_user_data(guild_key, target_id, "dsize_anti_surgery") == str(now):
                await interaction.response.send_message(f"{target_user.display_name} 使用了抗手術藥物，無法進行手術。", ephemeral=True)
                return
            removed = await ItemSystem.remove_item_from_user(guild_key, user_id, "scalpel", 1)
            if not removed:
                await interaction.response.send_message("你沒有手術刀，無法進行手術。", ephemeral=True)
                return
            # update user statistics
            statistics = get_user_data(0, target_id, "dsize_statistics", {})
            statistics["total_surgeries"] = statistics.get("total_surgeries", 0) + 1
            statistics["successful_surgeries"] = statistics.get("successful_surgeries", 0) + 1
            set_user_data(0, target_id, "dsize_statistics", statistics)
            performer_statistics = get_user_data(0, user_id, "dsize_statistics", {})
            performer_statistics["total_performed_surgeries"] = performer_statistics.get("total_performed_surgeries", 0) + 1
            set_user_data(0, user_id, "dsize_statistics", performer_statistics)

            new_size = random.randint(1, get_server_config(guild_key, "dsize_surgery_max", 10))
            orig_size = get_user_data(guild_key, target_id, "last_dsize_size", 0)
            set_user_data(guild_key, target_id, "last_dsize_size", orig_size + new_size)
            # print(f"[DSize] {interaction.user} performed surgery on {target_user.display_name}, original size: {orig_size} cm, new size: {orig_size + new_size} cm")
            log(f"{interaction.user} performed surgery on {target_user.display_name}, original size: {orig_size} cm, new size: {orig_size + new_size} cm", module_name="dsize", user=interaction.user, guild=interaction.guild)
            target_name = "自己" if target_id == user_id else " " + target_user.display_name + " "
            embed = discord.Embed(title=f"{interaction.user.display_name} 幫{target_name}動手術！", color=0xff0000)
            embed.add_field(name=f"{orig_size} cm", value=f"8{'=' * (orig_size - 1)}D", inline=False)
            await interaction.response.send_message(content=f"{target_user.mention} 被抓去動手術。", embed=embed)
            for i in range(1, new_size + 1):
                d_string_new = "=" * (orig_size + i - 1)
                embed.set_field_at(0, name=f"{orig_size} cm", value=f"8{d_string_new}D", inline=False)
                await interaction.edit_original_response(embed=embed)
                await asyncio.sleep(1)
                orig_size += 1
            embed.set_field_at(0, name=f"{orig_size + new_size} cm", value=f"8{'=' * (orig_size + new_size - 1)}D", inline=False)
            embed.color = 0x00ff00
            await interaction.edit_original_response(content=f"{target_user.mention} 手術成功。", embed=embed)
            
            # Save to history
            history = get_user_data(guild_key, target_id, "dsize_history", [])
            history.append({
                "date": now.isoformat(),
                "size": orig_size + new_size,
                "type": "手術成功"
            })
            if len(history) > 100:
                history = history[-100:]
            set_user_data(guild_key, target_id, "dsize_history", history)
    await interaction.response.send_modal(SelectUserModal())

async def use_rusty_scalpel(interaction: discord.Interaction):
    user_id = interaction.user.id
    guild_key = interaction.guild.id if interaction.guild else None
    
    class SelectUserModal(discord.ui.Modal, title="要幫誰手術？"):
        target_user = discord.ui.Label(text="選擇用戶", component=discord.ui.UserSelect(placeholder="選擇一個用戶", min_values=1, max_values=1))

        async def on_submit(self, interaction: discord.Interaction):
            target_user = self.target_user.component.values[0]
            target_id = target_user.id
            target_id = int(target_id)
            now = (datetime.utcnow() + timedelta(hours=8)).date()
            last = get_user_data(guild_key, target_id, "last_dsize")
            if last is not None and not isinstance(last, datetime):
                # If last is a string (e.g., from JSON), convert to date
                try:
                    last = datetime.fromisoformat(str(last)).date()
                except Exception:
                    last = datetime(1970, 1, 1).date()
            elif isinstance(last, datetime):
                last = last.date()
            if last is None:
                last = datetime(1970, 1, 1).date()
            if not now == last:
                await interaction.response.send_message(f"{target_user.display_name} 今天還沒有量過屌長，無法進行手術。", ephemeral=True)
                return
            if get_user_data(guild_key, target_id, "last_dsize_size", 0) == -1:
                await interaction.response.send_message(f"{target_user.display_name} 已經是男娘了。", ephemeral=True)
                return
            if get_user_data(guild_key, target_id, "dsize_anti_surgery") == str(now):
                await interaction.response.send_message(f"{target_user.display_name} 使用了抗手術藥物，無法進行手術。", ephemeral=True)
                return
            removed = await ItemSystem.remove_item_from_user(guild_key, user_id, "rusty_scalpel", 1)
            if not removed:
                await interaction.response.send_message("你沒有生鏽的手術刀，無法進行手術。", ephemeral=True)
            # update user statistics
            statistics = get_user_data(0, target_id, "dsize_statistics", {})
            statistics["total_surgeries"] = statistics.get("total_surgeries", 0) + 1
            statistics["failed_surgeries"] = statistics.get("failed_surgeries", 0) + 1
            statistics["mangirl_count"] = statistics.get("mangirl_count", 0) + 1
            set_user_data(0, target_id, "dsize_statistics", statistics)
            performer_statistics = get_user_data(0, user_id, "dsize_statistics", {})
            performer_statistics["total_performed_surgeries"] = performer_statistics.get("total_performed_surgeries", 0) + 1
            set_user_data(0, user_id, "dsize_statistics", performer_statistics)
            orig_size = get_user_data(guild_key, target_id, "last_dsize_size", 0)
            set_user_data(guild_key, target_id, "last_dsize_size", -1)
            # print(f"[DSize] {interaction.user} performed rusty surgery on {target_user.display_name}, original size: {orig_size} cm, new size: -1 cm")
            log(f"{interaction.user} performed rusty surgery on {target_user.display_name}, original size: {orig_size} cm, new size: -1 cm", module_name="dsize", user=interaction.user, guild=interaction.guild)
            target_name = "自己" if target_id == user_id else " " + target_user.display_name + " "
            embed = discord.Embed(title=f"{interaction.user.display_name} 幫{target_name}動手術！", color=0xff0000)
            embed.add_field(name=f"{orig_size} cm", value=f"8{'💥' * (orig_size - 1)}D", inline=False)
            await interaction.response.send_message(content=f"{target_user.mention} 被抓去動手術。", embed=embed)
            while orig_size > 0:
                d_string_new = "💥" * orig_size
                embed.set_field_at(0, name=f"{orig_size} cm", value=f"8{d_string_new}", inline=False)
                await interaction.edit_original_response(embed=embed)
                await asyncio.sleep(0.2)
                orig_size -= min(random.randint(2, 5), orig_size)
            embed.set_field_at(0, name=f"-1 cm", value=f"8", inline=False)
            await interaction.edit_original_response(content=f"{target_user.mention} 變男娘了。", embed=embed)
            
            # Save to history
            history = get_user_data(guild_key, target_id, "dsize_history", [])
            history.append({
                "date": now.isoformat(),
                "size": -1,
                "type": "手術失敗"
            })
            if len(history) > 100:
                history = history[-100:]
            set_user_data(guild_key, target_id, "dsize_history", history)
    await interaction.response.send_modal(SelectUserModal())
    
async def use_anti_surgery(interaction: discord.Interaction):
    user_id = interaction.user.id
    guild_key = interaction.guild.id if interaction.guild else None
    now = (datetime.utcnow() + timedelta(hours=8)).date()
    removed = await ItemSystem.remove_item_from_user(guild_key, user_id, "anti_surgery", 1)
    if not removed:
        await interaction.response.send_message("你沒有抗手術藥物，無法使用。", ephemeral=True)
        return
    # update user statistics
    statistics = get_user_data(0, user_id, "dsize_statistics", {})
    statistics["total_anti_surgery_used"] = statistics.get("total_anti_surgery_used", 0) + 1
    set_user_data(0, user_id, "dsize_statistics", statistics)
    set_user_data(guild_key, user_id, "dsize_anti_surgery", now)
    await interaction.response.send_message("你使用了抗手術藥物！\n今天不會被手術。")
    # print(f"[DSize] {interaction.user} used anti-surgery drug in guild {guild_key}")
    log(f"{interaction.user} 使用了抗手術藥物", module_name="dsize", user=interaction.user, guild=interaction.guild)

async def use_cloud_ruler(interaction: discord.Interaction):
    user_id = interaction.user.id
    guild_key = interaction.guild.id if interaction.guild else None
    class SelectUserModal(discord.ui.Modal, title="要幫誰量長度？"):
        target_user = discord.ui.Label(text="選擇用戶", component=discord.ui.UserSelect(placeholder="選擇一個用戶", min_values=1, max_values=1))

        async def on_submit(self, interaction: discord.Interaction):
            target_user = self.target_user.component.values[0]
            target_id = target_user.id
            target_id = int(target_id)
            now = (datetime.utcnow() + timedelta(hours=8)).date()
            last = get_user_data(guild_key, target_id, "last_dsize")
            if last is not None and not isinstance(last, datetime):
                # If last is a string (e.g., from JSON), convert to date
                try:
                    last = datetime.fromisoformat(str(last)).date()
                except Exception:
                    last = datetime(1970, 1, 1).date()
            elif isinstance(last, datetime):
                last = last.date()
            if last is None:
                last = datetime(1970, 1, 1).date()
            if now == last:
                await interaction.response.send_message(f"{target_user.display_name} 今天量過屌長了，無法幫他量長度。", ephemeral=True)
                return
            # size = get_user_data(guild_key, target_id, "last_dsize_size", 0)
            # if size == -1:
            #     await interaction.response.send_message(f"{target_user.display_name} 是男娘，無法幫他量長度。", ephemeral=True)
            #     return
            removed = await ItemSystem.remove_item_from_user(guild_key, user_id, "cloud_ruler", 1)
            if not removed:
                await interaction.response.send_message("你沒有雲端尺，無法幫他量長度。", ephemeral=True)
                return
            # check if user is online
            target = interaction.guild.get_member(target_id)
            if target.status == discord.Status.offline:
                await interaction.response.send_message(f"{target_user.display_name} 不在線上，無法幫他量長度。", ephemeral=True)
                return
            max_size = get_server_config(guild_key, "dsize_max", 30)
            statistics = get_user_data(0, target_id, "dsize_statistics", {})
            statistics["total_uses"] = statistics.get("total_uses", 0) + 1
            set_user_data(0, target_id, "dsize_statistics", statistics)

            # 隨機產生長度
            size = random.randint(1, max_size)
            fake_size = None
            if "ItemSystem" in modules:
                fake_ruler_used = get_user_data(guild_key, target_id, "dsize_fake_ruler_used", "False") == "True"
                if fake_ruler_used:
                    extra_size = random.randint(10, 20)
                    fake_size = size + extra_size
                    # reset fake ruler usage
                    set_user_data(guild_key, target_id, "dsize_fake_ruler_used", False)
                    set_user_data(guild_key, target_id, "dsize_fake_ruler_used_date", now)
                    set_user_data(guild_key, target_id, "last_dsize_fake_size", fake_size)
            final_size = fake_size if fake_size is not None else size
            log(f"對 {target_user.display_name} 使用了雲端尺, 長度: {size} cm, 最終長度: {final_size} cm", module_name="dsize", user=interaction.user, guild=interaction.guild)

            # 建立 Embed 訊息
            embed = discord.Embed(title=f"{interaction.user.display_name} 幫 {target_user.display_name} 測量長度：", color=0x00ff00)
            embed.add_field(name="1 cm", value=f"8D", inline=False)
            embed.timestamp = datetime.now(timezone.utc)
            await interaction.response.send_message(content=f"{target_user.mention} 被抓去量長度。", embed=embed)
            # animate to size
            speed = size // 50 + 1
            for i in range(1, size + 1, speed):
                d_string = "=" * (i - 1)
                current_size = i
                embed.set_field_at(0, name=f"{current_size} cm", value=f"8{d_string}D", inline=False)
                await interaction.edit_original_response(content=f"{target_user.mention} 被抓去量長度。", embed=embed)
                await asyncio.sleep(0.1)
            # final
            d_string = "=" * (size - 1)
            embed.set_field_at(0, name=f"{final_size} cm", value=f"8{d_string}D", inline=False)
            await interaction.edit_original_response(content=f"{target_user.mention} 被抓去量長度。", embed=embed)
    await interaction.response.send_modal(SelectUserModal())

if "ItemSystem" in modules:
    items = [
        {
            "id": "fake_ruler",
            "name": "自欺欺人尺",
            "description": "使用後下次量長度時或許會更長？",
            "callback": use_fake_ruler,
        },
        {
            "id": "grass",
            "name": "草",
            "description": "這是一把草，可以用來草飼男娘。使用 `/dsize-feedgrass`。",
            "callback": None,
        },
        {
            "id": "scalpel",
            "name": "手術刀",
            "description": "這是一把手術刀，可以用來進行手術，必定成功。",
            "callback": use_scalpel,
        },
        {
            "id": "rusty_scalpel",
            "name": "生鏽的手術刀",
            "description": "這是一把生鏽的手術刀，可以強制感染進而變成男娘。",
            "callback": use_rusty_scalpel,
        },
        {
            "id": "anti_surgery",
            "name": "抗手術藥物",
            "description": "一顆屌型的藥丸。使用後可以防止一天被手術。",
            "callback": use_anti_surgery,
        },
        {
            "id": "cloud_ruler",
            "name": "雲端尺",
            "description": "這是一把雲端尺，可以幫處於線上的網友量長度。",
            "callback": use_cloud_ruler,
        }
    ]
    import ItemSystem
    ItemSystem.items.extend(items)


if __name__ == "__main__":
    start_bot()
