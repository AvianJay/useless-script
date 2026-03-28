# 🌍 OXWU — 地震監測 自動化

整合 OXWU API 與中央氣象署 (CWA)，透過 Socket.IO 即時接收地震速報與報告，自動推送至設定的頻道。

- 指令?`/earthquake set-alert-channel` | 說明?設定地震速報推送頻道
- 指令?`/earthquake set-report-channel` | 說明?設定地震報告推送頻道
- 指令?`/earthquake query-warning` | 說明?查詢最近的地震速報
- 指令?`/earthquake query-report` | 說明?查詢最近的地震報告
