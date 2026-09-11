from urllib.parse import parse_qs, urlparse
import re


def is_youtube_url(value: str) -> bool:
    """Return True for an HTTP(S) URL hosted by YouTube."""

    try:
        parsed = urlparse(value.strip())
    except ValueError:
        return False

    if parsed.scheme.lower() not in {"http", "https"}:
        return False

    host = (parsed.hostname or "").rstrip(".").lower()
    return (
        host == "youtu.be"
        or host.endswith(".youtu.be")
        or host == "youtube.com"
        or host.endswith(".youtube.com")
        or host == "youtube-nocookie.com"
        or host.endswith(".youtube-nocookie.com")
    )


def is_single_video_url(value: str) -> bool:
    """Distinguish a video URL from an explicit playlist or channel URL."""

    parsed = urlparse(value.strip())
    host = (parsed.hostname or "").rstrip(".").lower()
    path = parsed.path.rstrip("/").lower()

    if host == "youtu.be" or host.endswith(".youtu.be"):
        return True

    if path == "/watch":
        return any(parse_qs(parsed.query).get("v", ()))

    return (
        path.startswith("/shorts/")
        or path.startswith("/live/")
        or path.startswith("/embed/")
    )


def normalize_youtube_url(url: str) -> str:
    """Return a canonical form of a YouTube URL for deduplication.

    Video URLs are normalized to ``https://www.youtube.com/watch?v=VIDEO_ID``.
    Playlist and channel URLs keep their original path but get a normalized
    host.  Non-YouTube URLs are returned unchanged.
    """

    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return url

    host = (parsed.hostname or "").rstrip(".").lower()
    is_yt = (
        host == "youtu.be"
        or host.endswith(".youtu.be")
        or host == "youtube.com"
        or host.endswith(".youtube.com")
        or host == "youtube-nocookie.com"
        or host.endswith(".youtube-nocookie.com")
    )
    if not is_yt:
        return url

    # Extract video ID from the various YouTube URL formats.
    video_id: str | None = None
    path = parsed.path.rstrip("/")

    if host == "youtu.be" or host.endswith(".youtu.be"):
        # youtu.be/VIDEO_ID
        video_id = path.lstrip("/").split("/")[0] or None
    elif path == "/watch":
        ids = parse_qs(parsed.query).get("v", [])
        video_id = ids[0] if ids else None
    elif path.startswith(("/shorts/", "/live/", "/embed/")):
        # /shorts/VIDEO_ID, /live/VIDEO_ID, /embed/VIDEO_ID
        parts = path.split("/")
        video_id = parts[2] if len(parts) > 2 else None

    if video_id:
        return f"https://www.youtube.com/watch?v={video_id}"

    # Playlist, channel, or other non-video URL — normalize the host only.
    normalized = parsed._replace(
        scheme="https",
        netloc="www.youtube.com",
    )
    return normalized.geturl()


def parse_url_entries(value: str) -> list[tuple[int, str]]:
    """Return nonblank URL entries with their original one-based line number."""

    entries: list[tuple[int, str]] = []
    for line_number, line in enumerate(value.splitlines(), start=1):
        url = line.strip().lstrip("\ufeff")
        if url:
            entries.append((line_number, url))
    return entries


def parse_url_list(value: str) -> list[str]:
    """Parse one URL per line, removing blanks and exact duplicates."""

    urls: list[str] = []
    seen: set[str] = set()
    for _line_number, url in parse_url_entries(value):
        if url not in seen:
            urls.append(url)
            seen.add(url)
    return urls
