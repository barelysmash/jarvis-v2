"""Phone notifications: own server only, best-effort, and wired to reminders."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest import mock

import httpx
import yaml

from orchestrator.reminders import ReminderStore
from orchestrator.tools import ToolRegistry
from scripts.trigger_briefing import BRIEFING_READY, push_briefing_to_phone
from server.reminder_scheduler import fire_due
from tools.integrations.phone import (
    MAX_MESSAGE_CHARS,
    PhoneError,
    PhoneNotifier,
    notifier_from_env,
)

BASE = "https://guildenstern.example.ts.net"


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.requests: list[httpx.Request] = []
        self.status = 200
        self.fail_connection = False
        self.notifier = self.make()

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail_connection:
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(self.status, json={"id": "abc"})

    def make(self, **kwargs: Any) -> PhoneNotifier:
        client = httpx.Client(transport=httpx.MockTransport(self.handle))
        self.addCleanup(client.close)
        options = {"base_url": BASE, "topic": "jarvis", "token": "tk_test"}
        options.update(kwargs)
        return PhoneNotifier(client=client, **options)

    def body(self, index: int = -1) -> dict[str, Any]:
        return json.loads(self.requests[index].content)


class SendTests(Base):
    def test_publishes_json_to_the_server_root_with_the_token(self) -> None:
        result = self.notifier.send("Bring the keys.", title="Reminder")
        request = self.requests[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(str(request.url), BASE)
        self.assertEqual(request.headers["Authorization"], "Bearer tk_test")
        self.assertEqual(
            self.body(),
            {
                "topic": "jarvis",
                "message": "Bring the keys.",
                "title": "Reminder",
                "priority": 3,
            },
        )
        self.assertEqual(result["sent"], True)

    def test_the_result_does_not_echo_the_token_or_topic(self) -> None:
        rendered = json.dumps(self.notifier.send("Hello"))
        self.assertNotIn("tk_test", rendered)
        self.assertNotIn(BASE, rendered)

    def test_priority_names_map_to_ntfy_levels(self) -> None:
        self.notifier.send("a", priority="high")
        self.notifier.send("b", priority="low")
        self.assertEqual([self.body(0)["priority"], self.body(1)["priority"]], [4, 2])

    def test_an_unknown_priority_is_refused(self) -> None:
        with self.assertRaises(PhoneError):
            self.notifier.send("a", priority="urgent")
        self.assertEqual(self.requests, [])

    def test_long_text_is_cut_to_the_limit(self) -> None:
        self.notifier.send("x" * 5000)
        self.assertEqual(len(self.body()["message"]), MAX_MESSAGE_CHARS)
        self.assertTrue(self.body()["message"].endswith("…"))

    def test_an_empty_message_is_not_sent(self) -> None:
        with self.assertRaises(PhoneError):
            self.notifier.send("   ")
        self.assertEqual(self.requests, [])

    def test_no_token_means_no_authorization_header(self) -> None:
        self.make(token="").send("Hello")
        self.assertNotIn("Authorization", self.requests[0].headers)

    def test_a_refused_token_is_explained_without_showing_it(self) -> None:
        self.status = 403
        with self.assertRaises(PhoneError) as caught:
            self.notifier.send("Hello")
        self.assertIn("refused the access token", str(caught.exception))
        self.assertNotIn("tk_test", str(caught.exception))

    def test_an_unreachable_server_is_a_phone_error(self) -> None:
        self.fail_connection = True
        with self.assertRaises(PhoneError):
            self.notifier.send("Hello")

    def test_try_send_never_raises(self) -> None:
        self.fail_connection = True
        with self.assertLogs("tools.integrations.phone", level="WARNING"):
            self.assertFalse(self.notifier.try_send("Hello"))
        self.fail_connection = False
        self.assertTrue(self.notifier.try_send("Hello"))


class OwnServerTests(Base):
    def test_the_public_service_is_refused(self) -> None:
        for url in ("https://ntfy.sh", "https://NTFY.sh/", "https://eu.ntfy.sh"):
            with self.assertRaises(ValueError) as caught:
                self.make(base_url=url)
            self.assertIn("a server you run", str(caught.exception))

    def test_a_lookalike_host_is_not_mistaken_for_the_public_one(self) -> None:
        self.assertEqual(
            self.make(base_url="https://myntfy.shop").base_url, "https://myntfy.shop"
        )

    def test_a_tailscale_address_is_accepted(self) -> None:
        notifier = self.make(base_url="http://100.113.110.44:8091/")
        self.assertEqual(notifier.base_url, "http://100.113.110.44:8091")

    def test_missing_settings_and_bad_urls_are_refused(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            for options in (
                {"base_url": "", "topic": "jarvis"},
                {"base_url": BASE, "topic": ""},
                {"base_url": "ftp://box", "topic": "jarvis"},
            ):
                with self.assertRaises(ValueError):
                    PhoneNotifier(**options)

    def test_from_env_is_off_until_url_and_topic_are_set(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(notifier_from_env())
        with mock.patch.dict(os.environ, {"NTFY_BASE_URL": BASE}, clear=True):
            self.assertIsNone(notifier_from_env())
        env = {"NTFY_BASE_URL": BASE, "NTFY_TOPIC": "jarvis"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertIsInstance(notifier_from_env(), PhoneNotifier)

    def test_from_env_turns_itself_off_for_the_public_service(self) -> None:
        env = {"NTFY_BASE_URL": "https://ntfy.sh", "NTFY_TOPIC": "jarvis"}
        with (
            mock.patch.dict(os.environ, env, clear=True),
            self.assertLogs("tools.integrations.phone", level="WARNING"),
        ):
            self.assertIsNone(notifier_from_env())


class ToolTests(Base):
    def setUp(self) -> None:
        super().setUp()
        self.tools = ToolRegistry()
        self.notifier.register(self.tools)

    def test_the_tool_sends_what_it_is_given(self) -> None:
        result, is_error = self.tools.execute(
            "phone_notify", {"message": "Gate code is by the door.", "title": "Note"}
        )
        self.assertFalse(is_error)
        self.assertEqual(self.body()["title"], "Note")
        self.assertEqual(self.body()["priority"], 3)
        self.assertIn("sent", str(result))

    def test_a_failed_send_is_a_tool_error(self) -> None:
        self.status = 500
        result, is_error = self.tools.execute("phone_notify", {"message": "Hi"})
        self.assertTrue(is_error)
        self.assertIn("HTTP 500", str(result))

    def test_the_description_limits_it_to_explicit_requests(self) -> None:
        description = self.tools._tools["phone_notify"]["description"]
        self.assertIn("only when the user asks", " ".join(description.split()))
        self.assertIn("Never use it on your own initiative", description)
        self.assertIn("relative changes only", description)

    def test_agents_yaml_gates_the_phone_on_url_and_topic(self) -> None:
        with open("config/agents.yaml", encoding="utf-8") as handle:
            entry = yaml.safe_load(handle)["agents"]["phone"]
        self.assertEqual(entry["requires_env"], ["NTFY_BASE_URL", "NTFY_TOPIC"])
        module = __import__(entry["module"], fromlist=[entry["class"]])
        self.assertIs(getattr(module, entry["class"]), PhoneNotifier)


class ReminderPushTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = ReminderStore(Path(directory.name) / "reminders.db")
        self.now = datetime(2026, 11, 18, 15, 0, tzinfo=UTC)
        self.published: list[str] = []
        self.pushed: list[tuple[str, str]] = []

    async def publish(self, event_type: str, data: Any) -> None:
        self.published.append(event_type)

    def notify(self, text: str, title: str) -> bool:
        self.pushed.append((text, title))
        return True

    def fire(self, notify: Any, listeners: int = 1) -> list[dict[str, Any]]:
        return asyncio.run(
            fire_due(
                self.store,
                self.publish,
                lambda: listeners,
                now=self.now + timedelta(seconds=1),
                notify=notify,
            )
        )

    def test_a_due_reminder_is_pushed_once_with_its_spoken_line(self) -> None:
        self.store.add("call the plumber", self.now, kind="reminder", now=self.now)
        self.assertEqual(len(self.fire(self.notify)), 1)
        self.assertEqual(self.pushed, [("Reminder: call the plumber.", "Reminder")])
        self.assertEqual(self.fire(self.notify), [])
        self.assertEqual(len(self.pushed), 1)

    def test_a_timer_is_titled_timer(self) -> None:
        self.store.add("pasta timer", self.now, kind="timer", now=self.now)
        self.fire(self.notify)
        self.assertEqual(self.pushed[0][1], "Timer")

    def test_the_phone_is_told_even_when_a_client_heard_it(self) -> None:
        self.store.add("stretch", self.now, kind="reminder", now=self.now)
        self.fire(self.notify, listeners=3)
        self.assertEqual(len(self.pushed), 1)

    def test_a_failed_push_does_not_stop_the_announcement(self) -> None:
        self.store.add("stretch", self.now, kind="reminder", now=self.now)
        fired = self.fire(lambda text, title: False)
        self.assertEqual(len(fired), 1)
        self.assertEqual(self.published, ["announce", "conversation"])

    def test_without_a_notifier_nothing_changes(self) -> None:
        self.store.add("stretch", self.now, kind="reminder", now=self.now)
        self.assertEqual(len(self.fire(None)), 1)
        self.assertEqual(self.published, ["announce", "conversation"])


class BriefingPushTests(Base):
    def test_a_quiet_briefing_sends_one_low_priority_notice(self) -> None:
        result = {"output": "Long text", "alerts": []}
        sent = push_briefing_to_phone(result, self.notifier)
        self.assertEqual(sent, 1)
        self.assertEqual(self.body()["message"], BRIEFING_READY)
        self.assertEqual(self.body()["priority"], 2)

    def test_the_briefing_text_itself_is_never_pushed(self) -> None:
        result = {"output": "SECRET BODY", "alerts": ["A"]}
        push_briefing_to_phone(result, self.notifier)
        sent = b"".join(request.content for request in self.requests)
        self.assertNotIn(b"SECRET BODY", sent)

    def test_each_alert_is_pushed_verbatim_at_high_priority(self) -> None:
        alert = "Atlas alert: no nightly report has reached the ledger since Monday."
        sent = push_briefing_to_phone({"alerts": [alert]}, self.notifier)
        self.assertEqual(sent, 2)
        self.assertEqual(self.body()["message"], alert)
        self.assertEqual(self.body()["title"], "Atlas alert")
        self.assertEqual(self.body()["priority"], 4)

    def test_no_notifier_sends_nothing(self) -> None:
        self.assertEqual(push_briefing_to_phone({"alerts": ["A"]}, None), 0)

    def test_an_unreachable_phone_does_not_fail_the_briefing(self) -> None:
        self.fail_connection = True
        with self.assertLogs("tools.integrations.phone", level="WARNING"):
            sent = push_briefing_to_phone({"alerts": ["A"]}, self.notifier)
        self.assertEqual(sent, 0)


if __name__ == "__main__":
    unittest.main()
