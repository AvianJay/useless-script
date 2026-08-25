# 🛡️ Moderate — 管理工具 管理員

核心管理模組，提供完整的使用者管理工具。支援時間字串解析（如 `1h`、`30m`、`7d`），並可一次對多位使用者執行操作。所有懲處動作會自動整合通知系統。

| 指令 | 說明 |
| --- | --- |
| `/moderate` | 開啟管理選單，可選擇踢出、封禁、禁言、黑名單等動作 |
| `y!moderate` `y!m` | 前綴版管理指令 |
| `y!moderate_reply` `y!mr` | 對被回覆訊息進行管理 |
| `/action-builder` | 建立自訂的管理動作組合 |
| `/send-moderation-message` | 手動發送懲處公告 |
| `/moderation-message-channel` | 設定懲處公告頻道；建議同時授予讀取訊息歷史權限以接續其他機器人的裁判字號 |
| `/moderation-message-format` | 編輯、預覽或重設公告模板與裁判字號格式 |
| `/custom-action-add` | 新增或更新伺服器自訂管理動作 |
| `/custom-action-remove` | 刪除伺服器自訂管理動作 |
| `/custom-action-list` | 查看伺服器自訂管理動作 |

> **時間格式：**支援 `s` / `秒`、`m` / `分鐘`、`h` / `小時`、`d` / `天`、`w` / `週`、`M` / `月`、`y` / `年`，可組合使用如 `1d12h`。

## 動作指令字串 (Action String)

`y!moderate` 和 `/multi-moderate` 的 `action` 參數使用動作指令字串格式，可用逗號 `,` 分隔多個動作（最多 5 個）。也可使用 `/action-builder` 透過互動介面產生指令字串。

| 動作 | 格式 | 說明 |
| --- | --- | --- |
| `ban` | `ban <時長> <刪除訊息時長> <原因>` | 封禁用戶。時長為 `0` 表示永久，刪除訊息時長 `0` 表示不刪除 |
| `kick` | `kick <原因>` | 踢出用戶 |
| `mute` | `mute <時長> <原因>` | 禁言用戶，預設 10 分鐘。也可使用 `timeout` 或 `to` |
| `unban` | `unban <原因>` | 解封用戶 |
| `unmute` | `unmute <原因>` | 解除禁言。也可使用 `untimeout` |
| `delete` | `delete <警告訊息>` | 刪除訊息，可附帶公開警告（`{user}` 代表用戶） |
| `warn` | `warn <警告訊息>` | 在頻道中發送公開警告 |
| `send_mod_message` | `send_mod_message` | 發送懲處公告到設定的公告頻道。也可使用 `smm` |
| `force_verify` | `force_verify <時長>` | 強制用戶進行網頁驗證（需啟用 ServerWebVerify） |

> **範例：**
> `ban 7d 1d 違規發言` — 封禁 7 天，刪除最近 1 天的訊息
> `mute 30m 注意行為, warn {user} 請注意你的發言` — 禁言 30 分鐘並公開警告
> `delete_dm 你的訊息已被刪除, mute 1h 違規內容` — 刪除訊息 + 私訊通知 + 禁言 1 小時
> `ban 0 0 嚴重違規, smm` — 永久封禁並發送懲處公告

## 自訂管理動作參數

自訂動作可使用 `{1}` 至 `{9}` 取得呼叫時的位置參數。`{1}` 是必要參數；`{1:預設值}` 在未提供第 1 個參數時使用 fallback。參數必須從 1 連續編號，逗號固定作為動作分隔符。

```text
/custom-action-add name:spam action:mute {1:10m} {2:洗版}, smm
spam 30m "重複洗版"
```

上例會展開成 `mute 30m 重複洗版, smm`。引號可將含空白的文字當成一個參數；缺少必要參數、多餘參數、循環引用或展開後超過 5 個動作時，整組操作會在執行前取消。

## 懲處公告模板

未設定時會使用目前的 Markdown 公告格式。管理員可透過 `/moderation-message-format` 或伺服器網頁面板編輯模板，模板可產生一般文字、單一 Embed，或兩者混合。

常用變數：

- `{user}`、`{user_name}`、`{user_id}`、`{user_avatar}`
- `{moderator}`、`{moderator_name}`、`{moderator_id}`、`{moderator_avatar}`
- `{reason}`、`{action}`、`{case_id}`
- `{guild}`、`{guild_id}`、`{guild_icon}`
- `{reported_message}`、`{report_context}`、`{ai_note}`（只在 ReportSystem 情境有內容）

支援與 AutoReply 相同的 Embed 指令子集：`{embedtitle:...}`、`{embeddescription:...}`、`{embedurl:...}`、`{embedimage:...}`、`{embedthumbnail:...}`、`{embedcolor:57F287}`、`{embedfooter:...}`、`{embedfooterimage:...}`、`{embedauthor:...}`、`{embedauthorurl:...}`、`{embedauthorimage:...}`、`{embedtime:true}`、`{embedfield:欄位名:內容}`。懲處公告不執行 AutoReply 的反應、條件、延遲、數學或狀態變數。

```text
{embedtitle:⛔ 違規處分}
{embeddescription:被處分者：{user}\n原因：{reason}\n結果：{action}}
{embedfield:裁判字號:{case_id}}
{embedfield:執行管理員:{moderator}}
{embedcolor:ED4245}
```

### 裁判字號與多機器人接續

預設字號格式為 `{roc_year}{sequence:04d}`，也可使用 `{year}`、`{roc_year}`、`{sequence}`，例如 `CASE-{year}-{sequence:04d}`。格式必須包含 `{sequence}`。

產生新字號時，bot 會讀取公告頻道最多 1000 則歷史訊息，從最新開始搜尋純文字及 Embed 的標題、描述、欄位、作者與 footer。辨識時先套用目前的裁判字號格式，再相容舊式 `裁判字號：1150001`；同一則訊息若含有互相衝突的多個字號會略過。搜尋不限制訊息作者，因此其他機器人使用相同格式時也能接續。找不到、缺少讀取歷史權限或格式無法辨識時，才使用本 bot 保存的伺服器狀態。

> 不同機器人沒有共用鎖；若多個機器人在幾乎同一時間產生公告，仍可能讀到同一個前號。要保證跨機器人完全不撞號，所有機器人必須共用同一個資料庫或鎖服務。
