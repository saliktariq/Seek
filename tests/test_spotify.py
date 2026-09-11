from __future__ import annotations

import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import urllib.error

import seek.core.spotify as spotify


def _fake_response(payload: dict) -> "_FakeResponse":
    return _FakeResponse(json.dumps(payload).encode("utf-8"))


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_args: object) -> bool:  # type: ignore
        return False

    def read(self) -> bytes:
        return self._payload


def _urlopen_dispatcher(responses: dict[str, dict]):
    def _urlopen(request, timeout=None):  # noqa: ARG001 - matches urlopen signature
        url = request.full_url
        if url not in responses:
            raise AssertionError(f"Unexpected URL requested: {url}")
        return _fake_response(responses[url])

    return _urlopen


class UrlParsingTests(unittest.TestCase):
    def test_parses_track_album_and_playlist_links(self) -> None:
        cases = {
            "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC": (
                "track",
                "4uLU6hMCjMI75M1A2tKUQC",
            ),
            "https://open.spotify.com/album/1DFixLWuPkv3KT3TnV35m3?si=abc": (
                "album",
                "1DFixLWuPkv3KT3TnV35m3",
            ),
            "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M/": (
                "playlist",
                "37i9dQZF1DXcBWIGoYBM5M",
            ),
            "https://open.spotify.com/intl-en/track/4uLU6hMCjMI75M1A2tKUQC": (
                "track",
                "4uLU6hMCjMI75M1A2tKUQC",
            ),
            "spotify:track:4uLU6hMCjMI75M1A2tKUQC": (
                "track",
                "4uLU6hMCjMI75M1A2tKUQC",
            ),
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(spotify.parse_spotify_url(url), expected)
                self.assertTrue(spotify.is_spotify_url(url))

    def test_rejects_deceptive_or_unsupported_urls(self) -> None:
        urls = (
            "",
            "not a url",
            "https://open.spotify.com.evil.test/track/4uLU6hMCjMI75M1A2tKUQC",
            "https://evil.test/open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC",
            "https://open.spotify.com/artist/4uLU6hMCjMI75M1A2tKUQC",
            "https://open.spotify.com/track/",
            "https://www.youtube.com/watch?v=abc123",
            "spotify:artist:4uLU6hMCjMI75M1A2tKUQC",
        )
        for url in urls:
            with self.subTest(url=url):
                self.assertIsNone(spotify.parse_spotify_url(url))
                self.assertFalse(spotify.is_spotify_url(url))


class SpotifyTrackTests(unittest.TestCase):
    def test_display_name_joins_multiple_artists(self) -> None:
        track = spotify.SpotifyTrack(
            id="1",
            title="Song",
            artists=("A", "B"),
            album="Alb",
            duration_ms=1000,
        )
        self.assertEqual(track.display_name, "A, B - Song")

    def test_display_name_handles_no_artists(self) -> None:
        track = spotify.SpotifyTrack(
            id="1",
            title="Song",
            artists=(),
            album="Alb",
            duration_ms=None,
        )
        self.assertEqual(track.display_name, "Unknown Artist - Song")


class CredentialStoreTests(unittest.TestCase):
    def test_environment_variables_take_precedence(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temporary,
            mock.patch.object(
                spotify,
                "credentials_config_path",
                return_value=Path(temporary) / "spotify.json",
            ),
            mock.patch.dict(
                os.environ,
                {
                    "SPOTIFY_CLIENT_ID": "env-id",
                    "SPOTIFY_CLIENT_SECRET": "env-secret",
                },
            ),
        ):
            spotify.save_credentials("file-id", "file-secret")
            self.assertEqual(
                spotify.load_credentials(),
                ("env-id", "env-secret"),
            )

    def test_falls_back_to_config_file_when_environment_is_unset(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temporary,
            mock.patch.object(
                spotify,
                "credentials_config_path",
                return_value=Path(temporary) / "spotify.json",
            ),
            mock.patch.dict(
                os.environ,
                {"SPOTIFY_CLIENT_ID": "", "SPOTIFY_CLIENT_SECRET": ""},
            ),
        ):
            self.assertIsNone(spotify.load_credentials())
            path = spotify.save_credentials("file-id", "file-secret")
            self.assertTrue(path.is_file())
            self.assertEqual(
                spotify.load_credentials(),
                ("file-id", "file-secret"),
            )


class SpotifyClientTests(unittest.TestCase):
    def test_constructor_requires_both_credentials(self) -> None:
        with self.assertRaises(spotify.SpotifyAuthError):
            spotify.SpotifyClient("", "secret")
        with self.assertRaises(spotify.SpotifyAuthError):
            spotify.SpotifyClient("id", "")

    def test_invalid_credentials_raise_auth_error(self) -> None:
        def _urlopen(request, timeout=None):
            raise urllib.error.HTTPError(
                request.full_url, 400, "Bad Request", {}, io.BytesIO(b"{}")
            )

        with mock.patch.object(spotify.urllib.request, "urlopen", side_effect=_urlopen):
            client = spotify.SpotifyClient("bad-id", "bad-secret")
            with self.assertRaises(spotify.SpotifyAuthError):
                client.get_track("abc")

    def test_get_track_maps_payload_to_spotify_track(self) -> None:
        responses = {
            "https://accounts.spotify.com/api/token": {
                "access_token": "tok123",
                "expires_in": 3600,
            },
            "https://api.spotify.com/v1/tracks/abc": {
                "id": "abc",
                "name": "Song Title",
                "duration_ms": 210000,
                "artists": [{"name": "Artist One"}, {"name": "Artist Two"}],
                "album": {"name": "Album Name"},
            },
        }
        with mock.patch.object(
            spotify.urllib.request,
            "urlopen",
            side_effect=_urlopen_dispatcher(responses),
        ):
            client = spotify.SpotifyClient("client-id", "client-secret")
            track = client.get_track("abc")

        self.assertEqual(track.title, "Song Title")
        self.assertEqual(track.artists, ("Artist One", "Artist Two"))
        self.assertEqual(track.album, "Album Name")
        self.assertEqual(track.duration_ms, 210000)
        self.assertEqual(
            track.display_name,
            "Artist One, Artist Two - Song Title",
        )

    def test_get_album_tracks_paginates_and_attaches_album_name(self) -> None:
        responses = {
            "https://accounts.spotify.com/api/token": {
                "access_token": "tok123",
                "expires_in": 3600,
            },
            "https://api.spotify.com/v1/albums/alb1": {"name": "Great Album"},
            "https://api.spotify.com/v1/albums/alb1/tracks?limit=50": {
                "items": [
                    {
                        "id": "t1",
                        "name": "Track One",
                        "duration_ms": 100000,
                        "artists": [{"name": "Artist"}],
                    }
                ],
                "next": (
                    "https://api.spotify.com/v1/albums/alb1/tracks"
                    "?limit=50&offset=50"
                ),
            },
            "https://api.spotify.com/v1/albums/alb1/tracks?limit=50&offset=50": {
                "items": [
                    {
                        "id": "t2",
                        "name": "Track Two",
                        "duration_ms": 120000,
                        "artists": [{"name": "Artist"}],
                    }
                ],
                "next": None,
            },
        }
        with mock.patch.object(
            spotify.urllib.request,
            "urlopen",
            side_effect=_urlopen_dispatcher(responses),  # type: ignore
        ):
            client = spotify.SpotifyClient("client-id", "client-secret")
            tracks = client.get_album_tracks("alb1")

        self.assertEqual([track.id for track in tracks], ["t1", "t2"])
        self.assertTrue(all(track.album == "Great Album" for track in tracks))

    def test_get_playlist_tracks_skips_local_and_missing_tracks(self) -> None:
        responses = {
            "https://accounts.spotify.com/api/token": {
                "access_token": "tok123",
                "expires_in": 3600,
            },
            "https://api.spotify.com/v1/playlists/pl1/tracks?limit=100": {
                "items": [
                    {
                        "track": {
                            "id": "t1",
                            "name": "Track One",
                            "duration_ms": 100000,
                            "artists": [{"name": "Artist"}],
                            "album": {"name": "Album"},
                            "is_local": False,
                        }
                    },
                    {
                        "track": {
                            "id": None,
                            "name": "Local Track",
                            "is_local": True,
                        }
                    },
                    {"track": None},
                ],
                "next": None,
            },
        }
        with mock.patch.object(
            spotify.urllib.request,
            "urlopen",
            side_effect=_urlopen_dispatcher(responses),  # type: ignore
        ):
            client = spotify.SpotifyClient("client-id", "client-secret")
            tracks = client.get_playlist_tracks("pl1")

        self.assertEqual([track.id for track in tracks], ["t1"])

    def test_resolve_dispatches_by_kind(self) -> None:
        responses = {
            "https://accounts.spotify.com/api/token": {
                "access_token": "tok123",
                "expires_in": 3600,
            },
            "https://api.spotify.com/v1/tracks/abc": {
                "id": "abc",
                "name": "Song",
                "duration_ms": 1000,
                "artists": [],
                "album": {},
            },
        }
        with mock.patch.object(
            spotify.urllib.request,
            "urlopen",
            side_effect=_urlopen_dispatcher(responses),
        ):
            client = spotify.SpotifyClient("client-id", "client-secret")
            tracks = client.resolve("track", "abc")

        self.assertEqual([track.id for track in tracks], ["abc"])
        with self.assertRaises(spotify.SpotifyError):
            client.resolve("artist", "abc")


class ZeroSetupTrackTests(unittest.TestCase):
    def test_prefers_embed_page_metadata_when_available(self) -> None:
        next_data = {
            "props": {
                "pageProps": {
                    "state": {
                        "data": {
                            "entity": {
                                "id": "abc",
                                "name": "Song Title",
                                "artists": [{"name": "The Artist"}],
                                "albumOfTrack": {"name": "The Album"},
                                "duration": 180000,
                            }
                        }
                    }
                }
            }
        }
        html = (
            '<html><script id="__NEXT_DATA__" type="application/json">'
            f"{json.dumps(next_data)}</script></html>"
        )

        with mock.patch.object(spotify, "_fetch_text", return_value=html):
            track = spotify.fetch_track_without_credentials(
                "abc", "https://open.spotify.com/track/abc"
            )

        self.assertEqual(track.title, "Song Title")
        self.assertEqual(track.artists, ("The Artist",))
        self.assertEqual(track.album, "The Album")
        self.assertEqual(track.duration_ms, 180000)

    def test_falls_back_to_oembed_title_when_embed_page_is_unparseable(
        self,
    ) -> None:
        def fake_fetch_text(url: str, *, timeout: float) -> str | None:
            if "oembed" in url:
                return json.dumps({"title": "Song Title"})
            return "<html>no matching script here</html>"

        with mock.patch.object(spotify, "_fetch_text", side_effect=fake_fetch_text):
            track = spotify.fetch_track_without_credentials(
                "abc", "https://open.spotify.com/track/abc"
            )

        self.assertEqual(track.id, "abc")
        self.assertEqual(track.title, "Song Title")
        self.assertEqual(track.artists, ())
        self.assertIsNone(track.duration_ms)

    def test_raises_when_neither_source_is_available(self) -> None:
        with mock.patch.object(spotify, "_fetch_text", return_value=None):
            with self.assertRaises(spotify.SpotifyError):
                spotify.fetch_track_without_credentials(
                    "abc", "https://open.spotify.com/track/abc"
                )


if __name__ == "__main__":
    unittest.main()
