from __future__ import annotations

from pathlib import Path
import queue
import threading
import unittest
from unittest import mock

from seek.ui import app_window as app
from seek.core.engine import DownloadResult


class FakeVar:
    def __init__(self, value: object = "") -> None:
        self.value = value

    def get(self) -> object:
        return self.value

    def set(self, value: object) -> None:
        self.value = value


class FakeText:
    def __init__(self, value: str) -> None:
        self.value = value
        self.modified = True

    def edit_modified(self, value: bool | None = None) -> bool:
        if value is not None:
            self.modified = value
        return self.modified

    def get(self, _start: str, _end: str) -> str:
        return self.value


class WorkerSummaryTests(unittest.TestCase):
    def _worker_without_tk(self) -> app.YouTubeAudioApp:
        worker = object.__new__(app.YouTubeAudioApp)
        worker.events = queue.Queue()
        worker.cancel_event = threading.Event()
        return worker

    def test_all_already_complete_is_reported_as_success(self) -> None:
        worker = self._worker_without_tk()
        with mock.patch.object(
            app,
            "download_urls",
            return_value=DownloadResult(0, False, 140),
        ):
            worker._download_worker(
                ["https://www.youtube.com/@example/videos"],
                Path("downloads"),
            )

        event = worker.events.get_nowait()
        self.assertEqual(event.kind, "job_complete")
        self.assertIn("140 already-complete videos skipped", event.message)  # type: ignore

    def test_mixed_resume_summary_reports_new_and_skipped_counts(self) -> None:
        worker = self._worker_without_tk()
        with mock.patch.object(
            app,
            "download_urls",
            return_value=DownloadResult(160, False, 140),
        ):
            worker._download_worker(
                ["https://www.youtube.com/@example/videos"],
                Path("downloads"),
            )

        event = worker.events.get_nowait()
        self.assertEqual(event.kind, "job_complete")
        self.assertIn("160 new videos saved", event.message)  # type: ignore
        self.assertIn("140 already-complete videos skipped", event.message)  # type: ignore


class UiStateTests(unittest.TestCase):
    def test_link_counter_counts_unique_nonblank_links(self) -> None:
        view = object.__new__(app.YouTubeAudioApp)
        view.url_input = FakeText(  # type: ignore
            "\n".join(
                [
                    "https://youtu.be/alpha",
                    "",
                    "https://youtu.be/beta",
                    "https://youtu.be/alpha",
                ]
            )
        )
        view.link_count_var = FakeVar()  # type: ignore
        view.link_badge_var = FakeVar()  # type: ignore

        view._on_links_modified()

        self.assertEqual(view.link_count_var.get(), "2")
        self.assertEqual(view.link_badge_var.get(), "2 links")
        self.assertFalse(view.url_input.modified)  # type: ignore

    def test_saved_and_skipped_counts_update_dashboard(self) -> None:
        view = object.__new__(app.YouTubeAudioApp)
        view.saved_count = 1
        view.skipped_count = 140
        view.count_var = FakeVar()  # type: ignore
        view.saved_stat_var = FakeVar()  # type: ignore
        view.skipped_stat_var = FakeVar()  # type: ignore

        view._update_count()

        self.assertEqual(
            view.count_var.get(),
            "1 video saved • 140 already complete",
        )
        self.assertEqual(view.saved_stat_var.get(), "1")
        self.assertEqual(view.skipped_stat_var.get(), "140")

    def test_visual_state_has_headless_safe_fallback(self) -> None:
        view = object.__new__(app.YouTubeAudioApp)
        view.visual_state_var = FakeVar()  # type: ignore

        view._set_visual_state("complete")

        self.assertEqual(view.visual_state, "complete")
        self.assertEqual(view.visual_state_var.get(), "COMPLETE")


if __name__ == "__main__":
    unittest.main()
