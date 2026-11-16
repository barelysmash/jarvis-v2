"""Built-in tools over things the JARVIS server already knows.

Each of these was visible on the HUD or stored automatically, but not
something the brain could call: memory, the weather feed, the routine
timers, and the health of the system itself. They are registered by the
API server, which hands in the live objects, so nothing here imports the
server.

    memory_remember / memory_recall / memory_forget
    weather_now
    routines_list / routine_run
    system_status
"""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from orchestrator.reminders import spoken_time

if TYPE_CHECKING:
    from orchestrator.memory.store import MemoryStore
    from orchestrator.tools import ToolRegistry

MAX_FACT_CHARS = 500
WEATHER_STALE_MINUTES = 60
DISK_WARN_PERCENT = 90.0
TIMER_PREFIXES = ("jarvis-routine-", "jarvis-", "atlas-")
# Timers routine_run may start. routines_list shows every timer the cockpit
# does, but starting one by voice is limited to JARVIS's own routines and the
# Atlas refresh: not trading jobs, not system housekeeping. Widen with
# JARVIS_RUN_TIMERS (comma-separated globs).
DEFAULT_RUN_TIMERS = "jarvis-routine-*.timer,jarvis-briefing.timer,atlas-*.timer"

_NO_ARGS: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


class CoreToolError(ValueError):
    """A request a core tool refuses. The message is for the user."""


def _when(epoch: float | None, now: datetime) -> str | None:
    if epoch is None:
        return None
    return spoken_time(datetime.fromtimestamp(epoch, UTC), now)


def _short_name(timer: str) -> str:
    """'jarvis-routine-backlog-drain.timer' -> 'backlog drain'."""
    name = timer.removesuffix(".timer")
    for prefix in TIMER_PREFIXES:
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    return name.replace("-", " ")


def run_globs() -> list[str]:
    raw = os.environ.get("JARVIS_RUN_TIMERS", DEFAULT_RUN_TIMERS)
    return [glob.strip() for glob in raw.split(",") if glob.strip()]


def may_run(timer: str) -> bool:
    return any(fnmatch.fnmatch(timer, glob) for glob in run_globs())


class CoreTools:
    def __init__(
        self,
        *,
        memory: MemoryStore,
        widgets: Callable[[], dict[str, dict[str, Any]]],
        agents: Callable[[], list[dict[str, Any]]],
        schedule: Callable[[], list[dict[str, Any]]],
        start_service: Callable[[str], None],
        client_count: Callable[[], int],
    ) -> None:
        self.memory = memory
        self.widgets = widgets
        self.agents = agents
        self.schedule = schedule
        self.start_service = start_service
        self.client_count = client_count

    # -- memory --

    def remember(self, fact: str) -> dict[str, Any]:
        fact = " ".join(str(fact).split())
        if not fact:
            raise CoreToolError("There is nothing to remember.")
        if len(fact) > MAX_FACT_CHARS:
            raise CoreToolError(f"Keep a fact under {MAX_FACT_CHARS} characters.")
        existing = self.memory.remember_fact(fact, category="explicit")
        if existing is not None:
            return {"ok": True, "already_known": True, "fact": existing}
        return {"ok": True, "already_known": False, "fact": fact}

    def recall(self, query: str, limit: int = 5) -> dict[str, Any]:
        if not str(query).strip():
            raise CoreToolError("Say what to recall.")
        limit = max(1, min(10, int(limit)))
        facts = self.memory.semantic.search_scored(str(query), k=limit)
        episodes = self.memory.episodic.search(str(query), k=3)
        return {
            "facts": [
                {
                    "fact": hit["text"],
                    "similarity": hit["similarity"],
                    "category": hit["category"],
                }
                for hit in facts
            ],
            "past_conversations": [
                {"date": episode.get("date"), "summary": episode.get("summary")}
                for episode in episodes
            ],
        }

    def forget(self, fact: str) -> dict[str, Any]:
        fact = str(fact).strip()
        if not fact:
            raise CoreToolError("Say which fact to forget.")
        deleted = self.memory.semantic.delete_exact(fact)
        if deleted == 0:
            raise CoreToolError(
                "No stored fact has exactly that text, so nothing was deleted. "
                "Use memory_recall to find the stored wording, then pass it "
                "unchanged."
            )
        return {"ok": True, "forgotten": fact, "copies_deleted": deleted}

    # -- weather --

    def weather(self) -> dict[str, Any]:
        event = self.widgets().get("weather")
        payload = ((event or {}).get("data") or {}).get("data")
        if not isinstance(payload, dict):
            raise CoreToolError(
                "The weather feed has not reported yet. Try again in a minute."
            )
        result = dict(payload)
        try:
            stamp = datetime.fromisoformat(str((event or {}).get("timestamp")))
            age = (datetime.now(UTC) - stamp).total_seconds() / 60
            result["age_minutes"] = round(age)
            if age > WEATHER_STALE_MINUTES:
                result["note"] = (
                    f"This reading is {round(age)} minutes old; the feed may "
                    "have stopped."
                )
        except ValueError:
            result["age_minutes"] = None
        result["units"] = "Fahrenheit, mph"
        return result

    # -- routines --

    def _routine_rows(self) -> list[dict[str, Any]]:
        now = datetime.now(UTC)
        rows = []
        for row in self.schedule():
            rows.append(
                {
                    "name": _short_name(str(row.get("timer"))),
                    "timer": row.get("timer"),
                    "service": row.get("service"),
                    "what": row.get("description"),
                    "schedule": row.get("schedule") or None,
                    "next_run": _when(row.get("next"), now),
                    "last_run": _when(row.get("last"), now),
                    "last_result": row.get("result") or None,
                    "running_now": row.get("service_state") == "active",
                    "enabled": row.get("enabled") == "enabled",
                    "can_run_by_voice": may_run(str(row.get("timer"))),
                }
            )
        return rows

    def routines(self) -> dict[str, Any]:
        rows = self._routine_rows()
        return {
            "routines": [
                {key: value for key, value in row.items() if key != "service"}
                for row in rows
            ]
        }

    def run_routine(self, name: str) -> dict[str, Any]:
        needle = str(name).strip().lower().replace("-", " ")
        if not needle:
            raise CoreToolError("Say which routine to run.")
        rows = self._routine_rows()
        exact = [row for row in rows if row["name"] == needle]
        partial = [row for row in rows if needle in row["name"]]
        matches = exact or partial
        available = ", ".join(row["name"] for row in rows) or "none"
        if not matches:
            raise CoreToolError(f"No routine matches '{name}'. Available: {available}.")
        if len(matches) > 1:
            raise CoreToolError(
                f"'{name}' matches more than one routine: "
                f"{', '.join(row['name'] for row in matches)}. Ask which."
            )
        row = matches[0]
        if not row["can_run_by_voice"]:
            raise CoreToolError(
                f"'{row['name']}' is not one I am allowed to start. Use the "
                "cockpit's run-now button for it."
            )
        if not row["service"]:
            raise CoreToolError(f"'{row['name']}' has no service to start.")
        if row["running_now"]:
            raise CoreToolError(f"'{row['name']}' is already running.")
        self.start_service(str(row["service"]))
        return {"ok": True, "started": row["name"], "service": row["service"]}

    # -- status --

    def status(self) -> dict[str, Any]:
        problems: list[str] = []

        agents = [
            {
                "name": agent.get("name"),
                "status": agent.get("status"),
                "reason": agent.get("reason") or None,
            }
            for agent in self.agents()
        ]
        for agent in agents:
            if agent["status"] == "failed":
                problems.append(
                    f"Agent {agent['name']} failed to load: {agent['reason']}"
                )

        system = ((self.widgets().get("system") or {}).get("data") or {}).get("data")
        if isinstance(system, dict):
            disk = system.get("disk")
            if isinstance(disk, int | float) and disk >= DISK_WARN_PERCENT:
                problems.append(f"Disk is {disk}% full.")
        else:
            system = None
            problems.append("System metrics are not reporting.")

        try:
            routines = self._routine_rows()
        except Exception as exc:
            routines = []
            problems.append(f"Routine timers could not be read: {exc}")
        for row in routines:
            if row["last_result"] not in (None, "success"):
                problems.append(
                    f"Routine '{row['name']}' last ended with {row['last_result']}."
                )

        return {
            "ok": not problems,
            "problems": problems,
            "agents": agents,
            "skipped_agents": [a["name"] for a in agents if a["status"] == "skipped"],
            "system_percent": system,
            "routines_running": [r["name"] for r in routines if r["running_now"]],
            "connected_clients": self.client_count(),
        }

    def register(self, tools: ToolRegistry) -> None:
        tools.register(
            name="memory_remember",
            description=(
                "Store one durable fact because the user asked you to remember "
                "it. Phrase it as a complete standalone sentence about the "
                "user or their world. Not for passing remarks: those are "
                "picked up automatically. If the result says already_known, "
                "tell the user it was already on record."
            ),
            schema={
                "type": "object",
                "properties": {"fact": {"type": "string"}},
                "required": ["fact"],
                "additionalProperties": False,
            },
            handler=lambda fact: self.remember(fact),
        )
        tools.register(
            name="memory_recall",
            description=(
                "Search long-term memory: stored facts and past conversations. "
                "Use when the user asks what you know or remember about "
                "something. A similarity near 1 is a close match; below about "
                "0.3 is probably unrelated, so say you have nothing on it "
                "instead of stretching a weak match."
            ),
            schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            handler=lambda query, limit=5: self.recall(query, limit),
        )
        tools.register(
            name="memory_forget",
            description=(
                "Delete one stored fact, only when the user asks you to forget "
                "it. Pass the fact's exact stored text, copied from "
                "memory_recall. It deletes exact matches only and cannot be "
                "undone. If recall shows several candidates, ask which."
            ),
            schema={
                "type": "object",
                "properties": {"fact": {"type": "string"}},
                "required": ["fact"],
                "additionalProperties": False,
            },
            handler=lambda fact: self.forget(fact),
        )
        tools.register(
            name="weather_now",
            description=(
                "Current conditions and a four-day forecast for the user's "
                "home location, from the same feed as the HUD weather panel. "
                "Fahrenheit and mph. Use this before web search for weather "
                "at home; use web search for anywhere else."
            ),
            schema=_NO_ARGS,
            handler=lambda: self.weather(),
        )
        tools.register(
            name="routines_list",
            description=(
                "The scheduled routines (systemd timers): what each does, when "
                "it next runs, when it last ran and how that ended."
            ),
            schema=_NO_ARGS,
            handler=lambda: self.routines(),
        )
        tools.register(
            name="routine_run",
            description=(
                "Start a scheduled routine now, by name from routines_list. "
                "Only when the user explicitly asks to run it: some routines "
                "change code, open pull requests or spend API budget. Only "
                "routines marked can_run_by_voice may be started. It "
                "starts in the background; use routines_list later for the "
                "result."
            ),
            schema={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
                "additionalProperties": False,
            },
            handler=lambda name: self.run_routine(name),
        )
        tools.register(
            name="system_status",
            description=(
                "Whether JARVIS itself is healthy: which sub-agents loaded, "
                "CPU / memory / disk on the host, routines that failed, and "
                "how many HUD or voice clients are connected. 'ok' is false "
                "when 'problems' lists anything; report those first."
            ),
            schema=_NO_ARGS,
            handler=lambda: self.status(),
        )
