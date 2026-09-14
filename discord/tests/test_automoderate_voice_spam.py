"""anti_voice_spam（防止抽插語音房）的行為測試。

重點在三件容易寫錯的事：
  1. 開關麥克風等「同頻道自身狀態變化」絕不能被算成一次進入
  2. same_channel 模式下正常換頻道不能觸發，但同一間房來回進出要觸發
  3. DynamicVoice 自己製造的 join/move 事件必須完全豁免
"""
import asyncio
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))

import discord  # noqa: E402

import AutoModerate  # noqa: E402
from tests.i18n_base import ZhTWLocaleMixin  # noqa: E402


GUILD_ID = 4001
USER_ID = 7002
CH_X, CH_Y, CH_Z = 9001, 9002, 9003
LOBBY_ID, TEMP_ID, TEMP2_ID = 9500, 9501, 9502
AFK_ID = 9600


class FakePermissions:
    def __init__(self, view_channel=True, send_messages=True):
        self.view_channel = view_channel
        self.send_messages = send_messages


class FakeVoiceChannel:
    def __init__(self, channel_id, perms=None):
        self.id = channel_id
        self.mention = f"<#{channel_id}>"
        self._perms = perms or FakePermissions()
        self.send = AsyncMock()

    def permissions_for(self, _member):
        return self._perms


class FakeMember:
    """SimpleNamespace 無法覆寫 __str__（特殊方法只在 type 上查找），所以用小類別。"""

    def __init__(self, guild):
        self.id = USER_ID
        self.bot = False
        self.guild = guild
        self.mention = f"<@{USER_ID}>"
        self.guild_permissions = SimpleNamespace(administrator=False)

    def __str__(self):
        return "Spammer#0001"


def voice_state(channel):
    return SimpleNamespace(channel=channel)


class VoiceSpamTestCase(ZhTWLocaleMixin, unittest.TestCase):
    """共用骨架：假的 guild/member、假設定、假動作執行。"""

    def setUp(self):
        super().setUp()
        AutoModerate._voice_spam_tracker.clear()
        self.addCleanup(AutoModerate._voice_spam_tracker.clear)

        self.channels = {cid: FakeVoiceChannel(cid) for cid in
                         (CH_X, CH_Y, CH_Z, LOBBY_ID, TEMP_ID, TEMP2_ID, AFK_ID)}
        self.afk_channel = self.channels[AFK_ID]
        self.guild = SimpleNamespace(id=GUILD_ID, me=SimpleNamespace(id=1), afk_channel=None)
        self.member = FakeMember(self.guild)

        self.feature_config = {"enabled": True, "action": "mute 10m test"}
        self.server_config = {}

        logp = patch.object(AutoModerate, "log")
        logp.start()
        self.addCleanup(logp.stop)

        self.do_action = AsyncMock(return_value=["muted"])
        patcher = patch.object(AutoModerate, "do_action_str", self.do_action)
        patcher.start()
        self.addCleanup(patcher.stop)

        cfg = patch.object(AutoModerate, "get_server_config", side_effect=self._get_server_config)
        cfg.start()
        self.addCleanup(cfg.stop)

        # 預設不啟用 DynamicVoice，個別測試自己打開
        mods = patch.object(AutoModerate, "modules", ["Moderate"])
        mods.start()
        self.addCleanup(mods.stop)

        # 動作合法性驗證：預設一律通過，讓測試聚焦在偵測邏輯
        self.analysis = {"valid": True, "requires_confirmation": False,
                         "normalized": "mute 10m test", "error": None, "confirmation": None}
        moderate = MagicMock()
        moderate.analyze_member_join_action.return_value = self.analysis
        # 測試環境的 globalenv.modules 是空的，所以 AutoModerate.Moderate 不存在 -> create=True
        mod_patch = patch.object(AutoModerate, "Moderate", moderate, create=True)
        mod_patch.start()
        self.addCleanup(mod_patch.stop)

    def _get_server_config(self, guild_id, key, default=None):
        if key == "automod":
            return {"anti_voice_spam": self.feature_config}
        return self.server_config.get(key, default)

    # -- helpers ---------------------------------------------------------
    def cog(self):
        return AutoModerate.AutoModerate.__new__(AutoModerate.AutoModerate)

    def fire(self, before_id, after_id):
        """送一個 voice state update 進 listener（含分類守衛）。"""
        before = voice_state(self.channels[before_id] if before_id is not None else None)
        after = voice_state(self.channels[after_id] if after_id is not None else None)
        asyncio.run(AutoModerate.AutoModerate.on_voice_state_update(
            self.cog(), self.member, before, after))

    def timestamps(self):
        return AutoModerate._voice_spam_tracker.get(GUILD_ID, {})


class ClassificationTests(VoiceSpamTestCase):
    def test_self_state_change_never_counts(self):
        """開關麥克風/耳機/直播都是同頻道事件，絕不能算成進入。"""
        self.feature_config["max_joins"] = "2"
        for _ in range(10):
            self.fire(CH_X, CH_X)
        self.assertEqual(self.timestamps(), {})
        self.do_action.assert_not_awaited()

    def test_leave_never_counts(self):
        self.feature_config["max_joins"] = "2"
        for _ in range(10):
            self.fire(CH_X, None)
        self.assertEqual(self.timestamps(), {})
        self.do_action.assert_not_awaited()

    def test_bot_member_ignored(self):
        self.member.bot = True
        self.feature_config["max_joins"] = "1"
        self.fire(None, CH_X)
        self.assertEqual(self.timestamps(), {})
        self.do_action.assert_not_awaited()

    def test_administrator_ignored(self):
        self.member.guild_permissions = SimpleNamespace(administrator=True)
        self.feature_config["max_joins"] = "1"
        self.fire(None, CH_X)
        self.assertEqual(self.timestamps(), {})
        self.do_action.assert_not_awaited()

    def test_disabled_feature_does_nothing(self):
        self.feature_config["enabled"] = False
        self.feature_config["max_joins"] = "1"
        self.fire(None, CH_X)
        self.assertEqual(self.timestamps(), {})
        self.do_action.assert_not_awaited()


class DetectModeTests(VoiceSpamTestCase):
    def test_same_channel_mode_ignores_normal_switching(self):
        """A→B→C→D→E 這種正常換房，每間房各一次，永遠到不了門檻。"""
        self.feature_config.update({"max_joins": "3", "detect_mode": "same_channel"})
        self.fire(None, CH_X)
        self.fire(CH_X, CH_Y)
        self.fire(CH_Y, CH_Z)
        self.do_action.assert_not_awaited()
        self.assertEqual({key: len(v) for key, v in self.timestamps().items()},
                         {(USER_ID, CH_X): 1, (USER_ID, CH_Y): 1, (USER_ID, CH_Z): 1})

    def test_same_channel_mode_triggers_on_cycling(self):
        """反覆進出同一間房 = 抽插，要觸發；且呼叫時不得帶 message。"""
        self.feature_config.update({"max_joins": "3", "detect_mode": "same_channel"})
        self.fire(None, CH_X)
        self.fire(CH_X, None)
        self.fire(None, CH_X)
        self.fire(CH_X, None)
        self.fire(None, CH_X)
        self.do_action.assert_awaited_once()
        args, kwargs = self.do_action.await_args
        self.assertEqual(args[0], "mute 10m test")
        self.assertEqual(kwargs["guild"], self.guild)
        self.assertEqual(kwargs["user"], self.member)
        self.assertNotIn("message", kwargs)

    def test_same_channel_mode_counts_move_in(self):
        """X→Y→X→Y→X 從不乾淨離開，但仍是抽插，必須算到。"""
        self.feature_config.update({"max_joins": "3", "detect_mode": "same_channel"})
        for before, after in ((None, CH_X), (CH_X, CH_Y), (CH_Y, CH_X),
                              (CH_X, CH_Y), (CH_Y, CH_X)):
            self.fire(before, after)
        self.do_action.assert_awaited_once()

    def test_any_channel_mode_triggers_on_hopping(self):
        """同一組跨房跳躍在 any_channel 模式下要觸發。"""
        self.feature_config.update({"max_joins": "3", "detect_mode": "any_channel"})
        self.fire(None, CH_X)
        self.fire(CH_X, CH_Y)
        self.fire(CH_Y, CH_Z)
        self.do_action.assert_awaited_once()
        self.assertEqual(list(self.timestamps()), [])

    def test_invalid_detect_mode_falls_back_to_same_channel(self):
        self.feature_config.update({"max_joins": "3", "detect_mode": "garbage"})
        self.fire(None, CH_X)
        self.fire(CH_X, CH_Y)
        self.fire(CH_Y, CH_Z)
        self.do_action.assert_not_awaited()


class WindowAndTrackerTests(VoiceSpamTestCase):
    def test_window_prune_drops_stale_timestamps(self):
        self.feature_config.update({"max_joins": "3", "time_window": "60"})
        now = datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc)
        times = [now, now + timedelta(seconds=90), now + timedelta(seconds=100)]
        with patch.object(AutoModerate, "datetime") as fake_dt:
            fake_dt.now.side_effect = times
            for _ in times:
                self.fire(None, CH_X)
        # 第一筆已超出視窗，只剩兩筆 -> 不觸發
        self.do_action.assert_not_awaited()
        self.assertEqual(len(self.timestamps()[(USER_ID, CH_X)]), 2)

    def test_tracker_cleared_after_trigger(self):
        self.feature_config["max_joins"] = "2"
        self.fire(None, CH_X)
        self.fire(None, CH_X)
        self.do_action.assert_awaited_once()
        # 觸發後計數器清空，下一次進入不會立刻再觸發
        self.fire(None, CH_X)
        self.do_action.assert_awaited_once()

    def test_tracker_cleared_even_when_action_raises(self):
        """先清空再 await 的保證：動作失敗不能讓每個後續事件都重新觸發。"""
        self.feature_config["max_joins"] = "2"
        self.do_action.side_effect = RuntimeError("no permission")
        self.fire(None, CH_X)
        self.fire(None, CH_X)
        self.assertEqual(self.do_action.await_count, 1)
        self.fire(None, CH_X)
        self.assertEqual(self.do_action.await_count, 1, "計數器沒清，動作被重複重試")

    def test_malformed_numeric_settings_fall_back_to_defaults(self):
        """max_joins 打錯字不能讓每個語音事件都拋 ValueError。"""
        self.feature_config.update({"max_joins": "abc", "time_window": "xyz"})
        for _ in range(4):
            self.fire(None, CH_X)
        self.do_action.assert_not_awaited()  # 退回預設 5
        self.fire(None, CH_X)
        self.do_action.assert_awaited_once()

    def test_tracker_prunes_when_over_key_limit(self):
        self.feature_config.update({"max_joins": "5", "time_window": "60"})
        stale = datetime.now(timezone.utc) - timedelta(seconds=600)
        guild_tracker = AutoModerate._voice_spam_tracker.setdefault(GUILD_ID, {})
        for i in range(AutoModerate._VOICE_SPAM_TRACKER_MAX_KEYS + 10):
            guild_tracker[(i, CH_Z)] = [stale]
        self.fire(None, CH_X)
        self.assertLessEqual(len(self.timestamps()), 2,
                             "超過上限時應清掉過期 key")

    def test_empty_guild_entry_is_dropped(self):
        self.feature_config["max_joins"] = "2"
        self.fire(None, CH_X)
        self.fire(None, CH_X)
        # 觸發後該 guild 已無任何 key，整個 guild 項應被移除
        self.assertNotIn(GUILD_ID, AutoModerate._voice_spam_tracker)


class ExemptionTests(VoiceSpamTestCase):
    def test_ignore_channels_respected(self):
        self.feature_config.update({"max_joins": "2", "ignore_channels": [CH_X]})
        self.fire(None, CH_X)
        self.fire(None, CH_X)
        self.do_action.assert_not_awaited()
        self.assertEqual(self.timestamps(), {})

    def test_afk_channel_moves_exempt(self):
        self.guild.afk_channel = self.afk_channel
        self.feature_config["max_joins"] = "2"
        self.fire(CH_X, AFK_ID)
        self.fire(AFK_ID, CH_X)
        self.do_action.assert_not_awaited()
        self.assertEqual(self.timestamps(), {})

    def test_dynamic_voice_lobby_and_temp_channels_exempt(self):
        """重播 DynamicVoice 的真實序列，必須一筆都不記。

        created_dynamic_channels 故意留空，模擬臨時房已被積極清掉的情況 ——
        這時仍要靠穩定的入口房 ID 攔下那次 move-in。
        """
        with patch.object(AutoModerate, "modules", ["Moderate", "DynamicVoice"]):
            self.server_config["dynamic_voice_channel"] = str(LOBBY_ID)
            self.server_config["created_dynamic_channels"] = []
            for mode in ("same_channel", "any_channel"):
                with self.subTest(mode=mode):
                    AutoModerate._voice_spam_tracker.clear()
                    self.do_action.reset_mock()
                    self.feature_config.update({"max_joins": "2", "detect_mode": mode})
                    self.fire(None, LOBBY_ID)      # 進入口房
                    self.fire(LOBBY_ID, TEMP_ID)   # 機器人搬到臨時房
                    self.fire(TEMP_ID, None)       # 離開，臨時房被刪
                    self.fire(None, LOBBY_ID)      # 想回來，又得走一次入口房
                    self.fire(LOBBY_ID, TEMP2_ID)
                    self.assertEqual(self.timestamps(), {})
                    self.do_action.assert_not_awaited()

    def test_dynamic_voice_exemption_inactive_when_module_absent(self):
        """沒載入 DynamicVoice 時不該為了它多讀兩次設定。"""
        self.server_config["dynamic_voice_channel"] = str(LOBBY_ID)
        self.feature_config["max_joins"] = "2"
        self.fire(None, LOBBY_ID)
        self.fire(None, LOBBY_ID)
        self.do_action.assert_awaited_once()


class VoiceNoticeTests(VoiceSpamTestCase):
    def trigger(self):
        self.feature_config["max_joins"] = "2"
        self.fire(None, CH_X)
        self.fire(None, CH_X)

    def test_voice_notice_sent_after_action(self):
        self.feature_config["log_into_voice_channel"] = True
        order = []
        self.do_action.side_effect = lambda *a, **k: order.append("action") or ["muted"]
        self.channels[CH_X].send.side_effect = lambda *a, **k: order.append("notice")
        self.trigger()
        self.channels[CH_X].send.assert_awaited_once()
        _, kwargs = self.channels[CH_X].send.await_args
        self.assertIsInstance(kwargs["embed"], discord.Embed)
        self.assertEqual(order, ["action", "notice"], "通知必須在處置之後才發")

    def test_voice_notice_defaults_to_enabled(self):
        self.feature_config.pop("log_into_voice_channel", None)
        self.trigger()
        self.channels[CH_X].send.assert_awaited_once()

    def test_voice_notice_disabled(self):
        self.feature_config["log_into_voice_channel"] = False
        self.trigger()
        self.channels[CH_X].send.assert_not_awaited()
        self.do_action.assert_awaited_once()

    def test_voice_notice_respects_falsey_string(self):
        self.feature_config["log_into_voice_channel"] = "False"
        self.trigger()
        self.channels[CH_X].send.assert_not_awaited()

    def test_voice_notice_skipped_without_send_permission(self):
        self.channels[CH_X]._perms = FakePermissions(view_channel=True, send_messages=False)
        self.trigger()
        self.channels[CH_X].send.assert_not_awaited()
        self.do_action.assert_awaited_once()  # 處置照樣執行

    def test_voice_notice_failure_does_not_break_action(self):
        self.channels[CH_X].send.side_effect = discord.NotFound(
            SimpleNamespace(status=404, reason="Not Found"), "channel gone")
        self.trigger()  # 不得向外拋
        self.do_action.assert_awaited_once()

    def test_no_notice_when_action_failed(self):
        """動作失敗時不能公告「已處置」。"""
        self.do_action.side_effect = RuntimeError("boom")
        self.trigger()
        self.channels[CH_X].send.assert_not_awaited()

    def test_no_action_when_config_invalid(self):
        """儲存的動作若通不過無訊息驗證器，直接放棄並記錄錯誤。"""
        self.analysis.update({"valid": False, "error": "unsupported"})
        self.trigger()
        self.do_action.assert_not_awaited()
        self.channels[CH_X].send.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
