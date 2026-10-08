# 我的 discord bot
就是一些答辯。

[邀請我的機器人awa](https://discord.com/oauth2/authorize?client_id=1048398804359061585)

## 音樂節點

使用 `lava-lyra~=2.2.4`，需要 Python ≥ 3.12、Lavalink ≥ 4.2.0 或 NodeLink ≥ 3.2.0。
Lyra 會從伺服器的 `/v4/info` 自動辨識 NodeLink，不需要傳入 `nodelink=True`。
參考 [Lyra 2.2.4 節點實作](https://github.com/ParrotXray/lava-lyra/blob/v2.2.4/lava_lyra/pool.py)。

`config.json` 保留 `lavalink_nodes`，另可加入 `nodelink_nodes`；兩者共用節點池、搜尋和自動備援。
啟動時會自動補上空的 `nodelink_nodes`。將以下設定合併到 `config.json`，填入自己的節點資訊：

```json
{
    "nodelink_nodes": [
        {
            "id": "NODELINK_MAIN",
            "host": "localhost",
            "port": 2333,
            "password": "YOUR_NODE_PASSWORD",
            "name": "NodeLink",
            "secure": false,
            "lyrics": false,
            "search": true,
            "fallback": true
        }
    ]
}
```

只使用 NodeLink 時可將 `lavalink_nodes` 設為 `[]`。兩個清單中的 `id` 必須唯一；
省略時分別產生 `NODE_0`、`NODELINK_0` 等識別碼。`host` 填主機名稱或 IP，不含協定與連接埠；
HTTPS / WSS 節點使用 `secure: true`。
`lyrics`、`search`、`fallback` 可依節點調整，預設分別為 `false`、`true`、`true`；
歌詞與進階搜尋需要伺服器支援，自動備援需要至少兩個可用節點。

在 `discord/` 執行 `python -m pip install -U "lava-lyra~=2.2.4"` 後重啟機器人。

## Discord 網址預覽

首頁、`/docs`、`/privacy-policy` 與 `/terms-of-service` 會在 HTML 中直接輸出
Components V2 分享卡片，同時提供 Open Graph／Twitter metadata。網站版型不受影響。
卡片沿用網站語言設定，站內按鈕攜帶 `lang`；例如 `/docs?lang=ja` 會產生日文卡片。

- 使用現有 `website_url` 設定指定公開 HTTPS 網址。未設定或網址無效時省略 CV2，保留文字 metadata。
- `/og-image.png` 提供頭像 PNG，快取成功下載；下載最多等待 4 秒，失敗時使用內建圖片，60 秒後可重試。
- CDN／WAF 需允許 Discordbot 匿名取得這四頁及圖片，且不出現 JavaScript 驗證挑戰。
- 部署後以 Discordbot User-Agent 檢查頁面及圖片回應為 `200`，再於 Discord 桌面版與手機版驗收卡片、三語按鈕與快速入門連結。Discord 的網址快取可能延後顯示更新。

依據 [Discord Component Embeds 分支文件](https://github.com/discord/discord-api-docs/blob/anthony%2Fembed-unfurl-components/developers%2Flink-previews%2Fcomponent-embeds.mdx)；預覽規格可能調整，Discord 無法使用 CV2 時仍可使用 OG 預覽。

相關測試（在 `discord/` 執行）：

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
py -3 -m pytest tests/test_web_previews.py tests/test_i18n_web.py tests/test_i18n_catalogs.py -q
```
