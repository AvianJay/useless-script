"""測試用 i18n 輔助。

ZhTWLocaleMixin：把 locale 釘在 zh-TW，讓既有對中文輸出做斷言的測試
不受環境語言影響、永遠通過。用法：

    class MyTests(ZhTWLocaleMixin, unittest.TestCase):
        ...
"""
import sys
from pathlib import Path

DISCORD_DIR = Path(__file__).resolve().parents[1]
if str(DISCORD_DIR) not in sys.path:
    sys.path.insert(0, str(DISCORD_DIR))

import i18n  # noqa: E402


class ZhTWLocaleMixin:
    def setUp(self):
        super().setUp()
        token = i18n.push_locale(i18n.SOURCE_LOCALE)
        self.addCleanup(i18n.reset_locale, token)


class FakeDB:
    """記憶體版 db，避免測試寫入真正的 data.db。"""

    def __init__(self):
        self.user_data = {}
        self.server_config = {}
        self.user_writes = 0
        self.server_writes = 0

    def get_user_data(self, user_id, guild_id, key, default=None):
        return self.user_data.get((user_id, guild_id, key), default)

    def set_user_data(self, user_id, guild_id, key, value):
        self.user_writes += 1
        self.user_data[(user_id, guild_id, key)] = value
        return True

    def get_server_config(self, guild_id, key, default=None):
        return self.server_config.get((guild_id, key), default)

    def set_server_config(self, guild_id, key, value):
        self.server_writes += 1
        self.server_config[(guild_id, key)] = value
        return True


def clear_i18n_caches():
    i18n._user_locale_cache.clear()
    i18n._guild_locale_cache.clear()
    i18n._last_locale_cache.clear()
