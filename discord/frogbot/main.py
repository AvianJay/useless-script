import asyncio
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

try:
    from .github import GitHub
except ImportError:
    from github import GitHub


class FrogBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(
            command_prefix=os.getenv('COMMAND_PREFIX', '!'),
            intents=intents,
        )

    async def setup_hook(self):
        await self.add_cog(GitHub(self))

    async def on_ready(self):
        if self.user is not None:
            print(f'Logged in as {self.user} ({self.user.id})')


async def main():
    load_dotenv()
    token = os.getenv('DISCORD_TOKEN') or os.getenv('TOKEN')
    if not token:
        raise RuntimeError('Missing DISCORD_TOKEN or TOKEN environment variable.')

    bot = FrogBot()
    async with bot:
        await bot.start(token)


if __name__ == '__main__':
    asyncio.run(main())
