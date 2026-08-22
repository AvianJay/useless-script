# AutoReply - Auto Reply

AutoReply can automatically reply with text, embeds, stickers, reactions, or delayed messages based on trigger conditions you configure, and can combine this with variables for conditional logic.

> Note
> Each guild's AutoReply currently has a trigger rate limit of `up to 3 times per second`.
> If there's a template syntax error, that reply becomes an empty string `""` and is not sent.

## Commands

- `/autoreply add`: add a new autoreply rule
- `/autoreply builder`: build a rule step by step using the interactive builder
- `/autoreply remove`: remove an autoreply rule
- `/autoreply edit`: edit an existing rule
- `/autoreply quickadd`: add extra trigger(s) / response(s) to an existing rule
- `/autoreply list`: list all current rules
- `/autoreply clear`: clear all rules
- `/autoreply test`: test the template substitution result
- `/autoreply template`: apply a built-in template pack
- `/autoreply export`: export as JSON
- `/autoreply import`: import from JSON
- `/autoreply ignore`: set ignored channels
- `/autoreply help`: show in-Discord help

## Builder

`/autoreply builder` opens an interactive interface where you can directly adjust:

- Trigger word(s)
- Reply content
- Trigger mode
- Whether to use reply
- Channel restriction mode
- Specified channel(s)
- Trigger probability

The `trigger / response` fields in the builder support:

- One item per line
- If you write only a single line, you can also separate multiple items with `,`

## Trigger Modes

- `contains`: triggers if the message contains any one of the triggers
- `equals`: the message must exactly match the trigger
- `starts_with`: triggers only if the message starts with the trigger
- `ends_with`: triggers only if the message ends with the trigger
- `regex`: matches using a Python regex

## Special Triggers

Besides the text-matching modes above, you can also use `type:name` to match directly against a Discord system message's `message.type`.

- `type:join`: equivalent to `discord.MessageType.new_member`
- `type:boost`: equivalent to `discord.MessageType.premium_guild_subscription`
- You can also enter the native Discord name directly, e.g. `type:premium_guild_tier_1`

Rules:

- A `type:` trigger checks `message.type` first
- If the same rule also has a regular trigger, the message content is still evaluated using modes like `contains` / `equals` / `regex`
- An unknown `type:` name raises an error immediately when adding or editing a rule, rather than failing silently

For example:

```text
Mode: equals
Trigger: type:join
Reply: Welcome {user} to {guild}!
```

```text
Mode: contains
Trigger: type:boost, boosted
Reply: Thanks {user} for boosting the server!
```

## Basic Variables

- `{user}`: mention of the user who triggered it
- `{content}`: the original message content
- `{guild}` / `{server}`: server name
- `{guildid}`: server ID
- `{guildicon}`: server icon URL
- `{guildbanner}`: server banner URL
- `{guildowner}`: server owner's name
- `{guildownerid}`: server owner's ID
- `{guildmembers}`: server member count
- `{guildroles}`: server role count
- `{guildboosts}`: server boost count
- `{channel}`: channel name
- `{author}` / `{member}`: name of the user who triggered it
- `{authorid}`: ID of the user who triggered it
- `{authoravatar}`: avatar URL of the user who triggered it
- `{authorbanner}`: banner URL of the user who triggered it
- `{authorcreated}`: account creation time of the user who triggered it, in `YYYY/MM/DD HH:MM:SS` format
- `{role}`: highest role name of the user who triggered it
- `{id}`: ID of the user who triggered it
- `{null}`: an empty string, useful for `if` comparisons
- `\n`: newline
- `\t`: tab

> Note
> For URL variables like `icon` / `banner`, if the target hasn't set an image, an empty string is returned.

## Date & Time

- `{date}`: `YYYY/MM/DD`
- `{year}` / `{month}` / `{day}`
- `{time}`: 12-hour format, e.g. `下午 08:23`
- `{time24}`: 24-hour format, e.g. `20:23`
- `{hour}` / `{minute}` / `{second}`
- `{timemd:t}` ~ `{timemd:R}`: outputs a Discord timestamp tag

> Note
> Date / time variables use the bot host's local timezone.
> The current deployment environment is `Asia/Taipei`, i.e. `UTC+8`.

## Content Slicing

- `{contentsplit:0}`: equivalent to `content.split()[0]`
- `{contentsplit:1}`: gets the 2nd word
- `{contentsplit:1-}`: gets from index `1` to the end
- `{contentsplit:-4}`: gets from the start to index `4`
- `{contentsplit:1-2}`: gets from index `1` to `2`

> Note
> The end of a range is inclusive.
> For example, `1-2` gets the 2nd and 3rd words.

## Math

- `{math:(1+2*3)}`
- Only `+ - * /` are supported
- Parentheses are supported
- Other variables can be applied inside `math` first

For example:

```text
{math:(10/4)}
{math:({contentsplit:1}+5)}
{math:({hour}+1)}
```

Limits:

- Numeric literals must be within `-1000 ~ 1000`
- Division by 0 or an invalid expression is treated as a syntax error

## Random / Interactive / State Variables

- `{random}`: a random number from 1 to 100
- `{randint:min-max}`: an integer within the specified range
- `{random_user}`: picks a member from recent messages
- `{react:emoji}`: adds a reaction to the triggering message
- `{sticker:ID}`: attaches a sticker
- `{newmsg:second}`: delays sending the next message
- `{edit:second}`: delays editing the current autoreply message
- `{mention:true}` / `{mention:false}`: controls whether `@everyone` and role mentions are allowed
- `{uservar:key}`: reads a user variable
- `{uservar:key:value}`: writes a user variable, without output
- `{guildvar:key}`: reads a guild variable
- `{guildvar:key:value}`: writes a guild variable, without output

Limits:

- `{newmsg:second}`: up to `2` allowed, seconds must be `1 ~ 3`
- `{edit:second}`: up to `4` allowed, seconds must be `1 ~ 3`
- By default `allowed_mentions` only allows users; to allow `@everyone` / role mentions, add `{mention:true}`
- `uservar` allows up to `5` keys
- `guildvar` allows up to `10` keys
- key / value length is at most `100`

## Conditionals

Three forms are supported:

```text
{if:condition:true_content:else:false_content}
{if:condition:true_content:false_content}
{if:condition:true_content}
```

Supported operators:

- `==`
- `!=`
- `<=`
- `>=`
- `&&`
- `||`

Rules:

- `&&` is evaluated before `||`
- `true / false` are treated as booleans
- Pure numbers are compared as numbers
- Other content is compared as strings

For example:

```text
{if:{contentsplit:1}==true:You sent true!:else:Its false}
{if:{contentsplit:1}!={null}:You entered something:else:Blank}
{if:{contentsplit:2}>=10:Greater than or equal to 10:else:Less than 10}
{if:{contentsplit:1}==true&&{hour}>=12:Good afternoon true:else:Not afternoon yet}
{mention:true}@everyone Maintenance starting
```

## Embed Replies

- `{embedtitle:Title}`
- `{embeddescription:Description}`
- `{embedurl:URL}`
- `{embedimage:SomeLink}`
- `{embedthumbnail:SomeLink}`
- `{embedcolor:57F287}`
- `{embedfooter:Footer}`
- `{embedfooterimage:SomeLink}`
- `{embedauthor:Author}`
- `{embedauthorurl:URL}`
- `{embedauthorimage:SomeLink}`
- `{embedtime:true}`
- `{embedfield:FieldName:Content}`

The content can use other `{}` variables as well.

For example:

```text
{embedtitle:Check-in Successful}
{embedurl:https://example.com}
{embeddescription:{user} completed check-in at {date} {time24}}
{embedauthor:System}
{embedauthorurl:https://example.com/profile}
{embedauthorimage:{authoravatar}}
{embedcolor:57F287}
{embedfield:Server:{guild}}
{embedfooter:AutoReply Template}
{embedfooterimage:{authoravatar}}
{embedtime:true}
```

## Channel Restrictions

You can restrict trigger channels from two places:

- `/autoreply ignore`
- The rule's own `channel_mode`

`channel_mode`:

- `all`: triggers in all channels
- `whitelist`: triggers only in specified channels
- `blacklist`: does not trigger in specified channels

## Built-in Template Packs

- `daily_greetings`: good morning / good afternoon / good night / hi
- `mini_commands`: `!say` / `!time` / `!date` / `!roll`
- `chat_fun`: check-in, random picks, reactions, small interactions

## Common Examples

### Basic Greeting

```text
Mode: contains
Trigger: hi, hello
Reply: Hello {user}
```

### `!say aaa`

```text
Mode: starts_with
Trigger: !say
Reply: {contentsplit:1-}
```

### Morning Greeting Logic

```text
Mode: starts_with
Trigger: good morning
Reply: {if:{hour}>=5:{if:{hour}<=11:Good morning {user}:else:It's already {time24} and you're still saying good morning}}
```

### Conditional Branch

```text
Mode: starts_with
Trigger: check
Reply: {if:{contentsplit:1}==true:You entered true:else:You did not enter true}
```

### Conditional Shorthand

```text
{if:{contentsplit:1}==true:Yes:No}
```

### Embed Check-in

```text
Mode: equals
Trigger: check-in
Reply: {embedtitle:Check-in Successful}{embeddescription:{user} completed check-in at {date} {time24}}{embedcolor:57F287}{embedfield:Server:{guild}}
```

### Delayed Messages / Editing

```text
Mode: equals
Trigger: countdown
Reply: 3...{edit:1}2...{edit:2}1...{newmsg:3}Start
```

### Remembering a User Variable

```text
Mode: starts_with
Trigger: !pet
Reply: {uservar:pet:{contentsplit:1-}}Your pet has been remembered
```

```text
Mode: equals
Trigger: !mypet
Reply: The pet you saved is: {uservar:pet}
```

### Math + Variables

```text
Mode: starts_with
Trigger: calc
Reply: Answer: {math:({contentsplit:1}+5)}
```

## Testing & Debugging

- First use `/autoreply test` to see the template substitution result
- `starts_with` combined with `{contentsplit:1-}` is great for building simple commands
- If there's no reply at all, check:
  - whether the template has a syntax error
  - whether a channel restriction is blocking it
  - whether you're hitting the `3 times per second` rate limit
