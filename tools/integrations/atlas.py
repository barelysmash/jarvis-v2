"""Atlas operational-intelligence tools for JARVIS v2.

Atlas's RestaurantOS peer API is read-only and reports relative figures only:
percent changes, percentage-point deltas, directions and night counts. It
never returns an absolute dollar amount or cover count, and that limit is
enforced in Atlas, not here. This adapter is transport only — it holds the
base URL and token, makes the HTTP call and returns the payload unchanged.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from orchestrator.tools import ToolRegistry

DEFAULT_BASE_URL = "http://127.0.0.1:8790"
DEFAULT_TIMEOUT = 20.0

_DATE = {"type": "string", "description": "YYYY-MM-DD."}


class AtlasAdapter:
    """Authenticated client for the same-host Atlas RestaurantOS service."""

    def __init__(
        self,
        base_url: str | None = None,
        api_token: str | None = None,
        timeout: float | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = (
            base_url
            or os.environ.get("ATLAS_BASE_URL")
            or DEFAULT_BASE_URL
        ).rstrip("/")

        self.api_token = (
            api_token
            if api_token is not None
            else os.environ.get("ATLAS_API_TOKEN", "")
        )

        configured_timeout = os.environ.get("ATLAS_TIMEOUT_SECONDS")
        self.timeout = (
            timeout
            if timeout is not None
            else float(configured_timeout or DEFAULT_TIMEOUT)
        )

        self.client = client or httpx.Client(timeout=self.timeout)
        self._owns_client = client is None

        if not self.api_token:
            raise ValueError("ATLAS_API_TOKEN is not configured")

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> Any:
        try:
            response = self.client.request(
                method,
                f"{self.base_url}{path}",
                headers={
                    "Authorization": f"Bearer {self.api_token}",
                    "Accept": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            code = "http_error"
            message = "Atlas rejected the request."

            try:
                error = exc.response.json().get("error", {})
                code = error.get("code", code)
                message = error.get("message", message)
            except Exception:
                pass

            raise RuntimeError(
                f"Atlas request failed: HTTP {status}, {code}: {message}"
            ) from None
        except httpx.RequestError as exc:
            raise RuntimeError(f"Atlas is unreachable: {exc}") from None

        body = response.json()

        if isinstance(body, dict) and "data" in body:
            return {
                "request_id": body.get("request_id"),
                "data": body["data"],
            }

        return body

    @staticmethod
    def _without_none(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in payload.items()
            if value is not None
        }

    def operating_summary(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        compare_start_date: str | None = None,
        compare_end_date: str | None = None,
        entity: str | None = None,
    ) -> Any:
        return self._request(
            "POST",
            "/v1/operating-summary",
            self._without_none(
                {
                    "start_date": start_date,
                    "end_date": end_date,
                    "compare_start_date": compare_start_date,
                    "compare_end_date": compare_end_date,
                    "entity": entity,
                }
            ),
        )

    def freshness(self) -> Any:
        return self._request("GET", "/v1/freshness")

    def register(self, tools: ToolRegistry) -> None:
        tools.register(
            name="atlas_operating_summary",
            description=(
                "Ask Atlas how the restaurant's operations moved between two "
                "periods: net sales, covers, labor, average check, SPLH, "
                "reservation share, comps and voids. Atlas returns RELATIVE "
                "figures only — percent changes, percentage-point deltas and "
                "directions — never dollar amounts or cover counts. With no "
                "dates it compares month to date against the same days of the "
                "prior month. With start_date and end_date only, it compares "
                "against the equal-length window just before. Read-only."
            ),
            schema={
                "type": "object",
                "properties": {
                    "start_date": _DATE,
                    "end_date": _DATE,
                    "compare_start_date": _DATE,
                    "compare_end_date": _DATE,
                    "entity": {
                        "type": "string",
                        "description": (
                            "Restaurant name. Omit unless Atlas asks for it."
                        ),
                    },
                },
                "additionalProperties": False,
            },
            handler=lambda start_date=None, end_date=None,
                           compare_start_date=None, compare_end_date=None,
                           entity=None: (
                self.operating_summary(
                    start_date=start_date,
                    end_date=end_date,
                    compare_start_date=compare_start_date,
                    compare_end_date=compare_end_date,
                    entity=entity,
                )
            ),
        )

        tools.register(
            name="atlas_freshness",
            description=(
                "Ask Atlas how current its restaurant ledger is: the first and "
                "last service night on record, days since the last one, and "
                "how many nightly reports are waiting on review. Use before "
                "relying on a summary, or when asked whether last night's "
                "numbers are in. Dates and counts only."
            ),
            schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            handler=lambda: self.freshness(),
        )
