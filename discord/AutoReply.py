import discord
from globalenv import bot, start_bot, set_server_config, get_server_config, config
from discord.ext import commands
from discord import app_commands
import asyncio
import random
import json
import io
import aiohttp
from logger import log
import logging


def percent_random(percent: int) -> bool:
    if percent == 100:
        return True
    try:
        percent = int(percent)
        if percent <= 0:
            return False
        return random.random() < percent / 100
    except Exception:
        return False


async def list_autoreply_autocomplete(interaction: discord.Interaction, current: str):
    guild_id = interaction.guild.id
    autoreplies = get_server_config(guild_id, "autoreplies", [])
    choices = []
    for ar in autoreplies:
        text = ", ".join(ar["trigger"])
        text = text if len(text) <= 100 else text[:97] + "..."
        if current.lower() in text.lower():
            choices.append(app_commands.Choice(name=text, value=text))
    return choices[:25]  # Discord 限制最多 25 個選項


@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
class AutoReply(commands.GroupCog, name="autoreply"):
    """自動回覆設定指令群組"""

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="add", description="新增自動回覆")
    @app_commands.describe(
        mode="回覆模式",
        trigger="觸發字串 (使用 , 分隔多個觸發字串)",
        response="回覆內容 (使用 , 分隔多個回覆，隨機選擇一個回覆)",
        reply="回覆原訊息",
        channel_mode="指定頻道模式",
        channels="指定頻道 ID (使用 , 分隔多個頻道 ID)",
        random_chance="隨機回覆機率 (1-100)"
    )
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="包含", value="contains"),
            app_commands.Choice(name="完全匹配", value="equals"),
            app_commands.Choice(name="開始於", value="starts_with"),
            app_commands.Choice(name="結束於", value="ends_with"),
        ],
        reply=[
            app_commands.Choice(name="是", value=1),
            app_commands.Choice(name="否", value=0),
        ],
        channel_mode=[
            app_commands.Choice(name="所有頻道", value="all"),
            app_commands.Choice(name="白名單", value="whitelist"),
            app_commands.Choice(name="黑名單", value="blacklist"),
        ]
    )
    @app_commands.default_permissions(administrator=True)
    async def add_autoreply(self, interaction: discord.Interaction, mode: str, trigger: str, response: str, reply: int = 0, channel_mode: str = "all", channels: str = "", random_chance: int = 100):
        guild_id = interaction.guild.id
        reply = bool(reply)
        if random_chance < 1 or random_chance > 100:
            await interaction.response.send_message("隨機回覆機率必須在 1 到 100 之間。", ephemeral=True)
            return
        autoreplies = get_server_config(guild_id, "autoreplies", [])
        trigger = trigger.split(",")  # multiple triggers
        trigger = [t.strip() for t in trigger if t.strip()]  # remove empty triggers
        response = response.split(",")  # random response
        response = [r.strip() for r in response if r.strip()]  # remove empty responses
        channels = channels.split(",") if channels else []
        channels = [int(c.strip()) for c in channels if c.strip().isdigit()]
        # verify channels exist in guild
        valid_channels = []
        for c in channels:
            if interaction.guild.get_channel(c):
                valid_channels.append(c)
        autoreplies.append({"trigger": trigger, "response": response, "mode": mode, "reply": reply, "channel_mode": channel_mode, "channels": valid_channels, "random_chance": random_chance})
        set_server_config(guild_id, "autoreplies", autoreplies)
        await interaction.response.send_message(f"已新增自動回覆：\n- 模式：{mode}\n- 觸發字串：`{', '.join(trigger)}`\n- 回覆內容：`{', '.join(response)}`\n- 回覆原訊息：{'是' if reply else '否'}\n- 指定頻道模式：{channel_mode}\n- 指定頻道：`{', '.join(map(str, valid_channels)) if valid_channels else '無'}`\n- 隨機回覆機率：{random_chance}%")
        trigger_str = ", ".join(trigger)
        log(f"自動回覆被新增：`{trigger_str[:10]}{'...' if len(trigger_str) > 10 else ''}`。", module_name="AutoReply", level=logging.INFO, user=interaction.user, guild=interaction.guild)

    @app_commands.command(name="remove", description="移除自動回覆")
    @app_commands.describe(
        trigger="觸發字串"
    )
    @app_commands.autocomplete(trigger=list_autoreply_autocomplete)
    @app_commands.default_permissions(administrator=True)
    async def remove_autoreply(self, interaction: discord.Interaction, trigger: str):
        guild_id = interaction.guild.id
        autoreplies = get_server_config(guild_id, "autoreplies", [])
        for ar in autoreplies:
            det = ", ".join(ar["trigger"])
            if det == trigger:
                autoreplies.remove(ar)
                set_server_config(guild_id, "autoreplies", autoreplies)
                await interaction.response.send_message(f"已移除自動回覆：`{trigger}`。")
                log(f"自動回覆被移除：`{trigger[:10]}{'...' if len(trigger) > 10 else ''}`。", module_name="AutoReply", level=logging.INFO, user=interaction.user, guild=interaction.guild)
                return
        await interaction.response.send_message(f"找不到觸發字串 `{trigger}` 的自動回覆。")
    
    @app_commands.command(name="list", description="列出所有自動回覆")
    @app_commands.default_permissions(administrator=True)
    async def list_autoreplies(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        autoreplies = get_server_config(guild_id, "autoreplies", [])
        if not autoreplies:
            await interaction.response.send_message("目前沒有設定任何自動回覆。")
            return
        description = ""
        for i, ar in enumerate(autoreplies, start=1):
            triggers = ", ".join(ar["trigger"])
            triggers = triggers if len(triggers) <= 100 else triggers[:97] + "..."
            responses = ", ".join(ar["response"])
            responses = responses if len(responses) <= 100 else responses[:97] + "..."
            # fix old data without reply and channel_mode and channels
            ar.setdefault("reply", False)
            ar.setdefault("channel_mode", "all")
            ar.setdefault("channels", [])
            ar.setdefault("random_chance", 100)
            description += f"**{i}.** 模式：{ar['mode']}，觸發字串：`{triggers}`，回覆內容：`{responses}`，回覆原訊息：{'是' if ar['reply'] else '否'}，指定頻道模式：{ar['channel_mode']}，指定頻道：`{', '.join(map(str, ar['channels'])) if ar['channels'] else '無'}`，隨機回覆機率：{ar['random_chance']}%\n"
        embed = discord.Embed(title="自動回覆列表", description=description, color=0x00ff00)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="clear", description="清除所有自動回覆")
    @app_commands.default_permissions(administrator=True)
    async def clear_autoreplies(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        autoreplies = get_server_config(guild_id, "autoreplies", [])
        user_id = interaction.user.id
        class Confirm(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=30)
            
            async def on_timeout(self):
                for child in self.children:
                    child.disabled = True
                await interaction.edit_original_response(content="操作逾時，已取消清除自動回覆。", view=self)
                self.stop()

            @discord.ui.button(label="確認清除", style=discord.ButtonStyle.danger)
            async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
                if interaction.user.id != user_id:
                    await interaction.response.send_message("只有發起操作的使用者可以確認清除。", ephemeral=True)
                    return
                set_server_config(guild_id, "autoreplies", [])
                for child in self.children:
                    child.disabled = True
                await interaction.response.edit_message(content="已清除所有自動回覆。", view=self)
                log(f"所有自動回覆被清除。", module_name="AutoReply", level=logging.INFO, user=interaction.user, guild=interaction.guild)
                self.stop()

            @discord.ui.button(label="取消", style=discord.ButtonStyle.secondary)
            async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
                for child in self.children:
                    child.disabled = True
                await interaction.response.edit_message(content="已取消清除自動回覆。", view=self)
                self.stop()

        await interaction.response.send_message(f"您確定要清除所有自動回覆嗎？\n目前有 {len(autoreplies)} 筆自動回覆。", view=Confirm())

    @app_commands.command(name="edit", description="編輯自動回覆")
    @app_commands.describe(
        trigger="觸發字串",
        new_mode="新的回覆模式",
        new_trigger="新的觸發字串",
        new_response="回覆內容",
        reply="是否回覆原訊息",
        channel_mode="指定頻道模式",
        channels="指定頻道 ID (使用 , 分隔多個頻道 ID)",
        random_chance="隨機回覆機率 (1-100)"
    )
    @app_commands.choices(
        new_mode=[
            app_commands.Choice(name="包含", value="contains"),
            app_commands.Choice(name="完全匹配", value="equals"),
            app_commands.Choice(name="開始於", value="starts_with"),
            app_commands.Choice(name="結束於", value="ends_with"),
        ],
        reply=[
            app_commands.Choice(name="是", value=1),
            app_commands.Choice(name="否", value=0),
        ],
        channel_mode=[
            app_commands.Choice(name="所有頻道", value="all"),
            app_commands.Choice(name="白名單", value="whitelist"),
            app_commands.Choice(name="黑名單", value="blacklist"),
        ]
    )
    @app_commands.autocomplete(trigger=list_autoreply_autocomplete)
    @app_commands.default_permissions(administrator=True)
    async def edit_autoreply(self, interaction: discord.Interaction, trigger: str, new_mode: str = None, new_trigger: str = None, new_response: str = None, reply: int = None, channel_mode: str = None, channels: str = None, random_chance: int = None):
        guild_id = interaction.guild.id
        reply = None if reply is None else (True if reply == 1 else False)
        autoreplies = get_server_config(guild_id, "autoreplies", [])
        if random_chance is not None:
            if random_chance < 1 or random_chance > 100:
                await interaction.response.send_message("隨機回覆機率必須在 1 到 100 之間。", ephemeral=True)
                return
        for ar in autoreplies:
            det = ", ".join(ar["trigger"])
            det = det if len(det) <= 100 else det[:97] + "..."
            if det == trigger:
                if new_mode:
                    ar["mode"] = new_mode
                if new_trigger:
                    ar["trigger"] = [t.strip() for t in new_trigger.split(",") if t.strip()]
                if new_response:
                    ar["response"] = [r.strip() for r in new_response.split(",") if r.strip()]
                if reply is not None:
                    ar["reply"] = reply
                if channel_mode:
                    ar["channel_mode"] = channel_mode
                if channels:
                    ar["channels"] = [int(c.strip()) for c in channels.split(",") if c.strip().isdigit()]
                if random_chance is not None:
                    ar["random_chance"] = random_chance
                set_server_config(guild_id, "autoreplies", autoreplies)
                await interaction.response.send_message(f"已編輯自動回覆：\n- 模式：{ar['mode']}\n- 觸發字串：`{', '.join(ar['trigger'])}`\n- 回覆內容：`{', '.join(ar['response'])}`\n- 回覆原訊息：{'是' if ar['reply'] else '否'}\n- 指定頻道模式：{ar['channel_mode']}\n- 指定頻道：`{', '.join(map(str, ar['channels'])) if ar['channels'] else '無'}`\n- 隨機回覆機率：{ar['random_chance']}%")
                log(f"自動回覆被編輯：`{det[:10]}{'...' if len(det) > 10 else ''}`。", module_name="AutoReply", level=logging.INFO, user=interaction.user, guild=interaction.guild)
                return
        await interaction.response.send_message(f"找不到觸發字串 `{trigger}` 的自動回覆。")
    
    @app_commands.command(name="quickadd", description="快速新增自動回覆，合併現有的自動回覆")
    @app_commands.describe(
        trigger="觸發字串",
        new_trigger="新的觸發字串",
        new_response="新的回覆內容"
    )
    @app_commands.autocomplete(trigger=list_autoreply_autocomplete)
    @app_commands.default_permissions(administrator=True)
    async def quick_add_autoreply(self, interaction: discord.Interaction, trigger: str, new_trigger: str = "", new_response: str = ""):
        guild_id = interaction.guild.id
        autoreplies = get_server_config(guild_id, "autoreplies", [])
        for ar in autoreplies:
            det = ", ".join(ar["trigger"])
            det = det if len(det) <= 100 else det[:97] + "..."
            if det == trigger:
                if new_trigger:
                    new_triggers = [t.strip() for t in new_trigger.split(",") if t.strip()]
                    ar["trigger"].extend(new_triggers)
                    ar["trigger"] = list(set(ar["trigger"]))  # remove duplicates
                if new_response:
                    new_responses = [r.strip() for r in new_response.split(",") if r.strip()]
                    ar["response"].extend(new_responses)
                    ar["response"] = list(set(ar["response"]))  # remove duplicates
                set_server_config(guild_id, "autoreplies", autoreplies)
                await interaction.response.send_message(f"已快速新增自動回覆，目前設定：\n- 模式：{ar['mode']}\n- 觸發字串：`{', '.join(ar['trigger'])}`\n- 回覆內容：`{', '.join(ar['response'])}`\n- 回覆原訊息：{'是' if ar['reply'] else '否'}\n- 指定頻道模式：{ar['channel_mode']}\n- 指定頻道：`{', '.join(map(str, ar['channels'])) if ar['channels'] else '無'}`\n- 隨機回覆機率：{ar['random_chance']}%")
                log(f"自動回覆被快速新增：`{det}`。", module_name="AutoReply", level=logging.INFO, user=interaction.user, guild=interaction.guild)
                return
        await interaction.response.send_message(f"找不到觸發字串 `{trigger}` 的自動回覆。")
    
    @app_commands.command(name="export", description="匯出自動回覆設定為 JSON")
    @app_commands.default_permissions(administrator=True)
    async def export_autoreplies(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        autoreplies = get_server_config(guild_id, "autoreplies", [])
        if not autoreplies:
            await interaction.response.send_message("此伺服器尚未設定自動回覆。")
            return
        json_data = json.dumps(autoreplies, ensure_ascii=False, indent=4)
        file = discord.File(io.StringIO(json_data), filename="autoreplies.json")
        await interaction.response.send_message("以下是此伺服器的自動回覆設定 JSON 檔案：", file=file)
        log(f"自動回覆設定被匯出。", module_name="AutoReply", level=logging.INFO, user=interaction.user, guild=interaction.guild)
    
    @app_commands.command(name="import", description="從 JSON 檔案匯入自動回覆設定")
    @app_commands.describe(file="要匯入的 JSON 檔案", merge="是否與現有設定合併")
    @app_commands.choices(
        merge=[
            app_commands.Choice(name="是", value=1),
            app_commands.Choice(name="否", value=0)
        ]
    )
    @app_commands.default_permissions(administrator=True)
    async def import_autoreplies(self, interaction: discord.Interaction, file: discord.Attachment, merge: int = 0):
        merge = bool(merge)
        guild_id = interaction.guild.id
        autoreplies = get_server_config(guild_id, "autoreplies", [])
        # if not autoreplies:
        #     await interaction.response.send_message("此伺服器尚未設定自動回覆。")
        #     return
        await interaction.response.defer()
        # download file content
        async with aiohttp.ClientSession() as session:
            async with session.get(file.url) as resp:
                if resp.status != 200:
                    await interaction.followup.send("無法下載檔案。")
                    return
                json_data = await resp.text()
        try:
            new_autoreplies = json.loads(json_data)
        except json.JSONDecodeError:
            await interaction.followup.send("無法解析 JSON 檔案。")
            return
        if merge:
            autoreplies.extend(new_autoreplies)
        else:
            autoreplies = new_autoreplies
        set_server_config(guild_id, "autoreplies", autoreplies)
        await interaction.followup.send("已匯入自動回覆設定。")
        log(f"自動回覆設定被匯入。", module_name="AutoReply", level=logging.INFO, user=interaction.user, guild=interaction.guild)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        guild = message.guild
        if guild is None:
            return
        guild_id = guild.id
        autoreplies = get_server_config(guild_id, "autoreplies", [])
        for ar in autoreplies:
            for trigger in ar["trigger"]:
                # check channel mode
                channel_mode = ar.get("channel_mode", "all")
                if channel_mode == "whitelist":
                    if message.channel.id not in ar["channels"]:
                        continue
                elif channel_mode == "blacklist":
                    if message.channel.id in ar["channels"]:
                        continue
                match_found = False
                if ar["mode"] == "contains" and trigger in message.content:
                    match_found = True
                elif ar["mode"] == "equals" and trigger == message.content:
                    match_found = True
                elif ar["mode"] == "starts_with" and message.content.startswith(trigger):
                    match_found = True
                elif ar["mode"] == "ends_with" and message.content.endswith(trigger):
                    match_found = True
                if match_found:
                    if not percent_random(ar.get("random_chance", 100)):
                        return
                    response = random.choice(ar["response"])
                    response = response.replace("{user}", message.author.mention)
                    response = response.replace("{content}", message.content)
                    response = response.replace("{guild}", guild.name)
                    if "{random}" in response:
                        random_number = random.randint(1, 100)
                        response = response.replace("{random}", str(random_number))
                    try:
                        if "{randint:" in response:
                            while "{randint:" in response:
                                start_index = response.index("{randint:")
                                end_index = response.index("}", start_index)
                                range_str = response[start_index + 9:end_index]
                                try:
                                    min_val, max_val = map(int, range_str.split("-"))
                                    rand_value = random.randint(min_val, max_val)
                                    response = response[:start_index] + str(rand_value) + response[end_index + 1:]
                                except:
                                    response = response[:start_index] + "0" + response[end_index + 1:]
                    except:
                        pass
                    if "{random_user}" in response:
                        channel = message.channel
                        messages = [msg async for msg in channel.history(limit=50)]
                        users = list(set(msg.author for msg in messages if not msg.author.bot))
                        if users:
                            selected_user = random.choice(users)
                            response = response.replace("{random_user}", selected_user.display_name)
                        else:
                            response = response.replace("{random_user}", "沒有人")
                    if ar.get("reply", False):
                        await message.reply(response)
                    else:
                        await message.channel.send(response)
                    log(f"自動回覆觸發：`{trigger[:10]}{'...' if len(trigger) > 10 else ''}` 回覆內容：`{response[:10]}{'...' if len(response) > 10 else ''}`。", module_name="AutoReply", level=logging.INFO, user=message.author, guild=message.guild)
                    return


asyncio.run(bot.add_cog(AutoReply(bot)))

if __name__ == "__main__":
    start_bot()
