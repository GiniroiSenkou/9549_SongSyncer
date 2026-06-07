"""
main.py – SongSyncer
Assign, replace, or delete embedded cover art on your music library.

Usage:
    pip install -r requirements.txt
    python main.py
"""

import sys
import os
import queue
import re
from collections import deque
from pathlib import Path
from datetime import datetime

def _configure_qt_platform():
    """
    Pick a stable Qt platform plugin before importing PyQt5.
    On some Linux Wayland setups, forcing xcb avoids startup crashes.
    """
    if os.name != 'posix':
        return
    if os.environ.get('QT_QPA_PLATFORM'):
        return

    forced = os.environ.get('SONGSYNCER_QT_PLATFORM', '').strip().lower()
    if forced:
        os.environ['QT_QPA_PLATFORM'] = forced
        return

    is_wayland = (
        os.environ.get('XDG_SESSION_TYPE', '').lower() == 'wayland'
        or bool(os.environ.get('WAYLAND_DISPLAY'))
    )
    if is_wayland and os.environ.get('DISPLAY'):
        os.environ['QT_QPA_PLATFORM'] = 'xcb'


_configure_qt_platform()

# Suppress Qt ICC / image-codec warnings (but keep stderr open for GStreamer)
os.environ.setdefault('QT_LOGGING_RULES',
                       'qt.gui.icc.warning=false;qt.multimedia.*=false;qt.qpa.wayland.warning=false')

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFileDialog, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QFrame, QSizePolicy, QMessageBox,
    QStatusBar, QProgressBar, QListWidget, QListWidgetItem, QStackedWidget,
    QMenu, QToolButton, QLineEdit,
)
from PyQt5.QtCore import (
    Qt, QSize, QSettings, QPropertyAnimation, QEasingCurve, QTimer,
    QVariantAnimation, pyqtSignal,
)
import time as _time
from PyQt5.QtGui import QPixmap, QIcon, QFont, QPainter, QColor, QFontMetrics, QPen, QImage
from PyQt5.QtWidgets import QGraphicsOpacityEffect

from themes import LIGHT, THEMES, THEME_ORDER
from style import build_stylesheet
from icons import get_icon, set_custom_icon_dir
from player import PlayerBar
from cover_art import (
    write_cover, delete_cover, write_metadata, clear_metadata, read_cover,
    SUPPORTED_EXTENSIONS,
)
from workers import ScanWorker, ThumbnailWorker, AutoAssignWorker, LyricsFetchWorker
from workers import PlaylistReconcileWorker
from dialogs import (
    ImagePickerDialog, ProgressDialog, MetadataReviewDialog,
    PlaylistAssignmentDialog, PlaylistJsonReportDialog, PlaylistSongIssueDialog,
    SongSequencerDialog, UnsupportedTagConverterDialog, SettingsPanel,
    IconManagerDialog,
)
from playlist_store import PlaylistAssignmentStore, default_playlist_json_path
from lyrics_store import LyricsStore
from tags_list import TAG_ICONS, resolve_tag_icon, display_for
from image_utils import (
    load_cropped_pixmap, get_decode_failures, reset_decode_failures,
    icon_from_file,
)


# ── Column indices (10-column table) ─────────────────────────────────────────
COL_COVER    = 0
COL_FILENAME = 1   # actual filename on disk (read-only)
COL_TITLE    = 2   # metadata title tag
COL_ARTIST   = 3   # metadata artist tag
COL_ALBUM    = 4   # metadata album tag
COL_LYRICS   = 5   # lyrics presence icon (yes/no)
COL_TAGS     = 6   # assigned tag preview icons
COL_ISSUES   = 7   # JSON warning / mismatch indicator
COL_FMT      = 8
COL_SIZE     = 9   # file size on disk

THUMB_SIZE    = 56    # table thumbnail px
GRID_ICON     = 120   # grid card icon px
GRID_ITEM_W   = 178
GRID_ITEM_H   = 238
ROW_HEIGHT    = 84
THUMB_OVERSCAN_ROWS = 10   # rows above/below the viewport to preload


# ── Numeric-sortable table item ───────────────────────────────────────────────

class _NumericItem(QTableWidgetItem):
    """QTableWidgetItem that sorts by its Qt.UserRole numeric value."""
    def __lt__(self, other):
        try:
            return self.data(Qt.UserRole) < other.data(Qt.UserRole)
        except TypeError:
            return super().__lt__(other)


# ── Song Table (click-to-deselect) ────────────────────────────────────────────

class SongTable(QTableWidget):
    """
    QTableWidget with two extra interaction behaviours:
    • Click on empty space  → clear all selection.
    • Click the one already-selected row (no modifier) → deselect it.
    """

    def mousePressEvent(self, event):
        idx = self.indexAt(event.pos())

        if not idx.isValid():
            self.clearSelection()
            return

        selected_rows = {i.row() for i in self.selectedIndexes()}
        if (
            not event.modifiers()
            and len(selected_rows) == 1
            and idx.row() in selected_rows
        ):
            self.clearSelection()
            return

        super().mousePressEvent(event)


# ── Stat Card ─────────────────────────────────────────────────────────────────

class StatCard(QFrame):
    """Compact inline stat: colored number + tiny label, no card border."""

    clicked = pyqtSignal()

    def __init__(self, label: str, color: str = '#7c3aed', parent=None):
        super().__init__(parent)
        self.setObjectName('statCardInline')
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 2, 10, 2)
        layout.setSpacing(0)

        self._val = QLabel('0')
        self._val.setAlignment(Qt.AlignCenter)
        self._color = color
        self._val.setStyleSheet(
            f'color: {color}; font-size: 48px; font-weight: 700;'
        )

        self._lbl = QLabel(label.upper())
        self._lbl.setAlignment(Qt.AlignCenter)
        self._lbl.setStyleSheet(
            'font-size: 11px; letter-spacing: 1px; font-weight: 600;'
        )
        self._lbl.setObjectName('statLabel')

        layout.addWidget(self._val)
        layout.addWidget(self._lbl)

        self._current = 0
        self._count_anim = None
        self._active = False

    def set_value(self, n: int):
        if n == self._current:
            return
        old = self._current
        self._current = n
        if self._count_anim is not None:
            try:
                self._count_anim.stop()
            except RuntimeError:
                # Animation QObject may already be deleted by Qt.
                pass
            self._count_anim = None
        anim = QVariantAnimation(self)
        anim.setStartValue(old)
        anim.setEndValue(n)
        anim.setDuration(min(350, 80 * abs(n - old)))
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.valueChanged.connect(lambda v: self._val.setText(str(v)))
        self._count_anim = anim
        def _on_finished(a=anim):
            if self._count_anim is a:
                self._count_anim = None
            a.deleteLater()
        anim.finished.connect(_on_finished)
        anim.start()
        self._pulse()

    def _pulse(self):
        """Briefly scale up the font then back down."""
        self._val.setStyleSheet(
            f'color: {self._color}; font-size: 56px; font-weight: 700;'
        )
        QTimer.singleShot(180, lambda: self._val.setStyleSheet(
            f'color: {self._color}; font-size: 48px; font-weight: 700;'
        ))

    def set_active(self, active: bool):
        self._active = active
        border = self._color if active else 'transparent'
        bg = 'rgba(255,255,255,0.04)' if active else 'transparent'
        self.setStyleSheet(
            f'#statCardInline {{ border: 1px solid {border}; border-radius: 10px; background: {bg}; }}'
        )

    def mousePressEvent(self, event):
        self.clicked.emit()
        super().mousePressEvent(event)


class StatRow(QWidget):
    """One clickable line inside a StatSection: small colored bullet · label ·
    value badge. Emits ``clicked`` and carries the filter mode the click
    should activate. Theme-aware via :meth:`apply_theme`."""

    clicked = pyqtSignal()

    def __init__(self, label: str, color: str, filter_mode: str,
                 theme: dict, parent=None):
        super().__init__(parent)
        self.filter_mode = filter_mode
        self._color = color
        self._active = False
        self._theme = theme
        self.setObjectName('statRow')
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(28)

        h = QHBoxLayout(self)
        h.setContentsMargins(8, 0, 8, 0)
        h.setSpacing(8)

        self._bullet = QFrame()
        self._bullet.setFixedSize(8, 8)
        self._bullet.setStyleSheet(f'background:{color};border-radius:4px;')
        h.addWidget(self._bullet, 0, Qt.AlignVCenter)

        self._label = QLabel(label)
        h.addWidget(self._label, 1, Qt.AlignVCenter)

        self._value = QLabel('0')
        self._value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._value.setMinimumWidth(34)
        h.addWidget(self._value, 0, Qt.AlignVCenter)

        self.apply_theme(theme)

    def set_value(self, n: int):
        self._value.setText(str(n))

    def set_active(self, active: bool):
        if active == self._active:
            return
        self._active = active
        self._apply_active_style()

    def apply_theme(self, theme: dict):
        self._theme = theme
        self._label.setStyleSheet(
            f"color:{theme.get('text', '#1e293b')};font-size:12px;"
        )
        self._value.setStyleSheet(
            f'color:{self._color};font-weight:700;font-size:14px;'
        )
        self._apply_active_style()

    def _apply_active_style(self):
        hover = self._theme.get('bg_hover', 'rgba(0,0,0,0.04)')
        sel   = self._theme.get('bg_selection', hover)
        if self._active:
            self.setStyleSheet(
                f'QWidget#statRow{{background:{sel};border-radius:6px;'
                f'border-left:3px solid {self._color};}}'
            )
        else:
            self.setStyleSheet(
                'QWidget#statRow{background:transparent;border-radius:6px;'
                'border-left:3px solid transparent;}'
                f'QWidget#statRow:hover{{background:{hover};}}'
            )

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class StatSection(QFrame):
    """A small card grouping related counters: section header + N StatRows.
    Theme-aware via :meth:`apply_theme`."""

    def __init__(self, title: str, theme: dict, parent=None):
        super().__init__(parent)
        self._rows: dict[str, StatRow] = {}    # filter_mode -> StatRow
        self._theme = theme
        self.setObjectName('statSection')

        self._v = QVBoxLayout(self)
        self._v.setContentsMargins(10, 8, 10, 8)
        self._v.setSpacing(2)

        self._title_lbl = QLabel(title.upper())
        self._v.addWidget(self._title_lbl)
        self.apply_theme(theme)

    def add_row(self, row: StatRow):
        self._rows[row.filter_mode] = row
        self._v.addWidget(row)

    def row(self, filter_mode: str) -> StatRow | None:
        return self._rows.get(filter_mode)

    def rows(self):
        return self._rows.values()

    def apply_theme(self, theme: dict):
        self._theme = theme
        bg     = theme.get('bg_card', '#ffffff')
        border = theme.get('border', '#e2e8f0')
        dim    = theme.get('text_muted', '#64748b')
        self.setStyleSheet(
            'QFrame#statSection{background:%s;border:1px solid %s;'
            'border-radius:10px;}' % (bg, border)
        )
        self._title_lbl.setStyleSheet(
            f'color:{dim};font-size:10px;font-weight:700;letter-spacing:1px;'
        )
        for row in self._rows.values():
            row.apply_theme(theme)


class ElidedLabel(QLabel):
    """Label that keeps a full string internally and elides to the available width."""

    def __init__(self, text: str = '', parent=None):
        super().__init__(parent)
        self._full_text = ''
        self.set_full_text(text)

    def set_full_text(self, text: str):
        self._full_text = text or ''
        self._apply_elide()

    def full_text(self) -> str:
        return self._full_text

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_elide()

    def _apply_elide(self):
        if not self._full_text:
            super().setText('')
            return
        text = self.fontMetrics().elidedText(
            self._full_text,
            Qt.ElideLeft,
            max(self.contentsRect().width() - 4, 24),
        )
        super().setText(text)


class AutoFitButton(QPushButton):
    """QPushButton that keeps its label readable inside whatever width the
    layout gives it.

    Strategy (best fit first):
      1. Single line at the largest pixel size in [_FIT_COMFORT_PX, _FIT_MAX_PX]
         that fits next to the icon.
      2. If single-line can't reach a comfortable size, split the text at the
         space closest to the middle and try **two-line** at the same range.
      3. As a last resort, keep shrinking single-line down to _FIT_MIN_PX.

    Re-fits on every resize and whenever ``setText`` / ``setIcon`` is called.

    Note: the font size is applied via stylesheet (not ``setFont``) because
    the global ``QPushButton { font-size: 15px; }`` rule in style.py takes
    precedence over ``setFont`` per Qt's stylesheet semantics.
    """

    _FIT_MIN_PX     = 8
    _FIT_COMFORT_PX = 11
    _FIT_MAX_PX     = 15
    _STYLE_MARKER   = '/*autofit*/'

    # Hysteresis margin (px): once wrapped, only un-wrap when single-line
    # comfortably fits with at least this much spare width.
    _FIT_UNWRAP_MARGIN = 14

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._fit_current_px: int | None = None
        # The original, single-line label provided by the caller. We may
        # render it as two lines internally without losing the source string.
        self._fit_base_text: str = super().text()
        self._fitting: bool = False
        # Debounce timer: coalesce rapid resize events into one fit pass.
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.setInterval(30)
        self._fit_timer.timeout.connect(self._fit_text)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_timer.start(30)

    def setText(self, text):
        super().setText(text)
        if not self._fitting:
            self._fit_base_text = text or ''
            self._fit_timer.start(30)

    def setIcon(self, icon):
        super().setIcon(icon)
        self._fit_timer.start(30)

    # ── fitting -------------------------------------------------------------

    def _fit_text(self):
        if self._fitting:
            return
        text = self._fit_base_text
        if not text:
            return
        icon_w = self.iconSize().width() if not self.icon().isNull() else 0
        # style.py: padding 16 px each side + ~6 px icon↔text gap + 2 safety
        avail = self.width() - icon_w - 40
        if avail <= 0:
            return

        # Hysteresis: if we're already wrapped, require a generous fit before
        # un-wrapping so the layout doesn't bounce single ↔ wrapped when the
        # button width is right at the threshold (the cause of the
        # "milliseconds flicker at full-screen").
        currently_wrapped = '\n' in super().text()
        unwrap_avail = avail - (self._FIT_UNWRAP_MARGIN if currently_wrapped else 0)

        # 1) single-line, MAX → COMFORT
        for px in range(self._FIT_MAX_PX, self._FIT_COMFORT_PX - 1, -1):
            if self._fits(text, px, unwrap_avail):
                self._apply(px, text)
                return

        # 2) two-line wrap, MAX → COMFORT
        wrapped = self._wrap_at_middle(text)
        if wrapped != text:
            for px in range(self._FIT_MAX_PX, self._FIT_COMFORT_PX - 1, -1):
                if self._fits(wrapped, px, avail):
                    self._apply(px, wrapped)
                    return

        # 3) single-line, COMFORT-1 → MIN
        for px in range(self._FIT_COMFORT_PX - 1, self._FIT_MIN_PX - 1, -1):
            if self._fits(text, px, avail):
                self._apply(px, text)
                return

        self._apply(self._FIT_MIN_PX, text)

    def _fits(self, text: str, px: int, avail: int) -> bool:
        font = QFont(self.font())
        font.setPixelSize(px)
        fm = QFontMetrics(font)
        # All lines must fit.
        for line in text.split('\n'):
            if fm.horizontalAdvance(line) > avail:
                return False
        return True

    @staticmethod
    def _wrap_at_middle(text: str) -> str:
        """Split `text` at the space closest to its midpoint. Returns the
        unchanged text if there's no usable space."""
        if '\n' in text or ' ' not in text:
            return text
        spaces = [i for i, ch in enumerate(text) if ch == ' ']
        if not spaces:
            return text
        mid = len(text) // 2
        best = min(spaces, key=lambda i: abs(i - mid))
        return text[:best] + '\n' + text[best + 1:]

    def _apply(self, px: int, text: str):
        # Update the visible text only when it actually differs (avoids
        # triggering layout churn on every fit pass).
        if super().text() != text:
            self._fitting = True
            super().setText(text)
            self._fitting = False
        if self._fit_current_px == px:
            return
        self._fit_current_px = px
        existing = self.styleSheet() or ''
        if self._STYLE_MARKER in existing:
            existing = existing.split(self._STYLE_MARKER, 1)[0].rstrip()
        suffix = f'{self._STYLE_MARKER} QPushButton {{ font-size: {px}px; }}'
        self.setStyleSheet(f'{existing}\n{suffix}' if existing else suffix)


class SongGridCard(QFrame):
    """Grid card with cover, metadata, and assigned tag previews."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('songGridCard')
        self.setFixedSize(GRID_ITEM_W - 12, GRID_ITEM_H - 12)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self._cover_lbl = QLabel()
        self._cover_lbl.setAlignment(Qt.AlignCenter)
        self._cover_lbl.setFixedSize(GRID_ICON, GRID_ICON)
        self._cover_lbl.setStyleSheet('background:#0a0a14; border-radius:8px;')

        self._title_lbl = QLabel('')
        self._title_lbl.setAlignment(Qt.AlignCenter)
        self._title_lbl.setWordWrap(True)
        self._title_lbl.setStyleSheet('font-size:11px; font-weight:600; color:#e2e8f0;')

        self._artist_lbl = QLabel('')
        self._artist_lbl.setAlignment(Qt.AlignCenter)
        self._artist_lbl.setWordWrap(True)
        self._artist_lbl.setStyleSheet('font-size:10px; color:#94a3b8;')

        self._tags_lbl = QLabel('—')
        self._tags_lbl.setAlignment(Qt.AlignCenter)
        self._tags_lbl.setWordWrap(True)
        self._tags_lbl.setFixedHeight(42)
        self._tags_lbl.setTextFormat(Qt.RichText)
        self._tags_lbl.setStyleSheet('color:#64748b;')

        layout.addWidget(self._cover_lbl, 0, Qt.AlignHCenter)
        layout.addWidget(self._title_lbl)
        layout.addWidget(self._artist_lbl)
        layout.addWidget(self._tags_lbl)

    def set_cover(self, pixmap: QPixmap):
        self._cover_lbl.setPixmap(pixmap)

    def set_text(self, title: str, artist: str):
        self._title_lbl.setText(title)
        self._artist_lbl.setText(artist)
        self._artist_lbl.setVisible(bool(artist))

    def set_tags(self, html: str, tooltip: str):
        self._tags_lbl.setText(html or '—')
        self._tags_lbl.setToolTip(tooltip)


# ── Main Window ───────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        # ── state ──────────────────────────────────────────────────────────────
        self._songs: dict[str, dict] = {}
        self._path_to_grid_item: dict[str, QListWidgetItem] = {}
        self._path_to_grid_card: dict[str, SongGridCard] = {}
        self._path_to_cover_item: dict[str, QTableWidgetItem] = {}
        self._path_to_title_item: dict[str, QTableWidgetItem] = {}
        self._path_to_artist_item: dict[str, QTableWidgetItem] = {}
        self._path_to_album_item: dict[str, QTableWidgetItem] = {}
        self._path_to_lyrics_label: dict[str, QLabel] = {}
        self._path_to_tag_label: dict[str, QLabel] = {}
        self._path_to_issue_button: dict[str, QToolButton] = {}
        self._playlist_issues_by_path: dict[str, list[dict]] = {}
        self._grid_build_queue: deque[str] = deque()
        self._grid_chunk_timer: QTimer | None = None
        self._is_scanning: bool = False
        self._folder: str = ''
        self._settings = QSettings('SongSyncer', 'SongSyncer')
        self._tag_icons = {
            name: path for name, path in TAG_ICONS.items() if os.path.exists(path)
        }
        self._playlist_store = PlaylistAssignmentStore()
        self._playlist_json_path = (
            self._settings.value('playlist_json_path', default_playlist_json_path()) or
            default_playlist_json_path()
        )
        self._lyrics_store = LyricsStore()
        lyrics_folder = self._settings.value('lyrics_folder', '') or ''
        if lyrics_folder:
            self._lyrics_store.set_folder(lyrics_folder)
        self._lyrics_fetch_worker: LyricsFetchWorker | None = None
        # Pending sync state — set while the Settings page is open, run when
        # the user closes Settings. Lets the app start without any
        # background work until the user is ready.
        self._pending_music_scan: str | None = None
        self._pending_json_load: str | None = None
        self._pending_lyrics_scan: bool = False
        self._active_song_filter = 'all'
        self._search_query: str = ''
        self._ghost_paths: set[str] = set()
        self._current_play_path: str = ''
        reset_decode_failures()

        # theme
        theme_name = self._settings.value('theme', 'light')
        self._theme = THEMES.get(theme_name, LIGHT)

        # custom icon directory
        custom_icon_dir = self._settings.value('custom_icon_dir', '')
        if custom_icon_dir:
            set_custom_icon_dir(custom_icon_dir)

        self._scan_worker:   ScanWorker | None    = None
        self._thumb_worker:  ThumbnailWorker | None = None
        self._assign_worker: AutoAssignWorker | None = None
        self._reconcile_worker: PlaylistReconcileWorker | None = None
        self._thumb_queue: queue.Queue = queue.Queue()
        self._thumbs_queued: set[str] = set()
        self._thumb_scroll_timer: QTimer | None = None
        self._progress_dlg:  ProgressDialog | None  = None
        self._playlist_reconcile_seq = 0
        self._playlist_reconcile_applied_seq = 0
        self._playlist_reconcile_pending = False
        self._playlist_reconcile_rerun_needed = False
        self._action_layout_update_pending = False

        # ── window ─────────────────────────────────────────────────────────────
        self.setWindowTitle('SongSyncer')
        self.setMinimumSize(960, 680)
        self.resize(1280, 860)

        # window icon
        _icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'icon.png')
        if os.path.exists(_icon_path):
            self.setWindowIcon(icon_from_file(_icon_path, 64))

        # ── build UI ──────────────────────────────────────────────────────────
        central = QWidget()
        central.setObjectName('centralWidget')
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_header())

        # Library content (action bar + song view) lives on page 0 of an
        # outer stack; page 1 is the in-place Settings panel.
        library_widget = QWidget()
        library_layout = QVBoxLayout(library_widget)
        library_layout.setContentsMargins(0, 0, 0, 0)
        library_layout.setSpacing(0)
        library_layout.addWidget(self._build_action_bar())
        library_layout.addWidget(self._build_song_view(), 1)

        self._settings_panel = SettingsPanel(
            theme=self._theme,
            music_path=self._settings.value('last_folder', '') or '',
            json_path=self._settings.value('playlist_json_path', '') or '',
            lyrics_path=self._settings.value('lyrics_folder', '') or '',
            parent=self,
        )
        self._settings_panel.music_folder_chosen.connect(self._on_settings_music_chosen)
        self._settings_panel.json_file_chosen.connect(self._on_settings_json_chosen)
        self._settings_panel.lyrics_folder_chosen.connect(self._on_settings_lyrics_chosen)
        self._settings_panel.music_refresh_requested.connect(self._on_settings_music_refresh)
        self._settings_panel.json_refresh_requested.connect(self._on_settings_json_refresh)
        self._settings_panel.lyrics_refresh_requested.connect(self._on_settings_lyrics_refresh)
        self._settings_panel.icon_manager_requested.connect(self._on_manage_icons)
        self._settings_panel.close_requested.connect(self._on_settings_close)

        self._main_stack = QStackedWidget()
        self._main_stack.addWidget(library_widget)
        self._main_stack.addWidget(self._settings_panel)
        root.addWidget(self._main_stack, 1)

        # player bar (slides up from the bottom)
        self._player_bar = PlayerBar(self)
        root.addWidget(self._player_bar)
        self._player_bar.prev_requested.connect(self._on_player_prev)
        self._player_bar.next_requested.connect(self._on_player_next)
        self._player_bar.shuffle_toggled.connect(self._on_shuffle_toggled)
        self._player_bar.set_shuffle(
            self._settings.value('shuffle', 'false') == 'true'
        )

        self._build_statusbar()
        self._set_buttons_enabled(False)
        QTimer.singleShot(0, lambda: self._update_action_button_layout(self.width()))

        # ── restore last session ───────────────────────────────────────────────
        # If a music folder was saved from a previous session, skip the Settings
        # panel and auto-load everything in the correct order: JSON is queued
        # first (loaded synchronously in _run_pending_sync before the scan
        # starts) → music scan → lyrics (handled inside _on_scan_complete).
        last = self._settings.value('last_folder', '')
        saved_json = self._settings.value('playlist_json_path', '') or ''
        if last and os.path.isdir(last):
            self._folder = last
            self._pending_music_scan = last
            if saved_json:
                self._pending_json_load = saved_json
            if self._lyrics_store.get_folder():
                self._pending_lyrics_scan = True
            # Paths configured — go straight to loading, skip Settings.
            QTimer.singleShot(0, self._run_pending_sync)
        else:
            # First run or folder was removed — open Settings to configure.
            QTimer.singleShot(0, self._on_open_settings)

    # =========================================================================
    # UI construction
    # =========================================================================

    def _icon_color(self) -> str:
        """Return the current theme's secondary text color for icon tinting."""
        return self._theme['text_secondary']

    def _build_header(self) -> QWidget:
        w = QWidget()
        w.setObjectName('header')
        w.setFixedHeight(110)

        h = QHBoxLayout(w)
        h.setContentsMargins(20, 0, 20, 0)
        h.setSpacing(14)

        # app icon (top-left)
        icon_lbl = QLabel()
        _hdr_icon = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'icon.png')
        header_set = False
        if os.path.exists(_hdr_icon):
            try:
                _px = load_cropped_pixmap(Path(_hdr_icon).read_bytes(), 42)
                if _px:
                    icon_lbl.setPixmap(_px)
                    header_set = True
            except OSError:
                pass
        if not header_set:
            icon_lbl.setPixmap(
                get_icon('music_note', 36, self._theme['accent']).pixmap(36, 36)
            )

        col = QVBoxLayout()
        col.setSpacing(0)
        title = QLabel('SongSyncer')
        title.setObjectName('appTitle')
        sub = QLabel('COVER ART MANAGER')
        sub.setObjectName('appSubtitle')
        col.addWidget(title)
        col.addWidget(sub)

        h.addWidget(icon_lbl)
        h.addLayout(col)

        # ── sectioned stat form ──
        # Each section groups related counters. Clicking a row applies the
        # corresponding filter; the filter→row map lives in
        # `_stat_rows_by_filter` so `_apply_song_filter` can highlight the
        # active row.
        self._stat_rows_by_filter: dict[str, StatRow] = {}

        def _make_row(label: str, color: str, filter_mode: str) -> StatRow:
            row = StatRow(label, color, filter_mode, self._theme)
            row.clicked.connect(lambda fm=filter_mode: self._on_stat_row_clicked(fm))
            self._stat_rows_by_filter[filter_mode] = row
            return row

        sec_music = StatSection('Music Files', self._theme)
        sec_music.add_row(_make_row('Total', self._theme.get('accent', '#7c3aed'), 'all'))

        sec_art = StatSection('Artwork', self._theme)
        sec_art.add_row(_make_row('No Cover', self._theme.get('red', '#dc2626'), 'no_cover'))

        sec_meta = StatSection('Metadata', self._theme)
        sec_meta.add_row(_make_row('No metadata', self._theme.get('orange', '#b45309'), 'no_meta'))

        sec_typo = StatSection('File Issues', self._theme)
        sec_typo.add_row(_make_row('Typo errors', self._theme.get('warning_fg', '#f59e0b'), 'typo_errors'))

        sec_json = StatSection('JSON', self._theme)
        sec_json.add_row(_make_row('Missing entries', self._theme.get('accent', '#7c3aed'), 'missing_file'))
        sec_json.add_row(_make_row('No tags',         self._theme.get('blue', '#2563eb'), 'no_tags'))
        sec_json.add_row(_make_row('JSON errors',     self._theme.get('red', '#dc2626'), 'mismatch'))

        sec_lyr = StatSection('Lyrics', self._theme)
        sec_lyr.add_row(_make_row('No Lyrics', self._theme.get('cyan', '#0891b2'), 'no_lyrics'))
        sec_lyr.add_row(_make_row('Orphan files', self._theme.get('red', '#dc2626'), 'lyrics_orphan'))

        # Cache sections so we can show/hide on theme refresh if needed.
        self._stat_sections = [sec_music, sec_art, sec_meta, sec_typo, sec_json, sec_lyr]
        for sec in self._stat_sections:
            h.addWidget(sec)

        self._btn_sequenze = AutoFitButton('Edit Song')
        self._btn_sequenze.setObjectName('btnSequenze')
        self._btn_sequenze.setFixedHeight(44)
        self._btn_sequenze.setMinimumWidth(110)
        self._btn_sequenze.setToolTip(
            'Open the focused single-song editor on the currently selected song. '
            'Use Previous/Next to step through every song in the current filter.'
        )
        self._btn_sequenze.clicked.connect(self._on_sequenze)
        h.addWidget(self._btn_sequenze)

        h.addStretch()

        self._folder_lbl = QLabel('No folder selected')
        self._folder_lbl.setObjectName('folderPath')
        self._folder_lbl.setMaximumWidth(400)

        # Settings (gear) — opens the Settings dialog where all three paths
        # (Music / JSON / Lyrics) are configured. Replaces the old separate
        # folder picker and rescan buttons.
        self._btn_settings = QPushButton()
        self._btn_settings.setObjectName('btnThemeToggle')   # reuse stylesheet
        self._btn_settings.setIcon(get_icon('gear', 22, self._icon_color()))
        self._btn_settings.setIconSize(QSize(22, 22))
        self._btn_settings.setToolTip('Open Settings (Music / JSON / Lyrics paths)')
        self._btn_settings.setFixedSize(52, 48)
        self._btn_settings.setCursor(Qt.PointingHandCursor)
        self._btn_settings.clicked.connect(self._on_open_settings)

        # Theme toggle stays next to Settings.
        self._btn_theme = QPushButton()
        self._btn_theme.setObjectName('btnThemeToggle')
        self._btn_theme.setToolTip('Switch theme')
        self._btn_theme.setFixedSize(52, 48)
        self._btn_theme.setCursor(Qt.PointingHandCursor)
        self._btn_theme.clicked.connect(self._on_toggle_theme)
        self._update_theme_icon()

        h.addWidget(self._folder_lbl)
        h.addWidget(self._btn_settings)
        h.addWidget(self._btn_theme)

        # Internal rescan-enabled state, formerly tied to `_btn_rescan`,
        # is now consulted by `_on_open_settings` only when needed.
        self._can_rescan = False

        return w

    def _build_action_bar(self) -> QWidget:
        """Three groups cleanly separated: Image Cover | Metadata | Tags/Playlist."""
        w = QWidget()
        w.setObjectName('actionBar')

        outer = QHBoxLayout(w)
        outer.setContentsMargins(24, 16, 24, 16)
        outer.setSpacing(16)

        # ── Group 1: Image Cover ─────────────────────────────────────────────
        self._grp_covers = grp1 = QFrame()
        grp1.setObjectName('actionGroup')
        g1 = QVBoxLayout(grp1)
        g1.setContentsMargins(20, 14, 20, 16)
        g1.setSpacing(6)

        grp1_label = QLabel('IMAGE COVER')
        grp1_label.setObjectName('groupLabel')
        g1.addWidget(grp1_label)

        row1 = QHBoxLayout()
        row1.setSpacing(6)

        self._btn_auto = AutoFitButton('Auto Assign')
        self._btn_auto.setObjectName('btnAutoAssign')
        self._btn_auto.setIcon(get_icon('bolt', 28, '#ffffff'))
        self._btn_auto.setIconSize(QSize(28, 28))
        self._btn_auto.setToolTip('Auto-assign cover art')
        auto_menu = QMenu(self)
        auto_menu.addAction('Auto Assign Selected (missing)', self._on_auto_assign_selected)
        auto_menu.addAction('Auto Assign All Songs (missing)', self._on_auto_assign_all)
        self._btn_auto.setMenu(auto_menu)

        self._btn_specific = AutoFitButton('Assign Specific')
        self._btn_specific.setObjectName('btnAssignSpecific')
        self._btn_specific.setIcon(get_icon('search', 28, '#ffffff'))
        self._btn_specific.setIconSize(QSize(28, 28))
        self._btn_specific.setToolTip('Search and pick from 6 candidates for the selected song.')
        self._btn_specific.clicked.connect(self._on_assign_specific)

        self._btn_delete = AutoFitButton('Delete')
        self._btn_delete.setObjectName('btnDeleteCover')
        self._btn_delete.setIcon(get_icon('trash', 28, '#ffffff'))
        self._btn_delete.setIconSize(QSize(28, 28))
        self._btn_delete.setToolTip('Remove embedded cover art from selected song(s).')
        self._btn_delete.clicked.connect(self._on_delete_cover)

        row1.addWidget(self._btn_auto)
        row1.addWidget(self._btn_specific)
        row1.addWidget(self._btn_delete)
        g1.addLayout(row1)

        outer.addWidget(grp1, 1)

        # ── Group 2: Metadata ────────────────────────────────────────────────
        self._grp_meta = grp2 = QFrame()
        grp2.setObjectName('actionGroup')
        g2 = QVBoxLayout(grp2)
        g2.setContentsMargins(20, 14, 20, 16)
        g2.setSpacing(6)

        grp2_lbl_row = QHBoxLayout()
        grp2_lbl_row.setSpacing(0)
        grp2_label = QLabel('METADATA')
        grp2_label.setObjectName('groupLabel')
        grp2_lbl_row.addWidget(grp2_label)
        grp2_lbl_row.addStretch()
        self._hint_lbl = QLabel('← Select a song')
        self._hint_lbl.setObjectName('actionHint')
        grp2_lbl_row.addWidget(self._hint_lbl)
        g2.addLayout(grp2_lbl_row)

        row2 = QHBoxLayout()
        row2.setSpacing(6)

        self._btn_fix_auto = AutoFitButton('Try to auto detect')
        self._btn_fix_auto.setObjectName('btnFixMeta')
        self._btn_fix_auto.setIcon(get_icon('wand', 28, '#ffffff'))
        self._btn_fix_auto.setIconSize(QSize(28, 28))
        self._btn_fix_auto.setToolTip(
            'Parse "Artist - Title (Album)" from each filename.\n'
            'Content in parentheses is set as the album tag.\n'
            'Operates on selected songs, or all songs if none selected.'
        )
        self._btn_fix_auto.clicked.connect(self._on_fix_auto)

        self._btn_fix_manual = AutoFitButton('Manual Fix')
        self._btn_fix_manual.setObjectName('btnFixMetaManual')
        self._btn_fix_manual.setIcon(get_icon('pencil', 28, '#ffffff'))
        self._btn_fix_manual.setIconSize(QSize(28, 28))
        self._btn_fix_manual.setToolTip(
            'Preview and edit all proposed metadata changes before applying.'
        )
        self._btn_fix_manual.clicked.connect(self._on_fix_manual)

        self._btn_clear_meta = AutoFitButton('Clear')
        self._btn_clear_meta.setObjectName('btnClearMeta')
        self._btn_clear_meta.setIcon(get_icon('x_mark', 24, self._icon_color()))
        self._btn_clear_meta.setIconSize(QSize(24, 24))
        self._btn_clear_meta.setToolTip(
            'Remove title, artist and album tags from selected song(s).\n'
            'Nothing selected: clears ALL songs.'
        )
        self._btn_clear_meta.clicked.connect(self._on_clear_meta)

        row2.addWidget(self._btn_fix_auto)
        row2.addWidget(self._btn_fix_manual)
        row2.addWidget(self._btn_clear_meta)
        g2.addLayout(row2)

        outer.addWidget(grp2, 1)

        # ── Group 3: Tags / Playlist ─────────────────────────────────────────
        self._grp_tags = grp3 = QFrame()
        grp3.setObjectName('actionGroup')
        g3 = QVBoxLayout(grp3)
        g3.setContentsMargins(20, 14, 20, 16)
        g3.setSpacing(6)

        grp3_lbl_row = QHBoxLayout()
        grp3_lbl_row.setSpacing(0)
        grp3_label = QLabel('TAGS / PLAYLIST')
        grp3_label.setObjectName('groupLabel')
        grp3_lbl_row.addWidget(grp3_label)
        grp3_lbl_row.addStretch()
        self._playlist_info_lbl = QLabel('')
        self._playlist_info_lbl.setObjectName('playlistInfo')
        grp3_lbl_row.addWidget(self._playlist_info_lbl)
        g3.addLayout(grp3_lbl_row)

        row3 = QHBoxLayout()
        row3.setSpacing(6)

        # JSON-picker moved into Settings dialog; this group keeps only the
        # tag-action buttons (Assign Tags / JSON Report / Fix Unsupported Tags
        # / Remove from JSON).

        self._btn_playlist_assign = AutoFitButton('Assign Tags')
        self._btn_playlist_assign.setObjectName('btnPlaylistAssign')
        self._btn_playlist_assign.setIcon(get_icon('tag', 28, '#ffffff'))
        self._btn_playlist_assign.setIconSize(QSize(28, 28))
        self._btn_playlist_assign.setToolTip(
            'Assign one or more tags to the selected songs.'
        )
        self._btn_playlist_assign.clicked.connect(self._on_assign_playlists)

        self._btn_playlist_report = AutoFitButton('JSON Report')
        self._btn_playlist_report.setObjectName('btnPlaylistReport')
        self._btn_playlist_report.setIcon(get_icon('list', 28, self._icon_color()))
        self._btn_playlist_report.setIconSize(QSize(28, 28))
        self._btn_playlist_report.setToolTip(
            'Show detailed mismatch and JSON diagnostics.'
        )
        self._btn_playlist_report.clicked.connect(self._on_show_playlist_report)

        self._btn_remove_missing = AutoFitButton('Remove from JSON')
        self._btn_remove_missing.setObjectName('btnRemoveMissing')
        self._btn_remove_missing.setIcon(get_icon('trash', 28, '#ffffff'))
        self._btn_remove_missing.setIconSize(QSize(28, 28))
        self._btn_remove_missing.setToolTip(
            'Delete the selected missing entries from the JSON file.\n'
            'Only available when Missing filter is active and ghost rows are selected.'
        )
        self._btn_remove_missing.clicked.connect(self._on_remove_missing_records)
        self._btn_remove_missing.setVisible(False)

        self._btn_fix_invalid_tags = AutoFitButton('Fix Unsupported Tags')
        self._btn_fix_invalid_tags.setObjectName('btnFixInvalidTags')
        self._btn_fix_invalid_tags.setIcon(get_icon('wand', 28, '#ffffff'))
        self._btn_fix_invalid_tags.setIconSize(QSize(28, 28))
        self._btn_fix_invalid_tags.setToolTip(
            'Remap each unsupported tag found in the JSON to a valid '
            'identifier (the stem of a PNG in Data/Tags/), or remove it.'
        )
        self._btn_fix_invalid_tags.clicked.connect(self._on_fix_unsupported_tags)
        self._btn_fix_invalid_tags.setEnabled(False)

        row3.addWidget(self._btn_playlist_assign)
        row3.addWidget(self._btn_playlist_report)
        row3.addWidget(self._btn_fix_invalid_tags)
        row3.addWidget(self._btn_remove_missing)
        g3.addLayout(row3)

        outer.addWidget(grp3, 1)

        # ── Group 4: Lyrics ──────────────────────────────────────────────────
        self._grp_lyrics = grp_lyrics = QFrame()
        grp_lyrics.setObjectName('actionGroup')
        g4 = QVBoxLayout(grp_lyrics)
        g4.setContentsMargins(20, 14, 20, 16)
        g4.setSpacing(6)

        grp4_lbl_row = QHBoxLayout()
        grp4_lbl_row.setSpacing(0)
        grp4_label = QLabel('LYRICS')
        grp4_label.setObjectName('groupLabel')
        grp4_lbl_row.addWidget(grp4_label)
        grp4_lbl_row.addStretch()
        g4.addLayout(grp4_lbl_row)

        row4 = QHBoxLayout()
        row4.setSpacing(6)

        # Lyrics folder picker lives in Settings dialog now; this group keeps
        # only the lyrics-action buttons (Fetch Online / Import .lrc / Remove).

        self._btn_lyrics_fetch = AutoFitButton('Fetch Online')
        self._btn_lyrics_fetch.setObjectName('btnLyricsFetch')
        self._btn_lyrics_fetch.setIcon(get_icon('search', 28, '#ffffff'))
        self._btn_lyrics_fetch.setIconSize(QSize(28, 28))
        self._btn_lyrics_fetch.setToolTip(
            'Download lyrics from LRCLIB for selected songs (or all songs '
            'without lyrics if none selected).'
        )
        self._btn_lyrics_fetch.clicked.connect(self._on_fetch_lyrics)

        self._btn_lyrics_import = AutoFitButton('Import .lrc')
        self._btn_lyrics_import.setObjectName('btnLyricsImport')
        self._btn_lyrics_import.setIcon(get_icon('document', 28, '#ffffff'))
        self._btn_lyrics_import.setIconSize(QSize(28, 28))
        self._btn_lyrics_import.setToolTip(
            'Pick an .lrc file from disk and copy it into the lyrics folder '
            'under the canonical name for the selected song.'
        )
        self._btn_lyrics_import.clicked.connect(self._on_import_lyrics)

        self._btn_lyrics_remove = AutoFitButton('Remove')
        self._btn_lyrics_remove.setObjectName('btnLyricsRemove')
        self._btn_lyrics_remove.setIcon(get_icon('trash', 28, '#ffffff'))
        self._btn_lyrics_remove.setIconSize(QSize(28, 28))
        self._btn_lyrics_remove.setToolTip(
            'Delete the .lrc file(s) for the selected songs.'
        )
        self._btn_lyrics_remove.clicked.connect(self._on_remove_lyrics)

        row4.addWidget(self._btn_lyrics_fetch)
        row4.addWidget(self._btn_lyrics_import)
        row4.addWidget(self._btn_lyrics_remove)
        g4.addLayout(row4)

        outer.addWidget(grp_lyrics, 1)

        for btn in (
            self._btn_auto, self._btn_specific, self._btn_delete,
            self._btn_fix_auto, self._btn_fix_manual, self._btn_clear_meta,
            self._btn_playlist_assign, self._btn_playlist_report,
            self._btn_fix_invalid_tags, self._btn_remove_missing,
            self._btn_lyrics_fetch,
            self._btn_lyrics_import, self._btn_lyrics_remove,
        ):
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            btn.setMinimumHeight(52)

        return w

    def _build_song_view(self) -> QWidget:
        """Container: thin toolbar (view toggle) + stacked table/grid."""
        container = QWidget()
        v = QVBoxLayout(container)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        v.addWidget(self._build_table_bar())

        self._stack = QStackedWidget()
        self._table = self._build_table()
        self._grid  = self._build_grid()
        self._stack.addWidget(self._table)   # index 0 = list
        self._stack.addWidget(self._grid)    # index 1 = grid

        v.addWidget(self._stack, 1)

        self._thumb_scroll_timer = QTimer(self)
        self._thumb_scroll_timer.setSingleShot(True)
        self._thumb_scroll_timer.setInterval(50)
        self._thumb_scroll_timer.timeout.connect(self._refresh_visible_thumbs)
        self._table.verticalScrollBar().valueChanged.connect(self._schedule_thumb_refresh)
        self._grid.verticalScrollBar().valueChanged.connect(self._schedule_thumb_refresh)
        self._table.viewport().installEventFilter(self)
        self._grid.viewport().installEventFilter(self)

        return container

    def _build_table_bar(self) -> QWidget:
        """Thin bar above the song list: hint + List/Grid toggle."""
        w = QWidget()
        w.setObjectName('tableBar')
        w.setFixedHeight(60)

        h = QHBoxLayout(w)
        h.setContentsMargins(20, 0, 14, 0)
        h.setSpacing(8)

        self._search_bar = QLineEdit()
        self._search_bar.setObjectName('searchBar')
        self._search_bar.setPlaceholderText('Search songs…')
        self._search_bar.setFixedHeight(36)
        self._search_bar.setMinimumWidth(180)
        self._search_bar.setMaximumWidth(320)
        self._search_bar.setClearButtonEnabled(True)
        self._search_bar.textChanged.connect(self._on_search_changed)
        h.addWidget(self._search_bar)

        h.addStretch(1)

        self._filter_title_lbl = QLabel('All Songs')
        self._filter_title_lbl.setObjectName('filterTitle')
        self._filter_title_lbl.setAlignment(Qt.AlignCenter)
        h.addWidget(self._filter_title_lbl)

        h.addStretch(1)

        view_lbl = QLabel('View:')
        view_lbl.setStyleSheet('color:#4a5568; font-size:12px;')
        h.addWidget(view_lbl)

        self._btn_view_list = QPushButton('List')
        self._btn_view_list.setObjectName('btnViewActive')
        self._btn_view_list.setIcon(get_icon('list', 24, '#ffffff'))
        self._btn_view_list.setIconSize(QSize(24, 24))
        self._btn_view_list.setFixedSize(140, 42)
        self._btn_view_list.clicked.connect(lambda: self._on_set_view('list'))

        self._btn_view_grid = QPushButton('Grid')
        self._btn_view_grid.setObjectName('btnViewInactive')
        self._btn_view_grid.setIcon(get_icon('grid', 24, self._icon_color()))
        self._btn_view_grid.setIconSize(QSize(24, 24))
        self._btn_view_grid.setFixedSize(140, 42)
        self._btn_view_grid.clicked.connect(lambda: self._on_set_view('grid'))

        h.addWidget(self._btn_view_list)
        h.addWidget(self._btn_view_grid)

        return w

    def _build_table(self) -> SongTable:
        t = SongTable(0, 10)
        t.setHorizontalHeaderLabels(
            ['Cover', 'File', 'Title', 'Artist', 'Album', 'Lrc', 'Tags', 'Issue', 'Fmt', 'Size']
        )
        t.setObjectName('songTable')
        t.setAlternatingRowColors(True)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setSelectionMode(QAbstractItemView.ExtendedSelection)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.verticalHeader().setVisible(False)
        t.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        t.setShowGrid(False)
        t.setIconSize(QSize(THUMB_SIZE, THUMB_SIZE))
        t.setSortingEnabled(True)

        hdr = t.horizontalHeader()
        hdr.setSectionsMovable(True)
        hdr.setSectionResizeMode(COL_COVER,    QHeaderView.Fixed)
        hdr.setSectionResizeMode(COL_FILENAME, QHeaderView.Stretch)
        hdr.setSectionResizeMode(COL_TITLE,    QHeaderView.Interactive)
        hdr.setSectionResizeMode(COL_ARTIST,   QHeaderView.Interactive)
        hdr.setSectionResizeMode(COL_ALBUM,    QHeaderView.Interactive)
        hdr.setSectionResizeMode(COL_LYRICS,   QHeaderView.Fixed)
        hdr.setSectionResizeMode(COL_TAGS,     QHeaderView.Interactive)
        hdr.setSectionResizeMode(COL_ISSUES,   QHeaderView.Fixed)
        hdr.setSectionResizeMode(COL_FMT,      QHeaderView.Interactive)
        hdr.setSectionResizeMode(COL_SIZE,     QHeaderView.Interactive)

        t.setColumnWidth(COL_COVER,  THUMB_SIZE + 14)
        t.setColumnWidth(COL_TITLE,  160)
        t.setColumnWidth(COL_ARTIST, 155)
        t.setColumnWidth(COL_ALBUM,  140)
        t.setColumnWidth(COL_LYRICS, 44)
        t.setColumnWidth(COL_TAGS,   190)
        t.setColumnWidth(COL_ISSUES, 58)
        t.setColumnWidth(COL_FMT,    60)
        t.setColumnWidth(COL_SIZE,   78)

        t.itemSelectionChanged.connect(self._on_selection_changed)
        t.cellDoubleClicked.connect(self._on_table_double_click)

        return t

    def _build_grid(self) -> QListWidget:
        g = QListWidget()
        g.setObjectName('songGrid')
        g.setViewMode(QListWidget.IconMode)
        g.setIconSize(QSize(GRID_ICON, GRID_ICON))
        g.setGridSize(QSize(GRID_ITEM_W, GRID_ITEM_H))
        g.setResizeMode(QListWidget.Adjust)
        g.setMovement(QListWidget.Static)
        g.setSelectionMode(QAbstractItemView.ExtendedSelection)
        g.setWordWrap(True)
        g.setTextElideMode(Qt.ElideRight)
        g.itemSelectionChanged.connect(self._on_selection_changed)
        g.itemDoubleClicked.connect(self._on_grid_double_click)
        return g

    def _build_statusbar(self):
        sb = QStatusBar()
        self.setStatusBar(sb)

        self._status_lbl = QLabel('Ready – select a music folder to begin.')
        sb.addWidget(self._status_lbl, 1)

        self._sb_pbar = QProgressBar()
        self._sb_pbar.setFixedSize(260, 10)
        self._sb_pbar.setRange(0, 0)
        self._sb_pbar.hide()
        sb.addPermanentWidget(self._sb_pbar)

    # ── debug helper ──────────────────────────────────────────────────────────

    def _dbg(self, msg: str):
        """Show a status message in the status bar."""
        self._status_lbl.setText(msg)
        stamp = datetime.now().strftime('%H:%M:%S')
        print(f'[{stamp}] [SongSyncer] {msg}', file=sys.stderr, flush=True)

    def _dbg_step(self, label: str, t0: float) -> float:
        """Show 'label  Xms' in the status bar and return the current time."""
        elapsed_ms = (_time.perf_counter() - t0) * 1000
        self._dbg(f"{label}  ({elapsed_ms:.0f} ms)")
        return _time.perf_counter()

    # =========================================================================
    # Theme
    # =========================================================================

    def _on_toggle_theme(self):
        cur = self._theme['name'].lower()
        try:
            idx = THEME_ORDER.index(cur)
        except ValueError:
            idx = 0
        new_key = THEME_ORDER[(idx + 1) % len(THEME_ORDER)]
        self._theme = THEMES[new_key]
        self._settings.setValue('theme', new_key)
        QApplication.instance().setStyleSheet(build_stylesheet(self._theme))
        self._update_theme_icon()
        self._refresh_button_icons()
        self._player_bar.update_icon_colors(
            self._theme['text'], self._theme['accent']
        )
        # Re-theme the new widgets that aren't reached by the global QSS.
        for sec in getattr(self, '_stat_sections', ()):
            sec.apply_theme(self._theme)
        if hasattr(self, '_settings_panel'):
            self._settings_panel.apply_theme(self._theme)

    def _update_theme_icon(self):
        self._btn_theme.setIcon(get_icon('palette', 28, self._icon_color()))
        self._btn_theme.setIconSize(QSize(28, 28))
        self._btn_theme.setToolTip(f'Theme: {self._theme["name"]}')

    def _refresh_button_icons(self):
        """Re-tint all button icons after a theme change."""
        ic = self._icon_color()
        # header
        self._btn_settings.setIcon(get_icon('gear', 22, ic))
        # action bar – colored-bg buttons keep white icons
        self._btn_auto.setIcon(get_icon('bolt', 28, '#ffffff'))
        self._btn_specific.setIcon(get_icon('search', 28, '#ffffff'))
        self._btn_delete.setIcon(get_icon('trash', 28, '#ffffff'))
        self._btn_fix_auto.setIcon(get_icon('wand', 28, '#ffffff'))
        self._btn_fix_manual.setIcon(get_icon('pencil', 28, '#ffffff'))
        self._btn_clear_meta.setIcon(get_icon('x_mark', 24, ic))
        self._btn_playlist_assign.setIcon(get_icon('tag', 28, '#ffffff'))
        self._btn_playlist_report.setIcon(get_icon('list', 28, ic))
        # view toggle
        is_list = self._stack.currentIndex() == 0
        self._btn_view_list.setIcon(
            get_icon('list', 24, '#ffffff' if is_list else ic)
        )
        self._btn_view_grid.setIcon(
            get_icon('grid', 24, '#ffffff' if not is_list else ic)
        )

    # =========================================================================
    # Playlist JSON helpers
    # =========================================================================

    def _invalidate_playlist_reconcile(self):
        self._playlist_reconcile_seq += 1
        self._playlist_reconcile_pending = False
        self._playlist_reconcile_rerun_needed = False
        self._update_playlist_action_state()

    def _load_playlist_store(self, update_status: bool = True, json_path: str | None = None) -> bool:
        target_path = json_path if json_path is not None else self._playlist_json_path
        prev_path = self._playlist_json_path
        prev_store = self._playlist_store
        self._invalidate_playlist_reconcile()
        self._dbg(f'Preparing tag JSON store: {target_path}')

        candidate = PlaylistAssignmentStore(target_path, self._folder)
        try:
            candidate.load()
        except Exception as exc:
            QMessageBox.warning(
                self,
                'Tag JSON Error',
                f'Failed to load tag JSON:\n{target_path}\n\n{exc}',
            )
            self._playlist_store = prev_store
            self._playlist_json_path = prev_path
            self._update_playlist_labels()
            return False

        self._dbg(f'Tag JSON loaded into memory: {len(candidate.records)} record(s)')
        self._playlist_store = candidate
        self._playlist_json_path = target_path
        if self._playlist_json_path:
            self._settings.setValue('playlist_json_path', self._playlist_json_path)
        self._request_playlist_reconcile()
        return True

    def _reconcile_playlist_store(self):
        self._request_playlist_reconcile()

    def _request_playlist_reconcile(self):
        self._playlist_reconcile_seq += 1
        seq = self._playlist_reconcile_seq

        if not self._songs and not self._playlist_store.records:
            self._playlist_store.reconcile({}, self._folder, self._tag_icons.keys())
            self._rebuild_playlist_issue_map()
            self._playlist_reconcile_pending = False
            self._playlist_reconcile_applied_seq = seq
            self._playlist_reconcile_rerun_needed = False
            self._update_playlist_labels()
            self._update_stats()
            self._update_playlist_action_state()
            return

        if self._reconcile_worker and self._reconcile_worker.isRunning():
            self._playlist_reconcile_pending = True
            self._playlist_reconcile_rerun_needed = True
            self._update_playlist_action_state()
            self._status_lbl.setText('Tag JSON matching queued…')
            return

        self._start_playlist_reconcile_worker(seq)

    def _start_playlist_reconcile_worker(self, seq: int):
        self._playlist_reconcile_pending = True
        self._playlist_reconcile_rerun_needed = False
        self._update_playlist_action_state()
        real_songs = {p: s for p, s in self._songs.items() if p not in self._ghost_paths}
        self._dbg(f'Matching {len(real_songs)} songs to JSON records…')
        previous_paths = set(self._playlist_store._path_to_record.keys())

        worker = PlaylistReconcileWorker(
            seq,
            self._playlist_store.records,
            real_songs,
            self._folder,
            list(self._tag_icons.keys()),
        )
        worker.result_ready.connect(
            lambda done_seq, result, old_paths=previous_paths: self._on_playlist_reconcile_complete(
                done_seq,
                result,
                old_paths,
            )
        )
        worker.error.connect(self._on_playlist_reconcile_error)
        worker.finished.connect(self._on_playlist_reconcile_worker_finished)
        worker.start()
        self._reconcile_worker = worker

    def _on_playlist_reconcile_complete(
        self,
        seq: int,
        result: dict,
        previous_paths: set[str],
    ):
        if seq != self._playlist_reconcile_seq:
            return

        t0 = _time.perf_counter()
        previous_issue_paths = set(self._playlist_issues_by_path.keys())
        self._playlist_store.apply_reconcile_result(result, self._songs, self._folder)
        self._rebuild_playlist_issue_map()
        self._sync_ghost_rows()
        self._playlist_reconcile_applied_seq = seq

        affected_paths = previous_paths | set(self._playlist_store._path_to_record.keys())
        affected_paths |= previous_issue_paths | set(self._playlist_issues_by_path.keys())
        self._refresh_tag_previews(affected_paths)
        self._refresh_issue_indicators(affected_paths)
        self._update_playlist_labels()
        self._update_stats()

        matched = len(self._playlist_store._path_to_record)
        total_records = len(self._playlist_store.records)
        songs_with_tags = sum(
            1 for path in self._songs if self._playlist_store.get_tags(path)
        )
        elapsed_ms = (_time.perf_counter() - t0) * 1000

        if total_records == 0:
            final_msg = 'Tag JSON loaded — no records found in file'
        elif matched == 0:
            final_msg = (
                f'Tag JSON loaded — 0/{total_records} records matched '
                f'(filenames may not match your library)'
            )
        else:
            final_msg = (
                f'Tag JSON loaded — {matched}/{total_records} records matched, '
                f'{songs_with_tags} song(s) tagged  ({elapsed_ms:.0f} ms)'
            )
        self._dbg(final_msg)

        if self._songs:
            self._status_lbl.setText(self._compose_library_status())

    def _on_playlist_reconcile_error(self, seq: int, message: str):
        if seq != self._playlist_reconcile_seq:
            return
        self._playlist_reconcile_applied_seq = seq
        QMessageBox.warning(
            self,
            'Tag JSON Error',
            f'Failed to match tag JSON:\n{self._playlist_json_path}\n\n{message}',
        )

    def _on_playlist_reconcile_worker_finished(self):
        if self._reconcile_worker:
            self._reconcile_worker.deleteLater()
            self._reconcile_worker = None

        if self._playlist_reconcile_rerun_needed:
            self._start_playlist_reconcile_worker(self._playlist_reconcile_seq)
            return

        self._playlist_reconcile_pending = False
        self._update_playlist_action_state()

    def _update_playlist_labels(self):
        # Path label moved into Settings dialog; just keep the in-group
        # summary line current.
        self._playlist_info_lbl.setText(self._playlist_store.summary())
        # Push the latest path into Settings (if the dialog is open).
        self._sync_settings_paths()

    def _save_playlist_store(self, reconcile: bool = True) -> bool:
        """Persist the in-memory store. By default also re-runs the full
        reconcile (needed when the user picks a new JSON or applies a bulk
        change). Pass ``reconcile=False`` for per-song edits where the
        in-memory state is already up to date."""
        try:
            self._playlist_store.save()
            if reconcile:
                self._request_playlist_reconcile()
            return True
        except Exception as exc:
            QMessageBox.warning(
                self,
                'Tag JSON Error',
                f'Failed to save tag JSON:\n{self._playlist_json_path}\n\n{exc}',
            )
            return False

    def _prompt_create_missing_records(self, paths: list[str], operation: str) -> int:
        missing = [
            p for p in paths
            if p in self._songs and not self._playlist_store.has_record(p)
        ]
        if not missing:
            return 0

        names = '\n'.join(
            f'  • {(self._songs[p].get("title") or self._songs[p].get("filename") or p)}'
            for p in missing[:12]
        )
        if len(missing) > 12:
            names += f'\n  … and {len(missing) - 12} more'

        reply = QMessageBox.question(
            self,
            'Create Missing JSON Entries',
            f'{len(missing)} song(s) do not exist in the selected JSON.\n'
            f'Create entries before {operation}?\n\n{names}',
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return 0

        return self._playlist_store.create_records_for_paths(missing, self._songs)

    def _file_dialog_options(self):
        options = QFileDialog.Options()
        if os.name == 'posix':
            options |= QFileDialog.DontUseNativeDialog
        return options

    # =========================================================================
    # Slots – Settings panel (inline page on the main window's stack)
    # =========================================================================

    def _on_open_settings(self):
        """Switch the central stack to the Settings page."""
        # Bring the panel up to date before showing it.
        self._sync_settings_paths()
        self._main_stack.setCurrentIndex(1)

    def _on_settings_close(self):
        """Close button: return to the library and run any deferred sync."""
        self._main_stack.setCurrentIndex(0)
        self._run_pending_sync()

    def _sync_settings_paths(self):
        """Push the latest paths into the Settings panel."""
        self._settings_panel.set_paths(
            self._folder or '',
            self._playlist_json_path or '',
            self._lyrics_store.get_folder() or '',
        )

    def _on_settings_music_chosen(self, folder: str):
        # Picked while Settings is open: remember it, defer the scan until
        # the user closes Settings.
        self._folder = folder
        self._settings.setValue('last_folder', folder)
        self._pending_music_scan = folder
        self._sync_settings_paths()

    def _on_settings_json_chosen(self, path: str):
        # Defer the load until close so we don't churn while picking.
        self._playlist_json_path = path
        self._settings.setValue('playlist_json_path', path)
        self._pending_json_load = path
        self._sync_settings_paths()

    def _on_settings_lyrics_chosen(self, folder: str):
        self._lyrics_store.set_folder(folder)
        self._settings.setValue('lyrics_folder', folder)
        self._pending_lyrics_scan = True
        self._sync_settings_paths()

    def _on_settings_music_refresh(self):
        if self._folder:
            self._pending_music_scan = self._folder
            self._status_lbl.setText(f'Music re-scan queued — closes Settings to run.')
        else:
            QMessageBox.information(
                self, 'Refresh',
                'Pick a music folder first before refreshing.'
            )

    def _on_settings_json_refresh(self):
        if self._playlist_json_path:
            self._pending_json_load = self._playlist_json_path
            self._status_lbl.setText('JSON reload queued — closes Settings to run.')
        else:
            QMessageBox.information(
                self, 'Refresh',
                'Pick a tag JSON first before refreshing.'
            )

    def _on_settings_lyrics_refresh(self):
        if not self._lyrics_store.get_folder():
            QMessageBox.information(
                self, 'Refresh',
                'Pick a lyrics folder first before refreshing.'
            )
            return
        self._pending_lyrics_scan = True
        self._status_lbl.setText('Lyrics re-scan queued — closes Settings to run.')

    def _on_manage_icons(self):
        tags_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'Data', 'Tags')
        dlg = IconManagerDialog(tags_dir, self._theme, self)
        dlg.icons_changed.connect(self._reload_tag_icons)
        dlg.exec_()

    def _reload_tag_icons(self):
        """Rescan Data/Tags/ and rebuild the tag-icon registry, then refresh all
        visible tag preview widgets to reflect added/renamed/removed icons."""
        import tags_list as _tl
        new_icons, new_names, new_fuzzy = _tl._load_tags()
        # Rebuild all module-level dicts in-place so every import sees the update.
        _tl.TAG_ICONS.clear()
        _tl.TAG_ICONS.update(new_icons)
        _tl.TAG_DISPLAY_NAMES.clear()
        _tl.TAG_DISPLAY_NAMES.update(new_names)
        _tl._TAG_FUZZY_LOOKUP.clear()
        _tl._TAG_FUZZY_LOOKUP.update(new_fuzzy)
        # Rebuild the window's own filtered copy.
        self._tag_icons = {
            name: path for name, path in new_icons.items() if os.path.exists(path)
        }
        # Refresh all tag-preview cells in the table.
        for path in list(self._path_to_tag_label.keys()):
            self._refresh_song_tag_preview(path)
        self._status_lbl.setText('Tag icons reloaded.')

    def _run_pending_sync(self):
        """After the user closes Settings (or at boot), kick off whatever they queued.
        Load order: JSON first (sync/fast) → music scan → lyrics (inside scan_complete)."""
        # Always load JSON synchronously first so it's in memory when the scan
        # completes and reconciliation runs.
        if self._pending_json_load:
            path = self._pending_json_load
            self._pending_json_load = None
            self._load_playlist_store(json_path=path, update_status=False)

        if self._pending_music_scan:
            folder = self._pending_music_scan
            self._pending_music_scan = None
            self._pending_lyrics_scan = False  # _on_scan_complete handles lyrics
            # _start_scan triggers reconcile + lyrics scan downstream.
            self._start_scan(folder)
            return

        # No music scan queued — handle lyrics independently.
        if self._pending_lyrics_scan:
            self._pending_lyrics_scan = False
            self._lyrics_store.scan(self._songs)
            self._update_stats()

    # =========================================================================
    # Slots – folder
    # =========================================================================

    def _on_select_folder(self):
        self._dbg('Opening music folder dialog…')
        folder = QFileDialog.getExistingDirectory(
            self,
            'Select Music Folder',
            self._folder or os.path.expanduser('~'),
            options=self._file_dialog_options(),
        )
        self._dbg(f'Music folder dialog closed. Selected: {bool(folder)}')
        if folder:
            self._folder = folder
            self._start_scan(folder)

    def _on_rescan(self):
        if self._folder:
            self._start_scan(self._folder)

    def _on_select_playlist_json(self):
        start_path = self._playlist_json_path or default_playlist_json_path()
        self._dbg(f'Opening tag JSON dialog from: {start_path}')
        json_path, _ = QFileDialog.getOpenFileName(
            self,
            'Select Tag JSON',
            start_path,
            'JSON Files (*.json)',
            options=self._file_dialog_options(),
        )
        self._dbg(f'Tag JSON dialog closed. Selected: {bool(json_path)}')
        if not json_path:
            return
        self._dbg(f'Loading tag JSON: {json_path}')
        self._load_playlist_store(json_path=json_path)

    def _on_remove_missing_records(self):
        """Delete selected ghost-row JSON entries from the JSON file."""
        selected_ghosts = [p for p in self._selected_paths() if p in self._ghost_paths]
        if not selected_ghosts:
            return

        names = '\n'.join(
            self._songs[p].get('filename', p) for p in selected_ghosts[:10]
        )
        if len(selected_ghosts) > 10:
            names += f'\n… and {len(selected_ghosts) - 10} more'

        reply = QMessageBox.question(
            self,
            'Remove from JSON',
            f'Permanently delete {len(selected_ghosts)} entr'
            f'{"y" if len(selected_ghosts) == 1 else "ies"} from the JSON file?\n\n{names}',
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        indexes = []
        for ghost_path in selected_ghosts:
            record = self._playlist_store._path_to_record.get(ghost_path)
            if record is None:
                continue
            try:
                idx = self._playlist_store.records.index(record)
                indexes.append(idx)
            except ValueError:
                pass

        removed = self._playlist_store.remove_records_by_indexes(indexes)
        if not removed:
            return

        if self._save_playlist_store():
            self._reconcile_playlist_store()
            self._status_lbl.setText(
                f'Removed {removed} entr{"y" if removed == 1 else "ies"} from JSON.'
            )

    def _on_show_playlist_report(self):
        if self._playlist_reconcile_pending:
            QMessageBox.information(
                self,
                'JSON Report',
                'Tag JSON matching is still in progress. Wait for it to finish, then open the report.',
            )
            return
        dlg = PlaylistJsonReportDialog(
            json_path=self._playlist_json_path,
            summary=self._playlist_store.summary(),
            mismatch_records=self._playlist_store.mismatch_records,
            invalid_tag_records=self._playlist_store.invalid_tag_records,
            image_decode_failures=get_decode_failures(),
            parent=self,
        )
        dlg.exec_()

    def _on_fix_unsupported_tags(self):
        if self._playlist_reconcile_pending:
            QMessageBox.information(
                self, 'Fix Unsupported Tags',
                'Tag JSON matching is still in progress. Wait for it to finish, then try again.',
            )
            return
        invalid: set[str] = set()
        for entry in self._playlist_store.invalid_tag_records:
            for tag in entry.get('tags', []) or []:
                if tag and tag not in TAG_ICONS:
                    invalid.add(tag)
        if not invalid:
            QMessageBox.information(
                self, 'Fix Unsupported Tags',
                'No unsupported tags found in the JSON.',
            )
            return
        dlg = UnsupportedTagConverterDialog(
            invalid_tags=sorted(invalid),
            valid_identifiers=list(TAG_ICONS),
            theme=self._theme,
            parent=self,
        )
        if dlg.exec_() != UnsupportedTagConverterDialog.Accepted:
            return
        mapping = dlg.get_mapping()
        if not mapping:
            return

        renamed = 0
        removed = 0
        for old, new in mapping.items():
            if new is None:
                if self._playlist_store.rewrite_tag_in_records(old, None):
                    removed += 1
            elif new != old:
                if self._playlist_store.rewrite_tag_in_records(old, new):
                    renamed += 1

        if not renamed and not removed:
            self._status_lbl.setText('No tag changes applied.')
            return

        if self._save_playlist_store():
            self._request_playlist_reconcile()
            self._update_stats()
            parts = []
            if renamed:
                parts.append(f'remapped {renamed}')
            if removed:
                parts.append(f'removed {removed}')
            self._status_lbl.setText(
                'Tag conversion saved (' + ', '.join(parts) + ').'
            )

    # ── Lyrics handlers ──────────────────────────────────────────────────────

    def _on_select_lyrics_folder(self):
        start = self._lyrics_store.get_folder() or self._folder or ''
        folder = QFileDialog.getExistingDirectory(
            self, 'Select Lyrics Folder', start,
            options=self._file_dialog_options(),
        )
        if not folder:
            return
        self._apply_lyrics_folder(folder)

    def _apply_lyrics_folder(self, folder: str):
        """Single entry point used by both the (now removed) sidebar picker
        and the new Settings dialog."""
        self._lyrics_store.set_folder(folder)
        self._settings.setValue('lyrics_folder', folder)
        self._lyrics_store.scan(self._songs)
        self._update_stats()
        self._sync_settings_paths()
        self._status_lbl.setText(
            f'Lyrics folder set — {self._lyrics_store.count} song(s) have lyrics.'
        )

    def _on_fetch_lyrics(self):
        if not self._lyrics_store.get_folder():
            QMessageBox.information(
                self, 'Fetch Lyrics',
                'Pick a lyrics folder first ("Select Lyrics Folder").'
            )
            return
        if self._lyrics_fetch_worker and self._lyrics_fetch_worker.isRunning():
            QMessageBox.information(
                self, 'Fetch Lyrics',
                'A lyrics fetch is already running. Wait for it to finish.'
            )
            return
        selected = self._selected_paths()
        if selected:
            real_paths = [p for p in selected if p not in self._ghost_paths]
            targets = [self._songs[p] for p in real_paths if p in self._songs]
        else:
            targets = [
                self._songs[p] for p in self._songs
                if p not in self._ghost_paths and not self._lyrics_store.has_lyrics(p)
            ]
        if not targets:
            QMessageBox.information(
                self, 'Fetch Lyrics',
                'No songs to fetch — every selected song already has lyrics.'
            )
            return
        reply = QMessageBox.question(
            self, 'Fetch Lyrics',
            f'Fetch lyrics from LRCLIB for {len(targets)} song(s)?\n\n'
            f'This contacts lrclib.net once per song (~0.35s rate-limit).',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            return

        worker = LyricsFetchWorker(targets, self._lyrics_store.get_folder())
        worker.song_done.connect(self._on_lyrics_song_done)
        worker.progress.connect(self._on_lyrics_progress)
        worker.finished.connect(self._on_lyrics_fetch_finished)
        worker.start()
        self._lyrics_fetch_worker = worker
        self._sb_pbar.setRange(0, len(targets))
        self._sb_pbar.setValue(0)
        self._sb_pbar.show()
        self._status_lbl.setText(f'Fetching lyrics for {len(targets)} song(s)…')

    def _on_lyrics_progress(self, current: int, total: int, name: str):
        self._sb_pbar.setRange(0, total)
        self._sb_pbar.setValue(current)
        self._status_lbl.setText(f'Fetching lyrics ({current}/{total}) — {name}')

    def _on_lyrics_song_done(self, path: str, success: bool):
        if success and path in self._songs:
            self._lyrics_store._paths_with_lyrics.add(path)

    def _on_lyrics_fetch_finished(self, fetched: int, failed: int):
        self._sb_pbar.hide()
        self._lyrics_store.scan(self._songs)
        for path in self._songs:
            self._refresh_song_lyrics_cell(path)
        self._update_stats()
        self._status_lbl.setText(
            f'Lyrics fetch complete — {fetched} added, {failed} failed.'
        )
        if self._lyrics_fetch_worker:
            self._lyrics_fetch_worker.deleteLater()
            self._lyrics_fetch_worker = None

    def _on_import_lyrics(self):
        if not self._lyrics_store.get_folder():
            QMessageBox.information(
                self, 'Import .lrc',
                'Pick a lyrics folder first ("Select Lyrics Folder").'
            )
            return
        selected = [p for p in self._selected_paths() if p not in self._ghost_paths]
        if len(selected) != 1:
            QMessageBox.information(
                self, 'Import .lrc',
                'Select exactly one song before importing a .lrc file.'
            )
            return
        song_path = selected[0]
        src, _ = QFileDialog.getOpenFileName(
            self, 'Pick .lrc to Import', '',
            'Lyrics Files (*.lrc);;All Files (*)',
            options=self._file_dialog_options(),
        )
        if not src:
            return
        dest = self._lyrics_store.lrc_path_for(song_path)
        overwrite = False
        if os.path.isfile(dest):
            reply = QMessageBox.question(
                self, 'Import .lrc',
                f'Lyrics file already exists:\n{dest}\n\nOverwrite?',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
            overwrite = True
        try:
            self._lyrics_store.import_lrc(song_path, src, overwrite=overwrite)
        except Exception as exc:
            QMessageBox.warning(self, 'Import .lrc', f'Failed to import:\n{exc}')
            return
        self._refresh_song_lyrics_cell(song_path)
        self._update_stats()
        self._status_lbl.setText(
            f'Imported lyrics for {self._songs[song_path].get("filename", song_path)}.'
        )

    def _on_remove_lyrics(self):
        if not self._lyrics_store.get_folder():
            QMessageBox.information(
                self, 'Remove Lyrics',
                'Pick a lyrics folder first ("Select Lyrics Folder").'
            )
            return
        targets = [
            p for p in self._selected_paths()
            if p not in self._ghost_paths and self._lyrics_store.has_lyrics(p)
        ]
        if not targets:
            QMessageBox.information(
                self, 'Remove Lyrics',
                'No selected song has a lyrics file to remove.'
            )
            return
        names = '\n'.join(
            self._songs[p].get('filename', p) for p in targets[:10]
        )
        if len(targets) > 10:
            names += f'\n… and {len(targets) - 10} more'
        reply = QMessageBox.question(
            self, 'Remove Lyrics',
            f'Delete the .lrc file for {len(targets)} song(s)?\n\n{names}',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        removed = sum(1 for p in targets if self._lyrics_store.remove_lrc(p))
        for p in targets:
            self._refresh_song_lyrics_cell(p)
        self._update_stats()
        self._status_lbl.setText(f'Removed lyrics for {removed} song(s).')

    # ── Sequenze ─────────────────────────────────────────────────────────────

    _FILTER_DISPLAY_NAMES = {
        'all':          'All songs',
        'no_cover':     'No Cover',
        'no_meta':      'No metadata',
        'typo_errors':  'Typo errors',
        'no_tags':      'No tags',
        'mismatch':     'JSON errors',
        'missing_file': 'Missing entries',
        'no_lyrics':    'No Lyrics',
    }

    def _filter_display_name(self) -> str:
        return self._FILTER_DISPLAY_NAMES.get(self._active_song_filter, 'All songs')

    def _json_filename_for_path(self, path: str) -> str:
        record = self._playlist_store._path_to_record.get(path)
        if record:
            return record.get('filename', '') or ''
        return ''

    def _on_sequenze(self):
        # Collect rows currently visible under the active filter, in table order.
        visible_paths = []
        for row in range(self._table.rowCount()):
            if self._table.isRowHidden(row):
                continue
            item = self._table.item(row, COL_COVER)
            if not item:
                continue
            p = item.data(Qt.UserRole)
            if p and p not in self._ghost_paths:
                visible_paths.append(p)
        if not visible_paths:
            QMessageBox.information(
                self, 'Edit Song',
                'Nothing to step through under the current filter.'
            )
            return
        songs = [self._songs[p] for p in visible_paths if p in self._songs]
        if not songs:
            return

        # Start at the currently-selected song if it's in the visible set;
        # otherwise fall back to the first visible song.
        start_index = 0
        for p in self._selected_paths():
            if p in visible_paths:
                start_index = visible_paths.index(p)
                break

        dlg = SongSequencerDialog(
            songs=songs,
            filter_label=self._filter_display_name(),
            tag_icons=self._tag_icons,
            existing_tags_provider=self._playlist_store.get_tags,
            json_filename_provider=self._json_filename_for_path,
            player_bar=self._player_bar,
            theme=self._theme,
            start_index=start_index,
            parent=self,
        )
        dlg.rename_requested.connect(self._sequencer_on_rename)
        dlg.metadata_requested.connect(self._sequencer_on_metadata)
        dlg.tags_requested.connect(self._sequencer_on_tags)
        # Persist every per-song commit (Save / Save & Next / Previous /
        # Close all funnel through _commit_current) so tag changes are on
        # disk before the user moves on.
        dlg.committed.connect(self._sequencer_on_committed)
        dlg.exec_()
        # Stats refresh once after the dialog closes — saves already
        # happened mid-session via _sequencer_on_committed.
        self._update_stats()

    def _sequencer_on_committed(self):
        # Per-song edits already updated `_path_to_record` / `_songs` /
        # `_lyrics_store` in-memory. Save the JSON to disk but skip the
        # full reconcile — no need to re-scan the whole library just to
        # pick up changes we already applied.
        self._save_playlist_store(reconcile=False)

    def _sequencer_on_rename(self, old_path: str, new_filename: str):
        if old_path not in self._songs:
            return
        try:
            result = self._playlist_store.rename_song_file(old_path, new_filename)
        except FileExistsError as exc:
            QMessageBox.warning(self, 'Rename Failed', str(exc))
            return
        except (ValueError, OSError) as exc:
            QMessageBox.warning(
                self, 'Rename Failed',
                f'Could not rename "{Path(old_path).name}":\n{exc}',
            )
            return
        if result is None:
            return
        _, new_path = result
        self._lyrics_store.rename_for_song(old_path, new_path)
        self._migrate_song_path_in_ui(old_path, new_path, new_filename)

    def _sequencer_on_metadata(self, path: str, title: str, artist: str, album: str):
        if path not in self._songs:
            return
        if not write_metadata(
            path,
            title=title or None,
            artist=artist or None,
            album=album or None,
        ):
            QMessageBox.warning(
                self, 'Write Failed',
                f'Could not write metadata to:\n{path}'
            )
            return
        song = self._songs[path]
        song['title']  = title
        song['artist'] = artist
        song['album']  = album
        # Refresh the table row labels.
        title_item  = self._path_to_title_item.get(path)
        artist_item = self._path_to_artist_item.get(path)
        album_item  = self._path_to_album_item.get(path)
        if title_item:  title_item.setText(title)
        if artist_item: artist_item.setText(artist)
        if album_item:  album_item.setText(album)
        self._refresh_grid_text(path)

    def _sequencer_on_tags(self, path: str, add_tags: set, remove_tags: set):
        if path not in self._songs:
            return
        self._playlist_store.set_tags_for_paths(
            [path], self._songs, add_tags, remove_tags
        )
        self._refresh_tag_previews([path])

    def _start_scan(self, folder: str):
        self._settings.setValue('last_folder', folder)
        self._cancel_workers()
        self._invalidate_playlist_reconcile()
        reset_decode_failures()
        if self._grid_chunk_timer:
            self._grid_chunk_timer.stop()
            self._grid_chunk_timer.deleteLater()
            self._grid_chunk_timer = None

        self._songs.clear()
        self._ghost_paths.clear()
        self._path_to_grid_item.clear()
        self._path_to_grid_card.clear()
        self._path_to_cover_item.clear()
        self._path_to_title_item.clear()
        self._path_to_artist_item.clear()
        self._path_to_album_item.clear()
        self._path_to_lyrics_label.clear()
        self._path_to_tag_label.clear()
        self._path_to_issue_button.clear()
        self._playlist_issues_by_path.clear()
        self._grid_build_queue.clear()
        self._reset_thumb_queue()
        self._is_scanning = True
        self._playlist_reconcile_pending = False

        self._table.setSortingEnabled(False)
        self._table.setRowCount(0)

        self._grid.clear()

        self._update_stats()
        self._set_buttons_enabled(False)

        display = folder if len(folder) <= 55 else '…' + folder[-52:]
        self._folder_lbl.setText(display)
        self._folder_lbl.setToolTip(folder)
        self._can_rescan = False

        self._status_lbl.setText(f'Scanning {folder} …')
        self._sb_pbar.setRange(0, 0)
        self._sb_pbar.show()
        self._start_scan_pulse()

        worker = ScanWorker(folder)
        worker.song_found.connect(self._on_song_found)
        worker.progress.connect(self._on_scan_progress)
        worker.scan_complete.connect(self._on_scan_complete)
        worker.timing_note.connect(self._dbg)
        worker.start()
        self._scan_worker = worker

    def _on_scan_progress(self, idx: int, filename: str):
        self._status_lbl.setText(f'Scanning ({idx} found) … {filename}')

    def _on_song_found(self, song: dict):
        self._songs[song['path']] = song
        t0 = _time.perf_counter()
        self._add_table_row(song)
        row_ms = (_time.perf_counter() - t0) * 1000
        if row_ms > 80:
            self._dbg(
                f"[row] ⚠ slow row insert {row_ms:.0f} ms — "
                f"{song['filename']} (total rows: {self._table.rowCount()})"
            )
        self._grid_build_queue.append(song['path'])

    def _on_scan_complete(self, total: int, without: int):
        t0 = _time.perf_counter()
        self._is_scanning = False

        self._dbg(f"[1/5] enabling table sort…")
        self._table.setSortingEnabled(True)
        t0 = self._dbg_step("[1/5] table sort enabled", t0)

        self._sb_pbar.hide()
        self._stop_scan_pulse()

        self._dbg(f"[2/5] reconciling tag JSON ({total} songs)…")
        self._request_playlist_reconcile()
        t0 = self._dbg_step("[2/5] tag JSON reconcile queued", t0)

        self._lyrics_store.scan(self._songs)
        self._dbg("[3/5] updating stat counters…")
        self._update_stats()
        t0 = self._dbg_step("[3/5] stat counters updated", t0)

        self._set_buttons_enabled(total > 0)
        self._can_rescan = True

        self._status_lbl.setText(self._compose_library_status())

        self._dbg("[4/5] starting on-demand thumbnail loader…")
        self._ensure_thumb_loader()
        self._refresh_visible_thumbs()
        paths_with_cover = sum(1 for s in self._songs.values() if s['has_cover'])
        t0 = self._dbg_step(
            f"[4/5] thumbnail loader idle — "
            f"{paths_with_cover} song(s) eligible, loading on scroll",
            t0,
        )

        if self._stack.currentIndex() == 1:
            self._dbg("[5/5] building grid…")
            self._start_grid_build()
            self._dbg_step("[5/5] grid build started", t0)

        self._status_lbl.setText(self._compose_library_status())

    def _start_grid_build(self):
        if self._grid_chunk_timer:
            self._grid_chunk_timer.stop()
            self._grid_chunk_timer.deleteLater()
            self._grid_chunk_timer = None
        self._grid_chunk_timer = QTimer(self)
        self._grid_chunk_timer.timeout.connect(self._build_grid_chunk)
        self._grid_chunk_timer.start(0)

    def _build_grid_chunk(self):
        if not self._grid_build_queue:
            if self._grid_chunk_timer:
                self._grid_chunk_timer.stop()
                self._grid_chunk_timer.deleteLater()
                self._grid_chunk_timer = None
            if self._stack.currentIndex() == 1:
                self._schedule_thumb_refresh()
            return
        chunk_size = 60
        for _ in range(min(chunk_size, len(self._grid_build_queue))):
            path = self._grid_build_queue.popleft()
            song = self._songs.get(path)
            if song:
                self._add_grid_item(song)
        if self._stack.currentIndex() == 1:
            self._schedule_thumb_refresh()

    def _start_scan_pulse(self):
        """Pulsing opacity on the status label during scan."""
        eff = QGraphicsOpacityEffect(self._status_lbl)
        self._status_lbl.setGraphicsEffect(eff)
        anim = QPropertyAnimation(eff, b'opacity')
        anim.setDuration(800)
        anim.setStartValue(1.0)
        anim.setEndValue(0.4)
        anim.setEasingCurve(QEasingCurve.InOutSine)
        anim.setLoopCount(-1)  # infinite
        # reverse on each cycle
        anim.finished.connect(lambda: None)
        self._scan_pulse_anim = anim
        self._scan_pulse_eff = eff
        # make it yoyo: connect direction flip
        def _flip():
            if anim.direction() == QPropertyAnimation.Forward:
                anim.setDirection(QPropertyAnimation.Backward)
            else:
                anim.setDirection(QPropertyAnimation.Forward)
        anim.finished.connect(_flip)
        anim.setLoopCount(-1)
        anim.start()

    def _stop_scan_pulse(self):
        if hasattr(self, '_scan_pulse_anim') and self._scan_pulse_anim:
            self._scan_pulse_anim.stop()
            self._scan_pulse_anim = None
        self._status_lbl.setGraphicsEffect(None)

    # =========================================================================
    # Song list helpers
    # =========================================================================

    def _add_table_row(self, song: dict):
        if not self._is_scanning:
            self._table.setSortingEnabled(False)
        row = self._table.rowCount()
        self._table.insertRow(row)
        self._table.setRowHeight(row, ROW_HEIGHT)

        # Col 0 – cover thumbnail
        cover_item = QTableWidgetItem()
        cover_item.setIcon(QIcon(_make_placeholder(song['has_cover'], THUMB_SIZE, self._theme)))
        cover_item.setData(Qt.UserRole, song['path'])
        self._table.setItem(row, COL_COVER, cover_item)
        self._path_to_cover_item[song['path']] = cover_item

        # Col 1-4 + fmt – text metadata + filename
        for col, key in [
            (COL_TITLE,    'title'),
            (COL_ARTIST,   'artist'),
            (COL_ALBUM,    'album'),
            (COL_FILENAME, 'filename'),
            (COL_FMT,      'format'),
        ]:
            val = song.get(key, '')
            item = QTableWidgetItem(val)
            item.setData(Qt.UserRole, song['path'])
            if col == COL_TITLE:
                item.setFont(QFont('Segoe UI', 10, QFont.Medium))
            if col == COL_FILENAME:
                color = '#ef4444' if song.get('is_ghost') else self._theme['text_dim']
                item.setForeground(QColor(color))
            self._table.setItem(row, col, item)
            if col == COL_TITLE:
                self._path_to_title_item[song['path']] = item
            elif col == COL_ARTIST:
                self._path_to_artist_item[song['path']] = item
            elif col == COL_ALBUM:
                self._path_to_album_item[song['path']] = item

        # COL_LYRICS – lyrics presence icon
        has_lyrics = self._lyrics_store.has_lyrics(song['path'])
        lyr_lbl = QLabel()
        lyr_lbl.setAlignment(Qt.AlignCenter)
        lyr_color = self._theme.get('cyan', '#0891b2') if has_lyrics else self._theme.get('text_dim', '#94a3b8')
        lyr_lbl.setPixmap(get_icon('text', 16, lyr_color).pixmap(16, 16))
        lyr_lbl.setToolTip('Has lyrics' if has_lyrics else 'No lyrics')
        self._table.setCellWidget(row, COL_LYRICS, lyr_lbl)
        self._path_to_lyrics_label[song['path']] = lyr_lbl

        tag_preview = self._make_tag_preview_label(song['path'], icon_size=16, max_icons=8)
        self._table.setCellWidget(row, COL_TAGS, tag_preview)
        self._path_to_tag_label[song['path']] = tag_preview

        issue_btn = self._make_issue_button(song['path'])
        self._table.setCellWidget(row, COL_ISSUES, issue_btn)

        # COL_SIZE – file size
        size_bytes = 0
        try:
            size_bytes = os.path.getsize(song['path'])
        except OSError:
            pass
        size_item = _NumericItem(_fmt_size(size_bytes))
        size_item.setData(Qt.UserRole, size_bytes)
        size_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        size_item.setForeground(QColor(self._theme['text_dim']))
        size_item.setFlags(size_item.flags() & ~Qt.ItemIsEditable)
        self._table.setItem(row, COL_SIZE, size_item)

        if not self._is_scanning:
            self._table.setSortingEnabled(True)

    def _add_grid_item(self, song: dict):
        title = song.get('title') or song['filename']
        artist = song.get('artist', '')

        item = QListWidgetItem()
        item.setData(Qt.UserRole, song['path'])
        item.setToolTip(f'{song["filename"]}\n{title}' + (f'\n{artist}' if artist else ''))
        item.setSizeHint(QSize(GRID_ITEM_W, GRID_ITEM_H))
        self._grid.addItem(item)
        self._path_to_grid_item[song['path']] = item

        card = SongGridCard()
        card.set_cover(_make_placeholder(song['has_cover'], GRID_ICON, self._theme))
        card.set_text(title, artist)
        html, tooltip = self._build_tag_preview(song['path'], icon_size=14, max_icons=5)
        card.set_tags(html, tooltip)
        self._grid.setItemWidget(item, card)
        self._path_to_grid_card[song['path']] = card

    def _update_row_cover(self, path: str, img_data: bytes | None, has: bool, cache_data: bool = True):
        """Refresh cover icon in both table and grid."""
        # ── table ──────────────────────────────────────────────────────────────
        cover_item = self._path_to_cover_item.get(path)
        if cover_item:
            px = _load_pixmap(img_data, THUMB_SIZE) if img_data else None
            cover_item.setIcon(
                QIcon(px) if px else QIcon(_make_placeholder(has, THUMB_SIZE, self._theme))
            )

        # ── grid ───────────────────────────────────────────────────────────────
        grid_item = self._path_to_grid_item.get(path)
        if grid_item:
            px = _load_pixmap(img_data, GRID_ICON) if img_data else None
            card = self._path_to_grid_card.get(path)
            if card:
                card.set_cover(px or _make_placeholder(has, GRID_ICON, self._theme))

        if path in self._songs:
            self._songs[path]['has_cover'] = has
            if not has:
                self._songs[path].pop('cover_data', None)
            elif img_data and cache_data:
                self._songs[path]['cover_data'] = img_data

    def _refresh_grid_text(self, path: str):
        song   = self._songs.get(path, {})
        title  = song.get('title') or song.get('filename', '')
        artist = song.get('artist', '')
        card = self._path_to_grid_card.get(path)
        if card:
            card.set_text(title, artist)
        item = self._path_to_grid_item.get(path)
        if item:
            item.setToolTip(
                f'{song.get("filename", "")}\n{title}' + (f'\n{artist}' if artist else '')
            )

    def _refresh_song_lyrics_cell(self, path: str):
        label = self._path_to_lyrics_label.get(path)
        if not label:
            return
        has = self._lyrics_store.has_lyrics(path)
        color = self._theme.get('cyan', '#0891b2') if has else self._theme.get('text_dim', '#94a3b8')
        label.setPixmap(get_icon('text', 16, color).pixmap(16, 16))
        label.setToolTip('Has lyrics' if has else 'No lyrics')

    def _refresh_song_row(self, path: str):
        """Refresh all visible cells for a single song row + update counters."""
        song = self._songs.get(path)
        if not song:
            return
        for item_dict, key in [
            (self._path_to_title_item,  'title'),
            (self._path_to_artist_item, 'artist'),
            (self._path_to_album_item,  'album'),
        ]:
            item = item_dict.get(path)
            if item:
                item.setText(song.get(key, ''))
        self._refresh_song_lyrics_cell(path)
        self._refresh_song_tag_preview(path)
        self._refresh_grid_text(path)
        self._update_stats()

    def _build_tag_preview(self, path: str, icon_size: int = 16, max_icons: int | None = None) -> tuple[str, str]:
        tags = self._playlist_store.get_tags(path)
        if not tags:
            return '', 'No tags assigned'

        shown = tags if max_icons is None else tags[:max_icons]
        parts = []
        for tag in shown:
            label = display_for(tag)
            icon_path = self._tag_icons.get(tag, '')
            if icon_path and os.path.exists(icon_path):
                uri = Path(icon_path).resolve().as_uri()
                parts.append(
                    f'<img src="{uri}" width="{icon_size}" height="{icon_size}" title="{label}"/>'
                )
            else:
                parts.append(
                    f'<span style="color:#94a3b8; font-size:{max(icon_size - 2, 9)}px;">{label}</span>'
                )

        remaining = len(tags) - len(shown)
        if remaining > 0:
            parts.append(
                f'<span style="color:#94a3b8; font-size:{max(icon_size - 2, 9)}px;">+{remaining}</span>'
            )
        return ' '.join(parts), ', '.join(display_for(t) for t in tags)

    def _build_tag_preview_pixmap(self, path: str, icon_size: int = 16, max_icons: int | None = None) -> QPixmap | None:
        tags = self._playlist_store.get_tags(path)
        if not tags:
            return None

        shown = tags if max_icons is None else tags[:max_icons]
        remaining = max(0, len(tags) - len(shown))
        gap = 4
        badge_width = icon_size + 8
        width = len(shown) * icon_size + max(0, len(shown) - 1) * gap
        if remaining:
            width += gap + badge_width
        if width <= 0:
            return None

        pm = QPixmap(width, icon_size)
        pm.fill(Qt.transparent)

        painter = QPainter(pm)
        painter.setRenderHint(QPainter.Antialiasing)

        x = 0
        for tag in shown:
            icon_path = resolve_tag_icon(tag) or self._tag_icons.get(tag, '')
            icon = icon_from_file(icon_path, icon_size) if icon_path and os.path.exists(icon_path) else QIcon()
            icon_pm = icon.pixmap(icon_size, icon_size) if not icon.isNull() else QPixmap()
            if icon_pm.isNull():
                painter.setPen(QPen(QColor(self._theme['text_dim'])))
                font = QFont(self.font())
                font.setPixelSize(max(9, icon_size - 4))
                painter.setFont(font)
                painter.drawText(x, 0, icon_size + 20, icon_size, Qt.AlignLeft | Qt.AlignVCenter, display_for(tag))
                x += icon_size + 20
            else:
                painter.drawPixmap(x, 0, icon_pm)
                x += icon_size
            x += gap

        if remaining:
            badge_x = max(0, width - badge_width)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(self._theme['panel_alt']))
            painter.drawRoundedRect(badge_x, 1, badge_width, max(14, icon_size - 2), 6, 6)
            painter.setPen(QColor(self._theme['text_secondary']))
            font = QFont(self.font())
            font.setPixelSize(max(9, icon_size - 4))
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(badge_x, 0, badge_width, icon_size, Qt.AlignCenter, f'+{remaining}')

        painter.end()
        return pm

    def _make_tag_preview_label(self, path: str, icon_size: int = 16, max_icons: int | None = None) -> QLabel:
        label = QLabel()
        label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        label.setWordWrap(False)
        label.setMargin(2)
        label.setMinimumHeight(icon_size + 6)
        pixmap = self._build_tag_preview_pixmap(path, icon_size=icon_size, max_icons=max_icons)
        _, tooltip = self._build_tag_preview(path, icon_size=icon_size, max_icons=max_icons)
        if pixmap:
            label.setPixmap(pixmap)
        else:
            label.setText('—')
            label.setStyleSheet('color:#64748b;')
        label.setToolTip(tooltip)
        return label

    def _refresh_song_tag_preview(self, path: str):
        label = self._path_to_tag_label.get(path)
        if isinstance(label, QLabel):
            pixmap = self._build_tag_preview_pixmap(path, icon_size=16, max_icons=8)
            _, tooltip = self._build_tag_preview(path, icon_size=16, max_icons=8)
            if pixmap:
                label.setPixmap(pixmap)
                label.setText('')
            else:
                label.setPixmap(QPixmap())
                label.setText('—')
            label.setToolTip(tooltip)
        card = self._path_to_grid_card.get(path)
        if card:
            html, tooltip = self._build_tag_preview(path, icon_size=14, max_icons=5)
            card.set_tags(html, tooltip)

    def _refresh_tag_previews(self, paths: set[str] | list[str] | tuple[str, ...] | None = None):
        targets = list(paths) if paths is not None else list(self._songs.keys())
        for path in targets:
            self._refresh_song_tag_preview(path)

    def _rebuild_playlist_issue_map(self):
        issues_by_path: dict[str, list[dict]] = {}

        for entry in self._playlist_store.mismatch_records:
            attached = False
            matched_path = entry.get('matched_path', '')
            if matched_path:
                issue = dict(entry)
                issue['issue_type'] = 'mismatch'
                issues_by_path.setdefault(matched_path, []).append(issue)
                attached = True
            for candidate_path in entry.get('candidate_paths', []):
                issue = dict(entry)
                issue['issue_type'] = 'mismatch'
                issues_by_path.setdefault(candidate_path, []).append(issue)
                attached = True
            if not attached and entry.get('path') in self._songs:
                issue = dict(entry)
                issue['issue_type'] = 'mismatch'
                issues_by_path.setdefault(entry['path'], []).append(issue)

        for entry in self._playlist_store.invalid_tag_records:
            matched_path = entry.get('matched_path', '')
            if matched_path:
                issue = dict(entry)
                issue['issue_type'] = 'invalid_tag'
                issues_by_path.setdefault(matched_path, []).append(issue)

        self._playlist_issues_by_path = issues_by_path

    def _sync_ghost_rows(self):
        """Remove stale ghost rows and rebuild them from missing_song_records."""
        if self._ghost_paths:
            self._table.setUpdatesEnabled(False)
            try:
                for row in range(self._table.rowCount() - 1, -1, -1):
                    item = self._table.item(row, COL_COVER)
                    if item and item.data(Qt.UserRole) in self._ghost_paths:
                        self._table.removeRow(row)
            finally:
                self._table.setUpdatesEnabled(True)

            for path in self._ghost_paths:
                grid_item = self._path_to_grid_item.pop(path, None)
                self._path_to_grid_card.pop(path, None)
                if grid_item is not None:
                    row = self._grid.row(grid_item)
                    if row >= 0:
                        self._grid.takeItem(row)
                self._songs.pop(path, None)
                self._path_to_cover_item.pop(path, None)
                self._path_to_title_item.pop(path, None)
                self._path_to_artist_item.pop(path, None)
                self._path_to_album_item.pop(path, None)
                self._path_to_tag_label.pop(path, None)
                self._path_to_issue_button.pop(path, None)
            self._ghost_paths.clear()

        for entry in self._playlist_store.missing_song_records:
            record_index = entry.get('record_index', -1)
            if not (0 <= record_index < len(self._playlist_store.records)):
                continue
            record = self._playlist_store.records[record_index]
            ghost_path = f'__missing__:{record_index}'
            ghost_song = {
                'path': ghost_path,
                'filename': entry.get('title', ''),
                'title': '',
                'artist': '',
                'album': '',
                'has_cover': False,
                'format': '',
                'is_ghost': True,
            }
            self._songs[ghost_path] = ghost_song
            self._ghost_paths.add(ghost_path)
            self._playlist_store._path_to_record[ghost_path] = record
            self._add_table_row(ghost_song)
            self._add_grid_item(ghost_song)

    def _make_issue_button(self, path: str) -> QToolButton:
        btn = QToolButton()
        btn.setAutoRaise(True)
        btn.setIconSize(QSize(18, 18))
        btn.clicked.connect(lambda _=False, song_path=path: self._on_song_issue_clicked(song_path))
        self._path_to_issue_button[path] = btn
        self._refresh_song_issue_indicator(path)
        return btn

    def _refresh_song_issue_indicator(self, path: str):
        btn = self._path_to_issue_button.get(path)
        if not btn:
            return
        issues = self._playlist_issues_by_path.get(path, [])
        if not issues:
            btn.hide()
            btn.setEnabled(False)
            btn.setToolTip('')
            return

        btn.show()
        btn.setEnabled(True)
        btn.setIcon(get_icon('warning', 18, '#f59e0b'))
        count = len(issues)
        summary = issues[0].get('song') or self._songs.get(path, {}).get('filename', path)
        btn.setToolTip(
            f'{count} JSON warning(s) for {summary}\nClick to review and update.'
        )

    def _refresh_issue_indicators(self, paths: set[str] | list[str] | tuple[str, ...] | None = None):
        targets = list(paths) if paths is not None else list(self._songs.keys())
        for path in targets:
            self._refresh_song_issue_indicator(path)

    # =========================================================================
    # Thumbnail loading (lazy, scroll-driven)
    # =========================================================================

    def _ensure_thumb_loader(self):
        """Make sure a single long-lived ThumbnailWorker is running."""
        if self._thumb_worker and self._thumb_worker.isRunning():
            return
        worker = ThumbnailWorker(self._thumb_queue, THUMB_SIZE, GRID_ICON)
        worker.thumb_ready.connect(self._on_thumb_ready)
        worker.start()
        self._thumb_worker = worker

    def _reset_thumb_queue(self):
        """Drop pending thumbnail requests (used on rescan / song-list reset)."""
        try:
            while True:
                self._thumb_queue.get_nowait()
        except queue.Empty:
            pass
        self._thumbs_queued.clear()

    def _schedule_thumb_refresh(self, *_):
        if self._thumb_scroll_timer:
            self._thumb_scroll_timer.start(50)

    def _refresh_visible_thumbs(self):
        """Push paths for currently-visible rows onto the thumbnail queue."""
        if not self._songs:
            return
        self._ensure_thumb_loader()

        if self._stack.currentIndex() == 1:
            self._enqueue_visible_grid_thumbs()
        else:
            self._enqueue_visible_table_thumbs()

    def _enqueue_visible_table_thumbs(self):
        table = self._table
        row_count = table.rowCount()
        if row_count == 0:
            return
        viewport_h = max(1, table.viewport().height())
        top = table.rowAt(0)
        bottom = table.rowAt(viewport_h - 1)
        if top < 0:
            top = 0
        if bottom < 0:
            bottom = row_count - 1
        top = max(0, top - THUMB_OVERSCAN_ROWS)
        bottom = min(row_count - 1, bottom + THUMB_OVERSCAN_ROWS)
        for row in range(top, bottom + 1):
            item = table.item(row, COL_COVER)
            if not item:
                continue
            path = item.data(Qt.UserRole)
            self._maybe_enqueue_thumb(path)

    def _enqueue_visible_grid_thumbs(self):
        grid = self._grid
        if grid.count() == 0:
            return
        rect = grid.viewport().rect().adjusted(
            0, -THUMB_OVERSCAN_ROWS * GRID_ITEM_H, 0, THUMB_OVERSCAN_ROWS * GRID_ITEM_H
        )
        for path, item in self._path_to_grid_item.items():
            if item.isHidden():
                continue
            item_rect = grid.visualItemRect(item)
            if item_rect.isValid() and rect.intersects(item_rect):
                self._maybe_enqueue_thumb(path)

    def _maybe_enqueue_thumb(self, path: str):
        if not path or path in self._thumbs_queued:
            return
        song = self._songs.get(path)
        if not song or not song.get('has_cover'):
            return
        if song.get('cover_data'):
            return
        self._thumbs_queued.add(path)
        self._thumb_queue.put(path)

    def _on_thumb_ready(self, path: str, thumb_rgba: bytes, grid_rgba: bytes):
        """Fast slot: worker pre-decoded RGBA pixels, no PIL/decode work here."""
        thumb_px = _pixmap_from_rgba(thumb_rgba, THUMB_SIZE) if thumb_rgba else None
        grid_px  = _pixmap_from_rgba(grid_rgba,  GRID_ICON)  if grid_rgba  else None

        cover_item = self._path_to_cover_item.get(path)
        if cover_item and thumb_px:
            cover_item.setIcon(QIcon(thumb_px))

        card = self._path_to_grid_card.get(path)
        if card and grid_px:
            card.set_cover(grid_px)

        if path in self._songs:
            self._songs[path]['has_cover'] = True

    def eventFilter(self, obj, event):
        from PyQt5.QtCore import QEvent
        if event.type() == QEvent.Resize:
            try:
                if obj is self._table.viewport() or obj is self._grid.viewport():
                    self._schedule_thumb_refresh()
            except RuntimeError:
                pass
        return super().eventFilter(obj, event)

    # =========================================================================
    # Slots – view toggle
    # =========================================================================

    def _on_set_view(self, mode: str):
        ic = self._icon_color()
        if mode == 'grid':
            self._stack.setCurrentIndex(1)
            self._btn_view_grid.setObjectName('btnViewActive')
            self._btn_view_list.setObjectName('btnViewInactive')
            self._btn_view_grid.setIcon(get_icon('grid', 24, '#ffffff'))
            self._btn_view_list.setIcon(get_icon('list', 24, ic))
            if self._grid_build_queue and not self._grid_chunk_timer:
                self._start_grid_build()
        else:
            self._stack.setCurrentIndex(0)
            self._btn_view_list.setObjectName('btnViewActive')
            self._btn_view_grid.setObjectName('btnViewInactive')
            self._btn_view_list.setIcon(get_icon('list', 24, '#ffffff'))
            self._btn_view_grid.setIcon(get_icon('grid', 24, ic))
        for btn in (self._btn_view_list, self._btn_view_grid):
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        self._schedule_thumb_refresh()

    # =========================================================================
    # Slots – player
    # =========================================================================

    def _on_table_double_click(self, row: int, _col: int):
        item = self._table.item(row, COL_COVER)
        if item:
            path = item.data(Qt.UserRole)
            if path:
                self._play_song(path)

    def _on_grid_double_click(self, item: QListWidgetItem):
        path = item.data(Qt.UserRole)
        if path:
            self._play_song(path)

    def _play_song(self, path: str):
        song = self._songs.get(path)
        if not song:
            return
        cover_data = song.get('cover_data') or b''
        if not cover_data and song.get('has_cover'):
            cover_data, _ = read_cover(path)
            if cover_data:
                song['cover_data'] = cover_data
        self._player_bar.play_song(
            path,
            song.get('title') or song['filename'],
            song.get('artist', ''),
            song.get('album', ''),
            cover_data,
            song.get('duration_ms', 0),
        )
        self._current_play_path = path

    def _get_ordered_paths(self) -> list[str]:
        """Return song paths in current table sort order."""
        paths = []
        for row in range(self._table.rowCount()):
            item = self._table.item(row, COL_COVER)
            if item:
                p = item.data(Qt.UserRole)
                if p:
                    paths.append(p)
        return paths

    def _on_player_prev(self):
        paths = self._get_ordered_paths()
        if not paths or not self._current_play_path:
            return
        try:
            idx = paths.index(self._current_play_path)
            new_idx = (idx - 1) % len(paths)
            self._play_song(paths[new_idx])
        except ValueError:
            pass

    def _on_player_next(self):
        paths = self._get_ordered_paths()
        if not paths or not self._current_play_path:
            return
        if self._player_bar.is_shuffle() and len(paths) > 1:
            import random
            candidates = [p for p in paths if p != self._current_play_path]
            self._play_song(random.choice(candidates))
            return
        try:
            idx = paths.index(self._current_play_path)
            new_idx = (idx + 1) % len(paths)
            self._play_song(paths[new_idx])
        except ValueError:
            pass

    def _on_shuffle_toggled(self, on: bool):
        self._settings.setValue('shuffle', 'true' if on else 'false')

    # =========================================================================
    # Slots – actions
    # =========================================================================

    def _on_selection_changed(self):
        selected = self._selected_paths()
        count = len(selected)
        if count == 0:
            self._hint_lbl.setText('← Select a song in the list below')
        elif count == 1:
            song = self._songs.get(selected[0], {})
            name = song.get('title') or song.get('filename', '')
            self._hint_lbl.setText(f'Selected: {name}')
        else:
            self._hint_lbl.setText(f'{count} songs selected')
        self._update_remove_missing_btn()

    def _on_auto_assign_selected(self):
        selected = self._selected_paths()
        if not selected:
            QMessageBox.information(
                self, 'Auto Assign',
                'No songs selected. Use "Auto Assign All Songs" instead.'
            )
            return
        targets = [self._songs[p] for p in selected
                   if p in self._songs and not self._songs[p]['has_cover']]
        if not targets:
            QMessageBox.information(
                self, 'Auto Assign',
                'All selected songs already have a cover.'
            )
            return
        self._run_auto_assign(targets)

    def _on_auto_assign_all(self):
        targets = [s for s in self._songs.values() if not s['has_cover']]
        if not targets:
            QMessageBox.information(
                self, 'Auto Assign',
                'Every song already has cover art — nothing to do!'
            )
            return
        self._run_auto_assign(targets)

    def _run_auto_assign(self, targets: list):
        self._set_buttons_enabled(False)
        dlg = ProgressDialog('Auto Assigning Cover Art', self)
        dlg.cancelled.connect(self._cancel_auto_assign)
        self._progress_dlg = dlg

        worker = AutoAssignWorker(targets)
        worker.progress.connect(dlg.update_progress)
        worker.song_done.connect(self._on_song_auto_done)
        worker.finished.connect(self._on_auto_assign_finished)
        self._assign_worker = worker
        worker.start()
        dlg.show()

    def _on_song_auto_done(self, path: str, img_data: bytes, success: bool):
        self._update_row_cover(path, img_data if success else None, success)
        self._update_stats()
        if success and img_data and self._progress_dlg:
            self._progress_dlg.update_image(img_data)

    def _on_auto_assign_finished(self, assigned: int, failed: int):
        if self._progress_dlg:
            self._progress_dlg.close()
            self._progress_dlg = None
        self._update_stats()
        self._set_buttons_enabled(True)
        self._assign_worker = None
        self._status_lbl.setText(
            f'Auto assign complete — {assigned} assigned, {failed} failed / not found.'
        )

    def _cancel_auto_assign(self):
        if self._assign_worker:
            self._assign_worker.cancel()

    def _on_assign_specific(self):
        selected = self._selected_paths()
        if len(selected) != 1:
            QMessageBox.information(
                self, 'Assign Specific',
                'Please select exactly one song to use this feature.'
            )
            return
        path = selected[0]
        song = self._songs.get(path)
        if not song:
            return
        dlg = ImagePickerDialog(song, self)
        if dlg.exec_() == ImagePickerDialog.Accepted:
            img = dlg.get_selected_image()
            if img:
                if write_cover(path, img):
                    self._songs[path]['has_cover'] = True
                    self._update_row_cover(path, img, True)
                    self._update_stats()
                    self._status_lbl.setText(
                        f'Cover assigned to: {song.get("title") or song["filename"]}'
                    )
                else:
                    QMessageBox.warning(
                        self, 'Error',
                        f'Failed to write cover art to:\n{path}\n\n'
                        'The file may be read-only or in an unsupported format.'
                    )

    def _on_delete_cover(self):
        selected = self._selected_paths()
        if not selected:
            QMessageBox.information(self, 'Delete Cover', 'No songs selected.')
            return
        targets = [p for p in selected
                   if p in self._songs and self._songs[p]['has_cover']]
        if not targets:
            QMessageBox.information(
                self, 'Delete Cover',
                'None of the selected songs have a cover to delete.'
            )
            return
        names = '\n'.join(
            f'  • {self._songs[p].get("title") or self._songs[p]["filename"]}'
            for p in targets[:8]
        )
        if len(targets) > 8:
            names += f'\n  … and {len(targets) - 8} more'

        reply = QMessageBox.question(
            self, 'Delete Cover Art',
            f'Remove embedded cover from {len(targets)} song(s)?\n\n{names}',
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        done = 0
        for path in targets:
            if delete_cover(path):
                self._songs[path]['has_cover'] = False
                self._update_row_cover(path, None, False)
                done += 1
        self._update_stats()
        self._status_lbl.setText(f'Deleted cover art from {done} song(s).')

    def _on_assign_playlists(self):
        if self._playlist_reconcile_pending:
            QMessageBox.information(
                self,
                'Assign Tags',
                'Tag JSON matching is still in progress. Wait for it to finish before editing tags.',
            )
            return

        selected = self._selected_paths()
        if not selected:
            QMessageBox.information(
                self, 'Assign Tags',
                'Select one or more songs before editing tag assignments.'
            )
            return

        songs = [self._songs[p] for p in selected if p in self._songs]
        if not songs:
            QMessageBox.information(self, 'Assign Tags', 'No valid songs selected.')
            return

        existing_tags = {
            song['path']: self._playlist_store.get_tags(song['path'])
            for song in songs
        }
        dlg = PlaylistAssignmentDialog(
            songs=songs,
            tag_icons=self._tag_icons,
            existing_tags=existing_tags,
            json_path=self._playlist_json_path,
            theme=self._theme,
            parent=self,
        )
        if dlg.exec_() != PlaylistAssignmentDialog.Accepted:
            return

        add_tags, remove_tags = dlg.get_tag_changes()
        if not add_tags and not remove_tags:
            self._status_lbl.setText('No tag changes applied.')
            return

        created = self._prompt_create_missing_records(selected, 'tag changes')
        changed = self._playlist_store.set_tags_for_paths(
            selected, self._songs, add_tags, remove_tags
        )

        if not changed and not created:
            self._status_lbl.setText('No tag changes applied.')
            return

        self._refresh_tag_previews(selected)
        if self._save_playlist_store():
            action_parts = []
            if created:
                action_parts.append(f'created {created} entries')
            if add_tags:
                action_parts.append(f'added {len(add_tags)}')
            if remove_tags:
                action_parts.append(f'removed {len(remove_tags)}')
            action_text = ', '.join(action_parts) if action_parts else 'updated'
            self._status_lbl.setText(
                f'Tag assignments saved for {changed} song(s) ({action_text}).'
            )

    def _apply_rename_intents(
        self,
        selected: list[str],
        intents: dict[str, str],
    ) -> tuple[list[str], int]:
        """Rename files on disk and update in-memory song state.

        Returns the (possibly translated) selection list and the number of
        successful renames.
        """
        translated = list(selected)
        renamed = 0
        for old_path, new_filename in intents.items():
            if old_path not in self._songs:
                continue
            try:
                result = self._playlist_store.rename_song_file(old_path, new_filename)
            except FileExistsError as exc:
                QMessageBox.warning(self, 'Rename Failed', str(exc))
                continue
            except (ValueError, OSError) as exc:
                QMessageBox.warning(
                    self,
                    'Rename Failed',
                    f'Could not rename "{Path(old_path).name}":\n{exc}',
                )
                continue
            if result is None:
                continue
            _, new_path = result
            self._lyrics_store.rename_for_song(old_path, new_path)
            self._migrate_song_path_in_ui(old_path, new_path, new_filename)
            translated = [new_path if p == old_path else p for p in translated]
            renamed += 1
        return translated, renamed

    def _migrate_song_path_in_ui(self, old_path: str, new_path: str, new_filename: str):
        """Move all per-path UI state from old_path to new_path."""
        song = self._songs.pop(old_path, None)
        if song is None:
            return
        song['path'] = new_path
        song['filename'] = new_filename
        self._songs[new_path] = song

        for mapping in (
            self._path_to_cover_item,
            self._path_to_title_item,
            self._path_to_artist_item,
            self._path_to_album_item,
            self._path_to_tag_label,
            self._path_to_issue_button,
            self._path_to_grid_item,
            self._path_to_grid_card,
        ):
            if old_path in mapping:
                mapping[new_path] = mapping.pop(old_path)

        cover_item = self._path_to_cover_item.get(new_path)
        if cover_item:
            cover_item.setData(Qt.UserRole, new_path)
            row = cover_item.row()
            filename_item = self._table.item(row, COL_FILENAME)
            if filename_item:
                filename_item.setText(new_filename)
                filename_item.setData(Qt.UserRole, new_path)
            for col_item in (
                self._path_to_title_item.get(new_path),
                self._path_to_artist_item.get(new_path),
                self._path_to_album_item.get(new_path),
            ):
                if col_item:
                    col_item.setData(Qt.UserRole, new_path)

        grid_item = self._path_to_grid_item.get(new_path)
        if grid_item:
            grid_item.setData(Qt.UserRole, new_path)

        if old_path in self._thumbs_queued:
            self._thumbs_queued.discard(old_path)
        if old_path in self._playlist_issues_by_path:
            self._playlist_issues_by_path[new_path] = self._playlist_issues_by_path.pop(old_path)

    # ── Fix Metadata ──────────────────────────────────────────────────────────

    def _on_fix_auto(self):
        """Parse filenames and write metadata directly after one confirmation."""
        proposals = self._build_proposals()
        if not proposals:
            return

        reply = QMessageBox.question(
            self, 'Auto Fix Metadata from Filename',
            f'Write title/artist/album metadata for {len(proposals)} song(s)?\n\n'
            'Parses  "Artist - Song Title (Album)"  from each filename.\n'
            'Content in brackets [...] is ignored.\n'
            'The filename on disk is NOT changed.',
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self._apply_tag_changes(proposals)

    def _on_fix_manual(self):
        """Open an editable preview dialog before writing any metadata."""
        proposals = self._build_proposals(require_separator=False)
        if not proposals:
            return
        dlg = MetadataReviewDialog(proposals, self)
        if dlg.exec_() == MetadataReviewDialog.Accepted:
            changes = dlg.get_changes()
            path_song = {p: s for p, s, _, _, _ in proposals}
            final = [
                (p, path_song.get(p, {}), a, t, al)
                for p, a, t, al in changes
                if a or t or al
            ]
            self._apply_tag_changes(final)

    def _build_proposals(self, require_separator: bool = True) -> list:
        """
        Returns list of (path, song_dict, artist, title, album) for the current pool.
        If require_separator is True, only include files where ' - ' was found.
        """
        selected = self._selected_paths()
        pool = (
            [self._songs[p] for p in selected if p in self._songs]
            if selected else list(self._songs.values())
        )
        if not pool:
            QMessageBox.information(self, 'Fix Metadata', 'No songs to process.')
            return []

        proposals = []
        for song in pool:
            artist, title, album = parse_filename(song['filename'])
            if require_separator and not artist:
                continue   # skip files with no " - " pattern
            proposals.append((song['path'], song, artist, title, album))

        if not proposals:
            QMessageBox.information(
                self, 'Fix Metadata',
                'No filenames with an "Artist - Song Title" pattern found\n'
                'in the current selection.'
            )
        return proposals

    def _apply_tag_changes(self, proposals: list):
        """Write (artist, title, album) metadata and update the table + grid."""
        done = 0
        touched_paths: list[str] = []
        playlist_dirty = False
        for path, _, artist, title, album in proposals:
            if write_metadata(path, title=title or None, artist=artist or None,
                              album=album or None):
                if title is not None:
                    self._songs[path]['title'] = title
                    item = self._path_to_title_item.get(path)
                    if item:
                        item.setText(title)
                if artist is not None:
                    self._songs[path]['artist'] = artist
                    item = self._path_to_artist_item.get(path)
                    if item:
                        item.setText(artist)
                if album is not None:
                    self._songs[path]['album'] = album
                    item = self._path_to_album_item.get(path)
                    if item:
                        item.setText(album)
                self._refresh_grid_text(path)
                playlist_dirty = self._playlist_store.sync_song(self._songs[path]) or playlist_dirty
                touched_paths.append(path)
                done += 1
        # Metadata writes go straight into the audio file via mutagen — they
        # don't require a JSON entry to exist, so we don't prompt to create
        # one here. (The JSON only tracks tag assignments.)
        if playlist_dirty:
            self._save_playlist_store()
        self._update_stats()
        self._status_lbl.setText(f'Metadata updated for {done} song(s).')

    # =========================================================================
    # Helpers
    # =========================================================================

    def _on_song_issue_clicked(self, path: str):
        song = self._songs.get(path)
        issues = self._playlist_issues_by_path.get(path, [])
        if not song or not issues:
            return

        dlg = PlaylistSongIssueDialog(
            song=song,
            issues=issues,
            current_tags=self._playlist_store.get_tags(path),
            parent=self,
        )
        if dlg.exec_() != dlg.Accepted:
            return

        issue = dlg.selected_issue()
        action = dlg.selected_action()
        if action == PlaylistSongIssueDialog.ACTION_UPDATE_JSON:
            self._apply_issue_update_from_song(path, song, issue)
        elif action == PlaylistSongIssueDialog.ACTION_REMOVE_INVALID_TAGS:
            self._apply_issue_remove_invalid_tags(issue)

    def _apply_issue_update_from_song(self, path: str, song: dict, issue: dict):
        record_index = int(issue.get('record_index', -1))
        if record_index < 0:
            QMessageBox.information(self, 'JSON Warning', 'This issue cannot be updated automatically.')
            return
        if not self._playlist_store.update_record_from_song(record_index, path, song):
            QMessageBox.information(self, 'JSON Warning', 'The JSON record already matches this file.')
            return
        if self._save_playlist_store():
            self._status_lbl.setText(f'Updated JSON record for {song.get("filename") or path}.')

    def _apply_issue_remove_invalid_tags(self, issue: dict):
        record_index = int(issue.get('record_index', -1))
        if record_index < 0:
            QMessageBox.information(self, 'JSON Warning', 'This invalid tag entry cannot be updated automatically.')
            return
        if not self._playlist_store.remove_invalid_tags_from_record(record_index, self._tag_icons.keys()):
            QMessageBox.information(self, 'JSON Warning', 'No invalid tags were removed.')
            return
        if self._save_playlist_store():
            self._status_lbl.setText('Removed invalid JSON tag names.')

    def _set_song_filter(self, filter_name: str):
        if filter_name == self._active_song_filter and filter_name != 'all':
            self._active_song_filter = 'all'
        else:
            self._active_song_filter = filter_name
        self._apply_song_filter()

    def _on_stat_row_clicked(self, filter_name: str):
        """Most rows just toggle a table filter; a few (orphan lyrics) open
        an informational popup instead because there's no song to filter to."""
        if filter_name == 'lyrics_orphan':
            self._show_orphan_lyrics_dialog()
            return
        self._set_song_filter(filter_name)

    def _show_orphan_lyrics_dialog(self):
        from PyQt5.QtWidgets import QDialog, QListWidget, QListWidgetItem, QCheckBox
        orphans = self._lyrics_store.orphan_lrc_files
        if not orphans:
            QMessageBox.information(
                self, 'Orphan Lyrics',
                'No orphan .lrc files — every lyrics file matches a song.'
            )
            return

        t = self._theme
        bg       = t.get('bg', '#f8fafc')
        bg_card  = t.get('bg_card', '#ffffff')
        text_c   = t.get('text', '#1e293b')
        dim      = t.get('text_muted', '#64748b')
        border   = t.get('border', '#e2e8f0')
        accent   = t.get('accent', '#7c3aed')
        red      = t.get('red', '#dc2626')

        dlg = QDialog(self)
        dlg.setWindowTitle('Orphan Lyrics Cleanup')
        dlg.resize(520, 420)
        dlg.setStyleSheet(f'QDialog{{background:{bg};}}')

        v = QVBoxLayout(dlg)
        v.setContentsMargins(20, 18, 20, 16)
        v.setSpacing(10)

        title_lbl = QLabel(f'Orphan Lyrics  ·  {len(orphans)} file(s)')
        title_lbl.setStyleSheet(f'color:{text_c};font-size:16px;font-weight:700;')
        v.addWidget(title_lbl)

        sub_lbl = QLabel(
            'These .lrc files exist in the lyrics folder but have no matching song '
            'in the current library. Select the ones you want to delete.'
        )
        sub_lbl.setStyleSheet(f'color:{dim};font-size:12px;')
        sub_lbl.setWordWrap(True)
        v.addWidget(sub_lbl)

        lst = QListWidget()
        lst.setStyleSheet(
            f'QListWidget{{background:{bg_card};border:1px solid {border};border-radius:6px;'
            f'color:{text_c};font-size:12px;}}'
            f'QListWidget::item{{padding:4px 8px;}}'
        )
        for path in orphans:
            item = QListWidgetItem(os.path.basename(path))
            item.setData(Qt.UserRole, path)
            item.setToolTip(path)
            item.setCheckState(Qt.Checked)
            lst.addItem(item)
        v.addWidget(lst, 1)

        # Select-all / none toggle row
        sel_row = QHBoxLayout()
        sel_row.setSpacing(8)

        def _check_all():
            for i in range(lst.count()):
                lst.item(i).setCheckState(Qt.Checked)

        def _check_none():
            for i in range(lst.count()):
                lst.item(i).setCheckState(Qt.Unchecked)

        btn_all  = QPushButton('Select All')
        btn_none = QPushButton('Select None')
        for b in (btn_all, btn_none):
            b.setStyleSheet(
                f'QPushButton{{background:transparent;color:{accent};border:1px solid {border};'
                f'border-radius:5px;padding:4px 12px;font-size:11px;}}'
                f'QPushButton:hover{{border:1px solid {accent};}}'
            )
        btn_all.clicked.connect(_check_all)
        btn_none.clicked.connect(_check_none)
        sel_row.addWidget(btn_all)
        sel_row.addWidget(btn_none)
        sel_row.addStretch(1)
        v.addLayout(sel_row)

        # Action buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch(1)

        btn_cancel = QPushButton('Cancel')
        btn_cancel.setStyleSheet(
            f'QPushButton{{background:transparent;color:{dim};border:1px solid {border};'
            f'border-radius:6px;padding:7px 20px;font-size:13px;}}'
        )
        btn_cancel.clicked.connect(dlg.reject)

        btn_delete = QPushButton('Delete Selected')
        btn_delete.setStyleSheet(
            f'QPushButton{{background:{red};color:white;border:none;border-radius:6px;'
            f'padding:7px 20px;font-size:13px;font-weight:600;}}'
        )

        def _do_delete():
            targets = [
                lst.item(i).data(Qt.UserRole)
                for i in range(lst.count())
                if lst.item(i).checkState() == Qt.Checked
            ]
            if not targets:
                QMessageBox.information(dlg, 'Delete Orphan Lyrics', 'No files selected.')
                return
            removed = 0
            for path in targets:
                try:
                    os.remove(path)
                    removed += 1
                except OSError:
                    pass
            # Re-scan lyrics so orphan list and stats are up to date.
            self._lyrics_store.scan(self._songs)
            for p in self._songs:
                self._refresh_song_lyrics_cell(p)
            self._update_stats()
            self._status_lbl.setText(f'Deleted {removed} orphan lyrics file(s).')
            dlg.accept()

        btn_delete.clicked.connect(_do_delete)

        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_delete)
        v.addLayout(btn_row)

        dlg.exec_()

    def _on_search_changed(self, text: str):
        self._search_query = text.strip().lower()
        self._apply_song_filter()

    def _song_matches_search(self, song: dict) -> bool:
        q = self._search_query
        if not q:
            return True
        return (
            q in song.get('filename', '').lower()
            or q in song.get('title', '').lower()
            or q in song.get('artist', '').lower()
            or q in song.get('album', '').lower()
        )

    def _song_matches_filter(self, path: str, song: dict) -> bool:
        is_ghost = path in self._ghost_paths
        mode = self._active_song_filter
        if mode == 'missing_file':
            if not is_ghost:
                return False
            return self._song_matches_search(song)
        if is_ghost:
            return False
        if not self._song_matches_search(song):
            return False
        if mode == 'all':
            return True
        if mode == 'no_cover':
            return not song.get('has_cover')
        if mode == 'no_meta':
            return not (song.get('title') and song.get('artist'))
        if mode == 'no_tags':
            return not self._playlist_store.get_tags(path)
        if mode == 'mismatch':
            return bool(self._playlist_issues_by_path.get(path))
        if mode == 'no_lyrics':
            return not self._lyrics_store.has_lyrics(path)
        if mode == 'typo_errors':
            return has_typo_error(song.get('filename') or song.get('title') or path)
        return True

    def _apply_song_filter(self):
        for fm, row in self._stat_rows_by_filter.items():
            row.set_active(fm == self._active_song_filter)

        if hasattr(self, '_filter_title_lbl'):
            self._filter_title_lbl.setText(self._filter_display_name())

        # Fast path: no filter active and no search → ensure everything is
        # visible without touching every row (avoids the O(n) setRowHidden
        # loop on large libraries with 3000+ songs).
        if self._active_song_filter == 'all' and not self._search_query:
            self._table.setUpdatesEnabled(False)
            try:
                for row in range(self._table.rowCount()):
                    if self._table.isRowHidden(row):
                        self._table.setRowHidden(row, False)
            finally:
                self._table.setUpdatesEnabled(True)
            for item in self._path_to_grid_item.values():
                if item.isHidden():
                    item.setHidden(False)
            self._schedule_thumb_refresh()
            self._update_remove_missing_btn()
            self._update_sidebar_visibility()
            return

        self._table.setUpdatesEnabled(False)
        try:
            for row in range(self._table.rowCount()):
                item = self._table.item(row, COL_COVER)
                if not item:
                    continue
                path = item.data(Qt.UserRole)
                song = self._songs.get(path, {})
                self._table.setRowHidden(row, not self._song_matches_filter(path, song))
        finally:
            self._table.setUpdatesEnabled(True)

        self._grid.setUpdatesEnabled(False)
        try:
            for path, item in self._path_to_grid_item.items():
                song = self._songs.get(path, {})
                item.setHidden(not self._song_matches_filter(path, song))
        finally:
            self._grid.setUpdatesEnabled(True)

        self._schedule_thumb_refresh()
        self._update_remove_missing_btn()
        self._update_sidebar_visibility()

    _FILTER_TO_GROUPS = {
        'all':          {'covers', 'meta', 'tags', 'lyrics'},
        'no_cover':     {'covers'},
        'no_meta':      {'meta'},
        'typo_errors':  {'meta'},
        'no_tags':      {'tags'},
        'mismatch':     {'tags'},
        'missing_file': {'tags'},
        'no_lyrics':    {'lyrics'},
    }

    def _update_sidebar_visibility(self):
        """Show only the action group(s) relevant to the active filter."""
        visible = self._FILTER_TO_GROUPS.get(
            self._active_song_filter,
            {'covers', 'meta', 'tags', 'lyrics'},
        )
        self._grp_covers.setVisible('covers' in visible)
        self._grp_meta.setVisible('meta' in visible)
        self._grp_tags.setVisible('tags' in visible)
        self._grp_lyrics.setVisible('lyrics' in visible)

    def _update_remove_missing_btn(self):
        """Show 'Remove from JSON' only when Missing filter is active and ghost rows are selected."""
        if self._active_song_filter != 'missing_file':
            self._btn_remove_missing.setVisible(False)
            return
        selected = [p for p in self._selected_paths() if p in self._ghost_paths]
        self._btn_remove_missing.setVisible(True)
        self._btn_remove_missing.setEnabled(bool(selected))

    def _selected_paths(self) -> list[str]:
        if self._stack.currentIndex() == 1:   # grid view
            return [
                item.data(Qt.UserRole)
                for item in self._grid.selectedItems()
                if item.data(Qt.UserRole)
            ]
        # table view
        rows = {idx.row() for idx in self._table.selectedIndexes()}
        paths = []
        for row in sorted(rows):
            item = self._table.item(row, COL_COVER)
            if item:
                p = item.data(Qt.UserRole)
                if p:
                    paths.append(p)
        return paths

    def _update_stats(self):
        total = sum(1 for p in self._songs if p not in self._ghost_paths)
        with_c = sum(1 for p, s in self._songs.items() if s.get('has_cover') and p not in self._ghost_paths)
        missing = total - with_c
        no_metadata = sum(
            1 for p, s in self._songs.items()
            if p not in self._ghost_paths and not (s.get('title') and s.get('artist'))
        )
        # Typo errors: walk every entry (real + ghost) because doubled-
        # extension typos like 'Foo.mp3.mp3' show up in JSON titles too.
        typo_errors = sum(
            1 for p, s in self._songs.items()
            if has_typo_error(s.get('filename') or s.get('title') or p)
        )
        missing_tags = sum(
            1 for path in self._songs
            if path not in self._ghost_paths and not self._playlist_store.get_tags(path)
        )
        mismatch_tags = len(self._playlist_store.invalid_tag_records)
        missing_file = len(self._ghost_paths)
        no_lyrics = sum(
            1 for path in self._songs
            if path not in self._ghost_paths and not self._lyrics_store.has_lyrics(path)
        )

        def _set(filter_mode, value):
            row = self._stat_rows_by_filter.get(filter_mode)
            if row is not None:
                row.set_value(value)

        lyrics_orphan = len(self._lyrics_store.orphan_lrc_files)

        _set('all',           total)
        _set('no_cover',      missing)
        _set('no_meta',       no_metadata)
        _set('typo_errors',   typo_errors)
        _set('no_tags',       missing_tags)
        _set('mismatch',      mismatch_tags)
        _set('missing_file',  missing_file)
        _set('no_lyrics',     no_lyrics)
        _set('lyrics_orphan', lyrics_orphan)

        self._btn_fix_invalid_tags.setEnabled(mismatch_tags > 0)
        self._apply_song_filter()

    def _compose_library_status(self) -> str:
        real_total = len(self._songs) - len(self._ghost_paths)
        with_cover  = sum(1 for p, s in self._songs.items() if s.get('has_cover') and p not in self._ghost_paths)
        without_cover = real_total - with_cover

        scan_part = (
            f'{real_total} songs ({with_cover} covered, {without_cover} no cover)'
        )

        store = self._playlist_store
        record_count = len(store.records)
        if record_count == 0:
            return scan_part

        # ghost paths are synthetic keys injected by _sync_ghost_rows; exclude them
        matched = sum(1 for p in store._path_to_record if p not in self._ghost_paths)
        missing_count = len(self._ghost_paths)
        invalid_count = len(store.invalid_tag_records)

        json_bits = [f'{matched}/{record_count} matched']
        if missing_count:
            json_bits.append(f'{missing_count} file(s) missing')
        if invalid_count:
            json_bits.append(f'{invalid_count} invalid tag(s)')
        if not missing_count and not invalid_count:
            json_bits.append('all JSON entries matched')

        status = f'{scan_part} — JSON: ' + ', '.join(json_bits)
        if invalid_count:
            status += '  (click the Mismatch card for details)'
        if missing_count:
            status += '  (click the Missing card to review)'
        return status

    def _on_clear_meta(self):
        """Remove title, artist and album tags from selected (or all) songs."""
        selected = self._selected_paths()
        targets = (
            [self._songs[p] for p in selected if p in self._songs]
            if selected else list(self._songs.values())
        )
        if not targets:
            QMessageBox.information(self, 'Clear Metadata', 'No songs to process.')
            return

        names = '\n'.join(
            f'  • {s.get("title") or s["filename"]}' for s in targets[:8]
        )
        if len(targets) > 8:
            names += f'\n  … and {len(targets) - 8} more'

        reply = QMessageBox.question(
            self, 'Clear Metadata',
            f'Remove title, artist and album tags from {len(targets)} song(s)?\n\n{names}',
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        done = 0
        touched_paths: list[str] = []
        playlist_dirty = False
        for song in targets:
            path = song['path']
            if clear_metadata(path):
                self._songs[path]['title']  = ''
                self._songs[path]['artist'] = ''
                self._songs[path]['album']  = ''
                for item in (
                    self._path_to_title_item.get(path),
                    self._path_to_artist_item.get(path),
                    self._path_to_album_item.get(path),
                ):
                    if item:
                        item.setText('')
                self._refresh_grid_text(path)
                playlist_dirty = self._playlist_store.sync_song(self._songs[path]) or playlist_dirty
                touched_paths.append(path)
                done += 1
        # Clearing metadata only touches the audio file; no JSON entry is
        # required for this operation either.
        if playlist_dirty:
            self._save_playlist_store()
        self._update_stats()
        self._status_lbl.setText(f'Cleared tags from {done} song(s).')

    def _set_buttons_enabled(self, enabled: bool):
        for btn in (
            self._btn_auto, self._btn_specific, self._btn_delete,
            self._btn_fix_auto, self._btn_fix_manual, self._btn_clear_meta,
        ):
            btn.setEnabled(enabled)
        self._update_playlist_action_state(base_enabled=enabled)

    def _update_playlist_action_state(self, base_enabled: bool | None = None):
        if base_enabled is None:
            base_enabled = self._btn_auto.isEnabled()
        playlist_ready = base_enabled and bool(self._songs) and not self._playlist_reconcile_pending
        self._btn_playlist_assign.setEnabled(playlist_ready)
        self._btn_playlist_report.setEnabled(playlist_ready)

    def _cancel_workers(self):
        for w in [self._scan_worker, self._thumb_worker, self._assign_worker]:
            if w and w.isRunning():
                w.cancel()
                w.wait(300)

    def _fit_button_text(self, btn: QPushButton, min_px: int = 9, max_px: int = 22):
        text = btn.text().strip()
        if not text:
            return
        rect = btn.contentsRect()
        avail = rect.width() - (btn.iconSize().width() + 18 if not btn.icon().isNull() else 0)
        avail = max(avail, 24)
        f = QFont(btn.font())
        for px in range(max_px, min_px - 1, -1):
            f.setPixelSize(px)
            if QFontMetrics(f).horizontalAdvance(text) <= avail:
                btn.setFont(f)
                return
        f.setPixelSize(min_px)
        btn.setFont(f)

    def _update_action_button_layout(self, width: int):
        fs = max(10, min(22, int(10 + (width - 960) * 12 / 640)))
        icon_sz = max(16, min(28, int(16 + (width - 960) * 12 / 640)))
        for btn in (
            self._btn_auto, self._btn_specific, self._btn_delete,
            self._btn_fix_auto, self._btn_fix_manual, self._btn_clear_meta,
            self._btn_playlist_assign, self._btn_playlist_report,
        ):
            btn.setIconSize(QSize(icon_sz, icon_sz))
            f = btn.font()
            f.setPixelSize(fs)
            btn.setFont(f)
        for btn in (
            self._btn_auto, self._btn_specific, self._btn_delete,
            self._btn_fix_auto, self._btn_fix_manual, self._btn_clear_meta,
            self._btn_playlist_assign, self._btn_playlist_report,
        ):
            self._fit_button_text(btn)

    def _schedule_action_button_layout(self):
        if self._action_layout_update_pending:
            return
        self._action_layout_update_pending = True

        def _apply():
            self._action_layout_update_pending = False
            self._update_action_button_layout(self.width())

        QTimer.singleShot(0, _apply)

    def resizeEvent(self, event):
        """Scale action-bar button font continuously with window width."""
        super().resizeEvent(event)
        self._schedule_action_button_layout()

    def closeEvent(self, event):
        self._cancel_workers()
        if self._reconcile_worker and self._reconcile_worker.isRunning():
            self._reconcile_worker.wait(500)
        self._player_bar.stop()
        super().closeEvent(event)


# ── Filename parser ───────────────────────────────────────────────────────────

def parse_filename(filename: str) -> tuple:
    """
    Extract (artist, title, album) from 'Artist - Song Title (Album).mp3'.
    • Text before the first ' - ' -> artist.
    • First parenthetical in the title part -> album.
    • Title after removing parenthetical / bracket content.
    • Leading track numbers ('01. ', '2 ') stripped.
    Returns ('', stem, '') when no ' - ' separator is found.
    The actual file on disk is NEVER modified.
    """
    stem = Path(filename).stem
    stem = re.sub(r'^\d+[.\s]+', '', stem).strip()

    if ' - ' in stem:
        artist_raw, title_raw = stem.split(' - ', 1)
    else:
        artist_raw, title_raw = '', stem

    # Extract album from the first parenthetical in the title part
    album = ''
    m = re.search(r'\(([^)]+)\)', title_raw)
    if m:
        album = m.group(1).strip()

    def _clean(s: str) -> str:
        s = re.sub(r'\([^)]*\)', '', s)
        s = re.sub(r'\[[^\]]*\]', '', s)
        return ' '.join(s.split())

    return _clean(artist_raw.strip()), _clean(title_raw.strip()), album


# ── Typo error detector ───────────────────────────────────────────────────────

_AUDIO_EXTS_LOWER = {ext.lower() for ext in SUPPORTED_EXTENSIONS}


def has_typo_error(name: str) -> bool:
    """True when a filename / JSON title ends with a duplicated audio
    extension (e.g. ``Foo.mp3.mp3`` or ``Bar.flac.mp3``). Catches the most
    common rename / drag-and-drop fumble where the original extension was
    left in place when a new one was appended."""
    if not name:
        return False
    parts = name.lower().rsplit('.', 2)
    if len(parts) < 3:
        return False
    return (f'.{parts[-2]}' in _AUDIO_EXTS_LOWER
            and f'.{parts[-1]}' in _AUDIO_EXTS_LOWER)


# ── Pixmap helpers ────────────────────────────────────────────────────────────

def _make_placeholder(has_cover: bool, size: int, theme: dict | None = None) -> QPixmap:
    if theme:
        bg = theme['cover_has'] if has_cover else theme['cover_missing']
    else:
        bg = '#059669' if has_cover else '#2d2d4e'
    sym = '♫' if has_cover else '—'
    px  = QPixmap(size, size)
    px.fill(QColor(bg))
    painter = QPainter(px)
    painter.setPen(QColor('#ffffff'))
    painter.setFont(QFont('Segoe UI', max(8, size // 3)))
    painter.drawText(px.rect(), Qt.AlignCenter, sym)
    painter.end()
    return px


def _fmt_size(size_bytes: int) -> str:
    if size_bytes >= 1_048_576:
        return f'{size_bytes / 1_048_576:.1f} MB'
    return f'{max(size_bytes // 1024, 1)} KB'


def _load_pixmap(data: bytes, size: int) -> QPixmap | None:
    return load_cropped_pixmap(data, size)


def _pixmap_from_rgba(data: bytes, size: int) -> QPixmap | None:
    """Wrap pre-decoded raw RGBA bytes (size×size×4) into a QPixmap.  No decode work."""
    img = QImage(data, size, size, size * 4, QImage.Format_RGBA8888)
    if img.isNull():
        return None
    return QPixmap.fromImage(img)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setApplicationName('SongSyncer')

    # Load saved theme
    settings = QSettings('SongSyncer', 'SongSyncer')
    theme_name = settings.value('theme', 'light')
    theme = THEMES.get(theme_name, LIGHT)
    app.setStyleSheet(build_stylesheet(theme))

    window = MainWindow()
    window.showMaximized()

    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
