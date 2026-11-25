"""Offline decoding and display checks for the attachment preview panel."""

import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


HAS_QT = importlib.util.find_spec("PyQt6") is not None
HAS_PDF = importlib.util.find_spec("pypdfium2") is not None
if HAS_QT:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtCore import QPoint, QPointF, QRect, Qt
    from PyQt6.QtGui import QAction, QColor, QImage, QKeySequence, QMouseEvent, QPainter, QPdfWriter, QWheelEvent
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication
    from elablink._attachment_preview_qt import PreviewPanel, load_preview


@unittest.skipUnless(HAS_QT, "PyQt6 is not installed")
class AttachmentPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setQuitOnLastWindowClosed(False)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def image_file(self, suffix):
        path = self.directory / f"image{suffix}"
        image = QImage(400, 200, QImage.Format.Format_RGB32)
        image.fill(QColor("red"))
        self.assertTrue(image.save(str(path)))
        return path

    def pdf_file(self):
        path = self.directory / "figures.pdf"
        writer = QPdfWriter(str(path))
        writer.setResolution(72)
        painter = QPainter(writer)
        painter.fillRect(0, 0, writer.width(), writer.height(), QColor("red"))
        self.assertTrue(writer.newPage())
        painter.fillRect(0, 0, writer.width(), writer.height(), QColor("blue"))
        painter.end()
        del writer
        return path

    def test_png_jpeg_bmp_decode_with_bounded_size(self):
        for suffix in (".png", ".jpg", ".bmp"):
            with self.subTest(suffix=suffix):
                result = load_preview(self.image_file(suffix), max_size=100)
                self.assertLessEqual(max(result["image"].width(), result["image"].height()), 100)
                self.assertEqual(result["pages"], 1)
                self.assertGreaterEqual(result["image"].pixelColor(0, 0).red(), 250)

    def test_corrupt_image_and_unsupported_file_fail_clearly(self):
        corrupt = self.directory / "broken.png"
        corrupt.write_bytes(b"This is not a PNG")
        with self.assertRaisesRegex(ValueError, "Cannot preview broken.png"):
            load_preview(corrupt)
        with self.assertRaisesRegex(ValueError, "file type"):
            load_preview(self.directory / "data.bin")

    def test_large_svg_is_rendered_to_a_bounded_image(self):
        path = self.directory / "figure.SVG"
        path.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="10000000" height="5000000">'
            '<rect width="10000000" height="5000000" fill="red"/></svg>', encoding="utf-8",
        )
        result = load_preview(path, max_size=100)
        self.assertEqual((result["image"].width(), result["image"].height()), (100, 50))
        self.assertEqual(result["image"].pixelColor(50, 25).red(), 255)
        path.unlink()  # The renderer must release the file, including on Windows.

    def test_text_preview_is_literal_read_only_and_switches_back_to_images(self):
        path = self.directory / "notes.xml"
        source = '<experiment><b>literal markup</b><value>12</value></experiment>'
        path.write_text(source, encoding="utf-8")
        panel = PreviewPanel()
        self.addCleanup(panel.close)
        panel.show_preview(load_preview(path))
        self.assertIs(panel.content.currentWidget(), panel.text)
        self.assertEqual(panel.text.toPlainText(), source)
        self.assertTrue(panel.text.isReadOnly())
        QTest.keyClicks(panel.text, "cannot edit")
        self.assertEqual(panel.text.toPlainText(), source)
        self.assertFalse(panel.page_controls.isVisible())
        panel.show_preview(load_preview(self.image_file(".png")))
        self.assertIs(panel.content.currentWidget(), panel.image_scroll)
        self.assertEqual(panel.text.toPlainText(), "")
        self.assertFalse(panel._image.isNull())
        panel.show_message("Loading", "other.txt")
        self.assertTrue(panel._image.isNull())
        self.assertEqual(panel.image.text(), "Loading")

    def test_shortened_text_has_a_notice_and_does_not_change_the_original(self):
        path = self.directory / "measurements.csv"
        source = "a,b,c\n" * 5000
        path.write_text(source, encoding="utf-8")
        panel = PreviewPanel()
        self.addCleanup(panel.close)
        panel.show_preview(load_preview(path))
        self.assertIn("Preview shortened", panel.notice.text())
        self.assertLess(len(panel.text.toPlainText()), len(source))
        self.assertEqual(path.read_text(encoding="utf-8"), source)

    def test_missing_pdf_dependency_has_install_message(self):
        path = self.pdf_file()
        with patch.dict("sys.modules", {"pypdfium2": None}):
            with self.assertRaisesRegex(ImportError, "PDF previews require pypdfium2"):
                load_preview(path)

    @unittest.skipUnless(HAS_PDF, "Optional PDF renderer is not installed")
    def test_pdf_pages_decode_and_release_the_source_file(self):
        path = self.pdf_file()
        first = load_preview(path, max_size=200)
        second = load_preview(path, page=1, max_size=200)
        self.assertEqual(first["pages"], 2)
        self.assertEqual(second["page"], 1)
        self.assertFalse(first["image"].isNull())
        self.assertFalse(second["image"].isNull())
        first_color = first["image"].pixelColor(first["image"].width() // 2, first["image"].height() // 2)
        second_color = second["image"].pixelColor(second["image"].width() // 2, second["image"].height() // 2)
        self.assertGreater(first_color.red(), first_color.blue())
        self.assertGreater(second_color.blue(), second_color.red())
        self.assertLessEqual(max(second["image"].width(), second["image"].height()), 200)
        with self.assertRaisesRegex(ValueError, "does not exist"):
            load_preview(path, page=2)
        path.unlink()
        self.assertFalse(path.exists())

    def test_panel_resizes_image_and_uses_page_and_navigation_signals(self):
        panel = PreviewPanel()
        self.addCleanup(panel.close)
        panel.resize(400, 350)
        panel.show()
        result = load_preview(self.image_file(".png"))
        result.update(page=0, pages=2)
        pages, navigation = [], []
        panel.page_requested.connect(pages.append)
        panel.navigate.connect(navigation.append)
        panel.show_preview(result)
        self.application.processEvents()
        self.assertTrue(panel.page_controls.isVisible())
        self.assertFalse(panel.previous_page.isEnabled())
        QTest.mouseClick(panel.next_page, Qt.MouseButton.LeftButton)
        QTest.keyClick(panel, Qt.Key.Key_Right)
        QTest.keyClick(panel, Qt.Key.Key_Left)
        self.assertEqual(pages, [1])
        self.assertEqual(navigation, [1, -1])
        before = panel.image.pixmap().size()
        panel.resize(600, 450)
        self.application.processEvents()
        self.assertGreater(panel.image.pixmap().width(), before.width())
        panel.show_message("Loading", "another.pdf")
        self.assertEqual(panel.title.text(), "another.pdf")
        self.assertEqual(panel.image.text(), "Loading")
        self.assertTrue(panel.image.pixmap().isNull())
        self.assertFalse(panel.page_controls.isVisible())

    def test_image_zoom_wheel_and_fit_keep_the_image_scrollable(self):
        panel = PreviewPanel()
        self.addCleanup(panel.close)
        panel.resize(450, 400)
        panel.show()
        panel.show_preview(load_preview(self.image_file(".png")))
        self.application.processEvents()
        original = panel.image.pixmap().size()
        self.assertTrue(panel._fit_mode)
        panel.zoom_in.click()
        self.assertFalse(panel._fit_mode)
        self.assertGreater(panel.image.pixmap().width(), original.width())
        panel.zoom_image(2)
        self.application.processEvents()
        self.assertGreater(panel.image_scroll.horizontalScrollBar().maximum(), 0)
        viewport = panel.image_scroll.viewport()
        position = QPointF(viewport.rect().center())
        wheel = QWheelEvent(
            position, QPointF(viewport.mapToGlobal(position.toPoint())), QPoint(), QPoint(0, 120),
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False,
        )
        previous = panel.image.pixmap().width()
        QApplication.sendEvent(viewport, wheel)
        self.assertGreater(panel.image.pixmap().width(), previous)
        self.assertAlmostEqual(panel.image.width() / panel.image.height(), 2.0, places=1)
        panel.fit_button.click()
        self.application.processEvents()
        self.assertTrue(panel._fit_mode)
        self.assertEqual(panel.image_scroll.horizontalScrollBar().maximum(), 0)
        self.assertEqual(panel.image_scroll.verticalScrollBar().maximum(), 0)
        self.assertLessEqual(panel.image.width(), viewport.width())

    def test_zoomed_image_can_be_panned_and_new_image_resets_to_fit(self):
        panel = PreviewPanel()
        self.addCleanup(panel.close)
        panel.resize(450, 400)
        panel.show()
        result = load_preview(self.image_file(".png"))
        panel.show_preview(result)
        self.application.processEvents()
        panel.zoom_image(3)
        self.application.processEvents()
        viewport = panel.image_scroll.viewport()
        bar = panel.image_scroll.horizontalScrollBar()
        bar.setValue(bar.maximum() // 2)
        original = bar.value()
        center = viewport.rect().center()
        panel.pan_button.click()
        QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=center)
        moved = center + QPoint(30, 0)
        event = QMouseEvent(
            QMouseEvent.Type.MouseMove, QPointF(moved), QPointF(viewport.mapToGlobal(moved)),
            Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(viewport, event)
        QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=moved)
        self.assertEqual(bar.value(), original - 30)
        panel.show_preview(result)
        self.application.processEvents()
        self.assertTrue(panel._fit_mode)
        self.assertEqual(bar.maximum(), 0)
        panel.zoom_image(100000)
        self.assertLessEqual(max(panel.image.width(), panel.image.height()), 4096)

    def rectangle_panel(self):
        panel = PreviewPanel()
        self.addCleanup(panel.close)
        panel.resize(600, 600)
        panel.show()
        panel.activateWindow()
        panel.show_preview(load_preview(self.image_file(".png")))
        self.application.processEvents()
        return panel

    def draw_rectangle(self, panel, start, end):
        viewport = panel.image_scroll.viewport()
        QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=start)
        move = QMouseEvent(
            QMouseEvent.Type.MouseMove, QPointF(end), QPointF(viewport.mapToGlobal(end)),
            Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(viewport, move)
        QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=end)
        self.application.processEvents()

    def test_rectangle_zoom_centers_selected_region_with_letterboxing_and_reverse_drag(self):
        panel = self.rectangle_panel()
        viewport = panel.image_scroll.viewport()
        for reverse in (False, True):
            panel.fit_button.click()
            self.application.processEvents()
            origin = panel.image.mapTo(viewport, QPoint())
            self.assertGreater(origin.y(), 0)
            start = origin + QPoint(round(panel.image.width() * 0.55), round(panel.image.height() * 0.35))
            end = origin + QPoint(round(panel.image.width() * 0.75), round(panel.image.height() * 0.65))
            center = QRect(start, end).center()
            relative_x = (center.x() - origin.x()) / panel.image.width()
            relative_y = (center.y() - origin.y()) / panel.image.height()
            previous = panel._scale
            self.draw_rectangle(panel, end if reverse else start, start if reverse else end)
            self.assertGreater(panel._scale, previous)
            self.assertFalse(panel._fit_mode)
            self.assertFalse(panel.image_scroll.rubber_band.isVisible())
            new_origin = panel.image.mapTo(viewport, QPoint())
            self.assertAlmostEqual(new_origin.x() + relative_x * panel.image.width(), viewport.rect().center().x(), delta=2)
            self.assertAlmostEqual(new_origin.y() + relative_y * panel.image.height(), viewport.rect().center().y(), delta=2)

    def test_rectangle_zoom_uses_current_scroll_offsets(self):
        panel = self.rectangle_panel()
        panel.zoom_image(3)
        self.application.processEvents()
        viewport = panel.image_scroll.viewport()
        panel.image_scroll.horizontalScrollBar().setValue(300)
        panel.image_scroll.verticalScrollBar().setValue(150)
        origin = panel.image.mapTo(viewport, QPoint())
        center = viewport.rect().center()
        start, end = center - QPoint(50, 40), center + QPoint(50, 40)
        relative_x = (center.x() - origin.x()) / panel.image.width()
        relative_y = (center.y() - origin.y()) / panel.image.height()
        self.draw_rectangle(panel, start, end)
        new_origin = panel.image.mapTo(viewport, QPoint())
        self.assertAlmostEqual(new_origin.x() + relative_x * panel.image.width(), viewport.rect().center().x(), delta=2)
        self.assertAlmostEqual(new_origin.y() + relative_y * panel.image.height(), viewport.rect().center().y(), delta=2)

    def test_margin_click_and_tiny_rectangle_do_not_change_zoom(self):
        panel = self.rectangle_panel()
        viewport = panel.image_scroll.viewport()
        previous = panel._scale
        center = viewport.rect().center()
        self.draw_rectangle(panel, QPoint(center.x(), 0), center)
        self.assertEqual(panel._scale, previous)
        self.draw_rectangle(panel, center, center + QPoint(2, 2))
        self.assertEqual(panel._scale, previous)
        self.assertTrue(panel._fit_mode)

    def test_escape_fit_and_new_preview_cancel_a_rectangle(self):
        panel = self.rectangle_panel()
        viewport = panel.image_scroll.viewport()
        center = viewport.rect().center()
        panel.fit_button.setFocus()
        self.application.processEvents()
        previous = panel._scale
        QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=center)
        self.assertTrue(panel.image_scroll.hasFocus())
        QTest.keyClick(panel.image_scroll, Qt.Key.Key_Escape)
        QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=center + QPoint(50, 30))
        self.assertEqual(panel._scale, previous)
        self.assertFalse(panel.image_scroll.rubber_band.isVisible())
        QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=center)
        panel.fit_image()
        self.assertIsNone(panel.image_scroll._selection_start)
        self.assertFalse(panel.image_scroll.rubber_band.isVisible())
        QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=center)
        panel.show_message("Loading another figure")
        self.assertIsNone(panel.image_scroll._selection_start)
        self.assertFalse(panel.image_scroll.rubber_band.isVisible())

    def test_middle_button_pans_without_changing_rectangle_zoom_mode(self):
        panel = self.rectangle_panel()
        panel.zoom_image(3)
        self.application.processEvents()
        viewport = panel.image_scroll.viewport()
        bar = panel.image_scroll.horizontalScrollBar()
        bar.setValue(bar.maximum() // 2)
        previous = bar.value()
        center = viewport.rect().center()
        QTest.mousePress(viewport, Qt.MouseButton.MiddleButton, pos=center)
        moved = center + QPoint(30, 0)
        move = QMouseEvent(
            QMouseEvent.Type.MouseMove, QPointF(moved), QPointF(viewport.mapToGlobal(moved)),
            Qt.MouseButton.NoButton, Qt.MouseButton.MiddleButton, Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(viewport, move)
        QTest.mouseRelease(viewport, Qt.MouseButton.MiddleButton, pos=moved)
        self.assertEqual(bar.value(), previous - 30)
        self.assertTrue(panel.area_zoom_button.isChecked())
        self.assertFalse(panel.pan_button.isChecked())

    def test_ctrl_f_search_navigates_text_without_editing_or_parsing(self):
        panel = PreviewPanel()
        self.addCleanup(panel.close)
        panel.resize(650, 400)
        panel.show()
        source = "first signal\n<b>signal</b>\nlast signal\n"
        panel.show_preview({"text": source, "name": "analysis.ipynb", "encoding": "utf-8"})
        find = QAction(panel)
        find.setShortcut(QKeySequence.StandardKey.Find)
        find.triggered.connect(panel.show_search)
        panel.addAction(find)
        panel.activateWindow()
        panel.text.setFocus()
        self.application.processEvents()
        QTest.keyClick(panel.text, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
        self.application.processEvents()
        self.assertTrue(panel.search_bar.isVisible())
        QTest.keyClicks(panel.search_input, "signal")
        self.assertEqual(panel.text.textCursor().selectedText(), "signal")
        first = panel.text.textCursor().selectionStart()
        panel.search_next.click()
        self.assertGreater(panel.text.textCursor().selectionStart(), first)
        panel.search_previous.click()
        self.assertEqual(panel.text.textCursor().selectionStart(), first)
        QTest.keyClick(panel.search_input, Qt.Key.Key_Return)
        second = panel.text.textCursor().selectionStart()
        self.assertGreater(second, first)
        QTest.keyClick(panel.search_input, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(panel.text.textCursor().selectionStart(), first)
        QTest.keyClick(panel.search_input, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
        self.assertGreater(panel.text.textCursor().selectionStart(), second)
        panel.search_input.setText("not present")
        self.assertEqual(panel.search_status.text(), "Not found")
        QTest.keyClick(panel.search_input, Qt.Key.Key_Escape)
        self.assertFalse(panel.search_bar.isVisible())
        QTest.keyClicks(panel.text, "cannot edit")
        self.assertEqual(panel.text.toPlainText(), source)
        panel.show_preview(load_preview(self.image_file(".png")))
        panel.show_search()
        self.assertFalse(panel.search_bar.isVisible())
        self.assertTrue(panel.zoom_controls.isVisible())


if __name__ == "__main__":
    unittest.main()
