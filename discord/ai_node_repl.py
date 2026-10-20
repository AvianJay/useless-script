"""Request-local Docker-isolated Node kernel with a permission-aware tool bridge."""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from pathlib import Path
from uuid import uuid4


DEFAULT_NODE_IMAGE = "node:24-alpine"
MAX_NODE_CODE_CHARS = 16_000
MAX_PROTOCOL_BYTES = 64_000


class NodeSandboxError(RuntimeError):
    pass


class NodeSandboxUnavailable(NodeSandboxError):
    pass


class NodeKernel:
    def __init__(self, *, image: str = DEFAULT_NODE_IMAGE):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/:@-]{0,200}", image):
            raise ValueError("Invalid Node sandbox image")
        self.image = image
        self.name = f"ai-skill-{uuid4().hex}"
        self.process = None
        self.lock = asyncio.Lock()
        self.docker = shutil.which("docker")
        self.node_version = None
        self.evaluations = 0
        self.last_runtime = None

    def command(self) -> list[str]:
        if not self.docker:
            raise NodeSandboxError("Node REPL requires Docker and a running Linux container daemon")
        worker = Path(__file__).with_name("ai_node_worker.cjs").resolve()
        # Mount exactly one non-secret source file. Never mount the repo, Docker
        # socket, user profile, host workspace or application configuration.
        return [
            self.docker, "run", "--rm", "--interactive", "--pull=never",
            "--name", self.name, "--network=none", "--read-only", "--cap-drop=ALL",
            "--security-opt=no-new-privileges", "--pids-limit=32", "--memory=192m",
            "--memory-swap=192m", "--cpus=0.5", "--user=65534:65534",
            "--tmpfs=/tmp:rw,noexec,nosuid,size=16m", "--workdir=/tmp",
            "--mount", f"type=bind,source={worker},target=/runtime/worker.cjs,readonly",
            "--entrypoint=node", self.image, "/runtime/worker.cjs",
        ]

    async def _receive(self) -> dict:
        line = await self.process.stdout.readline()
        if not line or len(line) > MAX_PROTOCOL_BYTES:
            raise NodeSandboxError("Node sandbox stopped or exceeded its output limit")
        try:
            result = json.loads(line)
        except (ValueError, UnicodeError):
            raise NodeSandboxError("Invalid Node sandbox response") from None
        if not isinstance(result, dict):
            raise NodeSandboxError("Invalid Node sandbox response")
        return result

    async def _send(self, message: dict) -> None:
        self.process.stdin.write(json.dumps(message, ensure_ascii=True).encode("ascii") + b"\n")
        await self.process.stdin.drain()

    async def start(self) -> None:
        if self.process is not None:
            return
        try:
            self.process = await asyncio.create_subprocess_exec(
                *self.command(), stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                limit=MAX_PROTOCOL_BYTES,
            )
            async with asyncio.timeout(20):
                ready = await self._receive()
                if ready.get("type") != "ready":
                    raise NodeSandboxError("Node sandbox did not initialize")
                self.node_version = str(ready.get("node_version") or "unknown")[:40]
        except asyncio.CancelledError:
            await self.close()
            raise
        except Exception:
            await self.close()
            raise NodeSandboxUnavailable(
                f"Node sandbox unavailable. Start Docker and pre-pull {self.image}; "
                "direct browser tools and execute_code remain available."
            ) from None

    async def evaluate(self, code: str, broker, *, timeout: int = 120) -> dict:
        if not isinstance(code, str) or not code.strip() or len(code) > MAX_NODE_CODE_CHARS:
            raise ValueError(f"code is required and must be at most {MAX_NODE_CODE_CHARS} characters")
        async with self.lock:
            await self.start()
            evaluation_id = uuid4().hex
            self.evaluations += 1
            rpc_calls = 0
            self.last_runtime = {"engine": "node", "sandbox": "docker", "node_version": self.node_version,
                                 "kernel_id": self.name.removeprefix("ai-skill-")[:12],
                                 "evaluation": self.evaluations, "bridge_calls": 0}
            try:
                async with asyncio.timeout(max(1, min(300, timeout))):
                    await self._send({"type": "evaluate", "id": evaluation_id, "code": code})
                    while True:
                        message = await self._receive()
                        if message.get("type") == "result" and message.get("id") == evaluation_id:
                            return {
                                **{key: message.get(key) for key in ("ok", "output", "value", "error", "details", "truncated")},
                                "runtime": dict(self.last_runtime),
                            }
                        if message.get("type") != "rpc" or message.get("evaluation_id") != evaluation_id:
                            raise NodeSandboxError("Unexpected Node sandbox message")
                        rpc_calls += 1
                        self.last_runtime["bridge_calls"] = rpc_calls
                        if rpc_calls > 24:
                            raise NodeSandboxError("Node bridge call limit exceeded")
                        name, arguments = message.get("name"), message.get("arguments")
                        if not isinstance(name, str) or not isinstance(arguments, dict):
                            reply = {"ok": False, "error": "Invalid tool bridge request"}
                        else:
                            reply = await broker(name, arguments)
                        await self._send({"type": "rpc_result", "id": message.get("id"), **reply})
            except asyncio.CancelledError:
                await self.close()
                raise
            except TimeoutError:
                await self.close()
                raise NodeSandboxError("Node evaluation timed out; kernel reset. Completed actions were not undone.") from None
            except Exception:
                await self.close()
                raise

    async def close(self) -> None:
        process, self.process = self.process, None
        if process is None:
            return
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        await process.wait()
        if self.docker:
            cleanup = await asyncio.create_subprocess_exec(
                self.docker, "rm", "--force", self.name,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(cleanup.wait(), timeout=10)
            except TimeoutError:
                cleanup.kill()
                await cleanup.wait()
