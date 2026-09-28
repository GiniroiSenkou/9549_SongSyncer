"""
Regression tests for the reconcile/health-report performance fixes.

Saving one song via Edit Song used to freeze the whole app for several
seconds on a large real library, and closing it could then crash: pressing
Save schedules a debounced reconcile; when it completed, MainWindow ran the
strict-sync audit (json_health.build_health_report, whose difflib fuzzy
search is O(missing records × library size)) synchronously on the UI
thread, then rebuilt every song's tag-preview icons (each paying a real
Path.resolve() filesystem call), then wiped and recreated every "missing
JSON entry" ghost table row on every single reconcile even when nothing
about them had changed. Measured on a ~3300-song / ~3600-record library:
5.7-7.8s of UI-thread work per reconcile, cut to ~0.1-0.5s here.

These tests pin down each piece: the audit runs off the UI thread, its
result is reused rather than recomputed, and ghost rows are rebuilt only
when the missing set actually changes.
"""
import os
import random
import string
import time
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from tests.offscreen_harness import boot


def _rand_name(rng: random.Random) -> str:
    words = [
        ''.join(rng.choices(string.ascii_lowercase, k=rng.randint(3, 9)))
        for _ in range(rng.randint(2, 5))
    ]
    return ' '.join(words).title() + ' - ' + ''.join(rng.choices(string.ascii_lowercase, k=8)) + '.mp3'


class ReconcilePerformanceTests(unittest.TestCase):
    N_SONGS = 1200          # large enough to make an O(n) UI-thread regression
    N_MISSING = 80          # obvious in wall-clock time without a slow test run

    def setUp(self):
        self.app, self.win, self.wait, self.ready, paths = boot(theme='light')
        self.music, self.lyrics, self.json_path = paths
        self.addCleanup(self._teardown_window)

        rng = random.Random(42)
        names = [_rand_name(rng) for _ in range(self.N_SONGS)]
        self.win._songs.clear()
        for i, n in enumerate(names):
            p = f'{self.music}/{n}'
            self.win._songs[p] = {
                'path': p, 'filename': n, 'title': '', 'artist': '', 'album': '',
                'has_cover': i % 3 == 0, 'format': 'MP3', 'is_ghost': False,
            }

        records = [{'title': n, 'tags': ['chill']} for n in names]
        for i in range(self.N_MISSING):
            records.append({'title': f'Ghost Song {i} - Nowhere.mp3', 'tags': ['ost']})
        store = self.win._playlist_store
        store.records = [store._sanitize_record(r) for r in records]

    def _teardown_window(self):
        timer = getattr(self.win, '_reconcile_debounce', None)
        if timer is not None:
            timer.stop()
        self.win.close()
        worker = getattr(self.win, '_reconcile_worker', None)
        if worker is not None and worker.isRunning():
            worker.wait(1000)

    def test_health_report_runs_off_the_ui_thread(self):
        """The reconcile worker's result must carry precomputed health
        issues, and applying them on the GUI thread must be fast — the
        UI-thread callback should not itself be doing the O(missing ×
        library) difflib search."""
        ticks = []
        from PyQt5.QtCore import QTimer
        heartbeat = QTimer()
        heartbeat.setInterval(15)
        heartbeat.timeout.connect(lambda: ticks.append(time.perf_counter()))
        heartbeat.start()

        t0 = time.perf_counter()
        self.win._request_playlist_reconcile()
        dispatch_ms = (time.perf_counter() - t0) * 1000
        ok = self.wait(self.ready, 20)
        heartbeat.stop()

        self.assertTrue(ok, 'reconcile did not settle in time')
        self.assertLess(dispatch_ms, 100,
                        'starting the reconcile must not itself block (work runs on a QThread)')
        self.assertGreater(len(self.win._health_issues), 0)

        gaps = [b - a for a, b in zip(ticks, ticks[1:])]
        worst_gap = max(gaps) if gaps else 0.0
        # A regression back to the old behaviour (audit + full ghost-row
        # rebuild synchronously on the GUI thread) blows well past a second
        # for this library size; a healthy run stays well under that.
        self.assertLess(worst_gap, 1.0,
                        f'GUI thread heartbeat stalled for {worst_gap:.2f}s — '
                        f'something is running the audit synchronously again')

    def test_repeated_reconcile_with_unchanged_missing_set_is_cheap(self):
        """The common case after an Edit Song save: one song's metadata
        changed, but *which* records are missing a file did not — ghost
        rows must not be torn down and rebuilt for no reason."""
        self.win._request_playlist_reconcile()
        self.wait(self.ready, 20)
        ghost_count_1 = len(self.win._ghost_paths)
        self.assertGreater(ghost_count_1, 0)
        rows_1 = self.win._table.rowCount()

        t0 = time.perf_counter()
        self.win._request_playlist_reconcile()
        self.wait(self.ready, 20)
        elapsed = time.perf_counter() - t0

        self.assertEqual(len(self.win._ghost_paths), ghost_count_1)
        self.assertEqual(self.win._table.rowCount(), rows_1)
        self.assertLess(elapsed, 1.5,
                        f'unchanged-ghost-set reconcile took {elapsed:.2f}s — '
                        f'ghost rows were rebuilt when nothing changed')

    def test_ghost_rows_are_rebuilt_when_the_missing_set_actually_changes(self):
        """The skip-if-unchanged shortcut must not become a stale cache:
        when a record's match status genuinely changes, the ghost rows
        must follow."""
        self.win._request_playlist_reconcile()
        self.wait(self.ready, 20)
        before = set(self.win._ghost_paths)
        self.assertTrue(before)

        # Resolve one missing record by adding a matching file.
        store = self.win._playlist_store
        ghost_path = next(iter(before))
        record = store._path_to_record[ghost_path]
        real_name = record['title']
        real_path = f'{self.music}/{real_name}'
        self.win._songs[real_path] = {
            'path': real_path, 'filename': real_name, 'title': '', 'artist': '',
            'album': '', 'has_cover': False, 'format': 'MP3', 'is_ghost': False,
        }

        self.win._request_playlist_reconcile()
        self.wait(self.ready, 20)
        after = set(self.win._ghost_paths)
        self.assertEqual(len(after), len(before) - 1)
        self.assertNotIn(real_path, after)


if __name__ == '__main__':
    unittest.main()
