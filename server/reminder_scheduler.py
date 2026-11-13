"""Delivers reminders when they come due.

One background task in the API process polls the store. A due reminder is
claimed in the database first (so it can never fire twice), then announced:

* ``announce`` on the WebSocket bus, which the voice client speaks;
* a ``conversation`` line, which the HUD shows like any JARVIS reply;
* an event-log entry, which the cockpit lists.

If nothing is connected when it fires, the reminder is kept as unseen and
handed to the next client that connects, so it is late instead of lost.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from orchestrator import event_log
from orchestrator.reminders import ReminderStore, announcement

logger = logging.getLogger(__name__)

POLL_SECONDS = 1.0

Publish = Callable[[str, Any], Awaitable[None]]


def announce_events(reminder: dict[str, Any], text: str) -> list[tuple[str, Any]]:
    """The bus events for one announcement: voice first, then the HUD line."""
    return [
        (
            "announce",
            {"id": reminder["id"], "kind": reminder["kind"], "text": text},
        ),
        ("conversation", {"role": "jarvis", "text": text, "streaming": False}),
    ]


async def fire_due(
    store: ReminderStore,
    publish: Publish,
    subscriber_count: Callable[[], int],
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Fire everything due at ``now``. Returns the reminders announced."""
    now = now or datetime.now(UTC)
    fired: list[dict[str, Any]] = []
    for reminder in await asyncio.to_thread(store.due, now):
        heard = subscriber_count() > 0
        claimed = await asyncio.to_thread(
            store.mark_fired, reminder["id"], now, seen=heard
        )
        if not claimed:
            continue  # cancelled, or fired by another task, since due() ran
        text = announcement(reminder, now)
        for event_type, data in announce_events(reminder, text):
            await publish(event_type, data)
        event_log.emit(
            "reminders",
            "agent",
            {
                "agent": "reminders",
                "kind": f"{reminder['kind']}_fired",
                "severity": "notice",
                "summary": text,
                "via": "scheduler",
                "data": {"id": reminder["id"], "heard": heard},
            },
        )
        fired.append(reminder)
    return fired


async def unseen_events(store: ReminderStore) -> list[dict[str, Any]]:
    """Events for reminders that fired with nobody connected; marks them seen.

    Called for a client that has just connected. Returned as ready-to-send
    WebSocket messages.
    """
    now = datetime.now(UTC)
    missed = await asyncio.to_thread(store.unseen)
    if not missed:
        return []
    await asyncio.to_thread(store.mark_seen, [r["id"] for r in missed])
    messages: list[dict[str, Any]] = []
    for reminder in missed:
        text = "While nothing was connected: " + announcement(reminder, now)
        for event_type, data in announce_events(reminder, text):
            messages.append(
                {"type": event_type, "timestamp": now.isoformat(), "data": data}
            )
    return messages


async def run_scheduler(
    store: ReminderStore,
    publish: Publish,
    subscriber_count: Callable[[], int],
) -> None:
    logger.info("Reminder scheduler started (%s)", store.path)
    while True:
        try:
            await fire_due(store, publish, subscriber_count)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Reminder scheduler pass failed")
        await asyncio.sleep(POLL_SECONDS)
