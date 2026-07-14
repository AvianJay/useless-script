import discord
from discord import app_commands
from discord.ext import commands
from globalenv import bot, start_bot, on_ready_tasks, get_user_data, set_user_data, config
from taiwanbus import api as busapi
import asyncio
import traceback
import youbike
from datetime import datetime
from typing import Optional, Callable, Any
from datetime import timezone, timedelta
from zoneinfo import ZoneInfo  # Python 3.9+
from logger import log
import logging
from functools import wraps


# Rate limiting decorator
def rate_limit(seconds: int = 10):
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(self, interaction: discord.Interaction, *args, **kwargs):
            user_last_used = get_user_data(0, str(interaction.user.id), "rate_limit_last", None)
            if user_last_used:
                last_time = datetime.fromisoformat(user_last_used)
                now = datetime.utcnow()
                delta = (now - last_time).total_seconds()
                if delta < seconds:
                    log(f"Rate limited: {func.__name__}", level=logging.WARNING, module_name="TWBus", user=interaction.user, guild=interaction.guild)
                    await interaction.response.send_message("你操作的太快了，請稍後再試。", ephemeral=True)
                    return
            set_user_data(0, str(interaction.user.id), "rate_limit_last", datetime.utcnow().isoformat())
            return await func(self, interaction, *args, **kwargs)
        return wrapper
    return decorator


def check_view_rate_limit(user_id: str, seconds: int = 3) -> bool:
    """View 元件共用的速率限制，回傳 True 表示允許操作。"""
    last = get_user_data(0, user_id, "view_rate_limit_last", None)
    if last:
        try:
            delta = (datetime.utcnow() - datetime.fromisoformat(last)).total_seconds()
            if delta < seconds:
                return False
        except ValueError:
            pass
    set_user_data(0, user_id, "view_rate_limit_last", datetime.utcnow().isoformat())
    return True


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1] + "…"


# Helper function to fetch stop info
def fetch_stop_context(route_key: int, stop_id: int) -> Optional[dict]:
    """取得站牌資訊與其所屬路徑的完整站牌列表（供上一站/下一站/路線總覽使用）。"""
    info = busapi.get_complete_bus_info(route_key)
    route = busapi.fetch_route(route_key)[0]

    for path_id, path_data in info.items():
        stops = path_data["stops"]
        for index, stop in enumerate(stops):
            if stop["stop_id"] == stop_id:
                stop_info = dict(stop)
                stop_info["route_name"] = route["route_name"]
                stop_info["path_name"] = path_data.get("name", "")
                return {
                    "stop": stop_info,
                    "route": route,
                    "path_id": path_id,
                    "stops": stops,
                    "index": index,
                }
    return None


def fetch_stop_info(route_key: int, stop_id: int) -> dict:
    """Fetch complete stop information including route and path details."""
    ctx = fetch_stop_context(route_key, stop_id)
    return ctx["stop"] if ctx else {}


# 到站狀態 -> emoji
ETA_EMOJIS = {
    "approaching": "🟢",  # 進站中
    "soon": "🟡",         # 3 分鐘內
    "time": "🔵",         # 一般倒數
    "scheduled": "🕒",    # 表定時間 (HH:MM)
    "msg": "⚪",          # 未發車 / 末班駛離等訊息
    "none": "⚫",         # 無資料
}


def format_eta(payload: dict) -> tuple[str, str]:
    """將站牌到站資料轉為 (顯示文字, 狀態)，狀態為 ETA_EMOJIS 的 key。"""
    sec = payload.get("sec")
    msg = payload.get("msg")
    try:
        sec = int(sec) if sec is not None else None
    except (TypeError, ValueError):
        sec = None

    if msg and (sec is None or sec < 0):
        if ":" in str(msg):
            return str(msg), "scheduled"
        return str(msg), "msg"
    if sec is None:
        return "暫無資料", "none"
    if sec <= 0:
        return "進站中", "approaching"
    if sec < 60:
        text = f"{sec}秒"
    else:
        text = f"{sec // 60}分 {sec % 60}秒"
    return text, "soon" if sec <= 180 else "time"


def format_stop_buses(payload: dict) -> list[str]:
    lines = []
    for b in payload.get("bus") or []:
        bid = b.get("id") or b.get("plate") or "?"
        full = int(b.get("full") or 0)
        status = "已滿" if full == 1 else "可上車"
        lines.append(f"`{bid}`: {status}")
    return lines


# Generic ActionsView with refresh and favorite buttons
class GenericActionsView(discord.ui.View):
    def __init__(
        self,
        interaction: discord.Interaction,
        map_url: Optional[str],
        refresh_callback: Callable,
        favorite_callback: Callable,
        item_name: str
    ):
        super().__init__(timeout=180)
        self.interaction = interaction
        self.refresh_callback = refresh_callback
        self.favorite_callback = favorite_callback
        self.item_name = item_name

        if map_url:
            self.add_item(discord.ui.Button(emoji="🗺️", url=map_url))

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
            if hasattr(item, 'url'):  # Keep map button enabled
                item.disabled = False
        try:
            await self.interaction.edit_original_response(view=self)
        except Exception:
            pass

    @discord.ui.button(emoji="🔄", style=discord.ButtonStyle.primary)
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.interaction.user:
            await interaction.response.send_message("你無權限使用此按鈕。", ephemeral=True)
            return

        user_last_used = get_user_data(0, str(interaction.user.id), "rate_limit_last", None)
        if user_last_used:
            last_time = datetime.fromisoformat(user_last_used)
            now = datetime.utcnow()
            delta = (now - last_time).total_seconds()
            if delta < 10:
                await interaction.response.send_message("你操作的太快了，請稍後再試。", ephemeral=True)
                return

        set_user_data(0, str(interaction.user.id), "rate_limit_last", datetime.utcnow().isoformat())

        try:
            embed, map_url = await self.refresh_callback()
            await interaction.response.edit_message(embed=embed, view=self)
        except Exception as e:
            await interaction.response.send_message(f"重新整理時發生錯誤：{e}", ephemeral=True)
            log(f"重新整理時發生錯誤：{e}", level=logging.ERROR, module_name="TWBus", user=interaction.user, guild=interaction.guild)
            traceback.print_exc()

    @discord.ui.button(emoji="❤️", style=discord.ButtonStyle.primary)
    async def favorite_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.interaction.user:
            await interaction.response.send_message("你無權限使用此按鈕。", ephemeral=True)
            return

        try:
            action = await self.favorite_callback(str(interaction.user.id))
            await interaction.response.send_message(f"{action} {self.item_name}", ephemeral=True)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)


class BaseBusView(discord.ui.View):
    """公車互動視圖的共同基底：擁有者檢查、速率限制、逾時停用按鈕。"""

    def __init__(self, owner_id: int, timeout: float = 300):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        self.message: Optional[discord.Message] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("你無權限使用此按鈕。", ephemeral=True)
            return False
        if not check_view_rate_limit(str(interaction.user.id)):
            await interaction.response.send_message("你操作的太快了，請稍後再試。", ephemeral=True)
            return False
        self.message = interaction.message
        return True

    async def on_timeout(self):
        for item in self.children:
            if getattr(item, "url", None):
                continue
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass


PAGE_SIZE = 20  # 每頁站數，同時是 selector 的選項數（Discord 上限 25）


class BusRouteView(BaseBusView):
    """路線總覽：分頁顯示站牌，左右換頁（跨路徑循環）、中間按鈕切換路徑、selector 快速查站。"""

    def __init__(self, owner_id: int, route_key: int, route: dict, path_id: Optional[int] = None, page: int = 0):
        super().__init__(owner_id)
        self.route_key = route_key
        self.route = route
        self.info: dict = {}
        self.path_ids: list = []
        self.path_index = 0
        self.page = page
        self._want_path_id = path_id
        self.stop_select: Optional[discord.ui.Select] = None

    async def fetch(self) -> bool:
        info = await asyncio.to_thread(busapi.get_complete_bus_info, self.route_key)
        path_ids = [pid for pid, pdata in info.items() if pdata["stops"]]
        if not path_ids:
            return False
        self.info = info
        self.path_ids = path_ids
        if self._want_path_id in self.path_ids:
            self.path_index = self.path_ids.index(self._want_path_id)
        self._want_path_id = None
        self.path_index = min(self.path_index, len(self.path_ids) - 1)
        self.page = max(0, min(self.page, self._page_count() - 1))
        return True

    def _path_data(self) -> dict:
        return self.info[self.path_ids[self.path_index]]

    def _stops(self) -> list:
        return self._path_data()["stops"]

    def _path_name(self) -> str:
        return self._path_data().get("name") or f"路徑 {self.path_ids[self.path_index]}"

    def _page_count(self) -> int:
        return max(1, -(-len(self._stops()) // PAGE_SIZE))

    def _chunk(self) -> list:
        return self._stops()[self.page * PAGE_SIZE:(self.page + 1) * PAGE_SIZE]

    def _flat_pages(self) -> list[tuple[int, int]]:
        """所有 (path_index, page) 的順序清單，讓左右按鈕可以跨路徑連續翻頁。"""
        flat = []
        for pi, pid in enumerate(self.path_ids):
            pages = max(1, -(-len(self.info[pid]["stops"]) // PAGE_SIZE))
            flat.extend((pi, pg) for pg in range(pages))
        return flat

    def build_embed(self) -> discord.Embed:
        stops = self._stops()
        pages = self._page_count()

        lines = [f"### 🚏 {self._path_name()}"]
        for stop in self._chunk():
            eta_text, state = format_eta(stop)
            seq = str(stop.get("sequence", "?")).rjust(2)
            name = (stop.get("stop_name") or "未知站名").strip()
            line = f"{ETA_EMOJIS[state]} `{seq}` {name} ─ {eta_text}"
            buses = stop.get("bus") or []
            if buses:
                tags = " ".join(
                    f"🚍`{b.get('id') or b.get('plate') or '?'}`" + ("（滿）" if int(b.get("full") or 0) == 1 else "")
                    for b in buses
                )
                line += f"　{tags}"
            lines.append(line)

        description = ""
        for line in lines:
            if len(description) + len(line) + 1 > 4000:
                description += "\n…"
                break
            description += ("\n" if description else "") + line

        title = f"🚌 {self.route['route_name']}"
        if self.route.get("description"):
            title += f"（{self.route['description']}）"

        embed = discord.Embed(title=title, description=description, color=0x3498DB)
        embed.set_footer(text=f"第 {self.page + 1}/{pages} 頁 ・ 共 {len(stops)} 站 ・ 上次更新")
        embed.timestamp = datetime.now(timezone.utc)
        return embed

    def rebuild_items(self):
        self.clear_items()
        single_page = len(self._flat_pages()) <= 1

        prev_btn = discord.ui.Button(emoji="◀️", style=discord.ButtonStyle.secondary, row=0, disabled=single_page)
        prev_btn.callback = self.on_prev_page
        self.add_item(prev_btn)

        path_btn = discord.ui.Button(
            emoji="🚏",
            label=_truncate(self._path_name(), 80),
            style=discord.ButtonStyle.primary,
            row=0,
            disabled=len(self.path_ids) <= 1,
        )
        path_btn.callback = self.on_switch_path
        self.add_item(path_btn)

        next_btn = discord.ui.Button(emoji="▶️", style=discord.ButtonStyle.secondary, row=0, disabled=single_page)
        next_btn.callback = self.on_next_page
        self.add_item(next_btn)

        refresh_btn = discord.ui.Button(emoji="🔄", style=discord.ButtonStyle.primary, row=0)
        refresh_btn.callback = self.on_refresh
        self.add_item(refresh_btn)

        options = []
        seen = set()
        for stop in self._chunk():
            value = str(stop["stop_id"])
            if value in seen:
                continue
            seen.add(value)
            eta_text, state = format_eta(stop)
            name = (stop.get("stop_name") or "未知站名").strip()
            options.append(discord.SelectOption(
                label=_truncate(f"{stop.get('sequence', '?')}. {name}", 100),
                description=_truncate(eta_text, 100),
                value=value,
                emoji=ETA_EMOJIS[state],
            ))
        self.stop_select = discord.ui.Select(
            placeholder="🚏 選擇站牌查看到站資訊",
            options=options,
            min_values=1,
            max_values=1,
            row=1,
        )
        self.stop_select.callback = self.on_select_stop
        self.add_item(self.stop_select)

    async def _refresh_message(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            if not await self.fetch():
                await interaction.followup.send("找不到該路線的公車到站資訊。", ephemeral=True)
                return
            self.rebuild_items()
            await interaction.edit_original_response(embed=self.build_embed(), view=self)
        except Exception as e:
            log(f"更新路線資訊時發生錯誤：{e}", level=logging.ERROR, module_name="TWBus", user=interaction.user, guild=interaction.guild)
            traceback.print_exc()
            try:
                await interaction.followup.send(f"更新路線資訊時發生錯誤：{e}", ephemeral=True)
            except Exception:
                pass

    async def _navigate(self, interaction: discord.Interaction, delta: int):
        flat = self._flat_pages()
        if flat:
            try:
                cur = flat.index((self.path_index, self.page))
            except ValueError:
                cur = 0
            self.path_index, self.page = flat[(cur + delta) % len(flat)]
        await self._refresh_message(interaction)

    async def on_prev_page(self, interaction: discord.Interaction):
        await self._navigate(interaction, -1)

    async def on_next_page(self, interaction: discord.Interaction):
        await self._navigate(interaction, 1)

    async def on_switch_path(self, interaction: discord.Interaction):
        if self.path_ids:
            self.path_index = (self.path_index + 1) % len(self.path_ids)
            self.page = 0
        await self._refresh_message(interaction)

    async def on_refresh(self, interaction: discord.Interaction):
        await self._refresh_message(interaction)

    async def on_select_stop(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            stop_id = int(self.stop_select.values[0])
            view = BusStopView(self.owner_id, self.route_key, stop_id)
            if not await view.fetch():
                await interaction.followup.send("找不到該站牌的到站資訊。", ephemeral=True)
                return
            view.rebuild_items()
            await interaction.edit_original_response(embed=view.build_embed(), view=view)
            view.message = interaction.message
            self.stop()
        except Exception as e:
            log(f"切換至站牌資訊時發生錯誤：{e}", level=logging.ERROR, module_name="TWBus", user=interaction.user, guild=interaction.guild)
            traceback.print_exc()
            try:
                await interaction.followup.send(f"發生錯誤：{e}", ephemeral=True)
            except Exception:
                pass


class BusStopView(BaseBusView):
    """站牌到站資訊：重整 / 最愛 / 地圖 + 上一站 / 下一站 / 路線總覽。"""

    def __init__(self, owner_id: int, route_key: int, stop_id: int):
        super().__init__(owner_id)
        self.route_key = route_key
        self.stop_id = stop_id
        self.ctx: Optional[dict] = None
        self.embed: Optional[discord.Embed] = None
        self.map_url: Optional[str] = None

    async def fetch(self) -> bool:
        ctx = await asyncio.to_thread(fetch_stop_context, self.route_key, self.stop_id)
        if not ctx:
            return False
        self.ctx = ctx
        stops, index = ctx["stops"], ctx["index"]
        prev_stop = (stops[index - 1].get("stop_name") or "").strip() if index > 0 else None
        next_stop = (stops[index + 1].get("stop_name") or "").strip() if index < len(stops) - 1 else None
        self.embed, self.map_url = make_bus_embed(ctx["stop"], prev_stop, next_stop)
        return True

    def build_embed(self) -> discord.Embed:
        return self.embed

    def rebuild_items(self):
        self.clear_items()

        refresh_btn = discord.ui.Button(emoji="🔄", style=discord.ButtonStyle.primary, row=0)
        refresh_btn.callback = self.on_refresh
        self.add_item(refresh_btn)

        fav_btn = discord.ui.Button(emoji="❤️", style=discord.ButtonStyle.primary, row=0)
        fav_btn.callback = self.on_favorite
        self.add_item(fav_btn)

        if self.map_url:
            self.add_item(discord.ui.Button(emoji="🗺️", url=self.map_url, row=0))

        index = self.ctx["index"] if self.ctx else 0
        total = len(self.ctx["stops"]) if self.ctx else 0

        prev_btn = discord.ui.Button(emoji="⬅️", label="上一站", style=discord.ButtonStyle.secondary, row=1, disabled=index <= 0)
        prev_btn.callback = self.on_prev_stop
        self.add_item(prev_btn)

        next_btn = discord.ui.Button(emoji="➡️", label="下一站", style=discord.ButtonStyle.secondary, row=1, disabled=index >= total - 1)
        next_btn.callback = self.on_next_stop
        self.add_item(next_btn)

        route_btn = discord.ui.Button(emoji="🚌", label="路線總覽", style=discord.ButtonStyle.secondary, row=1)
        route_btn.callback = self.on_route_overview
        self.add_item(route_btn)

    async def _goto(self, interaction: discord.Interaction, stop_id: int):
        await interaction.response.defer()
        old_stop_id = self.stop_id
        self.stop_id = stop_id
        try:
            if not await self.fetch():
                self.stop_id = old_stop_id
                await interaction.followup.send("找不到該站牌的到站資訊。", ephemeral=True)
                return
            self.rebuild_items()
            await interaction.edit_original_response(embed=self.build_embed(), view=self)
        except Exception as e:
            self.stop_id = old_stop_id
            log(f"更新站牌資訊時發生錯誤：{e}", level=logging.ERROR, module_name="TWBus", user=interaction.user, guild=interaction.guild)
            traceback.print_exc()
            try:
                await interaction.followup.send(f"更新站牌資訊時發生錯誤：{e}", ephemeral=True)
            except Exception:
                pass

    async def on_refresh(self, interaction: discord.Interaction):
        await self._goto(interaction, self.stop_id)

    async def on_prev_stop(self, interaction: discord.Interaction):
        if not self.ctx or self.ctx["index"] <= 0:
            await interaction.response.defer()
            return
        await self._goto(interaction, self.ctx["stops"][self.ctx["index"] - 1]["stop_id"])

    async def on_next_stop(self, interaction: discord.Interaction):
        if not self.ctx or self.ctx["index"] >= len(self.ctx["stops"]) - 1:
            await interaction.response.defer()
            return
        await self._goto(interaction, self.ctx["stops"][self.ctx["index"] + 1]["stop_id"])

    async def on_route_overview(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            view = BusRouteView(
                self.owner_id,
                self.route_key,
                self.ctx["route"],
                path_id=self.ctx["path_id"],
                page=self.ctx["index"] // PAGE_SIZE,
            )
            if not await view.fetch():
                await interaction.followup.send("找不到該路線的公車到站資訊。", ephemeral=True)
                return
            view.rebuild_items()
            await interaction.edit_original_response(embed=view.build_embed(), view=view)
            view.message = interaction.message
            self.stop()
        except Exception as e:
            log(f"切換至路線總覽時發生錯誤：{e}", level=logging.ERROR, module_name="TWBus", user=interaction.user, guild=interaction.guild)
            traceback.print_exc()
            try:
                await interaction.followup.send(f"發生錯誤：{e}", ephemeral=True)
            except Exception:
                pass

    async def on_favorite(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        stop_identifier = f"{self.route_key}:{self.stop_id}"
        stop_name = (self.ctx or {}).get("stop", {}).get("stop_name", "Unknown Stop")
        fav_stops = get_user_data(0, user_id, "favorite_stops", [])
        fav_limit = get_user_data(0, user_id, "favorite_stops_limit", config("default_favorite_stops_limit", 2))

        if stop_identifier not in fav_stops and len(fav_stops) >= fav_limit:
            await interaction.response.send_message(f"你最多只能有 {fav_limit} 個最愛站牌。", ephemeral=True)
            return

        if stop_identifier in fav_stops:
            fav_stops.remove(stop_identifier)
            action = "已從最愛移除"
        else:
            fav_stops.append(stop_identifier)
            action = "已加入最愛"

        set_user_data(0, user_id, "favorite_stops", fav_stops)
        await interaction.response.send_message(f"{action} 站牌：{stop_name}", ephemeral=True)


async def bus_route_autocomplete(interaction: discord.Interaction, current: str):
    routes = busapi.fetch_routes_by_name(current)
    return [
        app_commands.Choice(name=f"{route['route_name']} ({route['description']})", value=str(route['route_key']))
        for route in routes[:25]  # Discord autocomplete limit
    ]


async def get_stop_autocomplete(interaction: discord.Interaction, current: str):
    route_key = interaction.namespace.route_key
    if not route_key:
        return []
    stops = busapi.fetch_stops_by_route(int(route_key))
    paths = busapi.fetch_paths(int(route_key))
    choices = [
        (
            f"[{next((path['path_name'] for path in paths if path['path_id'] == stop['path_id']), '?')}] {stop['stop_name']}",
            str(stop['stop_id'])
        )
        for stop in stops
    ]
    filtered = [app_commands.Choice(name=name, value=value) for name, value in choices if current in name][:25]
    return filtered


async def youbike_station_autocomplete(interaction: discord.Interaction, current: str):
    global youbike_data
    if youbike_data is None:
        return []
    stations = [station for station in youbike_data if current in station['name_tw'] or current in station['address_tw'] or current in station['district_tw']]
    return [
        app_commands.Choice(name=station['name_tw'], value=station['station_no'])
        for station in stations[:25]
    ]


# ai time
YOUBIKE_IMG_BASE = "https://www.youbike.com.tw"  # 若不需要可設為 ""

def _parse_time(value: Optional[str]) -> Optional[datetime | str]:
    if not value:
        return None
    # 嘗試解析常見時間格式，若解析失敗就回傳原字串
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            try:
                tz = ZoneInfo("Asia/Taipei")
            except Exception:
                tz = timezone(timedelta(hours=8))
            dt = datetime.strptime(value, fmt)
            dt = dt.replace(tzinfo=tz)
            return dt
        except Exception:
            continue
    return value

def _color_for_ratio(available: int, capacity: int) -> discord.Color:
    try:
        if capacity <= 0:
            return discord.Color.greyple()
        ratio = available / capacity
    except Exception:
        return discord.Color.greyple()
    if ratio >= 0.5:
        return discord.Color.green()
    if ratio >= 0.15:
        return discord.Color.gold()
    return discord.Color.red()

def make_youbike_embed(station: dict) -> tuple[discord.Embed, str]:
    """
    將 youbike.getstationbyid 回傳的站點 dict 轉為 discord.Embed。
    """
    # 可能的欄位名稱映射（根據不同 youbike API 版本）
    name = station.get("sna") or station.get("name_tw") or station.get("name") or station.get("name_en") or "Unknown Station"
    name_en = station.get("sna_en") or station.get("name_en") or None
    district = station.get("sarea") or station.get("district_tw") or station.get("district") or None
    address = station.get("ar") or station.get("address_tw") or station.get("address") or None

    # 數值欄位：嘗試轉 int（若失敗設為 None）
    def _int_of(k):
        v = station.get(k)
        try:
            return int(v)
        except Exception:
            return None

    total = _int_of("tot") or _int_of("parking_spaces") or _int_of("total") or 0
    available = _int_of("sbi") or _int_of("available") or _int_of("available_spaces") or 0
    empty = _int_of("bemp") or _int_of("empty_spaces") or None

    # 時間欄位：mday / time / update / service_update
    time_raw = station.get("mday") or station.get("time") or station.get("update_time") or station.get("service_update")
    time_dt = _parse_time(time_raw)

    # 狀態欄位 (act 1=active)
    act = station.get("act")
    is_active = True
    if act is not None:
        try:
            is_active = str(act) not in ("0", "false", "False")
        except Exception:
            is_active = True

    # embed color 根據 available / total 決定
    color = _color_for_ratio(available or 0, total or 0)

    # 建立 embed
    title = f"{name}"
    if name_en:
        title = f"{name} · {name_en}"
    embed = discord.Embed(title=title, color=color)

    # description 放區域 + 地址
    desc_parts = []
    if district:
        desc_parts.append(str(district))
    if address:
        desc_parts.append(str(address))
    if not desc_parts and station.get("sna_en"):
        desc_parts.append(station.get("sna_en"))
    if desc_parts:
        embed.description = " · ".join(desc_parts)

    # 主欄位：可用、空位、總格
    embed.add_field(name="可借車數", value=str(available), inline=True)
    if empty is not None:
        embed.add_field(name="可停空位", value=str(empty), inline=True)
    embed.add_field(name="總停車格", value=str(total), inline=True)

    # 加上一些額外資訊欄位（如站點編號、狀態、時間）
    sid = station.get("sid") or station.get("station_id") or station.get("id")
    if sid:
        embed.add_field(name="站點編號", value=str(sid), inline=True)

    embed.add_field(name="狀態", value=("營運中" if is_active else "已停用"), inline=True)

    if time_dt:
        if isinstance(time_dt, datetime):
            embed.set_footer(text="最後更新")
            embed.timestamp = time_dt
        else:
            embed.add_field(name="最後更新", value=time_dt, inline=False)

    # 座標與地圖連結（若有）
    lat = station.get("lat") or station.get("latitude")
    lng = station.get("lng") or station.get("longitude")
    try:
        lat_f = float(lat) if lat is not None else None
        lng_f = float(lng) if lng is not None else None
    except Exception:
        lat_f = lng_f = None

    if lat_f is not None and lng_f is not None:
        # Google Maps 簡易連結
        map_url = f"https://www.google.com/maps/search/?api=1&query={lat_f},{lng_f}"
        # embed.add_field(name="地圖連結", value=f"[在地圖上開啟]({map_url})", inline=False)
        # 設 footer 顯示座標
        # embed.set_footer(text=f"座標: {lat_f:.6f}, {lng_f:.6f}")
        embed.add_field(name="座標", value=f"[{lat_f:.6f}, {lng_f:.6f}]({map_url})", inline=False)

    # 圖片處理
    img = station.get("img") or station.get("image") or station.get("picture")
    if img:
        # 若 img 看起來是相對路徑且有 base，則補上 base
        if img.startswith("/") and YOUBIKE_IMG_BASE:
            embed.set_thumbnail(url=YOUBIKE_IMG_BASE.rstrip("/") + img)
        else:
            embed.set_thumbnail(url=img)


    return embed, map_url if lat_f is not None and lng_f is not None else None


def make_youbike_text(station: dict) -> tuple[str]:
    """
    將 youbike.getstationbyid 回傳的站點 dict 轉為純文字描述。 (標題, 內容)
    """
    name = station.get("name_tw") or "未知位置"
    total = station.get("parking_spaces") or 0
    available = station.get("available_spaces") or 0
    empty = station.get("empty_spaces") or None

    title = f"YouBike/{name}"
    content = f"可借/可停/總格\n{available} / {empty} / {total}"
    return title, content


def make_bus_embed(payload: dict, prev_stop: Optional[str] = None, next_stop: Optional[str] = None) -> tuple[discord.Embed, Optional[str]]:
    """
    將公車到站資料（例如 taiwanbus searchstop 回傳的單筆資料）轉為 discord.Embed。
    回傳 (embed, map_url_or_None)
    """
    route_name = payload.get("route_name") or payload.get("route") or "Unknown Route"
    path_name = payload.get("path_name") or payload.get("path") or ""
    stop_name = payload.get("stop_name") or payload.get("stop") or "Unknown Stop"

    eta_text, state = format_eta(payload)
    bus_lines = format_stop_buses(payload)
    buses = payload.get("bus") or []
    all_full = bool(buses) and all(int(b.get("full") or 0) == 1 for b in buses)

    # 顏色：全部滿載紅色，其餘依到站狀態
    if all_full:
        color = discord.Color.red()
    else:
        color = {
            "approaching": discord.Color.green(),
            "soon": discord.Color.orange(),
            "time": discord.Color.blue(),
            "scheduled": discord.Color.gold(),
            "msg": discord.Color.gold(),
            "none": discord.Color.greyple(),
        }.get(state, discord.Color.greyple())

    title = f"🚌 {route_name}"
    if path_name:
        title += f"｜{path_name}"

    embed = discord.Embed(title=title, color=color)
    embed.description = f"### 🚏 {stop_name}"

    # 到站資訊
    if state == "approaching":
        embed.add_field(name="到站狀態", value=f"{ETA_EMOJIS[state]} 進站中", inline=True)
    elif state == "scheduled":
        embed.add_field(name="預計到站", value=f"{ETA_EMOJIS[state]} {eta_text}", inline=True)
    elif state == "msg":
        embed.add_field(name="訊息", value=f"{ETA_EMOJIS[state]} {eta_text}", inline=True)
    elif state == "none":
        embed.add_field(name="到站狀態", value=f"{ETA_EMOJIS[state]} 暫無資料", inline=True)
    else:
        embed.add_field(name="預估到站", value=f"{ETA_EMOJIS[state]} {eta_text}", inline=True)

    sequence = payload.get("sequence")
    if sequence is not None:
        embed.add_field(name="站序", value=str(sequence), inline=True)

    # 車輛狀態
    if bus_lines:
        embed.add_field(name="車輛狀態", value=_truncate("\n".join(bus_lines), 1024), inline=False)

    # 相鄰站牌
    if prev_stop is not None or next_stop is not None:
        embed.add_field(name="⬅️ 上一站", value=prev_stop or "─（起點）", inline=True)
        embed.add_field(name="下一站 ➡️", value=next_stop or "─（終點）", inline=True)

    embed.set_footer(text="上次更新")
    embed.timestamp = datetime.now(timezone.utc)

    # 座標與地圖連結
    lat = payload.get("lat")
    lon = payload.get("lon") or payload.get("lng")
    map_url = None
    try:
        if lat is not None and lon is not None:
            lat_f = float(lat)
            lon_f = float(lon)
            map_url = f"https://www.google.com/maps/search/?api=1&query={lat_f},{lon_f}"
            embed.add_field(name="座標", value=f"[{lat_f:.6f}, {lon_f:.6f}]({map_url})", inline=False)
    except Exception:
        map_url = None

    return embed, map_url


def make_bus_text(payload: dict) -> tuple[str, str]:
    """
    將公車到站資料（例如 taiwanbus searchstop 回傳的單筆資料）轉為純文字描述。
    僅到站時間。
    """
    title = f"公車/{payload.get('route_name', 'Unknown Route')}[{payload.get('path_name', 'Unknown Path')}] - {payload.get('stop_name', 'Unknown Stop')}"
    text, _ = format_eta(payload)

    bus_lines = format_stop_buses(payload)
    if bus_lines:
        text += "\n車輛狀態：\n" + "\n".join(bus_lines)
    return title, text


@app_commands.guild_only()
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.allowed_installs(guilds=True, users=True)
class TWBus(commands.GroupCog, name=app_commands.locale_str("bus")):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        super().__init__()

    @app_commands.command(name=app_commands.locale_str("getroute"), description="查詢指定的路線")
    @app_commands.describe(route_key="路線ID")
    @app_commands.autocomplete(route_key=bus_route_autocomplete)
    @rate_limit(10)
    async def get_route(self, interaction: discord.Interaction, route_key: str):
        await interaction.response.defer()
        log(f"查詢路線 {route_key}", module_name="TWBus", user=interaction.user, guild=interaction.guild)

        route_key_int = int(route_key)
        try:
            routes = await asyncio.to_thread(busapi.fetch_route, route_key_int)
            if not routes:
                await interaction.followup.send("找不到該路線。", ephemeral=True)
                return

            view = BusRouteView(interaction.user.id, route_key_int, routes[0])
            if not await view.fetch():
                await interaction.followup.send("找不到該路線的公車到站資訊。", ephemeral=True)
                return

            view.rebuild_items()
            view.message = await interaction.followup.send(embed=view.build_embed(), view=view)
        except Exception as e:
            await interaction.followup.send(f"發生錯誤：{e}", ephemeral=True)
            traceback.print_exc()

    @app_commands.command(name=app_commands.locale_str("getstop"), description="查詢指定的站牌")
    @app_commands.describe(route_key="路線ID", stop_id="站牌ID")
    @app_commands.autocomplete(route_key=bus_route_autocomplete, stop_id=get_stop_autocomplete)
    @rate_limit(10)
    async def get_stop(self, interaction: discord.Interaction, route_key: str, stop_id: str):
        await interaction.response.defer()
        log(f"查詢路線 {route_key} 的站牌 {stop_id}", module_name="TWBus", user=interaction.user, guild=interaction.guild)

        route_key_int = int(route_key)
        stop_id_int = int(stop_id)

        try:
            view = BusStopView(interaction.user.id, route_key_int, stop_id_int)
            if not await view.fetch():
                await interaction.followup.send("找不到該站牌的到站資訊。", ephemeral=True)
                return

            view.rebuild_items()
            view.message = await interaction.followup.send(embed=view.build_embed(), view=view)
        except Exception as e:
            await interaction.followup.send(f"發生錯誤：{e}", ephemeral=True)
            traceback.print_exc()

    @app_commands.command(name=app_commands.locale_str("youbike"), description="查詢指定的YouBike站點")
    @app_commands.describe(station_name="YouBike站點名稱")
    @app_commands.autocomplete(station_name=youbike_station_autocomplete)
    @rate_limit(10)
    async def youbike(self, interaction: discord.Interaction, station_name: str):
        await interaction.response.defer()
        log(f"查詢YouBike站點 {station_name}", module_name="TWBus", user=interaction.user, guild=interaction.guild)

        try:
            info = youbike.getstationbyid(station_name)
            if not info:
                await interaction.followup.send("找不到該YouBike站點的資訊。", ephemeral=True)
                return

            embed, map_url = make_youbike_embed(info)

            # Refresh callback
            async def refresh():
                info = youbike.getstationbyid(station_name)
                return make_youbike_embed(info)

            # Favorite callback
            async def toggle_favorite(user_id: str):
                fav_youbike = get_user_data(0, user_id, "favorite_youbike", [])
                fav_limit = get_user_data(0, user_id, "favorite_youbike_limit", config("default_favorite_youbike_limit", 2))

                if station_name not in fav_youbike and len(fav_youbike) >= fav_limit:
                    raise ValueError(f"你最多只能有 {fav_limit} 個最愛 YouBike 站點。")

                if station_name in fav_youbike:
                    fav_youbike.remove(station_name)
                    action = "已從最愛移除"
                else:
                    fav_youbike.append(station_name)
                    action = "已加入最愛"

                set_user_data(0, user_id, "favorite_youbike", fav_youbike)
                return f"{action} YouBike 站點：{station_name}"

            view = GenericActionsView(interaction, map_url, refresh, toggle_favorite, station_name)
            await interaction.followup.send(embed=embed, view=view)

        except Exception as e:
            await interaction.followup.send(f"發生錯誤：{e}", ephemeral=True)
            traceback.print_exc()

    @app_commands.command(name=app_commands.locale_str("favorites"), description="你的最愛站牌與YouBike站點")
    @rate_limit(10)
    async def favorites(self, interaction: discord.Interaction):
        await interaction.response.defer()
        log(f"{interaction.user} 查詢最愛站牌與YouBike站點", module_name="TWBus", user=interaction.user, guild=interaction.guild)

        try:
            user_id = str(interaction.user.id)
            fav_stops = get_user_data(0, user_id, "favorite_stops", [])
            fav_youbike = get_user_data(0, user_id, "favorite_youbike", [])

            if not fav_stops and not fav_youbike:
                await interaction.followup.send("你還沒有設定任何最愛站牌或YouBike站點。", ephemeral=True)
                return

            embed = discord.Embed(title="我的最愛", color=0x00ff00)
            selects = []

            # Process favorite bus stops concurrently
            async def process_bus_stop(stop_identifier: str):
                try:
                    route_key, stop_id = stop_identifier.split(":")
                    route_key_int = int(route_key)
                    stop_id_int = int(stop_id)

                    stop_info = await asyncio.to_thread(fetch_stop_info, route_key_int, stop_id_int)
                    if stop_info:
                        title, text = make_bus_text(stop_info)
                        return ("bus", stop_identifier, title, text, None)
                    else:
                        raise ValueError("找不到該站牌的到站資訊。")
                except Exception as e:
                    log(f"處理最愛站牌 {stop_identifier} 時發生錯誤：{e}", level=logging.ERROR, module_name="TWBus", user=interaction.user, guild=interaction.guild)
                    return ("bus", stop_identifier, f"[未知站牌]{stop_identifier}", f"無法取得站牌資訊：\n{str(e)}", e)

            # Process favorite youbike stations concurrently
            async def process_youbike_station(station_name: str):
                try:
                    info = await asyncio.to_thread(youbike.getstationbyid, station_name)
                    if info:
                        title, text = make_youbike_text(info)
                        return ("youbike", station_name, title, text, None)
                    else:
                        raise ValueError("找不到該YouBike站點的資訊。")
                except Exception as e:
                    log(f"處理最愛YouBike站點 {station_name} 時發生錯誤：{e}", level=logging.ERROR, module_name="TWBus", user=interaction.user, guild=interaction.guild)
                    return ("youbike", station_name, f"[未知YouBike站點]{station_name}", f"無法取得站點資訊：\n{str(e)}", e)

            # Fetch all favorites concurrently
            tasks = []
            tasks.extend([process_bus_stop(stop) for stop in fav_stops])
            tasks.extend([process_youbike_station(station) for station in fav_youbike])

            results = await asyncio.gather(*tasks)

            # Add results to embed and select options
            for category, identifier, title, text, error in results:
                embed.add_field(name=title, value=text, inline=False)
                if not error:
                    selects.append(discord.SelectOption(label=title, value=f"{category}/{identifier}"))

            # Select menu for quick access
            class FavoritesView(discord.ui.View):
                def __init__(self, interaction: discord.Interaction, options: list[discord.SelectOption]):
                    super().__init__(timeout=180)
                    self.interaction = interaction

                async def on_timeout(self):
                    for item in self.children:
                        item.disabled = True
                    try:
                        await self.interaction.edit_original_response(view=self)
                    except Exception:
                        pass

                @discord.ui.select(placeholder="快速前往最愛站牌或YouBike站點", options=selects, min_values=1, max_values=1)
                async def select_favorite(self, interaction: discord.Interaction, select: discord.ui.Select):
                    if interaction.user != self.interaction.user:
                        await interaction.response.send_message("你無權限使用此選單。", ephemeral=True)
                        return

                    await interaction.response.defer()
                    value = select.values[0]
                    try:
                        category, identifier = value.split("/", 1)
                        if category == "bus":
                            route_key, stop_id = identifier.split(":")
                            stop_view = BusStopView(interaction.user.id, int(route_key), int(stop_id))
                            if not await stop_view.fetch():
                                await interaction.followup.send("找不到該站牌的到站資訊。", ephemeral=True)
                                return
                            stop_view.rebuild_items()
                            stop_view.message = await interaction.followup.send(embed=stop_view.build_embed(), view=stop_view)
                        elif category == "youbike":
                            info = await asyncio.to_thread(youbike.getstationbyid, identifier)
                            embed, map_url = make_youbike_embed(info)
                            await interaction.followup.send(embed=embed)
                    except Exception as e:
                        try:
                            await interaction.followup.send(f"載入最愛項目時發生錯誤：{e}", ephemeral=True)
                        except Exception:
                            pass
                        log(f"載入最愛項目時發生錯誤：{e}", level=logging.ERROR, module_name="TWBus", user=interaction.user, guild=interaction.guild)
                        traceback.print_exc()

            await interaction.followup.send(embed=embed, view=FavoritesView(interaction, selects) if selects else None)

        except Exception as e:
            log(f"查詢最愛站牌與YouBike站點時發生錯誤：{e}", level=logging.ERROR, module_name="TWBus", user=interaction.user, guild=interaction.guild)
            await interaction.followup.send(f"發生錯誤：{e}", ephemeral=True)
            traceback.print_exc()

asyncio.run(bot.add_cog(TWBus(bot)))

youbike_data = None
async def on_ready_update_database():
    await bot.wait_until_ready()
    log("自動更新資料庫任務已啟動", module_name="TWBus")
    while not bot.is_closed():
        try:
            await asyncio.to_thread(busapi.update_database, info=True)
            log("公車資料庫更新完畢", module_name="TWBus")
        except Exception as e:
            log(f"更新資料庫時發生錯誤：{e}", level=logging.ERROR, module_name="TWBus")
        try:
            global youbike_data
            youbike_data = await asyncio.to_thread(youbike.getallstations)
            log("YouBike 資料更新完畢", module_name="TWBus")
        except Exception as e:
            log(f"更新 YouBike 資料時發生錯誤：{e}", level=logging.ERROR, module_name="TWBus")
        await asyncio.sleep(3600)  # 每小時更新一次
on_ready_tasks.append(on_ready_update_database)

if __name__ == "__main__":
    start_bot()
