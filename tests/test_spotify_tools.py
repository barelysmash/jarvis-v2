"""Spotify brain tools: what they send to Spotify and how failures surface.

A fake Spotify stands in for ``requests`` so every test checks the actual
HTTP call the client would make. Nothing here touches the network.
"""

from __future__ import annotations

import os
import unittest
from typing import Any
from unittest import mock

import yaml

from orchestrator.tools import ToolRegistry
from server.spotify_client import ENV_VARS, SpotifyClient, SpotifyError
from tools.integrations.spotify import SpotifyAdapter

DEVICES = [
    {"id": "dev-k", "name": "Kitchen Speaker", "type": "Speaker",
     "is_active": True, "volume_percent": 40},
    {"id": "dev-o", "name": "Office Echo", "type": "Speaker",
     "is_active": False, "volume_percent": 25},
    {"id": "dev-p", "name": "Barry's Phone", "type": "Smartphone",
     "is_active": False, "volume_percent": 80},
]


class FakeResponse:
    def __init__(self, status: int = 200, body: Any = None) -> None:
        self.status_code = status
        self._body = body

    def json(self) -> Any:
        if self._body is None:
            raise ValueError("no body")
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSpotify:
    """Records calls; answers the endpoints the tools use."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.fail: FakeResponse | None = None
        self.search_items: list[Any] = [
            {"uri": "spotify:track:1", "name": "So What",
             "artists": [{"name": "Miles Davis"}], "album": {"name": "Kind of Blue"}},
            {"uri": "spotify:track:2", "name": "So What (Live)",
             "artists": [{"name": "Miles Davis"}], "album": {"name": "Live"}},
        ]

    def _record(self, method: str, url: str, kwargs: dict[str, Any]) -> str:
        path = url.split("/v1", 1)[-1] if "/v1" in url else url
        self.calls.append(
            (method, path, {k: kwargs.get(k) for k in ("json", "params")})
        )
        return path

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        if "accounts.spotify.com" in url:
            return FakeResponse(
                200, {"access_token": "ACCESS-TOKEN-VALUE", "expires_in": 3600}
            )
        self._record("POST", url, kwargs)
        return self.fail or FakeResponse(204)

    def put(self, url: str, **kwargs: Any) -> FakeResponse:
        self._record("PUT", url, kwargs)
        return self.fail or FakeResponse(204)

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        path = self._record("GET", url, kwargs)
        if path == "/me/player/devices":
            return FakeResponse(200, {"devices": DEVICES})
        if path == "/search":
            kind = kwargs["params"]["type"]
            return FakeResponse(200, {f"{kind}s": {"items": self.search_items}})
        if path == "/me/player":
            return FakeResponse(
                200,
                {
                    "is_playing": True,
                    "progress_ms": 1000,
                    "device": {"name": "Kitchen Speaker"},
                    "item": {
                        "name": "So What",
                        "duration_ms": 9000,
                        "artists": [{"name": "Miles Davis"}],
                        "album": {"name": "Kind of Blue",
                                  "images": [{"url": "https://img"}]},
                    },
                },
            )
        return FakeResponse(404, {"error": {"message": "not found"}})


class SpotifyToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeSpotify()
        patcher = mock.patch("server.spotify_client.requests", self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        env = mock.patch.dict(os.environ, {name: "x" for name in ENV_VARS})
        env.start()
        self.addCleanup(env.stop)

        self.registry = ToolRegistry()
        SpotifyAdapter(SpotifyClient()).register(self.registry)

    def run_tool(self, name: str, args: dict[str, Any] | None = None):
        return self.registry.execute(name, args or {})

    def player_calls(self) -> list[tuple[str, str, dict[str, Any]]]:
        return [call for call in self.fake.calls if call[0] != "GET"]

    # -- registration --

    def test_registers_six_tools(self) -> None:
        names = {n for n in self.registry.names() if n.startswith("spotify_")}
        self.assertEqual(
            names,
            {
                "spotify_now_playing",
                "spotify_control",
                "spotify_play",
                "spotify_set_volume",
                "spotify_devices",
                "spotify_transfer",
            },
        )

    def test_agents_yaml_gates_spotify_on_all_three_variables(self) -> None:
        with open("config/agents.yaml", encoding="utf-8") as handle:
            entry = yaml.safe_load(handle)["agents"]["spotify"]
        self.assertEqual(entry["transport"], "adapter")
        self.assertEqual(sorted(entry["requires_env"]), sorted(ENV_VARS))
        module = __import__(entry["module"], fromlist=[entry["class"]])
        self.assertIs(getattr(module, entry["class"]), SpotifyAdapter)

    # -- reads --

    def test_now_playing_omits_art_and_progress(self) -> None:
        result, is_error = self.run_tool("spotify_now_playing")
        self.assertFalse(is_error)
        self.assertEqual(
            result,
            {
                "is_playing": True,
                "track": "So What",
                "artist": "Miles Davis",
                "album": "Kind of Blue",
                "device": "Kitchen Speaker",
            },
        )

    def test_devices_lists_names_without_ids(self) -> None:
        result, is_error = self.run_tool("spotify_devices")
        self.assertFalse(is_error)
        self.assertEqual(len(result["devices"]), 3)
        self.assertEqual(result["devices"][0]["name"], "Kitchen Speaker")
        self.assertTrue(result["devices"][0]["active"])
        self.assertNotIn("id", result["devices"][0])

    # -- transport --

    def test_control_maps_each_action_to_its_endpoint(self) -> None:
        expected = {
            "play": ("PUT", "/me/player/play"),
            "pause": ("PUT", "/me/player/pause"),
            "next": ("POST", "/me/player/next"),
            "previous": ("POST", "/me/player/previous"),
        }
        for action, (method, path) in expected.items():
            self.fake.calls.clear()
            result, is_error = self.run_tool("spotify_control", {"action": action})
            self.assertFalse(is_error, action)
            self.assertEqual(result, {"ok": True, "action": action})
            self.assertEqual(self.player_calls()[0][:2], (method, path))

    def test_unknown_action_is_an_error_and_sends_nothing(self) -> None:
        _, is_error = self.run_tool("spotify_control", {"action": "shuffle"})
        self.assertTrue(is_error)
        self.assertEqual(self.player_calls(), [])

    def test_volume_is_sent_as_a_percentage(self) -> None:
        result, is_error = self.run_tool("spotify_set_volume", {"percent": 35})
        self.assertFalse(is_error)
        self.assertEqual(result["volume_percent"], 35)
        self.assertEqual(
            self.player_calls()[0],
            ("PUT", "/me/player/volume",
             {"json": None, "params": {"volume_percent": "35"}}),
        )

    def test_out_of_range_volume_is_refused(self) -> None:
        _, is_error = self.run_tool("spotify_set_volume", {"percent": 140})
        self.assertTrue(is_error)
        self.assertEqual(self.player_calls(), [])

    # -- search and play --

    def test_play_starts_the_top_track_and_reports_it(self) -> None:
        result, is_error = self.run_tool("spotify_play", {"query": "so what miles"})

        self.assertFalse(is_error)
        self.assertEqual(result["playing"]["name"], "So What")
        self.assertEqual(result["playing"]["artist"], "Miles Davis")
        self.assertEqual(result["other_matches"][0]["name"], "So What (Live)")
        self.assertEqual(
            self.player_calls()[0],
            ("PUT", "/me/player/play",
             {"json": {"uris": ["spotify:track:1"]}, "params": None}),
        )
        search = next(c for c in self.fake.calls if c[1] == "/search")
        self.assertEqual(search[2]["params"]["type"], "track")
        self.assertLessEqual(int(search[2]["params"]["limit"]), 10)

    def test_album_plays_as_a_context_on_the_named_device(self) -> None:
        self.fake.search_items = [
            {"uri": "spotify:album:9", "name": "Kind of Blue",
             "artists": [{"name": "Miles Davis"}]},
        ]
        result, is_error = self.run_tool(
            "spotify_play",
            {"query": "kind of blue", "kind": "album", "device": "office"},
        )

        self.assertFalse(is_error)
        self.assertEqual(result["device"], "Office Echo")
        self.assertEqual(
            self.player_calls()[0],
            ("PUT", "/me/player/play",
             {"json": {"context_uri": "spotify:album:9"},
              "params": {"device_id": "dev-o"}}),
        )

    def test_playlist_search_skips_null_entries(self) -> None:
        self.fake.search_items = [
            None,
            {"uri": "spotify:playlist:5", "name": "Pink Noise",
             "owner": {"display_name": "Someone"}},
        ]
        result, is_error = self.run_tool(
            "spotify_play", {"query": "pink noise", "kind": "playlist"}
        )
        self.assertFalse(is_error)
        self.assertEqual(result["playing"]["name"], "Pink Noise")
        self.assertEqual(result["playing"]["artist"], "Someone")

    def test_no_match_is_an_error_and_plays_nothing(self) -> None:
        self.fake.search_items = []
        result, is_error = self.run_tool("spotify_play", {"query": "zzzz"})
        self.assertTrue(is_error)
        self.assertIn("found no track", str(result))
        self.assertEqual(self.player_calls(), [])

    # -- devices --

    def test_transfer_matches_a_spoken_partial_name(self) -> None:
        result, is_error = self.run_tool("spotify_transfer", {"device": "kitchen"})
        self.assertFalse(is_error)
        self.assertEqual(result["device"], "Kitchen Speaker")
        self.assertEqual(
            self.player_calls()[0][2]["json"],
            {"device_ids": ["dev-k"], "play": True},
        )

    def test_unknown_and_ambiguous_devices_list_what_is_available(self) -> None:
        for wanted in ("bedroom", "speaker e"):
            result, is_error = self.run_tool("spotify_transfer", {"device": wanted})
            self.assertTrue(is_error, wanted)
        result, is_error = self.run_tool("spotify_transfer", {"device": "bedroom"})
        self.assertIn("Kitchen Speaker", str(result))
        # "e" appears in all three names.
        result, is_error = self.run_tool("spotify_transfer", {"device": "e"})
        self.assertTrue(is_error)
        self.assertIn("more than one", str(result))
        self.assertEqual(self.player_calls(), [])

    # -- failures reach the brain --

    def test_no_active_device_is_reported_not_swallowed(self) -> None:
        self.fake.fail = FakeResponse(
            404, {"error": {"reason": "NO_ACTIVE_DEVICE", "message": "x"}}
        )
        for name, args in (
            ("spotify_control", {"action": "pause"}),
            ("spotify_set_volume", {"percent": 20}),
            ("spotify_play", {"query": "so what"}),
        ):
            result, is_error = self.run_tool(name, args)
            self.assertTrue(is_error, name)
            self.assertIn("No Spotify device is active", str(result))

    def test_rejected_authorization_does_not_leak_the_token(self) -> None:
        self.fake.fail = FakeResponse(401, {"error": {"message": "expired"}})
        result, is_error = self.run_tool("spotify_control", {"action": "play"})
        self.assertTrue(is_error)
        self.assertIn("rejected the stored authorization", str(result))
        self.assertNotIn("ACCESS-TOKEN-VALUE", str(result))

    def test_hud_calls_stay_lenient(self) -> None:
        # The miniplayer's buttons must not start raising on a refused call.
        self.fake.fail = FakeResponse(404, {"error": {"reason": "NO_ACTIVE_DEVICE"}})
        client = SpotifyClient()
        client.pause()
        client.next_track()
        with self.assertRaises(SpotifyError):
            client.pause(strict=True)


if __name__ == "__main__":
    unittest.main()
