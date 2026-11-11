from __future__ import annotations

import json
import unittest
from pathlib import Path

import httpx
import yaml

from orchestrator.grounding import ground_tool_result
from orchestrator.tools import ToolRegistry
from tools.integrations.atlas import AtlasAdapter

REPO = Path(__file__).resolve().parents[1]


class AtlasAdapterTests(unittest.TestCase):
    def make_adapter(self, handler):
        client = httpx.Client(
            transport=httpx.MockTransport(handler),
            timeout=5.0,
        )
        return AtlasAdapter(
            base_url="http://atlas.test/",
            api_token="secret-token",
            client=client,
        )

    def test_registers_two_read_only_tools(self):
        adapter = self.make_adapter(
            lambda request: httpx.Response(200, json={"data": {}})
        )
        registry = ToolRegistry()

        adapter.register(registry)

        names = {name for name in registry.names() if name.startswith("atlas_")}
        self.assertEqual(names, {"atlas_operating_summary", "atlas_freshness"})

    def test_operating_summary_posts_only_the_fields_given(self):
        captured = {}

        def handler(request):
            captured["method"] = request.method
            captured["path"] = request.url.path
            captured["authorization"] = request.headers["Authorization"]
            captured["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "request_id": "sum-1",
                    "data": {"figures": "relative_only", "changes": []},
                },
            )

        adapter = self.make_adapter(handler)
        result = adapter.operating_summary(
            start_date="2026-11-01", end_date="2026-11-09"
        )

        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["path"], "/v1/operating-summary")
        self.assertEqual(captured["authorization"], "Bearer secret-token")
        self.assertEqual(
            captured["body"],
            {"start_date": "2026-11-01", "end_date": "2026-11-09"},
        )
        self.assertEqual(result["request_id"], "sum-1")
        self.assertEqual(result["data"]["figures"], "relative_only")

    def test_default_summary_sends_an_empty_object(self):
        captured = {}

        def handler(request):
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"request_id": "r", "data": {}})

        self.make_adapter(handler).operating_summary()
        self.assertEqual(captured["body"], {})

    def test_freshness_is_a_get(self):
        captured = {}

        def handler(request):
            captured["method"] = request.method
            captured["path"] = request.url.path
            captured["authorization"] = request.headers["Authorization"]
            return httpx.Response(
                200,
                json={
                    "request_id": "f-1",
                    "data": {"last_service_date": "2026-11-09"},
                },
            )

        result = self.make_adapter(handler).freshness()

        self.assertEqual(captured["method"], "GET")
        self.assertEqual(captured["path"], "/v1/freshness")
        self.assertEqual(captured["authorization"], "Bearer secret-token")
        self.assertEqual(result["data"]["last_service_date"], "2026-11-09")

    def test_atlas_error_envelope_is_surfaced(self):
        adapter = self.make_adapter(
            lambda request: httpx.Response(
                422,
                json={
                    "request_id": "e-1",
                    "error": {
                        "code": "unprocessable_period",
                        "message": "comparison period: no daily records found",
                    },
                },
            )
        )

        with self.assertRaises(RuntimeError) as caught:
            adapter.operating_summary()

        message = str(caught.exception)
        self.assertIn("HTTP 422", message)
        self.assertIn("unprocessable_period", message)
        self.assertNotIn("secret-token", message)

    def test_unreachable_service_is_a_clear_error(self):
        def handler(request):
            raise httpx.ConnectError("connection refused", request=request)

        with self.assertRaises(RuntimeError) as caught:
            self.make_adapter(handler).freshness()

        self.assertIn("Atlas is unreachable", str(caught.exception))

    def test_missing_token_refuses_to_construct(self):
        with self.assertRaises(ValueError):
            AtlasAdapter(base_url="http://atlas.test", api_token="")

    def test_registry_grounds_atlas_results(self):
        adapter = self.make_adapter(
            lambda request: httpx.Response(
                200,
                json={
                    "request_id": "sum-2",
                    "data": {"figures": "relative_only", "changes": []},
                },
            )
        )
        registry = ToolRegistry()
        adapter.register(registry)

        result, is_error = registry.execute("atlas_operating_summary", {})

        self.assertFalse(is_error)
        self.assertIn("SOURCE BOUNDARY — ATLAS", result)
        self.assertIn("relative_only", result)

    def test_registry_reports_atlas_failures_as_errors(self):
        adapter = self.make_adapter(
            lambda request: httpx.Response(
                401, json={"error": {"code": "unauthorized"}}
            )
        )
        registry = ToolRegistry()
        adapter.register(registry)

        result, is_error = registry.execute("atlas_freshness", {})

        self.assertTrue(is_error)
        self.assertIn("unauthorized", str(result))


class AtlasBoundaryTests(unittest.TestCase):
    def test_boundary_forbids_absolute_figures(self):
        rendered = ground_tool_result("atlas_operating_summary", {"changes": []})

        self.assertIn("SOURCE BOUNDARY — ATLAS", rendered)
        self.assertIn("withholds absolute figures", rendered)

    def test_boundary_covers_implied_size_causes_and_drill_downs(self):
        rendered = ground_tool_result("atlas_operating_summary", {"changes": []})

        # Each of these was overstepped in the first live answer (2026-11-11).
        self.assertIn("small, large, low or high base", rendered)
        self.assertIn("Report what moved, not why", rendered)
        self.assertIn("Do not offer nightly detail", rendered)

    def test_boundary_covers_margin_cross_period_links_and_coverage(self):
        rendered = ground_tool_result("atlas_operating_summary", {"changes": []})

        # Each was missing or overstepped in the second live answer (2026-11-11).
        self.assertIn("Atlas reports no margin", rendered)
        self.assertIn("belongs to the two periods it compares", rendered)
        self.assertIn("nights_missing and nights_in_review", rendered)
        self.assertIn("do not claim the period is complete", rendered)
        self.assertIn("'changes': []", rendered)

    def test_boundary_is_not_applied_to_other_tools(self):
        self.assertEqual(ground_tool_result("atlasphere_lookup", "x"), "x")


class AtlasRegistryEntryTests(unittest.TestCase):
    def test_agents_yaml_declares_atlas_as_a_token_gated_adapter(self):
        config = yaml.safe_load(
            (REPO / "config" / "agents.yaml").read_text(encoding="utf-8")
        )
        entry = config["agents"]["atlas"]

        self.assertEqual(entry["transport"], "adapter")
        self.assertEqual(entry["requires_env"], ["ATLAS_API_TOKEN"])
        module = __import__(entry["module"], fromlist=[entry["class"]])
        self.assertIs(getattr(module, entry["class"]), AtlasAdapter)

    def test_env_template_documents_the_atlas_variables(self):
        template = (REPO / "deploy" / "env.template").read_text(encoding="utf-8")
        for name in ("ATLAS_BASE_URL=", "ATLAS_API_TOKEN=", "ATLAS_TIMEOUT_SECONDS="):
            self.assertIn(name, template)
        # Names only, never a value.
        self.assertIn("\nATLAS_API_TOKEN=\n", template)


if __name__ == "__main__":
    unittest.main()
