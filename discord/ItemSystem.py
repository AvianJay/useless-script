# Item System for fun
import discord
import asyncio
import logging
from globalenv import bot, start_bot, get_user_data, set_user_data
from discord import app_commands
from discord.ext import commands
from logger import log


# item example:
# {"id": "some_unique_id", "name": "Item Name", "description": "Item Description", "callback": some_function, "additional_data": Any}
items = []
admin_action_callbacks = []  # Economy module hooks into this


def get_item_by_id(item_id: str):
    """Get an item definition by its ID"""
    return next((i for i in items if i["id"] == item_id), None)


async def get_user_items_autocomplete(interaction: discord.Interaction, current: str):
    guild_id = interaction.guild.id if interaction.is_guild_integration() else None
    user_id = interaction.user.id
    user_items = get_user_data(guild_id, user_id, "items", {})
    user_items = {item_id: count for item_id, count in user_items.items() if count > 0}
    choices = [item for item in items if item["id"] in user_items.keys()]
    # id
    # choices.extend([item for item in items if item["id"] in user_items and current.lower() in item["id"].lower()])
    return [app_commands.Choice(name=item["name"], value=item["id"]) for item in choices[:25]]


async def all_items_autocomplete(interaction: discord.Interaction, current: str):
    choices = [item for item in items if current.lower() in item["name"].lower()]
    # id
    # choices.extend([item for item in items if current.lower() in item["id"].lower()])
    return [app_commands.Choice(name=item["name"], value=item["id"]) for item in choices[:25]]


async def get_user_global_items_autocomplete(interaction: discord.Interaction, current: str):
    """全域物品自動完成"""
    user_id = interaction.user.id
    user_items = get_user_data(0, user_id, "items", {})
    user_items = {item_id: count for item_id, count in user_items.items() if count > 0}
    choices = [item for item in items if item["id"] in user_items.keys()]
    if current:
        choices = [item for item in choices if current.lower() in item["name"].lower()]
    return [app_commands.Choice(name=f"{item['name']} x{user_items[item['id']]}", value=item["id"]) for item in choices[:25]]


async def get_user_items_scoped_autocomplete(interaction: discord.Interaction, current: str):
    """根據 scope 參數自動完成物品"""
    scope = getattr(interaction.namespace, 'scope', None)
    if scope == 'global':
        guild_id = 0
    elif scope == 'server':
        guild_id = interaction.guild.id if interaction.is_guild_integration() else 0
    else:
        guild_id = interaction.guild.id if interaction.is_guild_integration() else 0
    user_id = interaction.user.id
    user_items = get_user_data(guild_id, user_id, "items", {})
    user_items = {item_id: count for item_id, count in user_items.items() if count > 0}
    choices = [item for item in items if item["id"] in user_items.keys()]
    if current:
        choices = [item for item in choices if current.lower() in item["name"].lower()]
    scope_label = "🌐" if guild_id == 0 else "🏦"
    return [app_commands.Choice(name=f"{scope_label} {item['name']} x{user_items[item['id']]}", value=item["id"]) for item in choices[:25]]


async def give_item_to_user(guild_id: int, user_id: int, item_id: str, amount: int = 1):
    user_items = get_user_data(guild_id, user_id, "items", {})
    user_items[item_id] = user_items.get(item_id, 0) + amount
    set_user_data(guild_id, user_id, "items", user_items)
    # print(f"[ItemSystem] Gave {amount} of {item_id} to user {user_id} in guild {guild_id}")
    log(f"Gave {amount} of {item_id} to user {user_id} in guild {guild_id}", module_name="ItemSystem")


async def get_user_items(guild_id: int, user_id: int, item_id: str) -> int:
    """返回用戶擁有的指定物品數量"""
    user_items = get_user_data(guild_id, user_id, "items", {})
    return user_items.get(item_id, 0)


async def remove_item_from_user(guild_id: int, user_id: int, item_id: str, amount: int = 1):
    user_items = get_user_data(guild_id, user_id, "items", {})
    original_amount = user_items.get(item_id, 0)
    if original_amount == 0:
        return 0

    removed_amount = min(original_amount, amount)
    user_items[item_id] = max(0, original_amount - amount)
    set_user_data(guild_id, user_id, "items", user_items)

    # print(f"[ItemSystem] Removed {removed_amount} of {item_id} from user {user_id} in guild {guild_id}")
    log(f"Removed {removed_amount} of {item_id} from user {user_id} in guild {guild_id}", module_name="ItemSystem")
    return removed_amount


async def convert_item_list_to_dict():
    all_guild = bot.guilds
    for guild in all_guild:
        guild_id = guild.id
        members = guild.members
        for member in members:
            user_id = member.id
            user_items = get_user_data(guild_id, user_id, "items", None)
            if isinstance(user_items, list):
                user_items_dict = {}
                for item_id in user_items:
                    user_items_dict[item_id] = user_items_dict.get(item_id, 0) + 1
                print(f"Converting items for user {user_id} in guild {guild_id}: {len(user_items)} -> {user_items_dict}")
                set_user_data(guild_id, user_id, "items", user_items_dict)


@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
class ItemSystem(commands.GroupCog, name="item", description="物品系統指令"):
    def __init__(self):
        super().__init__()
    
    @app_commands.command(name="list", description="查看你擁有的物品")
    @app_commands.describe(scope="查看範圍（預設自動偵測）")
    @app_commands.choices(scope=[
        app_commands.Choice(name="伺服器", value="server"),
        app_commands.Choice(name="全域", value="global"),
    ])
    async def list_items(self, interaction: discord.Interaction, scope: str = None):
        user_id = interaction.user.id
        if scope is None:
            scope = "server" if (interaction.guild and interaction.is_guild_integration()) else "global"
        if scope == "global":
            guild_id = 0
            scope_name = "全域"
        else:
            if not interaction.is_guild_integration():
                await interaction.response.send_message("❌ 在私訊中請使用全域範圍。", ephemeral=True)
                return
            guild_id = interaction.guild.id
            scope_name = interaction.guild.name
        user_items = get_user_data(guild_id, user_id, "items", {})
        
        if not user_items or all(v <= 0 for v in user_items.values()):
            await interaction.response.send_message(f"你在 {scope_name} 沒有任何物品。", ephemeral=True)
            return
        embed = discord.Embed(title=f"{interaction.user.display_name} 的物品（{scope_name}）", color=0x00ff00)
        for item_id, amount in user_items.items():
            if amount <= 0:
                continue
            item = next((i for i in items if i["id"] == item_id), None)
            if item:
                worth_text = f"\n💰 價值: {item['worth']}" if item.get("worth", 0) > 0 else ""
                embed.add_field(name=f"{item['name']} x{amount}", value=f"{item['description']}{worth_text}", inline=False)
        embed.set_footer(
            text=scope_name if scope == "global" else (interaction.guild.name if interaction.guild else "未知"),
            icon_url=interaction.guild.icon.url if interaction.guild and interaction.guild.icon else None
        )
        
        await interaction.response.send_message(embed=embed)
    
    @app_commands.command(name="use", description="使用一個物品")
    @app_commands.describe(item_id="你想使用的物品ID", scope="使用範圍（預設自動偵測）")
    @app_commands.autocomplete(item_id=get_user_items_scoped_autocomplete)
    @app_commands.choices(scope=[
        app_commands.Choice(name="伺服器", value="server"),
        app_commands.Choice(name="全域", value="global"),
    ])
    async def use_item(self, interaction: discord.Interaction, item_id: str, scope: str = None):
        user_id = interaction.user.id
        if scope is None:
            scope = "server" if (interaction.guild and interaction.is_guild_integration()) else "global"
        guild_id = 0 if scope == "global" else (interaction.guild.id if interaction.is_guild_integration() else 0)
        user_items = get_user_data(guild_id, user_id, "items", {})
        
        if item_id not in user_items.keys() or user_items[item_id] <= 0:
            await interaction.response.send_message("你沒有這個物品。", ephemeral=True)
            return
        
        item = next((i for i in items if i["id"] == item_id), None)
        if not item:
            await interaction.response.send_message("無效的物品ID。", ephemeral=True)
            return
        
        # Pass scope to callback via interaction attribute
        interaction.guild_id = guild_id
        
        # Call the item's callback function
        if "callback" in item and callable(item["callback"]):
            await item["callback"](interaction)
        else:
            await interaction.response.send_message("這個物品無法使用。", ephemeral=True)
    
    @app_commands.command(name="drop", description="丟棄一個物品")
    @app_commands.describe(item_id="你想丟棄的物品ID", amount="你想丟棄的數量", can_pickup="其他人可以撿起這個物品嗎？", pickup_duration="物品可以被撿起的時間（秒）", pickup_only_once="物品只能被撿起一次嗎？", scope="物品來源範圍（預設自動偵測）")
    @app_commands.autocomplete(item_id=get_user_items_scoped_autocomplete)
    @app_commands.choices(
        can_pickup=[
            app_commands.Choice(name="是", value="True"),
            app_commands.Choice(name="否", value="False")
        ],
        pickup_only_once=[
            app_commands.Choice(name="是", value="True"),
            app_commands.Choice(name="否", value="False")
        ],
        scope=[
            app_commands.Choice(name="伺服器", value="server"),
            app_commands.Choice(name="全域", value="global"),
        ]
    )
    async def drop_item(self, interaction: discord.Interaction, item_id: str, amount: int = 1, can_pickup: str = "True", pickup_duration: int = 60, pickup_only_once: str = "False", scope: str = None):
        if scope is None:
            scope = "server" if (interaction.guild and interaction.is_guild_integration()) else "global"
        can_pickup = (can_pickup == "True")
        pickup_only_once = (pickup_only_once == "True")
        user_id = interaction.user.id
        guild_id = 0 if scope == "global" else (interaction.guild.id if interaction.is_guild_integration() else 0)
        user_item_count = await get_user_items(guild_id, user_id, item_id)

        if user_item_count <= 0:
            await interaction.response.send_message("你沒有這個物品。", ephemeral=True)
            return
        target_item = next((i for i in items if i["id"] == item_id), None)
        
        if can_pickup:
            if pickup_duration <= 0 or pickup_duration > 86400:
                await interaction.response.send_message("錯誤：撿起持續時間必須在 1 到 86400 秒之間。", ephemeral=True)
                return

        amount = await remove_item_from_user(guild_id, user_id, item_id, min(amount, user_item_count))
        remaining_count = amount  # 剩餘可撿起的數量
        picked_up = set()  # user ids who picked up
        # drop to current channel
        class DropView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=pickup_duration)
                self.interaction = interaction
            
            async def on_timeout(self):
                for child in self.children:
                    child.disabled = True
                await self.interaction.edit_original_response(content=f"{self.interaction.user.display_name} 丟棄了 {target_item['name']} x{amount}！\n物品消失了！", view=self)

            @discord.ui.button(label="撿起物品", style=discord.ButtonStyle.green, custom_id="pick_up_item")
            async def pick_up(self, interaction: discord.Interaction, button: discord.ui.Button):
                nonlocal remaining_count
                if pickup_only_once and interaction.user.id in picked_up:
                    await interaction.response.send_message("你已經撿起過這個物品了。\n-# 原物主設定了僅能撿起一次。", ephemeral=True)
                    return
                picked_up.add(interaction.user.id)
                if remaining_count <= 0:
                    await interaction.response.send_message("物品已經被撿光了！", ephemeral=True)
                    return
                user_id = interaction.user.id
                other_user_items = get_user_data(guild_id, user_id, "items", {})
                remaining_count -= 1  # 減少剩餘數量
                other_user_items[item_id] = other_user_items.get(item_id, 0) + 1
                set_user_data(guild_id, user_id, "items", other_user_items)
                log(f"{interaction.user} picked up {target_item['id']} in guild {guild_id}", module_name="ItemSystem")
                await interaction.response.send_message(f"你撿起了 {target_item['name']}。", ephemeral=True)
                if remaining_count <= 0:
                    await self.interaction.edit_original_response(content=f"{self.interaction.user.display_name} 丟棄了 {target_item['name']} x{amount}！\n物品已經被撿光了！", view=None)
                    self.stop()

        if can_pickup:
            await interaction.response.send_message(f"{interaction.user.display_name} 丟棄了 {target_item['name']} x{amount}！", view=DropView())
            # print(f"[ItemSystem] {interaction.user} dropped {target_item['name']} x{amount} in guild {guild_id}")
            log(f"{interaction.user} dropped {target_item['name']} x{amount} in guild {guild_id}", module_name="ItemSystem", user=interaction.user, guild=interaction.guild)
        else:
            await interaction.response.send_message(f"{interaction.user.display_name} 丟棄了 {target_item['name']} x{amount}，但是物品馬上不見了。")
            log(f"{interaction.user} dropped {target_item['name']} x{amount} (no pickup) in guild {guild_id}", module_name="ItemSystem", user=interaction.user, guild=interaction.guild)

    # @app_commands.command(name="to-global", description="將物品從伺服器背包轉移到全域背包")
    # @app_commands.describe(item_id="要轉移的物品", amount="轉移數量")
    # @app_commands.autocomplete(item_id=get_user_items_autocomplete)
    # async def to_global(self, interaction: discord.Interaction, item_id: str, amount: int = 1):
    #     if not interaction.guild:
    #         await interaction.response.send_message("❌ 此功能僅限伺服器使用。", ephemeral=True)
    #         return
    #     if amount <= 0:
    #         await interaction.response.send_message("❌ 數量必須大於 0。", ephemeral=True)
    #         return
    #     guild_id = interaction.guild.id
    #     user_id = interaction.user.id
    #     user_item_count = await get_user_items(guild_id, user_id, item_id)
    #     if user_item_count <= 0:
    #         await interaction.response.send_message("你沒有這個物品。", ephemeral=True)
    #         return
    #     target_item = get_item_by_id(item_id)
    #     if not target_item:
    #         await interaction.response.send_message("無效的物品ID。", ephemeral=True)
    #         return
    #     actual = min(amount, user_item_count)
    #     await remove_item_from_user(guild_id, user_id, item_id, actual)
    #     await give_item_to_user(0, user_id, item_id, actual)
    #     await interaction.response.send_message(
    #         f"✅ 已將 **{target_item['name']}** x{actual} 從伺服器背包轉移到全域背包。"
    #     )
    #     log(f"{interaction.user} transferred {target_item['name']} x{actual} to global in guild {guild_id}",
    #         module_name="ItemSystem", user=interaction.user, guild=interaction.guild)

    # @app_commands.command(name="to-server", description="將物品從全域背包轉移到伺服器背包")
    # @app_commands.describe(item_id="要轉移的物品", amount="轉移數量")
    # @app_commands.autocomplete(item_id=get_user_global_items_autocomplete)
    # async def to_server(self, interaction: discord.Interaction, item_id: str, amount: int = 1):
    #     if not interaction.guild:
    #         await interaction.response.send_message("❌ 此功能僅限伺服器使用。", ephemeral=True)
    #         return
    #     if amount <= 0:
    #         await interaction.response.send_message("❌ 數量必須大於 0。", ephemeral=True)
    #         return
    #     guild_id = interaction.guild.id
    #     user_id = interaction.user.id
    #     global_count = await get_user_items(0, user_id, item_id)
    #     if global_count <= 0:
    #         await interaction.response.send_message("你的全域背包沒有這個物品。", ephemeral=True)
    #         return
    #     target_item = get_item_by_id(item_id)
    #     if not target_item:
    #         await interaction.response.send_message("無效的物品ID。", ephemeral=True)
    #         return
    #     actual = min(amount, global_count)
    #     await remove_item_from_user(0, user_id, item_id, actual)
    #     await give_item_to_user(guild_id, user_id, item_id, actual)
    #     await interaction.response.send_message(
    #         f"✅ 已將 **{target_item['name']}** x{actual} 從全域背包轉移到伺服器背包。"
    #     )
    #     log(f"{interaction.user} transferred {target_item['name']} x{actual} from global in guild {guild_id}",
    #         module_name="ItemSystem", user=interaction.user, guild=interaction.guild)

    @app_commands.command(name="give", description="給予另一個用戶一個物品")
    @app_commands.describe(user="你想給予物品的用戶", item_id="你想給予的物品ID", amount="數量", scope="物品來源範圍（預設自動偵測）")
    @app_commands.autocomplete(item_id=get_user_items_scoped_autocomplete)
    @app_commands.choices(scope=[
        app_commands.Choice(name="伺服器", value="server"),
        app_commands.Choice(name="全域", value="global"),
    ])
    async def give_item(self, interaction: discord.Interaction, user: discord.User, item_id: str, amount: int = 1, scope: str = None):
        await interaction.response.defer()
        if scope is None:
            scope = "server" if (interaction.guild and interaction.is_guild_integration()) else "global"
        giver_id = interaction.user.id
        receiver_id = user.id
        guild_id = 0 if scope == "global" else (interaction.guild.id if interaction.is_guild_integration() else 0)
        
        if giver_id == receiver_id:
            await interaction.followup.send("你不能給自己物品。")
            return

        if user.bot:
            await interaction.followup.send("你不能給機器人物品。")
            return
        
        giver_item_count = await get_user_items(guild_id, giver_id, item_id)
        if giver_item_count <= 0:
            await interaction.followup.send("你沒有這個物品。")
            return
        
        item = next((i for i in items if i["id"] == item_id), None)
        if not item:
            await interaction.followup.send("無效的物品ID。")
            return
        
        # Remove from giver
        removed = await remove_item_from_user(guild_id, giver_id, item_id, amount)
        
        # Add to receiver
        await give_item_to_user(guild_id, receiver_id, item_id, removed)
        
        await interaction.followup.send(f"你給了 {user.display_name}(`{user.name}`) {removed} 個 {item['name']}。")
        # dm the receiver
        try:
            await user.send(f"你從 {interaction.user.display_name}(`{interaction.user.name}`) 那裡收到了 {amount} 個 {item['name']}！\n-# 伺服器: {interaction.guild.name if interaction.is_guild_integration() else '私人訊息'}")
        except Exception:
            pass

asyncio.run(bot.add_cog(ItemSystem()))


# admin cheating
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
class ItemModerate(commands.GroupCog, name="itemmod", description="物品系統管理指令"):
    def __init__(self):
        super().__init__()
    
    @app_commands.command(name="give", description="給予用戶一個物品（可能會影響經濟）")
    @app_commands.describe(user="你想給予物品的用戶", item_id="你想給予的物品ID", amount="你想給予的數量")
    @app_commands.autocomplete(item_id=all_items_autocomplete)
    async def admin_give_item(self, interaction: discord.Interaction, user: discord.User, item_id: str, amount: int = 1):
        await interaction.response.defer()
        
        if user.bot:
            await interaction.followup.send("你不能給機器人物品。")
            return
        
        receiver_id = user.id
        guild_id = interaction.guild.id
        
        item = next((i for i in items if i["id"] == item_id), None)
        if not item:
            await interaction.followup.send("無效的物品ID。")
            return
        
        await give_item_to_user(guild_id, receiver_id, item_id, amount)

        # Notify Economy module about admin injection
        # for callback in admin_action_callbacks:
        #     try:
        #         await callback(guild_id, "give", item_id, amount)
        #     except Exception as e:
        #         log(f"Error in admin action callback: {e}", module_name="ItemSystem", level=logging.ERROR)

        await interaction.followup.send(f"你給了 {user.display_name}(`{user.name}`) {amount} 個 {item['name']}。")

    @app_commands.command(name="remove", description="移除用戶的一個物品")
    @app_commands.describe(user="你想移除物品的用戶", item_id="你想移除的物品ID", amount="你想移除的數量")
    @app_commands.autocomplete(item_id=all_items_autocomplete)
    async def admin_remove_item(self, interaction: discord.Interaction, user: discord.User, item_id: str, amount: int):
        
        if user.bot:
            await interaction.response.send_message("你不能移除機器人物品。", ephemeral=True)
            return

        receiver_id = user.id
        guild_id = interaction.guild.id
        
        removed_count = await remove_item_from_user(guild_id, receiver_id, item_id, amount)
        if removed_count == 0:
            await interaction.response.send_message(f"{user.name} 沒有這個物品。", ephemeral=True)
            return
        
        item = next((i for i in items if i["id"] == item_id), None)
        item_name = item['name'] if item else "未知物品"

        await interaction.response.send_message(f"你移除了 {user.display_name}(`{user.name}`) 的 {removed_count} 個 {item_name}。", ephemeral=True)

    @app_commands.command(name="list", description="列出所有可用的物品")
    async def admin_list_items(self, interaction: discord.Interaction):
        if not items:
            await interaction.response.send_message("目前沒有任何物品。", ephemeral=True)
            return
        
        embed = discord.Embed(title="所有可用的物品", color=0x0000ff)
        for item in items:
            embed.add_field(name=item["name"], value=item["description"], inline=False)
        embed.set_footer(text=interaction.guild.name, icon_url=interaction.guild.icon.url if interaction.guild.icon else None)
        
        await interaction.response.send_message(embed=embed)
    
    @app_commands.command(name="listuser", description="列出用戶擁有的物品")
    @app_commands.describe(user="你想查詢的用戶")
    async def admin_list_user_items(self, interaction: discord.Interaction, user: discord.User):
        if user.bot:
            await interaction.response.send_message("機器人沒有物品。", ephemeral=True)
            return

        guild_id = interaction.guild.id
        scope_name = interaction.guild.name
        user_items = get_user_data(guild_id, user.id, "items", {})
        user_items = {item_id: count for item_id, count in user_items.items() if count > 0}

        if not user_items:
            await interaction.response.send_message(f"{user.name} 在 {scope_name} 目前沒有任何物品。", ephemeral=True)
            return

        embed = discord.Embed(title=f"{user.name} 擁有的物品（{scope_name}）", color=0x00ff00)
        for item_id, amount in user_items.items():
            item = next((i for i in items if i["id"] == item_id), None)
            if item:
                embed.add_field(name=f"{item['name']} x{amount}", value=item["description"], inline=False)
        embed.set_footer(text=interaction.guild.name, icon_url=interaction.guild.icon.url if interaction.guild.icon else None)

        await interaction.response.send_message(embed=embed)

asyncio.run(bot.add_cog(ItemModerate()))


if __name__ == "__main__":
    start_bot()
