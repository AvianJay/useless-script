import discord
from discord.ext import commands
from discord import app_commands
import random
import requests
import json
from globalenv import bot, start_bot, config
if not config("r34_user_id") or not config("r34_api_key"):
    raise ValueError("r34_user_id or r34_api_key is not set in config.json")


def r34(tags=None, pid=1):
    if tags:
        tags = tags.replace(' ', '%20')
        r = requests.get(f'https://api.rule34.xxx/index.php?page=dapi&s=post&q=index&json=1&tags={tags}&pid={pid}&api_key={config("r34_api_key")}&user_id={config("r34_user_id")}')
    else:
        r = requests.get(f'https://api.rule34.xxx/index.php?page=dapi&s=post&q=index&json=1&pid={pid}&api_key={config("r34_api_key")}&user_id={config("r34_user_id")}')
    try:
        rj = r.json()
        selected = random.choice(rj)
        return selected['file_url']
    except:
        return f'錯誤！{r.text}'


def r34tags(query=None):
    r = requests.get(f'https://api.rule34.xxx/index.php?page=dapi&s=tag&q=index&limit=999999&api_key={config("r34_api_key")}&user_id={config("r34_user_id")}')
    tags = []
    try:
        r1 = r.text.split('name="')
        r1.remove(r1[0])
        r2 = []
        for i in r1:
            r2.append(i.split('" ambiguous')[0])
        result = ''
        if query:
            for index, value in enumerate(r2):
                if index>10:
                    break
                if query in value or value.startswith(query) or value.endswith(query):
                    result = result + ' ' + value
        else:
            result = " ".join([random.choice(r2) for i in range(10)])
        if result == '':
            return '無搜尋結果'
        else:
            return result.strip()
    except:
        return '錯誤！'


@bot.tree.command(name="r34", description="從rule34.xxx隨機取得一張圖片", nsfw=True)
@app_commands.describe(tags="標籤", pid="頁數")
async def r34_command(interaction: discord.Interaction, tags: str = None, pid: int = 1):
    await interaction.response.defer()
    img_url = r34(tags, pid)
    await interaction.followup.send(img_url)


@bot.tree.command(name="r34tags", description="從rule34.xxx搜尋標籤", nsfw=True)
@app_commands.describe(query="搜尋關鍵字")
async def r34tags_command(interaction: discord.Interaction, query: str = None):
    await interaction.response.defer()
    tags = r34tags(query)
    await interaction.followup.send(tags)


if __name__ == "__main__":
    start_bot()
