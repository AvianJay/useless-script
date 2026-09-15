"""ActivityRole —— 依使用者正在進行的活動（遊戲 / 開台等）自動指派身分組。

資料來源是 presence（Guild Presences privileged intent）。沒開 intent 時本模組
仍可完整設定與檢視，只是不會有任何身分組變動；intent 一開就自動生效。

規模考量（1000+ 伺服器 / 10 萬+ 使用者，presence 是消防水管）：
  1. 先過濾「有啟用的伺服器」，未啟用的在第一行就 return。
  2. Diff 導向：記住上次算出的 wanted set，沒變就不打任何 API
     （切歌、online→idle、改自訂狀態都會死在這一行）。
  3. Debounce：遊戲啟動瞬間 activity 會分批補齊，alt-tab 也會抖。
  4. 每個伺服器一條序列化佇列，避免同一個 per-guild bucket 並發 429。

遊戲清單來源：
  - /applications/detectable（免 auth，約 2.4 萬筆）抓一次裁切後快取到本地，
    autocomplete 純記憶體比對，不在每個按鍵打 Discord。
  - 不在 detectable 裡的 custom application（小開發者自製 app）靠兩條路補：
    伺服器實際觀測到的活動清單，以及 /applications/{id}/rpc 反查。
"""

import asyncio
import gzip
import json
import logging
import os
import time

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

import i18n
from i18n import t
from globalenv import (
    bot,
    get_server_config,
    set_server_config,
    get_global_config,
    set_global_config,
    get_all_server_config_key,
    register_panel_settings,
    on_ready_tasks,
)
from logger import log

MODULE = "ActivityRole"

# ============= 設定鍵 =============

ENABLED_KEY = "activityrole_enabled"
MODE_KEY = "activityrole_mode"                    # live | sticky
REMOVE_AFTER_KEY = "activityrole_remove_after"    # 分鐘，0 = 立即收回
IGNORE_BOTS_KEY = "activityrole_ignore_bots"
LOG_CHANNEL_KEY = "activityrole_log_channel"
MAPPINGS_KEY = "activityrole_mappings"            # 由指令管理，不進面板
OBSERVED_KEY = "activityrole_observed"            # 觀測到的活動（自動學習）

APP_NAME_CACHE_KEY = "activityrole_app_name_cache"
GAMES_FETCHED_AT_KEY = "activityrole_games_fetched_at"

MODE_LIVE = "live"
MODE_STICKY = "sticky"

# ============= 常數 =============

DETECTABLE_URL = "https://discord.com/api/v10/applications/detectable"
APP_RPC_URL = "https://discord.com/api/v10/applications/{app_id}/rpc"

_HERE = os.path.dirname(os.path.abspath(__file__))
GAMES_CACHE_PATH = os.path.join(_HERE, "cache", "activityrole_games.json.gz")

GAMES_REFRESH_SECONDS = 7 * 24 * 3600     # 每週更新一次遊戲清單
DEBOUNCE_SECONDS = 15                     # 活動變動後等這麼久才套用
OBSERVED_FLUSH_SECONDS = 300              # 觀測表每 5 分鐘寫回資料庫
OBSERVED_MAX_PER_GUILD = 50               # 每伺服器保留的觀測筆數上限
OBSERVED_TTL_SECONDS = 30 * 24 * 3600     # 超過這個時間沒再看到就淘汰
RECONCILE_STAGGER_SECONDS = 3             # 開機對帳時每個伺服器之間的間隔
AUTOCOMPLETE_LIMIT = 25

ACTIVITY_TYPE_NAMES = {
    discord.ActivityType.playing: "playing",
    discord.ActivityType.streaming: "streaming",
    discord.ActivityType.listening: "listening",
    discord.ActivityType.watching: "watching",
    discord.ActivityType.competing: "competing",
}
VALID_TYPE_NAMES = tuple(ACTIVITY_TYPE_NAMES.values())
DEFAULT_TYPES = ("playing",)

# presence intent 有沒有開，決定要不要掛監聽器。
PRESENCE_INTENT_ON = bool(getattr(bot.intents, "presences", False))


# ============= 可偵測遊戲清單 =============

class GamesIndex:
    """detectable 清單的本地索引（id / 名稱 / 別名）。"""

    def __init__(self):
        self.rows: list[tuple[int, str]] = []            # (app_id, 顯示名稱)
        self.by_id: dict[int, str] = {}
        self._haystack: list[tuple[str, int, str]] = []  # (可搜尋字串, app_id, 顯示名稱)
        self.loaded = False

    # ---- 磁碟 ----

    def load_from_disk(self) -> bool:
        try:
            with gzip.open(GAMES_CACHE_PATH, "rt", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return False
        except Exception as e:
            log(f"Failed to read the games cache: {e}", level=logging.WARNING, module_name=MODULE)
            return False
        self._build(data)
        return True

    def _save_to_disk(self, data: list[dict]) -> None:
        os.makedirs(os.path.dirname(GAMES_CACHE_PATH), exist_ok=True)
        tmp = GAMES_CACHE_PATH + ".tmp"
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, GAMES_CACHE_PATH)

    def _build(self, data: list[dict]) -> None:
        rows: list[tuple[int, str]] = []
        by_id: dict[int, str] = {}
        haystack: list[tuple[str, int, str]] = []
        for entry in data:
            try:
                app_id = int(entry["i"])
            except (KeyError, TypeError, ValueError):
                continue
            name = str(entry.get("n") or "").strip()
            if not name:
                continue
            rows.append((app_id, name))
            by_id[app_id] = name
            searchable = [name]
            for alias in entry.get("a") or ():
                if alias:
                    searchable.append(str(alias))
            haystack.append(("\n".join(searchable).casefold(), app_id, name))
        self.rows = rows
        self.by_id = by_id
        self._haystack = haystack
        self.loaded = True

    # ---- 網路 ----

    async def refresh(self, *, force: bool = False) -> bool:
        """抓 detectable 清單、裁成 id/名稱/別名後快取。回傳是否真的更新了。"""
        if not force:
            fetched_at = get_global_config(GAMES_FETCHED_AT_KEY, 0) or 0
            if self.loaded and (time.time() - float(fetched_at)) < GAMES_REFRESH_SECONDS:
                return False
        try:
            timeout = aiohttp.ClientTimeout(total=120)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(DETECTABLE_URL) as resp:
                    if resp.status != 200:
                        log(f"detectable returned HTTP {resp.status}", level=logging.WARNING, module_name=MODULE)
                        return False
                    raw = await resp.json(content_type=None)
        except Exception as e:
            log(f"Failed to fetch the detectable list: {e}", level=logging.WARNING, module_name=MODULE)
            return False

        if not isinstance(raw, list) or not raw:
            return False

        trimmed = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            app_id = entry.get("id")
            name = entry.get("name")
            if not app_id or not name:
                continue
            item = {"i": str(app_id), "n": str(name)}
            aliases = [str(a) for a in (entry.get("aliases") or []) if a]
            if aliases:
                item["a"] = aliases
            trimmed.append(item)

        if not trimmed:
            return False

        try:
            self._save_to_disk(trimmed)
        except Exception as e:
            log(f"Failed to write the games cache: {e}", level=logging.WARNING, module_name=MODULE)

        self._build(trimmed)
        set_global_config(GAMES_FETCHED_AT_KEY, time.time())
        log(f"Games index updated: {len(trimmed)} entries", module_name=MODULE)
        return True

    # ---- 搜尋 ----

    def search(self, query: str, limit: int = AUTOCOMPLETE_LIMIT) -> list[tuple[int, str]]:
        """完全相符 > 開頭相符 > 包含，各段內維持原順序。"""
        q = (query or "").strip().casefold()
        if not q or not self._haystack:
            return []
        exact: list[tuple[int, str]] = []
        prefix: list[tuple[int, str]] = []
        contains: list[tuple[int, str]] = []
        for hay, app_id, name in self._haystack:
            if q not in hay:
                continue
            low = name.casefold()
            if low == q:
                exact.append((app_id, name))
            elif hay.startswith(q) or low.startswith(q):
                prefix.append((app_id, name))
            else:
                contains.append((app_id, name))
            if len(exact) + len(prefix) + len(contains) >= limit * 4:
                break
        seen: set[int] = set()
        out: list[tuple[int, str]] = []
        for bucket in (exact, prefix, contains):
            for app_id, name in bucket:
                if app_id in seen:
                    continue
                seen.add(app_id)
                out.append((app_id, name))
                if len(out) >= limit:
                    return out
        return out

    def match_count(self, query: str) -> int:
        q = (query or "").strip().casefold()
        if not q:
            return 0
        return sum(1 for hay, _, _ in self._haystack if q in hay)

    def all_matching_ids(self, query: str) -> list[int]:
        q = (query or "").strip().casefold()
        if not q:
            return []
        return [app_id for hay, app_id, _ in self._haystack if q in hay]

    def name_for(self, app_id: int) -> str | None:
        return self.by_id.get(int(app_id))


games = GamesIndex()


async def resolve_app_name(app_id: int) -> str | None:
    """把任意 application_id 反查成名稱。

    先查 detectable，再查快取，最後才打 /applications/{id}/rpc —— 那支是免 auth
    的端點（rate limit 綁 IP），所以只允許管理員手動操作觸發，絕不在 presence
    事件裡呼叫。
    """
    app_id = int(app_id)
    name = games.name_for(app_id)
    if name:
        return name

    cache = get_global_config(APP_NAME_CACHE_KEY, {}) or {}
    cached = cache.get(str(app_id))
    if cached:
        return cached

    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(APP_RPC_URL.format(app_id=app_id)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json(content_type=None)
    except Exception as e:
        log(f"Failed to look up application {app_id}: {e}", level=logging.WARNING, module_name=MODULE)
        return None

    name = (data or {}).get("name")
    if not name:
        return None
    name = str(name)
    cache[str(app_id)] = name
    set_global_config(APP_NAME_CACHE_KEY, cache)
    return name


# ============= 映射資料 =============

def _normalize_mapping(raw: dict) -> dict | None:
    try:
        role_id = int(raw.get("role_id"))
    except (TypeError, ValueError):
        return None
    if not role_id:
        return None
    app_ids = []
    for value in raw.get("app_ids") or ():
        try:
            app_ids.append(int(value))
        except (TypeError, ValueError):
            continue
    types = [str(v) for v in (raw.get("types") or ()) if str(v) in VALID_TYPE_NAMES]
    return {
        "role_id": role_id,
        "app_ids": sorted(set(app_ids)),
        "names": sorted({str(v) for v in (raw.get("names") or ()) if str(v).strip()}),
        "patterns": sorted({str(v) for v in (raw.get("patterns") or ()) if str(v).strip()}),
        "types": types or list(DEFAULT_TYPES),
    }


def get_mappings(guild_id: int) -> list[dict]:
    raw = get_server_config(guild_id, MAPPINGS_KEY, []) or []
    out = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                norm = _normalize_mapping(item)
                if norm:
                    out.append(norm)
    return out


def set_mappings(guild_id: int, mappings: list[dict]) -> None:
    set_server_config(guild_id, MAPPINGS_KEY, [m for m in mappings if m.get("role_id")])


def get_mapping_for_role(mappings: list[dict], role_id: int) -> dict | None:
    for m in mappings:
        if m["role_id"] == int(role_id):
            return m
    return None


def managed_role_ids(mappings: list[dict]) -> set[int]:
    """本模組負責增減的身分組集合 —— 只有這些才會被自動收回。"""
    return {m["role_id"] for m in mappings}


def mapping_is_empty(mapping: dict) -> bool:
    return not (mapping.get("app_ids") or mapping.get("names") or mapping.get("patterns"))


# ============= 比對邏輯 =============

def activity_signals(activities) -> list[tuple[str, int | None, str]]:
    """把 activities 攤平成 [(型別名, application_id|None, 活動名稱), ...]。

    自訂狀態一定排除：discord.py 會把 payload 裡 name="Custom Status" 換成使用者
    真正寫的狀態文字（activity.py 的 CustomActivity），所以自訂狀態的文字會出現在
    activity.name 裡。不濾掉的話「狀態寫 Minecraft」的人會白拿身分組。
    """
    out: list[tuple[str, int | None, str]] = []
    for act in activities or ():
        type_name = ACTIVITY_TYPE_NAMES.get(getattr(act, "type", None))
        if type_name is None:
            continue  # custom 與未知型別
        name = getattr(act, "name", None)
        if not name:
            continue
        app_id = getattr(act, "application_id", None)
        try:
            app_id = int(app_id) if app_id else None
        except (TypeError, ValueError):
            app_id = None
        out.append((type_name, app_id, str(name)))
    return out


def wanted_role_ids(mappings: list[dict], signals: list[tuple[str, int | None, str]]) -> frozenset[int]:
    if not mappings or not signals:
        return frozenset()
    wanted: set[int] = set()
    for mapping in mappings:
        app_ids = mapping["app_ids"]
        names = {n.casefold() for n in mapping["names"]}
        patterns = [p.casefold() for p in mapping["patterns"]]
        allowed_types = mapping["types"]
        for type_name, app_id, name in signals:
            if type_name not in allowed_types:
                continue
            low = name.casefold()
            if (app_id is not None and app_id in app_ids) or low in names or any(p in low for p in patterns):
                wanted.add(mapping["role_id"])
                break
    return frozenset(wanted)


# ============= Cog =============

@app_commands.guild_only()
@app_commands.default_permissions(manage_roles=True)
class ActivityRole(commands.GroupCog,
                   name=app_commands.locale_str("activity-role",
                                                i18n_key="cmd.activityrole.activity_role.root.name"),
                   description=app_commands.locale_str("Automatically assign roles based on member activity",
                                                       i18n_key="cmd.activityrole.activity_role.root.desc")):

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        super().__init__()

        # 有啟用本功能的伺服器；presence 事件第一行就用這個過濾。
        self._enabled_guilds: set[int] = set()
        # (guild_id, user_id) -> 上次算出的 wanted set；相同就完全不動作。
        self._last_wanted: dict[tuple[int, int], frozenset[int]] = {}
        # (guild_id, user_id) -> debounce 任務
        self._debounce: dict[tuple[int, int], asyncio.Task] = {}
        # guild_id -> 序列化佇列與 worker
        self._queues: dict[int, asyncio.Queue] = {}
        self._workers: dict[int, asyncio.Task] = {}
        # (guild_id, user_id, role_id) -> 預定收回的時間（remove_after）
        self._pending_removals: dict[tuple[int, int, int], float] = {}
        # guild_id -> {key: {"n": 名稱, "a": app_id|None, "c": 次數, "t": 最後出現}}
        self._observed: dict[int, dict] = {}
        self._observed_dirty: set[int] = set()

        self.refresh_enabled_guilds()

    async def cog_load(self):
        # 定時任務必須在 event loop 裡啟動：本模組的 cog 是在 asyncio.run() 的引數
        # 位置建構的，__init__ 跑的時候還沒有 running loop。
        for task in (self.flush_observed, self.process_pending_removals, self.refresh_games_index):
            if not task.is_running():
                task.start()

    async def cog_unload(self):
        self.flush_observed.cancel()
        self.process_pending_removals.cancel()
        self.refresh_games_index.cancel()
        for task in list(self._debounce.values()):
            task.cancel()
        for task in list(self._workers.values()):
            task.cancel()

    # ---- 啟用清單 ----

    def refresh_enabled_guilds(self) -> None:
        """重建有啟用的伺服器集合。這是整條管線最重要的一道過濾。"""
        enabled: set[int] = set()
        try:
            rows = get_all_server_config_key(ENABLED_KEY) or {}
        except Exception as e:
            log(f"Failed to load the enabled-guild list: {e}", level=logging.WARNING, module_name=MODULE)
            return
        items = rows.items() if isinstance(rows, dict) else rows
        for guild_id, value in items:
            if not value:
                continue
            try:
                enabled.add(int(guild_id))
            except (TypeError, ValueError):
                continue
        self._enabled_guilds = enabled

    def _guild_enabled(self, guild_id: int | None) -> bool:
        return bool(guild_id) and int(guild_id) in self._enabled_guilds

    # ---- 觀測表（自動學習，補 detectable 的缺口）----

    def _observed_for(self, guild_id: int) -> dict:
        if guild_id not in self._observed:
            raw = get_server_config(guild_id, OBSERVED_KEY, {}) or {}
            self._observed[guild_id] = raw if isinstance(raw, dict) else {}
        return self._observed[guild_id]

    def record_observed(self, guild_id: int, signals: list[tuple[str, int | None, str]]) -> None:
        """記下這個伺服器看到過的活動。

        presence 事件本身就帶名稱，所以這裡不需要任何 API 呼叫 —— 千萬不要在這條
        路徑上打 /rpc 反查，那是免 auth 的 IP bucket。
        """
        if not signals:
            return
        table = self._observed_for(guild_id)
        now = time.time()
        for type_name, app_id, name in signals:
            key = f"a{app_id}" if app_id else f"n{name.casefold()}"
            entry = table.get(key)
            if not isinstance(entry, dict):
                table[key] = {"n": name, "a": app_id, "t": now, "c": 1, "y": type_name}
            else:
                entry["n"] = name
                entry["t"] = now
                entry["c"] = int(entry.get("c", 0)) + 1
                entry["y"] = type_name
        # 淘汰：先丟過期的，再按最後出現時間裁到上限
        for key, entry in list(table.items()):
            if not isinstance(entry, dict) or now - float(entry.get("t", 0)) > OBSERVED_TTL_SECONDS:
                table.pop(key, None)
        if len(table) > OBSERVED_MAX_PER_GUILD:
            keep = sorted(table.items(), key=lambda kv: float(kv[1].get("t", 0)), reverse=True)
            self._observed[guild_id] = dict(keep[:OBSERVED_MAX_PER_GUILD])
        self._observed_dirty.add(guild_id)

    @tasks.loop(seconds=OBSERVED_FLUSH_SECONDS)
    async def flush_observed(self):
        """觀測表在記憶體裡累積，定期寫回，避免每個 presence 事件都碰資料庫。"""
        # 順便重讀啟用清單：面板以外的途徑（其他程序、直接改庫）也可能改動它。
        self.refresh_enabled_guilds()
        for guild_id in list(self._observed_dirty):
            self._observed_dirty.discard(guild_id)
            try:
                set_server_config(guild_id, OBSERVED_KEY, self._observed.get(guild_id, {}))
            except Exception as e:
                log(f"Failed to persist the observed-activity table for guild {guild_id}: {e}",
                    level=logging.WARNING, module_name=MODULE)

    @flush_observed.before_loop
    async def before_flush_observed(self):
        await self.bot.wait_until_ready()

    # ---- 遊戲清單定期更新 ----

    @tasks.loop(hours=24)
    async def refresh_games_index(self):
        await games.refresh()

    @refresh_games_index.before_loop
    async def before_refresh_games_index(self):
        await self.bot.wait_until_ready()

    # ---- presence 管線 ----

    @commands.Cog.listener()
    async def on_raw_presence_update(self, payload):
        await self._handle_presence(payload.guild_id, payload.user_id, payload.activities)

    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member):
        # raw 與非 raw 兩條都掛著：哪條會來取決於 intent 與 enable_raw_presences。
        # 重複投遞是安全的 —— _last_wanted 的 diff 會讓第二次變成 no-op。
        await self._handle_presence(after.guild.id, after.id, after.activities, member=after)

    async def _handle_presence(self, guild_id, user_id, activities, member=None):
        if not self._guild_enabled(guild_id):
            return  # 絕大多數事件死在這裡
        guild_id = int(guild_id)
        user_id = int(user_id)

        if member is not None and member.bot and get_server_config(guild_id, IGNORE_BOTS_KEY, True):
            return

        mappings = get_mappings(guild_id)
        if not mappings:
            return

        signals = activity_signals(activities)
        self.record_observed(guild_id, signals)

        wanted = wanted_role_ids(mappings, signals)
        key = (guild_id, user_id)
        if self._last_wanted.get(key) == wanted:
            return  # 跟上次一樣：零 REST 呼叫
        self._last_wanted[key] = wanted

        old = self._debounce.pop(key, None)
        if old is not None:
            old.cancel()
        self._debounce[key] = asyncio.create_task(self._debounced_apply(guild_id, user_id, wanted))

    async def _debounced_apply(self, guild_id: int, user_id: int, wanted: frozenset[int]):
        try:
            await asyncio.sleep(DEBOUNCE_SECONDS)
        except asyncio.CancelledError:
            return
        self._debounce.pop((guild_id, user_id), None)
        await self._enqueue(guild_id, user_id, wanted)

    # ---- 每伺服器序列化佇列 ----

    async def _enqueue(self, guild_id: int, user_id: int, wanted: frozenset[int]):
        queue = self._queues.get(guild_id)
        if queue is None:
            queue = asyncio.Queue()
            self._queues[guild_id] = queue
            self._workers[guild_id] = asyncio.create_task(self._worker(guild_id, queue))
        await queue.put((user_id, wanted))

    async def _worker(self, guild_id: int, queue: asyncio.Queue):
        """一個伺服器一條 worker：角色增減是 per-guild bucket，不能並發。"""
        while True:
            try:
                user_id, wanted = await queue.get()
            except asyncio.CancelledError:
                return
            try:
                await self._apply(guild_id, user_id, wanted)
            except asyncio.CancelledError:
                return
            except Exception as e:
                log(f"Failed to apply activity roles for user {user_id} in guild {guild_id}: {e}",
                    level=logging.ERROR, module_name=MODULE)
            finally:
                queue.task_done()

    async def _apply(self, guild_id: int, user_id: int, wanted: frozenset[int]):
        guild = self.bot.get_guild(guild_id)
        if guild is None or not self._guild_enabled(guild_id):
            return
        if guild.me is None or not guild.me.guild_permissions.manage_roles:
            return

        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return
        if member.bot and get_server_config(guild_id, IGNORE_BOTS_KEY, True):
            return

        mappings = get_mappings(guild_id)
        domain = managed_role_ids(mappings)
        if not domain:
            return

        mode = get_server_config(guild_id, MODE_KEY, MODE_LIVE)
        current = {r.id for r in member.roles}
        to_add = [rid for rid in wanted if rid not in current]
        to_remove = [] if mode == MODE_STICKY else [rid for rid in (domain - wanted) if rid in current]

        # remove_after：停止活動後延後收回，大幅減少 alt-tab 造成的抖動
        try:
            remove_after = int(get_server_config(guild_id, REMOVE_AFTER_KEY, 0) or 0)
        except (TypeError, ValueError):
            remove_after = 0

        if to_remove and remove_after > 0:
            deadline = time.time() + remove_after * 60
            for rid in to_remove:
                self._pending_removals.setdefault((guild_id, user_id, rid), deadline)
            to_remove = []

        # 重新拿到身分組就取消排定中的收回
        for rid in wanted:
            self._pending_removals.pop((guild_id, user_id, rid), None)

        added = await self._add_roles(member, to_add)
        removed = await self._remove_roles(member, to_remove)

        if added or removed:
            log(f"Activity roles updated for {member}: +{len(added)} -{len(removed)}",
                module_name=MODULE, guild=guild, user=member)
            await self._send_log(guild, member, added, removed)

    def _assignable(self, guild: discord.Guild, role_id: int) -> discord.Role | None:
        role = guild.get_role(int(role_id))
        if role is None or role.is_default() or role.managed:
            return None
        if role >= guild.me.top_role:
            return None
        return role

    async def _add_roles(self, member: discord.Member, role_ids) -> list[int]:
        roles = [r for r in (self._assignable(member.guild, rid) for rid in role_ids) if r]
        if not roles:
            return []
        reason = t("activityrole.audit.add", locale=i18n.resolve_locale(guild_id=member.guild.id))
        try:
            await member.add_roles(*roles, reason=reason)
            return [r.id for r in roles]
        except discord.Forbidden:
            log(f"Couldn't add activity roles for {member} (insufficient permissions)",
                level=logging.WARNING, module_name=MODULE, guild=member.guild, user=member)
        except discord.HTTPException as e:
            log(f"Error adding activity roles for {member}: {e}",
                level=logging.ERROR, module_name=MODULE, guild=member.guild, user=member)
        return []

    async def _remove_roles(self, member: discord.Member, role_ids) -> list[int]:
        roles = [r for r in (self._assignable(member.guild, rid) for rid in role_ids) if r]
        if not roles:
            return []
        reason = t("activityrole.audit.remove", locale=i18n.resolve_locale(guild_id=member.guild.id))
        try:
            await member.remove_roles(*roles, reason=reason)
            return [r.id for r in roles]
        except discord.Forbidden:
            log(f"Couldn't remove activity roles for {member} (insufficient permissions)",
                level=logging.WARNING, module_name=MODULE, guild=member.guild, user=member)
        except discord.HTTPException as e:
            log(f"Error removing activity roles for {member}: {e}",
                level=logging.ERROR, module_name=MODULE, guild=member.guild, user=member)
        return []

    @tasks.loop(seconds=60)
    async def process_pending_removals(self):
        now = time.time()
        due = [k for k, deadline in self._pending_removals.items() if deadline <= now]
        for key in due:
            self._pending_removals.pop(key, None)
            guild_id, user_id, role_id = key
            if not self._guild_enabled(guild_id):
                continue
            if get_server_config(guild_id, MODE_KEY, MODE_LIVE) == MODE_STICKY:
                continue
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                continue
            member = guild.get_member(user_id)
            if member is None:
                continue
            await self._remove_roles(member, [role_id])

    @process_pending_removals.before_loop
    async def before_process_pending_removals(self):
        await self.bot.wait_until_ready()

    # ---- 開機對帳 ----

    async def reconcile_all(self):
        """bot 離線期間錯過的事件會讓身分組永久卡住，開機時對帳一次。

        只對有啟用的伺服器做，所以量可控；chunk_guilds_at_startup=False 的情況下
        這是唯一能拿到 presence 的方式。
        """
        if not PRESENCE_INTENT_ON:
            return
        self.refresh_enabled_guilds()
        for guild_id in list(self._enabled_guilds):
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                continue
            mappings = get_mappings(guild_id)
            if not mappings:
                continue
            try:
                if not guild.chunked:
                    await guild.chunk(cache=True)
            except Exception as e:
                log(f"Failed to chunk guild {guild_id} for reconciliation: {e}",
                    level=logging.WARNING, module_name=MODULE)
                continue
            domain = managed_role_ids(mappings)
            ignore_bots = get_server_config(guild_id, IGNORE_BOTS_KEY, True)
            for member in guild.members:
                if member.bot and ignore_bots:
                    continue
                signals = activity_signals(member.activities)
                wanted = wanted_role_ids(mappings, signals)
                current = {r.id for r in member.roles}
                # 只有真的需要變動才排進佇列
                if (wanted - current) or ((domain - wanted) & current):
                    self._last_wanted[(guild_id, member.id)] = wanted
                    await self._enqueue(guild_id, member.id, wanted)
            await asyncio.sleep(RECONCILE_STAGGER_SECONDS)
        log("Activity role reconciliation finished", module_name=MODULE)

    # ---- 日誌 ----

    async def _send_log(self, guild: discord.Guild, member: discord.Member,
                        added: list[int], removed: list[int]):
        channel_id = get_server_config(guild.id, LOG_CHANNEL_KEY)
        if not channel_id:
            return
        channel = guild.get_channel(int(channel_id))
        if channel is None:
            return
        locale = i18n.resolve_locale(guild_id=guild.id)

        def mentions(ids):
            out = []
            for rid in ids:
                role = guild.get_role(rid)
                out.append(role.mention if role else f"`{rid}`")
            return i18n.join_list(out, locale=locale)

        embed = discord.Embed(
            title=t("activityrole.log.title", locale=locale),
            description=t("activityrole.log.updated", locale=locale, user=member.mention),
            color=0x5865F2,
        )
        if added:
            embed.add_field(name=t("activityrole.field.added", locale=locale),
                            value=mentions(added), inline=False)
        if removed:
            embed.add_field(name=t("activityrole.field.removed", locale=locale),
                            value=mentions(removed), inline=False)
        embed.set_footer(text=t("activityrole.log.footer", locale=locale, user_id=member.id))
        try:
            await channel.send(embed=embed)
        except Exception as e:
            log(f"Failed to send the ActivityRole log: {e}", level=logging.ERROR, module_name=MODULE, guild=guild)

    # ============= 自動完成 =============

    async def game_autocomplete(self, interaction: discord.Interaction,
                                current: str) -> list[app_commands.Choice[str]]:
        """先列本伺服器實際觀測到的活動，再列 detectable 搜尋結果。

        觀測清單放最前面是刻意的：detectable 只收錄 Discord 自動偵測的遊戲，
        小開發者的自製 app 不在裡面，但只要伺服器有人跑過就會出現在這裡。
        """
        locale = i18n.resolve_from_interaction(interaction)
        query = (current or "").strip()
        choices: list[app_commands.Choice[str]] = []
        used_values: set[str] = set()

        table = self._observed_for(interaction.guild_id) if interaction.guild_id else {}
        observed = sorted(
            (e for e in table.values() if isinstance(e, dict) and e.get("n")),
            key=lambda e: float(e.get("t", 0)),
            reverse=True,
        )
        for entry in observed:
            name = str(entry["n"])
            if query and query.casefold() not in name.casefold():
                continue
            app_id = entry.get("a")
            value = f"a:{app_id}" if app_id else f"n:{name}"
            if len(value) > 100 or value in used_values:
                continue
            label = t("activityrole.ac.observed", locale=locale, name=name)
            choices.append(app_commands.Choice(name=label[:100], value=value))
            used_values.add(value)
            if len(choices) >= 10:
                break

        if query:
            total = games.match_count(query)
            if total > 1:
                value = f"all:{query}"[:100]
                if value not in used_values:
                    label = t("activityrole.ac.all_variants", locale=locale, query=query, count=total)
                    choices.append(app_commands.Choice(name=label[:100], value=value))
                    used_values.add(value)
            for app_id, name in games.search(query, limit=AUTOCOMPLETE_LIMIT):
                value = f"a:{app_id}"
                if value in used_values:
                    continue
                choices.append(app_commands.Choice(name=name[:100], value=value))
                used_values.add(value)
                if len(choices) >= AUTOCOMPLETE_LIMIT:
                    break

        return choices[:AUTOCOMPLETE_LIMIT]

    async def entry_autocomplete(self, interaction: discord.Interaction,
                                 current: str) -> list[app_commands.Choice[str]]:
        """列出指定身分組目前的比對條目，供移除。"""
        locale = i18n.resolve_from_interaction(interaction)
        role = getattr(interaction.namespace, "role", None)
        if role is None or not interaction.guild_id:
            return []
        mapping = get_mapping_for_role(get_mappings(interaction.guild_id), role.id)
        if mapping is None:
            return []
        query = (current or "").strip().casefold()
        out: list[app_commands.Choice[str]] = []
        for app_id in mapping["app_ids"]:
            name = games.name_for(app_id) or str(app_id)
            label = t("activityrole.ac.entry_app", locale=locale, name=name, app_id=app_id)
            if query and query not in label.casefold():
                continue
            out.append(app_commands.Choice(name=label[:100], value=f"a:{app_id}"))
        for name in mapping["names"]:
            label = t("activityrole.ac.entry_name", locale=locale, name=name)
            if query and query not in label.casefold():
                continue
            out.append(app_commands.Choice(name=label[:100], value=f"n:{name}"[:100]))
        for pattern in mapping["patterns"]:
            label = t("activityrole.ac.entry_pattern", locale=locale, pattern=pattern)
            if query and query not in label.casefold():
                continue
            out.append(app_commands.Choice(name=label[:100], value=f"p:{pattern}"[:100]))
        return out[:AUTOCOMPLETE_LIMIT]

    # ============= 共用檢查 =============

    def _role_problem(self, interaction: discord.Interaction, role: discord.Role) -> str | None:
        """回傳身分組不可用的原因 key，可用則回 None。"""
        guild = interaction.guild
        if role.is_default():
            return "activityrole.err.role_everyone"
        if role.managed:
            return "activityrole.err.role_managed"
        if guild.me is None or role >= guild.me.top_role:
            return "activityrole.err.role_too_high"
        return None

    def _ensure_mapping(self, mappings: list[dict], role_id: int) -> dict:
        mapping = get_mapping_for_role(mappings, role_id)
        if mapping is None:
            mapping = {
                "role_id": int(role_id),
                "app_ids": [],
                "names": [],
                "patterns": [],
                "types": list(DEFAULT_TYPES),
            }
            mappings.append(mapping)
        return mapping

    # ============= 設定指令 =============

    @app_commands.command(
        name=app_commands.locale_str("toggle", i18n_key="cmd.activityrole.activity_role.toggle.name"),
        description=app_commands.locale_str("Enable or disable activity roles",
                                            i18n_key="cmd.activityrole.activity_role.toggle.desc"))
    @app_commands.describe(enable=app_commands.locale_str(
        "Whether to enable activity roles", i18n_key="cmd.activityrole.activity_role.toggle.param.enable"))
    @app_commands.choices(enable=[
        app_commands.Choice(name=app_commands.locale_str(
            "Enable", i18n_key="cmd.activityrole.activity_role.toggle.choice.true"), value="True"),
        app_commands.Choice(name=app_commands.locale_str(
            "Disable", i18n_key="cmd.activityrole.activity_role.toggle.choice.false"), value="False"),
    ])
    @app_commands.checks.has_permissions(administrator=True)
    async def toggle(self, interaction: discord.Interaction, enable: str):
        enabled = (enable == "True")
        if enabled and not interaction.guild.me.guild_permissions.manage_roles:
            await interaction.response.send_message(t("activityrole.err.missing_manage_roles"), ephemeral=True)
            return
        set_server_config(interaction.guild.id, ENABLED_KEY, enabled)
        self.refresh_enabled_guilds()
        log(f"ActivityRole {'enabled' if enabled else 'disabled'}",
            module_name=MODULE, guild=interaction.guild, user=interaction.user)

        message = t("activityrole.msg.enabled" if enabled else "activityrole.msg.disabled")
        if enabled and not PRESENCE_INTENT_ON:
            message += "\n" + t("activityrole.warn.no_presence_intent")
        await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(
        name=app_commands.locale_str("mode", i18n_key="cmd.activityrole.activity_role.mode.name"),
        description=app_commands.locale_str("Choose whether roles are taken back when the activity stops",
                                            i18n_key="cmd.activityrole.activity_role.mode.desc"))
    @app_commands.describe(mode=app_commands.locale_str(
        "live: remove when the activity stops / sticky: keep it forever",
        i18n_key="cmd.activityrole.activity_role.mode.param.mode"))
    @app_commands.choices(mode=[
        app_commands.Choice(name=app_commands.locale_str(
            "Live (remove when stopped)", i18n_key="cmd.activityrole.activity_role.mode.choice.live"), value=MODE_LIVE),
        app_commands.Choice(name=app_commands.locale_str(
            "Sticky (keep once earned)", i18n_key="cmd.activityrole.activity_role.mode.choice.sticky"), value=MODE_STICKY),
    ])
    @app_commands.checks.has_permissions(administrator=True)
    async def set_mode(self, interaction: discord.Interaction, mode: str):
        if mode not in (MODE_LIVE, MODE_STICKY):
            mode = MODE_LIVE
        set_server_config(interaction.guild.id, MODE_KEY, mode)
        await interaction.response.send_message(
            t(f"activityrole.msg.mode_set_{mode}"), ephemeral=True)

    @app_commands.command(
        name=app_commands.locale_str("remove-after", i18n_key="cmd.activityrole.activity_role.remove_after.name"),
        description=app_commands.locale_str("Delay before a role is taken back after the activity stops",
                                            i18n_key="cmd.activityrole.activity_role.remove_after.desc"))
    @app_commands.describe(minutes=app_commands.locale_str(
        "0 means remove immediately; a delay greatly reduces flapping from alt-tabbing",
        i18n_key="cmd.activityrole.activity_role.remove_after.param.minutes"))
    @app_commands.checks.has_permissions(administrator=True)
    async def set_remove_after(self, interaction: discord.Interaction,
                               minutes: app_commands.Range[int, 0, 1440]):
        set_server_config(interaction.guild.id, REMOVE_AFTER_KEY, int(minutes))
        await interaction.response.send_message(
            t("activityrole.msg.remove_after_set", minutes=int(minutes)), ephemeral=True)

    @app_commands.command(
        name=app_commands.locale_str("set-log-channel",
                                     i18n_key="cmd.activityrole.activity_role.set_log_channel.name"),
        description=app_commands.locale_str("Set the channel that logs activity role changes",
                                            i18n_key="cmd.activityrole.activity_role.set_log_channel.desc"))
    @app_commands.describe(channel=app_commands.locale_str(
        "Leave blank to clear the setting", i18n_key="cmd.activityrole.activity_role.set_log_channel.param.channel"))
    @app_commands.checks.has_permissions(administrator=True)
    async def set_log_channel(self, interaction: discord.Interaction,
                              channel: discord.TextChannel = None):
        if channel is None:
            set_server_config(interaction.guild.id, LOG_CHANNEL_KEY, None)
            await interaction.response.send_message(t("activityrole.msg.log_channel_cleared"), ephemeral=True)
            return
        perms = channel.permissions_for(interaction.guild.me)
        if not (perms.view_channel and perms.send_messages):
            await interaction.response.send_message(
                t("activityrole.err.log_channel_perms", channel=channel.mention), ephemeral=True)
            return
        set_server_config(interaction.guild.id, LOG_CHANNEL_KEY, channel.id)
        await interaction.response.send_message(
            t("activityrole.msg.log_channel_set", channel=channel.mention), ephemeral=True)

    @app_commands.command(
        name=app_commands.locale_str("types", i18n_key="cmd.activityrole.activity_role.types.name"),
        description=app_commands.locale_str("Choose which activity types count for a role",
                                            i18n_key="cmd.activityrole.activity_role.types.desc"))
    @app_commands.describe(
        role=app_commands.locale_str("The role to configure",
                                     i18n_key="cmd.activityrole.activity_role.types.param.role"),
        playing=app_commands.locale_str("Playing a game (default on)",
                                        i18n_key="cmd.activityrole.activity_role.types.param.playing"),
        streaming=app_commands.locale_str("Streaming",
                                          i18n_key="cmd.activityrole.activity_role.types.param.streaming"),
        listening=app_commands.locale_str("Listening (e.g. Spotify)",
                                          i18n_key="cmd.activityrole.activity_role.types.param.listening"),
        watching=app_commands.locale_str("Watching",
                                         i18n_key="cmd.activityrole.activity_role.types.param.watching"),
        competing=app_commands.locale_str("Competing",
                                          i18n_key="cmd.activityrole.activity_role.types.param.competing"))
    @app_commands.checks.has_permissions(administrator=True)
    async def set_types(self, interaction: discord.Interaction, role: discord.Role,
                        playing: bool = None, streaming: bool = None, listening: bool = None,
                        watching: bool = None, competing: bool = None):
        mappings = get_mappings(interaction.guild.id)
        mapping = get_mapping_for_role(mappings, role.id)
        if mapping is None:
            await interaction.response.send_message(
                t("activityrole.err.no_mapping", role=role.mention), ephemeral=True)
            return

        wanted = set(mapping["types"])
        for name, flag in (("playing", playing), ("streaming", streaming), ("listening", listening),
                           ("watching", watching), ("competing", competing)):
            if flag is None:
                continue
            if flag:
                wanted.add(name)
            else:
                wanted.discard(name)

        if not wanted:
            await interaction.response.send_message(t("activityrole.err.types_empty"), ephemeral=True)
            return

        mapping["types"] = sorted(wanted)
        set_mappings(interaction.guild.id, mappings)
        await interaction.response.send_message(
            t("activityrole.msg.types_set", role=role.mention,
              types=i18n.join_list(sorted(wanted))), ephemeral=True)

    # ============= 映射指令 =============

    @app_commands.command(
        name=app_commands.locale_str("add", i18n_key="cmd.activityrole.activity_role.add.name"),
        description=app_commands.locale_str("Map an activity to a role",
                                            i18n_key="cmd.activityrole.activity_role.add.desc"))
    @app_commands.describe(
        role=app_commands.locale_str("The role to grant",
                                     i18n_key="cmd.activityrole.activity_role.add.param.role"),
        game=app_commands.locale_str("Pick from the list, or type an activity name directly",
                                     i18n_key="cmd.activityrole.activity_role.add.param.game"))
    @app_commands.autocomplete(game=game_autocomplete)
    @app_commands.checks.has_permissions(administrator=True)
    async def add(self, interaction: discord.Interaction, role: discord.Role, game: str):
        problem = self._role_problem(interaction, role)
        if problem:
            await interaction.response.send_message(t(problem, role=role.mention), ephemeral=True)
            return

        mappings = get_mappings(interaction.guild.id)
        mapping = self._ensure_mapping(mappings, role.id)
        added_labels: list[str] = []

        if game.startswith("all:"):
            query = game[4:]
            ids = games.all_matching_ids(query)
            if not ids:
                await interaction.response.send_message(
                    t("activityrole.err.no_match", query=query), ephemeral=True)
                return
            existing = set(mapping["app_ids"])
            for app_id in ids:
                if app_id not in existing:
                    mapping["app_ids"].append(app_id)
                    existing.add(app_id)
                    added_labels.append(games.name_for(app_id) or str(app_id))
        elif game.startswith("a:"):
            try:
                app_id = int(game[2:])
            except ValueError:
                await interaction.response.send_message(t("activityrole.err.bad_app_id"), ephemeral=True)
                return
            if app_id not in mapping["app_ids"]:
                mapping["app_ids"].append(app_id)
                added_labels.append(games.name_for(app_id) or str(app_id))
        else:
            # n:<name> 或使用者直接打的文字
            name = game[2:] if game.startswith("n:") else game
            name = name.strip()
            if not name:
                await interaction.response.send_message(t("activityrole.err.empty_name"), ephemeral=True)
                return
            if name not in mapping["names"]:
                mapping["names"].append(name)
                added_labels.append(name)

        if not added_labels:
            await interaction.response.send_message(t("activityrole.err.already_mapped"), ephemeral=True)
            return

        mapping["app_ids"] = sorted(set(mapping["app_ids"]))
        mapping["names"] = sorted(set(mapping["names"]))
        set_mappings(interaction.guild.id, mappings)
        log(f"Mapped {len(added_labels)} activity entr(ies) to role {role.id}",
            module_name=MODULE, guild=interaction.guild, user=interaction.user)

        await interaction.response.send_message(
            t("activityrole.msg.added", role=role.mention, count=len(added_labels),
              entries=i18n.join_list(added_labels[:10])), ephemeral=True)

    @app_commands.command(
        name=app_commands.locale_str("add-custom", i18n_key="cmd.activityrole.activity_role.add_custom.name"),
        description=app_commands.locale_str("Map a role by raw application ID (for apps not in the game list)",
                                            i18n_key="cmd.activityrole.activity_role.add_custom.desc"))
    @app_commands.describe(
        role=app_commands.locale_str("The role to grant",
                                     i18n_key="cmd.activityrole.activity_role.add_custom.param.role"),
        app_id=app_commands.locale_str("The application ID shown in the activity",
                                       i18n_key="cmd.activityrole.activity_role.add_custom.param.app_id"))
    @app_commands.checks.has_permissions(administrator=True)
    async def add_custom(self, interaction: discord.Interaction, role: discord.Role, app_id: str):
        problem = self._role_problem(interaction, role)
        if problem:
            await interaction.response.send_message(t(problem, role=role.mention), ephemeral=True)
            return
        try:
            parsed = int(app_id.strip())
        except ValueError:
            await interaction.response.send_message(t("activityrole.err.bad_app_id"), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        name = await resolve_app_name(parsed)

        mappings = get_mappings(interaction.guild.id)
        mapping = self._ensure_mapping(mappings, role.id)
        if parsed in mapping["app_ids"]:
            await interaction.followup.send(t("activityrole.err.already_mapped"), ephemeral=True)
            return
        mapping["app_ids"] = sorted(set(mapping["app_ids"] + [parsed]))
        set_mappings(interaction.guild.id, mappings)

        if name:
            message = t("activityrole.msg.added_custom", role=role.mention, name=name, app_id=parsed)
        else:
            message = t("activityrole.msg.added_custom_unverified", role=role.mention, app_id=parsed)
        await interaction.followup.send(message, ephemeral=True)

    @app_commands.command(
        name=app_commands.locale_str("add-pattern", i18n_key="cmd.activityrole.activity_role.add_pattern.name"),
        description=app_commands.locale_str("Map a role by a loose name match",
                                            i18n_key="cmd.activityrole.activity_role.add_pattern.desc"))
    @app_commands.describe(
        role=app_commands.locale_str("The role to grant",
                                     i18n_key="cmd.activityrole.activity_role.add_pattern.param.role"),
        pattern=app_commands.locale_str("Grants the role when the activity name contains this text",
                                        i18n_key="cmd.activityrole.activity_role.add_pattern.param.pattern"))
    @app_commands.checks.has_permissions(administrator=True)
    async def add_pattern(self, interaction: discord.Interaction, role: discord.Role, pattern: str):
        problem = self._role_problem(interaction, role)
        if problem:
            await interaction.response.send_message(t(problem, role=role.mention), ephemeral=True)
            return
        pattern = pattern.strip()
        if len(pattern) < 3:
            await interaction.response.send_message(t("activityrole.err.pattern_too_short"), ephemeral=True)
            return

        mappings = get_mappings(interaction.guild.id)
        mapping = self._ensure_mapping(mappings, role.id)
        if pattern in mapping["patterns"]:
            await interaction.response.send_message(t("activityrole.err.already_mapped"), ephemeral=True)
            return
        mapping["patterns"] = sorted(set(mapping["patterns"] + [pattern]))
        set_mappings(interaction.guild.id, mappings)
        await interaction.response.send_message(
            t("activityrole.msg.added_pattern", role=role.mention, pattern=pattern), ephemeral=True)

    @app_commands.command(
        name=app_commands.locale_str("remove-entry", i18n_key="cmd.activityrole.activity_role.remove_entry.name"),
        description=app_commands.locale_str("Remove a single match entry from a role",
                                            i18n_key="cmd.activityrole.activity_role.remove_entry.desc"))
    @app_commands.describe(
        role=app_commands.locale_str("The role to edit",
                                     i18n_key="cmd.activityrole.activity_role.remove_entry.param.role"),
        entry=app_commands.locale_str("The entry to remove",
                                      i18n_key="cmd.activityrole.activity_role.remove_entry.param.entry"))
    @app_commands.autocomplete(entry=entry_autocomplete)
    @app_commands.checks.has_permissions(administrator=True)
    async def remove_entry(self, interaction: discord.Interaction, role: discord.Role, entry: str):
        mappings = get_mappings(interaction.guild.id)
        mapping = get_mapping_for_role(mappings, role.id)
        if mapping is None:
            await interaction.response.send_message(
                t("activityrole.err.no_mapping", role=role.mention), ephemeral=True)
            return

        removed = False
        if entry.startswith("a:"):
            try:
                app_id = int(entry[2:])
            except ValueError:
                app_id = None
            if app_id is not None and app_id in mapping["app_ids"]:
                mapping["app_ids"].remove(app_id)
                removed = True
        elif entry.startswith("n:"):
            name = entry[2:]
            if name in mapping["names"]:
                mapping["names"].remove(name)
                removed = True
        elif entry.startswith("p:"):
            pattern = entry[2:]
            if pattern in mapping["patterns"]:
                mapping["patterns"].remove(pattern)
                removed = True

        if not removed:
            await interaction.response.send_message(t("activityrole.err.entry_not_found"), ephemeral=True)
            return

        if mapping_is_empty(mapping):
            mappings = [m for m in mappings if m["role_id"] != role.id]
        set_mappings(interaction.guild.id, mappings)
        await interaction.response.send_message(
            t("activityrole.msg.entry_removed", role=role.mention), ephemeral=True)

    @app_commands.command(
        name=app_commands.locale_str("remove", i18n_key="cmd.activityrole.activity_role.remove.name"),
        description=app_commands.locale_str("Remove every match entry for a role",
                                            i18n_key="cmd.activityrole.activity_role.remove.desc"))
    @app_commands.describe(role=app_commands.locale_str(
        "The role to unmap", i18n_key="cmd.activityrole.activity_role.remove.param.role"))
    @app_commands.checks.has_permissions(administrator=True)
    async def remove(self, interaction: discord.Interaction, role: discord.Role):
        mappings = get_mappings(interaction.guild.id)
        if get_mapping_for_role(mappings, role.id) is None:
            await interaction.response.send_message(
                t("activityrole.err.no_mapping", role=role.mention), ephemeral=True)
            return
        set_mappings(interaction.guild.id, [m for m in mappings if m["role_id"] != role.id])
        await interaction.response.send_message(
            t("activityrole.msg.removed", role=role.mention), ephemeral=True)

    # ============= 檢視指令 =============

    @app_commands.command(
        name=app_commands.locale_str("list", i18n_key="cmd.activityrole.activity_role.list.name"),
        description=app_commands.locale_str("View the current activity role configuration",
                                            i18n_key="cmd.activityrole.activity_role.list.desc"))
    @app_commands.checks.has_permissions(administrator=True)
    async def list_config(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        mappings = get_mappings(guild_id)
        enabled = bool(get_server_config(guild_id, ENABLED_KEY, False))
        mode = get_server_config(guild_id, MODE_KEY, MODE_LIVE)
        try:
            remove_after = int(get_server_config(guild_id, REMOVE_AFTER_KEY, 0) or 0)
        except (TypeError, ValueError):
            remove_after = 0

        embed = discord.Embed(title=t("activityrole.config.title"), color=0x5865F2)
        embed.add_field(name=t("activityrole.field.status"),
                        value=t("activityrole.value.enabled" if enabled else "activityrole.value.disabled"),
                        inline=True)
        embed.add_field(name=t("activityrole.field.mode"),
                        value=t(f"activityrole.value.mode_{mode}"), inline=True)
        embed.add_field(name=t("activityrole.field.remove_after"),
                        value=t("activityrole.value.remove_after", minutes=remove_after)
                        if remove_after else t("activityrole.value.remove_immediately"),
                        inline=True)

        if not mappings:
            embed.description = t("activityrole.config.no_mappings")
        else:
            lines = []
            for mapping in mappings[:20]:
                role = interaction.guild.get_role(mapping["role_id"])
                role_text = role.mention if role else t("activityrole.config.deleted_role",
                                                        role_id=mapping["role_id"])
                parts = []
                if mapping["app_ids"]:
                    shown = [games.name_for(a) or str(a) for a in mapping["app_ids"][:4]]
                    extra = len(mapping["app_ids"]) - len(shown)
                    text = i18n.join_list(shown)
                    if extra > 0:
                        text += t("activityrole.config.and_more", count=extra)
                    parts.append(t("activityrole.config.part_games", value=text))
                if mapping["names"]:
                    parts.append(t("activityrole.config.part_names",
                                   value=i18n.join_list(mapping["names"][:4])))
                if mapping["patterns"]:
                    parts.append(t("activityrole.config.part_patterns",
                                   value=i18n.join_list([f"`{p}`" for p in mapping["patterns"][:4]])))
                parts.append(t("activityrole.config.part_types",
                               value=i18n.join_list(mapping["types"])))
                lines.append(f"{role_text}\n　" + "\n　".join(parts))
            embed.description = "\n\n".join(lines)[:4000]

        if enabled and not PRESENCE_INTENT_ON:
            embed.add_field(name=t("activityrole.field.warning"),
                            value=t("activityrole.warn.no_presence_intent"), inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name=app_commands.locale_str("observed", i18n_key="cmd.activityrole.activity_role.observed.name"),
        description=app_commands.locale_str("View activities recently seen in this server",
                                            i18n_key="cmd.activityrole.activity_role.observed.desc"))
    @app_commands.checks.has_permissions(administrator=True)
    async def observed(self, interaction: discord.Interaction):
        table = self._observed_for(interaction.guild.id)
        entries = sorted(
            (e for e in table.values() if isinstance(e, dict) and e.get("n")),
            key=lambda e: float(e.get("t", 0)),
            reverse=True,
        )
        embed = discord.Embed(title=t("activityrole.observed.title"), color=0x5865F2)
        if not entries:
            embed.description = t("activityrole.observed.empty")
            if not PRESENCE_INTENT_ON:
                embed.description += "\n" + t("activityrole.warn.no_presence_intent")
        else:
            lines = []
            for entry in entries[:25]:
                app_id = entry.get("a")
                in_list = bool(app_id and games.name_for(int(app_id)))
                lines.append(t(
                    "activityrole.observed.row",
                    name=str(entry["n"]),
                    count=int(entry.get("c", 0)),
                    when=i18n.fmt_ts(float(entry.get("t", 0)), "R"),
                    source=t("activityrole.observed.src_detectable" if in_list
                             else "activityrole.observed.src_custom"),
                    app_id=app_id or "-",
                ))
            embed.description = "\n".join(lines)[:4000]
            embed.set_footer(text=t("activityrole.observed.footer"))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name=app_commands.locale_str("status", i18n_key="cmd.activityrole.activity_role.status.name"),
        description=app_commands.locale_str("Diagnostics for the activity role system",
                                            i18n_key="cmd.activityrole.activity_role.status.desc"))
    @app_commands.checks.has_permissions(administrator=True)
    async def status(self, interaction: discord.Interaction):
        raw_flag = getattr(getattr(self.bot, "_connection", None), "raw_presence_flag", None)
        fetched_at = get_global_config(GAMES_FETCHED_AT_KEY, 0) or 0

        embed = discord.Embed(title=t("activityrole.status.title"), color=0x5865F2)
        embed.add_field(
            name=t("activityrole.status.intent"),
            value=t("activityrole.value.on" if PRESENCE_INTENT_ON else "activityrole.value.off"),
            inline=True)
        embed.add_field(
            name=t("activityrole.status.raw_event"),
            value=t("activityrole.value.on" if raw_flag else "activityrole.value.off"),
            inline=True)
        embed.add_field(name=t("activityrole.status.enabled_guilds"),
                        value=str(len(self._enabled_guilds)), inline=True)
        embed.add_field(name=t("activityrole.status.games_index"),
                        value=t("activityrole.status.games_count", count=len(games.rows))
                        if games.loaded else t("activityrole.status.games_missing"),
                        inline=True)
        if fetched_at:
            embed.add_field(name=t("activityrole.status.games_updated"),
                            value=i18n.fmt_ts(float(fetched_at), "R"), inline=True)
        embed.add_field(name=t("activityrole.status.queue"),
                        value=t("activityrole.status.queue_value",
                                workers=len(self._workers),
                                pending=sum(q.qsize() for q in self._queues.values()),
                                debounce=len(self._debounce),
                                removals=len(self._pending_removals)),
                        inline=False)

        if PRESENCE_INTENT_ON and not raw_flag:
            embed.add_field(name=t("activityrole.field.warning"),
                            value=t("activityrole.warn.raw_disabled"), inline=False)
        if not PRESENCE_INTENT_ON:
            embed.add_field(name=t("activityrole.field.warning"),
                            value=t("activityrole.warn.no_presence_intent"), inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name=app_commands.locale_str("refresh-games", i18n_key="cmd.activityrole.activity_role.refresh_games.name"),
        description=app_commands.locale_str("Force a refresh of the detectable game list",
                                            i18n_key="cmd.activityrole.activity_role.refresh_games.desc"))
    @app_commands.checks.has_permissions(administrator=True)
    async def refresh_games(self, interaction: discord.Interaction):
        fetched_at = float(get_global_config(GAMES_FETCHED_AT_KEY, 0) or 0)
        # 這是共用的全域快取，而且來源是免 auth 的端點（rate limit 綁 IP），
        # 所以任何伺服器的管理員都只能在冷卻結束後才觸發。
        if fetched_at and (time.time() - fetched_at) < 3600:
            await interaction.response.send_message(
                t("activityrole.err.refresh_cooldown",
                  when=i18n.fmt_ts(fetched_at + 3600, "R")), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        updated = await games.refresh(force=True)
        await interaction.followup.send(
            t("activityrole.msg.games_refreshed", count=len(games.rows)) if updated
            else t("activityrole.err.refresh_failed"), ephemeral=True)


# ============= 面板設定 =============

def _on_enabled_changed(guild_id: int, enabled):
    """面板改動不會經過指令，所以要在這裡同步啟用清單。"""
    cog = bot.get_cog("ActivityRole")
    if cog is not None:
        cog.refresh_enabled_guilds()


register_panel_settings(
    "ActivityRole",
    "Activity Roles",
    [
        {
            "display": "Enable activity roles",
            "description": "Automatically grant roles based on the game or app members are running (requires the Presence intent)",
            "database_key": ENABLED_KEY,
            "type": "boolean",
            "default": False,
            "trigger": _on_enabled_changed,
        },
        {
            "display": "Mode",
            "description": "live takes the role back when the activity stops; sticky keeps it once earned",
            "database_key": MODE_KEY,
            "type": "select",
            "default": MODE_LIVE,
            "options": [
                {"label": "Live (remove when stopped)", "value": MODE_LIVE},
                {"label": "Sticky (keep once earned)", "value": MODE_STICKY},
            ],
        },
        {
            "display": "Removal delay (minutes)",
            "description": "Wait this long after the activity stops before taking the role back; a delay reduces flapping from alt-tabbing",
            "database_key": REMOVE_AFTER_KEY,
            "type": "number",
            "default": 0,
            "min": 0,
            "max": 1440,
        },
        {
            "display": "Ignore bot accounts",
            "database_key": IGNORE_BOTS_KEY,
            "type": "boolean",
            "default": True,
        },
        {
            "display": "Log channel",
            "description": "Where to report activity role changes",
            "database_key": LOG_CHANNEL_KEY,
            "type": "channel",
            "default": None,
        },
    ],
    description="Grant roles automatically based on what members are playing (mappings are managed with /activity-role)",
    icon="🎮",
)


# ============= 啟動 =============

async def _startup():
    """載入遊戲清單，然後對帳一次。"""
    if not games.load_from_disk():
        await games.refresh(force=True)
    else:
        await games.refresh()  # 過期才會真的重抓

    if not PRESENCE_INTENT_ON:
        log("Presence intent is off: activity roles are configurable but inactive. "
            "Enable Guild Presences in the Developer Portal to activate it.",
            level=logging.WARNING, module_name=MODULE)
        return

    raw_flag = getattr(getattr(bot, "_connection", None), "raw_presence_flag", None)
    if not raw_flag:
        log("on_raw_presence_update is disabled (it defaults off when the members intent is on). "
            "Pass enable_raw_presences=True to the Bot constructor, or activity roles will only "
            "update for members that are already cached.",
            level=logging.WARNING, module_name=MODULE)

    cog = bot.get_cog("ActivityRole")
    if cog is not None:
        await cog.reconcile_all()


on_ready_tasks.append(_startup)

asyncio.run(bot.add_cog(ActivityRole(bot)))

if __name__ == "__main__":
    from globalenv import start_bot
    start_bot()
