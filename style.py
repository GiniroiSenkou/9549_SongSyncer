"""
style.py – Generate the application stylesheet from a theme palette.

Usage:
    from themes import DARK
    from style import build_stylesheet
    app.setStyleSheet(build_stylesheet(DARK))
"""

from themes import DARK


def build_stylesheet(t: dict) -> str:
    """Return a complete Qt stylesheet using colors from theme dict *t*."""
    return f"""
/* ── Global ──────────────────────────────────────────────────────── */
QMainWindow, QDialog {{
    background-color: {t['bg']};
}}

QWidget {{
    background-color: {t['bg']};
    color: {t['text']};
    font-family: 'Segoe UI', 'Inter', 'Ubuntu', Arial, sans-serif;
    font-size: 15px;
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
    font-size: 13px;
}}

QMenu {{
    background-color: {t['bg_card']};
    color: {t['text']};
    border: 1px solid {t['border']};
    border-radius: 6px;
    padding: 6px 0;
    font-size: 14px;
}}
QMenu::item {{
    padding: 8px 24px 8px 16px;
}}
QMenu::item:selected {{
    background-color: {t['bg_hover']};
}}

/* ── Header ──────────────────────────────────────────────────────── */
QWidget#header {{
    background-color: {t['bg_alt']};
    border-bottom: 1px solid {t['border_light']};
}}

QLabel#appTitle {{
    font-size: 24px;
    font-weight: 700;
    color: {t['text']};
    letter-spacing: -0.5px;
}}

QLabel#appSubtitle {{
    font-size: 11px;
    color: {t['text_dim']};
    letter-spacing: 1px;
}}

QLabel#folderPath {{
    color: {t['text_muted']};
    font-size: 12px;
    padding: 6px 10px;
    background: {t['bg_hover']};
    border-radius: 6px;
    border: 1px solid {t['border']};
}}

QLabel#playlistPath {{
    color: {t['text_secondary']};
    font-size: 12px;
    padding: 6px 10px;
    background: {t['bg_input']};
    border-radius: 6px;
    border: 1px solid {t['border']};
}}

QLabel#playlistInfo {{
    color: {t['text_muted']};
    font-size: 12px;
}}

/* ── Stat Cards (inline in header) ───────────────────────────────── */
QFrame#statCardInline {{
    background: transparent;
    border: none;
}}

QLabel#statLabel {{
    font-size: 9px;
    color: {t['text_dim']};
    letter-spacing: 1px;
    font-weight: 600;
}}

/* ── Action Bar ──────────────────────────────────────────────────── */
QWidget#actionBar {{
    background-color: {t['bg_alt']};
    border-bottom: 1px solid {t['border_light']};
}}

QLabel#actionHint {{
    color: {t['text_dim']};
    font-size: 12px;
    font-style: italic;
}}

QLabel#groupLabel {{
    color: {t['text_dim']};
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1.2px;
}}

QFrame#actionGroup {{
    background-color: {t['bg_alt']};
    border: 1px solid {t['border']};
    border-radius: 8px;
}}

QFrame#groupSeparator {{
    background-color: {t['border']};
    max-height: 1px;
}}

/* ── Buttons ─────────────────────────────────────────────────────── */
QPushButton {{
    border: none;
    border-radius: 6px;
    padding: 10px 16px;
    font-weight: 600;
    font-size: 15px;
    outline: none;
}}

QPushButton:focus {{
    outline: none;
}}

QPushButton#btnSelectFolder {{
    background-color: transparent;
    border: 2px solid {t['border']};
    color: {t['text_secondary']};
    padding: 8px 16px;
    border-radius: 6px;
    font-size: 14px;
}}

QPushButton#btnSelectFolder:hover {{
    border-color: {t['accent']};
    color: {t['text']};
    background-color: {t['bg_card']};
}}

QPushButton#btnRescan {{
    background-color: transparent;
    border: 2px solid {t['border']};
    color: {t['text_muted']};
    padding: 8px 12px;
    border-radius: 6px;
    font-size: 14px;
}}

QPushButton#btnRescan:hover {{
    border-color: {t['text_muted']};
    color: {t['text_secondary']};
}}

QPushButton#btnRescan:disabled {{
    color: {t['border']};
    border-color: {t['border_light']};
}}

QPushButton#btnThemeToggle {{
    background-color: transparent;
    border: 2px solid {t['border']};
    color: {t['text_secondary']};
    padding: 8px 12px;
    border-radius: 6px;
}}

QPushButton#btnThemeToggle:hover {{
    border-color: {t['accent']};
    color: {t['text']};
    background-color: {t['bg_card']};
}}

QPushButton#btnAutoAssign {{
    background-color: {t['green']};
    color: white;
}}

QPushButton#btnAutoAssign:hover {{
    background-color: {t['green_hover']};
}}

QPushButton#btnAutoAssign:pressed {{
    background-color: {t['green_pressed']};
}}

QPushButton#btnAutoAssign:disabled {{
    background-color: {t['disabled_bg']};
    color: {t['disabled_text']};
}}

QPushButton#btnAutoAssign::menu-indicator {{
    subcontrol-position: right center;
    subcontrol-origin: padding;
    width: 16px;
    right: 8px;
}}

QPushButton#btnAssignSpecific {{
    background-color: {t['accent']};
    color: white;
}}

QPushButton#btnAssignSpecific:hover {{
    background-color: {t['accent_hover']};
}}

QPushButton#btnAssignSpecific:pressed {{
    background-color: {t['accent_pressed']};
}}

QPushButton#btnAssignSpecific:disabled {{
    background-color: {t['disabled_bg']};
    color: {t['disabled_text']};
}}

QPushButton#btnFixMeta {{
    background-color: {t['orange']};
    color: white;
}}

QPushButton#btnFixMeta:hover {{
    background-color: {t['orange_hover']};
}}

QPushButton#btnFixMeta:pressed {{
    background-color: {t['orange_pressed']};
}}

QPushButton#btnFixMeta:disabled {{
    background-color: {t['disabled_bg']};
    color: {t['disabled_text']};
}}

QPushButton#btnDeleteCover {{
    background-color: {t['red']};
    color: white;
}}

QPushButton#btnDeleteCover:hover {{
    background-color: {t['red_hover']};
}}

QPushButton#btnDeleteCover:pressed {{
    background-color: {t['red_pressed']};
}}

QPushButton#btnDeleteCover:disabled {{
    background-color: {t['disabled_bg']};
    color: {t['disabled_text']};
}}

QPushButton#btnPlaylistJson {{
    background-color: transparent;
    border: 2px solid {t['border']};
    color: {t['text_secondary']};
}}

QPushButton#btnPlaylistJson:hover {{
    border-color: {t['cyan']};
    color: {t['text']};
    background-color: {t['bg_card']};
}}

QPushButton#btnPlaylistAssign {{
    background-color: {t['blue']};
    color: white;
}}

QPushButton#btnPlaylistAssign:hover {{
    background-color: {t['blue_hover']};
}}

QPushButton#btnPlaylistAssign:pressed {{
    background-color: {t['blue_pressed']};
}}

QPushButton#btnPlaylistAssign:disabled {{
    background-color: {t['disabled_bg']};
    color: {t['disabled_text']};
}}

QPushButton#btnPlaylistReport {{
    background-color: transparent;
    border: 2px solid {t['border']};
    color: {t['text_secondary']};
}}

QPushButton#btnPlaylistReport:hover {{
    border-color: {t['accent']};
    color: {t['text']};
    background-color: {t['bg_card']};
}}

QPushButton#btnPlaylistReport:disabled {{
    color: {t['disabled_text']};
    border-color: {t['disabled_border']};
    background-color: transparent;
}}

/* Generic dialog buttons */
QPushButton#btnOk {{
    background-color: {t['accent']};
    color: white;
    min-width: 160px;
}}

QPushButton#btnOk:hover {{
    background-color: {t['accent_hover']};
}}

QPushButton#btnOk:disabled {{
    background-color: {t['disabled_bg']};
    color: {t['disabled_text']};
}}

QPushButton#btnCancel {{
    background-color: transparent;
    border: 2px solid {t['border']};
    color: {t['text_secondary']};
}}

QPushButton#btnCancel:hover {{
    border-color: {t['red']};
    color: {t['red']};
}}

QPushButton#btnFixMetaManual {{
    background-color: {t['cyan']};
    color: white;
}}

QPushButton#btnFixMetaManual:hover {{
    background-color: {t['cyan_hover']};
}}

QPushButton#btnFixMetaManual:pressed {{
    background-color: {t['cyan_pressed']};
}}

QPushButton#btnFixMetaManual:disabled {{
    background-color: {t['disabled_bg']};
    color: {t['disabled_text']};
}}

QPushButton#btnClearMeta {{
    background-color: {t['gray_btn']};
    color: {t['text_secondary']};
    border: 1px solid {t['gray_btn_border']};
}}

QPushButton#btnClearMeta:hover {{
    background-color: {t['red_pressed']};
    color: white;
    border-color: {t['red_pressed']};
}}

QPushButton#btnClearMeta:pressed {{
    background-color: {t['red_pressed']};
    color: white;
}}

QPushButton#btnClearMeta:disabled {{
    background-color: {t['disabled_bg']};
    color: {t['disabled_text']};
    border-color: {t['disabled_border']};
}}

/* ── Song Table ──────────────────────────────────────────────────── */
QTableWidget {{
    background-color: {t['bg']};
    alternate-background-color: {t['bg_input']};
    border: none;
    gridline-color: {t['bg_card']};
    selection-background-color: {t['bg_selection']};
    selection-color: {t['text']};
    outline: none;
}}

QLineEdit {{
    background-color: {t['bg_input']};
    color: {t['text']};
    border: 1px solid {t['border']};
    border-radius: 6px;
    padding: 8px 12px;
    font-size: 14px;
    selection-background-color: {t['bg_selection']};
}}

QLineEdit:focus {{
    border-color: {t['blue']};
}}

QTableWidget::item {{
    border: none;
    padding: 4px 10px;
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
    padding: 10px 10px;
    border: none;
    border-right: 1px solid {t['border_light']};
    border-bottom: 2px solid {t['border_light']};
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 1px;
}}

QHeaderView::section:last {{
    border-right: none;
}}

/* ── Scrollbars ──────────────────────────────────────────────────── */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0;
}}

QScrollBar::handle:vertical {{
    background: {t['scrollbar']};
    border-radius: 5px;
    min-height: 40px;
}}

QScrollBar::handle:vertical:hover {{
    background: {t['accent']};
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
    margin: 0;
}}

QScrollBar::handle:horizontal {{
    background: {t['scrollbar']};
    border-radius: 5px;
    min-width: 40px;
}}

QScrollBar::handle:horizontal:hover {{
    background: {t['accent']};
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
    background-color: {t['bg_alt']};
    border-top: 1px solid {t['border_light']};
    color: {t['text_dim']};
    font-size: 12px;
    padding: 0 10px;
}}

QStatusBar QLabel {{
    color: {t['text_muted']};
    font-size: 12px;
}}

/* ── Progress Bar ────────────────────────────────────────────────── */
QProgressBar {{
    background-color: {t['bg_card']};
    border-radius: 5px;
    border: none;
    text-align: center;
    color: transparent;
}}

QProgressBar::chunk {{
    background-color: {t['accent']};
    border-radius: 5px;
}}

/* ── Image Picker Dialog ─────────────────────────────────────────── */
QDialog#imagePickerDialog {{
    background-color: {t['bg']};
}}

QLabel#dialogTitle {{
    font-size: 20px;
    font-weight: 700;
    color: {t['text']};
}}

QLabel#dialogSubtitle {{
    font-size: 13px;
    color: {t['text_muted']};
}}

QFrame#imageCard {{
    background-color: {t['bg_card']};
    border-radius: 12px;
    border: 2px solid transparent;
}}

QFrame#imageCard:hover {{
    border-color: {t['accent_pressed']};
    background-color: {t['bg_hover']};
}}

QFrame#imageCardSelected {{
    background-color: {t['bg_hover']};
    border-radius: 12px;
    border: 2px solid {t['accent']};
}}

/* ── Progress Dialog ─────────────────────────────────────────────── */
QDialog#progressDialog {{
    background-color: {t['bg']};
}}

QLabel#progressTitle {{
    font-size: 18px;
    font-weight: 700;
    color: {t['text']};
}}

QLabel#progressSong {{
    font-size: 13px;
    color: {t['text_muted']};
}}

/* ── Table / View toolbar ─────────────────────────────────────────── */
QWidget#tableBar {{
    background-color: {t['bg_alt']};
    border-bottom: 1px solid {t['border_light']};
}}

QLabel#filterTitle {{
    color: {t['accent']};
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 0.5px;
}}

QPushButton#btnViewActive {{
    background-color: {t['accent']};
    color: white;
    border: none;
    border-radius: 6px;
    font-size: 12px;
    font-weight: 600;
    padding: 6px 14px;
}}

QPushButton#btnViewInactive {{
    background-color: transparent;
    color: {t['text_dim']};
    border: 1px solid {t['border']};
    border-radius: 6px;
    font-size: 12px;
    font-weight: 600;
    padding: 6px 14px;
}}

QPushButton#btnViewInactive:hover {{
    border-color: {t['accent']};
    color: {t['text_secondary']};
}}

/* ── Grid view ───────────────────────────────────────────────────── */
QListWidget#songGrid {{
    background-color: {t['bg']};
    border: none;
    outline: none;
    padding: 12px;
}}

QListWidget#songGrid::item {{
    background-color: {t['bg_card']};
    border-radius: 10px;
    padding: 8px;
    color: {t['text']};
    font-size: 14px;
}}

QListWidget#songGrid::item:selected {{
    background-color: {t['bg_selection']};
    color: {t['text']};
}}

QListWidget#songGrid::item:hover:!selected {{
    background-color: {t['bg_hover']};
}}

QFrame#songGridCard {{
    background-color: {t['bg_card']};
    border: 1px solid {t['border']};
    border-radius: 12px;
}}

QListWidget#tagGrid {{
    background-color: {t['bg']};
    border: 1px solid {t['border_light']};
    border-radius: 12px;
    outline: none;
    padding: 14px;
}}

QListWidget#tagGrid::item {{
    background-color: {t['bg_card']};
    border: 1px solid {t['border']};
    border-radius: 12px;
    padding: 16px 12px;
    margin: 3px;
    color: {t['text']};
    font-size: 16px;
}}

QListWidget#tagGrid::item:hover {{
    border-color: {t['blue']};
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
    font-size: 14px;
}}

QMessageBox QPushButton {{
    background-color: {t['accent']};
    color: white;
    border: none;
    border-radius: 6px;
    padding: 10px 24px;
    min-width: 100px;
    font-weight: 600;
    font-size: 14px;
}}

QMessageBox QPushButton:hover {{
    background-color: {t['accent_hover']};
}}

/* ── Player Bar ──────────────────────────────────────────────────── */
QFrame#playerBar {{
    background-color: {t['player_bg']};
    border-top: 1px solid {t['player_border']};
}}

QLabel#playerTitle {{
    color: {t['text']};
    font-size: 22px;
    font-weight: 600;
}}

QLabel#playerArtist {{
    color: {t['text_secondary']};
    font-size: 18px;
}}

QLabel#playerAlbum {{
    color: {t['text_muted']};
    font-size: 18px;
}}

QLabel#playerTime {{
    color: {t['text_muted']};
    font-size: 18px;
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

/* ── Sliders (player progress / volume) ──────────────────────────── */
QSlider#playerProgress::groove:horizontal,
QSlider#playerVolume::groove:horizontal {{
    background: {t['border']};
    height: 6px;
    border-radius: 3px;
}}

QSlider#playerProgress::handle:horizontal,
QSlider#playerVolume::handle:horizontal {{
    background: {t['accent']};
    width: 18px;
    height: 18px;
    border-radius: 9px;
    margin: -6px 0;
}}

QSlider#playerProgress::sub-page:horizontal,
QSlider#playerVolume::sub-page:horizontal {{
    background: {t['accent']};
    border-radius: 3px;
}}
"""


# Backward-compatible default (dark theme)
STYLESHEET = build_stylesheet(DARK)
