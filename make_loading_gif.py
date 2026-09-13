"""Build the 8x8 loading animation from the supplied 5x5 sprite sheet."""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image


BACKGROUND_COLORS = {
    (255, 255, 255),
    (217, 217, 217),
}


def build_animation(source_path: Path, output_path: Path) -> None:
    sheet = Image.open(source_path).convert("RGBA")
    width, height = sheet.size
    frames: list[Image.Image] = []

    for row in range(5):
        for column in range(5):
            left = round(column * width / 5)
            right = round((column + 1) * width / 5)
            top = round(row * height / 5)
            bottom = round((row + 1) * height / 5)
            cell = sheet.crop((left, top, right, bottom))

            cleaned = Image.new("RGBA", cell.size, (0, 0, 0, 0))
            cleaned_pixels = cleaned.load()
            cell_pixels = cell.load()
            for y in range(cell.height):
                for x in range(cell.width):
                    pixel = cell_pixels[x, y]
                    if pixel[:3] not in BACKGROUND_COLORS:
                        cleaned_pixels[x, y] = pixel

            bounds = cleaned.getbbox()
            if bounds is None:
                raise ValueError(f"Frame {row * 5 + column + 1} is empty")
            frame = cleaned.crop(bounds).resize((8, 8), Image.Resampling.NEAREST)
            frames.append(frame)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        output_path,
        save_all=True,
        append_images=frames[1:],
        duration=50,
        loop=0,
        disposal=2,
        optimize=False,
    )


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("Usage: python make_loading_gif.py SOURCE.png OUTPUT.gif")
    build_animation(Path(sys.argv[1]), Path(sys.argv[2]))
