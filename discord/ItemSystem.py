# Item System for fun
import discord
import asyncio
from globalenv import bot, start_bot, get_user_data, set_user_data
from discord import app_commands
from discord.ext import commands


# item example:
# {"id": "some_unique_id", "name": "Item Name", "description": "Item Description", "callback": some_function, "additional_data": Any}
items = []


async def get_user_items_autocomplete(interaction: discord.Interaction, current: str):
    guild_id = interaction.guild.id if interaction.guild else None
    user_id = interaction.user.id
    user_items = get_user_data(guild_id, user_id, "items", [])
    choices = [item for item in items if item["id"] in user_items and current.lower() in item["name"].lower()]
    return [app_commands.Choice(name=item["name"], value=item["id"]) for item in choices[:25]]


@app_commands.guild_only()
class ItemSystem(commands.GroupCog, name="item", description="物品系統指令"):
    def __init__(self):
        super().__init__()
    
    @app_commands.command(name="list", description="查看你擁有的物品")
    async def list_items(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        guild_id = interaction.guild.id if interaction.guild else None
        user_items = get_user_data(guild_id, user_id, "items", [])
        
        if not user_items:
            await interaction.response.send_message("你沒有任何物品。", ephemeral=True)
            return

        embed = discord.Embed(title=f"{interaction.user.name} 的物品", color=0x00ff00)
        for item_id in user_items:
            item = next((i for i in items if i["id"] == item_id), None)
            if item:
                embed.add_field(name=item["name"], value=item["description"], inline=False)
        embed.set_footer(text=interaction.guild.name, icon_url=interaction.guild.icon.url if interaction.guild.icon else None)
        
        await interaction.response.send_message(embed=embed)
    
    @app_commands.command(name="use", description="使用一個物品")
    @app_commands.describe(item_id="你想使用的物品ID")
    @app_commands.autocomplete(item_id=get_user_items_autocomplete)
    async def use_item(self, interaction: discord.Interaction, item_id: str):
        user_id = interaction.user.id
        guild_id = interaction.guild.id if interaction.guild else None
        user_items = get_user_data(guild_id, user_id, "items", [])
        
        if item_id not in user_items:
            await interaction.response.send_message("你沒有這個物品。", ephemeral=True)
            return
        
        item = next((i for i in items if i["id"] == item_id), None)
        if not item:
            await interaction.response.send_message("無效的物品ID。", ephemeral=True)
            return
        
        # Call the item's callback function
        if "callback" in item and callable(item["callback"]):
            await item["callback"](interaction)
    
    @app_commands.command(name="drop", description="丟棄一個物品")
    @app_commands.describe(item_id="你想丟棄的物品ID")
    async def drop_item(self, interaction: discord.Interaction, item_id: str):
        user_id = interaction.user.id
        guild_id = interaction.guild.id if interaction.guild else None
        user_items = get_user_data(guild_id, user_id, "items", [])

        if item_id not in user_items:
            await interaction.response.send_message("你沒有這個物品。", ephemeral=True)
            return

        user_items.remove(item_id)
        set_user_data(guild_id, user_id, "items", user_items)

        await interaction.response.send_message(f"你丟棄了 {items[item_id]['name']}。", ephemeral=True)
    
    @app_commands.command(name="give", description="給予另一個用戶一個物品")
    @app_commands.describe(user="你想給予物品的用戶", item_id="你想給予的物品ID")
    async def give_item(self, interaction: discord.Interaction, user: discord.User, item_id: str):
        giver_id = interaction.user.id
        receiver_id = user.id
        guild_id = interaction.guild.id if interaction.guild else None
        
        giver_items = get_user_data(guild_id, giver_id, "items", [])
        if item_id not in giver_items:
            await interaction.response.send_message("你沒有這個物品。", ephemeral=True)
            return
        
        item = next((i for i in items if i["id"] == item_id), None)
        if not item:
            await interaction.response.send_message("無效的物品ID。", ephemeral=True)
            return
        
        # Remove from giver
        giver_items.remove(item_id)
        set_user_data(guild_id, giver_id, "items", giver_items)
        
        # Add to receiver
        receiver_items = get_user_data(guild_id, receiver_id, "items", [])
        receiver_items.append(item_id)
        set_user_data(guild_id, receiver_id, "items", receiver_items)
        
        await interaction.response.send_message(f"你給了 {user.name} 一個 {item['name']}。", ephemeral=True)

asyncio.run(bot.add_cog(ItemSystem()))


# admin cheating
@app_commands.guild_only()
@commands.has_permissions(administrator=True)
class ItemModerate(commands.GroupCog, name="itemmod", description="物品系統管理指令"):
    def __init__(self):
        super().__init__()
    
    @app_commands.command(name="give", description="給予用戶一個物品")
    @app_commands.describe(user="你想給予物品的用戶", item_id="你想給予的物品ID")
    async def admin_give_item(self, interaction: discord.Interaction, user: discord.User, item_id: str):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("你沒有權限使用這個指令。", ephemeral=True)
            return
        
        receiver_id = user.id
        guild_id = interaction.guild.id if interaction.guild else None
        
        item = next((i for i in items if i["id"] == item_id), None)
        if not item:
            await interaction.response.send_message("無效的物品ID。", ephemeral=True)
            return
        
        receiver_items = get_user_data(guild_id, receiver_id, "items", [])
        receiver_items.append(item_id)
        set_user_data(guild_id, receiver_id, "items", receiver_items)
        
        await interaction.response.send_message(f"你給了 {user.name} 一個 {item['name']}。", ephemeral=True)
        
    @app_commands.command(name="remove", description="移除用戶的一個物品")
    @app_commands.describe(user="你想移除物品的用戶", item_id="你想移除的物品ID")
    async def admin_remove_item(self, interaction: discord.Interaction, user: discord.User, item_id: str):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("你沒有權限使用這個指令。", ephemeral=True)
            return
        
        receiver_id = user.id
        guild_id = interaction.guild.id if interaction.guild else None
        
        receiver_items = get_user_data(guild_id, receiver_id, "items", [])
        if item_id not in receiver_items:
            await interaction.response.send_message("該用戶沒有這個物品。", ephemeral=True)
            return
        
        receiver_items.remove(item_id)
        set_user_data(guild_id, receiver_id, "items", receiver_items)
        
        item = next((i for i in items if i["id"] == item_id), None)
        item_name = item['name'] if item else "未知物品"
        
        await interaction.response.send_message(f"你移除了 {user.name} 的一個 {item_name}。", ephemeral=True)
        
    @app_commands.command(name="list", description="列出所有可用的物品")
    async def admin_list_items(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("你沒有權限使用這個指令。", ephemeral=True)
            return
        
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
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("你沒有權限使用這個指令。", ephemeral=True)
            return

        guild_id = interaction.guild.id if interaction.guild else None
        user_items = get_user_data(guild_id, user.id, "items", [])

        if not user_items:
            await interaction.response.send_message(f"{user.name} 目前沒有任何物品。", ephemeral=True)
            return

        embed = discord.Embed(title=f"{user.name} 擁有的物品", color=0x00ff00)
        for item_id in user_items:
            item = next((i for i in items if i["id"] == item_id), None)
            if item:
                embed.add_field(name=item["name"], value=item["description"], inline=False)
        embed.set_footer(text=interaction.guild.name, icon_url=interaction.guild.icon.url if interaction.guild.icon else None)

        await interaction.response.send_message(embed=embed)

asyncio.run(bot.add_cog(ItemModerate()))


if __name__ == "__main__":
    start_bot()
