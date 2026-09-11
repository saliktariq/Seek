import sys

with open("seek/ui/app_window.py", "r") as f:
    code = f.read()

# Imports
if "from plyer import notification" not in code:
    code = code.replace("import webbrowser", "import webbrowser\nimport pystray\nfrom pystray import MenuItem as item\nfrom plyer import notification\nfrom PIL import Image\nfrom seek.models.config import DownloadConfig")

if "self.config = DownloadConfig()" not in code:
    code = code.replace("self.events: queue.Queue[DownloadEvent] = queue.Queue()", "self.events: queue.Queue[DownloadEvent] = queue.Queue()\n        self.config = DownloadConfig()")

if "_open_general_settings" not in code:
    # Add to file menu
    code = code.replace(
        "label=\"Spotify settings…\",",
        "label=\"General settings…\",\n            command=self._open_general_settings,\n        )\n        self.file_menu.add_command(\n            label=\"Spotify settings…\","
    )
    
    settings_code = """
    def _open_general_settings(self) -> None:
        if self.running:
            return
            
        dialog = tk.Toplevel(self.root)
        dialog.title("Settings")
        dialog.geometry("300x150")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()
        
        main_frame = tk.Frame(dialog, padx=20, pady=20, background=COLORS["surface"])
        main_frame.pack(fill="both", expand=True)
        
        tk.Label(main_frame, text="Audio Format:", background=COLORS["surface"], foreground=COLORS["text"]).pack(anchor="w")
        
        format_var = tk.StringVar(value=self.config.audio_format)
        formats = ["mp3", "m4a", "flac", "wav"]
        dropdown = ttk.Combobox(main_frame, textvariable=format_var, values=formats, state="readonly")
        dropdown.pack(fill="x", pady=(5, 15))
        
        def save():
            self.config = DownloadConfig(audio_format=format_var.get(), audio_quality="192")
            dialog.destroy()
            
        ttk.Button(main_frame, text="Save", command=save, style="Primary.TButton").pack(side="right")
        ttk.Button(main_frame, text="Cancel", command=dialog.destroy).pack(side="right", padx=10)

    def _open_spotify_settings"""
    
    code = code.replace("    def _open_spotify_settings", settings_code)

if "config=self.config," not in code:
    code = code.replace(
        "            cancel_event=self.cancel_event,",
        "            cancel_event=self.cancel_event,\n            config=self.config,"
    )

with open("seek/ui/app_window.py", "w") as f:
    f.write(code)

