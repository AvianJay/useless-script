import asyncio
import json
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


@unittest.skipUnless(shutil.which('node'), 'Node is needed to test the trusted protocol fixture')
class NodeWorkerProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.kernel = NodeKernel()
        self.kernel.docker = None
        worker = Path(__file__).resolve().parents[1] / 'ai_node_worker.cjs'
        # Test-only override: run these fixed, trusted fixture snippets locally.
        # Production NodeKernel.command always requires the Docker sandbox.
        self.command = patch.object(self.kernel, 'command', return_value=[shutil.which('node'), str(worker)])
        self.command.start()
        self.broker = AsyncMock(return_value={'ok': True, 'data': {'count': 3}})

    async def asyncTearDown(self):
        await self.kernel.close()
        self.command.stop()

    async def test_top_level_await_and_variables_persist(self):
        first = await self.kernel.evaluate('var count = await Promise.resolve(40); write(count)', self.broker)
        second = await self.kernel.evaluate('count += 2; write(count)', self.broker)
        self.assertEqual(first['output'], ['40'])
        self.assertEqual(second['output'], ['42'])

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
