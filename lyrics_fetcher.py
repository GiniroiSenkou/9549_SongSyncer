"""
lyrics_fetcher.py - Multi-source online lyrics fetcher.

Sources, tried in order until one returns lyrics:

1. **LRCLIB**   (lrclib.net)      – synced (.lrc) or plain, exact + fuzzy search
2. **NetEase**  (music.163.com)   – very large catalogue, synced lyrics
3. **lyrics.ovh**                 – plain lyrics, last resort

Pure Python (urllib + json), no Qt, no extra dependencies — safe to import
from any thread. ``fetch_lyrics`` returns ``(text, source_name)``; the
legacy ``fetch_from_lrclib`` is kept for callers that only want LRCLIB.
"""

from __future__ import annotations

import difflib
import json
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional


_LRCLIB_GET_URL    = "https://lrclib.net/api/get"
_LRCLIB_SEARCH_URL = "https://lrclib.net/api/search"
_LRCLIB_UA         = "SongSyncer/lyrics (https://lrclib.net)"
_NETEASE_SEARCH    = "https://music.163.com/api/search/get"
_NETEASE_LYRIC     = "https://music.163.com/api/song/lyric"
_NETEASE_HEADERS   = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Referer": "https://music.163.com/",
    "Cookie": "appver=2.0.2; os=pc",
    "Accept": "application/json",
}
_OVH_URL           = "https://api.lyrics.ovh/v1/{artist}/{title}"

SOURCES = ("lrclib", "netease", "lyrics_ovh")
SOURCE_LABELS = {"lrclib": "LRCLIB", "netease": "NetEase", "lyrics_ovh": "lyrics.ovh"}

_LEADING_TRACKNO_RE = re.compile(r"^\s*\d{1,3}\s*[-.]\s*")
_CLUTTER_WORDS = (
    r"feat\.?|ft\.?|featuring|with|prod\.?\s*by|remaster(?:ed)?|mono|stereo|live|"
    r"acoustic|demo|bonus|official|audio|video|lyric|lyrics|hd|hq|4k|extended|edit|"
    r"version|mix|remix|radio|album|original|cover|explicit|clean|single|ost|"
    r"from\b|instrumental|karaoke|slowed|sped|reverb|nightcore|visualizer|visualiser"
)
_TITLE_CLUTTER_RE = re.compile(
    rf"""
    \s*
    (?:
        \(\s*(?:\d{{4}}\s*)?(?:{_CLUTTER_WORDS})\b[^)]*\)
        |
        \[\s*(?:\d{{4}}\s*)?(?:{_CLUTTER_WORDS})\b[^\]]*\]
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)
_FEAT_TAIL_RE = re.compile(r"\s+(?:feat\.?|ft\.?|featuring)\s+.*$", re.IGNORECASE)
_DASH_TAIL_RE = re.compile(
    rf"\s+[-–—]\s+(?:(?:\d{{4}}\s+)?(?:{_CLUTTER_WORDS})\b.*)$", re.IGNORECASE
)
_ARTIST_SPLIT_RE = re.compile(r"\s*(?:,|&|\+| and | x | vs\.? | feat\.? | ft\.? | featuring )\s*",
                              re.IGNORECASE)
_LRC_TS_RE = re.compile(r"\[\d{1,3}:\d{2}(?:[.:]\d{1,3})?\]")


# ── text helpers ─────────────────────────────────────────────────────────────

def clean_title(title: str) -> str:
    """Strip leading track numbers and clutter like '(feat. X)',
    '[Official Video]', '(2011 Remaster)' or a trailing ' - Live'. Improves
    the match rate when titles come from filenames."""
    if not title:
        return ""
    t = _LEADING_TRACKNO_RE.sub("", str(title))
    t = _TITLE_CLUTTER_RE.sub("", t)
    t = _DASH_TAIL_RE.sub("", t)
    t = _FEAT_TAIL_RE.sub("", t)
    t = t.replace("_", " ")
    return re.sub(r"\s+", " ", t).strip(" -–—")


def clean_artist(artist: str) -> str:
    if not artist:
        return ""
    a = str(artist).replace("_", " ")
    a = _TITLE_CLUTTER_RE.sub("", a)
    return re.sub(r"\s+", " ", a).strip()


def primary_artist(artist: str) -> str:
    """'A feat. B', 'A & B', 'A, B' → 'A'."""
    parts = [p for p in _ARTIST_SPLIT_RE.split(clean_artist(artist)) if p]
    return parts[0].strip() if parts else clean_artist(artist)


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or "")).casefold()
    return re.sub(r"[^0-9a-z぀-ヿ一-鿿]+", "", s)


def _sim(a: str, b: str) -> float:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    if na == nb or na in nb or nb in na:
        return 1.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def _artist_matches(wanted: str, candidate: str) -> bool:
    if not wanted or not candidate:
        return False
    if _sim(wanted, candidate) >= 0.6:
        return True
    # "ACDC" vs "AC/DC", "Jay-Z" vs "JAY Z": compare with punctuation removed
    w, c = _norm(primary_artist(wanted)), _norm(candidate)
    return bool(w) and (w in c or c in w)


def query_variants(title: str, artist: str, allow_swap: bool = False) -> list[tuple[str, str]]:
    """Ordered (title, artist) pairs worth asking a source for."""
    t = clean_title(title)
    a = clean_artist(artist)
    variants: list[tuple[str, str]] = []

    def add(pair):
        if pair[0] and pair[1] and pair not in variants:
            variants.append(pair)

    add((t, a))
    add((t, primary_artist(a)))
    if allow_swap:
        add((clean_title(artist), clean_artist(title)))
    return variants


def _looks_like_lyrics(text: Optional[str]) -> bool:
    if not isinstance(text, str):
        return False
    stripped = _LRC_TS_RE.sub("", text)
    return len(stripped.strip()) >= 40 and stripped.count("\n") >= 3


# ── HTTP ─────────────────────────────────────────────────────────────────────

def _get_json(url: str, timeout: float, throttle=None, headers: Optional[dict] = None):
    if throttle is not None:
        throttle()
    req = urllib.request.Request(url, headers=headers or {
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


# ── LRCLIB ───────────────────────────────────────────────────────────────────

def _lrclib_request(url: str, timeout: float, throttle=None):
    return _get_json(url, timeout, throttle)


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


def _lrclib_pick(payload, title: str, artist: str, duration_s: Optional[int]) -> Optional[str]:
    if not isinstance(payload, list) or not payload:
        return None

    def _score(item):
        if not isinstance(item, dict):
            return -1
        ts = _sim(title, item.get("trackName") or "")
        as_ = 1.0 if _artist_matches(artist, item.get("artistName") or "") else 0.0
        if ts < 0.7 or (as_ == 0.0 and duration_s is None):
            return -1
        d = 0.0
        if duration_s is not None:
            try:
                delta = abs(int(item.get("duration") or 0) - duration_s)
            except (TypeError, ValueError):
                delta = 999
            if as_ == 0.0 and delta > 5:
                return -1
            d = max(0.0, 1.0 - delta / 30.0)
        synced = 0.5 if (isinstance(item.get("syncedLyrics"), str) and item["syncedLyrics"].strip()) else 0.0
        return ts + as_ + d + synced

    best = max(payload, key=_score)
    if _score(best) < 0:
        return None
    return _payload_to_text(best)


def fetch_from_lrclib(title: str,
                      artist: str,
                      album: str = "",
                      duration_ms: Optional[int] = None,
                      timeout: float = 8.0,
                      throttle=None) -> Optional[str]:
    """Query lrclib.net for lyrics. Returns LRC text (synced preferred, plain
    fallback) or None on miss / error. Never raises.

    ``throttle`` (optional callable) is invoked before every HTTP request so
    a shared rate limiter can pace parallel workers.

    Strategy:
      1. /api/get with full params (most precise).
      2. /api/search with track + artist — best candidate by title/artist
         similarity and duration.
      3. /api/search with the track only — accepted when the artist still
         matches loosely or the duration is within 5 s.
    """
    title = clean_title(title)
    artist = clean_artist(artist)
    if not title or not artist:
        return None

    duration_s = None
    if duration_ms and duration_ms > 0:
        duration_s = int(round(duration_ms / 1000.0))

    params = {"track_name": title, "artist_name": artist}
    if album:
        params["album_name"] = album.strip()
    if duration_s is not None:
        params["duration"] = str(duration_s)
    text = _payload_to_text(_lrclib_request(
        f"{_LRCLIB_GET_URL}?{urllib.parse.urlencode(params)}", timeout, throttle))
    if text:
        return text

    payload = _lrclib_request(
        f"{_LRCLIB_SEARCH_URL}?{urllib.parse.urlencode({'track_name': title, 'artist_name': artist})}",
        timeout, throttle)
    text = _lrclib_pick(payload, title, artist, duration_s)
    if text:
        return text

    payload = _lrclib_request(
        f"{_LRCLIB_SEARCH_URL}?{urllib.parse.urlencode({'track_name': title})}",
        timeout, throttle)
    return _lrclib_pick(payload, title, artist, duration_s)


# ── NetEase Cloud Music ──────────────────────────────────────────────────────

def fetch_from_netease(title: str, artist: str, album: str = "",
                       duration_ms: Optional[int] = None,
                       timeout: float = 8.0, throttle=None) -> Optional[str]:
    title = clean_title(title)
    artist = clean_artist(artist)
    if not title:
        return None
    query = urllib.parse.urlencode({"s": f"{title} {artist}".strip(), "type": 1,
                                    "limit": 10, "offset": 0})
    payload = _get_json(f"{_NETEASE_SEARCH}?{query}", timeout, throttle, _NETEASE_HEADERS)
    songs = (payload or {}).get("result", {}).get("songs") if isinstance(payload, dict) else None
    if not songs:
        return None

    duration_s = int(round(duration_ms / 1000.0)) if duration_ms and duration_ms > 0 else None

    def _score(item):
        if not isinstance(item, dict):
            return -1
        ts = _sim(title, item.get("name") or "")
        names = [a.get("name", "") for a in item.get("artists") or item.get("ar") or [] if isinstance(a, dict)]
        am = 1.0 if any(_artist_matches(artist, n) for n in names) else 0.0
        if ts < 0.75:
            return -1
        d = 0.0
        if duration_s is not None:
            try:
                delta = abs(int((item.get("duration") or item.get("dt") or 0) / 1000) - duration_s)
            except (TypeError, ValueError):
                delta = 999
            if am == 0.0 and delta > 5:
                return -1
            d = max(0.0, 1.0 - delta / 30.0)
        elif am == 0.0 and artist:
            return -1
        return ts + am + d

    best = max(songs, key=_score)
    if _score(best) < 0:
        return None
    song_id = best.get("id")
    if not song_id:
        return None
    lyric = _get_json(f"{_NETEASE_LYRIC}?{urllib.parse.urlencode({'id': song_id, 'lv': 1, 'kv': 1, 'tv': -1})}",
                      timeout, throttle, _NETEASE_HEADERS)
    if not isinstance(lyric, dict) or lyric.get("nolyric") or lyric.get("uncollected"):
        return None
    text = (lyric.get("lrc") or {}).get("lyric")
    if _looks_like_lyrics(text):
        return text
    return None


# ── lyrics.ovh ───────────────────────────────────────────────────────────────

def fetch_from_lyrics_ovh(title: str, artist: str, album: str = "",
                          duration_ms: Optional[int] = None,
                          timeout: float = 8.0, throttle=None) -> Optional[str]:
    title = clean_title(title)
    artist = primary_artist(artist)
    if not title or not artist:
        return None
    url = _OVH_URL.format(artist=urllib.parse.quote(artist), title=urllib.parse.quote(title))
    payload = _get_json(url, timeout, throttle, {"User-Agent": _LRCLIB_UA, "Accept": "application/json"})
    text = payload.get("lyrics") if isinstance(payload, dict) else None
    if _looks_like_lyrics(text):
        return text.replace("\r\n", "\n").strip() + "\n"
    return None


_SOURCE_FUNCS = {
    "lrclib": fetch_from_lrclib,
    "netease": fetch_from_netease,
    "lyrics_ovh": fetch_from_lyrics_ovh,
}


# ── entry point ──────────────────────────────────────────────────────────────

def fetch_lyrics(title: str, artist: str, album: str = "",
                 duration_ms: Optional[int] = None, timeout: float = 8.0,
                 throttle=None, sources=SOURCES,
                 allow_swap: bool = False) -> tuple[Optional[str], str]:
    """Try every source (and a few query variants) until one returns
    lyrics. Returns ``(text, source_name)`` or ``(None, '')``. Never raises.

    ``allow_swap`` also tries the artist/title swapped — useful when the
    pair came from a filename that might be "Title - Artist".
    """
    variants = query_variants(title, artist, allow_swap)
    if not variants:
        return None, ""
    for source in sources:
        func = _SOURCE_FUNCS.get(source)
        if func is None:
            continue
        for t, a in variants:
            try:
                text = func(t, a, album, duration_ms, timeout, throttle)
            except Exception:
                text = None
            if text:
                return text, source
    return None, ""
