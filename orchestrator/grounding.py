"""Deterministic grounding helpers for model-facing tool results and output."""

from __future__ import annotations

import datetime
import re

_FRIDAY_SOURCE_BOUNDARY = (
    "SOURCE BOUNDARY — FRIDAY\n"
    "Treat only facts explicitly present in the Friday payload below as facts "
    "reported by Friday.\n"
    "Do not invent or infer catalyst dates, earnings dates, weekday labels, "
    "event details, or risk-gate reasons and attribute them to Friday.\n"
    "If a requested fact is absent, say that Friday did not provide it. If "
    "external information is needed, use another source and label it separately.\n"
)

_ATLAS_SOURCE_BOUNDARY = (
    "SOURCE BOUNDARY — ATLAS\n"
    "Atlas reports relative figures only: percent changes, percentage-point "
    "deltas, directions and night counts.\n"
    "Do not state, estimate, back-calculate or recall any absolute dollar "
    "amount, cover count, labor total or rate level for the restaurant, from "
    "this payload, from memory or from an earlier conversation. If asked for "
    "one, say that Atlas withholds absolute figures.\n"
    "That includes size by implication: Atlas does not say how large or small "
    "any figure is, so do not describe a change as coming off a small, large, "
    "low or high base, and do not rank metrics by size.\n"
    "Treat only changes explicitly present in the payload below as reported by "
    "Atlas. A change of null means Atlas has no data for it; do not fill it "
    "in.\n"
    "Report what moved, not why. Do not attach a cause, an explanation or a "
    "judgement about service, staff or guests to a change (for example "
    "'cleaner service') unless Atlas provides it. You may say which changes "
    "look worth a closer look, labelled as your own read.\n"
    "Atlas reports no margin, profit or cost other than labor. Do not call a "
    "period margin-positive, more or less profitable, or better or worse for "
    "the bottom line.\n"
    "A change belongs to the two periods it compares. Do not link it to a "
    "change from an earlier answer that compared different periods (for "
    "example 'the October spike is cooling').\n"
    "Each period carries nights_missing and nights_in_review. When either is "
    "above zero, say so plainly in one clause, with the count and which "
    "period (for example 'one November night is missing from the ledger'). "
    "A missing night is absent from every change shown. When they are null, "
    "coverage is unknown: do not claim the period is complete.\n"
    "Atlas offers exactly two tools: a comparison of two periods and a "
    "freshness check. Do not offer nightly detail, a single night's figures, "
    "item-level or check-level data, or any other drill-down; if asked, say "
    "Atlas does not expose it. You may offer a different pair of periods.\n"
)

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


def normalize_near_term_weekdays(
    text: str,
    *,
    reference: datetime.date | datetime.datetime,
    maximum_implicit_distance_days: int = 183,
) -> str:
    """Correct weekday labels attached to explicit or near-term month/day dates.

    Explicit years are always authoritative. When the year is omitted, choose the
    closest matching calendar date across the adjacent three years and only
    normalize it when that date is near the supplied reference date. This avoids
    rewriting ambiguous historical prose while protecting near-term operational
    and market dates.
    """

    reference_date = (
        reference.date()
        if isinstance(reference, datetime.datetime)
        else reference
    )

    def replace(match: re.Match[str]) -> str:
        month = _MONTHS[match.group("month").lower()]
        day = int(match.group("day"))
        explicit_year = match.group("year")

        target: datetime.date | None
        if explicit_year is not None:
            try:
                target = datetime.date(int(explicit_year), month, day)
            except ValueError:
                return match.group(0)
        else:
            candidates: list[datetime.date] = []
            for year in (
                reference_date.year - 1,
                reference_date.year,
                reference_date.year + 1,
            ):
                try:
                    candidates.append(datetime.date(year, month, day))
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


def ground_tool_result(tool_name: str, result: object) -> str:
    """Wrap source-bound tool output with provenance and calendar constraints."""

    rendered = str(result)
    if tool_name.startswith("friday_"):
        rendered = normalize_near_term_weekdays(
            rendered,
            reference=datetime.date.today(),
        )
        return f"{_FRIDAY_SOURCE_BOUNDARY}\nFriday payload:\n{rendered}"
    if tool_name.startswith("atlas_"):
        return f"{_ATLAS_SOURCE_BOUNDARY}\nAtlas payload:\n{rendered}"
    return rendered
