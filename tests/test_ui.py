import unittest
import os


class UITests(unittest.TestCase):
    def test_app_initialization(self) -> None:
        # GUI tests can be tricky in CI without Xvfb, but we can try importing and instantiating
        # if there is a display available.
        if "DISPLAY" in os.environ:
            try:
                import tkinter as tk
                from tkinterdnd2 import TkinterDnD  # type: ignore
                from seek.ui.app_window import YouTubeAudioApp

                root = TkinterDnD.Tk()
            except ImportError:
                self.skipTest("Missing TkinterDnD")
                return
            app = YouTubeAudioApp(root)
            root.update_idletasks()
            root.destroy()
            self.assertTrue(True)
        else:
            self.skipTest("No DISPLAY available for UI testing")


if __name__ == "__main__":
    unittest.main()
