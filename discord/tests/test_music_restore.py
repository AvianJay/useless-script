import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


DISCORD_DIR = Path(__file__).resolve().parents[1]


def _load_music_module():
    fake_globalenv = types.ModuleType("globalenv")
    fake_globalenv.bot = MagicMock()
    fake_globalenv.bot.add_cog = AsyncMock()
    fake_globalenv.config = MagicMock(return_value=[])
    fake_globalenv.on_close_tasks = set()
    fake_globalenv.get_server_config = MagicMock()
    fake_globalenv.set_server_config = MagicMock(return_value=True)
    fake_globalenv.get_all_server_config_key = MagicMock(return_value={})

    fake_logger = types.ModuleType("logger")
    fake_logger.log = MagicMock()

    spec = importlib.util.spec_from_file_location("music_restore_test_module", DISCORD_DIR / "Music.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    with patch.dict(sys.modules, {"globalenv": fake_globalenv, "logger": fake_logger}):
        spec.loader.exec_module(module)
    return module


music = _load_music_module()


class FakePermissions:
    def __init__(
        self,
        *,
        view_channel=True,
        connect=True,
        speak=True,
        move_members=False,
        mute_members=True,
        send_messages=True,
        send_messages_in_threads=True,
    ):
        self.view_channel = view_channel
        self.connect = connect
        self.speak = speak
        self.move_members = move_members
        self.mute_members = mute_members
        self.send_messages = send_messages
        self.send_messages_in_threads = send_messages_in_threads


class FakeTextChannel:
    def __init__(self, channel_id, guild, permissions=None):
        self.id = channel_id
        self.guild = guild
        self._permissions = permissions or FakePermissions()
        self.send = AsyncMock()

    def permissions_for(self, member):
        return self._permissions


class FakeVoiceChannel:
    def __init__(self, channel_id, guild, player=None, permissions=None, members=None, user_limit=0):
        self.id = channel_id
        self.guild = guild
        self._permissions = permissions or FakePermissions()
        self.members = list(members or [])
        self.user_limit = user_limit
        self.connect = AsyncMock(return_value=player)

    def permissions_for(self, member):
        return self._permissions


class FakeStageChannel(FakeVoiceChannel):
    pass


def make_track(name, *, length=120_000, seekable=True):
    return SimpleNamespace(
        track_id=f"encoded-{name}",
        uri=f"https://example.com/{name}",
        title=name,
        length=length,
        is_seekable=seekable,
    )


def make_player(channel, *, current=None, position=0, volume=80, paused=False):
    return SimpleNamespace(
        channel=channel,
        current=current,
        position=position,
        volume=volume,
        is_paused=paused,
        is_playing=bool(current),
        play=AsyncMock(),
        stop=AsyncMock(),
        destroy=AsyncMock(),
        set_volume=AsyncMock(),
        set_pause=AsyncMock(),
    )


class MusicRestoreTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        music.music_queues.clear()
        music.text_channels.clear()
        music.loop_modes.clear()
        music.radio_modes.clear()
        music.leave_timers.clear()
        self.bot = MagicMock()
        self.bot.get_channel.return_value = None
        self.bot.fetch_channel = AsyncMock(side_effect=music.discord.NotFound(MagicMock(), "missing"))
        self.cog = music.Music(self.bot)

    async def asyncTearDown(self):
        for task in list(music.leave_timers.values()):
            task.cancel()
        if music.leave_timers:
            await asyncio.gather(*music.leave_timers.values(), return_exceptions=True)
        music.leave_timers.clear()

    def test_build_track_snapshot_records_full_state_without_text_channel(self):
        guild = SimpleNamespace(id=100)
        voice = SimpleNamespace(id=200)
        current = make_track("current")
        queued = make_track("queued")
        player = make_player(voice, current=current, position=12_345, volume=37, paused=True)
        music.get_queue(guild.id).add(queued)
        music.loop_modes[guild.id] = music.LoopMode.QUEUE

        snapshot = self.cog._build_music_snapshot(guild, player)

        self.assertEqual(snapshot["version"], 2)
        self.assertEqual(snapshot["mode"], "track")
        self.assertEqual(snapshot["voice_channel_id"], 200)
        self.assertIsNone(snapshot["text_channel_id"])
        self.assertEqual(snapshot["current"]["encoded"], "encoded-current")
        self.assertEqual(snapshot["current"]["position_ms"], 12_345)
        self.assertEqual(snapshot["queue"][0]["encoded"], "encoded-queued")
        self.assertEqual(snapshot["volume"], 37)
        self.assertTrue(snapshot["paused"])
        self.assertEqual(snapshot["loop_mode"], music.LoopMode.QUEUE.value)
        self.assertIn("saved_at", snapshot)

    def test_build_radio_snapshot_records_station_and_channels(self):
        guild = SimpleNamespace(id=101)
        voice = SimpleNamespace(id=201)
        text = SimpleNamespace(id=301)
        player = make_player(voice, current=make_track("stream"), volume=55)
        music.text_channels[guild.id] = text
        music.radio_modes[guild.id] = "listenmoe"

        snapshot = self.cog._build_music_snapshot(guild, player)

        self.assertEqual(snapshot["mode"], "radio")
        self.assertEqual(snapshot["radio_station"], "listenmoe")
        self.assertEqual(snapshot["text_channel_id"], 301)
        self.assertIsNone(snapshot["current"])
        self.assertEqual(snapshot["queue"], [])

    async def test_encoded_track_falls_back_to_uri(self):
        fallback_track = make_track("fallback")
        node = SimpleNamespace(
            build_track=AsyncMock(side_effect=RuntimeError("decode failed")),
            get_tracks=AsyncMock(return_value=[fallback_track]),
        )

        loaded = await self.cog._load_saved_track(
            node,
            {"encoded": "bad-encoded", "uri": "https://example.com/fallback"},
        )

        self.assertIs(loaded, fallback_track)
        node.build_track.assert_awaited_once_with("bad-encoded")
        node.get_tracks.assert_awaited_once_with("https://example.com/fallback")

    def test_voice_permission_checks_missing_permission_capacity_and_stage(self):
        bot_member = SimpleNamespace()
        guild = SimpleNamespace(me=bot_member)
        missing_speak = FakeVoiceChannel(1, guild, permissions=FakePermissions(speak=False))
        full = FakeVoiceChannel(2, guild, members=[object()], user_limit=1)
        stage = FakeStageChannel(3, guild, permissions=FakePermissions(mute_members=False))

        with patch.object(music.discord, "VoiceChannel", FakeVoiceChannel), patch.object(music.discord, "StageChannel", FakeStageChannel):
            self.assertIn("speak", self.cog._voice_restore_error(guild, missing_speak))
            self.assertIn("已滿", self.cog._voice_restore_error(guild, full))
            self.assertIn("mute_members", self.cog._voice_restore_error(guild, stage))

    async def test_auto_restore_applies_seek_queue_volume_pause_and_loop(self):
        bot_member = SimpleNamespace(edit=AsyncMock())
        guild = SimpleNamespace(id=500, me=bot_member, voice_client=None)
        human = SimpleNamespace(bot=False)
        player = make_player(None, current=None, paused=False)
        voice = FakeVoiceChannel(600, guild, player=player, members=[human])
        player.channel = voice
        text = FakeTextChannel(700, guild)
        guild.get_channel = MagicMock(side_effect=lambda channel_id: voice if channel_id == 600 else None)
        self.bot.get_channel.return_value = text

        current = make_track("current")
        queued = make_track("queued")
        node = SimpleNamespace(
            build_track=AsyncMock(side_effect=[current, queued]),
            get_tracks=AsyncMock(),
        )
        saved = {
            "version": 2,
            "mode": "track",
            "voice_channel_id": 600,
            "text_channel_id": 700,
            "current": {"encoded": "one", "uri": current.uri, "position_ms": 4321},
            "queue": [{"encoded": "two", "uri": queued.uri}],
            "volume": 42,
            "paused": True,
            "loop_mode": music.LoopMode.QUEUE.value,
            "radio_station": None,
        }

        with (
            patch.object(music.discord, "VoiceChannel", FakeVoiceChannel),
            patch.object(music.discord, "StageChannel", FakeStageChannel),
            patch.object(music.lava_lyra.NodePool, "get_node", return_value=node),
            patch.object(music, "set_server_config", return_value=True) as save_config,
            patch.object(self.cog, "_start_empty_channel_timer") as start_timer,
        ):
            restored = await self.cog._restore_saved_session(guild, saved)

        self.assertTrue(restored)
        voice.connect.assert_awaited_once_with(cls=music.lava_lyra.Player)
        player.set_volume.assert_awaited_once_with(42)
        player.play.assert_awaited_once_with(current, start=4321)
        player.set_pause.assert_awaited_once_with(True)
        self.assertEqual(list(music.get_queue(guild.id)), [queued])
        self.assertEqual(music.loop_modes[guild.id], music.LoopMode.QUEUE)
        self.assertIs(music.text_channels[guild.id], text)
        save_config.assert_called_once_with(guild.id, music.MUSIC_SAVED_STATE_KEY, None)
        start_timer.assert_called_once_with(guild, player)

    async def test_auto_restore_load_failure_keeps_snapshot_and_does_not_connect(self):
        bot_member = SimpleNamespace(edit=AsyncMock())
        guild = SimpleNamespace(id=501, me=bot_member, voice_client=None)
        voice = FakeVoiceChannel(601, guild, permissions=FakePermissions())
        text = FakeTextChannel(701, guild)
        guild.get_channel = MagicMock(return_value=voice)
        self.bot.get_channel.return_value = text
        node = SimpleNamespace(
            build_track=AsyncMock(side_effect=RuntimeError("decode failed")),
            get_tracks=AsyncMock(side_effect=RuntimeError("load failed")),
        )
        saved = {
            "version": 2,
            "mode": "track",
            "voice_channel_id": 601,
            "text_channel_id": 701,
            "current": {"encoded": "broken", "uri": "https://example.com/broken", "position_ms": 0},
            "queue": [],
        }

        with (
            patch.object(music.discord, "VoiceChannel", FakeVoiceChannel),
            patch.object(music.discord, "StageChannel", FakeStageChannel),
            patch.object(music.lava_lyra.NodePool, "get_node", return_value=node),
            patch.object(music, "set_server_config", return_value=True) as save_config,
        ):
            restored = await self.cog._restore_saved_session(guild, saved)

        self.assertFalse(restored)
        voice.connect.assert_not_awaited()
        save_config.assert_not_called()
        self.assertTrue(any("已保留" in call.args[0] for call in text.send.await_args_list))

    async def test_auto_restore_play_failure_disconnects_and_keeps_snapshot(self):
        bot_member = SimpleNamespace(edit=AsyncMock())
        guild = SimpleNamespace(id=506, me=bot_member, voice_client=None)
        player = make_player(None, current=None)
        player.play.side_effect = RuntimeError("play failed")
        voice = FakeVoiceChannel(606, guild, player=player, members=[SimpleNamespace(bot=False)])
        player.channel = voice
        guild.get_channel = MagicMock(return_value=voice)
        track = make_track("play-failure")
        node = SimpleNamespace(build_track=AsyncMock(return_value=track), get_tracks=AsyncMock())
        saved = {
            "version": 2,
            "mode": "track",
            "voice_channel_id": 606,
            "text_channel_id": None,
            "current": {"encoded": "play-failure", "uri": track.uri, "position_ms": 10},
            "queue": [],
            "volume": 100,
        }

        with (
            patch.object(music.discord, "VoiceChannel", FakeVoiceChannel),
            patch.object(music.discord, "StageChannel", FakeStageChannel),
            patch.object(music.lava_lyra.NodePool, "get_node", return_value=node),
            patch.object(music, "set_server_config", return_value=True) as save_config,
        ):
            restored = await self.cog._restore_saved_session(guild, saved)

        self.assertFalse(restored)
        save_config.assert_not_called()
        player.stop.assert_awaited_once()
        player.destroy.assert_awaited_once()
        self.assertNotIn(guild.id, music.music_queues)

    async def test_auto_restore_missing_permission_keeps_snapshot(self):
        guild = SimpleNamespace(id=502, me=SimpleNamespace(), voice_client=None)
        voice = FakeVoiceChannel(602, guild, permissions=FakePermissions(connect=False))
        guild.get_channel = MagicMock(return_value=voice)
        saved = {
            "version": 2,
            "mode": "radio",
            "voice_channel_id": 602,
            "text_channel_id": None,
            "radio_station": "listenmoe",
        }

        with (
            patch.object(music.discord, "VoiceChannel", FakeVoiceChannel),
            patch.object(music.discord, "StageChannel", FakeStageChannel),
            patch.object(music, "set_server_config", return_value=True) as save_config,
        ):
            restored = await self.cog._restore_saved_session(guild, saved)

        self.assertFalse(restored)
        voice.connect.assert_not_awaited()
        save_config.assert_not_called()

    async def test_auto_restore_radio_uses_existing_activation_path(self):
        bot_member = SimpleNamespace(edit=AsyncMock())
        guild = SimpleNamespace(id=503, me=bot_member, voice_client=None)
        player = make_player(None, current=None)
        voice = FakeVoiceChannel(603, guild, player=player, members=[SimpleNamespace(bot=False)])
        player.channel = voice
        text = FakeTextChannel(703, guild)
        guild.get_channel = MagicMock(return_value=voice)
        self.bot.get_channel.return_value = text
        saved = {
            "version": 2,
            "mode": "radio",
            "voice_channel_id": 603,
            "text_channel_id": 703,
            "volume": 65,
            "paused": True,
            "radio_station": "r-a-dio",
        }

        with (
            patch.object(music.discord, "VoiceChannel", FakeVoiceChannel),
            patch.object(music.discord, "StageChannel", FakeStageChannel),
            patch.object(self.cog, "_activate_radio_mode", AsyncMock()) as activate,
            patch.object(self.cog, "_start_empty_channel_timer"),
            patch.object(music, "set_server_config", return_value=True) as save_config,
        ):
            restored = await self.cog._restore_saved_session(guild, saved)

        self.assertTrue(restored)
        player.set_volume.assert_awaited_once_with(65)
        activate.assert_awaited_once_with(guild, text, player, music.RADIO_STATIONS["r-a-dio"])
        player.set_pause.assert_awaited_once_with(True)
        save_config.assert_called_once_with(guild.id, music.MUSIC_SAVED_STATE_KEY, None)

    async def test_shutdown_saves_guild_without_text_mapping_before_destroy(self):
        guild = SimpleNamespace(id=504)
        voice = SimpleNamespace(id=604)
        player = make_player(voice, current=make_track("shutdown"), position=999)
        guild.voice_client = player
        self.bot.guilds = [guild]

        with patch.object(music, "set_server_config", return_value=True) as save_config:
            await self.cog.music_quit_task()
            await self.cog.music_quit_task()

        snapshot = save_config.call_args.args[2]
        self.assertEqual(snapshot["voice_channel_id"], 604)
        self.assertEqual(snapshot["current"]["position_ms"], 999)
        player.stop.assert_awaited_once()
        player.destroy.assert_awaited_once()

    async def test_empty_restored_channel_starts_leave_timer(self):
        guild = SimpleNamespace(id=507)
        voice = SimpleNamespace(members=[])
        player = SimpleNamespace(channel=voice)
        wait_forever = asyncio.Event()

        async def fake_timeout(guild_id, active_player):
            await wait_forever.wait()

        with patch.object(self.cog, "_auto_leave_after_timeout", side_effect=fake_timeout):
            self.cog._start_empty_channel_timer(guild, player)
            task = music.leave_timers[guild.id]
            await asyncio.sleep(0)

        self.assertFalse(task.done())
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def test_restore_scheduler_runs_only_once(self):
        self.cog._restore_saved_sessions = AsyncMock()

        self.cog._schedule_saved_sessions_restore()
        task = self.cog._restore_task
        self.cog._schedule_saved_sessions_restore()
        await task

        self.cog._restore_saved_sessions.assert_awaited_once()

    async def test_manual_restore_accepts_legacy_uri_snapshot(self):
        bot_member = SimpleNamespace(edit=AsyncMock())
        guild = SimpleNamespace(id=505, me=bot_member, voice_client=None)
        player = make_player(None, current=None)
        voice = FakeVoiceChannel(605, guild, player=player, members=[SimpleNamespace(bot=False)])
        player.channel = voice
        guild.get_channel = MagicMock(return_value=voice)
        interaction = SimpleNamespace(
            guild=guild,
            user=SimpleNamespace(voice=SimpleNamespace(channel=voice)),
            channel=SimpleNamespace(id=705),
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )
        first = make_track("legacy-one")
        second = make_track("legacy-two")
        node = SimpleNamespace(
            build_track=AsyncMock(),
            get_tracks=AsyncMock(side_effect=[[first], [second]]),
        )

        with (
            patch.object(music.discord, "VoiceChannel", FakeVoiceChannel),
            patch.object(music.discord, "StageChannel", FakeStageChannel),
            patch.object(self.cog, "_ensure_not_radio_mode", AsyncMock(return_value=True)),
            patch.object(self.cog, "_start_empty_channel_timer"),
            patch.object(music.lava_lyra.NodePool, "get_node", return_value=node),
            patch.object(music, "get_server_config", return_value={"uris": [first.uri, second.uri]}),
            patch.object(music, "set_server_config", return_value=True) as save_config,
        ):
            await music.Music.restore_queue.callback(self.cog, interaction)

        player.play.assert_awaited_once_with(first, start=0)
        self.assertEqual(list(music.get_queue(guild.id)), [second])
        save_config.assert_called_once_with(guild.id, music.MUSIC_SAVED_STATE_KEY, None)
        self.assertIn("已回復 2 首", interaction.followup.send.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
