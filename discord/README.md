# 我的 discord bot
就是一些答辯。

[邀請我的機器人awa](https://discord.com/oauth2/authorize?client_id=1048398804359061585)

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
