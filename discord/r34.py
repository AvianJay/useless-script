import discord
from discord.ext import commands
from discord import app_commands
import random
import requests
import json
import io
import time
from globalenv import bot, start_bot, config
from logger import log
import logging
if not config("r34_user_id") or not config("r34_api_key"):
    raise ValueError("r34_user_id or r34_api_key is not set in config.json")

caches = {}

def cache(key, value=None, expire_seconds=300):
    current_time = time.time()
    if value is not None:
        caches[key] = (value, current_time + expire_seconds)
    else:
        if key in caches:
            val, expire_time = caches[key]
            if current_time < expire_time:
                return val
            else:
                del caches[key]
        return None

def cache_request(tags=None, pid=1, expire_seconds=300):
    key = f"r34_{tags}_{pid}"
    cached = cache(key)
    if cached is not None:
        return cached
    if tags:
        tags = tags.replace(' ', '%20')
        r = requests.get(f'https://api.rule34.xxx/index.php?page=dapi&s=post&q=index&json=1&tags={tags}&pid={pid}&api_key={config("r34_api_key")}&user_id={config("r34_user_id")}')
    else:
        r = requests.get(f'https://api.rule34.xxx/index.php?page=dapi&s=post&q=index&json=1&pid={pid}&api_key={config("r34_api_key")}&user_id={config("r34_user_id")}')
    if not r.text:
        raise Exception('無搜尋結果')
    try:
        rj = r.json()
        cache(key, rj, expire_seconds)
        return rj
    except:
        log(f"Error fetching r34 data: {r.text}", module_name="r34", level=logging.ERROR)
        raise Exception(f'錯誤！{r.text}')


def r34(tags=None, pid=1, exclude_tags=None):
    try:
        rj = cache_request(tags, pid)
        if not rj:
            return False, '無搜尋結果'
        if exclude_tags:
            rj = [item for item in rj if not any(ex_tag in item.get('tags', '') for ex_tag in exclude_tags)]
        selected = random.choice(rj)
        return True, selected
    except Exception as e:
        return False, f'錯誤！{str(e)}'


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


async def r34_tags_autocomplete(interaction: discord.Interaction, current: str):
    # if not current:
    #     return []
    al = current.split()
    curr = al[-1]
    res = requests.get(f"https://api.rule34.xxx/autocomplete.php?q={curr}")
    al.pop()
    al = " ".join(al)
    return [app_commands.Choice(name=f"{al} {i['value']}", value=f"{al} {i['value']}") for i in res.json()]


@bot.tree.command(name="r34", description="從rule34.xxx隨機取得一張圖片", nsfw=True)
@app_commands.describe(tags="標籤", pid="頁數", spoilers="是否標記為暴雷內容", ai="是否包含AI生成的圖片")
@app_commands.choices(
    spoilers=[
        app_commands.Choice(name="是", value="True"),
        app_commands.Choice(name="否", value="False"),
    ],
    ai=[
        app_commands.Choice(name="是", value="True"),
        app_commands.Choice(name="否", value="False"),
    ]
)
@app_commands.autocomplete(tags=r34_tags_autocomplete)
async def r34_command(interaction: discord.Interaction, tags: str = None, pid: int = 1, spoilers: str = "False", ai: str = "True"):
    await interaction.response.defer()
    spoilers = (spoilers == "True")
    ai = (ai == "True")
    stat, img_data = r34(tags, pid, exclude_tags=["ai_generated"] if not ai else None)
    if not stat:
        embed = discord.Embed(title="錯誤", description=img_data, color=0xFF0000)
        await interaction.followup.send(embed=embed)
    else:
        embed = discord.Embed(
            title="Rule34.xxx",
            url=f"https://rule34.xxx/index.php?page=post&s=view&id={img_data.get('id', 'N/A')}",
            description=f"ID: `{img_data.get('id', 'N/A')}`\n共有 {len(img_data.get('tags', '').split())} 個標籤。",
            color=0x00FF00
        )
        if spoilers:
            attachment = discord.File(fp=io.BytesIO(requests.get(img_data.get('file_url', '')).content), filename="image.png", spoiler=True)
            embed.set_image(url="attachment://image.png")
        else:
            embed.set_image(url=img_data.get('file_url', ''))
        await interaction.followup.send(embed=embed, files=[attachment] if spoilers else [])


@bot.tree.command(name="r34tags", description="從rule34.xxx搜尋標籤", nsfw=True)
@app_commands.describe(query="搜尋關鍵字")
async def r34tags_command(interaction: discord.Interaction, query: str = None):
    await interaction.response.defer()
    tags = r34tags(query)
    await interaction.followup.send(tags)


if __name__ == "__main__":
    start_bot()
