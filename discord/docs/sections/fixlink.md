# 🔗 FixLink — 連結修復 管理員自動化

FixLink 會偵測伺服器中的內建與管理員設定的自訂平台連結，產生可在 Discord 正常顯示的修復連結。功能預設為停用。

| 指令 | 說明 |
| --- | --- |
| `/fixlink settings` | 開啟互動式設定面板 |

## 發送模式

- **一般回覆**：由 bot 回覆原始連結、作者頁與可用的 fixer。若原連結放在 `||...||` 暴雷標記內，修復回覆也會保留暴雷；7 秒後若修復訊息沒有預覽會自動刪除，預覽成功時會收起原訊息的 embed。
- **Webhook 替換**：使用發文者名稱與頭像重送修復後的訊息。若 7 秒後沒有預覽，會將 Webhook 訊息改回原文；完成後才刪除原訊息。
- Webhook 模式可設為「全部連結」或「僅追蹤碼」；後者只在至少一個支援連結含額外 query 或 fragment 時替換，乾淨連結仍使用一般回覆。
- 平台識別內容必需的 query 不算追蹤碼，例如 YouTube `v`、`t`、Instagram `img_index` 與 Bilibili `p`。
- Webhook 訊息會附上持久化型刪除按鈕，只有原訊息作者可使用。
- Webhook 無法保留回覆關係、貼圖、投票或語音訊息時，會保留原文並改用一般回覆。

## 內建平台

- 支援 22 個內建平台：Threads、Twitter/X、Instagram、TikTok、Reddit、Facebook、Bilibili、Pixiv、Pinterest、YouTube、Twitch、Bluesky、Spotify、Mastodon、Tumblr、DeviantArt、Imgur、Weibo、Newgrounds、PTT、Roblox 與 Fur Affinity。
- Spotify、Mastodon、Tumblr、Imgur 與 YouTube 預設停用，可由設定面板逐一開啟。
- 設定面板可選擇內建平台，分別啟用、停用及指定 Webhook 使用的主要 fixer；一般模式會列出該平台所有可用 fixer。
- 只採用程式內建的精確來源網域、路徑規則與 HTTPS fixer，不包含 Awesome Fixers 清單中的非官方惡搞網域或已棄用服務。
- 服務選擇參考 [FixTweetBot Awesome Fixers](https://github.com/Kyrela/FixTweetBot#awesome-fixers)。

## Threads

- 支援 `threads.com`、`threads.net`、`/@user/post/id` 及 `/share/code` 連結。
- 一般模式同時顯示 FzThreads 與 FixEmbed；Webhook 模式可選擇主要服務。
- `/share/code` 會受限制地解析至 Threads 正式文章網址，再產生 FixEmbed 連結。
- 啟用移除追蹤後，會刪除 Threads 網址的 query 與 fragment。

其他內建平台啟用移除追蹤後，只會保留識別內容必需的 query，例如 YouTube `v`、Instagram `img_index` 或 Bilibili `p`。

## 自訂平台

- 每個伺服器最多 10 個，以精確來源網域與路徑前綴匹配。
- 只允許結構化 query fixer：HTTPS endpoint、來源 URL 參數名與靜態 query。
- 不接受 wildcard、regex、IP endpoint、`{url}` 範本或網域替換。
- 啟用移除追蹤時，可設定必須保留的來源 query keys；未設定時會保留全部 query。
- 「僅追蹤碼」Webhook 只會對已設定 `keep_query_keys` 的自訂平台判定額外 query；未設定保留鍵時不會猜測哪些參數是追蹤碼。

## 略過處理

FixLink 只處理真人發送的訊息。Bot、Webhook 及使用 `<https://example.com/...>` 包住的連結都不會被處理。
