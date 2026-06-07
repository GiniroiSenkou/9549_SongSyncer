"""
icons.py – Self-contained painted icons for SongSyncer.

Every icon is drawn with QPainter so the app ships with zero external icon
files.  Callers pass *color* to get theme-aware tints.

Custom icon directory
---------------------
Call ``set_custom_icon_dir(path)`` to point at a folder of .png/.svg files
whose basenames match icon names (e.g. ``folder.png``).  If such a file
exists it takes priority over the painted fallback.
"""

import os
from PyQt5.QtGui import (
    QPixmap, QPainter, QColor, QPen, QIcon, QPolygonF, QBrush,
)
from PyQt5.QtCore import Qt, QPointF, QRectF
from image_utils import icon_from_file

_custom_dir: str = ''


def set_custom_icon_dir(path: str):
    global _custom_dir
    _custom_dir = path


# ── public API ────────────────────────────────────────────────────────────────

def get_icon(name: str, size: int = 20, color: str = '#94a3b8') -> QIcon:
    """Return a QIcon for *name*.  Checks the custom dir first."""
    if _custom_dir:
        for ext in ('.svg', '.png', '.ico'):
            p = os.path.join(_custom_dir, name + ext)
            if os.path.exists(p):
                if ext == '.png':
                    return icon_from_file(p, size)
                return QIcon(p)
    fn = _ICONS.get(name)
    if fn:
        return _make(size, color, fn)
    return QIcon()


# ── helpers ───────────────────────────────────────────────────────────────────

def _make(size, color, draw_fn):
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    draw_fn(p, float(size), QColor(color))
    p.end()
    return QIcon(pm)


def _pen(p, c, s, w=1.8):
    pen = QPen(c, max(w, s / 12))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)


def _fill(p, c):
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(c))


# ── icon draw functions ───────────────────────────────────────────────────────

def _folder(p, s, c):
    _pen(p, c, s)
    m = s * 0.18
    p.drawRoundedRect(QRectF(m, s * 0.35, s - 2 * m, s * 0.48), 3, 3)
    p.drawLine(QPointF(m + 1, s * 0.35), QPointF(m + 1, s * 0.26))
    p.drawLine(QPointF(m + 1, s * 0.26), QPointF(s * 0.42, s * 0.26))
    p.drawLine(QPointF(s * 0.42, s * 0.26), QPointF(s * 0.48, s * 0.35))


def _refresh(p, s, c):
    _pen(p, c, s, 2.0)
    m = s * 0.22
    from PyQt5.QtCore import QRect
    p.drawArc(QRect(int(m), int(m), int(s - 2 * m), int(s - 2 * m)), 30 * 16, 280 * 16)
    # arrowhead
    ax, ay = s * 0.7, s * 0.2
    _fill(p, c)
    p.drawPolygon(QPolygonF([
        QPointF(ax, ay - s * 0.08),
        QPointF(ax + s * 0.1, ay + s * 0.04),
        QPointF(ax - s * 0.04, ay + s * 0.06),
    ]))


def _bolt(p, s, c):
    _fill(p, c)
    m = s * 0.2
    p.drawPolygon(QPolygonF([
        QPointF(s * 0.55, m),
        QPointF(s * 0.3, s * 0.48),
        QPointF(s * 0.48, s * 0.48),
        QPointF(s * 0.42, s - m),
        QPointF(s * 0.7, s * 0.44),
        QPointF(s * 0.52, s * 0.44),
    ]))


def _search(p, s, c):
    _pen(p, c, s, 2.0)
    r = s * 0.24
    cx, cy = s * 0.42, s * 0.42
    p.drawEllipse(QPointF(cx, cy), r, r)
    lx = cx + r * 0.7
    ly = cy + r * 0.7
    p.drawLine(QPointF(lx, ly), QPointF(s * 0.8, s * 0.8))


def _trash(p, s, c):
    _pen(p, c, s)
    m = s * 0.25
    # body
    p.drawRoundedRect(QRectF(m, s * 0.35, s - 2 * m, s * 0.48), 2, 2)
    # lid
    p.drawLine(QPointF(m - s * 0.04, s * 0.32), QPointF(s - m + s * 0.04, s * 0.32))
    # handle
    p.drawLine(QPointF(s * 0.4, s * 0.32), QPointF(s * 0.4, s * 0.22))
    p.drawLine(QPointF(s * 0.4, s * 0.22), QPointF(s * 0.6, s * 0.22))
    p.drawLine(QPointF(s * 0.6, s * 0.22), QPointF(s * 0.6, s * 0.32))


def _wand(p, s, c):
    _pen(p, c, s, 2.0)
    p.drawLine(QPointF(s * 0.2, s * 0.8), QPointF(s * 0.7, s * 0.3))
    # star sparkle at tip
    cx, cy = s * 0.76, s * 0.22
    r = s * 0.08
    for angle in [0, 90]:
        import math
        rad = math.radians(angle)
        dx, dy = r * math.cos(rad), r * math.sin(rad)
        p.drawLine(QPointF(cx - dx, cy - dy), QPointF(cx + dx, cy + dy))


def _pencil(p, s, c):
    _pen(p, c, s, 1.8)
    p.drawLine(QPointF(s * 0.25, s * 0.75), QPointF(s * 0.72, s * 0.28))
    p.drawLine(QPointF(s * 0.72, s * 0.28), QPointF(s * 0.78, s * 0.22))
    p.drawLine(QPointF(s * 0.78, s * 0.22), QPointF(s * 0.82, s * 0.26))
    p.drawLine(QPointF(s * 0.82, s * 0.26), QPointF(s * 0.75, s * 0.33))
    p.drawLine(QPointF(s * 0.2, s * 0.82), QPointF(s * 0.25, s * 0.75))


def _x_mark(p, s, c):
    _pen(p, c, s, 2.2)
    m = s * 0.28
    p.drawLine(QPointF(m, m), QPointF(s - m, s - m))
    p.drawLine(QPointF(s - m, m), QPointF(m, s - m))


def _document(p, s, c):
    _pen(p, c, s)
    m = s * 0.2
    fold = s * 0.18
    path = [
        QPointF(m, m), QPointF(s - m - fold, m),
        QPointF(s - m, m + fold), QPointF(s - m, s - m),
        QPointF(m, s - m),
    ]
    p.drawPolygon(QPolygonF(path))
    p.drawLine(QPointF(s - m - fold, m), QPointF(s - m - fold, m + fold))
    p.drawLine(QPointF(s - m - fold, m + fold), QPointF(s - m, m + fold))


def _tag(p, s, c):
    _pen(p, c, s)
    m = s * 0.18
    p.drawPolygon(QPolygonF([
        QPointF(m, m), QPointF(s * 0.55, m),
        QPointF(s - m, s * 0.5),
        QPointF(s * 0.55, s - m), QPointF(m, s - m),
    ]))
    # dot
    _fill(p, c)
    p.drawEllipse(QPointF(s * 0.32, s * 0.5), s * 0.06, s * 0.06)


def _list_view(p, s, c):
    _pen(p, c, s, 2.0)
    for frac in (0.3, 0.5, 0.7):
        y = s * frac
        p.drawLine(QPointF(s * 0.2, y), QPointF(s * 0.8, y))


def _grid_view(p, s, c):
    _pen(p, c, s, 1.6)
    m = s * 0.2
    w = (s - 2 * m - s * 0.1) / 2
    gap = s * 0.1
    for r in range(2):
        for cc in range(2):
            x = m + cc * (w + gap)
            y = m + r * (w + gap)
            p.drawRoundedRect(QRectF(x, y, w, w), 2, 2)


def _sun(p, s, c):
    _pen(p, c, s, 1.6)
    cx, cy = s / 2, s / 2
    r = s * 0.16
    p.drawEllipse(QPointF(cx, cy), r, r)
    import math
    ray_in, ray_out = s * 0.24, s * 0.36
    for i in range(8):
        angle = math.radians(i * 45)
        p.drawLine(
            QPointF(cx + ray_in * math.cos(angle), cy + ray_in * math.sin(angle)),
            QPointF(cx + ray_out * math.cos(angle), cy + ray_out * math.sin(angle)),
        )


def _moon(p, s, c):
    _pen(p, c, s, 1.8)
    # crescent: outer circle minus inner offset circle
    from PyQt5.QtGui import QPainterPath
    path = QPainterPath()
    cx, cy = s * 0.48, s * 0.5
    r = s * 0.28
    path.addEllipse(QPointF(cx, cy), r, r)
    cut = QPainterPath()
    cut.addEllipse(QPointF(cx + s * 0.16, cy - s * 0.08), r * 0.85, r * 0.85)
    crescent = path.subtracted(cut)
    _fill(p, c)
    p.drawPath(crescent)


def _play(p, s, c):
    _fill(p, c)
    m = s * 0.25
    p.drawPolygon(QPolygonF([
        QPointF(m + s * 0.05, m),
        QPointF(s - m, s / 2),
        QPointF(m + s * 0.05, s - m),
    ]))


def _pause(p, s, c):
    _fill(p, c)
    w = s * 0.13
    m = s * 0.28
    p.drawRoundedRect(QRectF(m, m, w, s - 2 * m), 2, 2)
    p.drawRoundedRect(QRectF(s - m - w, m, w, s - 2 * m), 2, 2)


def _stop(p, s, c):
    _fill(p, c)
    m = s * 0.28
    p.drawRoundedRect(QRectF(m, m, s - 2 * m, s - 2 * m), 2, 2)


def _skip_back(p, s, c):
    _fill(p, c)
    # bar
    p.drawRect(QRectF(s * 0.2, s * 0.28, s * 0.07, s * 0.44))
    # triangle pointing left
    p.drawPolygon(QPolygonF([
        QPointF(s * 0.75, s * 0.25),
        QPointF(s * 0.35, s * 0.5),
        QPointF(s * 0.75, s * 0.75),
    ]))


def _skip_forward(p, s, c):
    _fill(p, c)
    # triangle pointing right
    p.drawPolygon(QPolygonF([
        QPointF(s * 0.25, s * 0.25),
        QPointF(s * 0.65, s * 0.5),
        QPointF(s * 0.25, s * 0.75),
    ]))
    # bar
    p.drawRect(QRectF(s * 0.73, s * 0.28, s * 0.07, s * 0.44))


def _volume(p, s, c):
    _fill(p, c)
    # speaker body
    p.drawPolygon(QPolygonF([
        QPointF(s * 0.18, s * 0.4), QPointF(s * 0.32, s * 0.4),
        QPointF(s * 0.48, s * 0.25), QPointF(s * 0.48, s * 0.75),
        QPointF(s * 0.32, s * 0.6), QPointF(s * 0.18, s * 0.6),
    ]))
    # waves
    _pen(p, c, s, 1.6)
    from PyQt5.QtCore import QRect
    p.drawArc(QRect(int(s * 0.46), int(s * 0.32), int(s * 0.2), int(s * 0.36)), -60 * 16, 120 * 16)
    p.drawArc(QRect(int(s * 0.5), int(s * 0.22), int(s * 0.3), int(s * 0.56)), -60 * 16, 120 * 16)


def _image(p, s, c):
    _pen(p, c, s)
    m = s * 0.18
    p.drawRoundedRect(QRectF(m, m, s - 2 * m, s - 2 * m), 3, 3)
    # mountain
    p.drawLine(QPointF(m + 2, s * 0.7), QPointF(s * 0.4, s * 0.45))
    p.drawLine(QPointF(s * 0.4, s * 0.45), QPointF(s * 0.55, s * 0.58))
    p.drawLine(QPointF(s * 0.55, s * 0.58), QPointF(s * 0.7, s * 0.4))
    p.drawLine(QPointF(s * 0.7, s * 0.4), QPointF(s - m - 2, s * 0.7))
    # sun
    _fill(p, c)
    p.drawEllipse(QPointF(s * 0.65, s * 0.32), s * 0.06, s * 0.06)


def _text(p, s, c):
    _pen(p, c, s, 2.0)
    # lines of text
    p.drawLine(QPointF(s * 0.18, s * 0.3), QPointF(s * 0.82, s * 0.3))
    p.drawLine(QPointF(s * 0.18, s * 0.5), QPointF(s * 0.68, s * 0.5))
    p.drawLine(QPointF(s * 0.18, s * 0.7), QPointF(s * 0.75, s * 0.7))


def _music_note(p, s, c):
    _pen(p, c, s, 2.0)
    # note stem
    p.drawLine(QPointF(s * 0.55, s * 0.2), QPointF(s * 0.55, s * 0.7))
    # note head
    _fill(p, c)
    p.drawEllipse(QPointF(s * 0.45, s * 0.72), s * 0.12, s * 0.09)


def _chevron_up(p, s, c):
    _pen(p, c, s, 2.2)
    p.drawLine(QPointF(s * 0.25, s * 0.6), QPointF(s * 0.5, s * 0.35))
    p.drawLine(QPointF(s * 0.5, s * 0.35), QPointF(s * 0.75, s * 0.6))


def _chevron_down(p, s, c):
    _pen(p, c, s, 2.2)
    p.drawLine(QPointF(s * 0.25, s * 0.35), QPointF(s * 0.5, s * 0.6))
    p.drawLine(QPointF(s * 0.5, s * 0.6), QPointF(s * 0.75, s * 0.35))


def _palette(p, s, c):
    _pen(p, c, s, 1.8)
    # palette outline
    cx, cy = s * 0.48, s * 0.5
    rx, ry = s * 0.32, s * 0.3
    p.drawEllipse(QPointF(cx, cy), rx, ry)
    # color dots
    _fill(p, c)
    for dx, dy in [(-0.12, -0.1), (0.08, -0.12), (-0.08, 0.08), (0.12, 0.06)]:
        p.drawEllipse(QPointF(cx + s * dx, cy + s * dy), s * 0.04, s * 0.04)


def _shuffle(p, s, c):
    _pen(p, c, s, 1.8)
    # two crossing arrows
    p.drawLine(QPointF(s * 0.2, s * 0.35), QPointF(s * 0.55, s * 0.65))
    p.drawLine(QPointF(s * 0.2, s * 0.65), QPointF(s * 0.55, s * 0.35))
    # right-side arrows
    p.drawLine(QPointF(s * 0.55, s * 0.35), QPointF(s * 0.8, s * 0.35))
    p.drawLine(QPointF(s * 0.55, s * 0.65), QPointF(s * 0.8, s * 0.65))
    # arrowheads
    _fill(p, c)
    p.drawPolygon(QPolygonF([
        QPointF(s * 0.75, s * 0.28), QPointF(s * 0.85, s * 0.35),
        QPointF(s * 0.75, s * 0.42),
    ]))
    p.drawPolygon(QPolygonF([
        QPointF(s * 0.75, s * 0.58), QPointF(s * 0.85, s * 0.65),
        QPointF(s * 0.75, s * 0.72),
    ]))


def _warning(p, s, c):
    _pen(p, c, s, 1.8)
    p.drawPolygon(QPolygonF([
        QPointF(s * 0.5, s * 0.14),
        QPointF(s * 0.86, s * 0.8),
        QPointF(s * 0.14, s * 0.8),
    ]))
    _fill(p, c)
    p.drawRoundedRect(QRectF(s * 0.47, s * 0.34, s * 0.06, s * 0.22), 1, 1)
    p.drawEllipse(QPointF(s * 0.5, s * 0.67), s * 0.035, s * 0.035)


def _gear(p, s, c):
    """Eight-tooth cog drawn as a stroked outline with a hub ring."""
    import math
    _pen(p, c, s, 1.8)
    p.setBrush(Qt.NoBrush)
    cx, cy = s * 0.5, s * 0.5
    # Eight teeth: alternate between tip and base radius around the rim.
    tip  = s * 0.46
    base = s * 0.34
    pts = []
    teeth = 8
    half_w = math.pi / (teeth * 2.4)   # tooth angular half-width
    for i in range(teeth):
        a_center = (-math.pi / 2) + (i * 2 * math.pi / teeth)
        # base edge (in), tip edge (out, two corners), back to base
        a_l, a_r = a_center - half_w, a_center + half_w
        pts.append(QPointF(cx + base * math.cos(a_l - half_w * 0.5),
                           cy + base * math.sin(a_l - half_w * 0.5)))
        pts.append(QPointF(cx + tip * math.cos(a_l),
                           cy + tip * math.sin(a_l)))
        pts.append(QPointF(cx + tip * math.cos(a_r),
                           cy + tip * math.sin(a_r)))
        pts.append(QPointF(cx + base * math.cos(a_r + half_w * 0.5),
                           cy + base * math.sin(a_r + half_w * 0.5)))
    p.drawPolygon(QPolygonF(pts))
    # Central hub ring.
    p.drawEllipse(QPointF(cx, cy), s * 0.13, s * 0.13)


# ── registry ──────────────────────────────────────────────────────────────────

_ICONS = {
    'folder': _folder,
    'refresh': _refresh,
    'bolt': _bolt,
    'search': _search,
    'trash': _trash,
    'wand': _wand,
    'pencil': _pencil,
    'x_mark': _x_mark,
    'document': _document,
    'tag': _tag,
    'list': _list_view,
    'grid': _grid_view,
    'sun': _sun,
    'moon': _moon,
    'play': _play,
    'pause': _pause,
    'stop': _stop,
    'skip_back': _skip_back,
    'skip_forward': _skip_forward,
    'volume': _volume,
    'image': _image,
    'text': _text,
    'music_note': _music_note,
    'chevron_up': _chevron_up,
    'chevron_down': _chevron_down,
    'palette': _palette,
    'shuffle': _shuffle,
    'warning': _warning,
    'gear': _gear,
}
