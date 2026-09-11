import sys

# 1. engine.py
with open("seek/core/engine.py", "r") as f:
    engine_code = f.read()
if "import time" not in engine_code:
    engine_code = engine_code.replace("import threading", "import threading\nimport time")
    with open("seek/core/engine.py", "w") as f:
        f.write(engine_code)

# 2. journal.py
with open("seek/core/journal.py", "r") as f:
    journal_code = f.read()
if "DownloadEvent" not in journal_code and "TYPE_CHECKING" not in journal_code:
    pass # Wait, let's just do it directly.
if "DownloadEvent" not in journal_code or True:
    if "from seek.ui.app_window import DownloadEvent" not in journal_code:
        # Actually DownloadEvent is defined in engine.py! No, wait... it's in engine.py.
        pass

# 3. app_window.py
with open("seek/ui/app_window.py", "r") as f:
    app_code = f.read()

app_code = app_code.replace("from seek.models.config import DownloadConfig", "")
app_code = app_code.replace("from plyer import notification", "")
app_code = app_code.replace("from tkinterdnd2 import DND_FILES, TkinterDnD", "")
app_code = app_code.replace("import darkdetect", "")

import_block = """import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from seek.models.config import DownloadConfig
from plyer import notification
from tkinterdnd2 import DND_FILES, TkinterDnD
import darkdetect
"""
app_code = app_code.replace("import tkinter as tk\nfrom tkinter import filedialog, messagebox, ttk\n", import_block)

with open("seek/ui/app_window.py", "w") as f:
    f.write(app_code)

