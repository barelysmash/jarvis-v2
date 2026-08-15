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


def _install_muse_review(project_id="project-1", artifact_id="artifact-1"):
    api.last_widget_events["muse_review"] = {
        "type": "widget",
        "timestamp": "2026-08-15T00:00:00",
        "data": {
            "widget": "muse_review",
            "data": {
                "project_id": project_id,
                "artifacts": [
                    {
                        "artifact_id": artifact_id,
                        "candidate_index": 0,
                        "mime_type": "image/png",
                        "width": 1024,
                        "height": 1024,
                    }
                ],
            },
        },
    }


def test_validate_muse_review_context_allows_no_context():
    assert api._validate_muse_review_context({}) is None
    assert api._validate_muse_review_context({"context": {}}) is None


def test_validate_muse_review_context_returns_only_verified_ids():
    original = dict(api.last_widget_events)

    try:
        api.last_widget_events.clear()
        _install_muse_review()

        result = api._validate_muse_review_context(
            {
                "context": {
                    "muse_review": {
                        "project_id": "project-1",
                        "artifact_id": "artifact-1",
                        "storage_uri": "file:///forged/path.png",
                    }
                }
            }
        )

        assert result == {
            "project_id": "project-1",
            "artifact_id": "artifact-1",
        }
    finally:
        api.last_widget_events.clear()
        api.last_widget_events.update(original)


def test_validate_muse_review_context_rejects_wrong_project():
    import pytest
    from fastapi import HTTPException

    original = dict(api.last_widget_events)

    try:
        api.last_widget_events.clear()
        _install_muse_review()

        with pytest.raises(HTTPException) as exc:
            api._validate_muse_review_context(
                {
                    "context": {
                        "muse_review": {
                            "project_id": "forged-project",
                            "artifact_id": "artifact-1",
                        }
                    }
                }
            )

        assert exc.value.status_code == 400
    finally:
        api.last_widget_events.clear()
        api.last_widget_events.update(original)


def test_validate_muse_review_context_rejects_unknown_artifact():
    import pytest
    from fastapi import HTTPException

    original = dict(api.last_widget_events)

    try:
        api.last_widget_events.clear()
        _install_muse_review()

        with pytest.raises(HTTPException) as exc:
            api._validate_muse_review_context(
                {
                    "context": {
                        "muse_review": {
                            "project_id": "project-1",
                            "artifact_id": "forged-artifact",
                        }
                    }
                }
            )

        assert exc.value.status_code == 400
    finally:
        api.last_widget_events.clear()
        api.last_widget_events.update(original)


def test_validate_muse_review_context_rejects_when_review_is_stale():
    import pytest
    from fastapi import HTTPException

    original = dict(api.last_widget_events)

    try:
        api.last_widget_events.clear()

        with pytest.raises(HTTPException) as exc:
            api._validate_muse_review_context(
                {
                    "context": {
                        "muse_review": {
                            "project_id": "project-1",
                            "artifact_id": "artifact-1",
                        }
                    }
                }
            )

        assert exc.value.status_code == 400
    finally:
        api.last_widget_events.clear()
        api.last_widget_events.update(original)
