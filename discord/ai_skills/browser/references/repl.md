# Request-local Node REPL

`node_repl({code, timeout_seconds?})` runs JavaScript in an isolated container.
Top-level `await`, `let`, `const`, `var` and collected data persist between calls
within this request. Reuse variables or choose new names to avoid redeclaration
errors. `reset: true` discards the kernel before evaluating optional new code.
The kernel and resources are discarded when the response completes or is cancelled.

Available globals:

- `browser.page` (also most methods directly on `browser`) controls the managed
  page through real Python Playwright. Use `goto(url)`, `snapshot()`, `title()`,
  `url()`, `viewportSize()` and `evaluate(functionOrExpression, optionalArgument)`.
- Locator constructors: `getByRole(role, {name, exact})`, `getByText`, `getByLabel`,
  `getByPlaceholder`, and `locator(css)`. These chain, along with `filter({hasText})`,
  `first()`, `last()` and `nth(index)`.
- Locator operations: `count()`, `isVisible()`, `isEnabled()`, `innerText()`,
  `textContent()`, `inputValue()`, `getAttribute(name)`, `boundingBox()`,
  `waitFor({state, timeout})`, `click()`, `dblclick()`, `hover()`, `focus()`,
  `fill(text)`, `clear()`, `pressSequentially(text, {delay})`, `press(key)`,
  `check()`, `uncheck()`, `selectOption(valueOrValues)`.
- `browser.page.mouse.click(x,y)`, `dblclick(x,y)`, `move(x,y)`, `wheel(dx,dy)`.
- `browser.page.keyboard.press(key)`, `type(text,{delay})`, `insertText(text)`.

Locator operations accept `{timeout: milliseconds}` (default 5000, maximum 30000).
Mouse coordinates and bounding boxes use viewport CSS pixels. Read the current
viewport and use observed bounds; do not reuse coordinates after a layout change.
The supported facade is intentionally not a host Playwright/CDP connection: it
does not expose cookies, browser storage, files, raw network or profile management.
Read operations return values directly; actions return fresh snapshots.

For a rich editor, locate the actual textbox instead of its opening button:

```javascript
var page = browser.page;
var editor = page.locator('[contenteditable="true"][role="textbox"]').first();
await editor.fill('The text requested by the user');
write(await editor.innerText());
```

If a locator action failed after the target was found, error details include
`action_may_have_applied` and a current snapshot. Inspect before retrying. A failed
ref should be replaced with a stable observed locator, not retried indefinitely.

Compatibility methods:

- `browser.read({action, url?, ref?, text?, milliseconds?, delta_y?})`
- `browser.act({action, ref, element, ...actionArguments})`
- `browser.evaluate({expression, ...evaluationArguments})`
- `browser.view({prompt?})`
- `tools.call(name, arguments)` for currently exposed tools except the REPL itself.
- `resources.read(resourceId, {offset?, limit?})`, `resources.list()`
- `resources.write(filename, content)` and `resources.send(resourceId, summary?)`
  when data-analysis is loaded.
- `write(value)` / `console.log(value)` to return compact output.

Bridge calls return the underlying tool's data and throw on tool errors (available
as `error.details` when caught, or the evaluation's `details` when uncaught). A long
result may instead return `resource_id` and a preview. Read slices of that resource.
The kernel has no direct network access and does not contain bot credentials.

Example:

```javascript
var first = await browser.read({action: "navigate", url: "https://example.com"});
write(first);
```

Inspect this snapshot before selecting a target. Do not put guessed interactions into
a loop. When the next steps are determined by observed state, collect data in a
variable and print only the relevant summary. Browser refs belong to the latest
snapshot, not to a saved variable from an earlier page state.

Every bridge call is subject to the same request permissions, call budget and
browser approval as direct tools. Timeout resets the kernel; completed external
actions are not undone. Never blindly repeat an action after a timeout.
