import importlib.util
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
from discord.ext import commands


DISCORD_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DISCORD_DIR))


def load_owner_tools_for_test():
    test_bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())

    globalenv_stub = ModuleType("globalenv")
    globalenv_stub.bot = test_bot
    globalenv_stub.start_bot = MagicMock()
    globalenv_stub.get_user_data = MagicMock()
    globalenv_stub.set_user_data = MagicMock()
    globalenv_stub.config = lambda key, default=None, **kwargs: default
    globalenv_stub.get_server_config = MagicMock()
    globalenv_stub.set_server_config = MagicMock()
    globalenv_stub._config = {}
    globalenv_stub.default_config = {}
    globalenv_stub.get_all_user_data = MagicMock()
    globalenv_stub.db = MagicMock()
    globalenv_stub.on_close_tasks = []
    globalenv_stub.reload_config = MagicMock()
    globalenv_stub.config_path = "config.json"
    globalenv_stub.fetched_emojis_cache = None

    logger_stub = ModuleType("logger")
    logger_stub.log = MagicMock()

    util_commands_stub = ModuleType("UtilCommands")
    util_commands_stub.full_version = "test"

    chat_exporter_stub = ModuleType("chat_exporter")
    chat_exporter_stub.raw_export = AsyncMock()

    dependency_stubs = {
        "globalenv": globalenv_stub,
        "logger": logger_stub,
        "UtilCommands": util_commands_stub,
        "chat_exporter": chat_exporter_stub,
    }
    previous_modules = {name: sys.modules.get(name) for name in dependency_stubs}
    sys.modules.update(dependency_stubs)

    module_name = "owner_tools_eval_under_test"
    module_path = DISCORD_DIR / "OwnerTools.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        for name, previous in previous_modules.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous

    return module


owner_tools = load_owner_tools_for_test()


def make_context(owner_id: int = 1):
    author = SimpleNamespace(id=owner_id)
    return SimpleNamespace(
        author=author,
        message=SimpleNamespace(id=10),
        channel=SimpleNamespace(id=20),
        guild=SimpleNamespace(id=30),
        send=AsyncMock(),
    )


class OwnerEvalExecutionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        owner_tools._eval_last_results.clear()

    async def assert_eval(self, code, expected, *, owner_id=1):
        content, is_error = await owner_tools.run_owner_eval(
            make_context(owner_id),
            code,
        )
        self.assertFalse(is_error, content)
        self.assertEqual(content, expected)

    async def test_expression_and_multiline_last_expression(self):
        await self.assert_eval("1 + 1", "2")
        await self.assert_eval("value = 3\nvalue * 4", "12")

    async def test_explicit_return(self):
        await self.assert_eval("return {'ok': True}", "{'ok': True}")

    async def test_await_import_builtins_and_context(self):
        code = """import math
await asyncio.sleep(0)
(math.sqrt(sum([1, 3, 5])), message.id, author.id, channel.id, guild.id, ctx.author.id, bot is not None)"""
        await self.assert_eval(code, "(3.0, 10, 1, 20, 30, 1, True)")

    async def test_print_and_result_are_combined(self):
        await self.assert_eval("print('hello')\n5", "hello\n5")

    async def test_no_output_has_explicit_message(self):
        await self.assert_eval("value = 1", owner_tools.EVAL_NO_OUTPUT_MESSAGE)

    async def test_repr_failure_is_safe(self):
        code = """class BrokenRepr:
    def __repr__(self):
        raise RuntimeError('broken')
BrokenRepr()"""
        await self.assert_eval(code, "<repr() failed: RuntimeError>")

    async def test_fenced_code_is_cleaned(self):
        await self.assert_eval("```python\n1 + 2\n```", "3")
        self.assertEqual(owner_tools.cleanup_eval_code("```py\n4 + 5\n```"), "4 + 5")
        self.assertEqual(owner_tools.cleanup_eval_code("```\n6 + 7\n```"), "6 + 7")

    async def test_syntax_error_includes_traceback(self):
        content, is_error = await owner_tools.run_owner_eval(make_context(), "if")
        self.assertTrue(is_error)
        self.assertIn("Traceback (most recent call last)", content)
        self.assertIn("SyntaxError", content)

    async def test_runtime_error_keeps_printed_output_and_traceback(self):
        code = "print('before')\nraise ValueError('boom')"
        content, is_error = await owner_tools.run_owner_eval(make_context(), code)
        self.assertTrue(is_error)
        self.assertTrue(content.startswith("before\nTraceback (most recent call last)"))
        self.assertIn("ValueError: boom", content)

    async def test_last_result_is_isolated_per_owner(self):
        await self.assert_eval("10", "10", owner_id=1)
        await self.assert_eval("_ + 1", "11", owner_id=1)
        await self.assert_eval("_ is None", "True", owner_id=2)

    async def test_error_and_none_do_not_replace_last_result(self):
        ctx = make_context()
        content, is_error = await owner_tools.run_owner_eval(ctx, "7")
        self.assertEqual((content, is_error), ("7", False))

        _, is_error = await owner_tools.run_owner_eval(ctx, "raise ValueError('no')")
        self.assertTrue(is_error)
        content, is_error = await owner_tools.run_owner_eval(ctx, "None")
        self.assertEqual((content, is_error), (owner_tools.EVAL_NO_OUTPUT_MESSAGE, False))
        content, is_error = await owner_tools.run_owner_eval(ctx, "_")
        self.assertEqual((content, is_error), ("7", False))


class OwnerEvalResponseTests(unittest.IsolatedAsyncioTestCase):
    async def test_short_output_is_sent_inline_without_mentions(self):
        ctx = make_context()
        await owner_tools.send_eval_response(ctx, "short", is_error=False)

        message = ctx.send.await_args.args[0]
        kwargs = ctx.send.await_args.kwargs
        self.assertEqual(message, "結果：\n```py\nshort\n```")
        self.assertNotIn("file", kwargs)
        self.assertFalse(kwargs["allowed_mentions"].everyone)
        self.assertFalse(kwargs["allowed_mentions"].users)
        self.assertFalse(kwargs["allowed_mentions"].roles)

    async def test_long_output_is_sent_as_utf8_file(self):
        ctx = make_context()
        content = "測" * (owner_tools.EVAL_INLINE_LIMIT + 1)
        await owner_tools.send_eval_response(ctx, content, is_error=False)

        kwargs = ctx.send.await_args.kwargs
        attachment = kwargs["file"]
        self.assertEqual(attachment.filename, "eval_output.txt")
        self.assertEqual(attachment.fp.getvalue().decode("utf-8"), content)

    async def test_code_fence_output_and_errors_use_files(self):
        ctx = make_context()
        await owner_tools.send_eval_response(ctx, "contains ``` fence", is_error=True)

        message = ctx.send.await_args.args[0]
        attachment = ctx.send.await_args.kwargs["file"]
        self.assertEqual(message, "執行代碼時發生錯誤：輸出已附加為檔案。")
        self.assertEqual(attachment.filename, "eval_error.txt")

    def test_eval_is_canonical_command_with_compatibility_alias(self):
        self.assertEqual(owner_tools.eval_command.name, "eval")
        self.assertIn("eval_command", owner_tools.eval_command.aliases)


if __name__ == "__main__":
    unittest.main()
