# require unoffical oxwu api (https://github.com/AvianJay/useless-script/tree/main/oxwu/)
from globalenv import bot, set_server_config, get_server_config, config
import discord
from discord import app_commands
from discord.ext import commands
import aiohttp
import asyncio
import socketio
from io import BytesIO
from typing import Optional
from logger import log
import logging

@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
class OXWU(commands.GroupCog, name="earthquake", description="OXWU 地震監測系統"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.api_url = config("oxwu_api") or "http://127.0.0.1:10281"
        self.temp_channel_id = config("temp_channel_id")
        
        # Socket.IO 客戶端
        self.sio = socketio.AsyncClient()
        
        # 儲存最後的警報/報告資訊
        self.last_warning_time: Optional[str] = None
        self.last_report_time: Optional[str] = None
        
        # 註冊 Socket.IO 事件
        self._register_sio_events()
    
    def _register_sio_events(self):
        """註冊 Socket.IO 事件處理器"""
        @self.sio.on("warningTimeChanged")
        async def on_warning_changed(data):
            await self._handle_warning_changed(data)
        
        @self.sio.on("reportTimeChanged")
        async def on_report_changed(data):
            await self._handle_report_changed(data)
        
        @self.sio.on("connect")
        async def on_connect():
            # print("[OXWU] Socket.IO 已連線")
            log("Socket.IO 已連線", module_name="OXWU", level=logging.INFO)
        
        @self.sio.on("disconnect")
        async def on_disconnect():
            # print("[OXWU] Socket.IO 已斷線")
            log("Socket.IO 已斷線", module_name="OXWU", level=logging.WARNING)
    
    async def _handle_warning_changed(self, data):
        """處理速報更新事件"""
        new_time = data.get("time")
        if new_time and new_time != self.last_warning_time:
            self.last_warning_time = new_time
            # print(f"[OXWU] 收到新速報: {new_time}")
            log(f"收到新速報: {new_time}", module_name="OXWU", level=logging.INFO)
            
            # 切換到速報頁面並等待一下
            await self._goto_warning()
            await asyncio.sleep(1)
            
            # 上傳截圖
            screenshot_url = await self._upload_screenshot_to_temp()
            
            # 取得詳細資訊
            info = await self._fetch_warning_info()
            if info:
                embed = self._create_warning_embed(info, screenshot_url)
                await self._send_to_all_servers(embed, "oxwu_warning_channel")
    
    async def _handle_report_changed(self, data):
        """處理報告更新事件"""
        new_time = data.get("time")
        if new_time and new_time != self.last_report_time:
            self.last_report_time = new_time
            # print(f"[OXWU] 收到新報告: {new_time}")
            log(f"收到新報告: {new_time}", module_name="OXWU", level=logging.INFO)
            
            # 切換到報告頁面並等待一下
            await self._goto_report()
            await asyncio.sleep(1)
            
            # 上傳截圖
            screenshot_url = await self._upload_screenshot_to_temp()
            
            # 取得詳細資訊
            report = await self._fetch_report_info()
            if report:
                embed = self._create_report_embed(report, screenshot_url)
                await self._send_to_all_servers(embed, "oxwu_report_channel")
    
    async def _fetch_screenshot(self) -> Optional[bytes]:
        """從 OXWU API 取得截圖"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.api_url}/screenshot") as resp:
                    if resp.status == 200:
                        return await resp.read()
        except Exception as e:
            # print(f"[OXWU] 無法取得截圖: {e}")
            log(f"無法取得截圖: {e}", module_name="OXWU", level=logging.ERROR)
        return None
    
    async def _upload_screenshot_to_temp(self) -> Optional[str]:
        """上傳截圖到臨時頻道並返回 URL"""
        channel_id = config("temp_channel_id")
        if not channel_id:
            print("[OXWU] 未設定臨時頻道 ID，無法上傳截圖")
            return None
        
        screenshot = await self._fetch_screenshot()
        if not screenshot:
            print("[OXWU] 無法取得截圖，無法上傳")
            return None
        
        channel = bot.get_channel(int(channel_id))
        if not channel:
            print("[OXWU] 無法找到臨時頻道，無法上傳截圖")
            return None
        
        try:
            file = discord.File(BytesIO(screenshot), filename="oxwu_screenshot.png")
            msg = await channel.send(file=file)
            if msg.attachments:
                return msg.attachments[0].url
        except Exception as e:
            # print(f"[OXWU] 無法上傳截圖: {e}")
            log(f"無法上傳截圖: {e}", module_name="OXWU", level=logging.ERROR)
        return None
    
    async def _fetch_warning_info(self) -> Optional[dict]:
        """取得地震速報資訊"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.api_url}/getWarningInfo") as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("ok"):
                            return data
        except Exception as e:
            # print(f"[OXWU] 無法取得速報資訊: {e}")
            log(f"無法取得速報資訊: {e}", module_name="OXWU", level=logging.ERROR)
        return None
    
    async def _fetch_report_info(self) -> Optional[dict]:
        """取得地震報告資訊"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.api_url}/getReportInfo") as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("ok"):
                            return data.get("report")
        except Exception as e:
            # print(f"[OXWU] 無法取得報告資訊: {e}")
            log(f"無法取得報告資訊: {e}", module_name="OXWU", level=logging.ERROR)
        return None
    
    async def _goto_warning(self):
        """切換到速報頁面"""
        try:
            async with aiohttp.ClientSession() as session:
                await session.get(f"{self.api_url}/gotoWarning")
        except Exception as e:
            # print(f"[OXWU] 無法切換到速報頁面: {e}")
            log(f"無法切換到速報頁面: {e}", module_name="OXWU", level=logging.ERROR)
    
    async def _goto_report(self):
        """切換到報告頁面"""
        try:
            async with aiohttp.ClientSession() as session:
                await session.get(f"{self.api_url}/gotoReport")
        except:
            pass
    
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
        
        if info.get("intensity"):
            embed.add_field(name="📈 預估震度", value=info["intensity"], inline=True)
        
        if info.get("eta"):
            embed.add_field(name="⏱️ 預估抵達", value=f"{info['eta']} 秒", inline=True)
        
        if screenshot_url:
            embed.set_image(url=screenshot_url)
        
        embed.set_footer(text="資料來源：OXWU")
        return embed
    
    def _create_report_embed(self, report: dict, screenshot_url: Optional[str] = None) -> discord.Embed:
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
        
        # 各地震度 (只顯示前幾個避免太長)
        if report.get("intensities"):
            intensity_text = []
            for area in report["intensities"][:10]:
                intensity_text.append(f"**{area.get('area', '未知')}**: {area.get('maxIntensity', '?')}")
            if intensity_text:
                embed.add_field(name="🗺️ 各地震度", value="\n".join(intensity_text), inline=False)
            if len(report["intensities"]) > 10:
                embed.add_field(name="", value=f"...還有 {len(report['intensities']) - 10} 個地區", inline=False)
        
        if screenshot_url:
            embed.set_image(url=screenshot_url)
        
        embed.set_footer(text="資料來源：OXWU")
        return embed
    
    async def _send_to_all_servers(self, embed: discord.Embed, config_key: str):
        """發送訊息到所有已設定的伺服器（含 429 避免機制）"""
        tasks = []
        for guild in self.bot.guilds:
            channel_id = get_server_config(guild.id, config_key)
            if channel_id:
                channel = self.bot.get_channel(int(channel_id))
                text_to_add = get_server_config(guild.id, f"{config_key}_text", "")
                if channel:
                    tasks.append((guild.name, channel, text_to_add))
        
        # 批次發送，每批 5 個，間隔 0.5 秒
        batch_size = 5
        for i in range(0, len(tasks), batch_size):
            batch = tasks[i:i + batch_size]
            for guild_name, channel, text_to_add in batch:
                await self._send_with_retry(channel, embed, guild_name, text_to_add)
            # 批次間延遲
            if i + batch_size < len(tasks):
                await asyncio.sleep(0.5)
    
    async def _send_with_retry(self, channel, embed: discord.Embed, guild_name: str, text_to_add: str = "", max_retries: int = 3):
        """發送訊息並在遇到 429 時重試"""
        for attempt in range(max_retries):
            try:
                await channel.send(content=text_to_add, embed=embed)
                return
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
                    return
            except Exception as e:
                # print(f"[OXWU] 無法發送到 {guild_name}: {e}")
                log(f"無法發送到 {guild_name}: {e}", module_name="OXWU", level=logging.ERROR)
                return
        # print(f"[OXWU] 重試次數已達上限，放棄發送到 {guild_name}")
        log(f"重試次數已達上限，放棄發送到 {guild_name}", module_name="OXWU", level=logging.ERROR)
    
    async def _connect_socketio(self):
        """連接到 Socket.IO 伺服器"""
        while True:
            try:
                if not self.sio.connected:
                    await self.sio.connect(self.api_url, transports=["polling"])
                await asyncio.sleep(5)
            except Exception as e:
                # print(f"[OXWU] Socket.IO 連線失敗: {e}")
                log(f"Socket.IO 連線失敗: {e}", module_name="OXWU", level=logging.ERROR)
                await asyncio.sleep(10)
    
    @commands.Cog.listener()
    async def on_ready(self):
        """Bot 準備就緒時啟動 Socket.IO 連線"""
        if not hasattr(self, "_task_started"):
            self._task_started = True
            self.bot.loop.create_task(self._connect_socketio())
    
    async def cog_unload(self):
        """Cog 卸載時斷開 Socket.IO 連線"""
        if self.sio.connected:
            await self.sio.disconnect()
    
    # Slash Commands
    @app_commands.command(name="set-alert-channel", description="設定接收地震速報的頻道")
    @app_commands.describe(channel="要接收速報的頻道", text="可選的附加文字訊息")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.allowed_installs(guilds=True, users=False)
    async def set_warning_channel(self, interaction: discord.Interaction, channel: discord.TextChannel, text: str = ""):
        if not interaction.is_guild_integration():
            await interaction.response.send_message("❌ 此指令只能在伺服器中使用", ephemeral=True)
            return
        if interaction.user.guild_permissions.manage_guild is False:
            await interaction.response.send_message("❌ 你沒有權限使用此指令（需要管理伺服器權限）", ephemeral=True)
            return
        set_server_config(interaction.guild_id, "oxwu_warning_channel", str(channel.id))
        set_server_config(interaction.guild_id, "oxwu_warning_channel_text", text)
        await interaction.response.send_message(f"✅ 已設定速報頻道為 {channel.mention}", ephemeral=True)
    
    @app_commands.command(name="set-report-channel", description="設定接收地震報告的頻道")
    @app_commands.describe(channel="要接收報告的頻道", text="可選的附加文字訊息")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.allowed_installs(guilds=True, users=False)
    async def set_report_channel(self, interaction: discord.Interaction, channel: discord.TextChannel, text: str = ""):
        if not interaction.is_guild_integration():
            await interaction.response.send_message("❌ 此指令只能在伺服器中使用", ephemeral=True)
            return
        if interaction.user.guild_permissions.manage_guild is False:
            await interaction.response.send_message("❌ 你沒有權限使用此指令（需要管理伺服器權限）", ephemeral=True)
            return
        set_server_config(interaction.guild_id, "oxwu_report_channel", str(channel.id))
        set_server_config(interaction.guild_id, "oxwu_report_channel_text", text)
        await interaction.response.send_message(f"✅ 已設定報告頻道為 {channel.mention}", ephemeral=True)
    
    @app_commands.command(name="query-report", description="查詢最近一次的地震報告")
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.allowed_installs(guilds=True, users=False)
    async def query_report(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        # 切換到報告頁面
        await self._goto_report()
        await asyncio.sleep(1)
        
        report = await self._fetch_report_info()
        if not report:
            await interaction.followup.send("❌ 無法取得地震報告資訊", ephemeral=True)
            return
        
        # 上傳截圖
        screenshot_url = await self._upload_screenshot_to_temp()
        
        embed = self._create_report_embed(report, screenshot_url)
        await interaction.followup.send(embed=embed)
    
    @app_commands.command(name="query-warning", description="查詢目前的地震速報狀態")
    async def query_warning(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        # 切換到速報頁面
        await self._goto_warning()
        await asyncio.sleep(.5)
        
        info = await self._fetch_warning_info()
        if not info:
            await interaction.followup.send("❌ 無法取得地震速報資訊（可能目前沒有速報）", ephemeral=True)
            return
        
        # 上傳截圖
        screenshot_url = await self._upload_screenshot_to_temp()
        
        embed = self._create_warning_embed(info, screenshot_url)
        await interaction.followup.send(embed=embed)
    
    @app_commands.command(name="screenshot", description="取得 OXWU 目前的畫面截圖")
    async def get_screenshot(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        screenshot = await self._fetch_screenshot()
        if not screenshot:
            await interaction.followup.send("❌ 無法取得截圖", ephemeral=True)
            return
        
        file = discord.File(BytesIO(screenshot), filename="oxwu_screenshot.png")
        await interaction.followup.send(file=file)
    
    @app_commands.command(name="status", description="查看 OXWU 連線狀態")
    async def check_status(self, interaction: discord.Interaction):
        embed = discord.Embed(title="🔌 OXWU 連線狀態", color=discord.Color.blue())
        # embed.add_field(name="API 位址", value=self.api_url, inline=False)
        embed.add_field(name="Socket.IO", value="✅ 已連線" if self.sio.connected else "❌ 未連線", inline=True)
        embed.add_field(name="最後速報時間", value=self.last_warning_time or "無", inline=True)
        embed.add_field(name="最後報告時間", value=self.last_report_time or "無", inline=True)
        
        # 檢查頻道設定
        warning_ch = get_server_config(interaction.guild_id, "oxwu_warning_channel")
        report_ch = get_server_config(interaction.guild_id, "oxwu_report_channel")
        embed.add_field(
            name="本伺服器設定",
            value=f"速報頻道: {f'<#{warning_ch}>' if warning_ch else '未設定'}\n報告頻道: {f'<#{report_ch}>' if report_ch else '未設定'}",
            inline=False
        )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)


asyncio.run(bot.add_cog(OXWU(bot)))