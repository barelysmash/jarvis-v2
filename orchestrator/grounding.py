"""Deterministic grounding helpers for model-facing tool results and output."""

from __future__ import annotations

import re
from datetime import date, datetime


_FRIDAY_SOURCE_BOUNDARY = """SOURCE BOUNDARY — FRIDAY
Treat only facts explicitly present in the Friday payload below as facts reported by Friday.
Do not invent or infer catalyst dates, earnings dates, weekday labels, event details, or risk-gate reasons and attribute them to Friday.
If a requested fact is absent, say that Friday did not provide it. If external information is needed, use another source and label it separately.
"""

_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}

_WEEKDAYS_FULL = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)
_WEEKDAYS_SHORT = tuple(day[:3] for day in _WEEKDAYS_FULL)

_WEEKDAY_DATE_RE = re.compile(
    r"\b(?P<weekday>Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|"
    r"Mon|Tue|Wed|Thu|Fri|Sat|Sun)"
    r"(?P<separator>,?\s+)"
    r"(?P<month>January|February|March|April|May|June|July|August|September|Sept|"
    r"October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
    r"\s+(?P<day>\d{1,2})"
    r"(?P<year_suffix>,?\s+(?P<year>\d{4}))?\b",
    re.IGNORECASE,
)


def ground_tool_result(tool_name: str, result: object) -> str:
    """Wrap source-bound tool output with model-facing provenance constraints."""

    rendered = str(result)
    if tool_name.startswith("friday_"):
        return f"{_FRIDAY_SOURCE_BOUNDARY}\nFriday payload:\n{rendered}"
    return rendered


def normalize_near_term_weekdays(
    text: str,
    *,
    reference: date | datetime,
    maximum_implicit_distance_days: int = 183,
) -> str:
    """Correct weekday labels attached to explicit or near-term month/day dates.

    Explicit years are always authoritative. When the year is omitted, choose the
    closest matching calendar date across the adjacent three years and only
    normalize it when that date is near the supplied reference date. This avoids
    rewriting ambiguous historical prose while protecting near-term operational
    and market dates.
    """

    reference_date = reference.date() if isinstance(reference, datetime) else reference

    def replace(match: re.Match[str]) -> str:
        month = _MONTHS[match.group("month").lower()]
        day = int(match.group("day"))
        explicit_year = match.group("year")

        target: date | None
        if explicit_year is not None:
            try:
                target = date(int(explicit_year), month, day)
            except ValueError:
                return match.group(0)
        else:
            candidates: list[date] = []
            for year in (
                reference_date.year - 1,
                reference_date.year,
                reference_date.year + 1,
            ):
                try:
                    candidates.append(date(year, month, day))
                except ValueError:
                    continue

            if not candidates:
                return match.group(0)

            target = min(
                candidates,
                key=lambda candidate: abs((candidate - reference_date).days),
            )
            if abs((target - reference_date).days) > maximum_implicit_distance_days:
                return match.group(0)

        observed_weekday = match.group("weekday")
        expected = (
            _WEEKDAYS_FULL[target.weekday()]
            if len(observed_weekday) > 3
            else _WEEKDAYS_SHORT[target.weekday()]
        )

        if observed_weekday.isupper():
            expected = expected.upper()
        elif observed_weekday.islower():
            expected = expected.lower()

        if expected.lower() == observed_weekday.lower():
            return match.group(0)

        start, end = match.span("weekday")
        whole = match.group(0)
        relative_start = start - match.start()
        relative_end = end - match.start()
        return whole[:relative_start] + expected + whole[relative_end:]

    return _WEEKDAY_DATE_RE.sub(replace, text)
