# Changelog

## 0.24.8
* Updated AI message search
  * AI now uses Discord's indexed guild message search across channels and threads where both the user and Bot can read message history, instead of scanning a limited amount of channel history. Searches can also be restricted to one visible channel.
  * Searches support filters for keywords, author types, mentioned users or roles, reply targets, messages before or after another message, pinned state, attachment/embed/link types, filenames, sorting, and pagination, returning up to 25 results per request.
  * Search remains guild-only and continues to enforce the user and Bot's channel permissions and private-thread visibility. If Discord has not finished indexing the guild, the index progress and suggested retry delay are returned.
* Updated AI Discord data tools
  * User, channel, role, and message IDs are now always passed and returned as exact decimal strings, preventing large Discord snowflakes from losing precision during JSON number handling and causing read, search, or user-data operations to target the wrong item.
* Fixed some bugs.

## 0.24.7
* Updated PetPet
  * Fixed `/petpet` and its context-menu command being unavailable in DMs or other non-guild channels.
* Fixed some bugs.

## 0.24.6
* Updated safe AI browser tools
  * Browser interactions now require approval only once per job. The first action pauses the AI and shows a localized Discord confirmation card containing the reason and actual action, allowing the user to approve or reject it. The card is single-use and restricted to the original user in the same channel; rejecting it or letting it expire prevents later interactions in that job.
  * Webpage snapshots now provide a `ref` for actionable elements. The AI must use a `ref` from the latest snapshot to perform one click, text input, key press, selection, checkbox, or scroll action at a time, receiving a fresh snapshot afterward. If the element becomes stale or the page changes while approval is pending, the original action is stopped to avoid interacting with the wrong control.
  * Page evaluation shares the same approval and the confirmation card displays the JavaScript that will run; long scripts are attached in full for review. The browser job limit was extended to 5 minutes and Playwright was updated to 1.61.
* Updated command localization
  * Slash-command group roots such as Contribute, Minigames, work and school closure lookup, and FixLink now display correctly in Traditional Chinese, English, and Japanese. Automated checks now catch command groups without localized names and references to missing translation keys.
* Updated related translations.
* Fixed some bugs.

## 0.24.5
* Updated safe AI browser tools
  * Clicks, text input, key presses, selections, checkbox changes, and page evaluation now show a localized Discord confirmation card instead of requiring the user to type a confirmation code in their next message.
  * The card displays the AI-provided reason before execution. Only the original user can approve it in the same server or DM and channel; approval is single-use, expires after 5 minutes, and its internal token is not shown to the AI or user.
  * After confirmation, the button is disabled and shows an executing, success, or failure state. The execution result is sent privately to the user who pressed the button.
* Updated related translations.
* Fixed some bugs.

## 0.24.4
* Updated safe AI browser tools
  * AI can now capture the current public webpage and use the configured vision model to analyze its layout, important controls, images, page state, and readable text. The analysis focus and full-page capture can be specified.
  * The analysis is returned to the original model for the response, while the PNG screenshot is also attached for the user to verify. Webpage visuals and their descriptions are always treated as untrusted external data, and instructions within them are not followed or used to infer sensitive traits.
  * Browser screenshot analysis uses the existing image-analysis charge and failure-refund behavior. Screenshots larger than 8 MB are rejected before attachment, analysis, or charging.
* Updated AI image analysis
  * Discord images are now downloaded and size-validated before charging. Invalid or unavailable images, missing vision-model configuration, and oversized data are not charged; analysis-provider failures are still automatically refunded.
* Updated related translations.
* Fixed some bugs.

## 0.24.3
* Fixed command execution errors
  * Fixed the owner-only `y!dev-economyhistory` command failing when global transaction history was requested because it referenced a nonexistent amount variable.
  * Fixed `/itemmod editcustom` and `/itemmod listcustom` failing while displaying shop prices because they read currency settings from an invalid scope variable; they now use the current server's currency name.
  * Fixed the ticket module not loading its translation helper and a variable-name collision in the claim flow, which prevented `/ticket panel`, `/ticket claim`, and related flows from displaying results.
* Fixed some bugs.

## 0.24.2
* Updated account safety defense
  * Suspicious new-member detection and cross-server compromised-account defense can now be enabled separately for each server. An opted-out server no longer contributes detection evidence, deletes matching messages, or receives defense actions from other servers.
  * Suspicious new-member handling can use server-specific Moderate actions. Quick setup and the web panel support presets, custom parameters, and confirmation previews; leaving the action blank restores the default 28-day timeout.
  * Timeout expiry is now tracked separately for each server with automatic migration of legacy data. Existing cases can still be unlocked and restored after detection or cross-server defense is disabled.
* Updated AI image analysis
  * Owners can mark models that accept images with `ai-config vision-tag` and select the delegated image-analysis model with `ai-config vision-delegate`; model lists and autocomplete also identify vision capability.
  * Vision models receive the current attachment directly. Text-only models delegate image analysis when needed and then return to the original model to finish the response, preventing guesses about unseen images. Message emoji, sticker, avatar, and banner analysis also uses the configured vision model.
  * Invalid attachments or a missing vision model are rejected before charging, and failed analysis requests are automatically refunded.
* Added safe AI browser tools
  * AI can open and read public HTTP(S) pages, capture accessibility snapshots and screenshots, navigate back or forward, reload, wait, and hover.
  * Clicks, text input, key presses, selections, checkbox changes, and page evaluation must first be proposed and then confirmed exactly by the same user in their next message. Confirmation codes are single-use and expire after 5 minutes.
  * Private-network targets and unsafe redirects, password and payment fields, uploads and downloads, local files, WebSockets, and host-code execution are blocked. Browser jobs use a single FIFO queue with wait and execution timeouts to prevent sessions from interfering with each other.
* Updated related translations, quick setup, and the web panel.
* Fixed some bugs.

## 0.24.1
* Added complete Japanese localization | /language
  * Discord slash-command names, parameters, responses, buttons, and menus, along with prefix commands, the server panel, official website, documentation, and changelog, are now available in Japanese; automatic mode also recognizes Japanese Discord client and browser settings.
  * AI responses now primarily use the effective Traditional Chinese, English, or Japanese language; an explicitly requested language still takes priority, and concurrent conversations in different languages remain isolated.
* Fixed AntiBeast AutoMod rule creation and resynchronization failing because server-language context was missing; the block message now uses the server's effective language.
* Updated related documentation and translations.
* Fixed some bugs.

## 0.24.0
* Added Traditional Chinese and English localization | /language
  * Discord slash-command names, parameters, responses, buttons, and menus, along with prefix commands, the server panel, official website, documentation, and changelog, can now be displayed in the selected language.
  * Use `/language set` to choose a personal language across servers, or `/language server` to set a server default as an administrator; automatic mode considers the personal setting, server setting, and Discord client language, while `/language show` explains the current result.
  * The website now includes a language selector. Signed-in users synchronize their personal preference, while signed-out users follow the website session and browser language.
* Updated moderation | /moderate
  * Added customizable punishment-announcement templates and previews for Markdown, embeds, displayed fields, and case-number formats; new case numbers can continue numbering found in text or embeds posted by other bots in the announcement channel.
  * Server-specific moderation actions now support parameters `{1}` through `{9}` and default values, allowing inputs such as durations and reasons to be reused; missing parameters, circular references, or expansions beyond the action limit are rejected before execution.
* Updated WebVerify | /webverify relation-blacklist
  * Existing relation IDs can be added to a per-server blacklist with a configured moderation action and report channel; when a verifying member matches, related accounts are handled automatically and successful results are recorded to prevent duplicate action.
  * Administrators can add, remove, list, or disable blacklist entries, preview a scan of existing members, and then confirm it; confirmation rechecks the configuration, member state, permissions, and role hierarchy.
* Updated AI | /ai-admin server-prompt
  * Each server's custom-prompt limit can be independently configured from 1 to 6,000 characters; prompts that are too long or contain code fences are returned as UTF-8 text attachments when viewed.
* Updated link fixing | /fixlink
  * Processing now continues as soon as Discord generates a link preview instead of always waiting for the full fixed delay; a timeout check remains as a fallback when no update event arrives.
* Updated related documentation, translations, quick setup, and the web panel.
* Fixed some bugs.

## 0.23.0
* Added sticky messages | /stickymessage
  * Text or announcement channels can now have a sticky message of up to 2,000 characters; each channel is limited to one, and each server can enable up to 5 by default — when the limit is exceeded, which entries are kept is determined by list order.
  * After a channel receives a new message, the content is automatically reposted to the bottom; a quiet period of 0 to 300 seconds and a minimum post interval of 5 to 3,600 seconds can be configured to avoid frequent spam.
  * Supports adding, editing, removing, reordering, moving channels, manual posting, and mention policy; automatic posting does not trigger mentions by default, and shared throttling and retry mechanisms were added to reduce failures caused by Discord API rate limits.
* Updated auto-reply
  * Expanded reply variations for good morning, good afternoon, good evening, and general greetings.
  * Added the `!weather <city name>` mini-command, which retrieves a Traditional Chinese weather image for the given city; usage instructions are shown when no city is provided.
* Updated economy system
  * Transfers, server/全域幣 currency exchange and transfer, item trading, shop buying and selling, check cashing, and scheduled rewards are now atomic transactions; if any step fails, it rolls back completely, preventing duplicate deductions, grants, trades, or reward claims.
  * Added checks for balance, inventory, supply, exchange rate, and settlement results; when an abnormal exchange rate, invalid value, or asset inconsistency is detected, the operation is aborted before assets are changed, and the case is automatically added to a currency-flow blacklist for the owner to review and handle.
* Updated related documentation, quick setup, and the web panel.
* Fixed some bugs.

## 0.22.4
* Updated AI mention and reply mode | /ai-admin mention-mode
  * When directly mentioning the Bot, the mention must be at the very first non-whitespace position in the message; mentions placed mid-sentence or at the end no longer trigger the AI, reducing accidental activation during normal chat.
  * When replying to an AI message, the Discord Bot mention must be preserved, and only messages logged by the AI during the current runtime session can be replied to; this will not trigger if the "mention" option for replies is disabled.
* Updated AI tool calls
  * Native tool calls and simulated tool calls are now parsed separately; when the model supports native mode, it is used directly, and only downgrades to simulated mode when the model explicitly doesn't support it or simulated mode has been configured.
  * Simulated tool calls now require the explicit `tool_calls` format; regular JSON arrays, status settings, or normal responses containing a `name` field are no longer mistakenly treated as tools and executed.
  * Improved parsing and display of progress text during tool execution.
* Updated owner tools | y!eval
  * Supports multi-line Python, `await`, imports, Discord code blocks, `print` output, and the result of the last line's expression; `_` can be used to retrieve that owner's last successful execution result.
  * Execution errors return the full traceback; output that is too long, contains code blocks, or contains errors is sent as a UTF-8 file instead, and does not trigger Discord mentions.
* Updated AI-related documentation.
* Fixed some bugs.

## 0.22.3
* Updated AI external data tools
  * Added Google Image Search; the AI can search for people, places, objects, or other reference images as needed, reviewing up to 3 candidates and attaching one image that passes the safety review along with its source page.
  * Searched images are restricted by file size, pixel dimensions, format, redirects, and internal network addresses; after passing review, the original metadata is stripped and the image is re-encoded before being uploaded as a Discord attachment; nothing is shown if the review times out, fails, or is inconclusive.
  * Added the `fetch_raw` tool, which can directly read JSON, plain text, HTML, XML, YAML, and source code from public URLs without consuming Serper quota; it blocks URLs with embedded credentials, localhost, internal network addresses, non-standard ports, and binary content, and limits the download and returned size.
* Added AI mention and reply mode | /ai-admin mention-mode
  * This feature is disabled by default; once enabled by a server administrator, members can directly mention the Bot, or reply to a message successfully sent by the AI, to trigger the AI without typing `y!ai`.
  * Mentions or replies must contain text content, and will not re-trigger existing Bot commands; responses follow the full AI processing pipeline, the default model, and the server's payer billing settings.
* Updated music | /music
  * When the Bot shuts down, each server's voice and text channel, current song and playback progress, queue, volume, pause state, loop mode, and radio are saved; after restarting and reconnecting to Lavalink, the playback session is automatically restored.
  * Before auto-restoring, voice/stage channel permissions and member limits are checked; if the channel no longer exists, permissions are insufficient, or the song fails to load or play, the state is kept for a later retry, and the reason is reported in an available text channel.
  * `/music restore-queue` supports manually restoring the new playback state format as well as the legacy URL queue; if a song's encoding has expired, it will also try reloading from the original URL.
* Updated AI-related documentation, translations, and runtime requirements.
* Fixed some bugs.

## 0.22.2
* Updated AI
  * Recent channel messages are now processed independently as untrusted reference content and no longer mixed into system instructions, reducing the risk of message content being mistaken for instructions, and improving prompt caching and response stability.
  * Conversation history now records which tools were used and their key parameters, but no longer stores query results that may quickly become outdated; when the same data is needed again, the AI will re-call the tool to query it, reducing cases of answering with stale results.
  * Improved math formula recognition; dollar prices, thousands separators, amounts with decimals, and currency text such as `NT$` and `US$` are no longer mistakenly rendered as math images, while genuine formulas still render correctly.
* Fixed some bugs.

## 0.22.1
* Updated link fixing | /fixlink
  * Threads `/share/code` share links are now first resolved to the canonical post URL, then used to generate FzThreads and FixEmbed links for the corresponding post path; it only falls back to the original FzThreads share link if resolution fails.
  * Threads share links are now treated as tracking-type links, so they are also correctly replaced when the "only process tracking codes" Webhook mode is enabled.
  * Twitter/X `/i/status/...` links now resolve the post's actual author and profile, and no longer mistake `i` for the author name.
* Updated FixLink-related documentation.
* Fixed some bugs.

## 0.22.0
* Updated AI
  * Added table rendering support
* Added MentionLimit
  * Role mention cooldown feature
  * Use `/mentionlimit setup` to start configuration
* Updated dynamic voice channels
  * Now inherits permission settings from the category
* Updated compromised account detection
  * Optimized the mute process, ignore notifications, and more
* Fixed some bugs.

## 0.21.11
* Updated AI math formula rendering
  * Added automatic correction for common LaTeX shorthand, supporting fractions, square roots, nth roots, and boxed answers with omitted braces, as well as notations like `dfrac`, `tfrac`, `cfrac`, and `operatorname`, reducing rendering failures when transcribing formulas from images.
  * Added support for multi-line formula environments `aligned`, `align`, `align*`, `gathered`, and `split`, laying out each line of the formula within the same image.
  * When generating formulas, the AI now prioritizes complete, properly paired braces, improving the stability and display quality of complex formulas.
* Fixed some bugs.

## 0.21.10
* Added ticket system | /ticket
  * Added `/ticket setup` quick setup, which lets you specify the ticket category, panel channel, support role, and transcript log channel all at once, and immediately publishes the ticket panel.
  * Users can fill in a subject and problem description from the panel to create a private text channel visible only to the ticket opener, support staff, and approved members; supports claiming by support staff, adding or removing members, and closing by support staff or the ticket opener.
  * Supports up to 10 custom ticket categories, each with configurable button text, emoji, color, dedicated category, additional support role, and welcome message; published panels are updated automatically when settings change.
  * When a ticket is closed, an HTML transcript along with the opener, claimer, closer, reason, and handling time are sent to the log channel; if HTML generation fails, a plain-text transcript is used instead, and it can also be viewed directly on the web when the website module is enabled.
  * The server panel now includes complete ticket settings, letting you adjust category permission inheritance, support and blacklist roles, the per-user concurrent ticket limit, panel appearance, welcome message, and channel name templates.
* Added moderation requests | /request
  * Added `/request ban`, `/request kick`, and `/request timeout`, which let you request confirmation of an action from the target themselves or an administrator with the corresponding permission; once confirmed, the bot carries out the action, and the requester or the requested party can also cancel the request.
* Added moderation voting | /vote
  * Added ban, kick, and mute votes; members can vote to approve or oppose and change their choice; once approval votes reach the threshold, the action is carried out immediately, and if the threshold isn't reached before the vote expires, no action is taken.
  * Administrators can use `/vote settings` to configure, for each action, whether voting is enabled, a fixed or automatic threshold, and the voting duration; the automatic threshold is calculated from the number of non-bot participants in the channel's most recent 50 messages, with a minimum of 2 votes.
* Thanks to rise.0313 for the idea.
* Added translations for ticket and moderation commands.
* Fixed some bugs.

## 0.21.9
* Updated moderation tools
  * Text commands can now use moderation action names directly, e.g. `!ban @User 1d 3600 violation`, without needing to type `!moderate` first.
  * When replying to a message, you can append `r` after a moderation action to directly act on that message's author, e.g. `!banr 1d 3600 violation`; this works with built-in actions such as ban, kick, mute, delete, warn, and force verification.
  * Fixed an issue where discord.Member could not be converted.
* Updated AI
  * Added Google Search, public webpage content reading, and AI web search tools, allowing lookups of recent information and summarization of search results and sources, while blocking access to local, internal network, and non-public URLs.
  * Display math formulas in AI replies are now automatically rendered as images; formulas that fail to render, exceed limits, or are inside code blocks keep their original text.
* Updated bot latency | /info ping
  * REST API latency now distinguishes between the Defer measurement for Slash commands and the Typing measurement for text commands.
* Fixed some bugs.

## 0.21.8
* Added server quick setup | /gettingstarted
  * Added an interactive quick setup hub that centralizes configuration for moderation, notifications, economy, auto-reply, AutoModerate, AntiBeast, WebVerify, FixLink, and other loaded modules, saving immediately upon confirmation.
  * When the bot joins a new server, it sends a quick setup link to the inviter or server owner; if no usable channel is found, it falls back to a DM reminder.
  * `/autoreply builder` and `/webverify quick-setup` have been integrated into the new setup flow.
* Added link fixing | /fixlink
  * Automatically detects 20 built-in platforms including Threads, Twitter/X, Instagram, TikTok, and YouTube, generating fixed links that display previews correctly on Discord.
  * Added `/fixlink settings`, which lets you configure per-platform toggles, the primary fix service, tracking parameter removal, Webhook replacement mode, and up to 10 custom platforms.
  * Supports resending as a normal reply or via Webhook to preserve the original author's name and avatar; if the preview fails, the message is automatically deleted or the original text is restored.
  * Fixed replies now preserve `||spoiler tags||`, and using `<link>` can also skip automatic processing.
* Updated moderation tools and web panel
  * Added common suggestions, autocomplete, syntax parsing, an execution preview, and a confirmation flow to Moderate action input, available in AutoModerate, AntiBeast, quick setup, and the web panel.
  * Added a `to` shorthand for `mute` / `timeout`.
  * Added complete settings interfaces for FixLink and AntiBeast to the web panel, and improved the editing experience for composite settings, role lists, and action types.
* Updated AntiBeast | /antibeast
  * Consecutive-trigger actions can now be configured to count only `@everyone` / `@here`; role mentions are still blocked by AutoMod, but no longer count toward the automatic action threshold.
* Updated economy system
  * A server now needs at least 15 real human members before Server Coin and 全域幣 circulation features can be enabled or used.
  * Below this threshold, exchanges, the global shop, check cashing, and switching to global mode are blocked, with the current member count and reason for the restriction shown.
  * Added bot owner approval exceptions and circulation restriction logging.
* Updated related documentation and settings descriptions.
* Fixed some bugs.

## 0.21.7
* Updated Explore | /explore
  * Added a global casino: accessible via the casino host near the bottom of the lobby, featuring nine games — roulette, dice, coin flip, scratch card, slot machine, tower climb, high-low, blackjack, and lottery — all using 全域幣 for bets and payouts.
  * The casino shares the same game rules and accounting system with the Discord side; bets, payouts, and balance changes are all processed server-side as database transactions, with protection against duplicate requests.
  * `/explore` now attaches a "currently playing" message with a "Play" button, so others can join directly by clicking it.
  * Game static assets now use versioned URLs and long-lived caching, so updated versions no longer load stale cached files.
* Updated mini-games | /games
  * `/games dice` — Dice
  * `/games coinflip` — Coin flip
  * `/games lottery` — Lottery, drawn every hour on the hour.
  * `/games scratchcard` — Scratch card
  * `/games roulette` — Roulette
* Updated Taiwan Bus | /twbus
  * Brand new route overview interface: browse stops by tab, switch outbound/return routes with one click, and use a dropdown menu to view stop arrival information directly.
  * Added "Previous Stop," "Next Stop," and "Route Overview" buttons to stop information; the refresh, favorites, and map buttons were also updated.
* Updated economy system
  * Balance changes are now atomic database transactions, fixing an issue where balances could be incorrect under concurrent operations.
* Added some command translations.
* Fixed some bugs.

## 0.21.6
* Updated mini-games
  * Added `/games slot`: Slot machine
  * Added `/games highlow`: High-low
  * Added `/games blackjack`: Blackjack
* Fixed an issue where `/aki` didn't work properly.
* Changed the AI default model to `glm-5.2`.
* Updated some Explore world events.
* Fixed some bugs.

## 0.21.5
* Updated Explore | /explore
  * Map editing permissions: only members with the "Manage Server" permission can now edit that server's space map (the world map can only be edited by the bot owner); an admin-only map editing drawer panel was added in-game (opened via the 🛠️ button on the right, with an A-E tabbed tile list, layer switching, and an eraser; left-click to place, right-click to pick; right-click and click-to-move are both disabled while editing).
  * Added an in-server chat room: a chat panel in the bottom-right corner, with overhead chat bubbles and unread indicators; a built-in Chinese/English banned word list automatically masks words as `***`, and admins can customize banned words with `/explore-settings banned-words add|remove|list`.
  * The chat room can be bidirectionally bridged with a Discord text channel: once configured with `/explore-settings chat-channel`, in-game messages are forwarded to the channel, and channel messages are also shown in-game.
  * Added a skin shop: over 30 skins (villagers, nobles, sci-fi, monsters, meme characters, etc.); free skins can be equipped directly, paid skins are purchased with 全域幣, and both ownership and balance are verified server-side.
  * Added a level/XP system: XP is earned from chatting, using emotes, and completing quests; levels are shown above the player's head and in the online player list, and level-ups are broadcast to the whole space.
  * Added emote bubbles: the chat panel can send emotes (!, ?, heart, music note, etc.), visible to other players on the same map.
  * Added an online player list: a menu that shows the skin, level, and current map of every player currently in the space.
* Updated AI
  * Added image generation feature
* Lowered the compromised account detection score.
* Fixed some bugs.

## 0.21.4
* Updated economy system
  * Added a support-server-join bonus to daily/hourly rewards; users who have joined the support server will receive an extra bonus.
* Fixed some bugs.

## 0.21.3
* Fixed an issue where the /ai divider wasn't displaying correctly.
* Fixed an issue where banned users could not submit appeals properly.
* Fixed some bugs.

## 0.21.2
* Updated Fake User | /fake
  * Fixed a mention permission check issue caused by enabling AntiBeast.
* Fixed some bugs.

## 0.21.1
### Changes in this update:
* Added AntiBeast | /antibeast
  * Added a mention protection module targeting Mr. Beast image scams, mass mentions, and compromised-account bots.
  * Can be enabled interactively via `/antibeast setup`, which automatically creates a native Discord AutoMod rule blocking `@everyone`, `@here`, and role mentions that haven't been bypassed.
  * Supports `/antibeast bypass`, `/antibeast settings`, and `/antibeast list`, letting you configure bypass roles and automatic actions such as `kick`, `ban`, `mute`, and `warn` after repeated triggers within a short time.
* Updated AI | /ai
  * The AI backend was switched to an OpenAI-compatible custom API; the main AI, image review, and report review now share the same endpoint, API key, and model settings.
  * Added developer-only `ai-config` / `aicfg` management commands for configuring the endpoint, API key, model pricing, review model, report model, and the overall model JSON.
  * Adjusted the default model and the source of model settings.
* Strengthened compromised account detection | /imhacked
  * Extended the preventive mute duration to 28 days, and cross-server action records are now retained.
  * Suspicious new accounts joining a server are now also automatically scored based on account characteristics, with an immediate preventive mute applied when necessary.
  * If an already-flagged account joins a new shared server, the action is now automatically carried over.
  * If verification still isn't completed within 28 days, the system automatically kicks accounts that remain unverified.
* Updated Explore
  * Fixed an issue where leftover player data could remain when switching servers, leaving, or disconnecting from the Explore space.
  * Joining, moving, and changing appearance now also sync map and position information, making space state more consistent.
* Updated notifications and verification
  * Moderation notifications now more accurately determine whether a user is still in the server or has been banned, avoiding incorrect notifications.
  * Improved the display of unmute time in mute notifications, now showing both relative and full timestamps.
  * Fixed an issue with the WebVerify setup wizard's handling when the notification method menu misbehaved.
* Updated documentation
  * Added AntiBeast usage instructions and a documentation page on the website.
* Fixed some bugs.

## 0.21.0
### Changes in this update:
* Updated info commands | /info
  * Consolidated previously scattered info-related slash commands — `/help`, `/info`, `/ping`, `/serverinfo`, `/changelog`, `/git-commits`, `/tutorial` — into the `/info` group.
  * Added `/info user`, `/info avatar`, `/info banner`, and `/info mention` for more centralized user information lookups.
  * Usage instructions and documentation were likewise moved to new paths such as `/info help` and `/info tutorial`.
* Updated Owner management tools
  * Added `restart` / `res` / `reboot` commands: automatically runs `git pull` before restarting, shows progress, and interactively fills in any newly detected config fields.
  * Added a `restart_command` setting to make it easier to automatically restart the bot after a reboot.
* Updated Explore
  * Added `/explore-settings require-join`, which lets you configure whether users must join the server before entering Explore, with support for automatically creating or verifying invite links.
  * Explore now correctly shows which servers can be entered based on public status, join restrictions, and actual shared-server status.
  * Added music status reading and control support within the Explore activity, letting you view and control playback in sync while in the same voice channel.
* Updated AI | /ai
  * Increased conversation history capacity; overly long history is now automatically summarized and compressed, reducing interruptions in long conversations.
  * Added `/ai-set-response-view`, which toggles whether AI replies are displayed using a Container.
  * Long replies are now automatically sent as a text attachment when they exceed the limit, avoiding message length overflow.
  * Improved reply segmentation and layout, and added a message search tool with a larger search cap.
  * Adjusted Allowed Mentions to prevent AI replies from accidentally mentioning users.
* Updated auto-reply | /autoreply
  * Added built-in template packs: a welcome-message pack and a booster-reply pack, for quickly creating `type:join` and `type:boost` replies.
* Updated stats and mini-games
  * Stats commands were moved into the `/stats` group, and `/stats petpet-stats` was added to view PetPet usage statistics.
  * Merged Doomcord into mini-games, adding `/games doom`.
* Updated submission review
  * `feedgrass`, `what-is-this-guy-talking-about`, and `dynamic-voice-audio` submissions now have a new review button interface.
  * Approved submissions can now automatically save assets, reload resources, and grant a one-time global reward.
* Updated DCTW
  * Adjusted for compatibility with the new DCTW API fields and data format, fixing the display of browsing, details, comments, and link buttons.
  * The `/dctw key help` instructions were updated for the new process of obtaining a key from the official site's back office.
* Updated compromised account detection | /imhacked
  * Relaxed suspicious message detection conditions; in addition to four images, suspicious spam messages with two images can now also be detected.
  * Extended the original event detection time window, improving interception success rate.
* Fixed server verification and other issues
  * Fixed an issue where `ServerWebVerify` could send repeated DMs requesting verification when another bot auto-assigned roles.
  * Fixed an issue in the appeal flow where users who were not banned or muted could still submit an appeal.
  * Reduced cases where duplicate DM content was logged repeatedly.
* Fixed some bugs.

## 0.20.8
### Changes:
* Updated AI | /ai
  * Fixed an issue where it would not run properly.
* Fixed some bugs.

## 0.20.7
### Changes:
* Updated school/work suspension notifications
  * Fixed a permissions issue.
* Updated AI
  * Fixed the memory tool.
  * Added the current time to the prompt.
  * Fixed an issue reading Components v2 messages.
* Updated DCTW
  * Fixed an issue where messages could not be edited properly.
* Fixed some bugs.

## 0.20.6
### Changes:
* Updated Auto Moderation | /automod
  * Added an ignored-channels setting.
* Fixed some bugs.

## 0.20.5
### Changes:
* Updated Auto Moderation | /automod
  * Added `anti_invite_link`: detects Discord invite links and applies a custom action.
  * Added `allow_current_server`: lets you choose whether to allow invite links to the current server.
  * Quick setup, the info page, and the web panel now all support the above settings.
* Updated Auto Reply | /autoreply
  * Added special triggers: you can use `type:join`, `type:boost`, or Discord `MessageType` names to listen for system messages.
  * `/autoreply add`, `/autoreply edit`, and `/autoreply quickadd` now validate the above triggers to prevent misconfiguration.
  * Added related explanations to `/autoreply help`.
* Updated DCTW Browser | /dctw
  * Added `search`: search bots / servers / templates by keyword, with support for keyword, tag, and ID matching.
  * Added `bumpall`: bump all of your own resources at once.
  * The browse and detail pages now show keywords and bump time, and added `bumped` sorting.
* Updated Compromised Account Detection | /imhacked
  * Added thumbhash detection: identifies suspicious four-image messages directly from raw events.
  * Added a suspicious thumbhash management command (for developers).
* Updated AI | /ai
  * Added the `gpt-5-mini` model.
  * Changed the default model to `kimi-k2.5-fw`.
  * Adjusted some model names.
  * Temporarily disabled the video generation tool.
* Updated interface and documentation
  * Added a batch of application emoji / button emoji; updated the display of some buttons and embeds.
  * The usage documentation interface now uses a new icon display style.
* Updated web panel
  * Added invite link detection options to the Auto Moderation settings.
* Updated Earthquake Monitoring | /earthquake
  * Temporarily disabled `/earthquake set-alert-channel`.
* Fixed a permissions issue with `/set-log-channel`.
* Fixed some bugs.

## 0.20.4
### Changes:
* Added Compromised Account Detection | /imhacked
  * If a user sends suspicious invite links or four images across multiple channels within a short time, they will automatically be flagged as a suspected compromised account.
  * Suspicious messages are automatically deleted, and the user is preemptively muted in shared servers.
  * If the user has administrator permissions, their administrator role will be temporarily removed before further action is taken.
  * Added `/imhacked`: allows the user to lift the mute via a verification code process and restore their previously removed administrator role.
* Updated AI
  * Added Poe model support; more text models are now available.
  * Added long-content file conversion: when a reply is too long, it will automatically be sent as an attached file instead.
  * The AI can now read currently visible channel messages, individual messages, user information, and some permission information.
  * Improved tool-use indicators; a clearer "querying" status is now shown.
  * Fixed issues related to custom emoji parsing and the loading emoji.
* Updated /dsize
  * If "炸" (fried/explode), "爆" (burst/explode), or `💥` have recently appeared in the channel, it becomes more likely to trigger the breaking event.
  * Fixed an issue where the `/dsize-battle` duel lock might not be released correctly.
* Updated web panel
  * `/panel/login` now redirects directly to Discord OAuth login.
* Fixed some bugs.

## 0.20.3
### Changes:
* Updated AI admin commands
  * Fixed some permission issues.
* Fixed some bugs.

## 0.20.2
### Changes:
* Updated AI tools
  * Fixed some permission issues.
* Fixed some bugs.

## 0.20.1
### Changes:
* Updated `/dsize-feedgrass`
  * The grass-feeding feature now requires the user to have used the `/dsize` command that day.
  * Femboys can no longer use grass-feeding, unless they have used Japanese draft cola.
* Fixed some permission issues.
* Fixed some bugs.

## 0.20.0
### Changes:
* Added DCTW Browser
  * Added the `/dctw` command group for browsing DCTW's bot, server, and template listings.
  * You can view detailed information, tags, descriptions, community links, images, and comments directly within Discord.
  * Supports voting for and bumping bots / servers / templates directly.
  * Added `/dctw key set|show|clear` for managing your personal DCTW API key.
* Added AI admin commands
  * You can now set a custom prompt.
  * You can now set who pays for AI usage.
* Added some command translations.
* Fixed DCTW page link issues.
* Fixed some bugs.

## 0.19.33
### Changes:
* Updated Auto Reply
  * By default, `@everyone`, `@here`, and roles can no longer be mentioned.
  * If you need mention functionality, add `{mention:true}` to the message.
* Fixed some bugs.

## 0.19.32
### Changes:
* Fixed a money-laundering exploit.
* Fixed some bugs.

## 0.19.31
### Changes:
* Updated Auto Reply
  * Added `/autoreply builder`, which lets you build rules step by step using an interactive interface.
  * Added more variables and syntax:
    * Support for more server / user information variables, range splitting like `{contentsplit:1-}`, and `{null}`.
    * Added `{math:(...)}` for arithmetic operations, as well as `&&` / `||` conditional logic.
    * Added `{newmsg:n}`, `{edit:n}`, `{uservar:...}`, `{guildvar:...}`.
    * Added link, author image, footer image, and other fields to embeds.
    * See `/autoreply help` for more details.
  * Strengthened duplicate trigger checking; template pack application, testing, and import prompts are also more complete.
* Updated AI
  * When asked about feature settings, variables, embeds, conditional logic, or documentation examples, the AI will now prioritize checking the usage documentation.
  * Added AI memory functionality; it can now remember or forget common user information and the server's general atmosphere when explicitly requested.
* Updated custom items and utility tools
  * The `content` field of `/itemmod addcustom` and `/itemmod editcustom` now supports AutoReply template syntax, and syntax is validated on creation.
  * The right-click "Emoji Info" action can now display up to 10 custom emoji at once, with page navigation support.
* Updated mini-games
  * Tower and Big Two now have more complete records for bets, refunds, withdrawals, and winnings.
  * Improved timeout handling and message update stability for some interactive Views.
* Updated dsize
  * Fixed a text overlap issue.
* Updated documentation and help pages.
* Fixed some bugs.

## 0.19.30
### Changes:
* Added AI tools
  * It can now integrate with the bot's related features.
  * Added web search
* Updated Auto Reply
  * Added more variables
    * You can now use embeds, date/time, content splitting, if statements, and more.
    * See /autoreply help for more details.
* Updated documentation.
* Fixed some bugs.

## 0.19.29
### Changes:
* Fixed some bugs.

## 0.19.28
### Changes:
* Added /music radio | `y!radio`
  * You can now play internet radio stations directly.
  * Currently supports LISTEN.moe and R/a/dio.
* Added some command translations.
* Fixed some bugs.

## 0.19.27
### Changes:
* Updated economy system
  * Added a currency circulation blacklist system.
* Fixed some bugs.

## 0.19.26
### Changes:
* Updated earthquake system
  * Added estimated arrival time display for various locations.
* Updated Impersonate User
  * Users with mention permissions can now use mentions.
  * Administrators can now add impersonation filters.
  * You can now use `{t:n}` in messages to send up to 3 messages.
    * For example, `嗨{t:1}你好啊{t:2}歡迎來到伺服器！` would be parsed into three messages, sent 1 second and 2 seconds apart respectively.
    * n: the delay before the next message, up to 2 seconds.
* Fixed some bugs.

## 0.19.25
### Changes:
* Updated economy system
  * Fixed some money-laundering exploits.
* Seems like some easter eggs were added.
* Fixed some bugs.

## 0.19.24
### Changes:
* Updated the terrible Make it a Quote
  * Fixed an issue where names were unclear.
* Updated web panel
  * Fixed some issues.
* Fixed some bugs.

## 0.19.23
### Changes:
* Updated admin items
  * Fixed an issue where they could be sold.
* Updated moderation features
  * Added custom action commands
    * Administrators can use commands like `/custom-action-add` to add custom actions.
* Updated Auto Reply
  * Updated the web panel UI.
* Updated AI
  * Updated the prompt
  * Server custom emoji can now be used.
* Fixed some bugs.

## 0.19.22
### Changes:
* Updated custom items
  * You can now add a revenue-share user: when the item is used, the designated user will receive a reward equal to 90% of the item's price.
* Fixed some bugs.

## 0.19.21
### Changes:
* Fixed an issue where an await was forgotten 🥀.
* Fixed some bugs.

## 0.19.20
### Changes:
* Added /economymod global-mode
  * Lets you toggle the server's global mode; once enabled, it forces global mode and disables related server-specific features.
* Updated /ai
  * You can now set a default model.
  * The AI can now see the server's description.
* Fixed some bugs.

## 0.19.19
### Changes:
* Updated /bazi
  * Added hidden stems, secondary stars, common shensha (auspicious/inauspicious stars), and Ten Gods annotations, and added support for generating a chart without entering the birth hour.
* Updated /dsize-battle
  * Fixed a potential "This interaction failed" issue; the bot now responds properly to the user when an error occurs during a battle.
* Fixed some bugs.

## 0.19.18
### Changes:
* New: BaZi (Four Pillars of Destiny) chart | /bazi
  * Supports entering year, month, day, hour, and gender to generate a BaZi chart.
  * Displays the Four Pillars, Ten Gods, Five Elements statistics, Day Master strength analysis, favorable/unfavorable elements, and Da Yun (luck pillar) sequence.
* Updated AI features | /ai, y!ai
  * Added model selection (with autocomplete).
  * Text commands now support using the model name as a prefix, e.g. `y!ai openai-fast ...`.
  * Added 全域幣 billing: fees are charged based on the model's rate and the number of input/output characters.
* Updated moderation features
  * Adjusted action string parsing: more stable parameter handling for `ban` and `mute/timeout`.
  * Disabled the `delete_dm` and `warn_dm` actions (including in the builder and documentation).
  * Subsequent steps now abort if certain actions fail, preventing duplicate or inconsistent punishments.
* Updated message screenshot features
  * Added a GIF toggle button to "Bad Make it a Quote" (usable only by the original requester).
  * Improved the upvote board send display (now uses an Embed image).
* Updated report system
  * Changed the AI review model to `openai-fast`.
* Updated /dsize-feedgrass-nsfw
  * Fixed an issue where the NSFW parameter was not correctly passed into the image generation flow.
* Updated documentation and translations.
* Fixed some bugs.

## 0.19.17
### Changes:
* Fixed some bugs.

## 0.19.16
### Changes:
* Updated /dsize
  * There is now a chance it breaks and turns into a femboy.
  * Added Japanese Draft Cola (?).
    * Drinking it adds 1-3 cm.
    * **Only femboys can drink it.**
    * Thanks to @1.jpg for the suggestion.
  * Only users who have **used it themselves before** will be mentioned.
* Updated /nitro
  * Added an option to restrict claiming to only users who have recently sent a message.
* Updated auto-moderation
  * Changed the text content of some default punishment actions.
* New: /dsize-feedgrass-nsfw
  * Added NSFW grass-feeding mode: you can choose whether to enable NSFW grass-feeding mode; when enabled, images marked as NSFW will be used.
* Fixed some typos.
* Fixed some bugs.

## 0.19.15
### Changes:
* Updated items
  * Adjusted the prices of several items.
* Command usage statistics
  * Now tracks the error count for application commands.
  * Updated /stats to display statistics for application commands.
* Fixed some bugs.

## 0.19.14
### Changes:
* Updated /dsize
  * Anti-surgery medication now has side effects
    * Measurements taken within two days will be shorter.
  * Added check-in freeze ball
    * Having this item prevents losing your previous check-in streak if you forget to check in.
  * Added the "Blue Pill" item
    * Today's status will carry over to the next day.
  * Added "Spray and Pray" skill
    * Can randomly attack a user on the leaderboard.
* Updated /ai | y!ai
  * The AI can now see more messages (up to 20).
  * Added support for more message types.
  * Updated the prompt, making the AI even more abstract 🥀
* New: context menu
  * Added emoji/sticker info, allowing direct copying to this server.
* Fixed issues with the AI review system.
* Added some missing command translations.
* Fixed some bugs.

## 0.19.13
### Changes:
* Updated /economy shop
  * Fixed an issue where purchasing from the global shop would reset your balance to zero.
* Updated /multi-moderate
  * Added an estimated time notice before the operation starts.
  * Added an execution interval to avoid 429 rate limiting.
* Updated AI-related features
  * Switched the model to `openai`.
  * Added a command usage cooldown.
* Fixed some bugs.

## 0.19.12
### Changes:
* Fixed some mention-related vulnerabilities.
* Fixed some bugs.


## 0.19.11
### Changes:
* Updated documentation
  * Report system
  * Bus system
* Added permission checks for most features.
* Fixed some bugs.

## 0.19.10
### Changes:
* Updated the "What Is This Guy Saying" feature
  * Fixed the 🔄 regenerate button.
    * Now caches the already-screenshotted message instead of regenerating it again.
    * Restricted the button so only the original requester can use it.
* Updated the website
  * Improved mobile optimization.
  * Updated documentation.
    * Auto-reply
    * Item system
* Updated join notification/bot info
  * Added a link/button to open the documentation.
* Updated /dsize-feedgrass
  * Added a context menu option for direct grass-feeding.
* Fixed some bugs.

## 0.19.9
### Changes:
* Updated the music system | /music
  * Added `/music loop`: set the loop playback mode.
    * Supports three modes: loop off, 🔂 single-track loop, and 🔁 queue loop.
    * If no mode is specified, it cycles through the modes in order.
    * Supports the text command `y!loop` (alias: `y!lp`).
  * `/music now-playing` and `y!np` now display the current loop mode.
* Updated submissions | /contribute
  * Added a position preview image for `/dsize-feedgrass` submissions: automatically draws Target, Feeder, and Extras marker circles on the background image when submitting.
  * Added a "📝 Edit JSON" button: administrators can edit the JSON configuration directly in the submission message and regenerate the preview image.
  * Fixed the attachment lookup method, now searching by file extension for improved reliability.
* Updated message screenshot features
  * Added a 🔄 refresh button to "What Is This Guy Saying," allowing the screenshot to be regenerated.
  * Fixed an issue where Chinese (CJK) characters would not wrap correctly.
* Updated the guild panel | /panel
  * Relaxed restrictions on the `/panel` command; it can now be used in DMs or in user-installed contexts.
  * Added Google Analytics (gtag) support to all panel pages.
* Updated the website
  * Added a `/docs` documentation page.
  * Added Open Graph image support (`/og-image.png`), improving link preview display.
* Updated dynamic voice | /dynamic-voice
  * Fixed an issue where a join sound effect would attempt to play even when already connected to voice (e.g., while music was playing).
* Updated dependencies
  * discord.py 2.7.0
  * lava-lyra 1.7.0
    * Added DAVE support
* Fixed an issue where `/userinfo` would error when a user had no avatar.
  * Really, thanks a lot, Copilot.
* Fixed an aiohttp Accept-Encoding issue.
* Added related translations.
* Fixed some bugs.

## 0.19.8
### Changes:
* Updated /itemmod
  * `/itemmod addcustom` added `remove_after_use`: configure whether the item is removed from the inventory after use.
  * `/itemmod addcustom` added `ephemeral_response`: configure whether the response after using the item is only visible to the user.
  * `/itemmod editcustom` now supports editing the above properties.
* Updated auto-moderation | /automod
  * Added `automod_detect` (AutoMod Detection): detects Discord's native AutoMod trigger events, and can execute custom punishments or log them to a specified channel.
  * Added an AutoMod detection option to the quick setup wizard.
  * Improved logging.
* Updated moderation features
  * `/send-moderation-message` added a `direct` option: send directly to the specified channel without showing the confirmation interface.
  * Fixed issues related to `ban`.
* Updated feedback | /feedback
  * Added a reply feature: developers can reply directly to users in feedback messages, with notification sent via DM.
  * Added a support server link.
  * Added a cooldown.
* Fixed a logging system vulnerability.
* Fixed a permissions issue with /dsize-feedgrass.
* Disabled certificate verification for the government website 🥀
* Fixed some bugs.

## 0.19.7
### Changes:
* Updated /economymod
  * Removed the ability to give and remove money.
* Updated /itemmod
  * Non-for-sale items can no longer be obtained.
* Thanks, @_ikun_.
* Fixed some bugs.

## 0.19.6
### Changes:
* Updated punishment notifications
  * Added `/user-appeal-blacklist`: allows adding a user to the appeal blacklist; blacklisted users will not be able to appeal punishments.
  * Added an "Add to Appeal Blacklist" button to appeal notifications, letting administrators block a user from appealing directly from the appeal message.
* Updated the economy system | /economy
  * Added a shop purchase interface: select items via a dropdown menu and choose to pay with Server Coin or 全域幣.
  * Improved inflation tracking for admin-granted items: items granted by administrators now use a separate inflation weight calculation when sold on the market, and the injected amount is logged.
  * Fixed an issue where checks could not be purchased in the shop.
  * Added some purchase limits.
* Updated submissions | /contribute
  * When a submission is approved, the submitter now receives a 200 全域幣 reward (only once per submission).
* Updated the music system | /music
  * Added `/music search`: search for music, with autocomplete support.
  * Added support for direct HTTPS links.
  * Improved connection cleanup logic.
* Updated join notifications | /joinnotify
  * Added a "Don't notify again" button.
  * Fixed related issues.
* Updated custom prefix | `y!setprefix`
  * The prefix hint now uses an Embed display.
  * Added a "Don't remind me again" button.
  * Fixed an issue where resetting the prefix would result in `None`
* Updated Taiwan Bus | /twbus
  * Optimized code structure.
* Updated the report system
  * Added removal of user mentions from reported messages.
* Added related translations.
* Fixed some bugs.

## 0.19.5
### Changes:
* Updated punishment notifications
  * Now displays the moderator's name and avatar.
  * Really, thanks a lot, @kusanagi_akane.
* Updated the music system
  * Disconnect now uses `.destory()` to avoid leftover `player` instances.
* Updated `y!setprefix`
  * Added a delay to avoid conflicts with prompt detection.
* Fixed an issue where custom items could not be purchased.
* Fixed some bugs.

## 0.19.4
### Changes:
* Updated /music nodes
  * Added the number of connected servers.
  * You can now see the health score and which server it's currently on.
* New: /music restore-queue
  * Can restore a saved playback queue.
  * The playback queue is automatically saved when the bot is shut down or restarted.
* Updated the item system | custom items
  * Can now be priced and listed in the server shop.
* Updated mini-games | /games
  * Added some timeout messages.
* Updated web verification | /webverify
  * Added some missing options.
* Fixed some bugs.

## 0.19.3
### Updates:
* Added guild panel settings editing
  * Added web verification settings: enable, CAPTCHA, unverified role, region alerts, etc. can now be edited in the panel.
  * Added auto-moderation settings: auto-moderation rules can now be edited in the panel.
  * Added auto-reply settings: ignore mode, channels, and rule list can now be edited in the panel.
  * Added an "Allow 全域幣 circulation" toggle to the economy system settings.
  * Empty state notice: shows a "log out and log back in" hint when no server is found.
* Updated economy system | /economy
  * Added an "Allow 全域幣 circulation" toggle: administrators can use `/economymod toggle-flow` to disable exchange, global shop buying/selling, and check cashing.
  * Added `/economy history`: view personal transaction history (supports server/global scope and pagination).
  * Check cashing: when cashed in a server inventory, converts to Server Coin based on the exchange rate to prevent arbitrage.
* Re-enabled /itemmod
  * Restored item system management commands.
  * Added custom items: use `/itemmod addcustom` to create custom items limited to that server, and send them with `/itemmod give`.
* Added mini-games | /games
  * `/games tower`: tower climbing game.
  * `/games big2`: Big Two.
* Updated moderation features
  * Added `/action-builder`: generates moderation action command strings, convenient for multi-user management or the prefix command `!moderate`.
* Updated auto-moderation | /automod
  * Added `/automod quick-setup`: an interactive quick setup wizard.
  * Added some options for force verification (force_verify).
* Updated earthquake monitoring | /earthquake (OXWU)
  * Changed CWA report image retrieval retry interval to 10 seconds.
  * The "Central Weather Administration report" button now only shows when the CWA image was successfully retrieved.
* The settings database now supports the boolean type, for toggle-type settings such as web verification and dsize.
* Fixed some bugs.

## 0.19.2
### Updates:
* Updated web verification | /webverify
  * Fixed a context menu permission issue
  * Fixed the context menu not being usable
* Updated /dsize-battle
  * Added direct duels via user context menu
* Updated economy system | /economy
  * Updated exchange rate logic (?
* Removed /itemmod
  * Temporary, might still be changed back
    * ~~I'll change it back if too many people complain~~
  * Please use /economymod give instead, then use /economy buy to purchase items.
* Fixed some bugs.

## 0.19.1
### Updates:
* Updated /stats
  * Now sorted by usage count.
* Fixed an issue where /help caused an application command error.
* Fixed some bugs.

## 0.19.0
### Updates:
* Added guild panel system | /panel
  * A brand-new web-based guild management panel.
  * View server statistics, settings, logs, and other information.
  * Requires logging in with a Discord account and having administrator permission.
* Added bot invite notification system | /joinnotify
  * Sends a DM notification when a user invites the bot to a server.
  * The /joinnotify command can be used to turn off join notifications.
* Updated auto-moderation | /automoderate
  * Added anti-user-installed-app spam (anti-uispam) feature.
  * Added anti-spam feature.
  * Added anti-raid feature.
  * Improved logging.
* Updated web verification | /webverify
  * Added force verification (force-verify) feature.
  * Added start force verification (start-force-verify) feature.
  * Added manual verification (manual-verify) feature.
* Updated moderation features | /moderate
  * Added command actions
    * force-verify: force verification.
      * duration: force verification duration.
  * Added multi-user management: perform moderation actions on multiple users at once.
  * Renamed "multi-action" to a clearer name.
  * Removed the group command (GroupCog) structure in favor of a flatter command architecture.
  * Improved permission checks.
* Updated music system | /music
  * Added /music recommend: recommendation feature.
* Updated utility commands
  * Added `/tutorial`: tutorial guide.
  * Prevented embed-too-large errors.
* Updated economy system | /economy
  * Improved 全域幣 support.
  * Optimized code structure.
  * Added check items.
* Updated /dsize
  * Added a check as a daily check-in reward.
* Updated item system | /item
  * Improved security.
* Updated earthquake monitoring | /earthquake
  * Improved OXWU API integration.
* Updated logging system
  * Significantly improved logging functionality.
  * Added more detailed error tracing.
* Fixed several issues with the paid board (有料板子).
* Fixed multiple issues with 全域幣 features.
* Added some translations.
* Fixed a missing `await` issue 🥀.
* Fixed some bugs.

## 0.18.4
### Updates:
* Updated economy system | /economy
  * Added `/economy hourly`: hourly reward.
    * Supports both global and server modes.
  * Updated `/economy daily`: daily reward.
    * Switched to date-based detection (Taiwan timezone UTC+8) instead of a 24-hour countdown.
    * Added `global_daily` parameter to support global check-in.
    * Fixed consecutive check-in streak detection logic.
    * Added timestamp display.
  * Updated `/economymod setdaily`: adjust the daily reward cap.
    * Amount cap changed from 1,000,000 to 1,000.
  * Added a minor inflation mechanism: inflation caused by hourly rewards.
  * Optimized 全域幣 feature.
* Updated message screenshot-related features
  * Added `/upvoteboard`: set the paid board (有料板) channel.
    * Messages with 5 or more upvotes are automatically sent to the specified channel.
  * Added server paid board feature.
  * Improved screenshot quality:
    * Automatically resize the window to fit content.
    * Removed spoiler-hiding effect.
    * Optimized message grouping logic (consecutive messages within 5 minutes).
  * Updated the paid board button style: switched to the ⬆️ emoji.
* Updated /petpet
  * Added message context menu support: use PetPet directly by right-clicking a user.
* Updated /stickyrole
  * Permission adjustment: lowered from administrator to manage roles (manage_roles).
* Added related translations.
* Fixed some bugs.

## 0.18.3
### Updates:
* Updated /dsize-feedgrass
  * Added support for global grass feeding.
* Updated /economy
  * Added support for user installation.
* Updated /music
  * Switched to using NodePool to manage music nodes.
  * Removed user installation support.
* Added related translations.
* Fixed some bugs.

## 0.18.2
### Updates:
* Updated /item use
  * Fixed several items that could not be used.
* Updated /economy
  * Changed the currency unit.

## 0.18.1
### Updates:
* Updated generated message image-related features
  * Added paid button
    * When more than 5 people click the button, it will be sent to the official channel.
  * Removed submission prompt.
* Updated /automoderate
  * Fixed an issue where scamtrap could not be triggered.
* Fixed some bugs.

## 0.18.0
### Updates:
* Added economy system | /economy
  * A really awesome economy system.
  * Integrated with the item system.
* Added sticky roles | /stickyrole
  * Administrators can use a command to set which roles apply.
  * If a user leaves and rejoins, previously assigned designated roles will be automatically restored.
* Updated /music
  * Now supports multiple music nodes.
  * Added `/music shuffle`: shuffle playback.
  * Added node status checks.
  * Added same-voice-channel check.
* Updated appeal system
  * Allowed multiple appeals.
* Updated /ai
  * Adjusted AI prompt.
* Added related translations.
* Fixed some bugs.

## 0.17.7
### Updates:
* Updated /ai
  * Updated the prompt to make it more abstract 🥀
  * Gave the AI the ability to see channel messages (5 messages only).
* Updated /music
  * Added a feature to automatically leave the voice channel after 5 minutes.
  * y!play will now act as resume if no argument is specified.
* Updated y!setprefix
  * Added a small tip explanation.
* Fixed some bugs.

## 0.17.6
### Updates:
* Updated /ai
  * The AI model was switched to `openai-fast`, fetched remotely.
  * Switched to using the `client.chat.completions.create` API.
  * Fetch g4f from GitHub.
* Updated /report
  * Adjusted the report text.
* Updated /help
  * Text command help can now only be used within a server.
* Fixed multiple DeprecationWarnings.
* Fixed some bugs.

## 0.17.5
### Updates:
* Updated /moderate related announcement publishing feature
  * Some idiot forgot to add `await` 🥀
* Fixed some bugs.

## 0.17.4
### Updates:
* Added /help application command
  * View descriptions of application commands and text commands.
* Updated /earthquake
  * Added CWA (Central Weather Administration) links and image display.
  * Updated /earthquake set-alert-channel and /earthquake set-report-channel
    * Can now unset by not specifying a channel.
* Added dynamic voice submission feature
  * Users can now submit custom voice lines via /contribute dynamic-voice.
* Updated /get-command-mention
  * Fixed logic to avoid receiving 429 rate limit errors.
* Added related translations.
* Fixed some bugs.

## 0.17.3
### Updates:
* Added `y!stickerinfo`
  * View detailed information about a sticker.
* Updated auto-reply | /autoreply
  * Added sticker reply feature, using `{sticker:StickerID}` to reply with a sticker.
  * Added /autoreply ignore: set globally ignored channels (e.g. announcement channels).
* Updated /r34
  * Added AI tag suggestions.
* Updated /stats
  * Fixed an issue that could cause a 400 error.
* Polished the display of the `y!help` command.
* Fixed an issue in the item system that could cause a MemoryError.
* Fixed an issue where Ctrl+C could not properly shut down the bot.
* Fixed a "Task was destroyed but it is pending!" error.
* Improved logging system error handling.
* Fixed some bugs.

## 0.17.2
### Changes:
* Added /earthquake (OXWU Earthquake Monitoring System)
  * `/earthquake set-alert-channel`: Set the earthquake early-warning notification channel
  * `/earthquake set-report-channel`: Set the earthquake report notification channel
  * `/earthquake query-warning`: Manually fetch the latest earthquake early warning
  * `/earthquake query-report`: Manually fetch the latest earthquake report
  * Supports automatic push notifications for earthquake early warnings and reports.
* Updated /music
  * Added video thumbnail display.
* Fixed a logging system display issue.
* Fixed some bugs.

## 0.17.1
### Changes:
* Updated /ai
  * Switched the AI model to use `openai`.
  * Added Discord mention text processing; the AI can now correctly understand mentioned users, channels, roles, etc.
  * The AI now knows the username of the user currently talking to it.
  * Added a safeguard: @everyone and @here mentions are now blocked in AI responses.
  * Updated the system prompt to require compliance with Discord's Terms of Service.
  * Allowed abstract jokes.
* Added AI text commands
  * `y!ai`: Chat with the AI
  * `y!ai-new`: Start a new conversation
  * `y!ai-clear`: Clear conversation history
  * `y!ai-history`: View conversation history
* Added translations for AI-related commands.
* Updated /music
  * Fixed multiple issues.
* Fixed an issue with /ai-clear.
* Fixed some bugs.

## 0.17.0
### Changes:
* Added /changelog
  * View the bot's changelog. (handwritten)
* /changelogs -> /git-commits
  * Renamed the command.
* Added /music
  * Music playback system.
  * Use `y!help Music` to view command descriptions.
* Added /ai
  * It's... a free AI, don't expect too much...
* Updated /dynamic-voice play-audio
  * No longer stays connected to the voice channel persistently.
* Updated /get-command-mention
  * Added command autocomplete.
* Updated the logging system
  * Removed unnecessary codeblock markers.
* Removed offline messages due to Discord restrictions.
* Fixed some bugs.

## 0.16.14
### Changes:
* Fixed some bugs.

## 0.16.13
### Changes:
* Updated /report
  * Fixed an issue where reports could not be rejected.
* Added translations for some command options.
* Updated /info
  * Added a `full` option to control whether the complete module list is shown.
* Added a status shown when the bot starts up.
* Updated an easter egg lol
* Fixed some bugs.

## 0.16.12
### Changes:
* Updated statistics | /stats
  * Can now be run as a User install.
  * Disabled ephemeral messages.
* Updated /info
  * Added the application command count.
* Updated dsize -> /item use Cloud Ruler
  * Fixed an issue where check-ins weren't being recorded.
* Added /user-appeal-channel
  * Can now choose whether to allow punished users to appeal.
* Updated /autopublish
  * The rate limit is now applied per channel.
* Fixed /nitro
  * Thanks to @ting's help, it now works properly.
* New /automod feature
  * Added scam trap (scamtrap)
* Updated moderation action commands
  * Added unmute/untimeout, unban
* Added command usage count to the bot status.
* Updated /r34: Replaced the removed-tag list display with a count.
* Added an easter egg, find it yourself lol
* Fixed some bugs.

## 0.16.11
### Changes:
* Added statistics | /stats
  * Starting from this version (0.16.11), command usage counts will be recorded (no data included).
* Added offline messages (broken).
* Switched the AI model to use `openai-fast`.
* Fixed some bugs.

## 0.16.10
### Changes:
* Updated auto-reply | /autoreply
  * Can now use `{react:emoji}` to add a reaction to a message.
  * Example: `{react:↖️}` `{react:<:good:1295339514868400209>}`
* Fixed some bugs.

## 0.16.9
### Changes:
* Updated /owoify | `y!owoify`
  * Added some replacement words.
  * Adjusted the prefix+suffix probability from 10% to 40%.
  * Adjusted the stutter probability from 10% to 20%.
* Fixed a display issue with /autoreply help
  * Removed the codeblock.
* Updated /r34: Added AI filtering and caching
* Fixed some bugs.

## 0.16.8
### Changes:
* Added /owoify | `y!owoify` | message command menu
  * Makes messages cuter!
  * Use /owoify or `y!owoify`
  * Or you can also use the message command menu!
* Fixed some bugs.

## 0.16.7
### Changes:
* Updated /autoreply help
  * Fixed a previously known issue: missing line breaks
  * Also forgot to remove the codeblock, sorry
* All commands that previously required Administrator permission have now also been made available with Manage Server permission.
  * /dsize-settings
  * /change
  * /settings-punishment-notify
  * /report
  * /webverify
  * /autopublish
  * `y!setprefix`
  * If you want to change slash command permissions, go to `Server Settings > Integrations > Yee` to adjust permissions.
* Added: mentioning the bot directly now shows the command prefix.
* Fixed an issue with the announcement display time in `y!moderate` | `y!moderatereply`.
* Fixed an uptime display issue.
* Fixed some bugs.

## 0.16.6
### Changes:
* Updated auto-reply | /autoreply
  * Added /autoreply help: get help with auto-reply
    * ⚠️ Known issue: missing line breaks and codeblock not removed
  * Added /autoreply test: test auto-reply variables
* Updated auto-publish | /autopublish
  * Limited to 10 published messages per hour to avoid the bot being rate-limited.
* Fixed some bugs

## 0.16.5
### Changes:
* Added `y!setprefix`
  * Change this bot's prefix.
* Added /serverinfo | `y!serverinfo`
  * View information about the current server.
* Updated /info | `y!info`
  * Added the database record count.
* Updated /fake-blacklist
  * If the specified person is this bot, no one will be able to impersonate you.
* Fixed some bugs
* Added a game that's still in development, ~~but I disabled it, it's in the test bot~~

## 0.16.4
### Changes:
* Added /ping
  * Check the bot's latency
* Added /nitro
  * Prevents public Nitro gifts from being stolen by selfbots
  * Will absolutely not steal your Nitro [related source code](<https://github.com/AvianJay/useless-script/blob/main/discord%2FUtilCommands.py#L422-L506>)
  * ⚠️ This feature hasn't been tested yet because the developer has no money
  * Update: tested, works properly
* Changed the display style of /r34
* Fixed some bugs

## 0.16.3
### Changes:
* Added /fake-blacklist
  * Users added to the blacklist will never be able to impersonate you again.
* Added regex (regular expression) support to auto-reply
* Improved auto-reply (hopefully)
* Web verification now supports setting warning regions
* Fixed some bugs

## 0.16.2
### Changes:
* Added mobile support for the /dsize-feedgrass submission editor
* When a submission is approved, the bot will now attempt to DM the user.

## 0.16.0
### Changes:
* You can now use /contribute to submit images, including:
  * Images used by /dsize-feedgrass
  * "What Is This Guy Saying" in the message command menu
  * ⚠️ Only one submission allowed every five minutes.
* Fixed an issue where the Cloud Ruler wasn't being recorded in the database.
