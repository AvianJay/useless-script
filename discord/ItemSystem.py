# Item System for fun
import discord
import asyncio
import logging
import secrets
from types import SimpleNamespace
from globalenv import (
    bot, start_bot, get_user_data, set_user_data, get_server_config, set_server_config,
    interaction_uses_guild_scope, get_interaction_scope_guild_id,
)
from discord import app_commands
from discord.ext import commands
from logger import log

import i18n
from i18n import t


# item example:
# {"id": "some_unique_id", "name": "Item Name", "description": "Item Description", "callback": some_function, "additional_data": Any}
items = []
admin_action_callbacks = []  # Economy module hooks into this

CUSTOM_ITEMS_KEY = "custom_items"


def _get_autoreply_cog():
    return bot.get_cog("AutoReply") or bot.get_cog("autoreply")


class _ItemTemplateMessage:
    def __init__(self, guild, author, channel, content):
        self.guild = guild
        self.author = author
        self.channel = channel
        self.content = content

    async def add_reaction(self, emoji):
        return None


def _build_item_template_guild(interaction: discord.Interaction, scope_guild_id: int):
    source_guild = interaction.guild
    if source_guild is not None and scope_guild_id == getattr(source_guild, "id", None):
        return source_guild

    if scope_guild_id == 0:
        guild_name = t("itemsystem.scope.global")
    elif source_guild is not None:
        guild_name = source_guild.name
    else:
        guild_name = t("itemsystem.scope.unknown_guild")

    return SimpleNamespace(
        id=scope_guild_id,
        name=guild_name,
        emojis=getattr(source_guild, "emojis", []),
        stickers=getattr(source_guild, "stickers", []),
    )


def _get_item_template_allowed_mentions(allow_everyone_and_roles: bool = False):
    return discord.AllowedMentions(
        users=True,
        roles=allow_everyone_and_roles,
        everyone=allow_everyone_and_roles,
        replied_user=True,
    )


def _validate_custom_item_template(content: str):
    autoreply_cog = _get_autoreply_cog()
    if autoreply_cog is None:
        return
    autoreply_cog._validate_template_syntax(content)


async def _execute_custom_item_original_edits(interaction: discord.Interaction, autoreply_cog, trigger_message: _ItemTemplateMessage, edit_actions: list[dict]):
    for edit_action in edit_actions:
        try:
            await asyncio.sleep(edit_action["delay"])
            edit_content, _, edit_embed, edit_allowed_mentions = await autoreply_cog._render_response_segment(edit_action["template"], trigger_message)
            if not edit_content and edit_embed is None:
                continue
            await interaction.edit_original_response(
                content=edit_content or None,
                embed=edit_embed,
                allowed_mentions=edit_allowed_mentions,
            )
        except discord.HTTPException as e:
            log(f"Custom item delayed edit failed: {e}", module_name="ItemSystem", level=logging.ERROR)
            return
        except Exception as e:
            log(f"Custom item delayed edit error: {e}", module_name="ItemSystem", level=logging.ERROR)
            return


async def _execute_custom_item_followup_edits(sent_message, autoreply_cog, trigger_message: _ItemTemplateMessage, edit_actions: list[dict]):
    for edit_action in edit_actions:
        try:
            await asyncio.sleep(edit_action["delay"])
            edit_content, _, edit_embed, edit_allowed_mentions = await autoreply_cog._render_response_segment(edit_action["template"], trigger_message)
            if not edit_content and edit_embed is None:
                continue
            await sent_message.edit(
                content=edit_content or None,
                embed=edit_embed,
                allowed_mentions=edit_allowed_mentions,
            )
        except discord.HTTPException as e:
            log(f"Custom item followup edit failed: {e}", module_name="ItemSystem", level=logging.ERROR)
            return
        except Exception as e:
            log(f"Custom item followup edit error: {e}", module_name="ItemSystem", level=logging.ERROR)
            return


async def _execute_custom_item_followup_stage(interaction: discord.Interaction, autoreply_cog, trigger_message: _ItemTemplateMessage, stage: dict, ephemeral_response: bool):
    try:
        await asyncio.sleep(stage["send_delay"])
        followup_content, _, followup_embed, followup_allowed_mentions = await autoreply_cog._render_response_segment(stage["template"], trigger_message)
        if not followup_content and followup_embed is None:
            return

        sent_message = await interaction.followup.send(
            followup_content or None,
            embed=followup_embed,
            ephemeral=ephemeral_response,
            allowed_mentions=followup_allowed_mentions,
            wait=bool(stage["edits"]),
        )

        if stage["edits"] and sent_message is not None:
            asyncio.create_task(_execute_custom_item_followup_edits(sent_message, autoreply_cog, trigger_message, stage["edits"]))
    except discord.HTTPException as e:
        log(f"Custom item delayed followup failed: {e}", module_name="ItemSystem", level=logging.ERROR)
    except Exception as e:
        log(f"Custom item delayed followup error: {e}", module_name="ItemSystem", level=logging.ERROR)


async def _send_custom_item_content(interaction: discord.Interaction, item_id: str, content: str, ephemeral_response: bool):
    autoreply_cog = _get_autoreply_cog()
    if autoreply_cog is None:
        await interaction.response.send_message(
            content,
            ephemeral=ephemeral_response,
            allowed_mentions=_get_item_template_allowed_mentions(),
        )
        return

    scope_guild_id = getattr(interaction, "guild_id", interaction.guild.id if interaction.guild else 0) or 0
    mock_guild = _build_item_template_guild(interaction, int(scope_guild_id))
    mock_message = _ItemTemplateMessage(
        mock_guild,
        interaction.user,
        interaction.channel,
        f"/item use {item_id}",
    )

    final_response, _, embed, allowed_mentions, delayed_actions = await autoreply_cog._process_response_v2(content, mock_message)
    has_delayed_actions = bool(delayed_actions["initial_edits"] or delayed_actions["followups"])

    if final_response or embed is not None:
        await interaction.response.send_message(
            final_response or None,
            embed=embed,
            ephemeral=ephemeral_response,
            allowed_mentions=allowed_mentions,
        )
    elif has_delayed_actions:
        await interaction.response.defer(ephemeral=ephemeral_response, thinking=True)
    else:
        await interaction.response.send_message(
            t("itemsystem.msg.item_used"),
            ephemeral=ephemeral_response,
            allowed_mentions=_get_item_template_allowed_mentions(),
        )

    if delayed_actions["initial_edits"]:
        asyncio.create_task(_execute_custom_item_original_edits(interaction, autoreply_cog, mock_message, delayed_actions["initial_edits"]))
    for stage in delayed_actions["followups"]:
        asyncio.create_task(_execute_custom_item_followup_stage(interaction, autoreply_cog, mock_message, stage, ephemeral_response))


def _make_custom_text_callback(item_id: str, content: str, remove_after_use: bool = True, ephemeral_response: bool = False, worth: float = 0, revenue_share_user_id: int = None):
    """建立自定義文字物品使用時的回呼函數"""

    async def callback(interaction: discord.Interaction):
        guild_id = getattr(interaction, "guild_id", interaction.guild.id if interaction.guild else 0)
        user_id = interaction.user.id
        
        removed = await remove_item_from_user(guild_id, user_id, item_id, 1)
        if removed <= 0:
            await interaction.response.send_message(t("itemsystem.err.not_owned"), ephemeral=True)
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
        await _send_custom_item_content(interaction, item_id, content, ephemeral_response)
        log(f"Custom item {item_id} used by {interaction.user} in guild {guild_id}", module_name="ItemSystem")

    return callback


def get_custom_items(guild_id: int) -> dict:
    """取得伺服器的自定義物品列表。格式：{item_id: {name, description, content}}"""
    return get_server_config(guild_id, CUSTOM_ITEMS_KEY, {})


def set_custom_items(guild_id: int, custom_items: dict):
    """設定伺服器的自定義物品列表"""
    set_server_config(guild_id, CUSTOM_ITEMS_KEY, custom_items)


def localize_builtin_item(item: dict) -> dict:
    """內建物品的 name/description 依當前語言解析。

    內建物品在 import 期建立，name/description 是原文；帶有 name_key /
    desc_key 的項目會在讀取時以當前 locale 解析成淺拷貝。
    伺服器自定義物品的名稱是 guild 資料，原樣通過。
    """
    if not item or not (item.get("name_key") or item.get("desc_key")):
        return item
    localized = dict(item)
    if item.get("name_key"):
        localized["name"] = t(item["name_key"])
    if item.get("desc_key"):
        localized["description"] = t(item["desc_key"])
    return localized


def get_item_by_id(item_id: str, guild_id: int = None):
    """Get an item definition by its ID. 若提供 guild_id，會一併檢查該伺服器的自定義物品。"""
    # 先檢查全域物品
    item = next((i for i in items if i["id"] == item_id), None)
    if item:
        return localize_builtin_item(item)
    # 再檢查伺服器自定義物品
    if guild_id and item_id.startswith("custom_"):
        custom_items = get_custom_items(guild_id)
        if item_id in custom_items:
            data = custom_items[item_id]
            return {
                "id": item_id,
                "name": data["name"],
                "description": data.get("description") or t("itemsystem.msg.custom_item_desc"),
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
    result = [localize_builtin_item(item) for item in items]
    if guild_id:
        for item_id, data in get_custom_items(guild_id).items():
            result.append({
                "id": item_id,
                "name": data["name"],
                "description": data.get("description") or t("itemsystem.msg.custom_item_desc"),
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
    if amount <= 0:
        return 0

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
class ItemSystem(commands.GroupCog, name=app_commands.locale_str("item", i18n_key="cmd.itemsystem.item.root.name"), description=app_commands.locale_str("Item system commands", i18n_key="cmd.itemsystem.item.root.desc")):
    def __init__(self):
        super().__init__()
    
    @app_commands.command(name=app_commands.locale_str("list", i18n_key="cmd.itemsystem.item.list.name"), description=app_commands.locale_str("View the items you own", i18n_key="cmd.itemsystem.item.list.desc"))
    @app_commands.describe(scope=app_commands.locale_str("Scope to view (auto-detected by default)", i18n_key="cmd.itemsystem.item.list.param.scope"))
    @app_commands.choices(scope=[
        app_commands.Choice(name=app_commands.locale_str("Server", i18n_key="cmd.itemsystem.item.list.choice.server"), value="server"),
        app_commands.Choice(name=app_commands.locale_str("Global", i18n_key="cmd.itemsystem.item.list.choice.global"), value="global"),
    ])
    async def list_items(self, interaction: discord.Interaction, scope: str = None):
        user_id = interaction.user.id
        if scope is None:
            scope = "server" if interaction_uses_guild_scope(interaction) else "global"
        if scope == "global":
            guild_id = 0
            scope_name = t("itemsystem.scope.global")
        else:
            if not interaction_uses_guild_scope(interaction):
                await interaction.response.send_message(t("itemsystem.err.dm_global_only"), ephemeral=True)
                return
            guild_id = interaction.guild.id
            scope_name = interaction.guild.name
        user_items = get_user_data(guild_id, user_id, "items", {})
        
        if not user_items or all(v <= 0 for v in user_items.values()):
            await interaction.response.send_message(t("itemsystem.msg.no_items", scope=scope_name), ephemeral=True)
            return
        embed = discord.Embed(title=t("itemsystem.embed.inventory_title", user=interaction.user.display_name, scope=scope_name), color=0x00ff00)
        for item_id, amount in user_items.items():
            if amount <= 0:
                continue
            item = get_item_by_id(item_id, guild_id if scope == "server" else None)
            if item:
                worth_text = t("itemsystem.msg.worth", worth=item["worth"]) if item.get("worth", 0) > 0 else ""
                embed.add_field(name=f"{item['name']} x{amount}", value=f"{item['description']}{worth_text}", inline=False)
        embed.set_footer(
            text=scope_name if scope == "global" else (interaction.guild.name if interaction.guild else t("itemsystem.scope.unknown_guild")),
            icon_url=interaction.guild.icon.url if interaction.guild and interaction.guild.icon else None
        )
        
        await interaction.response.send_message(embed=embed)
    
    @app_commands.command(name=app_commands.locale_str("use", i18n_key="cmd.itemsystem.item.use.name"), description=app_commands.locale_str("Use an item", i18n_key="cmd.itemsystem.item.use.desc"))
    @app_commands.describe(item_id=app_commands.locale_str("The item ID to use", i18n_key="cmd.itemsystem.item.use.param.item_id"), scope=app_commands.locale_str("Scope to use in (auto-detected by default)", i18n_key="cmd.itemsystem.item.use.param.scope"))
    @app_commands.autocomplete(item_id=get_user_items_scoped_autocomplete)
    @app_commands.choices(scope=[
        app_commands.Choice(name=app_commands.locale_str("Server", i18n_key="cmd.itemsystem.item.use.choice.server"), value="server"),
        app_commands.Choice(name=app_commands.locale_str("Global", i18n_key="cmd.itemsystem.item.use.choice.global"), value="global"),
    ])
    async def use_item(self, interaction: discord.Interaction, item_id: str, scope: str = None):
        user_id = interaction.user.id
        if scope is None:
            scope = "server" if interaction_uses_guild_scope(interaction) else "global"
        guild_id = 0 if scope == "global" else get_interaction_scope_guild_id(interaction)
        user_items = get_user_data(guild_id, user_id, "items", {})
        
        if item_id not in user_items.keys() or user_items[item_id] <= 0:
            await interaction.response.send_message(t("itemsystem.err.not_owned"), ephemeral=True)
            return
        
        item = get_item_by_id(item_id, guild_id)
        if not item:
            await interaction.response.send_message(t("itemsystem.err.invalid_item"), ephemeral=True)
            return
        
        # Pass scope to callback via interaction attribute
        interaction.guild_id = guild_id
        
        # Call the item's callback function
        if "callback" in item and callable(item["callback"]):
            await item["callback"](interaction)
        else:
            await interaction.response.send_message(t("itemsystem.err.not_usable"), ephemeral=True)
    
    @app_commands.command(name=app_commands.locale_str("drop", i18n_key="cmd.itemsystem.item.drop.name"), description=app_commands.locale_str("Drop an item", i18n_key="cmd.itemsystem.item.drop.desc"))
    @app_commands.describe(item_id=app_commands.locale_str("The item ID to drop", i18n_key="cmd.itemsystem.item.drop.param.item_id"), amount=app_commands.locale_str("How many to drop", i18n_key="cmd.itemsystem.item.drop.param.amount"), can_pickup=app_commands.locale_str("Can others pick this item up?", i18n_key="cmd.itemsystem.item.drop.param.can_pickup"), pickup_duration=app_commands.locale_str("How long the item can be picked up (seconds)", i18n_key="cmd.itemsystem.item.drop.param.pickup_duration"), pickup_only_once=app_commands.locale_str("Can the item only be picked up once?", i18n_key="cmd.itemsystem.item.drop.param.pickup_only_once"), scope=app_commands.locale_str("Item scope (auto-detected by default)", i18n_key="cmd.itemsystem.item.drop.param.scope"))
    @app_commands.autocomplete(item_id=get_user_items_scoped_autocomplete)
    @app_commands.choices(
        can_pickup=[
            app_commands.Choice(name=app_commands.locale_str("Yes", i18n_key="cmd.itemsystem.item.drop.choice.true"), value="True"),
            app_commands.Choice(name=app_commands.locale_str("No", i18n_key="cmd.itemsystem.item.drop.choice.false"), value="False")
        ],
        pickup_only_once=[
            app_commands.Choice(name=app_commands.locale_str("Yes", i18n_key="cmd.itemsystem.item.drop.choice.true"), value="True"),
            app_commands.Choice(name=app_commands.locale_str("No", i18n_key="cmd.itemsystem.item.drop.choice.false"), value="False")
        ],
        scope=[
            app_commands.Choice(name=app_commands.locale_str("Server", i18n_key="cmd.itemsystem.item.drop.choice.server"), value="server"),
            app_commands.Choice(name=app_commands.locale_str("Global", i18n_key="cmd.itemsystem.item.drop.choice.global"), value="global"),
        ]
    )
    async def drop_item(self, interaction: discord.Interaction, item_id: str, amount: int = 1, can_pickup: str = "True", pickup_duration: int = 60, pickup_only_once: str = "False", scope: str = None):
        if scope is None:
            scope = "server" if interaction_uses_guild_scope(interaction) else "global"
        can_pickup = (can_pickup == "True")
        pickup_only_once = (pickup_only_once == "True")
        if amount <= 0:
            await interaction.response.send_message(t("itemsystem.err.amount_positive"), ephemeral=True)
            return
        user_id = interaction.user.id
        guild_id = 0 if scope == "global" else get_interaction_scope_guild_id(interaction)
        user_item_count = await get_user_items(guild_id, user_id, item_id)

        if user_item_count <= 0:
            await interaction.response.send_message(t("itemsystem.err.not_owned"), ephemeral=True)
            return
        target_item = get_item_by_id(item_id, guild_id if guild_id else None)
        
        if can_pickup:
            if pickup_duration <= 0 or pickup_duration > 86400:
                await interaction.response.send_message(t("itemsystem.err.pickup_duration_range"), ephemeral=True)
                return

        amount = await remove_item_from_user(guild_id, user_id, item_id, min(amount, user_item_count))
        remaining_count = amount  # 剩餘可撿起的數量
        remaining_admin_count = 0
        if guild_id:
            from Economy import get_admin_item_count, remove_admin_item, add_admin_item

            admin_count = get_admin_item_count(guild_id, user_id, item_id)
            remaining_admin_count = min(admin_count, amount)
            if remaining_admin_count > 0:
                remove_admin_item(guild_id, user_id, item_id, remaining_admin_count)
        picked_up = set()  # user ids who picked up
        # drop to current channel
        class DropView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=pickup_duration)
                self.interaction = interaction
            
            async def on_timeout(self):
                for child in self.children:
                    child.disabled = True
                await self.interaction.edit_original_response(content=t("itemsystem.msg.drop_expired", user=self.interaction.user.display_name, item=target_item["name"], amount=amount), view=self)

            @discord.ui.button(label=t("itemsystem.btn.pick_up"), style=discord.ButtonStyle.green, custom_id="pick_up_item")
            async def pick_up(self, interaction: discord.Interaction, button: discord.ui.Button):
                nonlocal remaining_count, remaining_admin_count
                if pickup_only_once and interaction.user.id in picked_up:
                    await interaction.response.send_message(t("itemsystem.err.already_picked"), ephemeral=True)
                    return
                picked_up.add(interaction.user.id)
                if remaining_count <= 0:
                    await interaction.response.send_message(t("itemsystem.err.all_picked"), ephemeral=True)
                    return
                user_id = interaction.user.id
                other_user_items = get_user_data(guild_id, user_id, "items", {})
                remaining_count -= 1  # 減少剩餘數量
                other_user_items[item_id] = other_user_items.get(item_id, 0) + 1
                set_user_data(guild_id, user_id, "items", other_user_items)
                if guild_id and remaining_admin_count > 0:
                    add_admin_item(guild_id, user_id, item_id, 1)
                    remaining_admin_count -= 1
                log(f"{interaction.user} picked up {target_item['id']} in guild {guild_id}", module_name="ItemSystem")
                await interaction.response.send_message(t("itemsystem.msg.picked_up", item=target_item["name"]), ephemeral=True)
                if remaining_count <= 0:
                    await self.interaction.edit_original_response(content=t("itemsystem.msg.drop_all_picked", user=self.interaction.user.display_name, item=target_item["name"], amount=amount), view=None)
                    self.stop()

        if can_pickup:
            await interaction.response.send_message(t("itemsystem.msg.dropped", user=interaction.user.display_name, item=target_item["name"], amount=amount), view=DropView())
            # print(f"[ItemSystem] {interaction.user} dropped {target_item['name']} x{amount} in guild {guild_id}")
            log(f"{interaction.user} dropped {target_item['name']} x{amount} in guild {guild_id}", module_name="ItemSystem", user=interaction.user, guild=interaction.guild)
        else:
            await interaction.response.send_message(t("itemsystem.msg.dropped_gone", user=interaction.user.display_name, item=target_item["name"], amount=amount))
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
    #         await interaction.response.send_message(t("itemsystem.err.not_owned"), ephemeral=True)
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

    @app_commands.command(name=app_commands.locale_str("give", i18n_key="cmd.itemsystem.item.give.name"), description=app_commands.locale_str("Give an item to another user", i18n_key="cmd.itemsystem.item.give.desc"))
    @app_commands.describe(user=app_commands.locale_str("The user to give the item to", i18n_key="cmd.itemsystem.item.give.param.user"), item_id=app_commands.locale_str("The item ID to give", i18n_key="cmd.itemsystem.item.give.param.item_id"), amount=app_commands.locale_str("Amount", i18n_key="cmd.itemsystem.item.give.param.amount"), scope=app_commands.locale_str("Item scope (auto-detected by default)", i18n_key="cmd.itemsystem.item.give.param.scope"))
    @app_commands.autocomplete(item_id=get_user_items_scoped_autocomplete)
    @app_commands.choices(scope=[
        app_commands.Choice(name=app_commands.locale_str("Server", i18n_key="cmd.itemsystem.item.give.choice.server"), value="server"),
        app_commands.Choice(name=app_commands.locale_str("Global", i18n_key="cmd.itemsystem.item.give.choice.global"), value="global"),
    ])
    async def give_item(self, interaction: discord.Interaction, user: discord.User, item_id: str, amount: int = 1, scope: str = None):
        await interaction.response.defer()
        if scope is None:
            scope = "server" if interaction_uses_guild_scope(interaction) else "global"
        if amount <= 0:
            await interaction.followup.send(t("itemsystem.err.amount_positive"))
            return
        giver_id = interaction.user.id
        receiver_id = user.id
        guild_id = 0 if scope == "global" else get_interaction_scope_guild_id(interaction)
        
        if giver_id == receiver_id:
            await interaction.followup.send(t("itemsystem.err.give_self"))
            return

        if user.bot:
            await interaction.followup.send(t("itemsystem.err.give_bot"))
            return
        
        giver_item_count = await get_user_items(guild_id, giver_id, item_id)
        if giver_item_count <= 0:
            await interaction.followup.send(t("itemsystem.err.not_owned"))
            return
        
        item = get_item_by_id(item_id, guild_id if guild_id else None)
        if not item:
            await interaction.followup.send(t("itemsystem.err.invalid_item"))
            return

        # Remove from giver
        removed = await remove_item_from_user(guild_id, giver_id, item_id, amount)
        
        # Add to receiver
        await give_item_to_user(guild_id, receiver_id, item_id, removed)
        if guild_id:
            from Economy import get_admin_item_count, remove_admin_item, add_admin_item

            admin_count = get_admin_item_count(guild_id, giver_id, item_id)
            if admin_count > 0:
                transferred = min(admin_count, removed)
                remove_admin_item(guild_id, giver_id, item_id, transferred)
                add_admin_item(guild_id, receiver_id, item_id, transferred)
        
        await interaction.followup.send(t("itemsystem.msg.gave", user=f"{user.display_name}(`{user.name}`)", count=removed, item=item["name"]), allowed_mentions=discord.AllowedMentions.none())
        # dm the receiver
        try:
            recipient_loc = i18n.resolve_locale(user_id=user.id)
            scope_name = interaction.guild.name if interaction_uses_guild_scope(interaction) and interaction.guild else t("itemsystem.scope.dm", locale=recipient_loc)
            await user.send(t("itemsystem.msg.received", locale=recipient_loc, sender=f"{interaction.user.display_name}(`{interaction.user.name}`)", count=amount, item=item["name"], scope=scope_name), allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            pass

asyncio.run(bot.add_cog(ItemSystem()))


# admin cheating
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
class ItemModerate(commands.GroupCog, name=app_commands.locale_str("itemmod", i18n_key="cmd.itemsystem.itemmod.root.name"), description=app_commands.locale_str("Item system admin commands", i18n_key="cmd.itemsystem.itemmod.root.desc")):
    def __init__(self):
        super().__init__()
    
    @app_commands.command(name=app_commands.locale_str("give", i18n_key="cmd.itemsystem.itemmod.give.name"), description=app_commands.locale_str("Give a user an item (may affect the economy)", i18n_key="cmd.itemsystem.itemmod.give.desc"))
    @app_commands.describe(user=app_commands.locale_str("The user to give the item to", i18n_key="cmd.itemsystem.itemmod.give.param.user"), item_id=app_commands.locale_str("The item ID to give", i18n_key="cmd.itemsystem.itemmod.give.param.item_id"), amount=app_commands.locale_str("How many to give", i18n_key="cmd.itemsystem.itemmod.give.param.amount"))
    @app_commands.autocomplete(item_id=all_items_autocomplete)
    async def admin_give_item(self, interaction: discord.Interaction, user: discord.User, item_id: str, amount: int = 1):
        await interaction.response.defer()

        if amount <= 0:
            await interaction.followup.send(t("itemsystem.err.amount_positive"))
            return

        if not interaction_uses_guild_scope(interaction):
            await interaction.followup.send(t("itemsystem.err.global_mode"))
            return

        if user.bot:
            await interaction.followup.send(t("itemsystem.err.give_bot"))
            return
        
        receiver_id = user.id
        guild_id = interaction.guild.id
        
        item = get_item_by_id(item_id, interaction.guild.id)
        if not item:
            await interaction.followup.send(t("itemsystem.err.invalid_item"))
            return

        if item.get("worth", 0) == 0:
            await interaction.followup.send(t("itemsystem.err.item_unavailable"))
            return
        
        await give_item_to_user(guild_id, receiver_id, item_id, amount)

        # Notify Economy module about admin injection
        for callback in admin_action_callbacks:
            try:
                await callback(guild_id, "give", item_id, amount, receiver_id)
            except Exception as e:
                log(f"Error in admin action callback: {e}", module_name="ItemSystem", level=logging.ERROR)

        await interaction.followup.send(t("itemsystem.msg.gave", user=f"{user.display_name}(`{user.name}`)", count=amount, item=item["name"]), allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name=app_commands.locale_str("remove", i18n_key="cmd.itemsystem.itemmod.remove.name"), description=app_commands.locale_str("Remove an item from a user", i18n_key="cmd.itemsystem.itemmod.remove.desc"))
    @app_commands.describe(user=app_commands.locale_str("The user to remove the item from", i18n_key="cmd.itemsystem.itemmod.remove.param.user"), item_id=app_commands.locale_str("The item ID to remove", i18n_key="cmd.itemsystem.itemmod.remove.param.item_id"), amount=app_commands.locale_str("How many to remove", i18n_key="cmd.itemsystem.itemmod.remove.param.amount"))
    @app_commands.autocomplete(item_id=all_items_autocomplete)
    async def admin_remove_item(self, interaction: discord.Interaction, user: discord.User, item_id: str, amount: int):
        if not interaction_uses_guild_scope(interaction):
            await interaction.response.send_message(t("itemsystem.err.global_mode"), ephemeral=True)
            return
        if amount <= 0:
            await interaction.response.send_message(t("itemsystem.err.amount_positive"), ephemeral=True)
            return
        if user.bot:
            await interaction.response.send_message(t("itemsystem.err.remove_bot"), ephemeral=True)
            return

        receiver_id = user.id
        guild_id = interaction.guild.id
        
        removed_count = await remove_item_from_user(guild_id, receiver_id, item_id, amount)
        if removed_count == 0:
            await interaction.response.send_message(t("itemsystem.err.user_not_owned", user=user.name), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
            return
        
        item = get_item_by_id(item_id, guild_id)
        item_name = item["name"] if item else t("itemsystem.msg.unknown_item")

        await interaction.response.send_message(t("itemsystem.msg.removed", user=f"{user.display_name}(`{user.name}`)", count=removed_count, item=item_name), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @app_commands.command(name=app_commands.locale_str("list", i18n_key="cmd.itemsystem.itemmod.list.name"), description=app_commands.locale_str("List all available items", i18n_key="cmd.itemsystem.itemmod.list.desc"))
    async def admin_list_items(self, interaction: discord.Interaction):
        all_items_list = get_all_items_for_guild(interaction.guild.id)
        if not all_items_list:
            await interaction.response.send_message(t("itemsystem.msg.no_items_exist"), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
            return

        if not interaction_uses_guild_scope(interaction):
            await interaction.response.send_message(t("itemsystem.err.global_mode"), ephemeral=True)
            return

        embed = discord.Embed(title=t("itemsystem.embed.all_items_title"), color=0x0000ff)
        for item in all_items_list:
            custom_tag = t("itemsystem.msg.custom_tag") if item["id"].startswith("custom_") else ""
            embed.add_field(name=f"{item['name']}{custom_tag}", value=item["description"], inline=False)
        embed.set_footer(text=interaction.guild.name, icon_url=interaction.guild.icon.url if interaction.guild.icon else None)
        
        await interaction.response.send_message(embed=embed)
    
    @app_commands.command(name=app_commands.locale_str("listuser", i18n_key="cmd.itemsystem.itemmod.listuser.name"), description=app_commands.locale_str("List the items a user owns", i18n_key="cmd.itemsystem.itemmod.listuser.desc"))
    @app_commands.describe(user=app_commands.locale_str("The user to look up", i18n_key="cmd.itemsystem.itemmod.listuser.param.user"))
    async def admin_list_user_items(self, interaction: discord.Interaction, user: discord.User):
        if user.bot:
            await interaction.response.send_message(t("itemsystem.err.bot_has_no_items"), ephemeral=True)
            return

        if not interaction_uses_guild_scope(interaction):
            await interaction.response.send_message(t("itemsystem.err.global_mode"), ephemeral=True)
            return

        guild_id = interaction.guild.id
        scope_name = interaction.guild.name
        user_items = get_user_data(guild_id, user.id, "items", {})
        user_items = {item_id: count for item_id, count in user_items.items() if count > 0}

        if not user_items:
            await interaction.response.send_message(t("itemsystem.msg.user_no_items", user=user.name, scope=scope_name), ephemeral=True)
            return

        embed = discord.Embed(title=t("itemsystem.embed.inventory_title", user=user.name, scope=scope_name), color=0x00ff00)
        for item_id, amount in user_items.items():
            item = get_item_by_id(item_id, guild_id)
            if item:
                embed.add_field(name=f"{item['name']} x{amount}", value=item["description"], inline=False)
        embed.set_footer(text=interaction.guild.name, icon_url=interaction.guild.icon.url if interaction.guild.icon else None)

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name=app_commands.locale_str("addcustom", i18n_key="cmd.itemsystem.itemmod.addcustom.name"), description=app_commands.locale_str("Add a server custom item", i18n_key="cmd.itemsystem.itemmod.addcustom.desc"))
    @app_commands.describe(
        name=app_commands.locale_str("Item name", i18n_key="cmd.itemsystem.itemmod.addcustom.param.name"),
        content=app_commands.locale_str("Text sent when the item is used; AutoReply variables supported", i18n_key="cmd.itemsystem.itemmod.addcustom.param.content"),
        description=app_commands.locale_str("Item description (optional, defaults to \"Custom item\")", i18n_key="cmd.itemsystem.itemmod.addcustom.param.description"),
        list_in_shop=app_commands.locale_str("List it in the server shop", i18n_key="cmd.itemsystem.itemmod.addcustom.param.list_in_shop"),
        price=app_commands.locale_str("Shop price (server currency; only used when listed in shop)", i18n_key="cmd.itemsystem.itemmod.addcustom.param.price"),
        remove_after_use=app_commands.locale_str("Remove the item automatically after use", i18n_key="cmd.itemsystem.itemmod.addcustom.param.remove_after_use"),
        ephemeral_response=app_commands.locale_str("Respond to the user with a hidden (ephemeral) message", i18n_key="cmd.itemsystem.itemmod.addcustom.param.ephemeral_response"),
        revenue_share_user=app_commands.locale_str("Revenue-share user; receives 90% of the value when the item is used", i18n_key="cmd.itemsystem.itemmod.addcustom.param.revenue_share_user"),
    )
    async def addcustom(self, interaction: discord.Interaction, name: str, content: str, description: str = None, list_in_shop: bool = False, price: float = None, remove_after_use: bool = True, ephemeral_response: bool = False, revenue_share_user: discord.User = None):
        if not interaction_uses_guild_scope(interaction):
            await interaction.response.send_message(t("itemsystem.err.global_mode"), ephemeral=True)
            return
        if not name or len(name.strip()) < 1:
            await interaction.response.send_message(t("itemsystem.err.name_empty"), ephemeral=True)
            return
        if not content or len(content.strip()) < 1:
            await interaction.response.send_message(t("itemsystem.err.content_empty"), ephemeral=True)
            return
        if len(content) > 2000:
            await interaction.response.send_message(t("itemsystem.err.content_too_long"), ephemeral=True)
            return
        try:
            _validate_custom_item_template(content.strip())
        except Exception as e:
            await interaction.response.send_message(t("itemsystem.err.template_syntax", error=e), ephemeral=True)
            return
        if len(name) > 100:
            await interaction.response.send_message(t("itemsystem.err.name_too_long"), ephemeral=True)
            return
        if list_in_shop:
            if price is None or price <= 0:
                await interaction.response.send_message(t("itemsystem.err.price_required"), ephemeral=True)
                return
            price = round(float(price), 2)
        else:
            price = None
        if revenue_share_user is not None:
            if revenue_share_user.bot:
                await interaction.response.send_message(t("itemsystem.err.revenue_bot"), ephemeral=True)
                return
            if not remove_after_use:
                await interaction.response.send_message(t("itemsystem.err.revenue_once_only"), ephemeral=True)
                return
            if price is None or price <= 0:
                await interaction.response.send_message(t("itemsystem.err.revenue_needs_price"), ephemeral=True)
                return

        guild_id = interaction.guild.id
        custom_items = get_custom_items(guild_id)
        item_id = f"custom_{secrets.token_hex(4)}"
        custom_items[item_id] = {
            "name": name.strip()[:100],
            "description": (description or t("itemsystem.msg.custom_item_desc"))[:500],
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
            t("itemsystem.msg.custom_added", name=name.strip()) + "\n"
            + f"ID: `{item_id}`\n"
            + t("itemsystem.msg.custom_added_hint")
        )
        if list_in_shop:
            from Economy import get_currency_name
            msg += "\n" + t("itemsystem.msg.listed_in_shop", price=i18n.fmt_num(price, decimals=2), currency=get_currency_name(guild_id))
        if revenue_share_user is not None:
            msg += "\n" + t("itemsystem.msg.revenue_user", user=f"{revenue_share_user.mention} (`{revenue_share_user.id}`)")
        await interaction.response.send_message(msg, ephemeral=True)
        log(f"Custom item {item_id} ({name}) added in guild {guild_id}", module_name="ItemSystem", user=interaction.user, guild=interaction.guild)

    @app_commands.command(name=app_commands.locale_str("removecustom", i18n_key="cmd.itemsystem.itemmod.removecustom.name"), description=app_commands.locale_str("Remove a server custom item", i18n_key="cmd.itemsystem.itemmod.removecustom.desc"))
    @app_commands.describe(item_id=app_commands.locale_str("The custom item to remove", i18n_key="cmd.itemsystem.itemmod.removecustom.param.item_id"))
    @app_commands.autocomplete(item_id=custom_items_autocomplete)
    async def removecustom(self, interaction: discord.Interaction, item_id: str):
        if not interaction_uses_guild_scope(interaction):
            await interaction.response.send_message(t("itemsystem.err.global_mode"), ephemeral=True)
            return
        guild_id = interaction.guild.id
        custom_items = get_custom_items(guild_id)
        if item_id not in custom_items:
            await interaction.response.send_message(t("itemsystem.err.custom_not_found"), ephemeral=True)
            return
        item_name = custom_items[item_id]["name"]
        del custom_items[item_id]
        set_custom_items(guild_id, custom_items)
        await interaction.response.send_message(t("itemsystem.msg.custom_removed", name=item_name), ephemeral=True)
        log(f"Custom item {item_id} ({item_name}) removed in guild {guild_id}", module_name="ItemSystem", user=interaction.user, guild=interaction.guild)

    @app_commands.command(name=app_commands.locale_str("editcustom", i18n_key="cmd.itemsystem.itemmod.editcustom.name"), description=app_commands.locale_str("Edit a custom item's shop listing and price", i18n_key="cmd.itemsystem.itemmod.editcustom.desc"))
    @app_commands.describe(
        item_id=app_commands.locale_str("The custom item to edit", i18n_key="cmd.itemsystem.itemmod.editcustom.param.item_id"),
        name=app_commands.locale_str("Item name", i18n_key="cmd.itemsystem.itemmod.editcustom.param.name"),
        description=app_commands.locale_str("Item description", i18n_key="cmd.itemsystem.itemmod.editcustom.param.description"),
        content=app_commands.locale_str("Text sent when the item is used; AutoReply variables supported", i18n_key="cmd.itemsystem.itemmod.editcustom.param.content"),
        list_in_shop=app_commands.locale_str("List it in the server shop", i18n_key="cmd.itemsystem.itemmod.editcustom.param.list_in_shop"),
        remove_after_use=app_commands.locale_str("Remove the item automatically after use", i18n_key="cmd.itemsystem.itemmod.editcustom.param.remove_after_use"),
        ephemeral_response=app_commands.locale_str("Respond to the user with a hidden (ephemeral) message", i18n_key="cmd.itemsystem.itemmod.editcustom.param.ephemeral_response"),
        revenue_share_user=app_commands.locale_str("Revenue-share user; receives 90% of the value when the item is used", i18n_key="cmd.itemsystem.itemmod.editcustom.param.revenue_share_user"),
    )
    @app_commands.autocomplete(item_id=custom_items_autocomplete)
    async def editcustom(self, interaction: discord.Interaction, item_id: str, name: str = None, description: str = None, content: str = None, list_in_shop: bool = None, remove_after_use: bool = None, ephemeral_response: bool = None, revenue_share_user: discord.User = None):
        if not interaction_uses_guild_scope(interaction):
            await interaction.response.send_message(t("itemsystem.err.global_mode"), ephemeral=True)
            return
        guild_id = interaction.guild.id
        custom_items = get_custom_items(guild_id)
        if item_id not in custom_items:
            await interaction.response.send_message(t("itemsystem.err.custom_not_found"), ephemeral=True)
            return
        data = custom_items[item_id]
        if name is not None:
            if len(name.strip()) > 100:
                await interaction.response.send_message(t("itemsystem.err.name_too_long"), ephemeral=True)
                return
            data["name"] = name.strip()
        if description is not None:
            data["description"] = description.strip()[:500]
        if content is not None:
            try:
                _validate_custom_item_template(content.strip())
            except Exception as e:
                await interaction.response.send_message(t("itemsystem.err.template_syntax", error=e), ephemeral=True)
                return
            data["content"] = content.strip()[:2000]
        if remove_after_use is not None:
            data["remove_after_use"] = remove_after_use
        if ephemeral_response is not None:
            data["ephemeral_response"] = ephemeral_response
        if list_in_shop is not None:
            if list_in_shop:
                current_worth = data.get("worth")
                if current_worth is None or current_worth <= 0:
                    await interaction.response.send_message(t("itemsystem.err.cannot_reprice"), ephemeral=True)
                    return
            else:
                data.pop("worth", None)
        if revenue_share_user is not None:
            if revenue_share_user.bot:
                await interaction.response.send_message(t("itemsystem.err.revenue_bot"), ephemeral=True)
                return
            if not data.get("remove_after_use", True):
                await interaction.response.send_message(t("itemsystem.err.revenue_once_only"), ephemeral=True)
                return
            if data.get("worth") is None or data.get("worth", 0) <= 0:
                await interaction.response.send_message(t("itemsystem.err.revenue_needs_price"), ephemeral=True)
                return
            data["revenue_share_user_id"] = revenue_share_user.id
        if not data.get("remove_after_use", True) or data.get("worth") is None or data.get("worth", 0) <= 0:
            data.pop("revenue_share_user_id", None)
        set_custom_items(guild_id, custom_items)
        worth = data.get("worth")
        from Economy import get_currency_name
        status = t("itemsystem.msg.status_listed", price=i18n.fmt_num(worth, decimals=2), currency=get_currency_name(scope_guild_id)) if worth else t("itemsystem.msg.status_unlisted")
        await interaction.response.send_message(t("itemsystem.msg.custom_updated", name=data["name"], status=status), ephemeral=True)

    @app_commands.command(name=app_commands.locale_str("listcustom", i18n_key="cmd.itemsystem.itemmod.listcustom.name"), description=app_commands.locale_str("List this server's custom items", i18n_key="cmd.itemsystem.itemmod.listcustom.desc"))
    async def listcustom(self, interaction: discord.Interaction):
        if not interaction_uses_guild_scope(interaction):
            await interaction.response.send_message(t("itemsystem.err.global_mode"), ephemeral=True)
            return
        guild_id = interaction.guild.id
        custom_items = get_custom_items(guild_id)
        if not custom_items:
            await interaction.response.send_message(t("itemsystem.msg.no_custom_items"), ephemeral=True)
            return
        embed = discord.Embed(title=t("itemsystem.embed.custom_items_title"), color=0x9b59b6)
        for item_id, data in custom_items.items():
            preview = data["content"][:100] + ("..." if len(data["content"]) > 100 else "")
            worth = data.get("worth")
            shop_line = t("itemsystem.msg.shop_price", price=i18n.fmt_num(worth, decimals=2), currency=get_currency_name(scope_guild_id)) if worth else t("itemsystem.msg.shop_unlisted")
            embed.add_field(
                name=f"{data['name']} (`{item_id}`)",
                value=t("itemsystem.msg.content_preview", preview=preview) + f"\n{data.get('description', '')}\n{shop_line}",
                inline=False
            )
        embed.set_footer(text=interaction.guild.name, icon_url=interaction.guild.icon.url if interaction.guild.icon else None)
        await interaction.response.send_message(embed=embed)

asyncio.run(bot.add_cog(ItemModerate()))


if __name__ == "__main__":
    start_bot()
