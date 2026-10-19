"""Cockpit backend: event log schema, brain instrumentation, ingest, schedule."""

from __future__ import annotations

import os
import sqlite3
from types import SimpleNamespace
from typing import Any

import pytest

from orchestrator import event_log


@pytest.fixture(autouse=True)
def isolated_log(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(event_log, "_schema_ready_for", None)
    yield tmp_path


# ─── event_log ──────────────────────────────────────────────


def test_emit_migrates_legacy_table_and_tags_process(tmp_path):
    # A pre-cockpit events.db without pid/proc columns.
    conn = sqlite3.connect(tmp_path / "events.db")
    conn.execute(
        "CREATE TABLE tool_events (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "ts REAL NOT NULL, source TEXT NOT NULL, event_type TEXT NOT NULL, "
        "payload TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO tool_events (ts, source, event_type, payload) "
        "VALUES (1.0, 'brain', 'tool', '{}')"
    )
    conn.commit()
    conn.close()

    row_id = event_log.emit("brain", "tool", {"name": "x"})

    rows = event_log.fetch_tail(10)
    assert [r["id"] for r in rows] == [1, row_id]
    assert rows[0]["pid"] is None
    assert rows[1]["pid"] == os.getpid()
    assert rows[1]["proc"]


def test_fetch_after_id_is_gap_free_and_ordered():
    ids = [event_log.emit("t", "agent", {"n": i}) for i in range(5)]
    assert [r["payload"]["n"] for r in event_log.fetch_after_id(ids[1])] == [2, 3, 4]
    assert [r["id"] for r in event_log.fetch_tail(2)] == ids[-2:]


def test_large_payload_is_clipped():
    event_log.emit("brain", "tool", {"result": "x" * 100_000, "name": "big"})
    row = event_log.fetch_tail(1)[0]
    assert row["payload"]["name"] == "big"
    assert len(row["payload"]["result"]) < 10_000


def test_job_context_emits_start_and_end_with_error():
    with pytest.raises(ValueError), event_log.job("briefing", style="quick"):
        raise ValueError("boom")
    start, end = event_log.fetch_tail(2)
    assert start["payload"] == {"job": "briefing", "phase": "start", "style": "quick"}
    assert end["payload"]["ok"] is False
    assert "boom" in end["payload"]["error"]


# ─── brain instrumentation ──────────────────────────────────


class _Block(SimpleNamespace):
    pass


class _FakeMessages:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses

    def create(self, **_: Any) -> Any:
        return self.responses.pop(0)


class _FakeMemory:
    def retrieve(self, *_: Any, **__: Any) -> str:
        return ""

    def store(self, *_: Any) -> None:
        pass

    def remember_fact(self, *_: Any) -> None:
        pass


def test_brain_turn_emits_turn_llm_and_attributed_tool_events():
    from orchestrator.brain import JarvisBrain
    from orchestrator.tools import ToolRegistry

    tools = ToolRegistry()
    tools._owner_ctx = "friday"
    tools.register("friday_scan", "scan", {"type": "object"}, lambda: {"hits": 2})
    tools._owner_ctx = None

    brain = JarvisBrain(api_key="test", memory=_FakeMemory(), tools=tools, origin="hud")
    usage = SimpleNamespace(input_tokens=100, output_tokens=20)
    brain.client = SimpleNamespace(messages=_FakeMessages([
        SimpleNamespace(
            stop_reason="tool_use", model="m", usage=usage,
            content=[
                _Block(type="text", text="Checking."),
                _Block(type="tool_use", id="tu_1", name="friday_scan", input={}),
            ],
        ),
        SimpleNamespace(
            stop_reason="end_turn", model="m", usage=usage,
            content=[_Block(type="text", text="Two hits.")],
        ),
        # fact extraction call
        SimpleNamespace(content=[_Block(type="text", text="[]")]),
    ]))

    assert brain.think_and_act("scan please") == "Two hits."

    events = event_log.fetch_tail(50)
    types = [e["type"] for e in events]
    assert types == ["turn.start", "llm", "tool", "tool", "llm", "turn.end"]
    turn_id = events[0]["payload"]["turn_id"]
    assert all(e["payload"]["turn_id"] == turn_id for e in events)
    assert events[0]["payload"]["origin"] == "hud"
    assert events[1]["payload"]["text"] == "Checking."
    assert events[1]["payload"]["tool_calls"] == ["friday_scan"]
    done = events[3]["payload"]
    assert done["agent"] == "friday"
    assert done["status"] == "success"
    assert done["call_id"] == "tu_1"
    assert "ms" in done and "hits" in str(done["result"])
    end = events[-1]["payload"]
    assert end["text"] == "Two hits."
    assert end["tools"] == 1 and end["iterations"] == 2
    assert end["input_tokens"] == 200


def test_tool_registry_list_alias_for_briefing_collectors():
    from orchestrator.tools import ToolRegistry

    tools = ToolRegistry()
    tools.register("a", "a", {"type": "object"}, lambda: 1)
    assert tools.list() == ["a"]
    assert tools.owner_of("a") == "jarvis"


# ─── HTTP surface ───────────────────────────────────────────


@pytest.fixture
def client(monkeypatch):
    for key in ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REFRESH_TOKEN"):
        monkeypatch.setenv(key, "x")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from server.cockpit import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_ingest_requires_configured_token(client, monkeypatch):
    monkeypatch.delenv("JARVIS_EVENTS_TOKEN", raising=False)
    body = {"source": "friday", "summary": "x"}
    assert client.post("/api/events", json=body).status_code == 503

    monkeypatch.setenv("JARVIS_EVENTS_TOKEN", "s3cret")
    assert client.post("/api/events", json=body).status_code == 401
    ok = client.post(
        "/api/events",
        json={**body, "kind": "position_opened", "severity": "LOUD"},
        headers={"Authorization": "Bearer s3cret"},
    )
    assert ok.status_code == 200
    row = event_log.fetch_tail(1)[0]
    assert row["type"] == "agent"
    assert row["payload"]["kind"] == "position_opened"
    assert row["payload"]["severity"] == "info"  # unknown severity normalised


def test_cockpit_ws_backfills_then_tails(client):
    event_log.emit("brain", "turn.start", {"turn_id": "a"})
    with client.websocket_connect("/ws/cockpit?backfill=10") as ws:
        assert ws.receive_json()["type"] == "hello"
        backfill = ws.receive_json()
        assert [e["type"] for e in backfill["events"]] == ["turn.start"]
        event_log.emit("friday", "agent", {"summary": "live"})
        live = ws.receive_json()
        assert live["type"] == "events"
        assert live["events"][0]["payload"]["summary"] == "live"

    last = backfill["events"][-1]["id"]
    with client.websocket_connect(f"/ws/cockpit?after_id={last}") as ws:
        assert ws.receive_json()["mode"] == "resume"
        assert [e["payload"].get("summary") for e in ws.receive_json()["events"]] == [
            "live"
        ]


# ─── schedule parsing ───────────────────────────────────────


SHOW_OUTPUT = """Id=jarvis-briefing.timer
Triggers=jarvis-briefing.service
NextElapseUSecRealtime=@1759491900
LastTriggerUSec=@1759405500
TimersCalendar={ OnCalendar=Mon..Fri *-*-* 06:45:00 ; next_elapse=@1759491900 }
TimersMonotonic=
UnitFileState=enabled
ActiveState=active

Id=jarvis-sleep.timer
Triggers=jarvis-sleep.service
NextElapseUSecRealtime=n/a
LastTriggerUSec=n/a
TimersCalendar={ OnCalendar=*-*-* 03:00:00 ; next_elapse=n/a }
TimersMonotonic={ OnUnitActiveSec=15min ; next_elapse=0 }
UnitFileState=disabled
ActiveState=inactive
"""


def test_schedule_parsing_helpers():
    from server.cockpit import describe_schedule, parse_show, parse_ts

    blocks = parse_show(SHOW_OUTPUT)
    assert [b["Id"] for b in blocks] == ["jarvis-briefing.timer", "jarvis-sleep.timer"]
    assert parse_ts(blocks[0]["NextElapseUSecRealtime"]) == 1759491900.0
    assert parse_ts(blocks[1]["NextElapseUSecRealtime"]) is None
    first = describe_schedule(blocks[0]["TimersCalendar"], "")
    assert first == "Mon..Fri *-*-* 06:45:00"
    assert describe_schedule(
        blocks[1]["TimersCalendar"], blocks[1]["TimersMonotonic"]
    ) == "*-*-* 03:00:00 | OnUnitActiveSec 15min"


def test_run_now_rejects_unknown_timer(client, monkeypatch):
    from server import cockpit

    monkeypatch.setattr(cockpit, "collect_schedule", lambda: [
        {"timer": "jarvis-briefing.timer", "service": "jarvis-briefing.service"}
    ])
    started: list[tuple[str, ...]] = []
    monkeypatch.setattr(cockpit, "_systemctl", lambda *a: started.append(a) or "")

    assert client.post("/api/cockpit/schedule/rm-rf.timer/run").status_code == 404
    assert started == []
    ok = client.post("/api/cockpit/schedule/jarvis-briefing.timer/run")
    assert ok.status_code == 200
    assert started == [("start", "--no-block", "jarvis-briefing.service")]
