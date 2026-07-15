import re
import aiohttp
import discord
from discord.ext import commands

HIDDEN_SECTION_PATTERN = re.compile(
    r'<!--\s*YABUS_RELEASE_DOWNLOAD_TABLE_START\s*-->.*?<!--\s*YABUS_RELEASE_DOWNLOAD_TABLE_END\s*-->',
    re.DOTALL,
)

def strip_hidden_sections(text):
    text = HIDDEN_SECTION_PATTERN.sub('', text)
    text = re.sub(r'(?:\r?\n){3,}', '\n\n', text)
    return text.strip()

class GitHub(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name='github', help='Get information about a GitHub user')
    async def github(self, ctx, username: str):
        url = f'https://api.github.com/users/{username}'
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    embed = discord.Embed(title=data['login'], url=data['html_url'], description=data.get('bio', 'No bio available'), color=0x7289DA)
                    embed.set_thumbnail(url=data['avatar_url'])
                    embed.add_field(name='Public Repos', value=data['public_repos'], inline=True)
                    embed.add_field(name='Followers', value=data['followers'], inline=True)
                    embed.add_field(name='Following', value=data['following'], inline=True)
                    await ctx.send(embed=embed)
                else:
                    await ctx.send(f'User "{username}" not found.')

    @commands.Cog.listener()
    async def on_message(self, message):
        import asyncio
        from urllib.parse import urlparse

        if message.webhook_id is None or not message.embeds:
            return

        embed = message.embeds[0]
        release_url = embed.url
        if not release_url:
            return

        parsed_url = urlparse(release_url)
        path_parts = [part for part in parsed_url.path.split('/') if part]
        if (
            parsed_url.netloc.lower() != 'github.com'
            or len(path_parts) < 5
            or path_parts[2] != 'releases'
            or path_parts[3] != 'tag'
        ):
            return

        owner, repo = path_parts[0], path_parts[1]
        tag = '/'.join(path_parts[4:])
        api_url = f'https://api.github.com/repos/{owner}/{repo}/releases/tags/{tag}'

        timeout = aiohttp.ClientTimeout(total=10)
        headers = {
            'Accept': 'application/vnd.github+json',
            'User-Agent': 'frogbot',
        }

        try:
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                async with session.get(api_url) as response:
                    if response.status != 200:
                        return
                    data = await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            return

        if not isinstance(data, dict):
            return

        title = data.get('name') or data.get('tag_name') or 'GitHub Release'
        description = strip_hidden_sections(data.get('body') or '') or 'No description available'
        if len(description) > 4096:
            description = f"{description[:4093].rstrip()}..."

        release_embed = discord.Embed(
            title=title[:256],
            url=data.get('html_url') or release_url,
            description=description,
            color=0x7289DA,
        )

        author = data.get('author') or {}
        release_embed.set_author(
            name=author.get('login') or 'GitHub',
            url=author.get('html_url') or discord.Embed.Empty,
            icon_url=author.get('avatar_url') or discord.Embed.Empty,
        )

        await message.channel.send(embed=release_embed)
