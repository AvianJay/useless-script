"""i18n 核心模組。

繁體中文 (zh-TW) 為原文語言，其他語言（目前只有 en）透過 locales/ 下的
JSON 語言檔提供翻譯。locale 透過 contextvars.ContextVar 傳遞，於六個
choke point 設定：

1. I18nCommandTree.interaction_check   — 斜線指令 / context menu / autocomplete
2. BaseView._scheduled_task wrapper    — 全部 button / select / item callback（含 LayoutView）
3. Modal._scheduled_task wrapper       — 全部 modal submit
4. Flask @app.before_request           — 網頁面板與 docs（Website.py）
5. BotBase.invoke wrapper              — 全部前綴文字指令（含 check 失敗與錯誤處理）
6. ViewStore.schedule_dynamic_item_call — discord.ui.DynamicItem 的 callback
   （它不經過 _scheduled_task，所以 2 蓋不到）

choke point 未覆蓋的進入點（on_message、on_member_join、背景迴圈）
需要顯式開 scope：

    async with i18n.guild_scope(message.guild.id, user_id=message.author.id):
        ...

解析優先序：t(locale=...) 顯式 > ContextVar > 使用者設定 > 伺服器設定
> interaction.locale / last_locale > zh-TW。

此模組不得 import globalenv（globalenv 會 import 本模組）。
"""
from __future__ import annotations

import contextlib
import contextvars
import functools
import json
import logging
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any, Iterable

import discord
from discord import app_commands
from discord.app_commands.translator import (
    TranslationContext,
    TranslationContextLocation,
    locale_str,
)

from database import db

SOURCE_LOCALE = "zh-TW"   # 語言檔的原文語言
DEFAULT_LOCALE = "zh-TW"  # 無任何訊號時的預設
LOCALES_DIR = Path(__file__).parent / "locales"

DEBUG_MODE = "--debug" in sys.argv
STRICT = os.environ.get("I18N_STRICT") == "1"      # CI / 測試：缺 key 直接 raise
TEST_KEYS = os.environ.get("I18N_TEST_KEYS") == "1"  # 測試：t() 直接回傳 key

USER_LOCALE_KEY = "user_locale"
GUILD_LOCALE_KEY = "guild_locale"
LAST_LOCALE_KEY = "last_locale"

_log = logging.getLogger("i18n")

# 語言檔中「_xxx.json」檔名 -> 命名空間的別名
_NAMESPACE_ALIASES = {
    "commands": "cmd",
    "errors": "err",
}

# 複數項的合法類別（值為 dict 且 key 是這些的子集合 => 複數項）
_PLURAL_KEYS = {"zero", "one", "two", "few", "many", "other"}

# 無複數變化的語言（一律用 other）
_NO_PLURAL_PREFIXES = ("zh", "ja", "ko", "th", "vi", "id", "ms")

# 指令 metadata 用的 Discord locale 對應（只列有實際內容的語言，
# 沒列的 locale 交給 Discord fallback 到英文原文；不做 catch-all，
# 否則會對 30 個 locale 全部上傳一份跟原文一樣的英文）
_METADATA_LOCALE_MAP = {
    "zh-TW": "zh-TW",
    "zh-CN": "zh-TW",  # 尚無 zh-CN 語言檔前先給繁中
    "en-US": "en",
    "en-GB": "en",
}

# Discord 指令名稱限制（近似 ^[-_'\p{L}\p{N}]{1,32}$，且必須小寫）
_DISCORD_NAME_RE = re.compile(r"^[-_'\w]{1,32}$", re.UNICODE)


class MissingTranslationError(KeyError):
    pass


# ============= 語言檔載入 =============

_catalogs: dict[str, dict[str, Any]] = {}
_resolved: dict[tuple[str, str], Any] = {}
_loaded = False
_load_lock = threading.Lock()
_dir_signature: tuple | None = None
_last_scan = 0.0
_missing_warned: set[str] = set()
_noscope_warned: set[str] = set()


def _flatten(prefix: str, node: dict, out: dict) -> None:
    for k, v in node.items():
        key = f"{prefix}.{k}" if prefix else str(k)
        if isinstance(v, dict):
            if v and set(v.keys()) <= _PLURAL_KEYS:
                out[key] = v  # 複數項整包保留
            else:
                _flatten(key, v, out)
        else:
            out[key] = v  # str 或 null（null = 尚未翻譯）


def _scan_signature() -> tuple:
    sig = []
    if LOCALES_DIR.exists():
        for path in sorted(LOCALES_DIR.rglob("*.json")):
            try:
                sig.append((str(path), path.stat().st_mtime_ns))
            except OSError:
                pass
    return tuple(sig)


def reload_catalogs() -> dict[str, int]:
    """重新載入全部語言檔。回傳 {locale: key 數}。"""
    global _catalogs, _loaded, _dir_signature
    with _load_lock:
        catalogs: dict[str, dict[str, Any]] = {}
        if LOCALES_DIR.exists():
            for entry in sorted(LOCALES_DIR.iterdir()):
                if entry.name.startswith("."):
                    continue  # .migrated / .coverage.json 等中繼檔
                if entry.is_file() and entry.suffix == ".json":
                    # 單檔形式 locales/<locale>.json
                    flat = catalogs.setdefault(entry.stem, {})
                    try:
                        data = json.loads(entry.read_text(encoding="utf-8-sig"))
                    except (OSError, ValueError) as e:
                        _log.error("Failed to load locale file %s: %s", entry, e)
                        continue
                    _flatten("", data, flat)
                elif entry.is_dir() and not entry.name.startswith("."):
                    # 目錄形式 locales/<locale>/<namespace>.json
                    flat = catalogs.setdefault(entry.name, {})
                    for f in sorted(entry.glob("*.json")):
                        ns = f.stem
                        if ns.startswith("_"):
                            ns = _NAMESPACE_ALIASES.get(ns[1:], ns[1:])
                        try:
                            data = json.loads(f.read_text(encoding="utf-8-sig"))
                        except (OSError, ValueError) as e:
                            _log.error("Failed to load locale file %s: %s", f, e)
                            continue
                        _flatten(ns, data, flat)
        _catalogs = catalogs
        _resolved.clear()
        _dir_signature = _scan_signature()
        _loaded = True
        return {loc: len(flat) for loc, flat in catalogs.items()}


def _hot_reload_check() -> None:
    global _last_scan
    now = time.monotonic()
    if now - _last_scan < 1.0:
        return
    _last_scan = now
    if _scan_signature() != _dir_signature:
        _log.info("Locale files changed, reloading catalogs.")
        reload_catalogs()


def ensure_loaded() -> None:
    if not _loaded:
        reload_catalogs()
    elif DEBUG_MODE:
        _hot_reload_check()


def available_locales() -> list[str]:
    ensure_loaded()
    locales = sorted(_catalogs.keys())
    if SOURCE_LOCALE in locales:  # 原文語言排最前
        locales.remove(SOURCE_LOCALE)
        locales.insert(0, SOURCE_LOCALE)
    return locales


_LOCALE_AUTONYMS = {
    # autonym，永不翻譯
    "zh-TW": "繁體中文",
    "en": "English",
}


def locale_display_name(locale: str) -> str:
    return _LOCALE_AUTONYMS.get(locale, locale)


def coverage() -> dict[str, dict]:
    """給 /owner i18n status 之類的診斷用。"""
    ensure_loaded()
    source = _catalogs.get(SOURCE_LOCALE, {})
    result = {}
    for loc, cat in _catalogs.items():
        translated = sum(1 for k in source if cat.get(k) is not None)
        result[loc] = {
            "keys": len(cat),
            "translated_of_source": translated,
            "source_total": len(source),
        }
    return result


def catalog_subset(prefixes: tuple[str, ...] | list[str],
                   locale: str | None = None) -> dict[str, Any]:
    """回傳指定前綴的扁平 {key: value} 子集（給前端 window.I18N 用）。

    以 source 的 key 集合為準，值取目標語言、缺則回退原文；
    複數項（dict）原樣傳遞，由 JS 端 t() 做選擇。
    """
    ensure_loaded()
    loc = locale or current_locale()
    source = _catalogs.get(SOURCE_LOCALE, {})
    target = _catalogs.get(loc, {})
    result: dict[str, Any] = {}
    for key, value in source.items():
        if any(key.startswith(prefix) for prefix in prefixes):
            target_value = target.get(key)
            result[key] = target_value if target_value is not None else value
    return result


# ============= 查表 =============

def _lookup_chain(key: str, locale: str) -> Any:
    """locale -> 基底語言 -> 原文語言，null 視為缺。"""
    ck = (locale, key)
    if ck in _resolved:
        return _resolved[ck]
    value = None
    chain = [locale]
    base = locale.split("-")[0]
    if base != locale:
        chain.append(base)
    if SOURCE_LOCALE not in chain:
        chain.append(SOURCE_LOCALE)
    for loc in chain:
        cat = _catalogs.get(loc)
        if cat is not None:
            v = cat.get(key)
            if v is not None:
                value = v
                break
    _resolved[ck] = value
    return value


def lookup(key: str, locale: str) -> Any:
    """原始查表：只查 locale 與其基底語言，不 fallback 到原文、不做格式化。

    給 CatalogTranslator 用——查不到回 None，discord.py 就保留原文。
    """
    ensure_loaded()
    for loc in (locale, locale.split("-")[0]):
        cat = _catalogs.get(loc)
        if cat is not None:
            v = cat.get(key)
            if v is not None:
                return v
    return None


def has_key(key: str, locale: str | None = None) -> bool:
    ensure_loaded()
    return _lookup_chain(key, locale or current_locale()) is not None


# ============= 複數 =============

def _plural_category(locale: str, count) -> str:
    if locale.split("-")[0] in _NO_PLURAL_PREFIXES:
        return "other"
    return "one" if count == 1 else "other"


def _select_plural(entry: dict, locale: str, count) -> str:
    if count == 0 and "zero" in entry:
        return entry["zero"]
    cat = _plural_category(locale, count)
    value = entry.get(cat)
    if value is None:
        value = entry.get("other")
    if value is None:
        value = entry.get("one")
    if value is None:
        value = next(iter(entry.values()))
    return value


# ============= 格式化 =============

class _SafeDict(dict):
    """缺參數時保留 {name} 原樣，不 raise。"""

    def __missing__(self, key):
        return "{" + key + "}"


# ============= trace（測試用） =============

class _Trace:
    def __init__(self):
        self.keys: list[str] = []
        self.params: dict[str, dict] = {}


_trace_var: contextvars.ContextVar[_Trace | None] = contextvars.ContextVar(
    "i18n_trace", default=None
)


@contextlib.contextmanager
def trace():
    """with i18n.trace() as tr: ...；tr.keys / tr.params 記錄期間所有 t() 呼叫。"""
    tr = _Trace()
    token = _trace_var.set(tr)
    try:
        yield tr
    finally:
        _trace_var.reset(token)


# ============= 核心 t() =============

_current: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "i18n_locale", default=None
)


def current_locale() -> str:
    loc = _current.get()
    return loc if loc is not None else DEFAULT_LOCALE


def push_locale(locale: str) -> contextvars.Token:
    return _current.set(locale)


def reset_locale(token: contextvars.Token) -> None:
    _current.reset(token)


@contextlib.contextmanager
def use_locale(locale: str):
    token = _current.set(locale)
    try:
        yield
    finally:
        _current.reset(token)


def t(key: str, /, *, locale: str | None = None, count=None,
      default: str | None = None, **params: Any) -> str:
    ensure_loaded()

    tr = _trace_var.get()
    if tr is not None:
        tr.keys.append(key)
        merged = dict(params)
        if count is not None:
            merged.setdefault("count", count)
        tr.params[key] = merged

    if TEST_KEYS:
        return key

    if locale is None:
        ctx_loc = _current.get()
        if ctx_loc is None:
            if DEBUG_MODE and key not in _noscope_warned:
                _noscope_warned.add(key)
                _log.warning("t(%r) called with no locale scope; using %s",
                             key, DEFAULT_LOCALE)
            ctx_loc = DEFAULT_LOCALE
        locale = ctx_loc

    value = _lookup_chain(key, locale)
    if value is None:
        if default is not None:
            value = default
        elif STRICT:
            raise MissingTranslationError(key)
        else:
            if key not in _missing_warned:
                _missing_warned.add(key)
                _log.warning("Missing i18n key: %s", key)
            return f"⟦{key}⟧" if DEBUG_MODE else key

    if isinstance(value, dict):
        if count is None:
            if STRICT:
                raise MissingTranslationError(f"{key} is a plural entry but no count= given")
            if DEBUG_MODE:
                return f"⟦{key}:needs-count⟧"
            value = value.get("other") or next(iter(value.values()))
        else:
            value = _select_plural(value, locale, count)

    if count is not None and "count" not in params:
        params = {**params, "count": count}
    if params:
        try:
            value = value.format_map(_SafeDict(params))
        except Exception:
            _log.exception("i18n format failed for key %s", key)
    return value


def tn(key: str, count, /, **params) -> str:
    return t(key, count=count, **params)


def t_enum(prefix: str, value: str, /, **params) -> str:
    """動態 key（t_enum("moderate.action", verb)）；lint 以 prefix 加入白名單。"""
    return t(f"{prefix}.{value}", **params)


# ============= 使用者 / 伺服器語言設定 =============

_VALID_UNSET = (None, "", "auto")
_user_locale_cache: dict[int, str | None] = {}
_guild_locale_cache: dict[int, str | None] = {}
_last_locale_cache: dict[int, str | None] = {}


def _normalize_setting(value) -> str | None:
    if value in _VALID_UNSET:
        return None
    return str(value)


def get_user_locale(user_id: int) -> str | None:
    """None == auto。"""
    if user_id in _user_locale_cache:
        return _user_locale_cache[user_id]
    value = _normalize_setting(db.get_user_data(user_id, 0, USER_LOCALE_KEY, None))
    _user_locale_cache[user_id] = value
    return value


def set_user_locale(user_id: int, locale: str | None) -> bool:
    value = _normalize_setting(locale)
    ok = db.set_user_data(user_id, 0, USER_LOCALE_KEY, value or "auto")
    _user_locale_cache[user_id] = value
    return ok


def get_guild_locale(guild_id: int) -> str | None:
    """None == auto。"""
    if guild_id in _guild_locale_cache:
        return _guild_locale_cache[guild_id]
    value = _normalize_setting(db.get_server_config(guild_id, GUILD_LOCALE_KEY, None))
    _guild_locale_cache[guild_id] = value
    return value


def set_guild_locale(guild_id: int, locale: str | None) -> bool:
    value = _normalize_setting(locale)
    ok = db.set_server_config(guild_id, GUILD_LOCALE_KEY, value or "auto")
    _guild_locale_cache[guild_id] = value
    return ok


def invalidate_guild_locale_cache(guild_id: int) -> None:
    """給面板 trigger 用。"""
    _guild_locale_cache.pop(guild_id, None)


def get_last_locale(user_id: int) -> str | None:
    """最後一次 interaction 觀察到的 Discord 客戶端語言（text command 路徑用）。"""
    if user_id in _last_locale_cache:
        return _last_locale_cache[user_id]
    value = _normalize_setting(db.get_user_data(user_id, 0, LAST_LOCALE_KEY, None))
    _last_locale_cache[user_id] = value
    return value


def note_discord_locale(user_id: int, discord_locale) -> None:
    """持久化 interaction.locale，只在值改變時寫入 DB。"""
    if discord_locale is None:
        return
    value = getattr(discord_locale, "value", None) or str(discord_locale)
    if _last_locale_cache.get(user_id) == value:
        return
    stored = get_last_locale(user_id)
    if stored != value:
        try:
            db.set_user_data(user_id, 0, LAST_LOCALE_KEY, value)
        except Exception:
            _log.exception("Failed to persist last_locale for %s", user_id)
    _last_locale_cache[user_id] = value


# ============= locale 解析 =============

def map_discord_locale(discord_locale) -> str | None:
    """runtime 回覆用的對應：優先精準比對語言檔，最後 catch-all 到 en。"""
    if discord_locale is None:
        return None
    value = getattr(discord_locale, "value", None) or str(discord_locale)
    ensure_loaded()
    if value in ("zh-TW", "zh-CN", "zh-HK"):
        return "zh-TW" if "zh-TW" in _catalogs else None
    if value in _catalogs:
        return value
    base = value.split("-")[0]
    if base in _catalogs:
        return base
    # 其餘語言 catch-all 到英文（比給中文有用）
    if "en" in _catalogs:
        return "en"
    return None


def resolve_locale(*, user_id: int | None = None, guild_id: int | None = None,
                   discord_locale=None) -> str:
    """使用者設定 > 伺服器設定 > Discord locale (interaction 或 last_locale) > 預設。"""
    if user_id:
        user_setting = get_user_locale(user_id)
        if user_setting:
            return user_setting
    if guild_id:
        guild_setting = get_guild_locale(guild_id)
        if guild_setting:
            return guild_setting
    if discord_locale is None and user_id:
        discord_locale = get_last_locale(user_id)
    mapped = map_discord_locale(discord_locale)
    if mapped:
        return mapped
    return DEFAULT_LOCALE


def explain_locale(*, user_id: int | None = None, guild_id: int | None = None,
                   discord_locale=None) -> list[tuple[str, str | None]]:
    """給 /language show 用：每一層的值（None = 未設定/不適用）。"""
    steps: list[tuple[str, str | None]] = []
    steps.append(("user", get_user_locale(user_id) if user_id else None))
    steps.append(("guild", get_guild_locale(guild_id) if guild_id else None))
    dl = discord_locale
    if dl is None and user_id:
        dl = get_last_locale(user_id)
    dl_value = (getattr(dl, "value", None) or str(dl)) if dl is not None else None
    steps.append(("discord", dl_value))
    steps.append(("effective", resolve_locale(
        user_id=user_id, guild_id=guild_id, discord_locale=discord_locale)))
    return steps


def resolve_from_interaction(interaction: discord.Interaction) -> str:
    user = getattr(interaction, "user", None)
    user_id = user.id if user is not None else None
    guild_id = getattr(interaction, "guild_id", None)
    interaction_locale = getattr(interaction, "locale", None)
    if user_id and interaction_locale is not None:
        note_discord_locale(user_id, interaction_locale)
    return resolve_locale(user_id=user_id, guild_id=guild_id,
                          discord_locale=interaction_locale)


# ============= scope =============

@contextlib.asynccontextmanager
async def interaction_scope(interaction: discord.Interaction):
    token = _current.set(resolve_from_interaction(interaction))
    try:
        yield current_locale()
    finally:
        _current.reset(token)


@contextlib.asynccontextmanager
async def guild_scope(guild_id: int | None, *, user_id: int | None = None):
    token = _current.set(resolve_locale(user_id=user_id, guild_id=guild_id))
    try:
        yield current_locale()
    finally:
        _current.reset(token)


@contextlib.asynccontextmanager
async def user_scope(user_id: int):
    token = _current.set(resolve_locale(user_id=user_id))
    try:
        yield current_locale()
    finally:
        _current.reset(token)


def scoped(get_guild, get_user=None):
    """listener 用 decorator。getter 收到與被包函式相同的引數。

        @bot.event
        @i18n.scoped(lambda m: m.guild and m.guild.id, lambda m: m.author.id)
        async def on_message(m): ...
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                gid = get_guild(*args, **kwargs) if get_guild else None
            except Exception:
                gid = None
            try:
                uid = get_user(*args, **kwargs) if get_user else None
            except Exception:
                uid = None
            async with guild_scope(gid, user_id=uid):
                return await func(*args, **kwargs)
        return wrapper
    return decorator


# ============= choke point 1: CommandTree =============

class I18nCommandTree(app_commands.CommandTree):
    """在 interaction 處理 task 內設定 locale（含 autocomplete）。

    tree._call 於 per-event task 內先呼叫 interaction_check，該 task 的
    context 隨 task 結束丟棄，因此這裡 set 不需 reset。
    """

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        try:
            _current.set(resolve_from_interaction(interaction))
        except Exception:
            _log.exception("i18n locale resolution failed; falling back to default")
        return True

    async def sync(self, *, guild: discord.abc.Snowflake | None = None):
        annotate_parameter_name_keys(self)
        return await super().sync(guild=guild)


def _derive_param_name_key(command: app_commands.Command, param) -> str | None:
    """推導參數「名稱」的 i18n key。

    discord.py 會把沒有 rename 的參數名包成不帶 extras 的
    locale_str(name)（transformers._convert_to_locale_strings），
    CatalogTranslator 只翻譯帶 i18n_key 的字串，所以參數名稱需要在
    sync 前補上 key。優先從參數描述的 key（…param.<name>）推導，
    沒有 describe 的參數退而用指令描述的 key（…desc）推導。
    """
    desc = getattr(param, "description", None)
    if isinstance(desc, locale_str):
        desc_key = desc.extras.get("i18n_key")
        if desc_key and ".param." in desc_key:
            prefix, _, _ = desc_key.rpartition(".param.")
            return f"{prefix}.param_name.{param.name}"
    cmd_desc = getattr(command, "_locale_description", None)
    if isinstance(cmd_desc, locale_str):
        cmd_key = cmd_desc.extras.get("i18n_key")
        if cmd_key and cmd_key.endswith(".desc"):
            return f"{cmd_key[:-5]}.param_name.{param.name}"
    return None


def annotate_parameter_name_keys(tree: app_commands.CommandTree) -> int:
    """sync 前把有語言檔內容的參數名稱補上帶 i18n_key 的 locale_str。

    idempotent；只在任一語言的目錄裡真的有 …param_name.<name> 值時
    才標註，避免對 30 個 locale 送出無意義的 translate 呼叫。
    """
    ensure_loaded()
    annotated = 0
    for command in tree.walk_commands():
        if not isinstance(command, app_commands.Command):
            continue
        for param in command._params.values():
            rename = getattr(param, "_rename", None)
            if isinstance(rename, locale_str) and "i18n_key" in rename.extras:
                continue  # 已標註（或模組自帶 rename key）
            key = _derive_param_name_key(command, param)
            if key is None:
                continue
            if not any(key in flat for flat in _catalogs.values()):
                continue
            base_name = str(rename) if isinstance(rename, (str, locale_str)) else param.name
            param._rename = locale_str(base_name, i18n_key=key)
            annotated += 1
    return annotated


# ============= choke point 2/3: View / Modal =============

def _find_interaction(args) -> discord.Interaction | None:
    for arg in args:
        if isinstance(arg, discord.Interaction):
            return arg
    return None


def _wrap_scheduled_task(cls) -> None:
    original = cls.__dict__.get("_scheduled_task")
    if original is None or getattr(original, "_i18n_wrapped", False):
        return

    @functools.wraps(original)
    async def _i18n_scheduled_task(self, *args, **kwargs):
        interaction = _find_interaction(args)
        if interaction is None:
            return await original(self, *args, **kwargs)
        try:
            locale = resolve_from_interaction(interaction)
        except Exception:
            _log.exception("i18n locale resolution failed in UI dispatch")
            return await original(self, *args, **kwargs)
        token = _current.set(locale)
        try:
            return await original(self, *args, **kwargs)
        finally:
            _current.reset(token)

    _i18n_scheduled_task._i18n_wrapped = True
    setattr(cls, "_scheduled_task", _i18n_scheduled_task)


def _wrap_dynamic_item_dispatch(cls) -> None:
    """DynamicItem（如 FixLink/Ticket 的持久化刪除按鈕）不經過
    View._scheduled_task，是獨立的 ViewStore.schedule_dynamic_item_call
    路徑，所以需要單獨包一層。"""
    original = cls.__dict__.get("schedule_dynamic_item_call")
    if original is None or getattr(original, "_i18n_wrapped", False):
        return

    @functools.wraps(original)
    async def _i18n_schedule_dynamic_item_call(self, component_type, factory, interaction, custom_id, match):
        try:
            locale = resolve_from_interaction(interaction)
        except Exception:
            _log.exception("i18n locale resolution failed in dynamic item dispatch")
            return await original(self, component_type, factory, interaction, custom_id, match)
        token = _current.set(locale)
        try:
            return await original(self, component_type, factory, interaction, custom_id, match)
        finally:
            _current.reset(token)

    _i18n_schedule_dynamic_item_call._i18n_wrapped = True
    setattr(cls, "schedule_dynamic_item_call", _i18n_schedule_dynamic_item_call)


def install_ui_hooks() -> None:
    """Wrap BaseView / Modal 的 _scheduled_task，以及 ViewStore 的
    dynamic item dispatch（idempotent）。

    必須 wrap _scheduled_task 而非 _dispatch_item —— 後者在 gateway task
    的 context 執行，set 會洩漏到之後所有 listener task。
    BaseView 涵蓋 View 與 LayoutView；Modal 有自己的 override 所以分開包。
    """
    from discord.ui.view import BaseView, ViewStore
    from discord.ui.modal import Modal
    _wrap_scheduled_task(BaseView)
    _wrap_scheduled_task(Modal)
    _wrap_dynamic_item_dispatch(ViewStore)


# ============= choke point 5: 前綴文字指令 =============

def resolve_from_context(ctx) -> str:
    """Context 沒有 locale 欄位（只有 Interaction 有），所以 Discord 語言那一層
    是靠 last_locale——使用者用過任一斜線指令後才有值。"""
    author = getattr(ctx, "author", None)
    guild = getattr(ctx, "guild", None)
    return resolve_locale(user_id=author.id if author is not None else None,
                          guild_id=guild.id if guild is not None else None)


def _wrap_invoke(original):
    @functools.wraps(original)
    async def _i18n_invoke(self, ctx, /):
        try:
            locale = resolve_from_context(ctx)
        except Exception:
            _log.exception("i18n locale resolution failed in prefix command dispatch")
            return await original(self, ctx)
        token = _current.set(locale)
        try:
            return await original(self, ctx)
        finally:
            _current.reset(token)

    _i18n_invoke._i18n_wrapped = True
    return _i18n_invoke


def install_prefix_command_hook() -> None:
    """Wrap BotBase.invoke（idempotent）。

    包 invoke 而非 before_invoke：invoke 同時涵蓋 check 失敗與
    dispatch_error，所以錯誤訊息也拿得到語言。process_commands 本身跑在
    _schedule_event 建立的 per-message task 裡，不會污染其他 listener。
    """
    from discord.ext.commands.bot import BotBase
    original = BotBase.__dict__.get("invoke")
    if original is None or getattr(original, "_i18n_wrapped", False):
        return
    BotBase.invoke = _wrap_invoke(original)


# ============= choke point 4 helper: Flask =============

def push_locale_for_web(*, user_id: int | None = None, lang_param: str | None = None,
                        accept_language: str | None = None) -> str:
    """給 Website.py 的 @app.before_request 用；回傳解析出的 locale。

    Flask 每個 request 一個 context（thread 或 asgi task），set 後不需 reset。
    """
    ensure_loaded()
    if lang_param and lang_param in _catalogs:
        _current.set(lang_param)
        return lang_param
    if user_id:
        user_setting = get_user_locale(user_id)
        if user_setting:
            _current.set(user_setting)
            return user_setting
    if accept_language:
        for part in accept_language.split(","):
            code = part.split(";")[0].strip()
            if not code:
                continue
            mapped = map_discord_locale(code)
            if mapped:
                _current.set(mapped)
                return mapped
    _current.set(DEFAULT_LOCALE)
    return DEFAULT_LOCALE


# ============= 指令 metadata translator =============

_NAME_LOCATIONS = (
    TranslationContextLocation.command_name,
    TranslationContextLocation.group_name,
    TranslationContextLocation.parameter_name,
)

def _valid_command_name(value: str) -> bool:
    return bool(_DISCORD_NAME_RE.fullmatch(value)) and value == value.lower()


class CatalogTranslator(app_commands.Translator):
    """語言檔驅動的指令 metadata translator。

    宣告形式：
        name=app_commands.locale_str("ban", i18n_key="cmd.moderate.ban.name")

    未帶 i18n_key 的 locale_str 不翻譯（保留原文）。
    """

    async def load(self) -> None:
        ensure_loaded()

    async def translate(self, string: locale_str, locale: discord.Locale,
                        context: TranslationContext) -> str | None:
        key = string.extras.get("i18n_key")
        if key is None:
            return None

        target = _METADATA_LOCALE_MAP.get(locale.value)
        if target is None:
            return None
        value = lookup(key, target)
        if not isinstance(value, str) or not value:
            return None
        if value == string.message:
            return None  # 與原文相同，不必上傳
        if context.location in _NAME_LOCATIONS and not _valid_command_name(value):
            # context menu 名稱允許空白與大寫，不受斜線指令名稱規則限制
            if not isinstance(getattr(context, "data", None), app_commands.ContextMenu):
                _log.warning("Rejected invalid command-name localization %r for key %s",
                             value, key)
                return None
        return value


# ============= class body 用的延遲解析 =============

class K(str):
    """帶 i18n key 的 str，給 class-definition 時求值的位置用
    （@discord.ui.button(label=...)、Modal title 等）。

    繼承 str，所以忘了配 I18nView/I18nModal 也不會 crash——只會顯示 key，
    由 lint 抓出來。
    """
    __slots__ = ("key", "params")

    def __new__(cls, key: str, **params):
        obj = super().__new__(cls, key)
        obj.key = key
        obj.params = params
        return obj


def _resolve_k(value):
    if isinstance(value, K):
        return t(value.key, **value.params)
    return value


def resolve_component_texts(container) -> None:
    """把 container 內所有元件的 K() label / placeholder / option 解析成當前語言。"""
    walk = getattr(container, "walk_children", None)
    children = walk() if callable(walk) else getattr(container, "children", [])
    for child in children:
        for attr in ("label", "placeholder"):
            value = getattr(child, attr, None)
            if isinstance(value, K):
                try:
                    setattr(child, attr, _resolve_k(value))
                except Exception:
                    pass
        options = getattr(child, "options", None)
        if options:
            for option in options:
                if isinstance(getattr(option, "label", None), K):
                    option.label = _resolve_k(option.label)
                if isinstance(getattr(option, "description", None), K):
                    option.description = _resolve_k(option.description)


class I18nView(discord.ui.View):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        resolve_component_texts(self)


class I18nModal(discord.ui.Modal):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if isinstance(self.title, K):
            self.title = _resolve_k(self.title)
        resolve_component_texts(self)


# ============= 數字 / 日期 / 清單格式化 =============

def _sep(key: str, locale: str, fallback: str) -> str:
    value = _lookup_chain(key, locale)
    return value if isinstance(value, str) else fallback


def fmt_num(value, *, decimals: int | None = None, grouping: bool = True,
            locale: str | None = None) -> str:
    loc = locale or current_locale()
    ensure_loaded()
    if decimals is None:
        if isinstance(value, float):
            text = f"{value:,}" if grouping else repr(value)
        else:
            text = f"{value:,}" if grouping else str(value)
    else:
        text = f"{value:,.{decimals}f}" if grouping else f"{value:.{decimals}f}"
    group_sep = _sep("common.number.group_sep", loc, ",")
    decimal_sep = _sep("common.number.decimal_sep", loc, ".")
    if group_sep != "," or decimal_sep != ".":
        text = text.replace(",", "\0").replace(".", decimal_sep).replace("\0", group_sep)
    return text


def fmt_pct(value, *, decimals: int = 1, locale: str | None = None) -> str:
    return fmt_num(value, decimals=decimals, locale=locale) + "%"


def fmt_ts(dt, style: str = "f") -> str:
    """Discord timestamp markup——由觀看者的客戶端在地化，永遠優先用這個。"""
    ts = int(dt.timestamp()) if hasattr(dt, "timestamp") else int(dt)
    return f"<t:{ts}:{style}>"


def fmt_dt(dt, style: str = "short", locale: str | None = None) -> str:
    """strftime 版，只給 web / docs 用（Discord 輸出用 fmt_ts）。"""
    loc = locale or current_locale()
    ensure_loaded()
    pattern = _lookup_chain(f"common.datefmt.{style}", loc)
    if not isinstance(pattern, str):
        pattern = "%Y-%m-%d %H:%M"
    return dt.strftime(pattern)


def join_list(items: Iterable, locale: str | None = None) -> str:
    parts = [str(item) for item in items]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    loc = locale or current_locale()
    ensure_loaded()
    sep = _sep("common.list.sep", loc, "、")
    last_sep = _sep("common.list.last_sep", loc, sep)
    return sep.join(parts[:-1]) + last_sep + parts[-1]


# 於 import 時安裝 hook（idempotent，且純 wrapper 無副作用）
install_ui_hooks()
install_prefix_command_hook()
