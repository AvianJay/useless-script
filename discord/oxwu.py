#!/usr/bin/env python3
import os
import sys
import time
import json
import requests
from datetime import datetime

WEBHOOK_URL = "https://discord.com/api/webhooks/1418184380257665054/YBWwiwnIvvmsdSRWPFMZRaKNIOXzcJVahEUZiqSwn8GSE6TDiitJRioWzofb45ipyruo"


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


def get_level(argv1: str) -> str:
    """轉換震度顯示格式"""
    if "+" in argv1 or "-" in argv1:
        return argv1.replace("+", "強").replace("-", "弱")
    return argv1 + "級"


def screenshot_window() -> bytes:
    resp = requests.get("http://127.0.0.1:10281/screenshot")
    resp.raise_for_status()
    path = resp.text.strip()

    if not os.path.exists(path):
        raise FileNotFoundError(f"找不到截圖檔案: {path}")

    with open(path, "rb") as f:
        return f.read()


def send_webhook_embed(level: str, sec: int, screenshot: bytes, timestamp) -> str:
    files = {"file": ("screenshot.png", screenshot, "image/png")}
    data = {
        "embeds": [
            {
                "title": "地震速報",
                "description": f"{level}地震，{sec}秒後抵達。",
                "image": {"url": "attachment://screenshot.png"},
                "timestamp": datetime.utcnow().isoformat()
            }
        ]
    }
    resp = safe_request(
        "POST",
        WEBHOOK_URL,
        data={"payload_json": json.dumps(data)},
        files=files
    )
    return resp.json()["id"]


def edit_webhook_embed(message_id: str, level: str, sec: int, screenshot: bytes):
    url = f"{WEBHOOK_URL}/messages/{message_id}"
    files = {"file": ("screenshot.png", screenshot, "image/png")}
    data = {
        "embeds": [
            {
                "title": "地震速報",
                "description": f"{level}地震，{sec}秒後抵達。",
                "image": {"url": "attachment://screenshot.png"},
                "timestamp": datetime.utcnow().isoformat()
            }
        ],
        "attachments": []  # 清空之前的附件
    }
    safe_request(
        "PATCH",
        url,
        data={"payload_json": json.dumps(data)},
        files=files
    )


def main():
    if len(sys.argv) < 3:
        print("用法: python script.py [震度] [秒數]")
        sys.exit(1)

    level = get_level(sys.argv[1])
    sec = int(sys.argv[2])
    timestamp = datetime.utcnow().isoformat()

    # 第一次發送
    screenshot = screenshot_window()
    msg_id = send_webhook_embed(level, sec, screenshot, timestamp)

    # 倒數更新
    for t in range(30 - 1, -1, -1):
        time.sleep(1)
        screenshot = screenshot_window()
        edit_webhook_embed(msg_id, level, sec, screenshot, timestamp)


if __name__ == "__main__":
    main()
