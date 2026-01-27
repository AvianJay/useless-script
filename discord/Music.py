import lava_lyra
import discord
from globalenv import bot, config
from discord.ext import commands
from discord import app_commands
from logger import log
import logging
import asyncio
from typing import Optional
from collections import deque


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


def get_queue(guild_id: int) -> MusicQueue:
    """獲取伺服器的隊列，如果不存在則創建"""
    if guild_id not in music_queues:
        music_queues[guild_id] = MusicQueue()
    return music_queues[guild_id]


class Music(commands.GroupCog, name=app_commands.locale_str("music")):
    def __init__(self, bot):
        super().__init__()
        self.bot = bot
        self.node = None
    

    @commands.Cog.listener()
    async def on_ready(self):
        """初始化 Lavalink 節點"""
        if self.node:
            return
        
        try:
            self.node = await lava_lyra.NodePool.create_node(
                bot=self.bot,
                host=config("lavalink_host"),
                port=config("lavalink_port"),
                password=config("lavalink_password"),
                identifier="MAIN",
                lyrics=False,
                search=True,
                fallback=True,
            )
            log(f"已創建 Lavalink 節點: {self.node}", module_name="Music")
        except Exception as e:
            log(f"無法連接到 Lavalink 伺服器: {e}", level=logging.ERROR, module_name="Music")
    
    @commands.Cog.listener()
    async def on_track_start(self, event: lava_lyra.TrackStartEvent):
        """當音樂開始播放時"""
        player = event.player
        if not player:
            return
        
        track = event.track
        embed = discord.Embed(
            title="🎵 開始播放",
            description=f"**[{track.title}]({track.uri})**",
            color=0x3498db
        )
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
    async def on_track_end(self, event: lava_lyra.TrackEndEvent):
        """當音樂結束播放時"""
        player = event.player
        if not player:
            return
        
        guild_id = player.guild.id
        queue = get_queue(guild_id)
        
        if event.reason == "FINISHED":
            # 播放下一首歌
            next_track = queue.get()
            if next_track:
                await player.play(next_track)
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
        
        player: lava_lyra.Player = interaction.guild.voice_client
        if not player:
            await interaction.followup.send("❌ 沒有正在播放的音樂", ephemeral=True)
            return
        
        if player.is_paused:
            await interaction.followup.send("❌ 音樂已經暫停", ephemeral=True)
            return
        
        try:
            await player.pause()
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
        
        player: lava_lyra.Player = interaction.guild.voice_client
        if not player:
            await interaction.followup.send("❌ 沒有暫停的音樂", ephemeral=True)
            return
        
        if not player.is_paused:
            await interaction.followup.send("❌ 音樂未暫停", ephemeral=True)
            return
        
        try:
            await player.resume()
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
        
        player: lava_lyra.Player = interaction.guild.voice_client
        if not player:
            await interaction.followup.send("❌ 沒有正在播放的音樂", ephemeral=True)
            return
        
        try:
            queue = get_queue(interaction.guild.id)
            queue.clear()
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
    
    def _format_duration(self, milliseconds: int) -> str:
        """將毫秒轉換為 MM:SS 格式"""
        seconds = milliseconds // 1000
        minutes = seconds // 60
        seconds = seconds % 60
        return f"{minutes}:{seconds:02d}"


asyncio.run(bot.add_cog(Music(bot)))