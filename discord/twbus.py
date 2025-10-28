import discord
from discord import app_commands
from discord.ext import commands
from globalenv import bot, start_bot, on_ready_tasks, get_user_data, set_user_data, config
from taiwanbus import api as busapi
import asyncio
import traceback
import youbike
from datetime import datetime
from typing import Optional
from datetime import timezone, timedelta
from zoneinfo import ZoneInfo  # Python 3.9+
from logger import log
import logging


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
    choices =  [(f"[{next(path['path_name'] for path in paths if path['path_id'] == stop['path_id'])}] {stop['stop_name']}", str(stop['stop_id'])) for stop in stops]
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


def make_bus_embed(payload: dict) -> tuple[discord.Embed, Optional[str]]:
    """
    將公車到站資料（例如 taiwanbus searchstop 回傳的單筆資料）轉為 discord.Embed。
    回傳 (embed, map_url_or_None)
    """
    route_name = payload.get("route_name") or payload.get("route") or "Unknown Route"
    path_name = payload.get("path_name") or payload.get("path") or ""
    stop_name = payload.get("stop_name") or payload.get("stop") or "Unknown Stop"

    title = f"{route_name}"
    if path_name:
        title = f"{title} — {path_name}"

    # sec: 0 = approaching, <0 show msg, >0 seconds? depends on API
    sec = payload.get("sec")
    msg = payload.get("msg")

    # 解析 upcoming times 字串（例如 "5,10"）
    sec = payload.get("sec")
    if sec < 0:
        time_str = None
    elif sec < 60 and sec > 0:
        time_str = f"{sec}秒"
    else:
        time_str = f"{sec // 60}分 {sec % 60}秒"

    # bus list
    buses = payload.get("bus") or []
    bus_lines = []
    any_full = False
    for b in buses:
        bid = b.get("id") or b.get("plate") or "?"

        full = int(b.get("full") or 0)
        any_full = any_full or (full == 1)
        status = "已滿" if full == 1 else "可上車"
        bus_lines.append(f"`{bid}`: {status}")

    # 決定顏色：若進站中 (sec == 0) -> 綠；若全部已滿 -> 紅；若 sec < 0 則用金色顯示 msg；預設綠色
    try:
        if sec == 0:
            color = discord.Color.green()
        elif any_full and not time_str:
            color = discord.Color.red()
        elif sec is not None and isinstance(sec, (int, float)) and sec < 0:
            color = discord.Color.gold()
        else:
            color = discord.Color.green()
    except Exception:
        color = discord.Color.greyple()

    embed = discord.Embed(title=title, color=color)
    embed.description = stop_name

    # 顯示靠近狀態或時間訊息
    if not msg and sec <= 0:
        embed.add_field(name="到站狀態", value="進站中", inline=True)
    elif sec is not None and isinstance(sec, (int, float)) and sec < 0 and msg:
        # check msg is **:**
        if isinstance(msg, str) and ":" in msg:
            embed.add_field(name="預計到站", value=str(msg), inline=True)
        else:
            embed.add_field(name="訊息", value=str(msg), inline=True)
    elif time_str:
        embed.add_field(name="預估到站", value=time_str, inline=True)

    # 其他欄位
    sequence = payload.get("sequence")
    if sequence is not None:
        embed.add_field(name="站序", value=str(sequence), inline=True)

    # 加入到站資訊
    if bus_lines:
        embed.add_field(name="車輛狀態", value="\n".join(bus_lines), inline=False)

    # stop_id = payload.get("stop_id")
    # if stop_id:
    #     embed.set_footer(text=f"站牌ID: {stop_id}")
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


def make_bus_text(payload: dict) -> tuple[str]:
    """
    將公車到站資料（例如 taiwanbus searchstop 回傳的單筆資料）轉為純文字描述。
    僅到站時間。
    """
    title = f"公車/{payload.get('route_name', 'Unknown Route')}[{payload.get('path_name', 'Unknown Path')}] - {payload.get('stop_name', 'Unknown Stop')}"
    text = ""
    if payload.get("msg"):
        text = payload["msg"]
    elif payload.get("sec") and int(payload["sec"]) >= 0:
        sec = int(payload["sec"])
        if sec < 60:
            text = f"{sec}秒"
        else:
            text = f"還有 {sec // 60} 分 {sec % 60} 秒"
    else:
        text = "進站中"
    
    if payload.get("bus"):
        bus_lines = []
        for b in payload["bus"]:
            bid = b.get("id") or b.get("plate") or "?"
            full = int(b.get("full") or 0)
            status = "已滿" if full == 1 else "可上車"
            bus_lines.append(f"`{bid}`: {status}")
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
    async def get_route(self, interaction: discord.Interaction, route_key: str):
        await interaction.response.defer()
        user_last_used = get_user_data(0, str(interaction.user.id), "rate_limit_last", None)
        if user_last_used:
            last_time = datetime.fromisoformat(user_last_used)
            now = datetime.utcnow()
            delta = (now - last_time).total_seconds()
            if delta < 10:
                log(f"查詢路線 {route_key} 被限速", level=logging.WARNING, module_name="TWBus", user=interaction.user, guild=interaction.guild)
                await interaction.followup.send("你操作的太快了，請稍後再試。", ephemeral=True)
                return
        log(f"查詢路線 {route_key}", module_name="TWBus", user=interaction.user, guild=interaction.guild)
        set_user_data(0, str(interaction.user.id), "rate_limit_last", datetime.utcnow().isoformat())
        route_key = int(route_key)
        try:
            info = busapi.get_complete_bus_info(route_key)
            route = busapi.fetch_route(route_key)[0]
            if not info:
                await interaction.followup.send("找不到該路線的公車到站資訊。", ephemeral=True)
                return
            formated = busapi.format_bus_info(info)

            embed = discord.Embed(title=f"{route['route_name']} ({route['description']})", description=formated, color=0x00ff00)

            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(f"發生錯誤：{e}", ephemeral=True)
            traceback.print_exc()
            
    @app_commands.command(name=app_commands.locale_str("getstop"), description="查詢指定的站牌")
    @app_commands.describe(route_key="路線ID", stop_id="站牌ID")
    @app_commands.autocomplete(route_key=bus_route_autocomplete, stop_id=get_stop_autocomplete)
    async def get_stop(self, interaction: discord.Interaction, route_key: str, stop_id: str):
        await interaction.response.defer()
        user_last_used = get_user_data(0, str(interaction.user.id), "rate_limit_last", None)
        if user_last_used:
            last_time = datetime.fromisoformat(user_last_used)
            now = datetime.utcnow()
            delta = (now - last_time).total_seconds()
            if delta < 10:
                log(f"查詢路線 {route_key} 的站牌 {stop_id} 被限速", level=logging.WARNING, module_name="TWBus", user=interaction.user, guild=interaction.guild)
                await interaction.followup.send("你操作的太快了，請稍後再試。", ephemeral=True)
                return
        set_user_data(0, str(interaction.user.id), "rate_limit_last", datetime.utcnow().isoformat())
        log(f"查詢路線 {route_key} 的站牌 {stop_id}", module_name="TWBus", user=interaction.user, guild=interaction.guild)
        route_key = int(route_key)
        stop_id = int(stop_id)
        paths = busapi.fetch_paths(int(route_key))
        try:
            info = busapi.get_complete_bus_info(route_key)
            route = busapi.fetch_route(route_key)[0]
            if not info:
                await interaction.followup.send("找不到該路線的公車到站資訊。", ephemeral=True)
                return
            stop_info = {}
            for path_id, path_data in info.items():
                for stop in path_data["stops"]:
                    if stop["stop_id"] == stop_id:
                        stop_info.update(stop)
                        stop_info["route_name"] = route["route_name"]
                        path = next((p for p in paths if p["path_id"] == path_id), None)
                        if path:
                            stop_info["path_name"] = path["path_name"]

            if not stop_info:
                await interaction.followup.send("找不到該站牌的到站資訊。", ephemeral=True)
                return

            embed, map_url = make_bus_embed(stop_info)
            class ActionsView(discord.ui.View):
                def __init__(self, interaction: discord.Interaction, url: str):
                    super().__init__()
                    self.interaction = interaction
                    self.add_item(discord.ui.Button(emoji="🗺️", url=url))
                
                async def on_timeout(self):
                    for item in self.children:
                        item.disabled = True
                    self.children[-1].disabled = False  # keep map button enabled
                    try:
                        await self.interaction.edit_original_response(view=self)
                    except Exception:
                        pass
                
                # refresh button
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
                            log(f"重新整理路線 {route_key} 的站牌 {stop_id} 被限速", level=logging.WARNING, module_name="TWBus", user=interaction.user, guild=interaction.guild)
                            await interaction.response.send_message("你操作的太快了，請稍後再試。", ephemeral=True)
                            return
                    set_user_data(0, str(interaction.user.id), "rate_limit_last", datetime.utcnow().isoformat())
                    log(f"重新整理路線 {route_key} 的站牌 {stop_id}", module_name="TWBus", user=interaction.user, guild=interaction.guild)
                    try:
                        info = busapi.get_complete_bus_info(route_key)
                        stop_info = {}
                        for path_id, path_data in info.items():
                            for stop in path_data["stops"]:
                                if stop["stop_id"] == stop_id:
                                    stop_info.update(stop)
                                    stop_info["route_name"] = route["route_name"]
                                    path = next((p for p in paths if p["path_id"] == path_id), None)
                                    if path:
                                        stop_info["path_name"] = path["path_name"]
                        embed, map_url = make_bus_embed(stop_info)
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
                    user_id = str(interaction.user.id)
                    fav_stops = get_user_data(0, user_id, "favorite_stops", [])
                    stop_identifier = f"{route_key}:{stop_id}"
                    # limit favorites
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
                    await interaction.response.send_message(f"{action} 站牌：{stop_info.get('stop_name', 'Unknown Stop')}", ephemeral=True)

            await interaction.followup.send(embed=embed, view=ActionsView(interaction, map_url) if map_url else None)
        except Exception as e:
            await interaction.followup.send(f"發生錯誤：{e}", ephemeral=True)
            traceback.print_exc()

    @app_commands.command(name=app_commands.locale_str("youbike"), description="查詢指定的YouBike站點")
    @app_commands.describe(station_name="YouBike站點名稱")
    @app_commands.autocomplete(station_name=youbike_station_autocomplete)
    async def youbike(self, interaction: discord.Interaction, station_name: str):
        await interaction.response.defer()
        user_last_used = get_user_data(0, str(interaction.user.id), "rate_limit_last", None)
        if user_last_used:
            last_time = datetime.fromisoformat(user_last_used)
            now = datetime.utcnow()
            delta = (now - last_time).total_seconds()
            if delta < 10:
                log(f"查詢YouBike站點 {station_name} 被限速", level=logging.WARNING, module_name="TWBus", user=interaction.user, guild=interaction.guild)
                await interaction.followup.send("你操作的太快了，請稍後再試。", ephemeral=True)
                return
        set_user_data(0, str(interaction.user.id), "rate_limit_last", datetime.utcnow().isoformat())
        log(f"查詢YouBike站點 {station_name}", module_name="TWBus", user=interaction.user, guild=interaction.guild)
        try:
            info = youbike.getstationbyid(station_name)
            if not info:
                await interaction.followup.send("找不到該YouBike站點的資訊。", ephemeral=True)
                return

            embed, map_url = make_youbike_embed(info)

            class ActionsView(discord.ui.View):
                def __init__(self, interaction: discord.Interaction, url: str):
                    super().__init__()
                    self.interaction = interaction
                    self.add_item(discord.ui.Button(emoji="🗺️", url=url))
                
                async def on_timeout(self):
                    for item in self.children:
                        item.disabled = True
                    self.children[-1].disabled = False  # keep map button enabled
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
                            log(f"{interaction.user} 重新整理YouBike站點 {station_name} 被限速", level=logging.WARNING, module_name="TWBus", user=interaction.user, guild=interaction.guild)
                            await interaction.response.send_message("你操作的太快了，請稍後再試。", ephemeral=True)
                            return
                    set_user_data(0, str(interaction.user.id), "rate_limit_last", datetime.utcnow().isoformat())
                    log(f"{interaction.user} 重新整理YouBike站點 {station_name}", module_name="TWBus", user=interaction.user, guild=interaction.guild)
                    try:
                        info = youbike.getstationbyid(station_name)
                        embed, map_url = make_youbike_embed(info)
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
                    user_id = str(interaction.user.id)
                    fav_youbike = get_user_data(0, user_id, "favorite_youbike", [])
                    # limit favorites
                    fav_limit = get_user_data(0, user_id, "favorite_youbike_limit", config("default_favorite_youbike_limit", 2))
                    if station_name not in fav_youbike and len(fav_youbike) >= fav_limit:
                        await interaction.response.send_message(f"你最多只能有 {fav_limit} 個最愛 YouBike 站點。", ephemeral=True)
                        return
                    if station_name in fav_youbike:
                        fav_youbike.remove(station_name)
                        action = "已從最愛移除"
                    else:
                        fav_youbike.append(station_name)
                        action = "已加入最愛"
                    set_user_data(0, user_id, "favorite_youbike", fav_youbike)
                    await interaction.response.send_message(f"{action} YouBike 站點：{station_name}", ephemeral=True)

            await interaction.followup.send(embed=embed, view=ActionsView(interaction, map_url) if map_url else None)
        except Exception as e:
            await interaction.followup.send(f"發生錯誤：{e}", ephemeral=True)
            traceback.print_exc()
    
    @app_commands.command(name=app_commands.locale_str("favorites"), description="你的最愛站牌與YouBike站點")
    async def favorites(self, interaction: discord.Interaction):
        await interaction.response.defer()
        user_last_used = get_user_data(0, str(interaction.user.id), "rate_limit_last", None)
        if user_last_used:
            last_time = datetime.fromisoformat(user_last_used)
            now = datetime.utcnow()
            delta = (now - last_time).total_seconds()
            if delta < 10:
                log(f"{interaction.user} 查詢最愛站牌與YouBike站點被限速", level=logging.WARNING, module_name="TWBus", user=interaction.user, guild=interaction.guild)
                await interaction.followup.send("你操作的太快了，請稍後再試。", ephemeral=True)
                return
        log(f"{interaction.user} 查詢最愛站牌與YouBike站點", module_name="TWBus", user=interaction.user, guild=interaction.guild)
        try:
            user_id = str(interaction.user.id)
            fav_stops = get_user_data(0, user_id, "favorite_stops", [])
            fav_youbike = get_user_data(0, user_id, "favorite_youbike", [])

            if not fav_stops and not fav_youbike:
                await interaction.followup.send("你還沒有設定任何最愛站牌或YouBike站點。", ephemeral=True)
                return

            embed = discord.Embed(title="我的最愛", color=0x00ff00)

            # 處理最愛站牌
            for stop_identifier in fav_stops:
                try:
                    route_key, stop_id = stop_identifier.split(":")
                    route_key_int = int(route_key)
                    stop_id_int = int(stop_id)
                    paths = busapi.fetch_paths(int(route_key_int))
                    info = busapi.get_complete_bus_info(route_key_int)
                    route = busapi.fetch_route(route_key_int)[0]
                    stop_info = {}
                    for path_id, path_data in info.items():
                        for stop in path_data["stops"]:
                            if stop["stop_id"] == stop_id_int:
                                stop_info.update(stop)
                                stop_info["route_name"] = route["route_name"]
                                path = next((p for p in paths if p["path_id"] == path_id), None)
                                if path:
                                    stop_info["path_name"] = path["path_name"]
                    if stop_info:
                        title, text = make_bus_text(stop_info)
                        embed.add_field(name=title, value=text, inline=False)
                    else:
                        raise ValueError("找不到該站牌的到站資訊。")
                except Exception as e:
                    log(f"處理最愛站牌 {stop_identifier} 時發生錯誤：{e}", level=logging.ERROR, module_name="TWBus", user=interaction.user, guild=interaction.guild)
                    traceback.print_exc()
                    embed.add_field(name=f"[未知站牌]{stop_identifier}", value=f"無法取得站牌資訊：\n{str(e)}", inline=False)

            # 處理最愛YouBike站點
            global youbike_data
            for station_name in fav_youbike:
                try:
                    info = youbike.getstationbyid(station_name)
                    if info:
                        title, text = make_youbike_text(info)
                        embed.add_field(name=title, value=text, inline=False)
                    else:
                        raise ValueError("找不到該YouBike站點的資訊。")
                except Exception as e:
                    log(f"處理最愛YouBike站點 {station_name} 時發生錯誤：{e}", level=logging.ERROR, module_name="TWBus", user=interaction.user, guild=interaction.guild)
                    traceback.print_exc()
                    embed.add_field(name=f"[未知YouBike站點]{station_name}", value=f"無法取得站點資訊：\n{str(e)}", inline=False)

            await interaction.followup.send(embed=embed)

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
            busapi.update_database(info=True)
            log("公車資料庫更新完畢", module_name="TWBus")
        except Exception as e:
            log(f"更新資料庫時發生錯誤：{e}", level=logging.ERROR, module_name="TWBus")
        try:
            global youbike_data
            youbike_data = youbike.getallstations()
            log("YouBike 資料更新完畢", module_name="TWBus")
        except Exception as e:
            log(f"更新 YouBike 資料時發生錯誤：{e}", level=logging.ERROR, module_name="TWBus")
        await asyncio.sleep(3600)  # 每小時更新一次
on_ready_tasks.append(on_ready_update_database)

if __name__ == "__main__":
    start_bot()
