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
)
from PyQt5.QtCore import Qt, pyqtSignal, QSize
from PyQt5.QtGui import QFont, QColor, QIcon, QFontMetrics, QPixmap

from pathlib import Path

from workers import SearchWorker, ImageDownloadWorker
from search import build_query
from image_utils import load_cropped_pixmap, icon_from_file
from icons import get_icon
from tags_list import display_for, suggest_identifier


def _apply_overlay_chrome(dlg: 'QDialog', theme: dict | None = None) -> None:
    """Turn a QDialog into a frameless, centered-in-parent card.

    Drops the OS title bar and re-centers the dialog over its parent on
    every show. A small ✕ in the top-right corner replaces the missing
    close button.  The card is painted with a SOLID background pulled
    from `theme` (no translucency, no rounded corners) — translucent
    frameless windows render with BLACK corners on Linux systems without
    a compositor, which made the dialog look stuck in dark mode even when
    the app was on light theme.
    """
    if theme is None:
        theme = getattr(dlg.parent(), '_theme', None) or {}
    bg     = theme.get('bg_card',  '#0f1320')
    border = theme.get('border',   '#1f2937')
    dim    = theme.get('text_dim', '#94a3b8')
    err    = theme.get('error_fg', '#f87171')

    dlg.setWindowFlags(dlg.windowFlags() | Qt.FramelessWindowHint)
    # Solid opaque background — guaranteed to look right on every WM.
    dlg.setStyleSheet(
        (dlg.styleSheet() or '')
        + '\nQDialog{background:%s;border:1px solid %s;}'
        % (bg, border)
    )

    # in-card close button (top-right)
    close_btn = QPushButton('✕', dlg)
    close_btn.setObjectName('btnOverlayClose')
    close_btn.setFixedSize(28, 28)
    close_btn.setStyleSheet(
        'QPushButton#btnOverlayClose{background:transparent;color:%s;'
        'border:none;font-size:18px;font-weight:600;}'
        'QPushButton#btnOverlayClose:hover{color:%s;}'
        % (dim, err)
    )
    close_btn.clicked.connect(dlg.reject)
    close_btn.raise_()
    dlg._overlay_close_btn = close_btn

    _orig_show = dlg.showEvent
    _orig_resize = dlg.resizeEvent

    def _show(event):
        _orig_show(event)
        p = dlg.parent()
        if p is not None:
            pr = p.frameGeometry()
            dlg.move(pr.center() - dlg.rect().center())
        close_btn.move(dlg.width() - close_btn.width() - 10, 10)
        close_btn.raise_()

    def _resize(event):
        _orig_resize(event)
        close_btn.move(dlg.width() - close_btn.width() - 10, 10)
        close_btn.raise_()

    dlg.showEvent = _show       # type: ignore[assignment]
    dlg.resizeEvent = _resize   # type: ignore[assignment]


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

        self._tag_list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._tag_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
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
        self._fit_tag_grid_height(gw, gh)

    def _fit_tag_grid_height(self, gw: int, gh: int):
        """Resize the tag grid to show every chip without scrolling."""
        n = len(self._tag_items)
        if n == 0:
            self._tag_list.setFixedHeight(gh + 12)
            return
        viewport_w = max(1, self._tag_list.viewport().width() or self._tag_list.width())
        cols = max(1, viewport_w // max(1, gw))
        rows = max(1, (n + cols - 1) // cols)
        self._tag_list.setFixedHeight(rows * gh + 12)

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
        if issue_type == 'invalid_tag':
            self._btn_fix.setText('Remove Invalid Tags')
            self._btn_fix.show()
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


class PlaylistJsonReportDialog(QDialog):
    """Detailed JSON diagnostics dialog shown on demand."""

    def __init__(
        self,
        json_path: str,
        summary: str,
        mismatch_records: list[dict],
        invalid_tag_records: list[dict],
        image_decode_failures: int,
        parent=None,
    ):
        super().__init__(parent)
        self._json_path = json_path
        self._summary = summary
        self._mismatch_records = mismatch_records
        self._invalid_tag_records = invalid_tag_records
        self._image_decode_failures = image_decode_failures

        self.setWindowTitle('JSON Report')
        self.setModal(True)
        if parent:
            self.resize(int(parent.width() * 0.8), int(parent.height() * 0.75))
        else:
            self.resize(980, 700)
        self.setMinimumSize(700, 500)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 16)
        layout.setSpacing(10)

        lbl_title = QLabel('Tag JSON Diagnostic Report')
        lbl_title.setObjectName('dialogTitle')
        layout.addWidget(lbl_title)

        path_name = self._json_path or 'No JSON selected'
        lbl_path = QLabel(f'Path: {path_name}')
        lbl_path.setObjectName('dialogSubtitle')
        lbl_path.setWordWrap(True)
        layout.addWidget(lbl_path)

        lbl_summary = QLabel(self._summary)
        lbl_summary.setObjectName('dialogSubtitle')
        lbl_summary.setWordWrap(True)
        layout.addWidget(lbl_summary)

        lbl_img = QLabel(f'Image decode failures: {self._image_decode_failures}')
        lbl_img.setObjectName('dialogSubtitle')
        layout.addWidget(lbl_img)

        issues = QTextEdit()
        issues.setReadOnly(True)
        issues.setObjectName('songGrid')
        issues.setFont(QFont('Consolas', 10))
        issues.setPlainText(self._build_report_text())
        layout.addWidget(issues, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_close = QPushButton('Close')
        btn_close.setObjectName('btnOk')
        btn_close.clicked.connect(self.accept)
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)

    def _build_report_text(self) -> str:
        lines: list[str] = []

        if self._mismatch_records:
            lines.append('JSON mismatch records:')
            for entry in self._mismatch_records:
                lines.append(f'  - {_format_mismatch(entry)}')
        else:
            lines.append('JSON mismatch records: none')

        if self._invalid_tag_records:
            lines.append('')
            lines.append('Invalid tag groups:')
            for entry in self._invalid_tag_records:
                tags = ', '.join(entry.get('tags', []))
                lines.append(f'  - {entry.get("song", "Unknown")} -> {tags}')
        else:
            lines.append('')
            lines.append('Invalid tag groups: none')

        return '\n'.join(lines)


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
    return _format_mismatch(issue)


def _format_issue_summary(issue: dict) -> str:
    if issue.get('issue_type') == 'invalid_tag':
        tags = ', '.join(issue.get('tags', [])) or 'unknown tag'
        return f'JSON contains invalid tag names for this song: {tags}'
    return _format_mismatch(issue)


def _match_source_label(source: str) -> str:
    return {
        'path': 'path',
        'basename': 'filename',
        'filename': 'filename',
        'title_filename': 'title filename',
    }.get(source, 'record')

def _trunc(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n - 1] + '…'


# ── Sequenze: per-song focused editor ───────────────────────────────────────

class SongSequencerDialog(QDialog):
    """Centered single-song editor — walk through a filter selection one song
    at a time. Edit metadata, tags, and filename, with a play/stop control
    that drives the main window's player bar. Emits signals for rename /
    metadata / tag changes so the main window can perform the actual writes
    against its own state."""

    rename_requested   = pyqtSignal(str, str)        # (old_path, new_filename)
    metadata_requested = pyqtSignal(str, str, str, str)  # (path, title, artist, album)
    tags_requested     = pyqtSignal(str, set, set)   # (path, add_tags, remove_tags)
    committed          = pyqtSignal()                # fired after every per-song commit

    def __init__(self,
                 songs: list,
                 filter_label: str,
                 tag_icons: dict,
                 existing_tags_provider,
                 json_filename_provider,
                 player_bar,
                 theme: dict | None = None,
                 start_index: int = 0,
                 parent=None):
        super().__init__(parent)
        self._theme = theme or (getattr(parent, '_theme', None) or {})
        self._songs = list(songs)
        self._filter_label = filter_label
        self._tag_icons = tag_icons
        self._get_tags = existing_tags_provider
        self._get_json_filename = json_filename_provider
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
        # Background sheet that ALL child widgets inherit from so the
        # dialog feels light/dark consistently (overrides any leftover
        # dark backgrounds from earlier hardcoded sheets).
        self.setStyleSheet(
            f'QWidget{{background:{self._theme.get("bg", "#f8fafc")};'
            f'color:{self._theme.get("text", "#1e293b")};}}'
            f'QLineEdit{{background:{self._theme.get("bg_input", "#ffffff")};'
            f'color:{self._theme.get("text", "#1e293b")};'
            f'border:1px solid {self._theme.get("border", "#e2e8f0")};'
            f'border-radius:6px;padding:6px 10px;}}'
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
        play_row.addWidget(self._btn_play)
        play_row.addWidget(self._btn_stop)

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
        fn_row.addWidget(self._btn_use_json)
        fn_row.addWidget(self._btn_use_file)
        layout.addLayout(fn_row)

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
        self._tag_list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._tag_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._tag_list.setStyleSheet(
            'QListWidget#seqTagGrid{background:transparent;border:none;}'
            'QListWidget#seqTagGrid::item{'
            '  border:1.5px solid rgba(127,127,127,0.35);'
            '  border-radius:8px;'
            '  padding:4px;'
            '}'
            'QListWidget#seqTagGrid::item:hover{border-color:#2563eb;}'
        )
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
        self._btn_save_next = QPushButton('Save & Next  ▶')
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
            item.setData(Qt.UserRole, tag_name)
            # Custom selection flag — UserRole+1 holds True/False.
            item.setData(Qt.UserRole + 1, tag_name in current_tags)
            item.setTextAlignment(Qt.AlignCenter)
            self._tag_list.addItem(item)
            self._tag_items.append(item)
            self._paint_tag(item)
        self._fit_tag_height()

        # Cover preview
        cover_data = song.get('cover_data') or b''
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
            'filename': self._filename_edit.text(),
            'title': self._title_edit.text(),
            'artist': self._artist_edit.text(),
            'album': self._album_edit.text(),
            'tags': set(current_tags),
        }
        self._btn_prev.setEnabled(self._index > 0)
        self._btn_save_next.setEnabled(self._index < len(self._songs) - 1)
        self._refresh_selected_preview()

    def _commit_current(self):
        song = self._current_song()
        if not song:
            return
        path = song.get('path', '')
        baseline = self._baseline.get(path, {})
        any_change = False

        # Filename rename
        new_filename = self._filename_edit.text().strip()
        if new_filename and new_filename != baseline.get('filename', ''):
            self.rename_requested.emit(path, new_filename)
            any_change = True

        # Metadata
        title  = self._title_edit.text().strip()
        artist = self._artist_edit.text().strip()
        album  = self._album_edit.text().strip()
        if (title != baseline.get('title', '') or
                artist != baseline.get('artist', '') or
                album != baseline.get('album', '')):
            self.metadata_requested.emit(path, title, artist, album)
            any_change = True

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
            any_change = True

        # Tell the parent to flush to disk so the user's edit survives even
        # if the dialog is killed mid-session.
        if any_change:
            self.committed.emit()

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

    def _fit_tag_height(self):
        n = len(self._tag_items)
        if n == 0:
            self._tag_list.setFixedHeight(72)
            return
        gw = self._tag_list.gridSize().width()
        gh = self._tag_list.gridSize().height()
        viewport_w = max(1, self._tag_list.viewport().width() or self._tag_list.width())
        cols = max(1, viewport_w // max(1, gw))
        rows = max(1, (n + cols - 1) // cols)
        self._tag_list.setFixedHeight(rows * gh + 12)

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
        self._commit_current()
        # refresh baseline from currently-shown values
        song = self._current_song()
        if song:
            self._baseline[song.get('path', '')] = {
                'filename': self._filename_edit.text(),
                'title': self._title_edit.text(),
                'artist': self._artist_edit.text(),
                'album': self._album_edit.text(),
                'tags': {
                    item.data(Qt.UserRole) for item in self._tag_items
                    if item.data(Qt.UserRole + 1)
                },
            }

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

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_tag_height()


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

