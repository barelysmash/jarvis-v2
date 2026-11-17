"""Taxdesk tools for JARVIS v2: Texas sales tax and mixed-beverage receipts.

Taxdesk already runs on guildenstern (port 8770) and serves a dashboard.
This adapter lets the brain ask it the same questions by voice.

**Public data only.** Everything exposed here comes from the Texas
Comptroller's open datasets: sales tax allocations and TABC mixed-beverage
gross receipts, which the state publishes per venue. Taxdesk also holds
private restaurant data (the product mix and nightly operations loaded from
internal reports, under ``/api/mix/``). This adapter never calls those
routes: private operating figures reach JARVIS only through Atlas, which
returns relative figures.

The API is read-only and has no authentication; it is reachable on the
tailnet only. Transport and trimming live here, no analysis.
"""

from __future__ import annotations

import contextlib
import os
import time
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from orchestrator.tools import ToolRegistry

DEFAULT_TIMEOUT = 20.0
DEFAULT_MONTHS = 13
WATCHLIST_TTL_SECONDS = 600
MAX_MONTHS = 60

# The only routes this adapter may call. A test pins the list.
PUBLIC_ROUTES = (
    "/api/health",
    "/api/sales-tax/city",
    "/api/sales-tax/county",
    "/api/sales-tax/statewide",
    "/api/mb/watchlist",
    "/api/mb/venue/",
    "/api/mb/austin/top",
    "/api/mb/beta",
)

ARREARS_NOTE = (
    "Mixed-beverage receipts are filed in arrears: the newest month is "
    "usually incomplete and should not be read as a drop."
)


class TaxdeskError(RuntimeError):
    """Taxdesk refused or could not answer. The message is for the user."""


def _pct(now: float | None, before: float | None) -> float | None:
    if now is None or not before:
        return None
    return round((now / before - 1) * 100, 1)


def _months(value: int) -> int:
    return max(1, min(MAX_MONTHS, int(value)))


class TaxdeskAdapter:
    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = (base_url or os.environ.get("TAXDESK_BASE_URL") or "").rstrip(
            "/"
        )
        if not self.base_url:
            raise ValueError("TAXDESK_BASE_URL is not configured")
        self.client = client or httpx.Client(timeout=timeout or DEFAULT_TIMEOUT)
        # The watchlist route returns every venue's full history, so the
        # names are kept for a few minutes instead of refetched per lookup.
        self._watchlist: tuple[float, list[dict[str, Any]]] | None = None

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if not path.startswith(PUBLIC_ROUTES):
            # Belt and braces: a private route must be unreachable from here
            # even if a later edit asks for one.
            raise TaxdeskError(f"{path} is not a public Taxdesk route.")
        try:
            response = self.client.get(f"{self.base_url}{path}", params=params)
        except httpx.RequestError as exc:
            raise TaxdeskError(f"Taxdesk is unreachable: {exc}") from None
        if response.status_code == 404:
            raise TaxdeskError("Taxdesk has no record of that.")
        if response.status_code >= 400:
            detail = ""
            with contextlib.suppress(ValueError):
                detail = str(response.json().get("detail") or "")
            raise TaxdeskError(
                f"Taxdesk request failed: HTTP {response.status_code}"
                + (f", {detail}" if detail else "")
            )
        return response.json()

    # -- tools --

    def status(self) -> dict[str, Any]:
        health = self._get("/api/health")
        receipts = health.get("latest_mb_obligation") or {}
        # The newest sales tax period is read from the series itself:
        # /api/health picks its row by fetch time, which is not the newest
        # period when a scrape refreshes older months.
        series = self._get("/api/sales-tax/city", {"city": "AUSTIN", "years": 1}).get(
            "series"
        ) or []
        latest = series[-1] if series else {}
        return {
            "ok": bool(health.get("ok")),
            "latest_sales_tax_period": (
                f"{latest['period_year']}-{int(latest['period_month']):02d}"
                if latest.get("period_year") and latest.get("period_month")
                else None
            ),
            "latest_mixed_beverage_month": str(receipts.get("d") or "")[:7] or None,
            "mixed_beverage_fetched_at": receipts.get("fetched_at"),
            "note": ARREARS_NOTE,
        }

    def sales_tax(
        self,
        scope: str = "austin",
        entity: str | None = None,
        months: int = DEFAULT_MONTHS,
    ) -> dict[str, Any]:
        months = _months(months)
        years = max(2, -(-months // 12) + 1)
        if scope == "austin":
            data = self._get(
                "/api/sales-tax/city", {"city": entity or "AUSTIN", "years": years}
            )
            return {
                "scope": "city",
                "city": data.get("city"),
                "source": "Texas Comptroller sales tax allocations",
                "series": (data.get("series") or [])[-months:],
            }
        if scope == "statewide":
            data = self._get("/api/sales-tax/statewide", {"years": years})
            return {
                "scope": "statewide",
                "source": "Texas Comptroller monthly news releases",
                "series": (data.get("series") or [])[-months:],
            }
        if scope == "county":
            params: dict[str, Any] = {"years": years}
            if entity:
                params["entity_like"] = entity
            data = self._get("/api/sales-tax/county", params)
            return {
                "scope": "county and special districts",
                "source": "Texas Comptroller sales tax allocations",
                "note": (
                    "Travis County itself levies no sales tax. These are "
                    "CapMetro and the emergency service and utility districts."
                ),
                **{key: value for key, value in data.items() if key != "note"},
            }
        raise ValueError("scope must be austin, statewide or county")

    def watchlist(self) -> dict[str, Any]:
        now = time.monotonic()
        if self._watchlist is None or now - self._watchlist[0] > WATCHLIST_TTL_SECONDS:
            data = self._get("/api/mb/watchlist")
            venues = [
                {
                    "slug": venue.get("slug"),
                    "name": venue.get("display_name"),
                    "group": venue.get("bucket"),
                    "months_on_record": len(venue.get("series") or []),
                }
                for venue in data.get("venues") or []
            ]
            self._watchlist = (now, venues)
        return {"venues": [dict(venue) for venue in self._watchlist[1]]}

    def _resolve(self, wanted: str) -> str:
        """Watchlist slug for a spoken venue name: exact, then a unique partial."""
        needle = str(wanted).strip().lower()
        if not needle:
            raise TaxdeskError("Say which venue.")
        venues = self.watchlist()["venues"]
        names = ", ".join(str(venue["name"]) for venue in venues) or "none"

        def keys(venue: dict[str, Any]) -> tuple[str, str]:
            return str(venue["slug"]).lower(), str(venue["name"]).lower()

        exact = [venue for venue in venues if needle in keys(venue)]
        partial = [
            venue for venue in venues if any(needle in key for key in keys(venue))
        ]
        matches = exact or partial
        if len(matches) == 1:
            return str(matches[0]["slug"])
        if not matches:
            raise TaxdeskError(
                f"'{wanted}' is not on the Taxdesk watchlist. On it: {names}. "
                "For any other Austin venue, use taxdesk_top_venues."
            )
        raise TaxdeskError(
            f"'{wanted}' matches more than one watchlist venue: "
            f"{', '.join(str(venue['name']) for venue in matches)}. Ask which."
        )

    def venue(self, venue: str, months: int = DEFAULT_MONTHS) -> dict[str, Any]:
        months = _months(months)
        data = self._get(f"/api/mb/venue/{self._resolve(venue)}")
        monthly = data.get("monthly") or []
        totals = [float(row.get("total") or 0) for row in monthly]

        latest = monthly[-1] if monthly else None
        year_ago = monthly[-13] if len(monthly) >= 13 else None
        return {
            "venue": (data.get("venue") or {}).get("display_name"),
            "source": "TABC mixed-beverage gross receipts (public record)",
            "locations": [
                {
                    "name": location.get("location_name"),
                    "address": location.get("location_address"),
                }
                for location in data.get("locations") or []
            ],
            "latest_month": str((latest or {}).get("obligation_end_date") or "")[:7]
            or None,
            "latest_vs_same_month_last_year_pct": _pct(
                (latest or {}).get("total"), (year_ago or {}).get("total")
            ),
            "trailing_12_months_total": round(sum(totals[-12:]), 2)
            if totals
            else None,
            "trailing_12_vs_prior_12_pct": _pct(
                sum(totals[-12:]), sum(totals[-24:-12])
            )
            if len(totals) >= 24
            else None,
            "monthly": [
                {
                    "month": str(row.get("obligation_end_date") or "")[:7],
                    "total": row.get("total"),
                    "liquor": row.get("liquor"),
                    "wine": row.get("wine"),
                    "beer": row.get("beer"),
                }
                for row in monthly[-months:]
            ],
            "note": ARREARS_NOTE,
        }

    def top_venues(self, n: int = 10, months: int = 12) -> dict[str, Any]:
        n = max(1, min(50, int(n)))
        months = _months(months)
        data = self._get("/api/mb/austin/top", {"n": n, "months": months})
        return {
            "source": "TABC mixed-beverage gross receipts (public record)",
            "trailing_months": data.get("trailing_months"),
            "venues": [
                {
                    "rank": rank,
                    "name": row.get("location_name"),
                    "address": row.get("address"),
                    "total": round(float(row.get("total") or 0), 2),
                }
                for rank, row in enumerate(data.get("top") or [], 1)
            ],
            "note": ARREARS_NOTE,
        }

    def market(self, months: int = 36) -> dict[str, Any]:
        months = max(12, min(120, int(months)))
        data = self._get("/api/mb/beta", {"months": months})
        market = data.get("market") or {}
        return {
            "source": "TABC mixed-beverage gross receipts (public record)",
            "window_months": data.get("window_months"),
            "austin_market": {
                "trailing_12_months_total": market.get("ttm_total"),
                "vs_prior_12_months_pct": _pct(
                    market.get("ttm_total"), market.get("prior_ttm_total")
                ),
                "monthly_volatility": market.get("volatility"),
            },
            "venues": [
                {
                    "name": venue.get("display_name"),
                    "group": venue.get("bucket"),
                    "beta": venue.get("beta"),
                    "correlation": venue.get("corr"),
                    "monthly_volatility": venue.get("volatility"),
                    "months_used": venue.get("n_months"),
                    "trailing_12_months_total": venue.get("ttm_total"),
                }
                for venue in data.get("venues") or []
            ],
            "note": data.get("note"),
        }

    def register(self, tools: ToolRegistry) -> None:
        public = (
            " Public Texas Comptroller data; dollar figures here may be quoted."
        )
        tools.register(
            name="taxdesk_sales_tax",
            description=(
                "Monthly sales tax allocations: scope 'austin' (the city; or "
                "another Texas city via entity), 'statewide', or 'county' "
                "(CapMetro and Travis districts; entity filters by name). "
                "Returns the trailing months with year-over-year change."
                + public
            ),
            schema={
                "type": "object",
                "properties": {
                    "scope": {
                        "type": "string",
                        "enum": ["austin", "statewide", "county"],
                    },
                    "entity": {"type": "string"},
                    "months": {"type": "integer", "minimum": 1, "maximum": 60},
                },
                "additionalProperties": False,
            },
            handler=lambda scope="austin", entity=None, months=DEFAULT_MONTHS: (
                self.sales_tax(scope, entity, months)
            ),
        )
        tools.register(
            name="taxdesk_venue",
            description=(
                "Mixed-beverage (liquor, wine, beer) gross receipts for one "
                "venue on the Taxdesk watchlist, by month, with change against "
                "the same month last year and trailing twelve months. Name "
                "the venue as the user said it." + public
            ),
            schema={
                "type": "object",
                "properties": {
                    "venue": {"type": "string"},
                    "months": {"type": "integer", "minimum": 1, "maximum": 60},
                },
                "required": ["venue"],
                "additionalProperties": False,
            },
            handler=lambda venue, months=DEFAULT_MONTHS: self.venue(venue, months),
        )
        tools.register(
            name="taxdesk_watchlist",
            description=(
                "The venues Taxdesk tracks individually, with their group "
                "(home, mexican, cocktail, alumni). Use to see which names "
                "taxdesk_venue accepts."
            ),
            schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            handler=lambda: self.watchlist(),
        )
        tools.register(
            name="taxdesk_top_venues",
            description=(
                "Austin venues ranked by mixed-beverage gross receipts over "
                "the trailing months (default 12), top n (default 10)." + public
            ),
            schema={
                "type": "object",
                "properties": {
                    "n": {"type": "integer", "minimum": 1, "maximum": 50},
                    "months": {"type": "integer", "minimum": 1, "maximum": 60},
                },
                "additionalProperties": False,
            },
            handler=lambda n=10, months=12: self.top_venues(n, months),
        )
        tools.register(
            name="taxdesk_market",
            description=(
                "How the Austin mixed-beverage market is moving, and each "
                "watchlist venue's sensitivity to it. A beta below 1 means "
                "the venue moves less than the market." + public
            ),
            schema={
                "type": "object",
                "properties": {
                    "months": {"type": "integer", "minimum": 12, "maximum": 120},
                },
                "additionalProperties": False,
            },
            handler=lambda months=36: self.market(months),
        )
        tools.register(
            name="taxdesk_status",
            description=(
                "How current Taxdesk's data is: the latest sales tax period "
                "and mixed-beverage month on record, and when each was fetched."
            ),
            schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            handler=lambda: self.status(),
        )
