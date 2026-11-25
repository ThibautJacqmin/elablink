"""Search eLabFTW records before opening their attachments."""

from functools import partial
from html import escape

from PyQt6.QtCore import QObject, QSize, QThreadPool, QTimer, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPushButton, QStyle, QTabWidget, QToolButton, QVBoxLayout, QWidget,
)

from ._attachment_record_search import filter_choices, search_records
from .client import Client
from .exceptions import safe_error_message


_PAGE_SIZE = 25
_USER_ROLE = Qt.ItemDataRole.UserRole


class _Filter(QPushButton):
    changed = pyqtSignal()

    def __init__(self, title):
        super().__init__(title)
        self.title = title
        self.choices = []
        self.selected = []
        self.setEnabled(False)
        self.clicked.connect(self._choose)

    def set_choices(self, choices):
        """Reconcile IDs with the server and refresh the labels of retained choices."""
        self.choices = choices
        available = {value for value, title, color in choices}
        retained = [value for value in self.selected if value in available]
        changed = retained != self.selected
        self._set_selected(retained)
        self.setEnabled(bool(choices))
        return changed

    def _set_selected(self, values):
        self.selected = values
        selected = [(title, color) for value, title, color in self.choices if value in values]
        label = self.title
        tooltip = self.title
        color = None
        if selected:
            label, color = selected[0]
            label = self.fontMetrics().elidedText(label, Qt.TextElideMode.ElideRight,
                                                 14 * self.fontMetrics().averageCharWidth())
            if len(selected) > 1:
                label += f" +{len(selected) - 1}"
            tooltip += ": " + ", ".join(title for title, color in selected)
        self.setIcon(_color_icon(color))
        self.setText(label)
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)

    def _choose(self):
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Select {self.title.lower()}")
        layout = QVBoxLayout(dialog)
        search = QLineEdit(placeholderText="Find a choice…")
        search.setClearButtonEnabled(True)
        layout.addWidget(search)
        choices = QListWidget()
        layout.addWidget(choices)
        for value, title, color in self.choices:
            item = QListWidgetItem(_color_icon(color), title, choices)
            item.setData(_USER_ROLE, value)
            item.setCheckState(Qt.CheckState.Checked if value in self.selected
                               else Qt.CheckState.Unchecked)

        def filter_list(text):
            for i in range(choices.count()):
                item = choices.item(i)
                item.setHidden(text.casefold() not in item.text().casefold())

        search.textChanged.connect(filter_list)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        clear = buttons.addButton("Clear", QDialogButtonBox.ButtonRole.ResetRole)

        def clear_selection():
            for i in range(choices.count()):
                choices.item(i).setCheckState(Qt.CheckState.Unchecked)

        clear.clicked.connect(clear_selection)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            checked = [choices.item(i) for i in range(choices.count())
                       if choices.item(i).checkState() == Qt.CheckState.Checked]
            available = {value for value, title, color in self.choices}
            values = [item.data(_USER_ROLE) for item in checked if item.data(_USER_ROLE) in available]
            if values != self.selected:
                self._set_selected(values)
                self.changed.emit()
        dialog.deleteLater()


class _SearchSession(QObject):
    """Each tab has one lazily created client and runs one request at a time."""

    finished = pyqtSignal(int, object, object, str)

    def __init__(self, config_path):
        super().__init__()
        self.config_path = config_path
        self.client = None

    def read(self, generation, query, need_choices):
        choices, rows, error = None, None, ""
        try:
            if self.client is None:
                self.client = Client(self.config_path)
            if need_choices:
                try:
                    choices = filter_choices(self.client, query[0])
                except Exception as exc:
                    error = f"Filters unavailable. {safe_error_message(exc)}"
            rows = search_records(self.client, *query)
        except Exception as exc:
            error = safe_error_message(exc)
        self.finished.emit(generation, choices, rows, error)


def _color_icon(value):
    color = QColor("#" + str(value or "").lstrip("#"))
    if not color.isValid():
        return QIcon()
    swatch = QPixmap(12, 12)
    swatch.fill(color)
    return QIcon(swatch)


def _badge(row, field):
    title = escape(str(row.get(f"{field}_title") or ""))
    if not title:
        return ""
    color = QColor("#" + str(row.get(f"{field}_color") or "").lstrip("#"))
    if not color.isValid():
        return title
    brightness = (0.299 * color.red() + 0.587 * color.green() + 0.114 * color.blue())
    ink = "#000000" if brightness > 145 else "#ffffff"
    return f'<span style="background-color:{color.name()};color:{ink}">&nbsp;{title}&nbsp;</span>'


class _RecordList(QWidget):
    recordSelected = pyqtSignal(str, int)

    def __init__(self, config_path, entity_type):
        super().__init__()
        self.entity_type = entity_type
        self.offset = 0
        self.generation = 0
        self.busy = False
        self.catalog_revision = 0
        self.catalog_attempted = -1
        self.fetching_catalog = None
        self.session = _SearchSession(config_path)
        self.session.finished.connect(self._finished)
        self.timer = QTimer(self, singleShot=True)
        self.timer.timeout.connect(self._start)

        layout = QVBoxLayout(self)
        self.search = QLineEdit(placeholderText="Search entries…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda: self._request(0, delay=300))
        self.search.returnPressed.connect(lambda: self._request(0))
        search_row = QHBoxLayout()
        search_row.addWidget(self.search, 1)
        self.refresh = QToolButton()
        self.refresh.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        self.refresh.setToolTip("Refresh filters and records from eLabFTW")
        self.refresh.setAccessibleName("Refresh filters and records")
        self.refresh.clicked.connect(self._refresh)
        search_row.addWidget(self.refresh)
        layout.addLayout(search_row)
        filters = QHBoxLayout()
        self.category = _Filter("Category")
        self.status = _Filter("Status")
        self.tags = _Filter("Tags")
        for button in (self.category, self.status, self.tags):
            button.changed.connect(lambda: self._request(0))
            filters.addWidget(button)
        layout.addLayout(filters)

        self.results = QListWidget()
        self.results.setAccessibleName("Matching entries")
        self.results.itemClicked.connect(self._open)
        self.results.itemActivated.connect(self._open)
        layout.addWidget(self.results, 1)
        self.message = QLabel()
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        controls = QHBoxLayout()
        self.previous = QPushButton("Previous")
        self.next = QPushButton("Next")
        self.open_button = QPushButton("Open")
        self.previous.clicked.connect(lambda: self._request(max(0, self.offset - _PAGE_SIZE)))
        self.next.clicked.connect(lambda: self._request(self.offset + _PAGE_SIZE))
        self.open_button.clicked.connect(lambda: self._open(self.results.currentItem()))
        self.results.currentItemChanged.connect(lambda current, _: self.open_button.setEnabled(current is not None))
        for button in (self.previous, self.next, self.open_button):
            button.setEnabled(False)
            controls.addWidget(button)
        layout.addLayout(controls)

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh()

    def _refresh(self):
        self.catalog_revision += 1
        self._request(self.offset)

    def _request(self, offset, delay=0):
        self.offset = offset
        self.generation += 1
        self.results.clear()
        self.open_button.setEnabled(False)
        self.previous.setEnabled(False)
        self.next.setEnabled(False)
        self.message.setText("Searching…")
        self.timer.start(delay)

    def _start(self):
        if self.busy:
            return
        self.busy = True
        query = (self.entity_type, self.search.text(), self.category.selected,
                 self.status.selected, self.tags.selected, self.offset, _PAGE_SIZE + 1)
        self.fetching_catalog = self.catalog_revision if self.catalog_attempted != self.catalog_revision else None
        read = partial(self.session.read, self.generation, query, self.fetching_catalog is not None)
        QThreadPool.globalInstance().start(read)

    @pyqtSlot(int, object, object, str)
    def _finished(self, generation, choices, rows, error):
        self.busy = False
        if self.fetching_catalog == self.catalog_revision:
            self.catalog_attempted = self.catalog_revision
            if choices is not None:
                categories, statuses, tags = choices
                selection_changed = []
                for button, entries in ((self.category, categories), (self.status, statuses)):
                    options = [(entry["id"], entry["title"], entry.get("color")) for entry in entries]
                    selection_changed.append(button.set_choices([("null", "Unassigned", None)] + options))
                selection_changed.append(self.tags.set_choices([(tag, tag, None) for tag in tags]))
                if any(selection_changed):
                    # This response used a filter that the server has since removed.
                    self._request(0)
                    return
        if generation != self.generation:
            if not self.timer.isActive():
                self._start()
            return
        self.previous.setEnabled(self.offset > 0)
        if rows is None:
            self.message.setText(f"{error} Press Enter in the search box to retry.")
            return
        for row in rows[:_PAGE_SIZE]:
            self._add_result(row)
        count = self.results.count()
        self.next.setEnabled(len(rows) > _PAGE_SIZE)
        status = f"Showing {self.offset + 1}–{self.offset + count}" if count else "No matching entries."
        self.message.setText(f"{status}   {error}" if error else status)

    def _add_result(self, row):
        title = str(row.get("title") or "(Untitled)")
        record_id = int(row["id"])
        tags = row.get("tags") or []
        if isinstance(tags, str):
            tags = tags.split("|")
        details = [f"#{record_id}", escape(str(row.get("date") or "")),
                   _badge(row, "category"), _badge(row, "status")]
        lines = [f"<b>{escape(title)}</b>", " · ".join(detail for detail in details if detail)]
        if tags:
            lines.append(escape(", ".join(tags)))
        label = QLabel("<br>".join(lines))
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setMargin(4)
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        item = QListWidgetItem(self.results)
        item.setData(_USER_ROLE, record_id)
        item.setData(Qt.ItemDataRole.AccessibleTextRole, f"{title}, {record_id}")
        item.setToolTip("<qt>" + "<br>".join(lines) + "</qt>")
        item.setSizeHint(QSize(1, label.sizeHint().height()))
        self.results.setItemWidget(item, label)

    def _open(self, item):
        if item is not None:
            self.recordSelected.emit(self.entity_type, item.data(_USER_ROLE))


class RecordPicker(QTabWidget):
    """Select an accessible experiment or resource and browse its files."""

    recordSelected = pyqtSignal(str, int)

    def __init__(self, config_path, parent=None):
        super().__init__(parent)
        for entity_type, title in (("experiments", "Experiments"), ("items", "Resources")):
            page = _RecordList(config_path, entity_type)
            self.addTab(page, title)
            page.recordSelected.connect(self.recordSelected)

    def set_entity_type(self, entity_type):
        self.setCurrentIndex(("experiments", "items").index(entity_type))
