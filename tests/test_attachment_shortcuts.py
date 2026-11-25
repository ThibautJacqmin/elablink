"""Offline keyboard and toolbar behaviour in the attachment browser."""

import importlib.util
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from zipfile import ZipFile


HAS_QT = importlib.util.find_spec("PyQt6") is not None
if HAS_QT:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QKeySequence
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication, QLabel, QMessageBox, QTextBrowser
    import elablink._attachment_browser_qt as browser
    from tests import test_attachment_browser_qt as browser_tests


@unittest.skipUnless(HAS_QT, "PyQt6 is not installed")
class AttachmentShortcutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setQuitOnLastWindowClosed(False)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.store = browser_tests.FakeStore(self.directory)
        self.preview_store = browser_tests.FakeStore(self.directory / "previews")
        for name in ("warning", "information", "question"):
            patcher = patch.object(QMessageBox, name, return_value=QMessageBox.StandardButton.Cancel)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)
        self.window = browser.AttachmentBrowser(
            "unused.conf", "experiments", 123,
            store_factory=lambda: self.store, preview_store_factory=lambda: self.preview_store,
        )
        self.addCleanup(self.close_window)
        self.window.show()
        self.window.activateWindow()
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

    def select(self, identifier=2):
        self.window.table.clearSelection()
        for index in range(self.window.table.topLevelItemCount()):
            item = self.window.table.topLevelItem(index)
            if item.data(0, Qt.ItemDataRole.UserRole)["id"] == identifier:
                self.window.table.setCurrentItem(item)
                item.setSelected(True)
                return item
        self.fail("Attachment not found")

    def focus(self, widget):
        self.window.activateWindow()
        widget.setFocus()
        self.application.processEvents()

    def key(self, widget, key, modifiers=None):
        self.focus(widget)
        QTest.keyClick(widget, key, modifiers or Qt.KeyboardModifier.NoModifier)
        self.application.processEvents()

    def text_preview(self):
        self.select()
        self.wait_for(lambda: self.window.preview.text.toPlainText() == "downloaded content"
                      and not self.window._preview_busy)

    def guide_text(self):
        texts = [widget.text() for widget in self.window.shortcuts_panel.findChildren(QLabel)]
        texts.extend(widget.toPlainText() for widget in self.window.shortcuts_panel.findChildren(QTextBrowser))
        return "\n".join(texts)

    def test_toolbar_rows_use_icons_with_tooltips_and_adjacent_shortcut_help(self):
        self.assertTrue(self.window.toolBarBreak(self.window.tools_toolbar))
        actions = [action for action in self.window.tools_toolbar.actions() if action.text()]
        self.assertEqual(len(actions), 10)
        for toolbar in (self.window.attachments_toolbar, self.window.tools_toolbar):
            self.assertEqual(toolbar.toolButtonStyle(), Qt.ToolButtonStyle.ToolButtonIconOnly)
            self.assertTrue(all(action.isSeparator() or action.text() for action in toolbar.actions()))
        primary = [action for action in self.window.attachments_toolbar.actions() if action.text()]
        self.assertNotIn("More", [action.text() for action in primary])
        for action in (self.window.rename_action, self.window.delete_action):
            self.assertNotIn(action, self.window.attachments_toolbar.actions())
            self.assertIn(action, self.window.table.actions())
            self.assertTrue(action.isVisible())
        previous = self.window.tools_toolbar.widgetForAction(self.window.refresh_local_action)
        help_button = self.window.tools_toolbar.widgetForAction(self.window.shortcuts_action)
        self.assertLess(help_button.geometry().left() - previous.geometry().right(), 30)
        actions += primary
        for action in actions:
            with self.subTest(action=action.text()):
                self.assertFalse(action.icon().isNull())
                self.assertTrue(action.toolTip())
                if not action.shortcut().isEmpty():
                    self.assertIn(action.shortcut().toString(QKeySequence.SequenceFormat.NativeText), action.toolTip())

    def test_f1_shows_shortcuts_on_right_and_returns_to_current_preview(self):
        self.text_preview()
        calls = list(self.store.calls), list(self.preview_store.calls)
        self.key(self.window.search, Qt.Key.Key_F1)
        self.assertIs(self.window.preview_stack.currentWidget(), self.window.shortcuts_panel)
        self.assertTrue(self.window.shortcuts_action.isChecked())
        guide = self.guide_text()
        for action in (self.window.upload_action, self.window.download_action, self.window.shortcuts_action,
                       self.window.rename_action, self.window.delete_action, self.window.attachment_link_action):
            self.assertIn(action.text().replace("…", ""), guide)
            self.assertIn(action.shortcut().toString(QKeySequence.SequenceFormat.NativeText), guide)
        self.assertFalse(self.window.find_action.isEnabled())
        self.key(self.window.search, Qt.Key.Key_F1)
        self.wait_for(lambda: self.window.preview_stack.currentWidget() is self.window.preview)
        self.assertFalse(self.window.shortcuts_action.isChecked())
        self.assertEqual(self.window.preview.text.toPlainText(), "downloaded content")
        self.assertTrue(self.window.find_action.isEnabled())
        self.assertEqual(calls, (self.store.calls, self.preview_store.calls))

    def test_clicking_same_attachment_returns_from_help_to_preview(self):
        self.text_preview()
        self.window.shortcuts_action.trigger()
        self.assertIs(self.window.preview_stack.currentWidget(), self.window.shortcuts_panel)
        item = self.window.table.currentItem()
        QTest.mouseClick(self.window.table.viewport(), Qt.MouseButton.LeftButton,
                         pos=self.window.table.visualItemRect(item).center())
        self.application.processEvents()
        self.assertIs(self.window.preview_stack.currentWidget(), self.window.preview)
        self.assertFalse(self.window.shortcuts_action.isChecked())

    def test_delayed_preview_cannot_replace_visible_shortcut_guide(self):
        self.preview_store.blocked_operation = "download"
        self.select()
        self.wait_for(self.preview_store.started.is_set)
        self.key(self.window.table, Qt.Key.Key_F1)
        self.preview_store.release.set()
        self.wait_for(lambda: not self.window._preview_busy)
        QTest.qWait(230)
        self.assertIs(self.window.preview_stack.currentWidget(), self.window.shortcuts_panel)
        self.assertTrue(self.window.shortcuts_action.isChecked())
        self.key(self.window.table, Qt.Key.Key_F1)
        self.wait_for(lambda: self.window.preview_stack.currentWidget() is self.window.preview
                      and self.window.preview.text.toPlainText() == "downloaded content")

    def test_delayed_zip_preview_keeps_help_visible_and_returns_to_archive(self):
        source = self.directory / "fixture.zip"
        with ZipFile(source, "w") as archive:
            archive.writestr("results/notes.txt", "laser calibration")
        contents = source.read_bytes()
        for store in (self.store, self.preview_store):
            row = next(row for row in store.rows if row["id"] == 2)
            row.update(real_name="results.zip", filesize=len(contents))
            store.contents[2] = contents
        self.window.refresh()
        self.wait_for(lambda: not self.window.busy)
        self.preview_store.blocked_operation = "download"
        self.select()
        self.wait_for(self.preview_store.started.is_set)
        self.key(self.window.table, Qt.Key.Key_F1)
        self.preview_store.release.set()
        self.wait_for(lambda: not self.window._preview_busy and self.window._archive_path is not None)
        self.assertIs(self.window.preview_stack.currentWidget(), self.window.shortcuts_panel)
        self.assertTrue(self.window.shortcuts_action.isChecked())
        self.key(self.window.table, Qt.Key.Key_F1)
        self.assertIs(self.window.preview_stack.currentWidget(), self.window.archive_panel)
        self.assertFalse(self.window.shortcuts_action.isChecked())
        self.assertIn("results.zip", self.window.archive_title.text())
        folder = self.window.archive_tree.topLevelItem(0)
        self.assertEqual(folder.text(0), "results")
        self.assertEqual(folder.child(0).text(0), "notes.txt")

    def test_f5_refresh_keeps_help_filter_and_selected_attachment(self):
        self.text_preview()
        self.window.search.setText("alpha")
        self.key(self.window.search, Qt.Key.Key_F1)
        self.store.blocked_operation = "list"
        self.key(self.window.search, Qt.Key.Key_F5)
        self.wait_for(self.store.started.is_set)
        self.assertTrue(self.window.busy)
        self.assertIs(self.window.preview_stack.currentWidget(), self.window.shortcuts_panel)
        self.store.release.set()
        self.wait_for(lambda: not self.window.busy)
        QTest.qWait(230)
        self.assertIs(self.window.preview_stack.currentWidget(), self.window.shortcuts_panel)
        self.assertTrue(self.window.shortcuts_action.isChecked())
        self.assertEqual(self.window.search.text(), "alpha")
        self.assertEqual([row["id"] for row in self.window.selected_rows()], [2])
        self.assertEqual(self.window.table.currentItem().text(0), "alpha.txt")

    def test_filter_copy_paste_and_delete_keep_normal_text_behaviour(self):
        self.text_preview()
        self.window.search.setText("alpha")
        self.window.search.selectAll()
        self.key(self.window.search, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(QApplication.clipboard().text(), "alpha")
        QApplication.clipboard().setText("zeta")
        self.key(self.window.search, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.window.search.text(), "zeta")
        self.window.search.selectAll()
        self.key(self.window.search, Qt.Key.Key_Delete)
        self.assertEqual(self.window.search.text(), "")
        self.question.assert_not_called()
        self.assertFalse(any(operation in {"upload", "delete"} for operation, _, _ in self.store.calls))

    def test_copy_in_text_preview_copies_text_without_downloading_files(self):
        self.text_preview()
        before = len(self.store.calls)
        self.window.preview.text.selectAll()
        self.key(self.window.preview.text, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(QApplication.clipboard().text(), "downloaded content")
        self.assertFalse(QApplication.clipboard().mimeData().hasUrls())
        self.assertEqual(len(self.store.calls), before)

    def test_enter_and_keypad_enter_open_selected_attachment_once_each(self):
        self.text_preview()
        with patch.object(browser.QDesktopServices, "openUrl", return_value=True) as open_url:
            for expected, key in enumerate((Qt.Key.Key_Return, Qt.Key.Key_Enter), start=1):
                self.key(self.window.table, key)
                self.wait_for(lambda: not self.window.busy and open_url.call_count >= expected)
                self.assertEqual(open_url.call_count, expected)
        self.assertEqual(sum(operation == "download" for operation, _, _ in self.store.calls), 2)

    def test_save_is_scoped_to_list_and_upload_is_available_from_filter(self):
        self.text_preview()
        with patch.object(browser.QFileDialog, "getSaveFileName", return_value=("", "")) as save:
            self.key(self.window.search, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
            save.assert_not_called()
            self.key(self.window.table, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
            save.assert_called_once()
        with patch.object(browser.QFileDialog, "getOpenFileNames", return_value=([], "")) as upload:
            self.key(self.window.search, Qt.Key.Key_U, Qt.KeyboardModifier.ControlModifier)
            upload.assert_called_once()

    def test_link_shortcuts_copy_attachment_and_record_urls(self):
        self.text_preview()
        modifiers = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
        self.key(self.window.table, Qt.Key.Key_C, modifiers)
        self.wait_for(lambda: not self.window.busy and "blob-2" in QApplication.clipboard().text())
        self.key(self.window.search, Qt.Key.Key_L, modifiers)
        self.wait_for(lambda: not self.window.busy and "experiments.php" in QApplication.clipboard().text())
        self.assertEqual(sum(operation == "attachment_links" for operation, _, _ in self.store.calls), 1)
        self.assertEqual(sum(operation == "record_link" for operation, _, _ in self.store.calls), 1)

    def test_help_remains_available_during_transfer_without_enabling_file_actions(self):
        self.text_preview()
        self.store.blocked_operation = "download"
        self.window.copy_selected()
        self.wait_for(self.store.started.is_set)
        self.key(self.window.table, Qt.Key.Key_F1)
        self.assertIs(self.window.preview_stack.currentWidget(), self.window.shortcuts_panel)
        for action in (self.window.open_action, self.window.delete_action, self.window.copy_action,
                       self.window.upload_action, self.window.zip_action):
            self.assertFalse(action.isEnabled())
        self.store.release.set()
        self.wait_for(lambda: not self.window.busy)
        self.assertIs(self.window.preview_stack.currentWidget(), self.window.shortcuts_panel)
        self.assertTrue(self.window.open_action.isEnabled())
