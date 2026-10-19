"""Agent registry snapshot for the HUD constellation.

The HUD draws one node per declared agent from registry_snapshot() and lights
nodes from the `agent` field on tool events (ToolRegistry.owner_of). Both
sit on the cockpit's AGENT_STATUS / owner-tracking machinery, so this pins
the shape the HUD depends on.
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
from orchestrator.tools import ToolRegistry


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
        agents.AGENT_STATUS.clear()
        self.addCleanup(agents.AGENT_STATUS.clear)

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # Keep the event log writes inside the temp dir.
        self._old_data_dir = os.environ.get("JARVIS_DATA_DIR")
        os.environ["JARVIS_DATA_DIR"] = self.tmp.name
        self.addCleanup(self._restore_data_dir)

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

    def _restore_data_dir(self):
        if self._old_data_dir is None:
            os.environ.pop("JARVIS_DATA_DIR", None)
        else:
            os.environ["JARVIS_DATA_DIR"] = self._old_data_dir

    def _load(self) -> ToolRegistry:
        tools = ToolRegistry()
        tools.register("calendar_list_events", "native", {}, lambda **kw: None)
        agents.load_agents(tools, str(self.config))
        return tools

    def test_snapshot_reflects_load_outcomes_in_declaration_order(self):
        self._load()
        snap = agents.registry_snapshot()
        self.assertEqual([a["name"] for a in snap], ["good", "needs_env", "parked", "broken"])
        by_name = {a["name"]: a for a in snap}

        self.assertEqual(by_name["good"]["status"], "loaded")
        self.assertEqual(by_name["good"]["tools"], ["good_forecast", "good_scan"])
        self.assertEqual(by_name["good"]["description"], "Good agent across lines.")
        self.assertIsNone(by_name["good"]["reason"])

        self.assertEqual(by_name["needs_env"]["status"], "skipped")
        self.assertIn("NEVER_SET_FOR_TEST", by_name["needs_env"]["reason"])
        self.assertEqual(by_name["needs_env"]["tools"], [])

        self.assertEqual(by_name["parked"]["status"], "disabled")
        self.assertEqual(by_name["broken"]["status"], "failed")
        self.assertIn("unreachable", by_name["broken"]["reason"])

        for a in snap:
            self.assertEqual(
                set(a), {"name", "description", "transport", "status", "reason", "tools"}
            )

    def test_tool_ownership_matches_snapshot(self):
        tools = self._load()
        self.assertEqual(tools.owner_of("good_scan"), "good")
        # Built-ins resolve to a non-agent owner; the HUD ignores those.
        self.assertNotIn(tools.owner_of("calendar_list_events"), agents.AGENT_STATUS)
        self.assertEqual(tools.owner_of("nonexistent"), "unknown")

    def test_snapshot_is_a_copy(self):
        self._load()
        snap = agents.registry_snapshot()
        snap[0]["tools"].append("tampered")
        self.assertNotIn("tampered", agents.registry_snapshot()[0]["tools"])

    def test_missing_config_declares_nothing(self):
        agents.load_agents(ToolRegistry(), str(Path(self.tmp.name) / "nope.yaml"))
        self.assertEqual(agents.registry_snapshot(), [])


if __name__ == "__main__":
    unittest.main()
