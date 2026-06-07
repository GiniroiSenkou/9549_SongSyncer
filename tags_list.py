# =====================
# tags_list.py
# =====================
"""Tag identifier registry.

The icon filename stem in ``Data/Tags/`` is the **only** canonical tag
identifier. Examples:

    Data/Tags/fairy_tail.png            -> identifier: 'fairy_tail'
    Data/Tags/neon_genesis_evangelion.png -> identifier: 'neon_genesis_evangelion'

The JSON store keeps the identifier verbatim. The GUI shows a title-cased
display name produced by :func:`display_for` (``fairy_tail`` → ``Fairy Tail``)
with overrides in ``_DISPLAY_OVERRIDES`` for acronyms (``ita`` → ``ITA``).
"""

import re
import unicodedata
from pathlib import Path

TAGS_DIR = str(Path(__file__).resolve().parent / 'Data' / 'Tags')
_TAG_EXTENSIONS = {'.png', '.svg', '.ico'}
_DISPLAY_OVERRIDES = {
    'ita': 'ITA',
    'ost': 'OST',
    'bms': 'BMS',
    'cod': 'COD',
    'dc': 'DC',
    'kpop': 'KPOP',
}


def normalize_tag_name(value: str) -> str:
    value = unicodedata.normalize('NFC', str(value or '')).casefold()
    return re.sub(r'[^0-9a-z]+', '', value)


def tag_lookup_variants(value: str) -> set[str]:
    """Fuzzy variants of a string — used ONLY by ``suggest_identifier`` for
    the best-guess pre-fill in the unsupported-tag converter. Not part of
    the validity path anymore."""
    raw = unicodedata.normalize('NFC', str(value or '')).casefold()
    tokens = re.findall(r'[0-9a-z]+', raw)
    variants: set[str] = set()
    joined = ''.join(tokens)
    if joined:
        variants.add(joined)
    if len(tokens) > 1:
        initials = ''.join(token[0] for token in tokens if token)
        if initials:
            variants.add(initials)
        if tokens[0]:
            variants.add(tokens[0])
    return variants


def _display_name_from_stem(stem: str) -> str:
    stem = str(stem or '').strip()
    if not stem:
        return ''
    key = normalize_tag_name(stem)
    if key in _DISPLAY_OVERRIDES:
        return _DISPLAY_OVERRIDES[key]
    parts = re.split(r'[_\-\s]+', stem)
    parts = [part for part in parts if part]
    if not parts:
        return stem
    return ' '.join(part.capitalize() for part in parts)


def _load_tags():
    """Scan ``Data/Tags/`` and return (icons, display_names, fuzzy_lookup).

    - ``icons``: ``{identifier: icon_path}`` — keys are the PNG stems.
    - ``display_names``: ``{identifier: 'Fairy Tail'}`` — for the GUI.
    - ``fuzzy_lookup``: ``{normalized_variant: identifier}`` — internal,
      consumed by :func:`suggest_identifier` only.
    """
    icons: dict[str, str] = {}
    display_names: dict[str, str] = {}
    fuzzy: dict[str, str] = {}

    tags_dir = Path(TAGS_DIR)
    if tags_dir.exists():
        for entry in sorted(tags_dir.iterdir()):
            if not entry.is_file() or entry.suffix.lower() not in _TAG_EXTENSIONS:
                continue
            identifier = entry.stem
            icons[identifier] = str(entry)
            display_names[identifier] = _display_name_from_stem(identifier)
            for variant in tag_lookup_variants(identifier):
                fuzzy.setdefault(variant, identifier)
            for variant in tag_lookup_variants(display_names[identifier]):
                fuzzy.setdefault(variant, identifier)

    return icons, display_names, fuzzy


TAG_ICONS, TAG_DISPLAY_NAMES, _TAG_FUZZY_LOOKUP = _load_tags()


def display_for(identifier: str) -> str:
    """Return the GUI label for a tag identifier. Falls back to the
    identifier itself when it isn't registered, so invalid tags still render
    legibly."""
    if not identifier:
        return ''
    return TAG_DISPLAY_NAMES.get(identifier, identifier)


def suggest_identifier(raw_tag: str) -> str | None:
    """Best-guess canonical identifier for an unsupported tag string. Used
    only to pre-select an option in the unsupported-tag converter dialog —
    never as a validity check."""
    for variant in tag_lookup_variants(raw_tag):
        hit = _TAG_FUZZY_LOOKUP.get(variant)
        if hit:
            return hit
    return None


def resolve_tag_icon(identifier: str) -> str:
    """Exact lookup. Returns '' if the identifier isn't a known tag."""
    return TAG_ICONS.get(identifier, '')
