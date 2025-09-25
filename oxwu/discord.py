#!/usr/bin/env python3
import os
import sys
import time
import json
import requests
from datetime import datetime, timezone, timedelta

config_version = 2
config_path = 'config.json'

default_config = {
    "config_version": config_version,
    "webhook_url": "https://discord.com/api/webhooks/your_webhook_id/your_webhook_token",
    "screenshot": True,
    "report_wait_limit": 3600,
    "message_warning": "⚠️ 地震速報",
    "message_report": "📢 地震報告",
    "report_daemon": False,
    "report_link_cwa": True,
    "report_link_oxwu": True,
}
_config = None

try:
    if os.path.exists(config_path):
        _config = json.load(open(config_path, "r"))
        # Todo: verify
        if not isinstance(_config, dict):
            print("[!] Config file is not a valid JSON object, \
                resetting to default config.")
            _config = default_config.copy()
        for key in _config.keys():
            if not isinstance(_config[key], type(default_config[key])):
                print(f"[!] Config key '{key}' has an invalid type, \
                      resetting to default value.")
                _config[key] = default_config[key]
        if "config_version" not in _config:
            print("[!] Config file does not have 'config_version', \
                resetting to default config.")
            _config = default_config.copy()
    else:
        _config = default_config.copy()
        json.dump(_config, open(config_path, "w"), indent=4)
except ValueError:
    _config = default_config.copy()
    json.dump(_config, open(config_path, "w"), indent=4)

if _config.get("config_version", 0) < config_version:
    print("[+] Updating config file from version",
          _config.get("config_version", 0),
          "to version",
          config_version
          )
    for k in default_config.keys():
        if _config.get(k) is None:
            _config[k] = default_config[k]
    _config["config_version"] = config_version
    print("[+] Saving...")
    json.dump(_config, open(config_path, "w"), indent=4)
    print("[+] Done.")

def config(key, value=None, mode="r"):
    if mode == "r":
        return _config.get(key)
    elif mode == "w":
        _config[key] = value
        json.dump(_config, open(config_path, "w"), indent=4)
        return True
    else:
        raise ValueError(f"Invalid mode: {mode}")


if os.path.exists(".webhook_url"):
    with open(".webhook_url", "r") as f:
        WEBHOOK_URL = f.read().strip()
        config("webhook_url", WEBHOOK_URL, "w")
        os.remove(".webhook_url")


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
        "content": config("message_warning"),
        "embeds": [
            {
                "title": "⚠️ 地震速報",
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
        "content": config("message_report"),
        "embeds": [
            {
                "title": f'🌏 地震報告',
                "description": "中央氣象署發布地震報告",
                "color": 16733440,  # 橘黃
                "timestamp": timestamp,
                "fields": [
                    {
                        "name": "#️⃣ 編號",
                        "value": report["number"],
                        "inline": False
                    },
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


def send_webhook_embed(data: dict, screenshot: bytes=None, report=False) -> str:
    if report:
        data = report_to_embed(data)
    else:
        data = warning_to_embed(data)
    if screenshot:
        files = {"file": ("screenshot.png", screenshot, "image/png")}
        data["embeds"][0]["image"] = {"url": "attachment://screenshot.png"}
    else:
        files = {}
    resp = safe_request(
        "POST",
        config("webhook_url"),
        data={"payload_json": json.dumps(data)},
        files=files
    )
    return resp.json()["id"]


def edit_webhook_embed(message_id: str, data: dict, screenshot: bytes=None):
    url = f"{config('webhook_url')}/messages/{message_id}"
    data = warning_to_embed(data)
    if screenshot:
        files = {"file": ("screenshot.png", screenshot, "image/png")}
        data["embeds"][0]["image"] = {"url": "attachment://screenshot.png"}
        data["attachments"] = []  # clear old attachment
    else:
        files = {}
    try:
        safe_request(
            "PATCH",
            url,
            data={"payload_json": json.dumps(data)},
            files=files
        )
    except Exception as e:
        print("[!] 無法更改訊息:", str(e))


def main():
    if sys.argv[1:] and sys.argv[1] == "report":
        if config("report_daemon"):
            report = get_report_info()
            checkTime = report["report"]["time"]
            while True:
                print("[+] 等待地震報告出現...")
                report = get_report_info()
                if report["report"]["time"] != checkTime:
                    requests.get("http://127.0.0.1:10281/gotoReport")
                    data = get_report_info()
                    if config("screenshot"):
                        screenshot = screenshot_window()
                        msg_id = send_webhook_embed(report, screenshot, report=True)
                    else:
                        msg_id = send_webhook_embed(data, report=True)
                    print(f"[+] 發送成功，訊息 ID：{msg_id}")
                    checkTime = report["report"]["time"]
                    time.sleep(1)
        else:
            data = get_report_info()
            if config("screenshot"):
                screenshot = screenshot_window()
                msg_id = send_webhook_embed(data, screenshot, report=True)
            else:
                msg_id = send_webhook_embed(data, report=True)
            print(f"[+] 發送成功，訊息 ID：{msg_id}")
            return
    report = get_report_info()
    # first
    requests.get("http://127.0.0.1:10281/gotoWarning")
    data = get_warning_info()
    if config("screenshot"):
        screenshot = screenshot_window()
        msg_id = send_webhook_embed(data, screenshot)
    else:
        msg_id = send_webhook_embed(data)
    print(f"[+] 發送成功，訊息 ID：{msg_id}")

    for t in range(35 - 1, -1, -1):
        time.sleep(1)
        data = get_warning_info()
        if config("screenshot"):
            screenshot = screenshot_window()
            edit_webhook_embed(msg_id, data, screenshot)
        else:
            edit_webhook_embed(msg_id, data)
        print(f"[+] 更新成功，剩餘時間：{t} 秒")
    if config("report_daemon"):
        return
    requests.get("http://127.0.0.1:10281/gotoReport")
    print("[+] 等待地震報告出現...")
    counter = 0
    while counter < config("report_wait_limit"):
        checkReport = get_report_info()
        if report["report"]["time"] != checkReport["report"]["time"]:
            break
        time.sleep(1)
        counter += 1
    if counter >= config("report_wait_limit"):
        print("[!] 等待達到上限。")
        sys.exit(1)
    data = get_report_info()
    if config("screenshot"):
        screenshot = screenshot_window()
        msg_id = send_webhook_embed(data, screenshot, report=True)
    else:
        msg_id = send_webhook_embed(data, report=True)
    print(f"[+] 發送成功，訊息 ID：{msg_id}")


if __name__ == "__main__":
    main()
