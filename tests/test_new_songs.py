"""
Tests for the "New Songs" sidebar section (MainWindow._is_new_song /
_refresh_new_markers): songs on disk with no JSON entry, no cover and no
metadata — i.e. never touched by this app — as distinct from a JSON *sync*
problem (a matched-but-drifted record, or a record whose file went
missing), which lives under Sync Check / Missing Entries instead.

Boots a real MainWindow offscreen via tests.offscreen_harness, so these are
slower than the pure-logic suites; kept in their own module so a plain
`python -m unittest discover` still picks them up.
"""
import os
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from tests.offscreen_harness import boot


class NewSongsTests(unittest.TestCase):
    def setUp(self):
        self.app, self.win, self.wait, self.ready, paths = boot(theme='light')
        self.music, self.lyrics, self.json_path = paths
        self.addCleanup(self._teardown_window)

    def _teardown_window(self):
        # Each test boots its own MainWindow (and its own fixture directory,
        # see offscreen_harness.make_fixture); make sure this one's
        # background threads are fully stopped before the next test's window
        # starts touching a filesystem/QSettings path, rather than relying
        # on a bare .close() (which only hides the window).
        timer = getattr(self.win, '_reconcile_debounce', None)
        if timer is not None:
            timer.stop()
        self.win.close()
        worker = getattr(self.win, '_reconcile_worker', None)
        if worker is not None and worker.isRunning():
            worker.wait(1000)

    def _real_songs(self):
        return {p: s for p, s in self.win._songs.items() if p not in self.win._ghost_paths}

    def test_songs_without_json_entry_and_without_cover_or_metadata_are_new(self):
        # Fixture: 'AC DC - X.mp3' / 'Artist - Song.mp3' have JSON entries;
        # 'Foo -Bar.mp3' / 'Lo-Fi Beats.mp3' don't and carry no metadata.
        new_names = {
            os.path.basename(p) for p, s in self._real_songs().items()
            if self.win._is_new_song(p, s)
        }
        self.assertEqual(new_names, {'Foo -Bar.mp3', 'Lo-Fi Beats.mp3'})

    def test_matched_json_entry_is_never_new_even_if_drifted(self):
        # 'AC DC - X.mp3' has a (drifted-case) JSON record after reconcile —
        # it must never show up as "new", regardless of its Sync Check state.
        path = next(p for p in self._real_songs() if p.endswith('AC DC - X.mp3'))
        self.assertTrue(self.win._playlist_store.has_record(path))
        self.assertFalse(self.win._is_new_song(path, self.win._songs[path]))

    def test_ghost_paths_are_never_new(self):
        for path in self.win._ghost_paths:
            self.assertFalse(self.win._is_new_song(path, self.win._songs[path]))

    def test_sidebar_badge_and_filter_reflect_only_new_songs(self):
        self.win._update_stats()
        # The badge counts up via a short QVariantAnimation (see StatRow);
        # a jump of more than 1 needs an event-loop tick before the label
        # text reflects the new value, a jump of exactly 1 updates
        # synchronously — pump either way so this doesn't depend on which.
        self.wait(lambda: False, 0.15)
        self.assertEqual(self.win._stat_rows_by_filter['new_song']._value.text(), '2')

        self.win._set_song_filter('new_song')
        self.wait(lambda: False, 0.1)
        visible = {
            self.win._table.item(row, 1).text()
            for row in range(self.win._table.rowCount())
            if not self.win._table.isRowHidden(row)
        }
        self.assertEqual(visible, {'Foo -Bar.mp3', 'Lo-Fi Beats.mp3'})

    def test_processed_song_missing_its_json_entry_is_not_new(self):
        # The actual "don't confuse the two" case: a song that already has
        # a cover and metadata (clearly processed before) but has no JSON
        # record for some other reason is a JSON-sync concern (it surfaces
        # as an info-level "not in JSON" note in Sync Check), never "New".
        path = next(
            p for p, s in self._real_songs().items()
            if not self.win._playlist_store.has_record(p)
        )
        song = self.win._songs[path]
        self.assertTrue(self.win._is_new_song(path, song))  # sanity: starts new
        song['has_cover'] = True
        song['title'] = 'Some Title'
        song['artist'] = 'Some Artist'
        self.assertFalse(self.win._is_new_song(path, song))

    def test_new_song_count_is_independent_of_the_sync_issues_count(self):
        # The two sidebar badges must be computed independently — neither
        # count is derived from or inflated by the other.
        self.win._update_stats()
        self.wait(lambda: False, 0.15)   # let the badge count-up animation settle
        new_count = int(self.win._stat_rows_by_filter['new_song']._value.text())
        health_count = int(self.win._stat_rows_by_filter['health']._value.text())
        recomputed_health = sum(1 for i in self.win._health_issues if i.severity != 'info')
        self.assertEqual(new_count, 2)
        self.assertEqual(health_count, recomputed_health)

    def test_filename_is_tinted_and_untints_once_processed(self):
        path = next(
            p for p, s in self._real_songs().items()
            if os.path.basename(p) == 'Lo-Fi Beats.mp3'
        )
        self.win._update_stats()
        fn_item = self.win._table.item(self.win._path_to_cover_item[path].row(), 1)
        green = self.win._theme.get('green', '#34c759')
        self.assertEqual(fn_item.foreground().color().name(), green)
        self.assertTrue(fn_item.toolTip())

        # Processing the song (tagging it creates its JSON entry) must
        # clear both the "new" flag and the tint.
        self.win._playlist_store.create_records_for_paths([path], self.win._songs)
        self.win._playlist_store.set_tags_for_paths([path], self.win._songs, ['chill'], [])
        self.win._update_stats()
        self.wait(lambda: False, 0.15)   # let the badge count-up animation settle

        self.assertFalse(self.win._is_new_song(path, self.win._songs[path]))
        self.assertEqual(fn_item.foreground().color().name(),
                         self.win._theme.get('text', '#1d1d1f'))
        self.assertEqual(fn_item.toolTip(), '')
        self.assertEqual(self.win._stat_rows_by_filter['new_song']._value.text(), '1')


if __name__ == '__main__':
    unittest.main()
