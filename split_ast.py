import ast
import os

def extract_nodes(source_path, target_names, out_path, prefix=""):
    with open(source_path, "r") as f:
        source = f.read()

    tree = ast.parse(source)
    out_code = prefix + "\n"

    for node in tree.body:
        name = None
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            name = node.name
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    name = target.id
                    break
        
        if name in target_names:
            out_code += ast.get_source_segment(source, node) + "\n\n"

    with open(out_path, "w") as f:
        f.write(out_code)

if __name__ == "__main__":
    # app.py splits
    extract_nodes(
        "app.py", 
        ["AppState"], 
        "seek/models/state.py", 
        "import enum\n"
    )
    extract_nodes(
        "app.py", 
        ["APP_TITLE", "COLORS", "FONTS", "PADDINGS"], 
        "seek/ui/theme.py", 
        ""
    )
    extract_nodes(
        "app.py", 
        ["AudioVisualizer", "StatCard", "ScrollableFrame"], 
        "seek/ui/widgets.py", 
        "import tkinter as tk\nimport math\nfrom seek.ui.theme import COLORS, FONTS\n"
    )
    extract_nodes(
        "app.py", 
        ["SpotifyDialog"], 
        "seek/ui/dialogs.py", 
        "import tkinter as tk\nfrom tkinter import ttk, messagebox\nimport webbrowser\nfrom seek.ui.theme import COLORS, FONTS, PADDINGS\nimport seek.core.spotify as spotify\n"
    )
    extract_nodes(
        "app.py", 
        ["YouTubeAudioApp"], 
        "seek/ui/app_window.py", 
        """from __future__ import annotations
import logging
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext
from pathlib import Path
import sys
import subprocess

from seek.models.state import AppState
from seek.ui.theme import APP_TITLE, COLORS, FONTS, PADDINGS
from seek.ui.widgets import AudioVisualizer, StatCard, ScrollableFrame
from seek.ui.dialogs import SpotifyDialog
from seek.core.engine import DownloadEvent, download_urls, UserCancelledError, DownloadFailedError
from seek.utils.system import check_dependencies, format_bytes
from seek.models.links import parse_url_entries, is_youtube_url, normalize_youtube_url
import seek.core.spotify as spotify
"""
    )
    
    # downloader.py splits
    extract_nodes(
        "downloader.py",
        ["is_youtube_url", "is_single_video_url", "normalize_youtube_url", "parse_url_entries", "parse_url_list"],
        "seek/models/links.py",
        "from urllib.parse import parse_qs, urlparse\nimport re\n"
    )
    extract_nodes(
        "downloader.py",
        ["DependencyReport", "_JAVASCRIPT_RUNTIMES", "_RUNTIME_DISPLAY_NAMES", "check_dependencies", "_find_executable", "detect_javascript_runtimes", "format_bytes"],
        "seek/utils/system.py",
        "from typing import NamedTuple\nimport importlib.util\nimport re\nimport shutil\nimport subprocess\nimport sys\nimport sysconfig\nfrom pathlib import Path\n"
    )
    extract_nodes(
        "downloader.py",
        ["_REQUIRED_OUTPUT_FILENAMES", "_is_nonempty_file", "_is_complete_video_folder", "_infer_video_stage", "CompletionIndex"],
        "seek/core/journal.py",
        "import json\nimport logging\nimport os\nimport shutil\nimport threading\nfrom pathlib import Path\n"
    )
    extract_nodes(
        "downloader.py",
        ["DownloadEvent", "DownloadFailedError", "UserCancelledError", "MissingDependencyError", "_DURATION_TOLERANCE_MIN_S", "_DURATION_TOLERANCE_MAX_S", "_DURATION_TOLERANCE_RATIO", "_MAX_CONVERSION_FAILURES", "_duration_tolerance", "_select_best_match", "build_ydl_options", "match_filter", "WriteInfoPostProcessor", "write_info_file", "download_urls", "download_url", "expand_input_urls", "search_youtube_for_track"],
        "seek/core/engine.py",
        """import os
import shutil
import subprocess
import tempfile
import sys
import logging
from typing import Callable, Iterable
from pathlib import Path
import yt_dlp
import threading
import json
import re

import seek.core.spotify as spotify
from seek.core.journal import CompletionIndex
from seek.models.links import is_youtube_url, normalize_youtube_url, is_single_video_url
from seek.utils.system import check_dependencies, detect_javascript_runtimes, DependencyReport
"""
    )
    print("Files split via AST.")
