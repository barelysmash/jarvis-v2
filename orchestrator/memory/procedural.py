"""Procedural memory: learned multi-step workflows."""

import json
import sqlite3
from datetime import datetime
from typing import Optional


class ProceduralMemory:
    """Stores learned multi-step workflows the user has named or repeated."""

    def __init__(self, db_path: str = "./data/jarvis.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS workflows (
                    name TEXT PRIMARY KEY,
                    steps TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    times_used INTEGER DEFAULT 0
                )
                """
            )

    def save(self, name: str, steps: list[dict]):
        """Save a named workflow. Steps are tool_name + args dicts."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO workflows (name, steps, created_at) "
                "VALUES (?, ?, ?)",
                (name, json.dumps(steps), datetime.now().isoformat()),
            )

    def get(self, name: str) -> Optional[list[dict]]:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT steps FROM workflows WHERE name = ?", (name,)
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE workflows SET times_used = times_used + 1 "
                    "WHERE name = ?",
                    (name,),
                )
                return json.loads(row[0])
        return None

    def list_all(self) -> list[str]:
        with sqlite3.connect(self.db_path) as conn:
            return [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM workflows ORDER BY times_used DESC"
                ).fetchall()
            ]
