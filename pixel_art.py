#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "Pillow==12.3.0",
#   "proper-pixel-art==1.7.2",
# ]
# ///
"""Clean source artwork into a one-pixel-per-cell logical image.

Proper Pixel Art discovers the source grid and returns the logical image at
scale 1. Optional flood-fill background removal runs on that cleaned grid.
Terraria material matching will be added after this cleaning stage is stable.
"""

import argparse
import json
import math
from collections import Counter, deque
from pathlib import Path

from PIL import Image
from proper_pixel_art import pixelate
from proper_pixel_art.config import ColorConfig, PixelateConfig


VALID_MODES = ("blocks", "walls")


def load_database(db_dir: str | Path, mode: str) -> list[dict]:
    """Load a cleaned Terraria material database and validate its basic shape."""
    if mode not in VALID_MODES:
        raise ValueError(f"mode must be one of: {', '.join(VALID_MODES)}")

    db_path = Path(db_dir) / f"cleaned_{mode}.json"
    if not db_path.is_file():
        raise FileNotFoundError(f"Database not found: {db_path}")

    with db_path.open(encoding="utf-8") as file:
        materials = json.load(file)

    if not isinstance(materials, list):
        raise ValueError(f"Database must contain a JSON list: {db_path}")

    for index, material in enumerate(materials):
        if not isinstance(material, dict):
            raise ValueError(f"Material {index} must be a JSON object")
        if "name" not in material or "avg_color" not in material:
            raise ValueError(
                f"Material {index} must contain 'name' and 'avg_color'"
            )
        color = material["avg_color"]
        if (
            not isinstance(color, list)
            or len(color) != 3
            or any(
                not isinstance(channel, int) or not 0 <= channel <= 255
                for channel in color
            )
        ):
            raise ValueError(
                f"Material {index} has an invalid avg_color: {color!r}"
            )

    return materials


def clean_image(
    image: Image.Image,
    *,
    num_colors: int | None = None,
    pixel_width: int = 0,
    bin_size: int = 52,
    intermediate_dir: Path | None = None,
) -> Image.Image:
    """Return Proper Pixel Art's detected logical grid at scale 1."""
    if not 1 <= bin_size <= 255:
        raise ValueError("noise grouping bin size must be between 1 and 255")

    # Proper Pixel Art detects its mesh on a 2x working image. Its manual
    # pixel_width is interpreted in that enlarged coordinate space, while our
    # CLI and website expose the more intuitive width in source-image pixels.
    working_pixel_width = pixel_width * 2 if pixel_width > 0 else 0
    config = PixelateConfig(
        num_colors=0 if num_colors is None else num_colors,
        pixel_width=working_pixel_width,
        scale_result=1,
        transparent_background=False,
        colors=ColorConfig(bin_size=bin_size),
    )
    return pixelate(
        image,
        intermediate_dir=intermediate_dir,
        config=config,
    ).convert("RGBA")


def remove_edge_background(
    image: Image.Image,
    *,
    tolerance: int = 0,
) -> tuple[Image.Image, tuple[int, int, int], int]:
    """Flood-fill the dominant edge color and make that region transparent.

    Only pixels connected to the canvas edge are removed, so an enclosed area
    with the same color remains intact. ``tolerance`` is Euclidean RGB distance.
    """
    if tolerance < 0:
        raise ValueError("background tolerance cannot be negative")

    result = image.convert("RGBA")
    width, height = result.size
    if width == 0 or height == 0:
        raise ValueError("cannot remove the background from an empty image")

    pixels = result.load()
    boundary_coordinates = {
        *((x, 0) for x in range(width)),
        *((x, height - 1) for x in range(width)),
        *((0, y) for y in range(height)),
        *((width - 1, y) for y in range(height)),
    }
    boundary_colors = [
        pixels[x, y][:3]
        for x, y in boundary_coordinates
        if pixels[x, y][3] > 0
    ]
    if not boundary_colors:
        return result, (0, 0, 0), 0

    background_color = Counter(boundary_colors).most_common(1)[0][0]
    tolerance_squared = tolerance * tolerance

    def matches_background(x: int, y: int) -> bool:
        red, green, blue, alpha = pixels[x, y]
        if alpha == 0:
            return False
        return (
            (red - background_color[0]) ** 2
            + (green - background_color[1]) ** 2
            + (blue - background_color[2]) ** 2
            <= tolerance_squared
        )

    queue = deque(
        coordinate
        for coordinate in boundary_coordinates
        if matches_background(*coordinate)
    )
    visited = set(queue)

    while queue:
        x, y = queue.popleft()
        for neighbor_x, neighbor_y in (
            (x - 1, y),
            (x + 1, y),
            (x, y - 1),
            (x, y + 1),
        ):
            neighbor = (neighbor_x, neighbor_y)
            if (
                0 <= neighbor_x < width
                and 0 <= neighbor_y < height
                and neighbor not in visited
                and matches_background(neighbor_x, neighbor_y)
            ):
                visited.add(neighbor)
                queue.append(neighbor)

    for x, y in visited:
        red, green, blue, _ = pixels[x, y]
        pixels[x, y] = (red, green, blue, 0)

    return result, background_color, len(visited)


def count_visible_colors(image: Image.Image) -> Counter[tuple[int, int, int]]:
    """Build an RGB -> pixel count hash table, ignoring transparent pixels."""
    return Counter(
        (red, green, blue)
        for red, green, blue, alpha in image.convert("RGBA").get_flattened_data()
        if alpha > 0
    )


def _rgb_to_lab(color: tuple[int, int, int]) -> tuple[float, float, float]:
    """Convert sRGB to CIE Lab so nearest colors follow human perception."""
    linear = []
    for channel in color:
        value = channel / 255
        linear.append(
            value / 12.92
            if value <= 0.04045
            else ((value + 0.055) / 1.055) ** 2.4
        )
    red, green, blue = linear
    x = (red * 0.4124564 + green * 0.3575761 + blue * 0.1804375) / 0.95047
    y = red * 0.2126729 + green * 0.7151522 + blue * 0.0721750
    z = (red * 0.0193339 + green * 0.1191920 + blue * 0.9503041) / 1.08883

    def pivot(value: float) -> float:
        return value ** (1 / 3) if value > 0.008856 else 7.787 * value + 16 / 116

    x, y, z = pivot(x), pivot(y), pivot(z)
    return 116 * y - 16, 500 * (x - y), 200 * (y - z)


def _build_kd_tree(
    points: list[tuple[tuple[float, float, float], int]],
    depth: int = 0,
):
    """Build a balanced, dependency-free 3D search tree."""
    if not points:
        return None
    axis = depth % 3
    points.sort(key=lambda item: item[0][axis])
    middle = len(points) // 2
    return (
        points[middle],
        axis,
        _build_kd_tree(points[:middle], depth + 1),
        _build_kd_tree(points[middle + 1 :], depth + 1),
    )


def _query_kd_tree(node, target, best=(math.inf, -1)):
    """Return (squared distance, item index) for an exact nearest neighbor."""
    if node is None:
        return best
    (point, index), axis, left, right = node
    distance = sum((point[i] - target[i]) ** 2 for i in range(3))
    if distance < best[0]:
        best = (distance, index)
    delta = target[axis] - point[axis]
    near, far = (left, right) if delta < 0 else (right, left)
    best = _query_kd_tree(near, target, best)
    if delta * delta < best[0]:
        best = _query_kd_tree(far, target, best)
    return best


def match_materials(
    color_counts: Counter[tuple[int, int, int]],
    blocks: list[dict],
    walls: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Match each unique image color to its nearest block or wall average."""
    materials = [
        {
            "type": material_type,
            "name": material["name"],
            "avg_color": tuple(material["avg_color"]),
        }
        for material_type, database in (("block", blocks), ("wall", walls))
        for material in database
    ]
    materials.sort(key=lambda item: (item["avg_color"], item["type"], item["name"]))
    if not materials:
        raise ValueError("material databases contain no colors")

    tree = _build_kd_tree(
        [(_rgb_to_lab(material["avg_color"]), index) for index, material in enumerate(materials)]
    )
    color_matches = []
    material_counts: Counter[tuple[str, str, tuple[int, int, int]]] = Counter()

    for color, count in sorted(color_counts.items()):
        distance_squared, material_index = _query_kd_tree(tree, _rgb_to_lab(color))
        distance = math.sqrt(distance_squared)
        material = materials[material_index]
        material_key = (
            material["type"],
            material["name"],
            material["avg_color"],
        )
        material_counts[material_key] += count
        color_matches.append(
            {
                "source_color": list(color),
                "source_hex": f"#{color[0]:02X}{color[1]:02X}{color[2]:02X}",
                "pixel_count": count,
                "material_type": material["type"],
                "material_name": material["name"],
                "material_color": list(material["avg_color"]),
                "distance": round(float(distance), 3),
            }
        )

    material_summary = [
        {
            "material_type": material_type,
            "material_name": name,
            "material_color": list(color),
            "pixel_count": count,
        }
        for (material_type, name, color), count in material_counts.most_common()
    ]
    return color_matches, material_summary


def print_materials(material_summary: list[dict]) -> None:
    """Print the aggregated material list as a plain terminal table."""
    if not material_summary:
        print("\nNo visible pixels to match.")
        return

    type_width = max(4, *(len(item["material_type"]) for item in material_summary))
    name_width = max(8, *(len(item["material_name"]) for item in material_summary))
    print("\nTerraria material list")
    print(
        f"{'COUNT':>7}  {'TYPE':<{type_width}}  "
        f"{'MATERIAL':<{name_width}}  COLOR"
    )
    print(
        f"{'-' * 7}  {'-' * type_width}  "
        f"{'-' * name_width}  -------"
    )
    for item in material_summary:
        red, green, blue = item["material_color"]
        color_hex = f"#{red:02X}{green:02X}{blue:02X}"
        print(
            f"{item['pixel_count']:>7}  "
            f"{item['material_type']:<{type_width}}  "
            f"{item['material_name']:<{name_width}}  {color_hex}"
        )
    print(f"{'-' * 7}")
    print(f"{sum(item['pixel_count'] for item in material_summary):>7}  TOTAL")


def draw_material_reconstruction(
    cleaned: Image.Image,
    color_matches: list[dict],
    output_size: tuple[int, int],
) -> Image.Image:
    """Paint each logical pixel with its matched material average color."""
    lookup = {
        tuple(match["source_color"]): tuple(match["material_color"])
        for match in color_matches
    }
    source = cleaned.convert("RGBA")
    reconstruction = Image.new("RGBA", source.size, (0, 0, 0, 0))
    source_pixels = source.load()
    output_pixels = reconstruction.load()

    for y in range(source.height):
        for x in range(source.width):
            red, green, blue, alpha = source_pixels[x, y]
            if alpha > 0:
                output_pixels[x, y] = (*lookup[(red, green, blue)], 255)

    if reconstruction.size != output_size:
        reconstruction = reconstruction.resize(output_size, Image.Resampling.NEAREST)
    return reconstruction


def print_terminal_image(reconstruction: Image.Image) -> None:
    """Print one terminal cell per logical pixel using material average colors."""
    image = reconstruction.convert("RGBA")
    pixels = image.load()
    print("\nTerraria-color image (one cell per detected pixel)")
    for y in range(image.height):
        row = []
        for x in range(image.width):
            red, green, blue, alpha = pixels[x, y]
            if alpha == 0:
                row.append("  ")
            else:
                row.append(f"\033[48;2;{red};{green};{blue}m  \033[0m")
        print("".join(row))


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Discover an image's pixel grid with Proper Pixel Art and save "
            "one output pixel per detected grid cell."
        )
    )
    parser.add_argument("image", type=Path, help="Source image to clean")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help=(
            "Destination for the scale-1 cleaned PNG "
            "(default: local_outputs/<image>_scale1.png)"
        ),
    )
    parser.add_argument(
        "--remove-background",
        action="store_true",
        help="Flood-fill and remove the dominant edge-connected color",
    )
    parser.add_argument(
        "--background-tolerance",
        type=int,
        default=0,
        metavar="DISTANCE",
        help="RGB distance accepted by background flood fill (default: 0)",
    )
    parser.add_argument(
        "--colors",
        type=int,
        default=None,
        help="Proper Pixel Art palette size; 0 preserves all colors",
    )
    parser.add_argument(
        "--pixel-width",
        type=int,
        default=0,
        help="Override source pixel width; 0 detects it automatically",
    )
    parser.add_argument(
        "--bin-size",
        type=int,
        default=52,
        help="Proper Pixel Art noise/color grouping size (default: 52)",
    )
    parser.add_argument(
        "--intermediate-dir",
        type=Path,
        help="Save Proper Pixel Art's grid-detection diagnostics here",
    )
    parser.add_argument(
        "--materials-output",
        type=Path,
        help="Optionally save the detailed color matches as JSON",
    )
    parser.add_argument(
        "--draw-output",
        type=Path,
        help=(
            "Draw the Terraria-color reconstruction at the original image size "
            "and save it as PNG (default: local_outputs/<image>_terraria.png)"
        ),
    )
    parser.add_argument(
        "--no-terminal-image",
        action="store_true",
        help="Do not print the Terraria-color image in the terminal",
    )
    parser.add_argument(
        "--db-dir",
        type=Path,
        default=Path("./data"),
        help="Directory containing cleaned_blocks.json and cleaned_walls.json",
    )
    args = parser.parse_args()

    if not args.image.is_file():
        parser.error(f"image not found: {args.image}")
    if args.background_tolerance < 0:
        parser.error("--background-tolerance must be zero or greater")
    if not 1 <= args.bin_size <= 255:
        parser.error("--bin-size must be between 1 and 255")

    if args.output is None:
        args.output = Path("local_outputs") / f"{args.image.stem}_scale1.png"
    if args.draw_output is None:
        args.draw_output = Path("local_outputs") / f"{args.image.stem}_terraria.png"

    with Image.open(args.image) as source:
        source.load()
        source_size = source.size
        cleaned = clean_image(
            source,
            num_colors=args.colors,
            pixel_width=args.pixel_width,
            bin_size=args.bin_size,
            intermediate_dir=args.intermediate_dir,
        )

    print(
        f"Detected logical grid: {cleaned.width} x {cleaned.height} "
        f"from {args.image.name}"
    )

    if args.remove_background:
        cleaned, color, removed_count = remove_edge_background(
            cleaned,
            tolerance=args.background_tolerance,
        )
        print(
            "Removed edge-connected background "
            f"#{color[0]:02X}{color[1]:02X}{color[2]:02X}: "
            f"{removed_count} pixels"
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    cleaned.save(args.output, format="PNG")
    print(f"Saved cleaned image: {args.output.resolve()}")

    color_counts = count_visible_colors(cleaned)
    blocks = load_database(args.db_dir, "blocks")
    walls = load_database(args.db_dir, "walls")
    color_matches, material_summary = match_materials(
        color_counts,
        blocks,
        walls,
    )
    print(
        f"Matched {len(color_counts)} unique colors against "
        f"{len(blocks) + len(walls)} materials"
    )

    logical_reconstruction = draw_material_reconstruction(
        cleaned,
        color_matches,
        cleaned.size,
    )
    if not args.no_terminal_image:
        print_terminal_image(logical_reconstruction)
    print_materials(material_summary)

    reconstruction = logical_reconstruction
    if reconstruction.size != source_size:
        reconstruction = reconstruction.resize(
            source_size,
            Image.Resampling.NEAREST,
        )
    args.draw_output.parent.mkdir(parents=True, exist_ok=True)
    reconstruction.save(args.draw_output, format="PNG")
    print(
        "Saved Terraria-color reconstruction: "
        f"{args.draw_output.resolve()} "
        f"({reconstruction.width} x {reconstruction.height})"
    )

    if args.materials_output:
        report = {
            "image": str(args.image.resolve()),
            "cleaned_image": str(args.output.resolve()),
            "grid": {"width": cleaned.width, "height": cleaned.height},
            "visible_pixels": sum(color_counts.values()),
            "unique_colors": len(color_counts),
            "color_matches": color_matches,
            "materials": material_summary,
        }
        args.materials_output.parent.mkdir(parents=True, exist_ok=True)
        with args.materials_output.open("w", encoding="utf-8") as file:
            json.dump(report, file, indent=2)
            file.write("\n")
        print(f"Saved material report: {args.materials_output.resolve()}")


if __name__ == "__main__":
    main()
