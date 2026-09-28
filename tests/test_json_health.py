import json
import os
import tempfile
import unittest
from pathlib import Path

from json_health import (
    apply_json_fixes, build_health_report, describe_drift, has_typo_error,
    loose_key, loose_stem_key, normalize_name, summarize,
)
from lyrics_store import LyricsStore
from playlist_store import PlaylistAssignmentStore


def _song(root, name, **meta):
    path = os.path.join(root, name)
    song = {'path': path, 'filename': name, 'title': '', 'artist': '', 'album': ''}
    song.update(meta)
    return path, song


class NormalizeNameTests(unittest.TestCase):
    def test_separator_spacing_is_repaired(self):
        cases = {
            'Artist -Song.mp3': 'Artist - Song.mp3',
            'Artist- Song.mp3': 'Artist - Song.mp3',
            'Artist  -  Song.mp3': 'Artist - Song.mp3',
            'Artist – Song.mp3': 'Artist - Song.mp3',
            'Artist—Song.mp3': 'Artist - Song.mp3',
            'title -Song': 'title - Song',
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalize_name(raw), expected)

    def test_word_internal_hyphens_are_untouched(self):
        for name in ('Lo-Fi Beats.mp3', 'Jay-Z - 99 Problems.mp3', 'AC-DC - TNT.flac'):
            self.assertEqual(normalize_name(name), name)

    def test_whitespace_and_extension_cleanup(self):
        self.assertEqual(normalize_name('  Song .mp3 '), 'Song.mp3')
        self.assertEqual(normalize_name('A   B.mp3'), 'A B.mp3')

    def test_nfd_becomes_nfc_and_escapes_are_decoded(self):
        nfd = 'Café.mp3'
        self.assertEqual(normalize_name(nfd), 'Café.mp3')
        self.assertEqual(normalize_name('Caf\\u00e9.mp3'), 'Café.mp3')

    def test_loose_keys(self):
        self.assertEqual(loose_key('AC dc - X.mp3'), loose_key('AC DC - X.mp3'))
        self.assertEqual(loose_key('Artist -Song.mp3'), loose_key('Artist - Song.mp3'))
        self.assertEqual(loose_stem_key('Artist - Song.mp3'), loose_stem_key('Artist - Song'))
        self.assertEqual(loose_stem_key('Mr. Blue Sky'), 'mr. blue sky')

    def test_describe_drift_and_typo(self):
        self.assertEqual(describe_drift('AC dc - X.mp3', 'AC DC - X.mp3'), 'letter case differs')
        self.assertEqual(describe_drift('Artist -Song.mp3', 'Artist - Song.mp3'), 'spacing differs')
        self.assertEqual(describe_drift('Song', 'Song.mp3'), 'extension missing')
        self.assertEqual(describe_drift('Same.mp3', 'Same.mp3'), '')
        self.assertTrue(has_typo_error('Foo.mp3.mp3'))
        self.assertTrue(has_typo_error('Bar.flac.mp3'))
        self.assertFalse(has_typo_error('Foo.mp3'))


class HealthReportTests(unittest.TestCase):
    def _store(self, payload, library_root='/music'):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        path = Path(tmpdir.name) / 'music.json'
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        store = PlaylistAssignmentStore(str(path), library_root=library_root)
        store.load()
        return store, path

    def test_case_drift_is_matched_loosely_and_reported(self):
        store, _ = self._store({'version': 2, 'songs': [
            {'title': 'AC dc - Thunderstruck.mp3', 'tags': ['heavy']},
        ]})
        path, song = _song('/music', 'AC DC - Thunderstruck.mp3')
        result = store.reconcile({path: song}, '/music', ['heavy'])

        # case-only drift is caught by the case-folded exact tier already;
        # what matters is that it is now *reported* instead of hidden.
        self.assertEqual(result['match_source_counts']['filename'], 1)
        self.assertEqual(store.get_tags(path), ['heavy'])
        self.assertFalse(store.missing_song_records)

        issues = build_health_report(store, {path: song})
        drift = [i for i in issues if i.kind == 'title_drift']
        self.assertEqual(len(drift), 1)
        self.assertEqual(drift[0].suggestion, 'AC DC - Thunderstruck.mp3')
        self.assertTrue(drift[0].fixable)
        self.assertIn('letter case', drift[0].message)

    def test_spacing_drift_is_matched_loosely(self):
        store, _ = self._store({'version': 2, 'songs': [
            {'title': 'Artist -Song.mp3', 'tags': ['chill']},
        ]})
        path, song = _song('/music', 'Artist - Song.mp3')
        result = store.reconcile({path: song}, '/music', ['chill'])
        self.assertEqual(result['match_source_counts']['loose'], 1)
        self.assertEqual(store.get_tags(path), ['chill'])
        issues = build_health_report(store, {path: song})
        self.assertEqual([i.kind for i in issues if i.severity == 'error'], ['title_drift'])

    def test_missing_extension_drift(self):
        store, _ = self._store({'version': 2, 'songs': [
            {'title': 'Artist - Song', 'tags': ['chill']},
        ]})
        path, song = _song('/music', 'Artist - Song.mp3')
        store.reconcile({path: song}, '/music', ['chill'])
        issues = build_health_report(store, {path: song})
        drift = [i for i in issues if i.kind == 'title_drift']
        self.assertEqual(len(drift), 1)
        self.assertIn('extension missing', drift[0].message)

    def test_duplicates_are_reported_not_shadowed(self):
        # The consumer project appended a fresh empty entry for the re-cased
        # file; the tagged original must not be silently shadowed.
        store, _ = self._store({'version': 2, 'songs': [
            {'title': 'AC dc - X.mp3', 'tags': ['heavy', 'driving']},
            {'title': 'AC DC - X.mp3', 'tags': []},
        ]})
        path, song = _song('/music', 'AC DC - X.mp3')
        store.reconcile({path: song}, '/music', ['heavy', 'driving'])

        self.assertEqual(len(store.duplicate_records), 1)
        dup = store.duplicate_records[0]
        self.assertEqual(sorted(dup['record_indexes']), [0, 1])
        # primary = the one whose title already equals the disk name
        self.assertEqual(dup['primary_index'], 1)

        issues = build_health_report(store, {path: song})
        dups = [i for i in issues if i.kind == 'duplicate_entry']
        self.assertEqual(len(dups), 1)
        self.assertEqual(dups[0].tags, ['heavy', 'driving'])

    def test_end_to_end_recase_scenario_fix_and_save(self):
        store, json_path = self._store({'version': 2, 'songs': [
            {'title': 'AC dc - X.mp3', 'tags': ['heavy', 'driving']},
            {'title': 'AC DC - X.mp3', 'tags': []},
            {'title': 'Artist -Song.mp3', 'tags': ['chill']},
            {'title': 'Ghost.mp3', 'tags': ['ost']},
        ]})
        p1, s1 = _song('/music', 'AC DC - X.mp3')
        p2, s2 = _song('/music', 'Artist - Song.mp3')
        p3, s3 = _song('/music', 'Foo -Bar.mp3')
        songs = {p1: s1, p2: s2, p3: s3}
        store.reconcile(songs, '/music', ['heavy', 'driving', 'chill', 'ost'])

        issues = build_health_report(store, songs)
        kinds = sorted(i.kind for i in issues if i.severity != 'info')
        self.assertEqual(kinds, ['disk_separator_spacing', 'duplicate_entry',
                                 'missing_file', 'title_drift'])
        spacing = next(i for i in issues if i.kind == 'disk_separator_spacing')
        self.assertFalse(spacing.fixable)              # flag only, never rename
        self.assertEqual(spacing.suggestion, 'Foo - Bar.mp3')

        before = len(store.records)
        counts = apply_json_fixes(store, issues, songs)
        self.assertEqual(counts['duplicates_merged'], 1)
        self.assertEqual(counts['titles_synced'], 1)
        self.assertLess(len(store.records), before)   # never clones
        store.save()

        payload = json.loads(json_path.read_text(encoding='utf-8'))
        titles = {s['title']: s['tags'] for s in payload['songs']}
        self.assertEqual(titles, {
            'AC DC - X.mp3': ['heavy', 'driving'],
            'Artist - Song.mp3': ['chill'],
            'Ghost.mp3': ['ost'],
        })
        # strict contract: every matched title is byte-for-byte a disk name
        disk = {os.path.basename(p) for p in songs}
        for title in titles:
            if title != 'Ghost.mp3':
                self.assertIn(title, disk)

    def test_relink_suggestion_for_missing_entry(self):
        store, _ = self._store({'version': 2, 'songs': [
            {'title': 'Artist - Song (Remastered).mp3', 'tags': ['chill']},
        ]})
        path, song = _song('/music', 'Artist - Song (Remaster).mp3')
        store.reconcile({path: song}, '/music', ['chill'])
        issues = build_health_report(store, {path: song})
        missing = [i for i in issues if i.kind == 'missing_file']
        self.assertEqual(len(missing), 1)
        self.assertTrue(missing[0].fixable)
        self.assertFalse(missing[0].default_checked)
        self.assertEqual(missing[0].suggestion, 'Artist - Song (Remaster).mp3')

        counts = apply_json_fixes(store, issues, {path: song})
        self.assertEqual(counts['relinked'], 1)
        self.assertEqual(store.records[0]['title'], 'Artist - Song (Remaster).mp3')
        self.assertEqual(store.get_tags(path), ['chill'])

    def test_relink_into_existing_record_merges(self):
        store, _ = self._store({'version': 2, 'songs': [
            {'title': 'Artist - Song (Remastered).mp3', 'tags': ['chill']},
            {'title': 'Artist - Song (Remaster).mp3', 'tags': ['gym']},
        ]})
        path, song = _song('/music', 'Artist - Song (Remaster).mp3')
        store.reconcile({path: song}, '/music', ['chill', 'gym'])
        issues = build_health_report(store, {path: song})
        apply_json_fixes(store, issues, {path: song})
        self.assertEqual(len(store.records), 1)
        self.assertEqual(store.records[0]['tags'], ['gym', 'chill'])

    def test_create_records_adopts_unmatched_record_instead_of_cloning(self):
        store, _ = self._store({'version': 2, 'songs': [
            {'title': 'Artist - Song (Live).mp3', 'tags': ['sing']},
        ]})
        # a *different* file only, so the record stays unbound after reconcile
        p_other, s_other = _song('/music', 'Other.mp3')
        store.reconcile({p_other: s_other}, '/music', ['sing'])
        self.assertEqual(len(store.missing_song_records), 1)

        # the real file re-appears re-cased; Assign Tags asks to "create"
        path, song = _song('/music', 'artist - song (live).mp3')
        created = store.create_records_for_paths([path], {path: song, p_other: s_other})
        self.assertEqual(created, 1)
        self.assertEqual(len(store.records), 1)          # adopted, not cloned
        self.assertEqual(store.records[0]['title'], 'artist - song (live).mp3')
        self.assertEqual(store.get_tags(path), ['sing'])

    def test_lyrics_drift_and_no_lyrics(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        music = os.path.join(tmp.name, 'music')
        lyrics = os.path.join(tmp.name, 'lyrics')
        os.makedirs(music)
        os.makedirs(lyrics)
        Path(lyrics, 'artist - song.lrc').write_text('[00:01.00]hi\n')
        path, song = _song(music, 'Artist - Song.mp3')
        p2, s2 = _song(music, 'Other.mp3')
        songs = {path: song, p2: s2}
        ls = LyricsStore(lyrics)
        ls.scan(songs)
        store = PlaylistAssignmentStore(library_root=music)
        store.records = []
        store.reconcile(songs, music, [])

        issues = build_health_report(store, songs, lyrics_store=ls)
        drift = [i for i in issues if i.kind == 'lyrics_drift']
        self.assertEqual(len(drift), 1)
        self.assertEqual(drift[0].suggestion, 'Artist - Song.lrc')
        self.assertEqual(sum(1 for i in issues if i.kind == 'no_lyrics'), 2)
        counts = summarize(issues)
        self.assertEqual(counts['warning'], 1)

    def test_summary_mentions_duplicates(self):
        store, _ = self._store({'version': 2, 'songs': [
            {'title': 'a.mp3', 'tags': []}, {'title': 'A.mp3', 'tags': ['x']},
        ]})
        path, song = _song('/music', 'A.mp3')
        store.reconcile({path: song}, '/music', ['x'])
        self.assertIn('duplicate', store.summary())
        self.assertEqual(store.get_tags(path), ['x'])


if __name__ == '__main__':
    unittest.main()
