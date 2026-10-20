import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from playwright.async_api import Error, async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_browser import BrowserSessionManager, summarize_browser_action
from ai_browser_playwright import normalize_playwright_request, resolve_locator


class BrowserPlaywrightValidationTests(unittest.TestCase):
    def test_locator_chain_resolves_stable_roles_and_observed_selectors(self):
        editor = Mock()
        dialog = Mock()
        page = SimpleNamespace(get_by_role=Mock(return_value=dialog))
        dialog.locator.return_value = editor
        editor.nth.return_value = editor
        spec = normalize_playwright_request({
            'operation': 'fill', 'target': [
                {'kind': 'role', 'role': 'dialog'},
                {'kind': 'css', 'selector': '[contenteditable="true"][role="textbox"]'},
                {'kind': 'nth', 'index': 0},
            ], 'text': 'Draft',
        })
        self.assertIs(resolve_locator(page, spec['target']), editor)
        page.get_by_role.assert_called_once_with('dialog', exact=False)
        dialog.locator.assert_called_once_with('[contenteditable="true"][role="textbox"]')

    def test_bad_coordinates_and_unknown_operations_are_rejected(self):
        for payload in (
            {'operation': 'mouse_click', 'x': float('nan'), 'y': 2},
            {'operation': 'mouse_click', 'x': True, 'y': 2},
            {'operation': 'cookies'},
            {'operation': 'fill', 'target': [{'kind': '__class__'}], 'text': 'x'},
            {'operation': 'get_attribute', 'target': [{'kind': 'css', 'selector': 'input'}], 'name': 'value'},
        ):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                normalize_playwright_request(payload)

    def test_approval_description_shows_locator_coordinates_and_text(self):
        spec = normalize_playwright_request({'operation': 'fill', 'target': [{'kind': 'role', 'role': 'textbox'}], 'text': 'Draft'})
        self.assertIn('textbox', summarize_browser_action('act', spec))
        self.assertIn('Draft', summarize_browser_action('act', spec))
        mouse = normalize_playwright_request({'operation': 'mouse_click', 'x': 100, 'y': 200})
        self.assertIn('x=100, y=200', summarize_browser_action('act', mouse))


class BrowserPlaywrightExecutionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.manager = BrowserSessionManager()
        self.manager.snapshot_payload = AsyncMock(return_value={'snapshot': 'fresh UI'})
        self.manager.ensure_field_allowed = AsyncMock()
        self.manager.ensure_click_does_not_open_popup = AsyncMock()
        self.manager._failure_result = AsyncMock(return_value={'error': 'fixture failure', 'snapshot': 'fresh failure UI'})
        self.editor = SimpleNamespace(fill=AsyncMock(), click=AsyncMock(), inner_text=AsyncMock(return_value='Draft'))
        async def evaluate(expression, argument=None):
            if 'innerWidth' in expression:
                return {'width': 800, 'height': 600}
            return {'type': 'text', 'editable': True}
        self.page = SimpleNamespace(
            url='https://example.com', get_by_role=Mock(return_value=self.editor),
            evaluate=AsyncMock(side_effect=evaluate),
            mouse=SimpleNamespace(click=AsyncMock(), dblclick=AsyncMock(), move=AsyncMock(), wheel=AsyncMock()),
            keyboard=SimpleNamespace(insert_text=AsyncMock(), press=AsyncMock(), type=AsyncMock()),
        )

    async def test_fill_uses_real_locator_fill_and_returns_fresh_state(self):
        spec = normalize_playwright_request({'operation': 'fill', 'target': [{'kind': 'role', 'role': 'textbox'}], 'text': 'Draft'})
        result = await self.manager.execute_action(self.page, spec, {})
        self.editor.fill.assert_awaited_once_with('Draft', timeout=5000)
        self.assertTrue(result['ok'])
        self.assertEqual(result['snapshot'], 'fresh UI')

    async def test_coordinate_and_keyboard_input_use_real_playwright_devices(self):
        click = normalize_playwright_request({'operation': 'mouse_click', 'x': 300, 'y': 200})
        result = await self.manager.execute_playwright_action(self.page, click, {})
        self.assertTrue(result['ok'])
        self.page.mouse.click.assert_awaited_once_with(300, 200, button='left')
        insert = normalize_playwright_request({'operation': 'keyboard_insert_text', 'text': 'Draft'})
        result = await self.manager.execute_playwright_action(self.page, insert, {})
        self.assertTrue(result['ok'])
        self.page.keyboard.insert_text.assert_awaited_once_with('Draft')

    async def test_coordinates_outside_viewport_do_not_click(self):
        spec = normalize_playwright_request({'operation': 'mouse_click', 'x': 900, 'y': 200})
        result = await self.manager.execute_playwright_action(self.page, spec, {})
        self.page.mouse.click.assert_not_awaited()
        self.assertFalse(result['action_may_have_applied'])

    async def test_coordinate_and_focus_guards_reject_sensitive_fields(self):
        async def evaluate(expression, argument=None):
            return {'width': 800, 'height': 600} if 'innerWidth' in expression else {'type': 'password', 'editable': True}
        self.page.evaluate = AsyncMock(side_effect=evaluate)
        for spec in (
            normalize_playwright_request({'operation': 'mouse_click', 'x': 100, 'y': 100}),
            normalize_playwright_request({'operation': 'keyboard_insert_text', 'text': 'Draft'}),
        ):
            result = await self.manager.execute_playwright_action(self.page, spec, {})
            self.assertFalse(result['action_may_have_applied'])
        self.page.mouse.click.assert_not_awaited()
        self.page.keyboard.insert_text.assert_not_awaited()

    async def test_failed_action_preserves_uncertainty_and_snapshot(self):
        self.editor.click.side_effect = TimeoutError('fixture timeout')
        spec = normalize_playwright_request({'operation': 'click', 'target': [{'kind': 'role', 'role': 'button'}]})
        result = await self.manager.execute_playwright_action(self.page, spec, {})
        self.assertTrue(result['action_may_have_applied'])
        self.assertEqual(result['phase'], 'action')
        self.assertEqual(result['snapshot'], 'fresh failure UI')


class BrowserPlaywrightKeyboardIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        runtime = await async_playwright().start()
        self.addAsyncCleanup(runtime.stop)
        executable = Path(runtime.chromium.executable_path)
        try:
            browser = await runtime.chromium.launch(
                executable_path=str(executable) if executable.is_file() else None, headless=True,
            )
        except Error as error:
            if "Executable doesn't exist" in str(error):
                self.skipTest('Install Playwright Chromium for keyboard integration tests')
            raise
        self.addAsyncCleanup(browser.close)
        self.page = await browser.new_page()
        await self.page.set_content('''
            <button id="post" onclick="window.published = true">Post</button>
            <div role="textbox" aria-label="Draft" contenteditable="true" id="editor"><p>Old draft</p></div>
            <input id="other" value="Leave this alone">
            <script>
                window.published = false;
                window.model = 'Old draft';
                window.keys = [];
                window.execCalls = 0;
                document.execCommand = () => { window.execCalls++; return false; };
                const editor = document.getElementById('editor');
                document.addEventListener('keydown', event => {
                    window.keys.push(event.key);
                    if (window.blockDeletion && event.key === 'Backspace') event.preventDefault();
                    if (event.key === 'Enter' && !event.shiftKey) {
                        window.published = true;
                        event.preventDefault();
                    }
                });
                editor.addEventListener('beforeinput', event => {
                    if (window.blockInsertion && event.inputType === 'insertText') event.preventDefault();
                });
                editor.addEventListener('input', event => {
                    if (event.isTrusted) window.model = editor.innerText;
                });
                new MutationObserver(() => {
                    if (editor.innerText !== window.model) editor.innerText = window.model;
                }).observe(editor, {subtree: true, childList: true, characterData: true});
            </script>
        ''')
        self.manager = BrowserSessionManager()
        self.manager.snapshot_payload = AsyncMock(return_value={'snapshot': 'current editor'})

    async def replace(self, text, *, target=None, timeout=1500):
        spec = normalize_playwright_request({
            'operation': 'replace_text', 'target': target or [{'kind': 'role', 'role': 'textbox', 'name': 'Draft'}],
            'text': text, 'options': {'timeout': timeout},
        })
        return await self.manager.execute_playwright_action(self.page, spec, {})

    async def test_keyboard_replaces_controlled_editor_when_script_mutations_do_not_work(self):
        await self.page.evaluate('''() => {
            document.execCommand('insertText', false, 'Wrong draft');
            const editor = document.getElementById('editor');
            editor.textContent = 'Wrong draft';
            editor.dispatchEvent(new Event('input', {bubbles: true}));
        }''')
        await self.page.wait_for_function("document.getElementById('editor').innerText === 'Old draft'")
        text = 'Fresh draft\n\u6e2c\u8a66\u91cd\u6253'
        result = await self.replace(text)
        self.assertTrue(result.get('ok'), result)
        self.assertTrue(result['text_verified'])
        self.assertEqual(await self.page.get_by_role('textbox', name='Draft').inner_text(), text)
        self.assertEqual(await self.page.evaluate('window.model'), text)
        self.assertIn('Backspace', await self.page.evaluate('window.keys'))
        self.assertEqual(await self.page.evaluate('window.execCalls'), 1)
        self.assertFalse(await self.page.evaluate('window.published'))
        self.assertEqual(await self.page.locator('#other').input_value(), 'Leave this alone')

    async def test_clear_verifies_empty_and_supports_plain_inputs(self):
        result = await self.replace('')
        self.assertTrue(result.get('text_verified'), result)
        self.assertEqual((await self.page.get_by_role('textbox', name='Draft').inner_text()).strip(), '')
        result = await self.replace('Updated input', target=[{'kind': 'css', 'selector': '#other'}])
        self.assertTrue(result.get('text_verified'), result)
        self.assertEqual(await self.page.locator('#other').input_value(), 'Updated input')
        self.assertFalse(await self.page.evaluate('window.published'))

    async def test_failed_deletion_does_not_append_replacement(self):
        await self.page.evaluate('window.blockDeletion = true')
        result = await self.replace('Never append this', timeout=150)
        self.assertIn('error', result)
        self.assertNotIn('text_verified', result)
        self.assertTrue(result['action_may_have_applied'])
        self.assertEqual(await self.page.get_by_role('textbox', name='Draft').inner_text(), 'Old draft')
        self.assertEqual(result['snapshot'], 'current editor')
        self.assertFalse(await self.page.evaluate('window.published'))

    async def test_rejected_input_is_reported_instead_of_claiming_success(self):
        await self.page.evaluate('window.blockInsertion = true')
        result = await self.replace('Rejected', timeout=150)
        self.assertIn('error', result)
        self.assertNotIn('text_verified', result)
        self.assertEqual((await self.page.get_by_role('textbox', name='Draft').inner_text()).strip(), '')
        self.assertFalse(await self.page.evaluate('window.published'))

    async def test_noneditable_and_sensitive_targets_are_not_typed_into(self):
        result = await self.replace('Do not publish', target=[{'kind': 'role', 'role': 'button', 'name': 'Post'}])
        self.assertIn('error', result)
        self.assertFalse(await self.page.evaluate('window.published'))
        await self.page.locator('#other').evaluate("element => element.type = 'password'")
        result = await self.replace('Blocked', target=[{'kind': 'css', 'selector': '#other'}])
        self.assertIn('error', result)
        self.assertEqual(await self.page.locator('#other').input_value(), 'Leave this alone')

    async def test_multiline_text_does_not_erase_a_single_line_input(self):
        result = await self.replace('First\nSecond', target=[{'kind': 'css', 'selector': '#other'}])
        self.assertIn('error', result)
        self.assertEqual(await self.page.locator('#other').input_value(), 'Leave this alone')
        self.assertFalse(await self.page.evaluate('window.published'))

    async def test_ambiguous_locator_requires_a_more_specific_target(self):
        result = await self.replace('Do not guess', target=[{'kind': 'role', 'role': 'textbox'}])
        self.assertIn('error', result)
        self.assertFalse(result['action_may_have_applied'])
        self.assertIn('narrow', result['hint'])
        self.assertEqual(await self.page.locator('#editor').inner_text(), 'Old draft')
        inspected = await self.manager.inspect_playwright(self.page, normalize_playwright_request({
            'operation': 'inner_text', 'target': [{'kind': 'role', 'role': 'textbox'}],
        }), {})
        self.assertIn('narrow', inspected['hint'])
        self.assertEqual(inspected['snapshot'], 'current editor')
