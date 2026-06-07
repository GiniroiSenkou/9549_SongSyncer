"""
lyrics_fetcher.py - Standalone LRCLIB online lyrics fetcher.

Ported from LaLaDash/Modules/Music/lyrics.py. Pure Python (urllib + json),
no Qt, no extra dependencies — safe to import from any thread.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional


_LRCLIB_GET_URL    = "https://lrclib.net/api/get"
_LRCLIB_SEARCH_URL = "https://lrclib.net/api/search"
_LRCLIB_UA         = "SongSyncer/lyrics (https://lrclib.net)"

_LEADING_TRACKNO_RE = re.compile(r"^\s*\d{1,3}\s*[-.]\s*")
_TITLE_CLUTTER_RE = re.compile(
    r"""
    \s*
    (?:
        \(\s*(?:feat\.?|ft\.?|featuring|with|prod\.?\s*by|remaster(?:ed)?|mono|stereo|live|acoustic|demo|bonus|official|audio|video|lyric|lyrics|hd|hq|4k|extended|edit|version|mix|radio|album|original|cover)\b[^)]*\)
        |
        \[\s*(?:feat\.?|ft\.?|featuring|with|prod\.?\s*by|remaster(?:ed)?|mono|stereo|live|acoustic|demo|bonus|official|audio|video|lyric|lyrics|hd|hq|4k|extended|edit|version|mix|radio|album|original|cover)\b[^\]]*\]
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def clean_title(title: str) -> str:
    """Strip leading track numbers and parenthetical clutter like
    '(feat. X)' or '[Official Video]'. Improves LRCLIB match rate when
    titles come from filenames."""
    if not title:
        return ""
    t = _LEADING_TRACKNO_RE.sub("", str(title))
    t = _TITLE_CLUTTER_RE.sub("", t)
    return re.sub(r"\s+", " ", t).strip()


def _lrclib_request(url: str, timeout: float):
    req = urllib.request.Request(url, headers={
        "User-Agent": _LRCLIB_UA,
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", 200)
            if status != 200:
                return None
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError:
        return None
    except Exception:
        return None


def _payload_to_text(payload) -> Optional[str]:
    if not isinstance(payload, dict):
        return None
    synced = payload.get("syncedLyrics")
    if isinstance(synced, str) and synced.strip():
        return synced
    plain = payload.get("plainLyrics")
    if isinstance(plain, str) and plain.strip():
        return plain
    return None


def fetch_from_lrclib(title: str,
                      artist: str,
                      album: str = "",
                      duration_ms: Optional[int] = None,
                      timeout: float = 15.0) -> Optional[str]:
    """Query lrclib.net for lyrics. Returns LRC text (synced preferred, plain
    fallback) or None on miss / error. Never raises.

    Strategy:
      1. /api/get with full params (most precise).
      2. /api/get without `duration` (LRCLIB requires it within ~5s).
      3. /api/search with track + artist — picks the synced result with
         closest duration when available, else the first hit.
    """
    title = clean_title(title)
    artist = (artist or "").strip()
    if not title or not artist:
        return None

    params = {"track_name": title, "artist_name": artist}
    if album:
        params["album_name"] = album.strip()
    duration_s = None
    if duration_ms and duration_ms > 0:
        duration_s = int(round(duration_ms / 1000.0))
        params["duration"] = str(duration_s)
    payload = _lrclib_request(
        f"{_LRCLIB_GET_URL}?{urllib.parse.urlencode(params)}", timeout
    )
    text = _payload_to_text(payload)
    if text:
        return text

    if "duration" in params:
        params.pop("duration", None)
        payload = _lrclib_request(
            f"{_LRCLIB_GET_URL}?{urllib.parse.urlencode(params)}", timeout
        )
        text = _payload_to_text(payload)
        if text:
            return text

    search_params = {"track_name": title, "artist_name": artist}
    payload = _lrclib_request(
        f"{_LRCLIB_SEARCH_URL}?{urllib.parse.urlencode(search_params)}", timeout
    )
    if not isinstance(payload, list) or not payload:
        return None

    def _has_synced(item):
        v = item.get("syncedLyrics") if isinstance(item, dict) else None
        return isinstance(v, str) and bool(v.strip())

    def _has_plain(item):
        v = item.get("plainLyrics") if isinstance(item, dict) else None
        return isinstance(v, str) and bool(v.strip())

    def _dur_delta(item):
        if duration_s is None:
            return 0
        try:
            return abs(int(item.get("duration") or 0) - duration_s)
        except Exception:
            return 10_000

    synced_hits = sorted([i for i in payload if _has_synced(i)], key=_dur_delta)
    if synced_hits:
        return synced_hits[0].get("syncedLyrics") or None
    plain_hits = sorted([i for i in payload if _has_plain(i)], key=_dur_delta)
    if plain_hits:
        return plain_hits[0].get("plainLyrics") or None
    return None
