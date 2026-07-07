"""Event bus: pub/sub for HUD updates."""

import asyncio
from datetime import datetime
from typing import Any


class EventBus:
    """Broadcasts JARVIS state changes to all connected HUD clients."""

    def __init__(self):
        self.subscribers: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self.subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        self.subscribers.discard(q)

    async def publish(self, event_type: str, data: Any):
        event = {
            "type": event_type,
            "timestamp": datetime.now().isoformat(),
            "data": data,
        }
        for q in list(self.subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                    q.put_nowait(event)
                except Exception:
                    pass


# Global bus
bus = EventBus()


# Convenience publishers
async def emit_state(state: str):
    """idle | listening | thinking | speaking"""
    await bus.publish("state", {"state": state})


async def emit_user_speech(text: str):
    await bus.publish("conversation", {"role": "user", "text": text})


async def emit_jarvis_speech(text: str, streaming: bool = False):
    await bus.publish(
        "conversation",
        {"role": "jarvis", "text": text, "streaming": streaming},
    )


async def emit_tool_call(
    tool_name: str, args: dict, status: str = "running"
):
    await bus.publish(
        "tool", {"name": tool_name, "args": args, "status": status}
    )


async def emit_audio_level(level: float):
    """RMS level 0.0-1.0 for waveform viz."""
    await bus.publish("audio", {"level": level})


async def emit_widget_update(widget: str, data: dict):
    await bus.publish("widget", {"widget": widget, "data": data})
