#!/usr/bin/env python3
"""
pixel_art.py
Convert a pixel-art image into a Terraria block/wall material list.

Usage:
    python pixel_art.py <image> --mode blocks
    python pixel_art.py <image> --mode walls
    python pixel_art.py <image> --mode blocks --db-dir ./data

Requires blocks.json / walls.json produced by scrape_terraria.py.
"""

import argparse
import ctypes
import json
import math
import os
import sys
from collections import Counter
from pathlib import Path

from PIL import Image

# ---------------------------------------------------------------------------
# Enable ANSI escape codes on Windows
# ---------------------------------------------------------------------------
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')
    _kernel32 = ctypes.windll.kernel32
    _handle = _kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
    _mode = ctypes.c_ulong()
    if _kernel32.GetConsoleMode(_handle, ctypes.byref(_mode)):
        _kernel32.SetConsoleMode(_handle, _mode.value | 0x0004)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_LOGICAL_DIM = 150  # max logical pixels per dimension
DB_FILES = {
    "blocks": "cleaned_blocks.json",
    "walls":  "cleaned_walls.json",
}

# Windows: ANSI just enabled above so always use true color.
# Other platforms: check env var.
SUPPORTS_TRUE_COLOR = (
    sys.platform == "win32"
    or os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit")
    or os.environ.get("WT_SESSION") is not None
)


# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------

def rgb_distance(a: tuple, b: tuple) -> float:
    """Euclidean distance in RGB space."""
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def most_common_color(img: Image.Image) -> tuple[int, int, int]:
    """
    Return the most frequent RGB value in the image region.
    Ignores fully-transparent pixels (alpha <= 10).
    Falls back to (0,0,0) if all pixels are transparent.
    """
    rgba = img.convert("RGBA")
    pixels = [(r, g, b) for r, g, b, a in list(rgba.getdata()) if a > 10]
    if not pixels:
        return (0, 0, 0)
    return Counter(pixels).most_common(1)[0][0]


def color_to_ansi(r: int, g: int, b: int, text: str) -> str:
    """
    Wrap text in ANSI 24-bit background + contrasting foreground.
    Falls back to a hex string if the terminal doesn't support true color.
    """
    if SUPPORTS_TRUE_COLOR:
        # Pick black or white foreground based on perceived luminance
        lum = 0.299 * r + 0.587 * g + 0.114 * b
        fg = "0;0;0" if lum > 128 else "255;255;255"
        return f"\033[48;2;{r};{g};{b}m\033[38;2;{fg}m{text}\033[0m"
    else:
        return f"#{r:02X}{g:02X}{b:02X}  {text}"


def hue_of(rgb: tuple) -> float:
    """Return hue (0–360) for rainbow sorting. Grays sort to end."""
    r, g, b = (x / 255.0 for x in rgb)
    mx = max(r, g, b)
    mn = min(r, g, b)
    delta = mx - mn
    if delta < 0.01:            # achromatic — sort grays after colors
        return 360 + (r + g + b) / 3   # secondary sort by brightness
    if mx == r:
        h = (g - b) / delta % 6
    elif mx == g:
        h = (b - r) / delta + 2
    else:
        h = (r - g) / delta + 4
    return h * 60


# ---------------------------------------------------------------------------
# Pixel size detection
# ---------------------------------------------------------------------------

def detect_pixel_size(img: Image.Image) -> int:
    """
    Detect logical pixel size using color transition analysis.
    Samples rows and columns, calculates distances between consecutive color shifts,
    and returns the most common distance (mode).
    """
    import math
    from collections import Counter
    rgb = img.convert("RGB")
    width, height = rgb.size

    distances = []
    
    # Sample rows (every 10th row)
    for y in range(10, height - 10, 10):
        row_colors = [rgb.getpixel((x, y)) for x in range(width)]
        transitions = []
        for x in range(1, width):
            c1 = row_colors[x-1]
            c2 = row_colors[x]
            dist = math.sqrt(sum((a - b)**2 for a, b in zip(c1, c2)))
            if dist > 20:  # Color change threshold
                transitions.append(x)
        for i in range(1, len(transitions)):
            diff = transitions[i] - transitions[i-1]
            if 3 <= diff <= 100:  # filter noise and overly large transitions
                distances.append(diff)
                
    # Sample columns (every 10th column)
    for x in range(10, width - 10, 10):
        col_colors = [rgb.getpixel((x, y)) for y in range(height)]
        transitions = []
        for y in range(1, height):
            c1 = col_colors[y-1]
            c2 = col_colors[y]
            dist = math.sqrt(sum((a - b)**2 for a, b in zip(c1, c2)))
            if dist > 20:
                transitions.append(y)
        for i in range(1, len(transitions)):
            diff = transitions[i] - transitions[i-1]
            if 3 <= diff <= 100:
                distances.append(diff)
                
    if not distances:
        # Fallback to the old logic if no transitions detected
        return max(1, math.ceil(max(width, height) / MAX_LOGICAL_DIM))
        
    # Pick the most common transition distance
    counter = Counter(distances)
    mode, freq = counter.most_common(1)[0]
    return mode


# ---------------------------------------------------------------------------
# Color matching
# ---------------------------------------------------------------------------

def build_matcher(db: list[dict]):
    """
    Pre-process the DB into a list of (avg_color_tuple, name) pairs.
    Returns a function: rgb_tuple → best matching entry dict.
    """
    palette = [(tuple(e["avg_color"]), e["name"]) for e in db]

    def match(rgb: tuple) -> str:
        best_name = None
        best_dist = float("inf")
        for color, name in palette:
            d = rgb_distance(rgb, color)
            if d < best_dist:
                best_dist = d
                best_name = name
        return best_name

    return match


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------

def process_image(img: Image.Image, pixel_size: int, match_fn) -> list[dict]:
    """
    Divide image into pixel_size x pixel_size cells.
    For each cell: extract most-common color, find closest block/wall.
    Returns list of {source_color, block_name} for every unique color found.
    """
    rgb = img.convert("RGB")
    width, height = rgb.size

    color_to_block: dict[tuple, str] = {}

    cols = width  // pixel_size
    rows = height // pixel_size

    for row in range(rows):
        for col in range(cols):
            x0 = col * pixel_size
            y0 = row * pixel_size
            x1 = x0 + pixel_size
            y1 = y0 + pixel_size
            cell = rgb.crop((x0, y0, x1, y1))
            color = most_common_color(cell)
            if color not in color_to_block:
                color_to_block[color] = match_fn(color)

    return color_to_block


def count_blocks(img: Image.Image, pixel_size: int, color_to_block: dict) -> Counter:
    """Count how many of each block name appears across the whole image."""
    rgb = img.convert("RGB")
    width, height = rgb.size
    counts: Counter = Counter()
    cols = width  // pixel_size
    rows = height // pixel_size
    for row in range(rows):
        for col in range(cols):
            x0 = col * pixel_size
            y0 = row * pixel_size
            cell = rgb.crop((x0, y0, x0 + pixel_size, y0 + pixel_size))
            color = most_common_color(cell)
            block = color_to_block[color]
            counts[block] += 1
    return counts


# ---------------------------------------------------------------------------
# Output rendering
# ---------------------------------------------------------------------------

def render_color_map(color_to_block: dict, counts: Counter):
    """
    Merge all source colors that map to the same block into one row.
    Swatch color = the most-used source color for that block.
    Count = total across all source colors for that block.
    Sorted by hue of the representative color.
    """
    total = sum(counts.values())
    BAR_WIDTH = 30

    # Merge: block_name -> {total_count, representative_color}
    block_counts: dict = {}
    block_color: dict = {}
    color_count_for_block: dict = {}  # block -> {color: count}

    for color, name in color_to_block.items():
        c = counts.get(name, 0)
        if name not in block_counts:
            block_counts[name] = 0
            color_count_for_block[name] = {}
        block_counts[name] = counts.get(name, 0)
        color_count_for_block[name][color] = color_count_for_block[name].get(color, 0) + 1

    # Pick representative color = the source color closest to the block name's avg
    # Simple: just pick the first one encountered (already deduplicated by most_common)
    for color, name in color_to_block.items():
        if name not in block_color:
            block_color[name] = color

    # Sort by hue of representative color
    sorted_blocks = sorted(block_counts.keys(), key=lambda n: hue_of(block_color[n]))

    print("\n── Pixel art material list (rainbow order) ─────────────────────────")
    for name in sorted_blocks:
        count = block_counts[name]
        r, g, b = block_color[name]
        swatch = color_to_ansi(r, g, b, f"  #{r:02X}{g:02X}{b:02X}  ")
        pct = count / total * 100
        bar_len = max(1, round(pct / 100 * BAR_WIDTH))
        if SUPPORTS_TRUE_COLOR:
            bar = f"\033[38;2;{r};{g};{b}m" + "█" * bar_len + "\033[0m"
        else:
            bar = "█" * bar_len
        print(f"  {swatch}  {name:<40s}  x {count:<6}  {bar}")
    print(f"\n  Total pixels: {total}")





# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Convert pixel art into a Terraria block/wall material list."
    )
    parser.add_argument("image", help="Path to the pixel art image (PNG, JPG, etc.)")
    parser.add_argument(
        "--mode", choices=["blocks", "walls"], required=True,
        help="Use blocks or walls for the palette."
    )
    parser.add_argument(
        "--db-dir", default=".",
        help="Directory containing blocks.json / walls.json (default: current dir)"
    )
    parser.add_argument(
        "--pixel-size", type=int, default=0,
        help="Override automatic pixel size detection (size of 1 block in pixels)."
    )
    parser.add_argument(
        "--bg-threshold", type=int, default=230,
        help="Color threshold for background stripping (0-255, default 230)."
    )
    parser.add_argument(
        "--crop-bottom", type=int, default=0,
        help="Manually crop off this many pixels from the bottom of the image before processing."
    )
    parser.add_argument(
        "--no-auto-crop", action="store_true",
        help="Disable automatic detection and cropping of bottom caption text."
    )
    args = parser.parse_args()

    # --- Load database ---
    db_path = Path(args.db_dir) / DB_FILES[args.mode]
    if not db_path.exists():
        print(f"[ERROR] Database file not found: {db_path}")
        print("        Run scrape_terraria.py first.")
        sys.exit(1)

    with open(db_path) as f:
        db = json.load(f)
    print(f"Loaded {len(db)} {args.mode} from {db_path}")

    # --- Load image ---
    img_path = Path(args.image)
    if not img_path.exists():
        print(f"[ERROR] Image not found: {img_path}")
        sys.exit(1)

    try:
        img = Image.open(img_path)
    except Exception as e:
        print(f"[ERROR] Could not open image: {e}")
        sys.exit(1)

    print(f"Image: {img_path.name}  ({img.width} x {img.height} px)")

    # --- Manual bottom crop ---
    if args.crop_bottom > 0:
        img = img.crop((0, 0, img.width, max(1, img.height - args.crop_bottom)))
        print(f"Manually cropped bottom: height reduced to {img.height} px")

    # --- Auto-detect and crop bottom caption text (e.g. dark text at the bottom) ---
    if not args.no_auto_crop:
        img_rgb = img.convert("RGB")
        w, h = img_rgb.size
        caption_start_y = None
        for y in range(h - 1, max(0, h - 200), -1):
            dark_pixels = sum(1 for x in range(w) if all(c < 100 for c in img_rgb.getpixel((x, y))))
            # If we find a row with a significant amount of dark text-like pixels
            if 5 <= dark_pixels <= w * 0.7:
                caption_start_y = y
        if caption_start_y is not None:
            crop_height = max(1, caption_start_y - 15)
            img = img.crop((0, 0, w, crop_height))
            print(f"Auto-cropped bottom caption text (height reduced to {img.height} px)")

    # --- Strip white/transparent background ---
    # Convert to RGBA so we can check alpha or near-white pixels
    rgba = img.convert("RGBA")
    r_data, g_data, b_data, a_data = rgba.split()
    # Make near-white pixels (all channels >= bg-threshold) transparent
    pixels = list(rgba.getdata())
    new_pixels = []
    thresh = args.bg_threshold
    for r, g, b, a in pixels:
        if a < 30 or (r >= thresh and g >= thresh and b >= thresh):
            new_pixels.append((255, 255, 255, 0))  # transparent
        else:
            new_pixels.append((r, g, b, a))
    rgba.putdata(new_pixels)
    # Crop to non-transparent bounding box
    bbox = rgba.getbbox()
    if bbox:
        rgba = rgba.crop(bbox)
        print(f"Cropped to content: {rgba.width} x {rgba.height} px")
    img = rgba

    # --- Quantize to reduce compression noise ---
    # Convert to RGB palette with N colors, then back - snaps near-identical colors
    NUM_COLORS = 128
    rgb_img = img.convert("RGB")
    quantized = rgb_img.quantize(colors=NUM_COLORS, method=Image.Quantize.MEDIANCUT).convert("RGB")
    # Re-apply transparency mask from before quantization
    quantized_rgba = quantized.convert("RGBA")
    alpha_mask = img.split()[3]  # original alpha channel after bg strip
    quantized_rgba.putalpha(alpha_mask)
    img = quantized_rgba
    print(f"Quantized to {NUM_COLORS} colors")

    # --- Detect pixel size ---
    if args.pixel_size > 0:
        pixel_size = args.pixel_size
        print(f"Using manually specified pixel size: {pixel_size} px")
    else:
        pixel_size = detect_pixel_size(img)
        print(f"Auto-detected pixel size: {pixel_size} px")
        
    logical_w = img.width  // pixel_size
    logical_h = img.height // pixel_size
    print(f"logical grid: {logical_w} x {logical_h} blocks")

    # --- Build matcher & process ---
    match_fn = build_matcher(db)
    color_to_block = process_image(img, pixel_size, match_fn)
    counts = count_blocks(img, pixel_size, color_to_block)

    # --- Output ---
    print(f"\nUnique colors in image:  {len(color_to_block)}")
    print(f"Unique blocks/walls used: {len(counts)}")
    render_color_map(color_to_block, counts)


if __name__ == "__main__":
    main()
