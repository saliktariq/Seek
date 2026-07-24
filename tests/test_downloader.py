from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock

import downloader
from downloader import (
    DependencyReport,
    DownloadEvent,
    DownloadFailedError,
    build_ydl_options,
    download_url,
    download_urls,
    format_bytes,
    is_single_video_url,
    is_youtube_url,
    parse_url_entries,
    parse_url_list,
    write_info_file,
)


def _no_op(*_args, **_kwargs):
    return None


def _write_complete_output(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "audio.mp3").write_bytes(b"mp3")
    (folder / "thumbnail.jpg").write_bytes(b"jpg")
    (folder / "info.txt").write_text(
        "Title: Test\n\nDescription:\nTest\n",
        encoding="utf-8",
    )


class UrlValidationTests(unittest.TestCase):
    def test_parses_multiple_urls_and_removes_exact_duplicates(self) -> None:
        self.assertEqual(
            parse_url_list(
                "\nhttps://youtu.be/one\n"
                "  https://youtu.be/two  \n"
                "https://youtu.be/one\n\n"
            ),
            [
                "https://youtu.be/one",
                "https://youtu.be/two",
            ],
        )

    def test_parse_entries_preserves_line_numbers_and_strips_bom(self) -> None:
        self.assertEqual(
            parse_url_entries(
                "\ufeffhttps://youtu.be/one\r\n\r\n  "
                "https://youtu.be/two  \r\n"
            ),
            [
                (1, "https://youtu.be/one"),
                (3, "https://youtu.be/two"),
            ],
        )

    def test_accepts_common_youtube_urls(self) -> None:
        urls = (
            "https://www.youtube.com/watch?v=abc123",
            "https://youtu.be/abc123",
            "https://music.youtube.com/playlist?list=PL123",
            "https://www.youtube-nocookie.com/embed/abc123",
        )
        for url in urls:
            with self.subTest(url=url):
                self.assertTrue(is_youtube_url(url))

    def test_rejects_deceptive_or_non_http_urls(self) -> None:
        urls = (
            "",
            "not a url",
            "ftp://youtube.com/watch?v=abc123",
            "https://youtube.com.evil/watch?v=abc123",
            "https://youtube.com.evil.test/watch?v=abc123",
            "https://foo.youtube.evil/watch?v=abc123",
            "https://youtube.com@evil.test/watch?v=abc123",
            "https://example.com/youtube.com/watch?v=abc123",
        )
        for url in urls:
            with self.subTest(url=url):
                self.assertFalse(is_youtube_url(url))

    def test_video_url_does_not_expand_playlist_query(self) -> None:
        self.assertTrue(
            is_single_video_url(
                "https://www.youtube.com/watch?v=abc123&list=PL123"
            )
        )
        self.assertTrue(is_single_video_url("https://youtu.be/abc123"))
        self.assertTrue(
            is_single_video_url("https://www.youtube.com/shorts/abc123")
        )
        self.assertFalse(
            is_single_video_url(
                "https://www.youtube.com/playlist?list=PL123"
            )
        )
        self.assertFalse(
            is_single_video_url(
                "https://www.youtube.com/watch?list=PL123"
            )
        )
        self.assertTrue(
            is_single_video_url(
                "https://www.youtube.com/watch?v=abc123&list=PL123"
            )
        )
        self.assertFalse(
            is_single_video_url("https://www.youtube.com/@example/videos")
        )


class OutputTests(unittest.TestCase):
    def test_info_file_contains_unicode_title_and_multiline_description(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary) / "video"
            path = write_info_file(
                {
                    "title": "Café 🎵",
                    "description": "Line one\nLine two",
                },
                folder,
            )

            self.assertEqual(path.name, "info.txt")
            self.assertEqual(
                path.read_text(encoding="utf-8"),
                "Title: Café 🎵\n\nDescription:\nLine one\nLine two\n",
            )
            self.assertFalse((folder / ".info.txt.tmp").exists())

    def test_info_file_handles_missing_description(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = write_info_file(
                {"title": "Example", "description": None},
                Path(temporary),
            )
            self.assertIn(
                "(No description provided.)",
                path.read_text(encoding="utf-8"),
            )

    def test_ydl_options_have_the_required_output_contract(self) -> None:
        options = build_ydl_options(
            "https://www.youtube.com/playlist?list=PL123",
            Path("downloads"),
            logger=object(),
            progress_hook=_no_op,
            postprocessor_hook=_no_op,
            match_filter=_no_op,
            javascript_runtimes={"node": {"path": "node"}},
        )

        self.assertEqual(
            options["outtmpl"]["default"],
            "%(title).100S [%(id)s]/audio.%(ext)s",
        )
        self.assertEqual(
            options["outtmpl"]["thumbnail"],
            "%(title).100S [%(id)s]/thumbnail.%(ext)s",
        )
        self.assertFalse(options["noplaylist"])
        self.assertTrue(options["writethumbnail"])
        self.assertEqual(options["final_ext"], "mp3")
        self.assertTrue(options["continuedl"])
        self.assertFalse(options["overwrites"])
        self.assertEqual(options["js_runtimes"], {"node": {"path": "node"}})
        self.assertEqual(
            options["postprocessors"],
            [
                {
                    "key": "FFmpegThumbnailsConvertor",
                    "format": "jpg",
                    "when": "before_dl",
                },
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                },
            ],
        )

        direct_options = build_ydl_options(
            "https://www.youtube.com/watch?v=abc123",
            Path("downloads"),
            logger=object(),
            progress_hook=_no_op,
            postprocessor_hook=_no_op,
            match_filter=_no_op,
        )
        channel_options = build_ydl_options(
            "https://www.youtube.com/@example/videos",
            Path("downloads"),
            logger=object(),
            progress_hook=_no_op,
            postprocessor_hook=_no_op,
            match_filter=_no_op,
        )
        self.assertTrue(direct_options["noplaylist"])
        self.assertFalse(channel_options["noplaylist"])

    def test_format_bytes(self) -> None:
        self.assertEqual(format_bytes(0), "0 B")
        self.assertEqual(format_bytes(1024), "1.0 KiB")
        self.assertEqual(format_bytes(1024 * 1024), "1.0 MiB")
        self.assertEqual(format_bytes(None), "")

    def test_download_flow_writes_info_in_the_video_folder(self) -> None:
        fake_yt_dlp = types.ModuleType("yt_dlp")
        fake_utils = types.ModuleType("yt_dlp.utils")

        class FakeDownloadCancelled(Exception):
            pass

        class FakeDownloadError(Exception):
            pass

        class FakePostProcessingError(Exception):
            pass

        class FakePostProcessor:
            pass

        fake_utils.DownloadCancelled = FakeDownloadCancelled
        fake_utils.DownloadError = FakeDownloadError
        fake_utils.PostProcessingError = FakePostProcessingError

        fake_postprocessor = types.SimpleNamespace(
            PostProcessor=FakePostProcessor
        )
        fake_yt_dlp.utils = fake_utils
        fake_yt_dlp.postprocessor = fake_postprocessor

        class FakeYoutubeDL:
            last_options = None
            write_thumbnail = True
            download_attempts = 0
            recorded_download_phases = []

            def __init__(self, options):
                type(self).last_options = options
                self.processor = None

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def add_post_processor(self, processor, *, when):
                self.processor = processor
                self.when = when

            def download(self, _urls):
                info = {
                    "id": "fake123",
                    "title": "Fake title",
                    "description": "Fake description",
                }
                skip_reason = type(self).last_options["match_filter"](
                    info,
                    incomplete=True,
                )
                if skip_reason:
                    return 0

                type(self).download_attempts += 1
                output_root = Path(
                    type(self).last_options["paths"]["home"]
                )
                video_dir = output_root / "Fake title [fake123]"
                video_dir.mkdir(parents=True)
                source_path = video_dir / "audio.webm"
                source_path.write_bytes(b"fake source")
                type(self).last_options["progress_hooks"][0](
                    {
                        "status": "finished",
                        "filename": str(source_path),
                        "info_dict": info,
                    }
                )
                state = json.loads(
                    (
                        output_root
                        / ".youtube-audio-completed.json"
                    ).read_text(encoding="utf-8")
                )
                type(self).recorded_download_phases.append(
                    state["videos"]["fake123"]["phase"]
                )
                source_path.unlink()
                (video_dir / "audio.mp3").write_bytes(b"fake mp3")
                if type(self).write_thumbnail:
                    (video_dir / "thumbnail.jpg").write_bytes(b"fake jpg")
                self.processor.run(
                    {
                        **info,
                        "filepath": str(video_dir / "audio.mp3"),
                    }
                )
                return 0

        fake_yt_dlp.YoutubeDL = FakeYoutubeDL

        with tempfile.TemporaryDirectory() as temporary:
            events = []
            with (
                mock.patch.dict(
                    sys.modules,
                    {
                        "yt_dlp": fake_yt_dlp,
                        "yt_dlp.utils": fake_utils,
                    },
                ),
                mock.patch.object(
                    downloader,
                    "check_dependencies",
                    return_value=DependencyReport((), "Node.js"),
                ),
                mock.patch.object(
                    downloader,
                    "detect_javascript_runtimes",
                    return_value={"node": {"path": "node"}},
                ),
            ):
                result = download_url(
                    "https://www.youtube.com/watch?v=fake123",
                    Path(temporary),
                    events.append,
                    threading.Event(),
                )

                repeat_events = []
                repeat_result = download_url(
                    "https://www.youtube.com/watch?v=fake123",
                    Path(temporary),
                    repeat_events.append,
                    threading.Event(),
                )

                FakeYoutubeDL.write_thumbnail = False
                partial_root = Path(temporary) / "partial"
                partial_events = []
                partial_result = download_url(
                    "https://www.youtube.com/watch?v=fake456",
                    partial_root,
                    partial_events.append,
                    threading.Event(),
                )

            video_dir = Path(temporary) / "Fake title [fake123]"
            self.assertEqual(result.completed_videos, 1)
            self.assertFalse(result.had_errors)
            self.assertEqual(
                sorted(path.name for path in video_dir.iterdir()),
                ["audio.mp3", "info.txt", "thumbnail.jpg"],
            )
            self.assertEqual(
                (video_dir / "info.txt").read_text(encoding="utf-8"),
                "Title: Fake title\n\nDescription:\nFake description\n",
            )
            self.assertEqual(FakeYoutubeDL.last_options["noplaylist"], True)
            self.assertTrue(
                any(event.kind == "video_complete" for event in events)
            )
            self.assertEqual(repeat_result.completed_videos, 0)
            self.assertEqual(repeat_result.skipped_videos, 1)
            self.assertFalse(repeat_result.had_errors)
            self.assertTrue(
                any(
                    event.kind == "video_skipped"
                    for event in repeat_events
                )
            )
            self.assertEqual(partial_result.completed_videos, 0)
            self.assertTrue(partial_result.had_errors)
            self.assertTrue(
                any(event.kind == "error" for event in partial_events)
            )
            self.assertEqual(FakeYoutubeDL.download_attempts, 2)
            self.assertEqual(
                FakeYoutubeDL.recorded_download_phases,
                ["downloaded", "downloaded"],
            )
            complete_state = json.loads(
                (
                    Path(temporary)
                    / ".youtube-audio-completed.json"
                ).read_text(encoding="utf-8")
            )
            partial_state = json.loads(
                (
                    partial_root
                    / ".youtube-audio-completed.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(complete_state["version"], 2)
            self.assertEqual(
                complete_state["videos"]["fake123"]["phase"],
                "complete",
            )
            self.assertEqual(
                partial_state["videos"]["fake123"]["phase"],
                "converted",
            )


class CompletionIndexTests(unittest.TestCase):
    def test_journal_records_downloaded_converted_and_complete_phases(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output_root = Path(temporary)
            video_dir = output_root / "Phase test [phase123]"
            video_dir.mkdir()
            index = downloader.CompletionIndex(output_root)
            source_path = video_dir / "audio.webm"
            source_path.write_bytes(b"source audio")

            self.assertTrue(
                index.mark_downloaded("phase123", source_path)
            )
            state = json.loads(
                index.path.read_text(encoding="utf-8")
            )
            self.assertEqual(state["version"], 2)
            self.assertEqual(
                state["videos"]["phase123"]["phase"],
                "downloaded",
            )

            source_path.unlink()
            (video_dir / "audio.mp3").write_bytes(b"mp3")
            self.assertTrue(index.mark_converted("phase123", video_dir))
            state = json.loads(
                index.path.read_text(encoding="utf-8")
            )
            self.assertEqual(
                state["videos"]["phase123"]["phase"],
                "converted",
            )
            self.assertIsNone(index.find_complete("phase123"))

            (video_dir / "thumbnail.jpg").write_bytes(b"jpg")
            (video_dir / "info.txt").write_text(
                "Title: Phase test\n\nDescription:\nTest\n",
                encoding="utf-8",
            )
            self.assertTrue(index.mark_complete("phase123", video_dir))
            state = json.loads(
                index.path.read_text(encoding="utf-8")
            )
            self.assertEqual(
                state["videos"]["phase123"]["phase"],
                "complete",
            )
            self.assertEqual(
                index.find_complete("phase123"),
                video_dir.resolve(),
            )

    def test_version_one_completion_index_is_migrated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output_root = Path(temporary)
            video_dir = output_root / "Legacy [legacy123]"
            _write_complete_output(video_dir)
            state_path = output_root / ".youtube-audio-completed.json"
            state_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "videos": {
                            "legacy123": {
                                "folder": video_dir.name,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            index = downloader.CompletionIndex(output_root)
            state = json.loads(state_path.read_text(encoding="utf-8"))

            self.assertEqual(state["version"], 2)
            self.assertEqual(
                state["videos"]["legacy123"]["phase"],
                "complete",
            )
            self.assertEqual(
                index.find_complete("legacy123"),
                video_dir.resolve(),
            )

    def test_bootstraps_legacy_folders_and_revalidates_them(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output_root = Path(temporary)
            complete = output_root / "Complete [done123]"
            partial = output_root / "Partial [partial123]"
            _write_complete_output(complete)
            partial.mkdir()
            (partial / "audio.mp3").write_bytes(b"mp3")
            (partial / "info.txt").write_text(
                "Title: Partial\n\nDescription:\nTest\n",
                encoding="utf-8",
            )

            index = downloader.CompletionIndex(output_root)

            self.assertEqual(
                index.find_complete("done123"),
                complete.resolve(),
            )
            self.assertIsNone(index.find_complete("partial123"))
            self.assertTrue(
                (
                    output_root
                    / ".youtube-audio-completed.json"
                ).is_file()
            )

            (complete / "thumbnail.jpg").write_bytes(b"")
            reloaded = downloader.CompletionIndex(output_root)
            self.assertIsNone(reloaded.find_complete("done123"))
            self.assertEqual(
                reloaded.find_status("done123"),
                ("converted", complete.resolve()),
            )
            self.assertEqual(
                reloaded.remove_empty_media_artifacts("done123"),
                ("thumbnail.jpg",),
            )
            self.assertFalse((complete / "thumbnail.jpg").exists())
            self.assertTrue((complete / "audio.mp3").is_file())
            self.assertTrue((complete / "info.txt").is_file())

    def test_completion_state_is_scoped_to_the_destination(self) -> None:
        with (
            tempfile.TemporaryDirectory() as first_temporary,
            tempfile.TemporaryDirectory() as second_temporary,
        ):
            first_root = Path(first_temporary)
            second_root = Path(second_temporary)
            _write_complete_output(first_root / "Complete [same123]")

            first_index = downloader.CompletionIndex(first_root)
            second_index = downloader.CompletionIndex(second_root)

            self.assertIsNotNone(first_index.find_complete("same123"))
            self.assertIsNone(second_index.find_complete("same123"))

    def test_channel_resume_skips_140_and_downloads_remaining_160(
        self,
    ) -> None:
        fake_yt_dlp = types.ModuleType("yt_dlp")
        fake_utils = types.ModuleType("yt_dlp.utils")

        class FakeDownloadCancelled(Exception):
            pass

        class FakeDownloadError(Exception):
            pass

        class FakePostProcessingError(Exception):
            pass

        class FakePostProcessor:
            pass

        fake_utils.DownloadCancelled = FakeDownloadCancelled
        fake_utils.DownloadError = FakeDownloadError
        fake_utils.PostProcessingError = FakePostProcessingError
        fake_yt_dlp.utils = fake_utils
        fake_yt_dlp.postprocessor = types.SimpleNamespace(
            PostProcessor=FakePostProcessor
        )

        class FakeYoutubeDL:
            zero_artifact_was_removed = False

            def __init__(self, options):
                self.options = options
                self.processor = None
                self.download_attempts = 0

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def add_post_processor(self, processor, *, when):
                self.processor = processor
                self.when = when

            def download(self, _urls):
                output_root = Path(self.options["paths"]["home"])
                for number in range(300):
                    video_id = f"id{number:03d}"
                    info = {
                        "id": video_id,
                        "title": f"Video {video_id}",
                        "description": "Test",
                    }
                    if self.options["match_filter"](
                        info,
                        incomplete=True,
                    ):
                        continue

                    self.download_attempts += 1
                    video_dir = (
                        output_root
                        / f"Video {video_id} [{video_id}]"
                    )
                    if number == 140:
                        type(self).zero_artifact_was_removed = not (
                            video_dir / "audio.mp3"
                        ).exists()
                    _write_complete_output(video_dir)
                    self.processor.run(
                        {
                            **info,
                            "filepath": str(video_dir / "audio.mp3"),
                        }
                    )
                type(self).last_download_attempts = self.download_attempts
                return 0

        fake_yt_dlp.YoutubeDL = FakeYoutubeDL

        with tempfile.TemporaryDirectory() as temporary:
            output_root = Path(temporary)
            video_ids = [f"id{number:03d}" for number in range(300)]
            for video_id in video_ids[:140]:
                _write_complete_output(
                    output_root / f"Video {video_id} [{video_id}]"
                )
            partial = output_root / "Video id140 [id140]"
            partial.mkdir()
            (partial / "audio.mp3").write_bytes(b"")
            (partial / "thumbnail.jpg").write_bytes(b"jpg")
            (partial / "info.txt").write_text(
                "Title: Partial\n\nDescription:\nTest\n",
                encoding="utf-8",
            )

            events = []
            with (
                mock.patch.dict(
                    sys.modules,
                    {
                        "yt_dlp": fake_yt_dlp,
                        "yt_dlp.utils": fake_utils,
                    },
                ),
                mock.patch.object(
                    downloader,
                    "check_dependencies",
                    return_value=DependencyReport((), "Node.js"),
                ),
                mock.patch.object(
                    downloader,
                    "detect_javascript_runtimes",
                    return_value={"node": {"path": "node"}},
                ),
            ):
                result = download_url(
                    "https://www.youtube.com/@example/videos",
                    output_root,
                    events.append,
                    threading.Event(),
                )

            self.assertEqual(result.skipped_videos, 140)
            self.assertEqual(result.completed_videos, 160)
            self.assertFalse(result.had_errors)
            self.assertEqual(FakeYoutubeDL.last_download_attempts, 160)
            self.assertTrue(FakeYoutubeDL.zero_artifact_was_removed)
            self.assertEqual(
                sum(event.kind == "video_skipped" for event in events),
                140,
            )
            self.assertEqual(
                sum(event.kind == "video_complete" for event in events),
                160,
            )


class RuntimeDetectionTests(unittest.TestCase):
    def test_unsupported_node_version_is_not_reported_as_available(self) -> None:
        def find_executable(name):
            return "node.exe" if name == "node" else None

        with (
            mock.patch.object(
                downloader,
                "_find_executable",
                side_effect=find_executable,
            ),
            mock.patch.object(
                downloader.subprocess,
                "run",
                return_value=types.SimpleNamespace(
                    stdout="v20.20.2\n",
                    stderr="",
                ),
            ),
        ):
            self.assertEqual(downloader.detect_javascript_runtimes(), {})

    def test_supported_node_version_is_enabled(self) -> None:
        def find_executable(name):
            return "node.exe" if name == "node" else None

        with (
            mock.patch.object(
                downloader,
                "_find_executable",
                side_effect=find_executable,
            ),
            mock.patch.object(
                downloader.subprocess,
                "run",
                return_value=types.SimpleNamespace(
                    stdout="v22.1.0\n",
                    stderr="",
                ),
            ),
        ):
            self.assertEqual(
                downloader.detect_javascript_runtimes(),
                {"node": {"path": "node.exe"}},
            )


class BatchDownloadTests(unittest.TestCase):
    def test_batch_continues_after_one_input_fails(self) -> None:
        callback_events = []
        results = [
            downloader.DownloadResult(1, False),
            DownloadFailedError("Unavailable"),
            downloader.DownloadResult(2, True),
        ]

        with (
            mock.patch.object(
                downloader,
                "detect_javascript_runtimes",
                return_value={},
            ),
            mock.patch.object(
                downloader,
                "check_dependencies",
                return_value=DependencyReport((), None),
            ),
            mock.patch.object(
                downloader,
                "download_url",
                side_effect=results,
            ) as mocked_download,
        ):
            result = download_urls(
                [
                    "https://youtu.be/one",
                    "https://youtu.be/two",
                    "https://youtu.be/three",
                ],
                Path("downloads"),
                callback_events.append,
                threading.Event(),
            )

        self.assertEqual(mocked_download.call_count, 3)
        self.assertEqual(result.completed_videos, 3)
        self.assertTrue(result.had_errors)
        self.assertTrue(
            any(event.kind == "error" for event in callback_events)
        )
        self.assertTrue(
            any(
                event.kind == "processing"
                and event.message.startswith("[1/3]")
                for event in callback_events
            )
        )

    def test_batch_removes_exact_duplicate_inputs(self) -> None:
        with (
            mock.patch.object(
                downloader,
                "detect_javascript_runtimes",
                return_value={},
            ),
            mock.patch.object(
                downloader,
                "check_dependencies",
                return_value=DependencyReport((), None),
            ),
            mock.patch.object(
                downloader,
                "download_url",
                return_value=downloader.DownloadResult(1, False),
            ) as mocked_download,
        ):
            result = download_urls(
                [
                    "https://youtu.be/one",
                    "https://youtu.be/one",
                ],
                Path("downloads"),
                lambda _event: None,
                threading.Event(),
            )

        self.assertEqual(mocked_download.call_count, 1)
        self.assertEqual(result.completed_videos, 1)

    def test_batch_treats_already_complete_items_as_success(self) -> None:
        callback_events = []
        with (
            tempfile.TemporaryDirectory() as temporary,
            mock.patch.object(
                downloader,
                "detect_javascript_runtimes",
                return_value={},
            ),
            mock.patch.object(
                downloader,
                "check_dependencies",
                return_value=DependencyReport((), None),
            ),
            mock.patch.object(
                downloader,
                "download_url",
                return_value=downloader.DownloadResult(0, False, 140),
            ),
        ):
            result = download_urls(
                ["https://www.youtube.com/@example/videos"],
                Path(temporary),
                callback_events.append,
                threading.Event(),
            )

        self.assertEqual(result.completed_videos, 0)
        self.assertEqual(result.skipped_videos, 140)
        self.assertFalse(result.had_errors)
        self.assertFalse(
            any(
                event.kind == "warning"
                and "No complete videos" in event.message
                for event in callback_events
            )
        )

    def test_batch_counts_unique_paths_even_when_a_source_fails(self) -> None:
        events = []
        first_path = Path("downloads") / "First [one]"
        second_path = Path("downloads") / "Second [two]"

        def fake_download(url, _output, callback, _cancel, **_kwargs):
            if url.endswith("one"):
                callback(
                    DownloadEvent(
                        "video_complete",
                        "Saved first",
                        path=first_path,
                    )
                )
                raise DownloadFailedError("Later item failed")
            if url.endswith("duplicate"):
                callback(
                    DownloadEvent(
                        "video_complete",
                        "Saved first again",
                        path=first_path,
                    )
                )
                return downloader.DownloadResult(1, False)
            callback(
                DownloadEvent(
                    "video_complete",
                    "Saved second",
                    path=second_path,
                )
            )
            return downloader.DownloadResult(1, False)

        with (
            mock.patch.object(
                downloader,
                "detect_javascript_runtimes",
                return_value={},
            ),
            mock.patch.object(
                downloader,
                "check_dependencies",
                return_value=DependencyReport((), None),
            ),
            mock.patch.object(
                downloader,
                "download_url",
                side_effect=fake_download,
            ),
        ):
            result = download_urls(
                [
                    "https://youtu.be/one",
                    "https://youtu.be/duplicate",
                    "https://youtu.be/two",
                ],
                Path("downloads"),
                events.append,
                threading.Event(),
            )

        self.assertEqual(result.completed_videos, 2)
        self.assertTrue(result.had_errors)
        self.assertEqual(
            sum(event.kind == "video_complete" for event in events),
            2,
        )

    def test_batch_honours_preexisting_cancellation(self) -> None:
        cancel_event = threading.Event()
        cancel_event.set()

        with (
            mock.patch.object(
                downloader,
                "detect_javascript_runtimes",
                return_value={},
            ),
            mock.patch.object(
                downloader,
                "check_dependencies",
                return_value=DependencyReport((), None),
            ),
            mock.patch.object(downloader, "download_url") as mocked_download,
        ):
            with self.assertRaises(downloader.UserCancelledError):
                download_urls(
                    ["https://youtu.be/one"],
                    Path("downloads"),
                    lambda _event: None,
                    cancel_event,
                )

        mocked_download.assert_not_called()


if __name__ == "__main__":
    unittest.main()
