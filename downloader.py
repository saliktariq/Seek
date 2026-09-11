"""Core download logic for the YouTube Audio Saver application."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import spotify
import subprocess
import sys
import sysconfig
import threading
import time
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse


EventCallback = Callable[["DownloadEvent"], None]


@dataclass(frozen=True)
class DownloadEvent:
    """A thread-safe status update emitted by the downloader."""

    kind: str
    message: str = ""
    percent: float | None = None
    path: Path | None = None


@dataclass(frozen=True)
class DownloadResult:
    """Summary returned after yt-dlp has finished."""

    completed_videos: int
    had_errors: bool
    skipped_videos: int = 0


@dataclass(frozen=True)
class DependencyReport:
    """Result of checking the local runtime before a download."""

    missing_required: tuple[str, ...]
    javascript_runtime: str | None


class DownloaderError(RuntimeError):
    """Base error shown to the user."""


class MissingDependencyError(DownloaderError):
    """Raised when Python, yt-dlp, FFmpeg, or FFprobe is unavailable."""


class DownloadFailedError(DownloaderError):
    """Raised when yt-dlp cannot complete the requested job."""


class UserCancelledError(DownloaderError):
    """Raised after the user requests cancellation."""


_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_COMPLETION_INDEX_FILENAME = ".youtube-audio-completed.json"
_COMPLETION_INDEX_VERSION = 2
_REQUIRED_OUTPUT_FILENAMES = ("audio.mp3", "thumbnail.jpg", "info.txt")
_MAX_CONVERSION_FAILURES = 3
_VIDEO_FOLDER_ID = re.compile(r"\[([A-Za-z0-9_-]+)\]$")
_VIDEO_STAGE_RANK = {
    "downloaded": 1,
    "converted": 2,
    "complete": 3,
}
_JAVASCRIPT_RUNTIMES = (
    (
        "deno",
        "Deno",
        "deno",
        ("--version",),
        re.compile(r"(?im)^deno\s+(\S+)"),
        (2, 3, 0),
    ),
    (
        "node",
        "Node.js",
        "node",
        ("--version",),
        re.compile(r"(?im)^v(\S+)"),
        (22, 0, 0),
    ),
    (
        "quickjs",
        "QuickJS",
        "qjs",
        ("--help",),
        re.compile(r"(?im)^QuickJS(?:-ng)?\s+version\s+(\S+)"),
        (2023, 12, 9),
    ),
    (
        "bun",
        "Bun",
        "bun",
        ("--version",),
        re.compile(r"(?m)^(\S+)"),
        (1, 2, 11),
    ),
)
_RUNTIME_DISPLAY_NAMES = {
    key: display_name
    for key, display_name, *_remaining in _JAVASCRIPT_RUNTIMES
}


def _is_nonempty_file(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _is_complete_video_folder(video_dir: Path) -> bool:
    """Return True only when every required output exists and is non-empty."""

    try:
        return video_dir.is_dir() and all(
            _is_nonempty_file(video_dir / filename)
            for filename in _REQUIRED_OUTPUT_FILENAMES
        )
    except OSError:
        return False


def _infer_video_stage(video_dir: Path) -> str | None:
    """Infer the furthest durable stage supported by files on disk."""

    if _is_complete_video_folder(video_dir):
        return "complete"
    if _is_nonempty_file(video_dir / "audio.mp3"):
        return "converted"

    try:
        candidates = list(video_dir.iterdir())
    except OSError:
        return None

    for candidate in candidates:
        name = candidate.name.lower()
        if (
            name.startswith("audio.")
            and not name.endswith((".part", ".tmp", ".ytdl"))
            and _is_nonempty_file(candidate)
        ):
            return "downloaded"
    return None


class CompletionIndex:
    """Destination-local journal for download, conversion, and completion."""

    def __init__(
        self,
        output_dir: Path,
        callback: EventCallback | None = None,
    ) -> None:
        self.output_dir = output_dir.expanduser().resolve()
        self.path = self.output_dir / _COMPLETION_INDEX_FILENAME
        self._callback = callback
        self._entries: dict[str, str] = {}
        self._candidate_folders: dict[str, str] = {}
        self._stages: dict[str, str] = {}
        self._failures: dict[str, int] = {}
        self._lock = threading.RLock()
        self._load_and_reconcile()

    def _emit(self, kind: str, message: str) -> None:
        if self._callback is not None:
            self._callback(DownloadEvent(kind, message))

    def _path_from_entry(self, relative_folder: str) -> Path | None:
        relative = Path(relative_folder)
        if relative.is_absolute() or relative == Path("."):
            return None

        try:
            candidate = (self.output_dir / relative).resolve()
            candidate.relative_to(self.output_dir)
        except (OSError, ValueError):
            return None
        return candidate

    def _relative_folder(self, video_dir: Path) -> str | None:
        try:
            resolved = video_dir.resolve()
            relative = resolved.relative_to(self.output_dir)
        except (OSError, ValueError):
            return None

        if relative == Path("."):
            return None
        return relative.as_posix()

    def _records_locked(self) -> dict[str, dict[str, str | int]]:
        records: dict[str, dict[str, str | int]] = {}
        for video_id, stage in sorted(self._stages.items()):
            if video_id not in self._candidate_folders:
                continue
            record: dict[str, str | int] = {
                "folder": self._candidate_folders[video_id],
                "phase": stage,
            }
            failures = self._failures.get(video_id, 0)
            if failures > 0:
                record["failures"] = failures
            records[video_id] = record
        return records

    def _set_stage_locked(
        self,
        video_id: str,
        relative_folder: str,
        stage: str | None,
    ) -> bool:
        before = (
            self._candidate_folders.get(video_id),
            self._stages.get(video_id),
            self._entries.get(video_id),
        )
        self._candidate_folders[video_id] = relative_folder
        if stage is None:
            self._stages.pop(video_id, None)
            self._entries.pop(video_id, None)
        else:
            self._stages[video_id] = stage
            if stage == "complete":
                self._entries[video_id] = relative_folder
            else:
                self._entries.pop(video_id, None)
        after = (
            self._candidate_folders.get(video_id),
            self._stages.get(video_id),
            self._entries.get(video_id),
        )
        return before != after

    def _load_and_reconcile(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        # Clean up stale temporary files from interrupted saves.
        stale_tmp = self.path.with_name(f"{self.path.name}.tmp")
        try:
            stale_tmp.unlink(missing_ok=True)
        except OSError:
            pass
        index_existed = self.path.exists()
        index_was_invalid = False
        needs_upgrade = False

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raw = None
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raw = None
            index_was_invalid = True
            self._emit(
                "warning",
                f"Could not read the completion index; rebuilding it: {exc}",
            )

        if raw is None and index_existed and not index_was_invalid:
            index_was_invalid = True
            self._emit(
                "warning",
                "The completion index had an unsupported format; "
                "rebuilding it.",
            )
        elif raw is not None:
            videos = raw.get("videos") if isinstance(raw, dict) else None
            version = raw.get("version") if isinstance(raw, dict) else None
            if version in {1, _COMPLETION_INDEX_VERSION} and isinstance(
                videos,
                dict,
            ):
                needs_upgrade = version != _COMPLETION_INDEX_VERSION
                for video_id, entry in videos.items():
                    folder = (
                        entry.get("folder")
                        if isinstance(entry, dict)
                        else None
                    )
                    stage = (
                        "complete"
                        if version == 1
                        else entry.get("phase")
                        if isinstance(entry, dict)
                        else None
                    )
                    if (
                        isinstance(video_id, str)
                        and video_id
                        and isinstance(folder, str)
                        and folder
                        and stage in _VIDEO_STAGE_RANK
                    ):
                        self._candidate_folders[video_id] = folder
                        self._stages[video_id] = stage
                        if stage == "complete":
                            self._entries[video_id] = folder
                        failures = (
                            entry.get("failures", 0)
                            if isinstance(entry, dict)
                            else 0
                        )
                        if isinstance(failures, int) and failures > 0:
                            self._failures[video_id] = failures
                    else:
                        needs_upgrade = True
            else:
                index_was_invalid = True
                self._emit(
                    "warning",
                    "The completion index had an unsupported format; "
                    "rebuilding it.",
                )

        loaded_records = self._records_locked()
        for video_id, relative_folder in list(
            self._candidate_folders.items()
        ):
            video_dir = self._path_from_entry(relative_folder)
            if video_dir is None or not video_dir.is_dir():
                self._candidate_folders.pop(video_id, None)
                self._stages.pop(video_id, None)
                self._entries.pop(video_id, None)
                continue
            self._set_stage_locked(
                video_id,
                relative_folder,
                _infer_video_stage(video_dir),
            )

        try:
            candidates = sorted(
                self.output_dir.iterdir(),
                key=lambda item: item.name.casefold(),
            )
        except OSError as exc:
            candidates = []
            self._emit(
                "warning",
                f"Could not scan the destination for completed videos: {exc}",
            )

        for candidate in candidates:
            match = _VIDEO_FOLDER_ID.search(candidate.name)
            relative_folder = self._relative_folder(candidate)
            if (
                match is None
                or relative_folder is None
                or not candidate.is_dir()
            ):
                continue
            video_id = match.group(1)
            inferred_stage = _infer_video_stage(candidate)
            existing_stage = self._stages.get(video_id)
            if (
                existing_stage is None
                or (
                    inferred_stage is not None
                    and _VIDEO_STAGE_RANK[inferred_stage]
                    > _VIDEO_STAGE_RANK[existing_stage]
                )
            ):
                self._set_stage_locked(
                    video_id,
                    relative_folder,
                    inferred_stage,
                )
            else:
                self._candidate_folders.setdefault(
                    video_id,
                    relative_folder,
                )

        if (
            self._records_locked() != loaded_records
            or index_was_invalid
            or needs_upgrade
            or (self._stages and not index_existed)
        ):
            with self._lock:
                self._save_locked()

        complete_count = len(self._entries)
        partial_count = len(self._stages) - complete_count
        if complete_count or partial_count:
            parts = []
            if complete_count:
                parts.append(
                    f"{complete_count} complete "
                    f"{'video' if complete_count == 1 else 'videos'}"
                )
            if partial_count:
                parts.append(
                    f"{partial_count} resumable partial "
                    f"{'video' if partial_count == 1 else 'videos'}"
                )
            self._emit(
                "log",
                f"Resume check: found {' and '.join(parts)} "
                "in this destination.",
            )

    def _save_locked(self) -> None:
        payload = {
            "version": _COMPLETION_INDEX_VERSION,
            "videos": self._records_locked(),
        }
        temporary = self.path.with_name(f"{self.path.name}.tmp")

        try:
            with temporary.open(
                "w",
                encoding="utf-8",
                errors="strict",
                newline="\n",
            ) as handle:
                json.dump(
                    payload,
                    handle,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            self._emit(
                "warning",
                f"Could not update the completion index: {exc}",
            )

    def find_status(self, video_id: str) -> tuple[str | None, Path | None]:
        """Return the phase and folder after reconciling against disk."""

        normalized_id = str(video_id or "").strip()
        if not normalized_id:
            return None, None

        with self._lock:
            relative_folder = self._candidate_folders.get(normalized_id)
            if relative_folder is None:
                return None, None

            video_dir = self._path_from_entry(relative_folder)
            if video_dir is None or not video_dir.is_dir():
                had_stage = normalized_id in self._stages
                self._candidate_folders.pop(normalized_id, None)
                self._stages.pop(normalized_id, None)
                self._entries.pop(normalized_id, None)
                if had_stage:
                    self._save_locked()
                return None, None

            inferred_stage = _infer_video_stage(video_dir)
            changed = self._set_stage_locked(
                normalized_id,
                relative_folder,
                inferred_stage,
            )
            if changed:
                self._save_locked()
            return inferred_stage, video_dir

    def find_complete(self, video_id: str) -> Path | None:
        """Return the validated completed folder for *video_id*, if present."""

        stage, video_dir = self.find_status(video_id)
        return video_dir if stage == "complete" else None

    def remove_empty_media_artifacts(self, video_id: str) -> tuple[str, ...]:
        """Remove only zero-byte media files from a known partial folder."""

        normalized_id = str(video_id or "").strip()
        with self._lock:
            relative_folder = self._candidate_folders.get(normalized_id)
            if relative_folder is None:
                return ()
            video_dir = self._path_from_entry(relative_folder)
            if video_dir is None:
                return ()

            try:
                candidates = list(video_dir.iterdir())
            except OSError:
                return ()

            removed: list[str] = []
            for candidate in candidates:
                name = candidate.name.lower()
                if not (
                    name.startswith("audio.")
                    or name.startswith("thumbnail.")
                ):
                    continue
                try:
                    if candidate.is_file() and candidate.stat().st_size == 0:
                        candidate.unlink()
                        removed.append(candidate.name)
                except OSError:
                    continue
            if removed:
                self._set_stage_locked(
                    normalized_id,
                    relative_folder,
                    _infer_video_stage(video_dir),
                )
                self._save_locked()
            return tuple(sorted(removed, key=str.casefold))

    def _mark_stage(
        self,
        video_id: str,
        video_dir: Path,
        minimum_stage: str,
    ) -> bool:
        normalized_id = str(video_id or "").strip()
        relative_folder = self._relative_folder(video_dir)
        inferred_stage = _infer_video_stage(video_dir)
        if (
            not normalized_id
            or relative_folder is None
            or inferred_stage is None
            or _VIDEO_STAGE_RANK[inferred_stage]
            < _VIDEO_STAGE_RANK[minimum_stage]
        ):
            return False

        with self._lock:
            if self._set_stage_locked(
                normalized_id,
                relative_folder,
                inferred_stage,
            ):
                self._save_locked()
        return True

    def mark_downloaded(self, video_id: str, media_path: Path) -> bool:
        """Record that yt-dlp produced a complete source media file."""

        if (
            media_path.name.lower().endswith((".part", ".tmp", ".ytdl"))
            or not _is_nonempty_file(media_path)
        ):
            return False
        return self._mark_stage(video_id, media_path.parent, "downloaded")

    def mark_converted(self, video_id: str, video_dir: Path) -> bool:
        """Record that a non-empty audio.mp3 exists."""

        return self._mark_stage(video_id, video_dir, "converted")

    def mark_complete(self, video_id: str, video_dir: Path) -> bool:
        """Record a video after its complete output contract is verified."""

        return self._mark_stage(video_id, video_dir, "complete")

    def record_failure(self, video_id: str) -> int:
        """Increment and return the failure count for *video_id*."""
        normalized_id = str(video_id or "").strip()
        if not normalized_id:
            return 0
        with self._lock:
            count = self._failures.get(normalized_id, 0) + 1
            self._failures[normalized_id] = count
            self._save_locked()
        return count

    def failure_count(self, video_id: str) -> int:
        """Return the current failure count for *video_id*."""
        return self._failures.get(str(video_id or "").strip(), 0)


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


def check_dependencies(
    javascript_runtimes: dict[str, dict[str, str]] | None = None,
) -> DependencyReport:
    """Check required tools and supported JavaScript runtime versions."""

    missing: list[str] = []

    if sys.version_info < (3, 10):
        missing.append("Python 3.10 or newer")
    if importlib.util.find_spec("yt_dlp") is None:
        missing.append('yt-dlp (run: python -m pip install -r requirements.txt)')
    if shutil.which("ffmpeg") is None:
        missing.append("FFmpeg executable")
    if shutil.which("ffprobe") is None:
        missing.append("FFprobe executable")

    runtimes = (
        detect_javascript_runtimes()
        if javascript_runtimes is None
        else javascript_runtimes
    )
    runtime = next(
        (_RUNTIME_DISPLAY_NAMES[key] for key in runtimes),
        None,
    )
    return DependencyReport(tuple(missing), runtime)


def _find_executable(name: str) -> str | None:
    path = shutil.which(name)
    if path:
        return path

    executable_name = name + (sysconfig.get_config_var("EXE") or "")
    scripts_candidate = Path(sysconfig.get_path("scripts")) / executable_name
    if scripts_candidate.is_file():
        return str(scripts_candidate)
    return None


def _version_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", value))


def detect_javascript_runtimes() -> dict[str, dict[str, str]]:
    """Return yt-dlp settings for installed, supported runtime versions."""

    runtimes: dict[str, dict[str, str]] = {}
    creation_flags = (
        subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    )

    for (
        key,
        _display_name,
        executable,
        version_args,
        version_pattern,
        minimum_version,
    ) in _JAVASCRIPT_RUNTIMES:
        path = _find_executable(executable)
        if not path:
            continue

        try:
            result = subprocess.run(
                [path, *version_args],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
                creationflags=creation_flags,
            )
        except (OSError, subprocess.SubprocessError):
            continue

        output = f"{result.stdout or ''}\n{result.stderr or ''}"
        match = version_pattern.search(output)
        if not match:
            continue

        version = _version_tuple(match.group(1))
        quickjs_ng = key == "quickjs" and "QuickJS-ng" in output
        if version >= minimum_version or (quickjs_ng and version > (0,)):
            runtimes[key] = {"path": path}
    return runtimes


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


def format_bytes(value: int | float | None) -> str:
    """Format a byte count for display."""

    if value is None:
        return ""

    size = float(value)
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    for unit in units:
        if abs(size) < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{size:.0f} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return ""


def _format_eta(value: Any) -> str:
    try:
        seconds = max(0, int(value))
    except (TypeError, ValueError):
        return ""

    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _progress_event(data: dict[str, Any]) -> DownloadEvent:
    info = data.get("info_dict") or {}
    title = str(info.get("title") or "video")
    downloaded = data.get("downloaded_bytes")
    total = data.get("total_bytes") or data.get("total_bytes_estimate")
    speed = data.get("speed")
    eta = _format_eta(data.get("eta"))

    percent: float | None = None
    if downloaded is not None and total:
        percent = max(0.0, min(100.0, float(downloaded) / float(total) * 100.0))

    details: list[str] = []
    if percent is not None:
        details.append(f"{percent:.1f}%")
    if speed:
        details.append(f"{format_bytes(speed)}/s")
    if eta:
        details.append(f"ETA {eta}")

    suffix = f" — {' • '.join(details)}" if details else ""
    return DownloadEvent("progress", f'Downloading "{title}"{suffix}', percent)


def _raise_for_missing_dependencies(report: DependencyReport) -> None:
    if not report.missing_required:
        return

    items = "\n".join(f"• {item}" for item in report.missing_required)
    raise MissingDependencyError(f"Missing required software:\n{items}")


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
