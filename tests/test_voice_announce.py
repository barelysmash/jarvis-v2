"""Voice client: speaking reminders JARVIS announces over /ws.

The client imports audio and wake-word libraries that exist only on the
machine with the microphone, so they are stubbed to load the module. What is
tested is the logic added for announcements: which messages are spoken, and
that an announcement and a conversation never talk over each other.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import types
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

CLIENT = Path(__file__).resolve().parents[1] / "voice-client" / "voice_client.py"
STUBS = (
    "aiohttp",
    "numpy",
    "sounddevice",
    "websockets",
    "dotenv",
    "openwakeword",
    "openwakeword.model",
)


def load_client() -> Any:
    stubs: dict[str, Any] = {name: types.ModuleType(name) for name in STUBS}
    stubs["dotenv"].load_dotenv = lambda *args, **kwargs: None
    stubs["openwakeword.model"].Model = lambda *args, **kwargs: object()
    env = {
        "DEEPGRAM_API_KEY": "x",
        "ELEVENLABS_API_KEY": "x",
        "ELEVENLABS_VOICE_ID": "x",
        "JARVIS_API_URL": "http://jarvis.test:8765",
    }
    with mock.patch.dict(sys.modules, stubs), mock.patch.dict(os.environ, env):
        spec = importlib.util.spec_from_file_location("voice_client_test", CLIENT)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


def message(event_type: str, data: Any) -> str:
    return json.dumps({"type": event_type, "timestamp": "t", "data": data})


class FakeSocket:
    """One connection that yields the given messages, then closes."""

    def __init__(self, messages: list[str]) -> None:
        self._messages = messages

    async def __aenter__(self) -> FakeSocket:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def __aiter__(self) -> FakeSocket:
        return self

    async def __anext__(self) -> str:
        if not self._messages:
            raise StopAsyncIteration
        return self._messages.pop(0)


class AnnouncementTextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = load_client()

    def test_only_announce_events_are_spoken(self) -> None:
        text = self.client.announcement_text
        self.assertEqual(
            text(message("announce", {"id": 1, "kind": "timer", "text": " Done. "})),
            "Done.",
        )
        # A reply to the user is spoken by conversation_turn, not here.
        self.assertIsNone(
            text(message("conversation", {"role": "jarvis", "text": "hi"}))
        )
        self.assertIsNone(text(message("widget", {"widget": "spotify"})))

    def test_malformed_messages_are_ignored(self) -> None:
        text = self.client.announcement_text
        for raw in (
            "not json",
            "[]",
            message("announce", None),
            message("announce", {"text": ""}),
            message("announce", {"text": 7}),
        ):
            with self.subTest(raw):
                self.assertIsNone(text(raw))


class ListenerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = load_client()
        self.spoken: list[str] = []
        self.urls: list[str] = []

    def run_listener(
        self,
        connections: list[list[str]],
        lock: asyncio.Lock | None = None,
        before: Any = None,
    ) -> None:
        client = self.client

        async def fake_speak(chunks: Any) -> bool:
            async for chunk in chunks:
                self.spoken.append(chunk)
            return False

        def connect(url: str, **kwargs: Any) -> FakeSocket:
            self.urls.append(url)
            if not connections:
                raise asyncio.CancelledError
            return FakeSocket(connections.pop(0))

        async def no_sleep(delay: float) -> None:
            return None

        async def scenario() -> None:
            speaking = lock or asyncio.Lock()
            if before is not None:
                await before(speaking)
            with self.assertRaises(asyncio.CancelledError):
                await client.announce_listener(speaking)

        client.websockets.connect = connect
        with (
            mock.patch.object(client, "speak", fake_speak),
            mock.patch.object(client.asyncio, "sleep", no_sleep),
        ):
            asyncio.run(scenario())

    def test_speaks_announcements_and_nothing_else(self) -> None:
        self.run_listener(
            [
                [
                    message("snapshot", {"state": "idle"}),
                    message("conversation", {"role": "jarvis", "text": "Reminder: x."}),
                    message(
                        "announce",
                        {"id": 1, "kind": "reminder", "text": "Reminder: x."},
                    ),
                    message(
                        "announce",
                        {"id": 2, "kind": "timer", "text": "Your timer is done."},
                    ),
                ]
            ]
        )
        self.assertEqual(self.spoken, ["Reminder: x.", "Your timer is done."])
        self.assertEqual(self.urls[0], "ws://jarvis.test:8765/ws")

    def test_reconnects_after_the_connection_drops(self) -> None:
        self.run_listener(
            [
                [message("announce", {"text": "first"})],
                [message("announce", {"text": "second"})],
            ]
        )
        self.assertEqual(self.spoken, ["first", "second"])
        self.assertEqual(len(self.urls), 3)  # two connections, then the stop

    def test_a_failed_speak_does_not_kill_the_listener(self) -> None:
        client = self.client
        calls: list[str] = []

        async def flaky_speak(chunks: Any) -> bool:
            async for chunk in chunks:
                calls.append(chunk)
            if len(calls) == 1:
                raise RuntimeError("tts down")
            return False

        connections = [
            [message("announce", {"text": "a"}), message("announce", {"text": "b"})]
        ]

        def connect(url: str, **kwargs: Any) -> FakeSocket:
            if not connections:
                raise asyncio.CancelledError
            return FakeSocket(connections.pop(0))

        async def no_sleep(delay: float) -> None:
            return None

        async def scenario() -> None:
            with self.assertRaises(asyncio.CancelledError):
                await client.announce_listener(asyncio.Lock())

        client.websockets.connect = connect
        with (
            mock.patch.object(client, "speak", flaky_speak),
            mock.patch.object(client.asyncio, "sleep", no_sleep),
        ):
            asyncio.run(scenario())
        self.assertEqual(calls, ["a", "b"])

    def test_an_announcement_waits_for_a_conversation_to_finish(self) -> None:
        client = self.client
        order: list[str] = []
        connections = [[message("announce", {"text": "reminder"})]]

        async def fake_speak(chunks: Any) -> bool:
            async for chunk in chunks:
                order.append(f"spoke:{chunk}")
            return False

        def connect(url: str, **kwargs: Any) -> FakeSocket:
            if not connections:
                raise asyncio.CancelledError
            return FakeSocket(connections.pop(0))

        real_sleep = asyncio.sleep

        async def scenario() -> None:
            speaking = asyncio.Lock()

            async def conversation() -> None:
                async with speaking:
                    order.append("turn:start")
                    await real_sleep(0.05)
                    order.append("turn:end")

            turn = asyncio.create_task(conversation())
            await real_sleep(0)  # let the conversation take the lock first
            listener = asyncio.create_task(client.announce_listener(speaking))
            await turn
            await real_sleep(0.05)
            listener.cancel()
            await asyncio.gather(listener, return_exceptions=True)

        client.websockets.connect = connect
        with mock.patch.object(client, "speak", fake_speak):
            asyncio.run(scenario())
        self.assertEqual(order, ["turn:start", "turn:end", "spoke:reminder"])


if __name__ == "__main__":
    unittest.main()
