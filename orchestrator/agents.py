"""Declarative agent registry for JARVIS v2.

Replaces the hand-written adapter ladder in server/api.py with a single
config-driven loader. Agents are declared in config/agents.yaml and come in
two transports:

  adapter    — in-process class with a no-arg constructor and .register(tools),
               wrapping a peer HTTP service (Friday, BarelySwing, Muse) or an
               external API (Tavily). Matches the existing integration pattern.

  mcp-stdio  — an MCP server launched as a child process (Sparrow). Its tools
               are discovered at startup and registered into the ToolRegistry
               with a "{name}_" prefix, so the brain sees them like any other
               tool and needs zero changes.

Per JAM: JARVIS never owns domain reasoning. This module only routes; every
entry here is a boundary, not a capability.

Failure isolation matches the old ladder: any agent that fails to load prints
one line and is skipped — JARVIS always comes up.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = "config/agents.yaml"
MCP_CALL_TIMEOUT = 60.0
MCP_START_TIMEOUT = 20.0


# ─── Adapter transport ───────────────────────────────────────


def _load_adapter(name: str, spec: dict, tools) -> None:
    """Import the adapter class and let it register its own tools."""
    module_name = spec["module"]
    class_name = spec["class"]
    module = __import__(module_name, fromlist=[class_name])
    adapter_cls = getattr(module, class_name)
    adapter_cls().register(tools)


# ─── MCP stdio transport ─────────────────────────────────────


class McpStdioAgent:
    """Runs one MCP stdio server on a background event loop.

    The brain's ToolRegistry is synchronous; MCP is async. Bridge: a daemon
    thread owns an asyncio loop and the MCP session, and each tool handler
    submits its call with run_coroutine_threadsafe and blocks on the result.
    """

    def __init__(self, name: str, spec: dict):
        self.name = name
        self.command = spec["command"]
        self.args = list(spec.get("args", []))
        self.env_file = spec.get("env_file")
        self.allowed = set(spec.get("tools", []))  # empty set = allow all
        self._loop: asyncio.AbstractEventLoop | None = None
        self._session = None
        self._ready = threading.Event()
        self._startup_error: Exception | None = None
        self._discovered: list[Any] = []

    # -- lifecycle --

    def start(self) -> list[Any]:
        """Spawn the server, initialize the session, return discovered tools."""
        thread = threading.Thread(
            target=self._thread_main, name=f"mcp-{self.name}", daemon=True
        )
        thread.start()
        if not self._ready.wait(timeout=MCP_START_TIMEOUT):
            raise TimeoutError(
                f"MCP agent '{self.name}' did not start within "
                f"{MCP_START_TIMEOUT:.0f}s"
            )
        if self._startup_error is not None:
            raise self._startup_error
        return self._discovered

    def _thread_main(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._run())
        except Exception as exc:  # startup failed before _ready was set
            self._startup_error = exc
            self._ready.set()

    async def _run(self) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        env = dict(os.environ)
        if self.env_file and os.path.exists(self.env_file):
            for line in Path(self.env_file).read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    env[key.strip()] = value.strip()

        params = StdioServerParameters(
            command=self.command, args=self.args, env=env
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listing = await session.list_tools()
                self._session = session
                self._discovered = list(listing.tools)
                self._ready.set()
                # Keep the session alive for the life of the process.
                await asyncio.Event().wait()

    # -- calling --

    def call(self, tool_name: str, args: dict) -> Any:
        """Synchronous tool call, run on the agent's loop."""
        if self._session is None or self._loop is None:
            return {"error": f"MCP agent '{self.name}' is not running"}
        future = asyncio.run_coroutine_threadsafe(
            self._session.call_tool(tool_name, arguments=args), self._loop
        )
        result = future.result(timeout=MCP_CALL_TIMEOUT)

        texts = []
        for block in result.content:
            text = getattr(block, "text", None)
            if text is not None:
                texts.append(text)
        payload = "\n".join(texts) if texts else ""

        if getattr(result, "isError", False):
            return {"error": payload or f"{tool_name} failed"}

        # MCP servers commonly return JSON as text; hand the brain structure
        # when we can, the raw string when we can't.
        try:
            return json.loads(payload)
        except (ValueError, TypeError):
            return payload

    def register_tools(self, tools) -> int:
        """Register every discovered (and allowed) tool, prefixed."""
        count = 0
        for tool in self._discovered:
            if self.allowed and tool.name not in self.allowed:
                continue
            registered_name = f"{self.name}_{tool.name}"
            schema = tool.inputSchema or {
                "type": "object",
                "properties": {},
            }
            tools.register(
                name=registered_name,
                description=tool.description or registered_name,
                schema=schema,
                handler=self._make_handler(tool.name),
            )
            count += 1
        return count

    def _make_handler(self, tool_name: str):
        def handler(**kwargs):
            return self.call(tool_name, kwargs)

        return handler


# ─── Loader ──────────────────────────────────────────────────


def load_agents(tools, config_path: str | None = None) -> None:
    """Read agents.yaml and register every satisfiable agent.

    Missing config file is not an error (fresh checkout, tests). Each agent
    loads independently; one failure never blocks the rest.
    """
    path = Path(config_path or os.environ.get("JARVIS_AGENTS_CONFIG", DEFAULT_CONFIG_PATH))
    if not path.exists():
        print(f"[agents] no registry at {path} — skipping agent load")
        return

    config = yaml.safe_load(path.read_text()) or {}
    agents = config.get("agents", {})
    if not agents:
        print(f"[agents] registry at {path} declares no agents")
        return

    for name, spec in agents.items():
        try:
            if not spec.get("enabled", True):
                print(f"[agents] {name}: disabled — skipped")
                continue

            missing = [
                var for var in spec.get("requires_env", [])
                if not os.environ.get(var)
            ]
            if missing:
                print(
                    f"[agents] {name}: skipped "
                    f"(missing env: {', '.join(missing)})"
                )
                continue

            transport = spec.get("transport", "adapter")
            if transport == "adapter":
                _load_adapter(name, spec, tools)
                print(f"[agents] {name}: adapter loaded")
            elif transport == "mcp-stdio":
                agent = McpStdioAgent(name, spec)
                agent.start()
                count = agent.register_tools(tools)
                print(f"[agents] {name}: mcp-stdio loaded ({count} tools)")
            else:
                print(f"[agents] {name}: unknown transport '{transport}' — skipped")
        except Exception as exc:
            print(f"[agents] {name}: registration failed: {exc}")
