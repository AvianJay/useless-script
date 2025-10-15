import discord
from discord import app_commands
from discord.ext import commands
from globalenv import bot, start_bot, on_ready_tasks
from taiwanbus import api as busapi
import asyncio


async def bus_route_autocomplete(interaction: discord.Interaction, current: str):
    routes = busapi.fetch_routes_by_name(current)
    return [
        app_commands.Choice(name=f"{route['route_name']} ({route['description']})", value=route['route_key'])
        for route in routes[:25]  # Discord autocomplete limit
    ]


@app_commands.guild_only()
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
class TWBus(commands.GroupCog, name=app_commands.locale_str("bus")):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        super().__init__()

    @app_commands.command(name=app_commands.locale_str("getroute"), description="查詢指定的路線")
    @app_commands.describe(route_key="路線ID")
    @app_commands.autocomplete(route_key=bus_route_autocomplete)
    async def get_route(self, interaction: discord.Interaction, route_key: str):
        await interaction.response.defer()
        try:
            info = busapi.get_complete_bus_info(route_key)
            route = busapi.fetch_route(route_key)
            if not info:
                await interaction.followup.send("找不到該路線的公車到站資訊。", ephemeral=True)
                return
            formated = busapi.format_bus_info(info)

            embed = discord.Embed(title=f"{route['route_name']} ({route['description']})", description=formated, color=0x00ff00)

            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(f"發生錯誤：{e}", ephemeral=True)
    
asyncio.run(bot.add_cog(TWBus(bot)))

async def on_ready_update_database():
    await bot.wait_until_ready()
    print("[+] 自動更新資料庫任務已啟動")
    while not bot.is_closed():
        busapi.update_database(info=True)
        await asyncio.sleep(3600)  # 每小時更新一次
on_ready_tasks.append(on_ready_update_database)

if __name__ == "__main__":
    start_bot()
