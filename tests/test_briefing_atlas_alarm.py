from __future__ import annotations

import asyncio
import os
import unittest
from unittest import mock

from orchestrator.tools import ToolRegistry
from workflows.morning_briefing.analyzer import (
    BriefingAnalyzer,
    atlas_alerts,
    atlas_decision_lines,
    atlas_stale_days,
)
from workflows.morning_briefing.collectors import BriefingCollectors


def freshness(last: str | None, days: int | None) -> dict:
    return {
        "status": "ok",
        "last_service_date": last,
        "days_since_last_service": days,
    }


class AtlasAlertTests(unittest.TestCase):
    def test_no_alert_when_atlas_is_not_registered(self):
        self.assertEqual(atlas_alerts(None, 3), [])

    def test_no_alert_inside_the_limit(self):
        # Monday morning after a closed Sunday: Saturday is two days back.
        self.assertEqual(atlas_alerts(freshness("2026-11-07", 2), 3), [])
        self.assertEqual(atlas_alerts(freshness("2026-11-06", 3), 3), [])

    def test_alert_names_the_last_night_and_the_gap(self):
        alerts = atlas_alerts(freshness("2026-10-28", 14), 3)

        self.assertEqual(len(alerts), 1)
        self.assertIn("Wednesday, October 28", alerts[0])
        self.assertIn("14 days", alerts[0])
        self.assertIn("feed may have stopped", alerts[0])

    def test_alert_when_atlas_does_not_answer(self):
        alerts = atlas_alerts({"status": "unreachable", "error": "refused"}, 3)

        self.assertEqual(len(alerts), 1)
        self.assertIn("did not answer", alerts[0])
        self.assertNotIn("refused", alerts[0])

    def test_alert_when_the_ledger_is_empty(self):
        alerts = atlas_alerts(freshness(None, None), 3)
        self.assertIn("no service nights", alerts[0])

    def test_limit_comes_from_the_environment(self):
        with mock.patch.dict(os.environ, {"ATLAS_STALE_DAYS": "5"}):
            self.assertEqual(atlas_stale_days(), 5)
            self.assertEqual(atlas_alerts(freshness("2026-11-06", 5)), [])
            self.assertEqual(len(atlas_alerts(freshness("2026-11-05", 6))), 1)
        with mock.patch.dict(os.environ, {"ATLAS_STALE_DAYS": "soon"}):
            self.assertEqual(atlas_stale_days(), 3)

    def test_alerts_carry_no_operating_figure(self):
        # The alarm is built from two fields only; nothing else can leak in.
        noisy = {**freshness("2026-10-28", 14), "manifest": {"record_count": 3261}}
        self.assertNotIn("3261", " ".join(atlas_alerts(noisy, 3)))


def decision(
    summary: str = "Intervene on net sales.",
    priority: str = "high",
    days: int | None = 0,
    change: float | None = -7.8,
    metric: str = "net sales",
) -> dict:
    return {
        "summary": summary,
        "priority": priority,
        "days_since_first_emitted": days,
        "evidence": [
            {
                "metric": metric,
                "direction": "down",
                "change_pct": -13.6,
                "basis": "across 6 consecutive weekly moves the same way",
            },
            {
                "metric": metric,
                "direction": "down",
                "change_pct": change,
                "basis": "latest full week against the week before",
            },
        ],
    }


def payload(*items: dict) -> dict:
    return {"status": "ok", "count": len(items), "decisions": list(items)}


class AtlasDecisionLineTests(unittest.TestCase):
    def test_a_new_high_priority_decision_is_one_plain_line(self):
        lines = atlas_decision_lines(payload(decision()))
        self.assertEqual(
            lines,
            [
                "Atlas decision, high priority: Intervene on net sales. In the "
                "latest full week, net sales fell 7.8% against the week before."
            ],
        )

    def test_a_rise_reads_as_a_rise(self):
        item = decision("Intervene on labor cost percent.", change=4.0,
                        metric="labor cost percent")
        [line] = atlas_decision_lines(payload(item))
        self.assertIn("labor cost percent rose 4%", line)

    def test_only_the_week_over_week_citation_is_quoted(self):
        [line] = atlas_decision_lines(payload(decision()))
        self.assertNotIn("13.6", line)

    def test_without_a_week_over_week_citation_only_the_summary_is_said(self):
        item = decision()
        item["evidence"] = item["evidence"][:1]
        self.assertEqual(
            atlas_decision_lines(payload(item)),
            ["Atlas decision, high priority: Intervene on net sales."],
        )

    def test_medium_and_low_priority_are_left_for_when_asked(self):
        items = [decision(priority="medium"), decision(priority="low")]
        self.assertEqual(atlas_decision_lines(payload(*items)), [])

    def test_a_decision_is_mentioned_only_while_new(self):
        self.assertEqual(len(atlas_decision_lines(payload(decision(days=2)))), 1)
        self.assertEqual(atlas_decision_lines(payload(decision(days=3))), [])
        self.assertEqual(atlas_decision_lines(payload(decision(days=None))), [])

    def test_more_than_three_are_summarised_not_listed(self):
        items = [decision(f"Intervene on metric {n}.") for n in range(5)]
        lines = atlas_decision_lines(payload(*items))
        self.assertEqual(len(lines), 4)
        self.assertEqual(
            lines[-1],
            "Atlas has 2 more new high-priority Decisions; ask me for them.",
        )

    def test_nothing_is_said_when_atlas_has_no_goals_or_did_not_answer(self):
        self.assertEqual(atlas_decision_lines(None), [])
        self.assertEqual(atlas_decision_lines({"status": "not_configured"}), [])
        self.assertEqual(atlas_decision_lines({"status": "ok"}), [])

    def test_malformed_items_are_skipped(self):
        items = ["x", {"priority": "high"}, decision()]
        self.assertEqual(len(atlas_decision_lines(payload(*items))), 1)


class AnalyzerTests(unittest.TestCase):
    def test_analysis_includes_decision_lines(self):
        memory = mock.Mock()
        memory.semantic.search.return_value = []
        analysis = BriefingAnalyzer(memory, None).analyze(
            {"atlas_decisions": payload(decision())}
        )
        self.assertEqual(len(analysis["decision_lines"]), 1)
        self.assertEqual(analysis["alerts"], [])

    def test_analysis_includes_alerts(self):
        memory = mock.Mock()
        memory.semantic.search.return_value = []
        analysis = BriefingAnalyzer(memory, None).analyze(
            {"atlas": freshness("2026-10-28", 14)}
        )
        self.assertEqual(len(analysis["alerts"]), 1)

        quiet = BriefingAnalyzer(memory, None).analyze({"atlas": None})
        self.assertEqual(quiet["alerts"], [])


class DecisionCollectorTests(unittest.TestCase):
    def collect(self, tools: ToolRegistry):
        collectors = BriefingCollectors(tools, mock.Mock())
        return asyncio.run(collectors._atlas_decisions())

    def register(self, handler) -> ToolRegistry:
        tools = ToolRegistry()
        tools.register(
            name="atlas_decisions",
            description="x",
            schema={"type": "object", "properties": {}},
            handler=handler,
        )
        return tools

    def test_unregistered_atlas_collects_nothing(self):
        self.assertIsNone(self.collect(ToolRegistry()))

    def test_collects_structured_decisions_not_boundary_text(self):
        tools = self.register(lambda: {"request_id": "r", "data": payload(decision())})
        self.assertEqual(self.collect(tools)["count"], 1)

    def test_a_failing_atlas_collects_nothing(self):
        def boom():
            raise RuntimeError("Atlas is unreachable")

        with self.assertLogs("workflows.morning_briefing.collectors", "WARNING"):
            self.assertIsNone(self.collect(self.register(boom)))


class CollectorTests(unittest.TestCase):
    def collect(self, tools: ToolRegistry):
        collectors = BriefingCollectors(tools, mock.Mock())
        with mock.patch("orchestrator.event_log.emit"):
            return asyncio.run(collectors._atlas_freshness())

    def test_unregistered_atlas_collects_nothing(self):
        self.assertIsNone(self.collect(ToolRegistry()))

    def test_collects_structured_freshness_not_boundary_text(self):
        tools = ToolRegistry()
        tools.register(
            name="atlas_freshness",
            description="x",
            schema={"type": "object", "properties": {}},
            handler=lambda: {
                "request_id": "r",
                "data": {
                    "last_service_date": "2026-11-10",
                    "days_since_last_service": 1,
                },
            },
        )

        result = self.collect(tools)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["days_since_last_service"], 1)

    def test_failing_atlas_is_reported_as_unreachable(self):
        tools = ToolRegistry()

        def boom():
            raise RuntimeError("Atlas is unreachable: connection refused")

        tools.register(
            name="atlas_freshness",
            description="x",
            schema={"type": "object", "properties": {}},
            handler=boom,
        )

        result = self.collect(tools)
        self.assertEqual(result["status"], "unreachable")

    def test_malformed_answer_is_reported_as_unreachable(self):
        tools = ToolRegistry()
        tools.register(
            name="atlas_freshness",
            description="x",
            schema={"type": "object", "properties": {}},
            handler=lambda: "not a payload",
        )
        self.assertEqual(self.collect(tools)["status"], "unreachable")


if __name__ == "__main__":
    unittest.main()
