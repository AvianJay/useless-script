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
import i18n
from i18n import t
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
        raise Exception(t('r34.err.no_results'))
    try:
        rj = r.json()
        cache(key, rj, expire_seconds)
        return rj
    except:
        log(f"Error fetching r34 data: {r.text}", module_name="r34", level=logging.ERROR)
        raise Exception(t('r34.err.generic', error=r.text))


def r34(tags=None, pid=1, exclude_tags=None):
    try:
        rj = cache_request(tags, pid)
        if not rj:
            return False, t('r34.err.no_results')
        if exclude_tags:
            rj = [item for item in rj if not any(ex_tag in item.get('tags', '') for ex_tag in exclude_tags)]
        selected = random.choice(rj)
        return True, selected
    except Exception as e:
        return False, t('r34.err.generic', error=str(e))


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
            return t('r34.err.no_results')
        else:
            return result.strip()
    except:
        return t('r34.err.generic_bare')


async def r34_tags_autocomplete(interaction: discord.Interaction, current: str):
    # if not current:
    #     return []
    al = current.split()
    curr = al[-1]
    res = requests.get(f"https://api.rule34.xxx/autocomplete.php?q={curr}")
    al.pop()
    al = " ".join(al)
    return [app_commands.Choice(name=f"{al} {i['value']}", value=f"{al} {i['value']}") for i in res.json()]


@bot.tree.command(name=app_commands.locale_str("r34", i18n_key="cmd.r34.r34.name"), description=app_commands.locale_str("Get a random image from rule34.xxx", i18n_key="cmd.r34.r34.desc"), nsfw=True)
@app_commands.describe(tags=app_commands.locale_str("Tags", i18n_key="cmd.r34.r34.param.tags"), pid=app_commands.locale_str("Page number", i18n_key="cmd.r34.r34.param.pid"), spoilers=app_commands.locale_str("Mark as a spoiler", i18n_key="cmd.r34.r34.param.spoilers"), ai=app_commands.locale_str("Include AI-generated images", i18n_key="cmd.r34.r34.param.ai"))
@app_commands.choices(
    spoilers=[
        app_commands.Choice(name=app_commands.locale_str("Yes", i18n_key="cmd.r34.r34.choice.true"), value="True"),
        app_commands.Choice(name=app_commands.locale_str("No", i18n_key="cmd.r34.r34.choice.false"), value="False"),
    ],
    ai=[
        app_commands.Choice(name=app_commands.locale_str("Yes", i18n_key="cmd.r34.r34.choice.true"), value="True"),
        app_commands.Choice(name=app_commands.locale_str("No", i18n_key="cmd.r34.r34.choice.false"), value="False"),
    ]
)
@app_commands.autocomplete(tags=r34_tags_autocomplete)
async def r34_command(interaction: discord.Interaction, tags: str = None, pid: int = 1, spoilers: str = "False", ai: str = "True"):
    await interaction.response.defer()
    spoilers = (spoilers == "True")
    ai = (ai == "True")
    stat, img_data = r34(tags, pid, exclude_tags=["ai_generated"] if not ai else None)
    if not stat:
        embed = discord.Embed(title=t("r34.embed.error_title"), description=img_data, color=0xFF0000)
        await interaction.followup.send(embed=embed)
    else:
        ai_suffix = t("r34.value.ai_generated_suffix") if 'ai_generated' in img_data.get('tags', '') else ''
        embed = discord.Embed(
            title="Rule34.xxx",
            url=f"https://rule34.xxx/index.php?page=post&s=view&id={img_data.get('id', 'N/A')}",
            description=t("r34.value.tag_count_desc", count=len(img_data.get('tags', '').split()), id=img_data.get('id', 'N/A')) + ai_suffix,
            color=0x00FF00
        )
        if spoilers:
            attachment = discord.File(fp=io.BytesIO(requests.get(img_data.get('file_url', '')).content), filename="image.png", spoiler=True)
            embed.set_image(url="attachment://image.png")
        else:
            embed.set_image(url=img_data.get('file_url', ''))
        await interaction.followup.send(embed=embed, files=[attachment] if spoilers else [])


@bot.tree.command(name=app_commands.locale_str("r34tags", i18n_key="cmd.r34.r34tags.name"), description=app_commands.locale_str("Search tags on rule34.xxx", i18n_key="cmd.r34.r34tags.desc"), nsfw=True)
@app_commands.describe(query=app_commands.locale_str("Search keyword", i18n_key="cmd.r34.r34tags.param.query"))
async def r34tags_command(interaction: discord.Interaction, query: str = None):
    await interaction.response.defer()
    tags = r34tags(query)
    await interaction.followup.send(tags, allowed_mentions=discord.AllowedMentions.none())


if __name__ == "__main__":
    start_bot()
