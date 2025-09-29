# Discord 檢舉系統 (Per-Server Configuration)

這個系統已經從全域設定改為每個伺服器獨立設定的模式。

## 新功能特色

### 🔧 伺服器獨立設定
每個 Discord 伺服器現在可以獨立設定自己的檢舉系統參數，不再共用全域設定。

### 🗄️ 資料庫儲存
使用 SQLite 資料庫 (`data.db`) 儲存設定，取代舊的 JSON 設定檔。

### 📋 管理員指令
提供簡單易用的 slash 指令供伺服器管理員設定系統。

## 使用方法

### 管理員設定指令

#### `/設定` - 查看或修改伺服器設定
```
/設定                                    # 查看目前所有設定
/設定 檢舉紀錄頻道 #report-channel      # 設定檢舉紀錄頻道
/設定 管理員通知頻道 #mod-channel       # 設定管理員通知頻道  
/設定 檢舉回覆訊息 "感謝您的檢舉"        # 設定檢舉後回覆訊息
/設定 檢舉頻率限制 300                  # 設定檢舉冷卻時間(秒)
/設定 檢舉通知訊息 "@Moderator"         # 設定檢舉通知時的提及
```

#### `/檢舉黑名單` - 管理無法檢舉的身分組
```
/檢舉黑名單 查看                        # 查看目前黑名單身分組
/檢舉黑名單 新增 @某身分組              # 將身分組加入黑名單
/檢舉黑名單 移除 @某身分組              # 將身分組移出黑名單
```

### 一般使用者功能

#### 檢舉訊息
1. 對任何訊息按右鍵
2. 選擇「檢舉訊息」
3. 填寫檢舉原因
4. 系統會自動進行 AI 分析並通知管理員

## 技術說明

### 資料庫結構
```sql
-- 伺服器設定表
CREATE TABLE server_configs (
    guild_id INTEGER NOT NULL,      -- 伺服器 ID
    config_key TEXT NOT NULL,       -- 設定項目名稱
    config_value TEXT NOT NULL,     -- 設定值 (JSON格式)
    UNIQUE(guild_id, config_key)    -- 每個伺服器每個設定項目只能有一個值
);

-- 全域設定表 (主要用於存放 TOKEN)
CREATE TABLE global_config (
    config_key TEXT PRIMARY KEY,
    config_value TEXT NOT NULL
);
```

### 設定項目說明

| 設定項目 | 說明 | 資料型態 | 預設值 |
|---------|------|----------|--------|
| `REPORT_CHANNEL_ID` | 檢舉紀錄頻道 ID | Integer | `None` |
| `MODERATION_MESSAGE_CHANNEL_ID` | 管理員通知頻道 ID | Integer | `None` |
| `REPORTED_MESSAGE` | 檢舉後回覆訊息 | String | "感謝您的檢舉，我們會盡快處理您的檢舉。" |
| `REPORT_BLACKLIST` | 無法檢舉的身分組 ID 列表 | Array | `[]` |
| `REPORT_RATE_LIMIT` | 檢舉冷卻時間(秒) | Integer | `300` |
| `REPORT_MESSAGE` | 檢舉通知訊息 | String | "@Admin" |

## 遷移指南

### 從舊版本升級

如果您之前使用 `ReportToBan.py` 或舊版 `all.py`，請執行遷移腳本：

```bash
python migrate_config.py
```

此腳本會：
1. 將 `config.reporttoban.json` 的設定遷移到資料庫
2. 備份原始設定檔為 `.backup`
3. 保留 `config.json` 中的 TOKEN 設定

### 檔案變更

- ✅ `all.py` - 更新為支援每伺服器設定
- ✅ `database.py` - 新增資料庫操作模組
- ✅ `migrate_config.py` - 設定遷移腳本
- ✅ `data.db` - SQLite 資料庫檔案
- ⚠️ `ReportToBan.py` - 已整合到 `all.py`，可以刪除
- ⚠️ `config.reporttoban.json` - 可以刪除 (建議先備份)

## 故障排除

### 常見問題

**Q: 機器人說找不到檢舉頻道**
A: 使用 `/設定 檢舉紀錄頻道 #您的頻道` 設定檢舉紀錄頻道

**Q: 機器人沒有權限發送訊息到設定的頻道**
A: 確認機器人在該頻道有「查看頻道」和「發送訊息」權限

**Q: 資料庫檔案損壞**
A: 刪除 `data.db` 檔案，系統會自動重新建立

**Q: 想要重置所有設定**
A: 刪除 `data.db` 檔案，或使用 `/設定` 指令逐一修改

### 權限需求

機器人需要以下權限：
- 查看頻道
- 發送訊息  
- 使用斜線指令
- 管理成員 (用於禁言/踢出/封鎖)
- 讀取訊息歷史 (用於 AI 分析)

## 開發說明

### 主要模組

- `database.py` - 資料庫操作和設定管理
- `all.py` - 主要機器人功能和指令處理

### API 介面

```python
from database import db

# 取得伺服器設定 
value = db.get_server_config(guild_id, key, default_value)

# 設定伺服器設定
success = db.set_server_config(guild_id, key, value)

# 取得所有伺服器設定
config = db.get_all_server_config(guild_id)
```

### 擴充功能

要新增新的設定項目：

1. 在 `database.py` 的 `DEFAULT_SERVER_CONFIG` 加入預設值
2. 在 `/設定` 指令的 choices 中加入新選項
3. 在指令處理邏輯中加入對應的處理代碼