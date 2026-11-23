"""A small Agent Client Protocol (ACP) client: runs `qwen --acp` and talks JSON-RPC 2.0 over its stdin/stdout,
one JSON object per line.

Written against Qwen Code 0.28.x (ACP protocol version 1). Kept deliberately small and dependency-free:

  client = AcpClient(["qwen", "--acp"], cwd, on_update=..., on_permission=..., on_exit=...)
  await client.start()                     # spawns the agent and runs `initialize`
  session = await client.new_session()     # or load_session(id) / list_sessions()
  await client.prompt(session_id, "hi")    # returns when the turn ends; updates arrive meanwhile
  await client.cancel(session_id)          # stop the current turn

on_update(session_id, update)        every `session/update` notification (message/thought chunks, tool calls...)
on_permission(session_id, params)    awaited for `session/request_permission`; returns an optionId or None (cancel)
on_exit(returncode, stderr_tail)     the agent process ended
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from collections import deque
from pathlib import Path
from typing import Any, Awaitable, Callable

PROTOCOL_VERSION = 1
CLIENT_INFO = {"name": "spaceman-ui", "title": "spaceManUI", "version": "0.1.0"}
LINE_LIMIT = 64 * 1024 * 1024          # tool results can be big; asyncio's default line limit is 64 KB


class AcpError(Exception):
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(f"{message} (code {code})")
        self.code, self.message, self.data = code, message, data


def resolve_command(cmd: list[str]) -> list[str]:
    """Find the agent executable on PATH (on Windows `qwen` is really `qwen.cmd`)."""
    exe = shutil.which(cmd[0]) or cmd[0]
    return [exe, *cmd[1:]]


class AcpClient:
    def __init__(self, command: list[str], cwd: str | Path,
                 on_update: Callable[[str, dict], Any] | None = None,
                 on_permission: Callable[[str, dict], Awaitable[str | None]] | None = None,
                 on_exit: Callable[[int | None, str], Any] | None = None,
                 on_notice: Callable[[str, dict], Any] | None = None,
                 stderr_log: Path | None = None, env: dict | None = None) -> None:
        self.command = resolve_command(command)
        self.cwd = str(cwd)
        self.on_update, self.on_permission, self.on_exit, self.on_notice = on_update, on_permission, on_exit, on_notice
        self.stderr_log = stderr_log
        self.env = env
        self.proc: asyncio.subprocess.Process | None = None
        self.agent_info: dict = {}
        self.capabilities: dict = {}
        self.auth_methods: list = []
        self._next_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._tasks: set = set()
        self._stderr_tail: deque = deque(maxlen=40)
        self._closing = False

    # -- lifecycle -----------------------------------------------------------------------------
    async def start(self) -> dict:
        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = 0x08000000          # CREATE_NO_WINDOW: no console flashing up
        self.proc = await asyncio.create_subprocess_exec(
            *self.command, cwd=self.cwd, env=self.env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            limit=LINE_LIMIT, **kwargs)
        self._spawn(self._read_stdout())
        self._spawn(self._read_stderr())
        result = await self.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False},
            "clientInfo": CLIENT_INFO,
        })
        self.agent_info = result.get("agentInfo") or {}
        self.capabilities = result.get("agentCapabilities") or {}
        self.auth_methods = result.get("authMethods") or []
        return result

    async def close(self) -> None:
        self._closing = True
        if self.proc and self.proc.returncode is None:
            try:
                self.proc.stdin.close()
            except Exception:
                pass
            try:
                await asyncio.wait_for(self.proc.wait(), 3)
            except asyncio.TimeoutError:
                self.proc.kill()

    async def settle(self, seconds: float = 2.0) -> None:
        """After a failed start: give the agent a moment to exit and its stderr to be read."""
        if self.proc is None:
            return
        try:
            await asyncio.wait_for(self.proc.wait(), seconds)
            await asyncio.sleep(0.2)
        except asyncio.TimeoutError:
            pass

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    def stderr_tail(self) -> str:
        return "\n".join(self._stderr_tail)

    # -- sessions ------------------------------------------------------------------------------
    async def new_session(self) -> dict:
        return await self.request("session/new", {"cwd": self.cwd, "mcpServers": []})

    async def load_session(self, session_id: str) -> dict:
        """Replays the saved conversation as session/update notifications, then returns."""
        return await self.request("session/load", {"sessionId": session_id, "cwd": self.cwd, "mcpServers": []})

    @property
    def can_list(self) -> bool:
        return "list" in ((self.capabilities.get("sessionCapabilities")) or {})

    @property
    def can_load(self) -> bool:
        return bool(self.capabilities.get("loadSession"))

    async def list_sessions(self) -> list[dict]:
        if not self.can_list:
            return []
        result = await self.request("session/list", {"cwd": self.cwd})
        return result.get("sessions") or []

    async def prompt(self, session_id: str, text: str, images: list[dict] | None = None) -> dict:
        blocks = [{"type": "text", "text": text}] + list(images or [])
        return await self.request("session/prompt", {"sessionId": session_id, "prompt": blocks}, timeout=None)

    async def cancel(self, session_id: str) -> None:
        await self.notify("session/cancel", {"sessionId": session_id})

    async def set_config(self, session_id: str, config_id: str, value: str) -> dict:
        return await self.request("session/set_config_option",
                                  {"sessionId": session_id, "configId": config_id, "value": value})

    # -- JSON-RPC --------------------------------------------------------------------------------
    async def request(self, method: str, params: dict, timeout: float | None = 120) -> dict:
        if not self.alive:
            raise AcpError(-32000, "the agent is not running")
        self._next_id += 1
        rid = self._next_id
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        await self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        try:
            return await (asyncio.wait_for(fut, timeout) if timeout else fut)
        finally:
            self._pending.pop(rid, None)

    async def notify(self, method: str, params: dict) -> None:
        if self.alive:
            await self._send({"jsonrpc": "2.0", "method": method, "params": params})

    async def _send(self, obj: dict) -> None:
        data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            self.proc.stdin.write(data)
            await self.proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError, RuntimeError) as e:
            raise AcpError(-32000, f"lost the connection to the agent: {e}")

    def _spawn(self, coro) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _read_stdout(self) -> None:
        reader = self.proc.stdout
        while True:
            try:
                line = await reader.readline()
            except (asyncio.LimitOverrunError, ValueError):
                continue                                 # a monster line: skip it rather than die
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                self._stderr_tail.append(line.decode("utf-8", "replace")[:300])
                continue
            try:
                await self._dispatch(msg)
            except Exception as e:                       # one bad message must not cut the connection
                self._stderr_tail.append(f"spaceman: error handling {msg.get('method') or 'response'}: {e!r}")
        code = await self.proc.wait()
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(AcpError(-32000, "the agent stopped"))
        if self.on_exit and not self._closing and self.agent_info:   # a failed start is reported by start()
            await _maybe_await(self.on_exit(code, self.stderr_tail()))

    async def _read_stderr(self) -> None:
        log = None
        if self.stderr_log:
            try:
                self.stderr_log.parent.mkdir(parents=True, exist_ok=True)
                log = open(self.stderr_log, "a", encoding="utf-8")
            except OSError:
                log = None
        try:
            while True:
                line = await self.proc.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", "replace").rstrip()
                self._stderr_tail.append(text)
                if log:
                    log.write(text + "\n")
                    log.flush()
        finally:
            if log:
                log.close()

    async def _dispatch(self, msg: dict) -> None:
        method = msg.get("method")
        if method is None:                               # a response to one of our requests
            fut = self._pending.get(msg.get("id"))
            if fut and not fut.done():
                if "error" in msg:
                    e = msg["error"] or {}
                    fut.set_exception(AcpError(e.get("code", -1), e.get("message", "error"), e.get("data")))
                else:
                    fut.set_result(msg.get("result") or {})
            return
        params = msg.get("params") or {}
        if "id" in msg:                                  # a request from the agent: answer it (without blocking reads)
            self._spawn(self._answer(msg["id"], method, params))
            return
        if method == "session/update":
            if self.on_update:
                await _maybe_await(self.on_update(params.get("sessionId", ""), params.get("update") or {}))
        elif self.on_notice:
            await _maybe_await(self.on_notice(method, params))

    async def _answer(self, rid, method: str, params: dict) -> None:
        try:
            if method == "session/request_permission":
                choice = await self.on_permission(params.get("sessionId", ""), params) if self.on_permission else None
                outcome = {"outcome": "selected", "optionId": choice} if choice else {"outcome": "cancelled"}
                await self._send({"jsonrpc": "2.0", "id": rid, "result": {"outcome": outcome}})
            else:
                await self._send({"jsonrpc": "2.0", "id": rid,
                                  "error": {"code": -32601, "message": f"{method} is not supported by spaceManUI"}})
        except AcpError:
            pass


async def _maybe_await(value):
    if asyncio.iscoroutine(value) or isinstance(value, asyncio.Future):
        return await value
    return value


def default_command() -> list[str]:
    """The agent to run: $SPACEMAN_AGENT (a full command line) or `qwen --acp`."""
    env = os.environ.get("SPACEMAN_AGENT")
    if env:
        import shlex
        return shlex.split(env, posix=sys.platform != "win32")
    return ["qwen", "--acp"]
