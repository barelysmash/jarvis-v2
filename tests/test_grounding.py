from datetime import date

from orchestrator.grounding import (
    ground_tool_result,
    normalize_near_term_weekdays,
)
from orchestrator.tools import ToolRegistry


def test_friday_tool_result_carries_source_boundary() -> None:
    payload = {"status": "preview", "ticker": "NVDA"}

    rendered = ground_tool_result("friday_live_scan", payload)

    assert "SOURCE BOUNDARY — FRIDAY" in rendered
    assert "Do not invent or infer catalyst dates" in rendered
    assert "'ticker': 'NVDA'" in rendered


def test_non_friday_tool_result_is_plain_string() -> None:
    payload = {"status": "ok"}

    assert ground_tool_result("calendar_list_events", payload) == str(payload)


def test_tool_registry_wraps_successful_friday_result() -> None:
    registry = ToolRegistry()
    registry.register(
        name="friday_live_scan",
        description="test",
        schema={"type": "object", "properties": {}},
        handler=lambda: {"status": "preview", "ticker": "NVDA"},
    )

    result, is_error = registry.execute("friday_live_scan", {})

    assert is_error is False
    assert isinstance(result, str)
    assert "SOURCE BOUNDARY — FRIDAY" in result
    assert "NVDA" in result


def test_registry_preserves_error_semantics_before_grounding() -> None:
    registry = ToolRegistry()
    registry.register(
        name="friday_live_scan",
        description="test",
        schema={"type": "object", "properties": {}},
        handler=lambda: {"error": "provider unavailable"},
    )

    result, is_error = registry.execute("friday_live_scan", {})

    assert is_error is True
    assert result == {"error": "provider unavailable"}


def test_near_term_weekday_is_corrected_from_date() -> None:
    result = normalize_near_term_weekdays(
        "NVDA earnings Tue Aug 26 AMC",
        reference=date(2026, 8, 22),
    )

    assert result == "NVDA earnings Wed Aug 26 AMC"


def test_correct_weekday_is_unchanged() -> None:
    result = normalize_near_term_weekdays(
        "NVDA earnings Wednesday, August 26, 2026 AMC",
        reference=date(2026, 8, 22),
    )

    assert result == "NVDA earnings Wednesday, August 26, 2026 AMC"


def test_explicit_year_is_authoritative_even_when_not_near_reference() -> None:
    result = normalize_near_term_weekdays(
        "Historical note: Tue August 26, 2026",
        reference=date(2030, 1, 1),
    )

    assert result == "Historical note: Wed August 26, 2026"


def test_far_implicit_date_is_left_ambiguous() -> None:
    result = normalize_near_term_weekdays(
        "Archive says Tue Feb 1",
        reference=date(2026, 8, 22),
        maximum_implicit_distance_days=30,
    )

    assert result == "Archive says Tue Feb 1"
