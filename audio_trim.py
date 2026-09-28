"""
audio_trim.py – ffmpeg-backed song trimming, waveform peaks and silence
detection for the Trimmer dialog.

Pure Python + subprocess (no numpy / pydub). ``ffmpeg`` and ``ffprobe`` are
looked up on PATH; when they are missing every entry point raises
``TrimUnavailable`` with an install hint, except for WAV files which fall
back to the stdlib ``wave`` module.

The file is always **overwritten in place** (temp file next to the original
+ ``os.replace``) — the song is updated, never cloned — unless the caller
explicitly asks for a ``.bak`` copy.
"""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import wave
from array import array
from dataclasses import dataclass
from pathlib import Path

from cover_art import read_cover, write_cover

FFMPEG = shutil.which('ffmpeg')
FFPROBE = shutil.which('ffprobe')
PEAK_RATE = 8000          # Hz used for the mono analysis stream
INSTALL_HINT = ('ffmpeg was not found on this system. Install it with '
                '"sudo apt install ffmpeg" (Debian/Ubuntu), "sudo dnf install ffmpeg" '
                '(Fedora), "brew install ffmpeg" (macOS) or from ffmpeg.org on Windows.')

# Re-encode arguments per container when fades are requested (stream copy
# is used otherwise so the audio is untouched).
_ENCODERS = {
    '.mp3':  ['-c:a', 'libmp3lame', '-q:a', '0'],
    '.flac': ['-c:a', 'flac'],
    '.m4a':  ['-c:a', 'aac', '-b:a', '256k'],
    '.aac':  ['-c:a', 'aac', '-b:a', '256k'],
    '.ogg':  ['-c:a', 'libvorbis', '-q:a', '8'],
    '.opus': ['-c:a', 'libopus', '-b:a', '192k'],
    '.wav':  ['-c:a', 'pcm_s16le'],
}


class TrimUnavailable(RuntimeError):
    pass


class TrimError(RuntimeError):
    pass


@dataclass
class TrimResult:
    path: str
    old_duration_ms: int
    new_duration_ms: int
    removed_start_ms: int
    removed_end_ms: int
    backup_path: str = ''
    cover_reembedded: bool = False

    @property
    def removed_ms(self) -> int:
        return self.removed_start_ms + self.removed_end_ms


def ffmpeg_available() -> bool:
    return bool(FFMPEG and FFPROBE)


def _require_ffmpeg():
    if not ffmpeg_available():
        raise TrimUnavailable(INSTALL_HINT)


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    kw.setdefault('stdout', subprocess.PIPE)
    kw.setdefault('stderr', subprocess.PIPE)
    return subprocess.run(cmd, check=False, **kw)


def format_ms(ms: int, decimals: int = 1) -> str:
    ms = max(0, int(ms))
    minutes, rest = divmod(ms, 60_000)
    seconds = rest / 1000.0
    return f'{minutes}:{seconds:0{3 + decimals}.{decimals}f}' if decimals else f'{minutes}:{int(seconds):02d}'


def parse_time(text: str) -> int | None:
    """``m:ss.mmm`` / ``ss.s`` / ``h:mm:ss`` → milliseconds, or None."""
    text = (text or '').strip()
    if not text:
        return None
    parts = text.split(':')
    try:
        total = 0.0
        for part in parts:
            total = total * 60 + float(part)
    except ValueError:
        return None
    return int(round(total * 1000))


# ── probing / analysis ──────────────────────────────────────────────────────

def probe_duration_ms(path: str) -> int:
    if Path(path).suffix.lower() == '.wav' and not FFPROBE:
        with wave.open(path, 'rb') as wf:
            return int(wf.getnframes() * 1000 / wf.getframerate())
    _require_ffmpeg()
    proc = _run([FFPROBE, '-v', 'error', '-show_entries', 'format=duration',
                 '-of', 'default=noprint_wrappers=1:nokey=1', path])
    try:
        return int(float(proc.stdout.decode('utf-8', 'replace').strip()) * 1000)
    except ValueError:
        return 0


def _mono_samples(path: str) -> array:
    """Decode to 8 kHz mono signed-16 PCM."""
    if Path(path).suffix.lower() == '.wav' and not FFMPEG:
        with wave.open(path, 'rb') as wf:
            channels, width, rate = wf.getnchannels(), wf.getsampwidth(), wf.getframerate()
            raw = wf.readframes(wf.getnframes())
        if width != 2:
            raise TrimError('Only 16-bit WAV files are supported without ffmpeg.')
        data = array('h')
        data.frombytes(raw)
        step = max(1, channels * max(1, rate // PEAK_RATE))
        return array('h', data[::step])
    _require_ffmpeg()
    proc = _run([FFMPEG, '-v', 'error', '-i', path, '-vn', '-f', 's16le', '-ac', '1',
                 '-ar', str(PEAK_RATE), '-'])
    if proc.returncode != 0:
        raise TrimError(proc.stderr.decode('utf-8', 'replace').strip() or 'ffmpeg decode failed')
    data = array('h')
    raw = proc.stdout
    if len(raw) % 2:
        raw = raw[:-1]
    data.frombytes(raw)
    return data


def compute_peaks(path: str, buckets: int = 1200) -> tuple[list[float], int]:
    """Return (peaks, duration_ms): ``peaks`` holds ``buckets`` values in
    0..1 (max absolute amplitude per slice)."""
    samples = _mono_samples(path)
    n = len(samples)
    if n == 0:
        return [], 0
    duration_ms = int(n * 1000 / PEAK_RATE)
    buckets = max(1, min(buckets, n))
    size = math.ceil(n / buckets)
    peaks: list[float] = []
    for i in range(0, n, size):
        chunk = samples[i:i + size]
        peak = max(abs(min(chunk)), abs(max(chunk))) if len(chunk) else 0
        peaks.append(min(1.0, peak / 32768.0))
    return peaks, duration_ms


def detect_silence(peaks: list[float], ms_per_bucket: float,
                   threshold: float = 0.02, min_ms: int = 1500) -> dict:
    """Suggest cut points from a peak envelope.

    Returns ``{'lead_end_ms', 'trail_start_ms', 'gap_start_ms'}``:
    * ``lead_end_ms``   – where the leading silence ends (0 if none ≥ 300 ms)
    * ``trail_start_ms`` – where the trailing silence starts (duration if none ≥ ``min_ms``)
    * ``gap_start_ms``  – start of the *last* internal silence ≥ ``min_ms``
      that is followed by more sound (an "unexpected ending" / hidden
      track), or None
    """
    total = len(peaks)
    duration = int(total * ms_per_bucket)
    if not total:
        return {'lead_end_ms': 0, 'trail_start_ms': 0, 'gap_start_ms': None}
    loud = [p > threshold for p in peaks]
    first = next((i for i, v in enumerate(loud) if v), None)
    last = next((i for i in range(total - 1, -1, -1) if loud[i]), None)
    if first is None or last is None:
        return {'lead_end_ms': 0, 'trail_start_ms': duration, 'gap_start_ms': None}

    lead_end = int(first * ms_per_bucket)
    if lead_end < 300:
        lead_end = 0
    trail_start = int((last + 1) * ms_per_bucket)
    if duration - trail_start < min_ms:
        trail_start = duration

    # internal gaps between first and last loud bucket
    gap_start = None
    min_buckets = max(1, int(min_ms / ms_per_bucket))
    run_start = None
    for i in range(first, last + 1):
        if not loud[i]:
            if run_start is None:
                run_start = i
        else:
            if run_start is not None and i - run_start >= min_buckets:
                gap_start = int(run_start * ms_per_bucket)
            run_start = None
    return {'lead_end_ms': lead_end, 'trail_start_ms': trail_start, 'gap_start_ms': gap_start}


# ── trimming ────────────────────────────────────────────────────────────────

def trim(path: str, start_ms: int, end_ms: int, *,
         fade_in_ms: int = 0, fade_out_ms: int = 0,
         keep_backup: bool = False) -> TrimResult:
    """Keep ``[start_ms, end_ms]`` of ``path`` and overwrite the file in place.

    Stream-copies when no fades are requested (audio bytes untouched, tags
    and embedded cover carried over); re-encodes in the same codec when a
    fade is requested. Raises ``TrimError`` / ``TrimUnavailable``.
    """
    if not os.path.isfile(path):
        raise TrimError(f'File not found: {path}')
    old_duration = probe_duration_ms(path)
    start_ms = max(0, int(start_ms))
    end_ms = int(end_ms) if end_ms and end_ms > 0 else old_duration
    end_ms = min(end_ms, old_duration) if old_duration else end_ms
    if end_ms - start_ms < 500:
        raise TrimError('The kept region must be at least half a second long.')
    if start_ms == 0 and old_duration and end_ms >= old_duration and not (fade_in_ms or fade_out_ms):
        raise TrimError('Nothing to trim — the whole song is selected.')

    ext = Path(path).suffix.lower()
    directory = os.path.dirname(path)
    stem = Path(path).stem
    tmp = os.path.join(directory, f'{stem}.trim.tmp{ext}')
    cover_before = read_cover(path)

    if ext == '.wav' and not (fade_in_ms or fade_out_ms):
        # Lossless and sample-accurate without ffmpeg (stream copy would cut
        # at 256 ms packet boundaries).
        _trim_wav(path, tmp, start_ms, end_ms)
    else:
        _require_ffmpeg()
        # Only the audio stream goes through ffmpeg. The embedded cover is
        # re-attached afterwards with mutagen (see below) — that keeps
        # ogg/opus pictures intact and never trips over a malformed image.
        cmd = [FFMPEG, '-y', '-v', 'error', '-i', path,
               '-ss', f'{start_ms / 1000:.3f}', '-to', f'{end_ms / 1000:.3f}',
               '-map', '0:a:0', '-map_metadata', '0', '-vn',
               '-avoid_negative_ts', 'make_zero']
        lossless = ext in ('.flac', '.wav')
        if fade_in_ms or fade_out_ms:
            filters = []
            if fade_in_ms:
                filters.append(f'afade=t=in:st=0:d={fade_in_ms / 1000:.3f}')
            if fade_out_ms:
                kept = (end_ms - start_ms) / 1000
                filters.append(f'afade=t=out:st={max(0.0, kept - fade_out_ms / 1000):.3f}:'
                               f'd={fade_out_ms / 1000:.3f}')
            cmd += ['-af', ','.join(filters)] + _ENCODERS.get(ext, ['-c:a', 'copy'])
        elif lossless:
            # Re-encode losslessly for a sample-accurate cut.
            cmd += _ENCODERS[ext]
        else:
            cmd += ['-c:a', 'copy']
        if ext == '.mp3':
            cmd += ['-id3v2_version', '3', '-write_xing', '1']
        if ext in ('.m4a', '.aac'):
            cmd += ['-movflags', '+faststart']
        cmd.append(tmp)
        proc = _run(cmd)
        if proc.returncode != 0 or not os.path.isfile(tmp) or os.path.getsize(tmp) == 0:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise TrimError(proc.stderr.decode('utf-8', 'replace').strip() or 'ffmpeg failed')

    backup = ''
    if keep_backup:
        backup = os.path.join(directory, f'{stem}.bak{ext}')
        shutil.copy2(path, backup)
    os.replace(tmp, path)

    reembedded = False
    data, mime = cover_before
    if data and not read_cover(path)[0]:
        reembedded = write_cover(path, data, mime or 'image/jpeg')
    if data and not read_cover(path)[0]:
        # mutagen refused the bytes we read back — nothing more to do, the
        # audio itself is intact.
        reembedded = False

    new_duration = probe_duration_ms(path)
    return TrimResult(
        path=path,
        old_duration_ms=old_duration,
        new_duration_ms=new_duration,
        removed_start_ms=start_ms,
        removed_end_ms=max(0, old_duration - end_ms),
        backup_path=backup,
        cover_reembedded=reembedded,
    )


def _trim_wav(src: str, dst: str, start_ms: int, end_ms: int):
    with wave.open(src, 'rb') as wf:
        params = wf.getparams()
        rate = wf.getframerate()
        start = int(start_ms * rate / 1000)
        end = int(end_ms * rate / 1000)
        wf.setpos(min(start, wf.getnframes()))
        frames = wf.readframes(max(0, end - start))
    with wave.open(dst, 'wb') as out:
        out.setparams(params)
        out.writeframes(frames)
