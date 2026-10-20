import asyncio
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai import AICommands
from ai_skills import get_skill_registry, skill_session
from ai_node_repl import NodeSandboxUnavailable


def response(content='', calls=None):
    return SimpleNamespace(model='fixture-model', choices=[SimpleNamespace(message=SimpleNamespace(
        content=content, tool_calls=calls, images=None, reasoning_content=None,
    ))])


def native_call(name, arguments):
    return SimpleNamespace(id=f'call-{name}', function=SimpleNamespace(name=name, arguments=json.dumps(arguments)))


class AISkillToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = AICommands(SimpleNamespace())
        self.context = {'user': SimpleNamespace(id=42), 'channel': SimpleNamespace(id=1)}
        self.cog._get_docs_feature_prompt = lambda: ''

    async def asyncTearDown(self):
        await self.cog._close_skill_session(self.context)

    def test_catalog_maps_unloaded_tools_to_skills_without_exposing_schemas(self):
        with patch.object(self.cog, '_skill_availability', return_value=(True, None)):
            catalog = self.cog._skill_catalog_prompt()
        for skill in get_skill_registry().skills.values():
            entry = next(line for line in catalog.splitlines() if line.startswith(f'- {skill.name}:'))
            for tool_name in skill.tools:
                self.assertIn(tool_name, entry)
        self.assertIn('including when the user only asks whether you have that tool', catalog)
        offered = {tool['function']['name'] for tool in self.cog._build_request_ai_tools(self.context)}
        self.assertNotIn('get_user', offered)
        self.assertEqual(skill_session(self.context).loaded, set())

    async def test_list_skills_reports_tool_names_availability_and_loaded_state(self):
        with patch.object(self.cog, '_skill_availability', return_value=(True, None)):
            result = await self.cog._execute_ai_tool('list_skills', {}, self.context)
            skills = {skill['name']: skill for skill in result['data']['skills']}
            self.assertIn('get_user', skills['discord-investigate']['tools'])
            self.assertTrue(skills['discord-investigate']['available'])
            self.assertFalse(skills['discord-investigate']['loaded'])
            self.assertEqual(skill_session(self.context).loaded, set())

            await self.cog._execute_ai_tool('load_skill', {'name': 'discord-investigate'}, self.context)
            result = await self.cog._execute_ai_tool('list_skills', {}, self.context)
            loaded = next(skill for skill in result['data']['skills'] if skill['name'] == 'discord-investigate')
            self.assertTrue(loaded['loaded'])
            self.assertEqual(loaded['tools'], skills['discord-investigate']['tools'])

        with patch.object(self.cog, '_skill_availability', return_value=(False, 'Not configured')):
            result = await self.cog._execute_ai_tool('list_skills', {}, self.context)
        self.assertTrue(all(not skill['available'] and skill['reason'] == 'Not configured'
                            for skill in result['data']['skills']))

    def test_emulated_prompt_preserves_discovery_of_tools_outside_current_schemas(self):
        with patch.object(self.cog, '_skill_availability', return_value=(True, None)):
            catalog = self.cog._skill_catalog_prompt()
        messages = self.cog._prepare_tool_emulation_messages(
            [{'role': 'system', 'content': catalog},
             {'role': 'user', 'content': 'do u have get_user tool'}],
            self.cog._build_request_ai_tools(self.context),
        )
        prompt = messages[0]['content']
        schemas = json.loads(next(line[14:] for line in prompt.splitlines() if line.startswith('Tool schemas: ')))
        self.assertNotIn('get_user', schemas)
        self.assertIn('list_skills', schemas)
        self.assertIn('load_skill', schemas)
        self.assertIn('before claiming a tool is missing', prompt)
        self.assertEqual(messages[1]['content'], catalog)
        self.assertIn('get_user', messages[1]['content'])

    async def test_tools_are_loaded_on_demand_and_not_shared_between_requests(self):
        before = {tool['function']['name'] for tool in self.cog._build_request_ai_tools(self.context)}
        self.assertIn('load_skill', before)
        self.assertNotIn('search_google', before)
        self.assertNotIn('read_message', before)
        await self.cog._execute_ai_tool('load_skill', {'name': 'web-research'}, self.context)
        after = {tool['function']['name'] for tool in self.cog._build_request_ai_tools(self.context)}
        self.assertIn('search_google', after)
        other = {tool['function']['name'] for tool in self.cog._build_request_ai_tools({'user': SimpleNamespace(id=99)})}
        self.assertNotIn('search_google', other)

    async def test_unknown_skill_and_unloaded_reference_do_not_enable_tools(self):
        unknown = await self.cog._execute_ai_tool('load_skill', {'name': '../../config'}, self.context)
        reference = await self.cog._execute_ai_tool('read_skill_file', {'name': 'browser', 'path': 'references/repl.md'}, self.context)
        self.assertFalse(unknown['ok'])
        self.assertFalse(reference['ok'])
        self.assertEqual(skill_session(self.context).loaded, set())

    async def test_browser_defaults_to_repl_and_only_exposes_direct_fallback_after_failure(self):
        skill_session(self.context).loaded.add('browser')
        with patch('ai._get_ai_browser_cdp_endpoint', return_value='configured'):
            before = {tool['function']['name'] for tool in self.cog._build_request_ai_tools(self.context)}
            self.assertIn('node_repl', before)
            self.assertNotIn('browser_act', before)
            self.assertNotIn('browser_playwright', before)
            rejected = await self.cog._execute_ai_tool('browser_act', {'action':'click'}, self.context)
            self.assertFalse(rejected['ok'])
            self.context['_node_repl_unavailable'] = True
            after = {tool['function']['name'] for tool in self.cog._build_request_ai_tools(self.context)}
            self.assertIn('browser_act', after)
            self.assertIn('browser_playwright', after)
            schemas = {tool['function']['name']: tool['function']
                       for tool in self.cog._build_request_ai_tools(self.context)}
            self.assertIn('replace_text', schemas['browser_playwright']['parameters']['properties']['operation']['enum'])

    async def test_playwright_is_gated_until_browser_loads_and_available_to_node_bridge(self):
        with patch('ai._get_ai_browser_cdp_endpoint', return_value='configured'):
            before = {tool['function']['name'] for tool in self.cog._build_request_ai_tools(self.context)}
            self.assertNotIn('browser_playwright', before)
            skill_session(self.context).loaded.add('browser')
            with patch.object(self.cog, '_tool_browser_playwright', new=AsyncMock(return_value={'text_verified': True})) as action:
                payload = {'operation': 'replace_text', 'target': [{'kind': 'role', 'role': 'textbox'}], 'text': 'Draft'}
                result = await self.cog._node_tool_bridge('browser_playwright', payload, self.context)
            self.assertTrue(result['ok'])
            self.assertTrue(result['data']['text_verified'])
            self.assertEqual(action.await_args.args, (payload, self.context))

    async def test_threads_loads_the_browser_dependency_and_repl(self):
        with patch('ai_skill_tools.get_global_config', return_value='configured'):
            result = await self.cog._execute_ai_tool('load_skill', {'name':'threads-posting'}, self.context)
        self.assertTrue(result['ok'])
        self.assertEqual(skill_session(self.context).loaded, {'browser','threads-posting'})
        self.assertIn('node_repl', result['data']['tools'])

    async def test_playwright_mutation_uses_existing_approval_and_rejection_gate(self):
        skill_session(self.context).loaded.add('browser')
        page = SimpleNamespace(url='https://example.com')
        payload = {'operation':'fill','target':[{'kind':'role','role':'textbox'}],'text':'Draft'}
        with patch.object(self.cog, '_prepare_browser_interaction', new=AsyncMock(return_value=(page,page.url,None))), patch.object(
            self.cog.browser, 'request_approval', new=AsyncMock(return_value={'approved':False})) as approval, patch.object(
            self.cog.browser, 'execute_playwright_action', new=AsyncMock()) as execute:
            await self.cog._tool_browser_playwright(payload,self.context)
            approval.assert_awaited_once()
            execute.assert_not_awaited()
        with patch.object(self.cog, '_prepare_browser_interaction', new=AsyncMock(return_value=(None,None,{'error':'Session rejected'}))), patch.object(
            self.cog.browser, 'execute_playwright_action', new=AsyncMock()) as execute:
            result = await self.cog._tool_browser_playwright(payload,self.context)
            self.assertEqual(result['error'],'Session rejected')
            execute.assert_not_awaited()

    async def test_unavailable_sandbox_exposes_fallback_without_claiming_node_execution(self):
        skill_session(self.context).loaded.add('browser')
        kernel = SimpleNamespace(evaluate=AsyncMock(side_effect=NodeSandboxUnavailable('fixture unavailable')), close=AsyncMock())
        with patch('ai_skill_tools.NodeKernel',return_value=kernel):
            result = await self.cog._execute_ai_tool('node_repl',{'code':'write(1)'},self.context)
        self.assertFalse(result['ok'])
        self.assertTrue(self.context['_node_repl_unavailable'])
        with patch('ai._get_ai_browser_cdp_endpoint', return_value='configured'):
            schemas = self.cog._build_request_ai_tools(self.context)
            self.assertIn('browser_playwright', {item['function']['name'] for item in schemas})
        self.assertEqual(self.cog._model_name_with_runtime('model',self.context),'model')
        self.context['node_repl_runtime']={'engine':'node','sandbox':'docker'}
        self.assertEqual(self.cog._model_name_with_runtime('model',self.context),'model | Node REPL')

    async def test_native_loop_refreshes_schemas_after_skill_activation(self):
        offered = []
        responses = [response(calls=[native_call('load_skill', {'name': 'discord-investigate'})]),
                     response(calls=[native_call('get_user', {'user_id': '42'})]), response('Done')]

        async def request(messages, **kwargs):
            offered.append({item['function']['name'] for item in kwargs['tools']})
            return responses.pop(0), 'native'

        with patch.object(self.cog, '_request_ai_completion', side_effect=request), patch.object(
            self.cog, '_tool_get_user', new=AsyncMock(return_value={'id': '42'})), patch('ai._is_ai_vision_model', return_value=False):
            result = await self.cog.generate_response([{'role': 'user', 'content': 'Look up this user'}], tool_context=self.context)
        self.assertEqual(result[0], 'Done')
        self.assertNotIn('get_user', offered[0])
        self.assertIn('get_user', offered[1])
        self.assertNotIn('_skill_session', self.context)

    async def test_emulated_loop_refreshes_schemas_after_skill_activation(self):
        responses = [response(json.dumps({'tool_calls': [{'name': 'load_skill', 'arguments': {'name': 'web-research'}}]})),
                     response(json.dumps({'tool_calls': [{'name': 'search_google', 'arguments': {'query': 'fixture'}}]})),
                     response('Answer with evidence')]
        generated = AsyncMock(side_effect=responses)
        with patch.object(self.cog, '_generate_ai_completion', new=generated), patch('ai._resolve_ai_tool_call_mode', return_value='emulated'), patch.object(
            self.cog, '_tool_search_google', new=AsyncMock(return_value={'results': []})), patch('ai._is_ai_vision_model', return_value=False):
            result = await self.cog.generate_response([{'role': 'user', 'content': 'Research'}], tool_context=self.context)
        self.assertEqual(result[0], 'Answer with evidence')
        first = generated.await_args_list[0].kwargs['messages'][0]['content']
        second = generated.await_args_list[1].kwargs['messages'][0]['content']
        first_schemas = json.loads(next(line[14:] for line in first.splitlines() if line.startswith('Tool schemas: ')))
        second_schemas = json.loads(next(line[14:] for line in second.splitlines() if line.startswith('Tool schemas: ')))
        self.assertNotIn('search_google', first_schemas)
        self.assertIn('search_google', second_schemas)

    async def test_large_tool_result_can_be_read_without_repeating_fetch(self):
        skill_session(self.context)
        with patch.object(self.cog, '_tool_fetch_raw', new=AsyncMock(return_value={'text': 'x' * 24000})):
            result = await self.cog._execute_ai_tool('fetch_raw', {'url': 'https://example.com'}, self.context)
        resource_id = result['data']['resource_id']
        read = await self.cog._execute_ai_tool('read_resource', {'resource_id': resource_id, 'offset': 15000, 'limit': 16000}, self.context)
        self.assertIn('x' * 100, read['data']['text'])
        self.assertIsNone(read['data']['next_offset'])

    async def test_bridge_rejects_recursion_unloaded_tools_and_browser_rejection(self):
        state = skill_session(self.context)
        state.loaded.add('data-analysis')
        for name in ('node_repl', 'load_skill', 'read_message'):
            self.assertFalse((await self.cog._node_tool_bridge(name, {}, self.context))['ok'])
        state.loaded.add('browser')
        with patch('ai._get_ai_browser_cdp_endpoint', return_value='configured'), patch.object(
            self.cog, '_tool_browser_act', new=AsyncMock(return_value={'error': 'Session rejected'})) as action:
            result = await self.cog._node_tool_bridge('browser_act', {'action': 'click'}, self.context)
        self.assertFalse(result['ok'])
        self.assertEqual(result['error'], 'Session rejected')
        self.assertIs(action.await_args.args[1], self.context)

    async def test_attachment_access_is_checked_before_download(self):
        source = SimpleNamespace(attachments=[SimpleNamespace(id=1)], channel=SimpleNamespace(id=2))
        self.context['message'] = source
        with patch.object(self.cog, '_validate_channel_tool_access', return_value=('No permission', {})), patch('ai_skill_tools.aiohttp.ClientSession') as session:
            result = await self.cog._execute_ai_tool('read_attachment', {'attachment_id': '1'}, self.context)
        self.assertFalse(result['ok'])
        session.assert_not_called()

    async def test_attachment_ids_must_not_be_rounded_javascript_numbers(self):
        with patch.object(self.cog, '_attachment_candidates', new=AsyncMock()) as candidates:
            result = await self.cog._execute_ai_tool('read_attachment', {'attachment_id': 1451539155774343168}, self.context)
        self.assertFalse(result['ok'])
        candidates.assert_not_awaited()

    async def test_resource_catalog_keeps_every_handle_at_capacity(self):
        state = skill_session(self.context)
        for index in range(32):
            state.resources.add(str(index), filename=f'{index}-' + 'x' * 110, source='fixture:' + 'y' * 280)
        result = await self.cog._execute_ai_tool('list_resources', {}, self.context)
        self.assertTrue(result['ok'])
        self.assertEqual(len(result['data']['resources']), 32)
        self.assertEqual({item['resource_id'] for item in result['data']['resources']}, set(state.resources.items))

    async def test_send_resource_queues_the_actual_content_and_prevents_fake_binary_files(self):
        state = skill_session(self.context)
        csv = state.resources.add('a,b\n1,2\n', filename='result.csv')
        sent = await self.cog._execute_ai_tool('send_resource', {'resource_id': csv.resource_id}, self.context)
        self.assertTrue(sent['data']['queued'])
        self.assertEqual(self.context['pending_file_response']['content'], csv.text)
        fake = state.resources.add('not a PDF', filename='result.pdf')
        rejected = await self.cog._execute_ai_tool('send_resource', {'resource_id': fake.resource_id}, self.context)
        self.assertFalse(rejected['ok'])

    async def test_bridge_calls_share_the_request_budget(self):
        skill_session(self.context)
        with patch.object(self.cog, 'MAX_SKILL_TOOL_CALLS', 1):
            first = await self.cog._execute_ai_tool('list_resources', {}, self.context)
            second = await self.cog._execute_ai_tool('list_resources', {}, self.context)
        self.assertTrue(first['ok'])
        self.assertFalse(second['ok'])

    async def test_threads_interactions_require_an_owner_or_enabled_publisher(self):
        with patch('ai_skill_tools.config', return_value=[]), patch('ai_skill_tools.get_global_config', return_value=[]):
            self.assertIsNotNone(self.cog._threads_interaction_error('https://www.threads.com/@account', self.context))
            self.assertIsNone(self.cog._threads_interaction_error('https://threads.com.example.org', self.context))
        with patch('ai_skill_tools.config', return_value=[42]), patch('ai_skill_tools.get_global_config', return_value=[]):
            self.assertIsNone(self.cog._threads_interaction_error('https://www.threads.com', self.context))
        with patch('ai_skill_tools.config', return_value=[]), patch('ai_skill_tools.get_global_config', return_value=['42']):
            self.assertIsNone(self.cog._threads_interaction_error('https://www.threads.net', self.context))

    async def test_invalid_kernel_configuration_does_not_consume_a_slot(self):
        skill_session(self.context).loaded.add('data-analysis')
        def config_value(key, default=None):
            return '--invalid image' if key == 'ai_node_repl_image' else default
        with patch('ai_skill_tools.get_global_config', side_effect=config_value):
            for _ in range(3):
                result = await self.cog._execute_ai_tool('node_repl', {'code': 'write(1)'}, self.context)
                self.assertFalse(result['ok'])
        self.assertEqual(self.cog._node_kernel_semaphore._value, 2)

    async def test_kernel_start_failure_releases_slot_before_fallback(self):
        state = skill_session(self.context)
        state.loaded.add('data-analysis')
        kernel = SimpleNamespace(evaluate=AsyncMock(side_effect=RuntimeError('fixture startup failure')), close=AsyncMock())
        with patch('ai_skill_tools.NodeKernel', return_value=kernel):
            result = await self.cog._execute_ai_tool('node_repl', {'code': 'write(1)'}, self.context)
        self.assertFalse(result['ok'])
        kernel.close.assert_awaited_once()
        self.assertEqual(self.cog._node_kernel_semaphore._value, 2)
        self.assertIsNone(state.node)

    async def test_cancellation_and_request_deadline_close_kernel(self):
        state = skill_session(self.context)
        state.node = SimpleNamespace(close=AsyncMock())
        close = state.node.close
        started = asyncio.Event()

        async def request(*args, **kwargs):
            started.set()
            await asyncio.Event().wait()

        with patch.object(self.cog, '_request_ai_completion', side_effect=request), patch.object(
            self.cog, 'MAX_SKILL_REQUEST_SECONDS', 0.1), patch('ai._is_ai_vision_model', return_value=False):
            with self.assertRaises(TimeoutError):
                await self.cog.generate_response([{'role': 'user', 'content': 'Wait'}], tool_context=self.context)
        self.assertTrue(started.is_set())
        close.assert_awaited_once()
        self.assertNotIn('_skill_session', self.context)


if __name__ == '__main__':
    unittest.main()
