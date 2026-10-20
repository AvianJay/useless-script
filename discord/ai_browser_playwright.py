"""Validated Playwright operations used by the browser REPL bridge.

Only browser objects are exposed. The caller still owns URL checks, account
permissions and Discord's browser-session approval.
"""

import json
import math
import re

from playwright.async_api import expect


READ_OPERATIONS = {
    "count", "is_visible", "is_enabled", "inner_text", "text_content",
    "input_value", "get_attribute", "bounding_box", "wait_for", "viewport", "title", "url",
}
LOCATOR_ACTIONS = {"click", "dblclick", "hover", "focus", "fill", "replace_text", "type", "press", "check", "uncheck", "select_option"}
MOUSE_ACTIONS = {"mouse_click", "mouse_dblclick", "mouse_move", "mouse_wheel"}
KEYBOARD_ACTIONS = {"keyboard_press", "keyboard_type", "keyboard_insert_text"}
ATTRIBUTES = {"role", "type", "name", "id", "class", "aria-label", "aria-disabled", "placeholder", "contenteditable", "disabled", "href", "data-testid"}

BROWSER_PLAYWRIGHT_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "browser_playwright",
        "description": (
            "Use stable role/CSS locators and real keyboard/mouse operations on the managed browser page. "
            "Available directly when the Node sandbox cannot start; otherwise use browser.page in node_repl. "
            "For a rich editor that ignores fill or execCommand, use replace_text: it focuses the editable target, "
            "selects all, presses Backspace, verifies it is empty, types using keyboard events and verifies the result. "
            "It never submits or publishes. All mutations use the existing browser session approval and permissions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": sorted(READ_OPERATIONS | LOCATOR_ACTIONS | MOUSE_ACTIONS | KEYBOARD_ACTIONS)},
                "target": {
                    "type": "array", "minItems": 1, "maxItems": 8,
                    "description": "Observed locator chain. Example: [{kind: role, role: dialog}, {kind: role, role: textbox}]. Required for locator operations.",
                    "items": {
                        "type": "object", "required": ["kind"],
                        "properties": {
                            "kind": {"type": "string", "enum": ["css", "role", "text", "label", "placeholder", "filter", "nth"]},
                            **{key: {"type": "string"} for key in ("selector", "role", "name", "text", "hasText")},
                            "exact": {"type": "boolean"}, "index": {"type": "integer", "minimum": -1},
                        },
                    },
                },
                "text": {"type": "string", "maxLength": 8000, "description": "fill/type/replace_text or keyboard input; replace_text replaces the entire target's text."},
                "key": {"type": "string", "description": "press/keyboard_press: Playwright key such as ControlOrMeta+A or Backspace."},
                "name": {"type": "string", "enum": sorted(ATTRIBUTES), "description": "get_attribute: attribute name."},
                "values": {"type": "array", "items": {"type": "string"}, "maxItems": 20},
                **{key: {"type": "number"} for key in ("x", "y", "delta_x", "delta_y")},
                "options": {
                    "type": "object", "properties": {
                        "timeout": {"type": "integer", "minimum": 1, "maximum": 30000},
                        "delay": {"type": "number", "minimum": 0, "maximum": 100},
                        "button": {"type": "string", "enum": ["left", "middle", "right"]},
                        "force": {"type": "boolean"},
                        "state": {"type": "string", "enum": ["attached", "detached", "visible", "hidden"]},
                    },
                },
                "reason": {"type": "string", "maxLength": 600},
            },
            "required": ["operation"],
        },
    },
}


def normalize_target(value) -> list[dict]:
    if not isinstance(value, list) or not 1 <= len(value) <= 8:
        raise ValueError("A locator must have 1 to 8 selector steps")
    target = []
    for step in value:
        if not isinstance(step, dict):
            raise ValueError("Invalid locator step")
        kind = step.get("kind")
        if kind == "nth":
            index = step.get("index")
            if not isinstance(index, int) or isinstance(index, bool) or not -1 <= index <= 10000 or not target:
                raise ValueError("Invalid locator index")
            target.append({"kind": kind, "index": index})
        elif kind in {"css", "role", "text", "label", "placeholder", "filter"}:
            field = {"css": "selector", "role": "role", "filter": "hasText"}.get(kind, "text")
            text = step.get(field)
            if not isinstance(text, str) or not text or len(text) > 1000:
                raise ValueError("Locator selectors and names must be nonempty text up to 1000 characters")
            clean = {"kind": kind, field: text, "exact": step.get("exact", False) is True}
            if kind == "role" and "name" in step:
                if not isinstance(step["name"], str) or len(step["name"]) > 1000:
                    raise ValueError("Invalid role name")
                clean["name"] = step["name"]
            if kind == "filter" and not target:
                raise ValueError("filter requires an existing locator")
            target.append(clean)
        else:
            raise ValueError("Unsupported locator step")
    return target


def normalize_playwright_request(args: dict) -> dict:
    operation = args.get("operation")
    if operation not in READ_OPERATIONS | LOCATOR_ACTIONS | MOUSE_ACTIONS | KEYBOARD_ACTIONS:
        raise ValueError("Unsupported Playwright operation")
    options = args.get("options") or {}
    if not isinstance(options, dict):
        raise ValueError("options must be an object")
    timeout = options.get("timeout", 5000)
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not math.isfinite(timeout):
        raise ValueError("Invalid timeout")
    spec = {"action": "playwright", "operation": operation, "timeout_ms": max(1, min(30000, int(timeout)))}
    if operation in LOCATOR_ACTIONS or operation in READ_OPERATIONS - {"viewport", "title", "url"}:
        spec["target"] = normalize_target(args.get("target"))
    if operation in {"fill", "replace_text", "type", "keyboard_type", "keyboard_insert_text"}:
        text = args.get("text")
        if not isinstance(text, str) or len(text) > 8000:
            raise ValueError("text must be a string up to 8000 characters")
        spec["text"] = text
    if operation in {"press", "keyboard_press"}:
        key = args.get("key")
        if not isinstance(key, str) or not key or len(key) > 100:
            raise ValueError("Invalid key")
        spec["key"] = key
    if operation in MOUSE_ACTIONS:
        fields = ("delta_x", "delta_y") if operation == "mouse_wheel" else ("x", "y")
        for field in fields:
            value = args.get(field, 0 if operation == "mouse_wheel" else None)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or abs(value) > 20000:
                raise ValueError("Mouse coordinates/deltas must be finite CSS pixel values")
            spec[field] = value
    if operation in {"replace_text", "type", "keyboard_type"}:
        delay = options.get("delay", 0)
        if not isinstance(delay, (int, float)) or not math.isfinite(delay) or not 0 <= delay <= 100:
            raise ValueError("Typing delay must be between 0 and 100 ms")
        spec["delay"] = delay
    if operation in {"click", "dblclick", "mouse_click", "mouse_dblclick"}:
        button = options.get("button", "left")
        if button not in {"left", "middle", "right"}:
            raise ValueError("Invalid mouse button")
        spec["button"] = button
        spec["force"] = options.get("force", False) is True
    if operation == "get_attribute":
        if args.get("name") not in ATTRIBUTES:
            raise ValueError("Unsupported attribute; use inputValue for an editable field's value")
        spec["name"] = args["name"]
    if operation == "wait_for":
        state = options.get("state", "visible")
        if state not in {"attached", "detached", "visible", "hidden"}:
            raise ValueError("Invalid wait state")
        spec["state"] = state
    if operation == "select_option":
        values = args.get("values")
        if not isinstance(values, list) or not 1 <= len(values) <= 20 or any(not isinstance(item, str) or len(item) > 500 for item in values):
            raise ValueError("values must contain 1 to 20 short strings")
        spec["values"] = values
    spec["element"] = operation.replace("_", " ") + " " + json.dumps(spec.get("target", {}), ensure_ascii=False)[:500]
    return spec


def resolve_locator(page, target: list[dict]):
    locator = page
    for step in target:
        kind = step["kind"]
        if kind == "css":
            locator = locator.locator(step["selector"])
        elif kind == "role":
            options = {"exact": step["exact"]}
            if "name" in step:
                options["name"] = step["name"]
            locator = locator.get_by_role(step["role"], **options)
        elif kind in {"text", "label", "placeholder"}:
            method = {"text": "get_by_text", "label": "get_by_label", "placeholder": "get_by_placeholder"}[kind]
            locator = getattr(locator, method)(step["text"], exact=step["exact"])
        elif kind == "nth":
            locator = locator.nth(step["index"])
        elif kind == "filter":
            locator = locator.filter(has_text=step["hasText"])
    return locator


async def viewport_size(page) -> dict:
    return await page.evaluate("() => ({width: window.innerWidth, height: window.innerHeight})")


# This script only reads the current hit target/focus. It never inserts text,
# dispatches synthetic events, exports browser storage or bypasses input guards.
_TARGET_INFO = """point => {
    const hit = point ? document.elementFromPoint(point.x, point.y) : document.activeElement;
    if (!hit) return null;
    const editable = hit.closest('input,textarea,[contenteditable="true"]') || hit;
    const link = hit.closest('a,area');
    return {
        type: editable.getAttribute('type') || '', name: editable.getAttribute('name') || '',
        id: editable.id || '', autocomplete: editable.getAttribute('autocomplete') || '',
        ariaLabel: editable.getAttribute('aria-label') || '', placeholder: editable.getAttribute('placeholder') || '',
        editable: editable.isContentEditable || ['INPUT','TEXTAREA'].includes(editable.tagName),
        popup: !!(link && ((link.getAttribute('target') || '').toLowerCase() === '_blank' || link.hasAttribute('download')))
    };
}"""


class BrowserPlaywrightMixin:
    async def _playwright_failure_result(self, page, context, operation, error) -> dict:
        result = await self._failure_result(page, context, operation, error)
        if result.get("hint"):
            result["hint"] = "Inspect the current snapshot and narrow the locator using the target's dialog, name or observed CSS. Do not retry an ambiguous or stale target unchanged."
        if "snapshot" not in result:
            result.update(await self.snapshot_payload(page, max_chars=12000))
        return result

    async def replace_editor_text(self, locator, text: str, *, timeout: int, delay: float) -> None:
        """Replace editable text through keyboard events and verify application readback."""
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        field = await locator.evaluate(
            """element => ({
                input: ['INPUT', 'TEXTAREA'].includes(element.tagName),
                multiline: element.tagName !== 'INPUT',
                editable: element.isContentEditable ||
                    element.tagName === 'TEXTAREA' ||
                    (element.tagName === 'INPUT' && ['text', 'search', 'email', 'url', 'tel', ''].includes(element.type))
            })""", timeout=timeout,
        )
        if not field.get("editable"):
            raise ValueError("replace_text requires the actual input, textarea or contenteditable textbox")
        if "\n" in text and not field["multiline"]:
            raise ValueError("The target is a single-line input; it cannot accept multiline text")

        async def verify(expected):
            if field["input"]:
                await expect(locator).to_have_value(expected, timeout=timeout)
            else:
                # Contenteditable paragraphs may contribute a trailing empty line.
                pattern = re.compile("^" + re.escape(expected) + r"\n?$")
                await expect(locator).to_have_text(pattern, use_inner_text=True, timeout=timeout)

        await locator.focus(timeout=timeout)
        focused = await locator.evaluate(
            "element => element === element.ownerDocument.activeElement || element.contains(element.ownerDocument.activeElement)",
            timeout=timeout,
        )
        if not focused:
            raise ValueError("The target did not retain keyboard focus; inspect the current editor")
        await locator.press("ControlOrMeta+A", timeout=timeout)
        await locator.press("Backspace", timeout=timeout)
        await verify("")
        for index, line in enumerate(text.split("\n")):
            if index:
                # A plain Enter can submit a composer; Shift+Enter inserts a line
                # break rather than a new Lexical paragraph or submit command.
                await locator.press("Shift+Enter", timeout=timeout)
            if line:
                await locator.press_sequentially(line, delay=delay, timeout=timeout)
        await verify(text)

    async def inspect_playwright(self, page, spec: dict, context: dict) -> dict:
        operation, timeout = spec["operation"], spec["timeout_ms"]
        try:
            if operation == "viewport":
                value = await viewport_size(page)
            elif operation == "title":
                value = await page.title()
            elif operation == "url":
                value = page.url
            else:
                locator = resolve_locator(page, spec["target"])
                if operation == "input_value":
                    await self.ensure_field_allowed(locator, timeout)
                if operation == "count":
                    value = await locator.count()
                elif operation in {"is_visible", "is_enabled", "inner_text", "text_content", "input_value", "bounding_box"}:
                    value = await getattr(locator, operation)(timeout=timeout)
                elif operation == "get_attribute":
                    value = await locator.get_attribute(spec["name"], timeout=timeout)
                elif operation == "wait_for":
                    await locator.wait_for(state=spec["state"], timeout=timeout)
                    value = True
            return {"operation": operation, "value": value, "coordinate_space": "viewport CSS pixels"}
        except Exception as error:
            return await self._playwright_failure_result(page, context, operation, error)

    async def _guard_point_or_focus(self, page, point=None, *, editable=False):
        info = await page.evaluate(_TARGET_INFO, point)
        if not info:
            raise ValueError("No element at the mouse position/current focus")
        haystack = " ".join(str(info.get(key) or "") for key in ("type", "name", "id", "autocomplete", "ariaLabel", "placeholder"))
        if re.search(r"password|passwd|passcode|credit.?card|card.?number|payment|billing|cvv|cvc|security.?code|one.?time.?password|otp", haystack, re.I):
            raise ValueError("password and payment fields are not supported")
        if info.get("popup") and point is not None:
            raise ValueError("popups and downloads are not supported")
        if editable and not info.get("editable"):
            raise ValueError("Focus an editable field before inserting text")

    async def execute_playwright_action(self, page, spec: dict, context: dict) -> dict:
        operation, timeout = spec["operation"], spec["timeout_ms"]
        phase = "target"
        try:
            if operation in LOCATOR_ACTIONS:
                locator = resolve_locator(page, spec["target"])
                if operation in {"fill", "replace_text", "type", "press", "focus"}:
                    await self.ensure_field_allowed(locator, timeout)
                if operation in {"click", "dblclick"}:
                    await self.ensure_field_allowed(locator, timeout)
                    await self.ensure_click_does_not_open_popup(locator, timeout)
                phase = "action"
                if operation in {"click", "dblclick"}:
                    await getattr(locator, operation)(timeout=timeout, button=spec["button"], force=spec["force"], no_wait_after=True)
                elif operation == "fill":
                    await locator.fill(spec["text"], timeout=timeout)
                elif operation == "replace_text":
                    await self.replace_editor_text(locator, spec["text"], timeout=timeout, delay=spec["delay"])
                elif operation == "type":
                    await locator.press_sequentially(spec["text"], delay=spec["delay"], timeout=timeout)
                elif operation == "press":
                    await locator.press(spec["key"], timeout=timeout)
                elif operation == "select_option":
                    await locator.select_option(spec["values"], timeout=timeout)
                else:
                    await getattr(locator, operation)(timeout=timeout)
            elif operation in MOUSE_ACTIONS:
                if operation == "mouse_wheel":
                    phase = "action"
                    await page.mouse.wheel(spec["delta_x"], spec["delta_y"])
                else:
                    viewport = await viewport_size(page)
                    if not 0 <= spec["x"] < viewport["width"] or not 0 <= spec["y"] < viewport["height"]:
                        raise ValueError("Mouse position is outside the current viewport")
                    await self._guard_point_or_focus(page, {"x": spec["x"], "y": spec["y"]})
                    phase = "action"
                    if operation == "mouse_move":
                        await page.mouse.move(spec["x"], spec["y"])
                    else:
                        method = page.mouse.click if operation == "mouse_click" else page.mouse.dblclick
                        await method(spec["x"], spec["y"], button=spec["button"])
            else:
                await self._guard_point_or_focus(page, editable=operation != "keyboard_press")
                phase = "action"
                if operation == "keyboard_press":
                    await page.keyboard.press(spec["key"])
                elif operation == "keyboard_type":
                    await page.keyboard.type(spec["text"], delay=spec["delay"])
                else:
                    await page.keyboard.insert_text(spec["text"])
        except Exception as error:
            result = await self._playwright_failure_result(page, context, operation, error)
            result["phase"] = phase
            result["action_may_have_applied"] = phase == "action"
            return result
        result = {"ok": True, "action": operation, "target": spec.get("target"), "coordinate_space": "viewport CSS pixels"}
        if operation == "replace_text":
            result["text_verified"] = True
        result.update(await self.snapshot_payload(page, max_chars=12000))
        return result
