"""Google Calendar integration with full CRUD and natural-language times."""

from datetime import datetime, timedelta
from typing import Optional

from dateutil import parser as date_parser
from dateutil.tz import gettz
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .google_auth import GoogleAuth


class GoogleCalendar:
    """Real Google Calendar integration with NL-friendly methods."""

    def __init__(
        self,
        calendar_id: str = "primary",
        timezone_name: str = "America/Chicago",
    ):
        self.calendar_id = calendar_id
        self.tz = gettz(timezone_name)
        self.timezone_name = timezone_name
        self.auth = GoogleAuth()
        self._service = None

    @property
    def service(self):
        """Lazy-init service so auth doesn't run until first call."""
        if self._service is None:
            creds = self.auth.get_credentials()
            self._service = build(
                "calendar", "v3", credentials=creds, cache_discovery=False
            )
        return self._service

    # ─── LIST ─────────────────────────────────────────────────

    def list_events(
        self,
        hours_ahead: int = 24,
        days_ahead: Optional[int] = None,
        max_results: int = 25,
    ) -> list[dict]:
        """List upcoming events in a clean format for the brain."""
        now = datetime.now(self.tz)

        if days_ahead:
            time_max = now + timedelta(days=days_ahead)
        else:
            time_max = now + timedelta(hours=hours_ahead)

        try:
            result = (
                self.service.events()
                .list(
                    calendarId=self.calendar_id,
                    timeMin=now.isoformat(),
                    timeMax=time_max.isoformat(),
                    maxResults=max_results,
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
        except HttpError as exc:
            return [{"error": f"Calendar fetch failed: {exc}"}]

        return [self._format_event(e) for e in result.get("items", [])]

    # ─── RECURRENCE ───────────────────────────────────────────

    _RRULE_DAYS = {
        "monday": "MO", "tuesday": "TU", "wednesday": "WE",
        "thursday": "TH", "friday": "FR", "saturday": "SA", "sunday": "SU",
        "mon": "MO", "tue": "TU", "tues": "TU", "wed": "WE",
        "thu": "TH", "thur": "TH", "thurs": "TH", "fri": "FR",
        "sat": "SA", "sun": "SU",
    }

    def _build_rrule(self, recurrence: str) -> Optional[str]:
        """Translate natural language into an RRULE. Raw RRULEs pass through.

        Returns None when the phrase can't be translated — callers should
        surface that as an error rather than silently creating a one-off.
        """
        import re

        text = recurrence.strip()
        upper = text.upper()
        if upper.startswith("RRULE:"):
            return upper
        if upper.startswith("FREQ="):
            return f"RRULE:{upper}"

        t = text.lower()

        m = re.search(r"every\s+(\d+)\s+(day|week|month|year)", t)
        if m:
            freq = {"day": "DAILY", "week": "WEEKLY",
                    "month": "MONTHLY", "year": "YEARLY"}[m.group(2)]
            return f"RRULE:FREQ={freq};INTERVAL={m.group(1)}"

        if "every other week" in t or "biweekly" in t or "fortnight" in t:
            return "RRULE:FREQ=WEEKLY;INTERVAL=2"
        if "weekday" in t:
            return "RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"
        if "weekend" in t:
            return "RRULE:FREQ=WEEKLY;BYDAY=SA,SU"

        if "daily" in t or "every day" in t:
            return "RRULE:FREQ=DAILY"
        if "yearly" in t or "annual" in t or "every year" in t:
            return "RRULE:FREQ=YEARLY"
        if "monthly" in t or "every month" in t:
            return "RRULE:FREQ=MONTHLY"

        days = []
        seen = set()
        for name, code in self._RRULE_DAYS.items():
            if re.search(rf"\b{name}\b", t) and code not in seen:
                seen.add(code)
                days.append(code)
        if days:
            order = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
            days.sort(key=order.index)
            return f"RRULE:FREQ=WEEKLY;BYDAY={','.join(days)}"

        if "weekly" in t or "every week" in t:
            return "RRULE:FREQ=WEEKLY"
        return None

    # ─── CREATE ───────────────────────────────────────────────

    def create_event(
        self,
        title: str,
        start: str,
        duration_minutes: int = 60,
        end: Optional[str] = None,
        description: Optional[str] = None,
        location: Optional[str] = None,
        attendees: Optional[list[str]] = None,
        reminders_minutes: Optional[list[int]] = None,
        recurrence: Optional[str] = None,
    ) -> dict:
        """Create a calendar event. `recurrence` makes it a repeating
        series: natural language ('weekly on Monday', 'every weekday',
        'every 2 weeks', 'monthly') or a raw RRULE string."""
        start_dt = self._parse_time(start)
        if not start_dt:
            return {"error": f"Could not parse start time: {start}"}

        if end:
            end_dt = self._parse_time(end)
            if not end_dt:
                return {"error": f"Could not parse end time: {end}"}
        else:
            end_dt = start_dt + timedelta(minutes=duration_minutes)

        event_body: dict = {
            "summary": title,
            "start": {
                "dateTime": start_dt.isoformat(),
                "timeZone": self.timezone_name,
            },
            "end": {
                "dateTime": end_dt.isoformat(),
                "timeZone": self.timezone_name,
            },
        }

        if description:
            event_body["description"] = description
        if location:
            event_body["location"] = location
        if attendees:
            event_body["attendees"] = [{"email": e} for e in attendees]
        if reminders_minutes:
            event_body["reminders"] = {
                "useDefault": False,
                "overrides": [
                    {"method": "popup", "minutes": m}
                    for m in reminders_minutes
                ],
            }

        if recurrence:
            rrule = self._build_rrule(recurrence)
            if not rrule:
                return {"error": f"Could not parse recurrence: {recurrence}"}
            event_body["recurrence"] = [rrule]

        try:
            created = (
                self.service.events()
                .insert(
                    calendarId=self.calendar_id,
                    body=event_body,
                    sendUpdates="all" if attendees else "none",
                )
                .execute()
            )
            return {
                "status": "created",
                "id": created["id"],
                "title": created["summary"],
                "start": created["start"]["dateTime"],
                "end": created["end"]["dateTime"],
                "link": created.get("htmlLink"),
            }
        except HttpError as exc:
            return {"error": f"Failed to create event: {exc}"}

    # ─── UPDATE ───────────────────────────────────────────────

    def update_event(
        self,
        event_id: str,
        title: Optional[str] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        duration_minutes: Optional[int] = None,
        description: Optional[str] = None,
        location: Optional[str] = None,
    ) -> dict:
        """Update an existing event. Only provided fields change."""
        try:
            event = (
                self.service.events()
                .get(calendarId=self.calendar_id, eventId=event_id)
                .execute()
            )
        except HttpError as exc:
            return {"error": f"Event not found: {exc}"}

        if title:
            event["summary"] = title
        if description:
            event["description"] = description
        if location:
            event["location"] = location

        if start:
            start_dt = self._parse_time(start)
            if not start_dt:
                return {"error": f"Could not parse start: {start}"}

            if not end and not duration_minutes:
                # Preserve original duration
                old_start = date_parser.isoparse(event["start"]["dateTime"])
                old_end = date_parser.isoparse(event["end"]["dateTime"])
                duration = old_end - old_start
                end_dt = start_dt + duration
            elif duration_minutes:
                end_dt = start_dt + timedelta(minutes=duration_minutes)
            else:
                end_dt = self._parse_time(end)

            event["start"] = {
                "dateTime": start_dt.isoformat(),
                "timeZone": self.timezone_name,
            }
            event["end"] = {
                "dateTime": end_dt.isoformat(),
                "timeZone": self.timezone_name,
            }
        elif end:
            end_dt = self._parse_time(end)
            event["end"] = {
                "dateTime": end_dt.isoformat(),
                "timeZone": self.timezone_name,
            }

        try:
            updated = (
                self.service.events()
                .update(
                    calendarId=self.calendar_id,
                    eventId=event_id,
                    body=event,
                    sendUpdates="all",
                )
                .execute()
            )
            return {
                "status": "updated",
                "id": updated["id"],
                "title": updated["summary"],
                "start": updated["start"]["dateTime"],
                "end": updated["end"]["dateTime"],
            }
        except HttpError as exc:
            return {"error": f"Failed to update: {exc}"}

    # ─── DELETE ───────────────────────────────────────────────

    def delete_event(
        self, event_id: str, notify_attendees: bool = True
    ) -> dict:
        try:
            self.service.events().delete(
                calendarId=self.calendar_id,
                eventId=event_id,
                sendUpdates="all" if notify_attendees else "none",
            ).execute()
            return {"status": "deleted", "id": event_id}
        except HttpError as exc:
            return {"error": f"Failed to delete: {exc}"}

    # ─── FREE SLOTS ───────────────────────────────────────────

    def find_free_slots(
        self,
        duration_minutes: int = 60,
        within_days: int = 7,
        working_hours: tuple = (9, 17),
        days_of_week: Optional[list[int]] = None,
    ) -> list[dict]:
        """Find open slots of at least `duration_minutes`.

        days_of_week: 0=Monday, 6=Sunday. Default: weekdays only.
        """
        if days_of_week is None:
            days_of_week = [0, 1, 2, 3, 4]

        now = datetime.now(self.tz)
        time_max = now + timedelta(days=within_days)

        try:
            freebusy = (
                self.service.freebusy()
                .query(
                    body={
                        "timeMin": now.isoformat(),
                        "timeMax": time_max.isoformat(),
                        "items": [{"id": self.calendar_id}],
                        "timeZone": self.timezone_name,
                    }
                )
                .execute()
            )
        except HttpError as exc:
            return [{"error": f"Free/busy query failed: {exc}"}]

        busy = freebusy["calendars"][self.calendar_id].get("busy", [])
        busy_intervals = [
            (
                date_parser.isoparse(b["start"]),
                date_parser.isoparse(b["end"]),
            )
            for b in busy
        ]

        slots = []
        current = now.replace(minute=0, second=0, microsecond=0)
        if current < now:
            current += timedelta(hours=1)

        while current < time_max and len(slots) < 10:
            if (
                current.weekday() not in days_of_week
                or current.hour < working_hours[0]
                or current.hour >= working_hours[1]
            ):
                current += timedelta(hours=1)
                continue

            slot_end = current + timedelta(minutes=duration_minutes)

            overlaps = any(
                current < busy_end and slot_end > busy_start
                for busy_start, busy_end in busy_intervals
            )

            if not overlaps and slot_end.hour <= working_hours[1]:
                slots.append(
                    {
                        "start": current.isoformat(),
                        "end": slot_end.isoformat(),
                        "human": current.strftime(
                            "%A %b %-d at %-I:%M %p"
                        ),
                    }
                )

            current += timedelta(minutes=30)

        return slots

    # ─── HELPERS ──────────────────────────────────────────────

    def _parse_time(self, time_str: str) -> Optional[datetime]:
        """Parse natural-language or ISO time into tz-aware datetime.

        Strategy: try ISO first (fastest, deterministic), then fall back to
        dateparser for natural language ('tomorrow at 3pm', 'Friday 5pm', etc.).
        Avoids dateutil.parser.parse with fuzzy=True, which silently substitutes
        defaults for unparseable tokens and produces drift bugs.
        """
        # Try ISO first
        try:
            dt = date_parser.isoparse(time_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=self.tz)
            return dt
        except (ValueError, TypeError):
            pass

        # Fall back to dateparser for natural language
        import dateparser
        dt = dateparser.parse(
            time_str,
            settings={
                "TIMEZONE": self.timezone_name,
                "RETURN_AS_TIMEZONE_AWARE": True,
                "PREFER_DATES_FROM": "future",
            },
        )
        if dt and dt.tzinfo is None:
            dt = dt.replace(tzinfo=self.tz)
        return dt

    def _format_event(self, event: dict) -> dict:
        """Return event in a brain-friendly shape."""
        start = event.get("start", {})
        end = event.get("end", {})

        start_time = start.get("dateTime", start.get("date", ""))
        end_time = end.get("dateTime", end.get("date", ""))

        try:
            dt = date_parser.isoparse(start_time)
            human_time = dt.strftime("%A %b %-d at %-I:%M %p")
        except (ValueError, TypeError):
            human_time = start_time

        description = event.get("description")
        return {
            "id": event["id"],
            "title": event.get("summary", "(no title)"),
            "start": start_time,
            "end": end_time,
            "human_time": human_time,
            "location": event.get("location"),
            "description": description[:200] if description else None,
            "attendees": [
                a.get("email") for a in event.get("attendees", [])
            ],
            "is_all_day": "date" in start,
            "link": event.get("htmlLink"),
        }
