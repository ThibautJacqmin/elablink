"""Desktop browser for ordinary eLabFTW attachments.

The window and its network worker run in a separate process from the caller.

@author Thibaut Jacqmin
"""

from __future__ import annotations

import argparse
from concurrent.futures import CancelledError
from functools import partial
from html import escape
from pathlib import Path
import shutil
import sys
import threading
from time import monotonic
from urllib3.exceptions import TimeoutError as HTTPTimeoutError

from PyQt6.QtCore import QByteArray, QEvent, QEventLoop, QItemSelectionModel, QMimeData, QObject, QSize, QThread, QTimer, Qt, QUrl, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QAction, QDesktopServices, QDrag, QIcon, QKeySequence, QPainter, QPalette, QPixmap
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import (
    QApplication, QDockWidget, QFileDialog, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
    QMainWindow, QMenu, QMessageBox, QProgressBar, QPushButton, QStyle,
    QSplitter, QStackedWidget, QTextBrowser, QToolBar, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ._attachment_archive import create_zip, extract_zip, list_zip
from ._attachment_preview_qt import IMAGE_PREVIEW_SUFFIXES, PREVIEW_SUFFIXES, PreviewPanel, load_preview
from ._attachment_record_picker_qt import RecordPicker
from ._attachment_store import AttachmentStore
from .attachment_browser import open_h5viewer, open_klayout
from .config import resolve_browser_folder, resolve_config_path
from .exceptions import safe_error_message


_TEXT_PREVIEWS = {
    ".py", ".pyw", ".sh", ".bash", ".bat", ".cmd", ".ps1", ".psm1",
    ".vbs", ".js", ".html", ".htm", ".m", ".r", ".sql", ".ipynb",
}
_OPENABLE = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp",
    ".pdf", ".h5", ".hdf5", ".gds", ".zip", ".txt", ".csv", ".tsv", ".json", ".xml",
    ".yaml", ".yml", ".md", ".rst", ".log", ".ini", ".conf",
    ".xlsx", ".xls", ".ods", ".docx", ".doc", ".odt", ".pptx", ".ppt", ".odp", ".svg",
}
_AUTO_PREVIEW_BYTES = 20 * 1024 * 1024
_TOOLBAR_THEME_ICONS = {
    "refresh": "view-refresh", "upload": "go-up", "save": "document-save-as", "open": "document-open",
    "copy": "edit-copy", "paste": "edit-paste", "zip": "package-x-generic", "folder": "folder-open",
    "attachment_link": "insert-link", "record_link": "applications-internet", "find": "edit-find",
    "cache": "drive-harddisk", "working_folder": "folder-new", "temporary_cache": "document-open-recent",
    "refresh_local": "sync-synchronizing", "keyboard": "input-keyboard",
}
_TOOLBAR_STANDARD_ICONS = {
    "refresh": QStyle.StandardPixmap.SP_BrowserReload, "upload": QStyle.StandardPixmap.SP_ArrowUp,
    "save": QStyle.StandardPixmap.SP_DialogSaveButton, "open": QStyle.StandardPixmap.SP_DialogOpenButton,
    "folder": QStyle.StandardPixmap.SP_DirOpenIcon,
}
_TOOLBAR_SYMBOLS = {
    "copy": '<rect x="8" y="8" width="12" height="13" rx="2"/>'
            '<path d="M16 8V3H4v14h4"/>',
    "paste": '<path d="M9 5H5v16h14V5h-4M8 12h8m-8 4h8"/>'
             '<rect x="9" y="3" width="6" height="4" rx="1"/>',
    "zip": '<path fill="#f8c758" stroke="#a56e18" d="M2 7V4h8l2 3h10v14H2z"/>'
           '<path d="M11 7v2m2 0v2m-2 0v2m2 0v2"/><rect x="10" y="16" width="4" height="3" rx=".5"/>',
    "attachment_link": '<path d="m10 14 4-4m-5 6-1 1a4 4 0 0 1-6-6l4-4a4 4 0 0 1 6 0'
                       'm0 10a4 4 0 0 0 6 0l4-4a4 4 0 0 0-6-6l-1 1"/>',
    "record_link": '<path d="M10 21H5V3h10l4 4v4M14 3v5h5m-6 7h8m-3-3 3 3-3 3M8 8h3M8 12h2"/>',
    "find": '<circle cx="10" cy="10" r="6"/><path d="m14.5 14.5 6 6"/>',
    "cache": '<rect x="3" y="4" width="18" height="16" rx="2"/>'
             '<path d="M3 15h18m-5 3h2M8 9h8m-4-3v6m-3-3 3 3 3-3"/>',
    "working_folder": '<path d="M3 7V5h7l2 2h9v13H3z"/><path d="M8 13h8m-4-4v8"/>',
    "temporary_cache": '<path d="M3 7V5h7l2 2h9v13H3z"/><circle cx="12" cy="13.5" r="4"/>'
                       '<path d="M12 11v3h2"/>',
    "refresh_local": '<path d="M4 9a8 8 0 0 1 13-5l3 3M20 3v4h-4M20 15a8 8 0 0 1-13 5l-3-3M4 21v-4h4"/>'
                     '<path d="M12 8v8m-3-3 3 3 3-3"/>',
    "keyboard": '<rect x="2" y="5" width="20" height="14" rx="2"/>'
                '<path d="M5 9h1m3 0h1m3 0h1m3 0h2M5 12h1m3 0h1m3 0h1m3 0h2M7 16h10"/>',
}


def _toolbar_icon(symbol, palette):
    """Use native platform icons, with built-in fallbacks for other Qt backends."""
    icon = QIcon.fromTheme(_TOOLBAR_THEME_ICONS[symbol])
    if not icon.isNull():
        return icon
    if symbol in _TOOLBAR_STANDARD_ICONS:
        return QApplication.style().standardIcon(_TOOLBAR_STANDARD_ICONS[symbol])
    color = palette.color(QPalette.ColorRole.ButtonText).name()
    source = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
              f'fill="none" stroke="{color}" stroke-width="1.7" '
              f'stroke-linecap="round" stroke-linejoin="round">{_TOOLBAR_SYMBOLS[symbol]}</svg>')
    pixmap = QPixmap(48, 48)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    QSvgRenderer(QByteArray(source.encode("utf-8"))).render(painter)
    painter.end()
    return QIcon(pixmap)


def _file_name(row):
    return str(row.get("real_name") or row.get("name") or "")


def _cache_key(row):
    return tuple(row.get(field) for field in
                 ("id", "hash", "created_at", "filesize", "real_name", "long_name"))


def _size_text(size):
    try:
        number = float(size)
    except (TypeError, ValueError):
        return ""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if number < 1024 or unit == "TB":
            return f"{number:.0f} {unit}" if unit == "B" else f"{number:.1f} {unit}"
        number /= 1024


def _error_message(error):
    """Show useful failures without generated API response headers or bodies."""
    if isinstance(error, CancelledError):
        return "Transfer cancelled."
    status = getattr(error, "status", None)
    if isinstance(error, (TimeoutError, HTTPTimeoutError)) or status in (408, 504):
        return "Request timed out. Refresh the attachment list before trying again; an upload may have reached the server."
    if status is not None:
        return f"The eLabFTW request failed (HTTP {status}). Check your connection and permissions."
    return safe_error_message(error)


def _open_previews(files, cancel):
    """Prepare source files as text; never run downloaded programs or scripts."""
    allowed, unsupported = [], []
    for file in files:
        if cancel.is_set():
            raise CancelledError()
        path = Path(file["path"])
        suffix = path.suffix.lower()
        prepared = dict(file)
        if suffix in _TEXT_PREVIEWS:
            preview = path.with_name(path.name + ".txt")
            shutil.copyfile(path, preview)
            prepared["open_path"] = preview
        elif suffix in _OPENABLE:
            prepared["open_path"] = path
            if suffix == ".zip":
                prepared["archive"] = list_zip(path)
            elif suffix == ".svg":
                prepared["preview"] = load_preview(path)
        else:
            unsupported.append(path.name)
            continue
        if cancel.is_set():
            raise CancelledError()
        allowed.append(prepared)
    return allowed, unsupported


class _StoreWorker(QObject):
    def __init__(self, factory):
        super().__init__()
        self.set_store_factory(factory)

    @pyqtSlot(object)
    def set_store_factory(self, factory):
        self.factory = factory
        self.store = None


class _Worker(_StoreWorker):
    finished = pyqtSignal(object)
    progress = pyqtSignal(object)

    @pyqtSlot(object)
    def run(self, job):
        try:
            if self.store is None:
                self.store = self.factory()
            operation = job["operation"]
            args, kwargs = job["args"], job["kwargs"]
            transfer = {"progress": lambda *values: self.progress.emit(values),
                        "cancel": job["cancel"]}
            if operation == "list":
                result = {"rows": self.store.list_files()}
            elif operation == "upload":
                result = {"rows": self.store.upload(*args, **kwargs, **transfer)}
            elif operation == "rename":
                result = {"rows": self.store.rename(*args)}
            elif operation == "delete":
                result = {"rows": self.store.delete(*args, cancel=job["cancel"])}
            elif operation == "save":
                result = {"paths": [self.store.save_file(*args, **kwargs, **transfer)]}
            elif operation == "save_many":
                result = {"paths": self.store.save_files(*args, **kwargs, **transfer)}
            elif operation == "extract":
                result = {"paths": extract_zip(*args, **transfer), "directory": str(args[1])}
            elif operation == "zip":
                paths = self.store.download(args[0], **transfer)
                result = {"paths": [create_zip(paths, args[1], **kwargs, **transfer)]}
            elif operation == "cache_info":
                result = {"cache": self.store.cache_info()}
            elif operation == "clear_cache":
                result = {"cache": self.store.clear_cache(cancel=job["cancel"])}
            elif operation == "set_folder":
                result = self.store.set_folder(args[0])
            elif operation == "links":
                result = {"links": [self.store.record_link()] if kwargs.get("record")
                          else self.store.attachment_links(args[0])}
            elif operation == "open":
                files = self.store.download(*args, **kwargs, **transfer, with_metadata=True)
                result = {"files": files, "paths": [file["path"] for file in files]}
                result["open_files"], result["unsupported"] = _open_previews(files, job["cancel"])
            else:
                paths = self.store.download(*args, **kwargs, **transfer)
                result = {"paths": paths}
            result["title"] = self.store.title
            self.finished.emit(result)
        except Exception as error:
            result = {"error": _error_message(error)}
            if job["operation"] in {"delete", "upload"} and self.store is not None:
                try:
                    result["rows"] = self.store.list_files()
                except Exception:
                    pass
            self.finished.emit(result)


class _PreviewWorker(_StoreWorker):
    """Own a separate client so looking at figures never queues user transfers."""

    finished = pyqtSignal(object)

    def __init__(self, factory, folder=None):
        super().__init__(factory)
        self.folder = folder

    @pyqtSlot(object)
    def run(self, job):
        result = {"key": job["key"], "generation": job["generation"], "row": job["row"]}
        try:
            if job.get("folder") != self.folder:
                if self.store is None:
                    self.store = self.factory()
                self.store.set_folder(job.get("folder"))
                self.folder = job.get("folder")
            path = job.get("path")
            if path is None or not path.is_file():
                if self.store is None:
                    self.store = self.factory()
                path = self.store.download([job["row"]], cancel=job["cancel"], max_bytes=_AUTO_PREVIEW_BYTES)[0]
            if Path(path).stat().st_size > _AUTO_PREVIEW_BYTES:
                raise ValueError("This file is too large for automatic preview. Use Open or Save as… instead.")
            if job["cancel"].is_set():
                raise CancelledError()
            result["path"] = Path(path)
            if Path(path).suffix.lower() == ".zip":
                result["archive"] = list_zip(path)
            else:
                result["preview"] = load_preview(path, page=job["page"])
        except Exception as error:
            result["error"] = _error_message(error)
        self.finished.emit(result)


class _DragPreparation(QEventLoop):
    """Keep the original drag gesture alive while the worker prepares files."""

    def __init__(self, cancel, parent):
        super().__init__(parent)
        self.cancel = cancel
        self.aborted = False

    def abort(self):
        self.aborted = True
        self.cancel.set()
        self.quit()

    def eventFilter(self, watched, event):
        released = (event.type() == QEvent.Type.MouseButtonRelease
                    and event.button() == Qt.MouseButton.LeftButton)
        escaped = event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape
        if released or escaped or event.type() == QEvent.Type.MouseButtonPress:
            self.abort()
        return escaped


class _FileItem(QTreeWidgetItem):
    def __lt__(self, other):
        column = self.treeWidget().sortColumn()
        if column == 2:
            def size(item):
                try:
                    return float(item.data(0, Qt.ItemDataRole.UserRole).get("filesize") or 0)
                except (TypeError, ValueError):
                    return 0
            return size(self) < size(other)
        if column == 1:
            return (self.text(1).casefold(), self.text(0).casefold()) < (
                other.text(1).casefold(), other.text(0).casefold())
        return self.text(column).casefold() < other.text(column).casefold()


class _FileTable(QTreeWidget):
    def __init__(self, browser):
        super().__init__(browser)
        self.browser = browser
        self._dragging = False
        self.setObjectName("attachments")
        self.setHeaderLabels(["Name", "Extension", "Size", "Uploaded"])
        self.setRootIsDecorated(False)
        self.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.setSelectionBehavior(QTreeWidget.SelectionBehavior.SelectRows)
        self.setSortingEnabled(True)
        self.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3):
            self.header().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QTreeWidget.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)

    def dragEnterEvent(self, event):
        self.dragMoveEvent(event)

    def dragMoveEvent(self, event):
        urls = event.mimeData().urls()
        if (not self.browser.busy and not self.browser._preview_paused and urls and event.source() is not self
                and event.possibleActions() & Qt.DropAction.CopyAction
                and all(url.isLocalFile() for url in urls)):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if (self.browser.busy or self.browser._preview_paused or not urls or event.source() is self
                or not event.possibleActions() & Qt.DropAction.CopyAction
                or not all(url.isLocalFile() for url in urls)):
            event.ignore()
            return
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        self.browser.upload_paths([url.toLocalFile() for url in urls])

    def startDrag(self, supported_actions):
        if self._dragging:
            return
        self._dragging = True
        try:
            self.browser.drag_selected()
        finally:
            self._dragging = False

    def mouseMoveEvent(self, event):
        if self._dragging:
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def keyPressEvent(self, event):
        if (event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right)
                and event.modifiers() == Qt.KeyboardModifier.NoModifier):
            self.browser.navigate_preview(-1 if event.key() == Qt.Key.Key_Left else 1)
            event.accept()
        else:
            super().keyPressEvent(event)


class AttachmentBrowser(QMainWindow):
    """An attachment window whose single worker owns all server requests."""

    requested = pyqtSignal(object)
    preview_requested = pyqtSignal(object)
    store_changed = pyqtSignal(object)

    def __init__(self, config_path, entity_type=None, entity_id=None, cache_directory=None,
                 *, folder=None, store_factory=None, preview_store_factory=None):
        super().__init__()
        self.setWindowTitle("eLabLink — Attachments")
        self.resize(1240, 680)
        self.busy = False
        self.rows = []
        self._cached_paths = {}
        self._pending = None
        self._cancel = threading.Event()
        self._close_requested = False
        self._drag_wait = None
        self._showing_rows = False
        self._preview_busy = False
        self._preview_ready = False
        self._preview_paused = False
        self._after_preview = None
        self._preview_cancel = threading.Event()
        self._preview_target = None
        self._preview_generation = 0
        self._preview_row = None
        self._preview_page = 0
        self._archive_path = None
        self._folder = Path(folder).expanduser().resolve() if folder is not None else None
        self._config_path = config_path
        self._cache_directory = cache_directory
        self._record = (entity_type, entity_id) if entity_id is not None else None
        self._next_record = None
        self._record_label = self._format_record_label(*self._record) if self._record else "Select an experiment or resource"

        self.record_dock = QDockWidget("Find a record", self)
        self.record_dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable)
        self.record_picker = RecordPicker(config_path, self.record_dock)
        if self._record:
            self.record_picker.set_entity_type(entity_type)
        self.record_picker.recordSelected.connect(self.open_record)
        self.record_dock.setWidget(self.record_picker)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.record_dock)
        self.record_dock.setVisible(self._record is None)

        central = QWidget(self)
        layout = QVBoxLayout(central)
        self.heading = QLabel(self._record_label)
        self.heading.setTextFormat(Qt.TextFormat.PlainText)
        self.heading.setStyleSheet("font-size: 17px; font-weight: 600; padding: 6px 0;")
        header = QHBoxLayout()
        header.addWidget(self.heading, 1)
        find_record = QToolButton()
        find_record.setDefaultAction(self.record_dock.toggleViewAction())
        find_record.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        header.addWidget(find_record)
        layout.addLayout(header)
        self.storage_label = QLabel()
        self.storage_label.setTextFormat(Qt.TextFormat.PlainText)
        self.storage_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.storage_label.setWordWrap(True)
        self._show_folder()
        layout.addWidget(self.storage_label)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter attachments…")
        self.search.setClearButtonEnabled(True)
        layout.addWidget(self.search)
        self.table = _FileTable(self)
        self.preview = PreviewPanel(self)
        self.preview.navigate.connect(self.navigate_preview)
        self.preview.page_requested.connect(self.preview_page)
        self.preview.h5_open_requested.connect(self.open_h5_preview)
        self.preview_stack = QStackedWidget(self)
        self.preview_stack.addWidget(self.preview)
        self.shortcuts_panel = QWidget(self)
        shortcuts_layout = QVBoxLayout(self.shortcuts_panel)
        shortcuts_title = QLabel("Keyboard shortcuts")
        shortcuts_title.setStyleSheet("font-size: 17px; font-weight: 600;")
        shortcuts_layout.addWidget(shortcuts_title)
        shortcuts_note = QLabel("File shortcuts apply while the attachment list has focus. "
                               "Select a file or press F1 again to return to its preview.")
        shortcuts_note.setWordWrap(True)
        shortcuts_layout.addWidget(shortcuts_note)
        self.shortcuts_text = QTextBrowser()
        self.shortcuts_text.setAccessibleName("Keyboard shortcuts")
        shortcuts_layout.addWidget(self.shortcuts_text, 1)
        shortcuts_back = QPushButton("Back to preview")
        shortcuts_back.clicked.connect(self.show_shortcuts)
        shortcuts_layout.addWidget(shortcuts_back)
        self.preview_stack.addWidget(self.shortcuts_panel)
        self.archive_panel = QWidget(self)
        archive_layout = QVBoxLayout(self.archive_panel)
        self.archive_title = QLabel()
        self.archive_title.setTextFormat(Qt.TextFormat.PlainText)
        self.archive_title.setWordWrap(True)
        archive_layout.addWidget(self.archive_title)
        self.archive_tree = QTreeWidget()
        self.archive_tree.setHeaderLabels(["ZIP contents", "Size"])
        self.archive_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        archive_layout.addWidget(self.archive_tree, 1)
        self.extract_button = QPushButton("Extract to local folder…")
        self.extract_button.clicked.connect(self.extract_archive)
        archive_layout.addWidget(self.extract_button)
        self.preview_stack.addWidget(self.archive_panel)
        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.splitter.addWidget(self.table)
        self.splitter.addWidget(self.preview_stack)
        self.splitter.setSizes([680, 520])
        layout.addWidget(self.splitter, 1)
        self.setCentralWidget(central)

        toolbar = self.attachments_toolbar = QToolBar("Attachments", self)
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        toolbar.setIconSize(QSize(22, 22))
        self.addToolBar(toolbar)
        self.refresh_action = self._action(toolbar, "Refresh", self.refresh,
                                           "refresh", "F5")
        self.upload_action = self._action(toolbar, "Upload", self.choose_upload,
                                          "upload", "Ctrl+U")
        self.download_action = self._action(toolbar, "Save as…", self.save_selected,
                                            "save", "Ctrl+S")
        self.open_action = self._action(toolbar, "Open", self.open_selected,
                                        "open", ["Return", "Enter"])
        self.rename_action = self._action(self.table, "Rename", self.rename_selected, shortcut="F2")
        self.delete_action = self._action(self.table, "Delete", self.delete_selected,
                                          QStyle.StandardPixmap.SP_TrashIcon, "Delete")
        toolbar.addSeparator()
        self.copy_action = self._action(toolbar, "Copy", self.copy_selected,
                                        "copy", QKeySequence.StandardKey.Copy)
        self.paste_action = self._action(toolbar, "Paste", self.paste_files,
                                         "paste", QKeySequence.StandardKey.Paste)
        self.addToolBarBreak()
        tools = self.tools_toolbar = QToolBar("Attachment tools", self)
        tools.setMovable(False)
        tools.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        tools.setIconSize(QSize(22, 22))
        self.addToolBar(tools)
        self.zip_action = self._action(tools, "Save selected as ZIP…", self.save_selected_zip,
                                       "zip", "Ctrl+Shift+S")
        self.folder_action = self._action(tools, "Open local folder", self.open_local_folder,
                                          "folder", "Ctrl+Shift+O")
        self.folder_action.setToolTip(self.folder_action.toolTip()
                                     + "\nDownload the selected file if needed, then open its local folder.")
        tools.addSeparator()
        self.attachment_link_action = self._action(tools, "Copy attachment link", self.copy_attachment_links,
                                                   "attachment_link", "Ctrl+Shift+C")
        self.record_link_action = self._action(
            tools, f"Copy {'experiment' if entity_type == 'experiments' else 'resource'} link",
            self.copy_record_link, "record_link", "Ctrl+Shift+L",
        )
        tools.addSeparator()
        self.find_action = self._action(tools, "Find in preview…", self.preview.show_search,
                                        "find", QKeySequence.StandardKey.Find)
        tools.addSeparator()
        self.choose_folder_action = self._action(tools, "Working folder…", self.choose_working_folder, "working_folder")
        self.temporary_folder_action = self._action(tools, "Use temporary cache", self.use_temporary_cache, "temporary_cache")
        self.cache_action = self._action(tools, "Cached downloads…", self.show_cache, "cache")
        self.refresh_local_action = self._action(tools, "Refresh local copy from server…",
                                                 self.refresh_local_copy, "refresh_local")
        tools.addSeparator()
        self.shortcuts_action = self._action(tools, "Keyboard shortcuts", self.show_shortcuts, "keyboard", "F1")
        self.shortcuts_action.setCheckable(True)
        for action in (self.download_action, self.open_action, self.rename_action, self.delete_action,
                       self.copy_action, self.paste_action, self.zip_action, self.folder_action,
                       self.attachment_link_action):
            action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            self.table.addAction(action)
        self._show_shortcut_list()
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.context_menu)
        self.table.itemDoubleClicked.connect(self._open_item)
        self.table.itemSelectionChanged.connect(self._update_actions)
        self.table.itemSelectionChanged.connect(self._selection_preview)
        self.table.currentItemChanged.connect(self._selection_preview)
        self.table.itemClicked.connect(self._selection_preview)
        self.search.textChanged.connect(self._filter)
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(180)
        self.progress.hide()
        self.cancel_button = QPushButton("Cancel transfer")
        self.cancel_button.clicked.connect(self.cancel_transfer)
        self.cancel_button.hide()
        self.statusBar().addPermanentWidget(self.progress)
        self.statusBar().addPermanentWidget(self.cancel_button)

        factory = store_factory or self._store_factory(entity_type, entity_id)
        self._thread = QThread(self)
        self._worker = _Worker(factory)
        self._worker.moveToThread(self._thread)
        self.store_changed.connect(self._worker.set_store_factory, Qt.ConnectionType.QueuedConnection)
        self.requested.connect(self._worker.run, Qt.ConnectionType.QueuedConnection)
        self._worker.finished.connect(self._completed)
        self._worker.progress.connect(self._progress)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread_stopped)
        self._thread.start()
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(180)
        self._preview_timer.timeout.connect(self._start_preview)
        self._preview_thread = QThread(self)
        self._preview_worker = _PreviewWorker(preview_store_factory or factory, folder=self._folder)
        self._preview_worker.moveToThread(self._preview_thread)
        self.store_changed.connect(self._preview_worker.set_store_factory, Qt.ConnectionType.QueuedConnection)
        self.preview_requested.connect(self._preview_worker.run, Qt.ConnectionType.QueuedConnection)
        self._preview_worker.finished.connect(self._preview_completed)
        self._preview_thread.finished.connect(self._preview_worker.deleteLater)
        self._preview_thread.finished.connect(self._thread_stopped)
        self._preview_thread.start()
        self._update_actions()
        if self._record:
            QTimer.singleShot(0, self.refresh)

    @staticmethod
    def _format_record_label(entity_type, entity_id):
        return f"{'Experiment' if entity_type == 'experiments' else 'Resource'} {entity_id}"

    def _store_factory(self, entity_type, entity_id):
        return partial(AttachmentStore, self._config_path, entity_type, entity_id,
                       self._cache_directory, folder=self._folder)

    @pyqtSlot(str, int)
    def open_record(self, entity_type, entity_id):
        record = (entity_type, entity_id)
        if self._close_requested:
            return
        if self._next_record is not None or (self._pending and self._pending["intent"] == "list"):
            # Clicks can arrive faster than a read. Keep the last choice.
            self._next_record = record
            return
        if record == self._record:
            return
        if self.busy or self._preview_paused or self._drag_wait is not None:
            self.statusBar().showMessage("Finish the current operation before opening another record.")
            return
        # A pending preview can still write to the cache. Drain it before switching
        # both workers, so files and operations always belong to the displayed record.
        self._next_record = record
        self._pause_preview(self._switch_record)

    def _switch_record(self):
        record, self._next_record = self._next_record, None
        if record == self._record:
            self._resume_preview()
            return
        self._record = record
        entity_type, entity_id = record
        self._record_label = self._format_record_label(entity_type, entity_id)
        self.heading.setText(self._record_label)
        self.setWindowTitle(f"eLabLink — {self._record_label}")
        kind = "experiment" if entity_type == "experiments" else "resource"
        self.record_link_action.setText(f"Copy {kind} link")
        keys = self.record_link_action.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
        self.record_link_action.setToolTip(f"Copy {kind} link ({keys})")
        self._show_shortcut_list()
        self.store_changed.emit(self._store_factory(entity_type, entity_id))
        self._cached_paths.clear()
        self._archive_path = self._preview_row = None
        self._preview_ready = False
        self._show_rows([])
        self.search.clear()
        self._show_file_panel()
        self.preview.show_message("Select a file to preview.")
        self._resume_preview(reload=False)
        self.refresh()

    def _action(self, toolbar, text, slot, icon=None, shortcut=None):
        action = QAction(text, self)
        if isinstance(icon, str):
            action.setIcon(_toolbar_icon(icon, self.palette()))
        elif icon is not None:
            action.setIcon(self.style().standardIcon(icon))
        if shortcut is not None:
            shortcuts = shortcut if isinstance(shortcut, list) else [shortcut]
            action.setShortcuts([QKeySequence(key) for key in shortcuts])
            keys = " / ".join(key.toString(QKeySequence.SequenceFormat.NativeText) for key in action.shortcuts())
            action.setToolTip(f"{text} ({keys})")
        action.triggered.connect(slot)
        toolbar.addAction(action)
        return action

    def _show_shortcut_list(self):
        groups = [
            ("Files — attachment list", [self.open_action, self.download_action, self.rename_action,
                                         self.delete_action, self.copy_action, self.paste_action,
                                         self.zip_action, self.folder_action, self.attachment_link_action]),
            ("Window", [self.refresh_action, self.upload_action, self.record_link_action,
                        self.find_action, self.shortcuts_action]),
        ]
        sections = []
        for title, actions in groups:
            rows = []
            for action in actions:
                keys = " / ".join(key.toString(QKeySequence.SequenceFormat.NativeText) for key in action.shortcuts())
                rows.append(f'<tr><td style="padding:4px 12px 4px 0"><b>{escape(keys)}</b></td>'
                            f'<td>{escape(action.text().replace("…", ""))}</td></tr>')
            sections.append(f"<h3>{title}</h3><table>{''.join(rows)}</table>")
        sections.append("<h3>Preview</h3><table>"
                        "<tr><td><b>← / →</b></td><td>Previous / next image or PDF file</td></tr>"
                        "<tr><td><b>Enter / Shift+Enter</b></td><td>Next / previous search match</td></tr>"
                        "<tr><td><b>Escape</b></td><td>Close preview search or cancel selection zoom</td></tr>"
                        "</table>")
        self.shortcuts_text.setHtml("".join(sections))

    def show_shortcuts(self):
        if self.preview_stack.currentWidget() is self.shortcuts_panel:
            self._show_file_panel()
        else:
            self.preview_stack.setCurrentWidget(self.shortcuts_panel)
            self.shortcuts_action.setChecked(True)
        self._update_actions()

    def _show_file_panel(self):
        self.preview_stack.setCurrentWidget(self.archive_panel if self._archive_path is not None else self.preview)
        self.shortcuts_action.setChecked(False)

    def selected_rows(self):
        return [dict(item.data(0, Qt.ItemDataRole.UserRole))
                for item in self.table.selectedItems() if not item.isHidden()]

    def _update_actions(self):
        count = len(self.selected_rows())
        active = bool(self._record) and not self.busy and not self._close_requested and not self._preview_paused
        for action in (self.refresh_action, self.upload_action, self.paste_action,
                       self.record_link_action, self.cache_action, self.choose_folder_action):
            action.setEnabled(active)
        for action in (self.download_action, self.open_action, self.copy_action,
                       self.delete_action, self.zip_action, self.attachment_link_action):
            action.setEnabled(bool(count) and active)
        self.rename_action.setEnabled(count == 1 and active)
        self.folder_action.setEnabled(count == 1 and active)
        self.extract_button.setEnabled(self._archive_path is not None and active)
        self.find_action.setEnabled(not self._close_requested and not self._preview_paused
                                    and self.preview_stack.currentWidget() is self.preview
                                    and self.preview.content.currentWidget() is self.preview.text)
        self.preview.h5_open_button.setEnabled(active)
        self.temporary_folder_action.setEnabled(active and self._folder is not None)
        self.refresh_local_action.setEnabled(active and self._folder is not None and count == 1)
        self.shortcuts_action.setEnabled(not self._close_requested)

    def _filter(self, text):
        query = text.casefold()
        for index in range(self.table.topLevelItemCount()):
            item = self.table.topLevelItem(index)
            item.setHidden(query not in item.text(0).casefold())
        self._update_actions()
        self._selection_preview(keep_shortcuts=True)

    def _show_rows(self, rows):
        selected = {row["id"] for row in self.selected_rows()}
        current = self.table.currentItem()
        current_id = current.data(0, Qt.ItemDataRole.UserRole)["id"] if current else None
        self._showing_rows = True
        self.rows = rows
        self.table.setSortingEnabled(False)
        self.table.clear()
        for row in rows:
            item = _FileItem([_file_name(row), Path(_file_name(row)).suffix,
                              _size_text(row.get("filesize")),
                              str(row.get("created_at") or "")])
            item.setData(0, Qt.ItemDataRole.UserRole, dict(row))
            if Path(_file_name(row)).suffix == ".h5":
                font = item.font(0)
                font.setBold(True)
                item.setFont(0, font)
                item.setForeground(0, self.palette().brush(QPalette.ColorRole.Link))
                item.setToolTip(0, "H5 file — double-click to open a local copy in h5viewer.")
            self.table.addTopLevelItem(item)
            if row["id"] in selected:
                item.setSelected(True)
            if row["id"] == current_id:
                self.table.setCurrentItem(item, 0, QItemSelectionModel.SelectionFlag.NoUpdate)
        self.table.setSortingEnabled(True)
        self._showing_rows = False
        self._filter(self.search.text())

    def _selection_preview(self, *args, keep_shortcuts=False):
        if self._showing_rows or self._close_requested:
            return
        selected = [item for item in self.table.selectedItems() if not item.isHidden()]
        current = self.table.currentItem()
        item = current if current in selected else next(iter(selected), None)
        row = dict(item.data(0, Qt.ItemDataRole.UserRole)) if item and not item.isHidden() else None
        show = not (keep_shortcuts and self.preview_stack.currentWidget() is self.shortcuts_panel)
        self._request_preview(row, show=show)

    def _request_preview(self, row, page=0, *, show=True):
        if show:
            self._show_file_panel()
        h5 = row is not None and Path(_file_name(row)).suffix == ".h5"
        supported = row is not None and (h5 or Path(_file_name(row)).suffix.lower() in PREVIEW_SUFFIXES | {".zip"})
        target = (_cache_key(row), page) if supported else None
        if target == self._preview_target:
            self._update_actions()
            return
        self._preview_cancel.set()
        self._preview_generation += 1
        self._preview_target = target
        self._preview_ready = False
        self._preview_row, self._preview_page = row if supported else None, page
        self._archive_path = None
        if show:
            self.preview_stack.setCurrentWidget(self.preview)
        if h5:
            self._preview_timer.stop()
            self.preview.show_h5_file(_file_name(row))
            self._update_actions()
            return
        self.preview.show_message("Loading preview…" if supported else "No preview available for this file type.",
                                  _file_name(row) if row else "")
        self._update_actions()
        self._preview_timer.start()

    def _start_preview(self):
        if (self._close_requested or self._preview_paused or self._preview_busy
                or self._preview_ready or self._preview_target is None):
            return
        if Path(_file_name(self._preview_row)).suffix == ".h5":
            return
        self._preview_busy = True
        self._preview_cancel = threading.Event()
        self.preview_requested.emit({
            "key": self._preview_target, "generation": self._preview_generation,
            "row": dict(self._preview_row), "page": self._preview_page,
            "path": self._cached_paths.get(_cache_key(self._preview_row)), "cancel": self._preview_cancel,
            "folder": self._folder,
        })

    @pyqtSlot(object)
    def _preview_completed(self, result):
        self._preview_busy = False
        if self._close_requested:
            return
        if "path" in result and "error" not in result:
            self._cached_paths[_cache_key(result["row"])] = result["path"]
        if self._preview_paused:
            callback, self._after_preview = self._after_preview, None
            if callback is not None:
                callback()
            return
        if result["generation"] != self._preview_generation:
            if not self._preview_timer.isActive():
                self._preview_timer.start(0)
            return
        self._preview_ready = True
        if "error" in result:
            self.preview.show_message(result["error"], _file_name(result["row"]))
        elif "archive" in result:
            self._show_archive(result["path"], result["archive"])
        else:
            self.preview.show_preview(result["preview"])
        self._update_actions()

    def _show_open_preview(self, file):
        """Show an explicit Open result without restarting an automatic preview."""
        self._preview_cancel.set()
        self._preview_timer.stop()
        self._preview_generation += 1
        self._preview_row, self._preview_page = file["row"], 0
        self._preview_target = (_cache_key(file["row"]), 0)
        self._preview_ready = True
        self._archive_path = None
        self.preview_stack.setCurrentWidget(self.preview)
        self.shortcuts_action.setChecked(False)
        if "archive" in file:
            self._show_archive(Path(file["path"]), file["archive"])
        else:
            self.preview.show_preview(file["preview"])

    def _pause_preview(self, callback):
        self._preview_paused = True
        self._preview_timer.stop()
        self._preview_cancel.set()
        self._preview_generation += 1
        self._preview_target = None
        self._after_preview = callback
        self._update_actions()
        if self._preview_busy:
            self.statusBar().showMessage("Finishing the preview download…")
        else:
            self._after_preview = None
            callback()

    def _resume_preview(self, *, reload=True):
        self._preview_paused = False
        self._preview_target = None
        if reload:
            self._selection_preview(keep_shortcuts=True)
        self._update_actions()

    def _show_folder(self):
        self.storage_label.setText(f"Working folder: {self._folder}" if self._folder else "Temporary cache")

    def choose_working_folder(self):
        if self.busy or self._preview_paused or self._close_requested:
            return
        folder = QFileDialog.getExistingDirectory(self, "Choose a working folder", str(self._folder or ""))
        if folder:
            self._pause_preview(lambda: self._begin("set_folder", (Path(folder),)))

    def use_temporary_cache(self):
        if not self.busy and not self._preview_paused and not self._close_requested:
            self._pause_preview(lambda: self._begin("set_folder", (None,)))

    def refresh_local_copy(self):
        rows = self.selected_rows()
        if len(rows) != 1 or self._folder is None or self.busy or self._preview_paused or self._close_requested:
            return
        answer = QMessageBox.question(
            self, "Replace local copy?",
            f"Download {_file_name(rows[0])} again from eLabFTW?\nAny edits to the local copy will be replaced.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._pause_preview(lambda: self._begin("download", (rows,), rows=rows,
                                                    intent="refresh_local", refresh=True))

    def navigate_preview(self, direction):
        items = [self.table.topLevelItem(index) for index in range(self.table.topLevelItemCount())]
        figures = [item for item in items if not item.isHidden()
                   and Path(item.text(0)).suffix.lower() in IMAGE_PREVIEW_SUFFIXES]
        if not figures:
            return
        current = self.table.currentItem()
        if current is None or not current.isSelected() or current.isHidden():
            selected = [item for item in self.table.selectedItems() if not item.isHidden()]
            current = selected[0] if selected else None
        position = items.index(current) if current in items else (-1 if direction > 0 else len(items))
        candidates = [item for item in figures if (items.index(item) - position) * direction > 0]
        item = (candidates[0] if direction > 0 else candidates[-1]) if candidates else (
            figures[0] if direction > 0 else figures[-1])
        self.table.setCurrentItem(item, 0, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        self.table.scrollToItem(item)

    def preview_page(self, page):
        if self._preview_row is not None:
            self._request_preview(self._preview_row, page=page)

    def open_h5_preview(self):
        if self._preview_row is not None and Path(_file_name(self._preview_row)).suffix == ".h5":
            self._begin("open", ([self._preview_row],), rows=[self._preview_row])

    def _show_archive(self, path, entries):
        self._archive_path = Path(path)
        self.archive_title.setText(f"{path.name} — {_size_text(sum(entry['size'] for entry in entries))} uncompressed")
        self.archive_tree.clear()
        folders = {}
        for entry in entries:
            parts = entry["name"].rstrip("/").split("/")
            parent = self.archive_tree.invisibleRootItem()
            for index, part in enumerate(parts):
                key = tuple(parts[:index + 1])
                if key not in folders:
                    folders[key] = QTreeWidgetItem(parent, [part, ""])
                parent = folders[key]
            if not entry["is_dir"]:
                parent.setText(1, _size_text(entry["size"]))
        self.archive_tree.expandToDepth(0)
        if self.preview_stack.currentWidget() is not self.shortcuts_panel:
            self.preview_stack.setCurrentWidget(self.archive_panel)
        self._update_actions()

    def extract_archive(self):
        if self._archive_path is None or self.busy or self._close_requested:
            return
        directory = QFileDialog.getExistingDirectory(self, "Extract ZIP to local folder")
        if directory:
            self._begin("extract", (self._archive_path, Path(directory)))

    def _begin(self, operation, args=(), *, intent=None, rows=(), **kwargs):
        if (not self._record or self.busy or self._close_requested
                or (self._preview_paused and operation not in {"cache_info", "clear_cache", "set_folder"}
                    and intent != "refresh_local")):
            return
        self.busy = True
        self._cancel = threading.Event()
        self._pending = {"intent": intent or operation, "rows": list(rows)}
        self.progress.setRange(0, 0)
        self.progress.show()
        self.cancel_button.setVisible(operation in {"download", "save", "save_many", "open", "upload", "delete", "extract", "zip"})
        self.cancel_button.setEnabled(True)
        message = "Loading attachments…" if operation == "list" else "Working…"
        if intent == "copy":
            message = "Preparing files…"
        self.statusBar().showMessage(message)
        self._update_actions()
        self.requested.emit({"operation": operation, "args": args, "kwargs": kwargs,
                             "cancel": self._cancel})

    def refresh(self):
        self._preview_cancel.set()
        self._preview_generation += 1
        self._preview_target = None
        self._begin("list")

    @pyqtSlot(object)
    def _progress(self, values):
        done, total, name = values
        if total:
            self.progress.setRange(0, 100)
            self.progress.setValue(min(100, int(100 * done / total)))
        else:
            self.progress.setRange(0, 0)
        verb = "Extracting" if self._pending and self._pending["intent"] == "extract" else "Transferring"
        self.statusBar().showMessage(f"{verb} {name}…")

    @pyqtSlot(object)
    def _completed(self, result):
        pending = self._pending
        self._pending = None
        self.busy = False
        self.progress.hide()
        self.cancel_button.hide()
        if self._close_requested:
            self._thread.quit()
            return
        if pending["intent"] == "list" and self._next_record is not None:
            record, self._next_record = self._next_record, None
            if record != self._record:
                self.open_record(*record)
                return
        if "rows" in result:
            self._show_rows(result["rows"])
            self.statusBar().showMessage(f"{len(self.rows)} attachments")
        if "error" in result:
            self.statusBar().showMessage(result["error"])
            if result["error"] != "Transfer cancelled.":
                QMessageBox.warning(self, "Attachment operation failed", result["error"])
            if self._preview_paused:
                self._resume_preview()
            self._update_actions()
            return
        if result.get("title"):
            self.heading.setText(f"{self._record_label} — {result['title']}")
            self.setWindowTitle(f"eLabLink — {result['title']}")
        paths = result.get("paths", [])
        if "files" in result:
            updated = {int(file["row"]["id"]): file["row"] for file in result["files"]}
            for file in result["files"]:
                self._cached_paths[_cache_key(file["row"])] = Path(file["path"])
            self._show_rows([updated.get(int(row["id"]), row) for row in self.rows])
        else:
            for row, path in zip(pending["rows"], paths):
                self._cached_paths[_cache_key(row)] = Path(path)
        intent = pending["intent"]
        if intent == "cache_info":
            info = result["cache"]
            answer = QMessageBox.question(
                self, "Cached downloads",
                f"This record has {info['files']} cached files ({_size_text(info['bytes'])}).\n\n"
                "Clear these local copies? Clipboard links and viewers may still use them.\n"
                "Working files, Save as… copies and attachments on eLabFTW are kept.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer == QMessageBox.StandardButton.Yes:
                self._begin("clear_cache")
            else:
                self._resume_preview()
        elif intent == "set_folder":
            self._folder = result["folder"]
            self._cached_paths.clear()
            self._archive_path = None
            self._show_folder()
            self._resume_preview()
            self.statusBar().showMessage("Local storage folder changed. Existing files were kept.")
        elif intent == "refresh_local":
            self._resume_preview()
            self.statusBar().showMessage("Local copy refreshed from eLabFTW.")
        elif intent == "clear_cache":
            self._cached_paths.clear()
            self._archive_path = None
            self.preview_stack.setCurrentWidget(self.preview)
            self.preview.show_message("Cache cleared. Select a file to download its preview again.")
            self._resume_preview(reload=False)
            self.statusBar().showMessage(f"Cleared {_size_text(result['cache']['removed_bytes'])} of cached downloads.")
        elif intent == "links":
            QApplication.clipboard().setText("\n".join(result["links"]))
            self.statusBar().showMessage(f"Copied {len(result['links'])} eLabFTW links.")
        elif intent == "folder":
            if paths and QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(paths[0]).parent))):
                self.statusBar().showMessage("Opened the local folder.")
            else:
                QMessageBox.information(self, "Open local folder", "The file manager could not open this folder.")
        elif intent == "copy":
            mime = QMimeData()
            mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
            QApplication.clipboard().setMimeData(mime)
            self.statusBar().showMessage(f"{len(paths)} files copied. Paste them into a local folder.")
        elif intent == "drag":
            self.statusBar().showMessage("Files ready to drag.")
        elif intent == "open":
            failures = []
            opened = 0
            for file in result["open_files"]:
                path = Path(file["open_path"])
                try:
                    if path.suffix == ".h5":
                        self._watch_h5viewer(open_h5viewer(path))
                    elif path.suffix.lower() == ".gds":
                        self._watch_viewer(open_klayout(path), "KLayout")
                    elif path.suffix.lower() in {".zip", ".svg"}:
                        self._show_open_preview(file)
                    elif not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
                        raise OSError(f"No local application could open {path.name}")
                    opened += 1
                except (OSError, ImportError) as error:
                    failures.append(str(error))
            if result["unsupported"]:
                failures.append("Use Save as… for files without a supported viewer:\n"
                                + "\n".join(result["unsupported"]))
            if failures:
                QMessageBox.information(
                    self, "Open attachments", "\n\n".join(failures)
                )
            self.statusBar().showMessage(f"Opened {opened} attachments.")
        elif intent == "extract":
            self.statusBar().showMessage(f"Extracted {len(paths)} files to {result['directory']}")
        elif intent == "zip":
            self.statusBar().showMessage(f"Saved ZIP: {Path(paths[0]).name}")
        elif paths:
            self.statusBar().showMessage(f"Saved {len(paths)} files")
        self._update_actions()

    def _watch_h5viewer(self, process):
        """Report startup failures without waiting on the GUI thread."""
        self._watch_viewer(process, "h5viewer")

    def _watch_viewer(self, process, name):
        timer = QTimer(self)
        deadline = monotonic() + 15

        def check_startup():
            code = process.poll()
            if code is None and monotonic() < deadline and not self._close_requested:
                return
            timer.stop()
            timer.deleteLater()
            if code not in (None, 0) and not self._close_requested:
                QMessageBox.warning(
                    self, f"{name} could not start",
                    ("Check that h5viewer is compatible with your installed Labmate.\n" if name == "h5viewer" else "")
                    + f"Startup details: {process.browser_log_path}",
                )

        timer.timeout.connect(check_startup)
        timer.start(250)

    def cancel_transfer(self):
        self._cancel.set()
        if self._drag_wait is not None:
            self._drag_wait.abort()
        self.cancel_button.setEnabled(False)
        self.statusBar().showMessage("Cancelling after the current request…")

    def choose_upload(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Upload attachments")
        if paths:
            self.upload_paths(paths)

    def upload_paths(self, paths):
        if self.busy or not paths:
            return
        names = {_file_name(row) for row in self.rows}
        collisions = sorted({Path(path).name for path in paths} & names)
        replace = False
        if collisions:
            replace = QMessageBox.question(
                self, "Replace existing attachments?",
                "These attachments already exist:\n" + "\n".join(collisions)
                + "\n\nReplace them? Previous versions will be archived.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            ) == QMessageBox.StandardButton.Yes
            if not replace:
                return
        self._begin("upload", (list(paths),), replace=replace)

    def paste_files(self):
        mime = QApplication.clipboard().mimeData()
        urls = mime.urls() if mime is not None else []
        if urls and all(url.isLocalFile() for url in urls):
            self.upload_paths([url.toLocalFile() for url in urls])
        else:
            self.statusBar().showMessage("Copy files in your file manager, then paste them here to upload.")

    def copy_selected(self):
        rows = self.selected_rows()
        if rows:
            self._begin("download", (rows,), intent="copy", rows=rows)

    def drag_selected(self):
        if self.busy or self._preview_paused or self._close_requested:
            return
        rows = self.selected_rows()
        if not rows:
            return
        paths = [self._cached_paths.get(_cache_key(row)) for row in rows]
        if not all(path is not None and path.is_file() for path in paths):
            self._begin("download", (rows,), intent="drag", rows=rows)
            wait = _DragPreparation(self._cancel, self)
            self._drag_wait = wait
            application = QApplication.instance()
            application.installEventFilter(wait)
            self._worker.finished.connect(wait.quit)
            self.statusBar().showMessage("Preparing files… keep the mouse button pressed.")
            try:
                wait.exec()
            finally:
                try:
                    self._worker.finished.disconnect(wait.quit)
                except RuntimeError:
                    # Closing the window may already have destroyed the worker.
                    pass
                application.removeEventFilter(wait)
                self._drag_wait = None
                aborted = wait.aborted
                wait.deleteLater()
            if aborted or self.busy:
                return
            paths = [self._cached_paths.get(_cache_key(row)) for row in rows]
        if (self._close_requested or not QApplication.mouseButtons() & Qt.MouseButton.LeftButton
                or rows != self.selected_rows()
                or not all(path is not None and path.is_file() for path in paths)):
            return
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
        drag = QDrag(self.table)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction, Qt.DropAction.CopyAction)

    def open_selected(self):
        rows = self.selected_rows()
        if rows:
            self._begin("open", (rows,), rows=rows)

    def _open_item(self, item, column):
        row = dict(item.data(0, Qt.ItemDataRole.UserRole))
        self._begin("open", ([row],), rows=[row])

    def delete_selected(self):
        rows = self.selected_rows()
        if not rows or self.busy or self._close_requested:
            return
        answer = QMessageBox.question(
            self, "Delete attachments?", "Delete these attachments from eLabFTW?\n"
            + "\n".join(_file_name(row) for row in rows)
            + "\n\nLocal copies will be kept.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._begin("delete", (rows,))

    def save_selected(self):
        rows = self.selected_rows()
        if not rows or self.busy:
            return
        if len(rows) == 1:
            destination, _ = QFileDialog.getSaveFileName(
                self, "Save attachment", Path(_file_name(rows[0])).name,
                options=QFileDialog.Option.DontConfirmOverwrite,
            )
            if not destination:
                return
            targets = [Path(destination)]
        else:
            directory = QFileDialog.getExistingDirectory(self, "Save selected attachments")
            if not directory:
                return
            targets = [Path(directory) / Path(_file_name(row)).name for row in rows]
        targets = [path.expanduser().absolute() for path in targets]
        existing = [path for path in targets if path.exists()]
        if existing and QMessageBox.question(
            self, "Replace local files?", "Replace these local files?\n" + "\n".join(path.name for path in existing),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Yes:
            return
        if len(rows) == 1:
            self._begin("save", (rows[0], str(targets[0])), overwrite=bool(existing))
        else:
            destinations = {int(row["id"]): str(path) for row, path in zip(rows, targets)}
            self._begin("save_many", (destinations,), overwrite=[str(path) for path in existing])

    def save_selected_zip(self):
        rows = self.selected_rows()
        if not rows or self.busy or self._preview_paused or self._close_requested:
            return
        filename, _ = QFileDialog.getSaveFileName(
            self, "Save selected attachments as ZIP", "attachments.zip", "ZIP archives (*.zip)",
            options=QFileDialog.Option.DontConfirmOverwrite,
        )
        if not filename:
            return
        destination = Path(filename)
        overwrite = destination.exists()
        if overwrite and QMessageBox.question(
            self, "Replace local ZIP?", f"Replace {destination.name}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Yes:
            return
        self._begin("zip", (rows, destination), overwrite=overwrite)

    def open_local_folder(self):
        rows = self.selected_rows()
        if len(rows) == 1:
            self._begin("download", (rows,), rows=rows, intent="folder")

    def copy_attachment_links(self):
        rows = self.selected_rows()
        if rows:
            self._begin("links", (rows,))

    def copy_record_link(self):
        self._begin("links", record=True)

    def show_cache(self):
        if not self.busy and not self._preview_paused and not self._close_requested:
            self._pause_preview(lambda: self._begin("cache_info"))

    def rename_selected(self):
        rows = self.selected_rows()
        if len(rows) != 1 or self.busy:
            return
        row = rows[0]
        name, accepted = QInputDialog.getText(self, "Rename attachment", "File name:",
                                             text=_file_name(row))
        if accepted and name != _file_name(row):
            self._begin("rename", (row["id"], name))

    def context_menu(self, position):
        menu = QMenu(self)
        menu.addActions([self.open_action, self.download_action, self.copy_action,
                         self.rename_action, self.delete_action])
        menu.addActions([self.zip_action, self.folder_action, self.attachment_link_action])
        menu.addSeparator()
        menu.addActions([self.paste_action, self.upload_action, self.refresh_action])
        menu.exec(self.table.viewport().mapToGlobal(position))

    def closeEvent(self, event):
        if self._drag_wait is not None:
            self._drag_wait.abort()
        if not self._thread.isRunning() and not self._preview_thread.isRunning():
            event.accept()
            return
        event.ignore()
        if self.busy:
            if self._close_requested:
                return
            answer = QMessageBox.question(
                self, "Transfer in progress", "Cancel the transfer and close when the current request finishes?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            self._close_requested = True
            if self.busy:
                self.cancel_transfer()
            else:
                self._thread.quit()
            self._update_actions()
        else:
            self._close_requested = True
            self._thread.quit()
        if self._close_requested:
            self._preview_timer.stop()
            self._preview_cancel.set()
            self._preview_thread.quit()

    @pyqtSlot()
    def _thread_stopped(self):
        if self._close_requested and not self._thread.isRunning() and not self._preview_thread.isRunning():
            self.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Browse eLabFTW attachments")
    parser.add_argument("--config-path")
    parser.add_argument("--entity-type", choices=("experiments", "items"))
    parser.add_argument("--entity-id", type=int)
    parser.add_argument("--cache-directory")
    parser.add_argument("--folder")
    arguments = parser.parse_args(argv)
    if (arguments.entity_type is None) != (arguments.entity_id is None):
        parser.error("--entity-type and --entity-id must be provided together")
    if arguments.entity_id is not None and arguments.entity_id < 1:
        parser.error("--entity-id must be a positive integer")
    application = QApplication.instance() or QApplication(sys.argv[:1])
    application.setApplicationName("eLabLink")
    try:
        config_path = resolve_config_path(arguments.config_path)
        folder = resolve_browser_folder(arguments.folder)
    except (OSError, ValueError) as error:
        QMessageBox.warning(None, "Unable to open eLabLink", str(error))
        return 1
    window = AttachmentBrowser(config_path, arguments.entity_type, arguments.entity_id,
                               arguments.cache_directory, folder=folder)
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
