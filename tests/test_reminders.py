"""Reminders and timers: store, time arithmetic, tools and delivery."""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest import mock
from zoneinfo import ZoneInfo

import yaml

from orchestrator.reminders import (
    ReminderError,
    ReminderStore,
    announcement,
    resolve_due,
    spoken_time,
)
from orchestrator.tools import ToolRegistry
from server.reminder_scheduler import fire_due, unseen_events
from tools.integrations.reminders import ReminderAdapter

# Friday 2026-11-13 14:00 in Austin (CST, UTC-6).
NOW = datetime(2026, 11, 13, 20, 0, tzinfo=UTC)


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.store = ReminderStore(Path(self.dir.name) / "reminders.db")
        env = mock.patch.dict(os.environ, {"JARVIS_TIMEZONE": "America/Chicago"})
        env.start()
        self.addCleanup(env.stop)


class ResolveDueTests(Base):
    def test_delay_in_minutes(self) -> None:
        due = resolve_due(at=None, in_minutes=10, now=NOW)
        self.assertEqual(due, NOW + timedelta(minutes=10))
        self.assertEqual(
            resolve_due(at=None, in_minutes=0.5, now=NOW),
            NOW + timedelta(seconds=30),
        )

    def test_local_wall_clock_time_is_read_in_the_jarvis_timezone(self) -> None:
        due = resolve_due(at="2026-11-13T16:00", in_minutes=None, now=NOW)
        self.assertEqual(due, datetime(2026, 11, 13, 22, 0, tzinfo=UTC))

    def test_an_explicit_offset_is_honoured(self) -> None:
        due = resolve_due(at="2026-11-13T23:00+00:00", in_minutes=None, now=NOW)
        self.assertEqual(due, datetime(2026, 11, 13, 23, 0, tzinfo=UTC))

    def test_daylight_saving_is_handled_by_date(self) -> None:
        summer_now = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
        due = resolve_due(at="2026-07-01T16:00", in_minutes=None, now=summer_now)
        self.assertEqual(due, datetime(2026, 7, 1, 21, 0, tzinfo=UTC))  # CDT

    def test_a_past_time_is_refused_and_says_what_time_it_is(self) -> None:
        with self.assertRaises(ReminderError) as caught:
            resolve_due(at="2026-11-13T09:00", in_minutes=None, now=NOW)
        self.assertIn("already passed", str(caught.exception))
        self.assertIn("2:00 PM", str(caught.exception))
        self.assertIn("Friday, November 13", str(caught.exception))

    def test_bad_requests_are_refused(self) -> None:
        cases: list[dict[str, Any]] = [
            {"at": None, "in_minutes": None},
            {"at": "2026-11-13T16:00", "in_minutes": 5},
            {"at": "four o'clock", "in_minutes": None},
            {"at": None, "in_minutes": 0.01},
            {"at": None, "in_minutes": 60 * 24 * 400},
            {"at": "2028-11-13T16:00", "in_minutes": None},
            {"at": None, "in_minutes": "soon"},
        ]
        for case in cases:
            with self.subTest(case), self.assertRaises(ReminderError):
                resolve_due(now=NOW, **case)


class SpokenTimeTests(Base):
    def test_today_tomorrow_and_later(self) -> None:
        self.assertEqual(spoken_time(NOW + timedelta(hours=2), NOW), "4:00 PM")
        self.assertEqual(
            spoken_time(NOW + timedelta(hours=19, minutes=30), NOW),
            "tomorrow at 9:30 AM",
        )
        self.assertEqual(
            spoken_time(NOW + timedelta(days=4), NOW), "Tue Nov 17 at 2:00 PM"
        )


class StoreTests(Base):
    def test_due_returns_only_pending_reminders_whose_time_has_come(self) -> None:
        soon = self.store.add("call the distributor", NOW + timedelta(minutes=5))
        self.store.add("later", NOW + timedelta(hours=3))

        self.assertEqual(self.store.due(NOW), [])
        due = self.store.due(NOW + timedelta(minutes=5))
        self.assertEqual([r["id"] for r in due], [soon["id"]])
        self.assertEqual(len(self.store.pending()), 2)

    def test_a_reminder_can_be_claimed_only_once(self) -> None:
        reminder = self.store.add("x", NOW + timedelta(minutes=1))
        later = NOW + timedelta(minutes=2)

        self.assertTrue(self.store.mark_fired(reminder["id"], later, seen=True))
        self.assertFalse(self.store.mark_fired(reminder["id"], later, seen=True))
        self.assertEqual(self.store.due(later), [])

    def test_cancelled_reminders_never_come_due(self) -> None:
        reminder = self.store.add("x", NOW + timedelta(minutes=1))
        self.assertEqual(self.store.cancel(reminder["id"])["text"], "x")
        self.assertIsNone(self.store.cancel(reminder["id"]))
        self.assertEqual(self.store.due(NOW + timedelta(hours=1)), [])

    def test_reminders_survive_reopening_the_database(self) -> None:
        self.store.add("after restart", NOW + timedelta(minutes=1))
        reopened = ReminderStore(self.store.path)
        self.assertEqual(reopened.pending()[0]["text"], "after restart")

    def test_text_is_cleaned_and_bounded(self) -> None:
        saved = self.store.add("  call \n the   distributor ", NOW + timedelta(1))
        self.assertEqual(saved["text"], "call the distributor")
        for bad in ("", "   ", "x" * 301):
            with self.subTest(len(bad)), self.assertRaises(ReminderError):
                self.store.add(bad, NOW + timedelta(1))
        with self.assertRaises(ReminderError):
            self.store.add("x", NOW + timedelta(1), kind="alarm")

    def test_pending_reminders_are_capped(self) -> None:
        with mock.patch("orchestrator.reminders.MAX_PENDING", 2):
            self.store.add("a", NOW + timedelta(1))
            self.store.add("b", NOW + timedelta(1))
            with self.assertRaises(ReminderError):
                self.store.add("c", NOW + timedelta(1))


class AnnouncementTests(Base):
    def reminder(self, text: str, kind: str = "reminder") -> dict[str, Any]:
        return {"text": text, "kind": kind, "due_at": NOW}

    def test_wording(self) -> None:
        self.assertEqual(
            announcement(self.reminder("call the distributor"), NOW),
            "Reminder: call the distributor.",
        )
        self.assertEqual(
            announcement(self.reminder("Is the keg in?"), NOW),
            "Reminder: Is the keg in?",
        )
        self.assertEqual(
            announcement(self.reminder("pasta timer", "timer"), NOW),
            "Your pasta timer is done.",
        )
        self.assertEqual(
            announcement(self.reminder("check the oven", "timer"), NOW),
            "Your timer is done: check the oven.",
        )

    def test_a_late_reminder_says_when_it_was_due(self) -> None:
        line = announcement(self.reminder("x"), NOW + timedelta(minutes=30))
        self.assertTrue(line.startswith("Late, this was due at 2:00 PM."))
        on_time = announcement(self.reminder("x"), NOW + timedelta(seconds=90))
        self.assertFalse(on_time.startswith("Late"))


class SchedulerTests(Base):
    def setUp(self) -> None:
        super().setUp()
        self.published: list[tuple[str, Any]] = []
        emit = mock.patch("server.reminder_scheduler.event_log.emit")
        self.emit = emit.start()
        self.addCleanup(emit.stop)

    async def publish(self, event_type: str, data: Any) -> None:
        self.published.append((event_type, data))

    def fire(self, now: datetime, subscribers: int = 1) -> list[dict[str, Any]]:
        return asyncio.run(fire_due(self.store, self.publish, lambda: subscribers, now))

    def test_nothing_fires_before_its_time(self) -> None:
        self.store.add("x", NOW + timedelta(minutes=10))
        self.assertEqual(self.fire(NOW + timedelta(minutes=9)), [])
        self.assertEqual(self.published, [])

    def test_a_due_reminder_is_announced_once_on_both_channels(self) -> None:
        reminder = self.store.add("call the distributor", NOW + timedelta(minutes=10))
        when = NOW + timedelta(minutes=10, seconds=1)

        self.assertEqual(len(self.fire(when)), 1)
        self.assertEqual(self.fire(when), [])  # second pass: already claimed

        self.assertEqual(
            self.published,
            [
                (
                    "announce",
                    {
                        "id": reminder["id"],
                        "kind": "reminder",
                        "text": "Reminder: call the distributor.",
                    },
                ),
                (
                    "conversation",
                    {
                        "role": "jarvis",
                        "text": "Reminder: call the distributor.",
                        "streaming": False,
                    },
                ),
            ],
        )
        self.assertEqual(self.emit.call_count, 1)
        self.assertEqual(self.emit.call_args.args[0], "reminders")
        self.assertEqual(self.store.unseen(), [])

    def test_several_due_at_once_fire_in_order(self) -> None:
        self.store.add("second", NOW + timedelta(minutes=2))
        self.store.add("first", NOW + timedelta(minutes=1))
        fired = self.fire(NOW + timedelta(minutes=5))
        self.assertEqual([r["text"] for r in fired], ["first", "second"])

    def test_a_reminder_missed_while_the_server_was_down_fires_late(self) -> None:
        self.store.add("x", NOW + timedelta(minutes=1))
        self.fire(NOW + timedelta(hours=2))
        self.assertTrue(self.published[0][1]["text"].startswith("Late, this was due"))

    def test_with_nobody_connected_it_is_held_for_the_next_client(self) -> None:
        reminder = self.store.add("x", NOW + timedelta(minutes=1))
        self.fire(NOW + timedelta(minutes=2), subscribers=0)
        self.assertEqual([r["id"] for r in self.store.unseen()], [reminder["id"]])

        messages = asyncio.run(unseen_events(self.store))

        self.assertEqual([m["type"] for m in messages], ["announce", "conversation"])
        self.assertTrue(
            messages[0]["data"]["text"].startswith("While nothing was connected:")
        )
        # Handed over once: the next client to connect gets nothing.
        self.assertEqual(asyncio.run(unseen_events(self.store)), [])

    def test_a_reminder_cancelled_between_the_query_and_the_claim_is_skipped(
        self,
    ) -> None:
        reminder = self.store.add("x", NOW + timedelta(minutes=1))
        real_due = self.store.due

        def due_then_cancel(now: datetime) -> list[dict[str, Any]]:
            rows = real_due(now)
            self.store.cancel(reminder["id"])
            return rows

        with mock.patch.object(self.store, "due", due_then_cancel):
            self.assertEqual(self.fire(NOW + timedelta(minutes=2)), [])
        self.assertEqual(self.published, [])


class ToolTests(Base):
    def setUp(self) -> None:
        super().setUp()
        self.registry = ToolRegistry()
        ReminderAdapter(self.store).register(self.registry)

    def run_tool(self, name: str, args: dict[str, Any] | None = None):
        return self.registry.execute(name, args or {})

    def test_set_a_timer_then_list_and_cancel_it_by_text(self) -> None:
        result, is_error = self.run_tool(
            "reminder_set", {"text": "pasta timer", "in_minutes": 10, "kind": "timer"}
        )
        self.assertFalse(is_error)
        self.assertEqual(result["reminder"]["kind"], "timer")
        self.assertAlmostEqual(result["reminder"]["minutes_from_now"], 10.0, 1)

        listed, _ = self.run_tool("reminder_list")
        self.assertEqual([r["text"] for r in listed["pending"]], ["pasta timer"])

        cancelled, is_error = self.run_tool("reminder_cancel", {"text": "pasta"})
        self.assertFalse(is_error)
        self.assertEqual(cancelled["cancelled"]["text"], "pasta timer")
        self.assertEqual(self.run_tool("reminder_list")[0]["pending"], [])

    def test_set_at_a_clock_time_reports_the_time_actually_stored(self) -> None:
        tomorrow = (datetime.now(UTC) + timedelta(days=1)).astimezone(
            ZoneInfo("America/Chicago")
        )
        at = tomorrow.strftime("%Y-%m-%dT16:00")
        result, is_error = self.run_tool(
            "reminder_set", {"text": "call the distributor", "at": at}
        )
        self.assertFalse(is_error)
        self.assertIn("4:00 PM", result["reminder"]["due"])

    def test_errors_reach_the_brain_as_errors(self) -> None:
        for args in (
            {"text": "x"},
            {"text": "x", "at": "2020-01-01T09:00"},
            {"text": "", "in_minutes": 5},
        ):
            with self.subTest(args):
                _, is_error = self.run_tool("reminder_set", args)
                self.assertTrue(is_error)
        self.assertEqual(self.store.pending(), [])

    def test_ambiguous_cancel_names_the_candidates_and_cancels_nothing(self) -> None:
        self.run_tool("reminder_set", {"text": "call Paul", "in_minutes": 5})
        self.run_tool("reminder_set", {"text": "call Carlos", "in_minutes": 9})

        result, is_error = self.run_tool("reminder_cancel", {"text": "call"})
        self.assertTrue(is_error)
        self.assertIn("call Paul", str(result))
        self.assertIn("call Carlos", str(result))
        self.assertEqual(len(self.store.pending()), 2)

        for args in ({"text": "nobody"}, {"id": 999}, {}):
            with self.subTest(args):
                self.assertTrue(self.run_tool("reminder_cancel", args)[1])

    def test_agents_yaml_registers_reminders_without_requirements(self) -> None:
        with open("config/agents.yaml", encoding="utf-8") as handle:
            entry = yaml.safe_load(handle)["agents"]["reminders"]
        self.assertEqual(entry["requires_env"], [])
        module = __import__(entry["module"], fromlist=[entry["class"]])
        self.assertIs(getattr(module, entry["class"]), ReminderAdapter)


if __name__ == "__main__":
    unittest.main()
