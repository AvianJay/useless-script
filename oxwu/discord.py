#!/usr/bin/env python3
import os
import sys
import time
import json
import requests
from datetime import datetime, timezone, timedelta


if os.path.exists(".webhook_url"):
    with open(".webhook_url", "r") as f:
        WEBHOOK_URL = f.read().strip()
else:
    with open(".webhook_url", "w") as f:
        f.write("https://discord.com/api/webhooks/your_webhook_id/your_webhook_token")
    print("請在 .webhook_url 檔案中填入你的 Discord Webhook URL。")
    sys.exit(1)


def safe_request(method, url, **kwargs):
    """Anti Discord rate limit"""
    while True:
        resp = requests.request(method, url, **kwargs)
        if resp.status_code == 429 or resp.status_code == 400:  # 被限流
            data = resp.json()
            wait = data.get("retry_after", 1000) / 1000  # 毫秒 → 秒
            print(f"[!] Rate limited. 等待 {wait} 秒")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp


def get_warning_info() -> dict:
    resp = requests.get("http://127.0.0.1:10281/getWarningInfo")
    resp.raise_for_status()
    return resp.json()


def get_report_info() -> dict:
    resp = requests.get("http://127.0.0.1:10281/getReportInfo")
    resp.raise_for_status()
    return resp.json()


def get_level(argv1: str) -> str:
    """轉換震度顯示格式"""
    if "+" in argv1 or "-" in argv1:
        return argv1.replace("+", "強").replace("-", "弱")
    return argv1 + "級"


def warning_to_embed(data: dict) -> dict:
    # convert to ISO8601 UTC
    dt = datetime.strptime(data["time"], "%Y-%m-%d %H:%M:%S")
    dt_utc = dt.replace(tzinfo=timezone(timedelta(hours=8))).astimezone(timezone.utc)
    timestamp = dt_utc.isoformat().replace("+00:00", "Z")

    # format list
    list_lines = []
    for item in data.get("list", []):
        line = f'{item["id"]}. {item["time"]} | {item["latlon"]} | 深度 {item["depth"]}km | M{item["mag"]}'
        list_lines.append(line)
    list_text = "```yaml\n" + "\n".join(list_lines) + "\n```" if list_lines else "無資料"

    embed = {
        "embeds": [
            {
                "title": "🌏 地震速報",
                "description": "發生地震",
                "color": 16733440,  # 橘黃
                "timestamp": timestamp,
                "fields": [
                    {
                        "name": "📍 震央位置",
                        "value": data["location"]["text"],
                        "inline": False
                    },
                    {
                        "name": "📏 深度",
                        "value": f'{data["depth"]} 公里',
                        "inline": True
                    },
                    {
                        "name": "📊 規模",
                        "value": f'M{data["magnitude"]}',
                        "inline": True
                    },
                    {
                        "name": "⚡ 最大震度",
                        "value": data["maxIntensity"],
                        "inline": True
                    },
                    # {
                    #     "name": "📡 預估抵達",
                    #     "value": f'{data["eta"]} 秒',
                    #     "inline": True
                    # },
                    {
                        "name": "📋 警報列表",
                        "value": list_text,
                        "inline": False
                    }
                ],
                "footer": {
                    "text": "資料來源：OXWU"
                }
            }
        ]
    }

    return embed


def report_to_embed(data: dict) -> dict:
    report = data["report"]

    # 時間轉換 (UTC+8 → UTC ISO8601)
    dt = datetime.strptime(report["time"], "%Y-%m-%d %H:%M:%S")
    dt_utc = dt.replace(tzinfo=timezone(timedelta(hours=8))).astimezone(timezone.utc)
    timestamp = dt_utc.isoformat().replace("+00:00", "Z")

    # 各地震度排版
    area_fields = []
    for area in report["intensities"]:
        stations_texts = []
        for station in area["stations"]:
            names = "、".join(station["names"])
            stations_texts.append(f'{station["level"]}級: {names}')
        
        area_fields.append(
            {
                "name": f'{area["area"]} ({area["maxIntensity"]})',
                "value": "\n".join(stations_texts),
                "inline": False
            }
        )

    embed = {
        "embeds": [
            {
                "title": f'🌏 地震報告 {report["number"]}',
                "description": "中央氣象署發布地震報告",
                "color": 16733440,  # 橘黃
                "timestamp": timestamp,
                "fields": [
                    {
                        "name": "📍 震央位置",
                        "value": f'北緯 {report["latitude"]}° / 東經 {report["longitude"]}°',
                        "inline": False
                    },
                    {
                        "name": "📏 深度",
                        "value": f'{report["depth"]} 公里',
                        "inline": True
                    },
                    {
                        "name": "📊 規模",
                        "value": f'M{report["magnitude"]}',
                        "inline": True
                    },
                    {
                        "name": "⚡ 最大震度",
                        "value": report["maxIntensity"],
                        "inline": True
                    }
                ],
                "footer": {
                    "text": "資料來源：OXWU"
                }
            }
        ]
    }
    embed["embeds"][0]["fields"].extend(area_fields)

    return embed


def screenshot_window() -> bytes:
    resp = requests.get("http://127.0.0.1:10281/screenshot")
    resp.raise_for_status()
    return resp.content  # bytes


def send_webhook_embed(data: dict, screenshot: bytes, report=False) -> str:
    files = {"file": ("screenshot.png", screenshot, "image/png")}
    if report:
        data = report_to_embed(data)
    else:
        data = warning_to_embed(data)
    data["embeds"][0]["image"] = {"url": "attachment://screenshot.png"}
    resp = safe_request(
        "POST",
        WEBHOOK_URL,
        data={"payload_json": json.dumps(data)},
        files=files
    )
    return resp.json()["id"]


def edit_webhook_embed(message_id: str, data: dict, screenshot: bytes):
    url = f"{WEBHOOK_URL}/messages/{message_id}"
    files = {"file": ("screenshot.png", screenshot, "image/png")}
    data = warning_to_embed(data)
    data["embeds"][0]["image"] = {"url": "attachment://screenshot.png"}
    data["attachments"] = []  # clear old attachment
    safe_request(
        "PATCH",
        url,
        data={"payload_json": json.dumps(data)},
        files=files
    )


def main():
    if sys.argv[1:] and sys.argv[1] == "report":
        requests.get("http://127.0.0.1:10281/gotoReport")
        data = get_report_info()
        screenshot = screenshot_window()
        msg_id = send_webhook_embed(data, screenshot, report=True)
        print(f"[+] 發送成功，訊息 ID：{msg_id}")
        return
    # first
    requests.get("http://127.0.0.1:10281/gotoWarning")
    screenshot = screenshot_window()
    data = get_warning_info()
    msg_id = send_webhook_embed(data, screenshot)
    print(f"[+] 發送成功，訊息 ID：{msg_id}")

    for t in range(35 - 1, -1, -1):
        time.sleep(1)
        screenshot = screenshot_window()
        data = get_warning_info()
        edit_webhook_embed(msg_id, data, screenshot)
        print(f"[+] 更新成功，剩餘時間：{t} 秒")


if __name__ == "__main__":
    main()
