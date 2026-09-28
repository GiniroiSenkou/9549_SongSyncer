"""
Tests for "Fix All Spacing" — a bulk rename with a before/after recap and a
per-row selection, requested as a quicker alternative to fixing each
disk_separator_spacing warning one Edit Song visit at a time.

Covers: the recap dialog's selection/count bookkeeping, conflict detection
(destination already taken, or two rows resolving to the same destination),
and the end-to-end apply path (file renamed, JSON title kept in sync, the
issue disappears from the next Sync Check, and songs that never had a JSON
entry are still renamed without being force-added to the JSON).
"""
import json
import os
import shutil
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtCore import Qt

from dialogs import SpacingFixDialog
from tests.offscreen_harness import boot


class SpacingFixDialogTests(unittest.TestCase):
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

    def _spacing_issues(self):
        return [i for i in self.win._health_issues if i.kind == 'disk_separator_spacing']

    def test_single_spacing_issue_is_selected_by_default(self):
        issues = self._spacing_issues()
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].disk_filename, 'Foo -Bar.mp3')
        self.assertEqual(issues[0].suggestion, 'Foo - Bar.mp3')

        dlg = SpacingFixDialog(issues, self.win._theme, self.win)
        self.assertEqual(dlg._table.rowCount(), 1)
        intents = dlg.selected_intents()
        self.assertEqual(len(intents), 1)
        self.assertEqual(list(intents.values()), ['Foo - Bar.mp3'])
        self.assertEqual(dlg._btn_apply.text(), 'Rename 1 File')
        self.assertTrue(dlg._btn_apply.isEnabled())

    def test_unchecking_a_row_drops_it_from_the_intents(self):
        issues = self._spacing_issues()
        dlg = SpacingFixDialog(issues, self.win._theme, self.win)
        dlg._table.item(0, 0).setCheckState(Qt.Unchecked)
        self.assertEqual(dlg.selected_intents(), {})
        self.assertEqual(dlg._btn_apply.text(), 'Rename 0 Files')
        self.assertFalse(dlg._btn_apply.isEnabled())

    def _add_disk_song(self, filename: str):
        """Copy a real file into the fixture library AND register it in the
        already-scanned in-memory song dict — _request_playlist_reconcile
        only re-matches self._songs against the JSON, it doesn't rescan
        disk, so a test that only shutil.copy()s a new file would silently
        stay invisible to the health report."""
        path = os.path.join(self.music, filename)
        shutil.copy(os.path.join(self.music, 'AC DC - X.mp3'), path)
        self.win._songs[path] = {
            'path': path, 'filename': filename, 'title': '', 'artist': '',
            'album': '', 'has_cover': False, 'format': 'MP3', 'is_ghost': False,
        }
        return path

    def test_conflicting_destination_is_excluded_and_unselectable(self):
        # "Already - There.mp3" already exists, so "Already -There.mp3"'s
        # fix collides with a real file.
        self._add_disk_song('Already - There.mp3')
        self._add_disk_song('Already -There.mp3')
        self.win._request_playlist_reconcile()
        self.wait(self.ready, 10)

        issues = self._spacing_issues()
        target = next(i for i in issues if i.disk_filename == 'Already -There.mp3')
        dlg = SpacingFixDialog(issues, self.win._theme, self.win)
        row = next(r for r, d in enumerate(dlg._row_data) if d[0] == target.song_path)

        chk = dlg._table.item(row, dlg.COL_CHECK)
        self.assertEqual(chk.checkState(), Qt.Unchecked)
        self.assertFalse(bool(chk.flags() & Qt.ItemIsUserCheckable))
        self.assertNotIn(target.song_path, dlg.selected_intents())

        dlg._set_all_checks(True)   # "Select all" must not force-check a conflict
        self.assertNotIn(target.song_path, dlg.selected_intents())

    def test_two_rows_resolving_to_the_same_new_name_both_flagged(self):
        self._add_disk_song('Dup -Case.mp3')
        self._add_disk_song('Dup- Case.mp3')
        self.win._request_playlist_reconcile()
        self.wait(self.ready, 10)

        issues = self._spacing_issues()
        dlg = SpacingFixDialog(issues, self.win._theme, self.win)
        conflicts = [d for d in dlg._row_data if 'Dup' in d[0]]
        self.assertEqual(len(conflicts), 2)
        self.assertTrue(all(c[2] for c in conflicts))   # both marked conflict
        self.assertIn('2 files skipped', dlg._conflict_lbl.text())

    def test_apply_renames_file_and_syncs_json_title(self):
        # A spacing issue on a song that DOES have a JSON record: after the
        # fix, the JSON title must be the exact new filename (strict
        # contract), not just the file renamed underneath a stale title.
        store = self.win._playlist_store
        store.records.append(store._sanitize_record({'title': 'Artist -Song2.mp3', 'tags': ['chill']}))
        real_path = os.path.join(self.music, 'Artist -Song2.mp3')
        shutil.copy(os.path.join(self.music, 'AC DC - X.mp3'), real_path)
        self.win._songs[real_path] = {
            'path': real_path, 'filename': 'Artist -Song2.mp3', 'title': '', 'artist': '',
            'album': '', 'has_cover': False, 'format': 'MP3', 'is_ghost': False,
        }
        self.win._request_playlist_reconcile()
        self.wait(self.ready, 10)

        before = set(os.listdir(self.music))
        issues = self._spacing_issues()
        names = {i.disk_filename for i in issues}
        self.assertIn('Artist -Song2.mp3', names)
        self.assertIn('Foo -Bar.mp3', names)

        intents = {i.song_path: i.suggestion for i in issues}
        self.win._on_apply_spacing_fixes(intents)
        self.wait(self.ready, 10)

        after = set(os.listdir(self.music))
        self.assertNotIn('Artist -Song2.mp3', after)
        self.assertNotIn('Foo -Bar.mp3', after)
        self.assertIn('Artist - Song2.mp3', after)
        self.assertIn('Foo - Bar.mp3', after)
        self.assertEqual(after - before, {'Artist - Song2.mp3', 'Foo - Bar.mp3'})

        payload = json.load(open(self.json_path))
        titles = {s['title'] for s in payload['songs']}
        self.assertIn('Artist - Song2.mp3', titles)
        self.assertNotIn('Artist -Song2.mp3', titles)
        # Foo -Bar.mp3 never had a JSON entry — the rename must not create
        # one (renaming ≠ tagging).
        self.assertNotIn('Foo - Bar.mp3', titles)
        self.assertNotIn('Foo -Bar.mp3', titles)

        self.assertEqual(self._spacing_issues(), [])

    def test_apply_with_no_intents_is_a_safe_no_op(self):
        before = os.listdir(self.music)
        self.win._on_apply_spacing_fixes({})
        self.assertEqual(os.listdir(self.music), before)


if __name__ == '__main__':
    unittest.main()
