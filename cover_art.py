"""
cover_art.py – Read, write, and delete embedded cover art from audio files.
Supports: MP3, FLAC, M4A/AAC, OGG Vorbis, OPUS, WAV.
"""

import base64
from pathlib import Path
from typing import Optional, Tuple

from mutagen import File as MutagenFile
from mutagen.id3 import ID3, APIC, ID3NoHeaderError
from mutagen.mp4 import MP4, MP4Cover
from mutagen.flac import FLAC, Picture
from mutagen.oggvorbis import OggVorbis
from mutagen.oggopus import OggOpus

SUPPORTED_EXTENSIONS = {'.mp3', '.flac', '.m4a', '.aac', '.ogg', '.opus', '.wav'}

# ── Public helpers ────────────────────────────────────────────────────────────

def get_metadata(path: str) -> dict:
    """Return {'title', 'artist', 'album'} from audio file tags."""
    result = {'title': '', 'artist': '', 'album': ''}
    try:
        audio = MutagenFile(path, easy=True)
        if audio is None:
            return result
        result['title']  = _first(audio.get('title'))
        result['artist'] = _first(audio.get('artist'))
        result['album']  = _first(audio.get('album'))
    except Exception:
        pass
    return result


def clear_metadata(path: str) -> bool:
    """Delete all metadata tags from the audio file."""
    try:
        audio = MutagenFile(path, easy=True)
        if audio is None:
            return False
        if audio.tags:
            audio.delete()
            audio.save()
        return True
    except Exception:
        return False


def write_metadata(path: str, title: str = None, artist: str = None,
                   album: str = None) -> bool:
    """Write title, artist and/or album tags via mutagen's easy interface."""
    try:
        audio = MutagenFile(path, easy=True)
        if audio is None:
            return False
        if title is not None:
            audio['title'] = [title]
        if artist is not None:
            audio['artist'] = [artist]
        if album is not None:
            audio['album'] = [album]
        audio.save()
        return True
    except Exception:
        return False


def has_cover(path: str) -> bool:
    """Fast check – returns True if the file has embedded cover art."""
    ext = Path(path).suffix.lower()
    try:
        if ext == '.mp3':
            return _mp3_has_cover(path)
        elif ext == '.flac':
            return _flac_has_cover(path)
        elif ext in ('.m4a', '.aac', '.mp4'):
            return _mp4_has_cover(path)
        elif ext in ('.ogg',):
            return _ogg_has_cover(path, OggVorbis)
        elif ext == '.opus':
            return _ogg_has_cover(path, OggOpus)
        elif ext == '.wav':
            return _wav_has_cover(path)
    except Exception:
        pass
    return False


def read_cover(path: str) -> Tuple[Optional[bytes], Optional[str]]:
    """Return (image_bytes, mime_type) or (None, None)."""
    ext = Path(path).suffix.lower()
    try:
        if ext == '.mp3':
            return _read_mp3(path)
        elif ext == '.flac':
            return _read_flac(path)
        elif ext in ('.m4a', '.aac', '.mp4'):
            return _read_mp4(path)
        elif ext == '.ogg':
            return _read_ogg(path, OggVorbis)
        elif ext == '.opus':
            return _read_ogg(path, OggOpus)
        elif ext == '.wav':
            return _read_wav(path)
    except Exception:
        pass
    return None, None


def write_cover(path: str, img_data: bytes, mime: str = 'image/jpeg') -> bool:
    """Embed cover art into the audio file. Returns True on success."""
    ext = Path(path).suffix.lower()
    try:
        if ext == '.mp3':
            return _write_mp3(path, img_data, mime)
        elif ext == '.flac':
            return _write_flac(path, img_data, mime)
        elif ext in ('.m4a', '.aac', '.mp4'):
            return _write_mp4(path, img_data, mime)
        elif ext == '.ogg':
            return _write_ogg(path, img_data, mime, OggVorbis)
        elif ext == '.opus':
            return _write_ogg(path, img_data, mime, OggOpus)
        elif ext == '.wav':
            return _write_wav(path, img_data, mime)
    except Exception:
        pass
    return False


def delete_cover(path: str) -> bool:
    """Remove embedded cover art. Returns True on success."""
    ext = Path(path).suffix.lower()
    try:
        if ext == '.mp3':
            return _delete_mp3(path)
        elif ext == '.flac':
            return _delete_flac(path)
        elif ext in ('.m4a', '.aac', '.mp4'):
            return _delete_mp4(path)
        elif ext in ('.ogg',):
            return _delete_ogg(path, OggVorbis)
        elif ext == '.opus':
            return _delete_ogg(path, OggOpus)
        elif ext == '.wav':
            return _delete_wav(path)
    except Exception:
        pass
    return False


# ── MP3 ──────────────────────────────────────────────────────────────────────

def _mp3_has_cover(path):
    try:
        tag = ID3(path)
        return any(k.startswith('APIC') for k in tag.keys())
    except ID3NoHeaderError:
        return False


def _read_mp3(path):
    try:
        tag = ID3(path)
        for key in tag.keys():
            if key.startswith('APIC'):
                frame = tag[key]
                return frame.data, frame.mime
    except ID3NoHeaderError:
        pass
    return None, None


def _write_mp3(path, data, mime):
    try:
        try:
            tag = ID3(path)
        except ID3NoHeaderError:
            tag = ID3()
        tag.delall('APIC')
        tag.add(APIC(encoding=3, mime=mime, type=3, desc='Cover', data=data))
        tag.save(path)
        return True
    except Exception:
        return False


def _delete_mp3(path):
    try:
        tag = ID3(path)
        if any(k.startswith('APIC') for k in tag.keys()):
            tag.delall('APIC')
            tag.save(path)
        return True
    except ID3NoHeaderError:
        return True
    except Exception:
        return False


# ── FLAC ─────────────────────────────────────────────────────────────────────

def _flac_has_cover(path):
    audio = FLAC(path)
    return len(audio.pictures) > 0


def _read_flac(path):
    audio = FLAC(path)
    for pic in audio.pictures:
        if pic.type == 3 or len(audio.pictures) == 1:
            return pic.data, pic.mime
    return None, None


def _write_flac(path, data, mime):
    audio = FLAC(path)
    audio.clear_pictures()
    pic = Picture()
    pic.type = 3
    pic.mime = mime
    pic.data = data
    audio.add_picture(pic)
    audio.save()
    return True


def _delete_flac(path):
    audio = FLAC(path)
    if audio.pictures:
        audio.clear_pictures()
        audio.save()
    return True


# ── MP4 / M4A ────────────────────────────────────────────────────────────────

def _mp4_has_cover(path):
    audio = MP4(path)
    return bool(audio.get('covr'))


def _read_mp4(path):
    audio = MP4(path)
    covr = audio.get('covr', [])
    if covr:
        img = covr[0]
        mime = 'image/png' if img.imageformat == MP4Cover.FORMAT_PNG else 'image/jpeg'
        return bytes(img), mime
    return None, None


def _write_mp4(path, data, mime):
    audio = MP4(path)
    fmt = MP4Cover.FORMAT_PNG if 'png' in mime else MP4Cover.FORMAT_JPEG
    audio['covr'] = [MP4Cover(data, imageformat=fmt)]
    audio.save()
    return True


def _delete_mp4(path):
    audio = MP4(path)
    if 'covr' in audio:
        del audio['covr']
        audio.save()
    return True


# ── OGG Vorbis / OPUS ────────────────────────────────────────────────────────

def _ogg_has_cover(path, cls):
    audio = cls(path)
    return bool(audio.get('metadata_block_picture'))


def _read_ogg(path, cls):
    audio = cls(path)
    pics = audio.get('metadata_block_picture', [])
    if pics:
        try:
            pic = Picture(base64.b64decode(pics[0]))
            return pic.data, pic.mime
        except Exception:
            pass
    return None, None


def _write_ogg(path, data, mime, cls):
    audio = cls(path)
    pic = Picture()
    pic.type = 3
    pic.mime = mime
    pic.data = data
    encoded = base64.b64encode(pic.write()).decode('ascii')
    audio['metadata_block_picture'] = [encoded]
    audio.save()
    return True


def _delete_ogg(path, cls):
    audio = cls(path)
    if 'metadata_block_picture' in audio:
        del audio['metadata_block_picture']
        audio.save()
    return True


# ── WAV ──────────────────────────────────────────────────────────────────────

def _wav_has_cover(path):
    try:
        audio = MutagenFile(path)
        if audio and audio.tags:
            return any(k.startswith('APIC') for k in audio.tags.keys())
    except Exception:
        pass
    return False


def _read_wav(path):
    try:
        audio = MutagenFile(path)
        if audio and audio.tags:
            for key in audio.tags.keys():
                if key.startswith('APIC'):
                    frame = audio.tags[key]
                    return frame.data, frame.mime
    except Exception:
        pass
    return None, None


def _write_wav(path, data, mime):
    try:
        audio = MutagenFile(path)
        if audio is None:
            return False
        if audio.tags is None:
            audio.add_tags()
        audio.tags.delall('APIC')
        audio.tags.add(APIC(encoding=3, mime=mime, type=3, desc='Cover', data=data))
        audio.save()
        return True
    except Exception:
        return False


def _delete_wav(path):
    try:
        audio = MutagenFile(path)
        if audio and audio.tags and any(k.startswith('APIC') for k in audio.tags.keys()):
            audio.tags.delall('APIC')
            audio.save()
        return True
    except Exception:
        return False


# ── Utility ──────────────────────────────────────────────────────────────────

def _first(value) -> str:
    """Return first element of a list/tuple, or the value itself, as str."""
    if value is None:
        return ''
    if isinstance(value, (list, tuple)):
        return str(value[0]) if value else ''
    return str(value)
