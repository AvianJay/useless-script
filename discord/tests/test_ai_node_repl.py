import asyncio
import json
import os
import shutil
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_node_repl import NodeKernel, NodeSandboxError


class NodeSandboxCommandTests(unittest.TestCase):
    def test_container_has_no_network_secrets_workspace_or_privileges(self):
        kernel = NodeKernel()
        kernel.docker = 'docker'
        args = kernel.command()
        for expected in ('--pull=never', '--network=none', '--read-only', '--cap-drop=ALL',
                         '--security-opt=no-new-privileges', '--user=65534:65534', '--memory=192m'):
            self.assertIn(expected, args)
        self.assertEqual(args.count('--mount'), 1)
        self.assertIn('ai_node_worker.cjs', args[args.index('--mount') + 1])
        self.assertNotIn('--env', args)
        self.assertNotIn('--privileged', args)

    def test_missing_docker_cannot_fall_back_to_host_node(self):
        kernel = NodeKernel()
        kernel.docker = None
        with self.assertRaises(NodeSandboxError):
            kernel.command()


@unittest.skipUnless(os.environ.get('AI_TEST_NODE_DOCKER') or shutil.which('node'), 'Node is needed for the protocol fixtures')
class NodeWorkerProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.kernel = NodeKernel()
        self.addAsyncCleanup(self.kernel.close)
        if not os.environ.get('AI_TEST_NODE_DOCKER'):
            self.kernel.docker = None
            worker = Path(__file__).resolve().parents[1] / 'ai_node_worker.cjs'
            # 測試可在相容的本機 Node 跑固定案例，或指定使用正式的 Node 24 容器。
            command = patch.object(self.kernel, 'command', return_value=[shutil.which('node'), str(worker)])
            command.start()
            self.addCleanup(command.stop)
        self.broker = AsyncMock(return_value={'ok': True, 'data': {'count': 3}})

    async def test_top_level_await_and_variables_persist(self):
        first = await self.kernel.evaluate('var count = await Promise.resolve(40); write(count)', self.broker)
        second = await self.kernel.evaluate('count += 2; write(count)', self.broker)
        self.assertEqual(first['output'], ['40'])
        self.assertEqual(second['output'], ['42'])
        self.assertEqual(first['runtime']['engine'], 'node')
        self.assertRegex(first['runtime']['node_version'], r'^\d+\.\d+\.\d+')
        self.assertEqual(first['runtime']['kernel_id'], second['runtime']['kernel_id'])
        self.assertEqual(second['runtime']['evaluation'], 2)

    async def test_playwright_facade_serializes_locator_chain_and_keyboard(self):
        async def broker(name, arguments):
            return {'ok': True, 'data': {'value': 'Draft'} if arguments['operation']=='inner_text' else {'ok': True}}
        self.broker.side_effect = broker
        result = await self.kernel.evaluate('var editor = browser.page.getByRole("dialog").getByRole("textbox").first(); await editor.fill("Draft"); write(await editor.innerText()); await browser.page.mouse.click(120,80); await browser.page.keyboard.insertText("More");', self.broker)
        self.assertTrue(result['ok'])
        self.assertEqual(result['output'], ['Draft'])
        calls = self.broker.await_args_list
        self.assertEqual([call.args[1]['operation'] for call in calls], ['fill', 'inner_text', 'mouse_click', 'keyboard_insert_text'])
        self.assertEqual(calls[0].args[0], 'browser_playwright')
        self.assertEqual(calls[0].args[1]['target'], [
            {'kind':'role','role':'dialog'}, {'kind':'role','role':'textbox'}, {'kind':'nth','index':0},
        ])
        self.assertEqual(result['runtime']['bridge_calls'], 4)

    async def test_browser_tabs_and_preview_bridge(self):
        result = await self.kernel.evaluate(
            'await browser.tabs.list(); await browser.tabs.new(); await browser.tabs.select("t2"); '
            'await browser.tabs.keep("t2", true, "draft_pending_review"); await browser.livePreview(true); await browser.tabs.close("t2");', self.broker)
        self.assertTrue(result['ok'])
        calls = self.broker.await_args_list
        self.assertEqual([c.args[0] for c in calls], ['browser_read'] * 6)
        self.assertEqual([c.args[1]['action'] for c in calls],
                         ['list_tabs', 'new_tab', 'select_tab', 'keep_tab', 'set_live_preview', 'close_tab'])
        self.assertEqual(calls[2].args[1]['tab_id'], 't2')
        self.assertEqual(calls[3].args[1]['resume_status'], 'draft_pending_review')
        self.assertIs(calls[4].args[1]['enabled'], True)

    async def test_bridge_failure_retains_recovery_details(self):
        self.broker.return_value = {'ok': False, 'error': 'Click timed out', 'data': {'snapshot': 'Fresh UI', 'action_may_have_applied': True}}
        result = await self.kernel.evaluate('await browser.page.getByRole("button").click()', self.broker)
        self.assertFalse(result['ok'])
        self.assertEqual(result['details']['snapshot'], 'Fresh UI')
        self.assertTrue(result['details']['action_may_have_applied'])
        self.assertEqual(result['runtime']['bridge_calls'], 1)

    async def test_rich_editor_replace_and_clear_use_verified_keyboard_operation(self):
        self.broker.return_value = {'ok': True, 'data': {'text_verified': True}}
        result = await self.kernel.evaluate(
            'var editor = browser.page.getByRole("textbox"); write(await editor.replaceText("Fresh draft")); await editor.clear();',
            self.broker,
        )
        self.assertTrue(result['ok'])
        calls = self.broker.await_args_list
        self.assertEqual([call.args[0] for call in calls], ['browser_playwright', 'browser_playwright'])
        self.assertEqual([call.args[1]['operation'] for call in calls], ['replace_text', 'replace_text'])
        self.assertEqual([call.args[1]['text'] for call in calls], ['Fresh draft', ''])

    async def test_large_unicode_output_and_failure_details_stay_within_protocol_limit(self):
        self.broker.return_value = {'ok':False,'error':'Timeout','data':{'snapshot':'中'*12000,'action_may_have_applied':True}}
        result = await self.kernel.evaluate('write("\\u4e2d".repeat(12000)); await browser.page.getByRole("button").click()', self.broker)
        self.assertFalse(result['ok'])
        self.assertTrue(result['truncated'])
        self.assertTrue(result['details']['action_may_have_applied'])
        self.assertEqual(len(result['details']['snapshot']), 4000)

    async def test_csv_handles_quoted_newlines_and_safe_spreadsheet_output(self):
        code = r'''
        var rows = data.parseCsv('name,value\r\n"a,b","line1\nline2"\r\n"quoted ""name""",2\r\n');
        write(JSON.stringify(rows));
        write(data.toCsv([['=SUM(A1:A2)', -2, 'normal']]));
        '''
        result = await self.kernel.evaluate(code, self.broker)
        self.assertTrue(result['ok'])
        self.assertEqual(json.loads(result['output'][0]), [
            ['name', 'value'], ['a,b', 'line1\nline2'], ['quoted "name"', '2'],
        ])
        self.assertEqual(result['output'][1], '"\'=SUM(A1:A2)","-2","normal"')

    async def test_bridge_round_trip_uses_python_callback_and_reports_denial(self):
        result = await self.kernel.evaluate('write(await tools.call("read_resource", {resource_id:"fixture"}))', self.broker)
        self.broker.assert_awaited_once_with('read_resource', {'resource_id': 'fixture'})
        self.assertIn('3', result['output'][0])
        self.broker.return_value = {'ok': False, 'error': 'Access denied'}
        result = await self.kernel.evaluate('await browser.act({action:"click",ref:"e1",element:"button"})', self.broker)
        self.assertFalse(result['ok'])
        self.assertIn('Access denied', result['error'])

    async def test_timeout_kills_kernel_and_allows_clean_restart(self):
        with self.assertRaisesRegex(NodeSandboxError, 'timed out'):
            await self.kernel.evaluate('while (true) {}', self.broker, timeout=1)
        self.assertIsNone(self.kernel.process)
        result = await self.kernel.evaluate('typeof count', self.broker)
        self.assertIn('undefined', result['value'])

    async def test_cancellation_terminates_kernel(self):
        await self.kernel.start()
        task = asyncio.create_task(self.kernel.evaluate('await new Promise(() => {})', self.broker))
        await asyncio.sleep(0.05)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIsNone(self.kernel.process)


if __name__ == '__main__':
    unittest.main()
