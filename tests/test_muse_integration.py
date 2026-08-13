from __future__ import annotations

import json
import unittest

import httpx

from tools.integrations.muse import MuseAdapter


class RegistryStub:
    def __init__(self):
        self.tools = {}

    def register(self, name, description, schema, handler):
        self.tools[name] = {
            "description": description,
            "schema": schema,
            "handler": handler,
        }


class MuseAdapterTests(unittest.TestCase):
    def make_adapter(self, handler):
        client = httpx.Client(
            transport=httpx.MockTransport(handler),
            timeout=5.0,
        )
        return MuseAdapter(
            base_url="http://muse.test",
            client=client,
        )

    def test_registers_two_creative_tools(self):
        adapter = self.make_adapter(
            lambda request: httpx.Response(200, json={})
        )
        registry = RegistryStub()

        adapter.register(registry)

        self.assertEqual(
            set(registry.tools),
            {
                "muse_create_project",
                "muse_get_project",
            },
        )

    def test_create_project_uses_workflow_endpoint(self):
        captured = {}

        def handler(request):
            captured["method"] = request.method
            captured["path"] = request.url.path
            captured["body"] = json.loads(request.content)
            return httpx.Response(
                201,
                json={
                    "project": {
                        "project_id": "11111111-1111-1111-1111-111111111111",
                        "title": "Armadillo Motel",
                        "source_idea": "armadillo motel sign",
                    },
                    "human_approval_required": True,
                },
            )

        adapter = self.make_adapter(handler)

        result = adapter.create_project(
            idea="armadillo motel sign",
            working_title="Armadillo Motel",
            collection="Roadside",
        )

        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["path"], "/v1/workflows/creative")
        self.assertEqual(
            captured["body"],
            {
                "idea": "armadillo motel sign",
                "working_title": "Armadillo Motel",
                "collection": "Roadside",
            },
        )
        self.assertTrue(result["human_approval_required"])

    def test_create_project_omits_unspecified_optional_fields(self):
        captured = {}

        def handler(request):
            captured["body"] = json.loads(request.content)
            return httpx.Response(
                201,
                json={
                    "project": {
                        "project_id": "11111111-1111-1111-1111-111111111111",
                        "title": "Test",
                        "source_idea": "test idea",
                    },
                    "human_approval_required": True,
                },
            )

        adapter = self.make_adapter(handler)

        adapter.create_project(idea="test idea")

        self.assertEqual(captured["body"], {"idea": "test idea"})

    def test_get_project_uses_project_endpoint(self):
        captured = {}

        def handler(request):
            captured["method"] = request.method
            captured["path"] = request.url.path
            return httpx.Response(
                200,
                json={
                    "project_id": "11111111-1111-1111-1111-111111111111",
                    "title": "Armadillo Motel",
                    "source_idea": "armadillo motel sign",
                    "status": "story_ready",
                },
            )

        adapter = self.make_adapter(handler)

        result = adapter.get_project(
            "11111111-1111-1111-1111-111111111111"
        )

        self.assertEqual(captured["method"], "GET")
        self.assertEqual(
            captured["path"],
            "/v1/projects/11111111-1111-1111-1111-111111111111",
        )
        self.assertEqual(result["status"], "story_ready")

    def test_project_not_found_is_sanitized(self):
        def handler(request):
            return httpx.Response(
                404,
                json={"detail": "Creative project not found"},
            )

        adapter = self.make_adapter(handler)

        with self.assertRaisesRegex(
            RuntimeError,
            "HTTP 404: Creative project not found",
        ):
            adapter.get_project(
                "11111111-1111-1111-1111-111111111111"
            )


if __name__ == "__main__":
    unittest.main()
