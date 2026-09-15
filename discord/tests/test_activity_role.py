import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

import discord

DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))

import ActivityRole as ar


def fake_activity(type_name: str, name: str, app_id=None):
    reverse = {v: k for k, v in ar.ACTIVITY_TYPE_NAMES.items()}
    return SimpleNamespace(type=reverse[type_name], name=name, application_id=app_id)


class ActivitySignalTests(unittest.TestCase):
    def test_playing_activity_yields_type_app_id_and_name(self):
        signals = ar.activity_signals([fake_activity("playing", "Minecraft", 1402418491272986635)])
        self.assertEqual(signals, [("playing", 1402418491272986635, "Minecraft")])

    def test_custom_status_is_excluded(self):
        """自訂狀態的文字會出現在 activity.name，不濾掉就會讓人白拿身分組。"""
        custom = discord.CustomActivity(name="Minecraft")
        self.assertEqual(custom.type, discord.ActivityType.custom)
        self.assertEqual(custom.name, "Minecraft")  # discord.py 會把文字塞進 name
        self.assertEqual(ar.activity_signals([custom]), [])

    def test_game_without_application_id_still_yields_name(self):
        game = discord.Game(name="某個沒被偵測到的遊戲")
        self.assertIsNone(getattr(game, "application_id", None))
        self.assertEqual(ar.activity_signals([game]),
                         [("playing", None, "某個沒被偵測到的遊戲")])

    def test_nameless_and_unknown_activities_are_skipped(self):
        self.assertEqual(ar.activity_signals([SimpleNamespace(type=None, name="x")]), [])
        self.assertEqual(ar.activity_signals([fake_activity("playing", "")]), [])
        self.assertEqual(ar.activity_signals(None), [])


class MatchingTests(unittest.TestCase):
    def setUp(self):
        self.role = 999

    def mapping(self, **kwargs):
        base = {"role_id": self.role, "app_ids": [], "names": [], "patterns": [],
                "types": list(ar.DEFAULT_TYPES)}
        base.update(kwargs)
        return [ar._normalize_mapping(base)]

    def test_matches_by_application_id(self):
        mappings = self.mapping(app_ids=[123])
        signals = ar.activity_signals([fake_activity("playing", "Whatever", 123)])
        self.assertEqual(ar.wanted_role_ids(mappings, signals), frozenset({self.role}))

    def test_matches_by_exact_name_case_insensitively(self):
        mappings = self.mapping(names=["Minecraft"])
        signals = ar.activity_signals([fake_activity("playing", "MINECRAFT")])
        self.assertEqual(ar.wanted_role_ids(mappings, signals), frozenset({self.role}))

    def test_exact_name_does_not_match_a_substring(self):
        mappings = self.mapping(names=["Minecraft"])
        signals = ar.activity_signals([fake_activity("playing", "Minecraft Dungeons")])
        self.assertEqual(ar.wanted_role_ids(mappings, signals), frozenset())

    def test_pattern_matches_a_substring(self):
        mappings = self.mapping(patterns=["minecraft"])
        signals = ar.activity_signals([fake_activity("playing", "Minecraft: Java Edition")])
        self.assertEqual(ar.wanted_role_ids(mappings, signals), frozenset({self.role}))

    def test_type_filter_keeps_spotify_out_of_a_playing_only_mapping(self):
        """Spotify 是 listening 型別，不該命中只認 playing 的規則。"""
        mappings = self.mapping(patterns=["spotify"])
        signals = ar.activity_signals([fake_activity("listening", "Spotify")])
        self.assertEqual(ar.wanted_role_ids(mappings, signals), frozenset())

        mappings = self.mapping(patterns=["spotify"], types=["listening"])
        self.assertEqual(ar.wanted_role_ids(mappings, signals), frozenset({self.role}))

    def test_streaming_role_requires_the_streaming_type(self):
        signals = ar.activity_signals([fake_activity("streaming", "Just Chatting")])
        self.assertEqual(ar.wanted_role_ids(self.mapping(patterns=["chatting"]), signals),
                         frozenset())
        self.assertEqual(
            ar.wanted_role_ids(self.mapping(patterns=["chatting"], types=["streaming"]), signals),
            frozenset({self.role}))

    def test_no_signals_means_no_roles(self):
        self.assertEqual(ar.wanted_role_ids(self.mapping(app_ids=[123]), []), frozenset())

    def test_multiple_app_ids_cover_game_variants(self):
        """同一款遊戲在 detectable 裡有多個 application_id，單綁一個會漏人。"""
        variants = [1402418491272986635, 1410791091501928458, 1501058387645825165]
        mappings = self.mapping(app_ids=variants)
        for app_id in variants:
            signals = ar.activity_signals([fake_activity("playing", "Minecraft", app_id)])
            self.assertEqual(ar.wanted_role_ids(mappings, signals), frozenset({self.role}),
                             f"variant {app_id} should match")


class MappingNormalizationTests(unittest.TestCase):
    def test_junk_is_dropped_and_defaults_applied(self):
        mapping = ar._normalize_mapping({
            "role_id": "42",
            "app_ids": [1, "2", None, "oops"],
            "names": ["A", "A", "  "],
            "patterns": [],
            "types": ["playing", "nonsense"],
        })
        self.assertEqual(mapping["role_id"], 42)
        self.assertEqual(mapping["app_ids"], [1, 2])
        self.assertEqual(mapping["names"], ["A"])
        self.assertEqual(mapping["types"], ["playing"])

    def test_missing_types_falls_back_to_playing(self):
        mapping = ar._normalize_mapping({"role_id": 1, "types": []})
        self.assertEqual(mapping["types"], list(ar.DEFAULT_TYPES))

    def test_bad_role_id_returns_none(self):
        self.assertIsNone(ar._normalize_mapping({"role_id": None}))
        self.assertIsNone(ar._normalize_mapping({"role_id": "abc"}))
        self.assertIsNone(ar._normalize_mapping({"role_id": 0}))

    def test_mapping_is_empty_detects_no_criteria(self):
        self.assertTrue(ar.mapping_is_empty(ar._normalize_mapping({"role_id": 1})))
        self.assertFalse(ar.mapping_is_empty(ar._normalize_mapping({"role_id": 1, "app_ids": [7]})))


class GamesIndexTests(unittest.TestCase):
    def setUp(self):
        self.index = ar.GamesIndex()
        self.index._build([
            {"i": "1402418491272986635", "n": "Minecraft",
             "a": ["Minecraft Launcher", "Minecraft Windows 10 Edition"]},
            {"i": "1410791091501928458", "n": "Minecraft: Java Edition"},
            {"i": "1124352351269048370", "n": "Minecraft Dungeons"},
            {"i": "1220613344650727465", "n": "砍佳惠 ～全人館之戰～"},
            {"i": "bad", "n": "skipped"},
            {"i": "7", "n": "   "},
        ])

    def test_invalid_entries_are_skipped(self):
        self.assertEqual(len(self.index.rows), 4)

    def test_exact_match_ranks_first(self):
        results = self.index.search("minecraft")
        self.assertEqual(results[0][1], "Minecraft")

    def test_alias_is_searchable(self):
        results = self.index.search("windows 10")
        self.assertEqual([name for _, name in results], ["Minecraft"])

    def test_all_matching_ids_covers_every_variant(self):
        ids = self.index.all_matching_ids("minecraft")
        self.assertEqual(len(ids), 3)
        self.assertIn(1410791091501928458, ids)
        self.assertEqual(self.index.match_count("minecraft"), 3)

    def test_name_lookup_and_cjk_search(self):
        self.assertEqual(self.index.name_for(1220613344650727465), "砍佳惠 ～全人館之戰～")
        self.assertEqual([n for _, n in self.index.search("全人館")], ["砍佳惠 ～全人館之戰～"])

    def test_empty_query_returns_nothing(self):
        self.assertEqual(self.index.search(""), [])
        self.assertEqual(self.index.match_count(""), 0)


if __name__ == "__main__":
    unittest.main()
