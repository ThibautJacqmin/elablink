"""Offline checks of record-scoped attachment cache management."""

from concurrent.futures import CancelledError
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from elablink._attachment_store import AttachmentStore


class AttachmentCacheTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.client = SimpleNamespace(
            configuration=SimpleNamespace(host="https://offline.example/api/v2"),
            api_client=SimpleNamespace(call_api=Mock()),
            load_experiment=lambda **kwargs: SimpleNamespace(title="Offline record"),
        )
        self.client.load_resource = self.client.load_experiment
        self.factory = Mock(return_value=self.client)
        self.store = AttachmentStore(None, "experiments", 17, self.directory / "cache", self.factory)

    def cached_file(self, identifier, content=b"measurement", *, store=None):
        store = store or self.store
        row = {"id": identifier, "real_name": f"measurement-{identifier}.txt", "filesize": len(content)}
        store._connect()
        path = store._cache_target(row)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def test_empty_cache_reports_its_record_path_without_creating_directories(self):
        information = self.store.cache_info()
        self.assertEqual(information["bytes"], 0)
        self.assertEqual(information["files"], 0)
        self.assertEqual(information["path"].name, "experiments_17")
        self.assertTrue(information["path"].is_relative_to(self.directory / "cache"))
        self.assertFalse((self.directory / "cache").exists())
        self.factory.assert_called_once_with()

    def test_measure_and_clear_remove_only_current_record_cache(self):
        first = self.cached_file(1, b"first")
        second = self.cached_file(2, b"second")
        other_record = AttachmentStore(None, "experiments", 18, self.directory / "cache", self.factory)
        other_kind = AttachmentStore(None, "items", 17, self.directory / "cache", self.factory)
        retained = [self.cached_file(1, b"other record", store=other_record),
                    self.cached_file(1, b"resource", store=other_kind)]
        self.client.configuration.host = "https://second.example/api/v2"
        retained.append(self.cached_file(1, b"other server"))
        self.client.configuration.host = "https://offline.example/api/v2"
        saved = self.directory / "saved-measurement.txt"
        saved.write_bytes(b"saved copy")
        retained.append(saved)
        marker = self.directory / "cache" / "keep.txt"
        marker.write_bytes(b"cache root marker")
        retained.append(marker)
        expected = {path: path.read_bytes() for path in retained}
        information = self.store.cache_info()
        self.assertEqual((information["bytes"], information["files"]), (11, 2))
        result = self.store.clear_cache()
        self.assertEqual((result["bytes"], result["files"]), (0, 0))
        self.assertEqual((result["removed_bytes"], result["removed_files"]), (11, 2))
        self.assertTrue(result["path"].is_dir())
        self.assertFalse(first.exists())
        self.assertFalse(second.exists())
        self.assertTrue((self.directory / "cache").is_dir())
        for path, contents in expected.items():
            self.assertEqual(path.read_bytes(), contents)

    def test_symlink_or_junction_is_rejected_before_any_cached_file_is_deleted(self):
        original = self.cached_file(1)
        linked = original.parent.parent / "unsafe"
        linked.mkdir()
        (linked / "file.txt").write_bytes(b"preserved")
        for method in ("is_symlink", "is_junction"):
            with self.subTest(method=method), patch.object(
                Path, method, new=lambda path: path == linked, create=True,
            ):
                with self.assertRaisesRegex(ValueError, "symbolic link|unsafe"):
                    self.store.clear_cache()
                self.assertEqual(original.read_bytes(), b"measurement")
                self.assertEqual((linked / "file.txt").read_bytes(), b"preserved")

    def test_linked_server_or_record_directory_is_rejected(self):
        original = self.cached_file(1)
        record = original.parent.parent
        for linked in (record, record.parent):
            with self.subTest(path=linked), patch.object(
                Path, "is_symlink", autospec=True, side_effect=lambda path: path == linked,
            ):
                with self.assertRaisesRegex(ValueError, "symbolic link|unsafe"):
                    self.store.cache_info()
                self.assertTrue(original.exists())

    def test_in_use_file_reports_a_clear_error_and_preserves_contents(self):
        original = self.cached_file(1)
        with patch.object(Path, "unlink", side_effect=PermissionError("file is open")):
            with self.assertRaisesRegex(OSError, "Close applications"):
                self.store.clear_cache()
        self.assertEqual(original.read_bytes(), b"measurement")

    def test_resolved_paths_outside_the_cache_are_rejected_before_deletion(self):
        original = self.cached_file(1)
        outside = self.directory / "saved.txt"
        outside.write_bytes(b"saved copy")
        resolve = Path.resolve
        with patch.object(Path, "resolve", autospec=True,
                          side_effect=lambda path: outside if path == original else resolve(path)):
            with self.assertRaisesRegex(ValueError, "unsafe path"):
                self.store.clear_cache()
        self.assertEqual(original.read_bytes(), b"measurement")
        self.assertEqual(outside.read_bytes(), b"saved copy")

    def test_cancellation_preserves_cached_downloads(self):
        original = self.cached_file(1)
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(CancelledError):
            self.store.clear_cache(cancel=cancel)
        self.assertTrue(original.exists())

    def test_cache_methods_enforce_worker_thread_ownership(self):
        self.store.cache_info()
        errors = []

        def inspect_cache():
            try:
                self.store.cache_info()
            except RuntimeError as error:
                errors.append(str(error))

        worker = threading.Thread(target=inspect_cache)
        worker.start()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIn("worker", errors[0])


if __name__ == "__main__":
    unittest.main()
