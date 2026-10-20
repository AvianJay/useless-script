import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

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
