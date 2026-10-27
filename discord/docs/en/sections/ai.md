# 🤖 ai — AI Chat

Chat with AI using a selectable model, with support for images, multi-turn conversations, tools, personal long-term memory, and shared server context. The AI deducts **全域幣 (the bot's global currency)** based on actual usage — it is not a free feature.

## Conversation Commands

| Command | Description |
| --- | --- |
| `/ai message [image] [new_conversation] [model]` | Chat with the AI; you can attach an image, start a new conversation, or specify a model |
| `/ai-clear` | After confirmation, clears the conversation history for the current server/DM scope |
| `/ai-history` | Privately shows the most recent 10 conversation entries for the current scope |
| `/ai-set-default-model model` | Set your own cross-server default text model |
| `/ai-set-response-view container cost model` | Set whether the container, billing info, and model info are shown |
| `y!ai [model] [message]` | Prefix version of AI chat; you can put the model name at the start |
| `y!ai-new [message]` | Clears the history for the current scope and starts a new conversation |
| `y!ai-clear` | Clears the conversation history for the current scope |
| `y!ai-history` | View the conversation history for the current scope |

> `y!` is the default prefix; if the server has changed its prefix, use that server's current prefix instead.

AI replies may be laid out as cards or image galleries, or include link buttons. When you need to pick one of a few options, the AI may occasionally add follow-up buttons or a menu; clicking one sends that text to the AI as your next message and is charged like a normal message. Anyone can click them by default, but the AI can limit them to the person who asked.

## Public Knowledge and Contributions

Public knowledge is shared by all users, servers and DMs, stored separately from personal/server memory. Every AI request automatically retrieves relevant approved entries; the AI can proactively search and read full entries as needed.

| Command | Description |
| --- | --- |
| `/ai-knowledge search [query]` | Search approved entries, or show recent entries without a query |
| `/ai-knowledge submit title content [source_url] [knowledge_id] [expected_version]` | Submit public knowledge; revisions require the ID, current version and complete replacement content |
| `/ai-knowledge status submission_id` | Privately check your submission; bot owners can inspect all submissions |
| `y!ai-knowledge [query]` | Prefix knowledge search |
| `y!ai-knowledge submit "Title" content` | Prefix contribution |
| `y!ai-knowledge status submission_id` | Prefix submission status |
| `y!ai-knowledge review submission_id` | Bot owner: open review buttons; View submission shows the full draft privately |
| `y!ai-knowledge approve submission_id [reason]` | Bot owner: approve a submission, including one rejected by the AI |
| `y!ai-knowledge reject submission_id [reason]` | Bot owner: reject an unpublished submission |
| `y!ai-config knowledge-channel #channel` | Configure notifications; use `off` to disable |
| `y!ai-knowledge remove knowledge_id` | Bot owner: withdraw knowledge from search and automatic context |

- Background reviews use the existing `ai_review_model`, configured with `y!ai-config review-model`. Publication requires automatic approval or manual approval by a bot owner.
- Review notifications include View submission, Approve manually and Reject manually buttons. Only bot owners can use them, and they work after a restart. Pending, in-progress, rejected and failed submissions can be reviewed manually. Decisions record the reviewer ID and trigger a new notification; late AI results cannot overwrite them. Use `review` or `approve` with the submission ID for older notifications.
- Manual approval still checks revision versions and cannot overwrite newer content or restore withdrawn entries. Repeated approval does not republish; use `remove` to withdraw published knowledge.
- Titles allow 80 characters and content 4,000 characters; each user can create 5 submissions per hour. Identical submissions are deduplicated. Revision version checks prevent overwriting newer content.
- Failed reviews retry up to 3 times. Pending reviews resume after restart; failed notifications are retained for retry and delivered after a channel is configured.
- Administrator notifications include the submission ID, result, model and explanation, without reposting rejected/unreviewed text. Unpublished submissions are visible only to their contributor and bot owners.
- The AI proactively uses, searches and maintains memory without waiting for a "remember" command, and proposes reusable public solutions and lessons. Personal and server-only information stays in its original scope. Deletion still requires an explicit request.
- Reviews have no additional fixed bot-currency fee; normal AI conversations retain their existing billing. Model approval does not guarantee factual correctness; verify time-sensitive claims.

## Memory & Cross-Server Context

The AI uses three different types of context; clearing one does not clear the others:

| Type | Scope & Behavior |
| --- | --- |
| Conversation history | Kept separately per server; DMs use a global/DM scope. `/ai-clear` and `y!ai-clear` only clear the conversation history for that scope. |
| `user_global` personal memory | Tied to the current user and shared across servers. The AI automatically pulls in relevant memories, and can also proactively update them when the user clearly states a stable, low-risk preference or personal fact. |
| `guild_shared` server memory | Shared only within the current server, used for server atmosphere, shared preferences, common lists, or shared jokes/references. Can only be modified when explicitly requested by someone with Manage Server/Administrator permission. |

- Each long-term memory entry is limited to 2,000 characters, with a maximum of 80 entries per memory space; overly long content will be asked to be split into multiple entries rather than silently truncated.
- The system prioritizes memories relevant to the current message, then recently updated memories, when populating the model's context; content not directly included can still be searched on demand by the AI memory tools.
- You can simply say "remember that I prefer...", "update the preference I just mentioned...", or "forget...". Deleting a memory must always be explicitly requested by the user.
- When a user explicitly asks the AI to recall existing AI conversations from other servers or from the global/DM scope, the AI can search on demand, but it can only read that user's own records; shared memories from other servers will not be pulled in.
- Do not ask the AI to remember passwords, tokens, API keys, precise financial details, ID information, medical/legal privacy data, or other highly sensitive information.

## External Data Tools

- `search_google` and `search_google_images` search via Serper; `fetch_webpage` extracts readable page content via Serper.
- Tool previews for `fetch_webpage` and `fetch_raw` show only the domain, without URL paths, query parameters, or fragments.
- Reply images, thumbnails, and galleries can display image URLs from any HTTP/HTTPS host. Image downloads for `image_analyze` and generation references remain restricted to Discord CDN sources.
- `fetch_raw` has the bot directly GET a public URL, suitable for reading raw JSON, plain text, HTML, XML, YAML, or source code. It does not go through Serper, and applies the SOCKS5 proxy configured via `!aicfg proxy`.
- `fetch_raw` only allows standard ports on public HTTP/HTTPS; it does not accept URL credentials, localhost, internal/private addresses, or binary content. Each redirect is re-validated, downloads are capped at 1 MB, and a single call returns at most 12,000 characters.
- Like other search results, raw content is untrusted external data; the AI can only treat it as reference material and must not follow any instructions within it that ask it to change its rules, leak data, or perform actions.
- `fetch_raw` does not consume Serper quota and does not incur an additional fixed 全域幣 charge; regular `/ai` text input/output is still billed at the selected model's rate.
- `upload_html` uploads a single AI-generated HTML file to [sharemyhtml.com](https://sharemyhtml.com) and returns a public URL, suitable for web pages, HTML examples, or interactive demos. Anyone with the link can open it, so do not ask for passwords, tokens, or personal data to be included; each file is limited to 5 MB, and the AI cannot edit or delete a page after uploading it.

## Reading Messages and Screenshots

- Message reads first return Markdown-preserving text plus structured embeds, attachments, polls, stickers, reactions, forwards, and Components V2 data.
- The AI can look up any Discord user by ID. Members of the current server include their nickname, roles, and permissions; users outside the server, or any lookup made in DMs, only return public info such as name, avatar, and account creation date.
- The AI creates a message screenshot only when visual layout or Discord-specific presentation cannot be determined from text. The image is reconstructed by `chat_exporter`, not captured from the native Discord client; structured text fills in formats the reconstruction cannot show.
- Screenshots contain only the target message by default. When context is requested, they may include up to nine earlier messages from the same author within five minutes. Spoilers are expanded.
- Attaching the screenshot alone has no separate image-analysis fee. Asking the AI to inspect it uses the image-analysis tool at the built-in rate of 25 全域幣 per call, with a refund on failure.
- External webpage screenshots are used only for visual inspection by `browser_view` and are never attached to replies. External images are displayed using their direct image URLs rather than webpage screenshots.
- Messages, screenshots, and visible screenshot text are untrusted content and are never treated as system instructions.

## Billing

### Text Conversations

Text models are billed by character count:

```text
Input cost  = actual input characters × the selected model's per-character rate
Output cost = actual output characters × the selected model's per-character rate
Total cost  = input cost + output cost + any additional paid image tools used
```

- The input cost is deducted before the request is sent to the model; if your 全域幣 balance isn't enough to cover the input, the request will not be sent.
- After the output is complete, the cost is deducted based on the actual reply character count; if the AI uses `send_as_file` or `upload_html`, the full file or HTML content's character count is also counted as output.
- By default, a regular reply shows the rate, input/output character counts, and the actual total amount charged; using `/ai-set-response-view` to hide billing info only hides it from the display — it does not stop the charge.
- If the model call fails, the already-deducted input cost is refunded.

### Default Text Model Rates

The table below shows the built-in default values, in units of "全域幣 per character." The bot owner can adjust models and rates in real time, so always refer to the rate shown in the AI's reply for the actual value.

| Model | Default Rate |
| --- | ---: |
| `openai-fast` | 0.05/char |
| `openai` | 0.10/char |
| `gpt-5-mini` | 0.10/char |
| `openai-large` | 0.45/char |
| `perplexity-fast` | 0.10/char |
| `claude-fast` | 0.15/char |
| `kimi-k2.6` | 0.05/char |
| `gemma-4-31b` | 0.10/char |
| `glm-5.1-t` | 0.10/char |
| `qwen3.5-397b-a17b-t` | 0.15/char |

The built-in global default model is `kimi-k2.6` (subject to change based on model availability); users can change this themselves with `/ai-set-default-model`.

### Image Tool Costs

| Feature | Built-in Default Cost | Notes |
| --- | ---: | --- |
| Attaching an image directly in `/ai` | No extra fixed cost | Still billed for input/output characters based on the selected text model |
| Analyzing a Discord CDN image URL | 25 全域幣/use | Charged separately whenever the AI needs to call the image analysis tool |
| Inspecting a reconstructed Discord message screenshot | 25 全域幣/use | No charge for attachment-only display; charged only for visual inspection |
| AI image generation | `gpt-image-2`: 250 全域幣/image | Charged at the actual model rate × number of images requested; under-delivery refunds the cost for the missing images |
| Google Image Search | No extra fixed 全域幣 cost | Regular text input/output is still billed at the selected model's rate; the system downloads and reviews up to 3 candidates and attaches only one image that passes review |
| AI video generation | Currently disabled | While disabled, it does not enter the billing flow |

If a paid image tool fails to execute, the cost already charged for that tool is refunded.

### Reference Images and Editing

- Attach an image in `/ai` and ask for an edit or reference-based generation. The AI can pass the current attachment or one Discord CDN image URL to `generate_image`. One reference image is supported per call; it is converted to PNG for the OpenAI-compatible Images API's `images.edit`. Text-only generation continues to use `images.generate`.
- `gpt-image-2` supports image input by default. After adding another image model, the bot owner can enable its tag with `y!aicfg image-input-tag <model> on`, or disable it with `off`. First confirm that the current API provider supports image edits for that model. This setting is separate from text models' vision tags.
- `y!aicfg image-models` marks enabled models with `[image-input]`. An unmarked model, invalid image, or failed download is rejected before the generation charge. Generation failures are refunded, and the existing per-image model rate applies.

### Google Image Search & Safety

- The AI can search for public images via Serper Images. Since this endpoint has no SafeSearch option, the bot downloads up to 3 candidates in sequence and passes them to the currently configured AI review model; if review times out, the format is invalid, or the result is inconclusive, the image will not be shown.
- Each search consumes the bot owner's Serper API quota, and reviewing candidates also generates provider usage for the review model; currently neither is separately charged to the user as a fixed 全域幣 cost, but regular `/ai` text input/output is still billed at the selected model's rate.
- Search-tool images that pass review have their original metadata stripped and are re-encoded as PNG/JPEG before being uploaded as a Discord attachment. This download flow enforces limits on size, pixel dimensions, format, redirects, and internal/private addresses.
- Image review can still make mistakes, and Google Images search results do not imply copyright clearance. The AI should include the source page; images are used only for the current reply and are not cached long-term.
- The bot owner can use `y!aicfg proxy socks5://host:1080` to set a dedicated SOCKS5 proxy for image downloads, `y!aicfg proxy` to view the masked status, or `y!aicfg proxy clear` to clear it. `sock5://` is automatically corrected to `socks5://`.
- The proxy only applies to images the bot downloads itself and to `fetch_raw`; it does not change the connection used for Discord, OpenAI-compatible APIs, or the Serper API. If the URL contains credentials, the status and reply will only show a masked value.

## Mention & Reply Mode

This feature is disabled by default. Users with "Manage Server" or "Administrator" permission can configure it with the following command:

| Command | Description |
| --- | --- |
| `/ai-admin mention-mode enabled` | Enable or disable mention/reply triggers for this server |

- For a direct mention, the bot mention must be the first non-whitespace content in the message; placing it mid-sentence or at the end will not trigger it.
- For a reply trigger, you must reply to a temporary message that the AI successfully sent after this feature was enabled, and that Discord reply must keep the mention of the bot enabled; a reply with "mention" turned off will not trigger it.
- After removing the bot mention from the start of the message, there must still be text content; if the message is only a mention, whitespace, or only an attachment, the bot will not respond. Regular bot text commands will also not be triggered again.
- AI reply message IDs are only cached in memory, with a maximum of 200 entries per server, kept for up to 24 hours; they are cleared when the bot restarts or this feature is disabled, so old AI messages from before a restart will not trigger it.
- The system does not identify AI messages via Components V2, since other features may use the same component format; it only accepts reply message IDs actually recorded by this AI module.
- Mentions and replies follow the same full processing and billing flow as `y!ai`, with no extra fixed cost; text input/output is still billed at the user's default model rate, and if the server has designated a payer, that payer is charged instead.

## Server Payer

By default, each user who makes an AI request pays from their own 全域幣 balance. Users with "Manage Server" or "Administrator" permission can set themselves as the unified payer for this server; once set, text and paid image tool usage by everyone else in the server will instead be charged to the designated payer's 全域幣.

| Command | Description |
| --- | --- |
| `/ai-admin billing set` | Set yourself as this server's AI payer |
| `/ai-admin billing view` | View the current payment method/payer |
| `/ai-admin billing clear` | Clear the designated payer and revert to everyone paying individually |
| `y!ai-server-billing` | View the current payment method/payer |
| `y!ai-server-billing set` | Set the payer to yourself |
| `y!ai-server-billing clear` | Clear the designated payer |

Currently you cannot designate another member as the payer; `set` only sets the admin who runs the command themselves.

## Server Custom AI Prompt

Administrators can provide server context, reply style, or preferences, up to 1,800 characters. This is different from `guild_shared` memory: the custom prompt is a fixed instruction, whereas server memory is long-term data that the AI can search and update.

| Command | Description |
| --- | --- |
| `/ai-admin prompt set prompt` | Set the server's custom AI prompt |
| `/ai-admin prompt view` | View the current prompt |
| `/ai-admin prompt clear` | Clear the current prompt |
| `y!ai-server-prompt` | View the current prompt |
| `y!ai-server-prompt [content]` | Set the prompt |
| `y!ai-server-prompt clear` | Clear the prompt |


## Task Skills and File Analysis

The AI loads task instructions and tools only when needed. Available skills cover browser automation, web research with sources, Discord conversation investigation, reading attachments, data analysis, and text posting on Meta Threads. Ask naturally, for example: "Summarize yesterday's discussion with message links", "Compare these sources", or "Analyze this CSV and attach the cleaned result".

- Attach a document through `/ai`'s `file` option, attach it to a prefix-command message, or reference a visible message containing the file. Supported formats include text, CSV/TSV, JSON, PDF, DOCX and XLSX, up to 8 MB. PDF text extraction does not include OCR. Spreadsheet formulas are not executed.
- Large results are kept as temporary resources that the AI can read in slices. JavaScript variables and resources last for one request and are removed after completion, cancellation or timeout. Textual CSV/JSON results can be returned as attachments.
- Browser actions retain the existing Discord Allow/Reject flow. The Node runtime calls the same permission-checked browser and Discord handlers. It cannot access host files, bot credentials or the network directly.
- Browsing now runs through Node REPL by default, with Playwright-style role/CSS locators, real keyboard input and viewport-coordinate mouse actions backed by Python Playwright. Unavailable Docker startup exposes the direct tools as a fallback. A ref timeout should lead to a fresh inspection and stable locator rather than repeated clicks on the same ref.
- The progress label says Node REPL, and the response's model footer includes `Node REPL` only after a kernel really ran. Tool results and redacted diagnostic logs include the Node version, kernel ID, evaluation number and bridge-call count. Scripts and browser contents remain redacted from those logs.
- Meta Threads uses the configured profile's existing login and currently supports text and links, not media uploads. The AI publishes only when asked to publish, verifies the result, and returns its permalink. Drafting or testing the skill does not publish anything. Account interactions are restricted to bot owners and explicitly enabled publishers.

### Owner Setup for Skill Execution

Install the updated Python requirements and run a Linux Docker daemon on the bot host. Prepare the sandbox image with `docker pull node:24-alpine`. The bot does not automatically pull images or fall back to host execution. If the sandbox is unavailable, direct browser tools and the existing Piston execution tool remain usable.

- `!aicfg node-repl` displays the current sandbox setting.
- `!aicfg node-repl on [image]` enables the kernel using a pre-pulled Node-compatible image; `off` disables it.
- `!aicfg browser-cdp <profile CDP URL>` configures the managed browser as before.
- `!aicfg threads-publisher <Discord user ID> on` permits that user to interact with the Threads account; `off` revokes the extra permission. Bot owners are already permitted.

Each kernel has no network, a read-only filesystem, one read-only worker-script mount, an unprivileged user, and CPU/memory/process limits. At most two kernels run per bot instance. Requests have a ten-minute deadline, up to twelve skill/tool rounds and a shared 48-call budget, including calls made through JavaScript. Containers are removed when the request ends.
