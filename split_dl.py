import re
import sys

with open("downloader.py", "r") as f:
    dl_code = f.read()

def extract_block(code, regex_pattern):
    match = re.search(regex_pattern, code, re.MULTILINE | re.DOTALL)
    return match.group(1) if match else ""

links_code = """from urllib.parse import parse_qs, urlparse

"""
links_code += extract_block(dl_code, r"^(def is_youtube_url.*?)(?=\ndef is_single_video_url)") + "\n"
links_code += extract_block(dl_code, r"^(def is_single_video_url.*?)(?=\ndef normalize_youtube_url)") + "\n"
links_code += extract_block(dl_code, r"^(def normalize_youtube_url.*?)(?=\ndef parse_url_entries)") + "\n"
links_code += extract_block(dl_code, r"^(def parse_url_entries.*?)(?=\ndef parse_url_list)") + "\n"
links_code += extract_block(dl_code, r"^(def parse_url_list.*?\n\n)")

with open("seek/models/links.py", "w") as f:
    f.write(links_code)

system_code = """from typing import NamedTuple
import importlib.util
import re
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

class DependencyReport(NamedTuple):
    missing: tuple[str, ...]
    javascript_runtime: str | None

"""
system_code += extract_block(dl_code, r"^(_JAVASCRIPT_RUNTIMES =.*?_RUNTIME_DISPLAY_NAMES =.*?})\n\n") + "\n"
system_code += extract_block(dl_code, r"^(def check_dependencies.*?)(?=\ndef _find_executable)") + "\n"
system_code += extract_block(dl_code, r"^(def _find_executable.*?)(?=\ndef detect_javascript_runtimes)") + "\n"
system_code += extract_block(dl_code, r"^(def detect_javascript_runtimes.*?)(?=\nclass DownloadEvent)") + "\n"
system_code += extract_block(dl_code, r"^(def format_bytes.*?\n\n)")

with open("seek/utils/system.py", "w") as f:
    f.write(system_code)

journal_code = """import json
import logging
import os
import shutil
import threading
from pathlib import Path

_REQUIRED_OUTPUT_FILENAMES = ("audio.mp3", "info.txt", "thumbnail.jpg")

"""
journal_code += extract_block(dl_code, r"^(def _is_nonempty_file.*?)(?=\ndef _is_complete_video_folder)") + "\n"
journal_code += extract_block(dl_code, r"^(def _is_complete_video_folder.*?)(?=\ndef _infer_video_stage)") + "\n"
journal_code += extract_block(dl_code, r"^(def _infer_video_stage.*?)(?=\nclass CompletionIndex)") + "\n"
journal_code += extract_block(dl_code, r"^(class CompletionIndex.*?(?=\ndef is_youtube_url))")

with open("seek/core/journal.py", "w") as f:
    f.write(journal_code)

print("downloader split part 1 complete.")
