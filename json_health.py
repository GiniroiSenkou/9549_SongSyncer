"""
json_health.py – Strict sync audit between the tag JSON, the music folder
and the lyrics folder.

The JSON contract consumed by other projects is simple and strict: every
record's ``title`` must be the **exact on-disk filename, extension included**,
compared as a plain string. SongSyncer's matcher is deliberately lenient
(case-folded, NFC, whitespace-trimmed, and — since the "loose" tier — spacing
normalised) so that it can *find* a drifted record; this module is what turns
"found but drifted" into a visible, fixable issue instead of silently keeping
the stale title.

Nothing in here imports Qt or ``playlist_store`` at module level, so it is
safe to use from worker threads and from ``playlist_store`` itself.
"""

from __future__ import annotations

import difflib
import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

AUDIO_EXTENSIONS = {'.mp3', '.flac', '.m4a', '.aac', '.ogg', '.opus', '.wav'}

_WS_RE = re.compile(r'\s+')
# A hyphen / en dash / em dash that has whitespace on at least one side is an
# artist–title separator with broken spacing ("Artist -Song", "Artist- Song",
# "Artist  -  Song"). A bare hyphen with no spaces on either side ("Lo-Fi",
# "Jay-Z", "AC-DC") is part of a word and is left alone.
_SEP_SPACED_RE = re.compile(r'(?<=\S)(?:\s+[-–—]\s*|\s*[-–—]\s+)(?=\S)')
# En/em dashes are never word-internal punctuation in filenames; normalise
# even when tight ("Artist–Song").
_DASH_TIGHT_RE = re.compile(r'(?<=\S)[–—](?=\S)')
_SPACE_BEFORE_EXT_RE = re.compile(r'\s+(\.[A-Za-z0-9]{1,5})$')
_UNICODE_ESCAPE_RE = re.compile(r'\\u[0-9a-fA-F]{4}|\\U[0-9a-fA-F]{8}')

SEVERITY_ORDER = {'error': 0, 'warning': 1, 'info': 2}


# ── name rules ───────────────────────────────────────────────────────────────

def decode_unicode_escapes(value: str) -> str:
    """Turn literal ``\\u00e9`` sequences (a common artefact of JSON written
    with ``ensure_ascii=True`` and then re-read as text) back into characters."""
    if not value or not _UNICODE_ESCAPE_RE.search(value):
        return value
    try:
        return value.encode('utf-8').decode('unicode_escape')
    except UnicodeDecodeError:
        return value


def normalize_name(name: str) -> str:
    """Canonical spelling of a filename / JSON title.

    * NFC, trimmed, whitespace runs collapsed to one space
    * separator spacing repaired to ``" - "`` (see ``_SEP_SPACED_RE``)
    * en/em dashes replaced by ``" - "``
    * no whitespace before the extension (``"Song .mp3"`` → ``"Song.mp3"``)
    * literal ``\\uXXXX`` escapes decoded
    """
    if not name:
        return ''
    value = decode_unicode_escapes(str(name))
    value = unicodedata.normalize('NFC', value)
    value = _WS_RE.sub(' ', value).strip()
    value = _DASH_TIGHT_RE.sub(' - ', value)
    value = _SEP_SPACED_RE.sub(' - ', value)
    value = _WS_RE.sub(' ', value).strip()
    value = _SPACE_BEFORE_EXT_RE.sub(r'\1', value)
    return value


def loose_key(name: str) -> str:
    """Case-insensitive, spacing-insensitive lookup key."""
    return normalize_name(name).casefold()


def loose_stem_key(name: str) -> str:
    """``loose_key`` of the name without its audio extension. Titles that
    never carried an extension (old v1 records) hash the whole string."""
    clean = normalize_name(name)
    if Path(clean).suffix.lower() in AUDIO_EXTENSIONS:
        clean = str(Path(clean).with_suffix(''))
    return clean.casefold()


def has_audio_extension(name: str) -> bool:
    return Path(str(name or '')).suffix.lower() in AUDIO_EXTENSIONS


def has_typo_error(name: str) -> bool:
    """True when a filename / JSON title ends with a duplicated audio
    extension (``Foo.mp3.mp3``, ``Bar.flac.mp3``) — the classic rename fumble."""
    if not name:
        return False
    parts = str(name).lower().rsplit('.', 2)
    if len(parts) < 3:
        return False
    return f'.{parts[-2]}' in AUDIO_EXTENSIONS and f'.{parts[-1]}' in AUDIO_EXTENSIONS


def describe_drift(json_title: str, disk_name: str) -> str:
    """Human explanation of *how* a JSON title differs from the disk name."""
    if json_title == disk_name:
        return ''
    if not has_audio_extension(json_title) and has_audio_extension(disk_name):
        return 'extension missing'
    if _UNICODE_ESCAPE_RE.search(json_title):
        return 'escaped unicode'
    if json_title.casefold() == disk_name.casefold():
        return 'letter case differs'
    if normalize_name(json_title) == normalize_name(disk_name):
        return 'spacing differs'
    if loose_key(json_title) == loose_key(disk_name):
        return 'case and spacing differ'
    if unicodedata.normalize('NFC', json_title) == unicodedata.normalize('NFC', disk_name):
        return 'unicode form differs'
    return 'name differs'


# ── issues ───────────────────────────────────────────────────────────────────

@dataclass
class HealthIssue:
    kind: str
    severity: str                 # 'error' | 'warning' | 'info'
    message: str
    record_index: int = -1
    song_path: str = ''
    json_title: str = ''
    disk_filename: str = ''
    suggestion: str = ''
    fixable: bool = False
    default_checked: bool = True
    tags: list = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        return ISSUE_LABELS.get(self.kind, self.kind.replace('_', ' ').title())

    @property
    def sort_key(self):
        return (SEVERITY_ORDER.get(self.severity, 9), self.label,
                (self.json_title or self.disk_filename).casefold())


ISSUE_LABELS = {
    'title_drift': 'JSON title differs from file',
    'duplicate_entry': 'Duplicate JSON entries',
    'missing_file': 'No music file for entry',
    'disk_separator_spacing': 'Filename spacing issue',
    'double_extension': 'Doubled extension',
    'invalid_tag': 'Unsupported tag',
    'no_entry': 'Song not in JSON',
    'no_tags': 'No tags assigned',
    'no_lyrics': 'No lyrics file',
    'lyrics_not_found': 'Lyrics not found online',
    'lyrics_drift': 'Lyrics file name differs',
    'lyrics_orphan': 'Orphan lyrics file',
}

ISSUE_ORDER = list(ISSUE_LABELS)


def summarize(issues: Iterable[HealthIssue]) -> dict:
    counts = {'error': 0, 'warning': 0, 'info': 0, 'fixable': 0, 'total': 0}
    for issue in issues:
        counts['total'] += 1
        counts[issue.severity] = counts.get(issue.severity, 0) + 1
        if issue.fixable:
            counts['fixable'] += 1
    return counts


# ── audit ────────────────────────────────────────────────────────────────────

def build_health_report(
    store,
    songs_by_path: dict[str, dict],
    ghost_paths: Iterable[str] = (),
    lyrics_store=None,
    include_info: bool = True,
) -> list[HealthIssue]:
    """Audit ``store`` (an already-reconciled ``PlaylistAssignmentStore``)
    against the real songs in ``songs_by_path`` and, optionally, the lyrics
    folder. Returns issues sorted by severity."""
    ghosts = set(ghost_paths or ())
    real_paths = [p for p in songs_by_path if p not in ghosts]
    records = store.records
    path_to_record = getattr(store, '_path_to_record', {})
    index_by_id = {id(rec): idx for idx, rec in enumerate(records)}

    issues: list[HealthIssue] = []

    # Records that already resolve to a real file — the strict check.
    disk_loose: dict[str, list[str]] = {}
    disk_loose_stem: dict[str, list[str]] = {}
    for path in real_paths:
        base = os.path.basename(path)
        disk_loose.setdefault(loose_key(base), []).append(path)
        disk_loose_stem.setdefault(loose_stem_key(base), []).append(path)

        record = path_to_record.get(path)
        idx = index_by_id.get(id(record), -1) if record is not None else -1
        title = (record or {}).get('title', '') if record else ''
        tags = list((record or {}).get('tags', [])) if record else []

        if record is not None and title != base:
            issues.append(HealthIssue(
                kind='title_drift', severity='error',
                message=f'{describe_drift(title, base)} — JSON "{title}" vs file "{base}"',
                record_index=idx, song_path=path, json_title=title,
                disk_filename=base, suggestion=base, fixable=True, tags=tags,
            ))

        suggested = normalize_name(base)
        if suggested != base:
            issues.append(HealthIssue(
                kind='disk_separator_spacing', severity='warning',
                message=f'File on disk is "{base}" — suggested "{suggested}" '
                        f'(rename via Edit Song)',
                record_index=idx, song_path=path, json_title=title,
                disk_filename=base, suggestion=suggested, fixable=False, tags=tags,
            ))

        if has_typo_error(base):
            issues.append(HealthIssue(
                kind='double_extension', severity='warning',
                message=f'"{base}" ends with two audio extensions',
                record_index=idx, song_path=path, json_title=title,
                disk_filename=base, fixable=False, tags=tags,
            ))

        if include_info:
            if record is None:
                issues.append(HealthIssue(
                    kind='no_entry', severity='info',
                    message='Song has no entry in the JSON yet (Assign Tags creates one)',
                    song_path=path, disk_filename=base, fixable=False,
                ))
            elif not tags:
                issues.append(HealthIssue(
                    kind='no_tags', severity='info',
                    message='JSON entry exists but has no tags',
                    record_index=idx, song_path=path, json_title=title,
                    disk_filename=base, fixable=False,
                ))
            if lyrics_store is not None and lyrics_store.get_folder() \
                    and not lyrics_store.has_lyrics(path):
                info = None
                miss_key = getattr(lyrics_store, 'miss_key_for_song', None)
                if miss_key is not None:
                    info = lyrics_store.miss_info(miss_key(songs_by_path.get(path, {})))
                if info:
                    import datetime as _dt
                    when = _dt.datetime.fromtimestamp(float(info.get('ts', 0))).strftime('%Y-%m-%d')
                    sources = ', '.join(info.get('sources', [])) or 'lrclib'
                    issues.append(HealthIssue(
                        kind='lyrics_not_found', severity='info',
                        message=f'No source had lyrics ({sources}) on {when} — remembered; '
                                f'select the song and press Fetch to retry',
                        record_index=idx, song_path=path, json_title=title,
                        disk_filename=base, fixable=False, tags=tags,
                    ))
                else:
                    issues.append(HealthIssue(
                        kind='no_lyrics', severity='info',
                        message='No .lrc file for this song',
                        record_index=idx, song_path=path, json_title=title,
                        disk_filename=base, fixable=False, tags=tags,
                    ))

    # Duplicates: several records resolved to the same file.
    dropped_by_merge: set[int] = set()
    for dup in getattr(store, 'duplicate_records', []) or []:
        indexes = [i for i in dup.get('record_indexes', []) if 0 <= i < len(records)]
        if len(indexes) < 2:
            continue
        path = dup.get('path', '')
        base = os.path.basename(path)
        primary = dup.get('primary_index', indexes[0])
        if primary not in indexes:
            primary = indexes[0]
        drops = [i for i in indexes if i != primary]
        dropped_by_merge.update(drops)
        titles = ', '.join(f'"{records[i].get("title", "")}"' for i in indexes)
        merged_tags = _union_tags(records[i].get('tags', []) for i in indexes)
        issues.append(HealthIssue(
            kind='duplicate_entry', severity='error',
            message=f'{len(indexes)} entries point to "{base}": {titles} — '
                    f'merge into one entry with tags [{", ".join(merged_tags) or "none"}]',
            record_index=primary, song_path=path,
            json_title=records[primary].get('title', ''), disk_filename=base,
            suggestion=base, fixable=True, tags=merged_tags,
            extra={'keep_index': primary, 'drop_indexes': drops},
        ))

    # Unmatched records: duplicates among themselves, then missing files.
    missing = list(getattr(store, 'missing_song_records', []) or [])
    ghost_groups: dict[str, list[int]] = {}
    for entry in missing:
        idx = entry.get('record_index', -1)
        if 0 <= idx < len(records):
            ghost_groups.setdefault(loose_key(records[idx].get('title', '')), []).append(idx)
    for key, indexes in ghost_groups.items():
        if len(indexes) < 2 or not key:
            continue
        primary = indexes[0]
        drops = indexes[1:]
        dropped_by_merge.update(drops)
        merged_tags = _union_tags(records[i].get('tags', []) for i in indexes)
        issues.append(HealthIssue(
            kind='duplicate_entry', severity='error',
            message=f'{len(indexes)} unmatched entries with the same name — merge '
                    f'into one with tags [{", ".join(merged_tags) or "none"}]',
            record_index=primary, json_title=records[primary].get('title', ''),
            suggestion=records[primary].get('title', ''), fixable=True,
            tags=merged_tags, extra={'keep_index': primary, 'drop_indexes': drops},
        ))

    disk_keys = list(disk_loose)
    disk_stem_keys = list(disk_loose_stem)
    for entry in missing:
        idx = entry.get('record_index', -1)
        if not (0 <= idx < len(records)) or idx in dropped_by_merge:
            continue
        record = records[idx]
        title = record.get('title', '')
        tags = list(record.get('tags', []))
        reason = entry.get('reason', '')
        if reason == 'ambiguous_match':
            cands = ', '.join(entry.get('candidates', [])[:4])
            issues.append(HealthIssue(
                kind='missing_file', severity='error',
                message=f'Ambiguous — several files could match: {cands}',
                record_index=idx, json_title=title, fixable=False, tags=tags,
            ))
            continue
        target_path = ''
        key = loose_key(title)
        stem = loose_stem_key(title)
        close = difflib.get_close_matches(key, disk_keys, n=1, cutoff=0.85) if key else []
        if close and len(disk_loose[close[0]]) == 1:
            target_path = disk_loose[close[0]][0]
        else:
            close = difflib.get_close_matches(stem, disk_stem_keys, n=1, cutoff=0.85) if stem else []
            if close and len(disk_loose_stem[close[0]]) == 1:
                target_path = disk_loose_stem[close[0]][0]
        if target_path:
            base = os.path.basename(target_path)
            issues.append(HealthIssue(
                kind='missing_file', severity='error',
                message=f'No file named "{title}" — closest on disk: "{base}" (relink?)',
                record_index=idx, song_path=target_path, json_title=title,
                disk_filename=base, suggestion=base, fixable=True,
                default_checked=False, tags=tags, extra={'relink': True},
            ))
        else:
            issues.append(HealthIssue(
                kind='missing_file', severity='error',
                message=f'No file named "{title}" in the music folder',
                record_index=idx, json_title=title, fixable=False, tags=tags,
            ))

    # Invalid tags (already computed by the reconcile).
    for entry in getattr(store, 'invalid_tag_records', []) or []:
        idx = entry.get('record_index', -1)
        bad = list(entry.get('tags', []))
        issues.append(HealthIssue(
            kind='invalid_tag', severity='warning',
            message=f'Unsupported tag(s): {", ".join(bad)} — use "Fix Unsupported Tags"',
            record_index=idx, song_path=entry.get('matched_path', ''),
            json_title=entry.get('title', ''), fixable=False, tags=bad,
        ))

    # Lyrics folder: orphan .lrc files that loosely belong to a song.
    if lyrics_store is not None and lyrics_store.get_folder():
        lyricless_by_key: dict[str, list[str]] = {}
        for path in real_paths:
            if not lyrics_store.has_lyrics(path):
                lyricless_by_key.setdefault(loose_key(Path(path).stem), []).append(path)
        for orphan in lyrics_store.orphan_lrc_files:
            key = loose_key(Path(orphan).stem)
            owners = lyricless_by_key.get(key, [])
            if len(owners) == 1:
                song_path = owners[0]
                target = lyrics_store.lrc_path_for(song_path)
                issues.append(HealthIssue(
                    kind='lyrics_drift', severity='warning',
                    message=f'"{os.path.basename(orphan)}" belongs to '
                            f'"{os.path.basename(song_path)}" but the name differs — '
                            f'rename to "{os.path.basename(target)}"',
                    song_path=song_path, disk_filename=os.path.basename(song_path),
                    suggestion=os.path.basename(target), fixable=False,
                    extra={'lrc_path': orphan, 'target_lrc': target},
                ))
            elif include_info:
                issues.append(HealthIssue(
                    kind='lyrics_orphan', severity='info',
                    message=f'"{os.path.basename(orphan)}" matches no song',
                    fixable=False, extra={'lrc_path': orphan},
                ))

    issues.sort(key=lambda i: i.sort_key)
    return issues


def _union_tags(tag_lists: Iterable[Iterable[str]]) -> list[str]:
    seen: list[str] = []
    for tags in tag_lists:
        for tag in tags or []:
            if tag and tag not in seen:
                seen.append(tag)
    return seen


# ── fixer ────────────────────────────────────────────────────────────────────

def apply_json_fixes(store, issues: Iterable[HealthIssue], songs_by_path: dict[str, dict]) -> dict:
    """Apply the JSON-only fixes in ``issues`` to ``store`` in place.

    Records are always *updated*, never cloned: the record count can only
    stay the same or shrink (merges). The caller persists with ``store.save()``.
    """
    counts = {'titles_synced': 0, 'duplicates_merged': 0, 'relinked': 0}
    records = store.records
    before = len(records)

    # Resolve every index to the record object first — merges shift indexes.
    def rec(i):
        return records[i] if 0 <= i < len(records) else None

    merges: list[tuple[dict, list[dict]]] = []
    syncs: list[tuple[dict, str]] = []
    relinks: list[tuple[dict, str]] = []
    for issue in issues:
        if not issue.fixable:
            continue
        if issue.kind == 'duplicate_entry':
            keep = rec(issue.extra.get('keep_index', -1))
            drops = [r for r in (rec(i) for i in issue.extra.get('drop_indexes', [])) if r is not None]
            if keep is not None and drops:
                merges.append((keep, drops))
        elif issue.kind == 'title_drift':
            r = rec(issue.record_index)
            if r is not None and issue.song_path:
                syncs.append((r, issue.song_path))
        elif issue.kind == 'missing_file' and issue.extra.get('relink'):
            r = rec(issue.record_index)
            if r is not None and issue.song_path:
                relinks.append((r, issue.song_path))

    dropped: set[int] = set()
    for keep, drops in merges:
        live = [d for d in drops if id(d) not in dropped and d is not keep]
        if not live:
            continue
        if store.merge_record_objects(keep, live):
            counts['duplicates_merged'] += 1
            dropped.update(id(d) for d in live)

    for record, song_path in syncs:
        if id(record) in dropped:
            continue
        song = songs_by_path.get(song_path, {'filename': os.path.basename(song_path)})
        if store.set_record_title_from_disk_obj(record, song_path, song):
            counts['titles_synced'] += 1

    for record, song_path in relinks:
        if id(record) in dropped:
            continue
        song = songs_by_path.get(song_path, {'filename': os.path.basename(song_path)})
        outcome = store.relink_record_obj(record, song_path, song)
        if outcome == 'merged':
            counts['duplicates_merged'] += 1
            counts['relinked'] += 1
        elif outcome == 'relinked':
            counts['relinked'] += 1

    assert len(store.records) <= before, 'fixes must never add records'
    return counts


def rename_lrc_for_issue(issue: HealthIssue) -> bool:
    """Explicit, per-row action for ``lyrics_drift``: move the orphan .lrc to
    the canonical name. Returns True on success."""
    src = issue.extra.get('lrc_path', '')
    dst = issue.extra.get('target_lrc', '')
    if not src or not dst or not os.path.isfile(src):
        return False
    if os.path.exists(dst):
        return False
    try:
        os.replace(src, dst)
    except OSError:
        return False
    return True
