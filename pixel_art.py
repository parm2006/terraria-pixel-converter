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


def detect_background_color(img: Image.Image, tolerance=15) -> tuple[int, int, int] | None:
    """
    Detect the background color of the image by analyzing the four corners.
    If all corners are similar within the tolerance, returns their average RGB color.
    Otherwise, returns None (no background color detected).
    """
    import math
    rgba = img.convert("RGBA")
    width, height = rgba.size
    
    corners = [
        rgba.getpixel((0, 0)),
        rgba.getpixel((width - 1, 0)),
        rgba.getpixel((0, height - 1)),
        rgba.getpixel((width - 1, height - 1))
    ]
    
    # If all corners are transparent (alpha < 30), the background is already transparent
    if all(c[3] < 30 for c in corners):
        return None
        
    # Check if all corner RGB colors are similar
    first_rgb = corners[0][:3]
    for c in corners[1:]:
        rgb = c[:3]
        dist = math.sqrt(sum((a - b)**2 for a, b in zip(first_rgb, rgb)))
        if dist > tolerance:
            return None  # Corners don't match, don't auto-detect a background color
            
    # Return the average color of the corners
    avg_r = sum(c[0] for c in corners) // 4
    avg_g = sum(c[1] for c in corners) // 4
    avg_b = sum(c[2] for c in corners) // 4
    return (avg_r, avg_g, avg_b)


# ---------------------------------------------------------------------------
# Pixel size detection
# ---------------------------------------------------------------------------

def detect_pixel_size(img: Image.Image) -> int:
    """
    Detect logical pixel size using chunked color transition analysis.
    Splits the image into 5 horizontal bands, finds the local transition mode
    in each, and aggregates them (taking the median) to avoid text/caption pollution.
    """
    import math
    from collections import Counter
    rgb = img.convert("RGB")
    width, height = rgb.size

    num_chunks = 5
    chunk_height = height // num_chunks
    chunk_modes = []

    for i in range(num_chunks):
        y_start = i * chunk_height
        y_end = y_start + chunk_height
        
        distances = []
        # Sample rows in this chunk
        for y in range(y_start + 5, min(height, y_end - 5), 10):
            row_colors = [rgb.getpixel((x, y)) for x in range(width)]
            transitions = []
            for x in range(1, width):
                c1 = row_colors[x-1]
                c2 = row_colors[x]
                dist = math.sqrt(sum((a - b)**2 for a, b in zip(c1, c2)))
                if dist > 20:  # Color change threshold
                    transitions.append(x)
            for j in range(1, len(transitions)):
                diff = transitions[j] - transitions[j-1]
                if diff > 5:  # Filter out small noise
                    distances.append(diff)
                    
        if distances:
            counter = Counter(distances)
            local_mode = counter.most_common(1)[0][0]
            chunk_modes.append(local_mode)
            
    if not chunk_modes:
        # Fallback to the old logic if no transitions detected
        return max(1, math.ceil(max(width, height) / MAX_LOGICAL_DIM))
        
    # Aggregate: return the median of the chunk modes
    chunk_modes.sort()
    median_mode = chunk_modes[len(chunk_modes) // 2]
    return median_mode


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
        "--bg-threshold", type=int, default=15,
        help="Tolerance for matching the background color (default 15)."
    )
    parser.add_argument(
        "--bg-color", type=str, default=None,
        help="Manually specify background color in Hex (e.g. #F8F8F8) or RGB (e.g. 248,248,248) to bypass corner auto-detection."
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

    # --- Auto-detect and crop caption text (logical chunk analysis) ---
    if not args.no_auto_crop:
        pixel_size = detect_pixel_size(img)
        w, h = img.size
        chunk_height = pixel_size
        num_chunks = h // chunk_height
        
        chunk_valid_mins = []
        for i in range(num_chunks):
            y_start = i * chunk_height
            y_end = y_start + chunk_height
            
            distances = []
            # Sample a few rows in this chunk
            for y in range(y_start + 2, y_end - 2, max(1, chunk_height // 5)):
                if y >= h: break
                row_colors = [img.getpixel((x, y)) for x in range(w)]
                transitions = []
                for x in range(1, w):
                    c1 = row_colors[x-1]
                    c2 = row_colors[x]
                    dist = math.sqrt(sum((a - b)**2 for a, b in zip(c1, c2)))
                    if dist > 20:
                        transitions.append(x)
                for j in range(1, len(transitions)):
                    diff = transitions[j] - transitions[j-1]
                    if diff > 2:  # ignore tiny noise
                        distances.append(diff)
                        
            if distances:
                chunk_valid_mins.append((i, min(distances)))
            else:
                chunk_valid_mins.append((i, None))
                
        # Scan from bottom to top for caption crop
        crop_bottom_y = h
        for idx in range(num_chunks - 1, -1, -1):
            m = chunk_valid_mins[idx][1]
            if m is not None and m < pixel_size * 0.7:
                crop_bottom_y = idx * chunk_height
            else:
                break
                
        # Scan from top to bottom for header/title crop
        crop_top_y = 0
        for idx in range(num_chunks):
            m = chunk_valid_mins[idx][1]
            if m is not None and m < pixel_size * 0.7:
                crop_top_y = (idx + 1) * chunk_height
            else:
                break
                
        if crop_top_y > 0 or crop_bottom_y < h:
            img = img.crop((0, crop_top_y, w, crop_bottom_y))
            print(f"Auto-cropped borders via sequential chunk analysis: cropped Y range to [{crop_top_y}, {crop_bottom_y}] px")

    # --- Strip white/transparent background ---
    # Convert to RGBA so we can check alpha or near-white pixels
    rgba = img.convert("RGBA")
    w, h = rgba.size
    
    # Resolve background color (manual or auto-detected)
    bg_color = None
    if args.bg_color:
        try:
            if args.bg_color.startswith("#"):
                h_str = args.bg_color.lstrip("#")
                bg_color = tuple(int(h_str[i:i+2], 16) for i in (0, 2, 4))
            else:
                bg_color = tuple(map(int, args.bg_color.split(",")))
            print(f"Using manually specified background color: RGB{bg_color}")
        except Exception as e:
            print(f"[WARN] Failed to parse manual bg-color '{args.bg_color}': {e}. Falling back to auto-detection.")
            
    if bg_color is None:
        bg_color = detect_background_color(img, tolerance=15)
        if bg_color is not None:
            print(f"Auto-detected background color from corners: RGB{bg_color}")
            
    if bg_color is not None:
        pixels = list(rgba.getdata())
        new_pixels = []
        thresh = args.bg_threshold
        for r, g, b, a in pixels:
            dist = math.sqrt((r - bg_color[0])**2 + (g - bg_color[1])**2 + (b - bg_color[2])**2)
            if a < 30 or dist <= thresh:
                new_pixels.append((255, 255, 255, 0))  # transparent
            else:
                new_pixels.append((r, g, b, a))
        rgba.putdata(new_pixels)
    else:
        print("No uniform background color detected. Stripping transparent pixels only.")
        pixels = list(rgba.getdata())
        new_pixels = []
        for r, g, b, a in pixels:
            if a < 30:
                new_pixels.append((255, 255, 255, 0))
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
