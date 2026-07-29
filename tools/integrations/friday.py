"""Friday production analysis tools for JARVIS v2.

Friday is analysis-only. This adapter exposes forecasts and scans but no
brokerage execution, order placement, or position mutation.
"""

from __future__ import annotations

import os
from typing import Any

import httpx


DEFAULT_BASE_URL = "http://127.0.0.1:8082"
DEFAULT_TIMEOUT = 45.0


class FridayAdapter:
    """Authenticated client for the same-host Friday production service."""

    def __init__(
        self,
        base_url: str | None = None,
        api_token: str | None = None,
        timeout: float | None = None,
        client: httpx.Client | None = None,
    ):
        self.base_url = (
            base_url
            or os.environ.get("FRIDAY_BASE_URL")
            or DEFAULT_BASE_URL
        ).rstrip("/")

        self.api_token = (
            api_token
            if api_token is not None
            else os.environ.get("FRIDAY_API_TOKEN", "")
        )

        configured_timeout = os.environ.get("FRIDAY_TIMEOUT_SECONDS")
        self.timeout = (
            timeout
            if timeout is not None
            else float(configured_timeout or DEFAULT_TIMEOUT)
        )

        self.client = client or httpx.Client(timeout=self.timeout)
        self._owns_client = client is None

        if not self.api_token:
            raise ValueError("FRIDAY_API_TOKEN is not configured")

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def _post(self, path: str, payload: dict[str, Any]) -> Any:
        try:
            response = self.client.post(
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
            message = "Friday rejected the request."

            try:
                body = exc.response.json()
                error = body.get("error", {})
                code = error.get("code", code)
                message = error.get("message", message)
            except Exception:
                pass

            raise RuntimeError(
                f"Friday request failed: HTTP {status}, {code}: {message}"
            ) from None
        except httpx.RequestError as exc:
            raise RuntimeError(
                f"Friday production is unreachable: {exc}"
            ) from None

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

    def forecast(
        self,
        candidates: list[dict[str, Any]],
        include_rejected: bool = False,
        limit: int = 10,
    ) -> Any:
        return self._post(
            "/v1/forecast",
            {
                "candidates": candidates,
                "include_rejected": include_rejected,
                "limit": limit,
            },
        )

    def live_scan(
        self,
        targets: list[dict[str, str]],
        workers: int = 2,
        top: int | None = None,
        report_directory: str | None = None,
    ) -> Any:
        return self._post(
            "/v1/live-scan",
            self._without_none(
                {
                    "targets": targets,
                    "workers": workers,
                    "top": top,
                    "report_directory": report_directory,
                }
            ),
        )

    def live_scan_context(
        self,
        live_scan: dict[str, Any],
        report_root: str,
        history_last: int = 10,
        history_from: str | None = None,
        history_to: str | None = None,
    ) -> Any:
        return self._post(
            "/v1/live-scan-context",
            self._without_none(
                {
                    "live_scan": live_scan,
                    "report_root": report_root,
                    "history_last": history_last,
                    "history_from": history_from,
                    "history_to": history_to,
                }
            ),
        )

    def context_trend_watch(
        self,
        report_root: str,
        from_date: str | None = None,
        to_date: str | None = None,
        last: int = 20,
        direction: str | None = None,
        trend: str | None = None,
        classification: str | None = None,
        limit: int = 10,
    ) -> Any:
        return self._post(
            "/v1/context-trend-watch",
            self._without_none(
                {
                    "report_root": report_root,
                    "from_date": from_date,
                    "to_date": to_date,
                    "last": last,
                    "direction": direction,
                    "trend": trend,
                    "classification": classification,
                    "limit": limit,
                }
            ),
        )

    def register(self, tools) -> None:
        tools.register(
            name="friday_live_scan",
            description=(
                "Run a live Friday market-data scan for one or more tickers. "
                "Friday provides analysis and trade-plan intelligence only; it "
                "cannot place orders. Each target requires ticker and direction, "
                "where direction is 'long' or 'short'."
            ),
            schema={
                "type": "object",
                "properties": {
                    "targets": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "ticker": {"type": "string"},
                                "direction": {
                                    "type": "string",
                                    "enum": ["long", "short"],
                                },
                            },
                            "required": ["ticker", "direction"],
                            "additionalProperties": False,
                        },
                    },
                    "workers": {
                        "type": "integer",
                        "minimum": 1,
                        "default": 2,
                    },
                    "top": {
                        "type": "integer",
                        "minimum": 1,
                    },
                    "report_directory": {"type": "string"},
                },
                "required": ["targets"],
                "additionalProperties": False,
            },
            handler=lambda targets, workers=2, top=None, report_directory=None: (
                self.live_scan(
                    targets=targets,
                    workers=workers,
                    top=top,
                    report_directory=report_directory,
                )
            ),
        )

        tools.register(
            name="friday_live_scan_context",
            description=(
                "Run a Friday live scan and compare it with historical Friday "
                "reports. Use when the user asks whether a setup is strengthening, "
                "weakening, recurring, or changing over time."
            ),
            schema={
                "type": "object",
                "properties": {
                    "live_scan": {
                        "type": "object",
                        "description": (
                            "Friday live-scan request, normally containing targets."
                        ),
                    },
                    "report_root": {"type": "string"},
                    "history_last": {
                        "type": "integer",
                        "minimum": 1,
                        "default": 10,
                    },
                    "history_from": {
                        "type": "string",
                        "description": "Optional YYYY-MM-DD date.",
                    },
                    "history_to": {
                        "type": "string",
                        "description": "Optional YYYY-MM-DD date.",
                    },
                },
                "required": ["live_scan", "report_root"],
                "additionalProperties": False,
            },
            handler=lambda live_scan, report_root, history_last=10,
                           history_from=None, history_to=None: (
                self.live_scan_context(
                    live_scan=live_scan,
                    report_root=report_root,
                    history_last=history_last,
                    history_from=history_from,
                    history_to=history_to,
                )
            ),
        )

        tools.register(
            name="friday_context_trend_watch",
            description=(
                "Read and filter historical Friday scan reports by date, "
                "direction, trend, or classification. Use for questions about "
                "how Friday's view has changed across recent reports."
            ),
            schema={
                "type": "object",
                "properties": {
                    "report_root": {"type": "string"},
                    "from_date": {
                        "type": "string",
                        "description": "Optional YYYY-MM-DD date.",
                    },
                    "to_date": {
                        "type": "string",
                        "description": "Optional YYYY-MM-DD date.",
                    },
                    "last": {
                        "type": "integer",
                        "minimum": 1,
                        "default": 20,
                    },
                    "direction": {
                        "type": "string",
                        "enum": ["long", "short"],
                    },
                    "trend": {"type": "string"},
                    "classification": {"type": "string"},
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "default": 10,
                    },
                },
                "required": ["report_root"],
                "additionalProperties": False,
            },
            handler=lambda report_root, from_date=None, to_date=None, last=20,
                           direction=None, trend=None, classification=None,
                           limit=10: (
                self.context_trend_watch(
                    report_root=report_root,
                    from_date=from_date,
                    to_date=to_date,
                    last=last,
                    direction=direction,
                    trend=trend,
                    classification=classification,
                    limit=limit,
                )
            ),
        )

        tools.register(
            name="friday_forecast",
            description=(
                "Rank already-prepared Friday forecast candidates. This is for "
                "structured candidate data; use friday_live_scan when starting "
                "from ticker symbols."
            ),
            schema={
                "type": "object",
                "properties": {
                    "candidates": {
                        "type": "array",
                        "items": {"type": "object"},
                    },
                    "include_rejected": {
                        "type": "boolean",
                        "default": False,
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "default": 10,
                    },
                },
                "required": ["candidates"],
                "additionalProperties": False,
            },
            handler=lambda candidates, include_rejected=False, limit=10: (
                self.forecast(
                    candidates=candidates,
                    include_rejected=include_rejected,
                    limit=limit,
                )
            ),
        )
