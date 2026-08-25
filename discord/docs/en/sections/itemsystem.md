# 🎒 ItemSystem — Item System

A virtual item system supporting both server-scoped and global inventories. Each server can create custom items, and using an item can trigger specific functionality. Items can be dropped for others to pick up, gifted to other users, and more.

## General User Commands (/item)

| Command | Description |
| --- | --- |
| `/item list` | View the items you own (optionally scoped to server / global) |
| `/item use` | Use an item, triggering its callback functionality |
| `/item drop` | Drop an item in the current channel for others to pick up |
| `/item give` | Gift an item to another user |

## Admin Commands (/itemmod) 管理員

| Command | Description |
| --- | --- |
| `/itemmod give` | Give a specified item to a user |
| `/itemmod remove` | Remove a specified item from a user |
| `/itemmod list` | List all available items (including custom ones) |
| `/itemmod listuser` | View the items owned by a specified user |
| `/itemmod addcustom` | Add a custom server item |
| `/itemmod removecustom` | Remove a custom server item |
| `/itemmod editcustom` | Edit a custom item's content, pricing, and other settings |
| `/itemmod listcustom` | List all custom items in this server |

## Dropping and Picking Up

> **Drop options:** `/item drop` supports the following parameters:
> • **can_pickup** — whether others can pick it up (default: yes)
> • **pickup_duration** — how long it can be picked up, 1–86400 seconds (default: 60 seconds)
> • **pickup_only_once** — whether each user can only pick it up once (default: no)
> • **scope** — the scope the item is drawn from (server / global)

## Custom Items

Admins can use `/itemmod addcustom` to create server-exclusive items. When a custom item is used, it sends its preset text content.

`content` now supports AutoReply template syntax, including regular variables, `if` conditions, `math`, Embed commands, `newmsg` / `edit`, and more; the syntax validation rules also follow AutoReply's — if the template format is invalid, adding or editing will be blocked outright.

| Parameter | Description |
| --- | --- |
| `name` | Item name (max 100 characters) |
| `content` | Text content sent when used (max 2000 characters, AutoReply variables supported) |
| `description` | Item description (optional) |
| `list_in_shop` | Whether to list the item in the server shop |
| `price` | Shop price (required when listed; unit: server coins) |
| `remove_after_use` | Whether the item is automatically consumed after use (default: yes) |
| `ephemeral_response` | Whether the response after use is only visible to the user themselves (default: no) |

> **Tip:** if a custom item has `list_in_shop = 是` set along with a price, it will appear in the economy system's server shop, where other users can purchase it.
