from globalenv import bot, config, on_close_tasks
from discord.ext import commands
import discord
from logger import log
import logging
import asyncio

class OfflineInteraction(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.endpoint = config("offline_interaction_endpoint", "")
        on_close_tasks.append(self.set_offline_interaction_endpoint)
    
    async def set_offline_interaction_endpoint(self):
        if self.endpoint:
            try:
                await bot.application.edit(interactions_endpoint_url=self.endpoint)
                log("已設定 Interactions endpoint URL 為 {}".format(self.endpoint), level=logging.INFO, module_name="OfflineInteraction")
            except Exception as e:
                log("設定 interactions endpoint URL 時發生錯誤: {}".format(e), level=logging.ERROR, module_name="OfflineInteraction")

    @commands.Cog.listener()
    async def on_ready(self):
        try:
            await bot.application.edit(interactions_endpoint_url=None)
            log("已清除 Interactions endpoint URL", level=logging.INFO, module_name="OfflineInteraction")
        except Exception as e:
            log("清除 interactions endpoint URL 時發生錯誤: {}".format(e), level=logging.ERROR, module_name="OfflineInteraction")

asyncio.run(bot.add_cog(OfflineInteraction(bot)))