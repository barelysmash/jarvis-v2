"""Cross-process tool event log.

Any JARVIS process (api, briefing, sleep cycle, etc.) writes tool events
to a shared SQLite database. The API server polls for new rows and
broadcasts them to WebSocket subscribers, so the HUD sees activity from
all sources, not just direct text input.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _db_path() -> Path:
    data_dir = Path(os.environ.get("JARVIS_DATA_DIR", "data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "events.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path()), timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Idempotent table creation. Safe to call from any process."""
    with _connect() as conn:
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
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tool_events_ts "
            "ON tool_events(ts)"
        )
        conn.commit()


def emit(source: str, event_type: str, payload: dict[str, Any]) -> None:
    """Write a tool event. Best-effort: failures logged but don't raise."""
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT INTO tool_events (ts, source, event_type, payload) "
                "VALUES (?, ?, ?, ?)",
                (time.time(), source, event_type, json.dumps(payload)),
            )
            conn.commit()
    except Exception as e:
        logger.warning("Failed to emit event: %s", e)


def fetch_since(after_ts: float, limit: int = 100) -> list[dict]:
    """Return events newer than the given timestamp (ascending order)."""
    try:
        with _connect() as conn:
            rows = conn.execute(
                "SELECT id, ts, source, event_type, payload FROM tool_events "
                "WHERE ts > ? ORDER BY ts ASC LIMIT ?",
                (after_ts, limit),
            ).fetchall()
            return [
                {
                    "id": r["id"],
                    "ts": r["ts"],
                    "iso": datetime.fromtimestamp(r["ts"]).isoformat(),
                    "source": r["source"],
                    "type": r["event_type"],
                    "payload": json.loads(r["payload"]),
                }
                for r in rows
            ]
    except Exception as e:
        logger.warning("Failed to fetch events: %s", e)
        return []


def prune_older_than(seconds: float = 3600.0) -> int:
    """Delete events older than the given window. Call from sleep cycle."""
    cutoff = time.time() - seconds
    try:
        with _connect() as conn:
            cur = conn.execute(
                "DELETE FROM tool_events WHERE ts < ?", (cutoff,)
            )
            conn.commit()
            return cur.rowcount
    except Exception as e:
        logger.warning("Failed to prune events: %s", e)
        return 0
