import os
import shutil
import subprocess
import tempfile
import sys
import logging
from dataclasses import dataclass
from typing import Callable, Iterable, Any
from pathlib import Path
import yt_dlp
import threading
import json
import re

import seek.core.spotify as spotify
from seek.core.journal import CompletionIndex, _REQUIRED_OUTPUT_FILENAMES
from seek.models.links import is_youtube_url, normalize_youtube_url, is_single_video_url
from seek.utils.system import check_dependencies, detect_javascript_runtimes, DependencyReport

EventCallback = Callable[["DownloadEvent"], None]

@dataclass(frozen=True)
class DownloadEvent:
    """A thread-safe status update emitted by the downloader."""

    kind: str
    message: str | None = None
    percent: float | None = None
    path: Path | None = None

@dataclass(frozen=True)
class DownloadResult:
    """Summary returned after yt-dlp has finished."""

    completed_videos: int
    had_errors: bool
    skipped_videos: int = 0

class DownloaderError(Exception):
    """Base exception for all downloader errors."""

class MissingDependencyError(DownloaderError):
    """Raised when Python, yt-dlp, FFmpeg, or FFprobe is unavailable."""

class DownloadFailedError(DownloaderError):
    """Raised when yt-dlp cannot complete the requested job."""

class UserCancelledError(DownloaderError):
    """Raised after the user requests cancellation."""

_MAX_CONVERSION_FAILURES = 3

def build_ydl_options(
    url: str,
    output_dir: Path,
    *,
    logger: Any,
    progress_hook: Callable[[dict[str, Any]], None],
    postprocessor_hook: Callable[[dict[str, Any]], None],
    match_filter: Callable[..., str | None],
    javascript_runtimes: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Build the yt-dlp option dictionary used by the application."""

    folder = "%(title).100S [%(id)s]"
    options: dict[str, Any] = {
        "paths": {"home": str(output_dir)},
        "outtmpl": {
            "default": f"{folder}/audio.%(ext)s",
            "thumbnail": f"{folder}/thumbnail.%(ext)s",
        },
        "format": "bestaudio/best",
        "final_ext": "mp3",
        "writethumbnail": True,
        "allow_playlist_files": False,
        "windowsfilenames": True,
        "noplaylist": is_single_video_url(url),
        "lazy_playlist": True,
        "ignoreerrors": "only_download",
        "continuedl": True,
        # Preserve valid artifacts in partial folders. Zero-byte media files
        # are removed selectively before an incomplete item is retried.
        "overwrites": False,
        "retries": 10,
        "fragment_retries": 10,
        "extractor_retries": 3,
        "socket_timeout": 30,
        "noprogress_timeout": 120,
        "quiet": True,
        "no_warnings": False,
        "noprogress": True,
        "logger": logger,
        "progress_hooks": [progress_hook],
        "postprocessor_hooks": [postprocessor_hook],
        "match_filter": match_filter,
        "postprocessors": [
            {
                "key": "FFmpegThumbnailsConvertor",
                "format": "jpg",
                "when": "before_dl",
            },
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            },
        ],
    }

    if javascript_runtimes:
        options["js_runtimes"] = javascript_runtimes

    return options

_DURATION_TOLERANCE_MIN_S = 5.0

_DURATION_TOLERANCE_MAX_S = 30.0

_DURATION_TOLERANCE_RATIO = 0.10

def _duration_tolerance(target_seconds: float) -> float:
    """Return an adaptive tolerance: 10% of track length, clamped to [5, 30]s."""
    return max(
        _DURATION_TOLERANCE_MIN_S,
        min(_DURATION_TOLERANCE_MAX_S, target_seconds * _DURATION_TOLERANCE_RATIO),
    )

def _select_best_match(
    entries: list[dict[str, Any]],
    target_duration_ms: int | None,
) -> dict[str, Any] | None:
    """Pick the search result whose duration is closest to the target."""

    if not entries:
        return None
    if not target_duration_ms:
        return entries[0]

    target_seconds = target_duration_ms / 1000.0
    best: dict[str, Any] | None = None
    best_diff: float | None = None
    for entry in entries:
        duration = entry.get("duration")
        if duration is None:
            continue
        diff = abs(float(duration) - target_seconds)
        tolerance = _duration_tolerance(target_seconds)
        if diff <= tolerance and (best_diff is None or diff < best_diff):
            best, best_diff = entry, diff
    return best or entries[0]

def search_youtube_for_track(
    track: spotify.SpotifyTrack,
    *,
    javascript_runtimes: dict[str, dict[str, str]] | None = None,
    result_count: int = 5,
) -> str | None:
    """Return the URL of the best available YouTube match for a Spotify track."""

    import yt_dlp

    options: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "socket_timeout": 30,
    }
    if javascript_runtimes:
        options["js_runtimes"] = javascript_runtimes

    query = f"ytsearch{result_count}:{track.display_name} audio"
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(query, download=False)

    entries = [entry for entry in (info or {}).get("entries") or [] if entry]
    best = _select_best_match(entries, track.duration_ms)
    if best is None:
        return None

    video_id = best.get("id")
    if video_id:
        return f"https://www.youtube.com/watch?v={video_id}"
    return best.get("url")

def write_info_file(info: dict[str, Any], video_dir: Path) -> Path:
    """Atomically write the title and description for one downloaded video."""

    title = str(info.get("title") or "Untitled")
    description = info.get("description")
    if description is None or not str(description).strip():
        description = "(No description provided.)"
    else:
        description = str(description)

    content = f"Title: {title}\n\nDescription:\n{description.rstrip()}\n"
    destination = video_dir / "info.txt"
    temporary = video_dir / ".info.txt.tmp"
    video_dir.mkdir(parents=True, exist_ok=True)

    try:
        with temporary.open(
            "w",
            encoding="utf-8",
            errors="replace",
            newline="\n",
        ) as handle:
            handle.write(content)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()

    return destination

def download_url(
    url: str,
    output_dir: Path,
    callback: EventCallback,
    cancel_event: threading.Event,
    *,
    _dependency_report: DependencyReport | None = None,
    _javascript_runtimes: dict[str, dict[str, str]] | None = None,
    _announce_environment: bool = True,
    _completion_index: CompletionIndex | None = None,
) -> DownloadResult:
    """Download a YouTube video, playlist, or channel into per-video folders."""

    javascript_runtimes = (
        detect_javascript_runtimes()
        if _javascript_runtimes is None
        else _javascript_runtimes
    )
    report = _dependency_report or check_dependencies(javascript_runtimes)
    _raise_for_missing_dependencies(report)

    try:
        import yt_dlp
        from yt_dlp.utils import DownloadCancelled
    except ImportError as exc:
        raise MissingDependencyError(
            'yt-dlp is not installed. Run "python -m pip install -r requirements.txt".'
        ) from exc

    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    completion_index = _completion_index or CompletionIndex(
        output_dir,
        callback,
    )

    completed_ids: set[str] = set()
    incomplete_ids: set[str] = set()
    skipped_ids: set[str] = set()
    prepared_ids: set[str] = set()
    resumed_ids: set[str] = set()
    last_progress_emit = 0.0

    def ensure_not_cancelled() -> None:
        if cancel_event.is_set():
            raise DownloadCancelled("Cancelled by user")

    def progress_hook(data: dict[str, Any]) -> None:
        nonlocal last_progress_emit
        ensure_not_cancelled()
        status = data.get("status")

        if status == "downloading":
            now = time.monotonic()
            if now - last_progress_emit >= 0.15:
                last_progress_emit = now
                callback(_progress_event(data))
        elif status == "finished":
            info = data.get("info_dict") or {}
            video_id = str(info.get("id") or "").strip()
            filename = data.get("filename")
            if video_id and filename:
                completion_index.mark_downloaded(
                    video_id,
                    Path(str(filename)),
                )
            title = str(info.get("title") or "video")
            callback(
                DownloadEvent(
                    "processing",
                    f'Converting "{title}" to MP3 and finishing its files…',
                )
            )
        elif status == "error":
            callback(DownloadEvent("error", "yt-dlp reported a download error."))

    def postprocessor_hook(data: dict[str, Any]) -> None:
        ensure_not_cancelled()
        if data.get("status") == "started":
            name = str(data.get("postprocessor") or "postprocessor")
            callback(DownloadEvent("processing", f"Running {name}…"))

    def match_filter(info: dict[str, Any], *, incomplete: bool) -> str | None:
        del incomplete
        ensure_not_cancelled()
        if info.get("live_status") in {"is_live", "is_upcoming"}:
            return "Live and upcoming streams are skipped"

        video_id = str(info.get("id") or "").strip()
        stage, recorded_folder = completion_index.find_status(video_id)
        if stage == "complete" and recorded_folder is not None:
            if video_id not in skipped_ids:
                skipped_ids.add(video_id)
                title = str(info.get("title") or video_id)
                callback(
                    DownloadEvent(
                        "video_skipped",
                        f'Skipped "{title}" — already complete',
                        percent=100.0,
                        path=recorded_folder,
                    )
                )
            return "Already downloaded and fully processed in this destination"

        if (
            stage is not None
            and completion_index.failure_count(video_id)
            >= _MAX_CONVERSION_FAILURES
        ):
            title = str(info.get("title") or video_id)
            callback(
                DownloadEvent(
                    "error",
                    f'Skipping "{title}" — conversion has failed '
                    f"{_MAX_CONVERSION_FAILURES} times; remove its folder "
                    "to retry.",
                )
            )
            return (
                f"Conversion failed {_MAX_CONVERSION_FAILURES} times"
            )

        if stage in {"downloaded", "converted"} and video_id not in resumed_ids:
            resumed_ids.add(video_id)
            title = str(info.get("title") or video_id)
            callback(
                DownloadEvent(
                    "log",
                    f'Resuming "{title}" from the {stage} phase.',
                    path=recorded_folder,
                )
            )

        if video_id and video_id not in prepared_ids:
            prepared_ids.add(video_id)
            removed = completion_index.remove_empty_media_artifacts(video_id)
            if removed:
                callback(
                    DownloadEvent(
                        "log",
                        "Removed empty partial "
                        f"{'file' if len(removed) == 1 else 'files'} for "
                        f"{video_id}: {', '.join(removed)}",
                    )
                )
        return None

    logger = _YtDlpLogger(callback)
    options = build_ydl_options(
        url,
        output_dir,
        logger=logger,
        progress_hook=progress_hook,
        postprocessor_hook=postprocessor_hook,
        match_filter=match_filter,
        javascript_runtimes=javascript_runtimes,
    )

    class WriteInfoPostProcessor(yt_dlp.postprocessor.PostProcessor):
        """Create info.txt after yt-dlp has moved the final MP3."""

        def run(self, info: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
            ensure_not_cancelled()
            filepath = info.get("filepath") or info.get("_filename")
            if not filepath:
                raise yt_dlp.utils.PostProcessingError(
                    "Could not determine the final output folder"
                )

            video_dir = Path(str(filepath)).parent
            video_id = str(info.get("id") or filepath).strip()
            completion_index.mark_converted(video_id, video_dir)
            write_info_file(info, video_dir)

            missing_files = [
                filename
                for filename in _REQUIRED_OUTPUT_FILENAMES
                if not (video_dir / filename).is_file()
                or (video_dir / filename).stat().st_size == 0
            ]
            if missing_files:
                incomplete_ids.add(video_id)
                completion_index.record_failure(video_id)
                title = str(info.get("title") or "Untitled")
                callback(
                    DownloadEvent(
                        "error",
                        f'"{title}" is incomplete; missing: '
                        f'{", ".join(missing_files)}',
                        path=video_dir,
                    )
                )
                return [], info

            incomplete_ids.discard(video_id)
            if not completion_index.mark_complete(video_id, video_dir):
                incomplete_ids.add(video_id)
                title = str(info.get("title") or "Untitled")
                callback(
                    DownloadEvent(
                        "error",
                        f'"{title}" changed while its output files were '
                        "being verified; it was not marked complete.",
                        path=video_dir,
                    )
                )
                return [], info

            if video_id not in completed_ids:
                completed_ids.add(video_id)
                title = str(info.get("title") or "Untitled")
                callback(
                    DownloadEvent(
                        "video_complete",
                        f'Saved "{title}"',
                        percent=100.0,
                        path=video_dir,
                    )
                )
            return [], info

    if _announce_environment:
        callback(DownloadEvent("log", f"Output folder: {output_dir}"))
        if report.javascript_runtime:
            callback(
                DownloadEvent(
                    "log",
                    f"JavaScript runtime: {report.javascript_runtime}",
                )
            )
        else:
            callback(
                DownloadEvent(
                    "warning",
                    "No supported JavaScript runtime was found. "
                    "Install Deno or Node.js for full YouTube support.",
                )
            )

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.add_post_processor(WriteInfoPostProcessor(), when="after_move")
            return_code = ydl.download([url])
    except DownloadCancelled as exc:
        raise UserCancelledError("Download cancelled.") from exc
    except yt_dlp.utils.DownloadError as exc:
        if cancel_event.is_set():
            raise UserCancelledError("Download cancelled.") from exc
        raise DownloadFailedError(str(exc)) from exc
    except OSError as exc:
        if cancel_event.is_set():
            raise UserCancelledError("Download cancelled.") from exc
        raise DownloadFailedError(f"File system error: {exc}") from exc

    if cancel_event.is_set():
        raise UserCancelledError("Download cancelled.")

    return DownloadResult(
        completed_videos=len(completed_ids),
        had_errors=bool(return_code or incomplete_ids),
        skipped_videos=len(skipped_ids),
    )

def expand_input_urls(
    urls: Iterable[str],
    callback: EventCallback,
    cancel_event: threading.Event,
    *,
    javascript_runtimes: dict[str, dict[str, str]] | None = None,
) -> list[str]:
    """Resolve Spotify links to matching YouTube URLs; pass YouTube URLs through."""

    resolved: list[str] = []
    seen: set[str] = set()
    spotify_client: spotify.SpotifyClient | None = None

    def add(url: str) -> None:
        if url not in seen:
            seen.add(url)
            resolved.append(url)

    def resolve_spotify_tracks(
        kind: str,
        spotify_id: str,
        url: str,
    ) -> list[spotify.SpotifyTrack]:
        nonlocal spotify_client

        credentials = spotify.load_credentials()
        if credentials is None:
            if kind == "track":
                return [spotify.fetch_track_without_credentials(spotify_id, url)]
            raise spotify.SpotifyAuthError(
                f"Downloading a Spotify {kind} needs a free Client ID/Secret "
                "— add one from File → Spotify settings…"
            )

        if spotify_client is None:
            spotify_client = spotify.SpotifyClient(*credentials)
        return spotify_client.resolve(kind, spotify_id)

    for url in urls:
        if cancel_event.is_set():
            raise UserCancelledError("Download cancelled.")

        if is_youtube_url(url):
            add(url)
            continue

        parsed = spotify.parse_spotify_url(url)
        if parsed is None:
            callback(
                DownloadEvent(
                    "error",
                    f"Not a supported YouTube or Spotify link: {url}",
                )
            )
            continue
        kind, spotify_id = parsed

        try:
            callback(DownloadEvent("processing", f"Reading Spotify {kind}…"))
            tracks = resolve_spotify_tracks(kind, spotify_id, url)
        except spotify.SpotifyError as exc:
            callback(DownloadEvent("error", f"Spotify: {exc}"))
            continue

        if not tracks:
            callback(
                DownloadEvent("warning", f"No tracks found on Spotify for {url}")
            )
            continue

        callback(
            DownloadEvent(
                "log",
                f"Found {len(tracks)} "
                f"{'track' if len(tracks) == 1 else 'tracks'} on Spotify; "
                "matching each to YouTube…",
            )
        )
        for index, track in enumerate(tracks, start=1):
            if cancel_event.is_set():
                raise UserCancelledError("Download cancelled.")

            prefix = f"[{index}/{len(tracks)}]"
            callback(
                DownloadEvent(
                    "processing",
                    f'{prefix} Matching "{track.display_name}" on YouTube…',
                )
            )
            try:
                match = search_youtube_for_track(
                    track,
                    javascript_runtimes=javascript_runtimes,
                )
            except Exception as exc:
                callback(
                    DownloadEvent(
                        "warning",
                        f'{prefix} Could not search YouTube for '
                        f'"{track.display_name}": {exc}',
                    )
                )
                continue

            if match is None:
                callback(
                    DownloadEvent(
                        "warning",
                        f'{prefix} No YouTube match found for '
                        f'"{track.display_name}"',
                    )
                )
                continue

            callback(
                DownloadEvent(
                    "log",
                    f'{prefix} Matched "{track.display_name}" to {match}',
                )
            )
            add(match)

    return resolved

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

class _YtDlpLogger:
    """Translate yt-dlp log calls into application events."""

    def __init__(self, callback: EventCallback) -> None:
        self._callback = callback

    @staticmethod
    def _clean(message: Any) -> str:
        return _ANSI_ESCAPE.sub("", str(message)).strip()

    def debug(self, message: Any) -> None:
        clean = self._clean(message)
        if clean and not clean.startswith("[debug] "):
            self._callback(DownloadEvent("log", clean))

    def info(self, message: Any) -> None:
        clean = self._clean(message)
        if clean:
            self._callback(DownloadEvent("log", clean))

    def warning(self, message: Any) -> None:
        clean = self._clean(message)
        if clean:
            self._callback(DownloadEvent("warning", clean))

    def error(self, message: Any) -> None:
        clean = self._clean(message)
        if clean:
            self._callback(DownloadEvent("error", clean))

def _raise_for_missing_dependencies(report: DependencyReport) -> None:
    if not report.missing_required:
        return

    items = "\n".join(f"• {item}" for item in report.missing_required)
    raise MissingDependencyError(f"Missing required software:\n{items}")

def download_urls(
    urls: Iterable[str],
    output_dir: Path,
    callback: EventCallback,
    cancel_event: threading.Event,
) -> DownloadResult:
    """Download multiple independent input URLs, continuing after item errors."""

    unique_urls = list(dict.fromkeys(url.strip() for url in urls if url.strip()))
    if cancel_event.is_set():
        raise UserCancelledError("Download cancelled.")

    javascript_runtimes = detect_javascript_runtimes()
    dependency_report = check_dependencies(javascript_runtimes)
    _raise_for_missing_dependencies(dependency_report)

    resolved_urls = expand_input_urls(
        unique_urls,
        callback,
        cancel_event,
        javascript_runtimes=javascript_runtimes,
    )

    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    completion_index = CompletionIndex(output_dir, callback)

    completed_videos = 0
    skipped_videos = 0
    handled_paths: set[str] = set()
    had_errors = False
    total_inputs = len(resolved_urls)

    for index, url in enumerate(resolved_urls, start=1):
        if cancel_event.is_set():
            raise UserCancelledError("Download cancelled.")

        prefix = f"[{index}/{total_inputs}]"
        callback(
            DownloadEvent(
                "processing",
                f"{prefix} Starting input…",
            )
        )
        callback(DownloadEvent("log", f"{prefix} {url}"))

        source_completion_events = 0
        source_skip_events = 0

        def forward_event(event: DownloadEvent) -> None:
            nonlocal completed_videos, skipped_videos
            nonlocal source_completion_events, source_skip_events

            if event.kind == "video_complete":
                source_completion_events += 1
                if event.path is not None:
                    path_key = os.path.normcase(
                        str(event.path.resolve(strict=False))
                    )
                    if path_key in handled_paths:
                        return
                    handled_paths.add(path_key)
                completed_videos += 1
            elif event.kind == "video_skipped":
                source_skip_events += 1
                if event.path is not None:
                    path_key = os.path.normcase(
                        str(event.path.resolve(strict=False))
                    )
                    if path_key in handled_paths:
                        return
                    handled_paths.add(path_key)
                skipped_videos += 1

            if event.kind in {"progress", "processing"}:
                event = DownloadEvent(
                    event.kind,
                    f"{prefix} {event.message}",
                    event.percent,
                    event.path,
                )
            callback(event)

        try:
            result = download_url(
                url,
                output_dir,
                forward_event,
                cancel_event,
                _dependency_report=dependency_report,
                _javascript_runtimes=javascript_runtimes,
                _announce_environment=index == 1,
                _completion_index=completion_index,
            )
        except (MissingDependencyError, UserCancelledError):
            raise
        except DownloadFailedError as exc:
            had_errors = True
            callback(
                DownloadEvent(
                    "error",
                    f"Input {index} failed: {exc}",
                )
            )
            continue

        completed_videos += max(
            0,
            result.completed_videos - source_completion_events,
        )
        skipped_videos += max(
            0,
            result.skipped_videos - source_skip_events,
        )
        had_errors = had_errors or result.had_errors
        if result.completed_videos == 0 and result.skipped_videos == 0:
            had_errors = True
            callback(
                DownloadEvent(
                    "warning",
                    f"{prefix} No complete videos were produced.",
                )
            )

    return DownloadResult(
        completed_videos=completed_videos,
        had_errors=had_errors,
        skipped_videos=skipped_videos,
    )

