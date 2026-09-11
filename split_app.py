import re
import sys

with open("app.py", "r") as f:
    app_code = f.read()

def extract_class(code, class_name):
    pattern = rf"^(class {class_name}\b.*?)(?=\nclass |\nif __name__ == |\Z)"
    match = re.search(pattern, code, re.MULTILINE | re.DOTALL)
    return match.group(1) if match else ""

imports = """from __future__ import annotations
import enum
import logging
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext
from pathlib import Path
import math
import subprocess
import sys

from seek.models.state import AppState
from seek.ui.theme import APP_TITLE, COLORS, FONTS, PADDINGS
from seek.ui.widgets import AudioVisualizer, StatCard, ScrollableFrame
from seek.ui.dialogs import SpotifyDialog
from seek.core.engine import DownloadEvent, download_urls, UserCancelledError, DownloadFailedError
from seek.utils.system import check_dependencies
from seek.models.links import parse_url_entries, is_youtube_url, normalize_youtube_url
import seek.core.spotify as spotify
"""

state_code = """import enum

class AppState(enum.Enum):
    IDLE = enum.auto()
    PARSING = enum.auto()
    DOWNLOADING = enum.auto()
    CANCELLING = enum.auto()
"""

theme_code = """APP_TITLE = "SEEK"
COLORS = {
    "bg": "#121212",
    "surface": "#1E1E1E",
    "surface_light": "#2A2A2A",
    "primary": "#1DB954",
    "primary_hover": "#1ED760",
    "text": "#FFFFFF",
    "text_muted": "#B3B3B3",
    "border": "#333333",
    "error": "#E22134",
}
FONTS = {
    "heading": ("-weight bold", 24),
    "subheading": ("-weight bold", 14),
    "body": ("", 11),
    "body_bold": ("-weight bold", 11),
    "mono": ("Consolas", 10),
    "small": ("", 10),
}
PADDINGS = {
    "sm": 8,
    "md": 16,
    "lg": 24,
    "xl": 32,
}
"""

with open("seek/models/state.py", "w") as f:
    f.write(state_code)

with open("seek/ui/theme.py", "w") as f:
    f.write(theme_code)

widgets_code = "import tkinter as tk\nimport math\nfrom seek.ui.theme import COLORS, FONTS\n\n"
widgets_code += extract_class(app_code, "AudioVisualizer") + "\n"
widgets_code += extract_class(app_code, "StatCard") + "\n"
widgets_code += extract_class(app_code, "ScrollableFrame") + "\n"
with open("seek/ui/widgets.py", "w") as f:
    f.write(widgets_code)

dialogs_code = "import tkinter as tk\nfrom tkinter import ttk\nfrom seek.ui.theme import COLORS, FONTS, PADDINGS\nimport seek.core.spotify as spotify\n\n"
dialogs_code += extract_class(app_code, "SpotifyDialog") + "\n"
with open("seek/ui/dialogs.py", "w") as f:
    f.write(dialogs_code)

app_window_code = imports + "\n" + extract_class(app_code, "YouTubeAudioApp") + "\n"
with open("seek/ui/app_window.py", "w") as f:
    f.write(app_window_code)

main_code = """import tkinter as tk
import sys
import logging
from seek.ui.app_window import YouTubeAudioApp
from seek.ui.theme import COLORS

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    root = tk.Tk()
    root.title("SEEK")
    root.geometry("900x700")
    root.minsize(800, 600)
    root.configure(background=COLORS["bg"])
    
    app = YouTubeAudioApp(root)
    
    def on_closing() -> None:
        if app.running:
            app._cancel_download()
            root.after(100, on_closing)
        else:
            root.destroy()
            sys.exit(0)
            
    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()

if __name__ == "__main__":
    main()
"""
with open("seek/__main__.py", "w") as f:
    f.write(main_code)

print("app.py split complete.")
