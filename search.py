"""
search.py – Cover art search via iTunes Search API (free, no key required).
Falls back to a Deezer search if iTunes returns no results.
"""

import re
from io import BytesIO
import requests
from typing import List, Dict, Optional
from PIL import Image, ImageFile

ITUNES_URL      = "https://itunes.apple.com/search"
DEEZER_URL      = "https://api.deezer.com/search"
MUSICBRAINZ_URL = "https://musicbrainz.org/ws/2/release"
CAA_URL         = "https://coverartarchive.org/release"
TIMEOUT = 12
_HEADERS = {"User-Agent": "SongSyncer/1.0 ( github.com/user/songsyncer )"}
ImageFile.LOAD_TRUNCATED_IMAGES = True


def build_query(title: str = '', artist: str = '', album: str = '') -> str:
    """Build the best search query from available metadata."""
    parts = []
    if artist:
        parts.append(artist)
    if album:
        parts.append(album)
    elif title:
        parts.append(title)
    return ' '.join(parts) if parts else title


def search_cover_art(query: str, limit: int = 6) -> List[Dict]:
    """
    Search for cover art using three services in priority order:
      1. iTunes Search API
      2. Deezer
      3. MusicBrainz + Cover Art Archive (best for non-mainstream music)
    Tries multiple query variations per service to maximise hit rate.
    Returns a list of dicts: title, artist, album, artwork_url, thumb_url
    """
    if not query.strip():
        return []

    candidates = _query_candidates(query)
    seen_urls: set = set()
    results: List[Dict] = []

    # 1 – iTunes
    for q in candidates:
        if len(results) >= limit:
            break
        results.extend(_search_itunes(q, limit - len(results), seen_urls))

    # 2 – Deezer (if iTunes came up short)
    if len(results) < limit:
        for q in candidates:
            if len(results) >= limit:
                break
            results.extend(_search_deezer(q, limit - len(results), seen_urls))

    # 3 – MusicBrainz / Cover Art Archive (last resort)
    if len(results) < limit:
        for q in candidates:
            if len(results) >= limit:
                break
            results.extend(_search_musicbrainz(q, limit - len(results), seen_urls))

    return results[:limit]


def download_image(url: str) -> Optional[bytes]:
    """Download an image by URL. Returns bytes or None."""
    try:
        resp = requests.get(url, timeout=TIMEOUT, headers=_HEADERS)
        resp.raise_for_status()
        ct = resp.headers.get('Content-Type', '')
        if not ('image' in ct or len(resp.content) > 1000):
            return None
        cleaned = _normalize_image(resp.content)
        if cleaned:
            return cleaned
    except Exception:
        pass
    return None


# ── Query helpers ─────────────────────────────────────────────────────────────

def _clean(s: str) -> str:
    """Strip parenthetical / bracketed content and normalise whitespace."""
    s = re.sub(r'\([^)]*\)', '', s)
    s = re.sub(r'\[[^\]]*\]', '', s)
    s = re.sub(r'\s+', ' ', s)
    return s.strip()


def _query_candidates(query: str) -> List[str]:
    """
    Return an ordered list of query strings to try, from most to least specific.
    Handles both 'Artist - Title' style queries and plain metadata strings.
    """
    q = query.strip()
    candidates = []

    # 1. Original query (cleaned)
    cleaned = _clean(q)
    if cleaned:
        candidates.append(cleaned)

    # 2. If the query contains ' - ' (common filename pattern), split it
    if ' - ' in q:
        parts = q.split(' - ', 1)
        artist_part = _clean(parts[0])
        title_part  = _clean(parts[1])
        # artist + title combined (already in cleaned, but may differ after strip)
        combo = f'{artist_part} {title_part}'.strip()
        if combo and combo != cleaned:
            candidates.append(combo)
        # artist alone
        if artist_part and artist_part not in candidates:
            candidates.append(artist_part)
        # title alone
        if title_part and title_part not in candidates:
            candidates.append(title_part)
    else:
        # 3. Cleaned version without numbers at the start (track numbers)
        no_num = re.sub(r'^\d+[.\s]+', '', cleaned).strip()
        if no_num and no_num != cleaned:
            candidates.append(no_num)

    # Deduplicate while preserving order
    seen: set = set()
    unique = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            unique.append(c)
    return unique


# ── iTunes ───────────────────────────────────────────────────────────────────

def _search_itunes(query: str, limit: int, seen: set) -> List[Dict]:
    results = []
    try:
        for entity in ('album', 'song'):
            if len(results) >= limit:
                break
            params = {
                'term':    query,
                'media':   'music',
                'entity':  entity,
                'limit':   limit * 3,
                'country': 'US',
            }
            resp = requests.get(ITUNES_URL, params=params,
                                timeout=TIMEOUT, headers=_HEADERS)
            resp.raise_for_status()
            data = resp.json()

            for item in data.get('results', []):
                raw = item.get('artworkUrl100', '')
                if not raw:
                    continue
                art_url = raw.replace('100x100bb', '600x600bb')
                if art_url in seen:
                    continue
                seen.add(art_url)

                results.append({
                    'title':       item.get('collectionName') or item.get('trackName', ''),
                    'artist':      item.get('artistName', ''),
                    'album':       item.get('collectionName', ''),
                    'artwork_url': art_url,
                    'thumb_url':   raw.replace('100x100bb', '150x150bb'),
                })
                if len(results) >= limit:
                    break
    except Exception:
        pass
    return results


# ── Deezer ────────────────────────────────────────────────────────────────────

def _search_deezer(query: str, limit: int, seen: set) -> List[Dict]:
    results = []
    try:
        params = {'q': query, 'limit': limit * 2}
        resp = requests.get(DEEZER_URL, params=params,
                            timeout=TIMEOUT, headers=_HEADERS)
        resp.raise_for_status()
        data = resp.json()

        for item in data.get('data', []):
            album   = item.get('album', {})
            art_url = (album.get('cover_xl') or album.get('cover_big')
                       or album.get('cover', ''))
            if not art_url or art_url in seen:
                continue
            seen.add(art_url)

            results.append({
                'title':       album.get('title', item.get('title', '')),
                'artist':      item.get('artist', {}).get('name', ''),
                'album':       album.get('title', ''),
                'artwork_url': art_url,
                'thumb_url':   album.get('cover_medium', art_url),
            })
            if len(results) >= limit:
                break
    except Exception:
        pass
    return results


# ── MusicBrainz + Cover Art Archive ──────────────────────────────────────────

def _search_musicbrainz(query: str, limit: int, seen: set) -> List[Dict]:
    """
    Search MusicBrainz for releases, then pull front covers from the
    Cover Art Archive.  Good fallback for non-mainstream / non-English music
    that iTunes and Deezer miss.
    """
    results = []
    try:
        params = {
            'query': query,
            'fmt':   'json',
            'limit': min(limit * 4, 20),
        }
        resp = requests.get(MUSICBRAINZ_URL, params=params,
                            timeout=TIMEOUT, headers=_HEADERS)
        resp.raise_for_status()
        releases = resp.json().get('releases', [])

        for release in releases:
            if len(results) >= limit:
                break
            mbid = release.get('id', '')
            if not mbid:
                continue
            try:
                caa_resp = requests.get(
                    f'{CAA_URL}/{mbid}/front-500',
                    timeout=TIMEOUT,
                    headers=_HEADERS,
                    allow_redirects=True,
                )
                if (caa_resp.status_code == 200
                        and 'image' in caa_resp.headers.get('Content-Type', '')):
                    art_url = caa_resp.url
                    if art_url in seen:
                        continue
                    seen.add(art_url)
                    credits     = release.get('artist-credit', [])
                    artist_name = (credits[0].get('artist', {}).get('name', '')
                                   if credits else '')
                    results.append({
                        'title':       release.get('title', ''),
                        'artist':      artist_name,
                        'album':       release.get('title', ''),
                        'artwork_url': art_url,
                        'thumb_url':   art_url,
                    })
            except Exception:
                continue
    except Exception:
        pass
    return results


def _normalize_image(data: bytes) -> Optional[bytes]:
    """
    Decode and re-encode image bytes to a clean JPEG.
    Strips problematic metadata/profiles and rejects invalid payloads.
    """
    try:
        with Image.open(BytesIO(data)) as img:
            img.load()

            # Flatten alpha/transparency onto black background for JPEG output.
            if img.mode in ('RGBA', 'LA'):
                bg = Image.new('RGB', img.size, (0, 0, 0))
                alpha = img.getchannel('A')
                bg.paste(img.convert('RGB'), mask=alpha)
                img = bg
            elif img.mode != 'RGB':
                img = img.convert('RGB')

            # Cap very large images to keep memory use predictable.
            max_side = max(img.width, img.height)
            if max_side > 1400:
                ratio = 1400 / float(max_side)
                new_size = (
                    max(1, int(img.width * ratio)),
                    max(1, int(img.height * ratio)),
                )
                img = img.resize(new_size, Image.Resampling.LANCZOS)

            out = BytesIO()
            img.save(out, format='JPEG', quality=90, optimize=True)
            encoded = out.getvalue()
            return encoded if len(encoded) > 512 else None
    except Exception:
        return None
