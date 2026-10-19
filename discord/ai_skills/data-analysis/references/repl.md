# Analysis runtime

`node_repl({code, timeout_seconds?, reset?})` keeps variables across calls within
this request and supports top-level `await`. Use `var` for names you may reassign.
Resources and the runtime are discarded after completion or cancellation.

- `write(value)` / `console.log(value)` returns compact output.
- `data.parseCsv(text, {delimiter: ","})` parses quoted commas, escaped quotes,
  CRLF and embedded newlines into arrays of strings. Tab and semicolon delimiters
  are also supported. Limits: 500000 input characters, 10000 rows, 200 columns.
- `data.toCsv(rows)` correctly quotes fields and neutralizes spreadsheet formulas
  in text values by default. Numeric values remain numeric. Convert validated
  numeric columns explicitly; do not treat missing strings as zero.
- `await resources.list()` lists request-local resources.
- `await resources.read(id, {offset: 0, limit: 16000})` returns a text slice and
  `next_offset`. Read all relevant slices before analyzing the complete dataset.
- `await resources.write("result.csv", csvText)` returns a new resource descriptor.
- `await resources.send(id, "Brief result summary")` queues the textual attachment.
- `await tools.call(name, arguments)` calls a currently exposed tool and returns
  its data; errors throw. The REPL cannot call itself or load other skills.

Example:

```javascript
var input = await resources.read("ACTUAL_RESOURCE_ID", {limit: 16000});
var records = JSON.parse(input.text); // Only after checking the JSON fits this slice.
write({rows: records.length});
```

Use a real resource ID returned by a tool. Do not substitute a filename or a host
path. CSV can contain quoted commas and newlines: use `data.parseCsv` or the
Python csv module through execute_code. No third-party Node
packages are preinstalled. A timeout resets the kernel, so re-read inputs before
continuing and do not assume previous variables survived.
