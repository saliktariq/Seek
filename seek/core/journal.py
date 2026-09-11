import json
import logging
import os
import shutil
import threading
from pathlib import Path
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from seek.core.engine import DownloadEvent

EventCallback = Callable[["DownloadEvent"], None]

import re

_REQUIRED_OUTPUT_FILENAMES = ("audio.mp3", "thumbnail.jpg", "info.txt")
_COMPLETION_INDEX_FILENAME = ".youtube-audio-completed.json"
_COMPLETION_INDEX_VERSION = 2
_VIDEO_FOLDER_ID = re.compile(r"\[([A-Za-z0-9_-]+)\]$")
_VIDEO_STAGE_RANK = {
    "downloaded": 1,
    "converted": 2,
    "complete": 3,
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
            from seek.core.engine import DownloadEvent

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
                "The completion index had an unsupported format; " "rebuilding it.",
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
                    folder = entry.get("folder") if isinstance(entry, dict) else None
                    stage = (
                        "complete"
                        if version == 1
                        else entry.get("phase") if isinstance(entry, dict) else None
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
                            entry.get("failures", 0) if isinstance(entry, dict) else 0
                        )
                        if isinstance(failures, int) and failures > 0:
                            self._failures[video_id] = failures
                    else:
                        needs_upgrade = True
            else:
                index_was_invalid = True
                self._emit(
                    "warning",
                    "The completion index had an unsupported format; " "rebuilding it.",
                )

        loaded_records = self._records_locked()
        for video_id, relative_folder in list(self._candidate_folders.items()):
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
            relative_folder = self._relative_folder(candidate)  # type: ignore
            if match is None or relative_folder is None or not candidate.is_dir():
                continue
            video_id = match.group(1)
            inferred_stage = _infer_video_stage(candidate)
            existing_stage = self._stages.get(video_id)
            if existing_stage is None or (
                inferred_stage is not None
                and _VIDEO_STAGE_RANK[inferred_stage]
                > _VIDEO_STAGE_RANK[existing_stage]
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
                f"Resume check: found {' and '.join(parts)} " "in this destination.",
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
                if not (name.startswith("audio.") or name.startswith("thumbnail.")):
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
            or _VIDEO_STAGE_RANK[inferred_stage] < _VIDEO_STAGE_RANK[minimum_stage]
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

        if media_path.name.lower().endswith(
            (".part", ".tmp", ".ytdl")
        ) or not _is_nonempty_file(media_path):
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

    def clear_failures(self) -> None:
        """Clear all recorded conversion failures to allow retrying."""
        with self._lock:
            if not self._failures:
                return
            self._failures.clear()
            self._save_locked()
