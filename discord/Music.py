import lava_lyra
import discord
from globalenv import bot, config, on_close_tasks, get_server_config, set_server_config, get_all_server_config_key
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
from datetime import datetime, timezone
from urllib.parse import urlparse, quote
import i18n
from i18n import t

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

MUSIC_SAVED_STATE_KEY = "music_saved_queue"
MUSIC_SAVED_STATE_VERSION = 2

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
class Music(commands.GroupCog,
            group_name=app_commands.locale_str("music", i18n_key="cmd.music.music.root.name"),
            group_description=app_commands.locale_str("Music commands", i18n_key="cmd.music.music.root.desc")):
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
        self._restore_task: Optional[asyncio.Task] = None
        self._restore_started = False
        self._shutdown_started = False

    @staticmethod
    def _serialize_track(track: Optional[lava_lyra.Track], *, position_ms: Optional[int] = None) -> Optional[dict[str, Any]]:
        if not track:
            return None

        descriptor = {
            "encoded": getattr(track, "track_id", None),
            "uri": getattr(track, "uri", None),
        }
        if position_ms is not None:
            descriptor["position_ms"] = max(0, int(position_ms))

        if not descriptor["encoded"] and not descriptor["uri"]:
            return None
        return descriptor

    @staticmethod
    def _saved_track_descriptors(saved: dict[str, Any]) -> tuple[Optional[dict[str, Any]], list[dict[str, Any]]]:
        """Return current/queue descriptors for both v2 and legacy snapshots."""
        if saved.get("version") == MUSIC_SAVED_STATE_VERSION:
            current = saved.get("current")
            if not isinstance(current, dict):
                current = None
            raw_queue = saved.get("queue")
            queue = [item for item in raw_queue if isinstance(item, dict)] if isinstance(raw_queue, list) else []
            return current, queue

        raw_uris = saved.get("uris")
        uris = [uri for uri in raw_uris if isinstance(uri, str) and uri] if isinstance(raw_uris, list) else []
        if not uris:
            return None, []
        return {"uri": uris[0], "position_ms": 0}, [{"uri": uri} for uri in uris[1:]]

    def _build_music_snapshot(self, guild: discord.Guild, player: lava_lyra.Player) -> Optional[dict[str, Any]]:
        voice_channel = getattr(player, "channel", None)
        if not voice_channel:
            return None

        guild_id = guild.id
        radio_station = radio_modes.get(guild_id)
        queue = get_queue(guild_id)
        current = None
        queued_tracks: list[dict[str, Any]] = []

        if not radio_station:
            try:
                position_ms = int(player.position) if player.current else 0
            except (TypeError, ValueError, AttributeError):
                position_ms = 0
            current = self._serialize_track(player.current, position_ms=position_ms)
            queued_tracks = [
                descriptor
                for track in queue
                if (descriptor := self._serialize_track(track)) is not None
            ]
            if not current and not queued_tracks:
                return None

        text_channel = text_channels.get(guild_id)
        loop_mode = loop_modes.get(guild_id, LoopMode.OFF)
        return {
            "version": MUSIC_SAVED_STATE_VERSION,
            "mode": "radio" if radio_station else "track",
            "voice_channel_id": voice_channel.id,
            "text_channel_id": getattr(text_channel, "id", None),
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "current": current,
            "queue": queued_tracks,
            "volume": max(0, min(100, int(getattr(player, "volume", 100)))),
            "paused": bool(getattr(player, "is_paused", False)),
            "loop_mode": loop_mode.value,
            "radio_station": radio_station,
        }

    @staticmethod
    def _voice_restore_error(guild: discord.Guild, channel: discord.abc.Connectable) -> Optional[str]:
        if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
            return t("music.err.voice_type_unsupported")

        bot_member = guild.me
        if not bot_member:
            return t("music.err.no_bot_member_data")

        permissions = channel.permissions_for(bot_member)
        missing = [
            name
            for name in ("view_channel", "connect", "speak")
            if not getattr(permissions, name, False)
        ]
        if missing:
            return t("music.err.missing_voice_permissions", missing=", ".join(missing))

        user_limit = int(getattr(channel, "user_limit", 0) or 0)
        if user_limit and len(getattr(channel, "members", [])) >= user_limit and not getattr(permissions, "move_members", False):
            return t("music.err.voice_channel_full")

        if isinstance(channel, discord.StageChannel) and not getattr(permissions, "mute_members", False):
            return t("music.err.stage_needs_mute_permission")
        return None

    async def _resolve_restore_text_channel(self, guild: discord.Guild, channel_id: Any):
        try:
            channel_id = int(channel_id)
        except (TypeError, ValueError):
            return None

        channel = self.bot.get_channel(channel_id)
        if channel is None:
            get_channel_or_thread = getattr(guild, "get_channel_or_thread", None)
            channel = get_channel_or_thread(channel_id) if get_channel_or_thread else guild.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except (discord.DiscordException, AttributeError):
                return None

        if getattr(getattr(channel, "guild", None), "id", guild.id) != guild.id or not hasattr(channel, "send"):
            return None

        try:
            permissions = channel.permissions_for(guild.me)
            can_send = getattr(permissions, "send_messages_in_threads", False) if isinstance(channel, discord.Thread) else getattr(permissions, "send_messages", False)
            if not getattr(permissions, "view_channel", False) or not can_send:
                return None
        except (AttributeError, TypeError):
            return None
        return channel

    async def _load_saved_track(self, source, descriptor: dict[str, Any]) -> lava_lyra.Track:
        encoded = descriptor.get("encoded")
        uri = descriptor.get("uri")
        encoded_error: Optional[Exception] = None

        if encoded:
            node = getattr(source, "node", source)
            try:
                return await node.build_track(encoded)
            except Exception as e:
                encoded_error = e

        if uri:
            try:
                results = await source.get_tracks(uri)
                if results:
                    return results.tracks[0] if isinstance(results, lava_lyra.Playlist) else results[0]
            except Exception as e:
                if encoded_error:
                    raise RuntimeError(t("music.err.saved_track_load_failed_both", uri=uri)) from e
                raise

        if encoded_error:
            raise RuntimeError(t("music.err.saved_track_decode_failed")) from encoded_error
        raise RuntimeError(t("music.err.saved_track_missing_data"))

    async def _send_restore_status(self, channel, message: str, guild: discord.Guild, *, level: int = logging.INFO):
        log(message, level=level, module_name="Music", guild=guild)
        if not channel:
            return
        try:
            await channel.send(message)
        except Exception as e:
            log(f"Failed to send music restore notification: {e}", level=logging.WARNING, module_name="Music", guild=guild)

    async def _discard_restore_player(self, guild_id: int, player: Optional[lava_lyra.Player]):
        if player:
            try:
                await player.stop()
            except Exception:
                pass
            try:
                await player.destroy()
            except Exception:
                pass
        await self._cleanup_player(guild_id)

    def _start_empty_channel_timer(self, guild: discord.Guild, player: lava_lyra.Player):
        channel = getattr(player, "channel", None)
        if not channel:
            return
        human_count = sum(1 for member in channel.members if not member.bot)
        if human_count == 0 and guild.id not in leave_timers:
            leave_timers[guild.id] = asyncio.create_task(self._auto_leave_after_timeout(guild.id, player))

    async def _restore_saved_session(self, guild: discord.Guild, saved: dict[str, Any]) -> bool:
        async with i18n.guild_scope(guild.id):
            return await self._restore_saved_session_impl(guild, saved)

    async def _restore_saved_session_impl(self, guild: discord.Guild, saved: dict[str, Any]) -> bool:
        text_channel = await self._resolve_restore_text_channel(guild, saved.get("text_channel_id"))
        voice_channel = guild.get_channel(saved.get("voice_channel_id"))
        if voice_channel is None:
            await self._send_restore_status(text_channel, t("music.err.restore_voice_channel_gone"), guild, level=logging.WARNING)
            return False

        permission_error = self._voice_restore_error(guild, voice_channel)
        if permission_error:
            await self._send_restore_status(text_channel, t("music.err.restore_voice_permission", reason=permission_error), guild, level=logging.WARNING)
            return False

        if guild.voice_client:
            await self._send_restore_status(text_channel, t("music.err.restore_already_connected"), guild, level=logging.WARNING)
            return False

        mode = saved.get("mode", "track")
        loaded_current = None
        loaded_queue: list[lava_lyra.Track] = []
        current_descriptor, queue_descriptors = self._saved_track_descriptors(saved)

        try:
            if mode == "track":
                if not current_descriptor and not queue_descriptors:
                    raise RuntimeError(t("music.err.saved_state_no_tracks"))
                node = lava_lyra.NodePool.get_node()
                if current_descriptor:
                    loaded_current = await self._load_saved_track(node, current_descriptor)
                for descriptor in queue_descriptors:
                    loaded_queue.append(await self._load_saved_track(node, descriptor))
            elif mode == "radio":
                if saved.get("radio_station") not in RADIO_STATIONS:
                    raise RuntimeError(t("music.err.saved_radio_missing"))
            else:
                raise RuntimeError(t("music.err.restore_unsupported_mode_raw", mode=mode))
        except Exception as e:
            await self._send_restore_status(text_channel, t("music.err.restore_preload_failed", error=str(e)), guild, level=logging.WARNING)
            return False

        player: Optional[lava_lyra.Player] = None
        try:
            player = await voice_channel.connect(cls=lava_lyra.Player)
            if isinstance(voice_channel, discord.StageChannel):
                await guild.me.edit(suppress=False)

            if text_channel:
                text_channels[guild.id] = text_channel

            volume = max(0, min(100, int(saved.get("volume", 100))))
            await player.set_volume(volume)

            if mode == "radio":
                station = RADIO_STATIONS[saved["radio_station"]]
                await self._activate_radio_mode(guild, text_channel, player, station)
                if saved.get("paused"):
                    await player.set_pause(True)
            else:
                try:
                    loop_modes[guild.id] = LoopMode(int(saved.get("loop_mode", LoopMode.OFF.value)))
                except (TypeError, ValueError):
                    loop_modes[guild.id] = LoopMode.OFF

                queue = get_queue(guild.id)
                queue.clear()
                start_position = 0
                track_to_play = loaded_current
                if track_to_play and current_descriptor:
                    saved_position = max(0, int(current_descriptor.get("position_ms", 0) or 0))
                    length = int(getattr(track_to_play, "length", 0) or 0)
                    if length > 0 and saved_position >= length:
                        track_to_play = None
                    elif getattr(track_to_play, "is_seekable", False):
                        start_position = saved_position

                remaining_tracks = list(loaded_queue)
                if track_to_play is None and remaining_tracks:
                    track_to_play = remaining_tracks.pop(0)
                if track_to_play is None:
                    if not set_server_config(guild.id, MUSIC_SAVED_STATE_KEY, None):
                        raise RuntimeError(t("music.err.clear_played_state_failed"))
                    await self._discard_restore_player(guild.id, player)
                    await self._send_restore_status(text_channel, t("music.msg.restore_played_out"), guild)
                    return True

                for track in remaining_tracks:
                    queue.add(track)
                await player.play(track_to_play, start=start_position)
                if saved.get("paused"):
                    await player.set_pause(True)

            if not set_server_config(guild.id, MUSIC_SAVED_STATE_KEY, None):
                raise RuntimeError(t("music.err.clear_restored_state_failed"))
            self._start_empty_channel_timer(guild, player)
            await self._send_restore_status(text_channel, t("music.msg.restore_auto_done"), guild)
            return True
        except Exception as e:
            await self._discard_restore_player(guild.id, player)
            await self._send_restore_status(text_channel, t("music.err.restore_failed", error=str(e)), guild, level=logging.WARNING)
            return False

    async def _restore_saved_sessions(self):
        saved_states = get_all_server_config_key(MUSIC_SAVED_STATE_KEY)
        for guild_id, saved in saved_states.items():
            if not isinstance(saved, dict):
                continue
            if saved.get("version") != MUSIC_SAVED_STATE_VERSION:
                if saved.get("uris"):
                    guild = self.bot.get_guild(guild_id)
                    log("Found a legacy music queue; missing voice channel ID, use /music restore-queue to restore manually.", level=logging.WARNING, module_name="Music", guild=guild)
                continue

            guild = self.bot.get_guild(guild_id)
            if not guild:
                log(f"Could not find the server ({guild_id}) for the saved music state; data was kept.", level=logging.WARNING, module_name="Music")
                continue
            try:
                await self._restore_saved_session(guild, saved)
            except Exception as e:
                log(f"Unexpected error while restoring server music state: {e}", level=logging.ERROR, module_name="Music", guild=guild)

    def _schedule_saved_sessions_restore(self):
        if self._restore_started:
            return
        self._restore_started = True
        self._restore_task = asyncio.create_task(self._restore_saved_sessions())

        def _restore_finished(task: asyncio.Task):
            if self._restore_task is task:
                self._restore_task = None
            if task.cancelled():
                return
            error = task.exception()
            if error:
                log(f"Auto-restore music background task failed: {error}", level=logging.ERROR, module_name="Music")

        self._restore_task.add_done_callback(_restore_finished)
    
    async def _ensure_voice(self, ctx: commands.Context) -> Optional[lava_lyra.Player]:
        """確保使用者在語音頻道並返回播放器"""
        if not ctx.author.voice or not ctx.author.voice.channel:
            await ctx.send(t("music.err.join_voice"))
            return None
        
        player: lava_lyra.Player = ctx.guild.voice_client
        if player and player.channel:
            if ctx.author.voice.channel.id != player.channel.id:
                await ctx.send(t("music.err.same_voice_channel"))
                return None
        
        if not player:
            try:
                player = await ctx.author.voice.channel.connect(cls=lava_lyra.Player)
                text_channels[ctx.guild.id] = ctx.channel
            except Exception as e:
                await ctx.send(t("music.err.connect_failed", error=str(e)))
                return None
        return player
    
    def _check_voice_channel(self, user: discord.Member, guild: discord.Guild) -> Optional[str]:
        """檢查用戶是否與機器人在同一語音頻道，返回錯誤訊息或 None"""
        player: lava_lyra.Player = guild.voice_client
        if player and player.channel:
            if not user.voice or not user.voice.channel:
                return t("music.err.join_voice")
            if user.voice.channel.id != player.channel.id:
                return t("music.err.same_voice_channel")
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

        message = t("music.err.radio_mode_active", station=station.display_name)
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

        for guild in self.bot.guilds:
            if not self._guild_has_active_radio_station(guild, station_key):
                continue
            if self._radio_last_announced.get(guild.id) == signature:
                continue

            text_channel = text_channels.get(guild.id)
            if not text_channel:
                continue

            try:
                async with i18n.guild_scope(guild.id):
                    await text_channel.send(embed=self._build_radio_embed(station))
                self._radio_last_announced[guild.id] = signature
            except Exception as e:
                log(f"Failed to send radio track-change notification: {e}", level=logging.WARNING, module_name="Music", guild=guild)

            try:
                from Explore import _emit_music_update
                asyncio.create_task(_emit_music_update(guild.id))
            except Exception:
                pass

    def _parse_listen_moe_payload(self, payload: dict[str, Any]) -> Optional[dict[str, Any]]:
        song = payload.get("song") or {}
        if not song:
            return None

        title = song.get("title") or "Unknown Title"
        artists = [artist for artist in (song.get("artists") or []) if isinstance(artist, dict)]
        artist_names = [artist.get("nameRomaji") or artist.get("name") for artist in artists if artist.get("nameRomaji") or artist.get("name")]
        artist_text = ", ".join(artist_names) if artist_names else "Unknown Artist"
        primary_artist = artists[0] if artists else None
        artist_image = primary_artist.get("image") if primary_artist else None
        artist_id = primary_artist.get("id") if primary_artist else None
        artist_url = f"https://listen.moe/artists/{artist_id}" if artist_id else None
        if artist_image:
            artist_image = f"https://cdn.listen.moe/artists/{quote(artist_image)}"

        sources = [source for source in (song.get("sources") or []) if isinstance(source, dict)]
        source = sources[0] if sources else {}
        source_name = source.get("nameRomaji") or source.get("name")

        album_image = None
        albums = [album for album in (song.get("albums") or []) if isinstance(album, dict)]
        album = albums[0] if albums else None
        album_name = (album.get("nameRomaji") or album.get("name")) if album else None
        if album and album.get("image"):
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
        display = info.get("display") or t("music.value.fetching_radio_info")

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
            embed.add_field(name=t("music.field.listener_count"), value=str(info["listeners"]), inline=True)
        if info.get("progress_text"):
            embed.add_field(name=t("music.field.progress"), value=info["progress_text"], inline=False)
        if info.get("thumbnail"):
            embed.set_thumbnail(url=info["thumbnail"])
        # embed.set_footer(text="電台模式下不能新增歌曲或使用隊列功能")
        return embed

    async def _play_radio_stream(self, player: lava_lyra.Player, station: RadioStation):
        results = await player.get_tracks(station.stream_url)
        if not results:
            raise RuntimeError(t("music.err.radio_stream_load_failed", station=station.display_name))

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

    def _check_valid_query(self, query: str) -> bool:
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
            log("No Lavalink nodes configured; set lavalink_nodes in config.json", level=logging.ERROR, module_name="Music")
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
                log(f"Created Lavalink node: {display_name} ({node_config.get('host')}:{node_config.get('port')})", module_name="Music")
            except Exception as e:
                log(f"Failed to connect to Lavalink node {display_name}: {e}", level=logging.ERROR, module_name="Music")
        
        if connected == 0:
            log("All Lavalink nodes failed to connect", level=logging.ERROR, module_name="Music")
        else:
            log(f"Successfully connected {connected}/{len(lavalink_nodes)} Lavalink nodes", module_name="Music")
            self._schedule_saved_sessions_restore()
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
                            title=t("music.embed.left_voice_channel_title"),
                            description=message,
                            color=0x95a5a6
                        )
                        await text_channel.send(embed=embed)
                    except Exception as e:
                        log(f"Failed to send notification: {e}", level=logging.WARNING, module_name="Music")

            # 清理資源
            music_queues.pop(guild_id, None)
            text_channels.pop(guild_id, None)
            loop_modes.pop(guild_id, None)
            radio_modes.pop(guild_id, None)
            self._radio_last_announced.pop(guild_id, None)
            if radio_station:
                await self._stop_radio_listener_if_unused(radio_station)

            try:
                from Explore import _emit_music_update
                asyncio.create_task(_emit_music_update(guild_id))
            except Exception:
                pass

        except Exception as e:
            log(f"Error while cleaning up the player: {e}", level=logging.ERROR, module_name="Music")

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
                        message=t("music.msg.left_voice_no_members")
                    )
        except asyncio.CancelledError:
            pass
        finally:
            leave_timers.pop(guild_id, None)
    
    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        """當語音狀態變化時，檢查是否需要啟動或取消自動離開計時器"""
        async with i18n.guild_scope(member.guild.id):
            await self._on_voice_state_update_impl(member, before, after)

    async def _on_voice_state_update_impl(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        guild_id = member.guild.id

        # 檢查是否是機器人自己被踢出或離開
        if member.id == self.bot.user.id:
            # 機器人離開了語音頻道
            if before.channel and not after.channel:
                log(f"Bot left the voice channel", module_name="Music", guild=member.guild)
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
                log(f"Started the 5-minute auto-leave timer", module_name="Music", guild=member.guild)
        else:
            # 有真人，取消計時器
            if guild_id in leave_timers:
                leave_timers[guild_id].cancel()
                leave_timers.pop(guild_id, None)
                log(f"Cancelled the auto-leave timer", module_name="Music", guild=member.guild)
    
    @commands.Cog.listener()
    async def on_lyra_track_start(self, player: lava_lyra.Player, track: lava_lyra.Track):
        """當音樂開始播放時"""
        if not player:
            return
        async with i18n.guild_scope(player.guild.id):
            await self._on_lyra_track_start_impl(player, track)

    async def _on_lyra_track_start_impl(self, player: lava_lyra.Player, track: lava_lyra.Track):
        station = self._get_guild_radio_station(player.guild.id)
        if station:
            return
        
        embed = discord.Embed(
            title=t("music.embed.track_started_title"),
            description=f"**[{track.title}]({track.uri})**",
            color=0x3498db
        )
        embed.set_thumbnail(url=track.thumbnail)
        if track.author:
            embed.add_field(name=t("music.field.artist"), value=track.author, inline=True)
        embed.add_field(
            name=t("music.field.duration"), 
            value=f"{int(track.length / 1000 // 60)}:{int(track.length / 1000 % 60):02d}",
            inline=True
        )
        
        try:
            text_channel = text_channels.get(player.guild.id)
            if text_channel:
                await text_channel.send(embed=embed)
        except Exception as e:
            log(f"Failed to send playback notification: {e}", level=logging.WARNING, module_name="Music")

        try:
            from Explore import _emit_music_update
            asyncio.create_task(_emit_music_update(player.guild.id))
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_lyra_track_end(self, player: lava_lyra.Player, track: lava_lyra.Track, reason: Optional[str]):
        """當音樂結束播放時"""
        if not player:
            return
        async with i18n.guild_scope(player.guild.id):
            await self._on_lyra_track_end_impl(player, track, reason)

    async def _on_lyra_track_end_impl(self, player: lava_lyra.Player, track: lava_lyra.Track, reason: Optional[str]):
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
                log(f"Radio stream reconnect failed: {e}", level=logging.ERROR, module_name="Music", guild=player.guild)
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
                log(f"Track-loop replay failed: {e}", level=logging.ERROR, module_name="Music", guild=player.guild)
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
                log(f"Failed to play the next track: {e}", level=logging.ERROR, module_name="Music", guild=player.guild)
        else:
            # 離開語音頻道並清理資料
            try:
                await player.destroy()
            except:
                pass

            await self._cleanup_player(
                guild_id,
                send_message=True,
                message=t("music.msg.left_voice_queue_empty")
            )
    
    async def music_quit_task(self):
        """機器人關閉時，先保存所有播放工作階段再清理播放器。"""
        if self._shutdown_started:
            return
        self._shutdown_started = True

        restore_task = self._restore_task
        if restore_task and not restore_task.done() and restore_task is not asyncio.current_task():
            restore_task.cancel()
            await asyncio.gather(restore_task, return_exceptions=True)

        active_sessions = []
        for guild in list(self.bot.guilds):
            player: lava_lyra.Player = guild.voice_client
            if not player:
                continue

            try:
                snapshot = self._build_music_snapshot(guild, player)
                snapshot_saved = False
                if snapshot:
                    snapshot_saved = set_server_config(guild.id, MUSIC_SAVED_STATE_KEY, snapshot)
                    if not snapshot_saved:
                        log("Failed to save music playback state", level=logging.ERROR, module_name="Music", guild=guild)
                active_sessions.append((guild, player, text_channels.get(guild.id), snapshot_saved))
            except Exception as e:
                log(f"Error building shutdown music snapshot: {e}", level=logging.ERROR, module_name="Music", guild=guild)
                active_sessions.append((guild, player, text_channels.get(guild.id), False))

        # 所有 guild 都完成同步寫入後，才開始任何網路通知或播放器清理。
        for guild, player, channel, snapshot_saved in active_sessions:
            if channel:
                try:
                    async with i18n.guild_scope(guild.id):
                        description = t("music.embed.shutdown_desc")
                        if snapshot_saved:
                            description += t("music.embed.shutdown_desc_restore_hint")
                        embed = discord.Embed(
                            title=t("music.embed.shutdown_title"),
                            description=description,
                            color=0x95a5a6,
                        )
                        await channel.send(embed=embed)
                except Exception as e:
                    log(f"Failed to send shutdown music notification: {e}", level=logging.WARNING, module_name="Music", guild=guild)

            try:
                await player.stop()
                await player.destroy()
            except Exception as e:
                log(f"Failed to clean up player on shutdown: {e}", level=logging.WARNING, module_name="Music", guild=guild)
    
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
            log(f"Autocomplete search error: {e}", level=logging.WARNING, module_name="Music")
            return []

    @app_commands.command(name=app_commands.locale_str("search", i18n_key="cmd.music.music.search.name"), description=app_commands.locale_str("Search for and play music", i18n_key="cmd.music.music.search.desc"))
    @app_commands.describe(query=app_commands.locale_str("Search for a track (autocomplete supported)", i18n_key="cmd.music.music.search.param.query"))
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
            await interaction.followup.send(t("music.err.join_voice_to_play"), ephemeral=True)
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
                await interaction.followup.send(t("music.err.connect_failed", error=str(e)), ephemeral=True)
                return

        guild_id = interaction.guild.id
        queue = get_queue(guild_id)

        # 如果 query 是 URI（從自動完成選擇的），直接使用
        # 否則進行搜尋
        try:
            if not self._check_valid_query(query):
                await interaction.followup.send(t("music.err.invalid_query"), ephemeral=True)
                return

            if query.startswith(("http://", "https://", "ytsearch:", "scsearch:")):
                results = await player.get_tracks(query)
            else:
                results = await player.get_tracks(f"ytsearch:{query}")

            if not results:
                await interaction.followup.send(t("music.err.no_results", query=query), ephemeral=True)
                return

            # 如果結果是播放列表
            if isinstance(results, lava_lyra.Playlist):
                tracks = results.tracks
                embed = discord.Embed(
                    title=t("music.embed.playlist_added_title"),
                    description=f"**{results.name}**",
                    color=0x2ecc71
                )
                embed.add_field(name=t("music.field.track_count"), value=len(tracks), inline=True)
                embed.add_field(name=t("music.field.total_duration"), value=self._format_duration(sum(tr.length for tr in tracks)), inline=True)
                await interaction.followup.send(embed=embed)

                for track in tracks:
                    queue.add(track)
            else:
                # 單個搜尋結果
                track = results[0]
                queue.add(track)

                embed = discord.Embed(
                    title=t("music.embed.track_added_title"),
                    description=f"**[{track.title}]({track.uri})**",
                    color=0x2ecc71
                )
                embed.set_thumbnail(url=track.thumbnail)
                if track.author:
                    embed.add_field(name=t("music.field.artist"), value=track.author, inline=True)
                embed.add_field(
                    name=t("music.field.duration"),
                    value=self._format_duration(track.length),
                    inline=True
                )
                embed.add_field(name=t("music.field.queue_position"), value=len(queue), inline=True)
                await interaction.followup.send(embed=embed)

            # 開始播放
            if not player.is_playing:
                next_track = queue.get()
                if next_track:
                    try:
                        await player.play(next_track)
                    except Exception as e:
                        log(f"Failed to start playback: {e}", level=logging.ERROR, module_name="Music", guild=interaction.guild)
                        await interaction.followup.send(t("music.msg.track_added_play_warning", error=str(e)), ephemeral=True)

        except Exception as e:
            log(f"Search & play error: {e}", level=logging.ERROR, module_name="Music", guild=interaction.guild)
            await interaction.followup.send(t("music.err.search_play_failed", error=str(e)), ephemeral=True)

    @app_commands.command(name=app_commands.locale_str("play", i18n_key="cmd.music.music.play.name"), description=app_commands.locale_str("Play music", i18n_key="cmd.music.music.play.desc"))
    @app_commands.describe(query=app_commands.locale_str("Track name or URL", i18n_key="cmd.music.music.play.param.query"))
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
            await interaction.followup.send(t("music.err.join_voice_to_play"), ephemeral=True)
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
                await interaction.followup.send(t("music.err.connect_failed", error=str(e)), ephemeral=True)
                return
        
        guild_id = interaction.guild.id
        queue = get_queue(guild_id)
        
        # 搜尋歌曲
        try:
            if not self._check_valid_query(query):
                await interaction.followup.send(t("music.err.invalid_query"), ephemeral=True)
                return

            results = await player.get_tracks(query)
            
            if not results:
                await interaction.followup.send(t("music.err.no_results", query=query), ephemeral=True)
                return
            
            # 如果結果是播放列表
            if isinstance(results, lava_lyra.Playlist):
                tracks = results.tracks
                embed = discord.Embed(
                    title=t("music.embed.playlist_added_title"),
                    description=f"**{results.name}**",
                    color=0x2ecc71
                )
                embed.add_field(name=t("music.field.track_count"), value=len(tracks), inline=True)
                embed.add_field(name=t("music.field.total_duration"), value=self._format_duration(sum(tr.length for tr in tracks)), inline=True)
                await interaction.followup.send(embed=embed)
                
                for track in tracks:
                    queue.add(track)
            else:
                # 如果是單個搜尋結果
                track = results[0]
                queue.add(track)
                
                embed = discord.Embed(
                    title=t("music.embed.track_added_title"),
                    description=f"**[{track.title}]({track.uri})**",
                    color=0x2ecc71
                )
                embed.set_thumbnail(url=track.thumbnail)
                if track.author:
                    embed.add_field(name=t("music.field.artist"), value=track.author, inline=True)
                embed.add_field(
                    name=t("music.field.duration"),
                    value=self._format_duration(track.length),
                    inline=True
                )
                embed.add_field(name=t("music.field.queue_position"), value=len(queue), inline=True)
                await interaction.followup.send(embed=embed)
            
            # 開始播放
            if not player.is_playing:
                next_track = queue.get()
                if next_track:
                    try:
                        await player.play(next_track)
                    except Exception as e:
                        log(f"Failed to start playback: {e}", level=logging.ERROR, module_name="Music", guild=interaction.guild)
                        await interaction.followup.send(t("music.msg.track_added_play_warning", error=str(e)), ephemeral=True)

        except Exception as e:
            log(f"Playback error: {e}", level=logging.ERROR, module_name="Music", guild=interaction.guild)
            await interaction.followup.send(t("music.err.play_failed", error=str(e)), ephemeral=True)

    @app_commands.command(name=app_commands.locale_str("radio", i18n_key="cmd.music.music.radio.name"), description=app_commands.locale_str("Switch to radio mode", i18n_key="cmd.music.music.radio.desc"))
    @app_commands.describe(station=app_commands.locale_str("The station to play", i18n_key="cmd.music.music.radio.param.station"))
    @app_commands.choices(station=[
        app_commands.Choice(name=app_commands.locale_str("LISTEN.moe", i18n_key="cmd.music.music.radio.choice.listenmoe"), value="listenmoe"),
        app_commands.Choice(name=app_commands.locale_str("R/a/dio", i18n_key="cmd.music.music.radio.choice.r_a_dio"), value="r-a-dio"),
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
            await interaction.followup.send(t("music.err.unsupported_radio_station"), ephemeral=True)
            return

        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.followup.send(t("music.err.join_voice_to_radio"), ephemeral=True)
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
                await interaction.followup.send(t("music.err.connect_failed", error=str(e)), ephemeral=True)
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
                await interaction.followup.send(t("music.msg.radio_switching", station=station_info.display_name))
        except Exception as e:
            log(f"Failed to switch radio mode: {e}", level=logging.ERROR, module_name="Music", guild=interaction.guild)
            await interaction.followup.send(t("music.err.radio_switch_failed", station=station_info.display_name, error=str(e)), ephemeral=True)
    
    @app_commands.command(name=app_commands.locale_str("pause", i18n_key="cmd.music.music.pause.name"), description=app_commands.locale_str("Pause playback", i18n_key="cmd.music.music.pause.desc"))
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
            await interaction.followup.send(t("music.err.no_player"), ephemeral=True)
            return
        
        if player.is_paused:
            await interaction.followup.send(t("music.err.already_paused"), ephemeral=True)
            return
        
        try:
            await player.set_pause(True)
            await interaction.followup.send(t("music.msg.paused"))
        except Exception as e:
            await interaction.followup.send(t("music.err.pause_failed", error=str(e)), ephemeral=True)
    
    @app_commands.command(name=app_commands.locale_str("resume", i18n_key="cmd.music.music.resume.name"), description=app_commands.locale_str("Resume playback", i18n_key="cmd.music.music.resume.desc"))
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
            await interaction.followup.send(t("music.err.no_paused_player"), ephemeral=True)
            return
        
        if not player.is_paused:
            await interaction.followup.send(t("music.err.not_paused"), ephemeral=True)
            return
        
        try:
            await player.set_pause(False)
            await interaction.followup.send(t("music.msg.resumed"))
        except Exception as e:
            await interaction.followup.send(t("music.err.resume_failed", error=str(e)), ephemeral=True)
    
    @app_commands.command(name=app_commands.locale_str("stop", i18n_key="cmd.music.music.stop.name"), description=app_commands.locale_str("Stop playback and disconnect", i18n_key="cmd.music.music.stop.desc"))
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
            await interaction.followup.send(t("music.err.no_player"), ephemeral=True)
            return
        
        try:
            await player.stop()
            await player.destroy()
            await self._cleanup_player(interaction.guild.id)
            await interaction.followup.send(t("music.msg.stopped"))
        except Exception as e:
            await interaction.followup.send(t("music.err.stop_failed", error=str(e)), ephemeral=True)
    
    @app_commands.command(name=app_commands.locale_str("skip", i18n_key="cmd.music.music.skip.name"), description=app_commands.locale_str("Skip the current track", i18n_key="cmd.music.music.skip.desc"))
    @app_commands.guild_only()
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def skip(self, interaction: discord.Interaction):
        """跳過當前歌曲"""
        await interaction.response.defer()

        station = self._get_guild_radio_station(interaction.guild.id)
        if station:
            await interaction.followup.send(t("music.err.radio_no_skip", station=station.display_name), ephemeral=True)
            return
        
        error_msg = self._check_voice_channel(interaction.user, interaction.guild)
        if error_msg:
            await interaction.followup.send(error_msg, ephemeral=True)
            return
        
        player: lava_lyra.Player = interaction.guild.voice_client
        if not player or not player.is_playing:
            await interaction.followup.send(t("music.err.no_player"), ephemeral=True)
            return
        
        try:
            current_track = player.current
            await player.stop()

            embed = discord.Embed(
                title=t("music.embed.track_skipped_title"),
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
                    log(f"Failed to play next track after skip: {e}", level=logging.ERROR, module_name="Music", guild=interaction.guild)
                    await interaction.followup.send(t("music.msg.next_track_failed_warning", error=str(e)), ephemeral=True)
        except Exception as e:
            await interaction.followup.send(t("music.err.skip_failed", error=str(e)), ephemeral=True)
    
    @app_commands.command(name=app_commands.locale_str("queue", i18n_key="cmd.music.music.queue.name"), description=app_commands.locale_str("View the playback queue", i18n_key="cmd.music.music.queue.desc"))
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
            await interaction.followup.send(t("music.err.no_player"), ephemeral=True)
            return
        
        if not player.current and queue.is_empty:
            await interaction.followup.send(t("music.err.queue_empty"), ephemeral=True)
            return
        
        embed = discord.Embed(title=t("music.embed.queue_title"), color=0x3498db)
        
        # 顯示當前播放的歌曲
        if player.current:
            embed.description = t("music.embed.queue_now_playing_line", title=player.current.title, uri=player.current.uri)
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
                    name=t("music.field.upcoming_tracks", count=len(queue)),
                    value="\n".join(queue_list),
                    inline=False
                )
            
            if len(queue) > 10:
                embed.add_field(name=t("music.field.more_tracks"), value=t("music.value.more_tracks_count", count=len(queue) - 10), inline=False)
            
            embed.add_field(
                name=t("music.field.queue_total_duration"),
                value=self._format_duration(total_duration),
                inline=True
            )
        
        embed.set_footer(text=t("music.footer.queue_count", count=len(queue)))
        await interaction.followup.send(embed=embed)
    
    @app_commands.command(name=app_commands.locale_str("restore-queue", i18n_key="cmd.music.music.restore_queue.name"), description=app_commands.locale_str("Restore the playback state saved before restart", i18n_key="cmd.music.music.restore_queue.desc"))
    @app_commands.guild_only()
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.checks.bot_has_permissions(connect=True, speak=True)
    async def restore_queue(self, interaction: discord.Interaction):
        """回復重啟前儲存的完整播放狀態，並相容舊版 URI 隊列。"""
        await interaction.response.defer()

        if not await self._ensure_not_radio_mode(interaction, interaction.guild.id):
            return

        saved = get_server_config(interaction.guild.id, MUSIC_SAVED_STATE_KEY)
        if not isinstance(saved, dict):
            await interaction.followup.send(t("music.err.no_saved_state"), ephemeral=True)
            return

        mode = saved.get("mode", "track") if saved.get("version") == MUSIC_SAVED_STATE_VERSION else "track"
        if mode not in ("track", "radio"):
            await interaction.followup.send(t("music.err.unsupported_saved_mode", mode=mode), ephemeral=True)
            return
        current_descriptor, queue_descriptors = self._saved_track_descriptors(saved)
        if mode == "track" and not current_descriptor and not queue_descriptors:
            await interaction.followup.send(t("music.err.no_saved_state"), ephemeral=True)
            return
        if mode == "radio" and saved.get("radio_station") not in RADIO_STATIONS:
            await interaction.followup.send(t("music.err.saved_radio_gone"), ephemeral=True)
            return

        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.followup.send(t("music.err.join_voice"), ephemeral=True)
            return

        guild = interaction.guild
        voice_channel = interaction.user.voice.channel
        permission_error = self._voice_restore_error(guild, voice_channel)
        if permission_error:
            await interaction.followup.send(t("music.err.voice_restore_failed", reason=permission_error), ephemeral=True)
            return

        player: Optional[lava_lyra.Player] = guild.voice_client
        if player and voice_channel.id != player.channel.id:
            await interaction.followup.send(t("music.err.same_voice_channel"), ephemeral=True)
            return
        if mode == "radio" and player and (player.is_playing or player.current):
            await interaction.followup.send(t("music.err.stop_before_restore_radio"), ephemeral=True)
            return

        guild_id = guild.id
        loaded_entries: list[tuple[dict[str, Any], lava_lyra.Track, bool]] = []
        failed = 0
        if mode == "track":
            try:
                source = player or lava_lyra.NodePool.get_node()
            except Exception as e:
                await interaction.followup.send(t("music.err.no_lavalink_node", error=str(e)), ephemeral=True)
                return
            descriptors = []
            if current_descriptor:
                descriptors.append((current_descriptor, True))
            descriptors.extend((descriptor, False) for descriptor in queue_descriptors)

            for descriptor, is_current in descriptors:
                try:
                    loaded_entries.append((descriptor, await self._load_saved_track(source, descriptor), is_current))
                except Exception as e:
                    identifier = descriptor.get("uri") or "encoded track"
                    log(f"Failed to load saved track {identifier}: {e}", level=logging.WARNING, module_name="Music", guild=guild)
                    failed += 1

            if not loaded_entries:
                await interaction.followup.send(t("music.err.all_saved_tracks_failed", failed=failed), ephemeral=True)
                return

        created_player = False
        if not player:
            try:
                player = await voice_channel.connect(cls=lava_lyra.Player)
                created_player = True
                if isinstance(voice_channel, discord.StageChannel):
                    await guild.me.edit(suppress=False)
            except Exception as e:
                await interaction.followup.send(t("music.err.connect_failed", error=str(e)), ephemeral=True)
                return

        text_channels[guild_id] = interaction.channel
        queue = get_queue(guild_id)
        added = len(loaded_entries)

        try:
            if saved.get("version") == MUSIC_SAVED_STATE_VERSION:
                await player.set_volume(max(0, min(100, int(saved.get("volume", 100)))))

            if mode == "radio":
                station = RADIO_STATIONS[saved["radio_station"]]
                await self._activate_radio_mode(guild, interaction.channel, player, station)
                if saved.get("paused"):
                    await player.set_pause(True)
                added = 1
            else:
                if saved.get("version") == MUSIC_SAVED_STATE_VERSION:
                    try:
                        loop_modes[guild_id] = LoopMode(int(saved.get("loop_mode", LoopMode.OFF.value)))
                    except (TypeError, ValueError):
                        loop_modes[guild_id] = LoopMode.OFF

                if player.is_playing or player.current:
                    for _, track, _ in loaded_entries:
                        queue.add(track)
                else:
                    descriptor, track_to_play, is_saved_current = loaded_entries.pop(0)
                    start_position = 0
                    if is_saved_current:
                        saved_position = max(0, int(descriptor.get("position_ms", 0) or 0))
                        length = int(getattr(track_to_play, "length", 0) or 0)
                        if length > 0 and saved_position >= length:
                            added -= 1
                            if loaded_entries:
                                descriptor, track_to_play, is_saved_current = loaded_entries.pop(0)
                            else:
                                if not set_server_config(guild_id, MUSIC_SAVED_STATE_KEY, None):
                                    raise RuntimeError(t("music.err.clear_played_state_failed"))
                                if created_player:
                                    await self._discard_restore_player(guild_id, player)
                                await interaction.followup.send(t("music.msg.restore_played_out"))
                                return
                        elif getattr(track_to_play, "is_seekable", False):
                            start_position = saved_position

                    await player.play(track_to_play, start=start_position)
                    for _, track, _ in loaded_entries:
                        queue.add(track)
                    if saved.get("paused"):
                        await player.set_pause(True)

            if not set_server_config(guild_id, MUSIC_SAVED_STATE_KEY, None):
                raise RuntimeError(t("music.err.clear_restored_state_failed"))
            self._start_empty_channel_timer(guild, player)
        except Exception as e:
            if created_player:
                await self._discard_restore_player(guild_id, player)
            log(f"Manual playback-state restore failed: {e}", level=logging.ERROR, module_name="Music", guild=guild)
            await interaction.followup.send(t("music.err.restore_manual_failed", error=str(e)))
            return

        if mode == "radio":
            msg = t("music.msg.restore_radio_done", station=RADIO_STATIONS[saved['radio_station']].display_name)
        else:
            msg = t("music.msg.restore_tracks_done", count=added)
        if failed:
            msg += t("music.msg.restore_failed_count_suffix", failed=failed)
        await interaction.followup.send(msg)

    @app_commands.command(name=app_commands.locale_str("loop", i18n_key="cmd.music.music.loop.name"), description=app_commands.locale_str("Set the loop mode", i18n_key="cmd.music.music.loop.desc"))
    @app_commands.describe(mode=app_commands.locale_str("Loop mode", i18n_key="cmd.music.music.loop.param.mode"))
    @app_commands.choices(mode=[
        app_commands.Choice(name=app_commands.locale_str("Loop off", i18n_key="cmd.music.music.loop.choice.0"), value=0),
        app_commands.Choice(name=app_commands.locale_str("Loop track", i18n_key="cmd.music.music.loop.choice.1"), value=1),
        app_commands.Choice(name=app_commands.locale_str("Loop queue", i18n_key="cmd.music.music.loop.choice.2"), value=2),
    ])
    @app_commands.guild_only()
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def loop(self, interaction: discord.Interaction, mode: Optional[int] = None):
        """設定循環播放模式"""
        await interaction.response.defer()

        station = self._get_guild_radio_station(interaction.guild.id)
        if station:
            await interaction.followup.send(t("music.err.radio_no_loop", station=station.display_name), ephemeral=True)
            return

        error_msg = self._check_voice_channel(interaction.user, interaction.guild)
        if error_msg:
            await interaction.followup.send(error_msg, ephemeral=True)
            return

        player: lava_lyra.Player = interaction.guild.voice_client
        if not player:
            await interaction.followup.send(t("music.err.no_player"), ephemeral=True)
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

        mode_display = {LoopMode.OFF: t("music.value.loop_off"), LoopMode.TRACK: t("music.value.loop_track"), LoopMode.QUEUE: t("music.value.loop_queue")}
        await interaction.followup.send(mode_display[new_mode])

    @app_commands.command(name=app_commands.locale_str("now-playing", i18n_key="cmd.music.music.now_playing.name"), description=app_commands.locale_str("Show the currently playing track", i18n_key="cmd.music.music.now_playing.desc"))
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
            await interaction.followup.send(t("music.err.no_player"), ephemeral=True)
            return
        
        track = player.current
        
        # 進度條
        position = player.position
        length = track.length
        progress = int((position / length) * 20) if length > 0 else 0
        progress_bar = "█" * progress + "░" * (20 - progress)
        
        embed = discord.Embed(
            title=t("music.embed.current_track_title"),
            description=f"**[{track.title}]({track.uri})**",
            color=0x3498db
        )
        embed.set_thumbnail(url=track.thumbnail)
        
        if track.author:
            embed.add_field(name=t("music.field.artist"), value=track.author, inline=True)
        
        embed.add_field(
            name=t("music.field.progress"),
            value=f"`{progress_bar}`\n{self._format_duration(position)} / {self._format_duration(length)}",
            inline=False
        )
        
        embed.add_field(name=t("music.field.volume"), value=f"{player.volume}%", inline=True)

        loop_mode = loop_modes.get(interaction.guild.id, LoopMode.OFF)
        mode_display = {LoopMode.OFF: t("music.value.loop_off_short"), LoopMode.TRACK: t("music.value.loop_track"), LoopMode.QUEUE: t("music.value.loop_queue")}
        embed.add_field(name=t("music.field.loop_mode"), value=mode_display[loop_mode], inline=True)
        
        await interaction.followup.send(embed=embed)
    
    @app_commands.command(name=app_commands.locale_str("volume", i18n_key="cmd.music.music.volume.name"), description=app_commands.locale_str("Adjust the volume", i18n_key="cmd.music.music.volume.desc"))
    @app_commands.describe(level=app_commands.locale_str("Volume level (0-100)", i18n_key="cmd.music.music.volume.param.level"))
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
            await interaction.followup.send(t("music.err.volume_range"), ephemeral=True)
            return
        
        player: lava_lyra.Player = interaction.guild.voice_client
        if not player:
            await interaction.followup.send(t("music.err.no_player"), ephemeral=True)
            return
        
        try:
            await player.set_volume(level)
            await interaction.followup.send(t("music.msg.volume_set", level=level))
        except Exception as e:
            await interaction.followup.send(t("music.err.volume_failed", error=str(e)), ephemeral=True)
    
    @app_commands.command(name=app_commands.locale_str("shuffle", i18n_key="cmd.music.music.shuffle.name"), description=app_commands.locale_str("Shuffle the queue", i18n_key="cmd.music.music.shuffle.desc"))
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
            await interaction.followup.send(t("music.err.no_player"), ephemeral=True)
            return
        
        if queue.is_empty:
            await interaction.followup.send(t("music.err.queue_empty"), ephemeral=True)
            return
        
        try:
            tracks = list(queue)
            random.shuffle(tracks)
            queue.clear()
            for track in tracks:
                queue.add(track)
            await interaction.followup.send(t("music.msg.queue_shuffled"))
        except Exception as e:
            await interaction.followup.send(t("music.err.shuffle_failed", error=str(e)), ephemeral=True)
    
    @app_commands.command(name=app_commands.locale_str("recommend", i18n_key="cmd.music.music.recommend.name"), description=app_commands.locale_str("Recommend similar tracks based on the current one", i18n_key="cmd.music.music.recommend.desc"))
    @app_commands.describe(count=app_commands.locale_str("How many recommendations to add (1-10, default 5)", i18n_key="cmd.music.music.recommend.param.count"))
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
            await interaction.followup.send(t("music.err.no_player"), ephemeral=True)
            return
        
        count = max(1, min(count, 10))
        
        try:
            results = await player.get_recommendations(track=player.current)
            
            if not results:
                await interaction.followup.send(t("music.err.no_similar_recommendations"), ephemeral=True)
                return
            
            tracks = results.tracks if isinstance(results, lava_lyra.Playlist) else results
            tracks = tracks[:count]
            
            queue = get_queue(interaction.guild.id)
            for track in tracks:
                queue.add(track)
            
            track_list = "\n".join(
                f"{i}. [{tr.title}]({tr.uri})" for i, tr in enumerate(tracks, 1)
            )
            
            embed = discord.Embed(
                title=t("music.embed.recommend_added_title"),
                description=t("music.embed.recommend_desc", title=player.current.title, track_list=track_list),
                color=0x9b59b6
            )
            embed.set_thumbnail(url=player.current.thumbnail)
            embed.add_field(name=t("music.field.added_count"), value=t("music.value.track_count", count=len(tracks)), inline=True)
            embed.add_field(
                name=t("music.field.total_duration"),
                value=self._format_duration(sum(tr.length for tr in tracks)),
                inline=True
            )
            await interaction.followup.send(embed=embed)

            if not player.is_playing:
                next_track = queue.get()
                if next_track:
                    try:
                        await player.play(next_track)
                    except Exception as e:
                        log(f"Failed to start playback after recommend: {e}", level=logging.ERROR, module_name="Music", guild=interaction.guild)
                        await interaction.followup.send(t("music.msg.recommend_added_play_warning", error=str(e)), ephemeral=True)

        except Exception as e:
            log(f"Recommend-tracks error: {e}", level=logging.ERROR, module_name="Music", guild=interaction.guild)
            await interaction.followup.send(t("music.err.recommend_failed", error=str(e)), ephemeral=True)
    
    @app_commands.command(name=app_commands.locale_str("nodes", i18n_key="cmd.music.music.nodes.name"), description=app_commands.locale_str("View Lavalink node status", i18n_key="cmd.music.music.nodes.desc"))
    async def nodes_command(self, interaction: discord.Interaction):
        """查看 Lavalink 節點狀態"""
        await interaction.response.defer()
        embed = discord.Embed(title=t("music.embed.nodes_title"), color=0x3498db)
        for identifier, node in lava_lyra.NodePool._nodes.items():
            name = self.node_names.get(identifier, identifier)
            status = t("music.value.node_connected") if node.is_connected else t("music.value.node_disconnected")
            if node.is_connected:
                ping = f"{round(node.ping, 2)}ms" if node.is_connected else "N/A"
                status += t("music.embed.node_latency", ping=ping)
                players = node.player_count
                connected_players = len([player for player in node.players.values() if player._is_connected])
                playing_players = len([player for player in node.players.values() if player.is_playing])
                status += t("music.embed.node_usage", playing=playing_players, connected=connected_players, total=players)
                health = round(node.health_score, 2)
                status += t("music.embed.node_health", health=f"{health:.2f}")
                # try to get player and see if current guild is using this node
                if node.players.get(interaction.guild.id):
                    name += t("music.value.node_current_marker")
            embed.add_field(name=name, value=status, inline=False)
        await interaction.followup.send(embed=embed)
    
    def _format_duration(self, milliseconds: int) -> str:
        """將毫秒轉換為 MM:SS 格式"""
        seconds = milliseconds // 1000
        minutes = seconds // 60
        seconds = seconds % 60
        return f"{minutes}:{seconds:02d}"
    
    # ========== 文字指令 ==========
    
    @commands.command(name="play", aliases=["p", "播放"])  # i18n: skip (input syntax)
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
                await ctx.send(t("music.err.resume_no_player"))
                return
            
            if not player.is_paused:
                await ctx.send(t("music.err.resume_not_paused"))
                return
            
            try:
                await player.set_pause(False)
                await ctx.send(t("music.msg.resumed"))
            except Exception as e:
                await ctx.send(t("music.err.resume_failed", error=str(e)))
            return
        
        player = await self._ensure_voice(ctx)
        if not player:
            return
        
        guild_id = ctx.guild.id
        queue = get_queue(guild_id)
        
        try:
            if not self._check_valid_query(query):
                await ctx.send(t("music.err.invalid_query"))
                return
            results = await player.get_tracks(query)
            
            if not results:
                await ctx.send(t("music.err.no_results", query=query))
                return
            
            if isinstance(results, lava_lyra.Playlist):
                tracks = results.tracks
                embed = discord.Embed(
                    title=t("music.embed.playlist_added_title"),
                    description=f"**{results.name}**",
                    color=0x2ecc71
                )
                embed.set_thumbnail(url=results.thumbnail)
                embed.add_field(name=t("music.field.track_count"), value=len(tracks), inline=True)
                embed.add_field(name=t("music.field.total_duration"), value=self._format_duration(sum(tr.length for tr in tracks)), inline=True)
                await ctx.send(embed=embed)
                
                for track in tracks:
                    queue.add(track)
            else:
                track = results[0]
                queue.add(track)
                
                embed = discord.Embed(
                    title=t("music.embed.track_added_title"),
                    description=f"**[{track.title}]({track.uri})**",
                    color=0x2ecc71,
                )
                embed.set_thumbnail(url=track.thumbnail)
                if track.author:
                    embed.add_field(name=t("music.field.artist"), value=track.author, inline=True)
                embed.add_field(
                    name=t("music.field.duration"),
                    value=self._format_duration(track.length),
                    inline=True
                )
                embed.add_field(name=t("music.field.queue_position"), value=len(queue), inline=True)
                await ctx.send(embed=embed)
            
            if not player.is_playing:
                next_track = queue.get()
                if next_track:
                    try:
                        await player.play(next_track)
                    except Exception as e:
                        log(f"Failed to start playback: {e}", level=logging.ERROR, module_name="Music", guild=ctx.guild)
                        await ctx.send(t("music.msg.track_added_play_warning", error=str(e)))

        except Exception as e:
            log(f"Playback error: {e}", level=logging.ERROR, module_name="Music", guild=ctx.guild)
            await ctx.send(t("music.err.play_failed", error=str(e)))

    @commands.command(name="radio", aliases=["station", "電台"])  # i18n: skip (input syntax)
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
            await ctx.send(t("music.err.available_radio_stations"))
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
                await ctx.send(t("music.msg.radio_switching", station=station_info.display_name))
        except Exception as e:
            log(f"Failed to switch radio mode: {e}", level=logging.ERROR, module_name="Music", guild=ctx.guild)
            await ctx.send(t("music.err.radio_switch_failed", station=station_info.display_name, error=str(e)))
    
    @commands.command(name="pause", aliases=["暫停"])  # i18n: skip (input syntax)
    @commands.guild_only()
    async def text_pause(self, ctx: commands.Context):
        """暫停播放"""
        error_msg = self._check_voice_channel(ctx.author, ctx.guild)
        if error_msg:
            await ctx.send(error_msg)
            return
        
        player: lava_lyra.Player = ctx.guild.voice_client
        if not player:
            await ctx.send(t("music.err.no_player"))
            return
        
        if player.is_paused:
            await ctx.send(t("music.err.already_paused"))
            return
        
        try:
            await player.set_pause(True)
            await ctx.send(t("music.msg.paused"))
        except Exception as e:
            await ctx.send(t("music.err.pause_failed", error=str(e)))
    
    @commands.command(name="resume", aliases=["繼續"])  # i18n: skip (input syntax)
    @commands.guild_only()
    async def text_resume(self, ctx: commands.Context):
        """繼續播放"""
        error_msg = self._check_voice_channel(ctx.author, ctx.guild)
        if error_msg:
            await ctx.send(error_msg)
            return
        
        player: lava_lyra.Player = ctx.guild.voice_client
        if not player:
            await ctx.send(t("music.err.no_paused_player"))
            return
        
        if not player.is_paused:
            await ctx.send(t("music.err.not_paused"))
            return
        
        try:
            await player.set_pause(False)
            await ctx.send(t("music.msg.resumed"))
        except Exception as e:
            await ctx.send(t("music.err.resume_failed", error=str(e)))
    
    @commands.command(name="stop", aliases=["停止", "leave", "離開"])  # i18n: skip (input syntax)
    @commands.guild_only()
    async def text_stop(self, ctx: commands.Context):
        """停止播放並斷開連接"""
        error_msg = self._check_voice_channel(ctx.author, ctx.guild)
        if error_msg:
            await ctx.send(error_msg)
            return
        
        player: lava_lyra.Player = ctx.guild.voice_client
        if not player:
            await ctx.send(t("music.err.no_player"))
            return
        
        try:
            await player.stop()
            await player.destroy()
            await self._cleanup_player(ctx.guild.id)
            await ctx.send(t("music.msg.stopped"))
        except Exception as e:
            await ctx.send(t("music.err.stop_failed", error=str(e)))
    
    @commands.command(name="skip", aliases=["sk", "跳過", "下一首"])  # i18n: skip (input syntax)
    @commands.guild_only()
    async def text_skip(self, ctx: commands.Context):
        """跳過當前歌曲"""
        station = self._get_guild_radio_station(ctx.guild.id)
        if station:
            await ctx.send(t("music.err.radio_no_skip", station=station.display_name))
            return

        error_msg = self._check_voice_channel(ctx.author, ctx.guild)
        if error_msg:
            await ctx.send(error_msg)
            return
        
        player: lava_lyra.Player = ctx.guild.voice_client
        if not player or not player.is_playing:
            await ctx.send(t("music.err.no_player"))
            return
        
        try:
            current_track = player.current
            await player.stop()

            embed = discord.Embed(
                title=t("music.embed.track_skipped_title"),
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
                    log(f"Failed to play next track after skip: {e}", level=logging.ERROR, module_name="Music", guild=ctx.guild)
                    await ctx.send(t("music.msg.next_track_failed_warning", error=str(e)))
        except Exception as e:
            await ctx.send(t("music.err.skip_failed", error=str(e)))

    @commands.command(name="queue", aliases=["qu", "隊列"])  # i18n: skip (input syntax)
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
            await ctx.send(t("music.err.no_player"))
            return
        
        if not player.current and queue.is_empty:
            await ctx.send(t("music.err.queue_empty"))
            return
        
        embed = discord.Embed(title=t("music.embed.queue_title"), color=0x3498db)
        
        if player.current:
            embed.description = t("music.embed.queue_now_playing_line", title=player.current.title, uri=player.current.uri)
        
        if not queue.is_empty:
            queue_list = []
            total_duration = 0
            
            for i, track in enumerate(queue, 1):
                if i <= 10:
                    queue_list.append(f"{i}. [{track.title}]({track.uri})")
                total_duration += track.length
            
            if queue_list:
                embed.add_field(
                    name=t("music.field.upcoming_tracks", count=len(queue)),
                    value="\n".join(queue_list),
                    inline=False
                )
            
            if len(queue) > 10:
                embed.add_field(name=t("music.field.more_tracks"), value=t("music.value.more_tracks_count", count=len(queue) - 10), inline=False)
            
            embed.add_field(
                name=t("music.field.queue_total_duration"),
                value=self._format_duration(total_duration),
                inline=True
            )
        
        embed.set_footer(text=t("music.footer.queue_count", count=len(queue)))
        await ctx.send(embed=embed)
    
    @commands.command(name="loop", aliases=["lp", "循環"])  # i18n: skip (input syntax)
    @commands.guild_only()
    async def text_loop(self, ctx: commands.Context, mode: Optional[str] = None):
        """設定循環播放模式 (off/track/queue)"""
        station = self._get_guild_radio_station(ctx.guild.id)
        if station:
            await ctx.send(t("music.err.radio_no_loop", station=station.display_name))
            return

        error_msg = self._check_voice_channel(ctx.author, ctx.guild)
        if error_msg:
            await ctx.send(error_msg)
            return

        player: lava_lyra.Player = ctx.guild.voice_client
        if not player:
            await ctx.send(t("music.err.no_player"))
            return

        guild_id = ctx.guild.id
        current_mode = loop_modes.get(guild_id, LoopMode.OFF)

        # i18n: skip-start (input syntax)
        mode_map = {
            "off": LoopMode.OFF, "關閉": LoopMode.OFF, "0": LoopMode.OFF,
            "track": LoopMode.TRACK, "single": LoopMode.TRACK, "單曲": LoopMode.TRACK, "1": LoopMode.TRACK,
            "queue": LoopMode.QUEUE, "all": LoopMode.QUEUE, "隊列": LoopMode.QUEUE, "全部": LoopMode.QUEUE, "2": LoopMode.QUEUE,
        }
        # i18n: skip-end

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
                await ctx.send(t("music.err.invalid_loop_mode"))
                return

        loop_modes[guild_id] = new_mode

        mode_display = {LoopMode.OFF: t("music.value.loop_off"), LoopMode.TRACK: t("music.value.loop_track"), LoopMode.QUEUE: t("music.value.loop_queue")}
        await ctx.send(mode_display[new_mode])

    @commands.command(name="nowplaying", aliases=["np", "現正播放"])  # i18n: skip (input syntax)
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
            await ctx.send(t("music.err.no_player"))
            return
        
        track = player.current
        
        position = player.position
        length = track.length
        progress = int((position / length) * 20) if length > 0 else 0
        progress_bar = "█" * progress + "░" * (20 - progress)
        
        embed = discord.Embed(
            title=t("music.embed.current_track_title"),
            description=f"**[{track.title}]({track.uri})**",
            color=0x3498db
        )
        
        embed.set_thumbnail(url=track.thumbnail)
        
        if track.author:
            embed.add_field(name=t("music.field.artist"), value=track.author, inline=True)
        
        embed.add_field(
            name=t("music.field.progress"),
            value=f"`{progress_bar}`\n{self._format_duration(position)} / {self._format_duration(length)}",
            inline=False
        )
        
        embed.add_field(name=t("music.field.volume"), value=f"{player.volume}%", inline=True)

        loop_mode = loop_modes.get(ctx.guild.id, LoopMode.OFF)
        mode_display = {LoopMode.OFF: t("music.value.loop_off_short"), LoopMode.TRACK: t("music.value.loop_track"), LoopMode.QUEUE: t("music.value.loop_queue")}
        embed.add_field(name=t("music.field.loop_mode"), value=mode_display[loop_mode], inline=True)
        
        await ctx.send(embed=embed)
    
    @commands.command(name="volume", aliases=["vol", "音量"])  # i18n: skip (input syntax)
    @commands.guild_only()
    async def text_volume(self, ctx: commands.Context, level: int):
        """調整音量"""
        error_msg = self._check_voice_channel(ctx.author, ctx.guild)
        if error_msg:
            await ctx.send(error_msg)
            return
        
        if level < 0 or level > 100:
            await ctx.send(t("music.err.volume_range"))
            return
        
        player: lava_lyra.Player = ctx.guild.voice_client
        if not player:
            await ctx.send(t("music.err.no_player"))
            return
        
        try:
            await player.set_volume(level)
            await ctx.send(t("music.msg.volume_set", level=level))
        except Exception as e:
            await ctx.send(t("music.err.volume_failed", error=str(e)))
    
    @commands.command(name="shuffle", aliases=["sh", "隨機"])  # i18n: skip (input syntax)
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
            await ctx.send(t("music.err.no_player"))
            return
        
        if queue.is_empty:
            await ctx.send(t("music.err.queue_empty"))
            return
        
        try:
            tracks = list(queue)
            random.shuffle(tracks)
            queue.clear()
            for track in tracks:
                queue.add(track)
            await ctx.send(t("music.msg.queue_shuffled"))
        except Exception as e:
            await ctx.send(t("music.err.shuffle_failed", error=str(e)))
    
    @commands.command(name="recommend", aliases=["rec", "推薦"])  # i18n: skip (input syntax)
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
            await ctx.send(t("music.err.no_player"))
            return
        
        count = max(1, min(count, 10))
        
        try:
            results = await player.get_recommendations(track=player.current)
            
            if not results:
                await ctx.send(t("music.err.no_similar_recommendations"))
                return
            
            tracks = results.tracks if isinstance(results, lava_lyra.Playlist) else results
            tracks = tracks[:count]
            
            queue = get_queue(ctx.guild.id)
            for track in tracks:
                queue.add(track)
            
            track_list = "\n".join(
                f"{i}. [{tr.title}]({tr.uri})" for i, tr in enumerate(tracks, 1)
            )
            
            embed = discord.Embed(
                title=t("music.embed.recommend_added_title"),
                description=t("music.embed.recommend_desc", title=player.current.title, track_list=track_list),
                color=0x9b59b6
            )
            embed.set_thumbnail(url=player.current.thumbnail)
            embed.add_field(name=t("music.field.added_count"), value=t("music.value.track_count", count=len(tracks)), inline=True)
            embed.add_field(
                name=t("music.field.total_duration"),
                value=self._format_duration(sum(tr.length for tr in tracks)),
                inline=True
            )
            await ctx.send(embed=embed)

            if not player.is_playing:
                next_track = queue.get()
                if next_track:
                    try:
                        await player.play(next_track)
                    except Exception as e:
                        log(f"Failed to start playback after recommend: {e}", level=logging.ERROR, module_name="Music", guild=ctx.guild)
                        await ctx.send(t("music.msg.recommend_added_play_warning", error=str(e)))

        except Exception as e:
            log(f"Recommend-tracks error: {e}", level=logging.ERROR, module_name="Music", guild=ctx.guild)
            await ctx.send(t("music.err.recommend_failed", error=str(e)))


asyncio.run(bot.add_cog(Music(bot)))
