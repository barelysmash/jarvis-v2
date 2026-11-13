"""Reminder and timer tools for JARVIS v2.

Three tools over ``orchestrator.reminders``. They only write and read the
store; the scheduler in the API server delivers a reminder when it is due.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from orchestrator.reminders import (
    ReminderError,
    ReminderStore,
    get_store,
    resolve_due,
    spoken_time,
)

if TYPE_CHECKING:
    from orchestrator.tools import ToolRegistry


def _view(reminder: dict[str, Any], now: datetime) -> dict[str, Any]:
    return {
        "id": reminder["id"],
        "kind": reminder["kind"],
        "text": reminder["text"],
        "due": spoken_time(reminder["due_at"], now),
        "minutes_from_now": round(
            (reminder["due_at"] - now).total_seconds() / 60, 1
        ),
    }


class ReminderAdapter:
    def __init__(self, store: ReminderStore | None = None) -> None:
        self._store = store

    @property
    def store(self) -> ReminderStore:
        return self._store or get_store()

    def set(
        self,
        text: str,
        at: str | None = None,
        in_minutes: float | None = None,
        kind: str = "reminder",
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        due = resolve_due(at=at, in_minutes=in_minutes, now=now)
        reminder = self.store.add(text, due, kind, now=now)
        return {"ok": True, "reminder": _view(reminder, now)}

    def list(self) -> dict[str, Any]:
        now = datetime.now(UTC)
        return {
            "now": spoken_time(now, now),
            "pending": [_view(reminder, now) for reminder in self.store.pending()],
        }

    def cancel(self, id: int | None = None, text: str | None = None) -> dict[str, Any]:
        now = datetime.now(UTC)
        pending = self.store.pending()
        if id is None:
            if not text or not text.strip():
                raise ReminderError(
                    "Say which reminder: give its id or some of its text."
                )
            needle = text.strip().lower()
            matches = [r for r in pending if needle in r["text"].lower()]
            if not matches:
                raise ReminderError(f"No pending reminder mentions '{text}'.")
            if len(matches) > 1:
                options = "; ".join(
                    f"#{r['id']} {r['text']} ({spoken_time(r['due_at'], now)})"
                    for r in matches
                )
                raise ReminderError(
                    f"More than one pending reminder matches: {options}. "
                    "Ask which one."
                )
            id = int(matches[0]["id"])
        cancelled = self.store.cancel(int(id))
        if cancelled is None:
            raise ReminderError(f"There is no pending reminder #{id}.")
        return {"ok": True, "cancelled": _view(cancelled, now)}

    def register(self, tools: ToolRegistry) -> None:
        tools.register(
            name="reminder_set",
            description=(
                "Set a reminder or a timer. JARVIS announces it on the HUD and "
                "by voice when it comes due. Give EITHER 'at', a local "
                "wall-clock time as ISO 8601 such as 2026-11-13T16:00 (work it "
                "out from CURRENT TIME; for a bare hour like 'at 4', pick the "
                "next time that hour occurs), OR 'in_minutes' for a delay "
                "such as 10 or 0.5. Use kind 'timer' for 'set a timer'; its "
                "text is a short label like 'pasta timer'. Confirm back the "
                "time the tool returns, not the one you assumed."
            ),
            schema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "What to say when it is due.",
                    },
                    "at": {
                        "type": "string",
                        "description": "Local time, YYYY-MM-DDTHH:MM.",
                    },
                    "in_minutes": {"type": "number", "exclusiveMinimum": 0},
                    "kind": {"type": "string", "enum": ["reminder", "timer"]},
                },
                "required": ["text"],
                "additionalProperties": False,
            },
            handler=lambda text, at=None, in_minutes=None, kind="reminder": self.set(
                text, at, in_minutes, kind
            ),
        )

        tools.register(
            name="reminder_list",
            description=(
                "List pending reminders and timers with their ids and when "
                "each is due."
            ),
            schema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            handler=lambda: self.list(),
        )

        tools.register(
            name="reminder_cancel",
            description=(
                "Cancel a pending reminder or timer, by id (from "
                "reminder_list) or by some of its text. If the text matches "
                "more than one, the tool says which and you ask the user."
            ),
            schema={
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "text": {"type": "string"},
                },
                "additionalProperties": False,
            },
            handler=lambda id=None, text=None: self.cancel(id, text),
        )
