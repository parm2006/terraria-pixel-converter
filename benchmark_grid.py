"""Compare real image cleanup engines; artifacts stay in local_outputs."""
import argparse
import json
import time
from pathlib import Path

from PIL import Image, ImageDraw


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--engine', choices=['proper', 'current', 'fixer'], required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    output = root / 'local_outputs' / 'grid_benchmark'
    output.mkdir(parents=True, exist_ok=True)
    reports = []
    for path in sorted((root / 'images').glob('*')):
        if path.suffix.lower() not in {'.png', '.jpg', '.jpeg'}:
            continue
        source = Image.open(path).convert('RGBA')
        start = time.perf_counter()
        details = {}
        if args.engine == 'fixer':
            import numpy as np
            from vendor.pixelfixer.api import process
            result = process(np.array(source), return_png=False)
            logical = Image.fromarray(result.pop('array'))
            details = result
        elif args.engine == 'current':
            from api.convert import convert_request
            from io import BytesIO
            import base64
            result = convert_request(path.read_bytes(), 'remove_background=0')
            logical = Image.open(BytesIO(base64.b64decode(result['cleaned_png'].split(',')[1])))
            details = {key: result[key] for key in ('settings', 'grid_refinement', 'smoothing', 'detection')}
        else:
            from pixel_art import clean_image
            logical = clean_image(source, num_colors=0)
        seconds = round(time.perf_counter() - start, 3)
        logical.save(output / f'{path.stem}_{args.engine}.png')
        preview = logical.resize(source.size, Image.Resampling.NEAREST)
        preview.save(output / f'{path.stem}_{args.engine}_preview.png')
        report = dict(file=path.name, source=source.size, grid=logical.size, seconds=seconds, details=details)
        reports.append(report)
        print(json.dumps(report), flush=True)
    (output / f'{args.engine}.json').write_text(json.dumps(reports, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
