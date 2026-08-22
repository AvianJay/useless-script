# 🌍 OXWU — Earthquake Monitor 自動化

Integrates the OXWU API with the Central Weather Administration (CWA), receiving earthquake alerts and reports in real time via Socket.IO, and automatically pushes them to the configured channel.

| Command | Description |
| --- | --- |
| `/earthquake set-alert-channel` | Set the channel for earthquake alert pushes |
| `/earthquake set-report-channel` | Set the channel for earthquake report pushes |
| `/earthquake query-warning` | Query the most recent earthquake alert |
| `/earthquake query-report` | Query the most recent earthquake report |
