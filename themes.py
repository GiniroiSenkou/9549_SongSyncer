"""
themes.py – Four macOS-style palettes for SongSyncer.

``light`` is macOS Light with the system blue accent; ``purple``, ``pink`` and
``green`` share one macOS Dark base and differ only in accent colour (the way
the macOS "Accent colour" setting works). Every palette carries the same keys
so ``style.build_stylesheet`` and the widgets can swap at runtime. Values are
plain hex — several widgets feed them straight into ``QColor(str)``.
"""


def _dark(name: str, accent: str, accent_hover: str, accent_pressed: str,
          selection: str, sidebar_active: str) -> dict:
    return {
        'name': name,
        # backgrounds (macOS dark: window #1e1e1e, sidebar slightly lighter)
        'bg': '#1e1e1e',
        'bg_alt': '#252526',
        'bg_card': '#2c2c2e',
        'bg_hover': '#3a3a3c',
        'bg_input': '#1c1c1e',
        'bg_selection': selection,
        'sidebar_bg': '#232325',
        'sidebar_active_bg': sidebar_active,
        'sidebar_active_fg': '#ffffff',
        'toolbar_bg': '#2a2a2c',
        'badge_bg': '#3a3a3c',
        'badge_fg': '#d1d1d6',
        'separator': '#38383a',
        # borders
        'border': '#3a3a3c',
        'border_light': '#2f2f31',
        # text
        'text': '#f5f5f7',
        'text_secondary': '#d1d1d6',
        'text_muted': '#98989d',
        'text_dim': '#636366',
        # accent
        'accent': accent,
        'accent_hover': accent_hover,
        'accent_pressed': accent_pressed,
        # semantic (macOS dark system colours)
        'green': '#30d158',
        'green_hover': '#28b64c',
        'green_pressed': '#219a40',
        'red': '#ff453a',
        'red_hover': '#e03d33',
        'red_pressed': '#c2352c',
        'orange': '#ff9f0a',
        'orange_hover': '#e08c09',
        'orange_pressed': '#c27a08',
        'blue': '#0a84ff',
        'blue_hover': '#0974e0',
        'blue_pressed': '#0864c2',
        'cyan': '#64d2ff',
        'cyan_hover': '#58b9e0',
        'cyan_pressed': '#4ca1c2',
        'gray_btn': '#3a3a3c',
        'gray_btn_border': '#48484a',
        # disabled
        'disabled_bg': '#2c2c2e',
        'disabled_text': '#5a5a5e',
        'disabled_border': '#3a3a3c',
        # misc
        'scrollbar': '#5a5a5e',
        'placeholder_bg': '#2c2c2e',
        'tooltip_bg': '#2c2c2e',
        'tooltip_border': '#48484a',
        'msgbox_bg': '#2c2c2e',
        # player
        'player_bg': '#252526',
        'player_border': '#38383a',
        # cover placeholders
        'cover_has': '#30d158',
        'cover_missing': '#3a3a3c',
        # tag dialog helpers
        'partial_bg': '#3b2f12',
        'partial_fg': '#ffd60a',
        'checked_bg': sidebar_active,
        'error_fg': '#ff6961',
        'warning_fg': '#ffb340',
        'success_fg': '#30d158',
    }


LIGHT = {
    'name': 'Light',
    # backgrounds (macOS light: window #f5f5f7, content white)
    'bg': '#f5f5f7',
    'bg_alt': '#ffffff',
    'bg_card': '#ffffff',
    'bg_hover': '#ececef',
    'bg_input': '#ffffff',
    'bg_selection': '#d9e8ff',
    'sidebar_bg': '#ededf0',
    'sidebar_active_bg': '#007aff',
    'sidebar_active_fg': '#ffffff',
    'toolbar_bg': '#f9f9fb',
    'badge_bg': '#dcdce0',
    'badge_fg': '#3a3a3c',
    'separator': '#e1e1e5',
    # borders
    'border': '#d2d2d7',
    'border_light': '#e5e5ea',
    # text
    'text': '#1d1d1f',
    'text_secondary': '#3a3a3c',
    'text_muted': '#6e6e73',
    'text_dim': '#8e8e93',
    # accent (system blue)
    'accent': '#007aff',
    'accent_hover': '#0a6fe0',
    'accent_pressed': '#0862c2',
    # semantic (macOS light system colours)
    'green': '#34c759',
    'green_hover': '#2db24f',
    'green_pressed': '#279a45',
    'red': '#ff3b30',
    'red_hover': '#e0342a',
    'red_pressed': '#c22d24',
    'orange': '#ff9500',
    'orange_hover': '#e08400',
    'orange_pressed': '#c27200',
    'blue': '#007aff',
    'blue_hover': '#0a6fe0',
    'blue_pressed': '#0862c2',
    'cyan': '#32ade6',
    'cyan_hover': '#2c98ca',
    'cyan_pressed': '#2683ae',
    'gray_btn': '#e9e9ed',
    'gray_btn_border': '#d2d2d7',
    # disabled
    'disabled_bg': '#f0f0f3',
    'disabled_text': '#b8b8bd',
    'disabled_border': '#e5e5ea',
    # misc
    'scrollbar': '#c7c7cc',
    'placeholder_bg': '#e5e5ea',
    'tooltip_bg': '#ffffff',
    'tooltip_border': '#d2d2d7',
    'msgbox_bg': '#ffffff',
    # player
    'player_bg': '#f9f9fb',
    'player_border': '#e1e1e5',
    # cover placeholders
    'cover_has': '#34c759',
    'cover_missing': '#e5e5ea',
    # tag dialog helpers
    'partial_bg': '#fff4d6',
    'partial_fg': '#b25e00',
    'checked_bg': '#d9e8ff',
    'error_fg': '#ff3b30',
    'warning_fg': '#c77700',
    'success_fg': '#248a3d',
}

PURPLE = _dark('Purple', '#bf5af2', '#ab4fd9', '#9645bf', '#3d2a52', '#7d3fb0')
PINK   = _dark('Pink',   '#ff375f', '#e03154', '#c22b49', '#4a2431', '#b8294a')
GREEN  = _dark('Green',  '#30d158', '#28b64c', '#219a40', '#1f3d29', '#22843c')

# Ordered cycle: Light → Purple → Pink → Green → Light …
THEME_ORDER = ['light', 'purple', 'pink', 'green']
THEMES = {'light': LIGHT, 'purple': PURPLE, 'pink': PINK, 'green': GREEN}

# Backward compat – code that imported DARK gets the Purple theme
DARK = PURPLE
