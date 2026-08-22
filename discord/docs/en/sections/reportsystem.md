# 🚨 ReportSystem — Report System

Users can report messages that violate the rules via the right-click context menu. The system uses AI to automatically judge the severity of the message (levels 0–5) and suggests a disciplinary action based on the server's rules. Built-in cooldowns and a blacklist mechanism prevent abuse.

## How to Use

> **How to report:** **right-click** a message (long-press on mobile) → **Apps** → **Report Message**, fill in the reason for the report, and submit. The system automatically notifies admins and triggers AI review.

## Admin Settings Commands (/report) 管理員

| Command | Description |
| --- | --- |
| `/report settings` | View or modify the various report system settings |
| `/report blacklist-role` | Manage report blacklist roles (add / remove / view) |
| `/report set-server-rules` | Set the server rules the AI review is based on |

## Configurable Options

| Setting | Description | Default |
| --- | --- | --- |
| `檢舉通知頻道` | Channel that receives report records and AI judgment results | Not set |
| `處分通知頻道` | Channel where enforcement action announcements are posted | Not set |
| `檢舉回覆訊息` | Confirmation message shown to the reporter after submitting a report | Thank you for your report... |
| `檢舉頻率限制` | Cooldown between two reports from the same user (seconds) | 300 seconds |
| `檢舉通知訊息` | Additional @mentioned roles or content in the notification channel | @Admin |

## AI Review Process

Once a report is received, the system automatically runs through the following process:

> 1. Extract the content and attachments of the reported message
> 2. Collect the user's most recent 10 historical messages as context
> 3. Have the AI determine the violation severity (0–5) based on the server rules
> 4. The AI provides a suggested action (timeout / kick / ban) with reasoning
> 5. Admins can click a button to directly execute the AI's suggestion, or manually choose an action

## Admin Action Buttons

Once the report notification reaches the report channel, admins can use the following action buttons:

| Button | Description |
| --- | --- |
| `執行 AI 建議處置` | Execute the AI's suggested action with one click |
| `封鎖` | Manually ban the reported user (deletion window and ban duration configurable) |
| `踢出` | Manually kick the reported user |
| `禁言` | Manually timeout the reported user (duration configurable) |
| `查看前10則訊息` | View the reported user's message history in that channel |
| `拔除檢舉人檢舉權限` | Revoke the reporter's report privileges in cases of malicious reporting |
| `拒絕檢舉` | Reject this report and send the reporter a DM notification |

## Blacklist Mechanism

> **Report blacklist:** admins can use `/report blacklist-role` to set a "report blacklist role." Members with this role will be unable to submit reports. Useful for preventing specific users from abusing the report feature.
