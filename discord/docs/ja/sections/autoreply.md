# 自動返信 - 自動返信

AutoReply は、設定したトリガー条件に基づいて、テキスト、埋め込み、ステッカー、リアクション、または遅延メッセージで自動的に返信でき、これを条件ロジックの変数と組み合わせることができます。

> 注意
> 各ギルドの AutoReply には現在、`up to 3 times per second` というトリガー レート制限があります。
> テンプレートの構文エラーがある場合、その応答は空の文字列 `""` になり、送信されません。

## コマンド

- `/自動返信 追加`: 新しい自動返信ルールを追加します
- `/自動返信 ビルダー`: インタラクティブなビルダーを使用してルールを段階的に構築します
- `/自動返信 削除`: 自動返信ルールを削除します
- `/自動返信 編集`: 既存のルールを編集します
- `/自動返信 クイック追加`: 既存のルールに追加のトリガー/レスポンスを追加します
- `/自動返信 一覧`: 現在のルールをすべてリストします。
- `/自動返信 クリア`: すべてのルールをクリアします
- `/自動返信 テスト`: テンプレートの置換結果をテストします。
- `/自動返信 テンプレート`: 組み込みテンプレート パックを適用します
- `/自動返信 エクスポート`: JSON としてエクスポート
- `/自動返信 インポート`: JSON からインポート
- `/自動返信 除外`: 無視されるチャンネルを設定します
- `/自動返信 ヘルプ`: Discord 内のヘルプを表示

## ビルダー

`/自動返信 ビルダー` は、直接調整できる対話型インターフェイスを開きます。

- トリガーワード
- 返信内容
- トリガーモード
- 返信を使用するかどうか
- チャンネル制限モード
- 指定されたチャンネル
- 発動確率

ビルダーの `trigger / response` フィールドは以下をサポートします。

- 1 行に 1 つの項目
- 1行だけ記述する場合は、`,`で複数の項目を区切ることもできます。

## トリガーモード

- `contains`: メッセージにトリガーのいずれかが含まれている場合にトリガーされます。
- `equals`: メッセージはトリガーと正確に一致する必要があります
- `starts_with`: メッセージがトリガーで始まる場合にのみトリガーされます。
- `ends_with`: メッセージがトリガーで終了する場合にのみトリガーされます。
- `regex`: Python 正規表現を使用した一致

## 特別なトリガー

上記のテキストマッチングモードの他に、`type:name` を使用して、Discord システム メッセージの `message.type` と直接照合することもできます。

- `type:join`: `discord.MessageType.new_member` と同等
- `type:boost`: `discord.MessageType.premium_guild_subscription` と同等
- ネイティブの Discord 名を直接入力することもできます。 `type:premium_guild_tier_1`

ルール:

- `type:` トリガーは最初に `message.type` をチェックします
- 同じルールに通常のトリガーがある場合でも、メッセージの内容は `contains` / `equals` / `regex` のようなモードを使用して評価されます。
- 不明な `type:` 名は、ルールの追加または編集時に、何も通知せずに失敗するのではなく、ただちにエラーを発生させます。

たとえば:

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

## 基本的な変数

- `{user}`: トリガーしたユーザーの言及
- `{content}`: 元のメッセージの内容
- `{guild}` / `{server}`: サーバー名
- `{guildid}`: サーバーID
- `{guildicon}`: サーバーアイコンのURL
- `{guildbanner}`: サーバー バナー URL
- `{guildowner}`: サーバー所有者の名前
- `{guildownerid}`: サーバー所有者のID
- `{guildmembers}`: サーバーメンバー数
- `{guildroles}`: サーバーのロールの数
- `{guildboosts}`: サーバーブースト数
- `{channel}`: チャンネル名
- `{author}` / `{member}`: トリガーしたユーザーの名前
- `{authorid}`: トリガーしたユーザーの ID
- `{authoravatar}`: トリガーしたユーザーのアバター URL
- `{authorbanner}`: トリガーしたユーザーのバナー URL
- `{authorcreated}`: トリガーしたユーザーのアカウント作成時刻 (`YYYY/MM/DD HH:MM:SS` 形式)
- `{role}`: トリガーしたユーザーの最高のロール名
- `{id}`: トリガーしたユーザーの ID
- `{null}`: 空の文字列。`if` の比較に役立ちます。
- `\n`: 改行
- `\t`: タブ

> 注意
> `icon` / `banner` のような URL 変数の場合、ターゲットが画像を設定していない場合は、空の文字列が返されます。

## 日付と時刻

- `{date}`: `YYYY/MM/DD`
- `{year}` / `{month}` / `{day}`
- `{time}`: 12 時間形式、例: `下午 08:23`
- `{time24}`: 24 時間形式、例: `20:23`
- `{hour}` / `{minute}` / `{second}`
- `{timemd:t}` ~ `{timemd:R}`: Discord タイムスタンプ タグを出力します

> 注意
> 日付/時刻変数はボット ホストのローカル タイムゾーンを使用します。
> 現在のデプロイメント環境は `Asia/Taipei`、つまり `UTC+8` です。

## コンテンツのスライス

- `{contentsplit:0}`: `content.split()[0]` と同等
- `{contentsplit:1}`: 2 番目の単語を取得します
- `{contentsplit:1-}`: インデックス `1` から最後までを取得します
- `{contentsplit:-4}`: 先頭からインデックス `4` までを取得します。
- `{contentsplit:1-2}`: インデックス `1` から `2` までを取得します

> 注意
> 範囲の終わりも含みます。
> たとえば、`1-2` は 2 番目と 3 番目の単語を取得します。

## 数学

- `{math:(1+2*3)}`
- `+ - * /` のみがサポートされています
- 括弧がサポートされています
- 他の変数は最初に `math` 内に適用できます

たとえば:

```text
{math:(10/4)}
{math:({contentsplit:1}+5)}
{math:({hour}+1)}
```

制限:

- 数値リテラルは `-1000 ~ 1000` 以内である必要があります
- 0による除算または無効な式は構文エラーとして扱われます

## ランダム / インタラクティブ / 状態変数

- `{random}`: 1 ～ 100 の乱数
- `{randint:min-max}`: 指定された範囲内の整数
- `{random_user}`: 最近のメッセージからメンバーを選択します
- `{react:emoji}`: トリガーメッセージへの反応を追加します
- `{sticker:ID}`: ステッカーを添付します
- `{newmsg:second}`: 次のメッセージの送信を遅らせます
- `{edit:second}`: 現在の自動返信メッセージの編集が遅れます
- `{mention:true}` / `{mention:false}`: `@everyone` とロールの言及を許可するかどうかを制御します
- `{uservar:key}`: ユーザー変数を読み取ります
- `{uservar:key:value}`: 出力なしでユーザー変数を書き込みます
- `{guildvar:key}`: ギルド変数を読み取ります
- `{guildvar:key:value}`: 出力なしでギルド変数を書き込みます

制限:

- `{newmsg:second}`: `2` まで許可され、秒は `1 ~ 3` でなければなりません
- `{edit:second}`: `4` まで許可され、秒は `1 ~ 3` でなければなりません
- デフォルトでは、`allowed_mentions` はユーザーのみを許可します。 `@everyone` / ロールの言及を許可するには、`{mention:true}` を追加します
- `uservar` では、最大 `5` キーを使用できます
- `guildvar` では、最大 `10` キーを使用できます
- キー/値の長さは最大 `100` です

## 条件文

次の 3 つの形式がサポートされています。

```text
{if:condition:true_content:else:false_content}
{if:condition:true_content:false_content}
{if:condition:true_content}
```

サポートされている演算子:

- `==`
- `!=`
- `<=`
- `>=`
- `&&`
- `||`

ルール:

- `&&` は `||` より前に評価されます
- `true / false` はブール値として扱われます
- 純粋な数値は数値として比較されます
- 他のコンテンツは文字列として比較されます

たとえば:

```text
{if:{contentsplit:1}==true:You sent true!:else:Its false}
{if:{contentsplit:1}!={null}:You entered something:else:Blank}
{if:{contentsplit:2}>=10:Greater than or equal to 10:else:Less than 10}
{if:{contentsplit:1}==true&&{hour}>=12:Good afternoon true:else:Not afternoon yet}
{mention:true}@everyone Maintenance starting
```

## 返信を埋め込む

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

コンテンツでは他の `{}` 変数も使用できます。

たとえば:

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

## チャンネル制限

次の 2 つの場所からトリガー チャンネルを制限できます。

- `/自動返信 除外`
- ルール自体の `channel_mode`

`channel_mode`:

- `all`: すべてのチャンネルでトリガー
- `whitelist`: 指定されたチャンネルでのみトリガーします
- `blacklist`: 指定されたチャンネルではトリガーされません

## 組み込みテンプレート パック

- `daily_greetings`: おはよう / こんにちは / おやすみ / こんにちは
- `mini_commands`: `!say` / `!time` / `!date` / `!roll`
- `chat_fun`: チェックイン、ランダムな選択、反応、小さなインタラクション

## 一般的な例

### 基本的な挨拶

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

### 朝の挨拶のロジック

```text
Mode: starts_with
Trigger: good morning
Reply: {if:{hour}>=5:{if:{hour}<=11:Good morning {user}:else:It's already {time24} and you're still saying good morning}}
```

### 条件分岐

```text
Mode: starts_with
Trigger: check
Reply: {if:{contentsplit:1}==true:You entered true:else:You did not enter true}
```

### 条件付き省略表現

```text
{if:{contentsplit:1}==true:Yes:No}
```

### チェックインを埋め込む

```text
Mode: equals
Trigger: check-in
Reply: {embedtitle:Check-in Successful}{embeddescription:{user} completed check-in at {date} {time24}}{embedcolor:57F287}{embedfield:Server:{guild}}
```

### 遅延メッセージ/編集

```text
Mode: equals
Trigger: countdown
Reply: 3...{edit:1}2...{edit:2}1...{newmsg:3}Start
```

### ユーザー変数の記憶

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

### 数学 + 変数

```text
Mode: starts_with
Trigger: calc
Reply: Answer: {math:({contentsplit:1}+5)}
```

## テストとデバッグ

- 最初に `/自動返信 テスト` を使用して、テンプレートの置換結果を確認します。
- `starts_with` と `{contentsplit:1-}` の組み合わせは、単純なコマンドの構築に最適です
- まったく応答がない場合は、以下を確認してください。
  - テンプレートに構文エラーがあるかどうか
  - チャンネル制限によりブロックされているかどうか
  - `3 times per second` レート制限に達しているかどうか
