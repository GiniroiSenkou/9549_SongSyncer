"""
image_utils.py - Shared image sanitization and Qt pixmap helpers.
"""

from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageFile
from PyQt5.QtGui import QPixmap, QIcon
from PyQt5.QtCore import Qt

ImageFile.LOAD_TRUNCATED_IMAGES = True

_DECODE_FAILURES = 0
_FILE_SANITIZED_CACHE: dict[str, bytes | None] = {}
_FILE_DATA_URI_CACHE: dict[tuple[str, int], str] = {}
_FILE_ICON_CACHE: dict[tuple[str, int], QIcon] = {}


def reset_decode_failures():
    global _DECODE_FAILURES
    _DECODE_FAILURES = 0


def get_decode_failures() -> int:
    return _DECODE_FAILURES


def sanitize_image_bytes(data: bytes) -> bytes | None:
    """Decode and re-encode image bytes to strip broken color profiles."""
    try:
        with Image.open(BytesIO(data)) as src:
            src.load()
            if src.mode not in ('RGB', 'RGBA'):
                src = src.convert('RGBA')
            else:
                src = src.copy()
            # Rebuild from raw pixels to guarantee metadata/profile stripping.
            img = Image.frombytes(src.mode, src.size, src.tobytes())
            out = BytesIO()
            img.save(out, format='PNG', optimize=True, icc_profile=None)
            clean = out.getvalue()
            return clean if clean else None
    except Exception:
        return None


def _sanitize_image_file(path: str) -> bytes | None:
    key = str(Path(path).resolve())
    if key in _FILE_SANITIZED_CACHE:
        return _FILE_SANITIZED_CACHE[key]
    try:
        raw = Path(key).read_bytes()
    except OSError:
        _FILE_SANITIZED_CACHE[key] = None
        return None
    clean = sanitize_image_bytes(raw)
    _FILE_SANITIZED_CACHE[key] = clean
    return clean


def load_pixmap_from_data(data: bytes) -> QPixmap | None:
    """Return QPixmap from sanitized bytes, or None on decode failure."""
    global _DECODE_FAILURES
    clean = sanitize_image_bytes(data)
    if not clean:
        _DECODE_FAILURES += 1
        return None

    px = QPixmap()
    if not px.loadFromData(clean):
        _DECODE_FAILURES += 1
        return None
    if px.isNull():
        _DECODE_FAILURES += 1
        return None
    return px


def decode_cover_thumbnails(
    data: bytes,
    thumb_size: int,
    grid_size: int,
) -> tuple[bytes, bytes] | None:
    """Decode cover bytes and return two pre-scaled raw RGBA buffers.

    Runs entirely with PIL — no Qt objects created — so it is safe to call
    from any thread.  Returns (thumb_rgba, grid_rgba) where each buffer is
    exactly size×size×4 bytes in RGBA order, or None on failure.
    """
    try:
        with Image.open(BytesIO(data)) as img:
            img.load()
            img = img.convert('RGBA')
            w, h = img.size
            m = min(w, h)
            img = img.crop(((w - m) // 2, (h - m) // 2, (w + m) // 2, (h + m) // 2))
            grid_img  = img.resize((grid_size, grid_size), Image.LANCZOS)
            thumb_img = grid_img.resize((thumb_size, thumb_size), Image.LANCZOS)
            return thumb_img.tobytes(), grid_img.tobytes()
    except Exception:
        return None


def load_cropped_pixmap(data: bytes, size: int) -> QPixmap | None:
    """Load image bytes and return a center-cropped square pixmap."""
    px = load_pixmap_from_data(data)
    if not px:
        return None
    scaled = px.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    x = max(0, (scaled.width() - size) // 2)
    y = max(0, (scaled.height() - size) // 2)
    return scaled.copy(x, y, size, size)


def icon_from_file(path: str, size: int = 32) -> QIcon:
    key = str(Path(path).resolve())
    cache_key = (key, int(size))
    if cache_key in _FILE_ICON_CACHE:
        return _FILE_ICON_CACHE[cache_key]

    clean = _sanitize_image_file(key)
    if not clean:
        icon = QIcon()
        _FILE_ICON_CACHE[cache_key] = icon
        return icon

    px = load_cropped_pixmap(clean, size)
    if not px:
        icon = QIcon()
    else:
        icon = QIcon(px)
    _FILE_ICON_CACHE[cache_key] = icon
    return icon


def data_uri_from_file(path: str, size: int = 16) -> str:
    key = str(Path(path).resolve())
    cache_key = (key, int(size))
    if cache_key in _FILE_DATA_URI_CACHE:
        return _FILE_DATA_URI_CACHE[cache_key]

    clean = _sanitize_image_file(key)
    if not clean:
        _FILE_DATA_URI_CACHE[cache_key] = ''
        return ''
    try:
        with Image.open(BytesIO(clean)) as img:
            img.load()
            if img.mode not in ('RGB', 'RGBA'):
                img = img.convert('RGBA')
            if size > 0:
                img = img.resize((size, size), Image.Resampling.LANCZOS)
            out = BytesIO()
            img.save(out, format='PNG', optimize=True, icc_profile=None)
            payload = out.getvalue()
    except Exception:
        payload = clean
    encoded = base64.b64encode(payload).decode('ascii')
    uri = f'data:image/png;base64,{encoded}'
    _FILE_DATA_URI_CACHE[cache_key] = uri
    return uri
