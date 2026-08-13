"""Muse creative intelligence tools for JARVIS v2.

Muse runs as an independent same-host service. This adapter communicates
with Muse exclusively through its versioned HTTP API; JARVIS never imports
Muse domain code or accesses Muse storage directly.
"""

from __future__ import annotations

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

        encoded_project_id = quote(project_id, safe="")
        return self._get(f"/v1/projects/{encoded_project_id}")

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
