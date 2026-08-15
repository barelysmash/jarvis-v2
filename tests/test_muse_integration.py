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

    def test_registers_seven_creative_tools(self):
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
                "muse_create_art_direction",
                "muse_generate_candidates",
                "muse_critique_project",
                "muse_request_revision",
                "muse_approve_artifact",
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

    def test_phase_two_bodyless_project_actions(self):
        calls = []

        def handler(request):
            calls.append(
                (
                    request.method,
                    request.url.path,
                    request.content,
                )
            )
            return httpx.Response(
                200,
                json={
                    "project": {
                        "project_id": (
                            "11111111-1111-1111-1111-111111111111"
                        ),
                        "title": "Test",
                        "source_idea": "test idea",
                    },
                    "human_approval_required": True,
                },
            )

        adapter = self.make_adapter(handler)
        project_id = "11111111-1111-1111-1111-111111111111"

        adapter.prepare_art_direction(project_id)
        adapter.generate_candidates(project_id)
        adapter.critique_project(project_id)
        adapter.request_revision(project_id)

        self.assertEqual(
            calls,
            [
                (
                    "POST",
                    (
                        "/v1/projects/"
                        "11111111-1111-1111-1111-111111111111/"
                        "art-direction"
                    ),
                    b"",
                ),
                (
                    "POST",
                    (
                        "/v1/projects/"
                        "11111111-1111-1111-1111-111111111111/"
                        "generate"
                    ),
                    b"",
                ),
                (
                    "POST",
                    (
                        "/v1/projects/"
                        "11111111-1111-1111-1111-111111111111/"
                        "critique"
                    ),
                    b"",
                ),
                (
                    "POST",
                    (
                        "/v1/projects/"
                        "11111111-1111-1111-1111-111111111111/"
                        "revision"
                    ),
                    b"",
                ),
            ],
        )

    def test_approve_artifact_posts_explicit_artifact_id(self):
        captured = {}

        def handler(request):
            captured["method"] = request.method
            captured["path"] = request.url.path
            captured["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "project": {
                        "project_id": (
                            "11111111-1111-1111-1111-111111111111"
                        ),
                        "title": "Test",
                        "source_idea": "test idea",
                        "approval": "approved",
                    },
                    "human_approval_required": True,
                },
            )

        adapter = self.make_adapter(handler)

        adapter.approve_artifact(
            project_id="11111111-1111-1111-1111-111111111111",
            artifact_id="22222222-2222-2222-2222-222222222222",
        )

        self.assertEqual(captured["method"], "POST")
        self.assertEqual(
            captured["path"],
            (
                "/v1/projects/"
                "11111111-1111-1111-1111-111111111111/approve"
            ),
        )
        self.assertEqual(
            captured["body"],
            {
                "artifact_id": (
                    "22222222-2222-2222-2222-222222222222"
                )
            },
        )

    def test_structured_critique_conflict_is_sanitized(self):
        def handler(request):
            return httpx.Response(
                409,
                json={
                    "detail": {
                        "code": "operational_constraint_failure",
                        "artifact_id": (
                            "22222222-2222-2222-2222-222222222222"
                        ),
                        "failures": [
                            {
                                "constraint": "transparent_background",
                                "expected": True,
                                "actual": False,
                                "evidence": {
                                    "alpha_channel": False,
                                },
                            },
                            {
                                "constraint": "mockup_absent",
                                "expected": True,
                                "actual": False,
                                "evidence": {
                                    "mockup_detected": True,
                                },
                            },
                        ],
                    }
                },
            )

        adapter = self.make_adapter(handler)

        with self.assertRaisesRegex(
            RuntimeError,
            (
                "HTTP 409: operational_constraint_failure "
                "artifact=22222222-2222-2222-2222-222222222222 "
                "constraints=transparent_background,mockup_absent"
            ),
        ):
            adapter.critique_project(
                "11111111-1111-1111-1111-111111111111"
            )

    def test_approval_tool_requires_project_and_artifact_ids(self):
        adapter = self.make_adapter(
            lambda request: httpx.Response(200, json={})
        )
        registry = RegistryStub()

        adapter.register(registry)

        approval = registry.tools["muse_approve_artifact"]

        self.assertEqual(
            approval["schema"]["required"],
            [
                "project_id",
                "artifact_id",
            ],
        )
        self.assertIn(
            "ONLY when the user has explicitly approved",
            approval["description"],
        )


if __name__ == "__main__":
    unittest.main()


class MuseArtifactReviewTests(unittest.TestCase):
    def make_adapter(self, handler):
        client = httpx.Client(
            transport=httpx.MockTransport(handler),
            timeout=5.0,
        )
        return MuseAdapter(
            base_url="http://muse.test",
            client=client,
        )

    def test_get_artifact_content_uses_muse_content_endpoint(self):
        captured = {}

        def handler(request):
            captured["method"] = request.method
            captured["path"] = request.url.path
            captured["accept"] = request.headers.get("accept")
            return httpx.Response(
                200,
                content=b"png-bytes",
                headers={"content-type": "image/png"},
            )

        adapter = self.make_adapter(handler)

        content, media_type = adapter.get_artifact_content(
            "11111111-1111-1111-1111-111111111111",
            "22222222-2222-2222-2222-222222222222",
        )

        self.assertEqual(captured["method"], "GET")
        self.assertEqual(
            captured["path"],
            (
                "/v1/projects/"
                "11111111-1111-1111-1111-111111111111/"
                "artifacts/"
                "22222222-2222-2222-2222-222222222222/"
                "content"
            ),
        )
        self.assertEqual(captured["accept"], "image/*")
        self.assertEqual(content, b"png-bytes")
        self.assertEqual(media_type, "image/png")

    def test_get_artifact_content_rejects_non_image_response(self):
        adapter = self.make_adapter(
            lambda request: httpx.Response(
                200,
                content=b"not-an-image",
                headers={"content-type": "text/plain"},
            )
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "unexpected artifact content type",
        ):
            adapter.get_artifact_content(
                "11111111-1111-1111-1111-111111111111",
                "22222222-2222-2222-2222-222222222222",
            )

    def test_review_widget_whitelists_safe_project_fields(self):
        from tools.integrations.muse import build_review_widget

        result = {
            "project": {
                "project_id": (
                    "11111111-1111-1111-1111-111111111111"
                ),
                "title": "Armadillo Motel",
                "status": "candidates_ready",
                "approval": "pending",
                "recommended_artifact_id": None,
                "approved_artifact_id": None,
                "artifacts": [
                    {
                        "artifact_id": (
                            "22222222-2222-2222-2222-222222222222"
                        ),
                        "candidate_index": 0,
                        "mime_type": "image/png",
                        "width": 1024,
                        "height": 1024,
                        "storage_uri": "file:///secret/muse/path.png",
                        "generation_metadata": {
                            "raw_sha256": "secret",
                        },
                    }
                ],
                "brief": {"private": "not-for-hud"},
                "story": {"private": "not-for-hud"},
            },
            "human_approval_required": True,
        }

        widget = build_review_widget(result)

        self.assertIsNotNone(widget)
        assert widget is not None

        self.assertEqual(
            set(widget),
            {
                "project_id",
                "title",
                "status",
                "approval",
                "recommended_artifact_id",
                "approved_artifact_id",
                "artifacts",
            },
        )

        artifact = widget["artifacts"][0]

        self.assertEqual(
            set(artifact),
            {
                "artifact_id",
                "candidate_index",
                "mime_type",
                "width",
                "height",
            },
        )
        self.assertNotIn("storage_uri", artifact)
        self.assertNotIn("generation_metadata", artifact)
