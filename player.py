"""
player.py – Slide-up overlay music player bar for SongSyncer.

The bar animates from height 0 to PLAYER_HEIGHT when a song is played.
Layout is a single row: cover art | song info | progress | controls.

Audio backends (tried in order):
  1. PyQt5.QtMultimedia  (if installed)
  2. pygame.mixer        (pip install pygame)
"""

import os
import shutil
import subprocess
import importlib.util

from PyQt5.QtWidgets import (
    QFrame, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QSlider, QSizePolicy, QWidget,
)
from PyQt5.QtCore import (
    Qt, pyqtSignal, QPropertyAnimation, QEasingCurve, QUrl, QSize, QTimer,
)
from PyQt5.QtGui import QPixmap, QFont, QColor

from icons import get_icon
from image_utils import load_cropped_pixmap

# ── Audio backend detection ──────────────────────────────────────────────────

os.environ.setdefault('PYGAME_HIDE_SUPPORT_PROMPT', '1')


def _has_module(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _pick_backend() -> str | None:
    force_backend = os.environ.get('SONGSYNCER_AUDIO_BACKEND', '').strip().lower()
    if force_backend in {'none', 'off', 'disabled'}:
        return None
    if force_backend in {'qt', 'pygame'}:
        return force_backend

    is_wayland = (
        os.environ.get('XDG_SESSION_TYPE', '').lower() == 'wayland'
        or bool(os.environ.get('WAYLAND_DISPLAY'))
    )
    if is_wayland:
        if _has_module('pygame'):
            return 'pygame'
        if _has_module('PyQt5.QtMultimedia'):
            return 'qt'
        return None
    if _has_module('PyQt5.QtMultimedia'):
        return 'qt'
    if _has_module('pygame'):
        return 'pygame'
    return None

# Wayland audio backends can destabilize some environments. Keep playback
# disabled by default there; users can opt in with SONGSYNCER_AUDIO_BACKEND.
_BACKEND = _pick_backend()

PLAYER_HEIGHT = 200


class PlayerBar(QFrame):
    """Horizontal player bar that slides up from the bottom of the window."""

    prev_requested = pyqtSignal()
    next_requested = pyqtSignal()
    shuffle_toggled = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('playerBar')
        self._current_path: str = ''
        self._duration_ms: int = 0
        self._seeking = False
        self._shuffle: bool = False
        self._position_ms: int = 0
        self._pending_play: bool = False

        self.setMaximumHeight(0)
        self.setMinimumHeight(0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        # animation
        self._anim = QPropertyAnimation(self, b'maximumHeight')
        self._anim.setDuration(320)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

        # audio backend
        self._backend = _BACKEND
        self._audio_failed = False
        self._qt_player = None
        self._qmedia_player_cls = None
        self._qmedia_content_cls = None
        self._pygame = None
        self._pygame_inited = False
        self._pg_playing = False
        self._pg_paused = False
        self._pg_timer = QTimer(self)
        self._pg_timer.setInterval(250)
        self._pg_timer.timeout.connect(self._pg_tick)
        # Subprocess fallback for formats Qt's GStreamer backend can't decode
        # locally (most commonly M4A/AAC on Linux without the "bad" plugins).
        # We spawn ffplay or mpv in the background and kill it on stop.
        self._subproc: subprocess.Popen | None = None
        # Cache the resolved external-player command once. None → none found.
        self._ext_player_cmd: list[str] | None = None

        self._build_ui()
        QTimer.singleShot(0, self._ensure_audio_backend)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(32, 18, 32, 18)
        layout.setSpacing(24)

        # cover art
        self._cover = QLabel()
        self._cover.setFixedSize(150, 150)
        self._cover.setAlignment(Qt.AlignCenter)
        self._cover.setStyleSheet(
            'background: #1a1a2e; border-radius: 12px; font-size: 40px;'
        )
        self._cover.setText('\u266b')
        layout.addWidget(self._cover)

        # song info
        info = QVBoxLayout()
        info.setSpacing(4)
        info.setContentsMargins(0, 0, 0, 0)

        self._title_lbl = QLabel('No song playing')
        self._title_lbl.setObjectName('playerTitle')
        self._title_lbl.setFont(QFont('Segoe UI', 18, QFont.DemiBold))

        self._artist_lbl = QLabel('')
        self._artist_lbl.setObjectName('playerArtist')

        self._album_lbl = QLabel('')
        self._album_lbl.setObjectName('playerAlbum')

        info.addStretch()
        info.addWidget(self._title_lbl)
        info.addWidget(self._artist_lbl)
        info.addWidget(self._album_lbl)
        info.addStretch()

        info_w = QWidget()
        info_w.setLayout(info)
        info_w.setFixedWidth(360)
        layout.addWidget(info_w)

        # progress slider
        progress_col = QVBoxLayout()
        progress_col.setSpacing(6)
        progress_col.setContentsMargins(0, 0, 0, 0)

        self._progress = QSlider(Qt.Horizontal)
        self._progress.setObjectName('playerProgress')
        self._progress.setRange(0, 1000)
        self._progress.sliderPressed.connect(self._on_seek_start)
        self._progress.sliderReleased.connect(self._on_seek_end)
        self._progress.sliderMoved.connect(self._on_seek_move)

        time_row = QHBoxLayout()
        self._time_cur = QLabel('0:00')
        self._time_cur.setObjectName('playerTime')
        self._time_tot = QLabel('0:00')
        self._time_tot.setObjectName('playerTime')
        time_row.addWidget(self._time_cur)
        time_row.addStretch()
        time_row.addWidget(self._time_tot)

        progress_col.addStretch()
        progress_col.addWidget(self._progress)
        progress_col.addLayout(time_row)
        progress_col.addStretch()
        layout.addLayout(progress_col, 1)

        # playback controls
        ctrl = QHBoxLayout()
        ctrl.setSpacing(10)

        self._btn_prev = QPushButton()
        self._btn_prev.setObjectName('playerBtn')
        self._btn_prev.setIcon(get_icon('skip_back', 32, '#e2e8f0'))
        self._btn_prev.setIconSize(QSize(32, 32))
        self._btn_prev.setFixedSize(56, 56)
        self._btn_prev.setCursor(Qt.PointingHandCursor)
        self._btn_prev.clicked.connect(self.prev_requested)

        self._btn_play = QPushButton()
        self._btn_play.setObjectName('playerPlayBtn')
        self._btn_play.setIcon(get_icon('play', 38, '#ffffff'))
        self._btn_play.setIconSize(QSize(38, 38))
        self._btn_play.setFixedSize(72, 72)
        self._btn_play.setCursor(Qt.PointingHandCursor)
        self._btn_play.clicked.connect(self._toggle_play)

        self._btn_next = QPushButton()
        self._btn_next.setObjectName('playerBtn')
        self._btn_next.setIcon(get_icon('skip_forward', 32, '#e2e8f0'))
        self._btn_next.setIconSize(QSize(32, 32))
        self._btn_next.setFixedSize(56, 56)
        self._btn_next.setCursor(Qt.PointingHandCursor)
        self._btn_next.clicked.connect(self.next_requested)

        self._btn_shuffle = QPushButton()
        self._btn_shuffle.setObjectName('playerBtn')
        self._btn_shuffle.setIcon(get_icon('shuffle', 28, '#64748b'))
        self._btn_shuffle.setIconSize(QSize(28, 28))
        self._btn_shuffle.setFixedSize(52, 52)
        self._btn_shuffle.setCursor(Qt.PointingHandCursor)
        self._btn_shuffle.setToolTip('Shuffle')
        self._btn_shuffle.setCheckable(True)
        self._btn_shuffle.clicked.connect(self._on_shuffle_toggle)

        ctrl.addWidget(self._btn_prev)
        ctrl.addWidget(self._btn_play)
        ctrl.addWidget(self._btn_next)
        ctrl.addWidget(self._btn_shuffle)
        layout.addLayout(ctrl)

        # volume slider
        self._vol = QSlider(Qt.Horizontal)
        self._vol.setObjectName('playerVolume')
        self._vol.setRange(0, 100)
        self._vol.setValue(80)
        self._vol.setFixedWidth(150)
        self._vol.valueChanged.connect(self._on_volume)
        layout.addWidget(self._vol)

        # set initial volume
        if self._qt_player:
            self._qt_player.setVolume(80)
        elif self._pygame_inited:
            self._pygame.mixer.music.set_volume(0.8)

    def _ensure_audio_backend(self) -> bool:
        """Lazy-init audio backend; never raise into the GUI event loop."""
        if self._audio_failed:
            self._btn_play.setEnabled(False)
            self._btn_play.setToolTip('Audio playback unavailable in this session')
            return False
        if self._qt_player or self._pygame_inited:
            return True

        if self._backend == 'qt':
            try:
                from PyQt5.QtMultimedia import QMediaPlayer, QMediaContent
                self._qmedia_player_cls = QMediaPlayer
                self._qmedia_content_cls = QMediaContent
                self._qt_player = QMediaPlayer(self)
                self._qt_player.positionChanged.connect(self._on_position)
                self._qt_player.durationChanged.connect(self._on_duration)
                self._qt_player.stateChanged.connect(self._on_state)
                self._qt_player.mediaStatusChanged.connect(self._on_media_status)
                self._qt_player.error.connect(self._on_error)
                self._qt_player.setVolume(self._vol.value())
                return True
            except Exception as exc:
                print(f'[PlayerBar] Qt backend init failed: {exc}')
                # Fallback to pygame if available.
                self._backend = 'pygame' if _has_module('pygame') else None

        if self._backend == 'pygame':
            try:
                import pygame
                self._pygame = pygame
                self._pygame.mixer.init(
                    frequency=44100, size=-16, channels=2, buffer=2048
                )
                self._pygame_inited = True
                self._pygame.mixer.music.set_volume(self._vol.value() / 100.0)
                return True
            except Exception as exc:
                print(f'[PlayerBar] pygame backend init failed: {exc}')
                self._backend = None
                self._audio_failed = True
                self._btn_play.setEnabled(False)
                self._btn_play.setToolTip('Audio playback unavailable in this session')
                return False

        self._audio_failed = True
        self._btn_play.setEnabled(False)
        self._btn_play.setToolTip('Audio playback unavailable in this session')
        return False

    # ── public API ────────────────────────────────────────────────────────────

    def play_song(self, path: str, title: str, artist: str, album: str,
                  cover_data: bytes = b'', duration_ms: int = 0):
        """Load and play a song, showing the player bar."""
        self._ensure_audio_backend()
        self._current_path = path
        self._title_lbl.setText(title or 'Unknown')
        self._artist_lbl.setText(artist)
        self._album_lbl.setText(album)

        # cover art
        if cover_data:
            px = load_cropped_pixmap(cover_data, 150)
            if px:
                self._cover.setPixmap(px)
                self._cover.setText('')
            else:
                self._cover.setText('\u266b')
                self._cover.setPixmap(QPixmap())
        else:
            self._cover.setText('\u266b')
            self._cover.setPixmap(QPixmap())

        self._duration_ms = duration_ms

        # Always stop any subprocess fallback from a previous file.
        self._stop_subproc()

        # Hand M4A/AAC straight to the external player — Qt's GStreamer
        # backend on Linux frequently can't decode them without extra plugins,
        # and we'd rather have audio than nothing.
        ext = os.path.splitext(path)[1].lower()
        if ext in ('.m4a', '.aac', '.mp4'):
            if self._play_external(path):
                self._show_bar()
                return
            # Fall through to Qt/pygame even though M4A usually fails — the
            # error will at least surface in the status console.

        # play audio
        if self._qt_player:
            url = QUrl.fromLocalFile(path)
            self._pending_play = True
            self._qt_player.setMedia(self._qmedia_content_cls(url))
            self._qt_player.play()
            # fallback: if play() didn't start within 500ms, retry
            QTimer.singleShot(500, self._retry_play)
        elif self._pygame_inited:
            self._pg_play(path)

        # slide up
        self._show_bar()

    def stop(self):
        self._stop_subproc()
        if self._qt_player:
            self._qt_player.stop()
        elif self._pygame_inited:
            self._pg_stop()
        self._hide_bar()

    def current_path(self) -> str:
        return self._current_path

    def set_shuffle(self, on: bool):
        self._shuffle = on
        self._btn_shuffle.setChecked(on)
        self._update_shuffle_icon()

    def is_shuffle(self) -> bool:
        return self._shuffle

    def update_icon_colors(self, text_color: str, accent_color: str):
        self._btn_prev.setIcon(get_icon('skip_back', 32, text_color))
        self._btn_play.setIcon(
            get_icon('pause' if self._is_playing() else 'play', 38, '#ffffff')
        )
        self._btn_next.setIcon(get_icon('skip_forward', 32, text_color))
        self._update_shuffle_icon()

    # ── pygame backend ────────────────────────────────────────────────────────

    def _pg_play(self, path: str):
        if not self._pygame_inited and not self._ensure_audio_backend():
            return
        try:
            self._pygame.mixer.music.load(path)
            self._pygame.mixer.music.play()
            self._pg_playing = True
            self._pg_paused = False
            self._position_ms = 0
            if not self._duration_ms:
                self._duration_ms = self._get_duration_ms(path)
            self._time_tot.setText(_fmt_time(self._duration_ms))
            self._pg_timer.start()
            self._btn_play.setIcon(get_icon('pause', 38, '#ffffff'))
        except Exception as e:
            print(f'[PlayerBar] pygame error: {e}')

    def _pg_stop(self):
        if not self._pygame_inited:
            return
        self._pygame.mixer.music.stop()
        self._pg_playing = False
        self._pg_paused = False
        self._pg_timer.stop()
        self._btn_play.setIcon(get_icon('play', 38, '#ffffff'))

    # ── external player fallback (M4A / AAC) ──────────────────────────────────

    def _find_ext_player_cmd(self) -> list[str] | None:
        """Locate ffplay / mpv / paplay in PATH and return a base command
        list. Cached on first call. Returns None if no external player is
        available — the caller should give up and let Qt try (and fail)."""
        if self._ext_player_cmd is not None:
            return self._ext_player_cmd or None
        for cmd, args in (
            ('ffplay', ['-nodisp', '-autoexit', '-loglevel', 'quiet']),
            ('mpv',    ['--no-video', '--really-quiet']),
        ):
            exe = shutil.which(cmd)
            if exe:
                self._ext_player_cmd = [exe, *args]
                return list(self._ext_player_cmd)
        self._ext_player_cmd = []   # cache the negative result
        return None

    def _play_external(self, path: str) -> bool:
        """Spawn ffplay/mpv as a subprocess to play `path`. Returns True on
        success (process started); False if no external player was found."""
        cmd = self._find_ext_player_cmd()
        if not cmd:
            print(f'[PlayerBar] no external player (ffplay/mpv) found; cannot play {path}')
            return False
        try:
            self._subproc = subprocess.Popen(
                [*cmd, path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
        except Exception as exc:
            print(f'[PlayerBar] external player failed to launch: {exc}')
            self._subproc = None
            return False
        # The external player does its own playback; we don't know the
        # position, so the progress bar stays at 0. Show the play icon
        # flipped to "playing" so the user gets visual feedback.
        self._btn_play.setIcon(get_icon('pause', 38, '#ffffff'))
        return True

    def _stop_subproc(self):
        """Kill any running external player. Safe to call when nothing is
        running."""
        if self._subproc is None:
            return
        try:
            self._subproc.terminate()
            self._subproc.wait(timeout=1.0)
        except Exception:
            try:
                self._subproc.kill()
            except Exception:
                pass
        self._subproc = None
        self._btn_play.setIcon(get_icon('play', 38, '#ffffff'))

    def _pg_tick(self):
        """Called every 250ms while pygame is playing."""
        if not self._pygame_inited:
            return
        if not self._pygame.mixer.music.get_busy() and not self._pg_paused:
            # song ended
            self._pg_playing = False
            self._pg_timer.stop()
            self._btn_play.setIcon(get_icon('play', 38, '#ffffff'))
            self.next_requested.emit()
            return
        if self._pg_paused:
            return
        self._position_ms += 250
        if not self._seeking and self._duration_ms > 0:
            self._progress.setValue(int(self._position_ms * 1000 / self._duration_ms))
        self._time_cur.setText(_fmt_time(self._position_ms))

    @staticmethod
    def _get_duration_ms(path: str) -> int:
        try:
            from mutagen import File as MutagenFile
            audio = MutagenFile(path)
            if audio and audio.info:
                return int(audio.info.length * 1000)
        except Exception:
            pass
        return 0

    # ── animation ─────────────────────────────────────────────────────────────

    def _show_bar(self):
        if self.maximumHeight() == PLAYER_HEIGHT:
            return
        self._anim.stop()
        self._anim.setStartValue(self.maximumHeight())
        self._anim.setEndValue(PLAYER_HEIGHT)
        self._anim.start()

    def _hide_bar(self):
        self._anim.stop()
        self._anim.setStartValue(self.maximumHeight())
        self._anim.setEndValue(0)
        self._anim.start()

    # ── slots ─────────────────────────────────────────────────────────────────

    def _toggle_play(self):
        self._ensure_audio_backend()
        if self._qt_player:
            if self._qt_player.state() == self._qmedia_player_cls.PlayingState:
                self._qt_player.pause()
            elif self._current_path:
                # Works for both PausedState and StoppedState
                self._qt_player.play()
        elif self._pygame_inited:
            if self._pg_playing and not self._pg_paused:
                self._pygame.mixer.music.pause()
                self._pg_paused = True
                self._pg_timer.stop()
                self._btn_play.setIcon(get_icon('play', 38, '#ffffff'))
            elif self._pg_paused:
                self._pygame.mixer.music.unpause()
                self._pg_paused = False
                self._pg_timer.start()
                self._btn_play.setIcon(get_icon('pause', 38, '#ffffff'))
            elif self._current_path:
                self._pg_play(self._current_path)

    def _is_playing(self) -> bool:
        if self._qt_player:
            return self._qt_player.state() == self._qmedia_player_cls.PlayingState
        if self._pygame_inited:
            return self._pg_playing and not self._pg_paused
        return False

    def _on_position(self, pos_ms):
        if not self._seeking and self._duration_ms > 0:
            self._progress.setValue(int(pos_ms * 1000 / self._duration_ms))
        self._time_cur.setText(_fmt_time(pos_ms))

    def _on_duration(self, dur_ms):
        self._duration_ms = dur_ms
        self._time_tot.setText(_fmt_time(dur_ms))

    def _on_state(self, state):
        if self._qmedia_player_cls and state == self._qmedia_player_cls.PlayingState:
            self._pending_play = False
            self._btn_play.setIcon(get_icon('pause', 38, '#ffffff'))
        else:
            self._btn_play.setIcon(get_icon('play', 38, '#ffffff'))

    def _retry_play(self):
        """Fallback: if media loaded but play() didn't start, retry."""
        if (self._pending_play and self._qt_player
                and self._qmedia_player_cls
                and self._qt_player.state() != self._qmedia_player_cls.PlayingState):
            self._qt_player.play()

    def _on_media_status(self, status):
        if not self._qt_player or not self._qmedia_player_cls:
            return
        if status == self._qmedia_player_cls.EndOfMedia:
            self.next_requested.emit()
            return
        # auto-play when media finishes loading
        if self._pending_play and status in (
            self._qmedia_player_cls.LoadedMedia,
            self._qmedia_player_cls.BufferedMedia,
        ):
            self._pending_play = False
            if self._qt_player.state() != self._qmedia_player_cls.PlayingState:
                self._qt_player.play()

    def _on_error(self, error):
        if self._qt_player:
            msg = self._qt_player.errorString()
            if msg:
                print(f'[PlayerBar] media error: {msg}')

    def _on_seek_start(self):
        self._seeking = True

    def _on_seek_move(self, val):
        if self._duration_ms > 0:
            ms = int(val * self._duration_ms / 1000)
            self._time_cur.setText(_fmt_time(ms))

    def _on_seek_end(self):
        self._seeking = False
        if self._duration_ms > 0:
            ms = int(self._progress.value() * self._duration_ms / 1000)
            if self._qt_player:
                self._qt_player.setPosition(ms)
            elif self._pygame_inited:
                secs = ms / 1000.0
                self._pygame.mixer.music.play(start=secs)
                self._position_ms = ms
                if self._pg_paused:
                    self._pygame.mixer.music.pause()

    def _on_shuffle_toggle(self):
        self._shuffle = self._btn_shuffle.isChecked()
        self._update_shuffle_icon()
        self.shuffle_toggled.emit(self._shuffle)

    def _update_shuffle_icon(self):
        color = '#e2e8f0' if self._shuffle else '#64748b'
        self._btn_shuffle.setIcon(get_icon('shuffle', 28, color))

    def _on_volume(self, val):
        if self._qt_player:
            self._qt_player.setVolume(val)
        elif self._pygame_inited:
            self._pygame.mixer.music.set_volume(val / 100.0)


# ── helpers ───────────────────────────────────────────────────────────────────

def _fmt_time(ms: int) -> str:
    s = max(0, ms // 1000)
    return f'{s // 60}:{s % 60:02d}'
