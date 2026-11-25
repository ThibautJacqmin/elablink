"""Offline Qt interaction checks with a controlled background attachment store."""

from copy import deepcopy
from concurrent.futures import CancelledError
import importlib.util
import io
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from zipfile import ZipFile


HAS_QT = importlib.util.find_spec("PyQt6") is not None
if HAS_QT:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtCore import QEvent, QMimeData, QPoint, QPointF, QTimer, Qt, QUrl
    from PyQt6.QtGui import QColor, QDropEvent, QImage, QMouseEvent, QPainter, QPdfWriter
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication, QMessageBox
    import elablink._attachment_browser_qt as browser


class FakeStore:
    def __init__(self, directory):
        self.directory = directory
        self.title = "Offline attachments"
        self.rows = [
            {"id": 17, "real_name": "zeta.txt", "filesize": 2},
            {"id": 2, "real_name": "alpha.txt", "filesize": "1000"},
            {"id": 99, "real_name": "beam.png", "filesize": 50},
        ]
        self.calls = []
        self.blocked_operation = None
        self.started, self.release = threading.Event(), threading.Event()
        self.fail_rename = False
        self.fail_delete = False
        self.contents = {}
        self.folder = None
        self.download_options = []

    def record(self, operation, detail=None):
        self.calls.append((operation, detail, threading.get_ident()))
        if self.blocked_operation == operation:
            self.started.set()
            if not self.release.wait(5):
                raise TimeoutError("The test did not release its worker")

    def list_files(self):
        self.record("list")
        return deepcopy(self.rows)

    def record_link(self):
        self.record("record_link")
        return "https://offline.invalid/experiments.php?mode=view&id=123"

    def set_folder(self, folder):
        self.record("set_folder", folder)
        self.folder = Path(folder).resolve() if folder is not None else None
        return {"folder": self.folder}

    def attachment_links(self, rows):
        self.record("attachment_links", [row["id"] for row in rows])
        return [f"https://offline.invalid/app/download.php?f=blob-{row['id']}" for row in rows]

    def cache_info(self):
        self.record("cache_info")
        paths = [path for row in self.rows for path in (self.directory / str(row["id"])).glob("*") if path.is_file()]
        return {"path": self.directory, "bytes": sum(path.stat().st_size for path in paths), "files": len(paths)}

    def clear_cache(self, *, cancel=None):
        self.record("clear_cache")
        info = self.cache_info()
        for row in self.rows:
            for path in (self.directory / str(row["id"])).glob("*"):
                if path.is_file():
                    path.unlink()
        return {"path": self.directory, "bytes": 0, "files": 0,
                "removed_bytes": info["bytes"], "removed_files": info["files"]}

    def upload(self, paths, *, replace=False, **kwargs):
        self.record("upload", (paths, replace))
        return self.list_files()

    def rename(self, upload_id, name):
        self.record("rename", (upload_id, name))
        if self.fail_rename:
            raise PermissionError("The server refused this rename")
        next(row for row in self.rows if row["id"] == upload_id)["real_name"] = name
        return self.list_files()

    def delete(self, rows, *, cancel=None):
        self.record("delete", [row["id"] for row in rows])
        for row in rows:
            self.rows = [current for current in self.rows if current["id"] != row["id"]]
            if self.fail_delete:
                raise PermissionError("The server refused the remaining deletion")
        return self.list_files()

    def download(self, rows, directory=None, *, progress=None, cancel=None, **kwargs):
        self.record("download", [row["id"] for row in rows])
        self.download_options.append(dict(kwargs))
        if cancel is not None and cancel.is_set():
            raise CancelledError()
        current = {row["id"]: row for row in self.rows}
        if any(row["id"] not in current for row in rows):
            raise FileNotFoundError("An attachment changed or disappeared; refresh the file list")
        rows = [dict(current[row["id"]]) for row in rows]
        result = []
        for row in rows:
            parent = Path(directory) if directory else (self.folder or self.directory) / str(row["id"])
            parent.mkdir(parents=True, exist_ok=True)
            path = parent / row["real_name"]
            contents = self.contents.get(row["id"], b"downloaded content")
            limit = kwargs.get("max_bytes")
            if limit is not None and len(contents) > limit:
                raise ValueError("This file exceeds the automatic preview size limit")
            if directory or self.folder is None or not path.exists() or kwargs.get("refresh"):
                path.write_bytes(contents)
            result.append(path)
            if progress:
                progress(18, 18, row["real_name"])
        if kwargs.get("with_metadata"):
            return [{"row": row, "path": path} for row, path in zip(rows, result)]
        return result


@unittest.skipUnless(HAS_QT, "PyQt6 is not installed")
class QtAttachmentBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setQuitOnLastWindowClosed(False)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.store = FakeStore(self.directory)
        self.preview_store = FakeStore(self.directory / "previews")
        self.factory_threads = []

        def factory():
            self.factory_threads.append(threading.get_ident())
            return self.store

        for name in ("warning", "information", "question"):
            patcher = patch.object(QMessageBox, name, return_value=QMessageBox.StandardButton.Cancel)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)
        self.window = browser.AttachmentBrowser("unused.conf", "experiments", 123,
                                                 store_factory=factory, preview_store_factory=lambda: self.preview_store)
        self.addCleanup(self.close_window)
        self.window.show()
        self.wait_for(lambda: len(self.window.rows) == 3 and not self.window.busy)

    def wait_for(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            self.application.processEvents()
            time.sleep(0.002)
        self.assertTrue(predicate(), "Qt operation did not finish before the test deadline")

    def close_window(self):
        self.store.release.set()
        self.preview_store.release.set()
        self.wait_for(lambda: not self.window.busy)
        self.window.close()
        self.wait_for(lambda: not self.window._thread.isRunning())
        self.wait_for(lambda: not self.window._preview_thread.isRunning())
        self.application.clipboard().clear()
        self.application.processEvents()

    def select(self, *IDs):  # noqa: N803 - server IDs
        self.window.table.clearSelection()
        for index in range(self.window.table.topLevelItemCount()):
            item = self.window.table.topLevelItem(index)
            item.setSelected(item.data(0, Qt.ItemDataRole.UserRole)["id"] in IDs)

    def add_preview(self, identifier, name, contents=None):
        if contents is None:
            source = self.directory / name
            image = QImage(160, 80, QImage.Format.Format_RGB32)
            image.fill(QColor("red" if identifier == 99 else "blue"))
            self.assertTrue(image.save(str(source)))
            contents = source.read_bytes()
        row = {"id": identifier, "real_name": name, "filesize": len(contents)}
        for store in (self.store, self.preview_store):
            store.rows = [current for current in store.rows if current["id"] != identifier] + [dict(row)]
            store.contents[identifier] = contents
        self.window.refresh()
        self.wait_for(lambda: not self.window.busy)

    def wait_for_preview(self, name):
        self.wait_for(lambda: self.window.preview.title.text() == name
                      and not self.window.preview._image.isNull() and not self.window._preview_busy)

    def test_image_preview_and_arrow_navigation_follow_visible_sort_order(self):
        self.add_preview(99, "beam.png")
        self.add_preview(100, "second.JPG")
        self.add_preview(101, "third.bmp")
        self.select(99)
        self.wait_for_preview("beam.png")
        self.window.table.setFocus()
        QTest.keyClick(self.window.table, Qt.Key.Key_Right)
        self.wait_for_preview("second.JPG")
        self.assertEqual([row["id"] for row in self.window.selected_rows()], [100])
        self.window.search.setText("beam")
        QTest.keyClick(self.window.table, Qt.Key.Key_Right)
        self.wait_for_preview("beam.png")
        QTest.keyClick(self.window.table, Qt.Key.Key_Left)
        self.wait_for_preview("beam.png")

    def test_preview_does_not_block_explicit_transfers_or_run_on_gui_thread(self):
        self.add_preview(99, "beam.png")
        self.preview_store.blocked_operation = "download"
        self.select(99)
        self.wait_for(self.preview_store.started.is_set)
        self.assertFalse(self.window.busy)
        self.assertTrue(self.window.copy_action.isEnabled())
        self.window.copy_selected()
        self.wait_for(lambda: not self.window.busy)
        self.assertTrue(QApplication.clipboard().mimeData().hasUrls())
        self.assertTrue(self.window._preview_busy)
        self.preview_store.release.set()
        self.wait_for_preview("beam.png")
        self.assertTrue(all(thread != threading.get_ident() for _, _, thread in self.preview_store.calls))

    def test_text_and_notebook_selection_use_the_read_only_preview(self):
        source = "laser:\n  wavelength_nm: 780\n  enabled: true\n"
        self.add_preview(120, "settings.yaml", source.encode("utf-8"))
        self.select(120)
        self.wait_for(lambda: self.window.preview.text.toPlainText() == source)
        self.assertTrue(self.window.preview.text.isReadOnly())
        self.assertIs(self.window.preview.content.currentWidget(), self.window.preview.text)
        notebook = (
            b'{"nbformat":4,"cells":['
            b'{"cell_type":"markdown","source":["# Calibration"]},'
            b'{"cell_type":"code","source":["print(42)"],"outputs":[]}]}'
        )
        self.add_preview(121, "analysis.ipynb", notebook)
        self.select(121)
        self.wait_for(lambda: "print(42)" in self.window.preview.text.toPlainText())
        self.assertIn("# Calibration", self.window.preview.text.toPlainText())
        self.assertFalse(self.window.busy)

    def test_h5_preview_button_uses_double_click_action_without_automatic_download(self):
        self.add_preview(140, "measurement.h5", b"synthetic HDF5 data")
        self.select(140)
        self.assertTrue(self.window.preview.h5_open_button.isVisible())
        self.assertEqual(self.window.preview.title.text(), "measurement.h5")
        QTest.qWait(220)
        self.assertNotIn("download", [operation for operation, _, _ in self.preview_store.calls])
        process = Mock()
        process.poll.return_value = None
        with patch.object(browser, "open_h5viewer", return_value=process) as launch:
            self.window.preview.h5_open_button.click()
            self.wait_for(lambda: not self.window.busy)
        launch.assert_called_once()
        self.assertEqual(launch.call_args.args[0].name, "measurement.h5")
        self.assertEqual(launch.call_args.args[0].read_bytes(), b"synthetic HDF5 data")

    def test_switching_from_inflight_image_preview_to_h5_does_not_download_h5(self):
        self.add_preview(99, "beam.png")
        self.add_preview(140, "measurement.h5", b"synthetic HDF5 data")
        self.preview_store.blocked_operation = "download"
        self.select(99)
        self.wait_for(self.preview_store.started.is_set)
        self.select(140)
        self.preview_store.release.set()
        self.wait_for(lambda: not self.window._preview_busy)
        QTest.qWait(220)
        downloads = [IDs for operation, IDs, _ in self.preview_store.calls if operation == "download"]
        self.assertEqual(downloads, [[99]])
        self.assertTrue(self.window.preview.h5_open_button.isVisible())
        self.assertEqual(self.window.preview.title.text(), "measurement.h5")

    def test_ctrl_f_from_file_list_searches_read_only_markdown_preview(self):
        source = "# Calibration\n\nlaser one\nlaser two\n"
        self.add_preview(124, "README.md", source.encode("utf-8"))
        self.select(124)
        self.wait_for(lambda: self.window.preview.text.toPlainText() == source)
        self.window.activateWindow()
        self.window.table.setFocus()
        QTest.keyClick(self.window.table, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
        self.wait_for(lambda: self.window.preview.search_bar.isVisible())
        QTest.keyClicks(self.window.preview.search_input, "laser")
        first = self.window.preview.text.textCursor().selectionStart()
        QTest.keyClick(self.window.preview.search_input, Qt.Key.Key_Return)
        self.assertGreater(self.window.preview.text.textCursor().selectionStart(), first)
        QTest.keyClick(self.window.preview.search_input, Qt.Key.Key_Escape)
        self.assertFalse(self.window.preview.search_bar.isVisible())
        self.assertEqual(self.window.preview.text.toPlainText(), source)

    def choose_working_folder(self, name="working files"):
        folder = self.directory / name
        with patch.object(browser.QFileDialog, "getExistingDirectory", return_value=str(folder)):
            self.window.choose_folder_action.trigger()
        self.wait_for(lambda: not self.window.busy and not self.window._preview_paused)
        return folder.resolve()

    def test_working_folder_preserves_local_edits_and_cache_cleanup_keeps_them(self):
        self.add_preview(17, "notes.txt", b"server notes")
        self.select(17)
        folder = self.choose_working_folder()
        self.wait_for(lambda: self.window.preview.text.toPlainText() == "server notes")
        self.assertEqual(self.store.folder, folder)
        self.assertEqual(self.preview_store.folder, folder)
        self.assertIn(str(folder), self.window.storage_label.text())
        working = folder / "17/notes.txt"
        working.write_text("my local edits", encoding="utf-8")
        self.window.refresh()
        self.wait_for(lambda: not self.window.busy and self.window.preview.text.toPlainText() == "my local edits")
        self.window.copy_selected()
        self.wait_for(lambda: not self.window.busy)
        self.assertEqual(working.read_text(encoding="utf-8"), "my local edits")
        self.question.return_value = QMessageBox.StandardButton.Yes
        self.window.cache_action.trigger()
        self.wait_for(lambda: not self.window.busy and not self.window._preview_paused)
        self.assertEqual(working.read_text(encoding="utf-8"), "my local edits")
        self.assertEqual(self.window._folder, folder)

    def test_refresh_working_copy_requires_confirmation(self):
        self.add_preview(17, "notes.txt", b"server notes")
        self.select(17)
        folder = self.choose_working_folder()
        self.wait_for(lambda: self.window.preview.text.toPlainText() == "server notes")
        working = folder / "17/notes.txt"
        working.write_text("my local edits", encoding="utf-8")
        self.window.refresh_local_action.trigger()
        self.assertEqual(working.read_text(encoding="utf-8"), "my local edits")
        self.assertFalse(self.window.busy)
        self.question.return_value = QMessageBox.StandardButton.Yes
        self.window.refresh_local_action.trigger()
        self.wait_for(lambda: not self.window.busy and not self.window._preview_paused)
        self.assertEqual(working.read_text(encoding="utf-8"), "server notes")
        self.assertTrue(any(options.get("refresh") for options in self.store.download_options))

    def test_folder_switch_waits_for_preview_and_can_return_to_temporary_cache(self):
        self.add_preview(99, "beam.png")
        self.preview_store.blocked_operation = "download"
        self.select(99)
        self.wait_for(self.preview_store.started.is_set)
        folder = self.directory / "working files"
        with patch.object(browser.QFileDialog, "getExistingDirectory", return_value=str(folder)):
            self.window.choose_folder_action.trigger()
        self.assertTrue(self.window._preview_paused)
        self.preview_store.release.set()
        self.wait_for(lambda: not self.window.busy and not self.window._preview_paused)
        self.wait_for_preview("beam.png")
        working = folder / "99/beam.png"
        self.assertTrue(working.is_file())
        self.window.temporary_folder_action.trigger()
        self.wait_for(lambda: not self.window.busy and not self.window._preview_paused)
        self.wait_for_preview("beam.png")
        self.assertIsNone(self.store.folder)
        self.assertIsNone(self.preview_store.folder)
        self.assertTrue(working.is_file())
        self.assertEqual(self.window.storage_label.text(), "Temporary cache")

    def test_rejected_folder_keeps_current_storage_and_resumes_preview(self):
        with (patch.object(self.store, "set_folder", side_effect=ValueError("Choose a folder outside the cache")),
              patch.object(browser.QFileDialog, "getExistingDirectory", return_value=str(self.directory / "invalid"))):
            self.window.choose_folder_action.trigger()
            self.wait_for(lambda: not self.window.busy and not self.window._preview_paused)
        self.assertIsNone(self.window._folder)
        self.assertEqual(self.window.storage_label.text(), "Temporary cache")
        self.assertIn("outside the cache", self.warning.call_args.args[2])

    def test_automatic_preview_limit_does_not_limit_explicit_downloads(self):
        contents = b"large source text\n" * 10
        self.add_preview(150, "measurements.csv", contents)
        with patch.object(browser, "_AUTO_PREVIEW_BYTES", 32):
            self.select(150)
            self.wait_for(lambda: "preview size limit" in self.window.preview.image.text())
        self.assertEqual(self.preview_store.download_options[-1]["max_bytes"], 32)
        self.warning.assert_not_called()
        self.window.copy_selected()
        self.wait_for(lambda: not self.window.busy)
        local = Path(QApplication.clipboard().mimeData().urls()[0].toLocalFile())
        self.assertEqual(local.read_bytes(), contents)
        self.assertNotIn("max_bytes", self.store.download_options[-1])

    def test_svg_open_stays_in_internal_preview(self):
        svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100"><rect width="200" height="100" fill="blue"/></svg>'
        self.add_preview(151, "figure.svg", svg)
        self.select(151)
        with patch.object(browser.QDesktopServices, "openUrl") as external:
            self.window.open_selected()
            self.wait_for(lambda: not self.window.busy)
            self.wait_for_preview("figure.svg")
        external.assert_not_called()

    def test_explicit_open_browses_large_zip_after_automatic_preview_refuses_it(self):
        source = io.BytesIO()
        with ZipFile(source, "w") as archive:
            archive.writestr("data.bin", b"x" * (browser._AUTO_PREVIEW_BYTES + 1))
        self.add_preview(151, "large.zip", source.getvalue())
        self.select(151)
        self.wait_for(lambda: "preview size limit" in self.window.preview.image.text())
        self.window.open_selected()
        self.wait_for(lambda: not self.window.busy and self.window._archive_path is not None)
        self.assertEqual(self.window.archive_tree.topLevelItem(0).text(0), "data.bin")
        self.assertNotIn("max_bytes", self.store.download_options[-1])
        self.warning.assert_not_called()
        self.application.processEvents()
        self.assertIs(self.window.preview_stack.currentWidget(), self.window.archive_panel)

    def test_explicit_svg_open_bypasses_only_automatic_preview_limit(self):
        svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100"><rect width="200" height="100"/></svg>'
        self.add_preview(151, "figure.svg", svg)
        with (patch.object(browser, "_AUTO_PREVIEW_BYTES", 32),
              patch.object(browser.QDesktopServices, "openUrl") as external):
            self.select(151)
            self.wait_for(lambda: "preview size limit" in self.window.preview.image.text())
            self.window.open_selected()
            self.wait_for(lambda: not self.window.busy)
            self.wait_for_preview("figure.svg")
        external.assert_not_called()
        self.assertEqual((self.window.preview._image.width(), self.window.preview._image.height()), (200, 100))

    def test_late_automatic_preview_failure_does_not_replace_explicit_open(self):
        source = io.BytesIO()
        with ZipFile(source, "w") as archive:
            archive.writestr("signal.txt", b"signal")
        self.add_preview(151, "figures.zip", source.getvalue())
        self.preview_store.blocked_operation = "download"
        with patch.object(browser, "_AUTO_PREVIEW_BYTES", 32):
            self.select(151)
            self.wait_for(self.preview_store.started.is_set)
            self.window.open_selected()
            self.wait_for(lambda: not self.window.busy and self.window._archive_path is not None)
            path = self.window._archive_path
            self.preview_store.release.set()
            self.wait_for(lambda: not self.window._preview_busy and not self.window._preview_timer.isActive())
        self.assertIs(self.window.preview_stack.currentWidget(), self.window.archive_panel)
        self.assertEqual(self.window._archive_path, path)
        self.assertEqual(self.window.archive_tree.topLevelItem(0).text(0), "signal.txt")
        self.assertEqual(len(self.preview_store.download_options), 1)

    def test_explicit_zip_open_retries_after_failed_open_and_preserves_archive_limits(self):
        source = io.BytesIO()
        with ZipFile(source, "w") as archive:
            archive.writestr("signal.txt", b"signal")
            archive.writestr("notes.txt", b"notes")
        self.add_preview(151, "figures.zip", source.getvalue())
        self.select(151)
        self.window._preview_timer.stop()
        list_zip = browser.list_zip
        with patch.object(browser, "list_zip", side_effect=lambda path: list_zip(path, max_entries=1)):
            self.window.open_selected()
            self.wait_for(lambda: not self.window.busy)
        self.assertIn("too many entries", self.warning.call_args.args[2])
        self.assertIsNone(self.window._archive_path)
        self.window.open_selected()
        self.wait_for(lambda: not self.window.busy and self.window._archive_path is not None)
        self.assertEqual(self.window.archive_tree.topLevelItem(0).text(0), "signal.txt")

    def test_internal_open_is_background_work_and_can_be_cancelled(self):
        self.add_preview(151, "figures.zip", b"archive handled by test parser")
        self.select(151)
        self.window._preview_timer.stop()
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        threads = []

        def parse(path):
            threads.append(threading.get_ident())
            started.set()
            if not release.wait(3):
                raise TimeoutError("Test did not release archive parsing")
            return []

        with patch.object(browser, "list_zip", side_effect=parse):
            self.window.open_selected()
            self.wait_for(started.is_set)
            self.assertTrue(self.window.progress.isVisible())
            self.assertTrue(self.window.cancel_button.isVisible())
            self.window.cancel_button.click()
            release.set()
            self.wait_for(lambda: not self.window.busy)
        self.assertNotIn(threading.get_ident(), threads)
        self.assertIsNone(self.window._archive_path)
        self.assertEqual(self.window.statusBar().currentMessage(), "Transfer cancelled.")
        self.warning.assert_not_called()

    def test_internal_open_uses_upload_id_when_server_renames_zip_or_svg(self):
        from elablink._attachment_store import AttachmentStore
        from tests.test_attachment_store import FakeClient

        client = FakeClient()
        source = io.BytesIO()
        with ZipFile(source, "w") as archive:
            archive.writestr("signal.txt", b"signal")
        zip_row = client.add("old.zip", source.getvalue(), ID=151)
        svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100"><rect width="200" height="100"/></svg>'
        svg_row = client.add("old.svg", svg, ID=152)
        self.window._worker.store = None
        self.window._worker.factory = lambda: AttachmentStore(
            None, "experiments", 123, self.directory / "real-cache", lambda: client)
        self.window._show_rows([zip_row, svg_row])
        for identifier, name in ((151, "new.zip"), (152, "new.svg")):
            with self.subTest(name=name):
                self.select(identifier)
                self.window._preview_timer.stop()
                next(row for row in client.rows if row["id"] == identifier)["real_name"] = name
                item = next(self.window.table.topLevelItem(index)
                            for index in range(self.window.table.topLevelItemCount())
                            if self.window.table.topLevelItem(index).data(0, Qt.ItemDataRole.UserRole)["id"] == identifier)
                with patch.object(browser.QDesktopServices, "openUrl") as external:
                    self.window._open_item(item, 0)
                    self.wait_for(lambda: not self.window.busy)
                external.assert_not_called()
                row = self.window.selected_rows()[0]
                self.assertEqual((row["id"], row["real_name"]), (identifier, name))
                self.assertEqual(self.window._preview_row["id"], identifier)
                cached = self.window._cached_paths[browser._cache_key(row)]
                self.assertEqual(cached.name, name)
                if name.endswith(".zip"):
                    self.assertEqual(self.window._archive_path, cached)
                    self.assertEqual(self.window.archive_tree.topLevelItem(0).text(0), "signal.txt")
                else:
                    self.assertEqual(self.window.preview.title.text(), name)
                    self.assertFalse(self.window.preview._image.isNull())
        self.warning.assert_not_called()
        self.information.assert_not_called()

    def test_open_reports_attachment_deleted_since_selection(self):
        self.add_preview(151, "deleted.zip", b"unused")
        self.select(151)
        self.window._preview_timer.stop()
        self.store.rows = [row for row in self.store.rows if row["id"] != 151]
        self.window.open_selected()
        self.wait_for(lambda: not self.window.busy)
        self.assertIn("disappeared; refresh", self.warning.call_args.args[2])

    def test_copy_links_uses_clipboard_text_without_downloading_files(self):
        self.select(17, 2)
        self.window.attachment_link_action.trigger()
        self.wait_for(lambda: not self.window.busy)
        self.assertEqual(set(QApplication.clipboard().text().splitlines()), {
            "https://offline.invalid/app/download.php?f=blob-17",
            "https://offline.invalid/app/download.php?f=blob-2",
        })
        self.assertNotIn("download", [operation for operation, _, _ in self.store.calls])
        self.window.record_link_action.trigger()
        self.wait_for(lambda: not self.window.busy)
        self.assertEqual(QApplication.clipboard().text(), "https://offline.invalid/experiments.php?mode=view&id=123")

    def test_selected_attachments_export_as_one_zip_with_exact_contents(self):
        self.store.contents.update({17: b"calibration notes", 2: b"measured signal"})
        self.select(17, 2)
        destination = self.directory / "selected.zip"
        with patch.object(browser.QFileDialog, "getSaveFileName", return_value=(str(destination), "")):
            self.window.zip_action.trigger()
        self.wait_for(lambda: not self.window.busy)
        with ZipFile(destination) as archive:
            self.assertEqual(set(archive.namelist()), {"alpha.txt", "zeta.txt"})
            self.assertEqual(archive.read("zeta.txt"), b"calibration notes")
            self.assertEqual(archive.read("alpha.txt"), b"measured signal")
        self.assertIn("Saved ZIP", self.window.statusBar().currentMessage())

    def test_zip_overwrite_cancellation_preserves_existing_local_file(self):
        self.select(17)
        destination = self.directory / "selected.zip"
        destination.write_bytes(b"keep existing export")
        with patch.object(browser.QFileDialog, "getSaveFileName", return_value=(str(destination), "")):
            self.window.zip_action.trigger()
        self.assertFalse(self.window.busy)
        self.assertEqual(destination.read_bytes(), b"keep existing export")
        self.assertNotIn("download", [operation for operation, _, _ in self.store.calls])

    def test_batch_save_uses_confirmed_paths_even_after_remote_rename(self):
        from elablink._attachment_store import AttachmentStore
        from tests.test_attachment_store import FakeClient

        client = FakeClient()
        client.add("alpha.txt", b"new alpha", ID=2)
        client.add("zeta.txt", b"new zeta", ID=17)
        store = AttachmentStore(None, "experiments", 123, self.directory / "cache", lambda: client)
        self.store.save_files = store.save_files
        self.select(2, 17)
        destination = self.directory / "export"
        destination.mkdir()
        (destination / "alpha.txt").write_bytes(b"old alpha")
        notes = destination / "other-notes.txt"
        notes.write_bytes(b"unrelated notes")

        def approve(*args):
            self.assertIn("alpha.txt", args[2])
            self.assertNotIn("other-notes.txt", args[2])
            client.rows[1].update(name=notes.name, real_name=notes.name)
            return QMessageBox.StandardButton.Yes

        self.question.side_effect = approve
        with patch.object(browser.QFileDialog, "getExistingDirectory", return_value=str(destination)):
            self.window.save_selected()
        self.wait_for(lambda: not self.window.busy)
        self.warning.assert_not_called()
        self.assertEqual((destination / "alpha.txt").read_bytes(), b"new alpha")
        self.assertEqual((destination / "zeta.txt").read_bytes(), b"new zeta")
        self.assertEqual(notes.read_bytes(), b"unrelated notes")

    def test_batch_save_does_not_extend_confirmation_to_a_newly_appearing_file(self):
        from elablink._attachment_store import AttachmentStore
        from tests.test_attachment_store import FakeClient

        client = FakeClient()
        client.add("alpha.txt", b"new alpha", ID=2)
        client.add("zeta.txt", b"new zeta", ID=17)
        store = AttachmentStore(None, "experiments", 123, self.directory / "cache", lambda: client)
        self.store.save_files = store.save_files
        self.select(2, 17)
        destination = self.directory / "export"
        destination.mkdir()
        (destination / "alpha.txt").write_bytes(b"old alpha")

        def approve(*args):
            self.assertIn("alpha.txt", args[2])
            self.assertNotIn("zeta.txt", args[2])
            (destination / "zeta.txt").write_bytes(b"created while confirmation was open")
            return QMessageBox.StandardButton.Yes

        self.question.side_effect = approve
        with patch.object(browser.QFileDialog, "getExistingDirectory", return_value=str(destination)):
            self.window.save_selected()
        self.wait_for(lambda: not self.window.busy)
        self.warning.assert_called_once()
        self.assertEqual(client.reads, [])
        self.assertEqual((destination / "alpha.txt").read_bytes(), b"old alpha")
        self.assertEqual((destination / "zeta.txt").read_bytes(), b"created while confirmation was open")

    def test_open_local_folder_prepares_the_selected_file_then_opens_its_parent(self):
        self.select(17)
        with patch.object(browser.QDesktopServices, "openUrl", return_value=True) as launch:
            self.window.folder_action.trigger()
            self.wait_for(lambda: not self.window.busy)
        directory = Path(launch.call_args.args[0].toLocalFile())
        self.assertEqual(directory, self.directory / "17")
        self.assertTrue((directory / "zeta.txt").is_file())

    def test_cache_dialog_can_be_cancelled_or_cleared_without_redownloading(self):
        self.select(17)
        self.window.copy_selected()
        self.wait_for(lambda: not self.window.busy)
        cached = self.directory / "17/zeta.txt"
        self.window.cache_action.trigger()
        self.wait_for(lambda: not self.window.busy and not self.window._preview_paused)
        self.assertTrue(cached.exists())
        self.assertNotIn("clear_cache", [operation for operation, _, _ in self.store.calls])
        self.assertIn("1 cached files", self.question.call_args.args[2])
        self.question.return_value = QMessageBox.StandardButton.Yes
        self.window.cache_action.trigger()
        self.wait_for(lambda: not self.window.busy and not self.window._preview_paused)
        self.assertFalse(cached.exists())
        self.assertEqual(self.window._cached_paths, {})
        calls = len(self.preview_store.calls)
        QTest.qWait(250)
        self.assertEqual(len(self.preview_store.calls), calls)
        self.assertIn("Cache cleared", self.window.preview.image.text())

    def test_cache_cleanup_waits_for_cancelled_preview_before_deleting(self):
        self.add_preview(99, "beam.png")
        self.preview_store.blocked_operation = "download"
        self.select(99)
        self.wait_for(self.preview_store.started.is_set)
        self.question.return_value = QMessageBox.StandardButton.Yes
        self.window.cache_action.trigger()
        self.assertTrue(self.window._preview_paused)
        self.assertTrue(self.window._preview_cancel.is_set())
        self.assertFalse(self.window.copy_action.isEnabled())
        self.window.copy_selected()  # A direct call must also wait during cleanup.
        self.assertNotIn("clear_cache", [operation for operation, _, _ in self.store.calls])
        self.assertNotIn("download", [operation for operation, _, _ in self.store.calls])
        self.preview_store.release.set()
        self.wait_for(lambda: not self.window._preview_paused and not self.window.busy)
        self.assertIn("clear_cache", [operation for operation, _, _ in self.store.calls])

    def test_slideshow_includes_svg_and_skips_text_files(self):
        self.add_preview(99, "beam.png")
        self.add_preview(122, "settings.json", b'{"wavelength_nm":780}')
        svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100"><rect width="200" height="100" fill="blue"/></svg>'
        self.add_preview(123, "vector.svg", svg)
        self.select(99)
        self.wait_for_preview("beam.png")
        self.window.navigate_preview(1)
        self.wait_for_preview("vector.svg")
        self.assertEqual(self.window._preview_row["id"], 123)

    def test_filter_uses_visible_selection_when_current_image_becomes_hidden(self):
        self.add_preview(99, "beam.png")
        self.add_preview(100, "second.bmp")
        self.window.navigate_preview(1)
        self.wait_for_preview("beam.png")
        self.select(99, 100)
        self.window.search.setText("second")
        self.wait_for_preview("second.bmp")

    def test_returning_to_a_cancelled_preview_queues_a_fresh_request(self):
        self.add_preview(99, "beam.png")
        self.add_preview(100, "second.bmp")
        self.preview_store.blocked_operation = "download"
        self.select(99)
        self.wait_for(self.preview_store.started.is_set)
        self.select(100)
        self.select(99)
        # Let the debounce expire while the cancelled first request still runs.
        QTest.qWait(220)
        self.preview_store.release.set()
        self.wait_for_preview("beam.png")
        self.assertNotEqual(self.window.preview.image.text(), "Transfer cancelled.")

    def test_late_preview_never_replaces_the_latest_selection(self):
        self.add_preview(99, "beam.png")
        self.add_preview(100, "second.bmp")
        self.preview_store.blocked_operation = "download"
        self.select(99)
        self.wait_for(self.preview_store.started.is_set)
        self.select(100)
        self.preview_store.release.set()
        self.wait_for_preview("second.bmp")
        self.assertEqual(self.window._preview_row["id"], 100)
        self.select(17)
        self.assertTrue(self.window.preview._image.isNull())
        self.select(100)
        self.wait_for_preview("second.bmp")

    def test_pdf_preview_page_buttons_render_the_selected_page(self):
        source = self.directory / "figures.pdf"
        writer = QPdfWriter(str(source))
        writer.setResolution(72)
        painter = QPainter(writer)
        painter.fillRect(0, 0, writer.width(), writer.height(), QColor("red"))
        self.assertTrue(writer.newPage())
        painter.fillRect(0, 0, writer.width(), writer.height(), QColor("blue"))
        painter.end()
        del painter, writer
        self.add_preview(110, source.name, source.read_bytes())
        self.select(110)
        self.wait_for_preview(source.name)
        self.assertEqual(self.window.preview.page_label.text(), "1 / 2")
        self.window.preview.next_page.click()
        self.wait_for(lambda: self.window.preview.page_label.text() == "2 / 2")
        image = self.window.preview._image
        color = image.pixelColor(image.width() // 2, image.height() // 2)
        self.assertGreater(color.blue(), color.red())

    def test_zip_contents_and_local_extraction_leave_server_attachments_unchanged(self):
        source = self.directory / "figures.zip"
        with ZipFile(source, "w") as archive:
            archive.writestr("figures/signal.txt", b"synthetic figure data")
            archive.writestr("notes.txt", b"measurement notes")
        self.add_preview(111, source.name, source.read_bytes())
        self.select(111)
        self.wait_for(lambda: self.window._archive_path is not None)
        self.assertIs(self.window.preview_stack.currentWidget(), self.window.archive_panel)
        self.assertEqual(self.window.archive_tree.topLevelItemCount(), 2)
        original = deepcopy(self.store.rows)
        destination = self.directory / "extracted"
        destination.mkdir()
        with patch.object(browser.QFileDialog, "getExistingDirectory", return_value=str(destination)):
            self.window.extract_button.click()
        self.wait_for(lambda: not self.window.busy)
        self.assertEqual((destination / "figures/signal.txt").read_bytes(), b"synthetic figure data")
        self.assertEqual(self.store.rows, original)
        self.assertIn("Extracted 2 files", self.window.statusBar().currentMessage())

    def test_uppercase_gds_opens_in_klayout_instead_of_default_application(self):
        self.add_preview(112, "device.GDS", b"synthetic layout")
        self.select(112)
        process = Mock()
        process.poll.return_value = None
        with (patch.object(browser, "open_klayout", return_value=process) as launch,
              patch.object(browser.QDesktopServices, "openUrl") as default_open):
            self.window.open_selected()
            self.wait_for(lambda: not self.window.busy)
        self.assertEqual(launch.call_args.args[0].name, "device.GDS")
        default_open.assert_not_called()

    def test_preview_error_stays_in_panel_and_close_cancels_pending_download(self):
        self.select(99)  # The original fake PNG is deliberately not a valid image.
        self.wait_for(lambda: "Cannot preview" in self.window.preview.image.text())
        self.warning.assert_not_called()
        self.add_preview(100, "second.bmp")
        self.preview_store.blocked_operation = "download"
        self.preview_store.started.clear()
        self.select(100)
        self.wait_for(self.preview_store.started.is_set)
        self.window.close()
        self.assertTrue(self.window._preview_cancel.is_set())
        self.preview_store.release.set()
        self.wait_for(lambda: not self.window._preview_thread.isRunning())

    def test_switch_from_experiment_to_resource_drains_preview_and_rebinds_workers(self):
        self.preview_store.blocked_operation = "download"
        self.select(17)
        self.wait_for(self.preview_store.started.is_set)
        resource = FakeStore(self.directory / "resource")
        # The record and attachment IDs overlap intentionally.
        resource.title = "Cryostat wiring"
        resource.rows = [{"id": 17, "real_name": "wiring.txt", "filesize": 7}]
        resource.contents[17] = b"diagram"

        with patch.object(browser, "AttachmentStore", return_value=resource) as factory:
            self.window.record_picker.recordSelected.emit("items", 123)
            self.assertTrue(self.window._preview_paused)
            self.assertFalse(self.window.upload_action.isEnabled())
            self.preview_store.release.set()
            self.wait_for(lambda: self.window.heading.text() == "Resource 123 — Cryostat wiring"
                          and not self.window.busy)
            self.assertEqual(self.window.rows, resource.rows)
            self.assertFalse(self.window._cached_paths)
            self.assertFalse(self.window.selected_rows())
            self.select(17)
            self.wait_for(lambda: self.window._preview_ready
                          and self.window.preview.title.text() == "wiring.txt")
            self.assertEqual(self.window.preview.text.toPlainText(), "diagram")
            self.assertEqual(factory.call_count, 2)
            self.assertTrue(all(call.args[1:3] == ("items", 123) for call in factory.call_args_list))
            self.assertFalse(any(call[0] == "upload" for call in resource.calls))

    def test_latest_record_click_wins_while_attachment_list_is_loading(self):
        self.store.blocked_operation = "list"
        self.window.refresh()
        self.wait_for(self.store.started.is_set)
        resource = FakeStore(self.directory / "resource")
        resource.title = "Wiring"
        with patch.object(browser, "AttachmentStore", return_value=resource) as factory:
            self.window.open_record("experiments", 20)
            self.window.open_record("items", 21)
            self.store.release.set()
            self.wait_for(lambda: not self.window.busy and self.window.heading.text() == "Resource 21 — Wiring")
            self.assertEqual([call.args[1:3] for call in factory.call_args_list], [("items", 21)])

    def test_picker_cannot_redirect_an_active_transfer(self):
        self.store.blocked_operation = "upload"
        self.window.upload_paths([self.directory / "local.txt"])
        self.wait_for(self.store.started.is_set)
        self.window.record_picker.recordSelected.emit("items", 123)
        self.assertEqual(self.window._record, ("experiments", 123))
        self.store.release.set()
        self.wait_for(lambda: not self.window.busy)
        self.assertTrue(any(call[0] == "upload" for call in self.store.calls))
        self.assertIn("Experiment 123", self.window.heading.text())

    def test_sorting_and_filtering_keep_the_correct_server_identity(self):
        self.window.table.sortByColumn(2, Qt.SortOrder.AscendingOrder)
        first = self.window.table.topLevelItem(0)
        self.assertEqual(first.data(0, Qt.ItemDataRole.UserRole)["id"], 17)
        self.select(17, 99)
        self.assertEqual({row["id"] for row in self.window.selected_rows()}, {17, 99})
        self.window.search.setText("beam")
        self.assertEqual([row["id"] for row in self.window.selected_rows()], [99])
        self.assertTrue(all(thread != threading.get_ident() for thread in self.factory_threads))
        self.assertTrue(all(thread != threading.get_ident() for _, _, thread in self.store.calls))

    def test_extension_sort_groups_file_types_and_preserves_selected_identities(self):
        self.store.rows.extend([
            {"id": 101, "real_name": "README", "filesize": 20},
            {"id": 102, "real_name": "another.PNG", "filesize": 30},
        ])
        self.window.refresh()
        self.wait_for(lambda: not self.window.busy)
        self.select(17, 102)
        self.window.table.sortByColumn(1, Qt.SortOrder.AscendingOrder)
        self.assertEqual(
            [self.window.table.topLevelItem(index).text(0)
             for index in range(self.window.table.topLevelItemCount())],
            ["README", "another.PNG", "beam.png", "alpha.txt", "zeta.txt"],
        )
        self.assertEqual({row["id"] for row in self.window.selected_rows()}, {17, 102})

    def test_copy_prepares_real_local_files_without_blocking_qt(self):
        self.select(17, 99)
        self.store.blocked_operation = "download"
        self.window.copy_selected()
        self.wait_for(self.store.started.is_set)
        self.assertTrue(self.window.busy)
        events = []
        QTimer.singleShot(0, lambda: events.append("GUI still responds"))
        self.wait_for(lambda: bool(events))
        self.store.release.set()
        self.wait_for(lambda: not self.window.busy)
        urls = self.application.clipboard().mimeData().urls()
        self.assertEqual({Path(url.toLocalFile()).name for url in urls}, {"zeta.txt", "beam.png"})
        self.assertTrue(all(Path(url.toLocalFile()).read_bytes() == b"downloaded content" for url in urls))
        self.window.close()
        self.wait_for(lambda: not self.window._thread.isRunning())
        self.assertTrue(all(Path(url.toLocalFile()).is_file() for url in urls))

    def test_drag_out_prepares_and_exports_within_one_gesture(self):
        self.select(17)
        self.store.blocked_operation = "download"
        events = []
        QTimer.singleShot(20, lambda: events.append("UI responds"))
        QTimer.singleShot(40, self.store.release.set)
        with (patch.object(browser, "QDrag") as drag_type,
              patch.object(QApplication, "mouseButtons", return_value=Qt.MouseButton.LeftButton)):
            self.window.table.startDrag(Qt.DropAction.CopyAction)
            drag = drag_type.return_value
            drag.exec.assert_called_once_with(Qt.DropAction.CopyAction, Qt.DropAction.CopyAction)
            urls = drag.setMimeData.call_args.args[0].urls()
            self.assertEqual(len(urls), 1)
            self.assertTrue(Path(urls[0].toLocalFile()).is_file())
            self.assertEqual(events, ["UI responds"])
            self.assertFalse(self.window.table._dragging)
            self.window.table.startDrag(Qt.DropAction.CopyAction)
            self.assertEqual(drag.exec.call_count, 2)
            self.assertEqual(sum(op == "download" for op, _, _ in self.store.calls), 1)

    def test_drag_starts_from_an_initially_unselected_row(self):
        self.assertEqual(self.window.selected_rows(), [])
        item = next(self.window.table.topLevelItem(index)
                    for index in range(self.window.table.topLevelItemCount())
                    if self.window.table.topLevelItem(index).data(0, Qt.ItemDataRole.UserRole)["id"] == 17)
        point = self.window.table.visualItemRect(item).center()
        viewport = self.window.table.viewport()
        self.store.blocked_operation = "download"
        QTimer.singleShot(40, self.store.release.set)
        with (patch.object(browser, "QDrag") as drag_type,
              patch.object(QApplication, "mouseButtons", return_value=Qt.MouseButton.LeftButton)):
            QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=point)
            destination = QPointF(point + QPoint(QApplication.startDragDistance() + 10, 0))
            move = QMouseEvent(QEvent.Type.MouseMove, destination, destination,
                               Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
                               Qt.KeyboardModifier.NoModifier)
            self.application.sendEvent(viewport, move)
            drag_type.return_value.exec.assert_called_once()
            self.assertEqual([row["id"] for row in self.window.selected_rows()], [17])
            QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=point)

    def test_mouse_release_or_escape_aborts_preparation_without_late_drag(self):
        self.select(17)
        self.store.blocked_operation = "download"

        def released_then_pressed_again():
            viewport = self.window.table.viewport()
            QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))
            QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=QPoint(10, 10))

        QTimer.singleShot(20, released_then_pressed_again)
        with (patch.object(browser, "QDrag") as drag_type,
              patch.object(QApplication, "mouseButtons", return_value=Qt.MouseButton.LeftButton)):
            self.window.table.startDrag(Qt.DropAction.CopyAction)
            self.store.release.set()
            self.wait_for(lambda: not self.window.busy)
            drag_type.assert_not_called()
        self.select(17)
        self.store.release.clear()
        QTimer.singleShot(20, lambda: QTest.keyClick(self.window.table, Qt.Key.Key_Escape))
        with (patch.object(browser, "QDrag") as drag_type,
              patch.object(QApplication, "mouseButtons", return_value=Qt.MouseButton.LeftButton)):
            self.window.table.startDrag(Qt.DropAction.CopyAction)
            self.store.release.set()
            self.wait_for(lambda: not self.window.busy)
            drag_type.assert_not_called()

    def test_failed_preparation_does_not_start_a_drag(self):
        self.select(17)
        with (patch.object(self.store, "download", side_effect=OSError("Connection lost")),
              patch.object(browser, "QDrag") as drag_type,
              patch.object(QApplication, "mouseButtons", return_value=Qt.MouseButton.LeftButton)):
            self.window.table.startDrag(Qt.DropAction.CopyAction)
            drag_type.assert_not_called()
            self.warning.assert_called_once()

    def test_changed_selection_during_preparation_does_not_start_a_drag(self):
        self.select(17)
        self.store.blocked_operation = "download"

        def change_selection():
            self.select(99)
            self.store.release.set()

        QTimer.singleShot(20, change_selection)
        with (patch.object(browser, "QDrag") as drag_type,
              patch.object(QApplication, "mouseButtons", return_value=Qt.MouseButton.LeftButton)):
            self.window.table.startDrag(Qt.DropAction.CopyAction)
            drag_type.assert_not_called()

    def test_closing_during_preparation_does_not_start_a_drag(self):
        self.select(17)
        self.store.blocked_operation = "download"
        self.question.return_value = QMessageBox.StandardButton.Yes
        QTimer.singleShot(20, self.window.close)
        with (patch.object(browser, "QDrag") as drag_type,
              patch.object(QApplication, "mouseButtons", return_value=Qt.MouseButton.LeftButton)):
            self.window.table.startDrag(Qt.DropAction.CopyAction)
            self.store.release.set()
            self.wait_for(lambda: not self.window._thread.isRunning())
            drag_type.assert_not_called()

    def test_worker_can_finish_during_close_confirmation_while_preparing_drag(self):
        self.select(17)
        self.store.blocked_operation = "download"
        original_quit = self.window._thread.quit

        def finish_before_answer(*args):
            self.store.release.set()
            self.wait_for(lambda: not self.window.busy)
            return QMessageBox.StandardButton.Yes

        def finish_thread():
            original_quit()
            self.assertTrue(self.window._thread.wait(1000))

        self.question.side_effect = finish_before_answer
        QTimer.singleShot(20, self.window.close)
        with (patch.object(self.window._thread, "quit", side_effect=finish_thread),
              patch.object(browser, "QDrag") as drag_type,
              patch.object(QApplication, "mouseButtons", return_value=Qt.MouseButton.LeftButton)):
            self.window.table.startDrag(Qt.DropAction.CopyAction)
            drag_type.assert_not_called()
            self.assertIsNone(self.window._drag_wait)

    def test_delete_key_requires_confirmation_and_removes_only_selected_server_rows(self):
        self.select(17, 99)
        self.window.table.setFocus()
        self.application.processEvents()
        QTest.keyClick(self.window.table, Qt.Key.Key_Delete)
        self.question.assert_called_once()
        self.assertFalse(any(op == "delete" for op, _, _ in self.store.calls))
        self.question.return_value = QMessageBox.StandardButton.Yes
        QTest.keyClick(self.window.table, Qt.Key.Key_Delete)
        self.wait_for(lambda: not self.window.busy)
        deleted = next(detail for op, detail, _ in self.store.calls if op == "delete")
        self.assertEqual(set(deleted), {17, 99})
        self.assertEqual([row["id"] for row in self.window.rows], [2])

    def test_delete_in_filter_does_not_delete_attachments(self):
        self.select(17)
        self.window.search.setText("zeta")
        self.window.search.setFocus()
        self.window.search.setCursorPosition(0)
        self.application.processEvents()
        QTest.keyClick(self.window.search, Qt.Key.Key_Delete)
        self.assertEqual(self.window.search.text(), "eta")
        self.question.assert_not_called()

    def test_partial_delete_failure_refreshes_the_actual_remaining_rows(self):
        self.select(17, 99)
        self.store.fail_delete = True
        self.question.return_value = QMessageBox.StandardButton.Yes
        self.window.delete_selected()
        self.wait_for(lambda: not self.window.busy)
        self.assertEqual(len(self.window.rows), 2)
        self.assertEqual(self.window.rows, self.store.rows)
        self.warning.assert_called_once()

    def test_double_click_opens_the_clicked_office_file_in_its_local_application(self):
        self.store.rows[0]["real_name"] = "measurements.xlsx"
        self.window.refresh()
        self.wait_for(lambda: not self.window.busy)
        item = next(self.window.table.topLevelItem(index)
                    for index in range(self.window.table.topLevelItemCount())
                    if self.window.table.topLevelItem(index).data(0, Qt.ItemDataRole.UserRole)["id"] == 17)
        point = self.window.table.visualItemRect(item).center()
        with patch.object(browser.QDesktopServices, "openUrl", return_value=True) as launch:
            QTest.mouseClick(self.window.table.viewport(), Qt.MouseButton.LeftButton, pos=point)
            QTest.mouseDClick(self.window.table.viewport(), Qt.MouseButton.LeftButton, pos=point)
            self.wait_for(lambda: not self.window.busy)
            launch.assert_called_once()
            self.assertEqual(Path(launch.call_args.args[0].toLocalFile()).name, "measurements.xlsx")

    def test_h5_is_highlighted_and_double_click_uses_optional_h5viewer(self):
        self.store.rows[0]["real_name"] = "measurement.h5"
        self.window.refresh()
        self.wait_for(lambda: not self.window.busy)
        item = next(self.window.table.topLevelItem(index)
                    for index in range(self.window.table.topLevelItemCount())
                    if self.window.table.topLevelItem(index).data(0, Qt.ItemDataRole.UserRole)["id"] == 17)
        self.assertTrue(item.font(0).bold())
        self.assertEqual(item.foreground(0).color(), self.window.palette().color(browser.QPalette.ColorRole.Link))
        self.assertIn("h5viewer", item.toolTip(0))
        ordinary = next(self.window.table.topLevelItem(index)
                        for index in range(self.window.table.topLevelItemCount())
                        if self.window.table.topLevelItem(index).data(0, Qt.ItemDataRole.UserRole)["id"] == 2)
        self.assertFalse(ordinary.font(0).bold())
        point = self.window.table.visualItemRect(item).center()
        process = Mock()
        process.poll.return_value = None
        with (patch.object(browser, "open_h5viewer", return_value=process) as launch,
              patch.object(browser.QDesktopServices, "openUrl") as default_open):
            QTest.mouseClick(self.window.table.viewport(), Qt.MouseButton.LeftButton, pos=point)
            QTest.mouseDClick(self.window.table.viewport(), Qt.MouseButton.LeftButton, pos=point)
            self.wait_for(lambda: not self.window.busy)
            launch.assert_called_once()
            self.assertEqual(launch.call_args.args[0].name, "measurement.h5")
            default_open.assert_not_called()

    def test_missing_h5viewer_is_reported_without_breaking_the_browser(self):
        self.store.rows[0]["real_name"] = "measurement.h5"
        self.window.refresh()
        self.wait_for(lambda: not self.window.busy)
        self.select(17)
        with patch.object(browser, "open_h5viewer", side_effect=ImportError("Install compatible h5viewer")):
            self.window.open_selected()
            self.wait_for(lambda: not self.window.busy)
        self.information.assert_called_once()
        self.assertIn("Install compatible h5viewer", self.information.call_args.args[-1])
        self.assertTrue(self.window.refresh_action.isEnabled())

    def test_failed_viewer_startup_is_reported_without_waiting_for_the_process(self):
        process = Mock(browser_log_path=self.directory / "viewer.log")
        process.poll.return_value = 1
        self.window._watch_h5viewer(process)
        self.wait_for(lambda: self.warning.called)
        self.assertIn("viewer.log", self.warning.call_args.args[-1])
        process.wait.assert_not_called()

    def test_file_manager_drop_uploads_multiple_paths_as_copies(self):
        paths = [self.directory / "one.txt", self.directory / "two.txt"]
        for path in paths:
            path.write_text("original", encoding="utf-8")
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
        event = QDropEvent(QPointF(10, 10), Qt.DropAction.CopyAction | Qt.DropAction.MoveAction,
                           mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        event.setDropAction(Qt.DropAction.MoveAction)
        self.window.table.dropEvent(event)
        self.wait_for(lambda: not self.window.busy)
        upload = next(detail for operation, detail, _ in self.store.calls if operation == "upload")
        self.assertEqual(([Path(path) for path in upload[0]], upload[1]), (paths, False))
        self.assertEqual(event.dropAction(), Qt.DropAction.CopyAction)
        self.assertTrue(all(path.read_text(encoding="utf-8") == "original" for path in paths))

    def test_paste_reads_file_urls_and_upload_conflicts_require_confirmation(self):
        path = self.directory / "alpha.txt"
        path.write_text("original", encoding="utf-8")
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(path))])
        self.application.clipboard().setMimeData(mime)
        self.window.paste_files()
        self.question.assert_called_once()
        self.assertFalse(any(operation == "upload" for operation, _, _ in self.store.calls))
        self.question.return_value = QMessageBox.StandardButton.Yes
        self.window.paste_files()
        self.wait_for(lambda: not self.window.busy)
        upload = next(detail for operation, detail, _ in self.store.calls if operation == "upload")
        self.assertEqual(([Path(value) for value in upload[0]], upload[1]), ([path], True))

    def test_rename_is_published_only_after_server_confirmation(self):
        self.select(17)
        self.store.blocked_operation = "rename"
        with patch.object(browser.QInputDialog, "getText", return_value=("renamed.txt", True)):
            self.window.rename_action.trigger()
        self.wait_for(self.store.started.is_set)
        self.assertEqual(self.window.selected_rows()[0]["real_name"], "zeta.txt")
        self.store.release.set()
        self.wait_for(lambda: not self.window.busy)
        self.assertEqual(self.window.selected_rows()[0]["real_name"], "renamed.txt")

    def test_rejected_rename_keeps_current_name(self):
        self.select(17)
        self.store.fail_rename = True
        with patch.object(browser.QInputDialog, "getText", return_value=("renamed.txt", True)):
            self.window.rename_selected()
        self.wait_for(lambda: not self.window.busy)
        self.assertEqual(self.window.selected_rows()[0]["real_name"], "zeta.txt")
        self.warning.assert_called_once()

    def test_open_views_scripts_as_text_and_does_not_launch_programs(self):
        self.store.rows = [
            {"id": 5, "real_name": "analysis.py", "filesize": 18},
            {"id": 6, "real_name": "program.exe", "filesize": 18},
        ]
        self.window.refresh()
        self.wait_for(lambda: not self.window.busy)
        self.select(5, 6)
        with patch.object(browser.QDesktopServices, "openUrl", return_value=True) as launch:
            self.window.open_selected()
            self.wait_for(lambda: not self.window.busy)
        self.assertEqual(launch.call_count, 1)
        self.assertTrue(launch.call_args.args[0].toLocalFile().endswith("analysis.py.txt"))
        self.information.assert_called_once()

    def test_api_error_message_omits_response_headers(self):
        error = Mock(status=403)
        error.__str__ = Mock(return_value="HTTP response headers: API_KEY=private")
        message = browser._error_message(error)
        self.assertIn("403", message)
        self.assertNotIn("API_KEY", message)

    def test_close_finishes_when_transfer_completes_during_confirmation(self):
        self.select(17)
        self.store.blocked_operation = "download"
        self.window.copy_selected()
        self.wait_for(self.store.started.is_set)

        def finish_before_answer(*args):
            self.store.release.set()
            self.wait_for(lambda: not self.window.busy)
            return QMessageBox.StandardButton.Yes

        self.question.side_effect = finish_before_answer
        self.window.close()
        self.wait_for(lambda: not self.window._thread.isRunning())

    def test_cancel_keeps_window_responsive_and_can_be_used_again(self):
        self.select(17)
        self.store.blocked_operation = "download"
        self.window.copy_selected()
        self.wait_for(self.store.started.is_set)
        self.window.cancel_button.click()
        self.assertTrue(self.window._cancel.is_set())
        self.assertFalse(self.window.cancel_button.isEnabled())
        self.store.release.set()
        self.wait_for(lambda: not self.window.busy)
        self.store.started.clear()
        self.store.release.clear()
        self.window.copy_selected()
        self.wait_for(self.store.started.is_set)
        self.assertTrue(self.window.cancel_button.isEnabled())
        self.store.release.set()
        self.wait_for(lambda: not self.window.busy)

    def test_move_only_drop_is_rejected_without_uploading(self):
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(self.directory / "keep.txt"))])
        event = QDropEvent(QPointF(10, 10), Qt.DropAction.MoveAction,
                          mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        self.window.table.dropEvent(event)
        self.assertFalse(event.isAccepted())
        self.assertFalse(any(operation == "upload" for operation, _, _ in self.store.calls))


if __name__ == "__main__":
    unittest.main()
