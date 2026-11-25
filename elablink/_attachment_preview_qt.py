"""Local image, PDF and text previews for the attachment browser.

@author Thibaut Jacqmin
"""

from pathlib import Path

from PyQt6.QtCore import QEvent, QPoint, QRect, QSize, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QFontDatabase, QImage, QImageReader, QPixmap, QTextCursor, QTextDocument
from PyQt6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QRubberBand, QScrollArea,
    QSizePolicy, QStackedWidget, QStyle, QVBoxLayout, QWidget,
)

from ._attachment_text import TEXT_PREVIEW_SUFFIXES, read_text_preview


IMAGE_PREVIEW_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".webp", ".pdf", ".svg"}
PREVIEW_SUFFIXES = IMAGE_PREVIEW_SUFFIXES | TEXT_PREVIEW_SUFFIXES


def load_preview(path, page=0, max_size=1800):
    """Decode a bounded preview on the browser's serial preview worker.

    QImage can cross the thread boundary; QPixmap is created by the widget only.
    PDF documents and their page buffers are released before this returns.
    """
    path = Path(path)
    if path.suffix.lower() not in PREVIEW_SUFFIXES:
        raise ValueError("No preview is available for this file type.")
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.suffix.lower() in TEXT_PREVIEW_SUFFIXES:
        return read_text_preview(path)
    if path.suffix.lower() == ".pdf":
        image, page, pages = _read_pdf(path, page, max_size)
    else:
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        size = reader.size()
        if size.isValid() and (size.width() > max_size or size.height() > max_size):
            reader.setScaledSize(size.scaled(QSize(max_size, max_size), Qt.AspectRatioMode.KeepAspectRatio))
        image = reader.read()
        if image.isNull():
            raise ValueError(f"Cannot preview {path.name}: {reader.errorString()}")
        page, pages = 0, 1
    return {"image": image, "page": page, "pages": pages, "name": path.name}


def _read_pdf(path, page_number, max_size):
    try:
        import pypdfium2 as pdfium
        import pypdfium2.raw as pdfium_raw
    except ImportError as error:
        raise ImportError(
            "PDF previews require pypdfium2. Install this eLabLink prerelease "
            "with its elablink[browser] extra in this Python environment."
        ) from error
    document = pdfium.PdfDocument(str(path))
    try:
        pages = len(document)
        if not 0 <= page_number < pages:
            raise ValueError("This PDF page does not exist.")
        page = document[page_number]
        try:
            width, height = page.get_size()
            if width <= 0 or height <= 0:
                raise ValueError("This PDF page has an invalid size.")
            bitmap = page.render(
                scale=min(2.5, max_size / max(width, height)),
                force_bitmap_format=pdfium_raw.FPDFBitmap_BGRA, rev_byteorder=True,
            )
            try:
                image = QImage(
                    bytes(bitmap.buffer), bitmap.width, bitmap.height, bitmap.stride,
                    QImage.Format.Format_RGBA8888,
                ).copy()
            finally:
                bitmap.close()
        finally:
            page.close()
    finally:
        document.close()
    if image.isNull():
        raise ValueError(f"Cannot preview {path.name}.")
    return image, page_number, pages


class _ImageScrollArea(QScrollArea):
    """Select a region to zoom, or pan, without changing the selected attachment."""

    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel
        self._pan_start = None
        self._selection_start = None
        self._pan_mode = False
        self.rubber_band = QRubberBand(QRubberBand.Shape.Rectangle, self.viewport())
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(160, 140)
        self.viewport().installEventFilter(self)

    def _image_rectangle(self):
        return QRect(self.panel.image.mapTo(self.viewport(), QPoint()), self.panel.image.size())

    def _selection_rectangle(self, position):
        return QRect(self._selection_start, position).normalized().intersected(
            self._image_rectangle()).intersected(self.viewport().rect())

    def cancel_interaction(self):
        self._selection_start = None
        self._pan_start = None
        self.rubber_band.hide()
        if self.panel._image.isNull():
            self.viewport().unsetCursor()
            self.panel.image.unsetCursor()
        else:
            cursor = Qt.CursorShape.OpenHandCursor if self._pan_mode else Qt.CursorShape.CrossCursor
            self.viewport().setCursor(cursor)
            self.panel.image.setCursor(cursor)

    def set_pan_mode(self, enabled):
        self._pan_mode = enabled
        self.cancel_interaction()

    def eventFilter(self, watched, event):
        if self.panel._image.isNull():
            return super().eventFilter(watched, event)
        if event.type() == QEvent.Type.Wheel and event.angleDelta().y():
            anchor = self.viewport().mapFromGlobal(event.globalPosition().toPoint())
            self.panel.zoom_image(1.2 ** (event.angleDelta().y() / 120), anchor=anchor)
            return True
        if (event.type() == QEvent.Type.MouseButtonPress
                and event.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton)):
            position = self.viewport().mapFromGlobal(event.globalPosition().toPoint())
            if not self._image_rectangle().intersected(self.viewport().rect()).contains(position):
                return super().eventFilter(watched, event)
            self.cancel_interaction()
            self.panel._view_generation += 1
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            if self._pan_mode or event.button() == Qt.MouseButton.MiddleButton:
                self._pan_start = (event.globalPosition().toPoint(), self.horizontalScrollBar().value(),
                                   self.verticalScrollBar().value(), event.button())
                self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
                self.panel.image.setCursor(Qt.CursorShape.ClosedHandCursor)
            else:
                self._selection_start = position
                self.rubber_band.setGeometry(QRect(position, QSize()))
                self.rubber_band.show()
            return True
        if event.type() == QEvent.Type.MouseMove and self._selection_start is not None:
            position = self.viewport().mapFromGlobal(event.globalPosition().toPoint())
            self.rubber_band.setGeometry(self._selection_rectangle(position))
            return True
        if event.type() == QEvent.Type.MouseMove and self._pan_start is not None:
            position, horizontal, vertical, button = self._pan_start
            delta = event.globalPosition().toPoint() - position
            self.horizontalScrollBar().setValue(horizontal - delta.x())
            self.verticalScrollBar().setValue(vertical - delta.y())
            return True
        if (event.type() == QEvent.Type.MouseButtonRelease and self._selection_start is not None
                and event.button() == Qt.MouseButton.LeftButton):
            position = self.viewport().mapFromGlobal(event.globalPosition().toPoint())
            rectangle = self._selection_rectangle(position)
            self.cancel_interaction()
            self.panel.zoom_to_rectangle(rectangle)
            return True
        if (event.type() == QEvent.Type.MouseButtonRelease and self._pan_start is not None
                and event.button() == self._pan_start[3]):
            self.cancel_interaction()
            return True
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        self.cancel_interaction()
        super().resizeEvent(event)
        if self.panel._image.isNull():
            self.panel.image.resize(self.viewport().size())
        elif self.panel._fit_mode:
            self.panel._fit_image()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape and (self._selection_start is not None or self._pan_start is not None):
            self.cancel_interaction()
            event.accept()
        elif (event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right)
              and event.modifiers() == Qt.KeyboardModifier.NoModifier):
            self.panel.navigate.emit(-1 if event.key() == Qt.Key.Key_Left else 1)
            event.accept()
        else:
            super().keyPressEvent(event)

    def focusOutEvent(self, event):
        self.cancel_interaction()
        super().focusOutEvent(event)


class PreviewPanel(QWidget):
    """Display an image or read-only text, leaving decoding to the worker."""

    navigate = pyqtSignal(int)
    page_requested = pyqtSignal(int)
    h5_open_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(240)
        self._image = QImage()
        self._page = 0
        self._pages = 1
        self._fit_mode = True
        self._scale = 1.0
        self._view_generation = 0
        layout = QVBoxLayout(self)
        heading = QHBoxLayout()
        previous = QPushButton()
        previous.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowBack))
        previous.setMaximumWidth(40)
        previous.setToolTip("Previous image or PDF")
        previous.clicked.connect(lambda: self.navigate.emit(-1))
        next_button = QPushButton()
        next_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowForward))
        next_button.setMaximumWidth(40)
        next_button.setToolTip("Next image or PDF")
        next_button.clicked.connect(lambda: self.navigate.emit(1))
        self.title = QLabel("Preview")
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        self.title.setWordWrap(True)
        heading.addWidget(previous)
        heading.addWidget(self.title, 1)
        heading.addWidget(next_button)
        layout.addLayout(heading)
        self.search_bar = QWidget(self)
        search_layout = QHBoxLayout(self.search_bar)
        search_layout.setContentsMargins(0, 0, 0, 0)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Find in preview…")
        self.search_input.textChanged.connect(lambda: self.find_text(from_start=True))
        self.search_input.installEventFilter(self)
        self.search_previous = QPushButton("Previous")
        self.search_previous.clicked.connect(lambda: self.find_text(backward=True))
        self.search_next = QPushButton("Next")
        self.search_next.clicked.connect(self.find_text)
        self.search_close = QPushButton("Close")
        self.search_close.clicked.connect(self.hide_search)
        self.search_status = QLabel()
        search_layout.addWidget(self.search_input, 1)
        search_layout.addWidget(self.search_previous)
        search_layout.addWidget(self.search_next)
        search_layout.addWidget(self.search_close)
        search_layout.addWidget(self.search_status)
        self.search_bar.hide()
        layout.addWidget(self.search_bar)
        self.zoom_controls = QWidget(self)
        zoom_layout = QHBoxLayout(self.zoom_controls)
        zoom_layout.setContentsMargins(0, 0, 0, 0)
        self.zoom_out = QPushButton("−")
        self.zoom_out.setToolTip("Zoom out")
        self.zoom_out.clicked.connect(lambda: self.zoom_image(1 / 1.2))
        self.zoom_in = QPushButton("+")
        self.zoom_in.setToolTip("Zoom in")
        self.zoom_in.clicked.connect(lambda: self.zoom_image(1.2))
        self.fit_button = QPushButton("Fit")
        self.fit_button.clicked.connect(self.fit_image)
        self.zoom_label = QLabel()
        self.area_zoom_button = QPushButton("Zoom area")
        self.pan_button = QPushButton("Pan")
        self.zoom_modes = QButtonGroup(self)
        for button in (self.area_zoom_button, self.pan_button):
            button.setCheckable(True)
            self.zoom_modes.addButton(button)
        self.area_zoom_button.setChecked(True)
        self.area_zoom_button.setToolTip("Drag a rectangle over the figure to zoom into it. Escape cancels.")
        self.pan_button.setToolTip("Drag to move the zoomed figure. The middle mouse button also pans.")
        for button in (self.zoom_out, self.zoom_in, self.fit_button):
            button.setMaximumWidth(50)
            zoom_layout.addWidget(button)
        zoom_layout.addWidget(self.area_zoom_button)
        zoom_layout.addWidget(self.pan_button)
        zoom_layout.addWidget(self.zoom_label)
        zoom_layout.addStretch()
        layout.addWidget(self.zoom_controls)
        self.image = QLabel()
        self.image.setTextFormat(Qt.TextFormat.PlainText)
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setWordWrap(True)
        self.image.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.image_scroll = _ImageScrollArea(self)
        self.image_scroll.setWidget(self.image)
        self.image.installEventFilter(self.image_scroll)
        self.pan_button.toggled.connect(self.image_scroll.set_pan_mode)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.text.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.text.installEventFilter(self)
        self.content = QStackedWidget()
        self.content.addWidget(self.image_scroll)
        self.content.addWidget(self.text)
        layout.addWidget(self.content, 1)
        self.notice = QLabel()
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        self.notice.setWordWrap(True)
        self.notice.hide()
        layout.addWidget(self.notice)
        self.h5_open_button = QPushButton("Open in h5viewer")
        self.h5_open_button.setObjectName("h5OpenButton")
        self.h5_open_button.setMinimumSize(240, 60)
        self.h5_open_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.h5_open_button.setStyleSheet(
            "QPushButton#h5OpenButton { background-color: #0969da; color: white; "
            "font-size: 16px; font-weight: 600; padding: 14px 24px; "
            "border: 2px solid #0969da; border-radius: 8px; }"
            "QPushButton#h5OpenButton:hover { background-color: #0757b3; border-color: #0757b3; }"
            "QPushButton#h5OpenButton:pressed { background-color: #06428e; border-color: #06428e; }"
            "QPushButton#h5OpenButton:focus { border-color: #74b4ff; }"
            "QPushButton#h5OpenButton:disabled { background-color: #9bb4d0; border-color: #9bb4d0; }"
        )
        self.h5_open_button.clicked.connect(self.h5_open_requested.emit)
        self.h5_open_button.hide()
        self.h5_panel = QWidget()
        h5_layout = QVBoxLayout(self.h5_panel)
        h5_layout.addStretch()
        h5_layout.addWidget(self.h5_open_button, alignment=Qt.AlignmentFlag.AlignHCenter)
        h5_layout.addStretch()
        self.content.addWidget(self.h5_panel)
        self.page_controls = QWidget(self)
        pages = QHBoxLayout(self.page_controls)
        pages.setContentsMargins(0, 0, 0, 0)
        self.previous_page = QPushButton("Previous page")
        self.previous_page.clicked.connect(lambda: self.page_requested.emit(self._page - 1))
        self.next_page = QPushButton("Next page")
        self.next_page.clicked.connect(lambda: self.page_requested.emit(self._page + 1))
        self.page_label = QLabel()
        self.page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pages.addWidget(self.previous_page)
        pages.addWidget(self.page_label, 1)
        pages.addWidget(self.next_page)
        layout.addWidget(self.page_controls)
        self.show_message("Select a file to preview. Left/Right arrows browse images and PDFs.")

    def show_message(self, message, name=""):
        self._view_generation += 1
        self._image = QImage()
        self.image_scroll.cancel_interaction()
        self._fit_mode = True
        self._reset_search()
        self.zoom_controls.hide()
        self.title.setText(name or "Preview")
        self.text.clear()
        self.content.setCurrentWidget(self.image_scroll)
        self.notice.hide()
        self.image.clear()
        self.image.setText(message)
        self.image.resize(self.image_scroll.viewport().size())
        self.page_controls.hide()
        self.h5_open_button.hide()

    def show_h5_file(self, name):
        self.show_message("", name)
        self.content.setCurrentWidget(self.h5_panel)
        self.h5_open_button.show()

    def show_preview(self, result):
        self._view_generation += 1
        self.image_scroll.cancel_interaction()
        self.h5_open_button.hide()
        self._reset_search()
        if "text" in result:
            self._image = QImage()
            self.image.clear()
            self.zoom_controls.hide()
            self.title.setText(result["name"])
            self.text.setPlainText(result["text"])
            self.content.setCurrentWidget(self.text)
            self.page_controls.hide()
            notices = []
            if result.get("truncated"):
                notices.append("Preview shortened. Use Save as… to read the full file.")
            if result.get("encoding") in {"cp1252", "windows-1252"}:
                notices.append("Text decoded as Windows-1252.")
            self.notice.setText(" ".join(notices))
            self.notice.setVisible(bool(notices))
            return
        self.text.clear()
        self.content.setCurrentWidget(self.image_scroll)
        self.zoom_controls.show()
        self.notice.hide()
        self._image = result["image"]
        self.image_scroll.cancel_interaction()
        self._fit_mode = True
        self._page, self._pages = result["page"], result["pages"]
        self.title.setText(result["name"])
        self.page_label.setText(f"{self._page + 1} / {self._pages}")
        self.previous_page.setEnabled(self._page > 0)
        self.next_page.setEnabled(self._page + 1 < self._pages)
        self.page_controls.setVisible(self._pages > 1)
        self._fit_image()

    def _fit_image(self):
        if self._image.isNull() or not self._fit_mode:
            return
        viewport = self.image_scroll.viewport().size()
        size = self._image.size().scaled(viewport, Qt.AspectRatioMode.KeepAspectRatio)
        self._scale = size.width() / self._image.width()
        self._apply_scale()

    def _apply_scale(self):
        size = QSize(max(1, round(self._image.width() * self._scale)),
                     max(1, round(self._image.height() * self._scale)))
        pixmap = QPixmap.fromImage(self._image).scaled(
            size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation,
        )
        self.image.setPixmap(pixmap)
        self.image.resize(pixmap.size())
        self.zoom_label.setText(f"{self._scale:.0%}" + (" (Fit)" if self._fit_mode else ""))
        self.zoom_out.setEnabled(self._scale > 0.05)
        self.zoom_in.setEnabled(self._scale < self._maximum_scale())

    def _maximum_scale(self):
        return min(8.0, 4096 / max(self._image.width(), self._image.height()))

    def zoom_image(self, factor, *, anchor=None):
        if self._image.isNull():
            return
        self.image_scroll.cancel_interaction()
        self._view_generation += 1
        viewport = self.image_scroll.viewport()
        anchor = anchor if anchor is not None else viewport.rect().center()
        origin = self.image.mapTo(viewport, QPoint(0, 0))
        relative_x = (anchor.x() - origin.x()) / max(1, self.image.width())
        relative_y = (anchor.y() - origin.y()) / max(1, self.image.height())
        self._scale = min(self._maximum_scale(), max(0.05, self._scale * factor))
        self._fit_mode = False
        self._apply_scale()
        self._position_image(relative_x, relative_y, anchor=anchor)

    def zoom_to_rectangle(self, rectangle):
        if self._image.isNull() or rectangle.width() < 5 or rectangle.height() < 5:
            return
        self._view_generation += 1
        viewport = self.image_scroll.viewport()
        origin = self.image.mapTo(viewport, QPoint())
        relative_x = (rectangle.center().x() - origin.x()) / self.image.width()
        relative_y = (rectangle.center().y() - origin.y()) / self.image.height()
        factor = min(viewport.width() / rectangle.width(), viewport.height() / rectangle.height())
        self._scale = min(self._maximum_scale(), max(0.05, self._scale * factor))
        self._fit_mode = False
        self._apply_scale()
        self._position_image(relative_x, relative_y)

    def _position_image(self, relative_x, relative_y, *, anchor=None):
        generation = self._view_generation

        def position():
            if generation != self._view_generation or self._image.isNull() or self._fit_mode:
                return
            viewport = self.image_scroll.viewport()
            target = anchor if anchor is not None else viewport.rect().center()
            self.image_scroll.horizontalScrollBar().setValue(round(relative_x * self.image.width() - target.x()))
            self.image_scroll.verticalScrollBar().setValue(round(relative_y * self.image.height() - target.y()))

        # Scrollbars can shrink the viewport after the image has been resized.
        QTimer.singleShot(0, position)

    def fit_image(self):
        self._view_generation += 1
        self.image_scroll.cancel_interaction()
        if not self._image.isNull():
            self._fit_mode = True
            self._fit_image()

    def show_search(self):
        if self.content.currentWidget() is not self.text:
            return
        self.search_bar.show()
        self.search_input.setFocus()
        self.search_input.selectAll()

    def hide_search(self):
        self.search_bar.hide()
        self.text.setFocus()

    def _reset_search(self):
        self.search_bar.hide()
        self.search_input.clear()
        self.search_status.clear()

    def find_text(self, *, backward=False, from_start=False):
        query = self.search_input.text()
        cursor = self.text.textCursor()
        if not query:
            cursor.clearSelection()
            self.text.setTextCursor(cursor)
            self.search_status.clear()
            return
        flags = QTextDocument.FindFlag.FindBackward if backward else QTextDocument.FindFlag(0)
        if from_start:
            cursor.movePosition(QTextCursor.MoveOperation.End if backward else QTextCursor.MoveOperation.Start)
            self.text.setTextCursor(cursor)
        found = self.text.find(query, flags)
        if not found:
            cursor.movePosition(QTextCursor.MoveOperation.End if backward else QTextCursor.MoveOperation.Start)
            self.text.setTextCursor(cursor)
            found = self.text.find(query, flags)
        self.search_status.setText("" if found else "Not found")

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.KeyPress and self.search_bar.isVisible():
            if event.key() == Qt.Key.Key_Escape:
                self.hide_search()
                return True
            if watched is self.search_input and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.find_text(backward=bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier))
                return True
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_image()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            self.navigate.emit(-1 if event.key() == Qt.Key.Key_Left else 1)
            event.accept()
        else:
            super().keyPressEvent(event)
