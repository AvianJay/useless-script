# 🔗 FixLink — Link Fixer 管理員自動化

FixLink detects built-in and admin-configured custom platform links in the server and generates fixed links that embed properly in Discord. The feature is disabled by default.

| Command | Description |
| --- | --- |
| `/fixlink settings` | Open the interactive settings panel |

## Delivery Modes

- **Standard Reply**: the bot replies with the original link, the author's page, and the available fixers. If the original link is wrapped in `||...||` spoiler tags, the fixed reply also preserves the spoiler; if the fixed message has no preview after 7 seconds it is automatically deleted, and if the preview succeeds the original message's embed is collapsed.
- **Webhook Replacement**: resends the fixed message using the poster's name and avatar. If there is no preview after 7 seconds, the webhook message is reverted to the original text; the original message is only deleted once this completes.
- Webhook mode can be set to "All Links" or "Tracking Parameters Only"; the latter only replaces a link when at least one supported link contains extra query or fragment data, while clean links still use the standard reply.
- Query parameters required for platform content identification don't count as tracking parameters, e.g. YouTube's `v` and `t`, Instagram's `img_index`, and Bilibili's `p`.
- Webhook messages come with a persistent delete button that only the original message's author can use.
- If the webhook cannot preserve a reply relationship, sticker, poll, or voice message, the original text is kept and the standard reply is used instead.

## Built-in Platforms

- 22 built-in platforms are supported: Threads, Twitter/X, Instagram, TikTok, Reddit, Facebook, Bilibili, Pixiv, Pinterest, YouTube, Twitch, Bluesky, Spotify, Mastodon, Tumblr, DeviantArt, Imgur, Weibo, Newgrounds, PTT, Roblox, and Fur Affinity.
- Spotify, Mastodon, Tumblr, Imgur, and YouTube are disabled by default and can be enabled individually from the settings panel.
- The settings panel lets you select built-in platforms, enable or disable them individually, and specify the primary fixer used for webhook mode; standard mode lists all available fixers for that platform.
- Only the program's built-in, exact source domains, path rules, and HTTPS fixers are used — unofficial joke domains or deprecated services from the Awesome Fixers list are not included.
- Service selection is based on [FixTweetBot Awesome Fixers](https://github.com/Kyrela/FixTweetBot#awesome-fixers).

## Threads

- Supports `threads.com`, `threads.net`, `/@user/post/id`, and `/share/code` links.
- Standard mode shows both FzThreads and FixEmbed; webhook mode lets you choose the primary service.
- `/share/code` is resolved, with limits, to the official Threads post URL, which is then used to generate FzThreads and FixEmbed links with the official post path; it only falls back to the FzThreads share URL if resolution fails.
- `/share/code` is treated as a tracking-type link, so "Tracking Parameters Only" webhook mode also processes it.
- With tracking removal enabled, the query and fragment of Threads URLs are stripped.

For other built-in platforms, enabling tracking removal keeps only the query parameters required for content identification, e.g. YouTube's `v`, Instagram's `img_index`, or Bilibili's `p`.

## Custom Platforms

- Up to 10 per server, matched by exact source domain and path prefix.
- Only structured query fixers are allowed: HTTPS endpoint, source URL parameter name, and static query.
- Wildcards, regex, IP endpoints, `{url}` templates, or domain substitution are not accepted.
- When tracking removal is enabled, you can configure which source query keys must be kept; if none are configured, all query parameters are kept.
- "Tracking Parameters Only" webhook mode only evaluates extra query parameters for custom platforms that have `keep_query_keys` configured; if no keys are configured, it won't guess which parameters are tracking parameters.

## Skipped Messages

FixLink only processes messages sent by real users. Messages from bots, webhooks, and links wrapped in `<https://example.com/...>` are never processed.
