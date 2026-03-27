import lava_lyra
import discord
from globalenv import bot, config, on_close_tasks, get_server_config, set_server_config, get_command_mention
from discord.ext import commands
from discord import app_commands
from logger import log
import logging
import asyncio
from typing import Optional, Any
from collections import deque
import random
from enum import Enum
import aiohttp
import html
import re
from dataclasses import dataclass
from urllib.parse import urlparse, quote

ALLOWED_DOMAINS = [
    "youtube.com",
    "youtu.be",
    "spotify.com",
    "soundcloud.com",
    "bilibili.com",
    "b23.tv",
    "bandcamp.com",
    "twitch.tv",
    "vimeo.com",
]

aiohttp.client_reqrep.ClientRequest.DEFAULT_HEADERS["Accept-Encoding"] = "gzip, deflate"


class LoopMode(Enum):
    """循環播放模式"""
    OFF = 0      # 不循環
    TRACK = 1    # 單曲循環
    QUEUE = 2    # 隊列循環


class MusicQueue:
    """自定義音樂隊列"""
    def __init__(self):
        self._queue: deque[lava_lyra.Track] = deque()
    
    def add(self, track: lava_lyra.Track):
        self._queue.append(track)
    
    def get(self) -> Optional[lava_lyra.Track]:
        if self._queue:
            return self._queue.popleft()
        return None
    
    def clear(self):
        self._queue.clear()
    
    @property
    def is_empty(self) -> bool:
        return len(self._queue) == 0
    
    def __len__(self) -> int:
        return len(self._queue)
    
    def __iter__(self):
        return iter(self._queue)


# 儲存每個伺服器的隊列和文字頻道
music_queues: dict[int, MusicQueue] = {}
text_channels: dict[int, discord.TextChannel] = {}
# 儲存自動離開的計時器任務
leave_timers: dict[int, asyncio.Task] = {}
# 儲存每個伺服器的循環模式
loop_modes: dict[int, LoopMode] = {}
radio_modes: dict[int, str] = {}


@dataclass(frozen=True)
class RadioStation:
    key: str
    display_name: str
    stream_url: str
    source: str
    image: str
    website: str


RADIO_STATIONS: dict[str, RadioStation] = {
    "listenmoe": RadioStation(
        key="listenmoe",
        display_name="LISTEN.moe",
        stream_url="https://listen.moe/fallback",
        source="listen.moe",
        image="https://listen.moe/images/android-chrome-512x512.png",
        website="https://listen.moe/",
    ),
    "r-a-dio": RadioStation(
        key="r-a-dio",
        display_name="R/a/dio",
        stream_url="https://relay1.r-a-d.io/main.mp3",
        source="r-a-d.io",
        image="https://r-a-d.io/assets/images/logo_image_small.png",
        website="https://r-a-d.io/",
    ),
}


def get_queue(guild_id: int) -> MusicQueue:
    """獲取伺服器的隊列，如果不存在則創建"""
    if guild_id not in music_queues:
        music_queues[guild_id] = MusicQueue()
    return music_queues[guild_id]

@app_commands.allowed_installs(guilds=True, users=False)
@app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
class Music(commands.GroupCog, group_name=app_commands.locale_str("music")):
    def __init__(self, bot):
        super().__init__()
        self.bot = bot
        self.node_names: dict[str, str] = {}  # identifier -> display name
        self._nodes_initialized = False
        self._http_session: Optional[aiohttp.ClientSession] = None
        self._radio_tasks: dict[str, asyncio.Task] = {}
        self._latest_radio_info: dict[str, dict[str, Any]] = {}
        self._radio_info_events: dict[str, asyncio.Event] = {
            station_key: asyncio.Event() for station_key in RADIO_STATIONS
        }
        self._radio_last_announced: dict[int, str] = {}
        self._notification_tasks: set[asyncio.Task] = set()
    
    async def _ensure_voice(self, ctx: commands.Context) -> Optional[lava_lyra.Player]:
        """確保使用者在語音頻道並返回播放器"""
        if not ctx.author.voice or not ctx.author.voice.channel:
            await ctx.send("❌ 你必須加入語音頻道才能使用此指令")
            return None
        
        player: lava_lyra.Player = ctx.guild.voice_client
        if player and player.channel:
            if ctx.author.voice.channel.id != player.channel.id:
                await ctx.send("❌ 你必須與機器人在同一語音頻道才能使用此指令")
                return None
        
        if not player:
            try:
                player = await ctx.author.voice.channel.connect(cls=lava_lyra.Player)
                text_channels[ctx.guild.id] = ctx.channel
            except Exception as e:
                await ctx.send(f"❌ 無法連接到語音頻道: {e}")
                return None
        return player
    
    def _check_voice_channel(self, user: discord.Member, guild: discord.Guild) -> Optional[str]:
        """檢查用戶是否與機器人在同一語音頻道，返回錯誤訊息或 None"""
        player: lava_lyra.Player = guild.voice_client
        if player and player.channel:
            if not user.voice or not user.voice.channel:
                return "❌ 你必須加入語音頻道才能使用此指令"
            if user.voice.channel.id != player.channel.id:
                return "❌ 你必須與機器人在同一語音頻道才能使用此指令"
        return None
    
    async def _get_http_session(self) -> aiohttp.ClientSession:
        if self._http_session is None or self._http_session.closed:
            timeout = aiohttp.ClientTimeout(
                total=None,
                connect=15,
                sock_connect=15,
                sock_read=None,
            )
            self._http_session = aiohttp.ClientSession(
                timeout=timeout,
                headers={"User-Agent": "Mozilla/5.0"},
            )
        return self._http_session

    def _start_background_task(self, station_key: str, coro):
        task = asyncio.create_task(coro)
        self._radio_tasks[station_key] = task

        def _cleanup(done_task: asyncio.Task):
            if self._radio_tasks.get(station_key) is done_task:
                self._radio_tasks.pop(station_key, None)

        task.add_done_callback(_cleanup)
        return task

    def _get_station(self, station_key: str) -> Optional[RadioStation]:
        return RADIO_STATIONS.get(station_key)

    def _guild_has_active_radio_station(self, guild: discord.Guild, station_key: str) -> bool:
        if radio_modes.get(guild.id) != station_key:
            return False

        player: lava_lyra.Player = guild.voice_client
        if not player or not player.channel:
            return False

        return bool(player.is_playing)

    def _has_active_radio_station(self, station_key: str) -> bool:
        return any(self._guild_has_active_radio_station(guild, station_key) for guild in self.bot.guilds)

    def _ensure_radio_listener(self, station_key: str):
        if not self._has_active_radio_station(station_key):
            return

        task = self._radio_tasks.get(station_key)
        if task and not task.done():
            return

        loop_map = {
            "listenmoe": self._listen_moe_loop,
            "r-a-dio": self._r_a_dio_loop,
        }
        loop_factory = loop_map.get(station_key)
        if loop_factory:
            self._start_background_task(station_key, loop_factory())

    async def _stop_radio_listener_if_unused(self, station_key: str):
        if self._has_active_radio_station(station_key):
            return

        task = self._radio_tasks.pop(station_key, None)
        if not task:
            return

        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def _refresh_radio_listeners(self):
        for station_key in RADIO_STATIONS:
            if self._has_active_radio_station(station_key):
                self._ensure_radio_listener(station_key)
            else:
                await self._stop_radio_listener_if_unused(station_key)

    def _spawn_notification_task(self, coro):
        task = asyncio.create_task(coro)
        self._notification_tasks.add(task)
        task.add_done_callback(self._notification_tasks.discard)
        return task

    def _get_guild_radio_station(self, guild_id: int) -> Optional[RadioStation]:
        station_key = radio_modes.get(guild_id)
        if not station_key:
            return None
        return self._get_station(station_key)

    def _is_radio_mode(self, guild_id: int) -> bool:
        return guild_id in radio_modes

    async def _ensure_not_radio_mode(self, target, guild_id: int) -> bool:
        station = self._get_guild_radio_station(guild_id)
        if not station:
            return True

        message = f"目前正在使用 {station.display_name} 電台模式，不能新增歌曲或修改隊列。請先停止播放後再使用。"
        if isinstance(target, discord.Interaction):
            await target.followup.send(message, ephemeral=True)
        else:
            await target.send(message)
        return False

    def _set_radio_info(self, station_key: str, info: dict[str, Any]):
        if not info:
            return

        previous_signature = self._get_radio_signature(self._latest_radio_info.get(station_key, {}))
        self._latest_radio_info[station_key] = info

        if not self._is_valid_radio_info(info):
            return

        self._radio_info_events[station_key].set()
        signature = self._get_radio_signature(info)
        if signature and signature != previous_signature:
            self._spawn_notification_task(self._broadcast_radio_update(station_key, signature))

    def _get_radio_info(self, station_key: str) -> dict[str, Any]:
        return self._latest_radio_info.get(station_key, {})

    def _is_valid_radio_info(self, info: dict[str, Any]) -> bool:
        return bool(info.get("title") or info.get("display"))

    def _get_radio_signature(self, info: dict[str, Any]) -> Optional[str]:
        if not self._is_valid_radio_info(info):
            return None
        return f"{info.get('artist', '')}|{info.get('title', '')}|{info.get('display', '')}"

    async def _wait_for_valid_radio_info(self, station_key: str, timeout: float = 10.0) -> Optional[dict[str, Any]]:
        current = self._get_radio_info(station_key)
        if self._is_valid_radio_info(current):
            return current

        event = self._radio_info_events[station_key]
        event.clear()
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

        updated = self._get_radio_info(station_key)
        if self._is_valid_radio_info(updated):
            return updated
        return None

    async def _broadcast_radio_update(self, station_key: str, signature: str):
        station = self._get_station(station_key)
        if not station:
            return

        embed = self._build_radio_embed(station)
        for guild in self.bot.guilds:
            if not self._guild_has_active_radio_station(guild, station_key):
                continue
            if self._radio_last_announced.get(guild.id) == signature:
                continue

            text_channel = text_channels.get(guild.id)
            if not text_channel:
                continue

            try:
                await text_channel.send(embed=embed)
                self._radio_last_announced[guild.id] = signature
            except Exception as e:
                log(f"發送電台換曲通知失敗: {e}", level=logging.WARNING, module_name="Music", guild=guild)

    def _parse_listen_moe_payload(self, payload: dict[str, Any]) -> Optional[dict[str, Any]]:
        song = payload.get("song") or {}
        if not song:
            return None

        title = song.get("title") or "Unknown Title"
        artists = song.get("artists") or []
        artist_names = [artist.get("nameRomaji") or artist.get("name") for artist in artists if artist.get("nameRomaji") or artist.get("name")]
        artist_text = ", ".join(artist_names) if artist_names else "Unknown Artist"
        artist_image = artists[0].get("image") if artists else None
        artist_url = f"https://listen.moe/artists/{artists[0].get('id')}" if artists else None
        if artist_image:
            artist_image = f"https://cdn.listen.moe/avatars/{quote(artist_image)}"

        sources = song.get("sources") or []
        source = sources[0] if sources else {}
        source_name = source.get("nameRomaji") or source.get("name")

        album_image = None
        albums = song.get("albums") or []
        album = albums[0] if albums else None
        album_name = album.get("nameRomaji") or album.get("name") if album else None
        if album.get("image"):
            album_image = f"https://cdn.listen.moe/covers/{quote(album.get('image'))}"

        duration = song.get("duration") or 0

        return {
            "title": title,
            "artist": artist_text,
            "artist_image": artist_image,
            "artist_url": artist_url,
            "display": f"{artist_text} - {title}",
            "album": album_name,
            "source_name": source_name,
            "thumbnail": album_image,
            "duration": duration * 1000 if duration else 0,
            "start_time": payload.get("startTime"),
            "url": "https://listen.moe/",
            "station": "LISTEN.moe",
        }

    def _parse_r_a_dio_metadata_html(self, raw_html: str) -> dict[str, Any]:
        info: dict[str, Any] = {
            "station": "r-a-d.io",
            "url": "https://r-a-d.io/",
        }

        title_match = re.search(r'<div id="metadata"[^>]*>(.*?)</div>', raw_html, re.S)
        if title_match:
            display = html.unescape(re.sub(r"<[^>]+>", "", title_match.group(1))).strip()
            if display:
                info["display"] = display
                if " - " in display:
                    artist, title = display.split(" - ", 1)
                    info["artist"] = artist.strip()
                    info["title"] = title.strip()
                else:
                    info["title"] = display

        tags_match = re.search(r'<div id="now-playing-tags"[^>]*>(.*?)</div>', raw_html, re.S)
        if tags_match:
            # as album
            album = html.unescape(re.sub(r"<[^>]+>", "", tags_match.group(1))).strip()
            info["album"] = album


        listeners_match = re.search(r'listener-count">(\d+)</span>', raw_html)
        if listeners_match:
            info["listeners"] = int(listeners_match.group(1))

        progress_match = re.search(r'<span id="progress-current"[^>]*>(.*?)</span>\s*/\s*<span id="progress-max">(.*?)</span>', raw_html, re.S)
        if progress_match:
            info["progress_text"] = f"{html.unescape(progress_match.group(1)).strip()} / {html.unescape(progress_match.group(2)).strip()}"

        return info

    def _build_radio_embed(self, station: RadioStation) -> discord.Embed:
        info = self._get_radio_info(station.key)
        display = info.get("display") or "正在抓取電台資訊..."

        embed = discord.Embed(
            title=f"📻 {info.get('title') or station.display_name}",
            description=info.get("album") or display,
            color=0x3498db,
            url=info.get("url") or station.website
        )
        embed.set_author(name=info.get("artist", station.display_name), url=info.get("artist_url"), icon_url=info.get("artist_image"))
        embed.set_footer(text=station.display_name, icon_url=station.image)
        # embed.add_field(name="模式", value="電台模式", inline=True)
        # embed.add_field(name="來源", value=station.source, inline=True)

        # if info.get("artist"):
        #     embed.add_field(name="歌手", value=info["artist"], inline=True)
        # if info.get("title"):
        #     embed.add_field(name="歌曲", value=info["title"], inline=True)
        # if info.get("source_name"):
        #     embed.add_field(name="作品", value=info["source_name"], inline=True)
        if info.get("listeners") is not None:
            embed.add_field(name="聽眾數量", value=str(info["listeners"]), inline=True)
        if info.get("progress_text"):
            embed.add_field(name="進度", value=info["progress_text"], inline=False)
        if info.get("thumbnail"):
            embed.set_thumbnail(url=info["thumbnail"])
        # embed.set_footer(text="電台模式下不能新增歌曲或使用隊列功能")
        return embed

    async def _play_radio_stream(self, player: lava_lyra.Player, station: RadioStation):
        results = await player.get_tracks(station.stream_url)
        if not results:
            raise RuntimeError(f"無法載入 {station.display_name} 串流")

        track = results.tracks[0] if isinstance(results, lava_lyra.Playlist) else results[0]
        await player.play(track)

    async def _activate_radio_mode(self, guild: discord.Guild, channel: discord.abc.Messageable, player: lava_lyra.Player, station: RadioStation):
        guild_id = guild.id
        previous_station = radio_modes.get(guild_id)
        get_queue(guild_id).clear()
        loop_modes[guild_id] = LoopMode.OFF
        radio_modes[guild_id] = station.key
        self._radio_last_announced.pop(guild_id, None)
        text_channels[guild_id] = channel
        await self._play_radio_stream(player, station)
        self._ensure_radio_listener(station.key)
        if previous_station and previous_station != station.key:
            await self._stop_radio_listener_if_unused(previous_station)

    async def _listen_moe_loop(self):
        while self._has_active_radio_station("listenmoe"):
            try:
                session = await self._get_http_session()
                async with session.ws_connect("wss://listen.moe/gateway_v2") as ws:
                    heartbeat_task: Optional[asyncio.Task] = None
                    try:
                        async for msg in ws:
                            if msg.type != aiohttp.WSMsgType.TEXT:
                                continue

                            data = msg.json()
                            op = data.get("op")
                            if op == 0:
                                if heartbeat_task:
                                    heartbeat_task.cancel()
                                interval = max((data.get("d", {}).get("heartbeat") or 35000) / 1000, 5)

                                async def _heartbeat():
                                    while True:
                                        await asyncio.sleep(interval)
                                        await ws.send_json({"op": 9})

                                heartbeat_task = asyncio.create_task(_heartbeat())
                                await ws.send_json({"op": 9})
                                continue

                            if op != 1:
                                continue

                            payload = data.get("d", {})
                            parsed = self._parse_listen_moe_payload(payload)
                            if parsed:
                                self._set_radio_info("listenmoe", parsed)
                    finally:
                        if heartbeat_task:
                            heartbeat_task.cancel()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log(
                    f"LISTEN.moe websocket disconnected: {type(e).__name__}: {e!r}",
                    level=logging.WARNING,
                    module_name="Music",
                )
                if not self._has_active_radio_station("listenmoe"):
                    break
                await asyncio.sleep(5)

    async def _r_a_dio_loop(self):
        while self._has_active_radio_station("r-a-dio"):
            try:
                session = await self._get_http_session()
                async with session.get(
                    "https://r-a-d.io/v1/sse?theme=default-dark",
                    headers={"Accept": "text/event-stream"},
                ) as response:
                    event_name = None
                    data_lines: list[str] = []
                    async for raw_line in response.content:
                        line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                        if line.startswith("event:"):
                            event_name = line.split(":", 1)[1].strip()
                            continue
                        if line.startswith("data:"):
                            data_lines.append(line.split(":", 1)[1].lstrip())
                            continue
                        if line != "":
                            continue

                        if event_name == "metadata":
                            parsed = self._parse_r_a_dio_metadata_html("\n".join(data_lines))
                            self._set_radio_info("r-a-dio", parsed)
                        elif event_name == "listeners":
                            match = re.search(r"(\d+)", "\n".join(data_lines))
                            if match:
                                current = self._get_radio_info("r-a-dio").copy()
                                current["listeners"] = int(match.group(1))
                                self._set_radio_info("r-a-dio", current)

                        event_name = None
                        data_lines = []
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log(
                    f"r-a-d.io eventstream disconnected: {type(e).__name__}: {e!r}",
                    level=logging.WARNING,
                    module_name="Music",
                )
                if not self._has_active_radio_station("r-a-dio"):
                    break
                await asyncio.sleep(5)

    def _check_valid_query(query: str) -> bool:
        if not query:
            return False

        # 檢查是否 http(s) 開頭
        if not (query.startswith("http://") or query.startswith("https://")):
            return True  # 非 URL 類型的查詢，直接當作搜尋詞使用

        try:
            parsed = urlparse(query)
            domain = parsed.netloc.lower()

            # 移除 port (ex: youtube.com:443)
            domain = domain.split(":")[0]

            # 檢查是否在允許清單內（包含子網域）
            return any(
                domain == d or domain.endswith("." + d)
                for d in ALLOWED_DOMAINS
            )

        except Exception:
            return False

    @commands.Cog.listener()
    async def on_ready(self):
        """初始化 Lavalink 節點"""
        if self._nodes_initialized:
            return
        self._nodes_initialized = True
        
        lavalink_nodes = config("lavalink_nodes", [])
        if not lavalink_nodes:
            log("未設定任何 Lavalink 節點，請在 config.json 中設定 lavalink_nodes", level=logging.ERROR, module_name="Music")
            return
        
        connected = 0
        for i, node_config in enumerate(lavalink_nodes):
            identifier = node_config.get("id", f"NODE_{i}")
            display_name = node_config.get("name", identifier)
            try:
                await lava_lyra.NodePool.create_node(
                    bot=self.bot,
                    host=node_config.get("host", "localhost"),
                    port=node_config.get("port", 2333),
                    password=node_config.get("password", "youshallnotpass"),
                    identifier=identifier,
                    lyrics=False,
                    search=True,
                    fallback=True,
                    secure=node_config.get("secure", False),
                )
                self.node_names[identifier] = display_name
                connected += 1
                log(f"已創建 Lavalink 節點: {display_name} ({node_config.get('host')}:{node_config.get('port')})", module_name="Music")
            except Exception as e:
                log(f"無法連接到 Lavalink 節點 {display_name}: {e}", level=logging.ERROR, module_name="Music")
        
        if connected == 0:
            log("所有 Lavalink 節點均無法連接", level=logging.ERROR, module_name="Music")
        else:
            log(f"已成功連接 {connected}/{len(lavalink_nodes)} 個 Lavalink 節點", module_name="Music")
        on_close_tasks.add(self.music_quit_task)
        on_close_tasks.add(self._shutdown_radio_tasks)

    async def _shutdown_radio_tasks(self):
        for task in list(self._radio_tasks.values()):
            task.cancel()
        if self._radio_tasks:
            await asyncio.gather(*self._radio_tasks.values(), return_exceptions=True)
        self._radio_tasks.clear()

        for task in list(self._notification_tasks):
            task.cancel()
        if self._notification_tasks:
            await asyncio.gather(*self._notification_tasks, return_exceptions=True)
        self._notification_tasks.clear()

        if self._http_session and not self._http_session.closed:
            await self._http_session.close()
        self._http_session = None
    
    async def _cleanup_player(self, guild_id: int, send_message: bool = False, message: str = None):
        """統一的清理方法"""
        try:
            queue = get_queue(guild_id)
            queue.clear()
            radio_station = radio_modes.get(guild_id)

            # 取消自動離開計時器
            if guild_id in leave_timers:
                leave_timers[guild_id].cancel()
                leave_timers.pop(guild_id, None)

            # 發送通知
            if send_message and message:
                text_channel = text_channels.get(guild_id)
                if text_channel:
                    try:
                        embed = discord.Embed(
                            title="👋 已離開語音頻道",
                            description=message,
                            color=0x95a5a6
                        )
                        await text_channel.send(embed=embed)
                    except Exception as e:
                        log(f"無法發送通知: {e}", level=logging.WARNING, module_name="Music")

            # 清理資源
            music_queues.pop(guild_id, None)
            text_channels.pop(guild_id, None)
            loop_modes.pop(guild_id, None)
            radio_modes.pop(guild_id, None)
            self._radio_last_announced.pop(guild_id, None)
            if radio_station:
                await self._stop_radio_listener_if_unused(radio_station)

        except Exception as e:
            log(f"清理播放器時出錯: {e}", level=logging.ERROR, module_name="Music")

    async def _auto_leave_after_timeout(self, guild_id: int, player: lava_lyra.Player):
        """5 分鐘後自動離開語音頻道"""
        try:
            await asyncio.sleep(300)  # 5 分鐘 = 300 秒

            # 再次確認頻道內沒有真人
            if player and player.channel:
                human_count = sum(1 for m in player.channel.members if not m.bot)
                if human_count == 0:
                    try:
                        await player.stop()
                        await player.destroy()
                    except:
                        pass

                    await self._cleanup_player(
                        guild_id,
                        send_message=True,
                        message="語音頻道內已 5 分鐘無其他成員，機器人已離開"
                    )
        except asyncio.CancelledError:
            pass
        finally:
            leave_timers.pop(guild_id, None)
    
    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        """當語音狀態變化時，檢查是否需要啟動或取消自動離開計時器"""
        guild_id = member.guild.id

        # 檢查是否是機器人自己被踢出或離開
        if member.id == self.bot.user.id:
            # 機器人離開了語音頻道
            if before.channel and not after.channel:
                log(f"機器人已離開語音頻道", module_name="Music", guild=member.guild)
                player: lava_lyra.Player = member.guild.voice_client

                # 清理播放器
                if player:
                    try:
                        await player.stop()
                        await player.destroy()
                    except:
                        pass

                await self._cleanup_player(guild_id)
            return

        # 檢查機器人是否在語音頻道
        player: lava_lyra.Player = member.guild.voice_client
        if not player or not player.channel:
            return

        # 檢查是否是機器人所在頻道的變化
        is_bot_channel = (
            (before.channel and before.channel.id == player.channel.id) or
            (after.channel and after.channel.id == player.channel.id)
        )
        if not is_bot_channel:
            return

        # 計算頻道內的真人數量（排除機器人）
        human_count = sum(1 for m in player.channel.members if not m.bot)

        if human_count == 0:
            # 沒有真人，啟動 5 分鐘計時器（如果還沒啟動）
            if guild_id not in leave_timers:
                leave_timers[guild_id] = asyncio.create_task(
                    self._auto_leave_after_timeout(guild_id, player)
                )
                log(f"已啟動 5 分鐘自動離開計時器", module_name="Music", guild=member.guild)
        else:
            # 有真人，取消計時器
            if guild_id in leave_timers:
                leave_timers[guild_id].cancel()
                leave_timers.pop(guild_id, None)
                log(f"已取消自動離開計時器", module_name="Music", guild=member.guild)
    
    @commands.Cog.listener()
    async def on_lyra_track_start(self, player: lava_lyra.Player, track: lava_lyra.Track):
        """當音樂開始播放時"""
        if not player:
            return

        station = self._get_guild_radio_station(player.guild.id)
        if station:
            return
        
        embed = discord.Embed(
            title="🎵 開始播放",
            description=f"**[{track.title}]({track.uri})**",
            color=0x3498db
        )
        embed.set_thumbnail(url=track.thumbnail)
        if track.author:
            embed.add_field(name="藝術家", value=track.author, inline=True)
        embed.add_field(
            name="時長", 
            value=f"{int(track.length / 1000 // 60)}:{int(track.length / 1000 % 60):02d}",
            inline=True
        )
        
        try:
            text_channel = text_channels.get(player.guild.id)
            if text_channel:
                await text_channel.send(embed=embed)
        except Exception as e:
            log(f"無法發送播放通知: {e}", level=logging.WARNING, module_name="Music")
    
    @commands.Cog.listener()
    async def on_lyra_track_end(self, player: lava_lyra.Player, track: lava_lyra.Track, reason: Optional[str]):
        """當音樂結束播放時"""
        if not player:
            return
        
        guild_id = player.guild.id
        queue = get_queue(guild_id)
        station = self._get_guild_radio_station(guild_id)
        
        # 檢查結束原因，可能是字串或枚舉
        reason_str = str(reason).upper() if reason else ""
        log(f"Track ended with reason: {reason_str}", module_name="Music", guild=player.guild)

        if station and "STOPPED" not in reason_str and "REPLACED" not in reason_str:
            try:
                await asyncio.sleep(1)
                await self._play_radio_stream(player, station)
            except Exception as e:
                log(f"電台串流重新連線失敗: {e}", level=logging.ERROR, module_name="Music", guild=player.guild)
            return
        
        # 只在正常結束時播放下一首
        # REPLACED: 被新歌曲替換（不需要自動播放）
        # STOPPED: 手動停止（skip 會自己處理下一首）
        # LOAD_FAILED: 載入失敗
        if "REPLACED" in reason_str or "LOAD_FAILED" in reason_str:
            return
        
        # STOPPED 通常是 skip 或 stop 指令觸發的，這些指令會自己處理
        # 但如果是自然結束 (FINISHED)，需要播放下一首
        if "STOPPED" in reason_str:
            return
        
        # 取得循環模式
        loop_mode = loop_modes.get(guild_id, LoopMode.OFF)
        
        # 單曲循環：重新播放同一首歌
        if loop_mode == LoopMode.TRACK:
            try:
                await player.play(track)
            except Exception as e:
                log(f"單曲循環播放失敗: {e}", level=logging.ERROR, module_name="Music", guild=player.guild)
            return
        
        # 隊列循環：將剛播完的歌加回隊列尾端
        if loop_mode == LoopMode.QUEUE:
            queue.add(track)
        
        # 播放下一首歌 (FINISHED 的情況)
        next_track = queue.get()
        if next_track:
            try:
                await player.play(next_track)
            except Exception as e:
                log(f"播放下一首失敗: {e}", level=logging.ERROR, module_name="Music", guild=player.guild)
        else:
            # 離開語音頻道並清理資料
            try:
                await player.destroy()
            except:
                pass

            await self._cleanup_player(
                guild_id,
                send_message=True,
                message="沒有更多的歌曲要播放，已離開語音頻道"
            )
    
    async def music_quit_task(self):
        """機器人關閉時的清理任務"""
        for guild_id, channel in list(text_channels.items()):
            try:
                guild = self.bot.get_guild(guild_id)
                if not guild:
                    continue

                player: lava_lyra.Player = guild.voice_client
                if not player:
                    continue

                queue = get_queue(guild_id)
                uris = []
                is_radio_mode = self._is_radio_mode(guild_id)

                # 保存當前播放和隊列
                if player.current and not is_radio_mode:
                    uris.append(player.current.uri)
                if not is_radio_mode:
                    for track in queue:
                        uris.append(track.uri)

                if uris:
                    set_server_config(guild_id, "music_saved_queue", {"uris": uris})

                restore_mention = await get_command_mention("music", "restore-queue")
                restore_hint = f"重啟後可使用 {restore_mention or '`/music restore-queue`'} 回復儲存的播放隊列。" if uris else ""

                embed = discord.Embed(
                    title="🔴 機器人即將離開語音頻道",
                    description=f"機器人正在關機或重啟。\n{(' ' + restore_hint) if restore_hint else ''}",
                    color=0x95a5a6
                )
                await channel.send(embed=embed)

                # 清理播放器
                try:
                    await player.stop()
                    await player.destroy()
                except:
                    pass

            except Exception as e:
                log(f"關機清理時出錯: {e}", level=logging.ERROR, module_name="Music")
    
    async def search_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        """搜尋歌曲的自動完成"""
        if not current or len(current) < 2:
            return []

        try:
            # 嘗試獲取現有的 player 或使用任意節點
            player: lava_lyra.Player = interaction.guild.voice_client

            if not player:
                # 如果沒有 player，嘗試從 NodePool 獲取節點來搜尋
                try:
                    node = lava_lyra.NodePool.get_node()
                    if not node:
                        return []
                    # 使用節點的 get_tracks 方法
                    results = await node.get_tracks(f"ytsearch:{current}")
                except:
                    return []
            else:
                results = await player.get_tracks(f"ytsearch:{current}")

            if not results:
                return []

            # 如果是播放列表，取其中的歌曲
            tracks = results.tracks if isinstance(results, lava_lyra.Playlist) else results

            # 限制為前 25 個結果（Discord 限制）
            choices = []
            for track in tracks[:25]:
                # 截斷過長的標題
                title = track.title
                if len(title) > 100:
                    title = title[:97] + "..."

                # 添加作者信息
                if track.author:
                    display_name = f"{title} - {track.author}"
                    if len(display_name) > 100:
                        display_name = display_name[:97] + "..."
                else:
                    display_name = title

                choices.append(app_commands.Choice(name=display_name, value=track.uri))

            return choices

        except Exception as e:
            log(f"自動完成搜尋出錯: {e}", level=logging.WARNING, module_name="Music")
            return []

    @app_commands.command(name=app_commands.locale_str("search"), description="搜尋並播放音樂")
    @app_commands.describe(query="搜尋歌曲（支援自動完成）")
    @app_commands.autocomplete(query=search_autocomplete)
    @app_commands.guild_only()
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.checks.bot_has_permissions(connect=True, speak=True)
    async def search(self, interaction: discord.Interaction, query: str):
        """搜尋並播放音樂"""
        await interaction.response.defer()

        if not await self._ensure_not_radio_mode(interaction, interaction.guild.id):
            return

        # 檢查使用者是否在語音頻道
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.followup.send("❌ 你必須加入語音頻道才能播放音樂", ephemeral=True)
            return

        # 檢查是否與機器人在同一語音頻道
        error_msg = self._check_voice_channel(interaction.user, interaction.guild)
        if error_msg:
            await interaction.followup.send(error_msg, ephemeral=True)
            return

        # 獲取或創建播放器
        player: lava_lyra.Player = interaction.guild.voice_client

        if not player:
            try:
                player = await interaction.user.voice.channel.connect(cls=lava_lyra.Player)
                text_channels[interaction.guild.id] = interaction.channel
            except Exception as e:
                await interaction.followup.send(f"❌ 無法連接到語音頻道: {e}", ephemeral=True)
                return

        guild_id = interaction.guild.id
        queue = get_queue(guild_id)

        # 如果 query 是 URI（從自動完成選擇的），直接使用
        # 否則進行搜尋
        try:
            if not self._check_valid_query(query):
                await interaction.followup.send("❌ 請提供有效的歌曲名稱或 URL", ephemeral=True)
                return

            if query.startswith(("http://", "https://", "ytsearch:", "scsearch:")):
                results = await player.get_tracks(query)
            else:
                results = await player.get_tracks(f"ytsearch:{query}")

            if not results:
                await interaction.followup.send(f"❌ 找不到 '{query}' 的結果", ephemeral=True)
                return

            # 如果結果是播放列表
            if isinstance(results, lava_lyra.Playlist):
                tracks = results.tracks
                embed = discord.Embed(
                    title="📋 播放列表已添加",
                    description=f"**{results.name}**",
                    color=0x2ecc71
                )
                embed.add_field(name="歌曲數量", value=len(tracks), inline=True)
                embed.add_field(name="總時長", value=self._format_duration(sum(t.length for t in tracks)), inline=True)
                await interaction.followup.send(embed=embed)

                for track in tracks:
                    queue.add(track)
            else:
                # 單個搜尋結果
                track = results[0]
                queue.add(track)

                embed = discord.Embed(
                    title="✅ 已添加到隊列",
                    description=f"**[{track.title}]({track.uri})**",
                    color=0x2ecc71
                )
                embed.set_thumbnail(url=track.thumbnail)
                if track.author:
                    embed.add_field(name="藝術家", value=track.author, inline=True)
                embed.add_field(
                    name="時長",
                    value=self._format_duration(track.length),
                    inline=True
                )
                embed.add_field(name="隊列位置", value=len(queue), inline=True)
                await interaction.followup.send(embed=embed)

            # 開始播放
            if not player.is_playing:
                next_track = queue.get()
                if next_track:
                    try:
                        await player.play(next_track)
                    except Exception as e:
                        log(f"開始播放失敗: {e}", level=logging.ERROR, module_name="Music", guild=interaction.guild)
                        await interaction.followup.send(f"⚠️ 歌曲已添加到隊列，但播放失敗: {e}", ephemeral=True)

        except Exception as e:
            log(f"搜尋播放出錯: {e}", level=logging.ERROR, module_name="Music", guild=interaction.guild)
            await interaction.followup.send(f"❌ 搜尋播放出錯: {e}", ephemeral=True)

    @app_commands.command(name=app_commands.locale_str("play"), description="播放音樂")
    @app_commands.describe(query="歌曲名稱或 URL")
    @app_commands.guild_only()
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.checks.bot_has_permissions(connect=True, speak=True)
    async def play(self, interaction: discord.Interaction, query: str):
        """播放音樂"""
        await interaction.response.defer()

        if not await self._ensure_not_radio_mode(interaction, interaction.guild.id):
            return
        
        # 檢查使用者是否在語音頻道
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.followup.send("❌ 你必須加入語音頻道才能播放音樂", ephemeral=True)
            return
        
        # 檢查是否與機器人在同一語音頻道
        error_msg = self._check_voice_channel(interaction.user, interaction.guild)
        if error_msg:
            await interaction.followup.send(error_msg, ephemeral=True)
            return
        
        # 獲取或創建播放器
        player: lava_lyra.Player = interaction.guild.voice_client
        
        if not player:
            try:
                player = await interaction.user.voice.channel.connect(cls=lava_lyra.Player)
                text_channels[interaction.guild.id] = interaction.channel
            except Exception as e:
                await interaction.followup.send(f"❌ 無法連接到語音頻道: {e}", ephemeral=True)
                return
        
        guild_id = interaction.guild.id
        queue = get_queue(guild_id)
        
        # 搜尋歌曲
        try:
            if not self._check_valid_query(query):
                await interaction.followup.send("❌ 請提供有效的歌曲名稱或 URL", ephemeral=True)
                return

            results = await player.get_tracks(query)
            
            if not results:
                await interaction.followup.send(f"❌ 找不到 '{query}' 的結果", ephemeral=True)
                return
            
            # 如果結果是播放列表
            if isinstance(results, lava_lyra.Playlist):
                tracks = results.tracks
                embed = discord.Embed(
                    title="📋 播放列表已添加",
                    description=f"**{results.name}**",
                    color=0x2ecc71
                )
                embed.add_field(name="歌曲數量", value=len(tracks), inline=True)
                embed.add_field(name="總時長", value=self._format_duration(sum(t.length for t in tracks)), inline=True)
                await interaction.followup.send(embed=embed)
                
                for track in tracks:
                    queue.add(track)
            else:
                # 如果是單個搜尋結果
                track = results[0]
                queue.add(track)
                
                embed = discord.Embed(
                    title="✅ 已添加到隊列",
                    description=f"**[{track.title}]({track.uri})**",
                    color=0x2ecc71
                )
                embed.set_thumbnail(url=track.thumbnail)
                if track.author:
                    embed.add_field(name="藝術家", value=track.author, inline=True)
                embed.add_field(
                    name="時長",
                    value=self._format_duration(track.length),
                    inline=True
                )
                embed.add_field(name="隊列位置", value=len(queue), inline=True)
                await interaction.followup.send(embed=embed)
            
            # 開始播放
            if not player.is_playing:
                next_track = queue.get()
                if next_track:
                    try:
                        await player.play(next_track)
                    except Exception as e:
                        log(f"開始播放失敗: {e}", level=logging.ERROR, module_name="Music", guild=interaction.guild)
                        await interaction.followup.send(f"⚠️ 歌曲已添加到隊列，但播放失敗: {e}", ephemeral=True)

        except Exception as e:
            log(f"播放出錯: {e}", level=logging.ERROR, module_name="Music", guild=interaction.guild)
            await interaction.followup.send(f"❌ 播放出錯: {e}", ephemeral=True)

    @app_commands.command(name="radio", description="切換到電台模式")
    @app_commands.describe(station="要播放的電台")
    @app_commands.choices(station=[
        app_commands.Choice(name="LISTEN.moe", value="listenmoe"),
        app_commands.Choice(name="R/a/dio", value="r-a-dio"),
    ])
    @app_commands.guild_only()
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.checks.bot_has_permissions(connect=True, speak=True)
    async def radio(self, interaction: discord.Interaction, station: str):
        """切換到電台模式"""
        await interaction.response.defer()

        station_info = self._get_station(station)
        if not station_info:
            await interaction.followup.send("❌ 不支援的電台。", ephemeral=True)
            return

        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.followup.send("❌ 你必須加入語音頻道才能播放電台", ephemeral=True)
            return

        error_msg = self._check_voice_channel(interaction.user, interaction.guild)
        if error_msg:
            await interaction.followup.send(error_msg, ephemeral=True)
            return

        player: lava_lyra.Player = interaction.guild.voice_client
        if not player:
            try:
                player = await interaction.user.voice.channel.connect(cls=lava_lyra.Player)
                text_channels[interaction.guild.id] = interaction.channel
            except Exception as e:
                await interaction.followup.send(f"❌ 無法連接到語音頻道: {e}", ephemeral=True)
                return

        try:
            await self._activate_radio_mode(interaction.guild, interaction.channel, player, station_info)
            info = await self._wait_for_valid_radio_info(station_info.key)
            if info:
                signature = self._get_radio_signature(info)
                if signature:
                    self._radio_last_announced[interaction.guild.id] = signature
                await interaction.followup.send(embed=self._build_radio_embed(station_info))
            else:
                await interaction.followup.send(f"📻 已切換到 {station_info.display_name} 電台模式，正在等待電台資料...")
        except Exception as e:
            log(f"切換電台模式失敗: {e}", level=logging.ERROR, module_name="Music", guild=interaction.guild)
            await interaction.followup.send(f"❌ 切換到 {station_info.display_name} 失敗: {e}", ephemeral=True)
    
    @app_commands.command(name=app_commands.locale_str("pause"), description="暫停播放")
    @app_commands.guild_only()
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def pause(self, interaction: discord.Interaction):
        """暫停播放"""
        await interaction.response.defer()
        
        error_msg = self._check_voice_channel(interaction.user, interaction.guild)
        if error_msg:
            await interaction.followup.send(error_msg, ephemeral=True)
            return
        
        player: lava_lyra.Player = interaction.guild.voice_client
        if not player:
            await interaction.followup.send("❌ 沒有正在播放的音樂", ephemeral=True)
            return
        
        if player.is_paused:
            await interaction.followup.send("❌ 音樂已經暫停", ephemeral=True)
            return
        
        try:
            await player.set_pause(True)
            await interaction.followup.send("⏸️ 音樂已暫停")
        except Exception as e:
            await interaction.followup.send(f"❌ 暫停出錯: {e}", ephemeral=True)
    
    @app_commands.command(name=app_commands.locale_str("resume"), description="繼續播放")
    @app_commands.guild_only()
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def resume(self, interaction: discord.Interaction):
        """繼續播放"""
        await interaction.response.defer()
        
        error_msg = self._check_voice_channel(interaction.user, interaction.guild)
        if error_msg:
            await interaction.followup.send(error_msg, ephemeral=True)
            return
        
        player: lava_lyra.Player = interaction.guild.voice_client
        if not player:
            await interaction.followup.send("❌ 沒有暫停的音樂", ephemeral=True)
            return
        
        if not player.is_paused:
            await interaction.followup.send("❌ 音樂未暫停", ephemeral=True)
            return
        
        try:
            await player.set_pause(False)
            await interaction.followup.send("▶️ 音樂已繼續播放")
        except Exception as e:
            await interaction.followup.send(f"❌ 繼續播放出錯: {e}", ephemeral=True)
    
    @app_commands.command(name=app_commands.locale_str("stop"), description="停止播放並斷開連接")
    @app_commands.guild_only()
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def stop(self, interaction: discord.Interaction):
        """停止播放"""
        await interaction.response.defer()
        
        error_msg = self._check_voice_channel(interaction.user, interaction.guild)
        if error_msg:
            await interaction.followup.send(error_msg, ephemeral=True)
            return
        
        player: lava_lyra.Player = interaction.guild.voice_client
        if not player:
            await interaction.followup.send("❌ 沒有正在播放的音樂", ephemeral=True)
            return
        
        try:
            await player.stop()
            await player.destroy()
            await self._cleanup_player(interaction.guild.id)
            await interaction.followup.send("⏹️ 已停止播放並斷開連接")
        except Exception as e:
            await interaction.followup.send(f"❌ 停止出錯: {e}", ephemeral=True)
    
    @app_commands.command(name=app_commands.locale_str("skip"), description="跳過當前歌曲")
    @app_commands.guild_only()
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def skip(self, interaction: discord.Interaction):
        """跳過當前歌曲"""
        await interaction.response.defer()

        station = self._get_guild_radio_station(interaction.guild.id)
        if station:
            await interaction.followup.send(f"❌ {station.display_name} 電台模式不能跳過歌曲。", ephemeral=True)
            return
        
        error_msg = self._check_voice_channel(interaction.user, interaction.guild)
        if error_msg:
            await interaction.followup.send(error_msg, ephemeral=True)
            return
        
        player: lava_lyra.Player = interaction.guild.voice_client
        if not player or not player.is_playing:
            await interaction.followup.send("❌ 沒有正在播放的音樂", ephemeral=True)
            return
        
        try:
            current_track = player.current
            await player.stop()

            embed = discord.Embed(
                title="⏭️ 已跳過",
                description=f"**{current_track.title}**",
                color=0xe74c3c
            )
            await interaction.followup.send(embed=embed)

            queue = get_queue(interaction.guild.id)
            next_track = queue.get()
            if next_track:
                try:
                    await player.play(next_track)
                except Exception as e:
                    log(f"跳過後播放下一首失敗: {e}", level=logging.ERROR, module_name="Music", guild=interaction.guild)
                    await interaction.followup.send(f"⚠️ 無法播放下一首歌曲: {e}", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ 跳過出錯: {e}", ephemeral=True)
    
    @app_commands.command(name=app_commands.locale_str("queue"), description="查看播放隊列")
    @app_commands.guild_only()
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def queue(self, interaction: discord.Interaction):
        """查看播放隊列"""
        await interaction.response.defer()

        station = self._get_guild_radio_station(interaction.guild.id)
        if station:
            await self._wait_for_valid_radio_info(station.key, timeout=5)
            await interaction.followup.send(embed=self._build_radio_embed(station))
            return
        
        player: lava_lyra.Player = interaction.guild.voice_client
        queue = get_queue(interaction.guild.id)
        
        if not player:
            await interaction.followup.send("❌ 沒有正在播放的音樂", ephemeral=True)
            return
        
        if not player.current and queue.is_empty:
            await interaction.followup.send("❌ 播放隊列為空", ephemeral=True)
            return
        
        embed = discord.Embed(title="📋 播放隊列", color=0x3498db)
        
        # 顯示當前播放的歌曲
        if player.current:
            embed.description = f"**正在播放:**\n[{player.current.title}]({player.current.uri})"
            embed.set_thumbnail(url=player.current.thumbnail)
        
        # 顯示隊列中的歌曲
        if not queue.is_empty:
            queue_list = []
            total_duration = 0
            
            for i, track in enumerate(queue, 1):
                if i <= 10:
                    queue_list.append(f"{i}. [{track.title}]({track.uri})")
                total_duration += track.length
            
            if queue_list:
                embed.add_field(
                    name=f"接下來的歌曲 ({len(queue)} 首)",
                    value="\n".join(queue_list),
                    inline=False
                )
            
            if len(queue) > 10:
                embed.add_field(name="更多歌曲", value=f"還有 {len(queue) - 10} 首歌曲", inline=False)
            
            embed.add_field(
                name="隊列總時長",
                value=self._format_duration(total_duration),
                inline=True
            )
        
        embed.set_footer(text=f"隊列中共有 {len(queue)} 首歌曲")
        await interaction.followup.send(embed=embed)
    
    @app_commands.command(name=app_commands.locale_str("restore-queue"), description="回復重啟前儲存的播放隊列")
    @app_commands.guild_only()
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.checks.bot_has_permissions(connect=True, speak=True)
    async def restore_queue(self, interaction: discord.Interaction):
        """回復重啟前儲存的播放隊列"""
        await interaction.response.defer()

        if not await self._ensure_not_radio_mode(interaction, interaction.guild.id):
            return

        saved = get_server_config(interaction.guild.id, "music_saved_queue")
        if not saved or not saved.get("uris"):
            await interaction.followup.send("❌ 沒有儲存的播放隊列可回復。", ephemeral=True)
            return

        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.followup.send("❌ 你必須加入語音頻道才能使用此指令", ephemeral=True)
            return

        player: lava_lyra.Player = interaction.guild.voice_client
        if not player:
            try:
                player = await interaction.user.voice.channel.connect(cls=lava_lyra.Player)
                text_channels[interaction.guild.id] = interaction.channel
            except Exception as e:
                await interaction.followup.send(f"❌ 無法連接到語音頻道: {e}", ephemeral=True)
                return
        elif interaction.user.voice.channel.id != player.channel.id:
            await interaction.followup.send("❌ 你必須與機器人在同一語音頻道才能使用此指令", ephemeral=True)
            return

        guild_id = interaction.guild.id
        queue = get_queue(guild_id)
        added = 0
        failed = 0

        for uri in saved["uris"]:
            try:
                results = await player.get_tracks(uri)
                if not results:
                    failed += 1
                    continue
                track = results.tracks[0] if isinstance(results, lava_lyra.Playlist) else results[0]
                queue.add(track)
                added += 1
            except Exception as e:
                log(f"無法載入歌曲 {uri}: {e}", level=logging.WARNING, module_name="Music", guild=interaction.guild)
                failed += 1

        # 清除已保存的隊列
        set_server_config(guild_id, "music_saved_queue", None)

        # 開始播放
        if not player.is_playing and added > 0:
            next_track = queue.get()
            if next_track:
                try:
                    await player.play(next_track)
                except Exception as e:
                    log(f"回復隊列後播放失敗: {e}", level=logging.ERROR, module_name="Music", guild=interaction.guild)
                    await interaction.followup.send(f"⚠️ 已回復 {added} 首歌曲，但播放失敗: {e}")
                    return

        msg = f"✅ 已回復 {added} 首歌曲到隊列。"
        if failed:
            msg += f"（{failed} 首無法載入）"
        await interaction.followup.send(msg)

    @app_commands.command(name=app_commands.locale_str("loop"), description="設定循環播放模式")
    @app_commands.describe(mode="循環模式")
    @app_commands.choices(mode=[
        app_commands.Choice(name="關閉循環", value=0),
        app_commands.Choice(name="單曲循環", value=1),
        app_commands.Choice(name="隊列循環", value=2),
    ])
    @app_commands.guild_only()
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def loop(self, interaction: discord.Interaction, mode: Optional[int] = None):
        """設定循環播放模式"""
        await interaction.response.defer()

        station = self._get_guild_radio_station(interaction.guild.id)
        if station:
            await interaction.followup.send(f"❌ {station.display_name} 電台模式不能設定循環。", ephemeral=True)
            return

        error_msg = self._check_voice_channel(interaction.user, interaction.guild)
        if error_msg:
            await interaction.followup.send(error_msg, ephemeral=True)
            return

        player: lava_lyra.Player = interaction.guild.voice_client
        if not player:
            await interaction.followup.send("❌ 沒有正在播放的音樂", ephemeral=True)
            return

        guild_id = interaction.guild.id
        current_mode = loop_modes.get(guild_id, LoopMode.OFF)

        if mode is None:
            # 沒有指定模式：循環切換 OFF -> TRACK -> QUEUE -> OFF
            if current_mode == LoopMode.OFF:
                new_mode = LoopMode.TRACK
            elif current_mode == LoopMode.TRACK:
                new_mode = LoopMode.QUEUE
            else:
                new_mode = LoopMode.OFF
        else:
            new_mode = LoopMode(mode)

        loop_modes[guild_id] = new_mode

        mode_display = {LoopMode.OFF: "▶️ 關閉循環", LoopMode.TRACK: "🔂 單曲循環", LoopMode.QUEUE: "🔁 隊列循環"}
        await interaction.followup.send(mode_display[new_mode])

    @app_commands.command(name=app_commands.locale_str("now-playing"), description="查看當前播放的歌曲")
    @app_commands.guild_only()
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def now_playing(self, interaction: discord.Interaction):
        """查看當前播放的歌曲"""
        await interaction.response.defer()

        station = self._get_guild_radio_station(interaction.guild.id)
        if station:
            await self._wait_for_valid_radio_info(station.key, timeout=5)
            await interaction.followup.send(embed=self._build_radio_embed(station))
            return
        
        player: lava_lyra.Player = interaction.guild.voice_client
        if not player or not player.current:
            await interaction.followup.send("❌ 沒有正在播放的音樂", ephemeral=True)
            return
        
        track = player.current
        
        # 進度條
        position = player.position
        length = track.length
        progress = int((position / length) * 20) if length > 0 else 0
        progress_bar = "█" * progress + "░" * (20 - progress)
        
        embed = discord.Embed(
            title="🎵 當前播放",
            description=f"**[{track.title}]({track.uri})**",
            color=0x3498db
        )
        embed.set_thumbnail(url=track.thumbnail)
        
        if track.author:
            embed.add_field(name="藝術家", value=track.author, inline=True)
        
        embed.add_field(
            name="進度",
            value=f"`{progress_bar}`\n{self._format_duration(position)} / {self._format_duration(length)}",
            inline=False
        )
        
        embed.add_field(name="音量", value=f"{player.volume}%", inline=True)

        loop_mode = loop_modes.get(interaction.guild.id, LoopMode.OFF)
        mode_display = {LoopMode.OFF: "▶️ 關閉", LoopMode.TRACK: "🔂 單曲循環", LoopMode.QUEUE: "🔁 隊列循環"}
        embed.add_field(name="循環模式", value=mode_display[loop_mode], inline=True)
        
        await interaction.followup.send(embed=embed)
    
    @app_commands.command(name=app_commands.locale_str("volume"), description="調整音量")
    @app_commands.describe(level="音量等級 (0-100)")
    @app_commands.guild_only()
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def volume(self, interaction: discord.Interaction, level: int):
        """調整音量"""
        await interaction.response.defer()
        
        error_msg = self._check_voice_channel(interaction.user, interaction.guild)
        if error_msg:
            await interaction.followup.send(error_msg, ephemeral=True)
            return
        
        if level < 0 or level > 100:
            await interaction.followup.send("❌ 音量必須在 0-100 之間", ephemeral=True)
            return
        
        player: lava_lyra.Player = interaction.guild.voice_client
        if not player:
            await interaction.followup.send("❌ 沒有正在播放的音樂", ephemeral=True)
            return
        
        try:
            await player.set_volume(level)
            await interaction.followup.send(f"🔊 音量已設置為 {level}%")
        except Exception as e:
            await interaction.followup.send(f"❌ 設置音量出錯: {e}", ephemeral=True)
    
    @app_commands.command(name=app_commands.locale_str("shuffle"), description="隨機打亂隊列")
    @app_commands.guild_only()
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def shuffle(self, interaction: discord.Interaction):
        """隨機打亂隊列"""
        await interaction.response.defer()

        if not await self._ensure_not_radio_mode(interaction, interaction.guild.id):
            return
        
        error_msg = self._check_voice_channel(interaction.user, interaction.guild)
        if error_msg:
            await interaction.followup.send(error_msg, ephemeral=True)
            return
        
        player: lava_lyra.Player = interaction.guild.voice_client
        queue = get_queue(interaction.guild.id)
        
        if not player:
            await interaction.followup.send("❌ 沒有正在播放的音樂", ephemeral=True)
            return
        
        if queue.is_empty:
            await interaction.followup.send("❌ 播放隊列為空", ephemeral=True)
            return
        
        try:
            tracks = list(queue)
            random.shuffle(tracks)
            queue.clear()
            for track in tracks:
                queue.add(track)
            await interaction.followup.send("🔀 隊列已隨機打亂")
        except Exception as e:
            await interaction.followup.send(f"❌ 打亂隊列出錯: {e}", ephemeral=True)
    
    @app_commands.command(name=app_commands.locale_str("recommend"), description="根據當前播放的歌曲推薦相似歌曲")
    @app_commands.describe(count="要添加的推薦歌曲數量 (1-10，預設 5)")
    @app_commands.guild_only()
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def recommend(self, interaction: discord.Interaction, count: Optional[int] = 5):
        """根據當前播放的歌曲推薦相似歌曲並加入隊列"""
        await interaction.response.defer()

        if not await self._ensure_not_radio_mode(interaction, interaction.guild.id):
            return
        
        error_msg = self._check_voice_channel(interaction.user, interaction.guild)
        if error_msg:
            await interaction.followup.send(error_msg, ephemeral=True)
            return
        
        player: lava_lyra.Player = interaction.guild.voice_client
        if not player or not player.current:
            await interaction.followup.send("❌ 沒有正在播放的音樂", ephemeral=True)
            return
        
        count = max(1, min(count, 10))
        
        try:
            results = await player.get_recommendations(track=player.current)
            
            if not results:
                await interaction.followup.send("❌ 找不到相似的推薦歌曲", ephemeral=True)
                return
            
            tracks = results.tracks if isinstance(results, lava_lyra.Playlist) else results
            tracks = tracks[:count]
            
            queue = get_queue(interaction.guild.id)
            for track in tracks:
                queue.add(track)
            
            track_list = "\n".join(
                f"{i}. [{t.title}]({t.uri})" for i, t in enumerate(tracks, 1)
            )
            
            embed = discord.Embed(
                title="🎯 已添加推薦歌曲",
                description=f"根據 **{player.current.title}** 推薦：\n\n{track_list}",
                color=0x9b59b6
            )
            embed.set_thumbnail(url=player.current.thumbnail)
            embed.add_field(name="已添加", value=f"{len(tracks)} 首歌曲", inline=True)
            embed.add_field(
                name="總時長",
                value=self._format_duration(sum(t.length for t in tracks)),
                inline=True
            )
            await interaction.followup.send(embed=embed)

            if not player.is_playing:
                next_track = queue.get()
                if next_track:
                    try:
                        await player.play(next_track)
                    except Exception as e:
                        log(f"推薦後開始播放失敗: {e}", level=logging.ERROR, module_name="Music", guild=interaction.guild)
                        await interaction.followup.send(f"⚠️ 推薦歌曲已添加，但播放失敗: {e}", ephemeral=True)

        except Exception as e:
            log(f"推薦歌曲出錯: {e}", level=logging.ERROR, module_name="Music", guild=interaction.guild)
            await interaction.followup.send(f"❌ 推薦歌曲出錯: {e}", ephemeral=True)
    
    @app_commands.command(name=app_commands.locale_str("nodes"), description="查看 Lavalink 節點狀態")
    async def nodes_command(self, interaction: discord.Interaction):
        """查看 Lavalink 節點狀態"""
        await interaction.response.defer()
        embed = discord.Embed(title="🔧 Lavalink 節點狀態", color=0x3498db)
        for identifier, node in lava_lyra.NodePool._nodes.items():
            name = self.node_names.get(identifier, identifier)
            status = "✅ 已連接" if node.is_connected else "❌ 未連接"
            if node.is_connected:
                ping = f"{round(node.ping, 2)}ms" if node.is_connected else "N/A"
                status += f"\n延遲: {ping}"
                players = node.player_count
                connected_players = len([player for player in node.players.values() if player._is_connected])
                playing_players = len([player for player in node.players.values() if player.is_playing])
                status += f"\n有 {playing_players}/{connected_players}/{players} 個伺服器正在使用此節點"
                health = round(node.health_score, 2)
                status += f"\n健康分數: {health:.2f}%"
                # try to get player and see if current guild is using this node
                if node.players.get(interaction.guild.id):
                    name += " ⬅️ 你在這裡"
            embed.add_field(name=name, value=status, inline=False)
        await interaction.followup.send(embed=embed)
    
    def _format_duration(self, milliseconds: int) -> str:
        """將毫秒轉換為 MM:SS 格式"""
        seconds = milliseconds // 1000
        minutes = seconds // 60
        seconds = seconds % 60
        return f"{minutes}:{seconds:02d}"
    
    # ========== 文字指令 ==========
    
    @commands.command(name="play", aliases=["p", "播放"])
    @commands.guild_only()
    async def text_play(self, ctx: commands.Context, *, query: Optional[str] = None):
        """播放音樂，若無參數則繼續播放"""
        if query is not None and not await self._ensure_not_radio_mode(ctx, ctx.guild.id):
            return

        error_msg = self._check_voice_channel(ctx.author, ctx.guild)
        if error_msg:
            await ctx.send(error_msg)
            return
        
        # 如果沒有給參數，執行 resume
        if query is None:
            player: lava_lyra.Player = ctx.guild.voice_client
            if not player:
                await ctx.send("❌ 沒有正在播放的音樂，請提供歌曲名稱或 URL")
                return
            
            if not player.is_paused:
                await ctx.send("❌ 音樂未暫停，請提供歌曲名稱或 URL 來添加新歌曲")
                return
            
            try:
                await player.set_pause(False)
                await ctx.send("▶️ 音樂已繼續播放")
            except Exception as e:
                await ctx.send(f"❌ 繼續播放出錯: {e}")
            return
        
        player = await self._ensure_voice(ctx)
        if not player:
            return
        
        guild_id = ctx.guild.id
        queue = get_queue(guild_id)
        
        try:
            if not self._check_valid_query(query):
                await ctx.send("❌ 請提供有效的歌曲名稱或 URL")
                return
            results = await player.get_tracks(query)
            
            if not results:
                await ctx.send(f"❌ 找不到 '{query}' 的結果")
                return
            
            if isinstance(results, lava_lyra.Playlist):
                tracks = results.tracks
                embed = discord.Embed(
                    title="📋 播放列表已添加",
                    description=f"**{results.name}**",
                    color=0x2ecc71
                )
                embed.set_thumbnail(url=results.thumbnail)
                embed.add_field(name="歌曲數量", value=len(tracks), inline=True)
                embed.add_field(name="總時長", value=self._format_duration(sum(t.length for t in tracks)), inline=True)
                await ctx.send(embed=embed)
                
                for track in tracks:
                    queue.add(track)
            else:
                track = results[0]
                queue.add(track)
                
                embed = discord.Embed(
                    title="✅ 已添加到隊列",
                    description=f"**[{track.title}]({track.uri})**",
                    color=0x2ecc71,
                )
                embed.set_thumbnail(url=track.thumbnail)
                if track.author:
                    embed.add_field(name="藝術家", value=track.author, inline=True)
                embed.add_field(
                    name="時長",
                    value=self._format_duration(track.length),
                    inline=True
                )
                embed.add_field(name="隊列位置", value=len(queue), inline=True)
                await ctx.send(embed=embed)
            
            if not player.is_playing:
                next_track = queue.get()
                if next_track:
                    try:
                        await player.play(next_track)
                    except Exception as e:
                        log(f"開始播放失敗: {e}", level=logging.ERROR, module_name="Music", guild=ctx.guild)
                        await ctx.send(f"⚠️ 歌曲已添加到隊列，但播放失敗: {e}")

        except Exception as e:
            log(f"播放出錯: {e}", level=logging.ERROR, module_name="Music", guild=ctx.guild)
            await ctx.send(f"❌ 播放出錯: {e}")

    @commands.command(name="radio", aliases=["station", "電台"])
    @commands.guild_only()
    async def text_radio(self, ctx: commands.Context, station: str):
        """切換到電台模式"""
        station_key = station.strip().lower()
        station_aliases = {
            "listenmoe": "listenmoe",
            "listen.moe": "listenmoe",
            "listen-moe": "listenmoe",
            "r-a-dio": "r-a-dio",
            "radio": "r-a-dio",
            "r_a_dio": "r-a-dio",
            "r-a-d.io": "r-a-dio",
        }
        station_info = self._get_station(station_aliases.get(station_key, station_key))
        if not station_info:
            await ctx.send("❌ 可用電台: `listen.moe`, `r-a-d.io`")
            return

        player = await self._ensure_voice(ctx)
        if not player:
            return

        try:
            await self._activate_radio_mode(ctx.guild, ctx.channel, player, station_info)
            info = await self._wait_for_valid_radio_info(station_info.key)
            if info:
                signature = self._get_radio_signature(info)
                if signature:
                    self._radio_last_announced[ctx.guild.id] = signature
                await ctx.send(embed=self._build_radio_embed(station_info))
            else:
                await ctx.send(f"📻 已切換到 {station_info.display_name} 電台模式，正在等待電台資料...")
        except Exception as e:
            log(f"切換電台模式失敗: {e}", level=logging.ERROR, module_name="Music", guild=ctx.guild)
            await ctx.send(f"❌ 切換到 {station_info.display_name} 失敗: {e}")
    
    @commands.command(name="pause", aliases=["暫停"])
    @commands.guild_only()
    async def text_pause(self, ctx: commands.Context):
        """暫停播放"""
        error_msg = self._check_voice_channel(ctx.author, ctx.guild)
        if error_msg:
            await ctx.send(error_msg)
            return
        
        player: lava_lyra.Player = ctx.guild.voice_client
        if not player:
            await ctx.send("❌ 沒有正在播放的音樂")
            return
        
        if player.is_paused:
            await ctx.send("❌ 音樂已經暫停")
            return
        
        try:
            await player.set_pause(True)
            await ctx.send("⏸️ 音樂已暫停")
        except Exception as e:
            await ctx.send(f"❌ 暫停出錯: {e}")
    
    @commands.command(name="resume", aliases=["繼續"])
    @commands.guild_only()
    async def text_resume(self, ctx: commands.Context):
        """繼續播放"""
        error_msg = self._check_voice_channel(ctx.author, ctx.guild)
        if error_msg:
            await ctx.send(error_msg)
            return
        
        player: lava_lyra.Player = ctx.guild.voice_client
        if not player:
            await ctx.send("❌ 沒有暫停的音樂")
            return
        
        if not player.is_paused:
            await ctx.send("❌ 音樂未暫停")
            return
        
        try:
            await player.set_pause(False)
            await ctx.send("▶️ 音樂已繼續播放")
        except Exception as e:
            await ctx.send(f"❌ 繼續播放出錯: {e}")
    
    @commands.command(name="stop", aliases=["停止", "leave", "離開"])
    @commands.guild_only()
    async def text_stop(self, ctx: commands.Context):
        """停止播放並斷開連接"""
        error_msg = self._check_voice_channel(ctx.author, ctx.guild)
        if error_msg:
            await ctx.send(error_msg)
            return
        
        player: lava_lyra.Player = ctx.guild.voice_client
        if not player:
            await ctx.send("❌ 沒有正在播放的音樂")
            return
        
        try:
            await player.stop()
            await player.destroy()
            await self._cleanup_player(ctx.guild.id)
            await ctx.send("⏹️ 已停止播放並斷開連接")
        except Exception as e:
            await ctx.send(f"❌ 停止出錯: {e}")
    
    @commands.command(name="skip", aliases=["sk", "跳過", "下一首"])
    @commands.guild_only()
    async def text_skip(self, ctx: commands.Context):
        """跳過當前歌曲"""
        station = self._get_guild_radio_station(ctx.guild.id)
        if station:
            await ctx.send(f"❌ {station.display_name} 電台模式不能跳過歌曲。")
            return

        error_msg = self._check_voice_channel(ctx.author, ctx.guild)
        if error_msg:
            await ctx.send(error_msg)
            return
        
        player: lava_lyra.Player = ctx.guild.voice_client
        if not player or not player.is_playing:
            await ctx.send("❌ 沒有正在播放的音樂")
            return
        
        try:
            current_track = player.current
            await player.stop()

            embed = discord.Embed(
                title="⏭️ 已跳過",
                description=f"**{current_track.title}**",
                color=0xe74c3c
            )
            await ctx.send(embed=embed)

            queue = get_queue(ctx.guild.id)
            next_track = queue.get()
            if next_track:
                try:
                    await player.play(next_track)
                except Exception as e:
                    log(f"跳過後播放下一首失敗: {e}", level=logging.ERROR, module_name="Music", guild=ctx.guild)
                    await ctx.send(f"⚠️ 無法播放下一首歌曲: {e}")
        except Exception as e:
            await ctx.send(f"❌ 跳過出錯: {e}")

    @commands.command(name="queue", aliases=["qu", "隊列"])
    @commands.guild_only()
    async def text_queue(self, ctx: commands.Context):
        """查看播放隊列"""
        station = self._get_guild_radio_station(ctx.guild.id)
        if station:
            await self._wait_for_valid_radio_info(station.key, timeout=5)
            await ctx.send(embed=self._build_radio_embed(station))
            return

        player: lava_lyra.Player = ctx.guild.voice_client
        queue = get_queue(ctx.guild.id)
        
        if not player:
            await ctx.send("❌ 沒有正在播放的音樂")
            return
        
        if not player.current and queue.is_empty:
            await ctx.send("❌ 播放隊列為空")
            return
        
        embed = discord.Embed(title="📋 播放隊列", color=0x3498db)
        
        if player.current:
            embed.description = f"**正在播放:**\n[{player.current.title}]({player.current.uri})"
        
        if not queue.is_empty:
            queue_list = []
            total_duration = 0
            
            for i, track in enumerate(queue, 1):
                if i <= 10:
                    queue_list.append(f"{i}. [{track.title}]({track.uri})")
                total_duration += track.length
            
            if queue_list:
                embed.add_field(
                    name=f"接下來的歌曲 ({len(queue)} 首)",
                    value="\n".join(queue_list),
                    inline=False
                )
            
            if len(queue) > 10:
                embed.add_field(name="更多歌曲", value=f"還有 {len(queue) - 10} 首歌曲", inline=False)
            
            embed.add_field(
                name="隊列總時長",
                value=self._format_duration(total_duration),
                inline=True
            )
        
        embed.set_footer(text=f"隊列中共有 {len(queue)} 首歌曲")
        await ctx.send(embed=embed)
    
    @commands.command(name="loop", aliases=["lp", "循環"])
    @commands.guild_only()
    async def text_loop(self, ctx: commands.Context, mode: Optional[str] = None):
        """設定循環播放模式 (off/track/queue)"""
        station = self._get_guild_radio_station(ctx.guild.id)
        if station:
            await ctx.send(f"❌ {station.display_name} 電台模式不能設定循環。")
            return

        error_msg = self._check_voice_channel(ctx.author, ctx.guild)
        if error_msg:
            await ctx.send(error_msg)
            return

        player: lava_lyra.Player = ctx.guild.voice_client
        if not player:
            await ctx.send("❌ 沒有正在播放的音樂")
            return

        guild_id = ctx.guild.id
        current_mode = loop_modes.get(guild_id, LoopMode.OFF)

        mode_map = {
            "off": LoopMode.OFF, "關閉": LoopMode.OFF, "0": LoopMode.OFF,
            "track": LoopMode.TRACK, "single": LoopMode.TRACK, "單曲": LoopMode.TRACK, "1": LoopMode.TRACK,
            "queue": LoopMode.QUEUE, "all": LoopMode.QUEUE, "隊列": LoopMode.QUEUE, "全部": LoopMode.QUEUE, "2": LoopMode.QUEUE,
        }

        if mode is None:
            # 循環切換 OFF -> TRACK -> QUEUE -> OFF
            if current_mode == LoopMode.OFF:
                new_mode = LoopMode.TRACK
            elif current_mode == LoopMode.TRACK:
                new_mode = LoopMode.QUEUE
            else:
                new_mode = LoopMode.OFF
        else:
            new_mode = mode_map.get(mode.lower())
            if new_mode is None:
                await ctx.send("❌ 無效的循環模式，請使用 `off`、`track` 或 `queue`")
                return

        loop_modes[guild_id] = new_mode

        mode_display = {LoopMode.OFF: "▶️ 關閉循環", LoopMode.TRACK: "🔂 單曲循環", LoopMode.QUEUE: "🔁 隊列循環"}
        await ctx.send(mode_display[new_mode])

    @commands.command(name="nowplaying", aliases=["np", "現正播放"])
    @commands.guild_only()
    async def text_now_playing(self, ctx: commands.Context):
        """查看當前播放的歌曲"""
        station = self._get_guild_radio_station(ctx.guild.id)
        if station:
            await self._wait_for_valid_radio_info(station.key, timeout=5)
            await ctx.send(embed=self._build_radio_embed(station))
            return

        player: lava_lyra.Player = ctx.guild.voice_client
        if not player or not player.current:
            await ctx.send("❌ 沒有正在播放的音樂")
            return
        
        track = player.current
        
        position = player.position
        length = track.length
        progress = int((position / length) * 20) if length > 0 else 0
        progress_bar = "█" * progress + "░" * (20 - progress)
        
        embed = discord.Embed(
            title="🎵 當前播放",
            description=f"**[{track.title}]({track.uri})**",
            color=0x3498db
        )
        
        embed.set_thumbnail(url=track.thumbnail)
        
        if track.author:
            embed.add_field(name="藝術家", value=track.author, inline=True)
        
        embed.add_field(
            name="進度",
            value=f"`{progress_bar}`\n{self._format_duration(position)} / {self._format_duration(length)}",
            inline=False
        )
        
        embed.add_field(name="音量", value=f"{player.volume}%", inline=True)

        loop_mode = loop_modes.get(ctx.guild.id, LoopMode.OFF)
        mode_display = {LoopMode.OFF: "▶️ 關閉", LoopMode.TRACK: "🔂 單曲循環", LoopMode.QUEUE: "🔁 隊列循環"}
        embed.add_field(name="循環模式", value=mode_display[loop_mode], inline=True)
        
        await ctx.send(embed=embed)
    
    @commands.command(name="volume", aliases=["vol", "音量"])
    @commands.guild_only()
    async def text_volume(self, ctx: commands.Context, level: int):
        """調整音量"""
        error_msg = self._check_voice_channel(ctx.author, ctx.guild)
        if error_msg:
            await ctx.send(error_msg)
            return
        
        if level < 0 or level > 100:
            await ctx.send("❌ 音量必須在 0-100 之間")
            return
        
        player: lava_lyra.Player = ctx.guild.voice_client
        if not player:
            await ctx.send("❌ 沒有正在播放的音樂")
            return
        
        try:
            await player.set_volume(level)
            await ctx.send(f"🔊 音量已設置為 {level}%")
        except Exception as e:
            await ctx.send(f"❌ 設置音量出錯: {e}")
    
    @commands.command(name="shuffle", aliases=["sh", "隨機"])
    @commands.guild_only()
    async def text_shuffle(self, ctx: commands.Context):
        """隨機打亂隊列"""
        if not await self._ensure_not_radio_mode(ctx, ctx.guild.id):
            return

        error_msg = self._check_voice_channel(ctx.author, ctx.guild)
        if error_msg:
            await ctx.send(error_msg)
            return
        
        player: lava_lyra.Player = ctx.guild.voice_client
        queue = get_queue(ctx.guild.id)
        
        if not player:
            await ctx.send("❌ 沒有正在播放的音樂")
            return
        
        if queue.is_empty:
            await ctx.send("❌ 播放隊列為空")
            return
        
        try:
            tracks = list(queue)
            random.shuffle(tracks)
            queue.clear()
            for track in tracks:
                queue.add(track)
            await ctx.send("🔀 隊列已隨機打亂")
        except Exception as e:
            await ctx.send(f"❌ 打亂隊列出錯: {e}")
    
    @commands.command(name="recommend", aliases=["rec", "推薦"])
    @commands.guild_only()
    async def text_recommend(self, ctx: commands.Context, count: int = 5):
        """根據當前播放的歌曲推薦相似歌曲"""
        if not await self._ensure_not_radio_mode(ctx, ctx.guild.id):
            return

        error_msg = self._check_voice_channel(ctx.author, ctx.guild)
        if error_msg:
            await ctx.send(error_msg)
            return
        
        player: lava_lyra.Player = ctx.guild.voice_client
        if not player or not player.current:
            await ctx.send("❌ 沒有正在播放的音樂")
            return
        
        count = max(1, min(count, 10))
        
        try:
            results = await player.get_recommendations(track=player.current)
            
            if not results:
                await ctx.send("❌ 找不到相似的推薦歌曲")
                return
            
            tracks = results.tracks if isinstance(results, lava_lyra.Playlist) else results
            tracks = tracks[:count]
            
            queue = get_queue(ctx.guild.id)
            for track in tracks:
                queue.add(track)
            
            track_list = "\n".join(
                f"{i}. [{t.title}]({t.uri})" for i, t in enumerate(tracks, 1)
            )
            
            embed = discord.Embed(
                title="🎯 已添加推薦歌曲",
                description=f"根據 **{player.current.title}** 推薦：\n\n{track_list}",
                color=0x9b59b6
            )
            embed.set_thumbnail(url=player.current.thumbnail)
            embed.add_field(name="已添加", value=f"{len(tracks)} 首歌曲", inline=True)
            embed.add_field(
                name="總時長",
                value=self._format_duration(sum(t.length for t in tracks)),
                inline=True
            )
            await ctx.send(embed=embed)

            if not player.is_playing:
                next_track = queue.get()
                if next_track:
                    try:
                        await player.play(next_track)
                    except Exception as e:
                        log(f"推薦後開始播放失敗: {e}", level=logging.ERROR, module_name="Music", guild=ctx.guild)
                        await ctx.send(f"⚠️ 推薦歌曲已添加，但播放失敗: {e}")

        except Exception as e:
            log(f"推薦歌曲出錯: {e}", level=logging.ERROR, module_name="Music", guild=ctx.guild)
            await ctx.send(f"❌ 推薦歌曲出錯: {e}")


asyncio.run(bot.add_cog(Music(bot)))
