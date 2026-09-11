from typing import NamedTuple
import importlib.util
import re
import shutil
import subprocess
import os
import sys
import sysconfig
from pathlib import Path

from dataclasses import dataclass


@dataclass(frozen=True)
class DependencyReport:
    """Result of checking the local runtime before a download."""

    missing_required: tuple[str, ...]
    javascript_runtime: str | None


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
    key: display_name for key, display_name, *_remaining in _JAVASCRIPT_RUNTIMES
}


def check_dependencies(
    javascript_runtimes: dict[str, dict[str, str]] | None = None,
) -> DependencyReport:
    """Check required tools and supported JavaScript runtime versions."""

    missing: list[str] = []

    if sys.version_info < (3, 10):
        missing.append("Python 3.10 or newer")
    if importlib.util.find_spec("yt_dlp") is None:
        missing.append("yt-dlp (run: python -m pip install -r requirements.txt)")
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
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0  # type: ignore

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
