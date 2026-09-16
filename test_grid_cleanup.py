"""Regression checks with known grids; no filename-specific detector expectations."""
import unittest
from io import BytesIO

import numpy as np
from PIL import Image, ImageFilter

from grid_cleanup import recover_grid


class GridCleanupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rng = np.random.default_rng(120)
        palette = np.array([[20, 30, 45], [180, 70, 35], [55, 130, 90],
                            [210, 200, 140], [95, 60, 145]], dtype=np.uint8)
        cls.native = Image.fromarray(palette[rng.integers(0, len(palette), (18, 20))]).convert('RGBA')

    def test_native_and_manual_one_preserve_pixels(self):
        for width in (0, 1):
            result, info = recover_grid(self.native, pixel_width=width)
            self.assertEqual(result.tobytes(), self.native.tobytes())

    def test_scaled_noise_blur_and_jpeg(self):
        rng = np.random.default_rng(456)
        for scale in (2, 3, 5, 7, 8.5):
            size = (round(20 * scale), round(18 * scale))
            clean = self.native.resize(size, Image.Resampling.NEAREST)
            noisy = np.array(clean).astype(np.int16)
            noisy[:, :, :3] += rng.normal(0, 4, noisy[:, :, :3].shape).astype(np.int16)
            variants = {'clean': clean,
                        'noise': Image.fromarray(np.clip(noisy, 0, 255).astype(np.uint8)),
                        'blur': clean.filter(ImageFilter.GaussianBlur(.45))}
            buf = BytesIO()
            clean.convert('RGB').save(buf, format='JPEG', quality=85)
            variants['jpeg'] = Image.open(BytesIO(buf.getvalue())).convert('RGBA')
            for damage, source in variants.items():
                with self.subTest(scale=scale, damage=damage):
                    result, info = recover_grid(source)
                    print(f'{scale}x {damage}: {result.size} {info["method"]}', flush=True)
                    self.assertLessEqual(abs(result.width - 20), 1)
                    self.assertLessEqual(abs(result.height - 18), 1)
                    if result.size == self.native.size:
                        error = np.abs(np.array(result).astype(float) - np.array(self.native)).mean()
                        self.assertLess(error, 18)

    def test_manual_and_transparency(self):
        source = self.native.resize((100, 90), Image.Resampling.NEAREST)
        result, info = recover_grid(source, pixel_width=5)
        self.assertEqual(result.size, (20, 18))
        self.assertEqual(info['confidence'], 'manual')
        transparent = Image.new('RGBA', (80, 80), (190, 1, 240, 0))
        result, _ = recover_grid(transparent, pixel_width=4)
        self.assertEqual(result.getchannel('A').getextrema(), (0, 0))

    def test_api_response_and_background(self):
        from api.convert import convert_request
        source = self.native.resize((100, 90), Image.Resampling.NEAREST)
        buffer = BytesIO()
        source.save(buffer, format='PNG')
        result = convert_request(buffer.getvalue(), 'pixel_width=5&remove_background=0')
        self.assertEqual(result['grid'], {'width': 20, 'height': 18})
        self.assertEqual(result['settings']['pixel_width'], 5)
        self.assertEqual(sum(item['pixel_count'] for item in result['materials']), 360)
        self.assertEqual(result['detection']['confidence'], 'manual')
        self.assertEqual(result['grid_refinement'], [])
        transparent = Image.new('RGBA', (80, 80), (0, 0, 0, 0))
        buffer = BytesIO()
        transparent.save(buffer, format='PNG')
        result = convert_request(buffer.getvalue(), '')
        self.assertEqual(result['visible_pixels'], 0)
        self.assertEqual(result['materials'], [])

    def test_upload_validation_uses_actual_file_content(self):
        from api.convert import convert_request

        # A valid image in an unapproved format is rejected even though Pillow
        # can decode it; the server does not trust a client filename or MIME.
        gif = BytesIO()
        Image.new('RGB', (2, 2), 'red').save(gif, format='GIF')
        with self.assertRaisesRegex(ValueError, 'Only PNG, JPG, and WebP'):
            convert_request(gif.getvalue(), 'pixel_width=1')

        with self.assertRaisesRegex(ValueError, 'not a valid PNG, JPG, or WebP'):
            convert_request(b'<svg xmlns="http://www.w3.org/2000/svg"></svg>', '')


if __name__ == '__main__':
    unittest.main()
