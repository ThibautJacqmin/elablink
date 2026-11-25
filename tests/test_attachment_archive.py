"""Local ZIP browsing and extraction checks; no API or Qt dependencies."""

from concurrent.futures import CancelledError
from pathlib import Path
import stat
import tempfile
import threading
import unittest
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile, ZipInfo

from elablink._attachment_archive import create_zip, extract_zip, list_zip


class AttachmentArchiveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.archive = self.directory / "figures.zip"
        self.destination = self.directory / "extracted"

    def make_archive(self, entries):
        with ZipFile(self.archive, "w") as archive:
            for name, contents in entries:
                archive.writestr(name, contents)

    def test_listing_does_not_extract_and_extraction_preserves_nested_contents(self):
        self.make_archive([("figures/", b""), ("figures/signal.png", b"png-data"),
                           ("notes.txt", b"measurement")])
        rows = list_zip(self.archive)
        self.assertEqual(rows[0], {"name": "figures/", "size": 0, "compressed_size": 0, "is_dir": True})
        self.assertEqual(rows[1]["name"], "figures/signal.png")
        self.assertFalse(self.destination.exists())
        progress = []
        paths = extract_zip(self.archive, self.destination, progress=lambda *args: progress.append(args))
        self.assertEqual([path.relative_to(self.destination).as_posix() for path in paths],
                         ["figures/signal.png", "notes.txt"])
        self.assertEqual(paths[0].read_bytes(), b"png-data")
        self.assertEqual(paths[1].read_bytes(), b"measurement")
        self.assertEqual(progress[-1][:2], (19, 19))

    def test_unsafe_names_and_symlinks_are_rejected_before_extracting_any_file(self):
        unsafe = ["../outside.txt", "/absolute.txt", "C:/absolute.txt", "a/../../outside.txt",
                  "a\\..\\outside.txt", "folder//file.txt", "CON.txt", "trailing. ", "name:stream"]
        link = ZipInfo("link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        for name in [*unsafe, link]:
            with self.subTest(name=getattr(name, "filename", name)):
                self.make_archive([("valid.txt", b"safe"), (name, b"../outside")])
                with self.assertRaises(ValueError):
                    extract_zip(self.archive, self.destination)
                self.assertFalse(self.destination.exists())
        self.assertFalse((self.directory / "outside.txt").exists())

    def test_existing_files_are_not_overwritten_and_nothing_is_partly_extracted(self):
        self.destination.mkdir()
        original = self.destination / "notes.txt"
        original.write_text("original", encoding="utf-8")
        self.make_archive([("new.txt", b"new"), ("notes.txt", b"replacement")])
        with self.assertRaises(FileExistsError):
            extract_zip(self.archive, self.destination)
        self.assertEqual(original.read_text(encoding="utf-8"), "original")
        self.assertFalse((self.destination / "new.txt").exists())

    def test_conflicting_archive_paths_are_rejected_portably(self):
        for entries in [[("Signal.png", b"one"), ("signal.png", b"two")],
                        [("folder", b"file"), ("folder/child.txt", b"child")]]:
            with self.subTest(entries=entries):
                self.make_archive(entries)
                with self.assertRaises(ValueError):
                    extract_zip(self.archive, self.destination)
                self.assertFalse(self.destination.exists())

    def test_existing_symbolic_link_cannot_redirect_extraction(self):
        self.destination.mkdir()
        outside = self.directory / "outside"
        outside.mkdir()
        link = self.destination / "folder"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Creating symbolic links is unavailable")
        self.make_archive([("folder/file.txt", b"must stay inside")])
        with self.assertRaises(ValueError):
            extract_zip(self.archive, self.destination)
        self.assertFalse((outside / "file.txt").exists())

    def test_cancellation_removes_incomplete_file(self):
        self.make_archive([("large.bin", b"x" * (2 * 1024 * 1024))])
        cancel = threading.Event()

        def progress(done, total, name):
            if done:
                cancel.set()

        with self.assertRaises(CancelledError):
            extract_zip(self.archive, self.destination, cancel=cancel, progress=progress)
        self.assertFalse((self.destination / "large.bin").exists())

    def test_invalid_archive_reports_zip_error_without_creating_directory(self):
        self.archive.write_bytes(b"not a zip")
        with self.assertRaises(BadZipFile):
            list_zip(self.archive)
        with self.assertRaises(BadZipFile):
            extract_zip(self.archive, self.destination)
        self.assertFalse(self.destination.exists())


class AttachmentZipExportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.destination = self.directory / "selected attachments.zip"
        self.sources = self.directory / "cached files"
        self.sources.mkdir()

    def source(self, name, contents):
        path = self.sources / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
        return path

    def assert_no_partial_archive(self):
        self.assertEqual(list(self.directory.glob(".elablink-*.zip.part")), [])

    def test_selection_is_exported_with_flat_unicode_names_and_correct_contents(self):
        first = self.source("first/mesure \u03c0.csv", b"time,signal\n0,1\n")
        second = self.source("second/r\u00e9sultat.txt", b"calibration")
        progress = []
        result = create_zip([first, second], self.destination, progress=lambda *args: progress.append(args))
        self.assertEqual(result, self.destination)
        with ZipFile(result) as archive:
            self.assertEqual(archive.namelist(), [first.name, second.name])
            self.assertEqual(archive.read(first.name), first.read_bytes())
            self.assertEqual(archive.read(second.name), second.read_bytes())
            self.assertTrue(all(entry.compress_type == ZIP_DEFLATED for entry in archive.infolist()))
        self.assertEqual(progress[-1][:2], (27, 27))
        self.assertEqual(first.read_bytes(), b"time,signal\n0,1\n")
        self.assertEqual(second.read_bytes(), b"calibration")
        self.assert_no_partial_archive()

    def test_existing_destination_requires_explicit_overwrite(self):
        source = self.source("signal.txt", b"new")
        self.destination.write_bytes(b"previous download")
        with self.assertRaises(FileExistsError):
            create_zip([source], self.destination)
        self.assertEqual(self.destination.read_bytes(), b"previous download")
        create_zip([source], self.destination, overwrite=True)
        with ZipFile(self.destination) as archive:
            self.assertEqual(archive.read(source.name), b"new")
        self.assertEqual(source.read_bytes(), b"new")
        self.assert_no_partial_archive()

    def test_empty_duplicate_and_source_destination_selections_are_rejected(self):
        first = self.source("first/Signal.txt", b"first")
        second = self.source("second/signal.TXT", b"second")
        for paths in ([], [first, second], [first, first]):
            with self.subTest(paths=paths), self.assertRaises(ValueError):
                create_zip(paths, self.destination)
        with self.assertRaisesRegex(ValueError, "source files"):
            create_zip([first], first, overwrite=True)
        self.assertEqual(first.read_bytes(), b"first")
        self.assertFalse(self.destination.exists())
        self.assert_no_partial_archive()

    def test_cancelled_export_keeps_existing_destination_and_sources(self):
        source = self.source("large.bin", b"x" * (2 * 1024 * 1024))
        self.destination.write_bytes(b"existing")
        cancel = threading.Event()

        def progress(done, total, name):
            if done:
                cancel.set()

        with self.assertRaises(CancelledError):
            create_zip([source], self.destination, overwrite=True, cancel=cancel, progress=progress)
        self.assertEqual(self.destination.read_bytes(), b"existing")
        self.assertEqual(source.stat().st_size, 2 * 1024 * 1024)
        self.assert_no_partial_archive()

    def test_compression_failure_does_not_replace_existing_destination(self):
        source = self.source("signal.txt", b"contents")
        self.destination.write_bytes(b"existing")
        with patch("elablink._attachment_archive.ZipFile.open", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                create_zip([source], self.destination, overwrite=True)
        self.assertEqual(self.destination.read_bytes(), b"existing")
        self.assertEqual(source.read_bytes(), b"contents")
        self.assert_no_partial_archive()

    def test_destination_created_during_export_is_not_replaced(self):
        source = self.source("signal.txt", b"contents")

        def progress(done, total, name):
            if done:
                self.destination.write_bytes(b"created concurrently")

        with self.assertRaises(FileExistsError):
            create_zip([source], self.destination, progress=progress)
        self.assertEqual(self.destination.read_bytes(), b"created concurrently")
        self.assertEqual(source.read_bytes(), b"contents")
        self.assert_no_partial_archive()


if __name__ == "__main__":
    unittest.main()
