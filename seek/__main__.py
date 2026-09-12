import tkinter as tk
import sys
import logging
from seek.ui.app_window import YouTubeAudioApp, COLORS


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    try:
        from tkinterdnd2 import TkinterDnD  # type: ignore

        root = TkinterDnD.Tk()
    except Exception:
        root = tk.Tk()
    root.title("SEEK")
    root.geometry("900x700")
    root.minsize(800, 600)
    root.configure(background=COLORS["app_bg"])

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
