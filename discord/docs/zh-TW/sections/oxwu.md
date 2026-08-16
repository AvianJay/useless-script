# 🌍 OXWU — 地震監測 自動化

整合 OXWU API 與中央氣象署 (CWA)，透過 Socket.IO 即時接收地震速報與報告，自動推送至設定的頻道。

| 指令 | 說明 |
| --- | --- |
| `/earthquake set-alert-channel` | 設定地震速報推送頻道 |
| `/earthquake set-report-channel` | 設定地震報告推送頻道 |
| `/earthquake query-warning` | 查詢最近的地震速報 |
| `/earthquake query-report` | 查詢最近的地震報告 |
