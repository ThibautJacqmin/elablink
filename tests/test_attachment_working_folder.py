"""Offline working-copy preservation, preview transfer bounds and folder settings."""

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import elablink.config as configuration
from elablink._attachment_store import AttachmentStore
from tests.test_attachment_store import FakeClient, Response


@contextmanager
def working_directory(path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


class AttachmentWorkingFolderTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.client = FakeClient()
        self.cache = self.root / "cache"
        self.folder = self.root / "working"
        self.store = self.new_store(folder=self.folder)

    def new_store(self, kind="experiments", identifier=17, *, folder=None):
        return AttachmentStore(None, kind, identifier, self.cache, lambda: self.client, folder=folder)

    def test_working_copy_has_a_stable_path_and_retains_local_edits(self):
        row = self.client.add("measurement.txt", b"server content")
        path = self.store.download([row])[0]
        self.assertTrue(path.is_relative_to(self.folder))
        self.assertEqual(path.parts[-3:], ("experiments_17", "1", "measurement.txt"))
        path.write_bytes(b"local analysis edits")
        reused = self.store.download([row], overwrite=True)[0]
        self.assertEqual(reused, path)
        self.assertEqual(reused.read_bytes(), b"local analysis edits")
        self.assertEqual(len(self.client.reads), 1)

    def test_changed_server_copy_is_reused_until_explicit_refresh(self):
        row = self.client.add("signal.txt", b"original")
        path = self.store.download([row])[0]
        self.client.contents[row["id"]] = b"new version"
        self.client.rows[0].update(filesize=11, hash=hashlib.sha256(b"new version").hexdigest())
        self.assertEqual(self.store.download([row])[0], path)
        self.assertEqual(path.read_bytes(), b"original")
        self.assertEqual(len(self.client.reads), 1)
        self.assertEqual(self.store.download([row], refresh=True)[0], path)
        self.assertEqual(path.read_bytes(), b"new version")
        self.assertEqual(len(self.client.reads), 2)

    def test_failed_refresh_preserves_local_edits_and_removes_partial_file(self):
        row = self.client.add("signal.txt", b"original")
        path = self.store.download([row])[0]
        path.write_bytes(b"precious local changes")
        self.client.response_factory = lambda content: Response(content[:2], length=len(content))
        with self.assertRaisesRegex(OSError, "Incomplete download"):
            self.store.download([row], refresh=True)
        self.assertEqual(path.read_bytes(), b"precious local changes")
        self.assertEqual(list(path.parent.glob("*.part")), [])

    def test_working_copies_are_separate_by_record_kind_id_server_and_upload_id(self):
        first = self.client.add("same.txt", b"first")
        second = self.client.add("same.txt", b"second")
        paths = self.store.download([first, second])
        paths += self.new_store(identifier=18, folder=self.folder).download([first])
        paths += self.new_store(kind="items", folder=self.folder).download([first])
        self.client.configuration.host = "https://another.invalid/api/v2"
        paths += self.store.download([first])
        self.assertEqual(len(set(paths)), 5)
        self.assertEqual([path.read_bytes() for path in paths], [b"first", b"second", b"first", b"first", b"first"])

    def test_cache_cleanup_preserves_working_and_explicitly_saved_copies(self):
        row = self.client.add()
        cached = self.new_store().download([row])[0]
        working = self.store.download([row])[0]
        saved = self.store.save_file(row, self.root / "saved.txt")
        working.write_bytes(b"local edits")
        information = self.store.clear_cache()
        self.assertEqual(information["files"], 0)
        self.assertFalse(cached.exists())
        self.assertEqual(working.read_bytes(), b"local edits")
        self.assertEqual(saved.read_bytes(), b"measured signal")
        self.assertTrue(self.folder.is_dir())

    def test_switching_folders_or_back_to_cache_does_not_move_or_delete_files(self):
        row = self.client.add()
        original = self.store.download([row])[0]
        other = self.root / "other working folder"
        self.assertEqual(self.store.set_folder(other), {"folder": other})
        second = self.store.download([row])[0]
        self.assertTrue(second.is_relative_to(other))
        self.assertTrue(original.is_file())
        self.assertEqual(self.store.set_folder(None), {"folder": None})
        cached = self.store.download([row])[0]
        self.assertTrue(cached.is_relative_to(self.cache))
        self.assertTrue(original.is_file())
        self.assertTrue(second.is_file())

    def test_invalid_or_overlapping_working_folder_is_rejected_without_creating_it(self):
        for folder in (self.cache, self.cache / "working", self.cache.parent):
            with self.subTest(folder=folder), self.assertRaisesRegex(ValueError, "temporary cache"):
                self.store.set_folder(folder)
        self.assertFalse(self.cache.exists())
        self.assertEqual(self.store.folder_info(), {"folder": self.folder})
        file = self.root / "file.txt"
        file.write_bytes(b"keep")
        with self.assertRaises(NotADirectoryError):
            self.store.set_folder(file)
        self.assertEqual(file.read_bytes(), b"keep")

    def test_symbolic_link_or_junction_in_working_path_is_not_followed(self):
        row = self.client.add()
        target = self.store._working_target(row)
        target.parent.mkdir(parents=True)
        for method in ("is_symlink", "is_junction"):
            with self.subTest(method=method), patch.object(
                Path, method, new=lambda path: path == target.parent, create=True,
            ):
                with self.assertRaisesRegex(ValueError, "symbolic link|unsafe"):
                    self.store.download([row])
        self.assertEqual(self.client.reads, [])

    def test_junction_check_on_python_before_312_uses_windows_reparse_tag(self):
        row = self.client.add()
        target = self.store._working_target(row)
        target.parent.mkdir(parents=True)
        original = Path.lstat

        def information(path):
            if path == target.parent:
                return SimpleNamespace(st_mode=0o040755, st_reparse_tag=0xA0000003)
            return original(path)

        with (patch.object(Path, "is_junction", None, create=True),
              patch.object(Path, "lstat", information)):
            with self.assertRaisesRegex(ValueError, "symbolic link|unsafe"):
                self.store.download([row])
        self.assertEqual(self.client.reads, [])

    def test_file_created_during_working_download_is_not_overwritten(self):
        row = self.client.add()
        target = self.store._working_target(row)

        def appeared():
            if not target.exists():
                target.write_bytes(b"another application's copy")

        self.client.response_factory = lambda data: Response(data, on_read=appeared)
        self.assertEqual(self.store.download([row]), [target])
        self.assertEqual(target.read_bytes(), b"another application's copy")
        self.assertEqual(list(target.parent.glob("*.part")), [])

    def test_preview_limit_rejects_metadata_before_starting_any_transfer(self):
        first = self.client.add("first.txt", b"small")
        second = self.client.add("second.txt", b"larger contents")
        with self.assertRaisesRegex(ValueError, "preview download limit"):
            self.store.download([first, second], max_bytes=8)
        self.assertEqual(self.client.reads, [])
        self.assertFalse(self.folder.exists())

    def test_preview_limit_rejects_large_http_length_and_closes_response(self):
        row = self.client.add()
        self.client.rows[0]["filesize"] = None
        with self.assertRaisesRegex(ValueError, "preview download limit"):
            self.store.download([row], max_bytes=4)
        self.assertTrue(self.client.responses[-1].closed)
        self.assertTrue(self.client.responses[-1].released)
        self.assertEqual(list(self.folder.rglob("*.part")), [])

    def test_preview_limit_counts_actual_decoded_bytes_when_metadata_lies(self):
        row = self.client.add()
        self.client.rows[0]["filesize"] = 0

        def response(data):
            result = Response(data)
            result.headers = {}
            return result

        self.client.response_factory = response
        with self.assertRaisesRegex(ValueError, "preview download limit"):
            self.store.download([row], max_bytes=5)
        self.assertTrue(self.client.responses[-1].closed)
        self.assertTrue(self.client.responses[-1].released)
        self.assertFalse(self.store._working_target(row).exists())
        self.assertEqual(list(self.folder.rglob("*.part")), [])
        # A deliberate full download uses no preview cap.
        self.assertEqual(self.store.download([row])[0].read_bytes(), b"measured signal")


class BrowserFolderConfigurationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.project = self.root / "project"
        self.notebooks = self.project / "examples" / "notebooks"
        self.notebooks.mkdir(parents=True)
        fallback = patch.object(configuration, "_PROJECT_ROOT", self.root / "no-checkout")
        fallback.start()
        self.addCleanup(fallback.stop)

    def setting(self, directory, value):
        (directory / "pyproject.toml").write_text(f"[tool.elablink]\nbrowser_folder = {value}\n", encoding="utf-8")

    def test_project_folder_is_relative_to_project_and_does_not_need_credentials(self):
        self.setting(self.project, '"attachments"')
        with working_directory(self.notebooks):
            self.assertEqual(configuration.resolve_browser_folder(), self.project / "attachments")
        self.assertFalse((self.project / "attachments").exists())

    def test_explicit_folder_overrides_project_and_is_relative_to_current_directory(self):
        self.setting(self.project, '"attachments"')
        with working_directory(self.notebooks):
            self.assertEqual(configuration.resolve_browser_folder("chosen"), self.notebooks / "chosen")

    def test_nearest_declaring_project_wins_and_absent_setting_uses_temporary_cache(self):
        with working_directory(self.notebooks):
            self.assertIsNone(configuration.resolve_browser_folder())
        self.setting(self.project, '"attachments"')
        self.setting(self.notebooks.parent, '"other"')
        with working_directory(self.notebooks):
            self.assertEqual(configuration.resolve_browser_folder(), self.notebooks.parent / "other")

    def test_invalid_folder_values_and_existing_file_are_errors(self):
        for value in ('""', '7', '[]'):
            self.setting(self.project, value)
            with self.subTest(value=value), working_directory(self.notebooks), self.assertRaisesRegex(ValueError, "browser_folder"):
                configuration.resolve_browser_folder()
        file = self.project / "not a folder"
        file.write_bytes(b"keep")
        self.setting(self.project, '"not a folder"')
        with working_directory(self.notebooks), self.assertRaises(NotADirectoryError):
            configuration.resolve_browser_folder()
        self.assertEqual(file.read_bytes(), b"keep")


if __name__ == "__main__":
    unittest.main()
