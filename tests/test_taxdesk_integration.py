"""Taxdesk adapter: what it asks Taxdesk for, and what it must never ask for."""

from __future__ import annotations

import unittest
from typing import Any

import httpx
import yaml

from orchestrator.tools import ToolRegistry
from tools.integrations.taxdesk import PUBLIC_ROUTES, TaxdeskAdapter, TaxdeskError


def month_rows(count: int, start_total: float = 1000.0) -> list[dict[str, Any]]:
    rows = []
    year, month = 2024, 1
    for index in range(count):
        total = start_total + index * 10
        rows.append(
            {
                "obligation_end_date": f"{year}-{month:02d}-28",
                "liquor": total * 0.7,
                "wine": total * 0.2,
                "beer": total * 0.1,
                "total": total,
            }
        )
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return rows


WATCHLIST = {
    "venues": [
        {
            "slug": "fonda_san_miguel",
            "display_name": "Fonda San Miguel",
            "bucket": "home",
            "series": [1, 2, 3],
        },
        {"slug": "suerte", "display_name": "Suerte", "bucket": "mexican", "series": []},
        {
            "slug": "de_nada",
            "display_name": "De Nada Cantina",
            "bucket": "mexican",
            "series": [],
        },
    ]
}


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.requests: list[httpx.Request] = []
        self.replies: dict[str, httpx.Response] = {
            "/api/health": httpx.Response(
                200,
                json={
                    "ok": True,
                    "latest_city_period": {"period_year": 2024, "period_month": 6},
                    "latest_mb_obligation": {
                        "d": "2026-10-31",
                        "fetched_at": "2026-11-16 06:00:01",
                    },
                },
            ),
            "/api/sales-tax/city": httpx.Response(
                200,
                json={
                    "city": "AUSTIN",
                    "series": [
                        {
                            "period_year": 2026,
                            "period_month": m,
                            "net_payment": 1.0 * m,
                            "pct_change": 2.0,
                        }
                        for m in range(1, 11)
                    ],
                },
            ),
            "/api/sales-tax/statewide": httpx.Response(
                200, json={"series": [{"period_year": 2026, "period_month": 10}]}
            ),
            "/api/sales-tax/county": httpx.Response(
                200, json={"entities": [{"entity_name": "Austin MTA"}], "series": []}
            ),
            "/api/mb/watchlist": httpx.Response(200, json=WATCHLIST),
            "/api/mb/venue/fonda_san_miguel": httpx.Response(
                200,
                json={
                    "venue": {"display_name": "Fonda San Miguel"},
                    "locations": [
                        {
                            "location_name": "FONDA SAN MIGUEL",
                            "location_address": "2330 W NORTH LOOP",
                        }
                    ],
                    "monthly": month_rows(30),
                },
            ),
            "/api/mb/austin/top": httpx.Response(
                200,
                json={
                    "top": [
                        {"location_name": "A", "address": "1 St", "total": 10.123456}
                    ],
                    "trailing_months": 12,
                },
            ),
            "/api/mb/beta": httpx.Response(
                200,
                json={
                    "window_months": 36,
                    "market": {
                        "ttm_total": 110.0,
                        "prior_ttm_total": 100.0,
                        "volatility": 0.05,
                        "series": [{"ym": "x"}],
                    },
                    "venues": [
                        {
                            "display_name": "Suerte",
                            "bucket": "mexican",
                            "beta": 0.8,
                            "corr": 0.6,
                            "volatility": 0.07,
                            "n_months": 30,
                            "ttm_total": 5.0,
                        }
                    ],
                    "note": "Beta below 1.0 means the venue moves less.",
                },
            ),
        }

        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return self.replies.get(
                request.url.path, httpx.Response(404, json={"detail": "nope"})
            )

        self.adapter = TaxdeskAdapter(
            base_url="http://taxdesk.test/",
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        self.registry = ToolRegistry()
        self.adapter.register(self.registry)

    def paths(self) -> list[str]:
        return [request.url.path for request in self.requests]


class PrivacyTests(Base):
    def test_only_public_routes_are_allowed(self) -> None:
        self.assertFalse(
            any("mix" in route or "ops" in route for route in PUBLIC_ROUTES)
        )
        for private in (
            "/api/mix/periods",
            "/api/mix/groups",
            "/api/ops/daily",
            "/api/mb/geo",
        ):
            with self.subTest(private), self.assertRaises(TaxdeskError):
                self.adapter._get(private)
        self.assertEqual(self.requests, [])

    def test_every_tool_together_never_touches_a_private_route(self) -> None:
        for name, args in (
            ("taxdesk_status", {}),
            ("taxdesk_sales_tax", {}),
            ("taxdesk_sales_tax", {"scope": "statewide"}),
            ("taxdesk_sales_tax", {"scope": "county", "entity": "MTA"}),
            ("taxdesk_watchlist", {}),
            ("taxdesk_venue", {"venue": "fonda"}),
            ("taxdesk_top_venues", {}),
            ("taxdesk_market", {}),
        ):
            _, is_error = self.registry.execute(name, args)
            self.assertFalse(is_error, name)
        for path in self.paths():
            self.assertTrue(path.startswith(PUBLIC_ROUTES), path)
            self.assertNotIn("/mix", path)


class ToolTests(Base):
    def test_registers_six_tools(self) -> None:
        names = {n for n in self.registry.names() if n.startswith("taxdesk_")}
        self.assertEqual(
            names,
            {
                "taxdesk_sales_tax",
                "taxdesk_venue",
                "taxdesk_watchlist",
                "taxdesk_top_venues",
                "taxdesk_market",
                "taxdesk_status",
            },
        )

    def test_status_reads_the_period_from_the_series_not_from_health(self) -> None:
        result, _ = self.registry.execute("taxdesk_status", {})
        # /api/health says 2024-06 here; the series says 2026-10.
        self.assertEqual(result["latest_sales_tax_period"], "2026-10")
        self.assertEqual(result["latest_mixed_beverage_month"], "2026-10")

    def test_sales_tax_returns_only_the_trailing_months(self) -> None:
        result, _ = self.registry.execute("taxdesk_sales_tax", {"months": 3})
        self.assertEqual([r["period_month"] for r in result["series"]], [8, 9, 10])
        self.assertEqual(self.requests[-1].url.params["city"], "AUSTIN")

        _, is_error = self.registry.execute("taxdesk_sales_tax", {"scope": "moon"})
        self.assertTrue(is_error)

    def test_venue_by_spoken_name_with_year_over_year(self) -> None:
        result, is_error = self.registry.execute(
            "taxdesk_venue", {"venue": "Fonda", "months": 2}
        )
        self.assertFalse(is_error)
        self.assertEqual(result["venue"], "Fonda San Miguel")
        self.assertEqual(result["latest_month"], "2026-06")
        self.assertEqual(
            [m["month"] for m in result["monthly"]], ["2026-05", "2026-06"]
        )
        # Month 30 (1290) against month 18 (1170).
        self.assertEqual(result["latest_vs_same_month_last_year_pct"], 10.3)
        # Months 19-30 sum to 14,820; months 7-18 sum to 13,380.
        self.assertEqual(result["trailing_12_vs_prior_12_pct"], 10.8)
        self.assertEqual(result["locations"][0]["address"], "2330 W NORTH LOOP")

    def test_unknown_and_ambiguous_venues_name_the_options(self) -> None:
        result, is_error = self.registry.execute("taxdesk_venue", {"venue": "Uchi"})
        self.assertTrue(is_error)
        self.assertIn("not on the Taxdesk watchlist", str(result))
        self.assertIn("Suerte", str(result))

        result, is_error = self.registry.execute("taxdesk_venue", {"venue": "a"})
        self.assertTrue(is_error)
        self.assertIn("more than one", str(result))
        self.assertNotIn("/api/mb/venue/fonda_san_miguel", self.paths())

    def test_watchlist_is_fetched_once_for_several_lookups(self) -> None:
        self.registry.execute("taxdesk_venue", {"venue": "fonda"})
        self.registry.execute("taxdesk_venue", {"venue": "fonda"})
        self.registry.execute("taxdesk_watchlist", {})
        self.assertEqual(self.paths().count("/api/mb/watchlist"), 1)

    def test_watchlist_omits_the_series(self) -> None:
        result, _ = self.registry.execute("taxdesk_watchlist", {})
        self.assertEqual(
            result["venues"][0],
            {
                "slug": "fonda_san_miguel",
                "name": "Fonda San Miguel",
                "group": "home",
                "months_on_record": 3,
            },
        )

    def test_top_venues_and_market_are_trimmed(self) -> None:
        top, _ = self.registry.execute("taxdesk_top_venues", {"n": 500})
        self.assertEqual(
            top["venues"][0],
            {"rank": 1, "name": "A", "address": "1 St", "total": 10.12},
        )
        self.assertEqual(self.requests[-1].url.params["n"], "50")

        market, _ = self.registry.execute("taxdesk_market", {})
        self.assertEqual(market["austin_market"]["vs_prior_12_months_pct"], 10.0)
        self.assertNotIn("series", market["austin_market"])
        self.assertEqual(market["venues"][0]["beta"], 0.8)


class FailureTests(Base):
    def test_unreachable_service(self) -> None:
        def refuse(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        adapter = TaxdeskAdapter(
            base_url="http://taxdesk.test",
            client=httpx.Client(transport=httpx.MockTransport(refuse)),
        )
        registry = ToolRegistry()
        adapter.register(registry)
        result, is_error = registry.execute("taxdesk_status", {})
        self.assertTrue(is_error)
        self.assertIn("Taxdesk is unreachable", str(result))

    def test_server_error_carries_the_detail(self) -> None:
        self.replies["/api/mb/austin/top"] = httpx.Response(
            503, json={"detail": "database not found"}
        )
        result, is_error = self.registry.execute("taxdesk_top_venues", {})
        self.assertTrue(is_error)
        self.assertIn("HTTP 503, database not found", str(result))

    def test_missing_base_url_refuses_to_construct(self) -> None:
        with self.assertRaises(ValueError):
            TaxdeskAdapter(base_url="")


class RegistryEntryTests(unittest.TestCase):
    def test_agents_yaml_gates_taxdesk_on_its_base_url(self) -> None:
        with open("config/agents.yaml", encoding="utf-8") as handle:
            entry = yaml.safe_load(handle)["agents"]["taxdesk"]
        self.assertEqual(entry["requires_env"], ["TAXDESK_BASE_URL"])
        module = __import__(entry["module"], fromlist=[entry["class"]])
        self.assertIs(getattr(module, entry["class"]), TaxdeskAdapter)


if __name__ == "__main__":
    unittest.main()
