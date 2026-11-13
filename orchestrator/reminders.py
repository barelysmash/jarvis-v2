"""Reminders and timers: the store and the time arithmetic.

A reminder is a line of text and a moment. This module owns both and nothing
else: no network, no event loop, no model. The scheduler in
``server/reminder_scheduler.py`` polls :meth:`ReminderStore.due` and delivers;
the tools in ``tools/integrations/reminders.py`` create, list and cancel.

Rows survive a restart, so a reminder set before a deploy still fires, and one
that came due while the server was down fires late instead of vanishing.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = "America/Chicago"
MAX_TEXT_CHARS = 300
MAX_PENDING = 200
MIN_DELAY = timedelta(seconds=5)
MAX_DELAY = timedelta(days=366)
KINDS = ("reminder", "timer")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS reminders (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT    NOT NULL,
    text       TEXT    NOT NULL,
    due_ts     REAL    NOT NULL,
    created_ts REAL    NOT NULL,
    status     TEXT    NOT NULL DEFAULT 'pending',
    fired_ts   REAL,
    seen       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS reminders_due ON reminders (status, due_ts);
"""


class ReminderError(ValueError):
    """A request the reminder tools refuse. The message is for the user."""


def local_zone() -> ZoneInfo:
    return ZoneInfo(os.environ.get("JARVIS_TIMEZONE", DEFAULT_TIMEZONE))


def spoken_time(moment: datetime, now: datetime | None = None) -> str:
    """'4:00 PM' today, 'tomorrow at 9:30 AM', else 'Tue Nov 17 at 9:30 AM'."""
    zone = local_zone()
    local = moment.astimezone(zone)
    today = (now or datetime.now(UTC)).astimezone(zone).date()
    clock = local.strftime("%I:%M %p").lstrip("0")
    if local.date() == today:
        return clock
    if local.date() == today + timedelta(days=1):
        return f"tomorrow at {clock}"
    return f"{local.strftime('%a %b')} {local.day} at {clock}"


def resolve_due(
    *,
    at: str | None,
    in_minutes: float | None,
    now: datetime,
) -> datetime:
    """Turn the tool's ``at`` or ``in_minutes`` into an aware UTC moment.

    ``at`` is local wall-clock time, ISO 8601 (``2026-11-13T16:00``); a value
    that carries its own offset is honoured. Exactly one of the two is needed.
    """
    if (at is None) == (in_minutes is None):
        raise ReminderError("Give either a time ('at') or a delay ('in_minutes').")

    if in_minutes is not None:
        try:
            delay = timedelta(minutes=float(in_minutes))
        except (TypeError, ValueError, OverflowError):
            raise ReminderError("in_minutes must be a number.") from None
        if delay < MIN_DELAY:
            raise ReminderError("That delay is too short; the minimum is 5 seconds.")
        if delay > MAX_DELAY:
            raise ReminderError("That is more than a year away.")
        return now + delay

    try:
        parsed = datetime.fromisoformat(str(at))
    except ValueError:
        raise ReminderError(
            "'at' must look like 2026-11-13T16:00 (local time)."
        ) from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=local_zone())
    due = parsed.astimezone(UTC)
    if due <= now:
        raise ReminderError(
            f"That time has already passed. It is now "
            f"{spoken_time(now, now)} on "
            f"{now.astimezone(local_zone()).strftime('%A, %B')} "
            f"{now.astimezone(local_zone()).day}. Ask for a later time."
        )
    if due - now > MAX_DELAY:
        raise ReminderError("That is more than a year away.")
    return due


def _row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "text": row["text"],
        "due_at": datetime.fromtimestamp(row["due_ts"], UTC),
        "status": row["status"],
        "fired_at": (
            datetime.fromtimestamp(row["fired_ts"], UTC)
            if row["fired_ts"] is not None
            else None
        ),
        "seen": bool(row["seen"]),
    }


class ReminderStore:
    def __init__(self, path: str | Path | None = None) -> None:
        if path is None:
            data_dir = Path(os.environ.get("JARVIS_DATA_DIR", "data"))
            data_dir.mkdir(parents=True, exist_ok=True)
            path = data_dir / "reminders.db"
        self.path = Path(path)
        with self._db() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        # One short-lived connection per call: tools run on worker threads
        # and the scheduler on the event loop, and sqlite connections do not
        # cross threads.
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def add(
        self, text: str, due_at: datetime, kind: str = "reminder",
        *, now: datetime | None = None,
    ) -> dict[str, Any]:
        text = " ".join(str(text).split())
        if not text:
            raise ReminderError("A reminder needs some text.")
        if len(text) > MAX_TEXT_CHARS:
            raise ReminderError(
                f"Keep the reminder under {MAX_TEXT_CHARS} characters."
            )
        if kind not in KINDS:
            raise ReminderError(f"kind must be one of {', '.join(KINDS)}.")
        created = (now or datetime.now(UTC)).timestamp()
        with self._db() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM reminders WHERE status = 'pending'"
            ).fetchone()[0]
            if count >= MAX_PENDING:
                raise ReminderError(
                    f"There are already {MAX_PENDING} pending reminders; "
                    "cancel some first."
                )
            cursor = conn.execute(
                "INSERT INTO reminders (kind, text, due_ts, created_ts) "
                "VALUES (?, ?, ?, ?)",
                (kind, text, due_at.timestamp(), created),
            )
            row = conn.execute(
                "SELECT * FROM reminders WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
        return _row(row)

    def pending(self) -> list[dict[str, Any]]:
        with self._db() as conn:
            rows = conn.execute(
                "SELECT * FROM reminders WHERE status = 'pending' ORDER BY due_ts, id"
            ).fetchall()
        return [_row(row) for row in rows]

    def due(self, now: datetime) -> list[dict[str, Any]]:
        with self._db() as conn:
            rows = conn.execute(
                "SELECT * FROM reminders WHERE status = 'pending' AND due_ts <= ? "
                "ORDER BY due_ts, id",
                (now.timestamp(),),
            ).fetchall()
        return [_row(row) for row in rows]

    def mark_fired(self, reminder_id: int, now: datetime, *, seen: bool) -> bool:
        """Claim a pending reminder. False if it was already fired or cancelled."""
        with self._db() as conn:
            cursor = conn.execute(
                "UPDATE reminders SET status = 'fired', fired_ts = ?, seen = ? "
                "WHERE id = ? AND status = 'pending'",
                (now.timestamp(), int(seen), reminder_id),
            )
            return cursor.rowcount == 1

    def unseen(self) -> list[dict[str, Any]]:
        """Fired while nothing was connected to show or speak them."""
        with self._db() as conn:
            rows = conn.execute(
                "SELECT * FROM reminders WHERE status = 'fired' AND seen = 0 "
                "ORDER BY due_ts, id"
            ).fetchall()
        return [_row(row) for row in rows]

    def mark_seen(self, reminder_ids: list[int]) -> None:
        if not reminder_ids:
            return
        marks = ",".join("?" for _ in reminder_ids)
        with self._db() as conn:
            conn.execute(
                f"UPDATE reminders SET seen = 1 WHERE id IN ({marks})",
                reminder_ids,
            )

    def cancel(self, reminder_id: int) -> dict[str, Any] | None:
        with self._db() as conn:
            row = conn.execute(
                "SELECT * FROM reminders WHERE id = ? AND status = 'pending'",
                (reminder_id,),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE reminders SET status = 'cancelled' WHERE id = ?",
                (reminder_id,),
            )
        return _row(row)


_store: ReminderStore | None = None


def get_store() -> ReminderStore:
    """The process-wide store, shared by the tools and the scheduler."""
    global _store
    if _store is None:
        _store = ReminderStore()
    return _store


def announcement(reminder: dict[str, Any], now: datetime) -> str:
    """The line JARVIS shows and speaks when a reminder comes due."""
    text = str(reminder["text"])
    late = now - reminder["due_at"] > timedelta(minutes=2)
    if reminder["kind"] == "timer":
        line = f"Your {text} is done." if text.lower().endswith("timer") else (
            f"Your timer is done: {text}."
        )
    else:
        line = f"Reminder: {text}"
        if line[-1] not in ".!?":
            line += "."
    if late:
        line = f"Late, this was due at {spoken_time(reminder['due_at'], now)}. {line}"
    return line
