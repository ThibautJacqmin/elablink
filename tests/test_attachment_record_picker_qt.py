"""Offline interaction tests for the attachment browser's record selector."""

import importlib.util
import os
import threading
import time
import unittest
from unittest.mock import patch


HAS_QT = importlib.util.find_spec("PyQt6") is not None
if HAS_QT:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtCore import QTimer, Qt
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication, QDialogButtonBox, QLineEdit, QListWidget
    from elablink import _attachment_record_picker_qt as picker


@unittest.skipUnless(HAS_QT, "PyQt6 is not installed")
class RecordPickerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.queries = []
        self.started = threading.Event()
        self.release = threading.Event()
        self.client = self.enter_mock("Client", return_value=object())
        choices = ([{"id": 4, "title": "Cryostat"}], [{"id": 1, "title": "Active"}], ["notion-import", "membrane"])
        self.catalog = self.enter_mock("filter_choices", return_value=choices)
        self.enter_mock("search_records", side_effect=self.read)
        self.widget = picker.RecordPicker("offline.conf")
        self.addCleanup(self.widget.close)
        self.addCleanup(self.release.set)

    def enter_mock(self, name, **kwargs):
        replacement = patch.object(picker, name, **kwargs)
        self.addCleanup(replacement.stop)
        return replacement.start()

    def read(self, client, kind, text, categories, statuses, tags, offset, limit):
        self.queries.append((kind, text, categories, statuses, tags, offset))
        if text == "slow":
            self.started.set()
            if not self.release.wait(3):
                raise TimeoutError()
        return [{"id": i + 100, "title": f"{text} <sample>", "category_title": "Cryostat",
                 "category_color": "123abc", "status_title": "Active", "status_color": "eeff22",
                 "tags": ["notion-import"]} for i in range(offset, min(offset + limit, 29))]

    def wait(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertTrue(predicate())

    def test_lazy_record_tabs_and_pagination(self):
        self.widget.set_entity_type("items")
        self.app.processEvents()
        self.client.assert_not_called()
        self.widget.show()
        resources = self.widget.currentWidget()
        self.wait(lambda: resources.results.count() == 25)
        self.assertEqual([row[0] for row in self.queries], ["items"])
        self.assertTrue(resources.next.isEnabled())
        selected = []
        self.widget.recordSelected.connect(lambda kind, value: selected.append((kind, value)))
        resources.next.click()
        self.wait(lambda: resources.results.count() == 4)
        resources.results.setCurrentRow(0)
        self.assertEqual(selected, [])
        resources.open_button.click()
        self.assertEqual(selected, [("items", 125)])
        self.widget.set_entity_type("experiments")
        self.wait(lambda: self.widget.currentWidget().results.count() == 25)
        self.widget.set_entity_type("items")
        self.wait(lambda: resources.results.count() == 4)
        self.assertEqual(self.client.call_count, 2)

    def test_one_mouse_click_opens_either_record_type(self):
        opened = []
        self.widget.recordSelected.connect(lambda kind, value: opened.append((kind, value)))
        self.widget.show()
        for kind in ("experiments", "items"):
            self.widget.set_entity_type(kind)
            results = self.widget.currentWidget().results
            self.wait(lambda: results.count() > 1)
            point = results.visualItemRect(results.item(1)).center()
            QTest.mouseClick(results.viewport(), Qt.MouseButton.LeftButton, pos=point)
            self.assertEqual(opened.pop(), (kind, 101))
            self.assertFalse(opened)

    def test_latest_query_and_combined_filtered_tags(self):
        self.widget.show()
        page = self.widget.currentWidget()
        self.wait(lambda: page.results.count() == 25)
        page.search.setText("slow")
        self.wait(self.started.is_set)
        page.search.setText("MPDRIE")
        self.release.set()
        self.wait(lambda: page.results.count() == 25 and self.queries[-1][1] == "MPDRIE")

        def accept_tags():
            dialog = self.app.activeModalWidget()
            listing = dialog.findChild(QListWidget)
            listing.item(0).setCheckState(Qt.CheckState.Checked)
            dialog.findChild(QLineEdit).setText("membrane")
            listing.item(1).setCheckState(Qt.CheckState.Checked)
            dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok).click()

        QTimer.singleShot(0, accept_tags)
        page.tags.click()
        self.wait(lambda: page.results.count() == 25 and len(self.queries[-1][4]) == 2)
        self.assertEqual(self.queries[-1][4], ["notion-import", "membrane"])
        self.assertEqual(self.queries[-1][1], "MPDRIE")
        row = page.results.itemWidget(page.results.item(0)).text()
        self.assertIn("&lt;sample&gt;", row)
        self.assertIn("#123abc", row)
        self.assertIn("#eeff22", row)
        self.assertEqual(self.client.call_count, 1)

    def test_reopening_and_refresh_reconcile_team_choices(self):
        self.widget.show()
        page = self.widget.currentWidget()
        self.wait(lambda: page.results.count() == 25)
        page.category._set_selected([4])
        page.status._set_selected([1])
        page.tags._set_selected(["notion-import"])
        page.search.setText("same search")
        self.wait(lambda: page.results.count() == 25 and self.queries[-1][1] == "same search")
        self.assertEqual(self.catalog.call_count, 1)

        self.catalog.return_value = ([{"id": 4, "title": "New name", "color": "bc0011"}],
                                     [{"id": 1, "title": "In progress", "color": "bbaaff"}],
                                     ["membrane", "notion-import"])
        self.widget.hide()
        self.widget.show()
        self.wait(lambda: page.results.count() == 25 and "New name" in page.category.toolTip())
        self.assertEqual(self.queries[-1][2:5], ([4], [1], ["notion-import"]))
        self.assertEqual(page.status.toolTip(), "Status: In progress")
        self.assertNotEqual(page.status.text(), "Status")
        self.assertFalse(page.category.icon().isNull())

        self.catalog.return_value = ([], [{"id": 1, "title": "In progress"}], ["membrane"])
        page.refresh.click()
        self.wait(lambda: page.results.count() == 25 and self.queries[-1][2:5] == ([], [1], []))
        self.assertEqual(self.catalog.call_count, 3)
        self.assertEqual(page.category.text(), "Category")
        self.assertEqual(page.tags.choices, [("membrane", "membrane", None)])
        self.assertEqual(page.search.text(), "same search")
