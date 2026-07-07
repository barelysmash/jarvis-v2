"""Parallel collectors that gather all briefing inputs simultaneously."""

import asyncio
import logging
import sqlite3
from typing import Optional

logger = logging.getLogger(__name__)


def _unwrap(ret):
    """Normalize tools.execute() return into a plain result or None.

    tools.execute() now returns (result, is_error). Briefing collectors
    want the bare result on success and None on failure (collect_all and
    the composer treat None as "this section is unavailable"). Older code
    paths that still return a bare value (not a 2-tuple) are passed through
    unchanged for safety.
    """
    if isinstance(ret, tuple) and len(ret) == 2 and isinstance(ret[1], bool):
        result, is_error = ret
        return None if is_error else result
    return ret


class BriefingCollectors:
    """Gathers all briefing inputs in parallel."""

    def __init__(self, tools, memory):
        self.tools = tools
        self.memory = memory

    async def collect_all(self, user_location: str) -> dict:
        """Run all collectors concurrently."""
        results = await asyncio.gather(
            self._weather(user_location),
            self._calendar(),
            self._traffic(user_location),
            self._news_brief(),
            self._unread_messages(),
            self._tracked_items(),
            self._smart_home_status(),
            return_exceptions=True,
        )

        keys = [
            "weather",
            "calendar",
            "traffic",
            "news",
            "messages",
            "tracked",
            "home",
        ]
        return {
            k: (v if not isinstance(v, Exception) else None)
            for k, v in zip(keys, results)
        }

    async def _weather(self, location: str):
        if "get_weather" not in self.tools._tools:
            return None
        ret = await asyncio.to_thread(
            self.tools.execute,
            "get_weather",
            {"location": location, "include_forecast": True},
        )
        return _unwrap(ret)

    async def _execute_with_logging(
        self, tool_name: str, args: dict
    ) -> Optional[str]:
        """Execute a tool and log the event for cross-process visibility."""
        from orchestrator import event_log

        if tool_name not in self.tools.list():
            return None

        event_log.emit(
            source="briefing",
            event_type="tool",
            payload={"name": tool_name, "args": args, "status": "running"},
        )
        try:
            ret = await asyncio.to_thread(
                self.tools.execute, tool_name, args
            )
            result = _unwrap(ret)
            status = "success" if result is not None else "error"
            event_log.emit(
                source="briefing",
                event_type="tool",
                payload={"name": tool_name, "args": args, "status": status},
            )
            return result
        except Exception as e:
            event_log.emit(
                source="briefing",
                event_type="tool",
                payload={"name": tool_name, "args": args, "status": "error"},
            )
            logger.warning("%s failed: %s", tool_name, e)
            return None

    async def _calendar(self) -> Optional[str]:
        return await self._execute_with_logging(
            "calendar_list_events", {"hours_ahead": 24}
        )

    async def _traffic(self, location: str):
        if "get_traffic" not in self.tools._tools:
            return None
        destinations = self.memory.semantic.search(
            "user's primary work location", k=1
        )
        if not destinations:
            return None
        ret = await asyncio.to_thread(
            self.tools.execute,
            "get_traffic",
            {"origin": location, "destination": destinations[0]},
        )
        return _unwrap(ret)

    async def _news_brief(self):
        if "news_search" not in self.tools._tools:
            return []
        topics = self.memory.semantic.search(
            "topics user wants news about", k=5
        )
        if not topics:
            return []
        ret = await asyncio.to_thread(
            self.tools.execute,
            "news_search",
            {"topics": topics, "max_age_hours": 12, "limit": 3},
        )
        result = _unwrap(ret)
        return result if result is not None else []

    async def _unread_messages(self):
        if "check_messages" not in self.tools._tools:
            return None
        ret = await asyncio.to_thread(
            self.tools.execute,
            "check_messages",
            {"priority_only": True, "since_hours": 12},
        )
        return _unwrap(ret)

    async def _tracked_items(self):
        """Items the user previously asked JARVIS to watch."""
        try:
            with sqlite3.connect(self.memory.episodic.db_path) as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS tracked_items (
                        id INTEGER PRIMARY KEY,
                        item TEXT,
                        last_state TEXT,
                        active INTEGER DEFAULT 1
                    )
                    """
                )
                rows = conn.execute(
                    "SELECT item, last_state FROM tracked_items WHERE active = 1"
                ).fetchall()
            return [{"item": r[0], "last_state": r[1]} for r in rows]
        except Exception:
            return []

    async def _smart_home_status(self):
        if "home_status" not in self.tools._tools:
            return None
        ret = await asyncio.to_thread(
            self.tools.execute, "home_status", {}
        )
        return _unwrap(ret)
