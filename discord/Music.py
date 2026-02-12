import lava_lyra
import discord
from globalenv import bot, config, set_user_data, get_user_data
from discord.ext import commands
from discord import app_commands
from logger import log
import logging
import asyncio
from typing import Optional
from collections import deque
import random
import aiohttp
import time
import urllib.parse
import requests


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
# 儲存 headless session token: user_id -> session_token
headless_sessions: dict[int, str] = {}

# 嘗試載入 Website 模組以註冊 OAuth 回呼路由
HAS_WEBSITE = False
try:
    from Website import app as flask_app
    from flask import request as flask_request, render_template
    HAS_WEBSITE = True
except Exception:
    pass

if HAS_WEBSITE:
    @flask_app.route('/music-presence')
    def music_presence_callback():
        """處理音樂動態狀態的 OAuth2 回呼"""
        code = flask_request.args.get('code')
        error = flask_request.args.get('error')

        if error:
            return render_template('MusicPresence.html', success=False, message=f"授權被拒絕: {error}", bot=bot, gtag=config("website_gtag", ""))

        if not code:
            return render_template('MusicPresence.html', success=False, message="缺少授權碼", bot=bot, gtag=config("website_gtag", ""))

        redirect_uri = config("website_url", "http://localhost:8080").rstrip("/") + "/music-presence"

        # 交換授權碼取得 token
        try:
            resp = requests.post('https://discord.com/api/oauth2/token', data={
                'client_id': str(bot.application.id),
                'client_secret': config("client_secret"),
                'grant_type': 'authorization_code',
                'code': code,
                'redirect_uri': redirect_uri,
            }, timeout=10)
            resp.raise_for_status()
            token_info = resp.json()
        except Exception as e:
            log(f"Music presence token exchange error: {e}", level=logging.ERROR, module_name="Music")
            return render_template('MusicPresence.html', success=False, message="Token 交換失敗，請重試", bot=bot, gtag=config("website_gtag", ""))

        access_token = token_info.get('access_token')
        refresh_token = token_info.get('refresh_token')
        expires_in = token_info.get('expires_in', 604800)

        if not access_token:
            return render_template('MusicPresence.html', success=False, message="無法取得 access token", bot=bot, gtag=config("website_gtag", ""))

        # 透過 identify scope 取得用戶 ID
        try:
            user_resp = requests.get('https://discord.com/api/users/@me',
                headers={'Authorization': f'Bearer {access_token}'}, timeout=10)
            user_resp.raise_for_status()
            user_id = int(user_resp.json()['id'])
        except Exception as e:
            log(f"Music presence user fetch error: {e}", level=logging.ERROR, module_name="Music")
            return render_template('MusicPresence.html', success=False, message="無法驗證用戶身份", bot=bot, gtag=config("website_gtag", ""))

        # 儲存 token
        set_user_data(0, user_id, "music_presence_token", {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": time.time() + expires_in
        })

        log(f"用戶 {user_id} 已授權音樂動態狀態", module_name="Music")
        return render_template('MusicPresence.html', success=True, message="授權成功！播放音樂時你的 Discord 狀態將自動更新。", bot=bot, gtag=config("website_gtag", ""))


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
    
    async def _auto_leave_after_timeout(self, guild_id: int, player: lava_lyra.Player):
        """5 分鐘後自動離開語音頻道"""
        try:
            await asyncio.sleep(300)  # 5 分鐘 = 300 秒
            
            # 再次確認頻道內沒有真人
            if player and player.channel:
                human_count = sum(1 for m in player.channel.members if not m.bot)
                if human_count == 0:
                    queue = get_queue(guild_id)
                    
                    embed = discord.Embed(
                        title="👋 自動離開",
                        description="語音頻道內已 5 分鐘無其他成員，機器人已離開",
                        color=0x95a5a6
                    )
                    try:
                        text_channel = text_channels.get(guild_id)
                        if text_channel:
                            await text_channel.send(embed=embed)
                    except Exception as e:
                        log(f"無法發送自動離開通知: {e}", level=logging.WARNING, module_name="Music", guild=player.guild)
                    
                    # 清除所有用戶的音樂動態狀態
                    await self._clear_all_presences(player)
                    # 清理並離開
                    try:
                        queue.clear()
                        await player.stop()
                        await player.disconnect()
                        music_queues.pop(guild_id, None)
                        text_channels.pop(guild_id, None)
                    except:
                        pass
        except asyncio.CancelledError:
            pass
        finally:
            leave_timers.pop(guild_id, None)
    
    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        """當語音狀態變化時，檢查是否需要啟動或取消自動離開計時器"""
        # 檢查機器人是否在語音頻道
        player: lava_lyra.Player = member.guild.voice_client
        if not player or not player.channel:
            return
        
        guild_id = member.guild.id
        
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
        
        # 更新已授權用戶的音樂動態狀態
        await self._update_all_presences(player, track)
    
    @commands.Cog.listener()
    async def on_lyra_track_end(self, player: lava_lyra.Player, track: lava_lyra.Track, reason: Optional[str]):
        """當音樂結束播放時"""
        if not player:
            return
        
        guild_id = player.guild.id
        queue = get_queue(guild_id)
        
        # 檢查結束原因，可能是字串或枚舉
        reason_str = str(reason).upper() if reason else ""
        log(f"Track ended with reason: {reason_str}", module_name="Music")
        
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
        
        # 播放下一首歌 (FINISHED 的情況)
        next_track = queue.get()
        if next_track:
            try:
                await player.play(next_track)
            except Exception as e:
                log(f"播放下一首失敗: {e}", level=logging.ERROR, module_name="Music")
        else:
            embed = discord.Embed(
                title="🎵 播放隊列已清空",
                description="沒有更多的歌曲要播放，即將離開語音頻道",
                color=0x95a5a6
            )
            try:
                text_channel = text_channels.get(guild_id)
                if text_channel:
                    await text_channel.send(embed=embed)
            except:
                pass
            
            # 清除所有用戶的音樂動態狀態
            await self._clear_all_presences(player)
            # 離開語音頻道並清理資料
            try:
                await player.disconnect()
                music_queues.pop(guild_id, None)
                text_channels.pop(guild_id, None)
            except:
                pass
    
    @app_commands.command(name=app_commands.locale_str("play"), description="播放音樂")
    @app_commands.describe(query="歌曲名稱或 URL")
    @app_commands.guild_only()
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    @app_commands.checks.bot_has_permissions(connect=True, speak=True)
    async def play(self, interaction: discord.Interaction, query: str):
        """播放音樂"""
        await interaction.response.defer()
        
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
                    await player.play(next_track)
        
        except Exception as e:
            log(f"播放出錯: {e}", level=logging.ERROR, module_name="Music")
            await interaction.followup.send(f"❌ 播放出錯: {e}", ephemeral=True)
    
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
            queue = get_queue(interaction.guild.id)
            queue.clear()
            await self._clear_all_presences(player)
            await player.stop()
            await player.disconnect()
            # 清理資料
            music_queues.pop(interaction.guild.id, None)
            text_channels.pop(interaction.guild.id, None)
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
                await player.play(next_track)
        except Exception as e:
            await interaction.followup.send(f"❌ 跳過出錯: {e}", ephemeral=True)
    
    @app_commands.command(name=app_commands.locale_str("queue"), description="查看播放隊列")
    @app_commands.guild_only()
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def queue(self, interaction: discord.Interaction):
        """查看播放隊列"""
        await interaction.response.defer()
        
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
    
    @app_commands.command(name=app_commands.locale_str("now-playing"), description="查看當前播放的歌曲")
    @app_commands.guild_only()
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def now_playing(self, interaction: discord.Interaction):
        """查看當前播放的歌曲"""
        await interaction.response.defer()
        
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
                    await player.play(next_track)
        
        except Exception as e:
            log(f"推薦歌曲出錯: {e}", level=logging.ERROR, module_name="Music")
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
                players = len(node.players)
                status += f"\n有 {players} 個伺服器正在使用此節點"
            embed.add_field(name=name, value=status, inline=False)
        await interaction.followup.send(embed=embed)
    
    @app_commands.command(name=app_commands.locale_str("presence"), description="管理音樂播放動態狀態")
    @app_commands.describe(action="選擇操作")
    @app_commands.choices(action=[
        app_commands.Choice(name="啟用 (授權)", value="enable"),
        app_commands.Choice(name="停用 (取消授權)", value="disable"),
        app_commands.Choice(name="查看狀態", value="status"),
    ])
    @app_commands.guild_only()
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def presence(self, interaction: discord.Interaction, action: str = "status"):
        """管理音樂播放動態狀態 (授權後播放音樂時自動更新 Discord 狀態)"""
        await interaction.response.defer(ephemeral=True)
        
        user_id = interaction.user.id
        
        if action == "enable":
            if not HAS_WEBSITE:
                await interaction.followup.send("❌ 網站模組未啟用，無法使用此功能", ephemeral=True)
                return
            
            redirect_uri = config("website_url", "http://localhost:8080").rstrip("/") + "/music-presence"
            oauth_url = (
                f"https://discord.com/oauth2/authorize"
                f"?client_id={self.bot.application.id}"
                f"&response_type=code"
                f"&redirect_uri={urllib.parse.quote(redirect_uri, safe='')}"
                f"&scope=activities.write%20identify"
            )
            
            embed = discord.Embed(
                title="🎵 音樂動態狀態",
                description=(
                    "點擊下方按鈕授權後，當你在語音頻道收聽音樂時，\n"
                    "你的 Discord 狀態會自動顯示正在收聽的歌曲。\n\n"
                    "*需要授權 `activities.write` 和 `identify` 權限*"
                ),
                color=0x5865F2
            )
            
            view = discord.ui.View()
            view.add_item(discord.ui.Button(label="授權", url=oauth_url, style=discord.ButtonStyle.link))
            
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        
        elif action == "disable":
            await self._clear_presence(user_id)
            set_user_data(0, user_id, "music_presence_token", None)
            await interaction.followup.send("✅ 音樂動態狀態已停用，已清除授權資訊", ephemeral=True)
        
        elif action == "status":
            token_data = get_user_data(0, user_id, "music_presence_token")
            if token_data and token_data.get("access_token"):
                is_active = user_id in headless_sessions
                expired = token_data.get("expires_at", 0) < time.time()
                
                status_text = "✅ 已授權"
                if expired:
                    status_text += " (Token 已過期，將在下次播放時自動刷新)"
                if is_active:
                    status_text += "\n🎵 動態狀態啟用中"
                
                await interaction.followup.send(status_text, ephemeral=True)
            else:
                await interaction.followup.send("❌ 尚未授權，請使用 `啟用` 選項進行授權", ephemeral=True)
    
    def _format_duration(self, milliseconds: int) -> str:
        """將毫秒轉換為 MM:SS 格式"""
        seconds = milliseconds // 1000
        minutes = seconds // 60
        seconds = seconds % 60
        return f"{minutes}:{seconds:02d}"
    
    # ========== 音樂動態狀態 (Headless Sessions) ==========
    
    async def _refresh_presence_token(self, user_id: int, token_data: dict) -> bool:
        """刷新過期的 OAuth token"""
        refresh_token = token_data.get("refresh_token")
        if not refresh_token:
            set_user_data(0, user_id, "music_presence_token", None)
            return False
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    'https://discord.com/api/oauth2/token',
                    data={
                        'client_id': str(self.bot.application.id),
                        'client_secret': config("client_secret"),
                        'grant_type': 'refresh_token',
                        'refresh_token': refresh_token,
                    },
                    headers={'Content-Type': 'application/x-www-form-urlencoded'}
                ) as resp:
                    if resp.status == 200:
                        new_token = await resp.json()
                        set_user_data(0, user_id, "music_presence_token", {
                            "access_token": new_token["access_token"],
                            "refresh_token": new_token.get("refresh_token", refresh_token),
                            "expires_at": time.time() + new_token.get("expires_in", 604800)
                        })
                        return True
                    else:
                        log(f"Token refresh failed ({resp.status}) for user {user_id}", level=logging.WARNING, module_name="Music")
                        set_user_data(0, user_id, "music_presence_token", None)
                        return False
        except Exception as e:
            log(f"Token refresh error for user {user_id}: {e}", level=logging.WARNING, module_name="Music")
            return False
    
    async def _update_presence(self, user_id: int, track: lava_lyra.Track):
        """為用戶建立或更新 headless session"""
        token_data = get_user_data(0, user_id, "music_presence_token")
        if not token_data or not token_data.get("access_token"):
            return
        
        # 檢查 token 是否過期，嘗試刷新
        if token_data.get("expires_at", 0) < time.time():
            if not await self._refresh_presence_token(user_id, token_data):
                return
            token_data = get_user_data(0, user_id, "music_presence_token")
        
        # 刪除舊的 session
        await self._clear_presence(user_id)
        
        # 建立新的 headless session
        payload = {
            "activities": [{
                "application_id": str(self.bot.application.id),
                "name": track.title or "Music",
                "platform": "desktop",
                "type": 2,  # LISTENING
                "details": track.title,
                "state": track.author or "",
                "assets": {
                    "large_image": track.thumbnail or ""
                },
                "timestamps": {
                    "start": int(time.time() * 1000)
                }
            }]
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    'https://discord.com/api/v10/users/@me/headless-sessions',
                    headers={
                        'Authorization': f'Bearer {token_data["access_token"]}',
                        'User-Agent': 'DiscordBot'
                    },
                    json=payload
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        headless_sessions[user_id] = data.get("token")
                    elif resp.status == 401:
                        # Token 無效，嘗試刷新後重試一次
                        if await self._refresh_presence_token(user_id, token_data):
                            new_token_data = get_user_data(0, user_id, "music_presence_token")
                            async with session.post(
                                'https://discord.com/api/v10/users/@me/headless-sessions',
                                headers={
                                    'Authorization': f'Bearer {new_token_data["access_token"]}',
                                    'User-Agent': 'DiscordBot'
                                },
                                json=payload
                            ) as retry_resp:
                                if retry_resp.status == 200:
                                    data = await retry_resp.json()
                                    headless_sessions[user_id] = data.get("token")
                                else:
                                    log(f"Headless session retry failed ({retry_resp.status})", level=logging.WARNING, module_name="Music")
                        else:
                            log(f"Token invalid and refresh failed for user {user_id}", level=logging.WARNING, module_name="Music")
                    else:
                        body = await resp.text()
                        log(f"Headless session creation failed ({resp.status}): {body}", level=logging.WARNING, module_name="Music")
        except Exception as e:
            log(f"Headless session error for user {user_id}: {e}", level=logging.WARNING, module_name="Music")
    
    async def _clear_presence(self, user_id: int):
        """刪除用戶的 headless session"""
        session_token = headless_sessions.pop(user_id, None)
        if not session_token:
            return
        
        token_data = get_user_data(0, user_id, "music_presence_token")
        if not token_data or not token_data.get("access_token"):
            return
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    'https://discord.com/api/v10/users/@me/headless-sessions/delete',
                    headers={
                        'Authorization': f'Bearer {token_data["access_token"]}',
                        'User-Agent': 'DiscordBot'
                    },
                    json={"token": session_token}
                ) as resp:
                    if resp.status not in (204, 200):
                        log(f"Headless session deletion failed ({resp.status})", level=logging.WARNING, module_name="Music")
        except Exception as e:
            log(f"Headless session deletion error for user {user_id}: {e}", level=logging.WARNING, module_name="Music")
    
    async def _update_all_presences(self, player: lava_lyra.Player, track: lava_lyra.Track):
        """更新語音頻道內所有已授權用戶的動態狀態"""
        if not player or not player.channel:
            return
        
        tasks = []
        for member in player.channel.members:
            if member.bot:
                continue
            # 只為有 token 的用戶建立 session
            token_data = get_user_data(0, member.id, "music_presence_token")
            if token_data and token_data.get("access_token"):
                tasks.append(self._update_presence(member.id, track))
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    
    async def _clear_all_presences(self, player: lava_lyra.Player):
        """清除所有相關用戶的動態狀態"""
        if player and player.channel:
            user_ids = [m.id for m in player.channel.members if not m.bot and m.id in headless_sessions]
        else:
            user_ids = list(headless_sessions.keys())
        
        if user_ids:
            tasks = [self._clear_presence(uid) for uid in user_ids]
            await asyncio.gather(*tasks, return_exceptions=True)
    
    # ========== 文字指令 ==========
    
    @commands.command(name="play", aliases=["p", "播放"])
    @commands.guild_only()
    async def text_play(self, ctx: commands.Context, *, query: Optional[str] = None):
        """播放音樂，若無參數則繼續播放"""
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
                    await player.play(next_track)
        
        except Exception as e:
            log(f"播放出錯: {e}", level=logging.ERROR, module_name="Music")
            await ctx.send(f"❌ 播放出錯: {e}")
    
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
            queue = get_queue(ctx.guild.id)
            queue.clear()
            await self._clear_all_presences(player)
            await player.stop()
            await player.disconnect()
            music_queues.pop(ctx.guild.id, None)
            text_channels.pop(ctx.guild.id, None)
            await ctx.send("⏹️ 已停止播放並斷開連接")
        except Exception as e:
            await ctx.send(f"❌ 停止出錯: {e}")
    
    @commands.command(name="skip", aliases=["sk", "跳過", "下一首"])
    @commands.guild_only()
    async def text_skip(self, ctx: commands.Context):
        """跳過當前歌曲"""
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
                await player.play(next_track)
        except Exception as e:
            await ctx.send(f"❌ 跳過出錯: {e}")
    
    @commands.command(name="queue", aliases=["qu", "隊列"])
    @commands.guild_only()
    async def text_queue(self, ctx: commands.Context):
        """查看播放隊列"""
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
    
    @commands.command(name="nowplaying", aliases=["np", "現正播放"])
    @commands.guild_only()
    async def text_now_playing(self, ctx: commands.Context):
        """查看當前播放的歌曲"""
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
                    await player.play(next_track)
        
        except Exception as e:
            log(f"推薦歌曲出錯: {e}", level=logging.ERROR, module_name="Music")
            await ctx.send(f"❌ 推薦歌曲出錯: {e}")
    
    @commands.command(name="presence", aliases=["rp", "動態狀態"])
    @commands.guild_only()
    async def text_presence(self, ctx: commands.Context, action: str = "status"):
        """管理音樂播放動態狀態 (enable/disable/status)"""
        user_id = ctx.author.id
        
        if action in ("enable", "啟用"):
            if not HAS_WEBSITE:
                await ctx.send("❌ 網站模組未啟用，無法使用此功能")
                return
            
            redirect_uri = config("website_url", "http://localhost:8080").rstrip("/") + "/music-presence"
            oauth_url = (
                f"https://discord.com/oauth2/authorize"
                f"?client_id={self.bot.application.id}"
                f"&response_type=code"
                f"&redirect_uri={urllib.parse.quote(redirect_uri, safe='')}"
                f"&scope=activities.write%20identify"
            )
            
            embed = discord.Embed(
                title="🎵 音樂動態狀態",
                description=(
                    "點擊下方按鈕授權後，當你在語音頻道收聽音樂時，\n"
                    "你的 Discord 狀態會自動顯示正在收聯的歌曲。\n\n"
                    "*需要授權 `activities.write` 和 `identify` 權限*"
                ),
                color=0x5865F2
            )
            
            view = discord.ui.View()
            view.add_item(discord.ui.Button(label="授權", url=oauth_url, style=discord.ButtonStyle.link))
            
            await ctx.send(embed=embed, view=view)
        
        elif action in ("disable", "停用"):
            await self._clear_presence(user_id)
            set_user_data(0, user_id, "music_presence_token", None)
            await ctx.send("✅ 音樂動態狀態已停用，已清除授權資訊")
        
        else:  # status
            token_data = get_user_data(0, user_id, "music_presence_token")
            if token_data and token_data.get("access_token"):
                is_active = user_id in headless_sessions
                expired = token_data.get("expires_at", 0) < time.time()
                
                status_text = "✅ 已授權"
                if expired:
                    status_text += " (Token 已過期，將在下次播放時自動刷新)"
                if is_active:
                    status_text += "\n🎵 動態狀態啟用中"
                
                await ctx.send(status_text)
            else:
                await ctx.send("❌ 尚未授權，請使用 `presence enable` 進行授權")


asyncio.run(bot.add_cog(Music(bot)))