# Terraria Pixel Art Converter

Turn an image into a Terraria block/wall build preview and materials list. The web app detects the source pixel grid, cleans cell colors, optionally removes the outside background, and matches each cell to the included palette.

## Run locally

Requires Python 3.11 or newer. From the repository root:

```bash
python -m venv .venv
```

Activate with `.venv\Scripts\Activate.ps1` on Windows PowerShell or `source .venv/bin/activate` on macOS/Linux. Then:

```bash
python -m pip install -r requirements.txt
python -c "from http.server import ThreadingHTTPServer; from api.convert import handler; ThreadingHTTPServer(('127.0.0.1', 8000), handler).serve_forever()"
```

Open [localhost:8000](http://localhost:8000). This serves the static UI and Python endpoint; no separate frontend build is needed.

## Using the web app

1. Upload a single-frame PNG, JPG, or WebP (maximum 4,000,000 bytes; 16 megapixels decoded).
2. Leave pixel size on **Auto**, or enter source-image pixels per output tile.
3. Choose blocks, walls, or both and adjust background removal if needed.
4. Convert, inspect the preview and materials list, and download the results.

Auto displays the estimated size without switching to manual mode. Editing the slider or number selects a manual size; entering `0` restores Auto. Ambiguous estimates are labeled **uncertain**.

Downloads include a cleaned logical PNG, a mapped PNG at the original image size, a one-tile-per-pixel mapped PNG for importing into TEdit, and a materials CSV. The TEdit download is an image, not a world file. Conversion does not require TEdit.

## Cleanup and matching

The web pipeline checks repeating boundaries on both axes and uses independent detectors from [Pixel Art Fixer](https://github.com/Retro-Diffusion/pixel-art-fixer). When they disagree, [Proper Pixel Art](https://github.com/KennethJAllen/proper-pixel-art) provides a fallback. Cell colors are reconstructed from dominant color groups and center-weighted source pixels. Small images without reliable upscale evidence are preserved at native resolution.

The old harmonic reconstruction-error refinement is no longer used by the web endpoint. See [cleanup details and benchmark results](GRID_CLEANUP.md).

Material matching uses Euclidean distance in CIE Lab (CIE76). Each cleaned color is matched independently; multiple colors can use the same material. The tooltip and shopping list show the **material's sampled color**, not the source color. A limited palette can produce visibly different matches for neighboring shades; matching does not preserve their relative brightness or guarantee exact reproduction.

## Included datasets

- [cleaned_blocks.json](data/cleaned_blocks.json): 258 block entries.
- [cleaned_walls.json](data/cleaned_walls.json): 286 wall entries.

These are the only datasets required at runtime. Each entry has a `name` and an `avg_color` RGB triple, with additional source metadata. Red Candy Cane Wall uses a saved color fallback without a sprite URL. Sprite averages approximate appearance; they are not guaranteed in-game colors under every lighting condition.

The wiki utilities are `scrape_blocks_from_attachment.py`, `scrape_walls.py`, and `merge_block_palettes.py`. Their raw/review inputs are not bundled; rerunning them requires supplying those inputs. No scraping is needed to run the app.

## Checks and benchmarks

```bash
python -m unittest test_grid_cleanup -v
python benchmark_grid.py --engine current
```

The regression suite covers known grids with noise, blur, JPEG compression, integer/fractional scaling, transparency, manual sizing, and API responses. The benchmark processes local images in `images/` and writes measurements and previews to `local_outputs/grid_benchmark/`. The wizard is included; other local test images and generated outputs are ignored by Git.

`pixel_art.py` is a separate CLI using direct Proper Pixel Art, rather than the web consensus pipeline. Run `python pixel_art.py --help` for supported options. `local_mode_cleaner.py` retains the older experimental workflow for comparison.

## Deployment and third-party code

The Vercel configuration points to `api/convert.py`; static assets are in `site/dist/`. Keep the two cleaned datasets and `vendor/` available to the function. Test images and local outputs are excluded from deployment.

Pixel Art Fixer's pinned source, MIT license, and local modifications are documented in [vendor/README.md](vendor/README.md).

## Upload security

Uploads are never written to disk: request bytes are decoded in memory and the
only returned images are newly generated PNG data URLs. This means there is no
upload filename, upload directory, or user-uploaded file URL that could be
executed as a script.

The API accepts only `image/png`, `image/jpeg`, and `image/webp` requests and
then independently verifies the actual file structure with Pillow. Files are
accepted only when their detected format is PNG, JPEG, or WebP, are complete,
are single-frame, and stay within the byte and decoded-pixel limits. The static
site is served from an explicit fixed-file allowlist with `nosniff` and a
Content Security Policy; uploaded content is never served back directly.
