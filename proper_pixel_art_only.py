"""Run only Proper Pixel Art and save its cleaned reconstruction."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageOps
from proper_pixel_art import pixelate
from proper_pixel_art.config import ColorConfig, PixelateConfig


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Clean one image with Proper Pixel Art and save a viewable PNG."
    )
    parser.add_argument("image", type=Path, help="Image to process")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output PNG (default: proper_pixel_art_outputs/<name>_proper.png)",
    )
    parser.add_argument(
        "--pixel-width",
        type=int,
        default=0,
        help="Source pixel width; 0 lets Proper Pixel Art detect it (default: 0)",
    )
    parser.add_argument(
        "--bin-size",
        type=int,
        default=52,
        help="Noise/color grouping size from 1 to 255 (default: 52)",
    )
    parser.add_argument(
        "--colors",
        type=int,
        default=0,
        help="Maximum palette size; 0 preserves all detected colors (default: 0)",
    )
    args = parser.parse_args()

    if not args.image.is_file():
        parser.error(f"image not found: {args.image}")
    if args.pixel_width < 0:
        parser.error("--pixel-width must be zero or greater")
    if not 1 <= args.bin_size <= 255:
        parser.error("--bin-size must be between 1 and 255")
    if args.colors < 0:
        parser.error("--colors must be zero or greater")

    output = args.output or (
        Path("proper_pixel_art_outputs") / f"{args.image.stem}_proper.png"
    )

    with Image.open(args.image) as opened:
        source = ImageOps.exif_transpose(opened).convert("RGBA")
        source.load()

    logical = pixelate(
        source,
        config=PixelateConfig(
            num_colors=args.colors,
            pixel_width=args.pixel_width,
            scale_result=1,
            transparent_background=False,
            colors=ColorConfig(bin_size=args.bin_size),
        ),
    ).convert("RGBA")

    reconstruction = logical.resize(source.size, Image.Resampling.NEAREST)
    output.parent.mkdir(parents=True, exist_ok=True)
    reconstruction.save(output, format="PNG")

    print(f"Proper Pixel Art grid: {logical.width} x {logical.height}")
    print(
        "Saved reconstruction: "
        f"{output.resolve()} ({reconstruction.width} x {reconstruction.height})"
    )


if __name__ == "__main__":
    main()
