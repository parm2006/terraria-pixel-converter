#!/usr/bin/env python3
"""Merge two validated Terraria block palettes by item name.

The second file takes precedence for duplicate names. This is used to combine
the initially resolved wiki sprites with a later manually reviewed recovery.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def read_palette(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path} must contain a JSON list")
    for item in payload:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise ValueError(f"{path} contains an invalid block record")
        color = item.get("avg_color")
        if not isinstance(color, list) or len(color) != 3 or not all(isinstance(value, int) for value in color):
            raise ValueError(f"{path} has no valid avg_color for {item['name']}")
    return payload


def write_json(path: Path, payload: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base", type=Path)
    parser.add_argument("recovered", type=Path)
    parser.add_argument("--output", type=Path, default=Path("data/cleaned_blocks.json"))
    args = parser.parse_args()

    base = read_palette(args.base)
    recovered = read_palette(args.recovered)
    merged = {record["name"]: record for record in base}
    merged.update({record["name"]: record for record in recovered})
    output = sorted(merged.values(), key=lambda record: record["name"].casefold())
    write_json(args.output, output)
    print(f"Merged {len(base)} base + {len(recovered)} recovered records -> {len(output)} blocks")


if __name__ == "__main__":
    main()
