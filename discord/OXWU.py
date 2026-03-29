# require unoffical oxwu api (https://github.com/AvianJay/useless-script/tree/main/oxwu/)
from globalenv import bot, set_server_config, get_server_config, config, on_close_tasks
import discord
from discord import app_commands
from discord.ext import commands
import aiohttp
import asyncio
import sys
from io import BytesIO
from typing import Optional
from logger import log
import logging
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone
from pathlib import Path

_LOCAL_OXWU_PACKAGE = Path(__file__).resolve().parent.parent / "oxwu"
if str(_LOCAL_OXWU_PACKAGE) not in sys.path:
    sys.path.append(str(_LOCAL_OXWU_PACKAGE))

import uooxwu

TAIWAN_TZ = timezone(timedelta(hours=8))

# 用於關閉時的清理
_oxwu_cog_instance = None

# CWA 快取
cwa_last_link: Optional[str] = None
cwa_last_image_url: Optional[str] = None

# CWA SSL context（跳過驗證，因為氣象署證書缺少 Subject Key Identifier）
import ssl
_cwa_ssl_context = ssl.create_default_context()
_cwa_ssl_context.check_hostname = False
_cwa_ssl_context.verify_mode = ssl.CERT_NONE

async def cwa_get_last_link() -> tuple[str, bool]:
    """取得最新的 CWA 報告連結，返回 (連結, 是否與上次相同)"""
    global cwa_last_link
    BASE_URL = "https://www.cwa.gov.tw"
    LIST_URL = "https://www.cwa.gov.tw/V8/C/E/MOD/EQ_ROW.html?T=" + str(int(datetime.now().timestamp()))
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(LIST_URL, ssl=_cwa_ssl_context) as resp:
                text = await resp.text()
                soup = BeautifulSoup(text, "html.parser")
                latest = soup.select_one("tr.eq-row a")
                if latest:
                    link = BASE_URL + latest["href"]
                    is_same = (link == cwa_last_link)
                    cwa_last_link = link
                    if config("debug"):
                        print(f"[DEBUG] CWA link: {link}, is_same: {is_same}")
                    return link, is_same
    except Exception as e:
        log(f"無法取得 CWA 連結: {e}", module_name="OXWU", level=logging.ERROR)
    return "", False

async def cwa_get_image_url(report_url: str) -> Optional[str]:
    """取得 CWA 報告的圖片 URL，並快取結果"""
    global cwa_last_image_url
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(report_url, ssl=_cwa_ssl_context) as resp:
                text = await resp.text()
                soup = BeautifulSoup(text, "html.parser")
                meta = soup.find("meta", property="og:image")
                if meta and meta.get("content"):
                    cwa_last_image_url = meta["content"]
                    return cwa_last_image_url
    except Exception as e:
        log(f"無法取得 CWA 圖片 URL: {e}", module_name="OXWU", level=logging.ERROR)
    return None

def cwa_get_cached_image_url() -> Optional[str]:
    """取得快取的 CWA 圖片 URL"""
    return cwa_last_image_url

def cwa_get_cached_link() -> Optional[str]:
    """取得快取的 CWA 報告連結"""
    return cwa_last_link

@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
class OXWU(commands.GroupCog, name="earthquake", description="OXWU 地震監測系統"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.api_url = config("oxwu_api") or "http://127.0.0.1:5000"
        self.api_key = config("oxwu_api_key", "")
        self.temp_channel_id = config("temp_channel_id")
        
        # 共用 aiohttp session（在 on_ready 初始化）
        self._session: Optional[aiohttp.ClientSession] = None
        
        # Socket.IO 客戶端
        self.proxy_client = uooxwu.Client(url=self.api_url, api_key=self.api_key)
        self.town_map = self.proxy_client.load_builtin_town_map()
        self._socket_task: Optional[asyncio.Task] = None
        self._socket_started = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        
        # 儲存最後的警報/報告資訊
        self.last_warning_time: Optional[str] = None
        self.last_report_time: Optional[str] = None
        
        # 註冊 Socket.IO 事件
        self._register_proxy_events()
        log(
            f"Configured OXWU proxy url={self.api_url} api_key={'set' if bool(self.api_key) else 'missing'}",
            module_name="OXWU",
            level=logging.INFO,
        )
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """取得共用的 aiohttp session"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session
    
    def _run_on_loop(self, coroutine):
        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(coroutine, self._loop)

    def _register_proxy_events(self):
        """註冊 proxy websocket 事件處理器"""
        @self.proxy_client.event()
        def proxy_warning_update(event: uooxwu.WarningUpdateEvent):
            self._run_on_loop(self._handle_warning_changed(event))

        @self.proxy_client.event()
        def proxy_warning_updated(event: uooxwu.WarningUpdatedEvent):
            self._run_on_loop(self._handle_warning_updated(event))
        
        @self.proxy_client.event()
        def proxy_report_update(event: uooxwu.ReportUpdateEvent):
            self._run_on_loop(self._handle_report_changed(event))
        
        @self.proxy_client.event()
        def connect():
            log("Proxy Socket.IO 已連線", module_name="OXWU", level=logging.INFO)
        
        @self.proxy_client.event()
        def disconnect():
            log("Proxy Socket.IO 已斷線", module_name="OXWU", level=logging.WARNING)
    
    async def _handle_warning_changed(self, data):
        """處理速報更新事件"""
        new_time = data.time if hasattr(data, "time") else data.get("time")
        if new_time and new_time != self.last_warning_time:
            self.last_warning_time = new_time
            # print(f"[OXWU] 收到新速報: {new_time}")
            log(f"收到新速報: {new_time}", module_name="OXWU", level=logging.INFO)

            # 上傳截圖
            screenshot_url = await self._upload_screenshot_to_temp("warning")
            
            # 取得詳細資訊
            info = self._build_warning_info_from_event(data)
            if info is not None:
                fetched_info = await self._fetch_warning_info()
                info = self._merge_warning_info(info, fetched_info)
            else:
                info = await self._fetch_warning_info()
            if info:
                embed = self._create_warning_embed(info, screenshot_url)
                await self._send_to_all_servers(embed, "oxwu_warning_channel")
    
    async def _handle_warning_updated(self, data):
        info = self._build_warning_info_from_event(data)
        if info is not None:
            fetched_info = await self._fetch_warning_info()
            info = self._merge_warning_info(info, fetched_info)
        else:
            info = await self._fetch_warning_info()

        if info:
            estimated_count = len(info.get("estimated_intensities") or {})
            arrival_count = len(info.get("arrival_times") or {})
            log(
                f"速報更新資料同步完成: arrival_times={arrival_count}, estimated_intensities={estimated_count}",
                module_name="OXWU",
                level=logging.INFO,
            )

    async def _handle_report_changed(self, data):
        """處理報告更新事件"""
        new_time = data.time if hasattr(data, "time") else data.get("time")
        if new_time and new_time != self.last_report_time:
            self.last_report_time = new_time
            # print(f"[OXWU] 收到新報告: {new_time}")
            log(f"收到新報告: {new_time}", module_name="OXWU", level=logging.INFO)

            # 上傳截圖
            screenshot_url = await self._upload_screenshot_to_temp("report")
            
            # 取得詳細資訊
            report = await self._fetch_report_info()
            if report:
                # 嘗試取得 CWA 圖片 URL（最多 6 次，間隔 10 秒）
                cwa_image_url = await self._fetch_cwa_image_with_retry()
                embed = self._create_report_embed(report, screenshot_url, cwa_image_url)
                # 建立連結按鈕
                view = None
                cached_link = cwa_get_cached_link()
                if cached_link and cwa_image_url:
                    view = discord.ui.View(timeout=None)
                    view.add_item(discord.ui.Button(label="中央氣象署報告", emoji="🌐", url=cached_link, style=discord.ButtonStyle.link))
                await self._send_to_all_servers(embed, "oxwu_report_channel", view=view)
    
    async def _fetch_cwa_image_with_retry(self, max_retries: int = 6, delay: float = 10.0) -> Optional[str]:
        """嘗試取得 CWA 圖片 URL，直到 is_same 為 False"""
        await asyncio.sleep(delay)
        for attempt in range(max_retries):
            try:
                link, is_same = await cwa_get_last_link()
                if not is_same and link:
                    image_url = await cwa_get_image_url(link)
                    if image_url:
                        log(f"成功取得 CWA 圖片 (第 {attempt + 1} 次嘗試)", module_name="OXWU", level=logging.INFO)
                        return image_url
                if attempt < max_retries - 1:
                    log(f"CWA 報告尚未更新，{delay} 秒後重試 ({attempt + 1}/{max_retries})", module_name="OXWU", level=logging.INFO)
                    await asyncio.sleep(delay)
            except Exception as e:
                log(f"取得 CWA 圖片失敗: {e}", module_name="OXWU", level=logging.ERROR)
                if attempt < max_retries - 1:
                    await asyncio.sleep(delay)
        log("無法取得 CWA 圖片，已達最大重試次數", module_name="OXWU", level=logging.WARNING)
        return None
    
    async def _fetch_screenshot(self, type_: str) -> Optional[bytes]:
        """從 proxy API 取得截圖"""
        try:
            return await asyncio.to_thread(self.proxy_client.get_screenshot, type_)
        except Exception as e:
            log(f"無法取得截圖: {e}", module_name="OXWU", level=logging.ERROR)
        return None
    
    async def _upload_screenshot_to_temp(self, type_: str) -> Optional[str]:
        """上傳截圖到臨時頻道並返回 URL"""
        channel_id = config("temp_channel_id")
        if not channel_id:
            log("未設定臨時頻道 ID，無法上傳截圖", module_name="OXWU", level=logging.WARNING)
            return None
        
        screenshot = await self._fetch_screenshot(type_)
        if not screenshot:
            log("無法取得截圖，無法上傳", module_name="OXWU", level=logging.WARNING)
            return None
        
        channel = self.bot.get_channel(int(channel_id))
        if not channel:
            log("無法找到臨時頻道，無法上傳截圖", module_name="OXWU", level=logging.WARNING)
            return None
        
        try:
            file = discord.File(BytesIO(screenshot), filename="oxwu_screenshot.png")
            msg = await channel.send(file=file)
            if msg.attachments:
                return msg.attachments[0].url
        except Exception as e:
            log(f"無法上傳截圖: {e}", module_name="OXWU", level=logging.ERROR)
        return None
    
    async def _fetch_warning_info(self) -> Optional[dict]:
        """取得地震速報資訊"""
        try:
            warning = await asyncio.to_thread(self.proxy_client.get_warning)
            if warning.ok:
                return warning.raw
        except Exception as e:
            log(f"無法取得速報資訊: {e}", module_name="OXWU", level=logging.ERROR)
        return None
    
    async def _fetch_report_info(self) -> Optional[dict]:
        """取得地震報告資訊"""
        try:
            report = await asyncio.to_thread(self.proxy_client.get_report)
            if report.ok:
                return report.raw.get("report")
        except Exception as e:
            log(f"無法取得報告資訊: {e}", module_name="OXWU", level=logging.ERROR)
        return None
    
    def _build_warning_info_from_event(self, data) -> Optional[dict]:
        warning = getattr(data, "warning", None)
        if warning is not None:
            info = dict(warning.raw or {})
        elif isinstance(data, dict):
            info = dict(data.get("data") or {})
        else:
            info = {}

        if not info:
            return None

        event_arrival_times = getattr(data, "arrival_times", None)
        event_estimated_intensities = getattr(data, "estimated_intensities", None)
        event_arrival_count = getattr(data, "arrival_count", None)
        event_arrival_generated_at = getattr(data, "arrival_generated_at", None)

        if isinstance(data, dict):
            if event_arrival_times is None:
                event_arrival_times = data.get("arrival_times")
            if event_estimated_intensities is None:
                event_estimated_intensities = data.get("estimated_intensities")
            if event_arrival_count is None:
                event_arrival_count = data.get("arrival_count")
            if event_arrival_generated_at is None:
                event_arrival_generated_at = data.get("arrival_generated_at")

        if event_arrival_times is not None:
            info["arrival_times"] = {str(k): int(v) for k, v in event_arrival_times.items()}
        elif warning is not None:
            info["arrival_times"] = {str(k): int(v) for k, v in warning.arrival_times.items()}

        if event_estimated_intensities is not None:
            info["estimated_intensities"] = {
                str(k): str(v) for k, v in event_estimated_intensities.items()
            }
        elif warning is not None:
            info["estimated_intensities"] = {
                str(k): str(v) for k, v in warning.estimated_intensities.items()
            }

        if event_arrival_count is not None:
            info["arrival_count"] = int(event_arrival_count or 0)
        elif warning is not None:
            info["arrival_count"] = int(warning.arrival_count or 0)

        if event_arrival_generated_at is not None:
            info["arrival_generated_at"] = event_arrival_generated_at
        elif warning is not None and warning.arrival_generated_at:
            info["arrival_generated_at"] = warning.arrival_generated_at

        return info

    def _merge_warning_info(self, event_info: Optional[dict], fetched_info: Optional[dict]) -> Optional[dict]:
        if event_info is None:
            return fetched_info
        if fetched_info is None:
            return event_info

        merged = dict(fetched_info)
        merged.update(event_info)

        if event_info.get("arrival_times"):
            merged["arrival_times"] = dict(event_info["arrival_times"])
        elif fetched_info.get("arrival_times"):
            merged["arrival_times"] = dict(fetched_info["arrival_times"])

        if event_info.get("estimated_intensities"):
            merged["estimated_intensities"] = dict(event_info["estimated_intensities"])
        elif fetched_info.get("estimated_intensities"):
            merged["estimated_intensities"] = dict(fetched_info["estimated_intensities"])

        if event_info.get("arrival_count") is not None:
            merged["arrival_count"] = event_info["arrival_count"]
        elif fetched_info.get("arrival_count") is not None:
            merged["arrival_count"] = fetched_info["arrival_count"]

        if event_info.get("arrival_generated_at"):
            merged["arrival_generated_at"] = event_info["arrival_generated_at"]
        elif fetched_info.get("arrival_generated_at"):
            merged["arrival_generated_at"] = fetched_info["arrival_generated_at"]

        return merged

    async def _goto_warning(self):
        """Proxy 模式不需要手動切頁。"""
        return None
    
    async def _goto_report(self):
        """Proxy 模式不需要手動切頁。"""
        return None

    def _build_arrival_lines(self, info: dict, limit: int = 10) -> Optional[str]:
        time_text = info.get("time")
        arrival_times = info.get("arrival_times") or {}
        estimated_intensities = info.get("estimated_intensities") or {}
        if not time_text or not estimated_intensities:
            return None

        try:
            base_time = datetime.strptime(time_text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TAIWAN_TZ)
        except ValueError:
            return None

        intensity_rank = {
            "0\u7d1a": 0,
            "1\u7d1a": 1,
            "2\u7d1a": 2,
            "3\u7d1a": 3,
            "4\u7d1a": 4,
            "5\u5f31": 5,
            "5\u5f37": 6,
            "6\u5f31": 7,
            "6\u5f37": 8,
            "7\u7d1a": 9,
        }
        county_summary = {}
        for town_id, intensity in estimated_intensities.items():
            town = self.town_map.get(str(town_id))
            town_name = town.name if town else str(town_id)
            county_name = town_name.split(" ", 1)[0]
            rank = intensity_rank.get(str(intensity), -1)
            eta_seconds = arrival_times.get(str(town_id))

            current = county_summary.get(county_name)
            if current is None:
                county_summary[county_name] = {
                    "intensity": intensity,
                    "rank": rank,
                    "eta_seconds": eta_seconds,
                }
                continue

            current_eta = current["eta_seconds"]
            should_replace = rank > current["rank"]
            if not should_replace and rank == current["rank"]:
                if eta_seconds is not None and (current_eta is None or eta_seconds < current_eta):
                    should_replace = True

            if should_replace:
                county_summary[county_name] = {
                    "intensity": intensity,
                    "rank": rank,
                    "eta_seconds": eta_seconds,
                }

        sorted_items = sorted(
            county_summary.items(),
            key=lambda item: (
                -item[1]["rank"],
                item[1]["eta_seconds"] if item[1]["eta_seconds"] is not None else 10**9,
                item[0],
            ),
        )

        lines = []
        for county_name, summary in sorted_items[:limit]:
            eta_seconds = summary["eta_seconds"]
            intensity = summary["intensity"]
            if eta_seconds is not None:
                arrival_dt = base_time + timedelta(seconds=int(eta_seconds))
                arrival_ts = int(arrival_dt.timestamp())
                lines.append(f"{county_name}: <t:{arrival_ts}:R> | \u9810\u4f30 {intensity}")
            else:
                lines.append(f"{county_name}: \u5df2\u62b5\u9054 | \u9810\u4f30 {intensity}")

        remaining = len(sorted_items) - limit
        if remaining > 0:
            lines.append(f"...\u9084\u6709 {remaining} \u500b\u5730\u5340")
        return "\n".join(lines)

    def _create_warning_embed(self, info: dict, screenshot_url: Optional[str] = None) -> discord.Embed:
        """建立速報 Embed"""
        embed = discord.Embed(
            title="⚠️ 地震速報",
            color=discord.Color.red(),
            timestamp=discord.utils.utcnow()
        )
        
        if info.get("time"):
            embed.add_field(name="🕐 發生時間", value=info["time"], inline=False)
        
        if info.get("location"):
            loc = info["location"]
            embed.add_field(name="📍 震央位置", value=loc.get("text", "未知"), inline=False)
        
        if info.get("depth"):
            embed.add_field(name="📏 深度", value=f"{info['depth']} km", inline=True)
        
        if info.get("magnitude"):
            embed.add_field(name="📊 規模", value=f"M {info['magnitude']}", inline=True)
        
        if info.get("maxIntensity"):
            embed.add_field(name="💥 最大震度", value=info["maxIntensity"], inline=True)
        
        arrival_lines = self._build_arrival_lines(info)
        if arrival_lines:
            embed.add_field(name="各地到達", value=arrival_lines, inline=False)

        if screenshot_url:
            embed.set_image(url=screenshot_url)
        
        # embed.set_footer(text="資料來源：OXWU")  # ahh
        return embed
    
    def _create_report_embed(self, report: dict, screenshot_url: Optional[str] = None, cwa_image_url: Optional[str] = None) -> discord.Embed:
        """建立報告 Embed"""
        embed = discord.Embed(
            title="📋 地震報告",
            color=discord.Color.orange(),
            timestamp=discord.utils.utcnow()
        )
        
        if report.get("number"):
            embed.add_field(name="📝 編號", value=report["number"], inline=True)
        
        if report.get("time"):
            embed.add_field(name="🕐 發生時間", value=report["time"], inline=False)
        
        if report.get("latitude") and report.get("longitude"):
            embed.add_field(
                name="📍 震央位置",
                value=f"北緯 {report['latitude']} / 東經 {report['longitude']}",
                inline=False
            )
        
        if report.get("depth"):
            embed.add_field(name="📏 深度", value=f"{report['depth']} km", inline=True)
        
        if report.get("magnitude"):
            embed.add_field(name="📊 規模", value=f"M {report['magnitude']}", inline=True)
        
        if report.get("maxIntensity"):
            embed.add_field(name="💥 最大震度", value=report["maxIntensity"], inline=True)
        
        # 各地震度（截斷過長的 field 避免超過 Discord 1024 字元限制）
        if report.get("intensities"):
            for area in report["intensities"]:
                stations_texts = []
                for station in area["stations"]:
                    names = "、".join(station["names"])
                    stations_texts.append(f'{station["level"]}級: {names}')
                stations_info = "\n".join(stations_texts)
                if len(stations_info) > 1024:
                    stations_info = stations_info[:1021] + "..."
                embed.add_field(name=f"📍 {area['area']} ({area['maxIntensity']})", value=stations_info, inline=False)
        
        # 優先使用 CWA 圖片，否則使用截圖
        if cwa_image_url:
            embed.set_image(url=cwa_image_url)
        elif screenshot_url:
            embed.set_image(url=screenshot_url)
        
        embed.set_footer(text="資料來源：中央氣象署")  # ahh
        return embed
    
    async def _send_to_all_servers(self, embed: discord.Embed, config_key: str, view: Optional[discord.ui.View] = None):
        """發送訊息到所有已設定的伺服器（含 429 避免機制）"""
        tasks = []
        for guild in self.bot.guilds:
            channel_id = get_server_config(guild.id, config_key)
            if channel_id:
                channel = self.bot.get_channel(int(channel_id))
                text_to_add = get_server_config(guild.id, f"{config_key}_text", "")
                if channel:
                    tasks.append((guild.name, channel, text_to_add))

        success_count = 0
        failed_count = 0

        # 批次發送，每批 5 個，間隔 0.5 秒
        batch_size = 5
        for i in range(0, len(tasks), batch_size):
            batch = tasks[i:i + batch_size]
            for guild_name, channel, text_to_add in batch:
                if await self._send_with_retry(channel, embed, guild_name, text_to_add, view=view):
                    success_count += 1
                else:
                    failed_count += 1
            # 批次間延遲
            if i + batch_size < len(tasks):
                await asyncio.sleep(0.5)
        log(f"訊息發送完成: {success_count} 成功, {failed_count} 失敗", module_name="OXWU", level=logging.INFO)
    
    async def _send_with_retry(self, channel, embed: discord.Embed, guild_name: str, text_to_add: str = "", view: Optional[discord.ui.View] = None, max_retries: int = 3):
        """發送訊息並在遇到 429 時重試"""
        for attempt in range(max_retries):
            try:
                await channel.send(content=text_to_add, embed=embed, view=view)
                return True
            except discord.HTTPException as e:
                if e.status == 429:
                    # 從 header 取得重試時間，或預設等待
                    retry_after = getattr(e, 'retry_after', 5)
                    # print(f"[OXWU] 429 限速中，{retry_after:.1f} 秒後重試 ({guild_name})")
                    log(f"429 限速中，{retry_after:.1f} 秒後重試 ({guild_name})", module_name="OXWU", level=logging.WARNING)
                    await asyncio.sleep(retry_after)
                else:
                    # print(f"[OXWU] 無法發送到 {guild_name}: {e}")
                    log(f"無法發送到 {guild_name}: {e}", module_name="OXWU", level=logging.ERROR)
                    return False
            except Exception as e:
                # print(f"[OXWU] 無法發送到 {guild_name}: {e}")
                log(f"無法發送到 {guild_name}: {e}", module_name="OXWU", level=logging.ERROR)
                return False
        # print(f"[OXWU] 重試次數已達上限，放棄發送到 {guild_name}")
        log(f"重試次數已達上限，放棄發送到 {guild_name}", module_name="OXWU", level=logging.ERROR)
        return False
    
    async def _connect_socketio(self):
        """連接到 proxy Socket.IO 伺服器"""
        while not self.bot.is_closed():
            try:
                await asyncio.to_thread(self.proxy_client.connect, wait=True)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log(
                    f"Proxy Socket.IO connection failed ({self.api_url}): {type(e).__name__}: {e}",
                    module_name="OXWU",
                    level=logging.ERROR,
                )
                await asyncio.sleep(10)
    
    @commands.Cog.listener()
    async def on_ready(self):
        """Bot 準備就緒時啟動 Socket.IO 連線與 CWA 初始化"""
        if not self._socket_started:
            self._socket_started = True
            self._loop = asyncio.get_running_loop()
            self._socket_task = self.bot.loop.create_task(self._connect_socketio())
            # 啟動時取得一次 CWA 連結和圖片
            try:
                await cwa_get_last_link()
                if cwa_last_link:
                    await cwa_get_image_url(cwa_last_link)
            except Exception as e:
                log(f"CWA 初始化失敗: {e}", module_name="OXWU", level=logging.WARNING)
    
    async def cog_unload(self):
        """Cog 卸載時清理資源"""
        if self._socket_task:
            self._socket_task.cancel()
        await asyncio.to_thread(self.proxy_client.close)
        if self._session and not self._session.closed:
            await self._session.close()
    
    # Slash Commands
    @app_commands.command(name="set-alert-channel", description="設定接收地震速報的頻道")
    @app_commands.describe(channel="要接收速報的頻道", text="可選的附加文字訊息")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.allowed_installs(guilds=True, users=False)
    async def set_warning_channel(self, interaction: discord.Interaction, channel: discord.TextChannel = None, text: str = ""):
        if not interaction.is_guild_integration():
            await interaction.response.send_message("❌ 此指令只能在伺服器中使用", ephemeral=True)
            return
        if interaction.user.guild_permissions.manage_guild is False:
            await interaction.response.send_message("❌ 你沒有權限使用此指令（需要管理伺服器權限）", ephemeral=True)
            return
        if channel:
            perms = channel.permissions_for(interaction.guild.me)
            if not (perms.view_channel and perms.send_messages):
                await interaction.response.send_message(f"❌ 機器人在 {channel.mention} 沒有檢視頻道或發送訊息的權限，請先調整後再設定。", ephemeral=True)
                return
            set_server_config(interaction.guild_id, "oxwu_warning_channel", str(channel.id))
            set_server_config(interaction.guild_id, "oxwu_warning_channel_text", text)
            await interaction.response.send_message(f"✅ 已設定速報頻道為 {channel.mention}", ephemeral=True)
        else:
            # 移除設定
            set_server_config(interaction.guild_id, "oxwu_warning_channel", None)
            set_server_config(interaction.guild_id, "oxwu_warning_channel_text", None)
            await interaction.response.send_message("✅ 已移除速報頻道設定", ephemeral=True)
    
    @app_commands.command(name="set-report-channel", description="設定接收地震報告的頻道")
    @app_commands.describe(channel="要接收報告的頻道", text="可選的附加文字訊息")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.allowed_installs(guilds=True, users=False)
    async def set_report_channel(self, interaction: discord.Interaction, channel: discord.TextChannel = None, text: str = ""):
        if not interaction.is_guild_integration():
            await interaction.response.send_message("❌ 此指令只能在伺服器中使用", ephemeral=True)
            return
        if interaction.user.guild_permissions.manage_guild is False:
            await interaction.response.send_message("❌ 你沒有權限使用此指令（需要管理伺服器權限）", ephemeral=True)
            return
        if channel:
            perms = channel.permissions_for(interaction.guild.me)
            if not (perms.view_channel and perms.send_messages):
                await interaction.response.send_message(f"❌ 機器人在 {channel.mention} 沒有檢視頻道或發送訊息的權限，請先調整後再設定。", ephemeral=True)
                return
            set_server_config(interaction.guild_id, "oxwu_report_channel", str(channel.id))
            set_server_config(interaction.guild_id, "oxwu_report_channel_text", text)
            await interaction.response.send_message(f"✅ 已設定報告頻道為 {channel.mention}", ephemeral=True)
        else:
            # 移除設定
            set_server_config(interaction.guild_id, "oxwu_report_channel", None)
            set_server_config(interaction.guild_id, "oxwu_report_channel_text", None)
            await interaction.response.send_message("✅ 已移除報告頻道設定", ephemeral=True)
    
    @app_commands.command(name="query-report", description="查詢最近一次的地震報告")
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.allowed_installs(guilds=True, users=False)
    async def query_report(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        report = await self._fetch_report_info()
        if not report:
            await interaction.followup.send("❌ 無法取得地震報告資訊", ephemeral=True)
            return
        
        # 上傳截圖
        screenshot_url = await self._upload_screenshot_to_temp("report")
        
        # 取得 CWA 圖片（查詢時不需重試，直接取得當前最新的）
        cached_link = cwa_get_cached_link()
        cwa_image_url = cwa_get_cached_image_url()
        
        embed = self._create_report_embed(report, screenshot_url, cwa_image_url)
        
        # 建立連結按鈕
        view = None
        if cached_link:
            view = discord.ui.View(timeout=None)
            view.add_item(discord.ui.Button(label="中央氣象署報告", emoji="🌐", url=cached_link, style=discord.ButtonStyle.link))
        
        await interaction.followup.send(embed=embed, view=view)
    
    @app_commands.command(name="query-warning", description="查詢目前的地震速報狀態")
    async def query_warning(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        info = await self._fetch_warning_info()
        if not info:
            await interaction.followup.send("❌ 無法取得地震速報資訊（可能目前沒有速報）", ephemeral=True)
            return
        
        # 上傳截圖
        screenshot_url = await self._upload_screenshot_to_temp("warning")
        
        embed = self._create_warning_embed(info, screenshot_url)
        await interaction.followup.send(embed=embed)
    
    @app_commands.command(name="screenshot", description="取得 OXWU 目前的畫面截圖")
    async def get_screenshot(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        screenshot = await self._fetch_screenshot("warning")
        if not screenshot:
            await interaction.followup.send("❌ 無法取得截圖", ephemeral=True)
            return
        
        file = discord.File(BytesIO(screenshot), filename="oxwu_screenshot.png")
        await interaction.followup.send(file=file)
    
    @app_commands.command(name="status", description="查看 OXWU 連線狀態")
    async def check_status(self, interaction: discord.Interaction):
        embed = discord.Embed(title="🔌 OXWU 連線狀態", color=discord.Color.blue())
        proxy_connected = bool(getattr(self.proxy_client, "_socket", None) and self.proxy_client._socket.connected)
        embed.add_field(name="Socket.IO", value="✅ 已連線" if proxy_connected else "❌ 未連線", inline=True)
        embed.add_field(name="Proxy API", value=self.api_url or "not set", inline=False)
        embed.add_field(name="API Key", value="configured" if self.api_key else "missing", inline=True)
        embed.add_field(name="最後速報時間", value=self.last_warning_time or "無", inline=True)
        embed.add_field(name="最後報告時間", value=self.last_report_time or "無", inline=True)
        
        # 在伺服器中才顯示頻道設定
        if interaction.guild_id:
            warning_ch = get_server_config(interaction.guild_id, "oxwu_warning_channel")
            report_ch = get_server_config(interaction.guild_id, "oxwu_report_channel")
            embed.add_field(
                name="本伺服器設定",
                value=f"速報頻道: {f'<#{warning_ch}>' if warning_ch else '未設定'}\n報告頻道: {f'<#{report_ch}>' if report_ch else '未設定'}",
                inline=False
            )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def _cleanup_oxwu():
    """關閉 OXWU 的 Socket.IO 連線和 aiohttp session"""
    global _oxwu_cog_instance
    if _oxwu_cog_instance is not None:
        try:
            await asyncio.to_thread(_oxwu_cog_instance.proxy_client.close)
            log("已關閉 Proxy Socket.IO 連線", module_name="OXWU")
            if _oxwu_cog_instance._session and not _oxwu_cog_instance._session.closed:
                await _oxwu_cog_instance._session.close()
        except Exception as e:
            log(f"關閉時發生錯誤: {e}", module_name="OXWU", level=logging.WARNING)


on_close_tasks.add(_cleanup_oxwu)

_oxwu_cog_instance = OXWU(bot)
asyncio.run(bot.add_cog(_oxwu_cog_instance))
