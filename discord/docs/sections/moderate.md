# 🛡️ Moderate — 管理工具 管理員

核心管理模組，提供完整的使用者管理工具。支援時間字串解析（如 `1h`、`30m`、`7d`），並可一次對多位使用者執行操作。所有懲處動作會自動整合通知系統。

| 指令 | 說明 |
| --- | --- |
| `/moderate` | 開啟管理選單，可選擇踢出、封禁、禁言、黑名單等動作 |
| `y!moderate` `y!m` | 前綴版管理指令 |
| `y!moderate_reply` `y!mr` | 對被回覆訊息進行管理 |
| `/action-builder` | 建立自訂的管理動作組合 |
| `/send-moderation-message` | 手動發送懲處公告 |

> **時間格式：**支援 `s` / `秒`、`m` / `分鐘`、`h` / `小時`、`d` / `天`、`w` / `週`、`M` / `月`、`y` / `年`，可組合使用如 `1d12h`。

## 動作指令字串 (Action String)

`y!moderate` 和 `/multi-moderate` 的 `action` 參數使用動作指令字串格式，可用逗號 `,` 分隔多個動作（最多 5 個）。也可使用 `/action-builder` 透過互動介面產生指令字串。

| 動作 | 格式 | 說明 |
| --- | --- | --- |
| `ban` | `ban <時長> <刪除訊息時長> <原因>` | 封禁用戶。時長為 `0` 表示永久，刪除訊息時長 `0` 表示不刪除 |
| `kick` | `kick <原因>` | 踢出用戶 |
| `mute` | `mute <時長> <原因>` | 禁言用戶，預設 10 分鐘。也可使用 `timeout` |
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
