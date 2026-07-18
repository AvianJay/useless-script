# 🛡️ AntiBeast — 提及詐騙防護 管理員 自動化

AntiBeast 針對 Mr. Beast 圖片詐騙、批量提及與盜帳號機器人設計。啟用後會建立 Discord 原生 AutoMod 規則，阻擋 `@everyone`、`@here`，以及所有未被繞過的身分組提及。

不要再有野獸先生詐騙了🥀

| 指令 | 說明 |
| --- | --- |
| `/antibeast setup` | 互動式設定流程：查看說明、設定繞過身分組、設定連續觸發處置，最後啟用 |
| `/antibeast about` | 查看 AntiBeast 的功能說明 |
| `/antibeast toggle` | 啟用、停用或切換 AntiBeast |
| `/antibeast bypass` | 新增或移除繞過身分組 |
| `/antibeast settings` | 設定連續觸發處置的啟用狀態、時間窗口、觸發次數與 Moderate.py action |
| `/antibeast list` | 查看目前設定、AutoMod 規則 ID、受保護身分組與處置設定 |

## 運作方式

啟用時，AntiBeast 會同步一條專用 AutoMod keyword 規則。規則內容包含：

- `@everyone`
- `@here`
- 每個未被列入繞過清單的身分組 mention token，例如 `<@&role_id>`

同時，AntiBeast 會暫時開啟 `@everyone` 身分組的「提及 everyone/here/所有身分組」權限，讓詐騙機器人以為伺服器允許大量提及；實際訊息會由 AutoMod 規則阻擋。停用時會還原原本的 `@everyone` 權限狀態。

## 繞過身分組

繞過清單不是 Discord AutoMod 的 exempt roles。AntiBeast 的繞過代表「不要把這些身分組的 mention token 放進 keyword filter」。

適合加入繞過清單的例子：

- 需要被公告正常提及的通知身分組
- 管理員刻意保留可提及的活動身分組
- 不想由 AntiBeast 阻擋的特殊身分組

新身分組建立或刪除時，AntiBeast 會在已啟用的伺服器自動重新同步 AutoMod 規則；機器人重新啟動時也會做一次同步。

## 連續觸發處置

AntiBeast 可以監聽自己的 AutoMod rule 觸發事件。當同一位使用者在指定時間窗口內觸發達到門檻，就會執行設定好的 Moderate.py action。

預設設定：

- 時間窗口：`10` 秒
- 觸發次數：`2` 次
- 動作：`kick AntiBeast: {time_window} 秒內觸發 {trigger_count} 次`

`action` 使用 Moderate.py 的動作字串格式，可使用 `kick`、`ban`、`mute` / `timeout` / `to`、`warn`、`send_mod_message` / `smm`、`force_verify` 等。也可以用逗號分隔最多 5 個動作。

可用變數：

- `{time_window}`：執行時替換成目前設定的時間窗口秒數
- `{trigger_count}`：執行時替換成本次達標的觸發次數

範例：

```text
mute 10m AntiBeast 連續觸發, warn {user} 請勿大量提及
```

```text
ban 0 0 AntiBeast: {time_window} 秒內觸發 {trigger_count} 次, smm
```

## 權限需求

AntiBeast 需要機器人具備：

- 管理伺服器：建立、更新與讀取 AutoMod 規則
- 管理身分組：調整 `@everyone` 的提及權限

如果連續觸發處置使用 `kick`、`ban`、`mute` 或其他管理動作，機器人也需要對應的踢出、封禁、禁言等權限與足夠的身分組階級。

> **建議：**第一次設定建議使用 `/antibeast setup`。流程會先顯示說明，再設定繞過身分組與處置動作，最後才正式啟用。
