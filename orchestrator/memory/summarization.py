"""Memory summarization: compresses old episodes into condensed summaries."""

import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional


class MemorySummarization:
    """Compresses old episodes into daily summaries."""

    def __init__(self, episodic_memory, anthropic_client, db_path: str):
        self.episodic = episodic_memory
        self.client = anthropic_client
        self.db_path = db_path
        self._init_summary_table()

    def _init_summary_table(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS episode_summaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    period_start TEXT NOT NULL,
                    period_end TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    episode_count INTEGER,
                    created_at TEXT NOT NULL
                )
                """
            )

    def run(self, age_threshold_days: int = 30) -> dict:
        """Summarize episodes older than threshold, then delete originals."""
        cutoff = datetime.now() - timedelta(days=age_threshold_days)

        episodes_by_day = self._fetch_old_episodes(cutoff)
        if not episodes_by_day:
            return {"summarized_days": 0, "compressed_episodes": 0}

        summarized_days = 0
        compressed_count = 0

        for day, episodes in episodes_by_day.items():
            summary = self._summarize_day(day, episodes)
            if not summary:
                continue

            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT INTO episode_summaries "
                    "(period_start, period_end, summary, episode_count, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        f"{day}T00:00:00",
                        f"{day}T23:59:59",
                        summary,
                        len(episodes),
                        datetime.now().isoformat(),
                    ),
                )

                episode_ids = [e["id"] for e in episodes]
                placeholders = ",".join("?" * len(episode_ids))
                conn.execute(
                    f"DELETE FROM episodes WHERE id IN ({placeholders})",
                    episode_ids,
                )
                conn.execute(
                    f"DELETE FROM episodes_fts WHERE rowid IN ({placeholders})",
                    episode_ids,
                )

            summarized_days += 1
            compressed_count += len(episodes)

        return {
            "summarized_days": summarized_days,
            "compressed_episodes": compressed_count,
        }

    def _fetch_old_episodes(
        self, cutoff: datetime
    ) -> dict[str, list[dict]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, timestamp, user_input, response FROM episodes "
                "WHERE timestamp < ? ORDER BY timestamp",
                (cutoff.isoformat(),),
            ).fetchall()

        by_day: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            day = r["timestamp"][:10]
            by_day[day].append(dict(r))
        return dict(by_day)

    def _summarize_day(
        self, day: str, episodes: list[dict]
    ) -> Optional[str]:
        """Use Claude to produce a tight summary of a day's interactions."""
        transcript = "\n".join(
            f"[{e['timestamp'][11:16]}] User: {e['user_input']}\n"
            f"[{e['timestamp'][11:16]}] JARVIS: {e['response']}"
            for e in episodes
        )

        try:
            response = self.client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=512,
                system=(
                    "Summarize this day's interactions between the user "
                    "and JARVIS. Preserve: decisions made, preferences "
                    "expressed, tasks completed, anomalies, and recurring "
                    "themes. Drop: small talk, repeated queries, trivial "
                    "confirmations. Be concise but comprehensive - this "
                    "summary replaces the raw log permanently."
                ),
                messages=[
                    {
                        "role": "user",
                        "content": f"Date: {day}\n\nTranscript:\n{transcript}",
                    }
                ],
            )
            text = response.content[0].text.strip() if response.content else None
            return text if text else None
        except Exception:
            return None

    def search_summaries(self, query: str, k: int = 3) -> list[dict]:
        """Search compressed historical summaries."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            results = conn.execute(
                "SELECT period_start, summary FROM episode_summaries "
                "WHERE summary LIKE ? ORDER BY period_start DESC LIMIT ?",
                (f"%{query}%", k),
            ).fetchall()
            return [dict(r) for r in results]
