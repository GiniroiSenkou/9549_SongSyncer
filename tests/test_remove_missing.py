"""
Regression test for "Missing Entries keep reappearing after deleting them".

MainWindow._on_remove_missing_records used to locate a selected ghost's
record via ``self._playlist_store.records.index(record)`` — Python
``list.index()`` finds a *value*-equal match, not the specific object. Two
ghost records with identical content (same title, same tags — very common:
two untagged missing entries, or a duplicate pair after a merge) are `==`
to each other despite being different objects, so deleting the *second* one
actually deleted the *first*, leaving the one the user meant to delete
completely untouched. It then showed up as "missing" again on the very
next reconcile, looking exactly like the deletion never took effect.

The fix uses PlaylistAssignmentStore.record_index() (an identity/`is`
lookup, already used elsewhere for the same reason) instead.
"""
import os
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtWidgets import QMessageBox

from tests.offscreen_harness import boot


class RemoveMissingEntryTests(unittest.TestCase):
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

    def _set_ghost_records(self, records):
        store = self.win._playlist_store
        store.records = [store._sanitize_record(r) for r in records]
        self.win._request_playlist_reconcile()
        self.wait(self.ready, 10)

    def _ghost_path_for_index(self, idx):
        for path in self.win._ghost_paths:
            if self.win._playlist_store.record_index(
                self.win._playlist_store._path_to_record[path]
            ) == idx:
                return path
        raise AssertionError(f'no ghost row for record index {idx}')

    def _delete_by_index_and_check_survivor(self, delete_idx: int, keep_idx: int):
        """Two ghosts with IDENTICAL {title, tags} — indistinguishable by
        content, which is the whole point: only object *identity* can prove
        the right one was removed. list.index() (the bug) matches by value,
        so it always finds index 0 first regardless of which one is
        selected — this only passes if the surviving record is truly the
        one that was never selected for deletion."""
        self._set_ghost_records([
            {'title': 'Same Ghost.mp3', 'tags': []},
            {'title': 'Same Ghost.mp3', 'tags': []},
        ])
        records = self.win._playlist_store.records
        keep_id = id(records[keep_idx])

        target_path = self._ghost_path_for_index(delete_idx)
        self.win._table.clearSelection()
        row = self.win._path_to_cover_item[target_path].row()
        self.win._table.item(row, 0).setSelected(True)
        self.assertEqual(self.win._selected_paths(), [target_path])

        with patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes):
            self.win._on_remove_missing_records()
        self.wait(self.ready, 10)

        records = self.win._playlist_store.records
        self.assertEqual(len(records), 1)
        self.assertEqual(id(records[0]), keep_id,
                         'the surviving record is not the one that was left '
                         'unselected — the wrong (selected) one was deleted, '
                         'so it will reappear as "missing" on the next reconcile')

        # And the regression itself: re-reconciling must not resurrect it.
        self.win._request_playlist_reconcile()
        self.wait(self.ready, 10)
        self.assertEqual(len(self.win._playlist_store.records), 1)
        self.assertEqual(len(self.win._ghost_paths), 1)

    def test_deleting_the_second_of_two_identical_ghosts_keeps_the_first(self):
        self._delete_by_index_and_check_survivor(delete_idx=1, keep_idx=0)

    def test_deleting_the_first_of_two_identical_ghosts_keeps_the_second(self):
        self._delete_by_index_and_check_survivor(delete_idx=0, keep_idx=1)

    def test_multi_select_delete_removes_exactly_the_selected_records(self):
        self._set_ghost_records([
            {'title': 'Same Ghost.mp3', 'tags': []},
            {'title': 'Same Ghost.mp3', 'tags': []},
            {'title': 'Keep Me.mp3', 'tags': ['chill']},
        ])
        self.assertEqual(len(self.win._playlist_store.records), 3)

        p0 = self._ghost_path_for_index(0)
        p1 = self._ghost_path_for_index(1)
        # Select both rows together. selectRow() replaces rather than
        # extends the selection here, so select each row's item directly
        # (additive, same as a ctrl-click) instead.
        self.win._table.clearSelection()
        for path in (p0, p1):
            row = self.win._path_to_cover_item[path].row()
            self.win._table.item(row, 0).setSelected(True)
        selected = set(self.win._selected_paths())
        self.assertEqual(selected, {p0, p1})

        with patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes):
            self.win._on_remove_missing_records()
        self.wait(self.ready, 10)

        self.assertEqual(len(self.win._playlist_store.records), 1)
        self.assertEqual(self.win._playlist_store.records[0]['title'], 'Keep Me.mp3')


if __name__ == '__main__':
    unittest.main()
