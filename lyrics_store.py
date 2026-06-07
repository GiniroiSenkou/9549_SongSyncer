"""
lyrics_store.py - Track which songs have matching .lrc files.

Pairing rule: a song at `<music>/Foo - Bar.mp3` matches the lyrics file at
`<lyrics_folder>/Foo - Bar.lrc`. Strict (case-folded on case-insensitive
filesystems via os.path semantics; no whitespace collapsing), matching the
same exact-match convention used for JSON record filename matching.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path


LRC_EXT = '.lrc'


class LyricsStore:
    def __init__(self, folder: str = ''):
        self._lyrics_folder: str = folder or ''
        self._paths_with_lyrics: set[str] = set()
        # Orphan .lrc files: present in the lyrics folder but no song in the
        # current library shares their filename stem. Populated by `scan`.
        self._orphan_lrc_files: list[str] = []

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

    # ── introspection ────────────────────────────────────────────────────────

    @property
    def count(self) -> int:
        return len(self._paths_with_lyrics)
