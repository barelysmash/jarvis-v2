"""Spotify voice control for JARVIS v2.

Exposes the Spotify client the HUD miniplayer already uses as brain tools,
so "pause the music" or "play Kind of Blue on the kitchen speaker" works by
voice. Every command runs in strict mode: when Spotify refuses (no active
device, authorization rejected), the tool raises and the brain is told it
failed, instead of reporting a success that did not happen.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from server.spotify_client import SpotifyClient, SpotifyError, spotify_client

if TYPE_CHECKING:
    from orchestrator.tools import ToolRegistry

ACTIONS = ("play", "pause", "next", "previous")
KINDS = ("track", "album", "artist", "playlist")

_NO_ARGS: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


class SpotifyAdapter:
    """Registers Spotify playback tools on the shared client."""

    def __init__(self, client: SpotifyClient | None = None) -> None:
        # The module-level client is shared with the HUD routes so both use
        # one access token.
        self.client = client or spotify_client

    # -- helpers --

    def _device_summary(self) -> list[dict[str, Any]]:
        return [
            {
                "name": device.get("name"),
                "type": device.get("type"),
                "active": bool(device.get("is_active")),
                "volume_percent": device.get("volume_percent"),
            }
            for device in self.client.get_devices()
        ]

    def _resolve_device(self, wanted: str) -> dict[str, Any]:
        """Find one device by spoken name: exact first, then a unique partial."""
        devices = self.client.get_devices()
        if not devices:
            raise SpotifyError(
                "Spotify reports no available devices. Open Spotify on one first."
            )
        needle = wanted.strip().lower()
        names = ", ".join(str(device.get("name")) for device in devices)
        exact = [d for d in devices if str(d.get("name", "")).lower() == needle]
        partial = [d for d in devices if needle in str(d.get("name", "")).lower()]
        matches = exact or partial
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise SpotifyError(
                f"No Spotify device matches '{wanted}'. Available: {names}."
            )
        raise SpotifyError(
            f"'{wanted}' matches more than one Spotify device. Available: {names}."
        )

    # -- tools --

    def now_playing(self) -> dict[str, Any]:
        state = self.client.get_now_playing()
        if state is None:
            return {"is_playing": False, "note": "Nothing is playing."}
        return {
            key: state.get(key)
            for key in ("is_playing", "track", "artist", "album", "device")
        }

    def control(self, action: str) -> dict[str, Any]:
        if action == "play":
            self.client.play(strict=True)
        elif action == "pause":
            self.client.pause(strict=True)
        elif action == "next":
            self.client.next_track(strict=True)
        elif action == "previous":
            self.client.previous_track(strict=True)
        else:
            raise ValueError(f"action must be one of {', '.join(ACTIONS)}")
        return {"ok": True, "action": action}

    def play(
        self, query: str, kind: str = "track", device: str | None = None
    ) -> dict[str, Any]:
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {', '.join(KINDS)}")
        if not query.strip():
            raise ValueError("query is required")
        target = self._resolve_device(device) if device else None
        results = self.client.search(query, kind, limit=5)
        if not results:
            raise SpotifyError(f"Spotify found no {kind} matching '{query}'.")
        choice = results[0]
        self.client.play_uri(
            choice["uri"],
            device_id=str(target["id"]) if target else None,
            strict=True,
        )
        return {
            "ok": True,
            "playing": {
                key: choice.get(key) for key in ("kind", "name", "artist", "album")
            },
            "device": target.get("name") if target else None,
            # So the brain can offer the runner-up when the top hit is wrong.
            "other_matches": [
                {key: other.get(key) for key in ("name", "artist")}
                for other in results[1:4]
            ],
        }

    def set_volume(self, percent: int) -> dict[str, Any]:
        if not 0 <= int(percent) <= 100:
            raise ValueError("percent must be between 0 and 100")
        self.client.set_volume(int(percent), strict=True)
        return {"ok": True, "volume_percent": int(percent)}

    def devices(self) -> dict[str, Any]:
        return {"devices": self._device_summary()}

    def transfer(self, device: str) -> dict[str, Any]:
        target = self._resolve_device(device)
        self.client.transfer_playback(str(target["id"]), strict=True)
        return {"ok": True, "device": target.get("name")}

    def register(self, tools: ToolRegistry) -> None:
        tools.register(
            name="spotify_now_playing",
            description=(
                "What Spotify is playing right now: track, artist, album, "
                "device, and whether it is paused."
            ),
            schema=_NO_ARGS,
            handler=lambda: self.now_playing(),
        )

        tools.register(
            name="spotify_control",
            description=(
                "Transport control for Spotify: resume (play), pause, skip to "
                "the next track, or go back to the previous one. Use "
                "spotify_play instead to start specific music."
            ),
            schema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": list(ACTIONS)},
                },
                "required": ["action"],
                "additionalProperties": False,
            },
            handler=lambda action: self.control(action),
        )

        tools.register(
            name="spotify_play",
            description=(
                "Search Spotify and start playing the top match. kind is "
                "track (default), album, artist or playlist. Optionally name "
                "a device to play on. The result says exactly what was "
                "started and lists other matches; tell the user what is "
                "playing, and if it is not what they asked for, offer one of "
                "the others."
            ),
            schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What to play, e.g. 'Kind of Blue Miles Davis'.",
                    },
                    "kind": {"type": "string", "enum": list(KINDS)},
                    "device": {
                        "type": "string",
                        "description": "Device name, as the user said it.",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            handler=lambda query, kind="track", device=None: self.play(
                query, kind, device
            ),
        )

        tools.register(
            name="spotify_set_volume",
            description=(
                "Set the active Spotify device's volume to a percentage "
                "from 0 to 100. For 'turn it up' or 'down', read the current "
                "volume with spotify_devices first and adjust by about 15."
            ),
            schema={
                "type": "object",
                "properties": {
                    "percent": {"type": "integer", "minimum": 0, "maximum": 100},
                },
                "required": ["percent"],
                "additionalProperties": False,
            },
            handler=lambda percent: self.set_volume(percent),
        )

        tools.register(
            name="spotify_devices",
            description=(
                "List the Spotify devices available now: name, type, which "
                "one is active, and volume."
            ),
            schema=_NO_ARGS,
            handler=lambda: self.devices(),
        )

        tools.register(
            name="spotify_transfer",
            description=(
                "Move Spotify playback to another device, named as the user "
                "said it (for example 'kitchen'). Fails with the list of "
                "available devices if the name matches none or several."
            ),
            schema={
                "type": "object",
                "properties": {"device": {"type": "string"}},
                "required": ["device"],
                "additionalProperties": False,
            },
            handler=lambda device: self.transfer(device),
        )
