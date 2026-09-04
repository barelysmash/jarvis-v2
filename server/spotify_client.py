import base64
import os
import time
import requests

TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE = "https://api.spotify.com/v1"


class SpotifyClient:
    def __init__(self):
        self.client_id = os.environ["SPOTIFY_CLIENT_ID"]
        self.client_secret = os.environ["SPOTIFY_CLIENT_SECRET"]
        self.refresh_token = os.environ["SPOTIFY_REFRESH_TOKEN"]
        self._access_token = None
        self._expires_at = 0

    def _refresh_access_token(self):
        creds = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode()
        ).decode()
        resp = requests.post(
            TOKEN_URL,
            headers={"Authorization": f"Basic {creds}"},
            data={
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        self._access_token = data["access_token"]
        self._expires_at = time.time() + data.get("expires_in", 3600) - 60
        # Spotify sometimes rotates the refresh token
        if "refresh_token" in data:
            self.refresh_token = data["refresh_token"]

    def _get_token(self):
        if not self._access_token or time.time() >= self._expires_at:
            self._refresh_access_token()
        return self._access_token

    def _headers(self):
        return {"Authorization": f"Bearer {self._get_token()}"}

    def get_now_playing(self):
        resp = requests.get(
            f"{API_BASE}/me/player",
            headers=self._headers(),
            timeout=10,
        )
        if resp.status_code == 204:
            return None
        resp.raise_for_status()
        data = resp.json()
        item = data.get("item") or {}
        return {
            "is_playing": data.get("is_playing", False),
            "progress_ms": data.get("progress_ms", 0),
            "duration_ms": item.get("duration_ms", 0),
            "track": item.get("name"),
            "artist": ", ".join(a["name"] for a in item.get("artists", [])),
            "album": item.get("album", {}).get("name"),
            "album_art": (item.get("album", {}).get("images") or [{}])[0].get("url"),
            "device": data.get("device", {}).get("name"),
        }

    def play(self):
        requests.put(f"{API_BASE}/me/player/play", headers=self._headers(), timeout=10)

    def pause(self):
        requests.put(f"{API_BASE}/me/player/pause", headers=self._headers(), timeout=10)

    def next_track(self):
        requests.post(f"{API_BASE}/me/player/next", headers=self._headers(), timeout=10)

    def previous_track(self):
        requests.post(f"{API_BASE}/me/player/previous", headers=self._headers(), timeout=10)


spotify_client = SpotifyClient()