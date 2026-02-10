# 更新日誌

## 0.18.2
### 更新內容如下：
* 更新 /item use
  * 修復多個物品無法使用的問題。
* 更新 /economy
  * 改變了貨幣的單位。

## 0.18.1
### 更新內容如下：
* 更新生成訊息圖片相關功能
  * 新增有料按鈕
    * 當點下按鈕的人超過 5 人，將傳送到官方頻道。
  * 移除投稿提示。
* 更新 /automoderate
  * 修復 scamtrap 無法觸發的問題。
* 修復一些 Bug。

## 0.18.0
### 更新內容如下：
* 新增經濟系統 | /economy
  * 非常牛逼的經濟系統。
  * 與物品系統整合。
* 新增黏黏的身分組 | /stickyrole
  * 管理員可以使用指令設定哪些身分組。
  * 用戶退出再加入後若先前有指定的身分組則會自動恢復。
* 更新 /music
  * 改為支援多個音樂節點。
  * 新增 `/music shuffle`：隨機播放。
  * 新增節點狀態檢查。
  * 新增相同語音頻道檢查。
* 更新申訴系統
  * 允許多次申訴。
* 更新 /ai
  * 調整 AI 提示詞。
* 新增相關翻譯。
* 修復一些 Bug。

## 0.17.7
### 更新內容如下：
* 更新 /ai
  * 更新提示詞讓他更抽象了🥀
  * 給AI看頻道訊息的能力(僅 5 則訊息)。
* 更新 /music
  * 新增 5 分鐘自動離開語音頻道功能。
  * y!play 現在若沒有指定參數會當作繼續(resume)使用。
* 更新 y!setprefix
  * 新增小提示說明。
* 修復一些 Bug。

## 0.17.6
### 更新內容如下：
* 更新 /ai
  * AI 模型改為使用 `openai-fast` 並從遠端獲取模型。
  * 改用 `client.chat.completions.create` API。
  * 從 GitHub 獲取 g4f。
* 更新 /report
  * 調整檢舉文字。
* 更新 /help
  * 文字指令幫助現在只能在伺服器中使用。
* 修復多個 DeprecationWarning。
* 修復一些 Bug。

## 0.17.5
### 更新內容如下：
* 更新 /moderate 相關發布公告功能
  * 有個傻逼忘記加 await 了 🥀
* 修復一些 Bug。

## 0.17.4
### 更新內容如下：
* 新增 /help 應用指令
  * 可以查看應用指令及文字指令的說明。
* 更新 /earthquake
  * 新增 CWA（中央氣象署）連結和圖片顯示。
  * 更新 /earthquake set-alert-channel 和 /earthquake set-report-channel
    * 現在可以不指定頻道來取消設定。
* 新增動態語音投稿功能
  * 用戶現在可以透過 /contribute dynamic-voice 投稿自定義語音。
* 更新 /get-command-mention
  * 修復避免收到 429 速率限制錯誤的邏輯。
* 新增相關翻譯。
* 修復一些 Bug。

## 0.17.3
### 更新內容如下：
* 新增 `y!stickerinfo`
  * 可以查看貼圖的詳細資訊。
* 更新自動回覆 | /autoreply
  * 新增貼圖回覆功能，使用 `{sticker:貼圖ID}` 來回覆貼圖。
  * 新增 /autoreply ignore：設定全域忽略的頻道（例如公告頻道）。
* 更新 /r34
  * 新增 AI 標籤提示。
* 更新 /stats
  * 修復可能導致 400 錯誤的問題。
* 美化 `y!help` 指令顯示。
* 修復物品系統可能導致 MemoryError 的問題。
* 修復 Ctrl+C 無法正常關閉機器人的問題。
* 修復「Task was destroyed but it is pending!」錯誤。
* 改進日誌系統錯誤處理。
* 修復一些 Bug。

## 0.17.2
### 更新內容如下：
* 新增 /earthquake（OXWU 地震監測系統）
  * `/earthquake set-alert-channel`：設定地震速報通知頻道
  * `/earthquake set-report-channel`：設定地震報告通知頻道
  * `/earthquake query-warning`：手動取得最新地震速報
  * `/earthquake query-report`：手動取得最新地震報告
  * 支援自動推送地震速報與報告。
* 更新 /music
  * 新增影片縮圖顯示。
* 修復日誌系統顯示問題。
* 修復一些 Bug。

## 0.17.1
### 更新內容如下：
* 更新 /ai
  * AI 模型改為使用 `openai`。
  * 新增 Discord 提及文字處理，AI 現在可以正確理解提及的用戶、頻道、角色等。
  * AI 現在會知道目前與其對話的用戶名稱。
  * 新增安全防護：禁止 AI 回應中的 @everyone 和 @here 提及。
  * 更新系統提示詞，要求遵守 Discord 使用條款。
  * 允許抽象笑話。
* 新增 AI 文字指令
  * `y!ai`：與 AI 對話
  * `y!ai-new`：開始新對話
  * `y!ai-clear`：清除對話歷史
  * `y!ai-history`：查看對話歷史
* 新增 AI 相關指令的翻譯。
* 更新 /music
  * 修復多項問題。
* 修復 /ai-clear 的問題。
* 修復一些 Bug。

## 0.17.0
### 更新內容如下：
* 新增 /changelog
  * 可以查看機器人更新日誌。(手寫)
* /changelogs -> /git-commits
  * 已改變指令名稱。
* 新增 /music
  * 音樂播放系統。
  * 使用 `y!help Music` 查看指令說明。
* 新增 /ai
  * 就... 免費的 AI，別期待什麼了吧...
* 更新 /dynamic-voice play-audio
  * 現在不會常駐在語音頻道裡。
* 更新 /get-command-mention
  * 新增指令自動完成。
* 更新日誌系統
  * 移除了不必要的 Codeblock 標記。
* 因 Discord 限制移除離線訊息。
* 修復一些 Bug。

## 0.16.14
### 更新內容如下：
* 修復一些 Bug。

## 0.16.13
### 更新內容如下：
* 更新 /report
  * 修復了無法拒絕檢舉的問題。
* 新增了一些指令選項的翻譯。
* 更新 /info
  * 新增 `full` 選項，可以決定是否顯示完整模組列表。
* 新增機器人啟動時的狀態。
* 更新彩蛋lol
* 修復一些 Bug。

## 0.16.12
### 更新內容如下：
* 更新統計資訊 | /stats
  * 現在可以以 User install 的方式執行。
  * 關閉短暫訊息。
* 更新 /info
  * 新增應用指令數量。
* 更新 dsize -> /item use 雲端尺
  * 修復不會簽到的問題。
* 新增 /user-appeal-channel
  * 現在可以選擇是否讓被懲處的用戶申訴。
* 更新 /autopublish
  * 速率限制現在已改為分頻道制。
* 修復 /nitro
  * 感謝 @ting 的幫助，現在已經可以正常使用了。
* /automod 新功能
  * 新增詐騙陷阱 (scamtrap)
* 管理動作指令更新
  * 新增 unmute/untimeout、unban
* 機器人狀態新增指令使用次數。
* 更新 /r34：刪除標籤顯示改為數量。
* 新增一個彩蛋 自己去找lol
* 修復一些 Bug。

## 0.16.11
### 更新內容如下：
* 新增統計資訊 | /stats
  * 從本版本 (0.16.11) 開始會記錄指令使用次數（不含資料）。
* 新增離線訊息（壞的）。
* AI 模型改為使用 `openai-fast`。
* 修復一些 Bug。

## 0.16.10
### 更新內容如下：
* 更新自動回覆 | /autoreply
  * 現在可以使用 `{react:emoji}` 給予訊息反應。
  * 範例：`{react:↖️}` `{react:<:good:1295339514868400209>}`
* 修復一些 Bug。

## 0.16.9
### 更新內容如下：
* 更新 /owoify | `y!owoify`
  * 新增了一些替換詞彙。
  * 前綴+後綴的機率從 10% 調整為 40%。
  * 口吃機率從 10% 調整為 20%。
* 修復 /autoreply help 顯示問題
  * 移除了 Codeblock。
* 更新 /r34：新增 AI 過濾以及快取
* 修復一些 Bug。

## 0.16.8
### 更新內容如下：
* 新增 /owoify | `y!owoify` | 訊息指令選單
  * 把訊息變可愛！
  * 可以使用 /owoify 或是 `y!owoify`
  * 或者你也可以使用訊息指令選單！
* 修復一些 Bug。

## 0.16.7
### 更新內容如下：
* 更新 /autoreply help
  * 修復先前已知問題：忘記換行
  * 然後我忘記修移除 Codeblock 了對不起
* 已對所有先前需要管理者權限指令下放到管理伺服器也可執行。
  * /dsize-settings
  * /change
  * /settings-punishment-notify
  * /report
  * /webverify
  * /autopublish
  * `y!setprefix`
  * 若想更改斜線指令權限可至 `伺服器設定 > 整合 > Yee` 進行調整權限的動作。
* 新增若直接提及機器人可以查看指令前綴。
* 修復 `y!moderate` | `y!moderatereply` 的公告顯示時間的問題。
* 修復上線時間顯示問題。
* 修復一些 Bug。

## 0.16.6
### 更新內容如下：
* 更新自動回覆 | /autoreply
  * 新增 /autoreply help：可以獲得自動回覆的幫助
    * ⚠️ 已知問題：忘記換行與移除 Codeblock
  * 新增 /autoreply test：可以測試自動回覆的變數
* 更新自動發布 | /autopublish
  * 限制 1 小時內只能發布 10 則訊息，避免機器人被速率限制。
* 修復一些 Bug

## 0.16.5
### 更新內容如下：
* 新增 `y!setprefix`
  * 可以更改此機器人的前綴。
* 新增 /serverinfo | `y!serverinfo`
  * 可以查看目前所在伺服器的資訊。
* 更新 /info | `y!info`
  * 新增資料庫筆數。
* 更新 /fake-blacklist
  * 若指定人為本機器人，所有人將無法仿冒你。
* 修復一些 Bug
* 新增一個還在開發的遊戲，~~但是我把它禁用掉了 測試機器人裡有~~

## 0.16.4
### 更新內容如下：
* 新增 /ping
  * 可以檢查機器人的延遲
* 新增 /nitro
  * 可以防止公開 Nitro 禮物被 Selfbot 盜取
  * 絕對不會盜走你的尼戳 [相關原代碼](<https://github.com/AvianJay/useless-script/blob/main/discord%2FUtilCommands.py#L422-L506>)
  * ⚠️ 此功能因開發者沒錢尚未測試
  * 更新：已測試，功能正常
* 更改 /r34 的顯示樣式
* 修復一些 Bug

## 0.16.3
### 更新內容如下：
* 新增 /fake-blacklist
  * 加入進黑名單的使用者將再也無法仿冒你。
* 自動回覆新增可使用正規表達式 (regex)
* 優化自動回覆(應該)
* 網頁驗證可設定警告地區
* 修復一些 Bug

## 0.16.2
### 更新內容如下：
* 新增 /dsize-feedgrass 投稿編輯器行動版支援
* 投稿被批准的時候將會嘗試傳送私訊給用戶。

## 0.16.0
### 更新內容如下：
* 現在可以使用 /contribute 來進行投稿圖片，包括：
  * /dsize-feedgrass 所使用的圖片
  * 訊息選單中的「這傢伙在說什麼呢」
  * ⚠️ 五分鐘只能投一次稿。
* 修復雲端尺不會記錄進資料庫的問題。