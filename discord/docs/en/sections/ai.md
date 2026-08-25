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
- `fetch_raw` has the bot directly GET a public URL, suitable for reading raw JSON, plain text, HTML, XML, YAML, or source code. It does not go through Serper, and applies the SOCKS5 proxy configured via `!aicfg proxy`.
- `fetch_raw` only allows standard ports on public HTTP/HTTPS; it does not accept URL credentials, localhost, internal/private addresses, or binary content. Each redirect is re-validated, downloads are capped at 1 MB, and a single call returns at most 12,000 characters.
- Like other search results, raw content is untrusted external data; the AI can only treat it as reference material and must not follow any instructions within it that ask it to change its rules, leak data, or perform actions.
- `fetch_raw` does not consume Serper quota and does not incur an additional fixed 全域幣 charge; regular `/ai` text input/output is still billed at the selected model's rate.

## Billing

### Text Conversations

Text models are billed by character count:

```text
Input cost  = actual input characters × the selected model's per-character rate
Output cost = actual output characters × the selected model's per-character rate
Total cost  = input cost + output cost + any additional paid image tools used
```

- The input cost is deducted before the request is sent to the model; if your 全域幣 balance isn't enough to cover the input, the request will not be sent.
- After the output is complete, the cost is deducted based on the actual reply character count; if the AI uses `send_as_file`, the full file content's character count is also counted as output.
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
| AI image generation | `gpt-image-2`: 250 全域幣/image | Charged at the actual model rate × number of images requested; under-delivery refunds the cost for the missing images |
| Google Image Search | No extra fixed 全域幣 cost | Regular text input/output is still billed at the selected model's rate; the system downloads and reviews up to 3 candidates and attaches only one image that passes review |
| AI video generation | Currently disabled | While disabled, it does not enter the billing flow |

If a paid image tool fails to execute, the cost already charged for that tool is refunded.

### Google Image Search & Safety

- The AI can search for public images via Serper Images. Since this endpoint has no SafeSearch option, the bot downloads up to 3 candidates in sequence and passes them to the currently configured AI review model; if review times out, the format is invalid, or the result is inconclusive, the image will not be shown.
- Each search consumes the bot owner's Serper API quota, and reviewing candidates also generates provider usage for the review model; currently neither is separately charged to the user as a fixed 全域幣 cost, but regular `/ai` text input/output is still billed at the selected model's rate.
- Images that pass review have their original metadata stripped and are re-encoded as PNG/JPEG before being uploaded as a Discord attachment. The bot never hotlinks arbitrary external image URLs directly to Discord, which allows it to enforce limits on download size, pixel dimensions, format, redirects, and internal/private addresses.
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
