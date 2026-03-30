# AutoReply - 自動回覆

AutoReply 可以依照你設定的觸發條件，自動回覆文字、Embed、貼圖、反應、延遲訊息，或搭配變數做條件判斷。

> 注意
> 每個 guild 的 AutoReply 目前有 `1 秒最多 3 次` 的觸發限流。
> 如果模板語法錯誤，該次回覆會直接變成空字串 `""`，不會送出。

## 指令

- `/autoreply add`：新增一條自動回覆
- `/autoreply builder`：用互動式 builder 一步一步建立規則
- `/autoreply remove`：移除一條自動回覆
- `/autoreply edit`：編輯既有規則
- `/autoreply quickadd`：補 trigger / response 到既有規則
- `/autoreply list`：列出目前所有規則
- `/autoreply clear`：清空全部規則
- `/autoreply test`：測試模板替換結果
- `/autoreply template`：套用內建範本包
- `/autoreply export`：匯出 JSON
- `/autoreply import`：匯入 JSON
- `/autoreply ignore`：設定忽略頻道
- `/autoreply help`：顯示 Discord 內說明

## Builder

`/autoreply builder` 會開一個互動式介面，讓你直接調整：

- 觸發字
- 回覆內容
- 觸發模式
- 是否使用 reply
- 頻道限制模式
- 指定頻道
- 觸發機率

Builder 內的 `trigger / response` 支援：

- 一行一個項目
- 如果只寫一行，也可以用 `,` 分隔多個項目

## 觸發模式

- `contains`：訊息只要包含其中一個 trigger 就觸發
- `equals`：訊息必須和 trigger 完全相同
- `starts_with`：訊息開頭符合 trigger 才觸發
- `ends_with`：訊息結尾符合 trigger 才觸發
- `regex`：使用 Python regex 比對

## 基本變數

- `{user}`：觸發者 mention
- `{content}`：原始訊息內容
- `{guild}` / `{server}`：伺服器名稱
- `{channel}`：頻道名稱
- `{author}` / `{member}`：觸發者名稱
- `{authorid}`：觸發者 ID
- `{authoravatar}`：觸發者頭像 URL
- `{role}`：觸發者最高身分組名稱
- `{id}`：觸發者 ID
- `{null}`：空字串，可拿來做 `if` 比較
- `\n`：換行
- `\t`：Tab

## 日期與時間

- `{date}`：`YYYY/MM/DD`
- `{year}` / `{month}` / `{day}`
- `{time}`：12 小時制，例如 `下午 08:23`
- `{time24}`：24 小時制，例如 `20:23`
- `{hour}` / `{minute}` / `{second}`
- `{timemd:t}` ~ `{timemd:R}`：輸出 Discord timestamp 標記

> 注意
> 日期 / 時間變數使用機器人主機本地時區。
> 目前部署環境是 `Asia/Taipei`，也就是 `UTC+8`。

## 內容切片

- `{contentsplit:0}`：等於 `content.split()[0]`
- `{contentsplit:1}`：取第 2 個單字
- `{contentsplit:1-}`：從索引 `1` 取到結尾
- `{contentsplit:-4}`：從開頭取到索引 `4`
- `{contentsplit:1-2}`：取索引 `1` 到 `2`

> 注意
> 範圍結尾是包含的。
> 例如 `1-2` 會拿到第 2 和第 3 個單字。

## 數學

- `{math:(1+2*3)}`
- 只支援 `+ - * /`
- 支援括號
- 支援在 `math` 內先套用其他變數

例如：

```text
{math:(10/4)}
{math:({contentsplit:1}+5)}
{math:({hour}+1)}
```

限制：

- 數字字面值必須在 `-1000 ~ 1000`
- 除以 0 或非法算式會視為語法錯誤

## 隨機 / 互動 / 狀態變數

- `{random}`：1 到 100 的隨機數
- `{randint:min-max}`：指定範圍整數
- `{random_user}`：從最近訊息中抽一位成員
- `{react:emoji}`：對觸發訊息加反應
- `{sticker:ID}`：附上貼圖
- `{newmsg:second}`：延遲送出下一則訊息
- `{edit:second}`：延遲編輯目前這則 autoreply
- `{uservar:key}`：讀取 user 變數
- `{uservar:key:value}`：寫入 user 變數，不輸出
- `{guildvar:key}`：讀取 guild 變數
- `{guildvar:key:value}`：寫入 guild 變數，不輸出

限制：

- `{newmsg:second}`：最多 `2` 個，秒數只能 `1 ~ 3`
- `{edit:second}`：最多 `4` 個，秒數只能 `1 ~ 3`
- `uservar` 最多 `5` 個 key
- `guildvar` 最多 `10` 個 key
- key / value 長度最多 `100`

## 條件判斷

支援三種寫法：

```text
{if:條件:成立內容:else:不成立內容}
{if:條件:成立內容:不成立內容}
{if:條件:成立內容}
```

支援運算子：

- `==`
- `!=`
- `<=`
- `>=`
- `&&`
- `||`

規則：

- `&&` 會先算，再算 `||`
- `true / false` 會當布林值
- 純數字會當數字比較
- 其他內容會當字串比較

例如：

```text
{if:{contentsplit:1}==true:You sent true!:else:Its false}
{if:{contentsplit:1}!={null}:你有輸入內容:else:空白}
{if:{contentsplit:2}>=10:大於等於 10:else:小於 10}
{if:{contentsplit:1}==true&&{hour}>=12:午安 true:else:還不是午安時間}
```

## Embed 回覆

- `{embedtitle:Title}`
- `{embeddescription:Description}`
- `{embedurl:URL}`
- `{embedimage:SomeLink}`
- `{embedthumbnail:SomeLink}`
- `{embedcolor:57F287}`
- `{embedfooter:Footer}`
- `{embedfooterimage:SomeLink}`
- `{embedauthor:Author}`
- `{embedauthorurl:URL}`
- `{embedauthorimage:SomeLink}`
- `{embedtime:true}`
- `{embedfield:FieldName:Content}`

內容可以再使用其他 `{}` 變數。

例如：

```text
{embedtitle:簽到成功}
{embedurl:https://example.com}
{embeddescription:{user} 在 {date} {time24} 完成簽到}
{embedauthor:系統}
{embedauthorurl:https://example.com/profile}
{embedauthorimage:{authoravatar}}
{embedcolor:57F287}
{embedfield:伺服器:{guild}}
{embedfooter:AutoReply Template}
{embedfooterimage:{authoravatar}}
{embedtime:true}
```

## 頻道限制

你可以從兩個地方限制觸發頻道：

- `/autoreply ignore`
- 規則本身的 `channel_mode`

`channel_mode`：

- `all`：所有頻道都可觸發
- `whitelist`：只有指定頻道可觸發
- `blacklist`：指定頻道不會觸發

## 內建範本包

- `daily_greetings`：早安 / 午安 / 晚安 / 安安
- `mini_commands`：`!say` / `!time` / `!date` / `!roll`
- `chat_fun`：簽到、抽人、反應、小互動

## 常見範例

### 基本招呼

```text
模式: contains
觸發字: 哈囉, hello
回覆: 哈囉 {user}
```

### `!say aaa`

```text
模式: starts_with
觸發字: !say
回覆: {contentsplit:1-}
```

### 早安判斷

```text
模式: starts_with
觸發字: 早安
回覆: {if:{hour}>=5:{if:{hour}<=11:早安 {user}:else:現在都 {time24} 了還早安}}
```

### 條件分支

```text
模式: starts_with
觸發字: check
回覆: {if:{contentsplit:1}==true:你輸入了 true:else:你沒有輸入 true}
```

### 條件簡寫

```text
{if:{contentsplit:1}==true:Yes:No}
```

### Embed 簽到

```text
模式: equals
觸發字: 簽到
回覆: {embedtitle:簽到成功}{embeddescription:{user} 在 {date} {time24} 完成簽到}{embedcolor:57F287}{embedfield:伺服器:{guild}}
```

### 延遲訊息 / 編輯

```text
模式: equals
觸發字: 倒數
回覆: 3...{edit:1}2...{edit:2}1...{newmsg:3}開始
```

### 記住 user 變數

```text
模式: starts_with
觸發字: !pet
回覆: {uservar:pet:{contentsplit:1-}}已記住你的寵物
```

```text
模式: equals
觸發字: !mypet
回覆: 你記住的寵物是：{uservar:pet}
```

### 數學 + 變數

```text
模式: starts_with
觸發字: 算
回覆: 答案：{math:({contentsplit:1}+5)}
```

## 測試與除錯

- 先用 `/autoreply test` 看模板替換結果
- `starts_with` 搭配 `{contentsplit:1-}` 很適合做簡單指令
- 如果完全沒回覆，先檢查：
  - 模板有沒有語法錯誤
  - 頻道限制有沒有擋到
  - 是否撞到 `1 秒 3 次` 的限流
