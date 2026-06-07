"""
themes.py – Three color palettes for SongSyncer: Light, Purple, Orange.
All UI colors are defined here so the app can switch themes at runtime.
"""

LIGHT = {
    'name': 'Light',
    # backgrounds
    'bg': '#f8fafc',
    'bg_alt': '#ffffff',
    'bg_card': '#ffffff',
    'bg_hover': '#f1f5f9',
    'bg_input': '#ffffff',
    'bg_selection': '#ede9fe',
    # borders
    'border': '#e2e8f0',
    'border_light': '#f1f5f9',
    # text
    'text': '#1e293b',
    'text_secondary': '#475569',
    'text_muted': '#64748b',
    'text_dim': '#94a3b8',
    # accent
    'accent': '#7c3aed',
    'accent_hover': '#6d28d9',
    'accent_pressed': '#5b21b6',
    # semantic buttons
    'green': '#059669',
    'green_hover': '#047857',
    'green_pressed': '#065f46',
    'red': '#dc2626',
    'red_hover': '#b91c1c',
    'red_pressed': '#991b1b',
    'orange': '#b45309',
    'orange_hover': '#92400e',
    'orange_pressed': '#78350f',
    'blue': '#2563eb',
    'blue_hover': '#1d4ed8',
    'blue_pressed': '#1e40af',
    'cyan': '#0891b2',
    'cyan_hover': '#0e7490',
    'cyan_pressed': '#155e75',
    'gray_btn': '#e2e8f0',
    'gray_btn_border': '#cbd5e1',
    # disabled
    'disabled_bg': '#f1f5f9',
    'disabled_text': '#cbd5e1',
    'disabled_border': '#e2e8f0',
    # misc
    'scrollbar': '#cbd5e1',
    'placeholder_bg': '#e2e8f0',
    'tooltip_bg': '#ffffff',
    'tooltip_border': '#e2e8f0',
    'msgbox_bg': '#ffffff',
    # player
    'player_bg': '#ffffff',
    'player_border': '#e2e8f0',
    # cover placeholders
    'cover_has': '#059669',
    'cover_missing': '#e2e8f0',
    # tag dialog helpers
    'partial_bg': '#fef3c7',
    'partial_fg': '#b45309',
    'checked_bg': '#dbeafe',
    'error_fg': '#dc2626',
    'warning_fg': '#b45309',
    'success_fg': '#059669',
}

PURPLE = {
    'name': 'Purple',
    # backgrounds
    'bg': '#0f0f1a',
    'bg_alt': '#0a0a14',
    'bg_card': '#1a1a2e',
    'bg_hover': '#1e1e35',
    'bg_input': '#13131f',
    'bg_selection': '#2e1065',
    # borders
    'border': '#2d2d4e',
    'border_light': '#1e1e35',
    # text
    'text': '#e2e8f0',
    'text_secondary': '#94a3b8',
    'text_muted': '#64748b',
    'text_dim': '#4a5568',
    # accent (purple)
    'accent': '#7c3aed',
    'accent_hover': '#6d28d9',
    'accent_pressed': '#5b21b6',
    # semantic buttons
    'green': '#059669',
    'green_hover': '#047857',
    'green_pressed': '#065f46',
    'red': '#dc2626',
    'red_hover': '#b91c1c',
    'red_pressed': '#991b1b',
    'orange': '#b45309',
    'orange_hover': '#92400e',
    'orange_pressed': '#78350f',
    'blue': '#2563eb',
    'blue_hover': '#1d4ed8',
    'blue_pressed': '#1e40af',
    'cyan': '#0891b2',
    'cyan_hover': '#0e7490',
    'cyan_pressed': '#155e75',
    'gray_btn': '#374151',
    'gray_btn_border': '#4b5563',
    # disabled
    'disabled_bg': '#1a1a2e',
    'disabled_text': '#2d3748',
    'disabled_border': '#1e1e35',
    # misc
    'scrollbar': '#2d2d4e',
    'placeholder_bg': '#0a0a14',
    'tooltip_bg': '#1e1e35',
    'tooltip_border': '#3a3a5e',
    'msgbox_bg': '#1a1a2e',
    # player
    'player_bg': '#0a0a14',
    'player_border': '#1e1e35',
    # cover placeholders
    'cover_has': '#059669',
    'cover_missing': '#2d2d4e',
    # tag dialog helpers
    'partial_bg': '#2b2112',
    'partial_fg': '#f59e0b',
    'checked_bg': '#1e2a4a',
    'error_fg': '#f87171',
    'warning_fg': '#f59e0b',
    'success_fg': '#34d399',
}

PINK = {
    'name': 'Pink',
    # backgrounds – dark with rosy undertones
    'bg': '#1a0f14',
    'bg_alt': '#140c10',
    'bg_card': '#2a1822',
    'bg_hover': '#351e2c',
    'bg_input': '#1f1218',
    'bg_selection': '#4a1038',
    # borders
    'border': '#4e2840',
    'border_light': '#351e2c',
    # text
    'text': '#f0e0ea',
    'text_secondary': '#b898a8',
    'text_muted': '#8b6a7b',
    'text_dim': '#684858',
    # accent (pink)
    'accent': '#e8448a',
    'accent_hover': '#d03878',
    'accent_pressed': '#b82c66',
    # semantic buttons
    'green': '#059669',
    'green_hover': '#047857',
    'green_pressed': '#065f46',
    'red': '#dc2626',
    'red_hover': '#b91c1c',
    'red_pressed': '#991b1b',
    'orange': '#b45309',
    'orange_hover': '#92400e',
    'orange_pressed': '#78350f',
    'blue': '#2563eb',
    'blue_hover': '#1d4ed8',
    'blue_pressed': '#1e40af',
    'cyan': '#0891b2',
    'cyan_hover': '#0e7490',
    'cyan_pressed': '#155e75',
    'gray_btn': '#3d2830',
    'gray_btn_border': '#5a3848',
    # disabled
    'disabled_bg': '#2a1822',
    'disabled_text': '#3d2830',
    'disabled_border': '#351e2c',
    # misc
    'scrollbar': '#4e2840',
    'placeholder_bg': '#140c10',
    'tooltip_bg': '#351e2c',
    'tooltip_border': '#5a3848',
    'msgbox_bg': '#2a1822',
    # player
    'player_bg': '#140c10',
    'player_border': '#351e2c',
    # cover placeholders
    'cover_has': '#059669',
    'cover_missing': '#4e2840',
    # tag dialog helpers
    'partial_bg': '#2b1222',
    'partial_fg': '#f59eb0',
    'checked_bg': '#2a1830',
    'error_fg': '#f87171',
    'warning_fg': '#f59e0b',
    'success_fg': '#34d399',
}

GREEN = {
    'name': 'Green',
    # backgrounds – dark with forest undertones
    'bg': '#0f1a12',
    'bg_alt': '#0c140e',
    'bg_card': '#182a1c',
    'bg_hover': '#1e3524',
    'bg_input': '#121f15',
    'bg_selection': '#104a1a',
    # borders
    'border': '#284e30',
    'border_light': '#1e3524',
    # text
    'text': '#e0f0e4',
    'text_secondary': '#98b8a0',
    'text_muted': '#6a8b72',
    'text_dim': '#486850',
    # accent (green)
    'accent': '#22c55e',
    'accent_hover': '#16a34a',
    'accent_pressed': '#15803d',
    # semantic buttons
    'green': '#22c55e',
    'green_hover': '#16a34a',
    'green_pressed': '#15803d',
    'red': '#dc2626',
    'red_hover': '#b91c1c',
    'red_pressed': '#991b1b',
    'orange': '#b45309',
    'orange_hover': '#92400e',
    'orange_pressed': '#78350f',
    'blue': '#2563eb',
    'blue_hover': '#1d4ed8',
    'blue_pressed': '#1e40af',
    'cyan': '#0891b2',
    'cyan_hover': '#0e7490',
    'cyan_pressed': '#155e75',
    'gray_btn': '#283d2e',
    'gray_btn_border': '#385a40',
    # disabled
    'disabled_bg': '#182a1c',
    'disabled_text': '#283d2e',
    'disabled_border': '#1e3524',
    # misc
    'scrollbar': '#284e30',
    'placeholder_bg': '#0c140e',
    'tooltip_bg': '#1e3524',
    'tooltip_border': '#385a40',
    'msgbox_bg': '#182a1c',
    # player
    'player_bg': '#0c140e',
    'player_border': '#1e3524',
    # cover placeholders
    'cover_has': '#22c55e',
    'cover_missing': '#284e30',
    # tag dialog helpers
    'partial_bg': '#122b18',
    'partial_fg': '#86efac',
    'checked_bg': '#182a22',
    'error_fg': '#f87171',
    'warning_fg': '#f59e0b',
    'success_fg': '#34d399',
}

# Ordered cycle: Light → Purple → Pink → Green → Light …
THEME_ORDER = ['light', 'purple', 'pink', 'green']
THEMES = {'light': LIGHT, 'purple': PURPLE, 'pink': PINK, 'green': GREEN}

# Backward compat – code that imported DARK gets the Purple theme
DARK = PURPLE
