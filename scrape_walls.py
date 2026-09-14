#!/usr/bin/env python3
"""Build a deployment-ready Terraria wall palette from raw_walls.json.

Only records whose name contains ``natural`` are excluded. Every other raw
wall is retained, enriched with the same core fields used by blocks:
``name``, ``material_type``, ``avg_color``, ``sprite_url``, and
``color_source``. Wiki file metadata is resolved in batches, while every HTTP
request is throttled by the configured delay (0.3 seconds by default).
"""

from __future__ import annotations

import argparse
import json
import time
from io import BytesIO
from pathlib import Path
from typing import Any

import requests
from PIL import Image, UnidentifiedImageError
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


WIKI_API_URL = "https://terraria.wiki.gg/api.php"
USER_AGENT = "TerrariaPixelArtTool/3.1 (personal palette collection)"
FILES_PER_API_QUERY = 50


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def wiki_file_urls(session: requests.Session, names: list[str]) -> dict[str, str]:
    """Resolve exact ``File:<wall>.png`` image URLs using the Wiki API."""

    lookup = {f"File:{name}.png".casefold(): name for name in names}
    response = session.get(
        WIKI_API_URL,
        params={
            "action": "query",
            "format": "json",
            "prop": "imageinfo",
            "iiprop": "url|mime",
            "titles": "|".join(f"File:{name}.png" for name in names),
        },
        timeout=30,
    )
    response.raise_for_status()
    pages = response.json().get("query", {}).get("pages", {})
    if not isinstance(pages, dict):
        raise RuntimeError("Wiki API response did not include image pages")

    resolved: dict[str, str] = {}
    for page in pages.values():
        if not isinstance(page, dict):
            continue
        name = lookup.get(str(page.get("title", "")).casefold())
        image_info = page.get("imageinfo")
        if name is None or not isinstance(image_info, list) or not image_info:
            continue
        url = image_info[0].get("url")
        if isinstance(url, str):
            resolved[name] = url
    return resolved


def average_visible_sample(image: Image.Image, sample_size: int) -> list[int] | None:
    rgba = image.convert("RGBA")
    sample = rgba.crop((0, 0, min(image.width, sample_size), min(image.height, sample_size)))
    visible = [pixel for pixel in sample.get_flattened_data() if pixel[3] > 10]
    if not visible:
        return None
    return [sum(pixel[channel] for pixel in visible) // len(visible) for channel in range(3)]


def fetch_sprite_color(
    session: requests.Session, image_url: str, sample_size: int
) -> tuple[list[int] | None, str | None]:
    try:
        response = session.get(image_url, timeout=30)
        response.raise_for_status()
        if "image" not in response.headers.get("Content-Type", "").lower():
            return None, "Wiki image URL did not return an image"
        with Image.open(BytesIO(response.content)) as image:
            color = average_visible_sample(image, sample_size)
        return color, None if color is not None else "sprite sample was transparent"
    except (requests.RequestException, UnidentifiedImageError, OSError) as error:
        return None, str(error)


def raw_hex_color(value: Any) -> list[int] | None:
    if not isinstance(value, str):
        return None
    hexadecimal = value.removeprefix("#")
    if len(hexadecimal) not in {6, 8}:
        return None
    try:
        return [int(hexadecimal[offset:offset + 2], 16) for offset in (0, 2, 4)]
    except ValueError:
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("data/raw_walls.json"))
    parser.add_argument("--output", type=Path, default=Path("data/cleaned_walls.json"))
    parser.add_argument(
        "--rejected-output",
        type=Path,
        default=Path("data/rejected/walls_contains_natural.json"),
    )
    parser.add_argument(
        "--errors-output",
        type=Path,
        default=Path("data/errors/walls_sprite_errors.json"),
    )
    parser.add_argument("--request-delay", type=float, default=0.3)
    parser.add_argument("--sample-size", type=int, default=32)
    args = parser.parse_args()
    if args.request_delay < 0:
        parser.error("--request-delay cannot be negative")
    if args.sample_size < 1:
        parser.error("--sample-size must be at least 1")

    try:
        source = json.loads(args.source.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        parser.error(f"source file does not exist: {error.filename}")
    except json.JSONDecodeError as error:
        parser.error(f"source file is not valid JSON: {error}")
    if not isinstance(source, list):
        parser.error("source JSON must be a list of wall records")

    kept: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for record in source:
        if not isinstance(record, dict) or not isinstance(record.get("name"), str):
            parser.error("source JSON contains a record without a string name")
        if "natural" in record["name"].casefold():
            rejected.append(record)
        else:
            kept.append(record)

    session = make_session()
    urls: dict[str, str] = {}
    api_errors: dict[str, str] = {}
    for offset in range(0, len(kept), FILES_PER_API_QUERY):
        batch = kept[offset:offset + FILES_PER_API_QUERY]
        try:
            urls.update(wiki_file_urls(session, [record["name"] for record in batch]))
        except (requests.RequestException, RuntimeError, ValueError) as error:
            for record in batch:
                api_errors[record["name"]] = f"Wiki API lookup failed: {error}"
        time.sleep(args.request_delay)

    walls: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for number, record in enumerate(kept, start=1):
        name = record["name"]
        image_url = urls.get(name)
        error = api_errors.get(name)
        color: list[int] | None = None
        if image_url is not None:
            time.sleep(args.request_delay)
            color, error = fetch_sprite_color(session, image_url, args.sample_size)
        elif error is None:
            error = "No exact File:<name>.png on the Wiki"

        color_source = "wiki_32x32_sample"
        if color is None:
            color = raw_hex_color(record.get("color"))
            color_source = "raw_wall_hex_fallback"
            if color is None:
                errors.append({**record, "sprite_url": image_url, "error": error or "no usable color"})
                continue
            errors.append({**record, "sprite_url": image_url, "error": error, "used_fallback": True})

        walls.append(
            {
                **record,
                "material_type": "wall",
                "avg_color": color,
                "sprite_url": image_url,
                "color_source": color_source,
            }
        )
        print(f"[{number}/{len(kept)}] {name} ({color_source})")

    walls.sort(key=lambda record: record["name"].casefold())
    rejected.sort(key=lambda record: record["name"].casefold())
    errors.sort(key=lambda record: record["name"].casefold())
    write_json(args.output, walls)
    write_json(args.rejected_output, rejected)
    write_json(args.errors_output, errors)
    print(f"Saved {len(walls)} walls -> {args.output}")
    print(f"Removed {len(rejected)} natural walls -> {args.rejected_output}")
    print(f"Wiki sprite fallbacks/errors: {len(errors)} -> {args.errors_output}")


if __name__ == "__main__":
    main()
