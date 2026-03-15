# OXWU API
地牛 Wake Up! 的 API
# 開始使用
## 前提
* 你必須已經安裝了地牛 Wake Up!
## 安裝
Windows
* 直接開啟patch.bat即可修補。

Linux/MacOS
* 將`main.js`把地牛Wake Up! 本身的`main.js`取代，
# 已測試的版本
* v4.1.0

## API 文件

### Base URL
`http://127.0.0.1:10281`

### HTTP API

| Method | Path | 說明 | 回應類型 |
|---|---|---|---|
| GET | `/` | 健康檢查，確認 API 服務可用 | `text/plain` (`OXWU API`) |
| GET | `/screenshot` | 擷取主視窗截圖 | `image/png` |
| GET | `/gotoReport` | 切換到「報告」頁面 | `application/json` |
| GET | `/gotoWarning` | 切換到「警報」頁面 | `application/json` |
| GET | `/injectEruda` | 在主視窗注入 Eruda 開發工具 | `application/json` |
| GET | `/injectErudaSettings` | 在設定視窗注入 Eruda 開發工具 | `application/json` |
| GET | `/getWarningInfo` | 取得目前警報資訊（時間、座標、震源資料、列表） | `application/json` |
| GET | `/getReportInfo` | 取得目前報告資訊（編號、時間、震度分區） | `application/json` |
| GET | `/openSettings` | 開啟設定視窗（若尚未開啟） | `application/json` |
| GET | `/closeSettings` | 關閉設定視窗（若已開啟） | `application/json` |

> 註：目前所有 API 皆為 `GET`，且部分路徑雖回傳 JSON 字串，但未顯式設定 `Content-Type: application/json`。

### Socket.IO

- Endpoint：`http://127.0.0.1:10281/socket.io/`
- 伺服器會在頁面資料更新時主動推播：
	- `warningTimeChanged`
	- `reportTimeChanged`

事件 payload 範例：

```json
{
	"time": "2026-03-14 12:34:56",
	"parts": {
		"year": "2026",
		"month": "03",
		"date": "14",
		"hour": "12",
		"minute": "34",
		"second": "56"
	},
	"url": "https://..."
}
```

### 常見狀態碼

- `200`：請求成功
- `404`：找不到路徑
- `500`：找不到視窗或執行 JavaScript 失敗

## Discord Webhook
在 地牛Wake Up! 的設定內，在其他類別那裡有個連動設定，選擇此資料夾的discord.py。如果無法執行請先`chmod +x discord.py`

**一定要把僅執行一次打開！！！**

Windows 用戶請先使用pyinstaller打包好才可使用。`build.bat`