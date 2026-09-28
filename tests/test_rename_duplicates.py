"""
Regression test: renaming a file that has an unmerged duplicate JSON pair
must fold the duplicate in, never leave it behind.

A file can have two JSON records resolved to it at once — a case/spacing-
drifted pair that hasn't been merged in Sync Check yet (e.g. "AC dc - X.mp3"
and "AC DC - X.mp3" both matching the same real "AC DC - X.mp3"). Only one
of the two is ever "the" bound record. Renaming that file used to update
only the bound record; the other, shadowed duplicate was left completely
untouched — and since its title no longer matched *any* file once the real
one moved, it reappeared on the very next reconcile as a brand-new
"missing" ghost entry under its old name. From the list, that looks exactly
like "the old one stayed and a new one got created", even though on disk
only a rename ever happened — never a new file, never a new record.

Also covers the ``list.remove()`` (value-equality) trap in
``merge_record_objects`` — the same class of bug fixed for the Missing
Entries deletion — since the merge here can hit two content-identical
records.
"""
import json
import os
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QMessageBox

from dialogs import SongSequencerDialog
from tests.offscreen_harness import boot


class RenameDuplicateTests(unittest.TestCase):
    def setUp(self):
        self.app, self.win, self.wait, self.ready, paths = boot(theme='light')
        self.music, self.lyrics, self.json_path = paths
        self.addCleanup(self._teardown_window)

    def _teardown_window(self):
        timer = getattr(self.win, '_reconcile_debounce', None)
        if timer is not None:
            timer.stop()
        self.win.close()
        worker = getattr(self.win, '_reconcile_worker', None)
        if worker is not None and worker.isRunning():
            worker.wait(1000)

    def _open_edit_song_and_rename(self, path: str, new_filename: str):
        """Drive the real Edit Song dialog end-to-end for one rename, the
        same path a user takes: field text -> Save -> Close."""
        def drive():
            dlg = next(w for w in self.app.topLevelWidgets()
                      if isinstance(w, SongSequencerDialog))
            with patch.object(QMessageBox, 'warning'):
                dlg._filename_edit.setText(new_filename)
                dlg._on_save()
            QTimer.singleShot(50, dlg._on_close)
        QTimer.singleShot(30, drive)
        self.win._open_song_editor(start_path=path)
        self.wait(self.ready, 10)

    def test_renaming_a_duplicate_matched_file_merges_instead_of_orphaning(self):
        # Fixture already has this exact duplicate pair matched to
        # "AC DC - X.mp3": 'AC dc - X.mp3' (tags: heavy, driving) and
        # 'AC DC - X.mp3' (no tags) — both resolve to the same real file.
        path = next(p for p in self.win._songs if p.endswith('AC DC - X.mp3'))
        self.assertEqual(len(self.win._playlist_store.duplicate_records), 1)
        before_records = len(self.win._playlist_store.records)
        before_ghosts = len(self.win._ghost_paths)

        self._open_edit_song_and_rename(path, 'Renamed Track.mp3')

        # Exactly a rename: old name gone, new name present, no extra files.
        files = set(os.listdir(self.music))
        self.assertNotIn('AC DC - X.mp3', files)
        self.assertNotIn('AC dc - X.mp3', files)
        self.assertIn('Renamed Track.mp3', files)

        # The duplicate must be merged away, not left as an orphaned ghost —
        # exactly one fewer record than before, and no new "missing" entry.
        self.assertEqual(len(self.win._playlist_store.records), before_records - 1)
        self.assertEqual(len(self.win._ghost_paths), before_ghosts)
        self.assertEqual(self.win._playlist_store.duplicate_records, [])

        payload = json.load(open(self.json_path))
        titles = [s['title'] for s in payload['songs']]
        self.assertEqual(titles.count('Renamed Track.mp3'), 1)
        self.assertNotIn('AC dc - X.mp3', titles)
        self.assertNotIn('AC DC - X.mp3', titles)

        # Tags from BOTH duplicates survive the merge (union, not dropped).
        merged = next(s for s in payload['songs'] if s['title'] == 'Renamed Track.mp3')
        self.assertEqual(set(merged['tags']), {'heavy', 'driving'})

    def test_rename_after_a_failed_attempt_still_merges_correctly(self):
        """The exact reported scenario: first attempt collides with a
        different real song and is rejected; the user then renames to a
        second, unique name. The duplicate must still be folded in, not
        just abandoned because the first attempt failed."""
        path = next(p for p in self.win._songs if p.endswith('AC DC - X.mp3'))
        before_records = len(self.win._playlist_store.records)

        # Attempt 1: collides with "Artist - Song.mp3", a real, different song.
        self._open_edit_song_and_rename(path, 'Artist - Song.mp3')
        self.assertTrue(os.path.isfile(os.path.join(self.music, 'AC DC - X.mp3')))
        self.assertEqual(len(self.win._playlist_store.records), before_records)

        # Attempt 2: a unique name.
        path = next(p for p in self.win._songs if p.endswith('AC DC - X.mp3'))
        self._open_edit_song_and_rename(path, 'Artist - Song 2.mp3')

        files = set(os.listdir(self.music))
        self.assertNotIn('AC DC - X.mp3', files)
        self.assertIn('Artist - Song 2.mp3', files)
        self.assertEqual(len(self.win._playlist_store.records), before_records - 1)
        self.assertEqual(len(self.win._playlist_store.duplicate_records), 0)

    def test_merge_record_objects_removes_by_identity_not_value(self):
        """Direct unit check for the list.remove() (value-equality) trap:
        two content-identical records must not cause the wrong one to be
        removed when merging."""
        store = self.win._playlist_store
        a = {'title': 'Same.mp3', 'tags': []}
        b = {'title': 'Same.mp3', 'tags': []}   # == a, but a different object
        keep = {'title': 'Keeper.mp3', 'tags': ['chill']}
        store.records = [a, b, keep]

        store.merge_record_objects(keep, [b])

        self.assertEqual(len(store.records), 2)
        self.assertIn(a, store.records)
        self.assertTrue(any(r is a for r in store.records),
                        'the wrong (value-equal) record was removed instead of b')
        self.assertNotIn(b, [r for r in store.records if r is b])


if __name__ == '__main__':
    unittest.main()
