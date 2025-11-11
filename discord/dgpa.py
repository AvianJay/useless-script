from typing import List, Dict, Any, Optional
import requests
from bs4 import BeautifulSoup
import re
from datetime import datetime, timezone, timedelta
import discord
from discord.ext import commands
from discord import app_commands
from globalenv import bot, start_bot, modules, set_server_config, get_server_config
import asyncio
from logger import log
import logging
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
        m = re.search(r"更新時間[:：]\s*([\d/:\s]+)", h4.get_text())
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
    records: List[Dict[str, str]] = []
    if table:
        # 只取 tbody 內 tr（過濾說明列 colspan）
        for tr in table.find_all("tr"):
            # 跳過備註行（通常有 colspan 或 style 背景色）
            if tr.find(attrs={"colspan": True}) or tr.get("style"):
                continue
            tds = tr.find_all("td")
            if len(tds) < 2:
                continue
            city = tds[0].get_text(" ", strip=True)
            if "無停班停課訊息" in city:
                records = []  # 清空資料
                break
            status = _clean_text_from_cell(tds[1])
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
        resp = requests.get(url, headers=headers, timeout=timeout)
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
class nds(commands.GroupCog, description="天然災害停止上班及上課情形查詢"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="view", description="取得最新天然災害停止上班及上課情形")
    async def nds_command(self, interaction: discord.Interaction):
        await interaction.response.defer()  # 延遲回應
        try:
            result = fetch_and_parse_nds()
        except Exception as e:
            await interaction.followup.send(f"取得資料時發生錯誤：{e}")
            return

        embed = discord.Embed(title="天災停班停課情形")
        embed.color = discord.Color.blue()
        embed.timestamp = datetime.fromisoformat(result["fetched_at"])
        embed.footer.text = "上次更新時間"
        for record in result["data"]:
            city = record["city"]
            status = record["status"]
            embed.add_field(name=city, value=status or "無資料", inline=False)
        await interaction.followup.send(embed=embed)
        log(f"用戶查詢天然災害停止上班及上課情形。", module_name="nds", user=interaction.user, guild=interaction.guild)
    
    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    @app_commands.command(name="follow", description="追蹤天然災害停止上班及上課情形變更 (測試版)")
    @app_commands.describe(channel="要發送通知的頻道")
    async def nds_follow(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        if not interaction.is_guild_integration():
            await interaction.response.send_message("此指令只能在伺服器中使用。", ephemeral=True)
            return
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("你沒有權限使用此指令。", ephemeral=True)
            log(f"用戶 {interaction.user} 嘗試使用 nds follow 指令但沒有權限。", level=logging.WARNING, module_name="nds", user=interaction.user, guild=interaction.guild)
            return
        if channel:
            # check bot permissions
            if not channel.permissions_for(interaction.guild.me).send_messages:
                await interaction.response.send_message(f"我沒有在頻道 {channel.mention} 發送訊息的權限。", ephemeral=True)
                return
            set_server_config(interaction.guild_id, "nds_follow_channel_id", channel.id)
            await interaction.response.send_message(f"已設定在頻道 {channel.mention} 追蹤天然災害停止上班及上課情形變更通知。\n-# 測試版，功能尚未完善，請小心使用。")
            log(f"伺服器設定追蹤天然災害停止上班及上課情形通知，頻道 {channel.id}。", module_name="nds", user=interaction.user, guild=interaction.guild)
        else:
            set_server_config(interaction.guild_id, "nds_follow_channel_id", None)
            await interaction.response.send_message("已取消追蹤天然災害停止上班及上課情形變更通知。")
            log(f"伺服器取消追蹤天然災害停止上班及上課情形通知。", module_name="nds", user=interaction.user, guild=interaction.guild)
    
    async def _nds_monitor_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                data = fetch_and_parse_nds()
                log("取得資訊成功，檢查是否有更新。", module_name="nds")
                if data["update_time"] != (self._last_data.get("update_time") if self._last_data else None):
                    embed = discord.Embed(title="停班停課更新")
                    embed.color = discord.Color.blue()
                    embed.timestamp = datetime.fromisoformat(data["fetched_at"])
                    embed.footer.text = "上次更新時間"
                    for record in data["data"]:
                        # 相同的不重複發送
                        if (self._last_data and
                            any(r["city"] == record["city"] and r["status"] == record["status"] for r in self._last_data.get("data", []))):
                            continue
                        city = record["city"]
                        status = record["status"]
                        if "尚未列入警戒區" in status:
                            continue  # 忽略尚未列入警戒區的更新
                        embed.add_field(name=city, value=status or "無資料", inline=False)
                    # check field count
                    if len(embed.fields) != 0:
                        log("有更新，發送通知中...", module_name="nds")
                        for guild in self.bot.guilds:
                            channel_id = get_server_config(guild.id, "nds_follow_channel_id")
                            if channel_id:
                                channel = guild.get_channel(channel_id)
                                if channel and isinstance(channel, discord.TextChannel):
                                    await channel.send(embed=embed)
                    self._last_data = data
                await asyncio.sleep(60)  # 每 1 分鐘檢查一次
            except Exception as e:
                print(f"nds monitor loop error: {e}")
                log(f"nds monitor loop error: {e}", level=logging.ERROR, module_name="nds")
                await asyncio.sleep(60)  # 發生錯誤時稍等再試
    
    @commands.Cog.listener()
    async def on_ready(self):
        data = fetch_and_parse_nds()
        self._last_data = data
        self._nds_task = self.bot.loop.create_task(self._nds_monitor_loop())
        log("nds 模組已啟動。", module_name="nds")

asyncio.run(bot.add_cog(nds(bot)))

if __name__ == "__main__":
    start_bot()