# Terraria Pixel Art Converter

> Current note, May 2, 2026: this folder is still named `claudepixels`, but the intended project name is `Terraria Pixel Art Converter`. Next steps are to rename the folder, verify the CLI still works, and build a deployable frontend for public use.

Convert a pixel art image into a Terraria block/wall material list. The tool matches each pixel's color to the closest Terraria block or wall and outputs how many of each you need to build it.

The Terraria wiki has already been scraped and the deployment palette database is included (`data/cleaned_blocks.json`, `data/cleaned_walls.json`) — no setup required.

---

## Requirements

```bash
pip install pillow
```

---

## Usage

```bash
python pixel_art.py <image> --mode blocks --db-dir ./data
python pixel_art.py <image> --mode walls  --db-dir ./data
```

Use `--mode blocks` for foreground pixel art. Use `--mode walls` for backgrounds — the wall palette is larger (460 vs 266 entries).

---

## Output

```
── Pixel art material list (rainbow order) ──────────────────────────
  ██ #CB9174    Red Ice Block               x 847     ██
  ██ #261E5C    Granite Block               x 575     █
  ██ #615282    Demonite Ore                x 507     █
  ██ #030213    Asphalt Block               x 967     ██

  Total pixels: 14098
```

Each row is one Terraria block. The colored swatch is the source color from your image, the count is how many of that block to place, and the bar shows its proportion of the total.

---

## Image guidelines

- **PNG with transparent background** works best. White-background PNGs will produce a "Cloud" entry for all background pixels.
- The tool auto-detects pixel size from image dimensions, assuming at most 150 logical pixels along the longest axis. A 450px wide image is treated as 150 logical pixels wide (3px per pixel).
- JPEG or compressed images are automatically quantized to 128 colors before matching to reduce noise.

---

## Notes

- Gravity blocks (sand, silt), animated blocks (living fire, waterfalls), liquids, furniture, and multi-tile objects are excluded from the palette.
- Color matching uses Euclidean distance in RGB space — closest average color wins.
- The block database was scraped from [terraria.wiki.gg](https://terraria.wiki.gg). To regenerate it, run `python scrape_terraria.py --output-dir ./data` (requires `requests` and `beautifulsoup4`).
