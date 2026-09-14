# Pixel Art Fixer

`pixelfixer/` contains the Python reference implementation from
https://github.com/Retro-Diffusion/pixel-art-fixer at commit
`ef376e57e1c272633ca2dbf5f29ec3fcf6596465`.

Copyright (c) 2026 Astropulse, LLC. MIT license: `pixelfixer/LICENSE`.
The source is vendored to pin behavior; the application uses its fast consensus
detector and two-stage cell reconstruction, with its own conservative fallback
policy in `grid_cleanup.py`. It does not use the upstream CLI.

Local changes in `quantize.py`: seed OpenCV's k-means for repeatable results;
use 16K-pixel assignment chunks to limit temporary array memory on the server.
