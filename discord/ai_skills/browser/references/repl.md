# Request-local Node REPL

`node_repl({code, timeout_seconds?})` runs JavaScript in an isolated container.
Top-level `await`, `let`, `const`, `var` and collected data persist between calls
within this request. Reuse variables or choose new names to avoid redeclaration
errors. `reset: true` discards the kernel before evaluating optional new code.
The kernel and resources are discarded when the response completes or is cancelled.

Available globals:

- `browser.read({action, url?, ref?, text?, milliseconds?, delta_y?})`
- `browser.act({action, ref, element, ...actionArguments})`
- `browser.evaluate({expression, ...evaluationArguments})`
- `browser.view({prompt?})`
- `tools.call(name, arguments)` for currently exposed tools except the REPL itself.
- `resources.read(resourceId, {offset?, limit?})`, `resources.list()`
- `resources.write(filename, content)` and `resources.send(resourceId, summary?)`
  when data-analysis is loaded.
- `write(value)` / `console.log(value)` to return compact output.

Bridge calls return the underlying tool's data and throw on tool errors. A long
result may instead return `resource_id` and a preview. Read slices of that resource.
The kernel has no direct network access and does not contain bot credentials.

Example:

```javascript
var first = await browser.read({action: "navigate", url: "https://example.com"});
write(first);
```

Inspect this snapshot before selecting a ref. Do not put guessed interactions into
a loop. When the next steps are determined by observed state, collect data in a
variable and print only the relevant summary. Browser refs belong to the latest
snapshot, not to a saved variable from an earlier page state.

Every bridge call is subject to the same request permissions, call budget and
browser approval as direct tools. Timeout resets the kernel; completed external
actions are not undone. Never blindly repeat an action after a timeout.
