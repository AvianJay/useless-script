from typing import List, Dict, Any, Optional
import requests
from bs4 import BeautifulSoup
import re
from datetime import datetime, timezone, timedelta
import discord
from discord.ext import commands
from discord import app_commands
from globalenv import bot, start_bot, modules, set_server_config, get_server_config, get_all_server_config_key
import asyncio
from logger import log
import logging
import i18n
from i18n import t
if "UtilCommands" in modules:
    from UtilCommands import version
else:
    version = "unknown"

DEFAULT_URL = "https://www.dgpa.gov.tw/typh/daily/nds.html"

TPE_TZ = timezone(timedelta(hours=8))  # 台北時區 (UTC+8)

def _clean_text_from_cell(td) -> str:
    """
    從 <td> 中擷取可見文字，保留換行（例如多項 <font> 或 <br> 分段）
    """
    parts = []
    for s in td.stripped_strings:
        # 移除過多空白與控制字
        text = re.sub(r'\s+', ' ', s).strip()
        if text:
            parts.append(text)
    return "\n".join(parts)

def parse_nds_html(html: str, source_url: Optional[str] = None) -> Dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")

    # header 日期（例如 "114年 11月 11日 天然災害停止上班及上課情形"）
    header_ymd = soup.select_one(".Header_YMD")
    date_text = header_ymd.get_text(" ", strip=True) if header_ymd else None

    # 更新時間 (找第一個 h4 裡面的 "更新時間：...")
    update_time = None
    h4 = soup.find("h4")
    if h4:
        m = re.search(r"更新時間[:：]\s*([\d/:\s]+)", h4.get_text())  # i18n: skip (matches scraped government site text)
        if m:
            update_time = m.group(1).strip()
            # try to parse to datetime
            try:
                dt = datetime.strptime(update_time, "%Y/%m/%d %H:%M:%S")
                dt = dt.replace(tzinfo=TPE_TZ)
                update_time = dt.isoformat()
            except ValueError:
                pass

    # 表格資料
    table = soup.find("table", id="Table")
    tbody = table.find("tbody")
    records: List[Dict[str, str]] = []
    if tbody:
        # 只取 tbody 內 tr（過濾說明列 colspan）
        for tr in tbody.find_all("tr"):
            # 跳過備註行（通常有 colspan 或 style 背景色）
            if tr.find(attrs={"colspan": True}):  # or tr.get("style"):
                continue
            tds = tr.find_all("td")
            if len(tds) < 2:
                continue
            if "city_Region" in tds[0].get("headers"):
                cityi = 1
                statusi = 2
            else:
                cityi = 0
                statusi = 1
            city = tds[cityi].get_text(" ", strip=True)
            if "無停班停課訊息" in city:  # i18n: skip (matches scraped government site text)
                records = []  # 清空資料
                break
            status = _clean_text_from_cell(tds[statusi])
            # 若 city 看起來像空白，跳過
            if not city:
                continue
            records.append({
                "city": city,
                "status": status
            })

    result = {
        "source_url": source_url or DEFAULT_URL,
        "fetched_at": datetime.now(TPE_TZ).isoformat(),  # ISO with timezone
        "page_date_text": date_text,
        "update_time": update_time,
        "count": len(records),
        "data": records
    }
    return result

def fetch_and_parse_nds(url: str = DEFAULT_URL, timeout: int = 10) -> Dict[str, Any]:
    """
    直接從官方頁面抓取並解析 JSON。
    回傳 dict（可直接用 json.dumps 序列化）。
    若網路或解析失敗會在 result 裡給出 error 字段。
    """
    headers = {
        "User-Agent": f"YeeBot/{version}"
    }
    try:
        resp = requests.get(url, headers=headers, timeout=timeout, verify=False)
        resp.raise_for_status()
        # 適當設定 encoding（官方通常 UTF-8，但以伺服器回傳或 chardet 為準）
        if not resp.encoding or resp.encoding.lower() in ("iso-8859-1", "ascii"):
            resp.encoding = resp.apparent_encoding
        html = resp.text
        parsed = parse_nds_html(html, source_url=url)
        return parsed
    except requests.RequestException as e:
        raise e
    except Exception as e:
        raise e


@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
class nds(
    commands.GroupCog,
    group_name=app_commands.locale_str("nds", i18n_key="cmd.dgpa.nds.root.name"),
    group_description=app_commands.locale_str(
        "Look up natural-disaster work and school suspensions",
        i18n_key="cmd.dgpa.nds.root.desc",
    ),
):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name=app_commands.locale_str("view", i18n_key="cmd.dgpa.nds.view.name"), description=app_commands.locale_str("Get the latest natural-disaster work/school suspension status", i18n_key="cmd.dgpa.nds.view.desc"))
    async def nds_command(self, interaction: discord.Interaction):
        await interaction.response.defer()  # 延遲回應
        try:
            result = fetch_and_parse_nds()
        except Exception as e:
            await interaction.followup.send(t("dgpa.err.fetch_failed", error=str(e)))
            return

        embed = discord.Embed(title=t("dgpa.embed.status_title"))
        embed.color = discord.Color.blue()
        embed.timestamp = datetime.fromisoformat(result["fetched_at"])
        embed.set_footer(text=t("dgpa.field.last_updated"))
        for record in result["data"]:
            city = record["city"]
            status = record["status"]
            embed.add_field(name=city, value=status or t("common.state.none"), inline=False)
        await interaction.followup.send(embed=embed)
        log(f"User queried natural-disaster work/school suspension status.", module_name="nds", user=interaction.user, guild=interaction.guild)
    
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    @app_commands.command(name=app_commands.locale_str("follow", i18n_key="cmd.dgpa.nds.follow.name"), description=app_commands.locale_str("Follow natural-disaster work/school suspension updates (beta)", i18n_key="cmd.dgpa.nds.follow.desc"))
    @app_commands.describe(channel=app_commands.locale_str("Channel to send notifications to", i18n_key="cmd.dgpa.nds.follow.param.channel"))
    async def nds_follow(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        if not interaction.is_guild_integration():
            await interaction.response.send_message(t("dgpa.err.guild_only"), ephemeral=True)
            return
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(t("dgpa.err.no_permission"), ephemeral=True)
            log(f"User {interaction.user} tried to use nds follow without permission.", level=logging.WARNING, module_name="nds", user=interaction.user, guild=interaction.guild)
            return
        if channel:
            # check bot permissions
            if not channel.permissions_for(interaction.guild.me).send_messages:
                await interaction.response.send_message(t("dgpa.err.no_channel_permission", channel=channel.mention), ephemeral=True)
                return
            set_server_config(interaction.guild_id, "nds_follow_channel_id", channel.id)
            await interaction.response.send_message(t("dgpa.msg.follow_enabled", channel=channel.mention))
            log(f"Server enabled following natural-disaster suspension notices in channel {channel.id}.", module_name="nds", user=interaction.user, guild=interaction.guild)
        else:
            set_server_config(interaction.guild_id, "nds_follow_channel_id", None)
            await interaction.response.send_message(t("dgpa.msg.follow_disabled"))
            log(f"Server disabled following natural-disaster suspension notices.", module_name="nds", user=interaction.user, guild=interaction.guild)
    
    async def _nds_monitor_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            servers_with_follow = get_all_server_config_key("nds_follow_channel_id")
            if not servers_with_follow:
                await asyncio.sleep(60)
                continue  # 沒有伺服器需要追蹤
            try:
                data = fetch_and_parse_nds()
                # log("Fetched info successfully, checking for updates.", module_name="nds")
                if data["update_time"] != (self._last_data.get("update_time") if self._last_data else None):
                    new_fields = []
                    for record in data["data"]:
                        # 相同的不重複發送
                        if (self._last_data and
                            any(r["city"] == record["city"] and r["status"] == record["status"] for r in self._last_data.get("data", []))):
                            continue
                        city = record["city"]
                        status = record["status"]
                        if "尚未列入警戒區" in status:  # i18n: skip (matches scraped government site text)
                            continue  # 忽略尚未列入警戒區的更新
                        new_fields.append((city, status))
                    # check field count
                    if new_fields:
                        log("Update detected, sending notifications...", module_name="nds")
                        fetched_at = datetime.fromisoformat(data["fetched_at"])
                        for guild_id, channel_id in servers_with_follow.items():
                            channel_id = get_server_config(guild_id, "nds_follow_channel_id")
                            if channel_id:
                                guild = self.bot.get_guild(guild_id)
                                if guild:
                                    channel = guild.get_channel(channel_id)
                                    if channel and isinstance(channel, discord.TextChannel):
                                        if channel.permissions_for(guild.me).send_messages:
                                            async with i18n.guild_scope(guild_id):
                                                embed = discord.Embed(title=t("dgpa.embed.update_title"))
                                                embed.color = discord.Color.blue()
                                                embed.timestamp = fetched_at
                                                embed.set_footer(text=t("dgpa.field.last_updated_time"))
                                                for city, status in new_fields:
                                                    embed.add_field(name=city, value=status or t("common.state.none"), inline=False)
                                            await channel.send(embed=embed)
                    self._last_data = data
            except Exception as e:
                print(f"nds monitor loop error: {e}")
                log(f"nds monitor loop error: {e}", level=logging.ERROR, module_name="nds")
            finally:
                await asyncio.sleep(180)  # 每 3 分鐘檢查一次
    
    @commands.Cog.listener()
    async def on_ready(self):
        data = fetch_and_parse_nds()
        self._last_data = data
        self._nds_task = self.bot.loop.create_task(self._nds_monitor_loop())
        log("nds module started.", module_name="nds")

asyncio.run(bot.add_cog(nds(bot)))

if __name__ == "__main__":
    start_bot()
