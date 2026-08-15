from server import api


def test_cache_widget_event_keeps_latest_named_widget():
    original = dict(api.last_widget_events)

    try:
        api.last_widget_events.clear()

        event = {
            "type": "widget",
            "timestamp": "2026-08-15T00:00:00",
            "data": {
                "widget": "muse_review",
                "data": {
                    "project_id": "project-1",
                    "artifacts": [],
                },
            },
        }

        api._cache_widget_event(event)

        assert api.last_widget_events["muse_review"] == event
    finally:
        api.last_widget_events.clear()
        api.last_widget_events.update(original)


def test_cache_widget_event_ignores_non_widget_events():
    original = dict(api.last_widget_events)

    try:
        api.last_widget_events.clear()

        api._cache_widget_event(
            {
                "type": "tool",
                "data": {"widget": "muse_review"},
            }
        )

        assert api.last_widget_events == {}
    finally:
        api.last_widget_events.clear()
        api.last_widget_events.update(original)
