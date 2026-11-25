"""ZIP browsing/extraction quotas, using small synthetic archives only."""

import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile

from elablink._attachment_archive import extract_zip, list_zip


class ArchiveLimitTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.archive = self.directory / "attachments.zip"
        self.destination = self.directory / "extracted"

    def make_archive(self, rows):
        with ZipFile(self.archive, "w", compression=ZIP_DEFLATED) as archive:
            for name, data in rows:
                archive.writestr(name, data)

    def test_entry_count_is_limited_before_display_or_extraction(self):
        self.make_archive([("one.txt", b"one"), ("two.txt", b"two")])
        for operation in (list_zip, lambda path, **kwargs: extract_zip(path, self.destination, **kwargs)):
            with self.subTest(operation=operation), self.assertRaisesRegex(ValueError, "too many entries"):
                operation(self.archive, max_entries=1)
        self.assertFalse(self.destination.exists())
        self.assertEqual(len(list_zip(self.archive, max_entries=2)), 2)

    def test_small_compressed_archive_cannot_exceed_declared_expansion_limit(self):
        self.make_archive([("zeros.bin", b"\0" * (1024 * 1024))])
        self.assertLess(self.archive.stat().st_size, 2048)
        for operation in (list_zip, lambda path, **kwargs: extract_zip(path, self.destination, **kwargs)):
            with self.subTest(operation=operation), self.assertRaisesRegex(ValueError, "total uncompressed"):
                operation(self.archive, max_bytes=100_000)
        self.assertFalse(self.destination.exists())
        with self.assertRaisesRegex(ValueError, "uncompressed file size"):
            extract_zip(self.archive, self.destination, max_file_bytes=100_000)
        self.assertFalse(self.destination.exists())

    def test_extraction_checks_free_disk_space_before_creating_files(self):
        self.make_archive([("notes.txt", b"measurement")])
        with patch("elablink._attachment_archive.shutil.disk_usage", return_value=SimpleNamespace(free=1)):
            with self.assertRaisesRegex(OSError, "free disk space"):
                extract_zip(self.archive, self.destination)
        self.assertFalse(self.destination.exists())

    def test_actual_streamed_bytes_are_limited_even_if_entry_size_is_wrong(self):
        self.make_archive([("notes.txt", b"x")])
        for limits in ({"max_bytes": 100}, {"max_file_bytes": 100}):
            with self.subTest(limits=limits):
                with patch("elablink._attachment_archive.ZipFile.open", return_value=io.BytesIO(b"x" * 200)):
                    with self.assertRaisesRegex(ValueError, "uncompressed"):
                        extract_zip(self.archive, self.destination, **limits)
                self.assertFalse((self.destination / "notes.txt").exists())

    def test_normal_extraction_at_limits_preserves_contents(self):
        self.make_archive([("a/one.txt", b"one"), ("a/two.txt", b"two")])
        paths = extract_zip(self.archive, self.destination, max_entries=2, max_bytes=6, max_file_bytes=3)
        self.assertEqual([path.read_bytes() for path in paths], [b"one", b"two"])
        self.assertEqual(len(list_zip(self.archive, max_entries=2, max_bytes=6, max_file_bytes=3)), 2)

    def test_extreme_tree_depth_and_node_count_are_rejected_before_display(self):
        self.make_archive([("/".join(["deep"] * 65) + "/notes.txt", b"x")])
        with self.assertRaisesRegex(ValueError, "too deep"):
            list_zip(self.archive)
        self.make_archive([("one/a.txt", b"a"), ("two/b.txt", b"b")])
        with patch("elablink._attachment_archive._MAX_TREE_NODES", 3):
            with self.assertRaisesRegex(ValueError, "too many folders"):
                list_zip(self.archive)

    def test_invalid_limits_are_explicit_errors(self):
        self.make_archive([("notes.txt", b"x")])
        for name in ("max_bytes", "max_file_bytes", "max_entries"):
            for value in (0, -1, True, 1.5):
                with self.subTest(name=name, value=value), self.assertRaisesRegex(ValueError, "positive integer"):
                    list_zip(self.archive, **{name: value})
