import re

with open("downloader.py", "r") as f:
    dl_code = f.read()


def extract_block(code, regex_pattern):
    match = re.search(regex_pattern, code, re.MULTILINE | re.DOTALL)
    return match.group(1) if match else ""


engine_code = """import os
import shutil
import subprocess
import tempfile
import sys
import logging
from typing import Callable
from pathlib import Path
import yt_dlp
import threading

import seek.core.spotify as spotify
from seek.core.journal import CompletionIndex
from seek.models.links import is_youtube_url
from seek.utils.system import check_dependencies, detect_javascript_runtimes, DependencyReport

"""
engine_code += (
    extract_block(dl_code, r"^(class DownloadEvent.*?)(?=\n_DURATION_TOLERANCE_MIN_S)")
    + "\n"
)
engine_code += (
    extract_block(
        dl_code, r"^(_DURATION_TOLERANCE_MIN_S =.*?)(?=\ndef build_ydl_options)"
    )
    + "\n"
)
engine_code += extract_block(
    dl_code, r"^(def build_ydl_options.*?\n)(?=if __name__ == )"
)

with open("seek/core/engine.py", "w") as f:
    f.write(engine_code)

print("downloader split part 2 complete.")
