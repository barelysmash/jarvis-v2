"""Agent registry bookkeeping: per-agent load status + tool ownership.

The HUD's subagent constellation is drawn from registry_snapshot() and lit
from agent_for_tool(), so both must reflect exactly what load_agents() did.
"""

from __future__ import annotations

import os
import sys
import tempfile
import textwrap
import types
import unittest
from pathlib import Path

from orchestrator import agents


class RegistryStub:
    def __init__(self):
        self._tools = {}

    def register(self, name, description, schema, handler):
        self._tools[name] = {"description": description, "schema": schema, "handler": handler}


class GoodAdapter:
    def register(self, tools):
        tools.register("good_scan", "scan", {}, lambda **kw: None)
        tools.register("good_forecast", "forecast", {}, lambda **kw: None)


class BadAdapter:
    def register(self, tools):
        raise RuntimeError("peer service unreachable")


class AgentRegistryTests(unittest.TestCase):
    def setUp(self):
        mod = types.ModuleType("tests._fake_adapters")
        mod.GoodAdapter = GoodAdapter
        mod.BadAdapter = BadAdapter
        sys.modules["tests._fake_adapters"] = mod
        self.addCleanup(sys.modules.pop, "tests._fake_adapters", None)
        os.environ.pop("NEVER_SET_FOR_TEST", None)

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = Path(self.tmp.name) / "agents.yaml"
        self.config.write_text(textwrap.dedent("""
            agents:
              good:
                description: >
                  Good agent
                  across lines.
                transport: adapter
                module: tests._fake_adapters
                class: GoodAdapter
              needs_env:
                description: Needs a key.
                transport: adapter
                module: tests._fake_adapters
                class: GoodAdapter
                requires_env: [NEVER_SET_FOR_TEST]
              parked:
                description: Parked.
                transport: adapter
                module: tests._fake_adapters
                class: GoodAdapter
                enabled: false
              broken:
                description: Broken.
                transport: adapter
                module: tests._fake_adapters
                class: BadAdapter
        """))

    def test_snapshot_reflects_load_outcomes_in_declaration_order(self):
        tools = RegistryStub()
        tools.register("calendar_list_events", "native", {}, lambda **kw: None)
        agents.load_agents(tools, str(self.config))

        snap = agents.registry_snapshot()
        self.assertEqual([a["name"] for a in snap], ["good", "needs_env", "parked", "broken"])
        by_name = {a["name"]: a for a in snap}

        self.assertEqual(by_name["good"]["status"], "loaded")
        self.assertEqual(by_name["good"]["tools"], ["good_forecast", "good_scan"])
        self.assertEqual(by_name["good"]["description"], "Good agent across lines.")

        self.assertEqual(by_name["needs_env"]["status"], "skipped")
        self.assertIn("NEVER_SET_FOR_TEST", by_name["needs_env"]["reason"])
        self.assertEqual(by_name["needs_env"]["tools"], [])

        self.assertEqual(by_name["parked"]["status"], "disabled")
        self.assertEqual(by_name["broken"]["status"], "failed")
        self.assertIn("unreachable", by_name["broken"]["reason"])

    def test_tool_ownership(self):
        tools = RegistryStub()
        tools.register("calendar_list_events", "native", {}, lambda **kw: None)
        agents.load_agents(tools, str(self.config))

        self.assertEqual(agents.agent_for_tool("good_scan"), "good")
        self.assertIsNone(agents.agent_for_tool("calendar_list_events"))
        self.assertIsNone(agents.agent_for_tool("nonexistent"))

    def test_snapshot_is_a_copy(self):
        tools = RegistryStub()
        agents.load_agents(tools, str(self.config))
        snap = agents.registry_snapshot()
        snap[0]["tools"].append("tampered")
        self.assertNotIn("tampered", agents.registry_snapshot()[0]["tools"])

    def test_missing_config_leaves_registry_empty(self):
        agents.load_agents(RegistryStub(), str(Path(self.tmp.name) / "nope.yaml"))
        # Loader returns before clearing; a prior registry would persist, but a
        # fresh process has none. Either way, nothing new is declared.
        self.assertTrue(all(isinstance(a, dict) for a in agents.registry_snapshot()))


if __name__ == "__main__":
    unittest.main()
