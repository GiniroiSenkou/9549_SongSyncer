"""
workers.py – QThread workers for all background operations.
"""

import os
import queue
import time
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Optional

from PyQt5.QtCore import QThread, pyqtSignal

from cover_art import (
    get_metadata, has_cover, read_cover,
    write_cover, delete_cover, SUPPORTED_EXTENSIONS,
)
from image_utils import decode_cover_thumbnails
from lyrics_fetcher import fetch_from_lrclib
from lyrics_store import LyricsStore
from playlist_store import PlaylistAssignmentStore
from search import build_query, search_cover_art, download_image


# ── Folder scanner ────────────────────────────────────────────────────────────

class ScanWorker(QThread):
    """Walk a folder tree, emit one song dict per audio file found."""

    song_found    = pyqtSignal(dict)        # one song at a time
    progress      = pyqtSignal(int, str)    # (index, filename)
    scan_complete = pyqtSignal(int, int)    # (total, without_cover)
    timing_note   = pyqtSignal(str)         # debug timing messages

    def __init__(self, folder: str):
        super().__init__()
        self.folder = folder
        self._stop = False

    def cancel(self):
        self._stop = True

    def run(self):
        t_walk = time.perf_counter()
        audio_files: List[str] = []
        for root, _, files in os.walk(self.folder):
            for f in sorted(files):
                if Path(f).suffix.lower() in SUPPORTED_EXTENSIONS:
                    audio_files.append(os.path.join(root, f))
        walk_ms = (time.perf_counter() - t_walk) * 1000
        self.timing_note.emit(
            f"[scan] folder walk: {len(audio_files)} files found in {walk_ms:.0f} ms"
        )

        total = 0
        without = 0
        t_scan_start = time.perf_counter()
        SLOW_MS = 150  # flag individual files slower than this

        for i, path in enumerate(audio_files):
            if self._stop:
                break

            self.progress.emit(i + 1, os.path.basename(path))

            try:
                t_file = time.perf_counter()

                t0 = time.perf_counter()
                meta = get_metadata(path)
                t_meta = (time.perf_counter() - t0) * 1000

                t0 = time.perf_counter()
                has_art = has_cover(path)
                t_cover = (time.perf_counter() - t0) * 1000

                t0 = time.perf_counter()
                duration_ms = _get_duration_ms(path)
                t_dur = (time.perf_counter() - t0) * 1000

                file_ms = (time.perf_counter() - t_file) * 1000
                if file_ms > SLOW_MS:
                    self.timing_note.emit(
                        f"[scan] ⚠ slow file {file_ms:.0f} ms  "
                        f"(meta {t_meta:.0f}ms / cover {t_cover:.0f}ms / dur {t_dur:.0f}ms)  "
                        f"{os.path.basename(path)}"
                    )

                song = {
                    'path':        path,
                    'filename':    os.path.basename(path),
                    'title':       meta['title'],
                    'artist':      meta['artist'],
                    'album':       meta['album'],
                    'format':      Path(path).suffix.lower().lstrip('.').upper(),
                    'has_cover':   has_art,
                    'duration_ms': duration_ms,
                }

                total += 1
                if not has_art:
                    without += 1

                self.song_found.emit(song)
            except Exception as exc:
                self.timing_note.emit(f"[scan] ERROR {os.path.basename(path)}: {exc}")

        total_scan_ms = (time.perf_counter() - t_scan_start) * 1000
        avg_ms = total_scan_ms / total if total else 0
        self.timing_note.emit(
            f"[scan] done — {total} songs in {total_scan_ms:.0f} ms  "
            f"(avg {avg_ms:.0f} ms/file)"
        )
        self.scan_complete.emit(total, without)


# ── Thumbnail loader ──────────────────────────────────────────────────────────

class ThumbnailWorker(QThread):
    """Long-lived consumer that decodes cover thumbnails on demand.

    The main window pushes paths onto a shared queue as rows scroll into
    view.  The worker runs at LowPriority and emits pre-scaled raw RGBA
    pixel buffers so the main thread only has to wrap them in QImage —
    no PIL or Qt image decoding on the UI thread.
    """

    # (path, thumb_rgba, grid_rgba) — raw RGBA bytes, already cropped & scaled
    thumb_ready = pyqtSignal(str, bytes, bytes)

    def __init__(self, q: queue.Queue, thumb_size: int, grid_size: int):
        super().__init__()
        self.queue = q
        self.thumb_size = thumb_size
        self.grid_size = grid_size
        self._stop = False

    def cancel(self):
        self._stop = True

    def run(self):
        self.setPriority(QThread.LowPriority)
        while not self._stop:
            try:
                path = self.queue.get(timeout=0.25)
            except queue.Empty:
                continue
            if path is None:
                break
            raw = _safe_read_cover(path)
            if raw and not self._stop:
                result = decode_cover_thumbnails(raw, self.thumb_size, self.grid_size)
                if result and not self._stop:
                    self.thumb_ready.emit(path, result[0], result[1])
            time.sleep(0.005)  # yield 5 ms between items; keeps UI thread free


def _safe_read_cover(path: str) -> bytes | None:
    try:
        data, _ = read_cover(path)
        return data
    except Exception:
        return None


# ── Auto-assign worker ────────────────────────────────────────────────────────

class AutoAssignWorker(QThread):
    """Download and embed cover art for a list of songs."""

    song_done     = pyqtSignal(str, bytes, bool)  # (path, img_bytes, success)
    progress      = pyqtSignal(int, int, str)     # (current, total, song_title)
    finished      = pyqtSignal(int, int)          # (assigned, failed)

    def __init__(self, songs: list):
        super().__init__()
        self.songs = songs
        self._stop = False

    def cancel(self):
        self._stop = True

    def run(self):
        assigned = 0
        failed   = 0
        total    = len(self.songs)

        for i, song in enumerate(self.songs):
            if self._stop:
                break

            song_title = song.get('title') or song.get('filename') or Path(song['path']).name
            self.progress.emit(i + 1, total, song_title)

            if self._assign_song(song):
                assigned += 1
            else:
                self.song_done.emit(song['path'], b'', False)
                failed += 1

            # polite rate-limit between requests
            if i < total - 1 and not self._stop:
                time.sleep(0.35)

        self.finished.emit(assigned, failed)

    def _assign_song(self, song: dict) -> bool:
        """Try multiple queries/results for one song; return True once one works."""
        for query in _auto_query_candidates(song):
            if self._stop:
                return False
            results = search_cover_art(query, limit=6)
            if not results:
                continue

            for result in results:
                if self._stop:
                    return False
                for url in _result_urls(result):
                    img = download_image(url)
                    if not img:
                        continue
                    if write_cover(song['path'], img):
                        self.song_done.emit(song['path'], img, True)
                        return True
        return False


# ── Lyrics fetch worker ───────────────────────────────────────────────────────

class LyricsFetchWorker(QThread):
    """Download .lrc files from LRCLIB for a list of songs."""

    song_done = pyqtSignal(str, bool)         # (path, success)
    progress  = pyqtSignal(int, int, str)     # (current, total, song_title)
    finished  = pyqtSignal(int, int)          # (fetched, failed)

    def __init__(self, songs: list, lyrics_folder: str):
        super().__init__()
        self.songs = songs
        self.lyrics_folder = lyrics_folder
        self._stop = False
        # Local store instance used only for path math + atomic write.
        self._store = LyricsStore(lyrics_folder)

    def cancel(self):
        self._stop = True

    def run(self):
        fetched = 0
        failed  = 0
        total   = len(self.songs)

        for i, song in enumerate(self.songs):
            if self._stop:
                break

            path = song.get('path', '')
            title  = (song.get('title') or '').strip()
            artist = (song.get('artist') or '').strip()
            album  = (song.get('album') or '').strip()
            duration_ms = song.get('duration_ms', 0)

            # Fall back to splitting the filename on ' - ' if metadata is empty.
            if not title or not artist:
                stem = Path(song.get('filename', '')).stem
                if ' - ' in stem:
                    a, t = stem.split(' - ', 1)
                    if not artist:
                        artist = a.strip()
                    if not title:
                        title = t.strip()

            display = song.get('title') or song.get('filename') or path
            self.progress.emit(i + 1, total, display)

            # Skip if a .lrc already exists for this song.
            dest = self._store.lrc_path_for(path)
            if dest and os.path.isfile(dest):
                self.song_done.emit(path, True)
                fetched += 1
                continue

            ok = False
            try:
                text = fetch_from_lrclib(title, artist, album, duration_ms)
                if text:
                    self._store.save_text(path, text)
                    ok = True
            except Exception:
                ok = False

            if ok:
                fetched += 1
            else:
                failed += 1
            self.song_done.emit(path, ok)

            # Polite throttle between requests.
            if i < total - 1 and not self._stop:
                time.sleep(0.35)

        self.finished.emit(fetched, failed)


# ── Image-search worker ───────────────────────────────────────────────────────

class SearchWorker(QThread):
    """Search online for cover art candidates for a single song."""

    results_ready = pyqtSignal(list)   # list of result dicts
    error         = pyqtSignal(str)

    def __init__(self, query: str, limit: int = 6):
        super().__init__()
        self.query = query
        self.limit = limit

    def run(self):
        results = search_cover_art(self.query, self.limit)
        if results:
            self.results_ready.emit(results)
        else:
            self.error.emit(f'No results found for: {self.query}')


# ── Single image download worker ──────────────────────────────────────────────

class ImageDownloadWorker(QThread):
    """Download one image (used by the picker dialog)."""

    image_ready = pyqtSignal(int, bytes)  # (slot_index, data)
    error       = pyqtSignal(int, str)    # (slot_index, reason)

    def __init__(self, index: int, url: str):
        super().__init__()
        self.index = index
        self.url   = url

    def run(self):
        data = download_image(self.url)
        if data:
            self.image_ready.emit(self.index, data)
        else:
            self.error.emit(self.index, 'Download failed')


class PlaylistReconcileWorker(QThread):
    """Reconcile playlist JSON records against the current library off the UI thread."""

    result_ready = pyqtSignal(int, dict)  # (sequence, reconcile_result)
    error = pyqtSignal(int, str)          # (sequence, message)

    def __init__(
        self,
        sequence: int,
        records: list[dict],
        songs_by_path: dict[str, dict],
        library_root: str,
        valid_tags: list[str],
    ):
        super().__init__()
        self.sequence = sequence
        self.records = [dict(record) for record in records]
        self.songs_by_path = {
            path: dict(song)
            for path, song in songs_by_path.items()
        }
        self.library_root = library_root
        self.valid_tags = list(valid_tags)

    def run(self):
        try:
            store = PlaylistAssignmentStore(library_root=self.library_root)
            store.records = [store._sanitize_record(record) for record in self.records]
            result = store.build_reconcile_result(
                self.songs_by_path,
                self.library_root,
                self.valid_tags,
            )
            self.result_ready.emit(self.sequence, result)
        except Exception as exc:
            self.error.emit(self.sequence, str(exc))


def _get_duration_ms(path: str) -> int:
    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(path)
        if audio and audio.info:
            return int(audio.info.length * 1000)
    except Exception:
        pass
    return 0


def _auto_query_candidates(song: dict) -> List[str]:
    """
    Build robust auto-assign queries from metadata + filename fallbacks.
    Ordered from most specific to broadest.
    """
    title = (song.get('title') or '').strip()
    artist = (song.get('artist') or '').strip()
    album = (song.get('album') or '').strip()
    filename = song.get('filename') or Path(song.get('path', '')).name
    stem = Path(filename).stem.strip()
    stem = re.sub(r'^\d+[.\s]+', '', stem).strip()

    queries: List[str] = []

    primary = build_query(title=title, artist=artist, album=album).strip()
    if primary:
        queries.append(primary)

    if artist and title:
        queries.append(f'{artist} {title}'.strip())
    if artist and album:
        queries.append(f'{artist} {album}'.strip())
    if title:
        queries.append(title)
    if stem:
        queries.append(stem)

    # Try "Artist - Title" filename parsing when present.
    if ' - ' in stem:
        a, t = stem.split(' - ', 1)
        a = _clean_query_part(a)
        t = _clean_query_part(t)
        combo = f'{a} {t}'.strip()
        if combo:
            queries.append(combo)
        if a:
            queries.append(a)
        if t:
            queries.append(t)
    else:
        if stem:
            queries.append(stem)

    # De-duplicate while preserving order.
    seen = set()
    out: List[str] = []
    for q in queries:
        qq = _clean_query_part(q)
        if qq and qq not in seen:
            seen.add(qq)
            out.append(qq)
    return out


def _result_urls(result: dict) -> List[str]:
    """Ordered URL fallbacks for one search result."""
    urls = []
    for key in ('artwork_url', 'thumb_url'):
        val = (result.get(key) or '').strip()
        if val and val not in urls:
            urls.append(val)
    return urls


def _clean_query_part(text: str) -> str:
    text = re.sub(r'\([^)]*\)', '', text)
    text = re.sub(r'\[[^\]]*\]', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()
