"""Small, dependency-free web assets used by the Hermes dashboards."""

import json
import struct
import zlib


ICON_SIZE = 180


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    checksum = zlib.crc32(kind + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)


def _build_icon_png() -> bytes:
    """Create a compact, high-contrast Hermes H icon without image dependencies."""
    background = (36, 39, 43)
    foreground = (255, 255, 255)
    pixels = bytearray(background * (ICON_SIZE * ICON_SIZE))

    def fill(left: int, top: int, right: int, bottom: int, color: tuple[int, int, int]) -> None:
        for y_coord in range(top, bottom):
            row_start = y_coord * ICON_SIZE
            for x_coord in range(left, right):
                index = (row_start + x_coord) * 3
                pixels[index : index + 3] = bytes(color)

    # A simple geometric H stays readable at every shortcut size.
    fill(42, 34, 66, 146, foreground)
    fill(114, 34, 138, 146, foreground)
    fill(42, 78, 138, 102, foreground)

    scanlines = b"".join(
        b"\x00" + bytes(pixels[row * ICON_SIZE * 3 : (row + 1) * ICON_SIZE * 3])
        for row in range(ICON_SIZE)
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", ICON_SIZE, ICON_SIZE, 8, 2, 0, 0, 0))
        + _png_chunk(b"IDAT", zlib.compress(scanlines, level=9))
        + _png_chunk(b"IEND", b"")
    )


HERMES_ICON_PNG = _build_icon_png()
HERMES_ICON_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 180 180" role="img" aria-label="Hermes">
<rect width="180" height="180" rx="30" fill="#24272b"/>
<path d="M42 34h24v44h48V34h24v112h-24v-44H66v44H42z" fill="#fff"/>
</svg>"""


def _asset_url(base_path: str, asset_name: str) -> str:
    normalized = str(base_path or ".").rstrip("/")
    return f"./{asset_name}" if normalized in {"", "."} else f"{normalized}/{asset_name}"


def render_web_app_head(base_path: str) -> str:
    """Return shared favicon, iPhone shortcut and web-app metadata."""
    icon_png = _asset_url(base_path, "icon.png")
    icon_svg = _asset_url(base_path, "icon.svg")
    manifest = _asset_url(base_path, "manifest.webmanifest")
    return (
        f'<link rel="icon" type="image/svg+xml" href="{icon_svg}">'
        f'<link rel="alternate icon" type="image/png" href="{icon_png}">'
        f'<link rel="apple-touch-icon" href="{icon_png}">'
        f'<link rel="manifest" href="{manifest}">'
    )


def render_web_manifest(base_path: str) -> bytes:
    icon_png = _asset_url(base_path, "icon.png")
    start_url = str(base_path or ".")
    payload = {
        "name": "Hermes",
        "short_name": "Hermes",
        "start_url": start_url,
        "display": "standalone",
        "background_color": "#111315",
        "theme_color": "#111315",
        "icons": [{"src": icon_png, "sizes": "180x180", "type": "image/png", "purpose": "any maskable"}],
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
