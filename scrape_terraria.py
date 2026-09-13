#!/usr/bin/env python3
"""
scrape_terraria.py
Refresh the Terraria block and wall color databases from the wiki.

Usage:
    python scrape_terraria.py --output-dir ./data --replace-cleaned
"""

import argparse
import json
import time
from io import BytesIO
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from PIL import Image

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

WIKI_BASE = "https://terraria.wiki.gg"

BLOCK_SUBPAGES = [
    "/wiki/Soils",
    "/wiki/Grown_blocks",
    "/wiki/Other_found_blocks",
    "/wiki/Trap_blocks",
    "/wiki/Ore_blocks",
    "/wiki/Gemstone_Blocks",
    "/wiki/Bricks",
    "/wiki/Crafted_blocks",
    "/wiki/Purchased_blocks",
    "/wiki/Looted_blocks",
    "/wiki/Summoned_blocks",
]

WALLS_SUBPAGES = [
    "/wiki/Crafted_walls",
    "/wiki/Purchased_walls",
    "/wiki/Naturally_occurring_walls",
    "/wiki/Converted_walls",
]

HEADERS = {
    "User-Agent": "TerrariaPixelArtTool/1.0 (open-source educational project)"
}

REQUEST_DELAY = 0.25
COLOR_SAMPLE_SIZE = 32

# ---------------------------------------------------------------------------
# Manual exclusion list
# ---------------------------------------------------------------------------

EXCLUDED = {
    # Liquids
    "Water", "Lava", "Honey",
    # Animated
    "Living Fire Block", "Living Cursed Fire Block", "Living Demon Fire Block",
    "Living Frostfire Block", "Living Ichor Fire Block", "Living Ultrabright Fire Block",
    "Lavafall Block", "Waterfall Block", "Honeyfall Block",
    "Lavafall Wall", "Waterfall Wall", "Honeyfall Wall",
}

# These are intentionally included. They fall in a live Terraria world, but
# they are still valid 1x1 tiles and are useful choices for an art palette.
GRAVITY_BLOCKS = {
    "Sand Block", "Ebonsand Block", "Crimsand Block", "Pearlsand Block",
    "Hardened Sand Block", "Hardened Ebonsand Block",
    "Hardened Crimsand Block", "Hardened Pearlsand Block",
    "Sandstone Block", "Ebonsandstone Block", "Crimsandstone Block",
    "Pearlsandstone Block", "Silt Block", "Slush Block",
}

# ---------------------------------------------------------------------------
# Items that showed up in scrape but are NOT placeable 1x1 blocks/walls.
# These are nav links, crafting stations, furniture, ingredients, weapons, etc.
# ---------------------------------------------------------------------------

NOT_A_BLOCK = {
    # Wiki nav / section header ghost entries
    "Blocks", "Bricks", "Walls",
    # Crafting stations & furniture
    "Work Bench", "Furnace", "Hellforge", "Heavy Assembler", "Bone Welder",
    "Sawmill", "Loom", "Living Loom", "Meat Grinder", "Blend-O-Matic",
    "Solidifier", "Crystal Ball", "Sky Mill", "Sink", "Water fountain",
    "Bookcase", "Iron Anvil", "Lead Anvil", "Mythril Anvil", "Orichalcum Anvil",
    "Adamantite Forge", "Titanium Forge", "Ancient Manipulator",
    # Crafting ingredients / drops (not placeable blocks)
    "Wire", "Gel", "Pink Gel", "Confetti", "Fallen Star", "Coral",
    "Cursed Flame", "Ichor", "Spider Fang", "Feather", "Poo",
    "Mushroom", "Seashell", "Junonia Shell", "Lightning Whelk Shell",
    "Tulip Shell", "Starfish", "Book",
    "Hallowed Bar", "Shroomite Bar", "Solar Fragment", "Nebula Fragment",
    "Stardust Fragment", "Vortex Fragment", "Luminite",
    "Amethyst", "Diamond", "Emerald", "Ruby", "Sapphire", "Topaz", "Amber",
    "Crystal Shard", "Forbidden Fragment", "Flinx Fur",
    "Any Wood", "Any Sand Block", "Any Iron Bar", "Any Seashell or Starfish",
    # Weapons / tools / accessories that snuck in
    "Ice Rod", "Sandgun", "Spectre Goggles",
    # Enemies / bosses
    "Wall of Flesh", "Antlion", "Ghoulder",
    # Misc non-block sprites
    "Torches", "Large Gems", "Gemcorns", "Gem Locks", "Gem Hooks",
    "Gem Robes", "Phaseblades", "Phasesabers", "Gem staves",
    "Diamond Minecart", "Gemspark Blocks", "Gemstone Blocks", "Stained Glass",
    "Large Bamboo", "Pine Wood",
    "Demon Torch", "Ultrabright Torch",
    "Snow Balla", "Sand Ball", "Lava Bomb", "Lava Boulder",
    "Rainbow Boulder", "Poo Boulder", "Spider Boulder", "Bouncy Boulder",
    "Green Thread", "White Thread", "Purple Thread",
    # Multi-tile / non-1x1 structural objects
    "Conveyor Belt (Clockwise)", "Conveyor Belt (Counter Clockwise)",
    "Dart Trap", "Venom Dart Trap", "Super Dart Trap",
    "Spear Trap", "Spiky Ball Trap", "Flame Trap",
}

# The wall pages are already restricted to wall categories. Reusing the block
# filter here could accidentally throw away a valid wall whose name overlaps
# with furniture, a crafting item, or another block-page navigation label.
NOT_A_WALL = {
    "Blocks", "Bricks", "Walls", "Wall of Flesh",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_absolute(src: str) -> str:
    if src.startswith("//"):
        return "https:" + src
    if src.startswith("/"):
        return WIKI_BASE + src
    return src


def average_color(img: Image.Image):
    rgba = img.convert("RGBA")
    pixels = list(rgba.getdata())
    r_sum = g_sum = b_sum = count = 0
    for r, g, b, a in pixels:
        if a > 10:
            r_sum += r
            g_sum += g
            b_sum += b
            count += 1
    if count == 0:
        return None
    return (r_sum // count, g_sum // count, b_sum // count)


def fetch_and_avg(url: str, session: requests.Session):
    try:
        resp = session.get(url, timeout=15)
        resp.raise_for_status()
        img = Image.open(BytesIO(resp.content))
        w = min(img.width, COLOR_SAMPLE_SIZE)
        h = min(img.height, COLOR_SAMPLE_SIZE)
        tile = img.crop((0, 0, w, h))
        return average_color(tile)
    except Exception as e:
        print(f"    [WARN] fetch failed for {url}: {e}")
        return None, str(e)


# ---------------------------------------------------------------------------
# Core extraction
# ---------------------------------------------------------------------------

def extract_items(
    soup: BeautifulSoup,
    excluded: set,
    rejected_names: set,
    session: requests.Session,
    source_page: str,
) -> tuple[list[dict], list[dict]]:
    entries = []
    rejected = []
    seen = set()

    for span in soup.find_all("span", class_="i"):
        img_tag = span.find("img")
        if not img_tag:
            continue

        name = img_tag.get("alt", "").strip()
        if not name:
            a = span.find("a")
            name = a.get_text(strip=True) if a else ""
        if not name:
            continue

        src = img_tag.get("src") or img_tag.get("data-src") or ""
        if not src:
            rejected.append({
                "name": name,
                "sprite_url": "",
                "reason": "missing_sprite_url",
                "source_page": source_page,
            })
            continue
        sprite_url = make_absolute(src.split("?")[0])

        if name in seen:
            continue
        if name in excluded:
            print(f"  [SKIP-excluded] {name}")
            rejected.append({
                "name": name,
                "sprite_url": sprite_url,
                "reason": "excluded_from_palette",
                "source_page": source_page,
            })
            continue
        if name in rejected_names:
            print(f"  [SKIP-not-block] {name}")
            rejected.append({
                "name": name,
                "sprite_url": sprite_url,
                "reason": "not_a_placeable_1x1_material",
                "source_page": source_page,
            })
            continue

        seen.add(name)

        time.sleep(REQUEST_DELAY)
        result = fetch_and_avg(sprite_url, session)
        if isinstance(result, tuple):
            avg, error = result
        else:
            avg, error = result, None
        if avg is None:
            print(f"  [WARN] {name}: transparent or download failed, skipping.")
            rejected.append({
                "name": name,
                "sprite_url": sprite_url,
                "reason": "sprite_fetch_or_transparency_failed",
                "source_page": source_page,
                "error": error or "no visible pixels in sampled area",
            })
            continue

        entries.append({
            "name": name,
            "avg_color": list(avg),
            "sprite_url": sprite_url,
        })
        print(f"  [OK] {name:<48}  RGB{avg}")

    return entries, rejected


# ---------------------------------------------------------------------------
# Scrape subpages
# ---------------------------------------------------------------------------

def scrape_subpages(
    subpages: list,
    excluded: set,
    rejected_names: set,
    label: str,
    session: requests.Session,
) -> tuple[list[dict], list[dict]]:
    all_entries = []
    all_rejected = []
    seen_names = set()
    seen_rejected = set()

    for path in subpages:
        url = WIKI_BASE + path
        print(f"\n  -- {url}")
        try:
            resp = session.get(url, timeout=20)
            resp.raise_for_status()
        except Exception as e:
            print(f"  [ERROR] {url}: {e}")
            continue

        soup = BeautifulSoup(resp.text, "html.parser")
        entries, rejected = extract_items(soup, excluded, rejected_names, session, url)

        for e in entries:
            if e["name"] not in seen_names:
                seen_names.add(e["name"])
                all_entries.append(e)
        for entry in rejected:
            key = (entry["name"], entry["reason"])
            if key not in seen_rejected:
                seen_rejected.add(key)
                all_rejected.append(entry)

    print(
        f"\n  Total unique {label}: {len(all_entries)} "
        f"({len(all_rejected)} rejected for review)"
    )
    return all_entries, all_rejected


def clean_entries(entries: list[dict]) -> list[dict]:
    """Produce a stable, valid database from fresh scraped entries."""

    cleaned: dict[str, dict] = {}
    for entry in entries:
        name = str(entry.get("name", "")).strip()
        color = entry.get("avg_color")
        sprite_url = str(entry.get("sprite_url", "")).strip()
        if not name or not sprite_url or not isinstance(color, list) or len(color) != 3:
            continue
        if not all(isinstance(channel, int) and 0 <= channel <= 255 for channel in color):
            continue
        cleaned.setdefault(name, {"name": name, "avg_color": color, "sprite_url": sprite_url})
    return sorted(cleaned.values(), key=lambda entry: entry["name"].casefold())


def validate_refresh(blocks: list[dict], walls: list[dict]) -> None:
    """Reject an incomplete scrape before it can replace the live palette."""

    block_names = {entry["name"] for entry in blocks}
    missing = {"Dirt Block", "Stone Block", "Sand Block"} - block_names
    if missing:
        raise RuntimeError(f"Refusing to replace data: missing required blocks: {sorted(missing)}")
    if len(blocks) < 100 or len(walls) < 100:
        raise RuntimeError(
            f"Refusing to replace data: scrape is unexpectedly small "
            f"({len(blocks)} blocks, {len(walls)} walls)."
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Scrape terraria.wiki.gg once to build the block/wall color database."
    )
    parser.add_argument("--output-dir", default=".", help="Where to write blocks.json and walls.json")
    parser.add_argument(
        "--replace-cleaned",
        action="store_true",
        help="After validation, replace cleaned_blocks.json and cleaned_walls.json",
    )
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update(HEADERS)

    print("=== Scraping BLOCKS ===")
    blocks, rejected_blocks = scrape_subpages(
        BLOCK_SUBPAGES, EXCLUDED, NOT_A_BLOCK, "blocks", session
    )
    blocks_path = out_dir / "raw_blocks.json"
    with open(blocks_path, "w") as f:
        json.dump(blocks, f, indent=2)
    print(f"Saved {len(blocks)} blocks -> {blocks_path}")

    print("\n=== Scraping WALLS ===")
    walls, rejected_walls = scrape_subpages(
        WALLS_SUBPAGES, EXCLUDED, NOT_A_WALL, "walls", session
    )
    walls_path = out_dir / "raw_walls.json"
    with open(walls_path, "w") as f:
        json.dump(walls, f, indent=2)
    print(f"Saved {len(walls)} walls -> {walls_path}")

    rejected_dir = out_dir / "rejected"
    rejected_dir.mkdir(parents=True, exist_ok=True)
    for name, entries in (("blocks.json", rejected_blocks), ("walls.json", rejected_walls)):
        path = rejected_dir / name
        with path.open("w", encoding="utf-8") as file:
            json.dump(entries, file, indent=2)
            file.write("\n")
        print(f"Saved {len(entries)} rejected {name.removesuffix('.json')} -> {path}")

    if args.replace_cleaned:
        cleaned_blocks = clean_entries(blocks)
        cleaned_walls = clean_entries(walls)
        validate_refresh(cleaned_blocks, cleaned_walls)
        for name, entries in (("cleaned_blocks.json", cleaned_blocks), ("cleaned_walls.json", cleaned_walls)):
            path = out_dir / name
            with path.open("w", encoding="utf-8") as file:
                json.dump(entries, file, indent=2)
                file.write("\n")
            print(f"Replaced {path} with {len(entries)} validated entries")

    print("\nDone.")


if __name__ == "__main__":
    main()
