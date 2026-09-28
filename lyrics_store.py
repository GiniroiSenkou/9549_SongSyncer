"""
lyrics_store.py - Track which songs have matching .lrc files.

Pairing rule: a song at `<music>/Foo - Bar.mp3` matches the lyrics file at
`<lyrics_folder>/Foo - Bar.lrc`. Strict (case-folded on case-insensitive
filesystems via os.path semantics; no whitespace collapsing), matching the
same exact-match convention used for JSON record filename matching.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from pathlib import Path

from json_health import loose_key


LRC_EXT = '.lrc'
# "Not found online" list: songs no source had lyrics for. Re-asking every
# run made the automatic fetch slow, so a miss is remembered across sessions
# (for a year) together with the date and the sources that were tried. An
# explicit Fetch on a selected song clears its entry and asks again.
MISS_CACHE_NAME = '.lrc_misses.json'
MISS_TTL_S = 365 * 24 * 3600
_LRC_TS_RE = re.compile(r'\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]')


class LyricsStore:
    def __init__(self, folder: str = ''):
        self._lyrics_folder: str = folder or ''
        self._paths_with_lyrics: set[str] = set()
        # Orphan .lrc files: present in the lyrics folder but no song in the
        # current library shares their filename stem. Populated by `scan`.
        self._orphan_lrc_files: list[str] = []
        self._misses: dict[str, float] = {}
        self._misses_loaded_for: str = ''
        self._miss_lock = threading.Lock()

    # ── config ───────────────────────────────────────────────────────────────

    def set_folder(self, folder: str) -> None:
        self._lyrics_folder = folder or ''
        self._paths_with_lyrics.clear()

    def get_folder(self) -> str:
        return self._lyrics_folder

    # ── path helpers ─────────────────────────────────────────────────────────

    def lrc_path_for(self, song_path: str) -> str:
        """Canonical .lrc destination for a song. Returns '' if no folder set."""
        if not self._lyrics_folder or not song_path:
            return ''
        stem = Path(song_path).stem
        return os.path.join(self._lyrics_folder, stem + LRC_EXT)

    # ── scan ─────────────────────────────────────────────────────────────────

    def scan(self, songs_by_path: dict[str, dict]) -> set[str]:
        """Rebuild the set of song paths whose .lrc exists on disk, and the
        list of orphan .lrc files (lyrics present in the folder but with no
        matching song in the current library)."""
        self._paths_with_lyrics.clear()
        self._orphan_lrc_files = []
        if not self._lyrics_folder or not os.path.isdir(self._lyrics_folder):
            return self._paths_with_lyrics

        # Build the set of song stems we expect lyrics for.
        song_stems: set[str] = set()
        for song_path in songs_by_path:
            lrc = self.lrc_path_for(song_path)
            if lrc and os.path.isfile(lrc):
                self._paths_with_lyrics.add(song_path)
            song_stems.add(Path(song_path).stem)

        # Scan the lyrics folder for .lrc files whose stem isn't claimed by
        # any current song.
        try:
            for entry in os.listdir(self._lyrics_folder):
                if not entry.lower().endswith(LRC_EXT):
                    continue
                stem = Path(entry).stem
                if stem not in song_stems:
                    self._orphan_lrc_files.append(
                        os.path.join(self._lyrics_folder, entry)
                    )
        except OSError:
            pass

        return self._paths_with_lyrics

    @property
    def orphan_lrc_files(self) -> list[str]:
        return list(self._orphan_lrc_files)

    def has_lyrics(self, song_path: str) -> bool:
        return song_path in self._paths_with_lyrics

    # ── add / remove ─────────────────────────────────────────────────────────

    def import_lrc(self, song_path: str, source_lrc: str,
                   overwrite: bool = False) -> str:
        """Copy `source_lrc` into the lyrics folder under the canonical name.

        Returns the destination path. Raises ValueError if no folder is set,
        FileNotFoundError if source doesn't exist, FileExistsError if a file
        already exists at the destination and overwrite=False, OSError on
        copy failure.
        """
        if not self._lyrics_folder:
            raise ValueError('Lyrics folder is not configured.')
        if not os.path.isfile(source_lrc):
            raise FileNotFoundError(f'Source .lrc not found: {source_lrc}')
        dest = self.lrc_path_for(song_path)
        if not dest:
            raise ValueError(f'Cannot derive destination for {song_path}.')
        os.makedirs(self._lyrics_folder, exist_ok=True)
        if os.path.isfile(dest) and not overwrite:
            raise FileExistsError(f'Lyrics file already exists: {dest}')
        shutil.copyfile(source_lrc, dest)
        self._paths_with_lyrics.add(song_path)
        return dest

    def save_text(self, song_path: str, lrc_text: str) -> str:
        """Write LRC text atomically to the canonical destination. Used by the
        online fetcher. Overwrites by design."""
        if not self._lyrics_folder:
            raise ValueError('Lyrics folder is not configured.')
        dest = self.lrc_path_for(song_path)
        if not dest:
            raise ValueError(f'Cannot derive destination for {song_path}.')
        os.makedirs(self._lyrics_folder, exist_ok=True)
        tmp = dest + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as fh:
            fh.write(lrc_text)
        os.replace(tmp, dest)
        self._paths_with_lyrics.add(song_path)
        return dest

    def remove_lrc(self, song_path: str) -> bool:
        """Delete the .lrc for this song. Returns True if a file was removed."""
        dest = self.lrc_path_for(song_path)
        if not dest or not os.path.isfile(dest):
            self._paths_with_lyrics.discard(song_path)
            return False
        try:
            os.remove(dest)
        except OSError:
            return False
        self._paths_with_lyrics.discard(song_path)
        return True

    def rename_for_song(self, old_song_path: str, new_song_path: str) -> bool:
        """When a song file is renamed, move its .lrc to match.

        Returns True if a lyrics file existed and was renamed. False if there
        was nothing to rename, or if the rename failed silently."""
        old_lrc = self.lrc_path_for(old_song_path)
        new_lrc = self.lrc_path_for(new_song_path)
        if not old_lrc or not new_lrc or old_lrc == new_lrc:
            return False
        if not os.path.isfile(old_lrc):
            return False
        try:
            os.replace(old_lrc, new_lrc)
        except OSError:
            return False
        self._paths_with_lyrics.discard(old_song_path)
        self._paths_with_lyrics.add(new_song_path)
        return True

    # ── negative cache (LRCLIB misses) ───────────────────────────────────────

    @staticmethod
    def miss_key(artist: str, title: str) -> str:
        return loose_key(f'{artist or ""}|{title or ""}')

    @staticmethod
    def resolve_artist_title(song: dict) -> tuple[str, str, bool]:
        """(artist, title, from_filename) for a song dict: metadata first,
        then an ``Artist - Title`` split of the filename. Shared by the
        fetch worker and the UI so both agree on the cache key."""
        title = (song.get('title') or '').strip()
        artist = (song.get('artist') or '').strip()
        from_filename = False
        if not title or not artist:
            stem = Path(song.get('filename', '') or song.get('path', '')).stem
            if ' - ' in stem:
                a, t = stem.split(' - ', 1)
                if not artist:
                    artist = a.strip()
                    from_filename = True
                if not title:
                    title = t.strip()
                    from_filename = True
        return artist, title, from_filename

    @classmethod
    def miss_key_for_song(cls, song: dict) -> str:
        artist, title, _ = cls.resolve_artist_title(song)
        if not artist or not title:
            return ''
        return cls.miss_key(artist, title)

    def _miss_cache_path(self) -> str:
        return os.path.join(self._lyrics_folder, MISS_CACHE_NAME) if self._lyrics_folder else ''

    def load_misses(self) -> dict[str, float]:
        path = self._miss_cache_path()
        with self._miss_lock:
            if self._misses_loaded_for == path:
                return dict(self._misses)
            self._misses = {}
            self._misses_loaded_for = path
            if path and os.path.isfile(path):
                try:
                    with open(path, 'r', encoding='utf-8') as fh:
                        raw = json.load(fh)
                    now = time.time()
                    if isinstance(raw, dict):
                        for k, v in raw.items():
                            # v1 wrote a bare timestamp; v2 writes a dict.
                            if isinstance(v, (int, float)):
                                v = {'ts': float(v), 'sources': ['lrclib']}
                            if not isinstance(v, dict):
                                continue
                            try:
                                ts = float(v.get('ts', 0))
                            except (TypeError, ValueError):
                                continue
                            if now - ts < MISS_TTL_S:
                                self._misses[str(k)] = dict(v, ts=ts)
                except (OSError, ValueError):
                    self._misses = {}
            return dict(self._misses)

    def miss_info(self, key: str) -> dict | None:
        """Details of a remembered miss (``ts``, ``sources``, ``artist``,
        ``title``) or None."""
        if not key:
            return None
        self.load_misses()
        with self._miss_lock:
            info = self._misses.get(key)
        if info is None or time.time() - float(info.get('ts', 0)) >= MISS_TTL_S:
            return None
        return dict(info)

    def is_known_miss(self, key: str) -> bool:
        return self.miss_info(key) is not None

    # kept for older callers / tests
    def is_recent_miss(self, key: str) -> bool:
        return self.is_known_miss(key)

    def record_miss(self, key: str, sources=(), artist: str = '', title: str = '') -> None:
        self.load_misses()
        with self._miss_lock:
            self._misses[key] = {
                'ts': time.time(),
                'sources': list(sources) or ['lrclib'],
                'artist': artist,
                'title': title,
            }

    def not_found_count(self, songs_by_path: dict[str, dict]) -> int:
        """How many songs without lyrics are remembered as not found online."""
        return sum(
            1 for path, song in songs_by_path.items()
            if not self.has_lyrics(path) and self.is_known_miss(self.miss_key_for_song(song))
        )

    def forget_miss(self, key: str) -> None:
        with self._miss_lock:
            self._misses.pop(key, None)

    def save_misses(self) -> None:
        path = self._miss_cache_path()
        if not path:
            return
        with self._miss_lock:
            payload = dict(self._misses)
        try:
            os.makedirs(self._lyrics_folder, exist_ok=True)
            tmp = path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as fh:
                json.dump(payload, fh)
            os.replace(tmp, path)
        except OSError:
            pass

    # ── introspection ────────────────────────────────────────────────────────

    @property
    def count(self) -> int:
        return len(self._paths_with_lyrics)


# ── LRC timestamp helpers (used by the Trimmer) ─────────────────────────────

def shift_lrc_text(text: str, offset_ms: int) -> str:
    """Shift every ``[mm:ss.xx]`` tag by ``offset_ms`` (negative = earlier).
    Lines whose every timestamp would fall before 0 are dropped; a line with
    some surviving timestamps keeps only those."""
    if not offset_ms:
        return text
    out: list[str] = []
    for line in text.splitlines():
        stamps = list(_LRC_TS_RE.finditer(line))
        if not stamps:
            out.append(line)
            continue
        body = line[stamps[-1].end():]
        kept: list[str] = []
        for m in stamps:
            mins, secs, frac = int(m.group(1)), int(m.group(2)), m.group(3) or '0'
            frac_ms = int(frac.ljust(3, '0')[:3])
            total = mins * 60_000 + secs * 1000 + frac_ms + offset_ms
            if total < 0:
                continue
            kept.append(f'[{total // 60_000:02d}:{(total % 60_000) // 1000:02d}.'
                        f'{(total % 1000) // 10:02d}]')
        if kept:
            out.append(''.join(kept) + body)
    return '\n'.join(out) + ('\n' if text.endswith('\n') else '')


def shift_lrc(path: str, offset_ms: int) -> bool:
    """Rewrite ``path`` in place with shifted timestamps. Returns True if the
    file was changed."""
    if not offset_ms or not os.path.isfile(path):
        return False
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as fh:
            text = fh.read()
    except OSError:
        return False
    shifted = shift_lrc_text(text, offset_ms)
    if shifted == text:
        return False
    tmp = path + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as fh:
            fh.write(shifted)
        os.replace(tmp, path)
    except OSError:
        return False
    return True
