"""Spotify link resolution: turn a Spotify link into track metadata.

Spotify's own audio streams are DRM-protected and are not fetched here. This
module only reads public track/playlist metadata (title, artists, album,
duration) so the caller can find and download matching audio elsewhere (see
``downloader.search_youtube_for_track``).

Two ways to read that metadata are supported:

* A single track can be read with no credentials at all, using Spotify's
  public oEmbed endpoint and embed page. This is best-effort: the embed
  page's structure is undocumented and Spotify can change it at any time, so
  parsing failures fall back to the oEmbed title alone rather than raising.
* Albums and playlists require the official Web API, because there is no
  stable, key-free way to list every track in one. That needs a free
  Client ID and Client Secret from a Spotify Developer app (client-credentials
  flow; no user login involved).
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request


_SPOTIFY_KINDS = ("track", "album", "playlist")
_SPOTIFY_ID = re.compile(r"^[A-Za-z0-9]{10,40}$")
_TOKEN_URL = "https://accounts.spotify.com/api/token"
_API_BASE = "https://api.spotify.com/v1"
_OEMBED_URL = "https://open.spotify.com/oembed"
_EMBED_BASE = "https://open.spotify.com/embed"
_NEXT_DATA_PATTERN = re.compile(
    r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>',
    re.DOTALL,
)
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_ENV_CLIENT_ID = "SPOTIFY_CLIENT_ID"
_ENV_CLIENT_SECRET = "SPOTIFY_CLIENT_SECRET"


class SpotifyError(RuntimeError):
    """Base error raised when Spotify metadata cannot be resolved."""


class SpotifyAuthError(SpotifyError):
    """Raised when Spotify API credentials are missing or rejected."""


@dataclass(frozen=True)
class SpotifyTrack:
    """Minimal metadata used to find a matching audio source elsewhere."""

    id: str
    title: str
    artists: tuple[str, ...]
    album: str
    duration_ms: int | None

    @property
    def display_name(self) -> str:
        artist_text = ", ".join(self.artists) if self.artists else "Unknown Artist"
        return f"{artist_text} - {self.title}"


def parse_spotify_url(value: str) -> tuple[str, str] | None:
    """Return (kind, spotify_id) for a track/album/playlist link or URI."""

    text = value.strip()
    if not text:
        return None

    if text.lower().startswith("spotify:"):
        parts = text.split(":")
        if (
            len(parts) == 3
            and parts[1].lower() in _SPOTIFY_KINDS
            and _SPOTIFY_ID.match(parts[2])
        ):
            return parts[1].lower(), parts[2]
        return None

    try:
        parsed = urllib.parse.urlparse(text)
    except ValueError:
        return None

    if parsed.scheme.lower() not in {"http", "https"}:
        return None

    host = (parsed.hostname or "").rstrip(".").lower()
    if host != "open.spotify.com" and not host.endswith(".open.spotify.com"):
        return None

    segments = [segment for segment in parsed.path.split("/") if segment]
    if segments and segments[0].lower().startswith("intl-"):
        segments = segments[1:]
    if (
        len(segments) >= 2
        and segments[0].lower() in _SPOTIFY_KINDS
        and _SPOTIFY_ID.match(segments[1])
    ):
        return segments[0].lower(), segments[1]
    return None


def is_spotify_url(value: str) -> bool:
    """Return True for a recognizable Spotify track, album, or playlist link."""

    return parse_spotify_url(value) is not None


def credentials_config_path() -> Path:
    """Return the local file used to persist a user-provided Spotify app key."""

    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        return base / "SEEK" / "spotify.json"
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "seek" / "spotify.json"


def load_credentials() -> tuple[str, str] | None:
    """Return (client_id, client_secret) from the environment or config file."""

    env_id = os.environ.get(_ENV_CLIENT_ID, "").strip()
    env_secret = os.environ.get(_ENV_CLIENT_SECRET, "").strip()
    if env_id and env_secret:
        return env_id, env_secret

    path = credentials_config_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

    if not isinstance(raw, dict):
        return None
    client_id = str(raw.get("client_id") or "").strip()
    client_secret = str(raw.get("client_secret") or "").strip()
    if client_id and client_secret:
        return client_id, client_secret
    return None


def save_credentials(client_id: str, client_secret: str) -> Path:
    """Persist a Spotify Client ID/Secret to the local config file."""

    path = credentials_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"client_id": client_id.strip(), "client_secret": client_secret.strip()}
    temporary = path.with_name(f"{path.name}.tmp")

    temporary.write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    os.replace(temporary, path)
    return path


def _track_from_payload(payload: dict[str, Any]) -> SpotifyTrack | None:
    if not payload or payload.get("id") is None:
        return None
    artists = tuple(
        str(artist.get("name"))
        for artist in payload.get("artists") or []
        if isinstance(artist, dict) and artist.get("name")
    )
    album = payload.get("album") or {}
    duration = payload.get("duration_ms")
    return SpotifyTrack(
        id=str(payload["id"]),
        title=str(payload.get("name") or "Untitled"),
        artists=artists,
        album=str(album.get("name") or "") if isinstance(album, dict) else "",
        duration_ms=int(duration) if isinstance(duration, (int, float)) else None,
    )


class SpotifyClient:
    """Reads public catalog metadata via the official Spotify Web API.

    Uses the client-credentials flow (app-only auth, no user login) with a
    Client ID/Secret from a free Spotify Developer app.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        timeout: float = 15.0,
    ) -> None:
        if not client_id or not client_secret:
            raise SpotifyAuthError(
                "Spotify Client ID and Client Secret are required."
            )
        self._client_id = client_id
        self._client_secret = client_secret
        self._timeout = timeout
        self._token: str | None = None
        self._token_expiry = 0.0
        self._lock = threading.Lock()

    def _request_token(self) -> None:
        credentials = f"{self._client_id}:{self._client_secret}".encode("utf-8")
        header_value = base64.b64encode(credentials).decode("ascii")
        data = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode(
            "ascii"
        )
        request = urllib.request.Request(
            _TOKEN_URL,
            data=data,
            headers={
                "Authorization": f"Basic {header_value}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in (400, 401):
                raise SpotifyAuthError(
                    "Spotify rejected the Client ID/Secret. Check Spotify "
                    "settings."
                ) from exc
            raise SpotifyError(
                f"Spotify authentication failed ({exc.code})."
            ) from exc
        except urllib.error.URLError as exc:
            raise SpotifyError(f"Could not reach Spotify: {exc.reason}") from exc

        token = payload.get("access_token")
        if not token:
            raise SpotifyAuthError("Spotify did not return an access token.")
        self._token = str(token)
        expires_in = payload.get("expires_in", 0)
        try:
            expires_in = int(expires_in)
        except (TypeError, ValueError):
            expires_in = 0
        self._token_expiry = time.monotonic() + max(0, expires_in - 30)

    def _access_token(self, *, force_refresh: bool = False) -> str:
        with self._lock:
            if (
                force_refresh
                or self._token is None
                or time.monotonic() >= self._token_expiry
            ):
                self._request_token()
            assert self._token is not None
            return self._token

    def _get_json(self, url: str) -> dict[str, Any]:
        def attempt(*, force_refresh: bool) -> dict[str, Any]:
            request = urllib.request.Request(
                url,
                headers={"Authorization": f"Bearer {self._access_token(force_refresh=force_refresh)}"},
            )
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return json.loads(response.read().decode("utf-8"))

        try:
            return attempt(force_refresh=False)
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                try:
                    return attempt(force_refresh=True)
                except urllib.error.HTTPError as retry_exc:
                    raise SpotifyError(
                        f"Spotify API error ({retry_exc.code})."
                    ) from retry_exc
            if exc.code == 404:
                raise SpotifyError(
                    "That Spotify item was not found, or it is private."
                ) from exc
            if exc.code == 429:
                raise SpotifyError(
                    "Spotify's rate limit was hit. Try again shortly."
                ) from exc
            raise SpotifyError(f"Spotify API error ({exc.code}).") from exc
        except urllib.error.URLError as exc:
            raise SpotifyError(f"Could not reach Spotify: {exc.reason}") from exc

    def get_track(self, track_id: str) -> SpotifyTrack:
        payload = self._get_json(f"{_API_BASE}/tracks/{track_id}")
        track = _track_from_payload(payload)
        if track is None:
            raise SpotifyError("Could not read that Spotify track.")
        return track

    def get_album_tracks(self, album_id: str) -> list[SpotifyTrack]:
        album = self._get_json(f"{_API_BASE}/albums/{album_id}")
        album_name = str(album.get("name") or "")

        tracks: list[SpotifyTrack] = []
        url: str | None = f"{_API_BASE}/albums/{album_id}/tracks?limit=50"
        while url:
            page = self._get_json(url)
            for item in page.get("items") or []:
                if not isinstance(item, dict) or item.get("id") is None:
                    continue
                artists = tuple(
                    str(artist.get("name"))
                    for artist in item.get("artists") or []
                    if isinstance(artist, dict) and artist.get("name")
                )
                duration = item.get("duration_ms")
                tracks.append(
                    SpotifyTrack(
                        id=str(item["id"]),
                        title=str(item.get("name") or "Untitled"),
                        artists=artists,
                        album=album_name,
                        duration_ms=(
                            int(duration)
                            if isinstance(duration, (int, float))
                            else None
                        ),
                    )
                )
            url = page.get("next")
        return tracks

    def get_playlist_tracks(self, playlist_id: str) -> list[SpotifyTrack]:
        tracks: list[SpotifyTrack] = []
        url: str | None = f"{_API_BASE}/playlists/{playlist_id}/tracks?limit=100"
        while url:
            page = self._get_json(url)
            for item in page.get("items") or []:
                if not isinstance(item, dict):
                    continue
                track_payload = item.get("track")
                if not isinstance(track_payload, dict) or track_payload.get(
                    "is_local"
                ):
                    continue
                track = _track_from_payload(track_payload)
                if track is not None:
                    tracks.append(track)
            url = page.get("next")
        return tracks

    def resolve(self, kind: str, spotify_id: str) -> list[SpotifyTrack]:
        """Return the tracks for a parsed (kind, spotify_id) pair."""

        if kind == "track":
            return [self.get_track(spotify_id)]
        if kind == "album":
            return self.get_album_tracks(spotify_id)
        if kind == "playlist":
            return self.get_playlist_tracks(spotify_id)
        raise SpotifyError(f"Unsupported Spotify link type: {kind}")


def _fetch_text(url: str, *, timeout: float) -> str | None:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": _BROWSER_USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError):
        return None


def _extract_embed_entity(html: str) -> dict[str, Any] | None:
    match = _NEXT_DATA_PATTERN.search(html)
    if match is None:
        return None
    try:
        payload = json.loads(match.group(1))
    except ValueError:
        return None
    try:
        return payload["props"]["pageProps"]["state"]["data"]["entity"]
    except (KeyError, TypeError):
        return None


def _track_from_embed_entity(entity: dict[str, Any]) -> SpotifyTrack | None:
    track_id = entity.get("id")
    if not track_id:
        uri = str(entity.get("uri") or "")
        track_id = uri.rsplit(":", 1)[-1] if uri else None
    name = entity.get("name") or entity.get("title")
    if not track_id or not name:
        return None

    artists = tuple(
        str(artist.get("name"))
        for artist in entity.get("artists") or []
        if isinstance(artist, dict) and artist.get("name")
    )
    album = entity.get("albumOfTrack")
    if not isinstance(album, dict):
        album = entity.get("album") if isinstance(entity.get("album"), dict) else {}
    duration = entity.get("duration")
    if duration is None:
        duration = entity.get("duration_ms")

    return SpotifyTrack(
        id=str(track_id),
        title=str(name),
        artists=artists,
        album=str(album.get("name") or ""),
        duration_ms=int(duration) if isinstance(duration, (int, float)) else None,
    )


def _fetch_oembed_title(url: str, *, timeout: float) -> str | None:
    query = urllib.parse.urlencode({"url": url})
    text = _fetch_text(f"{_OEMBED_URL}?{query}", timeout=timeout)
    if text is None:
        return None
    try:
        payload = json.loads(text)
    except ValueError:
        return None
    title = payload.get("title") if isinstance(payload, dict) else None
    title = str(title).strip() if title else ""
    return title or None


def fetch_track_without_credentials(
    spotify_id: str,
    url: str,
    *,
    timeout: float = 15.0,
) -> SpotifyTrack:
    """Best-effort single-track metadata using only key-free public pages.

    Tries Spotify's public embed page first, since it can carry the artist
    name and duration. That page's structure is undocumented, so a parsing
    miss falls back to the oEmbed endpoint's track title alone.
    """

    html = _fetch_text(f"{_EMBED_BASE}/track/{spotify_id}", timeout=timeout)
    if html is not None:
        entity = _extract_embed_entity(html)
        if entity is not None:
            track = _track_from_embed_entity(entity)
            if track is not None:
                return track

    title = _fetch_oembed_title(url, timeout=timeout)
    if title is None:
        raise SpotifyError(
            "Could not read that Spotify track without an API key. Add a "
            "free Client ID/Secret from File → Spotify settings… and try "
            "again."
        )
    return SpotifyTrack(id=spotify_id, title=title, artists=(), album="", duration_ms=None)
