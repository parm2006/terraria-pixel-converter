#!/usr/bin/env python3
"""Rebuild complete Terraria block and wall color databases.

TEdit's maintained game metadata is the authoritative catalog. The Terraria
wiki is used only to obtain an image for a 32x32 color sample. Every catalog
record is accounted for as accepted, rejected, or errored-with-fallback.

Usage:
    uv run python scrape_terraria.py --output-dir data --replace-cleaned
"""

from __future__ import annotations

import argparse
import json
import time
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
from PIL import Image, UnidentifiedImageError
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

TEDIT_TILES_URL = (
    "https://raw.githubusercontent.com/TEdit/Terraria-Map-Editor/"
    "main/src/TEdit.Terraria/Data/tiles.json"
)
TEDIT_WALLS_URL = (
    "https://raw.githubusercontent.com/TEdit/Terraria-Map-Editor/"
    "main/src/TEdit.Terraria/Data/walls.json"
)
WIKI_FILE_REDIRECT = "https://terraria.wiki.gg/wiki/Special:Redirect/file/{}"
USER_AGENT = "TerrariaPixelArtTool/2.0 (open-source educational project)"
DEFAULT_SAMPLE_SIZE = 32
DEFAULT_REQUEST_DELAY = 0.08


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def fetch_json(url: str, session: requests.Session) -> list[dict[str, Any]]:
    response = session.get(url, timeout=30)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise RuntimeError(f"Expected a JSON list from {url}")
    return payload


def average_color(image: Image.Image, sample_size: int) -> tuple[int, int, int] | None:
    """Average visible pixels in the top-left sample_size square."""

    rgba = image.convert("RGBA")
    sample = rgba.crop((0, 0, min(rgba.width, sample_size), min(rgba.height, sample_size)))
    red = green = blue = count = 0
    for r, g, b, alpha in sample.getdata():
        if alpha > 10:
            red += r
            green += g
            blue += b
            count += 1
    if count == 0:
        return None
    return red // count, green // count, blue // count


def parse_tedit_color(value: Any) -> tuple[int, int, int] | None:
    if not isinstance(value, str):
        return None
    hexadecimal = value.removeprefix("#")
    if len(hexadecimal) not in {6, 8}:
        return None
    try:
        return tuple(int(hexadecimal[index:index + 2], 16) for index in (0, 2, 4))
    except ValueError:
        return None


def wiki_sprite_url(name: str) -> str:
    filename = quote(f"{name.replace(' ', '_')}.png", safe="_()'-")
    return WIKI_FILE_REDIRECT.format(filename)


def fetch_sprite_color(
    name: str,
    session: requests.Session,
    sample_size: int,
) -> tuple[tuple[int, int, int] | None, str, str | None]:
    requested_url = wiki_sprite_url(name)
    try:
        response = session.get(requested_url, timeout=20)
        response.raise_for_status()
        content_type = response.headers.get("Content-Type", "").lower()
        if "image" not in content_type:
            return None, response.url, f"not an image ({content_type or 'unknown content type'})"
        with Image.open(BytesIO(response.content)) as image:
            color = average_color(image, sample_size)
        if color is None:
            return None, response.url, "sample contains no visible pixels"
        return color, response.url, None
    except (requests.RequestException, UnidentifiedImageError, OSError) as error:
        return None, requested_url, str(error)


def tile_rejection_reason(record: dict[str, Any]) -> str | None:
    if not isinstance(record.get("id"), int) or not str(record.get("name", "")).strip():
        return "missing_id_or_name"
    return None


def wall_rejection_reason(record: dict[str, Any]) -> str | None:
    wall_id = record.get("id")
    name = str(record.get("name", "")).strip()
    if not isinstance(wall_id, int) or not name:
        return "missing_id_or_name"
    return None


def material_entry(
    record: dict[str, Any],
    material_type: str,
    session: requests.Session,
    sample_size: int,
    request_delay: float,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    material_id = record["id"]
    name = str(record["name"]).strip()
    fallback = parse_tedit_color(record.get("color"))
    time.sleep(request_delay)
    sprite_color, sprite_url, error = fetch_sprite_color(name, session, sample_size)

    color = sprite_color or fallback
    if color is None:
        return None, {
            "id": material_id,
            "name": name,
            "material_type": material_type,
            "reason": "sprite_and_fallback_color_unavailable",
            "sprite_url": sprite_url,
            "error": error,
        }

    entry = {
        "id": material_id,
        "name": name,
        "material_type": material_type,
        "avg_color": list(color),
        "sprite_url": sprite_url,
        "color_source": "wiki_32x32_sample" if sprite_color else "tedit_fallback",
    }
    if material_type == "block":
        entry.update({
            "is_solid": bool(record.get("isSolid", False)),
            "is_solid_top": bool(record.get("isSolidTop", False)),
            "is_framed": bool(record.get("isFramed", False)),
            "frame_size": record.get("frameSize"),
        })
    if error is None:
        return entry, None
    return entry, {
        "id": material_id,
        "name": name,
        "material_type": material_type,
        "reason": "wiki_sprite_failed_used_tedit_fallback",
        "sprite_url": sprite_url,
        "error": error,
    }


def build_palette(
    records: list[dict[str, Any]],
    material_type: str,
    session: requests.Session,
    sample_size: int,
    request_delay: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    reject = tile_rejection_reason if material_type == "block" else wall_rejection_reason

    for index, record in enumerate(records, start=1):
        reason = reject(record)
        if reason:
            rejected.append({
                "id": record.get("id"),
                "name": record.get("name"),
                "reason": reason,
                "catalog": "TEdit",
            })
            continue

        entry, error = material_entry(
            record, material_type, session, sample_size, request_delay
        )
        if entry is not None:
            accepted.append(entry)
            print(
                f"  [OK] {entry['name']:<52} ID {entry['id']:<4} "
                f"RGB{tuple(entry['avg_color'])} ({entry['color_source']})"
            )
        else:
            rejected.append({
                "id": record.get("id"),
                "name": record.get("name"),
                "reason": "no_usable_color",
                "catalog": "TEdit",
            })
        if error is not None:
            errors.append(error)
            print(f"  [FALLBACK] {error['name']}: {error['error']}")
        if index % 100 == 0:
            print(f"  Processed {index}/{len(records)} {material_type} catalog records")

    accepted.sort(key=lambda item: item["id"])
    rejected.sort(key=lambda item: (item.get("id") is None, item.get("id") or -1))
    errors.sort(key=lambda item: (item.get("id") is None, item.get("id") or -1))
    return accepted, rejected, errors


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as file:
        json.dump(payload, file, indent=2, ensure_ascii=False)
        file.write("\n")
    temporary.replace(path)


def validate_refresh(
    source_tiles: list[dict[str, Any]],
    source_walls: list[dict[str, Any]],
    blocks: list[dict[str, Any]],
    walls: list[dict[str, Any]],
    rejected_blocks: list[dict[str, Any]],
    rejected_walls: list[dict[str, Any]],
) -> None:
    block_names = {entry["name"] for entry in blocks}
    missing = {"Dirt Block", "Stone Block", "Sand Block"} - block_names
    if missing:
        raise RuntimeError(f"Missing required blocks: {sorted(missing)}")
    if len(blocks) < 200 or len(walls) < 200:
        raise RuntimeError(f"Unexpectedly small palette: {len(blocks)} blocks, {len(walls)} walls")
    if len(blocks) + len(rejected_blocks) != len(source_tiles):
        raise RuntimeError("Some TEdit tile records were not accounted for")
    if len(walls) + len(rejected_walls) != len(source_walls):
        raise RuntimeError("Some TEdit wall records were not accounted for")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("data"))
    parser.add_argument("--sample-size", type=int, default=DEFAULT_SAMPLE_SIZE)
    parser.add_argument("--request-delay", type=float, default=DEFAULT_REQUEST_DELAY)
    parser.add_argument(
        "--replace-cleaned",
        action="store_true",
        help="Replace cleaned_blocks.json and cleaned_walls.json after validation",
    )
    args = parser.parse_args()
    if args.sample_size < 1:
        parser.error("--sample-size must be at least 1")
    if args.request_delay < 0:
        parser.error("--request-delay cannot be negative")

    session = make_session()
    print("Downloading authoritative TEdit tile and wall catalogs...")
    source_tiles = fetch_json(TEDIT_TILES_URL, session)
    source_walls = fetch_json(TEDIT_WALLS_URL, session)
    write_json(args.output_dir / "raw_blocks.json", source_tiles)
    write_json(args.output_dir / "raw_walls.json", source_walls)

    print(f"Building block palette from {len(source_tiles)} tile definitions...")
    blocks, rejected_blocks, block_errors = build_palette(
        source_tiles, "block", session, args.sample_size, args.request_delay
    )
    print(f"Building wall palette from {len(source_walls)} wall definitions...")
    walls, rejected_walls, wall_errors = build_palette(
        source_walls, "wall", session, args.sample_size, args.request_delay
    )

    validate_refresh(
        source_tiles,
        source_walls,
        blocks,
        walls,
        rejected_blocks,
        rejected_walls,
    )
    write_json(args.output_dir / "rejected" / "blocks.json", rejected_blocks)
    write_json(args.output_dir / "rejected" / "walls.json", rejected_walls)
    write_json(args.output_dir / "errors" / "blocks.json", block_errors)
    write_json(args.output_dir / "errors" / "walls.json", wall_errors)

    if args.replace_cleaned:
        write_json(args.output_dir / "cleaned_blocks.json", blocks)
        write_json(args.output_dir / "cleaned_walls.json", walls)
    else:
        write_json(args.output_dir / "refreshed_blocks.json", blocks)
        write_json(args.output_dir / "refreshed_walls.json", walls)

    print("\nRefresh complete")
    print(f"  Blocks accepted: {len(blocks)}")
    print(f"  Walls accepted: {len(walls)}")
    print(f"  Tiles rejected by metadata: {len(rejected_blocks)}")
    print(f"  Walls rejected by metadata: {len(rejected_walls)}")
    print(f"  Block sprite fallbacks/errors: {len(block_errors)}")
    print(f"  Wall sprite fallbacks/errors: {len(wall_errors)}")


if __name__ == "__main__":
    main()
