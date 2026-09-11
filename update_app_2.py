import sys

with open("seek/ui/app_window.py", "r") as f:
    code = f.read()

# Add unmap binding and tray functions
if "self.root.bind(\"<Unmap>\"," not in code:
    code = code.replace(
        "        self._initialize_layout()",
        "        self._initialize_layout()\n        self.root.bind(\"<Unmap>\", self._on_unmap)"
    )

    tray_code = """
    def _on_unmap(self, event: tk.Event) -> None:
        if event.widget == self.root and self.root.state() == "iconic":
            self.root.withdraw()
            self._show_tray_icon()

    def _show_tray_icon(self) -> None:
        if getattr(self, "tray_icon", None) is not None:
            return
            
        # Create a simple icon image
        image = Image.new('RGB', (64, 64), color=(30, 30, 30))
        
        menu = pystray.Menu(
            item('Restore', self._restore_from_tray, default=True),
            item('Quit', self._quit_from_tray)
        )
        self.tray_icon = pystray.Icon("seek_audio", image, "Seek Audio Saver", menu)
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def _restore_from_tray(self, icon, item) -> None:
        self.tray_icon.stop()
        self.tray_icon = None
        self.root.after(0, self.root.deiconify)

    def _quit_from_tray(self, icon, item) -> None:
        self.tray_icon.stop()
        self.tray_icon = None
        self.root.after(0, self._on_close)
"""
    code = code.replace("    def _initialize_layout", tray_code + "\n    def _initialize_layout")

if "notification.notify" not in code:
    # Update _finish_job to send a notification when complete
    code = code.replace(
        "        self.status_var.set(message)",
        "        self.status_var.set(message)\n        if success:\n            try:\n                notification.notify(title=\"Download Complete\", message=message, app_name=\"Seek\")\n            except Exception:\n                pass"
    )

with open("seek/ui/app_window.py", "w") as f:
    f.write(code)

