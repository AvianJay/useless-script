# 🛡️ AntiBeast — Mention Scam Protection 管理員 自動化

AntiBeast is designed to counter Mr. Beast image scams, mass mentions, and compromised-account bots. Once enabled, it creates a native Discord AutoMod rule that blocks `@everyone`, `@here`, and mentions of any role that hasn't been added to the bypass list.

No more Mr. Beast scams 🥀

| Command | Description |
| --- | --- |
| `/antibeast setup` | Interactive setup flow: view the overview, configure bypass roles, set up repeat-trigger actions, then enable |
| `/antibeast about` | View an explanation of what AntiBeast does |
| `/antibeast toggle` | Enable, disable, or toggle AntiBeast |
| `/antibeast bypass` | Add or remove bypass roles |
| `/antibeast settings` | Configure whether repeat-trigger actions are enabled, the time window, trigger count, scope, and the Moderate.py action |
| `/antibeast list` | View the current configuration, AutoMod rule ID, protected roles, and action settings |

## How It Works

When enabled, AntiBeast syncs a dedicated AutoMod keyword rule. The rule includes:

- `@everyone`
- `@here`
- The mention token for every role that hasn't been added to the bypass list, e.g. `<@&role_id>`

At the same time, AntiBeast temporarily enables the "Mention @everyone, @here, and All Roles" permission for the `@everyone` role, so scam bots believe the server allows mass mentions — the actual messages are then blocked by the AutoMod rule. Disabling AntiBeast restores the `@everyone` role's original permission state.

## Bypass Roles

The bypass list is not the same as Discord AutoMod's exempt roles. In AntiBeast, bypassing a role means "don't put this role's mention token into the keyword filter."

Good candidates for the bypass list include:

- Notification roles that need to be mentioned normally in announcements
- Event roles that admins intentionally keep mentionable
- Special roles you don't want AntiBeast to block

Whenever a role is created or deleted, AntiBeast automatically re-syncs the AutoMod rule on servers where it's enabled; it also re-syncs once whenever the bot restarts.

## Repeat-Trigger Actions

AntiBeast can listen for its own AutoMod rule's trigger events. When the same user triggers the rule enough times within the configured time window, it runs the configured Moderate.py action.

Default settings:

- Time window: `10` seconds
- Trigger count: `2`
- Action: `kick AntiBeast: {time_window} 秒內觸發 {trigger_count} 次`
- Scope: all mentions protected by AntiBeast

`action` uses Moderate.py's action string format — you can use `kick`, `ban`, `mute` / `timeout` / `to`, `warn`, `send_mod_message` / `smm`, `force_verify`, and more. You can also chain up to 5 actions separated by commas.

If "Only process members who mention @everyone or @here" is enabled, mention tokens for non-bypassed roles are still blocked by AutoMod, but they won't count toward repeat-trigger actions and won't run the Moderate.py action.

Available variables:

- `{time_window}`: replaced with the currently configured time window (in seconds) when the action runs
- `{trigger_count}`: replaced with the trigger count reached this time when the action runs

Examples:

```text
mute 10m AntiBeast 連續觸發, warn {user} 請勿大量提及
```

```text
ban 0 0 AntiBeast: {time_window} 秒內觸發 {trigger_count} 次, smm
```

## Permission Requirements

AntiBeast requires the bot to have:

- Manage Server: to create, update, and read AutoMod rules
- Manage Roles: to adjust the `@everyone` role's mention permission

If repeat-trigger actions use `kick`, `ban`, `mute`, or other moderation actions, the bot also needs the corresponding Kick Members, Ban Members, Timeout Members, etc. permissions, plus a high enough role position.

> **Recommendation:** For your first setup, use `/antibeast setup`. The flow shows the overview first, then lets you configure bypass roles and action settings, before finally enabling AntiBeast.
