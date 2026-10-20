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

    def play(self) -> None:
        self._put("/me/player/play")

    def pause(self) -> None:
        self._put("/me/player/pause")

    def next_track(self) -> None:
        self._post("/me/player/next")

    def previous_track(self) -> None:
        self._post("/me/player/previous")

    def get_devices(self) -> list[dict[str, Any]]:
        resp = requests.get(
            f"{API_BASE}/me/player/devices",
            headers=self._headers(),
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        devices: list[dict[str, Any]] = resp.json().get("devices", [])
        return devices

    def transfer_playback(self, device_id: str, play: bool = True) -> None:
        self._put("/me/player", json={"device_ids": [device_id], "play": play})

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

    def _put(self, path: str, json: dict[str, Any] | None = None) -> None:
        requests.put(
            f"{API_BASE}{path}",
            headers=self._headers(),
            json=json,
            timeout=REQUEST_TIMEOUT,
        )

    def _post(self, path: str) -> None:
        requests.post(
            f"{API_BASE}{path}", headers=self._headers(), timeout=REQUEST_TIMEOUT
        )


def _artists(item: dict[str, Any]) -> str:
    return ", ".join(a["name"] for a in item.get("artists", []))


def _album_art(album: dict[str, Any]) -> str | None:
    images: list[dict[str, Any]] = album.get("images") or []
    return images[0].get("url") if images else None


spotify_client = SpotifyClient()
