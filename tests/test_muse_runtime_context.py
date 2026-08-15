from types import SimpleNamespace

from orchestrator.brain import JarvisBrain


class FakeMemory:
    def __init__(self):
        self.stored = []

    def retrieve(self, user_input, k=5):
        return ""

    def store(self, user_input, response):
        self.stored.append((user_input, response))


class FakeTools:
    def get_schemas(self):
        return []


class FakeMessages:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(text="Understood.")],
        )


class FakeClient:
    def __init__(self):
        self.messages = FakeMessages()


def make_brain():
    memory = FakeMemory()
    brain = JarvisBrain(
        api_key="test-key",
        memory=memory,
        tools=FakeTools(),
    )
    client = FakeClient()
    brain.client = client
    brain._extract_memorable_facts = lambda *_args: None
    return brain, memory, client


def test_runtime_muse_context_is_ephemeral_system_context():
    brain, memory, client = make_brain()

    result = brain.think_and_act(
        "approve this one",
        runtime_context={
            "muse_review": {
                "project_id": "project-1",
                "artifact_id": "artifact-1",
            }
        },
    )

    assert result == "Understood."

    system_prompt = client.messages.calls[0]["system"]
    assert "project-1" in system_prompt
    assert "artifact-1" in system_prompt
    assert "selection" in system_prompt.lower()
    assert "approval" in system_prompt.lower()

    assert brain.conversation[0] == {
        "role": "user",
        "content": "approve this one",
    }
    assert memory.stored == [
        ("approve this one", "Understood."),
    ]


def test_no_runtime_context_preserves_existing_prompt_behavior():
    brain, _memory, client = make_brain()

    brain.think_and_act("hello")

    system_prompt = client.messages.calls[0]["system"]
    assert "Muse review selection" not in system_prompt


def test_text_input_passes_verified_muse_context_to_brain(monkeypatch):
    import asyncio

    from server import api

    calls = []

    class FakeBrain:
        def think_and_act(self, text, runtime_context=None):
            calls.append(
                {
                    "text": text,
                    "runtime_context": runtime_context,
                }
            )
            return "done"

    async def noop(*_args, **_kwargs):
        return None

    original_widgets = dict(api.last_widget_events)

    try:
        api.last_widget_events.clear()
        api.last_widget_events["muse_review"] = {
            "type": "widget",
            "timestamp": "2026-08-15T00:00:00",
            "data": {
                "widget": "muse_review",
                "data": {
                    "project_id": "project-1",
                    "artifacts": [
                        {
                            "artifact_id": "artifact-1",
                            "candidate_index": 0,
                            "mime_type": "image/png",
                            "width": 1024,
                            "height": 1024,
                        }
                    ],
                },
            },
        }

        monkeypatch.setattr(api, "brain", FakeBrain())
        monkeypatch.setattr(api, "emit_user_speech", noop)
        monkeypatch.setattr(api, "emit_state", noop)
        monkeypatch.setattr(api, "emit_jarvis_speech", noop)
        monkeypatch.setattr(asyncio, "sleep", noop)

        result = asyncio.run(
            api.text_input(
                {
                    "text": "approve this one",
                    "context": {
                        "muse_review": {
                            "project_id": "project-1",
                            "artifact_id": "artifact-1",
                            "storage_uri": "file:///forged/path.png",
                        }
                    },
                }
            )
        )

        assert result == {"response": "done"}
        assert calls == [
            {
                "text": "approve this one",
                "runtime_context": {
                    "muse_review": {
                        "project_id": "project-1",
                        "artifact_id": "artifact-1",
                    }
                },
            }
        ]
    finally:
        api.last_widget_events.clear()
        api.last_widget_events.update(original_widgets)


def test_text_input_without_muse_context_passes_none(monkeypatch):
    import asyncio

    from server import api

    calls = []

    class FakeBrain:
        def think_and_act(self, text, runtime_context=None):
            calls.append(
                {
                    "text": text,
                    "runtime_context": runtime_context,
                }
            )
            return "done"

    async def noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(api, "brain", FakeBrain())
    monkeypatch.setattr(api, "emit_user_speech", noop)
    monkeypatch.setattr(api, "emit_state", noop)
    monkeypatch.setattr(api, "emit_jarvis_speech", noop)
    monkeypatch.setattr(asyncio, "sleep", noop)

    result = asyncio.run(
        api.text_input(
            {
                "text": "hello",
            }
        )
    )

    assert result == {"response": "done"}
    assert calls == [
        {
            "text": "hello",
            "runtime_context": None,
        }
    ]


def test_text_input_rejects_forged_muse_context_before_brain(monkeypatch):
    import asyncio

    import pytest
    from fastapi import HTTPException

    from server import api

    calls = []

    class FakeBrain:
        def think_and_act(self, *_args, **_kwargs):
            calls.append("brain")
            return "should-not-run"

    async def record_user_speech(*_args, **_kwargs):
        calls.append("user_speech")

    async def record_state(*_args, **_kwargs):
        calls.append("state")

    original_widgets = dict(api.last_widget_events)

    try:
        api.last_widget_events.clear()
        api.last_widget_events["muse_review"] = {
            "type": "widget",
            "timestamp": "2026-08-15T00:00:00",
            "data": {
                "widget": "muse_review",
                "data": {
                    "project_id": "project-1",
                    "artifacts": [
                        {
                            "artifact_id": "artifact-1",
                            "candidate_index": 0,
                            "mime_type": "image/png",
                            "width": 1024,
                            "height": 1024,
                        }
                    ],
                },
            },
        }

        monkeypatch.setattr(api, "brain", FakeBrain())
        monkeypatch.setattr(
            api,
            "emit_user_speech",
            record_user_speech,
        )
        monkeypatch.setattr(api, "emit_state", record_state)

        with pytest.raises(HTTPException) as exc:
            asyncio.run(
                api.text_input(
                    {
                        "text": "approve this one",
                        "context": {
                            "muse_review": {
                                "project_id": "project-1",
                                "artifact_id": "forged-artifact",
                            }
                        },
                    }
                )
            )

        assert exc.value.status_code == 400
        assert calls == []
    finally:
        api.last_widget_events.clear()
        api.last_widget_events.update(original_widgets)
