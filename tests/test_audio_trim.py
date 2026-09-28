import math
import os
import shutil
import struct
import subprocess
import tempfile
import unittest
import wave

import audio_trim
from audio_trim import (
    TrimError, compute_peaks, detect_silence, format_ms, parse_time,
    probe_duration_ms, trim,
)


def _make_wav(path, seconds=6.0, silence_tail=2.0, silence_head=0.0, rate=8000):
    with wave.open(path, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        n = int(seconds * rate)
        frames = bytearray()
        for i in range(n):
            t = i / rate
            loud = silence_head <= t < seconds - silence_tail
            v = int(12000 * math.sin(2 * math.pi * 440 * t)) if loud else 0
            frames += struct.pack('<h', v)
        wf.writeframes(bytes(frames))


class TrimTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_helpers(self):
        self.assertEqual(format_ms(65_500), '1:05.5')
        self.assertEqual(format_ms(65_500, 0), '1:05')
        self.assertEqual(parse_time('1:05.5'), 65_500)
        self.assertEqual(parse_time('12.25'), 12_250)
        self.assertIsNone(parse_time('abc'))

    def test_detect_silence_on_wav(self):
        p = os.path.join(self.tmp.name, 'tone.wav')
        _make_wav(p, seconds=6.0, silence_tail=2.0, silence_head=1.0)
        peaks, duration = compute_peaks(p, buckets=600)
        self.assertAlmostEqual(duration, 6000, delta=20)
        ms_per = duration / len(peaks)
        found = detect_silence(peaks, ms_per)
        self.assertAlmostEqual(found['lead_end_ms'], 1000, delta=60)
        self.assertAlmostEqual(found['trail_start_ms'], 4000, delta=60)
        self.assertIsNone(found['gap_start_ms'])

    def test_detect_internal_gap(self):
        # tone 0-2s, silence 2-4s, tone 4-4.5s, silence 4.5-6s (hidden track)
        p = os.path.join(self.tmp.name, 'gap.wav')
        rate = 8000
        with wave.open(p, 'wb') as wf:
            wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(rate)
            frames = bytearray()
            for i in range(6 * rate):
                t = i / rate
                loud = t < 2 or 4 <= t < 4.5
                frames += struct.pack('<h', int(12000 * math.sin(2 * math.pi * 440 * t)) if loud else 0)
            wf.writeframes(bytes(frames))
        peaks, duration = compute_peaks(p, buckets=600)
        found = detect_silence(peaks, duration / len(peaks))
        self.assertAlmostEqual(found['gap_start_ms'], 2000, delta=60)
        self.assertAlmostEqual(found['trail_start_ms'], 4500, delta=60)

    def test_trim_wav_in_place(self):
        p = os.path.join(self.tmp.name, 'tone.wav')
        _make_wav(p, seconds=6.0, silence_tail=2.0)
        before = sorted(os.listdir(self.tmp.name))
        result = trim(p, 500, 4000)
        self.assertEqual(sorted(os.listdir(self.tmp.name)), before)     # no clones
        self.assertAlmostEqual(result.new_duration_ms, 3500, delta=50)
        self.assertEqual(result.removed_start_ms, 500)
        self.assertAlmostEqual(result.removed_end_ms, 2000, delta=50)
        self.assertAlmostEqual(probe_duration_ms(p), 3500, delta=50)

    def test_trim_rejects_tiny_or_noop(self):
        p = os.path.join(self.tmp.name, 'tone.wav')
        _make_wav(p, seconds=3.0, silence_tail=0.0)
        with self.assertRaises(TrimError):
            trim(p, 1000, 1200)
        with self.assertRaises(TrimError):
            trim(p, 0, 0)

    def test_backup_only_when_requested(self):
        p = os.path.join(self.tmp.name, 'tone.wav')
        _make_wav(p, seconds=4.0)
        result = trim(p, 0, 2000, keep_backup=True)
        self.assertTrue(os.path.isfile(result.backup_path))
        self.assertEqual(Path_stem(result.backup_path), 'tone.bak')

    @unittest.skipUnless(audio_trim.ffmpeg_available(), 'ffmpeg not installed')
    def test_trim_mp3_keeps_tags_and_cover(self):
        from cover_art import write_cover, write_metadata, read_cover, get_metadata
        p = os.path.join(self.tmp.name, 'song.mp3')
        subprocess.run([audio_trim.FFMPEG, '-y', '-v', 'error', '-f', 'lavfi',
                        '-i', 'sine=frequency=440:duration=6', '-c:a', 'libmp3lame',
                        '-q:a', '9', p], check=True)
        write_metadata(p, title='T', artist='A', album='B')
        png = (b'\x89PNG\r\n\x1a\n' + b'\x00' * 64)
        write_cover(p, png, 'image/png')
        self.assertTrue(read_cover(p)[0])

        result = trim(p, 1000, 4000, fade_out_ms=300)     # fades force a re-encode
        self.assertAlmostEqual(result.new_duration_ms, 3000, delta=150)
        self.assertEqual(get_metadata(p).get('title'), 'T')
        self.assertTrue(read_cover(p)[0], 'cover lost after trim')
        self.assertFalse(any(f.endswith('.tmp.mp3') for f in os.listdir(self.tmp.name)))

        result2 = trim(p, 0, 2000)                          # stream copy path
        self.assertAlmostEqual(result2.new_duration_ms, 2000, delta=150)
        self.assertEqual(get_metadata(p).get('artist'), 'A')
        self.assertTrue(read_cover(p)[0])


def Path_stem(path):
    return os.path.splitext(os.path.basename(path))[0]


if __name__ == '__main__':
    unittest.main()
