#!/usr/bin/env python3
"""Build a cleaned block palette from a pasted Terraria Wiki item-table export.

The input is the plain text copied from the Terraria Wiki table. Each blank-line
separated row becomes a source record; only the displayed item name is used for
filtering. Accepted names have their wiki sprite fetched and sampled so the
result works with pixel_art.py. Excluded and failed names are retained in audit
JSON files, never silently discarded.

Usage:
    python scrape_blocks_from_attachment.py C:\\path\\to\\terraria-block-list.txt

The input must actually be a block list. To protect the palette, this script
refuses a list that is overwhelmingly made of wall names.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from io import BytesIO
from pathlib import Path
from typing import Any

import requests


WIKI_API_URL = "https://terraria.wiki.gg/api.php"
USER_AGENT = "TerrariaPixelArtTool/3.0 (personal palette collection)"
WALL_NAME = re.compile(r"\b(?:wall|wallpaper)s?\b", re.IGNORECASE)
FILES_PER_API_QUERY = 50

# These are the classes the user explicitly does not want in a solid-block
# palette. The audit file records the exact rule that excluded each item.
EXCLUSION_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("platform", re.compile(r"\bplatforms?\b", re.IGNORECASE)),
    ("chest", re.compile(r"\bchests?\b", re.IGNORECASE)),
    ("torch", re.compile(r"\btorches?\b", re.IGNORECASE)),
    ("flower", re.compile(r"\bflowers?\b", re.IGNORECASE)),
    ("candle", re.compile(r"\bcandles?\b", re.IGNORECASE)),
    ("terrarium", re.compile(r"\bterrariums?\b", re.IGNORECASE)),
    ("balloon", re.compile(r"\bballoons?\b", re.IGNORECASE)),
    ("magic_water_dropper", re.compile(r"\bmagic water dropper\b", re.IGNORECASE)),
    ("rope", re.compile(r"\bropes?\b", re.IGNORECASE)),
    (
        "crafting_station",
        re.compile(
            r"\b(?:work bench|anvil|furnace|hellforge|sawmill|loom|"
            r"heavy work bench|blend-o-matic|meat grinder|solidifier|"
            r"crystal ball|sky mill|bone welder|glass kiln|autohammer|"
            r"ancient manipulator|tinkerer's workshop|alchemy table|"
            r"bewitching table|imbuing station|dye vat|keg|cooking pot|"
            r"extractinator)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "animal",
        re.compile(
            r"\b(?:squirrel|cockatiel|bird|bunny|cat|dog|duck|penguin|"
            r"turtle|snail|frog|fish|butterfly|firefly|ladybug|grasshopper|"
            r"worm|seahorse|crab|scorpion|lizard|mouse|rat|monkey|bat|owl|"
            r"flamingo|dolphin|dragonfly|goldfish|jellyfish|shark|fox|deer|"
            r"parrot|blue jay|cardinal|seagull|macaw|toucan|lacewing)\b",
            re.IGNORECASE,
        ),
    ),
)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def parse_pasted_table(path: Path) -> list[dict[str, Any]]:
    """Parse blank-line separated rows from the Wiki's copied table text."""

    groups: list[list[str]] = []
    current: list[str] = []
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        value = raw_line.strip()
        if value:
            current.append(value)
        elif current:
            groups.append(current)
            current = []
    if current:
        groups.append(current)

    rows: list[dict[str, Any]] = []
    for group in groups:
        if group == ["Name", "Stats", "Rarity", "Sell"]:
            continue
        if group[0].casefold().startswith("showing all "):
            continue
        rows.append({"name": group[0], "raw_columns": group[1:]})
    if not rows:
        raise ValueError("no item rows found in the pasted table")
    return rows


def exclusion_reason(name: str) -> str | None:
    for label, pattern in EXCLUSION_RULES:
        if pattern.search(name):
            return label
    return None


def selected_names_from_jsonc(path: Path) -> list[str]:
    """Read active ``name`` fields from a manually commented JSON/JSONC file.

    The reviewer can prefix every unwanted record (or just its ``name`` line)
    with ``//``.  This deliberately reads lines instead of parsing JSON, since
    the review file is no longer valid strict JSON after comments are added.
    """

    pattern = re.compile(r'^\s*"name"\s*:\s*"(?P<name>[^"]+)"')
    selected: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if match:
            selected.append(match.group("name"))
    return selected


def make_session() -> Any:
    try:
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
    except ImportError as error:
        raise RuntimeError("Install dependencies first: python -m pip install -r requirements.txt") from error
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


def wiki_file_urls(session: Any, names: list[str]) -> dict[str, str]:
    """Resolve exact item PNG files in batches through the official Wiki API."""

    # The API accepts underscores in titles but returns canonical titles with
    # spaces (for example ``File:Stone Block.png``).  Key the lookup the same
    # way so multiword blocks do not appear missing.
    filename_to_name = {f"File:{name}.png".casefold(): name for name in names}
    response = session.get(
        WIKI_API_URL,
        params={
            "action": "query",
            "format": "json",
            "prop": "imageinfo",
            "iiprop": "url|mime",
            # Keep the supplied capitalization in the request. MediaWiki's
            # file namespace is case-sensitive beyond the namespace prefix;
            # the lowercased dictionary keys are only for response matching.
            "titles": "|".join(f"File:{name}.png" for name in names),
        },
        timeout=25,
    )
    response.raise_for_status()
    payload = response.json()
    pages = payload.get("query", {}).get("pages", {})
    if not isinstance(pages, dict):
        raise RuntimeError("Wiki API response did not include image pages")

    urls: dict[str, str] = {}
    for page in pages.values():
        if not isinstance(page, dict):
            continue
        name = filename_to_name.get(str(page.get("title", "")).casefold())
        image_info = page.get("imageinfo")
        if name is None or not isinstance(image_info, list) or not image_info:
            continue
        url = image_info[0].get("url")
        if isinstance(url, str):
            urls[name] = url
    return urls


def average_visible_sample(image: Any, sample_size: int) -> list[int] | None:
    rgba = image.convert("RGBA")
    sample = rgba.crop((0, 0, min(image.width, sample_size), min(image.height, sample_size)))
    visible = [pixel for pixel in sample.get_flattened_data() if pixel[3] > 10]
    if not visible:
        return None
    return [sum(pixel[channel] for pixel in visible) // len(visible) for channel in range(3)]


def fetch_sprite_color(session: Any, image_url: str, sample_size: int) -> tuple[list[int] | None, str | None]:
    import requests
    from PIL import Image, UnidentifiedImageError

    try:
        response = session.get(image_url, timeout=25)
        response.raise_for_status()
        if "image" not in response.headers.get("Content-Type", "").lower():
            return None, "wiki response was not an image"
        with Image.open(BytesIO(response.content)) as image:
            color = average_visible_sample(image, sample_size)
        return color, None if color is not None else "sprite sample was transparent"
    except (requests.RequestException, UnidentifiedImageError, OSError) as error:
        return None, str(error)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("attachment", type=Path, help="Pasted Terraria Wiki block-table text file")
    parser.add_argument("--output", type=Path, default=Path("data/cleaned_blocks.json"))
    parser.add_argument("--rejected-output", type=Path, default=Path("data/rejected/blocks_excluded.json"))
    parser.add_argument("--errors-output", type=Path, default=Path("data/errors/blocks_sprite_errors.json"))
    parser.add_argument("--sample-size", type=int, default=32)
    parser.add_argument(
        "--selection-file",
        type=Path,
        help="A manually commented errors file; only entries with an uncommented name are selected.",
    )
    parser.add_argument(
        "--include",
        action="append",
        default=[],
        help="Explicitly include an item name, even if it was automatically excluded; may be repeated.",
    )
    parser.add_argument(
        "--selection-only",
        action="store_true",
        help="Write the active manual selection without fetching sprites.",
    )
    parser.add_argument(
        "--selection-output",
        type=Path,
        default=Path("data/selected_blocks.json"),
        help="Destination used by --selection-only.",
    )
    parser.add_argument(
        "--request-delay",
        type=float,
        default=0.15,
        help="Seconds between direct image downloads after each batched API lookup.",
    )
    parser.add_argument("--api-delay", type=float, default=0.5, help="Seconds between Wiki API batches.")
    parser.add_argument(
        "--allow-wall-list",
        action="store_true",
        help="Override the safety check when the supplied input is intentionally a wall list.",
    )
    args = parser.parse_args()
    if args.sample_size < 1:
        parser.error("--sample-size must be at least 1")
    if args.request_delay < 0 or args.api_delay < 0:
        parser.error("request delays cannot be negative")

    try:
        rows = parse_pasted_table(args.attachment)
    except (OSError, ValueError) as error:
        parser.error(str(error))

    wall_like_rows = sum(bool(WALL_NAME.search(row["name"])) for row in rows)
    if wall_like_rows / len(rows) >= 0.5 and not args.allow_wall_list:
        parser.error(
            f"input appears to be a wall list ({wall_like_rows}/{len(rows)} names are wall-like). "
            "Provide the Terraria block list instead."
        )

    kept_rows: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    if args.selection_file:
        try:
            selected_names = selected_names_from_jsonc(args.selection_file)
        except OSError as error:
            parser.error(f"could not read selection file: {error}")
        selected_names.extend(args.include)
        # Preserve the review-file order while silently de-duplicating names.
        selected_names = list(dict.fromkeys(selected_names))
        rows_by_name = {row["name"]: row for row in rows}
        unknown = [name for name in selected_names if name not in rows_by_name]
        if unknown:
            parser.error(f"selected names are not in the attachment: {', '.join(unknown)}")
        kept_rows = [rows_by_name[name] for name in selected_names]
        if args.selection_only:
            write_json(
                args.selection_output,
                [
                    {**row, "selection_source": str(args.selection_file)}
                    for row in kept_rows
                ],
            )
            print(f"Saved {len(kept_rows)} manually selected blocks -> {args.selection_output}")
            return
    else:
        for row in rows:
            reason = exclusion_reason(row["name"])
            if reason is None:
                kept_rows.append(row)
            else:
                rejected.append({**row, "reason": reason})

    session = make_session()
    accepted: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for offset in range(0, len(kept_rows), FILES_PER_API_QUERY):
        batch = kept_rows[offset:offset + FILES_PER_API_QUERY]
        try:
            urls = wiki_file_urls(session, [row["name"] for row in batch])
        except (requests.RequestException, RuntimeError, ValueError) as error:
            for row in batch:
                errors.append({**row, "sprite_url": None, "error": f"Wiki API lookup failed: {error}"})
            continue

        for number, row in enumerate(batch, start=offset + 1):
            image_url = urls.get(row["name"])
            if image_url is None:
                errors.append({**row, "sprite_url": None, "error": "No exact File:<name>.png on the Wiki"})
                continue
            time.sleep(args.request_delay)
            color, error = fetch_sprite_color(session, image_url, args.sample_size)
            if color is None:
                errors.append({**row, "sprite_url": image_url, "error": error})
                continue
            accepted.append(
                {
                    "name": row["name"],
                    "material_type": "block",
                    "avg_color": color,
                    "sprite_url": image_url,
                    "color_source": "wiki_32x32_sample",
                    "source": {"attachment": str(args.attachment), "raw_columns": row["raw_columns"]},
                }
            )
            print(f"[{number}/{len(kept_rows)}] {row['name']}")
        time.sleep(args.api_delay)

    accepted.sort(key=lambda item: item["name"].casefold())
    rejected.sort(key=lambda item: item["name"].casefold())
    errors.sort(key=lambda item: item["name"].casefold())
    write_json(args.output, accepted)
    write_json(args.rejected_output, rejected)
    write_json(args.errors_output, errors)
    print(f"Saved {len(accepted)} blocks -> {args.output}")
    print(f"Excluded {len(rejected)} requested non-block items -> {args.rejected_output}")
    print(f"Sprite failures: {len(errors)} -> {args.errors_output}")


if __name__ == "__main__":
    main()
