# Item System for fun
import discord
import asyncio
import logging
import secrets
from globalenv import (
    bot, start_bot, get_user_data, set_user_data, get_server_config, set_server_config,
    interaction_uses_guild_scope, get_interaction_scope_guild_id,
)
from discord import app_commands
from discord.ext import commands
from logger import log


# item example:
# {"id": "some_unique_id", "name": "Item Name", "description": "Item Description", "callback": some_function, "additional_data": Any}
items = []
admin_action_callbacks = []  # Economy module hooks into this

CUSTOM_ITEMS_KEY = "custom_items"


def _make_custom_text_callback(item_id: str, content: str, remove_after_use: bool = True, ephemeral_response: bool = False, worth: float = 0, revenue_share_user_id: int = None):
    """建立自定義文字物品使用時的回呼函數"""

    async def callback(interaction: discord.Interaction):
        guild_id = getattr(interaction, "guild_id", interaction.guild.id if interaction.guild else 0)
        user_id = interaction.user.id
        
        removed = await remove_item_from_user(guild_id, user_id, item_id, 1)
        if removed <= 0:
            await interaction.response.send_message("你沒有這個物品。", ephemeral=True)
            return
        if remove_after_use:
            # 分潤
            if worth > 0 and revenue_share_user_id:
                # 90%
                from Economy import add_balance, get_balance, get_currency_name, log_transaction, queue_economy_audit_log
                revenue_amount = round(worth * 0.9, 2)
                balance_before = get_balance(guild_id, revenue_share_user_id)
                add_balance(guild_id=guild_id, user_id=revenue_share_user_id, amount=revenue_amount)
                balance_after = get_balance(guild_id, revenue_share_user_id)
                log_transaction(guild_id=guild_id, user_id=revenue_share_user_id, amount=revenue_amount, tx_type="item_sale", currency=get_currency_name(guild_id), detail=f"User {interaction.user.id} used item {item_id} worth {worth}")
                revenue_share_user = interaction.guild.get_member(revenue_share_user_id) if interaction.guild else None
                if revenue_share_user is None:
                    revenue_share_user = bot.get_user(revenue_share_user_id)
                queue_economy_audit_log("item_sale_income", guild_id=guild_id, actor=interaction.user, target=revenue_share_user, interaction=interaction, currency=get_currency_name(guild_id), amount=revenue_amount, target_balance_before=balance_before, target_balance_after=balance_after, item_name=item_id, item_amount=1, detail=f"User {interaction.user.id} used item {item_id} worth {worth}", color=0x2ECC71)
        else:
            # 如果不使用後移除，則補回去
            await give_item_to_user(guild_id, user_id, item_id, 1)
        await interaction.response.send_message(content, ephemeral=ephemeral_response)
        log(f"Custom item {item_id} used by {interaction.user} in guild {guild_id}", module_name="ItemSystem")

    return callback


def get_custom_items(guild_id: int) -> dict:
    """取得伺服器的自定義物品列表。格式：{item_id: {name, description, content}}"""
    return get_server_config(guild_id, CUSTOM_ITEMS_KEY, {})


def set_custom_items(guild_id: int, custom_items: dict):
    """設定伺服器的自定義物品列表"""
    set_server_config(guild_id, CUSTOM_ITEMS_KEY, custom_items)


def get_item_by_id(item_id: str, guild_id: int = None):
    """Get an item definition by its ID. 若提供 guild_id，會一併檢查該伺服器的自定義物品。"""
    # 先檢查全域物品
    item = next((i for i in items if i["id"] == item_id), None)
    if item:
        return item
    # 再檢查伺服器自定義物品
    if guild_id and item_id.startswith("custom_"):
        custom_items = get_custom_items(guild_id)
        if item_id in custom_items:
            data = custom_items[item_id]
            return {
                "id": item_id,
                "name": data["name"],
                "description": data.get("description", "自定義物品。使用時會傳送儲存的文字內容。"),
                "callback": _make_custom_text_callback(
                    item_id,
                    data["content"],
                    remove_after_use=data.get("remove_after_use", True),
                    ephemeral_response=data.get("ephemeral_response", False),
                    worth=float(data.get("worth", 0)) if data.get("worth") is not None else 0,
                    revenue_share_user_id=data.get("revenue_share_user_id"),
                ),
                "worth": float(data.get("worth", 0)) if data.get("worth") is not None else 0,
                "remove_after_use": data.get("remove_after_use", True),
                "ephemeral_response": data.get("ephemeral_response", False),
                "revenue_share_user_id": data.get("revenue_share_user_id"),
            }
    return None


async def custom_items_autocomplete(interaction: discord.Interaction, current: str):
    """itemmod 自定義物品選擇用 autocomplete"""
    guild_id = interaction.guild.id if interaction.guild else 0
    custom_items = get_custom_items(guild_id)
    choices = []
    for item_id, data in custom_items.items():
        if not current or current.lower() in data["name"].lower():
            choices.append(app_commands.Choice(name=data["name"], value=item_id))
    return choices[:25]


def get_all_items_for_guild(guild_id: int = None) -> list:
    """取得所有可用的物品（含該伺服器的自定義物品）。用於 autocomplete 等情境。"""
    result = list(items)
    if guild_id:
        for item_id, data in get_custom_items(guild_id).items():
            result.append({
                "id": item_id,
                "name": data["name"],
                "description": data.get("description", "自定義物品。使用時會傳送儲存的文字內容。"),
                "worth": float(data.get("worth", 0)) if data.get("worth") is not None else 0,
                "remove_after_use": data.get("remove_after_use", True),
                "ephemeral_response": data.get("ephemeral_response", False),
                "revenue_share_user_id": data.get("revenue_share_user_id"),
            })
    return result


async def get_user_items_autocomplete(interaction: discord.Interaction, current: str):
    guild_id = interaction.guild.id if interaction_uses_guild_scope(interaction) else None
    user_id = interaction.user.id
    user_items = get_user_data(guild_id, user_id, "items", {})
    user_items = {item_id: count for item_id, count in user_items.items() if count > 0}
    all_items_list = get_all_items_for_guild(guild_id)
    choices = [item for item in all_items_list if item["id"] in user_items.keys()]
    if current:
        choices = [item for item in choices if current.lower() in item["name"].lower()]
    return [app_commands.Choice(name=item["name"], value=item["id"]) for item in choices[:25]]


async def all_items_autocomplete(interaction: discord.Interaction, current: str):
    guild_id = interaction.guild.id if interaction_uses_guild_scope(interaction) else None
    all_items_list = get_all_items_for_guild(guild_id)
    choices = [item for item in all_items_list if current.lower() in item["name"].lower()]
    return [app_commands.Choice(name=item["name"], value=item["id"]) for item in choices[:25]]


async def get_user_global_items_autocomplete(interaction: discord.Interaction, current: str):
    """全域物品自動完成（不包含伺服器自定義物品）"""
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
        guild_id = get_interaction_scope_guild_id(interaction)
    else:
        guild_id = get_interaction_scope_guild_id(interaction)
    user_id = interaction.user.id
    user_items = get_user_data(guild_id, user_id, "items", {})
    user_items = {item_id: count for item_id, count in user_items.items() if count > 0}
    all_items_list = get_all_items_for_guild(guild_id if guild_id else None)
    choices = [item for item in all_items_list if item["id"] in user_items.keys()]
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
            scope = "server" if interaction_uses_guild_scope(interaction) else "global"
        if scope == "global":
            guild_id = 0
            scope_name = "全域"
        else:
            if not interaction_uses_guild_scope(interaction):
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
            item = get_item_by_id(item_id, guild_id if scope == "server" else None)
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
            scope = "server" if interaction_uses_guild_scope(interaction) else "global"
        guild_id = 0 if scope == "global" else get_interaction_scope_guild_id(interaction)
        user_items = get_user_data(guild_id, user_id, "items", {})
        
        if item_id not in user_items.keys() or user_items[item_id] <= 0:
            await interaction.response.send_message("你沒有這個物品。", ephemeral=True)
            return
        
        item = get_item_by_id(item_id, guild_id)
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
            scope = "server" if interaction_uses_guild_scope(interaction) else "global"
        can_pickup = (can_pickup == "True")
        pickup_only_once = (pickup_only_once == "True")
        user_id = interaction.user.id
        guild_id = 0 if scope == "global" else get_interaction_scope_guild_id(interaction)
        user_item_count = await get_user_items(guild_id, user_id, item_id)

        if user_item_count <= 0:
            await interaction.response.send_message("你沒有這個物品。", ephemeral=True)
            return
        target_item = get_item_by_id(item_id, guild_id if guild_id else None)
        
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
            scope = "server" if interaction_uses_guild_scope(interaction) else "global"
        giver_id = interaction.user.id
        receiver_id = user.id
        guild_id = 0 if scope == "global" else get_interaction_scope_guild_id(interaction)
        
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
        
        item = get_item_by_id(item_id, guild_id if guild_id else None)
        if not item:
            await interaction.followup.send("無效的物品ID。")
            return

        # Remove from giver
        removed = await remove_item_from_user(guild_id, giver_id, item_id, amount)
        
        # Add to receiver
        await give_item_to_user(guild_id, receiver_id, item_id, removed)
        
        await interaction.followup.send(f"你給了 {user.display_name}(`{user.name}`) {removed} 個 {item['name']}。", allowed_mentions=discord.AllowedMentions.none())
        # dm the receiver
        try:
            scope_name = interaction.guild.name if interaction_uses_guild_scope(interaction) and interaction.guild else "私人訊息"
            await user.send(f"你從 {interaction.user.display_name}(`{interaction.user.name}`) 那裡收到了 {amount} 個 {item['name']}！\n-# 伺服器: {scope_name}", allowed_mentions=discord.AllowedMentions.none())
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

        if amount <= 0:
            await interaction.followup.send("數量必須大於 0")
            return

        if not interaction_uses_guild_scope(interaction):
            await interaction.followup.send("伺服器啟用了全域模式，無法使用此指令。")
            return

        if user.bot:
            await interaction.followup.send("你不能給機器人物品。")
            return
        
        receiver_id = user.id
        guild_id = interaction.guild.id
        
        item = get_item_by_id(item_id, interaction.guild.id)
        if not item:
            await interaction.followup.send("無效的物品ID。")
            return

        if item.get("worth", 0) == 0:
            await interaction.followup.send("無法取得此物品。")
            return
        
        await give_item_to_user(guild_id, receiver_id, item_id, amount)

        # Notify Economy module about admin injection
        for callback in admin_action_callbacks:
            try:
                await callback(guild_id, "give", item_id, amount, receiver_id)
            except Exception as e:
                log(f"Error in admin action callback: {e}", module_name="ItemSystem", level=logging.ERROR)

        await interaction.followup.send(f"你給了 {user.display_name}(`{user.name}`) {amount} 個 {item['name']}。", allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name="remove", description="移除用戶的一個物品")
    @app_commands.describe(user="你想移除物品的用戶", item_id="你想移除的物品ID", amount="你想移除的數量")
    @app_commands.autocomplete(item_id=all_items_autocomplete)
    async def admin_remove_item(self, interaction: discord.Interaction, user: discord.User, item_id: str, amount: int):
        if not interaction_uses_guild_scope(interaction):
            await interaction.response.send_message("伺服器啟用了全域模式，無法使用此指令。", ephemeral=True)
            return
        if user.bot:
            await interaction.response.send_message("你不能移除機器人物品。", ephemeral=True)
            return

        receiver_id = user.id
        guild_id = interaction.guild.id
        
        removed_count = await remove_item_from_user(guild_id, receiver_id, item_id, amount)
        if removed_count == 0:
            await interaction.response.send_message(f"{user.name} 沒有這個物品。", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
            return
        
        item = get_item_by_id(item_id, guild_id)
        item_name = item['name'] if item else "未知物品"

        await interaction.response.send_message(f"你移除了 {user.display_name}(`{user.name}`) 的 {removed_count} 個 {item_name}。", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name="list", description="列出所有可用的物品")
    async def admin_list_items(self, interaction: discord.Interaction):
        all_items_list = get_all_items_for_guild(interaction.guild.id)
        if not all_items_list:
            await interaction.response.send_message("目前沒有任何物品。", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
            return

        if not interaction_uses_guild_scope(interaction):
            await interaction.response.send_message("伺服器啟用了全域模式，無法使用此指令。", ephemeral=True)
            return

        embed = discord.Embed(title="所有可用的物品", color=0x0000ff)
        for item in all_items_list:
            custom_tag = " [自定義]" if item["id"].startswith("custom_") else ""
            embed.add_field(name=f"{item['name']}{custom_tag}", value=item["description"], inline=False)
        embed.set_footer(text=interaction.guild.name, icon_url=interaction.guild.icon.url if interaction.guild.icon else None)
        
        await interaction.response.send_message(embed=embed)
    
    @app_commands.command(name="listuser", description="列出用戶擁有的物品")
    @app_commands.describe(user="你想查詢的用戶")
    async def admin_list_user_items(self, interaction: discord.Interaction, user: discord.User):
        if user.bot:
            await interaction.response.send_message("機器人沒有物品。", ephemeral=True)
            return

        if not interaction_uses_guild_scope(interaction):
            await interaction.response.send_message("伺服器啟用了全域模式，無法使用此指令。", ephemeral=True)
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
            item = get_item_by_id(item_id, guild_id)
            if item:
                embed.add_field(name=f"{item['name']} x{amount}", value=item["description"], inline=False)
        embed.set_footer(text=interaction.guild.name, icon_url=interaction.guild.icon.url if interaction.guild.icon else None)

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="addcustom", description="新增伺服器自定義物品")
    @app_commands.describe(
        name="物品名稱",
        content="使用物品時要傳送的文字內容",
        description="物品說明（可選，預設為「自定義物品」）",
        list_in_shop="是否上架伺服器商店",
        price="商店定價（伺服幣，僅在「上架商店」為是時有效）",
        remove_after_use="使用後是否自動移除物品",
        ephemeral_response="是否以隱藏訊息方式回應使用者",
        revenue_share_user="分潤用戶，物品被使用後會獲得 90% 價值",
    )
    async def addcustom(self, interaction: discord.Interaction, name: str, content: str, description: str = None, list_in_shop: bool = False, price: float = None, remove_after_use: bool = True, ephemeral_response: bool = False, revenue_share_user: discord.User = None):
        if not interaction_uses_guild_scope(interaction):
            await interaction.response.send_message("❌ 伺服器啟用了全域模式，無法使用此指令。", ephemeral=True)
            return
        if not name or len(name.strip()) < 1:
            await interaction.response.send_message("物品名稱不能為空。", ephemeral=True)
            return
        if not content or len(content.strip()) < 1:
            await interaction.response.send_message("文字內容不能為空。", ephemeral=True)
            return
        if len(content) > 2000:
            await interaction.response.send_message("文字內容不可超過 2000 字元。", ephemeral=True)
            return
        if len(name) > 100:
            await interaction.response.send_message("物品名稱不可超過 100 字元。", ephemeral=True)
            return
        if list_in_shop:
            if price is None or price <= 0:
                await interaction.response.send_message("上架商店時請設定大於 0 的定價。", ephemeral=True)
                return
            price = round(float(price), 2)
        else:
            price = None
        if revenue_share_user is not None:
            if revenue_share_user.bot:
                await interaction.response.send_message("分潤對象不能是機器人。", ephemeral=True)
                return
            if not remove_after_use:
                await interaction.response.send_message("只有一次性自訂物品才能設定分潤。", ephemeral=True)
                return
            if price is None or price <= 0:
                await interaction.response.send_message("設定分潤的自訂物品必須先上架並有價格，分潤金額會是價格的 90%。", ephemeral=True)
                return

        guild_id = interaction.guild.id
        custom_items = get_custom_items(guild_id)
        item_id = f"custom_{secrets.token_hex(4)}"
        custom_items[item_id] = {
            "name": name.strip()[:100],
            "description": (description or "自定義物品。使用時會傳送儲存的文字內容。")[:500],
            "content": content.strip()[:2000],
            "remove_after_use": remove_after_use,
            "ephemeral_response": ephemeral_response
        }
        if list_in_shop and price is not None:
            custom_items[item_id]["worth"] = price
        if revenue_share_user is not None:
            custom_items[item_id]["revenue_share_user_id"] = revenue_share_user.id
        set_custom_items(guild_id, custom_items)
        msg = (
            f"✅ 已新增自定義物品 **{name.strip()}**\n"
            f"ID: `{item_id}`\n"
            f"使用 `/itemmod give` 可發送給用戶。"
        )
        if list_in_shop:
            msg += f"\n🏪 已上架伺服器商店，定價 **{price:,.2f}** 伺服幣。"
        if revenue_share_user is not None:
            msg += f"\n💸 分潤用戶: {revenue_share_user.mention} (`{revenue_share_user.id}`) | 90%"
        await interaction.response.send_message(msg, ephemeral=True)
        log(f"Custom item {item_id} ({name}) added in guild {guild_id}", module_name="ItemSystem", user=interaction.user, guild=interaction.guild)

    @app_commands.command(name="removecustom", description="移除伺服器自定義物品")
    @app_commands.describe(item_id="要移除的自定義物品")
    @app_commands.autocomplete(item_id=custom_items_autocomplete)
    async def removecustom(self, interaction: discord.Interaction, item_id: str):
        if not interaction_uses_guild_scope(interaction):
            await interaction.response.send_message("❌ 伺服器啟用了全域模式，無法使用此指令。", ephemeral=True)
            return
        guild_id = interaction.guild.id
        custom_items = get_custom_items(guild_id)
        if item_id not in custom_items:
            await interaction.response.send_message("找不到此自定義物品。", ephemeral=True)
            return
        item_name = custom_items[item_id]["name"]
        del custom_items[item_id]
        set_custom_items(guild_id, custom_items)
        await interaction.response.send_message(f"✅ 已移除自定義物品 **{item_name}**。", ephemeral=True)
        log(f"Custom item {item_id} ({item_name}) removed in guild {guild_id}", module_name="ItemSystem", user=interaction.user, guild=interaction.guild)

    @app_commands.command(name="editcustom", description="編輯自定義物品的商店上架與定價")
    @app_commands.describe(
        item_id="要編輯的自定義物品",
        name="物品名稱",
        description="物品說明",
        content="使用物品時要傳送的文字內容",
        list_in_shop="是否上架伺服器商店",
        price="商店定價（伺服幣；若上架為否則會從商店移除）",
        remove_after_use="使用後是否自動移除物品",
        ephemeral_response="是否以隱藏訊息方式回應使用者",
        revenue_share_user="分潤用戶，物品被使用後會獲得 90% 價值",
    )
    @app_commands.autocomplete(item_id=custom_items_autocomplete)
    async def editcustom(self, interaction: discord.Interaction, item_id: str, name: str = None, description: str = None, content: str = None, list_in_shop: bool = None, price: float = None, remove_after_use: bool = None, ephemeral_response: bool = None, revenue_share_user: discord.User = None):
        if not interaction_uses_guild_scope(interaction):
            await interaction.response.send_message("❌ 伺服器啟用了全域模式，無法使用此指令。", ephemeral=True)
            return
        guild_id = interaction.guild.id
        custom_items = get_custom_items(guild_id)
        if item_id not in custom_items:
            await interaction.response.send_message("找不到此自定義物品。", ephemeral=True)
            return
        data = custom_items[item_id]
        if name is not None:
            if len(name.strip()) > 100:
                await interaction.response.send_message("物品名稱不可超過 100 字元。", ephemeral=True)
                return
            data["name"] = name.strip()
        if description is not None:
            data["description"] = description.strip()[:500]
        if content is not None:
            data["content"] = content.strip()[:2000]
        if remove_after_use is not None:
            data["remove_after_use"] = remove_after_use
        if ephemeral_response is not None:
            data["ephemeral_response"] = ephemeral_response
        if list_in_shop is not None:
            if list_in_shop:
                p = price if price is not None else data.get("worth")
                if p is None or p <= 0:
                    await interaction.response.send_message("上架商店時請設定大於 0 的定價。", ephemeral=True)
                    return
                data["worth"] = round(float(p), 2)
            else:
                data.pop("worth", None)
        elif price is not None and data.get("worth") is not None:
            if price <= 0:
                await interaction.response.send_message("定價必須大於 0。", ephemeral=True)
                return
            data["worth"] = round(float(price), 2)
        if revenue_share_user is not None:
            if revenue_share_user.bot:
                await interaction.response.send_message("分潤對象不能是機器人。", ephemeral=True)
                return
            if not data.get("remove_after_use", True):
                await interaction.response.send_message("只有一次性自訂物品才能設定分潤。", ephemeral=True)
                return
            if data.get("worth") is None or data.get("worth", 0) <= 0:
                await interaction.response.send_message("設定分潤的自訂物品必須先上架並有價格，分潤金額會是價格的 90%。", ephemeral=True)
                return
            data["revenue_share_user_id"] = revenue_share_user.id
        if not data.get("remove_after_use", True) or data.get("worth") is None or data.get("worth", 0) <= 0:
            data.pop("revenue_share_user_id", None)
        set_custom_items(guild_id, custom_items)
        worth = data.get("worth")
        status = f"已上架商店，定價 **{worth:,.2f}** 伺服幣" if worth else "未上架商店"
        await interaction.response.send_message(f"✅ 已更新 **{data['name']}**：{status}。", ephemeral=True)

    @app_commands.command(name="listcustom", description="列出本伺服器的自定義物品")
    async def listcustom(self, interaction: discord.Interaction):
        if not interaction_uses_guild_scope(interaction):
            await interaction.response.send_message("❌ 伺服器啟用了全域模式，無法使用此指令。", ephemeral=True)
            return
        guild_id = interaction.guild.id
        custom_items = get_custom_items(guild_id)
        if not custom_items:
            await interaction.response.send_message("本伺服器目前沒有自定義物品。", ephemeral=True)
            return
        embed = discord.Embed(title="伺服器自定義物品", color=0x9b59b6)
        for item_id, data in custom_items.items():
            preview = data["content"][:100] + ("..." if len(data["content"]) > 100 else "")
            worth = data.get("worth")
            shop_line = f"🏪 商店定價: **{worth:,.2f}** 伺服幣" if worth else "🏪 未上架商店"
            embed.add_field(
                name=f"{data['name']} (`{item_id}`)",
                value=f"內容預覽: {preview}\n{data.get('description', '')}\n{shop_line}",
                inline=False
            )
        embed.set_footer(text=interaction.guild.name, icon_url=interaction.guild.icon.url if interaction.guild.icon else None)
        await interaction.response.send_message(embed=embed)

asyncio.run(bot.add_cog(ItemModerate()))


if __name__ == "__main__":
    start_bot()
