"""重建 assets/discord_emoji_shortcodes.json（Discord 用戶端的 emoji 短碼表）。

    python -m tools.update_discord_emoji

資料來源是 Emzi0767 從 Discord 用戶端抽出的 emoji 定義。只保留 :name: 形式的名稱，
膚色變體另外以「基底 emoji -> 五種膚色」存放，:name_tone2: 與 :name::skin-tone-2:
都由 ai_emoji 依基底名稱查表。
"""
from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path

SOURCE_URL = "https://emzi0767.gl-pages.emzi0767.dev/discord-emoji/discordEmojiMap.json"
OUTPUT = Path(__file__).resolve().parents[1] / "assets" / "discord_emoji_shortcodes.json"
NAME_PATTERN = re.compile(r"[a-z0-9_+\-]+")
TONE_PATTERN = re.compile(r"(.+)_tone([1-5])")


def build(data: dict) -> dict:
    definitions = data["emojiDefinitions"]
    emoji: dict[str, str] = {}
    tone_variants: list[tuple[str, int, str]] = []
    for definition in definitions:
        tone = TONE_PATTERN.fullmatch(definition["primaryName"])
        if tone:
            tone_variants.append((tone.group(1), int(tone.group(2)), definition["surrogates"]))
            continue
        for name in definition["names"]:
            if NAME_PATTERN.fullmatch(name):
                emoji.setdefault(name, definition["surrogates"])

    tones: dict[str, list[str | None]] = {}
    for base_name, tone, surrogates in tone_variants:
        base = emoji.get(base_name)
        if base is None:
            continue
        tones.setdefault(base, [None] * 5)[tone - 1] = surrogates
    return {
        "source": SOURCE_URL,
        "version": data.get("version"),
        "emoji": emoji,
        "tones": {base: variants for base, variants in tones.items() if all(variants)},
    }


def main():
    with urllib.request.urlopen(SOURCE_URL, timeout=60) as response:
        data = json.load(response)
    result = build(data)
    OUTPUT.write_text(
        json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"wrote {len(result['emoji'])} names and {len(result['tones'])} tone sets to {OUTPUT}")


if __name__ == "__main__":
    main()
