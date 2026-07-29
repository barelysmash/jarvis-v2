from __future__ import annotations

import json
import unittest

import httpx

from tools.integrations.friday import FridayAdapter


class RegistryStub:
    def __init__(self):
        self.tools = {}

    def register(self, name, description, schema, handler):
        self.tools[name] = {
            "description": description,
            "schema": schema,
            "handler": handler,
        }


class FridayAdapterTests(unittest.TestCase):
    def make_adapter(self, handler):
        client = httpx.Client(
            transport=httpx.MockTransport(handler),
            timeout=5.0,
        )
        return FridayAdapter(
            base_url="http://friday.test",
            api_token="secret-token",
            client=client,
        )

    def test_registers_four_analysis_tools(self):
        adapter = self.make_adapter(
            lambda request: httpx.Response(
                200,
                json={"request_id": "unused", "data": {}},
            )
        )
        registry = RegistryStub()

        adapter.register(registry)

        self.assertEqual(
            set(registry.tools),
            {
                "friday_forecast",
                "friday_live_scan",
                "friday_live_scan_context",
                "friday_context_trend_watch",
            },
        )

    def test_live_scan_uses_bearer_auth_and_expected_payload(self):
        captured = {}

        def handler(request):
            captured["path"] = request.url.path
            captured["authorization"] = request.headers["Authorization"]
            captured["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "request_id": "scan-1",
                    "data": {"status": "complete"},
                },
            )

        adapter = self.make_adapter(handler)

        result = adapter.live_scan(
            targets=[{"ticker": "NVDA", "direction": "long"}],
            workers=2,
            top=1,
        )

        self.assertEqual(captured["path"], "/v1/live-scan")
        self.assertEqual(
            captured["authorization"],
            "Bearer secret-token",
        )
        self.assertEqual(
            captured["body"],
            {
                "targets": [
                    {"ticker": "NVDA", "direction": "long"}
                ],
                "workers": 2,
                "top": 1,
            },
        )
        self.assertEqual(result["request_id"], "scan-1")
        self.assertEqual(result["data"]["status"], "complete")

    def test_none_values_are_not_sent(self):
        captured = {}

        def handler(request):
            captured["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={"request_id": "trend-1", "data": {}},
            )

        adapter = self.make_adapter(handler)

        adapter.context_trend_watch(
            report_root="reports",
            direction=None,
            trend=None,
        )

        self.assertNotIn("direction", captured["body"])
        self.assertNotIn("trend", captured["body"])

    def test_server_error_is_sanitized(self):
        def handler(request):
            return httpx.Response(
                422,
                json={
                    "error": {
                        "code": "invalid_request",
                        "message": "unsupported field",
                    }
                },
            )

        adapter = self.make_adapter(handler)

        with self.assertRaisesRegex(
            RuntimeError,
            "HTTP 422, invalid_request: unsupported field",
        ):
            adapter.live_scan(
                targets=[{"ticker": "NVDA", "direction": "long"}]
            )


if __name__ == "__main__":
    unittest.main()
