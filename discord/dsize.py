# Powered by ChatGPT lol
import discord
import random
import asyncio
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timedelta, timezone
from globalenv import bot, start_bot, get_user_data, set_user_data, get_all_user_data, get_server_config, set_server_config, modules, get_command_mention, config
from PIL import Image, ImageDraw
from io import BytesIO
from logger import log
import logging
import os
import json
from typing import Union
from urllib.parse import urlencode
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
    Returns: (is_new_checkin, checkin_streak, broke_streak, broke_streak_on, freeze_used)
    freeze_used: number of checkin_freeze items consumed (0 if none)
    """
    now = (datetime.now(timezone(timedelta(hours=8)))).date()  # 台灣時間
    
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
    if last_checkin is not None and last_checkin >= now:
        # Already checked in today
        statistics = get_user_data(0, user_id, "dsize_statistics", {})
        return False, statistics.get("checkin_streak", 0), False, None, 0

    # reset claim reward unsuccessful flag
    set_user_data(0, user_id, "claim_reward_unsuccessful", False)

    # Calculate streak
    statistics = get_user_data(0, user_id, "dsize_statistics", {})
    checkin_streak = statistics.get("checkin_streak", 0)
    broke_streak = False
    broke_streak_on = None
    freeze_used = 0
    
    # Check if streak continues (last checkin was yesterday)
    if last_checkin and last_checkin == now - timedelta(days=1):
        checkin_streak += 1
    else:
        # Reset streak
        if last_checkin and last_checkin < now - timedelta(days=1):
            missed_days = (now - last_checkin).days - 1  # days without checkin
            # Check if user has checkin_freeze items
            if "ItemSystem" in modules:
                freeze_count = await ItemSystem.get_user_items(0, user_id, "checkin_freeze")
                if freeze_count > 0:
                    if freeze_count >= missed_days:
                        # Enough freeze to cover all missed days — streak continues
                        freeze_used = missed_days
                        await ItemSystem.remove_item_from_user(0, user_id, "checkin_freeze", missed_days)
                        checkin_streak += 1
                    else:
                        # Not enough freeze — use all, still break streak
                        freeze_used = freeze_count
                        await ItemSystem.remove_item_from_user(0, user_id, "checkin_freeze", freeze_count)
                        broke_streak = True
                        broke_streak_on = checkin_streak
                        checkin_streak = 1
                else:
                    broke_streak = True
                    broke_streak_on = checkin_streak
                    checkin_streak = 1
            else:
                broke_streak = True
                broke_streak_on = checkin_streak
                checkin_streak = 1
        else:
            checkin_streak = 1  # start new streak (no last_checkin)
    
    # Update statistics
    statistics["total_checkins"] = statistics.get("total_checkins", 0) + 1
    statistics["checkin_streak"] = checkin_streak
    set_user_data(0, user_id, "dsize_statistics", statistics)
    set_user_data(0, user_id, "last_checkin", now)
    
    return True, checkin_streak, broke_streak, broke_streak_on, freeze_used


async def handle_checkin_rewards(interaction: discord.Interaction, user: Union[discord.User, discord.Member], checkin_streak: int, guild_key: int = None):
    """
    Handle check-in rewards and goal selection.
    Shows rewards only on milestone days (7, and user-selected goals).
    """
    if checkin_streak < 7:
        # No rewards shown until day 7
        return
    
    # Get user's current goal
    current_goal = get_user_data(0, user.id, "checkin_goal")
    
    # Check if this is a milestone day
    is_milestone = False
    if checkin_streak == 7 or (current_goal and checkin_streak >= current_goal):
        is_milestone = True
    
    if not current_goal and checkin_streak > 7:
        # User has no goal set but exceeded day 7, set default goal to 14
        current_goal = checkin_streak + 7
        set_user_data(0, user.id, "checkin_goal", current_goal)
        set_user_data(0, user.id, "checkin_reward", ["grass", 5, "草"])
        await interaction.followup.send(f"{user.mention}\n由於您上次未選擇目標，系統已自動為您設定目標為 草 x 5。")
        
    
    if not is_milestone:
        return
    
    # check not global
    if guild_key is None:
        set_user_data(0, user.id, "claim_reward_unsuccessful", True)
        await interaction.followup.send(f"{user.mention}\n你獲得了簽到獎勵！\n請在有此機器人的伺服器中使用 {await get_command_mention('dsize')} 以領取獎勵。")
        return
    set_user_data(0, user.id, "claim_reward_unsuccessful", False)
    
    # Give random reward
    if "ItemSystem" in modules:
        # Random reward pool
        # use list instead of tuple to make it mutable
        level_1_rewards = [
            ["grass", 5, "草"],
            ["fake_ruler", 1, "自欺欺人尺"],
            ["anti_surgery", 1, "抗手術藥物"],
            ["cheque_100", 1, "100元支票"],
        ]

        level_2_rewards = [
            ["grass", 20, "草"],
            ["fake_ruler", 5, "自欺欺人尺"],
            ["anti_surgery", 5, "抗手術藥物"],
            ["surgery", 1, "手術刀"],
            ["rusty_surgery", 1, "生鏽的手術刀"],
            ["cheque_500", 1, "500元支票"],
        ]

        level_3_rewards = [
            ["grass", 100, "草"],
            ["fake_ruler", 20, "自欺欺人尺"],
            ["anti_surgery", 20, "抗手術藥物"],
            ["surgery", 3, "手術刀"],
            ["rusty_surgery", 3, "生鏽的手術刀"],
            ["cheque_1000", 1, "1000元支票"],
        ]
        
        if checkin_streak == 7:
            reward = random.choice(level_1_rewards)
        else:
            reward = get_user_data(0, user.id, "checkin_reward")
        await ItemSystem.give_item_to_user(guild_key, user.id, reward[0], reward[1])

        # Update statistics
        statistics = get_user_data(0, user.id, "dsize_statistics", {})
        statistics["total_checkins"] = statistics.get("total_checkins", 0) + 1
        statistics["checkin_streak"] = checkin_streak
        set_user_data(0, user.id, "dsize_statistics", statistics)
        level_1_reward = random.choice(level_1_rewards)
        level_2_reward = random.choice(level_2_rewards)
        level_3_reward = random.choice(level_3_rewards)
        
        # Create goal selection view
        class GoalSelectionView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=300)  # 5 minutes
                self.selected_goal = None
            
            async def on_timeout(self):
                for child in self.children:
                    child.disabled = True
                self.selected_goal = checkin_streak + 7
                set_user_data(0, user.id, "checkin_goal", self.selected_goal)
                set_user_data(0, user.id, "checkin_reward", level_1_reward)
                noteEmbed = discord.Embed(
                    title="目標設定",
                    description=f"超過時間未選擇目標，系統已自動為你設定下一個目標：{self.selected_goal} 天！繼續加油！",
                    color=0x00ff00
                )
                await interaction.response.edit_message(embeds=[embed, noteEmbed], view=None)
                self.stop()
            
            @discord.ui.button(label=f"+7 天 ({level_1_reward[2]} x {level_1_reward[1]})", style=discord.ButtonStyle.primary)
            async def goal_7(self, interaction: discord.Interaction, button: discord.ui.Button):
                if interaction.user.id != user.id:
                    await interaction.response.send_message("這不是你的目標選擇。", ephemeral=True)
                    return
                self.selected_goal = checkin_streak + 7
                set_user_data(0, user.id, "checkin_goal", self.selected_goal)
                set_user_data(0, user.id, "checkin_reward", level_1_reward)
                noteEmbed = discord.Embed(
                    title="目標設定",
                    description=f"你已選擇下一個目標：{self.selected_goal} 天！繼續加油！",
                    color=0x00ff00
                )
                await interaction.response.edit_message(embeds=[embed, noteEmbed], view=None)
                self.stop()
            
            @discord.ui.button(label=f"+14 天 ({level_2_reward[2]} x {level_2_reward[1]})", style=discord.ButtonStyle.success)
            async def goal_14(self, interaction: discord.Interaction, button: discord.ui.Button):
                if interaction.user.id != user.id:
                    await interaction.response.send_message("這不是你的目標選擇。", ephemeral=True)
                    return
                self.selected_goal = checkin_streak + 14
                set_user_data(0, user.id, "checkin_goal", self.selected_goal)
                set_user_data(0, user.id, "checkin_reward", level_2_reward)
                noteEmbed = discord.Embed(
                    title="目標設定",
                    description=f"你已選擇下一個目標：{self.selected_goal} 天！繼續加油！",
                    color=0x00ff00
                )
                await interaction.response.edit_message(embeds=[embed, noteEmbed], view=None)
                self.stop()
            
            @discord.ui.button(label=f"+30 天 ({level_3_reward[2]} x {level_3_reward[1]})", style=discord.ButtonStyle.danger)
            async def goal_30(self, interaction: discord.Interaction, button: discord.ui.Button):
                if interaction.user.id != user.id:
                    await interaction.response.send_message("這不是你的目標選擇。", ephemeral=True)
                    return
                self.selected_goal = checkin_streak + 30
                set_user_data(0, user.id, "checkin_goal", self.selected_goal)
                set_user_data(0, user.id, "checkin_reward", level_3_reward)
                noteEmbed = discord.Embed(
                    title="目標設定",
                    description=f"你已選擇下一個目標：{self.selected_goal} 天！繼續加油！",
                    color=0x00ff00
                )
                await interaction.response.edit_message(embeds=[embed, noteEmbed], view=None)
                self.stop()
        
        # Send reward notification with goal selection
        embed = discord.Embed(
            title="🎉 簽到獎勵！",
            description=f"恭喜達成 {checkin_streak} 天連續簽到！\n獲得：{reward[2]} x {reward[1]}！",
            color=0x00ff00
        )
        noteEmbed = discord.Embed(
            title="選擇下一個目標",
            description="請選擇你的下一個簽到目標天數：",
            color=0xffa500
        )
        await interaction.followup.send(user.mention, embeds=[embed, noteEmbed], view=GoalSelectionView())


@bot.tree.command(name="dsize", description="量屌長")
@app_commands.describe(global_dsize="是否使用全域紀錄 (預設否)")
@app_commands.choices(global_dsize=[
    app_commands.Choice(name="否", value="False"),
    app_commands.Choice(name="是", value="True"),
])
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def dsize(interaction: discord.Interaction, global_dsize: str = "False"):
    global_dsize = (global_dsize == "True")
    user_id = interaction.user.id
    # Use timezone-aware UTC and convert to Taiwan time (UTC+8)
    # ew broken
    now = datetime.now(timezone(timedelta(hours=8))).date()  # 台灣時間

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
    
    last_anti_surgery = get_user_data(guild_key, user_id, "dsize_anti_surgery")
    if last_anti_surgery is not None and not isinstance(last_anti_surgery, datetime):
        try:
            last_anti_surgery = datetime.fromisoformat(str(last_anti_surgery)).date()
        except Exception:
            last_anti_surgery = None
    elif isinstance(last_anti_surgery, datetime):
        last_anti_surgery = last_anti_surgery.date()

    # 檢查是否已經使用過指令，並且是否已超過一天
    if last >= now:
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
    is_new_checkin, checkin_streak, broke_streak, broke_streak_on, freeze_used = await process_checkin(user_id)

    message = ""

    # 隨機產生長度
    size = random.randint(1, max_size)
    # check if yesterday used anti-surgery
    if last_anti_surgery is not None and last_anti_surgery >= now - timedelta(days=2):
        size = max(-1, size - random.randint(1, max_size // 2))
        message = "糟糕！有副作用！"
    size = size if size != 0 else -1
    if size > 0:
        fake_size = None
        if "ItemSystem" in modules:
            fake_ruler_used = get_user_data(guild_key, user_id, "dsize_fake_ruler_used", False)
            if fake_ruler_used:
                extra_size = random.randint(10, 20)
                fake_size = size + extra_size
                # reset fake ruler usage
                set_user_data(guild_key, user_id, "dsize_fake_ruler_used", False)
                set_user_data(guild_key, user_id, "dsize_fake_ruler_used_date", now)
                set_user_data(guild_key, user_id, "last_dsize_fake_size", fake_size)
        final_size = fake_size if fake_size is not None else size
    else:
        final_size = size

    # 建立 Embed 訊息
    embed = discord.Embed(title=f"{interaction.user.display_name} 的長度：", color=0x00ff00)
    embed.add_field(name="1 cm", value=f"8D", inline=False)
    embed.timestamp = datetime.now(timezone.utc)
    
    if size <= 0:
        embed.fields[0].name = f"{size} cm"
        embed.fields[0].value = "8"
        message += "\n你變男娘了。"
        embed.color = 0xff0000
    
    # Set footer with check-in info
    if is_new_checkin:
        if broke_streak:
            if freeze_used > 0:
                footer_text = f"你在第 {broke_streak_on} 天打破了簽到紀錄，消耗了 {freeze_used} 個凍結球！重新開始簽到！ | 簽到第 {checkin_streak} 天！"
            else:
                footer_text = f"你在第 {broke_streak_on} 天打破了簽到紀錄，重新開始簽到！ | 簽到第 {checkin_streak} 天！"
        elif freeze_used > 0:
            footer_text = f"簽到第 {checkin_streak} 天！凍結球保護了連續（消耗 {freeze_used} 個）"
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

    if size > 0:
        # animate to size
        break_counter = 0
        break_content = None
        speed = size // 50 + 1
        for i in range(1, size + 1, speed):
            if random.random() < 0.1:
                break_counter += 1
                if break_content is None:
                    break_content = "你的ㄐㄐ今天好像怪怪的。"
            d_chars = list("=" * (i - 1))
            if break_counter > 0 and len(d_chars) > 0:
                num_replace = min(break_counter, len(d_chars))
                for idx in random.sample(range(len(d_chars)), num_replace):
                    d_chars[idx] = "≈"
            d_string = "".join(d_chars)
            if break_counter >= 5:
                size = -1
                final_size = -1
                break_content = "你變成男娘了。"
                embed.set_field_at(0, name=f"{size} cm", value="8", inline=False)
                embed.color = 0xff0000
                await interaction.edit_original_response(content=break_content, embed=embed)
                break
            current_size = i
            embed.set_field_at(0, name=f"{current_size} cm", value=f"8{d_string}D", inline=False)
            await interaction.edit_original_response(content=break_content, embed=embed)
            await asyncio.sleep(0.1)
        if break_counter < 5:
            # final
            d_string = "=" * (size - 1)
            embed.set_field_at(0, name=f"{final_size} cm", value=f"8{d_string}D", inline=False)
            await asyncio.sleep(0.5)
            await interaction.edit_original_response(content=break_content, embed=embed)

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
        await handle_checkin_rewards(interaction, interaction.user, checkin_streak, guild_key)
        log(f"簽到成功，連續 {checkin_streak} 天", module_name="dsize", user=interaction.user, guild=interaction.guild)
    elif claimed_unsuccessful:
        await handle_checkin_rewards(interaction, interaction.user, checkin_streak, guild_key)
        set_user_data(0, user_id, "claim_reward_unsuccessful", False)
        log(f"簽到成功，連續 {checkin_streak} 天 (補發獎勵)", module_name="dsize", user=interaction.user, guild=interaction.guild)

    surgery_percent = get_server_config(guild_key, "dsize_surgery_percent", 10)
    drop_item_chance = get_server_config(guild_key, "dsize_drop_item_chance", 5)
    # check if user got surgery chance
    if percent_random(surgery_percent) and size > 0:
        if last_anti_surgery is not None and last_anti_surgery >= now:
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
                        ori = min(size + i - 2, 200)  # limit animation length to 200 because discord message length limit
                        while ori > 0:
                            d_string_new = "💥" * ori
                            embed.set_field_at(0, name=f"{ori + 1} cm", value=f"8{d_string_new}", inline=False)
                            await interaction.edit_original_response(content="正在手術中...💥", embed=embed)
                            await asyncio.sleep(0.1)
                            ori -= min(random.randint(2, 10), ori)
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
        app_commands.Choice(name="否", value="False"),
        app_commands.Choice(name="是", value="True"),
    ],
    reverse=[
        app_commands.Choice(name="否", value="False"),
        app_commands.Choice(name="是", value="True"),
    ]
)
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def dsize_leaderboard(interaction: discord.Interaction, limit: int = 10, global_leaderboard: str = "False", reverse: str = "False"):
    global_leaderboard = (global_leaderboard == "True")
    reverse = (reverse == "True")
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

    today = (datetime.now(timezone(timedelta(hours=8)))).date()  # 台灣時間
    next_day = today + timedelta(days=1)  # for viagra check
    valid_user_ids = set(get_all_user_data(guild_id, "last_dsize", value=str(today)).keys()) | \
                     set(get_all_user_data(guild_id, "last_dsize", value=str(next_day)).keys())
    all_data_fake = {}
    for user_id in valid_user_ids:
        size = get_user_data(guild_id, user_id, "last_dsize_size")
        if size is not None:
            leaderboard.append((user_id, size))
        fake_ruler_used_date = get_user_data(guild_id, user_id, "dsize_fake_ruler_used_date")
        if fake_ruler_used_date is not None:
            try:
                if not isinstance(fake_ruler_used_date, datetime):
                    fake_ruler_used_date = datetime.fromisoformat(str(fake_ruler_used_date)).date()
                else:
                    fake_ruler_used_date = fake_ruler_used_date.date()
            except Exception:
                fake_ruler_used_date = None
            if fake_ruler_used_date == today:
                fake_size = get_user_data(guild_id, user_id, "last_dsize_fake_size")
                if fake_size is not None:
                    all_data_fake[user_id] = {"last_dsize_fake_size": fake_size}
    
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
async def dsize_battle_command(interaction: discord.Interaction, opponent: Union[discord.User, discord.Member]):
    await dsize_battle(interaction, opponent)

@bot.tree.context_menu(name="跟他 dsize 對決")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def dsize_battle_context(interaction: discord.Interaction, opponent: Union[discord.User, discord.Member]):
    await dsize_battle(interaction, opponent)


async def dsize_battle(interaction: discord.Interaction, opponent: Union[discord.User, discord.Member]):
    original_user = interaction.user
    user_id = interaction.user.id
    opponent_id = opponent.id
    now = (datetime.now(timezone(timedelta(hours=8)))).date()  # 台灣時間

    if user_id == opponent_id:
        await interaction.response.send_message("不能跟自己比屌長。", ephemeral=True)
        return
    
    if opponent.bot:
        await interaction.response.send_message("不能跟機器人比屌長。", ephemeral=True)
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

    if last_user >= now:
        await interaction.response.send_message("你今天已經量過屌長了。", ephemeral=True)
        return
    if last_opponent >= now:
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
            side_effect_messages = []
            # check anti-surgery side effect for user
            user_anti_surgery = get_user_data(guild_key, user_id, "dsize_anti_surgery")
            if user_anti_surgery is not None and not isinstance(user_anti_surgery, datetime):
                try:
                    user_anti_surgery = datetime.fromisoformat(str(user_anti_surgery)).date()
                except Exception:
                    user_anti_surgery = None
            elif isinstance(user_anti_surgery, datetime):
                user_anti_surgery = user_anti_surgery.date()
            if user_anti_surgery is not None and user_anti_surgery >= now - timedelta(days=2):
                size_user = max(-1, size_user - random.randint(1, max_size // 2))
                size_user = size_user if size_user != 0 else -1
                if size_user == -1:
                    side_effect_messages.append(f"{original_user.display_name} 糟糕！有副作用！變男娘了！")
                else:
                    side_effect_messages.append(f"{original_user.display_name} 糟糕！有副作用！")
            # check anti-surgery side effect for opponent
            opponent_anti_surgery = get_user_data(guild_key, opponent_id, "dsize_anti_surgery")
            if opponent_anti_surgery is not None and not isinstance(opponent_anti_surgery, datetime):
                try:
                    opponent_anti_surgery = datetime.fromisoformat(str(opponent_anti_surgery)).date()
                except Exception:
                    opponent_anti_surgery = None
            elif isinstance(opponent_anti_surgery, datetime):
                opponent_anti_surgery = opponent_anti_surgery.date()
            if opponent_anti_surgery is not None and opponent_anti_surgery >= now - timedelta(days=2):
                size_opponent = max(-1, size_opponent - random.randint(1, max_size // 2))
                size_opponent = size_opponent if size_opponent != 0 else -1
                if size_opponent == -1:
                    side_effect_messages.append(f"{opponent.display_name} 糟糕！有副作用！變男娘了！")
                else:
                    side_effect_messages.append(f"{opponent.display_name} 糟糕！有副作用！")
            # print(f"[DSize] {interaction.user} vs {opponent} - {size_user} cm vs {size_opponent} cm")
            log(f"{original_user} vs {opponent} - {size_user} cm vs {size_opponent} cm", module_name="dsize", user=interaction.user, guild=interaction.guild)
            speed = max(size_user, size_opponent) // 50 + 1

            # 取得訊息物件
            msg = await interaction.original_response()
            t = datetime.now(timezone.utc)

            user_break_counter = 0
            opponent_break_counter = 0
            user_broke = size_user <= 0
            opponent_broke = size_opponent <= 0
            battle_content = "開始對決。"
            user_break_message = None
            opponent_break_message = None
            max_anim = max(size_user if size_user > 0 else 1, size_opponent if size_opponent > 0 else 1)
            for i in range(1, max_anim, speed):
                # 10% break check for user
                if not user_broke and i < size_user and random.random() < 0.1:
                    user_break_counter += 1
                    if user_break_counter == 1:
                        user_break_message = f"{original_user.display_name} 的ㄐㄐ今天好像怪怪的。"
                    if user_break_counter >= 5:
                        size_user = -1
                        user_broke = True
                        user_break_message = f"{original_user.display_name} 變成男娘了。"
                # 10% break check for opponent
                if not opponent_broke and i < size_opponent and random.random() < 0.1:
                    opponent_break_counter += 1
                    if opponent_break_counter == 1:
                        opponent_break_message = f"{opponent.display_name} 的ㄐㄐ今天好像怪怪的。"
                    if opponent_break_counter >= 5:
                        size_opponent = -1
                        opponent_broke = True
                        opponent_break_message = f"{opponent.display_name} 變成男娘了。"
                content_lines = ["開始對決。"]
                if user_break_message:
                    content_lines.append(user_break_message)
                if opponent_break_message:
                    content_lines.append(opponent_break_message)
                battle_content = "\n".join(content_lines)
                # Build user display
                if user_broke:
                    user_field_name = f"{original_user.display_name} 的長度："
                    user_field_value = "8"
                else:
                    d_chars_user = list("=" * min(i, size_user - 1))
                    if user_break_counter > 0 and len(d_chars_user) > 0:
                        num = min(user_break_counter, len(d_chars_user))
                        for idx in random.sample(range(len(d_chars_user)), num):
                            d_chars_user[idx] = "≈"
                    user_field_name = f"{original_user.display_name} 的長度："
                    user_field_value = f"{size_user if i >= size_user - 1 else '??'} cm\n8{''.join(d_chars_user)}D"
                # Build opponent display
                if opponent_broke:
                    opp_field_name = f"{opponent.display_name} 的長度："
                    opp_field_value = "8"
                else:
                    d_chars_opp = list("=" * min(i, size_opponent - 1))
                    if opponent_break_counter > 0 and len(d_chars_opp) > 0:
                        num = min(opponent_break_counter, len(d_chars_opp))
                        for idx in random.sample(range(len(d_chars_opp)), num):
                            d_chars_opp[idx] = "≈"
                    opp_field_name = f"{opponent.display_name} 的長度："
                    opp_field_value = f"{size_opponent if i >= size_opponent - 1 else '??'} cm\n8{''.join(d_chars_opp)}D"
                embed = discord.Embed(title="比長度", color=0x00ff00)
                embed.add_field(name=user_field_name, value=user_field_value, inline=False)
                embed.add_field(name=opp_field_name, value=opp_field_value, inline=False)
                embed.timestamp = t
                if not guild_key:
                    embed.set_footer(text="此次對決將記錄到全域排行榜。")
                await msg.edit(content=battle_content, embed=embed)
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

            if size_user == -1:
                user_final_name = f"{original_user.display_name} 的長度："
                user_final_value = "-1 cm\n8"
            else:
                d_string_user = "=" * (size_user - 1)
                user_final_name = f"{original_user.display_name} 的長度："
                user_final_value = f"{size_user} cm\n8{d_string_user}D"
            if size_opponent == -1:
                opp_final_name = f"{opponent.display_name} 的長度："
                opp_final_value = "-1 cm\n8"
            else:
                d_string_opponent = "=" * (size_opponent - 1)
                opp_final_name = f"{opponent.display_name} 的長度："
                opp_final_value = f"{size_opponent} cm\n8{d_string_opponent}D"
            embed = discord.Embed(title="比長度", color=0x00ff00)
            embed.add_field(name=user_final_name, value=user_final_value, inline=False)
            embed.add_field(name=opp_final_name, value=opp_final_value, inline=False)
            embed.add_field(name="結果：", value=result, inline=False)
            embed.timestamp = t
            
            # Process daily check-in for both users (always global)
            user_is_new_checkin, user_checkin_streak, user_broke_streak, user_broke_streak_on, user_freeze_used = await process_checkin(user_id)
            opponent_is_new_checkin, opponent_checkin_streak, opponent_broke_streak, opponent_broke_streak_on, opponent_freeze_used = await process_checkin(opponent_id)
            
            # Set footer with check-in info
            footer_parts = []
            if user_is_new_checkin:
                if user_broke_streak:
                    if user_freeze_used > 0:
                        footer_parts.append(f"{original_user.display_name} 在第 {user_broke_streak_on} 天打破了簽到紀錄，消耗了 {user_freeze_used} 個凍結球！重新開始！")
                    else:
                        footer_parts.append(f"{original_user.display_name} 在第 {user_broke_streak_on} 天打破了簽到紀錄，重新開始！")
                elif user_freeze_used > 0:
                    footer_parts.append(f"{original_user.display_name} 簽到第 {user_checkin_streak} 天！凍結球保護了連續（消耗 {user_freeze_used} 個）")
                else:
                    footer_parts.append(f"{original_user.display_name} 簽到第 {user_checkin_streak} 天！")
            if opponent_is_new_checkin:
                if opponent_broke_streak:
                    if opponent_freeze_used > 0:
                        footer_parts.append(f"{opponent.display_name} 在第 {opponent_broke_streak_on} 天打破了簽到紀錄，消耗了 {opponent_freeze_used} 個凍結球！重新開始！")
                    else:
                        footer_parts.append(f"{opponent.display_name} 在第 {opponent_broke_streak_on} 天打破了簽到紀錄，重新開始！")
                elif opponent_freeze_used > 0:
                    footer_parts.append(f"{opponent.display_name} 簽到第 {opponent_checkin_streak} 天！凍結球保護了連續（消耗 {opponent_freeze_used} 個）")
                else:
                    footer_parts.append(f"{opponent.display_name} 簽到第 {opponent_checkin_streak} 天！")
            
            if not guild_key:
                footer_parts.append("此次對決將記錄到全域排行榜。")
            
            footer_parts.extend(side_effect_messages)
            
            if footer_parts:
                embed.set_footer(text=" | ".join(footer_parts))
            
            await msg.edit(content=battle_content, embed=embed)

            set_user_data(guild_key, user_id, "last_dsize_size", size_user)
            set_user_data(guild_key, opponent_id, "last_dsize_size", size_opponent)
            
            # Handle check-in rewards if applicable (milestone days only)
            if user_is_new_checkin:
                # Create a temporary interaction-like object for user rewards
                # We'll send it as a followup message
                await handle_checkin_rewards(interaction, original_user, user_checkin_streak, guild_key)
                log(f"{original_user.display_name} 簽到成功，連續 {user_checkin_streak} 天", module_name="dsize", user=original_user, guild=interaction.guild)
            
            if opponent_is_new_checkin:
                # For opponent, we need to note this but can't show interactive buttons
                # since they're not the one who triggered the interaction
                await handle_checkin_rewards(interaction, opponent, opponent_checkin_streak, guild_key)
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
@app_commands.default_permissions(manage_guild=True)
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
    embed.add_field(name="撿到物品次數", value=str(total_drops), inline=False)
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
    app_commands.Choice(name="否", value="False"),
    app_commands.Choice(name="是", value="True"),
])
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def dsize_history(interaction: discord.Interaction, user: discord.User = None, global_history: str = "False"):
    global_history = (global_history == "True")
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
        embed.set_footer(text=interaction.guild.name, icon_url=interaction.guild.icon.url if interaction.guild.icon else None)
    else:
        embed.set_footer(text="全域紀錄")
    
    embed.timestamp = datetime.now(timezone.utc)
    
    await interaction.followup.send(embed=embed)
    log(f"查看了 {target_user.display_name} 的歷史紀錄", module_name="dsize", user=interaction.user, guild=interaction.guild)


@bot.tree.command(name=app_commands.locale_str("dsize-feedgrass"), description="草飼男娘")
@app_commands.describe(user="要草飼的對象", global_feedgrass="是否草飼全域排行榜上的男娘 (預設否)")
@app_commands.choices(global_feedgrass=[
    app_commands.Choice(name="否", value="False"),
    app_commands.Choice(name="是", value="True"),
])
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def dsize_feedgrass_command(interaction: discord.Interaction, user: Union[discord.User, discord.Member] = None, global_feedgrass: str = "False"):
    await dsize_feedgrass(interaction, user, global_feedgrass)


@bot.tree.context_menu(name="草飼他")
@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def dsize_feedgrass_context(interaction: discord.Interaction, user: Union[discord.User, discord.Member]):
    await dsize_feedgrass(interaction, user, global_feedgrass="False")


async def dsize_feedgrass(interaction: discord.Interaction, user: Union[discord.User, discord.Member] = None, global_feedgrass: str = "False"):
    if "ItemSystem" not in modules:
        await interaction.response.send_message("此功能需要 ItemSystem 模組。", ephemeral=True)
        return
    if not user:
        user = interaction.user  # self-feedgrass
    global_feedgrass_bool = global_feedgrass.lower() == "true"
    # if user.id == interaction.user.id:
        # await interaction.response.send_message("不能草飼自己。", ephemeral=True)
        # return
    if global_feedgrass_bool:
        guild_id = None
    else:
        if not interaction.is_guild_integration():
            guild_id = None
        else:
            guild_id = interaction.guild.id if interaction.guild else None
    if get_user_data(guild_id, user.id, "last_dsize_size", 0) != -1:
        name = user.display_name + " " if user.id != interaction.user.id else "你"
        await interaction.response.send_message(f"{name}不是男娘，無法草飼。", ephemeral=True)
        return
    removed = await ItemSystem.remove_item_from_user(guild_id, interaction.user.id, "grass", 1)
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
    failed_to_get_history = False
    if interaction.is_guild_integration():
        try:
            async for msg in interaction.channel.history(limit=25):
                if msg.author.id != interaction.user.id and msg.author.id != user.id:
                    random_users.add(msg.author)
        except Exception as e:
            log(f"草飼獲取訊息時發生錯誤: {e}", level=logging.WARNING, module_name="dsize", user=interaction.user, guild=interaction.guild)
            failed_to_get_history = True
    random_users = list(random_users)
    image_bytes = await generate_feedgrass_image(user, interaction.user, random_users)
    if interaction.user.id != user.id:
        embed = discord.Embed(title=f"{interaction.user.display_name} 草飼了 {user.display_name}！", color=0x00ff00)
    else:
        embed = discord.Embed(title=f"{interaction.user.display_name} 草飼了自己！", color=0x00ff00)
    embed.set_image(url="attachment://feed_grass.png")
    embed.timestamp = datetime.now(timezone.utc)
    if failed_to_get_history:
        embed.set_footer(text="無法獲取頻道歷史訊息，未能顯示其他用戶的頭像。\n請確保機器人有權限讀取頻道歷史訊息。")
    redirect_uri = config('website_url') + "/contribute-feed-grass"
    url = f"https://discord.com/oauth2/authorize?client_id={bot.application.id}&response_type=code&scope=identify&prompt=none&{urlencode({'redirect_uri': redirect_uri})}"
    btn = discord.ui.Button(label="立即投稿！", url=url, emoji="🔗")
    view = discord.ui.View()
    view.add_item(btn)
    await interaction.followup.send(embed=embed, file=discord.File(image_bytes, "feed_grass.png"), view=view)
    # print(f"[DSize] {interaction.user} fed grass to {user} in guild {interaction.guild.id}")
    log(f"草飼了 {user}", module_name="dsize", user=interaction.user, guild=interaction.guild if not global_feedgrass_bool else None)


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
    guild_key = interaction.guild_id if interaction.guild else None
    if get_user_data(guild_key, user_id, "dsize_fake_ruler_used", False):
        await interaction.response.send_message("你今天已經使用過自欺欺人尺了。", ephemeral=True)
        return
    await ItemSystem.remove_item_from_user(guild_key, interaction.user.id, "fake_ruler", 1)
    set_user_data(guild_key, user_id, "dsize_fake_ruler_used", True)
    await interaction.response.send_message("你使用了自欺欺人尺！\n下次量長度時或許會更長？")
    # print(f"[DSize] {interaction.user} used fake ruler in guild {guild_key}")
    log(f"{interaction.user} 使用了自欺欺人尺", module_name="dsize", user=interaction.user, guild=interaction.guild)

async def use_scalpel(interaction: discord.Interaction):
    user_id = interaction.user.id
    guild_key = interaction.guild_id if interaction.guild else None
    
    class SelectUserModal(discord.ui.Modal, title="要幫誰手術？"):
        target_user = discord.ui.Label(text="選擇用戶", component=discord.ui.UserSelect(placeholder="選擇一個用戶", min_values=1, max_values=1))

        async def on_submit(self, interaction: discord.Interaction):
            target_user = self.target_user.component.values[0]
            target_id = target_user.id
            target_id = int(target_id)
            now = (datetime.now(timezone(timedelta(hours=8)))).date()
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
            if last < now:
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
    guild_key = interaction.guild_id if interaction.guild else None
    
    class SelectUserModal(discord.ui.Modal, title="要幫誰手術？"):
        target_user = discord.ui.Label(text="選擇用戶", component=discord.ui.UserSelect(placeholder="選擇一個用戶", min_values=1, max_values=1))

        async def on_submit(self, interaction: discord.Interaction):
            target_user = self.target_user.component.values[0]
            target_id = target_user.id
            target_id = int(target_id)
            now = (datetime.now(timezone(timedelta(hours=8)))).date()
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
            if last < now:
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
            orig_size = min(orig_size, 200)  # limit length because discord limit
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
                orig_size -= min(random.randint(2, 10), orig_size)
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
    guild_key = interaction.guild_id if interaction.guild else None
    now = (datetime.now(timezone(timedelta(hours=8)))).date()
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
    guild_key = interaction.guild_id if interaction.guild else None
    class SelectUserModal(discord.ui.Modal, title="要幫誰量長度？"):
        target_user = discord.ui.Label(text="選擇用戶", component=discord.ui.UserSelect(placeholder="選擇一個用戶", min_values=1, max_values=1))

        async def on_submit(self, interaction: discord.Interaction):
            target_user = self.target_user.component.values[0]
            target_id = target_user.id
            target_id = int(target_id)
            now = (datetime.now(timezone(timedelta(hours=8)))).date()
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
            if last >= now:
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
            # target = interaction.guild.get_member(target_id)
            # if target.status == discord.Status.offline:
            #     await interaction.response.send_message(f"{target_user.display_name} 不在線上，無法幫他量長度。", ephemeral=True)
            #     return
            max_size = get_server_config(guild_key, "dsize_max", 30)
            statistics = get_user_data(0, target_id, "dsize_statistics", {})
            statistics["total_uses"] = statistics.get("total_uses", 0) + 1
            set_user_data(0, target_id, "dsize_statistics", statistics)

            # 隨機產生長度
            size = random.randint(1, max_size)
            # 抗手術副作用
            side_effect_message = None
            last_anti_surgery_data = get_user_data(guild_key, target_id, "dsize_anti_surgery")
            last_anti_surgery = None
            if last_anti_surgery_data is not None and not isinstance(last_anti_surgery_data, datetime):
                try:
                    last_anti_surgery = datetime.fromisoformat(str(last_anti_surgery_data)).date()
                except Exception:
                    last_anti_surgery = None
            elif isinstance(last_anti_surgery_data, datetime):
                last_anti_surgery = last_anti_surgery_data.date()
            if last_anti_surgery is not None and last_anti_surgery >= now - timedelta(days=2):
                size = max(-1, size - random.randint(1, max_size // 2))
                size = size if size != 0 else -1
                if size == -1:
                    side_effect_message = "糟糕！有副作用！變男娘了！"
                else:
                    side_effect_message = "糟糕！有副作用！"
            set_user_data(guild_key, target_id, "last_dsize_size", size)
            set_user_data(guild_key, target_id, "last_dsize", now)
            fake_size = None
            if "ItemSystem" in modules:
                fake_ruler_used = get_user_data(guild_key, target_id, "dsize_fake_ruler_used", False)
                if fake_ruler_used and size != -1:
                    extra_size = random.randint(10, 20)
                    fake_size = size + extra_size
                    # reset fake ruler usage
                    set_user_data(guild_key, target_id, "dsize_fake_ruler_used", False)
                    set_user_data(guild_key, target_id, "dsize_fake_ruler_used_date", now)
                    set_user_data(guild_key, target_id, "last_dsize_fake_size", fake_size)
            final_size = fake_size if fake_size is not None else size
            log(f"對 {target_user.display_name} 使用了雲端尺, 長度: {size} cm, 最終長度: {final_size} cm", module_name="dsize", user=interaction.user, guild=interaction.guild)

            user_is_new_checkin, user_checkin_streak, user_broke_streak, user_broke_streak_on, user_freeze_used = await process_checkin(target_id)
            
            if user_is_new_checkin:
                if user_broke_streak:
                    if user_freeze_used > 0:
                        footer_text = f"你在第 {user_broke_streak_on} 天打破了簽到紀錄，消耗了 {user_freeze_used} 個簽到凍結！重新開始簽到！ | 簽到第 {user_checkin_streak} 天！"
                    else:
                        footer_text = f"你在第 {user_broke_streak_on} 天打破了簽到紀錄，重新開始簽到！ | 簽到第 {user_checkin_streak} 天！"
                elif user_freeze_used > 0:
                    footer_text = f"簽到第 {user_checkin_streak} 天！簽到凍結保護了連續（消耗 {user_freeze_used} 個）"
                else:
                    footer_text = f"簽到第 {user_checkin_streak} 天！"
            else:
                footer_text = None
            if side_effect_message:
                footer_text = (footer_text + " | " + side_effect_message) if footer_text else side_effect_message

            # 建立 Embed 訊息
            embed = discord.Embed(title=f"{interaction.user.display_name} 幫 {target_user.display_name} 測量長度：", color=0x00ff00)
            embed.add_field(name="1 cm", value=f"8D", inline=False)
            embed.set_footer(text=footer_text)
            embed.timestamp = datetime.now(timezone.utc)
            await interaction.response.send_message(content=f"{target_user.mention} 被抓去量長度。", embed=embed)
            if size == -1:
                embed.set_field_at(0, name="你現在是男娘了！", value="🏳️‍⚧️", inline=False)
                await interaction.edit_original_response(content=f"{target_user.mention} 被抓去量長度。", embed=embed)
            else:
                # animate to size
                break_counter = 0
                cloud_content = f"{target_user.mention} 被抓去量長度。"
                speed = size // 50 + 1
                for i in range(1, size + 1, speed):
                    if random.random() < 0.1:
                        break_counter += 1
                        if break_counter == 1:
                            cloud_content += f"\n{target_user.display_name} 的ㄐㄐ今天好像怪怪的。"
                    d_chars = list("=" * (i - 1))
                    if break_counter > 0 and len(d_chars) > 0:
                        num_replace = min(break_counter, len(d_chars))
                        for idx in random.sample(range(len(d_chars)), num_replace):
                            d_chars[idx] = "≈"
                    d_string = "".join(d_chars)
                    if break_counter >= 5:
                        size = -1
                        final_size = -1
                        cloud_content = f"{target_user.mention} 被抓去量長度。\n{target_user.display_name} 變成男娘了。"
                        set_user_data(guild_key, target_id, "last_dsize_size", -1)
                        # embed.set_field_at(0, name="斷掉了！男娘了！", value="🏳️‍⚧️", inline=False)
                        embed.color = 0xff0000
                        await interaction.edit_original_response(content=cloud_content, embed=embed)
                        break
                    current_size = i
                    embed.set_field_at(0, name=f"{current_size} cm", value=f"8{d_string}D", inline=False)
                    await interaction.edit_original_response(content=cloud_content, embed=embed)
                    await asyncio.sleep(0.1)
                if break_counter < 5:
                    # final
                    d_string = "=" * (size - 1)
                    embed.set_field_at(0, name=f"{final_size} cm", value=f"8{d_string}D", inline=False)
                    await interaction.edit_original_response(content=cloud_content, embed=embed)
            history = get_user_data(guild_key, target_id, "dsize_history", [])
            history.append({
                "date": now.isoformat(),
                "size": final_size,
                "type": "雲端尺"
            })
            if len(history) > 100:
                history = history[-100:]
            set_user_data(guild_key, target_id, "dsize_history", history)
            # Handle check-in rewards if applicable (milestone days only)
            claimed_unsuccessful = get_user_data(0, target_id, "claim_reward_unsuccessful", False)
            if user_is_new_checkin:
                await handle_checkin_rewards(interaction, target_user, user_checkin_streak, guild_key)
                log(f"簽到成功，連續 {user_checkin_streak} 天", module_name="dsize", user=target_user, guild=interaction.guild)
            elif claimed_unsuccessful:
                await handle_checkin_rewards(interaction, target_user, user_checkin_streak, guild_key)
                set_user_data(0, target_id, "claim_reward_unsuccessful", False)
                log(f"簽到成功，連續 {user_checkin_streak} 天 (補發獎勵)", module_name="dsize", user=target_user, guild=interaction.guild)
    await interaction.response.send_modal(SelectUserModal())

async def use_viagra(interaction: discord.Interaction):
    user_id = interaction.user.id
    guild_key = interaction.guild_id if interaction.guild else None
    now = datetime.now(timezone(timedelta(hours=8))).date()
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
        last = datetime(1970, 1, 1).date()
    if last < now:
        await interaction.response.send_message("你今天還沒有量過屌長，無法使用威而鋼。", ephemeral=True)
        return
    elif last > now:
        await interaction.response.send_message("你已經使用過威而鋼了。", ephemeral=True)
        return
    removed = await ItemSystem.remove_item_from_user(guild_key, user_id, "viagra", 1)
    if not removed:
        await interaction.response.send_message("你沒有威而鋼，無法使用。", ephemeral=True)
        return
    # update user statistics
    statistics = get_user_data(0, user_id, "dsize_statistics", {})
    statistics["total_viagra_used"] = statistics.get("total_viagra_used", 0) + 1
    set_user_data(0, user_id, "dsize_statistics", statistics)
    set_user_data(guild_key, user_id, "last_dsize", now + timedelta(days=1))
    # check anti surgery
    last_anti_surgery = get_user_data(guild_key, user_id, "dsize_anti_surgery")
    if last_anti_surgery is not None and not isinstance(last_anti_surgery, datetime):
        try:
            last_anti_surgery = datetime.fromisoformat(str(last_anti_surgery)).date()
        except Exception:
            last_anti_surgery = None
    elif isinstance(last_anti_surgery, datetime):
        last_anti_surgery = last_anti_surgery.date()
    if last_anti_surgery == now:
        set_user_data(guild_key, user_id, "dsize_anti_surgery", (now + timedelta(days=1)).isoformat())
    # check fake size
    fake_ruler_used_date = get_user_data(guild_key, user_id, "dsize_fake_ruler_used_date")
    if fake_ruler_used_date is not None and not isinstance(fake_ruler_used_date, datetime):
        try:
            fake_ruler_used_date = datetime.fromisoformat(str(fake_ruler_used_date)).date()
        except Exception:
            fake_ruler_used_date = None
    elif isinstance(fake_ruler_used_date, datetime):
        fake_ruler_used_date = fake_ruler_used_date.date()
    if fake_ruler_used_date == now:
        set_user_data(guild_key, user_id, "dsize_fake_ruler_used_date", (now + timedelta(days=1)).isoformat())
    await interaction.response.send_message("你使用了威而鋼！\n今天的狀態將會持續到明天，無論是好是壞。")
    # print(f"[DSize] {interaction.user} used viagra in guild {guild_key}")
    log(f"{interaction.user} 使用了威而鋼", module_name="dsize", user=interaction.user, guild=interaction.guild)

async def use_random_attack(interaction: discord.Interaction):
    await interaction.response.defer()
    guild_key = interaction.guild_id if interaction.guild else None
    removed = await ItemSystem.remove_item_from_user(guild_key, interaction.user.id, "random_attack", 1)
    if not removed:
        await interaction.followup.send("你沒有亂槍打鳥，無法使用。")
        return
    leaderboard = []
    today = (datetime.now(timezone(timedelta(hours=8)))).date()  # 台灣時間
    next_day = today + timedelta(days=1)  # for viagra check
    valid_user_ids = (set(get_all_user_data(guild_key, "last_dsize", value=str(today)).keys()) | \
                     set(get_all_user_data(guild_key, "last_dsize", value=str(next_day)).keys()))
    for user_id in valid_user_ids:
        size = get_user_data(guild_key, user_id, "last_dsize_size")
        if size is not None and size != -1:
            leaderboard.append((user_id, size))

    target = random.choice(leaderboard) if leaderboard else None
    if target is None:
        await interaction.followup.send("目前沒有可攻擊的目標。")
        await ItemSystem.give_item_to_user(guild_key, interaction.user.id, "random_attack", 1)  # refund
        return
    # perform the attack on the target
    reduced_size = random.randint(1, target[1] // 2 + 1)
    if reduced_size >= target[1]:
        await interaction.followup.send("被抽到的目標已經短到不能再短了。\n攻擊失敗，沒有造成任何傷害。")
        await ItemSystem.give_item_to_user(guild_key, interaction.user.id, "random_attack", 1)  # refund
        return
    set_user_data(guild_key, target[0], "last_dsize_size", target[1] - reduced_size)
    # update statistics
    statistics = get_user_data(0, target[0], "dsize_statistics", {})
    statistics["total_random_attacks"] = statistics.get("total_random_attacks", 0) + 1
    set_user_data(0, target[0], "dsize_statistics", statistics)
    attacker_statistics = get_user_data(0, interaction.user.id, "dsize_statistics", {})
    attacker_statistics["total_performed_random_attacks"] = attacker_statistics.get("total_performed_random_attacks", 0) + 1
    set_user_data(0, interaction.user.id, "dsize_statistics", attacker_statistics)
    target_user = bot.get_user(target[0]) or await bot.fetch_user(target[0])
    if target_user.id == interaction.user.id:
        await interaction.followup.send(f"# {interaction.user.mention} 亂槍打鳥打到自己啦！\n自殘造成了 {reduced_size} cm 的傷害！\n你的屌長從 {target[1]} cm 變成了 {target[1] - reduced_size} cm！", allowed_mentions=discord.AllowedMentions.none())
        log(f"{interaction.user} used random attack on themselves, reduced size: {reduced_size} cm", module_name="dsize", user=interaction.user, guild=interaction.guild)
    else:
        await interaction.followup.send(f"你使用了亂槍打鳥，對 {target_user.display_name}({target_user.name}) 造成了 {reduced_size} cm 的傷害！\n{target_user.display_name} 的屌長從 {target[1]} cm 變成了 {target[1] - reduced_size} cm！", allowed_mentions=discord.AllowedMentions.none())
        log(f"{interaction.user} used random attack on {target_user.display_name}({target_user.name})({target_user.id}), reduced size: {reduced_size} cm", module_name="dsize", user=interaction.user, guild=interaction.guild)
        try:
            await target_user.send(f"你被 {interaction.user.display_name}({interaction.user.name}) 使用了亂槍打鳥，造成了 {reduced_size} cm 的傷害！\n你的屌長從 {target[1]} cm 變成了 {target[1] - reduced_size} cm！\n-# {'伺服器：' + interaction.guild.name if guild_key else '全域 dsize'}", allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            pass  # user has DMs disabled

if "ItemSystem" in modules:
    items = [
        {
            "id": "fake_ruler",
            "name": "自欺欺人尺",
            "description": "使用後下次量長度時或許會更長？",
            "callback": use_fake_ruler,
            "worth": 50,
        },
        {
            "id": "grass",
            "name": "草",
            "description": "這是一把草，可以用來草飼男娘。使用 `/dsize-feedgrass`。",
            "callback": None,
            "worth": 5,
        },
        {
            "id": "scalpel",
            "name": "手術刀",
            "description": "這是一把手術刀，可以用來進行手術，必定成功。",
            "callback": use_scalpel,
            "worth": 250,
        },
        {
            "id": "rusty_scalpel",
            "name": "生鏽的手術刀",
            "description": "這是一把生鏽的手術刀，可以強制感染進而變成男娘。",
            "callback": use_rusty_scalpel,
            "worth": 300,
        },
        {
            "id": "anti_surgery",
            "name": "抗手術藥物",
            "description": "一顆屌型的藥丸。使用後可以防止一天被手術。\n使用後兩天內量長度時將會有變短的副作用。",
            "callback": use_anti_surgery,
            "worth": 30,
        },
        {
            "id": "cloud_ruler",
            "name": "雲端尺",
            "description": "這是一把雲端尺，可以幫處於線上的網友量長度。",
            "callback": use_cloud_ruler,
            "worth": 200,
        },
        {
            "id": "checkin_freeze",
            "name": "凍結球",
            "description": "一個神奇的凍結球，可以使其變成急凍鳥。\n可以抵消一天未簽到，保護你的簽到連續紀錄不被打破。",
            "callback": None,
            "worth": 50,
        },
        {
            "id": "viagra",
            "name": "威而鋼",
            "description": "一顆藍色的藥丸，有持久的作用。\n使用後今天的狀態將會持續到明天，無論是好是壞。",
            "callback": use_viagra,
            "worth": 100,
        },
        {
            "id": "random_attack",
            "name": "[技能] 亂槍打鳥",
            "description": "隨機對一個在排行榜上的非男娘人物造成傷害。",
            "callback": use_random_attack,
            "worth": 500,
        }
    ]
    import ItemSystem
    ItemSystem.items.extend(items)


if __name__ == "__main__":
    start_bot()
