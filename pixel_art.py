#!/usr/bin/env python3
"""Terraria pixel-art converter.

The conversion pipeline is intentionally empty while it is rebuilt. For now,
this module only loads and validates the Terraria material databases.
"""

import argparse
import json
from pathlib import Path


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
            or any(not isinstance(channel, int) or not 0 <= channel <= 255 for channel in color)
        ):
            raise ValueError(
                f"Material {index} has an invalid avg_color: {color!r}"
            )

    return materials


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Load a Terraria material database. Conversion is being rebuilt."
    )
    parser.add_argument("--mode", choices=VALID_MODES, required=True)
    parser.add_argument("--db-dir", default="./data")
    args = parser.parse_args()

    materials = load_database(args.db_dir, args.mode)
    print(f"Loaded {len(materials)} {args.mode} from {Path(args.db_dir).resolve()}")


if __name__ == "__main__":
    main()
