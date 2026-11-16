"""Built-in tools: memory, weather, routines and system status."""

from __future__ import annotations

import time
import unittest
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest import mock

from orchestrator.memory.semantic import SemanticMemory
from orchestrator.memory.store import MemoryStore, normalize_fact
from orchestrator.tools import ToolRegistry
from tools.core_tools import CoreTools, _short_name


class FakeCollection:
    """Stands in for Chroma: similarity is word overlap, which is enough to
    exercise the logic around it without downloading an embedding model."""

    def __init__(self) -> None:
        self.rows: dict[str, tuple[str, dict[str, Any]]] = {}
        self._next = 0

    def add(self, documents: list[str], metadatas: list[dict], ids: list[str]) -> None:
        for document, meta, row_id in zip(documents, metadatas, ids, strict=True):
            self.rows[row_id] = (document, dict(meta))

    @staticmethod
    def _similarity(a: str, b: str) -> float:
        left, right = set(a.lower().split()), set(b.lower().split())
        return len(left & right) / len(left | right) if left | right else 0.0

    def query(self, query_texts: list[str], n_results: int, where: Any = None) -> dict:
        scored = sorted(
            (
                (self._similarity(query_texts[0], document), row_id, document, meta)
                for row_id, (document, meta) in self.rows.items()
            ),
            reverse=True,
        )[:n_results]
        return {
            "ids": [[row[1] for row in scored]],
            "documents": [[row[2] for row in scored]],
            "distances": [[1 - row[0] for row in scored]],
            "metadatas": [[row[3] for row in scored]],
        }

    def get(self, ids: list[str] | None = None, where_document: Any = None) -> dict:
        if ids is not None:
            found = [(i, *self.rows[i]) for i in ids if i in self.rows]
        else:
            needle = where_document["$contains"]
            found = [(i, d, m) for i, (d, m) in self.rows.items() if needle in d]
        return {
            "ids": [row[0] for row in found],
            "documents": [row[1] for row in found],
            "metadatas": [row[2] for row in found],
        }

    def update(self, ids: list[str], metadatas: list[dict]) -> None:
        for row_id, meta in zip(ids, metadatas, strict=True):
            self.rows[row_id] = (self.rows[row_id][0], dict(meta))

    def delete(self, ids: list[str]) -> None:
        for row_id in ids:
            self.rows.pop(row_id, None)


def make_semantic() -> SemanticMemory:
    semantic = SemanticMemory.__new__(SemanticMemory)
    semantic.collection = FakeCollection()  # type: ignore[assignment]
    return semantic


def make_memory() -> MemoryStore:
    """A real MemoryStore, minus the databases it would open on disk."""
    memory = MemoryStore.__new__(MemoryStore)
    memory.semantic = make_semantic()
    memory.episodic = mock.Mock()
    memory.episodic.search.return_value = [
        {"date": "2026-11-10", "summary": "Talked about the bar menu."}
    ]
    return memory


def widget(name: str, data: dict[str, Any], age_minutes: float = 1) -> dict[str, Any]:
    stamp = datetime.now(UTC) - timedelta(minutes=age_minutes)
    return {
        "type": "widget",
        "timestamp": stamp.isoformat(),
        "data": {"widget": name, "data": data},
    }


def timer(name: str, **over: Any) -> dict[str, Any]:
    row = {
        "timer": f"{name}.timer",
        "service": f"{name}.service",
        "description": f"{name} description",
        "enabled": "enabled",
        "schedule": "Sun 03:00",
        "next": time.time() + 3600,
        "last": time.time() - 3600,
        "service_state": "inactive",
        "result": "success",
    }
    row.update(over)
    return row


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.memory = make_memory()
        self.widgets: dict[str, dict[str, Any]] = {
            "weather": widget(
                "weather", {"temp": 61, "condition": "Clear", "high": 70}
            ),
            "system": widget("system", {"cpu": 4.0, "ram": 41.0, "disk": 52.0}),
        }
        self.agents: list[dict[str, Any]] = [
            {"name": "atlas", "status": "loaded", "reason": ""},
            {"name": "muse", "status": "skipped", "reason": "MUSE_BASE_URL not set"},
        ]
        self.timers = [
            timer("jarvis-routine-backlog-drain"),
            timer("jarvis-routine-conformance-sweep"),
            timer("jarvis-briefing"),
        ]
        self.started: list[str] = []
        self.registry = ToolRegistry()
        CoreTools(
            memory=self.memory,
            widgets=lambda: self.widgets,
            agents=lambda: self.agents,
            schedule=lambda: self.timers,
            start_service=self.started.append,
            client_count=lambda: 2,
        ).register(self.registry)

    def run_tool(self, name: str, args: dict[str, Any] | None = None):
        return self.registry.execute(name, args or {})


class RegistrationTests(Base):
    def test_seven_tools_owned_by_jarvis(self) -> None:
        names = {
            "memory_remember",
            "memory_recall",
            "memory_forget",
            "weather_now",
            "routines_list",
            "routine_run",
            "system_status",
        }
        self.assertTrue(names <= set(self.registry.names()))
        for name in names:
            self.assertEqual(self.registry.owner_of(name), "jarvis")


class MemoryTests(Base):
    def test_remember_then_recall(self) -> None:
        result, is_error = self.run_tool(
            "memory_remember",
            {"fact": "  Barry's   distributor is Republic National. "},
        )
        self.assertFalse(is_error)
        self.assertEqual(
            result,
            {
                "ok": True,
                "already_known": False,
                "fact": "Barry's distributor is Republic National.",
            },
        )

        recalled, is_error = self.run_tool(
            "memory_recall", {"query": "who is Barry's distributor"}
        )
        self.assertFalse(is_error)
        self.assertEqual(
            recalled["facts"][0]["fact"], "Barry's distributor is Republic National."
        )
        self.assertEqual(recalled["facts"][0]["category"], "explicit")
        self.assertEqual(recalled["past_conversations"][0]["date"], "2026-11-10")

    def test_a_reworded_duplicate_is_not_stored_twice(self) -> None:
        self.run_tool("memory_remember", {"fact": "The keg room code is 4417"})
        result, _ = self.run_tool(
            "memory_remember", {"fact": "the keg room code is 4417"}
        )
        self.assertTrue(result["already_known"])
        self.assertEqual(len(self.memory.semantic.collection.rows), 1)

    def test_automatic_extraction_cannot_re_add_an_explicit_fact(self) -> None:
        # Seen live on 2026-11-16: the tool stored "The user's test fact is
        # purple." and the brain's own extraction then added "User's test
        # fact is purple" beside it.
        self.run_tool("memory_remember", {"fact": "The user's test fact is purple."})

        existing = self.memory.remember_fact("User's test fact is purple")

        self.assertEqual(existing, "The user's test fact is purple.")
        self.assertEqual(len(self.memory.semantic.collection.rows), 1)

    def test_normalize_fact(self) -> None:
        self.assertEqual(
            normalize_fact("The user's test-fact is PURPLE."),
            normalize_fact("User's test fact is purple"),
        )
        self.assertNotEqual(
            normalize_fact("Paul closes on Tuesdays"),
            normalize_fact("Paul closes on Tuesdays and Fridays"),
        )

    def test_distinct_facts_are_both_kept(self) -> None:
        self.assertIsNone(self.memory.remember_fact("Paul closes on Tuesdays"))
        self.assertIsNone(
            self.memory.remember_fact("Paul closes on Tuesdays and Fridays")
        )
        self.assertEqual(len(self.memory.semantic.collection.rows), 2)

    def test_the_duplicate_check_does_not_count_as_using_a_fact(self) -> None:
        self.memory.remember_fact("The keg room code is 4417")
        self.memory.remember_fact("the keg room code is 4417")
        ((_, meta),) = self.memory.semantic.collection.rows.values()
        self.assertEqual(meta["access_count"], 0)

    def test_a_failed_duplicate_check_still_stores_the_fact(self) -> None:
        with mock.patch.object(
            self.memory.semantic, "search_scored", side_effect=RuntimeError("db")
        ):
            self.assertIsNone(self.memory.remember_fact("Still stored"))
        self.assertEqual(len(self.memory.semantic.collection.rows), 1)

    def test_forget_deletes_only_the_exact_fact(self) -> None:
        self.run_tool("memory_remember", {"fact": "Paul closes on Tuesdays"})
        self.run_tool(
            "memory_remember", {"fact": "Paul closes on Tuesdays and Fridays"}
        )

        result, is_error = self.run_tool(
            "memory_forget", {"fact": "Paul closes on Tuesdays"}
        )

        self.assertFalse(is_error)
        self.assertEqual(result["copies_deleted"], 1)
        remaining = [doc for doc, _ in self.memory.semantic.collection.rows.values()]
        self.assertEqual(remaining, ["Paul closes on Tuesdays and Fridays"])

    def test_forget_with_inexact_text_deletes_nothing(self) -> None:
        self.run_tool("memory_remember", {"fact": "Paul closes on Tuesdays"})
        result, is_error = self.run_tool("memory_forget", {"fact": "paul closes"})
        self.assertTrue(is_error)
        self.assertIn("nothing was deleted", str(result))
        self.assertEqual(len(self.memory.semantic.collection.rows), 1)

    def test_empty_and_oversized_input_is_refused(self) -> None:
        for name, args in (
            ("memory_remember", {"fact": "   "}),
            ("memory_remember", {"fact": "x" * 501}),
            ("memory_recall", {"query": ""}),
            ("memory_forget", {"fact": ""}),
        ):
            with self.subTest(name=name):
                self.assertTrue(self.run_tool(name, args)[1])
        self.assertEqual(len(self.memory.semantic.collection.rows), 0)

    def test_recall_on_an_empty_memory_returns_no_facts(self) -> None:
        result, is_error = self.run_tool("memory_recall", {"query": "anything"})
        self.assertFalse(is_error)
        self.assertEqual(result["facts"], [])


class WeatherTests(Base):
    def test_returns_the_hud_feed_with_its_age(self) -> None:
        result, is_error = self.run_tool("weather_now")
        self.assertFalse(is_error)
        self.assertEqual(result["temp"], 61)
        self.assertEqual(result["age_minutes"], 1)
        self.assertNotIn("note", result)
        # The cache itself must not be modified by the tool.
        self.assertNotIn("age_minutes", self.widgets["weather"]["data"]["data"])

    def test_a_stale_reading_is_flagged(self) -> None:
        self.widgets["weather"] = widget("weather", {"temp": 61}, age_minutes=200)
        result, _ = self.run_tool("weather_now")
        self.assertIn("feed may have stopped", result["note"])

    def test_no_reading_yet_is_an_error(self) -> None:
        del self.widgets["weather"]
        result, is_error = self.run_tool("weather_now")
        self.assertTrue(is_error)
        self.assertIn("has not reported yet", str(result))


class RoutineTests(Base):
    def test_list_uses_spoken_names_and_hides_nothing_needed(self) -> None:
        result, is_error = self.run_tool("routines_list")
        self.assertFalse(is_error)
        names = [row["name"] for row in result["routines"]]
        self.assertEqual(names, ["backlog drain", "conformance sweep", "briefing"])
        first = result["routines"][0]
        self.assertEqual(first["last_result"], "success")
        self.assertTrue(first["enabled"])
        self.assertFalse(first["running_now"])
        self.assertIsNotNone(first["next_run"])

    def test_short_names(self) -> None:
        self.assertEqual(
            _short_name("jarvis-routine-backlog-drain.timer"), "backlog drain"
        )
        self.assertEqual(
            _short_name("atlas-restaurantos-nightly.timer"), "restaurantos nightly"
        )
        self.assertEqual(_short_name("other.timer"), "other")

    def test_run_starts_exactly_the_matched_service(self) -> None:
        result, is_error = self.run_tool("routine_run", {"name": "Conformance-Sweep"})
        self.assertFalse(is_error)
        self.assertEqual(result["started"], "conformance sweep")
        self.assertEqual(self.started, ["jarvis-routine-conformance-sweep.service"])

    def test_partial_name_works_when_unique(self) -> None:
        self.run_tool("routine_run", {"name": "sweep"})
        self.assertEqual(self.started, ["jarvis-routine-conformance-sweep.service"])

    def test_unknown_ambiguous_and_running_routines_start_nothing(self) -> None:
        self.timers.append(
            timer("jarvis-routine-jarvis-brain-mypy", service_state="active")
        )
        for name, expect in (
            ("nightly backup", "Available:"),
            ("r", "more than one"),
            ("brain mypy", "already running"),
            ("", "Say which"),
        ):
            with self.subTest(name=name):
                result, is_error = self.run_tool("routine_run", {"name": name})
                self.assertTrue(is_error)
                self.assertIn(expect, str(result))
        self.assertEqual(self.started, [])

    def test_timers_outside_the_allow_list_are_listed_but_not_started(self) -> None:
        self.timers += [
            timer("barelyswing-premarket"),
            timer("systemd-tmpfiles-clean"),
            timer("atlas-restaurantos-nightly"),
        ]
        listed, _ = self.run_tool("routines_list")
        allowed = {r["name"]: r["can_run_by_voice"] for r in listed["routines"]}
        self.assertFalse(allowed["barelyswing premarket"])
        self.assertFalse(allowed["systemd tmpfiles clean"])
        self.assertTrue(allowed["restaurantos nightly"])
        self.assertTrue(allowed["briefing"])

        for name in ("barelyswing premarket", "tmpfiles"):
            result, is_error = self.run_tool("routine_run", {"name": name})
            self.assertTrue(is_error, name)
            self.assertIn("not one I am allowed to start", str(result))
        self.assertEqual(self.started, [])

        self.run_tool("routine_run", {"name": "restaurantos nightly"})
        self.assertEqual(self.started, ["atlas-restaurantos-nightly.service"])

    def test_the_allow_list_is_configurable(self) -> None:
        self.timers.append(timer("barelyswing-premarket"))
        with mock.patch.dict(
            "os.environ", {"JARVIS_RUN_TIMERS": "barelyswing-*.timer"}
        ):
            self.run_tool("routine_run", {"name": "barelyswing premarket"})
            _, is_error = self.run_tool("routine_run", {"name": "briefing"})
        self.assertEqual(self.started, ["barelyswing-premarket.service"])
        self.assertTrue(is_error)

    def test_a_failed_start_reaches_the_brain(self) -> None:
        def boom(service: str) -> None:
            raise RuntimeError("systemctl: access denied")

        registry = ToolRegistry()
        CoreTools(
            memory=self.memory,
            widgets=lambda: self.widgets,
            agents=lambda: self.agents,
            schedule=lambda: self.timers,
            start_service=boom,
            client_count=lambda: 0,
        ).register(registry)
        result, is_error = registry.execute("routine_run", {"name": "briefing"})
        self.assertTrue(is_error)
        self.assertIn("access denied", str(result))


class StatusTests(Base):
    def test_healthy_system(self) -> None:
        result, is_error = self.run_tool("system_status")
        self.assertFalse(is_error)
        self.assertTrue(result["ok"])
        self.assertEqual(result["problems"], [])
        self.assertEqual(result["skipped_agents"], ["muse"])
        self.assertEqual(result["system_percent"]["disk"], 52.0)
        self.assertEqual(result["connected_clients"], 2)

    def test_each_kind_of_problem_is_named(self) -> None:
        self.agents.append(
            {"name": "sparrow", "status": "failed", "reason": "mcp import error"}
        )
        self.widgets["system"] = widget("system", {"cpu": 5, "ram": 50, "disk": 93.5})
        self.timers[0]["result"] = "exit-code"

        result, _ = self.run_tool("system_status")

        self.assertFalse(result["ok"])
        joined = " | ".join(result["problems"])
        self.assertIn("sparrow failed to load: mcp import error", joined)
        self.assertIn("Disk is 93.5% full", joined)
        self.assertIn("'backlog drain' last ended with exit-code", joined)
        # A skipped agent is configuration, not a fault.
        self.assertNotIn("muse", joined)

    def test_missing_metrics_and_unreadable_timers_are_problems_not_crashes(
        self,
    ) -> None:
        del self.widgets["system"]

        def broken() -> list[dict[str, Any]]:
            raise RuntimeError("systemctl not found")

        registry = ToolRegistry()
        CoreTools(
            memory=self.memory,
            widgets=lambda: self.widgets,
            agents=lambda: self.agents,
            schedule=broken,
            start_service=self.started.append,
            client_count=lambda: 0,
        ).register(registry)

        result, is_error = registry.execute("system_status", {})

        self.assertFalse(is_error)
        self.assertFalse(result["ok"])
        joined = " | ".join(result["problems"])
        self.assertIn("System metrics are not reporting", joined)
        self.assertIn("systemctl not found", joined)


if __name__ == "__main__":
    unittest.main()
