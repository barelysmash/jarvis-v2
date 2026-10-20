"""Spotify client configuration is lazy.

``server.api`` imports the module-level ``spotify_client`` at import time, so
the client must not read its env vars in ``__init__``: a test run, a dev box
or a fresh guildenstern without Spotify configured still has to be able to
import the API. Unconfigured, the miniplayer sees "nothing playing" and
transport calls refuse with a clear error. Nothing here touches the network.
"""

from __future__ import annotations

import os
import unittest

from server.spotify_client import (
    ENV_VARS,
    SpotifyClient,
    SpotifyNotConfiguredError,
)


class SpotifyClientConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = {name: os.environ.pop(name, None) for name in ENV_VARS}
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        for name, value in self._saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def test_import_and_construct_without_env(self) -> None:
        client = SpotifyClient()
        self.assertFalse(client.configured)

    def test_unconfigured_now_playing_is_idle(self) -> None:
        self.assertIsNone(SpotifyClient().get_now_playing())

    def test_unconfigured_transport_refuses(self) -> None:
        client = SpotifyClient()
        for call in (
            client.play,
            client.pause,
            client.next_track,
            client.previous_track,
        ):
            with (
                self.subTest(call.__name__),
                self.assertRaises(SpotifyNotConfiguredError) as ctx,
            ):
                call()
            self.assertIn("SPOTIFY_CLIENT_ID", str(ctx.exception))

    def test_env_is_read_lazily(self) -> None:
        client = SpotifyClient()  # constructed before the env exists
        for name in ENV_VARS:
            os.environ[name] = f"test-{name.lower()}"
        self.assertTrue(client.configured)
        self.assertEqual(
            client._load_credentials(),
            (
                "test-spotify_client_id",
                "test-spotify_client_secret",
                "test-spotify_refresh_token",
            ),
        )

    def test_partial_env_names_what_is_missing(self) -> None:
        os.environ["SPOTIFY_CLIENT_ID"] = "x"
        client = SpotifyClient()
        self.assertFalse(client.configured)
        with self.assertRaises(SpotifyNotConfiguredError) as ctx:
            client.play()
        self.assertNotIn("SPOTIFY_CLIENT_ID", str(ctx.exception))
        self.assertIn("SPOTIFY_CLIENT_SECRET", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
