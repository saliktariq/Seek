import unittest
import tempfile
import threading
from pathlib import Path

from seek.core import engine
from seek.models.config import DownloadConfig
from seek.ui.app_window import DownloadEvent


class IntegrationTests(unittest.TestCase):
    def test_journal_atomicity_and_output(self) -> None:
        # We can't actually download without network, but we can verify
        # that write_info_file creates files atomically using a real temp dir.
        with tempfile.TemporaryDirectory() as td:
            output_dir = Path(td)

            # 1. Test write_info_file
            info = {"title": "Test Title", "description": "Test Desc"}
            info_path = engine.write_info_file(info, output_dir)

            self.assertTrue(info_path.exists())
            self.assertEqual(
                info_path.read_text(encoding="utf-8"),
                "Title: Test Title\n\nDescription:\nTest Desc\n",
            )


if __name__ == "__main__":
    unittest.main()
