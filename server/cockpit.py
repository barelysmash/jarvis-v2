"""Cockpit API: the second-screen, full-detail view of JARVIS.

The HUD shows a curated, glanceable slice of what JARVIS is doing. The
cockpit shows everything, for watching (and later automating) the system:

  /ws/cockpit                      live tail of the cross-process event log
  GET  /api/cockpit/events         same rows over HTTP (backfill/debug)
  GET  /api/cockpit/agents         sub-agent registry + load status + tools
  GET  /api/cockpit/schedule       systemd --user timers, next/last run, result
  POST /api/cockpit/schedule/{t}/run   start a timer's service now
  POST /api/events                 sub-agents push key changes (token auth)

Everything the cockpit renders comes from orchestrator.event_log, so
events from the API process, the briefing job, the sleep cycle and any
sub-agent posting to /api/events all land in one ordered stream.
"""

from __future__ import annotations

import asyncio
import fnmatch
import hmac
import logging
import os
import re
import subprocess
import time
from typing import Any

from fastapi import APIRouter, Header, HTTPException, WebSocket, WebSocketDisconnect

from orchestrator import event_log

logger = logging.getLogger(__name__)

router = APIRouter()

BACKFILL_DEFAULT = 600
BACKFILL_MAX = 5000
TAIL_INTERVAL_S = 0.25

SEVERITIES = {"debug", "info", "notice", "warning", "error", "critical"}


# ─── Event feed ──────────────────────────────────────────────


@router.get("/api/cockpit/events")
async def cockpit_events(
    after_id: int | None = None, limit: int = 200
) -> dict[str, Any]:
    limit = max(1, min(limit, BACKFILL_MAX))
    if after_id is None:
        rows = await asyncio.to_thread(event_log.fetch_tail, limit)
    else:
        rows = await asyncio.to_thread(event_log.fetch_after_id, after_id, limit)
    return {"events": rows, "last_id": rows[-1]["id"] if rows else after_id}


@router.websocket("/ws/cockpit")
async def cockpit_ws(ws: WebSocket) -> None:
    """Backfill, then tail the event log by id.

    Query params: ``after_id`` resumes after a reconnect without gaps or
    duplicates; ``backfill`` sets how many rows to send on a fresh connect.
    """
    await ws.accept()
    params = ws.query_params
    try:
        backfill = int(params.get("backfill", BACKFILL_DEFAULT))
    except ValueError:
        backfill = BACKFILL_DEFAULT
    backfill = max(0, min(backfill, BACKFILL_MAX))

    after_raw = params.get("after_id") or ""
    if after_raw.isdigit():
        mode = "resume"
        rows = await asyncio.to_thread(
            event_log.fetch_after_id, int(after_raw), BACKFILL_MAX
        )
        last_id = rows[-1]["id"] if rows else int(after_raw)
    else:
        mode = "fresh"
        rows = (
            await asyncio.to_thread(event_log.fetch_tail, backfill)
            if backfill else []
        )
        if rows:
            last_id = rows[-1]["id"]
        else:
            latest = await asyncio.to_thread(event_log.fetch_tail, 1)
            last_id = latest[-1]["id"] if latest else 0

    try:
        await ws.send_json({
            "type": "hello",
            "mode": mode,
            "server_time": time.time(),
            "pid": os.getpid(),
        })
        await ws.send_json({"type": "backfill", "events": rows})
        while True:
            try:
                # Wake on client messages (ping) or every tick to poll.
                msg = await asyncio.wait_for(ws.receive_text(), TAIL_INTERVAL_S)
                if msg == "ping":
                    await ws.send_json({"type": "pong", "server_time": time.time()})
                continue
            except TimeoutError:
                pass
            fresh = await asyncio.to_thread(event_log.fetch_after_id, last_id, 500)
            if fresh:
                last_id = fresh[-1]["id"]
                await ws.send_json({"type": "events", "events": fresh})
    except (WebSocketDisconnect, RuntimeError):
        return


# ─── Agents ──────────────────────────────────────────────────


@router.get("/api/cockpit/agents")
async def cockpit_agents() -> dict[str, Any]:
    from orchestrator.agents import AGENT_STATUS
    from server import api as api_module

    owners: dict[str, list[str]] = {}
    registry = getattr(api_module, "tools", None)
    if registry is not None and hasattr(registry, "owners"):
        owners = registry.owners()

    agents: dict[str, Any] = {}
    for name, entry in AGENT_STATUS.items():
        tool_names = owners.get(name, entry.get("tools", []))
        agents[name] = {**entry, "tools": sorted(tool_names)}
    # Built-ins that aren't in agents.yaml (calendar, core jarvis tools).
    for owner, names in owners.items():
        if owner not in agents:
            agents[owner] = {
                "status": "loaded",
                "transport": "builtin",
                "description": "",
                "detail": "",
                "tools": sorted(names),
                "ts": None,
            }
    brain = getattr(api_module, "brain", None)
    return {
        "agents": agents,
        "brain": {
            "online": brain is not None,
            "model": getattr(brain, "model", None),
            "history": len(getattr(brain, "conversation", []) or []),
            "turn_id": getattr(brain, "_turn_id", None),
        },
    }


# ─── Schedule (systemd --user timers) ───────────────────────


class SystemctlError(RuntimeError):
    pass


def _systemctl(*args: str) -> str:
    try:
        proc = subprocess.run(
            ["systemctl", "--user", "--no-pager", *args],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except FileNotFoundError as exc:
        raise SystemctlError("systemctl not available on this host") from exc
    except subprocess.TimeoutExpired as exc:
        raise SystemctlError("systemctl timed out") from exc
    if proc.returncode != 0 and not proc.stdout:
        raise SystemctlError(proc.stderr.strip() or f"systemctl exit {proc.returncode}")
    return proc.stdout


def parse_show(output: str) -> list[dict[str, str]]:
    """Parse `systemctl show a b c` output (blank-line separated blocks)."""
    blocks: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in output.splitlines():
        if not line.strip():
            if current:
                blocks.append(current)
                current = {}
            continue
        key, sep, value = line.partition("=")
        if sep:
            current[key] = value
    if current:
        blocks.append(current)
    return blocks


def parse_ts(value: str | None) -> float | None:
    """'@1696339500' (or '@1696339500.123') -> epoch seconds; else None."""
    if not value or value in {"n/a", "0"}:
        return None
    if value.startswith("@"):
        try:
            return float(value[1:])
        except ValueError:
            return None
    return None


_ONCAL = re.compile(r"OnCalendar=([^;]+?)\s*;")
_ONMONO = re.compile(r"\{\s*(On\w+Sec)=([^;]+?)\s*;")


def describe_schedule(calendar: str, monotonic: str) -> str:
    parts = [m.strip() for m in _ONCAL.findall(calendar or "")]
    parts += [f"{k} {v.strip()}" for k, v in _ONMONO.findall(monotonic or "")]
    return " | ".join(parts)


def _timer_globs() -> list[str]:
    raw = os.environ.get("JARVIS_COCKPIT_TIMERS", "*.timer")
    return [g.strip() for g in raw.split(",") if g.strip()]


def _list_timer_names() -> list[str]:
    out = _systemctl("list-unit-files", "--type=timer", "--no-legend", "--plain")
    names = []
    globs = _timer_globs()
    for line in out.splitlines():
        parts = line.split()
        if not parts or not parts[0].endswith(".timer"):
            continue
        name = parts[0]
        if any(fnmatch.fnmatch(name, g) for g in globs):
            names.append(name)
    return sorted(set(names))


def _show(units: list[str], props: list[str]) -> list[dict[str, str]]:
    if not units:
        return []
    args = ["show", *units, "-p", ",".join(props)]
    try:
        return parse_show(_systemctl("--timestamp=unix", *args))
    except SystemctlError:
        # systemd < 251 has no --timestamp=unix; fall back to text stamps.
        return parse_show(_systemctl(*args))


TIMER_PROPS = [
    "Id", "Description", "ActiveState", "UnitFileState", "Triggers",
    "NextElapseUSecRealtime", "LastTriggerUSec", "TimersCalendar",
    "TimersMonotonic", "Persistent",
]
SERVICE_PROPS = [
    "Id", "Description", "ActiveState", "SubState", "Result",
    "ExecMainStartTimestamp", "ExecMainExitTimestamp", "ExecMainStatus",
]


def collect_schedule() -> list[dict[str, Any]]:
    timers = _show(_list_timer_names(), TIMER_PROPS)
    triggered = sorted(
        {t["Triggers"].split()[0] for t in timers if t.get("Triggers")}
    )
    services = {b.get("Id", ""): b for b in _show(triggered, SERVICE_PROPS)}
    rows = []
    for t in timers:
        svc_name = (t.get("Triggers", "").split() or [""])[0]
        svc = services.get(svc_name, {})
        status = svc.get("ExecMainStatus") or ""
        rows.append({
            "timer": t.get("Id"),
            "service": svc_name,
            "description": svc.get("Description") or t.get("Description"),
            "enabled": t.get("UnitFileState"),
            "active": t.get("ActiveState"),
            "schedule": describe_schedule(
                t.get("TimersCalendar", ""), t.get("TimersMonotonic", "")
            ),
            "next": parse_ts(t.get("NextElapseUSecRealtime")),
            "next_text": t.get("NextElapseUSecRealtime"),
            "last": parse_ts(t.get("LastTriggerUSec")),
            "last_text": t.get("LastTriggerUSec"),
            "service_state": svc.get("ActiveState"),
            "service_substate": svc.get("SubState"),
            "result": svc.get("Result"),
            "exit_status": int(status) if status.isdigit() else None,
            "run_started": parse_ts(svc.get("ExecMainStartTimestamp")),
            "run_ended": parse_ts(svc.get("ExecMainExitTimestamp")),
        })
    rows.sort(key=lambda r: (r["next"] is None, r["next"] or 0))
    return rows


@router.get("/api/cockpit/schedule")
async def cockpit_schedule() -> dict[str, Any]:
    try:
        rows = await asyncio.to_thread(collect_schedule)
        return {"timers": rows, "error": None, "server_time": time.time()}
    except SystemctlError as exc:
        return {"timers": [], "error": str(exc), "server_time": time.time()}


@router.post("/api/cockpit/schedule/{timer}/run")
async def cockpit_run_now(timer: str) -> dict[str, Any]:
    """Start the service behind a known timer immediately (non-blocking)."""
    try:
        rows = await asyncio.to_thread(collect_schedule)
    except SystemctlError as exc:
        raise HTTPException(503, str(exc)) from exc
    match = next((r for r in rows if r["timer"] == timer), None)
    if match is None or not match["service"]:
        raise HTTPException(404, f"unknown timer {timer!r}")
    try:
        await asyncio.to_thread(_systemctl, "start", "--no-block", match["service"])
    except SystemctlError as exc:
        raise HTTPException(500, str(exc)) from exc
    event_log.emit("cockpit", "job", {
        "job": match["service"].removesuffix(".service"),
        "phase": "requested",
        "summary": f"manual run of {match['service']} requested from cockpit",
    })
    return {"ok": True, "service": match["service"]}


# ─── Ingest: sub-agents push key changes ────────────────────


def _check_token(authorization: str | None) -> None:
    expected = os.environ.get("JARVIS_EVENTS_TOKEN", "")
    if not expected:
        raise HTTPException(503, "event ingest disabled: JARVIS_EVENTS_TOKEN not set")
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(401, "bad token")


@router.post("/api/events")
async def ingest_event(
    payload: dict[str, Any],
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Accept a key change from a sub-agent or job.

    Body: {"source": "friday", "kind": "position_opened",
           "summary": "Opened NVDA 40 @ 131.20", "severity": "notice",
           "data": {...}}
    """
    _check_token(authorization)
    source = str(payload.get("source", "")).strip()[:64]
    summary = str(payload.get("summary", "")).strip()
    if not source or not summary:
        raise HTTPException(422, "source and summary are required")
    kind = str(payload.get("kind", "change")).strip()[:64] or "change"
    severity = str(payload.get("severity", "info")).lower()
    if severity not in SEVERITIES:
        severity = "info"
    row_id = event_log.emit(source, "agent", {
        "agent": source,
        "kind": kind,
        "severity": severity,
        "summary": event_log.clip(summary, 500),
        "data": event_log.clip(payload.get("data")),
        "via": "ingest",
    })
    if row_id is None:
        raise HTTPException(500, "event log write failed")
    return {"ok": True, "id": row_id}
