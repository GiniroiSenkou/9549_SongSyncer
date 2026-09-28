import json
import os
import sys
import tempfile
import time
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
# QApplication, not QCoreApplication: this only needs an event loop for the
# QThread-based workers, but Qt allows exactly one application instance per
# process, and other test modules in the same run (e.g. offscreen_harness,
# for a real MainWindow) need the full QApplication. Whichever module runs
# first creates the shared singleton, so it must be the superset type.
from PyQt5.QtWidgets import QApplication

import workers
from lyrics_store import LyricsStore, MISS_CACHE_NAME, shift_lrc, shift_lrc_text


_APP = QApplication.instance() or QApplication(sys.argv[:1])


def _fake_fetch(title, artist, album='', duration_ms=None, timeout=8.0, throttle=None, **kw):
    if throttle:
        throttle()
    time.sleep(0.05)
    if title.startswith('hit'):
        return f'[00:01.00]{artist} - {title}\n', 'fake'
    return None, ''


class LyricsWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.music = os.path.join(self.tmp.name, 'music')
        self.lyrics = os.path.join(self.tmp.name, 'lyrics')
        os.makedirs(self.music)
        os.makedirs(self.lyrics)
        self._orig = workers.fetch_lyrics
        workers.fetch_lyrics = _fake_fetch
        self.addCleanup(lambda: setattr(workers, 'fetch_lyrics', self._orig))

    def _song(self, name, title, artist):
        path = os.path.join(self.music, name)
        return {'path': path, 'filename': name, 'title': title, 'artist': artist, 'album': ''}

    def _run(self, worker, timeout=10):
        results = {}
        done = {}
        worker.song_done.connect(lambda p, st: results.__setitem__(p, st))
        worker.finished.connect(lambda f, x, s: done.update(fetched=f, failed=x, skipped=s))
        worker.start()
        t0 = time.time()
        while not done and time.time() - t0 < timeout:
            _APP.processEvents()
            time.sleep(0.01)
        worker.wait(2000)
        return results, done

    def test_parallel_fetch_statuses_and_miss_cache(self):
        songs = [self._song(f'A - hit{i}.mp3', f'hit{i}', 'A') for i in range(6)]
        songs += [self._song('B - nope.mp3', 'nope', 'B'), self._song('untitled.mp3', '', '')]
        existing = self._song('C - hit9.mp3', 'hit9', 'C')
        open(os.path.join(self.lyrics, 'C - hit9.lrc'), 'w').write('x')
        songs.append(existing)

        t0 = time.time()
        results, done = self._run(workers.LyricsFetchWorker(songs, self.lyrics, max_workers=4,
                                                            requests_per_second=100))
        elapsed = time.time() - t0
        self.assertEqual(done['fetched'], 7)   # 6 hits + 1 existing
        self.assertEqual(done['failed'], 1)
        self.assertEqual(done['skipped'], 1)
        self.assertEqual(results[existing['path']], 'exists')
        self.assertEqual(results[songs[6]['path']], 'miss')
        self.assertEqual(results[songs[7]['path']], 'skipped')
        self.assertTrue(os.path.isfile(os.path.join(self.lyrics, 'A - hit0.lrc')))
        self.assertLess(elapsed, 2.0)            # 7 × 50 ms serial would be ≥ 0.35 s; parallel ≪

        cache = json.load(open(os.path.join(self.lyrics, MISS_CACHE_NAME)))
        key = LyricsStore.miss_key('B', 'nope')
        self.assertEqual(list(cache), [key])
        self.assertEqual(cache[key]['artist'], 'B')
        self.assertEqual(cache[key]['sources'], list(workers.LYRICS_SOURCES))

        # second run (fresh store = new session): the miss is remembered and
        # not asked again — reported as known_miss, not as a fresh failure
        calls = []
        def counting(title, artist, *a, **k):
            calls.append(title)
            return None, ''
        workers.fetch_lyrics = counting
        results, done = self._run(workers.LyricsFetchWorker([songs[6]], self.lyrics))
        self.assertEqual(calls, [])
        self.assertEqual(results[songs[6]['path']], 'known_miss')
        store = LyricsStore(self.lyrics)
        self.assertTrue(store.is_known_miss(LyricsStore.miss_key_for_song(songs[6])))
        self.assertEqual(store.not_found_count({songs[6]['path']: songs[6]}), 1)

        # an explicit retry forgets the entry and asks again
        store.forget_miss(key)
        store.save_misses()
        results, done = self._run(workers.LyricsFetchWorker([songs[6]], self.lyrics))
        self.assertEqual(calls, ['nope'])

    def test_retarget_mid_run_writes_to_new_folder(self):
        songs = [self._song(f'A - hit{i}.mp3', f'hit{i}', 'A') for i in range(8)]
        other = os.path.join(self.tmp.name, 'lyrics2')
        os.makedirs(other)
        worker = workers.LyricsFetchWorker(songs, self.lyrics, max_workers=1, requests_per_second=100)
        seen = []
        worker.song_done.connect(lambda p, st: seen.append(p))
        def _retarget_after_two(*_):
            if len(seen) == 2:
                worker.retarget(other)
        worker.song_done.connect(_retarget_after_two)
        results, done = self._run(worker)
        self.assertEqual(done['fetched'], 8)
        written_old = [f for f in os.listdir(self.lyrics) if f.endswith('.lrc')]
        written_new = [f for f in os.listdir(other) if f.endswith('.lrc')]
        self.assertGreaterEqual(len(written_old), 2)
        self.assertGreaterEqual(len(written_new), 1)
        self.assertEqual(len(written_old) + len(written_new), 8)

    def test_cancel_stops_early(self):
        songs = [self._song(f'A - hit{i}.mp3', f'hit{i}', 'A') for i in range(30)]
        worker = workers.LyricsFetchWorker(songs, self.lyrics, max_workers=2, requests_per_second=100)
        worker.song_done.connect(lambda p, st: worker.cancel())
        results, done = self._run(worker)
        self.assertLess(done['fetched'], 30)


class ResolveTests(unittest.TestCase):
    def test_resolve_artist_title_prefers_metadata_then_filename(self):
        song = {'path': '/m/A - B.mp3', 'filename': 'A - B.mp3', 'title': 'T', 'artist': 'X'}
        self.assertEqual(LyricsStore.resolve_artist_title(song), ('X', 'T', False))
        song = {'path': '/m/A - B.mp3', 'filename': 'A - B.mp3', 'title': '', 'artist': ''}
        self.assertEqual(LyricsStore.resolve_artist_title(song), ('A', 'B', True))
        self.assertEqual(LyricsStore.miss_key_for_song({'filename': 'nodash.mp3'}), '')


class LrcShiftTests(unittest.TestCase):
    def test_shift_text(self):
        src = '[00:01.50]a\n[00:03.00][00:05.00]b\nplain\n'
        self.assertEqual(shift_lrc_text(src, -2000), '[00:01.00][00:03.00]b\nplain\n')
        self.assertEqual(shift_lrc_text('[00:01.50]a\n', 1500), '[00:03.00]a\n')
        self.assertEqual(shift_lrc_text(src, 0), src)

    def test_shift_file(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p = os.path.join(tmp.name, 'x.lrc')
        open(p, 'w').write('[00:10.00]hi\n')
        self.assertTrue(shift_lrc(p, -4000))
        self.assertEqual(open(p).read(), '[00:06.00]hi\n')
        self.assertFalse(shift_lrc(p, 0))


if __name__ == '__main__':
    unittest.main()
