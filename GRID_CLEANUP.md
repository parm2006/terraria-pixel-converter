# Web conversion grid cleanup

The web endpoint uses `grid_cleanup.recover_grid`. It no longer runs the
reconstruction-error harmonic refinement in `local_mode_cleaner.py`; that
experimental CLI remains available but does not define web behavior.

The cat regression was caused by that refinement changing an initially correct
38x38 Proper Pixel Art grid through 18px -> 6px -> 2px candidates. Raw pixel
reconstruction error rewards reproducing noise and cannot establish grid size.

## Current selection and cleanup

- Strong horizontal AND vertical boundary concentration identifies integer
  upscales, allowing small interior color differences.
- Small images without reliable upscale evidence are preserved at native size.
- Otherwise an edge-preserving bilateral filter prepares detection pixels;
  Pixel Art Fixer's fast independent detectors seek agreement.
- On disagreement, Proper Pixel Art estimates the grid. The UI labels the
  result uncertain instead of reporting high confidence.
- Per-cell dominant color-group voting and center-weighted original colors
  produce one output pixel per cell. There is no filename-specific selection.
- Manual pixel width bypasses Auto; width 1 preserves the source exactly.

Pixel Art Fixer source and license attribution are in `vendor/README.md`.
Install with `pip install -r requirements.txt`.

## Verification

Run `python -m unittest test_grid_cleanup -v` for known-grid checks covering
clean, additive noise, blur and JPEG images at 2x, 3x, 5x, 7x and 8.5x scales;
native preservation, manual size, transparency and API response checks.
These are generated fixtures with known ground truth, not image-name rules.

Run `python benchmark_grid.py --engine current` for the local `images/` corpus.
It writes JSON measurements, logical PNGs and source-size nearest-neighbor
previews under `local_outputs/grid_benchmark/`. `--engine proper` measures the
unmodified Proper Pixel Art baseline. `--engine fixer` measures the full
vendored Pixel Art Fixer baseline.

Local web-pipeline results (2026-09-13, not hosted-server timing guarantees):

| Sample | Logical grid | Total seconds |
| --- | --- | --- |
| cat | 38x38 | 1.39 |
| image (chicken) | 40x43 | 0.35 |
| Pineapple | 24x32 | 4.41 |
| pixelated_test | 20x21, preserved | 0.02 |
| pyramid | 105x105 | 3.09 |
| wizard | 82x82 | 0.33 |

The sample results were visually reviewed; these files are not all annotated
ground truth. Pineapple invokes the uncertain fallback. This does not guarantee
perfect recovery of every image or removal of all noise: mixed-resolution art,
severe compression, shifted grids and tiny sprites remain ambiguous. Small
native images are intentionally preserved rather than forcibly denoised.
