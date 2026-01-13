import discord
from globalenv import bot, start_bot, set_server_config, get_server_config, config, get_command_mention
from discord.ext import commands
from discord import app_commands
import asyncio
import random
import json
import io
import aiohttp
from logger import log
import logging
import re


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
@app_commands.default_permissions(manage_guild=True)
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
            app_commands.Choice(name="正規表達式", value="regex"),
        ],
        reply=[
            app_commands.Choice(name="是", value="True"),
            app_commands.Choice(name="否", value="False"),
        ],
        channel_mode=[
            app_commands.Choice(name="所有頻道", value="all"),
            app_commands.Choice(name="白名單", value="whitelist"),
            app_commands.Choice(name="黑名單", value="blacklist"),
        ]
    )
    @app_commands.default_permissions(manage_guild=True)
    async def add_autoreply(self, interaction: discord.Interaction, mode: str, trigger: str, response: str, reply: str = "False", channel_mode: str = "all", channels: str = "", random_chance: int = 100):
        guild_id = interaction.guild.id
        reply = (reply == "True")
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
        trigger_str = ", ".join(trigger)
        trigger_str = trigger_str if len(trigger_str) <= 100 else trigger_str[:97] + "..."
        response_str = ", ".join(response)
        response_str = response_str if len(response_str) <= 100 else response_str[:97] + "..."
        embed = discord.Embed(title="新增自動回覆成功", color=0x00ff00)
        embed.add_field(name="模式", value=mode)
        embed.add_field(name="觸發字串", value=f"`{trigger_str}`")
        embed.add_field(name="回覆內容", value=f"`{response_str}`")
        embed.add_field(name="回覆原訊息", value="是" if reply else "否")
        embed.add_field(name="指定頻道模式", value=channel_mode)
        embed.add_field(name="指定頻道", value=f"`{', '.join(map(str, valid_channels)) if valid_channels else '無'}`")
        embed.add_field(name="隨機回覆機率", value=f"{random_chance}%")
        await interaction.response.send_message(embed=embed)
        trigger_str = ", ".join(trigger)
        log(f"自動回覆被新增：`{trigger_str[:10]}{'...' if len(trigger_str) > 10 else ''}`。", module_name="AutoReply", level=logging.INFO, user=interaction.user, guild=interaction.guild)

    @app_commands.command(name="remove", description="移除自動回覆")
    @app_commands.describe(
        trigger="觸發字串"
    )
    @app_commands.autocomplete(trigger=list_autoreply_autocomplete)
    @app_commands.default_permissions(manage_guild=True)
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
    @app_commands.default_permissions(manage_guild=True)
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
            triggers = triggers if len(triggers) <= 100 else triggers[:97] + "..."
            responses = responses if len(responses) <= 100 else responses[:97] + "..."
            description += f"**{i}.** 模式：{ar['mode']}，觸發字串：`{triggers}`，回覆內容：`{responses}`，回覆原訊息：{'是' if ar['reply'] else '否'}，指定頻道模式：{ar['channel_mode']}，指定頻道：`{', '.join(map(str, ar['channels'])) if ar['channels'] else '無'}`，隨機回覆機率：{ar['random_chance']}%\n"
        embed = discord.Embed(title="自動回覆列表", description=description, color=0x00ff00)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="clear", description="清除所有自動回覆")
    @app_commands.default_permissions(manage_guild=True)
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
            app_commands.Choice(name="正規表達式", value="regex"),
        ],
        reply=[
            app_commands.Choice(name="是", value="True"),
            app_commands.Choice(name="否", value="False"),
        ],
        channel_mode=[
            app_commands.Choice(name="所有頻道", value="all"),
            app_commands.Choice(name="白名單", value="whitelist"),
            app_commands.Choice(name="黑名單", value="blacklist"),
        ]
    )
    @app_commands.autocomplete(trigger=list_autoreply_autocomplete)
    @app_commands.default_permissions(manage_guild=True)
    async def edit_autoreply(self, interaction: discord.Interaction, trigger: str, new_mode: str = None, new_trigger: str = None, new_response: str = None, reply: str = None, channel_mode: str = None, channels: str = None, random_chance: int = None):
        guild_id = interaction.guild.id
        reply = None if reply is None else (True if reply == "True" else False)
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
                trigger_str = ", ".join(ar["trigger"])
                trigger_str = trigger_str if len(trigger_str) <= 100 else trigger_str[:97] + "..."
                response_str = ", ".join(ar["response"])
                response_str = response_str if len(response_str) <= 100 else response_str[:97] + "..."
                embed = discord.Embed(title="編輯自動回覆成功", color=0x00ff00)
                embed.add_field(name="模式", value=ar["mode"])
                embed.add_field(name="觸發字串", value=f"`{trigger_str}`")
                embed.add_field(name="回覆內容", value=f"`{response_str}`")
                embed.add_field(name="回覆原訊息", value="是" if ar["reply"] else "否")
                embed.add_field(name="指定頻道模式", value=ar["channel_mode"])
                embed.add_field(name="指定頻道", value=f"`{', '.join(map(str, ar['channels'])) if ar['channels'] else '無'}`")
                embed.add_field(name="隨機回覆機率", value=f"{ar['random_chance']}%")
                await interaction.response.send_message(embed=embed)
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
    @app_commands.default_permissions(manage_guild=True)
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
                trigger_str = ", ".join(ar["trigger"])
                trigger_str = trigger_str if len(trigger_str) <= 100 else trigger_str[:97] + "..."
                response_str = ", ".join(ar["response"])
                response_str = response_str if len(response_str) <= 100 else response_str[:97] + "..."
                embed = discord.Embed(title="快速新增自動回覆成功", color=0x00ff00)
                embed.add_field(name="模式", value=ar["mode"])
                embed.add_field(name="觸發字串", value=f"`{trigger_str}`")
                embed.add_field(name="回覆內容", value=f"`{response_str}`")
                embed.add_field(name="回覆原訊息", value="是" if ar["reply"] else "否")
                embed.add_field(name="指定頻道模式", value=ar["channel_mode"])
                embed.add_field(name="指定頻道", value=f"`{', '.join(map(str, ar['channels'])) if ar['channels'] else '無'}`")
                embed.add_field(name="隨機回覆機率", value=f"{ar['random_chance']}%")
                await interaction.response.send_message(embed=embed)
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
            app_commands.Choice(name="是", value="True"),
            app_commands.Choice(name="否", value="False")
        ]
    )
    @app_commands.default_permissions(administrator=True)
    async def import_autoreplies(self, interaction: discord.Interaction, file: discord.Attachment, merge: str = "False"):
        merge = (merge == "True")
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
    
    @app_commands.command(name="test", description="測試自動回覆內容的變數替換")
    @app_commands.describe(response="要測試的回覆內容")
    @app_commands.default_permissions(manage_guild=True)
    async def test_autoreply_response(self, interaction: discord.Interaction, response: str):
        guild = interaction.guild
        author = interaction.user
        channel = interaction.channel

        # 建立一個模擬的訊息物件
        class MockMessage:
            def __init__(self, guild, author, channel, content):
                self.guild = guild
                self.author = author
                self.channel = channel
                self.content = content

        mock_message = MockMessage(guild, author, channel, "這是一則測試訊息內容。")

        final_response = await self._process_response(response, mock_message)
        await interaction.response.send_message(f"測試結果：\n{final_response}")
    
    @app_commands.command(name="help", description="顯示自動回覆的使用說明")
    async def autoreply_help(self, interaction: discord.Interaction):
        # vibe coding is fun lol
        await interaction.response.defer()
        embed = discord.Embed(
            title="自動回覆使用說明",
            description="您可以使用以下設定，讓回覆更加靈活。",
            color=0x00FF00,
        )
        
        embed.add_field(
            name="指令說明",
            value=(
                f"使用 {await get_command_mention('autoreply', 'add')} 指令新增自動回覆。\n"
                f"使用 {await get_command_mention('autoreply', 'quickadd')} 指令可以快速新增自動回覆到一個現有的自動回覆裡。\n"
                f"使用 {await get_command_mention('autoreply', 'list')} 指令可以列出目前所有的自動回覆。\n"
                f"使用 {await get_command_mention('autoreply', 'remove')} 指令可以移除指定的自動回覆。\n"
                f"使用 {await get_command_mention('autoreply', 'edit')} 指令可以編輯指定的自動回覆。\n"
                f"使用 {await get_command_mention('autoreply', 'clear')} 指令可以清除所有自動回覆。\n"
                f"使用 {await get_command_mention('autoreply', 'export')} 指令可以匯出自動回覆設定為 JSON 檔案。\n"
                f"使用 {await get_command_mention('autoreply', 'import')} 指令可以從 JSON 檔案匯入自動回覆設定。\n"
                f"使用 {await get_command_mention('autoreply', 'test')} 指令可以測試自動回覆內容的變數替換效果。"
            ),
            inline=False,
        )

        embed.add_field(
            name="基本變數",
            value=(
                "您可以在自動回覆的回覆內容中使用以下變數，讓回覆更靈活。\n"
                "- `{user}`：提及觸發者\n"
                "- `{content}`：觸發訊息內容\n"
                "- `{guild}` / `{server}`：伺服器名稱\n"
                "- `{channel}`：頻道名稱\n"
                "- `{author}` / `{member}`：觸發者名稱\n"
                "- `{role}`：觸發者最高角色名稱\n"
                "- `{id}`：觸發者 ID\n"
                "- `\\n`：換行\n"
                "- `\\t`：制表符"
            ),
            inline=False,
        )

        embed.add_field(
            name="隨機 / 進階",
            value=(
                "- `{random}`：隨機產生 1 到 100 的整數\n"
                "- `{randint:min-max}`：隨機產生 min~max（例：`{randint:10-50}`）\n"
                "- `{random_user}`：從最近 50 則訊息中隨機選一位非機器人使用者顯示名稱"
            ),
            inline=False,
        )

        embed.add_field(
            name="快速範例",
            value=(
                "- `你好 {user}，你剛剛說：{content}`\n"
                "- `今天的幸運數字是 {randint:1-99}`"
            ),
            inline=False,
        )

        class HelpView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=60)
            
            async def on_timeout(self):
                for child in self.children:
                    child.disabled = True
                await interaction.edit_original_response(view=self)
                self.stop()

            @discord.ui.button(label="顯示更多範例", style=discord.ButtonStyle.primary)
            async def examples(self, i: discord.Interaction, _: discord.ui.Button):
                ex = discord.Embed(title="自動回覆範例", color=0x00FF00)
                ex.description = (
                    "1) `歡迎 {user} 來到 {guild}！`\n"
                    "2) `你在 #{channel} 發了：{content}`\n"
                    "3) `抽獎號碼：{randint:1000-9999}`\n"
                    "4) `剛剛聊天室隨機點名：{random_user}`"
                )
                await i.response.send_message(embed=ex, ephemeral=True)

            @discord.ui.button(label="提示：測試替換", style=discord.ButtonStyle.secondary)
            async def hint(self, i: discord.Interaction, _: discord.ui.Button):
                await i.response.send_message(f"可用 {await get_command_mention('autoreply', 'test')} 測試變數替換結果。", ephemeral=True)

        await interaction.followup.send(embed=embed, view=HelpView())

    async def _process_response(self, response: str, message: discord.Message) -> str:
        """處理回覆內容中的變數替換"""
        guild = message.guild
        author = message.author
        channel = message.channel

        # 基本變數替換
        replacements = {
            "{user}": author.mention,
            "{content}": message.content,
            "{guild}": guild.name,
            "{server}": guild.name,
            "{channel}": channel.name,
            "{author}": author.name,
            "{member}": author.name,
            "{role}": author.top_role.name,
            "{id}": str(author.id),
            "\\n": "\n",
            "\\t": "\t"
        }
        
        for key, value in replacements.items():
            response = response.replace(key, value)

        # {random}
        if "{random}" in response:
            response = response.replace("{random}", str(random.randint(1, 100)))

        # {randint:min-max}
        # 使用 regex 尋找所有 {randint:min-max} 格式
        # 非貪婪匹配，並捕捉 min 和 max
        randint_pattern = re.compile(r"\{randint:(\d+)-(\d+)\}")
        
        def randint_replacer(match):
            try:
                min_val = int(match.group(1))
                max_val = int(match.group(2))
                if min_val > max_val:
                    min_val, max_val = max_val, min_val
                return str(random.randint(min_val, max_val))
            except (ValueError, IndexError):
                return match.group(0) # 發生錯誤則不替換

        response = randint_pattern.sub(randint_replacer, response)

        # {random_user}
        if "{random_user}" in response:
            try:
                users = set()
                # 限制讀取歷史訊息數量以避免效能問題
                async for msg in channel.history(limit=50):
                     if not msg.author.bot:
                        users.add(msg.author)
                
                if users:
                    selected_user = random.choice(list(users))
                    response = response.replace("{random_user}", selected_user.display_name)
                else:
                    response = response.replace("{random_user}", "沒有人")
            except Exception as e:
                log(f"處理 {{random_user}} 時發生錯誤: {e}", module_name="AutoReply", level=logging.ERROR)
                response = response.replace("{random_user}", "未知使用者")

        return response

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        
        # check permissions
        if not message.channel.permissions_for(message.guild.me).send_messages:
            return

        guild_id = message.guild.id
        autoreplies = get_server_config(guild_id, "autoreplies", [])
        
        # 預先取得 channel_id 避免在迴圈中重複存取
        channel_id = message.channel.id
        content = message.content

        for ar in autoreplies:
            # check channel mode
            channel_mode = ar.get("channel_mode", "all")
            # 確保 ar['channels'] 存在，避免 KeyError
            channels = ar.get("channels", [])
            
            if channel_mode == "whitelist" and channel_id not in channels:
                continue
            elif channel_mode == "blacklist" and channel_id in channels:
                continue

            match_found = False
            mode = ar.get("mode")
            triggers = ar.get("trigger", [])
            
            # 優化：根據模式選擇匹配邏輯
            if mode == "regex":
                for trigger in triggers:
                    try:
                        if re.search(trigger, content):
                            match_found = True
                            break
                    except re.error:
                        continue
            else:
                 # 對於字串比對，可以使用 any 提早結束
                if mode == "contains":
                    match_found = any(trigger in content for trigger in triggers)
                elif mode == "equals":
                    match_found = any(trigger == content for trigger in triggers)
                elif mode == "starts_with":
                    match_found = any(content.startswith(trigger) for trigger in triggers)
                elif mode == "ends_with":
                    match_found = any(content.endswith(trigger) for trigger in triggers)
            
            if match_found:
                if not percent_random(ar.get("random_chance", 100)):
                    # 雖然匹配但隨機機率未中，繼續檢查下一個設定嗎？
                    # 原始邏輯是 return，表示同一個訊息只會有一次自動回覆機會(或該次判定結束)
                    # 依照原始邏輯保留 return
                    return

                responses = ar.get("response", [])
                if not responses:
                    return

                raw_response = random.choice(responses)
                
                # 使用新的處理方法
                final_response = await self._process_response(raw_response, message)
                
                try:
                    if ar.get("reply", False):
                        await message.reply(final_response)
                    else:
                        await message.channel.send(final_response)
                    
                    # 記錄日誌
                    # 避免 trigger 太長
                    trigger_used = triggers[0] if triggers else "unknown" 
                    log(f"自動回覆觸發：`{trigger_used[:10]}...` 回覆內容：`{final_response[:10]}...`。", 
                        module_name="AutoReply", level=logging.INFO, user=message.author, guild=message.guild)
                except discord.HTTPException as e:
                    log(f"自動回覆發送失敗: {e}", module_name="AutoReply", level=logging.ERROR)
                
                return


asyncio.run(bot.add_cog(AutoReply(bot)))

if __name__ == "__main__":
    start_bot()
