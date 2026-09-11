import sys

# 1. Update config.py
with open("seek/models/config.py", "r") as f:
    code = f.read()
if "bandwidth_limit" not in code:
    code = code.replace(
        "    audio_quality: str = \"192\"",
        "    audio_quality: str = \"192\"\n    bandwidth_limit: str = \"Unlimited\""
    )
with open("seek/models/config.py", "w") as f:
    f.write(code)

# 2. Update engine.py
with open("seek/core/engine.py", "r") as f:
    code = f.read()
if "ratelimit" not in code:
    limit_code = """        "postprocessors": [
            {
                "key": "FFmpegThumbnailsConvertor",
                "format": "jpg",
                "when": "before_dl",
            },
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": config.audio_format,
                "preferredquality": config.audio_quality,
            },
        ],
    }

    if config.bandwidth_limit != "Unlimited":
        try:
            val = float(config.bandwidth_limit.split()[0])
            options["ratelimit"] = val * 1024 * 1024
        except Exception:
            pass"""
    code = code.replace(
        "        \"postprocessors\": [\n            {\n                \"key\": \"FFmpegThumbnailsConvertor\",\n                \"format\": \"jpg\",\n                \"when\": \"before_dl\",\n            },\n            {\n                \"key\": \"FFmpegExtractAudio\",\n                \"preferredcodec\": config.audio_format,\n                \"preferredquality\": config.audio_quality,\n            },\n        ],\n    }",
        limit_code
    )
with open("seek/core/engine.py", "w") as f:
    f.write(code)

# 3. Update app_window.py
with open("seek/ui/app_window.py", "r") as f:
    code = f.read()
if "Bandwidth Limit:" not in code:
    old_dialog = """        format_var = tk.StringVar(value=self.config.audio_format)
        formats = ["mp3", "m4a", "flac", "wav"]
        dropdown = ttk.Combobox(main_frame, textvariable=format_var, values=formats, state="readonly")
        dropdown.pack(fill="x", pady=(5, 15))
        
        def save():
            self.config = DownloadConfig(audio_format=format_var.get(), audio_quality="192")"""
            
    new_dialog = """        format_var = tk.StringVar(value=self.config.audio_format)
        formats = ["mp3", "m4a", "flac", "wav"]
        dropdown = ttk.Combobox(main_frame, textvariable=format_var, values=formats, state="readonly")
        dropdown.pack(fill="x", pady=(5, 15))
        
        tk.Label(main_frame, text="Bandwidth Limit:", background=COLORS["surface"], foreground=COLORS["text"]).pack(anchor="w")
        bw_var = tk.StringVar(value=self.config.bandwidth_limit)
        bw_formats = ["Unlimited", "1 MB/s", "5 MB/s", "10 MB/s", "25 MB/s"]
        bw_dropdown = ttk.Combobox(main_frame, textvariable=bw_var, values=bw_formats, state="readonly")
        bw_dropdown.pack(fill="x", pady=(5, 15))
        
        def save():
            self.config = DownloadConfig(
                audio_format=format_var.get(),
                audio_quality="192",
                bandwidth_limit=bw_var.get()
            )"""
            
    code = code.replace(old_dialog, new_dialog)
    # increase window size slightly
    code = code.replace("dialog.geometry(\"300x150\")", "dialog.geometry(\"300x200\")")

with open("seek/ui/app_window.py", "w") as f:
    f.write(code)

