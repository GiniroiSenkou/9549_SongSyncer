from __future__ import annotations

import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Iterable

from tags_list import normalize_tag_name

CURRENT_VERSION = 2
AUDIO_EXTENSIONS = {
    '.mp3',
    '.flac',
    '.m4a',
    '.aac',
    '.ogg',
    '.opus',
    '.wav',
}
UNICODE_ESCAPE_RE = re.compile(r'\\u[0-9a-fA-F]{4}|\\U[0-9a-fA-F]{8}')


def default_playlist_json_path() -> str:
    return str(Path(__file__).resolve().parent / 'Data' / 'music.json')


class PlaylistAssignmentStore:
    """Load, reconcile, edit and save song -> playlist assignments."""

    def __init__(self, json_path: str = '', library_root: str = ''):
        self.json_path = json_path
        self.library_root = library_root
        self.records: list[dict] = []
        self.version = CURRENT_VERSION
        self.missing_song_records: list[dict] = []
        self.mismatch_records: list[dict] = []
        self.invalid_tag_records: list[dict] = []
        self.match_source_counts: dict[str, int] = {}
        self._songs_by_path: dict[str, dict] = {}
        self._path_to_record: dict[str, dict] = {}
        self._match_source_by_path: dict[str, str] = {}

    def set_json_path(self, json_path: str):
        self.json_path = json_path

    def load(self):
        self.records = []
        self.version = CURRENT_VERSION
        self.missing_song_records = []
        self.mismatch_records = []
        self.invalid_tag_records = []
        self.match_source_counts = {}
        self._path_to_record = {}
        self._match_source_by_path = {}

        if not self.json_path or not os.path.exists(self.json_path):
            return

        with open(self.json_path, 'r', encoding='utf-8') as fh:
            payload = json.load(fh)

        records: list[dict]
        if isinstance(payload, list):
            records = payload
            self.version = 1
        elif isinstance(payload, dict):
            records = payload.get('songs', [])
            self.version = int(payload.get('version', CURRENT_VERSION))
        else:
            raise ValueError('Unsupported playlist JSON format.')

        self.records = [self._sanitize_record(record) for record in records if isinstance(record, dict)]

    def reconcile(self, songs_by_path: dict[str, dict], library_root: str, valid_tags: Iterable[str]):
        result = self.build_reconcile_result(songs_by_path, library_root, valid_tags)
        self.apply_reconcile_result(result, songs_by_path, library_root)
        return result

    def build_reconcile_result(
        self,
        songs_by_path: dict[str, dict],
        library_root: str,
        valid_tags: Iterable[str],
    ) -> dict:
        self.library_root = library_root or ''
        self._songs_by_path = songs_by_path

        # Strict exact-match validity: a tag is valid iff it matches a known
        # identifier verbatim. No more fuzzy variant matching.
        valid_set = set(valid_tags)
        rel_map: dict[str, list[str]] = {}
        basename_map: dict[str, list[str]] = {}
        filename_map: dict[str, list[str]] = {}
        result = {
            'path_to_record_indexes': {},
            'match_source_by_path': {},
            'match_source_counts': {
                'path': 0,
                'basename': 0,
                'filename': 0,
                'title_filename': 0,
            },
            'mismatch_records': [],
            'missing_song_records': [],
            'invalid_tag_records': [],
        }

        for path, song in songs_by_path.items():
            rel_key = self._normalize_lookup_path(self._relative_song_path(path))
            self._remember_candidate(rel_map, rel_key, path)

            basename_key = self._normalize_filename(os.path.basename(path))
            self._remember_candidate(basename_map, basename_key, path)

            filename = song.get('filename') or os.path.basename(path)
            filename_key = self._normalize_filename(filename)
            self._remember_candidate(filename_map, filename_key, path)

        for idx, record in enumerate(self.records):
            matched_path = ''
            match_source = ''
            candidate_paths: list[str] = []
            reason = ''

            lookups = [
                ('path', self._normalize_lookup_path(record.get('path', ''))),
                ('basename', self._normalize_filename(os.path.basename(record.get('path', '')))),
                ('filename', self._normalize_filename(record.get('filename', ''))),
                ('title_filename', self._normalize_filename(record.get('title_filename', ''))),
            ]
            maps = {
                'path': rel_map,
                'basename': basename_map,
                'filename': filename_map,
                'title_filename': filename_map,
            }

            for source, key in lookups:
                if not key:
                    continue
                candidate_paths = list(maps[source].get(key, []))
                if len(candidate_paths) == 1:
                    matched_path = candidate_paths[0]
                    match_source = source
                    break
                if len(candidate_paths) > 1:
                    reason = 'ambiguous_match'
                    match_source = source
                    break

            invalid = [
                tag for tag in record['tags']
                if tag not in valid_set
            ]
            if matched_path:
                result['path_to_record_indexes'][matched_path] = idx
                result['match_source_by_path'][matched_path] = match_source
                result['match_source_counts'][match_source] += 1
            else:
                if not reason:
                    reason = 'filename_not_found'
                mismatch = self._build_mismatch_entry(
                    reason=reason,
                    record=record,
                    record_index=idx,
                    match_source=match_source,
                    candidate_paths=candidate_paths,
                    matched_path='',
                )
                result['mismatch_records'].append(mismatch)
                result['missing_song_records'].append(mismatch)

            if invalid:
                result['invalid_tag_records'].append(
                    self._build_invalid_tag_entry(
                        record=record,
                        record_index=idx,
                        tags=invalid,
                        matched_path=matched_path,
                        match_source=match_source,
                    )
                )

        return result

    def apply_reconcile_result(
        self,
        result: dict,
        songs_by_path: dict[str, dict],
        library_root: str,
    ):
        self.library_root = library_root or ''
        self._songs_by_path = songs_by_path
        self._path_to_record = {}
        self._match_source_by_path = dict(result.get('match_source_by_path', {}))
        self.match_source_counts = dict(result.get('match_source_counts', {}))
        self.mismatch_records = list(result.get('mismatch_records', []))
        self.missing_song_records = list(result.get('missing_song_records', []))
        self.invalid_tag_records = list(result.get('invalid_tag_records', []))

        for path, idx in result.get('path_to_record_indexes', {}).items():
            if path in songs_by_path and 0 <= idx < len(self.records):
                record = self.records[idx]
                self._canonicalize_record(record, path, songs_by_path[path])
                self._path_to_record[path] = record

    def get_tags(self, song_path: str) -> list[str]:
        record = self._path_to_record.get(song_path)
        if not record:
            return []
        return list(record.get('tags', []))

    def update_record_from_song(self, record_index: int, song_path: str, song: dict) -> bool:
        if not (0 <= record_index < len(self.records)):
            return False
        record = self.records[record_index]
        before = (
            record.get('path', ''),
            record.get('filename', ''),
            record.get('title', ''),
        )
        self._canonicalize_record(record, song_path, song)
        after = (
            record.get('path', ''),
            record.get('filename', ''),
            record.get('title', ''),
        )
        return before != after

    def remove_invalid_tags_from_record(self, record_index: int, valid_tags: Iterable[str]) -> bool:
        if not (0 <= record_index < len(self.records)):
            return False
        record = self.records[record_index]
        before = list(record.get('tags', []))
        valid = set(valid_tags)
        record['tags'] = [tag for tag in before if tag in valid]
        return record['tags'] != before

    def rewrite_tag_in_records(self, old: str, new: str | None) -> int:
        """Replace `old` with `new` (or drop it if `new is None`) in every
        record's tag list, de-duplicating. Returns count of records changed.
        Caller is responsible for persisting via :meth:`save`."""
        if not old:
            return 0
        changed = 0
        for record in self.records:
            tags = record.get('tags', []) or []
            if old not in tags:
                continue
            updated: list[str] = []
            seen: set[str] = set()
            for tag in tags:
                replacement = new if tag == old else tag
                if replacement is None:
                    continue
                if replacement in seen:
                    continue
                seen.add(replacement)
                updated.append(replacement)
            if updated != tags:
                record['tags'] = updated
                changed += 1
        return changed

    def has_record(self, song_path: str) -> bool:
        return song_path in self._path_to_record

    def remove_records_by_indexes(self, indexes: list[int]) -> int:
        """Delete records at the given indexes from self.records. Returns count removed."""
        to_remove = sorted(set(i for i in indexes if 0 <= i < len(self.records)), reverse=True)
        for i in to_remove:
            self.records.pop(i)
        # _path_to_record and mismatch lists are stale after this; caller must re-reconcile.
        return len(to_remove)

    def create_records_for_paths(self, song_paths: Iterable[str], songs_by_path: dict[str, dict]) -> int:
        created = 0
        for path in song_paths:
            if path in self._path_to_record:
                continue
            song = songs_by_path.get(path)
            if not song:
                continue
            self._create_record_for_song(path, song)
            created += 1
        return created

    def set_tags_for_paths(
        self,
        song_paths: list[str],
        songs_by_path: dict[str, dict],
        add_tags: Iterable[str],
        remove_tags: Iterable[str],
    ) -> int:
        add_tags = list(add_tags)
        remove_tags = list(remove_tags)
        if not add_tags and not remove_tags:
            return 0

        changed = 0
        for path in song_paths:
            song = songs_by_path.get(path)
            if not song:
                continue

            record = self._path_to_record.get(path)
            if not record:
                continue

            before = list(record['tags'])
            tag_list = list(record['tags'])

            for tag in add_tags:
                if tag not in tag_list:
                    tag_list.append(tag)
            for tag in remove_tags:
                if tag in tag_list:
                    tag_list.remove(tag)

            record['tags'] = tag_list
            record['filename'] = song.get('filename', '')
            if not record.get('title'):
                record['title'] = song.get('filename', '') or os.path.basename(path)
            record['path'] = self._relative_song_path(path)

            if record['tags'] != before:
                changed += 1

        return changed

    def rename_song_file(self, old_path: str, new_filename: str) -> tuple[str, str] | None:
        """Rename the audio file on disk and update the linked JSON record.

        Returns (old_path, new_path) on success, None if the requested name
        equals the current name. Raises ValueError on invalid input,
        FileExistsError if the destination is taken, OSError on rename failure.
        """
        if not old_path:
            raise ValueError('Empty source path.')
        new_filename = (new_filename or '').strip()
        if not new_filename:
            raise ValueError('New filename cannot be empty.')
        if os.sep in new_filename or '/' in new_filename:
            raise ValueError('Filename cannot contain path separators.')

        original_ext = Path(old_path).suffix
        if original_ext and not Path(new_filename).suffix:
            new_filename = new_filename + original_ext

        new_path = os.path.join(os.path.dirname(old_path), new_filename)
        if os.path.normcase(new_path) == os.path.normcase(old_path):
            return None
        if os.path.exists(new_path):
            raise FileExistsError(f'A file named "{new_filename}" already exists in this folder.')

        os.rename(old_path, new_path)

        record = self._path_to_record.pop(old_path, None)
        if record is not None:
            record['path'] = self._relative_song_path(new_path)
            record['filename'] = new_filename
            record['title'] = new_filename
            self._path_to_record[new_path] = record

        if old_path in self._match_source_by_path:
            self._match_source_by_path[new_path] = self._match_source_by_path.pop(old_path)
        if old_path in self._songs_by_path:
            song = self._songs_by_path.pop(old_path)
            song['path'] = new_path
            song['filename'] = new_filename
            self._songs_by_path[new_path] = song

        return old_path, new_path

    def sync_song(self, song: dict) -> bool:
        song_path = song.get('path', '')
        if not song_path:
            return False

        record = self._path_to_record.get(song_path)
        if not record:
            rel_path = self._relative_song_path(song_path)
            for existing in self.records:
                if existing.get('path', '') == rel_path:
                    record = existing
                    self._path_to_record[song_path] = existing
                    break
        if not record:
            return False

        changed = False
        updates = {
            'path': self._relative_song_path(song_path),
            'filename': song.get('filename', ''),
        }
        if not record.get('title'):
            updates['title'] = song.get('filename', '') or os.path.basename(song_path)
        for key, value in updates.items():
            if record.get(key) != value:
                record[key] = value
                changed = True
        return changed

    def save(self):
        if not self.json_path:
            raise ValueError('No playlist JSON path selected.')

        out_path = Path(self.json_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = out_path.with_name(out_path.name + '.tmp')

        songs = sorted(
            (self._serialize_record(record) for record in self.records),
            key=lambda item: item.get('title', ''),
        )
        if self.version == 1:
            payload: object = songs
        else:
            payload = {'version': CURRENT_VERSION, 'songs': songs}

        try:
            with tmp_path.open('w', encoding='utf-8') as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=4)
                fh.write('\n')
            os.replace(tmp_path, out_path)
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

    def summary(self) -> str:
        total = len(self.records)
        if not total:
            return 'JSON: no tag assignments loaded yet'

        issues = []
        if self.mismatch_records:
            issues.append(f'{len(self.mismatch_records)} mismatch(es)')
        if self.invalid_tag_records:
            issues.append(f'{len(self.invalid_tag_records)} invalid tag group(s)')
        if not issues:
            issues.append('all assignments matched')
        return f'JSON: {total} song entries, ' + ', '.join(issues)

    def _create_record_for_song(self, path: str, song: dict) -> dict:
        record = self._sanitize_record({
            'path': self._relative_song_path(path),
            'filename': song.get('filename', ''),
            'title': song.get('filename', '') or os.path.basename(path),
            'tags': [],
        })
        self.records.append(record)
        self._path_to_record[path] = record
        return record

    def _build_mismatch_entry(
        self,
        reason: str,
        record: dict,
        record_index: int,
        match_source: str,
        candidate_paths: list[str],
        matched_path: str,
    ) -> dict:
        candidate_labels = []
        for path in candidate_paths:
            song = self._songs_by_path.get(path, {})
            label = song.get('title') or song.get('filename') or path
            candidate_labels.append(label)

        song = self._songs_by_path.get(matched_path, {}) if matched_path else {}
        return {
            'reason': reason,
            'record_index': record_index,
            'match_source': match_source,
            'song': self._record_label(record),
            'path': record.get('path', ''),
            'title': record.get('title', ''),
            'filename': record.get('filename', ''),
            'matched_path': matched_path,
            'current_title': song.get('title', '') if song else '',
            'candidates': candidate_labels,
            'candidate_paths': list(candidate_paths),
        }

    def _build_invalid_tag_entry(
        self,
        record: dict,
        record_index: int,
        tags: list[str],
        matched_path: str,
        match_source: str,
    ) -> dict:
        return {
            'record_index': record_index,
            'song': self._record_label(record),
            'path': record.get('path', ''),
            'filename': record.get('filename', ''),
            'title': record.get('title', ''),
            'tags': list(tags),
            'matched_path': matched_path,
            'match_source': match_source,
        }

    def _relative_song_path(self, path: str) -> str:
        if not path:
            return ''
        if self.library_root:
            try:
                return os.path.relpath(path, self.library_root).replace('\\', '/')
            except ValueError:
                pass
        return path.replace('\\', '/')

    @staticmethod
    def _remember_candidate(mapping: dict[str, list[str]], key: str, value: str):
        if not key:
            return
        bucket = mapping.setdefault(key, [])
        if value not in bucket:
            bucket.append(value)

    @staticmethod
    def _record_label(record: dict) -> str:
        return record.get('title') or record.get('filename') or record.get('path') or 'Unknown song'

    @staticmethod
    def _normalize_lookup_path(value: str) -> str:
        if not value:
            return ''
        value = str(value).replace('\\', '/').strip()
        value = unicodedata.normalize('NFC', value)
        return value.casefold()

    @staticmethod
    def _normalize_filename(value: str) -> str:
        if not value:
            return ''
        value = os.path.basename(str(value)).strip()
        value = unicodedata.normalize('NFC', value)
        return value.casefold()

    @staticmethod
    def _sanitize_record(record: dict) -> dict:
        tags = []
        for tag in record.get('tags', []):
            if isinstance(tag, str):
                clean = tag.strip()
                if clean and clean not in tags:
                    tags.append(clean)

        path = str(record.get('path', '') or '').replace('\\', '/').strip()
        filename = str(record.get('filename', '') or '').strip()
        title = str(record.get('title', '') or '').strip()
        if not title and filename:
            title = filename
        title_filename = PlaylistAssignmentStore._title_filename_candidate(title, filename)
        if not filename and title_filename:
            filename = title_filename

        return {
            'path': path,
            'filename': filename,
            'title': title,
            'title_filename': title_filename,
            'tags': tags,
        }

    def _canonicalize_record(self, record: dict, song_path: str, song: dict):
        record['path'] = self._relative_song_path(song_path)
        record['filename'] = song.get('filename') or os.path.basename(song_path)
        if not record.get('title'):
            record['title'] = record['filename']

    @staticmethod
    def _serialize_record(record: dict) -> dict:
        clean = PlaylistAssignmentStore._sanitize_record(record)
        return {
            'title': clean.get('title', '') or clean.get('filename', ''),
            'tags': list(clean.get('tags', [])),
        }

    @staticmethod
    def _title_filename_candidate(title: str, filename: str = '') -> str:
        if filename:
            return ''
        title = str(title or '').strip()
        if not title:
            return ''

        decoded = PlaylistAssignmentStore._decode_unicode_escapes(title)
        if Path(decoded).suffix.lower() not in AUDIO_EXTENSIONS:
            return ''
        return os.path.basename(decoded).strip()

    @staticmethod
    def _decode_unicode_escapes(value: str) -> str:
        if not value or not UNICODE_ESCAPE_RE.search(value):
            return value
        try:
            return value.encode('utf-8').decode('unicode_escape')
        except UnicodeDecodeError:
            return value
