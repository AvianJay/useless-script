# 🛡️ Moderate — Moderation Tools 管理員

Core moderation module providing a full set of user management tools. Supports duration string parsing (e.g. `1h`, `30m`, `7d`) and can act on multiple users at once. All moderation actions are automatically integrated with the notification system.

| Command | Description |
| --- | --- |
| `/moderate` | Open the moderation menu, with actions such as kick, ban, mute, or blacklist |
| `y!moderate` `y!m` | Prefix version of the moderation command |
| `y!moderate_reply` `y!mr` | Moderate the author of the replied-to message |
| `/action-builder` | Build a custom combination of moderation actions |
| `/send-moderation-message` | Manually send a moderation announcement |
| `/moderation-message-channel` | Set the moderation announcement channel; it's recommended to also grant the Read Message History permission so the case ID can continue from other bots |
| `/moderation-message-format` | Edit, preview, or reset the announcement template and case-ID format |
| `/custom-action-add` | Add or update a custom server moderation action |
| `/custom-action-remove` | Delete a custom server moderation action |
| `/custom-action-list` | View custom server moderation actions |

> **Duration format:** supports `s` / `秒`, `m` / `分鐘`, `h` / `小時`, `d` / `天`, `w` / `週`, `M` / `月`, `y` / `年`, and can be combined like `1d12h`.

## Action Strings

The `action` parameter of `y!moderate` and `/multi-moderate` uses the action string format, where multiple actions (up to 5) can be chained, separated by commas `,`. You can also use `/action-builder` to generate the string through an interactive interface.

| Action | Format | Description |
| --- | --- | --- |
| `ban` | `ban <duration> <delete-message-duration> <reason>` | Ban the user. A duration of `0` means permanent; a delete-message duration of `0` means no messages are deleted |
| `kick` | `kick <reason>` | Kick the user |
| `mute` | `mute <duration> <reason>` | Mute the user, 10 minutes by default. `timeout` or `to` can also be used |
| `unban` | `unban <reason>` | Unban the user |
| `unmute` | `unmute <reason>` | Remove the mute. `untimeout` can also be used |
| `delete` | `delete <warning message>` | Delete the message, optionally with a public warning (`{user}` refers to the user) |
| `warn` | `warn <warning message>` | Send a public warning in the channel |
| `send_mod_message` | `send_mod_message` | Send a moderation announcement to the configured announcement channel. `smm` can also be used |
| `force_verify` | `force_verify <duration>` | Force the user to complete web verification (requires ServerWebVerify to be enabled) |

> **Examples:**
> `ban 7d 1d rule violation` — ban for 7 days, deleting messages from the last day
> `mute 30m watch your behavior, warn {user} please mind what you say` — mute for 30 minutes with a public warning
> `delete_dm your message has been deleted, mute 1h rule violation` — delete the message + DM notice + mute for 1 hour
> `ban 0 0 serious violation, smm` — permanent ban and send a moderation announcement

## Custom Moderation Action Parameters

Custom actions can use `{1}` through `{9}` to access positional parameters passed at call time. `{1}` is a required parameter; `{1:default value}` provides a fallback when the first parameter isn't supplied. Parameters must be numbered consecutively starting from 1, and commas always act as the action separator.

```text
/custom-action-add name:spam action:mute {1:10m} {2:洗版}, smm
spam 30m "重複洗版"
```

The example above expands to `mute 30m 重複洗版, smm`. Quotation marks let text containing spaces be treated as a single parameter; the entire operation is cancelled before execution if a required parameter is missing, extra parameters are given, there's a circular reference, or the expansion exceeds 5 actions.

## Moderation Announcement Template

If not configured, the current Markdown announcement format is used. Admins can edit the template via `/moderation-message-format` or the server web panel; the template can produce plain text, a single embed, or a mix of both.

Common variables:

- `{user}`, `{user_name}`, `{user_id}`, `{user_avatar}`
- `{moderator}`, `{moderator_name}`, `{moderator_id}`, `{moderator_avatar}`
- `{reason}`, `{action}`, `{case_id}`
- `{guild}`, `{guild_id}`, `{guild_icon}`
- `{reported_message}`, `{report_context}`, `{ai_note}` (only populated in the ReportSystem context)

Supports the same subset of embed commands as AutoReply: `{embedtitle:...}`, `{embeddescription:...}`, `{embedurl:...}`, `{embedimage:...}`, `{embedthumbnail:...}`, `{embedcolor:57F287}`, `{embedfooter:...}`, `{embedfooterimage:...}`, `{embedauthor:...}`, `{embedauthorurl:...}`, `{embedauthorimage:...}`, `{embedtime:true}`, `{embedfield:field name:content}`. Moderation announcements do not process AutoReply's reactions, conditions, delays, math, or state variables.

```text
{embedtitle:⛔ 違規處分}
{embeddescription:被處分者：{user}\n原因：{reason}\n結果：{action}}
{embedfield:裁判字號:{case_id}}
{embedfield:執行管理員:{moderator}}
{embedcolor:ED4245}
```

### Case IDs and Multi-Bot Continuation

The default case-ID format is `{roc_year}{sequence:04d}`. You can also use `{year}`, `{roc_year}`, `{sequence}`, for example `CASE-{year}-{sequence:04d}`. The format must include `{sequence}`.

When generating a new case ID, the bot reads up to 1000 historical messages in the announcement channel, searching from most recent for plain text and embed titles, descriptions, fields, authors, and footers. It first applies the current case-ID format, then falls back to compatibility with the legacy `裁判字號：1150001` style; a message containing multiple conflicting case IDs is skipped. The search isn't limited to messages sent by this bot, so other bots using the same format can also continue the sequence. Only when nothing is found, the bot lacks the Read Message History permission, or the format can't be recognized does it fall back to the server state saved by this bot.

> Different bots don't share a lock; if multiple bots generate announcements at nearly the same time, they may still read the same preceding ID. To guarantee no collisions across bots, all bots must share the same database or locking service.
