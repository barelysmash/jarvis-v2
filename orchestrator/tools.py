"""Tool registry: extensible plugin system for tools the brain can call."""

import logging
from typing import Any, Callable, TYPE_CHECKING

from .grounding import ground_tool_result

if TYPE_CHECKING:
    from tools.integrations.calendar import GoogleCalendar

logger = logging.getLogger("jarvis.tools")


class ToolRegistry:
    """Registers and executes tools the brain can invoke."""

    def __init__(self):
        self._tools: dict[str, dict] = {}
        self._register_defaults()

    # ─── Registration ────────────────────────────────────────

    def register(
        self,
        name: str,
        description: str,
        schema: dict,
        handler: Callable,
    ):
        """Register a single tool."""
        self._tools[name] = {
            "description": description,
            "input_schema": schema,
            "handler": handler,
        }
        logger.debug("Registered tool: %s", name)

    def get_schemas(self) -> list:
        """Return tool schemas in Anthropic API format."""
        return [
            {
                "name": name,
                "description": t["description"],
                "input_schema": t["input_schema"],
            }
            for name, t in self._tools.items()
        ]

    def execute(self, name: str, args: dict) -> tuple[Any, bool]:
        """Run a tool. Returns (result, is_error).

        is_error=True means the tool failed and the result is an error
        description that should be sent to Claude with is_error=True
        on the tool_result block — so Claude knows the tool failed and
        can tell the user honestly instead of fabricating an answer.

        We also normalize the legacy "error in result" conventions
        (dict with .error key, list whose first item is a dict with
        .error key) into is_error=True so older tools work without
        modification.
        """
        if name not in self._tools:
            return (f"Unknown tool: {name}", True)
        try:
            result = self._tools[name]["handler"](**args)
        except Exception as exc:
            logger.exception("Tool %s failed", name)
            return (f"Tool error: {exc}", True)

        # Detect legacy "error in result" conventions
        if isinstance(result, dict) and result.get("error"):
            return (result, True)
        if (
            isinstance(result, list)
            and result
            and isinstance(result[0], dict)
            and result[0].get("error")
        ):
            return (result, True)

        # Friday is a source-bound analysis service. Give the reasoning model a
        # provenance contract together with the payload so facts absent from the
        # service cannot be casually attributed to Friday during synthesis.
        if name.startswith("friday_"):
            return (ground_tool_result(name, result), False)

        return (result, False)

    # ─── Calendar registration helper ────────────────────────

    def register_calendar(self, calendar: "GoogleCalendar"):
        """Register all calendar tools with the brain."""

        self.register(
            name="calendar_list_events",
            description=(
                "List upcoming calendar events. Use for queries like "
                "'what's on my schedule', 'what meetings do I have today/this week'."
            ),
            schema={
                "type": "object",
                "properties": {
                    "hours_ahead": {
                        "type": "integer",
                        "description": "Hours ahead. 24=today, 168=week.",
                    },
                    "days_ahead": {
                        "type": "integer",
                        "description": "Alternative: days to look ahead.",
                    },
                    "max_results": {"type": "integer", "default": 25},
                },
            },
            handler=lambda hours_ahead=24, days_ahead=None, max_results=25: calendar.list_events(
                hours_ahead, days_ahead, max_results
            ),
        )

        self.register(
            name="calendar_create_event",
            description=(
                "Schedule a new calendar event. Accepts natural language times "
                "like 'tomorrow at 2pm' or ISO timestamps. For REPEATING "
                "events, set the recurrence field — ONE call creates the "
                "whole series. Never create multiple individual events to "
                "simulate recurrence."
            ),
            schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "start": {
                        "type": "string",
                        "description": "Start - natural language or ISO.",
                    },
                    "duration_minutes": {"type": "integer", "default": 60},
                    "end": {"type": "string"},
                    "description": {"type": "string"},
                    "location": {"type": "string"},
                    "attendees": {"type": "array", "items": {"type": "string"}},
                    "reminders_minutes": {
                        "type": "array",
                        "items": {"type": "integer"},
                    },
                    "recurrence": {
                        "type": "string",
                        "description": (
                            "Repeat rule for recurring events: 'daily', "
                            "'weekly on Monday', 'every weekday', "
                            "'every 2 weeks', 'monthly', 'yearly', or a raw "
                            "RRULE string. Use this single field for any "
                            "repeating event."
                        ),
                    },
                },
                "required": ["title", "start"],
            },
            handler=lambda **kwargs: calendar.create_event(**kwargs),
        )

        self.register(
            name="calendar_update_event",
            description=(
                "Modify an existing event. Get event_id from list_events first."
            ),
            schema={
                "type": "object",
                "properties": {
                    "event_id": {"type": "string"},
                    "title": {"type": "string"},
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                    "duration_minutes": {"type": "integer"},
                    "description": {"type": "string"},
                    "location": {"type": "string"},
                },
                "required": ["event_id"],
            },
            handler=lambda **kwargs: calendar.update_event(**kwargs),
        )

        self.register(
            name="calendar_delete_event",
            description="Cancel/delete a calendar event.",
            schema={
                "type": "object",
                "properties": {
                    "event_id": {"type": "string"},
                    "notify_attendees": {"type": "boolean", "default": True},
                },
                "required": ["event_id"],
            },
            handler=lambda **kwargs: calendar.delete_event(**kwargs),
        )

        self.register(
            name="calendar_find_free_slots",
            description=(
                "Find open time slots for scheduling. Use for "
                "'when am I free this week', 'find me an hour for X'."
            ),
            schema={
                "type": "object",
                "properties": {
                    "duration_minutes": {"type": "integer", "default": 60},
                    "within_days": {"type": "integer", "default": 7},
                    "working_hours": {
                        "type": "array",
                        "items": {"type": "integer"},
                    },
                },
            },
            handler=lambda **kwargs: calendar.find_free_slots(**kwargs),
        )

    # ─── Defaults ────────────────────────────────────────────

    def _register_defaults(self):
        # Speak tool intentionally not registered. When registered, the
        # brain routes text replies into it instead of returning them in
        # the response field, producing empty API responses.
        pass
