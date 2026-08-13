"""Muse creative intelligence tools for JARVIS v2.

Muse runs as an independent same-host service. This adapter communicates
with Muse exclusively through its versioned HTTP API; JARVIS never imports
Muse domain code or accesses Muse storage directly.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any, Protocol, cast
from urllib.parse import quote

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8787"
DEFAULT_TIMEOUT = 45.0

JsonObject = dict[str, Any]


class ToolRegistryLike(Protocol):
    """Minimum registry interface required by the Muse adapter."""

    def register(
        self,
        name: str,
        description: str,
        schema: dict[str, Any],
        handler: Callable[..., object],
    ) -> None: ...


class MuseAdapter:
    """Client for the same-host Muse creative intelligence service."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = (
            base_url
            or os.environ.get("MUSE_BASE_URL")
            or DEFAULT_BASE_URL
        ).rstrip("/")

        configured_timeout = os.environ.get("MUSE_TIMEOUT_SECONDS")
        self.timeout = (
            timeout
            if timeout is not None
            else float(configured_timeout or DEFAULT_TIMEOUT)
        )

        self.client = client or httpx.Client(timeout=self.timeout)
        self._owns_client = client is None

    def close(self) -> None:
        """Close the internally owned HTTP client."""

        if self._owns_client:
            self.client.close()

    @staticmethod
    def _decode_object(response: httpx.Response) -> JsonObject:
        try:
            body = response.json()
        except ValueError:
            raise RuntimeError(
                "Muse returned an invalid JSON response."
            ) from None

        if not isinstance(body, dict):
            raise RuntimeError(
                "Muse returned an unexpected JSON response."
            )

        return cast(JsonObject, body)

    @staticmethod
    def _error_message(response: httpx.Response) -> str:
        fallback = "Muse rejected the request."

        try:
            body = response.json()
        except ValueError:
            return fallback

        if not isinstance(body, dict):
            return fallback

        detail = body.get("detail")

        if isinstance(detail, str):
            return detail

        if isinstance(detail, list):
            messages: list[str] = []

            for item in detail:
                if not isinstance(item, dict):
                    continue

                message = item.get("msg")
                if isinstance(message, str):
                    messages.append(message)

            if messages:
                return "; ".join(messages)

        if isinstance(detail, dict):
            code = detail.get("code")
            artifact_id = detail.get("artifact_id")
            failures = detail.get("failures")

            if code == "operational_constraint_failure":
                constraints: list[str] = []

                if isinstance(failures, list):
                    for item in failures:
                        if not isinstance(item, dict):
                            continue

                        constraint = item.get("constraint")
                        if isinstance(constraint, str):
                            constraints.append(constraint)

                parts = ["operational_constraint_failure"]

                if isinstance(artifact_id, str):
                    parts.append(f"artifact={artifact_id}")

                if constraints:
                    parts.append(
                        "constraints=" + ",".join(constraints)
                    )

                return " ".join(parts)

            return json.dumps(
                detail,
                sort_keys=True,
                separators=(",", ":"),
            )

        return fallback

    def _post(self, path: str, payload: JsonObject) -> JsonObject:
        try:
            response = self.client.post(
                f"{self.base_url}{path}",
                headers={"Accept": "application/json"},
                json=payload,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            message = self._error_message(exc.response)
            raise RuntimeError(
                f"Muse request failed: HTTP {status}: {message}"
            ) from None
        except httpx.RequestError as exc:
            raise RuntimeError(
                f"Muse production is unreachable: {exc}"
            ) from None

        return self._decode_object(response)

    def _post_without_body(self, path: str) -> JsonObject:
        try:
            response = self.client.post(
                f"{self.base_url}{path}",
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            message = self._error_message(exc.response)
            raise RuntimeError(
                f"Muse request failed: HTTP {status}: {message}"
            ) from None
        except httpx.RequestError as exc:
            raise RuntimeError(
                f"Muse production is unreachable: {exc}"
            ) from None

        return self._decode_object(response)

    def _get(self, path: str) -> JsonObject:
        try:
            response = self.client.get(
                f"{self.base_url}{path}",
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            message = self._error_message(exc.response)
            raise RuntimeError(
                f"Muse request failed: HTTP {status}: {message}"
            ) from None
        except httpx.RequestError as exc:
            raise RuntimeError(
                f"Muse production is unreachable: {exc}"
            ) from None

        return self._decode_object(response)

    def create_project(
        self,
        idea: str,
        working_title: str | None = None,
        brand: str | None = None,
        collection: str | None = None,
    ) -> JsonObject:
        """Create a persisted Muse creative workflow from one raw idea."""

        payload: JsonObject = {"idea": idea}

        if working_title is not None:
            payload["working_title"] = working_title
        if brand is not None:
            payload["brand"] = brand
        if collection is not None:
            payload["collection"] = collection

        return self._post("/v1/workflows/creative", payload)

    def get_project(self, project_id: str) -> JsonObject:
        """Retrieve one persisted Muse creative project."""

        project = self._encoded_project_id(project_id)
        return self._get(f"/v1/projects/{project}")

    @staticmethod
    def _encoded_project_id(project_id: str) -> str:
        return quote(project_id, safe="")

    def prepare_art_direction(self, project_id: str) -> JsonObject:
        """Prepare art direction and a generation request."""

        project = self._encoded_project_id(project_id)
        return self._post_without_body(
            f"/v1/projects/{project}/art-direction"
        )

    def generate_candidates(self, project_id: str) -> JsonObject:
        """Generate creative candidates for a prepared project."""

        project = self._encoded_project_id(project_id)
        return self._post_without_body(
            f"/v1/projects/{project}/generate"
        )

    def critique_project(self, project_id: str) -> JsonObject:
        """Critique current candidates and determine the next action."""

        project = self._encoded_project_id(project_id)
        return self._post_without_body(
            f"/v1/projects/{project}/critique"
        )

    def request_revision(self, project_id: str) -> JsonObject:
        """Prepare the project's requested revision."""

        project = self._encoded_project_id(project_id)
        return self._post_without_body(
            f"/v1/projects/{project}/revision"
        )

    def approve_artifact(
        self,
        project_id: str,
        artifact_id: str,
    ) -> JsonObject:
        """Record explicit human approval of one Muse artifact."""

        project = self._encoded_project_id(project_id)
        return self._post(
            f"/v1/projects/{project}/approve",
            {"artifact_id": artifact_id},
        )

    def register(self, tools: ToolRegistryLike) -> None:
        """Register the intentionally small JARVIS-facing Muse surface."""

        def create_handler(
            idea: str,
            working_title: str | None = None,
            brand: str | None = None,
            collection: str | None = None,
        ) -> JsonObject:
            return self.create_project(
                idea=idea,
                working_title=working_title,
                brand=brand,
                collection=collection,
            )

        def get_handler(project_id: str) -> JsonObject:
            return self.get_project(project_id)

        def art_direction_handler(project_id: str) -> JsonObject:
            return self.prepare_art_direction(project_id)

        def generate_handler(project_id: str) -> JsonObject:
            return self.generate_candidates(project_id)

        def critique_handler(project_id: str) -> JsonObject:
            return self.critique_project(project_id)

        def revision_handler(project_id: str) -> JsonObject:
            return self.request_revision(project_id)

        def approve_handler(
            project_id: str,
            artifact_id: str,
        ) -> JsonObject:
            return self.approve_artifact(
                project_id=project_id,
                artifact_id=artifact_id,
            )

        tools.register(
            name="muse_create_project",
            description=(
                "Turn a raw creative idea into a persisted Muse creative "
                "project. Muse develops the brief and story while retaining "
                "its explicit human-approval requirement. Use for apparel "
                "graphic concepts and other Muse creative work."
            ),
            schema={
                "type": "object",
                "properties": {
                    "idea": {
                        "type": "string",
                        "minLength": 1,
                        "description": "The raw creative idea to develop.",
                    },
                    "working_title": {
                        "type": "string",
                        "minLength": 1,
                    },
                    "brand": {
                        "type": "string",
                        "minLength": 1,
                        "description": (
                            "Optional brand override. Omit to let Muse use "
                            "its configured default."
                        ),
                    },
                    "collection": {
                        "type": "string",
                        "minLength": 1,
                    },
                },
                "required": ["idea"],
                "additionalProperties": False,
            },
            handler=create_handler,
        )

        tools.register(
            name="muse_get_project",
            description=(
                "Retrieve a previously created Muse creative project by "
                "project ID, including its current workflow status, creative "
                "development, artifacts, critiques, and approval state."
            ),
            schema={
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "format": "uuid",
                    },
                },
                "required": ["project_id"],
                "additionalProperties": False,
            },
            handler=get_handler,
        )

        project_id_schema = {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "format": "uuid",
                },
            },
            "required": ["project_id"],
            "additionalProperties": False,
        }

        tools.register(
            name="muse_create_art_direction",
            description=(
                "Prepare provider-neutral art direction and a generation "
                "request for a Muse project. This advances creative planning "
                "but does not generate candidates or approve anything."
            ),
            schema=project_id_schema,
            handler=art_direction_handler,
        )

        tools.register(
            name="muse_generate_candidates",
            description=(
                "Generate candidate artifacts for an art-direction-ready "
                "Muse project. Generation never constitutes human approval."
            ),
            schema=project_id_schema,
            handler=generate_handler,
        )

        tools.register(
            name="muse_critique_project",
            description=(
                "Critique the current candidate artifacts for a Muse project, "
                "including deterministic operational constraints, and return "
                "Muse's recommendation or required next action. Critique does "
                "not approve an artifact."
            ),
            schema=project_id_schema,
            handler=critique_handler,
        )

        tools.register(
            name="muse_request_revision",
            description=(
                "Prepare a Muse project for another generation round after "
                "revision has been requested. This does not approve anything."
            ),
            schema=project_id_schema,
            handler=revision_handler,
        )

        tools.register(
            name="muse_approve_artifact",
            description=(
                "Record explicit human approval of one specific Muse artifact. "
                "Call this ONLY when the user has explicitly approved the "
                "specific artifact_id. Never infer approval from a Muse "
                "recommendation, critique, generation request, or request to "
                "continue the workflow."
            ),
            schema={
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "format": "uuid",
                    },
                    "artifact_id": {
                        "type": "string",
                        "format": "uuid",
                    },
                },
                "required": [
                    "project_id",
                    "artifact_id",
                ],
                "additionalProperties": False,
            },
            handler=approve_handler,
        )
