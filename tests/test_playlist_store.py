import json
import tempfile
import unittest
from pathlib import Path

from playlist_store import PlaylistAssignmentStore


class PlaylistAssignmentStoreTests(unittest.TestCase):
    def _load_store(self, payload, library_root='/music'):
        tmpdir = tempfile.TemporaryDirectory()
        path = Path(tmpdir.name) / 'music.json'
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        store = PlaylistAssignmentStore(str(path), library_root=library_root)
        store.load()
        self.addCleanup(tmpdir.cleanup)
        return store, path

    def test_matches_exact_relative_path_first(self):
        store = PlaylistAssignmentStore(library_root='/music')
        store.records = [{
            'path': 'Album/Artist - Song.mp3',
            'filename': 'Artist - Song.mp3',
            'title': 'Song',
            'tags': ['ITA'],
        }]
        songs = {
            '/music/Album/Artist - Song.mp3': {
                'path': '/music/Album/Artist - Song.mp3',
                'filename': 'Artist - Song.mp3',
                'title': 'Different Embedded Title',
            }
        }

        result = store.reconcile(songs, '/music', ['ITA'])

        self.assertEqual(store.get_tags('/music/Album/Artist - Song.mp3'), ['ITA'])
        self.assertEqual(result['match_source_counts']['path'], 1)
        self.assertFalse(store.mismatch_records)

    def test_matches_filename_when_song_is_in_subfolder(self):
        store = PlaylistAssignmentStore(library_root='/music')
        store.records = [{
            'path': 'Artist - Song.mp3',
            'filename': 'Artist - Song.mp3',
            'title': 'Song',
            'tags': ['ITA'],
        }]
        songs = {
            '/music/Sub/Artist - Song.mp3': {
                'path': '/music/Sub/Artist - Song.mp3',
                'filename': 'Artist - Song.mp3',
                'title': 'Different Embedded Title',
            }
        }

        result = store.reconcile(songs, '/music', ['ITA'])

        self.assertEqual(store.get_tags('/music/Sub/Artist - Song.mp3'), ['ITA'])
        self.assertEqual(result['match_source_counts']['basename'], 1)
        self.assertFalse(store.mismatch_records)

    def test_title_difference_does_not_create_mismatch_when_filename_matches(self):
        store = PlaylistAssignmentStore(library_root='/music')
        store.records = [{
            'path': '',
            'filename': 'Artist - Song.mp3',
            'title': 'JSON Title',
            'tags': ['ITA'],
        }]
        songs = {
            '/music/Artist - Song.mp3': {
                'path': '/music/Artist - Song.mp3',
                'filename': 'Artist - Song.mp3',
                'title': 'Embedded Title',
            }
        }

        store.reconcile(songs, '/music', ['ITA'])

        self.assertEqual(store.get_tags('/music/Artist - Song.mp3'), ['ITA'])
        self.assertFalse(store.mismatch_records)

    def test_unicode_normalization_matches_equivalent_filenames(self):
        store = PlaylistAssignmentStore(library_root='/music')
        store.records = [{
            'path': '',
            'filename': 'Cafe\u0301.mp3',
            'title': 'Cafe',
            'tags': ['ITA'],
        }]
        songs = {
            '/music/Café.mp3': {
                'path': '/music/Café.mp3',
                'filename': 'Café.mp3',
                'title': 'Cafe',
            }
        }

        store.reconcile(songs, '/music', ['ITA'])

        self.assertEqual(store.get_tags('/music/Café.mp3'), ['ITA'])
        self.assertFalse(store.mismatch_records)

    def test_title_only_escaped_unicode_filename_matches_song(self):
        payload = {
            'version': 2,
            'songs': [{
                'title': '07 - \\u6226\\u95d8\\uff01\\u91ce\\u751f\\u30dd\\u30b1\\u30e2\\u30f3\\uff08\\u30ab\\u30f3\\u30c8\\u30fc\\uff09.mp3',
                'tags': ['Pokemon'],
            }],
        }
        store, _ = self._load_store(payload)
        songs = {
            '/music/07 - 戦闘！野生ポケモン（カントー）.mp3': {
                'path': '/music/07 - 戦闘！野生ポケモン（カントー）.mp3',
                'filename': '07 - 戦闘！野生ポケモン（カントー）.mp3',
                'title': 'Wild Pokemon Battle',
            }
        }

        result = store.reconcile(songs, '/music', ['Pokemon'])

        self.assertEqual(store.get_tags('/music/07 - 戦闘！野生ポケモン（カントー）.mp3'), ['Pokemon'])
        self.assertEqual(result['match_source_counts']['filename'], 1)
        self.assertFalse(store.mismatch_records)

    def test_title_only_decoded_unicode_filename_matches_song(self):
        store = PlaylistAssignmentStore(library_root='/music')
        store.records = [store._sanitize_record({
            'path': '',
            'filename': '',
            'title': '07 - 戦闘！野生ポケモン（カントー）.mp3',
            'tags': ['Pokemon'],
        })]
        songs = {
            '/music/07 - 戦闘！野生ポケモン（カントー）.mp3': {
                'path': '/music/07 - 戦闘！野生ポケモン（カントー）.mp3',
                'filename': '07 - 戦闘！野生ポケモン（カントー）.mp3',
                'title': 'Wild Pokemon Battle',
            }
        }

        result = store.reconcile(songs, '/music', ['Pokemon'])

        self.assertEqual(store.get_tags('/music/07 - 戦闘！野生ポケモン（カントー）.mp3'), ['Pokemon'])
        self.assertEqual(result['match_source_counts']['filename'], 1)
        self.assertFalse(store.mismatch_records)

    def test_explicit_filename_wins_over_title_filename_fallback(self):
        store = PlaylistAssignmentStore(library_root='/music')
        store.records = [store._sanitize_record({
            'path': '',
            'filename': 'Actual.mp3',
            'title': 'Fallback.mp3',
            'tags': ['ITA'],
        })]
        songs = {
            '/music/Actual.mp3': {
                'path': '/music/Actual.mp3',
                'filename': 'Actual.mp3',
                'title': 'Actual Title',
            },
            '/music/Fallback.mp3': {
                'path': '/music/Fallback.mp3',
                'filename': 'Fallback.mp3',
                'title': 'Fallback Title',
            },
        }

        result = store.reconcile(songs, '/music', ['ITA'])

        self.assertEqual(store.get_tags('/music/Actual.mp3'), ['ITA'])
        self.assertEqual(store.get_tags('/music/Fallback.mp3'), [])
        self.assertEqual(result['match_source_counts']['filename'], 1)
        self.assertEqual(result['match_source_counts']['title_filename'], 0)

    def test_duplicate_filenames_are_reported_as_ambiguous(self):
        store = PlaylistAssignmentStore(library_root='/music')
        store.records = [{
            'path': '',
            'filename': 'Shared.mp3',
            'title': 'Shared',
            'tags': ['ITA'],
        }]
        songs = {
            '/music/A/Shared.mp3': {
                'path': '/music/A/Shared.mp3',
                'filename': 'Shared.mp3',
                'title': 'One',
            },
            '/music/B/Shared.mp3': {
                'path': '/music/B/Shared.mp3',
                'filename': 'Shared.mp3',
                'title': 'Two',
            },
        }

        store.reconcile(songs, '/music', ['ITA'])

        self.assertEqual(len(store.mismatch_records), 1)
        self.assertEqual(store.mismatch_records[0]['reason'], 'ambiguous_match')
        self.assertEqual(store.get_tags('/music/A/Shared.mp3'), [])
        self.assertEqual(store.get_tags('/music/B/Shared.mp3'), [])

    def test_title_only_duplicate_filenames_are_reported_as_ambiguous(self):
        store = PlaylistAssignmentStore(library_root='/music')
        store.records = [store._sanitize_record({
            'path': '',
            'title': 'Shared.mp3',
            'tags': ['ITA'],
        })]
        songs = {
            '/music/A/Shared.mp3': {
                'path': '/music/A/Shared.mp3',
                'filename': 'Shared.mp3',
                'title': 'One',
            },
            '/music/B/Shared.mp3': {
                'path': '/music/B/Shared.mp3',
                'filename': 'Shared.mp3',
                'title': 'Two',
            },
        }

        result = store.reconcile(songs, '/music', ['ITA'])

        self.assertEqual(len(store.mismatch_records), 1)
        self.assertEqual(store.mismatch_records[0]['reason'], 'ambiguous_match')
        self.assertEqual(store.mismatch_records[0]['match_source'], 'filename')
        self.assertEqual(result['match_source_counts']['title_filename'], 0)

    def test_invalid_tags_are_reported_without_blocking_match(self):
        store = PlaylistAssignmentStore(library_root='/music')
        store.records = [{
            'path': '',
            'filename': 'Artist - Song.mp3',
            'title': 'Song',
            'tags': ['ITA', 'BROKEN'],
        }]
        songs = {
            '/music/Artist - Song.mp3': {
                'path': '/music/Artist - Song.mp3',
                'filename': 'Artist - Song.mp3',
                'title': 'Song',
            }
        }

        store.reconcile(songs, '/music', ['ITA'])

        self.assertEqual(store.get_tags('/music/Artist - Song.mp3'), ['ITA', 'BROKEN'])
        self.assertEqual(len(store.invalid_tag_records), 1)
        self.assertFalse(store.mismatch_records)
        self.assertEqual(store.invalid_tag_records[0]['record_index'], 0)

    def test_tag_aliases_are_not_reported_invalid(self):
        store = PlaylistAssignmentStore(library_root='/music')
        store.records = [store._sanitize_record({
            'title': 'Artist - Song.mp3',
            'tags': ['Call of Duty', 'Anime Op/End', 'Hoodie'],
        })]
        songs = {
            '/music/Artist - Song.mp3': {
                'path': '/music/Artist - Song.mp3',
                'filename': 'Artist - Song.mp3',
                'title': 'Song',
            }
        }

        store.reconcile(songs, '/music', ['COD', 'Anime', 'Hoodie'])

        self.assertFalse(store.invalid_tag_records)

    def test_save_persists_canonical_filename_after_title_only_import(self):
        payload = {
            'version': 2,
            'songs': [{
                'title': '07 - \\u6226\\u95d8\\uff01\\u91ce\\u751f\\u30dd\\u30b1\\u30e2\\u30f3\\uff08\\u30ab\\u30f3\\u30c8\\u30fc\\uff09.mp3',
                'tags': ['Pokemon'],
            }],
        }
        store, path = self._load_store(payload)
        songs = {
            '/music/Pokemon/07 - 戦闘！野生ポケモン（カントー）.mp3': {
                'path': '/music/Pokemon/07 - 戦闘！野生ポケモン（カントー）.mp3',
                'filename': '07 - 戦闘！野生ポケモン（カントー）.mp3',
                'title': 'Wild Pokemon Battle',
            }
        }

        store.reconcile(songs, '/music', ['Pokemon'])
        store.save()

        saved = json.loads(path.read_text(encoding='utf-8'))
        song = saved['songs'][0]
        self.assertEqual(set(song.keys()), {'title', 'tags'})
        self.assertEqual(song['title'], '07 - 戦闘！野生ポケモン（カントー）.mp3')
        self.assertEqual(song['tags'], ['Pokemon'])

    def test_update_record_from_song_canonicalizes_record(self):
        store = PlaylistAssignmentStore(library_root='/music')
        store.records = [store._sanitize_record({
            'path': '',
            'filename': 'Shared.mp3',
            'title': 'Shared',
            'tags': ['ITA'],
        })]
        song = {
            'path': '/music/Album/Actual.mp3',
            'filename': 'Actual.mp3',
            'title': 'Actual Song',
        }

        changed = store.update_record_from_song(0, song['path'], song)

        self.assertTrue(changed)
        self.assertEqual(store.records[0]['path'], 'Album/Actual.mp3')
        self.assertEqual(store.records[0]['filename'], 'Actual.mp3')
        self.assertEqual(store.records[0]['title'], 'Actual.mp3')

    def test_remove_invalid_tags_from_record_keeps_only_valid(self):
        store = PlaylistAssignmentStore(library_root='/music')
        store.records = [store._sanitize_record({
            'path': '',
            'filename': 'Artist - Song.mp3',
            'title': 'Song',
            'tags': ['ITA', 'BROKEN', 'Pokemon'],
        })]

        changed = store.remove_invalid_tags_from_record(0, ['ITA', 'Pokemon'])

        self.assertTrue(changed)
        self.assertEqual(store.records[0]['tags'], ['ITA', 'Pokemon'])


if __name__ == '__main__':
    unittest.main()
