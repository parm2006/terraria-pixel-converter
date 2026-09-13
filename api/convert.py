"""Vercel HTTP endpoint for the Terraria pixel-art converter."""

from __future__ import annotations

import base64
import json
import time
from io import BytesIO
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from PIL import Image, ImageOps, UnidentifiedImageError

from pixel_art import (
    clean_image,
    count_visible_colors,
    draw_material_reconstruction,
    load_database,
    match_materials,
    remove_edge_background,
)


MAX_UPLOAD_BYTES = 4_000_000
MAX_IMAGE_PIXELS = 16_000_000
DB_DIR = Path(__file__).resolve().parents[1] / "data"
STATIC_DIR = Path(__file__).resolve().parents[1] / "site" / "dist"
BLOCKS = load_database(DB_DIR, "blocks")
WALLS = load_database(DB_DIR, "walls")
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS


def _bool_param(params: dict[str, list[str]], name: str, default: bool) -> bool:
    value = params.get(name, ["1" if default else "0"])[0].lower()
    return value in {"1", "true", "yes", "on"}


def _int_param(
    params: dict[str, list[str]],
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        value = int(params.get(name, [str(default)])[0])
    except ValueError as error:
        raise ValueError(f"{name} must be a whole number") from error
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _png_data_url(image: Image.Image) -> str:
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def convert_request(body: bytes, query: str) -> dict:
    if not body:
        raise ValueError("Request body must contain an image")
    if len(body) > MAX_UPLOAD_BYTES:
        raise ValueError("Image must be smaller than 4 MB")

    params = parse_qs(query)
    remove_background = _bool_param(params, "remove_background", True)
    tolerance = _int_param(params, "tolerance", 3, 0, 255)
    pixel_width = _int_param(params, "pixel_width", 0, 0, 256)
    bin_size = _int_param(params, "bin_size", 52, 1, 255)
    palette = params.get("palette", ["both"])[0]
    if palette not in {"both", "blocks", "walls"}:
        raise ValueError("palette must be blocks, walls, or both")

    started = time.perf_counter()
    try:
        with Image.open(BytesIO(body)) as opened:
            source = ImageOps.exif_transpose(opened).convert("RGBA")
            source.load()
    except (UnidentifiedImageError, OSError) as error:
        raise ValueError("The uploaded file is not a supported image") from error

    if source.width * source.height > MAX_IMAGE_PIXELS:
        raise ValueError("Image is too large; maximum decoded size is 16 megapixels")

    cleaned = clean_image(
        source,
        num_colors=0,
        pixel_width=pixel_width,
        bin_size=bin_size,
    )

    background = None
    if remove_background:
        cleaned, background_color, removed_count = remove_edge_background(
            cleaned,
            tolerance=tolerance,
        )
        background = {
            "color": list(background_color),
            "removed_pixels": removed_count,
            "tolerance": tolerance,
        }

    color_counts = count_visible_colors(cleaned)
    blocks = BLOCKS if palette in {"both", "blocks"} else []
    walls = WALLS if palette in {"both", "walls"} else []
    color_matches, material_summary = match_materials(color_counts, blocks, walls)
    mapped = draw_material_reconstruction(cleaned, color_matches, cleaned.size)

    return {
        "grid": {"width": cleaned.width, "height": cleaned.height},
        "source": {"width": source.width, "height": source.height},
        "visible_pixels": sum(color_counts.values()),
        "unique_colors": len(color_counts),
        "settings": {
            "pixel_width": pixel_width,
            "bin_size": bin_size,
            "palette": palette,
            "remove_background": remove_background,
        },
        "processing_ms": round((time.perf_counter() - started) * 1000),
        "background": background,
        "materials": material_summary,
        "cleaned_png": _png_data_url(cleaned),
        "mapped_png": _png_data_url(mapped),
    }


class handler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, payload: dict) -> None:
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(encoded)

    def _send_static(self, filename: str, content_type: str) -> None:
        path = STATIC_DIR / filename
        if not path.is_file():
            self._send_json(404, {"error": "Not found"})
            return
        content = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "public, max-age=300")
        self.end_headers()
        self.wfile.write(content)

    def do_OPTIONS(self) -> None:
        self._send_json(204, {})

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in {"/", "/index.html"}:
            self._send_static("index.html", "text/html; charset=utf-8")
            return
        if path == "/styles.css":
            self._send_static("styles.css", "text/css; charset=utf-8")
            return
        if path == "/app.js":
            self._send_static("app.js", "text/javascript; charset=utf-8")
            return
        if path != "/api/convert":
            self._send_json(404, {"error": "Not found"})
            return
        self._send_json(
            200,
            {
                "status": "ready",
                "max_upload_bytes": MAX_UPLOAD_BYTES,
                "materials": len(BLOCKS) + len(WALLS),
            },
        )

    def do_POST(self) -> None:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length > MAX_UPLOAD_BYTES:
                self._send_json(413, {"error": "Image must be smaller than 4 MB"})
                return
            body = self.rfile.read(content_length)
            result = convert_request(body, urlparse(self.path).query)
            self._send_json(200, result)
        except ValueError as error:
            self._send_json(400, {"error": str(error)})
        except Exception:
            self._send_json(
                500,
                {"error": "Conversion failed. Try a smaller image or set the pixel width manually."},
            )
