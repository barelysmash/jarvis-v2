"""Cross-process event log.

Any JARVIS process (api, briefing, sleep cycle, etc.) writes events to a
shared SQLite database. Two readers consume it:

* the API server's HUD poller, which rebroadcasts rows written by OTHER
  processes onto the in-process WebSocket bus (rows from its own process
  were already published directly), and
* the cockpit feed (``/ws/cockpit``), which tails every row by id so a
  second screen sees the full, ordered history across all processes.

Event types written here:

  tool        a tool call (running → success/error), with turn_id, agent,
              duration and a clipped result once finished
  widget      HUD widget payloads
  turn.start  a brain turn began (input text, origin)
  llm         one model round-trip inside a turn (tokens, latency, stop)
  turn.end    a brain turn finished (final reply, duration, tool count)
  agent       sub-agent lifecycle + key changes pushed via /api/events
  job         scheduled job start/end (briefing, sleep cycle, ...)
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sqlite3
import sys
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Biggest JSON payload stored per event. Tool results can be large
# (calendar dumps, scans); the cockpit only needs enough to eyeball them.
MAX_PAYLOAD_CHARS = 16_000
MAX_FIELD_CHARS = 4_000

_schema_ready_for: str | None = None


def process_label() -> str:
    """Short name for the writing process, shown in the cockpit."""
    explicit = os.environ.get("JARVIS_PROC")
    if explicit:
        return explicit
    stem = Path(sys.argv[0]).stem if sys.argv and sys.argv[0] else "python"
    return {"uvicorn": "api", "trigger_briefing": "briefing",
            "run_sleep_cycle": "sleep", "run_voice": "voice"}.get(stem, stem)


def _db_path() -> Path:
    data_dir = Path(os.environ.get("JARVIS_DATA_DIR", "data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "events.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path()), timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create/migrate once per process per database path."""
    global _schema_ready_for
    path = str(_db_path())
    if _schema_ready_for == path:
        return
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tool_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            source TEXT NOT NULL,
            event_type TEXT NOT NULL,
            payload TEXT NOT NULL
        )
        """
    )
    cols = {row[1] for row in conn.execute("PRAGMA table_info(tool_events)")}
    if "pid" not in cols:
        conn.execute("ALTER TABLE tool_events ADD COLUMN pid INTEGER")
    if "proc" not in cols:
        conn.execute("ALTER TABLE tool_events ADD COLUMN proc TEXT")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tool_events_ts ON tool_events(ts)"
    )
    conn.commit()
    _schema_ready_for = path


def init_db() -> None:
    """Idempotent table creation/migration. Safe to call from any process."""
    with _connect() as conn:
        _ensure_schema(conn)


def clip(value: Any, limit: int = MAX_FIELD_CHARS) -> Any:
    """Make a value JSON-safe and bounded for storage."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, default=str)
            if len(text) <= limit:
                return json.loads(text)
        except (TypeError, ValueError):
            text = str(value)
    if len(text) <= limit:
        return text
    return text[:limit] + f"… [+{len(text) - limit} chars]"


def emit(source: str, event_type: str, payload: dict[str, Any]) -> int | None:
    """Write an event. Best-effort: failures logged but never raised.

    Returns the new row id (or None on failure).
    """
    try:
        body = json.dumps(payload, default=str)
        if len(body) > MAX_PAYLOAD_CHARS:
            body = json.dumps(
                {k: clip(v, MAX_FIELD_CHARS // 2) for k, v in payload.items()},
                default=str,
            )
        with _connect() as conn:
            _ensure_schema(conn)
            cur = conn.execute(
                "INSERT INTO tool_events "
                "(ts, source, event_type, payload, pid, proc) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (time.time(), source, event_type, body,
                 os.getpid(), process_label()),
            )
            conn.commit()
            return cur.lastrowid
    except Exception as e:
        logger.warning("Failed to emit event: %s", e)
        return None


def _row_to_event(r: sqlite3.Row) -> dict:
    try:
        payload = json.loads(r["payload"])
    except (TypeError, ValueError):
        payload = {"raw": r["payload"]}
    return {
        "id": r["id"],
        "ts": r["ts"],
        "iso": datetime.fromtimestamp(r["ts"]).isoformat(),
        "source": r["source"],
        "type": r["event_type"],
        "pid": r["pid"],
        "proc": r["proc"],
        "payload": payload,
    }


_SELECT = "SELECT id, ts, source, event_type, payload, pid, proc FROM tool_events"


def fetch_since(after_ts: float, limit: int = 100) -> list[dict]:
    """Return events newer than the given timestamp (ascending order)."""
    try:
        with _connect() as conn:
            _ensure_schema(conn)
            rows = conn.execute(
                f"{_SELECT} WHERE ts > ? ORDER BY ts ASC LIMIT ?",
                (after_ts, limit),
            ).fetchall()
            return [_row_to_event(r) for r in rows]
    except Exception as e:
        logger.warning("Failed to fetch events: %s", e)
        return []


def fetch_after_id(after_id: int, limit: int = 200) -> list[dict]:
    """Return events with id > after_id (ascending). Gap-free tailing."""
    try:
        with _connect() as conn:
            _ensure_schema(conn)
            rows = conn.execute(
                f"{_SELECT} WHERE id > ? ORDER BY id ASC LIMIT ?",
                (after_id, limit),
            ).fetchall()
            return [_row_to_event(r) for r in rows]
    except Exception as e:
        logger.warning("Failed to fetch events: %s", e)
        return []


def fetch_tail(limit: int = 500) -> list[dict]:
    """Return the most recent `limit` events in ascending order."""
    try:
        with _connect() as conn:
            _ensure_schema(conn)
            rows = conn.execute(
                f"{_SELECT} ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [_row_to_event(r) for r in reversed(rows)]
    except Exception as e:
        logger.warning("Failed to fetch events: %s", e)
        return []


def prune_older_than(seconds: float = 3600.0) -> int:
    """Delete events older than the given window. Called by the sleep cycle."""
    cutoff = time.time() - seconds
    try:
        with _connect() as conn:
            _ensure_schema(conn)
            cur = conn.execute(
                "DELETE FROM tool_events WHERE ts < ?", (cutoff,)
            )
            conn.commit()
            return cur.rowcount
    except Exception as e:
        logger.warning("Failed to prune events: %s", e)
        return 0


def retention_seconds() -> float:
    """Event retention window (JARVIS_EVENT_RETENTION_DAYS, default 7)."""
    try:
        days = float(os.environ.get("JARVIS_EVENT_RETENTION_DAYS", "7"))
    except ValueError:
        days = 7.0
    return max(days, 0.04) * 86_400


@contextlib.contextmanager
def job(name: str, **detail: Any) -> Iterator[dict[str, Any]]:
    """Bracket a scheduled job with job start/end events.

    Usage::

        with event_log.job("briefing", style="standard") as rec:
            ...
            rec["summary"] = "3 events, 2 headlines"
    """
    started = time.time()
    record: dict[str, Any] = {}
    emit("job", "job", {"job": name, "phase": "start", **detail})
    try:
        yield record
    except BaseException as exc:
        emit("job", "job", {
            "job": name, "phase": "end", "ok": False,
            "ms": int((time.time() - started) * 1000),
            "error": clip(f"{type(exc).__name__}: {exc}", 1000),
            **record,
        })
        raise
    else:
        emit("job", "job", {
            "job": name, "phase": "end", "ok": True,
            "ms": int((time.time() - started) * 1000),
            **{k: clip(v, 1000) for k, v in record.items()},
        })
