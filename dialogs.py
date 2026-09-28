"""
dialogs.py – Image picker dialog + progress dialog.
"""

import os
import shutil

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QGridLayout, QWidget, QProgressBar, QSizePolicy,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QListWidget, QListWidgetItem, QLineEdit, QTextEdit, QComboBox,
    QScrollArea, QToolButton, QFileDialog, QSplitter, QMessageBox,
    QCheckBox, QApplication, QButtonGroup, QStyledItemDelegate,
)
from PyQt5.QtCore import Qt, pyqtSignal, QSize, QRectF
from PyQt5.QtGui import QFont, QColor, QIcon, QFontMetrics, QPixmap, QPainter

from pathlib import Path

from workers import SearchWorker, ImageDownloadWorker
from search import build_query
from image_utils import load_cropped_pixmap, icon_from_file
from cover_art import read_cover
from icons import get_icon
from tags_list import display_for, suggest_identifier
from json_health import (
    HealthIssue, ISSUE_LABELS, normalize_name, summarize, rename_lrc_for_issue,
)


def _apply_overlay_chrome(dlg: 'QDialog', theme: dict | None = None) -> None:
    """Apply the theme-aware card background to a dialog.

    Historically this also stripped the OS title bar (``Qt.FramelessWindowHint``)
    and hand-centered the dialog over its parent with a custom ✕ button. On
    Linux/Wayland that frameless window had no title bar to grab, could not be
    moved off the main window, and stacked unpredictably behind the parent's
    toolbar. We now keep the **native window frame** — the window manager owns
    the title bar, stacking (above the parent), centering and resizing — and
    only paint the themed background here.
    """
    if theme is None:
        theme = getattr(dlg.parent(), '_theme', None) or {}
    bg = theme.get('bg_card', '#0f1320')

    # Solid opaque background so the dialog matches the active theme. No
    # FramelessWindowHint: the native frame gives a real close button and
    # lets the WM place the window correctly on every platform.
    dlg.setStyleSheet(
        (dlg.styleSheet() or '')
        + '\nQDialog{background:%s;}' % bg
    )


# ── Image Card ────────────────────────────────────────────────────────────────

class ImageCard(QFrame):
    """A clickable card showing one cover-art candidate."""

    selected = pyqtSignal(int, bytes)   # (index, image_bytes)

    CARD_W, CARD_H = 210, 265
    IMG_SIZE       = 190

    def __init__(self, index: int, parent=None):
        super().__init__(parent)
        self.index      = index
        self.image_data = b''

        self.setObjectName('imageCard')
        self.setFixedSize(self.CARD_W, self.CARD_H)
        self.setCursor(Qt.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 6)
        layout.setSpacing(5)

        # Artwork area
        self.img_label = QLabel('⏳')
        self.img_label.setAlignment(Qt.AlignCenter)
        self.img_label.setFixedSize(self.IMG_SIZE, self.IMG_SIZE)
        self.img_label.setStyleSheet(
            'background:#0a0a14; border-radius:6px; color:#4a5568; font-size:22px;'
        )

        # Text info
        self.title_lbl = QLabel('—')
        self.title_lbl.setAlignment(Qt.AlignCenter)
        self.title_lbl.setWordWrap(True)
        self.title_lbl.setFixedHeight(30)
        self.title_lbl.setStyleSheet('color:#e2e8f0; font-size:11px; font-weight:600;')

        self.artist_lbl = QLabel('—')
        self.artist_lbl.setAlignment(Qt.AlignCenter)
        self.artist_lbl.setFixedHeight(18)
        self.artist_lbl.setStyleSheet('color:#4a5568; font-size:10px;')

        layout.addWidget(self.img_label)
        layout.addWidget(self.title_lbl)
        layout.addWidget(self.artist_lbl)

    # ── public API ────────────────────────────────────────────────────────────

    def set_image(self, data: bytes):
        self.image_data = data
        px = load_cropped_pixmap(data, self.IMG_SIZE)
        if px:
            self.img_label.setPixmap(px)
            self.img_label.setStyleSheet('background:#0a0a14; border-radius:6px;')

    def set_info(self, title: str, artist: str):
        self.title_lbl.setText(_trunc(title, 40))
        self.artist_lbl.setText(_trunc(artist, 38))

    def set_error(self):
        self.img_label.setText('✗')
        self.img_label.setStyleSheet(
            'background:#1a0d0d; border-radius:6px; color:#ef4444; font-size:24px;'
        )
        self.title_lbl.setText('Not found')
        self.title_lbl.setStyleSheet('color:#64748b; font-size:11px;')

    def mark_selected(self, yes: bool):
        name = 'imageCardSelected' if yes else 'imageCard'
        self.setObjectName(name)
        self.style().unpolish(self)
        self.style().polish(self)

    # ── event ─────────────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if self.image_data:
            self.selected.emit(self.index, self.image_data)


# ── Image Picker Dialog ───────────────────────────────────────────────────────

class ImagePickerDialog(QDialog):
    """
    Shows up to 6 cover-art candidates fetched from the internet.
    Call get_selected_image() after exec_() to retrieve the chosen bytes.
    """

    def __init__(self, song: dict, parent=None):
        super().__init__(parent)
        self.song            = song
        self.selected_image  = b''
        self.cards:          list[ImageCard] = []
        self._search_worker  = None
        self._dl_workers:    list[ImageDownloadWorker] = []

        self.setObjectName('imagePickerDialog')
        self.setWindowTitle('Select Cover Art')
        self.setModal(True)
        if parent:
            self.resize(int(parent.width() * 0.6), int(parent.height() * 0.7))
        else:
            self.resize(720, 560)
        self.setMinimumSize(520, 400)

        self._build_ui()
        self._start_search()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 22, 22, 18)
        layout.setSpacing(14)

        # Title
        title = QLabel(f'Cover art for: {_trunc(self.song.get("title","Unknown"), 60)}')
        title.setObjectName('dialogTitle')
        layout.addWidget(title)

        # Query hint
        q = self._make_query()
        self._subtitle = QLabel(f'Searching: {q}')
        self._subtitle.setObjectName('dialogSubtitle')
        layout.addWidget(self._subtitle)

        # Indeterminate progress bar
        self._pbar = QProgressBar()
        self._pbar.setRange(0, 0)
        self._pbar.setFixedHeight(4)
        layout.addWidget(self._pbar)

        # 3 × 2 grid of cards
        grid_w = QWidget()
        grid   = QGridLayout(grid_w)
        grid.setSpacing(12)
        grid.setContentsMargins(0, 0, 0, 0)

        for i in range(6):
            card = ImageCard(i)
            card.selected.connect(self._on_card_selected)
            self.cards.append(card)
            grid.addWidget(card, i // 3, i % 3)

        layout.addWidget(grid_w, 1)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self._btn_cancel = QPushButton('Cancel')
        self._btn_cancel.setObjectName('btnCancel')
        self._btn_cancel.clicked.connect(self.reject)

        self._btn_ok = QPushButton('Use Selected')
        self._btn_ok.setObjectName('btnOk')
        self._btn_ok.setEnabled(False)
        self._btn_ok.clicked.connect(self.accept)

        btn_row.addWidget(self._btn_cancel)
        btn_row.addSpacing(8)
        btn_row.addWidget(self._btn_ok)
        layout.addLayout(btn_row)

    # ── helpers ───────────────────────────────────────────────────────────────

    def _make_query(self) -> str:
        return build_query(
            title=self.song.get('title', ''),
            artist=self.song.get('artist', ''),
            album=self.song.get('album', ''),
        ) or Path(self.song.get('filename', '')).stem

    def _start_search(self):
        self._search_worker = SearchWorker(self._make_query(), limit=6)
        self._search_worker.results_ready.connect(self._on_results)
        self._search_worker.error.connect(self._on_search_error)
        self._search_worker.start()

    # ── slots ─────────────────────────────────────────────────────────────────

    def _on_results(self, results: list):
        self._pbar.setRange(0, 1)
        self._pbar.setValue(1)
        self._pbar.hide()

        for i, r in enumerate(results[:6]):
            self.cards[i].set_info(r.get('title', ''), r.get('artist', ''))
            w = ImageDownloadWorker(i, r['artwork_url'])
            w.image_ready.connect(self._on_img_ready)
            w.error.connect(self._on_img_error)
            w.start()
            self._dl_workers.append(w)

    def _on_search_error(self, msg: str):
        self._pbar.hide()
        self._subtitle.setText(msg)
        for card in self.cards:
            card.set_error()

    def _on_img_ready(self, idx: int, data: bytes):
        if idx < len(self.cards):
            self.cards[idx].set_image(data)

    def _on_img_error(self, idx: int, _msg: str):
        if idx < len(self.cards):
            self.cards[idx].set_error()

    def _on_card_selected(self, idx: int, data: bytes):
        for i, c in enumerate(self.cards):
            c.mark_selected(i == idx)
        self.selected_image = data
        self._btn_ok.setEnabled(True)

    # ── public ────────────────────────────────────────────────────────────────

    def get_selected_image(self) -> bytes:
        return self.selected_image

    # ── cleanup ───────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        for w in [self._search_worker] + self._dl_workers:
            if w and w.isRunning():
                w.quit()
                w.wait(500)
        super().closeEvent(event)


# ── Batch Progress Dialog ─────────────────────────────────────────────────────

class ProgressDialog(QDialog):
    """Non-blocking progress dialog shown during auto-assign."""

    cancelled = pyqtSignal()

    _COVER_SIZE = 140

    def __init__(self, title: str = 'Processing…', parent=None):
        super().__init__(parent)
        self.setObjectName('progressDialog')
        self.setWindowTitle(title)
        self.setModal(True)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowCloseButtonHint)
        self.resize(580, 192)
        self.setFixedSize(580, 192)

        # ── outer: image left | info right ───────────────────────────────────
        outer = QHBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.setSpacing(18)

        # Cover preview
        self._cover_lbl = QLabel('♫')
        self._cover_lbl.setAlignment(Qt.AlignCenter)
        self._cover_lbl.setFixedSize(self._COVER_SIZE, self._COVER_SIZE)
        self._cover_lbl.setStyleSheet(
            'background:#0a0a14; border-radius:8px; color:#2d2d4e; font-size:30px;'
        )
        outer.addWidget(self._cover_lbl)

        # Right-side info
        right = QVBoxLayout()
        right.setSpacing(8)

        self._title_lbl = QLabel(title)
        self._title_lbl.setObjectName('progressTitle')
        right.addWidget(self._title_lbl)

        self._song_lbl = QLabel('Starting…')
        self._song_lbl.setObjectName('progressSong')
        right.addWidget(self._song_lbl)

        self._pbar = QProgressBar()
        self._pbar.setFixedHeight(8)
        right.addWidget(self._pbar)

        bottom_row = QHBoxLayout()
        self._count_lbl = QLabel('')
        self._count_lbl.setStyleSheet('color:#4a5568; font-size:11px;')
        bottom_row.addWidget(self._count_lbl, 1)

        self._btn_cancel = QPushButton('Cancel')
        self._btn_cancel.setObjectName('btnCancel')
        self._btn_cancel.clicked.connect(self._do_cancel)
        bottom_row.addWidget(self._btn_cancel)
        right.addLayout(bottom_row)

        outer.addLayout(right, 1)

    def update_progress(self, current: int, total: int, song_name: str):
        self._pbar.setRange(0, total)
        self._pbar.setValue(current)
        self._song_lbl.setText(_trunc(song_name, 52))
        self._count_lbl.setText(f'{current} / {total}')

    def update_image(self, data: bytes):
        """Show the newly assigned cover art in the preview area."""
        px = load_cropped_pixmap(data, self._COVER_SIZE)
        if px:
            self._cover_lbl.setPixmap(px)
            self._cover_lbl.setStyleSheet(
                'background:#0a0a14; border-radius:8px;'
            )

    def _do_cancel(self):
        self._btn_cancel.setEnabled(False)
        self._btn_cancel.setText('Cancelling…')
        self.cancelled.emit()


# ── Metadata Review Dialog ───────────────────────────────────────────────────

class MetadataReviewDialog(QDialog):
    """
    Shows a table of proposed filename → tag changes.
    Artist, Title and Album cells are editable before the user confirms.
    Call get_changes() after exec_() returns Accepted.
    """

    _COL_FILE   = 0
    _COL_ARTIST = 1
    _COL_TITLE  = 2
    _COL_ALBUM  = 3

    def __init__(self, proposals: list, parent=None):
        """proposals: list of (path, song_dict, artist, title, album)"""
        super().__init__(parent)
        self._proposals = proposals
        self.setWindowTitle('Review Metadata Changes')
        self.setModal(True)
        if parent:
            self.resize(int(parent.width() * 0.75), int(parent.height() * 0.65))
        else:
            self.resize(980, 520)
        self.setMinimumSize(600, 340)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 16)
        layout.setSpacing(12)

        lbl_title = QLabel('Review & Edit Proposed Tag Changes')
        lbl_title.setObjectName('dialogTitle')
        layout.addWidget(lbl_title)

        lbl_sub = QLabel(
            'Artist, Title and Album columns are editable (double-click). '
            'Filename is never modified. Click Apply to write metadata.'
        )
        lbl_sub.setObjectName('dialogSubtitle')
        lbl_sub.setWordWrap(True)
        layout.addWidget(lbl_sub)

        # Table
        self._tbl = QTableWidget(len(self._proposals), 4)
        self._tbl.setHorizontalHeaderLabels([
            'Filename', 'Artist  (editable)', 'Title  (editable)', 'Album  (editable)'
        ])
        self._tbl.setAlternatingRowColors(True)
        self._tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._tbl.setEditTriggers(
            QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
        )
        self._tbl.verticalHeader().setVisible(False)
        self._tbl.setShowGrid(False)

        hdr = self._tbl.horizontalHeader()
        hdr.setSectionResizeMode(self._COL_FILE,   QHeaderView.Stretch)
        hdr.setSectionResizeMode(self._COL_ARTIST, QHeaderView.Interactive)
        hdr.setSectionResizeMode(self._COL_TITLE,  QHeaderView.Interactive)
        hdr.setSectionResizeMode(self._COL_ALBUM,  QHeaderView.Interactive)
        self._tbl.setColumnWidth(self._COL_ARTIST, 170)
        self._tbl.setColumnWidth(self._COL_TITLE,  190)
        self._tbl.setColumnWidth(self._COL_ALBUM,  170)
        self._tbl.setIconSize(QSize(0, 0))

        for row, (path, song, artist, title, album) in enumerate(self._proposals):
            fname = QTableWidgetItem(song['filename'])
            fname.setFlags(fname.flags() & ~Qt.ItemIsEditable)
            fname.setForeground(QColor('#64748b'))
            fname.setData(Qt.UserRole, path)
            self._tbl.setItem(row, self._COL_FILE,   fname)
            self._tbl.setItem(row, self._COL_ARTIST, QTableWidgetItem(artist))
            self._tbl.setItem(row, self._COL_TITLE,  QTableWidgetItem(title))
            self._tbl.setItem(row, self._COL_ALBUM,  QTableWidgetItem(album))

        layout.addWidget(self._tbl, 1)

        # Buttons
        btn_row = QHBoxLayout()

        btn_all = QPushButton('Select All')
        btn_all.setObjectName('btnCancel')
        btn_all.setFixedWidth(100)
        btn_all.clicked.connect(self._tbl.selectAll)
        btn_row.addWidget(btn_all)
        btn_row.addStretch()

        btn_cancel = QPushButton('Cancel')
        btn_cancel.setObjectName('btnCancel')
        btn_cancel.clicked.connect(self.reject)

        btn_apply = QPushButton('Apply All')
        btn_apply.setObjectName('btnOk')
        btn_apply.clicked.connect(self.accept)

        btn_row.addWidget(btn_cancel)
        btn_row.addSpacing(8)
        btn_row.addWidget(btn_apply)
        layout.addLayout(btn_row)

    def get_changes(self) -> list:
        """Return list of (path, artist, title, album) for every row."""
        out = []
        for row in range(self._tbl.rowCount()):
            p  = self._tbl.item(row, self._COL_FILE)
            a  = self._tbl.item(row, self._COL_ARTIST)
            t  = self._tbl.item(row, self._COL_TITLE)
            al = self._tbl.item(row, self._COL_ALBUM)
            if p and a and t and al:
                out.append((
                    p.data(Qt.UserRole),
                    a.text().strip(),
                    t.text().strip(),
                    al.text().strip(),
                ))
        return out


# ── Playlist Assignment Dialog ───────────────────────────────────────────────

class SongRenameDialog(QDialog):
    """Rename a single audio file. Used inline from the tag-edit dialog
    so the user can resolve song↔JSON name mismatches without leaving
    the assignment flow.
    """

    def __init__(
        self,
        song_path: str,
        current_filename: str,
        json_filename: str = '',
        parent=None,
    ):
        super().__init__(parent)
        self._song_path = song_path
        self._current_filename = current_filename
        self._json_filename = json_filename
        self._result_filename = ''

        self.setWindowTitle('Rename Song File')
        self.setModal(True)
        self.resize(560, 240)
        self.setMinimumSize(420, 220)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 14)
        layout.setSpacing(10)

        title = QLabel('Rename audio file')
        title.setObjectName('dialogTitle')
        layout.addWidget(title)

        path_lbl = QLabel(f'Folder: {Path(self._song_path).parent}')
        path_lbl.setObjectName('dialogSubtitle')
        path_lbl.setWordWrap(True)
        layout.addWidget(path_lbl)

        layout.addWidget(QLabel('Current filename:'))
        cur = QLabel(self._current_filename or '—')
        cur.setObjectName('dialogSubtitle')
        cur.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(cur)

        if self._json_filename:
            layout.addWidget(QLabel('JSON entry filename:'))
            js = QLabel(self._json_filename)
            js.setObjectName('dialogSubtitle')
            js.setTextInteractionFlags(Qt.TextSelectableByMouse)
            layout.addWidget(js)

        layout.addWidget(QLabel('New filename:'))
        self._edit = QLineEdit(self._current_filename)
        layout.addWidget(self._edit)

        quick = QHBoxLayout()
        quick.addStretch()
        if self._json_filename and self._json_filename != self._current_filename:
            btn_use_json = QPushButton('Use JSON name')
            btn_use_json.setObjectName('btnCancel')
            btn_use_json.clicked.connect(lambda: self._edit.setText(self._json_filename))
            quick.addWidget(btn_use_json)
        btn_use_current = QPushButton('Use file name')
        btn_use_current.setObjectName('btnCancel')
        btn_use_current.clicked.connect(lambda: self._edit.setText(self._current_filename))
        quick.addWidget(btn_use_current)
        layout.addLayout(quick)

        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_cancel = QPushButton('Cancel')
        btn_cancel.setObjectName('btnCancel')
        btn_cancel.clicked.connect(self.reject)
        btn_ok = QPushButton('OK')
        btn_ok.setObjectName('btnOk')
        btn_ok.clicked.connect(self._on_accept)
        btn_row.addWidget(btn_cancel)
        btn_row.addSpacing(8)
        btn_row.addWidget(btn_ok)
        layout.addLayout(btn_row)

    def _on_accept(self):
        text = self._edit.text().strip()
        if not text:
            return
        if '/' in text or '\\' in text:
            return
        self._result_filename = text
        self.accept()

    def new_filename(self) -> str:
        return self._result_filename


class PlaylistAssignmentDialog(QDialog):
    """Assign one or more tags to the current song selection."""

    def __init__(
        self,
        songs: list[dict],
        tag_icons: dict[str, str],
        existing_tags: dict[str, list[str]],
        json_path: str,
        theme: dict | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._theme = theme or (getattr(parent, '_theme', None) or {})
        self._songs = songs
        self._tag_icons = tag_icons
        self._existing_tags = existing_tags
        self._json_path = json_path
        self._tag_items: list[QListWidgetItem] = []

        self.setWindowTitle('Assign Tags')
        self.setModal(True)
        if parent:
            pw, ph = parent.width(), parent.height()
            self.resize(int(pw * 0.55), int(ph * 0.70))
        else:
            self.resize(680, 600)
        self.setMinimumSize(520, 420)
        # Theme-aware background sheet for child widgets.
        self.setStyleSheet(
            f'QWidget{{background:{self._theme.get("bg", "#f8fafc")};'
            f'color:{self._theme.get("text", "#1e293b")};}}'
            f'QLineEdit{{background:{self._theme.get("bg_input", "#ffffff")};'
            f'color:{self._theme.get("text", "#1e293b")};'
            f'border:1px solid {self._theme.get("border", "#e2e8f0")};'
            f'border-radius:6px;padding:6px 10px;}}'
        )
        self._build_ui()
        _apply_overlay_chrome(self, self._theme)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 16)
        layout.setSpacing(12)

        lbl_title = QLabel('Assign Tags to Selected Songs')
        lbl_title.setObjectName('dialogTitle')
        layout.addWidget(lbl_title)

        json_name = Path(self._json_path).name if self._json_path else 'No JSON selected'
        lbl_sub = QLabel(
            f'{len(self._songs)} selected song(s) · Editing {json_name}\n'
            'Checked: all selected songs · Filled square: some selected songs · Empty: none'
        )
        lbl_sub.setObjectName('dialogSubtitle')
        lbl_sub.setWordWrap(True)
        layout.addWidget(lbl_sub)

        # Preview of tags that will be applied (rebuilt on every check change)
        preview_lbl = QLabel('Currently selected tags')
        layout.addWidget(preview_lbl)

        self._selected_preview_scroll = QScrollArea()
        self._selected_preview_scroll.setObjectName('selectedTagsPreview')
        self._selected_preview_scroll.setWidgetResizable(True)
        self._selected_preview_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._selected_preview_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._selected_preview_scroll.setFixedHeight(46)
        self._selected_preview_host = QWidget()
        self._selected_preview_layout = QHBoxLayout(self._selected_preview_host)
        self._selected_preview_layout.setContentsMargins(6, 4, 6, 4)
        self._selected_preview_layout.setSpacing(6)
        self._selected_preview_layout.addStretch()
        self._selected_preview_scroll.setWidget(self._selected_preview_host)
        layout.addWidget(self._selected_preview_scroll)

        grid_title = QLabel('Available Tags')
        layout.addWidget(grid_title)

        self._filter = QLineEdit()
        self._filter.setPlaceholderText('Filter tags...')
        self._filter.textChanged.connect(self._apply_filter)
        layout.addWidget(self._filter)

        self._tag_list = QListWidget()
        self._tag_list.setObjectName('tagGrid')
        self._tag_list.setSelectionMode(QAbstractItemView.NoSelection)
        self._tag_list.setViewMode(QListWidget.IconMode)
        self._tag_list.setMovement(QListWidget.Static)
        self._tag_list.setResizeMode(QListWidget.Adjust)
        self._tag_list.setWrapping(True)
        self._tag_list.setWordWrap(True)
        self._tag_list.setSpacing(8)
        # scale icon/grid to dialog width — compact chip sizing
        dw = self.width()
        ico = max(28, min(48, int(dw * 0.035)))
        gw = max(90, min(150, int(dw * 0.11)))
        gh = max(54, min(80, int(dw * 0.06)))
        self._tag_list.setIconSize(QSize(ico, ico))
        self._tag_list.setGridSize(QSize(gw, gh))
        self._tag_list.setUniformItemSizes(True)
        self._tag_list.itemChanged.connect(self._on_tag_item_changed)
        self._tag_list.itemClicked.connect(self._on_tag_item_clicked)

        selected_count = max(len(self._songs), 1)
        for tag_name, icon_path in self._tag_icons.items():
            assigned = sum(
                1
                for song in self._songs
                if tag_name in self._existing_tags.get(song.get('path', ''), [])
            )
            if assigned == selected_count:
                state = Qt.Checked
            elif assigned:
                state = Qt.PartiallyChecked
            else:
                state = Qt.Unchecked

            icon = icon_from_file(icon_path, ico) if icon_path and Path(icon_path).exists() else QIcon()
            item = QListWidgetItem(icon, display_for(tag_name))
            item.setFlags((item.flags() | Qt.ItemIsEnabled) & ~Qt.ItemIsUserCheckable)
            item.setCheckState(state)
            item.setData(Qt.UserRole, tag_name)
            item.setData(Qt.UserRole + 1, int(state))
            item.setTextAlignment(Qt.AlignCenter)
            item.setToolTip(
                'Assigned to all selected songs' if state == Qt.Checked else
                'Assigned to some selected songs' if state == Qt.PartiallyChecked else
                'Not assigned to the current selection'
            )
            self._tag_list.addItem(item)
            self._tag_items.append(item)
            self._on_tag_item_changed(item)

        # Scroll vertically when there are more tags than fit. Previously the
        # grid was force-grown to show every chip (old _fit_tag_grid_height),
        # which overflowed this fixed-size dialog and pushed the song list and
        # the Cancel/Apply buttons up so they overlapped the grid.
        self._tag_list.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._tag_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._tag_list.setMinimumHeight(140)
        layout.addWidget(self._tag_list, 2)

        tag_btns = QHBoxLayout()
        btn_none = QPushButton('Clear All')
        btn_none.setObjectName('btnCancel')
        btn_none.clicked.connect(lambda: self._set_all_tags(Qt.Unchecked))

        tag_btns.addWidget(btn_none)
        tag_btns.addStretch()
        layout.addLayout(tag_btns)

        # Read-only list of songs the changes will apply to.
        layout.addWidget(QLabel('Selected Songs'))
        self._song_list = QListWidget()
        self._song_list.setObjectName('songGrid')
        self._song_list.setSelectionMode(QAbstractItemView.NoSelection)
        self._song_list.setMaximumHeight(140)
        for song in self._songs:
            item = QListWidgetItem()
            item.setData(Qt.UserRole, song.get('path', ''))
            self._song_list.addItem(item)
            self._refresh_song_item(item, song)
        layout.addWidget(self._song_list, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        btn_cancel = QPushButton('Cancel')
        btn_cancel.setObjectName('btnCancel')
        btn_cancel.clicked.connect(self.reject)

        btn_apply = QPushButton('Apply Tag Changes')
        btn_apply.setObjectName('btnOk')
        btn_apply.clicked.connect(self.accept)

        btn_row.addWidget(btn_cancel)
        btn_row.addSpacing(8)
        btn_row.addWidget(btn_apply)
        layout.addLayout(btn_row)

        # initial preview render (after items exist)
        self._refresh_selected_preview()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        dw = event.size().width()
        ico = max(28, min(48, int(dw * 0.035)))
        gw = max(90, min(150, int(dw * 0.11)))
        gh = max(54, min(80, int(dw * 0.06)))
        self._tag_list.setIconSize(QSize(ico, ico))
        self._tag_list.setGridSize(QSize(gw, gh))
        # Height is governed by the layout stretch + internal scrolling; do NOT
        # force it to fit every chip (that overflowed the dialog).

    def _refresh_selected_preview(self):
        """Rebuild the top chip strip from tags currently in Checked state."""
        # Clear existing chips, keep trailing stretch.
        while self._selected_preview_layout.count() > 1:
            item = self._selected_preview_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        # Add a chip for every Checked tag (skip Partial — those aren't applied).
        any_added = False
        for item in self._tag_items:
            if item.checkState() != Qt.Checked:
                continue
            tag_name = item.data(Qt.UserRole)
            icon_path = self._tag_icons.get(tag_name, '')
            chip = QLabel()
            chip.setObjectName('selectedTagChip')
            chip.setStyleSheet(
                'background:#2563eb;color:#ffffff;border-radius:11px;'
                'padding:3px 10px;font-weight:600;'
            )
            label = display_for(tag_name)
            if icon_path and Path(icon_path).exists():
                ico = icon_from_file(icon_path, 18)
                if not ico.isNull():
                    chip.setPixmap(ico.pixmap(18, 18))
                    chip.setText(' ' + label)
                else:
                    chip.setText(label)
            else:
                chip.setText(label)
            self._selected_preview_layout.insertWidget(
                self._selected_preview_layout.count() - 1, chip
            )
            any_added = True
        if not any_added:
            empty = QLabel('No tags selected')
            empty.setStyleSheet('color:#64748b;font-style:italic;')
            self._selected_preview_layout.insertWidget(
                self._selected_preview_layout.count() - 1, empty
            )

    def _apply_filter(self, text: str):
        needle = text.strip().lower()
        for item in self._tag_items:
            item.setHidden(bool(needle) and needle not in item.text().lower())

    def _set_all_tags(self, state: Qt.CheckState):
        for item in self._tag_items:
            item.setCheckState(state)
            self._on_tag_item_changed(item)

    def _on_tag_item_clicked(self, item: QListWidgetItem):
        """Toggle check state on click anywhere on the item."""
        if item.checkState() == Qt.Checked:
            item.setCheckState(Qt.Unchecked)
        else:
            item.setCheckState(Qt.Checked)

    def _on_tag_item_changed(self, item: QListWidgetItem):
        """Chip-style background: blue when checked, amber when partial, neutral otherwise."""
        font = item.font()
        state = item.checkState()
        if state == Qt.Checked:
            item.setBackground(QColor('#2563eb'))   # blue
            item.setForeground(QColor('#ffffff'))
            font.setBold(True)
        elif state == Qt.PartiallyChecked:
            item.setBackground(QColor('#f59e0b'))   # amber
            item.setForeground(QColor('#1a1a2e'))
            font.setBold(True)
        else:
            item.setBackground(QColor(0, 0, 0, 0))  # transparent
            item.setForeground(QColor('#e2e8f0'))
            font.setBold(False)
        item.setFont(font)
        # keep the live preview row in sync
        if hasattr(self, '_selected_preview_layout'):
            self._refresh_selected_preview()

    def _refresh_song_item(self, item: QListWidgetItem, song: dict):
        title = song.get('title') or song.get('filename', '')
        artist = song.get('artist', '')
        text = title
        if artist:
            text += f'\n{artist}'
        item.setText(text)
        item.setToolTip(song.get('path', ''))

    def get_tag_changes(self) -> tuple[set[str], set[str]]:
        add_tags: set[str] = set()
        remove_tags: set[str] = set()

        for row in range(self._tag_list.count()):
            item = self._tag_list.item(row)
            tag_name = item.data(Qt.UserRole)
            initial_state = int(item.data(Qt.UserRole + 1))
            final_state = item.checkState()

            if final_state == initial_state or final_state == Qt.PartiallyChecked:
                continue
            if final_state == Qt.Checked:
                add_tags.add(tag_name)
            elif final_state == Qt.Unchecked:
                remove_tags.add(tag_name)

        return add_tags, remove_tags


class PlaylistSongIssueDialog(QDialog):
    """Show JSON/file mismatch details for a specific song and allow quick fixes."""

    ACTION_NONE = ''
    ACTION_UPDATE_JSON = 'update_json'
    ACTION_REMOVE_INVALID_TAGS = 'remove_invalid_tags'
    ACTION_MERGE_DUPLICATES = 'merge_duplicates'

    def __init__(self, song: dict, issues: list[dict], current_tags: list[str], parent=None):
        super().__init__(parent)
        self._song = song
        self._issues = list(issues)
        self._current_tags = list(current_tags)
        self._selected_action = self.ACTION_NONE
        self._selected_issue_index = 0

        self.setWindowTitle('Song JSON Warning')
        self.setModal(True)
        if parent:
            self.resize(int(parent.width() * 0.62), int(parent.height() * 0.62))
        else:
            self.resize(760, 560)
        self.setMinimumSize(620, 460)
        self._build_ui()
        self._apply_issue(0)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 16)
        layout.setSpacing(12)

        title = self._song.get('title') or self._song.get('filename', 'Unknown song')
        lbl_title = QLabel(f'JSON Warning: {title}')
        lbl_title.setObjectName('dialogTitle')
        layout.addWidget(lbl_title)

        subtitle = QLabel(self._song.get('path', ''))
        subtitle.setObjectName('dialogSubtitle')
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        if len(self._issues) > 1:
            self._issue_selector = QComboBox()
            for issue in self._issues:
                self._issue_selector.addItem(_format_issue_heading(issue))
            self._issue_selector.currentIndexChanged.connect(self._apply_issue)
            layout.addWidget(self._issue_selector)
        else:
            self._issue_selector = None

        self._summary_lbl = QLabel('')
        self._summary_lbl.setObjectName('dialogSubtitle')
        self._summary_lbl.setWordWrap(True)
        layout.addWidget(self._summary_lbl)

        self._json_box = QTextEdit()
        self._json_box.setReadOnly(True)
        self._json_box.setObjectName('songGrid')
        self._json_box.setFont(QFont('Consolas', 10))

        self._file_box = QTextEdit()
        self._file_box.setReadOnly(True)
        self._file_box.setObjectName('songGrid')
        self._file_box.setFont(QFont('Consolas', 10))

        panes = QHBoxLayout()
        panes.addWidget(self._json_box, 1)
        panes.addWidget(self._file_box, 1)
        layout.addLayout(panes, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self._btn_fix = QPushButton('')
        self._btn_fix.setObjectName('btnOk')
        self._btn_fix.clicked.connect(self._on_fix_clicked)

        self._btn_close = QPushButton('Close')
        self._btn_close.setObjectName('btnCancel')
        self._btn_close.clicked.connect(self.reject)

        btn_row.addWidget(self._btn_close)
        btn_row.addSpacing(8)
        btn_row.addWidget(self._btn_fix)
        layout.addLayout(btn_row)

    def _apply_issue(self, index: int):
        if not self._issues:
            self._selected_issue_index = 0
            self._summary_lbl.setText('No issue details available.')
            self._json_box.setPlainText('No JSON issue selected.')
            self._file_box.setPlainText(self._format_file_text())
            self._btn_fix.hide()
            return

        index = max(0, min(index, len(self._issues) - 1))
        self._selected_issue_index = index
        issue = self._issues[index]

        self._summary_lbl.setText(_format_issue_summary(issue))
        self._json_box.setPlainText(self._format_json_text(issue))
        self._file_box.setPlainText(self._format_file_text())

        issue_type = issue.get('issue_type', '')
        kind = issue.get('kind', '')
        if issue_type == 'invalid_tag':
            self._btn_fix.setText('Remove Invalid Tags')
            self._btn_fix.show()
        elif issue_type == 'health' and kind == 'duplicate_entry':
            self._btn_fix.setText('Merge Duplicate Entries')
            self._btn_fix.show()
        elif issue_type == 'health' and kind == 'title_drift':
            self._btn_fix.setText('Sync JSON Title To File')
            self._btn_fix.show()
        elif issue_type == 'health':
            self._btn_fix.hide()
        elif issue.get('record_index', -1) >= 0:
            self._btn_fix.setText('Update JSON From This File')
            self._btn_fix.show()
        else:
            self._btn_fix.hide()

    def _format_json_text(self, issue: dict) -> str:
        lines = [
            'JSON contains:',
            f'  title: {issue.get("title", "") or "—"}',
        ]
        tags = issue.get('tags', [])
        if tags:
            lines.append(f'  tags: {", ".join(tags)}')
        elif issue.get('issue_type') == 'mismatch':
            lines.append('  tags: (not part of this mismatch report)')

        candidates = issue.get('candidates', [])
        if candidates:
            lines.append('')
            lines.append('Possible matches:')
            for candidate in candidates:
                lines.append(f'  - {candidate}')
        return '\n'.join(lines)

    def _format_file_text(self) -> str:
        tags = (
            ', '.join(display_for(t) for t in self._current_tags)
            if self._current_tags else '—'
        )
        return '\n'.join([
            'Current file has:',
            f'  path: {self._song.get("path", "") or "—"}',
            f'  filename: {self._song.get("filename", "") or "—"}',
            f'  title: {self._song.get("title", "") or "—"}',
            f'  artist: {self._song.get("artist", "") or "—"}',
            f'  album: {self._song.get("album", "") or "—"}',
            f'  tags: {tags}',
        ])

    def _on_fix_clicked(self):
        issue = self._issues[self._selected_issue_index] if self._issues else {}
        if issue.get('issue_type') == 'health' and issue.get('kind') == 'duplicate_entry':
            self._selected_action = self.ACTION_MERGE_DUPLICATES
            self.accept()
            return
        if issue.get('issue_type') == 'health' and issue.get('kind') == 'title_drift':
            self._selected_action = self.ACTION_UPDATE_JSON
            self.accept()
            return
        issue = self.selected_issue()
        if not issue:
            return
        if issue.get('issue_type') == 'invalid_tag':
            self._selected_action = self.ACTION_REMOVE_INVALID_TAGS
        else:
            self._selected_action = self.ACTION_UPDATE_JSON
        self.accept()

    def selected_action(self) -> str:
        return self._selected_action

    def selected_issue(self) -> dict:
        if not self._issues:
            return {}
        return self._issues[self._selected_issue_index]


def build_report_text(mismatch_records: list[dict], invalid_tag_records: list[dict],
                      duplicate_records: list[dict] | None = None,
                      image_decode_failures: int = 0) -> str:
    """Plain-text diagnostics (the old "JSON Report" body), kept behind the
    Details toggle of the Sync Check dialog and used by Copy report."""
    lines: list[str] = []
    if mismatch_records:
        lines.append('JSON mismatch records:')
        for entry in mismatch_records:
            lines.append(f'  - {_format_mismatch(entry)}')
    else:
        lines.append('JSON mismatch records: none')

    lines.append('')
    if duplicate_records:
        lines.append('Duplicate JSON entries:')
        for dup in duplicate_records:
            titles = ', '.join(f'"{t}"' for t in dup.get('titles', []))
            lines.append(f'  - {dup.get("disk_filename", "")}: {titles}')
    else:
        lines.append('Duplicate JSON entries: none')

    lines.append('')
    if invalid_tag_records:
        lines.append('Invalid tag groups:')
        for entry in invalid_tag_records:
            tags = ', '.join(entry.get('tags', []))
            lines.append(f'  - {entry.get("song", "Unknown")} -> {tags}')
    else:
        lines.append('Invalid tag groups: none')

    lines.append('')
    lines.append(f'Image decode failures: {image_decode_failures}')
    return '\n'.join(lines)


def _severity_pixmap(color: str, size: int = 12) -> QPixmap:
    px = QPixmap(size, size)
    px.fill(Qt.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawEllipse(1, 1, size - 2, size - 2)
    p.end()
    return px


class JsonHealthDialog(QDialog):
    """Sync Check — every discrepancy between the JSON, the music folder and
    the lyrics folder in one reviewable list. JSON-only fixes are ticked and
    applied in one go by the main window; disk changes are never automatic
    (filename spacing issues are flagged, .lrc renames need an explicit
    click)."""

    RESULT_NONE = ''
    RESULT_APPLY = 'apply'
    RESULT_OPEN_EDITOR = 'open_editor'
    RESULT_FIX_SPACING = 'fix_spacing'

    COL_CHECK, COL_SEV, COL_ISSUE, COL_JSON, COL_DISK, COL_TAGS = range(6)

    def __init__(self,
                 json_path: str,
                 issues: list[HealthIssue],
                 summary_text: str = '',
                 report_text: str = '',
                 theme: dict | None = None,
                 parent=None):
        super().__init__(parent)
        self._theme = theme or (getattr(parent, '_theme', None) or {})
        self._json_path = json_path
        self._issues = list(issues)
        self._summary_text = summary_text
        self._report_text = report_text
        self._filter = 'all'
        self._row_issue: list[HealthIssue] = []
        self.result_action = self.RESULT_NONE
        self.editor_request: tuple[str, str] = ('', '')
        self.spacing_fix_intents: dict[str, str] = {}
        self.lyrics_changed = False

        self.setWindowTitle('JSON Sync Check')
        self.setModal(True)
        if parent:
            self.resize(int(parent.width() * 0.82), int(parent.height() * 0.8))
        else:
            self.resize(1040, 720)
        self.setMinimumSize(760, 520)
        self._build_ui()
        self._populate()
        _apply_overlay_chrome(self, self._theme)

    # ── ui ───────────────────────────────────────────────────────────────────

    def _build_ui(self):
        t = self._theme
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 16)
        layout.setSpacing(10)

        head = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        lbl_title = QLabel('JSON Sync Check')
        lbl_title.setObjectName('dialogTitle')
        title_col.addWidget(lbl_title)
        lbl_path = QLabel(self._json_path or 'No JSON selected')
        lbl_path.setObjectName('dialogSubtitle')
        lbl_path.setWordWrap(True)
        title_col.addWidget(lbl_path)
        head.addLayout(title_col, 1)

        counts = summarize(self._issues)
        self._chips = QHBoxLayout()
        self._chips.setSpacing(6)
        for key, label, color in (
            ('fixable', 'fixable', t.get('accent', '#007aff')),
            ('error', 'errors', t.get('red', '#ff3b30')),
            ('warning', 'warnings', t.get('orange', '#ff9500')),
            ('info', 'info', t.get('text_dim', '#8e8e93')),
        ):
            chip = QLabel(f'{counts.get(key, 0)} {label}')
            chip.setObjectName('healthChip')
            chip.setStyleSheet(
                f'QLabel#healthChip{{color:{color};border:1px solid {color};'
                f'border-radius:10px;padding:2px 10px;font-size:12px;font-weight:600;}}'
            )
            self._chips.addWidget(chip)
        head.addLayout(self._chips)
        layout.addLayout(head)

        intro = QLabel(
            'Other apps look songs up by the JSON title, compared exactly with the '
            'filename on disk. Ticked rows are JSON-only fixes; files on disk are '
            'never renamed here.'
        )
        intro.setObjectName('dialogSubtitle')
        intro.setWordWrap(True)
        layout.addWidget(intro)

        seg = QHBoxLayout()
        seg.setSpacing(0)
        self._seg_group = QButtonGroup(self)
        self._seg_group.setExclusive(True)
        for i, (key, label) in enumerate((
            ('all', 'All'), ('fixable', 'Fixable'),
            ('warning', 'Warnings'), ('info', 'Info'),
        )):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setChecked(key == 'all')
            btn.setCursor(Qt.PointingHandCursor)
            btn.setObjectName('btnSegLeft' if i == 0 else ('btnSegRight' if i == 3 else 'btnSegMid'))
            btn.clicked.connect(lambda _=False, k=key: self._set_filter(k))
            self._seg_group.addButton(btn)
            seg.addWidget(btn)
        seg.addStretch(1)
        self._search = QLineEdit()
        self._search.setObjectName('searchBar')
        self._search.setPlaceholderText('Filter by name…')
        self._search.setClearButtonEnabled(True)
        self._search.setMaximumWidth(280)
        self._search.textChanged.connect(lambda _t: self._populate())
        seg.addWidget(self._search)
        layout.addLayout(seg)

        self._table = QTableWidget(0, 6)
        self._table.setObjectName('healthTable')
        self._table.setHorizontalHeaderLabels(['', '', 'Issue', 'JSON title', 'On disk / suggestion', 'Tags'])
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.setShowGrid(False)
        self._table.setAlternatingRowColors(False)
        self._table.setWordWrap(False)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(self.COL_CHECK, QHeaderView.Fixed)
        hdr.setSectionResizeMode(self.COL_SEV, QHeaderView.Fixed)
        hdr.setSectionResizeMode(self.COL_ISSUE, QHeaderView.Interactive)
        hdr.setSectionResizeMode(self.COL_JSON, QHeaderView.Stretch)
        hdr.setSectionResizeMode(self.COL_DISK, QHeaderView.Stretch)
        hdr.setSectionResizeMode(self.COL_TAGS, QHeaderView.Interactive)
        self._table.setColumnWidth(self.COL_CHECK, 34)
        self._table.setColumnWidth(self.COL_SEV, 28)
        self._table.setColumnWidth(self.COL_ISSUE, 210)
        self._table.setColumnWidth(self.COL_TAGS, 150)
        self._table.itemSelectionChanged.connect(self._update_buttons)
        self._table.itemChanged.connect(self._on_item_changed)
        self._table.itemDoubleClicked.connect(lambda _i: self._on_open_editor())
        layout.addWidget(self._table, 1)

        self._detail = QLabel('')
        self._detail.setObjectName('dialogSubtitle')
        self._detail.setWordWrap(True)
        self._detail.setMinimumHeight(34)
        layout.addWidget(self._detail)

        self._report_box = QTextEdit()
        self._report_box.setReadOnly(True)
        self._report_box.setObjectName('songGrid')
        self._report_box.setFont(QFont('Menlo', 10))
        self._report_box.setPlainText(self._report_text)
        self._report_box.setMaximumHeight(160)
        self._report_box.hide()
        layout.addWidget(self._report_box)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        self._btn_select_all = QPushButton('Select all fixable')
        self._btn_select_all.setObjectName('btnSecondary')
        self._btn_select_all.clicked.connect(lambda: self._set_all_checks(True))
        self._btn_select_none = QPushButton('Clear')
        self._btn_select_none.setObjectName('btnSecondary')
        self._btn_select_none.clicked.connect(lambda: self._set_all_checks(False))
        self._btn_editor = QPushButton('Open in Edit Song')
        self._btn_editor.setObjectName('btnSecondary')
        self._btn_editor.clicked.connect(self._on_open_editor)
        self._btn_rename_lrc = QPushButton('Rename .lrc')
        self._btn_rename_lrc.setObjectName('btnSecondary')
        self._btn_rename_lrc.clicked.connect(self._on_rename_lrc)
        self._btn_fix_spacing = QPushButton('Fix All Spacing…')
        self._btn_fix_spacing.setObjectName('btnSecondary')
        self._btn_fix_spacing.setToolTip(
            'Review and rename every file with a spacing error (e.g. "title -Song" → '
            '"title - Song") in one batch, with a before/after list to check first.'
        )
        self._btn_fix_spacing.clicked.connect(self._on_fix_spacing)
        self._btn_details = QPushButton('Details')
        self._btn_details.setObjectName('btnSecondary')
        self._btn_details.setCheckable(True)
        self._btn_details.toggled.connect(self._report_box.setVisible)
        self._btn_copy = QPushButton('Copy report')
        self._btn_copy.setObjectName('btnSecondary')
        self._btn_copy.clicked.connect(self._on_copy)
        self._btn_close = QPushButton('Close')
        self._btn_close.setObjectName('btnSecondary')
        self._btn_close.clicked.connect(self.reject)
        self._btn_apply = QPushButton('Apply 0 fixes')
        self._btn_apply.setObjectName('btnPrimary')
        self._btn_apply.clicked.connect(self._on_apply)
        for b in (self._btn_select_all, self._btn_select_none, self._btn_editor,
                  self._btn_rename_lrc, self._btn_fix_spacing, self._btn_details, self._btn_copy):
            footer.addWidget(b)
        footer.addStretch(1)
        footer.addWidget(self._btn_close)
        footer.addWidget(self._btn_apply)
        layout.addLayout(footer)

    # ── population ───────────────────────────────────────────────────────────

    def _visible_issues(self) -> list[HealthIssue]:
        q = self._search.text().strip().casefold() if hasattr(self, '_search') else ''
        out = []
        for issue in self._issues:
            if self._filter == 'fixable' and not issue.fixable:
                continue
            if self._filter == 'warning' and issue.severity != 'warning':
                continue
            if self._filter == 'info' and issue.severity != 'info':
                continue
            if q and q not in (issue.json_title + ' ' + issue.disk_filename + ' '
                               + issue.suggestion + ' ' + issue.message).casefold():
                continue
            out.append(issue)
        return out

    def _populate(self):
        t = self._theme
        sev_color = {
            'error': t.get('red', '#ff3b30'),
            'warning': t.get('orange', '#ff9500'),
            'info': t.get('text_dim', '#8e8e93'),
        }
        dim = t.get('text_dim', '#8e8e93')
        checked_state = {id(i): i.default_checked for i in self._issues}
        for row, issue in enumerate(self._row_issue):
            item = self._table.item(row, self.COL_CHECK)
            if item is not None and issue.fixable:
                checked_state[id(issue)] = item.checkState() == Qt.Checked

        self._table.blockSignals(True)
        self._table.setRowCount(0)
        self._row_issue = []
        for issue in self._visible_issues():
            row = self._table.rowCount()
            self._table.insertRow(row)
            self._table.setRowHeight(row, 30)

            chk = QTableWidgetItem()
            if issue.fixable:
                chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                chk.setCheckState(Qt.Checked if checked_state.get(id(issue), issue.default_checked) else Qt.Unchecked)
            else:
                chk.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self._table.setItem(row, self.COL_CHECK, chk)

            sev = QTableWidgetItem()
            sev.setIcon(QIcon(_severity_pixmap(sev_color.get(issue.severity, dim))))
            sev.setToolTip(issue.severity)
            self._table.setItem(row, self.COL_SEV, sev)

            lbl = QTableWidgetItem(issue.label)
            lbl.setToolTip(issue.message)
            self._table.setItem(row, self.COL_ISSUE, lbl)

            js = QTableWidgetItem(issue.json_title or '—')
            js.setToolTip(issue.json_title)
            self._table.setItem(row, self.COL_JSON, js)

            disk_text = issue.suggestion or issue.disk_filename or '—'
            if issue.kind == 'duplicate_entry':
                disk_text = f'merge → "{issue.suggestion}"'
            elif issue.kind == 'missing_file' and issue.fixable:
                disk_text = f'relink → "{issue.suggestion}"'
            elif issue.kind == 'disk_separator_spacing':
                disk_text = f'file "{issue.disk_filename}" → suggest "{issue.suggestion}"'
            dk = QTableWidgetItem(disk_text)
            dk.setToolTip(issue.message)
            if not issue.fixable:
                dk.setForeground(QColor(dim))
            self._table.setItem(row, self.COL_DISK, dk)

            tg = QTableWidgetItem(', '.join(display_for(x) for x in issue.tags) if issue.tags else '')
            tg.setForeground(QColor(dim))
            self._table.setItem(row, self.COL_TAGS, tg)
            self._row_issue.append(issue)
        self._table.blockSignals(False)
        self._update_buttons()

    def _set_filter(self, key: str):
        self._filter = key
        self._populate()

    def _set_all_checks(self, on: bool):
        self._table.blockSignals(True)
        for row, issue in enumerate(self._row_issue):
            if not issue.fixable:
                continue
            item = self._table.item(row, self.COL_CHECK)
            if item is not None:
                item.setCheckState(Qt.Checked if on else Qt.Unchecked)
        self._table.blockSignals(False)
        self._update_buttons()

    def _on_item_changed(self, _item):
        self._update_buttons()

    def _current_issue(self) -> HealthIssue | None:
        rows = {i.row() for i in self._table.selectedIndexes()}
        if not rows:
            return None
        row = min(rows)
        return self._row_issue[row] if 0 <= row < len(self._row_issue) else None

    def _update_buttons(self):
        n = len(self.selected_fixes())
        self._btn_apply.setText(f'Apply {n} fix{"" if n == 1 else "es"}')
        self._btn_apply.setEnabled(n > 0)
        cur = self._current_issue()
        self._btn_editor.setEnabled(bool(cur and cur.song_path and not cur.song_path.startswith('__missing__')))
        self._btn_rename_lrc.setVisible(bool(cur and cur.kind == 'lyrics_drift'))
        spacing_count = sum(1 for i in self._issues if i.kind == 'disk_separator_spacing')
        self._btn_fix_spacing.setVisible(spacing_count > 0)
        if spacing_count:
            self._btn_fix_spacing.setText(f'Fix All Spacing ({spacing_count})…')
        self._detail.setText(cur.message if cur else '')

    # ── actions ──────────────────────────────────────────────────────────────

    def selected_fixes(self) -> list[HealthIssue]:
        picked: list[HealthIssue] = []
        for row, issue in enumerate(self._row_issue):
            if not issue.fixable:
                continue
            item = self._table.item(row, self.COL_CHECK)
            if item is not None and item.checkState() == Qt.Checked:
                picked.append(issue)
        # Issues hidden by the current filter keep their default/last state.
        visible = {id(i) for i in self._row_issue}
        for issue in self._issues:
            if issue.fixable and id(issue) not in visible and issue.default_checked:
                picked.append(issue)
        return picked

    def _on_apply(self):
        if not self.selected_fixes():
            return
        self.result_action = self.RESULT_APPLY
        # Fade the list out before handing the fixes back (the rows are
        # about to disappear from the JSON anyway).
        from PyQt5.QtWidgets import QGraphicsOpacityEffect
        from PyQt5.QtCore import QPropertyAnimation, QEasingCurve
        effect = QGraphicsOpacityEffect(self._table)
        self._table.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b'opacity', self)
        anim.setDuration(160)
        anim.setStartValue(1.0)
        anim.setEndValue(0.25)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.finished.connect(self.accept)
        self._fade_anim = anim
        anim.start()

    def _on_open_editor(self):
        cur = self._current_issue()
        if not cur or not cur.song_path:
            return
        suggestion = cur.suggestion if cur.kind == 'disk_separator_spacing' else ''
        self.editor_request = (cur.song_path, suggestion)
        self.result_action = self.RESULT_OPEN_EDITOR
        self.accept()

    def _on_rename_lrc(self):
        cur = self._current_issue()
        if not cur or cur.kind != 'lyrics_drift':
            return
        if rename_lrc_for_issue(cur):
            self.lyrics_changed = True
            self._issues = [i for i in self._issues if i is not cur]
            self._populate()
            self._detail.setText(f'Renamed lyrics file to "{cur.suggestion}".')
        else:
            QMessageBox.warning(
                self, 'Rename .lrc',
                f'Could not rename the lyrics file to "{cur.suggestion}" '
                f'(destination may already exist).'
            )

    def _on_copy(self):
        lines = [f'JSON Sync Check — {self._json_path}', self._summary_text, '']
        for issue in self._issues:
            lines.append(f'[{issue.severity}] {issue.label}: {issue.message}')
        lines.append('')
        lines.append(self._report_text)
        QApplication.clipboard().setText('\n'.join(lines))
        self._detail.setText('Report copied to the clipboard.')

    def _on_fix_spacing(self):
        spacing_issues = [i for i in self._issues if i.kind == 'disk_separator_spacing']
        if not spacing_issues:
            return
        dlg = SpacingFixDialog(spacing_issues, self._theme, self)
        if dlg.exec_() != QDialog.Accepted:
            return
        intents = dlg.selected_intents()
        if not intents:
            return
        self.spacing_fix_intents = intents
        self.result_action = self.RESULT_FIX_SPACING
        self.accept()


class SpacingFixDialog(QDialog):
    """Recap + selection sheet for "Fix all spacing at once": every filename
    with a spacing error (``title -Song`` → ``title - Song``, an easy, very
    common mistake) listed before → after, each with its own checkbox so the
    user can review — and deselect — before anything on disk is touched.
    Renaming, if confirmed, still goes through the normal single-song rename
    path (updates the JSON title and the .lrc together), just for many
    files in one step instead of one Edit Song visit each."""

    COL_CHECK, COL_BEFORE, COL_ARROW, COL_AFTER = range(4)

    def __init__(self, issues: list, theme: dict | None = None, parent=None):
        super().__init__(parent)
        self._theme = theme or (getattr(parent, '_theme', None) or {})
        self._issues = list(issues)
        self._row_data: list[tuple] = []   # (song_path, suggestion, conflict: bool)

        self.setWindowTitle('Fix Filename Spacing')
        self.setModal(True)
        if parent:
            self.resize(int(parent.width() * 0.7), int(parent.height() * 0.7))
        else:
            self.resize(760, 560)
        self.setMinimumSize(560, 420)
        self._build_ui()
        self._populate()
        _apply_overlay_chrome(self, self._theme)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 16)
        layout.setSpacing(10)

        title = QLabel('Fix Filename Spacing')
        title.setObjectName('dialogTitle')
        layout.addWidget(title)
        sub = QLabel(
            'Review every rename below, then apply the ones you want. Each file is '
            'renamed on disk, and its JSON title and lyrics file are updated to match.'
        )
        sub.setObjectName('dialogSubtitle')
        sub.setWordWrap(True)
        layout.addWidget(sub)

        self._table = QTableWidget(0, 4)
        self._table.setObjectName('healthTable')
        self._table.setHorizontalHeaderLabels(['', 'Current name', '', 'Fixed name'])
        self._table.setSelectionMode(QAbstractItemView.NoSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.setShowGrid(False)
        self._table.setWordWrap(False)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(self.COL_CHECK, QHeaderView.Fixed)
        hdr.setSectionResizeMode(self.COL_BEFORE, QHeaderView.Stretch)
        hdr.setSectionResizeMode(self.COL_ARROW, QHeaderView.Fixed)
        hdr.setSectionResizeMode(self.COL_AFTER, QHeaderView.Stretch)
        self._table.setColumnWidth(self.COL_CHECK, 34)
        self._table.setColumnWidth(self.COL_ARROW, 28)
        layout.addWidget(self._table, 1)

        self._conflict_lbl = QLabel('')
        self._conflict_lbl.setObjectName('dialogSubtitle')
        self._conflict_lbl.setWordWrap(True)
        self._conflict_lbl.setStyleSheet(f"color:{self._theme.get('orange', '#ff9500')};")
        layout.addWidget(self._conflict_lbl)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        btn_all = QPushButton('Select all')
        btn_all.setObjectName('btnSecondary')
        btn_all.clicked.connect(lambda: self._set_all_checks(True))
        btn_none = QPushButton('Select none')
        btn_none.setObjectName('btnSecondary')
        btn_none.clicked.connect(lambda: self._set_all_checks(False))
        footer.addWidget(btn_all)
        footer.addWidget(btn_none)
        footer.addStretch(1)
        btn_cancel = QPushButton('Cancel')
        btn_cancel.setObjectName('btnSecondary')
        btn_cancel.clicked.connect(self.reject)
        self._btn_apply = QPushButton('Rename 0 Files')
        self._btn_apply.setObjectName('btnPrimary')
        self._btn_apply.clicked.connect(self.accept)
        footer.addWidget(btn_cancel)
        footer.addWidget(self._btn_apply)
        layout.addLayout(footer)

    def _populate(self):
        dim = self._theme.get('text_dim', '#8e8e93')
        warn = self._theme.get('orange', '#ff9500')

        # A rename target that collides with an existing file (or with
        # another row's own target) can't be applied — flag it instead of
        # silently skipping or letting one bad row abort the whole batch.
        target_counts: dict[str, int] = {}
        for issue in self._issues:
            directory = os.path.dirname(issue.song_path)
            target = os.path.join(directory, issue.suggestion)
            target_counts[target] = target_counts.get(target, 0) + 1

        n_conflicts = 0
        self._table.setRowCount(len(self._issues))
        self._row_data = []
        for row, issue in enumerate(self._issues):
            directory = os.path.dirname(issue.song_path)
            target = os.path.join(directory, issue.suggestion)
            same_dest_taken = os.path.exists(target) and os.path.normcase(target) != os.path.normcase(issue.song_path)
            collides_with_another_row = target_counts.get(target, 0) > 1
            conflict = same_dest_taken or collides_with_another_row
            if conflict:
                n_conflicts += 1
            self._row_data.append((issue.song_path, issue.suggestion, conflict))

            chk = QTableWidgetItem()
            if conflict:
                chk.setFlags(Qt.ItemIsEnabled)
                chk.setCheckState(Qt.Unchecked)
            else:
                chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                chk.setCheckState(Qt.Checked)
            self._table.setItem(row, self.COL_CHECK, chk)

            before = QTableWidgetItem(issue.disk_filename)
            after = QTableWidgetItem(issue.suggestion)
            if conflict:
                reason = ('another file already has this name' if same_dest_taken
                         else 'two files would end up with the same name')
                tip = f'Skipped — {reason}.'
                before.setToolTip(tip)
                after.setToolTip(tip)
                before.setForeground(QColor(dim))
                after.setForeground(QColor(warn))
            self._table.setItem(row, self.COL_BEFORE, before)
            arrow = QTableWidgetItem('→')
            arrow.setTextAlignment(Qt.AlignCenter)
            arrow.setForeground(QColor(dim))
            self._table.setItem(row, self.COL_ARROW, arrow)
            self._table.setItem(row, self.COL_AFTER, after)

        if n_conflicts:
            self._conflict_lbl.setText(
                f'{n_conflicts} file{"s" if n_conflicts != 1 else ""} skipped — the fixed '
                f'name is already taken, so {"they" if n_conflicts != 1 else "it"} '
                f'need{"" if n_conflicts != 1 else "s"} a manual look in Edit Song.'
            )
        else:
            self._conflict_lbl.setText('')
        self._table.itemChanged.connect(lambda _i: self._update_count())
        self._update_count()

    def _set_all_checks(self, on: bool):
        self._table.blockSignals(True)
        for row, (_, _, conflict) in enumerate(self._row_data):
            if conflict:
                continue
            item = self._table.item(row, self.COL_CHECK)
            if item is not None:
                item.setCheckState(Qt.Checked if on else Qt.Unchecked)
        self._table.blockSignals(False)
        self._update_count()

    def _update_count(self):
        n = len(self.selected_intents())
        self._btn_apply.setText(f'Rename {n} File{"" if n == 1 else "s"}')
        self._btn_apply.setEnabled(n > 0)

    def selected_intents(self) -> dict:
        """``{song_path: new_filename}`` for every checked, non-conflicting row."""
        intents = {}
        for row, (song_path, suggestion, conflict) in enumerate(self._row_data):
            if conflict:
                continue
            item = self._table.item(row, self.COL_CHECK)
            if item is not None and item.checkState() == Qt.Checked:
                intents[song_path] = suggestion
        return intents


# ── Utility ───────────────────────────────────────────────────────────────────

def _format_mismatch(entry: dict) -> str:
    reason = entry.get('reason', 'unknown')
    song = entry.get('song', 'Unknown song')
    json_title = entry.get('title', '')
    candidates = entry.get('candidates', [])
    match_source = _match_source_label(entry.get('match_source', ''))

    if reason == 'ambiguous_match':
        ctext = ', '.join(candidates) if candidates else 'multiple songs'
        return f'Ambiguous {match_source} match: {song} | Candidates: {ctext}'
    if reason == 'filename_not_found':
        lookup = json_title or entry.get('path', '')
        return f'{match_source.capitalize()} not found: {song} | JSON="{lookup}"'
    return f'Unknown mismatch: {song}'


def _format_issue_heading(issue: dict) -> str:
    if issue.get('issue_type') == 'invalid_tag':
        tags = ', '.join(issue.get('tags', [])) or 'invalid tags'
        return f'Invalid tags: {tags}'
    if issue.get('issue_type') == 'health':
        return ISSUE_LABELS.get(issue.get('kind', ''), 'Sync issue')
    return _format_mismatch(issue)


def _format_issue_summary(issue: dict) -> str:
    if issue.get('issue_type') == 'invalid_tag':
        tags = ', '.join(issue.get('tags', [])) or 'unknown tag'
        return f'JSON contains invalid tag names for this song: {tags}'
    if issue.get('issue_type') == 'health':
        return issue.get('message', '')
    return _format_mismatch(issue)


def _match_source_label(source: str) -> str:
    return {
        'path': 'path',
        'basename': 'filename',
        'filename': 'filename',
        'title_filename': 'title filename',
        'loose': 'loose (case/spacing-insensitive)',
    }.get(source, 'record')

def _trunc(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n - 1] + '…'


# ── Sequenze: per-song focused editor ───────────────────────────────────────

class _ItemBackgroundDelegate(QStyledItemDelegate):
    """Paints an item's BackgroundRole brush as a rounded fill. Once a list's
    ``::item`` has stylesheet rules (border, radius…), Qt's stylesheet style
    ignores ``item.setBackground()``, so selected tags lost their fill."""

    def paint(self, painter, option, index):
        brush = index.data(Qt.BackgroundRole)
        if brush is not None and brush.style() != Qt.NoBrush and brush.color().alpha():
            painter.save()
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(Qt.NoPen)
            painter.setBrush(brush)
            painter.drawRoundedRect(QRectF(option.rect).adjusted(1, 1, -1, -1), 8, 8)
            painter.restore()
        super().paint(painter, option, index)


class SongSequencerDialog(QDialog):
    """Centered single-song editor — walk through a filter selection one song
    at a time. Edit metadata, tags, and filename, with a play/stop control
    that drives the main window's player bar. Emits signals for rename /
    metadata / tag changes so the main window can perform the actual writes
    against its own state."""

    rename_requested   = pyqtSignal(str, str)        # (old_path, new_filename)
    metadata_requested = pyqtSignal(str, str, str, str)  # (path, title, artist, album)
    tags_requested     = pyqtSignal(str, set, set)   # (path, add_tags, remove_tags)
    trim_requested     = pyqtSignal(str)             # (path) → open the Trimmer
    committed          = pyqtSignal(str)             # (summary) after every per-song commit

    def __init__(self,
                 songs: list,
                 filter_label: str,
                 tag_icons: dict,
                 existing_tags_provider,
                 json_filename_provider,
                 player_bar,
                 theme: dict | None = None,
                 start_index: int = 0,
                 json_title_provider=None,
                 initial_filename: str = '',
                 lyrics_store=None,
                 parent=None):
        super().__init__(parent)
        self._theme = theme or (getattr(parent, '_theme', None) or {})
        self._songs = list(songs)
        self._filter_label = filter_label
        self._tag_icons = tag_icons
        self._get_tags = existing_tags_provider
        self._get_json_filename = json_filename_provider
        self._get_json_title = json_title_provider or json_filename_provider
        self._initial_filename = initial_filename or ''
        self._lyrics_store = lyrics_store
        self._player_bar = player_bar
        self._index = max(0, min(start_index, len(self._songs) - 1)) if self._songs else 0
        self._baseline = {}   # path -> snapshot dict so we know what changed
        self._tag_items = []
        self.setWindowTitle('Edit Song')
        self.setModal(True)
        if parent:
            self.resize(int(parent.width() * 0.65), int(parent.height() * 0.80))
        else:
            self.resize(720, 720)
        self.setMinimumSize(560, 600)
        # Only paint the dialog surface and labels here — a blanket
        # ``QWidget{background}`` rule at dialog scope would override the
        # application's button styles (primary/secondary roles).
        self.setStyleSheet(
            f'QDialog{{background:{self._theme.get("bg", "#f8fafc")};}}'
            f'QLabel{{color:{self._theme.get("text", "#1e293b")};background:transparent;}}'
        )
        self._build_ui()
        self._load_current()
        _apply_overlay_chrome(self, self._theme)

    # ── layout ───────────────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 16)
        layout.setSpacing(12)

        self._header_lbl = QLabel('')
        self._header_lbl.setObjectName('dialogTitle')
        self._header_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._header_lbl)

        # Read the parent's theme so the inner widgets match the active
        # light/dark palette (the overlay chrome only paints the outer card).
        theme = self._theme
        cover_bg = theme.get('placeholder_bg', '#0a0a14')
        dim      = theme.get('text_dim', '#94a3b8')

        # Cover + play controls
        top_row = QHBoxLayout()
        top_row.setSpacing(12)
        self._cover_lbl = QLabel()
        self._cover_lbl.setFixedSize(96, 96)
        self._cover_lbl.setStyleSheet(f'background:{cover_bg};border-radius:8px;')
        self._cover_lbl.setAlignment(Qt.AlignCenter)
        top_row.addWidget(self._cover_lbl)

        controls = QVBoxLayout()
        controls.setSpacing(6)
        self._path_lbl = QLabel('')
        self._path_lbl.setStyleSheet(f'color:{dim};font-size:11px;')
        self._path_lbl.setWordWrap(True)
        controls.addWidget(self._path_lbl)

        play_row = QHBoxLayout()
        play_row.setSpacing(6)
        self._btn_play = QPushButton('▶  Play')
        self._btn_play.setObjectName('btnOk')
        self._btn_play.clicked.connect(self._on_play_clicked)
        self._btn_stop = QPushButton('■  Stop')
        self._btn_stop.setObjectName('btnCancel')
        self._btn_stop.clicked.connect(self._on_stop_clicked)
        self._btn_trim = QPushButton('Trim…')
        self._btn_trim.setObjectName('btnCancel')
        self._btn_trim.setToolTip('Cut part of the song (e.g. an unexpected ending). '
                                  'Overwrites the file after two confirmations.')
        self._btn_trim.clicked.connect(self._on_trim_clicked)
        play_row.addWidget(self._btn_play)
        play_row.addWidget(self._btn_stop)
        play_row.addWidget(self._btn_trim)

        # Live preview of currently-selected tag icons. Sits next to the
        # play/stop control so the user can see at a glance what tags will
        # be applied to the current song.
        self._selected_preview_strip = QWidget()
        sp = QHBoxLayout(self._selected_preview_strip)
        sp.setContentsMargins(8, 0, 0, 0)
        sp.setSpacing(4)
        self._selected_preview_layout = sp
        sp.addStretch(1)
        play_row.addWidget(self._selected_preview_strip, 1)

        controls.addLayout(play_row)
        controls.addStretch()
        top_row.addLayout(controls, 1)
        layout.addLayout(top_row)

        # Filename
        fn_row = QHBoxLayout()
        fn_row.setSpacing(6)
        fn_row.addWidget(QLabel('Filename:'))
        self._filename_edit = QLineEdit()
        fn_row.addWidget(self._filename_edit, 1)
        self._btn_use_json = QPushButton('Use JSON')
        self._btn_use_json.setObjectName('btnCancel')
        self._btn_use_json.clicked.connect(self._on_use_json_name)
        self._btn_use_file = QPushButton('Use file')
        self._btn_use_file.setObjectName('btnCancel')
        self._btn_use_file.clicked.connect(self._on_use_file_name)
        self._btn_from_meta = QPushButton('From metadata')
        self._btn_from_meta.setObjectName('btnCancel')
        self._btn_from_meta.setToolTip('Propose "Artist - Title.ext" from the tags below. '
                                       'Saving renames the file, its lyrics and the JSON entry.')
        self._btn_from_meta.clicked.connect(self._on_use_metadata_name)
        fn_row.addWidget(self._btn_use_json)
        fn_row.addWidget(self._btn_use_file)
        fn_row.addWidget(self._btn_from_meta)
        layout.addLayout(fn_row)

        # Live sync hint: is the JSON title byte-for-byte the filename? Does
        # the filename itself have a spacing problem?
        hint_row = QHBoxLayout()
        hint_row.setSpacing(6)
        self._sync_hint = QLabel('')
        self._sync_hint.setObjectName('seqSyncHint')
        self._sync_hint.setWordWrap(True)
        hint_row.addWidget(self._sync_hint, 1)
        self._btn_use_suggestion = QPushButton('Use suggestion')
        self._btn_use_suggestion.setObjectName('btnCancel')
        self._btn_use_suggestion.clicked.connect(self._on_use_suggestion)
        self._btn_use_suggestion.hide()
        hint_row.addWidget(self._btn_use_suggestion)
        layout.addLayout(hint_row)
        self._filename_edit.textChanged.connect(self._refresh_sync_hint)
        self._title_edit_connected = False

        # Title / Artist / Album
        meta_grid = QGridLayout()
        meta_grid.setHorizontalSpacing(8)
        meta_grid.setVerticalSpacing(6)
        meta_grid.addWidget(QLabel('Title:'),  0, 0)
        meta_grid.addWidget(QLabel('Artist:'), 1, 0)
        meta_grid.addWidget(QLabel('Album:'),  2, 0)
        self._title_edit  = QLineEdit()
        self._artist_edit = QLineEdit()
        self._album_edit  = QLineEdit()
        meta_grid.addWidget(self._title_edit,  0, 1)
        meta_grid.addWidget(self._artist_edit, 1, 1)
        meta_grid.addWidget(self._album_edit,  2, 1)
        layout.addLayout(meta_grid)

        # Tag chips — every chip is a visible "box". The selected state
        # paints a strong blue background; the unselected boxes show a thin
        # accent-colored outline so each tag is a distinct hit target.
        layout.addWidget(QLabel('Tags'))
        self._tag_list = QListWidget()
        self._tag_list.setObjectName('seqTagGrid')
        self._tag_list.setSelectionMode(QAbstractItemView.NoSelection)
        self._tag_list.setViewMode(QListWidget.IconMode)
        self._tag_list.setMovement(QListWidget.Static)
        self._tag_list.setResizeMode(QListWidget.Adjust)
        self._tag_list.setWrapping(True)
        self._tag_list.setSpacing(8)
        self._tag_list.setIconSize(QSize(36, 36))
        self._tag_list.setGridSize(QSize(118, 70))
        self._tag_list.setUniformItemSizes(True)
        # Scroll when the tags don't fit rather than force-growing the grid
        # (which overflowed the dialog and overlapped the footer buttons).
        self._tag_list.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._tag_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._tag_list.setMinimumHeight(140)
        self._tag_list.setStyleSheet(
            'QListWidget#seqTagGrid{background:transparent;border:none;}'
            'QListWidget#seqTagGrid::item{'
            '  border:1.5px solid rgba(127,127,127,0.35);'
            '  border-radius:8px;'
            '  padding:4px;'
            '}'
            'QListWidget#seqTagGrid::item:hover{border-color:#2563eb;}'
        )
        self._tag_list.setItemDelegate(_ItemBackgroundDelegate(self._tag_list))
        self._tag_list.itemClicked.connect(self._on_tag_clicked)
        layout.addWidget(self._tag_list, 1)

        # Footer
        footer = QHBoxLayout()
        self._btn_prev = QPushButton('◀  Previous')
        self._btn_prev.setObjectName('btnCancel')
        self._btn_prev.clicked.connect(self._on_prev)
        self._btn_save = QPushButton('Save')
        self._btn_save.setObjectName('btnOk')
        self._btn_save.clicked.connect(self._on_save)
        self._btn_save_next = QPushButton('Save && Next  ▶')
        self._btn_save_next.setObjectName('btnOk')
        self._btn_save_next.clicked.connect(self._on_save_next)
        self._btn_discard = QPushButton('Discard')
        self._btn_discard.setObjectName('btnCancel')
        self._btn_discard.clicked.connect(self._load_current)
        self._btn_close = QPushButton('Close')
        self._btn_close.setObjectName('btnCancel')
        self._btn_close.clicked.connect(self._on_close)
        footer.addWidget(self._btn_prev)
        footer.addStretch()
        footer.addWidget(self._btn_discard)
        footer.addWidget(self._btn_save)
        footer.addWidget(self._btn_save_next)
        footer.addStretch()
        footer.addWidget(self._btn_close)
        layout.addLayout(footer)

    # ── data / load / commit ─────────────────────────────────────────────────

    def _current_song(self) -> dict:
        return self._songs[self._index] if 0 <= self._index < len(self._songs) else {}

    def _load_current(self):
        song = self._current_song()
        if not song:
            return
        path = song.get('path', '')
        self._header_lbl.setText(
            f'Editing {self._index + 1} of {len(self._songs)} — {self._filter_label}'
        )
        self._path_lbl.setText(path)
        self._filename_edit.setText(song.get('filename', '') or Path(path).name)
        self._title_edit.setText(song.get('title', '') or '')
        self._artist_edit.setText(song.get('artist', '') or '')
        self._album_edit.setText(song.get('album', '') or '')
        if self._initial_filename:
            # Hand-off from Sync Check: pre-fill the suggested filename for
            # this song only; the baseline stays the real name so Save
            # performs the rename.
            self._filename_edit.setText(self._initial_filename)
            self._initial_filename = ''

        # Tag chips — same chip style as PlaylistAssignmentDialog.
        self._tag_list.clear()
        self._tag_items = []
        current_tags = set(self._get_tags(path) or [])
        for tag_name, icon_path in self._tag_icons.items():
            icon = (icon_from_file(icon_path, 32)
                    if icon_path and Path(icon_path).exists() else QIcon())
            item = QListWidgetItem(icon, display_for(tag_name))
            # Drop UserCheckable entirely; the tickbox indicator only renders
            # when the item carries a CheckState, so we don't set one at all.
            item.setFlags(item.flags() | Qt.ItemIsEnabled)
            item.setSizeHint(QSize(108, 62))   # fill the grid cell so labels fit
            item.setData(Qt.UserRole, tag_name)
            # Custom selection flag — UserRole+1 holds True/False.
            item.setData(Qt.UserRole + 1, tag_name in current_tags)
            item.setTextAlignment(Qt.AlignCenter)
            self._tag_list.addItem(item)
            self._tag_items.append(item)
            self._paint_tag(item)

        # Cover preview — cover bytes are loaded lazily, so read them from
        # the file when this song's thumbnail hasn't been fetched yet.
        cover_data = song.get('cover_data') or b''
        if not cover_data and song.get('has_cover') and path:
            cover_data, _ = read_cover(path)
            if cover_data:
                song['cover_data'] = cover_data
        if cover_data:
            px = load_cropped_pixmap(cover_data, 96)
            if px:
                self._cover_lbl.setPixmap(px)
                self._cover_lbl.setText('')
            else:
                self._cover_lbl.setText('♪')
                self._cover_lbl.setPixmap(QIcon().pixmap(0, 0))
        else:
            theme = self._theme
            ph_bg  = theme.get('placeholder_bg', '#0a0a14')
            ph_fg  = theme.get('text_dim', '#475569')
            self._cover_lbl.setText('♪')
            self._cover_lbl.setPixmap(QIcon().pixmap(0, 0))
            self._cover_lbl.setStyleSheet(
                f'background:{ph_bg};border-radius:8px;color:{ph_fg};font-size:36px;'
            )

        self._baseline[path] = {
            'filename': song.get('filename', '') or Path(path).name,
            'title': self._title_edit.text(),
            'artist': self._artist_edit.text(),
            'album': self._album_edit.text(),
            'tags': set(current_tags),
        }
        self._btn_prev.setEnabled(self._index > 0)
        self._btn_save_next.setEnabled(self._index < len(self._songs) - 1)
        self._refresh_selected_preview()
        self._refresh_sync_hint()

    def _commit_current(self) -> bool:
        """Emit every change of the current song to the main window.
        Returns True when something was committed."""
        song = self._current_song()
        if not song:
            return False
        path = song.get('path', '')
        baseline = self._baseline.get(path, {})
        parts: list[str] = []

        # Filename rename
        new_filename = self._filename_edit.text().strip()
        old_filename = baseline.get('filename', '')
        if new_filename and new_filename != old_filename:
            self.rename_requested.emit(path, new_filename)
            # The main window renames the file and updates the *same* song
            # dict in place — re-read the path so the metadata/tag edits
            # below land on the renamed file instead of being dropped.
            new_path = song.get('path', '') or path
            if new_path != path:
                self._baseline[new_path] = self._baseline.pop(path, baseline)
                path = new_path
                parts.append('renamed')
            elif (song.get('filename', '') or Path(path).name) != old_filename:
                parts.append('renamed')
            else:
                # Rename refused (dialog shown by the main window) — put the
                # real name back so the user sees what is on disk.
                self._filename_edit.setText(song.get('filename', '') or Path(path).name)

        # Metadata
        title  = self._title_edit.text().strip()
        artist = self._artist_edit.text().strip()
        album  = self._album_edit.text().strip()
        if (title != baseline.get('title', '') or
                artist != baseline.get('artist', '') or
                album != baseline.get('album', '')):
            self.metadata_requested.emit(path, title, artist, album)
            parts.append('metadata')

        # Tags
        current_checked = {
            item.data(Qt.UserRole)
            for item in self._tag_items
            if item.data(Qt.UserRole + 1)
        }
        baseline_tags = baseline.get('tags', set())
        add_tags = current_checked - baseline_tags
        remove_tags = baseline_tags - current_checked
        if add_tags or remove_tags:
            self.tags_requested.emit(path, add_tags, remove_tags)
            n = len(add_tags) + len(remove_tags)
            parts.append(f'{n} tag{"" if n == 1 else "s"}')

        # Tell the parent to flush to disk so the user's edit survives even
        # if the dialog is killed mid-session.
        if parts:
            name = song.get('filename', '') or Path(path).name
            summary = f'Saved "{name}" — ' + ', '.join(parts)
            self.committed.emit(summary)
            self._rebaseline()
            self._flash_saved(summary)
            self._refresh_sync_hint()
            return True
        return False

    def _rebaseline(self):
        """Snapshot the current field values as the new baseline."""
        song = self._current_song()
        if not song:
            return
        path = song.get('path', '')
        self._baseline[path] = {
            'filename': song.get('filename', '') or Path(path).name,
            'title': self._title_edit.text().strip(),
            'artist': self._artist_edit.text().strip(),
            'album': self._album_edit.text().strip(),
            'tags': {
                item.data(Qt.UserRole) for item in self._tag_items
                if item.data(Qt.UserRole + 1)
            },
        }
        self._path_lbl.setText(path)
        self._filename_edit.setText(song.get('filename', '') or Path(path).name)

    # ── save confirmation ───────────────────────────────────────────────────

    def _flash_saved(self, summary: str):
        """Visible confirmation that the save went through: the Save
        button turns green with a check for a moment and a pill with the
        summary fades in over the footer."""
        from PyQt5.QtWidgets import QGraphicsOpacityEffect
        from PyQt5.QtCore import QPropertyAnimation, QEasingCurve, QTimer as _QTimer
        ok = self._theme.get('green', '#34c759')
        for btn in (self._btn_save, self._btn_save_next):
            btn.setStyleSheet(f'QPushButton{{background:{ok};color:white;}}')
        self._btn_save.setText('✓  Saved')
        _QTimer.singleShot(1300, self._unflash_saved)

        pill = getattr(self, '_saved_pill', None)
        if pill is None:
            pill = QLabel(self)
            pill.setObjectName('savedPill')
            pill.setAlignment(Qt.AlignCenter)
            pill.setStyleSheet(
                f'QLabel#savedPill{{background:{ok};color:#ffffff;border-radius:11px;'
                f'padding:8px 16px;font-size:12px;font-weight:600;}}'
            )
            pill.hide()
            self._saved_pill = pill
            self._saved_effect = QGraphicsOpacityEffect(pill)
            pill.setGraphicsEffect(self._saved_effect)
            self._saved_anim = QPropertyAnimation(self._saved_effect, b'opacity', self)
            self._saved_anim.setEasingCurve(QEasingCurve.OutCubic)
            self._saved_timer = _QTimer(self)
            self._saved_timer.setSingleShot(True)
            self._saved_timer.timeout.connect(self._fade_saved_pill)
        pill.setText('✓  ' + summary)
        pill.adjustSize()
        w = min(pill.width(), self.width() - 48)
        pill.resize(w, pill.height())
        pill.move((self.width() - w) // 2, self._btn_save.y() - pill.height() - 12)
        pill.show()
        pill.raise_()
        try:
            self._saved_anim.stop()
            self._saved_anim.finished.disconnect()
        except (RuntimeError, TypeError):
            pass
        self._saved_anim.setDuration(180)
        self._saved_anim.setStartValue(self._saved_effect.opacity() if pill.isVisible() else 0.0)
        self._saved_anim.setEndValue(1.0)
        self._saved_anim.start()
        self._saved_timer.start(2200)

    def _fade_saved_pill(self):
        pill = getattr(self, '_saved_pill', None)
        if pill is None:
            return
        try:
            self._saved_anim.stop()
            self._saved_anim.finished.disconnect()
        except (RuntimeError, TypeError):
            pass
        self._saved_anim.setDuration(300)
        self._saved_anim.setStartValue(self._saved_effect.opacity())
        self._saved_anim.setEndValue(0.0)
        self._saved_anim.finished.connect(pill.hide)
        self._saved_anim.start()

    def _unflash_saved(self):
        for btn in (self._btn_save, self._btn_save_next):
            btn.setStyleSheet('')
        self._btn_save.setText('Save')

    # ── slots ────────────────────────────────────────────────────────────────

    def _on_tag_clicked(self, item: QListWidgetItem):
        # Custom selection flag (UserRole+1) instead of CheckState so Qt
        # doesn't paint a tickbox indicator on the chip.
        item.setData(Qt.UserRole + 1, not bool(item.data(Qt.UserRole + 1)))
        self._paint_tag(item)
        self._refresh_selected_preview()

    def _paint_tag(self, item: QListWidgetItem):
        font = item.font()
        if item.data(Qt.UserRole + 1):
            item.setBackground(QColor('#2563eb'))
            item.setForeground(QColor('#ffffff'))
            font.setBold(True)
        else:
            item.setBackground(QColor(0, 0, 0, 0))
            # text color picks the theme's text color if we can find one
            theme = self._theme
            item.setForeground(QColor(theme.get('text', '#1e293b')))
            font.setBold(False)
        item.setFont(font)

    def _refresh_selected_preview(self):
        """Repaint the icon strip next to Play/Stop with one small icon per
        currently-selected tag, in the order they're declared in tag_icons."""
        layout = self._selected_preview_layout
        while layout.count() > 1:    # keep the trailing stretch
            it = layout.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
        any_added = False
        for tag_item in self._tag_items:
            if not tag_item.data(Qt.UserRole + 1):
                continue
            tag_name = tag_item.data(Qt.UserRole)
            icon_path = self._tag_icons.get(tag_name, '')
            if not icon_path or not Path(icon_path).exists():
                continue
            chip = QLabel()
            chip.setFixedSize(24, 24)
            chip.setPixmap(icon_from_file(icon_path, 22).pixmap(22, 22))
            chip.setToolTip(display_for(tag_name))
            layout.insertWidget(layout.count() - 1, chip)
            any_added = True
        if not any_added:
            theme = self._theme
            dim   = theme.get('text_dim', '#94a3b8')
            empty = QLabel('—')
            empty.setStyleSheet(f'color:{dim};font-size:11px;')
            empty.setToolTip('No tags selected')
            layout.insertWidget(layout.count() - 1, empty)


    def _on_use_json_name(self):
        song = self._current_song()
        if not song:
            return
        name = self._get_json_filename(song.get('path', ''))
        if name:
            self._filename_edit.setText(name)

    def _on_use_file_name(self):
        song = self._current_song()
        if not song:
            return
        self._filename_edit.setText(song.get('filename', '') or Path(song.get('path', '')).name)

    def _on_use_metadata_name(self):
        """Propose ``Artist - Title.ext`` from the metadata fields."""
        song = self._current_song()
        if not song:
            return
        ext = Path(song.get('filename', '') or song.get('path', '')).suffix
        artist = self._artist_edit.text().strip()
        title = self._title_edit.text().strip()
        if not title and not artist:
            self._sync_hint.setText('Fill in Title (and Artist) first to build a filename.')
            return
        stem = f'{artist} - {title}' if artist and title else (title or artist)
        stem = stem.replace('/', '-').replace('\\', '-')
        self._filename_edit.setText(normalize_name(stem + ext))

    def _on_use_suggestion(self):
        suggestion = getattr(self, '_current_suggestion', '')
        if suggestion:
            self._filename_edit.setText(suggestion)

    def _on_trim_clicked(self):
        song = self._current_song()
        if song and song.get('path'):
            self.trim_requested.emit(song['path'])

    def _refresh_sync_hint(self, *_):
        """Explain, live, whether the name in the field is in sync with the
        JSON entry and whether it has a spacing problem."""
        song = self._current_song()
        if not song:
            return
        theme = self._theme
        ok_c = theme.get('success_fg', '#34c759')
        warn_c = theme.get('warning_fg', '#ff9500')
        dim_c = theme.get('text_dim', '#8e8e93')
        path = song.get('path', '')
        current = song.get('filename', '') or Path(path).name
        typed = self._filename_edit.text().strip()
        json_title = self._get_json_title(path) if self._get_json_title else ''
        suggestion = normalize_name(typed) if typed else ''
        self._current_suggestion = ''

        parts: list[str] = []
        color = ok_c
        if typed and suggestion and suggestion != typed:
            parts.append(f'Spacing issue — suggested "{suggestion}"')
            self._current_suggestion = suggestion
            color = warn_c
        if typed != current:
            parts.append('Saving will rename the file, its .lrc and the JSON entry')
            color = warn_c if color != ok_c else theme.get('accent', '#007aff')
        elif not json_title:
            parts.append('No JSON entry yet (Assign Tags creates one)')
            color = dim_c
        elif json_title != current:
            parts.append(f'JSON title differs: "{json_title}" — Save fixes it via Sync Check '
                         f'or rename here')
            color = warn_c
        else:
            parts.append('JSON title in sync with the file')
        if self._lyrics_store is not None and self._lyrics_store.get_folder():
            parts.append('lyrics ✓' if self._lyrics_store.has_lyrics(path) else 'no lyrics')
        self._sync_hint.setText(' · '.join(parts))
        self._sync_hint.setStyleSheet(f'color:{color};font-size:11px;')
        self._btn_use_suggestion.setVisible(bool(self._current_suggestion))

    def _on_play_clicked(self):
        if not self._player_bar:
            return
        song = self._current_song()
        if not song:
            return
        path = song.get('path', '')
        cover_data = song.get('cover_data') or b''
        self._player_bar.play_song(
            path,
            song.get('title') or song.get('filename', ''),
            song.get('artist', ''),
            song.get('album', ''),
            cover_data,
            song.get('duration_ms', 0),
        )

    def _on_stop_clicked(self):
        if self._player_bar:
            self._player_bar.stop()

    def _on_prev(self):
        if self._index <= 0:
            return
        self._commit_current()
        self._index -= 1
        self._load_current()

    def _on_save(self):
        if not self._commit_current():
            self._sync_hint.setText('Nothing changed — nothing to save.')

    def _on_save_next(self):
        if self._index >= len(self._songs) - 1:
            self._commit_current()
            return
        self._commit_current()
        self._index += 1
        self._load_current()

    def _on_close(self):
        # Closing the editor leaves the player bar untouched — if the user is
        # listening to the current song, it keeps playing.
        self._commit_current()
        self.accept()

    def closeEvent(self, event):
        super().closeEvent(event)


# ── Unsupported-tag converter ───────────────────────────────────────────────

class UnsupportedTagConverterDialog(QDialog):
    """Remap each unsupported tag string in the JSON to a supported
    identifier (or drop it). Used when the strict-match validity scan flags
    tags that don't correspond to any icon in ``Data/Tags/``."""

    _REMOVE_SENTINEL = '— Remove this tag —'

    def __init__(self,
                 invalid_tags: list,
                 valid_identifiers: list,
                 theme: dict | None = None,
                 parent=None):
        super().__init__(parent)
        self._theme = theme or (getattr(parent, '_theme', None) or {})
        self._invalid_tags = sorted(set(invalid_tags))
        self._valid_identifiers = sorted(set(valid_identifiers))
        self._row_combos: dict = {}    # raw_tag -> QComboBox
        self.setWindowTitle('Fix Unsupported Tags')
        self.setModal(True)
        if parent:
            self.resize(int(parent.width() * 0.55), int(parent.height() * 0.65))
        else:
            self.resize(620, 560)
        self.setMinimumSize(480, 380)
        self.setStyleSheet(
            f'QWidget{{background:{self._theme.get("bg", "#f8fafc")};'
            f'color:{self._theme.get("text", "#1e293b")};}}'
        )
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 16)
        layout.setSpacing(10)

        title = QLabel('Convert unsupported tags')
        title.setObjectName('dialogTitle')
        layout.addWidget(title)

        sub = QLabel(
            'Tag identifiers must exactly match an icon filename stem in '
            '<b>Data/Tags/</b> (e.g., <b>fairy_tail</b> for '
            '<b>fairy_tail.png</b>). The dropdowns below let you remap each '
            'unsupported tag to a supported one, or remove it from the JSON.'
        )
        sub.setObjectName('dialogSubtitle')
        sub.setWordWrap(True)
        layout.addWidget(sub)

        # Scrollable row container
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        host = QWidget()
        rows = QVBoxLayout(host)
        rows.setContentsMargins(4, 4, 4, 4)
        rows.setSpacing(8)

        if not self._invalid_tags:
            empty = QLabel('No unsupported tags found.')
            empty.setStyleSheet('color:#64748b;font-style:italic;')
            rows.addWidget(empty)
        else:
            for raw in self._invalid_tags:
                rows.addLayout(self._build_row(raw))

        rows.addStretch()
        scroll.setWidget(host)
        layout.addWidget(scroll, 1)

        # Footer
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_cancel = QPushButton('Cancel')
        btn_cancel.setObjectName('btnCancel')
        btn_cancel.clicked.connect(self.reject)
        btn_apply = QPushButton('Apply')
        btn_apply.setObjectName('btnOk')
        btn_apply.clicked.connect(self.accept)
        btn_row.addWidget(btn_cancel)
        btn_row.addSpacing(8)
        btn_row.addWidget(btn_apply)
        layout.addLayout(btn_row)

    def _build_row(self, raw: str) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        lbl = QLabel(raw)
        lbl.setStyleSheet('color:#ef4444;font-weight:600;')
        lbl.setMinimumWidth(180)
        lbl.setWordWrap(True)
        row.addWidget(lbl, 1)

        arrow = QLabel('→')
        arrow.setStyleSheet('color:#94a3b8;font-size:14px;')
        row.addWidget(arrow)

        combo = QComboBox()
        # Show both the canonical identifier (what's saved to JSON) and the
        # display name (what the GUI shows) so the mapping is unambiguous.
        for identifier in self._valid_identifiers:
            combo.addItem(f'{identifier}  —  {display_for(identifier)}', identifier)
        combo.addItem(self._REMOVE_SENTINEL, None)
        # Pre-select best-guess via fuzzy lookup; fall back to "Remove".
        guess = suggest_identifier(raw)
        if guess and guess in self._valid_identifiers:
            combo.setCurrentIndex(self._valid_identifiers.index(guess))
        else:
            combo.setCurrentIndex(combo.count() - 1)
        combo.setMinimumWidth(280)
        row.addWidget(combo, 1)

        self._row_combos[raw] = combo
        return row

    def get_mapping(self) -> dict:
        """Return ``{raw_tag: new_identifier_or_None}`` after the user
        accepts. ``None`` means "remove this tag from every record"."""
        return {raw: combo.currentData() for raw, combo in self._row_combos.items()}


# ── Settings dialog ─────────────────────────────────────────────────────────

class _PathCard(QFrame):
    """One square card inside SettingsPanel: icon · title · path caption · refresh."""

    pick_requested    = pyqtSignal()
    refresh_requested = pyqtSignal()

    def __init__(self,
                 title: str,
                 icon_name: str,
                 accent: str,
                 placeholder: str,
                 theme: dict,
                 parent=None):
        super().__init__(parent)
        self._title    = title
        self._accent   = accent
        self._path     = ''
        self._placeholder = placeholder
        self._theme = theme
        self._icon_name = icon_name
        self.setObjectName('settingsPathCard')
        self.setFixedSize(QSize(200, 200))
        self.setCursor(Qt.PointingHandCursor)

        v = QVBoxLayout(self)
        v.setContentsMargins(14, 16, 14, 12)
        v.setSpacing(8)

        # Big icon at the top
        self._icon_holder = QLabel()
        self._icon_holder.setAlignment(Qt.AlignCenter)
        v.addWidget(self._icon_holder)

        # Title
        self._title_lbl = QLabel(title)
        self._title_lbl.setAlignment(Qt.AlignCenter)
        v.addWidget(self._title_lbl)

        # Path caption (elided to fit)
        self._caption = QLabel(placeholder)
        self._caption.setAlignment(Qt.AlignCenter)
        self._caption.setWordWrap(False)
        v.addWidget(self._caption)
        v.addStretch()

        # Refresh button anchored bottom-right
        self._refresh_btn = QToolButton(self)
        self._refresh_btn.setIconSize(QSize(18, 18))
        self._refresh_btn.setAutoRaise(True)
        self._refresh_btn.setCursor(Qt.PointingHandCursor)
        self._refresh_btn.setFixedSize(28, 28)
        self._refresh_btn.setToolTip(f'Refresh {title.lower()}')
        self._refresh_btn.clicked.connect(self._on_refresh_clicked)

        self.apply_theme(theme)

    def apply_theme(self, theme: dict):
        self._theme = theme
        bg     = theme.get('bg_card',  '#ffffff')
        border = theme.get('border',   '#e2e8f0')
        text   = theme.get('text',     '#1e293b')
        dim    = theme.get('text_dim', '#94a3b8')
        self.setStyleSheet(
            'QFrame#settingsPathCard{background:%s;border:2px solid %s;'
            'border-radius:14px;}'
            'QFrame#settingsPathCard:hover{border-color:%s;}'
            % (bg, border, self._accent)
        )
        self._icon_holder.setPixmap(get_icon(self._icon_name, 56, self._accent).pixmap(56, 56))
        self._title_lbl.setStyleSheet(f'color:{text};font-weight:700;font-size:13px;')
        self._caption.setStyleSheet(f'color:{dim};font-size:10px;')
        self._refresh_btn.setIcon(get_icon('refresh', 18, dim))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_btn.move(self.width() - self._refresh_btn.width() - 8,
                               self.height() - self._refresh_btn.height() - 8)
        self._apply_elide()

    def mousePressEvent(self, event):
        # Body click → pick. Refresh button has its own click handler.
        if event.button() == Qt.LeftButton:
            self.pick_requested.emit()
        super().mousePressEvent(event)

    def _on_refresh_clicked(self):
        self.refresh_requested.emit()

    def set_path(self, path: str):
        self._path = path or ''
        self._apply_elide()

    def _apply_elide(self):
        text = self._path or self._placeholder
        fm = QFontMetrics(self._caption.font())
        avail = max(self.width() - 28, 60)
        self._caption.setText(fm.elidedText(text, Qt.ElideLeft, avail))
        self._caption.setToolTip(self._path or '')


class SettingsPanel(QWidget):
    """In-place Settings page. Lives inside the main window's stacked widget
    so the user never sees a floating dialog.

    Emits ``close_requested`` when the user clicks the Close button. The
    MainWindow is responsible for switching back to the library view *and*
    triggering any deferred scans on close."""

    music_folder_chosen   = pyqtSignal(str)
    json_file_chosen      = pyqtSignal(str)
    lyrics_folder_chosen  = pyqtSignal(str)
    music_refresh_requested  = pyqtSignal()
    json_refresh_requested   = pyqtSignal()
    lyrics_refresh_requested = pyqtSignal()
    icon_manager_requested   = pyqtSignal()
    auto_lyrics_toggled      = pyqtSignal(bool)
    close_requested          = pyqtSignal()

    def __init__(self,
                 theme: dict,
                 music_path: str = '',
                 json_path: str = '',
                 lyrics_path: str = '',
                 parent=None):
        super().__init__(parent)
        self._theme = theme
        self.setObjectName('settingsPanel')
        self._build_ui(music_path, json_path, lyrics_path)
        self.apply_theme(theme)

    def _build_ui(self,
                  music_path: str,
                  json_path: str,
                  lyrics_path: str):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 20)
        layout.setSpacing(14)

        self._title_lbl = QLabel('Settings')
        self._title_lbl.setObjectName('dialogTitle')
        self._title_lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._title_lbl)

        self._sub_lbl = QLabel(
            'Pick the Music folder, the tag JSON file, and the Lyrics folder. '
            'Use the refresh icon on a card to re-scan.'
        )
        self._sub_lbl.setObjectName('dialogSubtitle')
        self._sub_lbl.setAlignment(Qt.AlignCenter)
        self._sub_lbl.setWordWrap(True)
        layout.addWidget(self._sub_lbl)

        layout.addStretch(1)

        # Three cards in a centered horizontal row.
        cards_row = QHBoxLayout()
        cards_row.setSpacing(20)
        cards_row.addStretch(1)

        self._music_card = _PathCard(
            'Music Folder', 'music_note', self._theme.get('accent', '#7c3aed'),
            'No music folder selected', self._theme, self,
        )
        self._music_card.set_path(music_path)
        self._music_card.pick_requested.connect(self._on_music_pick)
        self._music_card.refresh_requested.connect(self.music_refresh_requested)
        cards_row.addWidget(self._music_card)

        self._json_card = _PathCard(
            'JSON File', 'document', self._theme.get('blue', '#2563eb'),
            'No tag JSON selected', self._theme, self,
        )
        self._json_card.set_path(json_path)
        self._json_card.pick_requested.connect(self._on_json_pick)
        self._json_card.refresh_requested.connect(self.json_refresh_requested)
        cards_row.addWidget(self._json_card)

        self._lyrics_card = _PathCard(
            'Lyrics Folder', 'text', self._theme.get('cyan', '#0891b2'),
            'No lyrics folder selected', self._theme, self,
        )
        self._lyrics_card.set_path(lyrics_path)
        self._lyrics_card.pick_requested.connect(self._on_lyrics_pick)
        self._lyrics_card.refresh_requested.connect(self.lyrics_refresh_requested)
        cards_row.addWidget(self._lyrics_card)

        cards_row.addStretch(1)
        layout.addLayout(cards_row)

        layout.addStretch(1)

        # Options row: automatic lyrics download
        opts_row = QHBoxLayout()
        opts_row.addStretch(1)
        self._chk_auto_lyrics = QCheckBox('Download missing lyrics automatically in the background')
        self._chk_auto_lyrics.setObjectName('chkAutoLyrics')
        self._chk_auto_lyrics.setToolTip(
            'After every scan, fetch lyrics from LRCLIB for songs without an .lrc file.\n'
            'Runs 4 downloads in parallel, remembers misses for a week and keeps\n'
            'going when you change the music or lyrics folder.'
        )
        self._chk_auto_lyrics.toggled.connect(self.auto_lyrics_toggled)
        opts_row.addWidget(self._chk_auto_lyrics)
        opts_row.addStretch(1)
        layout.addLayout(opts_row)

        # Manage Icons row
        icons_row = QHBoxLayout()
        icons_row.addStretch(1)
        self._btn_manage_icons = QPushButton('Manage Icons')
        self._btn_manage_icons.setObjectName('btnManageIcons')
        self._btn_manage_icons.setIcon(
            get_icon('image', 18, self._theme.get('accent', '#7c3aed'))
        )
        self._btn_manage_icons.setMinimumWidth(160)
        self._btn_manage_icons.setMinimumHeight(36)
        self._btn_manage_icons.clicked.connect(self.icon_manager_requested)
        icons_row.addWidget(self._btn_manage_icons)
        icons_row.addStretch(1)
        layout.addLayout(icons_row)

        layout.addSpacing(8)

        # Continue button bottom-center
        close_row = QHBoxLayout()
        close_row.addStretch(1)
        self._btn_close = QPushButton('Continue')
        self._btn_close.setObjectName('btnOk')
        self._btn_close.setMinimumWidth(140)
        self._btn_close.setMinimumHeight(40)
        self._btn_close.clicked.connect(self.close_requested)
        close_row.addWidget(self._btn_close)
        close_row.addStretch(1)
        layout.addLayout(close_row)

    # ── theming -------------------------------------------------------------

    def apply_theme(self, theme: dict):
        self._theme = theme
        bg   = theme.get('bg', '#f8fafc')
        text = theme.get('text', '#1e293b')
        dim  = theme.get('text_muted', '#64748b')
        self.setStyleSheet(
            f'QWidget#settingsPanel{{background:{bg};}}'
        )
        self._title_lbl.setStyleSheet(
            f'color:{text};font-size:22px;font-weight:700;'
        )
        self._sub_lbl.setStyleSheet(f'color:{dim};font-size:12px;')
        for card in (self._music_card, self._json_card, self._lyrics_card):
            card.apply_theme(theme)
        accent = theme.get('accent', '#7c3aed')
        border = theme.get('border', '#e2e8f0')
        self._btn_manage_icons.setStyleSheet(
            f'QPushButton{{background:transparent;color:{accent};border:1px solid {border};'
            f'border-radius:6px;padding:6px 16px;font-size:12px;font-weight:600;}}'
            f'QPushButton:hover{{border:1px solid {accent};}}'
        )
        self._btn_manage_icons.setIcon(get_icon('image', 18, accent))

    # ── slots ----------------------------------------------------------------

    def _on_music_pick(self):
        start = self._music_card._path or ''
        folder = QFileDialog.getExistingDirectory(self, 'Select Music Folder', start)
        if folder:
            self._music_card.set_path(folder)
            self.music_folder_chosen.emit(folder)

    def _on_json_pick(self):
        start = self._json_card._path or ''
        path, _ = QFileDialog.getOpenFileName(
            self, 'Select Tag JSON', start, 'JSON Files (*.json)'
        )
        if path:
            self._json_card.set_path(path)
            self.json_file_chosen.emit(path)

    def _on_lyrics_pick(self):
        start = self._lyrics_card._path or ''
        folder = QFileDialog.getExistingDirectory(self, 'Select Lyrics Folder', start)
        if folder:
            self._lyrics_card.set_path(folder)
            self.lyrics_folder_chosen.emit(folder)

    # ── public API -----------------------------------------------------------

    def set_button_label(self, text: str):
        self._btn_close.setText(text)

    def set_auto_lyrics(self, enabled: bool):
        self._chk_auto_lyrics.blockSignals(True)
        self._chk_auto_lyrics.setChecked(bool(enabled))
        self._chk_auto_lyrics.blockSignals(False)

    def set_paths(self, music: str, json: str, lyrics: str):
        self._music_card.set_path(music)
        self._json_card.set_path(json)
        self._lyrics_card.set_path(lyrics)


# ── Icon Manager Dialog ────────────────────────────────────────────────────────

class IconManagerDialog(QDialog):
    """Browse, add, rename, and remove tag icon files in the Data/Tags/ folder.

    Emits ``icons_changed`` after any modification so the caller can reload
    the tag-icon registry.
    """

    icons_changed = pyqtSignal()

    _SUPPORTED = ('.png', '.svg', '.ico')

    def __init__(self, tags_dir: str, theme: dict, parent=None):
        super().__init__(parent)
        self._tags_dir = tags_dir
        self._theme = theme
        self._selected_path: str = ''

        _apply_overlay_chrome(self, theme)
        self.setWindowTitle('Icon Manager')
        self.resize(720, 500)

        self._build_ui()
        self._scan_icons()

    # ── build UI ──────────────────────────────────────────────────────────────

    def _build_ui(self):
        t = self._theme
        bg       = t.get('bg', '#f8fafc')
        bg_card  = t.get('bg_card', '#ffffff')
        text     = t.get('text', '#1e293b')
        dim      = t.get('text_muted', '#64748b')
        border   = t.get('border', '#e2e8f0')
        accent   = t.get('accent', '#7c3aed')

        self.setStyleSheet(f'QDialog{{background:{bg};}}')

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 16)
        root.setSpacing(12)

        # Title
        title_lbl = QLabel('Icon Manager')
        title_lbl.setStyleSheet(f'color:{text};font-size:18px;font-weight:700;')
        root.addWidget(title_lbl)

        sub_lbl = QLabel(f'Add, rename, or remove tag icons  ·  {self._tags_dir}')
        sub_lbl.setStyleSheet(f'color:{dim};font-size:11px;')
        sub_lbl.setWordWrap(True)
        root.addWidget(sub_lbl)

        # ── Splitter: list (left) | preview+editor (right) ────────────────────
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(6)
        splitter.setStyleSheet(f'QSplitter::handle{{background:{border};}}')

        # ── Left: existing icons list ─────────────────────────────────────────
        left = QWidget()
        left.setStyleSheet(f'background:{bg_card};border:1px solid {border};border-radius:8px;')
        lv = QVBoxLayout(left)
        lv.setContentsMargins(10, 10, 10, 10)
        lv.setSpacing(8)

        list_hdr = QLabel('EXISTING ICONS')
        list_hdr.setStyleSheet(f'color:{dim};font-size:10px;font-weight:700;letter-spacing:1px;')
        lv.addWidget(list_hdr)

        self._icon_list = QListWidget()
        self._icon_list.setStyleSheet(
            f'QListWidget{{background:transparent;border:none;color:{text};font-size:12px;}}'
            f'QListWidget::item{{padding:4px 6px;border-radius:4px;}}'
            f'QListWidget::item:selected{{background:{accent};color:white;}}'
        )
        self._icon_list.setIconSize(QSize(32, 32))
        self._icon_list.itemSelectionChanged.connect(self._on_list_selection)
        lv.addWidget(self._icon_list, 1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self._btn_add = QPushButton('Add Icon')
        self._btn_add.setStyleSheet(
            f'QPushButton{{background:{accent};color:white;border:none;border-radius:6px;'
            f'padding:6px 14px;font-size:12px;font-weight:600;}}'
            f'QPushButton:hover{{opacity:0.9;}}'
        )
        self._btn_add.clicked.connect(self._on_add_icon)
        self._btn_remove = QPushButton('Remove')
        self._btn_remove.setStyleSheet(
            f'QPushButton{{background:{t.get("red","#dc2626")};color:white;border:none;border-radius:6px;'
            f'padding:6px 14px;font-size:12px;font-weight:600;}}'
        )
        self._btn_remove.setEnabled(False)
        self._btn_remove.clicked.connect(self._on_remove_icon)
        btn_row.addWidget(self._btn_add)
        btn_row.addWidget(self._btn_remove)
        lv.addLayout(btn_row)

        splitter.addWidget(left)

        # ── Right: preview + name editor ─────────────────────────────────────
        right = QWidget()
        right.setStyleSheet(f'background:{bg_card};border:1px solid {border};border-radius:8px;')
        rv = QVBoxLayout(right)
        rv.setContentsMargins(16, 16, 16, 16)
        rv.setSpacing(10)

        preview_hdr = QLabel('PREVIEW')
        preview_hdr.setStyleSheet(f'color:{dim};font-size:10px;font-weight:700;letter-spacing:1px;')
        rv.addWidget(preview_hdr)

        self._preview_lbl = QLabel()
        self._preview_lbl.setAlignment(Qt.AlignCenter)
        self._preview_lbl.setFixedSize(96, 96)
        self._preview_lbl.setStyleSheet(
            f'background:{t.get("bg_alt","#f1f5f9")};border:1px solid {border};border-radius:8px;'
        )
        rv.addWidget(self._preview_lbl, 0, Qt.AlignHCenter)

        name_hdr = QLabel('Identifier (filename stem)')
        name_hdr.setStyleSheet(f'color:{dim};font-size:11px;')
        rv.addWidget(name_hdr)

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText('e.g. anime, kpop, gym…')
        self._name_edit.setStyleSheet(
            f'QLineEdit{{background:{bg};border:1px solid {border};border-radius:6px;'
            f'color:{text};font-size:13px;padding:6px 10px;}}'
            f'QLineEdit:focus{{border:1px solid {accent};}}'
        )
        self._name_edit.setEnabled(False)
        rv.addWidget(self._name_edit)

        self._path_caption = QLabel('')
        self._path_caption.setStyleSheet(f'color:{dim};font-size:10px;')
        self._path_caption.setWordWrap(True)
        rv.addWidget(self._path_caption)

        rv.addStretch(1)

        self._btn_save = QPushButton('Save / Rename')
        self._btn_save.setStyleSheet(
            f'QPushButton{{background:{accent};color:white;border:none;border-radius:6px;'
            f'padding:8px 20px;font-size:13px;font-weight:600;}}'
        )
        self._btn_save.setEnabled(False)
        self._btn_save.clicked.connect(self._on_save_rename)
        rv.addWidget(self._btn_save, 0, Qt.AlignRight)

        splitter.addWidget(right)
        splitter.setSizes([280, 420])

        root.addWidget(splitter, 1)

        # ── Done button ───────────────────────────────────────────────────────
        done_row = QHBoxLayout()
        done_row.addStretch(1)
        btn_done = QPushButton('Done')
        btn_done.setStyleSheet(
            f'QPushButton{{background:transparent;color:{accent};border:1px solid {accent};'
            f'border-radius:6px;padding:8px 24px;font-size:13px;font-weight:600;}}'
        )
        btn_done.clicked.connect(self.accept)
        done_row.addWidget(btn_done)
        root.addLayout(done_row)

    # ── helpers ────────────────────────────────────────────────────────────────

    def _scan_icons(self):
        """Reload the icon list from disk."""
        self._icon_list.clear()
        self._selected_path = ''
        self._btn_remove.setEnabled(False)
        self._btn_save.setEnabled(False)
        self._name_edit.setEnabled(False)
        self._name_edit.clear()
        self._preview_lbl.clear()
        self._path_caption.clear()

        if not os.path.isdir(self._tags_dir):
            return

        entries = sorted(
            (e for e in os.scandir(self._tags_dir)
             if e.is_file() and os.path.splitext(e.name)[1].lower() in self._SUPPORTED),
            key=lambda e: e.name.lower(),
        )
        for entry in entries:
            stem = os.path.splitext(entry.name)[0]
            icon = icon_from_file(entry.path, 32)
            item = QListWidgetItem(icon, stem)
            item.setData(Qt.UserRole, entry.path)
            self._icon_list.addItem(item)

    def _load_preview(self, path: str):
        ext = os.path.splitext(path)[1].lower()
        px = None
        if ext in ('.png', '.ico'):
            raw = QPixmap(path)
            if not raw.isNull():
                px = raw.scaled(80, 80, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        elif ext == '.svg':
            icon = icon_from_file(path, 80)
            if not icon.isNull():
                px = icon.pixmap(80, 80)
        if px and not px.isNull():
            self._preview_lbl.setPixmap(px)
        else:
            self._preview_lbl.clear()

    # ── slots ──────────────────────────────────────────────────────────────────

    def _on_list_selection(self):
        items = self._icon_list.selectedItems()
        if not items:
            self._selected_path = ''
            self._btn_remove.setEnabled(False)
            self._btn_save.setEnabled(False)
            self._name_edit.setEnabled(False)
            self._name_edit.clear()
            self._preview_lbl.clear()
            self._path_caption.clear()
            return

        item = items[0]
        path = item.data(Qt.UserRole)
        self._selected_path = path
        stem = os.path.splitext(os.path.basename(path))[0]
        self._name_edit.setText(stem)
        self._name_edit.setEnabled(True)
        self._btn_remove.setEnabled(True)
        self._btn_save.setEnabled(True)
        self._path_caption.setText(path)
        self._load_preview(path)

    def _on_add_icon(self):
        src, _ = QFileDialog.getOpenFileName(
            self, 'Select Icon File', '',
            'Images (*.png *.svg *.ico);;All Files (*)',
        )
        if not src:
            return

        stem = os.path.splitext(os.path.basename(src))[0]
        ext  = os.path.splitext(src)[1].lower()

        # Prefill name field and show preview; user edits then clicks Save.
        self._selected_path = src
        self._name_edit.setText(stem)
        self._name_edit.setEnabled(True)
        self._btn_save.setEnabled(True)
        self._path_caption.setText(f'Source: {src}')
        self._load_preview(src)

        # Store the source path as a temporary staging marker.
        self._staged_src = src
        self._staged_ext = ext

    def _on_remove_icon(self):
        if not self._selected_path or not os.path.isfile(self._selected_path):
            return
        name = os.path.basename(self._selected_path)
        reply = QMessageBox.question(
            self, 'Remove Icon',
            f'Delete "{name}" from the tags folder?\nThis cannot be undone.',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        try:
            os.remove(self._selected_path)
        except OSError as exc:
            QMessageBox.warning(self, 'Remove Icon', f'Could not delete file:\n{exc}')
            return
        self._scan_icons()
        self.icons_changed.emit()

    def _on_save_rename(self):
        new_name = self._name_edit.text().strip()
        if not new_name:
            QMessageBox.warning(self, 'Save Icon', 'Please enter an identifier name.')
            return

        # Sanitise: only alphanumeric, underscore, hyphen.
        safe = ''.join(c if c.isalnum() or c in '_-' else '_' for c in new_name).lower()
        if not safe:
            QMessageBox.warning(self, 'Save Icon', 'Name contains no valid characters.')
            return

        staged = getattr(self, '_staged_src', None)
        if staged and os.path.isfile(staged) and staged != self._selected_path:
            # Copying a new file into tags_dir.
            ext  = getattr(self, '_staged_ext', os.path.splitext(staged)[1].lower())
            dest = os.path.join(self._tags_dir, safe + ext)
            if os.path.exists(dest) and dest != staged:
                reply = QMessageBox.question(
                    self, 'Save Icon',
                    f'"{safe + ext}" already exists. Overwrite?',
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
                )
                if reply != QMessageBox.Yes:
                    return
            os.makedirs(self._tags_dir, exist_ok=True)
            try:
                shutil.copy2(staged, dest)
            except OSError as exc:
                QMessageBox.warning(self, 'Save Icon', f'Could not copy file:\n{exc}')
                return
            self._staged_src = None
        elif self._selected_path and os.path.isfile(self._selected_path):
            # Renaming an existing file.
            old_ext  = os.path.splitext(self._selected_path)[1].lower()
            old_stem = os.path.splitext(os.path.basename(self._selected_path))[0]
            if safe == old_stem:
                return  # nothing to rename
            dest = os.path.join(self._tags_dir, safe + old_ext)
            if os.path.exists(dest):
                reply = QMessageBox.question(
                    self, 'Rename Icon',
                    f'"{safe + old_ext}" already exists. Overwrite?',
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
                )
                if reply != QMessageBox.Yes:
                    return
            try:
                os.rename(self._selected_path, dest)
            except OSError as exc:
                QMessageBox.warning(self, 'Rename Icon', f'Could not rename file:\n{exc}')
                return
        else:
            return

        self._scan_icons()
        self.icons_changed.emit()



# ── Trimmer ──────────────────────────────────────────────────────────────────

from PyQt5.QtWidgets import QSpinBox
from PyQt5.QtCore import QRectF, QPointF, QTimer
from PyQt5.QtGui import QPen
from audio_trim import (
    ffmpeg_available, INSTALL_HINT, detect_silence, format_ms, parse_time,
)
from workers import WaveformWorker, TrimWorker


class WaveformView(QWidget):
    """Peak-envelope waveform with draggable start/end handles, a playhead,
    click-to-seek and wheel zoom (with a minimap while zoomed)."""

    seek_requested = pyqtSignal(int)          # ms
    range_changed = pyqtSignal(int, int)      # start_ms, end_ms

    HANDLE_GRAB_PX = 9
    MINIMAP_H = 14
    RULER_H = 16

    def __init__(self, theme: dict, parent=None):
        super().__init__(parent)
        self._theme = theme or {}
        self._peaks: list[float] = []
        self._duration = 0
        self._start = 0
        self._end = 0
        self._playhead = -1
        self._zoom = 1.0                 # 1 = whole song visible
        self._view_start = 0             # ms at the left edge
        self._drag: str | None = None    # 'start' | 'end' | 'pan' | 'minimap'
        self._drag_origin = (0, 0)
        self._loading = True
        self.setMinimumHeight(170)
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)
        self.setAttribute(Qt.WA_StyledBackground, True)

    # ── data ────────────────────────────────────────────────────────────────

    def set_peaks(self, peaks: list[float], duration_ms: int):
        self._peaks = list(peaks)
        self._duration = max(0, int(duration_ms))
        self._loading = False
        if self._end <= 0 or self._end > self._duration:
            self._end = self._duration
        self.update()

    def set_loading(self, loading: bool):
        self._loading = loading
        self.update()

    def set_range(self, start_ms: int, end_ms: int):
        self._start = max(0, min(int(start_ms), self._duration))
        self._end = max(self._start, min(int(end_ms), self._duration)) if self._duration else int(end_ms)
        self.update()

    def range(self) -> tuple[int, int]:
        return self._start, self._end

    def set_playhead(self, ms: int):
        self._playhead = int(ms)
        self.update()

    def duration(self) -> int:
        return self._duration

    # ── geometry ────────────────────────────────────────────────────────────

    def _wave_rect(self) -> QRectF:
        top = self.MINIMAP_H + 4 if self._zoom > 1.01 else 0
        return QRectF(0, top, self.width(), max(10, self.height() - top - self.RULER_H))

    def _visible_ms(self) -> int:
        return max(1, int(self._duration / self._zoom)) if self._duration else 1

    def _ms_to_x(self, ms: int) -> float:
        r = self._wave_rect()
        return r.left() + (ms - self._view_start) / self._visible_ms() * r.width()

    def _x_to_ms(self, x: float) -> int:
        r = self._wave_rect()
        if r.width() <= 0:
            return 0
        ms = self._view_start + (x - r.left()) / r.width() * self._visible_ms()
        return max(0, min(self._duration, int(ms)))

    def _clamp_view(self):
        self._view_start = max(0, min(self._view_start, self._duration - self._visible_ms()))

    # ── painting ────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        t = self._theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        bg = QColor(t.get('bg_card', '#ffffff'))
        p.fillRect(self.rect(), bg)
        r = self._wave_rect()
        accent = QColor(t.get('accent', '#007aff'))
        dim = QColor(t.get('text_dim', '#8e8e93'))
        muted = QColor(t.get('border', '#d2d2d7'))
        text = QColor(t.get('text_muted', '#6e6e73'))

        if self._loading or not self._peaks:
            p.setPen(text)
            p.drawText(self.rect(), Qt.AlignCenter,
                       'Analysing waveform…' if self._loading else 'No audio data')
            p.end()
            return

        # kept region shading
        x0 = self._ms_to_x(self._start)
        x1 = self._ms_to_x(self._end)
        shade = QColor(accent)
        shade.setAlpha(28)
        p.fillRect(QRectF(x0, r.top(), max(0.0, x1 - x0), r.height()), shade)

        # bars
        mid = r.center().y()
        n = len(self._peaks)
        width = int(r.width())
        vis = self._visible_ms()
        p.setPen(Qt.NoPen)
        for px in range(0, width, 2):
            ms_a = self._view_start + px / r.width() * vis
            ms_b = self._view_start + (px + 2) / r.width() * vis
            ia = int(ms_a / self._duration * n) if self._duration else 0
            ib = max(ia + 1, int(ms_b / self._duration * n)) if self._duration else ia + 1
            chunk = self._peaks[ia:ib] or [0.0]
            amp = max(chunk)
            h = max(1.0, amp * (r.height() * 0.46))
            inside = self._start <= ms_a <= self._end
            p.setBrush(accent if inside else dim)
            p.drawRect(QRectF(r.left() + px, mid - h, 1.6, h * 2))

        # handles
        for x, label in ((x0, 'start'), (x1, 'end')):
            pen = QPen(accent, 2)
            p.setPen(pen)
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
            p.setBrush(accent)
            p.setPen(Qt.NoPen)
            grip = QRectF(x - 5, r.top(), 10, 14) if label == 'start' else QRectF(x - 5, r.bottom() - 14, 10, 14)
            p.drawRoundedRect(grip, 3, 3)

        # playhead
        if 0 <= self._playhead <= self._duration:
            xp = self._ms_to_x(self._playhead)
            if r.left() <= xp <= r.right():
                p.setPen(QPen(QColor(t.get('red', '#ff3b30')), 1.5))
                p.drawLine(QPointF(xp, r.top()), QPointF(xp, r.bottom()))

        # ruler
        p.setPen(QPen(muted, 1))
        p.drawLine(QPointF(r.left(), r.bottom() + 0.5), QPointF(r.right(), r.bottom() + 0.5))
        p.setPen(text)
        f = p.font()
        f.setPixelSize(10)
        p.setFont(f)
        step = self._ruler_step(vis)
        first = (self._view_start // step) * step
        ms = first
        while ms <= self._view_start + vis:
            x = self._ms_to_x(ms)
            if r.left() <= x <= r.right():
                p.drawLine(QPointF(x, r.bottom()), QPointF(x, r.bottom() + 4))
                p.drawText(QRectF(x + 3, r.bottom() + 1, 60, self.RULER_H), Qt.AlignLeft | Qt.AlignVCenter,
                           format_ms(ms, 0 if step >= 1000 else 1))
            ms += step

        # minimap while zoomed
        if self._zoom > 1.01:
            mm = QRectF(0, 0, self.width(), self.MINIMAP_H)
            p.fillRect(mm, QColor(t.get('bg_hover', '#ececef')))
            win = QRectF(self._view_start / self._duration * mm.width(), 0,
                         max(6.0, vis / self._duration * mm.width()), mm.height())
            p.setBrush(accent)
            p.setPen(Qt.NoPen)
            p.setOpacity(0.5)
            p.drawRoundedRect(win, 3, 3)
            p.setOpacity(1.0)
        p.end()

    @staticmethod
    def _ruler_step(visible_ms: int) -> int:
        for step in (500, 1000, 2000, 5000, 10000, 15000, 30000, 60000, 120000, 300000):
            if visible_ms / step <= 12:
                return step
        return 600000

    # ── interaction ─────────────────────────────────────────────────────────

    def _hit_handle(self, x: float) -> str | None:
        if abs(x - self._ms_to_x(self._start)) <= self.HANDLE_GRAB_PX:
            return 'start'
        if abs(x - self._ms_to_x(self._end)) <= self.HANDLE_GRAB_PX:
            return 'end'
        return None

    def mousePressEvent(self, event):
        if not self._peaks:
            return
        pos = event.pos()
        if self._zoom > 1.01 and pos.y() < self.MINIMAP_H + 2:
            self._drag = 'minimap'
            self._pan_minimap(pos.x())
            return
        if event.button() == Qt.LeftButton:
            handle = self._hit_handle(pos.x())
            if handle:
                self._drag = handle
                self.setCursor(Qt.SizeHorCursor)
                return
            if event.modifiers() & Qt.ShiftModifier:
                self._drag = 'pan'
                self._drag_origin = (pos.x(), self._view_start)
                self.setCursor(Qt.ClosedHandCursor)
                return
            self.seek_requested.emit(self._x_to_ms(pos.x()))
        elif event.button() == Qt.MiddleButton:
            self._drag = 'pan'
            self._drag_origin = (pos.x(), self._view_start)
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        pos = event.pos()
        if self._drag in ('start', 'end'):
            ms = self._x_to_ms(pos.x())
            if self._drag == 'start':
                self._start = min(ms, max(0, self._end - 500))
            else:
                self._end = max(ms, min(self._duration, self._start + 500))
            self.range_changed.emit(self._start, self._end)
            self.update()
        elif self._drag == 'pan':
            ox, ov = self._drag_origin
            r = self._wave_rect()
            delta_ms = (ox - pos.x()) / max(1.0, r.width()) * self._visible_ms()
            self._view_start = int(ov + delta_ms)
            self._clamp_view()
            self.update()
        elif self._drag == 'minimap':
            self._pan_minimap(pos.x())
        else:
            self.setCursor(Qt.SizeHorCursor if self._hit_handle(pos.x()) else Qt.CrossCursor)

    def mouseReleaseEvent(self, event):
        self._drag = None
        self.setCursor(Qt.CrossCursor)

    def _pan_minimap(self, x: float):
        if not self._duration:
            return
        centre = x / max(1.0, self.width()) * self._duration
        self._view_start = int(centre - self._visible_ms() / 2)
        self._clamp_view()
        self.update()

    def wheelEvent(self, event):
        if not self._peaks:
            return
        anchor_ms = self._x_to_ms(event.pos().x())
        factor = 1.25 if event.angleDelta().y() > 0 else 0.8
        new_zoom = max(1.0, min(32.0, self._zoom * factor))
        if abs(new_zoom - self._zoom) < 1e-6:
            return
        r = self._wave_rect()
        frac = (event.pos().x() - r.left()) / max(1.0, r.width())
        self._zoom = new_zoom
        self._view_start = int(anchor_ms - frac * self._visible_ms())
        self._clamp_view()
        self.update()

    def reset_zoom(self):
        self._zoom = 1.0
        self._view_start = 0
        self.update()


class _ConfirmOverwriteDialog(QDialog):
    """Second confirmation: the user must tick the acknowledgement before the
    red button enables."""

    def __init__(self, filename: str, summary: str, theme: dict, parent=None):
        super().__init__(parent)
        self._theme = theme or {}
        self.setWindowTitle('Overwrite original file')
        self.setModal(True)
        self.setMinimumWidth(600)
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 20, 24, 18)
        v.setSpacing(12)
        title = QLabel('This will permanently overwrite the original file')
        title.setObjectName('dialogTitle')
        title.setStyleSheet(f"color:{self._theme.get('red', '#ff3b30')};")
        v.addWidget(title)
        body = QLabel(summary + '\n\nThe audio file is replaced on disk. This cannot be undone.')
        body.setWordWrap(True)
        v.addWidget(body)
        self._chk = QCheckBox(f'I understand — overwrite "{filename}"')
        self._chk.toggled.connect(self._update)
        v.addWidget(self._chk)
        row = QHBoxLayout()
        row.addStretch(1)
        btn_cancel = QPushButton('Cancel')
        btn_cancel.setObjectName('btnSecondary')
        btn_cancel.clicked.connect(self.reject)
        self._btn_go = QPushButton('Overwrite && Trim')
        self._btn_go.setObjectName('btnDestructive')
        self._btn_go.setEnabled(False)
        self._btn_go.clicked.connect(self.accept)
        row.addWidget(btn_cancel)
        row.addWidget(self._btn_go)
        v.addLayout(row)
        _apply_overlay_chrome(self, self._theme)

    def _update(self, checked: bool):
        self._btn_go.setEnabled(checked)


class TrimmerDialog(QDialog):
    """Cut a region out of a song — typically leading/trailing silence or an
    unexpected ending. Preview through the main player bar; the original
    file is overwritten in place after two confirmations."""

    trimmed = pyqtSignal(object)   # TrimResult

    PREVIEW_TAIL_MS = 5000

    def __init__(self, song: dict, theme: dict, player_bar=None,
                 lyrics_store=None, parent=None):
        super().__init__(parent)
        self._song = song
        self._path = song.get('path', '')
        self._theme = theme or (getattr(parent, '_theme', None) or {})
        self._player = player_bar
        self._lyrics_store = lyrics_store
        self._peaks: list[float] = []
        self._duration = int(song.get('duration_ms', 0) or 0)
        self._wave_worker: WaveformWorker | None = None
        self._trim_worker: TrimWorker | None = None
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._stop_preview)
        self._detected: dict | None = None
        self._updating = False

        name = song.get('filename') or Path(self._path).name
        self.setWindowTitle(f'Trim — {name}')
        self.setModal(True)
        if parent:
            self.resize(int(parent.width() * 0.72), int(parent.height() * 0.7))
        else:
            self.resize(960, 620)
        self.setMinimumSize(720, 520)
        self._build_ui()
        _apply_overlay_chrome(self, self._theme)
        if self._player is not None:
            try:
                self._player.position_changed.connect(self._on_player_position)
            except (AttributeError, TypeError):
                pass
        if ffmpeg_available() or self._path.lower().endswith('.wav'):
            self._start_analysis()
        else:
            self._wave.set_loading(False)
            self._status.setText(INSTALL_HINT)
            self._status.setStyleSheet(f"color:{self._theme.get('red', '#ff3b30')};")
            self._btn_trim.setEnabled(False)

    # ── ui ───────────────────────────────────────────────────────────────────

    def _build_ui(self):
        t = self._theme
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 18, 24, 16)
        layout.setSpacing(10)

        name = self._song.get('filename') or Path(self._path).name
        title = QLabel(f'Trim "{name}"')
        title.setObjectName('dialogTitle')
        layout.addWidget(title)
        sub = QLabel('Drag the handles (or use the fields) to choose the part to KEEP. '
                     'Everything outside is removed. Scroll to zoom, shift-drag to pan, click to seek.')
        sub.setObjectName('dialogSubtitle')
        sub.setWordWrap(True)
        layout.addWidget(sub)

        self._wave = WaveformView(t)
        self._wave.seek_requested.connect(self._on_seek)
        self._wave.range_changed.connect(self._on_range_dragged)
        layout.addWidget(self._wave, 1)

        # row 1: start / end fields + detection
        row1 = QHBoxLayout()
        row1.setSpacing(8)
        row1.addWidget(QLabel('Start'))
        self._start_edit = QLineEdit('0:00.000')
        self._start_edit.setFixedWidth(96)
        self._start_edit.editingFinished.connect(self._on_fields_edited)
        row1.addWidget(self._start_edit)
        self._btn_start_here = QPushButton('Set at playhead')
        self._btn_start_here.setObjectName('btnSecondary')
        self._btn_start_here.clicked.connect(lambda: self._set_from_playhead('start'))
        row1.addWidget(self._btn_start_here)
        row1.addSpacing(14)
        row1.addWidget(QLabel('End'))
        self._end_edit = QLineEdit('0:00.000')
        self._end_edit.setFixedWidth(96)
        self._end_edit.editingFinished.connect(self._on_fields_edited)
        row1.addWidget(self._end_edit)
        self._btn_end_here = QPushButton('Set at playhead')
        self._btn_end_here.setObjectName('btnSecondary')
        self._btn_end_here.clicked.connect(lambda: self._set_from_playhead('end'))
        row1.addWidget(self._btn_end_here)
        row1.addStretch(1)
        self._btn_lead = QPushButton('Detect leading silence')
        self._btn_lead.setObjectName('btnSecondary')
        self._btn_lead.clicked.connect(self._on_detect_lead)
        self._btn_trail = QPushButton('Detect ending')
        self._btn_trail.setObjectName('btnSecondary')
        self._btn_trail.setToolTip('Move the end to where the trailing silence starts, or to the\n'
                                   'last long gap before a hidden/unexpected ending.')
        self._btn_trail.clicked.connect(self._on_detect_trail)
        self._btn_reset = QPushButton('Reset')
        self._btn_reset.setObjectName('btnSecondary')
        self._btn_reset.clicked.connect(self._on_reset)
        row1.addWidget(self._btn_lead)
        row1.addWidget(self._btn_trail)
        row1.addWidget(self._btn_reset)
        layout.addLayout(row1)

        # row 2: fades + options
        row2 = QHBoxLayout()
        row2.setSpacing(8)
        row2.addWidget(QLabel('Fade in'))
        self._fade_in = QSpinBox()
        self._fade_in.setRange(0, 10000)
        self._fade_in.setSingleStep(100)
        self._fade_in.setSuffix(' ms')
        self._fade_in.setFixedWidth(96)
        row2.addWidget(self._fade_in)
        row2.addWidget(QLabel('Fade out'))
        self._fade_out = QSpinBox()
        self._fade_out.setRange(0, 10000)
        self._fade_out.setSingleStep(100)
        self._fade_out.setSuffix(' ms')
        self._fade_out.setFixedWidth(96)
        row2.addWidget(self._fade_out)
        fade_note = QLabel('(0 = untouched stream copy)')
        fade_note.setObjectName('dialogSubtitle')
        fade_note.setToolTip('Any fade re-encodes the audio in the same format; with both at 0 the audio bytes are copied untouched.')
        row2.addWidget(fade_note)
        row2.addStretch(1)
        self._chk_lrc = QCheckBox('Shift .lrc timestamps')
        self._chk_lrc.setToolTip('Keep the lyrics in sync when the start is trimmed.')
        has_lrc = bool(self._lyrics_store and self._lyrics_store.has_lyrics(self._path))
        self._chk_lrc.setEnabled(has_lrc)
        self._chk_lrc.setChecked(has_lrc)
        row2.addWidget(self._chk_lrc)
        self._chk_backup = QCheckBox('Keep a backup copy')
        self._chk_backup.setChecked(False)
        row2.addWidget(self._chk_backup)
        for w in (self._fade_in, self._fade_out):
            w.valueChanged.connect(lambda _v: self._refresh_readout())
        layout.addLayout(row2)

        # row 3: transport + readout
        row3 = QHBoxLayout()
        row3.setSpacing(8)
        self._btn_preview = QPushButton('▶  Preview kept part')
        self._btn_preview.setObjectName('btnPrimary')
        self._btn_preview.clicked.connect(self._on_preview)
        self._btn_preview_end = QPushButton('▶  Last 5 s before end')
        self._btn_preview_end.setObjectName('btnSecondary')
        self._btn_preview_end.clicked.connect(self._on_preview_end)
        self._btn_stop = QPushButton('■  Stop')
        self._btn_stop.setObjectName('btnSecondary')
        self._btn_stop.clicked.connect(self._stop_preview)
        row3.addWidget(self._btn_preview)
        row3.addWidget(self._btn_preview_end)
        row3.addWidget(self._btn_stop)
        row3.addStretch(1)
        layout.addLayout(row3)

        self._readout = QLabel('')
        self._readout.setWordWrap(True)
        self._readout.setStyleSheet(f"color:{t.get('text', '#1d1d1f')};font-weight:600;")
        layout.addWidget(self._readout)
        self._status = QLabel('')
        self._status.setObjectName('dialogSubtitle')
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self._btn_close = QPushButton('Close')
        self._btn_close.setObjectName('btnSecondary')
        self._btn_close.clicked.connect(self.reject)
        self._btn_trim = QPushButton('Trim && Overwrite…')
        self._btn_trim.setObjectName('btnDestructive')
        self._btn_trim.setToolTip('Overwrites the original file. Asks twice.')
        self._btn_trim.clicked.connect(self._on_trim_clicked)
        footer.addWidget(self._btn_close)
        footer.addWidget(self._btn_trim)
        layout.addLayout(footer)

        if not self._player:
            for b in (self._btn_preview, self._btn_preview_end, self._btn_stop,
                      self._btn_start_here, self._btn_end_here):
                b.setEnabled(False)

    # ── analysis ─────────────────────────────────────────────────────────────

    def _start_analysis(self):
        self._wave.set_loading(True)
        self._btn_trim.setEnabled(False)
        worker = WaveformWorker(self._path)
        worker.ready.connect(self._on_peaks)
        worker.error.connect(self._on_peaks_error)
        worker.finished.connect(worker.deleteLater)
        worker.start()
        self._wave_worker = worker

    def _on_peaks(self, peaks: list, duration_ms: int):
        self._peaks = peaks
        self._duration = int(duration_ms)
        self._wave.set_peaks(peaks, duration_ms)
        self._wave.set_range(0, duration_ms)
        self._detected = detect_silence(peaks, duration_ms / max(1, len(peaks)))
        self._sync_fields()
        self._btn_trim.setEnabled(True)
        hints = []
        if self._detected['lead_end_ms']:
            hints.append(f'{format_ms(self._detected["lead_end_ms"])} of silence at the start')
        if self._detected['trail_start_ms'] < duration_ms:
            hints.append(f'silence from {format_ms(self._detected["trail_start_ms"])} to the end')
        if self._detected['gap_start_ms'] is not None:
            hints.append(f'a long gap at {format_ms(self._detected["gap_start_ms"])} '
                         f'(possible unexpected ending after it)')
        self._status.setText(('Detected: ' + '; '.join(hints) + '.') if hints
                             else 'No leading/trailing silence detected — set the range by hand.')

    def _on_peaks_error(self, message: str):
        self._wave.set_loading(False)
        self._status.setText(f'Could not analyse the audio: {message}')
        self._status.setStyleSheet(f"color:{self._theme.get('red', '#ff3b30')};")
        self._btn_trim.setEnabled(False)

    # ── range editing ────────────────────────────────────────────────────────

    def _sync_fields(self):
        self._updating = True
        start, end = self._wave.range()
        self._start_edit.setText(format_ms(start, 3))
        self._end_edit.setText(format_ms(end, 3))
        self._updating = False
        self._refresh_readout()

    def _on_range_dragged(self, start: int, end: int):
        self._sync_fields()

    def _on_fields_edited(self):
        if self._updating:
            return
        start = parse_time(self._start_edit.text())
        end = parse_time(self._end_edit.text())
        cur_start, cur_end = self._wave.range()
        if start is None:
            start = cur_start
        if end is None:
            end = cur_end
        start = max(0, min(start, self._duration))
        end = max(0, min(end, self._duration)) if self._duration else end
        if end - start < 500:
            end = min(self._duration, start + 500) if self._duration else start + 500
        self._wave.set_range(start, end)
        self._sync_fields()

    def _set_from_playhead(self, which: str):
        if not self._player:
            return
        ms = self._player.position_ms() if self._player.current_path() == self._path else 0
        start, end = self._wave.range()
        if which == 'start':
            self._wave.set_range(min(ms, end - 500), end)
        else:
            self._wave.set_range(start, max(ms, start + 500))
        self._sync_fields()

    def _on_detect_lead(self):
        if not self._detected:
            return
        start, end = self._wave.range()
        lead = self._detected['lead_end_ms']
        self._wave.set_range(lead, end)
        self._sync_fields()
        self._status.setText('Start moved to the end of the leading silence.' if lead
                             else 'No leading silence found — start left at 0:00.')

    def _on_detect_trail(self):
        if not self._detected:
            return
        start, _ = self._wave.range()
        trail = self._detected['trail_start_ms']
        gap = self._detected['gap_start_ms']
        target = trail
        note = 'End moved to where the trailing silence starts.'
        if gap is not None and gap > start + 500:
            target = gap
            note = ('End moved to the long gap before the unexpected ending. '
                    'Press again to use the trailing silence instead.')
            self._detected['gap_start_ms'] = None
        elif trail >= self._duration:
            note = 'No trailing silence found — end left at the song end.'
        self._wave.set_range(start, max(target, start + 500))
        self._sync_fields()
        self._status.setText(note)

    def _on_reset(self):
        self._wave.set_range(0, self._duration)
        self._wave.reset_zoom()
        self._sync_fields()
        if self._peaks:
            self._detected = detect_silence(self._peaks, self._duration / max(1, len(self._peaks)))

    def _refresh_readout(self):
        start, end = self._wave.range()
        removed_start = start
        removed_end = max(0, self._duration - end)
        kept = max(0, end - start)
        parts = []
        if removed_start:
            parts.append(f'{format_ms(removed_start)} at start')
        if removed_end:
            parts.append(f'{format_ms(removed_end)} at end')
        text = ('Removing ' + ' and '.join(parts) if parts else 'Nothing removed yet')
        text += f'  →  new length {format_ms(kept)} (was {format_ms(self._duration)})'
        if self._fade_in.value() or self._fade_out.value():
            text += '  ·  re-encode'
        self._readout.setText(text)

    # ── preview ──────────────────────────────────────────────────────────────

    def _play_from(self, ms: int, stop_at: int):
        if not self._player:
            return
        song = self._song
        if self._player.current_path() != self._path or not self._player._is_playing():
            self._player.play_song(
                self._path, song.get('title') or song.get('filename', ''),
                song.get('artist', ''), song.get('album', ''),
                song.get('cover_data') or b'', self._duration,
            )
            QTimer.singleShot(350, lambda: self._player.seek_ms(ms))
        else:
            self._player.seek_ms(ms)
        self._preview_timer.start(max(200, stop_at - ms + 400))

    def _on_preview(self):
        start, end = self._wave.range()
        self._play_from(start, end)

    def _on_preview_end(self):
        start, end = self._wave.range()
        self._play_from(max(start, end - self.PREVIEW_TAIL_MS), end)

    def _stop_preview(self):
        self._preview_timer.stop()
        if self._player and self._player.current_path() == self._path:
            self._player.stop()

    def _on_seek(self, ms: int):
        self._wave.set_playhead(ms)
        if self._player and self._player.current_path() == self._path:
            self._player.seek_ms(ms)

    def _on_player_position(self, ms: int):
        if self._player and self._player.current_path() == self._path:
            self._wave.set_playhead(ms)
            _, end = self._wave.range()
            if self._preview_timer.isActive() and ms >= end:
                self._stop_preview()

    # ── commit ───────────────────────────────────────────────────────────────

    def _on_trim_clicked(self):
        start, end = self._wave.range()
        name = self._song.get('filename') or Path(self._path).name
        if end - start < 500:
            QMessageBox.warning(self, 'Trim', 'The kept part must be at least half a second long.')
            return
        if start == 0 and end >= self._duration and not (self._fade_in.value() or self._fade_out.value()):
            QMessageBox.information(self, 'Trim', 'Nothing to trim — the whole song is selected.')
            return

        # 1st confirmation
        summary = (f'Keep {format_ms(start)} – {format_ms(end)} of "{name}".\n'
                   f'Removed: {format_ms(start)} at the start, {format_ms(max(0, self._duration - end))} at the end.\n'
                   f'New length: {format_ms(end - start)}.')
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle('Overwrite original file?')
        box.setText('The original file will be overwritten. This cannot be undone.')
        box.setInformativeText(summary + ('\n\nA .bak copy will be kept.' if self._chk_backup.isChecked() else ''))
        btn_continue = box.addButton('Continue…', QMessageBox.AcceptRole)
        box.addButton(QMessageBox.Cancel)
        box.setDefaultButton(QMessageBox.Cancel)
        box.exec_()
        if box.clickedButton() is not btn_continue:
            return

        # 2nd confirmation (explicit acknowledgement)
        confirm = _ConfirmOverwriteDialog(name, summary, self._theme, self)
        if confirm.exec_() != QDialog.Accepted:
            return

        self._stop_preview()
        self._btn_trim.setEnabled(False)
        self._status.setText('Trimming…')
        self._status.setStyleSheet('')
        worker = TrimWorker(self._path, start, end,
                            fade_in_ms=self._fade_in.value(),
                            fade_out_ms=self._fade_out.value(),
                            keep_backup=self._chk_backup.isChecked())
        worker.done.connect(self._on_trim_done)
        worker.error.connect(self._on_trim_error)
        worker.finished.connect(worker.deleteLater)
        worker.start()
        self._trim_worker = worker

    def _on_trim_done(self, result):
        if self._chk_lrc.isChecked() and result.removed_start_ms and self._lyrics_store:
            from lyrics_store import shift_lrc
            lrc = self._lyrics_store.lrc_path_for(self._path)
            if lrc:
                shift_lrc(lrc, -int(result.removed_start_ms))
        self.trimmed.emit(result)
        self.accept()

    def _on_trim_error(self, message: str):
        self._btn_trim.setEnabled(True)
        self._status.setText(f'Trim failed: {message}')
        self._status.setStyleSheet(f"color:{self._theme.get('red', '#ff3b30')};")
        QMessageBox.warning(self, 'Trim failed', message)

    def closeEvent(self, event):
        self._stop_preview()
        if self._wave_worker and self._wave_worker.isRunning():
            self._wave_worker.wait(500)
        super().closeEvent(event)

    def reject(self):
        self._stop_preview()
        super().reject()
