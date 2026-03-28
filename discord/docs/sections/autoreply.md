# AutoReply - 自動回覆

管理員可設定關鍵字觸發自動回覆。支援正則表達式、貼圖回覆、表情反應、機率觸發、頻道忽略、條件判斷與 Embed 回覆。

> 提示：同一個 guild 的 AutoReply 目前為每 1 秒最多 3 條。超過時會直接略過，不會排隊補發。

## 指令

- `/autoreply add`：新增自動回覆規則
- `/autoreply remove`：移除自動回覆規則
- `/autoreply edit`：編輯現有規則
- `/autoreply quickadd`：快速補 trigger 或 response 到既有規則
- `/autoreply list`：列出所有規則
- `/autoreply clear`：清空所有規則
- `/autoreply test`：測試模板變數替換效果
- `/autoreply template`：從預設模板快速建立規則
- `/autoreply export`：匯出 JSON
- `/autoreply import`：匯入 JSON
- `/autoreply ignore`：設定模組層級的忽略或白名單頻道
- `/autoreply help`：查看 Discord 內說明

## 觸發模式

- `contains`：訊息包含關鍵字就觸發
- `equals`：訊息完全相同才觸發
- `starts_with`：訊息以前綴開頭時觸發
- `ends_with`：訊息以指定字尾結束時觸發
- `regex`：使用 Python regex 比對

> 提示：觸發字串與回覆內容都支援使用逗號 `,` 分隔多個值。多個 trigger 是「任一即觸發」，多個 response 會「隨機挑一個」。

## 基本變數

- `{user}`：提及觸發者
- `{content}`：觸發訊息完整內容
- `{guild}` / `{server}`：伺服器名稱
- `{channel}`：頻道名稱
- `{author}` / `{member}`：觸發者名稱
- `{role}`：觸發者最高身份組名稱
- `{id}`：觸發者 ID
- `\n`：換行
- `\t`：Tab

## 日期、時間與內容切割

- `{date}`：目前日期，格式為 `YYYY/MM/DD`
- `{year}` / `{month}` / `{day}`：年、月、日
- `{time}`：12 小時制時間
- `{time24}`：24 小時制時間
- `{hour}` / `{minute}` / `{second}`：時、分、秒
- `{timemd:t}` 到 `{timemd:R}`：產生 Discord 時間戳標記
- `{contentsplit:0}`：等同 `content.split()[0]`
- `{contentsplit:1}`：取第二個單字，依此類推

> 時區：`{date}`、`{time}`、`{timemd:...}` 目前跟隨機器人主機本地時區。

## 隨機與進階語法

- `{random}`：隨機產生 1 到 100 的整數
- `{randint:min-max}`：隨機產生指定範圍整數
- `{random_user}`：從最近 50 則訊息中隨機選一位非機器人使用者
- `{react:emoji}`：對觸發訊息加表情反應，不會產生文字回覆
- `{sticker:ID}`：傳送指定 ID 的伺服器貼圖

> 取得貼圖 ID 可使用 `y!sticker`。

## 條件判斷

- `{if:左邊==右邊:成立內容:else:不成立內容}`：完整 if/else
- `{if:左邊==右邊:成立內容:不成立內容}`：簡寫版 if/else
- `{if:條件:成立內容}`：只在成立時輸出內容
- 支援 `==`、`!=`、`<=`、`>=`

> `true` / `false` 會視為布林值，純數字會視為數值，其餘內容用字串比較。條件內外都可以繼續使用其他 `{}` 變數。

### 條件判斷範例

```text
{if:{contentsplit:1}==true:你輸入了 true:else:你沒有輸入 true}
{if:{author}!=Admin:不是管理員:else:是管理員}
{if:{contentsplit:2}>=10:合格:else:未達標}
```

## Embed 回覆

如果你想問「自動回覆怎麼做 embed」，核心就是把多個 `embed` token 串在同一段 response 裡。

- `{embedtitle:文字}`：設定 Embed 標題
- `{embeddescription:文字}`：設定 Embed 內容
- `{embedimage:連結}`：設定大圖
- `{embedthumbnail:連結}`：設定縮圖
- `{embedcolor:HEX}`：設定顏色，可用 `57F287`、`#57F287`、`0x57F287`
- `{embedfooter:文字}`：設定 footer
- `{embedauthor:文字}`：設定 author
- `{embedtime:true}`：顯示目前時間
- `{embedfield:欄位名:欄位值}`：新增欄位，可重複使用多次

> 提示：Embed 裡面的文字也會再跑一次變數替換，所以可以混用 `{user}`、`{date}`、`{if:...}` 等語法。

### 最小 Embed 範例

```text
{embedtitle:簽到成功}{embeddescription:{user} 在 {date} {time24} 完成簽到}{embedcolor:57F287}
```

### 帶欄位的 Embed 範例

```text
{embedtitle:簽到成功}{embeddescription:{user} 在 {date} {time24} 完成簽到}{embedcolor:57F287}{embedfield:伺服器:{guild}}
```

### 帶條件的 Embed 範例

```text
{embedtitle:檢查結果}{embeddescription:{if:{contentsplit:1}==true:條件成立:else:條件不成立}}{embedcolor:#5865F2}
```

## 頻道控制

AutoReply 有兩層頻道控制：

- 模組層級：`/autoreply ignore`
- 單條規則：`/autoreply add` 或 `/autoreply edit` 的 `channel_mode`

> 如果模板語法錯誤，例如括號不成對、`{if:...}` 格式不合法、`{contentsplit:...}` 參數不正確，系統會直接輸出空字串。

## 實用範例

### 歡迎訊息

```text
模式：contains
觸發：大家好, 我是新來的
回覆：歡迎 {user} 來到 {guild}！🎉
```

### 抽獎

```text
模式：equals
觸發：抽獎
回覆：{user} 的抽獎號碼是 {randint:1000-9999}！
```

### 只加反應

```text
模式：contains
觸發：好耶
回覆：{react:🎉}{react:👍}
```

### 小指令式查詢

```text
模式：starts_with
觸發：!say 
回覆：你剛剛說的是：{contentsplit:1}
```

### 早安判斷

```text
模式：starts_with
觸發：早安
回覆：{if:{hour}>=5:{if:{hour}<=11:早安 {user}，記得先喝水再開始今天。:現在都 {time24} 了，這句早安是不是送得有點晚。}:現在才 {time24}，你這不是早安，是熬夜安。}
```

## 建議用法

- 先用 `/autoreply test` 驗證模板結果，再正式存成規則
- 問題偏複雜時，先從純文字回覆做起，再逐步加上 `{if:...}` 或 `embed` token
- 如果要做像小指令一樣的行為，優先搭配 `starts_with` 與 `{contentsplit:n}`
