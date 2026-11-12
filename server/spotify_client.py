"""Spotify Web API client for the HUD miniplayer and transport controls.

Credentials come from SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET /
SPOTIFY_REFRESH_TOKEN (the refresh token is minted once by
scripts/spotify_auth on Rosencrantz; see docs). They are read lazily, on
first use, so importing this module — and therefore ``server.api`` — never
requires Spotify to be configured. An unconfigured client reports nothing
playing and refuses transport calls with :class:`SpotifyNotConfiguredError`.
"""

from __future__ import annotations

import base64
import os
import time
from typing import Any

import requests

TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE = "https://api.spotify.com/v1"
ENV_VARS = ("SPOTIFY_CLIENT_ID", "SPOTIFY_CLIENT_SECRET", "SPOTIFY_REFRESH_TOKEN")
REQUEST_TIMEOUT = 10


class SpotifyNotConfiguredError(RuntimeError):
    """Raised by transport calls when the Spotify env vars are absent."""


class SpotifyError(RuntimeError):
    """Spotify refused a command. The message is safe to show the user."""


_REASONS = {
    "NO_ACTIVE_DEVICE": (
        "No Spotify device is active. Open Spotify on a device, or name a "
        "device to play on."
    ),
    "PREMIUM_REQUIRED": "Spotify Premium is required for playback control.",
}


def _raise_for_spotify(resp: requests.Response) -> None:
    """Turn a failed player call into a SpotifyError that says why."""
    if resp.status_code < 400:
        return
    reason = ""
    message = ""
    try:
        error = resp.json().get("error", {})
        if isinstance(error, dict):
            reason = str(error.get("reason") or "")
            message = str(error.get("message") or "")
    except ValueError:
        pass
    if reason in _REASONS:
        raise SpotifyError(_REASONS[reason])
    if resp.status_code == 404:
        raise SpotifyError(_REASONS["NO_ACTIVE_DEVICE"])
    if resp.status_code == 401:
        raise SpotifyError("Spotify rejected the stored authorization.")
    if resp.status_code == 429:
        raise SpotifyError("Spotify is rate-limiting requests; try again shortly.")
    raise SpotifyError(
        f"Spotify refused the request (HTTP {resp.status_code})"
        + (f": {message}" if message else ".")
    )


class SpotifyClient:
    def __init__(self) -> None:
        self._client_id: str | None = None
        self._client_secret: str | None = None
        self._refresh_token: str | None = None
        self._access_token: str | None = None
        self._expires_at = 0.0

    # -- configuration --

    @property
    def configured(self) -> bool:
        return all(os.environ.get(name) for name in ENV_VARS)

    def _load_credentials(self) -> tuple[str, str, str]:
        if self._client_id is None or self._client_secret is None:
            missing = [name for name in ENV_VARS if not os.environ.get(name)]
            if missing:
                raise SpotifyNotConfiguredError(
                    "Spotify is not configured; missing " + ", ".join(missing)
                )
            self._client_id = os.environ["SPOTIFY_CLIENT_ID"]
            self._client_secret = os.environ["SPOTIFY_CLIENT_SECRET"]
            self._refresh_token = os.environ["SPOTIFY_REFRESH_TOKEN"]
        assert self._refresh_token is not None
        return self._client_id, self._client_secret, self._refresh_token

    # -- auth --

    def _refresh_access_token(self) -> None:
        client_id, client_secret, refresh_token = self._load_credentials()
        creds = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        resp = requests.post(
            TOKEN_URL,
            headers={"Authorization": f"Basic {creds}"},
            data={"grant_type": "refresh_token", "refresh_token": refresh_token},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        self._access_token = str(data["access_token"])
        self._expires_at = time.time() + float(data.get("expires_in", 3600)) - 60
        # Spotify sometimes rotates the refresh token.
        if "refresh_token" in data:
            self._refresh_token = str(data["refresh_token"])

    def _get_token(self) -> str:
        if not self._access_token or time.time() >= self._expires_at:
            self._refresh_access_token()
        assert self._access_token is not None
        return self._access_token

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._get_token()}"}

    # -- playback state --

    def get_now_playing(self) -> dict[str, Any] | None:
        """Current player state, or None when nothing is playing or Spotify
        is not configured (the HUD treats both as "idle")."""
        if not self.configured:
            return None
        resp = requests.get(
            f"{API_BASE}/me/player",
            headers=self._headers(),
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code == 204:
            return None
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        item: dict[str, Any] = data.get("item") or {}
        album: dict[str, Any] = item.get("album") or {}
        return {
            "is_playing": data.get("is_playing", False),
            "progress_ms": data.get("progress_ms", 0),
            "duration_ms": item.get("duration_ms", 0),
            "track": item.get("name"),
            "artist": _artists(item),
            "album": album.get("name"),
            "album_art": _album_art(album),
            "device": (data.get("device") or {}).get("name"),
        }

    # -- transport --

    # ``strict`` makes a refused command raise SpotifyError. The HUD's
    # buttons leave it off (they re-read the player state anyway); the
    # brain's tools turn it on, because a tool must not report success for
    # a command Spotify rejected.

    def play(self, *, strict: bool = False) -> None:
        self._put("/me/player/play", strict=strict)

    def pause(self, *, strict: bool = False) -> None:
        self._put("/me/player/pause", strict=strict)

    def next_track(self, *, strict: bool = False) -> None:
        self._post("/me/player/next", strict=strict)

    def previous_track(self, *, strict: bool = False) -> None:
        self._post("/me/player/previous", strict=strict)

    def set_volume(self, percent: int, *, strict: bool = False) -> None:
        self._put(
            "/me/player/volume",
            params={"volume_percent": str(max(0, min(100, int(percent))))},
            strict=strict,
        )

    def play_uri(
        self, uri: str, *, device_id: str | None = None, strict: bool = False
    ) -> None:
        """Start a track, or an album / artist / playlist as a context."""
        body: dict[str, Any] = (
            {"uris": [uri]}
            if uri.startswith("spotify:track:")
            else {"context_uri": uri}
        )
        self._put(
            "/me/player/play",
            json=body,
            params={"device_id": device_id} if device_id else None,
            strict=strict,
        )

    def search(
        self, query: str, kind: str = "track", limit: int = 5
    ) -> list[dict[str, Any]]:
        """Top matches for ``query``. ``kind``: track, album, artist, playlist."""
        resp = requests.get(
            f"{API_BASE}/search",
            headers=self._headers(),
            # Development-mode apps are capped at 10 results per search.
            params={"q": query, "type": kind, "limit": str(max(1, min(10, limit)))},
            timeout=REQUEST_TIMEOUT,
        )
        _raise_for_spotify(resp)
        items = (resp.json().get(f"{kind}s") or {}).get("items") or []
        results: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict) or not item.get("uri"):
                continue  # playlist searches can return null entries
            owner = item.get("owner") or {}
            results.append(
                {
                    "uri": item["uri"],
                    "kind": kind,
                    "name": item.get("name"),
                    "artist": _artists(item)
                    or (owner.get("display_name") if isinstance(owner, dict) else None)
                    or None,
                    "album": (item.get("album") or {}).get("name"),
                }
            )
        return results

    def get_devices(self) -> list[dict[str, Any]]:
        resp = requests.get(
            f"{API_BASE}/me/player/devices",
            headers=self._headers(),
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        devices: list[dict[str, Any]] = resp.json().get("devices", [])
        return devices

    def transfer_playback(
        self, device_id: str, play: bool = True, *, strict: bool = False
    ) -> None:
        self._put(
            "/me/player",
            json={"device_ids": [device_id], "play": play},
            strict=strict,
        )

    def get_queue(self) -> list[dict[str, Any]]:
        resp = requests.get(
            f"{API_BASE}/me/player/queue",
            headers=self._headers(),
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        queue: list[dict[str, Any]] = resp.json().get("queue", [])
        return [
            {
                "track": item.get("name"),
                "artist": _artists(item),
                "album_art": _album_art(item.get("album") or {}),
            }
            for item in queue[:10]
        ]

    # -- helpers --

    def _put(
        self,
        path: str,
        json: dict[str, Any] | None = None,
        *,
        params: dict[str, str] | None = None,
        strict: bool = False,
    ) -> None:
        resp = requests.put(
            f"{API_BASE}{path}",
            headers=self._headers(),
            json=json,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
        if strict:
            _raise_for_spotify(resp)

    def _post(self, path: str, *, strict: bool = False) -> None:
        resp = requests.post(
            f"{API_BASE}{path}", headers=self._headers(), timeout=REQUEST_TIMEOUT
        )
        if strict:
            _raise_for_spotify(resp)


def _artists(item: dict[str, Any]) -> str:
    return ", ".join(a["name"] for a in item.get("artists", []))


def _album_art(album: dict[str, Any]) -> str | None:
    images: list[dict[str, Any]] = album.get("images") or []
    return images[0].get("url") if images else None


spotify_client = SpotifyClient()
