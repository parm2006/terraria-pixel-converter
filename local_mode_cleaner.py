"""Save every stage of local denoising and Terraria material conversion.

The source-resolution denoiser snaps locally similar RGB colors together. The
script then runs Proper Pixel Art, saves its logical grid and diagnostics,
optionally removes the outside background, and draws the Terraria result.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from PIL import Image, ImageOps

from pixel_art import (
    clean_image as proper_pixel_art,
    count_visible_colors as count_logical_colors,
    draw_material_reconstruction,
    load_database,
    match_materials,
    print_materials,
    remove_edge_background,
)

RGB = tuple[int, int, int]


def colors_are_close(left: RGB, right: RGB, tolerance: int) -> bool:
    """Return true when no RGB channel differs by more than tolerance."""

    return max(abs(a - b) for a, b in zip(left, right, strict=True)) <= tolerance


def local_mode(colors: list[RGB], tolerance: int) -> RGB:
    """Find the color with the strongest nearby-color support.

    Exact frequency breaks the first tie. Total RGB distance breaks the next,
    which makes the result a representative existing color rather than a new
    averaged color.
    """

    counts = Counter(colors)
    candidates = list(counts)

    def score(candidate: RGB) -> tuple[int, int, int]:
        nearby_support = sum(
            count
            for color, count in counts.items()
            if colors_are_close(candidate, color, tolerance)
        )
        total_distance = sum(
            count * sum(abs(a - b) for a, b in zip(candidate, color, strict=True))
            for color, count in counts.items()
        )
        return nearby_support, counts[candidate], -total_distance

    return max(candidates, key=score)


def window_starts(length: int, stride: int) -> range:
    return range(0, length, stride)


def clean_pass(
    image: Image.Image,
    *,
    window_size: int,
    stride: int,
    tolerance: int,
) -> tuple[Image.Image, int]:
    """Run one immutable-source pass and combine overlapping-window votes."""

    source = image.convert("RGBA")
    width, height = source.size
    pixels = source.load()
    proposals: list[list[RGB]] = [[] for _ in range(width * height)]

    for top in window_starts(height, stride):
        bottom = min(top + window_size, height)
        for left in window_starts(width, stride):
            right = min(left + window_size, width)
            colors = [
                pixels[x, y][:3]
                for y in range(top, bottom)
                for x in range(left, right)
                if pixels[x, y][3] > 0
            ]
            if not colors:
                continue

            mode = local_mode(colors, tolerance)
            for y in range(top, bottom):
                for x in range(left, right):
                    rgba = pixels[x, y]
                    if rgba[3] > 0 and colors_are_close(rgba[:3], mode, tolerance):
                        proposals[y * width + x].append(mode)

    output = source.copy()
    output_pixels = output.load()
    changed = 0

    for y in range(height):
        for x in range(width):
            original = pixels[x, y]
            votes = proposals[y * width + x]
            if not votes:
                continue

            vote_counts = Counter(votes)
            replacement = min(
                vote_counts,
                key=lambda color: (
                    -vote_counts[color],
                    sum(abs(a - b) for a, b in zip(color, original[:3], strict=True)),
                    color,
                ),
            )
            if replacement != original[:3]:
                output_pixels[x, y] = (*replacement, original[3])
                changed += 1

    return output, changed


def clean_image(
    image: Image.Image,
    *,
    window_size: int = 6,
    stride: int = 3,
    tolerance: int = 8,
    passes: int = 1,
) -> tuple[Image.Image, list[int]]:
    cleaned = image.convert("RGBA")
    changes: list[int] = []
    for _ in range(passes):
        cleaned, changed = clean_pass(
            cleaned,
            window_size=window_size,
            stride=stride,
            tolerance=tolerance,
        )
        changes.append(changed)
    return cleaned, changes


def count_visible_colors(image: Image.Image) -> int:
    """Count distinct RGBA colors, excluding fully transparent pixels."""

    rgba = image.convert("RGBA")
    return len({pixel for pixel in rgba.get_flattened_data() if pixel[3] > 0})


def reconstruction_error(source: Image.Image, logical: Image.Image) -> float:
    """Measure mean per-channel error after nearest-neighbor reconstruction."""

    reference = source.convert("RGBA")
    reconstruction = logical.convert("RGBA").resize(
        reference.size,
        Image.Resampling.NEAREST,
    )
    total_error = 0
    compared_channels = 0
    for original, rebuilt in zip(
        reference.get_flattened_data(),
        reconstruction.get_flattened_data(),
        strict=True,
    ):
        if original[3] == 0 and rebuilt[3] == 0:
            continue
        total_error += sum(abs(a - b) for a, b in zip(original, rebuilt, strict=True))
        compared_channels += 4
    return total_error / compared_channels if compared_channels else 0.0


def harmonic_widths(estimated_width: int) -> list[int]:
    """Return plausible fundamental widths without considering random periods."""

    if estimated_width <= 2:
        return [estimated_width]
    return [
        width
        for width in range(estimated_width, 1, -1)
        if estimated_width % width == 0
    ]


def _cluster_edge_positions(positions: list[int], maximum_gap: int = 2) -> list[int]:
    """Collapse a several-pixel-wide compressed edge into one coordinate."""

    if not positions:
        return []
    clusters = [[positions[0]]]
    for position in positions[1:]:
        if position - clusters[-1][-1] <= maximum_gap:
            clusters[-1].append(position)
        else:
            clusters.append([position])
    return [round(sum(cluster) / len(cluster)) for cluster in clusters]


def collect_edge_events(
    image: Image.Image,
    *,
    threshold: int = 18,
    sample_lines: int = 220,
) -> tuple[list[int], list[int]]:
    """Collect vertical and horizontal boundary coordinates from sampled lines."""

    rgb = image.convert("RGB")
    pixels = rgb.load()
    width, height = rgb.size
    vertical_events: list[int] = []
    horizontal_events: list[int] = []

    row_step = max(1, height // sample_lines)
    for y in range(0, height, row_step):
        positions = []
        for x in range(1, width):
            if sum(abs(a - b) for a, b in zip(pixels[x - 1, y], pixels[x, y], strict=True)) >= threshold:
                positions.append(x)
        vertical_events.extend(_cluster_edge_positions(positions))

    column_step = max(1, width // sample_lines)
    for x in range(0, width, column_step):
        positions = []
        for y in range(1, height):
            if sum(abs(a - b) for a, b in zip(pixels[x, y - 1], pixels[x, y], strict=True)) >= threshold:
                positions.append(y)
        horizontal_events.extend(_cluster_edge_positions(positions))

    return vertical_events, horizontal_events


def _period_alignment(events: list[int], period: int) -> float:
    """Score whether edge coordinates share a repeating phase for a period."""

    if not events or period < 2:
        return 0.0
    tolerance = max(1, min(3, round(period * 0.08)))
    if period <= 2 * tolerance + 1:
        return 0.0
    residues = Counter(position % period for position in events)
    aligned = max(
        sum(residues[(phase + offset) % period] for offset in range(-tolerance, tolerance + 1))
        for phase in range(period)
    )
    raw_score = aligned / len(events)
    random_baseline = (2 * tolerance + 1) / period
    return max(0.0, (raw_score - random_baseline) / (1 - random_baseline))


def edge_period_candidates(
    image: Image.Image,
    *,
    maximum_period: int,
) -> list[dict]:
    """Rank source pixel widths by two-axis repeating edge alignment."""

    vertical, horizontal = collect_edge_events(image)
    results = []
    for period in range(4, maximum_period + 1):
        vertical_score = _period_alignment(vertical, period)
        horizontal_score = _period_alignment(horizontal, period)
        results.append(
            {
                "source_pixel_width": period,
                "edge_score": round((vertical_score + horizontal_score) / 2, 6),
                "vertical_score": round(vertical_score, 6),
                "horizontal_score": round(horizontal_score, 6),
            }
        )
    return sorted(
        results,
        key=lambda item: (item["edge_score"], item["source_pixel_width"]),
        reverse=True,
    )


def refine_logical_grid(
    source: Image.Image,
    auto_logical: Image.Image,
    *,
    num_colors: int | None,
    bin_size: int,
    diagnostics_dir: Path | None,
    improvement_threshold: float,
) -> tuple[Image.Image, int, list[dict]]:
    """Correct a detected grid when a harmonic divisor preserves more detail.

    Proper Pixel Art sometimes detects twice the fundamental pixel width. We
    only consider exact integer divisors of its estimate. A finer grid is
    accepted only when its reconstruction error improves by a substantial
    relative amount, preventing arbitrary periods and the trivial 1px answer.
    """

    estimated_width = max(1, round(source.width / auto_logical.width))
    candidates = harmonic_widths(estimated_width)
    logical_by_width = {estimated_width: auto_logical}
    errors = {estimated_width: reconstruction_error(source, auto_logical)}

    candidate_root = (
        diagnostics_dir / "harmonic_candidates" if diagnostics_dir is not None else None
    )
    if candidate_root is not None:
        candidate_root.mkdir(parents=True, exist_ok=True)
    for width in candidates:
        if width == estimated_width:
            continue
        candidate_dir = (
            candidate_root / f"source_width_{width}"
            if candidate_root is not None
            else None
        )
        if candidate_dir is not None:
            candidate_dir.mkdir(parents=True, exist_ok=True)
        candidate = proper_pixel_art(
            source,
            num_colors=num_colors,
            pixel_width=width,
            bin_size=bin_size,
            intermediate_dir=candidate_dir,
        )
        logical_by_width[width] = candidate
        errors[width] = reconstruction_error(source, candidate)

    selected_width = estimated_width
    decisions: list[dict] = []
    while True:
        children = [
            width
            for width in candidates
            if width < selected_width and selected_width % width == 0
        ]
        accepted_width = None
        for width in sorted(children, reverse=True):
            current_error = errors[selected_width]
            relative_improvement = (
                (current_error - errors[width]) / current_error
                if current_error > 0
                else 0.0
            )
            decisions.append(
                {
                    "from_width": selected_width,
                    "candidate_width": width,
                    "from_error": round(current_error, 6),
                    "candidate_error": round(errors[width], 6),
                    "relative_improvement": round(relative_improvement, 6),
                    "accepted": relative_improvement >= improvement_threshold,
                }
            )
            if relative_improvement >= improvement_threshold:
                accepted_width = width
                break
        if accepted_width is None:
            break
        selected_width = accepted_width

    edge_rankings = edge_period_candidates(
        source,
        maximum_period=min(estimated_width, max(4, min(source.size) // 4)),
    )
    edge_candidate = None
    if edge_rankings and edge_rankings[0]["edge_score"] > 0:
        credible_score = edge_rankings[0]["edge_score"] * 0.70
        credible_periods = [
            item["source_pixel_width"]
            for item in edge_rankings
            if item["edge_score"] >= credible_score
        ]
        if credible_periods:
            edge_candidate = max(credible_periods)

    # If the selected harmonic remains dramatically coarser than a strong
    # image-derived period, Proper Pixel Art's first estimate was not merely a
    # small multiple (the pyramid is 159px vs. an observed ~15px cadence).
    if (
        edge_candidate is not None
        and edge_candidate < selected_width
        and selected_width / edge_candidate >= 2.5
    ):
        if edge_candidate not in logical_by_width:
            candidate_dir = (
                candidate_root / f"edge_width_{edge_candidate}"
                if candidate_root is not None
                else None
            )
            if candidate_dir is not None:
                candidate_dir.mkdir(parents=True, exist_ok=True)
            candidate = proper_pixel_art(
                source,
                num_colors=num_colors,
                pixel_width=edge_candidate,
                bin_size=bin_size,
                intermediate_dir=candidate_dir,
            )
            logical_by_width[edge_candidate] = candidate
            errors[edge_candidate] = reconstruction_error(source, candidate)
            candidates.append(edge_candidate)
        current_error = errors[selected_width]
        relative_improvement = (
            (current_error - errors[edge_candidate]) / current_error
            if current_error > 0
            else 0.0
        )
        accepted = relative_improvement >= improvement_threshold
        decisions.append(
            {
                "from_width": selected_width,
                "candidate_width": edge_candidate,
                "candidate_source": "edge_periodicity",
                "from_error": round(current_error, 6),
                "candidate_error": round(errors[edge_candidate], 6),
                "relative_improvement": round(relative_improvement, 6),
                "accepted": accepted,
            }
        )
        if accepted:
            selected_width = edge_candidate

    report = [
        {
            "source_pixel_width": width,
            "logical_grid": list(logical_by_width[width].size),
            "reconstruction_error": round(errors[width], 6),
            "selected": width == selected_width,
        }
        for width in sorted(set(candidates), reverse=True)
    ]
    report.extend({"decision": decision} for decision in decisions)
    report.append({"edge_period_rankings": edge_rankings[:20]})
    return logical_by_width[selected_width], selected_width, report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Snap nearby colors to the local mode using overlapping 6x6 windows "
            "moving 3 pixels at a time, then save every Proper Pixel Art and "
            "Terraria conversion stage."
        )
    )
    parser.add_argument("image", type=Path, help="Image to clean")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output PNG (default: local_outputs/<name>_mode_cleaned.png)",
    )
    parser.add_argument("--window", type=int, default=6, help="Window size (default: 6)")
    parser.add_argument("--stride", type=int, default=3, help="Window stride (default: 3)")
    parser.add_argument(
        "--tolerance",
        type=int,
        default=8,
        help="Maximum allowed difference in every RGB channel (default: 8)",
    )
    parser.add_argument(
        "--passes",
        type=int,
        default=1,
        help="Number of complete overlapping-window passes (default: 1)",
    )
    parser.add_argument(
        "--stage-dir",
        type=Path,
        help="Directory for every pipeline stage",
    )
    parser.add_argument(
        "--pixel-width",
        type=int,
        default=0,
        help="Proper Pixel Art source pixel width; 0 detects it (default: 0)",
    )
    parser.add_argument(
        "--bin-size",
        type=int,
        default=52,
        help="Proper Pixel Art color grouping size (default: 52)",
    )
    parser.add_argument(
        "--colors",
        type=int,
        default=0,
        help="Proper Pixel Art palette limit; 0 preserves colors (default: 0)",
    )
    parser.add_argument(
        "--keep-background",
        action="store_true",
        help="Do not remove the dominant edge-connected background",
    )
    parser.add_argument(
        "--background-tolerance",
        type=int,
        default=1,
        help="RGB distance for edge-connected background removal (default: 1)",
    )
    parser.add_argument(
        "--db-dir",
        type=Path,
        default=Path("data"),
        help="Terraria material database directory (default: data)",
    )
    parser.add_argument(
        "--grid-improvement-threshold",
        type=float,
        default=0.30,
        help=(
            "Relative reconstruction improvement required to accept a harmonic "
            "grid subdivision (default: 0.30)"
        ),
    )
    args = parser.parse_args()

    if not args.image.is_file():
        parser.error(f"image not found: {args.image}")
    if args.window < 1:
        parser.error("--window must be at least 1")
    if args.stride < 1:
        parser.error("--stride must be at least 1")
    if not 0 <= args.tolerance <= 255:
        parser.error("--tolerance must be between 0 and 255")
    if args.passes < 1:
        parser.error("--passes must be at least 1")
    if args.pixel_width < 0:
        parser.error("--pixel-width must be zero or greater")
    if not 1 <= args.bin_size <= 255:
        parser.error("--bin-size must be between 1 and 255")
    if args.colors < 0:
        parser.error("--colors must be zero or greater")
    if args.background_tolerance < 0:
        parser.error("--background-tolerance cannot be negative")
    if not 0 <= args.grid_improvement_threshold <= 1:
        parser.error("--grid-improvement-threshold must be between 0 and 1")

    output = args.output or (
        Path("local_outputs") / f"{args.image.stem}_mode_cleaned.png"
    )
    stage_dir = args.stage_dir or (
        Path("local_outputs") / f"{args.image.stem}_stages"
    )

    with Image.open(args.image) as opened:
        source = ImageOps.exif_transpose(opened).convert("RGBA")
        source.load()

    cleaned, changes = clean_image(
        source,
        window_size=args.window,
        stride=args.stride,
        tolerance=args.tolerance,
        passes=args.passes,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    stage_dir.mkdir(parents=True, exist_ok=True)

    original_path = stage_dir / "00_original.png"
    smoothed_path = stage_dir / "01_smoothed.png"
    source.save(original_path, format="PNG")
    cleaned.save(output, format="PNG")
    cleaned.save(smoothed_path, format="PNG")

    diagnostics_dir = stage_dir / "proper_pixel_art_diagnostics"
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    auto_logical = proper_pixel_art(
        cleaned,
        num_colors=None if args.colors == 0 else args.colors,
        pixel_width=args.pixel_width,
        bin_size=args.bin_size,
        intermediate_dir=diagnostics_dir,
    )

    auto_proper_path = stage_dir / "02_proper_pixel_art_auto.png"
    auto_logical.resize(source.size, Image.Resampling.NEAREST).save(
        auto_proper_path,
        format="PNG",
    )

    estimated_width = max(1, round(source.width / auto_logical.width))
    selected_width = args.pixel_width or estimated_width
    refinement_report: list[dict] = []
    logical = auto_logical
    if args.pixel_width == 0:
        logical, selected_width, refinement_report = refine_logical_grid(
            cleaned,
            auto_logical,
            num_colors=None if args.colors == 0 else args.colors,
            bin_size=args.bin_size,
            diagnostics_dir=diagnostics_dir,
            improvement_threshold=args.grid_improvement_threshold,
        )

    selected_proper_path = stage_dir / "03_proper_pixel_art_selected.png"
    logical_path = stage_dir / "04_logical_grid.png"
    logical.resize(source.size, Image.Resampling.NEAREST).save(
        selected_proper_path,
        format="PNG",
    )
    logical.save(logical_path, format="PNG")

    refinement_path = stage_dir / "grid_refinement.json"
    with refinement_path.open("w", encoding="utf-8") as file:
        json.dump(
            {
                "automatic_estimated_source_pixel_width": estimated_width,
                "selected_source_pixel_width": selected_width,
                "improvement_threshold": args.grid_improvement_threshold,
                "candidates": refinement_report,
            },
            file,
            indent=2,
        )

    logical_for_matching = logical
    background_color = None
    removed_count = 0
    if not args.keep_background:
        logical_for_matching, background_color, removed_count = remove_edge_background(
            logical,
            tolerance=args.background_tolerance,
        )
    background_path = stage_dir / "05_background_removed.png"
    logical_for_matching.save(background_path, format="PNG")

    blocks = load_database(args.db_dir, "blocks")
    walls = load_database(args.db_dir, "walls")
    color_counts = count_logical_colors(logical_for_matching)
    color_matches, material_summary = match_materials(color_counts, blocks, walls)
    terraria = draw_material_reconstruction(
        logical_for_matching,
        color_matches,
        source.size,
    )
    terraria_path = stage_dir / "06_terraria.png"
    materials_path = stage_dir / "06_materials.json"
    terraria.save(terraria_path, format="PNG")
    with materials_path.open("w", encoding="utf-8") as file:
        json.dump(
            {
                "source": str(args.image),
                "logical_grid": [logical.width, logical.height],
                "color_matches": color_matches,
                "materials": material_summary,
            },
            file,
            indent=2,
        )

    print(f"Input size preserved: {source.width} x {source.height}")
    print(f"Visible colors found in input: {count_visible_colors(source)}")
    print(f"Visible colors after cleaning: {count_visible_colors(cleaned)}")
    print(
        f"Cleaner: {args.window}x{args.window} window, stride {args.stride}, "
        f"RGB tolerance {args.tolerance}, {args.passes} pass(es)"
    )
    print(f"Pixels changed per pass: {', '.join(map(str, changes))}")
    print(f"Saved cleaned image: {output.resolve()}")
    print(
        "Proper Pixel Art automatic grid: "
        f"{auto_logical.width} x {auto_logical.height} "
        f"(~{estimated_width}px source pixels)"
    )
    print(
        f"Selected logical grid: {logical.width} x {logical.height} "
        f"({selected_width}px source pixels)"
    )
    print(f"Logical colors found: {len(count_logical_colors(logical))}")
    if background_color is not None:
        print(
            "Removed edge-connected background "
            f"#{background_color[0]:02X}{background_color[1]:02X}{background_color[2]:02X}: "
            f"{removed_count} logical pixels"
        )
    print(f"Terraria material matches: {len(color_matches)} unique logical colors")
    print_materials(material_summary)
    print(f"Saved all numbered stages in: {stage_dir.resolve()}")
    print(f"  00 original: {original_path.resolve()}")
    print(f"  01 smoothed: {smoothed_path.resolve()}")
    print(f"  02 automatic Proper Pixel Art: {auto_proper_path.resolve()}")
    print(f"  03 selected Proper Pixel Art: {selected_proper_path.resolve()}")
    print(f"  04 logical grid: {logical_path.resolve()}")
    print(f"  05 background removed: {background_path.resolve()}")
    print(f"  06 Terraria: {terraria_path.resolve()}")
    print(f"  06 materials: {materials_path.resolve()}")
    print(f"  Grid refinement report: {refinement_path.resolve()}")


if __name__ == "__main__":
    main()
