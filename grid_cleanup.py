"""Noise-tolerant grid recovery with explicit uncertainty and cell reconstruction.

Use independent periodicity detectors for Auto, Proper Pixel Art for ambiguous
cases, and representative cell colors instead of an error-minimizing divisor
search. No filenames or sample-specific grid sizes participate in selection.
"""
from __future__ import annotations

import time
import cv2
import numpy as np
from PIL import Image

from pixel_art import clean_image as proper_pixel_art
from vendor.pixelfixer import detect
from vendor.pixelfixer.reconstruct import two_stage_pack


def integer_grid(rgba):
    """Find a shared integer lattice only when edges strongly support it.

    Weighted boundary votes tolerate small interior color noise. Requiring
    concentrated edges on BOTH axes prevents a single stripe or outline from
    providing sufficient evidence for a grid. Fractional grids fall through.
    """
    rgb = rgba[:, :, :3].astype(np.float32) * (rgba[:, :, 3:4] / 255.0)
    candidates = []
    for axis in (1, 0):
        differences = np.max(np.abs(np.diff(rgb, axis=axis)), axis=2)
        # Estimate low-level variation from the lower half of the gradients.
        noise = min(24.0, float(np.median(differences)) * 3)
        strength = np.maximum(differences - max(6.0, noise), 0)
        profile = strength.sum(axis=0 if axis == 1 else 1)
        total = profile.sum()
        accepted = {}
        if total > 0:
            coordinates = np.arange(1, len(profile) + 1)
            for step in range(2, min(64, len(profile) // 4) + 1):
                votes = np.bincount(coordinates % step, weights=profile, minlength=step)
                # Only phase-zero evidence here; cropped/offset images use
                # the general detector rather than silently shifting cells.
                fraction = float(votes[0] / total)
                if fraction >= .80:
                    accepted[step] = fraction
        candidates.append(accepted)
    shared = candidates[0].keys() & candidates[1].keys()
    return max(shared) if shared else None


def recover_grid(image: Image.Image, *, pixel_width: int = 0, bin_size: int = 52):
    started = time.perf_counter()
    source = image.convert('RGBA')
    width, height = source.size
    if pixel_width < 0 or pixel_width > 256:
        raise ValueError('pixel width must be between 0 and 256')
    if not 1 <= bin_size <= 255:
        raise ValueError('noise grouping bin size must be between 1 and 255')

    # At thumbnail/native size there may be only a handful of edges per axis.
    # There is insufficient evidence to shrink these automatically. Preserve
    # the original pixels and let a deliberate manual choice override this.
    rgba = np.array(source)
    exact_step = integer_grid(rgba) if pixel_width == 0 else None
    if pixel_width == 1 or (pixel_width == 0 and exact_step is None and min(source.size) <= 32):
        return source.copy(), dict(
            method='native_preserved', confidence='low' if pixel_width == 0 else 'manual',
            step_x=1.0, step_y=1.0, seconds=round(time.perf_counter() - started, 3),
        )

    # Hidden RGB must not create false boundaries in transparent backgrounds.
    detection = rgba.copy()
    detection[detection[:, :, 3] == 0, :3] = 0
    # Limit detector working memory; translate its cell counts back to the
    # source resolution. Reconstruction always samples the original image.
    scale = min(1.0, (2_600_000 / (width * height)) ** 0.5)
    if scale < 1:
        detection = cv2.resize(detection, (round(width * scale), round(height * scale)),
                               interpolation=cv2.INTER_AREA)
    detection[:, :, :3] = cv2.bilateralFilter(detection[:, :, :3], 5, bin_size / 3.25, 2)
    if pixel_width or exact_step:
        step = pixel_width or exact_step
        cols, rows = max(1, round(width / step)), max(1, round(height / step))
        method, confidence = ('manual', 'manual') if pixel_width else ('integer_boundary_consensus', 'high')
    else:
        result = detect(detection, mode='fast', low_memory=True)
        method = result['consensus']
        cols, rows = int(result['cols']), int(result['rows'])
        confidence = 'high' if method.startswith('fast:') else 'medium'
        if method == 'fastmode:lowconf':
            # Disagreement is evidence of ambiguity, not permission to select
            # whichever smaller grid happens to reproduce source noise best.
            fallback = proper_pixel_art(Image.fromarray(detection), num_colors=0, bin_size=bin_size)
            cols, rows = fallback.size
            method, confidence = 'proper_pixel_art_fallback', 'low'
    cols, rows = min(width, max(1, cols)), min(height, max(1, rows))
    logical = Image.fromarray(two_stage_pack(rgba, cols, rows))
    return logical, dict(method=method, confidence=confidence,
                         step_x=round(width / cols, 4), step_y=round(height / rows, 4),
                         seconds=round(time.perf_counter() - started, 3))
