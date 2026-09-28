"""
style.py – Generate the application stylesheet from a theme palette.

macOS-flavoured chrome: system font stack, 13 px base size, flat controls
with 6–8 px radii, a sidebar + toolbar layout, and three button roles that
every button in the app maps onto:

* primary     – filled with the accent colour (one per action group)
* secondary   – subtle gray fill with a hairline border
* destructive – gray fill, red text; red fill on hover

Usage:
    from themes import LIGHT
    from style import build_stylesheet
    app.setStyleSheet(build_stylesheet(LIGHT))
"""

import re

from themes import DARK

FONT_STACK = ("-apple-system, 'SF Pro Text', 'SF Pro Display', 'Helvetica Neue', "
              "'Inter', 'Segoe UI', 'Ubuntu', 'Cantarell', Arial, sans-serif")

PRIMARY_BUTTONS = (
    'btnPrimary', 'btnOk', 'btnAutoAssign', 'btnFixMeta', 'btnPlaylistAssign',
    'btnLyricsFetch', 'btnSequenze',
)
SECONDARY_BUTTONS = (
    'btnSecondary', 'btnCancel', 'btnAssignSpecific', 'btnFixMetaManual',
    'btnPlaylistReport', 'btnFixInvalidTags', 'btnLyricsImport', 'btnManageIcons',
    'btnSelectFolder', 'btnRescan', 'btnPlaylistJson',
)
DESTRUCTIVE_BUTTONS = (
    'btnDestructive', 'btnDeleteCover', 'btnClearMeta', 'btnRemoveMissing',
    'btnLyricsRemove',
)


def _sel(names, suffix=''):
    return ', '.join(f'QPushButton#{n}{suffix}' for n in names)


_PX_RE = re.compile(r'(-?\d+(?:\.\d+)?)px')


def scale_stylesheet(qss: str, scale: float) -> str:
    """Multiply every ``Npx`` in *qss* by *scale* (hairlines stay ≥ 1 px)."""
    if abs(scale - 1.0) < 0.01:
        return qss

    def _sub(m):
        v = float(m.group(1)) * scale
        if v > 0:
            v = max(1, round(v))
        elif v < 0:
            v = min(-1, round(v))
        else:
            v = 0
        return f'{int(v)}px'
    return _PX_RE.sub(_sub, qss)


def build_stylesheet(t: dict, scale: float = 1.0) -> str:
    """Return a complete Qt stylesheet using colors from theme dict *t*,
    with every pixel size multiplied by *scale* (see ``scale_stylesheet``)."""
    return scale_stylesheet(_build_base_stylesheet(t), scale)


def _build_base_stylesheet(t: dict) -> str:
    is_dark = t.get('bg', '#ffffff').lower() < '#808080'
    primary_text = '#ffffff'
    sec_fill = t['gray_btn']
    sec_border = t['gray_btn_border']
    sec_hover = t['bg_hover'] if is_dark else '#dfdfe4'
    return f"""
/* ── Global ──────────────────────────────────────────────────────── */
QMainWindow, QDialog {{
    background-color: {t['bg']};
}}

QWidget {{
    background-color: {t['bg']};
    color: {t['text']};
    font-family: {FONT_STACK};
    font-size: 14px;
}}

QLabel {{
    background: transparent;
}}

QToolTip {{
    background-color: {t['tooltip_bg']};
    color: {t['text']};
    border: 1px solid {t['tooltip_border']};
    padding: 6px 10px;
    border-radius: 6px;
    font-size: 12px;
}}

QMenu {{
    background-color: {t['bg_card']};
    color: {t['text']};
    border: 1px solid {t['border']};
    border-radius: 8px;
    padding: 6px 4px;
    font-size: 13px;
}}
QMenu::item {{
    padding: 6px 22px 6px 12px;
    border-radius: 5px;
}}
QMenu::item:selected {{
    background-color: {t['accent']};
    color: white;
}}
QMenu::separator {{
    height: 1px;
    background: {t['separator']};
    margin: 4px 8px;
}}

/* ── Sidebar ─────────────────────────────────────────────────────── */
QWidget#sidebar {{
    background-color: {t['sidebar_bg']};
    border-right: 1px solid {t['separator']};
}}
QWidget#sidebar QLabel {{
    background: transparent;
}}
QLabel#appTitle {{
    font-size: 16px;
    font-weight: 700;
    color: {t['text']};
    letter-spacing: -0.2px;
}}
QLabel#appSubtitle {{
    font-size: 10px;
    color: {t['text_dim']};
    letter-spacing: 1px;
}}
QLabel#sidebarCaption {{
    font-size: 12px;
    font-weight: 600;
    color: {t['text_dim']};
    letter-spacing: 0.4px;
    padding: 0 8px;
}}
QLabel#folderPath {{
    color: {t['text_muted']};
    font-size: 11px;
    padding: 4px 6px;
    background: transparent;
    border: none;
}}
QFrame#statSection {{
    background: transparent;
    border: none;
}}
QScrollArea#sidebarScroll {{
    background: {t['sidebar_bg']};
    border: none;
}}
QScrollArea#sidebarScroll > QWidget > QWidget {{
    background: {t['sidebar_bg']};
}}

/* ── Toolbar ─────────────────────────────────────────────────────── */
QWidget#toolbar {{
    background-color: {t['toolbar_bg']};
    border-bottom: 1px solid {t['separator']};
}}
QWidget#header {{
    background-color: {t['toolbar_bg']};
    border-bottom: 1px solid {t['separator']};
}}
QLabel#filterTitle {{
    color: {t['text']};
    font-size: 17px;
    font-weight: 700;
    letter-spacing: -0.2px;
}}
QLabel#filterCount {{
    color: {t['text_dim']};
    font-size: 12px;
}}
QPushButton#btnToolbar {{
    background: transparent;
    border: none;
    border-radius: 6px;
    padding: 4px;
}}
QPushButton#btnToolbar:hover, QPushButton#btnThemeToggle:hover {{
    background: {t['bg_hover']};
}}
QPushButton#btnThemeToggle {{
    background: transparent;
    border: none;
    border-radius: 6px;
    padding: 4px;
}}

/* ── Search field ────────────────────────────────────────────────── */
QLineEdit {{
    background-color: {t['bg_input']};
    color: {t['text']};
    border: 1px solid {t['border']};
    border-radius: 6px;
    padding: 5px 9px;
    font-size: 13px;
    selection-background-color: {t['bg_selection']};
    selection-color: {t['text']};
}}
QLineEdit:focus {{
    border: 2px solid {t['accent']};
    padding: 4px 8px;
}}
QLineEdit#searchBar {{
    border-radius: 8px;
    padding: 5px 12px;
    background-color: {t['bg_hover'] if not is_dark else t['bg_input']};
    border: 1px solid {t['border_light']};
}}
QLineEdit#searchBar:focus {{
    border: 2px solid {t['accent']};
    padding: 4px 11px;
    background-color: {t['bg_input']};
}}

/* ── Action Bar ──────────────────────────────────────────────────── */
QWidget#actionBar {{
    background-color: {t['bg_alt']};
    border-bottom: 1px solid {t['separator']};
}}
QWidget#actionBar QPushButton {{
    padding: 6px 10px;
}}
QFrame#actionGroup {{
    background: transparent;
    border: none;
}}
QFrame#groupSeparator {{
    background-color: {t['separator']};
    max-width: 1px;
    min-width: 1px;
}}
QLabel#groupLabel {{
    color: {t['text_dim']};
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.4px;
}}
QLabel#actionHint, QLabel#playlistInfo {{
    color: {t['text_dim']};
    font-size: 11px;
}}

/* ── Buttons ─────────────────────────────────────────────────────── */
QPushButton {{
    border: none;
    border-radius: 8px;
    padding: 7px 14px;
    font-weight: 500;
    font-size: 14px;
    outline: none;
    min-height: 22px;
}}
QPushButton:focus {{
    outline: none;
}}

{_sel(PRIMARY_BUTTONS)} {{
    background-color: {t['accent']};
    color: {primary_text};
    font-weight: 600;
}}
{_sel(PRIMARY_BUTTONS, ':hover')} {{
    background-color: {t['accent_hover']};
}}
{_sel(PRIMARY_BUTTONS, ':pressed')} {{
    background-color: {t['accent_pressed']};
}}
{_sel(PRIMARY_BUTTONS, ':disabled')} {{
    background-color: {t['disabled_bg']};
    color: {t['disabled_text']};
}}

{_sel(SECONDARY_BUTTONS)} {{
    background-color: {sec_fill};
    color: {t['text']};
    border: 1px solid {sec_border};
}}
{_sel(SECONDARY_BUTTONS, ':hover')} {{
    background-color: {sec_hover};
    border-color: {t['text_dim']};
}}
{_sel(SECONDARY_BUTTONS, ':pressed')} {{
    background-color: {t['border']};
}}
{_sel(SECONDARY_BUTTONS, ':disabled')} {{
    background-color: {t['disabled_bg']};
    color: {t['disabled_text']};
    border-color: {t['disabled_border']};
}}

{_sel(DESTRUCTIVE_BUTTONS)} {{
    background-color: {sec_fill};
    color: {t['red']};
    border: 1px solid {sec_border};
}}
{_sel(DESTRUCTIVE_BUTTONS, ':hover')} {{
    background-color: {t['red']};
    color: white;
    border-color: {t['red']};
}}
{_sel(DESTRUCTIVE_BUTTONS, ':pressed')} {{
    background-color: {t['red_pressed']};
    color: white;
}}
{_sel(DESTRUCTIVE_BUTTONS, ':disabled')} {{
    background-color: {t['disabled_bg']};
    color: {t['disabled_text']};
    border-color: {t['disabled_border']};
}}

QPushButton#btnAutoAssign::menu-indicator {{
    subcontrol-position: right center;
    subcontrol-origin: padding;
    width: 12px;
    right: 6px;
}}

/* Segmented control (List | Grid, dialog filters) */
QPushButton#btnSegLeft, QPushButton#btnSegMid, QPushButton#btnSegRight {{
    background-color: {sec_fill};
    color: {t['text_secondary']};
    border: 1px solid {sec_border};
    padding: 4px 14px;
    font-size: 12px;
    font-weight: 500;
    border-radius: 0;
    min-height: 18px;
}}
QPushButton#btnSegLeft {{
    border-top-left-radius: 7px;
    border-bottom-left-radius: 7px;
}}
QPushButton#btnSegRight {{
    border-top-right-radius: 7px;
    border-bottom-right-radius: 7px;
}}
QPushButton#btnSegMid, QPushButton#btnSegRight {{
    border-left: none;
}}
QPushButton#btnSegLeft:hover, QPushButton#btnSegMid:hover, QPushButton#btnSegRight:hover {{
    background-color: {sec_hover};
}}
QPushButton#btnSegLeft:checked, QPushButton#btnSegMid:checked, QPushButton#btnSegRight:checked {{
    background-color: {t['accent']};
    color: white;
    border-color: {t['accent']};
}}
/* legacy names kept for safety */
QPushButton#btnViewActive {{
    background-color: {t['accent']};
    color: white;
    border-radius: 7px;
    font-size: 12px;
    padding: 4px 12px;
}}
QPushButton#btnViewInactive {{
    background-color: {sec_fill};
    color: {t['text_secondary']};
    border: 1px solid {sec_border};
    border-radius: 7px;
    font-size: 12px;
    padding: 4px 12px;
}}

/* ── Check boxes ─────────────────────────────────────────────────── */
QCheckBox {{
    spacing: 8px;
    background: transparent;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid {t['border']};
    background: {t['bg_input']};
}}
QCheckBox::indicator:checked {{
    background: {t['accent']};
    border-color: {t['accent']};
}}
QCheckBox::indicator:hover {{
    border-color: {t['accent']};
}}

/* ── Spin boxes / combos ─────────────────────────────────────────── */
QSpinBox, QDoubleSpinBox, QComboBox {{
    background-color: {t['bg_input']};
    color: {t['text']};
    border: 1px solid {t['border']};
    border-radius: 6px;
    padding: 4px 8px;
    min-height: 20px;
}}
QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border: 2px solid {t['accent']};
    padding: 3px 7px;
}}
QComboBox::drop-down {{
    border: none;
    width: 22px;
}}
QComboBox QAbstractItemView {{
    background: {t['bg_card']};
    color: {t['text']};
    border: 1px solid {t['border']};
    selection-background-color: {t['accent']};
    selection-color: white;
}}

/* ── Song Table ──────────────────────────────────────────────────── */
QTableWidget {{
    background-color: {t['bg_alt']};
    alternate-background-color: {t['bg_alt']};
    border: none;
    gridline-color: transparent;
    selection-background-color: {t['bg_selection']};
    selection-color: {t['text']};
    outline: none;
}}
QTableWidget::item {{
    border: none;
    border-bottom: 1px solid {t['border_light']};
    padding: 4px 10px;
}}
QTableWidget::item:hover {{
    background-color: {t['bg_hover']};
}}
QTableWidget::item:selected {{
    background-color: {t['bg_selection']};
    color: {t['text']};
}}
QTableWidget::item:focus {{
    background-color: {t['bg_selection']};
    outline: none;
}}
QHeaderView {{
    background-color: {t['bg_alt']};
}}
QHeaderView::section {{
    background-color: {t['bg_alt']};
    color: {t['text_dim']};
    padding: 6px 10px;
    border: none;
    border-bottom: 1px solid {t['separator']};
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.3px;
}}
QHeaderView::section:hover {{
    color: {t['text_secondary']};
}}
QTableWidget#healthTable {{
    background-color: {t['bg_card']};
    border: 1px solid {t['border_light']};
    border-radius: 8px;
}}

/* ── Scrollbars (overlay style) ──────────────────────────────────── */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {t['scrollbar']};
    border-radius: 3px;
    min-height: 40px;
}}
QScrollBar::handle:vertical:hover {{
    background: {t['text_dim']};
}}
QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical,
QScrollBar::sub-page:vertical {{
    background: none;
    height: 0;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {t['scrollbar']};
    border-radius: 3px;
    min-width: 40px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {t['text_dim']};
}}
QScrollBar::add-line:horizontal,
QScrollBar::sub-line:horizontal,
QScrollBar::add-page:horizontal,
QScrollBar::sub-page:horizontal {{
    background: none;
    width: 0;
}}

/* ── Status Bar ──────────────────────────────────────────────────── */
QStatusBar {{
    background-color: {t['toolbar_bg']};
    border-top: 1px solid {t['separator']};
    color: {t['text_dim']};
    font-size: 11px;
    padding: 0 10px;
}}
QStatusBar QLabel {{
    color: {t['text_muted']};
    font-size: 11px;
}}
QStatusBar::item {{
    border: none;
}}

/* ── Progress Bar ────────────────────────────────────────────────── */
QProgressBar {{
    background-color: {t['border_light']};
    border-radius: 3px;
    border: none;
    text-align: center;
    color: transparent;
    max-height: 6px;
}}
QProgressBar::chunk {{
    background-color: {t['accent']};
    border-radius: 3px;
}}

/* ── Dialog chrome ───────────────────────────────────────────────── */
QDialog#imagePickerDialog, QDialog#progressDialog {{
    background-color: {t['bg']};
}}
QLabel#dialogTitle {{
    font-size: 17px;
    font-weight: 600;
    color: {t['text']};
    letter-spacing: -0.2px;
}}
QLabel#dialogSubtitle {{
    font-size: 12px;
    color: {t['text_muted']};
}}
QLabel#progressTitle {{
    font-size: 16px;
    font-weight: 600;
    color: {t['text']};
}}
QLabel#progressSong {{
    font-size: 12px;
    color: {t['text_muted']};
}}
QFrame#imageCard {{
    background-color: {t['bg_card']};
    border-radius: 12px;
    border: 2px solid transparent;
}}
QFrame#imageCard:hover {{
    border-color: {t['border']};
    background-color: {t['bg_hover']};
}}
QFrame#imageCardSelected {{
    background-color: {t['bg_hover']};
    border-radius: 12px;
    border: 2px solid {t['accent']};
}}
QTextEdit {{
    background-color: {t['bg_card']};
    color: {t['text']};
    border: 1px solid {t['border_light']};
    border-radius: 8px;
    padding: 6px;
    selection-background-color: {t['bg_selection']};
}}
QFrame#settingsPathCard {{
    background-color: {t['bg_card']};
    border: 1px solid {t['border_light']};
    border-radius: 12px;
}}

/* ── Table / View toolbar (legacy) ───────────────────────────────── */
QWidget#tableBar {{
    background-color: {t['toolbar_bg']};
    border-bottom: 1px solid {t['separator']};
}}

/* ── Grid view ───────────────────────────────────────────────────── */
QListWidget#songGrid {{
    background-color: {t['bg_alt']};
    border: none;
    outline: none;
    padding: 12px;
}}
QListWidget#songGrid::item {{
    background-color: transparent;
    border-radius: 10px;
    padding: 6px;
    color: {t['text']};
    font-size: 13px;
}}
QListWidget#songGrid::item:selected {{
    background-color: {t['bg_selection']};
    color: {t['text']};
}}
QListWidget#songGrid::item:hover:!selected {{
    background-color: {t['bg_hover']};
}}
QFrame#songGridCard {{
    background-color: transparent;
    border: none;
    border-radius: 12px;
}}
QListWidget#tagGrid {{
    background-color: {t['bg_card']};
    border: 1px solid {t['border_light']};
    border-radius: 12px;
    outline: none;
    padding: 12px;
}}
QListWidget#tagGrid::item {{
    background-color: {t['bg']};
    border: 1px solid {t['border_light']};
    border-radius: 10px;
    padding: 14px 10px;
    margin: 3px;
    color: {t['text']};
    font-size: 13px;
}}
QListWidget#tagGrid::item:hover {{
    border-color: {t['accent']};
    background-color: {t['bg_hover']};
}}
QListWidget#tagGrid::item:selected {{
    border-color: {t['accent']};
    background-color: {t['bg_selection']};
}}

/* ── Message Box ─────────────────────────────────────────────────── */
QMessageBox {{
    background-color: {t['msgbox_bg']};
}}
QMessageBox QLabel {{
    color: {t['text']};
    font-size: 13px;
}}
QMessageBox QPushButton {{
    background-color: {sec_fill};
    color: {t['text']};
    border: 1px solid {sec_border};
    border-radius: 7px;
    padding: 5px 18px;
    min-width: 80px;
    font-weight: 500;
    font-size: 13px;
}}
QMessageBox QPushButton:hover {{
    background-color: {sec_hover};
}}
QMessageBox QPushButton:default {{
    background-color: {t['accent']};
    color: white;
    border-color: {t['accent']};
    font-weight: 600;
}}
QMessageBox QPushButton:default:hover {{
    background-color: {t['accent_hover']};
}}

/* ── Player Bar ──────────────────────────────────────────────────── */
QFrame#playerBar {{
    background-color: {t['player_bg']};
    border-top: 1px solid {t['player_border']};
}}
QLabel#playerTitle {{
    color: {t['text']};
    font-size: 17px;
    font-weight: 600;
}}
QLabel#playerArtist {{
    color: {t['text_secondary']};
    font-size: 14px;
}}
QLabel#playerAlbum {{
    color: {t['text_muted']};
    font-size: 13px;
}}
QLabel#playerTime {{
    color: {t['text_muted']};
    font-size: 12px;
}}
QPushButton#playerBtn {{
    background: transparent;
    border: none;
    border-radius: 28px;
}}
QPushButton#playerBtn:hover {{
    background: {t['bg_hover']};
}}
QPushButton#playerPlayBtn {{
    background: {t['accent']};
    border: none;
    border-radius: 36px;
}}
QPushButton#playerPlayBtn:hover {{
    background: {t['accent_hover']};
}}

/* ── Sliders (player progress / volume, trimmer) ─────────────────── */
QSlider::groove:horizontal {{
    background: {t['border']};
    height: 4px;
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {t['bg_card']};
    border: 1px solid {t['border']};
    width: 16px;
    height: 16px;
    border-radius: 8px;
    margin: -6px 0;
}}
QSlider::handle:horizontal:hover {{
    border-color: {t['accent']};
}}
QSlider::sub-page:horizontal {{
    background: {t['accent']};
    border-radius: 2px;
}}

/* ── Toast ───────────────────────────────────────────────────────── */
QLabel#toast {{
    background-color: {'#f5f5f7' if is_dark else '#1d1d1f'};
    color: {'#1d1d1f' if is_dark else '#ffffff'};
    border-radius: 10px;
    padding: 9px 16px;
    font-size: 12px;
    font-weight: 500;
}}
"""


# Backward-compatible default (dark theme)
STYLESHEET = build_stylesheet(DARK)
