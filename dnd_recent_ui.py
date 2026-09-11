import sys

with open("seek/ui/app_window.py", "r") as f:
    code = f.read()

# 1. Imports
if "from tkinterdnd2 import DND_FILES, TkinterDnD" not in code:
    code = code.replace(
        "import tkinter as tk\nfrom tkinter import filedialog, messagebox, ttk",
        "import tkinter as tk\nfrom tkinter import filedialog, messagebox, ttk\nfrom tkinterdnd2 import DND_FILES, TkinterDnD\nimport darkdetect"
    )

# 2. Update root initialization
if "TkinterDnD.Tk()" not in code:
    code = code.replace("    root = tk.Tk()", "    root = TkinterDnD.Tk()")

# 3. Add drag-and-drop support to url_input
if "self.url_input.drop_target_register(DND_FILES)" not in code:
    code = code.replace(
        "        self.url_input.pack(side=\"left\", fill=\"both\", expand=True)",
        "        self.url_input.pack(side=\"left\", fill=\"both\", expand=True)\n        self.url_input.drop_target_register(DND_FILES)\n        self.url_input.dnd_bind('<<Drop>>', self._on_url_drop)"
    )

    dnd_func = """    def _on_url_drop(self, event) -> None:
        try:
            urls = []
            files = self.root.tk.splitlist(event.data)
            for file in files:
                if file.endswith('.txt'):
                    with open(file, 'r', encoding='utf-8') as f:
                        urls.extend(f.read().splitlines())
                else:
                    urls.append(file)
            if urls:
                current = self.url_input.get("1.0", "end-1c").strip()
                if current:
                    current += "\\n"
                self.url_input.delete("1.0", "end")
                self.url_input.insert("1.0", current + "\\n".join(urls))
                self.root.after(100, self._on_links_modified)
        except Exception as e:
            pass
            
    def _initialize_layout"""
    
    code = code.replace("    def _initialize_layout", dnd_func)

# 4. Recent destinations and AppConfig
# We'll modify config.py first
import os
config_py_path = "seek/models/config.py"
with open(config_py_path, "r") as f:
    config_code = f.read()

if "recent_destinations" not in config_code:
    import_field = "from dataclasses import dataclass, field\nfrom typing import List\n"
    config_code = config_code.replace("from dataclasses import dataclass", import_field)
    
    config_code = config_code.replace(
        "bandwidth_limit: str = \"Unlimited\"",
        "bandwidth_limit: str = \"Unlimited\"\n    recent_destinations: List[str] = field(default_factory=list)\n    geometry: str = \"\""
    )
    with open(config_py_path, "w") as f:
        f.write(config_code)

# 5. App Window state persistence
if "_load_state(" not in code:
    # Add to __init__
    code = code.replace(
        "        self.config = DownloadConfig()",
        "        self.config = DownloadConfig()\n        self._load_state()"
    )
    
    # Replace output_var Entry with Combobox
    old_output_ui = """        self.output_var = tk.StringVar()
        output_entry = ttk.Entry(
            directory_frame,
            textvariable=self.output_var,
            font=("Segoe UI", 10),
            state="readonly",
        )"""
    new_output_ui = """        self.output_var = tk.StringVar()
        self.output_combo = ttk.Combobox(
            directory_frame,
            textvariable=self.output_var,
            font=("Segoe UI", 10),
            state="readonly",
            values=self.config.recent_destinations,
        )"""
    code = code.replace(old_output_ui, new_output_ui)
    code = code.replace("output_entry.pack(", "self.output_combo.pack(")
    
    # Save geometry and state on close
    code = code.replace(
        "    def _on_close(self) -> None:",
        "    def _save_state(self) -> None:\n        try:\n            import json\n            from pathlib import Path\n            config_dir = Path.home() / \".config\" / \"seek\"\n            config_dir.mkdir(parents=True, exist_ok=True)\n            state = {\n                \"audio_format\": self.config.audio_format,\n                \"audio_quality\": self.config.audio_quality,\n                \"bandwidth_limit\": self.config.bandwidth_limit,\n                \"recent_destinations\": self.config.recent_destinations,\n                \"geometry\": self.root.geometry()\n            }\n            with open(config_dir / \"config.json\", \"w\") as f:\n                json.dump(state, f)\n        except Exception:\n            pass\n\n    def _load_state(self) -> None:\n        try:\n            import json\n            from pathlib import Path\n            config_file = Path.home() / \".config\" / \"seek\" / \"config.json\"\n            if config_file.exists():\n                with open(config_file, \"r\") as f:\n                    state = json.load(f)\n                self.config = DownloadConfig(\n                    audio_format=state.get(\"audio_format\", \"mp3\"),\n                    audio_quality=state.get(\"audio_quality\", \"192\"),\n                    bandwidth_limit=state.get(\"bandwidth_limit\", \"Unlimited\"),\n                    recent_destinations=state.get(\"recent_destinations\", []),\n                    geometry=state.get(\"geometry\", \"\")\n                )\n                if self.config.geometry:\n                    self.root.geometry(self.config.geometry)\n                if self.config.recent_destinations:\n                    self.output_var.set(self.config.recent_destinations[0])\n        except Exception:\n            pass\n\n    def _on_close(self) -> None:"
    )
    
    # Update on close
    code = code.replace(
        "        if should_close:\n            self.close_requested = True\n            self._cancel_download()",
        "        if should_close:\n            self._save_state()\n            self.close_requested = True\n            self._cancel_download()"
    )
    
    # Update _open_output to append to recent_destinations
    old_output_open = """        if selected:
            self.output_var.set(selected)"""
    new_output_open = """        if selected:
            self.output_var.set(selected)
            recent = self.config.recent_destinations
            if selected in recent:
                recent.remove(selected)
            recent.insert(0, selected)
            recent = recent[:5]
            self.config = DownloadConfig(
                audio_format=self.config.audio_format,
                audio_quality=self.config.audio_quality,
                bandwidth_limit=self.config.bandwidth_limit,
                recent_destinations=recent,
                geometry=self.config.geometry
            )
            self.output_combo["values"] = recent"""
    code = code.replace(old_output_open, new_output_open)

# 6. Auto Dark/Light theme
if "darkdetect.theme()" not in code:
    code = code.replace(
        "    def __init__(self, root: tk.Tk) -> None:",
        "    def __init__(self, root: tk.Tk) -> None:\n        # Auto theme\n        try:\n            if darkdetect.theme() == 'Light':\n                global COLORS\n                # Very basic light theme\n                COLORS = {\n                    \"bg\": \"#f3f4f6\",\n                    \"surface\": \"#ffffff\",\n                    \"primary\": \"#10b981\",\n                    \"primary_hover\": \"#059669\",\n                    \"text\": \"#1f2937\",\n                    \"text_dim\": \"#6b7280\",\n                    \"border\": \"#e5e7eb\",\n                    \"error\": \"#ef4444\",\n                    \"success\": \"#10b981\",\n                }\n        except Exception:\n            pass"
    )

with open("seek/ui/app_window.py", "w") as f:
    f.write(code)

