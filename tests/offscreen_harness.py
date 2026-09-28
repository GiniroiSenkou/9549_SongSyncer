"""
Offscreen dev harness (not a unit test): builds a throw-away fixture library,
boots the real MainWindow with *isolated* QSettings (never touches your real
Music / Lyrics / JSON settings) and returns helpers for scripted checks and
screenshots.

    from tests.offscreen_harness import boot
    app, win, wait, ready, (music, lyrics, json_path) = boot(theme='light')
    win.grab().save('shot.png')

Run any scenario with:  QT_QPA_PLATFORM=offscreen .venv/bin/python your_script.py
"""
from __future__ import annotations

import itertools
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

_fixture_counter = itertools.count()

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJ not in sys.path:
    sys.path.insert(0, PROJ)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

_WORK = os.path.join(tempfile.gettempdir(), 'songsyncer-harness')


def _tone(path: str):
    """4 s tone + 2 s silence, via ffmpeg (falls back to a copy of Data if missing)."""
    if os.path.isfile(path):
        return
    ffmpeg = shutil.which('ffmpeg')
    if ffmpeg:
        subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi',
                        '-i', 'sine=frequency=440:duration=4', '-af', 'apad=pad_dur=2',
                        '-c:a', 'libmp3lame', '-q:a', '9', path], check=True)
    else:
        open(path, 'wb').write(b'\x00' * 4096)


def make_fixture(name: str | None = None):
    """Build a throw-away library under its own subdirectory.

    Defaults to a fresh, uniquely-named directory every call (rather than a
    fixed 'fixture' name) — a unittest run boots several MainWindow
    instances in one process, and a previous window's background threads
    (the reconcile worker, its debounce timer, thumbnail/lyrics workers)
    can outlive a plain ``.close()``; sharing one directory let a
    still-finishing window race a later test's fixture rewrite and read or
    write the wrong JSON mid-flight. A unique directory makes that
    impossible regardless of exact teardown timing.
    """
    if name is None:
        name = f'fixture-{next(_fixture_counter)}-{os.getpid()}'
    os.makedirs(_WORK, exist_ok=True)
    fx = os.path.join(_WORK, name)
    shutil.rmtree(fx, ignore_errors=True)
    music, lyrics, data = (os.path.join(fx, d) for d in ('music', 'lyrics', 'data'))
    for d in (music, lyrics, data):
        os.makedirs(d)
    tone = os.path.join(_WORK, 'tone_tail.mp3')
    _tone(tone)
    for n in ('AC DC - X.mp3', 'Artist - Song.mp3', 'Foo -Bar.mp3', 'Lo-Fi Beats.mp3'):
        shutil.copy(tone, os.path.join(music, n))
    open(os.path.join(lyrics, 'artist - song.lrc'), 'w').write('[00:01.00]hello\n')
    open(os.path.join(lyrics, 'Lo-Fi Beats.lrc'), 'w').write('[00:01.00]hello\n')
    jp = os.path.join(data, 'music.json')
    json.dump({'version': 2, 'songs': [
        {'title': 'AC dc - X.mp3', 'tags': ['heavy', 'driving']},
        {'title': 'AC DC - X.mp3', 'tags': []},
        {'title': 'Artist -Song.mp3', 'tags': ['chill', 'Bogus Tag']},
        {'title': 'Ghost.mp3', 'tags': ['ost']},
    ]}, open(jp, 'w'), indent=2)
    return music, lyrics, jp


def boot(theme: str = 'light', auto_lyrics: str = 'false', fixture=None, size=(1440, 900)):
    from PyQt5.QtCore import QSettings
    QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, os.path.join(_WORK, 'qsettings'))
    music, lyrics, jp = fixture or make_fixture()
    st = QSettings('SongSyncer', 'SongSyncer')
    st.clear()
    st.setValue('last_folder', music)
    st.setValue('playlist_json_path', jp)
    st.setValue('lyrics_folder', lyrics)
    st.setValue('theme', theme)
    st.setValue('lyrics_auto_fetch', auto_lyrics)
    st.sync()

    import main as appmod
    from PyQt5.QtWidgets import QApplication
    from style import build_stylesheet
    from themes import THEMES, LIGHT
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setStyleSheet(build_stylesheet(THEMES.get(theme, LIGHT)))
    win = appmod.MainWindow()
    win.resize(*size)
    win.show()

    def wait(cond=lambda: False, timeout: float = 15):
        t0 = time.time()
        while time.time() - t0 < timeout:
            app.processEvents()
            if cond():
                return True
            time.sleep(0.02)
        return False

    def ready():
        return (not win._is_scanning
                and win._playlist_reconcile_applied_seq == win._playlist_reconcile_seq
                and not win._playlist_reconcile_pending)

    wait(ready)
    return app, win, wait, ready, (music, lyrics, jp)
