"""
workers.py – QThread workers for all background operations.
"""

import os
import queue
import threading
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
from json_health import build_health_report
from lyrics_fetcher import fetch_lyrics, SOURCES as LYRICS_SOURCES
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

class _RateLimiter:
    """Token-bucket-ish pacing shared by the parallel lyrics fetchers so the
    total request rate to lrclib.net stays polite regardless of thread count."""

    def __init__(self, per_second: float):
        self._interval = 1.0 / max(0.1, per_second)
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self):
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next)
            self._next = slot + self._interval
        delay = slot - now
        if delay > 0:
            time.sleep(delay)


class LyricsFetchWorker(QThread):
    """Download .lrc files from LRCLIB for a list of songs.

    Runs ``max_workers`` fetches in parallel behind a shared rate limiter,
    skips songs that already have lyrics or are a recent known miss, and can
    be re-targeted to a new lyrics folder mid-run (``retarget``) so a path
    change in Settings never interrupts the download."""

    song_done = pyqtSignal(str, str)          # (path, status: ok|exists|miss|known_miss|skipped|cancelled)
    progress  = pyqtSignal(int, int, str)     # (done, total, song_title)
    finished  = pyqtSignal(int, int, int)     # (fetched, failed, skipped)

    STATUS_OK = 'ok'
    STATUS_EXISTS = 'exists'
    STATUS_MISS = 'miss'              # asked every source, nothing — now remembered
    STATUS_KNOWN_MISS = 'known_miss'  # remembered from an earlier session, not asked again
    STATUS_SKIPPED = 'skipped'        # no artist/title to search with
    STATUS_CANCELLED = 'cancelled'

    def __init__(self, songs: list, lyrics_folder: str,
                 max_workers: int = 4, requests_per_second: float = 6.0):
        super().__init__()
        self.songs = list(songs)
        self._stop = False
        self._max_workers = max(1, int(max_workers))
        self._limiter = _RateLimiter(requests_per_second)
        # Local store instance used only for path math + atomic write. Its
        # folder can be swapped while running.
        self._store = LyricsStore(lyrics_folder)
        self._done = 0

    def cancel(self):
        self._stop = True

    def retarget(self, lyrics_folder: str):
        """Point subsequent writes at a new lyrics folder (files already
        written stay where they are)."""
        self._store.save_misses()
        self._store.set_folder(lyrics_folder)

    @property
    def lyrics_folder(self) -> str:
        return self._store.get_folder()

    # ── per-song job (runs in the pool) ─────────────────────────────────────

    def _fetch_one(self, song: dict) -> tuple[str, str]:
        path = song.get('path', '')
        if self._stop:
            return path, self.STATUS_CANCELLED

        # Skip if a .lrc already exists for this song.
        dest = self._store.lrc_path_for(path)
        if dest and os.path.isfile(dest):
            return path, self.STATUS_EXISTS

        artist, title, from_filename = LyricsStore.resolve_artist_title(song)
        album = (song.get('album') or '').strip()
        duration_ms = song.get('duration_ms', 0)
        if not title or not artist:
            return path, self.STATUS_SKIPPED

        key = LyricsStore.miss_key(artist, title)
        if self._store.is_known_miss(key):
            return path, self.STATUS_KNOWN_MISS

        try:
            text, _source = fetch_lyrics(title, artist, album, duration_ms,
                                         throttle=self._limiter.wait,
                                         allow_swap=from_filename)
        except Exception:
            text = None
        if self._stop:
            return path, self.STATUS_CANCELLED
        if text:
            try:
                self._store.save_text(path, text)
                self._store.forget_miss(key)
                return path, self.STATUS_OK
            except (OSError, ValueError):
                return path, self.STATUS_MISS
        self._store.record_miss(key, LYRICS_SOURCES, artist, title)
        return path, self.STATUS_MISS

    def run(self):
        fetched = failed = skipped = 0
        total = len(self.songs)
        self._store.load_misses()
        try:
            with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
                futures = {pool.submit(self._fetch_one, song): song for song in self.songs}
                for future in as_completed(futures):
                    song = futures[future]
                    path = song.get('path', '')
                    try:
                        path, status = future.result()
                    except Exception:
                        status = self.STATUS_MISS
                    self._done += 1
                    if status in (self.STATUS_OK, self.STATUS_EXISTS):
                        fetched += 1
                    elif status in (self.STATUS_MISS, self.STATUS_KNOWN_MISS):
                        failed += 1
                    else:
                        skipped += 1
                    display = song.get('title') or song.get('filename') or path
                    self.progress.emit(self._done, total, display)
                    self.song_done.emit(path, status)
                    if self._stop:
                        pool.shutdown(wait=False, cancel_futures=True)
                        break
        finally:
            self._store.save_misses()
        self.finished.emit(fetched, failed, skipped)


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
    """Reconcile playlist JSON records against the current library off the UI
    thread, and — on the same background thread — run the strict-sync audit
    (``json_health.build_health_report``) too.

    The audit used to run synchronously on the UI thread every time a
    reconcile completed (including the debounced re-check after every Edit
    Song save), and its ``difflib.get_close_matches`` fuzzy search over
    every unmatched JSON record against every file on disk is O(missing ×
    library size): on a library of a few thousand songs with a few hundred
    drifted/missing entries that was measured to freeze the UI for 1-3+
    seconds per reconcile. Computing it here means the UI thread only ever
    does the cheap, already-computed-list bookkeeping in
    ``MainWindow._rebuild_playlist_issue_map``.
    """

    result_ready = pyqtSignal(int, dict, list)  # (sequence, reconcile_result, health_issues)
    error = pyqtSignal(int, str)          # (sequence, message)

    def __init__(
        self,
        sequence: int,
        records: list[dict],
        songs_by_path: dict[str, dict],
        library_root: str,
        valid_tags: list[str],
        lyrics_store: LyricsStore | None = None,
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
        self.lyrics_store = lyrics_store

    def run(self):
        try:
            store = PlaylistAssignmentStore(library_root=self.library_root)
            store.records = [store._sanitize_record(record) for record in self.records]
            result = store.build_reconcile_result(
                self.songs_by_path,
                self.library_root,
                self.valid_tags,
            )
            # Apply the result to this throwaway store (never the live one —
            # that still happens on the UI thread) purely so the audit below
            # sees fully-populated _path_to_record / missing_song_records /
            # duplicate_records, exactly as MainWindow._on_playlist_reconcile
            # _complete would produce on the real store from the same result.
            store.apply_reconcile_result(result, self.songs_by_path, self.library_root)
            health_issues = build_health_report(
                store, self.songs_by_path, (), self.lyrics_store,
            )
            self.result_ready.emit(self.sequence, result, health_issues)
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


# ── Trimmer workers ───────────────────────────────────────────────────────────

class WaveformWorker(QThread):
    """Decode a song to a peak envelope off the UI thread."""

    ready = pyqtSignal(list, int)     # (peaks, duration_ms)
    error = pyqtSignal(str)

    def __init__(self, path: str, buckets: int = 1600):
        super().__init__()
        self.path = path
        self.buckets = buckets

    def run(self):
        try:
            from audio_trim import compute_peaks
            peaks, duration = compute_peaks(self.path, self.buckets)
            self.ready.emit(peaks, duration)
        except Exception as exc:
            self.error.emit(str(exc))


class TrimWorker(QThread):
    """Run ``audio_trim.trim`` off the UI thread."""

    done = pyqtSignal(object)         # TrimResult
    error = pyqtSignal(str)

    def __init__(self, path: str, start_ms: int, end_ms: int,
                 fade_in_ms: int = 0, fade_out_ms: int = 0, keep_backup: bool = False):
        super().__init__()
        self.path = path
        self.start_ms = start_ms
        self.end_ms = end_ms
        self.fade_in_ms = fade_in_ms
        self.fade_out_ms = fade_out_ms
        self.keep_backup = keep_backup

    def run(self):
        try:
            from audio_trim import trim
            result = trim(self.path, self.start_ms, self.end_ms,
                          fade_in_ms=self.fade_in_ms, fade_out_ms=self.fade_out_ms,
                          keep_backup=self.keep_backup)
            self.done.emit(result)
        except Exception as exc:
            self.error.emit(str(exc))
