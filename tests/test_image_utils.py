"""
Tests for the icon/cover sanitization fast path in image_utils.py.

The slow path (full PIL decode -> rebuild -> re-encode) used to run
unconditionally for every image, which froze the UI thread for many
seconds the first time a dialog loaded a whole tag-icon set (Edit Song,
Assign Tags: ~50 icons in Data/Tags/). These tests pin down the fast-path
behaviour (no ICC profile -> bytes returned unchanged, still validated)
and confirm the slow path is still used — correctly, and without the
expensive `optimize=True` PNG search — when a profile actually needs
stripping.
"""
import time
import unittest
from io import BytesIO

from PIL import Image

from image_utils import sanitize_image_bytes


def _png_bytes(mode='RGBA', size=(32, 32), icc_profile=None, color=(200, 60, 60, 255)):
    img = Image.new(mode, size, color if mode == 'RGBA' else color[:3])
    out = BytesIO()
    kwargs = {'format': 'PNG'}
    if icc_profile is not None:
        kwargs['icc_profile'] = icc_profile
    img.save(out, **kwargs)
    return out.getvalue()


class SanitizeImageBytesTests(unittest.TestCase):
    def test_no_icc_profile_returns_original_bytes_unchanged(self):
        data = _png_bytes()
        result = sanitize_image_bytes(data)
        self.assertEqual(result, data)  # fast path: no rebuild, no re-encode

    def test_with_icc_profile_still_strips_it(self):
        # A syntactically-present (if fake) ICC profile chunk is enough to
        # take the slow path; the output must decode cleanly and carry no
        # profile of its own.
        data = _png_bytes(icc_profile=b'\x00' * 128)
        with Image.open(BytesIO(data)) as src:
            self.assertTrue(src.info.get('icc_profile'))

        result = sanitize_image_bytes(data)
        self.assertIsNotNone(result)
        with Image.open(BytesIO(result)) as cleaned:
            cleaned.load()
            self.assertIsNone(cleaned.info.get('icc_profile'))
            self.assertEqual(cleaned.size, (32, 32))

    def test_palette_mode_without_profile_is_passed_through(self):
        # Palette (P) images used to always be converted to RGBA even with
        # nothing to sanitize; the fast path must still validate them (not
        # just assume they're fine) and hand back working bytes.
        img = Image.new('P', (16, 16))
        img.putpalette([c for n in range(256) for c in (n, 0, 0)])
        out = BytesIO()
        img.save(out, format='PNG')
        data = out.getvalue()

        result = sanitize_image_bytes(data)
        self.assertEqual(result, data)
        with Image.open(BytesIO(result)) as check:
            check.load()

    def test_corrupt_data_returns_none(self):
        self.assertIsNone(sanitize_image_bytes(b'not an image'))
        self.assertIsNone(sanitize_image_bytes(b''))

    def test_fast_path_is_fast_even_for_a_large_image(self):
        # Regression guard for the freeze: a reasonably large icon with no
        # ICC profile must sanitize in well under the ~150ms/icon the old
        # optimize=True rebuild cost (which made ~50 icons take 8-18s).
        data = _png_bytes(size=(512, 512))
        t0 = time.perf_counter()
        for _ in range(20):
            sanitize_image_bytes(data)
        elapsed = time.perf_counter() - t0
        self.assertLess(elapsed, 1.0, f'20 calls took {elapsed:.2f}s — fast path regressed')


if __name__ == '__main__':
    unittest.main()
