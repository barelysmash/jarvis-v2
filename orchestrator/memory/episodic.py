"""Episodic memory: timestamped log of every interaction."""

import json
import sqlite3
from datetime import datetime
from typing import Optional


class EpisodicMemory:
    """Timestamped log of every interaction. Searchable by time and content."""

    def __init__(self, db_path: str = "./data/jarvis.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS episodes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    user_input TEXT NOT NULL,
                    response TEXT NOT NULL,
                    metadata TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_timestamp ON episodes(timestamp)"
            )
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts
                USING fts5(user_input, response, content=episodes)
                """
            )

    def log(
        self,
        user_input: str,
        response: str,
        timestamp: str,
        metadata: Optional[dict] = None,
    ):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "INSERT INTO episodes (timestamp, user_input, response, metadata) "
                "VALUES (?, ?, ?, ?)",
                (timestamp, user_input, response, json.dumps(metadata or {})),
            )
            episode_id = cursor.lastrowid
            conn.execute(
                "INSERT INTO episodes_fts (rowid, user_input, response) "
                "VALUES (?, ?, ?)",
                (episode_id, user_input, response),
            )

    def search(self, query: str, k: int = 3) -> list[dict]:
        """Full-text search across all logged episodes."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            try:
                results = conn.execute(
                    """
                    SELECT e.timestamp, e.user_input, e.response
                    FROM episodes_fts f
                    JOIN episodes e ON e.id = f.rowid
                    WHERE episodes_fts MATCH ?
                    ORDER BY rank LIMIT ?
                    """,
                    (query, k),
                ).fetchall()
            except sqlite3.OperationalError:
                # FTS query syntax error - fall back to LIKE
                results = conn.execute(
                    """
                    SELECT timestamp, user_input, response FROM episodes
                    WHERE user_input LIKE ? OR response LIKE ?
                    ORDER BY timestamp DESC LIMIT ?
                    """,
                    (f"%{query}%", f"%{query}%", k),
                ).fetchall()

            return [
                {
                    "date": r["timestamp"][:10],
                    "summary": (
                        f"{r['user_input'][:80]} -> {r['response'][:80]}"
                    ),
                }
                for r in results
            ]

    def get_range(self, start: datetime, end: datetime) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM episodes WHERE timestamp BETWEEN ? AND ? "
                    "ORDER BY timestamp DESC",
                    (start.isoformat(), end.isoformat()),
                ).fetchall()
            ]

    def count(self) -> int:
        with sqlite3.connect(self.db_path) as conn:
            return conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
